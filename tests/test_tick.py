import time
from datetime import datetime, timezone

from bot.tick import MAX_ACTIONS, run_tick, select, send_key
from tests.fakes import FakeLLM

NOW = datetime(2026, 4, 26, 10, tzinfo=timezone.utc)
LATE = datetime(2026, 9, 27, tzinfo=timezone.utc)   # after every seed expiry


def ids(cands):
    return [c.trigger["id"] for c in cands]


def test_one_per_recipient_highest_urgency_wins(loaded_store):
    got = ids(select(loaded_store, ["trg_001_research_digest_dentists", "trg_002_compliance_dci_radiograph"]))
    assert got == ["trg_002_compliance_dci_radiograph"]


def test_customer_and_merchant_sends_are_separate_recipients(loaded_store):
    got = ids(select(loaded_store, ["trg_001_research_digest_dentists", "trg_003_recall_due_priya"]))
    assert sorted(got) == ["trg_001_research_digest_dentists", "trg_003_recall_due_priya"]


def test_unknown_trigger_ignored(loaded_store):
    assert ids(select(loaded_store, ["nope"])) == []


def test_expired_triggers_still_sent_when_listed(loaded_store):
    assert ids(select(loaded_store, ["trg_010_ipl_match_delhi"])) == ["trg_010_ipl_match_delhi"]


def test_expiry_breaks_urgency_ties(loaded_store):
    t1 = {"id": "a", "kind": "x", "merchant_id": "m_005_pizzajunction_restaurant_delhi", "urgency": 2,
          "expires_at": "2026-06-01T00:00:00Z", "suppression_key": "a"}
    t2 = {**t1, "id": "b", "expires_at": "2026-05-01T00:00:00Z", "suppression_key": "b"}
    loaded_store.put("trigger", "a", 1, t1)
    loaded_store.put("trigger", "b", 1, t2)
    assert ids(select(loaded_store, ["a", "b"])) == ["b"]


def test_sent_suppression_key_skipped(loaded_store):
    loaded_store.sent_keys.add(send_key("m_001_drmeera_dentist_delhi", None,
                                        {"suppression_key": "compliance:dci_radiograph:2026"}))
    got = ids(select(loaded_store, ["trg_001_research_digest_dentists", "trg_002_compliance_dci_radiograph"]))
    assert got == ["trg_001_research_digest_dentists"]


def test_customer_trigger_without_customer_context_skipped(loaded_store):
    t = loaded_store.get("trigger", "trg_003_recall_due_priya")
    loaded_store.put("trigger", "t_ghost", 1, {**t, "id": "t_ghost", "customer_id": "c_missing", "suppression_key": "g"})
    assert ids(select(loaded_store, ["t_ghost"])) == []


def test_customer_without_consent_skipped(loaded_store):
    c = loaded_store.get("customer", "c_001_priya_for_m001")
    loaded_store.put("customer", "c_001_priya_for_m001", 2,
                     {**c, "preferences": {**c["preferences"], "reminder_opt_in": False}})
    assert ids(select(loaded_store, ["trg_003_recall_due_priya"])) == []


def test_opted_out_merchant_skipped(loaded_store):
    loaded_store.suppress_merchant("m_001_drmeera_dentist_delhi")
    assert ids(select(loaded_store, ["trg_001_research_digest_dentists"])) == []


def test_cap_is_twenty(loaded_store, ds):
    cands = select(loaded_store, [t["id"] for t in ds.triggers()])
    assert len(cands) == MAX_ACTIONS


def test_run_tick_action_shape_and_records_state(loaded_store):
    acts = run_tick(loaded_store, ["trg_001_research_digest_dentists"], NOW, llm=None)
    a = acts[0]
    assert set(a) == {"conversation_id", "merchant_id", "customer_id", "send_as", "trigger_id", "template_name",
                      "template_params", "body", "cta", "suppression_key", "rationale"}
    assert send_key("m_001_drmeera_dentist_delhi", None, {"suppression_key": "research:dentists:2026-W17"}) \
        in loaded_store.sent_keys
    assert loaded_store.has_conversation(a["conversation_id"])
    assert run_tick(loaded_store, ["trg_001_research_digest_dentists"], NOW, llm=None) == []


def test_run_tick_skips_low_value(loaded_store):
    loaded_store.put("trigger", "t_low", 1, {"id": "t_low", "kind": "ping", "urgency": 1, "payload": {},
                                             "merchant_id": "m_003_studio11_salon_hyderabad", "suppression_key": "low"})
    assert run_tick(loaded_store, ["t_low"], NOW, llm=None) == []
    assert not any(k.endswith("|low") for k in loaded_store.sent_keys)


def test_run_tick_respects_deadline_with_template_fallback(loaded_store, ds):
    class SlowLLM(FakeLLM):
        def complete(self, s, u):
            time.sleep(0.3)
            return None

    start = time.monotonic()
    acts = run_tick(loaded_store, [t["id"] for t in ds.triggers()], NOW, llm=SlowLLM([]), budget_s=0.5)
    assert time.monotonic() - start < 3 and len(acts) >= 15 and all(a["body"] for a in acts)


def test_concurrent_ticks_never_double_send(loaded_store):
    import threading
    results, barrier = [], threading.Barrier(6)

    def worker():
        barrier.wait()
        results.append(run_tick(loaded_store, ["trg_001_research_digest_dentists"], NOW, llm=None))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sum(len(r) for r in results) == 1
