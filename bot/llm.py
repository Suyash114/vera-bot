"""Pluggable LLM clients. Every client returns text or None (never raises), and
caches by prompt hash so identical inputs always produce identical output.

Provider is chosen by env: LLM_PROVIDER = anthropic | openai | openai_compatible | none.
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections import OrderedDict

import anthropic
import httpx

log = logging.getLogger("vera.llm")

LLM_TIMEOUT_S = 8.0
CACHE_SIZE = 4096
_FALLBACK_MODELS = ("claude-opus-5", "claude-fable-5")


class NullLLM:
    provider, model = "none", "template-only"

    def complete(self, system: str, user: str) -> str | None:
        return None


class _CachedLLM:
    provider = "base"

    def __init__(self, model: str):
        self.model = model
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._lock = threading.Lock()

    def complete(self, system: str, user: str) -> str | None:
        key = hashlib.sha256(f"{self.provider}\0{self.model}\0{system}\0{user}".encode()).hexdigest()
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        try:
            text = self._call(system, user)
        except Exception as exc:  # network, 4xx/5xx, timeouts, malformed JSON
            log.warning("llm call failed (%s): %s", self.provider, exc)
            return None
        text = (text or "").strip() or None
        if text:
            with self._lock:
                self._cache[key] = text
                if len(self._cache) > CACHE_SIZE:
                    self._cache.popitem(last=False)
        return text

    def _call(self, system: str, user: str) -> str | None:
        raise NotImplementedError


class AnthropicLLM(_CachedLLM):
    provider = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-opus-5", base_url: str | None = None,
                 transport=None):
        super().__init__(model)
        http_client = anthropic.DefaultHttpxClient(transport=transport) if transport else None
        self._client = anthropic.Anthropic(api_key=api_key, base_url=base_url, max_retries=0,
                                           timeout=LLM_TIMEOUT_S, http_client=http_client)

    def _call(self, system: str, user: str) -> str | None:
        # Current models reject sampling params; determinism comes from the cache.
        extra = {}
        if self.model.startswith(_FALLBACK_MODELS):
            # Route policy declines to a fallback model server-side instead of losing the send.
            extra = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}
        resp = self._client.beta.messages.create(
            model=self.model,
            max_tokens=1024,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": "low"},
            **extra,
        )
        if resp.stop_reason == "refusal":
            return None
        return "".join(b.text for b in resp.content if b.type == "text")


class OpenAICompatLLM(_CachedLLM):
    provider = "openai_compatible"

    def __init__(self, api_key: str, model: str, base_url: str = "https://api.openai.com/v1", transport=None):
        super().__init__(model)
        self._url = base_url.rstrip("/") + "/chat/completions"
        self._client = httpx.Client(timeout=LLM_TIMEOUT_S, transport=transport,
                                    headers={"Authorization": f"Bearer {api_key}"})

    def _call(self, system: str, user: str) -> str | None:
        r = self._client.post(self._url, json={
            "model": self.model, "temperature": 0, "seed": 7, "max_tokens": 400,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        })
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


class LLMConfigError(RuntimeError):
    """Raised at startup when an LLM provider is configured but unusable."""


_PROVIDERS = ("anthropic", "openai", "openai_compatible", "none")


def from_env():
    """Build the LLM client from env. No provider configured -> template-only mode.
    A provider that IS configured but has no key (or an unknown name) fails at
    startup instead of silently degrading to templates."""
    provider = os.getenv("LLM_PROVIDER", "").strip().lower()
    model = os.getenv("LLM_MODEL", "").strip()
    base = os.getenv("LLM_BASE_URL", "").strip() or None
    if not provider:
        provider = "anthropic" if os.getenv("ANTHROPIC_API_KEY") else "none"
    # Only ever send a key to the vendor it belongs to.
    vendor_key = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}.get(provider)
    key = os.getenv("LLM_API_KEY") or (os.getenv(vendor_key) if vendor_key else None)
    if provider not in _PROVIDERS:
        raise LLMConfigError(f"Unknown LLM_PROVIDER {provider!r}; expected one of {', '.join(_PROVIDERS)}")

    if provider == "none" or not key:
        return NullLLM()

    if provider == "anthropic":
        return AnthropicLLM(key, model or "claude-opus-5", base_url=base)
    return OpenAICompatLLM(key, model or "gpt-4o", base or "https://api.openai.com/v1")
