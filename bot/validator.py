"""Checks a drafted body against the grounding and CTA rules before it is sent."""
from __future__ import annotations

import re

from .facts import extract_numbers

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def taboo_list(category: dict | None) -> list[str]:
    voice = (category or {}).get("voice") or {}
    raw = list(voice.get("vocab_taboo") or []) + list(voice.get("taboos") or [])
    return [re.sub(r"\s*\(.*?\)", "", t).strip().lower() for t in raw if t]


def _taboos_in(text: str, taboos: list[str]) -> list[str]:
    low = text.lower()
    return [t for t in taboos if t and re.search(rf"(?<!\w){re.escape(t)}(?!\w)", low)]


def _ungrounded(text: str, allowed: set[str]) -> list[str]:
    return sorted(n for n in extract_numbers(text) if n not in allowed)


def validate(body: str, *, allowed: set[str], taboos: list[str], prior: list[str]) -> list[str]:
    if not body or not body.strip():
        return ["empty"]
    problems = [f"ungrounded_number:{n}" for n in _ungrounded(body, allowed)]
    problems += [f"taboo:{t}" for t in _taboos_in(body, taboos)]
    low = body.lower()
    if body.count("?") > 1 or len(re.findall(r"\breply\b", low)) > 1:
        problems.append("multi_cta")
    if body.strip() in (p.strip() for p in prior):
        problems.append("repeat")
    return problems


def strip_violations(body: str, *, allowed: set[str], taboos: list[str]) -> str:
    """Drop whole sentences carrying an ungrounded number or a taboo word."""
    kept = [s for s in _SENTENCE_RE.split(body.strip())
            if not _ungrounded(s, allowed) and not _taboos_in(s, taboos)]
    return " ".join(kept).strip()
