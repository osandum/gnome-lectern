"""Mermaid parsing and layout.

Parsing is pure data and needs nothing from GTK. Layout does need Pango
to measure text, but not a display -- it measures against an image
surface (see mermaid/text.py), so these run wherever the rest of the
suite does.

Geometry is asserted as *relations* -- this node is left of that one,
these two boxes don't overlap -- rather than as coordinates. The
coordinates depend on the font the machine happens to have, exactly as
test_layout.py's do, and a diagram that reads correctly is one whose
parts are in the right order and don't collide, not one whose nodes are
at particular pixels.
"""
import pytest

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from markdown_it.tree import SyntaxTreeNode

from lectern import mermaid
from lectern.document import make_parser
from lectern.mermaid import flowchart, scene as sc
from lectern.renderer import MarkdownRenderer
from lectern.tags import create_tag_table

_PARSER = make_parser()

SIMPLE = """flowchart LR
    A[Open file] --> B{Cached?}
    B -- yes --> C[Render]
    B -- no --> D[Parse] --> C
"""


def build(source):
    return mermaid.scene_for_source(source, mermaid.ui_font())


def texts(scene):
    return [shape.text for shape in scene.shapes if isinstance(shape, sc.Text)]


def boxes(scene):
    return [shape for shape in scene.shapes if isinstance(shape, (sc.Rect, sc.Ellipse))]


# -- parsing ---------------------------------------------------------------

def test_parses_nodes_edges_and_direction():
    chart = mermaid.parse(SIMPLE)
    assert chart.direction == "LR"
    assert list(chart.nodes) == ["A", "B", "C", "D"]
    assert chart.nodes["A"].text == "Open file"
    assert chart.nodes["B"].shape == "diamond"
    assert [(e.src, e.dst) for e in chart.edges] == [
        ("A", "B"), ("B", "C"), ("B", "D"), ("D", "C")]


def test_label_between_dashes_and_in_pipes_are_the_same_link():
    piped = mermaid.parse("flowchart TD\n  A -->|yes| B\n")
    dashed = mermaid.parse("flowchart TD\n  A -- yes --> B\n")
    for chart in (piped, dashed):
        assert len(chart.edges) == 1
        edge = chart.edges[0]
        assert (edge.label, edge.style, edge.head) == ("yes", "solid", "arrow")


@pytest.mark.parametrize("link, style, head", [
    ("-->", "solid", "arrow"),
    ("---", "solid", None),
    ("-.->", "dotted", "arrow"),
    ("-.-", "dotted", None),
    ("==>", "thick", "arrow"),
    ("===", "thick", None),
    ("--o", "solid", "circle"),
    ("--x", "solid", "cross"),
])
def test_link_styles_and_arrowheads(link, style, head):
    chart = mermaid.parse(f"flowchart TD\n  A {link} B\n")
    edge = chart.edges[0]
    assert (edge.style, edge.head) == (style, head)


def test_chain_declares_every_hop():
    chart = mermaid.parse("flowchart TD\n  A --> B --> C --> D\n")
    assert [(e.src, e.dst) for e in chart.edges] == [("A", "B"), ("B", "C"), ("C", "D")]


def test_bare_reference_takes_its_id_as_label_until_declared():
    chart = mermaid.parse("flowchart TD\n  A --> B\n  B[Real label]\n")
    assert chart.nodes["A"].text == "A"
    assert chart.nodes["B"].text == "Real label"


@pytest.mark.parametrize("source, shape", [
    ("A[rect]", "rect"),
    ("A(round)", "round"),
    ("A([stadium])", "stadium"),
    ("A[[subroutine]]", "subroutine"),
    ("A[(cylinder)]", "cylinder"),
    ("A((circle))", "circle"),
    ("A{diamond}", "diamond"),
    ("A{{hexagon}}", "hexagon"),
    ("A>asymmetric]", "asymmetric"),
    ("A[/parallelogram/]", "parallelogram"),
    ("A[\\alt\\]", "parallelogram-alt"),
    ("A[/trapezoid\\]", "trapezoid"),
    ("A[\\trap alt/]", "trapezoid-alt"),
])
def test_node_shapes(source, shape):
    chart = mermaid.parse(f"flowchart TD\n  {source}\n")
    assert chart.nodes["A"].shape == shape


def test_quoted_label_may_contain_delimiters():
    chart = mermaid.parse('flowchart TD\n  A["a [b] {c}"] --> B\n')
    assert chart.nodes["A"].text == "a [b] {c}"


def test_br_becomes_a_line_break():
    chart = mermaid.parse("flowchart TD\n  A[first<br/>second]\n")
    assert chart.nodes["A"].text == "first\nsecond"


def test_comments_and_presentational_statements_are_ignored():
    chart = mermaid.parse(
        "flowchart TD\n"
        "%% a comment\n"
        "  A --> B  %% trailing\n"
        "  style A fill:#f9f\n"
        "  classDef big font-size:20px\n"
        "  click A callback\n"
    )
    assert list(chart.nodes) == ["A", "B"]
    assert len(chart.edges) == 1


@pytest.mark.parametrize("source", [
    "gantt\n  title Nope\n",                       # a type we don't draw
    "sequenceDiagram\n  A ->> B: hi\n",            # ditto
    "flowchart TD\n  subgraph one\n  A\n  end\n",  # a construct we don't draw
    "flowchart TD\n  A --> B & C\n",
    "flowchart XY\n  A --> B\n",                   # unknown direction
    "flowchart TD\n  A[unterminated\n",
    "",
])
def test_unsupported_sources_raise(source):
    with pytest.raises(mermaid.Unsupported):
        mermaid.parse(source)


def test_subgraphs_are_refused_rather_than_flattened():
    # Silently dropping the grouping would draw a diagram the author
    # didn't write, with nothing to tell the reader it happened.
    with pytest.raises(mermaid.Unsupported):
        mermaid.parse("flowchart TD\n  subgraph s\n    A --> B\n  end\n  B --> C\n")


# -- layout ----------------------------------------------------------------

def test_scene_carries_every_label():
    scene = build(SIMPLE)
    assert set(texts(scene)) == {"Open file", "Cached?", "Render", "Parse", "yes", "no"}


def test_scene_box_contains_every_shape():
    scene = build(SIMPLE)
    for shape in scene.shapes:
        x0, y0, x1, y1 = sc._point_extents(shape)
        assert x0 >= 0 and y0 >= 0
        assert x1 <= scene.width and y1 <= scene.height


def test_node_boxes_do_not_overlap():
    chart = mermaid.parse(SIMPLE)
    mermaid.build_scene(chart, mermaid.ui_font())
    nodes = list(chart.nodes.values())
    for index, a in enumerate(nodes):
        for b in nodes[index + 1:]:
            gap_x = abs(a.x - b.x) - (a.w + b.w) / 2
            gap_y = abs(a.y - b.y) - (a.h + b.h) / 2
            assert gap_x > 0 or gap_y > 0, f"{a.key} overlaps {b.key}"


def test_direction_decides_which_axis_ranks_run_along():
    source = "flowchart {}\n  A --> B --> C\n"
    left_right = mermaid.parse(source.format("LR"))
    mermaid.build_scene(left_right, mermaid.ui_font())
    assert left_right.nodes["A"].x < left_right.nodes["B"].x < left_right.nodes["C"].x

    top_down = mermaid.parse(source.format("TD"))
    mermaid.build_scene(top_down, mermaid.ui_font())
    assert top_down.nodes["A"].y < top_down.nodes["B"].y < top_down.nodes["C"].y


def test_reversed_directions_mirror_the_rank_axis():
    bottom_top = mermaid.parse("flowchart BT\n  A --> B --> C\n")
    mermaid.build_scene(bottom_top, mermaid.ui_font())
    assert bottom_top.nodes["A"].y > bottom_top.nodes["B"].y > bottom_top.nodes["C"].y

    right_left = mermaid.parse("flowchart RL\n  A --> B --> C\n")
    mermaid.build_scene(right_left, mermaid.ui_font())
    assert right_left.nodes["A"].x > right_left.nodes["B"].x > right_left.nodes["C"].x


def test_a_cycle_still_ranks_and_terminates():
    # Every retry loop is a cycle; ranking has to treat the edge that
    # closes it as a back edge rather than looping forever on it.
    chart = mermaid.parse("flowchart TD\n  A --> B\n  B --> C\n  C --> A\n")
    mermaid.build_scene(chart, mermaid.ui_font())
    assert [chart.nodes[k].rank for k in "ABC"] == [0, 1, 2]


def test_long_edge_is_routed_through_the_ranks_it_spans():
    # A --> C skips a rank. Without a virtual node on rank 1 the line
    # would be drawn straight across whatever sits there.
    chart = mermaid.parse("flowchart TD\n  A --> B --> C\n  A --> C\n")
    scene = mermaid.build_scene(chart, mermaid.ui_font())
    lines = [shape for shape in scene.shapes if isinstance(shape, sc.Line)]
    assert any(len(line.points) > 2 for line in lines)


def test_wider_label_makes_a_wider_node():
    narrow = mermaid.parse("flowchart TD\n  A[x]\n")
    wide = mermaid.parse("flowchart TD\n  A[a much longer label than x]\n")
    mermaid.build_scene(narrow, mermaid.ui_font())
    mermaid.build_scene(wide, mermaid.ui_font())
    assert wide.nodes["A"].w > narrow.nodes["A"].w


def test_edge_label_is_drawn_on_a_plate():
    # The plate is what keeps a label legible where it crosses its own
    # edge -- without it the line runs through the text.
    scene = build("flowchart LR\n  A -->|yes| B\n")
    label = next(s for s in scene.shapes if isinstance(s, sc.Text) and s.text == "yes")
    plate = next(s for s in scene.shapes if isinstance(s, sc.Rect) and s.fill == sc.LABEL_BG)
    assert plate.x <= label.x and plate.y <= label.y
    assert plate.x + plate.w >= label.x + label.w
    assert plate.y + plate.h >= label.y + label.h


# -- renderer integration --------------------------------------------------

def render(markdown_text):
    tree = SyntaxTreeNode(_PARSER.parse(markdown_text))
    buffer = Gtk.TextBuffer(tag_table=create_tag_table(dark=False))
    renderer = MarkdownRenderer()
    renderer.render(tree, buffer)
    return renderer, buffer


def test_mermaid_fence_becomes_a_diagram_widget():
    renderer, _buffer = render("```mermaid\nflowchart LR\n  A --> B\n```\n")
    assert len(renderer.diagrams) == 1
    assert [item.kind for item in renderer.print_model] == ["diagram"]
    # Print keeps the parsed model, not the screen's scene: paper lays it
    # out again in the print font.
    assert renderer.print_model[0].diagram is not None
    assert len(renderer._pending_anchors) == 1


def test_unsupported_mermaid_falls_back_to_a_code_block():
    renderer, buffer = render("```mermaid\ngantt\n  title Nope\n```\n")
    assert renderer.diagrams == []
    assert [item.kind for item in renderer.print_model] == ["code-block"]
    start, end = buffer.get_bounds()
    assert "gantt" in buffer.get_text(start, end, False)


def test_other_fences_are_untouched():
    renderer, _buffer = render("```python\nx = 1\n```\n")
    assert renderer.diagrams == []
    assert [item.kind for item in renderer.print_model] == ["code-block"]
