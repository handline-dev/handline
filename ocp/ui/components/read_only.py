"""A page of text over whatever is open."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.containers import VerticalScroll
from textual.widgets import Footer, Static, TabbedContent, TabPane

from ... import textsafe, tui


class ReadOnly(ModalScreen[None]):
    """`/knows`, `/done`, and an ACCOUNTS row opened: a page of text over
    whatever was open, in `tui.page()`'s shape - a title per section, then its
    rows. A single flat list is one section with no heading, and several
    sections are tabs. Textual's own scroll container and tab strip do the
    scrolling and the tab keys; a page has nothing to act on, so Esc closes it."""

    BINDINGS = [
        Binding("escape", "dismiss(None)", "back"),
        Binding("left", "tab(-1)", "prev tab", show=False),
        Binding("right,tab", "tab(1)", "next tab", show=False),
        Binding("j,down", "scroll(1)", "down", show=False),
        Binding("k,up", "scroll(-1)", "up", show=False),
        Binding("space", "page(1)", "page", show=False),
        Binding("b", "page(-1)", "page up", show=False),
        Binding("g", "edge(0)", "top", show=False),
        Binding("G", "edge(1)", "bottom", show=False),
        Binding("q,ctrl+c", "app.quit", "quit", show=False),
    ]
    DEFAULT_CSS = """
    ReadOnly #title { dock: top; height: 1; }
    ReadOnly TabbedContent { height: 1fr; }
    """

    def __init__(self, title: str, sections: list) -> None:
        super().__init__()
        if sections and not isinstance(sections[0], tuple):
            sections = [("", sections)]
        self.title_text = title
        self.sections = [(str(h), [str(r) for r in rows]) for h, rows in sections]

    def _body(self, rows: list[str]) -> VerticalScroll:
        lines = [ln for r in rows for ln in textsafe.drawable(r)]
        return VerticalScroll(Static(Text("\n".join(lines))))

    def compose(self) -> ComposeResult:
        yield Static(Text(self.title_text, style="bold"), id="title")
        if len(self.sections) == 1 and not self.sections[0][0]:
            yield self._body(self.sections[0][1])
        else:
            with TabbedContent():
                for i, (head, rows) in enumerate(self.sections):
                    with TabPane(head or "-", id=f"sec{i}"):
                        yield self._body(rows)
        yield Footer()

    def _pane(self) -> VerticalScroll:
        tabs = self.query(TabbedContent)
        if tabs:
            return self.query_one(f"#{tabs.first().active} VerticalScroll", VerticalScroll)
        return self.query_one(VerticalScroll)

    def action_tab(self, step: int) -> None:
        tabs = list(self.query(TabbedContent))
        if tabs:
            n = len(self.sections)
            cur = int(tabs[0].active.removeprefix("sec") or 0)
            tabs[0].active = f"sec{(cur + step) % n}"

    def action_scroll(self, step: int) -> None:
        self._pane().scroll_relative(y=step, animate=False)

    def action_page(self, step: int) -> None:
        pane = self._pane()
        (pane.scroll_page_down if step > 0 else pane.scroll_page_up)(animate=False)

    def action_edge(self, end: int) -> None:
        pane = self._pane()
        (pane.scroll_end if end else pane.scroll_home)(animate=False)
