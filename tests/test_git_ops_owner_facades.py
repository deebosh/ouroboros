"""Facade-identity contract for the v7 G1 supervisor/git_ops.py leaf owners.

Every member the G1 split moved out of ``supervisor/git_ops.py`` keeps a git_ops
re-export under its historical name, so existing callers and monkeypatching
tests keep working unchanged — the git_ops binding IS the leaf's object, the
same way the queue, loop and update_merge splits pin their leaves. The
hot-code label parity clause pins the update_merge direction of the rule:
``supervisor/git_ops.py`` is not a HOT_CODE_PATHS member at the v7 base, so a
leaf that merely moved code out of it must not silently acquire the label
either — parent and leaves carry the SAME membership.
"""

from __future__ import annotations

import importlib

# leaf module -> every member the leaf owns (git_ops re-exports each name).
GIT_OPS_LEAF_OWNERS: dict[str, str] = {
    "git_ops_remotes": (
        "configure_remote configure_personal_remote _configure_credential_helper "
        "push_to_remote"
    ),
    "git_ops_updates": (
        "list_versions list_commits ensure_official_update_remote "
        "list_official_update_tags compute_managed_update_status prepare_managed_update"
    ),
    "git_ops_reset": (
        "_compute_ref_ahead_count _ref_points_at_ref preserve_local_ref_branch "
        "_preserve_branch_for_official_reset _run_git_resilient "
        "_admission_gate_for_unsynced_tree checkout_and_reset "
        "sync_runtime_dependencies import_test safe_restart"
    ),
}


def test_git_ops_owner_facade_preserves_identity():
    import supervisor.git_ops as git_ops

    for leaf, names in GIT_OPS_LEAF_OWNERS.items():
        module = importlib.import_module(f"supervisor.{leaf}")
        for name in names.split():
            assert getattr(git_ops, name) is getattr(module, name), f"{leaf}.{name}"


def test_git_ops_leaves_keep_hot_code_label_parity():
    """Managed-update conflict labelling does not name ``supervisor/git_ops.py``;
    the split must not silently upgrade or downgrade the label for code that
    merely moved — parent and leaves carry the SAME membership."""
    from supervisor.update_merge_policy import HOT_CODE_PATHS

    parent_is_hot = "supervisor/git_ops.py" in HOT_CODE_PATHS
    for leaf in GIT_OPS_LEAF_OWNERS:
        assert (f"supervisor/{leaf}.py" in HOT_CODE_PATHS) == parent_is_hot, leaf
