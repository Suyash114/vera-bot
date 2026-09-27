"""Phrasing: per-kind deterministic templates (the no-LLM / fallback path) and
the LLM prompt. Both read only the Plan and the contexts it was built from."""
from __future__ import annotations

import re

from .facts import (days_between, fmt_date, fmt_money, fmt_time, humanize, months_between, parse_dt, pct,
                    rel_days, resolve_digest, slot_labels)
from .planner import Plan
from .validator import taboo_list

# ---------------------------------------------------------------- helpers


def _p(ctx) -> dict:
    return (ctx["trigger"] or {}).get("payload") or {}


def _m(ctx) -> dict:
    return ctx["merchant"] or {}


def _offers(ctx) -> list[str]:
    return [o["title"] for o in _m(ctx).get("offers") or [] if o.get("status") == "active" and o.get("title")]


def _offer(ctx) -> str | None:
    offers = _offers(ctx)
    return offers[0] if offers else None


def _perf(ctx) -> dict:
    return _m(ctx).get("performance") or {}


def _agg(ctx, key):
    return (_m(ctx).get("customer_aggregate") or {}).get(key)


def _days_to(ctx, key) -> int | None:
    dt = parse_dt(_p(ctx).get(key))
    return days_between(ctx["now"], dt) if dt else None


def _when(days: int | None) -> str:
    return "soon" if days is None else rel_days(days)


def _num(v) -> str:
    return f"{v:,}" if isinstance(v, int) else str(v)


def _merchant_label(plan: Plan) -> str:
    return plan.merchant_name or "your clinic"


def _peer_line(ctx) -> str | None:
    perf, peer = _perf(ctx), (ctx["category"] or {}).get("peer_stats") or {}
    ctr, pctr = perf.get("ctr"), peer.get("avg_ctr")
    if isinstance(ctr, (int, float)) and isinstance(pctr, (int, float)):
        mine, theirs = pct(ctr, keep_decimal=True), pct(pctr, keep_decimal=True)
        if mine == theirs:
            return f"Your profile CTR is {mine}, level with the peer average"
        rel = "below" if ctr < pctr else "above"
        return f"Your profile CTR is {mine}, {rel} the {theirs} peer average"
    return None


def _cta(plan: Plan, action: str, question: str | None = None) -> str:
    """The single closing call-to-action, shaped by plan.cta and language."""
    if plan.cta == "open_ended":
        return question or f"Want me to {action}?"
    if plan.cta == "binary_confirm_cancel":
        return (f"Reply CONFIRM to {action} — ya CANCEL agar abhi nahi chahiye."
                if plan.hinglish else f"Reply CONFIRM to {action}, or CANCEL to skip.")
    if plan.cta == "none":
        return ""
    if plan.send_as == "merchant_on_behalf":
        return (f"{action[0].upper() + action[1:]}? YES reply kar dijiye." if plan.hinglish
                else f"Reply YES and we'll {action}.")
    return f"Want me to {action}? Bas YES bol dijiye." if plan.hinglish else f"Want me to {action}? Reply YES."


def _customer_open(plan: Plan) -> str:
    return (f"Namaste {plan.salutation}, {_merchant_label(plan)} se." if plan.hinglish
            else f"Hi {plan.salutation}, {_merchant_label(plan)} here.")


def _join(*parts: str | None) -> str:
    return " ".join(p.strip() for p in parts if p and p.strip())


_SENT_SPLIT = re.compile(r"(?<!\bDr)(?<!\bMr)(?<!\bMs)(?<!\bMrs)(?<!\bvs)(?<!\b[A-Z])\.\s+(?=[A-Z0-9])")


def _first_sentence(text: str | None) -> str:
    return _SENT_SPLIT.split((text or "").strip())[0]


def _nth_sentence(text: str | None, n: int) -> str:
    parts = _SENT_SPLIT.split((text or "").strip())
    return parts[n] if len(parts) > n else ""


def _sentence(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip()
    return s if s.endswith((".", "!", "?")) else s + "."


# ---------------------------------------------------------------- merchant-facing kinds


def t_research(plan, ctx):
    d = resolve_digest(ctx["category"] or {}, ctx["trigger"] or {}) or {}
    seg = humanize(d.get("patient_segment", "")) if d.get("patient_segment") else None
    if seg:  # "high_risk_adults" -> "high-risk adult", so it reads "... adult patients"
        seg = seg.replace("high risk", "high-risk").removesuffix("s")
    cohort = _agg(ctx, "high_risk_adult_count") if seg and "high-risk" in seg else None
    rel = (f"relevant to your {cohort} {seg} patients" if cohort else
           f"relevant to your {seg} patients" if seg else "worth a look for your practice")
    trial = f" — {int(d['trial_n']):,}-patient trial" if d.get("trial_n") else ""
    summary = _first_sentence(d.get("summary"))
    src = f" ({d['source']})" if d.get("source") else ""
    return _join(f"{plan.salutation}, this week's digest has one item {rel}: {d.get('title', 'new research')}{trial}{src}.",
                 _sentence(summary), _cta(plan, "pull the abstract and draft a patient-education WhatsApp you can share"))


def t_regulation(plan, ctx):
    d = resolve_digest(ctx["category"] or {}, ctx["trigger"] or {}) or {}
    days = _days_to(ctx, "deadline_iso")
    deadline = f" — effective {fmt_date(_p(ctx)['deadline_iso'])} ({_when(days)})" if days is not None else ""
    src = f" ({d['source']})" if d.get("source") else ""
    title = re.sub(r"\s*effective \d{4}-\d{2}-\d{2}", "", d.get("title", "a regulation changed"))
    return _join(f"{plan.salutation}, compliance heads-up: {title}{deadline}{src}.",
                 _sentence(_first_sentence(d.get("summary"))),
                 _sentence(d.get("actionable")), _cta(plan, "send a short audit checklist for your setup"))


def t_cde(plan, ctx):
    d = resolve_digest(ctx["category"] or {}, ctx["trigger"] or {}) or {}
    p = _p(ctx)
    when = f" on {fmt_date(d['date'])}" if d.get("date") else ""
    credits = f" — {p['credits']} CDE credits" if p.get("credits") else ""
    fee = f", {humanize(p['fee'])}" if p.get("fee") else ""
    return _join(f"{plan.salutation}, {d.get('title', 'a CDE session')}{when}{credits}{fee}.",
                 _sentence(_first_sentence(d.get("summary"))),
                 _cta(plan, "block it on your calendar and send the registration link"))


def t_supply(plan, ctx):
    d = resolve_digest(ctx["category"] or {}, ctx["trigger"] or {}) or {}
    p = _p(ctx)
    batches = ", ".join(p.get("affected_batches") or [])
    mol = p.get("molecule", "")
    who = f" by {p['manufacturer']}" if p.get("manufacturer") else ""
    rx = _agg(ctx, "chronic_rx_count")
    return _join(f"{plan.salutation}, urgent: recall on {mol} batches {batches}{who}.".replace("  ", " "),
                 _sentence(_nth_sentence(d.get("summary"), 1) or _first_sentence(d.get("summary"))),
                 f"You have {rx:,} chronic-Rx customers on file who may need a replacement." if isinstance(rx, int) else None,
                 _cta(plan, "draft the customer WhatsApp and a replacement-pickup note"))


def t_perf_dip(plan, ctx):
    p = _p(ctx)
    if not isinstance(p.get("delta_pct"), (int, float)):
        return ""
    metric = humanize(p.get("metric", "calls"))
    drop = f"dropped {pct(abs(p['delta_pct']))}"
    base = f" against your usual {p['vs_baseline']}" if p.get("vs_baseline") else ""
    offer = _offer(ctx)
    return _join(f"{plan.salutation}, your {metric} {drop} in the last {p.get('window', '7d').replace('d', ' days')}{base}.",
                 _sentence(_peer_line(ctx)),
                 f"Your {offer} is live but not being seen." if offer else None,
                 _cta(plan, f"publish a fresh Google post featuring {offer} today" if offer
                      else "publish a fresh Google post today to win back visibility"))


def t_perf_spike(plan, ctx):
    p = _p(ctx)
    if not isinstance(p.get("delta_pct"), (int, float)):
        return ""
    metric = humanize(p.get("metric", "calls"))
    up = f"up {pct(abs(p['delta_pct']))}"
    driver = f" — looks driven by your {humanize(p['likely_driver'])}" if p.get("likely_driver") else ""
    return _join(f"{plan.salutation}, nice momentum: {metric} {up} over the last {p.get('window', '7d').replace('d', ' days')}{driver}.",
                 "The fastest way to hold it is to repeat what worked while it's fresh.",
                 _cta(plan, "draft 2 more posts in the same style for this week"))


def t_seasonal_dip(plan, ctx):
    p = _p(ctx)
    delta = p.get("delta_pct")
    note = humanize(p.get("season_note", "seasonal")).replace("apr jun", "April-June")
    members = _agg(ctx, "total_active_members")
    return _join(f"{plan.salutation}, your {humanize(p.get('metric', 'views'))} are down {pct(abs(delta)) if isinstance(delta, (int, float)) else ''} this week"
                 f" — that's the expected {note} lull, not something you did.",
                 "Best use of this window: hold ad spend and focus on retention"
                 + (f" of your {members:,} active members." if isinstance(members, int) else "."),
                 _cta(plan, "draft a 4-week attendance challenge to keep members through the dip"))


def t_renewal(plan, ctx):
    p, perf = _p(ctx), _perf(ctx)
    amount = f" ({fmt_money(p['renewal_amount'])})" if p.get("renewal_amount") else ""
    stats = [f"{_num(perf[k])} {k}" for k in ("views", "calls", "leads") if isinstance(perf.get(k), int)]
    return _join(f"{plan.salutation}, your {p.get('plan', 'magicpin')} plan renews in {p.get('days_remaining', 'a few')} days{amount}.",
                 f"Last {perf.get('window_days', 30)} days it brought you {', '.join(stats)}." if stats else None,
                 _cta(plan, "renew it now so your listing stays live without a gap"))


def t_winback(plan, ctx):
    p = _p(ctx)
    dip = p.get("perf_dip_pct")
    lapsed = p.get("lapsed_customers_added_since_expiry")
    offer = _offer(ctx)
    return _join(f"{plan.salutation}, it's been {p.get('days_since_expiry', 'a while')} days since your plan lapsed"
                 + (f" — visibility is down {pct(abs(dip))}" if isinstance(dip, (int, float)) else "")
                 + (f" and {lapsed} more customers have gone quiet" if lapsed else "") + ".",
                 _cta(plan, f"reactivate and win them back with your {offer}" if offer
                      else "reactivate your listing and win those customers back"))


def t_dormant(plan, ctx):
    p = _p(ctx)
    if not p.get("days_since_last_merchant_message"):
        return ""
    last = f" we last spoke about {humanize(p['last_topic'])}" if p.get("last_topic") else ""
    return _join(f"{plan.salutation}, it's been {p.get('days_since_last_merchant_message', 'a few')} days since{last}.",
                 _sentence(_peer_line(ctx)),
                 _cta(plan, "send a 2-minute summary of what changed on your profile and one quick fix"))


def t_festival(plan, ctx):
    p = _p(ctx)
    if not p.get("festival"):
        return ""
    name = p.get("festival", "the festival")
    days = _days_to(ctx, "date")
    offer = _offer(ctx)
    return _join(f"{plan.salutation}, {name} is {_when(days)}" + (f" ({fmt_date(p['date'])})" if p.get("date") else "") + ".",
                 "Festival searches start climbing well before the day, so listings that post early get seen first.",
                 _cta(plan, f"draft a {name} post featuring your {offer}" if offer else f"draft a {name} post and offer for you"))


def t_ipl(plan, ctx):
    p = _p(ctx)
    t = parse_dt(p.get("match_time_iso"))
    time = fmt_time(t) if t else "tonight"
    offer = _offer(ctx)
    advice = ("Weekday match nights pull dine-in crowds after work." if p.get("is_weeknight")
              else "On a weekend match most fans watch at home, so delivery beats a dine-in promo.")
    return _join(f"{plan.salutation}, {p.get('match', 'the match')} at {p.get('venue', 'the stadium')} today, {time}.",
                 advice, f"Your {offer} already fits." if offer else None,
                 _cta(plan, "draft a match-time delivery post and a story for today"))


def t_review(plan, ctx):
    p = _p(ctx)
    quote = f' — e.g. "{p["common_quote"]}"' if p.get("common_quote") else ""
    trend = f" and {p['trend']}" if p.get("trend") else ""
    return _join(f"{plan.salutation}, {p.get('occurrences_30d', 'several')} reviews in the last 30 days mention "
                 f"{humanize(p.get('theme', 'the same issue'))}{trend}{quote}.",
                 "Unanswered, a theme like this starts to show up in your rating.",
                 _cta(plan, "draft a polite public reply plus a one-line fix you can announce"))


def t_milestone(plan, ctx):
    p = _p(ctx)
    if not (p.get("value_now") and p.get("milestone_value")):
        return ""
    metric = humanize(p.get("metric", "reviews")).replace("review count", "reviews")
    now_v, goal = p.get("value_now"), p.get("milestone_value")
    return _join(f"{plan.salutation}, you're at {now_v} {metric} — very close to {goal}.",
                 "A short thank-you ask to recent happy customers usually gets you over the line this week.",
                 _cta(plan, "send that review request to your recent customers"))


def t_planning(plan, ctx):
    p = _p(ctx)
    topic = humanize(p.get("intent_topic", "your idea"))
    said = (f' You asked: "{p["merchant_last_message"].rstrip("?!. ").replace("?", "")}".'
            if p.get("merchant_last_message") else "")
    offer = _offer(ctx)
    lines = [f"{plan.salutation}, here's a first draft for the {topic} — edit anything.{said}",
             f"• Anchor it on your {offer} so the price is familiar" if offer else "• One simple starting price",
             "• A clear who-it's-for line and a fixed weekly slot",
             "• A WhatsApp enquiry line so bookings come straight to you"]
    return "\n".join(lines) + "\n" + _cta(plan, "", "Which of these should I tighten first, pricing or the schedule?")


def t_curious(plan, ctx):
    return _join(f"{plan.salutation}, quick one — which service has been asked for most at {_merchant_label(plan)} this week?",
                 "I'll turn your answer into a Google post and a ready WhatsApp reply for price enquiries. Takes 5 min.")


def t_unverified(plan, ctx):
    p = _p(ctx)
    uplift = p.get("estimated_uplift_pct")
    path = humanize(p["verification_path"]).replace(" or ", " or a ") if p.get("verification_path") else None
    return _join(f"{plan.salutation}, your Google profile is still unverified"
                 + (f" — verified listings see roughly {pct(uplift)} more visibility" if isinstance(uplift, (int, float)) else "") + ".",
                 f"Verification is by {path}." if path else None,
                 _cta(plan, "start the verification for you now"))


def t_competitor(plan, ctx):
    p = _p(ctx)
    if not p.get("competitor_name"):
        return ""
    dist = f" {p['distance_km']} km away" if p.get("distance_km") else " nearby"
    when = f" on {fmt_date(p['opened_date'])}" if p.get("opened_date") else ""
    theirs = f", leading with {p['their_offer']}" if p.get("their_offer") else ""
    offer = _offer(ctx)
    return _join(f"{plan.salutation}, {p.get('competitor_name', 'a new competitor')} opened{dist}{when}{theirs}.",
                 f"Your {offer} is live — the difference now is how visible you are." if offer else None,
                 _cta(plan, "refresh your listing and highlight what sets you apart this week"))


def t_category_seasonal(plan, ctx):
    p = _p(ctx)
    trends = []
    for tr in p.get("trends") or []:
        name, _, delta = str(tr).rpartition("_")
        trends.append(f"{name.replace('_demand', '').replace('_', ' ')} {delta}%")
    return _join(f"{plan.salutation}, {humanize(p.get('season', 'seasonal'))} demand is shifting: {', '.join(trends)}.",
                 _cta(plan, "draft a shelf-and-WhatsApp plan for the top movers"))


# ---------------------------------------------------------------- customer-facing kinds


def _slots(ctx) -> list[str]:
    return slot_labels(_p(ctx))


def _slot_cta(plan, ctx, action: str) -> str:
    slots = _slots(ctx)
    if len(slots) >= 2:
        return (f"Reply 1 for {slots[0]} ya 2 for {slots[1]} — ya jo time suit kare, bata dijiye." if plan.hinglish
                else f"Reply 1 for {slots[0]}, 2 for {slots[1]}, or tell us a time that suits you.")
    if slots:
        return (f"{slots[0]} book kar dein? YES reply kar dijiye." if plan.hinglish
                else f"Reply YES to book {slots[0]}.")
    return _cta(plan, action)


def _months_since_last(ctx) -> int | None:
    last = parse_dt(((ctx["customer"] or {}).get("relationship") or {}).get("last_visit"))
    return months_between(last, ctx["now"]) if last else None


def t_recall(plan, ctx):
    p = _p(ctx)
    months = _months_since_last(ctx)
    service = humanize(p.get("service_due", "check-up")).replace("6 month", "6-month")
    offer = _offer(ctx)
    unit = "month" if months == 1 else "months"
    since = (f"It's been about {months} {unit} since your last visit, so your {service} is due." if months
             else f"Your {service} is due.")
    return _join(_customer_open(plan), since, f"{offer}." if offer else None, _slot_cta(plan, ctx, "book your visit"))


def t_appointment(plan, ctx):
    return _join(_customer_open(plan), "Just a reminder that your appointment is tomorrow.",
                 "Reply YES to confirm, or tell us if you'd like a different time.")


def t_lapsed_soft(plan, ctx):
    rel = (ctx["customer"] or {}).get("relationship") or {}
    last = f" (your last visit was {fmt_date(rel['last_visit'])})" if rel.get("last_visit") else ""
    offer = _offer(ctx)
    return _join(_customer_open(plan), f"It's been a little while{last} — hope all is well.",
                 f"{offer} is on this month." if offer else None, _cta(plan, "hold a slot for you this week"))


def t_lapsed_hard(plan, ctx):
    p = _p(ctx)
    if not p.get("days_since_last_visit"):
        return ""
    focus = f" We'd love to help you pick your {humanize(p['previous_focus'])} goal back up." if p.get("previous_focus") else ""
    offer = _offer(ctx)
    return _join(_customer_open(plan), f"It's been {p.get('days_since_last_visit', 'a few')} days — happens to everyone, no pressure.{focus}",
                 f"{offer} is available if you'd like to restart." if offer else None,
                 _cta(plan, "hold a spot for you this week — no commitment"))


def t_trial_followup(plan, ctx):
    p = _p(ctx)
    when = f" on {fmt_date(p['trial_date'])}" if p.get("trial_date") else ""
    return _join(_customer_open(plan), f"Thanks for joining the trial session{when}.",
                 _slot_cta(plan, ctx, "book the next session"))


def t_refill(plan, ctx):
    p = _p(ctx)
    if not p.get("molecule_list"):
        return ""
    meds = ", ".join(p.get("molecule_list") or []) or "regular medicines"
    days = _days_to(ctx, "stock_runs_out_iso")
    when = fmt_date(p["stock_runs_out_iso"]).split(",")[0] if p.get("stock_runs_out_iso") else "soon"
    delivery = next((o for o in _offers(ctx) if "deliver" in o.lower()), None)
    addr = " to your saved address" if p.get("delivery_address_saved") else ""
    line = (f"Aapki medicines ({meds}) {when} ko khatam hongi ({_when(days)})." if plan.hinglish
            else f"Your medicines ({meds}) run out on {when} ({_when(days)}).")
    return _join(_customer_open(plan), line, "Same pack is ready" + (f" — {delivery}." if delivery else "."),
                 _cta(plan, f"dispatch the refill{addr}"))


def t_wedding(plan, ctx):
    p = _p(ctx)
    days = _days_to(ctx, "wedding_date")
    step = humanize(p.get("next_step_window_open", "next step")).replace("30day", "30-day")
    date = f" ({fmt_date(p['wedding_date'])})" if p.get("wedding_date") else ""
    return _join(_customer_open(plan), f"{days} days to your wedding{date} — this is the right window to start the {step}." if days
                 else f"This is the right window to start the {step}.",
                 _cta(plan, "book your first session next week"))


# ---------------------------------------------------------------- generic (unseen kinds)


_KIND_PHRASES = {
    "perf_dip": "a dip in your profile performance", "perf_spike": "a lift in your profile performance",
    "competitor_opened": "a new competitor near you", "festival_upcoming": "an upcoming festival window",
    "milestone_reached": "a milestone you're close to", "dormant_with_vera": "a quick check-in",
    "chronic_refill_due": "your upcoming refill", "customer_lapsed_hard": "your membership",
}


def t_generic(plan, ctx):
    facts = [f.text for f in plan.chosen[:2]]
    why = _KIND_PHRASES.get(plan.kind, humanize(plan.kind))
    if plan.send_as == "merchant_on_behalf":
        visible = [f.text for f in plan.chosen if not f.key.startswith("customer_")][:2]
        return _join(_customer_open(plan), f"A quick note about {why}.", *(_sentence(f) for f in visible),
                     _cta(plan, "set this up for you"))
    return _join(f"{plan.salutation}, flagging {why} for {_merchant_label(plan)}.", *(_sentence(f) for f in facts),
                 _cta(plan, "handle the next step for you"))


KIND_TEMPLATES = {
    "research_digest": t_research, "regulation_change": t_regulation, "cde_opportunity": t_cde,
    "supply_alert": t_supply, "perf_dip": t_perf_dip, "perf_spike": t_perf_spike,
    "seasonal_perf_dip": t_seasonal_dip, "renewal_due": t_renewal, "winback_eligible": t_winback,
    "dormant_with_vera": t_dormant, "festival_upcoming": t_festival, "ipl_match_today": t_ipl,
    "review_theme_emerged": t_review, "milestone_reached": t_milestone, "active_planning_intent": t_planning,
    "curious_ask_due": t_curious, "gbp_unverified": t_unverified, "competitor_opened": t_competitor,
    "category_seasonal": t_category_seasonal, "recall_due": t_recall, "appointment_tomorrow": t_appointment,
    "customer_lapsed_soft": t_lapsed_soft, "customer_lapsed_hard": t_lapsed_hard, "trial_followup": t_trial_followup,
    "chronic_refill_due": t_refill, "wedding_package_followup": t_wedding,
}


def render_template(plan: Plan, ctx: dict) -> str:
    fn = KIND_TEMPLATES.get(plan.kind, t_generic)
    try:
        body = fn(plan, ctx)
    except (KeyError, TypeError, ValueError, AttributeError, IndexError):
        body = ""
    return body.strip() or t_generic(plan, ctx).strip()


# ---------------------------------------------------------------- LLM prompt

_VOICE_HINTS = {
    "dentists": "clinical peer-to-peer; technical vocabulary welcome; no hype; cite sources",
    "salons": "warm, practical fellow-operator",
    "restaurants": "operator-to-operator (covers, AOV, delivery, footfall)",
    "gyms": "coach-to-operator; motivating but evidence-based; no shaming",
    "pharmacies": "trustworthy and precise; no alarm; exact molecule/batch names",
}

SYSTEM_PROMPT = """You write one WhatsApp message for Vera, magicpin's assistant for Indian local merchants.

Hard rules:
- Never invent facts. Use ONLY the facts listed. Every number, date, price, name and source you write must appear in them. If a fact isn't listed, leave it out.
- Open with the salutation given. No preamble ("hope you're well"), no self-introduction.
- Lead with the reason-for-now and the single most compelling fact. Two or three facts at most — pick, don't list.
- Exactly one call-to-action, in the final sentence, matching the CTA type. At most one question mark.
- Use service+price offers exactly as written (e.g. "Dental Cleaning @ ₹299"); never invent discounts.
- Match the voice. Never use the taboo words.
- If told to write Hinglish, use natural Hindi-English code-mix in Roman script; keep technical terms in English.
- Keep it under 90 words. Output only the message text."""

_CTA_HINTS = {
    "binary_yes_no": "a yes/no offer to do the next step for them (e.g. ends with 'Reply YES')",
    "binary_confirm_cancel": "ask them to reply CONFIRM (or CANCEL)",
    "multi_choice_slot": "offer the listed slots as 'Reply 1 for …, 2 for …' plus 'or tell us a time'",
    "open_ended": "one low-effort open question",
    "none": "no call-to-action",
}


def build_prompt(plan: Plan) -> tuple[str, str]:
    voice = plan.voice or {}
    taboos = ", ".join(taboo_list({"voice": voice})) or "none"
    tone = _VOICE_HINTS.get(plan.category_slug, str(voice.get("tone", "peer")).replace("_", " "))
    sender = (f"the merchant '{plan.merchant_name}' writing to their customer {plan.customer_name}"
              if plan.send_as == "merchant_on_behalf" else f"Vera writing to the merchant ({plan.merchant_name})")
    facts = "\n".join(f"- {f.text}" for f in [plan.facts[0]] + plan.chosen)
    user = (f"Sender: {sender}\n"
            f"Salutation: {plan.salutation}\n"
            f"Trigger: {humanize(plan.kind)} (urgency {plan.urgency}/5)\n"
            f"Angle: {plan.angle.replace('_', ' ')}\n"
            f"CTA type: {_CTA_HINTS.get(plan.cta, plan.cta)}\n"
            f"Language: {'Hinglish (Hindi-English code-mix)' if plan.hinglish else 'English'}\n"
            f"Voice: {tone}. Taboo words: {taboos}\n"
            f"Facts (the only facts you may use):\n{facts}")
    return SYSTEM_PROMPT, user
