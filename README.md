# Lectern

[![Tests](https://github.com/osandum/gnome-lectern/actions/workflows/tests.yml/badge.svg)](https://github.com/osandum/gnome-lectern/actions/workflows/tests.yml)
[![License: GPL-2.0-or-later](https://img.shields.io/badge/License-GPL--2.0--or--later-blue.svg)](LICENSE)

A read-only, [Papers](https://apps.gnome.org/da/Papers/)-style Markdown
viewer for GNOME: open a `.md` file, read it, search/zoom/print it. No
editing UI.

- Full CommonMark + GFM: tables, fenced code with syntax highlighting,
  task lists, footnotes, images.
- Native GTK4/Libadwaita rendering (a single `Gtk.TextView` + tags) —
  no embedded browser engine.
- Find bar, zoom, printing, auto-reload when the file changes on disk.
- One window per opened file, no sidebar — same feel as Papers/Evince.

### Remote images

Local images render immediately. Images with `http(s)` URLs are **not**
fetched when you open a document — opening a file shouldn't tell whoever
hosts its images that you opened it, and a per-image URL makes a
serviceable read receipt. A banner offers to load them, per document.

## Running from source

```sh
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
pip install -e .
lectern path/to/file.md
```

`--system-site-packages` is required so the venv can see the system
PyGObject/GTK4/Libadwaita bindings, which are not installed via pip.

To get a desktop entry with its icon:

```sh
cp data/io.github.osandum.Lectern.desktop ~/.local/share/applications/
cp -r data/icons/hicolor ~/.local/share/icons/
gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor
update-desktop-database ~/.local/share/applications
```

The desktop entry's `Exec=lectern %U` resolves via `PATH`, so the venv's
`bin/` has to be on your `PATH` — symlinking `.venv/bin/lectern` into
`~/.local/bin/` is the easy way. Copying the entry without the icons
leaves it showing a blank tile, since `Icon=` names a themed icon rather
than a file path.

Nothing under `data/` is installed by `pip install -e .` — `pyproject.toml`
ships only the Python package. Packaging that properly is still open.

## Tests

```sh
pytest tests/
```

The tests are headless — `Gtk.TextBuffer` manipulation and widget
construction only need GTK initialized, not a realized window. Verified
with `DISPLAY` and `WAYLAND_DISPLAY` both unset, so CI needs no `xvfb`.

## Status

v1. Known scope cuts: table column widths are a simple
median-character-count heuristic rather than true
text-measurement-based sizing, highlighting for undeclared/obscure
fenced-code languages is best-effort, images don't scale with zoom, and
an image sitting *inside* a paragraph prints above that paragraph rather
than within it (on screen it's positioned correctly).

## License

GPL-2.0-or-later, see [LICENSE](LICENSE).
