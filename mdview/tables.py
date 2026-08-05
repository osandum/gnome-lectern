"""Markdown `table` subtree -> on-screen Gtk.Grid, plus the plain row-text
extraction shared with the print model.
"""
import statistics

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib

MIN_COLUMN_CHARS = 3  # floor so an all-empty or all-tiny column doesn't collapse to nothing


def column_char_weights(rows):
    """Per-column weight = median character length of that column's cells
    (header row included), used as a simple, sensible proxy for how much
    horizontal space each column deserves. Shared by both the on-screen
    Gtk.Grid (as a max-width-chars hint) and printing.py (as a
    proportional share of the page width), so the two can't disagree
    about which columns are "wide" and which are "narrow".

    Median rather than mean: one abnormally long cell in an otherwise
    narrow column (a stray long sentence in a "Notes" column, say)
    shouldn't blow that column's width out the way an outlier drags a
    mean up.
    """
    if not rows:
        return []
    ncols = max(len(r) for r in rows)
    weights = []
    for col in range(ncols):
        lengths = [len(row[col]) for row in rows if col < len(row)]
        weights.append(max(MIN_COLUMN_CHARS, round(statistics.median(lengths))) if lengths else MIN_COLUMN_CHARS)
    return weights


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
    col_weights = column_char_weights(rows)
    grid = Gtk.Grid(column_spacing=16, row_spacing=4,
                     margin_top=4, margin_bottom=4,
                     margin_start=4, margin_end=4)
    for row_index, row_texts in enumerate(rows):
        is_head = row_index == 0
        for col_index, text in enumerate(row_texts):
            label = Gtk.Label(xalign=0.0, wrap=True, hexpand=True)
            # Caps this label's *natural* width to roughly its column's
            # typical content length, instead of the unwrapped full-text
            # width every column got before -- GtkGrid then sizes each
            # column to the max natural width among its cells, so a
            # short numeric column no longer claims as much room as a
            # column of long sentences.
            label.set_max_width_chars(col_weights[col_index])
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
