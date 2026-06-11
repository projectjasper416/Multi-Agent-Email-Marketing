# Architecture & Interview Guide

This document explains **how the system is built and how each framework is used**,
written for an Applied AI Engineer interview. Read it top to bottom and you can
whiteboard the whole system and defend every design decision.

It is organized as:

1. The mental model — what makes this "agentic"
2. LangGraph, deeply (state, nodes, edges, routing)
3. Durability: checkpointer vs store
4. Human-in-the-loop with `interrupt()`
5. The native tool-use loop (how each agent actually "thinks")
6. Walkthrough of all five agents
7. The generic, schema-agnostic design
8. Security & operational design
9. The 18 critical rules (and the risk each prevents)
10. Framework trade-offs (LangGraph vs CrewAI vs AutoGen vs raw loops)
11. Likely interview questions with answers

---

## 1. The mental model — what makes this "agentic"

A *workflow* hardcodes the steps. An *agent* is given tools and a goal and
**decides** what to do. This system is a **multi-agent pipeline**: deterministic
where determinism is safe (routing, eligibility, key-splitting) and
agentic where reasoning is the whole point (interpreting an unknown schema,
deciding what to write to whom).

Three design principles drive everything:

1. **Schema is discovered, never assumed.** No table/column name is hardcoded.
2. **Behavioral signals are raw facts, never campaign categories.** "Last active
   14 days ago," not "send re-engagement email."
3. **Email decisions are made by reasoning, never by if/else.** Approved signals
   are *vocabulary the Copywriter reasons with*, not triggers that fire emails.

> Interview soundbite: *"The intelligence lives in the agents, not the code. The
> code is generic plumbing; Claude supplies the product-specific judgment at two
> points — schema interpretation at setup, and copy decisions at runtime."*

---

## 2. LangGraph, deeply

LangGraph models an agent system as a **directed graph over a typed shared
state**. Three primitives matter.

### 2.1 The shared state (`graph/state.py`)

```python
class GraphState(TypedDict, total=False):
    customer_id: str
    product_context: str
    schema_map: str
    approved_behaviors: list[dict]
    eligible_users: list[UserProfile]
    planned_emails: list[PlannedEmail]
    approval_status: Literal["approved","cancelled","expired"]
    ...
```

Every node receives the state and returns a **partial dict**; LangGraph merges
that partial into the state and moves on. This is the single source of truth —
"if data needs to cross an agent boundary, it must be in the state" (TDD §6).

> **Reducers**: by default LangGraph *overwrites* a key with the returned value.
> If you wanted nodes to *append* (e.g. two parallel nodes both adding messages),
> you annotate the field with a reducer like `Annotated[list, add]`. Here the
> pipeline is mostly sequential so plain overwrite semantics are correct and
> simpler — a good thing to be able to explain.

### 2.2 Nodes

A node is just `async def node(state) -> dict`. Each of our five agents is one
node (`agents/*.py`). Two nodes also take `*, store` — LangGraph **injects** the
Store into any node that declares that parameter:

```python
async def orchestrator_node(state: GraphState, *, store) -> dict:
```

### 2.3 Edges and conditional routing (`graph/graph.py`)

```python
g.add_edge(START, ORCHESTRATOR)
g.add_conditional_edges(ORCHESTRATOR, route_after_orchestrator,
                        {SCHEMA_AGENT: SCHEMA_AGENT, AUDIENCE_AGENT: AUDIENCE_AGENT})
g.add_edge(AUDIENCE_AGENT, COPYWRITER_AGENT)
g.add_edge(COPYWRITER_AGENT, HITL_GATE)
g.add_conditional_edges(HITL_GATE, route_after_gate,
                        {DELIVERY_AGENT: DELIVERY_AGENT, END: END})
```

- **Unconditional edge** = "always go here next."
- **Conditional edge** = a Python function reads state and returns the next node
  name. Crucially **our routing functions make no LLM call** — routing is
  deterministic and cheap (TDD §5.2). The *agents* are smart; the *router* is
  dumb and predictable. That separation is a deliberate, defensible choice.

> Interview soundbite: *"Supervisor routing is deterministic; only the leaf
> agents call the LLM. That keeps control flow auditable and cheap, and means a
> model hiccup can never send the graph down the wrong branch."*

---

## 3. Durability: checkpointer vs store (the part people get wrong)

LangGraph has **two** separate persistence concepts and they do different jobs.
Being crisp on this is a strong interview signal.

| | **Checkpointer** (`PostgresSaver`) | **Store** (`AsyncPostgresStore`) |
|---|---|---|
| Holds | the **run's** state, snapshotted at every node transition | **long-lived memory** across runs |
| Keyed by | `thread_id` (one per run) | namespace tuple `("customer", id, doc)` |
| Lifespan | one run; cleaned up after | permanent until changed |
| Here | enables pause/resume across two Lambdas | holds `product_context`, `schema_map`, `approved_behaviors`, `email_settings` |

```python
# graph/checkpointer.py — both bound to the SAME Supabase Postgres
async with AsyncPostgresSaver.from_conn_string(url) as saver, \
           AsyncPostgresStore.from_conn_string(url) as store:
    graph = build_graph(saver, store)   # compile with both
```

**Why this matters:** the planning Lambda runs the graph until the approval
interrupt, the checkpointer **serializes the entire state to Postgres**, and the
Lambda exits. Hours later the delivery Lambda — a *different process* — loads
that checkpoint by `thread_id` and resumes from exactly the paused node. Without
durable checkpointing you simply cannot do human-in-the-loop across a serverless
boundary.

---

## 4. Human-in-the-loop with `interrupt()`

The approval gate is a node that calls LangGraph's `interrupt()`:

```python
async def hitl_gate_node(state):
    decision = interrupt({"kind": "email_approval", "run_id": state["run_id"], ...})
    return {"approval_status": decision}
```

`interrupt()` does three things: persists state to the checkpointer, raises an
internal signal that returns control to the caller, and **remembers where it
paused**. The planning Lambda detects the pause and sends the Telegram preview,
then exits.

To resume, the delivery Lambda calls the graph again with the **same thread_id**
and a `Command`:

```python
await graph.ainvoke(Command(resume="approved"), config={"configurable":{"thread_id":run_id}})
```

The value passed to `Command(resume=...)` becomes the **return value of
`interrupt()`** — so `decision` above is `"approved"`/`"cancelled"`, and the
conditional edge routes to delivery or to END.

> Sequencing rule (Rule 11): state must be persisted **before** the operator is
> notified. `interrupt()` guarantees this — we only send Telegram messages after
> the graph has paused, so an approval can always be resumed.

---

## 5. The native tool-use loop — how an agent actually "thinks"

We call the **Anthropic SDK directly** inside nodes (no LangChain LLM wrappers).
`services/llm_service.run_tool_loop` is the reusable engine:

```
1. send (system + messages + tool schemas) to Claude
2. Claude replies with text and/or tool_use blocks
3. for each tool_use: run the matching Python fn, collect a tool_result
4. append the assistant turn + the tool_results (as a user turn)
5. loop until Claude stops calling tools (or it calls a designated stop tool)
```

This loop *is* the agentic behavior: **Claude decides which tool to call and when
it's done; our code just executes and relays results.** The Schema and Audience
agents use a **stop tool** (`submit_schema_map`, `submit_profiles`) as an
explicit "I'm finished, here's the structured output" signal. The Copywriter
loops naturally until it has emitted a send/skip for everyone.

> Tool definitions live in `tools/` as JSON schemas; handlers are plain Python.
> Same mechanism for DB introspection, audience queries, and recording email
> decisions.

---

## 6. The five agents

**Orchestrator** (`agents/orchestrator.py`) — supervisor. Loads the memory docs
into state, computes `schema_refresh_needed` from four deterministic conditions
(manual flag / missing map / >7 days old / DB fingerprint changed), routes. No
LLM.

**Schema Intelligence** (`agents/schema_agent.py`) — reads `product_context`
first (a `subscriptions` table means different things in different products),
then uses introspection tools (`list_tables`, `describe_table`,
`list_foreign_keys`, `sample_rows`) to map the DB. Emits `schema_map.md` (saved
to Store, **preserving the customer-corrections section and archiving the prior
version** — Rule 10) and a *comprehensive* raw signal list for the human review.

**Audience Analysis** (`agents/audience_agent.py`) — the deterministic
eligibility filter (premium / unsubscribed / recently-emailed / too-new) runs in
**code** so it can't be skipped; the agent computes **concrete signal values**
("tailored twice", "last active 14 days ago") using only schema-map names. Emits
`eligible_users` + ephemeral `audience_analysis.md`.

**Copywriter** (`agents/copywriter_agent.py`) — the reasoning core. Per user it
weighs signals *as dimensions, not triggers*, checks email history to avoid
repeats, and emits a complete draft or a specific skip. After the loop we
**reconcile**: any undecided user is force-skipped so the audit log is complete
(Rule 6). Produces the run summary for Telegram.

**Delivery** (`agents/delivery_agent.py`) — the simplest. Sends via Resend, logs
every send/failure/skip to `ai_marketing_email_log`, continues past individual
failures. **Never calls Claude** (Rule 4) — the operator approved exact copy;
re-generating would violate that.

---

## 7. The generic, schema-agnostic design

This is the system's headline feature and a great interview topic.

- `services/supabase_service.py` is the **only** file that runs queries, and it
  **hardcodes no table/column names** — every identifier is a parameter derived
  from `schema_map.md` at runtime (Rule 2).
- The Schema agent turns an unknown DB into a `schema_map.md` that documents
  exact names, join paths, soft-delete flags, absent-row semantics, and JSONB
  paths.
- The Audience agent reads that map and builds queries dynamically.
- Result: **the same codebase serves any SaaS product** with zero per-customer
  code. Onboarding (form + schema analysis + signal review) is the only
  customer-specific work, and it produces *data*, not code.

> Interview soundbite: *"A single hardcoded table name anywhere destroys the
> entire value of the Schema Intelligence Agent — so query construction is
> 100% data-driven from the schema map."*

---

## 8. Security & operational design

- **Two Lambdas, hard key split** (`serverless.yml`): planning has Anthropic but
  not Resend; delivery has Resend but not Anthropic. Enforced at the
  *infrastructure* level, not just code — a compromised planner physically
  cannot send mail.
- **Service-role key** is mandatory: RLS (`auth.uid() = user_id`) would silently
  return zero rows for the cross-user reads the agent needs (Rule 1). The anon
  key "succeeds" and returns nothing — the nastiest possible bug.
- **Webhook security, 3 layers** (`handler_send.py`): secret-token header + chat
  id match + run is pending & not expired. Any failure returns **200** (so
  Telegram won't retry) and logs the rejection (Rule 15).
- **Optimistic concurrency** (Rule 14): approve/cancel is a conditional UPDATE
  filtered on the expected status, so a double-tap can't trigger two send waves —
  the second update affects zero rows and loses silently.
- **Auto-expiry** (Rule 13): every planning run first sweeps stale pending runs
  so the operator can never approve a days-old batch.
- **LangSmith** traces every node/LLM/tool call — non-negotiable observability.

---

## 9. The 18 critical rules

Each exists because of a specific failure mode. They're documented inline where
they apply; here's the index:

1. Always use the service-role key (anon key silently returns nothing).
2. No hardcoded table/column names — all from the schema map.
3. Planning Lambda never imports the email service.
4. Delivery Lambda never calls Claude.
5. Never use the anon key; one client config only.
6. Every eligible user → a send or skip (reconciled in the Copywriter).
7. Soft deletes always filtered.
8. Absent rows carry meaning — handle explicitly (LEFT JOIN semantics).
9. Frequency cap checks the agent log **and** any legacy log.
10. Schema-map writes preserve the customer-corrections section (+ archive).
11. State persisted (interrupt) **before** any Telegram notification.
12. Telegram messages are plain text, no parse_mode (copy can break parsers).
13. Expiry sweep runs before every planning invocation.
14. Optimistic concurrency on all run-state transitions.
15. All three webhook checks must pass; failures return 200.
16. Approved behaviors are vocabulary, not if/else triggers.
17. LangSmith configured before the first run.
18. Schema map human-verified once before first production send.

---

## 10. Framework trade-offs

**Why LangGraph over…**

- **Raw Anthropic SDK while-loop** — fine for one agent. Falls apart with
  multiple agents sharing state, conditional routing, and *durable* pause/resume
  across processes. You'd be hand-rolling a checkpointer.
- **CrewAI** — faster to prototype, but weaker state management, weaker
  debugging, and **no native durable checkpointing**. Pausing a crew across two
  Lambda invocations and resuming isn't a supported pattern — and that pattern is
  the spine of this system.
- **AutoGen** — built for conversational multi-agent *debate*, not a
  deterministic pipeline with a human approval gate.

**Why native Anthropic SDK over LangChain wrappers** — LangGraph is
provider-agnostic; native tool-use is cleaner, more predictable, and avoids
wrapper churn. We use LangGraph for orchestration and the raw SDK for inference.

---

## 11. Likely interview questions (with crisp answers)

**Q: What's the difference between the checkpointer and the store?**
The checkpointer snapshots one *run's* state (keyed by thread_id) at every node
transition so the graph can pause and resume — even across processes. The store
is long-lived *memory* (keyed by a customer namespace) holding the three context
docs across all runs. Different lifetimes, different keys, different jobs.

**Q: How does human-in-the-loop actually work across two Lambdas?**
The HITL node calls `interrupt()`, which persists state to the checkpointer and
returns control. Planning sends Telegram and exits. On approval, delivery calls
`ainvoke(Command(resume="approved"), thread_id=run_id)`; LangGraph loads the
checkpoint and continues from the paused node. The resume value becomes the
return of `interrupt()`.

**Q: How do you keep it generic across customers?**
Zero hardcoded identifiers. The Schema agent produces a schema map; the only
query file reads every table/column name from that map at runtime. Customer
specifics are *data* (three Store documents), never code.

**Q: How do you stop a planning bug from emailing users?**
Two-Lambda split. Planning can't send because it has no Resend key and never
imports the email service; delivery only runs after an operator approves, and it
never calls Claude, so it can only send the exact content that was approved.

**Q: Why deterministic routing instead of an LLM supervisor?**
Routing is cheap, auditable, and safe. The expensive, fallible LLM calls are
isolated to the leaf agents where reasoning is actually required. A model error
can't misroute the pipeline.

**Q: Signals vs triggers — why does that distinction matter?**
A trigger maps a condition to a fixed email (rules engine — dumb, can't combine
signals). Treating signals as *vocabulary* lets Claude weigh a user's full
combination and write one genuinely relevant message. It's the whole reason the
system beats Mailchimp-style tooling.

**Q: What happens if Claude doesn't emit a decision for a user?**
The Copywriter reconciles after the loop and force-skips any undecided user with
an explicit reason, so the audit log always accounts for every eligible user
(Rule 6).

**Q: How do you prevent double-sends from a double Telegram tap?**
Optimistic concurrency: the approve transition is a conditional UPDATE on
`status = pending_approval`. Only one tap wins; the second updates zero rows and
returns without resuming.
