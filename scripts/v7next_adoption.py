#!/usr/bin/env python3
"""Validate ADOPTION_v7next.md — the v7-side adoption manifest (Ф0 skeleton).

The manifest enumerates every v7-side delta that must be re-applied on top of
the v7next upstream base: the 17 approved semantic-delta families from the
frozen reference ledger (``ouroboros_v7_wip @ 9f691656`` —
``scripts/v7_migration.py::APPROVED_SEMANTIC_DELTAS`` minus ``"none"``) plus
the campaign-decision items of plan §6 (ABI package 7.0) and §7 (completeness)
and the plan §2 class returns.

Checks (plan §5.1, roast F2 — artifact/train-based manifest):

- the fixed 7-column table schema parses;
- ids are unique and well-formed;
- every required delta family D02–D38 is present as ``kind=semantic-delta``;
- ``kind`` / ``disposition`` / ``status`` / ``phase`` come from closed enums
  (dispositions per the plan §5.4 three-column rule: retain / re-prove /
  superseded-by-upstream, with ``pending-decision`` allowed only before
  release);
- every row carries a non-empty verification hook;
- ``--release``: no ``pending-decision`` dispositions and every row ``done``
  ("no unresolved rows at release", plan §10).

Exit 0 when green, 1 with findings, 2 when the manifest itself is missing or
structurally unparseable.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys
from collections import Counter

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "ADOPTION_v7next.md"

HEADER = ["id", "kind", "what", "disposition", "status", "phase", "verification hook"]

# APPROVED_SEMANTIC_DELTAS of the frozen reference, minus "none".
REQUIRED_DELTAS = (
    "D02", "D03", "D04", "D05", "D06", "D07", "D08", "D09", "D11",
    "D13", "D18", "D31", "D33", "D34", "D35", "D36", "D37", "D38",
)
KINDS = frozenset({"semantic-delta", "plan-item", "class-return"})
DISPOSITIONS = frozenset({"retain", "re-prove", "superseded-by-upstream", "pending-decision"})
STATUSES = frozenset({"pending", "in-progress", "done"})
PHASES = frozenset({"F0", "F1", "F2", "F3", "F4", "F5", "F6"})
ID_RE = re.compile(r"^(D\d\d|ABI-\d+|CPL-\d+|R-[A-Z0-9]+|TRAIN-[A-Za-z0-9._-]+)$")


def split_row(line: str) -> list[str]:
    """Split one markdown table row on unescaped pipes."""
    body = line.strip().strip("|")
    cells, cur, escaped = [], [], False
    for ch in body:
        if escaped:
            cur.append(ch)
            escaped = False
        elif ch == "\\":
            cur.append(ch)
            escaped = True
        elif ch == "|":
            cells.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    cells.append("".join(cur).strip())
    return cells


def parse_rows(text: str) -> tuple[list[dict[str, str]], list[str]]:
    errors: list[str] = []
    rows: list[dict[str, str]] = []
    lines = text.splitlines()
    header_at = None
    for i, line in enumerate(lines):
        if line.startswith("|") and [c.lower() for c in split_row(line)] == HEADER:
            header_at = i
            break
    if header_at is None:
        errors.append(f"table header not found; expected columns: {' | '.join(HEADER)}")
        return rows, errors
    for j in range(header_at + 2, len(lines)):
        line = lines[j]
        if not line.startswith("|"):
            break
        cells = split_row(line)
        if len(cells) != len(HEADER):
            errors.append(f"line {j + 1}: expected {len(HEADER)} cells, got {len(cells)}")
            continue
        rows.append(dict(zip(HEADER, cells)))
    return rows, errors


def validate(rows: list[dict[str, str]], release: bool) -> list[str]:
    errors: list[str] = []
    ids = [r["id"] for r in rows]
    for rid, n in Counter(ids).items():
        if n > 1:
            errors.append(f"duplicate id: {rid} ({n} rows)")
    for r in rows:
        rid = r["id"]
        if not ID_RE.match(rid):
            errors.append(f"{rid or '<empty>'}: malformed id")
        if r["kind"] not in KINDS:
            errors.append(f"{rid}: unknown kind {r['kind']!r}")
        if r["disposition"] not in DISPOSITIONS:
            errors.append(f"{rid}: unknown disposition {r['disposition']!r}")
        if r["status"] not in STATUSES:
            errors.append(f"{rid}: unknown status {r['status']!r}")
        if r["phase"] not in PHASES:
            errors.append(f"{rid}: unknown phase {r['phase']!r}")
        if not r["what"]:
            errors.append(f"{rid}: empty 'what'")
        if not r["verification hook"]:
            errors.append(f"{rid}: empty verification hook")
    by_id = {r["id"]: r for r in rows}
    for d in REQUIRED_DELTAS:
        row = by_id.get(d)
        if row is None:
            errors.append(f"required semantic delta {d} is missing")
        elif row["kind"] != "semantic-delta":
            errors.append(f"{d}: must be kind=semantic-delta, got {row['kind']!r}")
    if release:
        for r in rows:
            if r["disposition"] == "pending-decision":
                errors.append(f"release: {r['id']} still pending-decision")
            if r["status"] != "done":
                errors.append(f"release: {r['id']} status {r['status']!r} != done")
    return errors


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--release", action="store_true",
                    help="enforce the release bar: no pending-decision, all rows done")
    ap.add_argument("--manifest", type=pathlib.Path, default=MANIFEST)
    args = ap.parse_args()

    if not args.manifest.is_file():
        print(f"missing manifest: {args.manifest}", file=sys.stderr)
        return 2
    rows, errors = parse_rows(args.manifest.read_text(encoding="utf-8"))
    if not rows and errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 2
    errors += validate(rows, args.release)

    by_phase = Counter(r["phase"] for r in rows)
    by_disp = Counter(r["disposition"] for r in rows)
    by_status = Counter(r["status"] for r in rows)
    by_kind = Counter(r["kind"] for r in rows)
    print(f"{args.manifest.name}: {len(rows)} rows")
    print(f"  kind:        {dict(sorted(by_kind.items()))}")
    print(f"  phase:       {dict(sorted(by_phase.items()))}")
    print(f"  disposition: {dict(sorted(by_disp.items()))}")
    print(f"  status:      {dict(sorted(by_status.items()))}")
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print("OK" + (" (release bar)" if args.release else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
