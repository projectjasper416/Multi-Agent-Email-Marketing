"""
Delivery Lambda entry point (TDD §14.1, §14.4, §14.5).

Triggered by the Telegram Approve/Cancel button via API Gateway. Verifies three
security layers, then resumes the paused graph with the operator's decision.

Webhook security (Rule 15 — all three must pass; any failure returns 200 so
Telegram does not retry, and the rejection is logged):
  1. X-Telegram-Bot-Api-Secret-Token header == TELEGRAM_WEBHOOK_SECRET
  2. callback chat.id == TELEGRAM_CHAT_ID
  3. target run is in pending_approval state and not expired

Optimistic concurrency (Rule 14): the run is transitioned pending -> approved/
cancelled with a conditional update. If THIS call didn't win the transition, we
return without resuming — a double-tap can't trigger two send waves.
"""
from __future__ import annotations

import asyncio
import json

from langgraph.types import Command

import config
from graph.checkpointer import open_checkpointer_and_store
from graph.graph import build_graph
from services import telegram_service, runs_service


def handler(event, context):
    """API Gateway -> Lambda entry point for the Telegram callback webhook."""
    try:
        # ── Layer 1: secret token header ──────────────────────────────
        headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
        token = headers.get("x-telegram-bot-api-secret-token")
        if token != config.TELEGRAM_WEBHOOK_SECRET:
            return _silent_reject("bad secret token")

        body = json.loads(event.get("body") or "{}")
        callback = body.get("callback_query")
        if not callback:
            return _silent_reject("no callback_query")

        # ── Layer 2: chat id match ────────────────────────────────────
        chat_id = str(((callback.get("message") or {}).get("chat") or {}).get("id"))
        if chat_id != str(config.TELEGRAM_CHAT_ID):
            return _silent_reject(f"chat id mismatch: {chat_id}")

        action, _, run_id = (callback.get("data") or "").partition(":")
        if action not in ("approve", "cancel") or not run_id:
            return _silent_reject(f"bad callback data: {callback.get('data')}")

        # ── Layer 3: run is pending and not expired ───────────────────
        run = runs_service.get_run(run_id)
        if not run or run["status"] != runs_service.STATUS_PENDING:
            telegram_service.answer_callback(callback["id"], "This run is no longer pending.")
            return _silent_reject(f"run not pending: {run_id}")

        # Acknowledge the tap so the spinner stops.
        telegram_service.answer_callback(callback["id"])

        if action == "cancel":
            return _handle_cancel(run_id, run)
        return _handle_approve(run_id, run)

    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        # Still 200 so Telegram doesn't hammer us with retries.
        return {"statusCode": 200, "body": json.dumps({"error": str(exc)})}


def _handle_cancel(run_id: str, run: dict) -> dict:
    # Rule 14: only the winner of the conditional transition proceeds.
    if not runs_service.transition(run_id, runs_service.STATUS_PENDING, runs_service.STATUS_CANCELLED):
        return {"statusCode": 200, "body": "already handled"}

    asyncio.run(_resume(run_id, "cancelled"))
    if run.get("summary_message_id"):
        telegram_service.edit_message(run["summary_message_id"], f"✖️ Run {run_id} cancelled. Nothing sent.")
    return {"statusCode": 200, "body": "cancelled"}


def _handle_approve(run_id: str, run: dict) -> dict:
    if not runs_service.transition(run_id, runs_service.STATUS_PENDING, runs_service.STATUS_APPROVED):
        return {"statusCode": 200, "body": "already handled"}

    results = asyncio.run(_resume(run_id, "approved"))
    runs_service.transition(run_id, runs_service.STATUS_APPROVED, runs_service.STATUS_SENT)

    summary = (results or {}).get("run_summary", {})
    if run.get("summary_message_id"):
        telegram_service.edit_message(
            run["summary_message_id"],
            f"✅ Run {run_id} approved. Sent {summary.get('sent', 0)}, "
            f"failed {summary.get('failed', 0)}.",
        )
    return {"statusCode": 200, "body": "approved"}


async def _resume(run_id: str, decision: str) -> dict:
    """Resume the paused graph from its checkpoint with the operator's decision.

    Same thread_id as planning -> LangGraph loads the persisted state and
    continues from the interrupt. On 'approved' the delivery agent runs; on
    'cancelled' the graph routes straight to END.
    """
    async with open_checkpointer_and_store() as (saver, store):
        graph = build_graph(saver, store)
        cfg = {"configurable": {"thread_id": run_id, "customer_id": config.CUSTOMER_ID}}
        final = await graph.ainvoke(Command(resume=decision), config=cfg)
        return final or {}


def _silent_reject(reason: str) -> dict:
    """Log to CloudWatch and return 200 so Telegram does not retry (Rule 15)."""
    print(f"[webhook-reject] {reason}")
    return {"statusCode": 200, "body": "ok"}
