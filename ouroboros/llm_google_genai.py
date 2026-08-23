"""Direct Google Generative AI chat lane — split out of llm.py (module-size + byte gates).

Mixed into ``LLMClient`` (see llm.py). NOT OpenAI-compatible: the ``generateContent`` v1beta
endpoint, its own message shape, and its own usage-field names. Landed as the deep-review
escape route after OpenRouter exhausted its credit pool on task c7862982 (a 100k-token
attempt 402'd for $11 of pure waste) — see llm.py's provider-target resolution for the
``google_genai`` branch and ``provider_models.GOOGLE_GENAI_DIRECT_DEFAULTS``.

``_attempt_request`` is imported locally (call-time, not module-level) to avoid a load-order
circular import with ``ouroboros.llm``, which imports this module's mixin into ``LLMClient``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ouroboros.usage_accounting import UsageAccountingError, execute_physical_attempt


class GoogleGenAIChatMixin:
    """Direct Google Generative AI (Gemini) chat methods, mixed into ``LLMClient``."""

    @staticmethod
    def _google_genai_contents(messages: List[Dict[str, Any]]) -> Tuple[Optional[str], List[Dict[str, Any]]]:
        """Translate OpenAI-style messages into Gemini ``contents`` + systemInstruction.

        Gemini's ``contents`` is a flat role/parts list: each turn is a single
        ``{role: "user"|"model", parts: [...]}`` block. Multi-turn dialogue
        alternates user/model. OpenAI's tool/system roles do not map directly;
        the system message is lifted into a sibling ``systemInstruction`` (the
        documented Gemini channel) and tool calls/results are intentionally
        skipped on this lane — the c7862982 failure path is the deep review,
        which never uses tools, and tool-calling on Gemini needs a separate
        schema-translation pass to be honest about its capability delta.
        Anything we cannot translate becomes a documented silent skip; the
        reviewer would rather see a clean text-only round than a half-translated
        one that silently changes the contract.
        """
        system_instruction: Optional[str] = None
        contents: List[Dict[str, Any]] = []
        for msg in messages or []:
            role = str(msg.get("role") or "").strip().lower()
            content = msg.get("content")
            if role == "system":
                if isinstance(content, str) and content.strip():
                    if system_instruction is None:
                        system_instruction = content
                    else:
                        system_instruction = system_instruction + "\n\n" + content
                elif isinstance(content, list):
                    text_chunks = [
                        str(block.get("text") or "")
                        for block in content
                        if isinstance(block, dict) and str(block.get("type") or "") == "text"
                    ]
                    joined = "\n\n".join(chunk for chunk in text_chunks if chunk.strip())
                    if joined:
                        system_instruction = (
                            joined if system_instruction is None
                            else system_instruction + "\n\n" + joined
                        )
                continue
            if role in ("tool", "function"):
                # Tool/function results on Gemini need a separate ``functionResponse``
                # part with the function NAME; we don't carry that through OpenAI's
                # tool-call-id-only message. Skip with a deliberate comment rather
                # than silently fabricate a name.
                continue
            if role in ("assistant", "model"):
                role = "model"
            else:
                role = "user"
            text: Optional[str] = None
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                # Flatten text blocks; image blocks would need a base64/inlineData
                # part — out of scope for the deep-review escape route.
                chunks = [
                    str(block.get("text") or "")
                    for block in content
                    if isinstance(block, dict)
                    and str(block.get("type") or "") in ("text", "")
                ]
                text = "\n".join(chunk for chunk in chunks if chunk)
            if not text:
                continue
            contents.append({"role": role, "parts": [{"text": text}]})
        if not contents:
            # Gemini REQUIRES at least one content entry; an empty model input is
            # a 400. Inject a minimal one so the request is well-formed and the
            # owner sees Gemini's actual error rather than an Ouroboros crash.
            contents.append({"role": "user", "parts": [{"text": "(empty input)"}]})
        return system_instruction, contents

    def _chat_google_genai(
        self,
        target: Dict[str, Any],
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        reasoning_effort: str,
        max_tokens: int,
        tool_choice: str,
        temperature: Optional[float] = None,
        no_proxy: bool = False,
        timeout: Optional[float] = None,
        allow_server_web_search: bool = False,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Sync native Google Generative AI ``generateContent`` call.

        Same shape as ``_chat_anthropic``/``_chat_gigachat``: ``requests`` with
        scrubbed proxy detection, attempts routed through the shared usage
        ledger so every physical call is priced/recorded. The base URL trailing
        slash and the ``:generateContent`` suffix are the documented v1beta
        endpoint shape; ``?key=...`` query auth matches Google's docs and is the
        form that survives the API key being rotated without a header rebuild.
        ``x-goog-api-key`` is also accepted — using the query param keeps the
        retry path identical to the documented examples and avoids leaking the
        key into the request line of any proxy log.
        """
        if tools:
            # Capability disclosure: tool calling on the direct Gemini route would
            # require translating OpenAI's function-calling schema to Gemini's
            # ``functionDeclarations`` and threading function-call parts through
            # subsequent turns. The c7862982 deep-review escape does not exercise
            # tools, so a tool-bearing request lands here as a typed refusal
            # rather than a half-implemented silent stub.
            raise ValueError(
                "google_genai direct provider does not support tool calls in v6.103.8; "
                "use anthropic/openai/minimax/cloudru/gigachat for tool-bearing turns."
            )

        import requests

        from ouroboros.llm import _attempt_request

        resolved_model = str(target.get("resolved_model") or "")
        api_key = str(target.get("api_key") or "")
        base_url = str(target.get("base_url") or "").rstrip("/")
        if not resolved_model:
            raise ValueError("google_genai target missing resolved_model")
        if not api_key:
            raise ValueError("google_genai target missing api_key — set OUROBOROS_GEMINI_API_KEY in env")

        system_instruction, contents = self._google_genai_contents(messages)
        generation_config: Dict[str, Any] = {
            # ``maxOutputTokens`` caps the model including the thoughts budget on
            # thinking models (gemini-3.7-flash is a thinking model). Gemini docs
            # explicitly separate this from the input window — passing it on a
            # 10k cap means the model can never produce more than 10k tokens of
            # visible output, with the thoughts budget drawn from the same pool.
            "maxOutputTokens": int(max_tokens or 0) if max_tokens and max_tokens > 0 else 65536,
        }
        if temperature is not None:
            generation_config["temperature"] = float(temperature)

        payload: Dict[str, Any] = {"contents": contents}
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        payload["generationConfig"] = generation_config

        url = f"{base_url}/v1beta/models/{resolved_model}:generateContent"
        # Use the documented ``?key=`` query form (preferred — survives key
        # rotation, matches Google's reference examples). The header form
        # (``x-goog-api-key``) is also valid but adds nothing for us.
        params = {"key": api_key}
        headers = {"content-type": "application/json"}
        request_timeout = float(timeout) if timeout and timeout > 0 else 120.0

        def _post():
            if no_proxy:
                with requests.Session() as session:
                    session.trust_env = False
                    sent = session.post(
                        url, params=params, headers=headers,
                        json=payload, timeout=request_timeout,
                    )
            else:
                sent = requests.post(
                    url, params=params, headers=headers,
                    json=payload, timeout=request_timeout,
                )
            if sent.status_code >= 400:
                body_preview = (sent.text or "")[:2000]
                raise requests.HTTPError(
                    f"{sent.status_code} {sent.reason} for url {sent.url}: {body_preview}",
                    response=sent,
                )
            return sent

        try:
            response = execute_physical_attempt(
                _attempt_request(target, payload, source="llm.google_genai"),
                _post,
            )
        except UsageAccountingError:
            raise
        return self._normalize_google_genai_response(
            response.json(), target,
        )

    def _normalize_google_genai_response(
        self,
        resp_dict: Dict[str, Any],
        target: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Translate a Gemini ``generateContent`` body into (message, usage).

        Gemini returns ``candidates[0[]].content.parts`` as a list of typed
        parts (``text``, ``functionCall``, etc.); we collapse text parts into
        the OpenAI-shape ``content`` field and drop everything else on the
        text-only deep-review lane. ``usageMetadata`` carries
        ``promptTokenCount``/``candidatesTokenCount``/``thoughtsTokenCount``
        — the ``thoughts`` field is visible to the ledger so reviewers can see
        reasoning spend without us inventing a category. Cost is unknown on
        this lane (no live catalog mirror yet) — ``cost=None`` is honest, not
        a freebie: it reads ``cost_final=False`` so a downstream "is this row
        settled?" check still says no.
        """
        candidates = resp_dict.get("candidates") or []
        first = candidates[0] if candidates else None
        text_parts: List[str] = []
        finish_reason: Optional[str] = None
        if isinstance(first, dict):
            content = first.get("content") or {}
            parts = content.get("parts") or []
            for part in parts:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    text_parts.append(str(part.get("text") or ""))
            finish_reason = first.get("finishReason") or first.get("finish_reason")
        message: Dict[str, Any] = {
            "role": "assistant",
            "content": "".join(text_parts),
        }
        if finish_reason:
            message["stop_reason"] = str(finish_reason)

        usage_meta = resp_dict.get("usageMetadata") or {}
        prompt_tokens = int(usage_meta.get("promptTokenCount") or 0)
        candidates_tokens = int(usage_meta.get("candidatesTokenCount") or 0)
        thoughts_tokens = int(usage_meta.get("thoughtsTokenCount") or 0)
        cached_tokens = int(usage_meta.get("cachedContentTokenCount") or 0)
        total_tokens = int(usage_meta.get("totalTokenCount") or (prompt_tokens + candidates_tokens + thoughts_tokens))

        usage: Dict[str, Any] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": candidates_tokens,
            "total_tokens": total_tokens,
            # ``thoughts`` rides the usage dict under a documented field so the
            # ledger can show reasoning spend on thinking models (Gemini 3.x
            # flash returns 50-100 thoughts tokens even for a 1-token answer).
            # Other providers do not surface this; consumers ignore unknown keys.
            "thoughts_tokens": thoughts_tokens,
            "cached_tokens": cached_tokens,
            "provider": "google_genai",
            "resolved_model": str(target.get("usage_model") or target.get("resolved_model") or ""),
            "cost": None,
            "cost_final": False,
        }
        if resp_dict.get("modelVersion"):
            usage["model_version"] = str(resp_dict.get("modelVersion") or "")
        return message, usage
