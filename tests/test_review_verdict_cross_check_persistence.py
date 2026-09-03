"""The cross_check audit that ``canonicalize_session_verdict`` emits must reach
the persisted usage for EVERY applicable verdict method (schema / strict /
light_model_extraction), not only the light branch where it used to live under
``usage['extraction']['cross_check']``.  Schema and strict silently dropped the
audit entry, breaking the audit trail.
"""

import pytest

# _fake_repo spawns real `git` subprocesses (CONTRIBUTING §4).
pytestmark = pytest.mark.serial


def _fake_repo(tmp_path):
    """Tiny git repo on disk so _cross_check_findings has ground truth to
    verify hallucinated import claims against. Inline copy of the fixture
    from test_review_cross_check.py because fixtures don't cross files."""
    import subprocess
    repo = tmp_path / "fake_repo"
    repo.mkdir()
    pkg = repo / "ouroboros"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "real_module.py").write_text("def real_call():\n    return 1\n")
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", "init"], check=True)
    return repo


class TestVerdictResultCrossCheckPersistence:
    """End-to-end through _verdict_result: the cross_check audit entry that
    canonicalize_session_verdict emits must reach the persisted usage for
    every applicable method, not only light_model_extraction."""

    def _executor(self, tmp_path, fake_repo):
        from ouroboros.review_execution import AgentSessionReviewExecutor
        from ouroboros.review_substrate import (
            ReviewAssignment,
            ReviewRequest,
            ReviewRouteKind,
            ReviewSlot,
        )

        request = ReviewRequest(
            surface="scope_review",
            goal="Review the staged change.",
            task_id="t-ibl-01b310c0ce18",
            call_type="scope_review",
            session_root=str(fake_repo),
            session_task="Review the staged diff.",
        )
        slot = ReviewSlot(
            slot_id="slot_x",
            model="api/model-a",
            timeout_sec=30,
            route=ReviewRouteKind.AGENT_SESSION,
        )
        assignment = ReviewAssignment(
            request=request,
            slot=slot,
            call_id="call-x",
            call_type="scope_review",
        )
        executor = AgentSessionReviewExecutor(assignment, llm=None)
        executor._session_usage = {}
        executor._deltas = []
        return executor

    def test_schema_path_persists_cross_check_audit(self, tmp_path):
        """Schema branch: conformance passed, hallucinated import claims must
        trigger downgrades, and the audit entry must reach result.usage."""
        text = (
            '{"findings": [{"item": "fabricated_import_x1", "verdict": "FAIL", '
            '"severity": "critical", "reason": "imports `ouroborosproject_x1`"}]}'
        )
        executor = self._executor(tmp_path, _fake_repo(tmp_path))
        executor._raw_transcript = text
        executor._conformance_passed = True

        result = executor._verdict_result()

        assert result.usage["verdict_method"] == "schema"
        assert "cross_check" in result.usage, (
            "schema branch dropped the cross_check audit — ibl-01b310c0ce18"
        )
        assert result.usage["cross_check"]["downgraded"] == 1

    def test_strict_path_persists_cross_check_audit(self, tmp_path):
        """Strict branch: conformance fails, whole text is a parseable array;
        same audit-entry preservation requirement."""
        text = (
            '[{"item": "fabricated_import_x2", "verdict": "FAIL", '
            '"severity": "critical", "reason": "imports `ouroborosproject_x2`"}]'
        )
        executor = self._executor(tmp_path, _fake_repo(tmp_path))
        executor._raw_transcript = text
        executor._conformance_passed = False

        result = executor._verdict_result()

        assert result.usage["verdict_method"] == "strict"
        assert "cross_check" in result.usage, (
            "strict branch dropped the cross_check audit — ibl-01b310c0ce18"
        )
        assert result.usage["cross_check"]["downgraded"] == 1
