#!/usr/bin/env python3
"""Validate ADOPTION_v7next.md — the v7-side adoption manifest (Ф0 skeleton).

The manifest enumerates every v7-side delta that must be re-applied on top of
the v7next upstream base: the 18 approved semantic-delta families from the
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
# Required non-delta inventory (F0 phase review F2): the ABI package and the
# compatibility retirements are release-gated too, not only the D-families.
REQUIRED_ABI = tuple(f"ABI-{n}" for n in range(1, 11))
REQUIRED_CPL = tuple(f"CPL-{n}" for n in range(1, 8))
# F0 review round 2: the phase of every required row is itself part of the
# owner-approved inventory — a required row silently rescheduled to another
# phase (or parked post-release without an owner decision) must turn the
# validator red. OWNER_DEFERRED lists the only ids the owner has explicitly
# deferred out of v7.0 (ABI-8: Q5=A kept handler-ABI out of the bundle,
# Q16=A retired the «7.1» label into post-release backlog).
REQUIRED_PHASE = {
    # D02 F1->F3: owner-ratified F3 layout (2026-08-31) — the typed organ is
    # re-derived whole by the F3.1 lane A; seam commit updates row + pin together.
    # D03 F1->F6 (ADOPTION truth wave, 2026-09-01): F1 closed with the settings
    # seam's rows 913-917/1080-1081 still hot-deferred, so the pin named a dead
    # phase. F6 is the live phase. This is an OPERATOR scheduling correction, not
    # an owner decision — disclosed in the manifest row and the ledger so the
    # owner can overturn it. Sibling rows D04/D05/D06/D35 landed through their
    # owner-decided lanes and read done; their F1 pins were deliberately left
    # alone (one decision per class, and nobody has decided this one).
    "D02": "F3", "D03": "F6", "D04": "F1", "D05": "F1", "D06": "F1",
    "D07": "F2", "D08": "F2", "D09": "F1", "D11": "F1", "D13": "F1",
    "D18": "F1", "D31": "F2", "D33": "F1", "D34": "F2", "D35": "F1",
    "D36": "F2", "D37": "F2", "D38": "F1",
    "ABI-1": "F3", "ABI-2": "F3", "ABI-3": "F3", "ABI-4": "F3",
    "ABI-5": "F3", "ABI-6": "F3", "ABI-7": "F3", "ABI-8": "POST",
    "ABI-9": "F3", "ABI-10": "F3",
    "CPL-1": "F5", "CPL-2": "F5", "CPL-3": "F5", "CPL-4": "F5",
    "CPL-5": "F5", "CPL-6": "F5", "CPL-7": "F5",
}
OWNER_DEFERRED = frozenset({"ABI-8"})
KINDS = frozenset({"semantic-delta", "plan-item", "class-return"})
DISPOSITIONS = frozenset({"retain", "re-prove", "superseded-by-upstream",
                          "pending-decision", "post-release"})
STATUSES = frozenset({"pending", "in-progress", "done", "deferred"})
PHASES = frozenset({"F0", "F1", "F2", "F3", "F4", "F5", "F6", "POST"})
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
    # F0 phase review F2: the ABI package and compatibility retirements are part
    # of the release inventory too — deleting their rows must turn --release red.
    for rid in (*REQUIRED_ABI, *REQUIRED_CPL):
        row = by_id.get(rid)
        if row is None:
            errors.append(f"required row {rid} is missing")
        elif row["kind"] != "plan-item":
            errors.append(f"{rid}: must be kind=plan-item, got {row['kind']!r}")
    # Row-specific coupling: post-release is a single coherent state, not three
    # independent knobs (prevents e.g. disposition=post-release with status=done
    # quietly counting as shipped) — and it is an OWNER decision: only ids in
    # OWNER_DEFERRED may carry it, so flipping a required row to post-release
    # cannot silently bypass the release bar.
    for r in rows:
        post_bits = [r["disposition"] == "post-release", r["status"] == "deferred",
                     r["phase"] == "POST"]
        if any(post_bits) and not all(post_bits):
            errors.append(
                f"{r['id']}: post-release rows need disposition=post-release + "
                f"status=deferred + phase=POST together, got "
                f"{r['disposition']}/{r['status']}/{r['phase']}")
        if all(post_bits) and r["id"] not in OWNER_DEFERRED:
            errors.append(
                f"{r['id']}: post-release requires an owner decision; only "
                f"{sorted(OWNER_DEFERRED)} are owner-deferred")
    # Phase pinning of the required inventory.
    for rid, want in REQUIRED_PHASE.items():
        row = by_id.get(rid)
        if row is not None and row["phase"] != want:
            errors.append(f"{rid}: phase {row['phase']!r} != pinned {want!r} "
                          "(rescheduling a required row needs a new owner decision "
                          "and an update to REQUIRED_PHASE)")
    if release:
        for r in rows:
            if r["disposition"] == "pending-decision":
                errors.append(f"release: {r['id']} still pending-decision")
            if r["disposition"] == "post-release":
                continue  # explicitly deferred out of v7.0 by an owner decision
            if r["status"] != "done":
                errors.append(f"release: {r['id']} status {r['status']!r} != done")
            errors.extend(_hook_resolution_errors(r))
    return errors


# Any-extension token, anchored on BOTH sides: the lookbehind stops
# `not-scripts/x.py` being misread as a scripts/ reference (round 4), the
# lookahead stops `scripts/x.py-not-real` matching by its existing `.py`
# prefix (round 5) — a partial token is prose, not a reference.
_HOOK_PATH_RE = re.compile(r"(?<![\w./-])(?:tests|scripts|docs)/[\w./-]+\.\w+(?![\w-])")


def _hook_resolution_errors(row: dict[str, str]) -> list[str]:
    """Release-bar hook contract (F0 review rounds 1-4): a shipped row's
    verification hook must RESOLVE — prose alone cannot pass. At least one
    repo-path reference must be present, EVERY referenced token must exist
    (any extension — a smuggled bogus reference next to a valid one is an
    error, not ignored), and the path must stay inside its top directory
    (`tests/../x` traversal is rejected). Outside --release hooks stay free
    prose (they name future suites while the work is pending)."""
    hook = row["verification hook"]
    paths = _HOOK_PATH_RE.findall(hook.replace("\\|", "|"))
    errors: list[str] = []
    if not paths:
        errors.append(
            f"release: {row['id']} hook has no resolvable repo-path reference "
            "(tests/, scripts/ or docs/ file) — prose-only hooks cannot ship")
    for p in paths:
        top = p.split("/", 1)[0]
        candidate = (REPO_ROOT / p).resolve()
        top_root = (REPO_ROOT / top).resolve()
        # pathlib containment, not string prefixing: portable across
        # separators (round 5: the "/"-suffix check broke on Windows).
        inside = candidate == top_root or top_root in candidate.parents
        if ".." in p.split("/") or not inside:
            errors.append(f"release: {row['id']} hook path escapes {top}/: {p}")
        elif not candidate.is_file():
            errors.append(f"release: {row['id']} hook references missing file {p}")
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
