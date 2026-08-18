"""Facade-identity contract for the v7 L-B loop.py leaf owners.

Every member the L-B split moved out of ``ouroboros/loop.py`` keeps a loop.py
re-export under its historical name; callers (and monkeypatching tests) must
never learn which leaf a name landed in. This pins the facade identity — the
loop binding IS the leaf's object — and the hot-code label parity for the
leaves, the same way the queue split pins both for its leaves.
"""

from __future__ import annotations

import importlib

# leaf module -> every member the leaf owns (loop.py re-exports each name).
LOOP_LEAF_OWNERS: dict[str, tuple[str, ...]] = {
    "loop_messages": (
        "_emit_checkpoint_event _extract_plain_text_from_content _append_or_merge_user_message "
        "_evict_stale_image_blocks _append_or_merge_user_content _owner_marked_content "
        "_record_owner_directive _initialize_owner_directives _last_assistant_text "
        "_visible_round_text _emit_round_progress"
    ),
}


def test_loop_owner_facades_preserve_identity():
    import ouroboros.loop as loop

    for leaf, names in LOOP_LEAF_OWNERS.items():
        module = importlib.import_module(f"ouroboros.{leaf}")
        for name in names.split():
            assert getattr(loop, name) is getattr(module, name), f"{leaf}.{name}"


def test_loop_leaves_keep_the_hot_code_label():
    """Managed-update conflict labelling names ``ouroboros/loop.py``; the split
    must not silently downgrade the label for code that merely moved."""
    from supervisor.update_merge_policy import HOT_CODE_PATHS

    assert "ouroboros/loop.py" in HOT_CODE_PATHS
    for leaf in LOOP_LEAF_OWNERS:
        assert f"ouroboros/{leaf}.py" in HOT_CODE_PATHS, leaf
