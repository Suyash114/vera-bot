from tests.conftest import push

TICK_NOW = "2026-04-26T10:30:00Z"


def tick(client, ids, now=TICK_NOW):
    return client.post("/v1/tick", json={"now": now, "available_triggers": ids})


def reply(client, cid, msg, mid="m_001_drmeera_dentist_delhi", customer_id=None, turn=2):
    return client.post("/v1/reply", json={"conversation_id": cid, "merchant_id": mid, "customer_id": customer_id,
                                          "from_role": "customer" if customer_id else "merchant", "message": msg,
                                          "received_at": TICK_NOW, "turn_number": turn})


def test_healthz_ok(client):
    body = client.get("/v1/healthz").json()
    assert body["status"] == "ok" and body["contexts_loaded"] == {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}


def test_warmup_counts(warm_client):
    assert warm_client.get("/v1/healthz").json()["contexts_loaded"] == {
        "category": 5, "merchant": 50, "customer": 200, "trigger": 0}


def test_metadata_from_env(monkeypatch):
    from fastapi.testclient import TestClient

    from bot.app import create_app

    monkeypatch.setenv("TEAM_NAME", "Team Vera")
    monkeypatch.setenv("TEAM_MEMBERS", "A, B")
    body = TestClient(create_app(llm=None)).get("/v1/metadata").json()
    assert body["team_name"] == "Team Vera" and body["team_members"] == ["A", "B"]
    assert {"model", "approach", "version", "contact_email", "submitted_at"} <= set(body)


def test_context_ack_and_idempotency(client):
    first = push(client, "merchant", "m1", {"merchant_id": "m1"})
    assert first.status_code == 200 and first.json()["accepted"] and first.json()["ack_id"]
    assert push(client, "merchant", "m1", {"merchant_id": "m1"}).status_code == 200


def test_context_stale_version_409(client):
    push(client, "merchant", "m1", {}, version=5)
    r = push(client, "merchant", "m1", {}, version=4)
    assert r.status_code == 409 and r.json() == {"accepted": False, "reason": "stale_version", "current_version": 5}


def test_context_bad_scope_400(client):
    r = push(client, "planet", "p1", {})
    assert r.status_code == 400 and r.json()["reason"] == "invalid_scope"


def test_context_malformed_body_400(client):
    r = client.post("/v1/context", json={"scope": "merchant"})
    assert r.status_code == 400 and r.json()["accepted"] is False and r.json()["reason"] == "invalid_scope"


def test_tick_empty(client):
    assert tick(client, []).json() == {"actions": []}


def test_tick_sends_then_suppresses(warm_client, ds):
    t = ds.trigger("trg_001_research_digest_dentists")
    push(warm_client, "trigger", t["id"], t)
    acts = tick(warm_client, [t["id"]]).json()["actions"]
    assert len(acts) == 1 and acts[0]["send_as"] == "vera" and acts[0]["body"]
    assert tick(warm_client, [t["id"]], "2026-04-26T10:35:00Z").json()["actions"] == []


def test_tick_customer_scope_via_api(warm_client, ds):
    t = ds.trigger("trg_003_recall_due_priya")
    push(warm_client, "trigger", t["id"], t)
    a = tick(warm_client, [t["id"]]).json()["actions"][0]
    assert a["send_as"] == "merchant_on_behalf" and a["customer_id"] == "c_001_priya_for_m001"
    assert a["cta"] == "multi_choice_slot"


def test_tick_two_recipients_same_merchant(warm_client, ds):
    for tid in ("trg_001_research_digest_dentists", "trg_003_recall_due_priya"):
        push(warm_client, "trigger", tid, ds.trigger(tid))
    acts = tick(warm_client, ["trg_001_research_digest_dentists", "trg_003_recall_due_priya"]).json()["actions"]
    assert len(acts) == 2 and len({a["conversation_id"] for a in acts}) == 2


def test_tick_bad_now_falls_back_to_clock(warm_client, ds):
    t = ds.trigger("trg_010_ipl_match_delhi")
    push(warm_client, "trigger", t["id"], t)
    assert len(tick(warm_client, [t["id"]], now="not-a-date").json()["actions"]) == 1


def test_adaptive_merchant_v2_reflected_in_next_send(warm_client, ds):
    m = ds.merchant("m_002_bharat_dentist_mumbai")
    push(warm_client, "merchant", m["merchant_id"], {**m, "performance": {**m["performance"], "ctr": 0.011}}, version=2)
    t = ds.trigger("trg_004_perf_dip_bharat")
    push(warm_client, "trigger", t["id"], t)
    body = tick(warm_client, [t["id"]]).json()["actions"][0]["body"]
    assert "1.1%" in body


def test_reply_flow_after_tick(warm_client, ds):
    t = ds.trigger("trg_001_research_digest_dentists")
    push(warm_client, "trigger", t["id"], t)
    cid = tick(warm_client, [t["id"]]).json()["actions"][0]["conversation_id"]
    r = reply(warm_client, cid, "Yes please").json()
    assert r["action"] == "send" and r["body"] and r["rationale"]


def test_reply_unknown_conversation(warm_client):
    r = reply(warm_client, "conv_never_seen", "Ok lets do it. Whats next?").json()
    assert r["action"] == "send" and r["body"]


def test_reply_missing_fields_is_handled(client):
    r = client.post("/v1/reply", json={"message": "hi"})
    assert r.status_code == 200 and r.json()["action"] in ("send", "wait", "end")


def test_teardown_wipes_state(warm_client):
    assert warm_client.post("/v1/teardown").status_code == 200
    assert warm_client.get("/v1/healthz").json()["contexts_loaded"]["merchant"] == 0
