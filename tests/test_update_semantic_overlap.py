"""Tests for the semantic-overlap advisory layer (v6.103.0)."""

import subprocess

import supervisor.git_ops as git_ops
from supervisor.update_semantic_overlap import (
    compute_overlap_candidates,
    detect_semantic_overlap,
    read_semantic_overlap_cache,
    write_semantic_overlap_cache,
)


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def _init_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "shared.txt").write_text("base\n")
    (repo / "other.txt").write_text("untouched\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    return repo


def _diverge(repo):
    """Base commit, then two branches independently editing shared.txt for
    "bug X" — a real overlap on non-overlapping lines, plus one branch-only
    file each so the intersection is a proper subset."""
    _git(repo, "checkout", "-q", "-b", "local")
    (repo / "shared.txt").write_text("base\nlocal fix for bug X\n")
    (repo / "local_only.txt").write_text("local\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "local: fix bug X")
    local_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    _git(repo, "checkout", "-q", "master") if _git(repo, "rev-parse", "--verify", "master").returncode == 0 \
        else _git(repo, "checkout", "-q", "main")
    _git(repo, "checkout", "-q", "-b", "upstream")
    (repo / "shared.txt").write_text("upstream fix for bug X\nbase\n")
    (repo / "upstream_only.txt").write_text("upstream\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "upstream: fix bug X differently")
    upstream_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    return local_sha, upstream_sha


def test_compute_overlap_candidates_finds_shared_file_both_sides(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    local_sha, upstream_sha = _diverge(repo)
    monkeypatch.setattr(git_ops, "REPO_DIR", repo)

    result = compute_overlap_candidates(local_sha, upstream_sha)

    assert result["merge_base_sha"]
    paths = [f["path"] for f in result["files"]]
    assert paths == ["shared.txt"]
    assert not result["truncated"]
    entry = result["files"][0]
    assert entry["local_shas"] == [local_sha]
    assert entry["upstream_shas"] == [upstream_sha]
    assert entry["local_subjects"][local_sha] == "local: fix bug X"
    assert entry["upstream_subjects"][upstream_sha] == "upstream: fix bug X differently"


def test_compute_overlap_candidates_empty_when_no_shared_files(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "local")
    (repo / "local_only.txt").write_text("local\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "local only change")
    local_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    _git(repo, "checkout", "-q", "-b", "upstream", "HEAD~1")
    (repo / "upstream_only.txt").write_text("upstream\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "upstream only change")
    upstream_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    monkeypatch.setattr(git_ops, "REPO_DIR", repo)
    result = compute_overlap_candidates(local_sha, upstream_sha)
    assert result["files"] == []


def test_compute_overlap_candidates_fails_soft_on_bad_shas(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.setattr(git_ops, "REPO_DIR", repo)
    result = compute_overlap_candidates("not-a-sha", "also-not-a-sha")
    assert result == {"merge_base_sha": "", "files": [], "truncated": False}


def test_compute_overlap_candidates_respects_max_files(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "local")
    for i in range(3):
        (repo / f"f{i}.txt").write_text(f"local {i}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "local: touch 3 files")
    local_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    _git(repo, "checkout", "-q", "-b", "upstream", "HEAD~1")
    for i in range(3):
        (repo / f"f{i}.txt").write_text(f"upstream {i}\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "upstream: touch 3 files")
    upstream_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    monkeypatch.setattr(git_ops, "REPO_DIR", repo)
    result = compute_overlap_candidates(local_sha, upstream_sha, max_files=2)
    assert len(result["files"]) == 2
    assert result["truncated"] is True


def test_detect_semantic_overlap_short_circuits_on_no_files():
    result = detect_semantic_overlap("a" * 40, "b" * 40, {"files": []})
    assert result == {"available": True, "flags": [], "model": "", "computed_at": result["computed_at"]}


def test_detect_semantic_overlap_fails_soft_when_model_raises(monkeypatch):
    import supervisor.update_semantic_overlap as uso

    def _boom():
        raise RuntimeError("no model configured")

    monkeypatch.setattr(uso, "_semantic_overlap_model", _boom)
    candidates = {"files": [{"path": "x.py", "local_shas": ["a" * 40], "upstream_shas": ["b" * 40],
                              "local_subjects": {}, "upstream_subjects": {}}]}
    result = detect_semantic_overlap("a" * 40, "b" * 40, candidates)
    assert result["available"] is False
    assert result["flags"] == []
    assert "error" in result


def test_semantic_overlap_cache_roundtrip(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    monkeypatch.setattr(git_ops, "DRIVE_ROOT", tmp_path)
    import supervisor.git_ops as _g

    monkeypatch.setattr(_g, "_git_dir", lambda: repo / ".git")

    base_sha, target_sha = "a" * 40, "b" * 40
    assert read_semantic_overlap_cache(base_sha, target_sha) is None

    payload = {"available": True, "flags": [{"path": "x.py", "verdict": "unclear"}]}
    write_semantic_overlap_cache(base_sha, target_sha, payload)
    assert read_semantic_overlap_cache(base_sha, target_sha) == payload

    # A stale sha pair (different target) must never return a mismatched cache.
    assert read_semantic_overlap_cache(base_sha, "c" * 40) is None
