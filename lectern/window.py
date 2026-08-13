"""One LecternWindow per opened file -- Evince/Papers-style: no sidebar, no
editing surface, minimal headerbar, a top find-bar revealer and a floating
bottom-right zoom pill (the same idiom Papers/Loupe use for zoom controls).
"""
import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gio, GLib, Gdk

from . import tags as tagdefs
from .document import Document, DocumentLoadError
from .renderer import MarkdownRenderer
from .findbar import FindController
from .zoom import ZoomController
from .filewatch import FileWatcher
from .printing import PrintCoordinator
from .decorated_textview import DecoratedTextView


class LecternWindow(Adw.ApplicationWindow):
    def __init__(self, application, gfile=None):
        super().__init__(application=application, default_width=760, default_height=900)
        self._document = None
        self._renderer = None
        self._watcher = None
        self._style_manager = Adw.StyleManager.get_default()
        self._dark_handler_id = self._style_manager.connect("notify::dark", self._on_dark_changed)

        self._build_ui()
        self._install_actions()
        self._sync_window_title()

        if gfile is not None:
            self._open_file(gfile)
        else:
            self._show_empty_state()

    # -- UI construction --------------------------------------------------

    def _build_ui(self):
        self._toast_overlay = Adw.ToastOverlay()
        self.set_content(self._toast_overlay)

        self._toolbar_view = Adw.ToolbarView()
        self._toast_overlay.set_child(self._toolbar_view)
        self._toolbar_view.add_top_bar(self._build_headerbar())

        # Remote images are not fetched on open -- opening a document
        # shouldn't tell whoever hosts its images that you opened it, and
        # a per-image URL is a serviceable read receipt. This banner is
        # the opt-in, per document.
        self._remote_images_banner = Adw.Banner(button_label="Load")
        self._remote_images_banner.connect("button-clicked", self._on_load_remote_images)
        self._toolbar_view.add_top_bar(self._remote_images_banner)

        self._content_overlay = Gtk.Overlay()
        self._toolbar_view.set_content(self._content_overlay)

        self._build_textview()
        self._content_overlay.set_child(self._scrolled)
        self._content_overlay.add_overlay(self._build_findbar_widget())
        self._content_overlay.add_overlay(self._build_zoom_widget())

    def _build_headerbar(self):
        header = Adw.HeaderBar()
        self._window_title = Adw.WindowTitle(title="Lectern")
        header.set_title_widget(self._window_title)

        self._find_toggle = Gtk.ToggleButton(icon_name="edit-find-symbolic", tooltip_text="Find")
        self._find_toggle.connect("toggled", self._on_find_toggled)
        header.pack_start(self._find_toggle)

        menu = Gio.Menu()
        menu.append("Print…", "win.print-doc")
        menu.append("Document Properties", "win.properties")
        menu.append("Keyboard Shortcuts", "win.show-help-overlay")
        menu.append("About Lectern", "app.about")
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", tooltip_text="Main Menu")
        menu_button.set_menu_model(menu)
        header.pack_end(menu_button)
        return header

    def _build_textview(self):
        # A plain, untagged buffer for now -- real content (and the full,
        # ~30-tag style table) only gets built in _render_document, once a
        # file is actually being opened. Building the real tag table here
        # unconditionally would be pure wasted startup work for the (very
        # common) case where _render_document replaces it immediately
        # after, or the empty-state page replaces this view entirely.
        # No margins passed: the view sets its own four, since where the
        # content column starts depends on how wide the view turns out to
        # be (it centres a capped text column) and on the zoom level.
        self._textview = DecoratedTextView(
            editable=False, cursor_visible=False,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
        )
        self._textview.add_css_class("lectern-content")

        click = Gtk.GestureClick()
        click.connect("released", self._on_textview_click)
        self._textview.add_controller(click)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_textview_motion)
        self._textview.add_controller(motion)

        self._scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC, vscrollbar_policy=Gtk.PolicyType.AUTOMATIC
        )
        self._scrolled.set_child(self._textview)

        # Gtk.TextView never stretches an anchored child widget to fill
        # the line -- hexpand/halign on the widget itself are silently
        # ignored, confirmed empirically -- so table frames and hr
        # separators (Lectern's only two anchored widget kinds) would
        # otherwise sit at their own tiny natural width forever, in a sea
        # of unused space, and never react to the window being resized.
        # The adjustment's page-size is the one reliable, signal-based way
        # to observe the view's actual content width changing (Gtk.Widget
        # itself has no public size-change signal in GTK4); connecting
        # here, before the window is ever presented, also catches the
        # initial layout pass, so newly opened documents get correctly
        # sized tables immediately, not just after the first resize.
        self._fill_width_widgets = []
        self._images = []
        self._textview.get_hadjustment().connect("notify::page-size", self._on_content_width_changed)

        self._zoom = ZoomController(self._textview)
        self._find = FindController(self._textview)

        scroll_ctrl = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll_ctrl.connect("scroll", self._on_ctrl_scroll)
        self._scrolled.add_controller(scroll_ctrl)

    def _build_findbar_widget(self):
        self._search_entry = Gtk.SearchEntry(placeholder_text="Find in document")
        self._search_entry.set_hexpand(True)
        self._search_entry.connect("search-changed", self._on_search_changed)
        self._search_entry.connect("activate", self._on_find_activate)
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_find_key)
        self._search_entry.add_controller(key_ctrl)

        self._find_word = Gtk.ToggleButton(label="Whole word", tooltip_text="Match whole word only")
        self._find_word.connect("toggled", self._on_find_options_changed)
        self._find_case = Gtk.ToggleButton(label="Case", tooltip_text="Case sensitive")
        self._find_case.connect("toggled", self._on_find_options_changed)

        prev_btn = Gtk.Button(icon_name="go-up-symbolic", tooltip_text="Previous match")
        prev_btn.connect("clicked", lambda b: self._advance_find(-1))
        next_btn = Gtk.Button(icon_name="go-down-symbolic", tooltip_text="Next match")
        next_btn.connect("clicked", lambda b: self._advance_find(1))

        self._find_label = Gtk.Label(label="")
        self._find_label.add_css_class("dim-label")

        close_btn = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Close")
        close_btn.connect("clicked", lambda b: self._find_toggle.set_active(False))

        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for widget in (self._search_entry, self._find_word, self._find_case,
                       prev_btn, next_btn, self._find_label, close_btn):
            inner.append(widget)
        inner.set_margin_top(6)
        inner.set_margin_bottom(6)
        inner.set_margin_start(6)
        inner.set_margin_end(6)
        inner.add_css_class("toolbar")
        inner.add_css_class("osd")

        self._find_revealer = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN)
        self._find_revealer.set_child(inner)
        self._find_revealer.set_valign(Gtk.Align.START)
        self._find_revealer.set_halign(Gtk.Align.FILL)
        return self._find_revealer

    def _build_zoom_widget(self):
        zoom_out_btn = Gtk.Button(icon_name="zoom-out-symbolic", tooltip_text="Zoom out")
        zoom_out_btn.connect("clicked", lambda b: self._zoom.zoom_out())
        zoom_in_btn = Gtk.Button(icon_name="zoom-in-symbolic", tooltip_text="Zoom in")
        zoom_in_btn.connect("clicked", lambda b: self._zoom.zoom_in())
        self._zoom_label = Gtk.Label(label="100%")
        self._zoom_label.set_width_chars(5)
        self._zoom.connect("changed", self._on_zoom_changed)

        pill = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        for widget in (zoom_out_btn, self._zoom_label, zoom_in_btn):
            pill.append(widget)
        pill.set_margin_top(6)
        pill.set_margin_bottom(6)
        pill.set_margin_start(8)
        pill.set_margin_end(8)
        pill.add_css_class("osd")
        pill.add_css_class("toolbar")

        # Always-on-screen, it would permanently cover whatever content
        # happens to scroll under this corner -- most noticeably the
        # last few lines once scrolled to the document's end, since the
        # overlay sits outside the ScrolledWindow's own notion of
        # "content" entirely. Auto-hide after a moment of inactivity
        # instead, matching the OSD convention GNOME uses for floating
        # transient controls (volume/brightness, video player chrome):
        # visible right after a zoom change or while the pointer is over
        # it, gone otherwise.
        self._zoom_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.CROSSFADE, reveal_child=False
        )
        self._zoom_hide_source_id = 0
        hover = Gtk.EventControllerMotion()
        hover.connect("enter", lambda *a: self._flash_zoom_osd())
        pill.add_controller(hover)
        self._zoom_revealer.set_child(pill)

        wrapper = Gtk.Box(halign=Gtk.Align.END, valign=Gtk.Align.END)
        wrapper.set_margin_end(16)
        wrapper.set_margin_bottom(16)
        wrapper.append(self._zoom_revealer)
        return wrapper

    def _flash_zoom_osd(self):
        self._zoom_revealer.set_reveal_child(True)
        if self._zoom_hide_source_id:
            GLib.source_remove(self._zoom_hide_source_id)
        self._zoom_hide_source_id = GLib.timeout_add(1500, self._hide_zoom_osd)

    def _hide_zoom_osd(self):
        self._zoom_hide_source_id = 0
        self._zoom_revealer.set_reveal_child(False)
        return GLib.SOURCE_REMOVE

    # -- actions / shortcuts -----------------------------------------------

    def _install_actions(self):
        actions = [
            ("find", self._action_find),
            ("zoom-in", lambda a, p: self._zoom.zoom_in()),
            ("zoom-out", lambda a, p: self._zoom.zoom_out()),
            ("zoom-reset", lambda a, p: self._zoom.zoom_reset()),
            ("print-doc", self._action_print),
            ("properties", self._action_properties),
        ]
        for name, handler in actions:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)

    def _action_find(self, action, param):
        self._find_toggle.set_active(True)

    def _action_print(self, action, param):
        if self._renderer is None:
            return
        coordinator = PrintCoordinator()
        title = self._document.basename if self._document else "document"
        coordinator.print_document(self, self._renderer.print_model, self._style_manager.get_dark(), title)

    def _action_properties(self, action, param):
        if self._document is None:
            return
        dialog = Adw.AlertDialog(
            heading="Document Properties",
            body=(
                f"Name: {self._document.basename}\n"
                f"Location: {self._document.parent_path or '—'}\n"
                f"Size: {self._document.size_bytes():,} bytes\n"
                f"Words: {self._document.word_count():,}\n"
                f"Est. reading time: {self._document.reading_time_minutes()} min"
            ),
        )
        dialog.add_response("ok", "OK")
        dialog.present(self)

    # -- find bar wiring -----------------------------------------------

    def _on_find_toggled(self, button):
        active = button.get_active()
        self._find_revealer.set_reveal_child(active)
        if active:
            self._search_entry.grab_focus()
        else:
            self._find.clear()
            self._sync_find_label()
            self._textview.grab_focus()

    def _on_search_changed(self, entry):
        self._find.search(entry.get_text())
        self._sync_find_label()

    def _on_find_options_changed(self, button):
        self._find.case_sensitive = self._find_case.get_active()
        self._find.whole_word = self._find_word.get_active()
        self._find.search(self._search_entry.get_text())
        self._sync_find_label()

    def _on_find_activate(self, entry):
        self._advance_find(1)

    def _on_find_key(self, controller, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self._find_toggle.set_active(False)
            return True
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        if keyval == Gdk.KEY_Return and shift:
            self._advance_find(-1)
            return True
        return False

    def _advance_find(self, direction):
        self._find.advance(direction)
        self._sync_find_label()

    def _sync_find_label(self):
        count = self._find.match_count
        text = f"{self._find.current_position} / {count}" if count else "No matches"
        self._find_label.set_text(text if self._search_entry.get_text() else "")

    # -- remote images ----------------------------------------------------

    def _sync_remote_images_banner(self):
        pending = [img for img in self._images if img.remote]
        self._remote_images_banner.set_revealed(bool(pending))
        if pending:
            count = len(pending)
            noun = "image" if count == 1 else "images"
            self._remote_images_banner.set_title(
                f"This document contains {count} remote {noun}, not loaded"
            )

    def _on_load_remote_images(self, banner):
        for image in self._images:
            image.load_remote()
        self._remote_images_banner.set_revealed(False)

    # -- anchored-widget width sync ---------------------------------------

    def _on_content_width_changed(self, hadjustment, pspec):
        self._sync_fill_width_widgets()

    def _sync_fill_width_widgets(self):
        # page-size is the raw viewport width, not reduced by the
        # TextView's own left/right margins (confirmed empirically -- it
        # does NOT subtract them), so that has to happen here to match
        # the width normal text actually wraps to.
        usable = (
            self._textview.get_hadjustment().get_page_size()
            - self._textview.get_left_margin()
            - self._textview.get_right_margin()
        )
        if usable <= 0:
            return  # not yet laid out
        for widget in self._fill_width_widgets:
            widget.set_size_request(round(usable), -1)
        # Images take the same number as a ceiling to scale down to,
        # rather than a width to fill -- see ImageView._apply_size.
        for image in self._images:
            image.set_available_width(round(usable))

    # -- zoom ------------------------------------------------------------

    def _on_zoom_changed(self, controller, factor):
        self._zoom_label.set_text(f"{round(factor * 100)}%")
        # Zooming moves the view's margins (they scale like everything else)
        # without the viewport itself changing size, so the page-size signal
        # the width sync normally rides on never fires -- anchored tables and
        # separators would keep the previous zoom's width until the next
        # window resize.
        self._sync_fill_width_widgets()
        self._flash_zoom_osd()

    def _on_ctrl_scroll(self, controller, dx, dy):
        state = controller.get_current_event_state()
        if not (state & Gdk.ModifierType.CONTROL_MASK):
            return False
        if dy < 0:
            self._zoom.zoom_in()
        elif dy > 0:
            self._zoom.zoom_out()
        return True

    # -- link / footnote click dispatch -----------------------------------

    def _iter_at_widget_xy(self, x, y):
        """Widget-relative (x, y) -> Gtk.TextIter at that position, or None."""
        bx, by = self._textview.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y))
        found, it = self._textview.get_iter_at_location(bx, by)
        return it if found else None

    def _on_textview_click(self, gesture, n_press, x, y):
        if self._renderer is None:
            return
        it = self._iter_at_widget_xy(x, y)
        target = self._renderer.target_at_iter(it) if it is not None else None
        if target is not None:
            self._activate_target(target)

    def _on_textview_motion(self, controller, x, y):
        target = None
        if self._renderer is not None:
            it = self._iter_at_widget_xy(x, y)
            target = self._renderer.target_at_iter(it) if it is not None else None
        self._textview.set_cursor_from_name("pointer" if target is not None else "text")

    def _open_href(self, href):
        """Open a link/table-cell href, resolving it against the open
        document's own directory first if it has no URI scheme -- a bare
        Gio.AppInfo.launch_default_for_uri("../app/foo/") fails outright
        (GLib.Error: g-io-error-quark: Operation not supported), since
        that's not a URI at all, just a relative filesystem path. This is
        the common case for documentation that cross-links to files
        sitting next to it, e.g. this app's own tests/fixtures/*.md.
        A resolved link to another .md file opens in Lectern itself,
        for free, since we're the registered default handler for
        text/markdown -- no special-casing needed here.
        """
        if not href or GLib.uri_parse_scheme(href) is not None:
            target_uri = href
        else:
            base_dir = self._document.gfile.get_parent() if self._document else None
            target_uri = base_dir.resolve_relative_path(href).get_uri() if base_dir else None
        if not target_uri:
            return
        try:
            Gio.AppInfo.launch_default_for_uri(target_uri, None)
        except GLib.Error:
            self._toast_overlay.add_toast(Adw.Toast(title="Couldn’t open link", timeout=3))

    def _on_table_link_activated(self, label, uri):
        self._open_href(uri)
        return True  # stop Gtk.Label's own default handling

    def _activate_target(self, target):
        kind = target["type"]
        if kind == "url":
            self._open_href(target["href"])
        elif kind == "footnote-jump":
            mark_name = self._renderer.footnote_def_mark_name(target["label"])
            self._scroll_to_mark_name(mark_name)
        elif kind == "footnote-back":
            mark_name = self._renderer.footnote_ref_mark_name(target["label"])
            self._scroll_to_mark_name(mark_name)

    def _scroll_to_mark_name(self, mark_name):
        if not mark_name:
            return
        buffer = self._textview.get_buffer()
        mark = buffer.get_mark(mark_name)
        if mark is not None:
            self._textview.scroll_to_mark(mark, 0.1, True, 0.0, 0.1)

    # -- document loading / reload -----------------------------------------

    def _show_empty_state(self):
        status = Adw.StatusPage(
            title="No Document",
            description="Open a Markdown file to view it here.",
            icon_name="text-x-generic-symbolic",
        )
        open_button = Gtk.Button(label="Open File…", halign=Gtk.Align.CENTER)
        open_button.add_css_class("suggested-action")
        open_button.add_css_class("pill")
        open_button.connect("clicked", self._on_open_clicked)
        status.set_child(open_button)
        self._content_overlay.set_child(status)

    def _on_open_clicked(self, button):
        dialog = Gtk.FileDialog(title="Open Markdown File")
        filter_md = Gtk.FileFilter(name="Markdown files")
        filter_md.add_pattern("*.md")
        filter_md.add_pattern("*.markdown")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filter_md)
        dialog.set_filters(filters)
        dialog.open(self, None, self._on_file_chosen)

    def _on_file_chosen(self, dialog, result):
        try:
            gfile = dialog.open_finish(result)
        except GLib.Error:
            return
        if gfile is not None:
            self.get_application().open([gfile], "")

    def _open_file(self, gfile):
        self._document = Document(gfile)
        try:
            self._document.load()
        except DocumentLoadError as ex:
            self._show_load_error(str(ex))
            return
        self._content_overlay.set_child(self._scrolled)
        self._window_title.set_title(self._document.basename)
        self._window_title.set_subtitle(self._document.parent_path or "")
        self._render_document()
        self._watcher = FileWatcher(gfile)
        self._watcher.connect("reload-needed", self._on_reload_needed)
        self._watcher.connect("file-missing", self._on_file_missing)

    def _sync_window_title(self):
        """Set Gtk.Window's own title, which is what the shell's window
        list and Alt-Tab read.

        The headerbar's Adw.WindowTitle is a *widget* and setting it does
        nothing for the window property, so before this every window
        reported no title at all and the shell fell back to the
        application name -- leaving several open documents all listed
        identically as "Lectern".

        No " - Lectern" suffix: the GNOME HIG wants a window titled after
        its document, and the shell already shows which application the
        window belongs to.
        """
        if self._document is None:
            self.set_title("Lectern")
            return
        self.set_title(self._document.title or self._document.basename)

    def _show_load_error(self, message):
        status = Adw.StatusPage(
            title="Couldn’t Open File", description=message, icon_name="dialog-error-symbolic"
        )
        self._content_overlay.set_child(status)

    def _render_document(self):
        dark = self._style_manager.get_dark()
        buffer = Gtk.TextBuffer(tag_table=tagdefs.create_tag_table(dark))
        self._renderer = MarkdownRenderer()
        base_dir = self._document.gfile.get_parent() if self._document else None
        self._renderer.render(self._document.tree, buffer, dark=dark, base_dir=base_dir)
        self._textview.set_buffer(buffer)
        self._images = self._renderer.images
        self._fill_width_widgets = self._renderer.attach_pending_widgets(self._textview)
        self._sync_fill_width_widgets()
        self._sync_remote_images_banner()
        # Here rather than in _open_file, so a reload that changed the
        # document's first heading retitles the window too.
        self._sync_window_title()
        for label in self._renderer.table_link_labels:
            label.connect("activate-link", self._on_table_link_activated)
        self._find = FindController(self._textview, self._renderer.tables)
        self._sync_find_label()

    def _on_reload_needed(self, watcher):
        adjustment = self._scrolled.get_vadjustment()
        max_scroll = max(adjustment.get_upper() - adjustment.get_page_size(), 1.0)
        scroll_fraction = adjustment.get_value() / max_scroll
        had_query = self._search_entry.get_text() if self._find_toggle.get_active() else None

        try:
            self._document.reload()
        except DocumentLoadError:
            return  # keep showing the last-good content
        self._render_document()

        def restore_scroll():
            adj = self._scrolled.get_vadjustment()
            new_max = max(adj.get_upper() - adj.get_page_size(), 1.0)
            adj.set_value(scroll_fraction * new_max)
            if had_query:
                self._find.search(had_query)
                self._sync_find_label()
            return GLib.SOURCE_REMOVE

        GLib.idle_add(restore_scroll)
        self._toast_overlay.add_toast(Adw.Toast(title="Reloaded", timeout=2))

    def _on_file_missing(self, watcher):
        self._toast_overlay.add_toast(Adw.Toast(title="File no longer available", timeout=3))

    def _on_dark_changed(self, style_manager, pspec):
        if self._textview.get_buffer() is not None:
            tagdefs.update_tag_colors(self._textview.get_buffer().get_tag_table(), style_manager.get_dark())

    def do_close_request(self):
        if self._watcher is not None:
            self._watcher.close()
        self._zoom.close()
        # Adw.StyleManager is a process-wide singleton that outlives every
        # window -- without this, each closed window's bound-method
        # callback (and everything it closes over: buffer, renderer,
        # document...) would stay reachable forever.
        self._style_manager.disconnect(self._dark_handler_id)
        return False
