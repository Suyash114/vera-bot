from bot.store import Store


def test_same_version_is_noop():
    s = Store()
    s.put("merchant", "m1", 1, {"a": 1})
    code, body = s.put("merchant", "m1", 1, {"a": 2})
    assert code == 200 and body["accepted"] is True
    assert s.get("merchant", "m1") == {"a": 1}


def test_higher_version_replaces():
    s = Store()
    s.put("merchant", "m1", 1, {"a": 1})
    s.put("merchant", "m1", 3, {"a": 3})
    assert s.get("merchant", "m1") == {"a": 3}


def test_stale_version_conflicts():
    s = Store()
    s.put("merchant", "m1", 3, {"a": 3})
    assert s.put("merchant", "m1", 2, {}) == (
        409, {"accepted": False, "reason": "stale_version", "current_version": 3})


def test_invalid_scope_rejected():
    code, body = Store().put("bogus", "x", 1, {})
    assert code == 400 and body["reason"] == "invalid_scope"


def test_counts_per_scope():
    s = Store()
    s.put("merchant", "m1", 1, {})
    s.put("trigger", "t1", 1, {})
    assert s.counts() == {"category": 0, "merchant": 1, "customer": 0, "trigger": 1}


def test_conversation_lazy_create_and_reuse():
    s = Store()
    c = s.conversation("conv_x", "m1", None)
    assert c.merchant_id == "m1" and c.bodies == [] and not c.ended
    assert s.conversation("conv_x", "m1", None) is c


def test_open_conversation_records_trigger():
    s = Store()
    c = s.open_conversation("conv_y", "m1", "c1", "t1", "hello")
    assert c.trigger_id == "t1" and c.bodies == ["hello"] and s.has_conversation("conv_y")


def test_reset_wipes_everything():
    s = Store()
    s.put("merchant", "m1", 1, {})
    s.sent_keys.add("k")
    s.open_conversation("c", "m1", None, "t", "b")
    s.reset()
    assert s.counts()["merchant"] == 0 and not s.sent_keys and not s.has_conversation("c")


def test_merchant_suppression():
    s = Store()
    s.suppress_merchant("m1")
    assert s.is_suppressed("m1") and not s.is_suppressed("m2")


def test_claim_is_atomic_under_threads():
    import threading
    s = Store()
    wins = []
    barrier = threading.Barrier(16)

    def worker():
        barrier.wait()
        wins.append(s.claim("key"))

    threads = [threading.Thread(target=worker) for _ in range(16)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert wins.count(True) == 1 and "key" in s.sent_keys


def test_release_frees_a_claim():
    s = Store()
    assert s.claim("k") and not s.claim("k")
    s.release("k")
    assert s.claim("k")


def test_auto_reply_counter_never_loses_increments():
    import threading
    s = Store()
    threads = [threading.Thread(target=lambda: [s.bump_auto_reply("m") for _ in range(200)]) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert s.bump_auto_reply("m") == 1601


def test_record_inbound_reports_long_repeats_only():
    s = Store()
    long_msg = "We are closed on Sundays, please visit Monday to Saturday."
    assert s.record_inbound("m", long_msg) is False
    assert s.record_inbound("m", long_msg) is True
    assert s.record_inbound("m", "yes") is False and s.record_inbound("m", "yes") is False
