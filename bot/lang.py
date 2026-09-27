"""Language preference (from context) and per-turn language detection."""
from __future__ import annotations

import re

_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_HINGLISH = {
    "haan", "han", "nahi", "nahin", "kar", "karo", "karna", "kardo", "hai", "hain", "ji", "bhai",
    "kya", "theek", "thik", "accha", "acha", "achha", "chalo", "chalega", "aap", "aapka", "aapke",
    "mujhe", "mera", "meri", "hum", "humein", "kab", "kaise", "kyun", "bahut", "shukriya", "dhanyavaad",
    "abhi", "baad", "mein", "se", "ko", "yeh", "woh", "bolo", "batao", "samajh", "zaroor", "bilkul",
}


def detect_lang(text: str) -> str:
    """'hi' for Devanagari, 'hinglish' for romanised Hindi, else 'en'."""
    if _DEVANAGARI.search(text or ""):
        return "hi"
    words = re.findall(r"[a-z]+", (text or "").lower())
    hits = sum(w in _HINGLISH for w in words)
    return "hinglish" if hits >= 2 or (hits == 1 and len(words) <= 3) else "en"


def wants_hinglish(merchant: dict | None, customer: dict | None) -> bool:
    if customer:
        pref = str((customer.get("identity") or {}).get("language_pref") or "").lower()
        return "hi" in re.findall(r"[a-z]+", pref) or "hindi" in pref
    langs = ((merchant or {}).get("identity") or {}).get("languages") or []
    return "hi" in langs
