"""Tests for ocp/ui/bridge_textual.py (task #45) - the port's first screen.

Needs `textual`, which the interpreter running `tests/test_ocp.py` may not
have (it is only in $OCP_HOME/venv - see requirements.txt and
ocp/infra/venv.py's own docstring on why the suite cannot assume it everywhere).
`tests/test_ocp.py` runs this file under the first python that can import
textual (its own, else the venv's) and prints a SKIPPED line when none can, so
a missing dependency is said out loud instead of the screen going unexercised.

Run: python3 tests/ui/test_bridge_textual.py
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# First, so OCP_TEST_ROOT is set before any ocp module reads a path.
import test_ocp as t  # noqa: E402 - its `_daemon_home`/`DESIGN_TEAM` fixture, not re-derived here
from textual import events  # noqa: E402
from ocp import actions, db
from ocp.team import agents, hr
from ocp.ui import bridge_textual as bt, jobs as jobsmod, tui
from ocp.infra import timefmt  # noqa: E402
from ocp.daemon import states as dmn_states
from ocp.ui.chat_page import ChatPage  # noqa: E402
from ocp.ui.clipboard_app import ClipboardApp  # noqa: E402
from ocp.ui.components.confirm import Confirm, confirm_answer  # noqa: E402
from ocp.ui.components.read_only import ReadOnly  # noqa: E402
from textual import events  # noqa: E402
from ocp.ui.components.text_prompt import TextPrompt  # noqa: E402
from ocp.ui.pages.account import AccountPage  # noqa: E402
from ocp.ui.pages.home import HomePage  # noqa: E402
from textual.widgets import DataTable, Static  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not cond else ""))


def _said(screen) -> str:
    """The newest toast the app has shown."""
    notes = list(screen.app._notifications)
    return notes[-1].message if notes else ""


def _home(tag: str) -> Path:
    """`t._daemon_home`, minus the daemon-trigger fixture (a percentage, a
    Stop event) this screen has no use for: registers `backend` on `alpha`
    with a terminal, and a real team.yaml so `actions.chat_reply`/`ask` have
    somewhere to route through."""
    home = Path(tempfile.mkdtemp(prefix=f"ocp_bt_{tag}_", dir=t.TEST_ROOT))
    t._daemon_home(home, 20)
    return home


def _screen_text(screen) -> str:
    return "\n".join(str(w.render()) for w in screen.query(Static))


async def test_dashboard_shows_a_registered_agent_and_moves_the_cursor() -> None:
    _home("dash")
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        screen = app.screen
        row = screen.query_one("#agents", DataTable).get_row("backend")
        check("dashboard: the registered agent is on the AGENTS panel",
              "backend" in str(row[0]) and "alpha" in str(row[1]), row)
        await pilot.press("w")
        await pilot.pause(0.2)
        check("dashboard: a refused verb says why, as `actions.can` does",
              "not parked or closed" in _said(screen), _said(screen))


async def test_help_key_names_every_agent_verb_the_footer_folds_out() -> None:
    """Task #54 item 2: `?` on an AGENTS/STUCK row - the one place fold and
    move (and whatever a row's own state hid) are named at all, same as
    curses's `agent_help_line` (tui.py). It must not fall through and do
    nothing, the way an unhandled AGENT_KEYS lookup normally would."""
    _home("help")
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        screen = app.screen
        await pilot.press("?")
        await pilot.pause(0.1)
        from ocp.ui.components.key_guide import KeyHelp
        box = app.screen
        listed = str(box.query_one("#keys").render()) if isinstance(box, KeyHelp) else ""
        check("help: `?` opens the box with the full AGENT_KEYS legend, not nothing",
              isinstance(box, KeyHelp) and all(label in listed for _, label in t.tui.AGENT_KEYS if label != "open"), listed)
        check("help: the legend names a key the footer can fold away (move)", "move" in listed, listed)
        await pilot.press("escape")
        await pilot.pause(0.1)


async def test_an_action_runs_off_the_key_loop_and_the_screen_says_so() -> None:
    """Task #45 / #1615 (bridge-working): a key that acts must not block the
    screen - it keeps redrawing and reading keys while the action runs, with a
    line saying what it is doing."""
    home = _home("jobs")
    stub = home / "stub_slow.py"
    stub.write_text(
        "import sys, json, time\n"
        "json.load(sys.stdin)\n"
        "time.sleep(1.2)\n"
        "print(json.dumps({'ok': True, 'said': 'closed backend (stub)'}), flush=True)\n"
    )
    real_command = jobsmod.ACT_COMMAND
    jobsmod.ACT_COMMAND = [sys.executable, str(stub)]
    try:
        app = bt.BridgeApp()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            screen = app.screen
            await pilot.press("c")             # close: CONFIRM
            await pilot.pause(0.2)
            check("act: `c` on an idle agent with a terminal asks first",
                  isinstance(app.screen, Confirm), type(app.screen).__name__)
            await pilot.press("y")
            await pilot.pause(0.2)
            check("act: confirming starts the job and returns to the dashboard at once",
                  isinstance(app.screen, HomePage) and screen.jobs.running, "")
            await pilot.pause(0.5)     # past jobs.QUIET_S, so the running line is drawn
            mid_text = _screen_text(screen)
            check("act: the running line names what it is and that it is running",
                  "closing backend" in mid_text, mid_text)
            before = dict(screen.sel)
            await pilot.press("down")
            await pilot.pause(0.1)
            check("act: the screen still answers other keys while the job runs",
                  screen.jobs.running, "job ended before the screen could be exercised")
            deadline = asyncio.get_event_loop().time() + 5
            while asyncio.get_event_loop().time() < deadline and screen.jobs.running:
                await pilot.pause(0.1)
            check("act: once it answers, the line is replaced by what it said",
                  "closed backend (stub)" in _said(screen) and not screen.jobs.running, _said(screen))
    finally:
        jobsmod.ACT_COMMAND = real_command


async def test_confirm_rechecks_can_if_the_state_changed_while_it_was_open() -> None:
    """auditor #2035 SHOULD-FIX 2: a confirm waits on a human for as long as
    the human takes, and the daemon ticks every two seconds in the meantime -
    `dispatch_agent_verb` reruns `tui._confirmed_can` on a yes rather than
    trusting what `actions.can` said when the key was first pressed. Flips
    `backend` to HANDOFF while the modal is open; curses's own pilot for this
    (auditor #2008/#2035) got 'backend is HANDOFF - it changed while you
    looked' - the port must say the same."""
    home = _home("recheck")
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        screen = app.screen
        await pilot.press("c")             # close: CONFIRM
        await pilot.pause(0.1)
        check("recheck: c on an idle agent asks first",
              isinstance(app.screen, Confirm), type(app.screen).__name__)
        con = db.connect()
        try:
            db.set_agent_state(con, "backend", dmn_states.HANDOFF)
        finally:
            con.close()
        await pilot.press("y")
        await pilot.pause(0.2)
        check("recheck: a yes rechecks actions.can rather than trusting the first read",
              "HANDOFF" in _said(screen) and "changed while you looked" in _said(screen),
              _said(screen))
        check("recheck: and does not start the job", not screen.jobs.running, "")


async def test_text_prompt_composer_sends_and_cancels() -> None:
    async def read(multi: bool, keys: list[str]) -> str | None:
        app_ = _HostApp(TextPrompt("to backend:", multi=multi))
        async with app_.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            for k in keys:
                await pilot.press(k)
            await pilot.pause(0.2)
        return app_.result

    class _HostApp(bt.App):
        def __init__(self, screen):
            super().__init__()
            self._screen, self.result = screen, "<unset>"

        async def on_mount(self):
            self.run_worker(self._read())

        async def _read(self):
            self.result = await self.push_screen_wait(self._screen)

    sent = await read(False, list("hi") + ["enter"])
    check("prompt: typed text is sent on enter", sent == "hi", repr(sent))
    for multi in (True, False):
        app_ = _HostApp(TextPrompt("to backend:", multi=multi))
        async with app_.run_test(size=(80, 20)) as pilot:
            await pilot.pause()
            await pilot.press("안")
            await app_.on_event(events.Key("enter", "녕"))      # what the founder's terminal sends
            await pilot.pause(0.2)
        check(f"prompt: a syllable the IME commits with enter is sent (multi={multi})",
              app_.result == "안녕", repr(app_.result))
    cancelled = await read(False, list("hi") + ["escape"])
    check("prompt: escape cancels and sends nothing", cancelled is None, repr(cancelled))


async def test_accounts_panel_cap_add_remove_and_open() -> None:
    """ACCOUNTS' non-interactive actions (task #45's own third piece): `c`
    sets the cap through `loadmod` directly, `a`/`x` refuse through the same
    `accounts.add_account`/`remove_account` the CLI uses before either ever
    needs a real login shell, and `enter` opens the account's page
    (`AccountPage`)."""
    _home("accts")
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.press("left")          # AGENTS -> ACCOUNTS
        await pilot.pause(0.1)
        screen = app.screen
        check("accounts: left from AGENTS enters the ACCOUNTS panel", screen.panel == 0, str(screen.panel))

        await pilot.press("c")
        await pilot.pause(0.1)
        for ch in "3":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause(0.2)
        check("accounts: c sets the cap through loadmod",
              "at most 3 agents mid-turn" in _said(screen), _said(screen))

        await pilot.press("a")
        await pilot.pause(0.1)
        for ch in "alpha":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause(0.2)
        check("accounts: adding one that exists refuses through accounts.add_account, "
              "never reaching a real login", _said(screen) == "account 'alpha' already exists",
              _said(screen))

        await pilot.press("x")
        await pilot.pause(0.2)
        check("accounts: removing one an agent is on refuses through remove_plan, "
              "before the human is ever asked to type its name",
              "session open" in _said(screen) and not isinstance(app.screen, TextPrompt),
              _said(screen))

        await pilot.press("enter")
        await pilot.pause(0.2)
        check("accounts: enter opens that account's page",
              isinstance(app.screen, AccountPage) and app.screen.account == "alpha",
              type(app.screen).__name__)
        await pilot.press("escape")
        await pilot.pause(0.1)
        check("accounts: escape closes it", isinstance(app.screen, HomePage),
              type(app.screen).__name__)


async def test_the_account_page_shows_the_account_and_sets_its_cap() -> None:
    from unittest import mock
    from textual.widgets import Input, Switch, Tabs
    from ocp.team import accounts as accountsmod
    from ocp.usage import load as loadmod
    _home("acctpage")
    check("account cap: a non-number or zero is refused",
          not actions.set_account_cap("alpha", "")[0] and not actions.set_account_cap("alpha", "0")[0]
          and not actions.set_account_cap("alpha", "x")[0])
    loadmod.set_usage_recover("alpha", False)     # switched off with `ocp account recover`, before the page opens
    ok, said = actions.set_account_cap("alpha", " 5 ")
    check("account cap: a number is kept", ok and loadmod.cap_of("alpha") == 5 and "at most 5" in said, said)
    with mock.patch.object(accountsmod, "identity", lambda acct: "me@example.com"):
        app = bt.BridgeApp()
        async with app.run_test(size=(120, 34)) as pilot:
            await pilot.pause(0.3)
            home = app.screen
            app.push_screen(AccountPage("alpha"))
            await pilot.pause(0.5)
            page = app.screen
            text = "\n".join(str(page.query_one(i, Static).render()) for i in ("#t_title", "#t_meta", "#t_badge", "#a_usage"))
            check("account page: the overview names the account, its vendor and its email",
                  "alpha" in text and "claude" in text and "me@example.com" in text, text)
            check("account page: and says whether it is logged in and how full it is",
                  "logged in" in text.lower() and "5h" in text and "7d" in text, text)
            check("account page: it opens with the tab bar focused", isinstance(page.focused, Tabs), page.focused)
            await pilot.press("right")
            await pilot.pause(0.2)
            table = page.query_one("#a_agents", DataTable)
            check("account page: right on the bar changes tab, down goes into it, up comes back",
                  table.display, "")
            await pilot.press("down")
            check("account page: down enters the agents table", page.focused is table, page.focused)
            await pilot.press("up")
            check("account page: up from its first row returns to the bar", isinstance(page.focused, Tabs), page.focused)
            await pilot.press("right", "down", "down")
            check("account page: in settings the arrows walk the fields", page.focused is page.query_one("#a_cap", Input),
                  page.focused)
            await pilot.press("up", "up", "left")     # back to the bar, then to the Agents tab
            await pilot.pause(0.2)
            check("account page: the agents tab lists who runs on the account",
                  table.display and [k.value for k in table.rows] == ["backend"], [k.value for k in table.rows])
            check("account page: the overview is hidden behind it", not page.query_one("#detail_pane").display)
            await pilot.press("down", "enter")
            await pilot.pause(0.4)
            check("account page: Enter on an agent opens its chat page", isinstance(app.screen, ChatPage),
                  type(app.screen).__name__)
            await pilot.press("escape")
            await pilot.pause(0.3)
            check("account page: and Esc comes back to the account", app.screen is page, type(app.screen).__name__)

            page.query_one("#scope", Tabs).active = "settings"
            await pilot.pause(0.2)
            check("account page: settings shows the cap and the buttons",
                  page.query_one("#a_cap", Input).value == "5" and page.query_one("#a_buttons").display)
            page.query_one("#a_cap", Input).value = "2"
            page.query_one("#a_name", Input).value = "Work"
            check("account page: the usage recovery switch is drawn from the config, off",
                  page.query_one("#a_recover", Switch).value is False)
            await pilot.click("#a_save")
            await pilot.pause(0.3)
            flash = _said(page)
            check("account page: a save that changes the cap leaves recovery off",
                  not loadmod.usage_recover_enabled("alpha") and page.query_one("#a_recover", Switch).value is False)
            check("account page: Save writes the alias and the cap",
                  loadmod.cap_of("alpha") == 2
                  and loadmod.label_of("alpha") == "Work" and "at most 2" in flash, flash)
            page.query_one("#a_recover", Switch).value = True
            await pilot.click("#a_save")
            await pilot.pause(0.3)
            check("account page: Save writes the usage recovery switch", loadmod.usage_recover_enabled("alpha")
                  and page.query_one("#a_recover", Switch).value is True)
            check("account page: the title shows the new name",
                  "Work" in str(page.query_one("#t_title", Static).render()))
            page.query_one("#a_cap", Input).value = ""
            await pilot.click("#a_save")
            await pilot.pause(0.3)
            flash = _said(page)
            check("account page: an empty cap writes nothing", loadmod.cap_of("alpha") == 2, flash)
            check("account page: and the box shows the account's own cap again",
                  page.query_one("#a_cap", Input).value == "2", page.query_one("#a_cap", Input).value)
            await pilot.click("#leave")
            await pilot.pause(0.3)
            check("account page: Back returns to the home page", app.screen is home, type(app.screen).__name__)


async def test_accounts_remove_only_fires_on_the_typed_name() -> None:
    """auditor #2016 SHOULD-FIX 2: the only remove check in the file uses an
    in-use account, refused by remove_plan before confirm() is ever
    consulted, so the typed-name check itself was never exercised. `beta` is
    free (registered with `no_login`, so nothing here needs a real login
    shell) - a wrong name must leave it, the exact name must remove it."""
    _home("removefree")
    from ocp.team import accounts as accountsmod
    ok, msg = accountsmod.add_account("beta", lambda argv, env: 0, no_login=True)
    assert ok, msg

    app = bt.BridgeApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.press("left")          # AGENTS -> ACCOUNTS
        await pilot.press("down")          # alpha -> beta (list_accounts is sorted)
        await pilot.pause(0.1)
        screen = app.screen

        await pilot.press("x")
        await pilot.pause(0.1)
        check("accounts: x on a free account prompts for its name",
              isinstance(app.screen, TextPrompt), type(app.screen).__name__)
        for ch in "betx":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause(0.2)
        check("accounts: a wrong typed name leaves it",
              "beta" in accountsmod.paths.list_accounts(),
              str(accountsmod.paths.list_accounts()))
        check("accounts: and says nothing was removed", "not removed" in _said(screen), _said(screen))

        await pilot.press("x")
        await pilot.pause(0.1)
        for ch in "beta":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause(0.2)
        check("accounts: the exact typed name removes it",
              "beta" not in accountsmod.paths.list_accounts(),
              str(accountsmod.paths.list_accounts()))
        check("accounts: and says so", "removed account 'beta'" in _said(screen), _said(screen))


async def test_messages_panel_open_reaches_the_agents_page() -> None:
    """A MESSAGES row's `enter` opens the same conversation page an AGENTS
    row does when the message belongs to one - `actions.message_place`,
    reused rather than a separate detail view for the common case."""
    home = _home("msgopen")
    agents.register("reviewer")
    con = db.connect()
    try:
        db.set_terminal(con, "reviewer", "term_r", "wt")
    finally:
        con.close()
    from ocp.team import ask as askmod
    askmod.ask("human", "please review this", requester="reviewer")

    app = bt.BridgeApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.press("right")         # AGENTS -> MESSAGES
        await pilot.pause(0.1)
        screen = app.screen
        check("messages: right from AGENTS enters MESSAGES", screen.panel == 2, str(screen.panel))
        await pilot.press("enter")
        await pilot.pause(0.2)
        check("messages: enter opens the sender's chat page",
              isinstance(app.screen, ChatPage) and app.screen.agent == "reviewer",
              type(app.screen).__name__)
        await pilot.pause(0.3)
        text = " ".join(str(w.render()) for w in app.screen.query("#transcript Static"))
        check("messages: the message itself is on it", "please review this" in text, text)


async def test_enter_on_an_agent_opens_its_chat_page_and_esc_comes_back() -> None:
    """Task #89: Enter on an agent's row opens its chat page over the dashboard,
    and Esc leaves it back to the dashboard."""
    _home("chatopen")
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        dash = app.screen
        want = dash.sel.get(tui.AGENTS)
        check("chat open: the dashboard is on the agents panel with an agent selected",
              dash.panel == tui.AGENTS and want is not None, str(dash.sel))
        await pilot.press("enter")
        await pilot.pause(0.3)
        check("chat open: Enter opens that agent's chat page",
              isinstance(app.screen, ChatPage) and app.screen.agent == want, type(app.screen).__name__)
        await pilot.press("escape")
        await pilot.pause(0.3)
        check("chat open: Esc leaves it and the dashboard is back", app.screen is dash,
              type(app.screen).__name__)


async def test_a_double_click_on_a_row_is_enter() -> None:
    _home("dblclick")
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.3)
        dash = app.screen
        table = dash.query_one("#agents", DataTable)
        await pilot.click(table, offset=(5, 1))
        await pilot.pause(0.3)
        check("double click: one click only selects the row", app.screen is dash and dash.sel[tui.AGENTS] == "backend",
              type(app.screen).__name__)
        await pilot.pause(0.7)     # past DOUBLE_PRESS_S
        await pilot.click(table, offset=(5, 1), times=2)
        await pilot.pause(0.4)
        check("double click: two open the agent's chat page, as Enter does",
              isinstance(app.screen, ChatPage) and app.screen.agent == "backend", type(app.screen).__name__)


async def test_wake_on_a_stalled_agent_asks_first() -> None:
    """auditor #2008 BLOCKING 2a: waking a STALLED agent needs a yes first in
    curses (`_needs_confirm`, founder #1620/#1631) - checking only `label in
    tui.CONFIRM` misses it, since `wake` is deliberately left off that set for
    every other state."""
    home = _home("wake")
    agents.register("backend")
    con = db.connect()
    try:
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.STALLED)
    finally:
        con.close()
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        screen = app.screen
        await pilot.press("w")
        await pilot.pause(0.2)
        check("wake: a STALLED agent asks before clearing the stall",
              isinstance(app.screen, Confirm), type(app.screen).__name__)
        await pilot.press("y")
        deadline = asyncio.get_event_loop().time() + 5
        while asyncio.get_event_loop().time() < deadline and screen.jobs.running:
            await pilot.pause(0.1)
        check("wake: confirming it clears the stall",
              "STALLED -> QUEUED" in _said(screen), _said(screen))


async def test_messages_panel_acts_on_a_folded_row() -> None:
    """auditor #2008 BLOCKING 1: a folded row's key is `actions.fold_key`
    (`"agent:<name>"`), not an id, and the ordinary row is folded - a report
    with nothing pinning it. Acting on it must look the id up fresh, the same
    way `tui._act_on` does, rather than trying to read the key as one."""
    # auditor #2016 BLOCKING: Enter on a folded row - the ordinary way to read
    # a message - reaches the same int(target) crash, and its own path
    # (message_place -> the agent's page) also drops curses' check that the
    # page could actually be built before choosing it. Its own fixture: Enter
    # closes a report (`opened_by_human`), and the dismiss scenario below
    # needs its own two reports still queued and folded.
    _home("msgfoldopen")
    agents.register("reviewer")
    con = db.connect()
    try:
        db.set_terminal(con, "reviewer", "term_r", "wt")
    finally:
        con.close()
    from ocp.team import ask as askmod
    askmod.ask("human", "opened report", requester="reviewer", fyi=True)

    opener = bt.BridgeApp()
    async with opener.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.press("right")         # AGENTS -> MESSAGES
        await pilot.pause(0.1)
        await pilot.press("enter")
        await pilot.pause(0.2)
        check("messages: enter on a folded row does not crash the app",
              opener.is_running, "app exited")
        check("messages: it opens the sender's chat page",
              isinstance(opener.screen, ChatPage) and opener.screen.agent == "reviewer",
              type(opener.screen).__name__)

    _home("msgfold")
    agents.register("reviewer")
    con = db.connect()
    try:
        db.set_terminal(con, "reviewer", "term_r", "wt")
    finally:
        con.close()
    askmod.ask("human", "first report", requester="reviewer", fyi=True)
    askmod.ask("human", "second report", requester="reviewer", fyi=True)

    app = bt.BridgeApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        await pilot.press("right")         # AGENTS -> MESSAGES
        await pilot.pause(0.1)
        screen = app.screen
        check("messages: a folded row is on the panel and selected",
              screen.sel.get(screen.panel) == "agent:reviewer", str(screen.sel))
        await pilot.press("x")
        await pilot.pause(0.2)
        check("messages: acting on a folded row does not crash the app",
              app.is_running, "app exited")
        deadline = asyncio.get_event_loop().time() + 5
        while asyncio.get_event_loop().time() < deadline and screen.jobs.running:
            await pilot.pause(0.1)
        check("messages: it dismissed the row's latest, not a literal 'agent:reviewer'",
              "dismiss" in _said(screen).lower() or "#" in _said(screen), _said(screen))


async def test_text_prompt_keeps_a_paste_instead_of_dropping_it() -> None:
    """auditor #2008 BLOCKING 3: Textual delivers a paste as `events.Paste`,
    not as keys, so a key-only port drops what was pasted. `Composer.feed`
    needs it wrapped in its own bracketed-paste markers, the same shape a raw
    terminal read already gives it."""
    class _HostApp(bt.App):
        def __init__(self):
            super().__init__()
            self.result = "<unset>"

        async def on_mount(self):
            self.run_worker(self._read())

        async def _read(self):
            self.result = await self.push_screen_wait(TextPrompt("to backend:", multi=True))

    app = _HostApp()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        app.screen.post_message(events.Paste("line one\nline two"))
        await pilot.pause(0.2)
        await pilot.press("enter")
        await pilot.pause(0.2)
    check("prompt: a multi-line paste is sent whole, not dropped",
          app.result == "line one\nline two", repr(app.result))


async def test_copy_reaches_the_system_clipboard() -> None:
    """T121: Textual's copy also goes to the system clipboard, not only OSC 52."""
    from ocp.infra import clipboard
    copied, real = [], clipboard.copy
    clipboard.copy = lambda text: copied.append(text) or True
    try:
        app = ClipboardApp()
        async with app.run_test(size=(40, 6)) as pilot:
            await pilot.pause()
            app.copy_to_clipboard("picked")
    finally:
        clipboard.copy = real
    check("copy: the text also went to the system clipboard", copied == ["picked"], repr(copied))


async def test_a_drag_selection_is_copied_when_the_mouse_is_let_go() -> None:
    """T121, founder: Cmd+C copied nothing. The selection is copied on release."""
    from ocp.infra import clipboard
    from textual.widgets import Static
    copied, real = [], clipboard.copy
    clipboard.copy = lambda text: copied.append(text) or True
    try:
        class _HostApp(ClipboardApp):
            def compose(self):
                yield Static("hello world", id="s")

        app = _HostApp()
        async with app.run_test(size=(40, 6)) as pilot:
            await pilot.pause()
            await pilot.mouse_down("#s", offset=(0, 0))
            await pilot.hover("#s", offset=(5, 0))
            await pilot.mouse_up("#s", offset=(5, 0))
            await pilot.pause(0.2)
    finally:
        clipboard.copy = real
    check("drag: what was selected is copied on release", copied and copied[-1].startswith("hello"),
          repr(copied))


async def test_text_prompt_line_keeps_a_multiline_paste_and_shows_escapes_inert() -> None:
    """A one-line prompt joins a pasted block into one line (Input alone would
    keep only its first), and a pasted escape sequence is never handed to the
    terminal; alt+enter is a new line in a several-line prompt, enter sends."""
    class _HostApp(bt.App):
        def __init__(self, multi):
            super().__init__()
            self.multi, self.result = multi, "<unset>"

        async def on_mount(self):
            self.run_worker(self._read())

        async def _read(self):
            self.result = await self.push_screen_wait(TextPrompt("p:", multi=self.multi))

    app = _HostApp(False)
    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        app.screen.post_message(events.Paste("one\ntwo \033[2J\u009b31m\033]8;;http://evil.example\033\\x"))
        await pilot.pause(0.2)
        segs = [seg for strip in app.screen._compositor.render_strips() for seg in strip]
        text = "".join(seg.text for seg in segs)
        check("prompt: a pasted escape reaches no rendered segment",
              "\033" not in text and "\u009b" not in text
              and not any(seg.style and seg.style.link for seg in segs), repr(text[:200]))
        await pilot.press("enter")
        await pilot.pause(0.2)
    check("prompt: a one-line prompt joins a pasted block with a space",
          app.result.startswith("one two "), repr(app.result))

    app = _HostApp(True)
    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        await pilot.press("a", "alt+enter", "b")
        await pilot.press("enter")
        await pilot.pause(0.2)
    check("prompt: alt+enter is a new line and enter sends, in a several-line prompt",
          app.result == "a\nb", repr(app.result))

    app = _HostApp(True)
    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        app.screen.post_message(events.Paste("x\033[2Jy\u009bz\tw"))
        await pilot.pause(0.2)
        await pilot.press("enter")
        await pilot.pause(0.2)
    check("prompt: a pasted escape is not sent from a several-line prompt, a tab is",
          app.result == "xyz\tw", repr(app.result))


async def test_dashboard_accounts_table_is_fed_by_accounts_data() -> None:
    """The ACCOUNTS panel is a DataTable fed by `tui.accounts_data` - the rows
    (`alpha`, not logged in so its tick is a cross, and not the account work
    goes next)."""
    _home("acctdata")
    con = db.connect()
    try:
        rows, notes = tui.accounts_data(con)
    finally:
        con.close()
    check("accounts_data: one row per registered account, keyed by name",
          [r.name for r in rows] == ["alpha"], rows)
    check("accounts_data: a not-logged-in account carries the cross and not 'next'",
          "\u2718" in rows[0].mark and rows[0].next == "", rows[0])
    check("accounts_data: every column the panel draws has a cell",
          set(rows[0].cells) == set(tui.ACCT_W) - {"name"}, sorted(rows[0].cells))


async def test_dashboard_agents_table_is_fed_by_agents_data() -> None:
    """The AGENTS panel is a DataTable fed by `tui.agents_data`: one row per
    drawn agent with its account, kind, state and inbox as plain values, the
    state word and the fold count built by `state_cell`/`fold_suffix`."""
    _home("agentdata")
    con = db.connect()
    try:
        rows, notes = tui.agents_data(con)
    finally:
        con.close()
    r = rows[0]
    check("agents_data: one row per drawn agent, with its account and no error",
          [x.name for x in rows] == ["backend"] and r.acct == "alpha" and r.error is None
          and not notes, rows)
    check("agents_data: the inbox is a count and the state a word",
          r.inbox == 0 and r.state == tui.screen_state(db.connect(), db.get_agent_state(
              db.connect(), "backend"))[0], r)
    check("fold_suffix: nothing under it is nothing, a fold is its count, urgency follows",
          tui.fold_suffix(0, None) == ""
          and tui._ANSI.sub("", tui.fold_suffix(3, None)) == " +3"
          and tui._ANSI.sub("", tui.fold_suffix(3, ("\u26a0", 2, tui.RED))) == " +3 \u26a02")


async def test_dashboard_messages_table_is_fed_by_messages_data() -> None:
    """The FOR YOU panel is a DataTable fed by `tui.messages_data`: a title that
    counts what is waiting, a row per message (sender, kind, its first line
    with nothing a terminal would obey in it)."""
    _home("msgdata")
    agents.register("reviewer")
    from ocp.team import ask as askmod
    askmod.ask("human", "\n\033[2Jplease \033]8;;http://evil.example\033\\review\033]8;;\033\\ this",
               requester="reviewer")
    con = db.connect()
    try:
        title, rows, notes = tui.messages_data(con)
    finally:
        con.close()
    check("messages_data: the title counts what is waiting", "1 " in title and title.startswith("FOR YOU"), title)
    check("messages_data: one row from the sender, no note",
          [r.sender for r in rows] == ["reviewer"] and not notes, rows)
    check("messages_data: the first line is the first with something on it, inert",
          rows[0].first == "please review this", repr(rows[0].first))


async def test_stuck_data_with_no_agent_to_act_on_leaves_the_home_page_safe() -> None:
    """`tui.stuck_data` rows about the daemon or an account have no agent (agent
    None, and more than one row can share that). There is no STUCK panel: the home
    page says what is stuck on the agent's row, so it must draw with such rows and
    stay up."""
    _home("stuckdata")
    con = db.connect()
    try:
        title, rows = tui.stuck_data(con)
    finally:
        con.close()
    check("stuck_data: the daemon not running is a row with no agent to act on",
          rows and rows[0].agent is None and "daemon" in rows[0].what + rows[0].why, rows)
    check("stuck_data: with something stuck the title is plain", title == "STUCK", title)
    app = bt.BridgeApp()
    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("tab")
        await pilot.pause(0.3)
        check("home: no STUCK panel, and stuck rows with no agent leave the page up",
              app.is_running and not app.screen.query("#stuck"), app.is_running)


async def test_a_key_never_acts_on_a_row_nobody_pointed_at() -> None:
    """A selection can be stale - the row it named left the list - and a key
    pressed then must do nothing, not act on whatever is under it now."""
    _home("stalekey")
    agents.register("reviewer")
    from ocp.team import ask as askmod
    askmod.ask("human", "first thing", requester="reviewer")
    con = db.connect()
    try:
        before = [m["id"] for m in actions.messages(con)]
    finally:
        con.close()
    app = bt.BridgeApp()
    async with app.run_test(size=(110, 30)) as pilot:
        await pilot.pause(0.3)
        await pilot.press("right")         # AGENTS -> MESSAGES
        await pilot.pause(0.2)
        app.screen.sel[tui.MESSAGES] = "agent:nobody-is-called-this"
        await pilot.press("x")             # dismiss
        await pilot.pause(0.5)
        con = db.connect()
        try:
            states = [db.get_request(con, rid)["state"] for rid in before]
        finally:
            con.close()
        check("dashboard: a key on a selection that names no row acts on nothing",
              states == ["queued"] * len(before) and not app.screen.jobs.running, (states, before))


def test_key_guide_keeps_the_high_ranks_on_one_line_and_ends_with_a_question_mark() -> None:
    from ocp.ui.components.key_guide import Key, fit
    keys = [Key("a", "alpha", 1), Key("b", "bravo", 3), Key("c", "charlie", 2), Key("d", "delta", 1)]
    wide = fit(keys, 80).plain
    check("key guide: with room, every key in the order given, then ?",
          wide == "a alpha · b bravo · c charlie · d delta · ? keys", wide)
    for width in (12, 24, 31, 40):
        line = fit(keys, width)
        check(f"key guide: {width} cells is one line that fits and ends with ?",
              line.cell_len <= width and line.plain.endswith("? keys"), line.plain)
    tight = fit(keys, 31).plain
    check("key guide: the lowest rank goes first", "bravo" not in tight and "alpha" in tight and "delta" in tight, tight)


def test_key_guide_never_keeps_a_lower_rank_while_a_higher_one_is_left_out() -> None:
    """T149: a wide rank-1 key that alone still does not fit used to just be skipped, leaving
    room a rank-3 key after it would then fill - so the guide showed the least important key
    while dropping a more important one. The rank-3 key must go first regardless."""
    from ocp.ui.components.key_guide import Key, fit
    keys = [Key("a", "aa", 1), Key("b", "b" * 10, 1), Key("z", "z", 3)]
    line = fit(keys, 19).plain
    check("key guide: the rank-3 key is dropped before the rank-1 one, even though that one still does not fit",
          "z" not in line and "aa" in line and "b" * 10 not in line, line)


async def test_the_key_guide_stays_on_screen_when_the_table_is_long() -> None:
    """founder #725: with many rows the table pushed the guide off the bottom."""
    _home("guidefits")
    con = db.connect()
    try:
        for i in range(40):
            db.new_task(con, f"task {i}", "worker", "manager")
    finally:
        con.close()
    app = bt.BridgeApp()
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        await pilot.press("T")
        await pilot.pause(0.4)
        foot = app.screen.query_one("#tasks_foot").region
        check("tasks: the guide is on the last line of a screen with 40 tasks", foot.bottom <= 24, foot)


async def test_a_numeric_ime_input_refuses_a_committed_syllable_and_takes_a_digit() -> None:
    """T96: the Enter that commits a syllable goes through `Input.replace`, which checks
    `restrict` against the whole new value, so a numeric field never takes a syllable."""
    from textual.app import App
    from textual.widgets import Input
    from ocp.ui.components.ime import ImeInput

    class _App(App):
        def compose(self):
            yield ImeInput(id="n", restrict=r"[0-9]*")

    async with _App().run_test(size=(40, 6)) as pilot:
        field = pilot.app.query_one("#n", Input)
        field.focus()
        await pilot.pause(0.1)
        await pilot.press("7")
        await field._on_key(events.Key("enter", "가"))
        check("ime: a syllable is refused by a numeric field", field.value == "7", field.value)
        field.insert_text_at_cursor("8")
        check("ime: a digit is taken", field.value == "78", field.value)
        await pilot.press("enter")


def _week_sample(con, account: str, ts: int, used: int, reset: int) -> None:
    import json as _json
    con.execute("INSERT INTO usage_samples(account, ts, exists_, windows) VALUES(?,?,1,?)",
                (account, ts, _json.dumps([
                    {"minutes": 300, "used_percent": 10, "resets_at": ts + 3600, "observed_at": ts},
                    {"minutes": 10080, "used_percent": used, "resets_at": reset, "observed_at": ts}])))


def test_the_weekly_outlook_averages_accounts_and_restores_at_reset() -> None:
    from ocp.usage import outlook
    _home("outlook")
    con = db.connect()
    try:
        now = 1_800_000_000
        for h in range(24, -1, -1):
            _week_sample(con, "alpha", now - h * 3600, 50 - h, now + 6 * 3600)     # 1 point an hour
            _week_sample(con, "beta", now - h * 3600, 20, now + 3 * 86400)         # idle
        con.commit()
        o = actions.weekly_outlook(con, now)
    finally:
        con.close()
    now_left = o.past[-1][1]
    check("outlook: two accounts are counted", o.accounts == 2, o.accounts)
    check("outlook: the headroom now is the mean of what is left (alpha 50 used, beta 20)",
          now_left == 65, now_left)
    at_reset = dict(o.future)[now + 6 * 3600] if (now + 6 * 3600) in dict(o.future) else None
    check("outlook: after alpha's week resets what it used comes back", at_reset is not None and at_reset > now_left - 5,
          (at_reset, now_left))
    check("outlook: the guess ends inside 24h", o.future[-1][0] <= now + 24 * 3600, o.future[-1][0])
    con = db.connect()
    try:
        used = outlook.week_consumed(db.usage_samples(con), now - 24 * 3600, now)
    finally:
        con.close()
    check("outlook: the points used are the rises (alpha 26 to 50 = 24, beta idle = 0)",
          used == {"alpha": 24.0, "beta": 0.0}, used)
    flat = outlook.weekly_outlook([], now)
    check("outlook: no samples, no points", not flat.past and not flat.future and flat.accounts == 0)


def test_the_overlay_draws_what_the_agent_ate_in_front_of_the_headroom_and_shares_add_up() -> None:
    from ocp.ui.components.outlook_view import chart
    from ocp.usage.outlook import Outlook
    o = Outlook(past=[(0, 50.0), (1, 50.0)], future=[(2, 50.0)], accounts=1)
    plain = chart(o, 30, 8, "24h")
    over = chart(o, 30, 8, "24h", [20.0, 20.0])
    check("overlay: without it there is no magenta", not any("magenta" in str(sp.style) for sp in plain.spans))
    check("overlay: with it the agent's part is drawn in magenta in front of the headroom",
          any("magenta" in str(sp.style) for sp in over.spans))
    _home("agentseries")
    con = db.connect()
    try:
        now = 1_800_000_000
        con.execute("INSERT INTO agent_state(agent, state, updated, worktree) VALUES ('writer', 'IDLE', 0, '/w/writer')")
        con.execute("INSERT INTO token_calls VALUES('alpha','p','p1',?,'claude-sonnet-5','/w/writer',0,1000,0,0,200,0)",
                    (now - 3600,))
        con.execute("INSERT INTO token_calls VALUES('alpha','p','p2',?,'claude-sonnet-5','/w/other',0,1000,0,0,200,0)",
                    (now - 3600,))
        con.commit()
        uses = {u.agent: u for u in actions.agent_use(con, now, 24)}
    finally:
        con.close()
    from ocp.usage import stats as ustats
    con = db.connect()
    try:
        est = {u.agent: u.est_pct for u in ustats.agent_use(con, now - 86400, now, None, {"alpha": 10.0})}
    finally:
        con.close()
    check("agent use: an agent's share of the week is its cost share times the account's points",
          abs(est["writer"] - 5.0) < 1e-6 and abs(est["(unattributed)"] - 5.0) < 1e-6, est)
    check("agent use: the table splits the same calls between the agents",
          set(uses) == {"writer", "(unattributed)"} and abs(uses["writer"].share - 0.5) < 1e-6, uses)


def test_the_agents_table_is_a_tree_with_the_group_total_on_top() -> None:
    from ocp.usage.stats import AgentUse, group_uses
    uses = [AgentUse("orcapipe.ui", 10, 3.0, 0.3, 1.0, {"a": 0.3}), AgentUse("orcapipe", 5, 6.0, 0.6, 2.0, {"a": 0.6}),
            AgentUse("harbor", 1, 1.0, 0.1, 0.5, {"a": 0.1})]
    rows = group_uses(uses)
    got = [(r.key, r.label, r.depth) for r in rows]
    check("tree: the group with the most cost first, its total on top, then self and the children",
          got == [("grp:orcapipe", "orcapipe", 0), ("orcapipe.ui", "ui", 1), ("orcapipe", "self", 1),
                  ("harbor", "harbor", 0)], got)
    top = rows[0].use
    check("tree: the group row is the sum of its members", top.tokens == 15 and abs(top.cost - 9.0) < 1e-9
          and abs(top.est_pct - 3.0) < 1e-9 and abs(top.fracs["a"] - 0.9) < 1e-9, top)


def test_the_outlook_chart_draws_a_column_per_slot_with_the_guess_apart() -> None:
    from ocp.ui.components.outlook_view import chart
    from ocp.usage.outlook import Outlook
    o = Outlook(past=[(0, 100.0), (1, 50.0)], future=[(2, 25.0), (3, 0.0)], accounts=1)
    text = chart(o, 40, 8)
    check("chart: one line per row and an axis", len(text.plain.split("\n")) == 9, text.plain)
    styles = {str(sp.style) for sp in text.spans}
    check("chart: the recorded part and the guess have their own colours", {"cyan", "yellow"} <= styles, styles)


async def test_stats_screen_draws_the_weekly_graph() -> None:
    """Founder #824: S opens the weekly headroom graph, and the Stats tab shows the same."""
    from ocp.ui.pages.stats import StatsScreen
    _home("statsscreen")
    con = db.connect()
    try:
        now = int(time.time())
        for h in range(6, -1, -1):
            _week_sample(con, "alpha", now - h * 3600, 40 + h, now + 86400)
        con.execute("INSERT INTO agent_state(agent, state, updated, worktree) VALUES ('writer', 'IDLE', 0, '/w/writer')")
        con.execute("INSERT INTO token_calls VALUES('alpha','p','p1',?,'claude-sonnet-5','/w/writer',0,1000,0,0,200,0)",
                    (now - 3600,))
        con.commit()
    finally:
        con.close()
    app = bt.BridgeApp()
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        await pilot.press("S")
        await pilot.pause(0.3)
        screen = app.screen
        check("stats: S opens the screen", isinstance(screen, StatsScreen), type(screen).__name__)
        view = screen.query_one("#stats_view")
        check("stats: the recorded week is drawn", view.outlook.accounts == 1 and view.outlook.past, view.outlook.accounts)
        await pilot.press("r")
        await pilot.pause(0.3)
        check("stats: r steps the date range and the graph follows",
              view.range == "3d" and view.outlook.future[-1][0] - view.outlook.past[-1][0] >= 71 * 3600,
              (view.range, view.outlook.future[-1][0] - view.outlook.past[-1][0]))
        table = view.query_one("#outlook_table")
        check("stats: the agents' use is a table under the graph", table.row_count == len(view.uses) and len(view.uses) >= 1,
              table.row_count)
        await pilot.press("enter")
        await pilot.pause(0.3)
        check("stats: enter on an agent overlays what it ate on the same graph, on the same screen",
              view.picked == "writer" and app.screen is screen and len(view.over) == len(view.outlook.past), (view.picked, type(app.screen).__name__))
        await pilot.press("enter")
        await pilot.pause(0.3)
        check("stats: enter on it again goes back to the week", view.picked is None, view.picked)
        await pilot.press("escape")
        await pilot.pause(0.2)
        from textual.widgets import Tabs
        app.screen.query_one("#scope", Tabs).active = "stats"
        await pilot.pause(0.3)
        tab = app.screen.query_one("#stats_view")
        check("stats tab: home has a Stats tab with the same graph", tab.display and tab.outlook.accounts == 1,
              (tab.display, tab.outlook.accounts))


async def test_tasks_screen_marks_batches_and_shows_each_refusal() -> None:
    """T69: T opens the tasks screen; space marks; p sets the priority of the
    marked tasks; c on tasks that are not done shows each refusal with its reason."""
    from ocp.ui.pages.tasks import TasksScreen
    from textual.widgets import Tabs
    _home("tasksscreen")
    con = db.connect()
    try:
        a = db.new_task(con, "first", "worker", "manager")
        b = db.new_task(con, "second", "worker", "manager")
        c = db.new_task(con, "third", "worker", "manager")
    finally:
        con.close()
    app = bt.BridgeApp()
    async with app.run_test(size=(110, 26)) as pilot:
        await pilot.pause()
        await pilot.press("T")
        await pilot.pause()
        screen = app.screen
        view = screen.query_one('#tasks_view')
        check("tasks: T opens the screen, every open task listed",
              isinstance(screen, TasksScreen) and {a, b, c} <= {r["id"] for r in view.rows},
              type(screen).__name__)
        foot = str(view.query_one("#tasks_foot").render())
        check("tasks: the foot guides keys on one line and ends with ?",
              "mark" in foot and "close" in foot and foot.rstrip().endswith("? keys") and "\n" not in foot, foot)
        await pilot.press("question_mark")
        await pilot.pause(0.2)
        from ocp.ui.components.key_guide import KeyHelp
        check("tasks: ? opens a box with every key", isinstance(app.screen, KeyHelp)
              and "merge" in str(app.screen.query_one("#keys").render()), type(app.screen).__name__)
        await pilot.press("escape")
        await pilot.pause(0.2)
        check("tasks: esc closes the box and stays on the screen", isinstance(app.screen, TasksScreen),
              type(app.screen).__name__)
        await pilot.press("space", "down", "space")
        await pilot.pause()
        check("tasks: space marks the rows", len(view.checked) == 2, view.checked)
        await pilot.press("p")
        await pilot.pause()
        await pilot.press("1", "enter")
        await pilot.pause(0.3)
        from ocp.ui.components.confirm import Confirm
        check("tasks: a batch asks once more, naming the tasks", isinstance(app.screen, Confirm)
              and "2 tasks" in str(app.screen.query_one("#question").render()), type(app.screen).__name__)
        await pilot.press("y")
        await pilot.pause(0.3)
        con = db.connect()
        try:
            prios = [db.get_task(con, t)["priority"] for t in (a, b)]
        finally:
            con.close()
        check("tasks: p wrote P1 to both marked tasks", prios == [1, 1], prios)
        await pilot.press("c")
        await pilot.pause(0.3)
        await pilot.press("y")
        await pilot.pause(0.3)
        said = " ".join(n.message for n in app._notifications)
        check("tasks: c on tasks that are not done toasts a line per refusal, with the reason",
              "T" in said and any(n.severity == "error" for n in app._notifications), said)
        await pilot.press("enter")
        await pilot.pause(0.3)
        check("tasks: enter opens the task's detail", type(app.screen).__name__ == "TaskDetail", type(app.screen).__name__)
        await pilot.press("escape", "escape")
        await pilot.pause(0.2)
        con = db.connect()
        try:
            mine = db.new_task(con, "founder's own", "worker", actions.HUMAN)
        finally:
            con.close()
        home = app.screen
        home.query_one("#scope", Tabs).active = "tasks"
        await pilot.pause(0.3)
        view = home.query_one("#tasks_view")
        ids = {r["id"] for r in view.rows}
        check("tasks tab: only the tasks that concern the founder", mine in ids and not ({a, b, c} & ids), ids)
        view.query_one("#tasks_table").focus()
        await pilot.press("f")
        await pilot.pause(0.2)
        check("tasks tab: f steps to an agent's own tasks", view.owner == "worker", view.owner)
        from textual.widgets import Select
        dd = view.query_one("#f_agent", Select)
        check("tasks tab: the agent dropdown follows f", dd.value == "worker", dd.value)
        dd.value = ""
        await pilot.pause(0.2)
        check("tasks tab: choosing all in the dropdown clears the agent", view.owner is None, view.owner)
        view.query_one("#f_sort", Select).value = "number"
        await pilot.pause(0.2)
        check("tasks tab: the sort dropdown sets the sort", view.by == "number", view.by)
        await pilot.press("S")
        await pilot.pause(0.2)
        check("tasks tab: S reverses the sort and the head shows it", view.rev
              and "▼" in str(view.table.columns["number"].label), view.rev)
        view.query_one("#tasks_table").focus()
        await pilot.press("s")
        await pilot.pause(0.2)
        check("tasks tab: s sorts by the next column", view.by == "state", view.by)
        view.action_check_all()
        await pilot.pause(0.2)
        check("tasks tab: the head of the mark column marks every row shown",
              {r["id"] for r in view.rows} <= view.checked and view._mark_head == "[x]", view.checked)
        view.action_check_all()
        await pilot.pause(0.2)
        check("tasks tab: and unmarks them when all are marked", not view.checked, view.checked)
        from ocp.ui.components.tasks_view import TaskPane
        pane = view.query_one(TaskPane)
        await pilot.resize_terminal(170, 40)
        await pilot.pause(0.3)
        check("tasks tab: a wide terminal shows the task under the cursor at the right",
              pane.display and view.rows[view.cur]["title"] in str(pane.query_one("#pane_text").render()), pane.display)
        from textual.widgets import Button
        check("tasks tab: the pane has Close, Reopen and Discard, enabled as the task page has them",
              all(pane.query_one(f"#pane_{n}", Button).disabled == (not pane.d[f"can_{n}"])
                  for n in ("close", "reopen", "discard")), pane.d)
        await pilot.resize_terminal(100, 24)
        await pilot.pause(0.3)
        check("tasks tab: a narrow one hides the pane", not pane.display)
        from ocp.ui.components.tasks_view import StatePicker
        await pilot.press("v")
        await pilot.pause(0.2)
        check("tasks tab: v opens the state checkboxes", isinstance(app.screen, StatePicker), type(app.screen).__name__)
        await pilot.press("space")
        await pilot.pause(0.2)
        check("tasks tab: space marks without applying", "draft" in view.states and isinstance(app.screen, StatePicker))
        await pilot.press("enter")
        await pilot.pause(0.2)
        check("tasks tab: enter applies the checkboxes and closes (draft now unmarked)",
              not isinstance(app.screen, StatePicker) and "draft" not in view.states
              and {"queued", "doing", "done"} <= view.states, view.states)
        await pilot.press("a")
        await pilot.pause(0.2)
        check("tasks tab: a adds the closed states", {"closed", "discarded", "merged"} <= view.states, view.states)
        agent_dd = view.query_one("#f_agent", Select)
        rebuilt = []
        real = agent_dd.set_options
        agent_dd.set_options = lambda *a, **k: (rebuilt.append(1), real(*a, **k))[1]
        await pilot.pause(2.3)
        view.refresh_rows()
        await pilot.pause(0.2)
        agent_dd.set_options = real
        check("tasks tab: a refresh with the same agents does not rebuild the agent dropdown", not rebuilt, len(rebuilt))


async def test_b_opens_the_tasks_tab_on_the_founders_done_tasks() -> None:
    from textual.widgets import Tabs
    from ocp.ui.components import summary as sm
    from ocp.ui.components.tasks_view import TasksView
    _home("checks")
    agents.register("reviewer")
    con = db.connect()
    try:
        mine = db.new_task(con, "founder's done", "worker", actions.HUMAN)
        theirs = db.new_task(con, "someone else's done", "worker", "orcapipe")
        for t in (mine, theirs):
            con.execute("UPDATE tasks SET done_evidence = 'x', done_at = 1 WHERE id = ?", (t,))
        con.commit()
    finally:
        con.close()
    app = bt.BridgeApp()
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        page = app.screen
        view = page.query_one("#tasks_view", TasksView)
        await pilot.press("b")
        await pilot.pause(0.3)
        ids = {r["id"] for r in view.rows}
        check("checks: b goes to the Tasks tab, filtered to the done tasks the founder signs off",
              app.screen is page and page.query_one("#scope", Tabs).active == "tasks"
              and mine in ids and theirs not in ids, ids)
        page.query_one("#scope", Tabs).active = "home"
        await pilot.pause(0.2)
        page.query_one("#scope", Tabs).active = "tasks"
        await pilot.pause(0.3)
        check("checks: the tab opened by hand is not filtered", view.closer is None and "doing" in view.states)
        view.states = {"queued"}
        page.query_one("#scope", Tabs).active = "home"
        await pilot.pause(0.2)
        page.query_one("#scope", Tabs).active = "tasks"
        await pilot.pause(0.3)
        check("checks: a filter the founder set stays when the tab is left and come back to", view.states == {"queued"})
        page.query_one("#scope", Tabs).active = "home"
        await pilot.pause(0.2)
        text = sm.summary_text(True, "t", sm.Summary(0, 0, 1))
        check("checks: the to-check part of the header is a link to the screen's checks action",
              any(getattr(sp.style, "meta", {}).get("@click") == "screen.checks" for sp in text.spans))
        await page.run_action("checks")
        await pilot.pause(0.3)
        check("checks: that action goes to the same filtered tab",
              page.query_one("#scope", Tabs).active == "tasks" and {r["id"] for r in view.rows} == {mine})


async def test_read_only_page_shows_foreign_text_inert() -> None:
    """Auditor #2259: a page shows text an agent wrote (message bodies, a
    handoff), so a colour code, an OSC 8 link, a C1 CSI or a BEL in a row must
    not reach the terminal."""
    _home("readonly_inert")
    bad = "red:\033[31mRED\033[0m link:\033]8;;http://evil.example\033\\click\033]8;;\033\\ c1:\u009b2J bel:\a end"
    app = bt.BridgeApp()
    async with app.run_test(size=(80, 12)) as pilot:
        await pilot.pause()
        secs = [("ONE", [bad] + [f"row {i}" for i in range(30)]), ("TWO", ["x"])]
        app.push_screen(ReadOnly("t", secs))
        await pilot.pause()
        segs = [seg for strip in app.screen._compositor.render_strips() for seg in strip]
        text = "".join(seg.text for seg in segs)
        check("readonly: no C1 or BEL reaches the rendered text",
              "\u009b" not in text and "\a" not in text, repr(text[:200]))
        check("readonly: no row carries a hyperlink",
              not any(seg.style and seg.style.link for seg in segs))
        check("readonly: the visible words survive", "red:RED" in text and "click" in text, text[:200])


def test_only_y_n_or_escape_answers_a_confirm() -> None:
    """A wheel notch reaches a confirm as an arrow key and must not answer it."""
    for key, ch in (("up", None), ("down", None), ("left", None), ("right", None),
                    ("enter", "\r"), ("space", " "), ("x", "x"), ("pageup", None)):
        check(f"confirm: {key} leaves it open", confirm_answer(key, ch) is None)
    check("confirm: y confirms", confirm_answer("y", "y") is True)
    check("confirm: Y confirms", confirm_answer("Y", "Y") is True)
    check("confirm: n declines", confirm_answer("n", "n") is False)
    check("confirm: escape declines", confirm_answer("escape", "\x1b") is False)
    check("confirm: ctrl+c declines", confirm_answer("ctrl+c", "\x03") is False)


async def test_a_confirm_on_screen_ignores_an_arrow_and_answers_n() -> None:
    """auditor #2281: the wiring, not only the rule - a wheel notch leaves the
    pushed Confirm up and a later n answers it."""
    _home("confirm")
    answers: list = []
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        app.push_screen(Confirm("close it"), answers.append)
        await pilot.pause()
        await pilot.press("down", "up", "pagedown")
        await pilot.pause()
        check("confirm: arrows leave the pushed Confirm up",
              isinstance(app.screen, Confirm) and answers == [], (type(app.screen).__name__, answers))
        await pilot.press("n")
        await pilot.pause()
        check("confirm: n then answers it False", answers == [False], answers)


def test_the_menu_offers_what_the_text_could_become() -> None:
    from ocp.ui import chat_page as cp
    tasks = [(84, "owns", "waiting", "keeper fix cache", "orcapipe", 300),
             (85, "owns", "doing", "clerk discard", "orcapipe", 200),
             (7, "opened", "done", "auditor pass", "orcapipe", 100)]
    reqs = [(2305, "human -> orcapipe", "first line"), (2289, "human -> orcapipe", "other")]

    def names(text):
        return [x.label[0] for x in cp.suggest(text, tasks, reqs)]
    check("menu: a slash lists every page command",
          names("/") == ["/chat", "/all", "/task", "/sort", "/settings", "/back"], str(names("/")))
    check("menu: a slash narrows by the letters typed", names("/ta") == ["/task"], str(names("/ta")))
    check("menu: /task lists the tasks, newest change first", names("/task ") == ["T84", "T85", "T7"])
    check("menu: /task narrows by a word of the title", names("/task audit") == ["T7"])
    check("menu: /task narrows by the digits of the number", names("/task 8") == ["T84", "T85"])
    check("menu: /sort names the columns", names("/sort ti") == ["title"], str(names("/sort ti")))
    check("menu: a hash lists tasks then requests", names("see #") == ["T84", "T85", "T7", "#2305", "#2289"])
    check("menu: /task also takes the T form, and a task inserts as T<n> but a request as its bare number",
          names("/task t8") == ["T84", "T85"]
          and [x.arg for x in cp.suggest("see #", tasks, reqs)][:1] == ["T84"]
          and [x.arg for x in cp.suggest("see #", tasks, reqs)][3:4] == ["2305"])
    check("menu: a hash narrows by the digits after it", names("see #23") == ["#2305"])
    check("menu: a hash not after a space, or with letters, offers nothing",
          names("a#2") == [] and names("a #zz") == [])
    check("menu: plain text and unknown commands offer nothing", names("hello") == [] and names("/nope") == [])
    check("menu: text after a newline is never a command", names("hi\n/task") == [])
    pick = cp.suggest("/task 8", tasks, reqs)[0]
    check("menu: Tab on a task fills the command, Enter opens it",
          pick.tab == "/task 84" and pick.action == "open" and pick.arg == "84")
    ref = cp.suggest("see #", tasks, reqs)[0]
    check("menu: a task reference inserts T<n>", ref.action == "insert" and ref.arg == "T84")


def test_the_task_pages_write_a_task_as_T_and_a_request_as_a_bare_number() -> None:
    from ocp.ui import chat_page as cp
    d = {"id": 1, "title": "t", "state": "doing", "owner": "o", "opened_by": "o", "created_at": 1,
         "parent": (9, "up"), "done": None, "closed": None, "asker": "o",
         "children": [(2, "kid", "waiting")], "requests": [(3, "a", "b", "queued", "line")]}
    page = cp.task_page(d)
    check("page: the badge and the parent and the sub-tasks say T<n>",
          "T1" in page["badge"].plain and "T9" in page["meta"].plain
          and page["kids"][0].plain.startswith("T2"), str((page["badge"].plain, page["meta"].plain)))
    check("page: a request under it is still a bare #<n>", page["reqs"][0].plain.startswith("#3"))


def test_the_typed_history_keeps_the_newest_and_never_repeats() -> None:
    from ocp.ui import chat_page as cp
    from ocp.infra import paths
    _home("hist")
    check("history: a repeat moves up instead of doubling", cp.remember(["a", "b", "c"], "b") == ["a", "c", "b"])
    check("history: blank is not remembered", cp.remember(["a"], "  \n") == ["a"])
    full = [str(i) for i in range(cp.HISTORY_MAX)]
    grown = cp.remember(full, "new")
    check("history: only the newest HISTORY_MAX are kept",
          len(grown) == cp.HISTORY_MAX and grown[-1] == "new" and grown[0] == "1")
    check("history: matches are newest first and ignore case",
          cp.history_matches("AB", ["xab", "no", "ab2"]) == ["ab2", "xab"])
    check("history: no file is an empty history", cp.load_history() == [])
    cp.save_history(["one", "two é"])
    check("history: it survives a reload", cp.load_history() == ["one", "two é"]
          and str(cp.history_file()).startswith(str(paths.home())))
    cp.history_file().write_text("{not json")
    check("history: a corrupt file is an empty history, not an error", cp.load_history() == [])
    cp.history_file().write_text('[1, "ok", null]')
    check("history: only strings are read back", cp.load_history() == ["ok"])


def test_the_chat_is_paged_and_hides_what_never_reached_the_agent() -> None:
    from ocp.team import ask as askmod
    from ocp.ui import chat_page as cp
    _home("chat")
    agents.register("reviewer")
    con = db.connect()
    try:
        for body in ("one", "two", "three"):
            actions.chat_reply(con, "reviewer", body)
        rows, more = cp.requests_page(con, "reviewer", False, limit=2)
        check("chat: a page is the newest requests, oldest first, and says more remain",
              [r["body"] for r in rows] == ["two", "three"] and more)
        older, more = cp.requests_page(con, "reviewer", False, before=rows[0]["id"], limit=2)
        check("chat: the page before it holds the rest", [r["body"] for r in older] == ["one"] and not more)
        sig = cp.signature(con, "reviewer", False)
        second = rows[0]["id"]
        first = second - 1
        askmod.cancel(second)
        check("chat: a request taken back before delivery leaves the chat",
              [r["body"] for r in cp.requests_page(con, "reviewer", False)[0]] == ["one", "three"])
        check("chat: and the change is seen by the cheap reading", cp.signature(con, "reviewer", False) != sig)
        msgs = cp.messages_of(cp.requests_page(con, "reviewer", False)[0], "reviewer")
        check("chat: a queued message can be cancelled, not yet delivered",
              msgs[0]["queued"] and not msgs[0]["delivered"] and msgs[0]["side"] == "you", str(msgs[0]))
        con.execute("UPDATE requests SET delivered_at = ? WHERE id = ?", (int(time.time()), first))
        con.commit()
        check("chat: once delivered it says so",
              cp.messages_of(cp.requests_page(con, "reviewer", False)[0], "reviewer")[0]["delivered"])
        askmod.cancel(first)
        cancelled = cp.messages_of(cp.requests_page(con, "reviewer", False)[0], "reviewer")
        check("chat: a request cancelled after delivery stays, marked, without a reply from the agent",
              cancelled[0]["cancelled"] and all(m["part"] == 0 for m in cancelled), str(cancelled[0]))
    finally:
        con.close()


def _page_rows(con, rid: int) -> list:
    from ocp.ui import chat_page as cp
    return [r for r in cp.requests_page(con, "reviewer", False)[0] if r["id"] == rid]


def test_a_dismissed_request_leaves_no_dismissed_line_in_the_chat() -> None:
    from ocp.team import ask as askmod
    from ocp.ui import chat_page as cp
    _home("dismissline")
    agents.register("reviewer")
    con = db.connect()
    try:
        ok, _, rid = askmod.ask("human", "fyi: done", requester="reviewer")
        ok2, said = actions.dismiss(con, rid, actions.HUMAN, no_task="just an fyi")
        msgs = cp.messages_of(_page_rows(con, rid), "reviewer")
        check("chat: a dismissal is not drawn as a line, the ask itself stays",
              ok and ok2 and [m["part"] for m in msgs] == [0] and not any("dismissed" in m["text"] for m in msgs),
              str(msgs))
    finally:
        con.close()


def test_a_reply_closes_a_waiting_question_or_starts_a_new_request() -> None:
    from ocp.team import ask as askmod
    from ocp.ui import chat_page as cp
    _home("reply")
    agents.register("reviewer")
    con = db.connect()
    try:
        ok, _, rid = askmod.ask("human", "merge it?", requester="reviewer")
        check("reply: the agent's question is waiting for the human",
              ok and cp.messages_of(_page_rows(con, rid), "reviewer")[0]["waiting"])
        ok1, _ = cp.send_reply(con, "reviewer", rid, "yes")
        after = db.get_request(con, rid)
        check("reply: a reply to a waiting question is its verdict",
              ok1 and after["state"] == "done" and after["verdict"] == "yes", str(dict(after)))
        check("reply: the question then reads as answered", cp.messages_of(_page_rows(con, rid), "reviewer")[0]["answered"])
        ok2, _ = cp.send_reply(con, "reviewer", rid, "and again")
        child = db.get_request(con, rid + 1)
        check("reply: a second reply is a new request to the agent under it",
              ok2 and child is not None and child["parent_id"] == rid and child["target"] == "reviewer"
              and child["requester"] == actions.HUMAN)
        ok3, _, mine = askmod.ask("reviewer", "status?", requester=actions.HUMAN)
        con.execute("UPDATE requests SET state = 'done', verdict = 'fine' WHERE id = ?", (mine,))
        con.commit()
        ok4, _ = cp.send_reply(con, "reviewer", mine, "thanks, now ship it")
        check("reply: an answer to the human's own request is marked read, as the mailbox does",
              ok3 and ok4 and db.get_request(con, mine)["announced_at"], str(dict(db.get_request(con, mine))))
        _, _, theirs = askmod.ask("reviewer", "status again?", requester=actions.HUMAN)
        ok5, _ = cp.send_reply(con, "backend", theirs, "and the date?")
        kid5 = db.get_request(con, theirs + 1)
        check("reply: a reply from another agent's page (All chat) goes to the agent on that message, under it",
              ok5 and kid5["target"] == "reviewer" and kid5["parent_id"] == theirs, str(dict(kid5)))
        got = cp.messages_of(cp.requests_page(con, "reviewer", False)[0], "reviewer")
        check("reply: the message replied to reads answered",
              next(m for m in got if m["rid"] == mine and m["part"] == 1)["answered"], str(got))
        for _ in range(cp.PAGE):
            con.execute("INSERT INTO requests (ts, requester, target, state, body, parent_id) "
                        "SELECT ts, requester, target, 'done', 'filler', NULL FROM requests WHERE id = ?", (rid,))
        con.commit()
        page, _more = cp.requests_page(con, "reviewer", False)
        older, _ = cp.requests_page(con, "reviewer", False, before=page[0]["id"])
        parent_older = [m for m in cp.messages_of(older, "reviewer") if m["rid"] == mine and m["part"] == 1]
        check("reply: it still reads answered when scrolled up to, its reply on the newer page",
              mine < page[0]["id"] and parent_older and parent_older[0]["answered"], str([r["id"] for r in older]))
        check("reply: a blank reply is refused", cp.send_reply(con, "reviewer", rid, "  ")[0] is False)
        check("reply: a reply to a request that is gone is refused",
              cp.send_reply(con, "reviewer", 99999, "x")[0] is False)
    finally:
        con.close()


def test_an_agents_queued_message_waits_below_the_line_without_buttons() -> None:
    from ocp.team import ask as askmod
    from ocp.ui import chat_page as cp
    _home("heldbelow")
    agents.register("reviewer")
    _, _, first = askmod.ask("reviewer", "hello", requester="backend")
    con = db.connect()
    try:
        con.execute("UPDATE requests SET delivered_at = ts WHERE id = ?", (first,))
        _, _, held = askmod.ask("reviewer", "second, not read yet", requester="backend")
        _, _, later = askmod.ask("backend", "asked after", requester="reviewer")
        con.commit()
        rows = cp.requests_page(con, "reviewer", True)[0]
    finally:
        con.close()
    msgs = cp.messages_of(rows, "reviewer")
    widgets = cp.transcript_widgets(msgs, False)
    texts = [w.m["text"] if isinstance(w, cp.ChatMessage) else "--" for w in widgets]
    check("chat: an agent's undelivered message sits below the queue line, after the delivered ones",
          texts.index("--") == texts.index("second, not read yet") - 1
          and texts.index("hello") < texts.index("--") and texts.index("asked after") > texts.index("--"),
          str(texts))
    check("chat: it has no Cancel / Send now buttons", not any(
        m["queued"] for m in msgs if m["text"] == "second, not read yet"))


async def test_escape_does_not_cancel_a_queued_message() -> None:
    from ocp.team import ask as askmod
    _home("noesc")
    agents.register("reviewer")
    _, _, mine = askmod.ask("reviewer", "still queued", requester=actions.HUMAN)
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await app.push_screen(ChatPage("reviewer"))
        await pilot.pause(0.3)
        await pilot.press("escape")
        await pilot.pause(0.3)
        left = not isinstance(app.screen, ChatPage)
    con = db.connect()
    try:
        state = db.get_request(con, mine)["state"]
    finally:
        con.close()
    check("chat: Esc no longer takes back a queued message (Cancel is the way), it just leaves",
          state == "queued" and left, state)


async def test_a_plain_input_keeps_the_syllable_an_ime_commits_with_enter() -> None:
    from unittest import mock
    from textual.widgets import Input as _Input
    from ocp.team import accounts as accountsmod
    _home("imeplain")
    with mock.patch.object(accountsmod, "identity", lambda acct: "me@example.com"):
        app = bt.BridgeApp()
        async with app.run_test(size=(120, 34)) as pilot:
            await pilot.pause(0.3)
            app.push_screen(AccountPage("alpha"))
            await pilot.pause(0.5)
            page = app.screen
            box = page.query_one("#a_name", _Input)
            box.focus()
            await pilot.press("안")
            await app.on_event(events.Key("enter", "녕"))
            await pilot.pause(0.3)
            check("account page: the alias box takes a syllable the IME commits with enter, and saves it",
                  box.value == "안녕" and "안녕" in _said(page), f"{box.value!r} {_said(page)!r}")


async def test_a_reply_to_a_selected_message_carries_its_request() -> None:
    from ocp.team import ask as askmod
    _home("selreply")
    agents.register("reviewer")
    _, _, mine = askmod.ask("reviewer", "status?", requester=actions.HUMAN)
    con = db.connect()
    try:
        con.execute("UPDATE requests SET state = 'done', verdict = 'fine' WHERE id = ?", (mine,))
        con.commit()
    finally:
        con.close()
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await app.push_screen(ChatPage("reviewer"))
        await pilot.pause(0.3)
        page = app.screen
        msg = next(w for w in page.query("ChatMessage") if w.m["rid"] == mine and w.m["part"] == 1)
        await pilot.click(msg)
        await pilot.pause(0.1)
        check("reply: clicking a message selects it", page.replying is msg, str(page.replying))
        for ch in "ok":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause(1.0)
    con = db.connect()
    try:
        kid = con.execute("SELECT parent_id, target FROM requests WHERE requester = ? AND id > ?",
                          (actions.HUMAN, mine)).fetchone()
    finally:
        con.close()
    check("reply: the send carries the selected message's request as its parent",
          kid is not None and kid["parent_id"] == mine, str(dict(kid)) if kid else "none")


def test_the_tasks_of_an_agent_are_what_it_owns_or_opened() -> None:
    from ocp.ui import chat_page as cp
    _home("tasks")
    agents.register("reviewer")
    con = db.connect()
    try:
        a = db.new_task(con, "bravo", "backend", "orcapipe")
        b = db.new_task(con, "alpha", "backend", "zed")
        c = db.new_task(con, "opened by it", "orcapipe", "backend")
        db.new_task(con, "someone else's", "orcapipe", "orcapipe")
        closed = db.new_task(con, "closed", "backend", "orcapipe")
        con.execute("UPDATE tasks SET closed_by = 'human' WHERE id = ?", (closed,))
        con.execute("UPDATE tasks SET created_at = created_at - 5000 WHERE id = ?", (a,))
        con.commit()
        rows = cp.tasks_of(con, "backend")
        check("tasks: what it owns or opened, not others', not closed",
              sorted(r[0] for r in rows) == sorted([a, b, c]), str([r[0] for r in rows]))
        check("tasks: the role is owns or opened",
              {r[0]: r[1] for r in rows} == {a: "owns", b: "owns", c: "opened"})
        check("tasks: the other party is shown",
              {r[0]: r[4] for r in rows} == {a: "orcapipe", b: "zed", c: "orcapipe"})
        actions.chat_reply(con, "reviewer", "touch")
        db.link_request(con, a, actions.chat_parent(con, "reviewer"))
        newer = {r[0]: r[5] for r in cp.tasks_of(con, "backend")}
        check("tasks: a linked request is a change to the task", newer[a] > {r[0]: r[5] for r in rows}[a])
    finally:
        con.close()


def test_batch_results_are_one_toast_for_what_worked_and_one_for_the_refusals() -> None:
    from ocp.ui.components.tasks_view import result_notes
    got = result_notes([(14, True, "x"), (15, True, "y"), (16, False, "not done yet")], "close")
    check("results: successes in one toast, the refusals in another with the reason",
          got == [("closed 2: T14 T15", "information"), ("T16: not done yet", "error")], got)


async def test_marks_hidden_by_a_state_change_are_dropped_so_an_action_cannot_reach_them() -> None:
    """T149: a mark used to survive a row leaving the current states filter, so c/d/o/p/m
    could act on a task the table no longer shows. A mark not among the current rows is
    dropped the next time the view refreshes."""
    from ocp.ui.components.tasks_view import TasksView
    _home("marksdrop")
    con = db.connect()
    try:
        a = db.new_task(con, "will close", "worker", "manager")
        b = db.new_task(con, "stays open", "worker", "manager")
    finally:
        con.close()
    app = bt.BridgeApp()
    async with app.run_test(size=(110, 26)) as pilot:
        await pilot.pause()
        await pilot.press("T")
        await pilot.pause()
        view = app.screen.query_one("#tasks_view", TasksView)
        await pilot.press("space", "down", "space")
        await pilot.pause()
        check("marksdrop: both marked", view.checked == {a, b}, view.checked)
        con = db.connect()
        try:
            con.execute("UPDATE tasks SET done_evidence = 'x', done_at = 1 WHERE id = ?", (a,))
            con.commit()
            ok, _ = actions.close_task(con, a, "manager")
        finally:
            con.close()
        check("marksdrop: the task closed", ok)
        view.refresh_rows()
        check("marksdrop: the closed task left the row list", a not in {r["id"] for r in view.rows}, view.rows)
        check("marksdrop: its mark was dropped along with it", view.checked == {b}, view.checked)


async def test_merge_refuses_instead_of_crashing_when_the_cursor_row_is_gone() -> None:
    """T149: `_act`'s merge branch reads `self.rows[self.cur]` after the Confirm modal
    returns - if a background refresh emptied or reordered the rows while that modal was
    open (the marked tasks closed elsewhere), it must refuse, not raise IndexError."""
    from ocp.ui.components.tasks_view import TasksView
    from ocp.ui.components.confirm import Confirm
    _home("mergerace")
    con = db.connect()
    try:
        a = db.new_task(con, "keep", "worker", "manager")
        b = db.new_task(con, "fold", "worker", "manager")
    finally:
        con.close()
    app = bt.BridgeApp()
    async with app.run_test(size=(110, 26)) as pilot:
        await pilot.pause()
        await pilot.press("T")
        await pilot.pause()
        view = app.screen.query_one("#tasks_view", TasksView)
        await pilot.press("space", "down", "space")
        await pilot.pause()
        await pilot.press("m")
        await pilot.pause(0.2)
        check("mergerace: merge asks first", isinstance(app.screen, Confirm), type(app.screen).__name__)
        con = db.connect()
        try:
            con.execute("UPDATE tasks SET done_evidence = 'x', done_at = 1 WHERE id IN (?, ?)", (a, b))
            con.commit()
            oks = [actions.close_task(con, t, "manager")[0] for t in (a, b)]
        finally:
            con.close()
        check("mergerace: both closed", all(oks), oks)
        view.refresh_rows()      # what the 2s interval would have done meanwhile
        check("mergerace: the row list emptied under the open modal", not view.rows, view.rows)
        await pilot.press("y")
        await pilot.pause(0.2)
        check("mergerace: no crash - the app is still on the tasks screen", app.screen is not None
              and not isinstance(app.screen, Confirm), type(app.screen).__name__)
        check("mergerace: refused with a plain reason instead",
              any("no row under the cursor" in n.message for n in app._notifications),
              [n.message for n in app._notifications])


async def test_the_agent_tasks_tab_shows_the_pane_when_wide() -> None:
    from textual.widgets import Tabs
    from ocp.ui import chat_page as cp
    _home("agentpane")
    agents.register("reviewer")
    con = db.connect()
    try:
        db.new_task(con, "pane subject", "reviewer", "orcapipe")
    finally:
        con.close()
    app = bt.BridgeApp()
    async with app.run_test(size=(170, 40)) as pilot:
        await app.push_screen(ChatPage("reviewer"))
        await pilot.pause(0.3)
        page = app.screen
        page.query_one(Tabs).active = "tasks"
        await pilot.pause(0.5)
        pane = page.query_one(cp.TaskPane)
        check("agent tasks: a wide terminal shows the pane with the task under the cursor",
              pane.display and "pane subject" in str(pane.query_one("#pane_text").render()), pane.display)
        await pilot.resize_terminal(100, 30)
        await pilot.pause(0.3)
        check("agent tasks: a narrow one hides it", not pane.display)


async def test_the_agent_tasks_tab_is_the_tasks_view_scoped_to_the_agent() -> None:
    from textual.widgets import DataTable, Select, Tabs
    from ocp.ui.components.tasks_view import TasksView
    _home("agentview")
    agents.register("reviewer")
    con = db.connect()
    try:
        own = db.new_task(con, "its own", "reviewer", "orcapipe")
        opened = db.new_task(con, "it opened", "orcapipe", "reviewer")
        db.new_task(con, "someone else's", "orcapipe", "orcapipe")
    finally:
        con.close()
    app = bt.BridgeApp()
    async with app.run_test(size=(170, 40)) as pilot:
        await app.push_screen(ChatPage("reviewer"))
        await pilot.pause(0.3)
        page = app.screen
        page.query_one(Tabs).active = "tasks"
        await pilot.pause(0.5)
        view = page.query_one(TasksView)
        check("agent tasks: the rows are what it owns or opened",
              sorted(r["id"] for r in view.rows) == sorted([own, opened]), str([r["id"] for r in view.rows]))
        check("agent tasks: no agent dropdown, the sort and state filters stay",
              not view.query("#f_agent") and view.query_one("#f_sort", Select) is not None)
        await pilot.press("space")
        await pilot.pause(0.2)
        check("agent tasks: space marks a row", len(view.checked) == 1, str(view.checked))
        await pilot.press("A")
        await pilot.pause(0.2)
        check("agent tasks: A marks every row shown", view.checked == {own, opened}, str(view.checked))
        page.set_sort("title")
        check("agent tasks: /sort sorts the view", view.by == "title", view.by)


def test_the_task_page_offers_discard_only_on_the_founders_waiting_task() -> None:
    from ocp.team import ask as askmod
    from ocp.ui import chat_page as cp
    _home("discardable")
    agents.register("reviewer")
    con = db.connect()
    try:
        mine = db.new_task(con, "mine", "backend", actions.HUMAN)
        theirs = db.new_task(con, "theirs", "backend", "orcapipe")
        done = db.new_task(con, "done", "backend", actions.HUMAN)
        con.execute("UPDATE tasks SET done_evidence = 'x', done_at = 1 WHERE id = ?", (done,))
        doing = db.new_task(con, "doing", "backend", actions.HUMAN)
        ok, _, rid = askmod.ask("reviewer", "work on it", requester=actions.HUMAN)
        db.link_request(con, doing, rid)
        con.commit()
        can = {tid: cp.task_detail(con, tid)["can_discard"] for tid in (mine, theirs, done, doing)}
        check("discard: offered on a waiting task the founder asked for, not one done, doing or another's",
              can == {mine: True, theirs: False, done: False, doing: False}, str(can))
        check("discard: the doing task is the one with a request queued",
              ok and cp.task_detail(con, doing)["state"] == "doing")
        ok, said = actions.discard_task(con, mine, actions.HUMAN, "not needed")
        check("discard: the button's action discards it", ok, said)
        check("discard: and a discarded task is not offered again", not cp.task_detail(con, mine)["can_discard"])
    finally:
        con.close()


async def test_the_settings_tab_switch_shows_and_saves_skip_prompts() -> None:
    from textual.widgets import Switch, Tabs
    _home("skipswitch")
    agents.register("reviewer", skip_permissions=True)
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 40)) as pilot:
        await app.push_screen(ChatPage("reviewer"))
        await pilot.pause(0.3)
        page = app.screen
        page.query_one(Tabs).active = "settings"
        await pilot.pause(0.4)
        sw = page.query_one("#s_skip", Switch)
        check("settings tab: the switch shows the agent's setting", sw.value is True)
        sw.value = False
        check("settings tab: the form reads the switch", page.form()["skip_permissions"] == "off")


def test_the_settings_form_checks_and_saves_only_what_changed() -> None:
    from ocp.ui import chat_page as cp
    home = _home("settings")
    cfg = cp.settings_of("backend")
    check("settings: the agent file is read", cfg["effort"] in agents.VALID_EFFORT
          and cfg["model"] and cfg["weight"] in cp.WEIGHTS, str(cfg))
    form = {k: cfg[k] for k in cp.EDITABLE}
    check("settings: an untouched form has no changes", cp.settings_changes(cfg, form) == [])
    check("settings: a blank repo means leave the one it has",
          cp.settings_changes({**cfg, "repo": "/somewhere"}, {**form, "repo": " "}) == [])
    check("settings: a changed field is reported old to new",
          cp.settings_changes(cfg, {**form, "model": "opus"}) == [("model", cfg["model"], "opus")])
    check("settings: a good form has no problem", cp.settings_problem({**form, "repo": ""}) is None)
    for name, bad in (("empty model", {"model": " "}), ("control character in model", {"model": "a\x1bb"}),
                      ("yaml would not read it as text", {"model": "no"}), ("unknown effort", {"effort": "loud"}),
                      ("unknown weight", {"weight": "huge"}), ("repo that is not a folder", {"repo": "/no/such/dir"})):
        check(f"settings: refused - {name}", cp.settings_problem({**form, "repo": "", **bad}) is not None)
    repo = home / "work"
    repo.mkdir()
    changes = cp.settings_changes(cfg, {**form, "model": "opus", "effort": "high", "repo": str(repo)})
    ok, said = cp.save_settings("backend", changes)
    now = agents.read("backend")
    check("settings: a save writes the changed fields", ok and now["model"] == "opus"
          and now["effort"] == "high" and now["repo"] == str(repo), said)
    check("settings: and leaves the rest of the file as it was",
          now["weight"] == cfg["weight"] and now.get("enabled") is True)
    agents.register("backend", weight="heavy")
    ok, said = cp.save_settings("backend", [("effort", now["effort"], "low")])
    check("settings: a save leaves a field changed since the tab was read",
          ok and agents.read("backend")["weight"] == "heavy", said)
    check("settings: skip prompts reads off for an agent that never set it", cfg["skip_permissions"] == "off")
    check("settings: skip prompts must be on or off",
          cp.settings_problem({**form, "repo": "", "skip_permissions": "yes"}) is not None)
    ok, said = cp.save_settings("backend", cp.settings_changes(cfg, {**form, "skip_permissions": "on"}))
    check("settings: turning skip prompts on writes it and reads back on",
          ok and agents.skip_permissions(agents.read("backend")) and cp.settings_of("backend")["skip_permissions"] == "on", said)
    check("settings: and the rest of the file is as it was", agents.read("backend")["weight"] == "heavy")
    ok, said = cp.save_settings("backend", [("skip_permissions", "on", "off")])
    check("settings: turning it off removes it", ok and not agents.skip_permissions(agents.read("backend")), said)
    with open(agents.agent_file("backend"), "a") as f:
        f.write("skip_permissions: true\n")
    t.reseal(agents.agent_file("backend"))
    unhinted = cp.settings_of("backend")
    check("settings: a key in agent.yaml with no grant shows off, as the launch ignores it",
          agents.skip_permissions(agents.read("backend")) and not agents.skip_permissions_granted("backend")
          and unhinted["skip_permissions"] == "off", str(unhinted["skip_permissions"]))
    ok, said = cp.save_settings("backend", cp.settings_changes(unhinted, {**form, "skip_permissions": "on"}))
    check("settings: saving on then grants it", ok and agents.skip_permissions_granted("backend")
          and cp.settings_of("backend")["skip_permissions"] == "on", said)


def test_a_message_sits_where_the_agent_read_it_and_a_line_ahead_of_the_queue() -> None:
    from ocp.ui import chat_page as cp
    def row(i, ts, delivered, requester=actions.HUMAN):
        return {"id": i, "ts": ts, "resolved_at": None, "requester": requester, "target": "reviewer",
                "state": "queued", "body": f"m{i}", "verdict": None, "dismissed_by": None,
                "dismissed_reason": None, "delivered_at": delivered, "replied": 0}
    msgs = cp.messages_of([row(1, 100, 300), row(2, 200, 250), row(3, 150, None)], "reviewer")
    check("chat: yours are ordered by when the agent read them, unread ones last",
          [m["text"] for m in msgs] == ["m2", "m1", "m3"], str([m["text"] for m in msgs]))
    between = cp.messages_of([row(1, 100, 400, "alpha"), row(2, 200, 300, "beta"), row(3, 150, None, "alpha")],
                             "reviewer")
    check("chat: agents' messages to each other too, by when they were delivered, undelivered last",
          [m["text"] for m in between] == ["m2", "m1", "m3"], str([m["text"] for m in between]))
    kinds = [type(w).__name__ for w in cp.transcript_widgets(msgs, True)]
    check("chat: one line sits ahead of the queued message", kinds == ["ChatMessage", "ChatMessage", "QueueLine", "ChatMessage"],
          str(kinds))
    spare = cp.transcript_widgets(msgs[:2], True)
    check("chat: nothing queued leaves a spare row at the bottom, without a rule",
          type(spare[-1]).__name__ == "QueueLine" and not spare[-1].rule and len(spare) == 3)
    waits = [type(w).__name__ for w in cp.transcript_widgets(msgs, False, wait="waiting for x's reply (#1)")]
    check("chat: what the agent waits on sits just above the queue line",
          waits == ["ChatMessage", "ChatMessage", "Static", "QueueLine", "ChatMessage"], str(waits))


def test_a_delivery_changes_the_chat_signature() -> None:
    from ocp.ui import chat_page as cp
    _home("sigdeliver")
    con = db.connect()
    try:
        rid = db.add_request(con, "alpha", "reviewer", "one", None, None, None, None, 0, None)
        con.commit()
        before = cp.signature(con, "reviewer", True)
        con.execute("UPDATE requests SET delivered_at = 5 WHERE id = ?", (rid,))
        con.commit()
        check("chat: a delivery moves the signature though the state stays queued",
              cp.signature(con, "reviewer", True) != before)
    finally:
        con.close()


def test_a_queued_message_says_why_it_has_not_reached_the_agent() -> None:
    from ocp.team import ask as askmod
    _home("qwhy")
    con = db.connect()
    try:
        first = db.add_request(con, "reviewer", "backend", "one", None, None, None, None, 0, None)
        second = db.add_request(con, actions.HUMAN, "backend", "two", None, None, None, None, 0, None)
        con.commit()
        con.execute("UPDATE agent_state SET state = 'RUNNING', terminal = 'x' WHERE agent = 'backend'")
        con.commit()
        check("chat: the head of the queue is waiting on the agent",
              "mid-turn" in actions.queue_reason(con, "backend", first), actions.queue_reason(con, "backend", first))
        check("chat: the one behind it says what is ahead",
              f"behind #{first}" == actions.queue_reason(con, "backend", second))
        actions.send_now(con, second)
        check("chat: send now marks it for the daemon", [r["id"] for r in db.send_now_pending(con, "backend")] == [second])
        check("chat: a message marked send now no longer says it is behind another",
              "behind" not in actions.queue_reason(con, "backend", second), actions.queue_reason(con, "backend", second))
        check("chat: send now refuses a request that is not the founder's",
              "not sent" in actions.send_now(con, first))
        con.execute("UPDATE requests SET delivered_at = 1 WHERE id = ?", (second,))
        check("chat: and one already delivered", "not sent" in actions.send_now(con, second))
        from ocp.ui import chat_page as cp
        def buttons(delivered):
            m = {"who": "you", "when": 1, "rid": 9, "part": 0, "side": "you", "text": "x", "waiting": False,
                 "replyable": False, "answered": False, "queued": True, "delivered": delivered}
            row = [c for c in cp.ChatMessage(m)._pending_children if type(c).__name__ == "Horizontal"][0]
            return [b.label.plain if hasattr(b.label, "plain") else str(b.label)
                    for b in row._pending_children if type(b).__name__ == "Button"]
        check("chat: send now and cancel are gone once delivered",
              len(buttons(False)) == 2 and buttons(True) == [], f"{buttons(False)} {buttons(True)}")
        m = {"who": "you", "when": 1, "rid": 9, "part": 0, "side": "you", "text": "x", "waiting": False,
             "replyable": False, "answered": False, "queued": True, "delivered": False}
        idle, busy = cp.ChatMessage(m, working=False), cp.ChatMessage(m, working=True)
        check("chat (T133): send now is offered only while the agent is mid-turn",
              idle._send_now.display is False and busy._send_now.display is True,
              (idle._send_now.display, busy._send_now.display))
        idle.set_working(True)
        check("chat (T133): and appears when the agent starts a turn", idle._send_now.display is True)
    finally:
        con.close()


async def test_the_queue_line_says_why_an_agents_message_waits() -> None:
    _home("heldwhy")
    agents.register("reviewer")
    con = db.connect()
    try:
        db.add_request(con, "reviewer", "backend", "for the busy one", None, None, None, None, 0, None)
        con.execute("UPDATE agent_state SET state = 'RUNNING', terminal = 'x' WHERE agent = 'backend'")
        con.commit()
    finally:
        con.close()
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.3)
        await app.push_screen(ChatPage("reviewer"))
        await pilot.pause(0.5)
        app.screen.run_command("all")
        await pilot.pause(0.5)
        line = next(w for w in app.screen.query("QueueLine") if w.rule)
        check("chat: with only an agent's message below the line, the line says why (its recipient's state)",
              "backend" in line.why and "mid-turn" in line.why, repr(line.why))


def test_the_context_setting_is_in_thousands_and_shows_its_share_of_the_window() -> None:
    from ocp.ui import chat_page as cp
    _home("ctxset")
    check("settings: the share of a window is worked out", actions.context_share("200", "claude-opus-5") == "20% of the 1000k window"
          and actions.context_share("900", "claude-opus-5") == "held to 60% of the 1000k window"
          and actions.context_share("50", "gpt-x") == "window unknown for this model")
    cfg = cp.settings_of("backend")
    check("settings: the default reads as thousands", cfg["context_k"] == "200", cfg["context_k"])
    check("settings: 0 is refused", cp.settings_problem({**{k: cfg[k] for k in cp.EDITABLE}, "repo": "", "context_k": "0"}))
    ok, _ = cp.save_settings("backend", [("context_k", "200", "150")])
    check("settings: a save writes it as tokens", ok and agents.read("backend")["context_tokens"] == 150_000)


def test_what_an_agent_wrote_is_drawn_inert_on_every_part_of_the_page() -> None:
    from ocp.ui import chat_page as cp
    from ocp.infra import textsafe
    evil = "x\x9b31m y\x1b]8;;http://evil.example\x07link\x1b]8;;\x07 [red]not markup[/red] \x00\x1b[2J\ttab"
    m = {"who": evil, "when": 1, "rid": 1, "part": 0, "side": "agent", "text": evil, "waiting": False,
         "replyable": True, "answered": True, "queued": False, "delivered": True, "cancelled": True}
    head, body, note = cp.message_parts(m)
    d = {"id": 1, "title": evil, "state": "doing", "owner": evil, "opened_by": evil, "created_at": 1,
         "parent": (9, evil), "done": (evil, 1), "closed": (evil, 1), "asker": evil,
         "children": [(2, evil, "waiting")], "requests": [(3, evil, evil, "queued", evil)]}
    page = cp.task_page(d)
    texts = [head, body, *page["kids"], *page["reqs"], page["title"], page["meta"],
             cp.agent_badge("FATAL", evil)]
    suggestions = (cp.suggest("/task ", [(1, "owns", "waiting", evil, evil, 1)], [])
                   + cp.suggest("#", [(1, "owns", "waiting", evil, evil, 1)], [(2, evil, evil)]))
    leaks = [ln for t in texts for ln in t.plain.split("\n") if textsafe.has_unsafe(ln)]
    check("inert: nothing drawn carries a control character or a link", not leaks, str(leaks[:1]))
    check("inert: text that looks like markup is shown as typed, not interpreted",
          all("[red]not markup[/red]" in t.plain for t in (body, page["title"])), body.plain)
    leaked = [x.label for x in suggestions if textsafe.has_unsafe("".join(x.label))]
    check("inert: the menu labels are safe too", not leaked, str(leaked[:1]))
    check("inert: a cancelled message says why",
          note is not None and "told to stop" in note.plain)


# ---------------------------------------------------------------- ui components

def _plain(s: str) -> str:
    return tui._ANSI.sub("", s)


def test_gauge_elapsed_fraction_bar_and_usage_cells() -> None:
    from types import SimpleNamespace
    from ocp.usage import status
    from ocp.ui.components import gauge
    ef = gauge.elapsed_fraction
    check("gauge: a reset that is unknown or already passed has no fraction",
          ef(None, 3600, now=1000) is None and ef(0, 3600, now=1000) is None
          and ef(900, 3600, now=1000) is None and ef(1000, 3600, now=1000) is None)
    check("gauge: partway through the window is the share of it already gone",
          abs(ef(1000 + 900, 3600, now=1000) - 0.75) < 1e-9
          and ef(1000 + 3600, 3600, now=1000) == 0.0)
    check("gauge: a reset further off than the window never goes below 0",
          ef(1000 + 7200, 3600, now=1000) == 0.0)

    check("gauge: no figure is a row of dots",
          _plain(gauge.bar(None, 6)) == "·" * 6)
    check("gauge: the fill is round(pct * width)", _plain(gauge.bar(50, 10)).count("█") == 5)
    check("gauge: a figure past 100 or below 0 stays inside the bar",
          _plain(gauge.bar(250, 4)) == "████" and _plain(gauge.bar(-5, 4)) == "░░░░")
    for pct, colour, name in ((59, tui.GRN, "green"), (60, tui.YEL, "yellow"),
                              (84, tui.YEL, "yellow"), (85, tui.RED, "red")):
        check(f"gauge: {pct}% is drawn {name}", gauge.bar(pct, 10).startswith(colour), repr(gauge.bar(pct, 10)))
    last = gauge.bar(90, 10, last_seen=True)
    check("gauge: a last-seen figure keeps its fill but has no colour",
          last.startswith(tui.DIM) and not any(c in last for c in (tui.RED, tui.YEL, tui.GRN))
          and _plain(last).count("█") == 9)

    def at(elapsed, width=10, pct=50):
        return _plain(gauge.bar(pct, width, elapsed=elapsed)).index("│")
    check("gauge: the pace mark sits at its share of the width",
          at(0.0) == 0 and at(0.5) == 5 and at(0.95) == 9)
    check("gauge: the mark takes the place of a cell, it does not add one",
          len(_plain(gauge.bar(50, 10, elapsed=0.5))) == 10)
    check("gauge: past the end and on a one-cell bar the mark clamps inside the bar",
          at(1.0) == 9 and at(2.0) == 9 and _plain(gauge.bar(50, 1, elapsed=0.9)) == "│")
    check("gauge: no elapsed, no mark", "│" not in gauge.bar(50, 10))

    now = time.time()
    metered = SimpleNamespace(unmetered=False,
                              short=status.Window(300, reset=int(now + 9000)),
                              long=status.Window(10080, reset=int(now + 86400)),
                              long_trusted=40, long_source="live",
                              shown_short=lambda: (70, status.mark("live")))
    five, seven = gauge.usage_cells(metered, True, now=now)
    check("gauge: a metered account has a 5h and a 7d cell, each with its bar and figure",
          "70%" in _plain(five) and "40%" in _plain(seven) and "│" in _plain(five) and "│" in _plain(seven),
          (_plain(five), _plain(seven)))
    bare_five, _ = gauge.usage_cells(metered, False, now=now)
    check("gauge: without show_pct only the bar is drawn", "%" not in _plain(bare_five))
    check("gauge: an unmetered account has no meter to draw",
          gauge.usage_cells(SimpleNamespace(unmetered=True)) is None)


def test_agent_rows_short_model_cache_and_founder_block() -> None:
    from ocp.ui.components import agent_rows as ar
    check("agent_rows: the model loses its provider prefix",
          ar.kind_short({"vendor": "zai", "model": "zai-coding-plan/glm-5.3-flash", "effort": "high"})
          == "glm-5.3-flash high")
    check("agent_rows: nor does it carry the vendor, and the effort is its short form",
          ar.kind_short({"vendor": "claude", "model": "opus", "effort": "medium"}) == "opus mid"
          and "claude" not in ar.kind_short({"vendor": "claude", "model": "opus", "effort": "medium"}))
    check("agent_rows: a model with no slash is shown as it is",
          ar.kind_short({"model": "sonnet"}).startswith("sonnet "))

    home = _home("agentrows")
    con = db.connect()
    try:
        check("agent_rows: no activity on record is no cache figure", ar.cache_left(con, "nobody", now=10_000) is None)
        db.add_event(con, "nobody", "Stop")
        last = db.last_activity(con, "nobody")
        check("agent_rows: the cache left is what remains of PROMPT_CACHE_S since the last activity",
              ar.cache_left(con, "nobody", now=last + 600) == dmn_states.PROMPT_CACHE_S - 600)
        check("agent_rows: it goes negative once the cache has lapsed",
              ar.cache_left(con, "nobody", now=last + dmn_states.PROMPT_CACHE_S + 5) == -5)

        cell = lambda left: _plain(ar.cache_cell(left))
        check("agent_rows: the cache cell is minutes, then m:ss in the last ten, then a dash",
              cell(3480) == "58m" and cell(600) == "10m" and cell(599) == "9:59"
              and cell(65) == "1:05" and cell(0) == "-" and cell(-30) == "-" and cell(None) == "-")
        check("agent_rows: an agent that ignores cache expiry shows that, not a countdown",
              _plain(ar.cache_cell(3480, True)) == "ignored" and _plain(ar.cache_cell(-30, True)) == "ignored")
        check("agent_rows: the last ten minutes are yellow, the rest plain",
              tui.YEL in ar.cache_cell(599) and tui.YEL not in ar.cache_cell(3480))

        now = 5_000
        db.block_open(con, "backend", "modal", "a dialog", "w", now=now, show_at=now + 100)
        check("agent_rows: a block not yet old enough to be said is not the founder's yet",
              ar.founder_block(con, "backend", now=now + 99) is None)
        check("agent_rows: at show_at it is",
              ar.founder_block(con, "backend", now=now + 100) is not None)
        check("agent_rows: an agent with no block has none", ar.founder_block(con, "nobody", now=now + 100) is None)
    finally:
        con.close()


def test_agent_rows_collect_carries_the_agents_own_model_and_effort() -> None:
    from ocp.ui.components import agent_rows as ar
    _home("collectcfg")
    agents.register("opusy", model="claude-opus-5-5", effort="high")
    con = db.connect()
    try:
        row = tui.AgentRow("opusy", "", "opusy", False, None, "IDLE", "", None)
        extra = ar.collect(con, [row])["opusy"]
        check("agent_rows: collect shows the agent's own model and effort, not the defaults",
              extra[0] == ar.kind_short(agents.read("opusy")) and "opus" in extra[0], str(extra))
        from ocp.session import idle_close
        check("agent_rows: collect flags cache expiry as ignored only for the agent whose switch is on",
              extra.cache_ignored is False)
        idle_close.set_agent_ignore_cache_expiry("opusy", True)
        check("agent_rows: ... and it does once the agent's ignore switch is on",
              ar.collect(con, [row])["opusy"].cache_ignored is True)
    finally:
        con.close()


def test_fold_urgency_ranks_what_a_fold_hides() -> None:
    from ocp.team import ask as askmod
    from ocp.ui.components import agent_rows as ar
    _home("foldurg")
    for n in ("w1", "w2", "w3", "w4"):
        agents.register(n)
    con = db.connect()
    try:
        check("fold_urgency: nothing to say is None", ar.fold_urgency(con, ["w1", "w2"], {}) is None)
        db.set_agent_state(con, "w1", "RUNNING")
        check("fold_urgency: work going on is a green ▶",
              ar.fold_urgency(con, ["w1", "w2"], {}) == ("▶", 1, tui.STATE_COLOR["RUNNING"]))
        db.set_agent_state(con, "w2", "RUNNING")
        check("fold_urgency: it counts the agents that share it",
              ar.fold_urgency(con, ["w1", "w2"], {})[1] == 2)
        check("fold_urgency: a stuck delivery is a yellow !, above work",
              ar.fold_urgency(con, ["w1", "w2"], {"w2": {}}) == ("!", 1, tui.YEL))
        db.set_agent_state(con, "w3", dmn_states.STALLED, detail="dead")
        check("fold_urgency: an error is a red !, above a stuck delivery",
              ar.fold_urgency(con, ["w1", "w2", "w3"], {"w2": {}}) == ("!", 2, tui.RED))
        askmod.ask("human", "a question", requester="w4")
        check("fold_urgency: an open ask to the founder is the ✉, above an error",
              ar.fold_urgency(con, ["w1", "w3", "w4"], {})[0] == "✉")
        check("fold_urgency: a red among yellows of the same rank makes the whole one red",
              ar.fold_urgency(con, ["w2", "w3"], {"w2": {}}) == ("!", 2, tui.RED))
    finally:
        con.close()


def test_agent_rows_collect_says_why_a_row_needs_the_founder() -> None:
    from ocp.team import ask as askmod
    from ocp.ui.components import agent_rows as ar
    _home("collect")
    agents.register("reviewer")
    askmod.ask("human", "please look", requester="reviewer")
    con = db.connect()
    try:
        rows, _notes = tui.agents_data(con)
        extra = ar.collect(con, rows)
        check("collect: one Extra per row with cells, by name", set(extra) == {r.name for r in rows}, extra)
        x = extra["reviewer"]
        check("collect: an open ask to the founder is counted on its row",
              x.asks == 1 and extra["backend"].asks == 0, x)
        check("collect: a row with nothing to say has no block, no stuck reason, no urgency",
              extra["backend"].block == "" and extra["backend"].urgency is None
              , extra["backend"])
    finally:
        con.close()


def test_open_proposals_lists_only_what_is_the_founders_move() -> None:
    from ocp.team import ask as askmod
    from ocp.ui.components import proposals as pr
    home = _home("openprops")
    agents.register("hr")
    con = db.connect()
    try:
        pids = {}
        for name in ("queued", "unclear", "approved", "rejected"):
            ok, said = hr.propose("add", name, requester="hr", why=f"why {name}")
            check(f"proposals: {name} was raised", ok, said)
            pids[name] = int(said.split("#")[1].split(".")[0])
        # `resolve` refuses a non-decision on a proposal now; one closed that way by an
        # older ocp is what is stranded.
        con.execute("UPDATE requests SET state = 'done', verdict = 'sounds good' WHERE id = ?",
                    (pids["unclear"],))
        askmod.resolve(pids["approved"], "approve")
        askmod.resolve(pids["rejected"], "reject not now")
        got = {p["agent"]: p for p in pr.open_proposals(con)}
        check("proposals: a queued escalation is 'decide'",
              got.get("queued", {}).get("status") == pr.DECIDE, got)
        check("proposals: one closed with an unclear reply is 'no decision', and keeps the reply",
              got.get("unclear", {}).get("status") == pr.STRANDED
              and got["unclear"]["reply"] == "sounds good", got)
        check("proposals: an approved or a rejected one is left out",
              "approved" not in got and "rejected" not in got, sorted(got))
        check("proposals: each carries its request's id",
              got["queued"]["rid"] == pids["queued"])
        check("proposals: they are oldest first", [p["asked"] for p in pr.open_proposals(con)]
              == sorted(p["asked"] for p in pr.open_proposals(con)))
        ids = []
        for name in ("tie1", "tie2", "tie3", "tie4"):
            ok, said = hr.propose("add", name, requester="hr", why="w")
            ids.append(int(said.split("#")[1].split(".")[0]))
        con.execute("UPDATE requests SET ts = 1000 WHERE id IN (%s)" % ",".join(map(str, ids)))
        tied = [p["rid"] for p in pr.open_proposals(con) if p["rid"] in ids]
        check("proposals: proposals raised in the same second come oldest first by request id",
              tied == sorted(ids), tied)
        check("proposals: the age is the biggest whole unit",
              timefmt.ago_short(0, now=59) == "0m" and timefmt.ago_short(0, now=3600) == "1h" and timefmt.ago_short(0, now=86400 * 2) == "2d")
    finally:
        con.close()


async def test_a_screen_says_how_it_went_in_a_toast_and_leaves_nothing_at_the_foot() -> None:
    from ocp.ui.pages.home import HomePage
    _home("toasts")
    app = bt.BridgeApp()
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        page = app.screen
        page.toast("done it")
        page.toast("refused it", False)
        page.toast("  ")
        await pilot.pause(0.1)
        notes = list(app._notifications)
        check("toast: a result is a toast, a refusal an error one, blank says nothing",
              [(n.message, n.severity) for n in notes] == [("done it", "information"), ("refused it", "error")],
              str([(n.message, n.severity) for n in notes]))
        check("toast: the foot line holds no message", not hasattr(page, "flash") and isinstance(page, HomePage))


async def test_starting_an_agent_asks_for_a_repo_only_when_it_has_none() -> None:
    from types import SimpleNamespace
    from ocp.ui.components.jobs_aware import dispatch_agent_verb
    home = _home("startrepo")
    agents.register("withrepo", repo=str(home))
    agents.register("norepo")
    asked, started = [], []

    async def push_screen_wait(screen):
        asked.append(screen.prompt)
        return None
    fake = SimpleNamespace(app=SimpleNamespace(push_screen_wait=push_screen_wait),
                           toast=lambda *a: None,
                           _start=lambda verb, target, what, args, *k: started.append(args) or "")
    con = db.connect()
    try:
        await dispatch_agent_verb(fake, con, "withrepo", "terminal")
        check("start: an agent with a repo set is not asked, and starts in it",
              asked == [] and started == [("withrepo", str(home))], str((asked, started)))
        await dispatch_agent_verb(fake, con, "norepo", "terminal")
        check("start: one without a repo is asked plainly for the path",
              asked == ["repo path for norepo:"] and len(started) == 1, str((asked, started)))
    finally:
        con.close()


async def test_the_bridge_follows_the_os_light_dark_setting_while_open() -> None:
    from unittest import mock
    from ocp.ui import theme
    _home("theme")
    which = ["textual-light"]
    with mock.patch.object(theme, "os_theme", lambda: which[0]), mock.patch.object(theme, "POLL_S", 0.1):
        app = bt.BridgeApp()
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.pause(0.2)
            check("theme: the bridge opens in the OS's theme", app.theme == "textual-light", app.theme)
            which[0] = "textual-dark"
            await pilot.pause(0.4)
            check("theme: and switches when the OS changes while it is open", app.theme == "textual-dark", app.theme)
    with mock.patch.object(theme.subprocess, "run", side_effect=OSError):
        check("theme: an unreadable setting is dark", theme.os_theme() == "textual-dark")


class _FakeRules:
    """Stands in for `ocp.team.dialog_rules`, whose store is the founder-only protected dir."""

    def __init__(self) -> None:
        self.rules: list[dict] = []

    def list_rules(self):
        return list(self.rules)

    def add(self, vendor, title, label):
        self.rules.append({"vendor": vendor, "title": title, "label": label})
        return True, "saved"

    def remove(self, vendor, title, label):
        self.rules = [r for r in self.rules if (r["vendor"], r["title"], r["label"]) != (vendor, title, label)]
        return True, "removed"


async def test_a_dialog_on_an_agent_terminal_is_answered_from_its_page() -> None:
    import sys
    from unittest import mock
    from ocp.session import orca as orcamod
    from ocp.team import trust
    from ocp.ui.components.dialog_modal import DialogModal
    home = _home("dialogmodal")
    agents.register("reviewer")
    rules = _FakeRules()
    con = db.connect()
    db.set_terminal(con, "reviewer", "term_d", "wt")
    con.commit()
    con.close()
    dialog = orcamod.Dialog("Do you want to proceed?", ((1, "Yes"), (2, "Yes, and don't ask again"), (3, "No")), "Esc to cancel")
    screen = {"dialog": dialog, "folder": None}
    pressed = []

    def press(handle, number):
        pressed.append((handle, number))
        screen["dialog"] = None
        return orcamod.CLEARED, dialog.title, ""
    with mock.patch.dict(sys.modules, {"ocp.team.dialog_rules": rules}), \
            mock.patch.object(orcamod, "dialog_on_screen", lambda h: screen["dialog"]), \
            mock.patch.object(orcamod, "trust_prompt_folder", lambda h: screen["folder"]), \
            mock.patch.object(orcamod, "confirm_trust", lambda h: (True, "trusted")), \
            mock.patch.object(orcamod, "press_option", press):
        import ocp.team as teampkg
        teampkg.dialog_rules = rules
        con = db.connect()
        try:
            d = actions.dialog_of(con, "reviewer")
            check("dialog: a dialog on the terminal is read as title and numbered options",
                  d and d["kind"] == "dialog" and d["title"] == dialog.title and d["options"][1][1].startswith("Yes, and"), str(d))
            screen["dialog"] = orcamod.Dialog(dialog.title, dialog.options, dialog.footer,
                                              (dialog.title, "Edit /tmp/x.py", "1. Yes", "2. Yes, and don't ask again", "3. No"))
            check("dialog: the panel's other lines ride along, not the title or the options",
                  actions.dialog_of(con, "reviewer")["body"] == ["Edit /tmp/x.py"])
            screen["dialog"] = dialog
            ok, said = actions.answer_dialog(con, "reviewer", "Some other title", 1)
            check("dialog: a changed dialog is not pressed", not ok and not pressed, said)
            screen["dialog"] = dialog
            ok, said = actions.answer_dialog(con, "reviewer", dialog.title, 2, always=True)
            check("dialog: the pick is pressed and 'always' saves a rule on the exact title and label",
                  ok and pressed == [("term_d", 2)] and rules.rules == [
                      {"vendor": "claude", "title": dialog.title, "label": "Yes, and don't ask again"}], str((said, rules.rules)))
            screen["folder"] = str(home)
            with mock.patch.object(orcamod, "screen_text", lambda h: "old\n" + "\u2500" * 20 + "\n Accessing workspace:\n\n /x/y\n\n \u276f No, exit\n   Yes, I trust this folder\n\n Enter to confirm \u00b7 Esc to cancel"):
                check("dialog: the trust screen's own words ride along, without its two choices or key hint",
                      actions.dialog_of(con, "reviewer")["body"] == ["Accessing workspace:", "/x/y"],
                      str(actions.dialog_of(con, "reviewer")["body"]))
            ok, said = actions.answer_dialog(con, "reviewer", f"Trust {home}?", 1, always=True)
            check("dialog: 'always' on the folder-trust screen trusts the folder, not a rule",
                  ok and trust.is_declared(home) and len(rules.rules) == 1, said)
            screen["folder"] = None
            answers = actions.standing_answers()
            check("dialog: the standing answers list the rule", any(a["kind"] == "dialog" for a in answers), str(answers))
            ok, _ = actions.remove_answer("dialog", ("claude", dialog.title, "Yes, and don't ask again"))
            check("dialog: and it can be removed", ok and rules.rules == [])
        finally:
            con.close()

        screen["dialog"] = dialog
        pressed.clear()
        app = bt.BridgeApp()
        async with app.run_test(size=(120, 40)) as pilot:
            page = ChatPage("reviewer")
            await app.push_screen(page)
            await pilot.pause(1.0)
            check("dialog: the agent's page shows the modal", isinstance(app.screen, DialogModal), type(app.screen).__name__)
            await pilot.press("escape")
            await pilot.pause(0.3)
            page._dialog_at = 0.0
            page.poll_dialog()
            await pilot.pause(0.8)
            check("dialog: closed by hand, it is not offered again while it stays", app.screen is page)
            screen["dialog"] = None
            page._dialog_at = 0.0
            page.poll_dialog()
            await pilot.pause(0.5)
            screen["dialog"] = dialog
            page._dialog_at = 0.0
            page.poll_dialog()
            await pilot.pause(0.8)
            check("dialog: gone and back, it is offered again", isinstance(app.screen, DialogModal))
            await pilot.click("#opt_1")
            await pilot.pause(0.8)
            check("dialog: a click on an option presses it", pressed == [("term_d", 1)], str(pressed))
    del teampkg.dialog_rules


async def test_home_settings_lists_the_standing_answers_and_removes_one() -> None:
    import sys
    from unittest import mock
    from textual.widgets import DataTable, Tabs
    _home("answerslist")
    rules = _FakeRules()
    rules.add("claude", "Do you want to proceed?", "Yes")
    import ocp.team as teampkg
    teampkg.dialog_rules = rules
    try:
        with mock.patch.dict(sys.modules, {"ocp.team.dialog_rules": rules}):
            app = bt.BridgeApp()
            async with app.run_test(size=(120, 40)) as pilot:
                await pilot.pause()
                page = app.screen
                page.query_one("#scope", Tabs).active = "settings"
                await pilot.pause(0.4)
                table = page.query_one("#answers", DataTable)
                check("answers: the settings tab lists the saved rule",
                      table.row_count == 1 and "Do you want to proceed?" in str(table.get_row_at(0)[0]),
                      str(table.row_count))
                page._remove_answer()
                await pilot.pause(0.2)
                check("answers: remove deletes it and the list says none",
                      rules.rules == [] and "none" in str(table.get_row_at(0)[0]))
    finally:
        del teampkg.dialog_rules


def test_summary_line() -> None:
    from ocp.ui.components import summary as sm
    text = lambda running, s: sm.summary_text(running, "12:00:00", s).plain
    check("summary: the daemon word comes first, and a quiet team says only that",
          text(True, sm.Summary(0, 0, 0)) == "daemon running · 12:00:00")
    check("summary: a daemon that is down says so", "NOT RUNNING" in text(False, sm.Summary(0, 0, 0)))
    check("summary: working, to decide and to check follow, in that order, each only when not zero",
          text(True, sm.Summary(1, 2, 3)) == "daemon running · 1 working · 2 to decide · 3 to check · 12:00:00"
          and "working" not in text(True, sm.Summary(0, 2, 0)))

    _home("summarize")
    con = db.connect()
    try:
        check("summary: nothing running or waiting counts zero",
              sm.summarize(con) == sm.Summary(0, 0, 0), sm.summarize(con))
        db.set_agent_state(con, "backend", "RUNNING")
        check("summary: a running agent counts", sm.summarize(con).running == 1)
    finally:
        con.close()


def test_key_token_translates_textual_keys() -> None:
    from ocp.ui.keys import key_token
    ev = lambda key, ch=None: events.Key(key, ch)
    check("keys: a named key is its byte token",
          key_token(ev("up")) == "\033[A" and key_token(ev("enter")) == "\r"
          and key_token(ev("escape")) == "\033" and key_token(ev("ctrl+c")) == "\x03")
    check("keys: a printable character is itself", key_token(ev("q", "q")) == "q" and key_token(ev("question_mark", "?")) == "?")
    check("keys: a bare modifier or an unbound function key has nothing to say",
          key_token(ev("shift")) is None and key_token(ev("f5")) is None and key_token(ev("x", "\x00")) is None)


def test_the_agent_row_names_the_fold_alarm_and_why_it_is_stuck() -> None:
    from types import SimpleNamespace
    from ocp.ui.components.agent_rows import Extra
    row = lambda **kw: SimpleNamespace(**{"twig": "", "shown": "boss", "under": 0, "state": "IDLE",
                                          "until": "", "fire": "", **kw})
    extra = lambda **kw: Extra(**{"kind_short": "m", "cache_left": None, "block": "", "stuck": "",
                                  "asks": 0, "urgency": None, **kw})
    page = HomePage.__new__(HomePage)
    name = lambda r, x, on: page._name_cell(r, x, on).plain
    err = ("!", 2, tui.RED)
    check("home: a fold hiding an error wears ⚠ and its count while the blink is on",
          "+3 ⚠2" in name(row(under=3), extra(urgency=err), True))
    check("home: and the icon is blank in the off phase, keeping the row's width",
          name(row(under=3), extra(urgency=err), False) == "boss +3 " + " " * 2
          and len(name(row(under=3), extra(urgency=err), False)) == len(name(row(under=3), extra(urgency=err), True)))
    check("home: a fold hiding only a limit does not blink",
          "⚠2" in name(row(under=3), extra(urgency=("!", 2, tui.YEL)), False))
    check("home: a fold hiding an ask or work shows its glyph and count",
          "✉1" in name(row(under=1), extra(urgency=("✉", 1, tui.YEL)), True)
          and "▶4" in name(row(under=4), extra(urgency=("▶", 4, "g")), True))
    check("home: the blink is only for an error under a fold",
          HomePage._fold_errs(row(under=3), extra(urgency=err))
          and not HomePage._fold_errs(row(under=3), extra(urgency=("!", 1, tui.YEL)))
          and not HomePage._fold_errs(row(under=0), extra(urgency=err))
          and not HomePage._fold_errs(row(under=3), extra()))
    check("home: a row is an alarm on a dialog or a block, not on plain work",
          HomePage._alarm(row(state="DIALOG"), extra()) and HomePage._alarm(row(), extra(block="b"))
          and not HomePage._alarm(row(state="RUNNING"), extra()))
    state = lambda r, x: page._state_cell(r, x, False).plain
    note = lambda r, x, wall="": page._note_cell(r, x, wall).plain
    check("home: the state column is the state word alone, whatever the row is stuck on or until",
          state(row(until="12:30"), extra(stuck="its delivery is not moving")) == state(row(), extra())
          and state(row(), extra(block="dialog open")) == state(row(), extra()))
    check("home: the last column says why it is stuck, and a block in its own words",
          "⚠ its delivery is not moving" in note(row(), extra(stuck="its delivery is not moving"))
          and note(row(), extra(block="dialog open")) == "dialog open" and note(row(), extra()) == "")
    check("home: the last column also holds what the state is until, then the rest, joined by ·",
          note(row(until="12:30"), extra(stuck="stuck")) == "12:30 · ⚠ stuck"
          and note(row(until="12:30"), extra(), "⏳ 14:00") .startswith("12:30 · "))


async def test_home_tab_bar_focus_mouse_and_columns() -> None:
    from unittest import mock
    from textual.widgets import Input, Tabs
    from ocp.usage import load as loadmod
    _home("homekeys")
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.3)
        screen = app.screen
        await pilot.press("up")            # AGENTS' first row -> ACCOUNTS' last: the panels are one list
        await pilot.pause(0.1)
        check("home: up from a panel's first row crosses into the panel above, not the tab bar",
              not isinstance(screen.focused, Tabs) and screen.panel == tui.ACCOUNTS, screen.panel)
        await pilot.press("up")
        await pilot.pause(0.1)
        check("home: up from the very first row does", isinstance(screen.focused, Tabs), screen.focused)
        await pilot.press("right")
        await pilot.pause(0.2)
        check("home: right on the tab bar changes tab", screen.tab == "tasks", screen.tab)
        await pilot.press("left")
        await pilot.pause(0.2)
        check("home: left changes it back", screen.tab == "home", screen.tab)
        await pilot.press("down")
        await pilot.pause(0.1)
        check("home: down goes back into the tab's content", screen.focused is None, screen.focused)
        await pilot.press("right")
        await pilot.press("right")
        await pilot.pause(0.1)
        check("home: with the focus back in the panels the arrows move panels again", screen.panel != tui.ACCOUNTS)

        screen.query_one("#scope", Tabs).active = "settings"
        await pilot.pause(0.2)
        screen.query_one("#default_cap", Input).value = "7"
        await pilot.click("#settings_save")
        await pilot.pause(0.2)
        check("home: the Settings tab saves the default cap", loadmod.default_cap() == 7, loadmod.default_cap())
        for bad in ("0", ""):
            screen.query_one("#default_cap", Input).value = bad
            await pilot.click("#settings_save")
            await pilot.pause(0.2)
            check(f"home: a default cap of {bad!r} is refused and the saved one stays",
                  loadmod.default_cap() == 7 and "at least 1" in _said(screen),
                  _said(screen))
        screen.query_one("#default_cap", Input).value = "9"
        screen.query_one("#scope", Tabs).active = "home"
        await pilot.pause(0.2)
        screen.query_one("#scope", Tabs).active = "settings"
        await pilot.pause(0.2)
        check("home: a cap typed and not saved (T67) is left unsaved by changing tab, and the field shows the saved one",
              loadmod.default_cap() == 7 and screen.query_one("#default_cap", Input).value == "7",
              (loadmod.default_cap(), screen.query_one("#default_cap", Input).value))
        screen.query_one("#scope", Tabs).active = "home"
        await pilot.pause(0.2)
        last = next(p for p in reversed(screen.PANELS) if tui._keys_of(p))
        screen.panel, screen.sel[last] = last, tui._keys_of(last)[-1]
        screen.set_focus(None)
        await pilot.press("down")
        check("home: down on the last row stays there, it does not wrap to the top",
              screen.panel == last and screen.sel[last] == tui._keys_of(last)[-1], (screen.panel, screen.sel))

        table = screen.query_one("#agents", DataTable)
        screen.panel = tui.ACCOUNTS
        screen._redraw()
        await pilot.pause(0.1)
        with mock.patch("ocp.ui.pages.home.marks_on_screen", lambda: True):
            screen._redraw()
            sub = screen.query_one("#accounts-box").border_subtitle
        check("home: with a mark on screen the legend joins the window note on the border",
              "│ time passed in the window" in sub and "inferred" in sub, sub)
        with mock.patch("ocp.ui.pages.home.marks_on_screen", lambda: False):
            screen._redraw()
            check("home: and it is gone again without one",
                  "inferred" not in screen.query_one("#accounts-box").border_subtitle)

        # a mouse press picks the row under the pointer
        agents.register("second")
        screen._redraw()
        await pilot.pause(0.1)
        agents_table = screen.query_one("#agents", DataTable)
        keys = [k.value for k in agents_table.rows]
        check("home: two agents are drawn", len(keys) >= 2, keys)
        want = keys[-1]
        await pilot.mouse_down(agents_table, offset=(3, agents_table.row_count - 1 + (1 if agents_table.show_header else 0)))
        await pilot.pause(0.1)
        check("home: a mouse press picks the row under the pointer",
              screen.panel == tui.AGENTS and screen.sel.get(tui.AGENTS) == want, (screen.panel, screen.sel))

    widths = {}
    for w in (200, 70):
        app = bt.BridgeApp()
        async with app.run_test(size=(w, 30)) as pilot:
            await pilot.pause(0.3)
            widths[w] = len(app.screen.query_one("#accounts", DataTable).columns)
    check("home: the account columns drop as the terminal narrows", widths[200] > widths[70], widths)


async def test_hr_tab_approve_reject_withdraw_and_the_red_dot() -> None:
    from textual.widgets import Button, Tabs
    from ocp.team import ask as askmod
    from ocp.ui.components.proposals import ProposalsView
    _home("hrtab")
    agents.register("hr")
    ok, said = hr.propose("add", "newbie", requester="hr", why="more hands")
    check("hr tab: a proposal was raised", ok, said)
    rid = int(said.split("#")[1].split(".")[0])
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 34)) as pilot:
        await pilot.pause(0.3)
        screen = app.screen
        tabs = screen.query_one("#scope", Tabs)
        check("hr tab: the tab wears a red dot while a proposal waits",
              "●" in tabs.get_tab("hr").label.plain, tabs.get_tab("hr").label)
        tabs.active = "hr"
        await pilot.pause(0.3)
        view = screen.query_one("#hr_view", ProposalsView)
        check("hr tab: the proposal is listed and selected",
              [p["rid"] for p in view.rows] == [rid] and view.current["agent"] == "newbie", view.rows)
        check("hr tab: a decidable one offers approve and reject",
              not screen.query_one("#hr_approve", Button).disabled
              and not screen.query_one("#hr_reject", Button).disabled)

        view.action_reject()
        await pilot.pause(0.2)
        check("hr tab: reject asks for a reason", isinstance(app.screen, TextPrompt), type(app.screen).__name__)
        await pilot.press("space", "enter")
        await pilot.pause(0.3)
        con = db.connect()
        try:
            check("hr tab: a blank reason rejects nothing",
                  db.get_request(con, rid)["state"] == "queued", dict(db.get_request(con, rid)))
        finally:
            con.close()

        view.action_approve()
        await pilot.pause(0.2)
        check("hr tab: approve goes through a Confirm", isinstance(app.screen, Confirm), type(app.screen).__name__)
        await pilot.press("n")
        await pilot.pause(0.2)
        con = db.connect()
        try:
            check("hr tab: answering no to it approves nothing", db.get_request(con, rid)["state"] == "queued")
        finally:
            con.close()
        view.action_approve()
        await pilot.pause(0.2)
        await pilot.press("y")
        await pilot.pause(0.5)
        con = db.connect()
        try:
            req = db.get_request(con, rid)
            check("hr tab: yes answers the escalation `approve`, through answer_ok",
                  req["state"] == "done" and hr.decision(req["verdict"]) == "approved", dict(req))
        finally:
            con.close()
        check("hr tab: the answered proposal leaves the list, and nothing is left to decide",
              view.rows == [] and screen.query_one("#hr_approve", Button).disabled)
        screen._redraw()
        check("hr tab: the dot goes with it", "●" not in tabs.get_tab("hr").label.plain)

    ok, said = hr.propose("add", "second", requester="hr", why="w")
    rid2 = int(said.split("#")[1].split(".")[0])
    ok, said = hr.propose("add", "third", requester="hr", why="w")
    rid3 = int(said.split("#")[1].split(".")[0])
    con = db.connect()
    try:
        con.execute("UPDATE requests SET state = 'done', verdict = 'sounds good' WHERE id = ?", (rid3,))
    finally:
        con.close()
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 34)) as pilot:
        await pilot.pause(0.3)
        screen = app.screen
        screen.query_one("#scope", Tabs).active = "hr"
        await pilot.pause(0.3)
        view = screen.query_one("#hr_view", ProposalsView)
        check("hr tab: a decide row and a no-decision row are both listed",
              [p["status"] for p in view.rows] == ["decide", "no decision"], [p["status"] for p in view.rows])
        view.query_one("#hr_table", DataTable).move_cursor(row=1)
        await pilot.pause(0.2)
        check("hr tab: a no-decision proposal only offers withdraw",
              screen.query_one("#hr_approve", Button).disabled and screen.query_one("#hr_reject", Button).disabled
              and not screen.query_one("#hr_withdraw", Button).disabled)
        view.action_approve()
        view.action_reject()
        await pilot.pause(0.2)
        check("hr tab: approve and reject do nothing on it", not isinstance(app.screen, (Confirm, TextPrompt)),
              type(app.screen).__name__)
        view.query_one("#hr_table", DataTable).move_cursor(row=0)
        await pilot.pause(0.2)
        view.action_reject()
        await pilot.pause(0.2)
        for ch in "too many":
            await pilot.press("space" if ch == " " else ch)
        await pilot.press("enter")
        await pilot.pause(0.5)
        con = db.connect()
        try:
            req = db.get_request(con, rid2)
            check("hr tab: reject with a reason answers `reject <why>`",
                  req["state"] == "done" and req["verdict"] == "reject too many", dict(req))
        finally:
            con.close()

async def test_the_account_page_leaves_a_page_on_top_alone_and_closes_when_it_is_back() -> None:
    from unittest import mock
    from ocp.team import accounts as accountsmod
    from ocp.ui.components.read_only import ReadOnly
    _home("acctgone")
    with mock.patch.object(accountsmod, "identity", lambda acct: None):
        app = bt.BridgeApp()
        async with app.run_test(size=(120, 34)) as pilot:
            await pilot.pause(0.3)
            home = app.screen
            app.push_screen(AccountPage("alpha"))
            await pilot.pause(0.4)
            page = app.screen
            app.push_screen(ReadOnly("on top", [("", ["x"])]))
            await pilot.pause(0.2)
            top = app.screen
            with mock.patch("ocp.ui.pages.account.paths.list_accounts", lambda: []):
                page._load()        # its timer keeps running under the page on top
                await pilot.pause(0.2)
                check("account page: the account going while another page is on top does not pop that page",
                      app.screen is top, type(app.screen).__name__)
                app.pop_screen()
                await pilot.pause(0.4)
            check("account page: back on top it sees the account is gone and closes itself",
                  app.screen is home, type(app.screen).__name__)


async def test_the_home_accounts_row_shows_the_alias() -> None:
    from ocp.usage import load as loadmod
    _home("alias_row")
    loadmod.set_label("alpha", "Work")
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.3)
        first = app.screen.query_one("#accounts", DataTable).get_row_at(0)[0]
        check("home: the ACCOUNTS row shows the alias, not the account's name",
              first.plain.strip() == "Work", first.plain)


def test_account_settings_alias_and_default_cap() -> None:
    from ocp.usage import load as loadmod
    from ocp.session import triggers
    _home("settings")
    ok, _ = actions.save_account_settings("alpha", "x" * 41, "3")
    check("settings: an alias over 40 characters is refused", not ok and loadmod.label_of("alpha") == "alpha")
    ok, _ = actions.save_account_settings("alpha", "a\x1bb", "3")
    check("settings: an alias with a control character is refused", not ok and loadmod.label_of("alpha") == "alpha")

    actions.save_account_settings("alpha", "Work", "3")
    ok, _ = actions.save_account_settings("alpha", "Other", "0")
    check("settings: a bad cap leaves the new alias unwritten", not ok and loadmod.label_of("alpha") == "Work",
          loadmod.label_of("alpha"))
    ok, _ = actions.save_account_settings("alpha", "  ", "3")
    check("settings: a blank alias goes back to the account's own name",
          ok and loadmod.label_of("alpha") == "alpha" and "label_alpha" not in loadmod._config())

    cfg = loadmod._config()
    for bad in (None, 0, -1, True, "3"):
        cfg["active_cap"] = bad
        check(f"default cap: {bad!r} falls back to the built-in", loadmod.default_cap(cfg) == triggers.DEFAULT_ACTIVE_CAP)
    loadmod.set_default_cap(6)
    check("default cap: an account with no cap of its own follows it", loadmod.cap_of("beta") == 6)
    check("own cap: none for an account that follows the default", loadmod.own_cap("beta") is None)
    ok, _ = actions.save_account_settings("beta", "", "")
    check("settings: a blank cap writes no own cap", ok and loadmod.own_cap("beta") is None)
    actions.save_account_settings("beta", "", "4")
    check("settings: a typed cap is written as its own", loadmod.own_cap("beta") == 4 and loadmod.cap_of("beta") == 4)
    check("settings: usage recovery is on until switched off", loadmod.usage_recover_enabled("beta"))
    ok, said = actions.save_account_settings("beta", "", "", False)
    check("settings: the switch turns usage recovery off", ok and not loadmod.usage_recover_enabled("beta")
          and "recovery off" in said, said)
    actions.save_account_settings("beta", "", "")
    check("settings: a save that does not carry the switch leaves it", not loadmod.usage_recover_enabled("beta"))
    actions.save_account_settings("beta", "", "", True)
    check("settings: and back on", loadmod.usage_recover_enabled("beta"))


async def _run_all() -> None:
    test_only_y_n_or_escape_answers_a_confirm()
    test_the_menu_offers_what_the_text_could_become()
    test_the_task_pages_write_a_task_as_T_and_a_request_as_a_bare_number()
    test_the_typed_history_keeps_the_newest_and_never_repeats()
    test_the_chat_is_paged_and_hides_what_never_reached_the_agent()
    test_a_reply_closes_a_waiting_question_or_starts_a_new_request()
    test_a_dismissed_request_leaves_no_dismissed_line_in_the_chat()
    test_the_tasks_of_an_agent_are_what_it_owns_or_opened()
    test_a_delivery_changes_the_chat_signature()
    test_batch_results_are_one_toast_for_what_worked_and_one_for_the_refusals()
    await test_the_agent_tasks_tab_shows_the_pane_when_wide()
    await test_the_agent_tasks_tab_is_the_tasks_view_scoped_to_the_agent()
    await test_the_settings_tab_switch_shows_and_saves_skip_prompts()
    test_the_task_page_offers_discard_only_on_the_founders_waiting_task()
    test_the_settings_form_checks_and_saves_only_what_changed()
    test_what_an_agent_wrote_is_drawn_inert_on_every_part_of_the_page()
    await test_a_confirm_on_screen_ignores_an_arrow_and_answers_n()
    await test_dashboard_shows_a_registered_agent_and_moves_the_cursor()
    await test_help_key_names_every_agent_verb_the_footer_folds_out()
    await test_an_action_runs_off_the_key_loop_and_the_screen_says_so()
    await test_confirm_rechecks_can_if_the_state_changed_while_it_was_open()
    await test_text_prompt_composer_sends_and_cancels()
    await test_accounts_panel_cap_add_remove_and_open()
    await test_the_account_page_shows_the_account_and_sets_its_cap()
    await test_accounts_remove_only_fires_on_the_typed_name()
    await test_messages_panel_open_reaches_the_agents_page()
    await test_enter_on_an_agent_opens_its_chat_page_and_esc_comes_back()
    await test_a_double_click_on_a_row_is_enter()
    await test_wake_on_a_stalled_agent_asks_first()
    await test_messages_panel_acts_on_a_folded_row()
    await test_text_prompt_keeps_a_paste_instead_of_dropping_it()
    test_key_guide_keeps_the_high_ranks_on_one_line_and_ends_with_a_question_mark()
    test_key_guide_never_keeps_a_lower_rank_while_a_higher_one_is_left_out()
    await test_a_numeric_ime_input_refuses_a_committed_syllable_and_takes_a_digit()
    test_the_weekly_outlook_averages_accounts_and_restores_at_reset()
    test_the_overlay_draws_what_the_agent_ate_in_front_of_the_headroom_and_shares_add_up()
    test_the_agents_table_is_a_tree_with_the_group_total_on_top()
    test_the_outlook_chart_draws_a_column_per_slot_with_the_guess_apart()
    await test_stats_screen_draws_the_weekly_graph()
    await test_the_key_guide_stays_on_screen_when_the_table_is_long()
    await test_tasks_screen_marks_batches_and_shows_each_refusal()
    await test_marks_hidden_by_a_state_change_are_dropped_so_an_action_cannot_reach_them()
    await test_merge_refuses_instead_of_crashing_when_the_cursor_row_is_gone()
    await test_copy_reaches_the_system_clipboard()
    await test_a_drag_selection_is_copied_when_the_mouse_is_let_go()
    await test_b_opens_the_tasks_tab_on_the_founders_done_tasks()
    await test_dashboard_accounts_table_is_fed_by_accounts_data()
    await test_dashboard_agents_table_is_fed_by_agents_data()
    await test_dashboard_messages_table_is_fed_by_messages_data()
    await test_stuck_data_with_no_agent_to_act_on_leaves_the_home_page_safe()
    await test_a_key_never_acts_on_a_row_nobody_pointed_at()
    await test_text_prompt_line_keeps_a_multiline_paste_and_shows_escapes_inert()
    await test_read_only_page_shows_foreign_text_inert()
    await test_the_account_page_leaves_a_page_on_top_alone_and_closes_when_it_is_back()
    test_gauge_elapsed_fraction_bar_and_usage_cells()
    test_agent_rows_short_model_cache_and_founder_block()
    test_agent_rows_collect_carries_the_agents_own_model_and_effort()
    test_fold_urgency_ranks_what_a_fold_hides()
    test_agent_rows_collect_says_why_a_row_needs_the_founder()
    test_open_proposals_lists_only_what_is_the_founders_move()
    test_summary_line()
    await test_home_settings_lists_the_standing_answers_and_removes_one()
    await test_a_dialog_on_an_agent_terminal_is_answered_from_its_page()
    await test_the_bridge_follows_the_os_light_dark_setting_while_open()
    await test_starting_an_agent_asks_for_a_repo_only_when_it_has_none()
    await test_a_screen_says_how_it_went_in_a_toast_and_leaves_nothing_at_the_foot()
    test_key_token_translates_textual_keys()
    test_the_agent_row_names_the_fold_alarm_and_why_it_is_stuck()
    await test_home_tab_bar_focus_mouse_and_columns()
    await test_hr_tab_approve_reject_withdraw_and_the_red_dot()
    await test_the_home_accounts_row_shows_the_alias()
    test_a_message_sits_where_the_agent_read_it_and_a_line_ahead_of_the_queue()
    test_the_context_setting_is_in_thousands_and_shows_its_share_of_the_window()
    test_a_queued_message_says_why_it_has_not_reached_the_agent()
    test_an_agents_queued_message_waits_below_the_line_without_buttons()
    await test_escape_does_not_cancel_a_queued_message()
    await test_a_plain_input_keeps_the_syllable_an_ime_commits_with_enter()
    await test_the_queue_line_says_why_an_agents_message_waits()
    await test_a_reply_to_a_selected_message_carries_its_request()
    test_account_settings_alias_and_default_cap()


def main() -> int:
    os.environ.pop("OCP_AGENT", None)
    asyncio.run(_run_all())
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
