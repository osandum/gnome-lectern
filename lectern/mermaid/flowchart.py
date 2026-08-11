"""`flowchart` / `graph`: source -> node-and-edge model -> Scene.

The layout is a cut-down Sugiyama: rank the nodes into layers along the
diagram's direction, order each layer to unpick crossings, then assign
positions across the layer. That is the same shape of algorithm dagre
(what mermaid itself uses) implements, minus the parts that need a solver
-- ordering is barycentre sweeps rather than an optimal ordering, and
cross-axis placement is priority-based rather than a network simplex. The
difference shows up as slightly wider spacing and the odd avoidable
crossing on dense graphs, not as unreadable output.

Parsing is deliberately strict: anything this module doesn't understand
raises Unsupported, and the caller falls back to rendering the fence as a
highlighted code block. A diagram drawn *wrongly* is worse than one not
drawn at all -- the reader can't tell a mis-parse from what the author
meant, whereas the code block is at least honest.
"""
import re

from . import scene as sc

# -- geometry, in nominal 96dpi pixels -------------------------------------

NODE_PAD_X = 14.0
NODE_PAD_Y = 9.0
MIN_NODE_W = 46.0
RANK_GAP = 46.0        # between one layer and the next
NODE_GAP = 26.0        # between siblings within a layer
VIRTUAL_W = 14.0       # cross-axis room reserved for an edge passing a layer
LABEL_PAD = 3.0
CORNER_RADIUS = 7.0
EDGE_LABEL_FONT_SCALE = 0.92

_DIRECTIONS = {"TD": "TD", "TB": "TD", "BT": "BT", "LR": "LR", "RL": "RL"}


class Unsupported(Exception):
    """This source is mermaid we don't draw. Caller falls back to code."""


# -- parsing ---------------------------------------------------------------

_HEADER_RE = re.compile(r"^(?:flowchart|graph)(?:\s+(?P<dir>[A-Za-z]{2}))?\s*$")
_ID_RE = re.compile(r"[A-Za-z0-9_]+")
_IGNORED_STATEMENTS = ("style ", "classdef ", "class ", "click ", "linkstyle ",
                       "direction ", "%%")

# Shape delimiters, longest first -- "[[" has to be tried before "[", or
# every subroutine node parses as a rect whose text starts with "[". The
# four slanted forms share the "[/" and "[\" openings and differ only in
# how they close, so a candidate whose closing delimiter isn't found is
# skipped rather than treated as a syntax error (see _parse_node).
_SHAPES = [
    ("[[", "]]", "subroutine"),
    ("[(", ")]", "cylinder"),
    ("([", "])", "stadium"),
    ("((", "))", "circle"),
    ("{{", "}}", "hexagon"),
    ("[/", "/]", "parallelogram"),
    ("[\\", "\\]", "parallelogram-alt"),
    ("[/", "\\]", "trapezoid"),
    ("[\\", "/]", "trapezoid-alt"),
    ("[", "]", "rect"),
    ("(", ")", "round"),
    ("{", "}", "diamond"),
    (">", "]", "asymmetric"),
]

# Mermaid spells a labelled link two ways: `A -- text --> B` and
# `A -->|text| B`. Rewriting the first into the second up front means the
# link scanner below only has to know one of them.
_MID_LABEL_FORMS = [
    (re.compile(r"--\s+(?P<text>.+?)\s+(?P<tail>-{2,}[>ox]?)"), "solid"),
    (re.compile(r"-\.\s+(?P<text>.+?)\s+(?P<tail>\.-+[>ox]?)"), "dotted"),
    (re.compile(r"==\s+(?P<text>.+?)\s+(?P<tail>={2,}[>ox]?)"), "thick"),
]

_LINK_RE = re.compile(r"""
    \s*
    (?P<tail>[<ox])?
    (?:
        (?P<dotted>-\.-+|-\.)
      | (?P<thick>={2,})
      | (?P<solid>-{2,})
    )
    (?P<head>[>ox])?
    (?:\|(?P<label>[^|]*)\|)?
    \s*
""", re.X)


class FlowNode:
    __slots__ = ("key", "text", "shape", "w", "h", "x", "y", "rank", "order")

    def __init__(self, key, text, shape):
        self.key = key
        self.text = text
        self.shape = shape
        self.w = self.h = 0.0
        self.x = self.y = 0.0      # centre, filled in by layout
        self.rank = 0
        self.order = 0

    @property
    def virtual(self):
        return False


class FlowEdge:
    __slots__ = ("src", "dst", "label", "style", "head", "tail")

    def __init__(self, src, dst, label, style, head, tail):
        self.src = src
        self.dst = dst
        self.label = label
        self.style = style      # solid | dotted | thick
        self.head = head        # arrow | circle | cross | None
        self.tail = tail


class _Virtual:
    """A placeholder node an edge is routed through when it spans more
    than one rank. Without them a long edge cuts straight across whatever
    happens to sit in the ranks between its ends."""
    __slots__ = ("key", "w", "h", "x", "y", "rank", "order")

    def __init__(self, key, rank):
        self.key = key
        self.w = self.h = VIRTUAL_W
        self.x = self.y = 0.0
        self.rank = rank
        self.order = 0

    @property
    def virtual(self):
        return True


class Flowchart:
    def __init__(self, direction, nodes, edges):
        self.direction = direction
        self.nodes = nodes          # ordered dict: key -> FlowNode
        self.edges = edges

    @property
    def vertical(self):
        return self.direction in ("TD", "BT")

    def build_scene(self, measurer):
        return _build_scene(self, measurer)


def _strip_comment(line):
    # `%%{init: ...}%%` directives and plain `%%` comments both go; a `%%`
    # inside a quoted label does not.
    out, quoted = [], False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '"':
            quoted = not quoted
        if not quoted and line.startswith("%%", i):
            break
        out.append(ch)
        i += 1
    return "".join(out)


def _split_statements(source):
    statements = []
    for raw_line in source.splitlines():
        line = _strip_comment(raw_line)
        # Semicolons separate statements; mermaid allows but doesn't
        # require them, so they're a separator here and not a terminator.
        for part in line.split(";"):
            part = part.strip()
            if part:
                statements.append(part)
    return statements


def _parse_label(text):
    """Unquote a label and turn mermaid's `<br>` into a real newline."""
    text = text.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1]
    return re.sub(r"<br\s*/?>", "\n", text).strip()


def _parse_node(statement, pos):
    """Parse `ID`, `ID[text]`, `ID{text}`, ... at `pos`.

    Returns (key, text_or_None, shape_or_None, new_pos). A bare `ID` is a
    reference, which is why text is optional: `A --> B` declares both ends
    with no shape of their own, and a later `A[Real label]` fills it in.
    """
    match = _ID_RE.match(statement, pos)
    if not match:
        raise Unsupported(f"expected a node id at {statement[pos:pos + 12]!r}")
    key = match.group(0)
    pos = match.end()
    opened = False
    for open_delim, close_delim, shape in _SHAPES:
        if not statement.startswith(open_delim, pos):
            continue
        opened = True
        start = pos + len(open_delim)
        if statement.startswith('"', start):
            end = statement.find('"', start + 1)
            if end < 0:
                raise Unsupported("unterminated quoted label")
            close = statement.find(close_delim, end + 1)
            text = statement[start:end + 1]
        else:
            close = statement.find(close_delim, start)
            text = statement[start:close] if close >= 0 else ""
        if close < 0:
            continue  # try the next shape sharing this opening delimiter
        return key, _parse_label(text), shape, close + len(close_delim)
    if opened:
        raise Unsupported(f"unterminated shape on node {key}")
    return key, None, None, pos


def _canonical_link(style, tail):
    """The `-->`-style spelling of a link whose label was written between
    its dashes, so the one link scanner sees a single form."""
    head = tail[-1] if tail and tail[-1] in ">ox" else ""
    body = {"solid": "--", "dotted": "-.-", "thick": "=="}[style]
    if style == "dotted" and not head:
        return "-.-"
    return body + head if head else {"solid": "---", "thick": "==="}[style]


def _normalize_mid_labels(statement):
    for pattern, style in _MID_LABEL_FORMS:
        while True:
            match = pattern.search(statement)
            if not match:
                break
            label = match.group("text").strip()
            replacement = f"{_canonical_link(style, match.group('tail'))}|{label}|"
            statement = statement[:match.start()] + replacement + statement[match.end():]
    return statement


_HEAD_GLYPHS = {">": "arrow", "o": "circle", "x": "cross"}


def _parse_link(statement, pos):
    match = _LINK_RE.match(statement, pos)
    if not match:
        return None
    style = "dotted" if match.group("dotted") else "thick" if match.group("thick") else "solid"
    label = _parse_label(match.group("label")) if match.group("label") else ""
    head = _HEAD_GLYPHS.get(match.group("head") or "")
    tail = "arrow" if match.group("tail") == "<" else _HEAD_GLYPHS.get(match.group("tail") or "")
    return style, label, head, tail, match.end()


def parse(source):
    """Mermaid flowchart source -> Flowchart. Raises Unsupported."""
    statements = _split_statements(source)
    if not statements:
        raise Unsupported("empty diagram")
    header = _HEADER_RE.match(statements[0])
    if not header:
        raise Unsupported("not a flowchart header")
    direction = _DIRECTIONS.get((header.group("dir") or "TD").upper())
    if direction is None:
        raise Unsupported(f"unknown direction {header.group('dir')!r}")

    nodes = {}
    edges = []

    def touch(key, text, shape):
        node = nodes.get(key)
        if node is None:
            # A node first seen as a bare reference gets its id as its
            # label, which is what mermaid shows until a shaped
            # declaration turns up.
            node = nodes[key] = FlowNode(key, text if text is not None else key, shape or "rect")
        elif text is not None:
            node.text = text
            node.shape = shape or node.shape
        return node

    for statement in statements[1:]:
        lowered = statement.lower()
        if lowered.startswith("subgraph") or lowered == "end":
            # Deliberately unsupported rather than flattened: dropping the
            # grouping would silently redraw the author's diagram as a
            # different one.
            raise Unsupported("subgraphs are not supported")
        if any(lowered.startswith(prefix) for prefix in _IGNORED_STATEMENTS):
            continue
        if "&" in statement:
            raise Unsupported("`&` node lists are not supported")
        statement = _normalize_mid_labels(statement)
        pos = 0
        key, text, shape, pos = _parse_node(statement, 0)
        touch(key, text, shape)
        while pos < len(statement):
            link = _parse_link(statement, pos)
            if link is None:
                raise Unsupported(f"unparsed trailing {statement[pos:][:16]!r}")
            style, label, head, tail, pos = link
            next_key, text, shape, pos = _parse_node(statement, pos)
            touch(next_key, text, shape)
            edges.append(FlowEdge(key, next_key, label, style, head, tail))
            key = next_key
    if not nodes:
        raise Unsupported("no nodes")
    return Flowchart(direction, nodes, edges)


# -- sizing ----------------------------------------------------------------

def _size_node(node, measurer):
    text_w, text_h = measurer.size(node.text)
    width = max(MIN_NODE_W, text_w + 2 * NODE_PAD_X)
    height = text_h + 2 * NODE_PAD_Y
    shape = node.shape
    if shape == "diamond":
        # A rhombus only contains its inscribed rectangle if it's grown by
        # roughly the text box's own half-diagonal in each axis; anything
        # tighter clips the corners of long labels.
        width = text_w + 2 * NODE_PAD_X + text_h
        height = text_h * 2 + NODE_PAD_Y
    elif shape == "circle":
        diameter = max(text_w, text_h) * 1.3 + 2 * NODE_PAD_Y
        width = height = max(diameter, MIN_NODE_W)
    elif shape == "hexagon":
        width += height * 0.5
    elif shape == "subroutine":
        width += 16
    elif shape == "asymmetric":
        width += 14
    elif shape == "cylinder":
        height += 12
    elif shape in ("parallelogram", "parallelogram-alt", "trapezoid", "trapezoid-alt"):
        # The slanted sides eat into the text's own box at top or bottom,
        # so the label needs the width back.
        width += height * 0.5
    node.w, node.h = width, height


# -- ranking, ordering, positioning ----------------------------------------

def _rank_nodes(chart):
    """Longest-path ranking over the edges that aren't back edges.

    Cycles are normal in flowcharts (any retry loop is one), so the back
    edges found by a DFS are left out of the ranking and simply drawn
    against the flow, the way dagre does it. Ranking with them included
    would either not terminate or collapse the loop into one rank.
    """
    succ = {key: [] for key in chart.nodes}
    for edge in chart.edges:
        if edge.src != edge.dst:
            succ[edge.src].append(edge.dst)

    WHITE, GREY, BLACK = 0, 1, 2
    color = {key: WHITE for key in chart.nodes}
    back = set()

    def visit(start):
        # Iterative DFS: a deep chain of nodes would otherwise be a
        # recursion depth away from crashing the viewer.
        stack = [(start, iter(succ[start]))]
        color[start] = GREY
        while stack:
            node, children = stack[-1]
            advanced = False
            for child in children:
                if color[child] == GREY:
                    back.add((node, child))
                elif color[child] == WHITE:
                    color[child] = GREY
                    stack.append((child, iter(succ[child])))
                    advanced = True
                    break
            if not advanced:
                color[node] = BLACK
                stack.pop()

    for key in chart.nodes:
        if color[key] == WHITE:
            visit(key)

    forward = [(edge.src, edge.dst) for edge in chart.edges
               if edge.src != edge.dst and (edge.src, edge.dst) not in back]
    rank = {key: 0 for key in chart.nodes}
    # Relaxation rather than a topological sort: the forward set is
    # acyclic, so |V| sweeps are guaranteed to settle it, and this needs
    # no second graph structure to walk.
    for _ in range(len(chart.nodes)):
        changed = False
        for src, dst in forward:
            if rank[dst] < rank[src] + 1:
                rank[dst] = rank[src] + 1
                changed = True
        if not changed:
            break
    for key, node in chart.nodes.items():
        node.rank = rank[key]
    return back


def _build_layers(chart, back_edges):
    """Ranked layers with virtual nodes spliced into every long edge.

    Returns (layers, routes) where routes maps each edge to the list of
    virtual keys it passes through, source-to-target.
    """
    cells = dict(chart.nodes)
    routes = {}
    counter = 0
    for index, edge in enumerate(chart.edges):
        src, dst = chart.nodes[edge.src], chart.nodes[edge.dst]
        low, high = sorted((src.rank, dst.rank))
        if high - low <= 1:
            routes[index] = []
            continue
        chain = []
        for rank in range(low + 1, high):
            counter += 1
            key = f"\0v{counter}"
            cells[key] = _Virtual(key, rank)
            chain.append(key)
        if src.rank > dst.rank:
            chain.reverse()
        routes[index] = chain
    max_rank = max(cell.rank for cell in cells.values())
    layers = [[] for _ in range(max_rank + 1)]
    for cell in cells.values():
        layers[cell.rank].append(cell)
    return cells, layers, routes


def _neighbour_map(chart, cells, routes):
    """Adjacency over the *expanded* graph (virtual nodes included), which
    is what ordering and placement both work on."""
    up = {key: [] for key in cells}
    down = {key: [] for key in cells}
    for index, edge in enumerate(chart.edges):
        path = [edge.src] + routes[index] + [edge.dst]
        for a, b in zip(path, path[1:]):
            if cells[a].rank == cells[b].rank:
                continue
            lower, upper = (a, b) if cells[a].rank < cells[b].rank else (b, a)
            down[lower].append(upper)
            up[upper].append(lower)
    return up, down


def _order_layers(layers, up, down, passes=4):
    """Barycentre sweeps: repeatedly reorder each layer by the average
    position of its neighbours in the layer just processed. Two or three
    passes get most of the crossings a flowchart-sized graph has; more
    stops paying."""
    for cell_list in layers:
        for position, cell in enumerate(cell_list):
            cell.order = position

    def sweep(neighbours, ordered_layers):
        for cell_list in ordered_layers:
            def barycentre(cell):
                positions = [n.order for n in (cells_by_key[k] for k in neighbours[cell.key])]
                # A cell with no neighbours in the reference layer keeps
                # its current position rather than being swept to the
                # front, which is what a 0 barycentre would do.
                return sum(positions) / len(positions) if positions else cell.order
            cell_list.sort(key=barycentre)
            for position, cell in enumerate(cell_list):
                cell.order = position

    cells_by_key = {cell.key: cell for layer in layers for cell in layer}
    for index in range(passes):
        if index % 2 == 0:
            sweep(up, layers[1:])
        else:
            sweep(down, layers[-2::-1])


def _cross_size(cell, vertical):
    return cell.w if vertical else cell.h


def _rank_size(cell, vertical):
    return cell.h if vertical else cell.w


def _place(layers, up, down, vertical):
    """Assign every cell a centre on both axes.

    Rank axis: layers are stacked by their tallest (widest) member.
    Cross axis: pack each layer, then pull cells toward the average of
    their neighbours in adjacent layers and re-separate. Sweeping in both
    directions keeps a node with only successors (a source) as
    well-aligned as one with only predecessors.
    """
    cells_by_key = {cell.key: cell for layer in layers for cell in layer}
    positions = {}

    rank_pos = 0.0
    for layer in layers:
        extent = max((_rank_size(cell, vertical) for cell in layer), default=0.0)
        for cell in layer:
            positions[cell.key] = [0.0, rank_pos + extent / 2]
        rank_pos += extent + RANK_GAP

    def pack(layer):
        cursor = 0.0
        for cell in layer:
            half = _cross_size(cell, vertical) / 2
            positions[cell.key][0] = max(positions[cell.key][0], cursor + half)
            cursor = positions[cell.key][0] + half + NODE_GAP

    def separate(layer):
        """Push apart in both directions so a pulled cell never overlaps
        its neighbour, and the pull isn't systematically biased toward
        whichever end the pass started from."""
        for index in range(1, len(layer)):
            prev, cell = layer[index - 1], layer[index]
            minimum = (positions[prev.key][0] + _cross_size(prev, vertical) / 2
                       + NODE_GAP + _cross_size(cell, vertical) / 2)
            positions[cell.key][0] = max(positions[cell.key][0], minimum)
        for index in range(len(layer) - 2, -1, -1):
            cell, following = layer[index], layer[index + 1]
            maximum = (positions[following.key][0] - _cross_size(following, vertical) / 2
                       - NODE_GAP - _cross_size(cell, vertical) / 2)
            positions[cell.key][0] = min(positions[cell.key][0], maximum)

    for layer in layers:
        pack(layer)

    for iteration in range(4):
        neighbours = up if iteration % 2 == 0 else down
        ordered = layers[1:] if iteration % 2 == 0 else layers[-2::-1]
        for layer in ordered:
            for cell in layer:
                keys = neighbours[cell.key]
                if keys:
                    positions[cell.key][0] = sum(
                        positions[cells_by_key[k].key][0] for k in keys) / len(keys)
            layer.sort(key=lambda cell: positions[cell.key][0])
            separate(layer)

    # Nothing above pins the diagram to the origin; shift it there so the
    # scene's own box starts at (0, 0).
    lowest = min(positions[cell.key][0] - _cross_size(cell, vertical) / 2
                 for layer in layers for cell in layer)
    for layer in layers:
        for cell in layer:
            cross, rank = positions[cell.key]
            cross -= lowest
            if vertical:
                cell.x, cell.y = cross, rank
            else:
                cell.x, cell.y = rank, cross


def _mirror(cells, direction, width, height):
    """BT and RL are TD and LR drawn backwards along the rank axis."""
    for cell in cells.values():
        if direction == "BT":
            cell.y = height - cell.y
        elif direction == "RL":
            cell.x = width - cell.x


# -- edge geometry ---------------------------------------------------------

def _boundary_point(cell, toward):
    """Where the line from `cell`'s centre to `toward` leaves the cell.

    Everything is clipped against the bounding box (circles against their
    ellipse), which is close enough for the diamond and hexagon that the
    couple of pixels of overshoot land under the arrowhead anyway.
    """
    cx, cy = cell.x, cell.y
    dx, dy = toward[0] - cx, toward[1] - cy
    if dx == 0 and dy == 0:
        return cx, cy
    if getattr(cell, "shape", None) == "circle":
        radius_x, radius_y = cell.w / 2, cell.h / 2
        norm = ((dx / radius_x) ** 2 + (dy / radius_y) ** 2) ** 0.5
        return cx + dx / norm, cy + dy / norm
    half_w, half_h = cell.w / 2, cell.h / 2
    scale = min(half_w / abs(dx) if dx else float("inf"),
                half_h / abs(dy) if dy else float("inf"))
    return cx + dx * scale, cy + dy * scale


def _edge_points(chart, cells, routes, index, edge):
    src, dst = cells[edge.src], cells[edge.dst]
    middle = [(cells[key].x, cells[key].y) for key in routes[index]]
    first_target = middle[0] if middle else (dst.x, dst.y)
    last_source = middle[-1] if middle else (src.x, src.y)
    start = _boundary_point(src, first_target)
    end = _boundary_point(dst, last_source)
    return [start] + middle + [end]


def _self_loop_points(cell):
    """A short arc out of the node's right side and back into its top --
    a self-edge has nowhere else to go, and mermaid draws it the same
    way."""
    right = cell.x + cell.w / 2
    top = cell.y - cell.h / 2
    reach = max(18.0, cell.h * 0.5)
    return [
        (right, cell.y - cell.h * 0.15),
        (right + reach, cell.y - cell.h * 0.15),
        (right + reach, top - reach * 0.6),
        (cell.x + cell.w * 0.2, top - reach * 0.6),
        (cell.x + cell.w * 0.2, top),
    ]


def _label_position(points):
    """Midpoint of the edge's longest segment -- the place with the most
    room for a plate, rather than the geometric middle, which on an
    L-shaped route lands on the corner."""
    best, best_length = None, -1.0
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        if length > best_length:
            best, best_length = ((x1 + x2) / 2, (y1 + y2) / 2), length
    return best


# -- scene assembly --------------------------------------------------------

_DASHED = {"dotted": True, "solid": False, "thick": False}
_STROKE_WIDTH = {"thick": 2.4, "solid": 1.4, "dotted": 1.4}


def _node_shapes(node):
    """The scene primitives for one node's outline, sans its text."""
    x = node.x - node.w / 2
    y = node.y - node.h / 2
    w, h = node.w, node.h
    shape = node.shape
    if shape == "circle":
        return [sc.Ellipse(x, y, w, h, fill=sc.NODE, stroke=sc.EDGE)]
    if shape == "diamond":
        return [sc.Polygon(
            [(node.x, y), (x + w, node.y), (node.x, y + h), (x, node.y)],
            fill=sc.NODE, stroke=sc.EDGE)]
    if shape == "hexagon":
        inset = min(h * 0.5, w * 0.3)
        return [sc.Polygon(
            [(x + inset, y), (x + w - inset, y), (x + w, node.y),
             (x + w - inset, y + h), (x + inset, y + h), (x, node.y)],
            fill=sc.NODE, stroke=sc.EDGE)]
    if shape in ("parallelogram", "parallelogram-alt", "trapezoid", "trapezoid-alt"):
        slant = min(h * 0.45, w * 0.28)
        top_left, top_right, bottom_left, bottom_right = {
            "parallelogram": (slant, 0, 0, slant),
            "parallelogram-alt": (0, slant, slant, 0),
            "trapezoid": (slant, slant, 0, 0),
            "trapezoid-alt": (0, 0, slant, slant),
        }[shape]
        return [sc.Polygon(
            [(x + top_left, y), (x + w - top_right, y),
             (x + w - bottom_right, y + h), (x + bottom_left, y + h)],
            fill=sc.NODE, stroke=sc.EDGE)]
    if shape == "asymmetric":
        inset = min(14.0, w * 0.25)
        return [sc.Polygon(
            [(x, y), (x + w - inset, y), (x + w, node.y), (x + w - inset, y + h), (x, y + h)],
            fill=sc.NODE, stroke=sc.EDGE)]
    if shape == "stadium":
        return [sc.Rect(x, y, w, h, radius=h / 2, fill=sc.NODE, stroke=sc.EDGE)]
    if shape == "round":
        return [sc.Rect(x, y, w, h, radius=CORNER_RADIUS, fill=sc.NODE, stroke=sc.EDGE)]
    if shape == "subroutine":
        return [
            sc.Rect(x, y, w, h, fill=sc.NODE, stroke=sc.EDGE),
            sc.Line([(x + 8, y), (x + 8, y + h)], stroke=sc.EDGE),
            sc.Line([(x + w - 8, y), (x + w - 8, y + h)], stroke=sc.EDGE),
        ]
    if shape == "cylinder":
        cap = 10.0
        return [
            sc.Rect(x, y, w, h, radius=cap * 0.9, fill=sc.NODE, stroke=sc.EDGE),
            sc.Line([(x, y + cap), (x + w, y + cap)], stroke=sc.EDGE),
        ]
    return [sc.Rect(x, y, w, h, fill=sc.NODE, stroke=sc.EDGE)]


def _build_scene(chart, measurer):
    for node in chart.nodes.values():
        _size_node(node, measurer)

    back_edges = _rank_nodes(chart)
    cells, layers, routes = _build_layers(chart, back_edges)
    up, down = _neighbour_map(chart, cells, routes)
    _order_layers(layers, up, down)
    _place(layers, up, down, chart.vertical)

    width = max((cell.x + cell.w / 2 for cell in cells.values()), default=0.0)
    height = max((cell.y + cell.h / 2 for cell in cells.values()), default=0.0)
    _mirror(cells, chart.direction, width, height)

    scene = sc.Scene(width, height, [], measurer.font_px)

    # Edges first so a node's fill covers the stub of any line that
    # overshot its boundary, rather than the line being drawn over the box.
    for index, edge in enumerate(chart.edges):
        if edge.src == edge.dst:
            points = _self_loop_points(cells[edge.src])
        else:
            points = _edge_points(chart, cells, routes, index, edge)
        scene.add(sc.Line(
            points,
            dashed=_DASHED[edge.style],
            width=_STROKE_WIDTH[edge.style],
            head=edge.head,
            tail=edge.tail,
        ))
        if edge.label:
            text_w, text_h = measurer.size(
                edge.label, wrap=False, scale=EDGE_LABEL_FONT_SCALE)
            cx, cy = _label_position(points)
            scene.add(sc.Rect(
                cx - text_w / 2 - LABEL_PAD, cy - text_h / 2 - LABEL_PAD,
                text_w + 2 * LABEL_PAD, text_h + 2 * LABEL_PAD,
                radius=3.0, fill=sc.LABEL_BG,
            ))
            scene.add(sc.Text(
                cx - text_w / 2, cy - text_h / 2, text_w, text_h,
                edge.label, color=sc.DIM, scale=EDGE_LABEL_FONT_SCALE,
            ))

    for node in chart.nodes.values():
        for shape in _node_shapes(node):
            scene.add(shape)
        text_w, text_h = measurer.size(node.text)
        scene.add(sc.Text(
            node.x - text_w / 2, node.y - text_h / 2, text_w, text_h, node.text))

    # Self loops arc above their node and edge labels overhang the
    # leftmost box, so the box the widget draws into comes from the shapes
    # themselves rather than from the node placement above.
    return scene.normalize()
