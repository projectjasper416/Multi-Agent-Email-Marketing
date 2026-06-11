"""
Database tool definitions + handlers (TDD §9.1, §9.2).

Two sets of tools, both backed by `supabase_service`:

  * Schema Intelligence tools — introspect the database (list tables, describe
    columns, foreign keys, row counts, sample rows). This is how the agent
    *discovers* the schema instead of us hardcoding it.

  * Audience Analysis tools — run schema-driven SELECTs / SQL whose identifiers
    come from the schema map (never hardcoded — Rule 2).

Each tool is a pair: an Anthropic JSON schema (the `*_TOOLS` lists) and a Python
handler the tool-use loop dispatches to.
"""
from __future__ import annotations

from typing import Any

from services import supabase_service as db


# ─────────────────────────────────────────────────────────────────────
# Schema Intelligence Agent tools
# ─────────────────────────────────────────────────────────────────────
SCHEMA_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_tables",
        "description": "List every base table in the customer's public schema.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "describe_table",
        "description": "Get column names, types, nullability and defaults for one table.",
        "input_schema": {
            "type": "object",
            "properties": {"table_name": {"type": "string"}},
            "required": ["table_name"],
        },
    },
    {
        "name": "list_foreign_keys",
        "description": "List all foreign-key relationships (join paths) in the schema.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "table_row_count",
        "description": "Count rows in a table to understand data volume.",
        "input_schema": {
            "type": "object",
            "properties": {"table_name": {"type": "string"}},
            "required": ["table_name"],
        },
    },
    {
        "name": "sample_rows",
        "description": "Fetch a few representative rows to inspect JSONB content or value shapes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["table_name"],
        },
    },
]

SCHEMA_TOOL_HANDLERS = {
    "list_tables": lambda i: db.list_tables(),
    "describe_table": lambda i: db.describe_table(i["table_name"]),
    "list_foreign_keys": lambda i: db.list_foreign_keys(),
    "table_row_count": lambda i: db.table_row_count(i["table_name"]),
    "sample_rows": lambda i: db.sample_rows(i["table_name"], i.get("limit", 5)),
}


# ─────────────────────────────────────────────────────────────────────
# Audience Analysis Agent tools
# ─────────────────────────────────────────────────────────────────────
AUDIENCE_TOOLS: list[dict[str, Any]] = [
    {
        "name": "select_rows",
        "description": (
            "Schema-driven SELECT. Provide table, columns, optional filters. "
            "All identifiers MUST come from the schema map — never invented."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table": {"type": "string"},
                "columns": {"type": "string", "default": "*"},
                "filters": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "column": {"type": "string"},
                            "op": {
                                "type": "string",
                                "enum": ["eq", "neq", "gt", "gte", "lt", "lte", "is_", "in_"],
                            },
                            "value": {},
                        },
                        "required": ["column"],
                    },
                },
                "limit": {"type": "integer"},
            },
            "required": ["table"],
        },
    },
    {
        "name": "run_sql",
        "description": (
            "Run a read-only SQL query for multi-table joins/aggregations the "
            "schema map describes (LEFT JOIN for absent-row semantics, soft-delete "
            "filters, etc.). Identifiers come from the schema map only."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"sql": {"type": "string"}},
            "required": ["sql"],
        },
    },
]

AUDIENCE_TOOL_HANDLERS = {
    "select_rows": lambda i: db.select_rows(
        i["table"], i.get("columns", "*"), i.get("filters"), i.get("limit")
    ),
    "run_sql": lambda i: db.run_sql(i["sql"]),
}
