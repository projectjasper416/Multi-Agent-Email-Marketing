"""
Setup lifecycle orchestration (PRD §6).

Runs once per customer. Ties together the two human checkpoints of setup:

  generate_brief()        -> Step 1: form -> product_context.md (saved to Store)
  analyze_schema()        -> Step 2: Schema Intelligence Agent -> schema_map.md
                                      (saved) + raw_signals (returned for review)
  save_approved_signals() -> Step 3: customer's yes/no answers -> approved_behaviors

These are deliberately separate calls because Steps 1->2 are automatic but Step 3
is a human gate (the behavioral signal review). A thin web/API layer would call
these in order, pausing for the operator between analyze_schema() and
save_approved_signals().
"""
from __future__ import annotations

from graph.checkpointer import open_checkpointer_and_store
from services import store_service
from onboarding.context_generator import generate_product_context
from agents.schema_agent import schema_agent_node


async def get_status(customer_id: str) -> dict:
    """Report which setup artifacts already exist for this customer.

    The Store is the source of truth for progress: each step persists one
    document, so the furthest completed step is derivable from which documents
    are present. The web layer uses this to resume the wizard after a reload (or
    a login on any device) instead of restarting at Step 1. Returns presence
    flags plus the already-generated brief so the UI can rehydrate without
    re-running anything.
    """
    async with open_checkpointer_and_store() as (_, store):
        ctx = await store_service.load_customer_context(store, customer_id)
    return {
        "has_brief": bool(ctx.get("product_context")),
        "has_schema": bool(ctx.get("schema_map")),
        "has_behaviors": ctx.get("approved_behaviors") is not None,
        "product_context": ctx.get("product_context"),
    }


async def generate_brief(customer_id: str, form_inputs: dict, email_settings: dict) -> str:
    """Step 1: synthesize and persist product_context.md + email settings."""
    brief = generate_product_context(form_inputs)
    async with open_checkpointer_and_store() as (_, store):
        await store_service.save_product_context(store, customer_id, brief)
        await store_service.save_email_settings(store, customer_id, email_settings)
    return brief


async def analyze_schema(customer_id: str) -> list[dict]:
    """Step 2: run the Schema Intelligence Agent. Persists schema_map.md and
    returns the raw signal list for the customer's yes/no review."""
    async with open_checkpointer_and_store() as (_, store):
        ctx = await store_service.load_customer_context(store, customer_id)
        # Precondition: schema analysis only makes sense once a brief exists.
        # Guard BEFORE the schema agent runs so an unknown/un-onboarded
        # customer id can never trigger a (billed) LLM call on empty context.
        if not ctx.get("product_context"):
            raise ValueError(
                f"No product brief found for customer '{customer_id}'. "
                "Complete Step 1 (generate the brief) before analyzing the schema."
            )
        state = {
            "customer_id": customer_id,
            "product_context": ctx["product_context"],
        }
        result = await schema_agent_node(state, store=store)
    return result.get("raw_signals", [])


async def save_approved_signals(customer_id: str, approvals: list[dict]) -> list[dict]:
    """Step 3: keep only the signals the customer marked yes.

    `approvals` is the raw signal list with an added boolean `approved` per item.
    Setup is complete once this is saved.
    """
    approved = [s for s in approvals if s.get("approved")]
    async with open_checkpointer_and_store() as (_, store):
        await store_service.save_approved_behaviors(store, customer_id, approved)
    return approved
