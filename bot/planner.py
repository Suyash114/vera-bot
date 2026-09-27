"""Deterministic decisions about a message: who it's from, who it's to, which
facts lead, what the single CTA is, and why. The writer only phrases a Plan."""
from __future__ import annotations

from dataclasses import dataclass, field

from .facts import Fact, owner_name
from .lang import wants_hinglish

CTAS = ("binary_yes_no", "binary_confirm_cancel", "multi_choice_slot", "open_ended", "none")

# (kind substring, angle) — first match wins; customer scope handled separately.
_ANGLES = (
    ("research", "source_cited"), ("regulation", "source_cited"), ("compliance", "source_cited"),
    ("supply", "source_cited"), ("alert", "source_cited"), ("cde", "source_cited"),
    ("curious", "ask_merchant"), ("planning", "draft_artifact"), ("intent", "draft_artifact"),
    ("seasonal_perf_dip", "reframe"), ("perf_dip", "loss_aversion"), ("competitor", "loss_aversion"),
    ("winback", "loss_aversion"), ("renewal", "loss_aversion"), ("unverified", "loss_aversion"),
    ("dormant", "reciprocity"), ("review", "reciprocity"),
    ("spike", "momentum"), ("milestone", "momentum"),
    ("festival", "timely_event"), ("ipl", "timely_event"), ("match", "timely_event"),
    ("seasonal", "timely_event"), ("weather", "timely_event"), ("heat", "timely_event"),
)

# Where each fact family comes from — rendered into the rationale as an evidence map.
_SOURCES = (
    ("why_now", "trigger.kind"), ("digest", "category.digest"), ("customer_", "customer"),
    ("slots", "trigger.payload"), ("date_", "trigger.payload"), ("payload_", "trigger.payload"),
    ("peer_", "merchant.performance+category.peer_stats"), ("delta_", "merchant.performance.delta_7d"),
    ("offer_", "merchant.offers"), ("aggregate_", "merchant.customer_aggregate"),
    ("review_", "merchant.review_themes"), ("subscription", "merchant.subscription"),
    ("history", "merchant.conversation_history"), ("signal_", "merchant.signals"),
    ("unverified", "merchant.identity"), ("season_", "category.seasonal_beats"),
    ("trend_", "category.trend_signals"),
)

_CONTENT_KEYS = ("digest", "customer_", "slots", "date_", "payload_")
# What a merchant's customer may be told: their own relationship, the event, slots and public offers.
# Never the merchant's metrics, peer benchmarks, subscription or chats with Vera.
CUSTOMER_SAFE = ("customer_relationship", "customer_prefs", "slots", "date_", "payload_", "offer_")


def is_low_value(trigger: dict, facts: list[Fact]) -> bool:
    """Urgency-1 trigger with nothing specific to say -> better not to send at all."""
    has_content = any(f.key.startswith(_CONTENT_KEYS) for f in facts)
    return int((trigger or {}).get("urgency") or 1) <= 1 and not has_content


@dataclass
class Plan:
    send_as: str
    salutation: str
    merchant_name: str
    hinglish: bool
    cta: str
    angle: str
    kind: str
    category_slug: str
    conversation_id: str
    suppression_key: str
    template_name: str
    template_params: list[str]
    rationale: str
    chosen: list[Fact]
    facts: list[Fact]
    voice: dict = field(default_factory=dict)
    customer_name: str | None = None
    low_value: bool = False
    urgency: int = 1


def fact_source(key: str) -> str:
    return next((src for prefix, src in _SOURCES if key.startswith(prefix)), "context")


def salutation(category_slug: str, merchant: dict, customer: dict | None) -> str:
    if customer:
        return ((customer.get("identity") or {}).get("name") or "there").strip()
    name = owner_name(merchant)
    if not name:
        return ((merchant.get("identity") or {}).get("name") or "there").strip()
    if category_slug == "dentists" and not name.lower().startswith("dr"):
        return f"Dr. {name}"
    return name


def _angle(kind: str, customer: dict | None) -> str:
    if customer:
        return "customer_personal"
    return next((a for frag, a in _ANGLES if frag in kind), "specific_benchmark")


def _cta(kind: str, angle: str, facts: list[Fact], customer: dict | None) -> str:
    if customer and any(f.key == "slots" for f in facts):
        return "multi_choice_slot"
    if "refill" in kind or "renewal" in kind:
        return "binary_confirm_cancel"
    if angle in ("ask_merchant", "draft_artifact"):
        return "open_ended"
    return "binary_yes_no"


def make_plan(category: dict, merchant: dict, trigger: dict, customer: dict | None,
              facts: list[Fact]) -> Plan:
    category, merchant, trigger = category or {}, merchant or {}, trigger or {}
    kind = str(trigger.get("kind") or "update")
    slug = merchant.get("category_slug") or category.get("slug") or ""
    mid = merchant.get("merchant_id") or trigger.get("merchant_id") or "unknown"
    tid = trigger.get("id") or "trigger"
    angle = _angle(kind, customer)
    usable = [f for f in facts if f.key != "why_now"]
    if customer:
        usable = [f for f in usable if f.key.startswith(CUSTOMER_SAFE)]
    chosen = usable[:5]
    urgency = int(trigger.get("urgency") or 1)
    salutation_ = salutation(slug, merchant, customer)
    cta = _cta(kind, angle, facts, customer)
    evidence = ", ".join(f"{f.key}<-{fact_source(f.key)}" for f in chosen)
    kind_h = kind.replace("_", " ")
    rationale = (f"{kind_h} ({trigger.get('source', 'internal')}, urgency {urgency}) -> {angle.replace('_', ' ')} "
                 f"angle, {cta} CTA, {'merchant_on_behalf to ' + salutation_ if customer else 'to merchant'}. "
                 f"Evidence: {evidence}.")
    return Plan(
        send_as="merchant_on_behalf" if customer or trigger.get("scope") == "customer" else "vera",
        salutation=salutation_,
        merchant_name=((merchant.get("identity") or {}).get("name") or "").strip(),
        hinglish=wants_hinglish(merchant, customer),
        cta=cta,
        angle=angle,
        kind=kind,
        category_slug=slug,
        conversation_id=f"conv_{mid}_{tid}",
        suppression_key=trigger.get("suppression_key") or f"{kind}:{mid}:{tid}",
        template_name=f"{'merchant' if customer else 'vera'}_{kind}_v1",
        template_params=[salutation_] + [f.text for f in chosen[:2]],
        rationale=rationale,
        chosen=chosen,
        facts=facts,
        voice=category.get("voice") or {},
        customer_name=salutation_ if customer else None,
        low_value=is_low_value(trigger, facts),
        urgency=urgency,
    )
