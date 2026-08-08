"""File I/O + Markdown parsing. Imports of markdown_it/mdit_py_plugins are
deliberately kept inside this module (not at lectern.main top level) so a
bare `lectern` launch with no file to parse doesn't pay for them.
"""
from gi.repository import Gio, GLib


class DocumentLoadError(Exception):
    pass


def make_parser():
    from markdown_it import MarkdownIt
    from mdit_py_plugins.footnote import footnote_plugin
    from mdit_py_plugins.tasklists import tasklists_plugin

    md = MarkdownIt("commonmark").enable(["table", "strikethrough"])
    md.use(footnote_plugin).use(tasklists_plugin, enabled=True, label=False)
    return md


class Document:
    """Owns the Gio.File, its raw text, and the parsed syntax tree for one
    open window. `load`/`reload` are synchronous -- Markdown files are small
    enough that this is not worth doing asynchronously.
    """

    def __init__(self, gfile: Gio.File):
        self.gfile = gfile
        self.text = ""
        self.tree = None
        self._parser = None

    @property
    def parser(self):
        if self._parser is None:
            self._parser = make_parser()
        return self._parser

    @property
    def path(self):
        return self.gfile.get_path()

    @property
    def basename(self):
        return self.gfile.get_basename()

    @property
    def parent_path(self):
        parent = self.gfile.get_parent()
        return parent.get_path() if parent else None

    def load(self):
        """(Re)read the file from disk and re-parse it. Raises
        DocumentLoadError on failure, leaving the previous self.text/tree
        untouched so callers can keep showing the last-good content.
        """
        from markdown_it.tree import SyntaxTreeNode

        try:
            ok, contents, _etag = self.gfile.load_contents(None)
        except GLib.Error as ex:
            raise DocumentLoadError(str(ex)) from ex
        if not ok:
            raise DocumentLoadError(f"Could not read {self.path}")
        text = contents.decode("utf-8", errors="replace")
        tokens = self.parser.parse(text)
        self.text = text
        self.tree = SyntaxTreeNode(tokens)
        return self.tree

    reload = load

    def word_count(self):
        return len(self.text.split())

    def reading_time_minutes(self):
        return max(1, self.word_count() // 200)  # ~200 wpm, a common estimate

    def size_bytes(self):
        try:
            info = self.gfile.query_info(
                Gio.FILE_ATTRIBUTE_STANDARD_SIZE, Gio.FileQueryInfoFlags.NONE, None
            )
            return info.get_size()
        except GLib.Error:
            return len(self.text.encode("utf-8"))
