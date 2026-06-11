"""
Resend integration (TDD §16).

CRITICAL (Rule 3): this module is imported ONLY by the delivery Lambda. The
planning Lambda must never import it — that's the architectural guarantee that a
planning-side bug can't send email. `RESEND_API_KEY` only exists in the delivery
Lambda's environment, so importing this in planning would also fail at runtime.

Every send carries the two List-Unsubscribe headers (§16.2) — mandatory for
deliverability with major inbox providers.
"""
from __future__ import annotations

from typing import Any

import resend

import config
from services.email_template import build_html


def _ensure_key() -> None:
    if not config.RESEND_API_KEY:
        raise RuntimeError("RESEND_API_KEY is required in the delivery Lambda")
    resend.api_key = config.RESEND_API_KEY


def send_email(
    *,
    to_email: str,
    subject: str,
    body_text: str,
    cta_text: str,
    cta_url: str,
    user_id: str,
    from_address: str,
    brand_name: str,
    unsubscribe_base_url: str,
) -> dict[str, Any]:
    """Send one email via Resend.

    Returns {"success": bool, "message_id": str|None, "error": str|None}.
    Never raises for a provider error — the Delivery Agent must continue with
    the remaining emails if one fails (§15 / TDD §15).
    """
    _ensure_key()

    html_body = build_html(
        user_id=user_id,
        body_text=body_text,
        cta_text=cta_text,
        cta_url=cta_url,
        brand_name=brand_name,
        unsubscribe_base_url=unsubscribe_base_url,
    )

    params: dict[str, Any] = {
        "from": from_address,
        "to": [to_email],
        "subject": subject,
        "html": html_body,
        "headers": {
            # One-click unsubscribe — required for bulk-sender compliance.
            "List-Unsubscribe": f"<{unsubscribe_base_url}?uid={user_id}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    }

    try:
        result = resend.Emails.send(params)
        return {"success": True, "message_id": result.get("id"), "error": None}
    except Exception as exc:  # noqa: BLE001 — a single failure must not stop the run
        return {"success": False, "message_id": None, "error": str(exc)}
