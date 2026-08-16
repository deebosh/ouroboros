"""``bump_version`` — one atomic P9 version bump instead of a hand-run sequence
of separate edits.

Structural fix for a recurring commit-readiness debt pattern (``crd-0003``,
``advisory_stale``): a version bump historically meant several independent
``edit_text`` calls (VERSION, README badge/changelog, ARCHITECTURE.md header,
pyproject.toml, ...), each one an independent worktree mutation that can stale
an already-fresh ``advisory_review`` if it lands after that review ran. This
tool collapses the whole cascade into one mutation, and therefore into at most
one advisory-staleness transition per commit attempt instead of several.

The heavy lifting (VERSION -> every derived carrier -> changelog row, with the
P9 changelog cap enforced) lives in the pure, ToolContext-free
``ouroboros.tools.release_sync`` module so it stays reusable outside a tool
call (``claude_advisory_review.py`` already reuses ``sync_release_metadata``
directly). This module is only the thin ``{verb}_{noun}`` wrapper: resolve the
active repo, call the pure helper, report what changed.
"""

from __future__ import annotations

import logging
import pathlib
from typing import List

from ouroboros.tools.registry import ToolContext, ToolEntry, active_repo_dir_for
from ouroboros.tools.release_sync import bump_version_files

log = logging.getLogger(__name__)


def _bump_version(
    ctx: ToolContext,
    new_version: str,
    changelog_description: str,
    changelog_date: str = "",
) -> str:
    repo_dir = pathlib.Path(active_repo_dir_for(ctx))
    try:
        changed = bump_version_files(
            str(repo_dir),
            new_version,
            changelog_description,
            changelog_date=changelog_date,
        )
    except ValueError as exc:
        return f"⚠️ BUMP_VERSION_INVALID: {exc}"
    except Exception as exc:
        log.warning("bump_version failed for %s: %s", repo_dir, exc, exc_info=True)
        return f"⚠️ BUMP_VERSION_FAILED: {exc}"

    return (
        f"Version bumped to {new_version}. Synced carriers: {', '.join(changed)}.\n"
        "Files are on disk but NOT committed. Run advisory_review then "
        "commit_reviewed when ready."
    )


def get_tools() -> List[ToolEntry]:
    return [
        ToolEntry(
            "bump_version",
            {
                "name": "bump_version",
                "description": (
                    "Atomic P9 version bump: writes VERSION, cascades every derived "
                    "release carrier (pyproject.toml, uv.lock, web/package.json, "
                    "web/modules/api_types.js, README badge, docs/ARCHITECTURE.md "
                    "header) via the existing sync_release_metadata cascade, and "
                    "inserts the changelog row (enforcing the P9 cap of 2 major / "
                    "5 minor / 5 patch visible rows by rolling the oldest "
                    "same-category row off to git tags). One mutation instead of a "
                    "hand-run sequence of edit_text calls, so it stales an "
                    "already-fresh advisory_review at most once per version bump. "
                    "Call this once, as the LAST edit before advisory_review, "
                    "instead of separately edit_text-ing VERSION/pyproject.toml/"
                    "README/ARCHITECTURE.md."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "new_version": {
                            "type": "string",
                            "description": "New version, e.g. '6.101.0' or '6.101.0-rc.1'. Must be >= the current VERSION per BIBLE P9.",
                        },
                        "changelog_description": {
                            "type": "string",
                            "description": "One Version History table cell describing this release. Must not contain '|'.",
                        },
                        "changelog_date": {
                            "type": "string",
                            "description": "ISO date (YYYY-MM-DD) for the changelog row. Defaults to today (UTC) when omitted.",
                        },
                    },
                    "required": ["new_version", "changelog_description"],
                },
            },
            _bump_version,
            mutates_worktree=True,
        ),
    ]
