"""
Telegram Bot API — raw HTTPS via `requests`, no SDK (TDD §2.7, §14.2).

The approval gate's whole UX is here: a run summary, one message per planned
email, then an inline keyboard with Approve / Cancel. Messages go out in plain
text with NO parse_mode (Rule 12) — Claude's free-form copy can contain
characters that would break HTML/MarkdownV2 parsing.
"""
from __future__ import annotations

import time
from typing import Any

import requests

import config


def _api(method: str) -> str:
    return f"{config.TELEGRAM_API_BASE}/bot{config.TELEGRAM_BOT_TOKEN}/{method}"


def send_message(text: str, reply_markup: dict | None = None) -> dict[str, Any]:
    """Send a plain-text message to the operator chat. Returns the Telegram result."""
    payload: dict[str, Any] = {"chat_id": config.TELEGRAM_CHAT_ID, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    resp = requests.post(_api("sendMessage"), json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json().get("result", {})


def edit_message(message_id: int, text: str) -> None:
    """Edit a previously sent message (e.g. the summary after approve/cancel)."""
    requests.post(
        _api("editMessageText"),
        json={"chat_id": config.TELEGRAM_CHAT_ID, "message_id": message_id, "text": text},
        timeout=15,
    )


def answer_callback(callback_query_id: str, text: str = "") -> None:
    """Acknowledge a button tap so Telegram stops the loading spinner."""
    requests.post(
        _api("answerCallbackQuery"),
        json={"callback_query_id": callback_query_id, "text": text},
        timeout=15,
    )


# ── High-level: the full approval notification sequence (§14.2) ───────
def send_approval_request(
    *,
    run_id: str,
    summary_text: str,
    email_previews: list[str],
) -> int:
    """Send summary -> one message per email -> approve/cancel buttons.

    Returns the message_id of the SUMMARY message so the caller can persist it
    and edit it later (after approve/cancel/expiry).
    """
    summary = send_message(summary_text)
    summary_message_id = summary.get("message_id")

    for preview in email_previews:
        send_message(preview)
        # Respect Telegram's per-chat rate limit (~1 msg/sec).
        time.sleep(config.TELEGRAM_SEND_DELAY_SECONDS)

    send_message(
        "Review the drafts above, then choose:",
        reply_markup={
            "inline_keyboard": [
                [
                    {"text": "✅ Approve", "callback_data": f"approve:{run_id}"},
                    {"text": "✖️ Cancel", "callback_data": f"cancel:{run_id}"},
                ]
            ]
        },
    )
    return summary_message_id


def format_summary(run_summary: dict[str, Any], run_id: str) -> str:
    breakdown = run_summary.get("campaign_breakdown", {}) or {}
    lines = [
        "📨 Email run ready for review",
        f"Run: {run_id}",
        "",
        f"Eligible users: {run_summary.get('total_eligible', 0)}",
        f"Planned sends:  {run_summary.get('total_planned', 0)}",
        f"Skipped:        {run_summary.get('total_skipped', 0)}",
    ]
    if breakdown:
        lines.append("")
        lines.append("By campaign:")
        lines.extend(f"  • {k}: {v}" for k, v in breakdown.items())
    if run_summary.get("notable_patterns"):
        lines.append("")
        lines.append(f"Notable: {run_summary['notable_patterns']}")
    return "\n".join(lines)


def format_email_preview(email: dict[str, Any], index: int, total: int) -> str:
    return "\n".join(
        [
            f"✉️  {index}/{total}  →  {email.get('name') or email.get('email')}",
            f"To: {email.get('email')}",
            "",
            f"Subject: {email.get('subject')}",
            "",
            email.get("body", ""),
            "",
            f"CTA: {email.get('cta_text')} → {email.get('cta_url')}",
            f"Signal: {email.get('primary_signal')}  |  Campaign: {email.get('campaign_key')}",
        ]
    )
