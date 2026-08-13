"""Print pipeline, driven entirely off MarkdownRenderer.print_model -- never
by re-walking the live TextBuffer. That's deliberate: Gtk.TextChildAnchor
-embedded tables leave a single object-replacement character in the
buffer's text stream, so reconstructing formatted text from the buffer
after the fact would silently lose every table cell.

Pango.AttrList offsets are UTF-8 *byte* offsets, not character offsets --
this bit us once already while building this file, so run-boundary byte
lengths are computed explicitly throughout rather than assumed to match
`len(text)`.
"""
import math

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gtk, Gdk, Pango, PangoCairo

from . import tags as tagdefs
from . import tables as tabledefs
from . import zoom as zoomdefs

# Screen pixels are nominally 96dpi; print units are 72dpi points. Every
# length tags.py and renderer.py express is in those pixels, so it has to
# come through here to land on paper at the proportions it has on screen
# -- a "16" spent directly as points is a third too big.
PX_TO_PT = 72.0 / 96.0


def pt(px):
    return px * PX_TO_PT


HR_HEIGHT_PT = 1.0
HR_BLOCK_HEIGHT_PT = 16.0
TABLE_CELL_PAD_PT = 6.0
TABLE_ROW_GAP_PT = 6.0
TABLE_RULE_RGB = (0.7, 0.7, 0.7)
CODE_BLOCK_PAD_PT = pt(tagdefs.CODE_BLOCK_PADDING)
CODE_BLOCK_RADIUS_PT = pt(tagdefs.CODE_BLOCK_RADIUS)
HEADING_RULE_WIDTH_PT = pt(tagdefs.HEADING_RULE_WIDTH)
HEADING_RULE_PAD_PT = pt(tagdefs.HEADING_RULE_PAD)
# Cap on how much of one page a single image may claim, so a very tall
# image leaves room for something else rather than owning a page outright.
IMAGE_MAX_PAGE_FRACTION = 0.9

# Only run-level (character-range) properties translate to Pango
# attributes; margins/backgrounds/pixel-spacing are block-level concerns
# handled by this module's own layout math instead.
_PROP_TO_ATTR_CTOR = {
    "weight": Pango.attr_weight_new,
    "style": Pango.attr_style_new,
    "strikethrough": Pango.attr_strikethrough_new,
    "family": Pango.attr_family_new,
    "underline": Pango.attr_underline_new,
    "rise": Pango.attr_rise_new,
}


# Print-only substitutes for a UI font that can't carry its own weights
# onto paper (see _base_font), nearest-first by resemblance to the GNOME
# UI font. Hand-picked because each ships its weights as *separate face
# files*, which is the property that matters and the one thing Pango
# cannot report: Pango.FontFamily.is_variable() is true for Cantarell too
# (it ships a variable face alongside its static ones) even though it
# prints all weights correctly, so the list carries this knowledge rather
# than a predicate.
_STATIC_FONT_FALLBACKS = ("Cantarell", "Liberation Sans", "DejaVu Sans")


def _base_font():
    """The font to print in: GTK's UI font family, at zoom.py's 100% size.

    A Gtk.PrintContext layout otherwise inherits whatever the print
    system picked -- typically a serif, at its own size -- so a document
    came out in a different typeface from the one it was read in, and
    every length scaled from a screen pixel (indents, the hanging indent,
    line spacing) was measured against the wrong body size. Print is
    deliberately pinned to 100%: the reader's zoom level is a reading
    aid, not a property of the document.

    The exception is a *variable* UI font, which the current GNOME
    default (Adwaita Sans) is: one file, with every weight as a named
    instance. Cairo's PDF font subsetting keeps only one weight pairing
    per face, so mixing regular and bold silently prints every heading
    above the first size in regular -- reproducible in a dozen lines of
    plain Pango/cairo, with no fix available from this side (font
    variations, a "... Bold" family name and Weight.HEAVY were all
    tried). Such a font is swapped for a static-faced one on paper only:
    a near-miss in letterforms costs less than losing the heading
    hierarchy, and sizes/metrics still come from the screen either way.
    """
    settings = Gtk.Settings.get_default()
    name = settings.get_property("gtk-font-name") if settings is not None else None
    desc = Pango.FontDescription.new() if not name else Pango.FontDescription.from_string(name)
    families = {f.get_name(): f for f in PangoCairo.FontMap.get_default().list_families()}
    ui_family = families.get(desc.get_family() or "")
    if ui_family is None or ui_family.is_variable():
        substitute = next((f for f in _STATIC_FONT_FALLBACKS if f in families), None)
        if substitute is not None:
            desc.set_family(substitute)
    desc.set_size(int(zoomdefs.BASE_PT * Pango.SCALE))
    return desc


# Tag properties that describe a *font* rather than decorate a run. They
# go into one complete Pango.FontDescription per run instead of separate
# attributes: layered on a print-context layout as deltas, a bold run at
# any size other than the base one resolved back to the regular face,
# which silently un-bolded every heading above h4.
_DESC_PROPS = frozenset({"weight", "style", "family", "scale"})


def _run_font(base, run_props):
    """One font description for a run, from the tags applied to it."""
    desc = base.copy()
    scale = 1.0
    for props in run_props:
        for prop, value in props.items():
            if prop == "scale":
                # Gtk.TextTag scales *compose* where several tags apply
                # (inline code inside a heading takes both), so they
                # multiply out into the single size below.
                scale *= value
            elif prop == "weight":
                desc.set_weight(value)
            elif prop == "style":
                desc.set_style(value)
            elif prop == "family":
                desc.set_family(value)
    desc.set_size(int(round(zoomdefs.BASE_PT * scale * Pango.SCALE)))
    return desc


def _foreground_attr(rgba):
    return Pango.attr_foreground_new(
        int(rgba.red * 65535), int(rgba.green * 65535), int(rgba.blue * 65535)
    )


def _rgba_rgb(rgba):
    return rgba.red, rgba.green, rgba.blue


def _rounded_rect(cr, x, y, width, height, radius):
    """Cairo path for the code panel. Deliberately a second, tiny copy of
    decorated_textview.py's: importing that module would pull Adw and the
    whole on-screen view into the print path for eight lines of geometry."""
    radius = max(0.0, min(radius, width / 2.0, height / 2.0))
    x2, y2 = x + width, y + height
    cr.new_sub_path()
    cr.arc(x2 - radius, y + radius, radius, -math.pi / 2, 0.0)
    cr.arc(x2 - radius, y2 - radius, radius, 0.0, math.pi / 2)
    cr.arc(x + radius, y2 - radius, radius, math.pi / 2, math.pi)
    cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
    cr.close_path()


def _left_margin_pt(block_tags):
    """How far in from the text column these block tags put a block on
    screen, so print puts it in the same place.

    Two things this is not. It is not a *sum*: `left-margin` is a plain
    Gtk.TextTag property, so where several applied tags set it GTK takes
    the highest-priority one rather than adding them up -- and the
    list-indent tags are created innermost-last, which makes the innermost
    one win. Their margins are absolute already, so that one value is the
    whole indent, and a blockquote nested in a list adds nothing on top.

    And it is not the tag's raw value: a Gtk.TextTag left-margin
    *replaces* the view's own rather than adding to it, so on screen an
    indented block clears unindented prose by the difference between the
    two. Print's page margin is the equivalent of the view's.
    """
    columns = [
        # A "list-body-" tag is the same level's text column, one hanging
        # indent further in than its marker column.
        (tagdefs.list_text_column if name.startswith("list-body-")
         else tagdefs.list_marker_column)(int(name.rsplit("-", 1)[1]))
        for name in block_tags
        if name.startswith("list-indent-") or name.startswith("list-body-")
    ]
    if columns:
        margin = max(columns)
    elif "blockquote" in block_tags:
        margin = tagdefs.BLOCKQUOTE_INDENT
    else:
        return 0.0
    return pt(max(0, margin - tagdefs.CONTENT_MARGIN))


def _inline_code_spans(item):
    """UTF-8 byte ranges of the item's inline-code runs, merged where they
    touch, so print can paint the same padded rounded chip behind them
    that decorated_textview.py paints on screen. Pango can only give a run
    a flat, tight background, which is the very thing Gtk.TextTag could
    already do and the chip exists to improve on."""
    spans, offset = [], 0
    for text, tag_names in item.runs:
        nbytes = len(text.encode("utf-8"))
        if "code-inline" in tag_names:
            if spans and spans[-1][1] == offset:
                spans[-1] = (spans[-1][0], offset + nbytes)
            else:
                spans.append((offset, offset + nbytes))
        offset += nbytes
    return spans


_MARKER_TAGS = frozenset({"list-marker", "task-checked-glyph", "task-unchecked-glyph"})


def _marker_hang_pt(item):
    """The hanging indent for a list item's opening line: it starts at the
    marker column, and every line after it at the text column one
    LIST_HANGING_INDENT further in. Zero for anything that isn't a
    marker line -- including the item's own continuation blocks, which
    are already placed on the text column by _left_margin_pt.

    Pango.Layout's indent means the same thing as Gtk.TextTag's (negative
    indents the lines *after* the first, which is how both spell "hang"),
    so screen and paper stay in step through one shared constant.
    """
    if not any(name.startswith("list-indent-") for name in item.block_tags):
        return 0.0
    if not item.runs or not _MARKER_TAGS.intersection(item.runs[0][1]):
        return 0.0
    return pt(-tagdefs.LIST_HANGING_INDENT)


def _line_geometry(layout):
    """[(Pango.LayoutLine, y_top_pt, height_pt, baseline_pt, x_off_pt), ...].

    x_off is the line's own horizontal offset inside the layout, which is
    where a hanging indent lives. Pages are drawn a line at a time
    (show_layout_line, so a block can be split across a page break), and
    that draws wherever the caller puts the pen -- every offset Pango
    computed for the line is lost unless it travels with it.
    """
    result = []
    it = layout.get_iter()
    while True:
        line = it.get_line_readonly()
        _ink, logical = it.get_line_extents()
        y_top = Pango.units_to_double(logical.y)
        height = Pango.units_to_double(logical.height)
        baseline = Pango.units_to_double(it.get_baseline())
        x_off = Pango.units_to_double(logical.x)
        result.append((line, y_top, height, baseline, x_off))
        if not it.next_line():
            break
    return result


def _leading_pt(context, font):
    """Line-height padding in points, from the *print* font's own metrics.

    Screen and paper each measure their own font (see tags.line_leading):
    the two are not always the same family, since a variable UI font gets
    substituted on paper, and a leading computed for one would be wrong for
    the other."""
    metrics = context.create_pango_layout().get_context().get_metrics(font, None)
    natural = (metrics.get_ascent() + metrics.get_descent()) / Pango.SCALE
    return pt(tagdefs.line_leading(zoomdefs.BASE_PT * 96 / 72, natural / PX_TO_PT))


def _build_text_layout(context, style_table, item, width_pt, hanging_pt=0.0,
                       font=None, leading_pt=0.0):
    combined = "".join(text for text, _tags in item.runs) or " "
    layout = context.create_pango_layout()
    layout.set_font_description(font or _base_font())
    layout.set_width(Pango.units_from_double(max(width_pt, 1.0)))
    layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    if hanging_pt:
        layout.set_indent(Pango.units_from_double(-hanging_pt))
    # Matches the "prose" tag's line-height leading on screen (tags.py) --
    # Pango.Layout has no per-run equivalent, so it's set once here at
    # the whole-layout level instead. Pango puts this *between* lines and
    # not after the last one, which is why _build_blocks adds one more
    # leading to each block's gap: on screen the equivalent is
    # pixels-below-lines, which every line gets including a block's last.
    layout.set_spacing(Pango.units_from_double(leading_pt))
    layout.set_text(combined, -1)
    attr_list = Pango.AttrList()
    byte_offset = 0
    base_font = font or _base_font()
    for text, tag_names in item.runs:
        nbytes = len(text.encode("utf-8"))
        run_props = [style_table[n] for n in tag_names if style_table.get(n)]
        for props in run_props:
            for prop, value in props.items():
                if prop in _DESC_PROPS:
                    continue  # folded into the run's font description below
                if prop == "foreground-rgba":
                    attr = _foreground_attr(value)
                else:
                    ctor = _PROP_TO_ATTR_CTOR.get(prop)
                    if ctor is None:
                        continue
                    attr = ctor(value)
                attr.start_index = byte_offset
                attr.end_index = byte_offset + nbytes
                attr_list.insert(attr)
        attr = Pango.attr_font_desc_new(_run_font(base_font, run_props))
        attr.start_index = byte_offset
        attr.end_index = byte_offset + nbytes
        attr_list.insert(attr)
        byte_offset += nbytes
    layout.set_attributes(attr_list)
    return layout


def _build_table_rows(context, style_table, rows, width_pt, font=None):
    if not rows:
        return [], [], []
    # Shared with tables.py's on-screen <b>...</b> header markup via
    # tags.py's "table-header" entry, so "headers are bold" is one fact.
    header_weight = style_table.get("table-header", {}).get("weight", Pango.Weight.BOLD)
    ncols = max(len(r) for r in rows)
    # Same median-character-count weighting tables.py uses for the
    # on-screen Gtk.Grid (via max-width-chars), so a column doesn't come
    # out "wide" on screen and "narrow" on paper -- one shared notion of
    # column proportions, applied via the two renderers' own units.
    weights = tabledefs.column_char_weights(rows)
    total_weight = sum(weights)
    col_widths = [width_pt * w / total_weight for w in weights]
    row_layouts, row_heights = [], []
    for row_index, row in enumerate(rows):
        cells, max_h = [], 0.0
        for col_index in range(ncols):
            text = row[col_index] if col_index < len(row) else ""
            col_width = col_widths[col_index]
            layout = context.create_pango_layout()
            layout.set_font_description(font or _base_font())
            layout.set_width(Pango.units_from_double(max(col_width - 2 * TABLE_CELL_PAD_PT, 1.0)))
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
            layout.set_text(text, -1)
            if row_index == 0:
                al = Pango.AttrList()
                attr = Pango.attr_weight_new(header_weight)
                attr.start_index = 0
                attr.end_index = len(text.encode("utf-8"))
                al.insert(attr)
                layout.set_attributes(al)
            _w, h = layout.get_pixel_size()
            max_h = max(max_h, h + 2 * TABLE_CELL_PAD_PT)
            cells.append(layout)
        row_layouts.append(cells)
        row_heights.append(max_h)
    return row_layouts, row_heights, col_widths


class _Block:
    """One drawable unit produced from a PrintItem, laid out against a
    specific content width and ready to be sliced across pages."""

    def __init__(self, kind, x=0.0):
        self.kind = kind
        self.x = x
        self.layout = None
        self.lines = None
        self.rows = None
        self.col_widths = None
        self.pixbuf = None
        self.draw_size = None
        self.height = 0.0
        # Space above this block, carried over from the renderer's own
        # collapsed-margin model (PrintItem.gap) so paper spaces blocks
        # exactly as the screen does.
        self.gap = 0.0
        # Chrome drawn outside the text box, mirroring what
        # decorated_textview.py paints on screen: `panel` is the
        # fenced-code background (a dict of geometry, or None), `rule` the
        # RGB of an h1/h2 bottom rule (or None).
        self.panel = None
        self.rule = None
        self.chips = ()
        self.chip_rgb = None

    @classmethod
    def text(cls, x, layout, *, panel=None, rule=None, chips=(), chip_rgb=None):
        block = cls("layout", x)
        block.layout = layout
        block.lines = _line_geometry(layout)
        block.height = (block.lines[-1][1] + block.lines[-1][2]) if block.lines else 0.0
        block.panel = panel
        block.rule = rule
        block.chips = chips
        block.chip_rgb = chip_rgb
        return block

    @classmethod
    def hr(cls):
        block = cls("hr", 0.0)
        block.height = HR_BLOCK_HEIGHT_PT
        return block

    @classmethod
    def image(cls, x, pixbuf, width, height):
        block = cls("image", x)
        block.pixbuf = pixbuf
        block.draw_size = (width, height)
        block.height = height
        return block

    @classmethod
    def table(cls, x, row_layouts, row_heights, col_widths):
        block = cls("table", x)
        block.rows = (row_layouts, row_heights)
        block.col_widths = col_widths
        block.height = sum(row_heights) + TABLE_ROW_GAP_PT * max(len(row_heights) - 1, 0)
        return block


class _AltTextItem:
    """Stands in for an image that has no pixels to print -- one that
    failed, or a remote one the reader never chose to load. Printing the
    alt text keeps the page honest about what was meant to be there
    instead of leaving an unexplained gap."""
    __slots__ = ("runs", "block_tags", "language", "kind")

    def __init__(self, alt, block_tags):
        self.kind = "paragraph"
        # Italic rather than a dim colour: there's no "dim" entry in
        # tag_style_props, and italic alt text is the conventional way to
        # show it anyway.
        self.runs = [(f"[{alt}]", ["em"])]
        self.block_tags = block_tags
        self.language = None


def _image_draw_size(pixbuf, max_width_pt, max_height_pt):
    """Point size to draw at: the image's own 96dpi size, shrunk to fit
    the column, then shrunk again if it's still too tall for a page.
    Never enlarged -- a 32px icon blown up to column width looks broken."""
    natural_w = pt(pixbuf.get_width())
    natural_h = pt(pixbuf.get_height())
    if natural_w <= 0 or natural_h <= 0:
        return None
    scale = min(1.0, max_width_pt / natural_w, max_height_pt / natural_h)
    return natural_w * scale, natural_h * scale


def _build_blocks(context, style_table, print_model, page_width, page_height, dark):
    blocks = []
    # One description for the whole job rather than a Gtk.Settings lookup
    # per layout.
    font = _base_font()
    leading = _leading_pt(context, font)
    code_bg = style_table.get("code-inline", {}).get("background-rgba")
    code_bg_rgb = _rgba_rgb(code_bg) if code_bg is not None else None
    heading_rule = tagdefs.heading_rule_rgba(dark)
    heading_rule_rgb = _rgba_rgb(heading_rule)
    for item in print_model:
        before = len(blocks)
        if item.kind == "image":
            x = _left_margin_pt(item.block_tags)
            # Read the texture now, at print time, rather than at render
            # time -- a remote image the reader loaded after opening the
            # document has pixels by now and should print.
            texture = item.image.texture if item.image is not None else None
            pixbuf = Gdk.pixbuf_get_from_texture(texture) if texture is not None else None
            size = _image_draw_size(
                pixbuf, page_width - x, page_height * IMAGE_MAX_PAGE_FRACTION
            ) if pixbuf is not None else None
            if size is not None:
                blocks.append(_Block.image(x, pixbuf, size[0], size[1]))
            else:
                alt = item.image.alt if item.image is not None else "image"
                layout = _build_text_layout(
                    context, style_table, _AltTextItem(alt, item.block_tags),
                    page_width - x, font=font, leading_pt=leading,
                )
                blocks.append(_Block.text(x, layout))
        elif item.kind == "paragraph":
            x = _left_margin_pt(item.block_tags)
            layout = _build_text_layout(
                context, style_table, item, page_width - x, _marker_hang_pt(item),
                font, leading,
            )
            run_tags = {tag for _text, tags in item.runs for tag in tags}
            rule = heading_rule_rgb if run_tags & {"heading1", "heading2"} else None
            blocks.append(_Block.text(
                x, layout, rule=rule,
                chips=_inline_code_spans(item), chip_rgb=code_bg_rgb,
            ))
        elif item.kind == "code-block":
            # Same shape as the on-screen panel: it spans the content
            # column edge to edge, with the code text inset by the pad on
            # every side.
            x = _left_margin_pt(item.block_tags)
            layout = _build_text_layout(
                context, style_table, item, page_width - x - 2 * CODE_BLOCK_PAD_PT,
                font=font, leading_pt=leading,
            )
            panel = {
                "x": x,
                "width": page_width - x,
                "pad": CODE_BLOCK_PAD_PT,
                "radius": CODE_BLOCK_RADIUS_PT,
                "fill_rgb": code_bg_rgb or (0.96, 0.96, 0.96),
            }
            blocks.append(_Block.text(x + CODE_BLOCK_PAD_PT, layout, panel=panel))
        elif item.kind == "hr":
            blocks.append(_Block.hr())
        elif item.kind == "table":
            x = _left_margin_pt(item.block_tags)
            row_layouts, row_heights, col_widths = _build_table_rows(
                context, style_table, item.rows, page_width - x, font
            )
            if row_layouts:
                blocks.append(_Block.table(x, row_layouts, row_heights, col_widths))
        if len(blocks) > before:
            # One leading on top of the collapsed margin: on screen every
            # line carries it as pixels-below-lines, including a block's
            # last, whereas Pango's set_spacing only goes *between* lines.
            blocks[before].gap = pt(item.gap) + leading
    return blocks


def _out_of_room(y, gap, page_height):
    """True if even the inter-block gap wouldn't fit before this block
    starts. Used by table/layout blocks, which paginate their own content
    chunk-by-chunk once started -- this is only the pre-check for whether
    there's room to *begin*. hr is atomic (never chunked) so it checks its
    own full height too, inline, rather than through this helper."""
    return y + gap >= page_height


def _paginate(blocks, page_height):
    pages, current, y = [], [], 0.0

    def new_page():
        nonlocal current, y
        pages.append(current)
        current, y = [], 0.0

    for block in blocks:
        # A page break already separates a block from what came before it,
        # so the gap it was given only applies mid-page.
        gap = block.gap if current else 0.0
        if block.kind == "hr":
            if y + gap + block.height > page_height and current:
                new_page()
                gap = 0.0
            current.append({"type": "hr", "y": y + gap + block.height / 2})
            y += gap + block.height
        elif block.kind == "image":
            # Atomic like hr -- an image is never sliced across a page
            # break; _image_draw_size already guaranteed it fits on one.
            if y + gap + block.height > page_height and current:
                new_page()
                gap = 0.0
            current.append({
                "type": "image", "x": block.x, "y": y + gap,
                "pixbuf": block.pixbuf, "size": block.draw_size,
            })
            y += gap + block.height
        elif block.kind == "table":
            if current and _out_of_room(y, gap, page_height):
                new_page()
                gap = 0.0
            y += gap
            row_layouts, row_heights = block.rows
            total_rows = len(row_layouts)
            i = 0
            while i < len(row_layouts):
                rows_here, row_y = [], y
                while i < len(row_layouts):
                    if row_y + row_heights[i] > page_height and rows_here:
                        break
                    # Global row index travels with each row (not reset
                    # per page) so _draw_entry can tell whether a given
                    # row is the table's true last row -- it draws a rule
                    # after every row except that one, to match the
                    # on-screen rules-between-rows style exactly.
                    rows_here.append((row_layouts[i], row_y, row_heights[i], i))
                    row_y += row_heights[i] + TABLE_ROW_GAP_PT
                    i += 1
                current.append({
                    "type": "table", "x": block.x, "col_widths": block.col_widths,
                    "rows": rows_here, "total_rows": total_rows,
                })
                y = row_y
                if i < len(row_layouts):
                    new_page()
        else:  # "layout": paragraph or code-block
            if current and _out_of_room(y, gap, page_height):
                new_page()
                gap = 0.0
            y += gap
            lines = block.lines
            # Padding for the code panel, and room for a heading's rule.
            # Both are per *page fragment*: a fence split across a page
            # break gets a self-contained panel on each side of it.
            pad = block.panel["pad"] if block.panel else 0.0
            rule_space = (HEADING_RULE_PAD_PT + HEADING_RULE_WIDTH_PT) if block.rule else 0.0
            li = 0
            while li < len(lines):
                y += pad
                remaining = page_height - y - pad - rule_space
                chunk_top = lines[li][1]
                chunk = []
                while li < len(lines):
                    _line, y_top, height, _baseline, _x_off = lines[li]
                    if (y_top - chunk_top) + height > remaining and chunk:
                        break
                    chunk.append(lines[li])
                    li += 1
                if not chunk:
                    # A single line taller than a whole page: draw it anyway
                    # (it will clip) rather than loop forever.
                    chunk = [lines[li]]
                    li += 1
                chunk_height = (chunk[-1][1] + chunk[-1][2]) - chunk[0][1]
                current.append({
                    "type": "layout", "x": block.x,
                    # `y` is where the chunk's *top* goes, but
                    # show_layout_line draws from the baseline, so the
                    # entry is anchored on the first line's baseline --
                    # one ascent further down. Chrome (panel, rule) needs
                    # the top and height instead, so both travel along.
                    "y": y + (chunk[0][3] - chunk[0][1]),
                    # get_baseline() is absolute (layout-origin-relative),
                    # same coordinate space as y_top -- draw position must
                    # track the *baseline* delta directly, not y_top plus
                    # baseline (that double-counts each line's ascent).
                    "lines": chunk, "origin_baseline": chunk[0][3],
                    "top": y, "height": chunk_height,
                    "panel": block.panel,
                    # Whole-layout byte ranges; each Pango line knows its
                    # own, so they need no per-chunk slicing.
                    "chips": block.chips, "chip_rgb": block.chip_rgb,
                    # Only the fragment that ends the heading carries its
                    # rule; a heading that wrapped across a page break
                    # would otherwise get one on every page.
                    "rule": block.rule if li >= len(lines) else None,
                })
                y += chunk_height + pad
                if li < len(lines):
                    new_page()
            y += rule_space
    if current or not pages:
        pages.append(current)
    return pages


def _draw_inline_code_chips(cr, entry):
    """Paint the padded rounded chip behind each inline-code span on this
    page fragment, matching decorated_textview.py on screen. Positions
    come from the Pango line rather than the layout, so a chip that wraps
    gets one chip per line, and each line's own extents (relative to its
    baseline, and excluding the inter-line spacing) size it."""
    spans, rgb = entry["chips"], entry["chip_rgb"]
    if not spans or rgb is None:
        return
    pad_x, pad_y = pt(tagdefs.INLINE_CODE_PAD_X), pt(tagdefs.INLINE_CODE_PAD_Y)
    cr.save()
    cr.set_source_rgb(*rgb)
    for line, _y_top, _height, baseline, x_off in entry["lines"]:
        line_start, line_end = line.start_index, line.start_index + line.length
        _ink, logical = line.get_extents()
        top = (entry["y"] + (baseline - entry["origin_baseline"])
               + Pango.units_to_double(logical.y))
        height = Pango.units_to_double(logical.height)
        for span_start, span_end in spans:
            start, end = max(span_start, line_start), min(span_end, line_end)
            if start >= end:
                continue
            x0 = Pango.units_to_double(line.index_to_x(start, False))
            x1 = Pango.units_to_double(line.index_to_x(end - 1, True))
            if x1 < x0:
                x0, x1 = x1, x0
            _rounded_rect(
                cr, entry["x"] + x_off + x0 - pad_x, top - pad_y,
                (x1 - x0) + pad_x * 2, height + pad_y * 2,
                pt(tagdefs.INLINE_CODE_RADIUS),
            )
            cr.fill()
    cr.restore()


def _draw_entry(cr, entry, page_width):
    if entry["type"] == "hr":
        cr.save()
        cr.set_source_rgb(0.6, 0.6, 0.6)
        cr.set_line_width(HR_HEIGHT_PT)
        cr.move_to(0, entry["y"])
        cr.line_to(page_width, entry["y"])
        cr.stroke()
        cr.restore()
    elif entry["type"] == "table":
        # Thin rule under each row except the table's true last one --
        # matches tables.py's on-screen Gtk.Separator-between-rows style
        # (no per-cell boxes: tried a full grid, rejected as too heavy/
        # inconsistent with a plain-rules look).
        x0, col_widths = entry["x"], entry["col_widths"]
        for cell_layouts, row_y, row_h, row_index in entry["rows"]:
            cx = x0
            for col_index, layout in enumerate(cell_layouts):
                cr.move_to(cx + TABLE_CELL_PAD_PT, row_y + TABLE_CELL_PAD_PT)
                PangoCairo.show_layout(cr, layout)
                cx += col_widths[col_index]
            if row_index < entry["total_rows"] - 1:
                rule_y = row_y + row_h + TABLE_ROW_GAP_PT / 2
                cr.save()
                cr.set_source_rgb(*TABLE_RULE_RGB)
                cr.set_line_width(0.75)
                cr.move_to(x0, rule_y)
                cr.line_to(x0 + sum(col_widths[:len(cell_layouts)]), rule_y)
                cr.stroke()
                cr.restore()
    elif entry["type"] == "image":
        pixbuf = entry["pixbuf"]
        width, height = entry["size"]
        cr.save()
        cr.translate(entry["x"], entry["y"])
        # Gdk.cairo_set_source_pixbuf places the image at its pixel size,
        # so the scale to point-size has to be on the matrix, not on the
        # source.
        cr.scale(width / pixbuf.get_width(), height / pixbuf.get_height())
        Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
        cr.paint()
        cr.restore()
    elif entry["type"] == "layout":
        panel = entry["panel"]
        if panel is not None:
            pad = panel["pad"]
            cr.save()
            _rounded_rect(
                cr, panel["x"], entry["top"] - pad,
                panel["width"], entry["height"] + pad * 2, panel["radius"],
            )
            cr.set_source_rgb(*panel["fill_rgb"])
            cr.fill()
            cr.restore()
        _draw_inline_code_chips(cr, entry)
        for line, _y_top, _height, baseline, x_off in entry["lines"]:
            cr.move_to(entry["x"] + x_off, entry["y"] + (baseline - entry["origin_baseline"]))
            PangoCairo.show_layout_line(cr, line)
        if entry["rule"] is not None:
            rule_y = entry["top"] + entry["height"] + HEADING_RULE_PAD_PT
            cr.save()
            cr.set_source_rgb(*entry["rule"])
            cr.set_line_width(HEADING_RULE_WIDTH_PT)
            cr.move_to(entry["x"], rule_y)
            cr.line_to(page_width, rule_y)
            cr.stroke()
            cr.restore()


class PrintCoordinator:
    """One instance per print action; holds no state between calls."""

    def print_document(self, parent_window, print_model, dark, title,
                        action=Gtk.PrintOperationAction.PRINT_DIALOG, export_path=None):
        op = Gtk.PrintOperation()
        op.set_job_name(title)
        if export_path:
            op.set_export_filename(export_path)
        state = {}
        op.connect("begin-print", self._on_begin_print, print_model, dark, state)
        op.connect("draw-page", self._on_draw_page, state)
        return op.run(action, parent_window)

    def _on_begin_print(self, op, context, print_model, dark, state):
        style_table = tagdefs.tag_style_props(dark)
        width, height = context.get_width(), context.get_height()
        state["width"] = width
        blocks = _build_blocks(context, style_table, print_model, width, height, dark)
        # Pango.LayoutLine keeps only a *weak* back-reference to its parent
        # Pango.Layout -- without this, `blocks` (and therefore every
        # Layout) would be garbage collected the moment this method
        # returns, and draw-page would run against dangling lines.
        state["blocks"] = blocks
        pages = _paginate(blocks, height)
        state["pages"] = pages
        op.set_n_pages(len(pages))

    def _on_draw_page(self, op, context, page_nr, state):
        cr = context.get_cairo_context()
        for entry in state["pages"][page_nr]:
            _draw_entry(cr, entry, state["width"])
