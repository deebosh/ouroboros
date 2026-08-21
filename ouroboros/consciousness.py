"""Background thinking loop with scoped tools and no silent context drops."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import pathlib
import queue
import threading
import traceback
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional

if TYPE_CHECKING:
    from ouroboros.tools.registry import ToolRegistry

from ouroboros.loop_tool_execution import StatefulToolExecutor, _truncate_tool_result
from ouroboros.utils import (
    append_jsonl,
    emit_log_event,
    read_text,
    sanitize_tool_args_for_log,
    sanitize_tool_result_for_log,
    truncate_for_log,
    utc_now_iso,
)
from ouroboros.config import get_consciousness_model, get_context_mode, resolve_effort, resolve_temperature
from ouroboros.pricing import infer_provider_from_model
from ouroboros.llm import LLMClient
from ouroboros.memory import Memory
from ouroboros.context import (
    build_runtime_section, build_memory_sections,
    build_recent_sections, build_health_invariants,
    build_knowledge_sections, build_governance_sections, safe_read,
)
from ouroboros.context_budget import (
    BG_CONTEXT_MAX_CHARS,
    BG_CONTEXT_WARN_CHARS,
    BG_STATE_JSON_WARN_CHARS,
)

log = logging.getLogger(__name__)


class _ConsciousnessOverflow(OverflowError):
    """OverflowError carrying per-section diagnostics for consciousness context.

    The bare ``OverflowError`` raised from ``_build_context`` cannot name which
    sections crossed the ``BG_CONTEXT_MAX_CHARS`` limit; this subclass carries
    the breakdown so the overflow event in ``logs/events.jsonl`` can surface
    top contributors (structural fix for ``ibl-consciousness-context-overflow``:
    the prior event only carried ``{ts, type, error}``, so future investigations
    could not name which sections crossed the limit without inferring from logs).
    """
    def __init__(self, *, total_chars: int, max_chars: int, mode: str,
                 sections: List[Any]) -> None:
        self.total_chars = int(total_chars)
        self.max_chars = int(max_chars)
        self.mode = str(mode or "")
        # sections is List[Tuple[str, int]] — coerce defensively.
        norm: List[tuple] = []
        for entry in sections or []:
            try:
                name, chars = entry  # type: ignore[misc]
            except (TypeError, ValueError):
                continue
            norm.append((str(name), int(chars)))
        self.sections = norm
        # Top 5 contributors (descending chars).
        self.top_contributors = sorted(norm, key=lambda x: -x[1])[:5]
        over = max(0, self.total_chars - self.max_chars)
        super().__init__(
            f"Background consciousness context too large "
            f"({self.total_chars:,} chars, {over:,} over the {self.max_chars:,} limit, "
            f"mode={self.mode}). Top contributors: "
            f"{[(n, c) for n, c in self.top_contributors]}"
        )


def _label_section(content: str, fallback: str) -> str:
    """Best-effort label from a section's leading '## Header' marker.

    Falls back to the caller-supplied position-based label when the section is
    missing a recognised heading (the low-mode ARCHITECTURE navigation map, for
    example, is built without a leading ``## ARCHITECTURE.md`` header)."""
    head = str(content or "")[:200]
    if head.startswith("## "):
        first_line = head.split("\n", 1)[0]
        label = first_line[3:].strip()
        if label:
            return label[:80].lower().replace(" ", "_").replace("(", "").replace(")", "").replace("/", "_")
    return fallback


class BackgroundConsciousness:
    """Persistent background thinking loop for Ouroboros."""

    def __init__(
        self,
        drive_root: pathlib.Path,
        repo_dir: pathlib.Path,
        event_queue: Any,
        owner_chat_id_fn: Callable[[], Optional[int]],
    ):
        self._drive_root = drive_root
        self._repo_dir = repo_dir
        self._event_queue = event_queue
        self._owner_chat_id_fn = owner_chat_id_fn

        self._max_bg_rounds = int(os.environ.get("OUROBOROS_BG_MAX_ROUNDS", "10"))
        self._wakeup_min = int(os.environ.get("OUROBOROS_BG_WAKEUP_MIN", "30"))
        self._wakeup_max = int(os.environ.get("OUROBOROS_BG_WAKEUP_MAX", "7200"))

        self._llm = LLMClient()
        self._registry = self._build_registry()
        self._running = False
        self._paused = False
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._wakeup_event = threading.Event()
        self._next_wakeup_sec: float = 300.0
        self._observations: queue.Queue = queue.Queue(maxsize=100)
        self._deferred_events: list = []
        self._tool_executor = StatefulToolExecutor()

        self._bg_spent_usd: float = 0.0
        self._bg_budget_pct: float = float(
            os.environ.get("OUROBOROS_BG_BUDGET_PCT", "10")
        )
        self._last_cycle_started_at: str = ""
        self._last_cycle_finished_at: str = ""
        self._last_idle_reason: str = "stopped"
        self._last_error: str = ""

    @property
    def is_running(self) -> bool:
        return self._running and self._thread is not None and self._thread.is_alive()

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def _model(self) -> str:
        return get_consciousness_model()

    def status_snapshot(self) -> Dict[str, Any]:
        return {
            "running": bool(self.is_running),
            "paused": bool(self._paused),
            "next_wakeup_sec": int(self._next_wakeup_sec),
            "last_cycle_started_at": self._last_cycle_started_at,
            "last_cycle_finished_at": self._last_cycle_finished_at,
            "last_idle_reason": self._last_idle_reason,
            "last_error": self._last_error,
        }

    def start(self) -> str:
        if self.is_running:
            return "Background consciousness is already running."
        self._running = True
        self._paused = False
        self._last_idle_reason = "starting"
        self._last_error = ""
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return "Background consciousness started."

    def stop(self) -> str:
        if not self.is_running:
            return "Background consciousness is not running."
        self._running = False
        self._last_idle_reason = "stopping"
        self._stop_event.set()
        self._wakeup_event.set()  # Unblock sleep
        try:
            self._tool_executor.shutdown(wait=False, cancel_futures=True)
        except Exception:
            log.debug("Failed to shutdown consciousness tool executor", exc_info=True)
        return "Background consciousness stopping."

    def pause(self) -> None:
        """Pause during foreground task execution."""
        self._paused = True
        self._last_idle_reason = "paused_by_active_task"

    def resume(self) -> None:
        """Resume after a task and flush deferred events first."""
        if self._deferred_events and self._event_queue is not None:
            for evt in self._deferred_events:
                self._event_queue.put(evt)
            self._deferred_events.clear()
        self._paused = False
        self._last_idle_reason = "waking"
        self._wakeup_event.set()

    def inject_observation(self, text: str) -> None:
        """Push an observation for the next background cycle."""
        try:
            self._observations.put_nowait(text)
        except queue.Full:
            pass

    def _emit_live_log(self, event_type: str, **fields: Any) -> None:
        emit_log_event(
            self._event_queue,
            {
                "type": event_type,
                "ts": utc_now_iso(),
                "task_id": "bg-consciousness",
                "task_type": "consciousness",
                **fields,
            },
            blocking=True,
            log_label="consciousness live",
        )

    def _emit_cycle_idle(self, state: str) -> None:
        """Signal that a background-thinking cycle ended, so the web UI can retire
        the bg-consciousness live card instead of leaving it in a perpetual
        "thinking" phase.

        Background consciousness writes no task_result, so the renderer has no
        terminal signal of its own. This emits a structured marker
        (``consciousness_state``) consumed by ``web/modules/log_events.js`` — never
        a text-matched one. Replay after reload is handled separately in
        ``gateway/history.py``.
        """
        self._emit_live_log(
            "consciousness_status",
            is_progress=True,
            consciousness_state=state,
        )

    def _loop(self) -> None:
        """Daemon thread: sleep, wake, think, repeat."""
        while not self._stop_event.is_set():
            self._wakeup_event.clear()
            self._wakeup_event.wait(timeout=self._next_wakeup_sec)

            if self._stop_event.is_set():
                break

            if self._paused:
                self._last_idle_reason = "paused_by_active_task"
                continue

            if not self._check_budget():
                self._last_idle_reason = "budget_blocked"
                self._next_wakeup_sec = self._wakeup_max
                continue

            try:
                self._last_cycle_started_at = utc_now_iso()
                self._last_idle_reason = "thinking"
                self._last_error = ""
                cycle_completed = self._think()
                self._last_cycle_finished_at = utc_now_iso()
                # Preserve distinct overflow/LLM error statuses set inside _think().
                if cycle_completed and not self._stop_event.is_set() and not self._paused:
                    self._last_idle_reason = "sleeping"
                # Retire the live card now that this cycle is done (skip while paused:
                # a real task is active and owns the status).
                if not self._paused:
                    self._emit_cycle_idle(self._last_idle_reason)
            except Exception as e:
                self._last_cycle_finished_at = utc_now_iso()
                self._last_idle_reason = "error_backoff"
                self._last_error = repr(e)
                append_jsonl(self._drive_root / "logs" / "events.jsonl", {
                    "ts": utc_now_iso(),
                    "type": "consciousness_error",
                    "error": repr(e),
                    "traceback": traceback.format_exc()[:1500],
                })
                self._emit_cycle_idle("error_backoff")
                self._next_wakeup_sec = min(
                    self._next_wakeup_sec * 2, self._wakeup_max
                )
        self._last_idle_reason = "stopped"
        self._emit_cycle_idle("stopped")

    def _check_budget(self) -> bool:
        """Return whether background consciousness is within its budget."""
        try:
            from ouroboros.usage_accounting import usage_projection

            total_budget = float(os.environ.get("TOTAL_BUDGET", "1"))
            if total_budget <= 0:
                return True
            max_bg = total_budget * (self._bg_budget_pct / 100.0)
            projection = usage_projection(
                self._drive_root, root_task_id="bg-consciousness",
            )
            accounted = float(projection.get("accounted_usd") or 0.0)
            self._bg_spent_usd = float(projection.get("settled_usd") or 0.0)
            return accounted < max_bg
        except Exception:
            log.warning("Failed to check background consciousness budget", exc_info=True)
            return False

    def _think(self) -> bool:
        """Bind each wakeup to the global ledger and its background sub-budget."""
        from ouroboros.usage_accounting import UsageScope, usage_scope

        try:
            total_budget = float(os.environ.get("TOTAL_BUDGET", "0") or 0)
        except (TypeError, ValueError):
            total_budget = 0.0
        root_limit = total_budget * (self._bg_budget_pct / 100.0) if total_budget > 0 else None
        with usage_scope(UsageScope(
            drive_root=self._drive_root,
            task_id="bg-consciousness",
            root_task_id="bg-consciousness",
            category="consciousness",
            source="background_consciousness",
            global_limit_usd=total_budget if total_budget > 0 else None,
            root_limit_usd=root_limit,
        )):
            return self._think_scoped()

    def _think_scoped(self) -> bool:
        """Run one context/LLM/tools cycle; False preserves skip/error status."""
        try:
            context = self._build_context()
        except _ConsciousnessOverflow as exc:
            # P1: skip the cycle rather than silently truncating cognitive context.
            # Structural fix for ibl-consciousness-context-overflow: emit
            # per-section attribution so the owner can see which sections
            # crossed the BG_CONTEXT_MAX_CHARS limit. The previous event row
            # only carried {ts, type, error}, leaving investigation to infer
            # top contributors from logs.
            log.warning("consciousness: wakeup cycle skipped: %s", exc)
            self._last_idle_reason = "context_overflow"
            append_jsonl(self._drive_root / "logs" / "events.jsonl", {
                "ts": utc_now_iso(),
                "type": "consciousness_context_overflow",
                "error": str(exc),
                "total_chars": exc.total_chars,
                "max_chars": exc.max_chars,
                "mode": exc.mode,
                "sections": [
                    {"name": name, "chars": chars}
                    for name, chars in exc.sections
                ],
                "top_contributors": [
                    {"name": name, "chars": chars}
                    for name, chars in exc.top_contributors
                ],
            })
            return False
        except OverflowError as exc:
            # Defensive: a bare OverflowError from a future builder change
            # should still skip without losing the existing minimal event shape.
            log.warning("consciousness: wakeup cycle skipped (untyped overflow): %s", exc)
            self._last_idle_reason = "context_overflow"
            append_jsonl(self._drive_root / "logs" / "events.jsonl", {
                "ts": utc_now_iso(),
                "type": "consciousness_context_overflow",
                "error": str(exc),
            })
            return False
        model = self._model

        tools = self._tool_schemas()
        messages = [
            {"role": "system", "content": context},
            {"role": "user", "content": "Wake up. Think."},
        ]

        total_cost = 0.0
        cost_final = True
        final_content = ""
        round_idx = 0
        all_pending_events = []

        try:
            for round_idx in range(1, self._max_bg_rounds + 1):
                if self._paused:
                    break
                _use_local_consciousness = os.environ.get("USE_LOCAL_CONSCIOUSNESS", "").lower() in ("true", "1")
                self._emit_live_log(
                    "llm_round_started",
                    round=round_idx,
                    attempt=1,
                    model=model,
                    reasoning_effort=resolve_effort("consciousness"),
                    use_local=bool(_use_local_consciousness),
                )
                from ouroboros.llm_observability import chat_observed

                msg, usage = chat_observed(
                    self._llm,
                    drive_root=self._drive_root,
                    task_id="consciousness",
                    call_type="consciousness_round",
                    messages=messages,
                    model=model,
                    tools=tools,
                    reasoning_effort=resolve_effort("consciousness"),
                    max_tokens=65536,
                    use_local=_use_local_consciousness,
                    temperature=resolve_temperature("consciousness"),
                )
                cost = float(usage["cost"]) if usage.get("cost") is not None else None
                if cost is None:
                    cost_final = False
                else:
                    total_cost += cost
                    self._bg_spent_usd += cost

                # Global budget updates via events.py; direct updates would double-count.

                if not self._check_budget():
                    self._last_idle_reason = "budget_blocked"
                    append_jsonl(self._drive_root / "logs" / "events.jsonl", {
                        "ts": utc_now_iso(),
                        "type": "bg_budget_exceeded_mid_cycle",
                        "round": round_idx,
                    })
                    break

                if self._event_queue is not None:
                    provider = "local" if _use_local_consciousness else str(usage.get("provider") or infer_provider_from_model(model))
                    resolved_model = str(usage.get("resolved_model") or model)
                    model_name = f"{model} (local)" if _use_local_consciousness else resolved_model
                    self._event_queue.put({
                        "type": "llm_usage",
                        "provider": provider,
                        "model": model_name,
                        "usage": usage,
                        "cost": cost,
                        "source": "consciousness",
                        "ts": utc_now_iso(),
                        "category": "consciousness",
                    })

                content = msg.get("content") or ""
                tool_calls = msg.get("tool_calls") or []
                self._emit_live_log(
                    "llm_round_finished",
                    round=round_idx,
                    attempt=1,
                    model=model,
                    reasoning_effort=resolve_effort("consciousness"),
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                    cached_tokens=int(usage.get("cached_tokens") or 0),
                    cache_write_tokens=int(usage.get("cache_write_tokens") or 0),
                    cost_usd=cost,
                    response_kind="tool_calls" if tool_calls else "message",
                    tool_call_count=len(tool_calls),
                    has_text=bool(content.strip()),
                )

                self._emit_progress(content)

                if self._paused:
                    break

                if content and not tool_calls:
                    final_content = content
                    break

                if tool_calls:
                    messages.append(msg)
                    for tc in tool_calls:
                        result = self._execute_tool(tc, all_pending_events)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": result,
                        })
                    continue

                break

            if all_pending_events and self._event_queue is not None:
                if self._paused:
                    self._deferred_events.extend(all_pending_events)
                else:
                    for evt in all_pending_events:
                        self._event_queue.put(evt)

            append_jsonl(self._drive_root / "logs" / "events.jsonl", {
                "ts": utc_now_iso(),
                "type": "consciousness_thought",
                "thought_preview": (final_content or "")[:300],
                "cost_usd": total_cost if cost_final else None,
                "cost_final": cost_final,
                "rounds": round_idx,
                "model": model,
            })

        except Exception as e:
            self._emit_live_log("llm_round_error", round=round_idx, model=model, error=repr(e))
            append_jsonl(self._drive_root / "logs" / "events.jsonl", {
                "ts": utc_now_iso(),
                "type": "consciousness_llm_error",
                "error": repr(e),
            })
            self._last_idle_reason = "llm_error"
            # Back off persistent provider/tool failures.
            self._next_wakeup_sec = min(self._next_wakeup_sec * 2, self._wakeup_max)
            return False

        return True

    def _emit_progress(self, content: str) -> None:
        if not content or not content.strip():
            return
        chat_id = self._owner_chat_id_fn()
        entry = {
            "type": "send_message",
            "chat_id": chat_id,
            "text": f"💬 {content.strip()}",
            "format": "markdown",
            "ts": utc_now_iso(),
            "task_id": "bg-consciousness",
            "content": content.strip(),
            "is_progress": True,
        }
        persist_locally = self._event_queue is None or chat_id is None
        if self._event_queue is not None and chat_id is not None:
            try:
                if self._paused:
                    self._deferred_events.append(entry)
                else:
                    self._event_queue.put(entry)
            except Exception:
                log.warning("Failed to emit progress event", exc_info=True)
                persist_locally = False
        if persist_locally:
            append_jsonl(self._drive_root / "logs" / "progress.jsonl", entry)

    def _load_bg_prompt(self) -> str:
        """Load consciousness system prompt."""
        prompt_path = self._repo_dir / "prompts" / "CONSCIOUSNESS.md"
        if prompt_path.exists():
            return read_text(prompt_path)
        return "You are Ouroboros in background consciousness mode. Think."

    def _build_context(self) -> str:
        """Assemble the BG consciousness context; per-section attribution on overflow.

        Each section is tracked by name and char count BEFORE the final join, so
        the ``_ConsciousnessOverflow`` raised below carries the breakdown that
        the structured overflow event emits (structural fix for
        ``ibl-consciousness-context-overflow``: prior event had only
        ``{ts, type, error}``, leaving the owner to infer top contributors from
        logs).

        Mode-aware assembly honours ``OUROBOROS_CONTEXT_MODE`` (BIBLE P1 + the
        v6.80.0 owner coupling): in ``low`` we skip the improvement backlog
        digest (an action-hint, not a core cognitive artifact) and the
        ephemeral observations queue (a transient injection, not memory). The
        ARCHITECTURE navigation-map vs full-text split is already wired inside
        ``build_governance_sections`` via ``context_layout``. Knowledge index,
        Pattern Register, identity, scratchpad and recent dialogue horizon stay
        full in both modes — P1 preservation, granularity varies.
        """
        from ouroboros.agent import Env
        env = Env(repo_dir=self._repo_dir, drive_root=self._drive_root)
        memory = Memory(drive_root=self._drive_root, repo_dir=self._repo_dir)
        bg_task = {"id": "bg-consciousness", "type": "consciousness"}
        # Mode is read fresh each call — owner toggle takes effect on next wakeup.
        context_mode = get_context_mode()

        # Per-section attribution: name + char count for every part we append.
        # The OverflowError below cannot name what crossed the limit unless each
        # section reports its size; this is the diagnostic backbone.
        sections: List[Any] = []
        parts: List[str] = []

        bg_prompt = self._load_bg_prompt()
        parts.append(bg_prompt)
        sections.append(("bg_prompt", len(bg_prompt)))

        if not (self._repo_dir / "docs" / "ARCHITECTURE.md").is_file():
            logging.getLogger(__name__).warning(
                "consciousness: docs/ARCHITECTURE.md not found or empty"
            )
        gov_sections = build_governance_sections(env, warn_large=True, warn_label="consciousness")
        parts.extend(gov_sections)
        for idx, g in enumerate(gov_sections):
            sections.append((_label_section(g, f"governance[{idx}]"), len(g)))

        mem_sections = build_memory_sections(memory)
        parts.extend(mem_sections)
        for idx, m in enumerate(mem_sections):
            sections.append((_label_section(m, f"memory[{idx}]"), len(m)))

        knowledge_sections = build_knowledge_sections(
            env,
            warn_large=True,
            pattern_header="## Pattern Register",
        )
        parts.extend(knowledge_sections)
        for idx, k in enumerate(knowledge_sections):
            sections.append((_label_section(k, f"knowledge[{idx}]"), len(k)))

        # Improvement backlog digest: low-mode compression (not a core cognitive
        # artifact; it's an action-hint projection of the durable backlog).
        include_backlog = context_mode != "low"
        if include_backlog:
            try:
                from ouroboros.improvement_backlog import format_backlog_digest

                backlog_digest = format_backlog_digest(self._drive_root, limit=8, max_chars=4000)
                if backlog_digest:
                    parts.append(backlog_digest)
                    sections.append(("backlog_digest", len(backlog_digest)))
            except Exception:
                log.debug("Failed to include improvement backlog in consciousness context", exc_info=True)
        else:
            sections.append(("backlog_digest_skipped_low_mode", 0))

        health_section = build_health_invariants(env)
        if health_section:
            parts.append(health_section)
            sections.append(("health_invariants", len(health_section)))

        # Full drive state: no clip_text here.
        state_json = safe_read(env.drive_path("state/state.json"), fallback="{}")
        if len(state_json) > BG_STATE_JSON_WARN_CHARS:
            log.warning(
                "consciousness: drive state JSON is large (%d chars)", len(state_json)
            )
        state_section = "## Drive state\n\n" + state_json
        parts.append(state_section)
        sections.append(("drive_state", len(state_section)))

        runtime_section = build_runtime_section(env, bg_task)
        parts.append(runtime_section)
        sections.append(("runtime", len(runtime_section)))

        # Empty task_id includes recent sections across tasks. The P1 horizon is
        # preserved by build_recent_sections itself (low mode widens the chat
        # tail when consolidated_offset>0); we do NOT skip it here.
        recent_sections = build_recent_sections(memory, env, task_id="")
        parts.extend(recent_sections)
        for idx, r in enumerate(recent_sections):
            sections.append((_label_section(r, f"recent[{idx}]"), len(r)))

        # Observations: low-mode compression (ephemeral queue-injected hints, not
        # memory; deferring them to the next wakeup is safe — they are NOT a P1
        # cognitive artifact). We still drain the queue in low mode so observations
        # do not accumulate forever, but they are NOT appended to the context.
        include_observations = context_mode != "low"
        drained_observations: List[str] = []
        while not self._observations.empty():
            try:
                drained_observations.append(self._observations.get_nowait())
            except queue.Empty:
                break
        if include_observations and drained_observations:
            obs_section = "## Recent observations\n\n" + "\n".join(
                f"- {o}" for o in drained_observations[-10:])
            parts.append(obs_section)
            sections.append(("observations", len(obs_section)))
        else:
            sections.append(
                ("observations_skipped_low_mode" if not include_observations
                 else "observations", 0)
            )

        bg_info_lines = [
            f"BG budget spent: ${self._bg_spent_usd:.4f}",
            f"Current wakeup interval: {self._next_wakeup_sec}s",
            f"Current model: {self._model}",
            f"Context mode: {context_mode}",
        ]
        bg_info_section = "## Background consciousness info\n\n" + "\n".join(bg_info_lines)
        parts.append(bg_info_section)
        sections.append(("bg_info", len(bg_info_section)))

        # P1 guard: warn when large, fail the wakeup instead of truncating artifacts.
        _BG_TOTAL_WARN_CHARS = BG_CONTEXT_WARN_CHARS   # ~150K tokens — warn but proceed
        _BG_TOTAL_MAX_CHARS = BG_CONTEXT_MAX_CHARS  # ~300K tokens — fail fast (P1 compliance)
        full_text = "\n\n".join(parts)
        total_chars = len(full_text)
        if total_chars > _BG_TOTAL_MAX_CHARS:
            log.warning(
                "consciousness: context too large (%d chars > %d limit, mode=%s) — "
                "skipping wakeup cycle; top contributors: %s; "
                "groom memory (knowledge, patterns, scratchpad) to reduce size",
                total_chars, _BG_TOTAL_MAX_CHARS, context_mode,
                sorted(sections, key=lambda x: -x[1])[:5],
            )
            # Stash on self for any post-mortem tool that inspects without reading
            # the events row (e.g. a future operator dashboard).
            self._last_context_sections = sections
            self._last_context_mode = context_mode
            self._last_context_total = total_chars
            raise _ConsciousnessOverflow(
                total_chars=total_chars,
                max_chars=_BG_TOTAL_MAX_CHARS,
                mode=context_mode,
                sections=sections,
            )
        if total_chars > _BG_TOTAL_WARN_CHARS:
            log.warning(
                "consciousness: context is large (%d chars, mode=%s) — consider grooming memory",
                total_chars, context_mode,
            )
        # Stash for tests / post-mortem observability even on success.
        self._last_context_sections = sections
        self._last_context_mode = context_mode
        self._last_context_total = total_chars
        return full_text

    _BG_TOOL_WHITELIST = frozenset({
        "send_user_message", "update_scratchpad",
        "update_identity", "set_next_wakeup",
        "knowledge_read", "knowledge_write", "knowledge_list",
        "web_search", "read_file", "list_files", "query_code",
        "chat_history", "recent_tasks",
        "list_github_issues", "get_github_issue",
    })

    def _build_registry(self) -> "ToolRegistry":
        """Create a ToolRegistry scoped to background-allowed tools."""
        from ouroboros.tools.registry import ToolRegistry, ToolEntry

        registry = ToolRegistry(repo_dir=self._repo_dir, drive_root=self._drive_root)

        def _set_next_wakeup(ctx: Any, seconds: int = 300) -> str:
            self._next_wakeup_sec = max(self._wakeup_min, min(self._wakeup_max, int(seconds)))
            return f"OK: next wakeup in {self._next_wakeup_sec}s"

        registry.register(ToolEntry("set_next_wakeup", {
            "name": "set_next_wakeup",
            "description": "Set how many seconds until your next thinking cycle. "
                           "Default 300. Range: 60-3600.",
            "parameters": {"type": "object", "properties": {
                "seconds": {"type": "integer",
                            "description": "Seconds until next wakeup (60-3600)"},
            }, "required": ["seconds"]},
        }, _set_next_wakeup))

        return registry

    def _tool_schemas(self) -> List[Dict[str, Any]]:
        """Return tool schemas filtered to the background whitelist."""
        return [
            s for s in self._registry.schemas()
            if s.get("function", {}).get("name") in self._BG_TOOL_WHITELIST
        ]

    def _execute_tool(self, tc: Dict[str, Any], all_pending_events: List[Dict[str, Any]]) -> str:
        """Execute a background tool call with timeout."""
        fn_name = tc.get("function", {}).get("name", "")
        if fn_name not in self._BG_TOOL_WHITELIST:
            return f"Tool {fn_name} not available in background mode."
        try:
            args = json.loads(tc.get("function", {}).get("arguments", "{}"))
        except (json.JSONDecodeError, ValueError):
            return "Failed to parse arguments."

        self._emit_live_log(
            "tool_call_started",
            tool=fn_name,
            args=sanitize_tool_args_for_log(fn_name, args if isinstance(args, dict) else {}),
            timeout_sec=self._registry.get_timeout(fn_name),
        )

        chat_id = self._owner_chat_id_fn()
        self._registry._ctx.current_chat_id = chat_id
        self._registry._ctx.pending_events = []
        self._registry._ctx.event_queue = self._event_queue
        self._registry._ctx.task_id = "bg-consciousness"
        self._registry._ctx.task_metadata = {
            "root_task_id": "bg-consciousness",
            "session_id": "background-consciousness",
            "actor_id": "background-consciousness",
            "delegation_role": "background",
        }

        timeout_sec = self._registry.get_timeout(fn_name)
        result = None
        error = None
        timed_out = False

        def _run_tool():
            nonlocal result, error
            try:
                result = self._registry.execute(fn_name, args)
            except Exception as e:
                error = e

        future = self._tool_executor.submit(_run_tool)
        try:
            future.result(timeout=timeout_sec)
        except (TimeoutError, concurrent.futures.TimeoutError):
            self._tool_executor.reset()
            timed_out = True
            result = f"[TIMEOUT after {timeout_sec}s]"
            self._emit_live_log(
                "tool_call_timeout",
                tool=fn_name,
                args=sanitize_tool_args_for_log(fn_name, args if isinstance(args, dict) else {}),
                timeout_sec=timeout_sec,
            )
            append_jsonl(self._drive_root / "logs" / "events.jsonl", {
                "ts": utc_now_iso(),
                "type": "consciousness_tool_timeout",
                "tool": fn_name,
                "timeout_sec": timeout_sec,
            })

        if error is not None:
            self._emit_live_log(
                "tool_call_finished",
                tool=fn_name,
                args=sanitize_tool_args_for_log(fn_name, args if isinstance(args, dict) else {}),
                is_error=True,
                result_preview=repr(error),
            )
            append_jsonl(self._drive_root / "logs" / "events.jsonl", {
                "ts": utc_now_iso(),
                "type": "consciousness_tool_error",
                "tool": fn_name,
                "error": repr(error),
            })
            result = f"Error: {repr(error)}"

        for evt in self._registry._ctx.pending_events:
            all_pending_events.append(evt)

        result_str = _truncate_tool_result(
            result,
            tool_name=fn_name,
            tool_args=args if isinstance(args, dict) else {},
        )

        args_for_log = sanitize_tool_args_for_log(fn_name, args)
        if error is None and result is not None and not timed_out:
            self._emit_live_log(
                "tool_call_finished",
                tool=fn_name,
                args=args_for_log,
                is_error=False,
                result_preview=sanitize_tool_result_for_log(truncate_for_log(result_str, 500)),
            )
        append_jsonl(self._drive_root / "logs" / "tools.jsonl", {
            "ts": utc_now_iso(),
            "tool": fn_name,
            "source": "consciousness",
            "args": args_for_log,
            "result_preview": sanitize_tool_result_for_log(truncate_for_log(result_str, 2000)),
        })

        return result_str
