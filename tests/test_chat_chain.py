"""The chat-generation chain helpers moved out of ``consolidator`` keep their behavior.

The behavior cases pin the outputs the helpers produced in ``consolidator`` on the
same inputs (archive ordering, cursor-segment resolution in every branch, the
A2A-free row stream, signatures, immutable source retention). The structural
cases pin the move itself: one definition in ``chat_chain``, the legacy writer
binding the same objects, and ``retain_memory_source`` reached as a module
attribute so a single substitution reaches every caller.
"""
from __future__ import annotations

import ast
import hashlib
import json
import pathlib
from types import SimpleNamespace

from ouroboros import chat_chain as cc
from ouroboros.utils import jsonl_generation_signature

REPO = pathlib.Path(__file__).resolve().parents[1]
MOVED = ("retain_memory_source", "_ordered_chat_generation_paths", "_resolve_generation_segments",
         "_chat_log_signature", "_read_chat_entries")


def _layout(tmp_path: pathlib.Path) -> tuple[pathlib.Path, pathlib.Path]:
    logs, archive = tmp_path / "logs", tmp_path / "archive"
    logs.mkdir()
    archive.mkdir()
    return logs / "chat.jsonl", archive


def _rows(tag: str, count: int = 2) -> str:
    return "".join(json.dumps({"chat_id": 1, "direction": "in", "text": f"{tag}-{i}"}) + "\n" for i in range(count))


def _first_sha(path: pathlib.Path) -> str:
    return jsonl_generation_signature(path)["first_line_sha256"]


# --- behavior on fixed inputs ------------------------------------------------------------------

def test_ordered_chain_is_sorted_archives_then_live(tmp_path):
    live, archive = _layout(tmp_path)
    for name in ("chat_20260902T000000.jsonl", "chat_20260901T000000_1.jsonl", "chat_20260901T000000.jsonl"):
        (archive / name).write_text(_rows(name), encoding="utf-8")
    (archive / "chat_20260903T000000.txt").write_text("x", encoding="utf-8")
    (archive / "other_20260901.jsonl").write_text("x", encoding="utf-8")
    chain = cc._ordered_chat_generation_paths(live)
    assert [p.name for p in chain] == ["chat_20260901T000000.jsonl", "chat_20260901T000000_1.jsonl",
                                       "chat_20260902T000000.jsonl", "chat.jsonl"]
    assert chain[-1] == live  # the live file closes the chain even before it exists


def test_ordered_chain_without_archive_dir_is_live_only(tmp_path):
    live = tmp_path / "logs" / "chat.jsonl"
    assert cc._ordered_chat_generation_paths(live) == [live]


def test_uninitialized_cursor_takes_every_archive_but_legacy_offset_stays_live_only(tmp_path):
    live, archive = _layout(tmp_path)
    live.write_text(_rows("live"), encoding="utf-8")
    assert cc._resolve_generation_segments({}, live) == ([live], 0, False)
    old = archive / "chat_20260901T000000.jsonl"
    old.write_text(_rows("old"), encoding="utf-8")
    assert cc._resolve_generation_segments({}, live) == ([old, live], 0, False)
    # A nonzero offset without a signature is the pre-signature legacy shape: live only.
    assert cc._resolve_generation_segments({"last_consolidated_offset": 7}, live) == ([live], 7, False)
    # A signature that is not a mapping counts as absent.
    assert cc._resolve_generation_segments({"chat_log_signature": "junk"}, live) == ([old, live], 0, False)


def test_stored_signature_locates_its_generation_or_reports_a_gap(tmp_path):
    live, archive = _layout(tmp_path)
    gens = []
    for stamp in ("20260901T000000", "20260902T000000", "20260903T000000"):
        path = archive / f"chat_{stamp}.jsonl"
        path.write_text(_rows(stamp), encoding="utf-8")
        gens.append(path)
    live.write_text(_rows("live"), encoding="utf-8")

    def meta(path, offset=5):
        return {"last_consolidated_offset": offset, "chat_log_signature": {"first_line_sha256": _first_sha(path)}}

    assert cc._resolve_generation_segments(meta(live), live) == ([live], 5, False)
    assert cc._resolve_generation_segments(meta(gens[1]), live) == ([gens[1], gens[2], live], 5, False)
    assert cc._resolve_generation_segments(meta(gens[0], 0), live) == ([*gens, live], 0, False)
    missing = {"last_consolidated_offset": 5, "chat_log_signature": {"first_line_sha256": "f" * 64}}
    assert cc._resolve_generation_segments(missing, live) == ([live], 0, True)


def test_row_stream_skips_blank_broken_and_a2a_rows_only(tmp_path):
    live, _archive = _layout(tmp_path)
    assert cc._read_chat_entries(live) == []
    rows = [{"chat_id": 1, "text": "main"}, {"chat_id": 1001, "text": "project"},
            {"chat_id": -1, "text": "a2a"}, {"chat_id": "-3999", "text": "a2a-text-id"},
            {"text": "no chat id"}, {"chat_id": "main", "text": "non-numeric id"}]
    body = "\n".join(json.dumps(row) for row in rows[:3]) + "\n\n   \n{broken json\n" + \
        "\n".join(json.dumps(row) for row in rows[3:]) + "\n"
    live.write_text(body, encoding="utf-8")
    assert [row["text"] for row in cc._read_chat_entries(live)] == [
        "main", "project", "no chat id", "non-numeric id"]


def test_generation_signature_is_the_shared_utils_signature(tmp_path):
    live, _archive = _layout(tmp_path)
    assert cc._chat_log_signature is jsonl_generation_signature
    assert cc._chat_log_signature(live) == {}
    live.write_text("\n  \n" + _rows("first", 1) + _rows("second", 1), encoding="utf-8")
    first = json.dumps({"chat_id": 1, "direction": "in", "text": "first-0"})
    assert cc._chat_log_signature(live) == {
        "first_line_sha256": hashlib.sha256(first.encode("utf-8")).hexdigest(), "size": live.stat().st_size}


def test_retained_source_is_exact_and_readable_after_the_task(tmp_path):
    from ouroboros.artifacts import read_actor_source_bytes

    data = "exact bytes ✓\n".encode("utf-8")
    ref = cc.retain_memory_source(SimpleNamespace(drive_root=tmp_path, task_id="task-1"), "probe", data, "jsonl")
    assert ref["task_id"] == "task-1" and ref["canonical_root"] == str(tmp_path.resolve())
    assert ref["path"].endswith(".jsonl")
    arguments = ref["read"]["arguments"]
    assert ref["read"]["tool"] == "read_file" and arguments["root"] == "runtime_data"
    assert arguments["start_line"] == 1
    assert (tmp_path.resolve() / arguments["path"]).read_bytes() == data
    assert read_actor_source_bytes(tmp_path, "task-1", ref) == data
    default = cc.retain_memory_source(SimpleNamespace(drive_root=tmp_path, task_id=None), "probe", b"x")
    assert default["task_id"] == "consolidation" and default["path"].endswith(".md")


# --- the move itself ---------------------------------------------------------------------------

def _module_ast(relative: str) -> ast.Module:
    return ast.parse((REPO / relative).read_text(encoding="utf-8"))


def _top_level_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.ImportFrom) and node.module == "ouroboros.utils":
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def test_moved_helpers_are_defined_once_in_chat_chain():
    assert set(MOVED) <= _top_level_names(_module_ast("ouroboros/chat_chain.py"))
    assert not set(MOVED) & _top_level_names(_module_ast("ouroboros/consolidator.py"))


def test_legacy_writer_binds_the_chain_helpers_without_a_facade():
    import ouroboros.consolidator as cons

    source = (REPO / "ouroboros/consolidator.py").read_text(encoding="utf-8")
    imported = [node for node in _module_ast("ouroboros/consolidator.py").body
                if isinstance(node, ast.ImportFrom) and node.module == "ouroboros.chat_chain"]
    assert [sorted(alias.name for alias in node.names) for node in imported] == [
        ["_chat_log_signature", "_read_chat_entries", "_resolve_generation_segments"]]
    line = source.splitlines()[imported[0].lineno - 1]
    assert "noqa" not in line
    for name in ("_chat_log_signature", "_read_chat_entries", "_resolve_generation_segments"):
        assert getattr(cons, name) is getattr(cc, name)
    assert not hasattr(cons, "retain_memory_source") and not hasattr(cons, "_ordered_chat_generation_paths")


def _retain_bindings(relative: str) -> tuple[list[int], list[int], int]:
    """(module-level imports of the name, bare-name calls, ``chat_chain.retain_memory_source`` calls)."""
    tree = _module_ast(relative)
    top = [node.lineno for node in tree.body if isinstance(node, ast.ImportFrom)
           and any(alias.name == "retain_memory_source" for alias in node.names)]
    bare = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == "retain_memory_source"]
    attribute = sum(1 for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute) and node.func.attr == "retain_memory_source"
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "chat_chain")
    return top, bare, attribute


def test_retain_memory_source_is_reached_as_a_module_attribute_everywhere():
    """A module-level ``from ... import retain_memory_source`` freezes a copy that a
    substitution of ``chat_chain.retain_memory_source`` would miss; function-level
    imports and attribute calls read the module at call time."""
    top, bare, attribute = _retain_bindings("ouroboros/consolidator.py")
    assert top == [] and bare == [] and attribute > 0
    importers = []
    for path in sorted((REPO / "ouroboros").rglob("*.py")):
        relative = path.relative_to(REPO).as_posix()
        text = path.read_text(encoding="utf-8")
        if relative == "ouroboros/chat_chain.py" or "retain_memory_source" not in text:
            continue
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(a.name == "retain_memory_source" for a in node.names):
                importers.append((relative, node.module))
        top, _bare, _attr = _retain_bindings(relative)
        assert top == [], f"{relative} binds retain_memory_source at module level: lines {top}"
    assert importers and all(module == "ouroboros.chat_chain" for _path, module in importers), importers

