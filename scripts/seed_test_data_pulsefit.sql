-- ─────────────────────────────────────────────────────────────────────
-- seed_test_data_pulsefit.sql — one-shot test fixtures for the example
-- business "PulseFit" (a fitness / workout-tracking SaaS).
--
-- This is a SECOND, independent dataset (see TEST_DATA_PULSEFIT.md for the
-- onboarding inputs + expected-outcome oracle). It is deliberately built with
-- DIFFERENT schema conventions from TaskFlow so it exercises the Schema
-- Intelligence Agent's generalization:
--   * premium is an INLINE COLUMN  (members.plan_tier), not a separate table
--   * soft-delete is a STATUS ENUM (members.status = 'deactivated'), not deleted_at
--   * unsubscribe is the PRESENCE of a row in communication_prefs with
--     email_opt_out = true  (ABSENT row == opted-in)
--   * identity table/PK/email use non-obvious names (members / member_id / email_address)
--   * the legacy email log is mailchimp_sends (member_id, send_date)
--
-- Run order:
--   1. migrations/001..003 (agent tables + exec_sql RPC)
--   2. this file
--
-- Dated relative to today = 2026-06-22. CUSTOMER_ID must be 'pulsefit'.
-- The frequency cap (4 days) excludes anyone mailed since 2026-06-18; accounts
-- created after 2026-06-21 are too new. Idempotent: drops the tables first.
-- ─────────────────────────────────────────────────────────────────────

-- ── reset business tables ──────────────────────────────────────────
drop table if exists public.mailchimp_sends cascade;
drop table if exists public.program_enrollments cascade;
drop table if exists public.goals cascade;
drop table if exists public.workouts cascade;
drop table if exists public.communication_prefs cascade;
drop table if exists public.members cascade;

-- ── DDL ─────────────────────────────────────────────────────────────
-- members: identity. premium = plan_tier in ('plus','pro'); soft-delete via
-- status='deactivated'; recency via last_login_at; JSONB meta for goal/flags.
create table public.members (
    member_id      uuid primary key default gen_random_uuid(),
    email_address  text not null unique,
    display_name   text,
    signup_date    timestamptz not null default now(),   -- account age
    last_login_at  timestamptz,                            -- recency signal
    plan_tier      text not null default 'free'
                     check (plan_tier in ('free','plus','pro')),  -- premium = not 'free'
    status         text not null default 'active'
                     check (status in ('active','deactivated')),  -- soft delete
    meta           jsonb default '{}'::jsonb               -- {"goal":..,"onboarding_done":bool}
);

-- communication_prefs: ABSENT row == opted-in. A row with email_opt_out=true
-- means the member unsubscribed. (Inverse of a plain boolean column.)
create table public.communication_prefs (
    member_id     uuid primary key references public.members(member_id) on delete cascade,
    email_opt_out boolean not null default false,
    updated_at    timestamptz not null default now()
);

-- workouts: the CORE ACTION (count + recency via logged_at).
create table public.workouts (
    id            uuid primary key default gen_random_uuid(),
    member_id     uuid not null references public.members(member_id) on delete cascade,
    workout_type  text not null,
    duration_min  int,
    logged_at     timestamptz not null default now()
);

-- goals: free tier capped at 3 ACTIVE goals; pro/plus unlimited.
create table public.goals (
    id          uuid primary key default gen_random_uuid(),
    member_id   uuid not null references public.members(member_id) on delete cascade,
    goal_type   text not null,
    is_active   boolean not null default true,
    created_at  timestamptz not null default now()
);

-- program_enrollments: advanced/premium feature (structured training programs).
create table public.program_enrollments (
    id            uuid primary key default gen_random_uuid(),
    member_id     uuid not null references public.members(member_id) on delete cascade,
    program_name  text not null,
    status        text not null check (status in ('active','completed','cancelled')),
    enrolled_at   timestamptz not null default now()
);

-- mailchimp_sends: pre-existing marketing log (Rule 9 second cap source).
create table public.mailchimp_sends (
    id         uuid primary key default gen_random_uuid(),
    member_id  uuid not null references public.members(member_id) on delete cascade,
    campaign   text,
    send_date  timestamptz not null
);

-- ── members ─────────────────────────────────────────────────────────
insert into public.members (member_id, email_address, display_name, signup_date, last_login_at, plan_tier, status, meta) values
('10000000-0000-0000-0000-000000000001','aaron@example.com','Aaron Wells','2025-08-10T10:00:00Z','2026-05-27T08:00:00Z','free','active','{"goal":"weight_loss","onboarding_done":true}'),
('10000000-0000-0000-0000-000000000002','bella@example.com','Bella Norton','2025-10-02T10:00:00Z','2026-06-21T19:00:00Z','free','active','{"goal":"strength","onboarding_done":true}'),
('10000000-0000-0000-0000-000000000003','carlos@example.com','Carlos Mendez','2025-05-01T10:00:00Z','2026-06-20T07:00:00Z','pro','active','{"goal":"marathon","onboarding_done":true}'),
('10000000-0000-0000-0000-000000000004','dina@example.com','Dina Patel','2025-09-15T10:00:00Z','2026-06-17T18:00:00Z','plus','active','{"goal":"strength","onboarding_done":true}'),
('10000000-0000-0000-0000-000000000005','erik@example.com','Erik Olsen','2025-11-20T10:00:00Z','2026-06-08T12:00:00Z','free','active','{"goal":"endurance","onboarding_done":true}'),
('10000000-0000-0000-0000-000000000006','farah@example.com','Farah Aziz','2026-06-22T07:00:00Z','2026-06-22T07:30:00Z','free','active','{"onboarding_done":false}'),
('10000000-0000-0000-0000-000000000007','greg@example.com','Greg Hall','2025-12-05T10:00:00Z','2026-06-16T20:00:00Z','free','active','{"goal":"weight_loss","onboarding_done":true}'),
('10000000-0000-0000-0000-000000000008','hana@example.com','Hana Kim','2025-07-22T10:00:00Z','2026-06-12T09:00:00Z','free','active','{"goal":"flexibility","onboarding_done":true}'),
('10000000-0000-0000-0000-000000000009','igor@example.com','Igor Petrov','2025-06-30T10:00:00Z','2026-04-22T10:00:00Z','free','deactivated','{"goal":"strength","onboarding_done":true}'),
('10000000-0000-0000-0000-000000000010','julia@example.com','Julia Reyes','2026-06-01T10:00:00Z','2026-06-02T11:00:00Z','free','active','{"onboarding_done":false}'),
('10000000-0000-0000-0000-000000000011','kevin@example.com','Kevin Brooks','2025-09-01T10:00:00Z','2026-05-29T08:00:00Z','free','active','{"goal":"weight_loss","onboarding_done":true}'),
('10000000-0000-0000-0000-000000000012','lena@example.com','Lena Vogt','2025-08-05T10:00:00Z','2026-06-16T07:00:00Z','free','active','{"goal":"strength","onboarding_done":true}'),
('10000000-0000-0000-0000-000000000013','marco@example.com','Marco Bianchi','2025-10-10T10:00:00Z','2026-05-25T19:00:00Z','free','active','{"goal":"endurance","onboarding_done":true}');

-- ── communication_prefs (absent == opted-in; only m05 opted OUT) ────
insert into public.communication_prefs (member_id, email_opt_out) values
('10000000-0000-0000-0000-000000000002', false),  -- m02 explicit opt-in (still eligible)
('10000000-0000-0000-0000-000000000005', true);   -- m05 unsubscribed -> excluded

-- ── workouts (core action; m06 & m10 intentionally have none) ───────
insert into public.workouts (member_id, workout_type, duration_min, logged_at) values
-- m01: dormant 26 days, last workout 2026-05-27
('10000000-0000-0000-0000-000000000001','run',32,'2026-05-25T07:00:00Z'),
('10000000-0000-0000-0000-000000000001','strength',45,'2026-05-27T07:00:00Z'),
-- m02: active this week
('10000000-0000-0000-0000-000000000002','strength',50,'2026-06-19T18:00:00Z'),
('10000000-0000-0000-0000-000000000002','yoga',30,'2026-06-20T18:00:00Z'),
-- m03: premium, active
('10000000-0000-0000-0000-000000000003','run',60,'2026-06-19T06:00:00Z'),
-- m04: premium, active
('10000000-0000-0000-0000-000000000004','strength',40,'2026-06-16T17:00:00Z'),
-- m05: unsubscribed
('10000000-0000-0000-0000-000000000005','cycling',55,'2026-06-06T07:00:00Z'),
-- m07: emailed 2 days ago (agent log) -> excluded
('10000000-0000-0000-0000-000000000007','run',28,'2026-06-15T07:00:00Z'),
-- m08: emailed 1 day ago (legacy) -> excluded
('10000000-0000-0000-0000-000000000008','yoga',35,'2026-06-11T07:00:00Z'),
-- m09: deactivated
('10000000-0000-0000-0000-000000000009','strength',40,'2026-04-21T07:00:00Z'),
-- m11: dormant 24 days, last workout 2026-05-29 (re-eligible after old email)
('10000000-0000-0000-0000-000000000011','run',30,'2026-05-29T07:00:00Z'),
-- m12: heavy free user, last workout 2026-06-16
('10000000-0000-0000-0000-000000000012','strength',50,'2026-06-12T07:00:00Z'),
('10000000-0000-0000-0000-000000000012','run',40,'2026-06-14T07:00:00Z'),
('10000000-0000-0000-0000-000000000012','cycling',60,'2026-06-16T07:00:00Z'),
-- m13: dormant 28 days, last workout 2026-05-25
('10000000-0000-0000-0000-000000000013','run',35,'2026-05-25T07:00:00Z');

-- ── goals (free tier capped at 3 ACTIVE; m12 sits AT the limit) ─────
insert into public.goals (member_id, goal_type, is_active) values
('10000000-0000-0000-0000-000000000001','lose_5kg',true),
('10000000-0000-0000-0000-000000000002','bench_100kg',true),
('10000000-0000-0000-0000-000000000002','run_10k',true),
('10000000-0000-0000-0000-000000000011','lose_3kg',true),
('10000000-0000-0000-0000-000000000012','squat_120kg',true),
('10000000-0000-0000-0000-000000000012','run_half_marathon',true),
('10000000-0000-0000-0000-000000000012','bodyweight_pullups',true),  -- m12 -> 3 active = at free limit
('10000000-0000-0000-0000-000000000013','sub_50_10k',true);

-- ── program_enrollments (advanced feature; mostly premium) ──────────
insert into public.program_enrollments (member_id, program_name, status, enrolled_at) values
('10000000-0000-0000-0000-000000000003','Marathon Builder','active','2026-03-08T10:00:00Z'),
('10000000-0000-0000-0000-000000000004','Strength 5x5','active','2026-04-08T10:00:00Z'),
('10000000-0000-0000-0000-000000000012','Beginner Trial','completed','2025-09-01T10:00:00Z');  -- free user, past trial

-- ── mailchimp_sends (legacy log; Rule 9 second cap source) ──────────
insert into public.mailchimp_sends (member_id, campaign, send_date) values
('10000000-0000-0000-0000-000000000008','june_promo','2026-06-21T09:00:00Z'),   -- m08: 1 day ago -> excluded
('10000000-0000-0000-0000-000000000013','spring_newsletter','2026-05-18T09:00:00Z');  -- m13: 35 days ago -> eligible

-- ── agent-log frequency-cap fixtures (customer_id must match CUSTOMER_ID) ──
-- The agent log's user_id FK targets auth.users; our test members live only in
-- public.members, so drop the FK for the fixture (same as the TaskFlow seed).
alter table public.ai_marketing_email_log
    drop constraint if exists ai_marketing_email_log_user_id_fkey;

insert into public.ai_marketing_email_log
  (customer_id, run_id, user_id, sent_to_email, campaign_key, subject, decision, status, sent_at)
values
  ('pulsefit','run_2026-06-20','10000000-0000-0000-0000-000000000007',
   'greg@example.com','streak_nudge','Keep your run streak alive',
   'sent','sent','2026-06-20T09:05:00Z'),   -- m07: 2 days ago -> excluded
  ('pulsefit','run_2026-06-11','10000000-0000-0000-0000-000000000011',
   'kevin@example.com','winback_v1','Your workouts are waiting, Kevin',
   'sent','sent','2026-06-11T09:04:00Z');   -- m11: 11 days ago -> eligible again (avoid repeat)
