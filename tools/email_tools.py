"""
Copywriter Agent tools (TDD §9.3).

The Copywriter gets exactly three tools:

  * get_user_email_history (read)  — what was sent to this user before, from the
    agent log and any legacy log, so it never repeats an angle.
  * record_send_decision   (write) — a complete email draft for a user.
  * record_skip_decision   (write) — a specific reason for not emailing a user.

The two write tools collect into per-run lists owned by the agent node (so they
land in graph state). We therefore expose a factory that binds fresh collector
lists each run, plus the static Anthropic tool schemas.

Rule 6: the agent must call exactly one write tool for every eligible user — no
silent omissions. The agent enforces this after the loop, not the tools.
"""
from __future__ import annotations

from typing import Any, Callable

from services import supabase_service as db

COPYWRITER_TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_user_email_history",
        "description": "Get recent emails sent to one user (agent + legacy logs) to avoid repeating angles.",
        "input_schema": {
            "type": "object",
            "properties": {"user_id": {"type": "string"}},
            "required": ["user_id"],
        },
    },
    {
        "name": "record_send_decision",
        "description": (
            "Record a decision to SEND an email to a user, with the complete draft. "
            "Subject < 50 chars; body 2-3 sentences; CTA URL must be a deep link."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "cta_text": {"type": "string"},
                "cta_url": {"type": "string"},
                "campaign_key": {"type": "string", "description": "snake_case label you invent"},
                "primary_signal": {"type": "string", "description": "the signal that drove this"},
            },
            "required": [
                "user_id",
                "subject",
                "body",
                "cta_text",
                "cta_url",
                "campaign_key",
                "primary_signal",
            ],
        },
    },
    {
        "name": "record_skip_decision",
        "description": (
            "Record a decision to SKIP a user. Reason must be specific and reference "
            "the actual signals present — never 'no signal' or 'not relevant'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["user_id", "reason"],
        },
    },
]


def build_copywriter_handlers(
    *,
    profiles_by_id: dict[str, dict],
    planned: list[dict],
    skipped: list[dict],
    legacy_log: dict[str, str] | None,
) -> dict[str, Callable[[dict], Any]]:
    """Bind tool handlers to this run's collector lists.

    `planned` and `skipped` are mutated in place; the agent reads them after the
    loop and writes them into graph state.
    """

    def _history(i: dict) -> Any:
        return db.get_user_email_history(i["user_id"], legacy_log=legacy_log)

    def _send(i: dict) -> Any:
        profile = profiles_by_id.get(i["user_id"], {})
        planned.append(
            {
                "user_id": i["user_id"],
                "email": profile.get("email"),
                "name": profile.get("name"),
                "subject": i["subject"],
                "body": i["body"],
                "cta_text": i["cta_text"],
                "cta_url": i["cta_url"],
                "campaign_key": i["campaign_key"],
                "primary_signal": i["primary_signal"],
                "signals_snapshot": profile.get("signals", {}),
            }
        )
        return {"recorded": "send", "user_id": i["user_id"]}

    def _skip(i: dict) -> Any:
        profile = profiles_by_id.get(i["user_id"], {})
        skipped.append(
            {
                "user_id": i["user_id"],
                "email": profile.get("email"),
                "reason": i["reason"],
                "campaign_key": "skipped",
                "signals_snapshot": profile.get("signals", {}),
            }
        )
        return {"recorded": "skip", "user_id": i["user_id"]}

    return {
        "get_user_email_history": _history,
        "record_send_decision": _send,
        "record_skip_decision": _skip,
    }
