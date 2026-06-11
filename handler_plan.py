"""
Planning Lambda entry point (TDD §4 handler_plan, §14).

Triggered daily by EventBridge (or manually). Runs the graph up to the human
approval interrupt, sends the Telegram preview, and exits WITHOUT sending any
email. A bug here can never dispatch mail — this module does not import the
email service (Rule 3).

Flow:
  1. expiry sweep — close any stale pending runs first (Rule 13)
  2. open checkpointer + store, build graph
  3. ainvoke the graph with a fresh run_id/thread; it pauses at the interrupt
     once state is persisted (Rule 11: persisted BEFORE we notify)
  4. read planned_emails from the interrupted state, send Telegram preview
  5. record the pending run (for expiry + concurrency) and exit
"""
from __future__ import annotations

import asyncio
import uuid

import config
from graph.checkpointer import open_checkpointer_and_store
from graph.graph import build_graph
from services import telegram_service, runs_service


async def _plan() -> dict:
    customer_id = config.CUSTOMER_ID
    run_id = str(uuid.uuid4())

    # (1) Expiry sweep BEFORE any new work, so the operator's feed is accurate.
    expired = runs_service.sweep_expired()
    for run in expired:
        if run.get("summary_message_id"):
            telegram_service.edit_message(
                run["summary_message_id"],
                f"⏰ Run {run['run_id']} expired without approval. Nothing was sent.",
            )
        else:
            telegram_service.send_message(
                f"⏰ Previous run {run['run_id']} expired without approval. Nothing was sent."
            )

    async with open_checkpointer_and_store() as (saver, store):
        graph = build_graph(saver, store)

        # thread_id ties this run's checkpoints together for pause/resume.
        cfg = {"configurable": {"thread_id": run_id, "customer_id": customer_id}}
        initial = {"customer_id": customer_id, "run_id": run_id}

        # Runs until the interrupt() in the HITL node. State is now persisted.
        await graph.ainvoke(initial, config=cfg)

        # Read the paused state to build the Telegram preview.
        snapshot = await graph.aget_state(cfg)
        state = snapshot.values
        planned = state.get("planned_emails", [])
        summary = state.get("run_summary", {})

    # (4) Telegram preview: summary -> per-email drafts -> approve/cancel buttons.
    if not planned:
        telegram_service.send_message(
            f"Run {run_id}: 0 emails planned today "
            f"({summary.get('total_skipped', 0)} users skipped). Nothing to approve."
        )
        runs_service.create_pending_run(run_id, None)
        # Auto-resolve an empty run as cancelled so it doesn't linger.
        runs_service.transition(run_id, runs_service.STATUS_PENDING, runs_service.STATUS_CANCELLED)
        return {"run_id": run_id, "planned": 0}

    summary_text = telegram_service.format_summary(summary, run_id)
    previews = [
        telegram_service.format_email_preview(e, i + 1, len(planned))
        for i, e in enumerate(planned)
    ]
    summary_message_id = telegram_service.send_approval_request(
        run_id=run_id,
        summary_text=summary_text,
        email_previews=previews,
    )

    # (5) Record the pending run for the expiry sweep + concurrency guard.
    runs_service.create_pending_run(run_id, summary_message_id)
    return {"run_id": run_id, "planned": len(planned)}


def handler(event, context):
    """AWS Lambda entry point."""
    try:
        result = asyncio.run(_plan())
        return {"statusCode": 200, "body": result}
    except Exception as exc:  # noqa: BLE001 — return a structured error, never crash silently
        import traceback

        traceback.print_exc()
        return {"statusCode": 500, "body": {"error": str(exc)}}


if __name__ == "__main__":
    # Local dev: run the planning phase directly.
    print(asyncio.run(_plan()))
