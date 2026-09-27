"""compose(category, merchant, trigger, customer?) -> message dict.

    facts ─► plan ─► LLM draft ─► validate ─┬─ ok ──────────────────────► send
                        │                   └─ bad ─► retry w/ problems ─┬─ ok ─► send
                        │ (no LLM / no time)                            └─ bad ─► strip bad sentences
                        ▼                                                          └─ nothing left ─► template
                     template
"""
from __future__ import annotations

import time
from datetime import datetime

from .facts import allowed_numbers, build_facts, now_or
from .planner import make_plan
from .validator import strip_violations, taboo_list, validate
from .writer import build_prompt, render_template

MIN_LLM_BUDGET_S = 1.0
MIN_RETRY_BUDGET_S = 4.0


def _remaining(deadline: float | None) -> float:
    return float("inf") if deadline is None else deadline - time.monotonic()


def _clean(text: str | None) -> str:
    t = (text or "").strip()
    if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
        t = t[1:-1].strip()
    return t


def compose(category: dict, merchant: dict, trigger: dict, customer: dict | None = None, *,
            now: datetime | None = None, llm=None, prior_bodies=(), deadline: float | None = None) -> dict:
    now = now_or(now)
    facts = build_facts(category, merchant, trigger, customer, now)
    plan = make_plan(category, merchant, trigger, customer, facts)
    allowed = allowed_numbers(category, merchant, trigger, customer, facts)
    taboos = taboo_list(category)
    prior = list(prior_bodies)
    check = lambda b: validate(b, allowed=allowed, taboos=taboos, prior=prior)

    body, source = None, "template"
    if llm is not None and _remaining(deadline) > MIN_LLM_BUDGET_S:
        system, user = build_prompt(plan)
        draft = _clean(llm.complete(system, user))
        problems = check(draft) if draft else ["empty"]
        if problems and draft and _remaining(deadline) > MIN_RETRY_BUDGET_S:
            fix = (f"{user}\n\nYour previous draft broke these rules: {', '.join(problems)}. "
                   f"Previous draft: {draft}\nRewrite it using only the listed facts.")
            second = _clean(llm.complete(system, fix))
            if second:
                draft, problems = second, check(second)
        if draft and not problems:
            body, source = draft, "llm"
        elif draft:
            stripped = strip_violations(draft, allowed=allowed, taboos=taboos)
            if stripped and not check(stripped) and len(stripped) >= 40:
                body, source = stripped, "llm_stripped"

    if body is None:
        body = render_template(plan, {"category": category, "merchant": merchant, "trigger": trigger,
                                      "customer": customer, "now": now})
        if "repeat" in check(body):
            body = f"{body} (Following up on my last note.)"

    return {
        "body": body,
        "cta": plan.cta,
        "send_as": plan.send_as,
        "suppression_key": plan.suppression_key,
        "rationale": f"{plan.rationale} Writer: {source}.",
        "template_name": plan.template_name,
        "template_params": plan.template_params,
        "conversation_id": plan.conversation_id,
        "source": source,
        "low_value": plan.low_value,
    }
