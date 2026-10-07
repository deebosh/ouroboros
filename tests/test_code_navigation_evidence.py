"""Public-consumer regressions for syntax evidence, scope, pages and fresh joins."""
import subprocess

import pytest

from ouroboros.tools.query_code import _query_code
from ouroboros.tools.registry import ToolContext, ToolRegistry


@pytest.fixture
def project(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    ctx = ToolContext(repo_dir=repo, drive_root=tmp_path / "data")
    return repo, ctx


def write(repo, path, text):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def body(result):
    return result.split("\n\n", 1)[1] if "\n\n" in result else ""


def public(repo, ctx):
    """query_code through the model-facing ToolRegistry dispatch."""
    registry = ToolRegistry(repo_dir=repo, drive_root=ctx.drive_root)
    return lambda op, **args: registry.execute("query_code", {"op": op, **args})


def test_public_typescript_views_and_file_impact(project):
    repo, ctx = project
    write(repo, "util.ts", "export namespace util {\n"
          "export const objectKeys = typeof Object.keys === 'function' ? (o: object) => Object.keys(o) : (o: object) => [];\n"
          "export function use(o: object) { return objectKeys(o); }\n}\n")
    write(repo, "consumer.ts", 'import type { util } from "./util";\n')
    registry = ToolRegistry(repo_dir=repo, drive_root=ctx.drive_root)
    refs = registry.execute("query_code", {"op": "references", "query": "objectKeys"})
    assert "util.ts:2:" in refs and "util.ts:3:" in refs
    assert "variable_declarator.name" in refs
    callers = registry.execute("query_code", {"op": "callers", "query": "objectKeys"})
    assert "util.ts:3:" in body(callers) and "util.ts:2:" not in body(callers)
    symbols = registry.execute("query_code", {"op": "symbols", "query": "objectKeys"})
    assert "util.ts:2 constant" in symbols and "[outline]" in symbols
    assert "name-field candidate" not in symbols
    digest = registry.execute("query_code", {"op": "digest", "path": "util.ts"})
    assert "Symbols: objectKeys" in digest and "Calls:" in digest
    definition = registry.execute("query_code", {"op": "definition", "query": "objectKeys"})
    assert "util.ts:2 constant" in definition
    impact = registry.execute("query_code", {"op": "impact", "query": "util.ts"})
    assert "consumer.ts:1:" in impact and "import type" in impact
    assert "unique candidate (relative: util.ts) hop 1" in impact


def test_alias_shadow_namespace_and_reexport_are_source_not_binding(project):
    repo, ctx = project
    write(repo, "a.py", "def helper(): pass\ndef own(): return helper()\n")
    write(repo, "b.py", "def helper(): pass\ndef unrelated(): return helper()\n")
    write(repo, "use.py", "from a import helper as chosen\ndef client(chosen): return chosen()\n")
    write(repo, "attr.py", "obj.helper = 1\n")
    refs = _query_code(ctx, "references", query="helper", path="a.py")
    assert "b.py:2:" in refs and "use.py:1:" in refs and "attr.py:1:" in refs
    assert "use.py:2:" not in refs and "order: heuristic" in refs
    chosen = _query_code(ctx, "references", query="chosen", path="use.py")
    assert "parameters" in chosen and "call.function call?" in chosen
    callers = body(_query_code(ctx, "callers", query="helper", path="a.py"))
    assert "b.py:2:" in callers and "attr.py:" not in callers and "use.py:" not in callers
    callees = _query_code(ctx, "callees", query="own", path="a.py")
    assert "a.py:2:" in callees and "helper()" in callees
    write(repo, "ns.ts", "import * as U from './util';\nU.objectKeys({});\n")
    write(repo, "reexport.ts", "export { objectKeys as keys } from './util';\n")
    write(repo, "use.ts", "import { keys } from './reexport';\nkeys();\n")
    refs = _query_code(ctx, "references", query="objectKeys")
    assert "member_expression.property" in refs and "export_specifier.name" in refs
    assert "use.ts:" not in body(refs)
    assert "use.ts:2:" in _query_code(ctx, "references", query="keys")


def test_noncall_decoys_do_not_spend_caller_quota_and_deep_pages_work(project):
    repo, ctx = project
    write(repo, "a.py", "# needle\n" * 6000 + "def real(): return needle()\n")
    result = _query_code(ctx, "callers", query="needle", limit=1)
    assert "a.py:6001:" in body(result)
    assert "1 of 1" in result and "6001 token occurrences" in result
    write(repo, "pages.py", "needle()\n" * 250)
    page = _query_code(ctx, "callers", query="needle", path="pages.py", offset=205, limit=2)
    assert "pages.py:206:" in body(page) and "pages.py:207:" in body(page)
    assert "page re-scanned" in page and "No results" not in page


def test_digest_and_relevance_scope_filter_before_pagination(project):
    repo, ctx = project
    for i in range(210):
        write(repo, f"outside/worker{i:03d}.py", "def worker(): pass\n")
        write(repo, f"target/worker{i:03d}.py", "VALUE=1\n")
    result = _query_code(ctx, "relevant_files", query="worker", path="target", limit=2, offset=205)
    assert "target/worker205.py" in result and "target/worker206.py" in result
    assert "outside/" not in result and "2 of 210" in result
    digest = _query_code(ctx, "digest", path="target", limit=2, offset=205)
    assert body(digest).count("== ") == 2
    assert "outside/" not in digest and "2 of 210" in digest
    past = _query_code(ctx, "digest", path="target/worker000.py", limit=1, offset=100)
    assert "0 of 1" in past and "offset=100" in past and not body(past)


def test_import_join_is_fresh_without_importer_edit_and_depth_is_candidate(project):
    repo, ctx = project
    write(repo, "main.ts", "import {x} from './target';\n")
    write(repo, "upper.ts", "import './main';\n")
    assert "main.ts:1:" not in body(_query_code(ctx, "impact", query="target.ts"))
    write(repo, "target.ts", "export const x=1;\n")
    result = _query_code(ctx, "impact", query="target.ts", depth=2)
    assert "main.ts:1:" in result and "upper.ts:1:" in result
    assert "candidate (relative: main.ts) hop 2" in result
    write(repo, "target.js", "export const x=1;\n")
    ambiguous = _query_code(ctx, "impact", query="target.ts", depth=2)
    assert "ambiguous candidate" in ambiguous and "target.js" in ambiguous
    assert "upper.ts:1:" not in body(ambiguous)
    filtered = _query_code(ctx, "impact", query="target.ts", lang="typescript", depth=2)
    assert "ambiguous candidate" in filtered and "target.js" in filtered
    assert "upper.ts:1:" not in body(filtered)
    (repo / "target.ts").unlink()
    assert "main.ts:1:" not in body(_query_code(ctx, "impact", query="target.ts"))


def test_symbol_impact_keeps_occurrences_and_imports(project):
    repo, ctx = project
    write(repo, "a.py", "def helper(): pass\n")
    write(repo, "use.py", "from a import helper as chosen\nchosen()\n")
    write(repo, "name.cfg", "helper\n")
    result = _query_code(ctx, "impact", query="helper", path="a.py")
    assert "target: symbol helper" in result
    assert "name.cfg:1:" in result and "symbol occurrence evidence" in result
    assert "use.py:1:" in result and "unique candidate" in result
    conflict = _query_code(ctx, "impact", query="use.py", path="a.py")
    assert "TOOL_ARG_ERROR" in conflict and "conflicts" in conflict


@pytest.mark.parametrize("path,source,slot", [
    ("x.py", "def run(): return target()\n", "call.function"),
    ("x.ts", "function run() { return obj.target(); }\n", "member_expression.property"),
    ("x.js", "function run() { return target(); }\n", "call_expression.function"),
    ("x.go", "package main\nfunc run() { pkg.target() }\n", "selector_expression.field"),
    ("X.java", "class X { void run() { obj.target(); } }\n", "method_invocation.name"),
    ("x.rs", "fn run() { thing.target(); }\n", "field_expression.field"),
    ("y.py", "def run(): return (target)()\n", "parenthesized_expression"),
    ("y.ts", "function run() { return obj.target!(); }\n", "member_expression.property"),
])
def test_representative_grammar_callers(project, path, source, slot):
    repo, ctx = project
    write(repo, path, source)
    result = _query_code(ctx, "callers", query="target")
    assert slot in body(result), result
    assert "call?" in result


@pytest.mark.parametrize("path,source,receiver,index,index_slot,parser", [
    ("x.py", "def run(getters, key): return getters[key]() + (target)()\n",
     "getters", "key", "subscript.subscript", True),
    ("x.py", "def run(getters, key): return getters[key]() + target()\n", "getters", "key", "text", False),
    ("x.ts", "function run(getters, key) { getters[key](); return obj.target!(); }\n",
     "getters", "key", "subscript_expression.index", True),
    ("x.rs", "fn run() { getters[key](); thing.target(); }\n", "getters", "key", "index_expression", True),
    ("x.cpp", "int run() { return getters[key]() + obj.target(); }\n",
     "getters", "key", "subscript_argument_list", True),
    ("x.go", "package main\nfunc run() { handlers[0](); handlers[i+1](); pkg.target() }\n",
     "handlers", "i", "binary_expression.left", True),
])
def test_computed_callee_operands_stay_ordinary_references(project, monkeypatch, path, source,
                                                            receiver, index, index_slot, parser):
    from ouroboros import code_intelligence as ci

    repo, ctx = project
    write(repo, path, source)
    if not parser:
        monkeypatch.setattr(ci, "_ts_parser", lambda grammar: None)
    query = public(repo, ctx)
    line = source.count("\n", 0, source.index(receiver + "[")) + 1
    for token in (receiver, index):
        assert not body(query("callers", query=token)), token
        refs = body(query("references", query=token))
        assert f"{path}:{line}:" in refs and "call?" not in refs, refs
    assert index_slot in body(query("references", query=index))
    # The direct or member call beside the computed callee is still recognized
    # and, where run() has an outline range, it is the only callee row.
    target = body(query("callers", query="target")).splitlines()
    assert len(target) == 1, target
    outlined = "[outline]" in query("symbols", query="run")
    assert body(query("callees", query="run")).splitlines() == (target if outlined else [])


def test_go_bracket_calls_keep_the_grammar_ambiguity_visible(project):
    repo, ctx = project
    # Go spells type arguments with index brackets, so syntax alone cannot tell an
    # indexed function value from a generic call: tree-sitter-go gives
    # handlers[i]() and Make[int]() one tree, and both names stay call candidates.
    # A literal or expression index is unambiguous. With one argument the grammar
    # parses a generic-type conversion, which stays a reference.
    write(repo, "x.go", "package main\nfunc run() {\n\thandlers[i]()\n\tMake[int]()\n"
          "\thandlers[0]()\n\tMake[T](x)\n}\n")
    query = public(repo, ctx)
    assert body(query("callers", query="handlers")).splitlines() == [
        "x.go:3:2 call_expression.function call? in run | \thandlers[i]()"]
    assert body(query("callers", query="Make")).splitlines() == [
        "x.go:4:2 call_expression.function call? in run | \tMake[int]()"]
    assert "x.go:5:2 index_expression.operand" in body(query("references", query="handlers"))
    assert "x.go:6:2 generic_type.type" in body(query("references", query="Make"))


def test_unknown_grammar_event_config_and_comment_source(project):
    repo, ctx = project
    write(repo, "emit.js", 'bus.emit("ready");\n')
    write(repo, "listen.ts", 'bus.on("ready", handler);\n')
    write(repo, "settings.cfg", "event=ready\n# ready is configured here\n")
    result = _query_code(ctx, "references", query="ready")
    assert "emit.js:1:" in result and "listen.ts:1:" in result
    assert "settings.cfg:1:" in result and "settings.cfg:2:" in result
    assert "text" in result and "no grammar: cfg" in result
    assert 'bus.on("ready", handler);' in result
    assert not body(_query_code(ctx, "callers", query="ready"))


def test_name_field_candidate_not_universal_definition(project):
    repo, ctx = project
    write(repo, "X.java", "class X { void run() { target(); } }\n")
    write(repo, "x.py", "func(target=1)\n")
    result = _query_code(ctx, "definition", query="target")
    assert "name-field candidate, not a declaration assertion" in result
    assert "method_invocation.name" in result and "keyword_argument.name" in result
    assert "[outline]" not in body(result)
    symbols = _query_code(ctx, "symbols", query="target")
    assert not body(symbols)


def test_nested_git_symbols_and_digest_include_real_files(project):
    repo, ctx = project
    nested = repo / "foreign"
    nested.mkdir()
    subprocess.run(["git", "init", "-q", str(nested)], check=True)
    write(repo, "foreign/.gitignore", "hidden.py\n")
    write(repo, "foreign/mod.py", "def visible(): pass\n")
    write(repo, "foreign/hidden.py", "def invisible(): pass\n")
    result = _query_code(ctx, "symbols", path="foreign/mod.py")
    assert "visible" in result and "nested repositories: 1" in result
    digest = _query_code(ctx, "digest", path="foreign")
    assert "foreign/mod.py" in digest and "foreign/hidden.py" not in digest


def test_missing_parser_keeps_text_and_python_local_calls(project, monkeypatch):
    import ouroboros.code_intelligence as ci
    repo, ctx = project
    write(repo, "x.py", "def run(): return needle()\n")
    write(repo, "x.go", "package main\nfunc run() { needle() }\n")
    monkeypatch.setattr(ci, "_ts_parser", lambda grammar: None)
    refs = _query_code(ctx, "references", query="needle")
    assert "x.py:1:" in refs and "x.go:2:" in refs and "text" in refs
    callers = _query_code(ctx, "callers", query="needle")
    assert "ast.Call.func" in callers and "no grammar: go" in callers
    callees = _query_code(ctx, "callees", query="run", path="x.py")
    assert "needle()" in callees


@pytest.mark.parametrize("mode", ["callers", "callees"])
def test_missing_parser_rechecks_call_fact_hash_after_inventory(project, monkeypatch, mode):
    from ouroboros import code_intelligence as ci, code_occurrences as co

    repo, ctx = project
    write(repo, "x.py", "def run(): return needle()\n")
    monkeypatch.setattr(ci, "_ts_parser", lambda grammar: None)
    original = co.search_skip_reason
    mutated = []

    def mutate_before_source_read(path):
        if path.name == "x.py" and not mutated:
            # The inventory has the call, but the scanner will read a comment.
            # Keep the queried token so the literal prefilter cannot hide the race.
            path.write_text("# needle()\n", encoding="utf-8")
            mutated.append(path)
        return original(path)

    monkeypatch.setattr(co, "search_skip_reason", mutate_before_source_read)
    result = _query_code(ctx, mode, query="run" if mode == "callees" else "needle", path="x.py")

    assert mutated
    assert "ast.Call.func" not in body(result), result
    assert not body(result), result
    assert "QUERY_CODE_TRUNCATED" in result, result
    assert "file changed since inventory; Python call facts not used" in result, result


@pytest.mark.parametrize("parser", [True, False])
def test_callees_never_apply_inventory_ranges_to_changed_bytes(project, monkeypatch, parser):
    from ouroboros import code_intelligence as ci
    from ouroboros import code_occurrences as co

    repo, ctx = project
    write(repo, "a.py", "def run():\n    return target()\n")
    write(repo, "b.py", "def run():\n    return kept()\n")
    if parser:
        assert ci._ts_parser("python") is not None
    else:
        monkeypatch.setattr(ci, "_ts_parser", lambda grammar: None)
    original = co.search_skip_reason

    def move_before_source_read(path):
        if path.name == "a.py":
            # After the inventory: its run() range 1-2 now holds another body.
            path.write_text("def other():\n    return other()\n\n\ndef run():\n    return target()\n",
                            encoding="utf-8")
        return original(path)

    monkeypatch.setattr(co, "search_skip_reason", move_before_source_read)
    result = public(repo, ctx)("callees", query="run")

    assert ("tree-sitter/python" in result) is parser, result
    assert "a.py:" not in body(result), result
    assert "b.py:2" in body(result) and "kept()" in body(result), result
    assert "QUERY_CODE_TRUNCATED" in result and "file changed since inventory" in result, result


def test_callees_never_mix_outline_and_parser_line_models(project):
    repo, ctx = project
    # Python AST ends a line at the bare CR; tree-sitter rows do not.
    (repo / "x.py").write_bytes(b"x = 1\r\ry = 2\ndef run():\n    return target()\n"
                                b"def other():\n    return other()\n")
    query = public(repo, ctx)
    result = query("callees", query="run")
    assert "other()" not in body(result), result
    assert "QUERY_CODE_TRUNCATED" in result and "outline and parser lines differ" in result, result
    write(repo, "x.py", "x = 1\ny = 2\ndef run():\n    return target()\ndef other():\n    return other()\n")
    lf = query("callees", query="run")
    assert "target()" in body(lf) and "other()" not in body(lf), lf


@pytest.mark.parametrize("parser", [True, False])
@pytest.mark.parametrize("separator", ["\f", "\v", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029", "\r"])
def test_source_slices_use_the_line_model_of_their_anchors(project, monkeypatch, separator, parser):
    from ouroboros import code_intelligence as ci

    repo, ctx = project
    write(repo, "x.py", f"# heading{separator}continued\nneedle()\n")
    write(repo, "crlf.py", "# heading\r\nspin()\r\n")
    if not parser:
        monkeypatch.setattr(ci, "_ts_parser", lambda grammar: None)
    # Tree-sitter rows and literal anchors count LF only; Python AST also ends a
    # line at a bare CR. Each slice follows the anchor it renders.
    query = public(repo, ctx)
    line = 3 if separator == "\r" and not parser else 2
    callers = body(query("callers", query="needle"))
    assert f"x.py:{line}" in callers and callers.endswith("| needle()"), repr(callers)
    heading = body(query("references", query="continued"))
    assert "x.py:1:" in heading and f"| # heading{separator}continued" in heading, repr(heading)
    assert body(query("callers", query="spin")).endswith("| spin()")


def test_limits_kind_lang_and_changed_pages(project, monkeypatch):
    from ouroboros import code_occurrences as co
    repo, ctx = project
    write(repo, "a.py", "# needle\ndef broken(:\nneedle()\n")
    write(repo, "b.rs", "fn needle() {}\n")
    refs = _query_code(ctx, "references", query="needle", lang="rust")
    assert "b.rs:" in body(refs) and "a.py:" not in body(refs)
    assert "syntax error" in _query_code(ctx, "references", query="needle", lang="python")
    assert "TOOL_ARG_ERROR" in _query_code(ctx, "callers", query="needle", kind="class")
    assert "TOOL_ARG_ERROR" in _query_code(ctx, "digest", query="needle")
    before = _query_code(ctx, "references", query="needle", limit=1, offset=1)
    write(repo, "0.py", "needle()\n")
    after = _query_code(ctx, "references", query="needle", limit=1, offset=1)
    assert body(before) != body(after) and "no snapshot" in after
    original = co.search_skip_reason
    monkeypatch.setattr(co, "search_skip_reason", lambda p: "oversized" if p.name == "a.py" else original(p))
    assert "oversized: 1" in _query_code(ctx, "references", query="needle")


def test_selected_cap_and_past_collected_page_are_explicit(project, monkeypatch):
    import ouroboros.code_navigation as navigation
    repo, ctx = project
    write(repo, "a.py", "# needle\n" * 100 + "needle()\nneedle()\n")
    original = navigation.scan_occurrences

    def capped(*args, **kwargs):
        return original(*args, max_rows=1, **kwargs)

    monkeypatch.setattr(navigation, "scan_occurrences", capped)
    page = _query_code(ctx, "callers", query="needle", limit=1)
    assert "1 of at least 1" in page and "a.py:101:" in page
    assert "selected row cap 1" in page
    past = _query_code(ctx, "callers", query="needle", limit=1, offset=2)
    assert "QUERY_CODE_TRUNCATED" in past and "offset=2" in past
    assert "No results" not in past


def test_scoped_inventory_is_not_spent_on_unrelated_files(project, monkeypatch):
    import ouroboros.code_intelligence as ci
    repo, ctx = project
    for i in range(8):
        write(repo, f"aaa/unrelated{i}.py", "def worker(): pass\n")
    write(repo, "zzz/worker.py", "def worker(): pass\n")
    monkeypatch.setattr(ci, "_MAX_ENUMERATION_PATHS", 2)
    for op in ("digest", "symbols", "relevant_files", "references"):
        kwargs = {"query": "worker"} if op != "digest" else {}
        out = _query_code(ctx, op, path="zzz", **kwargs)
        assert "zzz/worker.py" in body(out), out
        assert "enumeration_entry_limit" not in out


def test_structural_transport_limit_is_visible(project, monkeypatch):
    import ouroboros.code_search_rg as search
    repo, ctx = project
    write(repo, "large.py", "def worker(): pass\n" * 100)
    monkeypatch.setattr(search, "MAX_FILE_SIZE_BYTES", 10)
    result = _query_code(ctx, "structural", query="FunctionDef")
    assert "oversized" in result and not body(result)
    assert "independent bounded filesystem walk" in result
