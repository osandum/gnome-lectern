# mdview

A read-only, [Papers](https://apps.gnome.org/da/Papers/)-style Markdown
viewer for GNOME: open a `.md` file, read it, search/zoom/print it. No
editing UI.

- Full CommonMark + GFM: tables, fenced code with syntax highlighting,
  task lists, footnotes.
- Native GTK4/Libadwaita rendering (a single `Gtk.TextView` + tags) —
  no embedded browser engine.
- Find bar, zoom, printing, auto-reload when the file changes on disk.
- One window per opened file, no sidebar — same feel as Papers/Evince.

## Running from source

```sh
python3 -m venv .venv --system-site-packages
source .venv/bin/activate
pip install -e .
mdview path/to/file.md
```

`--system-site-packages` is required so the venv can see the system
PyGObject/GTK4/Libadwaita bindings, which are not installed via pip.

## Tests

```sh
pytest tests/
```

The renderer tests are headless (no display required) — `Gtk.TextBuffer`
manipulation only needs GTK initialized, not a realized window.

## Status

v1. See known scope cuts in the design notes: find doesn't search inside
table cells, table column widths are a simple median-character-count
heuristic rather than true text-measurement-based sizing, and highlighting for
undeclared/obscure fenced-code languages is best-effort.

## License

GPL-2.0-or-later, see [LICENSE](LICENSE).
