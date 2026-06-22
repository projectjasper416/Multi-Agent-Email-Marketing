"""
Central configuration and constants.

One place to read environment variables so the rest of the code never touches
os.environ directly. Also holds the system-wide constants (model id, frequency
cap, approval window) that the PRD/TDD pin down.
"""
from __future__ import annotations

import os

try:
    # Local dev convenience only. On Lambda the vars are injected by the runtime.
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover - dotenv is optional in prod
    pass


def _get(name: str, default: str | None = None, required: bool = False) -> str | None:
    val = os.environ.get(name, default)
    if required and not val:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return val


# ── Identity ──────────────────────────────────────────────────────────
CUSTOMER_ID: str = _get("CUSTOMER_ID", "")  # namespaces all Store reads/writes

# ── Supabase ──────────────────────────────────────────────────────────
SUPABASE_URL: str = _get("SUPABASE_URL", "")
SUPABASE_SERVICE_ROLE_KEY: str = _get("SUPABASE_SERVICE_ROLE_KEY", "")
# Anon (public) key — used ONLY by the onboarding web auth layer to talk to
# Supabase Auth (GoTrue): sign-up, sign-in, JWT verification, token refresh.
# Deliberately separate from the service-role key: auth runs as the end user,
# never with RLS bypassed.
SUPABASE_ANON_KEY: str = _get("SUPABASE_ANON_KEY", "")
# Raw Postgres connection string for the checkpointer + store (psycopg).
SUPABASE_DB_URL: str = _get("SUPABASE_DB_URL", "")

# ── Onboarding web auth ───────────────────────────────────────────────
# httpOnly session cookies. Set WEB_COOKIE_SECURE=true behind HTTPS in prod so
# the cookie is only sent over TLS; left false for local http dev, otherwise the
# browser silently refuses to store a Secure cookie on http://localhost.
WEB_COOKIE_SECURE: bool = _get("WEB_COOKIE_SECURE", "false").lower() == "true"

# ── LLM ───────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str = _get("ANTHROPIC_API_KEY", "")
# Pinned model id — no floating aliases. A model bump requires a side-by-side
# decision-quality audit before changing this (TDD 2.2).
MODEL: str = "claude-haiku-4-5-20251001"
# Must comfortably fit the schema agent's one-shot submit_schema_map payload
# (full schema_map.md + raw_signals array). 2048 truncated it mid-tool-call,
# yielding empty args and zero signals. claude-sonnet-4-6 supports far more.
MAX_TOKENS: int = 16384

# ── Email ─────────────────────────────────────────────────────────────
RESEND_API_KEY: str = _get("RESEND_API_KEY", "")

# ── Telegram ──────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = _get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID: str = _get("TELEGRAM_CHAT_ID", "")
TELEGRAM_WEBHOOK_SECRET: str = _get("TELEGRAM_WEBHOOK_SECRET", "")
TELEGRAM_API_BASE: str = "https://api.telegram.org"

# ── Business rules (PRD §7.2, §7.4, §15) ──────────────────────────────
FREQUENCY_CAP_DAYS: int = 4          # no email if any system mailed within N days
MIN_ACCOUNT_AGE_DAYS: int = 1        # accounts younger than this are ineligible
SCHEMA_MAX_AGE_DAYS: int = 7         # weekly schema freshness check
APPROVAL_WINDOW_HOURS: int = 2       # auto-expire a pending run after this
TELEGRAM_SEND_DELAY_SECONDS: float = 1.0  # per-chat rate limit cushion
