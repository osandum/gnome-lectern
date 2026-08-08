"""Headless find tests, with the emphasis on table cells -- the half of
the document that lives in embedded Gtk.Labels rather than the
Gtk.TextBuffer, and which find silently skipped before. Labels report
their text without being realized, so none of this needs a display.
"""
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from markdown_it.tree import SyntaxTreeNode

from lectern.document import make_parser
from lectern.tags import create_tag_table
from lectern.renderer import MarkdownRenderer
from lectern.findbar import FindController, MATCH_BG, CURRENT_BG

_PARSER = make_parser()


def find_controller(markdown_text):
    """Render `markdown_text` into a TextView the way window.py does, and
    return a FindController over the result."""
    tree = SyntaxTreeNode(_PARSER.parse(markdown_text))
    buffer = Gtk.TextBuffer(tag_table=create_tag_table(dark=False))
    renderer = MarkdownRenderer()
    renderer.render(tree, buffer)
    textview = Gtk.TextView()
    textview.set_buffer(buffer)
    renderer.attach_pending_widgets(textview)
    return FindController(textview, renderer.tables)


def current_match(find):
    return find._matches[find._current_index]


def markup_of(cell):
    """The Pango markup a cell's label is currently rendering -- where
    find highlights live, since they're baked into the markup rather than
    layered on as Pango attributes."""
    return cell.label.get_label()


TABLE_DOC = """\
Intro paragraph mentioning apricot.

| Fruit   | Note            |
| ------- | --------------- |
| Apricot | dried apricots  |
| Plum    | fresh           |

Closing paragraph about apricot jam.
"""


def test_finds_text_only_present_in_a_table_cell():
    find = find_controller("| Fruit |\n| ----- |\n| Apricot |\n")
    find.search("apricot")
    assert find.match_count == 1


def test_matches_are_ordered_by_document_position():
    find = find_controller(TABLE_DOC)
    find.search("apricot")
    # intro, header-adjacent cell "Apricot", cell "dried apricots", closing
    assert find.match_count == 4
    kinds = [type(match).__name__ for match in find._matches]
    assert kinds == ["_BufferMatch", "_CellMatch", "_CellMatch", "_BufferMatch"]
    assert find.current_position == 1


def test_advance_steps_into_and_back_out_of_the_table():
    find = find_controller(TABLE_DOC)
    find.search("apricot")
    positions = []
    for _ in range(4):
        positions.append(find.current_position)
        find.advance(1)
    assert positions == [1, 2, 3, 4]
    find.advance(-1)  # wrapped to 1 by the loop above, so this wraps back
    assert find.current_position == 4


def test_cell_matches_are_highlighted_in_the_cell_markup():
    find = find_controller(TABLE_DOC)
    find.search("apricot")
    find.advance(1)  # step off the intro paragraph onto the first cell match
    current = current_match(find)
    other = next(m for m in find._matches if isinstance(m, type(current)) and m is not current)
    assert f'<span background="{CURRENT_BG}">Apricot</span>' in markup_of(current.cell)
    assert f'<span background="{MATCH_BG}">apricot' in markup_of(other.cell)


def test_clear_removes_cell_highlighting():
    find = find_controller(TABLE_DOC)
    find.search("apricot")
    cells = list(find._highlighted_cells)
    assert cells
    find.clear()
    assert find.match_count == 0
    assert all("background" not in markup_of(cell) for cell in cells)


def test_shrinking_the_query_drops_stale_cell_highlights():
    """Backspacing in the find bar used to leave the longer query's
    highlights painted: Gtk.Label.set_attributes merges into the label's
    cached layout rather than replacing it, so ranges that fell out of the
    match set were never removed."""
    find = find_controller("| Col |\n| --- |\n| subject sub su s |\n")
    cell = find._tables[0][1][1][0]
    for query, expected in (("sub", 2), ("su", 3), ("s", 4)):
        find.search(query)
        assert markup_of(cell).count("<span background=") == expected, query
    find.search("")
    assert "background" not in markup_of(cell)


def test_case_sensitive_search_applies_to_cells():
    find = find_controller("| Fruit |\n| ----- |\n| Apricot |\n")
    find.case_sensitive = True
    find.search("apricot")
    assert find.match_count == 0
    find.search("Apricot")
    assert find.match_count == 1


def test_whole_word_search_applies_to_cells():
    find = find_controller("| Fruit |\n| ----- |\n| Apricots and apricot |\n")
    find.whole_word = True
    find.search("apricot")
    assert find.match_count == 1


def test_non_ascii_cell_text_highlights_the_right_slice():
    find = find_controller("| Fruit |\n| ----- |\n| Æble pære |\n")
    find.search("pære")
    assert find.match_count == 1
    match = current_match(find)
    assert match.cell.text[match.start:match.end] == "pære"
    assert f'<span background="{CURRENT_BG}">pære</span>' in markup_of(match.cell)


def test_highlight_inside_a_link_keeps_the_link_intact():
    find = find_controller("| Link |\n| ---- |\n| [apricot jam](https://example.com) |\n")
    find.search("jam")
    markup = markup_of(current_match(find).cell)
    assert '<a href="https://example.com">' in markup
    assert f'<span background="{CURRENT_BG}">jam</span>' in markup


def test_highlight_spanning_an_inline_tag_boundary_stays_well_formed():
    # "e t" straddles the end of the `code` span, so the highlight has to
    # break into one span each side rather than crossing the </tt>.
    find = find_controller("| Cell |\n| ---- |\n| `code` text |\n")
    find.search("e t")
    assert find.match_count == 1
    markup = markup_of(current_match(find).cell)
    assert markup.count("<span background=") == 2
    assert "<tt>cod<span" in markup and "</span></tt>" in markup


def test_cell_text_of_a_link_is_searchable():
    find = find_controller("| Link |\n| ---- |\n| [apricot](https://example.com) |\n")
    find.search("apricot")
    assert find.match_count == 1


def test_search_without_tables_still_works():
    find = find_controller("Just a plain apricot paragraph.\n")
    find.search("apricot")
    assert find.match_count == 1
