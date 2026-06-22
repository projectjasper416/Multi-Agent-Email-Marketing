# Test Dataset #2 — Example Business: **PulseFit**

A second, independent end-to-end test, separate from TaskFlow. Use it to verify
the agent generalizes to a **different schema shape**. The SQL fixture is
[scripts/seed_test_data_pulsefit.sql](scripts/seed_test_data_pulsefit.sql).

**PulseFit** is a fitness / workout-tracking SaaS. What makes it a good *second*
test is that every schema convention differs from TaskFlow:

| Concept | TaskFlow | **PulseFit** |
|---|---|---|
| Identity table / PK / email | `users` / `id` / `email` | `members` / `member_id` / `email_address` |
| Premium | presence of `subscriptions` row | **inline column** `members.plan_tier in ('plus','pro')` |
| Soft-delete | `users.deleted_at` not null | **status enum** `members.status = 'deactivated'` |
| Unsubscribe | `users.marketing_opt_out = true` | **row present** in `communication_prefs` with `email_opt_out=true` (absent row = opted-in) |
| Core action | `tasks` | `workouts` (count + `logged_at`) |
| Legacy log | `legacy_email_sends` | `mailchimp_sends` (`member_id`, `send_date`) |

> Everything is dated relative to **today = 2026-06-22**. Set `CUSTOMER_ID=pulsefit`.
> Constants that matter: `FREQUENCY_CAP_DAYS=4` (no email if mailed since
> 2026-06-18), `MIN_ACCOUNT_AGE_DAYS=1` (signed up after 2026-06-21 = too new).

---

## How to run this test

1. Run `migrations/001..003` in your throwaway Supabase project (if not already).
2. Paste **all of** [scripts/seed_test_data_pulsefit.sql](scripts/seed_test_data_pulsefit.sql)
   into the Supabase SQL editor and run it.
3. Set `.env`: `CUSTOMER_ID=pulsefit` and point `SUPABASE_*` at the project.
4. In the onboarding UI, fill **Step 1** with the inputs in §1 below.
5. Run **Step 2** (Schema analysis) — confirm the discovered map matches §2.
6. **Step 3**: approve the signals in §3.
7. Trigger a planning run (`python handler_plan.py`) and compare the
   send/skip/filter decisions against the **oracle in §4**.

---

# 1. Onboarding UI inputs (Step 1 — Product brief)

Type these into the form fields verbatim (field names match the UI).

| Field | Value |
|---|---|
| **Brand name** | `PulseFit` |
| **From address** | `hello@pulsefit.app` |
| **Unsubscribe base URL** | `https://pulsefit.app/unsubscribe` |

**What does the product do?**
```
PulseFit is a workout-tracking app for everyday athletes. Members log workouts,
set fitness goals, and follow structured training programs. Logging a workout is
the core habit the product is built around; consistent logging is what keeps
members engaged and improving.
```

**Who are the users?**
```
Individuals who want to get fitter and stay consistent — beginners building a
habit, and committed athletes training for events. They sign up free, set a goal
(weight loss, strength, endurance), and log workouts. Power users train several
times a week and want structure.
```

**Conversion model**
```
Free members can log unlimited workouts but are capped at 3 active goals and
cannot enroll in structured training programs. Plus and Pro members get
unlimited goals and full access to coached programs. A member is "premium" if
and only if members.plan_tier is 'plus' or 'pro'; the default 'free' is not
premium. Conversion means upgrading from free to plus/pro.
```

**Brand tone**
```
Energetic, encouraging, and concrete. Talk like a supportive coach — celebrate
real progress, never guilt-trip. Concise, no hype, no fake urgency, no emojis.
```

**Email hard rules**
```
Never email premium members (plan_tier plus or pro). Never email deactivated
members (status = 'deactivated'). Never promise specific fitness results or
medical claims. Respect the 4-day frequency cap across BOTH the agent's own log
and the legacy mailchimp_sends table.
```

**Exclusion rules**
```
Do not email: premium members (plan_tier != 'free'); members who opted out
(a row in communication_prefs with email_opt_out = true — note that the ABSENCE
of a row means opted-in); deactivated members; accounts less than 1 day old; and
anyone emailed within the last 4 days.
```

**Anything else?** (schema hints — the operator knows their own DB)
```
Schema notes: identity is members (PK member_id, email in email_address, signup
in signup_date, recency in last_login_at). Premium is the inline column
plan_tier. Soft-delete is status='deactivated'. Unsubscribe is the PRESENCE of a
communication_prefs row with email_opt_out=true (absent row = opted-in). Core
action is the workouts table (count rows, recency via logged_at). Goals live in
goals (free capped at 3 where is_active=true). Structured programs (advanced
feature) live in program_enrollments. The legacy marketing log is mailchimp_sends
with columns member_id and send_date. meta is JSONB: meta.goal and
meta.onboarding_done. Deep links: a member's dashboard is
https://app.pulsefit.app/home, log a workout at https://app.pulsefit.app/log,
upgrade at https://app.pulsefit.app/upgrade.
```

---

# 2. Expected schema map (verify Step 2 discovered this)

| Concept | Where the agent should map it |
|---|---|
| User identity / PK | `members.member_id` |
| Email | `members.email_address` |
| Account-creation timestamp | `members.signup_date` |
| Premium status | `members.plan_tier in ('plus','pro')` (default `'free'` = not premium) |
| Unsubscribe state | row in `communication_prefs` with `email_opt_out = true` (**absent = opted-in**) |
| Soft-delete | `members.status = 'deactivated'` |
| Core action | rows in `workouts` (count + `max(logged_at)`) |
| Last activity | `members.last_login_at` |
| Free goal limit | `goals` where `is_active = true` (limit 3) |
| Advanced feature | rows in `program_enrollments` |
| Legacy email log | `mailchimp_sends` (`member_id`, `send_date`) |
| JSONB | `members.meta` → `goal`, `onboarding_done` |

> The schema agent should flag in **Confidence Notes** that `communication_prefs`
> uses inverted/absent-row semantics and that premium is an inline enum, not a
> join.

---

# 3. Signals to approve (Step 3)

The raw-signal list will be longer; approve at least these so the copywriter has
useful vocabulary:

- `workout_count` — total workouts logged.
- `days_since_last_workout` — recency of the core action (from `logged_at`).
- `days_since_last_login` — from `members.last_login_at`.
- `never_logged_workout` — zero rows in `workouts`.
- `at_free_goal_limit` — 3 active goals on a free plan.
- `tried_program` — has a `program_enrollments` row (esp. `completed`/`cancelled`).
- `onboarding_incomplete` — `meta.onboarding_done = false`.
- `primary_goal` — `meta.goal` (personalization).

---

# 4. Expected outcomes — the test oracle

What a planning run on **2026-06-22** should produce. 13 members.

| # | member | plan | outcome | reason |
|---|---|---|---|---|
| m01 | Aaron Wells | free | **SEND** | Eligible; last workout 26 days ago, has history → re-engagement. |
| m02 | Bella Norton | free | **SKIP** | Eligible (explicit opt-in row) but trained 2 days ago — engaged, no nudge. |
| m03 | Carlos Mendez | **pro** | **FILTERED** | Premium (`plan_tier = 'pro'`). |
| m04 | Dina Patel | **plus** | **FILTERED** | Premium (`plan_tier = 'plus'`). |
| m05 | Erik Olsen | free | **FILTERED** | Unsubscribed (`communication_prefs.email_opt_out = true`). |
| m06 | Farah Aziz | free | **FILTERED** | Account < 1 day old (signed up 2026-06-22). |
| m07 | Greg Hall | free | **FILTERED** | Emailed 2 days ago via the **agent log** (inside 4-day cap). |
| m08 | Hana Kim | free | **FILTERED** | Emailed 1 day ago via the **legacy log** `mailchimp_sends` (Rule 9). |
| m09 | Igor Petrov | free | **FILTERED** | Soft-deleted (`status = 'deactivated'`). |
| m10 | Julia Reyes | free | **SEND** | Eligible; never logged a workout, onboarding incomplete → activation. |
| m11 | Kevin Brooks | free | **SEND** | Eligible again (last email 11d ago); avoid repeating `winback_v1`. |
| m12 | Lena Vogt | free | **SEND** | Eligible; heavy free user at the 3-goal limit, tried a program → upgrade angle. |
| m13 | Marco Bianchi | free | **SEND** | Eligible; dormant 28d, last legacy email 35d ago (outside cap) → re-engagement. |

**Summary:** 13 members → **7 filtered**, **6 reach the copywriter** → ~**5 sends**,
**~1 skip** (m02).

### Hard guarantees to verify (the 18 rules)
- m03, m04, m05, m06, m07, m08, m09 must **never** appear in the copywriter's input.
- The two premium members are filtered via the **inline `plan_tier` column** (no
  subscriptions table exists here) — proves premium mapping isn't hardcoded.
- m05 is filtered by a **present** `communication_prefs` row; m02 has a present
  row with `email_opt_out=false` and is **still eligible** (absent-row semantics
  handled correctly).
- m08 is filtered by the **legacy** `mailchimp_sends` table, not the agent log —
  the frequency cap checks both sources.
- Every eligible member gets **exactly one** `record_send_decision` or
  `record_skip_decision` (no silent drops).
- m11's new email's `campaign_key` differs from `winback_v1`.
- Subjects < 50 chars; no spam words (free/winner/urgent/limited time/act now/
  guaranteed); CTAs are deep links (e.g. `/log`, `/upgrade`), never the homepage.

> For the legacy-log cap to fire in code, the approved-behaviors metadata must
> carry the legacy-log coordinates the schema map identified:
> `{"table":"mailchimp_sends","user_col":"member_id","sent_at_col":"send_date"}`
> (this is how `supabase_service.user_ids_emailed_within` finds the second
> source). If you're testing only the deterministic filter, you can confirm m08
> is excluded by that path.
