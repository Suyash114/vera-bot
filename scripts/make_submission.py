"""Write submission.jsonl: one composed message per canonical test pair.

    python -m scripts.make_submission --out submission.jsonl [--now 2026-04-26T10:00:00Z]

Uses the LLM configured by env (LLM_PROVIDER, LLM_API_KEY, ...); template-only without one.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from bot.composer import compose
from bot.facts import now_or
from bot.llm import from_env

ROOT = Path(__file__).resolve().parent.parent
EXPANDED = ROOT / "expanded"


def _load(kind: str, key: str) -> dict:
    return {d[key]: d for d in (json.loads(p.read_text()) for p in sorted((EXPANDED / kind).glob("*.json")))}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "submission.jsonl"))
    ap.add_argument("--now", default=None, help="ISO time used for days-until maths (default: now)")
    args = ap.parse_args(argv)
    now = now_or(args.now)

    categories = _load("categories", "slug")
    merchants = _load("merchants", "merchant_id")
    customers = _load("customers", "customer_id")
    triggers = _load("triggers", "id")
    pairs = json.loads((EXPANDED / "test_pairs.json").read_text())["pairs"]
    llm = from_env()

    with open(args.out, "w") as fh:
        for pair in pairs:
            trigger = triggers[pair["trigger_id"]]
            merchant = merchants[pair["merchant_id"]]
            customer = customers.get(pair.get("customer_id") or trigger.get("customer_id") or "")
            msg = compose(categories.get(merchant.get("category_slug"), {}), merchant, trigger, customer,
                          now=now, llm=llm)
            row = {"test_id": pair["test_id"], **{k: msg[k] for k in
                                                   ("body", "cta", "send_as", "suppression_key", "rationale")}}
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
