from __future__ import annotations

from textual.containers import Horizontal


class ActionBar(Horizontal):
    """The row of action buttons every page keeps at its bottom, under the
    scrolling part and the line that says what came of the last action."""

    DEFAULT_CSS = """
    ActionBar { height: 1; padding: 0 2; margin-bottom: 1; }
    ActionBar Button { width: auto; min-width: 0; margin-right: 2; }
    """
