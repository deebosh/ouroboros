# Ledger corrections discovered during v7next transplants (append-only)

Rows of the reference MIGRATION_v7.md / DOMAIN_MAP.md falsified by upstream drift,
with evidence, found lane by lane. Applied to the campaign's carried ledger at F5.

## From the D15 pilot (base b9f7597f, 2026-08-30)
1. MIGRATION row 351 (`tools/core.py::_filter_out_project_store` ->
   `project_facts.py::filter_out_project_store`, status "pending upstream
   transfer") — SUPERSEDED-BY-UPSTREAM: the tip already carries the extraction
   (project_facts.py byte-identical to the reference; core.py keeps only the
   import alias at :17 with two call sites).
2. DOMAIN_MAP §D15 "v7 delta" prose — remeasure from the new base: consolidator
   delta absorbed upstream (now 0); the true residue is +23/-12 in two files
   (consciousness.py, reflection.py), not +25/-14 in three.
3. RE-PROVE TRAP (D02 family, reflection.py): the reference's
   `_trace_call_errored` reads `_OK_TOOL_STATUSES` (with "untyped") from the v7
   leaf `_outcome_tool_errors`, which upstream does not have; a verbatim replay
   of the delta over upstream's own status handling would invert the fix. The
   D02 adoption must re-derive the delta against upstream bytes.
4. MIGRATION row 166 (retirement of 4 CLAUDE_CODE markers, id "none") — needs an
   explicit ADOPTION disposition (umbrella under D02 or its own row): zero
   production emitters of those markers exist at this tip (claim re-proven).
