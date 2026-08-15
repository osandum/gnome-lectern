"""Print pipeline tests, driven end-to-end through a real
Gtk.PrintOperation with the EXPORT action -- headless, no printer or
dialog needed, and it exercises the same begin-print/draw-page signals a
real print job does rather than a hand-rolled stand-in for them.
"""
import re

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk
import pytest

from markdown_it.tree import SyntaxTreeNode

from lectern.document import make_parser
from lectern.tags import create_tag_table
from lectern.renderer import MarkdownRenderer
from lectern import printing
from lectern import zoom as zoomdefs

_PARSER = make_parser()


def print_model_for(markdown_text):
    tree = SyntaxTreeNode(_PARSER.parse(markdown_text))
    buffer = Gtk.TextBuffer(tag_table=create_tag_table(dark=False))
    renderer = MarkdownRenderer()
    renderer.render(tree, buffer)
    return renderer.print_model


def run_export(print_model, tmp_path, header_footer, doc_title="Hello", file_name="hello.md"):
    """Drive begin-print/draw-page exactly as print_document does, but with
    `header_footer` forced rather than left to a dialog checkbox, and
    handing back the mutated state dict so tests can inspect the geometry
    the pipeline actually computed."""
    coordinator = printing.PrintCoordinator()
    op = Gtk.PrintOperation()
    op.set_default_page_setup(printing._print_page_setup())
    op.set_export_filename(str(tmp_path / "out.pdf"))
    state = {
        "header_footer": header_footer,
        "header_left": printing._header_left_text(doc_title, file_name),
    }
    op.connect("begin-print", coordinator._on_begin_print, print_model, False, state)
    op.connect("draw-page", coordinator._on_draw_page, state)
    result = op.run(Gtk.PrintOperationAction.EXPORT, None)
    assert result == Gtk.PrintOperationResult.APPLY
    return state


# -- #13: page margins and base font size -----------------------------------

def test_print_base_font_is_one_zoom_notch_below_screen_size():
    """PRINT_BASE_PT rides zoom.py's own 0.9 step, not a value invented
    just for print -- see the STEPS ladder in zoom.py."""
    assert printing.PRINT_BASE_PT == pytest.approx(zoomdefs.BASE_PT * 0.9)
    assert printing.PRINT_BASE_PT == pytest.approx(10.8)


def test_page_setup_adds_margin_on_three_sides_only():
    setup = printing._print_page_setup()
    assert setup.get_top_margin(Gtk.Unit.MM) == pytest.approx(printing.PAGE_MARGIN_MM)
    assert setup.get_left_margin(Gtk.Unit.MM) == pytest.approx(printing.PAGE_MARGIN_MM)
    assert setup.get_right_margin(Gtk.Unit.MM) == pytest.approx(printing.PAGE_MARGIN_MM)
    # Bottom is untouched -- GTK's own default is already generous (more
    # than the 10mm added elsewhere), and the issue is explicit that it
    # should not be applied symmetrically.
    default_bottom = Gtk.PageSetup().get_bottom_margin(Gtk.Unit.MM)
    assert setup.get_bottom_margin(Gtk.Unit.MM) == pytest.approx(default_bottom)
    assert setup.get_bottom_margin(Gtk.Unit.MM) > printing.PAGE_MARGIN_MM


def test_print_document_exports_successfully(tmp_path):
    print_model = print_model_for("# Hello\n\nSome *text* and a [link](x).\n")
    coordinator = printing.PrintCoordinator()
    out = tmp_path / "out.pdf"
    result = coordinator.print_document(
        None, print_model, False, "Hello", "hello.md",
        action=Gtk.PrintOperationAction.EXPORT, export_path=str(out),
    )
    assert result == Gtk.PrintOperationResult.APPLY
    assert out.exists() and out.stat().st_size > 0


def pdf_page_count(path):
    # A page object's dict has "/Type /Page", the document catalog's page
    # *tree* root has "/Type /Pages" -- the lookahead tells the two apart
    # without pulling in a PDF-parsing dependency just for a page count.
    return len(re.findall(rb"/Type\s*/Page(?!s)", path.read_bytes()))


def test_print_document_forwards_header_footer_flag(tmp_path):
    """header_footer is now a plain argument on print_document (see the
    docstring on that method for why it can no longer be a dialog
    checkbox) -- this is the one test that exercises the public API end
    to end rather than driving begin-print/draw-page directly."""
    markdown = "# Hello\n\n" + "more text. " * 500 + "\n"
    print_model = print_model_for(markdown)
    coordinator = printing.PrintCoordinator()
    plain_out, decorated_out = tmp_path / "plain.pdf", tmp_path / "decorated.pdf"
    coordinator.print_document(
        None, print_model, False, "Hello", "hello.md",
        action=Gtk.PrintOperationAction.EXPORT, export_path=str(plain_out),
    )
    coordinator.print_document(
        None, print_model, False, "Hello", "hello.md",
        action=Gtk.PrintOperationAction.EXPORT, export_path=str(decorated_out),
        header_footer=True,
    )
    # Same content, less usable height per page with the header/footer on
    # -> at least as many pages, which only happens if the flag actually
    # reached _on_begin_print.
    assert pdf_page_count(decorated_out) >= pdf_page_count(plain_out)


# -- #14: optional header/footer ---------------------------------------------

def test_header_left_text_combines_title_and_file_name():
    assert printing._header_left_text("My Report", "report.md") == "My Report – report.md"


def test_header_left_text_falls_back_to_file_name_alone():
    assert printing._header_left_text(None, "report.md") == "report.md"
    # A document titled exactly after its own file name would otherwise
    # repeat itself ("report.md -- report.md").
    assert printing._header_left_text("report.md", "report.md") == "report.md"


def test_header_footer_is_off_unless_explicitly_turned_on(tmp_path):
    print_model = print_model_for("# Hello\n\nSome text.\n")
    state = run_export(print_model, tmp_path, header_footer=False)
    assert state["header_band"] == 0.0


def test_header_footer_claims_room_from_the_body_not_the_paper(tmp_path):
    """Enabling the header/footer must not shrink the page itself (that's
    #13's job) -- it takes its space out of the body height that
    _paginate sees, which is why the same content needs at least as many
    pages once it's on."""
    markdown = "# Hello\n\n" + "more text. " * 500 + "\n"
    print_model = print_model_for(markdown)
    plain = run_export(print_model, tmp_path, header_footer=False)
    decorated = run_export(print_model, tmp_path, header_footer=True)
    assert plain["header_band"] == 0.0
    assert decorated["header_band"] == pytest.approx(printing.HEADER_BAND_PT)
    assert plain["width"] == decorated["width"]
    assert plain["height"] == decorated["height"]
    assert len(decorated["pages"]) >= len(plain["pages"])
    assert len(decorated["pages"]) > 1
