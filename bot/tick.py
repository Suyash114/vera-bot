"""Tick: choose which available triggers deserve a send, compose them within the
time budget, and record what was sent.

    available_triggers ─► select() ─► ≤20 candidates, one per recipient
                                         │ (pool of 8, overall budget)
                                         ▼
                              compose(llm) ── late? ──► compose(template)
                                         │
                              drop low-value ─► record sent_keys + conversations
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone

from .composer import compose
from .facts import build_facts, parse_dt
from .planner import is_low_value
from .store import Store

MAX_ACTIONS = 20
TICK_BUDGET_S = 10.0
_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="compose")
_FAR_FUTURE = datetime.max.replace(tzinfo=timezone.utc)


@dataclass
class Candidate:
    trigger: dict
    merchant: dict
    category: dict
    customer: dict | None


def _has_consent(customer: dict) -> bool:
    prefs = customer.get("preferences") or {}
    if prefs.get("reminder_opt_in") is False:
        return False
    consent = customer.get("consent")
    return not (isinstance(consent, dict) and "scope" in consent and not consent["scope"])


def send_key(merchant_id, customer_id, trigger: dict) -> str:
    """Dedup is per recipient: judge suppression keys can be category-wide ("research:dentists:2026-W17")."""
    skey = trigger.get("suppression_key") or f"{trigger.get('kind')}:{trigger.get('id')}"
    return f"{merchant_id}|{customer_id or '-'}|{skey}"


def _key(c: Candidate) -> str:
    return send_key(c.merchant.get("merchant_id"), (c.customer or {}).get("customer_id"), c.trigger)


def _rank(c: Candidate):
    t = c.trigger
    expires = parse_dt(t.get("expires_at")) or _FAR_FUTURE
    return (-int(t.get("urgency") or 1), expires, str(t.get("id")))


def select(store: Store, trigger_ids: list[str]) -> list[Candidate]:
    """`available_triggers` is the judge's statement of what is active now, so
    expiry only breaks ties; it never drops a listed trigger."""
    best: dict[tuple, Candidate] = {}
    for tid in dict.fromkeys(trigger_ids or []):
        trigger = store.get("trigger", tid)
        if not trigger:
            continue
        mid = trigger.get("merchant_id") or (trigger.get("payload") or {}).get("merchant_id")
        merchant = store.get("merchant", mid)
        if not merchant:
            continue
        cid = trigger.get("customer_id") or (trigger.get("payload") or {}).get("customer_id")
        customer = store.get("customer", cid)
        if (cid or trigger.get("scope") == "customer") and (not customer or not _has_consent(customer)):
            continue
        cid = cid if customer else None
        if store.is_suppressed(mid, cid) or send_key(mid, cid, trigger) in store.sent_keys:
            continue
        category = store.get("category", merchant.get("category_slug")) or {"slug": merchant.get("category_slug")}
        try:
            if is_low_value(trigger, build_facts(category, merchant, trigger, customer, datetime.now(timezone.utc))):
                continue
        except Exception:
            continue  # a context we can't read can't be composed either
        cand = Candidate(trigger, merchant, category, customer)
        recipient = (mid, cid)
        if recipient not in best or _rank(cand) < _rank(best[recipient]):
            best[recipient] = cand
    return sorted(best.values(), key=_rank)[:MAX_ACTIONS]


def run_tick(store: Store, trigger_ids: list[str], now: datetime, llm, budget_s: float = TICK_BUDGET_S) -> list[dict]:
    deadline = time.monotonic() + budget_s
    # Reserve each send before paying for composition, so overlapping ticks can't double-send.
    cands = [c for c in select(store, trigger_ids) if store.claim(_key(c))]
    args = [(c.category, c.merchant, c.trigger, c.customer) for c in cands]
    futures = [_POOL.submit(compose, *a, now=now, llm=llm, deadline=deadline) for a in args]
    wait(futures, timeout=max(0.0, deadline - time.monotonic()))
    for fut in futures:
        fut.cancel()  # queued work past the deadline would only be thrown away

    actions = []
    for cand, a, fut in zip(cands, args, futures, strict=True):
        # A compose still waiting on the LLM at the deadline gets the instant template.
        try:
            msg = fut.result() if fut.done() and not fut.cancelled() and not fut.exception() \
                else compose(*a, now=now, llm=None)
        except Exception:
            store.release(_key(cand))  # one unreadable context must not sink the whole tick
            continue
        if msg["low_value"] or store.has_conversation(msg["conversation_id"]):
            store.release(_key(cand))
            continue
        mid, cid = cand.merchant.get("merchant_id"), (cand.customer or {}).get("customer_id")
        store.open_conversation(msg["conversation_id"], mid, cid, cand.trigger.get("id"), msg["body"])
        actions.append({
            "conversation_id": msg["conversation_id"], "merchant_id": mid, "customer_id": cid,
            "send_as": msg["send_as"], "trigger_id": cand.trigger.get("id"),
            "template_name": msg["template_name"], "template_params": msg["template_params"],
            "body": msg["body"], "cta": msg["cta"], "suppression_key": msg["suppression_key"],
            "rationale": msg["rationale"],
        })
    return actions
