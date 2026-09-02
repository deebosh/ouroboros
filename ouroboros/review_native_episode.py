"""The native tool-round review delivery (configured-subagent api rows).

A reviewer row bound to an ``api_model`` configured subagent RETRIEVES the
subject through bounded read-only inspection rounds instead of receiving the
assembled packet. Not a third public route kind — ``api_chat`` stays the wire
vocabulary; the slot's actor binding selects this executor at the one transport
seam (``review_execution._review_route_executor``).

ONE episode is ONE logical review attempt: ``LLMClient.chat(tools=…)`` calls
against a fresh, instance-local inspection-only ``ToolRegistry`` until the
reviewer answers. There is NO round cap (BIBLE P13: the floor is hardcoded,
never the ceiling): the episode's bounds are the transcript bound derived from
the reviewer's own context window (never above the owner ceiling), the owner
deadline and the paid ledger. The host announces the bound once at the landing
fraction so the reviewer can finish; exhaustion is a typed refusal for verdict
shapes and a disclosed INCOMPLETE product for the report shape — never
mid-episode compaction or resume. Every provider call is its own ledger row;
the coordinator's second actor attempt repairs FORMAT locally, exactly like
the session executor.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

from ouroboros.config import get_finalization_grace_sec
from ouroboros.deadline_utils import owner_deadline_exhausted, review_transport_timeout
from ouroboros.review_dispatch import bind_api_review_paid_stamp, invoke_review_paid_stamp
from ouroboros.review_verdict_extraction import canonicalize_session_verdict
from ouroboros.triad_review import default_output_contract, review_output_shape
from ouroboros.usage_accounting import (
    POSITIVE_PHYSICAL_ATTEMPT_STATES,
    physical_attempt_limit,
)

from ouroboros.review_execution import (
    ReviewAssignment,
    ReviewAttemptResult,
    ReviewRouteKind,
    ReviewRouteUnavailable,
    ReviewSlotExecutor,
    _deadline_exhausted_error,
)

log = logging.getLogger("review_native_episode")


def review_native_max_transcript_chars() -> int:
    """Owner CEILING on the episode transcript (chars). The effective bound is
    the reviewer window's calibrated capacity (`review_native_transcript_bound`),
    never above this number."""
    from ouroboros.config import _clamped_number_setting

    return _clamped_number_setting(
        "OUROBOROS_REVIEW_NATIVE_MAX_TRANSCRIPT_CHARS", low=50_000, high=2_000_000, cast=int)


# The landing fraction of the transcript bound: the host announces the bound
# ONCE when the transcript crosses it, so the reviewer answers from what it has
# read instead of discovering the wall on the send that would have exceeded it.
NATIVE_LANDING_FRACTION = 0.8

# Chars per estimated token — the `utils.estimate_tokens` heuristic inverted,
# so the transcript counter (chars) and the reviewer window (tokens) meet on
# the SAME scale the review packet sizer uses.
_CHARS_PER_ESTIMATED_TOKEN = 4


def review_native_transcript_bound(
    model_id: str, *, output_reserve: int, use_local: Optional[bool] = None,
) -> int:
    """The episode's SEND bound in chars, derived from the reviewer's window.

    The same density-calibrated input capacity the packet route sizes its pack
    with (`calibrated_input_token_limit`: window minus the output reserve,
    divided by the freshest exact-model token density, never above the
    absolute-margin form), converted back to chars and capped by the owner
    ceiling `OUROBOROS_REVIEW_NATIVE_MAX_TRANSCRIPT_CHARS`. A 1M reviewer
    therefore lands on the ceiling; a 200K route gets a bound its own window
    can carry instead of a number written for a different model — the
    previous fixed cap either starved a large window or overflowed a small one.
    """
    from ouroboros.reviewer_window import reviewer_context_window, window_scaled_reserves
    from ouroboros.tools.review_helpers import calibrated_input_token_limit

    ceiling = review_native_max_transcript_chars()
    window = int(reviewer_context_window(str(model_id or ""), use_local=use_local))
    reserve, margin = window_scaled_reserves(
        window, output_reserve=int(output_reserve or 0), tokenizer_margin=window // 8)
    tokens = calibrated_input_token_limit(
        str(model_id or ""), context_window=window, output_reserve=reserve,
        tokenizer_margin=margin, budget_cap=ceiling // _CHARS_PER_ESTIMATED_TOKEN)
    return max(0, min(ceiling, int(tokens) * _CHARS_PER_ESTIMATED_TOKEN))


def native_or_packet_attempt_rail(slot: Any, two_send_surface: bool) -> Any:
    """The physical-send rail for one actor: the historical two-send rail for
    a packet/session actor on the P3/acceptance surfaces; NO local send count
    for a native tool-round slot (its bounds are the transcript bound, the
    owner deadline and the paid ledger — a count would be a round cap by
    another name, and `llm.py` already rails its own recovery sends); no rail
    otherwise."""
    if not two_send_surface or bool(getattr(slot, "native_retrieval", False)):
        return contextlib.nullcontext()
    return physical_attempt_limit(2)

# ---------------------------------------------------------------------------
# The native tool-round route variant: a configured-subagent api row RETRIEVES
# the subject through bounded read-only inspection rounds instead of receiving
# the assembled packet. Not a third public route kind — ``api_chat`` stays the
# wire vocabulary; the slot's actor binding selects this executor internally.
# ---------------------------------------------------------------------------

_INSPECTION_TOOL_NAMES = (
    "read_file", "list_files", "search_code", "query_code",
    "vcs_status", "vcs_diff",
)

_NATIVE_REVIEW_INSTRUCTIONS = (
    "You are an independent Ouroboros reviewer running a bounded read-only "
    "inspection episode. Retrieve the evidence yourself with the tools you are "
    "given inside the repository root — read_file, list_files, search_code, "
    "query_code, vcs_status, vcs_diff; no other tools exist here. You cannot "
    "modify anything and have no shell. Read LARGE files in bounded chunks "
    "(read_file supports offset/limit) instead of requesting a whole large "
    "document at once: the episode has a hard transcript budget sized to your "
    "own context window, and an oversized read spends it. There is no round "
    "limit. Your host will tell you once when the budget is nearly spent; "
    "answer from what you have read at that point. Read what the checklist "
    "requires, then answer. Your FINAL message must contain no tool calls and "
    "must follow the output contract in the task EXACTLY; your host parses it "
    "structurally, and prose around the verdict is a non-response."
)

# The host's budget fact (same class as the main loop's [ROUND_LIMIT] notice):
# a typed, once-only user message — never a silent cut of the transcript.
_LANDING_NOTICE = (
    "[EPISODE_BUDGET] Your inspection transcript is at {pct}% of its bound "
    "({used} of {bound} chars); no further reading fits. Your NEXT message "
    "must be the final deliverable in the output contract, with no tool "
    "calls. Mark anything you could not verify as unverified — an honest "
    "bounded answer is the expected outcome here, not a failure."
)

# Per-tool-result bound inside the episode: one greedy full read of a giant
# artifact must not consume the whole transcript budget in a single round.
# Disclosed truncation with an offset/limit continuation handle is honest —
# unlike compaction, nothing unseen is summarized into the record. The cap is
# ALSO clamped to the room left below the transcript bound minus the landing
# reserve, so no single read can jump over the landing notice and the bound.
_EPISODE_TOOL_RESULT_CHAR_CAP = 120_000

# Room kept below the bound for the landing notice itself: the notice must
# always fit under the send bound it announces.
_LANDING_RESERVE_CHARS = 512

class NativeToolRoundReviewExecutor(ReviewSlotExecutor):
    """Bounded native inspection episode for a configured-subagent api row.

    ONE episode is ONE logical review attempt: ``LLMClient.chat(tools=…)``
    calls against a fresh, instance-local inspection-only ``ToolRegistry``
    until the reviewer answers or a bound lands — the window-derived transcript
    bound, the owner deadline, the paid ledger; never a round count. Every
    provider call is its own ledger row (the ambient usage scope attributes
    them); the coordinator's second actor attempt repairs FORMAT locally over
    the collected answer, exactly like the session executor — there is no
    mid-episode resume, transcript compaction, or per-round durable ledger.
    Exhaustion is a typed refusal for verdict shapes; the report shape delivers
    what was collected, marked INCOMPLETE.
    """

    route = ReviewRouteKind.API_CHAT

    def __init__(self, assignment: ReviewAssignment, *, llm: Any = None):
        super().__init__(assignment, llm=llm)
        self._episode_prompt: Optional[str] = None
        self._raw_transcript: Optional[str] = None
        self._episode_usage: Dict[str, Any] = {}
        self._tool_receipts: List[Dict[str, Any]] = []
        self._tool_calls_total = 0
        self._rounds_used = 0
        self._episode_deltas: List[Dict[str, Any]] = []
        self._settled_failure: Optional[BaseException] = None

    # -- prompt (route-owned; never the api pack) ------------------------------

    def _output_contract(self) -> str:
        contract = str((self.assignment.request.policy or {}).get("output_contract") or "")
        return contract or default_output_contract(review_output_shape(self.assignment.request.surface))

    def prompt_payload(self) -> Dict[str, Any]:
        return {
            "native_episode_prompt": self.episode_prompt,
            "inspection_tools": list(_INSPECTION_TOOL_NAMES),
        }

    def prompt_chars(self) -> int:
        return len(self.episode_prompt)

    @property
    def episode_prompt(self) -> str:
        """The SAME compact work-order the session route sends (D12): task,
        criteria and output contract, minus any assembled evidence — the actor
        retrieves context itself, so the packet is never built here."""
        if self._episode_prompt is None:
            request, slot = self.assignment.request, self.assignment.slot
            task = str(request.session_task or "").strip()
            if not task:
                raise ReviewRouteUnavailable(
                    "native tool-round slot has no session task: the surface must "
                    "supply the route-owned task text (request.session_task) — the "
                    "assembled api pack is deliberately not sendable to a retrieving "
                    "actor", code="session_task_missing")
            parts = [
                "You are an independent Ouroboros reviewer slot running a bounded "
                "read-only native inspection episode.",
                f"Surface: {request.surface}",
                f"Role hint: {slot.role_hint or 'general reviewer'}",
                "",
                task,
                "",
                "OUTPUT CONTRACT (your host parses this structurally):",
                self._output_contract()
                + "\nYour final message must contain the deliverable alone, with no tool calls.",
                f"Slot: {slot.slot_id}",
            ]
            self._episode_prompt = "\n".join(parts)
        return self._episode_prompt

    # -- delivery --------------------------------------------------------------

    def execute(self) -> ReviewAttemptResult:
        if self._raw_transcript is not None:
            # The permitted resend repairs FORMAT locally over the collected
            # final answer; it never runs a second paid episode.
            return self._verdict_result(force_extraction=True)
        if self._settled_failure is not None:
            raise self._settled_failure
        try:
            self._run_episode()
        except BaseException as exc:
            self._settled_failure = exc
            raise
        return self._verdict_result()

    def _inspection_registry(self, root: str, drive_root: Any) -> tuple[Any, List[Dict[str, Any]]]:
        """A fresh instance-local registry pinned to ``root``, read-only.

        Reuses the existing capability machinery wholesale instead of a new
        allowlist mechanism: the ``local_readonly_subagent`` constraint gives
        the read-only operation/root matrix, ``disabled_tools`` trims its
        broader name allowlist down to the inspection six, and the resource
        contract keeps extension/MCP discovery off. ``registry._ctx`` is
        per-instance, so the worker's own registry/context is never touched.
        ``drive_root`` is the data plane the inspection tools may read: an
        empty scratch directory by default, or the surface's opt-in
        ``policy["native_data_root"]`` (task acceptance reads task results and
        receipts there; deep self-review reads memory) — the read-only
        constraint applies to it exactly as to the repository.
        """
        import pathlib as _pathlib

        from ouroboros.tool_capabilities import LOCAL_READONLY_SUBAGENT_TOOL_NAMES
        from ouroboros.tools.registry import ToolContext, ToolRegistry

        registry = ToolRegistry(repo_dir=_pathlib.Path(root), drive_root=_pathlib.Path(drive_root))
        ctx = ToolContext(
            repo_dir=_pathlib.Path(root),
            drive_root=_pathlib.Path(drive_root),
            task_id=str(self.assignment.request.task_id or "") or None,
            task_constraint={"mode": "local_readonly_subagent"},
            task_contract={
                "allowed_resources": {"network": False, "web": False},
                "disabled_tools": sorted(
                    set(LOCAL_READONLY_SUBAGENT_TOOL_NAMES) - set(_INSPECTION_TOOL_NAMES)
                ),
            },
        )
        registry.set_context(ctx)
        schemas = []
        for name in _INSPECTION_TOOL_NAMES:
            schema = registry.get_schema_by_name(name)
            if schema:
                schemas.append(schema)
        if not schemas:
            raise ReviewRouteUnavailable(
                "no inspection tool schemas are projectable for the native "
                "tool-round episode", code="native_inspection_unavailable")
        return registry, schemas

    def _run_episode(self) -> None:
        import shutil
        import tempfile

        from ouroboros.llm import add_usage
        from ouroboros.openai_chat_dispatch import (
            custom_validation_by_call_id,
            pop_custom_validation_receipts,
        )

        request, slot = self.assignment.request, self.assignment.slot
        root = str(request.session_root or "").strip()
        if not root:
            raise ReviewRouteUnavailable(
                "native tool-round slot has no session root: the surface must name "
                "the repository root the reviewer episode runs in",
                code="session_root_missing")
        chat = getattr(self.llm, "chat", None)
        if not callable(chat):
            raise ReviewRouteUnavailable(
                "native tool-round episode needs a synchronous chat transport",
                code="api_chat_unavailable")
        deadline_at = str(getattr(request, "deadline_at", "") or "")
        max_tokens = int(request.max_tokens or slot.max_tokens)
        transcript_cap = review_native_transcript_bound(
            slot.model, output_reserve=max_tokens, use_local=bool(slot.use_local))
        landing_at = min(int(transcript_cap * NATIVE_LANDING_FRACTION),
                         max(0, transcript_cap - _LANDING_RESERVE_CHARS))
        shape = review_output_shape(request.surface)
        # The data plane is opt-in per surface (policy["native_data_root"]):
        # the default is an empty scratch directory so a repository review
        # cannot read the host's state; a surface that needs task results or
        # memory names the real root, which is the caller's and is never removed.
        data_root = str((request.policy or {}).get("native_data_root") or "").strip()
        scratch = tempfile.mkdtemp(prefix="ouro-native-review-")
        registry = None
        total_usage: Dict[str, Any] = {}
        final_answer: Optional[str] = None
        last_content = ""  # the reviewer's latest prose — the product of an exhausted report episode
        landed = False
        end_reason = "transcript_bound"
        round_idx = 0
        transcript_chars = 0
        episode: Dict[str, Any] = {}
        try:
            registry, schemas = self._inspection_registry(root, data_root or scratch)
            # The counter measures what every send actually carries: the
            # system instructions and the tool schemas ride EVERY provider
            # call, and tool-call argument objects accumulate in `messages`
            # exactly like results do. Counting only prompt+content+results
            # understated each send by the fixed system/schema cost and let
            # the argument tail drift past the promised bound unmeasured.
            # Units are CHARS throughout — same as the cap.
            transcript_chars = (
                len(self.episode_prompt)
                + len(_NATIVE_REVIEW_INSTRUCTIONS)
                + len(json.dumps(schemas, ensure_ascii=False, default=str))
            )
            messages: List[Dict[str, Any]] = [
                {"role": "system", "content": _NATIVE_REVIEW_INSTRUCTIONS},
                {"role": "user", "content": self.episode_prompt},
            ]
            if transcript_chars >= landing_at:
                # FLOOR: a bound that lands before the first send leaves no
                # room to read anything — a review with zero reads is not a
                # review, and the landing notice must never be the first
                # thing the reviewer hears.
                end_reason = "bound_below_first_send"
                raise ReviewRouteUnavailable(
                    f"native review episode bound ({transcript_cap} chars) leaves no "
                    f"room to read: the first send alone carries {transcript_chars} "
                    "chars; the episode fails closed", code="native_bound_below_first_send")
            while True:
                if owner_deadline_exhausted(
                    deadline_at=deadline_at, reserve_sec=get_finalization_grace_sec(),
                ):
                    end_reason = "deadline_exhausted"
                    raise _deadline_exhausted_error(
                        "owner deadline exhausted mid native review episode")
                # The bound is a SEND bound: it is enforced BEFORE every
                # provider call, because the transcript IS the growing context
                # the next send would carry. A final content-only answer that
                # lands past the number is accepted — no further send exists
                # for it to poison, and refusing a completed verdict would
                # protect nothing.
                if transcript_chars > transcript_cap:
                    break
                if not landed and transcript_chars >= landing_at:
                    # Once: the host's budget fact, so the reviewer lands on
                    # the next send instead of walking into the bound.
                    landed = True
                    notice = _LANDING_NOTICE.format(
                        pct=int(100 * transcript_chars / max(1, transcript_cap)),
                        used=transcript_chars, bound=transcript_cap)
                    messages.append({"role": "user", "content": notice})
                    transcript_chars += len(notice)
                    if transcript_chars > transcript_cap:
                        break  # even the notice would not fit: the bound has landed
                round_idx += 1
                chat_kwargs: Dict[str, Any] = {
                    "messages": messages,
                    "model": slot.model,
                    "tools": schemas,
                    "tool_choice": "auto",
                    "reasoning_effort": slot.effort,
                    "max_tokens": max_tokens,
                    "no_proxy": bool(request.no_proxy),
                    "use_local": bool(slot.use_local),
                    "cache_affinity": f"{request.surface}:{request.task_id or 'review'}",
                }
                if request.temperature is not None or slot.temperature is not None:
                    chat_kwargs["temperature"] = (
                        request.temperature if request.temperature is not None
                        else slot.temperature
                    )
                transport = review_transport_timeout(
                    slot.model, getattr(slot, "transport_timeout_sec", None), deadline_at,
                )
                if transport is not None:
                    chat_kwargs["timeout"] = transport
                with bind_api_review_paid_stamp(self.assignment.dispatch_stamp):
                    try:
                        msg, usage = chat(**chat_kwargs)
                    except BaseException as exc:
                        end_reason = "transport_error"
                        capture = getattr(exc, "physical_attempt_capture", None)
                        if str(getattr(capture, "state", "") or "") in POSITIVE_PHYSICAL_ATTEMPT_STATES:
                            invoke_review_paid_stamp(self.assignment.dispatch_stamp)
                        raise
                self._rounds_used = round_idx
                tool_calls = (msg.get("tool_calls") or []) if isinstance(msg, dict) else []
                usage = dict(usage or {})
                # Pop the wire-validation sidecar BEFORE accumulation, exactly
                # like the existing bounded loops — receipts are per-round
                # execution facts, not usage numbers.
                wire_validation = pop_custom_validation_receipts(usage, tool_calls)
                validation_by_id = custom_validation_by_call_id(wire_validation)
                add_usage(total_usage, usage)
                # add_usage accumulates only token/cost keys: carry the ledger
                # linkage and provenance facts the substrate's actor records
                # consume, or the episode rollup would echo the REQUESTED model
                # dressed as resolved and lose its physical attempt ids.
                for _attempt_id in (usage.get("ledger_attempt_ids") or []):
                    total_usage.setdefault("ledger_attempt_ids", []).append(_attempt_id)
                for _fact in ("resolved_model", "provider"):
                    if usage.get(_fact):
                        total_usage[_fact] = usage[_fact]
                content = str(msg.get("content") or "") if isinstance(msg, dict) else ""
                if content:
                    last_content = content
                if not tool_calls:
                    # The reviewer's answer — or an empty round, which is the
                    # episode's honest end: the empty answer rides the ordinary
                    # empty-response rail upstream (a report keeps its draft).
                    final_answer = content
                    end_reason = "final_answer" if content else "empty_answer"
                    break
                well_formed = [
                    tc for tc in tool_calls
                    if isinstance(tc, dict) and isinstance(tc.get("function"), dict)
                    and str(tc["function"].get("name") or "").strip()
                ]
                if not content and not well_formed:
                    # PROGRESS FLOOR (P13: a floor, never a ceiling): a round
                    # carrying neither prose nor one well-formed tool call is
                    # malformed provider output — it adds nothing to the
                    # transcript and would re-enter the paid send forever.
                    end_reason = "round_without_progress"
                    break
                assistant = dict(msg)
                assistant.setdefault("role", "assistant")
                messages.append(assistant)
                # The WHOLE assistant envelope (content + tool-call objects,
                # names and argument JSON) joins `messages` and rides every
                # later send — counting only its parts understated each send.
                transcript_chars += len(json.dumps(assistant, ensure_ascii=False, default=str))
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue  # a non-dict tool_call is malformed provider output, not a crash
                    # The per-result room is what is left below the bound minus
                    # the landing reserve: one read can never jump over the
                    # landing notice and the bound in a single round.
                    call_id, result = self._execute_inspection_call(
                        registry, tc, validation_by_id, round_idx=round_idx,
                        room=transcript_cap - _LANDING_RESERVE_CHARS - transcript_chars,
                    )
                    transcript_chars += len(result)
                    messages.append({
                        "role": "tool", "tool_call_id": call_id, "content": result,
                    })
            if shape == "report" and not final_answer and last_content:
                # A report is a product, not a verdict: the collected draft is
                # delivered marked INCOMPLETE rather than discarded (the bound
                # landed, the round made no progress, or the final round came
                # back empty) — the consumer discloses it; nothing unseen is
                # summarized into it. Decided BEFORE the custody row is written
                # so the durable fact knows the product is partial.
                final_answer = last_content
                episode["native_incomplete"] = end_reason
        finally:
            # Only the host's own scratch is removed; an opted-in data root
            # belongs to the caller and survives a failed episode untouched.
            shutil.rmtree(scratch, ignore_errors=True)
            # One typed custody row per episode END — including the ends that
            # leave through an exception (deadline, transport, registry): a
            # refused episode used to leave no trace of how far it got. The
            # actor usage is assembled HERE too, so a refused or errored
            # episode still carries its delivery, rounds, receipts and paid
            # ledger facts (`failure_custody` hands them to the error actor).
            episode.update({
                "native_rounds": self._rounds_used,
                "native_tool_calls": self._tool_calls_total,
                "native_transcript_chars": transcript_chars,
                "native_transcript_bound": transcript_cap,
                "native_landing_notified": landed,
                "native_end_reason": end_reason,
            })
            episode["native_custody_row"] = self._emit_episode_fact(episode)
            self._episode_usage = dict(total_usage)
            self._episode_usage.update({
                "provider": str(total_usage.get("provider") or ""),
                "resolved_model": str(total_usage.get("resolved_model") or slot.model),
                **episode,
                "native_tool_receipts": list(self._tool_receipts),
                # Provenance class of this delivery: the host SAW these reads.
                "host_file_read_attestation": "host_observed",
                "delivery": "native_tool_rounds",
            })
        if final_answer is None:
            if end_reason == "round_without_progress":
                raise ReviewRouteUnavailable(
                    f"native review episode round {self._rounds_used} carried neither "
                    "an answer nor a well-formed tool call; the episode fails closed "
                    "— a zero-progress round would re-enter the paid send forever",
                    code="native_round_without_progress")
            raise ReviewRouteUnavailable(
                f"native review episode transcript ({transcript_chars} chars) "
                f"exceeded its bound ({transcript_cap}) before a final answer; "
                "the episode fails closed — compaction would review a "
                "fabricated cut", code="native_transcript_cap_exceeded")
        if episode.get("native_incomplete"):
            self._episode_deltas.append({
                "kind": "capability_delta",
                "requested": "a finished report from the episode",
                "effective": (
                    f"the reviewer's last draft ({len(final_answer)} chars) — "
                    f"{end_reason} after {self._rounds_used} rounds without a final "
                    f"answer (transcript {transcript_chars} of {transcript_cap} chars)"
                ),
                "reason": f"native_{end_reason}_before_final_answer",
            })
        self._raw_transcript = final_answer

    def _execute_inspection_call(
        self, registry: Any, tc: Dict[str, Any], validation_by_id: Dict[str, Any],
        *, round_idx: int, room: int,
    ) -> tuple[str, str]:
        """Run ONE inspection tool call of a round and return ``(call_id, result)``.

        Owns the tool-policy half of the episode — wire-validation refusal, the
        inspection allowlist, argument parsing, execution, the disclosed result
        bound (the fixed per-result cap, clamped to ``room``) and the
        host-observed receipt — while the caller owns the loop, the transcript
        counter and the messages. Provenance comes from CONTROL FLOW, never
        string-sniffing: a refused call must not read as an executed one.
        """
        call_id = str(tc.get("id") or "")
        function = tc.get("function") if isinstance(tc.get("function"), dict) else {}
        name = str(function.get("name") or "")
        raw_args = function.get("arguments")
        args: Optional[Dict[str, Any]] = None
        outcome = "executed"
        verdict = validation_by_id.get(call_id)
        if verdict is not None and not getattr(verdict, "allows_execution", True):
            outcome = "refused"
            result = f"⚠️ TOOL_ARG_ERROR: {getattr(verdict, 'error', 'invalid arguments')}"
        elif name not in _INSPECTION_TOOL_NAMES:
            outcome = "refused"
            result = f"⚠️ tool {name!r} is not available in this read-only inspection episode"
        else:
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args or {})
                if not isinstance(args, dict):
                    raise ValueError("arguments must be a JSON object")
            except (TypeError, ValueError) as exc:
                outcome = "refused"
                args, result = None, f"⚠️ TOOL_ARG_ERROR: {exc}"
            if isinstance(args, dict):
                try:
                    result = str(registry.execute(name, args))
                except Exception as exc:  # tool errors feed the model, not the rail
                    outcome = "error"
                    result = f"⚠️ {type(exc).__name__}: {exc}"
                result_cap = max(0, min(_EPISODE_TOOL_RESULT_CHAR_CAP, room))
                if len(result) > result_cap:
                    # Disclosed bound with a continuation handle — the reader
                    # keeps reading in chunks (read_file supports offset/limit),
                    # so nothing is silently cut.
                    result = (
                        result[:result_cap]
                        + f"\n⚠️ RESULT TRUNCATED: showed {result_cap} of {len(result)} chars"
                        + (
                            " — the episode transcript budget is nearly spent;"
                            " answer now from what you have read."
                            if room < _EPISODE_TOOL_RESULT_CHAR_CAP else
                            ". Continue reading the remainder in bounded"
                            " chunks (read_file supports offset/limit)."
                        )
                    )
        # Host-observed evidence (bounded): which artifacts THIS episode
        # actually opened — disclosure, never a claim of full-surface coverage.
        self._tool_calls_total += 1
        if len(self._tool_receipts) < 200:
            receipt: Dict[str, Any] = {"round": round_idx, "tool": name}
            if isinstance(args, dict):
                for key in ("path", "root", "query", "pattern"):
                    if args.get(key):
                        receipt[key] = str(args[key])[:300]
            receipt["result_chars"] = len(result)
            receipt["outcome"] = outcome
            self._tool_receipts.append(receipt)
        return call_id, result

    def failure_custody(self) -> Dict[str, Any]:
        """The proven facts of a refused or errored episode for the error actor:
        delivery, rounds, receipts, transcript vs bound, end reason and the paid
        ledger facts of the rounds that DID run — so a failed native execution
        stays visible on the public wire instead of vanishing. Empty until the
        episode started."""
        return dict(self._episode_usage)

    def _emit_episode_fact(self, episode: Dict[str, Any]) -> str:
        """One typed custody row per episode end (rounds, transcript vs bound,
        landing, end reason). Returns the row's fate — ``written``, ``failed``
        or ``no_custody_root`` — so the usage can say whether the durable
        trace exists. Never raises."""
        drive = self.assignment.custody_root
        if not drive:
            return "no_custody_root"
        try:
            from ouroboros.delegate_custody import emit
        except Exception:  # telemetry never masks the episode's own outcome
            return "failed"
        written = emit(drive, "review_native_episode", {
            "surface": str(self.assignment.request.surface or ""),
            "task_id": str(self.assignment.request.task_id or ""),
            "slot_id": str(self.assignment.slot.slot_id or ""),
            "model": str(self.assignment.slot.model or ""),
            **episode,
        })
        return "written" if written else "failed"

    def _verdict_result(self, force_extraction: bool = False) -> ReviewAttemptResult:
        text = self._raw_transcript or ""
        canonical, method, extraction_usage = canonicalize_session_verdict(
            text,
            conformance_passed=False,  # no structured-output channel on this route
            contract=self._output_contract(),
            llm=self.llm,
            deadline_at=getattr(self.assignment.request, "deadline_at", "") or "",
            transport_timeout_sec=getattr(self.assignment.slot, "transport_timeout_sec", None),
            shape=review_output_shape(self.assignment.request.surface),
        )
        usage = dict(self._episode_usage)
        deltas: List[Dict[str, Any]] = list(self._episode_deltas)
        if method == "light_model_extraction":
            usage["extraction"] = extraction_usage
            deltas.append({
                "kind": "capability_delta",
                "requested": "contract-shaped verdict from the episode",
                "effective": "light-model extraction over the collected answer",
                "reason": "extraction_instead_of_contract",
            })
        elif method == "extraction_incomplete":
            deltas.append({
                "kind": "capability_delta",
                "requested": "contract-shaped verdict from the episode",
                "effective": (
                    f"no verdict: answer ({len(text)} chars) exceeds the "
                    "single-send extraction bound"
                ),
                "reason": "extraction_incomplete_transcript_exceeds_bound",
            })
        usage["verdict_method"] = method
        usage["verdict_provenance"] = {
            "raw_transcript_chars": len(text),
            "raw_transcript_sha256": hashlib.sha256(text.encode("utf-8", "replace")).hexdigest(),
            "canonical_chars": len(canonical),
            "canonical_sha256": hashlib.sha256(canonical.encode("utf-8", "replace")).hexdigest(),
            "output_conformance": "",
            "conformance_trusted": False,
            "verdict_method": method,
            "raw_transcript_carrier": "message.native_transcript (durable response_ref)",
        }
        if deltas:
            usage["capability_delta"] = deltas
        message = {
            "content": canonical,
            "native_transcript": text,
            "verdict_method": method,
        }
        if usage.get("native_incomplete"):
            # The partial-product fact travels WITH the product, not only in
            # usage: a consumer that reads the text alone must still see it.
            message["native_incomplete"] = usage["native_incomplete"]
        return ReviewAttemptResult(message=message, usage=usage, raw_text=canonical)
