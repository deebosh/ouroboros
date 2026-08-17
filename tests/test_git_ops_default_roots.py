"""``supervisor.git_ops`` pre-``init`` roots follow the environment, never a hard-coded home path.

A process that imports git_ops without calling ``init`` (isolated tests, smokes) must
resolve its supervisor log and repo roots under the configured ``OUROBOROS_*`` roots —
otherwise a test-context write lands in the live ``~/Ouroboros/data`` drive.
"""

from __future__ import annotations

import pathlib

from ouroboros import config
from supervisor import git_ops, state


def test_git_ops_default_drive_root_follows_config_not_home() -> None:
    # Fixtures may re-point REPO_DIR at a scratch repository; the drive root is the
    # invariant that decides where supervisor rows land.
    assert git_ops.DRIVE_ROOT == pathlib.Path(config.DATA_DIR) == state.DRIVE_ROOT
    home_default = pathlib.Path.home() / "Ouroboros" / "data"
    if pathlib.Path(config.DATA_DIR).resolve() != home_default.resolve():
        assert git_ops.DRIVE_ROOT.resolve() != home_default.resolve()
        assert git_ops.REPO_DIR.resolve() != (pathlib.Path.home() / "Ouroboros" / "repo").resolve()
