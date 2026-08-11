"""The widget one mermaid fence is anchored into.

Like images.ImageView, and unlike tables, a diagram takes the view's width
as a *ceiling* rather than something to fill: it has a natural size, is
scaled down to fit a narrow window, and is never blown up past that size.
"""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw

from .. import tags as tagdefs
from . import draw as drawlib

MARGIN = 6


class DiagramView(Gtk.DrawingArea):
    def __init__(self, scene, base_font):
        super().__init__()
        self._scene = scene
        self._base_font = base_font
        self._available_width = 0
        self._zoom = 1.0
        self.set_halign(Gtk.Align.START)
        self.set_margin_top(MARGIN)
        self.set_margin_bottom(MARGIN)
        self.set_draw_func(self._draw)
        self._apply_size()

    # -- public API, mirroring images.ImageView ---------------------------

    def set_available_width(self, width):
        self._available_width = width
        self._apply_size()

    def set_zoom(self, factor):
        """Follow the window's zoom, so a diagram grows with the text it
        sits among instead of staying a fixed island in a zoomed page.
        Scaling the cairo context (rather than re-laying the scene out)
        keeps the text crisp: Pango re-renders at the scaled size."""
        self._zoom = factor
        self._apply_size()

    @property
    def scene(self):
        return self._scene

    # -- internals ---------------------------------------------------------

    @property
    def _scale(self):
        scale = self._zoom
        if self._available_width > 0 and self._scene.width > 0:
            scale = min(scale, self._available_width / self._scene.width)
        return scale

    def _apply_size(self):
        scale = self._scale
        self.set_content_width(max(1, round(self._scene.width * scale)))
        self.set_content_height(max(1, round(self._scene.height * scale)))
        self.queue_draw()

    def _draw(self, area, cr, width, height, *_args):
        # The palette is read at draw time rather than held, so a
        # light/dark flip repaints correctly with no plumbing -- the same
        # thing decorated_textview.py does for its heading rules.
        palette = tagdefs.diagram_palette(Adw.StyleManager.get_default().get_dark())
        scale = self._scale
        cr.scale(scale, scale)
        drawlib.draw_scene(cr, self._scene, palette, self._base_font)
