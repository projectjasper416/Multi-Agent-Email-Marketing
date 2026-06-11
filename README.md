# AI Email Marketing Agent

A **generic, standalone multi-agent email marketing system** built on LangGraph.
Any SaaS product connects its database, completes a short onboarding, and the
system discovers the schema, identifies behavioral signals, and sends
intelligent personalized emails — with a human approving every batch.

> Core principle: **no hardcoded table names, no hardcoded campaign logic, no
> if/else email rules.** The Schema Intelligence Agent maps each customer's
> database at setup; the Copywriter Agent reasons from signals at runtime.
> Intelligence lives in the agents, not the code.

---

## Architecture at a glance

```
SETUP (once per customer)                 DAILY RUN (every day, 9am UTC)
─────────────────────────                 ──────────────────────────────
form ─► product_context.md                EventBridge ─► Planning Lambda
        │                                      │
Schema Intelligence Agent ─► schema_map.md     orchestrator (load context, route)
        │                                      │
raw signals ─► [human yes/no] ─►               ├─► (schema agent, only if stale)
        approved_behaviors                     ├─► audience agent  (eligibility + signals)
                                               ├─► copywriter agent (reason ► draft/skip)
                                               └─► interrupt() ───► Telegram preview
                                                                        │
                                                   [human approves] ────┘
                                                        │
                                               Delivery Lambda ─► Resend ─► log
```

Five agent nodes on one shared, typed graph state:

| Node | Runs | Job |
|---|---|---|
| **Orchestrator** | every run | Load the 3 memory docs, decide schema-refresh, route. Deterministic, no LLM. |
| **Schema Intelligence** | setup + when stale | Discover the DB, write `schema_map.md`, propose raw signals. |
| **Audience Analysis** | every run | Eligibility filtering + concrete signal values per user. |
| **Copywriter** | every run | Reason per user → full email draft or specific skip. |
| **Delivery** | after approval | Send via Resend, log every outcome. No LLM. |

---

## Why these frameworks

- **LangGraph** — typed shared state + durable `PostgresSaver` checkpointing +
  native `interrupt()` give us a multi-agent pipeline that can *pause for human
  approval in one Lambda and resume in another* hours later. That pause/resume
  across processes is the thing raw SDK loops, CrewAI, and AutoGen don't do
  cleanly.
- **Anthropic SDK (native, no LangChain wrappers)** — clean tool-use, no wrapper
  churn. Model pinned to `claude-sonnet-4-6`.
- **LangGraph Store** (`AsyncPostgresStore`) — per-customer memory, namespaced by
  customer id, versioned.
- **Supabase** (service-role key) — the customer's own Postgres; the only shared
  resource. Service role is mandatory because RLS would otherwise hide every
  other user's rows.
- **Resend** — delivery, with one-click `List-Unsubscribe` headers.
- **Telegram** (raw HTTP) — the human approval gate.
- **LangSmith** — full tracing of every node / LLM / tool call.

See **[ARCHITECTURE.md](ARCHITECTURE.md)** for the deep, interview-oriented walkthrough.

---

## Repository layout

```
graph/        state.py · graph.py · checkpointer.py     (the LangGraph machine)
agents/       orchestrator · schema · audience · copywriter · delivery · prompts
tools/        db_tools · email_tools                    (Anthropic tool-use defs)
services/     supabase · store · email · email_template · telegram · runs · llm
onboarding/   context_generator · setup_flow
scripts/      db_setup.py
migrations/   001 email log · 002 runs · 003 exec_sql rpc
handler_plan.py   handler_send.py   serverless.yml   config.py
```

The two Lambdas have a **hard key split** enforced in `serverless.yml`:
planning gets `ANTHROPIC_API_KEY` (never `RESEND_API_KEY`); delivery gets
`RESEND_API_KEY` (never `ANTHROPIC_API_KEY`). A planning-side bug cannot send
email; the delivery side cannot call Claude.

---

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env          # fill in your keys

# 1. create infra tables
python -m scripts.db_setup
#    then run migrations/001..003 against your Supabase

# 2. onboard a customer (programmatically — see onboarding/setup_flow.py)
#    generate_brief -> analyze_schema -> [review signals] -> save_approved_signals

# 3. local dry run of a planning pass
python handler_plan.py
```

## Deploy

```bash
npm i -g serverless serverless-python-requirements
serverless deploy
# register the Telegram webhook to the API Gateway /telegram/callback URL
```

---

## The 18 critical rules

All enforced in code and documented inline at the point they matter. Highlights:
service-role key only · zero hardcoded identifiers · planning never imports email
· delivery never calls Claude · every eligible user gets a send/skip · soft
deletes always filtered · frequency cap checks agent **and** legacy logs · all 3
webhook checks must pass · approved signals are vocabulary, not triggers. Full
list in [ARCHITECTURE.md](ARCHITECTURE.md#the-18-critical-rules).
