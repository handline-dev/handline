"""Tests for ocp/bridge_textual.py (task #45) - the port's first screen.

Needs `textual`, which the interpreter running `tests/test_ocp.py` may not
have (it is only in $OCP_HOME/venv - see requirements.txt and
ocp/venv.py's own docstring on why the suite cannot assume it everywhere).
`test_ocp.py`'s own `test_bridge_textual_dashboard` shells this file out to a
python3 that does have it, the same way `test_yaml_differential` does for
PyYAML - so a missing dependency fails loud instead of the screen going
unexercised on whichever interpreter happens to run the suite.

Run: python3 tests/test_bridge_textual.py
"""
from __future__ import annotations

import asyncio
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
                 db, jobs as jobsmod)
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


async def test_dashboard_shows_a_registered_agent_and_moves_the_cursor() -> None:
    _home("dash")
    app = bt.BridgeApp()
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause()
        screen = app.screen
        text = str(screen.query_one("#canvas").render())
        check("dashboard: the registered agent is on the AGENTS panel",
              "backend" in text and "alpha" in text, text)
        check("dashboard: its row carries the cursor once entered", "▸" in text, text)
        await pilot.press("w")
        await pilot.pause(0.2)
        check("dashboard: a refused verb says why, as `actions.can` does",
              "not parked or closed" in screen.flash, screen.flash)


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
            mid_text = str(screen.query_one("#canvas").render())
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


async def test_agent_page_box_sends_a_message_and_runs_slash_commands() -> None:
    """chat-box (task #16/#1390), taken over: typing lands in the box, Enter
    sends it, '/' opens the same commands `agent_commands` lists elsewhere,
    and escape goes back keeping the draft - through `ocp.actrun`, same as
    every other verb here (bridge-working).

    Targets `reviewer`, not `backend`: DESIGN_TEAM (`test_ocp._daemon_home`)
    gives `*` an edge to `reviewer` alone, so a human message to `backend`
    would be denied for a reason that has nothing to do with this screen."""
    _home("box")
    agents.register("reviewer", "alpha")
    con = db.connect()
    try:
        db.set_terminal(con, "reviewer", "term_r", "wt")
    finally:
        con.close()

    class _HostApp(bt.App):
        def __init__(self):
            super().__init__()
            self.jobs = jobsmod.Jobs()

        def on_mount(self):
            self.push_screen(bt.AgentDetail("reviewer"))

    app = _HostApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        screen = app.screen
        check("page: opens on the agent it is given",
              isinstance(screen, bt.AgentDetail) and screen.agent == "reviewer",
              type(screen).__name__)

        for ch in "hello there":
            await pilot.press(ch)
        text = str(screen.query_one("#canvas").render())
        check("page: what is typed lands in the box", "hello there" in text, text)

        await pilot.press("enter")
        deadline = asyncio.get_event_loop().time() + 8
        while asyncio.get_event_loop().time() < deadline and "hello there" in screen.box.text:
            await pilot.pause(0.1)
        check("page: the box is cleared once the message is handed off",
              screen.box.text == "", repr(screen.box.text))
        while asyncio.get_event_loop().time() < deadline and not screen.flash:
            await pilot.pause(0.1)
        check("page: sending it runs through the real chat_reply, not a stub",
              "queued as #" in screen.flash, screen.flash)

        await pilot.press("slash")
        await pilot.pause(0.1)
        text = str(screen.query_one("#canvas").render())
        check("page: '/' alone lists every command agent_commands does",
              "knows" in text and "terminal" in text and "cancel" in text, text)
        for ch in "knows":
            await pilot.press(ch)
        await pilot.press("escape")
        await pilot.pause(0.1)
        check("page: escape backs out of the page, keeping the draft",
              not isinstance(app.screen, bt.AgentDetail) and screen.box.text == "/knows",
              f"{type(app.screen).__name__} draft={screen.box.text!r}")


async def test_agent_page_box_keeps_a_draft_a_busy_target_refuses() -> None:
    """auditor #2013 BLOCKING (new): `_deliver` used to clear the box whatever
    `_start` returned. A busy target is refused before anything is started -
    what was typed must stay, not vanish with nothing sent."""
    _home("boxbusy")
    agents.register("reviewer", "alpha")
    con = db.connect()
    try:
        db.set_terminal(con, "reviewer", "term_r", "wt")
    finally:
        con.close()
    bt._DRAFTS.pop("reviewer", None)   # a global keyed by name; other tests use "reviewer" too

    class _HostApp(bt.App):
        def __init__(self):
            super().__init__()
            self.jobs = jobsmod.Jobs()

        def on_mount(self):
            self.push_screen(bt.AgentDetail("reviewer"))

    app = _HostApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        screen = app.screen
        # Occupies the target with a real (harmless, long-sleeping) process,
        # not a stand-in `proc=None` - `reap()`'s own timer tick polls it on
        # every redraw, stand-in or not.
        stub = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
        busy = jobsmod.Job("chat_reply", "reviewer", "messaging reviewer", stub, time.time())
        screen.jobs.running.append(busy)
        try:
            for ch in "second draft":
                await pilot.press(ch)
            await pilot.press("enter")
            await pilot.pause(0.2)
            check("page: a refused send keeps what was typed",
                  screen.box.text == "second draft", repr(screen.box.text))
            check("page: and says why", "one thing at a time on it" in screen.flash, screen.flash)
        finally:
            screen.jobs.running.remove(busy)
            stub.kill()
            stub.wait()


async def test_agent_page_box_restores_a_draft_the_send_failed() -> None:
    """auditor #2013 BLOCKING (new), the other half: a send that starts but
    fails - a team routing refusal, here - must put the draft back rather
    than leave the box empty with nothing having gone. Real `chat_reply`
    through `ocp.actrun`, not a stub: `backend` has no edge from the human in
    DESIGN_TEAM, so the refusal is genuine, not manufactured."""
    _home("boxfail")
    con = db.connect()
    try:
        db.set_terminal(con, "backend", "term_x", "wt")
    finally:
        con.close()
    bt._DRAFTS.pop("backend", None)

    class _HostApp(bt.App):
        def __init__(self):
            super().__init__()
            self.jobs = jobsmod.Jobs()

        def on_mount(self):
            self.push_screen(bt.AgentDetail("backend"))

    app = _HostApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        screen = app.screen
        for ch in "not routed":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause(0.1)
        check("page: the box clears the moment the send is on its way",
              screen.box.text == "", repr(screen.box.text))
        deadline = asyncio.get_event_loop().time() + 8
        while asyncio.get_event_loop().time() < deadline and not screen.flash:
            await pilot.pause(0.1)
        check("page: the real routing refusal comes back",
              "failed" in screen.flash or "denied" in screen.flash, screen.flash)
        check("page: and the draft is restored, not lost",
              screen.box.text == "not routed", repr(screen.box.text))


async def test_agent_page_box_keeps_a_refused_draft_ahead_of_the_next_one() -> None:
    """auditor #2050 SHOULD-FIX 2: `restore_if_refused` only put the draft
    back "if not self.box.text" - a human who sends, then starts typing the
    next thing before the refusal comes back, had the failed send discarded
    for good, with nothing in the flash to retype it from. It must go back
    ahead of whatever is now in the box, not only into an empty one."""
    _home("boxfail2")
    con = db.connect()
    try:
        db.set_terminal(con, "backend", "term_x", "wt")
    finally:
        con.close()
    bt._DRAFTS.pop("backend", None)

    class _HostApp(bt.App):
        def __init__(self):
            super().__init__()
            self.jobs = jobsmod.Jobs()

        def on_mount(self):
            self.push_screen(bt.AgentDetail("backend"))

    app = _HostApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        screen = app.screen
        for ch in "not routed":
            await pilot.press(ch)
        await pilot.press("enter")
        await pilot.pause(0.1)
        check("page2: the box clears the moment the send is on its way",
              screen.box.text == "", repr(screen.box.text))
        for ch in "next thing":       # started before the refusal comes back
            await pilot.press(ch)
        deadline = asyncio.get_event_loop().time() + 8
        while asyncio.get_event_loop().time() < deadline and not screen.flash:
            await pilot.pause(0.1)
        check("page2: the real routing refusal comes back",
              "failed" in screen.flash or "denied" in screen.flash, screen.flash)
        check("page2: the failed draft is put back ahead of what was typed since, not dropped",
              screen.box.text == "not routed\nnext thing", repr(screen.box.text))


async def test_agent_page_box_keeps_a_paste_instead_of_dropping_it() -> None:
    """auditor #2050 SHOULD-FIX 1: `AgentDetail.on_paste` had no test of its
    own - renaming it away still passed 47/0, since the only paste check was
    `TextPrompt`'s, from phase 1."""
    _home("boxpaste")
    agents.register("reviewer", "alpha")
    con = db.connect()
    try:
        db.set_terminal(con, "reviewer", "term_r", "wt")
    finally:
        con.close()
    bt._DRAFTS.pop("reviewer", None)

    class _HostApp(bt.App):
        def __init__(self):
            super().__init__()
            self.jobs = jobsmod.Jobs()

        def on_mount(self):
            self.push_screen(bt.AgentDetail("reviewer"))

    app = _HostApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        screen = app.screen
        for ch in "draft ":
            await pilot.press(ch)
        screen.post_message(events.Paste("pasted one\npasted two"))
        await pilot.pause(0.2)
        check("page: a paste into the agent's own box lands whole, not dropped",
              screen.box.text == "draft pasted one\npasted two", repr(screen.box.text))


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
        text = str(app.screen.query_one("#readonly").render())
        check("accounts: it is that account's own detail",
              "account alpha" in text and "config" in text, text)
        await pilot.press("q")
        await pilot.pause(0.1)
        check("accounts: any key closes it", isinstance(app.screen, bt.Dashboard),
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
        check("messages: enter opens the sender's own page",
              isinstance(app.screen, bt.AgentDetail) and app.screen.agent == "reviewer",
              type(app.screen).__name__)
        text = str(app.screen.query_one("#canvas").render())
        check("messages: the message itself is on it", "please review this" in text, text)


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
        check("messages: it opens the sender's own page",
              isinstance(opener.screen, bt.AgentDetail) and opener.screen.agent == "reviewer",
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


async def _run_all() -> None:
    await test_dashboard_shows_a_registered_agent_and_moves_the_cursor()
    await test_an_action_runs_off_the_key_loop_and_the_screen_says_so()
    await test_confirm_rechecks_can_if_the_state_changed_while_it_was_open()
    await test_text_prompt_composer_sends_and_cancels()
    await test_agent_page_box_sends_a_message_and_runs_slash_commands()
    await test_agent_page_box_keeps_a_draft_a_busy_target_refuses()
    await test_agent_page_box_restores_a_draft_the_send_failed()
    await test_agent_page_box_keeps_a_refused_draft_ahead_of_the_next_one()
    await test_agent_page_box_keeps_a_paste_instead_of_dropping_it()
    await test_accounts_panel_cap_add_remove_and_open()
    await test_accounts_remove_only_fires_on_the_typed_name()
    await test_messages_panel_open_reaches_the_agents_page()
    await test_wake_on_a_stalled_agent_asks_first()
    await test_messages_panel_acts_on_a_folded_row()
    await test_text_prompt_keeps_a_paste_instead_of_dropping_it()


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
