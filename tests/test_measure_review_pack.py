"""devtools/measure_review_pack.py — offline by construction, honest headroom, one checkout.

Pins the three review findings against the F3-A measurer: reviewer windows come
from the Capability Evidence CACHE only (no metadata fetch, no persisted record),
the o200k BPE is never downloaded, the diff headroom is derived from the exact
zero-diff serialized message (constitutional head + stable prefix + dynamic
scaffolding + user turn) in ``estimate_tokens`` units, and ``--repo`` selects
EVERY governance corpus, the BIBLE included.
"""

from __future__ import annotations

import json
import os
import subprocess

import pytest

from devtools import measure_review_pack as mrp

SYNTHETIC_BIBLE = "# SYNTHETIC BIBLE 7f3a\n\nP0 synthetic principle: the measured checkout is this one.\n"
_GIT_ENV = {
    "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True, text=True,
                   env={**os.environ, **_GIT_ENV})


@pytest.fixture
def isolated_roots(tmp_path, monkeypatch):
    """Evidence store + settings under tmp: the measurer reads only this data root."""
    from ouroboros import config as cfg

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(cfg, "DATA_DIR", data)
    monkeypatch.setattr(cfg, "SETTINGS_PATH", data / "settings.json")
    return data


@pytest.fixture
def synthetic_repo(tmp_path):
    """A one-commit checkout with its own (tiny, unique) governance corpus and one staged edit."""
    repo = tmp_path / "checkout"
    (repo / "docs").mkdir(parents=True)
    (repo / "BIBLE.md").write_text(SYNTHETIC_BIBLE, encoding="utf-8")
    (repo / "docs" / "CHECKLISTS.md").write_text(
        "# Checklists\n\n## Repo Commit Checklist\n\n- synthetic item 7f3a\n\n## Other\n\nnot inlined\n",
        encoding="utf-8")
    (repo / "docs" / "CHECKLISTS_ARCHIVE.md").write_text("archive row 7f3a\n", encoding="utf-8")
    for rel in ("DEVELOPMENT.md", "DESIGN.md", "ARCHITECTURE.md"):
        (repo / "docs" / rel).write_text(f"# {rel} synthetic 7f3a\n", encoding="utf-8")
    (repo / "app.py").write_text("BUTTON_COLOUR = 'red'\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    (repo / "app.py").write_text("BUTTON_COLOUR = 'blue'\n", encoding="utf-8")
    _git(repo, "add", "app.py")
    return repo


def _no_bpe():
    raise mrp.TokenizerUnavailable("not cached (test)")


def test_window_resolution_reads_the_evidence_cache_only(isolated_roots, monkeypatch):
    import ouroboros.capability_evidence as ce
    import ouroboros.reviewer_window as rw
    from ouroboros.deadline_utils import utc_now
    from ouroboros.reviewer_window import reviewer_route

    def boom(*_args, **_kwargs):
        raise AssertionError("the measurer must never fetch provider metadata, probe, or persist evidence")

    # Every network seam under `probe`, the persisting writer, and the FETCHING
    # resolver the runtime uses all raise: the measurer must not reach any of them.
    for seam in ("_provider_metadata_window", "_local_health_window", "_generative_probe_window", "_store_evidence"):
        monkeypatch.setattr(ce, seam, boom)
    monkeypatch.setattr(rw, "resolve_reviewer_window", boom)
    monkeypatch.setattr(rw, "reviewer_context_window", boom)
    calls = []
    real_probe = ce.probe

    def recording_probe(*args, **kwargs):
        calls.append(kwargs)
        return real_probe(*args, **kwargs)

    monkeypatch.setattr(ce, "probe", recording_probe)
    model = "openai/gpt-5.6-terra"

    window, evidence = mrp._cached_window(model)
    assert window == rw.REVIEWER_FULL_WINDOW  # the ladder's own unknown-route default
    assert "window unknown (cache-only)" in evidence
    assert calls and all(call["allow_fetch"] is False for call in calls)
    assert not (isolated_roots / "state" / "capability_evidence.json").exists()

    # A cached record is served as-is, still without any fetch.
    provider, base_url = reviewer_route(model)
    fp = ce.route_fingerprint(provider=provider, base_url=base_url, model=model)
    store = isolated_roots / "state" / "capability_evidence.json"
    store.parent.mkdir(parents=True)
    store.write_text(json.dumps({"probes": {fp: {
        "window_tokens": 400_000, "status": "confirmed", "source": "provider_metadata",
        "ts": utc_now().isoformat(),
    }}}), encoding="utf-8")
    window, evidence = mrp._cached_window(model)
    assert (window, evidence) == (400_000, "confirmed (cache-only)")

    limit, slots = mrp._quorum_limit([model])
    assert slots[model]["window"] == 400_000 and slots[model]["evidence"] == "confirmed (cache-only)"
    assert 0 < limit == slots[model]["input_limit_chars_div_4"] < 400_000


def test_o200k_is_never_downloaded(tmp_path, monkeypatch):
    tiktoken = pytest.importorskip("tiktoken")
    import requests
    from tiktoken import load as tiktoken_load

    def network(*_args, **_kwargs):
        raise AssertionError("tiktoken reached the network")

    monkeypatch.setattr(requests, "get", network)
    monkeypatch.setattr(tiktoken.registry, "ENCODINGS", {})  # force a real BPE load
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", str(tmp_path / "empty-cache"))
    fetch_before = tiktoken_load.read_file
    with pytest.raises(mrp.TokenizerUnavailable, match="never downloads"):
        mrp._o200k()
    assert tiktoken_load.read_file is fetch_before  # the refusal binding never leaks


def test_headroom_is_derived_from_the_zero_diff_message(synthetic_repo, isolated_roots, monkeypatch):
    from ouroboros.tools import review
    from ouroboros.tools import review_multi_model as mm
    from ouroboros.tools.review_helpers import build_goal_section
    from ouroboros.utils import estimate_tokens

    limit = 10_000
    monkeypatch.setattr(mrp, "_quorum_limit", lambda models: (limit, {
        m: {"window": 1_000_000, "evidence": "window unknown (cache-only)", "input_limit_chars_div_4": limit}
        for m in models}))
    monkeypatch.setattr(mrp, "_o200k", _no_bpe)

    report = mrp.measure(synthetic_repo)

    assert report["staged_paths"] == ["app.py"]
    assert report["tokenizer"].startswith("o200k unavailable (cache-only)")
    assert report["touched_pack"]["after"]["o200k"] is None
    fit = report["fit"]
    assert fit["units"] == mrp.FIT_UNITS and "estimate_tokens" in fit["units"]
    assert report["zero_diff_message"]["components"] == [
        "constitutional_head_preamble_plus_BIBLE", "stable_prefix",
        "dynamic_scaffolding_empty_pack_and_diff", "user_turn",
    ]
    # The expected message is rebuilt from the RUNTIME's own pieces, in wire
    # order: head (review_multi_model), stable + "\n" + dynamic (review's
    # _assemble_prompt with an empty pack and diff), the fixed user turn.
    head = (mm._CONSTITUTIONAL_PREAMBLE + "### BIBLE.md (Full Text)\n\n" + SYNTHETIC_BIBLE
            + "\n\n---\n\n## REVIEW INSTRUCTIONS\n\n")
    stable = mrp._governance_prefix(synthetic_repo)["stable_prefix"]
    dynamic = review._REVIEW_PROMPT_TEMPLATE_DYNAMIC.format(
        goal_section=build_goal_section("", "", ""), scope_section="", current_files_section="",
        rebuttal_section="", review_history_section="", diff_text="", changed_files="app.py")
    zero_message = head + stable + "\n" + dynamic + mrp.TRIAD_USER_TURN
    assert report["zero_diff_message"]["total"]["chars"] == len(zero_message)
    assert fit["zero_diff_message_chars_div_4"] == estimate_tokens(zero_message)
    assert fit["headroom_after_zero_diff_message"] == limit - estimate_tokens(zero_message)
    pack = report["touched_pack"]
    assert fit["headroom_for_diff_before"] == fit["headroom_after_zero_diff_message"] - pack["before"]["chars_div_4"]
    assert fit["headroom_for_diff_after"] == fit["headroom_after_zero_diff_message"] - pack["after"]["chars_div_4"]
    assert fit["uncounted_by_fit_triad_prompt_chars_div_4"] == estimate_tokens(head + mrp.TRIAD_USER_TURN)
    # The F3-A formula (limit - stable prefix - pack) overstated headroom by the
    # head + scaffolding + user turn it never counted.
    assert fit["headroom_for_diff_after"] < limit - estimate_tokens(stable) - pack["after"]["chars_div_4"]


def test_repo_selects_every_governance_corpus(synthetic_repo, isolated_roots, monkeypatch):
    monkeypatch.setattr(mrp, "_quorum_limit", lambda models: (10_000, {}))
    monkeypatch.setattr(mrp, "_o200k", _no_bpe)

    head = mrp._constitutional_head(synthetic_repo)
    assert SYNTHETIC_BIBLE in head
    assert (mrp.REPO_ROOT / "BIBLE.md").read_text(encoding="utf-8") not in head
    prefix = mrp._governance_prefix(synthetic_repo)
    # Section cut at the next "\n## " (its own trailing newline kept) + "\n\n" +
    # the stripped archive — the runtime's `_load_checklist_section` join.
    assert prefix["checklist_section"] == "## Repo Commit Checklist\n\n- synthetic item 7f3a\n\n\narchive row 7f3a"
    assert "not inlined" not in prefix["stable_prefix"]
    for rel in ("DEVELOPMENT.md", "DESIGN.md", "ARCHITECTURE.md"):
        assert f"# {rel} synthetic 7f3a" in prefix["stable_prefix"]

    report = mrp.measure(synthetic_repo)
    parts = report["governance_prefix"]["parts"]
    assert parts["constitutional_head_preamble_plus_BIBLE"]["chars"] == len(head)
    assert parts["checklist_section_plus_archive"]["chars"] == len(prefix["checklist_section"])
    assert report["zero_diff_message"]["parts"]["constitutional_head_preamble_plus_BIBLE"]["chars"] == len(head)
