"""Turn the four contexts into a ranked list of grounded, human-readable facts.

Every sentence the bot sends is built from these facts, and the validator only
accepts numbers that can be traced back to the contexts. Nothing here invents
data: a fact exists only if its source field exists.

    category ─┐
    merchant ─┼─► build_facts(now) ─► [why_now, digest, customer, dates, payload,
    trigger  ─┤                         peer gaps, offers, aggregates, reviews, ...]
    customer ─┘                         sorted by weight (kind-aware boosts)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?")

# Payload keys that are plumbing, not facts a merchant should read.
_SKIP_KEYS = {"category", "placeholder", "metric_or_topic", "top_item_id", "digest_item_id",
              "alert_id", "last_ask_at", "top_item", "merchant_id", "customer_id"}
_DATE_KEYS = ("date", "deadline_iso", "due_date", "wedding_date", "match_time_iso",
              "stock_runs_out_iso", "trial_date", "opened_date", "last_service_date",
              "last_refill", "trial_completed")

_LABELS = {
    "total_unique_ytd": "unique customers this year",
    "lapsed_180d_plus": "customers lapsed 180+ days",
    "lapsed_90d_plus": "customers lapsed 90+ days",
    "retention_6mo_pct": "6-month retention",
    "retention_3mo_pct": "3-month retention",
    "high_risk_adult_count": "high-risk adult patients",
    "repeat_customer_pct": "repeat customers",
    "total_active_members": "active members",
    "monthly_churn_pct": "monthly churn",
    "trial_to_paid_pct": "trial-to-paid conversion",
    "chronic_rx_count": "chronic-Rx customers",
    "delivery_share_pct": "delivery share of orders",
    "delivery_orders_30d": "delivery orders (30d)",
    "dine_in_orders_30d": "dine-in orders (30d)",
    "vs_baseline": "baseline",
    "delta_pct": "change",
    "occurrences_30d": "mentions in 30 days",
    "days_remaining": "days remaining",
    "renewal_amount": "renewal amount",
    "distance_km": "distance (km)",
    "estimated_uplift_pct": "estimated uplift",
    "perf_dip_pct": "performance change",
}

# Which fact families matter most for which trigger families (substring match on kind).
# Fields whose fractional values are rates (0.021 -> 2.1%); any other float stays as written.
_PCT_HINTS = ("pct", "delta", "ctr", "rate", "share", "uplift", "retention", "churn", "yoy", "conversion")


def _is_pct_key(key: str) -> bool:
    return any(h in (key or "").lower() for h in _PCT_HINTS)


_KIND_BOOSTS = {
    "perf": ("peer_", "delta_"), "dip": ("peer_", "delta_"), "spike": ("delta_", "peer_"),
    "review": ("review_",), "renewal": ("subscription", "peer_"), "winback": ("subscription", "aggregate_"),
    "dormant": ("history", "peer_"), "competitor": ("offer_", "peer_"), "milestone": ("peer_",),
    "curious": ("history", "offer_"), "planning": ("history", "offer_"), "unverified": ("peer_",),
    "festival": ("offer_",), "ipl": ("offer_",), "seasonal": ("delta_", "aggregate_"),
    "research": ("aggregate_", "peer_"), "regulation": ("aggregate_",), "supply": ("aggregate_",),
    "cde": ("aggregate_",),
}


@dataclass(frozen=True)
class Fact:
    key: str
    text: str
    weight: int
    data: dict = field(default_factory=dict, compare=False, hash=False)


# ---------------------------------------------------------------- helpers
def _norm_num(tok: str) -> str:
    tok = tok.replace(",", "")
    whole, _, frac = tok.partition(".")
    whole = whole.lstrip("0") or "0"
    frac = frac.rstrip("0")
    return f"{whole}.{frac}" if frac else whole


def extract_numbers(text: str) -> set[str]:
    return {_norm_num(t) for t in _NUM_RE.findall(text or "")}


def humanize(key: str) -> str:
    return _LABELS.get(key, key.replace("_", " ").strip())


def pct(v: float, signed: bool = False, keep_decimal: bool = False) -> str:
    """0.021 -> '2.1%', -0.5 -> '-50%' (signed), 0.03 -> '3.0%' with keep_decimal."""
    s = f"{v * 100:+.1f}" if signed else f"{v * 100:.1f}"
    if s.endswith(".0") and not keep_decimal:
        s = s[:-2]
    return s + "%"


def fmt_money(v) -> str:
    try:
        return f"₹{int(float(v)):,}"
    except (TypeError, ValueError):
        return str(v)


def _humanize_value(key: str, v) -> str | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, float) and _is_pct_key(key):
        return pct(v, signed="delta" in key or "dip" in key)
    if isinstance(v, (int, float)):
        return fmt_money(v) if "amount" in key or "price" in key else f"{v:,}" if isinstance(v, int) else str(v)
    if isinstance(v, list):
        parts = [_humanize_value(key, x) for x in v]
        parts = [p for p in parts if p]
        return ", ".join(parts) if parts else None
    if isinstance(v, dict):
        return v.get("label") or v.get("title") or None
    s = str(v).strip()
    if not s:
        return None
    if _ISO_DATE_RE.match(s):
        return fmt_date(s)
    return s.replace("_", " ")


def parse_dt(s) -> datetime | None:
    if not s or not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fmt_date(s: str) -> str:
    dt = parse_dt(s)
    if not dt:
        return s
    out = f"{dt.day} {dt.strftime('%b')} {dt.year}"
    if "T" in s and (dt.hour or dt.minute):
        out += f", {fmt_time(dt)}"
    return out


def days_between(now: datetime, target: datetime) -> int:
    return (target.date() - now.date()).days


def months_between(earlier: datetime, later: datetime) -> int:
    return max(0, round(days_between(earlier, later) / 30))


def rel_days(d: int) -> str:
    if d > 1:
        return f"in {d} days"
    return {1: "tomorrow", 0: "today"}.get(d, f"{-d} days ago")


def fmt_time(dt: datetime) -> str:
    return f"{dt.hour % 12 or 12}{':%02d' % dt.minute if dt.minute else ''}{'am' if dt.hour < 12 else 'pm'}"


def now_or(value=None) -> datetime:
    """A datetime, an ISO string, or None (-> current UTC time)."""
    if isinstance(value, datetime):
        return value
    return parse_dt(value) or datetime.now(timezone.utc)


def slot_labels(payload: dict) -> list[str]:
    raw = payload.get("available_slots") or payload.get("next_session_options") or []
    return [s["label"] for s in raw if isinstance(s, dict) and s.get("label")]


def last_merchant_message(merchant: dict) -> dict | None:
    return next((h for h in reversed(merchant.get("conversation_history") or [])
                 if h.get("from") == "merchant" and h.get("body")), None)


def resolve_digest(category: dict, trigger: dict) -> dict | None:
    payload = trigger.get("payload") or {}
    wanted = payload.get("top_item_id") or payload.get("digest_item_id") or payload.get("alert_id")
    if wanted:
        for item in category.get("digest") or []:
            if item.get("id") == wanted:
                return item
    inline = payload.get("top_item")
    return inline if isinstance(inline, dict) else None


def owner_name(merchant: dict) -> str:
    ident = merchant.get("identity") or {}
    return (ident.get("owner_first_name") or "").strip()


# ---------------------------------------------------------------- builders
def _why_now(trigger: dict, customer: dict | None) -> Fact:
    kind = trigger.get("kind") or "update"
    who = ((customer or {}).get("identity") or {}).get("name")
    text = f"Reason for messaging now: {humanize(kind)}" + (f" for {who}" if who else "")
    return Fact("why_now", text, 100, {"kind": kind, "urgency": trigger.get("urgency", 1)})


def _digest_fact(item: dict) -> Fact:
    bits = [item.get("title", "")]
    if item.get("trial_n"):
        bits.append(f"{int(item['trial_n']):,}-patient trial")
    if item.get("summary"):
        bits.append(item["summary"])
    if item.get("actionable"):
        bits.append(f"Suggested action: {item['actionable']}")
    src = item.get("source")
    text = ". ".join(b.rstrip(".") for b in bits if b) + (f" (Source: {src})" if src else "")
    return Fact("digest", text, 95, dict(item))


def _date_facts(payload: dict, now: datetime) -> list[Fact]:
    out = []
    for key in _DATE_KEYS:
        dt = parse_dt(payload.get(key))
        if not dt:
            continue
        d = days_between(now, dt)
        label = humanize(key.replace("_iso", ""))
        out.append(Fact(f"date_{key}", f"{label}: {fmt_date(payload[key])} ({rel_days(d)})", 85,
                        {"days": d, "iso": payload[key]}))
    return out


def _payload_facts(payload: dict) -> list[Fact]:
    out = []
    for key, v in payload.items():
        if key in _SKIP_KEYS or key in _DATE_KEYS or key.startswith("days_until") or key == "days_to_wedding":
            continue
        if key.endswith("_id"):
            continue
        hv = _humanize_value(key, v)
        if hv is None:
            continue
        if key in ("available_slots", "next_session_options"):
            out.append(Fact("slots", f"Open slots: {hv}", 88, {"slots": v}))
            continue
        out.append(Fact(f"payload_{key}", f"{humanize(key)}: {hv}", 80, {"key": key, "value": v}))
    return out


def _peer_facts(category: dict, merchant: dict) -> list[Fact]:
    peer = category.get("peer_stats") or {}
    perf = merchant.get("performance") or {}
    scope = (peer.get("scope") or "peers").replace("_", " ")
    out = []
    ctr, pctr = perf.get("ctr"), peer.get("avg_ctr")
    if isinstance(ctr, (int, float)) and isinstance(pctr, (int, float)):
        out.append(Fact("peer_ctr", f"Profile CTR {pct(ctr, keep_decimal=True)} vs "
                        f"{pct(pctr, keep_decimal=True)} peer average ({scope})", 70,
                        {"ctr": ctr, "peer": pctr, "below": ctr < pctr}))
    for metric in ("views", "calls", "directions"):
        mine, avg = perf.get(metric), peer.get(f"avg_{metric}_30d")
        if isinstance(mine, (int, float)) and isinstance(avg, (int, float)):
            out.append(Fact(f"peer_{metric}", f"{metric.capitalize()} last {perf.get('window_days', 30)} days: "
                            f"{mine:,} (peer average {avg:,})", 60, {"mine": mine, "avg": avg}))
    for metric, v in (perf.get("delta_7d") or {}).items():
        if isinstance(v, (int, float)) and v:
            name = metric.replace("_pct", "")
            out.append(Fact(f"delta_{name}", f"{name.upper() if name == 'ctr' else name.capitalize()} "
                            f"{pct(v, signed=True)} over the last 7 days", 45, {"metric": name, "delta": v}))
    return out


def _offer_facts(merchant: dict) -> list[Fact]:
    out = []
    for o in merchant.get("offers") or []:
        title = o.get("title")
        if not title:
            continue
        if o.get("status") == "active":
            out.append(Fact(f"offer_{o.get('id', title)}", f"Active offer: {title}", 60, dict(o)))
        elif o.get("status") in ("expired", "paused"):
            out.append(Fact(f"offer_{o.get('id', title)}", f"Past offer ({o['status']}): {title}", 20, dict(o)))
    return out


def _aggregate_facts(merchant: dict) -> list[Fact]:
    out = []
    for key, v in (merchant.get("customer_aggregate") or {}).items():
        hv = _humanize_value(key, v)
        if hv is not None:
            out.append(Fact(f"aggregate_{key}", f"{humanize(key).capitalize()}: {hv}", 55, {"key": key, "value": v}))
    return out


def _review_facts(merchant: dict) -> list[Fact]:
    out = []
    for r in merchant.get("review_themes") or []:
        n, theme = r.get("occurrences_30d"), humanize(r.get("theme", ""))
        if not theme:
            continue
        tone = {"neg": "negative", "pos": "positive"}.get(r.get("sentiment"), r.get("sentiment") or "")
        quote = f' — "{r["common_quote"]}"' if r.get("common_quote") else ""
        text = f"{n} reviews in 30 days mention {theme} ({tone}){quote}" if n else f"Reviews mention {theme}{quote}"
        out.append(Fact(f"review_{r.get('theme')}", text, 50, dict(r)))
    return out


def _profile_facts(merchant: dict) -> list[Fact]:
    out = []
    sub = merchant.get("subscription") or {}
    if sub.get("status"):
        bits = [f"{sub.get('plan', '')} plan {sub['status']}".strip()]
        if isinstance(sub.get("days_remaining"), int):
            bits.append(f"{sub['days_remaining']} days remaining")
        out.append(Fact("subscription", "Subscription: " + ", ".join(bits), 40, dict(sub)))
    ident = merchant.get("identity") or {}
    if ident.get("verified") is False:
        out.append(Fact("unverified", "Google profile is not verified", 45, {}))
    last_mx = last_merchant_message(merchant)
    if last_mx:
        out.append(Fact("history", f'Merchant last said to Vera: "{last_mx["body"]}"', 35, dict(last_mx)))
    for s in merchant.get("signals") or []:
        name, _, val = str(s).partition(":")
        text = f"Signal: {humanize(name)}" + (f" ({val.replace('d', ' days')})" if val else "")
        out.append(Fact(f"signal_{name}", text, 30, {"signal": s}))
    return out


def _customer_facts(customer: dict, now: datetime) -> list[Fact]:
    ident = customer.get("identity") or {}
    rel = customer.get("relationship") or {}
    prefs = customer.get("preferences") or {}
    if not isinstance(prefs, dict):
        prefs = {}
    out = [Fact("customer_name", f"Customer: {ident.get('name', 'customer')}"
                + (f", prefers {ident['language_pref']}" if ident.get("language_pref") else ""), 92, dict(ident))]
    bits = []
    if rel.get("visits_total"):
        bits.append(f"{rel['visits_total']} visits")
    last = parse_dt(rel.get("last_visit"))
    if last:
        months = months_between(last, now)
        bits.append(f"last visit {fmt_date(rel['last_visit'])} (about {months} month{'s' if months != 1 else ''} ago)" if months
                    else f"last visit {fmt_date(rel['last_visit'])}")
    if rel.get("services_received"):
        bits.append("services: " + ", ".join(dict.fromkeys(humanize(str(x)) for x in rel["services_received"])))
    if customer.get("state"):
        bits.append(f"status {humanize(customer['state'])}")
    if bits:
        out.append(Fact("customer_relationship", "Relationship: " + "; ".join(bits), 90, dict(rel)))
    pref_bits = [f"{humanize(k)}: {_humanize_value(k, v)}" for k, v in prefs.items()
                 if k not in ("channel", "reminder_opt_in") and _humanize_value(k, v)]
    if pref_bits:
        out.append(Fact("customer_prefs", "Preferences: " + "; ".join(pref_bits), 75, dict(prefs)))
    return out


def _seasonal_facts(category: dict, now: datetime) -> list[Fact]:
    month = now.strftime("%b")
    order = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    out = []
    for beat in category.get("seasonal_beats") or []:
        rng = beat.get("month_range", "")
        a, _, b = rng.partition("-")
        if a in order and (b or a) in order:
            i, j, k = order.index(a), order.index(b or a), order.index(month)
            inside = i <= k <= j if i <= j else (k >= i or k <= j)
            if inside:
                out.append(Fact(f"season_{rng}", f"Seasonal pattern ({rng}): {beat.get('note', '')}", 40, dict(beat)))
    for tr in (category.get("trend_signals") or [])[:2]:
        if tr.get("query") and isinstance(tr.get("delta_yoy"), (int, float)):
            out.append(Fact(f"trend_{tr['query']}", f'Searches for "{tr["query"]}" {pct(tr["delta_yoy"], signed=True)} '
                            f"year on year", 35, dict(tr)))
    return out


def build_facts(category: dict, merchant: dict, trigger: dict, customer: dict | None,
                now: datetime) -> list[Fact]:
    category, merchant, trigger = category or {}, merchant or {}, trigger or {}
    payload = trigger.get("payload") or {}
    facts: list[Fact] = [_why_now(trigger, customer)]
    item = resolve_digest(category, trigger)
    if item:
        facts.append(_digest_fact(item))
    if customer:
        facts += _customer_facts(customer, now)
    facts += _date_facts(payload, now)
    facts += _payload_facts(payload)
    facts += _peer_facts(category, merchant)
    facts += _offer_facts(merchant)
    facts += _aggregate_facts(merchant)
    facts += _review_facts(merchant)
    facts += _profile_facts(merchant)
    facts += _seasonal_facts(category, now)

    kind = str(trigger.get("kind") or "")
    boosts = tuple(p for frag, prefixes in _KIND_BOOSTS.items() if frag in kind for p in prefixes)

    def score(f: Fact) -> int:
        if f.key == "why_now":
            return 10_000
        return f.weight + (30 if boosts and f.key.startswith(boosts) else 0)

    seen, ranked = set(), []
    for f in sorted(facts, key=lambda f: (-score(f), f.key)):
        if f.text not in seen:
            seen.add(f.text)
            ranked.append(f)
    return ranked


# ---------------------------------------------------------------- grounding
def _walk_numbers(obj, out: set[str], key: str = "") -> None:
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            _walk_numbers(v, out, str(k))
    elif isinstance(obj, list):
        for v in obj:
            _walk_numbers(v, out, key)
    elif isinstance(obj, (int, float)):
        _add_number_forms(obj, out, _is_pct_key(key))
    elif isinstance(obj, str):
        out |= extract_numbers(obj)
        m = _ISO_DATE_RE.match(obj)
        if m:
            y, mo, d, hh, mm = m.groups()
            out |= {y, str(int(mo)), str(int(d))}
            if hh:
                h = int(hh)
                out |= {str(h), str(h % 12 or 12), str(int(mm))}


def _add_number_forms(v: float, out: set[str], is_pct: bool) -> None:
    a = abs(v)
    out.add(_norm_num(f"{a:.6f}") if isinstance(v, float) else str(int(a)))
    if isinstance(v, float) and is_pct:
        out.add(_norm_num(f"{round(a * 100):d}"))
        out.add(_norm_num(f"{a * 100:.1f}"))
    if isinstance(v, float):
        out.add(str(round(a)))


def allowed_numbers(category: dict, merchant: dict, trigger: dict, customer: dict | None,
                    facts: list[Fact]) -> set[str]:
    """Every number a message may contain: all numbers present anywhere in the
    contexts (plus percent / date / 12h-time forms) and in the derived facts."""
    out: set[str] = set()
    for ctx in (category, merchant, trigger, customer):
        _walk_numbers(ctx or {}, out)
    for f in facts:
        out |= extract_numbers(f.text)
    out |= {str(i) for i in range(0, 11)}  # small counts: "Reply 1", "2 slots", "5 min"
    return out
