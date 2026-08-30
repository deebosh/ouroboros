"""Per-task context_section_sizes event emission (closes ibl-local-29a81a770be0).

The instrument-first step: ``measure_context_section_bytes`` is called from the
message-assembly path on the FIRST ``build_context_fit_plan`` invocation for a
task, the result is stashed on ``ctx.context_section_measurement``, and a single
structured ``context_section_sizes`` event is appended to ``logs/events.jsonl``.
Subsequent rounds see the stashed measurement and skip. No behavioural change
to the rendered context — that is the follow-up.

This test pins:
  * the event is emitted once with the expected keys;
  * the per-section byte map reconciles to ``assembled_system_content_bytes``
    within the recorded ``system_side_delta_bytes``;
  * a second call on the same ctx is a no-op (single event);
  * ``last_section_measurement(ctx)`` returns the stashed measurement.
"""

from __future__ import annotations

import json
import pathlib
import sys
from types import SimpleNamespace

import pytest


# _capture_context_core touches memory/repo_path; we bypass it by patching the
# symbol on the module where the wrapper looks it up (ouroboros.context).
def _patched_core(monkeypatch, *, architecture_text="ARCH", development_text="DEV"):
    """Patch the wrapper's _capture_context_core to return a controlled core."""
    from ouroboros import context as context_module
    from ouroboros.context_fit import ContextCore

    def _fake_capture(env, memory, task, review_context_builder, ctx):
        return ContextCore(
            base_prompt="BP",
            bible_md="BIBLE",
            architecture_md=architecture_text,
            development_md=development_text,
            semi_stable_text="SS",
            dynamic_text="DY",
            user_content_json='"u"',
            docs_need_development=False,
        )

    monkeypatch.setattr(context_module, "_capture_context_core", _fake_capture)
    # reference_doc_sections is called by measure_context_section_bytes to
    # slice ARCH/DEV into the section breakdown; return a deterministic triple
    # so the byte counts are stable and easy to reconcile.
    monkeypatch.setattr(
        context_module,
        "reference_doc_sections",
        lambda env, **kwargs: ["## A\nA1", "## B\nB1", "## C\nC1"],
    )


def _patched_resolver(monkeypatch, context_module):
    """Route resolver for the wrapped build_context_fit_plan."""
    monkeypatch.setattr(
        context_module,
        "_context_fit_route",
        lambda task, *, allow_fetch: (
            {"provider": "openai", "model": "openai/gpt-5.5", "base_url": "", "use_local": False},
            SimpleNamespace(
                route_fp="fp", status="confirmed", stale=False, window_tokens=400_000,
            ),
        ),
    )


def _make_env(tmp_path):
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True, exist_ok=True)

    def _drive_path(*parts):
        return tmp_path.joinpath(*parts)

    def _repo_path(*parts):
        return str(repo_root.joinpath(*parts))

    return SimpleNamespace(
        drive_root=str(tmp_path),
        repo_root=repo_root,
        drive_path=_drive_path,
        repo_path=_repo_path,
    )


def test_context_section_sizes_event_emitted_once_with_expected_keys(
    tmp_path, monkeypatch,
):
    """First build_context_fit_plan call emits the event; second is a no-op."""
    from ouroboros import context as context_module

    _patched_core(monkeypatch)
    _patched_resolver(monkeypatch, context_module)

    env = _make_env(tmp_path)
    memory = SimpleNamespace(ensure_files=lambda: None)
    ctx = SimpleNamespace()

    plan_first = context_module.build_context_fit_plan(
        env, memory, {"id": "task-xyz"}, preferred_mode="max", ctx=ctx,
    )
    assert plan_first is not None
    # Measurement stashed on ctx.
    assert getattr(ctx, "context_section_measurement", None) is not None
    measurement_first = ctx.context_section_measurement

    events_path = tmp_path / "logs" / "events.jsonl"
    assert events_path.exists(), "events.jsonl must be created"
    lines = events_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, f"expected exactly one event, got {len(lines)}"
    event = json.loads(lines[0])

    expected_keys = {
        "ts", "type", "task_id", "mode", "per_section_bytes",
        "reference_doc_sections_breakdown",
        "assembled_system_content_bytes", "system_side_total_bytes",
        "system_side_delta_bytes",
    }
    assert expected_keys <= set(event.keys()), (
        f"missing keys: {expected_keys - set(event.keys())}"
    )
    assert event["type"] == "context_section_sizes"
    assert event["task_id"] == "task-xyz"
    assert event["mode"] == "max"

    # The event's per_section_bytes payload is byte-for-byte the same per-section
    # byte numbers that measure_context_section_bytes stashed on ctx.
    expected_per_section = {
        k: v
        for k, v in measurement_first.items()
        if isinstance(k, str) and k.endswith("_bytes")
    }
    assert event["per_section_bytes"] == expected_per_section
    assert event["assembled_system_content_bytes"] == (
        measurement_first["assembled_system_content_bytes"]
    )
    assert event["system_side_delta_bytes"] == (
        measurement_first["system_side_delta_bytes"]
    )

    # Second call on the same ctx: measurement already present, so the gate
    # skips — no additional event is appended.
    plan_second = context_module.build_context_fit_plan(
        env, memory, {"id": "task-xyz"}, preferred_mode="max", ctx=ctx,
    )
    assert plan_second is not None
    assert ctx.context_section_measurement is measurement_first, (
        "stashed measurement must NOT be replaced on subsequent calls"
    )
    lines_after = events_path.read_text(encoding="utf-8").splitlines()
    assert len(lines_after) == 1, (
        f"second call must not emit a second event; got {len(lines_after)}"
    )


def test_per_section_sum_reconciles_to_assembled_system_content_within_delta(
    tmp_path, monkeypatch,
):
    """The recorded system_side_delta_bytes explains the gap exactly."""
    from ouroboros import context as context_module

    _patched_core(monkeypatch)
    _patched_resolver(monkeypatch, context_module)

    env = _make_env(tmp_path)
    memory = SimpleNamespace(ensure_files=lambda: None)
    ctx = SimpleNamespace()

    context_module.build_context_fit_plan(
        env, memory, {"id": "task-recon"}, preferred_mode="max", ctx=ctx,
    )

    measurement = ctx.context_section_measurement
    system_side_total = int(measurement["system_side_total_bytes"])
    rendered = measurement.get("assembled_system_content_bytes")
    delta = measurement.get("system_side_delta_bytes")

    # When reconciliation succeeds, the gap is explained exactly by the delta.
    assert rendered is not None, "rendered system content must be measurable here"
    assert (system_side_total + int(delta)) == int(rendered), (
        "system_side_total_bytes + system_side_delta_bytes "
        "must equal assembled_system_content_bytes"
    )

    # And the per-section sum reconciles to the same rendered length within the
    # recorded delta: the event's ``per_section_bytes`` payload is exactly the
    # measurement's flat ``*_bytes`` entries (same filter the emitter applies).
    per_section = {
        k: v for k, v in measurement.items()
        if isinstance(k, str) and k.endswith("_bytes")
    }
    expected_system_keys = {
        "base_prompt_bytes",
        "bible_md_bytes",
        "reference_doc_sections_bytes",
        "semi_stable_bytes",
        "dynamic_bytes",
    }
    actual_system_keys = expected_system_keys & set(per_section.keys())
    assert actual_system_keys == expected_system_keys, (
        f"missing system-side byte entries: {expected_system_keys - actual_system_keys}"
    )
    summed = sum(int(per_section[k]) for k in expected_system_keys)
    assert (summed + int(delta)) == int(rendered), (
        f"event per-section sum {summed} + delta {delta} != rendered {rendered}"
    )


def test_last_section_measurement_returns_stashed_measurement(
    tmp_path, monkeypatch,
):
    """The probe's reader helper exposes what the wrapper stashed."""
    from ouroboros import context as context_module

    _patched_core(monkeypatch)
    _patched_resolver(monkeypatch, context_module)

    env = _make_env(tmp_path)
    memory = SimpleNamespace(ensure_files=lambda: None)
    ctx = SimpleNamespace()

    assert context_module.last_section_measurement(ctx) is None, (
        "pre-call: last_section_measurement must return None"
    )

    context_module.build_context_fit_plan(
        env, memory, {"id": "task-reader"}, preferred_mode="max", ctx=ctx,
    )

    measurement = context_module.last_section_measurement(ctx)
    assert measurement is not None
    assert measurement["mode"] == "max"
    assert measurement is ctx.context_section_measurement, (
        "last_section_measurement must read the same object the wrapper stashed"
    )


def test_no_event_when_ctx_is_none(
    tmp_path, monkeypatch,
):
    """When ctx is None, the gate skips; the message-assembly path is unaffected.

    The wrapper is also called from contexts that do not have a per-task ctx
    (e.g. commit/scope reviewers per the legacy comment). The probe must
    stay a no-op there — no AttributeError, no event.
    """
    from ouroboros import context as context_module

    _patched_core(monkeypatch)
    _patched_resolver(monkeypatch, context_module)

    env = _make_env(tmp_path)
    memory = SimpleNamespace(ensure_files=lambda: None)

    plan = context_module.build_context_fit_plan(
        env, memory, {"id": "task-noctx"}, preferred_mode="max", ctx=None,
    )
    assert plan is not None

    events_path = tmp_path / "logs" / "events.jsonl"
    if events_path.exists():
        lines = events_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 0, (
            f"no ctx must mean no event; got {len(lines)}"
        )
