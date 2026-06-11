"""
LangGraph Store service — the agent's permanent, per-customer memory (TDD §7).

Three documents are stored per customer, namespaced by customer id so one
customer's run can never read or overwrite another's:

    ("customer", <id>, "product_context")   -> product_context.md   (str)
    ("customer", <id>, "schema_map")         -> schema_map.md         (str)
    ("customer", <id>, "approved_behaviors") -> approved_behaviors    (list)

This module is the single choke-point for those reads/writes: it owns namespace
construction, serialization, the schema-map metadata used for the freshness
check, and the versioning that preserves the customer-corrections section across
re-analyses (Rule 10).

All functions take the live `store` (AsyncPostgresStore) so the caller controls
the connection lifecycle.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

# Keys within each customer namespace.
NS_ROOT = "customer"
DOC_PRODUCT = "product_context"
DOC_SCHEMA = "schema_map"
DOC_BEHAVIORS = "approved_behaviors"
DOC_EMAIL = "email_settings"
ARCHIVE_SCHEMA = "schema_map_archive"

# Marker that separates Claude-owned content from the never-overwrite section.
CORRECTIONS_HEADER = "## Customer Corrections"


def _ns(customer_id: str, doc: str) -> tuple[str, str, str]:
    return (NS_ROOT, customer_id, doc)


# ── Reads ─────────────────────────────────────────────────────────────
async def load_customer_context(store, customer_id: str) -> dict[str, Any]:
    """Load all three documents at the start of a daily run.

    Returns a dict with keys product_context, schema_map, approved_behaviors and
    schema_meta. Any missing document comes back as None so the orchestrator can
    detect an un-onboarded customer and route to setup instead of the daily flow.
    """
    product = await _get_value(store, customer_id, DOC_PRODUCT)
    schema = await _get_value(store, customer_id, DOC_SCHEMA)
    behaviors = await _get_value(store, customer_id, DOC_BEHAVIORS)
    email = await _get_value(store, customer_id, DOC_EMAIL)
    schema_meta = await _get_meta(store, customer_id, DOC_SCHEMA)

    return {
        "product_context": product.get("text") if product else None,
        "schema_map": schema.get("text") if schema else None,
        "approved_behaviors": behaviors.get("items") if behaviors else None,
        "email_settings": email if email else None,
        "schema_meta": schema_meta,
    }


async def save_email_settings(store, customer_id: str, settings: dict) -> None:
    """Persist sender config: from_address, brand_name, unsubscribe_base_url."""
    await store.aput(_ns(customer_id, DOC_EMAIL), "doc", settings)


async def get_schema_meta(store, customer_id: str) -> dict[str, Any]:
    """Just the schema-map metadata: analyzed_at, refresh_requested, db_fingerprint.
    Used by the orchestrator's deterministic refresh check."""
    return await _get_meta(store, customer_id, DOC_SCHEMA)


# ── Writes ────────────────────────────────────────────────────────────
async def save_product_context(store, customer_id: str, text: str) -> None:
    await store.aput(_ns(customer_id, DOC_PRODUCT), "doc", {"text": text})


async def save_approved_behaviors(store, customer_id: str, items: list[dict]) -> None:
    await store.aput(_ns(customer_id, DOC_BEHAVIORS), "doc", {"items": items})


async def save_schema_map(
    store,
    customer_id: str,
    new_text: str,
    db_fingerprint: str | None = None,
) -> None:
    """Write a new schema map while (Rule 10) preserving the customer-corrections
    section verbatim and archiving the previous version.

    Steps:
      1. read the previous map, extract its corrections section
      2. archive the previous map under a timestamped key
      3. graft the carried-forward corrections onto the new map
      4. write the new map with fresh metadata
    """
    prev = await _get_value(store, customer_id, DOC_SCHEMA)
    carried = _extract_corrections(prev.get("text")) if prev else ""

    if prev:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        await store.aput((NS_ROOT, customer_id, ARCHIVE_SCHEMA, ts), "doc", prev)

    merged = _merge_corrections(new_text, carried)
    await store.aput(
        _ns(customer_id, DOC_SCHEMA),
        "doc",
        {
            "text": merged,
            "analyzed_at": datetime.now(timezone.utc).isoformat(),
            "refresh_requested": False,
            "db_fingerprint": db_fingerprint or "",
        },
    )


async def request_schema_refresh(store, customer_id: str) -> None:
    """Settings-page action: flag that the next run must re-analyze the schema."""
    doc = await _get_value(store, customer_id, DOC_SCHEMA) or {}
    doc["refresh_requested"] = True
    await store.aput(_ns(customer_id, DOC_SCHEMA), "doc", doc)


# ── Internals ─────────────────────────────────────────────────────────
async def _get_value(store, customer_id: str, doc: str) -> dict[str, Any] | None:
    item = await store.aget(_ns(customer_id, doc), "doc")
    return item.value if item else None


async def _get_meta(store, customer_id: str, doc: str) -> dict[str, Any]:
    val = await _get_value(store, customer_id, doc) or {}
    return {
        "analyzed_at": val.get("analyzed_at"),
        "refresh_requested": val.get("refresh_requested", False),
        "db_fingerprint": val.get("db_fingerprint", ""),
    }


def _extract_corrections(text: str | None) -> str:
    """Pull everything from the corrections header onward."""
    if not text or CORRECTIONS_HEADER not in text:
        return ""
    return text[text.index(CORRECTIONS_HEADER):].strip()


def _merge_corrections(new_text: str, carried: str) -> str:
    """Ensure the new map ends with the carried-forward corrections section."""
    base = new_text
    if CORRECTIONS_HEADER in base:
        base = base[: base.index(CORRECTIONS_HEADER)].rstrip()
    if not carried:
        carried = f"{CORRECTIONS_HEADER}\n\n<!-- Customer-owned. Agents never overwrite this section. -->"
    return f"{base}\n\n{carried}\n"
