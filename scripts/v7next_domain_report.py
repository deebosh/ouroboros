#!/usr/bin/env python3
"""Report-only domain quotient graph for the v7next integration tree (Ф0).

Reads ``scripts/v7next_domains.toml`` (module -> domain, 1:1), computes the
import graph of THIS tree, collapses modules to domain nodes and REPORTS:

- domain-level edges of the strict graph (unconditional module-level imports),
- cycles on the domain quotient, each with its exact module-edge witnesses,
- a dependency direction table,
- lazy / dynamic / TYPE_CHECKING imports, classified separately and excluded
  from the strict graph.

Per plan §7.1 and roast disposition F17 this tool NEVER gates: cycles are
owner-batch material (regroup vs split vs allowed-edge), so the exit code is 0
whenever the report was produced, regardless of findings.  Exit 2 only when
the report itself cannot be trusted (unreadable/unparseable source, missing
manifest) — a silent skip would falsify the graph.

Output: ``docs/v7next/DOMAIN_QUOTIENT_REPORT.md`` (generated; header names the
generator and the input SHAs).
"""
from __future__ import annotations

import ast
import hashlib
import pathlib
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

try:  # Python 3.11+
    import tomllib
except ImportError:  # pragma: no cover - the 3.10 venv ships tomli
    import tomli as tomllib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "scripts" / "v7next_domains.toml"
REPORT = REPO_ROOT / "docs" / "v7next" / "DOMAIN_QUOTIENT_REPORT.md"

STRICT, TYPE_ONLY, LAZY, DYNAMIC = "strict", "type_checking", "lazy", "dynamic"


def module_name(path: str) -> str:
    parts = path[:-3].split("/")  # drop .py
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def is_type_checking_test(test: ast.expr) -> bool:
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    if isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING":
        return True
    return False


class ImportCollector(ast.NodeVisitor):
    """Collect (kind, raw dotted target or ImportFrom base+names, lineno)."""

    def __init__(self) -> None:
        self.records: list[tuple[str, str, tuple[str, ...], int]] = []
        # each record: (kind, base_or_module, aliases (() for plain import), lineno)
        self._depth = 0  # function nesting depth
        self._tc = 0     # TYPE_CHECKING nesting depth

    def _kind(self) -> str:
        if self._tc:
            return TYPE_ONLY
        if self._depth:
            return LAZY
        return STRICT

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._depth += 1
        self.generic_visit(node)
        self._depth -= 1

    def visit_If(self, node: ast.If) -> None:
        tc = is_type_checking_test(node.test)
        if tc:
            self._tc += 1
        for child in node.body:
            self.visit(child)
        if tc:
            self._tc -= 1
        for child in node.orelse:
            self.visit(child)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.records.append((self._kind(), alias.name, (), node.lineno))

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        names = tuple(a.name for a in node.names)
        base = ("." * node.level) + (node.module or "")
        self.records.append((self._kind(), base, names, node.lineno))

    def visit_Call(self, node: ast.Call) -> None:
        target = None
        f = node.func
        if isinstance(f, ast.Attribute) and f.attr == "import_module":
            target = "?"
        elif isinstance(f, ast.Name) and f.id == "__import__":
            target = "?"
        if target is not None:
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                target = node.args[0].value
            else:
                target = "<unresolved>"
            self.records.append((DYNAMIC, target, (), node.lineno))
        self.generic_visit(node)


def resolve_relative(base: str, importer: str, is_pkg: bool) -> str:
    level = len(base) - len(base.lstrip("."))
    tail = base[level:]
    parts = importer.split(".")
    if not is_pkg:
        parts = parts[:-1]
    if level > 1:
        parts = parts[: len(parts) - (level - 1)]
    prefix = ".".join(parts)
    return f"{prefix}.{tail}" if tail else prefix


def main() -> int:
    if not MANIFEST.is_file():
        print(f"missing manifest: {MANIFEST}", file=sys.stderr)
        return 2
    manifest_bytes = MANIFEST.read_bytes()
    data = tomllib.loads(manifest_bytes.decode("utf-8"))
    modules: dict[str, str] = data["modules"]
    domains: dict[str, str] = data["domains"]
    proposed = set(data.get("classification", {}).get("proposed", []))
    split_pending = set(data.get("split_pending", {}))
    meta = data.get("meta", {})

    bad_domains = sorted({d for d in modules.values() if d not in domains})
    if bad_domains:
        print(f"manifest names unknown domains: {bad_domains}", file=sys.stderr)
        return 2

    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    tracked = subprocess.run(
        ["git", "ls-files", "ouroboros/**/*.py", "ouroboros/*.py",
         "supervisor/*.py", "supervisor/**/*.py", "server.py", "launcher.py"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    tracked_set = set(tracked)
    drift_missing = sorted(tracked_set - set(modules))   # in tree, not in manifest
    drift_stale = sorted(set(modules) - tracked_set)     # in manifest, not in tree

    mod_by_name = {module_name(p): p for p in modules}
    dom_of_path = modules

    def resolve(dotted: str) -> str | None:
        """Longest population module matching the dotted name."""
        parts = dotted.split(".")
        for i in range(len(parts), 0, -1):
            cand = ".".join(parts[:i])
            if cand in mod_by_name:
                return mod_by_name[cand]
        return None

    # kind -> {(src_path, dst_path): [linenos]}
    edges: dict[str, dict[tuple[str, str], list[int]]] = {
        STRICT: defaultdict(list), TYPE_ONLY: defaultdict(list),
        LAZY: defaultdict(list), DYNAMIC: defaultdict(list),
    }
    dynamic_unresolved: list[tuple[str, int]] = []

    for path in sorted(set(modules) & tracked_set):
        src = REPO_ROOT / path
        try:
            tree = ast.parse(src.read_text(encoding="utf-8"), filename=path)
        except (OSError, SyntaxError) as exc:
            print(f"cannot parse {path}: {exc}", file=sys.stderr)
            return 2
        importer = module_name(path)
        is_pkg = path.endswith("__init__.py")
        col = ImportCollector()
        col.visit(tree)
        for kind, base, names, lineno in col.records:
            if kind == DYNAMIC and base == "<unresolved>":
                dynamic_unresolved.append((path, lineno))
                continue
            base_abs = resolve_relative(base, importer, is_pkg) if base.startswith(".") else base
            targets = []
            if names:
                for name in names:
                    if name == "*":
                        targets.append(base_abs)
                        continue
                    sub = f"{base_abs}.{name}" if base_abs else name
                    targets.append(sub if resolve(sub) else base_abs)
            else:
                targets.append(base_abs)
            for dotted in targets:
                dst = resolve(dotted)
                if dst is None or dst == path:
                    continue
                edges[kind][(path, dst)].append(lineno)

    # ---- quotient (strict only) ---------------------------------------------
    dom_edges: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for (src, dst) in sorted(edges[STRICT]):
        d1, d2 = dom_of_path[src], dom_of_path[dst]
        if d1 != d2:
            dom_edges[(d1, d2)].append((src, dst))

    # Tarjan SCC over domain nodes
    graph: dict[str, set[str]] = defaultdict(set)
    for (d1, d2) in dom_edges:
        graph[d1].add(d2)
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    sccs: list[list[str]] = []
    counter = [0]

    def strongconnect(v: str) -> None:
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in sorted(graph.get(v, ())):
            if w not in index:
                strongconnect(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            comp = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            sccs.append(sorted(comp))

    for v in sorted(domains):
        if v not in index:
            strongconnect(v)
    cycles = [c for c in sccs if len(c) > 1]

    # ---- render ---------------------------------------------------------------
    used_domains = sorted({d for pair in dom_edges for d in pair} | set(Counter(dom_of_path.values())))
    manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    dom_counts = Counter(dom_of_path.values())

    L: list[str] = []
    L.append("# Domain quotient report — v7next (report-only)")
    L.append("")
    L.append(f"Generated by `scripts/v7next_domain_report.py` on {now}. Do not edit.")
    L.append("")
    L.append(f"- target tree HEAD: `{head}`")
    L.append(f"- manifest: `scripts/v7next_domains.toml` sha256 `{manifest_sha}`")
    L.append(f"- reference (domain vocabulary): {meta.get('reference_tree', 'unknown')}")
    L.append("- discipline: plan §7.1 / roast F17 — this is a REPORT; cycles are owner-batch")
    L.append("  items (regroup vs split vs allowed-edge), the checker never dictates.")
    L.append("")
    L.append("## Population")
    L.append("")
    L.append(f"- modules mapped: **{len(modules)}** across **{len(dom_counts)}** domains"
             f" ({len(proposed)} rows are `classification=proposed`)")
    if drift_missing:
        L.append(f"- **manifest drift** — tracked but unmapped: {', '.join(f'`{p}`' for p in drift_missing)}")
    if drift_stale:
        L.append(f"- **manifest drift** — mapped but not in tree: {', '.join(f'`{p}`' for p in drift_stale)}")
    if not drift_missing and not drift_stale:
        L.append("- manifest drift: none (manifest == tracked population)")
    L.append("")
    L.append("| domain | modules | proposed |")
    L.append("|---|---:|---:|")
    for d in sorted(dom_counts):
        n_prop = sum(1 for p, dd in dom_of_path.items() if dd == d and p in proposed)
        L.append(f"| {d} — {domains[d]} | {dom_counts[d]} | {n_prop} |")
    L.append("")

    n_strict = sum(len(v) for v in edges[STRICT].values())
    L.append("## Strict import graph")
    L.append("")
    L.append(f"- module-level import edges (unconditional): **{len(edges[STRICT])}** unique, {n_strict} statements")
    L.append(f"- cross-domain module edges: **{sum(len(v) for v in dom_edges.values())}**")
    L.append(f"- domain-level edges (quotient): **{len(dom_edges)}**")
    L.append(f"- quotient cycles (SCCs with >1 domain): **{len(cycles)}**")
    L.append("")

    L.append("## Quotient cycles")
    L.append("")
    if not cycles:
        L.append("None. The strict domain quotient is acyclic.")
    for i, comp in enumerate(cycles, 1):
        comp_set = set(comp)
        n_split_wit = sum(
            1 for (d1, d2), wit in dom_edges.items()
            if d1 in comp_set and d2 in comp_set
            for (src, dst) in wit if src in split_pending or dst in split_pending
        )
        n_all_wit = sum(
            len(wit) for (d1, d2), wit in dom_edges.items()
            if d1 in comp_set and d2 in comp_set
        )
        L.append(f"### Cycle group {i}: {' ⇄ '.join(comp)}")
        L.append("")
        L.append(f"{len(comp)} domains form one strongly connected component. Every edge below")
        L.append("needs an owner disposition (regroup / split / allowed-edge).")
        L.append(f"{n_split_wit} of the {n_all_wit} module-edge witnesses touch a `[split_pending]`")
        L.append("monolith (marked †): those edges are expected to move or dissolve when the")
        L.append("ledger-derived leaves are transplanted (Ф1 recipe, plan §5.3).")
        L.append("")
        for (d1, d2) in sorted(dom_edges):
            if d1 in comp_set and d2 in comp_set:
                wit = dom_edges[(d1, d2)]
                L.append(f"- **{d1} → {d2}** ({len(wit)} module edges)")
                for (src, dst) in wit:
                    lines = ",".join(str(n) for n in sorted(set(edges[STRICT][(src, dst)]))[:4])
                    mark = " †" if src in split_pending or dst in split_pending else ""
                    L.append(f"  - `{src}` → `{dst}` (line {lines}){mark}")
        L.append("")

    L.append("## Domain-level edges (strict)")
    L.append("")
    L.append("| from | to | module edges | in a cycle group |")
    L.append("|---|---|---:|:---:|")
    cyc_nodes = {d for comp in cycles for d in comp}
    for (d1, d2) in sorted(dom_edges):
        incyc = "yes" if d1 in cyc_nodes and d2 in cyc_nodes and any(
            d1 in c and d2 in c for c in map(set, cycles)) else ""
        L.append(f"| {d1} | {d2} | {len(dom_edges[(d1, d2)])} | {incyc} |")
    L.append("")

    L.append("## Dependency direction table")
    L.append("")
    L.append("Rows import columns (module-edge counts, strict graph).")
    L.append("")
    header = "| ↓ imports → | " + " | ".join(used_domains) + " |"
    L.append(header)
    L.append("|---|" + "---|" * len(used_domains))
    for d1 in used_domains:
        row = [f"| **{d1}**"]
        for d2 in used_domains:
            n = len(dom_edges.get((d1, d2), ()))
            row.append(str(n) if n else "·")
        L.append(" | ".join(row) + " |")
    L.append("")

    for kind, title in ((LAZY, "Lazy imports (function-level)"),
                        (TYPE_ONLY, "TYPE_CHECKING imports"),
                        (DYNAMIC, "Dynamic imports (importlib / __import__)")):
        pairs = edges[kind]
        dom_pairs: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
        for (src, dst) in sorted(pairs):
            d1, d2 = dom_of_path[src], dom_of_path[dst]
            if d1 != d2:
                dom_pairs[(d1, d2)].append((src, dst))
        L.append(f"## {title} — excluded from the strict graph")
        L.append("")
        L.append(f"{len(pairs)} module edges, {len(dom_pairs)} cross-domain pairs.")
        L.append("")
        if dom_pairs:
            L.append("| from | to | module edges | pair also strict? |")
            L.append("|---|---|---:|:---:|")
            for (d1, d2) in sorted(dom_pairs):
                also = "yes" if (d1, d2) in dom_edges else "**lazy-only**" if kind == LAZY else "no"
                L.append(f"| {d1} | {d2} | {len(dom_pairs[(d1, d2)])} | {also} |")
            L.append("")
            lazy_only = [(k, v) for k, v in sorted(dom_pairs.items()) if k not in dom_edges]
            if lazy_only:
                L.append(f"Cross-domain pairs reachable ONLY through {kind} imports (hidden coupling):")
                L.append("")
                for (d1, d2), wit in lazy_only:
                    L.append(f"- **{d1} → {d2}**:")
                    for (src, dst) in wit[:20]:
                        L.append(f"  - `{src}` → `{dst}`")
                    if len(wit) > 20:
                        L.append(f"  - … and {len(wit) - 20} more")
                L.append("")
    if dynamic_unresolved:
        L.append("Unresolved dynamic import call sites (non-literal target):")
        L.append("")
        for path, lineno in dynamic_unresolved:
            L.append(f"- `{path}:{lineno}`")
        L.append("")

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"wrote {REPORT}")
    print(f"strict: {len(edges[STRICT])} module edges, {len(dom_edges)} domain edges, {len(cycles)} cycle groups")
    for comp in cycles:
        print("  cycle:", " ⇄ ".join(comp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
