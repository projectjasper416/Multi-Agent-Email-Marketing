"""
Run-state tracking in Supabase (TDD §14.3–§14.5).

A small `ai_marketing_runs` table tracks the lifecycle of each planned batch
separately from the per-email audit log. It is what powers three things:

  * the auto-expiry sweep (§14.3): find pending runs past their expiry and close
    them before a new run begins;
  * the Telegram summary-message edit (we persist the summary message id here);
  * optimistic concurrency on approve/cancel (§14.5, Rule 14): every transition
    is a conditional UPDATE filtered on the expected current status, so a double
    tap loses silently instead of triggering two send waves.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import config
from services.supabase_service import get_client

STATUS_PENDING = "pending_approval"
STATUS_APPROVED = "approved"
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"
STATUS_SENT = "sent"

_TABLE = "ai_marketing_runs"


def create_pending_run(run_id: str, summary_message_id: int | None) -> None:
    """Record a freshly-planned run awaiting operator approval."""
    expires = datetime.now(timezone.utc) + timedelta(hours=config.APPROVAL_WINDOW_HOURS)
    get_client().table(_TABLE).insert(
        {
            "run_id": run_id,
            "customer_id": config.CUSTOMER_ID,
            "status": STATUS_PENDING,
            "summary_message_id": summary_message_id,
            "expires_at": expires.isoformat(),
        }
    ).execute()


def transition(run_id: str, from_status: str, to_status: str) -> bool:
    """Atomically move a run from one status to another.

    Returns True only if THIS call performed the transition. Implemented as a
    conditional update (filtered on the expected current status) so concurrent
    callers can't both win — the second one updates zero rows and gets False.
    """
    resp = (
        get_client()
        .table(_TABLE)
        .update({"status": to_status, "updated_at": datetime.now(timezone.utc).isoformat()})
        .eq("run_id", run_id)
        .eq("customer_id", config.CUSTOMER_ID)
        .eq("status", from_status)
        .execute()
    )
    return bool(resp.data)


def get_run(run_id: str) -> dict[str, Any] | None:
    rows = (
        get_client()
        .table(_TABLE)
        .select("*")
        .eq("run_id", run_id)
        .eq("customer_id", config.CUSTOMER_ID)
        .limit(1)
        .execute()
        .data
    )
    return rows[0] if rows else None


def sweep_expired() -> list[dict[str, Any]]:
    """Mark every pending run past its expiry as expired. Returns those runs so
    the caller can notify the operator. Runs at the START of every planning
    invocation, before any new work (Rule 13)."""
    now = datetime.now(timezone.utc).isoformat()
    pending = (
        get_client()
        .table(_TABLE)
        .select("*")
        .eq("customer_id", config.CUSTOMER_ID)
        .eq("status", STATUS_PENDING)
        .lt("expires_at", now)
        .execute()
        .data
    )
    expired_now: list[dict[str, Any]] = []
    for run in pending:
        # Conditional transition guards against a race with a late approval.
        if transition(run["run_id"], STATUS_PENDING, STATUS_EXPIRED):
            expired_now.append(run)
    return expired_now
