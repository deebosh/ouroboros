#!/usr/bin/env python3
"""ABI 7.0 RC auditor (ABI-7b, F13): pre-upgrade scan of a third-party install.

The migration-window instrument of owner decision Q6=A: point it at an
install's DATA ROOT (the directory holding ``settings.json``, ``skills/``,
``state/``, ``task_results/``) and it names every ABI-7.0 incompatibility with
its migration BEFORE the owner upgrades. It is strictly READ-ONLY over the
audited install — it never writes, moves, creates, or locks anything there
(the report file, when requested, is refused inside the audited root).

Scope (docs/v7next/DESIGN_RC_AUDIT_SCOPE.md) is the UNION of the frozen F3
lane inventories, emitted as one machine-readable JSON document
(``--scope``): abi "7.0", sources (tree SHA + the SHA the feeder inventories
were frozen at), and ``checks[]`` of exactly five classes:

- ``gateway-alias`` — the five removed gateway compat aliases
  (docs/v7next/ABI3_GATEWAY_ALIAS_INVENTORY.md, F11 axes). Stored rows stay
  read-tolerated BY DESIGN, so on-disk hits are notes; live clients are
  owner attestation.
- ``retired-setting`` — keys a release deleted (``RETIRED_SETTING_KEYS``,
  ABI-5/Q10 plus earlier retirements): stripped-on-load, value inert.
- ``comma-list`` — the ABI-10 reviewer comma-list / route keys
  (``RETIRED_COMMA_LIST_SETTING_KEYS``, snapped from settings_defaults at
  execution time): migration is "move the config to the structured
  OUROBOROS_REVIEWER_SLOTS BEFORE upgrade" or accept the shipped default
  panel.
- ``plugin-api`` — ABI-1 admission facts: absent manifest field ≡ LEGACY
  "1.3" (hash-bound grandfather keeps an existing PASS loading; a NEW PASS
  is refused via ``extension_new_pass_admission_error``).
- ``schema-stamp`` — ABI-2: durable task results require
  ``_schema_version: 1``; pre-7.0 history is QUARANTINED after upgrade
  (owner decision Q8=B, BY DESIGN — no converter exists; manual recovery
  only: re-stamp and move the file back).

Everything not machine-checkable is an OWNER ATTESTATION list the auditor
prints verbatim (F13: no pretend-coverage).

Exit codes: 0 = clean, 1 = incompatibilities found, 2 = install unreadable or
the audit itself failed (traversal/report-write OSError). A mandatory source
the audit cannot read or parse (a skill manifest, a hash-verifiable payload)
is NEVER a clean exit 0: it becomes a BLOCKING ``unauditable-source`` finding
(exit 1) — the install is not proven clean until the source is fixed.

Reuse-first: the classifiers are the runtime's own, consumed read-only —
``task_result_schema_refusal`` (pure), ``parse_skill_manifest_text``,
``extension_new_pass_admission_error``, ``skill_review_gate``,
``skill_loader._walk_skill_packages`` / ``compute_content_hash`` (the
runtime's own skill discovery and review-staleness hash),
``RETIRED_SETTING_KEYS`` / ``RETIRED_COMMA_LIST_SETTING_KEYS``. None of the
imported modules touches config paths at import time. Review-exempt dev/ops
tool: not part of the runtime gate.
"""
from __future__ import annotations

import sys

# READ-ONLY guarantee, before ANY further import: a packaged launcher may point
# PYTHONPYCACHEPREFIX inside the audited install, and the runtime imports below
# would then write .pyc trees into it before the audit even starts. The startup
# value is captured first: if the interpreter itself was allowed to write
# bytecode (no -B / PYTHONDONTWRITEBYTECODE), stdlib .pyc files landed under the
# prefix BEFORE this line — main() refuses such a prefix inside the audited root.
_STARTUP_DONT_WRITE_BYTECODE = bool(sys.dont_write_bytecode)
sys.dont_write_bytecode = True

import argparse  # noqa: E402
import json  # noqa: E402
import pathlib  # noqa: E402
import subprocess  # noqa: E402
from typing import Any, Dict, List, Optional  # noqa: E402

# This checkout must WIN the import resolution: an earlier checkout already on
# sys.path would otherwise supply the classifiers while sources.tree names ours.
REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(REPO_ROOT) in sys.path:
    sys.path.remove(str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT))

from ouroboros.contracts.plugin_api import (  # noqa: E402
    LEGACY_PLUGIN_API_GENERATION,
    PLUGIN_API_VERSION,
    extension_new_pass_admission_error,
    manifest_plugin_api_field,
)
from ouroboros.contracts.schema_versions import SCHEMA_VERSION_KEY  # noqa: E402
from ouroboros.contracts.skill_manifest import (  # noqa: E402
    SkillManifestError,
    canonical_skill_name,
    parse_skill_manifest_text,
)
from ouroboros.settings_defaults import (  # noqa: E402
    RETIRED_COMMA_LIST_SETTING_KEYS,
    RETIRED_SETTING_KEYS,
)
from ouroboros.skill_loader import (  # noqa: E402
    SkillPayloadUnreadable,
    _walk_skill_packages,
    compute_content_hash,
)
from ouroboros.skill_review_status import skill_review_gate  # noqa: E402
from ouroboros.task_result_schema import (  # noqa: E402
    TASK_RESULT_QUARANTINE_DIR,
    TASK_RESULT_SCHEMA_VERSION,
    task_result_schema_refusal,
)

ABI = "7.0"
# The base SHA at which every feeder inventory of this scope was frozen and
# landed (ABI-3 doc, ABI-5/ABI-10 RETIRED_SETTING_KEYS, ABI-1 admission facts,
# ABI-2 stamp semantics) — the F3.3 serial-tail base.
INVENTORIES_FROZEN_AT = "4fa2f01abc02e7f68ee3ce0e3c7931046fc92173"

SEV_INCOMPATIBLE = "incompatible"
SEV_NOTE = "note"

_MANIFEST_NAMES = ("SKILL.md", "skill.json")

# ABI-3 feeder: docs/v7next/ABI3_GATEWAY_ALIAS_INVENTORY.md (frozen, F11 axes).
_GATEWAY_ALIASES: List[Dict[str, str]] = [
    {
        "id": "gateway-alias",
        "surface": "ChatOutbound (WS chat frames, task-result projections)",
        "removed": "cost_usd",
        "replacement": "accounted_upper_bound_usd",
        "migration": "clients read the honest name; stored rows stay read-tolerated "
                     "(deprecated-wins) and normalize at projection/rewrite",
    },
    {
        "id": "gateway-alias",
        "surface": "ChatOutbound (WS chat frames, task-result projections)",
        "removed": "cost_usd_with_children",
        "replacement": "accounted_upper_bound_usd_with_children",
        "migration": "clients read the honest name; stored rows stay read-tolerated "
                     "(deprecated-wins) and normalize at projection/rewrite",
    },
    {
        "id": "gateway-alias",
        "surface": "Chat/Photo/Video/DocumentOutbound frames + history replay",
        "removed": "telegram_chat_id",
        "replacement": "transport (TransportMetadata)",
        "migration": "clients read transport; stored rows replay tolerated without "
                     "re-emitting the key",
    },
    {
        "id": "gateway-alias",
        "surface": "UiPreferencesResponse / POST /api/ui/preferences",
        "removed": "project_last_viewed",
        "replacement": "project_seen_revision",
        "migration": "clients stop sending the key (unknown-key 400 after removal); "
                     "stored legacy values are ignored on read, dropped on next write",
    },
    {
        "id": "gateway-alias",
        "surface": "UiPreferencesResponse / POST /api/ui/preferences",
        "removed": "project_hidden",
        "replacement": "project_seen_revision",
        "migration": "clients stop sending the key (unknown-key 400 after removal); "
                     "stored legacy values are ignored on read, dropped on next write",
    },
]

_UI_PREFERENCES_LEGACY_KEYS = ("project_last_viewed", "project_hidden")
_STORED_COST_ALIAS_KEYS = ("cost_usd", "cost_usd_with_children", "telegram_chat_id")

# ABI-5 (Q10) knobs removed WITHOUT an install-visible settings key: named in
# the scope prose and the schema-stamp/attestation planes, never as key checks.
_REMOVED_KNOBS_PROSE = (
    "fail_tasks: the budget-drain batch terminalizer is removed with no "
    "install-visible key; pausing before dispatch is the one live semantics "
    "for a budget-exhausted queued task.",
    "until_deadline / stall_rounds_threshold: the legacy pacing aliases are "
    "removed — an unknown improvement_policy normalizes to \"fixed\", the "
    "stall knob left the normalized profile shape, and a stored task result "
    "still carrying improvement_policy: \"until_deadline\" quarantines under "
    "the schema-stamp class (retired_contract_until_deadline).",
)

OWNER_ATTESTATION: List[str] = [
    "No custom gateway client SENDS project_last_viewed/project_hidden to "
    "POST /api/ui/preferences (the endpoint answers unknown-key 400 after the "
    "upgrade) and none REQUIRES cost_usd/cost_usd_with_children/"
    "telegram_chat_id in live outbound frames (the ABI emits the honest names "
    "and transport metadata only).",
    "No external automation treats the retired reviewer comma-list keys "
    "(RETIRED_COMMA_LIST_SETTING_KEYS) as a SETTINGS surface; the env "
    "spellings survive only as the derived runtime projection.",
    "Authors of out-of-tree extension skills will declare "
    f"plugin_api: \"{PLUGIN_API_VERSION}\" before requesting NEW review "
    "PASSes (existing hash-bound PASSes keep loading; editing a payload "
    "invalidates its PASS).",
    "No workflow depends on the removed fail_tasks budget-drain batch "
    "terminalizer or on the removed until_deadline/stall_rounds_threshold "
    "pacing knobs (external config templates must drop them).",
    "The owner accepts the Q8=B consequence: pre-7.0 task-result history is "
    "quarantined after the upgrade, BY DESIGN, with no converter — recovery "
    "is a manual re-stamp of each file moved back out of "
    "task_results/quarantine/.",
]


def _tree_sha() -> str:
    """Provenance of the classifier BYTES actually used, not just of HEAD:
    uncommitted tracked changes append the ``-dirty`` suffix (untracked files
    do not ship classifiers this run resolved — the sweep is tracked-scope)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        )
        sha = (out.stdout or "").strip()
        if out.returncode == 0 and sha:
            st = subprocess.run(
                ["git", "-C", str(REPO_ROOT), "status", "--porcelain",
                 "--untracked-files=no"],
                capture_output=True, text=True, timeout=10, check=False,
            )
            if st.returncode == 0 and (st.stdout or "").strip():
                return f"{sha}-dirty"
            return sha
    except (OSError, subprocess.SubprocessError):
        pass
    version = ""
    try:
        version = (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    except OSError:
        pass
    return f"unknown (VERSION {version or '?'})"


def build_scope() -> Dict[str, Any]:
    """The machine-readable scope document (design-note schema)."""
    # Fail closed on classification drift: the comma-list class must stay a
    # subset of the retirement SSOT it classifies.
    stray = sorted(set(RETIRED_COMMA_LIST_SETTING_KEYS) - set(RETIRED_SETTING_KEYS))
    if stray:
        raise RuntimeError(
            f"RETIRED_COMMA_LIST_SETTING_KEYS drifted out of RETIRED_SETTING_KEYS: {stray}"
        )
    checks: List[Dict[str, Any]] = list(_GATEWAY_ALIASES)
    comma = set(RETIRED_COMMA_LIST_SETTING_KEYS)
    for key in RETIRED_SETTING_KEYS:
        if key in comma:
            continue
        checks.append({
            "id": "retired-setting",
            "key": key,
            "since": "7.0" if key == "OUROBOROS_SCOPE_REVIEW_FLOOR" else "pre-7.0",
            "behavior": "stripped-on-load",
            "migration": "remove the key; the surface is retired with no replacement "
                         "knob — a stored value never reaches effective settings",
        })
    for key in RETIRED_COMMA_LIST_SETTING_KEYS:
        checks.append({
            "id": "comma-list",
            "key": key,
            "replacement": "reviewer slots (OUROBOROS_REVIEWER_SLOTS)",
            "migration": "move config to slots BEFORE upgrade; an install carrying "
                         "only comma keys gets the shipped default panel",
        })
    checks.append({
        "id": "plugin-api",
        "requirement": "manifest plugin_api field "
                       f"(host PluginAPI {PLUGIN_API_VERSION}; absent field ≡ LEGACY "
                       f"{LEGACY_PLUGIN_API_GENERATION!r} by construction)",
        "grandfather": "hash-bound PASS (an already-reviewed payload keeps loading; "
                       "a NEW PASS is refused at issuance)",
        "migration": f"declare plugin_api: \"{PLUGIN_API_VERSION}\" in the manifest "
                     "before the next review/edit",
    })
    checks.append({
        "id": "schema-stamp",
        "entity": "task_results",
        "requirement": f"{SCHEMA_VERSION_KEY}={TASK_RESULT_SCHEMA_VERSION} stamp on "
                       "every durable task-result row",
        "consequence": "pre-7.0 history quarantined (Q8=B, BY DESIGN — no converter; "
                       "manual recovery: re-stamp and move back)",
    })
    return {
        "abi": ABI,
        "sources": {"tree": _tree_sha(), "inventories_frozen_at": INVENTORIES_FROZEN_AT},
        "checks": checks,
    }


class InstallUnreadable(Exception):
    pass


def _finding(check_id: str, severity: str, subject: str, detail: str, migration: str) -> Dict[str, str]:
    return {
        "check_id": check_id,
        "severity": severity,
        "subject": subject,
        "detail": detail,
        "migration": migration,
    }


def _read_json(path: pathlib.Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _audit_settings(data_root: pathlib.Path, findings: List[Dict[str, str]]) -> None:
    settings_path = data_root / "settings.json"
    if not settings_path.is_file():
        raise InstallUnreadable(f"no settings.json under {data_root} — not a readable install")
    try:
        settings = _read_json(settings_path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallUnreadable(f"settings.json unreadable: {type(exc).__name__}: {exc}") from exc
    if not isinstance(settings, dict):
        raise InstallUnreadable("settings.json is not a JSON object")
    comma = set(RETIRED_COMMA_LIST_SETTING_KEYS)
    for key in RETIRED_SETTING_KEYS:
        if key not in settings:
            continue
        if key in comma:
            findings.append(_finding(
                "comma-list", SEV_INCOMPATIBLE, f"settings.json:{key}",
                "retired reviewer comma-list/route key present; stripped on load "
                "after upgrade — the value never reaches effective settings",
                "move the reviewer configuration to the structured "
                "OUROBOROS_REVIEWER_SLOTS BEFORE upgrading; otherwise the install "
                "gets the shipped default panel",
            ))
        else:
            findings.append(_finding(
                "retired-setting", SEV_INCOMPATIBLE, f"settings.json:{key}",
                "retired settings key present; stripped on load after upgrade "
                "(the stored value becomes inert)",
                "remove the key; the surface is retired with no replacement knob",
            ))


def _iter_skill_dirs(data_root: pathlib.Path):
    """The RUNTIME's own discovery walk, consumed read-only (no parallel rules):
    hidden and ``.replaced-``/``.staging-``/``.tmp-`` orphan names are excluded
    and descent stops at a found package, exactly as ``skill_loader`` loads —
    a crash leftover the runtime never loads must not become an audit finding."""
    yield from _walk_skill_packages(data_root / "skills")


def _review_gate_for(data_root: pathlib.Path, name: str) -> Dict[str, Any]:
    safe = canonical_skill_name(name)
    review_path = data_root / "state" / "skills" / safe / "review.json"
    try:
        data = _read_json(review_path) if review_path.is_file() else {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    status = str(data.get("status") or "")
    gate = skill_review_gate(status)
    gate["content_hash"] = str(data.get("content_hash") or "")
    return gate


def _audit_skills(data_root: pathlib.Path, findings: List[Dict[str, str]]) -> None:
    for skill_dir in _iter_skill_dirs(data_root):
        manifest_text = None
        for name in _MANIFEST_NAMES:
            mf = skill_dir / name
            if mf.is_file():
                try:
                    manifest_text = mf.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError) as exc:
                    # A mandatory source the audit cannot read is a BLOCKING
                    # audit-integrity finding (exit 1), never a clean exit 0.
                    findings.append(_finding(
                        "unauditable-source", SEV_INCOMPATIBLE,
                        str(mf.relative_to(data_root)),
                        f"manifest unreadable ({type(exc).__name__}) — this skill "
                        "cannot be audited, so the install is NOT proven clean",
                        "fix the manifest file, then re-run the audit",
                    ))
                break
        if manifest_text is None:
            continue
        rel = str(skill_dir.relative_to(data_root))
        try:
            manifest = parse_skill_manifest_text(manifest_text)
        except SkillManifestError as exc:
            findings.append(_finding(
                "unauditable-source", SEV_INCOMPATIBLE, rel,
                f"manifest does not parse ({exc}) — this skill cannot be "
                "audited, so the install is NOT proven clean",
                "fix the manifest, then re-run the audit",
            ))
            continue
        if str(getattr(manifest, "type", "") or "") != "extension":
            continue
        admission_error = extension_new_pass_admission_error(manifest)
        if not admission_error:
            continue
        # Runtime/state identity is the DIRECTORY BASENAME (skill_loader.load_skill);
        # manifest.name is display metadata and may point at another state dir.
        gate = _review_gate_for(data_root, skill_dir.name)
        declared = manifest_plugin_api_field(manifest) is not None
        stored_hash = str(gate.get("content_hash") or "")
        grandfathered = False
        if not declared and gate.get("executable_review") and stored_hash:
            # A PASS is HASH-BOUND: only a PASS over the payload's CURRENT bytes
            # keeps loading. The hash is the runtime's own (read-only reuse).
            try:
                current_hash = compute_content_hash(
                    skill_dir,
                    manifest_entry=manifest.entry,
                    manifest_scripts=manifest.scripts,
                )
            except SkillPayloadUnreadable as exc:
                findings.append(_finding(
                    "unauditable-source", SEV_INCOMPATIBLE, rel,
                    f"skill payload unreadable ({exc}) — the hash-bound review "
                    "PASS cannot be verified against the current bytes",
                    "fix the payload files, then re-run the audit",
                ))
                continue
            if stored_hash == current_hash:
                grandfathered = True
            else:
                findings.append(_finding(
                    "plugin-api", SEV_INCOMPATIBLE, rel,
                    "extension manifest declares no plugin_api field and its "
                    "stored review PASS is STALE (payload bytes changed since "
                    "the PASS): the runtime will not load it and a NEW PASS "
                    "will be refused",
                    admission_error,
                ))
                continue
        if grandfathered:
            findings.append(_finding(
                "plugin-api", SEV_NOTE, rel,
                "extension manifest declares no plugin_api field but holds a "
                "hash-bound review PASS over its CURRENT bytes: it keeps loading "
                "GRANDFATHERED on that PASS; any edit invalidates it and a NEW "
                "PASS will be refused",
                admission_error,
            ))
        else:
            findings.append(_finding(
                "plugin-api", SEV_INCOMPATIBLE, rel,
                "extension is not admissible for a NEW review PASS after upgrade "
                + ("(declared plugin_api fails negotiation)" if declared
                   else "(no plugin_api field and no executable hash-bound PASS)"),
                admission_error,
            ))


def _audit_task_results(data_root: pathlib.Path, findings: List[Dict[str, str]]) -> None:
    results_dir = data_root / "task_results"
    if not results_dir.is_dir():
        return
    reasons: Dict[str, List[str]] = {}
    stored_alias_examples: List[str] = []
    for path in sorted(results_dir.glob("*.json")):
        if path.parent.name == TASK_RESULT_QUARANTINE_DIR:
            continue
        try:
            data = _read_json(path)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            data = None  # classifier maps a non-dict to "malformed"
        refusal = task_result_schema_refusal(data)
        if refusal:
            reasons.setdefault(refusal, []).append(path.name)
        if isinstance(data, dict) and any(k in data for k in _STORED_COST_ALIAS_KEYS):
            stored_alias_examples.append(path.name)
    for reason, names in sorted(reasons.items()):
        findings.append(_finding(
            "schema-stamp", SEV_INCOMPATIBLE,
            f"task_results ({len(names)} file(s), e.g. {', '.join(names[:3])})",
            f"rows classified {reason!r} will be QUARANTINED after upgrade — "
            "pre-7.0 history quarantined (Q8=B, BY DESIGN; no converter)",
            "none by design; manual recovery only (re-stamp and move the file "
            "back out of task_results/quarantine/)",
        ))
    if stored_alias_examples:
        findings.append(_finding(
            "gateway-alias", SEV_NOTE,
            f"task_results ({len(stored_alias_examples)} file(s), e.g. "
            f"{', '.join(stored_alias_examples[:3])})",
            "stored rows carry removed gateway alias keys "
            f"({'/'.join(_STORED_COST_ALIAS_KEYS)}); read-tolerance is KEPT — "
            "they resolve deprecated-wins and normalize at projection/rewrite",
            "no action required for stored data",
        ))


def _audit_ui_preferences(data_root: pathlib.Path, findings: List[Dict[str, str]]) -> None:
    path = data_root / "state" / "ui_preferences.json"
    if not path.is_file():
        return
    try:
        data = _read_json(path)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    legacy = [k for k in _UI_PREFERENCES_LEGACY_KEYS if k in data]
    if legacy:
        findings.append(_finding(
            "gateway-alias", SEV_NOTE, "state/ui_preferences.json",
            f"stored legacy keys present ({', '.join(legacy)}); tolerated — "
            "ignored on read and dropped on the next write",
            "no action required for stored data; clients must write "
            "project_seen_revision instead",
        ))


def audit(data_root: pathlib.Path) -> Dict[str, Any]:
    if not data_root.is_dir():
        raise InstallUnreadable(f"data root {data_root} is not a directory")
    findings: List[Dict[str, str]] = []
    _audit_settings(data_root, findings)
    _audit_skills(data_root, findings)
    _audit_task_results(data_root, findings)
    _audit_ui_preferences(data_root, findings)
    incompatible = sum(1 for f in findings if f["severity"] == SEV_INCOMPATIBLE)
    notes = len(findings) - incompatible
    return {
        "rc_audit_report": 1,
        "abi": ABI,
        "audited_root": str(data_root),
        "scope": build_scope(),
        "findings": findings,
        "prose_notes": list(_REMOVED_KNOBS_PROSE),
        "owner_attestation": list(OWNER_ATTESTATION),
        "summary": {"incompatible": incompatible, "notes": notes},
        "exit_code": 1 if incompatible else 0,
    }


def render(report: Dict[str, Any]) -> str:
    lines: List[str] = []
    src = report["scope"]["sources"]
    lines.append(f"RC audit — ABI {report['abi']} (tree {src['tree'][:12]}, "
                 f"inventories frozen at {src['inventories_frozen_at'][:12]})")
    lines.append(f"Audited install: {report['audited_root']}")
    lines.append("")
    findings = report["findings"]
    if not findings:
        lines.append("No incompatibilities found: the install is clean for ABI 7.0.")
    for f in findings:
        lines.append(f"[{f['severity'].upper()}] {f['check_id']} — {f['subject']}")
        lines.append(f"    {f['detail']}")
        lines.append(f"    migration: {f['migration']}")
    lines.append("")
    lines.append("Removed without an install-visible key (report prose):")
    for note in report["prose_notes"]:
        lines.append(f"  - {note}")
    lines.append("")
    lines.append("OWNER ATTESTATION (not machine-checkable — confirm each item "
                 "before upgrading):")
    for i, item in enumerate(report["owner_attestation"], 1):
        lines.append(f"  {i}. {item}")
    s = report["summary"]
    lines.append("")
    lines.append(f"Summary: {s['incompatible']} incompatible, {s['notes']} note(s).")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("data_root", type=pathlib.Path,
                        help="the audited install's data root (holds settings.json)")
    parser.add_argument("--json", type=pathlib.Path, default=None,
                        help="also write the typed JSON report to this path "
                             "(refused inside the audited root)")
    parser.add_argument("--scope-only", action="store_true",
                        help="print the machine-readable scope document and exit 0")
    args = parser.parse_args(argv)

    if args.scope_only:
        print(json.dumps(build_scope(), ensure_ascii=False, indent=2))
        return 0

    data_root = args.data_root.resolve()
    # The dont_write_bytecode flag above stops every post-startup write, but the
    # INTERPRETER already compiled stdlib bytecode under PYTHONPYCACHEPREFIX
    # before this script's first line ran. A prefix inside the audited install
    # means the read-only guarantee was violated by the invoking environment —
    # refuse loudly instead of printing a false "read-only" report over it.
    prefix = getattr(sys, "pycache_prefix", None)
    if prefix and not _STARTUP_DONT_WRITE_BYTECODE:
        prefix_path = pathlib.Path(prefix).resolve()
        if prefix_path == data_root or data_root in prefix_path.parents:
            print("READ-ONLY GUARANTEE VIOLATED: PYTHONPYCACHEPREFIX points inside "
                  "the audited install; interpreter startup already wrote bytecode "
                  "there. Re-run with PYTHONDONTWRITEBYTECODE=1 (or python -B) or "
                  "an outside prefix.", file=sys.stderr)
            return 2
    try:
        report = audit(data_root)
    except InstallUnreadable as exc:
        print(f"INSTALL UNREADABLE: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        # Traversal failure is an AUDIT failure (exit 2), never Python's exit 1
        # — that code means "incompatibilities found" to automation.
        print(f"INSTALL UNREADABLE: audit traversal failed: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.json is not None:
        out = args.json.resolve()
        if out == data_root or data_root in out.parents:
            print("refusing to write the report inside the audited install "
                  "(read-only guarantee)", file=sys.stderr)
            return 2
        try:
            out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
        except OSError as exc:
            print(f"REPORT UNWRITABLE: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            return 2
    print(render(report))
    return int(report["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
