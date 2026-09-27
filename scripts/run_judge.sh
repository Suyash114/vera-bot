#!/usr/bin/env bash
# Run magicpin's judge_simulator.py against a bot WITHOUT editing the original file.
#   BOT_URL=http://localhost:8080 JUDGE_PROVIDER=anthropic JUDGE_API_KEY=... [JUDGE_MODEL=...] [SCENARIO=all] scripts/run_judge.sh
set -euo pipefail
cd "$(dirname "$0")/.."
: "${JUDGE_API_KEY:?set JUDGE_API_KEY (the judge LLM key)}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cp -R dataset "$tmp/dataset"
python3 - "$tmp/judge_simulator.py" <<'PY'
import os, re, sys
src = open("judge_simulator.py").read()
subs = {"BOT_URL": os.getenv("BOT_URL", "http://localhost:8080"), "LLM_PROVIDER": os.getenv("JUDGE_PROVIDER", "anthropic"),
        "LLM_API_KEY": os.environ["JUDGE_API_KEY"], "LLM_MODEL": os.getenv("JUDGE_MODEL", ""),
        "TEST_SCENARIO": os.getenv("SCENARIO", "all")}
for k, v in subs.items():
    src = re.sub(rf'^{k} = .*$', f'{k} = {v!r}', src, count=1, flags=re.M)
open(sys.argv[1], "w").write(src)
PY
python3 "$tmp/judge_simulator.py"
