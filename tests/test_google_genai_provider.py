"""v6.103.8 — Google Generative AI direct provider (escape route after task
c7862982 burned $11 on a 100k-token deep-review attempt against OpenRouter's
exhausted credit pool).

The provider is NOT OpenAI-compatible (different endpoint, different auth
shape, different response body), so all coverage is local: prefix recognition,
credential detection, target shape, message translation, response normalization,
and a single end-to-end HTTP test through ``_chat_google_genai`` with ``requests``
monkeypatched to capture URL / params / body — NO real API call, NO live key.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from ouroboros.llm import LLMClient
from ouroboros.provider_models import (
    GOOGLE_GENAI_DIRECT_DEFAULTS,
    migrate_model_value,
    normalize_model_identity,
    provider_for_model,
    provider_has_credentials,
)


# -------- prefix & credential registry -----------------------------------


def test_provider_prefix_recognized():
    assert provider_for_model("google_genai::gemini-3.7-flash") == "google_genai"
    assert provider_for_model("google_genai::gemini-3.5-flash-lite") == "google_genai"
    # Bare ``google/gemini-...`` is the OpenRouter id form; it stays openrouter.
    assert provider_for_model("google/gemini-3.7-flash") == "openrouter"


def test_provider_credential_detection_uses_ouroboros_env_key(monkeypatch):
    monkeypatch.delenv("OUROBOROS_GEMINI_API_KEY", raising=False)
    assert provider_has_credentials("google_genai") is False
    monkeypatch.setenv("OUROBOROS_GEMINI_API_KEY", "AIza-test-only-fixture")
    assert provider_has_credentials("google_genai") is True


def test_direct_defaults_include_deep_self_review():
    # Without a shipped deep_self_review default a direct-only install keeps an
    # unreachable OpenRouter-form id (the very failure mode v6.103.8 lands to
    # fix). Pin the shipped default so a future refactor cannot silently drop it.
    assert GOOGLE_GENAI_DIRECT_DEFAULTS["deep_self_review"] == "google_genai::gemini-3.7-flash"
    assert GOOGLE_GENAI_DIRECT_DEFAULTS["main"] == "google_genai::gemini-3.7-flash"


def test_migrate_model_value_lifts_openrouter_google_id():
    # Owner carrying the legacy ``google/gemini-...`` OpenRouter shape migrates
    # to the direct prefix when the provider is google_genai.
    assert (
        migrate_model_value("google_genai", "google/gemini-3.7-flash")
        == "google_genai::gemini-3.7-flash"
    )
    # Already-prefixed values pass through unchanged.
    assert (
        migrate_model_value("google_genai", "google_genai::gemini-3.7-flash")
        == "google_genai::gemini-3.7-flash"
    )


def test_normalize_model_identity_round_trips():
    assert normalize_model_identity("google_genai::gemini-3.7-flash") == "google/gemini-3.7-flash"


# -------- _resolve_remote_target ------------------------------------------


def test_resolve_remote_target_google_genai(monkeypatch):
    monkeypatch.setenv("OUROBOROS_GEMINI_API_KEY", "AIza-test-fixture")
    monkeypatch.setenv("OUROBOROS_GEMINI_BASE_URL", "https://generativelanguage.googleapis.com")

    target = LLMClient()._resolve_remote_target("google_genai::gemini-3.7-flash")

    assert target["provider"] == "google_genai"
    assert target["resolved_model"] == "gemini-3.7-flash"
    assert target["usage_model"] == "google/gemini-3.7-flash"
    # API key is read from the OWNER env var verbatim. The fake key is fine —
    # nothing else here ships it anywhere; the resolved target is a dict we
    # inspect, not a payload we transmit.
    assert target["api_key"] == "AIza-test-fixture"
    assert target["base_url"] == "https://generativelanguage.googleapis.com"
    assert target["gemini_native"] is True
    assert target["supports_openrouter_extensions"] is False
    assert target["supports_generation_cost"] is False


def test_resolve_remote_target_uses_default_base_url_when_env_unset(monkeypatch):
    monkeypatch.setenv("OUROBOROS_GEMINI_API_KEY", "AIza-test-fixture")
    monkeypatch.delenv("OUROBOROS_GEMINI_BASE_URL", raising=False)

    target = LLMClient()._resolve_remote_target("google_genai::gemini-3.7-flash")

    assert target["base_url"] == "https://generativelanguage.googleapis.com"


def test_resolve_remote_target_empty_api_key_when_env_missing(monkeypatch):
    monkeypatch.delenv("OUROBOROS_GEMINI_API_KEY", raising=False)
    target = LLMClient()._resolve_remote_target("google_genai::gemini-3.7-flash")
    # Empty string is the honest answer: provider_has_credentials is the
    # gate that reports ``False`` upstream; the target itself never raises.
    assert target["api_key"] == ""


# -------- _google_genai_contents -----------------------------------------


def test_google_genai_contents_lifts_system_message():
    system, contents = LLMClient._google_genai_contents(
        [
            {"role": "system", "content": "You are a careful reviewer."},
            {"role": "user", "content": "Hi"},
        ]
    )
    assert system == "You are a careful reviewer."
    assert contents == [{"role": "user", "parts": [{"text": "Hi"}]}]


def test_google_genai_contents_concatenates_multiple_system_messages():
    # OpenAI stacks several system messages; Gemini wants ONE systemInstruction.
    system, contents = LLMClient._google_genai_contents(
        [
            {"role": "system", "content": "First half."},
            {"role": "system", "content": "Second half."},
            {"role": "user", "content": "Hi"},
        ]
    )
    assert system == "First half.\n\nSecond half."
    assert contents == [{"role": "user", "parts": [{"text": "Hi"}]}]


def test_google_genai_contents_skips_tool_and_function_messages():
    _, contents = LLMClient._google_genai_contents(
        [
            {"role": "user", "content": "Hi"},
            {"role": "tool", "content": "tool result"},
            {"role": "function", "content": "function result"},
            {"role": "assistant", "content": "Hello back"},
        ]
    )
    # Tool/function messages are skipped (would need ``functionResponse``
    # parts with the function NAME — we don't carry it through the OpenAI
    # tool-call-id-only contract). Assistant becomes ``model``.
    assert contents == [
        {"role": "user", "parts": [{"text": "Hi"}]},
        {"role": "model", "parts": [{"text": "Hello back"}]},
    ]


def test_google_genai_contents_injects_placeholder_on_empty():
    # Gemini 400s on an empty contents array; inject a minimal one so the
    # owner sees Google's actual error rather than an Ouroboros crash.
    _, contents = LLMClient._google_genai_contents([])
    assert contents == [{"role": "user", "parts": [{"text": "(empty input)"}]}]


# -------- _normalize_google_genai_response --------------------------------


def test_normalize_google_genai_response_text_and_usage():
    resp = {
        "candidates": [
            {
                "content": {"parts": [{"text": "Hello back"}], "role": "model"},
                "finishReason": "STOP",
            }
        ],
        "usageMetadata": {
            "promptTokenCount": 11,
            "candidatesTokenCount": 1,
            "thoughtsTokenCount": 87,
            "totalTokenCount": 99,
        },
        "modelVersion": "gemini-3.7-flash",
    }
    target = {
        "provider": "google_genai",
        "resolved_model": "gemini-3.7-flash",
        "usage_model": "google/gemini-3.7-flash",
    }
    message, usage = LLMClient()._normalize_google_genai_response(resp, target)

    assert message["role"] == "assistant"
    assert message["content"] == "Hello back"
    assert message["stop_reason"] == "STOP"
    assert usage["prompt_tokens"] == 11
    assert usage["completion_tokens"] == 1
    assert usage["thoughts_tokens"] == 87
    assert usage["total_tokens"] == 99
    assert usage["provider"] == "google_genai"
    assert usage["resolved_model"] == "google/gemini-3.7-flash"
    # Cost is unknown on this lane; the honest answer is ``None`` + ``cost_final=False``.
    assert usage["cost"] is None
    assert usage["cost_final"] is False


def test_normalize_google_genai_response_missing_candidate():
    # Safety block / quota exhausted: Gemini returns ``promptFeedback``
    # instead of a candidate. The classifier should produce an empty message
    # with no stop_reason — downstream code decides the next step.
    resp = {"promptFeedback": {"blockReason": "SAFETY"}}
    message, usage = LLMClient()._normalize_google_genai_response(
        resp, {"provider": "google_genai", "resolved_model": "gemini-3.7-flash"},
    )
    assert message["content"] == ""
    assert "stop_reason" not in message
    assert usage["prompt_tokens"] == 0


# -------- _chat_google_genai end-to-end (mocked HTTP) ---------------------


class _FakeResponse:
    """Minimal stand-in for ``requests.Response`` that the chat method consumes."""

    def __init__(self, body: Dict[str, Any], status_code: int = 200) -> None:
        self._body = body
        self.status_code = status_code
        self.text = json_dumps_safe(body)
        self.url = ""

    def json(self) -> Dict[str, Any]:
        return self._body


def json_dumps_safe(obj: Any) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False)


def test_chat_google_genai_sends_correct_request_and_parses_response(monkeypatch):
    """Single end-to-end pass: capture URL / params / body, return a fake
    response, and assert the parsed (message, usage) shape.

    NO real HTTP call. NO real API key (the test fixture uses an obviously
    fake value that the mock never transmits)."""
    captured: Dict[str, Any] = {}

    def _fake_post(url, params, headers, json, timeout):
        captured["url"] = url
        captured["params"] = dict(params)
        captured["headers"] = dict(headers)
        captured["json"] = json
        captured["timeout"] = timeout
        resp = _FakeResponse({
            "candidates": [
                {
                    "content": {"parts": [{"text": "pong"}], "role": "model"},
                    "finishReason": "STOP",
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 11,
                "candidatesTokenCount": 1,
                "thoughtsTokenCount": 87,
                "totalTokenCount": 99,
            },
            "modelVersion": "gemini-3.7-flash",
        })
        resp.url = url
        return resp

    monkeypatch.setenv("OUROBOROS_GEMINI_API_KEY", "AIza-test-fixture")
    monkeypatch.setenv("OUROBOROS_GEMINI_BASE_URL", "https://generativelanguage.googleapis.com")

    # The chat path uses ``requests.post`` directly when ``no_proxy=False``
    # (the no_proxy branch wraps a ``requests.Session``). Monkeypatch both
    # the module-level ``post`` and the ``Session`` ctor so either branch
    # lands on the fake and the test stays network-free.
    import requests
    monkeypatch.setattr(requests, "post", _fake_post)

    class _FakeSession:
        trust_env = True

        def post(self, url, params, headers, json, timeout):
            return _fake_post(url, params, headers, json, timeout)

    monkeypatch.setattr(requests, "Session", _FakeSession)

    client = LLMClient()
    target = client._resolve_remote_target("google_genai::gemini-3.7-flash")
    message, usage = client._chat_google_genai(
        target,
        [{"role": "user", "content": "respond with the single word 'pong'"}],
        None,
        "high",
        1024,
        "auto",
        temperature=0.0,
        no_proxy=False,
        timeout=30.0,
    )

    # URL shape: documented v1beta ``generateContent`` with the model id in
    # the path; auth via the documented ``?key=`` query param.
    assert captured["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.7-flash:generateContent"
    )
    assert captured["params"] == {"key": "AIza-test-fixture"}
    assert captured["headers"]["content-type"] == "application/json"
    assert captured["timeout"] == 30.0

    # Payload shape: Gemini wants ``contents`` with role/parts and a sibling
    # ``generationConfig.maxOutputTokens``.
    assert captured["json"]["contents"] == [
        {"role": "user", "parts": [{"text": "respond with the single word 'pong'"}]}
    ]
    assert captured["json"]["generationConfig"]["maxOutputTokens"] == 1024
    assert captured["json"]["generationConfig"]["temperature"] == 0.0

    # Parsed response: the model returned "pong" with the expected usage shape.
    assert message["content"] == "pong"
    assert message["stop_reason"] == "STOP"
    assert usage["thoughts_tokens"] == 87
    assert usage["provider"] == "google_genai"


def test_chat_google_genai_refuses_tool_calls():
    """Tool calling on the direct Gemini route requires a separate schema-
    translation pass; the v6.103.8 lane is text-only. Tool-bearing requests
    surface as a typed refusal, not a silent stub."""
    import pytest as _pytest

    client = LLMClient()
    target = {
        "provider": "google_genai",
        "resolved_model": "gemini-3.7-flash",
        "api_key": "AIza-test-fixture",
        "base_url": "https://generativelanguage.googleapis.com",
    }
    with _pytest.raises(ValueError, match="does not support tool calls"):
        client._chat_google_genai(
            target,
            [{"role": "user", "content": "hi"}],
            [{"type": "function", "function": {"name": "f", "parameters": {}}}],
            "high",
            1024,
            "auto",
        )


def test_chat_google_genai_refuses_when_api_key_missing():
    """The target dict carries ``api_key=""`` when the env is unset;
    ``_chat_google_genai`` refuses with a typed ValueError rather than
    emitting a request that would 401."""
    client = LLMClient()
    target = {
        "provider": "google_genai",
        "resolved_model": "gemini-3.7-flash",
        "api_key": "",
        "base_url": "https://generativelanguage.googleapis.com",
    }
    with pytest.raises(ValueError, match="OUROBOROS_GEMINI_API_KEY"):
        client._chat_google_genai(
            target,
            [{"role": "user", "content": "hi"}],
            None,
            "high",
            1024,
            "auto",
        )


# -------- deep_self_review cap (regression for ibl-be9ba2d99b25) ----------


def test_deep_self_review_output_cap_lowered_from_default():
    """Regression for the rot captured by ibl-be9ba2d99b25: a 100k-token
    deep-review request 402'd for $11 of pure waste. The cap MUST stay
    conservative — any future bump carries a typed rationale in the same
    commit that lifts it, not a silent constant revert.
    """
    from ouroboros.deep_self_review import _DEEP_MAX_OUTPUT_TOKENS

    assert _DEEP_MAX_OUTPUT_TOKENS <= 15_000
    assert _DEEP_MAX_OUTPUT_TOKENS >= 1_000