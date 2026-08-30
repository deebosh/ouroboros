"""The module-handle extraction (spec §1.9 batch №8, delta D18) and its invariants.

`supervisor/queue.py` and `supervisor/workers.py` could not be split the way every
other v7 module was. Their bodies read module globals that ``init`` /
``init_queue_refs`` REBIND — PENDING, RUNNING, DRIVE_ROOT, WORKERS and the rest —
so a leaf holding `from supervisor.queue import PENDING` would freeze the object it
saw at import time, and a leaf keeping its own copy would be a second answer to the
same question (67 test sites rebind these names on the parent and must keep
working). The owner approved ONE mechanical exception: a declared parent name X is
read as ``_queue().X`` / ``_pool().X`` — a function-local import of the parent — so
the binding is resolved at call time.

The one-time proof that each moved body is otherwise unchanged (AST-equal modulo
exactly that substitution, over the declared set, with zero other differences) is
recorded in the extraction commits. What is pinned HERE is the property that has to
survive every later edit:

* the parent is reached only through a call-time handle, never a top-level import;
* every declared name is really bound by the parent (a typo would silently match
  nothing and make the proof vacuous);
* the declared set is exactly the set the leaf actually reads through the handle —
  neither a stale name nor an undeclared one;
* and, the load-bearing one, NO leaf reads a parent-owned name directly. That is
  the bug class the handle exists to prevent, and it is the one a later "tidy-up"
  would reintroduce by adding an innocent-looking from-import.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]

# leaf -> (parent, handle, declared substitution set)
# v7next transplant note: the reference table (ouroboros_v7_wip @ 9f691656)
# carries one row per extracted leaf across every domain, plus the
# queue/worker/loop/git_ops-specific invariant tests below the parametrized
# trio. On this integration branch each row lands with the lane that
# transplants its domain; only the D16 L-C2 usage split exists so far. The
# domain-specific standalone tests travel with their own rows.
LEAVES: dict[str, tuple[str, str, frozenset[str]]] = {
    "ouroboros/usage_legacy_import.py": ("ouroboros/usage_accounting.py", "_usage", frozenset({
        "_legacy_snapshot", "_locked", "_read_records_locked",
    })),
}


def _tree(rel: str) -> ast.Module:
    return ast.parse((REPO / rel).read_text(encoding="utf-8"))


def _module_bindings(tree: ast.Module) -> set[str]:
    bound: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
        elif isinstance(node, ast.Assign):
            bound.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            bound.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            bound.update(a.asname or a.name.split(".")[0] for a in node.names)
        elif (isinstance(node, ast.If) and isinstance(node.test, ast.Name)
                and node.test.id == "TYPE_CHECKING"):
            # Annotation-only bindings: lazy under future annotations, never
            # imported at runtime, so nothing is frozen at import time.
            for sub in node.body:
                if isinstance(sub, (ast.Import, ast.ImportFrom)):
                    bound.update(a.asname or a.name.split(".")[0] for a in sub.names)
    return bound


def _handle_reads(tree: ast.AST, handle: str) -> set[str]:
    reads: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name) and node.value.func.id == handle):
            reads.add(node.attr)
    return reads


@pytest.mark.parametrize("leaf", sorted(LEAVES))
def test_each_leaf_reaches_its_parent_only_through_a_call_time_handle(leaf: str) -> None:
    parent, handle, _declared = LEAVES[leaf]
    parent_module = parent[:-3].replace("/", ".")
    tree = _tree(leaf)
    for node in tree.body:  # module scope only: a lazy import inside the handle is the point
        if isinstance(node, ast.ImportFrom):
            assert node.module != parent_module, f"{leaf} imports its parent at module scope"
        if isinstance(node, ast.Import):
            assert all(a.name != parent_module for a in node.names), leaf
    handles = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == handle]
    assert len(handles) == 1, f"{leaf}: expected exactly one {handle}() definition"
    assert [n for n in ast.walk(handles[0]) if isinstance(n, (ast.Import, ast.ImportFrom))], (
        f"{leaf}: {handle}() must import the parent at call time"
    )


@pytest.mark.parametrize("leaf", sorted(LEAVES))
def test_the_declared_set_is_exactly_what_the_leaf_reads_through_the_handle(leaf: str) -> None:
    parent, handle, declared = LEAVES[leaf]
    actual = _handle_reads(_tree(leaf), handle)
    assert actual == set(declared), (
        f"{leaf}: declared {sorted(declared)} but reads {sorted(actual)}"
    )
    bound = _module_bindings(_tree(parent))
    missing = sorted(set(declared) - bound)
    assert missing == [], f"{leaf}: declared names absent from {parent}: {missing}"


@pytest.mark.parametrize("leaf", sorted(LEAVES))
def test_no_leaf_reads_a_parent_owned_name_directly(leaf: str) -> None:
    """The bug the handle exists to prevent: a direct read freezes the binding the
    leaf saw at import time, so `init` rebinding the parent's name — or a test doing
    the same — would leave this module looking at the old object forever."""
    parent, _handle, _declared = LEAVES[leaf]
    leaf_tree = _tree(leaf)
    parent_defs: set[str] = set()
    for node in _tree(parent).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            parent_defs.add(node.name)
        elif isinstance(node, ast.Assign):
            parent_defs.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            parent_defs.add(node.target.id)
    own = _module_bindings(leaf_tree)
    direct = {
        node.id for node in ast.walk(leaf_tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        and node.id in parent_defs and node.id not in own
    }
    assert direct == set(), f"{leaf} reads {sorted(direct)} directly instead of through the handle"
