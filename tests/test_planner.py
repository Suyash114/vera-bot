from datetime import datetime, timezone

from bot.facts import build_facts
from bot.lang import detect_lang, wants_hinglish
from bot.planner import CTAS, make_plan

NOW = datetime(2026, 4, 26, 10, tzinfo=timezone.utc)


def plan(ds, tid):
    cat, m, t, c = ds.contexts_for(tid)
    return make_plan(cat, m, t, c, build_facts(cat, m, t, c, NOW))


def test_customer_trigger_is_sent_on_behalf_of_merchant(ds):
    p = plan(ds, "trg_003_recall_due_priya")
    assert p.send_as == "merchant_on_behalf" and p.salutation == "Priya"


def test_customer_hinglish_pref_honoured(ds):
    assert plan(ds, "trg_003_recall_due_priya").hinglish is True


def test_slot_trigger_uses_multi_choice_cta(ds):
    assert plan(ds, "trg_003_recall_due_priya").cta == "multi_choice_slot"


def test_dentist_salutation_gets_dr_prefix(ds):
    p = plan(ds, "trg_001_research_digest_dentists")
    assert p.salutation == "Dr. Meera" and p.send_as == "vera"


def test_no_double_dr_prefix(ds):
    for t in ds.triggers():
        if t["merchant_id"].startswith("m_011"):
            assert not plan(ds, t["id"]).salutation.startswith("Dr. Dr.")


def test_research_digest_angle_is_source_cited(ds):
    assert plan(ds, "trg_001_research_digest_dentists").angle == "source_cited"


def test_curious_ask_is_open_ended(ds):
    p = plan(ds, "trg_008_curious_ask_studio11")
    assert p.cta == "open_ended" and p.angle == "ask_merchant"


def test_refill_uses_confirm_cancel(ds):
    assert plan(ds, "trg_019_chronic_refill_grandfather").cta == "binary_confirm_cancel"


def test_cta_always_from_allowed_enum(ds):
    for t in ds.triggers():
        assert plan(ds, t["id"]).cta in CTAS, t["id"]


def test_rationale_has_evidence_map(ds):
    p = plan(ds, "trg_001_research_digest_dentists")
    assert "research digest" in p.rationale and "digest<-category.digest" in p.rationale


def test_conversation_id_is_decodable(ds):
    p = plan(ds, "trg_003_recall_due_priya")
    assert p.conversation_id == "conv_m_001_drmeera_dentist_delhi_trg_003_recall_due_priya"


def test_suppression_key_passthrough_with_fallback(ds):
    assert plan(ds, "trg_001_research_digest_dentists").suppression_key == "research:dentists:2026-W17"
    cat, m, _, _ = ds.contexts_for("trg_001_research_digest_dentists")
    t = {"id": "t_new", "kind": "x", "merchant_id": m["merchant_id"]}
    assert make_plan(cat, m, t, None, build_facts(cat, m, t, None, NOW)).suppression_key == "x:m_001_drmeera_dentist_delhi:t_new"


def test_low_value_trigger_is_flagged():
    m = {"merchant_id": "m", "category_slug": "salons", "identity": {"name": "S"}}
    t = {"id": "t", "kind": "unknown_ping", "merchant_id": "m", "urgency": 1, "payload": {}}
    assert make_plan({"slug": "salons"}, m, t, None, build_facts({}, m, t, None, NOW)).low_value is True


def test_real_trigger_is_not_low_value(ds):
    assert plan(ds, "trg_008_curious_ask_studio11").low_value is False


def test_chosen_facts_exclude_why_now_and_are_capped(ds):
    p = plan(ds, "trg_004_perf_dip_bharat")
    assert 1 <= len(p.chosen) <= 5 and all(f.key != "why_now" for f in p.chosen)


def test_detect_lang():
    assert detect_lang("haan bhai kar do") == "hinglish"
    assert detect_lang("हाँ ठीक है") == "hi"
    assert detect_lang("Yes please go ahead") == "en"


def test_wants_hinglish_for_merchant_languages():
    assert wants_hinglish({"identity": {"languages": ["en", "hi"]}}, None) is True
    assert wants_hinglish({"identity": {"languages": ["en", "ta"]}}, None) is False
    assert wants_hinglish({}, {"identity": {"language_pref": "english"}}) is False
