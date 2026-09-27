import pytest
from fastapi.testclient import TestClient

from bot.app import create_app


@pytest.fixture
def pg():
    c = TestClient(create_app(llm=None, playground=True))
    assert c.post("/playground/api/reset").status_code == 200
    return c


def test_playground_off_by_default():
    c = TestClient(create_app(llm=None))
    assert c.get("/").status_code == 404
    assert c.get("/playground/api/catalog").status_code == 404
    assert c.post("/playground/api/reset").status_code == 404


def test_playground_enabled_by_env(monkeypatch):
    monkeypatch.setenv("PLAYGROUND", "1")
    assert TestClient(create_app(llm=None)).get("/").status_code == 200


def test_page_has_strict_csp_and_no_inline_script(pg):
    r = pg.get("/")
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp and "unsafe-inline" not in csp and "unsafe-eval" not in csp
    assert r.headers["x-content-type-options"] == "nosniff"
    html = r.text
    assert '<script src="/playground/static/playground.js"' in html
    assert "<script>" not in html and "onclick=" not in html and "style=" not in html


def test_static_assets_served_with_csp(pg):
    for path, ctype in (("/playground/static/playground.js", "javascript"),
                        ("/playground/static/playground.css", "css")):
        r = pg.get(path)
        assert r.status_code == 200 and ctype in r.headers["content-type"]
        assert "default-src 'self'" in r.headers["content-security-policy"]


def test_reset_loads_dataset(pg):
    assert pg.get("/v1/healthz").json()["contexts_loaded"] == {
        "category": 5, "merchant": 50, "customer": 200, "trigger": 100}


def test_info_reports_mode_without_secrets(pg):
    info = pg.get("/playground/api/info").json()
    assert info["mode"] == "template" and info["model"] == "template-only"
    assert not any("key" in k.lower() for k in info)


def test_catalog_lists_merchants_with_triggers(pg):
    cat = pg.get("/playground/api/catalog").json()
    assert len(cat["merchants"]) == 50
    meera = next(m for m in cat["merchants"] if m["merchant_id"] == "m_001_drmeera_dentist_delhi")
    assert meera["name"] == "Dr. Meera's Dental Clinic" and meera["category"] == "dentists"
    assert meera["locality"] == "Lajpat Nagar" and meera["city"] == "Delhi"
    kinds = {t["kind"] for t in meera["triggers"]}
    assert {"research_digest", "recall_due"} <= kinds
    recall = next(t for t in meera["triggers"] if t["id"] == "trg_003_recall_due_priya")
    assert recall["customer_name"] == "Priya" and recall["urgency"] == 3 and recall["label"]


def test_send_returns_message_with_explained_facts(pg):
    r = pg.post("/playground/api/send", json={"trigger_id": "trg_001_research_digest_dentists"}).json()
    a = r["action"]
    assert a["body"].startswith("Dr. Meera") and a["send_as"] == "vera" and a["conversation_id"]
    assert r["writer"] == "template" and r["angle"] == "source_cited"
    used = [f for f in r["facts"] if f["used"]]
    assert used and all({"key", "text", "source", "used"} <= set(f) for f in r["facts"])
    assert any(f["source"] == "category.digest" for f in used)


def test_send_same_trigger_twice_restarts_conversation(pg):
    body = {"trigger_id": "trg_010_ipl_match_delhi"}
    first = pg.post("/playground/api/send", json=body).json()["action"]
    second = pg.post("/playground/api/send", json=body).json()["action"]
    assert first["body"] == second["body"]


def test_send_then_reply_through_judge_endpoint(pg):
    a = pg.post("/playground/api/send", json={"trigger_id": "trg_002_compliance_dci_radiograph"}).json()["action"]
    r = pg.post("/v1/reply", json={"conversation_id": a["conversation_id"], "merchant_id": a["merchant_id"],
                                   "from_role": "merchant", "message": "yes send it"}).json()
    assert r["action"] == "send" and "checklist" in r["body"].lower()


def test_send_unknown_trigger_404(pg):
    assert pg.post("/playground/api/send", json={"trigger_id": "nope"}).status_code == 404


def test_send_customer_trigger(pg):
    r = pg.post("/playground/api/send", json={"trigger_id": "trg_003_recall_due_priya"}).json()
    assert r["action"]["send_as"] == "merchant_on_behalf" and r["action"]["customer_id"] == "c_001_priya_for_m001"


def test_playground_rate_limited():
    c = TestClient(create_app(llm=None, playground=True))
    codes = [c.post("/playground/api/reset").status_code for _ in range(12)]
    assert 429 in codes and codes[0] == 200


def test_trigger_labels_keep_acronyms(pg):
    labels = {t["kind"]: t["label"] for m in pg.get("/playground/api/catalog").json()["merchants"] for t in m["triggers"]}
    assert labels["cde_opportunity"] == "CDE opportunity"
    assert labels["ipl_match_today"] == "IPL match today"
    assert labels["gbp_unverified"] == "GBP unverified"
    assert labels["research_digest"] == "Research digest"
