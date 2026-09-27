"""Local developer playground: a browser UI over the same bot the judge talks to.

Off unless PLAYGROUND=1 (it can wipe state, so it must never be live on a public
judge deployment). Same-origin only, rate-limited, strict CSP on every page asset.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .facts import build_facts, humanize, now_or
from .planner import fact_source, make_plan
from .store import Store
from .tick import run_tick, send_key

STATIC_DIR = Path(__file__).resolve().parent / "static"
DATA_DIR = Path(__file__).resolve().parent.parent / "expanded"
DEFAULT_NOW = "2026-04-26T10:30:00Z"  # the dataset's week, so "in N days" reads sensibly

CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'none'; "
       "form-action 'self'; frame-ancestors 'none'")
SECURITY_HEADERS = {"Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff",
                    "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"}


class RateLimiter:
    """Sliding-window limit per bucket; the playground is single-user, so limits are tight."""

    def __init__(self, limit: int, window_s: float):
        self.limit, self.window = limit, window_s
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = time.monotonic()
        with self._lock:
            while self._hits and now - self._hits[0] > self.window:
                self._hits.popleft()
            if len(self._hits) >= self.limit:
                return False
            self._hits.append(now)
            return True


class SendBody(BaseModel):
    trigger_id: str
    now: str | None = None


def load_dataset(store: Store, data_dir: Path = DATA_DIR) -> int:
    loaded = 0
    for scope, folder, key in (("category", "categories", "slug"), ("merchant", "merchants", "merchant_id"),
                               ("customer", "customers", "customer_id"), ("trigger", "triggers", "id")):
        for path in sorted((data_dir / folder).glob("*.json")):
            payload = json.loads(path.read_text())
            if payload.get(key):
                store.put(scope, payload[key], 1, payload)
                loaded += 1
    return loaded


_ACRONYMS = {"cde": "CDE", "ipl": "IPL", "gbp": "GBP", "ctr": "CTR", "sms": "SMS"}


def trigger_label(kind: str) -> str:
    words = [_ACRONYMS.get(w, w) for w in humanize(kind or "update").split()]
    first = words[0] if words[0].isupper() else words[0].capitalize()
    return " ".join([first] + words[1:])


def _writer_from(rationale: str) -> str:
    return rationale.rsplit("Writer: ", 1)[-1].rstrip(".") if "Writer: " in rationale else "unknown"


def make_router(store: Store, llm) -> APIRouter:
    router = APIRouter()
    try:
        load_dataset(store)
    except Exception:
        pass
    api_limit, reset_limit = RateLimiter(120, 10), RateLimiter(5, 10)

    def limited(limiter: RateLimiter):
        if not limiter.allow():
            raise HTTPException(status_code=429, detail="rate_limited")

    @router.get("/", include_in_schema=False)
    def page():
        return FileResponse(STATIC_DIR / "playground.html", headers=SECURITY_HEADERS)

    @router.get("/playground/static/{name}", include_in_schema=False)
    def static(name: str):
        allowed = {"playground.js": "text/javascript", "playground.css": "text/css"}
        if name not in allowed:  # fixed allowlist: no path traversal, no directory listing
            raise HTTPException(status_code=404)
        return FileResponse(STATIC_DIR / name, media_type=allowed[name], headers=SECURITY_HEADERS)

    @router.get("/playground/api/info")
    def info():
        limited(api_limit)
        model = getattr(llm, "model", "template-only")
        return {"mode": "template" if getattr(llm, "provider", "none") == "none" else "llm",
                "provider": getattr(llm, "provider", "none"), "model": model,
                "default_now": DEFAULT_NOW, "contexts_loaded": store.counts()}

    @router.post("/playground/api/reset")
    def reset():
        limited(reset_limit)
        store.reset()
        return {"loaded": load_dataset(store), "contexts_loaded": store.counts()}

    @router.get("/playground/api/catalog")
    def catalog():
        limited(api_limit)
        customers = {c.get("customer_id"): c for c in store.items("customer")}
        by_merchant: dict[str, list] = {}
        for t in store.items("trigger"):
            mid = t.get("merchant_id") or (t.get("payload") or {}).get("merchant_id")
            cust = customers.get(t.get("customer_id"))
            cname = ((cust or {}).get("identity") or {}).get("name")
            by_merchant.setdefault(mid, []).append({
                "id": t.get("id"), "kind": t.get("kind"), "label": trigger_label(str(t.get("kind", "update"))),
                "urgency": int(t.get("urgency") or 1), "scope": "customer" if t.get("customer_id") else "merchant",
                "customer_name": cname, "source": t.get("source")})
        merchants = []
        for m in store.items("merchant"):
            ident = m.get("identity") or {}
            trigs = sorted(by_merchant.get(m.get("merchant_id"), []), key=lambda x: (-x["urgency"], x["id"]))
            merchants.append({"merchant_id": m.get("merchant_id"), "name": ident.get("name") or m.get("merchant_id"),
                              "owner": ident.get("owner_first_name"), "category": m.get("category_slug"),
                              "locality": ident.get("locality"), "city": ident.get("city"),
                              "languages": ident.get("languages") or [], "triggers": trigs})
        merchants.sort(key=lambda x: (x["category"] or "", x["name"]))
        return {"merchants": merchants}

    @router.post("/playground/api/send")
    def send(body: SendBody):
        limited(api_limit)
        trigger = store.get("trigger", body.trigger_id)
        if not trigger:
            raise HTTPException(status_code=404, detail="unknown trigger")
        mid = trigger.get("merchant_id") or (trigger.get("payload") or {}).get("merchant_id")
        merchant = store.get("merchant", mid) or {}
        cid = trigger.get("customer_id")
        customer = store.get("customer", cid)
        category = store.get("category", merchant.get("category_slug")) or {}
        now = now_or(body.now or DEFAULT_NOW)
        # The playground may resend a trigger: clear only this trigger's own send record.
        store.release(send_key(mid, cid if customer else None, trigger))
        store.forget_conversation(f"conv_{mid}_{trigger.get('id')}")
        actions = run_tick(store, [trigger["id"]], now, llm)
        facts = build_facts(category, merchant, trigger, customer, now)
        plan = make_plan(category, merchant, trigger, customer, facts)
        used = {f.key for f in plan.chosen}
        explained = [{"key": f.key, "text": f.text, "source": fact_source(f.key), "used": f.key in used}
                     for f in facts if f.key != "why_now"]
        if not actions:
            reason = "opted_out" if store.is_suppressed(mid, cid) else \
                "low_value" if plan.low_value else "skipped"
            return {"action": None, "reason": reason, "facts": explained, "angle": plan.angle}
        a = actions[0]
        return {"action": a, "writer": _writer_from(a["rationale"]), "angle": plan.angle,
                "language": "Hinglish" if plan.hinglish else "English", "facts": explained}

    return router


async def security_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/playground/"):
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
    return response
