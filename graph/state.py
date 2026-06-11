"""
Shared graph state — the single source of truth that flows between agents.

This is the heart of the LangGraph design. Every agent node reads from and
writes to this one typed dictionary. If a piece of data needs to cross an agent
boundary, it MUST be declared here (TDD §6). Nothing is passed out-of-band.

LangGraph merges each node's returned partial dict into this state and persists
the whole thing to PostgresSaver at every node transition — which is exactly
what lets us pause at the human approval gate in one Lambda and resume in
another, hours later.
"""
from __future__ import annotations

from typing import Any, Literal, TypedDict


class UserProfile(TypedDict, total=False):
    """One eligible user with concrete (not boolean) signal values."""
    user_id: str
    email: str
    name: str | None
    signals: dict[str, Any]          # signal_name -> concrete value (int/date/bool/list)
    signal_notes: dict[str, str]     # signal_name -> how it was derived
    email_history: list[dict[str, Any]]  # recent campaigns from agent + legacy logs


class PlannedEmail(TypedDict, total=False):
    """A complete, ready-to-send draft produced by the Copywriter."""
    user_id: str
    email: str
    name: str | None
    subject: str
    body: str
    cta_text: str
    cta_url: str
    campaign_key: str                # snake_case label Claude invents
    primary_signal: str              # the signal that drove the decision
    signals_snapshot: dict[str, Any] # full profile at decision time (for audit log)


class SkippedUser(TypedDict, total=False):
    """A user Claude consciously chose not to email, with a specific reason."""
    user_id: str
    email: str
    reason: str
    campaign_key: str                # often "skipped"; kept for log uniformity
    signals_snapshot: dict[str, Any]


class RunSummary(TypedDict, total=False):
    total_eligible: int
    total_planned: int
    total_skipped: int
    campaign_breakdown: dict[str, int]
    notable_patterns: str


class SendResult(TypedDict, total=False):
    user_id: str
    success: bool
    provider_message_id: str | None
    error_message: str | None


class GraphState(TypedDict, total=False):
    """The complete shared state. `total=False` so nodes can fill it in stages."""

    # ── Identity / run metadata (set by the Lambda before the graph starts)
    customer_id: str
    run_id: str

    # ── Permanent context, loaded from LangGraph Store by the orchestrator
    product_context: str             # product_context.md
    schema_map: str                  # schema_map.md
    approved_behaviors: list[dict[str, Any]]  # parsed approved_behaviors entries
    email_settings: dict[str, Any]   # from_address, brand_name, unsubscribe_base_url

    # ── Routing flag set by the orchestrator (deterministic, no LLM)
    schema_refresh_needed: bool

    # ── Raw signal list from the Schema Intelligence Agent (setup/review only).
    # Surfaced to the customer's yes/no review; not used in the daily flow.
    raw_signals: list[dict[str, Any]]

    # ── Ephemeral per-run audience picture (never persisted to Store/DB)
    audience_analysis: str           # audience_analysis.md
    eligible_users: list[UserProfile]

    # ── Copywriter outputs
    planned_emails: list[PlannedEmail]
    skipped_users: list[SkippedUser]
    run_summary: RunSummary

    # ── HITL + delivery
    approval_status: Literal["approved", "cancelled", "expired"]
    send_results: list[SendResult]
