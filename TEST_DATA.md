# Test Data — Example Business: **TaskFlow**

A ready-to-use dataset for testing the AI Email Marketing Agent end-to-end
without wiring up a real customer. It contains:

1. **The agent's own operational tables** (from `migrations/001..003`) + sample rows.
2. **An example customer business — "TaskFlow"** (a project-management SaaS):
   full DDL, readable row tables, and copy-paste `INSERT`s.
3. **An expected-outcome oracle** — exactly which users should be emailed,
   skipped, or filtered, so you can verify a planning run.

> Everything is dated relative to **today = 2026-06-15** so the time-based rules
> (frequency cap, account age, recency signals) actually fire. The relevant
> constants from `config.py`:
>
> | Constant | Value | Meaning |
> |---|---|---|
> | `FREQUENCY_CAP_DAYS` | `4` | No email if any system mailed the user since **2026-06-11** |
> | `MIN_ACCOUNT_AGE_DAYS` | `1` | Accounts created after **2026-06-14** are too new |
> | `CUSTOMER_ID` | `taskflow` | Namespaces the agent log + Store |

---

## How to use this file

**Option A — point the agent at a throwaway Supabase project (recommended):**

1. Run `migrations/001..003` (creates `ai_marketing_email_log`, `ai_marketing_runs`, `exec_sql`).
2. Run the **TaskFlow DDL** (§2.1) then the **TaskFlow inserts** (§2.3).
3. Run the **agent-log inserts** (§1.2) — these seed the frequency-cap test cases.
4. Set `.env`: `CUSTOMER_ID=taskflow`, point `SUPABASE_*` at the project.
5. Use the **Product Context** (§3) as the onboarding form output, run the
   Schema Intelligence Agent, approve the signals (§4), then run `handler_plan.py`.
6. Compare the resulting send/skip/filter decisions against the **oracle (§5)**.

**Option B — pure offline review:** just read the markdown tables; the oracle in
§5 is the source of truth for "what should happen."

---

# 1. Agent operational tables (the agent's own bookkeeping)

These come from the migrations and are the same for every customer.

## 1.1 Schema recap

**`ai_marketing_email_log`** — permanent audit trail, one row per user per run.

| column | type | notes |
|---|---|---|
| id | uuid PK | |
| customer_id | text | = `taskflow` here |
| run_id | text | |
| user_id | uuid | FK → the customer's users |
| sent_to_email | text | |
| campaign_key | text | snake_case label the copywriter invents |
| subject | text | |
| decision | text | `sent` \| `skipped` |
| status | text | `sent` \| `failed` |
| decision_reason | text | |
| signals_snapshot | jsonb | signals at decision time |
| model | text | |
| provider_message_id | text | Resend id |
| error_message | text | |
| sent_at | timestamptz | drives the frequency cap |
| created_at | timestamptz | |

**`ai_marketing_runs`** — lifecycle of each planned batch.

| column | type | notes |
|---|---|---|
| run_id | text PK | |
| customer_id | text | |
| status | text | `pending_approval`\|`approved`\|`cancelled`\|`expired`\|`sent` |
| summary_message_id | bigint | Telegram message to edit |
| expires_at | timestamptz | auto-expiry sweep |
| created_at / updated_at | timestamptz | |

## 1.2 Sample rows — seeds the frequency-cap test cases

These two rows make **u06** and **u11** behave correctly: u06 was emailed
*inside* the 4-day cap (excluded), u11 was emailed *12 days ago* (eligible again,
but the copywriter should avoid repeating `winback_dormant_v1`).

```sql
-- Frequency-cap fixtures in the agent's OWN log (customer_id must match CUSTOMER_ID)
insert into public.ai_marketing_email_log
  (customer_id, run_id, user_id, sent_to_email, campaign_key, subject, decision, status, sent_at)
values
  -- u06: emailed 2 days ago -> INSIDE the 4-day cap -> must be excluded today
  ('taskflow','run_2026-06-13','66666666-6666-6666-6666-666666666666',
   'frank@example.com','feature_nudge_integrations','Connect Slack in one click',
   'sent','sent','2026-06-13T09:05:00Z'),
  -- u11: emailed 12 days ago -> OUTSIDE the cap -> eligible again (avoid repeat angle)
  ('taskflow','run_2026-06-03','bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
   'kara@example.com','winback_dormant_v1','Your TaskFlow projects are waiting',
   'sent','sent','2026-06-03T09:04:00Z');
```

```sql
-- One example run row (optional; illustrates the approval lifecycle)
insert into public.ai_marketing_runs (run_id, customer_id, status, expires_at)
values ('run_2026-06-15','taskflow','pending_approval','2026-06-15T11:00:00Z');
```

---

# 2. Example customer business — **TaskFlow**

> TaskFlow is a freemium project-management SaaS. **Free** users are capped at
> **3 active projects**. **Pro/Business** users (a row in `subscriptions`) are
> unlimited. The **core action is creating/completing tasks**. The schema is
> deliberately built to exercise every requirement of the Schema Intelligence
> Agent: a separate subscriptions table (**absent row = free**), a soft-delete
> column, an unsubscribe flag, a JSONB column, an advanced-feature table, and a
> **legacy email log** (so the frequency cap must check two sources — Rule 9).

## 2.1 DDL

```sql
-- ── users: identity, recency, unsubscribe, soft-delete, JSONB prefs ──
create table public.users (
    id                uuid primary key default gen_random_uuid(),
    email             text not null unique,
    full_name         text,
    created_at        timestamptz not null default now(),  -- account age
    last_active_at    timestamptz,                          -- recency signal
    marketing_opt_out boolean not null default false,       -- unsubscribe state
    deleted_at        timestamptz,                          -- soft delete (null = active)
    preferences       jsonb default '{}'::jsonb             -- e.g. {"onboarding_completed": true}
);

-- ── subscriptions: ABSENCE of a row == free user (key semantics) ──
create table public.subscriptions (
    id                  uuid primary key default gen_random_uuid(),
    user_id             uuid not null references public.users(id) on delete cascade,
    plan                text not null check (plan in ('pro','business')),
    status              text not null check (status in ('active','past_due','canceled')),
    started_at          timestamptz not null,
    current_period_end  timestamptz
);

-- ── projects: free plan capped at 3 non-archived projects ──
create table public.projects (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references public.users(id) on delete cascade,
    name        text not null,
    created_at  timestamptz not null default now(),
    archived    boolean not null default false
);

-- ── tasks: the CORE ACTION (count + recency) ──
create table public.tasks (
    id           uuid primary key default gen_random_uuid(),
    user_id      uuid not null references public.users(id) on delete cascade,
    project_id   uuid references public.projects(id) on delete set null,
    title        text not null,
    status       text not null default 'todo' check (status in ('todo','in_progress','done')),
    created_at   timestamptz not null default now(),
    completed_at timestamptz
);

-- ── integrations: advanced-feature presence (Slack/GitHub/etc.) ──
create table public.integrations (
    id           uuid primary key default gen_random_uuid(),
    user_id      uuid not null references public.users(id) on delete cascade,
    provider     text not null check (provider in ('slack','github','zapier')),
    connected_at timestamptz not null default now()
);

-- ── legacy_email_sends: pre-existing marketing log (Rule 9 second source) ──
create table public.legacy_email_sends (
    id           uuid primary key default gen_random_uuid(),
    user_id      uuid not null references public.users(id) on delete cascade,
    template_key text,
    sent_at      timestamptz not null
);
```

## 2.2 Users — readable view

UUID shorthand: `u01 = 11111111-…-1111`, `u02 = 22222222-…-2222`, … `u09 = 99999999-…-9999`,
`u10 = aaaaaaaa-…-aaaa`, `u11 = bbbbbbbb-…-bbbb`, `u12 = cccccccc-…-cccc`.

| # | name | email | created_at | last_active_at | opt_out | deleted_at | premium? | preferences |
|---|---|---|---|---|---|---|---|---|
| u01 | Alice Dorman | alice@example.com | 2025-09-01 | 2026-05-25 (21d ago) | no | – | free | onboarding_completed: true |
| u02 | Ben Carter | ben@example.com | 2025-11-10 | 2026-06-14 (1d ago) | no | – | free | onboarding_completed: true |
| u03 | Chloe Park | chloe@example.com | 2025-06-20 | 2026-06-13 (2d ago) | no | – | **PRO (active)** | onboarding_completed: true |
| u04 | Dan Lee | dan@example.com | 2025-10-05 | 2026-06-01 (14d ago) | **YES** | – | free | onboarding_completed: true |
| u05 | Eve Nguyen | eve@example.com | **2026-06-14 23:10** (<1d) | 2026-06-15 | no | – | free | onboarding_completed: false |
| u06 | Frank Ruiz | frank@example.com | 2025-12-01 | 2026-06-08 (7d ago) | no | – | free | onboarding_completed: true |
| u07 | Grace Kim | grace@example.com | 2025-08-15 | 2026-06-05 (10d ago) | no | – | free | onboarding_completed: true |
| u08 | Henry Cole | henry@example.com | 2025-07-01 | 2026-04-20 | no | **2026-05-30** | free | onboarding_completed: true |
| u09 | Ivy Watson | ivy@example.com | 2026-01-12 | 2026-06-02 (13d ago) | no | – | free | onboarding_completed: true |
| u10 | Jack Moss | jack@example.com | 2026-05-20 | 2026-05-21 (25d ago) | no | – | free | onboarding_completed: false |
| u11 | Kara Singh | kara@example.com | 2025-10-30 | 2026-05-28 (18d ago) | no | – | free | onboarding_completed: true |
| u12 | Leo Tan | leo@example.com | 2025-09-22 | 2026-06-10 (5d ago) | no | – | free | onboarding_completed: true |

**Behavioral shape per user** (drives the copywriter's reasoning):

| # | tasks (total / done) | last task | active projects | integrations | recent email? |
|---|---|---|---|---|---|
| u01 | 34 / 28 | 2026-05-25 | 2 | – | legacy: 2026-04-18 (old) |
| u02 | 51 / 40 | 2026-06-14 | 3 | slack | – |
| u03 | 120 / 95 | 2026-06-13 | 8 | slack, github | – |
| u04 | 22 / 15 | 2026-05-30 | 2 | – | – |
| u05 | 0 / 0 | – | 0 | – | – |
| u06 | 40 / 30 | 2026-06-07 | 2 | – | **AI log: 2026-06-13 (2d)** |
| u07 | 18 / 9 | 2026-06-04 | 2 | – | **legacy: 2026-06-14 (1d)** |
| u08 | 60 / 50 | 2026-04-19 | 3 | – | – |
| u09 | 47 / 38 | 2026-06-02 | **3 (at free limit)** | – | – |
| u10 | 0 / 0 | – | 0 | – | – |
| u11 | 29 / 20 | 2026-05-28 | 2 | – | **AI log: 2026-06-03 (12d, old)** |
| u12 | 88 / 70 | 2026-06-10 | 3 | slack, github | – |

## 2.3 Inserts

```sql
-- ── users ──
insert into public.users (id, email, full_name, created_at, last_active_at, marketing_opt_out, deleted_at, preferences) values
('11111111-1111-1111-1111-111111111111','alice@example.com','Alice Dorman','2025-09-01T10:00:00Z','2026-05-25T14:00:00Z',false,null,'{"onboarding_completed":true,"timezone":"America/New_York"}'),
('22222222-2222-2222-2222-222222222222','ben@example.com','Ben Carter','2025-11-10T10:00:00Z','2026-06-14T18:00:00Z',false,null,'{"onboarding_completed":true}'),
('33333333-3333-3333-3333-333333333333','chloe@example.com','Chloe Park','2025-06-20T10:00:00Z','2026-06-13T09:00:00Z',false,null,'{"onboarding_completed":true}'),
('44444444-4444-4444-4444-444444444444','dan@example.com','Dan Lee','2025-10-05T10:00:00Z','2026-06-01T12:00:00Z',true,null,'{"onboarding_completed":true}'),
('55555555-5555-5555-5555-555555555555','eve@example.com','Eve Nguyen','2026-06-14T23:10:00Z','2026-06-15T08:00:00Z',false,null,'{"onboarding_completed":false}'),
('66666666-6666-6666-6666-666666666666','frank@example.com','Frank Ruiz','2025-12-01T10:00:00Z','2026-06-08T11:00:00Z',false,null,'{"onboarding_completed":true}'),
('77777777-7777-7777-7777-777777777777','grace@example.com','Grace Kim','2025-08-15T10:00:00Z','2026-06-05T16:00:00Z',false,null,'{"onboarding_completed":true}'),
('88888888-8888-8888-8888-888888888888','henry@example.com','Henry Cole','2025-07-01T10:00:00Z','2026-04-20T10:00:00Z',false,'2026-05-30T10:00:00Z','{"onboarding_completed":true}'),
('99999999-9999-9999-9999-999999999999','ivy@example.com','Ivy Watson','2026-01-12T10:00:00Z','2026-06-02T13:00:00Z',false,null,'{"onboarding_completed":true}'),
('aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','jack@example.com','Jack Moss','2026-05-20T10:00:00Z','2026-05-21T09:00:00Z',false,null,'{"onboarding_completed":false}'),
('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb','kara@example.com','Kara Singh','2025-10-30T10:00:00Z','2026-05-28T15:00:00Z',false,null,'{"onboarding_completed":true}'),
('cccccccc-cccc-cccc-cccc-cccccccccccc','leo@example.com','Leo Tan','2025-09-22T10:00:00Z','2026-06-10T17:00:00Z',false,null,'{"onboarding_completed":true}');

-- ── subscriptions (ONLY u03 -> premium; everyone else absent == free) ──
insert into public.subscriptions (user_id, plan, status, started_at, current_period_end) values
('33333333-3333-3333-3333-333333333333','pro','active','2025-07-01T10:00:00Z','2026-07-01T10:00:00Z');

-- ── projects (active = archived false; u09 sits AT the free limit of 3) ──
insert into public.projects (user_id, name, created_at, archived) values
('11111111-1111-1111-1111-111111111111','Website Redesign','2025-09-02T10:00:00Z',false),
('11111111-1111-1111-1111-111111111111','Q4 Planning','2025-10-01T10:00:00Z',false),
('22222222-2222-2222-2222-222222222222','Marketing','2025-11-12T10:00:00Z',false),
('22222222-2222-2222-2222-222222222222','Sales Ops','2025-12-01T10:00:00Z',false),
('22222222-2222-2222-2222-222222222222','Hiring','2026-02-01T10:00:00Z',false),
('44444444-4444-4444-4444-444444444444','Personal','2025-10-06T10:00:00Z',false),
('44444444-4444-4444-4444-444444444444','Side Project','2025-11-01T10:00:00Z',false),
('66666666-6666-6666-6666-666666666666','Ops','2025-12-02T10:00:00Z',false),
('66666666-6666-6666-6666-666666666666','Roadmap','2026-01-15T10:00:00Z',false),
('77777777-7777-7777-7777-777777777777','Research','2025-08-16T10:00:00Z',false),
('77777777-7777-7777-7777-777777777777','Content','2025-09-01T10:00:00Z',false),
('88888888-8888-8888-8888-888888888888','Archive Me','2025-07-02T10:00:00Z',false),
('99999999-9999-9999-9999-999999999999','Launch','2026-01-13T10:00:00Z',false),
('99999999-9999-9999-9999-999999999999','Growth','2026-02-01T10:00:00Z',false),
('99999999-9999-9999-9999-999999999999','Support','2026-03-01T10:00:00Z',false),
('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb','Thesis','2025-11-01T10:00:00Z',false),
('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb','Reading List','2025-12-01T10:00:00Z',false),
('cccccccc-cccc-cccc-cccc-cccccccccccc','Engineering','2025-09-23T10:00:00Z',false),
('cccccccc-cccc-cccc-cccc-cccccccccccc','Design','2025-10-10T10:00:00Z',false),
('cccccccc-cccc-cccc-cccc-cccccccccccc','QA','2025-11-05T10:00:00Z',false);

-- ── tasks: representative rows showing recency of the core action.
--    (Add more freely; the agent counts rows and reads max(created_at).) ──
insert into public.tasks (user_id, title, status, created_at, completed_at) values
-- u01: dormant 21 days, last task 2026-05-25
('11111111-1111-1111-1111-111111111111','Finalize homepage','done','2026-05-20T10:00:00Z','2026-05-22T10:00:00Z'),
('11111111-1111-1111-1111-111111111111','Review copy','done','2026-05-25T10:00:00Z','2026-05-25T12:00:00Z'),
-- u02: active yesterday
('22222222-2222-2222-2222-222222222222','Ship campaign','in_progress','2026-06-14T09:00:00Z',null),
('22222222-2222-2222-2222-222222222222','Draft Q3 plan','done','2026-06-12T09:00:00Z','2026-06-13T09:00:00Z'),
-- u03: premium, heavy use
('33333333-3333-3333-3333-333333333333','Sprint 12 board','in_progress','2026-06-13T09:00:00Z',null),
-- u04: unsubscribed
('44444444-4444-4444-4444-444444444444','Tax docs','done','2026-05-30T10:00:00Z','2026-05-30T12:00:00Z'),
-- u06: active 2026-06-07 (but emailed 2 days ago in AI log)
('66666666-6666-6666-6666-666666666666','Quarterly review','done','2026-06-07T10:00:00Z','2026-06-07T15:00:00Z'),
-- u07: active 2026-06-04 (but emailed 1 day ago via legacy)
('77777777-7777-7777-7777-777777777777','Lit review','in_progress','2026-06-04T10:00:00Z',null),
-- u08: soft-deleted
('88888888-8888-8888-8888-888888888888','Old task','done','2026-04-19T10:00:00Z','2026-04-19T12:00:00Z'),
-- u09: at the free project limit, last task 2026-06-02
('99999999-9999-9999-9999-999999999999','Launch checklist','in_progress','2026-06-02T10:00:00Z',null),
-- u11: dormant 18 days, last task 2026-05-28 (re-eligible after old email)
('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb','Outline chapter 3','done','2026-05-28T10:00:00Z','2026-05-28T14:00:00Z'),
-- u12: power user, integrations, last task 2026-06-10
('cccccccc-cccc-cccc-cccc-cccccccccccc','Release 2.0','in_progress','2026-06-10T10:00:00Z',null);
-- (u05 and u10 intentionally have NO tasks -> "never performed core action" signal)

-- ── integrations (advanced-feature presence) ──
insert into public.integrations (user_id, provider, connected_at) values
('22222222-2222-2222-2222-222222222222','slack','2025-12-01T10:00:00Z'),
('33333333-3333-3333-3333-333333333333','slack','2025-08-01T10:00:00Z'),
('33333333-3333-3333-3333-333333333333','github','2025-09-01T10:00:00Z'),
('cccccccc-cccc-cccc-cccc-cccccccccccc','slack','2025-10-01T10:00:00Z'),
('cccccccc-cccc-cccc-cccc-cccccccccccc','github','2025-11-01T10:00:00Z');

-- ── legacy_email_sends (Rule 9 second frequency-cap source) ──
insert into public.legacy_email_sends (user_id, template_key, sent_at) values
-- u01: old legacy email (well outside cap) -> still eligible
('11111111-1111-1111-1111-111111111111','newsletter_april','2026-04-18T09:00:00Z'),
-- u07: legacy email 1 day ago -> INSIDE cap -> must be excluded
('77777777-7777-7777-7777-777777777777','newsletter_june','2026-06-14T09:00:00Z');
```

---

# 3. Product Context (use as the onboarding output)

Paste this as `product_context.md` (or feed the form inputs to
`onboarding/context_generator.py`).

```markdown
## Product Brief

**TaskFlow** is a freemium project-management SaaS for small teams and solo
professionals. Users organize work into **projects** and track **tasks** within
them; creating and completing tasks is the core habit the product is built around.

**Users & conversion model.** Free accounts may keep up to **3 active
(non-archived) projects** and have no integrations. Conversion to **Pro** (or
**Business**) unlocks unlimited projects plus integrations (Slack, GitHub,
Zapier). A user is **premium** if and only if they have a row in `subscriptions`
with `status = 'active'`; the *absence* of a subscription row means a free user.

**Brand tone.** Warm, concise, practical. Talk like a helpful teammate, never
like a salesperson. No hype, no pressure.

**Email hard rules (never do).** Never email premium (active-subscription) users.
Never email users who opted out (`users.marketing_opt_out = true`). Never email
soft-deleted users (`users.deleted_at is not null`). Respect the 4-day frequency
cap across BOTH the agent log and the legacy `legacy_email_sends` table.

**Exclusion rules.** Premium, unsubscribed, soft-deleted, accounts under 1 day
old, and anyone emailed within the last 4 days are ineligible.

**Extra context.** `last_active_at` reflects the last app session. `preferences`
is JSONB; `preferences.onboarding_completed = false` flags users who never set up
their workspace. Deep links: a project lives at `https://app.taskflow.com/p/{project_id}`,
the new-task screen at `https://app.taskflow.com/new`, billing at
`https://app.taskflow.com/upgrade`.

## Customer Notes

<!-- Freeform, customer-owned. Corrections and edge cases go here. -->
```

---

# 4. Expected schema map & approved signals

After introspection the **Schema Intelligence Agent** should map roughly:

| Concept | Where it lives |
|---|---|
| User identity / PK | `users.id` |
| Email | `users.email` |
| Account-creation timestamp | `users.created_at` |
| Premium status | **presence** of `subscriptions` row with `status='active'` (absent = free) |
| Unsubscribe state | `users.marketing_opt_out = true` |
| Soft-delete flag | `users.deleted_at is not null` |
| Core action | rows in `tasks` (count + `max(created_at)` for recency) |
| Last activity | `users.last_active_at` |
| Free project limit | count of `projects` where `archived = false` (limit 3) |
| Advanced features | rows in `integrations` |
| Legacy email log | `legacy_email_sends` (`user_id`, `sent_at`) |
| JSONB | `users.preferences` → `onboarding_completed` |

**Suggested approved signals** (the reasoning vocabulary to approve in review):

- `core_action_count` — total tasks created.
- `days_since_last_task` — recency of the core action.
- `days_since_last_active` — from `last_active_at`.
- `never_performed_core_action` — zero rows in `tasks`.
- `at_free_project_limit` — 3 active projects on a free plan.
- `advanced_feature_count` — number of connected integrations.
- `onboarding_incomplete` — `preferences.onboarding_completed = false`.

---

# 5. Expected outcomes — the test oracle

What a planning run on **2026-06-15** should produce. Filtered users never reach
the copywriter; eligible users get a per-user send/skip decision.

| # | user | outcome | reason |
|---|---|---|---|
| u01 | Alice | **SEND** | Eligible; dormant 21d with real history → re-engagement angle. |
| u02 | Ben | **SKIP** | Eligible but active yesterday & engaged — no nudge needed. |
| u03 | Chloe | **FILTERED** | Premium (active subscription). |
| u04 | Dan | **FILTERED** | Unsubscribed (`marketing_opt_out = true`). |
| u05 | Eve | **FILTERED** | Account < 1 day old (created 2026-06-14 23:10). |
| u06 | Frank | **FILTERED** | Emailed 2 days ago via the **agent log** (inside 4-day cap). |
| u07 | Grace | **FILTERED** | Emailed 1 day ago via the **legacy log** (Rule 9). |
| u08 | Henry | **FILTERED** | Soft-deleted (`deleted_at` set). |
| u09 | Ivy | **SEND** | Eligible; at the free 3-project limit → upgrade/limit angle. |
| u10 | Jack | **SEND** | Eligible; never created a task, onboarding incomplete → activation. |
| u11 | Kara | **SEND** | Eligible again (last email 12d ago); avoid repeating `winback_dormant_v1`. |
| u12 | Leo | **SEND** | Eligible; power user on free plan w/ integrations → value/upgrade angle. |

**Summary:** 12 users → **4 filtered**, **8 reach the copywriter** → ~**6 sends**,
**~2 skips** (u02 and possibly u12, at the copywriter's discretion).

> Hard guarantees to verify (PRD §7.2 / the 18 rules):
> - u03, u04, u05, u06, u07, u08 must **never** appear in the copywriter's input.
> - Every eligible user gets **exactly one** `record_send_decision` or
>   `record_skip_decision` (no silent drops).
> - u11's new email's `campaign_key` differs from `winback_dormant_v1`.
> - Subjects < 50 chars; no spam words; CTAs are deep links, not the homepage.
