"""The new agent chat page, Textual widgets only. Run it alone with
`~/.orcapipe/venv/bin/python3 -m ocp.ui.chat_page [agent]`; the layout lives in
chat_page.tcss."""
from __future__ import annotations

import json
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from rich.style import Style
from rich.text import Text
from textual import events
from textual.message import Message
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.binding import Binding
from textual.coordinate import Coordinate
from textual.screen import Screen
from textual.widgets import Button, DataTable, Header, Input, OptionList, Select, Static, Switch, Tabs, TextArea
from textual.widgets.option_list import Option

from .. import actions, db
from ..team import agents, ask as askmod
from ..infra import paths, textsafe
from ..usage import status
from . import tui
from .clipboard_app import ClipboardApp
from .theme import follow_os_theme
from ..infra import timefmt
from .components.ime import ImeInput
from .components.confirm import Confirm
from .components.dialog_modal import DialogModal
from .components.permission_modal import PermissionModal
from .components.question_modal import QuestionModal
from .components.jobs_aware import JobsAware, dispatch_agent_verb
from .components.text_prompt import TextPrompt, _TextBox
from .components.action_bar import ActionBar
from .components.page_header import page_header
from .components.switch_row import SwitchRow

# States of a request that never reached its target: withdrawn, replaced, denied.
# They are not part of the conversation, so no chat query reads them.
_GONE = "(" + ", ".join(repr(x) for x in db.NEVER_REACHED_TARGET) + ")"

# Requests read per page. The newest page is what opens; older ones are read
# only when the chat is scrolled to its top.
PAGE = 30


def _scope(agent: str, everyone: bool) -> tuple[str, tuple]:
    """Which requests a chat is made of: the ones between the human and `agent`,
    or, for `everyone`, every one `agent` sent or received."""
    human = actions.HUMAN
    if everyone:
        return "(requester = ? OR target = ?)", (agent, agent)
    return ("((requester = ? AND target = ?) OR (requester = ? AND target = ?))",
            (human, agent, agent, human))


def requests_page(con, agent: str, everyone: bool, before: int | None = None,
                  since: int | None = None, limit: int = PAGE) -> tuple[list, bool]:
    """Requests of a chat, oldest first, and whether older ones remain.
    `before` reads the `limit` requests just older than that id (the newest
    `limit` when None); `since` reads every request from that id on, which is
    how a window already on screen is brought up to date."""
    where, params = _scope(agent, everyone)
    params = (actions.HUMAN, *params)
    sql = ("SELECT id, ts, resolved_at, requester, target, state, body, verdict, "
           "dismissed_by, dismissed_reason, delivered_at, "
           "EXISTS (SELECT 1 FROM requests c WHERE c.parent_id = requests.id AND c.requester = ? "
           f"AND c.state NOT IN {_GONE}) AS replied "
           f"FROM requests WHERE state NOT IN {_GONE} AND {where}")
    if since is not None:
        rows = con.execute(sql + " AND id >= ? ORDER BY id", (*params, since)).fetchall()
        return rows, False
    if before is not None:
        sql += " AND id < ?"
        params = (*params, before)
    rows = con.execute(sql + " ORDER BY id DESC LIMIT ?", (*params, limit + 1)).fetchall()
    return rows[:limit][::-1], len(rows) > limit


def signature(con, agent: str, everyone: bool) -> tuple:
    """A cheap reading that changes whenever something in the chat does: a new
    request, or a request answered, closed or changed."""
    where, params = _scope(agent, everyone)
    row = con.execute(
        "SELECT COUNT(*), COALESCE(MAX(id), 0), COALESCE(MAX(resolved_at), 0), "
        "COALESCE(MAX(delivered_at), 0), COUNT(delivered_at), "
        f"COALESCE(SUM(state = 'queued'), 0) FROM requests WHERE state NOT IN {_GONE} AND {where}",
        params).fetchone()
    return tuple(row)


def messages_of(rows, agent: str) -> list[dict]:
    """One dict per message of these requests, in time order: who, when, text,
    the request it belongs to, whether it is the ask (part 0) or the verdict
    (part 1), which side said it, whether it is waiting on the human or has been
    answered, and whether the human can reply to it."""
    human = actions.HUMAN
    msgs = []
    for r in rows:
        shown = db.shown_verdict(r)
        for part, speaker, to, text, at in (
                (0, r["requester"], r["target"], r["body"], r["ts"]),
                (1, r["target"], r["requester"], shown, r["resolved_at"] or r["ts"])):
            cancelled = r["state"] == "rejected" and r["verdict"] == askmod.CANCEL_VERDICT
            if part == 1 and (not shown or cancelled or db.dismissal(r)[0]):
                continue
            side = "you" if speaker == human else "agent" if speaker == agent else "other"
            queued = (part == 0 and speaker == human and r["target"] != human
                      and r["state"] == "queued")
            held = (part == 0 and speaker != human and r["target"] != human
                    and r["state"] == "queued" and not r["delivered_at"])
            waiting_to_go = held or queued and not r["delivered_at"]
            if part == 0 and r["delivered_at"]:
                at = r["delivered_at"]      # a message sits where its agent read it, not where it queued
            who = ("you" if speaker == human else speaker) + (
                "" if human in (speaker, to) else f" → {to}")
            msgs.append({"who": who, "when": at or 0, "rid": r["id"], "part": part, "side": side,
                         "text": (text or "").strip("\n") or "(empty)",
                         "waiting": part == 0 and r["state"] == "queued" and r["target"] == human,
                         "replyable": human in (r["requester"], r["target"]),
                         "answered": side == "agent" and (part == 0 and bool(shown) or bool(r["replied"])),
                         "queued": queued,
                         "held": held,
                         "to": to,
                         "delivered": bool(r["delivered_at"]),
                         "cancelled": part == 0 and cancelled,
                         "_key": (float("inf") if waiting_to_go else at or 0, r["id"], part)})
    msgs.sort(key=lambda m: m["_key"])
    for m in msgs:
        del m["_key"]
    return msgs


def tasks_of(con, agent: str) -> list[tuple]:
    """The tasks nobody has signed off yet that `agent` owns or opened, as (id,
    role, state, title, the other party, last update). A task it opened for
    itself is owned. The last update is the latest of its creation, edit, done
    and close, and the newest time on any request linked to it."""
    rows = con.execute(
        "SELECT * FROM tasks WHERE closed_by IS NULL AND (owner = ? OR opened_by = ?) ORDER BY id",
        (agent, agent)).fetchall()
    touched = {r[0]: r[1] for r in con.execute(
        "SELECT l.task_id, MAX(COALESCE(MAX(r.ts), 0), COALESCE(MAX(r.resolved_at), 0)) "
        "FROM task_requests l JOIN requests r ON r.id = l.request_id "
        "WHERE l.task_id IN (SELECT id FROM tasks WHERE closed_by IS NULL AND "
        "(owner = ? OR opened_by = ?)) GROUP BY l.task_id", (agent, agent))}
    out = []
    for t in rows:
        own = t["owner"] == agent
        updated = max(t["created_at"] or 0, t["edited_at"] or 0, t["done_at"] or 0,
                      t["closed_at"] or 0, touched.get(t["id"], 0))
        out.append((t["id"], "owns" if own else "opened", actions.task_state(con, t),
                    t["title"], t["opened_by"] if own else t["owner"], updated))
    return out


def task_detail(con, tid: int) -> dict | None:
    """A task as what its page shows: its own fields, the sub-tasks and
    requests tied to it, and which of close, reopen and discard the human may do now.
    None when there is no such task."""
    t = db.get_task(con, tid)
    if t is None:
        return None
    state = actions.task_state(con, t)
    mine = actions.task_asker(con, t) == actions.HUMAN
    kids = [(c["id"], c["title"], actions.task_state(con, c)) for c in
            con.execute("SELECT * FROM tasks WHERE parent_task = ? ORDER BY id", (tid,)).fetchall()]
    reqs = []
    for r in db.task_requests(con, tid):
        body = (r["body"] or "").strip()
        reqs.append((r["id"], r["requester"], r["target"], r["state"],
                     body.splitlines()[0] if body else ""))
    parent = db.get_task(con, t["parent_task"]) if t["parent_task"] else None
    return {"id": tid, "title": t["title"], "state": state, "owner": t["owner"],
            "opened_by": t["opened_by"], "created_at": t["created_at"],
            "parent": (t["parent_task"], parent["title"]) if parent else None,
            "done": (t["done_evidence"], t["done_at"]) if t["done_evidence"] else None,
            "closed": (t["closed_by"], t["closed_at"]) if t["closed_by"] else None,
            "children": kids, "requests": reqs, "asker": actions.task_asker(con, t),
            "can_close": state == "done" and mine,
            "can_reopen": state == "done" and mine,
            "can_discard": state in ("draft", "queued") and mine}


EDITABLE = ("model", "effort", "weight", "repo", "skip_permissions", "context_k")
WEIGHTS = ("light", "heavy")


def settings_of(agent: str) -> dict:
    """An agent's configuration as the settings tab shows it: what its agent
    file says, and the account its session is actually on."""
    cfg = agents.read(agent)
    return {"model_id": status.read_for_agent(agent, agents.current_account(agent)).model_id or "",
            "session_account": agents.current_account(agent) or "",
            "vendor": str(cfg.get("vendor") or agents.DEFAULT_VENDOR),
            "enabled": bool(cfg.get("enabled", True)),
            "model": str(cfg.get("model") or agents.DEFAULT_MODEL),
            "effort": str(cfg.get("effort") or agents.DEFAULT_EFFORT),
            "weight": str(cfg.get("weight") or "light"), "repo": str(cfg.get("repo") or ""),
            "skip_permissions": "on" if agents.skip_permissions_in_effect(agent, cfg) else "off",
            "context_k": str(agents.context_tokens(cfg) // 1000)}


def settings_problem(form: dict) -> str | None:
    """Why `form` cannot be saved, or None. Checked here so nothing is written
    that the agent file would refuse to load again."""
    model, repo = form["model"].strip(), form["repo"].strip()
    if not model:
        return "model cannot be empty"
    if textsafe.has_unsafe(model) or not agents.is_plain_text(model):
        return f"model {model!r} would not be read back as text"
    if form["effort"] not in agents.VALID_EFFORT:
        return f"effort must be one of {', '.join(agents.VALID_EFFORT)}"
    if form["weight"] not in WEIGHTS:
        return f"weight must be one of {', '.join(WEIGHTS)}"
    if form["skip_permissions"] not in ("on", "off"):
        return "skip permissions must be on or off"
    if not form["context_k"].strip().isdigit() or int(form["context_k"]) < 1:
        return "context must be a whole number of thousands of tokens, at least 1"
    if repo and (textsafe.has_unsafe(repo) or not Path(repo).expanduser().is_dir()):
        return f"repo {repo!r} is not a folder"
    return None


def settings_changes(current: dict, form: dict) -> list[tuple[str, str, str]]:
    """(field, old, new) for each editable field the form changes. A blank repo
    means leave it: the agent file cannot be told to forget one."""
    out = []
    for key in EDITABLE:
        new = form[key].strip()
        if key == "repo" and not new:
            continue
        if new != current[key]:
            out.append((key, current[key], new))
    return out


def save_settings(agent: str, changes: list[tuple[str, str, str]]) -> tuple[bool, str]:
    """Write `changes` to the agent's file through the same call `ocp agent
    register` uses, which keeps every other field as it is. A running session
    is not restarted."""
    new = {k: v for k, _, v in changes}
    try:
        agents.register(agent, model=new.get("model"), effort=new.get("effort"),
                        weight=new.get("weight"), repo=new.get("repo"),
                        skip_permissions=None if "skip_permissions" not in new else new["skip_permissions"] == "on",
                        context_tokens=int(new["context_k"]) * 1000 if "context_k" in new else None)
    except (ValueError, agents.ConfigError, OSError) as e:
        return False, f"not saved: {e}"
    return True, "saved: " + ", ".join(f"{k} {v}" for k, _, v in changes)


def send_reply(con, agent: str, rid: int | None, body: str) -> tuple[bool, str]:
    """Send `body` as a reply to request `rid`, or where the conversation says
    it goes when `rid` is None. A question the agent is waiting on is closed
    with `body` as its verdict, and an answer to the human's own request gets a
    new request and is marked read (both `actions.answer_ok`); anything else -
    a question already closed - is a new request under it to the agent on
    that row - its target if the human asked it, else its requester - which in All chat
    is not always the agent whose page this is."""
    if rid is None:
        return actions.chat_reply_ok(con, agent, body)
    row = db.get_request(con, rid)
    if row is None:
        return False, f"#{rid} is gone"
    if (row["target"] == actions.HUMAN and row["state"] == "queued") or actions._is_answer_to_human(row):
        return actions.answer_ok(con, rid, body)
    if not body.strip():
        return False, "nothing sent: no message typed"
    other = row["target"] if row["requester"] == actions.HUMAN else row["requester"]
    ok, msg, _ = askmod.ask(other, body, requester=actions.HUMAN, parent_id=rid)
    return ok, msg


def safe(value) -> str:
    """`value` as one line of plain text nothing in a terminal would obey. Every
    string an agent or a request can put on this page is drawn through this or
    `safe_block`, as a `Text` (never as markup)."""
    return textsafe.oneline(str(value))


def safe_block(value) -> str:
    """`value` as several lines of plain text, tabs widened, nothing a terminal
    would obey."""
    return "\n".join(textsafe.drawable(str(value)))


def message_parts(m: dict) -> tuple[Text | None, Text, Text | None]:
    """What one message draws: its head line (not for the human's own), its
    body, and the note under it (cancelled), all as safe `Text`."""
    mine = m["side"] == "you"
    body = Text(safe_block(m["text"]), style="strike dim" if m.get("cancelled") else "")
    head = None
    if not mine:
        stamp = timefmt.stamp(m["when"], timefmt.CLOCK) if m["when"] else ""
        head = Text(f"{safe(m['who'])}  {stamp}", style="bold")
        if m["answered"]:
            head.append("  answered", style="italic dim")
        elif m["waiting"]:
            head.append("  waiting for you", style="italic yellow")
    note = None
    if m.get("cancelled"):
        note = Text("cancelled" + (" - the agent was told to stop" if m.get("delivered") else ""))
    return head, body, note


def task_page(d: dict) -> dict:
    """What a task's page draws, as safe `Text`: title, badge, the facts, the
    sub-tasks (each a link that opens it) and the requests."""
    def link(tid: int, label: str) -> Text:
        return Text(label, style=Style(underline=True, meta={"@click": f"screen.open_task({tid})"}))

    def when(ts) -> Text:
        return Text(timefmt.stamp(ts, timefmt.DATE_TIME) if ts else "", style="dim")

    facts: list[tuple[str, Text]] = [
        ("owner", Text(safe(d["owner"]))),
        ("opened by", Text.assemble(safe(d["opened_by"]) + "  ", when(d["created_at"])))]
    if d["parent"]:
        facts.append(("parent", link(d["parent"][0], f"{actions.task_ref(d['parent'][0])}  {safe(d['parent'][1])}")))
    if d["done"]:
        facts.append(("done", Text.assemble(safe(d["done"][0]) + "  ", when(d["done"][1]))))
    if d["closed"]:
        facts.append(("closed", Text.assemble(safe(d["closed"][0]) + "  ", when(d["closed"][1]))))
    facts.append(("signs off", Text(safe(d["asker"]))))
    meta = Text()
    for label, value in facts:
        meta.append(f"{label:<11}", style="dim")
        meta.append_text(value)
        meta.append("\n")
    badge = Text(f" {d['state']} ", style=STATE_STYLE.get(d["state"], ""))
    badge.append(f"   {actions.task_ref(d['id'])}", style="dim")
    return {
        "title": Text(safe(d["title"]), style="bold"), "badge": badge, "meta": meta,
        "kids": [Text.assemble(link(i, f"{actions.task_ref(i)}  "), (f"{st:<8}", STATE_STYLE_FG.get(st, "")),
                               link(i, safe(title))) for i, title, st in d["children"]],
        "reqs": [Text.assemble((f"#{i}  ", "dim"), f"{safe(a)} → {safe(b)}  ", (f"{st}  ", "dim"), safe(line))
                 for i, a, b, st, line in d["requests"]]}


class QueueLine(Static):
    """The line between what the agent has read and what is still queued for
    it; a spinner sits in it while the agent is working, and what it waits on when idle."""

    SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, working: bool, rule: bool = True, why: str = "", wait: str = "") -> None:
        super().__init__(classes="queueline")
        self.working, self.rule, self.why, self.wait, self._frame = working, rule, why, wait, 0

    def on_mount(self) -> None:
        self.set_interval(0.1, self._tick)

    def _tick(self) -> None:
        if self.working:
            self._frame += 1
            self.refresh()

    def render(self) -> Text:
        mid = f" {self.SPINNER[self._frame % len(self.SPINNER)]} working " if self.working \
            else f" {safe(self.wait)} " if self.wait else ""
        if not self.rule:
            return Text(mid, style="dim")
        if self.why:
            room = max(self.size.width - len(mid) - 12, 0)
            why = safe(self.why)
            mid += f"· {why if len(why) <= room else why[:max(room - 1, 0)] + '…'} "
        side = max(self.size.width - len(mid), 0)
        left = side // 2
        return Text("─" * left + mid + "─" * (side - left), style="dim")


def transcript_widgets(msgs: list[dict], working: bool, why: str = "", wait: str = "") -> list:
    """The messages as widgets, the queue line ahead of the first message the
    agent has not read yet; with none unread, a spare row at the bottom that
    only shows the spinner. `wait` (what the agent waits on) shows where the spinner does."""
    out: list = []
    line = False
    for m in msgs:
        if (m.get("held") or m.get("queued") and not m.get("delivered")) and not line:
            out.append(QueueLine(working, why=why, wait=wait))
            line = True
        out.append(ChatMessage(m, working))
    if not line:
        out.append(QueueLine(working, rule=False, wait=wait))
    return out


class ChatMessage(Vertical):
    """One message; a click makes it what the next thing typed replies to. Its send
    now button is shown only while the agent is `working` (mid-turn): with the agent
    idle the message goes on its own."""

    def __init__(self, m: dict, working: bool = True) -> None:
        side = {"you": "me", "agent": "them"}.get(m["side"], "other")
        head, body_text, note = message_parts(m)
        parts = [Static(body_text, classes="text")]
        if head is not None:
            parts.insert(0, Static(head, classes="who"))
        if note is not None:
            parts.append(Static(note, classes="qstate"))
        self._send_now: Button | None = None
        if m.get("queued"):
            buttons = []
            if not m.get("delivered"):
                self._send_now = Button("⚡ send now", classes="sendnow", compact=True)
                buttons = [self._send_now, Button("✕ cancel", classes="cancel", compact=True)]
            parts.append(Horizontal(
                Static("delivered, not answered yet" if m.get("delivered") else "queued, not delivered",
                       classes="qstate"),
                *buttons, classes="qrow"))
        super().__init__(*parts, classes=f"msg {side}")
        self.m = m
        self.set_working(working)

    def set_working(self, working: bool) -> None:
        if self._send_now is not None:
            self._send_now.display = working

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.has_class("sendnow"):
            self.screen.send_now(self.m["rid"])
        else:
            self.screen.cancel_message(self.m["rid"], self.m["text"])

    def on_click(self) -> None:
        if self.m["replyable"]:
            self.screen.reply_to(None if self.screen.replying is self else self)


# Slash commands of the page itself: name, what it does, the tab it opens.
PAGE_COMMANDS = (("chat", "My chat", "mine"), ("all", "All chat", "all"), ("task", "Tasks", "tasks"),
                 ("sort", "Sort the tasks", "tasks"), ("settings", "Agent settings", "settings"),
                 ("back", "Back to the main screen", None))


MENU_MAX = 8
VERB_WHAT = {'terminal': 'go to its terminal', 'wake': 'wake it now', 'close': 'close its session',
             'park': 'park it', 'fold': 'fold it into its handoff', 'move': 'move it to another account'}

# The home page's agent keys that make sense typed in the agent's own chat: `open` is this page,
# and `message` and `editor` are the box itself.
CHAT_VERBS = ('terminal', 'wake', 'close', 'park', 'fold', 'move')
_REF_TAIL = re.compile(r"(?:^|\s)#(\d*)$")


HISTORY_MAX = 100


def history_file() -> Path:
    return paths.home() / "chat_history.json"


def log_file() -> Path:
    return paths.home() / "chat_page.log"


def log_action(what: str) -> None:
    """One line in the chat page's own log for something the human did through
    it that changed or tried to change state: when, and what came of it."""
    try:
        log_file().parent.mkdir(parents=True, exist_ok=True)
        with log_file().open("a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + " ".join(what.split()) + "\n")
    except OSError:
        pass


def load_history() -> list[str]:
    """What was typed and sent, oldest first. A missing or unreadable file is
    an empty history, never an error."""
    try:
        data = json.loads(history_file().read_text())
    except (OSError, ValueError):
        return []
    return [x for x in data if isinstance(x, str)][-HISTORY_MAX:] if isinstance(data, list) else []


def remember(history: list[str], text: str) -> list[str]:
    """`history` with `text` as the newest entry: an earlier copy of it moves
    up instead of repeating, and only the newest HISTORY_MAX are kept."""
    text = text.strip("\n")
    if not text.strip():
        return history
    return ([h for h in history if h != text] + [text])[-HISTORY_MAX:]


def save_history(history: list[str]) -> None:
    try:
        history_file().parent.mkdir(parents=True, exist_ok=True)
        history_file().write_text(json.dumps(history, ensure_ascii=False))
    except OSError:
        pass


def history_matches(query: str, history: list[str]) -> list[str]:
    """Entries containing `query` (any case), newest first."""
    q = query.strip().lower()
    return [h for h in reversed(history) if q in h.lower()]


@dataclass
class Pending:
    """A message handed to a worker and not yet answered."""
    widget: object
    body: str


@dataclass(frozen=True)
class Suggestion:
    """One row of the menu: what taking it does (`run` a page command, `open`
    a task, `sort` the tasks, `insert` a reference), its argument, the text Tab
    puts in the box, and how it is drawn."""
    action: str
    arg: str
    tab: str
    label: tuple[str, str]      # (bold part, dim part)


def suggest(before: str, tasks: list[tuple], requests: list[tuple], verbs: tuple = ()) -> list[Suggestion]:
    """What the text before the cursor could become. `tasks` are `tasks_of`
    rows; `requests` are (id, "who → to", first line) newest first.

    A `/` line names a page command; after `/task ` it names one of the tasks
    (by number or a word of its title), after `/sort ` a table column. A `#` at
    the end of a word offers the tasks and requests whose number starts with
    the digits typed. Anything else offers nothing. `verbs` are the agent actions on offer now."""
    if before.startswith("/") and "\n" not in before:
        name, sep, arg = before[1:].partition(" ")
        if not sep:
            return [Suggestion("run", n, f"/{n} ", (f"/{n}", f"   {what}"))
                    for n, what, _ in PAGE_COMMANDS if n.startswith(name)] \
                + [Suggestion("verb", v, f"/{v} ", (f"/{v}", f"   {VERB_WHAT[v]}"))
                   for v in verbs if v.startswith(name)]
        arg = arg.strip().lower()
        if name == "task":
            hits = [t for t in tasks if not arg or str(t[0]).startswith(arg.lstrip("#").lstrip("t"))
                    or arg in t[3].lower()]
            hits.sort(key=lambda t: t[5], reverse=True)
            return [Suggestion("open", str(t[0]), f"/task {t[0]}", (actions.task_ref(t[0]), f"  {t[2]}  {safe(t[3])}"))
                    for t in hits[:MENU_MAX]]
        if name == "sort":
            return [Suggestion("sort", c, f"/sort {c}", (c, "   sort the tasks by this column"))
                    for c in actions.TASK_SORTS if c.startswith(arg)]
        return []
    m = _REF_TAIL.search(before)
    if not m:
        return []
    digits = m.group(1)
    out = [Suggestion("insert", actions.task_ref(t[0]), actions.task_ref(t[0]), (actions.task_ref(t[0]), f"  task · {safe(t[3])}"))
           for t in sorted(tasks, key=lambda t: t[5], reverse=True) if str(t[0]).startswith(digits)]
    out += [Suggestion("insert", str(rid), str(rid), (f"#{rid}", f"  {safe(who)} · {safe(line)}"))
            for rid, who, line in requests if str(rid).startswith(digits)]
    return out[:MENU_MAX]


def refs_of(con, agent: str) -> tuple[list[tuple], list[tuple]]:
    """What a `#` can point at for `agent`: its open tasks and its newest
    requests (every one it sent or received)."""
    reqs = con.execute(
        f"SELECT id, requester, target, body FROM requests WHERE state NOT IN {_GONE} AND "
        "(requester = ? OR target = ?) ORDER BY id DESC LIMIT 30", (agent, agent)).fetchall()
    return tasks_of(con, agent), [
        (r["id"], f"{r['requester']} → {r['target']}",
         textsafe.oneline((r["body"] or "").strip().splitlines()[0] if (r["body"] or "").strip() else "")[:60])
        for r in reqs]


# What each word of `tui.screen_state` says about the agent, as (glyph, label,
# rich style): the same six words the dashboard's state column uses.
AGENT_BADGE = {"RUNNING": ("●", "working", "green"), "IDLE": ("○", "idle", "dim"),
               "PENDING": ("◌", "waiting to start", "yellow"), "MOVING": ("◐", "moving account", "yellow"),
               "DIALOG": ("▲", "needs you", "bold yellow"), "FATAL": ("✕", "stalled", "bold red")}


def agent_badge(word: str, detail: str = "") -> Text:
    """The agent's state as one small badge: a coloured glyph, what it means,
    and what it is doing or waiting for when the state says."""
    glyph, label, style = AGENT_BADGE.get(word, AGENT_BADGE["IDLE"])
    out = Text(f"{glyph} {label}", style=style)
    if detail:
        out.append(f" · {textsafe.oneline(detail)[:40]}", style="dim")
    return out


def agent_state_of(con, agent: str) -> tuple[str, str]:
    """(word, detail) for `agent`: the same reading the dashboard row shows."""
    return tui.screen_state(con, db.get_agent_state(con, agent))


class ChatBox(_TextBox):
    """The chat box. While the command menu is up, Up and Down move in it and
    Tab takes the highlighted command; everything else is `_TextBox`'s."""

    class Complete(Message):
        pass

    class Recall(Message):
        """Up on the first line or Down on the last: step through what was sent."""

        def __init__(self, older: bool) -> None:
            super().__init__()
            self.older = older

    menu: OptionList | None = None

    async def _on_key(self, event: events.Key) -> None:
        menu = self.menu
        if menu is not None and menu.display and menu.option_count:
            if event.key in ("up", "down"):
                event.prevent_default()
                event.stop()
                menu.action_cursor_up() if event.key == "up" else menu.action_cursor_down()
                return
            if event.key == "tab":
                event.prevent_default()
                event.stop()
                self.post_message(self.Complete())
                return
        row, _ = self.cursor_location
        last = self.document.line_count - 1
        if event.key == "up" and row == 0 or event.key == "down" and row == last:
            event.prevent_default()
            event.stop()
            self.post_message(self.Recall(event.key == "up"))
            return
        await super()._on_key(event)


class TaskTable(DataTable):
    """A table whose click on a row opens it: DataTable alone only moves the
    cursor there and selects on a second click. `prevent_default` keeps the
    base handler from running a second time on the same click."""

    class Sort(Message):
        """Sort by the column named, or 'next' column, or 'reverse' the order."""

        def __init__(self, column: str) -> None:
            super().__init__()
            self.column = column

    click_selects = True
    BINDINGS = [Binding("s", "sort('next')", "sort", show=False),
                Binding("S", "sort('reverse')", "reverse", show=False)]

    def action_sort(self, column: str) -> None:
        self.post_message(self.Sort(column))

    async def _on_click(self, event: events.Click) -> None:
        event.prevent_default()
        event.stop()
        meta = event.style.meta
        if meta.get("row") == -1 and meta.get("column", -1) >= 0:
            self.post_message(self.Sort(self.ordered_columns[meta["column"]].key.value))
        elif meta.get("row", -1) >= 0 and "column" in meta:
            self.cursor_coordinate = Coordinate(meta["row"], meta["column"])
            if self.click_selects:
                self.action_select_cursor()


STATE_STYLE_FG = {"draft": "dim", "queued": "yellow", "doing": "blue", "done": "green", "closed": "dim"}
STATE_STYLE = {"draft": "white on grey30", "queued": "black on yellow", "doing": "white on blue",
               "done": "black on green", "closed": "white on grey37"}


class TaskActions:
    """Close, reopen and discard for the task `self.tid` (its `task_detail` in `self.d`): the
    calls the human may make, asked for and reported the same wherever the buttons are.
    The host redraws after an action (`redraw`); a refusal is a toast (`say`)."""

    tid: int
    d: dict | None

    def say(self, text: str) -> None:
        self.notify(safe(text), severity="error", timeout=10, markup=False)

    def report(self, verb: str, ok: bool, said: str) -> None:
        """Say what came of an action in a toast, log it, and redraw."""
        log_action(f"task {actions.task_ref(self.tid)} {verb}: {'ok' if ok else 'refused'} - {said}")
        self.notify(safe(said), title=f"{verb} {actions.task_ref(self.tid)}", severity="information" if ok else "error",
                    timeout=10, markup=False)
        self.redraw()

    def action_close_task(self) -> None:
        if not (self.d and self.d["can_close"]):
            self.say("only a done task that you asked for can be closed")
            return

        def answered(yes: bool | None) -> None:
            log_action(f"task {actions.task_ref(self.tid)} close: answered {'yes' if yes else 'no'}")
            if yes:
                con = db.connect()
                try:
                    ok, said = actions.close_task(con, self.tid, actions.HUMAN)
                finally:
                    con.close()
                self.report("close", ok, said)
        self.app.push_screen(
            Confirm(f"Close task {actions.task_ref(self.tid)}", "Your sign-off, and final: a closed task cannot be reopened.",
                    yes="Close", danger=True), answered)

    def action_reopen_task(self) -> None:
        if not (self.d and self.d["can_reopen"]):
            self.say("only a done task that you asked for can be reopened")
            return

        def answered(reason: str | None) -> None:
            log_action(f"task {actions.task_ref(self.tid)} reopen: reason {'given' if reason and reason.strip() else 'not given'}")
            if reason and reason.strip():
                con = db.connect()
                try:
                    ok, said = actions.reopen_task(con, self.tid, actions.HUMAN, reason)
                finally:
                    con.close()
                self.report("reopen", ok, said)
        self.app.push_screen(TextPrompt("reopen - why?"), answered)

    def action_discard_task(self) -> None:
        if not (self.d and self.d["can_discard"]):
            self.say("only a task that you asked for, with nothing running on it, can be discarded")
            return

        def answered(reason: str | None) -> None:
            log_action(f"task {actions.task_ref(self.tid)} discard: reason {'given' if reason and reason.strip() else 'not given'}")
            if reason and reason.strip():
                con = db.connect()
                try:
                    ok, said = actions.discard_task(con, self.tid, actions.HUMAN, reason)
                finally:
                    con.close()
                self.report("discard", ok, said)
        self.app.push_screen(TextPrompt("discard - why?"), answered)


class TaskPane(TaskActions, Vertical):
    """The task under the cursor, beside a table when the terminal is wide: what its page
    (`chat_page.task_page`) says, the sub-tasks without links."""

    WIDE = 130
    WIDTH = 50
    DEFAULT_CSS = f"""
    TaskPane {{ width: {WIDTH}; height: 100%; padding: 0 1; border-left: solid $primary; }}
    TaskPane #pane_scroll {{ height: 1fr; }}
    TaskPane #pane_foot {{ dock: bottom; height: auto; }}
    TaskPane #pane_buttons {{ height: 1; }}
    """

    class Changed(Message):
        """An action changed the task: the host redraws its table."""

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.tid = 0
        self.d = None

    def fit(self, width: int, table: "TaskTable") -> bool:
        """Show beside `table` when `width` allows; then a click on a row only moves the
        cursor, and the pane says what is under it."""
        wide = width >= self.WIDE
        self.display = wide
        table.click_selects = not wide
        return wide

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="pane_scroll"):
            yield Static(id="pane_text")
        with Vertical(id="pane_foot"):
            with Horizontal(id="pane_buttons"):
                yield Button("Close", id="pane_close", variant="success", compact=True)
                yield Button("Reopen", id="pane_reopen", variant="warning", compact=True)
                yield Button("Discard", id="pane_discard", variant="error", compact=True)

    def redraw(self) -> None:
        self.show(self.tid)
        self.post_message(self.Changed())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        {"pane_close": self.action_close_task, "pane_reopen": self.action_reopen_task,
         "pane_discard": self.action_discard_task}[event.button.id]()

    def show(self, tid: int | None) -> None:
        text = self.query_one("#pane_text", Static)
        self.tid = tid or 0
        self.d = None
        for name in ("close", "reopen", "discard"):
            self.query_one(f"#pane_{name}", Button).disabled = True
        if tid is None:
            text.update(Text("no task", style="dim"))
            return
        con = db.connect()
        try:
            d = task_detail(con, tid)
        finally:
            con.close()
        if d is None:
            text.update(Text(f"task {actions.task_ref(tid)} is gone", style="dim"))
            return
        self.d = d
        for name in ("close", "reopen", "discard"):
            self.query_one(f"#pane_{name}", Button).disabled = not d[f"can_{name}"]
        page = task_page(d)
        out = Text()
        for part in (page["title"], page["badge"]):
            out.append_text(part)
            out.append("\n")
        out.append("\n")
        out.append_text(page["meta"])
        for title, lines in (("Sub-tasks", [Text(f"{actions.task_ref(i)}  {st}  {t}")
                                            for i, t, st in d["children"]]), ("Requests", page["reqs"])):
            out.append(f"\n{title}", style="bold")
            out.append(f"  {len(lines)}\n", style="dim")
            for line in lines or [Text("none", style="dim")]:
                out.append("  ")
                out.append_text(line)
                out.append("\n")
        text.update(out)


class TaskDetail(TaskActions, Screen):
    """One task: its title and state, who has it, what it grew out of, its
    sub-tasks and requests, and the calls the human may make on it - close
    a done task, send it back with a reason, or discard one nothing is running on."""
    BINDINGS = [Binding("escape", "app.pop_screen", "back"),
                Binding("c", "close_task", "close"),
                Binding("r", "reopen_task", "reopen"),
                Binding("d", "discard_task", "discard")]

    def __init__(self, tid: int) -> None:
        super().__init__(classes="chat_page")
        self.tid = tid
        self.d: dict | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="t_top"):
            yield Button("‹ Back", id="back", compact=True)
        with VerticalScroll(id="detail_pane"):
            yield Static(id="t_title")
            yield Static(id="t_badge")
            yield Static(id="t_meta")
            yield Static(id="t_kids", classes="section")
            yield Static(id="t_reqs", classes="section")
        with ActionBar(id="t_actions"):
            yield Button("Close  c", id="close", variant="success", compact=True)
            yield Button("Reopen  r", id="reopen", variant="warning", compact=True)
            yield Button("Discard  d", id="discard", variant="error", compact=True)

    def on_mount(self) -> None:
        self.sub_title = f"task {actions.task_ref(self.tid)}"
        self.load()

    def load(self) -> None:
        con = db.connect()
        try:
            self.d = task_detail(con, self.tid)
        finally:
            con.close()
        d = self.d
        if d is None:
            self.query_one("#t_title", Static).update(f"task {actions.task_ref(self.tid)} is gone")
            return
        page = task_page(d)
        self.query_one("#t_title", Static).update(page["title"])
        self.query_one("#t_badge", Static).update(page["badge"])
        self.query_one("#t_meta", Static).update(page["meta"])
        self.query_one("#t_kids", Static).update(self._section("Sub-tasks", page["kids"]))
        self.query_one("#t_reqs", Static).update(self._section("Requests", page["reqs"]))
        self.query_one("#close", Button).disabled = not d["can_close"]
        self.query_one("#reopen", Button).disabled = not d["can_reopen"]
        self.query_one("#discard", Button).disabled = not d["can_discard"]

    def action_open_task(self, tid: int) -> None:
        self.app.push_screen(TaskDetail(tid))

    @staticmethod
    def _section(title: str, lines: list[Text]) -> Text:
        out = Text(title, style="bold")
        out.append(f"  {len(lines)}\n", style="dim")
        for line in lines or [Text("none", style="dim")]:
            out.append("  ")
            out.append_text(line)
            out.append("\n")
        return out

    def redraw(self) -> None:
        self.load()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        {"close": self.action_close_task, "reopen": self.action_reopen_task,
         "discard": self.action_discard_task,
         "back": self.app.pop_screen}[event.button.id]()


class ChatPage(JobsAware, Screen):
    BINDINGS = [("escape", "back", "back"), ("ctrl+r", "history", "history")]

    def __init__(self, agent: str) -> None:
        super().__init__(classes="chat_page")
        self.agent = agent
        self.replying: ChatMessage | None = None
        self.everyone = False
        self.showing_tasks = False
        self.showing_settings = False
        self.settings: dict | None = None
        self._menu_closed = False
        self.agent_state = ("IDLE", "")
        self.verbs_pool: tuple = ()           # the agent actions `/` can offer right now
        self.tasks_pool: list[tuple] = []     # what `#` and `/task` can offer
        self.requests_pool: list[tuple] = []
        self.suggestions: list[Suggestion] = []
        self.history = load_history()
        self.hist_pos: int | None = None    # entry Up/Down has recalled, None when typing fresh
        self.hist_draft = ""                # what was in the box before the first Up
        self._recalling = False
        self.history_mode = False           # Ctrl+R: the menu lists the history
        self.sending = 0                    # sends handed to workers, not yet answered
        self.pending: list[Pending] = []
        self._spinning, self._frame = False, 0
        self._send_lock = threading.Lock()  # one send at a time, in the order typed
        self.last_chat = "mine"     # the chat tab Esc returns to from Tasks
        self.oldest: int | None = None      # id of the oldest request on screen
        self.more = False                   # older requests than `oldest` exist
        self._sig: tuple | None = None
        self._dialog_busy, self._dialog_at = False, 0.0
        self._dialog_closed: str | None = None      # the title of a dialog the founder closed
        self._confirm_busy, self._confirm_at = False, 0.0
        self._confirm_closed: int | None = None      # the wait id of a permission prompt the founder closed
        self._question_busy, self._question_at = False, 0.0
        self._question_closed: tuple[str, ...] | None = None      # the lines of a question the founder closed

    def compose(self) -> ComposeResult:
        yield from page_header([("My chat", "mine"), ("All chat", "all"), ("Tasks", "tasks"),
                                ("Settings", "settings")], back=True)
        with Horizontal(id="body"):
            yield VerticalScroll(id="transcript")
        from .components.tasks_view import TasksView    # it draws on this module
        yield TasksView(agent=self.agent, id="tasks_view")
        with Vertical(id="settings_view"):
            with VerticalScroll(id="settings_pane"):
                for key, label in (("session_account", "session runs on"),
                                   ("vendor", "vendor"), ("enabled", "enabled")):
                    with Horizontal(classes="frow"):
                        yield Static(label, classes="flabel")
                        yield Static(id=f"s_{key}", classes="fvalue")
                with Horizontal(classes="frow"):
                    yield Static("model", classes="flabel")
                    yield ImeInput(id="s_model", compact=True)
                with Horizontal(classes="frow"):
                    yield Static("effort", classes="flabel")
                    yield Select([(e, e) for e in agents.VALID_EFFORT], id="s_effort", allow_blank=False, compact=True)
                with Horizontal(classes="frow"):
                    yield Static("weight", classes="flabel")
                    yield Select([(w, w) for w in WEIGHTS], id="s_weight", allow_blank=False, compact=True)
                with Horizontal(classes="frow"):
                    yield Static("repo", classes="flabel")
                    yield ImeInput(id="s_repo", compact=True, placeholder="where it works")
                yield SwitchRow("skip prompts", "claude --dangerously-skip-permissions, from the next launch", "s_skip")
                with Horizontal(classes="frow"):
                    yield Static("hand off at", classes="flabel")
                    yield ImeInput(id="s_context_k", compact=True, restrict=r"[0-9]*", max_length=6)
                    yield Static("k tokens", classes="unit")
                    yield Static(id="s_context_share", classes="fvalue")
            with ActionBar(id="s_buttons"):
                yield Button("Save", id="s_save", variant="primary", compact=True)
                yield Button("Reset", id="s_reset", compact=True)
        with Vertical(id="dock"):
            yield OptionList(id="menu")
            with Vertical(id="composer"):
                yield ChatBox(id="compose", placeholder="message · ctrl+j new line, / for a command")
                yield Static(self._status(), id="status")

    def _status(self) -> Text:
        """The line under the box: what the next message will be, and to whom."""
        reply = self.replying
        word, detail = self.agent_state
        return Text.assemble(
            ("Reply" if reply else "Chat", "bold blue"), (" · ", "dim"), self.agent, "  ",
            agent_badge(word, "" if detail.startswith("waiting for") else detail),   # the queue line says it
            (f"   {self.SPINNER[self._frame % len(self.SPINNER)]} sending"
             + (f" {self.sending}" if self.sending > 1 else "") + "…" if self.sending else "", "cyan"),
            (f"   {safe(reply.m['who'])}: {safe(reply.m['text'])[:40]}" if reply else "", "dim"),
            ("  · esc to cancel" if reply else "", "dim"))

    def on_mount(self) -> None:
        self.sub_title = f"agent {self.agent}"
        self.query_one("#tasks_view").display = False
        self.query_one("#compose", ChatBox).menu = self.query_one("#menu", OptionList)
        self.query_one("#compose", ChatBox).focus()
        self.refresh_view()
        self.set_interval(2, self.refresh_view)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "leave":
            event.stop()
            self.leave()
        elif event.button.id == "s_save":
            event.stop()
            self.save_form()
        elif event.button.id == "s_reset":
            event.stop()
            self.load_settings()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self.app.push_screen(TaskDetail(int(event.row_key.value)))

    DIALOG_POLL_S = 4.0

    def poll_dialog(self) -> None:
        """Ask the agent's terminal, off the event loop, whether it is showing a dialog."""
        if self._dialog_busy or time.time() - self._dialog_at < self.DIALOG_POLL_S:
            return
        self._dialog_busy, self._dialog_at = True, time.time()

        def read() -> None:
            con = db.connect()
            try:
                found = actions.dialog_of(con, self.agent)
            except Exception:  # noqa: BLE001 - an unreadable screen shows no modal
                found = None
            finally:
                con.close()
            self.app.call_from_thread(self.dialog_seen, found)
        self.run_worker(read, thread=True, exclusive=True, group="dialog")

    def dialog_seen(self, found: dict | None) -> None:
        self._dialog_busy = False
        if found is None:
            self._dialog_closed = None          # gone: the same dialog coming back is asked again
            return
        if found["title"] == self._dialog_closed or isinstance(self.app.screen, (DialogModal, QuestionModal)):
            return
        self.app.push_screen(DialogModal(found), lambda picked: self.dialog_answered(found, picked))

    def dialog_answered(self, found: dict, picked: "tuple[int, bool] | None") -> None:
        if picked is None:
            self._dialog_closed = found["title"]      # closed by hand: not offered again while it stays
            return
        number, always = picked

        def press() -> None:
            con = db.connect()
            try:
                ok, said = actions.answer_dialog(con, self.agent, found["title"], number, always)
            finally:
                con.close()
            log_action(f"dialog {self.agent}: {number} always={always}: {'ok' if ok else 'refused'} - {said}")
            self.app.call_from_thread(self.notify, safe(said), title=f"dialog {self.agent}",
                                      severity="information" if ok else "error",
                                      timeout=10, markup=False)
        self.run_worker(press, thread=True, group="dialog_press")

    CONFIRM_POLL_S = 2.0

    def poll_confirmation(self) -> None:
        """Ask the agent's terminal, off the event loop, whether it is showing a
        Claude permission prompt (T224)."""
        if self._confirm_busy or time.time() - self._confirm_at < self.CONFIRM_POLL_S:
            return
        self._confirm_busy, self._confirm_at = True, time.time()

        def read() -> None:
            con = db.connect()
            try:
                found = actions.pending_confirmation(con, self.agent)
            except Exception:  # noqa: BLE001 - an unreadable screen shows no modal
                found = None
            finally:
                con.close()
            self.app.call_from_thread(self.confirmation_seen, found)
        self.run_worker(read, thread=True, exclusive=True, group="confirm")

    def confirmation_seen(self, found: dict | None) -> None:
        """`found["wait"]` None is a prompt with no hook record to pin an identity to:
        `answer_confirmation` refuses one on principle (T223), so a Yes/No for it would
        be two buttons that always fail. It is left for the agent's own terminal
        instead."""
        self._confirm_busy = False
        if found is None:
            self._confirm_closed = None          # gone: the same prompt coming back is asked again
            return
        if found["wait"] is None or found["wait"] == self._confirm_closed \
                or isinstance(self.app.screen, (DialogModal, PermissionModal, QuestionModal)):
            return
        self.app.push_screen(PermissionModal(found), lambda picked: self.confirmation_answered(found, picked))

    def confirmation_answered(self, found: dict, picked: "bool | None") -> None:
        if picked is None:
            self._confirm_closed = found["wait"]     # closed by hand: not offered again while it stays
            return
        self._confirm_closed = found["wait"]      # answered: not offered again before the screen catches up
        yes = picked

        def press() -> None:
            con = db.connect()
            try:
                ok, said = actions.answer_confirmation(con, self.agent, yes, found)
            finally:
                con.close()
            log_action(f"confirm {self.agent}: {'yes' if yes else 'no'}: {'ok' if ok else 'refused'} - {said}")
            self.app.call_from_thread(self.notify, safe(said), title=f"confirm {self.agent}",
                                      severity="information" if ok else "error",
                                      timeout=10, markup=False)
        self.run_worker(press, thread=True, group="confirm_press")

    QUESTION_POLL_S = 2.0

    def poll_question(self) -> None:
        """Ask the agent's terminal, off the event loop, whether it is showing a
        pending AskUserQuestion (T304)."""
        if self._question_busy or time.time() - self._question_at < self.QUESTION_POLL_S:
            return
        self._question_busy, self._question_at = True, time.time()

        def read() -> None:
            con = db.connect()
            try:
                found = actions.pending_question(con, self.agent)
            except Exception:  # noqa: BLE001 - an unreadable screen shows no modal
                found = None
            finally:
                con.close()
            self.app.call_from_thread(self.question_seen, found)
        self.run_worker(read, thread=True, exclusive=True, group="question")

    def question_seen(self, found: dict | None) -> None:
        self._question_busy = False
        if found is None:
            self._question_closed = None          # gone: the same question coming back is asked again
            return
        lines = tuple(found["lines"])
        if lines == self._question_closed \
                or isinstance(self.app.screen, (DialogModal, PermissionModal, QuestionModal, TextPrompt)):
            return
        self.app.push_screen(QuestionModal(found), lambda picked: self.question_answered(found, picked))

    def question_answered(self, found: dict, picked: "tuple[tuple[int, ...], str | None] | None") -> None:
        lines = tuple(found["lines"])
        if picked is None:
            self._question_closed = lines     # closed by hand: not offered again while it stays
            return
        self._question_closed = lines     # answered: not offered again before the screen catches up
        choices, text = picked

        def press() -> None:
            con = db.connect()
            try:
                ok, said = actions.answer_question(con, self.agent, found, choices, text)
            finally:
                con.close()
            log_action(f"question {self.agent}: {'ok' if ok else 'refused'} - {said}")
            self.app.call_from_thread(self.notify, safe(said), title=f"question {self.agent}",
                                      severity="information" if ok else "error",
                                      timeout=10, markup=False)
        self.run_worker(press, thread=True, group="question_press")

    def refresh_agent_state(self) -> None:
        con = db.connect()
        try:
            state = agent_state_of(con, self.agent)
        finally:
            con.close()
        if state == self.agent_state:
            return
        # The word (RUNNING/IDLE/...) flips rarely - that is what a queued
        # message's wait reason and the working flag follow. The detail text
        # ticks every second while background work is under 2 minutes old
        # (timefmt.ago), so comparing the whole tuple here would rebuild the
        # transcript (`_sig = None`) on almost every 2s poll for no reason.
        word_changed = state[0] != self.agent_state[0]
        self.agent_state = state
        if word_changed:
            for line in self.query(QueueLine):
                line.working = state[0] == "RUNNING"
            for msg in self.query(ChatMessage):
                msg.set_working(state[0] == "RUNNING")
            self._sig = None                # the reason a message waits follows the agent's state
        self.query_one("#status", Static).update(self._status())

    def refresh_pools(self) -> None:
        con = db.connect()
        try:
            self.tasks_pool, self.requests_pool = refs_of(con, self.agent)
            self.verbs_pool = tuple(v for v in CHAT_VERBS if actions.can(con, self.agent, v)[0])
        finally:
            con.close()

    def refresh_view(self) -> None:
        self.refresh_agent_state()
        self.poll_dialog()
        self.poll_confirmation()
        self.poll_question()
        self.refresh_pools()
        if self.showing_settings:
            return
        if not self.showing_tasks:
            self.refresh_conversation()

    def refresh_conversation(self, force: bool = False) -> None:
        """Bring what is on screen up to date. Unchanged, this is one small
        count; changed, it re-reads only the requests already on screen and
        anything newer, never the whole history."""
        con = db.connect()
        try:
            sig = signature(con, self.agent, self.everyone)
            if sig == self._sig and not force:
                return
            self._sig = sig
            if self.oldest is None:
                rows, self.more = requests_page(con, self.agent, self.everyone)
            else:
                rows, _ = requests_page(con, self.agent, self.everyone, since=self.oldest)
        finally:
            con.close()
        self.oldest = rows[0]["id"] if rows else None
        keep = (self.replying.m["rid"], self.replying.m["part"]) if self.replying else None
        pane = self.query_one("#transcript", VerticalScroll)
        pane.remove_children()
        msgs = messages_of(rows, self.agent)
        waiting = [m for m in msgs if m["queued"] and not m["delivered"]]
        held = [m for m in msgs if m["held"]]
        why = ""
        if waiting and self.agent_state[0] != "RUNNING":     # the spinner already says it
            con = db.connect()
            try:
                why = actions.queue_reason(con, self.agent, waiting[0]["rid"])
            finally:
                con.close()
        elif not waiting and held:      # an agent's message waits on its recipient, not on this page's agent
            con = db.connect()
            try:
                why = f"{held[0]['to']}: {actions.queue_reason(con, held[0]['to'], held[0]['rid'])}"
            finally:
                con.close()
        state, detail = self.agent_state
        widgets = transcript_widgets(msgs, state == "RUNNING", why,
                                     detail if detail.startswith("waiting for") else "")
        pane.mount(*widgets)
        pane.scroll_end(animate=False)
        self.replying = None
        for w in widgets:
            if keep and isinstance(w, ChatMessage) and (w.m["rid"], w.m["part"]) == keep:
                self.reply_to(w)

    def load_older(self) -> None:
        """Read the page of requests just older than the oldest on screen and
        put it above, leaving the view where it was."""
        if not self.more or self.oldest is None:
            return
        con = db.connect()
        try:
            rows, self.more = requests_page(con, self.agent, self.everyone, before=self.oldest)
        finally:
            con.close()
        if not rows:
            return
        self.oldest = rows[0]["id"]
        pane = self.query_one("#transcript", VerticalScroll)
        was_height, was_y = pane.virtual_size.height, pane.scroll_y
        first = pane.children[0] if pane.children else None
        pane.mount(*(ChatMessage(m) for m in messages_of(rows, self.agent)), before=first)

        def keep_place() -> None:
            pane.scroll_to(y=was_y + pane.virtual_size.height - was_height,
                           animate=False, immediate=True)
        self.call_after_refresh(keep_place)

    def on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        pane = self.query_one("#transcript", VerticalScroll)
        if not self.showing_tasks and pane.scroll_y <= 1:
            self.load_older()

    def _before_cursor(self) -> tuple[str, str]:
        """The box's text before the cursor, and after it."""
        box = self.query_one("#compose", ChatBox)
        row, col = box.cursor_location
        lines = box.text.split("\n")
        row = min(row, len(lines) - 1)
        return ("\n".join(lines[:row] + [lines[row][:col]]),
                "\n".join([lines[row][col:]] + lines[row + 1:]))

    def update_menu(self) -> None:
        """Show what the text before the cursor could become, or hide the menu."""
        menu = self.query_one("#menu", OptionList)
        box_text = self.query_one("#compose", ChatBox).text
        if self.history_mode:
            hits = history_matches(box_text, self.history)
            found = [Suggestion("recall", h, h, ("❯ " + textsafe.oneline(h)[:90], ""))
                     for h in hits[:MENU_MAX]]
            menu.border_title = f"History {len(hits)}/{len(self.history)}"
        else:
            menu.border_title = ""
            found = [] if self._menu_closed else suggest(
                self._before_cursor()[0], self.tasks_pool, self.requests_pool, self.verbs_pool)
        self.suggestions = found
        if not found:
            menu.display = False
            return
        menu.clear_options()
        menu.add_options([Option(Text.assemble((sg.label[0], "bold"), (sg.label[1], "dim")), id=str(i))
                          for i, sg in enumerate(found)])
        menu.highlighted = 0
        menu.display = True

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        self._menu_closed = False
        if not self._recalling:
            self.hist_pos = None
        self._recalling = False
        self.update_menu()

    def action_history(self) -> None:
        """Ctrl+R: list what was sent before; what is typed narrows the list."""
        if self.showing_tasks:
            return
        self.history_mode = True
        self._menu_closed = False
        self.query_one("#compose", ChatBox).focus()
        self.update_menu()

    def _set_box(self, text: str, recalling: bool = False) -> None:
        box = self.query_one("#compose", ChatBox)
        self._recalling = recalling
        box.text = text
        box.move_cursor(box.document.end)

    def on_chat_box_recall(self, event: ChatBox.Recall) -> None:
        """Up and Down in the box: step back and forward through what was sent,
        keeping what was being typed until Down comes back to it."""
        event.stop()
        if not self.history:
            return
        if event.older:
            if self.hist_pos is None:
                self.hist_draft = self.query_one("#compose", ChatBox).text
                self.hist_pos = len(self.history) - 1
            elif self.hist_pos > 0:
                self.hist_pos -= 1
            self._set_box(self.history[self.hist_pos], recalling=True)
        elif self.hist_pos is not None:
            if self.hist_pos < len(self.history) - 1:
                self.hist_pos += 1
                self._set_box(self.history[self.hist_pos], recalling=True)
            else:
                self.hist_pos = None
                self._set_box(self.hist_draft, recalling=True)

    def on_text_area_selection_changed(self, event: TextArea.SelectionChanged) -> None:
        self.update_menu()

    def _highlighted(self) -> Suggestion | None:
        menu = self.query_one("#menu", OptionList)
        if not menu.display or menu.highlighted is None or menu.highlighted >= len(self.suggestions):
            return None
        return self.suggestions[menu.highlighted]

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.apply(self.suggestions[int(event.option.id)])

    def on_chat_box_complete(self, event: ChatBox.Complete) -> None:
        """Tab: put the highlighted suggestion in the box without doing it."""
        event.stop()
        sg = self._highlighted()
        if sg is None:
            return
        if sg.action == "insert":
            self._insert_ref(sg.arg)
            return
        if sg.action == "recall":
            self.apply(sg)
            return
        box = self.query_one("#compose", ChatBox)
        box.text = sg.tab
        box.move_cursor(box.document.end)

    def _insert_ref(self, number: str) -> None:
        before, after = self._before_cursor()
        m = _REF_TAIL.search(before)
        ref = number if number[:1] == "T" else f"#{number}"
        before = before[:m.start(1) - 1] + f"{ref} " if m else before
        box = self.query_one("#compose", ChatBox)
        box.text = before + after
        lines = before.split("\n")
        box.move_cursor((len(lines) - 1, len(lines[-1])))

    def apply(self, sg: Suggestion) -> None:
        """Do what the suggestion says: run the command, open the task, sort the
        table, or put the reference in the text."""
        if sg.action == "insert":
            self._insert_ref(sg.arg)
            return
        if sg.action == "recall":
            self.history_mode = False
            self._set_box(sg.arg)
            return
        self.query_one("#compose", ChatBox).text = ""
        if sg.action == "run":
            self.run_command(sg.arg)
        elif sg.action == "verb":
            self.run_verb(sg.arg)
        elif sg.action == "open":
            self.app.push_screen(TaskDetail(int(sg.arg)))
        elif sg.action == "sort":
            self.set_sort(sg.arg)
            self.query_one("#scope", Tabs).active = "tasks"

    def run_verb(self, label: str, arg: str = "") -> None:
        """The home page's key for `label`, on this page's agent: same asks, same job."""
        async def go() -> None:
            con = db.connect()
            try:
                await dispatch_agent_verb(self, con, self.agent, label, arg)
            finally:
                con.close()
        self.run_worker(go(), group="verb", exit_on_error=False)

    def _redraw(self) -> None:
        self.refresh_agent_state()

    def set_sort(self, column: str) -> None:
        self.query_one("#tasks_view").sort_named(column)

    def run_command(self, name: str) -> None:
        """Do the page command `name`: open the tab it names."""
        tab = next(t for n, _, t in PAGE_COMMANDS if n == name)
        if tab is None:
            self.leave()
            return
        self.query_one("#compose", ChatBox).text = ""
        self.query_one("#scope", Tabs).active = tab

    def leave(self) -> None:
        """Go back to the main screen, asking first when that would lose a draft
        or leave messages that are still being sent."""
        box = self.query_one("#compose", ChatBox)
        lost = []
        if box.text.strip():
            lost.append("the draft in the box")
        if self.sending:
            lost.append(f"{self.sending} message(s) still being sent")
        if not lost:
            self.app.pop_screen()
            return
        self.app.push_screen(Confirm("Go back to the main screen", "This leaves " + " and ".join(lost) + ".",
                                     yes="Go back", no="Stay"),
                             lambda yes: yes and self.app.pop_screen())

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        self.everyone = event.tab.id == "all"
        self.showing_tasks = event.tab.id == "tasks"
        self.showing_settings = event.tab.id == "settings"
        chat = not (self.showing_tasks or self.showing_settings)
        if chat:
            self.last_chat = event.tab.id
        self.oldest, self.more, self._sig = None, False, None
        self.reply_to(None)
        if self.is_mounted:
            self.query_one("#body").display = chat
            self.query_one("#dock").display = chat
            self.query_one("#tasks_view").display = self.showing_tasks
            self.query_one("#settings_view").display = self.showing_settings
            if self.showing_settings:
                self.load_settings()
                self.query_one("#s_model", Input).focus()
            else:
                (self.query_one("#compose", ChatBox) if chat else self.query_one("#tasks_table")).focus()
            self.refresh_view()
            if self.showing_tasks:
                self.query_one("#tasks_view").refresh_rows()

    def load_settings(self) -> None:
        """Fill the settings form from the agent's file."""
        try:
            self.settings = cfg = settings_of(self.agent)
        except (OSError, agents.ConfigError) as e:
            self.settings = None
            self.notify(f"cannot read this agent's settings: {safe(e)}", severity="error", timeout=10, markup=False)
            return
        for key in ("session_account", "vendor"):
            self.query_one(f"#s_{key}", Static).update(Text(safe(cfg[key])))
        self.query_one("#s_enabled", Static).update("yes" if cfg["enabled"] else "no")
        self.query_one("#s_model", Input).value = safe(cfg["model"])
        self.query_one("#s_repo", Input).value = safe(cfg["repo"])
        self.query_one("#s_context_k", Input).value = cfg["context_k"]
        self.query_one("#s_skip", Switch).value = cfg["skip_permissions"] == "on"
        self.show_context_share()
        self.query_one("#s_effort", Select).value = cfg["effort"] if cfg["effort"] in agents.VALID_EFFORT else "medium"
        self.query_one("#s_weight", Select).value = cfg["weight"] if cfg["weight"] in WEIGHTS else "light"

    def form(self) -> dict:
        return {"model": self.query_one("#s_model", Input).value,
                "effort": str(self.query_one("#s_effort", Select).value),
                "weight": str(self.query_one("#s_weight", Select).value),
                "repo": self.query_one("#s_repo", Input).value,
                "skip_permissions": "on" if self.query_one("#s_skip", Switch).value else "off",
                "context_k": self.query_one("#s_context_k", Input).value}

    def show_context_share(self) -> None:
        self.query_one("#s_context_share", Static).update(
            Text("= " + actions.context_share(self.query_one("#s_context_k", Input).value,
                                              self.settings["model_id"] or self.query_one("#s_model", Input).value)
                 if self.query_one("#s_context_k", Input).value else ""))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id in ("s_context_k", "s_model") and self.settings is not None:
            self.show_context_share()

    def save_form(self) -> None:
        """Save what the form changes, after showing exactly what that is."""
        if self.settings is None:
            return
        form = self.form()
        problem = settings_problem(form)
        if problem:
            self.notify(safe(problem), severity="error", timeout=10, markup=False)
            return
        changes = settings_changes(self.settings, form)
        if not changes:
            self.notify("nothing changed", timeout=5, markup=False)
            return
        detail = "\n".join(f"{k}:  {old or '(none)'}  →  {new}" for k, old, new in changes)

        def answered(yes: bool | None) -> None:
            log_action(f"settings {self.agent}: answered {'yes' if yes else 'no'} - {detail!r}")
            if not yes:
                return
            ok, said = save_settings(self.agent, changes)
            log_action(f"settings {self.agent}: {'ok' if ok else 'refused'} - {said}")
            skip = ok and any(k == "skip_permissions" for k, _, _ in changes)
            self.notify(safe(said) + ("\n(a running session is not restarted"
                                      + ("; skip prompts applies from its next launch)" if skip else ")") if ok else ""),
                        title=f"settings {self.agent}", severity="information" if ok else "error",
                        timeout=10, markup=False)
            self.load_settings()
        self.app.push_screen(Confirm(f"Change {self.agent}'s settings", detail,
                                     yes="Save", no="Cancel",
                                     danger=any(k == "skip_permissions" and new == "on" for k, _, new in changes)),
                             answered)

    def reply_to(self, msg: ChatMessage | None) -> None:
        for old in self.query(".replying"):
            old.remove_class("replying")
        self.replying = msg
        if msg is not None:
            msg.add_class("replying")
            self.query_one("#compose", ChatBox).focus()
        self.query_one("#status", Static).update(self._status())

    def action_back(self) -> None:
        menu = self.query_one("#menu", OptionList)
        if self.showing_tasks or self.showing_settings:
            self.query_one("#scope", Tabs).active = self.last_chat
        elif menu.display or self.history_mode:
            self._menu_closed = True
            self.history_mode = False
            menu.display = False
        elif self.replying is not None:
            self.reply_to(None)
        elif not self.query_one("#compose", ChatBox).text.strip():
            self.app.pop_screen()

    def on__text_box_send(self, event: _TextBox.Send) -> None:
        event.stop()
        box = self.query_one("#compose", ChatBox)
        body = box.text
        sg = self._highlighted()
        if sg is not None:
            self.apply(sg)
            return
        if not body.strip():
            return
        named = body.strip()[1:] if body.strip().startswith("/") else None
        if named in {n for n, _, _ in PAGE_COMMANDS}:
            self.run_command(named)
            return
        word, _, arg = (named or "").partition(" ")
        if word in CHAT_VERBS and (word == "move" or not arg.strip()):   # a refusal is toasted, never sent as a message
            box.text = ""
            self.run_verb(word, arg.strip())
            return
        rid = self.replying.m["rid"] if self.replying else None
        self.start_send(body, rid)

    def send_now(self, rid: int) -> None:
        def work() -> None:
            con = db.connect()
            try:
                said = actions.send_now(con, rid)
            finally:
                con.close()
            self.app.call_from_thread(self._sent_now, said)
        self.run_worker(work, thread=True, group="send_now", exit_on_error=False)

    def _sent_now(self, said: str) -> None:
        if self.is_attached:
            self.toast(said, not said.startswith("not sent"))
            self.refresh_conversation(force=True)

    def cancel_message(self, rid: int, body: str) -> bool:
        """Take request `rid` back and put its text in the box. Not yet
        delivered, nobody is told and it just goes; delivered, the agent is
        told to stop, so the plan is shown and asked first."""
        con = db.connect()
        try:
            ok, plan = actions.cancel_plan(con, rid)
            delivered = bool((db.get_request(con, rid) or {"delivered_at": None})["delivered_at"])
        finally:
            con.close()
        if not ok:
            self.notify(safe(plan), severity="error", timeout=10, markup=False)
            return False
        if delivered:
            self.app.push_screen(Confirm(f"Cancel #{rid}", plan, yes="Cancel it", no="Keep",
                                     danger=True), lambda yes: yes and self._cancel_sent(rid, body))
        else:
            self._cancel_sent(rid, body)
        return True

    def _restore(self, body: str) -> None:
        box = self.query_one("#compose", ChatBox)
        box.text = body
        box.move_cursor(box.document.end)
        box.focus()

    def _cancel_sent(self, rid: int, body: str) -> None:
        def work() -> None:
            con = db.connect()
            try:
                said = actions.cancel(con, rid)
            except Exception as e:  # noqa: BLE001
                said = f"not cancelled - {e}"
            finally:
                con.close()
            self.app.call_from_thread(self._cancelled, said, body)
        self.run_worker(work, thread=True, group="cancel", exit_on_error=False)

    def _cancelled(self, said: str, body: str) -> None:
        ok = not said.startswith("not cancelled")
        self.toast(said, ok)
        if ok:
            box = self.query_one("#compose", ChatBox)
            if not box.text.strip():
                self._restore(body)
        self.refresh_conversation(force=True)

    def start_send(self, body: str, rid: int | None) -> None:
        """Hand `body` to a worker thread and give the box back at once: a send
        can wait a long while on an agent that is off or restarting, and the
        screen must not wait with it. The message shows as pending until the
        worker answers; a refusal puts the text back in the box."""
        box = self.query_one("#compose", ChatBox)
        self.history = remember(self.history, body)
        save_history(self.history)
        self.hist_pos, self.history_mode = None, False
        box.text = ""
        self.reply_to(None)
        pending = ChatMessage({"who": "you", "when": 0, "rid": None, "part": 0, "side": "you",
                               "text": body, "waiting": False, "replyable": False,
                               "answered": False})
        pending.add_class("pending")
        pane = self.query_one("#transcript", VerticalScroll)
        pane.mount(pending)
        pane.scroll_end(animate=False)
        rec = Pending(pending, body)
        self.pending.append(rec)
        self.sending += 1
        self._spin()

        def work() -> None:
            with self._send_lock:
                con = db.connect()
                try:
                    ok, said = send_reply(con, self.agent, rid, body)
                except Exception as e:  # noqa: BLE001 - shown, never a crash of the screen
                    ok, said = False, f"not sent: {e}"
                finally:
                    con.close()
            self.app.call_from_thread(self.sent, ok, said, body, rec)
        self.run_worker(work, thread=True, group="send", exit_on_error=False)

    def sent(self, ok: bool, said: str, body: str, rec: Pending) -> None:
        self.sending -= 1
        if rec in self.pending:
            self.pending.remove(rec)
        if not self.is_attached:
            return
        rec.widget.remove()
        if not ok:      # a send that went says nothing: the message and its queue line show it
            self.toast(said, ok)
        if not ok:
            box = self.query_one("#compose", ChatBox)
            box.text = body if not box.text else body + "\n" + box.text
            box.move_cursor(box.document.end)
        self.query_one("#status", Static).update(self._status())
        self.refresh_conversation()

    SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def _spin(self) -> None:
        """Redraw the status line's spinner while a send is in flight."""
        self.query_one("#status", Static).update(self._status())
        if self.sending and not self._spinning:
            self._spinning = True
            self.set_timer(0.1, self._spin_tick)

    def _spin_tick(self) -> None:
        self._spinning = False
        self._frame += 1
        if self.sending:
            self._spin()


class ChatApp(ClipboardApp):
    CSS_PATH = Path(__file__).with_name("chat_page.tcss")

    def __init__(self, agent: str) -> None:
        super().__init__()
        self.agent = agent

    def on_mount(self) -> None:
        follow_os_theme(self)
        self.push_screen(ChatPage(self.agent))


if __name__ == "__main__":
    ChatApp(sys.argv[1] if len(sys.argv) > 1 else "orcapipe").run()
