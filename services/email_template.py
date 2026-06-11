"""
HTML email builder (TDD §16.3).

Assembles a clean, inline-CSS HTML email as a plain Python string — no templating
engine. Four inputs only: the user id (for the unsubscribe link), the body text
Claude wrote, the CTA label, and the CTA URL. Newlines in the body become <br>.

Kept deliberately simple and brand-neutral so it works for any customer.
"""
from __future__ import annotations

import html


def build_html(
    *,
    user_id: str,
    body_text: str,
    cta_text: str,
    cta_url: str,
    brand_name: str,
    unsubscribe_base_url: str,
    accent_color: str = "#2563eb",
) -> str:
    safe_body = html.escape(body_text).replace("\n", "<br>")
    safe_cta = html.escape(cta_text)
    unsubscribe_url = f"{unsubscribe_base_url}?uid={user_id}"

    return f"""\
<!DOCTYPE html>
<html>
  <body style="margin:0;padding:0;background:#f4f4f7;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f7;padding:24px 0;">
      <tr><td align="center">
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:480px;background:#ffffff;border-radius:12px;overflow:hidden;">
          <tr><td style="padding:24px 32px;border-bottom:1px solid #eee;">
            <span style="font-size:18px;font-weight:700;color:#111;">{html.escape(brand_name)}</span>
          </td></tr>
          <tr><td style="padding:28px 32px;font-size:15px;line-height:1.6;color:#333;">
            {safe_body}
          </td></tr>
          <tr><td style="padding:0 32px 28px;">
            <a href="{html.escape(cta_url)}"
               style="display:inline-block;background:{accent_color};color:#ffffff;text-decoration:none;
                      font-weight:600;font-size:15px;padding:12px 22px;border-radius:8px;">
              {safe_cta}
            </a>
          </td></tr>
          <tr><td style="padding:18px 32px;border-top:1px solid #eee;font-size:12px;color:#999;">
            You're receiving this because you have an account with {html.escape(brand_name)}.<br>
            <a href="{html.escape(unsubscribe_url)}" style="color:#999;">Unsubscribe</a>
          </td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>"""
