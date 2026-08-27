"""Regression tests for review_execution._cross_check_findings.

Closes ibl-e8665b941f7e — the triad reviewer hallucinated critical findings
twice this cycle, blocking commit_reviewed without a real basis:

1. task acfaf5dc: triad claimed ``tests/test_attachment_staging.py`` imports
   ``'ouroborosproject_naming'`` (missing dot). The real import is
   ``'ouroboros.project_naming'`` — pytest collect confirmed 29 items.

2. task b924c25544ac4f61: triad claimed a ``'Claudexor.unavailable'`` typo.
   The codebase uses ``'ClaudexorUnavailable'`` (no dot) in 194 references.

These tests prove the cross-check catches BOTH shapes without widening
to false-positive downgrades on real critical findings.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import pytest

from ouroboros.review_execution import (  # noqa: E402
    canonicalize_session_verdict,
    _cross_check_findings,
    _identifier_present_in_repo,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_repo(tmp_path: Path) -> Path:
    """A self-contained repo tree with deliberate marker files.

    The cross-check walks the tree to verify identifier presence. Using
    ``tmp_path`` isolates the tests from the real ``/opt/ouroboros`` repo —
    so the assertions don't depend on the live tree's contents and the
    tests run identically regardless of working directory.
    """
    repo = tmp_path / "fake_repo"
    repo.mkdir()
    # Real module marker (used by the positive control tests).
    (repo / "ouroboros").mkdir()
    (repo / "ouroboros" / "project_naming.py").write_text(
        "# real module marker\nPROJECT_NAME = 'ouroboros'\n",
        encoding="utf-8",
    )
    (repo / "ouroboros" / "config.py").write_text(
        "# real config module\nSETTINGS = {}\n",
        encoding="utf-8",
    )
    # Real class/identifier marker — ``ClaudexorUnavailable`` appears here.
    (repo / "claudexor_daemon.py").write_text(
        "class ClaudexorUnavailable(Exception):\n    pass\n",
        encoding="utf-8",
    )
    # Hallucinated identifier markers — these names NEVER appear in the
    # fake repo, so any finding mentioning only them is a clean test target.
    (repo / "tests").mkdir()
    (repo / "tests" / "test_attachment_staging.py").write_text(
        "from ouroboros.project_naming import PROJECT_NAME\n"
        "def test_staging_smoke():\n    assert PROJECT_NAME\n",
        encoding="utf-8",
    )
    # Vendor/cache trees that the walker must skip.
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__" / "hallucinated_fabricated.py").write_text(
        "fabricated_ouroborosproject_naming = True\n",
        encoding="utf-8",
    )
    return repo


def _critical(
    *,
    item: str = "missing import",
    reason: str,
    severity: str = "critical",
) -> Dict[str, Any]:
    """A minimal critical finding shape that mirrors the canonical contract."""
    return {
        "item": item,
        "verdict": "FAIL",
        "severity": severity,
        "reason": reason,
    }


# ---------------------------------------------------------------------------
# Counter-existence — proves the test infra itself works.
# ---------------------------------------------------------------------------

class TestIdentifierResolution:
    """Sanity tests for the verification primitive itself."""

    def test_real_dotted_module_resolves_via_filesystem(
        self, fake_repo: Path,
    ) -> None:
        # ouroboros.project_naming → repo/ouroboros/project_naming.py exists.
        assert _identifier_present_in_repo(
            "ouroboros.project_naming", fake_repo,
        )

    def test_hallucinated_dotted_module_is_absent(self, fake_repo: Path) -> None:
        # ouroborosproject_naming has NO dot, so the filesystem fast path
        # treats it as a single token (no split, nothing to resolve) and
        # the substring walker finds zero matches in real files (the
        # __pycache__ copy is excluded by the SKIP_DIRS filter).
        assert not _identifier_present_in_repo(
            "ouroborosproject_naming", fake_repo,
        )

    def test_real_camelcase_identifier_resolves_via_substring_walk(
        self, fake_repo: Path,
    ) -> None:
        # ``ClaudexorUnavailable`` is NOT a dotted module (no dot, so the
        # fast path skips). The substring walker finds it in
        # ``claudexor_daemon.py``. This proves the CamelCase catch-all
        # catches the real identifier even when fast-path fails.
        assert _identifier_present_in_repo(
            "ClaudexorUnavailable", fake_repo,
        )

    def test_hallucinated_dotted_classname_is_absent_via_walk(
        self, fake_repo: Path,
    ) -> None:
        # ``Claudexor.unavailable`` splits on the dot into ``Claudexor``
        # and ``unavailable``. Neither resolves as a module path.
        # ``Claudexor`` alone (CamelCase) appears in the file ONLY as part
        # of the joined ``ClaudexorUnavailable`` token; the substring
        # walker matches substring, so ``Claudexor`` IS found there. But
        # the FULL dotted token ``Claudexor.unavailable`` is not a
        # substring of ``ClaudexorUnavailable`` (the dot breaks the join).
        # Therefore the check on the dotted form returns False.
        assert not _identifier_present_in_repo(
            "Claudexor.unavailable", fake_repo,
        )


# ---------------------------------------------------------------------------
# Confirmed hallucination cases — ibl-e8665b941f7e regression set
# ---------------------------------------------------------------------------

class TestCrossCheckDowngradesHallucinatedFindings:
    """Both confirmed hallucination cases must downgrade critical → advisory."""

    def test_case_1_fabricated_import_downgrades(self, fake_repo: Path) -> None:
        """task acfaf5dc: triad cited ``'ouroborosproject_naming'``."""
        findings: List[Dict[str, Any]] = [_critical(
            item="attachment_staging_import",
            reason=(
                "tests/test_attachment_staging.py imports `ouroborosproject_naming` "
                "which does not exist in this repository."
            ),
        )]
        out, audit = _cross_check_findings(findings, fake_repo)
        assert len(out) == 1
        assert out[0]["severity"] == "advisory"
        assert "cross-check" in out[0]["reason"].lower()
        assert audit["checked"] == 1
        assert audit["downgraded"] == 1
        assert audit["kept"] == 0
        # The audit entry names the absent identifier verbatim.
        assert any(
            "ouroborosproject_naming" in entry.get("absent_identifiers", [])
            for entry in audit["entries"]
        )

    def test_case_2_fabricated_typo_classname_downgrades(
        self, fake_repo: Path,
    ) -> None:
        """task b924c25544ac4f61: triad cited ``'Claudexor.unavailable'`` typo."""
        findings: List[Dict[str, Any]] = [_critical(
            item="claudexor_daemon_typo",
            reason=(
                "claudexor_daemon.py:1234 uses `Claudexor.unavailable` — typo."
            ),
        )]
        out, audit = _cross_check_findings(findings, fake_repo)
        assert len(out) == 1
        assert out[0]["severity"] == "advisory"
        assert audit["downgraded"] == 1


# ---------------------------------------------------------------------------
# Defensive — never widen the downgrade scope past what the reviewer asserted
# ---------------------------------------------------------------------------

class TestCrossCheckDefensiveBoundaries:
    """Critical findings that ARE substantiated must stay critical."""

    def test_real_identifier_stays_critical(self, fake_repo: Path) -> None:
        findings: List[Dict[str, Any]] = [_critical(
            item="config_import_failure",
            reason=(
                "module `ouroboros.config` is missing the required `x` setting; "
                "this should be present."
            ),
        )]
        out, audit = _cross_check_findings(findings, fake_repo)
        assert len(out) == 1
        assert out[0]["severity"] == "critical"
        assert audit["checked"] == 1
        assert audit["downgraded"] == 0
        assert audit["kept"] == 1

    def test_partial_identifier_match_keeps_critical(
        self, fake_repo: Path,
    ) -> None:
        """When SOME identifiers exist and others don't, do NOT downgrade.

        A partial match can mean the reviewer's claim is partly true;
        downgrading here would silently unblock an actually-broken part
        of the codebase.
        """
        findings: List[Dict[str, Any]] = [_critical(
            item="mixed_claim",
            reason=(
                "Compare `ouroboros.config` (real, present) with the "
                "`ouroboros.does_not_exist_helper` (fake) — the helper "
                "is missing from the codebase, expected to exist there."
            ),
        )]
        out, audit = _cross_check_findings(findings, fake_repo)
        assert len(out) == 1
        assert out[0]["severity"] == "critical"  # conservative
        assert audit["downgraded"] == 0

    def test_vague_critical_with_no_identifiers_kept(
        self, fake_repo: Path,
    ) -> None:
        """A critical that names no concrete identifier is left untouched.

        The cross-check is intentionally narrow. A vague FAIL without
        code-shape tokens has nothing to verify against.
        """
        findings: List[Dict[str, Any]] = [_critical(
            item="vague_architecture",
            reason=(
                "FAIL: the overall architecture does not match the proposed design."
            ),
        )]
        out, audit = _cross_check_findings(findings, fake_repo)
        assert len(out) == 1
        assert out[0]["severity"] == "critical"
        assert audit["checked"] == 1
        assert audit["downgraded"] == 0
        assert audit["kept"] == 1

    def test_advisory_severity_is_never_modified(self, fake_repo: Path) -> None:
        """Advisory findings pass through unchanged — they're not blocking."""
        findings: List[Dict[str, Any]] = [_critical(
            item="advisory_naming",
            severity="advisory",
            reason="`ouroborosproject_naming` is referenced; double check spelling.",
        )]
        out, audit = _cross_check_findings(findings, fake_repo)
        assert len(out) == 1
        assert out[0]["severity"] == "advisory"  # unchanged
        assert audit["checked"] == 0  # only critical findings are checked


# ---------------------------------------------------------------------------
# Fail-closed — per-file OSError must not silently produce a downgrade
# ---------------------------------------------------------------------------

class TestFailClosedOnPerFileOSError:
    """self_consistency (triad finding ibl-e8665b941f7e follow-up).

    A partial walk where ANY per-file ``OSError`` occurred (on ``stat`` or
    ``open``) must NOT claim ``identifier == absent``. The unreadable file
    might be the one containing the identifier, so we conservatively keep
    the finding critical. The cross-check is fail-closed: incomplete
    evidence == no downgrade.
    """

    def test_open_oserror_on_real_file_fails_closed(
        self, fake_repo: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If ``open()`` raises OSError on a real file, fail-closed: True.

        The identifier ``ouroborosproject_naming`` is genuinely absent from
        the repo (the only file referencing it is inside ``__pycache__``,
        which the walker skips). Without OSError, the function returns
        ``False`` (absent). WITH OSError on one file open during the walk,
        the function MUST return ``True`` (fail-closed) so we do not
        silently downgrade a critical finding based on incomplete
        evidence.
        """
        real_open = open
        failing_path = str(fake_repo / "claudexor_daemon.py")

        def patched_open(file, *args, **kwargs):
            if str(file) == failing_path:
                raise OSError("simulated permission denied on open")
            return real_open(file, *args, **kwargs)

        monkeypatch.setattr("builtins.open", patched_open)

        # Genuinely absent identifier, but the walk hits an OSError.
        # Fail-closed must return True (treat as present).
        assert _identifier_present_in_repo(
            "ouroborosproject_naming", fake_repo,
        ) is True

    def test_stat_oserror_on_real_file_fails_closed(
        self, fake_repo: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If ``stat()`` raises OSError on a real file, fail-closed: True.

        Same fail-closed contract applies when the per-file ``stat()`` call
        (size check before reading) raises OSError — that file is the one
        we cannot prove does NOT contain the identifier.
        """
        import pathlib

        real_stat = pathlib.Path.stat
        failing_path = str(fake_repo / "claudexor_daemon.py")

        def patched_stat(self, *args, **kwargs):
            if str(self) == failing_path:
                raise OSError("simulated permission denied on stat")
            return real_stat(self, *args, **kwargs)

        monkeypatch.setattr(pathlib.Path, "stat", patched_stat)

        # Identifier is absent from readable files, but stat on one file
        # raised OSError. Fail-closed must return True.
        assert _identifier_present_in_repo(
            "ouroborosproject_naming", fake_repo,
        ) is True

    def test_walk_failure_without_oserror_still_returns_absent(
        self, fake_repo: Path,
    ) -> None:
        """Negative control: no OSError, no OSError tracker set, returns False.

        When the walker completes successfully and the identifier is NOT
        in any file, the function returns ``False`` (absent). The
        fail-closed tracker must not over-trigger on the clean path.
        """
        # Without any OSError, ``ouroborosproject_naming`` is absent from
        # the readable files (the ``__pycache__`` copy is skipped by
        # SKIP_DIRS), so the function returns False — not True.
        assert _identifier_present_in_repo(
            "ouroborosproject_naming", fake_repo,
        ) is False

    def test_real_identifier_still_found_with_clean_walk(
        self, fake_repo: Path,
    ) -> None:
        """Positive control: real identifier, clean walk, returns True.

        The fail-closed tracker must not break the happy path: when the
        identifier IS in a readable file, we still return True (without
        needing to scan the rest of the tree).
        """
        assert _identifier_present_in_repo(
            "ClaudexorUnavailable", fake_repo,
        ) is True


# ---------------------------------------------------------------------------
# Off-by-default — existing call sites see no behavioral change
# ---------------------------------------------------------------------------

class TestCrossCheckOptIn:
    def test_no_repo_root_means_passthrough(self) -> None:
        findings: List[Dict[str, Any]] = [_critical(
            item="fabricated_critical",
            reason="`ouroborosproject_naming` should exist",
        )]
        out, audit = _cross_check_findings(findings, None)
        assert out == findings
        assert audit == {"checked": 0, "downgraded": 0, "kept": 0, "entries": []}

    def test_empty_findings_returns_empty_audit(self, fake_repo: Path) -> None:
        out, audit = _cross_check_findings([], fake_repo)
        assert out == []
        assert audit == {"checked": 0, "downgraded": 0, "kept": 0, "entries": []}

    def test_nonexistent_repo_root_defaults_to_passthrough(
        self, tmp_path: Path,
    ) -> None:
        findings: List[Dict[str, Any]] = [_critical(
            item="fabricated_critical",
            reason="`ouroborosproject_naming` should exist",
        )]
        out, audit = _cross_check_findings(findings, tmp_path / "does_not_exist")
        assert out == findings
        assert audit["checked"] == 0


# ---------------------------------------------------------------------------
# Integration — cross-check inside canonicalize_session_verdict
# ---------------------------------------------------------------------------

class TestCanonicalizeSessionVerdictCrossCheck:
    """The cross-check must run inside canonicalize_session_verdict, not only
    at the helper layer, so all three parse paths (schema / strict /
    light_model_extraction) inherit the protection."""

    def test_schema_path_downgrades_hallucinated_critical(
        self, fake_repo: Path,
    ) -> None:
        raw = json.dumps({
            "findings": [{
                "item": "fabricated_import",
                "verdict": "FAIL",
                "severity": "critical",
                "reason": "imports `ouroborosproject_naming` — that doesn't exist",
            }],
        })
        text, method, usage = canonicalize_session_verdict(
            raw,
            conformance_passed=True,
            repo_root=str(fake_repo),
        )
        assert method == "schema"
        canonical_findings = json.loads(text)
        assert canonical_findings[0]["severity"] == "advisory"
        assert "cross-check" in canonical_findings[0]["reason"].lower()
        assert usage.get("cross_check", {}).get("downgraded") == 1

    def test_strict_path_downgrades_hallucinated_critical(
        self, fake_repo: Path,
    ) -> None:
        # The strict path takes the WHOLE text as the findings array —
        # produce exactly that shape (a bare list, not wrapped in
        # ``{"findings": ...}``).
        raw = json.dumps([{
            "item": "fabricated_typo",
            "verdict": "FAIL",
            "severity": "critical",
            "reason": "typo: `Claudexor.unavailable`",
        }])
        text, method, usage = canonicalize_session_verdict(
            raw,
            conformance_passed=False,
            repo_root=str(fake_repo),
        )
        assert method == "strict"
        canonical_findings = json.loads(text)
        assert canonical_findings[0]["severity"] == "advisory"
        assert usage.get("cross_check", {}).get("downgraded") == 1

    def test_repo_root_none_keeps_existing_behavior(self, fake_repo: Path) -> None:
        """When repo_root is None, critical findings pass through unchanged."""
        raw = json.dumps([{
            "item": "fabricated_import",
            "verdict": "FAIL",
            "severity": "critical",
            "reason": "imports `ouroborosproject_naming` — that doesn't exist",
        }])
        text, _method, usage = canonicalize_session_verdict(
            raw,
            conformance_passed=False,
            repo_root=None,
        )
        canonical_findings = json.loads(text)
        assert canonical_findings[0]["severity"] == "critical"
        assert "cross_check" not in usage  # audit only recorded when active

    def test_real_critical_stays_critical_through_canonicalize(
        self, fake_repo: Path,
    ) -> None:
        raw = json.dumps({
            "findings": [{
                "item": "real_issue",
                "verdict": "FAIL",
                "severity": "critical",
                "reason": (
                    "module `ouroboros.config` is missing required setup; "
                    "expected to contain configuration."
                ),
            }],
        })
        text, _method, _usage = canonicalize_session_verdict(
            raw,
            conformance_passed=True,
            repo_root=str(fake_repo),
        )
        canonical_findings = json.loads(text)
        assert canonical_findings[0]["severity"] == "critical"
