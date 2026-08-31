"""Direct DeepSeek provider regressions (v4 family, OpenAI-compatible endpoint).

Covers the single-provider independence contract (DEVELOPMENT.md "Provider
Independence") and the two DeepSeek-specific wire classes established by live
probes (2026-09-01):

- the effort-carrying route: DeepSeek accepts the full reasoning-effort enum
  except the codex-only ``ultra`` tier (400 naming ``none…max``), so the lane
  carries the configured effort instead of dropping it like other generic
  compatible lanes;
- the reasoning-echo REQUIREMENT: tool-bearing requests must pass every
  assistant turn's ``reasoning_content`` back (v4-pro enforces with a 400;
  an explicit empty string satisfies the gate for turns produced elsewhere).
"""

import os

import pytest

from ouroboros import provider_models
from ouroboros.llm import LLMClient
from ouroboros.provider_models import (
    DEEPSEEK_BASE_URL,
    DEEPSEEK_DIRECT_DEFAULTS,
    DIRECT_PROVIDER_DEFAULTS,
    DIRECT_PROVIDER_REVIEW_ROLES,
    DIRECT_PROVIDER_SCOPE_DEFAULTS,
    migrate_model_value,
    normalize_model_identity,
    provider_for_model,
    provider_has_credentials,
    supports_vision,
)


def _clear_provider_env(monkeypatch):
    for key in (
        "OPENROUTER_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL",
        "OPENAI_COMPATIBLE_API_KEY", "OPENAI_COMPATIBLE_BASE_URL",
        "ANTHROPIC_API_KEY", "MINIMAX_API_KEY", "DEEPSEEK_API_KEY",
        "CLOUDRU_FOUNDATION_MODELS_API_KEY", "GIGACHAT_CREDENTIALS",
        "GIGACHAT_USER", "GIGACHAT_PASSWORD", "USE_LOCAL_MAIN",
    ):
        monkeypatch.delenv(key, raising=False)


class TestRegistry:
    def test_prefix_routes_direct(self):
        assert provider_for_model("deepseek::deepseek-v4-pro") == "deepseek"

    def test_slash_form_stays_openrouter(self):
        # deepseek/ is a REAL OpenRouter vendor namespace (the CI canary uses
        # it); only the :: prefix selects the direct route.
        assert provider_for_model("deepseek/deepseek-v4-pro") == "openrouter"
        from ouroboros.pricing import infer_api_key_type
        assert infer_api_key_type("deepseek/deepseek-v4-pro") == "openrouter"
        assert infer_api_key_type("deepseek::deepseek-v4-pro") == "deepseek"

    def test_credentials_mapping(self, monkeypatch):
        _clear_provider_env(monkeypatch)
        assert provider_has_credentials("deepseek") is False
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-x")
        assert provider_has_credentials("deepseek") is True
        assert provider_models.model_has_credentials("deepseek::deepseek-v4-flash") is True

    def test_direct_defaults_registered(self):
        assert DIRECT_PROVIDER_DEFAULTS["deepseek"] is DEEPSEEK_DIRECT_DEFAULTS
        assert DEEPSEEK_DIRECT_DEFAULTS["main"] == "deepseek::deepseek-v4-pro"
        assert DEEPSEEK_DIRECT_DEFAULTS["light"] == "deepseek::deepseek-v4-flash"
        assert DEEPSEEK_DIRECT_DEFAULTS["deep_self_review"] == "deepseek::deepseek-v4-pro"
        assert DIRECT_PROVIDER_REVIEW_ROLES["deepseek"] == ("main", "main", "main")
        assert DIRECT_PROVIDER_SCOPE_DEFAULTS["deepseek"] == "deepseek::deepseek-v4-pro"

    def test_migrate_and_normalize_round_trip(self):
        assert migrate_model_value("deepseek", "deepseek/deepseek-v4-pro") == "deepseek::deepseek-v4-pro"
        assert migrate_model_value("deepseek", "deepseek::deepseek-v4-pro") == "deepseek::deepseek-v4-pro"
        assert normalize_model_identity("deepseek::deepseek-v4-flash") == "deepseek/deepseek-v4-flash"

    def test_vision_narrow_prefix(self):
        assert supports_vision("deepseek::deepseek-v4-flash-vision-exp") is True
        assert supports_vision("deepseek::deepseek-v4-flash") is False
        assert supports_vision("deepseek/deepseek-chat") is False


class TestSingleProviderIndependence:
    def test_exclusive_direct_env_detection(self, monkeypatch):
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-x")
        from ouroboros.config import _exclusive_direct_remote_provider_env
        assert _exclusive_direct_remote_provider_env() == "deepseek"

    def test_review_and_scope_fallback_compile(self, monkeypatch):
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-x")
        monkeypatch.setenv("OUROBOROS_MODEL", "deepseek::deepseek-v4-pro")
        monkeypatch.setenv("OUROBOROS_MODEL_LIGHT", "deepseek::deepseek-v4-flash")
        from ouroboros.config import get_review_models, get_scope_review_models
        assert get_review_models() == ["deepseek::deepseek-v4-pro"] * 3
        assert get_scope_review_models() == ["deepseek::deepseek-v4-pro"]

    def test_startup_gate_accepts_deepseek_only(self):
        from ouroboros.server_runtime import (
            _exclusive_direct_remote_provider,
            has_remote_provider,
            has_startup_ready_provider,
        )
        settings = {"DEEPSEEK_API_KEY": "sk-x"}
        assert has_remote_provider(settings) is True
        assert has_startup_ready_provider(settings) is True
        assert _exclusive_direct_remote_provider(settings) == "deepseek"

    def test_local_only_review_route_sees_deepseek(self, monkeypatch):
        _clear_provider_env(monkeypatch)
        monkeypatch.setenv("USE_LOCAL_MAIN", "1")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-x")
        # A live remote DeepSeek credential means review slots must NOT be
        # forced onto the local route.
        assert provider_models.local_only_review_route_env() is False

    def test_secret_surfaces_cover_deepseek(self):
        from ouroboros.contracts.plugin_api import FORBIDDEN_SKILL_SETTINGS
        from ouroboros.secret_masking import MASKED_SECRET_SETTING_KEYS
        assert "DEEPSEEK_API_KEY" in FORBIDDEN_SKILL_SETTINGS
        assert "DEEPSEEK_API_KEY" in MASKED_SECRET_SETTING_KEYS
        from ouroboros.config import SETTINGS_DEFAULTS
        assert SETTINGS_DEFAULTS["DEEPSEEK_API_KEY"] == ""


class TestWireProjection:
    def _target(self, monkeypatch, model="deepseek::deepseek-v4-flash"):
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-x")
        return LLMClient()._resolve_remote_target(model)

    def test_resolve_remote_target_shape(self, monkeypatch):
        target = self._target(monkeypatch)
        assert target["provider"] == "deepseek"
        assert target["base_url"] == DEEPSEEK_BASE_URL
        assert target["api_key"] == "sk-x"
        assert target["usage_model"] == "deepseek/deepseek-v4-flash"
        assert target["carries_reasoning_effort"] is True
        assert target["supports_openrouter_extensions"] is False

    def test_effort_carried_with_max_tokens_carrier(self, monkeypatch):
        client = LLMClient()
        target = self._target(monkeypatch)
        kwargs = client._build_remote_kwargs(
            target, [{"role": "user", "content": "hi"}], "xhigh", 256, "auto", None, None,
        )
        assert kwargs["reasoning_effort"] == "xhigh"
        assert kwargs["max_tokens"] == 256
        assert "max_completion_tokens" not in kwargs

    def test_ultra_clamps_to_max(self, monkeypatch):
        client = LLMClient()
        target = self._target(monkeypatch)
        kwargs = client._build_remote_kwargs(
            target, [{"role": "user", "content": "hi"}], "ultra", 256, "auto", None, None,
        )
        assert kwargs["reasoning_effort"] == "max"

    def test_openai_still_carries_effort_via_flag(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        client = LLMClient()
        target = client._resolve_remote_target("openai::gpt-5.6-terra")
        assert target["carries_reasoning_effort"] is True
        kwargs = client._build_remote_kwargs(
            target, [{"role": "user", "content": "hi"}], "high", 128, "auto", None, None,
        )
        assert kwargs["reasoning_effort"] == "high"

    def test_reasoning_content_replayed_and_gap_filled(self, monkeypatch):
        client = LLMClient()
        target = self._target(monkeypatch)
        messages = [
            {"role": "user", "content": "weather?"},
            {  # DeepSeek's own turn: reasoning must ride back verbatim.
                "role": "assistant", "content": "",
                "reasoning_content": "call the tool",
                "tool_calls": [{"id": "c1", "type": "function",
                                "function": {"name": "t", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "18C"},
            {  # Foreign-model turn (no reasoning): the strict v4-pro gate
                # still demands the field — an explicit "" satisfies it.
                "role": "assistant", "content": "done",
            },
        ]
        kwargs = client._build_remote_kwargs(
            target, messages, "high", 256, "auto", None,
            [{"type": "function", "function": {"name": "t", "parameters": {"type": "object", "properties": {}}}}],
        )
        sent = [m for m in kwargs["messages"] if m.get("role") == "assistant"]
        assert sent[0]["reasoning_content"] == "call the tool"
        assert sent[1]["reasoning_content"] == ""

    def test_other_compatible_lanes_still_strip(self, monkeypatch):
        monkeypatch.setenv("CLOUDRU_FOUNDATION_MODELS_API_KEY", "k")
        client = LLMClient()
        target = client._resolve_remote_target("cloudru::zai-org/GLM-4.7")
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "x", "reasoning_content": "glm echo"},
        ]
        kwargs = client._build_remote_kwargs(
            target, messages, "high", 256, "auto", None, None,
        )
        sent = [m for m in kwargs["messages"] if m.get("role") == "assistant"]
        assert "reasoning_content" not in sent[0]
        assert "reasoning_effort" not in kwargs  # non-carrying lane unchanged

    def test_normalize_keeps_deepseek_reasoning_in_transcript(self):
        client = LLMClient(api_key="x")
        resp = {
            "id": "1",
            "choices": [{"message": {
                "role": "assistant", "content": "4",
                "reasoning_content": "2+2 -> 4",
            }}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2},
        }
        target = {
            "provider": "deepseek",
            "usage_model": "deepseek/deepseek-v4-flash",
            "supports_openrouter_extensions": False,
            "supports_generation_cost": False,
        }
        msg, _usage = client._normalize_remote_response(resp, target, skip_cost_fetch=True)
        assert msg["reasoning_content"] == "2+2 -> 4"

    def test_normalize_cached_tokens_top_level_fallback(self):
        client = LLMClient(api_key="x")
        resp = {
            "id": "1",
            "choices": [{"message": {"role": "assistant", "content": "ok"}}],
            # DeepSeek-shaped usage: top-level hit/miss beside an EMPTY
            # details block — the fallback must still account the cache hit.
            "usage": {
                "prompt_tokens": 446, "completion_tokens": 19,
                "prompt_tokens_details": {},
                "prompt_cache_hit_tokens": 384, "prompt_cache_miss_tokens": 62,
            },
        }
        target = {
            "provider": "deepseek",
            "usage_model": "deepseek/deepseek-v4-flash",
            "supports_openrouter_extensions": False,
            "supports_generation_cost": False,
        }
        _msg, usage = client._normalize_remote_response(resp, target, skip_cost_fetch=True)
        assert usage["cached_tokens"] == 384

    def test_cross_family_switch_strips_deepseek_reasoning(self):
        messages = [
            {"role": "assistant", "content": "x", "reasoning_content": "ds"},
        ]
        out = LLMClient.sanitize_reasoning_on_model_switch(
            messages, "deepseek::deepseek-v4-pro", "google/gemini-3.7-flash",
        )
        assert "reasoning_content" not in out[0]
        same = LLMClient.sanitize_reasoning_on_model_switch(
            messages, "deepseek::deepseek-v4-pro", "deepseek::deepseek-v4-flash",
        )
        assert same[0].get("reasoning_content") == "ds"

    def test_vision_images_survive_for_vision_variant_only(self, monkeypatch):
        client = LLMClient()
        image_msg = [{"role": "user", "content": [
            {"type": "text", "text": "what is this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]}]
        vision_target = self._target(monkeypatch, "deepseek::deepseek-v4-flash-vision-exp")
        kwargs = client._build_remote_kwargs(
            vision_target, image_msg, "high", 128, "auto", None, None,
        )
        blocks = kwargs["messages"][0]["content"]
        assert any(isinstance(b, dict) and b.get("type") == "image_url" for b in blocks)

        blind_target = self._target(monkeypatch, "deepseek::deepseek-v4-flash")
        kwargs = client._build_remote_kwargs(
            blind_target, image_msg, "high", 128, "auto", None, None,
        )
        blocks = kwargs["messages"][0]["content"]
        assert not any(isinstance(b, dict) and b.get("type") == "image_url" for b in blocks)

    def test_direct_openai_vision_judged_on_qualified_identity(self, monkeypatch):
        # Regression for the latent direct-lane class: the bare resolved id
        # never matched the slash-form vision prefixes, so every direct route
        # was captioned/placeholder'd regardless of real capability.
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        client = LLMClient()
        target = client._resolve_remote_target("openai::gpt-5.5")
        image_msg = [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]}]
        kwargs = client._build_remote_kwargs(
            target, image_msg, "high", 128, "auto", None, None,
        )
        blocks = kwargs["messages"][0]["content"]
        assert any(isinstance(b, dict) and b.get("type") == "image_url" for b in blocks)
