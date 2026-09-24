"""One account's page, laid out like a task's: Overview (who it is, how full it is, what the
home row says, large), Agents (who runs on it) and Settings (its name, caps, login, removal).
What it says comes from `actions.account_facts` and the home row from `tui.accounts_data`."""
from __future__ import annotations

import time

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Header, Input, Static, Tabs

from ... import accounts, actions, db, load as loadmod, paths, status, textsafe, tui
from ..components import gauge
from ..components.account_actions import AccountActions
from ..components.action_bar import ActionBar
from ..components.page_header import page_header

BAR_W = 30
_TABS = {"overview": "#detail_pane", "agents": "#a_agents", "settings": "#a_settings"}
_INPUTS = {"name": "#a_name", "cap": "#a_cap", "default": "#a_default"}


class AccountPage(AccountActions, Screen):
    BINDINGS = [Binding("escape", "back", "back")]
    DEFAULT_CSS = """
    AccountPage #a_agents { height: 1fr; }
    AccountPage #a_settings { height: 1fr; padding: 1 3; }
    AccountPage .frow { height: 1; margin-bottom: 1; }
    AccountPage .flabel { width: 16; color: $text-muted; }
    AccountPage #a_name { width: 40; }
    AccountPage #a_cap, AccountPage #a_default { width: 8; }
    AccountPage #a_flash { height: 1; padding: 0 3; color: $text-muted; }
    """

    def __init__(self, name: str) -> None:
        super().__init__(classes="chat_page")     # the task page's styles (chat_page.tcss)
        self.account = name
        self.flash = ""
        self.email: str | None = None       # asked of the vendor once: it is a subprocess
        self.facts: dict | None = None
        self._shown: dict[str, str] = {}    # what each settings box last drew from the config

    def compose(self) -> ComposeResult:
        yield from page_header([("Overview", "overview"), ("Agents", "agents"),
                                ("Settings", "settings")], back=True)
        with VerticalScroll(id="detail_pane"):
            yield Static(id="t_title")
            yield Static(id="t_badge")
            yield Static(id="t_meta")
            yield Static(id="a_usage", classes="section")
            yield Static(id="a_home", classes="section")
            yield Static(id="a_status", classes="section")
        yield DataTable(id="a_agents", cursor_type="row", zebra_stripes=True)
        with Vertical(id="a_settings"):
            for label, key, hint in (("name", "name", "  shown on the bridge; agents still call it by its own name"),
                                     ("cap", "cap", "  most agents mid-turn on this account at once"),
                                     ("default cap", "default", "  the cap of an account with none of its own")):
                with Horizontal(classes="frow"):
                    yield Static(label, classes="flabel")
                    yield Input(id=f"a_{key}", compact=True, restrict=r"[0-9]*" if key != "name" else None)
                    yield Static(hint, classes="flabel")
            for label, key in (("vendor", "vendor"), ("config dir", "config")):
                with Horizontal(classes="frow"):
                    yield Static(label, classes="flabel")
                    yield Static(id=f"a_{key}")
        yield Static(id="a_flash", classes="flash")
        with ActionBar(id="a_buttons"):
            yield Button("Save", id="a_save", variant="primary", compact=True)
            yield Button("Login  l", id="a_login", compact=True)
            yield Button("Remove  x", id="a_remove", variant="error", compact=True)

    def on_mount(self) -> None:
        self.sub_title = f"account {self.account}"
        self.query_one("#a_agents", DataTable).add_columns("agent", "state", "detail")
        self._show_tab("overview")
        self.query_one("#scope", Tabs).focus()
        self._load()
        self.set_interval(2.0, self._load)
        self.run_worker(self._ask_email, thread=True, exclusive=True, group="email")

    def _ask_email(self) -> None:
        self.email = accounts.identity(self.account) or "unknown"
        self.app.call_from_thread(self._load)

    # ------------------------------------------------------------------ drawing

    def on_screen_resume(self) -> None:
        self._load()

    def _load(self) -> None:
        if self.app.screen is not self:     # a page on top of this one (a chat): its timer keeps running
            return
        if self.account not in paths.list_accounts():
            self.app.pop_screen()
            return
        con = db.connect()
        try:
            self.facts = f = actions.account_facts(con, self.account, accounts.logged_in)
            row = next((r for r in tui.accounts_data(con)[0] if r.name == self.account), None)
        finally:
            con.close()
        self._draw_overview(f, row)
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
        label = loadmod.label_of(self.account)
        for key, now in (("name", "" if label == self.account else label), ("cap", str(f["cap"])),
                         ("default", str(loadmod.default_cap()))):
            box = self.query_one(_INPUTS[key], Input)
            if box.value in (self._shown.get(key, ""), now):    # unedited: follow the config, never what is typed
                box.value = self._shown[key] = now
        self.query_one("#a_flash", Static).update(Text(textsafe.oneline(self.flash)))

    def _draw_overview(self, f: dict, row) -> None:
        st = f["status"]
        label = loadmod.label_of(self.account)
        self.query_one("#t_title", Static).update(textsafe.oneline(label))
        self.query_one("#t_badge", Static).update(
            Text(" logged in ", style="black on green") if f["logged_in"]
            else Text(" NOT logged in ", style="white on red"))
        meta = Text()
        for key, value, style in (("account", self.account, ""), ("vendor", f["vendor"], ""),
                                  ("email", self.email or "…", "dim" if self.email in (None, "unknown") else "")):
            meta.append(f"{key:<9}", style="dim")
            meta.append(textsafe.oneline(value) + "\n", style=style)
        self.query_one("#t_meta", Static).update(meta)

        usage = Text("Usage\n", style="bold")
        shown, src = st.shown_pct_5h()
        for name, pct, mark, reset, window in (
                ("5h", shown, src, st.reset_5h, 5 * 3600),
                ("7d", st.trusted_pct_7d, status.mark(st.pct_7d_source), st.reset_7d, 7 * 86400)):
            bar = gauge.bar(pct, BAR_W, mark == status.IDLE_MARK, gauge.elapsed_fraction(reset, window))
            usage.append(f"  {name:<6}", style="dim")
            usage.append_text(Text.from_ansi(f"{bar} {tui.pct(pct)}%{mark}"))
            left = (reset or 0) - time.time()
            usage.append(f"   resets in {status.span(left)}" if reset and left > 0 else "", style="dim")
            usage.append("\n")
        self.query_one("#a_usage", Static).update(usage)

        home = Text("On the home row\n", style="bold")
        for key in ("agents", "runway"):
            if row is not None:
                home.append(f"  {tui.ACCT_HEAD[key]:<16}", style="dim")
                home.append_text(Text.from_ansi(row.cells[key] + "\n"))
        if row is not None:
            home.append(f"  {'can take work':<16}", style="dim")
            home.append_text(Text.from_ansi(row.mark + row.next + "\n"))
        self.query_one("#a_home", Static).update(home)

        allowed, why = f["assign"]
        live = f["cost"]
        out = Text("Status\n", style="bold")
        for key, value in (("assign", f"{'yes' if allowed else 'no'} - {why}"),
                           ("snapshot", "-" if st.age_s is None else f"{status.ago(st.age_s)} old"),
                           ("session", st.session_id or "-"),
                           ("spend", "nothing running to read it from" if live is None
                            else f"${live:.2f} across the sessions running now"),
                           ("cap", f"{f['cap']} agents mid-turn at once")):
            out.append(f"  {key:<16}", style="dim")
            out.append(textsafe.oneline(value) + "\n")
        self.query_one("#a_status", Static).update(out)

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

    def _save(self) -> None:
        box = {k: self.query_one(sel, Input).value for k, sel in _INPUTS.items()}
        _, self.flash = actions.save_account_settings(self.account, box["name"], box["cap"], box["default"])
        self._load()

    async def _run(self, act) -> None:
        await act(self.account)
        self._load()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._save()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        bid = event.button.id
        if bid == "leave":
            self.action_back()
        elif bid == "a_save":
            self._save()
        else:
            # Login and Remove open a prompt or hand the terminal over, which Textual
            # only allows from inside a worker.
            self.run_worker(self._run(self._account_login if bid == "a_login" else self._account_remove),
                            exclusive=True, group="account")

    def _enter_tab(self) -> None:
        """Take the focus from the tab bar into the tab's content."""
        tab = self.query_one("#scope", Tabs).active
        target = {"agents": "#a_agents", "settings": "#a_name", "overview": "#detail_pane"}[tab]
        self.query_one(target).focus()

    def on_key(self, event: events.Key) -> None:
        """The page works from the keyboard alone: it opens on the tab bar, where ←→ change
        tab; ↓ goes into the tab, and ↑ from its first row or field comes back up. In the
        settings ↑↓ walk the fields and buttons."""
        tabs = self.query_one("#scope", Tabs)
        focused = self.focused
        if isinstance(focused, Tabs):
            if event.key == "down":
                event.stop()
                self._enter_tab()
            return
        top = (isinstance(focused, DataTable) and focused.cursor_row == 0) \
            or focused is self.query_one("#a_name") or focused is self.query_one("#detail_pane")
        if event.key == "up" and top:
            event.stop()
            tabs.focus()
        elif event.key in ("up", "down") and self.query_one("#a_settings").display \
                and (isinstance(focused, (Input, Button))):
            event.stop()
            (self.focus_next if event.key == "down" else self.focus_previous)()
        if isinstance(focused, Input):
            return
        if self.query_one("#a_settings").display and event.character in ("l", "x"):
            event.stop()
            self.run_worker(self._run(self._account_login if event.character == "l" else self._account_remove),
                            exclusive=True, group="account")
