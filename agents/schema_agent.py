"""
Schema Intelligence Agent node (TDD §8.2, §11).

Runs during setup and conditionally during daily runs. It reads the product
context, uses introspection tools to discover the database, and produces:
  * schema_map.md  -> written to LangGraph Store (corrections preserved, Rule 10)
  * raw signal list -> returned for the customer's yes/no review

The agent reasons with Claude via the native tool-use loop. The submit tool is
the explicit "I'm done" stop signal carrying the two structured outputs.
"""
from __future__ import annotations

from typing import Any

import config
from graph.state import GraphState
from services import llm_service, store_service
from tools.db_tools import SCHEMA_TOOLS, SCHEMA_TOOL_HANDLERS
from agents.prompts import SCHEMA_AGENT_SYSTEM, schema_user_message

# The stop tool: Claude calls this once with the finished analysis.
_SUBMIT_TOOL = {
    "name": "submit_schema_map",
    "description": "Submit the finished schema map and the comprehensive raw signal list.",
    "input_schema": {
        "type": "object",
        "properties": {
            "schema_map_markdown": {"type": "string"},
            "raw_signals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "description": {"type": "string"},
                        "derivation": {"type": "string"},
                    },
                    "required": ["name", "description", "derivation"],
                },
            },
        },
        "required": ["schema_map_markdown", "raw_signals"],
    },
}


async def schema_agent_node(state: GraphState, *, store) -> dict:
    customer_id = state["customer_id"]
    product_context = state.get("product_context", "")

    tools = SCHEMA_TOOLS + [_SUBMIT_TOOL]
    handlers: dict[str, Any] = dict(SCHEMA_TOOL_HANDLERS)
    handlers["submit_schema_map"] = lambda i: {"received": True}

    # Tool-use loop runs sync inside the async node; offload to a thread so we
    # don't block the event loop the store/checkpointer share.
    import asyncio

    result = await asyncio.to_thread(
        llm_service.run_tool_loop,
        system=SCHEMA_AGENT_SYSTEM,
        user_message=schema_user_message(product_context),
        tools=tools,
        tool_handlers=handlers,
        stop_tool="submit_schema_map",
        max_turns=40,
    )

    payload = result.get("stop_tool_input") or {}
    schema_map_md = payload.get("schema_map_markdown", "")
    raw_signals = payload.get("raw_signals", [])

    if schema_map_md:
        # Persist (archives previous, preserves customer corrections — Rule 10).
        await store_service.save_schema_map(
            store,
            customer_id,
            schema_map_md,
            db_fingerprint=_fingerprint(),
        )

    # On a daily-run refresh we keep the already-approved behaviors. The raw
    # signal list is surfaced to the setup/review flow (returned in state for the
    # caller / settings page to present). It does not auto-approve anything.
    return {
        "schema_map": schema_map_md or state.get("schema_map", ""),
        "raw_signals": raw_signals,
    }


def _fingerprint() -> str:
    import hashlib

    return hashlib.sha256(config.SUPABASE_URL.encode()).hexdigest()[:16]
