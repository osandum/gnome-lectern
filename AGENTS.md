# AGENTS.md

Guidance for coding agents working in this repository.

## What this is

Lectern: a read-only, [Papers](https://apps.gnome.org/da/Papers/)-style Markdown viewer for GNOME. Open a `.md` file, read/search/zoom/print it — no editing UI. One window per opened file, no sidebar (deliberate departures/similarities to Papers/Evince, not accidents).

## Commands

```sh
# Setup (once) -- --system-site-packages is required: PyGObject/GTK4/Libadwaita
# come from system packages, not pip, and the venv needs to see them.
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
pip install -e .

# Run
lectern path/to/file.md

# Test (headless, no display needed -- see Architecture below)
pytest tests/
pytest tests/test_renderer.py::test_bold_produces_strong_tag  # single test

# Manual smoke test fixture exercising every Markdown feature:
lectern tests/fixtures/kitchen_sink.md
```

There is no linter or formatter configured in this repo.

## Architecture

**Rendering is native GTK widgets, not HTML.** A single `Gtk.TextView` + `Gtk.TextBuffer` with `Gtk.TextTag`s does all inline/block formatting. This was a deliberate choice over embedding WebKitGTK — benchmarked and rejected because loading even trivial HTML in WebKit cost 300ms+, which conflicts with the "feels instant" launch goal. The only widgets embedded in the buffer (via `Gtk.TextChildAnchor`) are table grids and `hr` separators.

**Critical, non-obvious GTK behavior:** `Gtk.TextView` never stretches an anchored child widget to fill the line — `hexpand`/`halign` on the widget are silently ignored, no matter how they're set. This bit us once already (tables/hr rendering narrow and non-responsive to resize). The fix, and the pattern to follow for any future anchored widget: track it and actively push a width onto it via `window.py`'s `_sync_fill_width_widgets`, driven by the `TextView`'s `hadjustment` `page-size` (the one reliable signal for "the view's content width changed" — `Gtk.Widget` has no public size-change signal in GTK4).

**Module responsibilities:**
- `document.py` — file I/O + Markdown parsing (`markdown-it-py` + `mdit-py-plugins` for GFM tables/strikethrough/footnotes/tasklists). Imports of `markdown_it`/`pygments` are deliberately deferred into function bodies here and in `highlighting.py`, not at module top level — a bare `lectern` launch or an argument error shouldn't pay for parsing/highlighting machinery it never uses.
- `renderer.py` — walks the `markdown_it.tree.SyntaxTreeNode` tree into the `Gtk.TextBuffer`. Every block-level emission *also* appends a `PrintItem` to `self.print_model` in the same pass. This dual-write exists because `Gtk.TextChildAnchor`-embedded content (tables) is invisible to buffer-native search and can't be reconstructed by re-walking the buffer afterward (it's a single object-replacement character in the text stream) — `printing.py` works off `print_model` exclusively, never off the live buffer.
- `tags.py` — the single source of truth for `Gtk.TextTag` style properties (`tag_style_props`), consumed by both the live buffer (screen) and `printing.py` (paper), so the two can't visually drift apart. Also owns light/dark palettes and re-colors tags in place on theme change.
- `decorated_textview.py` — the `Gtk.TextView` subclass the window actually uses. `Gtk.TextTag` cannot draw a *box*: `background-rgba` hugs the glyph runs with no padding or corner radius, `paragraph-background-rgba` adds no vertical padding either, and there is no border property at all. So the h1/h2 bottom rule, the inline-code chip and the fenced-code panel are painted in `snapshot()` with Cairo, under the text, from rectangles found via `get_iter_location()`. Two things to know before touching it: **`Gtk.TextIter` is mutable and `forward_to_tag_toggle()` edits it in place** (handing the walk's own iter out as a range end once made every chip grow to swallow the rest of the document — hence the `.copy()` calls and the regression tests), and **drawing outside the text box does not create the space it needs** — the padding constants in `tags.py` are paired with layout that reserves room for them (`heading{1,2}`'s `pixels-below-lines`, and `renderer.py`'s `_BLOCK_PADDING` for fences).
- `tables.py` — table subtree → `Gtk.Grid` (screen) + row-text extraction (print). Column widths are weighted by *median* character length per column (`column_char_weights`), shared by both the on-screen `Gtk.Label.max_width_chars` hint and print's proportional pt-width split — one heuristic, not two independently-tuned ones. Median, not mean/max, so one outlier cell doesn't blow out its column.
- `highlighting.py` — Pygments wrapper mapping token types to a small fixed set of `pyg-*` tag names (defined in `tags.py`).
- `images.py` — one `ImageView` per `![alt](src)`, anchored into the buffer like tables. **Remote (`http(s)`) images are deliberately not fetched on open** — that would let whoever hosts an image learn you opened the document, with a per-image URL working as a read receipt; `window.py` raises an `Adw.Banner` and only then calls `load_remote()`. Local paths resolve against the document's directory, the same base `_open_href` uses. Loading can't cascade: raster formats are self-contained and librsvg (verified on 2.60) drops *every* external reference — remote and local `href`, CSS `@import`, `<use>`, `@font-face`, `xi:include`. A Flatpak without `--share=network` is the real backstop for that, not the note. Images are excluded from `_fill_width_widgets`: they take the available width as a ceiling to scale down to, never a width to fill, and are never upscaled past their own pixels.
- `findbar.py` — `Gtk.TextIter`-based search over the buffer, *plus* a parallel scan of the table cells (whose text never enters the buffer). Cell matches are highlighted by re-rendering the cell's markup with background spans baked in, **not** via `Gtk.Label.set_attributes` — GTK merges those into the label's cached `PangoLayout` rather than replacing them, so a shrinking match set (backspacing in the find bar) leaves stale highlights painted. Both kinds of match merge into one list ordered by document position, so Next/Previous and the counter don't expose the split.
- `zoom.py` — one `Gtk.CssProvider` per window scoped via a unique CSS class (not per-widget `StyleContext.add_provider`, which is deprecated), stepping a font-size on the `TextView`. Heading/code tags carry static `Gtk.TextTag.scale` values that GTK composes on top automatically — zooming never touches the tag table.
- `printing.py` — `Gtk.PrintOperation` pagination built from `print_model`, walking Pango layouts page-by-page. `Pango.AttrList` offsets are UTF-8 *byte* offsets, not character offsets — run-boundary byte lengths are computed explicitly, not assumed to match `len(text)`. `Pango.LayoutLine` keeps only a weak reference to its parent `Pango.Layout`, so the built `_Block` list must be kept alive in `state` between `begin-print` and `draw-page`, not just its derived page geometry.
- `filewatch.py` — `Gio.FileMonitor` with `WATCH_MOVES` (needed because editors that save via write-temp-then-rename otherwise look like the file vanishing) and a 300ms debounce coalescing the burst of events one logical save produces.
- `window.py` — one `LecternWindow` per file; owns the widget tree, find-bar/zoom UI, click dispatch for links/footnotes (via `renderer.target_at_iter`), reload handling (preserves scroll position as a fraction, not an exact mark, since content may reshuffle), and the anchored-widget width sync described above. **The headerbar's `Adw.WindowTitle` is a widget, not the window's title** — setting it does nothing for `Gtk.Window`'s own `title` property, which is what the shell's window list and Alt-Tab read. `_sync_window_title` sets that separately (document's first h1, else the filename); without it every window reported no title and the shell listed them all identically as the application name. It's called from `_render_document`, so a reload that changed the heading retitles the window too.
- `main.py` — `Adw.Application` with `HANDLES_OPEN`. `do_open` creates one window per file, including on repeat launches while already running (GApplication's default D-Bus single-process activation still produces a new window, not a reused one — this is intentional, not an oversight). App-scoped accelerators (`window.close`, `app.quit`, zoom, find, print) are bound once in `do_startup`, not per-window — binding them per-`LecternWindow.__init__` would redundantly re-apply identical accelerator maps for every window opened.

**Testing:** the tests never realize or show a window, but they **do need a display** — `Gtk.Grid()` construction segfaults outright when there is none, which is why CI runs them under `xvfb-run -a`. On a desktop they just work, so this only bites in containers and CI.

**Do not "verify" headlessness by unsetting `DISPLAY` and `WAYLAND_DISPLAY`.** GDK falls back to the default `wayland-0` socket in `XDG_RUNTIME_DIR` and connects anyway, so the suite passes and looks headless while still talking to the compositor. This exact mistake led to CI being written without xvfb and segfaulting on both distros. To test it properly, also point `XDG_RUNTIME_DIR` at an empty directory.

Tests assert tag placement on parsed-and-rendered buffers (e.g., parse `"hello **world**"`, locate `"world"`, assert it has the `strong` tag) — this is the pattern to follow for new renderer coverage rather than snapshot-testing rendered output.

**CI** (`.github/workflows/`): `tests.yml` runs the suite on Ubuntu and in a Fedora container; `data.yml` validates the desktop entry and the SVGs. The two distros are there for the **GTK version** spread, not the Python one — a Python matrix is impossible anyway, since PyGObject is a system package carrying the typelibs and is pinned to the distro's Python. Both test jobs build the venv exactly as the README documents, so breaking the documented install breaks CI.

**App ID:** `io.github.osandum.Lectern` — permanent once published to Flathub, so don't churn it. The Python package/import name (`lectern`) and the GitHub repo name (`osandum/lectern`) are independent of it and don't need to match.

## Data files and packaging

`data/` holds the desktop entry and the icons (`icons/hicolor/scalable/apps/` for the colour icon, `icons/hicolor/symbolic/apps/` for the monochrome one, both named after the app ID).

**None of it is installed by `pip install -e .`** — `pyproject.toml` ships only the Python package, so for a plain pip install the desktop entry and icons have to be copied by hand (see the README). The **Flatpak manifest installs them explicitly** in its `build-commands`; if you add a data file, add it there too or it silently won't ship. `Exec=lectern %U` deliberately resolves via `PATH` rather than hardcoding a path into somebody's venv, which an earlier version did.

**Flatpak** (`build-aux/io.github.osandum.Lectern.yml`): GNOME runtime 50, which supplies PyGObject, GTK4, libadwaita, libsoup3 and librsvg — only the four pure-Python dependencies are built, from wheels pinned by URL and sha256 (a Flatpak build has no network). The manifest currently uses a `type: dir` source so it builds from a clone; **Flathub requires immutable sources**, so submission needs a git source pinned to a tag and commit. Two `finish-args` carry real justification and shouldn't be trimmed without thought: `--filesystem=host:ro` because the document portal grants only the opened file while Markdown resolves images and links *relative* to it, and `--share=network` because the remote-image banner has nothing to do without it. `--socket=cups` is deliberately absent on the theory that GTK4 routes printing through the portal when sandboxed — **untested in an actual sandbox**, and the first thing to try if printing turns out broken.

**Screenshots** (`data/screenshots/`) are generated from `demo.md` in that directory, and are referenced by the metainfo as raw.githubusercontent URLs, so renaming one breaks the metainfo. They are deliberately shot from a neutral path (`/tmp/lectern-demo/Documents`) rather than a home directory, since the headerbar shows the document's directory and these end up on a public store page.

**Icon constraints, learned by testing rather than assumed** — worth not re-litigating:
- The page is **portrait**. A landscape page above a stand reads as a *laptop* at 32px; four drafts failed this way before the shape was changed.
- The stand is **one flaring mass**, not a column and a foot. Thin slabs vanish at 32px and leave the page looking like a sign on a post.
- In the symbolic variant the page must be an **outline**. Filled, it merges into the stand and the silhouette becomes one blob.
- Always check a candidate at 32/48/64, not just at 128 — every one of the above only shows up small. Render through librsvg (what GTK uses), not ImageMagick's SVG delegate.
- `--` is illegal inside an XML comment, so the `--` used as a dash elsewhere in this codebase can't be used in the SVGs.
