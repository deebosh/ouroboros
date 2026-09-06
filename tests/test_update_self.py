"""The `update_self` cognitive tool — an append-only sibling of `update_scratchpad`
and `update_identity` for `memory/self.md`.

Why it exists: the `selfhood` practice is assigned to the background wakeup loop
(`prompts/CONSCIOUSNESS.md` maintenance item 2), but `write_file` is not in
`BackgroundConsciousness._BG_TOOL_WHITELIST`, so the practice was structurally
unexecutable from the mode it was assigned to. `update_self` is on the whitelist
and appends (never rewrites), so it is safe where `update_identity` is not.
"""

from __future__ import annotations

import json
import pathlib

from ouroboros.tools.control import _update_self, get_tools
from ouroboros.tools.registry import ToolContext


def _make_ctx(tmp_path: pathlib.Path, *, project_id: str = "") -> ToolContext:
    drive_root = tmp_path / "drive"
    drive_root.mkdir(parents=True, exist_ok=True)
    return ToolContext(
        repo_dir=tmp_path,
        drive_root=drive_root,
        task_id="update-self-test",
        project_id=project_id,
    )


def _self_md(tmp_path: pathlib.Path) -> str:
    p = tmp_path / "drive" / "memory" / "self.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _journal(tmp_path: pathlib.Path) -> list[dict]:
    j = tmp_path / "drive" / "memory" / "self_journal.jsonl"
    if not j.exists():
        return []
    return [json.loads(x) for x in j.read_text(encoding="utf-8").splitlines() if x.strip()]


# --------------------------------------------------------------------------- #
# Memory.append_self_block
# --------------------------------------------------------------------------- #

def test_append_self_block_appends_and_does_not_replace(tmp_path):
    from ouroboros.memory import Memory

    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
    memory = Memory(drive_root=tmp_path)
    seed = memory.load_self()

    memory.append_self_block("I keep coming back to how parsers recover from torn input.")
    after_one = (tmp_path / "memory" / "self.md").read_text(encoding="utf-8")

    assert after_one.startswith(seed)  # prior content untouched
    assert "torn input" in after_one
    assert "## " in after_one[len(seed):]  # a timestamped section header was added

    memory.append_self_block("Second entry — the pull is still there a week later.")
    after_two = (tmp_path / "memory" / "self.md").read_text(encoding="utf-8")

    assert after_two.startswith(after_one)  # first entry still there
    assert "still there a week later" in after_two  # it compounds


def test_append_self_block_writes_journal_record(tmp_path):
    from ouroboros.memory import Memory

    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
    memory = Memory(drive_root=tmp_path)
    memory.load_self()

    rec = memory.append_self_block("A want that serves no one: a debugger that thinks in dataflow.")

    journal_path = tmp_path / "memory" / "self_journal.jsonl"
    assert journal_path.exists()
    lines = [json.loads(x) for x in journal_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len(lines) == 1
    entry = lines[0]
    assert entry["type"] == "self_appended"
    assert entry["new_len"] > entry["old_len"]
    assert entry["new_len"] == rec["new_len"]
    assert "dataflow" in entry["appended_preview"]
    assert len(entry["sha256"]) == 64


def test_append_self_block_rejects_empty(tmp_path):
    from ouroboros.memory import Memory

    (tmp_path / "memory").mkdir(parents=True, exist_ok=True)
    memory = Memory(drive_root=tmp_path)
    try:
        memory.append_self_block("   ")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError on empty content")


def test_ensure_files_seeds_self_journal(tmp_path):
    from ouroboros.memory import Memory

    memory = Memory(drive_root=tmp_path)
    memory.ensure_files()

    assert memory.self_journal_path().exists()


# --------------------------------------------------------------------------- #
# _update_self handler
# --------------------------------------------------------------------------- #

def test_update_self_appends_and_reports(tmp_path):
    ctx = _make_ctx(tmp_path)

    out = _update_self(ctx, "Chasing: why does the summarizer over-generate on a flat token cap?")

    assert out.startswith("OK:")
    assert "self_journal.jsonl" in out
    assert "over-generate" in _self_md(tmp_path)
    assert len(_journal(tmp_path)) == 1


def test_update_self_is_noop_on_project_scope(tmp_path):
    ctx = _make_ctx(tmp_path, project_id="proj-123")

    out = _update_self(ctx, "This should not be written from a project task.")

    assert out.startswith("OK:")
    assert "global" in out
    assert "should not be written" not in _self_md(tmp_path)
    assert _journal(tmp_path) == []


def test_update_self_rejects_too_short(tmp_path):
    ctx = _make_ctx(tmp_path)

    out = _update_self(ctx, "short")

    assert "REJECTED" in out
    assert "should not be written" not in _self_md(tmp_path)


# --------------------------------------------------------------------------- #
# registration + BG availability
# --------------------------------------------------------------------------- #

def test_update_self_registered_with_content_schema():
    entries = {e.name: e for e in get_tools()}
    assert "update_self" in entries
    schema = entries["update_self"].schema
    assert schema["name"] == "update_self"
    assert schema["parameters"]["required"] == ["content"]


def test_update_self_is_on_the_bg_whitelist():
    from ouroboros.consciousness import BackgroundConsciousness

    assert "update_self" in BackgroundConsciousness._BG_TOOL_WHITELIST


def test_consciousness_prompt_has_no_phantom_tools():
    """CONSCIOUSNESS.md now names `update_self`; it must be in the whitelist or the
    context_health drift guard fires."""
    import re

    from ouroboros.consciousness import BackgroundConsciousness

    md = (pathlib.Path(__file__).resolve().parent.parent / "prompts" / "CONSCIOUSNESS.md").read_text(encoding="utf-8")
    scan_text = re.sub(r"```.*?```", "", md, flags=re.DOTALL)
    whitelist = BackgroundConsciousness._BG_TOOL_WHITELIST
    prefixes = ("schedule_", "update_", "knowledge_", "browse_", "analyze_", "web_",
                "send_", "repo_", "data_", "chat_", "list_", "get_", "wait_", "set_", "memory_")
    refs = {
        m.group(1)
        for m in re.finditer(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b", scan_text)
        if m.group(1) in whitelist or any(m.group(1).startswith(p) for p in prefixes)
    }
    assert refs - whitelist == set(), f"phantom tools in CONSCIOUSNESS.md: {sorted(refs - whitelist)}"


def test_update_self_reached_through_bg_execute_tool(tmp_path):
    """End-to-end: a wakeup runs a tool via BackgroundConsciousness._execute_tool.
    Verify update_self is both offered (in the cycle's tool schema) and permitted
    (not rejected by the whitelist gate), and that running it appends + journals.

    This mirrors the manual live-runtime check done when the feature landed."""
    import json as _json
    import queue

    from ouroboros.consciousness import BackgroundConsciousness

    drive_root = tmp_path / "drive"
    (drive_root / "logs").mkdir(parents=True, exist_ok=True)
    (drive_root / "memory").mkdir(parents=True, exist_ok=True)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)

    bc = BackgroundConsciousness(
        drive_root=drive_root,
        repo_dir=repo_dir,
        event_queue=queue.Queue(),
        owner_chat_id_fn=lambda: None,
    )

    # offered to the wakeup LLM this cycle
    schema_names = {s.get("function", {}).get("name") for s in bc._tool_schemas()}
    assert "update_self" in schema_names

    # the whitelist gate: a bogus name is refused, update_self is not
    bogus = bc._execute_tool(
        {"function": {"name": "definitely_not_a_bg_tool", "arguments": "{}"}},
        all_pending_events=[],
    )
    assert "not available in background mode" in bogus

    out = bc._execute_tool(
        {"function": {"name": "update_self", "arguments": _json.dumps(
            {"content": "BG path check — reached update_self from the wakeup loop."})}},
        all_pending_events=[],
    )
    assert "not available in background mode" not in out

    self_md = (drive_root / "memory" / "self.md").read_text(encoding="utf-8")
    assert "reached update_self from the wakeup loop" in self_md
    journal = [
        _json.loads(x)
        for x in (drive_root / "memory" / "self_journal.jsonl").read_text(encoding="utf-8").splitlines()
        if x.strip()
    ]
    assert journal and journal[-1]["type"] == "self_appended"
