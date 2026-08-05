"""Walks a markdown-it-py SyntaxTreeNode tree into a Gtk.TextBuffer.

Every block-level emission also appends a PrintItem to `self.print_model`,
built in the same pass -- this is the fix for the fact that
Gtk.TextChildAnchor-embedded tables (and separators) are invisible to both
buffer-native find and any attempt to reconstruct print content by
re-walking the buffer afterward. Printing (see printing.py) works off
print_model exclusively, never off the live buffer.
"""
import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk

from . import tags as tagdefs
from . import tables
from . import highlighting

# Inline node types that just wrap their children in one more tag, with no
# other behavior -- driven by a table instead of three near-identical
# elif branches in _walk_inline.
_SIMPLE_INLINE_WRAP_TAGS = {"strong": "strong", "em": "em", "s": "strike"}


def _task_checkbox_state(html_inline_content):
    """Recognize mdit_py_plugins.tasklists' injected checkbox markup (an
    `<input class="task-list-item-checkbox" ...>` html_inline token) and
    return True/False for checked/unchecked, or None if this html_inline
    isn't one -- the one place that knows what that plugin's markup looks
    like, rather than an inline sniff repeated wherever it's needed."""
    if "task-list-item-checkbox" not in html_inline_content:
        return None
    return "checked" in html_inline_content


class PrintItem:
    __slots__ = ("kind", "runs", "block_tags", "rows", "language")

    def __init__(self, kind, runs=None, block_tags=None, rows=None, language=None):
        self.kind = kind
        self.runs = runs or []
        self.block_tags = block_tags or []
        self.rows = rows
        self.language = language


class RenderCtx:
    __slots__ = ("block_tags",)

    def __init__(self, block_tags=None):
        self.block_tags = block_tags or []

    def push_block(self, tag_name):
        return RenderCtx(self.block_tags + [tag_name])


class MarkdownRenderer:
    """Stateful per-render helper -- create one, call render() once, read
    print_model/dispatch_targets/footnote marks off it afterward. A fresh
    instance is used for every reload so nothing leaks between renders.
    """

    def __init__(self):
        self.tag_table = None
        self.dispatch_targets = {}      # tag name -> {"type": ..., ...}
        self.print_model = []
        self._footnote_ref_marks = {}   # label -> mark name
        self._footnote_def_marks = {}   # label -> mark name
        self._pending_anchors = []      # (anchor, widget) drained by caller
        self._instance_counter = 0

    # -- public API ---------------------------------------------------

    def render(self, tree, buffer):
        self.tag_table = buffer.get_tag_table()
        it = buffer.get_start_iter()
        self._walk_block(tree, buffer, it, RenderCtx())

    def attach_pending_widgets(self, textview):
        """Must be called after render() and after the buffer is assigned
        to the view -- Gtk.TextView.add_child_at_anchor requires a realized
        text view/buffer pairing that create_child_anchor doesn't."""
        for anchor, widget in self._pending_anchors:
            textview.add_child_at_anchor(widget, anchor)
        self._pending_anchors = []

    def footnote_def_mark_name(self, label):
        return self._footnote_def_marks.get(label)

    def footnote_ref_mark_name(self, label):
        return self._footnote_ref_marks.get(label)

    def target_at_iter(self, it):
        """Resolve the dispatch target (link/footnote) at a buffer
        position, or None. Window.py's click and hover handlers both call
        this instead of each re-scanning the iter's tags themselves."""
        for tag in it.get_tags():
            target = self.dispatch_targets.get(tag.get_property("name") or "")
            if target is not None:
                return target
        return None

    # -- instance-tag bookkeeping --------------------------------------

    def _new_instance_tag(self, prefix):
        self._instance_counter += 1
        name = f"{prefix}-{self._instance_counter}"
        tagdefs.ensure_instance_tag(self.tag_table, name)
        return name

    def _emit(self, buffer, it, text, buffer_tags, run_tags, runs):
        if not text:
            return
        buffer.insert_with_tags_by_name(it, text, *buffer_tags)
        runs.append((text, run_tags))

    # -- block-level walk ------------------------------------------------

    def _walk_block(self, node, buffer, it, ctx):
        for child in node.children:
            self._walk_block_node(child, buffer, it, ctx)

    def _walk_block_node(self, child, buffer, it, ctx):
        t = child.type
        if t == "heading":
            level = int(child.tag[1])
            self._emit_simple_paragraph(child, buffer, it, ctx, extra_tag=f"heading{level}")
        elif t == "paragraph":
            self._emit_simple_paragraph(child, buffer, it, ctx)
        elif t == "blockquote":
            self._walk_block(child, buffer, it, ctx.push_block("blockquote"))
        elif t in ("bullet_list", "ordered_list"):
            self._walk_list(child, buffer, it, ctx, ordered=(t == "ordered_list"))
        elif t == "fence":
            self._emit_code_block(child, buffer, it, ctx)
        elif t == "hr":
            self._emit_hr(buffer, it, ctx)
        elif t == "table":
            self._emit_table(child, buffer, it, ctx)
        elif t == "footnote_block":
            self._walk_footnote_block(child, buffer, it, ctx)
        # Anything else (raw html_block, etc.) is silently skipped -- v1
        # scope cut, not requested.

    def _emit_simple_paragraph(self, node, buffer, it, ctx, extra_tag=None):
        inline_tags = [extra_tag] if extra_tag else []
        self._emit_paragraph_body(node, buffer, it, ctx, runs=[], leading_inline_tags=inline_tags)

    def _emit_paragraph_body(self, para_node, buffer, it, ctx, runs, leading_inline_tags=()):
        """Walk a paragraph's inline content (plus any trailing footnote
        back-reference arrow) into `buffer`/`runs`, terminate the line, and
        record the PrintItem. `runs` may already hold a leading marker run
        (list bullet/number, footnote-def label) inserted by the caller --
        shared by _emit_simple_paragraph and _walk_block_with_marker so
        this sequence exists in exactly one place."""
        if para_node.children:
            self._walk_inline(para_node.children[0], buffer, it, ctx.block_tags, list(leading_inline_tags), runs)
            anchor = self._paragraph_footnote_anchor(para_node)
            if anchor is not None:
                self._emit_footnote_back(anchor, buffer, it, ctx.block_tags, [], runs)
        buffer.insert(it, "\n")
        self.print_model.append(PrintItem("paragraph", runs=runs, block_tags=list(ctx.block_tags)))

    @staticmethod
    def _paragraph_footnote_anchor(para_node):
        """footnote_anchor (the back-to-reference arrow) is emitted by
        mdit_py_plugins as a direct child of the paragraph, a sibling of
        the paragraph's `inline` node -- not nested inside it."""
        for extra in para_node.children[1:]:
            if extra.type == "footnote_anchor":
                return extra
        return None

    def _emit_code_block(self, node, buffer, it, ctx):
        info = (node.info or "").strip()
        language = info.split()[0] if info else None
        code = node.content
        runs = []
        for text, pyg_tag in highlighting.highlight_runs(code, language):
            run_tags = ["code-block"] + ([pyg_tag] if pyg_tag else [])
            buffer.insert_with_tags_by_name(it, text, *(ctx.block_tags + run_tags))
            runs.append((text, run_tags))
        if not code.endswith("\n"):
            buffer.insert(it, "\n")
        self.print_model.append(
            PrintItem("code-block", runs=runs, block_tags=list(ctx.block_tags), language=language)
        )

    def _emit_hr(self, buffer, it, ctx):
        anchor = buffer.create_child_anchor(it)
        separator = Gtk.Separator(hexpand=True)
        separator.set_margin_top(8)
        separator.set_margin_bottom(8)
        self._pending_anchors.append((anchor, separator))
        buffer.insert(it, "\n")
        self.print_model.append(PrintItem("hr", block_tags=list(ctx.block_tags)))

    def _emit_table(self, node, buffer, it, ctx):
        widget, rows = tables.build_table_widget(node)
        anchor = buffer.create_child_anchor(it)
        self._pending_anchors.append((anchor, widget))
        buffer.insert(it, "\n")
        self.print_model.append(PrintItem("table", rows=rows, block_tags=list(ctx.block_tags)))

    # -- lists / task lists -----------------------------------------------

    def _walk_list(self, node, buffer, it, ctx, ordered):
        level = sum(1 for t in ctx.block_tags if t.startswith("list-indent-"))
        indent_tag = tagdefs.ensure_list_indent_tag(self.tag_table, level)
        child_ctx = ctx.push_block(indent_tag.get_property("name"))
        for item in node.children:
            self._walk_list_item(item, buffer, it, child_ctx, ordered)

    def _walk_list_item(self, item, buffer, it, ctx, ordered):
        is_task = bool(item.attrs) and "task-list-item" in str(item.attrs.get("class", ""))
        block_children = list(item.children)
        if is_task:
            checked = self._detect_task_checked(block_children)
            marker_text = "☑" if checked else "☐"  # ☑ / ☐
            marker_tag = "task-checked-glyph" if checked else "task-unchecked-glyph"
        elif ordered:
            marker_text = f"{item.info}. "
            marker_tag = "list-marker"
        else:
            marker_text = "•  "  # •
            marker_tag = "list-marker"
        self._walk_block_with_marker(block_children, buffer, it, ctx, marker_text, marker_tag)

    def _detect_task_checked(self, block_children):
        if not block_children or block_children[0].type != "paragraph":
            return False
        para = block_children[0]
        if not para.children:
            return False
        for child in para.children[0].children:
            if child.type == "html_inline":
                state = _task_checkbox_state(child.content)
                if state is not None:
                    return state
        return False

    def _walk_block_with_marker(self, block_children, buffer, it, ctx, marker_text, marker_tag):
        """Shared by list items and footnote definitions: render `marker_text`
        immediately before the first paragraph's content (same visual line),
        then walk any remaining block children normally."""
        if block_children and block_children[0].type == "paragraph":
            first, rest = block_children[0], block_children[1:]
            runs = [(marker_text, [marker_tag])]
            buffer.insert_with_tags_by_name(it, marker_text, *(ctx.block_tags + [marker_tag]))
            self._emit_paragraph_body(first, buffer, it, ctx, runs)
        else:
            buffer.insert_with_tags_by_name(it, marker_text + "\n", *(ctx.block_tags + [marker_tag]))
            self.print_model.append(
                PrintItem("paragraph", runs=[(marker_text, [marker_tag])], block_tags=list(ctx.block_tags))
            )
            rest = block_children
        for child in rest:
            self._walk_block_node(child, buffer, it, ctx)

    # -- footnotes -------------------------------------------------------

    def _walk_footnote_block(self, node, buffer, it, ctx):
        if not node.children:
            return
        self._emit_hr(buffer, it, ctx)
        for footnote in node.children:
            label = str(footnote.meta.get("label", ""))
            mark_name = f"footnote-def-{label}"
            if buffer.get_mark(mark_name) is None:
                buffer.create_mark(mark_name, it, True)
            self._footnote_def_marks[label] = mark_name
            self._walk_block_with_marker(list(footnote.children), buffer, it, ctx, f"{label}. ", "list-marker")

    def _emit_footnote_ref(self, node, buffer, it, block_tags, inline_tags, runs):
        label = str(node.meta.get("label", ""))
        mark_name = f"footnote-src-{label}"
        if buffer.get_mark(mark_name) is None:
            buffer.create_mark(mark_name, it, True)
        self._footnote_ref_marks[label] = mark_name
        tagname = self._new_instance_tag("footnote-ref")
        self.dispatch_targets[tagname] = {"type": "footnote-jump", "label": label}
        run_tags = inline_tags + ["footnote-ref", tagname]
        self._emit(buffer, it, label, block_tags + run_tags, run_tags, runs)

    def _emit_footnote_back(self, node, buffer, it, block_tags, inline_tags, runs):
        label = str(node.meta.get("label", ""))
        tagname = self._new_instance_tag("footnote-back")
        self.dispatch_targets[tagname] = {"type": "footnote-back", "label": label}
        run_tags = inline_tags + ["footnote-ref", tagname]
        self._emit(buffer, it, " ↩", block_tags + run_tags, run_tags, runs)

    # -- inline walk -------------------------------------------------------

    def _walk_inline(self, node, buffer, it, block_tags, inline_tags, runs):
        for child in node.children:
            t = child.type
            if t == "text":
                self._emit(buffer, it, child.content, block_tags + inline_tags, inline_tags, runs)
            elif t == "softbreak":
                self._emit(buffer, it, " ", block_tags + inline_tags, inline_tags, runs)
            elif t == "hardbreak":
                self._emit(buffer, it, "\n", block_tags + inline_tags, inline_tags, runs)
            elif t in _SIMPLE_INLINE_WRAP_TAGS:
                wrap_tag = _SIMPLE_INLINE_WRAP_TAGS[t]
                self._walk_inline(child, buffer, it, block_tags, inline_tags + [wrap_tag], runs)
            elif t == "code_inline":
                new_tags = inline_tags + ["code-inline"]
                self._emit(buffer, it, child.content, block_tags + new_tags, new_tags, runs)
            elif t == "link":
                tagname = self._new_instance_tag("link")
                self.dispatch_targets[tagname] = {"type": "url", "href": child.attrs.get("href", "")}
                self._walk_inline(child, buffer, it, block_tags, inline_tags + ["link", tagname], runs)
            elif t == "footnote_ref":
                self._emit_footnote_ref(child, buffer, it, block_tags, inline_tags, runs)
            elif t == "footnote_anchor":
                self._emit_footnote_back(child, buffer, it, block_tags, inline_tags, runs)
            elif t == "html_inline":
                # Raw HTML isn't rendered in v1. This is also what makes the
                # task-list checkbox <input> injected by mdit_py_plugins
                # disappear cleanly -- the visible glyph comes from the
                # marker text the list-item walker inserts instead.
                pass
            else:
                content = getattr(child, "content", "")
                if content:
                    self._emit(buffer, it, content, block_tags + inline_tags, inline_tags, runs)
