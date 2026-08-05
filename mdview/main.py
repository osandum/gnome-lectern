"""CLI entry point. Imports of markdown_it/pygments deliberately stay
inside document.py/highlighting.py, not here -- a bare `mdview` launch or
an argument error shouldn't pay for parsing/highlighting machinery it
never uses.
"""
import sys

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio

from . import __version__
from .window import MdViewWindow

APPLICATION_ID = "io.github.osandum.Mdview"


class MdViewApplication(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APPLICATION_ID, flags=Gio.ApplicationFlags.HANDLES_OPEN)
        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self._on_about)
        self.add_action(about_action)

    def do_startup(self):
        Adw.Application.do_startup(self)
        # Accels are application-scoped state (win.* actions are resolved
        # against whichever window has focus at trigger time) -- set once
        # per process here, not once per window in MdViewWindow.__init__.
        self.set_accels_for_action("win.find", ["<primary>f"])
        self.set_accels_for_action("win.zoom-in", ["<primary>plus", "<primary>equal", "<primary>KP_Add"])
        self.set_accels_for_action("win.zoom-out", ["<primary>minus", "<primary>KP_Subtract"])
        self.set_accels_for_action("win.zoom-reset", ["<primary>0"])
        self.set_accels_for_action("win.print-doc", ["<primary>p"])

    def do_open(self, files, n_files, hint):
        # One window per file, including when this arrives at an
        # already-running instance via GApplication's default D-Bus
        # activation -- no window-reuse logic, by design (Papers/Evince
        # model: a second file always gets a second window).
        for gfile in files:
            win = MdViewWindow(application=self, gfile=gfile)
            win.present()

    def do_activate(self):
        win = MdViewWindow(application=self)
        win.present()

    def _on_about(self, action, param):
        about = Adw.AboutDialog(
            application_name="mdview",
            application_icon="text-x-generic-symbolic",
            version=__version__,
            developer_name="mdview contributors",
            license_type=Gtk.License.GPL_2_0,
            comments="A read-only, Papers-style Markdown viewer.",
            website="https://github.com/osandum/gnome-mdview",
        )
        about.present(self.get_active_window())


def main():
    app = MdViewApplication()
    try:
        return app.run(sys.argv)
    except KeyboardInterrupt:
        # PyGObject's own SIGINT-fallback (gi/_ossighelper.py) lets the
        # GLib main loop quit cleanly on Ctrl-C, then re-raises
        # KeyboardInterrupt out of app.run() so callers can decide what to
        # do with it -- a bare CLI traceback isn't the right answer here.
        return 130  # conventional exit code for SIGINT-terminated processes


if __name__ == "__main__":
    sys.exit(main())
