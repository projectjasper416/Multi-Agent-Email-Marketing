"""
Delivery Agent node (TDD §8.5, §15).

The simplest agent: send approved emails, log everything, exit. It runs ONLY
after operator approval, only in the delivery Lambda.

Hard guarantees:
  * Does NOT call Claude (Rule 4) — re-invoking the model would produce content
    the operator never approved. All copy was finalized in the planning phase.
  * Imports the Resend email service (which the planning Lambda must never
    import — Rule 3).
  * A single Resend failure is logged and the run continues (§15).
  * Every processed user — sends, failures, and skips — lands in
    ai_marketing_email_log so the audit trail is complete.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import config
from graph.state import GraphState
from services import supabase_service as db

# NOTE: services.email_service (Resend) is imported LAZILY inside _deliver, not
# at module top. graph.py imports every agent node — including this one — so a
# top-level import would pull Resend into the PLANNING Lambda's import graph and
# violate Rule 3 ("planning Lambda must never import the email service"). The
# import only resolves when delivery actually runs, which is delivery-Lambda-only.


async def delivery_agent_node(state: GraphState) -> dict:
    # Offload the blocking Resend + Supabase I/O to a worker thread.
    return await asyncio.to_thread(_deliver, state)


def _deliver(state: GraphState) -> dict:
    # Lazy import (Rule 3): only resolves in the delivery Lambda, never planning.
    from services.email_service import send_email

    run_id = state["run_id"]
    customer_id = state["customer_id"]
    planned = state.get("planned_emails", [])
    skipped = state.get("skipped_users", [])
    settings = state.get("email_settings", {}) or {}

    from_address = settings.get("from_address", "")
    brand_name = settings.get("brand_name", "")
    unsubscribe_base_url = settings.get("unsubscribe_base_url", "")

    send_results = []

    for email in planned:
        result = send_email(
            to_email=email["email"],
            subject=email["subject"],
            body_text=email["body"],
            cta_text=email["cta_text"],
            cta_url=email["cta_url"],
            user_id=email["user_id"],
            from_address=from_address,
            brand_name=brand_name,
            unsubscribe_base_url=unsubscribe_base_url,
        )
        send_results.append(
            {
                "user_id": email["user_id"],
                "success": result["success"],
                "provider_message_id": result["message_id"],
                "error_message": result["error"],
            }
        )
        _log_send(customer_id, run_id, email, result)

    # Skips are written here too, so the whole run is accounted for in one place.
    for skip in skipped:
        _log_skip(customer_id, run_id, skip)

    sent = sum(1 for r in send_results if r["success"])
    failed = len(send_results) - sent
    return {
        "send_results": send_results,
        "run_summary": {**state.get("run_summary", {}), "sent": sent, "failed": failed},
    }


def _log_send(customer_id: str, run_id: str, email: dict, result: dict) -> None:
    db.write_email_log(
        {
            "customer_id": customer_id,
            "run_id": run_id,
            "user_id": email["user_id"],
            "sent_to_email": email["email"],
            "campaign_key": email["campaign_key"],
            "subject": email["subject"],
            "decision": "sent",
            "status": "sent" if result["success"] else "failed",
            "decision_reason": f"Primary signal: {email.get('primary_signal')}",
            "signals_snapshot": email.get("signals_snapshot", {}),
            "model": config.MODEL,
            "provider_message_id": result["message_id"],
            "error_message": result["error"],
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
    )


def _log_skip(customer_id: str, run_id: str, skip: dict) -> None:
    db.write_email_log(
        {
            "customer_id": customer_id,
            "run_id": run_id,
            "user_id": skip["user_id"],
            "sent_to_email": skip.get("email"),
            "campaign_key": skip.get("campaign_key", "skipped"),
            "subject": None,
            "decision": "skipped",
            "status": "sent",  # status column applies to sends; default per migration
            "decision_reason": skip.get("reason"),
            "signals_snapshot": skip.get("signals_snapshot", {}),
            "model": config.MODEL,
            "provider_message_id": None,
            "error_message": None,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
    )
