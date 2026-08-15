"""Pagination policy tests (#22): _paginate takes plain _Block objects
and a page height, and needs no display -- so these build real blocks
from real markdown (through _build_blocks, against a real
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
    markdown = "".join(f"Filler paragraph {n}.\n\n" for n in range(3)) + "word " * 100 + "\n"
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
    blocks = blocks_for("word " * 110 + "\n", tmp_path)
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

