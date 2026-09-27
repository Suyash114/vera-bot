import time
from datetime import datetime, timezone

from bot.composer import compose
from tests.fakes import FakeLLM

NOW = datetime(2026, 4, 26, 10, tzinfo=timezone.utc)
GOOD = ("Dr. Meera, this week's JIDA digest (JIDA Oct 2026, p.14): a 2,100-patient trial found 3-month "
        "fluoride recall cut caries recurrence 38% vs 6-month. Want me to draft a patient note? Reply YES.")


def test_output_contract_keys(ds):
    out = compose(*ds.contexts_for("trg_001_research_digest_dentists"), now=NOW)
    assert {"body", "cta", "send_as", "suppression_key", "rationale", "template_name",
            "template_params", "conversation_id", "source"} <= set(out)


def test_no_llm_uses_template(ds):
    out = compose(*ds.contexts_for("trg_001_research_digest_dentists"), now=NOW)
    assert out["source"] == "template" and out["body"]


def test_good_llm_output_is_used(ds):
    out = compose(*ds.contexts_for("trg_001_research_digest_dentists"), now=NOW, llm=FakeLLM([GOOD]))
    assert out["source"] == "llm" and out["body"] == GOOD and out["send_as"] == "vera"


def test_fabricated_number_triggers_retry_then_fix(ds):
    llm = FakeLLM(["Dr. Meera, 999 patients need this. Want me to draft it?", GOOD])
    out = compose(*ds.contexts_for("trg_001_research_digest_dentists"), now=NOW, llm=llm)
    assert out["body"] == GOOD and len(llm.calls) == 2 and "999" in llm.calls[1][1]


def test_bad_sentence_stripped_when_retry_also_bad(ds):
    bad = "Dr. Meera, JIDA Oct 2026, p.14 has a 2,100-patient trial. 999 clinics use it. Want me to draft a patient note?"
    out = compose(*ds.contexts_for("trg_001_research_digest_dentists"), now=NOW, llm=FakeLLM([bad, bad]))
    assert out["source"] == "llm_stripped" and "999" not in out["body"] and "2,100" in out["body"]


def test_unusable_llm_output_falls_back_to_template(ds):
    out = compose(*ds.contexts_for("trg_001_research_digest_dentists"), now=NOW, llm=FakeLLM(["777 777", "888"]))
    assert out["source"] == "template" and "777" not in out["body"]


def test_llm_skipped_when_deadline_passed(ds):
    llm = FakeLLM([GOOD])
    out = compose(*ds.contexts_for("trg_001_research_digest_dentists"), now=NOW, llm=llm,
                  deadline=time.monotonic() - 1)
    assert out["source"] == "template" and llm.calls == []


def test_repeat_of_prior_body_avoided(ds):
    ctx = ds.contexts_for("trg_001_research_digest_dentists")
    first = compose(*ctx, now=NOW)["body"]
    assert compose(*ctx, now=NOW, prior_bodies=[first])["body"] != first


def test_customer_send_as_and_rationale(ds):
    out = compose(*ds.contexts_for("trg_003_recall_due_priya"), now=NOW)
    assert out["send_as"] == "merchant_on_behalf" and "Evidence:" in out["rationale"]


def test_adaptive_injection_new_numbers_flow_through(ds):
    cat, m, t, c = ds.contexts_for("trg_004_perf_dip_bharat")
    before = compose(cat, m, t, c, now=NOW)["body"]
    m2 = {**m, "performance": {**m["performance"], "ctr": 0.011}}
    after = compose(cat, m2, t, c, now=NOW)["body"]
    assert "1.8%" in before and "1.1%" in after and "1.8%" not in after


def test_deterministic_for_same_inputs(ds):
    ctx = ds.contexts_for("trg_010_ipl_match_delhi")
    assert compose(*ctx, now=NOW) == compose(*ctx, now=NOW)
