"""Physical Markdown addresses remain exact across syntax and note metadata."""

import hashlib

import pytest

from ouroboros.markdown_source import MarkdownSourceError, parse_markdown_source


def test_note_metadata_and_source_bytes_survive_parsing():
    raw = (
        "---\r\ntype: unexpected-kind\r\nsummary: |\r\n  First thought.\r\n"
        "  Second thought.\r\ncustom:\r\n  future: [yes, 3]\r\n---\r\n"
        "# Знакомство\r\n\r\nA note [related](../other%20note.md).\r\n"
    ).encode()
    source = parse_markdown_source(raw, "knowledge/people/one.md")
    assert source.raw == raw
    assert source.text.encode() == raw
    assert source.sha256 == hashlib.sha256(raw).hexdigest()
    assert source.frontmatter["type"] == "unexpected-kind"
    assert source.frontmatter["summary"] == "First thought.\nSecond thought.\n"
    assert source.frontmatter["custom"] == {"future": [True, 3]}
    assert source.text_at(source.body_span).startswith("# Знакомство\r\n")
    assert source.links[0].destination == "../other%20note.md"
    assert source.links[0].span.start_line == 11
    assert source.text_at(source.links[0].span) == "[related](../other%20note.md)"


def test_real_markdown_structure_excludes_code_and_resolves_references():
    raw = b"""# Title

A [direct](a.md) and [Reference][r].

```markdown
## Fake heading
[fake](fake.md)
```

    [indented](also-fake.md)

## Actual section

[Reference][] and [r].

[r]: <folder/b.md>
[Reference]: c.md
"""
    source = parse_markdown_source(raw, "note.md")
    assert [h.title for h in source.headings] == ["Title", "Actual section"]
    assert [link.destination for link in source.links] == ["a.md", "folder/b.md", "c.md", "folder/b.md"]
    for link in source.links:
        assert source.text_at(link.span).startswith("[")
    assert source.headings[0].section.end_byte == len(raw)


def test_physical_lf_lines_do_not_split_unicode_line_separators():
    raw = "# Heading\n\nOne\u2028two\u2029three.\nTail.".encode()
    source = parse_markdown_source(raw, "note.md")
    span = source.lines(3, 1)
    assert source.text_at(span) == "One\u2028two\u2029three.\n"
    assert (span.start_line, span.end_line) == (3, 3)
    assert source.text_at(source.lines(4)) == "Tail."
    with pytest.raises(ValueError):
        source.lines(5)


def test_legacy_markdown_and_setext_heading_have_no_invented_metadata():
    raw = b"Title\n=====\n\nLegacy text.\n"
    source = parse_markdown_source(raw, "legacy.md")
    assert source.frontmatter is None
    assert source.body_span.start_byte == 0
    assert source.headings[0].level == 1
    assert source.headings[0].title == "Title"
    assert source.text_at(source.lines()) == raw.decode()


def test_invalid_source_is_not_repaired_into_plausible_content():
    with pytest.raises(MarkdownSourceError, match="bad.md.*UnicodeDecodeError"):
        parse_markdown_source(b"bad\xff", "bad.md")
    with pytest.raises(ValueError, match="mapping"):
        parse_markdown_source(b"---\n- not-a-map\n---\ntext", "bad.md")


def test_invalid_yaml_is_one_value_error_contract_with_its_original_cause():
    import yaml

    with pytest.raises(MarkdownSourceError, match="broken.md") as caught:
        parse_markdown_source(b"---\nx: [unterminated\n---\n# Body\n", "broken.md")
    assert isinstance(caught.value, ValueError)
    assert isinstance(caught.value.__cause__, yaml.YAMLError)


def test_missing_native_grammar_is_a_source_error_without_module_import_failure(monkeypatch):
    import sys
    from types import SimpleNamespace

    def unavailable(name):
        raise LookupError("native grammar missing")

    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", SimpleNamespace(get_parser=unavailable))
    with pytest.raises(MarkdownSourceError, match="body.md: Markdown parser unavailable.*LookupError"):
        parse_markdown_source(b"# Body\n", "body.md")


# NOTE (fork): upstream also carries `test_absent_native_package_preserves_context_import_and_exact_note_read`
# and `test_malformed_book_yaml_uses_existing_context_gap_without_losing_identity` here, exercising
# `ouroboros.context`'s ContextCore.reference_book*  integration and `ouroboros.knowledge`. This fork has not
# adopted that main-loop context integration (only `deep_self_review.py` consumes `reference_books` so far),
# so those two tests are dropped rather than carried in a state that can never pass. See
# ibl-deep-self-review-large-context-truncation for the scope of the adopted slice.
