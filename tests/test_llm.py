import json

import httpx
import httpx2

from bot.llm import AnthropicLLM, NullLLM, OpenAICompatLLM, from_env


def anthropic_transport(captured, text="Hi there", stop_reason="end_turn", status=200):
    def handler(request):
        captured.append(json.loads(request.content))
        if status != 200:
            return httpx2.Response(status, json={"type": "error", "error": {"type": "overloaded_error", "message": "x"}})
        return httpx2.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5",
            "content": [{"type": "text", "text": text}], "stop_reason": stop_reason,
            "stop_details": None, "usage": {"input_tokens": 1, "output_tokens": 1}})
    return httpx2.MockTransport(handler)


def test_anthropic_request_shape_and_text():
    captured = []
    llm = AnthropicLLM(api_key="k", model="claude-opus-5", transport=anthropic_transport(captured))
    assert llm.complete("sys", "user") == "Hi there"
    body = captured[0]
    assert body["model"] == "claude-opus-5" and body["system"] == "sys"
    assert body["messages"] == [{"role": "user", "content": "user"}]
    assert body["output_config"] == {"effort": "low"} and body["fallbacks"] == "default"
    assert "temperature" not in body


def test_anthropic_refusal_returns_none():
    llm = AnthropicLLM(api_key="k", model="claude-opus-5", transport=anthropic_transport([], stop_reason="refusal"))
    assert llm.complete("s", "u") is None


def test_anthropic_http_error_returns_none():
    llm = AnthropicLLM(api_key="k", model="claude-opus-5", transport=anthropic_transport([], status=529))
    assert llm.complete("s", "u") is None


def test_cache_makes_identical_prompts_one_call():
    captured = []
    llm = AnthropicLLM(api_key="k", model="claude-opus-5", transport=anthropic_transport(captured))
    assert llm.complete("s", "u") == llm.complete("s", "u")
    assert len(captured) == 1


def test_openai_compat_request_shape():
    captured = []

    def handler(request):
        captured.append((str(request.url), json.loads(request.content)))
        return httpx.Response(200, json={"choices": [{"message": {"content": "Hello"}}]})

    llm = OpenAICompatLLM(api_key="k", model="m1", base_url="https://gw.example/v1",
                          transport=httpx.MockTransport(handler))
    assert llm.complete("s", "u") == "Hello"
    url, body = captured[0]
    assert url == "https://gw.example/v1/chat/completions"
    assert body["temperature"] == 0 and body["messages"][0] == {"role": "system", "content": "s"}


def test_openai_compat_error_returns_none():
    llm = OpenAICompatLLM(api_key="k", model="m", transport=httpx.MockTransport(lambda r: httpx.Response(429)))
    assert llm.complete("s", "u") is None


def test_from_env_selects_provider(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert isinstance(from_env(), NullLLM)
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("LLM_BASE_URL", "https://gw.example/v1")
    assert isinstance(from_env(), OpenAICompatLLM)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    llm = from_env()
    assert isinstance(llm, AnthropicLLM) and llm.model == "claude-opus-5"


def test_null_llm_returns_none():
    assert NullLLM().complete("s", "u") is None


def test_fallbacks_only_for_models_that_support_them():
    captured = []
    llm = AnthropicLLM(api_key="k", model="claude-haiku-4-5", transport=anthropic_transport(captured))
    llm.complete("s", "u")
    assert "fallbacks" not in captured[0]


def test_configured_provider_without_key_fails_fast(monkeypatch):
    import pytest
    from bot.llm import LLMConfigError
    monkeypatch.setenv("LLM_PROVIDER", "openai_compatible")
    with pytest.raises(LLMConfigError, match="LLM_API_KEY"):
        from_env()


def test_unknown_provider_fails_fast(monkeypatch):
    import pytest
    from bot.llm import LLMConfigError
    monkeypatch.setenv("LLM_PROVIDER", "gemni")
    monkeypatch.setenv("LLM_API_KEY", "k")
    with pytest.raises(LLMConfigError, match="gemni"):
        from_env()


def test_explicit_none_provider_is_template_mode(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "none")
    assert isinstance(from_env(), NullLLM)
