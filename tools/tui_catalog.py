"""Walk the Textual IDE on a scratch home with seeded data and save one screenshot (SVG) per screen, tab,
modal and state, with a line on what each is, into docs/tui-catalog/.

Run with the venv python (it has textual):  tools/tui_catalog.py [only-name-prefix]
Never touches the live home: tests/support.py puts everything in a temp directory."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

os.environ.pop("OCP_AGENT", None)    # the scratch home's founder, as the tests run
import support as t  # noqa: E402 - sets the scratch home before anything reads a path
from handline import actions, db  # noqa: E402
from handline.team import agents  # noqa: E402
from handline.ui import api  # noqa: E402

OUT = ROOT / "docs" / "tui-catalog"
SIZE = (150, 42)
ONLY = next((a for a in sys.argv[1:] if not a.startswith("-")), "")
SHOW = "--show" in sys.argv
SHOTS: list[tuple[str, str, str]] = []      # (file stem, group, what it shows)


def serve_in_process():
    """A connection to the daemon's own serving code on a thread of this process, in place of its socket."""
    import socket
    import threading
    from handline.daemon import socket_server
    from handline.infra import sessiontoken
    sessiontoken.ensure_founder_key()
    a, b = socket.socketpair()
    threading.Thread(target=socket_server._serve_one, args=(b, lambda m: None), daemon=True).start()
    return a


api.connect = serve_in_process


def seed() -> None:
    home = Path(tempfile.mkdtemp(prefix="tui_catalog_", dir=t.TEST_ROOT))
    t._daemon_home(home, 62)
    for name in ("frontend", "product", "reviewer"):
        agents.register(name)
    con = db.connect()
    try:
        for name in ("frontend", "product", "reviewer"):
            db.set_account(con, name, "alpha")
            db.add_event(con, name, "Stop", {})
        mk = lambda title, owner, by="human", **kw: db.new_task(con, title, owner, by, **kw)
        draft = mk("Sketch the settings page", "frontend")
        queued = mk("Write the release notes", "product")
        doing = mk("Fix the login redirect", "backend")
        blocked = mk("Ship the settings page", "frontend")
        done = mk("Rename the export column", "backend")
        con.commit()
        for tid in (queued, doing, blocked):
            actions.set_queued(con, tid, actions.HUMAN, True)
        rid = db.add_request(con, actions.HUMAN, "backend", "Fix the login redirect: it drops the query string.", None,
                             "peer", "async", "task", 0, None)
        db.link_request(con, doing, rid)
        con.execute("INSERT INTO task_blocks(task_id, blocks_on, created_at) VALUES (?, ?, ?)", (blocked, doing, int(time.time())))
        con.commit()
        actions.task_start(con, "backend", doing)
        actions.memo_add(con, doing, "backend", "Reproduced it: the redirect drops the query string.")
        actions.memo_add(con, doing, "backend", "Fix is in the router; adding a test.")
        actions.mark_task_done(con, done, "backend", reason="renamed in the export and the docs")
        con.commit()
        print({n: actions.task_state(con, db.get_task(con, i)) for n, i in
               (("draft", draft), ("queued", queued), ("doing", doing), ("blocked", blocked), ("done", done))})
        for who, body, kind in (("reviewer", "The diff for T%d is ready for you: two files, no migrations." % doing, None),
                                ("product", "Which of the two onboarding flows should we ship first?", "question"),
                                ("frontend", "Pushed the settings page; it needs your eye on the empty state.", None)):
            db.add_request(con, who, actions.HUMAN, body, None, "peer", "async", kind, 0, None)
        db.set_agent_state(con, "backend", "RUNNING")
        db.set_agent_state(con, "reviewer", "FATAL", detail="the launch failed: no such directory")
        con.commit()
    finally:
        con.close()
    # The scratch home has no live sessions, so every verb would be refused: offer them all, as a live session does.
    actions.can = lambda con, agent, label: (True, "")
    from handline.ui.pages import home as home_page
    home_page.daemon_running = lambda: True


def plain_text(svg: str) -> str:
    """The screen's text out of its SVG, a line per row, to check by reading what a shot caught."""
    import html
    import re
    rows: dict[float, list[tuple[float, str]]] = {}
    for m in re.finditer(r'<text[^>]*x="([\d.]+)" y="([\d.]+)"[^>]*>(.*?)</text>', svg):
        rows.setdefault(float(m.group(2)), []).append((float(m.group(1)), html.unescape(re.sub(r"<[^>]*>", "", m.group(3)))))
    return "\n".join("".join(w for _, w in sorted(r)).replace("\xa0", " ").rstrip() for _, r in sorted(rows.items()))


async def shot(pilot, stem: str, group: str, what: str) -> None:
    """Let the screen settle, then keep it as `stem`.svg."""
    if ONLY and not stem.startswith(ONLY):
        return
    await pilot.pause(0.6)
    OUT.joinpath("img").mkdir(parents=True, exist_ok=True)
    OUT.joinpath("img", f"{stem}.svg").write_text(pilot.app.export_screenshot(title=stem))
    SHOTS.append((stem, group, what))
    print("shot", stem)
    if SHOW:
        print(plain_text(pilot.app.export_screenshot(title=stem)))


async def press(pilot, *keys: str, wait: float = 0.3) -> None:
    for k in keys:
        await pilot.press(k)
        await pilot.pause(wait)


async def walk() -> None:
    from handline.ui.bridge_textual import BridgeApp
    from textual.widgets import Tabs
    app = BridgeApp()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause(1.5)
        await shot(pilot, "home-agents", "Home", "The home page opens on AGENTS: one row per agent with account, model, state, cache, context, inbox, task.")
        await press(pilot, "question_mark")
        await shot(pilot, "home-agents-keys", "Home", "The key list of the AGENTS panel (?).")
        await press(pilot, "escape")
        await press(pilot, "left")
        await shot(pilot, "home-accounts", "Home", "ACCOUNTS panel: each account's 5h and 7d usage, resets, agents on it, runway.")
        await press(pilot, "question_mark")
        await shot(pilot, "home-accounts-keys", "Home", "The key list of the ACCOUNTS panel (?).")
        await press(pilot, "escape")
        await press(pilot, "right", "right")
        await shot(pilot, "home-messages", "Home", "FOR YOU panel: what agents have sent the founder, oldest unanswered first.")
        await press(pilot, "question_mark")
        await shot(pilot, "home-messages-keys", "Home", "The key list of the FOR YOU panel (?).")
        await press(pilot, "escape")
        for tab in ("stats", "hr", "settings"):
            app.screen.query_one("#scope", Tabs).active = tab
            await pilot.pause(1.2)
            await shot(pilot, f"tab-{tab}", "Home tabs", f"The {tab} tab of the home page.")
        await home_again(pilot)
        await tasks_tab(pilot)
        await home_again(pilot)
        await home_actions(pilot)
        await account_page(pilot)
        await chat_pages(pilot)
        await daemon_down(pilot)


async def home_again(pilot) -> None:
    """Back on the Home tab with the focus in its rows, not on the tab bar."""
    from textual.widgets import Tabs
    pilot.app.screen.query_one("#scope", Tabs).active = "home"
    await pilot.pause(0.5)
    await press(pilot, "down", wait=0.4)


async def tasks_tab(pilot) -> None:
    await press(pilot, "T", wait=0.8)
    await shot(pilot, "tasks-list", "Tasks tab", "Every open task as a list: priority, state, title, owner, idle time, timeout, asker, blocked-by; the side pane shows the task under the cursor.")
    await press(pilot, "t", wait=0.8)
    await shot(pilot, "tasks-tree", "Tasks tab", "The same tasks as a tree of what waits on what (t switches).")
    await press(pilot, "t", wait=0.5)
    await press(pilot, "a", wait=0.8)
    await shot(pilot, "tasks-closed", "Tasks tab", "Done and discarded tasks shown too (a toggles).")
    await press(pilot, "a", wait=0.5)
    await press(pilot, "v", wait=0.5)
    await shot(pilot, "tasks-states", "Tasks tab", "Pick which states to show, with checkboxes (v).")
    await press(pilot, "escape", wait=0.4)
    await press(pilot, "f", wait=0.8)
    await shot(pilot, "tasks-agent-filter", "Tasks tab", "One agent's tasks; each f press moves to the next agent.")
    await press(pilot, "f", "f", "f", "f", "f", wait=0.3)
    await press(pilot, "s", wait=0.6)
    await shot(pilot, "tasks-sorted", "Tasks tab", "Sorted by the next column (s; S reverses; a click on a column head sorts by it).")
    await press(pilot, "space", "down", "space", wait=0.4)
    await shot(pilot, "tasks-marked", "Tasks tab", "Rows marked with space; d, o, p, m act on all marked rows, and the bar below says so.")
    await press(pilot, "o", wait=0.6)
    await shot(pilot, "tasks-owner", "Tasks tab", "Change the owner of the marked tasks (o).")
    await press(pilot, "escape", wait=0.4)
    await press(pilot, "p", wait=0.6)
    await shot(pilot, "tasks-priority", "Tasks tab", "Set the priority of the marked tasks (p).")
    await press(pilot, "escape", wait=0.4)
    await press(pilot, "d", wait=0.6)
    await shot(pilot, "tasks-discard", "Tasks tab", "Discard the marked tasks, with a reason (d).")
    await press(pilot, "escape", wait=0.4)
    await press(pilot, "m", wait=0.6)
    await shot(pilot, "tasks-merge", "Tasks tab", "Fold the marked tasks into the row under the cursor (m).")
    await press(pilot, "escape", wait=0.4)
    await press(pilot, "enter", wait=1.0)
    await shot(pilot, "task-detail", "Tasks tab", "One task opened (enter): its state, memos, request and what it waits on.")
    await press(pilot, "escape", wait=0.5)
    await press(pilot, "question_mark", wait=0.5)
    await shot(pilot, "tasks-keys", "Tasks tab", "The Tasks tab key list (?).")
    await press(pilot, "escape", wait=0.4)


async def home_actions(pilot) -> None:
    """The row verbs of each home panel, up to the question they ask (no answer is given, except m in FOR YOU, which marks that message read in the scratch home)."""
    from textual.widgets import DataTable
    app = pilot.app
    await press(pilot, "left", wait=0.4)        # AGENTS, on backend (RUNNING)
    for key, stem, what in (
            ("m", "agents-message", "m: type a message to the agent under the cursor."),
            ("c", "agents-close", "c: close the agent's session (asks first)."),
            ("r", "agents-restart", "r: restart the agent's session (asks first)."),
            ("p", "agents-park", "p: park the agent (asks first)."),
            ("f", "agents-fold", "f: fold the agent's events into its digest."),
            ("v", "agents-move", "v: move the agent to another account (asks which).")):
        await press(pilot, key, wait=0.6)
        await shot(pilot, stem, "Home: agent verbs", what)
        await press(pilot, "escape", wait=0.4)
    await press(pilot, "left", wait=0.4)
    for key, stem, what in (
            ("a", "accounts-add", "a: add an account by name."),
            ("c", "accounts-cap", "c: cap how many agents may run on the account."),
            ("x", "accounts-remove", "x: remove the account (asks first).")):
        await press(pilot, key, wait=0.6)
        await shot(pilot, stem, "Home: account verbs", what)
        await press(pilot, "escape", wait=0.4)
    await press(pilot, "right", "right", wait=0.4)
    for key, stem, what in (
            ("a", "messages-answer", "a: answer the message under the cursor."),
            ("x", "messages-dismiss", "x: dismiss the message."),
            ("m", "messages-mark-read", "m: mark a message read away from FOR YOU (what waits on an answer stays).")):
        await press(pilot, key, wait=0.6)
        await shot(pilot, stem, "Home: message verbs", what)
        await press(pilot, "escape", wait=0.4)


async def to_panel(pilot, index: int) -> None:
    """The home page's panel `index` (0 ACCOUNTS, 1 AGENTS, 2 FOR YOU); ←→ wrap round."""
    for _ in range(3):
        if pilot.app.screen.panel == index:
            return
        await press(pilot, "right", wait=0.3)


async def tab_of(pilot, tab: str) -> None:
    from textual.widgets import Tabs
    pilot.app.screen.query_one("#scope", Tabs).active = tab
    await pilot.pause(0.8)


async def account_page(pilot) -> None:
    await to_panel(pilot, 0)
    await press(pilot, "enter", wait=1.2)
    await shot(pilot, "account-overview", "Account page", "One account: usage windows, month cycle, how it counts on the home row, status.")
    await tab_of(pilot, "agents")
    await shot(pilot, "account-agents", "Account page", "The agents running on the account, with their state.")
    await tab_of(pilot, "settings")
    await shot(pilot, "account-settings", "Account page", "The account's settings: alias, cap, usage recovery, month end; Login and Remove buttons.")
    await press(pilot, "escape", wait=0.6)


async def chat_pages(pilot) -> None:
    await to_panel(pilot, 1)
    await press(pilot, "enter", wait=1.5)
    await shot(pilot, "chat-backend", "Chat page", "An agent's chat: the conversation, its work line, the compose box.")
    await tab_of(pilot, "all")
    await shot(pilot, "chat-all", "Chat page", "All chat: everything said to and by the agent, including its peers' messages.")
    await tab_of(pilot, "tasks")
    await shot(pilot, "chat-tasks", "Chat page", "The agent's tasks, in the same board as the Tasks tab.")
    await tab_of(pilot, "settings")
    await shot(pilot, "chat-settings", "Chat page", "The agent's settings: model, effort, account, switches.")
    await tab_of(pilot, "mine")
    await press(pilot, "slash", wait=0.6)
    await shot(pilot, "chat-slash", "Chat page", "Typing / in the compose box lists the agent's slash commands.")
    await press(pilot, "escape", wait=0.3)


async def daemon_down(pilot) -> None:
    """The home page when the daemon is not running (its banner and what the rows say then)."""
    from handline.ui.pages import home as home_page
    pilot.app.pop_screen()
    await pilot.pause(0.8)
    home_page.daemon_running = lambda: False
    await pilot.pause(2.5)
    await shot(pilot, "home-daemon-down", "States", "The home page with the daemon stopped.")
    home_page.daemon_running = lambda: True


def main() -> None:
    seed()
    asyncio.run(walk())
    index()


def index() -> None:
    lines = ["# TUI catalog", "", "One screenshot per screen, tab, modal and state of the Textual IDE.", ""]
    group = None
    for stem, g, what in SHOTS:
        if g != group:
            lines += ["", f"## {g}", ""]
            group = g
        lines += [f"### {stem}", "", what, "", f"![{stem}](img/{stem}.svg)", ""]
    lines += ["", "## Not captured", "",
              "- Shots are SVG (no PNG converter on the build machine); open them in a browser.",
              "- Modals are shown up to the question; no answer is sent, so no result screen (confirm done, error toast) is shown.",
              "- Stats, HR and Settings tabs show the scratch home's thin data: no weekly readings, no per-agent usage rows.",
              "- Opened outside the TUI, so not shot: t (terminal), e (editor), the account Login flow.",
              "- Empty states other than daemon-down (no agents, no tasks, no messages) are not shot; error and long-text states are not shot either.",
              "- Modals: Question, Permission, Dialog (answering an agent's blocked prompt in chat), Handoff, ScheduleAdd, SiteEdit, AnswerAdd, Output (shell card), and the f5 restart and agent-settings Confirms (skip permissions, no sandbox).",
              "- Task detail verbs a/x/u/r/d (assign, done, reopen, discard), memo and brief edit; Tasks tab A and S.",
              "- HR proposals (a/A/r/R/w/e) and the agent settings screen: no proposals are seeded.",
              "- Stats: r/v/backspace/? on WEEK LEFT and the full Stats screen (S).",
              "- Chat ctrl+r history; Home w (wake) and M (mark all read).", ""]
    OUT.joinpath("index.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
