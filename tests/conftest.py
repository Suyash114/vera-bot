import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
EXPANDED = ROOT / "expanded"


class Dataset:
    """Read-only view over the generated `expanded/` dataset."""

    def __init__(self, root: Path = EXPANDED):
        load = lambda d: [json.loads(p.read_text()) for p in sorted((root / d).glob("*.json"))]
        self.categories = {c["slug"]: c for c in load("categories")}
        self.merchants = {m["merchant_id"]: m for m in load("merchants")}
        self.customers = {c["customer_id"]: c for c in load("customers")}
        self.trigger_map = {t["id"]: t for t in load("triggers")}
        self.pairs = json.loads((root / "test_pairs.json").read_text())["pairs"]

    def category(self, slug):
        return self.categories[slug]

    def merchant(self, mid):
        return self.merchants[mid]

    def customer(self, cid):
        return self.customers.get(cid) if cid else None

    def trigger(self, tid):
        return self.trigger_map[tid]

    def triggers(self):
        return list(self.trigger_map.values())

    def test_pairs(self):
        return self.pairs

    def contexts_for(self, trigger_id):
        t = self.trigger(trigger_id)
        m = self.merchant(t["merchant_id"])
        return self.category(m["category_slug"]), m, t, self.customer(t.get("customer_id"))


@pytest.fixture(scope="session")
def ds():
    return Dataset()


@pytest.fixture
def loaded_store(ds):
    from bot.store import Store

    s = Store()
    for slug, c in ds.categories.items():
        s.put("category", slug, 1, c)
    for mid, m in ds.merchants.items():
        s.put("merchant", mid, 1, m)
    for cid, c in ds.customers.items():
        s.put("customer", cid, 1, c)
    for tid, t in ds.trigger_map.items():
        s.put("trigger", tid, 1, t)
    return s


@pytest.fixture
def client():
    from fastapi.testclient import TestClient

    from bot.app import create_app

    return TestClient(create_app(llm=None))


def push(client, scope, cid, payload, version=1):
    return client.post("/v1/context", json={"scope": scope, "context_id": cid, "version": version,
                                            "payload": payload, "delivered_at": "2026-04-26T10:00:00Z"})


@pytest.fixture
def warm_client(client, ds):
    for slug, c in ds.categories.items():
        push(client, "category", slug, c)
    for mid, m in ds.merchants.items():
        push(client, "merchant", mid, m)
    for cid, c in ds.customers.items():
        push(client, "customer", cid, c)
    return client


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    """Tests must never reach a real LLM, whatever the developer's shell exports."""
    for var in ("LLM_PROVIDER", "LLM_API_KEY", "LLM_MODEL", "LLM_BASE_URL", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
