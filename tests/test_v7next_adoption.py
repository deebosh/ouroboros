"""The v7next release bar, executed.

``scripts/v7next_adoption.py`` is the checker that says whether
``ADOPTION_v7next.md`` still describes the tree. Until this file existed the
checker was run by hand, which is how a whole upstream-train row (sync #2,
``TRAIN-F6b-f3fbfdbb``) could be deleted by a stale-base overwrite and leave
both validator modes at rc 0. This suite runs ``validate()`` on the live
manifest in both modes and drives one mutant per rule that the deletion
taught us to want, so the bar is executed by something automatic.

Deliberately NOT a CI-workflow change: ``.github/workflows/ci.yml`` is a
protected file and the default pytest lane already carries this file.
"""
from __future__ import annotations

import copy
import pathlib

import pytest

from scripts.v7next_adoption import (
    DEFERRED_OUT_OF_V70,
    OPERATOR,
    REQUIRED_TRAINS,
    parse_rows,
    validate,
)

REPO = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = REPO / "ADOPTION_v7next.md"


@pytest.fixture(scope="module")
def rows() -> list[dict[str, str]]:
    parsed, errors = parse_rows(MANIFEST.read_text(encoding="utf-8"))
    assert not errors, errors
    assert parsed, "the manifest table parsed to zero rows"
    return parsed


def _without(rows: list[dict[str, str]], row_id: str) -> list[dict[str, str]]:
    kept = [r for r in rows if r["id"] != row_id]
    assert len(kept) == len(rows) - 1, f"{row_id} is not in the manifest"
    return kept


def _mutate(rows: list[dict[str, str]], row_id: str, **cells: str) -> list[dict[str, str]]:
    out = copy.deepcopy(rows)
    for r in out:
        if r["id"] == row_id:
            r.update(cells)
            return out
    raise AssertionError(f"{row_id} is not in the manifest")


def _first_done(rows: list[dict[str, str]]) -> dict[str, str]:
    for r in rows:
        if r["status"] == "done" and "::" in r["verification hook"]:
            return r
    raise AssertionError("no done row carries a ::nodeid hook")


def test_the_live_manifest_passes_both_modes(rows):
    """The manifest on this tree is the thing the bar is about."""
    assert validate(copy.deepcopy(rows), release=False) == []
    assert validate(copy.deepcopy(rows), release=True) == []


@pytest.mark.parametrize("train_id", sorted(REQUIRED_TRAINS))
@pytest.mark.parametrize("release", [False, True])
def test_deleting_an_upstream_train_row_turns_the_bar_red(rows, train_id, release):
    """The mutant that actually happened: a whole-file overwrite drops a train
    row. It must be red in BOTH modes — the deletion in 285ab66d survived
    because the default mode was the one being run."""
    errors = validate(_without(rows, train_id), release=release)
    assert any(train_id in e for e in errors), errors


@pytest.mark.parametrize("release", [False, True])
def test_repointing_a_train_row_at_another_merge_turns_the_bar_red(rows, release):
    """A train row that no longer names its own upstream tip and merge is not a
    record of that train."""
    train_id = sorted(REQUIRED_TRAINS)[0]
    errors = validate(_mutate(rows, train_id, what="upstream train, details elsewhere"),
                      release=release)
    assert any(train_id in e for e in errors), errors


def test_a_bogus_hook_nodeid_turns_the_bar_red(rows):
    """A hook may name a test that does not exist only if nothing checks it.
    Paths were already resolved; the ``::nodeid`` half was free text."""
    victim = _first_done(rows)
    bogus = "tests/test_smoke.py::test_no_such_pin_was_ever_written"
    errors = validate(_mutate(rows, victim["id"], **{"verification hook": bogus}),
                      release=False)
    assert any("test_no_such_pin_was_ever_written" in e for e in errors), errors


def test_a_hook_nodeid_that_names_a_real_test_stays_green(rows):
    """The AST read must accept what the manifest legitimately names, including
    a data carrier (``tests/_shared.py::SETTINGS_WRITERS``) — a hook may point
    at the inventory a pin closes, not only at a function."""
    victim = _first_done(rows)
    good = ("tests/_shared.py::settings_writers + tests/_shared.py::SETTINGS_WRITERS "
            "+ tests/test_smoke.py::test_size_ratchet_transition_against_explicit_base")
    assert validate(_mutate(rows, victim["id"], **{"verification hook": good}),
                    release=False) == []


@pytest.mark.parametrize("marker", ["NOT DONE", "OPEN RESIDUAL", "not integrated yet",
                                    "still owed", "read pending"])
def test_a_done_row_that_says_it_is_not_done_turns_the_bar_red(rows, marker):
    """The contradiction this wave found six times: a row whose text says the
    work is open while its status cell says ``done``."""
    victim = _first_done(rows)
    text = f"{victim['what']} — {marker} on this tree"
    errors = validate(_mutate(rows, victim["id"], what=text), release=False)
    assert any(victim["id"] in e and "done" in e for e in errors), errors


def test_a_named_residual_clause_is_the_escape_not_a_second_status(rows):
    """A shipped row may carry an open residual — that is what ``residual:``
    declares. The rule refuses the contradiction, not the disclosure."""
    victim = _first_done(rows)
    text = f"{victim['what']} — NOT DONE for the review surfaces; residual: the migration is post-release"
    assert validate(_mutate(rows, victim["id"], what=text), release=False) == []


def test_a_post_release_row_needs_a_recorded_deferral(rows):
    """post-release is the one state that leaves the release bar. An id nobody
    recorded cannot take it."""
    victim = _first_done(rows)
    errors = validate(_mutate(rows, victim["id"], disposition="post-release",
                              status="deferred", phase="POST"), release=True)
    assert any(victim["id"] in e and "DEFERRED_OUT_OF_V70" in e for e in errors), errors


def test_a_required_row_cannot_be_parked_post_release_by_the_operator(rows, monkeypatch):
    """The property the old frozenset carried: a row of the owner-approved
    inventory leaves 7.0 only by an owner decision. Operator authority exists
    for disclosures, and must not become a way past that."""
    monkeypatch.setitem(DEFERRED_OUT_OF_V70, "ABI-8", OPERATOR)
    errors = validate(copy.deepcopy(rows), release=True)
    assert any("ABI-8" in e and "owner decision" in e for e in errors), errors
