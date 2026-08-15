"""Pagination policy tests (#22, #23): _paginate takes plain _Block
objects and a page height, and needs no display -- so these build real
blocks from real markdown (through _build_blocks, against a real
Gtk.PrintContext from a throwaway EXPORT job) and then call _paginate
directly with a page height chosen from the blocks' own measured
geometry, never a guessed constant. That keeps the tests independent of
font metrics, which differ machine to machine.
"""
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from markdown_it.tree import SyntaxTreeNode

from lectern.document import make_parser
from lectern.tags import create_tag_table
from lectern.renderer import MarkdownRenderer
from lectern import tags as tagdefs
from lectern import printing

_PARSER = make_parser()


def print_model_for(markdown_text):
    tree = SyntaxTreeNode(_PARSER.parse(markdown_text))
    buffer = Gtk.TextBuffer(tag_table=create_tag_table(dark=False))
    renderer = MarkdownRenderer()
    renderer.render(tree, buffer)
    return renderer.print_model


def blocks_for(markdown_text, tmp_path):
    """The real _build_blocks output for `markdown_text`, against a real
    (but arbitrarily large) Gtk.PrintContext -- large so no image/diagram
    ever needs scaling down, which would make its height a function of
    the page rather than of its own content."""
    print_model = print_model_for(markdown_text)
    captured = {}

    def begin_print(op, ctx):
        style_table = tagdefs.tag_style_props(False)
        captured["blocks"] = printing._build_blocks(
            ctx, style_table, print_model, ctx.get_width(), 100_000.0, False,
        )
        op.set_n_pages(1)

    def draw_page(op, ctx, page_nr):
        pass

    op = Gtk.PrintOperation()
    op.set_export_filename(str(tmp_path / "probe.pdf"))
    op.connect("begin-print", begin_print)
    op.connect("draw-page", draw_page)
    result = op.run(Gtk.PrintOperationAction.EXPORT, None)
    assert result == Gtk.PrintOperationResult.APPLY
    return captured["blocks"]


def cumulative_height(blocks, upto):
    """y position right after placing blocks[0:upto], on a page tall
    enough that none of them ever break -- the same arithmetic
    _paginate itself does, just without the page-height cap."""
    total = 0.0
    for i, block in enumerate(blocks[:upto]):
        if i > 0:
            total += block.gap
        total += block.height
        if block.rule:
            total += printing.HEADING_RULE_PAD_PT + printing.HEADING_RULE_WIDTH_PT
    return total


def line_extent(lines, n):
    """Height of the first `n` lines as a single chunk, exactly the
    quantity _paginate's own greedy chunk builder compares against
    `remaining` -- (y_top - chunk_top) + height of the last of them."""
    first, last = lines[0], lines[n - 1]
    return (last[1] - first[1]) + last[2]


def word_count_for_lines(n, tmp_path):
    """A "word " repeat count that wraps to exactly `n` lines when laid
    out alone, found by measuring against this machine's live font
    metrics rather than assumed -- a fixed repeat count calibrated to
    hit `n` lines on one machine's fonts is not portable (this is
    exactly what broke CI once already: a count that gave 5 lines
    locally gave 6 there). Converges in a couple of steps since a
    uniform filler word wraps at a very predictable rate.
    """
    k = max(1, n * 6)
    for _ in range(30):
        lines = len(blocks_for("word " * k + "\n", tmp_path)[0].lines)
        if lines == n:
            return k
        k = max(1, round(n * k / lines))
    raise AssertionError(f"could not find a word count wrapping to exactly {n} lines")


# -- #22 part 1: keep a lead-in paragraph with the block it introduces ------

def test_short_lead_in_moves_with_the_hr_it_introduces(tmp_path):
    markdown = "".join(f"Filler paragraph {n}.\n\n" for n in range(5)) + "Intro line.\n\n---\n"
    blocks = blocks_for(markdown, tmp_path)
    fillers, intro, hr = blocks[:5], blocks[5], blocks[6]
    assert intro.kind == "layout" and len(intro.lines) <= printing.LEAD_IN_MAX_LINES
    assert hr.kind == "hr"

    before_intro = cumulative_height(blocks, 5)
    intro_bottom = before_intro + intro.gap + intro.height
    # One point short of what hr needs to join intro on this page --
    # forces a break, but leaves plenty of margin for intro+hr to fit a
    # *fresh* page together (this page already holds 5 fillers' worth).
    page_height = intro_bottom + hr.gap + hr.height - 1.0

    pages = printing._paginate(blocks, page_height)
    assert len(pages) == 2
    # intro retracted off page 1 -- only the 5 fillers remain.
    assert len(pages[0]) == 5
    assert pages[1][0]["type"] == "layout"
    assert pages[1][1]["type"] == "hr"


def test_lead_in_left_behind_if_it_would_not_fit_with_the_block_either(tmp_path):
    """The other half of the guard: if intro+hr don't fit a fresh page
    together either, retracting would just bounce the pair forever --
    so intro stays put and only hr moves."""
    markdown = "".join(f"Filler paragraph {n}.\n\n" for n in range(5)) + "Intro line.\n\n---\n"
    blocks = blocks_for(markdown, tmp_path)
    intro, hr = blocks[5], blocks[6]
    before_intro = cumulative_height(blocks, 5)
    intro_bottom = before_intro + intro.gap + intro.height
    page_height = intro_bottom + hr.gap + hr.height - 1.0
    # A page too short for intro+hr together at all.
    tiny_page_height = intro.height + hr.gap + hr.height - 1.0
    assert tiny_page_height < page_height  # sanity: really is smaller

    pages = printing._paginate(blocks, tiny_page_height)
    # intro stays on whatever page it naturally landed on; hr is the
    # last block in the document, so if it couldn't join intro it must
    # be starting a final page entirely on its own.
    assert len(pages[-1]) == 1 and pages[-1][0]["type"] == "hr"


# -- #22 part 2: orphans and widows in wrapped text --------------------------

def test_a_single_line_is_not_stranded_at_a_page_foot(tmp_path):
    # Short enough (5 lines) that once deferred, the whole paragraph
    # fits a single fresh page in one go -- otherwise it would rightly
    # keep spilling onto further pages of its own, which isn't what this
    # test is about.
    body_words = word_count_for_lines(5, tmp_path)
    markdown = "".join(f"Filler paragraph {n}.\n\n" for n in range(3)) + "word " * body_words + "\n"
    blocks = blocks_for(markdown, tmp_path)
    fillers, long_block = blocks[:3], blocks[3]
    assert long_block.kind == "layout" and len(long_block.lines) == 5

    before_long = cumulative_height(blocks, 3)
    start = before_long + long_block.gap
    extent_1, extent_2 = line_extent(long_block.lines, 1), line_extent(long_block.lines, 2)
    # Room for exactly one line of the paragraph -- not two.
    page_height = start + (extent_1 + extent_2) / 2

    pages = printing._paginate(blocks, page_height)
    assert len(pages) == 2
    # The stranded single line was deferred whole -- page 1 ends with
    # just the fillers, and all 5 lines land together on page 2.
    assert len(pages[0]) == 3
    assert len(pages[1]) == 1
    assert len(pages[1][0]["lines"]) == 5


def test_a_single_line_is_not_carried_over_alone_to_a_page_head(tmp_path):
    # Exactly 6 lines: enough to still have 2+ lines left over once one
    # is held back, short enough that both pages hold the paragraph in
    # one chunk each rather than spilling further.
    blocks = blocks_for("word " * word_count_for_lines(6, tmp_path) + "\n", tmp_path)
    long_block = blocks[0]
    assert long_block.kind == "layout" and len(long_block.lines) == 6

    extent_4 = line_extent(long_block.lines, 4)
    extent_5 = line_extent(long_block.lines, 5)
    # Room for exactly the first five lines -- naive greedy chunking
    # would leave the sixth stranded alone at the head of page 2.
    page_height = (extent_4 + extent_5) / 2

    pages = printing._paginate(blocks, page_height)
    assert len(pages) == 2
    assert len(pages[0][0]["lines"]) == 4
    assert len(pages[1][0]["lines"]) == 2


# -- #23: keep a heading with the section it introduces ----------------------

def blocks_with_heading_and_body(heading_markdown, body_lines, tmp_path):
    """Filler paragraphs + `heading_markdown` + a body paragraph of
    exactly `body_lines` lines, growing the filler count until the
    *whole* heading+body group is provably small enough to land
    entirely on one fresh page once _paginate moves it there.

    Without this, "everything fits on the fresh page in one go" is
    only true by coincidence of how big a handful of fillers happen to
    be relative to however many lines the body wraps to on this
    machine's font metrics -- which is exactly what made these tests
    fail on CI (a body sized in words, not measured lines, wrapped to
    more/taller lines there than it did locally, and the fresh page's
    budget -- sized off the fillers alone -- no longer covered it).
    """
    body_words = word_count_for_lines(body_lines, tmp_path)
    fillers = 5
    while True:
        markdown = (
            "".join(f"Filler paragraph {n}.\n\n" for n in range(fillers))
            + heading_markdown + "word " * body_words + "\n"
        )
        blocks = blocks_for(markdown, tmp_path)
        heading_index = fillers
        before_heading = cumulative_height(blocks, heading_index)
        lookahead = printing._heading_group_height(blocks, heading_index)
        full_group = cumulative_height(blocks[heading_index:], len(blocks) - heading_index)
        if before_heading + blocks[heading_index].gap + lookahead - 1.0 >= full_group:
            return blocks, heading_index, before_heading, lookahead
        fillers += 5


def test_heading_moves_to_a_fresh_page_with_its_section(tmp_path):
    blocks, heading_index, before_heading, lookahead = blocks_with_heading_and_body(
        "### Section\n\n", 6, tmp_path,
    )
    fillers, heading, body = blocks[:heading_index], blocks[heading_index], blocks[heading_index + 1]
    assert heading.heading_level == 3
    assert body.heading_level is None and len(body.lines) >= printing.HEADING_LOOKAHEAD_LINES + 2

    # One point short of room for heading + lookahead -- but comfortably
    # under what a fresh page (which doesn't carry the fillers) needs.
    page_height = before_heading + heading.gap + lookahead - 1.0
    assert lookahead <= page_height  # sanity: the group does fit fresh

    pages = printing._paginate(blocks, page_height)
    assert len(pages) == 2
    assert len(pages[0]) == len(fillers)
    assert pages[1][0]["type"] == "layout"
    assert pages[1][1]["type"] == "layout"
    assert len(pages[1][1]["lines"]) >= printing.HEADING_LOOKAHEAD_LINES


def test_consecutive_headings_chain_rather_than_split(tmp_path):
    blocks, heading_index, before_a, lookahead = blocks_with_heading_and_body(
        "## A\n\n### B\n\n", 6, tmp_path,
    )
    fillers = blocks[:heading_index]
    heading_a, heading_b = blocks[heading_index], blocks[heading_index + 1]
    assert heading_a.heading_level == 2 and heading_b.heading_level == 3

    page_height = before_a + heading_a.gap + lookahead - 1.0
    assert lookahead <= page_height

    pages = printing._paginate(blocks, page_height)
    assert len(pages) == 2
    # Both headings moved together, not split across the break.
    assert len(pages[0]) == len(fillers)
    assert pages[1][0]["type"] == "layout"
    assert pages[1][1]["type"] == "layout"
    assert pages[1][2]["type"] == "layout"
