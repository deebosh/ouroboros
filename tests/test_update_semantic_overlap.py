"""Tests for the semantic-overlap advisory layer (v6.103.0)."""

import subprocess

import supervisor.git_ops as git_ops
from supervisor.update_semantic_overlap import (
    _diffstat_order,
    build_semantic_overlap_prompt,
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


def test_diffstat_order_puts_largest_change_first(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    _git(repo, "checkout", "-q", "-b", "local")
    (repo / "shared.txt").write_text("base\n" + "line\n" * 20)  # big change
    (repo / "other.txt").write_text("untouched\ntiny change\n")  # small change
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "local: mixed-size edits")
    local_sha = _git(repo, "rev-parse", "HEAD").stdout.strip()

    monkeypatch.setattr(git_ops, "REPO_DIR", repo)
    ordered = _diffstat_order(f"HEAD~1..{local_sha}", ["other.txt", "shared.txt"])
    assert ordered == ["shared.txt", "other.txt"]


def test_diffstat_order_empty_paths_short_circuits():
    assert _diffstat_order("HEAD~1..HEAD", []) == []


def test_diffstat_order_fails_soft_keeping_original_order(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path)
    monkeypatch.setattr(git_ops, "REPO_DIR", repo)
    # Bogus rev-range -> git fails -> original relative order is preserved.
    ordered = _diffstat_order("not-a-real..range", ["b.txt", "a.txt"])
    assert ordered == ["b.txt", "a.txt"]


def test_build_semantic_overlap_prompt_includes_header_and_each_file(monkeypatch):
    import supervisor.update_semantic_overlap as uso

    monkeypatch.setattr(uso, "_commit_diff_excerpt", lambda sha, path, **_k: f"diff for {path}@{sha[:4]}")
    candidates = {
        "files": [
            {
                "path": "a.py",
                "local_shas": ["a" * 40],
                "upstream_shas": ["b" * 40],
                "local_subjects": {"a" * 40: "local fix"},
                "upstream_subjects": {"b" * 40: "upstream fix"},
            },
            {
                "path": "c.py",
                "local_shas": ["c" * 40],
                "upstream_shas": ["d" * 40],
                "local_subjects": {"c" * 40: "local fix 2"},
                "upstream_subjects": {"d" * 40: "upstream fix 2"},
            },
        ],
    }
    prompt = build_semantic_overlap_prompt(candidates, "base" * 10, "target" * 6)

    assert prompt.startswith("You are reviewing a managed self-update")
    assert "## a.py" in prompt and "## c.py" in prompt
    assert "local fix" in prompt and "upstream fix" in prompt
    assert "diff for a.py@" in prompt


def test_build_semantic_overlap_prompt_discloses_truncation_over_budget(monkeypatch):
    import supervisor.update_semantic_overlap as uso

    monkeypatch.setattr(uso, "_MAX_PROMPT_CHARS", len(uso._OVERLAP_PROMPT_HEADER) + 200)
    monkeypatch.setattr(uso, "_commit_diff_excerpt", lambda *_a, **_k: "")
    candidates = {
        "files": [
            {"path": f"f{i}.py", "local_shas": [], "upstream_shas": [],
             "local_subjects": {}, "upstream_subjects": {}}
            for i in range(20)
        ],
    }
    prompt = build_semantic_overlap_prompt(candidates, "a" * 40, "b" * 40)

    assert "more candidate file(s) omitted for prompt size" in prompt
    assert not all(f"## f{i}.py" in prompt for i in range(20))


def test_build_semantic_overlap_prompt_empty_files_is_just_the_header():
    prompt = build_semantic_overlap_prompt({"files": []}, "a" * 40, "b" * 40)
    assert prompt.startswith("You are reviewing a managed self-update")
    assert "##" not in prompt
