from datetime import datetime, timezone

from bot.facts import allowed_numbers, build_facts, extract_numbers, resolve_digest

NOW = datetime(2026, 4, 26, 10, tzinfo=timezone.utc)


def texts(facts):
    return " | ".join(f.text for f in facts)


def test_research_digest_resolves_item_with_source(ds):
    cat, m, t, c = ds.contexts_for("trg_001_research_digest_dentists")
    blob = texts(build_facts(cat, m, t, c, NOW))
    assert "JIDA Oct 2026, p.14" in blob and "2,100" in blob and "38%" in blob


def test_peer_ctr_gap_fact(ds):
    cat, m, t, c = ds.contexts_for("trg_001_research_digest_dentists")
    gap = next(f for f in build_facts(cat, m, t, c, NOW) if f.key == "peer_ctr")
    assert "2.1%" in gap.text and "3.0%" in gap.text


def test_every_trigger_has_why_now_fact_first(ds):
    for t in ds.triggers():
        cat, m, _, c = ds.contexts_for(t["id"])
        facts = build_facts(cat, m, t, c, NOW)
        assert facts[0].key == "why_now", t["id"]


def test_payload_is_humanised(ds):
    for t in ds.triggers():
        cat, m, _, c = ds.contexts_for(t["id"])
        blob = texts(build_facts(cat, m, t, c, NOW))
        for junk in ("None", "True", "False", "top_item_id", "_id:", "null"):
            assert junk not in blob, (t["id"], junk)


def test_days_until_computed_from_now_not_payload(ds):
    cat, m, t, c = ds.contexts_for("trg_006_festival_diwali")
    now = datetime(2026, 10, 1, tzinfo=timezone.utc)
    blob = texts(build_facts(cat, m, t, c, now))
    assert "30 days" in blob and "188" not in blob


def test_customer_facts_present_for_customer_trigger(ds):
    cat, m, t, c = ds.contexts_for("trg_003_recall_due_priya")
    blob = texts(build_facts(cat, m, t, c, NOW))
    assert "Priya" in blob and "Wed 5 Nov, 6pm" in blob


def test_active_offer_fact(ds):
    cat, m, t, c = ds.contexts_for("trg_001_research_digest_dentists")
    assert "Dental Cleaning @ ₹299" in texts(build_facts(cat, m, t, c, NOW))


def test_sparse_contexts_do_not_crash(ds):
    for slug in ds.categories:
        m = {"merchant_id": "m_x", "category_slug": slug, "identity": {"name": "X Store"}}
        t = {"id": "t_x", "kind": "brand_new_kind", "merchant_id": "m_x"}
        facts = build_facts({"slug": slug}, m, t, None, NOW)
        assert facts and facts[0].key == "why_now"


def test_resolve_digest_by_alert_id_and_inline():
    cat = {"digest": [{"id": "a1", "title": "Recall"}]}
    assert resolve_digest(cat, {"payload": {"alert_id": "a1"}})["title"] == "Recall"
    assert resolve_digest(cat, {"payload": {"top_item": {"title": "Inline"}}})["title"] == "Inline"
    assert resolve_digest(cat, {"payload": {}}) is None


def test_extract_numbers_normalises():
    assert extract_numbers("2,100 patients, 38% better, ₹1,499, 3.0% on 05 Nov") == {"2100", "38", "1499", "3", "5"}


def test_allowed_numbers_include_derived_forms(ds):
    cat, m, t, c = ds.contexts_for("trg_003_recall_due_priya")
    allowed = allowed_numbers(cat, m, t, c, build_facts(cat, m, t, c, NOW))
    assert {"2.1", "299", "5", "6", "18"} <= allowed        # ctr 0.021, slot 18:00 -> 6pm
    assert not any(n.startswith(".") for n in allowed)
    cat, m, t, c = ds.contexts_for("trg_004_perf_dip_bharat")
    assert "50" in allowed_numbers(cat, m, t, c, [])               # delta_pct -0.5


def test_extract_numbers_keeps_leading_zero_of_decimals():
    assert extract_numbers("ctr 0.021 and 0.5") == {"0.021", "0.5"}
