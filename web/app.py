"""
Onboarding web UI — a thin FastAPI layer over onboarding/setup_flow.py.

setup_flow.py's own docstring describes exactly this layer:

    "A thin web/API layer would call these in order, pausing for the operator
     between analyze_schema() and save_approved_signals()."

That is all this app is. Three JSON endpoints wrap the three setup functions,
and a single static page drives them as a 3-step wizard. The intelligence stays
in the agents; this file only marshals form input in and results out.

Run locally from the repo root:

    pip install -r requirements.txt
    uvicorn web.app:app --reload

then open http://127.0.0.1:8000/
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from onboarding import setup_flow

app = FastAPI(title="AI Email Marketing — Onboarding")

_STATIC = Path(__file__).parent / "static"


# ── Request models (mirror the setup_flow signatures) ─────────────────────
class BriefRequest(BaseModel):
    customer_id: str = Field(..., min_length=1)
    # Free-form product answers; Claude synthesizes these into product_context.md.
    form_inputs: dict
    # Sender config persisted as email_settings.
    email_settings: dict


class AnalyzeRequest(BaseModel):
    customer_id: str = Field(..., min_length=1)


class ApproveRequest(BaseModel):
    customer_id: str = Field(..., min_length=1)
    # The raw signal list, each item carrying an added `approved` boolean.
    approvals: list[dict]


# ── API: one endpoint per setup step ──────────────────────────────────────
@app.post("/api/onboarding/brief")
async def brief(req: BriefRequest) -> dict:
    """Step 1: synthesize + persist product_context.md and email settings."""
    text = await setup_flow.generate_brief(
        req.customer_id, req.form_inputs, req.email_settings
    )
    return {"brief": text}


@app.post("/api/onboarding/analyze")
async def analyze(req: AnalyzeRequest) -> dict:
    """Step 2: run the Schema Intelligence Agent; return raw signals to review."""
    raw_signals = await setup_flow.analyze_schema(req.customer_id)
    return {"raw_signals": raw_signals}


@app.post("/api/onboarding/approve")
async def approve(req: ApproveRequest) -> dict:
    """Step 3: keep only the signals the operator marked yes. Setup complete."""
    approved = await setup_flow.save_approved_signals(req.customer_id, req.approvals)
    return {"approved": approved}


# ── Static page ────────────────────────────────────────────────────────────
@app.get("/")
async def index() -> FileResponse:
    return FileResponse(_STATIC / "index.html")


app.mount("/static", StaticFiles(directory=_STATIC), name="static")
