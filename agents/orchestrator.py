"""
Orchestrator (supervisor) node (TDD §8.1).

The first node on every run. Two jobs, both deterministic — NO LLM call:

  1. Load the three permanent context documents from LangGraph Store into graph
     state, so every downstream node reads from state, not the store.
  2. Decide whether the schema map needs refreshing and set
     `schema_refresh_needed`, which the graph's conditional edge uses to route.

Schema refresh conditions (PRD §6.4 / TDD §5.2), checked in order:
  a) the settings page flagged a manual re-analysis (refresh_requested)
  b) the schema map is missing entirely (un-onboarded / first run)
  c) the schema map is older than SCHEMA_MAX_AGE_DAYS (weekly freshness)
  d) the database fingerprint (credentials) changed since last analysis
"""
from __future__ import annotations

from datetime import datetime, timezone

import config
from graph.state import GraphState
from services import store_service


async def orchestrator_node(state: GraphState, *, store) -> dict:
    customer_id = state["customer_id"]

    ctx = await store_service.load_customer_context(store, customer_id)

    # Onboarding guard. A daily/planning run requires a fully-onboarded
    # customer. If the customer_id does not resolve to onboarding memory — e.g.
    # the run's CUSTOMER_ID does not match the auth uid used during onboarding —
    # every document comes back absent. Fail loud here rather than silently
    # proceeding on empty context (which would target the wrong product) or
    # auto-running the schema agent to bootstrap from nothing. Building these
    # documents is onboarding's job alone (onboarding/setup_flow.py).
    missing = [
        name
        for name, value in (
            ("product_context", ctx["product_context"]),
            ("schema_map", ctx["schema_map"]),
            ("approved_behaviors", ctx["approved_behaviors"]),
        )
        if value is None
    ]
    if missing:
        raise RuntimeError(
            f"Customer '{customer_id}' is not onboarded (missing: "
            f"{', '.join(missing)}). Complete onboarding for this customer, or "
            "verify CUSTOMER_ID matches the auth uid used during onboarding. The "
            "planning run will not auto-run schema analysis."
        )

    refresh_needed = _needs_refresh(ctx)

    return {
        "product_context": ctx["product_context"] or "",
        "schema_map": ctx["schema_map"] or "",
        "approved_behaviors": ctx["approved_behaviors"] or [],
        "email_settings": ctx["email_settings"] or {},
        "schema_refresh_needed": refresh_needed,
    }


def _needs_refresh(ctx: dict) -> bool:
    meta = ctx.get("schema_meta") or {}

    # (a) explicit settings-page request
    if meta.get("refresh_requested"):
        return True

    # (b) no schema map yet
    if not ctx.get("schema_map"):
        return True

    # (c) staleness
    analyzed_at = meta.get("analyzed_at")
    if not analyzed_at:
        return True
    try:
        analyzed_dt = datetime.fromisoformat(analyzed_at)
        if analyzed_dt.tzinfo is None:
            analyzed_dt = analyzed_dt.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - analyzed_dt).days
        if age_days >= config.SCHEMA_MAX_AGE_DAYS:
            return True
    except ValueError:
        return True

    # (d) credentials/fingerprint change. We fingerprint the current Supabase URL
    # so a credential swap forces a re-analysis.
    current_fp = _db_fingerprint()
    if meta.get("db_fingerprint") and meta["db_fingerprint"] != current_fp:
        return True

    return False


def _db_fingerprint() -> str:
    """Stable identifier for the current DB connection (used to detect a swap)."""
    import hashlib

    raw = f"{config.SUPABASE_URL}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]
