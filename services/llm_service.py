"""
Anthropic LLM helper — the native tool-use loop (TDD §2.2, §9).

We call the Anthropic SDK directly inside LangGraph nodes (no LangChain LLM
wrappers). This module owns the one pattern every reasoning agent needs: the
tool-use conversation loop.

The loop:
  1. send messages + tool definitions to Claude
  2. if Claude returns tool_use blocks, run the matching Python functions
  3. feed the tool_result blocks back as a new user turn
  4. repeat until Claude stops requesting tools (or we hit a sentinel "done" tool)

This is exactly what an interviewer means by "agentic" — the model decides which
tools to call and when to stop; the orchestration code just executes and relays.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from anthropic import Anthropic
from langsmith.wrappers import wrap_anthropic

import config

logger = logging.getLogger("llm_service")
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)
    logger.propagate = False

_client: Anthropic | None = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        if not config.ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY is required (planning Lambda only)")
        # wrap_anthropic instruments the SDK so every messages.create call is
        # traced to LangSmith (token counts, inputs/outputs, stop_reason) even
        # outside a LangGraph run — e.g. the direct-call onboarding path.
        _client = wrap_anthropic(Anthropic(api_key=config.ANTHROPIC_API_KEY))
    return _client


def run_tool_loop(
    *,
    system: str,
    user_message: str,
    tools: list[dict[str, Any]],
    tool_handlers: dict[str, Callable[[dict], Any]],
    stop_tool: str | None = None,
    max_turns: int = 25,
) -> dict[str, Any]:
    """Drive a multi-turn tool-use conversation to completion.

    Parameters
    ----------
    system            : system prompt (the agent's personality + rules)
    user_message      : the initial user turn
    tools             : Anthropic tool JSON schemas
    tool_handlers     : name -> python callable(tool_input) -> result
    stop_tool         : if Claude calls this tool, we run it then end the loop
                        (used as the agent's explicit "I'm done" signal)
    max_turns         : safety cap on loop iterations

    Returns {"final_text": str, "stop_tool_input": dict|None} so callers can read
    Claude's closing prose and/or the structured payload from the stop tool.
    """
    client = get_client()
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
    stop_tool_input: dict | None = None
    final_text = ""

    for turn in range(max_turns):
        resp = client.messages.create(
            model=config.MODEL,
            max_tokens=config.MAX_TOKENS,
            system=system,
            tools=tools,
            messages=messages,
        )

        # Collect any text Claude emitted this turn.
        text_parts = [b.text for b in resp.content if b.type == "text"]
        if text_parts:
            final_text = "\n".join(text_parts)
            logger.info("[turn %d] stop_reason=%s text: %s", turn, resp.stop_reason, final_text[:500])

        tool_uses = [b for b in resp.content if b.type == "tool_use"]
        if not tool_uses:
            # Claude answered with prose and no tool call -> conversation done.
            logger.info("[turn %d] no tool call — loop ends without stop tool (stop_reason=%s)",
                        turn, resp.stop_reason)
            break

        for tu in tool_uses:
            logger.info("[turn %d] tool_call: %s args=%s", turn, tu.name, _stringify(tu.input)[:300])

        # Echo the assistant turn back so the thread stays coherent.
        messages.append({"role": "assistant", "content": resp.content})

        tool_results = []
        reached_stop = False
        for tu in tool_uses:
            handler = tool_handlers.get(tu.name)
            result = handler(tu.input) if handler else {"error": f"unknown tool {tu.name}"}
            logger.info("[turn %d] tool_result %s -> %s", turn, tu.name, _stringify(result)[:500])
            if tu.name == stop_tool:
                stop_tool_input = tu.input
                reached_stop = True
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tu.id,
                    "content": _stringify(result),
                }
            )

        messages.append({"role": "user", "content": tool_results})

        if reached_stop:
            break

    return {"final_text": final_text, "stop_tool_input": stop_tool_input}


def _stringify(result: Any) -> str:
    import json

    if isinstance(result, str):
        return result
    try:
        return json.dumps(result, default=str)
    except Exception:  # noqa: BLE001
        return str(result)
