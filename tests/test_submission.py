import json

from scripts.make_submission import main


def test_submission_has_30_complete_rows(tmp_path):
    out = tmp_path / "submission.jsonl"
    main(["--out", str(out), "--now", "2026-04-26T10:00:00Z"])
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert [r["test_id"] for r in rows] == [f"T{i:02d}" for i in range(1, 31)]
    for r in rows:
        assert set(r) == {"test_id", "body", "cta", "send_as", "suppression_key", "rationale"}
        assert r["body"].strip() and r["rationale"].strip()


def test_submission_is_deterministic(tmp_path):
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    main(["--out", str(a), "--now", "2026-04-26T10:00:00Z"])
    main(["--out", str(b), "--now", "2026-04-26T10:00:00Z"])
    assert a.read_text() == b.read_text()
