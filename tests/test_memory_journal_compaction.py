"""CPL4-C16 pins (owner batch №8, 4A): old journal snapshots go digest-only.

Fresh entries keep their full old/new text; entries older than GC retention
keep only sha256 + length (existing hashes never overwritten) and gain
``content_digested``. Unparseable lines and rows without a readable ``ts``
survive byte-identical; the consciousness observation inbox is out of scope.
"""

from __future__ import annotations

import hashlib
import json

from ouroboros.memory_journal_compaction import compact_memory_journal_snapshots
from ouroboros.utils import utc_now_iso

_OLD_TS = "2020-01-01T00:00:00+00:00"


def _journal(tmp_path, rel):
    path = tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def test_old_rows_digested_fresh_rows_kept_full(tmp_path):
    path = _journal(tmp_path, "memory/identity_journal.jsonl")
    old_row = {
        "ts": _OLD_TS, "old_content": "I was v1", "new_content": "I am v2",
        "old_sha256": "pinned-old-hash", "old_len": 8,
    }
    fresh_row = {"ts": utc_now_iso(), "old_content": "I am v2", "new_content": "I am v3"}
    broken_line = "{not json at all\n"
    path.write_text(
        json.dumps(old_row) + "\n" + broken_line + json.dumps(fresh_row) + "\n",
        encoding="utf-8",
    )

    report = compact_memory_journal_snapshots(tmp_path)

    lines = path.read_text(encoding="utf-8").splitlines()
    digested = json.loads(lines[0])
    assert "old_content" not in digested and "new_content" not in digested
    assert digested["content_digested"] is True
    assert digested["old_sha256"] == "pinned-old-hash"  # existing hash never overwritten
    assert digested["new_sha256"] == hashlib.sha256(b"I am v2").hexdigest()
    assert digested["new_len"] == len("I am v2")
    assert lines[1] == broken_line.rstrip("\n")  # unreadable: byte-identical
    kept = json.loads(lines[2])
    assert kept["old_content"] == "I am v2" and kept["new_content"] == "I am v3"
    assert report["digested"] == {"memory/identity_journal.jsonl": 1}


def test_patterns_history_gains_derived_digests(tmp_path):
    path = _journal(tmp_path, "memory/knowledge/patterns_history.jsonl")
    path.write_text(json.dumps({
        "ts": _OLD_TS, "task_id": "t", "markers": ["m"],
        "old_content": "old body", "new_content": "new body\n",
    }) + "\n", encoding="utf-8")

    compact_memory_journal_snapshots(tmp_path)

    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["old_sha256"] == hashlib.sha256(b"old body").hexdigest()
    assert row["new_len"] == len("new body\n")
    assert "old_content" not in row and row["content_digested"] is True


def test_row_without_readable_ts_keeps_full_text(tmp_path):
    path = _journal(tmp_path, "memory/knowledge_history.jsonl")
    original = json.dumps({"topic": "x", "old_content": "a", "new_content": "b"}) + "\n"
    path.write_text(original, encoding="utf-8")

    report = compact_memory_journal_snapshots(tmp_path)

    assert path.read_text(encoding="utf-8") == original
    assert not report["digested"] and not report["errors"]


def test_observation_inbox_is_out_of_scope(tmp_path):
    inbox = tmp_path / "state" / "consciousness_observations.jsonl"
    inbox.parent.mkdir(parents=True)
    original = json.dumps({"ts": _OLD_TS, "op": "enqueue", "payload": "keep me"}) + "\n"
    inbox.write_text(original, encoding="utf-8")

    compact_memory_journal_snapshots(tmp_path)

    assert inbox.read_text(encoding="utf-8") == original


def test_startup_prune_sweeps_run_the_compaction():
    import inspect

    import ouroboros.server_maintenance as sm

    assert "compact_memory_journal_snapshots" in inspect.getsource(sm._startup_prune_sweeps)
