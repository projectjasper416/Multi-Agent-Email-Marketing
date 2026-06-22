"""
Auth service — wraps Supabase Auth (GoTrue) for the onboarding web layer.

This is the ONE place that talks to Supabase Auth, and the only place the anon
(public) key is used. It is deliberately separate from supabase_service.py:

  * supabase_service.py uses the SERVICE-ROLE key and bypasses RLS — it is the
    agent acting as the system, reading across all users.
  * auth_service.py uses the ANON key and acts as the end user — sign-up,
    sign-in, JWT verification and token refresh, all scoped to one human.

The web layer never sees raw GoTrue objects: every function returns plain dicts
(or None / raises AuthError) so app.py stays decoupled from the SDK shape.

The account is the customer: a signed-in user's Supabase auth uid IS the
customer_id that namespaces all of their stored onboarding memory. There is no
separate customer_id to type or manage.
"""
from __future__ import annotations

from supabase import Client, create_client

import config


class AuthError(Exception):
    """Raised when sign-up / sign-in fails (bad credentials, taken email, …).
    Carries a human-readable message safe to surface to the UI."""


# Single shared anon-key client. Auth calls pass tokens explicitly (get_user,
# refresh_session), so verification is stateless w.r.t. this shared instance.
_client: Client | None = None


def _auth():
    global _client
    if _client is None:
        if not config.SUPABASE_URL or not config.SUPABASE_ANON_KEY:
            raise RuntimeError("SUPABASE_URL and SUPABASE_ANON_KEY are required for auth")
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_ANON_KEY)
    return _client.auth


# ── Public API ─────────────────────────────────────────────────────────
def sign_up(email: str, password: str) -> dict:
    """Create an account. Returns a normalized session dict.

    If the Supabase project requires email confirmation, `access_token` comes
    back None (no session until confirmed) and the caller should tell the user
    to check their inbox. Otherwise the user is signed in immediately.
    """
    try:
        res = _auth().sign_up({"email": email, "password": password})
    except Exception as exc:  # gotrue raises AuthApiError / AuthError
        raise AuthError(_message(exc)) from exc
    return _normalize(res.session, res.user)


def sign_in(email: str, password: str) -> dict:
    """Verify credentials and return a normalized session dict (with tokens)."""
    try:
        res = _auth().sign_in_with_password({"email": email, "password": password})
    except Exception as exc:
        raise AuthError(_message(exc)) from exc
    return _normalize(res.session, res.user)


def get_user(access_token: str) -> dict | None:
    """Verify an access token. Returns {customer_id, email} or None if invalid
    or expired. Never raises — an invalid token is just an unauthenticated user.
    """
    try:
        res = _auth().get_user(access_token)
    except Exception:
        return None
    if not res or not getattr(res, "user", None):
        return None
    return _user(res.user)


def refresh(refresh_token: str) -> dict | None:
    """Exchange a refresh token for a fresh session. Returns a normalized
    session dict (with new tokens) or None if the refresh token is invalid."""
    try:
        res = _auth().refresh_session(refresh_token)
    except Exception:
        return None
    if not res or not res.session:
        return None
    return _normalize(res.session, res.user)


# ── Internals ──────────────────────────────────────────────────────────
def _user(user) -> dict | None:
    return {"customer_id": user.id, "email": user.email} if user else None


def _normalize(session, user) -> dict:
    """Flatten a GoTrue session/user pair into the dict the web layer expects."""
    return {
        "user": _user(user),
        "access_token": session.access_token if session else None,
        "refresh_token": session.refresh_token if session else None,
    }


def _message(exc: Exception) -> str:
    """Best-effort human-readable message from a GoTrue error."""
    return getattr(exc, "message", None) or str(exc) or "Authentication failed"
