"""Print pipeline tests, driven end-to-end through a real
Gtk.PrintOperation with the EXPORT action -- headless, no printer or
dialog needed, and it exercises the same begin-print/draw-page signals a
real print job does rather than a hand-rolled stand-in for them.
"""
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
        None, print_model, False, "hello.md",
        action=Gtk.PrintOperationAction.EXPORT, export_path=str(out),
    )
    assert result == Gtk.PrintOperationResult.APPLY
    assert out.exists() and out.stat().st_size > 0
