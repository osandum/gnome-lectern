"""clipboard.py tests: build a real Gtk.TextBuffer from markdown (same
harness as test_renderer.py), select a range, and check the HTML and
Markdown it serializes to. Headless -- Gtk.TextBuffer manipulation needs
GTK initialized, not a realized/displayed window.
"""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gio", "2.0")
from gi.repository import Gtk, Gdk, GLib, Gio
import pytest

from markdown_it.tree import SyntaxTreeNode

from lectern.document import make_parser
from lectern.tags import create_tag_table
from lectern.renderer import MarkdownRenderer
from lectern import clipboard

_PARSER = make_parser()


def render(markdown_text):
    """Parse + render `markdown_text`, returning (renderer, buffer)."""
    tree = SyntaxTreeNode(_PARSER.parse(markdown_text))
    buffer = Gtk.TextBuffer(tag_table=create_tag_table(dark=False))
    renderer = MarkdownRenderer()
    renderer.render(tree, buffer)
    return renderer, buffer


def to_html_and_markdown(markdown_text, offsets=None):
    """Render `markdown_text` and serialize either the whole buffer
    (default) or the [start, end) character-offset range `offsets`."""
    renderer, buffer = render(markdown_text)
    if offsets is None:
        start, end = buffer.get_bounds()
    else:
        start = buffer.get_iter_at_offset(offsets[0])
        end = buffer.get_iter_at_offset(offsets[1])
    return clipboard.selection_to_html_and_markdown(
        buffer, renderer.dispatch_targets, renderer.anchor_descriptors, start, end,
    )


# -- inline formatting --------------------------------------------------------

def test_bold_and_italic():
    html, md = to_html_and_markdown("Some **bold** and *italic* text.\n")
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert "**bold**" in md
    assert "*italic*" in md


def test_nested_emphasis():
    html, md = to_html_and_markdown("bold with *nested italic* inside\n")
    assert "<strong>" not in html  # no bold in this source at all
    assert "<em>nested italic</em>" in html
    assert "*nested italic*" in md


def test_strikethrough_and_inline_code():
    html, md = to_html_and_markdown("~~gone~~ and `code`\n")
    assert "<s>gone</s>" in html
    assert "<code>code</code>" in html
    assert "~~gone~~" in md
    assert "`code`" in md


def test_inline_code_containing_a_backtick_gets_a_wider_fence():
    # CommonMark: the delimiter must be one backtick wider than the
    # longest backtick run already inside the span.
    assert clipboard._md_code_span("a ` b") == "``a ` b``"


def test_inline_code_starting_with_a_backtick_gets_padded():
    # Without the padding space, "```a`" would misread as a 3-backtick
    # fence immediately followed by content, not a 1-backtick span.
    assert clipboard._md_code_span("`a") == "`` `a ``"


def test_link_carries_its_href():
    html, md = to_html_and_markdown("a [link](https://example.com/x) here\n")
    assert '<a href="https://example.com/x">link</a>' in html
    assert "[link](https://example.com/x)" in md


# -- headings and code blocks -------------------------------------------------

def test_heading_levels():
    html, md = to_html_and_markdown("# One\n\n### Three\n")
    assert "<h1>One</h1>" in html
    assert "<h3>Three</h3>" in html
    assert "# One" in md
    assert "### Three" in md


def test_fenced_code_block_does_not_swallow_what_follows():
    html, md = to_html_and_markdown("```\nline one\nline two\n```\n\nAfter the fence.\n")
    assert "<pre><code>line one\nline two</code></pre>" in html
    assert "After the fence." in html
    assert "<pre>" not in html.split("After the fence.")[0].split("</pre>")[-1]
    assert "```\nline one\nline two\n```" in md
    assert "After the fence." in md
    assert md.index("```", md.index("line two")) < md.index("After the fence.")


def test_fenced_code_block_whose_source_already_ends_in_a_newline():
    # A common case (a fence copy-pasted with its own trailing newline)
    # that once made the block absorb everything after it -- see
    # walk_lines' _has_code_block lookahead.
    html, md = to_html_and_markdown("```\ncode\n```\n\nAfter.\n")
    assert "<pre><code>code</code></pre>" in html
    assert "<p>After.</p>" in html
    assert md.strip().endswith("After.")


# -- lists ---------------------------------------------------------------------

def test_bullet_list_is_one_shared_ul():
    html, md = to_html_and_markdown("- one\n- two\n- three\n")
    assert html.count("<ul>") == 1
    assert html.count("</ul>") == 1
    assert html.count("<li>") == 3
    assert "- one\n- two\n- three" in md


def test_ordered_list_preserves_numbers():
    html, md = to_html_and_markdown("5. five\n6. six\n")
    assert "<ol>" in html
    assert "5. five" in md
    assert "6. six" in md


def test_task_list_checked_and_unchecked():
    html, md = to_html_and_markdown("- [ ] todo\n- [x] done\n")
    assert '<input type="checkbox" disabled> todo</li>' in html
    assert '<input type="checkbox" disabled checked> done</li>' in html
    assert "- [ ] todo" in md
    assert "- [x] done" in md


def test_nested_list_lands_inside_the_parent_li():
    html, _md = to_html_and_markdown("- outer\n  - inner\n")
    # The nested <ul> must be a *child* of <li>outer</li>, not its sibling.
    outer_open = html.index("<li>")
    outer_close = html.index("</li>", outer_open)
    assert "<ul>" in html[outer_open:outer_close]


def test_adjacent_lists_of_different_type_do_not_merge():
    html, md = to_html_and_markdown("- bullet\n\n1. ordered\n")
    assert html.count("<ul>") == 1
    assert html.count("<ol>") == 1
    assert html.index("</ul>") < html.index("<ol>")
    assert "- bullet\n\n1. ordered" in md


def test_loose_item_continuation_paragraph_stays_in_its_li():
    html, _md = to_html_and_markdown("- item a\n\n  second paragraph\n\n- item b\n")
    first_li_end = html.index("</li>")
    assert "second paragraph" in html[:first_li_end]
    assert html.count("<li>") == 2


# -- blockquote ------------------------------------------------------------

def test_blockquote_gets_quote_marker_and_wrapper():
    html, md = to_html_and_markdown("> quoted text\n")
    assert "<blockquote><p>quoted text</p></blockquote>" in html.replace("\n", "")
    assert "> quoted text" in md


# -- hr and inline images ----------------------------------------------------

def test_hr_anchor():
    html, md = to_html_and_markdown("before\n\n---\n\nafter\n")
    assert "<hr>" in html
    assert "<p><hr></p>" not in html  # hr is block-level -- invalid inside a <p>
    assert "---" in md


def test_inline_image_anchor():
    html, md = to_html_and_markdown("before ![a dot](dot.png) after\n")
    assert '<img src="dot.png" alt="a dot">' in html
    assert "![a dot](dot.png)" in md


def test_image_alone_in_its_own_paragraph_still_gets_a_p_wrapper():
    # Unlike hr/table, an image is inline -- CommonMark itself renders
    # one alone in its own paragraph inside a <p>.
    html, _md = to_html_and_markdown("![a dot](dot.png)\n")
    assert '<p><img src="dot.png" alt="a dot"></p>' in html


# -- tables --------------------------------------------------------------

def test_table_becomes_a_real_table_in_html():
    html, _md = to_html_and_markdown(
        "| Feature | Supported |\n| --- | --- |\n| Tables | Yes |\n"
    )
    assert "<table><thead><tr><th>Feature</th><th>Supported</th></tr></thead>" in html
    assert "<tbody><tr><td>Tables</td><td>Yes</td></tr></tbody></table>" in html
    assert "<p><table>" not in html  # table is block-level too


def test_table_becomes_a_pipe_table_in_markdown():
    _html, md = to_html_and_markdown(
        "| Feature | Supported |\n| --- | --- |\n| Tables | Yes |\n"
    )
    lines = md.strip().splitlines()
    assert lines[0].startswith("| Feature")
    assert set(lines[1].replace("|", "").strip()) <= {"-", " "}
    assert "| Tables" in lines[2]


def test_table_cell_pipe_is_escaped_in_markdown():
    _html, md = to_html_and_markdown(
        "| A | B |\n| --- | --- |\n| has \\| pipe | plain |\n"
    )
    assert "has \\| pipe" in md


def test_table_between_paragraphs_does_not_absorb_them():
    html, md = to_html_and_markdown(
        "Before.\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n\nAfter.\n"
    )
    assert "<p>Before.</p>" in html
    assert "<p>After.</p>" in html
    assert "Before." not in html.split("<table>")[1]
    assert "Before." in md and "After." in md


# -- footnotes ----------------------------------------------------------------

def test_footnote_reference_and_definition():
    html, md = to_html_and_markdown("A claim.[^1]\n\n[^1]: The citation.\n")
    assert "<sup>1</sup>" in html
    assert "[^1]" in md
    assert "1. The citation." in md


# -- partial (mid-buffer) selection -------------------------------------------

def test_partial_selection_only_covers_the_selected_range():
    markdown = "one two **three** four five\n"
    # Select just "three** four" -- the offsets don't matter precisely,
    # only that it's a proper sub-range, not the whole buffer.
    html, md = to_html_and_markdown(markdown, offsets=(8, 20))
    assert "one two" not in html
    assert "five" not in html


# -- clipboard plumbing -------------------------------------------------------

def test_content_provider_exposes_all_three_flavours():
    provider = clipboard.make_content_provider("plain", "<p>html</p>", "**md**")
    formats = provider.ref_formats()
    assert "text/html" in formats.get_mime_types()
    assert "text/markdown" in formats.get_mime_types()
    assert any(t.name == "gchararray" for t in formats.get_gtypes())


def test_content_provider_round_trips_the_bytes():
    provider = clipboard.make_content_provider("plain", "<p>html</p>", "**md**")
    loop = GLib.MainLoop()
    result = {}

    def on_done(source, res, user_data):
        mime, stream = user_data
        source.write_mime_type_finish(res)
        stream.close(None)
        result[mime] = stream.steal_as_bytes().get_data().decode("utf-8")
        if len(result) == 2:
            loop.quit()

    for mime in ("text/html", "text/markdown"):
        stream = Gio.MemoryOutputStream.new_resizable()
        provider.write_mime_type_async(
            mime, stream, GLib.PRIORITY_DEFAULT, None, on_done, (mime, stream),
        )
    GLib.timeout_add(2000, loop.quit)
    loop.run()
    assert result == {"text/html": "<p>html</p>", "text/markdown": "**md**"}
