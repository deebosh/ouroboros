#!/usr/bin/env python3
"""Measure the commit-triad packet for one staged change — offline, $0.

Reports, for the checkout at ``--repo`` (its INDEX is the reviewed change):

* the touched-file pack BEFORE and AFTER the disclosed pack exclusions
  (``review_file_pack.triad_pack_exclusions``: span-only release carriers on a
  VERSION-staged commit, governance docs byte-identical to the inlined prefix);
* the byte-stable governance prefix the triad prepends (checklist section +
  archive, DEVELOPMENT.md, DESIGN.md, ARCHITECTURE.md) and the constitutional
  head (preamble + BIBLE.md) each api row receives per round, part by part;
* the default panel's quorum input limit and the headroom left for the diff.

Three units are printed side by side and never conflated: chars, the host's
own ``utils.estimate_tokens`` (chars/4 — what the fit ladder compares), and
tiktoken ``o200k_base`` (offline when ``TIKTOKEN_CACHE_DIR`` holds the BPE;
not Anthropic's tokenizer). Nothing is dispatched.

Usage::

    python devtools/measure_review_pack.py --repo /path/to/checkout [--json]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def _o200k():
    try:
        import tiktoken

        return tiktoken.get_encoding("o200k_base")
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"o200k unavailable ({exc}); set TIKTOKEN_CACHE_DIR to the BPE cache", file=sys.stderr)
        return None


def _measure(text: str, enc) -> dict:
    from ouroboros.utils import estimate_tokens

    return {
        "chars": len(text),
        "chars_div_4": int(estimate_tokens(text)),
        "o200k": len(enc.encode(text, disallowed_special=())) if enc is not None else None,
    }


def _staged_paths(repo: pathlib.Path) -> list[str]:
    out = subprocess.run(
        ["git", "diff", "--cached", "--name-only"], cwd=str(repo),
        check=True, capture_output=True, text=True,
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def _governance_prefix(repo: pathlib.Path) -> dict[str, str]:
    """The stable prefix parts exactly as `_prepare_unified_review` loads them."""
    from ouroboros.tools import review
    from ouroboros.tools.review_helpers import load_governance_doc

    docs = {
        rel: load_governance_doc(repo, rel, on_missing="explicit")
        for rel in ("docs/DEVELOPMENT.md", "docs/DESIGN.md", "docs/ARCHITECTURE.md")
    }
    stable = review._REVIEW_PROMPT_TEMPLATE_STABLE.format(
        preamble=review.REVIEW_PREAMBLE,
        critical_calibration=review.CRITICAL_FINDING_CALIBRATION,
        json_contract=review.REVIEW_JSON_ARRAY_CONTRACT,
        anti_pattern_lock_guard=review.REPO_ANTI_PATTERN_LOCK_GUARD,
        checklist_section=review._load_checklist_section(),
        dev_guide_text=docs["docs/DEVELOPMENT.md"],
        design_text=docs["docs/DESIGN.md"],
        architecture_section=docs["docs/ARCHITECTURE.md"],
    )
    return {"stable_prefix": stable, "checklist_section": review._load_checklist_section(), **docs}


def _constitutional_head() -> str:
    from ouroboros.tools import review_multi_model as mm
    from ouroboros.tools.review_helpers import load_governance_doc

    bible = load_governance_doc(REPO_ROOT, "BIBLE.md", on_missing="explicit")
    return mm._CONSTITUTIONAL_PREAMBLE + "### BIBLE.md (Full Text)\n\n" + bible + "\n\n---\n\n## REVIEW INSTRUCTIONS\n\n"


def _quorum_limit(models: list[str]) -> int:
    from ouroboros.tools import review as _rv

    def _slot(model: str) -> int:
        window = _rv.reviewer_context_window(model)
        output_reserve, margin = _rv.window_scaled_reserves(
            window, output_reserve=_rv._review_output_budget(), tokenizer_margin=50_000)
        return max(0, _rv.calibrated_input_token_limit(
            model, context_window=window, output_reserve=output_reserve,
            tokenizer_margin=margin, budget_cap=_rv.REVIEW_PROMPT_TOKEN_BUDGET))

    return int(_rv._quorum_input_token_limit(models, {m: _slot(m) for m in models}))


def measure(repo: pathlib.Path) -> dict:
    from ouroboros.reviewer_slot_config import commit_triad_delivery
    from ouroboros.tools.review_file_pack import build_touched_file_pack, triad_pack_exclusions

    enc = _o200k()
    paths = _staged_paths(repo)
    prefix = _governance_prefix(repo)

    def _pack(exclude: set[str], note: str) -> str:
        section, omitted = build_touched_file_pack(repo, paths, exclude_paths=exclude)
        if omitted:
            section += (f"\n\n⚠️ OMISSION NOTE: {len(omitted)} file(s) omitted from direct context: "
                        f"{', '.join(omitted)}")
        if note:
            section += f"\n\n{note}"
        return section

    excluded, note = triad_pack_exclusions(repo, paths, prefix_texts={
        rel: prefix[rel] for rel in ("docs/DEVELOPMENT.md", "docs/DESIGN.md", "docs/ARCHITECTURE.md")})
    before, after = _pack(set(), ""), _pack(excluded, note)
    per_file = {}
    for rel in paths:
        one, _ = build_touched_file_pack(repo, [rel])
        per_file[rel] = {**_measure(one, enc), "excluded": rel in excluded}
    models = list(commit_triad_delivery()["models"])
    limit = _quorum_limit(models)
    head = _constitutional_head()
    prefix_tokens = _measure(prefix["stable_prefix"], enc)["chars_div_4"]
    return {
        "repo": str(repo),
        "staged_paths": paths,
        "touched_pack": {
            "before": _measure(before, enc),
            "after": _measure(after, enc),
            "excluded_paths": sorted(excluded),
            "exclusion_note": note,
            "per_file": per_file,
        },
        "governance_prefix": {
            "stable_prefix_total": _measure(prefix["stable_prefix"], enc),
            "parts": {
                "checklist_section_plus_archive": _measure(prefix["checklist_section"], enc),
                "docs/DEVELOPMENT.md": _measure(prefix["docs/DEVELOPMENT.md"], enc),
                "docs/DESIGN.md": _measure(prefix["docs/DESIGN.md"], enc),
                "docs/ARCHITECTURE.md": _measure(prefix["docs/ARCHITECTURE.md"], enc),
                "constitutional_head_preamble_plus_BIBLE": _measure(head, enc),
            },
        },
        "fit": {
            "panel_models": models,
            "quorum_input_limit_chars_div_4": limit,
            "headroom_for_diff_before": limit - prefix_tokens - _measure(before, enc)["chars_div_4"],
            "headroom_for_diff_after": limit - prefix_tokens - _measure(after, enc)["chars_div_4"],
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True, help="checkout whose staged change is measured")
    parser.add_argument("--json", action="store_true", help="print the full JSON report only")
    args = parser.parse_args(argv)
    report = measure(pathlib.Path(args.repo).resolve())
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    pack, fit = report["touched_pack"], report["fit"]
    print(f"staged paths: {len(report['staged_paths'])}; excluded: {pack['excluded_paths']}")
    for arm in ("before", "after"):
        m = pack[arm]
        print(f"touched pack {arm:6}: {m['chars']:>10,} chars  {m['chars_div_4']:>9,} chars/4  {m['o200k']!s:>9} o200k")
    for rel, m in sorted(pack["per_file"].items(), key=lambda kv: -(kv[1]["o200k"] or 0)):
        flag = "CUT " if m["excluded"] else "keep"
        print(f"  {flag} {rel:40} {m['chars']:>10,} chars {m['o200k']!s:>9} o200k")
    print("governance prefix (per api row, per round):")
    for name, m in report["governance_prefix"]["parts"].items():
        print(f"  {name:42} {m['chars']:>10,} chars {m['o200k']!s:>9} o200k")
    total = report["governance_prefix"]["stable_prefix_total"]
    print(f"  stable prefix total (without BIBLE head) {total['chars']:>10,} chars {total['o200k']!s:>9} o200k")
    print(f"panel {fit['panel_models']}: quorum input limit {fit['quorum_input_limit_chars_div_4']:,} (chars/4); "
          f"headroom for the diff before {fit['headroom_for_diff_before']:,} -> after {fit['headroom_for_diff_after']:,}")
    print(pack["exclusion_note"] or "(no exclusion note)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
