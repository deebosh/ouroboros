"""S6 C1/C2 — what a CORRUPT cancel-intent projection does to the claim fence.

The mint (``request_cancel``) reads the projection strictly: a malformed file
refuses the mutation with a typed ``CancelIntentProjectionCorrupt`` and keeps
the bytes (pinned by ``tests/test_gate_round3_fixes.py``). This module pins the
five NON-minting mutators — ``mark_finalize_control_drained``,
``mark_intent_scope``, ``claim_intent``, ``release_claim``, ``settle_intent`` —
and the watchdog's enforcement read over the same corrupt file.

The distinction being characterized is the one the module already draws in
prose: reading-for-behaviour (fail-soft with disclosure) versus
authoring-a-record (fail-closed). A mutator that reads softly cannot tell
"nobody minted an intent" from "the projection is unreadable", and the second
answer silently removes the claim-first exclusion that ``cancel_task_custody``
relies on before it tears a task down.

CURRENT BEHAVIOUR (characterization only): the five mutators read softly, so
the two answers are literally the same values. The assertions below say so
exactly, so the fix that separates them has to state what it changed.
"""

from __future__ import annotations

import json
import pathlib
import types

import pytest

from ouroboros import cancel_intents as ci


CORRUPT_CONTAINER = '"not an object"'
CORRUPT_INTENTS = '{"schema_version": 1, "intents": "not an object"}'


def _store(drive_root) -> pathlib.Path:
    return pathlib.Path(drive_root) / "state" / "cancel_intents.json"


def _trail(drive_root):
    path = pathlib.Path(drive_root) / "logs" / "supervisor.jsonl"
    if not path.is_file():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _corrupt_after_mint(tmp_path, payload: str) -> dict:
    """One live intent, then the projection is replaced by ``payload``."""
    intent = ci.request_cancel(tmp_path, "victim", reason="stop it", source="http_single")
    _store(tmp_path).write_text(payload, encoding="utf-8")
    return intent


def _mutator_answers(drive_root, task_id, intent):
    """The five non-minting mutators' answers, in a stable order."""
    return [
        ci.claim_intent(drive_root, task_id, owner="cancel_task_custody"),
        ci.settle_intent(
            drive_root, task_id, outcome="cancelled",
            expected_generation=(intent or {}).get("generation"),
            request_id=str((intent or {}).get("request_id") or ""),
        ),
        ci.release_claim(
            drive_root, task_id, error="teardown failed",
            expected_generation=(intent or {}).get("generation"),
            request_id=str((intent or {}).get("request_id") or ""),
        ),
        ci.mark_intent_scope(drive_root, task_id, ci.SCOPE_CASCADE),
        ci.mark_finalize_control_drained(drive_root, task_id),
    ]


# ---------------------------------------------------------------------------
# C1 — the five non-minting mutators over a corrupt projection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("payload", [CORRUPT_CONTAINER, CORRUPT_INTENTS])
def test_c1_non_minting_mutators_read_a_corrupt_projection_softly(tmp_path, payload):
    """C1: the mint refuses loudly; the five mutators answer "no intent".

    Both corruption shapes are covered: a malformed CONTAINER (the whole file is
    not an object) and a malformed ``intents`` value under a valid container.
    The mint refuses both; the five mutators return their absent-intent answers
    without raising and without a forensic row of their own.
    """
    intent = _corrupt_after_mint(tmp_path, payload)

    with pytest.raises(ci.CancelIntentProjectionCorrupt):
        ci.request_cancel(tmp_path, "another", reason="the mint fails closed")

    assert _mutator_answers(tmp_path, "victim", intent) == [None, None, None, False, False]
    assert _store(tmp_path).read_text(encoding="utf-8") == payload, "bytes are kept"
    assert [
        row.get("op") for row in _trail(tmp_path)
        if row.get("event") == "projection_corrupt_refused"
    ] == ["request_cancel"], "only the MINT discloses the corruption it refused"


def test_c1_a_corrupt_projection_is_indistinguishable_from_no_intent(tmp_path):
    """C1, stated as the defect it is: corruption and absence answer alike.

    Same five calls against (a) a corrupt projection holding a live intent and
    (b) a data root that never had one. Identical answers means no caller can
    tell the two apart, which is what silently removes the claim-first fence.
    """
    intent = _corrupt_after_mint(tmp_path, CORRUPT_CONTAINER)
    absent_root = tmp_path / "never_written"

    corrupt_answers = _mutator_answers(tmp_path, "victim", intent)
    absent_answers = _mutator_answers(absent_root, "victim", intent)

    assert corrupt_answers == absent_answers == [None, None, None, False, False]
    assert not _store(absent_root).exists(), "the absent case stays a first-write case"


def test_c1_custody_reads_a_corrupt_projection_as_the_legacy_no_intent_path(tmp_path):
    """C1 consequence at the caller: the claim-first fence is dropped.

    ``task_lifecycle._claim_intent`` documents two distinct shapes — ``{}``
    means "no active intent exists, custody may proceed on the legacy path
    where capture under the queue lock is the exclusion", a RAISED claim means
    "cannot tell whether a live owner exists, treat as refused". A corrupt
    projection currently produces the FIRST shape, so custody proceeds without
    the exclusivity the fence exists to prove and its later settle no-ops.
    """
    from supervisor.task_lifecycle import _claim_intent

    _corrupt_after_mint(tmp_path, CORRUPT_CONTAINER)
    q = types.SimpleNamespace(DRIVE_ROOT=tmp_path)

    assert _claim_intent(q, "victim") == {}


def test_c1_a_healthy_projection_without_the_row_answers_the_same_way(tmp_path):
    """The genuine absent-row case: a valid projection that simply holds no row
    for this task. Whatever the corruption answer becomes, THIS answer must
    stay — it is the legacy/no-intent path, not an error."""
    ci.request_cancel(tmp_path, "other", reason="unrelated")

    assert _mutator_answers(tmp_path, "victim", {}) == [None, None, None, False, False]
    assert list(ci.active_intents(tmp_path)) == ["other"], "the live row is untouched"


# ---------------------------------------------------------------------------
# C2 — the watchdog's enforcement read is fail-soft-but-loud
# ---------------------------------------------------------------------------


def test_c2_a_corrupt_projection_blinds_the_watchdog_loudly(tmp_path, monkeypatch, caplog):
    """C2: enforcement DEGRADES to "no intents" — deliberately, and loudly.

    ``active_intents(..., disclose_corruption=True)`` is a READ for behaviour,
    not an authored record: it keeps its fail-soft contract so one unreadable
    file cannot wedge the supervisor tick, and pays for it with a ``log.error``
    plus a typed forensic row. This test exists so write-side strictness cannot
    be mistaken for a licence to change the read side too: the observable below
    is the state of the art, not a defect awaiting a fix.
    """
    from supervisor import queue as q
    from supervisor import task_lifecycle as tl

    _corrupt_after_mint(tmp_path, CORRUPT_CONTAINER)
    monkeypatch.setattr(q, "DRIVE_ROOT", tmp_path)
    fed: list[str] = []
    monkeypatch.setattr(tl, "cancel_task_custody", lambda tid, **_kw: fed.append(tid))
    monkeypatch.setattr(tl, "cancel_task_by_id", lambda tid, **_kw: fed.append(tid))

    with caplog.at_level("ERROR"):
        outcomes = tl.sweep_cancel_intents()

    assert outcomes == {}, "the sweep sees no intents at all"
    assert fed == [], "no task is fed into custody even though an intent exists"
    assert any(
        "cancel-intent projection is unreadable/malformed" in record.getMessage()
        for record in caplog.records
    ), "the degrade is loud"
    assert any(
        row.get("event") == "projection_corrupt_refused"
        and row.get("op") == "active_intents"
        for row in _trail(tmp_path)
    ), "the enforcement read discloses the degrade in the durable trail"
