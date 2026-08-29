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
from ouroboros.config import get_consciousness_model, get_context_mode, resolve_effort
from ouroboros.temperature_settings import resolve_temperature
from ouroboros.pricing import infer_provider_from_model
from ouroboros.llm import LLMClient, add_usage
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
    LARGE_CONTEXT_SECTION_CHARS,
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
        _use_local_consciousness = os.environ.get(
            "USE_LOCAL_CONSCIOUSNESS", ""
        ).lower() in ("true", "1")
        effort = resolve_effort("consciousness")
        total_cost = 0.0
        cost_final = True
        cycle_usage: Dict[str, Any] = {}
        final_content = ""
        round_idx = 0
        all_pending_events = []

        try:
            target = (
                self._llm._resolve_remote_target(model)
                if not _use_local_consciousness else None
            )
            for round_idx in range(1, self._max_bg_rounds + 1):
                if self._paused:
                    break
                if target is not None:
                    from ouroboros.openai_chat_dispatch import projected_context_size_bytes

                    physical_chars = projected_context_size_bytes(
                        messages,
                        tools,
                        provider=str(target.get("provider") or ""),
                        reasoning_effort=effort,
                    )
                    if physical_chars > BG_CONTEXT_MAX_CHARS:
                        error = (
                            "Background consciousness physical context too large "
                            f"({physical_chars:,} bytes including tools). "
                            "Groom memory to continue."
                        )
                        self._last_idle_reason = "context_overflow"
                        append_jsonl(self._drive_root / "logs" / "events.jsonl", {
                            "ts": utc_now_iso(),
                            "type": "consciousness_context_overflow",
                            "error": error,
                        })
                        return False
                    if physical_chars > BG_CONTEXT_WARN_CHARS:
                        log.warning(
                            "consciousness: physical context is large "
                            "(%d bytes including tools)",
                            physical_chars,
                        )
                self._emit_live_log(
                    "llm_round_started",
                    round=round_idx,
                    attempt=1,
                    model=model,
                    reasoning_effort=effort,
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
                    reasoning_effort=effort,
                    max_tokens=65536,
                    use_local=_use_local_consciousness,
                    temperature=resolve_temperature("consciousness"),
                )
                from ouroboros.openai_chat_dispatch import (
                    custom_validation_by_call_id,
                    pop_custom_validation_receipts,
                )

                wire_validation = pop_custom_validation_receipts(
                    usage,
                    msg.get("tool_calls") or [],
                )
                validation_by_id = custom_validation_by_call_id(wire_validation)
                cost = float(usage["cost"]) if usage.get("cost") is not None else None
                if cost is None:
                    cost_final = False
                else:
                    total_cost += cost
                    self._bg_spent_usd += cost
                add_usage(cycle_usage, usage)

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
                    reasoning_effort=effort,
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
                        result = self._execute_tool(
                            tc,
                            all_pending_events,
                            validation_by_id.get(str(tc.get("id") or "")),
                        )
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
                **{
                    key: cycle_usage[key]
                    for key in (
                        "request_wire", "request_wire_history",
                        "request_wire_history_omitted",
                    )
                    if key in cycle_usage
                },
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
        """Assemble the BG consciousness context; graceful degradation on overflow.

        Each section is tracked by name, char count AND DROP PRIORITY (lower =
        dropped first when the assembled context overflows
        ``BG_CONTEXT_MAX_CHARS``). P1 cognitive artifacts (BIBLE.md, identity,
        scratchpad, knowledge index, Pattern Register, dialogue horizon,
        ARCHITECTURE nav-map) carry drop_priority=0 and are never dropped.
        Non-P1 sections (backlog digest, observations, runtime, drive state,
        recent reflections, chat tail) drop in a fixed order so an overflowing
        wakeup degrades to a runnable context instead of skipping entirely
        (structural fix for ``ibl-local-31f19191be34``: the prior
        all-or-nothing skip-loop ran for 4+ hours when drive_state alone was
        ~634K chars).

        Mode-aware assembly honours ``OUROBOROS_CONTEXT_MODE`` (BIBLE P1 + the
        v6.80.0 owner coupling): in ``low`` we skip the improvement backlog
        digest (an action-hint, not a core cognitive artifact), the
        ephemeral observations queue, and bound the chat tail + drive state
        + identity. ARCHITECTURE is forced to nav-map in BOTH modes — the
        consciousness loop only navigates the doc, never reads the whole 564K
        body. Knowledge index, Pattern Register, scratchpad and recent
        dialogue horizon stay full in both modes — P1 preservation,
        granularity varies.
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

        # Drop-priority constants (lower = dropped first on overflow). P1 cognitive
        # artifacts carry P1=0 and are never dropped by graceful degradation.
        P1 = 0          # never dropped: BIBLE, identity, scratchpad, knowledge,
                        # pattern register, dialogue horizon, bg_prompt,
                        # ARCHITECTURE nav-map, health_invariants
        DROP_FIRST = 10  # backlog digest, observations, recent reflections,
                        # recent events, recent tools, recent progress
        DROP_MID = 20    # drive state, runtime
        DROP_LATE = 30   # chat tail (heaviest non-P1 — drops last)

        bg_prompt = self._load_bg_prompt()
        parts.append(bg_prompt)
        sections.append(("bg_prompt", len(bg_prompt), P1))

        if not (self._repo_dir / "docs" / "ARCHITECTURE.md").is_file():
            logging.getLogger(__name__).warning(
                "consciousness: docs/ARCHITECTURE.md not found or empty"
            )
        # ARCHITECTURE forced to nav-map regardless of mode (structural fix for
        # ibl-local-31f19191be34: the consciousness loop never pays the 564K cost
        # of the full text — P1 horizon is preserved by granularity, not by
        # reading the whole file). BIBLE.md stays full in both modes.
        from ouroboros.context_layout import architecture_context_section
        bible_text = safe_read(env.repo_path("BIBLE.md"))
        if bible_text:
            if len(bible_text) > LARGE_CONTEXT_SECTION_CHARS:
                log.warning("consciousness: BIBLE.md is large (%d chars)", len(bible_text))
            bible_part = "## BIBLE.md\n\n" + bible_text
            parts.append(bible_part)
            sections.append(("bible", len(bible_part), P1))
        arch_section = architecture_context_section(env, context_mode="low")
        if arch_section:
            parts.append(arch_section)
            sections.append(("architecture", len(arch_section), P1))
        else:
            log.warning("consciousness: docs/ARCHITECTURE.md not found or empty")

        mem_sections = build_memory_sections(memory)
        # In low mode, identity is bounded — full identity is a P1 cognitive
        # artifact only in max mode. The marker is rewritten to make the bound
        # explicit (no "already loaded" duplicate impression when the section
        # has been slim-trimmed).
        bounded_identity_done = False
        parts.extend(mem_sections)
        for idx, m in enumerate(mem_sections):
            priority = P1
            label = _label_section(m, f"memory[{idx}]")
            if (
                context_mode == "low"
                and not bounded_identity_done
                and m.startswith("## Identity (from `memory/identity.md`")
            ):
                bounded = self._bounded_identity_for_low_mode(m)
                if bounded != m:
                    parts[-1] = bounded
                    m = bounded
                    label = "identity_bounded_low_mode"
                    bounded_identity_done = True
            sections.append((label, len(m), priority))

        knowledge_sections = build_knowledge_sections(
            env,
            warn_large=True,
            pattern_header="## Pattern Register",
        )
        parts.extend(knowledge_sections)
        for idx, k in enumerate(knowledge_sections):
            sections.append((_label_section(k, f"knowledge[{idx}]"), len(k), P1))

        # Improvement backlog digest: low-mode skip (not a core cognitive artifact;
        # it's an action-hint projection of the durable backlog). DROP_FIRST in max.
        include_backlog = context_mode != "low"
        if include_backlog:
            try:
                from ouroboros.improvement_backlog import format_backlog_digest

                backlog_digest = format_backlog_digest(self._drive_root, limit=8, max_chars=4000)
                if backlog_digest:
                    parts.append(backlog_digest)
                    sections.append(("backlog_digest", len(backlog_digest), DROP_FIRST))
            except Exception:
                log.debug("Failed to include improvement backlog in consciousness context", exc_info=True)
        else:
            sections.append(("backlog_digest_skipped_low_mode", 0, P1))

        health_section = build_health_invariants(env)
        if health_section:
            parts.append(health_section)
            sections.append(("health_invariants", len(health_section), P1))

        # Slim drive-state projection (structural fix for ibl-local-31f19191be34).
        # Strips usage_accounting.by_root (the 453K-char mostly-zero per-root map)
        # in BOTH modes; in low mode further bounds the section to the documented
        # key subset so drive_state never exceeds ~30K chars. Matches the
        # projection _drive_state_section in ouroboros.context for the chat path.
        drive_state = self._slim_drive_state(context_mode=context_mode)
        if drive_state:
            parts.append(drive_state)
            sections.append(("drive_state", len(drive_state), DROP_MID))

        runtime_section = build_runtime_section(env, bg_task)
        if runtime_section:
            parts.append(runtime_section)
            sections.append(("runtime", len(runtime_section), DROP_MID))

        # Empty task_id includes recent sections across tasks. P1 horizon preserved
        # by build_recent_sections itself (low mode widens the chat tail when
        # consolidated_offset>0).
        recent_sections = build_recent_sections(memory, env, task_id="")
        parts.extend(recent_sections)
        for idx, r in enumerate(recent_sections):
            # Recent chat tail is the heaviest single section — DROP_LATE (drops
            # only in extreme overflows after drive_state / runtime are gone).
            if r.startswith("## Recent chat"):
                priority = DROP_LATE
            else:
                priority = DROP_FIRST
            sections.append((_label_section(r, f"recent[{idx}]"), len(r), priority))

        # Observations: low-mode skip (ephemeral queue-injected hints, not memory;
        # deferring to the next wakeup is safe — they are NOT a P1 cognitive
        # artifact). We still drain the queue in low mode so observations do not
        # accumulate forever, but they are NOT appended to the context.
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
            sections.append(("observations", len(obs_section), DROP_FIRST))
        else:
            sections.append(
                ("observations_skipped_low_mode" if not include_observations
                 else "observations", 0, P1))

        bg_info_lines = [
            f"BG budget spent: ${self._bg_spent_usd:.4f}",
            f"Current wakeup interval: {self._next_wakeup_sec}s",
            f"Current model: {self._model}",
            f"Context mode: {context_mode}",
        ]
        bg_info_section = "## Background consciousness info\n\n" + "\n".join(bg_info_lines)
        parts.append(bg_info_section)
        sections.append(("bg_info", len(bg_info_section), P1))

        # Graceful degradation: drop the highest-priority non-P1 sections
        # iteratively until the assembled context fits BG_CONTEXT_MAX_CHARS.
        # P1 sections are never dropped — only action-hints / state-of-the-world
        # / chat tail, in that order. This replaces the prior all-or-nothing
        # skip-loop (structural fix for ibl-local-31f19191be34: a 4+ hour skip
        # cascade where drive_state alone was ~634K chars).
        _BG_TOTAL_WARN_CHARS = BG_CONTEXT_WARN_CHARS   # ~150K tokens — warn but proceed
        _BG_TOTAL_MAX_CHARS = BG_CONTEXT_MAX_CHARS  # ~300K tokens — fail fast (P1 compliance)
        full_text, total_chars, dropped = self._graceful_assemble(
            parts, sections, _BG_TOTAL_MAX_CHARS,
        )
        if total_chars > _BG_TOTAL_MAX_CHARS:
            # Even after dropping every non-P1 section, the P1 core itself is
            # too large. This is a memory-discipline signal: P1 core (BIBLE +
            # identity + scratchpad + knowledge + dialogue horizon + ARCHITECTURE
            # nav-map) grew past BG_CONTEXT_MAX_CHARS. Preserve the prior
            # all-or-nothing skip here so the owner sees a loud event instead
            # of silently truncated cognitive artifacts.
            log.warning(
                "consciousness: P1 core too large (%d chars > %d limit, mode=%s, "
                "non-P1 dropped=%d); skipping wakeup; groom memory (knowledge, "
                "patterns, scratchpad, identity) to reduce size",
                total_chars, _BG_TOTAL_MAX_CHARS, context_mode, dropped,
            )
            self._last_context_sections = sections
            self._last_context_mode = context_mode
            self._last_context_total = total_chars
            self._last_context_dropped = dropped
            raise _ConsciousnessOverflow(
                total_chars=total_chars,
                max_chars=_BG_TOTAL_MAX_CHARS,
                mode=context_mode,
                sections=sections,
            )
        if total_chars > _BG_TOTAL_WARN_CHARS:
            log.warning(
                "consciousness: context is large (%d chars, mode=%s, dropped=%d) — "
                "consider grooming memory",
                total_chars, context_mode, dropped,
            )
        if dropped:
            log.info(
                "consciousness: graceful degradation dropped %d non-P1 section(s) "
                "(mode=%s, total=%d chars, max=%d) — emitting owner-visible note",
                dropped, context_mode, total_chars, _BG_TOTAL_MAX_CHARS,
            )
        # Stash for tests / post-mortem observability even on success.
        self._last_context_sections = sections
        self._last_context_mode = context_mode
        self._last_context_total = total_chars
        self._last_context_dropped = dropped
        return full_text

    def _graceful_assemble(
        self,
        parts: List[str],
        sections: List[Any],
        max_chars: int,
    ) -> Tuple[str, int, int]:
        """Assemble context with graceful overflow degradation.

        Iteratively drops the highest-priority non-P1 sections until the
        joined context fits ``max_chars``. P1 sections (drop_priority=0) are
        NEVER dropped — if even the P1 core alone overflows, the caller will
        raise ``_ConsciousnessOverflow`` (all-or-nothing skip).

        Returns ``(text, total_chars, dropped_count)``. ``dropped_count`` is
        the number of non-P1 sections removed during graceful degradation;
        it is exposed via ``self._last_context_dropped`` for observability.
        """
        keep_parts: List[str] = []
        keep_sections: List[Any] = []
        for p, s in zip(parts, sections):
            label, chars, priority = s[0], s[1], s[2]
            keep_parts.append(p)
            keep_sections.append(s)
        # Iteratively drop the LARGEST non-P1 section until under budget, or
        # until only P1 sections remain.
        dropped = 0
        while True:
            full_text = "\n\n".join(keep_parts)
            total_chars = len(full_text)
            if total_chars <= max_chars:
                break
            # Find the largest non-P1 section to drop.
            largest_idx = -1
            largest_chars = -1
            for idx, s in enumerate(keep_sections):
                if s[2] == 0:  # P1 — never dropped
                    continue
                if s[1] > largest_chars:
                    largest_chars = s[1]
                    largest_idx = idx
            if largest_idx < 0:
                # All remaining sections are P1; caller raises overflow.
                break
            dropped_name = keep_sections[largest_idx][0]
            log.info(
                "consciousness: dropping %s (%d chars, priority=%d) — overflow "
                "graceful degradation",
                dropped_name, keep_sections[largest_idx][1], keep_sections[largest_idx][2],
            )
            keep_parts.pop(largest_idx)
            keep_sections.pop(largest_idx)
            dropped += 1
        return full_text, total_chars, dropped

    def _slim_drive_state(self, *, context_mode: str) -> str:
        """Return a slim projection of state/state.json for the consciousness loop.

        Strips ``usage_accounting.by_root`` (the 453K-char mostly-zero
        per-root map that was bloating the consciousness context) in BOTH
        modes. In ``low`` mode further bounds the section to the documented
        key subset so the drive-state section never exceeds ~30K chars even
        if the legacy state.json carries extra top-level keys.

        Mirrors ``ouroboros.context._drive_state_section`` (used by the
        chat/main path) so the two consumers see the same projection shape.
        """
        from ouroboros.context import _drive_state_section  # canonical slim projection
        from ouroboros.agent import Env

        env = Env(repo_dir=self._repo_dir, drive_root=self._drive_root)
        section = _drive_state_section(env)
        if not section:
            return ""
        # Defence in depth: if usage_accounting.by_root is present in the
        # rendered text, strip it. The slim _drive_state_section does NOT
        # include usage_accounting keys, but older renderers might.
        if '"by_root"' in section:
            try:
                import json as _json
                from ouroboros.context_health import read_json_dict as _rjd
                raw = _rjd(env.drive_path("state/state.json")) or {}
                ua = dict(raw.get("usage_accounting") or {})
                ua.pop("by_root", None)
                raw["usage_accounting"] = ua
                keys = (
                    "session_id", "current_branch", "current_sha",
                    "evolution_mode_enabled", "evolution_owner_stopped",
                    "evolution_cycle", "evolution_consecutive_failures",
                    "last_evolution_task_at", "bg_consciousness_enabled",
                    "post_task_autostop", "budget_drift_pct",
                    "budget_drift_alert", "last_owner_message_at",
                )
                projected = {k: raw[k] for k in keys if k in raw}
                omitted = sorted(set(raw) - set(projected))
                note = (
                    "Projection of state/state.json (spend/budget facts live "
                    "in the Runtime section, from the usage-accounting "
                    "authority)."
                    + ((" Omitted keys: " + ", ".join(omitted) + ". Full file: "
                        "read_file(root='runtime_data', "
                        "path='state/state.json').") if omitted else "")
                )
                section = ("## Drive state\n\n"
                           + _json.dumps(
                               projected, ensure_ascii=False, indent=1,
                               sort_keys=True, default=str,
                           )
                           + "\n\n" + note)
            except Exception:
                pass
        # In low mode, hard-cap to 30K chars (defence in depth — if a future
        # schema adds more keys, the loop should not regress).
        if context_mode == "low" and len(section) > 30_000:
            section = section[:30_000] + "\n\n[truncated — drive state bounded in low mode]\n"
        if len(section) > BG_STATE_JSON_WARN_CHARS:
            log.warning(
                "consciousness: drive state JSON is large (%d chars, mode=%s)",
                len(section), context_mode,
            )
        return section

    def _bounded_identity_for_low_mode(self, identity_section: str) -> str:
        """Return a bounded identity section for low-mode consciousness.

        The identity file is a living manifesto that grows over time; the
        structural fix here is to bound its in-context size in ``low`` mode
        (P1 cognitive horizon preserved by granularity — full identity stays
        available on demand via ``read_file(root='runtime_data',
        path='memory/identity.md')``). The marker is rewritten so the
        "already loaded" reminder is replaced by an explicit "bounded —
        read full on demand" pointer.

        Bounded identity keeps the most recent appended §-numbered section
        (the newest reflection) and trims everything before it to a short
        preamble. The bound is generous enough to retain growth-room for
        ~30 more appended sections before re-bounding is needed.
        """
        # Identity is structured as "\n\n## §N. ..." sections. Keep the
        # preamble + the most recent §-section. Anything before the LAST §
        # marker is dropped (header + earlier reflections). Identity is
        # replaceable: a future agent reads the full file via read_file.
        # Marker line is preserved verbatim so loaders recognize the section.
        if "## §" not in identity_section:
            return identity_section
        last_section_idx = identity_section.rfind("\n\n## §")
        if last_section_idx < 0:
            return identity_section
        # Keep the header (everything up to the first body section) plus the
        # last §-section. The preamble line is "~ Constitutional core ...".
        first_body_idx = identity_section.find("\n\n## §")
        if first_body_idx < 0 or first_body_idx >= last_section_idx:
            return identity_section
        preamble = identity_section[:first_body_idx]
        last_section = identity_section[last_section_idx:].lstrip("\n")
        bounded = (
            preamble
            + "\n\n[Earlier §-sections trimmed in low mode for context budget — "
              "full identity available via "
              "read_file(root='runtime_data', path='memory/identity.md').]\n\n"
            + last_section
        )
        return bounded

    _BG_TOOL_WHITELIST = frozenset({
        "send_user_message", "update_scratchpad",
        "update_identity", "set_next_wakeup",
        "knowledge_read", "knowledge_write", "knowledge_list",
        "web_search", "read_file", "list_files", "query_code",
        "chat_history", "recent_tasks",
        "initiate_presence",
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

    def _execute_tool(
        self,
        tc: Dict[str, Any],
        all_pending_events: List[Dict[str, Any]],
        custom_validation: Any = None,
    ) -> str:
        """Execute a background tool call with timeout."""
        fn_name = tc.get("function", {}).get("name", "")
        if custom_validation is not None and not custom_validation.allows_execution:
            from ouroboros.openai_chat_dispatch import custom_tool_argument_error

            return custom_tool_argument_error(fn_name, custom_validation)
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
