"""Tests for ocp/bridge_textual.py (task #45) - the port's first screen.

Needs `textual`, which the interpreter running `tests/test_ocp.py` may not
have (it is only in $OCP_HOME/venv - see requirements.txt and
ocp/venv.py's own docstring on why the suite cannot assume it everywhere).
`tests/test_ocp.py` runs this file under the first python that can import
textual (its own, else the venv's) and prints a SKIPPED line when none can, so
a missing dependency is said out loud instead of the screen going unexercised.

Run: python3 tests/test_bridge_textual.py
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from textual import events  # noqa: E402
from ocp import (actions, agents, bridge_textual as bt, daemon as daemonmod,  # noqa: E402
                 db, jobs as jobsmod, tui)
from ocp.chat_page import ChatPage  # noqa: E402
import test_ocp as t  # noqa: E402 - its `_daemon_home`/`DESIGN_TEAM` fixture, not re-derived here

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not cond else ""))


def _home(tag: str) -> Path:
    """`t._daemon_home`, minus the daemon-trigger fixture (a percentage, a
    Stop event) this screen has no use for: registers `backend` on `alpha`
    with a terminal, and a real team.yaml so `actions.chat_reply`/`ask` have
    somewhere to route through."""
    home = Path(tempfile.mkdtemp(prefix=f"ocp_bt_{tag}_"))
    t._daemon_home(home, 20)
    return home


def _screen_text(screen) -> str:
    return "\n".join(str(w.render()) for w in screen.query(bt.Static))


async def test_dashboard_shows_a_registered_agent_and_moves_the_cursor() -> None:
    _home("dash")
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        screen = app.screen
        row = screen.query_one("#agents", bt.DataTable).get_row("backend")
        check("dashboard: the registered agent is on the AGENTS panel",
              "backend" in str(row[0]) and "alpha" in str(row[1]), row)
        await pilot.press("w")
        await pilot.pause(0.2)
        check("dashboard: a refused verb says why, as `actions.can` does",
              "not parked or closed" in screen.flash, screen.flash)


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
        check("help: `?` shows the full AGENT_KEYS legend, not nothing",
              screen.flash == t.tui.agent_help_line(), screen.flash)
        check("help: the legend names a key the footer can fold away (move)",
              "v move" in screen.flash, screen.flash)


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
                  isinstance(app.screen, bt.Confirm), type(app.screen).__name__)
            await pilot.press("y")
            await pilot.pause(0.2)
            check("act: confirming starts the job and returns to the dashboard at once",
                  isinstance(app.screen, bt.Dashboard) and screen.jobs.running, "")
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
                  "closed backend (stub)" in screen.flash and not screen.jobs.running, screen.flash)
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
              isinstance(app.screen, bt.Confirm), type(app.screen).__name__)
        con = db.connect()
        try:
            db.set_agent_state(con, "backend", daemonmod.HANDOFF)
        finally:
            con.close()
        await pilot.press("y")
        await pilot.pause(0.2)
        check("recheck: a yes rechecks actions.can rather than trusting the first read",
              "HANDOFF" in screen.flash and "changed while you looked" in screen.flash,
              screen.flash)
        check("recheck: and does not start the job", not screen.jobs.running, "")


async def test_text_prompt_composer_sends_and_cancels() -> None:
    async def read(multi: bool, keys: list[str]) -> str | None:
        app_ = _HostApp(bt.TextPrompt("to backend:", multi=multi))
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
    cancelled = await read(False, list("hi") + ["escape"])
    check("prompt: escape cancels and sends nothing", cancelled is None, repr(cancelled))


async def test_accounts_panel_cap_add_remove_and_open() -> None:
    """ACCOUNTS' non-interactive actions (task #45's own third piece): `c`
    sets the cap through `loadmod` directly, `a`/`x` refuse through the same
    `accounts.add_account`/`remove_account` the CLI uses before either ever
    needs a real login shell, and `enter` opens a read-only summary
    (`actions.account_detail`)."""
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
              "at most 3 agents mid-turn" in screen.flash, screen.flash)

        await pilot.press("a")
        await pilot.pause(0.1)
        for ch in "alpha":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause(0.2)
        check("accounts: adding one that exists refuses through accounts.add_account, "
              "never reaching a real login", screen.flash == "account 'alpha' already exists",
              screen.flash)

        await pilot.press("x")
        await pilot.pause(0.2)
        check("accounts: removing one an agent is on refuses through remove_plan, "
              "before the human is ever asked to type its name",
              "in use by" in screen.flash and not isinstance(app.screen, bt.TextPrompt),
              screen.flash)

        await pilot.press("enter")
        await pilot.pause(0.2)
        check("accounts: enter opens a read-only page (account_detail)",
              isinstance(app.screen, bt.ReadOnly), type(app.screen).__name__)
        text = app.screen.title_text + "\n".join(r for _, rows in app.screen.sections for r in rows)
        check("accounts: it is that account's own detail",
              "account alpha" in text and "config" in text, text)
        await pilot.press("escape")
        await pilot.pause(0.1)
        check("accounts: escape closes it", isinstance(app.screen, bt.Dashboard),
              type(app.screen).__name__)


async def test_accounts_remove_only_fires_on_the_typed_name() -> None:
    """auditor #2016 SHOULD-FIX 2: the only remove check in the file uses an
    in-use account, refused by remove_plan before confirm() is ever
    consulted, so the typed-name check itself was never exercised. `beta` is
    free (registered with `no_login`, so nothing here needs a real login
    shell) - a wrong name must leave it, the exact name must remove it."""
    _home("removefree")
    from ocp import accounts as accountsmod
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
              isinstance(app.screen, bt.TextPrompt), type(app.screen).__name__)
        for ch in "betx":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause(0.2)
        check("accounts: a wrong typed name leaves it",
              "beta" in accountsmod.paths.list_accounts(),
              str(accountsmod.paths.list_accounts()))
        check("accounts: and says nothing was removed", "not removed" in screen.flash, screen.flash)

        await pilot.press("x")
        await pilot.pause(0.1)
        for ch in "beta":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause(0.2)
        check("accounts: the exact typed name removes it",
              "beta" not in accountsmod.paths.list_accounts(),
              str(accountsmod.paths.list_accounts()))
        check("accounts: and says so", "removed account 'beta'" in screen.flash, screen.flash)


async def test_messages_panel_open_reaches_the_agents_page() -> None:
    """A MESSAGES row's `enter` opens the same conversation page an AGENTS
    row does when the message belongs to one - `actions.message_place`,
    reused rather than a separate detail view for the common case."""
    home = _home("msgopen")
    agents.register("reviewer", "alpha")
    con = db.connect()
    try:
        db.set_terminal(con, "reviewer", "term_r", "wt")
    finally:
        con.close()
    from ocp import ask as askmod
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


async def test_wake_on_a_stalled_agent_asks_first() -> None:
    """auditor #2008 BLOCKING 2a: waking a STALLED agent needs a yes first in
    curses (`_needs_confirm`, founder #1620/#1631) - checking only `label in
    tui.CONFIRM` misses it, since `wake` is deliberately left off that set for
    every other state."""
    home = _home("wake")
    agents.register("backend", "alpha")
    con = db.connect()
    try:
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", daemonmod.STALLED)
    finally:
        con.close()
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        screen = app.screen
        await pilot.press("w")
        await pilot.pause(0.2)
        check("wake: a STALLED agent asks before clearing the stall",
              isinstance(app.screen, bt.Confirm), type(app.screen).__name__)
        await pilot.press("y")
        deadline = asyncio.get_event_loop().time() + 5
        while asyncio.get_event_loop().time() < deadline and screen.jobs.running:
            await pilot.pause(0.1)
        check("wake: confirming it clears the stall",
              "STALLED -> QUEUED" in screen.flash, screen.flash)


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
    agents.register("reviewer", "alpha")
    con = db.connect()
    try:
        db.set_terminal(con, "reviewer", "term_r", "wt")
    finally:
        con.close()
    from ocp import ask as askmod
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
    agents.register("reviewer", "alpha")
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
              "dismiss" in screen.flash.lower() or "#" in screen.flash, screen.flash)


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
            self.result = await self.push_screen_wait(bt.TextPrompt("to backend:", multi=True))

    app = _HostApp()
    async with app.run_test(size=(80, 20)) as pilot:
        await pilot.pause()
        app.screen.post_message(events.Paste("line one\nline two"))
        await pilot.pause(0.2)
        await pilot.press("enter")
        await pilot.pause(0.2)
    check("prompt: a multi-line paste is sent whole, not dropped",
          app.result == "line one\nline two", repr(app.result))


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
            self.result = await self.push_screen_wait(bt.TextPrompt("p:", multi=self.multi))

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
    agents.register("reviewer", "alpha")
    from ocp import ask as askmod
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


async def test_dashboard_stuck_table_is_fed_by_stuck_data_and_entering_it_is_safe() -> None:
    """The STUCK panel is a DataTable fed by `tui.stuck_data`. Its rows about
    the daemon or an account have no agent to act on (agent None, and more than
    one row can share that), so they are drawn without a row key and entering
    the panel - where the selection can be nothing - must not fail."""
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
        table = app.screen.query_one("#stuck", bt.DataTable)
        for _ in range(6):
            if app.screen.panel == tui.STUCK or not app.is_running:
                break
            await pilot.press("tab")
        await pilot.pause(0.3)
        check("dashboard: the stuck table has a row per stuck thing and entering it is safe",
              app.is_running and table.row_count == len(rows), (app.is_running, table.row_count))


async def test_a_key_never_acts_on_a_row_nobody_pointed_at() -> None:
    """A selection can be stale - the row it named left the list - and a key
    pressed then must do nothing, not act on whatever is under it now."""
    _home("stalekey")
    agents.register("reviewer", "alpha")
    from ocp import ask as askmod
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


async def test_task_checks_lists_and_acts_on_the_founders_own() -> None:
    """Task #72 (founder #2154/#2155): the bridge's own 'done - waiting for
    check' list (#71), reached with `b` from the dashboard - only the tasks
    the founder is the asker of (`actions.task_asker`, the bridge always
    acting as `actions.HUMAN`), never a task someone else opened and asks
    for themselves. `c` closes the row under the cursor, `r` reopens it with
    a typed one-line reason, and space + `C` close several at once."""
    _home("checks")
    con = db.connect()
    try:
        t_mine = db.new_task(con, "manager's own", "worker", "manager")
        t_founder1 = db.new_task(con, "founder's first", "worker", "manager")
        t_founder2 = db.new_task(con, "founder's second", "worker", "manager")
        t_open = db.new_task(con, "not done yet", "worker", "manager")
        r1 = db.add_request(con, actions.HUMAN, "manager", "please", None, None, None, None, 0, None)
        db.link_request(con, t_founder1, r1)
        r2 = db.add_request(con, actions.HUMAN, "manager", "please too", None, None, None, None, 0, None)
        db.link_request(con, t_founder2, r2)
        db.mark_task_done(con, t_mine, "shipped it")
        db.mark_task_done(con, t_founder1, "shipped that too")
        db.mark_task_done(con, t_founder2, "and this")
    finally:
        con.close()

    app = bt.BridgeApp()
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        await pilot.press("b")
        await pilot.pause()
        screen = app.screen
        check("checks: b opens the screen from the dashboard",
              isinstance(screen, bt.TaskChecks), type(screen).__name__)
        listed = [t["id"] for t in screen.rows]
        check("checks: the founder's own done tasks are both listed",
              t_founder1 in listed and t_founder2 in listed, listed)
        check("checks: not the manager's own, and not the still-open one",
              t_mine not in listed and t_open not in listed, listed)

        await pilot.press("c")
        await pilot.pause()
        con = db.connect()
        try:
            row = db.get_task(con, t_founder1)
        finally:
            con.close()
        check("checks: c closes the row under the cursor",
              row["closed_by"] == actions.HUMAN, dict(row))
        check("checks: a closed row drops off the list",
              t_founder1 not in [t["id"] for t in screen.rows], screen.rows)

        await pilot.press("r")
        await pilot.pause()
        for ch in "not really done":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause()
        con = db.connect()
        try:
            row = db.get_task(con, t_founder2)
        finally:
            con.close()
        check("checks: r reopens the row under the cursor",
              row["done_evidence"] is None and row["closed_by"] is None, dict(row))
        con = db.connect()
        try:
            payload = con.execute(
                "SELECT payload FROM events WHERE kind = 'reopen_task' "
                "ORDER BY id DESC LIMIT 1").fetchone()
        finally:
            con.close()
        check("checks: the typed reason is recorded, not silently dropped",
              payload and json.loads(payload["payload"])["reason"] == "not really done", payload)


async def test_task_checks_closes_several_checked_at_once() -> None:
    """Founder #2154's own worry: on a day with more of these than are worth
    doing one at a time, space marks several rows and `C` closes them all in
    one key, not one at a time."""
    _home("checks2")
    con = db.connect()
    try:
        tids = []
        for i in range(3):
            tid = db.new_task(con, f"founder's #{i}", "worker", "manager")
            rid = db.add_request(con, actions.HUMAN, "manager", "please", None, None, None,
                                 None, 0, None)
            db.link_request(con, tid, rid)
            db.mark_task_done(con, tid, f"shipped {i}")
            tids.append(tid)
    finally:
        con.close()

    app = bt.BridgeApp()
    async with app.run_test(size=(100, 24)) as pilot:
        await pilot.pause()
        await pilot.press("b")
        await pilot.pause()
        screen = app.screen

        await pilot.press("space")
        await pilot.press("down")
        await pilot.press("space")
        await pilot.press("down")
        await pilot.press("space")
        await pilot.press("space")             # space again: unchecks the same row
        await pilot.pause()
        check("checks: space toggles - two stay checked, the third toggled back off",
              len(screen.checked) == 2, screen.checked)

        await pilot.press("C")
        await pilot.pause()
        con = db.connect()
        try:
            rows = {tid: db.get_task(con, tid) for tid in tids}
        finally:
            con.close()
        check("checks: both checked rows closed, the third left alone",
              rows[tids[0]]["closed_by"] == actions.HUMAN
              and rows[tids[1]]["closed_by"] == actions.HUMAN
              and rows[tids[2]]["closed_by"] is None,
              {k: v["closed_by"] for k, v in rows.items()})
        check("checks: checked is cleared once acted on", not screen.checked, screen.checked)


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
        app.push_screen(bt.ReadOnly("t", secs))
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
        check(f"confirm: {key} leaves it open", bt.confirm_answer(key, ch) is None)
    check("confirm: y confirms", bt.confirm_answer("y", "y") is True)
    check("confirm: Y confirms", bt.confirm_answer("Y", "Y") is True)
    check("confirm: n declines", bt.confirm_answer("n", "n") is False)
    check("confirm: escape declines", bt.confirm_answer("escape", "\x1b") is False)
    check("confirm: ctrl+c declines", bt.confirm_answer("ctrl+c", "\x03") is False)


async def test_a_confirm_on_screen_ignores_an_arrow_and_answers_n() -> None:
    """auditor #2281: the wiring, not only the rule - a wheel notch leaves the
    pushed Confirm up and a later n answers it."""
    _home("confirm")
    answers: list = []
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        app.push_screen(bt.Confirm("close it"), answers.append)
        await pilot.pause()
        await pilot.press("down", "up", "pagedown")
        await pilot.pause()
        check("confirm: arrows leave the pushed Confirm up",
              isinstance(app.screen, bt.Confirm) and answers == [], (type(app.screen).__name__, answers))
        await pilot.press("n")
        await pilot.pause()
        check("confirm: n then answers it False", answers == [False], answers)


def test_the_menu_offers_what_the_text_could_become() -> None:
    from ocp import chat_page as cp
    tasks = [(84, "owns", "waiting", "keeper fix cache", "orcapipe", 300),
             (85, "owns", "doing", "clerk discard", "orcapipe", 200),
             (7, "opened", "done", "auditor pass", "orcapipe", 100)]
    reqs = [(2305, "human -> orcapipe", "first line"), (2289, "human -> orcapipe", "other")]

    def names(text):
        return [x.label[0] for x in cp.suggest(text, tasks, reqs)]
    check("menu: a slash lists every page command",
          names("/") == ["/chat", "/all", "/task", "/sort", "/settings", "/back"], str(names("/")))
    check("menu: a slash narrows by the letters typed", names("/ta") == ["/task"], str(names("/ta")))
    check("menu: /task lists the tasks, newest change first", names("/task ") == ["#84", "#85", "#7"])
    check("menu: /task narrows by a word of the title", names("/task audit") == ["#7"])
    check("menu: /task narrows by the digits of the number", names("/task 8") == ["#84", "#85"])
    check("menu: /sort names the columns", names("/sort ti") == ["title"], str(names("/sort ti")))
    check("menu: a hash lists tasks then requests", names("see #") == ["#84", "#85", "#7", "#2305", "#2289"])
    check("menu: a hash narrows by the digits after it", names("see #23") == ["#2305"])
    check("menu: a hash not after a space, or with letters, offers nothing",
          names("a#2") == [] and names("a #zz") == [])
    check("menu: plain text and unknown commands offer nothing", names("hello") == [] and names("/nope") == [])
    check("menu: text after a newline is never a command", names("hi\n/task") == [])
    pick = cp.suggest("/task 8", tasks, reqs)[0]
    check("menu: Tab on a task fills the command, Enter opens it",
          pick.tab == "/task 84" and pick.action == "open" and pick.arg == "84")
    ref = cp.suggest("see #", tasks, reqs)[0]
    check("menu: a reference inserts only the number", ref.action == "insert" and ref.arg == "84")


def test_the_typed_history_keeps_the_newest_and_never_repeats() -> None:
    from ocp import chat_page as cp, paths
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
    from ocp import ask as askmod, chat_page as cp
    _home("chat")
    agents.register("reviewer", "alpha")
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


def test_a_reply_closes_a_waiting_question_or_starts_a_new_request() -> None:
    from ocp import ask as askmod, chat_page as cp
    _home("reply")
    agents.register("reviewer", "alpha")
    con = db.connect()
    try:
        ok, _, rid = askmod.ask("human", "merge it?", requester="reviewer")
        check("reply: the agent's question is waiting for the human",
              ok and cp.messages_of([db.get_request(con, rid)], "reviewer")[0]["waiting"])
        ok1, _ = cp.send_reply(con, "reviewer", rid, "yes")
        after = db.get_request(con, rid)
        check("reply: a reply to a waiting question is its verdict",
              ok1 and after["state"] == "done" and after["verdict"] == "yes", str(dict(after)))
        check("reply: the question then reads as answered", cp.messages_of([after], "reviewer")[0]["answered"])
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
        check("reply: a blank reply is refused", cp.send_reply(con, "reviewer", rid, "  ")[0] is False)
        check("reply: a reply to a request that is gone is refused",
              cp.send_reply(con, "reviewer", 99999, "x")[0] is False)
    finally:
        con.close()


def test_the_tasks_table_lists_owned_and_opened_and_sorts() -> None:
    from ocp import chat_page as cp
    _home("tasks")
    agents.register("reviewer", "alpha")
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
        check("tasks: sorted by title", [r[3] for r in cp.sort_tasks(rows, "title", False)]
              == sorted(r[3] for r in rows))
        check("tasks: reversed is the reverse",
              [r[0] for r in cp.sort_tasks(rows, "task", True)] == sorted((r[0] for r in rows), reverse=True))
        check("tasks: state sorts waiting before done",
              cp.sort_tasks([(1, "owns", "done", "x", "y", 1), (2, "owns", "waiting", "x", "y", 1)],
                            "state", False)[0][0] == 2)
    finally:
        con.close()


def test_the_task_page_offers_discard_only_on_the_founders_waiting_task() -> None:
    from ocp import ask as askmod, chat_page as cp
    _home("discardable")
    agents.register("reviewer", "alpha")
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


def test_the_settings_form_checks_and_saves_only_what_changed() -> None:
    from ocp import chat_page as cp
    home = _home("settings")
    cfg = cp.settings_of("backend")
    check("settings: the agent file is read", cfg["account"] and cfg["effort"] in agents.VALID_EFFORT
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
          now["account"] == cfg["account"] and now["weight"] == cfg["weight"] and now.get("enabled") is True)
    agents.register("backend", "beta")
    ok, said = cp.save_settings("backend", [("effort", now["effort"], "low")])
    check("settings: a save leaves an account moved since the tab was read",
          ok and agents.read("backend")["account"] == "beta", said)


def test_what_an_agent_wrote_is_drawn_inert_on_every_part_of_the_page() -> None:
    from ocp import chat_page as cp, textsafe
    evil = "x\x9b31m y\x1b]8;;http://evil.example\x07link\x1b]8;;\x07 [red]not markup[/red] \x00\x1b[2J\ttab"
    m = {"who": evil, "when": 1, "rid": 1, "part": 0, "side": "agent", "text": evil, "waiting": False,
         "replyable": True, "answered": True, "queued": False, "delivered": True, "cancelled": True}
    head, body, note = cp.message_parts(m)
    d = {"id": 1, "title": evil, "state": "doing", "owner": evil, "opened_by": evil, "created_at": 1,
         "parent": (9, evil), "done": (evil, 1), "closed": (evil, 1), "asker": evil,
         "children": [(2, evil, "waiting")], "requests": [(3, evil, evil, "queued", evil)]}
    page = cp.task_page(d)
    texts = [head, body, *page["kids"], *page["reqs"], page["title"], page["meta"],
             *[c for c in cp.task_cells((1, "owns", "waiting", evil, evil, 1)) if hasattr(c, "plain")],
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


async def _run_all() -> None:
    test_only_y_n_or_escape_answers_a_confirm()
    test_the_menu_offers_what_the_text_could_become()
    test_the_typed_history_keeps_the_newest_and_never_repeats()
    test_the_chat_is_paged_and_hides_what_never_reached_the_agent()
    test_a_reply_closes_a_waiting_question_or_starts_a_new_request()
    test_the_tasks_table_lists_owned_and_opened_and_sorts()
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
    await test_accounts_remove_only_fires_on_the_typed_name()
    await test_messages_panel_open_reaches_the_agents_page()
    await test_enter_on_an_agent_opens_its_chat_page_and_esc_comes_back()
    await test_wake_on_a_stalled_agent_asks_first()
    await test_messages_panel_acts_on_a_folded_row()
    await test_text_prompt_keeps_a_paste_instead_of_dropping_it()
    await test_task_checks_lists_and_acts_on_the_founders_own()
    await test_task_checks_closes_several_checked_at_once()
    await test_dashboard_accounts_table_is_fed_by_accounts_data()
    await test_dashboard_agents_table_is_fed_by_agents_data()
    await test_dashboard_messages_table_is_fed_by_messages_data()
    await test_dashboard_stuck_table_is_fed_by_stuck_data_and_entering_it_is_safe()
    await test_a_key_never_acts_on_a_row_nobody_pointed_at()
    await test_text_prompt_line_keeps_a_multiline_paste_and_shows_escapes_inert()
    await test_read_only_page_shows_foreign_text_inert()


def main() -> int:
    os.environ.pop("OCP_AGENT", None)
    try:
        asyncio.run(_run_all())
    finally:
        # Leave no stray OCP_HOME for whatever imports this module next.
        home = os.environ.pop("OCP_HOME", None)
        if home:
            shutil.rmtree(home, ignore_errors=True)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
