"""
Onboarding web UI — a thin FastAPI layer over onboarding/setup_flow.py, gated by
Supabase Auth.

The account IS the customer. A signed-in user's Supabase auth uid is the
customer_id that namespaces all of their stored onboarding memory, so there is
no customer_id to type. Progress persists server-side (each setup step writes one
document to the Store); on login — even from another device — /api/onboarding/
status reports which documents exist and the UI resumes at the right step.

Session handling: login/signup set two httpOnly cookies (access + refresh
tokens). JS can't read them, so a stolen-via-XSS token is not a risk. The
require_user dependency verifies the access token on every request and silently
refreshes it from the refresh-token cookie when it has expired.

Run locally from the repo root:

    pip install -r requirements.txt
    uvicorn web.app:app --reload

then open http://127.0.0.1:8000/
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import config
from onboarding import setup_flow
from services import auth_service

app = FastAPI(title="AI Email Marketing — Onboarding")

_STATIC = Path(__file__).parent / "static"

# httpOnly session cookies holding the Supabase tokens.
ACCESS_COOKIE = "sb_access"
REFRESH_COOKIE = "sb_refresh"
ACCESS_MAX_AGE = 60 * 60            # access token lifetime (~1h)
REFRESH_MAX_AGE = 60 * 60 * 24 * 30  # keep the user signed in across restarts


# ── Session cookies ───────────────────────────────────────────────────────
def _set_session_cookies(response: Response, session: dict) -> None:
    common = dict(httponly=True, secure=config.WEB_COOKIE_SECURE, samesite="lax", path="/")
    response.set_cookie(ACCESS_COOKIE, session["access_token"], max_age=ACCESS_MAX_AGE, **common)
    response.set_cookie(REFRESH_COOKIE, session["refresh_token"], max_age=REFRESH_MAX_AGE, **common)


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/")


async def require_user(request: Request, response: Response) -> dict:
    """Resolve the signed-in user from the session cookies, or 401.

    Verifies the access token; if it has expired, transparently refreshes the
    session from the refresh-token cookie and re-issues both cookies. Returns
    {customer_id, email} — the customer_id namespaces all of this user's memory.
    """
    access = request.cookies.get(ACCESS_COOKIE)
    user = auth_service.get_user(access) if access else None

    if user is None:
        refresh_token = request.cookies.get(REFRESH_COOKIE)
        session = auth_service.refresh(refresh_token) if refresh_token else None
        if session and session.get("access_token"):
            _set_session_cookies(response, session)
            user = session["user"]

    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


# ── Auth request models ────────────────────────────────────────────────────
class Credentials(BaseModel):
    email: str = Field(..., min_length=3)
    password: str = Field(..., min_length=6)


# ── API: auth ──────────────────────────────────────────────────────────────
@app.post("/api/auth/signup")
async def signup(creds: Credentials, response: Response) -> dict:
    """Create an account. Signs the user in immediately unless the Supabase
    project requires email confirmation, in which case they must confirm first."""
    try:
        session = auth_service.sign_up(creds.email, creds.password)
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not session.get("access_token"):
        return {"authenticated": False, "needs_confirmation": True, "email": creds.email}
    _set_session_cookies(response, session)
    return {"authenticated": True, "user": session["user"]}


@app.post("/api/auth/login")
async def login(creds: Credentials, response: Response) -> dict:
    try:
        session = auth_service.sign_in(creds.email, creds.password)
    except auth_service.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    _set_session_cookies(response, session)
    return {"authenticated": True, "user": session["user"]}


@app.post("/api/auth/logout")
async def logout(response: Response) -> dict:
    _clear_session_cookies(response)
    return {"ok": True}


@app.get("/api/auth/me")
async def me(user: dict = Depends(require_user)) -> dict:
    """Who am I — used on page load to decide auth screen vs. resume the wizard."""
    return {"user": user}


# ── Onboarding request models (customer_id now comes from the session) ──────
class BriefRequest(BaseModel):
    # Free-form product answers; Claude synthesizes these into product_context.md.
    form_inputs: dict
    # Sender config persisted as email_settings.
    email_settings: dict


class ApproveRequest(BaseModel):
    # The raw signal list, each item carrying an added `approved` boolean.
    approvals: list[dict]


# ── API: one endpoint per setup step ───────────────────────────────────────
@app.get("/api/onboarding/status")
async def status(user: dict = Depends(require_user)) -> dict:
    """Which steps this account has completed — drives resume-on-load."""
    return await setup_flow.get_status(user["customer_id"])


@app.post("/api/onboarding/brief")
async def brief(req: BriefRequest, user: dict = Depends(require_user)) -> dict:
    """Step 1: synthesize + persist product_context.md and email settings."""
    text = await setup_flow.generate_brief(
        user["customer_id"], req.form_inputs, req.email_settings
    )
    return {"brief": text}


@app.post("/api/onboarding/analyze")
async def analyze(user: dict = Depends(require_user)) -> dict:
    """Step 2: run the Schema Intelligence Agent; return raw signals to review."""
    try:
        raw_signals = await setup_flow.analyze_schema(user["customer_id"])
    except ValueError as exc:
        # Un-onboarded account — no brief to analyze against yet.
        raise HTTPException(status_code=404, detail=str(exc))
    return {"raw_signals": raw_signals}


@app.post("/api/onboarding/approve")
async def approve(req: ApproveRequest, user: dict = Depends(require_user)) -> dict:
    """Step 3: keep only the signals the operator marked yes. Setup complete."""
    approved = await setup_flow.save_approved_signals(user["customer_id"], req.approvals)
    return {"approved": approved}


# ── Static page ────────────────────────────────────────────────────────────
@app.get("/")
async def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


app.mount("/static", StaticFiles(directory=_STATIC), name="static")
