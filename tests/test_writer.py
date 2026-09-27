from datetime import datetime, timezone


from bot.facts import allowed_numbers, build_facts
from bot.planner import make_plan
from bot.validator import strip_violations, taboo_list, validate
from bot.writer import KIND_TEMPLATES, build_prompt, render_template

NOW = datetime(2026, 4, 26, 10, tzinfo=timezone.utc)


def setup(ds, tid, now=NOW):
    cat, m, t, c = ds.contexts_for(tid)
    facts = build_facts(cat, m, t, c, now)
    p = make_plan(cat, m, t, c, facts)
    ctx = {"category": cat, "merchant": m, "trigger": t, "customer": c, "now": now}
    return p, ctx, allowed_numbers(cat, m, t, c, facts), taboo_list(cat)


def test_every_known_kind_has_a_template(ds):
    assert {t["kind"] for t in ds.triggers()} <= set(KIND_TEMPLATES)


def test_templates_pass_validator_for_all_triggers(ds):
    for t in ds.triggers():
        p, ctx, allowed, taboos = setup(ds, t["id"])
        body = render_template(p, ctx)
        assert body.strip(), t["id"]
        assert validate(body, allowed=allowed, taboos=taboos, prior=[]) == [], (t["id"], body)


def test_template_opens_with_salutation(ds):
    for tid in ("trg_001_research_digest_dentists", "trg_003_recall_due_priya"):
        p, ctx, *_ = setup(ds, tid)
        assert p.salutation in render_template(p, ctx)[:60]


def test_research_template_cites_source_and_numbers(ds):
    p, ctx, *_ = setup(ds, "trg_001_research_digest_dentists")
    body = render_template(p, ctx)
    assert "JIDA Oct 2026, p.14" in body and "2,100" in body


def test_customer_template_offers_real_slots(ds):
    p, ctx, *_ = setup(ds, "trg_003_recall_due_priya")
    body = render_template(p, ctx)
    assert "Wed 5 Nov, 6pm" in body and "Thu 6 Nov, 5pm" in body


def test_unknown_kind_uses_generic_template(ds):
    cat, m, _, _ = ds.contexts_for("trg_001_research_digest_dentists")
    t = {"id": "t_new", "kind": "water_outage", "merchant_id": m["merchant_id"], "urgency": 3,
         "payload": {"area": "Lajpat Nagar", "hours": 6}}
    facts = build_facts(cat, m, t, None, NOW)
    p = make_plan(cat, m, t, None, facts)
    body = render_template(p, {"category": cat, "merchant": m, "trigger": t, "customer": None, "now": NOW})
    assert "Lajpat Nagar" in body and "6" in body
    assert validate(body, allowed=allowed_numbers(cat, m, t, None, facts), taboos=taboo_list(cat), prior=[]) == []


def test_template_one_cta_line(ds):
    for t in ds.triggers():
        p, ctx, *_ = setup(ds, t["id"])
        assert render_template(p, ctx).count("?") <= 1, t["id"]


def test_validator_flags_ungrounded_number():
    assert validate("57 patients waiting", allowed={"2.1"}, taboos=[], prior=[]) == ["ungrounded_number:57"]


def test_validator_accepts_derived_forms():
    assert validate("CTR 2.1% vs 3.0%, 6pm slot", allowed={"2.1", "3", "6"}, taboos=[], prior=[]) == []


def test_validator_flags_taboo_case_insensitive():
    assert validate("Results GUARANTEED", allowed=set(), taboos=["guaranteed"], prior=[]) == ["taboo:guaranteed"]


def test_validator_flags_repeat():
    assert validate("Hello there", allowed=set(), taboos=[], prior=["Hello there"]) == ["repeat"]


def test_validator_flags_multi_cta():
    assert "multi_cta" in validate("Reply YES? Or reply NO? Or MAYBE?", allowed=set(), taboos=[], prior=[])


def test_validator_flags_empty():
    assert validate("   ", allowed=set(), taboos=[], prior=[]) == ["empty"]


def test_taboo_list_strips_parentheticals(ds):
    taboos = taboo_list(ds.category("dentists"))
    assert "fda-approved" in taboos and "guaranteed" in taboos


def test_strip_violations_drops_offending_sentence():
    body = "Dr. Meera, CTR is 2.1%. 999 patients agree. Want me to draft it?"
    assert strip_violations(body, allowed={"2.1"}, taboos=[]) == "Dr. Meera, CTR is 2.1%. Want me to draft it?"


def test_prompt_contains_facts_rules_and_no_raw_json(ds):
    p, ctx, *_ = setup(ds, "trg_001_research_digest_dentists")
    system, user = build_prompt(p)
    assert "Never invent" in system and "JIDA Oct 2026, p.14" in user and "Dr. Meera" in user
    assert "{" not in user


BROKEN_PHRASES = ("soon (soon)", "a the ", "since.", "Dr. Want", "Dr. R. Want", " 1 months",
                  "(regular medicines)", "above the 2.5% peer", "dropped in the", "up over the")


def test_no_broken_phrasing_for_thin_payloads(ds):
    for t in ds.triggers():
        p, ctx, *_ = setup(ds, t["id"])
        body = render_template(p, ctx)
        for bad in BROKEN_PHRASES:
            assert bad not in body, (t["id"], bad, body)


def test_regulation_date_not_repeated(ds):
    p, ctx, *_ = setup(ds, "trg_002_compliance_dci_radiograph")
    body = render_template(p, ctx)
    assert body.count("15 Dec 2026") + body.count("2026-12-15") == 1


def test_research_segment_reads_naturally(ds):
    p, ctx, *_ = setup(ds, "trg_001_research_digest_dentists")
    body = render_template(p, ctx)
    assert "124 high-risk adult patients" in body and "adults patients" not in body
