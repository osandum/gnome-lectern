"""Text measurement for diagram layout, and the matching layout builder
drawing uses.

Both sides go through `make_layout` on purpose. Layout measures strings
against a scratch context to decide how big a node is; drawing re-lays the
same string out against a cairo context that may be a window or a printer.
Those two land in the same place only if every knob (font, absolute size,
wrap width, alignment) is set identically, so there is one function that
sets them and no second copy to drift.

Sizes here are *absolute* -- `set_absolute_size`, not `set_size`. A point
size would be multiplied by the context's resolution, which is 96dpi for a
window and 72dpi for a Gtk.PrintContext, so the same node would come out a
third smaller on paper than the box laid out for it. Absolute sizes are in
the target's own device units, and printing.py hands the printer a context
already scaled so that one device unit is one nominal 96dpi pixel.
"""
import gi
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo

import cairo

# Body text in a diagram, in nominal 96dpi pixels. Slightly under the
# 16px body size (zoom.py's 12pt), because a diagram carries many short
# strings at once and reads better a little tighter than prose.
FONT_PX = 14.0

# Longest a single label line gets before it wraps, in characters. Mermaid
# wraps node text too; without a cap one long label stretches its rank
# across the page and squashes everything else.
WRAP_CHARS = 28

_scratch_context = None


def scratch_context():
    """A Pango context to measure against, with no window or printer
    behind it. One per process: building it costs an image surface, and
    nothing about it varies per diagram."""
    global _scratch_context
    if _scratch_context is None:
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)
        _scratch_context = PangoCairo.create_context(cairo.Context(surface))
    return _scratch_context


def font_for(base, font_px, *, bold=False, italic=False, mono=False):
    desc = base.copy() if base is not None else Pango.FontDescription.new()
    if mono:
        desc.set_family("monospace")
    desc.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
    desc.set_style(Pango.Style.ITALIC if italic else Pango.Style.NORMAL)
    desc.set_absolute_size(font_px * Pango.SCALE)
    return desc


def make_layout(context, base_font, font_px, text, *, bold=False, italic=False,
                mono=False, max_px=None, width=None, align="center"):
    layout = Pango.Layout(context)
    layout.set_font_description(font_for(base_font, font_px, bold=bold, italic=italic, mono=mono))
    layout.set_text(text, -1)
    if width is not None:
        layout.set_width(int(width * Pango.SCALE))
        layout.set_alignment({
            "center": Pango.Alignment.CENTER,
            "right": Pango.Alignment.RIGHT,
        }.get(align, Pango.Alignment.LEFT))
    elif max_px is not None:
        layout.set_width(int(max_px * Pango.SCALE))
    if width is not None or max_px is not None:
        layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    return layout


class Measurer:
    """Measures label text for one diagram, at one font."""

    def __init__(self, base_font, font_px=FONT_PX):
        self.base_font = base_font
        self.font_px = font_px
        self._context = scratch_context()
        self._cache = {}
        # Width of WRAP_CHARS average characters, from the font's own
        # metrics rather than a guess -- "28 characters" is meaningless in
        # pixels until the font says how wide its characters are, and that
        # differs by a third between the narrow GNOME UI font and DejaVu.
        metrics = self._context.get_metrics(font_for(base_font, font_px), None)
        self.char_px = metrics.get_approximate_char_width() / Pango.SCALE
        self.wrap_px = self.char_px * WRAP_CHARS
        self.line_px = (metrics.get_ascent() + metrics.get_descent()) / Pango.SCALE

    def size(self, text, *, bold=False, italic=False, mono=False, wrap=True, scale=1.0):
        """(width, height) of `text` as it will be drawn. Empty text still
        gets one line's height, so an unlabelled node isn't a sliver.

        `scale` must match the scene.Text's own -- it is the size the
        string is measured *and* drawn at, not a post-hoc adjustment.
        """
        key = (text, bold, italic, mono, wrap, scale)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        layout = make_layout(
            self._context, self.base_font, self.font_px * scale, text,
            bold=bold, italic=italic, mono=mono,
            max_px=self.wrap_px if wrap else None,
        )
        width, height = layout.get_pixel_size()
        size = (float(width), float(max(height, self.line_px * scale)))
        self._cache[key] = size
        return size
