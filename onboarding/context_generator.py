"""
product_context.md generation (TDD §17).

Called ONCE when a customer submits the onboarding form (and again only on an
explicit settings update). Claude turns raw form inputs into a coherent,
intelligent product brief — synthesizing, filling reasonable gaps, connecting
related facts — not a mechanical template render.

The output has two clearly labeled sections:
  * a static section (Claude-authored, the synthesized brief), and
  * a customer-notes section (freeform, never overwritten by any agent).
"""
from __future__ import annotations

import json

from services import llm_service

CUSTOMER_NOTES_HEADER = "## Customer Notes"

_GENERATE_SYSTEM = """\
You are a product strategist writing an internal product brief that an AI email
marketing system will rely on to understand a SaaS product. Turn the raw
onboarding form inputs into a coherent, intelligent markdown brief — synthesize
the information, fill gaps with reasonable inferences, and connect related facts.
It should read as if a thoughtful product manager wrote it, not a form dump.

Cover: what the product does, who the users are, the conversion model (free vs
premium, what conversion looks like), brand tone rules, email hard rules (what
the agent must never do), exclusion rules, and any extra context.

Output ONLY markdown. Start with '## Product Brief'. Do NOT include a customer
notes section — the system appends that separately.
"""

_UPDATE_SYSTEM = """\
You are editing an existing product brief. Apply ONLY the requested change to
the relevant part of the document. Leave every other section intact and
unchanged. Output the full updated markdown for the static section only — do not
touch or output the customer notes section.
"""


def generate_product_context(form_inputs: dict) -> str:
    """Create the initial product_context.md from form inputs."""
    result = llm_service.run_tool_loop(
        system=_GENERATE_SYSTEM,
        user_message="# Onboarding Form Inputs\n" + json.dumps(form_inputs, indent=2, default=str),
        tools=[],
        tool_handlers={},
    )
    static = (result.get("final_text") or "").strip()
    return _with_notes_section(static)


def update_product_context(current_doc: str, changed_field: str, new_value) -> str:
    """Targeted update of one field; customer notes preserved verbatim (§17.3)."""
    static, notes = _split(current_doc)
    result = llm_service.run_tool_loop(
        system=_UPDATE_SYSTEM,
        user_message=(
            "# Current Brief (static section)\n"
            f"{static}\n\n"
            f"# Change\nField: {changed_field}\nNew value: {json.dumps(new_value, default=str)}"
        ),
        tools=[],
        tool_handlers={},
    )
    new_static = (result.get("final_text") or static).strip()
    return f"{new_static}\n\n{notes}".strip() + "\n"


# ── helpers ───────────────────────────────────────────────────────────
def _with_notes_section(static: str) -> str:
    placeholder = (
        f"{CUSTOMER_NOTES_HEADER}\n\n"
        "<!-- Freeform, customer-owned. Corrections and edge cases go here. "
        "Agents read this but never overwrite it. -->"
    )
    return f"{static}\n\n{placeholder}\n"


def _split(doc: str) -> tuple[str, str]:
    if CUSTOMER_NOTES_HEADER in doc:
        idx = doc.index(CUSTOMER_NOTES_HEADER)
        return doc[:idx].rstrip(), doc[idx:].strip()
    return doc.strip(), _with_notes_section("").strip()
