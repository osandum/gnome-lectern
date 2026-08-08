"""Find across both places document text lives: the Gtk.TextBuffer (via
Gtk.TextIter search) and the embedded Gtk.Labels holding table cells.

Table cells are separate widgets outside the buffer entirely (see
tables.build_table_widget), so a Gtk.TextIter walk alone can't see them.
Their matches are found by scanning each cell's own text and marked by
re-rendering that cell's markup with the highlight baked in (see
tables.TableCell.highlight) rather than with TextTags.

Both kinds are merged into one list ordered by document position, so
Next/Previous and the "3 / 12" counter step through the document in
reading order without the caller needing to know which side of the
buffer/widget divide a given match came from.
"""
import re

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib

from . import tags as tagdefs

# Shared by the buffer's TextTags and the cells' markup so a match looks
# the same either side of that divide.
MATCH_BG = "#f9e79f"
CURRENT_BG = "#f39c12"


def _is_word_char(ch):
    return ch.isalnum() or ch == "_"


def _is_whole_word(text, start, end):
    """Cell-label equivalent of the buffer path's
    Gtk.TextIter.starts_word()/ends_word() pair."""
    before = text[start - 1] if start > 0 else ""
    after = text[end] if end < len(text) else ""
    return not (_is_word_char(before) or _is_word_char(after))


class _BufferMatch:
    """Held as offsets rather than iters: iters are invalidated by any
    buffer change, offsets survive."""
    __slots__ = ("start_offset", "end_offset")

    def __init__(self, start_offset, end_offset):
        self.start_offset = start_offset
        self.end_offset = end_offset

    @property
    def sort_key(self):
        return (self.start_offset, 0, 0)


class _CellMatch:
    """A match inside one table cell. `start`/`end` index that cell's
    text. `anchor_offset` is where the whole table sits in the buffer and
    `cell_index` the cell's reading-order position within it -- together
    they place this match among the buffer matches without the table's
    text ever being in the buffer.
    """
    __slots__ = ("anchor_offset", "cell_index", "cell", "start", "end")

    def __init__(self, anchor_offset, cell_index, cell, start, end):
        self.anchor_offset = anchor_offset
        self.cell_index = cell_index
        self.cell = cell
        self.start = start
        self.end = end

    @property
    def sort_key(self):
        return (self.anchor_offset, self.cell_index, self.start)


class FindController:
    def __init__(self, textview, tables=None):
        self.textview = textview
        self.buffer = textview.get_buffer()
        # (Gtk.TextChildAnchor, tables.TableCell grid as [row][col]) per
        # rendered table -- see MarkdownRenderer.tables.
        self._tables = tables or []
        table = self.buffer.get_tag_table()
        self.tag_match = tagdefs.get_or_create_tag(table, "find-match", {"background": MATCH_BG})
        # Created after tag_match so it wins on priority ties.
        self.tag_current = tagdefs.get_or_create_tag(table, "find-current", {"background": CURRENT_BG})
        self._matches = []
        self._current_index = -1
        self._highlighted_cells = []
        self.case_sensitive = False
        self.whole_word = False

    @property
    def match_count(self):
        return len(self._matches)

    @property
    def current_position(self):
        """1-based index of the current match, or 0 if none."""
        return self._current_index + 1 if self._matches else 0

    def clear(self):
        start, end = self.buffer.get_bounds()
        self.buffer.remove_tag(self.tag_match, start, end)
        self.buffer.remove_tag(self.tag_current, start, end)
        for cell in self._highlighted_cells:
            cell.highlight([])
        self._highlighted_cells = []
        self._matches = []
        self._current_index = -1

    def search(self, query):
        self.clear()
        if not query:
            return
        matches = self._search_buffer(query) + self._search_cells(query)
        matches.sort(key=lambda match: match.sort_key)
        self._matches = matches
        if self._matches:
            self._current_index = -1
            self.advance(1)

    def advance(self, direction):
        if not self._matches:
            return
        self._current_index = (self._current_index + direction) % len(self._matches)
        self._highlight_current()

    # -- searching ------------------------------------------------------

    def _search_buffer(self, query):
        flags = Gtk.TextSearchFlags.TEXT_ONLY
        if not self.case_sensitive:
            flags |= Gtk.TextSearchFlags.CASE_INSENSITIVE
        matches = []
        it = self.buffer.get_start_iter()
        while True:
            result = it.forward_search(query, flags, None)
            if result is None:
                break
            match_start, match_end = result
            if self.whole_word and not (match_start.starts_word() and match_end.ends_word()):
                it = match_end
                continue
            matches.append(_BufferMatch(match_start.get_offset(), match_end.get_offset()))
            self.buffer.apply_tag(self.tag_match, match_start, match_end)
            it = match_end
        return matches

    def _search_cells(self, query):
        # re.escape + finditer to mirror the buffer path's TEXT_ONLY,
        # non-overlapping, literal search rather than regex semantics.
        pattern = re.compile(re.escape(query), 0 if self.case_sensitive else re.IGNORECASE)
        matches = []
        for anchor, cells in self._tables:
            if anchor.get_deleted():
                continue
            anchor_offset = self.buffer.get_iter_at_child_anchor(anchor).get_offset()
            cell_index = 0
            for row in cells:
                for cell in row:
                    text = cell.text
                    for found in pattern.finditer(text):
                        if self.whole_word and not _is_whole_word(text, found.start(), found.end()):
                            continue
                        matches.append(
                            _CellMatch(anchor_offset, cell_index, cell, found.start(), found.end())
                        )
                    cell_index += 1
        return matches

    # -- highlighting / scrolling ---------------------------------------

    def _highlight_current(self):
        start, end = self.buffer.get_bounds()
        self.buffer.remove_tag(self.tag_current, start, end)
        self._apply_cell_highlights()
        match = self._matches[self._current_index]
        if isinstance(match, _BufferMatch):
            s = self.buffer.get_iter_at_offset(match.start_offset)
            e = self.buffer.get_iter_at_offset(match.end_offset)
            self.buffer.apply_tag(self.tag_current, s, e)
            self.textview.scroll_to_iter(s, 0.1, False, 0.0, 0.0)
        else:
            self._scroll_to_cell(match)

    def _apply_cell_highlights(self):
        """Re-render every matched cell with its full highlight set. Each
        pass is one walk of the match list, which is cheaper than tracking
        which cell held the current match last time -- and a cell has to
        be re-rendered whole regardless, since its highlights live in its
        markup.
        """
        current = self._matches[self._current_index] if self._matches else None
        ranges = {}
        for match in self._matches:
            if isinstance(match, _BufferMatch):
                continue
            color = CURRENT_BG if match is current else MATCH_BG
            ranges.setdefault(match.cell, []).append((match.start, match.end, color))
        for cell in self._highlighted_cells:
            if cell not in ranges:
                cell.highlight([])
        for cell, cell_ranges in ranges.items():
            cell.highlight(cell_ranges)
        self._highlighted_cells = list(ranges)

    def _scroll_to_cell(self, match):
        """Scroll to the table first (the buffer knows where its anchor
        is), then refine onto the individual cell -- a table taller than
        the window would otherwise leave a match in a low row off-screen.
        The refinement waits for an idle pass because Gtk.TextView only
        positions an anchored child once it has validated the lines around
        it, which scroll_to_iter is what triggers.
        """
        it = self.buffer.get_iter_at_offset(match.anchor_offset)
        self.textview.scroll_to_iter(it, 0.1, False, 0.0, 0.0)
        GLib.idle_add(self._scroll_label_into_view, match.cell.label, priority=GLib.PRIORITY_LOW)

    def _scroll_label_into_view(self, label):
        scrolled = self.textview.get_ancestor(Gtk.ScrolledWindow)
        if scrolled is None or not label.get_mapped():
            return GLib.SOURCE_REMOVE
        ok, bounds = label.compute_bounds(self.textview)
        if not ok:
            return GLib.SOURCE_REMOVE
        # compute_bounds reports widget coordinates; the vadjustment is in
        # buffer coordinates.
        _x, top = self.textview.window_to_buffer_coords(
            Gtk.TextWindowType.WIDGET, 0, int(bounds.origin.y)
        )
        adjustment = scrolled.get_vadjustment()
        page = adjustment.get_page_size()
        value = adjustment.get_value()
        if value <= top and top + bounds.size.height <= value + page:
            return GLib.SOURCE_REMOVE  # already fully visible; don't jump for nothing
        highest = max(adjustment.get_upper() - page, 0.0)
        adjustment.set_value(min(max(top - page * 0.1, 0.0), highest))
        return GLib.SOURCE_REMOVE
