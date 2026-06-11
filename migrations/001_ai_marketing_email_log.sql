-- ─────────────────────────────────────────────────────────────────────
-- ai_marketing_email_log  (TDD §19.1, PRD §13.3)
-- Permanent audit trail. One row per user per run — both sends and skips.
-- Nothing is ever deleted from this table.
-- Run this in the customer's Supabase before the first run.
-- ─────────────────────────────────────────────────────────────────────
create table if not exists public.ai_marketing_email_log (
    id                  uuid primary key default gen_random_uuid(),
    customer_id         text        not null,
    run_id              text        not null,
    user_id             uuid        references auth.users(id) on delete cascade,
    sent_to_email       text,
    campaign_key        text,
    subject             text,
    decision            text        not null check (decision in ('sent', 'skipped')),
    status              text        not null default 'sent' check (status in ('sent', 'failed')),
    decision_reason     text,
    signals_snapshot    jsonb,
    model               text,
    provider_message_id text,
    error_message       text,
    sent_at             timestamptz not null default now(),
    created_at          timestamptz not null default now()
);

-- Indexes (TDD §19.1).
create index if not exists idx_email_log_user_id
    on public.ai_marketing_email_log (user_id);
create index if not exists idx_email_log_sent_at
    on public.ai_marketing_email_log (sent_at desc);     -- frequency-cap recency
create index if not exists idx_email_log_campaign_key
    on public.ai_marketing_email_log (campaign_key);     -- analytics

-- RLS: users may read only their own rows. The agent inserts via the service
-- role key, which bypasses RLS entirely.
alter table public.ai_marketing_email_log enable row level security;

drop policy if exists "users read own email log" on public.ai_marketing_email_log;
create policy "users read own email log"
    on public.ai_marketing_email_log
    for select
    using (auth.uid() = user_id);
