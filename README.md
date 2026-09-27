# Vera bot — magicpin AI Challenge

A merchant-engagement bot that decides **what to say** with deterministic code and lets an LLM decide only **how to say it**, with every number checked against the pushed context before it's sent.

## Approach

```
context ─► facts (grounded, ranked, kind-aware) ─► plan (send_as, salutation, language, angle, single CTA, evidence)
        ─► LLM phrasing ─► validator ─┬─ ok ─► send
                                      └─ ungrounded number / taboo / 2+ CTAs / repeat
                                           ─► 1 retry with the problems ─► drop bad sentences ─► per-kind template
```

- **Facts, not prompts, carry the decision.** `bot/facts.py` resolves digest items by id (source, trial size), computes peer gaps (CTR vs peer average), days-until from the tick's `now`, customer relationship and slots, offers, aggregates and review themes, all humanised (no ids, bools or nulls). Facts are ranked by trigger family so a `perf_dip` leads with peer gaps and a `supply_alert` with batch numbers.
- **Planner** (`bot/planner.py`) fixes `send_as` (customer scope → `merchant_on_behalf`), the salutation (Dr. prefix for dentists, owner first name), Hinglish when the merchant or customer prefers `hi`, the compulsion angle, and one CTA from the harness enum (`binary_yes_no`, `binary_confirm_cancel`, `multi_choice_slot`, `open_ended`, `none`). The rationale carries an evidence map (`fact ← source field`).
- **No fabrication.** `bot/validator.py` rejects any number that isn't in the contexts, including derived forms (0.021 → 2.1%, 18:00 → 6pm, dates, integers ≤ 10).
- **Works without an LLM.** 26 per-kind templates plus a generic template for unseen kinds. All 100 seed triggers pass the validator in template mode.
- **Replies** (`bot/replies.py`) are rule-routed: auto-replies caught by canned phrasing (English + Hindi) or long repeats across conversations → nudge once, wait 24h, then end; "let's do it" → act immediately, with no qualifying question; explicit opt-out → end and suppress the merchant; abuse → apologise and offer STOP; off-topic (GST…) → decline and redirect; questions → grounded answer, with the merchant's text quoted as data (injection-safe) and validated.
- **Tick policy**: trusts `available_triggers` (expiry only breaks ties); one send per recipient (merchant, or each customer) per tick; checks consent; skips low-value pings; composes in parallel inside a 10 s budget, with the instant template for anything late.

## Run

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
python3 dataset/generate_dataset.py --seed-dir dataset --out expanded
.venv/bin/pytest                                       # 180 tests, no network
LLM_PROVIDER=anthropic LLM_API_KEY=... .venv/bin/uvicorn bot.app:app --port 8080 --workers 1
python -m scripts.make_submission --out submission.jsonl
BOT_URL=http://localhost:8080 JUDGE_PROVIDER=anthropic JUDGE_API_KEY=... scripts/run_judge.sh
```

Docker: `docker build -t vera-bot . && docker run -p 8080:8080 -e LLM_PROVIDER=anthropic -e LLM_API_KEY=... vera-bot`.
**Keep one worker**: all state is in process memory, as the brief allows.

| Env | Meaning |
|---|---|
| `LLM_PROVIDER` | `anthropic` (default model `claude-opus-5`) · `openai` · `openai_compatible` (+ `LLM_BASE_URL`) · `none` |
| `LLM_API_KEY`, `LLM_MODEL` | key and optional model override |
| `TEAM_NAME`, `TEAM_MEMBERS`, `CONTACT_EMAIL` | `/v1/metadata` identity |

## Playground (local UI)

A browser UI over the same bot: pick a merchant, send any trigger, see the composed message with the exact facts
it used (and where each came from), then reply as the merchant or customer to test the conversation.

```bash
PLAYGROUND=1 .venv/bin/uvicorn bot.app:app --port 8080 --workers 1 --env-file .env   # drop --env-file for template mode
open http://127.0.0.1:8080/
```

It is **off unless `PLAYGROUND=1`** (it can reset state), is same-origin only, rate-limited, and served with a strict
Content-Security-Policy. Never enable it on a deployment the judge is calling.

## Tradeoffs

- **Determinism**: current Claude models don't accept `temperature`, so determinism comes from a prompt-hash LRU cache (and temperature 0 with a fixed seed on OpenAI-style APIs). The same input gets the same output within a run.
- **Restraint over volume**: one message per recipient per tick, and none for low-value pings. Opt-outs suppress the merchant for the rest of the run.
- **Grounding over flourish**: a strong sentence with an unverifiable number is dropped. Case-study-style claims (e.g. "-12% covers on IPL Saturdays") are only made if the contexts contain them.

## What would have helped

Real merchant availability (slots) and prior send outcomes per template; category-level benchmarks for event effects (IPL, festivals); and a stated language preference per merchant rather than a list of spoken languages.
