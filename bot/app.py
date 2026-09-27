"""HTTP surface for the magicpin judge harness.

Run with ONE worker — all state is in process memory:
    uvicorn bot.app:app --host 0.0.0.0 --port 8080 --workers 1
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import llm as llm_mod
from .facts import now_or
from .replies import respond
from .store import Store
from .tick import TICK_BUDGET_S, run_tick

log = logging.getLogger("vera.app")
VERSION = "1.0.0"
REPLY_BUDGET_S = 10.0
BACKOFF_S = 300
_UNSET = object()


class ContextBody(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: dict[str, Any]
    delivered_at: str | None = None


class TickBody(BaseModel):
    now: str | None = None
    available_triggers: list[str] = Field(default_factory=list)


class ReplyBody(BaseModel):
    conversation_id: str = "conv_unknown"
    merchant_id: str | None = None
    customer_id: str | None = None
    from_role: str = "merchant"
    message: str = ""
    received_at: str | None = None
    turn_number: int | None = None


def _metadata(llm) -> dict:
    members = [m.strip() for m in os.getenv("TEAM_MEMBERS", "").split(",") if m.strip()]
    return {
        "team_name": os.getenv("TEAM_NAME", "Vera Rebuild"),
        "team_members": members or [os.getenv("TEAM_NAME", "Vera Rebuild")],
        "model": getattr(llm, "model", "template-only"),
        "approach": ("deterministic fact extraction + planner (angle, CTA, send_as, language) -> LLM phrasing "
                     "with number-grounding validator, retry and per-kind template fallback; rule-based reply "
                     "router for auto-reply, intent, opt-out and off-topic"),
        "contact_email": os.getenv("CONTACT_EMAIL", ""),
        "version": VERSION,
        "submitted_at": os.getenv("SUBMITTED_AT", "2026-09-27T00:00:00Z"),
    }


def create_app(llm=_UNSET, playground: bool | None = None) -> FastAPI:
    app = FastAPI(title="Vera bot", version=VERSION, docs_url="/docs", redoc_url="/redoc", openapi_url="/openapi.json")
    store = Store()
    started = time.time()
    model = llm_mod.from_env() if llm is _UNSET else llm

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError):
        if request.url.path == "/v1/reply":
            # Never leave the judge without a move; treat an unreadable reply as a pause.
            return JSONResponse({"action": "wait", "wait_seconds": BACKOFF_S, "rationale": "unreadable reply payload"})
        if request.url.path == "/v1/tick":
            return JSONResponse({"actions": []})
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_scope",
                                                      "details": str(exc.errors())[:500]})

    @app.get("/v1/healthz")
    async def healthz():
        return {"status": "ok", "uptime_seconds": int(time.time() - started), "contexts_loaded": store.counts()}

    @app.get("/v1/metadata")
    async def metadata():
        return _metadata(model)

    @app.post("/v1/context")
    async def context(body: ContextBody):
        code, out = store.put(body.scope, body.context_id, body.version, body.payload)
        return JSONResponse(status_code=code, content=out)

    @app.post("/v1/tick")
    async def tick(body: TickBody):
        now = now_or(body.now)
        try:
            actions = await run_in_threadpool(run_tick, store, body.available_triggers, now, model, TICK_BUDGET_S)
        except Exception:
            log.exception("tick failed")
            actions = []
        return {"actions": actions}

    @app.post("/v1/reply")
    async def reply(body: ReplyBody):
        now = now_or(body.received_at)
        try:
            return await run_in_threadpool(respond, store, body.conversation_id, body.merchant_id,
                                           body.customer_id, body.message, model, now,
                                           time.monotonic() + REPLY_BUDGET_S)
        except Exception:
            log.exception("reply failed")
            return {"action": "wait", "wait_seconds": BACKOFF_S, "rationale": "internal error; backing off briefly"}

    @app.post("/v1/teardown")
    async def teardown():
        store.reset()
        return {"status": "wiped"}

    if playground if playground is not None else os.getenv("PLAYGROUND", "true").lower() in ("1", "true", "yes"):
        from .playground import make_router, security_headers
        app.include_router(make_router(store, model))
        app.middleware("http")(security_headers)

    app.state.store = store
    return app


app = create_app()
