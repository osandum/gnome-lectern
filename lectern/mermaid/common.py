"""The two things every diagram parser needs, kept out of any one of
them so the others don't have to import a type they don't use."""
import re

_BR_RE = re.compile(r"<br\s*/?>", re.I)


class Unsupported(Exception):
    """This source is mermaid we don't draw. The caller (renderer.py)
    falls back to rendering the fence as a highlighted code block."""


def parse_label(text):
    """Unquote a label and turn mermaid's `<br>` into a real newline.

    Mermaid's own labels are HTML, and `<br>` is the one piece of it that
    turns up in ordinary diagrams often enough to matter. Everything else
    is left exactly as written rather than half-stripped: showing a stray
    tag is at least visibly wrong, where quietly dropping the text inside
    one is not.
    """
    text = text.strip()
    if len(text) >= 2 and text[0] == '"' and text[-1] == '"':
        text = text[1:-1]
    return _BR_RE.sub("\n", text).strip()
