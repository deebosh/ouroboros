"""Facade-identity contract for the v7 L-C review-stack leaf owners.

Every member the L-C split moved out of ``ouroboros/tools/review.py``,
``ouroboros/review_execution.py`` and ``ouroboros/tools/claude_advisory_review.py``
keeps a parent re-export under its historical name for the DURATION of the v7
stream, so existing callers and monkeypatching tests keep working unchanged
while the split lands. This pins the facade identity — the parent binding IS
the leaf's object — and the merge-label parity for the leaves, the same way
the loop and queue splits pin both for theirs. The private half of the facade
is temporary (spec 1.9-15): the L3 package re-homes the private test imports
to the leaf owners and retires those re-exports, and this test shrinks with
them.
"""

from __future__ import annotations

import importlib

# parent module -> {leaf module -> every member the leaf owns
# (the parent re-exports each name)}.
REVIEW_LEAF_OWNERS: dict[str, dict[str, str]] = {
    "ouroboros.tools.review": {
        "ouroboros.tools.review_multi_model": (
            "MAX_MODELS CONCURRENCY_LIMIT DEFAULT_REVIEW_MODEL_TIMEOUT_SEC _CONSTITUTIONAL_PREAMBLE "
            "_review_model_timeout_sec _handle_multi_model_review _review_output_budget _query_model "
            "_multi_model_review_async _parse_model_response"
        ),
    },
}


def test_review_owner_facades_preserve_identity():
    for parent_name, leaves in REVIEW_LEAF_OWNERS.items():
        parent = importlib.import_module(parent_name)
        for leaf_name, names in leaves.items():
            leaf = importlib.import_module(leaf_name)
            for name in names.split():
                assert getattr(parent, name) is getattr(leaf, name), f"{leaf_name}.{name}"


def test_review_leaves_inherit_the_unlabeled_merge_class():
    """Managed-update conflict labelling names neither review parent, so the
    leaves inherit that — parity, not blanket labelling (the queue split pins
    the same rule in the labeled direction)."""
    from supervisor.update_merge_policy import HOT_CODE_PATHS

    for parent_name, leaves in REVIEW_LEAF_OWNERS.items():
        assert parent_name.replace(".", "/") + ".py" not in HOT_CODE_PATHS, parent_name
        for leaf_name in leaves:
            assert leaf_name.replace(".", "/") + ".py" not in HOT_CODE_PATHS, leaf_name
