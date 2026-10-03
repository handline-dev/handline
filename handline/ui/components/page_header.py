"""The header every bridge page starts with: the title bar, then a row of tabs
(and, on a page reached from another, a Back button)."""
from __future__ import annotations

from textual.containers import Horizontal
from textual.widgets import Button, Header, Tab, Tabs


class TopBar(Horizontal):
    DEFAULT_CSS = """
    TopBar { height: 2; }
    TopBar #scope { width: 1fr; }
    TopBar #leave { width: auto; min-width: 0; height: 100%; margin: 0 1 0 1; }
    """


def page_header(tabs: list[tuple[str, str]], back: bool = False) -> tuple:
    """The widgets to yield at the top of a page: `tabs` is (label, id) pairs,
    the tab row is `#scope`, the Back button `#leave`."""
    row = Tabs(*(Tab(label, id=tid) for label, tid in tabs), id="scope")
    bar = TopBar(Button("‹ Back", id="leave", compact=True), row, id="topbar") if back \
        else TopBar(row, id="topbar")
    return Header(), bar
