"""Markdown `table` subtree -> on-screen Gtk.Grid, plus the plain row-text
extraction shared with the print model.
"""
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib


def _cell_text(cell_node):
    parts = []

    def walk(node):
        if node.type == "text":
            parts.append(node.content)
        elif node.type == "softbreak":
            parts.append(" ")
        else:
            for child in node.children:
                walk(child)

    walk(cell_node)
    return "".join(parts).strip()


def extract_rows(table_node):
    """Return list[list[str]] of cell text, header row(s) included first."""
    rows = []
    for section in table_node.children:  # thead, tbody
        for tr in section.children:
            rows.append([_cell_text(cell) for cell in tr.children])
    return rows


def build_table_widget(table_node):
    """Return (widget_to_embed, rows) -- rows is reused verbatim for print.

    GFM tables always have exactly one header row (from <thead>) followed
    by the body, so row 0 of the flat list from extract_rows() is always
    the header -- same convention printing.py's _build_table_rows uses.
    """
    rows = extract_rows(table_node)
    grid = Gtk.Grid(column_spacing=16, row_spacing=4,
                     margin_top=4, margin_bottom=4,
                     margin_start=4, margin_end=4)
    for row_index, row_texts in enumerate(rows):
        is_head = row_index == 0
        for col_index, text in enumerate(row_texts):
            label = Gtk.Label(xalign=0.0, wrap=True, hexpand=True)
            if is_head:
                label.set_markup(f"<b>{GLib.markup_escape_text(text)}</b>")
            else:
                label.set_text(text)
            grid.attach(label, col_index, row_index, 1, 1)

    frame = Gtk.Frame()
    frame.set_child(grid)
    frame.set_margin_top(6)
    frame.set_margin_bottom(6)
    return frame, rows
