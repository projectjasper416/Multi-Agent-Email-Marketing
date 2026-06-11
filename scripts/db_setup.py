"""
One-time database setup (TDD §19.2).

Creates the LangGraph Store + PostgresSaver tables in Supabase Postgres. The SQL
migrations under /migrations create the audit log, runs table, and exec_sql RPC.

Run once before the first use:
    python -m scripts.db_setup
"""
from __future__ import annotations

import asyncio

from graph.checkpointer import run_setup


if __name__ == "__main__":
    asyncio.run(run_setup())
    print("LangGraph Store + PostgresSaver tables are ready.")
    print("Remember to also run the SQL files in /migrations against Supabase.")
