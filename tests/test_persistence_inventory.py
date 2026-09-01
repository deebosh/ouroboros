"""CPL-4 verify pair: every factual data/-path writer has a row in docs/PERSISTENCE.md.

The scanner AST-walks every runtime module (``ouroboros/``, ``supervisor/``,
``server.py``, ``launcher.py``) and collects every data-relative path it
constructs: ``/``-join chains rooted at a data-root expression, chains whose
leading literal is a known top-level data entity, ``pathlib.Path("literal")``
chain bases, and ``drive_path("literal")`` calls. Non-literal segments
normalize to ``*``.

Contract (count-anchored both ways):

- every scanned path must be covered by a backticked path pattern in the
  first column of a PERSISTENCE.md inventory row;
- every inventory row must still name something the scan sees (no stale rows),
  except rows for planes written outside the scanned tree (external daemon);
- the total number of distinct scanned paths is pinned — adding a NEW
  data-relative path fails here until PERSISTENCE.md gets its row and the pin
  moves.
"""

from __future__ import annotations

import ast
import fnmatch
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "PERSISTENCE.md"

# --- scanner ---------------------------------------------------------------

DATA_ROOT_MARKERS = (
    "DATA_DIR", "data_dir", "data_root", "drive_root", "canonical_data_root",
    "state_drive_root", "budget_drive_root",
)
DATA_REL_CALL_NAMES = frozenset({"drive_path"})
TOP_LEVEL = frozenset({
    "state", "logs", "memory", "skills", "task_results", "observability",
    "locks", "archive", "settings.json", "uploads", "services", "artifacts",
    "task_drives", "tmp_scripts", "playwright-browsers", "cache", "tmp",
    "projects", "task_trees",
})

# Chains constructed relative to an intermediate sub-root the AST cannot
# resolve; each alias names the canonical data-relative location the doc rows
# describe. Keep this table SHORT — a growing alias list means writers are
# drifting away from recognizable root expressions.
SUBROOT_ALIASES = {
    "blobs/*": "observability/blobs/*",
    "calls/*": "observability/calls/*",
    "calls/*/*": "observability/calls/*/*",
    # claudexor managed-runtime archive cache (managed_runtime_root(...) base).
    "cache/*": "state/cx/cache/*",
}

# The pinned scan-population size. Moving it is deliberate: a new distinct
# data-relative path requires a PERSISTENCE.md row AND this bump in one diff.
# 118 -> 120: the CPL4-C13 sweep enumerates the delegate_recovery /
# delegate_recovery_transactions / delegate_supervision directories directly
# (their per-file chains were already pinned; the dir prefixes are new).
# 120 -> 122: the CPL4-C14/C15 prunes enumerate state/code_intel and
# state/extension_reconcile/failed the same way.
# 122 -> 124: the CPL4-C16 compaction names the three memory-journal paths
# directly (previously only their writers' per-module spellings were seen).
EXPECTED_SCAN_PATHS = 124

# Scanned paths that must always be present — guards the scanner itself
# against a silent regression that would shrink coverage while keeping counts
# plausible.
SENTINELS = frozenset({
    "settings.json",
    "state/state.json",
    "state/queue_snapshot.json",
    "state/process_ledger.jsonl",
    "state/consciousness_observations.jsonl",
    "state/skills/*/review_history.jsonl",
    "logs/events.jsonl",
    "logs/chat.jsonl",
    "memory/identity.md",
    "task_results/artifacts/*",
    "task_trees/*/blackboard.jsonl",
    "uploads",
})

# Inventory rows whose writers live outside the scanned python tree
# (external processes); the forward direction still documents them.
ROW_SCAN_EXEMPT_PRIMARY = frozenset({
    "claudexor/**",  # written by the external claudexord daemon
})


def _chain(node: ast.BinOp):
    parts, cur = [], node
    while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div):
        right = cur.right
        parts.append(
            right.value
            if isinstance(right, ast.Constant) and isinstance(right.value, str)
            else "*"
        )
        cur = cur.left
    # A pathlib.Path("literal") base contributes its own leading segment
    # (e.g. Path("state") / "headless_tasks").
    if isinstance(cur, ast.Call):
        fn = cur.func
        fn_name = fn.attr if isinstance(fn, ast.Attribute) else (
            fn.id if isinstance(fn, ast.Name) else "")
        if fn_name in {"Path", "PurePath", "PurePosixPath", "PureWindowsPath"} and cur.args:
            arg0 = cur.args[0]
            if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
                parts.append(arg0.value)
    parts.reverse()
    return cur, parts


def _root_is_data(node: ast.expr) -> bool:
    for sub in ast.walk(node):
        name = None
        if isinstance(sub, ast.Name):
            name = sub.id
        elif isinstance(sub, ast.Attribute):
            name = sub.attr
        if name and any(marker in name for marker in DATA_ROOT_MARKERS):
            return True
    return False


def _normalize(parts) -> str:
    out = []
    for part in parts:
        if part == "*":
            out.append("*")
            continue
        for seg in str(part).split("/"):
            seg = seg.strip()
            if seg:
                out.append(seg)
    return "/".join(out)


def scan_data_paths(root: pathlib.Path = REPO) -> set[str]:
    paths: set[str] = set()
    files = [
        p
        for p in list(root.glob("ouroboros/**/*.py"))
        + list(root.glob("supervisor/**/*.py"))
        + [root / "server.py", root / "launcher.py"]
        if "__pycache__" not in p.parts
    ]
    for path in sorted(files):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        consumed: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                if id(node) in consumed:
                    continue
                base, parts = _chain(node)
                cur = node
                while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div):
                    if isinstance(cur.left, ast.BinOp):
                        consumed.add(id(cur.left))
                    cur = cur.left
                rel = _normalize(parts)
                if not rel or not rel.replace("*", "").replace("/", ""):
                    continue
                if _root_is_data(base) or rel.split("/")[0] in TOP_LEVEL:
                    paths.add(SUBROOT_ALIASES.get(rel, rel))
            elif isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else (
                    fn.id if isinstance(fn, ast.Name) else "")
                if name in DATA_REL_CALL_NAMES and node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        rel = _normalize([arg.value])
                        if rel:
                            paths.add(SUBROOT_ALIASES.get(rel, rel))
    return paths


# --- doc parsing -----------------------------------------------------------

_BACKTICK_RE = re.compile(r"`([^`]+)`")
_PLACEHOLDER_RE = re.compile(r"<[^>/]+>")
_BRACES_RE = re.compile(r"\{([^{}]+)\}")


def _expand_braces(token: str) -> list[str]:
    match = _BRACES_RE.search(token)
    if not match:
        return [token]
    head, tail = token[: match.start()], token[match.end():]
    out: list[str] = []
    for option in match.group(1).split(","):
        out.extend(_expand_braces(head + option.strip() + tail))
    return out


def _pattern_tokens(cell: str) -> list[str]:
    tokens: list[str] = []
    for raw in _BACKTICK_RE.findall(cell):
        for piece in raw.split():
            piece = _PLACEHOLDER_RE.sub("*", piece).strip().rstrip("/")
            if not piece or piece in {"+", "("}:
                continue
            tokens.extend(_expand_braces(piece))
    return tokens


def doc_rows(text: str) -> list[list[str]]:
    """Every inventory-table row as its list of first-cell path patterns."""
    rows: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("| `"):
            continue
        first_cell = stripped.strip("|").split("|", 1)[0]
        tokens = _pattern_tokens(first_cell)
        if tokens:
            rows.append(tokens)
    return rows


# --- matching --------------------------------------------------------------


def _seg_match(scan_seg: str, pat_seg: str) -> bool:
    return scan_seg == "*" or pat_seg == "*" or fnmatch.fnmatchcase(scan_seg, pat_seg)


def _match(scan: list[str], pat: list[str]) -> bool:
    """Segment match with mutual prefix semantics and multi-segment ``**``."""
    if not pat:
        return True  # pattern is a prefix: a directory row covers children
    if pat[0] == "**":
        return any(_match(scan[i:], pat[1:]) for i in range(len(scan) + 1))
    if not scan:
        return True  # scan path is a prefix: named by a deeper row
    return _seg_match(scan[0], pat[0]) and _match(scan[1:], pat[1:])


def _covers(scan_path: str, pattern: str) -> bool:
    scan_segs = scan_path.split("/")
    pat_segs = pattern.split("/")
    if _match(scan_segs, pat_segs):
        return True
    # Bare filename tokens (contain a dot, single segment) also cover a path
    # by basename — the row names the file inside its family directory.
    if len(pat_segs) == 1 and "." in pat_segs[0]:
        return _seg_match(scan_segs[-1], pat_segs[0])
    return False


# --- tests -----------------------------------------------------------------


def test_scan_is_populated_and_pinned():
    paths = scan_data_paths()
    missing_sentinels = sorted(SENTINELS - paths)
    assert not missing_sentinels, (
        f"scanner regressed — sentinel paths vanished: {missing_sentinels}"
    )
    assert len(paths) == EXPECTED_SCAN_PATHS, (
        f"distinct data-relative paths changed: {len(paths)} != {EXPECTED_SCAN_PATHS}. "
        "A new/removed writer path must land together with its PERSISTENCE.md "
        f"row and this pin. Full set:\n" + "\n".join(sorted(paths))
    )


def test_every_scanned_path_has_an_inventory_row():
    text = DOC.read_text(encoding="utf-8")
    patterns = [token for row in doc_rows(text) for token in row]
    assert patterns, "PERSISTENCE.md inventory tables not found"
    uncovered = sorted(
        path for path in scan_data_paths()
        if not any(_covers(path, pattern) for pattern in patterns)
    )
    assert not uncovered, (
        "data/-relative paths written by the runtime but absent from "
        f"docs/PERSISTENCE.md: {uncovered}"
    )


def test_every_inventory_row_is_still_real():
    text = DOC.read_text(encoding="utf-8")
    paths = scan_data_paths()
    stale = []
    for row in doc_rows(text):
        if row[0] in ROW_SCAN_EXEMPT_PRIMARY:
            continue
        if not any(_covers(path, token) for token in row for path in paths):
            stale.append(row[0])
    assert not stale, (
        f"PERSISTENCE.md rows no scanned writer path matches (stale?): {stale}"
    )
