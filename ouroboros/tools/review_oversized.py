"""Oversized-function gate primitives, split from ``ouroboros.tools.review_helpers``
(module ceiling). The fork's staged-snapshot >MAX_FUNCTION_LINES check
(``ibl-oversized-function-gate-bypass``): an AST walk of one file body plus the
HEAD-baseline delta comparison the pre-commit preflight consults before pytest.

Re-exported from ``ouroboros.tools.review_helpers`` so existing
``review_helpers._find_oversized_functions`` / ``_check_staged_oversized_functions``
references and monkeypatching keep working unchanged. Imports from
``ouroboros.review`` stay LAZY (inside the bodies) to avoid the
``ouroboros.review`` <-> review-helpers-stack circular import.
"""
from __future__ import annotations

import ast
import pathlib
import subprocess
from typing import List, Optional, Tuple


def _find_oversized_functions(content: str, rel_path: str) -> List[Tuple[str, int]]:
    """Parse Python content; return [(func_name, line_count)] for non-grandfathered functions > MAX_FUNCTION_LINES.

    The smoke test (tests/test_smoke.py::test_no_extremely_oversized_functions) walks the
    HEAD working tree and only catches rot AFTER it lands. This helper walks ONE file
    body and is the AST primitive used by both the smoke test (for HEAD) and the
    pre-commit staged-snapshot check (closes ibl-oversized-function-gate-bypass: commit
    9224e188 grew ouroboros/tools/shell.py::_run_shell to 324 lines and the gate never
    blocked because the smoke test runs on HEAD, not on staged).

    ``rel_path`` is the repo-relative POSIX path of the file the ``content`` came
    from. Grandfather exemptions live in ouroboros.review.GRANDFATHERED_OVERSIZED_FUNCTIONS
    and key on (repo-relative path, function_name) tuples — the same SSOT the canonical
    inventory uses; pass the full path (not just a basename) so the match lines up.
    Scope filtering (devtools/, tests/, launcher.py) is the CALLER's job — see
    ouroboros.review.is_function_gated_path — this primitive stays a pure AST walk.

    Imports are LAZY (inside the body) because ouroboros.tools.review_helpers is a
    SSOT leaf for review_helpers-importing consumers (ouroboros.review imports back
    from here); a module-level ouroboros.review import would create a circular
    import at ouroboros.review load time.
    """
    # Lazy import to avoid ouroboros.review <-> ouroboros.tools.review_helpers cycle.
    from ouroboros.review import GRANDFATHERED_OVERSIZED_FUNCTIONS, MAX_FUNCTION_LINES
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []
    out: List[Tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            size = node.end_lineno - node.lineno + 1
            if size <= MAX_FUNCTION_LINES:
                continue
            if (rel_path, node.name) in GRANDFATHERED_OVERSIZED_FUNCTIONS:
                continue
            out.append((node.name, size))
    return out


def _head_oversized_functions(repo_dir: pathlib.Path, rel: str) -> Optional[dict]:
    """Oversized-function baseline for ``rel`` at HEAD: ``{func_name: [line_count, ...]}``.

    The value is a LIST because ``ast.walk`` yields every function/method, and one
    module can hold several oversized functions that share a bare name (two classes
    with a ``run`` method, or a top-level ``run`` plus a method ``run``). Keeping the
    sizes per name lets the caller compare positionally instead of letting one large
    HEAD function silently grandfather a different, newly-added one of the same name.

    Returns ``{}`` when the path does not exist at HEAD (a brand-new file has no
    baseline — every oversized function in it is new). Returns ``None`` on an
    unexpected git failure so the caller can fail open, matching
    :func:`_check_staged_oversized_functions`'s transient-error convention.
    """
    try:
        exists = subprocess.run(
            ["git", "cat-file", "-e", f"HEAD:{rel}"],
            cwd=str(repo_dir),
            capture_output=True,
            timeout=10,
        ).returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return None
    if not exists:
        return {}
    try:
        show = subprocess.run(
            ["git", "show", f"HEAD:{rel}"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if show.returncode != 0:
        return None
    baseline: dict = {}
    for name, size in _find_oversized_functions(show.stdout, rel):
        baseline.setdefault(name, []).append(size)
    return baseline


def _check_staged_oversized_functions(repo_dir: pathlib.Path) -> Optional[str]:
    """Walk STAGED .py files (post `git add`, pre `git commit`); return a typed error only
    when the staged content ADDS a function over MAX_FUNCTION_LINES, or GROWS one that was
    already over.

    Closes the class observed at commit 9224e188 (v6.93.2): _run_shell grew to 324 lines in
    one commit, but the smoke test (which walks HEAD) never saw the staged delta. By the
    time the smoke test ran in CI, the rot was already merged. The pre-commit preflight
    consults this helper BEFORE pytest so the gate fails fast with a precise message
    naming each oversized function instead of waiting for an expensive CI re-run.

    Delta-only against HEAD (see :func:`_head_oversized_functions`): a function that is
    already oversized at HEAD at the same or a larger size is NOT a staged regression, so
    it is not reported. Without this a large merge — which stages the whole file — flagged
    every pre-existing oversized function it happened to touch (e.g. the tracked local
    debt under ibl-local-56334da38429), and grandfathered manifest entries were flagged
    too because the lookup used a basename, not the SSOT ``(path, qualname)`` key.

    Scope matches the canonical function ratchet (ouroboros.review.is_function_gated_path):
    ``devtools/``, ``tests/`` and the one-shot entry-point files are OUT of the >300-line
    function gate. Without this filter a managed update — which stages hundreds of upstream
    files at once — falsely tripped this gate on upstream ``devtools/``/``tests/`` functions
    that are pre-existing and green under upstream's own (identical) ratchet.

    Fail-open on transient git errors: a missing staged list, a non-zero git exit, or an
    unreadable file is reported as None (no error) — the subsequent pytest pass + advisory
    gate + triad + scope review still cover the commit.
    """
    from ouroboros.review import MAX_FUNCTION_LINES, is_function_gated_path

    try:
        proc = subprocess.run(
            ["git", "diff", "--cached", "--name-only", "--diff-filter=AM"],
            cwd=str(repo_dir),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    py_paths = [
        p for p in (line.strip() for line in proc.stdout.splitlines())
        if p and p.endswith(".py") and is_function_gated_path(p)
    ]
    if not py_paths:
        return None
    violations: List[str] = []
    for rel in py_paths:
        full = repo_dir / rel
        try:
            content = full.read_text(encoding="utf-8")
        except OSError:
            continue
        staged: dict = {}
        for func_name, size in _find_oversized_functions(content, rel):
            staged.setdefault(func_name, []).append(size)
        if not staged:
            continue
        baseline = _head_oversized_functions(repo_dir, rel)
        if baseline is None:
            return None  # unexpected git failure — fail open
        for func_name, sizes in sorted(staged.items()):
            # Compare the same-named oversized functions positionally, largest
            # first: a staged entry regresses when it exceeds the HEAD entry at
            # its rank (0 once HEAD's same-named entries run out — i.e. a newly
            # added oversized function).
            staged_desc = sorted(sizes, reverse=True)
            base_desc = sorted(baseline.get(func_name, []), reverse=True)
            for rank, size in enumerate(staged_desc):
                base_size = base_desc[rank] if rank < len(base_desc) else 0
                if size > base_size:
                    violations.append(f"{rel}:{func_name} = {size} lines")
    if not violations:
        return None
    return (
        f"⚠️ OVERSIZED_FUNCTIONS_BLOCKED: staged snapshot adds or grows a function past "
        f"{MAX_FUNCTION_LINES} lines (closes ibl-oversized-function-gate-bypass):\n"
        + "\n".join(violations)
    )
