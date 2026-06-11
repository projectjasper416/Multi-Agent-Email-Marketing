"""
Copywriter Agent node (TDD §8.4, §13).

The reasoning core of the system. For each eligible user it reads the concrete
signal values, weighs them as DIMENSIONS (not triggers — Rule 16), checks email
history to avoid repeats, and decides: send a fully-written email, or skip with
a specific reason.

It must account for every eligible user — exactly one send/skip per user
(Rule 6). After the tool-use loop we reconcile: any user the model didn't decide
on is recorded as a safe, explicit skip so the audit log is never incomplete.

Produces planned_emails, skipped_users, and a run_summary for the Telegram gate.
"""
from __future__ import annotations

import asyncio
from collections import Counter

from graph.state import GraphState
from services import llm_service
from tools.email_tools import COPYWRITER_TOOLS, build_copywriter_handlers
from agents.prompts import COPYWRITER_AGENT_SYSTEM, copywriter_user_message


async def copywriter_agent_node(state: GraphState) -> dict:
    product_context = state.get("product_context", "")
    approved = state.get("approved_behaviors", [])
    audience_md = state.get("audience_analysis", "")
    profiles = state.get("eligible_users", [])

    profiles_by_id = {str(p["user_id"]): p for p in profiles}
    planned: list[dict] = []
    skipped: list[dict] = []
    legacy_log = next((b.get("legacy_log") for b in approved or [] if b.get("legacy_log")), None)

    handlers = build_copywriter_handlers(
        profiles_by_id=profiles_by_id,
        planned=planned,
        skipped=skipped,
        legacy_log=legacy_log,
    )

    if profiles:
        await asyncio.to_thread(
            llm_service.run_tool_loop,
            system=_system_prompt(product_context, approved),
            user_message=copywriter_user_message(product_context, approved, audience_md, profiles),
            tools=COPYWRITER_TOOLS,
            tool_handlers=handlers,
            stop_tool=None,  # ends when Claude stops calling tools
            max_turns=4 * max(len(profiles), 1) + 10,
        )

    # Rule 6 reconciliation: ensure every eligible user was decided on.
    decided = {p["user_id"] for p in planned} | {s["user_id"] for s in skipped}
    for uid, profile in profiles_by_id.items():
        if uid not in decided:
            skipped.append(
                {
                    "user_id": uid,
                    "email": profile.get("email"),
                    "reason": "No decision was produced for this user; skipped to keep the audit log complete.",
                    "campaign_key": "skipped",
                    "signals_snapshot": profile.get("signals", {}),
                }
            )

    summary = _summarize(profiles, planned, skipped, audience_md)
    return {
        "planned_emails": planned,
        "skipped_users": skipped,
        "run_summary": summary,
    }


def _system_prompt(product_context: str, approved: list[dict]) -> str:
    behaviors = "\n".join(f"- {b.get('name')}: {b.get('description')}" for b in approved or [])
    return (
        COPYWRITER_AGENT_SYSTEM
        + "\n\n# Product Context\n"
        + product_context
        + "\n\n# Approved Behavioral Signals\n"
        + behaviors
    )


def _summarize(profiles, planned, skipped, audience_md) -> dict:
    breakdown = Counter(e.get("campaign_key", "unknown") for e in planned)
    return {
        "total_eligible": len(profiles),
        "total_planned": len(planned),
        "total_skipped": len(skipped),
        "campaign_breakdown": dict(breakdown),
        "notable_patterns": (audience_md or "").strip()[:400],
    }
