"""
PostgresSaver checkpointer + AsyncPostgresStore setup.

These two are the durability backbone of the whole system, both backed by the
customer's Supabase Postgres (TDD §2.3, §2.4):

  * Checkpointer (PostgresSaver)  -> serializes the *run* state at every node
    transition. This is what makes pause/resume across two Lambda invocations
    possible: the planning Lambda writes the checkpoint and exits at the
    interrupt; the delivery Lambda reads it back and resumes.

  * Store (AsyncPostgresStore)    -> holds the *permanent* per-customer memory
    documents (product_context, schema_map, approved_behaviors), namespaced by
    customer id.

We use the async variants so a single asyncio event loop drives the whole run.
Both expose a `.setup()` that idempotently creates their tables (TDD §19.2).
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres.aio import AsyncPostgresStore

import config


@asynccontextmanager
async def open_checkpointer_and_store():
    """Yield a ready (checkpointer, store) pair bound to Supabase Postgres.

    Used as:
        async with open_checkpointer_and_store() as (saver, store):
            graph = build_graph(saver, store)
            ...

    The context manager owns the psycopg connection pool lifecycle so we never
    leak connections in the Lambda runtime.
    """
    conn_string = config.SUPABASE_DB_URL
    if not conn_string:
        raise RuntimeError("SUPABASE_DB_URL is required for checkpointer/store")

    async with AsyncPostgresSaver.from_conn_string(conn_string) as saver, \
            AsyncPostgresStore.from_conn_string(conn_string) as store:
        yield saver, store


async def run_setup() -> None:
    """Create checkpointer + store tables. Idempotent — safe to re-run.

    Call once before the first run (or from a one-off migration script).
    """
    async with open_checkpointer_and_store() as (saver, store):
        await saver.setup()
        await store.setup()
