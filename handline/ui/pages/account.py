"""One account's page, laid out like a task's: Overview (who it is, how full it is, what the
home row says, large), Agents (who runs on it) and Settings (its alias, cap, login, removal).
What it says comes from `actions.account_facts` and the home row from `tui.accounts_data`."""
from __future__ import annotations

import time

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Header, Input, Static, Switch, Tabs

from ...team import accounts
from ... import actions, db
from ...usage import cycle, load as loadmod, status
from ...infra import paths, textsafe
from .. import tui
from ...infra import timefmt
from ..components import gauge
from ..components.confirm import leave_or_ask
from ..components.field_note import FieldNote
from ..components.ime import ImeInput
from ..components.account_actions import AccountActions
from ..components.action_bar import ActionBar
from ..components.page_header import page_header
from ..components.switch_row import SwitchRow
from ..components.table_sync import sync_rows

BAR_W = 30
EMAIL_RETRY_S = 20.0
_TABS = {"overview": "#detail_pane", "agents": "#a_agents", "settings": "#a_settings"}
_INPUTS = {"name": "#a_name", "cap": "#a_cap", "end": "#a_end"}


class AccountPage(AccountActions, Screen):
    BINDINGS = [Binding("escape", "back", "back")]
    DEFAULT_CSS = """
    AccountPage #a_agents { height: 1fr; }
    AccountPage #a_settings { height: 1fr; padding: 1 3; }
    AccountPage .frow { height: 1; margin-bottom: 1; }
    AccountPage .flabel { width: 16; color: $text-muted; }
    AccountPage .fhint { color: $text-muted; }
    AccountPage #a_name { width: 40; }
    AccountPage #a_cap { width: 8; }
    AccountPage #a_end { width: 14; }
    """

    def __init__(self, name: str) -> None:
        super().__init__(classes="chat_page")     # the task page's styles (chat_page.tcss)
        self.account = name
        self.email: str | None = None       # the stored one, asked of the vendor when none is kept
        self._email_asked = 0.0
        self.facts: dict | None = None
        self._recover_shown = True          # what it last drew from the config
        self._cycle_shown = False
        self._shown: dict[str, str] = {}    # what each settings box last drew from the config

    def compose(self) -> ComposeResult:
        yield from page_header([("Overview", "overview"), ("Agents", "agents"),
                                ("Settings", "settings")], back=True)
        with VerticalScroll(id="detail_pane"):
            yield Static(id="t_title")
            yield Static(id="t_badge")
            yield Static(id="t_meta")
            yield Static(id="a_usage", classes="section")
            yield Static(id="a_month", classes="section")
            yield Static(id="a_home", classes="section")
            yield Static(id="a_status", classes="section")
        yield DataTable(id="a_agents", cursor_type="row", zebra_stripes=True)
        with Vertical(id="a_settings"):
            with Horizontal(classes="frow"):
                yield Static("account", classes="flabel")
                yield Static(self.account)
            for label, key, hint in (("alias", "name", "  shown instead of the account's name on the bridge; blank shows the name"),
                                     ("cap", "cap", "  most agents mid-turn on this account at once; empty is the default")):
                with Horizontal(classes="frow"):
                    yield Static(label, classes="flabel")
                    yield ImeInput(id=f"a_{key}", compact=True, restrict=r"[0-9]*" if key != "name" else None,
                                placeholder=self.account if key == "name" else "")
                    yield Static(hint, classes="fhint")
                    yield FieldNote(id=f"n_a_{key}")
            yield SwitchRow("usage recovery", "one real turn that renews a dead credential or restarts the usage window",
                            "a_recover", True)
            yield SwitchRow("ends this month", "a one-off month: not picked after its last day; off = renewing", "a_cycle")
            with Horizontal(classes="frow"):
                yield Static("last day", classes="flabel")
                yield ImeInput(id="a_end", compact=True, max_length=10, placeholder="YYYY-MM-DD")
                yield Static("  usable through it; blank = the next monthly anniversary of the subscription start", classes="fhint")
                yield FieldNote(id="n_a_end")
            for label, key in (("vendor", "vendor"), ("config dir", "config")):
                with Horizontal(classes="frow"):
                    yield Static(label, classes="flabel")
                    yield Static(id=f"a_{key}")
        with ActionBar(id="a_buttons"):
            yield Button("Login  l", id="a_login", compact=True)
            yield Button("Remove  x", id="a_remove", variant="error", compact=True)

    def on_mount(self) -> None:
        self.sub_title = f"account {self.account}"
        self.query_one("#a_agents", DataTable).add_columns("agent", "state", "detail")
        self._show_tab("overview")
        self.query_one("#scope", Tabs).focus()
        self._load()
        self.set_interval(2.0, self._load)
        self._ask_email_later()

    def _ask_email_later(self) -> None:
        self._email_asked = time.monotonic()
        self.run_worker(self._ask_email, thread=True, exclusive=True, group="email")

    def _ask_email(self) -> None:
        self.email = accounts.email(self.account) or "unknown"
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
        rebuilt = sync_rows(table, [(name, (Text(textsafe.oneline(name), style="bold"), Text(state),
                                            Text(textsafe.oneline(detail), style="dim")))
                                    for name, state, detail in f["agents"]])
        if rebuilt and f["agents"]:
            table.move_cursor(row=min(keep, len(f["agents"]) - 1), scroll=False)
        if self.email == "unknown" and f["logged_in"] and time.monotonic() - self._email_asked > EMAIL_RETRY_S:
            self._ask_email_later()     # a failed ask is not kept, so a login claude was slow to answer for is asked again
        self.query_one("#a_vendor", Static).update(f["vendor"])
        self.query_one("#a_config", Static).update(textsafe.oneline(f["config"]))
        label = loadmod.label_of(self.account)
        for key, now in (("name", "" if label == self.account else label), ("cap", "" if f["own_cap"] is None else str(f["own_cap"])),
                         ("end", f["own_end"])):
            box = self.query_one(_INPUTS[key], Input)
            if box.value in (self._shown.get(key, ""), now):    # unedited: follow the config, never what is typed
                box.value = self._shown[key] = now
        box = self.query_one("#a_recover", Switch)
        if box.value == self._recover_shown:    # unedited: follow the config
            box.value = self._recover_shown = f["recover"]
        box = self.query_one("#a_cycle", Switch)
        if box.value == self._cycle_shown:
            box.value = self._cycle_shown = f["cycle"]
        self.query_one("#a_cap", Input).placeholder = str(f["default_cap"])

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
        shown, src = st.shown_short()
        for w, pct, mark in ((st.short, shown, src),
                             (st.long, st.long_trusted, status.mark(st.long_source))):
            if w is st.long and not w.minutes:
                continue        # a vendor with one window has no longest
            name, reset, window = w.label, w.reset, w.minutes * 60
            bar = gauge.bar(pct, BAR_W, mark == status.IDLE_MARK, gauge.elapsed_fraction(reset, window))
            usage.append(f"  {name:<6}", style="dim")
            usage.append_text(Text.from_ansi(f"{bar} {tui.pct(pct)}%{mark}"))
            left = (reset or 0) - time.time()
            cut = st.sub_end is not None and w is st.long and time.time() < st.sub_end < (reset or float("inf"))
            if cut:     # the subscription ends inside this week: the week is cut there
                usage.append(f"   ends in {timefmt.span(st.sub_end - time.time())}", style="dim")
            else:
                usage.append(f"   resets in {timefmt.span(left)}" if reset and left > 0 else "", style="dim")
            usage.append("\n")
        self.query_one("#a_usage", Static).update(usage)

        month = Text("Month\n", style="bold")
        pos = f["cycle_pos"]
        month.append(f"  {'cycle':<16}", style="dim")
        where = cycle.fraction(pos)
        if where is not None:       # the same track and marker as the usage gauges above
            month.append_text(Text.from_ansi(gauge.bar(0, BAR_W, False, where)))
            month.append("  ")
        month.append(pos.line() if pos else "renewing, subscription start unknown")
        self.query_one("#a_month", Static).update(month)

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
                           *((("counted", "no - logged out; left out of the graphs and forecasts until it logs in again"),)
                             if not f["logged_in"] else ()),
                           ("snapshot", "-" if st.age_s is None else f"{timefmt.ago(st.age_s)} old"),
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
        from ..chat_page import ChatPage  # chat_page imports the components this page uses
        self.app.push_screen(ChatPage(str(event.row_key.value)))

    def action_back(self) -> None:
        leave_or_ask(self.app, self.settle())

    # ------------------------------------------------------------------ settings

    def _shown_value(self, key: str) -> str:
        label = loadmod.label_of(self.account)
        if key == "name":
            return "" if label == self.account else label
        if key == "end":
            return cycle.own_end(self.account)
        own = loadmod.own_cap(self.account)
        return "" if own is None else str(own)

    def _save(self, which: str) -> bool:
        """Save the one setting that was changed, at once; the others are left as they stand, so
        one that is refused does not hold the rest. The note beside it says what came of it."""
        note = self.query_one(f"#n_a_{which}", FieldNote)
        recover = bool(self.query_one("#a_recover", Switch).value)
        if which == "cycle":
            ok, said = actions.set_cycle_on(self.account, bool(self.query_one("#a_cycle", Switch).value))
        elif which == "end":
            typed = self.query_one("#a_end", Input).value
            if typed.strip() == self._shown_value("end"):
                note.clear()
                return True
            ok, said = actions.set_cycle_end(self.account, typed)
        elif which == "recover":
            ok, said = actions.save_account_settings(self.account, self._shown_value("name"), "", recover)
        else:
            typed = self.query_one(_INPUTS[which], Input).value
            if typed.strip() == self._shown_value(which):
                note.clear()
                return True
            ok, said = actions.save_account_settings(self.account, typed if which == "name" else self._shown_value("name"),
                                                     typed if which == "cap" else "", None)
        if not ok:
            note.error(said)
            return False
        if said != "nothing changed":
            note.saved()
        else:
            note.clear()
        self._shown.clear()     # a saved form shows the config again, even where a blank wrote nothing
        if which == "recover":
            self._recover_shown = recover
        elif which == "cycle":
            self._cycle_shown = bool(self.query_one("#a_cycle", Switch).value)
        self._load()
        return True

    def settle(self) -> list[str]:
        """Save every typed field that differs from the config; what is still refused is what going
        back must ask about."""
        for key in _INPUTS:
            self._save(key)
        return [n.problem for n in self.query("#a_settings FieldNote") if n.problem]

    async def _run(self, act) -> None:
        await act(self.account)
        self._load()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        for key, sel in _INPUTS.items():
            if event.input.id == sel[1:]:
                self._save(key)

    def on_descendant_blur(self, event) -> None:
        for key, sel in _INPUTS.items():
            if getattr(event.widget, "id", None) == sel[1:]:
                self._save(key)

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.switch.id == "a_cycle" and bool(event.value) != cycle.enabled(self.account):
            event.stop()
            self._save("cycle")
            return
        if event.switch.id == "a_recover" and bool(event.value) != loadmod.usage_recover_enabled(self.account):
            event.stop()
            self._save("recover")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        bid = event.button.id
        if bid == "leave":
            self.action_back()
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
                and (isinstance(focused, (Input, Button, Switch))):
            event.stop()
            if event.key == "down" and focused is self.query_one("#a_remove"):
                return      # the last button: stop here, do not wrap to the tab bar
            (self.focus_next if event.key == "down" else self.focus_previous)()
        if isinstance(focused, Input):
            return
        if self.query_one("#a_settings").display and event.character in ("l", "x"):
            event.stop()
            self.run_worker(self._run(self._account_login if event.character == "l" else self._account_remove),
                            exclusive=True, group="account")
