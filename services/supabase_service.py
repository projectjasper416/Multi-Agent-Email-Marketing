"""
Supabase service — the ONLY place in the codebase that executes DB queries.

Two hard rules from the TDD live here (Critical Implementation Rules 1, 2 & 5):

  * Always use the SERVICE ROLE key. RLS restricts every table to
    `auth.uid() = user_id`, so the anon key silently returns zero rows for the
    cross-user reads this agent needs. The query "succeeds" and returns nothing
    — the worst class of bug. Service role bypasses RLS.

  * No table or column name is hardcoded here. Every name is a parameter passed
    in from the schema map in graph state. This file has no knowledge of what
    product it serves — that's what makes the system generic.

This module is schema-agnostic plumbing. The *intelligence* about which tables
and columns to use lives in schema_map.md and is supplied by the callers.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from supabase import Client, create_client

import config

# Single shared client. Rule 5: exactly one client config, service role only.
_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        if not config.SUPABASE_URL or not config.SUPABASE_SERVICE_ROLE_KEY:
            raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required")
        # Service role key — bypasses RLS. Treat like a prod DB password.
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)
    return _client


# ─────────────────────────────────────────────────────────────────────
# Schema introspection — used by the Schema Intelligence Agent's tools.
# These read Postgres information_schema via RPC so the agent can discover
# tables/columns/relationships without us hardcoding anything.
# (Requires a `exec_sql` style RPC or direct SQL; see notes in each fn.)
# ─────────────────────────────────────────────────────────────────────
def list_tables() -> list[str]:
    """All base table names in the public schema."""
    rows = _run_sql(
        """
        select table_name
        from information_schema.tables
        where table_schema = 'public' and table_type = 'BASE TABLE'
        order by table_name
        """
    )
    return [r["table_name"] for r in rows]


def describe_table(table_name: str) -> list[dict[str, Any]]:
    """Column definitions (name, type, nullability, default) for one table."""
    return _run_sql(
        f"""
        select column_name, data_type, is_nullable, column_default
        from information_schema.columns
        where table_schema = 'public' and table_name = {_lit(table_name)}
        order by ordinal_position
        """
    )


def list_foreign_keys() -> list[dict[str, Any]]:
    """Foreign-key relationships across the public schema (join paths)."""
    return _run_sql(
        """
        select
          tc.table_name           as from_table,
          kcu.column_name         as from_column,
          ccu.table_name          as to_table,
          ccu.column_name         as to_column
        from information_schema.table_constraints tc
        join information_schema.key_column_usage kcu
          on tc.constraint_name = kcu.constraint_name
        join information_schema.constraint_column_usage ccu
          on tc.constraint_name = ccu.constraint_name
        where tc.constraint_type = 'FOREIGN KEY' and tc.table_schema = 'public'
        """
    )


def table_row_count(table_name: str) -> int:
    rows = _run_sql(f'select count(*) as c from "{_ident(table_name)}"')
    return int(rows[0]["c"]) if rows else 0


def sample_rows(table_name: str, limit: int = 5) -> list[dict[str, Any]]:
    """A few representative rows — used to inspect JSONB content / value shapes."""
    return get_client().table(table_name).select("*").limit(limit).execute().data


# ─────────────────────────────────────────────────────────────────────
# Audience queries — used by the Audience Analysis Agent's tools.
# All identifiers arrive as parameters derived from the schema map.
# ─────────────────────────────────────────────────────────────────────
def select_rows(
    table: str,
    columns: str = "*",
    filters: list[dict[str, Any]] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Generic schema-driven SELECT.

    `filters` is a list of {"column", "op", "value"} where op is one of
    eq, neq, gt, gte, lt, lte, is_, in_. This keeps query construction generic
    while letting the agent express the conditions the schema map dictates.
    """
    q = get_client().table(table).select(columns)
    for f in filters or []:
        col, op, val = f["column"], f.get("op", "eq"), f.get("value")
        q = _apply_filter(q, col, op, val)
    if limit:
        q = q.limit(limit)
    return q.execute().data


def run_sql(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Escape hatch for the multi-table joins/aggregations the schema map
    describes (LEFT JOINs for absent-row semantics, soft-delete filters, etc.).
    Exposed to the audience agent as a tool. Identifiers come from the schema
    map — never hardcoded in this file."""
    return _run_sql(sql, params)


# ─────────────────────────────────────────────────────────────────────
# Frequency cap — checks BOTH the agent log and any legacy log (Rule 9).
# ─────────────────────────────────────────────────────────────────────
def user_ids_emailed_within(
    days: int,
    legacy_log: dict[str, str] | None = None,
) -> set[str]:
    """Return the set of user ids that received an email within `days` from
    EITHER ai_marketing_email_log OR the legacy log table the schema map
    identifies. These users are filtered out before Claude sees anyone.

    `legacy_log` (optional) = {"table": ..., "user_col": ..., "sent_at_col": ...}
    """
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    emailed: set[str] = set()

    # Agent's own log.
    agent_rows = (
        get_client()
        .table("ai_marketing_email_log")
        .select("user_id")
        .eq("customer_id", config.CUSTOMER_ID)
        .gte("sent_at", since)
        .execute()
        .data
    )
    emailed.update(str(r["user_id"]) for r in agent_rows if r.get("user_id"))

    # Legacy log, if the customer has one (Section 15).
    if legacy_log and legacy_log.get("table"):
        legacy_rows = (
            get_client()
            .table(legacy_log["table"])
            .select(legacy_log["user_col"])
            .gte(legacy_log["sent_at_col"], since)
            .execute()
            .data
        )
        emailed.update(str(r[legacy_log["user_col"]]) for r in legacy_rows if r.get(legacy_log["user_col"]))

    return emailed


# ─────────────────────────────────────────────────────────────────────
# Audit log writes (ai_marketing_email_log).
# ─────────────────────────────────────────────────────────────────────
def write_email_log(row: dict[str, Any]) -> None:
    """Insert one decision row (send or skip). Service role -> bypasses RLS."""
    get_client().table("ai_marketing_email_log").insert(row).execute()


def get_user_email_history(
    user_id: str,
    limit: int = 10,
    legacy_log: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Recent campaigns sent to one user, from the agent log (+ legacy if given).
    Used by the Copywriter so it doesn't repeat angles."""
    rows = (
        get_client()
        .table("ai_marketing_email_log")
        .select("campaign_key, subject, decision, sent_at")
        .eq("user_id", user_id)
        .order("sent_at", desc=True)
        .limit(limit)
        .execute()
        .data
    )
    history = [{**r, "source": "ai_agent"} for r in rows]

    if legacy_log and legacy_log.get("table"):
        legacy = (
            get_client()
            .table(legacy_log["table"])
            .select("*")
            .eq(legacy_log["user_col"], user_id)
            .order(legacy_log["sent_at_col"], desc=True)
            .limit(limit)
            .execute()
            .data
        )
        history.extend({**r, "source": "legacy"} for r in legacy)
    return history


# ── Internal helpers ──────────────────────────────────────────────────
def _apply_filter(q, col: str, op: str, val: Any):
    return {
        "eq": q.eq,
        "neq": q.neq,
        "gt": q.gt,
        "gte": q.gte,
        "lt": q.lt,
        "lte": q.lte,
        "is_": q.is_,
        "in_": q.in_,
    }.get(op, q.eq)(col, val)


def _lit(value: str) -> str:
    """Quote a string literal for safe inlining (doubles single quotes)."""
    return "'" + str(value).replace("'", "''") + "'"


def _ident(name: str) -> str:
    """Sanitize an identifier (table/column) for safe inlining in a quoted name."""
    return str(name).replace('"', "")


def _run_sql(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Run a read-only SQL query through the customer's Supabase Postgres.

    Implemented via a database RPC named `exec_sql` that the migration creates
    (SECURITY DEFINER, read-only guard, returns JSON). We keep raw SQL out of the
    codebase except for the introspection/aggregation queries above, all of which
    reference only information_schema or schema-map-derived identifiers.
    """
    resp = get_client().rpc("exec_sql", {"query": sql, "params": params or {}}).execute()
    return resp.data or []
