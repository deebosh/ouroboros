"""Provider target resolution, client construction and route affinity.

Which provider a model id belongs to, which credentials and base url that route
uses, which client object serves it (cached, proxy-free, async or local), and
which affinity key keeps repeat calls on one warm upstream — all of it is the
same question: where does this call go. The capability probe lives here because
it answers that question about a route without being a chat turn.
"""


from __future__ import annotations


import hashlib


import json


import os


from typing import Any, Dict, List, Optional, Tuple


from ouroboros.llm_attempt import (
    _attempt_request,
    _candidate_before_dispatch,
    _execute_candidate,
    _physical_candidate,
)


from ouroboros.provider_models import (
    PROVIDER_PREFIXES,
    normalize_anthropic_model_id,
    normalize_model_identity,
    resolve_minimax_base_url,
)


from ouroboros.usage_accounting import UsageScope, current_usage_scope, usage_scope


_OR_PROVIDER_PRESETS = {
    # Same-model provider failover versus reproducible provider pinning.
    "resilience": {"allow_fallbacks": True},
    "repro": {"allow_fallbacks": False},
}


def _resolve_or_provider() -> Dict[str, Any]:
    """Resolve ``OUROBOROS_OR_PROVIDER`` (a preset name or a raw JSON object) into an
    OpenRouter ``provider`` routing dict. Empty/unset/invalid -> ``{}`` (no routing)."""
    raw = (os.environ.get("OUROBOROS_OR_PROVIDER") or "").strip()
    if not raw:
        return {}
    preset = _OR_PROVIDER_PRESETS.get(raw.lower())
    if preset is not None:
        return dict(preset)
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


class _ProviderRoutingMixin:
    """Provider targets, client factories, affinity keys and the window probe."""

    @staticmethod
    def _prompt_cache_identity(model_id: str, messages: List[Dict[str, Any]]) -> str:
        """Stable, credential-free affinity key for one policy prefix.

        Ouroboros' Main context places stable policy/governance in the first
        system text block and dynamic evidence last.  Hash only that stable
        prefix plus the normalized model identity, so changing task evidence
        does not fragment the provider cache while different policies cannot
        collide.  Routes without a leading system prefix simply opt out.
        """
        if not messages or str(messages[0].get("role") or "") != "system":
            return ""
        content = messages[0].get("content")
        stable_prefix = ""
        if isinstance(content, str):
            stable_prefix = content
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    stable_prefix = text
                    break
        if not stable_prefix.strip():
            return ""
        identity = normalize_model_identity(model_id) or str(model_id or "").strip()
        digest = hashlib.sha256(
            f"{identity}\0{stable_prefix}".encode("utf-8")
        ).hexdigest()[:32]
        return f"ouroboros-{digest}"

    @staticmethod
    def _explicit_cache_affinity_identity(model_id: str, cache_affinity: str) -> str:
        """Caller-declared session affinity: stable across rounds of one logical
        surface (e.g. ``plan_review:<task>``) so OpenRouter sticky routing keeps
        repeat calls on the same upstream and its prompt cache warm. The model
        identity is folded in so two models never share a session bucket; the
        caller key deliberately excludes slot ids so N same-model reviewer slots
        keep today's provider-concentration behavior."""
        affinity = str(cache_affinity or "").strip()
        if not affinity:
            return ""
        identity = normalize_model_identity(model_id) or str(model_id or "").strip()
        digest = hashlib.sha256(
            f"{identity}\0{affinity}".encode("utf-8")
        ).hexdigest()[:32]
        return f"ouroboros-session-{digest}"

    @classmethod
    def _openrouter_session_identity(
        cls,
        model_id: str,
        messages: List[Dict[str, Any]],
    ) -> str:
        """Conversation-stable OpenRouter affinity, bounded well below 256 chars."""
        prefix_identity = cls._prompt_cache_identity(model_id, messages)
        if not prefix_identity:
            return ""
        first_user: Any = ""
        for message in messages:
            if str(message.get("role") or "") == "user":
                first_user = message.get("content")
                break
        serialized_user = json.dumps(
            first_user,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        digest = hashlib.sha256(
            f"{prefix_identity}\0{serialized_user}".encode("utf-8")
        ).hexdigest()[:32]
        return f"ouroboros-session-{digest}"

    @staticmethod
    def _parse_provider_model(model: str) -> Tuple[str, str]:
        model_name = str(model or "").strip()
        for prefix, provider in PROVIDER_PREFIXES:
            if model_name.startswith(prefix):
                return provider, model_name[len(prefix):].strip()
        return "openrouter", model_name

    @staticmethod
    def _qualified_model_name(provider: str, resolved_model: str) -> str:
        if provider == "openrouter":
            return resolved_model
        if provider == "openai":
            return f"openai/{resolved_model}"
        if provider == "anthropic":
            return f"anthropic/{resolved_model}"
        if provider == "cloudru":
            return f"cloudru/{resolved_model}"
        if provider == "gigachat":
            return f"gigachat/{resolved_model}"
        if provider == "minimax":
            return f"minimax/{resolved_model}"
        return f"openai-compatible/{resolved_model}"

    def _resolve_remote_target(self, model: str) -> Dict[str, Any]:
        provider, resolved_model = self._parse_provider_model(model)
        usage_model = self._qualified_model_name(provider, resolved_model)

        if provider == "openai":
            return {
                "provider": provider,
                "resolved_model": resolved_model,
                "usage_model": usage_model,
                "api_key": os.environ.get("OPENAI_API_KEY", ""),
                "base_url": "https://api.openai.com/v1",
                "default_headers": {},
                "supports_openrouter_extensions": False,
                "supports_generation_cost": False,
            }

        if provider == "anthropic":
            resolved_model = normalize_anthropic_model_id(resolved_model)
            return {
                "provider": provider,
                "resolved_model": resolved_model,
                "usage_model": self._qualified_model_name(provider, resolved_model),
                "api_key": os.environ.get("ANTHROPIC_API_KEY", ""),
                "base_url": "https://api.anthropic.com/v1",
                "default_headers": {},
                "supports_openrouter_extensions": False,
                "supports_generation_cost": False,
            }

        if provider == "minimax":
            return {
                "provider": provider,
                "resolved_model": resolved_model,
                "usage_model": usage_model,
                "api_key": os.environ.get("MINIMAX_API_KEY", ""),
                "base_url": resolve_minimax_base_url(os.environ.get("MINIMAX_REGION", "")),
                "default_headers": {},
                "supports_openrouter_extensions": False,
                "supports_generation_cost": False,
            }

        if provider == "cloudru":
            return {
                "provider": provider,
                "resolved_model": resolved_model,
                "usage_model": usage_model,
                "api_key": os.environ.get("CLOUDRU_FOUNDATION_MODELS_API_KEY", ""),
                "base_url": (
                    os.environ.get("CLOUDRU_FOUNDATION_MODELS_BASE_URL", "") or ""
                ).strip() or "https://foundation-models.api.cloud.ru/v1",
                "default_headers": {},
                "supports_openrouter_extensions": False,
                "supports_generation_cost": False,
            }

        if provider == "gigachat":
            # GigaChat is NOT OpenAI-compatible — the `gigachat` library owns
            # the transport and auth. Everything is env-configurable: `api_key`
            # holds the authorization key (base64 client_id:secret) for the OAuth
            # flow, OR user/password for basic auth against an internal endpoint.
            # base_url/scope/verify are carried for the `_chat_gigachat` path.
            verify_raw = (os.environ.get("GIGACHAT_VERIFY_SSL_CERTS", "") or "").strip().lower()
            return {
                "provider": provider,
                "resolved_model": resolved_model,
                "usage_model": usage_model,
                "api_key": os.environ.get("GIGACHAT_CREDENTIALS", ""),
                "user": (os.environ.get("GIGACHAT_USER", "") or "").strip(),
                "password": os.environ.get("GIGACHAT_PASSWORD", "") or "",
                "base_url": (
                    os.environ.get("GIGACHAT_BASE_URL", "") or ""
                ).strip() or "https://api.giga.chat/v1",
                "scope": (os.environ.get("GIGACHAT_SCOPE", "") or "").strip() or "GIGACHAT_API_PERS",
                "verify_ssl_certs": verify_raw not in ("0", "false", "no", "off"),
                "default_headers": {},
                "supports_openrouter_extensions": False,
                "supports_generation_cost": False,
            }

        if provider == "openai-compatible":
            compatible_key = (os.environ.get("OPENAI_COMPATIBLE_API_KEY", "") or "").strip()
            compatible_base_url = (os.environ.get("OPENAI_COMPATIBLE_BASE_URL", "") or "").strip()
            legacy_base_url = (os.environ.get("OPENAI_BASE_URL", "") or "").strip()
            legacy_key = (os.environ.get("OPENAI_API_KEY", "") or "").strip()
            return {
                "provider": provider,
                "resolved_model": resolved_model,
                "usage_model": usage_model,
                "api_key": compatible_key or legacy_key,
                "base_url": compatible_base_url or legacy_base_url,
                "default_headers": {},
                "supports_openrouter_extensions": False,
                "supports_generation_cost": False,
            }

        current_api_key = self._api_key_override
        if current_api_key is None:
            current_api_key = os.environ.get("OPENROUTER_API_KEY", "")
        return {
            "provider": "openrouter",
            "resolved_model": resolved_model,
            "usage_model": usage_model,
            "api_key": current_api_key,
            "base_url": self._base_url,
            "default_headers": {
                "HTTP-Referer": "https://ouroboros.local/",
                "X-Title": "Ouroboros",
            },
            "supports_openrouter_extensions": True,
            "supports_generation_cost": True,
        }

    def _get_client(self):
        target = self._resolve_remote_target("openrouter::")
        return self._get_remote_client(target)

    def _get_remote_client(self, target: Dict[str, Any]):
        base_url = str(target.get("base_url") or "")
        api_key = str(target.get("api_key") or "")
        headers_dict = dict(target.get("default_headers") or {})
        headers = tuple(sorted((str(k), str(v)) for k, v in headers_dict.items()))
        cache_key = (str(target.get("provider") or ""), base_url, api_key, headers)

        client = self._remote_clients.get(cache_key)
        if client is None:
            from openai import OpenAI

            kwargs: Dict[str, Any] = {
                "api_key": api_key,
                "max_retries": 0,
            }
            if base_url:
                kwargs["base_url"] = base_url
            if headers_dict:
                kwargs["default_headers"] = headers_dict
            client = OpenAI(**kwargs)
            self._remote_clients[cache_key] = client
        return client

    def probe_oversized_context(
        self, model: str, content: str, *,
        base_url: str = "", max_output_tokens: int = 8, timeout: float = 20.0,
        api_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Capability probe: send ONE deliberately over-window request on the model's
        OpenAI-compatible route and report the RAW outcome for window classification.

        This is a capability check, NOT a chat turn: it deliberately bypasses the
        chat-round path (a probe must not count as an LLM round) but still records its
        physical provider attempt in monetary accounting, and NEVER raises. The expected free case is a 4xx pre-inference
        reject whose body carries the limit; a rare 200-accept returns the echo +
        prompt_tokens (the caller treats it as possibly-paid -> owner-ack, never a
        silent confirm). When an explicit ``base_url`` is given (Settings save/toggle
        passes the route being fingerprinted) it overrides the env-resolved one so a
        route change verifies the NEW endpoint. Returns
        ``{ok, status_code, body, echoed_text, usage_prompt}``.
        """
        try:
            target = self._resolve_remote_target(model)
            if str(base_url or "").strip():
                target = {**target, "base_url": str(base_url).strip()}
            if api_key is not None:
                target = {**target, "api_key": api_key}
            oai = self._get_remote_client(target)
            # resolved_model is the provider REQUEST model ("gpt-5.5"), not the
            # slash-qualified usage/tracking name the API would reject.
            resolved_model = str(target.get("resolved_model") or model.split("::")[-1])
            provider = str(target.get("provider") or "")
        except Exception as exc:  # pragma: no cover - setup failure -> fail-closed
            return {"ok": False, "status_code": None, "body": f"probe setup failed: {type(exc).__name__}",
                    "echoed_text": "", "usage_prompt": 0}
        # Direct OpenAI GPT-5/o-series reject ``max_tokens`` and require
        # ``max_completion_tokens``; other OpenAI-compatible stacks take max_tokens.
        cap = {"max_completion_tokens": max_output_tokens} if provider == "openai" else {"max_tokens": max_output_tokens}
        probe_payload = {
            "model": resolved_model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0,
            **cap,
        }

        def _dispatch_probe() -> Any:
            candidate = _physical_candidate(probe_payload)
            request = _attempt_request(target, candidate, source="capability_probe")
            return _execute_candidate(
                request,
                lambda: oai.with_options(timeout=timeout).chat.completions.create(**candidate),
                _candidate_before_dispatch(candidate, request),
            )

        try:
            if current_usage_scope() is None:
                # Owner/settings probes run outside a task, but they are still
                # physical provider attempts. Give that system activity one
                # stable ledger identity instead of misclassifying it as an
                # unattributed task. A task-bound probe keeps its caller's
                # canonical task/root attribution and budget rails unchanged.
                with usage_scope(UsageScope(
                    task_id="system:capability_probe",
                    root_task_id="system:capability_probe",
                    category="capability_probe",
                    source="capability_probe",
                )):
                    resp = _dispatch_probe()
            else:
                resp = _dispatch_probe()
            echoed, usage_prompt = "", 0
            try:
                echoed = str(resp.choices[0].message.content or "")
                usage_prompt = int(getattr(getattr(resp, "usage", None), "prompt_tokens", 0) or 0)
            except Exception:
                pass
            return {"ok": True, "status_code": 200, "body": "", "echoed_text": echoed, "usage_prompt": usage_prompt}
        except Exception as exc:
            status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
            body = str(getattr(exc, "message", "") or getattr(exc, "body", "") or str(exc))
            return {"ok": False, "status_code": status if isinstance(status, int) else None,
                    "body": body, "echoed_text": "", "usage_prompt": 0}

    def _get_local_client(self):
        port = int(os.environ.get("LOCAL_MODEL_PORT", "8766"))
        if self._local_client is None or self._local_port != port:
            from openai import OpenAI
            self._local_client = OpenAI(
                base_url=f"http://127.0.0.1:{port}/v1",
                api_key="local",
                max_retries=0,
            )
            self._local_port = port
        return self._local_client

    def _get_async_remote_client(self, target: Dict[str, Any]):
        base_url = str(target.get("base_url") or "")
        api_key = str(target.get("api_key") or "")
        headers_dict = dict(target.get("default_headers") or {})
        headers = tuple(sorted((str(k), str(v)) for k, v in headers_dict.items()))
        cache_key = (str(target.get("provider") or ""), base_url, api_key, headers)

        client = self._async_remote_clients.get(cache_key)
        if client is None:
            from openai import AsyncOpenAI

            kwargs: Dict[str, Any] = {
                "api_key": api_key,
                "max_retries": 0,
            }
            if base_url:
                kwargs["base_url"] = base_url
            if headers_dict:
                kwargs["default_headers"] = headers_dict
            client = AsyncOpenAI(**kwargs)
            self._async_remote_clients[cache_key] = client
        return client

    @staticmethod
    def _no_proxy_timeout(read_timeout: Optional[float] = None):
        import httpx
        from ouroboros.config import get_llm_transport_read_timeout_sec

        read_write = (
            float(read_timeout) if read_timeout and read_timeout > 0
            else get_llm_transport_read_timeout_sec()
        )
        return httpx.Timeout(connect=30.0, read=read_write, write=read_write, pool=30.0)

    @classmethod
    def _make_no_proxy_client(cls, target: Dict[str, Any], timeout: Optional[float] = None):
        import httpx
        from openai import OpenAI

        http_client = httpx.Client(
            trust_env=False,
            mounts={},
            timeout=cls._no_proxy_timeout(timeout),
        )
        oa_client = OpenAI(
            api_key=str(target.get("api_key") or ""),
            base_url=str(target.get("base_url") or ""),
            default_headers=dict(target.get("default_headers") or {}),
            http_client=http_client,
            max_retries=0,
        )
        return oa_client, http_client

    @classmethod
    def _make_no_proxy_async_client(cls, target: Dict[str, Any], timeout: Optional[float] = None):
        import httpx
        from openai import AsyncOpenAI

        http_client = httpx.AsyncClient(
            trust_env=False,
            mounts={},
            timeout=cls._no_proxy_timeout(timeout),
        )
        oa_client = AsyncOpenAI(
            api_key=str(target.get("api_key") or ""),
            base_url=str(target.get("base_url") or ""),
            default_headers=dict(target.get("default_headers") or {}),
            http_client=http_client,
            max_retries=0,
        )
        return oa_client, http_client
