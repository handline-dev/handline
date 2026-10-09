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
        mk("Rework the whole onboarding flow so that a new user reaches the first useful screen in under a minute, "
           "with the tour, the empty states and the sample data all reviewed by design", "frontend")
        for who, body, kind in (("product", "A long note on the roadmap: " + "the quarter's themes are speed, trust and the first minute. " * 6, None),
                                ("reviewer", "The diff for T%d is ready for you: two files, no migrations." % doing, None),
                                ("product", "Which of the two onboarding flows should we ship first?", "question"),
                                ("frontend", "Pushed the settings page; it needs your eye on the empty state.", None)):
            db.add_request(con, who, actions.HUMAN, body, None, "peer", "async", kind, 0, None)
        db.set_agent_state(con, "backend", "RUNNING")
        db.set_agent_state(con, "reviewer", "FATAL", detail="the launch failed: no such directory")
        con.commit()
    finally:
        con.close()
    from handline.team import hr
    hr.propose("settings", "backend", requester="backend", why="it reads the docs a lot",
               sets={"browser": "playwright", "browser_sites": "https://docs.example.org"})
    hr.propose("add", "dohyun", repo="/repo", requester="reviewer", why="the review queue is two days deep")
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
        await hr_tab(pilot)
        await stats_tab(pilot)
        await home_again(pilot)
        await tasks_tab(pilot)
        await home_again(pilot)
        await home_actions(pilot)
        await home_extras(pilot)
        await account_page(pilot)
        await chat_pages(pilot)
        await modals(pilot)
        await daemon_down(pilot)


async def hr_tab(pilot) -> None:
    from handline.ui.components.agent_settings_screen import AgentSettingsScreen
    await tab_of(pilot, "hr")
    pilot.app.screen.query_one("#hr_table").focus()
    await pilot.pause(0.3)
    await shot(pilot, "hr-proposals", "HR tab", "Roster proposals waiting on the founder: the list, the one under the cursor in full, the agents below.")
    for key, stem, what in (
            ("r", "hr-reject", "r: reject the proposal, with a reason."),
            ("e", "hr-edit-settings", "e: the agent settings screen for the agent under the cursor in the agents list.")):
        if key == "e":
            pilot.app.screen.query_one("#hr_agents").focus()
            await pilot.pause(0.3)
        await press(pilot, key, wait=0.8)
        await shot(pilot, stem, "HR tab", what)
        if type(pilot.app.screen).__name__ == "AgentSettingsScreen":
            await press(pilot, "escape", wait=0.6)
            await press(pilot, "escape", wait=0.6)      # leave_or_ask: a second Esc if it asked
        else:
            await press(pilot, "escape", wait=0.4)
    pilot.app.screen.query_one("#hr_table").focus()
    await pilot.pause(0.3)
    await press(pilot, "w", wait=0.8)
    await shot(pilot, "hr-withdraw", "HR tab", "w: withdraw a proposal the founder raised (what it says on one an agent raised).")
    await press(pilot, "escape", wait=0.4)


async def stats_tab(pilot) -> None:
    await tab_of(pilot, "stats")
    from handline.ui.components.outlook_view import OutlookView
    pilot.app.screen.query_one(OutlookView).focus()
    await pilot.pause(0.3)
    for key, stem, what in (
            ("v", "stats-view", "v: choose what the WEEK LEFT numbers are (session left, week left, ...)."),
            ("r", "stats-range", "r: step the range (12h, 24h, 3d, 7d, 14d; 7d, 14d, 30d in the cost view)."),
            ("question_mark", "stats-keys", "?: the Stats key list.")):
        await press(pilot, key, wait=0.7)
        await shot(pilot, stem, "Stats", what)
        await press(pilot, "escape", wait=0.4)
    await tab_of(pilot, "home")
    await press(pilot, "S", wait=1.2)
    await shot(pilot, "stats-screen", "Stats", "S from Home: the Stats page on its own screen.")
    await press(pilot, "escape", wait=0.6)


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
    await press(pilot, "A", wait=0.5)
    await shot(pilot, "tasks-all-marked", "Tasks tab", "A: mark every row shown.")
    await press(pilot, "A", wait=0.4)
    await press(pilot, "S", wait=0.5)
    await shot(pilot, "tasks-sort-reverse", "Tasks tab", "S: the sort reversed.")
    await press(pilot, "S", wait=0.4)
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
    for key, stem, what in (
            ("a", "task-assign", "a on a task page: hand the task to another agent."),
            ("x", "task-done", "x on a task page: mark it done, with a reason."),
            ("d", "task-discard", "d on a task page: discard it, with a reason.")):
        await press(pilot, key, wait=0.7)
        await shot(pilot, stem, "Task page verbs", what)
        if type(pilot.app.screen).__name__ != "TaskDetail":     # a modal is up: close it, keep the page
            await press(pilot, "escape", wait=0.4)
    await press(pilot, "escape", wait=0.5)
    await press(pilot, "question_mark", wait=0.5)
    await shot(pilot, "tasks-keys", "Tasks tab", "The Tasks tab key list (?).")
    await press(pilot, "escape", wait=0.4)


async def home_extras(pilot) -> None:
    """Home verbs not shot with their panel: w wake, and M mark all read in FOR YOU."""
    await to_panel(pilot, 1)
    await press(pilot, "w", wait=0.8)
    await shot(pilot, "agents-wake", "Home: agent verbs", "w: wake the agent under the cursor (what it says when it is already working).")
    await press(pilot, "escape", wait=0.4)
    await to_panel(pilot, 2)
    await press(pilot, "M", wait=0.8)
    await shot(pilot, "messages-mark-all", "Home: message verbs", "M: mark every message read (what waits on an answer stays).")
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
    await press(pilot, "ctrl+r", wait=0.6)
    await shot(pilot, "chat-history", "Chat page", "ctrl+r: what was sent before; typing narrows the list (empty here: nothing was sent from this scratch home).")
    await press(pilot, "escape", wait=0.3)
    await press(pilot, "slash", wait=0.6)
    await shot(pilot, "chat-slash", "Chat page", "Typing / in the compose box lists the agent's slash commands.")
    await press(pilot, "escape", wait=0.3)


async def daemon_down(pilot) -> None:
    """The home page when the daemon is not running (its banner and what the rows say then)."""
    from handline.ui.pages import home as home_page
    home_page.daemon_running = lambda: False
    await pilot.pause(2.5)
    await shot(pilot, "home-daemon-down", "States", "The home page with the daemon stopped.")
    home_page.daemon_running = lambda: True


async def modal(pilot, screen, stem: str, what: str, group: str = "Modals") -> None:
    """Push `screen` as the app's own code would, shoot it, close it."""
    pilot.app.push_screen(screen)
    await pilot.pause(0.6)
    await shot(pilot, stem, group, what)
    pilot.app.pop_screen()
    await pilot.pause(0.3)


async def modals(pilot) -> None:
    """Modals that open only while a live session is in a state (a blocked prompt, a running command), built from the
    same dicts the code makes them from; and the forms whose open path is a menu or button deep in a settings tab."""
    from handline.ui.chat_page import HandoffModal
    from handline.ui.components.answer_add import AnswerAdd
    from handline.ui.components.confirm import Confirm
    from handline.ui.components.dialog_modal import DialogModal
    from handline.ui.components.permission_modal import PermissionModal
    from handline.ui.components.question_modal import QuestionModal
    from handline.ui.components.schedule_add import ScheduleAdd
    from handline.ui.components.shell_card import OutputModal
    from handline.ui.components.sites_list import SiteEdit
    pilot.app.pop_screen()          # the chat page, back to Home
    await pilot.pause(0.8)

    async def press_fn(*_a):
        return True, ""
    question = {"agent": "product", "header": "Onboarding", "question": "Which of the two onboarding flows should we ship first?",
                "options": [{"label": "Short flow", "description": "three screens"}, {"label": "Guided flow", "description": "seven screens, with a tour"}],
                "multi_select": False, "other_number": 3, "other_text": ""}
    await modal(pilot, QuestionModal(question, press_fn), "modal-question", "An agent's blocked question (one choice) answered from the screen.")
    await modal(pilot, QuestionModal({**question, "header": "Checks", "question": "Which checks should run before the release?",
                                      "options": [{"label": "Unit tests", "checked": True}, {"label": "Browser tests"}, {"label": "Load test"}],
                                      "multi_select": True}, press_fn),
                "modal-question-multi", "The same for a question that takes several answers: checkboxes and Submit picked.")
    await modal(pilot, PermissionModal({"agent": "backend", "tool": "Bash", "lines": ["rm -rf build/", "Do you want to proceed?"], "reason": ""}),
                "modal-permission", "An agent waits for permission to run a tool; y or n answers it.")
    await modal(pilot, DialogModal({"agent": "frontend", "title": "Choose a template", "body": ["Which template for the new page?"],
                                    "options": [(1, "Blank"), (2, "Settings form"), (3, "List and detail")], "always": True}),
                "modal-dialog", "An agent's blocked menu prompt: pick its number, or tick always to keep the pick as a rule.")
    await modal(pilot, HandoffModal("session 2 of backend", "\n".join(f"line {i} of the handoff: what was done, what is left" for i in range(60))),
                "modal-handoff", "A session's handoff, whole and scrollable (enter on the session marker in chat).")
    await modal(pilot, OutputModal("$ ls -la", [f"-rw-r--r--  1 ron  staff  {i * 100}  file{i}.txt" for i in range(80)], 12),
                "modal-output", "Everything a shell command printed (Show all on a shell card), with how many early lines were let go.")
    await modal(pilot, Confirm("Restart backend's session?", "Its terminal is closed and started again.", yes="Restart"),
                "modal-confirm-restart", "A confirm before a restart (f5 in chat).", )
    await modal(pilot, Confirm("Skip permission prompts for backend?", "It will run every tool without asking.", yes="Skip", danger=True),
                "modal-confirm-skip-permissions", "Agent settings: turning off permission prompts asks first (danger style).")
    await modal(pilot, Confirm("Run backend with no sandbox?", "Its commands can then reach everything on this machine.", yes="Turn off", danger=True),
                "modal-confirm-no-sandbox", "Agent settings: turning the sandbox off asks first (danger style).")
    await modal(pilot, ScheduleAdd(), "modal-schedule-add", "Add a schedule: when, and the message sent to the agent.")
    await modal(pilot, ScheduleAdd(("30 9 * * 1-5", "Check the open tasks")), "modal-schedule-edit", "Edit a schedule: the form opens on its time and message.")
    await modal(pilot, SiteEdit(["example.com"]), "modal-site-add", "Add an allowed site (Settings tab, sites).")
    await modal(pilot, SiteEdit([], "example.com"), "modal-site-edit", "Edit a site.")
    await modal(pilot, AnswerAdd(), "modal-answer-add", "Add an auto-answer rule (Settings tab): trust a folder, or answer a prompt for good.")


async def empty_walk() -> None:
    """A home with no agent, task or message: what each page says when there is nothing to list."""
    from handline.ui.bridge_textual import BridgeApp
    t._daemon_home(Path(tempfile.mkdtemp(prefix="tui_catalog_empty_", dir=t.TEST_ROOT)), 0)
    app = BridgeApp()
    async with app.run_test(size=SIZE) as pilot:
        await pilot.pause(1.5)
        await shot(pilot, "empty-home", "Empty states", "Home with one idle agent, no task and nothing waiting on the founder.")
        await press(pilot, "T", wait=0.8)
        await shot(pilot, "empty-tasks", "Empty states", "The Tasks tab with no task.")
        for tab in ("stats", "hr"):
            await tab_of(pilot, tab)
            await shot(pilot, f"empty-{tab}", "Empty states", f"The {tab} tab with nothing recorded.")


def main() -> None:
    seed()
    asyncio.run(walk())
    asyncio.run(empty_walk())
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
              "- The modals in `Modals` are built with sample data, as the code builds them from a live session's prompt; they were not reached by a real blocked session.",
              "- Modals are shown up to the question; no answer is sent, so no result screen (confirm done, error toast) is shown.",
              "- Toasts (a refused verb, a saved setting) do not show in a shot.",
              "- Stats and the Settings tab show the scratch home's thin data: no weekly readings, no per-agent usage rows.",
              "- Opened outside the TUI, so not shot: t (terminal), e (editor), the account Login flow.",
              "- Task page u (queue/draft) and r (reopen a done task); memo and brief edit.",
              "- The f5 restart outside a modal, and approve (a, A) and reject-all (R) in HR: they change the roster.",
              "- Error states: only the FATAL agent row is shot.", ""]
    OUT.joinpath("index.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
