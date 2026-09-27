"""Regression tests for findings from the pre-landing code review."""
from datetime import datetime, timezone

from bot.facts import _humanize_value, allowed_numbers, build_facts
import pytest

from bot.llm import LLMConfigError, OpenAICompatLLM, from_env
from bot.planner import make_plan
from bot.replies import classify, respond
from bot.tick import run_tick, select
from bot.writer import build_prompt

NOW = datetime(2026, 4, 26, 10, tzinfo=timezone.utc)
MID, PRIYA = "m_001_drmeera_dentist_delhi", "c_001_priya_for_m001"


# 1. customer opt-out is scoped to that customer
def test_customer_stop_does_not_silence_merchant(loaded_store):
    loaded_store.open_conversation("conv_c", MID, PRIYA, "trg_003_recall_due_priya", "slots")
    assert respond(loaded_store, "conv_c", MID, PRIYA, "STOP", now=NOW)["action"] == "end"
    assert [c.trigger["id"] for c in select(loaded_store, ["trg_001_research_digest_dentists"])] == \
        ["trg_001_research_digest_dentists"]
    assert select(loaded_store, ["trg_003_recall_due_priya"]) == []


# 2. nothing is sent to an opted-out recipient, even to an auto-responder
def test_no_nudge_after_opt_out(loaded_store):
    respond(loaded_store, "conv_o", MID, None, "STOP", now=NOW)
    out = respond(loaded_store, "conv_o", MID, None, "Thank you for contacting us, we will get back to you shortly", now=NOW)
    assert out["action"] == "end"


def test_human_can_reengage_after_opt_out(loaded_store):
    respond(loaded_store, "conv_r", MID, None, "STOP", now=NOW)
    out = respond(loaded_store, "conv_r", MID, None, "Actually yes, send me the draft", now=NOW)
    assert out["action"] == "send" and not loaded_store.is_suppressed(MID)


# 3. a failing compose releases its claim and doesn't sink the tick
def test_broken_customer_context_does_not_sink_tick(loaded_store):
    c = loaded_store.get("customer", PRIYA)
    loaded_store.put("customer", PRIYA, 2, {**c, "identity": None})
    acts = run_tick(loaded_store, ["trg_003_recall_due_priya", "trg_010_ipl_match_delhi"], NOW, llm=None)
    assert "trg_010_ipl_match_delhi" in [a["trigger_id"] for a in acts]


# 4. replies to the bot's own CTAs
def test_cancel_no_nahi_are_not_interested():
    for msg in ("CANCEL", "No", "nahi", "no thanks"):
        assert classify(msg) == "not_interested", msg


def test_change_is_reschedule(loaded_store):
    assert classify("CHANGE") == "reschedule"
    loaded_store.open_conversation("conv_ch", MID, PRIYA, "trg_003_recall_due_priya", "slots")
    out = respond(loaded_store, "conv_ch", MID, PRIYA, "CHANGE", now=NOW)
    assert out["action"] == "send" and "Thu 6 Nov, 5pm" in out["body"]


# 5. provider-matched API keys
def test_openai_provider_never_uses_anthropic_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    with pytest.raises(LLMConfigError):
        from_env()
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    llm = from_env()
    assert isinstance(llm, OpenAICompatLLM) and llm._client.headers["Authorization"] == "Bearer openai-key"


def test_anthropic_provider_never_uses_openai_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    with pytest.raises(LLMConfigError):
        from_env()


# 6. customer-facing prompts carry no merchant-internal facts
def test_customer_prompt_has_no_internal_facts(ds):
    customer_triggers = [t for t in ds.triggers() if t.get("customer_id")]
    assert len(customer_triggers) >= 20
    for t in customer_triggers:
        cat, m, _, c = ds.contexts_for(t["id"])
        p = make_plan(cat, m, t, c, build_facts(cat, m, t, c, NOW))
        _, user = build_prompt(p)
        for internal in ("Subscription:", "peer average", "Merchant last said", "Signal:", "customers this year",
                         "Profile CTR", "last 30 days", "over the last 7 days", "Seasonal pattern", "Searches for"):
            assert internal not in user, (t["id"], internal)


# 7. only percent-like fields become percentages
def test_non_percent_float_is_not_a_percentage():
    assert _humanize_value("distance_km", 0.8) == "0.8"
    assert _humanize_value("delta_pct", -0.5) == "-50%"


def test_non_percent_float_does_not_whitelist_x100(ds):
    cat, m, t, _ = ds.contexts_for("trg_023_competitor_opened_dentist")
    t2 = {**t, "payload": {**t["payload"], "distance_km": 0.8}}
    assert "80" not in allowed_numbers(cat, m, t2, None, build_facts(cat, m, t2, None, NOW))


# 8. a low-value trigger never blocks a sendable one for the same recipient
def test_low_value_does_not_shadow_real_trigger(loaded_store):
    mid = "m_003_studio11_salon_hyderabad"
    loaded_store.put("trigger", "t_low", 1, {"id": "t_low", "kind": "ping", "urgency": 1, "payload": {},
                                             "merchant_id": mid, "expires_at": "2026-04-27T00:00:00Z",
                                             "suppression_key": "low"})
    got = [c.trigger["id"] for c in select(loaded_store, ["t_low", "trg_008_curious_ask_studio11"])]
    assert got == ["trg_008_curious_ask_studio11"]


# 9. "stop" inside a normal request is not an opt-out
def test_stop_in_context_is_not_opt_out():
    assert classify("Can you stop the old offer and start a new one?") != "opt_out"
    assert classify("yes, non-stop delivery during the match") != "opt_out"
    assert classify("STOP") == "opt_out" and classify("please stop") == "opt_out"
    assert classify("Stop messaging me. This is useless spam.") == "opt_out"


# 10. dedup is per recipient, not global
def test_shared_suppression_key_across_merchants(loaded_store):
    t = loaded_store.get("trigger", "trg_001_research_digest_dentists")
    loaded_store.put("trigger", "t_other", 1, {**t, "id": "t_other", "merchant_id": "m_002_bharat_dentist_mumbai"})
    first = run_tick(loaded_store, ["trg_001_research_digest_dentists"], NOW, llm=None)
    second = run_tick(loaded_store, ["t_other"], NOW, llm=None)
    assert len(first) == 1 and len(second) == 1


# smaller: a real human reply resets the auto-reply ladder
def test_human_reply_resets_auto_reply_count(loaded_store):
    auto = "Thank you for contacting us! Our team will respond shortly."
    respond(loaded_store, "c1", MID, None, auto, now=NOW)
    respond(loaded_store, "c1", MID, None, "How many calls did I get?", now=NOW)
    assert respond(loaded_store, "c1", MID, None, auto, now=NOW)["action"] == "send"   # back to first nudge
