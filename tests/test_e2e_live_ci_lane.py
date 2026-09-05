"""The paid `e2e-live` CI job: the live E2E stand (devtools/e2e_live) on a nightly
cron or manual dispatch, sized to a $30 cap, skipped honestly without its secret.

Pinned as a contract, not as text: the job fires only on its OWN cron string or a
dispatch (never push, pull_request, tag, or the keyless lane's cron); it names
exactly one secret, `OUROBOROS_E2E_LIVE_OPENROUTER_KEY`, gated through a
non-secret job-level env (GitHub rejects `secrets.*` inside `if:`); a missing
secret is one step-summary line and a green exit, not a red run and not a
pretend run; the stand is invoked with the operator's flag set on a clean
detached seed of the checked-out sha; the run size is FEASIBLE under the cap by
the stand's own worst-case reservation rule computed from the code (a set that
can never be admitted would be a nightly red by construction); artifacts are
uploaded even on failure and never include a lane settings file (0600, carries
the key).
"""

from __future__ import annotations

import pathlib
import re
import shlex

import yaml

from devtools.e2e_live.run_live_lanes import HARD_STOP_INVERSE, LANE_BUDGET_FLOOR_USD
from devtools.e2e_live.scenarios import SCENARIOS

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CI_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
JOB = "e2e-live"
SECRET = "OUROBOROS_E2E_LIVE_OPENROUTER_KEY"
LIVE_CRON = "17 3 * * *"
SKIP_LINE = f"skipped: secret {SECRET} not configured"
TOTAL_BUDGET_USD = 30.0


def _workflow() -> dict:
    return yaml.safe_load(CI_PATH.read_text(encoding="utf-8"))


def _job() -> dict:
    return _workflow()["jobs"][JOB]


def _job_text() -> str:
    ci = CI_PATH.read_text(encoding="utf-8")
    block = re.search(rf"^  {re.escape(JOB)}:\n(.*?)(?=^  [A-Za-z0-9_-]+:$|\Z)", ci, re.MULTILINE | re.DOTALL)
    assert block, f"ci.yml has no `{JOB}:` job"
    return block.group(1)


def _stand_step() -> dict:
    return next(step for step in _job()["steps"] if "run_live_lanes" in str(step.get("run", "")))


def _stand_args() -> dict[str, str | None]:
    """The stand's argv as `{flag: value}` (a bare flag maps to None)."""
    argv = shlex.split(_stand_step()["run"].replace("\\\n", " "))
    assert argv[:3] == ["python", "-m", "devtools.e2e_live.run_live_lanes"], argv[:3]
    args: dict[str, str | None] = {}
    rest = argv[3:]
    while rest:
        flag = rest.pop(0)
        assert flag.startswith("--"), flag
        args[flag] = rest.pop(0) if rest and not rest[0].startswith("--") else None
    return args


def test_the_paid_lane_fires_only_on_its_own_cron_or_a_dispatch():
    workflow = _workflow()
    crons = [str(entry["cron"]) for entry in (workflow.get("on") or workflow.get(True))["schedule"]]
    assert LIVE_CRON in crons, crons
    condition = " ".join(str(_job()["if"]).split())
    assert condition == (
        "github.event_name == 'workflow_dispatch'"
        f" || (github.event_name == 'schedule' && github.event.schedule == '{LIVE_CRON}')"
    )
    assert _job()["runs-on"] == "ubuntu-latest"
    # One SM1 lane with --self-mod: the task, the evolution cycle, the absorb
    # wait and two hermetic preflight suites on a 4-vCPU runner.
    assert int(_job()["timeout-minutes"]) >= 120
    # No job downstream of the release chain may wait for a paid nightly lane.
    for name, job in workflow["jobs"].items():
        needs = job.get("needs") or []
        assert JOB not in ([needs] if isinstance(needs, str) else needs), name


def test_the_secret_is_gated_through_a_non_secret_env_and_named_once():
    job = _job()
    assert job["env"]["HAS_E2E_LIVE_KEY"] == f"${{{{ secrets.{SECRET} != '' && 'true' || 'false' }}}}"
    text = _job_text()
    assert re.findall(r"secrets\.([A-Z0-9_]+)", text) == [SECRET, SECRET], text
    for step in job["steps"]:
        assert "secrets." not in str(step.get("if", "")), step
    # The value reaches exactly the stand step, under the NAME the stand reads.
    stand = _stand_step()
    assert stand["env"] == {SECRET: f"${{{{ secrets.{SECRET} }}}}"}
    assert _stand_args()["--key-env"] == SECRET
    for step in job["steps"]:
        if "run_live_lanes" not in str(step.get("run", "")):
            assert SECRET not in str(step.get("env", {})), step


def test_a_missing_secret_is_one_summary_line_and_a_green_exit():
    steps = _job()["steps"]
    skip = next(step for step in steps if SKIP_LINE in str(step.get("run", "")))
    assert skip["if"] == "env.HAS_E2E_LIVE_KEY != 'true'"
    assert f'echo "{SKIP_LINE}" >> "$GITHUB_STEP_SUMMARY"' in skip["run"]
    assert "exit 1" not in skip["run"] and "false" not in skip["run"]
    assert not skip.get("continue-on-error")
    # Every other step (after checkout) is gated the other way: no dependency
    # install, no stand, no upload without the key.
    for step in steps:
        if step is skip or step.get("uses", "").startswith("actions/checkout"):
            continue
        assert "env.HAS_E2E_LIVE_KEY == 'true'" in str(step.get("if", "")), step


def test_the_stand_runs_with_the_operator_flag_set_on_a_clean_seed_of_this_sha():
    args = _stand_args()
    assert args["--source-repo"] == "$GITHUB_WORKSPACE"
    assert args["--seed"] == "$GITHUB_SHA"
    assert args["--out"].startswith("$RUNNER_TEMP/")
    assert args["--self-mod"] is None
    assert float(args["--total-budget"]) == TOTAL_BUDGET_USD
    assert int(args["--task-timeout"]) == 2400 and float(args["--watch-interval"]) == 60
    assert "--stub" not in args and "--profile" not in args and "--model" not in args
    # The seed's `git describe` and the release admission gate read history and tags.
    checkout = _job()["steps"][0]
    assert checkout["uses"].startswith("actions/checkout@")
    assert checkout["with"] == {"fetch-depth": 0, "persist-credentials": False}
    # The gate's node lane and the UI probe need node 22 and Chromium, as in ui-smoke.
    steps = _job()["steps"]
    assert any(step.get("uses", "").startswith("actions/setup-node@") for step in steps)
    assert any("playwright install --with-deps chromium" in str(step.get("run", "")) for step in steps)


def test_the_run_size_is_feasible_under_the_cap_by_the_worst_case_reservation_rule():
    """Every requested attempt must be admissible when each earlier attempt spent
    its whole reservation — the stand records an inadmissible one as `not_run`
    and fails the verdict, which would make the nightly red by construction."""
    args = _stand_args()
    per_task, attempts = float(args["--per-task-usd"]), int(args["--attempts"])
    scenarios = [s for s in str(args["--scenarios"]).split(",") if s]
    assert scenarios and set(scenarios) <= set(SCENARIOS), scenarios
    assert 1 <= int(args["--pass-of"]) <= attempts
    assert 1 <= int(args["--lanes"]) <= len(scenarios) * attempts
    reservations = [
        max(LANE_BUDGET_FLOOR_USD, HARD_STOP_INVERSE * per_task * SCENARIOS[sid].root_tasks)
        for sid in scenarios for _ in range(attempts)
    ]
    assert sum(reservations) <= TOTAL_BUDGET_USD, (reservations, per_task, HARD_STOP_INVERSE)
    # ...and the set is MAXIMAL at this fence: one more single-root attempt would
    # not fit. When the reservation factor changes this trips on purpose — the
    # subset (and the arithmetic comment in ci.yml) must be revisited, not left.
    assert sum(reservations) + HARD_STOP_INVERSE * per_task > TOTAL_BUDGET_USD, (reservations, HARD_STOP_INVERSE)
    # The job title says which subset runs; the full operator set is SM1,SW1,SK1 x3.
    assert "SM1" in _job()["name"] and "feasible" in _job()["name"]


def test_artifacts_upload_even_on_failure_and_never_a_lane_settings_file():
    steps = _job()["steps"]
    upload = next(step for step in steps if step.get("uses", "").startswith("actions/upload-artifact@"))
    assert upload["if"] == "always() && env.HAS_E2E_LIVE_KEY == 'true'"
    paths = [line.strip() for line in str(upload["with"]["path"]).splitlines() if line.strip()]
    root = "${{ runner.temp }}/e2e_live/"
    assert all(path.startswith(root) for path in paths), paths
    rel = [path[len(root):] for path in paths]
    assert "run_manifest.json" in rel and "lanes/*/result.json" in rel
    assert any(path.endswith(".png") for path in rel), rel
    for path in rel:
        assert "**" not in path and "settings" not in path and not path.endswith("/*"), path
    assert upload["with"]["if-no-files-found"] in ("warn", "ignore")
    summary = next(step for step in steps if "GITHUB_STEP_SUMMARY" in str(step.get("run", ""))
                   and SKIP_LINE not in str(step.get("run", "")))
    assert summary["if"] == "always() && env.HAS_E2E_LIVE_KEY == 'true'"
    assert "run_manifest.json" in summary["run"]
