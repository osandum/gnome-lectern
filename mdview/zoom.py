"""CSS-font-size-based zoom. Heading/code/footnote tags carry static,
never-recomputed `scale` values (see tags.py) that GTK composes on top of
whatever font-size this controller sets -- so zooming never touches the
tag table, just one Gtk.CssProvider rule."""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk, Gdk, GObject

STEPS = [0.5, 0.67, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 3.0, 4.0]
BASE_PT = 11.0
DEFAULT_INDEX = STEPS.index(1.0)

# Unique per-instance CSS class so each window's provider only ever
# addresses its own TextView, even though the provider is registered at
# the display level (the modern, non-deprecated way to scope a
# Gtk.CssProvider -- per-widget StyleContext.add_provider is deprecated).
_next_instance_id = 0


class ZoomController(GObject.Object):
    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_FIRST, None, (float,))}

    def __init__(self, textview):
        super().__init__()
        global _next_instance_id
        _next_instance_id += 1
        self.textview = textview
        self._css_class = f"mdview-zoom-{_next_instance_id}"
        textview.add_css_class(self._css_class)
        self._provider = Gtk.CssProvider()
        self._display = textview.get_display() or Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(
            self._display, self._provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._index = DEFAULT_INDEX
        self._apply()

    @property
    def factor(self):
        return STEPS[self._index]

    def _apply(self):
        css = f"textview.{self._css_class} {{ font-size: {BASE_PT * self.factor:.1f}pt; }}"
        self._provider.load_from_string(css)
        self.emit("changed", self.factor)

    def zoom_in(self):
        if self._index < len(STEPS) - 1:
            self._index += 1
            self._apply()

    def zoom_out(self):
        if self._index > 0:
            self._index -= 1
            self._apply()

    def zoom_reset(self):
        if self._index != DEFAULT_INDEX:
            self._index = DEFAULT_INDEX
            self._apply()

    def close(self):
        """Must be called when the owning window closes -- display-level
        CSS providers otherwise accumulate for the lifetime of the process,
        one per window ever opened."""
        Gtk.StyleContext.remove_provider_for_display(self._display, self._provider)
