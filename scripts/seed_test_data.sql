-- ─────────────────────────────────────────────────────────────────────
-- seed_test_data.sql — one-shot test fixtures for the example business "TaskFlow"
-- See TEST_DATA.md for the full explanation and the expected-outcome oracle.
--
-- Run order:
--   1. migrations/001..003 (agent tables + exec_sql RPC)
--   2. this file (creates TaskFlow tables, rows, and frequency-cap fixtures)
--
-- Dated relative to today = 2026-06-15. CUSTOMER_ID must be 'taskflow'.
-- Idempotent-ish: drops the TaskFlow tables first so you can re-seed freely.
-- ─────────────────────────────────────────────────────────────────────

-- ── reset business tables ──────────────────────────────────────────
drop table if exists public.legacy_email_sends cascade;
drop table if exists public.integrations cascade;
drop table if exists public.tasks cascade;
drop table if exists public.projects cascade;
drop table if exists public.subscriptions cascade;
drop table if exists public.users cascade;

-- ── DDL ─────────────────────────────────────────────────────────────
create table public.users (
    id                uuid primary key default gen_random_uuid(),
    email             text not null unique,
    full_name         text,
    created_at        timestamptz not null default now(),
    last_active_at    timestamptz,
    marketing_opt_out boolean not null default false,
    deleted_at        timestamptz,
    preferences       jsonb default '{}'::jsonb
);

create table public.subscriptions (
    id                  uuid primary key default gen_random_uuid(),
    user_id             uuid not null references public.users(id) on delete cascade,
    plan                text not null check (plan in ('pro','business')),
    status              text not null check (status in ('active','past_due','canceled')),
    started_at          timestamptz not null,
    current_period_end  timestamptz
);

create table public.projects (
    id          uuid primary key default gen_random_uuid(),
    user_id     uuid not null references public.users(id) on delete cascade,
    name        text not null,
    created_at  timestamptz not null default now(),
    archived    boolean not null default false
);

create table public.tasks (
    id           uuid primary key default gen_random_uuid(),
    user_id      uuid not null references public.users(id) on delete cascade,
    project_id   uuid references public.projects(id) on delete set null,
    title        text not null,
    status       text not null default 'todo' check (status in ('todo','in_progress','done')),
    created_at   timestamptz not null default now(),
    completed_at timestamptz
);

create table public.integrations (
    id           uuid primary key default gen_random_uuid(),
    user_id      uuid not null references public.users(id) on delete cascade,
    provider     text not null check (provider in ('slack','github','zapier')),
    connected_at timestamptz not null default now()
);

create table public.legacy_email_sends (
    id           uuid primary key default gen_random_uuid(),
    user_id      uuid not null references public.users(id) on delete cascade,
    template_key text,
    sent_at      timestamptz not null
);

-- ── users ───────────────────────────────────────────────────────────
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

-- ── subscriptions (only u03 -> premium) ─────────────────────────────
insert into public.subscriptions (user_id, plan, status, started_at, current_period_end) values
('33333333-3333-3333-3333-333333333333','pro','active','2025-07-01T10:00:00Z','2026-07-01T10:00:00Z');

-- ── projects (active = archived false; u09 at the free limit of 3) ──
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

-- ── tasks (core action; u05 & u10 intentionally have none) ──────────
insert into public.tasks (user_id, title, status, created_at, completed_at) values
('11111111-1111-1111-1111-111111111111','Finalize homepage','done','2026-05-20T10:00:00Z','2026-05-22T10:00:00Z'),
('11111111-1111-1111-1111-111111111111','Review copy','done','2026-05-25T10:00:00Z','2026-05-25T12:00:00Z'),
('22222222-2222-2222-2222-222222222222','Ship campaign','in_progress','2026-06-14T09:00:00Z',null),
('22222222-2222-2222-2222-222222222222','Draft Q3 plan','done','2026-06-12T09:00:00Z','2026-06-13T09:00:00Z'),
('33333333-3333-3333-3333-333333333333','Sprint 12 board','in_progress','2026-06-13T09:00:00Z',null),
('44444444-4444-4444-4444-444444444444','Tax docs','done','2026-05-30T10:00:00Z','2026-05-30T12:00:00Z'),
('66666666-6666-6666-6666-666666666666','Quarterly review','done','2026-06-07T10:00:00Z','2026-06-07T15:00:00Z'),
('77777777-7777-7777-7777-777777777777','Lit review','in_progress','2026-06-04T10:00:00Z',null),
('88888888-8888-8888-8888-888888888888','Old task','done','2026-04-19T10:00:00Z','2026-04-19T12:00:00Z'),
('99999999-9999-9999-9999-999999999999','Launch checklist','in_progress','2026-06-02T10:00:00Z',null),
('bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb','Outline chapter 3','done','2026-05-28T10:00:00Z','2026-05-28T14:00:00Z'),
('cccccccc-cccc-cccc-cccc-cccccccccccc','Release 2.0','in_progress','2026-06-10T10:00:00Z',null);

-- ── integrations (advanced-feature presence) ────────────────────────
insert into public.integrations (user_id, provider, connected_at) values
('22222222-2222-2222-2222-222222222222','slack','2025-12-01T10:00:00Z'),
('33333333-3333-3333-3333-333333333333','slack','2025-08-01T10:00:00Z'),
('33333333-3333-3333-3333-333333333333','github','2025-09-01T10:00:00Z'),
('cccccccc-cccc-cccc-cccc-cccccccccccc','slack','2025-10-01T10:00:00Z'),
('cccccccc-cccc-cccc-cccc-cccccccccccc','github','2025-11-01T10:00:00Z');

-- ── legacy_email_sends (Rule 9 second frequency-cap source) ─────────
insert into public.legacy_email_sends (user_id, template_key, sent_at) values
('11111111-1111-1111-1111-111111111111','newsletter_april','2026-04-18T09:00:00Z'),  -- old, still eligible
('77777777-7777-7777-7777-777777777777','newsletter_june','2026-06-14T09:00:00Z');   -- 1 day ago, excluded

-- ── agent-log frequency-cap fixtures (customer_id must match CUSTOMER_ID) ──
-- The log's user_id FK targets auth.users (real Supabase identities). Our test
-- users live only in public.users, so drop the FK for the test fixture.
alter table public.ai_marketing_email_log
    drop constraint if exists ai_marketing_email_log_user_id_fkey;

insert into public.ai_marketing_email_log
  (customer_id, run_id, user_id, sent_to_email, campaign_key, subject, decision, status, sent_at)
values
  ('taskflow','run_2026-06-13','66666666-6666-6666-6666-666666666666',
   'frank@example.com','feature_nudge_integrations','Connect Slack in one click',
   'sent','sent','2026-06-13T09:05:00Z'),   -- u06: 2 days ago -> excluded
  ('taskflow','run_2026-06-03','bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
   'kara@example.com','winback_dormant_v1','Your TaskFlow projects are waiting',
   'sent','sent','2026-06-03T09:04:00Z');   -- u11: 12 days ago -> eligible again
