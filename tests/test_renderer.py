"""Headless renderer tests. Gtk.TextBuffer manipulation only needs GTK
initialized, not a realized/displayed window, so these run without a
compositor -- `xvfb-run -a pytest` in CI as a portable safety net for
environments with no display at all.
"""
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from markdown_it.tree import SyntaxTreeNode

from mdview.document import make_parser
from mdview.tags import create_tag_table
from mdview.renderer import MarkdownRenderer

# One parser reused across every test in this module, both to avoid
# rebuilding it ~13 times over and to test against the exact same
# construction the app ships (mdview.document.make_parser), rather than a
# hand-copied duplicate that could silently drift from it.
_PARSER = make_parser()


def render(markdown_text):
    """Parse + render `markdown_text`, returning (renderer, buffer)."""
    tree = SyntaxTreeNode(_PARSER.parse(markdown_text))
    buffer = Gtk.TextBuffer(tag_table=create_tag_table(dark=False))
    renderer = MarkdownRenderer()
    renderer.render(tree, buffer)
    return renderer, buffer


def buffer_text(buffer):
    start, end = buffer.get_bounds()
    return buffer.get_text(start, end, True)


def tag_at(buffer, substring, tag_name):
    text = buffer_text(buffer)
    offset = text.index(substring)
    it = buffer.get_iter_at_offset(offset)
    tag = buffer.get_tag_table().lookup(tag_name)
    return it.has_tag(tag)


def test_bold_produces_strong_tag():
    _renderer, buffer = render("hello **world**\n")
    assert "world" in buffer_text(buffer)
    assert tag_at(buffer, "world", "strong")


def test_nested_emphasis_and_strikethrough():
    _renderer, buffer = render("**bold *and italic* text** plus ~~gone~~\n")
    assert tag_at(buffer, "and italic", "strong")
    assert tag_at(buffer, "and italic", "em")
    assert tag_at(buffer, "gone", "strike")


def test_headings_get_level_specific_tags():
    _renderer, buffer = render("# One\n## Two\n###### Six\n")
    assert tag_at(buffer, "One", "heading1")
    assert tag_at(buffer, "Two", "heading2")
    assert tag_at(buffer, "Six", "heading6")


def test_inline_code_tag():
    _renderer, buffer = render("call `f(x)` now\n")
    assert tag_at(buffer, "f(x)", "code-inline")


def test_fenced_code_block_is_tagged_and_highlighted():
    renderer, buffer = render("```python\ndef f():\n    return 1\n```\n")
    assert "def f():" in buffer_text(buffer)
    assert tag_at(buffer, "def", "code-block")
    # keyword highlighting: "def" should carry a pygments tag on top of code-block
    text = buffer_text(buffer)
    it = buffer.get_iter_at_offset(text.index("def"))
    tag_names = {t.get_property("name") for t in it.get_tags()}
    assert "pyg-keyword" in tag_names


def test_list_indent_levels_nest_correctly():
    markdown_text = "- one\n  - nested\n    - double nested\n"
    _renderer, buffer = render(markdown_text)
    assert tag_at(buffer, "one", "list-indent-0")
    assert tag_at(buffer, "nested", "list-indent-0")  # inherits parent's own indent too
    assert tag_at(buffer, "nested", "list-indent-1")
    assert tag_at(buffer, "double nested", "list-indent-2")


def test_ordered_list_uses_source_numbering():
    _renderer, buffer = render("5. five\n6. six\n")
    text = buffer_text(buffer)
    assert "5. five" in text
    assert "6. six" in text


def test_task_list_glyphs_and_no_raw_html():
    _renderer, buffer = render("- [ ] todo\n- [x] done\n")
    text = buffer_text(buffer)
    assert "☐" in text and "todo" in text
    assert "☑" in text and "done" in text
    assert "<input" not in text  # raw html_inline must never leak into the buffer


def test_table_becomes_embedded_widget_and_print_rows():
    renderer, buffer = render("| A | B |\n|---|---|\n| 1 | 2 |\n")
    table_items = [item for item in renderer.print_model if item.kind == "table"]
    assert len(table_items) == 1
    assert table_items[0].rows == [["A", "B"], ["1", "2"]]
    # a child anchor was created and queued for widget attachment
    assert len(renderer._pending_anchors) == 1


def test_table_cell_link_with_code_text_is_not_blank():
    # Regression test: a link whose display text is itself inline code
    # (a common pattern in documentation cross-references, e.g.
    # `[`../app/foo/`](../app/foo/)`) rendered as a completely blank
    # cell -- code_inline is a leaf node with zero children, so the
    # cell-text walker's generic "recurse into children" fallback
    # silently produced nothing for it.
    markdown_text = "| Path |\n|---|\n| [`../app/foo/`](../app/foo/) |\n"
    renderer, buffer = render(markdown_text)
    table_items = [item for item in renderer.print_model if item.kind == "table"]
    assert table_items[0].rows == [["Path"], ["../app/foo/"]]


def test_table_cell_link_is_clickable():
    markdown_text = "| Path |\n|---|\n| [`../app/foo/`](../app/foo/) |\n"
    renderer, buffer = render(markdown_text)
    assert len(renderer.table_link_labels) == 1
    label = renderer.table_link_labels[0]
    markup = label.get_label()
    assert 'href="../app/foo/"' in markup
    assert "../app/foo/" in markup


def test_footnote_ref_and_definition_roundtrip():
    renderer, buffer = render("claim.[^1]\n\n[^1]: the definition.\n")
    text = buffer_text(buffer)
    assert "claim.1" in text
    assert "1. the definition." in text
    assert "↩" in text  # back-reference arrow
    # the ref's dispatch target resolves to a mark that actually exists
    jump_targets = [t for t in renderer.dispatch_targets.values() if t["type"] == "footnote-jump"]
    assert len(jump_targets) == 1
    label = jump_targets[0]["label"]
    mark_name = renderer.footnote_def_mark_name(label)
    assert buffer.get_mark(mark_name) is not None


def test_link_registers_dispatch_target():
    renderer, buffer = render("[text](https://example.com)\n")
    assert tag_at(buffer, "text", "link")
    urls = [t["href"] for t in renderer.dispatch_targets.values() if t["type"] == "url"]
    assert urls == ["https://example.com"]


def test_horizontal_rule_produces_print_item_and_anchor():
    renderer, buffer = render("above\n\n---\n\nbelow\n")
    kinds = [item.kind for item in renderer.print_model]
    assert "hr" in kinds


def test_print_model_paragraph_runs_reconstruct_visible_text():
    renderer, _buffer = render("plain **bold** plain\n")
    reconstructed = "".join(text for item in renderer.print_model for text, _tags in item.runs)
    assert reconstructed == "plain bold plain"
