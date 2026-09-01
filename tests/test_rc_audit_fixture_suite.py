"""RC audit fixture suite (ABI-7b, F13/F14) — the verification hook of the
ADOPTION ABI-7 row for the RC auditor half.

Runs ``scripts/rc_audit.py`` over fixture installs built from the shared N−1
catalog ``tests/fixtures/nminus1/`` — REAL bytes authored by the previous
minor, not synthetic shapes (F14; shared property with the ABI-2 quarantine
suite and the ABI-7a updater shim suite, whose N−1 byte forms are inline):

- ``settings_v6.113.4.json`` — written by the v6.113.4 ``config.save_settings``
  itself in an isolated root (carries the retired comma-list keys and
  ``OUROBOROS_SCOPE_REVIEW_FLOOR`` exactly as a real N−1 install did; all
  secret fields empty).
- ``task_result_v6.113.4.json`` — written by the v6.113.4
  ``task_results.write_task_result`` (no ``_schema_version`` stamp; carries a
  real stored ``cost_usd`` alias key).
- ``telegram_SKILL_v6.113.4.md`` — the bundled telegram extension manifest at
  f0313064 (the commit before ABI-1 added ``plugin_api``): a real pre-7.0
  extension manifest without the field.

Pinned semantics: N−1 install → exit 1 with the expected check ids; clean
7.0 install → exit 0; broken/unreadable install → exit 2; strict read-only
guarantee (byte-for-byte fixture-tree snapshot before/after, no new files).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "rc_audit.py"
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "nminus1"


def _load_module():
    spec = importlib.util.spec_from_file_location("rc_audit_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(data_root: pathlib.Path, *extra: str, isolated_root: pathlib.Path):
    env = dict(os.environ)
    env.update({
        "OUROBOROS_APP_ROOT": str(isolated_root),
        "OUROBOROS_REPO_DIR": str(isolated_root / "repo"),
        "OUROBOROS_DATA_DIR": str(isolated_root / "data"),
        "OUROBOROS_SETTINGS_PATH": str(isolated_root / "data" / "settings.json"),
    })
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(data_root), *extra],
        capture_output=True, text=True, env=env, timeout=120,
    )


def _build_nminus1_install(root: pathlib.Path) -> pathlib.Path:
    data = root / "data"
    (data / "skills" / "external" / "telegram").mkdir(parents=True)
    (data / "task_results").mkdir()
    (data / "state").mkdir()
    shutil.copyfile(FIXTURES / "settings_v6.113.4.json", data / "settings.json")
    shutil.copyfile(FIXTURES / "task_result_v6.113.4.json",
                    data / "task_results" / "tsk_n1_fixture.json")
    shutil.copyfile(FIXTURES / "telegram_SKILL_v6.113.4.md",
                    data / "skills" / "external" / "telegram" / "SKILL.md")
    (data / "state" / "ui_preferences.json").write_text(
        json.dumps({"project_last_viewed": {"p1": 1}}), encoding="utf-8")
    return data


def _build_clean_70_install(root: pathlib.Path) -> pathlib.Path:
    data = root / "data"
    (data / "skills" / "external" / "telegram").mkdir(parents=True)
    (data / "task_results").mkdir()
    data.joinpath("settings.json").write_text(
        json.dumps({"TOTAL_BUDGET": "10", "OUROBOROS_REVIEWER_SLOTS": ""}, indent=2),
        encoding="utf-8")
    data.joinpath("task_results", "tsk_clean.json").write_text(
        json.dumps({
            "_schema_version": 1, "task_id": "tsk_clean", "status": "done",
            "summary": "clean 7.0 row",
        }), encoding="utf-8")
    # The CURRENT bundled manifest declares plugin_api and negotiates cleanly.
    shutil.copyfile(REPO / "skills" / "telegram" / "SKILL.md",
                    data / "skills" / "external" / "telegram" / "SKILL.md")
    return data


def _tree_snapshot(root: pathlib.Path):
    snapshot = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            snapshot[str(path.relative_to(root))] = hashlib.sha256(
                path.read_bytes()).hexdigest()
    return snapshot


# ------------------------------------------------------------- N−1 install


def test_nminus1_fixture_install_exits_1_with_the_expected_checks(tmp_path):
    data = _build_nminus1_install(tmp_path / "install")
    result = _run(data, "--json", str(tmp_path / "report.json"),
                  isolated_root=tmp_path / "isol")
    assert result.returncode == 1, result.stderr
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))

    incompatible = [f for f in report["findings"] if f["severity"] == "incompatible"]
    by_check = {}
    for f in incompatible:
        by_check.setdefault(f["check_id"], []).append(f["subject"])

    assert "settings.json:OUROBOROS_SCOPE_REVIEW_FLOOR" in by_check["retired-setting"]
    # The real N−1 settings document carried the comma-list keys as defaults.
    comma_subjects = set(by_check["comma-list"])
    for key in ("OUROBOROS_REVIEW_MODELS", "OUROBOROS_SCOPE_REVIEW_MODELS",
                "OUROBOROS_SCOPE_REVIEW_MODEL"):
        assert f"settings.json:{key}" in comma_subjects
    assert any("telegram" in s for s in by_check["plugin-api"])
    assert any("tsk_n1_fixture" in s for s in by_check["schema-stamp"])
    # ABI-2: the Q8=B consequence MUST be named, verbatim semantics.
    schema_findings = [f for f in incompatible if f["check_id"] == "schema-stamp"]
    assert any("Q8=B" in f["detail"] and "BY DESIGN" in f["detail"]
               for f in schema_findings)

    # Stored gateway-alias keys are notes (read-tolerance kept), never blocking.
    alias_findings = [f for f in report["findings"] if f["check_id"] == "gateway-alias"]
    assert alias_findings and all(f["severity"] == "note" for f in alias_findings)

    # F13: the owner-attestation list is printed, not silently absorbed.
    assert "OWNER ATTESTATION" in result.stdout
    assert len(report["owner_attestation"]) >= 5
    assert any("fail_tasks" in note for note in report["prose_notes"])


def test_grandfathered_hash_bound_pass_downgrades_plugin_api_to_a_note(tmp_path):
    data = _build_nminus1_install(tmp_path / "install")
    review_dir = data / "state" / "skills" / "telegram"
    review_dir.mkdir(parents=True)
    (review_dir / "review.json").write_text(
        json.dumps({"status": "pass", "content_hash": "a" * 64}), encoding="utf-8")
    result = _run(data, "--json", str(tmp_path / "report.json"),
                  isolated_root=tmp_path / "isol")
    assert result.returncode == 1  # other incompatibilities remain
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    plugin_findings = [f for f in report["findings"] if f["check_id"] == "plugin-api"]
    assert plugin_findings
    assert all(f["severity"] == "note" for f in plugin_findings)
    assert any("GRANDFATHERED" in f["detail"] for f in plugin_findings)


# ----------------------------------------------------------- clean install


def test_clean_70_install_exits_0(tmp_path):
    data = _build_clean_70_install(tmp_path / "install")
    result = _run(data, "--json", str(tmp_path / "report.json"),
                  isolated_root=tmp_path / "isol")
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    assert report["summary"]["incompatible"] == 0
    # The attestation list prints even on a clean install (F13).
    assert "OWNER ATTESTATION" in result.stdout


# ------------------------------------------------------- unreadable install


def test_broken_settings_document_exits_2(tmp_path):
    data = tmp_path / "install" / "data"
    data.mkdir(parents=True)
    (data / "settings.json").write_text("{not json", encoding="utf-8")
    result = _run(data, isolated_root=tmp_path / "isol")
    assert result.returncode == 2
    assert "INSTALL UNREADABLE" in result.stderr


def test_missing_data_root_exits_2(tmp_path):
    result = _run(tmp_path / "nope" / "data", isolated_root=tmp_path / "isol")
    assert result.returncode == 2


def test_data_root_without_settings_exits_2(tmp_path):
    data = tmp_path / "install" / "data"
    data.mkdir(parents=True)
    result = _run(data, isolated_root=tmp_path / "isol")
    assert result.returncode == 2


# --------------------------------------------------------------- read-only


def test_audit_is_byte_for_byte_read_only_over_the_install(tmp_path):
    data = _build_nminus1_install(tmp_path / "install")
    before = _tree_snapshot(data)
    result = _run(data, isolated_root=tmp_path / "isol")
    assert result.returncode == 1
    after = _tree_snapshot(data)
    assert before == after  # same files, same bytes — nothing created or touched


def test_report_file_is_refused_inside_the_audited_root(tmp_path):
    data = _build_nminus1_install(tmp_path / "install")
    result = _run(data, "--json", str(data / "rc_report.json"),
                  isolated_root=tmp_path / "isol")
    assert result.returncode == 2
    assert not (data / "rc_report.json").exists()


# ------------------------------------------------------------ scope schema


def test_scope_document_matches_the_design_note_schema():
    module = _load_module()
    scope = module.build_scope()
    assert scope["abi"] == "7.0"
    assert set(scope["sources"]) == {"tree", "inventories_frozen_at"}
    assert scope["sources"]["inventories_frozen_at"] == module.INVENTORIES_FROZEN_AT
    ids = {c["id"] for c in scope["checks"]}
    assert ids == {"gateway-alias", "retired-setting", "comma-list",
                   "plugin-api", "schema-stamp"}
    aliases = [c for c in scope["checks"] if c["id"] == "gateway-alias"]
    assert {c["removed"] for c in aliases} == {
        "cost_usd", "cost_usd_with_children", "telegram_chat_id",
        "project_last_viewed", "project_hidden",
    }  # the five frozen ABI-3 aliases (F11 inventory)
    schema_checks = [c for c in scope["checks"] if c["id"] == "schema-stamp"]
    assert len(schema_checks) == 1
    assert "Q8=B" in schema_checks[0]["consequence"]


def test_comma_list_class_is_snapped_from_settings_defaults_not_hardcoded():
    from ouroboros.settings_defaults import (
        RETIRED_COMMA_LIST_SETTING_KEYS,
        RETIRED_SETTING_KEYS,
    )

    assert set(RETIRED_COMMA_LIST_SETTING_KEYS) <= set(RETIRED_SETTING_KEYS)
    module = _load_module()
    scope = module.build_scope()
    comma_keys = {c["key"] for c in scope["checks"] if c["id"] == "comma-list"}
    assert comma_keys == set(RETIRED_COMMA_LIST_SETTING_KEYS)
    retired_keys = {c["key"] for c in scope["checks"] if c["id"] == "retired-setting"}
    assert retired_keys == set(RETIRED_SETTING_KEYS) - set(RETIRED_COMMA_LIST_SETTING_KEYS)


def test_scope_only_flag_prints_the_scope_and_exits_0(tmp_path):
    env = dict(os.environ)
    isolated = tmp_path / "isol"
    env.update({
        "OUROBOROS_APP_ROOT": str(isolated),
        "OUROBOROS_REPO_DIR": str(isolated / "repo"),
        "OUROBOROS_DATA_DIR": str(isolated / "data"),
        "OUROBOROS_SETTINGS_PATH": str(isolated / "data" / "settings.json"),
    })
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_path / "irrelevant"), "--scope-only"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert result.returncode == 0
    scope = json.loads(result.stdout)
    assert scope["abi"] == "7.0"


# ----------------------------------------------------- fixture provenance


def test_nminus1_fixtures_are_the_real_previous_minor_byte_forms():
    """The catalog documents (not synthetic shapes): the settings document
    carries the N−1 defaults for the retired keys, the task result is
    unstamped with the stored cost alias, the manifest is an extension
    without the plugin_api field — and none carries a secret value."""
    settings = json.loads((FIXTURES / "settings_v6.113.4.json").read_text("utf-8"))
    for key in ("OUROBOROS_SCOPE_REVIEW_FLOOR", "OUROBOROS_REVIEW_MODELS",
                "OUROBOROS_SCOPE_REVIEW_MODELS", "OUROBOROS_SCOPE_REVIEW_MODEL"):
        assert key in settings
    secretish = [k for k in settings
                 if k.endswith(("_API_KEY", "_TOKEN", "_CREDENTIALS", "_PASSWORD"))
                 or k == "GITHUB_TOKEN"]
    assert secretish and all(not settings[k] for k in secretish)

    row = json.loads((FIXTURES / "task_result_v6.113.4.json").read_text("utf-8"))
    assert "_schema_version" not in row
    assert "cost_usd" in row

    manifest_text = (FIXTURES / "telegram_SKILL_v6.113.4.md").read_text("utf-8")
    from ouroboros.contracts.skill_manifest import parse_skill_manifest_text

    manifest = parse_skill_manifest_text(manifest_text)
    assert manifest.type == "extension"
    assert manifest.plugin_api is None
