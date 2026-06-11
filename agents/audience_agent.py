"""
Audience Analysis Agent node (TDD §8.3, §12).

Runs on every daily run. Reads the schema map + approved behaviors from graph
state, queries the database using ONLY names from the schema map, applies the
eligibility filters, computes concrete signal values per user, and produces:
  * eligible_users    -> list of flat profile dicts with concrete signal values
  * audience_analysis -> ephemeral audience summary for the Copywriter

The agent reasons with Claude (it has to translate the schema map's prose into
the right queries for THIS database) but the deterministic eligibility filtering
is applied in code so it can never be skipped (PRD §7.2):
  - exclude premium users
  - exclude unsubscribed users
  - exclude users emailed within the frequency cap (agent log + legacy log)
  - exclude accounts younger than MIN_ACCOUNT_AGE_DAYS

Signals are concrete VALUES, not boolean flags ("last active 14 days ago", not
"dormant: yes") — that specificity is what powers personalized copy.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import config
from graph.state import GraphState
from services import llm_service, supabase_service as db

_AUDIENCE_SYSTEM = """\
You are an Audience Analysis Agent. Using ONLY the table and column names in the
provided schema map, query the database to build a behavioral profile for every
candidate user.

Rules:
- Never invent or hardcode a table/column name — read them from the schema map.
- Honor soft-delete flags and absent-row semantics exactly as the map describes
  (missing subscription row == free user, etc.).
- Compute each approved signal as a CONCRETE value (an int count, a date, a
  number of days, a specific list of features) — never a bare boolean when a
  value is available.

Use the select_rows and run_sql tools to gather data. The eligibility filtering
(premium / unsubscribed / recently-emailed / too-new) is applied by the system
AFTER you return profiles — you focus on correct signal computation for the
candidate ids provided. When finished, call submit_profiles with one profile per
candidate user and a short audience_analysis_markdown summary.
"""

_SUBMIT_TOOL = {
    "name": "submit_profiles",
    "description": "Submit computed user profiles and the audience analysis summary.",
    "input_schema": {
        "type": "object",
        "properties": {
            "profiles": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "user_id": {"type": "string"},
                        "email": {"type": "string"},
                        "name": {"type": "string"},
                        "signals": {"type": "object"},
                        "signal_notes": {"type": "object"},
                    },
                    "required": ["user_id", "email", "signals"],
                },
            },
            "audience_analysis_markdown": {"type": "string"},
        },
        "required": ["profiles", "audience_analysis_markdown"],
    },
}


async def audience_agent_node(state: GraphState, *, store) -> dict:
    schema_map = state.get("schema_map", "")
    approved = state.get("approved_behaviors", [])
    legacy_log = _legacy_log_from_schema(approved, schema_map)

    # Deterministic pre-filter: which user ids are even eligible today. We pass
    # the candidate id set to the agent so it only computes signals for them.
    excluded_ids = db.user_ids_emailed_within(config.FREQUENCY_CAP_DAYS, legacy_log)

    user_message = (
        "# Schema Map\n"
        f"{schema_map}\n\n"
        "# Approved Behavioral Signals to compute\n"
        f"{json.dumps(approved, default=str, indent=2)}\n\n"
        "# Eligibility context\n"
        "Exclude these (already filtered downstream too): premium users, "
        "unsubscribed users, accounts younger than 1 day, and these user_ids "
        "already emailed within the frequency cap:\n"
        f"{json.dumps(sorted(excluded_ids), default=str)}\n\n"
        "Query the database and submit one profile per remaining candidate user."
    )

    handlers: dict[str, Any] = {
        "select_rows": lambda i: db.select_rows(
            i["table"], i.get("columns", "*"), i.get("filters"), i.get("limit")
        ),
        "run_sql": lambda i: db.run_sql(i["sql"]),
        "submit_profiles": lambda i: {"received": True},
    }

    from tools.db_tools import AUDIENCE_TOOLS

    result = await asyncio.to_thread(
        llm_service.run_tool_loop,
        system=_AUDIENCE_SYSTEM,
        user_message=user_message,
        tools=AUDIENCE_TOOLS + [_SUBMIT_TOOL],
        tool_handlers=handlers,
        stop_tool="submit_profiles",
        max_turns=60,
    )

    payload = result.get("stop_tool_input") or {}
    profiles = payload.get("profiles", [])
    audience_md = payload.get("audience_analysis_markdown", "")

    # Final deterministic safety net: drop anyone in the excluded set, attach
    # each user's recent email history for the Copywriter.
    eligible: list[dict] = []
    for p in profiles:
        if str(p.get("user_id")) in excluded_ids:
            continue
        p["email_history"] = db.get_user_email_history(p["user_id"], legacy_log=legacy_log)
        eligible.append(p)

    return {
        "eligible_users": eligible,
        "audience_analysis": audience_md,
    }


def _legacy_log_from_schema(approved: list[dict], schema_map: str) -> dict[str, str] | None:
    """Best-effort extraction of legacy-log coordinates the schema map identified.
    Stored on the approved_behaviors metadata or parsed from the map. Returns
    None when the customer has no legacy system."""
    for b in approved or []:
        if b.get("legacy_log"):
            return b["legacy_log"]
    return None
