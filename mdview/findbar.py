"""Buffer-native find, driven by Gtk.TextIter search rather than any
separate text index. Known limitation: table cells live in embedded
Gtk.Labels, not the TextBuffer, so matches inside them are invisible here
-- acceptable for v1 (see plan)."""
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from . import tags as tagdefs


class FindController:
    def __init__(self, textview):
        self.textview = textview
        self.buffer = textview.get_buffer()
        table = self.buffer.get_tag_table()
        self.tag_match = tagdefs.get_or_create_tag(table, "find-match", {"background": "#f9e79f"})
        # Created after tag_match so it wins on priority ties.
        self.tag_current = tagdefs.get_or_create_tag(table, "find-current", {"background": "#f39c12"})
        self._matches = []       # list[(start_offset, end_offset)]
        self._current_index = -1
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
        self._matches = []
        self._current_index = -1

    def search(self, query):
        self.clear()
        if not query:
            return
        flags = Gtk.TextSearchFlags.TEXT_ONLY
        if not self.case_sensitive:
            flags |= Gtk.TextSearchFlags.CASE_INSENSITIVE
        it = self.buffer.get_start_iter()
        while True:
            result = it.forward_search(query, flags, None)
            if result is None:
                break
            match_start, match_end = result
            if self.whole_word and not (match_start.starts_word() and match_end.ends_word()):
                it = match_end
                continue
            self._matches.append((match_start.get_offset(), match_end.get_offset()))
            self.buffer.apply_tag(self.tag_match, match_start, match_end)
            it = match_end
        if self._matches:
            self._current_index = -1
            self.advance(1)

    def advance(self, direction):
        if not self._matches:
            return
        self._current_index = (self._current_index + direction) % len(self._matches)
        self._highlight_current()

    def _highlight_current(self):
        start, end = self.buffer.get_bounds()
        self.buffer.remove_tag(self.tag_current, start, end)
        start_off, end_off = self._matches[self._current_index]
        s = self.buffer.get_iter_at_offset(start_off)
        e = self.buffer.get_iter_at_offset(end_off)
        self.buffer.apply_tag(self.tag_current, s, e)
        self.textview.scroll_to_iter(s, 0.1, False, 0.0, 0.0)
