"""Declarative Gtk.TextTag definitions shared by the on-screen renderer and
the print pipeline (see printing.py's use of tag_style_props), so the two
can never visually drift apart.
"""
import functools

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gtk, Pango, Gdk

# Colors that must adapt to the system light/dark preference. Keep this
# small and flat rather than trying to pull live theme colors out of GTK's
# CSS machinery -- a fixed, hand-picked pair of palettes is simpler and good
# enough for a text viewer.
_LIGHT = {
    "dim": "#5e5c64",
    "link": "#1a5fb4",
    "code-bg": "#f6f5f4",
    "pyg-keyword": "#a51e75",
    "pyg-string": "#26a269",
    "pyg-comment": "#77767b",
    "pyg-number": "#c64600",
    "pyg-builtin": "#12488b",
    "pyg-function": "#1a5fb4",
    "pyg-class": "#9a5b00",
    "pyg-operator": "#5e5c64",
    "pyg-decorator": "#813d9c",
}

_DARK = {
    "dim": "#9a9996",
    "link": "#62a0ea",
    "code-bg": "#2d2d2d",
    "pyg-keyword": "#dc8add",
    "pyg-string": "#8ff0a4",
    "pyg-comment": "#9a9996",
    "pyg-number": "#ffa348",
    "pyg-builtin": "#99c1f1",
    "pyg-function": "#62a0ea",
    "pyg-class": "#f8e45c",
    "pyg-operator": "#c0bfbc",
    "pyg-decorator": "#dc8add",
}

# Heading scales, largest first (heading1 == h1), aligned to GitHub's
# typography ratios.
_HEADING_SCALE = [2.0, 1.5, 1.25, 1.0, 0.875, 0.85]

# Extra space between wrapped display lines *within* one paragraph --
# distinct from inter-block and inter-list-item spacing. GTK's default is
# 0, which reads as slightly cramped for prose; applied via the always-on
# "prose" tag below (see renderer.py's root RenderCtx), not per block
# type, since it's a base body-text property everything else layers on
# top of.
PROSE_LINE_SPACING = 3

# Space above every list item after a list's first, aligned to GitHub's
# li + li = 0.25em target.
LIST_ITEM_GAP = 4

# Indent step, in pixels, per nesting level of list content (~2em).
LIST_INDENT_STEP = 30
LIST_HANGING_INDENT = -16
BLOCKQUOTE_INDENT = 24

# Pygments token-type tag names this module knows how to color. Kept in one
# place with highlighting.py's TOKEN_TAG_NAMES so the two can't disagree.
PYGMENTS_TAG_NAMES = [
    "pyg-keyword", "pyg-string", "pyg-comment", "pyg-number",
    "pyg-builtin", "pyg-function", "pyg-class", "pyg-operator",
    "pyg-decorator",
]


def _rgba(spec):
    rgba = Gdk.RGBA()
    rgba.parse(spec)
    return rgba


def link_color_hex(dark):
    """The raw hex string backing the "link" tag's color, for callers that
    need to build Pango markup directly (tables.py's clickable cell
    links) rather than going through the shared Gtk.TextTag table. Table
    cells are separate Gtk.Label widgets outside that table, so unlike
    buffer text they won't repaint automatically via update_tag_colors()
    if the desktop theme flips light/dark mid-session -- accepted as a
    minor known limitation rather than building live-recolor plumbing
    for it."""
    return (_DARK if dark else _LIGHT)["link"]


@functools.lru_cache(maxsize=2)
def tag_style_props(dark):
    """The single source of truth for tag -> GObject-property-value pairs,
    shared by create_tag_table/update_tag_colors below *and* by
    printing.py (which reuses it to build matching Pango run attributes,
    so on-screen and printed styling can't drift apart). Cached: there are
    only ever two possible results (light/dark), and this gets called on
    every reload, print job and theme change.

    Mixes run-level properties (weight, style, scale, family, underline,
    rise, foreground-rgba -- what printing.py's _PROP_TO_ATTR_CTOR
    understands) with block-level ones (left-margin, pixels-above-lines,
    paragraph-background-rgba, wrap-mode -- meaningful only to
    Gtk.TextTag/TextView, which printing.py handles separately via its own
    page-layout math). Callers on the print side are expected to pick out
    only what applies to them.
    """
    palette = _DARK if dark else _LIGHT
    props = {
        # Always present in every top-level RenderCtx (see renderer.py),
        # so it's the one tag guaranteed to overlap every paragraph's
        # wrapped lines -- including inside blockquotes/list items, since
        # push_block() only ever appends to block_tags, never replaces
        # them. Harmless on code-block text too: wrap-mode=NONE there
        # means lines never wrap, so pixels-inside-wrap has nothing to do.
        "prose": {"pixels-inside-wrap": PROSE_LINE_SPACING},
        "em": {"style": Pango.Style.ITALIC},
        "strong": {"weight": Pango.Weight.BOLD},
        "strike": {"strikethrough": True},
        "code-inline": {
            "family": "monospace",
            "scale": 0.92,
            "background-rgba": _rgba(palette["code-bg"]),
        },
        "code-block": {
            "family": "monospace",
            "scale": 0.92,
            "left-margin": 16,
            "right-margin": 16,
            "wrap-mode": Gtk.WrapMode.NONE,
        },
        # Renderer-created dynamic block-gap-* tags carry the actual
        # inter-block top spacing value.
        "list-item-gap": {"pixels-above-lines": LIST_ITEM_GAP},
        "blockquote": {
            "left-margin": BLOCKQUOTE_INDENT,
            "foreground-rgba": _rgba(palette["dim"]),
            "style": Pango.Style.ITALIC,
        },
        # "tnum" (OpenType tabular numerals) makes every digit render at
        # the same fixed width -- without it, digit "1" commonly renders
        # visibly narrower than "2"-"9" in proportional UI fonts, so two
        # same-length ordered-list markers (e.g. "1." and "2.") can end up
        # different pixel widths, offsetting where their item text starts
        # even though nothing else about them differs. (A generalized
        # Pango-tab-stop-based fix for the further case of *different*
        # digit counts, e.g. "9." vs "10.", was tried and reverted: it
        # broke wrapped continuation-line alignment for multi-line items,
        # a worse regression than the one it fixed.)
        "list-marker": {"foreground-rgba": _rgba(palette["dim"]), "font-features": "tnum 1"},
        "link": {
            "foreground-rgba": _rgba(palette["link"]),
            "underline": Pango.Underline.SINGLE,
        },
        "footnote-ref": {
            "rise": 6 * Pango.SCALE,
            "scale": 0.75,
            "foreground-rgba": _rgba(palette["link"]),
        },
        "task-checked-glyph": {"foreground-rgba": _rgba(palette["dim"])},
        "task-unchecked-glyph": {"foreground-rgba": _rgba(palette["dim"])},
        # Shared by tables.py (on-screen Gtk.Label markup) and printing.py
        # (Pango run attribute) so "table headers are bold" is one fact,
        # not two separately-hardcoded ones.
        "table-header": {"weight": Pango.Weight.BOLD},
    }
    for i in range(6):
        props[f"heading{i + 1}"] = {
            "weight": Pango.Weight.BOLD,
            "scale": _HEADING_SCALE[i],
        }
    for name in PYGMENTS_TAG_NAMES:
        props[name] = {"foreground-rgba": _rgba(palette[name])}
    return props


def _make_tag(name, props):
    tag = Gtk.TextTag(name=name)
    for prop, value in props.items():
        tag.set_property(prop, value)
    return tag


def create_tag_table(dark=False):
    """Build a fresh Gtk.TextTagTable with every static tag installed."""
    table = Gtk.TextTagTable()
    for name, props in tag_style_props(dark).items():
        table.add(_make_tag(name, props))
    return table


def update_tag_colors(tag_table, dark):
    """Re-apply theme-dependent colors in place (called on dark-mode change)."""
    for name, props in tag_style_props(dark).items():
        tag = tag_table.lookup(name)
        if tag is None:
            continue
        for prop, value in props.items():
            tag.set_property(prop, value)


def get_or_create_tag(tag_table, name, props=None):
    """Look up `name` in `tag_table`, creating it (with `props` applied, if
    given) if it doesn't exist yet. The shared lookup-or-create primitive
    behind ensure_list_indent_tag/ensure_instance_tag below, and reused
    as-is by findbar.py for its match-highlight tags -- one "create this
    tag if missing" idiom, not three."""
    tag = tag_table.lookup(name)
    if tag is None:
        tag = Gtk.TextTag(name=name)
        for prop, value in (props or {}).items():
            tag.set_property(prop, value)
        tag_table.add(tag)
    return tag


def ensure_block_gap_tag(tag_table, pixels):
    """Lazily create and cache a block-gap tag with `pixels-above-lines`."""
    pixels = max(0, int(pixels))
    return get_or_create_tag(
        tag_table,
        f"block-gap-{pixels}",
        {"pixels-above-lines": pixels},
    )


def list_indent_tag_name(level):
    return f"list-indent-{level}"


def ensure_list_indent_tag(tag_table, level):
    """Lazily create (and cache in the table) the indent tag for `level`."""
    name = list_indent_tag_name(level)
    return get_or_create_tag(tag_table, name, {
        "left-margin": LIST_INDENT_STEP * (level + 1),
        "indent": LIST_HANGING_INDENT,
    })


def ensure_instance_tag(tag_table, name):
    """Create a bare, style-less tag used only as a per-instance dispatch
    key (links, footnote refs) if it doesn't already exist."""
    return get_or_create_tag(tag_table, name)
