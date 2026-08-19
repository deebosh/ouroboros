"""Facade-identity contract for the v7 DEL1 delegate-family leaf owners.

Every member the DEL1 split moved out of the delegate family —
``ouroboros/delegate_custody.py``, ``ouroboros/tools/delegate.py``,
``ouroboros/tools/delegate_integration.py`` and
``ouroboros/tools/subagent_integration.py`` — keeps a parent re-export under
its historical name for the DURATION of the v7 stream, so existing callers and
monkeypatching tests keep working unchanged while the split lands. This pins
the facade identity — the parent binding IS the leaf's object — and the
hot-code label parity for the leaves, the same way the queue and loop splits
pin both for theirs.
"""

from __future__ import annotations

import importlib

# parent module -> {leaf module -> every member the leaf owns (parent re-exports each name)}.
DELEGATE_LEAF_OWNERS: dict[str, dict[str, str]] = {
    "ouroboros.delegate_custody": {
        "ouroboros.delegate_custody_reconcile": (
            "open_runs pending_invocations release_task_runs reconcile_task_runs "
            "reconcile_orphaned_runs _reconcile_each _recover_pending_invocation "
            "_retire_recovered_registration _capture_stranded_patch _reconcile_one"
        ),
    },
}


def test_delegate_owner_facades_preserve_identity() -> None:
    for parent_name, leaves in DELEGATE_LEAF_OWNERS.items():
        parent = importlib.import_module(parent_name)
        for leaf_name, members in leaves.items():
            leaf = importlib.import_module(leaf_name)
            for member in members.split():
                assert getattr(parent, member) is getattr(leaf, member), (
                    f"{parent_name}.{member} is not the {leaf_name} object"
                )


def test_delegate_leaves_share_their_parents_hot_code_label_parity() -> None:
    """The delegate family is UNLABELED in the managed-update conflict policy at
    the DEL1 base, so its leaves inherit that — parity, not blanket labelling
    (the queue split pins the same property from the labeled side)."""
    from supervisor.update_merge_policy import HOT_CODE_PATHS

    parents = (
        "ouroboros/delegate_custody.py",
        "ouroboros/tools/delegate.py",
        "ouroboros/tools/delegate_integration.py",
        "ouroboros/tools/subagent_integration.py",
    )
    leaves = tuple(
        leaf.replace(".", "/") + ".py"
        for owners in DELEGATE_LEAF_OWNERS.values()
        for leaf in owners
    )
    for path in parents:
        assert path not in HOT_CODE_PATHS, f"{path} gained a hot-code label; relabel its leaves too"
    for path in leaves:
        assert path not in HOT_CODE_PATHS, f"{path} must keep parity with its unlabeled parent"
