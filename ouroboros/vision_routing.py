"""One image projection policy and the route-scoped image-input evidence it reads.

A model name is not evidence. Whether a route accepts image input is a fact about
the exact resolved route (provider, endpoint, API surface, routing options and the
available account identity), recorded only from a catalog Ouroboros already reads
(OpenRouter ``/models``) and valid for 24 hours from that catalog's response. No
record means unknown, and unknown never withholds the owner's image: the route
receives the pixels and answers for itself. A Claudexor route answers from its
account's catalog (``provider_models.supports_vision``).

``prepare_messages_for_send`` is the only place that turns image blocks into
captions or markers; transport builders encode what they are given. For a Main
send the owner's image mode decides:

* Auto: pixels unless the route's own metadata says no; then a caption from the
  explicit vision slot or an automatic candidate that is not confirmed-no, else a
  marker naming that metadata and its date.
* Inline: pixels even when metadata says no; the provider's refusal is shown.
* Caption: a caption, never pixels.
* Off: a marker, and no hidden image work.

Our own lanes that cannot carry image bytes (local llama.cpp, GigaChat) are named
as ours in the marker. A VLM or caption call (``purpose`` "vlm"/"caption") names
its model explicitly and receives the pixels; it never captions, so a caption
cannot recurse into another caption.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from hashlib import sha256
import json
import logging
import pathlib
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from ouroboros.config import get_image_input_mode, get_vision_caption_timeout_sec, get_vision_model, resolve_effort
from ouroboros.deadline_utils import owner_deadline_exhausted, transport_timeout_with_deadline
from ouroboros.observability import new_call_id, persist_call
from ouroboros.provider_models import provider_for_model, supports_vision
from ouroboros.utils import emit_cognitive_operation_event, utc_now_iso
from ouroboros.config import runtime_setting

log = logging.getLogger(__name__)


_CAPTION_PROMPT = (
    "Describe this image in detail for a coding/research agent that may not see pixels. "
    "Be objective and include visible text, UI state, diagrams, layout, and salient details. "
    "Do not infer hidden facts."
)

# The capability_evidence.json namespace of route-scoped image-input records.
EVIDENCE_NAMESPACE = "image_input"
# A catalog's statement holds for 24 hours from its response; older is unknown.
_EVIDENCE_FRESH_SEC = 24 * 3600
# Our own lanes that cannot carry image bytes: llama.cpp is launched without a
# vision handler, and the GigaChat lane flattens message content to text.
_OWN_LANES_WITHOUT_IMAGES = {"local": "local llama.cpp", "gigachat": "GigaChat"}
_IMAGE_TYPES = frozenset({"image_url", "image"})
_OFF_MARKER = "[image omitted: the image-input mode is Off]"
# A documented field present in a shape this parser does not read.
_MALFORMED = object()


def _vision_finalization_reserve() -> float:
    try:
        from ouroboros.config import get_finalization_grace_sec
        return float(get_finalization_grace_sec())
    except Exception:
        return 0.0


@dataclass
class VisionRoutingContext:
    model: str
    llm: Any
    accumulated_usage: Dict[str, Any]
    drive_root: pathlib.Path | None = None
    task_id: str = ""
    event_queue: Any = None
    use_local: bool = False
    task_attempt: Any = None
    deadline_ts: Any = None
    model_role: str = "main"
    model_account_override: str | None = None


# --- Evidence: one parser, one route scope, one store ---------------------------

def _modalities(value: Any) -> Any:
    if value is None or value == []:
        return None
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return "image" in {item.strip().lower() for item in value}
    return _MALFORMED


def _strict_flag(value: Any) -> Any:
    if value is None:
        return None
    return value if isinstance(value, bool) else _MALFORMED


def _supported_object(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict) and isinstance(value.get("supported"), bool):
        return value["supported"]
    return _MALFORMED


def image_input_from_row(row: Any) -> Optional[bool]:
    """Image input stated by one catalog row: True/False, or None when the row is unclear.

    Only a documented, unambiguous field counts: a non-empty input-modality list
    (``architecture.input_modalities``), a strict JSON bool in ``supports_vision``
    or ``capabilities.supports_vision``, or Anthropic's
    ``capabilities.image_input.supported``. A missing, null or empty field says
    nothing; any other shape, or fields that disagree, make the whole row unknown,
    so a catalog format this parser does not know can never become a "no".
    """
    if not isinstance(row, dict):
        return None
    architecture, capabilities = row.get("architecture"), row.get("capabilities")
    if any(part is not None and not isinstance(part, dict) for part in (architecture, capabilities)):
        return None
    architecture, capabilities = architecture or {}, capabilities or {}
    found = [
        _modalities(architecture.get("input_modalities")),
        _strict_flag(row.get("supports_vision")),
        _strict_flag(capabilities.get("supports_vision")),
        _supported_object(capabilities.get("image_input")),
    ]
    if any(value is _MALFORMED for value in found):
        return None
    verdicts = {value for value in found if value is not None}
    return verdicts.pop() if len(verdicts) == 1 else None


def image_route_scope(target: Mapping[str, Any]) -> str:
    """Key of the image-input namespace for one resolved route, or "" when its scope is unknown.

    Provider, endpoint, API surface, routing options and the available account
    identity: what one catalog response describes. The model is a member of the
    record, because one response observes every model of its endpoint at once.
    """
    provider = str(target.get("provider") or "").strip().lower()
    endpoint = str(target.get("base_url") or "").strip().rstrip("/").lower()
    if not provider or not endpoint:
        return ""
    routing: Dict[str, Any] = {}
    if provider == "openrouter":
        from ouroboros.llm_routing import _resolve_or_provider

        routing = _resolve_or_provider()
    scope = {
        "provider": provider, "endpoint": endpoint,
        "surface": "messages" if provider == "anthropic" else "chat.completions",
        "routing": routing, "account": str(target.get("account_fingerprint") or ""),
    }
    return sha256(json.dumps(scope, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:24]


def record_catalog_image_input(provider: str, base_url: str, rows: Any, *, source: str) -> None:
    """Persist one catalog response's image-input statements for its route scope.

    Called by the readers Ouroboros already runs, right after the response
    arrives, so the record's time is the catalog's observation time. The newest
    response replaces the scope's record whole: a model it no longer describes
    becomes unknown. Never raises; a lost write leaves the route unknown.
    """
    try:
        key = image_route_scope({"provider": provider, "base_url": base_url})
        if not key:
            return
        statements: Dict[str, bool] = {}
        for row in rows if isinstance(rows, list) else []:
            model = str(row.get("id") or row.get("name") or "") if isinstance(row, dict) else ""
            verdict = image_input_from_row(row)
            if model and verdict is not None:
                statements[model] = verdict
        from ouroboros import capability_evidence as evidence

        evidence._store_evidence(evidence.canonical_evidence_root(), EVIDENCE_NAMESPACE, key, {
            "source": source, "observed_at": utc_now_iso(), "models": statements,
        })
    except Exception:
        log.debug("image-input evidence write failed", exc_info=True)


@dataclass(frozen=True)
class ImageInputEvidence:
    """One route's image-input fact: ``verdict`` True, False or None (unknown)."""

    verdict: Optional[bool] = None
    source: str = ""
    observed_at: str = ""


def route_image_input(model: str) -> ImageInputEvidence:
    """The fresh catalog statement for the exact API route ``model`` resolves to, else unknown.

    Reads the shared store on every call and never fetches, so a worker or a
    cold VLM child sees what any process recorded under the canonical root.
    Claudexor routes answer from their account catalog instead; a local lane has
    no catalog.
    """
    name = str(model or "").strip()
    if not name or provider_for_model(name) in {"local", "claudexor"}:
        return ImageInputEvidence()
    try:
        from ouroboros import capability_evidence as evidence
        from ouroboros.llm import LLMClient

        target = LLMClient()._resolve_remote_target(name)
        key = image_route_scope(target)
        records = evidence._load(evidence.canonical_evidence_root()).get(EVIDENCE_NAMESPACE) or {}
        record = records.get(key) if key else None
        if not isinstance(record, dict):
            return ImageInputEvidence()
        verdict = (record.get("models") or {}).get(str(target.get("resolved_model") or ""))
        observed_at = str(record.get("observed_at") or "")
        if not isinstance(verdict, bool) or evidence._age_seconds(observed_at) > _EVIDENCE_FRESH_SEC:
            return ImageInputEvidence()
        return ImageInputEvidence(verdict, str(record.get("source") or ""), observed_at)
    except Exception:
        log.debug("image-input evidence read failed; the route stays unknown", exc_info=True)
        return ImageInputEvidence()


def own_lane_without_images(model: str, *, use_local: bool = False) -> str:
    """Our own lane that cannot carry image bytes, named; "" when the transport carries them.

    Decided by the resolved lane (``use_local``, our own provider namespace), never
    by what a model is called: the limit is ours, not evidence about the model.
    """
    return _OWN_LANES_WITHOUT_IMAGES.get("local" if use_local else provider_for_model(model), "")


def _metadata_says_no(model: str) -> str:
    fact = route_image_input(model)
    if fact.verdict is False and fact.source:
        return f"{fact.source}, observed {fact.observed_at[:10]} UTC, lists no image input for this model"
    if provider_for_model(model) == "claudexor":
        return "the Claudexor model catalog of this account lists no image input for this model"
    return "this route's model metadata lists no image input for this model"


def _image_input_verdict(model: str, **binding: Any) -> Optional[bool]:
    """``supports_vision`` where an unreadable fact is an unknown one.

    Waits and interruptions keep their owner (``propagate_model_error``); any
    other failure to read the route's evidence is not a "no".
    """
    try:
        return supports_vision(model, **binding)
    except Exception as error:
        from ouroboros.llm_claudexor import propagate_model_error

        propagate_model_error(error)
        log.debug("image-input evidence unreadable for %s; the route stays unknown", model, exc_info=True)
        return None


def _unique(models: Iterable[Any], seen: set) -> List[str]:
    out: List[str] = []
    for model in models:
        text = str(model or "").strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out


def choose_image_model(explicit: Iterable[Any], automatic: Iterable[Any]) -> Tuple[str, List[Tuple[str, str]]]:
    """Pick the model that receives an image for a VLM or caption call.

    Returns ``(model, passed_over)``: ``model`` is "" when nothing can take the
    image, and ``passed_over`` pairs each skipped candidate with why. An explicit
    model (an owner-switched vision route, ``vlm_query model=``, the vision slot)
    is called even when its metadata says no; only our own lane's limit skips it.
    Automatic candidates take a confirmed yes first, then an unknown; a confirmed
    no is skipped.
    """
    passed_over: List[Tuple[str, str]] = []
    seen: set = set()
    for model in _unique(explicit, seen):
        lane = own_lane_without_images(model)
        if not lane:
            return model, passed_over
        passed_over.append((model, f"our {lane} transport lane cannot carry images"))
    unknown = ""
    for model in _unique(automatic, seen):
        lane = own_lane_without_images(model)
        if lane:
            passed_over.append((model, f"our {lane} transport lane cannot carry images"))
            continue
        verdict = _image_input_verdict(model, model_role="vision")
        if verdict is True:
            return model, passed_over
        if verdict is None:
            unknown = unknown or model
        else:
            passed_over.append((model, ""))
    return unknown, passed_over


def describe_passed_over(passed_over: Iterable[Tuple[str, str]]) -> str:
    """Why each candidate was passed over, with the metadata's source and date."""
    return "; ".join(f"{model} ({why or _metadata_says_no(model)})" for model, why in passed_over)


def resolve_vision_caption_model(ctx: Any, llm: Any, *, use_local: bool = False) -> str:
    """The model that captions an image for a route that will not receive it, or "".

    An owner-switched vision route and the explicit vision slot are used even when
    their metadata says no; automatic candidates prefer a confirmed yes, then an
    unknown, and skip a confirmed no and our own lanes. A local Main route captions
    only through an explicit vision slot, so it starts no hidden remote image work.
    """
    from ouroboros.model_wait import current_model_wait

    wait = current_model_wait()
    override = wait.overrides.get("vision") if wait is not None else None
    if override:
        return "" if override.get("use_local") else choose_image_model([override["model"]], ())[0]
    explicit = str(runtime_setting("OUROBOROS_MODEL_VISION", "") or "").strip()
    if use_local and not explicit:
        return ""
    automatic = [
        get_vision_model(),
        getattr(ctx, "model", ""),
        getattr(ctx, "active_model", "") or getattr(ctx, "task_model_override", ""),
    ]
    try:
        from ouroboros.config import get_light_model, parse_fallback_chain

        automatic.append(get_light_model())
        automatic.extend(parse_fallback_chain())
    except Exception:
        pass
    try:
        automatic.append(llm.default_model())
    except Exception:
        pass
    return choose_image_model([explicit], automatic)[0]


# --- The projection policy ---------------------------------------------------

def _image_url_from_block(block: Dict[str, Any]) -> str:
    image_url = block.get("image_url")
    if isinstance(image_url, dict):
        return str(image_url.get("url") or "")
    return str(block.get("url") or "")


def _is_image(block: Any) -> bool:
    return isinstance(block, dict) and str(block.get("type") or "") in _IMAGE_TYPES


def _has_image(messages: List[Dict[str, Any]]) -> bool:
    return any(
        isinstance(msg, dict) and isinstance(msg.get("content"), list) and any(map(_is_image, msg["content"]))
        for msg in messages
    )


def _rewrite(messages: List[Dict[str, Any]],
             project: Callable[[Dict[str, Any]], Optional[str]]) -> List[Dict[str, Any]]:
    """Replace each image block for which ``project`` returns text; ``messages`` itself when none.

    The replacement keeps ``_source_path`` so a re-view hint survives; host
    metadata never reaches the wire (``_copy_messages_with_cache_policy``).
    """
    out: Optional[List[Dict[str, Any]]] = None
    for msg_index, msg in enumerate(messages):
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block_index, block in enumerate(content):
            if not _is_image(block):
                continue
            text = project(block)
            if text is None:
                continue
            if out is None:
                out = copy.deepcopy(messages)
            replacement: Dict[str, Any] = {"type": "text", "text": text}
            if block.get("_source_path"):
                replacement["_source_path"] = block["_source_path"]
            out[msg_index]["content"][block_index] = replacement
    return messages if out is None else out


def refused_images(routing: VisionRoutingContext) -> frozenset:
    """Images this route already refused in this task (seam for the semantic retry).

    The one same-round retry after a real image refusal records the refused
    blocks here; a recorded block takes its mode's text projection instead of
    pixels (Auto/Caption: a caption from another route, Inline/Off: a marker).
    Until that retry lands nothing is recorded, so every image keeps its mode's
    projection.
    """
    return frozenset()


def _image_digest(block: Dict[str, Any]) -> str:
    return sha256(_image_url_from_block(block).encode("utf-8", errors="replace")).hexdigest()


def _caption_for_block(
    block: Dict[str, Any],
    *,
    ctx: Any,
    llm: Any,
    accumulated_usage: Dict[str, Any],
    drive_root: pathlib.Path | None = None,
    task_id: str = "",
    event_queue: Any = None,
) -> Tuple[str, str]:
    """``(caption, failure)``: a paid caption, or why the attempt failed; both empty when no route."""
    memo = accumulated_usage.setdefault("_vision_caption_memo", {})
    url = _image_url_from_block(block)
    model = resolve_vision_caption_model(ctx, llm, use_local=bool(getattr(ctx, "use_local", False)))
    url_digest = sha256(url.encode("utf-8", errors="replace")).hexdigest()
    # Reuse already completed visual work even when its generating account is
    # unavailable later; the caption is content, not that account's capability.
    key = f"{url_digest}|{model}|v1"
    if memo.get(key):
        return str(memo[key]), ""
    if not model or not url:
        return "", ""
    call_id = new_call_id("vision_caption")
    prompt_ref = {}
    emit_cognitive_operation_event(
        event_queue,
        task_id=task_id,
        operation_id=call_id,
        phase="started",
        kind="vlm",
        task_attempt=getattr(ctx, "task_attempt", None),
    )
    # Receipts are BOOKKEEPING and live OUTSIDE the caption-producing try: a
    # persist_call failure used to jump into the failure arm below, REPLACE the
    # paid caption with a failure label and memoize it for the task.
    if drive_root is not None:
        try:
            prompt_ref = persist_call(
                drive_root,
                task_id=task_id,
                call_id=f"{call_id}_request",
                call_type="vision_caption_request",
                payload={"prompt": _CAPTION_PROMPT, "image_url": url, "model": model},
                manifest={"model": model},
            )
        except Exception:
            log.warning("vision caption request receipt failed", exc_info=True)
    try:
        reserve = _vision_finalization_reserve()
        if owner_deadline_exhausted(
            deadline_ts=getattr(ctx, "deadline_ts", None), reserve_sec=reserve,
        ):
            raise TimeoutError("owner deadline leaves no window for a vision caption")
        text, usage = llm.vision_query(
            _CAPTION_PROMPT,
            [{"url": url}],
            model=model,
            reasoning_effort=resolve_effort("task"),
            timeout=transport_timeout_with_deadline(
                get_vision_caption_timeout_sec(),
                deadline_ts=getattr(ctx, "deadline_ts", None),
                reserve_sec=reserve,
            ),
            purpose="caption",
        )
    except Exception as exc:
        from ouroboros.llm_claudexor import propagate_model_error
        propagate_model_error(exc)
        emit_cognitive_operation_event(
            event_queue,
            task_id=task_id,
            operation_id=call_id,
            phase="failed",
            kind="vlm",
            task_attempt=getattr(ctx, "task_attempt", None),
        )
        # NOT memoized: a memoized failure used to block every retry for this
        # image for the rest of the task.
        return "", f"{type(exc).__name__}: {exc}"
    try:
        from ouroboros.llm import add_usage

        add_usage(accumulated_usage, usage)
    except Exception:
        pass
    try:
        from ouroboros.pricing import emit_llm_usage_event

        emit_llm_usage_event(
            event_queue,
            task_id,
            model,
            usage,
            (
                float(usage["cost"])
                if isinstance(usage, dict) and usage.get("cost") is not None
                else None
            ),
            category="task",
            source="vision_caption",
        )
    except Exception:
        pass
    caption = str(text or "").strip()
    if drive_root is not None:
        try:
            persist_call(
                drive_root,
                task_id=task_id,
                call_id=f"{call_id}_response",
                call_type="vision_caption_response",
                payload={"caption": caption, "usage": usage, "prompt_ref": prompt_ref},
                manifest={"model": model},
            )
        except Exception:
            log.warning("vision caption response receipt failed", exc_info=True)
    emit_cognitive_operation_event(
        event_queue,
        task_id=task_id,
        operation_id=call_id,
        phase="finished",
        kind="vlm",
        task_attempt=getattr(ctx, "task_attempt", None),
    )
    if not caption:
        return "", f"{model} returned an empty caption"
    memo[key] = caption
    return caption, ""


def _usable_existing_caption(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    # Browser/view_image producers use bracketed labels for eviction/re-view hints
    # (e.g. "[browser screenshot ...]" / "[image: file.png]"), not visual captions.
    if text.startswith("[") and text.endswith("]"):
        return ""
    return text


def _caption_or_marker(block: Dict[str, Any], routing: VisionRoutingContext, reason: str) -> str:
    existing = _usable_existing_caption(str(block.get("_caption") or ""))
    if existing:
        return f"[image caption: {existing}]"
    caption, failure = _caption_for_block(
        block,
        ctx=routing,
        llm=routing.llm,
        accumulated_usage=routing.accumulated_usage,
        drive_root=routing.drive_root,
        task_id=routing.task_id,
        event_queue=routing.event_queue,
    )
    if caption:
        return f"[image caption: {caption}]"
    if failure:
        # A failure is reported as one, never presented as a caption.
        return f"[image caption unavailable: {failure}]"
    return f"[image omitted: {reason}; no caption route is available]"


def _lane_marker(lane: str, block: Dict[str, Any]) -> str:
    from ouroboros.llm_messages import own_lane_image_marker

    return own_lane_image_marker(lane, str(block.get("_caption") or "").strip())


def _project(messages: List[Dict[str, Any]], routing: VisionRoutingContext, purpose: str) -> List[Dict[str, Any]]:
    lane = own_lane_without_images(routing.model, use_local=routing.use_local)
    refused = refused_images(routing)
    # A VLM or caption call names its model explicitly: Inline semantics, never a caption.
    mode = get_image_input_mode() if purpose == "main" else "inline"
    if mode == "off":
        return _rewrite(messages, lambda _block: _OFF_MARKER)
    if mode == "inline":
        if lane:
            return _rewrite(messages, lambda block: _lane_marker(lane, block))
        if not refused:
            return messages
        return _rewrite(messages, lambda block: (
            "[image omitted: this route refused this image earlier in the task]"
            if _image_digest(block) in refused else None))
    if mode == "auto" and not lane:
        verdict = _image_input_verdict(routing.model, model_role=routing.model_role,
                                       model_account_override=routing.model_account_override)
        if verdict is not False:
            if not refused:
                return messages
            return _rewrite(messages, lambda block: _caption_or_marker(
                block, routing, "this route refused this image earlier in the task")
                if _image_digest(block) in refused else None)
        reason = _metadata_says_no(routing.model)
    elif lane:
        reason = f"our {lane} transport lane cannot carry images"
    else:
        reason = "Caption mode sends a caption instead of the image"
    return _rewrite(messages, lambda block: _caption_or_marker(block, routing, reason))


def withhold_images(messages: List[Dict[str, Any]], error: BaseException, *,
                    purpose: str = "main") -> List[Dict[str, Any]]:
    """The safe projection after the projection itself failed.

    Unknown evidence may send pixels; a failure to carry out the owner's mode may
    not, except where the mode sends pixels whatever the evidence (Inline, or an
    explicitly named VLM/caption model, whose own lane still marks its images).
    The marker discloses the failure.
    """
    mode = get_image_input_mode() if purpose == "main" else "inline"
    if mode == "inline":
        return messages
    marker = (f"[image omitted: image preparation failed ({type(error).__name__}); "
              f"not sent under the {mode} image mode]")
    return _rewrite(messages, lambda _block: marker)


def prepare_messages_for_send(
    messages: List[Dict[str, Any]],
    *,
    routing: VisionRoutingContext,
    purpose: str = "main",
) -> List[Dict[str, Any]]:
    """Project image blocks for one send; the canonical transcript is never changed.

    Returns ``messages`` itself when every image goes as pixels. ``purpose`` is
    "main" (the owner's image mode applies), "vlm" or "caption" (an explicitly
    named model: pixels, never a caption, so a caption cannot recurse).
    """
    if not _has_image(messages):
        return messages
    try:
        return _project(messages, routing, purpose)
    except Exception as error:
        from ouroboros.llm_claudexor import propagate_model_error

        propagate_model_error(error)
        log.warning("image projection failed; images withheld per the image mode", exc_info=True)
        return withhold_images(messages, error, purpose=purpose)
