import gi
import cairo
gi.require_foreign("cairo")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Graphene", "1.0")
from gi.repository import Gtk, Adw, Graphene

from . import tags as tagdefs

_INLINE_CODE_PAD_X = 4
_INLINE_CODE_PAD_Y = 2
_INLINE_CODE_RADIUS = 6
_CODE_BLOCK_PAD = 16
_CODE_BLOCK_RADIUS = 6
_HEADING_RULE_PAD = 8
_HEADING_RULE_WIDTH = 1


def _rounded_rect(cr, x, y, width, height, radius):
    radius = max(0.0, min(radius, width / 2.0, height / 2.0))
    if radius == 0:
        cr.rectangle(x, y, width, height)
        return
    x2, y2 = x + width, y + height
    cr.new_sub_path()
    cr.arc(x2 - radius, y + radius, radius, -1.5707963267948966, 0.0)
    cr.arc(x2 - radius, y2 - radius, radius, 0.0, 1.5707963267948966)
    cr.arc(x + radius, y2 - radius, radius, 1.5707963267948966, 3.141592653589793)
    cr.arc(x + radius, y + radius, radius, 3.141592653589793, 4.71238898038469)
    cr.close_path()


class DecoratedTextView(Gtk.TextView):
    def _tagged_ranges(self, tag):
        buffer = self.get_buffer()
        if buffer is None:
            return []
        ranges = []
        it = buffer.get_start_iter()
        while it.forward_to_tag_toggle(tag):
            if not it.starts_tag(tag):
                continue
            start = it.copy()
            end = start.copy()
            if not end.forward_to_tag_toggle(tag):
                end = buffer.get_end_iter()
            ranges.append((start, end))
            if end.compare(it) <= 0:
                break
            it = end
        return ranges

    def _segment_rect(self, start, end):
        it = start.copy()
        x0 = y0 = x1 = y1 = None
        while it.compare(end) < 0:
            rect = self.get_iter_location(it)
            if x0 is None:
                x0, y0 = rect.x, rect.y
                x1, y1 = rect.x + rect.width, rect.y + rect.height
            else:
                x0 = min(x0, rect.x)
                y0 = min(y0, rect.y)
                x1 = max(x1, rect.x + rect.width)
                y1 = max(y1, rect.y + rect.height)
            if not it.forward_char():
                break
        if x0 is None:
            return None
        wx, wy = self.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, x0, y0)
        return (wx, wy, x1 - x0, y1 - y0)

    def _line_segment_rects(self, start, end):
        rects = []
        line_start = start.copy()
        while line_start.compare(end) < 0:
            line_end = line_start.copy()
            if not line_end.ends_line():
                line_end.forward_to_line_end()
            if line_end.compare(end) > 0:
                line_end = end.copy()
            rect = self._segment_rect(line_start, line_end)
            if rect is not None:
                rects.append(rect)
            if line_end.compare(end) >= 0:
                break
            line_start = line_end.copy()
            if line_start.compare(end) < 0:
                line_start.forward_char()
        return rects

    @staticmethod
    def _union_rects(rects):
        if not rects:
            return None
        x0 = min(r[0] for r in rects)
        y0 = min(r[1] for r in rects)
        x1 = max(r[0] + r[2] for r in rects)
        y1 = max(r[1] + r[3] for r in rects)
        return (x0, y0, x1 - x0, y1 - y0)

    def _append_cairo(self, snapshot):
        bounds = Graphene.Rect()
        bounds.init(0, 0, self.get_allocated_width(), self.get_allocated_height())
        return snapshot.append_cairo(bounds)

    @staticmethod
    def _set_source_rgba(cr, rgba):
        cr.set_source_rgba(rgba.red, rgba.green, rgba.blue, rgba.alpha)

    def _draw_inline_code(self, cr, buffer):
        tag = buffer.get_tag_table().lookup("code-inline")
        if tag is None:
            return
        bg = tag.get_property("background-rgba")
        for start, end in self._tagged_ranges(tag):
            for x, y, width, height in self._line_segment_rects(start, end):
                _rounded_rect(
                    cr,
                    x - _INLINE_CODE_PAD_X,
                    y - _INLINE_CODE_PAD_Y,
                    width + _INLINE_CODE_PAD_X * 2,
                    height + _INLINE_CODE_PAD_Y * 2,
                    _INLINE_CODE_RADIUS,
                )
                self._set_source_rgba(cr, bg)
                cr.fill()

    def _draw_code_blocks(self, cr, buffer):
        tag = buffer.get_tag_table().lookup("code-block")
        if tag is None:
            return
        inline_tag = buffer.get_tag_table().lookup("code-inline")
        bg = inline_tag.get_property("background-rgba") if inline_tag is not None else None
        if bg is None:
            return
        for start, end in self._tagged_ranges(tag):
            rect = self._union_rects(self._line_segment_rects(start, end))
            if rect is None:
                continue
            x, y, width, height = rect
            _rounded_rect(
                cr,
                x - _CODE_BLOCK_PAD,
                y - _CODE_BLOCK_PAD,
                width + _CODE_BLOCK_PAD * 2,
                height + _CODE_BLOCK_PAD * 2,
                _CODE_BLOCK_RADIUS,
            )
            self._set_source_rgba(cr, bg)
            cr.fill()

    def _draw_heading_rules(self, cr, buffer):
        dark = Adw.StyleManager.get_default().get_dark()
        color = tagdefs.heading_rule_rgba(dark)
        right = self.get_hadjustment().get_page_size() - self.get_right_margin()
        for name in ("heading1", "heading2"):
            tag = buffer.get_tag_table().lookup(name)
            if tag is None:
                continue
            for start, end in self._tagged_ranges(tag):
                rect = self._union_rects(self._line_segment_rects(start, end))
                if rect is None:
                    continue
                x, y, width, height = rect
                baseline = y + height + _HEADING_RULE_PAD
                cr.set_line_width(_HEADING_RULE_WIDTH)
                self._set_source_rgba(cr, color)
                cr.move_to(x, baseline)
                cr.line_to(max(right, x + width), baseline)
                cr.stroke()

    def do_snapshot(self, snapshot):
        buffer = self.get_buffer()
        if buffer is not None:
            cr = self._append_cairo(snapshot)
            self._draw_code_blocks(cr, buffer)
            self._draw_inline_code(cr, buffer)
            self._draw_heading_rules(cr, buffer)
        Gtk.TextView.do_snapshot(self, snapshot)
