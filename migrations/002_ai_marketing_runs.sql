-- ─────────────────────────────────────────────────────────────────────
-- ai_marketing_runs  (TDD §14.3–§14.5)
-- Tracks the lifecycle of each planned batch: pending -> approved/cancelled/
-- expired -> sent. Powers the expiry sweep, the Telegram summary edit, and the
-- optimistic-concurrency guard on approve/cancel.
-- ─────────────────────────────────────────────────────────────────────
create table if not exists public.ai_marketing_runs (
    run_id             text        primary key,
    customer_id        text        not null,
    status             text        not null default 'pending_approval'
                         check (status in ('pending_approval','approved','cancelled','expired','sent')),
    summary_message_id bigint,                     -- Telegram message to edit later
    expires_at         timestamptz not null,
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now()
);

create index if not exists idx_runs_status
    on public.ai_marketing_runs (customer_id, status);
create index if not exists idx_runs_expires_at
    on public.ai_marketing_runs (expires_at);

alter table public.ai_marketing_runs enable row level security;
-- No user-facing policy: this is operational state, accessed only via the
-- service role key from the agent.
