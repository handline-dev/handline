"""One account's page: Overview (who it is and how full it is), Agents (who runs on it)
and Settings (its cap, login and removal). What it says comes from `actions.account_facts`."""
from __future__ import annotations

import time

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Input, Static, Tabs

from ... import accounts, actions, db, paths, status, textsafe, tui
from ..components import gauge
from ..components.account_actions import AccountActions
from ..components.action_bar import ActionBar
from ..components.page_header import page_header

BAR_W = 30
_TABS = {"overview": "#a_overview", "agents": "#a_agents", "settings": "#a_settings"}


class AccountPage(AccountActions, Screen):
    BINDINGS = [Binding("escape", "back", "back")]
    DEFAULT_CSS = """
    AccountPage #a_overview { padding: 1 3; }
    AccountPage #a_agents { height: 1fr; }
    AccountPage #a_settings { height: 1fr; padding: 1 3; }
    AccountPage .frow { height: 1; margin-bottom: 1; }
    AccountPage .flabel { width: 12; color: $text-muted; }
    AccountPage #a_cap { width: 8; }
    AccountPage #a_flash { height: 1; padding: 0 3; color: $text-muted; }
    """

    def __init__(self, name: str) -> None:
        super().__init__()
        self.account = name
        self.flash = ""
        self.email: str | None = None       # asked of the vendor once: it is a subprocess
        self.facts: dict | None = None
        self._cap_shown = ""                 # what the cap box last drew from the file

    def compose(self) -> ComposeResult:
        yield from page_header([("Overview", "overview"), ("Agents", "agents"),
                                ("Settings", "settings")], back=True)
        with VerticalScroll(id="a_overview"):
            yield Static(id="a_text")
        yield DataTable(id="a_agents", cursor_type="row", zebra_stripes=True)
        with Vertical(id="a_settings"):
            for label, key in (("vendor", "vendor"), ("config dir", "config")):
                with Horizontal(classes="frow"):
                    yield Static(label, classes="flabel")
                    yield Static(id=f"a_{key}")
            with Horizontal(classes="frow"):
                yield Static("cap", classes="flabel")
                yield Input(id="a_cap", compact=True, restrict=r"[0-9]*")
                yield Static("  most agents mid-turn at once", classes="flabel")
        yield Static(id="a_flash")
        with ActionBar(id="a_buttons"):
            yield Button("Save cap", id="a_save", variant="primary", compact=True)
            yield Button("Login  l", id="a_login", compact=True)
            yield Button("Remove  x", id="a_remove", variant="error", compact=True)

    def on_mount(self) -> None:
        self.sub_title = f"account {self.account}"
        self.query_one("#a_agents", DataTable).add_columns("agent", "state", "detail")
        self.query_one("#a_agents", DataTable).can_focus = True
        self._show_tab("overview")
        self._load()
        self.set_interval(2.0, self._load)
        self.run_worker(self._ask_email, thread=True, exclusive=True, group="email")

    def _ask_email(self) -> None:
        who = accounts.identity(self.account)
        self.email = who or "unknown"
        self.app.call_from_thread(self._load)

    # ------------------------------------------------------------------ drawing

    def _load(self) -> None:
        if self.account not in paths.list_accounts():
            self.app.pop_screen()
            return
        con = db.connect()
        try:
            self.facts = actions.account_facts(con, self.account, accounts.logged_in)
        finally:
            con.close()
        f = self.facts
        self.query_one("#a_text", Static).update(self._overview(f))
        table = self.query_one("#a_agents", DataTable)
        keep = table.cursor_row
        table.clear()
        for name, state, detail in f["agents"]:
            table.add_row(Text(textsafe.oneline(name), style="bold"), Text(state),
                          Text(textsafe.oneline(detail), style="dim"), key=name)
        if f["agents"]:
            table.move_cursor(row=min(keep, len(f["agents"]) - 1))
        self.query_one("#a_vendor", Static).update(f["vendor"])
        self.query_one("#a_config", Static).update(textsafe.oneline(f["config"]))
        cap = self.query_one("#a_cap", Input)
        if cap.value in (self._cap_shown, str(f["cap"])):   # unedited: follow the file, never what is being typed
            cap.value = self._cap_shown = str(f["cap"])
        self.query_one("#a_flash", Static).update(Text(textsafe.oneline(self.flash)))

    def _overview(self, f: dict) -> Text:
        st = f["status"]
        out = Text(self.account, style="bold underline")
        out.append("\n\n")
        for label, value, style in (
                ("vendor", f["vendor"], ""),
                ("email", self.email or "…", "" if self.email not in (None, "unknown") else "dim"),
                ("login", "logged in" if f["logged_in"] else "NOT logged in",
                 "green" if f["logged_in"] else "bold red")):
            out.append(f"{label:<10}", style="dim")
            out.append(textsafe.oneline(value) + "\n", style=style)
        out.append("\n")
        shown, src = st.shown_pct_5h()
        for label, pct, mark, reset, window in (
                ("5h", shown, src, st.reset_5h, 5 * 3600),
                ("7d", st.trusted_pct_7d, status.mark(st.pct_7d_source), st.reset_7d, 7 * 86400)):
            bar = gauge.bar(pct, BAR_W, mark == status.IDLE_MARK, gauge.elapsed_fraction(reset, window))
            out.append(f"{label:<10}", style="dim")
            out.append_text(Text.from_ansi(f"{bar} {tui.pct(pct)}%{mark}"))
            left = (reset or 0) - time.time()
            out.append(f"   resets in {status.span(left)}\n" if reset and left > 0 else "\n", style="dim")
        out.append("\n")
        allowed, why = f["assign"]
        live = f["cost"]
        for label, value in (
                ("assign", f"{'yes' if allowed else 'no'} - {why}"),
                ("snapshot", "-" if st.age_s is None else f"{status.ago(st.age_s)} old"),
                ("session", st.session_id or "-"),
                ("spend", "nothing running to read it from" if live is None
                 else f"${live:.2f} across the sessions running now"),
                ("agents", str(len(f["agents"]))),
                ("cap", f"{f['cap']} mid-turn at once")):
            out.append(f"{label:<10}", style="dim")
            out.append(textsafe.oneline(value) + "\n")
        return out

    # --------------------------------------------------------------------- tabs

    def _show_tab(self, tab: str) -> None:
        for name, sel in _TABS.items():
            self.query_one(sel).display = name == tab
        self.query_one("#a_buttons").display = tab == "settings"

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        self._show_tab(event.tab.id)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        from ...chat_page import ChatPage   # chat_page imports the components this page uses
        self.app.push_screen(ChatPage(str(event.row_key.value)))

    def action_back(self) -> None:
        self.app.pop_screen()

    # ------------------------------------------------------------------ settings

    def _save_cap(self) -> None:
        ok, self.flash = actions.set_account_cap(self.account, self.query_one("#a_cap", Input).value)
        self._load()

    async def _run(self, act) -> None:
        await act(self.account)
        self._load()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._save_cap()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        bid = event.button.id
        if bid == "leave":
            self.action_back()
        elif bid == "a_save":
            self._save_cap()
        else:
            # Login and Remove open a prompt or hand the terminal over, which Textual
            # only allows from inside a worker.
            self.run_worker(self._run(self._account_login if bid == "a_login" else self._account_remove),
                            exclusive=True, group="account")

    def on_key(self, event: events.Key) -> None:
        if isinstance(self.focused, Input):
            return
        if self.query_one("#a_settings").display and event.character in ("l", "x"):
            event.stop()
            self.run_worker(self._run(self._account_login if event.character == "l" else self._account_remove),
                            exclusive=True, group="account")
