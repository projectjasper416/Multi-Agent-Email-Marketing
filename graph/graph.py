"""
The LangGraph StateGraph — the authoritative map of how the system flows.

Nothing about agent sequencing or routing exists anywhere else (TDD §4). Read
this file top to bottom and you understand the entire control flow:

    START
      │
      ▼
   orchestrator ──(schema_refresh_needed?)──► schema_agent ─┐
      │  else                                               │
      ▼ ◄──────────────────────────────────────────────────┘
   audience_agent
      │
      ▼
   copywriter_agent
      │
      ▼
   hitl_gate  ──interrupt()──►  (operator approves via Telegram)
      │                              │
      │ cancelled / expired          │ approved
      ▼                              ▼
     END  ◄────────────────────  delivery_agent ──► END

The graph is compiled with:
  * a checkpointer (PostgresSaver) so the interrupt can pause in one Lambda and
    resume in another, and
  * a store (AsyncPostgresStore) so nodes can read/write the permanent
    per-customer memory documents.
"""
from __future__ import annotations

from langgraph.graph import START, END, StateGraph
from langgraph.types import interrupt

from graph.state import GraphState
from agents.orchestrator import orchestrator_node
from agents.schema_agent import schema_agent_node
from agents.audience_agent import audience_agent_node
from agents.copywriter_agent import copywriter_agent_node
from agents.delivery_agent import delivery_agent_node


# ── Node names (single place to avoid typos in edges) ─────────────────
ORCHESTRATOR = "orchestrator"
SCHEMA_AGENT = "schema_agent"
AUDIENCE_AGENT = "audience_agent"
COPYWRITER_AGENT = "copywriter_agent"
HITL_GATE = "hitl_gate"
DELIVERY_AGENT = "delivery_agent"


async def hitl_gate_node(state: GraphState) -> dict:
    """Human-in-the-loop approval gate (TDD §14).

    `interrupt()` serializes the full graph state to the checkpointer and hands
    control back to the calling Lambda. The graph stays dormant until it is
    resumed with `Command(resume=<value>)` from the delivery Lambda after the
    operator taps a Telegram button.

    The value passed back into `interrupt()` on resume becomes the return value
    here — we expect "approved", "cancelled", or "expired".
    """
    decision = interrupt(
        {
            "kind": "email_approval",
            "run_id": state.get("run_id"),
            "summary": state.get("run_summary"),
        }
    )
    decision = decision if decision in ("approved", "cancelled", "expired") else "cancelled"
    return {"approval_status": decision}


# ── Conditional routing functions (deterministic, no LLM) ─────────────
def route_after_orchestrator(state: GraphState) -> str:
    """Schema refresh needed -> schema agent; otherwise straight to audience."""
    return SCHEMA_AGENT if state.get("schema_refresh_needed") else AUDIENCE_AGENT


def route_after_gate(state: GraphState) -> str:
    """Only an explicit approval routes to delivery. Everything else ends."""
    return DELIVERY_AGENT if state.get("approval_status") == "approved" else END


def build_graph(checkpointer, store):
    """Assemble and compile the StateGraph.

    Parameters are the live checkpointer and store from
    `graph.checkpointer.open_checkpointer_and_store()`.
    """
    g = StateGraph(GraphState)

    # Register nodes. Each agent node is an async (state) -> partial-state fn.
    # The store is injected by LangGraph into nodes that declare `*, store`.
    g.add_node(ORCHESTRATOR, orchestrator_node)
    g.add_node(SCHEMA_AGENT, schema_agent_node)
    g.add_node(AUDIENCE_AGENT, audience_agent_node)
    g.add_node(COPYWRITER_AGENT, copywriter_agent_node)
    g.add_node(HITL_GATE, hitl_gate_node)
    g.add_node(DELIVERY_AGENT, delivery_agent_node)

    # Edges.
    g.add_edge(START, ORCHESTRATOR)
    g.add_conditional_edges(
        ORCHESTRATOR,
        route_after_orchestrator,
        {SCHEMA_AGENT: SCHEMA_AGENT, AUDIENCE_AGENT: AUDIENCE_AGENT},
    )
    g.add_edge(SCHEMA_AGENT, AUDIENCE_AGENT)
    g.add_edge(AUDIENCE_AGENT, COPYWRITER_AGENT)
    g.add_edge(COPYWRITER_AGENT, HITL_GATE)
    g.add_conditional_edges(
        HITL_GATE,
        route_after_gate,
        {DELIVERY_AGENT: DELIVERY_AGENT, END: END},
    )
    g.add_edge(DELIVERY_AGENT, END)

    # Compile with durability + memory.
    return g.compile(checkpointer=checkpointer, store=store)
