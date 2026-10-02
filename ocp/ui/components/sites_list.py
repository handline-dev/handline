from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Static

from ...infra import textsafe
from ...team import agents
from .dialog import Dialog
from .ime import ImeInput


class SiteEdit(Dialog):
    """One site to add or change. Dismisses with the origin, or None. A site that is not
    an origin, or that the list already has, is refused with the reason."""

    FOCUS = "#se_site"
    DEFAULT_CSS = """
    SiteEdit #title { margin-bottom: 1; }
    SiteEdit #se_buttons { height: 1; margin-top: 1; }
    SiteEdit #se_buttons Button { width: auto; min-width: 0; margin-right: 2; }
    """

    def __init__(self, others: list[str], initial: str = "") -> None:
        super().__init__()
        self.others, self.initial = others, initial

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text("edit a site" if self.initial else "add a site", style="bold"), id="title")
            yield ImeInput(id="se_site", compact=True, placeholder="https://example.com", value=self.initial)
            with Horizontal(id="se_buttons"):
                yield Button("Save" if self.initial else "Add", id="se_ok", variant="primary", compact=True)
                yield Button("Cancel", id="se_cancel", compact=True)

    def _ok(self) -> None:
        try:
            self.finish(agents.check_origin(self.query_one("#se_site", ImeInput).value, self.others))
        except ValueError as e:
            self.notify(str(e), severity="error", timeout=8, markup=False)

    def on_input_submitted(self, event) -> None:
        event.stop()
        self._ok()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self._ok() if event.button.id == "se_ok" else self.finish(None)


class SitesList(Vertical):
    """The sites a sandboxed browser may open: one row each with a ✎ and a ✕, and an Add button.
    It only holds the list; the settings page's Save writes it with the other fields."""

    DEFAULT_CSS = """
    SitesList { height: auto; }
    SitesList #st_rows { height: auto; }
    SitesList .st_row { height: 1; }
    SitesList .st_text { width: 1fr; height: 1; text-wrap: nowrap; text-overflow: ellipsis; }
    SitesList .st_x { width: 3; min-width: 0; margin-left: 1; }
    SitesList #st_add { width: auto; min-width: 0; }
    """

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.sites: list[str] = []

    def compose(self) -> ComposeResult:
        yield Vertical(id="st_rows")
        yield Button("+ Add site", id="st_add", compact=True)

    def on_mount(self) -> None:
        self.show()

    def set_sites(self, sites: list[str]) -> None:
        self.sites = list(sites)
        self.show()

    def show(self) -> None:
        box = self.query_one("#st_rows", Vertical)
        box.remove_children()
        if not self.sites:
            box.mount(Static(Text("none", style="dim")))
        for i, site in enumerate(self.sites):
            box.mount(Horizontal(Static(Text(textsafe.oneline(site)), classes="st_text"),
                                 Button("✎", id=f"st_ed_{i}", compact=True, classes="st_x"),
                                 Button("✕", id=f"st_rm_{i}", variant="error", compact=True, classes="st_x"),
                                 classes="st_row"))

    def _add(self) -> None:
        def answered(site: str | None) -> None:
            if site is not None:
                self.sites.append(site)
                self.show()
        self.app.push_screen(SiteEdit(self.sites), answered)

    def _edit(self, i: int) -> None:
        def answered(site: str | None) -> None:
            if site is not None:
                self.sites[i] = site
                self.show()
        self.app.push_screen(SiteEdit([s for j, s in enumerate(self.sites) if j != i], self.sites[i]), answered)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "st_add":
            event.stop()
            self._add()
        elif bid[:6] in ("st_ed_", "st_rm_") and bid[6:].isdigit() and int(bid[6:]) < len(self.sites):
            event.stop()
            if bid.startswith("st_ed_"):
                self._edit(int(bid[6:]))
            else:
                del self.sites[int(bid[6:])]
                self.show()
