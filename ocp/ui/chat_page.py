"""The new agent chat page, Textual widgets only. Run it alone with
`~/.orcapipe/venv/bin/python3 -m ocp.ui.chat_page [agent]`; the layout lives in
chat_page.tcss."""
from __future__ import annotations

import difflib
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
from ..actions.prompt_blocks import PERMISSION, QUESTION
from ..daemon import landing as landingmod
from ..team import agents, ask as askmod
from ..infra import paths, textsafe
from ..session import idle_close
from ..usage import status
from . import stall, tui
from .clipboard_app import ClipboardApp
from .theme import follow_os_theme
from ..infra import timefmt
from .components.ime import ImeInput
from .components.confirm import Confirm
from .components.dialog_modal import DialogModal
from .components.permission_modal import PermissionModal
from .components.question_modal import QuestionModal
from .components.jobs_aware import JobsAware, dispatch_agent_verb
from .components.ime import TextBox
from .components.text_prompt import TextPrompt
from .components.action_bar import ActionBar
from .components.page_header import page_header
from .components.section_head import SectionHead
from .components.schedules_section import SchedulesSection
from .components.task_assign import TaskAssign
from .components.switch_row import SwitchRow

# States of a request that never reached its target: withdrawn, replaced, denied.
# They are not part of the conversation, so no chat query reads them.
_GONE = "(" + ", ".join(repr(x) for x in db.NEVER_REACHED_TARGET) + ")"

# Requests read per page. The newest page is what opens; older ones are read when the
# chat is scrolled to within a screen of its top. Small, because drawing what is not in
# view is most of what a page costs (founder #5425).
PAGE = 10
WAITS = ("waiting for", "blocked by")      # what the work line says of an agent that waits on something

# T418: what the row under a message the agent has not answered says. Only a line
# its transcript shows is delivered; queued and typed both read sending (founder #5299).
LANDING_LABELS = {
    "queued": "sending",
    "typed": "sending",
    "delivered": "delivered, not answered yet",
}


def landing_label(landing: str, why: str = "") -> str:
    """The row under a message the agent has not answered; a message still sending says why."""
    label = LANDING_LABELS[landing]
    return f"{label} · {safe(why)}" if why and label == "sending" else label


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
    sql = (f"SELECT id, ts, state_at, requester, target, state, body, {db.ANSWER_SQL}, "
           f"{db.DISMISSED_SQL}, delivered_at, typed_at, landed_at, parent_id, kind, "
           "(SELECT group_concat(task_id) FROM task_requests WHERE request_id = requests.id) AS tasks, "
           "(SELECT t.title FROM tasks t JOIN task_requests q ON q.task_id = t.id "
           "WHERE q.request_id = requests.id ORDER BY t.id LIMIT 1) AS task_title, "
           "(SELECT MIN(task_id) FROM task_requests WHERE request_id = requests.id) AS task_id, "
           "(SELECT a.id FROM requests a WHERE a.settles = requests.id "
           "ORDER BY a.id LIMIT 1) AS answer_id "
           f"FROM requests WHERE state NOT IN {_GONE} AND settles IS NULL AND {where}")
    if since is not None:
        rows = con.execute(sql + " AND id >= ? ORDER BY id", (*params, since)).fetchall()
        return _with_task_state(con, rows), False
    if before is not None:
        sql += " AND id < ?"
        params = (*params, before)
    rows = con.execute(sql + " ORDER BY id DESC LIMIT ?", (*params, limit + 1)).fetchall()
    return _with_task_state(con, rows[:limit][::-1]), len(rows) > limit


def _with_task_state(con, rows) -> list[dict]:
    """The rows as dicts; a task delivery (the only row drawn as a card) also gets the state of
    its task, read live, not stored."""
    out = []
    for r in rows:
        d = dict(r)
        t = con.execute("SELECT * FROM tasks WHERE id = ?", (d["task_id"],)).fetchone() if d["task_id"] and d["kind"] == "task" else None
        d["task_state"] = actions.task_state(con, t) if t else None
        out.append(d)
    return out


# The task events that move a task to a state; the state `doing` is not stored, so the two
# events that put a task in its owner's hands stand for it.
_STATE_AFTER = {"draft_task": "draft", "queue_task": "queued", "task_begin": "doing", "task_start": "doing",
                "mark_task_done": "done", "close_task": "closed", "discard_task": "discarded",
                "reopen_task": "queued"}


def _edit_notes(payload: dict) -> list[str]:
    """What an `edit_task` event says, one note per field the chat shows."""
    notes = []
    if "owner" in payload:
        notes.append(f"assigned to {payload['owner']['new']}")
    if "title" in payload:
        notes.append("title changed")
    if "priority" in payload:
        notes.append(f"priority {payload['priority']['old']} -> {payload['priority']['new']}")
    return notes


def fold_handoffs(con, msgs: list[dict]) -> None:
    """A handoff delivers its task to the new owner, so it has both a delivery and a change. Draw it as
    ONE card: the delivery, as CHANGED TASK `handed to X` with the typed text, keeping its delivery state.
    NEW TASK stays for a task really created."""
    rids = [m["rid"] for m in msgs if is_card(m)]
    if not rids:
        return
    for e in con.execute("SELECT payload FROM events WHERE kind = 'task_handoff' AND "
                         f"json_extract(payload, '$.request_id') IN ({','.join('?' * len(rids))})", rids):
        p = json.loads(e["payload"])
        for m in msgs:
            if is_card(m) and m["rid"] == p["request_id"]:
                m["change"], m["note"] = f"handed to {p['to']}", p.get("note", "")


def task_changes(con, msgs: list[dict], agent: str = "") -> list[dict]:
    """The changes, since the first of `msgs`, of the tasks delivered in them, as entries for the
    chat: {change: "queued -> doing", who, side (as a message's, from `agent`'s page), note (the text
    typed with a handoff), tasks, task_title, when}. A task starts as queued."""
    cards = {m["tasks"][0]: m["task_title"] for m in msgs if is_card(m)}
    if not cards:
        return []
    folded = {m["rid"] for m in msgs if is_card(m) and "change" in m}
    since = min((m["when"] for m in msgs if m["when"]), default=0)
    marks = ",".join("?" * len(cards))
    kinds = (*_STATE_AFTER, "task_handoff", "edit_task")
    events = con.execute(
        f"SELECT ts, agent, kind, payload, {db.EVENT_TASK} AS tid FROM events "
        f"WHERE kind IN ({','.join('?' * len(kinds))}) "
        f"AND {db.EVENT_TASK} IN ({marks}) ORDER BY id", (*kinds, *cards)).fetchall()
    steps = []      # (time, task, who, the state it reaches or None, what to say instead, the note with it)
    for e in events:
        if e["kind"] in _STATE_AFTER:
            steps.append((e["ts"], e["tid"], e["agent"], _STATE_AFTER[e["kind"]], None, ""))
            continue
        payload = json.loads(e["payload"])
        if e["kind"] == "task_handoff" and payload.get("request_id") in folded:
            continue        # drawn by its delivery card
        notes = [f"handed to {payload['to']}"] if e["kind"] == "task_handoff" else _edit_notes(payload)
        said = payload.get("note", "") if e["kind"] == "task_handoff" else ""
        steps += [(e["ts"], e["tid"], e["agent"], None, note, said) for note in notes]
    for m in msgs:      # every delivery leaves when the task reached its owner, which is when it became doing
        if is_card(m) and m["landing"] == "delivered" and m["when"]:
            steps.append((m["when"], m["tasks"][0], m["to"], "doing", None, ""))
    state: dict[int, str] = {}
    out = []
    for ts, tid, who, after, note, said in sorted(steps, key=lambda step: step[0]):
        if after is not None:
            before = state.get(tid, "queued")
            state[tid] = after
            if before == after:
                continue
            note = f"{before} -> {after}"
        if ts >= since:
            side = "you" if who == actions.HUMAN else "agent" if who == agent else "other"
            out.append({"change": note, "note": said, "who": who, "side": side, "tasks": [tid],
                        "task_title": cards[tid], "when": ts})
    return out


def with_task_changes(msgs: list[dict], changes: list[dict]) -> list[dict]:
    """The messages with each change of a task set among them at its time; a message still waiting to
    go stays last."""
    sent = [m for m in msgs if not m["unread"]]
    waiting = [m for m in msgs if m["unread"]]
    out: list[dict] = []
    later = sorted(changes, key=lambda c: c["when"])
    for m in sent:
        while later and later[0]["when"] < (m["when"] or 0):
            out.append(later.pop(0))
        out.append(m)
    return out + later + waiting


def signature(con, agent: str, everyone: bool) -> tuple:
    """A cheap reading that changes whenever something in the chat does: a new
    request, or a request answered, closed or changed."""
    where, params = _scope(agent, everyone)
    row = con.execute(
        f"SELECT COUNT(*), COALESCE(MAX(id), 0), COALESCE(MAX({db.touched_sql('requests')}), 0), "
        "COALESCE(MAX(delivered_at), 0), COUNT(delivered_at), COALESCE(MAX(landed_at), 0), "
        "COUNT(landed_at), COALESCE(MAX(typed_at), 0), "
        f"COALESCE(SUM(state = 'queued'), 0) FROM requests WHERE state NOT IN {_GONE} AND {where}",
        params).fetchone()
    events = con.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM events WHERE kind IN "
                         f"({','.join('?' * len(_STATE_AFTER))})", tuple(_STATE_AFTER)).fetchone()
    return (*row, *events)


def _col(r, name: str):
    """A column of a page row, None when the row was read without it."""
    return r[name] if name in r.keys() else None


def _answer_id(r) -> int | None:
    """The number of the answer's own message, when the row was read with it."""
    return _col(r, "answer_id")


def messages_of(rows, agent: str) -> list[dict]:
    """One dict per message of these requests, in time order: who, when, text,
    the request it belongs to (`rid`), its own number (`mid`: the request's for an
    ask, the answer row's for a verdict), whether it is the ask (part 0) or the verdict
    (part 1), which side said it, and whether the human can reply to it. Nothing here tracks
    whether a message was answered (founder #2659)."""
    human = actions.HUMAN
    msgs = []
    for r in rows:
        shown = db.shown_verdict(r)
        for part, speaker, to, text, at in (
                (0, r["requester"], r["target"], r["body"], r["ts"]),
                (1, r["target"], r["requester"], shown, db.answered_at(r))):
            cancelled = r["state"] in ("landed", "withdrawn") and shown == askmod.CANCEL_VERDICT
            if part == 1 and (not shown or cancelled or db.dismissal(r)[0]):
                continue
            side = "you" if speaker == human else "agent" if speaker == agent else "other"
            queued = (part == 0 and speaker == human and r["target"] != human
                      and r["state"] == "queued")
            held = (part == 0 and speaker != human and r["target"] != human
                    and r["state"] == "queued" and not r["delivered_at"])
            landing = landingmod.state(r)
            # last, marked unread: only what this page's agent has yet to read; what it sent
            # (or two others') sits at its own time
            waiting_to_go = held and r["target"] == agent or queued and landing != "delivered"
            if part == 0 and r["landed_at"]:
                at = r["landed_at"]      # a message sits where its agent read it, not where it queued
            who = ("you" if speaker == human else speaker) + (
                "" if human in (speaker, to) else f" → {to}")
            msgs.append({"who": who, "when": at or 0, "rid": r["id"], "part": part, "side": side,
                         "mid": r["id"] if part == 0 else _answer_id(r),
                         "text": (text or "").strip("\n") or "(empty)",
                         "replyable": True,      # any message, also one between two other agents in All chat
                         "queued": queued,
                         "held": held,
                         "unread": waiting_to_go,
                         "to": to,
                         "kind": _col(r, "kind"),
                         "tasks": sorted(int(t) for t in (_col(r, "tasks") or "").split(",") if t),
                         "task_title": _col(r, "task_title"), "task_state": _col(r, "task_state"),
                         "delivered": landing == "delivered",
                         "landing": landing,
                         "reply_to": _col(r, "parent_id") if part == 0 else r["id"] if speaker == human else None,   # the founder's reply is linked
                         "cancelled": part == 0 and cancelled,
                         "_key": (float("inf") if waiting_to_go else at or 0, r["id"], part)})
    msgs.sort(key=lambda m: m["_key"])
    for m in msgs:
        del m["_key"]
    return msgs


def reply_link(parent: int) -> Text:
    """A reply icon and the number of what it answers, ahead of the reply's text; a click goes there."""
    return Text(f"↩ #{parent}", style=Style(dim=True, underline=True, meta={"@click": f"screen.show_message({parent})"}))


def mid_of(m: dict) -> int:
    """The number a message goes by: its own row's (an answer has one), else its request's."""
    return m.get("mid") or m["rid"]


def tasks_of(con, agent: str) -> list[tuple]:
    """The tasks not yet done or discarded that `agent` owns or opened, as (id,
    role, state, title, the other party, last update). A task it opened for
    itself is owned. The last update is the latest of its creation, edit, done
    and discard, and the newest time on any request linked to it."""
    rows = con.execute(
        f"SELECT * FROM tasks WHERE {db.OPEN_SQL} AND (owner = ? OR opened_by = ?) ORDER BY id",
        (agent, agent)).fetchall()
    touched = {r[0]: r[1] for r in con.execute(
        f"SELECT l.task_id, MAX(COALESCE(MAX(r.ts), 0), COALESCE(MAX({db.touched_sql('r')}), 0)) "
        "FROM task_requests l JOIN requests r ON r.id = l.request_id "
        f"WHERE l.task_id IN (SELECT id FROM tasks WHERE {db.OPEN_SQL} AND "
        "(owner = ? OR opened_by = ?)) GROUP BY l.task_id", (agent, agent))}
    out = []
    for t in rows:
        own = t["owner"] == agent
        updated = max(t["created_at"] or 0, t["edited_at"] or 0, t["done_at"] or 0,
                      t["discarded_at"] or 0, touched.get(t["id"], 0))
        out.append((t["id"], "owns" if own else "opened", actions.task_state(con, t),
                    t["title"], t["opened_by"] if own else t["owner"], updated))
    return out


def task_detail(con, tid: int) -> dict | None:
    """A task as what its page shows: its own fields, the sub-tasks and
    requests tied to it, and which of reopen and discard the human may do now.
    None when there is no such task."""
    t = db.get_task(con, tid)
    if t is None:
        return None
    state = actions.task_state(con, t)
    mine = actions.task_asker(con, t) == actions.HUMAN
    kids = [(c["id"], c["title"], actions.task_state(con, c)) for c in
            con.execute("SELECT * FROM tasks WHERE parent_task = ? ORDER BY id", (tid,)).fetchall()]
    parent = db.get_task(con, t["parent_task"]) if t["parent_task"] else None
    brief = ((db.first_linked_request(con, tid) or {"body": ""})["body"] or "").strip()      # the one `edit_task` edits
    if brief.split("\n", 1)[0].strip() == t["title"].strip():
        brief = brief.split("\n", 1)[1].strip("\n") if "\n" in brief else ""      # the title is drawn above
    return {"id": tid, "title": t["title"], "brief": brief, "state": state, "owner": t["owner"],
            "opened_by": t["opened_by"], "created_at": t["created_at"],
            "parent": (t["parent_task"], parent["title"]) if parent else None,
            "done": (t["done_evidence"], t["done_at"]) if t["done_evidence"] else None,
            "discarded": (t["discarded_by"], t["discard_reason"], t["discarded_at"]) if t["discarded_by"] else None,
            "merged_into": t["merged_into"],
            "brief_text": actions.task_brief(con, tid),
            "children": kids, "timeline": actions.task_timeline(con, tid), "asker": actions.task_asker(con, t),
            "can_done": state in ("draft", "queued", "doing"),
            "can_reopen": state == "done" and mine,
            "can_discard": state in ("draft", "queued", "doing"),
            "can_assign": state in ("draft", "queued", "doing")}


EDITABLE = ("model", "effort", "weight", "repo", "skip_permissions", "context_k", "idle_close")
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
            "context_k": str(agents.context_tokens(cfg) // 1000),
            "idle_close": actions.idle_close_minutes(cfg["park_timeout_s"])
            if isinstance(cfg.get("park_timeout_s"), int) and not isinstance(cfg.get("park_timeout_s"), bool) else ""}


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
    if form["idle_close"].strip() and not form["idle_close"].strip().isdigit():
        return "idle close must be a whole number of minutes (0 = never), or blank to follow the global time"
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
    idle = new.pop("idle_close", None)
    if idle is not None:
        try:
            actions.parse_idle_close(idle)      # a bad one is refused before the file is written
        except ValueError as e:
            return False, f"not saved: {e}"
    if new:
        try:
            agents.register(agent, model=new.get("model"), effort=new.get("effort"),
                            weight=new.get("weight"), repo=new.get("repo"),
                            skip_permissions=None if "skip_permissions" not in new else new["skip_permissions"] == "on",
                            context_tokens=int(new["context_k"]) * 1000 if "context_k" in new else None)
        except (ValueError, agents.ConfigError, OSError) as e:
            return False, f"not saved: {e}"
    if idle is not None:
        ok, said = actions.set_idle_close(idle, agent)
        if not ok:
            done = ", ".join(f"{k} {v}" for k, v in new.items())
            return False, f"idle close not saved: {said}" + (f" ({done} was saved)" if done else "")
    return True, "saved: " + ", ".join(f"{k} {v}" for k, _, v in changes)


def send_reply(con, agent: str, rid: int | None, body: str) -> tuple[bool, str]:
    """Send `body` as a reply to request `rid`, or as a plain message when `rid`
    is None. Inside a decision card the reply is the decision, and inside a
    legacy question to the human it is that request's verdict (both
    `actions.answer_ok`); an answer to the human's own request gets a new request
    and is marked read; anything else is a new request under the row, to the agent
    on it - its target if the human asked it, else its requester - which in All
    chat is not always the agent whose page this is."""
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
    ok, msg, _ = askmod.say(other, body, requester=actions.HUMAN, parent_id=rid)
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


_REF = re.compile(r"(?<![\w#])#(\d+)\b")


def wait_reason(con, agent: str, msgs: list[dict], running: bool) -> str:
    """Why the first unread message waits: the human's own that is not typed yet
    (unless the spinner already says it), else one to `agent` from another agent, which waits
    on its recipient. What the agent sent is not waiting."""
    waiting = [m for m in msgs if m["queued"] and m["landing"] == "queued"]
    held = [m for m in msgs if m["held"] and m["unread"]]
    if waiting and not running:
        return actions.queue_reason(con, agent, waiting[0]["rid"])
    if not waiting and held:
        return f"{held[0]['to']}: {actions.queue_reason(con, held[0]['to'], held[0]['rid'])}"
    return ""


def link_refs(text: Text) -> Text:
    """Every `#<id>` in `text` becomes a link to that message on the page (a click runs
    `show_message`), so a reply that names a number can be followed to it."""
    for hit in _REF.finditer(text.plain):
        text.stylize(Style(underline=True, bold=True, meta={"@click": f"screen.show_message({hit[1]})"}),
                     hit.start(), hit.end())
    return text


def link_paths(text: Text, who: str = "") -> Text:
    """Every file path in `text` becomes a link (a click runs `app.open_path`: the file opens in the
    system's default app). A relative path is read against `who`'s worktree and the repo root and
    linked only if it is there; an absolute or `~/` one is linked even if it is not, and the click says so."""
    for hit in actions.find_paths(text.plain, actions.path_bases(who) if who else ()):
        text.stylize(Style(underline=True, meta={"@click": f"app.open_path({hit.path!r})"}), hit.start, hit.end)
    return text


_TASK_REF = re.compile(r"(?<![\w#])T(\d+)\b")


def link_task_refs(text: Text) -> Text:
    """Every `T<n>` in `text` becomes a link that opens that task (a click runs `open_task`)."""
    for hit in _TASK_REF.finditer(text.plain):
        text.stylize(Style(underline=True, meta={"@click": f"screen.open_task({hit[1]})"}), hit.start(), hit.end())
    return text


def is_card(m: dict) -> bool:
    """A task delivery is drawn as the task page's card; every other message is a row."""
    return m.get("part") == 0 and m.get("kind") == "task" and bool(m.get("tasks"))


def card_head(m: dict) -> Text:
    """Above a task card, as over a message: who made the change and when."""
    stamp = timefmt.stamp(m["when"], timefmt.CLOCK) if m.get("when") else ""
    who = "you" if m["who"] == actions.HUMAN else safe(m["who"])
    return Text(f"{who}  {stamp}", style="bold")


def card_lines(m: dict) -> Text:
    """A task in the chat, inside its box: NEW TASK (or CHANGED TASK) T<n> and the title on one line
    (cut to the room, and a click opens the task), then what happened - a new task says whom it went
    to, a change says what changed, and the text typed with a handoff."""
    tid = m["tasks"][0]
    go = {"@click": f"screen.open_task({tid})"}
    new = "change" not in m
    out = Text(no_wrap=True, overflow="ellipsis")
    out.append("NEW " if new else "CHANGED ", style="bold green" if new else "bold yellow")
    out.append("TASK ", style="dim")
    out.append(actions.task_ref(tid), style=Style(bold=True, underline=True, meta=go))
    out.append(" : ", style="dim")
    out.append(safe(m.get("task_title") or ""), style=Style(bold=True, meta=go))
    said = m.get("change") or f"assigned to {safe(m['to'])}"
    out.append("\n" + said, style=STATE_STYLE.get(said.rsplit(" ", 1)[-1], ""))
    if m.get("note"):
        out.append("\n")
        out.append_text(link_paths(Text(safe_block(m["note"])), m["who"]))
    return out


def message_text(m: dict, head_shown: bool, picked: bool, side: str, colors: dict | None = None) -> Text:
    """Everything a message draws but its send row, as one text (a widget each was most of
    the cost of drawing a chat): the reply link, the task card's top and title, the head line
    when it is shown (with the #id when picked), the body and the note."""
    head, body, note = message_parts(m)
    out = Text()
    if is_card(m):
        return card_lines(m)
    if head_shown:
        head = head.copy()
        colour = (colors or {}).get({"them": "success", "other": "text-muted"}.get(side, ""), "")
        if colour:
            head.stylize(colour)
        if picked:
            head.append(f"  #{mid_of(m)}", style="bold")
        out.append_text(head)
        out.append("\n")
    if m.get("reply_to"):
        out.append_text(reply_link(m["reply_to"]))
        out.append(" ")
    out.append_text(body)
    if note is not None:
        out.append("\n")
        out.append_text(Text(note.plain, style="dim"))
    return out


def message_parts(m: dict) -> tuple[Text, Text, Text | None]:
    """What one message draws: its head line (who, when, its kind and linked tasks), its body, and
    the note under it (cancelled), all as safe `Text`."""
    body = link_paths(link_refs(Text(safe_block(m["text"]), style="strike dim" if m.get("cancelled") else "")), m["who"])
    stamp = timefmt.stamp(m["when"], timefmt.CLOCK) if m["when"] else ""
    head = Text(f"{safe(m['who'])}  {stamp}", style="bold")
    if m.get("part") == 0 and m.get("kind") not in (None, "say") and not is_card(m):
        head.append("  " + safe(m["kind"]).replace("_", " "), style="dim")
        for t in m.get("tasks", ()):
            head.append(f"  {actions.task_ref(t)}", style="bold cyan")
    note = None
    if m.get("cancelled"):
        note = Text("cancelled" + (" - the agent was told to stop" if m.get("delivered") else ""))
    return head, body, note


# What a timeline entry is called, the icon before it and its colour: the label and the text of a memo
# and of a request differ at a glance, and a request's state has a colour of its own.
TIMELINE_WORD = {"memo": "memo", "handoff": "handed", "request": "request", "edit": "body edited",
                 "state": "state"}
TIMELINE_ICON = {"memo": "✎", "request": "↗", "handoff": "⇄", "edit": "±", "state": "◆"}
TIMELINE_STYLE = {"memo": "cyan", "request": "magenta", "handoff": "yellow", "edit": "blue",
                  "state": "green"}
REQUEST_STATE_STYLE = {"queued": "bold yellow", "landed": "bold green", **dict.fromkeys(db.NEVER_REACHED_TARGET, "dim")}


def timeline_text(e: dict) -> Text:
    """One entry of a task's timeline: when, what kind (with its icon), who and its text, the kind and
    the text in the kind's colour. A text that fits the line stays on it; one of several lines (or
    too long for one) shows whole, each line indented under the first. A memo that was edited says when."""
    kind = e["kind"]      # a kind not listed (a new one) is drawn plain, never an error
    colour = TIMELINE_STYLE.get(kind, "")
    out = Text.assemble((timefmt.stamp(e["ts"], timefmt.DATE_TIME) + "  ", "dim"),
                        (f"{TIMELINE_ICON.get(kind, '•')} {TIMELINE_WORD.get(kind, safe(kind))}  ", f"bold {colour}".strip()),
                        safe(e["who"]) + "  ")
    if kind == "request":      # its number, state and first line
        out.append(f"{e['ref']}  ", style="dim")
        out.append(f"{e['state']}  ", style=REQUEST_STATE_STYLE.get(e["state"], "bold green"))
        out.append(safe(e["summary"]), style=colour)
        lines = []
    else:
        lines = safe_block(e["text"].strip()).split("\n") or [""]
        out.append(lines[0], style=colour)
    for more in lines[1:]:
        out.append("\n    " + more, style=colour)
    if e["edited_at"]:
        out.append(f"  · edited {timefmt.stamp(e['edited_at'], timefmt.DATE_TIME)}", style="dim")
    return link_paths(out, e["who"])


def task_page(d: dict) -> dict:
    """What a task's page draws, as safe `Text`: title, badge, the facts, the
    sub-tasks (each a link that opens it) and the timeline."""
    def link(tid: int, label: str) -> Text:
        return Text(label, style=Style(underline=True, meta={"@click": f"open_task({tid})"}))

    def when(ts) -> Text:
        return Text(timefmt.stamp(ts, timefmt.DATE_TIME) if ts else "", style="dim")

    facts: list[tuple[str, Text]] = [
        ("owner", Text(safe(d["owner"]))),
        ("opened by", Text.assemble(safe(d["opened_by"]) + "  ", when(d["created_at"])))]
    if d["parent"]:
        facts.append(("parent", link(d["parent"][0], f"{actions.task_ref(d['parent'][0])}  {safe(d['parent'][1])}")))
    if d["done"]:
        facts.append(("done", Text.assemble(safe(d["done"][0]) + "  ", when(d["done"][1]))))
    if d["discarded"]:
        by, why, at = d["discarded"]
        facts.append(("discarded", Text.assemble(safe(by) + "  ", when(at))))
        if d["merged_into"]:
            facts.append(("merged into", link(d["merged_into"], actions.task_ref(d["merged_into"]))))
        elif why:
            facts.append(("reason", Text(safe(why))))
    facts.append(("asked by", Text(safe(d["asker"]))))
    meta = Text()
    for label, value in facts:
        meta.append(f"{label:<11}", style="dim")
        meta.append_text(value)
        meta.append("\n")
    badge = Text(f" {d['state']} ", style=STATE_STYLE.get(d["state"], ""))
    badge.append(f"   {actions.task_ref(d['id'])}", style="dim")
    return {
        "title": Text(safe(d["title"]), style="bold"), "badge": badge, "meta": meta,
        "brief": link_paths(Text(safe_block(d["brief"])), d["owner"]) if d.get("brief") else Text(),
        "kids": [Text.assemble(link(i, f"{actions.task_ref(i)}  "), (f"{st:<8}", STATE_STYLE_FG.get(st, "")),
                               link(i, safe(title))) for i, title, st in d["children"]],
        "timeline": [timeline_text(e) for e in d["timeline"]]}


class TaskChange(Vertical):
    """A change to a task, set among the messages at the time it happened."""

    def __init__(self, m: dict) -> None:
        side = {"you": "me", "agent": "them"}.get(m["side"], "other")
        super().__init__(Static(card_head(m), classes="cardhead"), Static(card_lines(m), classes="cardbox"),
                         classes=f"msg {side} card change")
        self.m = m


class WorkLine(Static):
    """The spare row under the last message: a spinner while the agent is working, and what
    it waits on when idle."""

    SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, working: bool, wait: str = "") -> None:
        super().__init__(classes="workline")
        self.working, self.wait, self._frame = working, wait, 0

    def on_mount(self) -> None:
        self.set_interval(0.1, self._tick)

    def _tick(self) -> None:
        if self.working:
            self._frame += 1
            self.refresh()

    def show(self, working: bool, wait: str) -> None:
        self.working, self.wait = working, wait
        self.refresh()

    def render(self) -> Text:
        mid = f" {self.SPINNER[self._frame % len(self.SPINNER)]} working " if self.working \
            else f" {safe(self.wait)} " if self.wait else ""
        return link_task_refs(Text(mid, style="dim"))


def transcript_widgets(msgs: list[dict], working: bool, why: str = "", wait: str = "") -> list:
    """The messages as widgets, then a spare row that shows the spinner or what the agent waits
    on. A message not yet read says it is sending itself; the first of them also says why."""
    out: list = []
    first = True
    for m in msgs:
        if "change" in m and not is_card(m):
            out.append(TaskChange(m))
            continue
        out.append(ChatMessage(m, working, why if m["unread"] and first else ""))
        first = first and not m["unread"]
    out.append(WorkLine(working, wait=wait))
    return out


class ChatMessage(Vertical):
    """One message; a click makes it what the next thing typed replies to. Its send
    now button is shown only while the agent is `working` (mid-turn): with the agent
    idle the message goes on its own."""

    def __init__(self, m: dict, working: bool = True, why: str = "") -> None:
        side = {"you": "me", "agent": "them"}.get(m["side"], "other")
        self._side = side
        # the human's own message tells who and when only once picked
        self._always = m["side"] != "you"
        self._picked = False
        self._colors: dict[str, str] = {}
        card = is_card(m)
        self._text = Static(message_text(m, self._always, False, side), classes="body cardbox" if card else "body")
        parts = [Static(card_head(m), classes="cardhead"), self._text] if card else [self._text]
        self._send_now: Button | None = None
        landing = m.get("landing") or ("delivered" if m.get("delivered") else "queued")
        self._qstate = Static(landing_label(landing, why), classes="qstate")
        self._landing = landing
        if m.get("queued") or m.get("held"):
            buttons = []
            if landing == "queued" and m.get("queued"):
                self._send_now = Button("⚡ send now", classes="sendnow", compact=True)
                buttons = [self._send_now, Button("✕ cancel", classes="cancel", compact=True)]
            parts.append(Horizontal(
                self._qstate,
                *buttons, classes="qrow"))
        super().__init__(*parts, classes=f"msg {side}"
                         + (" card" if is_card(m) else ""))
        self.m = m
        self.set_working(working)

    def on_mount(self) -> None:
        """The head line takes the theme's colours, which a widget can only read once mounted."""
        variables = self.app.theme_variables
        self._colors = {"success": variables.get("success", ""), "text-muted": "dim"}   # the muted one is 'auto 60%' in CSS
        self._redraw()

    def _redraw(self) -> None:
        self._text.update(message_text(self.m, self._always or self._picked, self._picked, self._side, self._colors))

    def show_id(self, on: bool) -> None:
        """A picked message tells its #id, and the human's own tells who and when too."""
        self._picked = on
        self._redraw()

    def set_why(self, why: str) -> None:
        self._qstate.update(landing_label(self._landing, why))

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
        if self.screen.selections:   # the end of a drag: picking would redraw the head line under the selection
            return
        if self.m["replyable"]:
            self.screen.reply_to(None if self.screen.replying is self else self)


# Slash commands of the page itself: name, what it does, the tab it opens.
PAGE_COMMANDS = (("chat", "My chat", "mine"), ("all", "All chat", "all"), ("task", "Tasks", "tasks"),
                 ("sort", "Sort the tasks", "tasks"), ("settings", "Agent settings", "settings"),
                 ("back", "Back to the main screen", None), ("help", "List the commands", "help"),
                 ("exit", "Quit the bridge", "exit"))


MENU_MAX = 8
VERB_WHAT = {'terminal': 'go to its terminal', 'wake': 'wake it now', 'close': 'close its session',
             'park': 'park it', 'fold': 'fold it into its handoff', 'move': 'move it to another account'}

# The home page's agent keys that make sense typed in the agent's own chat: `open` is this page,
# and `message` and `editor` are the box itself.
CHAT_VERBS = ('terminal', 'wake', 'close', 'park', 'fold', 'move')


def slash_action(body: str) -> tuple | None:
    """What a box text starting with `/` asks for, never a message to the agent: None when it is
    not a command line; ("run", name), ("verb", word, arg), ("open", task id), ("sort", column);
    or ("error", why) for a name nothing answers to or an argument it cannot take."""
    text = body.strip()
    if not text.startswith("/"):
        return None
    word, _, arg = text[1:].partition(" ")
    arg = arg.strip()
    names = {n for n, _, _ in PAGE_COMMANDS}
    if word in names and (not arg or word not in ("task", "sort")):
        return ("error", f"/{word} takes no argument") if arg else ("run", word)
    if word == "task":
        tid = arg.lstrip("#").lstrip("tT")
        return ("open", int(tid)) if tid.isdigit() else ("error", "/task needs a task number, like /task 12")
    if word == "sort":
        return ("sort", arg) if arg in actions.TASK_SORTS else ("error", "/sort needs a column: " + ", ".join(actions.TASK_SORTS))
    if word in CHAT_VERBS:
        return ("error", f"/{word} takes no argument") if arg and word != "move" else ("verb", word, arg)
    return ("error", f"no command /{word} - /help lists them" if word else "type a command after the /, /help lists them")


_REF_TAIL = re.compile(r"(?:^|\s)#(\d*)$")


HISTORY_MAX = 100


def history_file() -> Path:
    return paths.home() / "chat_history.json"


def scope_file() -> Path:
    return paths.home() / "chat_scope"


def load_scope() -> str:
    """The chat tab the human last picked, "mine" or "all"; anything else is "mine"."""
    try:
        return "all" if scope_file().read_text().strip() == "all" else "mine"
    except OSError:
        return "mine"


def save_scope(tab: str) -> None:
    try:
        scope_file().parent.mkdir(parents=True, exist_ok=True)
        scope_file().write_text(tab)
    except OSError:
        pass


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


def _history_file_data() -> "list | dict | None":
    try:
        return json.loads(history_file().read_text())
    except (OSError, ValueError):
        return None


def _strings(data) -> list[str]:
    return [x for x in data if isinstance(x, str)][-HISTORY_MAX:] if isinstance(data, list) else []


def load_history(room: str) -> list[str]:
    """What was typed and sent in `room` (an agent's chat), oldest first, at most HISTORY_MAX. The
    one shared list the file held before rooms were kept apart is not read. A missing or
    unreadable file is an empty history, never an error."""
    data = _history_file_data()
    rooms = data.get("rooms") if isinstance(data, dict) else None
    return _strings(rooms.get(room) if isinstance(rooms, dict) else None)


def remember(history: list[str], text: str) -> list[str]:
    """`history` with `text` as the newest entry: an earlier copy of it moves
    up instead of repeating, and only the newest HISTORY_MAX are kept."""
    text = text.strip("\n")
    if not text.strip():
        return history
    return ([h for h in history if h != text] + [text])[-HISTORY_MAX:]


def save_history(room: str, history: list[str]) -> None:
    """`room`'s history, keeping every other room's."""
    data = _history_file_data()
    rooms = data.get("rooms") if isinstance(data, dict) and isinstance(data.get("rooms"), dict) else {}
    try:
        history_file().parent.mkdir(parents=True, exist_ok=True)
        history_file().write_text(json.dumps({"rooms": {**rooms, room: history}}, ensure_ascii=False))
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


class ChatBox(TextBox):
    """The chat box. While the command menu is up, Up and Down move in it and
    Tab takes the highlighted command; everything else is `TextBox`'s."""

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


STATE_STYLE_FG = {"draft": "dim", "queued": "yellow", "doing": "blue", "done": "green", "discarded": "dim"}
STATE_STYLE = {"draft": "white on grey30", "queued": "black on yellow", "doing": "white on blue",
               "done": "black on green", "discarded": "white on grey37"}


class TaskActions:
    """Reopen and discard for the task `self.tid` (its `task_detail` in `self.d`): the
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

    def action_assign_task(self) -> None:
        if not (self.d and self.d["can_assign"]):
            self.say("a finished task has nothing left to answer or assign")
            return
        ref = actions.task_ref(self.tid)
        stall.snapshot(self.app, f"assign {ref}: open requested")

        def answered(got: "tuple[str, str] | None") -> None:
            stall.crumb(f"assign {ref}: answered {'cancelled' if got is None else 'to ' + got[0]}")
            if got is None:
                return
            to, text = got
            log_action(f"task {ref} assign to {to}")
            with stall.timed(f"assign {ref}: db.connect"):
                con = db.connect()
            try:
                with stall.timed(f"assign {ref}: actions.assign_task"):
                    ok, said = actions.assign_task(con, self.tid, to, text)
            finally:
                con.close()
            self.report("assign", ok, said)
            stall.crumb(f"assign {ref}: callback end ({'ok' if ok else 'refused'})")
            self.app.call_after_refresh(stall.snapshot, self.app, f"assign {ref}: after the callback")
        with stall.timed(f"assign {ref}: db.connect"):
            con = db.connect()
        try:
            with stall.timed(f"assign {ref}: actions.recent_assignees"):
                recent = actions.recent_assignees(con, self.tid)
        finally:
            con.close()
        with stall.timed(f"assign {ref}: paths.list_agents"):
            agents_here = paths.list_agents()
        with stall.timed(f"assign {ref}: TaskAssign built and pushed"):
            self.app.push_screen(TaskAssign(ref, self.d["owner"], agents_here, recent), answered)

    def action_done_task(self) -> None:
        if not (self.d and self.d["can_done"]):
            self.say("only a task that is not done can be marked done")
            return

        def answered(reason: str | None) -> None:
            log_action(f"task {actions.task_ref(self.tid)} done: reason {'given' if reason and reason.strip() else 'not given'}")
            if reason and reason.strip():
                con = db.connect()
                try:
                    ok, said = actions.mark_task_done(con, self.tid, actions.HUMAN, reason=reason.strip())
                finally:
                    con.close()
                self.report("done", ok, said)
        self.app.push_screen(TextPrompt("done - what came of it?"), answered)

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
            self.say("only a task that is not done can be discarded")
            return

        doing = self.d["state"] == "doing"

        def answered(reason: str | None) -> None:
            log_action(f"task {actions.task_ref(self.tid)} discard: reason {'given' if reason and reason.strip() else 'not given'}")
            if reason and reason.strip():
                con = db.connect()
                try:
                    ok, said = actions.discard_task(con, self.tid, actions.HUMAN, reason, stop=doing)
                finally:
                    con.close()
                self.report("discard", ok, said)
        self.app.push_screen(TextPrompt("stop it and discard - why?" if doing else "discard - why?"), answered)


class TaskText(Static):
    """A `Static` on a task's page whose links open another task's page, wherever the page is."""

    def action_open_task(self, tid: int) -> None:
        self.app.push_screen(TaskDetail(tid))


class TaskBody(TaskActions, Vertical):
    """One task as the human sees it: title, state, the facts, sub-tasks, requests, the timeline
    with a memo box, and the calls on it. The task page and the side view of the task list are
    both this, so what one can do the other can. `show(tid)` draws a task; the host redraws
    its own lists when `Changed` arrives."""

    DEFAULT_CSS = """
    TaskBody { height: 1fr; }
    TaskBody #detail_pane { height: 1fr; padding: 1 3; }
    TaskBody .tl_row { height: auto; }
    TaskBody .tl_text { width: 1fr; height: auto; }
    TaskBody .tl_icon { width: 3; min-width: 0; margin-left: 1; }
    TaskBody #t_memo_new { height: 1; margin-top: 1; }
    TaskBody #t_memo_box { width: 1fr; }
    TaskBody #t_memo_new Button { width: auto; min-width: 0; margin-left: 1; }
    TaskBody #t_brief_edit { width: auto; min-width: 0; margin-bottom: 1; }
    TaskBody #t_title { text-style: bold; padding-bottom: 1; }
    TaskBody #t_badge, TaskBody #t_meta { padding-bottom: 1; }
    TaskBody .section { padding-top: 1; border-top: solid $primary-darken-1; margin-top: 1; height: auto; }
    """

    POLL_S = 2      # how often an open page looks for what an agent added

    class Changed(Message):
        """An action changed the task: the host redraws what it lists."""

    def __init__(self, tid: int = 0, keys: bool = False, **kw) -> None:
        super().__init__(**kw)
        self.tid = tid
        self.d: dict | None = None
        self._tl_rows: list[tuple[str, Horizontal]] = []      # the timeline rows on screen, each with its entry's key
        self.keys = keys          # the buttons name their keys: the host binds them

    def compose(self) -> ComposeResult:
        def label(word: str, key: str) -> str:
            return f"{word}  {key}" if self.keys else word
        with VerticalScroll(id="detail_pane"):
            yield TaskText(id="t_title")
            yield TaskText(id="t_badge")
            yield TaskText(id="t_brief")
            yield Button("✎ edit text", id="t_brief_edit", compact=True)
            yield TaskText(id="t_meta")
            yield TaskText(id="t_kids", classes="section")
            yield Static(id="t_memo_head", classes="section")
            yield Vertical(id="t_memos")
            with Horizontal(id="t_memo_new"):
                yield ImeInput(id="t_memo_box", compact=True, placeholder="a memo on this task")
                yield Button("Add", id="memo_add", variant="primary", compact=True)
        with ActionBar(id="t_actions"):
            yield Button(label("Assign", "a"), id="assign", variant="primary", compact=True)
            yield Button(label("Done", "x"), id="done", variant="success", compact=True)
            yield Button(label("Reopen", "r"), id="reopen", variant="warning", compact=True)
            yield Button(label("Discard", "d"), id="discard", variant="error", compact=True)

    def on_mount(self) -> None:
        self.load()
        self._poller = self.set_interval(self.POLL_S, self.poll)

    def show(self, tid: int | None) -> None:
        self.tid = tid or 0
        self.load()

    def poll(self) -> None:
        """What an agent adds while the page is open (a memo, a handoff, a state change) shows
        without reopening it; a page whose task has not changed is left as it is."""
        if not self.tid or not self.display:
            return
        con = db.connect()
        try:
            now = task_detail(con, self.tid)
        finally:
            con.close()
        if now != self.d:
            self.load()

    def load(self) -> None:
        self.d = None
        if not self.tid:
            self._blank("no task")
            return
        con = db.connect()
        try:
            self.d = task_detail(con, self.tid)
        finally:
            con.close()
        d = self.d
        if d is None:
            self._blank(f"task {actions.task_ref(self.tid)} is gone")
            return
        page = task_page(d)
        self.query_one("#t_title", Static).update(page["title"])
        self.query_one("#t_badge", Static).update(page["badge"])
        brief = self.query_one("#t_brief", Static)
        brief.update(page["brief"])
        brief.display = bool(page["brief"].plain)
        self.query_one("#t_brief_edit", Button).display = d["brief_text"] is not None
        self.query_one("#t_meta", Static).update(page["meta"])
        self.query_one("#t_kids", Static).update(self._section("Sub-tasks", page["kids"]))
        self.draw_memos(page["timeline"])
        for name in ("assign", "done", "reopen", "discard"):
            self.query_one(f"#{name}", Button).disabled = not d[f"can_{name}"]

    def _blank(self, said: str) -> None:
        self.query_one("#t_title", Static).update(Text(said, style="dim"))
        for name in ("badge", "brief", "meta", "kids", "memo_head"):
            self.query_one(f"#t_{name}", Static).update("")
        self.query_one("#t_brief", Static).display = False
        self.query_one("#t_brief_edit", Button).display = False
        self.query_one("#t_memos", Vertical).remove_children()
        self._tl_rows = []
        for name in ("assign", "done", "reopen", "discard"):
            self.query_one(f"#{name}", Button).disabled = True

    def draw_memos(self, texts: list[Text]) -> None:
        """The timeline: a line each, and a memo's own line ends in edit and delete icons. A line
        is kept while its entry is unchanged: only entries that came, went or changed are removed
        or mounted, so an agent's one new memo does not rebuild a hundred rows."""
        self.query_one("#t_memo_head", Static).update(Text.assemble(("Timeline", "bold"), (f"  {len(texts)}", "dim")))
        box = self.query_one("#t_memos", Vertical)
        entries = self.d["timeline"]
        keys = [json.dumps(e, sort_keys=True, default=str) for e in entries]
        have = self._tl_rows
        kept: dict[int, Horizontal] = {}        # new index -> the row that already shows it
        for a, b, size in difflib.SequenceMatcher(None, [k for k, _ in have], keys, autojunk=False).get_matching_blocks():
            for i in range(size):
                kept[b + i] = have[a + i][1]
        for _, row in have:
            if row not in kept.values():
                row.remove()
        rows: list[tuple[str, Horizontal]] = []
        for i, (key, text, e) in enumerate(zip(keys, texts, entries)):
            row = kept.get(i)
            if row is None:
                cells = [Static(text, classes="tl_text")]
                if e["memo"]:
                    cells += [Button("✎", id=f"me_{e['memo']}", compact=True, classes="tl_icon"),
                              Button("✕", id=f"md_{e['memo']}", variant="error", compact=True, classes="tl_icon")]
                row = Horizontal(*cells, classes="tl_row")
                later = next((kept[j] for j in range(i + 1, len(keys)) if j in kept), None)
                box.mount(row, before=later) if later is not None else box.mount(row)
            rows.append((key, row))
        self._tl_rows = rows

    def action_memo_add(self) -> None:
        box = self.query_one("#t_memo_box", ImeInput)
        text = box.value
        if not text.strip():
            return
        box.value = ""
        self.memo_call("add", lambda con: actions.memo_add(con, self.tid, actions.HUMAN, text)[:2])

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "t_memo_box":
            event.stop()
            self.action_memo_add()

    def memo_edit(self, mid: int) -> None:
        old = next((e["text"] for e in self.d["timeline"] if e["memo"] == mid), None)
        if old is None:
            return

        def answered(text: str | None) -> None:
            if text is not None and text.strip():
                self.memo_call("edit", lambda con: actions.memo_edit(con, mid, actions.HUMAN, text))
        self.app.push_screen(TextPrompt("edit the memo", multi=True, initial=old), answered)

    def memo_delete(self, mid: int) -> None:
        def answered(yes: bool | None) -> None:
            if yes:
                self.memo_call("delete", lambda con: actions.memo_delete(con, mid, actions.HUMAN))
        self.app.push_screen(Confirm("Delete the memo", "It is removed from the task.", yes="Delete", danger=True), answered)

    def action_edit_brief(self) -> None:
        if not (self.d and self.d["brief_text"] is not None):
            return

        def answered(text: str | None) -> None:
            if text is None:
                return
            con = db.connect()
            try:
                ok, said = actions.edit_task(con, self.tid, actions.HUMAN, body=text)
                if ok:
                    actions.tell_body_edited(con, self.tid, actions.HUMAN)
            finally:
                con.close()
            self.report("edit text", ok, said)
        self.app.push_screen(TextPrompt("edit the task's text", multi=True, initial=self.d["brief_text"]), answered)

    def memo_call(self, verb: str, call) -> None:
        con = db.connect()
        try:
            ok, said = call(con)
        finally:
            con.close()
        self.report(f"memo {verb}", ok, said)

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
        self.post_message(self.Changed())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid[:3] in ("me_", "md_") and bid[3:].isdigit():
            event.stop()
            (self.memo_edit if bid[:3] == "me_" else self.memo_delete)(int(bid[3:]))
            return
        call = {"memo_add": self.action_memo_add, "t_brief_edit": self.action_edit_brief, "assign": self.action_assign_task,
                "done": self.action_done_task, "reopen": self.action_reopen_task, "discard": self.action_discard_task}.get(bid)
        if call:
            event.stop()
            call()


class TaskPane(TaskBody):
    """The task under the cursor, beside a table when the terminal is wide."""

    WIDE = 130
    WIDTH = 50
    DEFAULT_CSS = f"""
    TaskPane {{ width: {WIDTH}; height: 100%; border-left: solid $primary; }}
    TaskPane #detail_pane {{ padding: 0 1; }}
    TaskPane #t_actions {{ padding: 0 1; margin-bottom: 0; }}
    TaskPane #t_actions Button {{ margin-right: 1; }}
    """

    def fit(self, width: int, table: "TaskTable") -> bool:
        """Show beside `table` when `width` allows; then a click on a row only moves the
        cursor, and the pane says what is under it."""
        wide = width >= self.WIDE
        self.display = wide
        table.click_selects = not wide
        return wide


class TaskDetail(Screen):
    """One task's page: the shared `TaskBody` under a Back button, with a key for each call the
    human may make on it."""
    BINDINGS = [Binding("escape", "app.pop_screen", "back"),
                Binding("x", "done_task", "done"),
                Binding("r", "reopen_task", "reopen"),
                Binding("a", "assign_task", "assign"),
                Binding("d", "discard_task", "discard")]

    def __init__(self, tid: int) -> None:
        super().__init__(classes="chat_page")
        self.tid = tid

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="t_top"):
            yield Button("‹ Back", id="back", compact=True)
        yield TaskBody(self.tid, keys=True, id="task_body")

    def on_mount(self) -> None:
        self.sub_title = f"task {actions.task_ref(self.tid)}"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "back":
            event.stop()
            self.app.pop_screen()

    def action_open_task(self, tid: int) -> None:
        self.app.push_screen(TaskDetail(tid))

    def _body(self) -> TaskBody:
        return self.query_one(TaskBody)

    def action_done_task(self) -> None:
        self._body().action_done_task()

    def action_reopen_task(self) -> None:
        self._body().action_reopen_task()

    def action_assign_task(self) -> None:
        self._body().action_assign_task()

    def action_discard_task(self) -> None:
        self._body().action_discard_task()


class ChatPage(JobsAware, Screen):
    BINDINGS = [("escape", "back", "back"), ("ctrl+r", "history", "history")]

    def __init__(self, agent: str, focus: tuple[int, int] | None = None) -> None:
        super().__init__(classes="chat_page")
        self.agent = agent
        self._focus = focus      # (request id, part) to pick as the reply target once it is drawn
        self._tabbed = False     # the opening tab has been activated (that clears any reply target)
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
        self.history = load_history(self.agent)
        self.hist_pos: int | None = None    # entry Up/Down has recalled, None when typing fresh
        self.hist_draft = ""                # what was in the box before the first Up
        self._recalling = False
        self.history_mode = False           # Ctrl+R: the menu lists the history
        self.sending = 0                    # sends handed to workers, not yet answered
        self.pending: list[Pending] = []
        self._spinning, self._frame = False, 0
        self._send_lock = threading.Lock()  # one send at a time, in the order typed
        self.last_chat = "mine"     # the chat tab Esc returns to from Tasks
        self._scope_wait: str | None = load_scope()   # the remembered tab, until it has been shown
        self.oldest: int | None = None      # id of the oldest request on screen
        self.more = False                   # older requests than `oldest` exist
        self._sig: tuple | None = None
        self._live: tuple | None = None             # the spinner, wait and reason the widgets show now
        self._transcript_key: tuple | None = None   # what the transcript widgets were last built from
        self._block_id: int | None = None      # the block the modal on screen shows
        self._block_modal = None
        self._block_pressing = False      # an answer is in flight: the block going away is its own doing
        self._blocks_done: set[int] = set()      # blocks closed by hand or answered: not offered again

    def compose(self) -> ComposeResult:
        yield from page_header([("My chat", "mine"), ("All chat", "all"), ("Tasks", "tasks"),
                                ("Settings", "settings")], back=True)
        with Horizontal(id="body"):
            yield VerticalScroll(id="transcript")
        from .components.tasks_view import TasksView    # it draws on this module
        yield TasksView(agent=self.agent, id="tasks_view")
        with Vertical(id="settings_view"):
            with VerticalScroll(id="settings_pane"):
                yield SectionHead("agent")
                for key, label in (("session_account", "session runs on"),
                                   ("vendor", "vendor"), ("enabled", "enabled")):
                    with Horizontal(classes="frow"):
                        yield Static(label, classes="flabel")
                        yield Static(id=f"s_{key}", classes="fvalue")
                yield SectionHead("model")
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
                    yield Static("hand off at", classes="flabel")
                    yield ImeInput(id="s_context_k", compact=True, restrict=r"[0-9]*", max_length=6)
                    yield Static("k tokens", classes="unit")
                    yield Static(id="s_context_share", classes="fvalue")
                yield SectionHead("work")
                with Horizontal(classes="frow"):
                    yield Static("repo", classes="flabel")
                    yield ImeInput(id="s_repo", compact=True, placeholder="where it works")
                yield SectionHead("session")
                yield SwitchRow("skip prompts", "claude --dangerously-skip-permissions, from the next launch", "s_skip")
                with Horizontal(classes="frow"):
                    yield Static("idle close", classes="flabel")
                    yield ImeInput(id="s_idle_close", compact=True, restrict=r"[0-9]*", max_length=5)
                    yield Static("min, 0 = never", classes="unit")
                yield SchedulesSection(self.agent, id="s_schedules")
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
            agent_badge(word, "" if detail.startswith(WAITS) else detail),   # the work line says it
            (f"   {self.SPINNER[self._frame % len(self.SPINNER)]} sending"
             + (f" {self.sending}" if self.sending > 1 else "") + "…" if self.sending else "", "cyan"),
            (f"   reply to #{mid_of(reply.m)}" if reply else "", "bold"),
            ("  · esc to cancel" if reply else "", "dim"))

    def on_mount(self) -> None:
        self.sub_title = f"agent {self.agent}"
        self.query_one("#tasks_view").display = False
        self.query_one("#compose", ChatBox).menu = self.query_one("#menu", OptionList)
        self.query_one("#compose", ChatBox).focus()
        if self._scope_wait == "all":
            self.query_one("#scope", Tabs).active = "all"
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

    def refresh_block(self) -> None:
        """Show this agent's block - the one prompt the daemon's watch found on its screen - as
        exactly one modal, by its kind. A block is offered once: closed by hand or answered, it
        is not offered again. Nothing here reads a terminal."""
        con = db.connect()
        try:
            block = actions.open_block_of(con, self.agent)
        finally:
            con.close()
        shown = self._block_modal
        if shown is not None:
            if shown is self.app.screen and not self._block_pressing \
                    and (block is None or block["id"] != self._block_id):
                self.app.pop_screen()      # the prompt moved on without this modal's own answer
            if shown is self.app.screen or shown in self.app.screen_stack:
                return
            self._block_modal = None
        if block is None or not block["answerable"] or block["id"] in self._blocks_done \
                or isinstance(self.app.screen, (DialogModal, PermissionModal, QuestionModal, TextPrompt)):
            return
        self._block_id, self._block_pressing = block["id"], False
        detail = {**block["detail"], "title": block["title"]}
        bid = block["id"]
        if block["kind"] == PERMISSION:
            modal = PermissionModal(detail)
            self.app.push_screen(modal, lambda yes: self.block_picked(bid, "permission", yes,
                                                                     None if yes is None else {"yes": yes}))
        elif block["kind"] == QUESTION:
            def press(choices: "tuple[int, ...]", text: "str | None") -> "tuple[bool, str]":
                return actions.request_block_answer(bid, {"choices": list(choices), "text": text})

            def start() -> None:
                self._block_pressing = True     # sending, on the modal's own thread
            modal = QuestionModal(detail, press, start)
            self.app.push_screen(modal, lambda result: self.block_asked(bid, result))
        else:
            modal = DialogModal(detail)
            self.app.push_screen(modal, lambda picked: self.block_picked(
                bid, "dialog", picked, None if picked is None else {"number": picked[0], "always": picked[1]}))
        self._block_modal = modal

    CANCEL = {"cancel": True}

    def block_picked(self, block_id: int, kind: str, picked, choice: "dict | None") -> None:
        """A permission or dialog modal closed: send its pick to the daemon and say what came back.
        Esc closes it with no pick, and that is sent as a cancel (Esc in the terminal)."""
        self._blocks_done.add(block_id)      # closed or answered: not offered again
        self.send_block(block_id, kind, choice or self.CANCEL)

    def send_block(self, block_id: int, kind: str, choice: dict) -> None:
        def press() -> None:
            ok, said = actions.request_block_answer(block_id, choice)
            self.app.call_from_thread(self.block_result, kind, ok, said)
        self.run_worker(press, thread=True, group="block_press")

    def block_asked(self, block_id: int, result: "tuple[bool, str] | None") -> None:
        self._blocks_done.add(block_id)
        self._block_pressing = False
        if result is None:      # Esc before anything was sent: a cancel
            self.send_block(block_id, "question", self.CANCEL)
        else:
            self.block_result("question", *result)

    def block_result(self, kind: str, ok: bool, said: str) -> None:
        log_action(f"{kind} {self.agent}: {'ok' if ok else 'refused'} - {said}")
        self.notify(safe(said), title=f"{kind} {self.agent}",
                    severity="information" if ok else "error", timeout=10, markup=False)

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
        wait_changed = [d if d.startswith(WAITS) else "" for d in (state[1], self.agent_state[1])]
        self.agent_state = state
        if wait_changed[0] != wait_changed[1]:
            self._sig = None                # the work line's wait text follows the detail
        if word_changed:
            for line in self.query(WorkLine):
                line.show(state[0] == "RUNNING", line.wait)
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
        self.refresh_block()
        self.refresh_pools()
        if self.showing_settings:
            return
        if not self.showing_tasks:
            self.refresh_conversation()

    def refresh_conversation(self, force: bool = False) -> None:
        """Bring what is on screen up to date. Unchanged, this is one small
        count; changed, it re-reads only the requests already on screen and
        anything newer, never the whole history.

        `_sig` is forced dirty by the agent's word flipping between RUNNING
        and IDLE (`refresh_agent_state`), which happens often while it works
        and says nothing about what is on screen. So the transcript is only
        actually torn down and remounted when `_transcript_key` - what its
        rows, their states and the work line actually say - has changed,
        and the scroll only jumps to the bottom when the viewer was already
        there (founder #1975/T309): moving it while someone has scrolled up
        to read older messages would yank the page out from under them."""
        con = db.connect()
        try:
            sig = signature(con, self.agent, self.everyone)
            if sig == self._sig and not force:
                return
            self._sig = sig
            if self.oldest is None:
                rows, self.more = requests_page(con, self.agent, self.everyone)
                # Opened on a message: page back until it is on screen.
                while self._focus and self.more and rows and rows[0]["id"] > self._focus[0]:
                    older, self.more = requests_page(con, self.agent, self.everyone, before=rows[0]["id"])
                    rows = older + rows
            else:
                rows, _ = requests_page(con, self.agent, self.everyone, since=self.oldest)
        finally:
            con.close()
        self.oldest = rows[0]["id"] if rows else None
        msgs = messages_of(rows, self.agent)
        con = db.connect()
        try:
            # What is drawn is seen: it leaves FOR YOU. An answer counts by its own row.
            actions.for_you_seen(con, self.agent, max((max(r["id"], r["answer_id"] or 0) for r in rows), default=0))
            why = wait_reason(con, self.agent, msgs, self.agent_state[0] == "RUNNING")
            fold_handoffs(con, msgs)
            changes = task_changes(con, msgs, self.agent)
        finally:
            con.close()
        state, detail = self.agent_state
        wait = detail if detail.startswith(WAITS) else ""
        key = (tuple((r["id"], r["state"], db.answered_at(r), r["delivered_at"], r["landed_at"], r["typed_at"],
                      r["dismisser"])
                     for r in rows), tuple((c["tasks"][0], c["when"], c["change"]) for c in changes))
        live = (state == "RUNNING", why, wait)
        if key == self._transcript_key and not force:
            if live != self._live:      # only the spinner, the wait or a reason: redraw those, not the chat
                self._live = live
                for line in self.query(WorkLine):
                    line.show(live[0], wait)
                first = next((m for m in msgs if m["unread"]), None)
                for msg in self.query(ChatMessage):
                    msg.set_working(live[0])
                    msg.set_why(why if first and (msg.m["rid"], msg.m["part"]) == (first["rid"], first["part"]) else "")
            return          # nothing a viewer would see changed - the widgets and scroll stay put
        self._transcript_key, self._live = key, live
        keep = (self.replying.m["rid"], self.replying.m["part"]) if self.replying else None
        pane = self.query_one("#transcript", VerticalScroll)
        at_bottom = pane.is_vertical_scroll_end
        pane.remove_children()
        widgets = transcript_widgets(with_task_changes(msgs, changes), state == "RUNNING", why, wait)
        pane.mount(*widgets)
        if at_bottom:
            pane.anchor()       # stays at its end as the new messages are laid out, until the reader scrolls
        self.replying = None
        for w in widgets:
            if keep and isinstance(w, ChatMessage) and (w.m["rid"], w.m["part"]) == keep:
                self.reply_to(w)
        if self._focus:
            picked = next((w for w in widgets if isinstance(w, ChatMessage)
                           and (w.m["rid"], w.m["part"]) == self._focus and w.m["replyable"]), None)
            if picked is not None:
                self.reply_to(picked)
                self.call_after_refresh(picked.scroll_visible)
                self._focus = None if self._tabbed else self._focus

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
        if not self.showing_tasks and pane.scroll_y <= max(pane.size.height, 1):
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

    def run_slash(self, what: tuple) -> None:
        """A `/` line is a command of the bridge: do it, or say why not and keep the text."""
        kind = what[0]
        if kind == "error":
            self.notify(what[1], severity="error", timeout=6, markup=False)
            return
        self.query_one("#compose", ChatBox).text = ""
        if kind == "run":
            self.run_command(what[1])
        elif kind == "verb":
            self.run_verb(what[1], what[2])
        elif kind == "open":
            self.app.push_screen(TaskDetail(what[1]))
        elif kind == "sort":
            self.set_sort(what[1])
            self.query_one("#scope", Tabs).active = "tasks"

    def run_command(self, name: str) -> None:
        """Do the page command `name`: open the tab it names, list the commands, or quit."""
        tab = next(t for n, _, t in PAGE_COMMANDS if n == name)
        if tab == "help":
            self.query_one("#compose", ChatBox).text = ""
            self.notify("\n".join(f"/{n}  {what}" for n, what, _ in PAGE_COMMANDS)
                        + "\n" + "\n".join(f"/{v}  {VERB_WHAT[v]}" for v in CHAT_VERBS),
                        title="commands", timeout=15, markup=False)
            return
        if tab == "exit":
            self.query_one("#compose", ChatBox).text = ""
            if not self.sending:
                self.app.exit()
            else:
                self.app.push_screen(Confirm("Quit the bridge", f"{self.sending} message(s) still being sent.",
                                             yes="Quit", no="Stay", danger=True),
                                     lambda yes: yes and self.app.exit())
            return
        if tab is None:
            self.leave()
            return
        self.query_one("#compose", ChatBox).text = ""
        self.query_one("#scope", Tabs).active = tab

    def unsent(self) -> list[str]:
        """What leaving this page now would lose."""
        lost = []
        if self.query_one("#compose", ChatBox).text.strip():
            lost.append("the draft in the box")
        if self.sending:
            lost.append(f"{self.sending} message(s) still being sent")
        return lost

    def leave(self) -> None:
        """Go back to the main screen, asking first when that would lose a draft
        or leave messages that are still being sent."""
        lost = self.unsent()
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
            if self._scope_wait is None:
                save_scope(event.tab.id)
            elif event.tab.id == self._scope_wait:
                self._scope_wait = None     # the remembered tab is up; from here a pick is the human's
        self.oldest, self.more, self._sig, self._transcript_key = None, False, None, None
        self.reply_to(None)
        self._tabbed = True
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
        self.query_one("#s_idle_close", Input).value = cfg["idle_close"]
        self.query_one("#s_idle_close", Input).placeholder = actions.idle_close_minutes(idle_close.default_close_timeout_s())
        self.show_context_share()
        self.query_one("#s_schedules", SchedulesSection).load()
        self.query_one("#s_effort", Select).value = cfg["effort"] if cfg["effort"] in agents.VALID_EFFORT else "medium"
        self.query_one("#s_weight", Select).value = cfg["weight"] if cfg["weight"] in WEIGHTS else "light"

    def form(self) -> dict:
        return {"model": self.query_one("#s_model", Input).value,
                "effort": str(self.query_one("#s_effort", Select).value),
                "weight": str(self.query_one("#s_weight", Select).value),
                "repo": self.query_one("#s_repo", Input).value,
                "skip_permissions": "on" if self.query_one("#s_skip", Switch).value else "off",
                "context_k": self.query_one("#s_context_k", Input).value,
                "idle_close": self.query_one("#s_idle_close", Input).value}

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

    def action_show_message(self, rid: int) -> None:
        """Scroll to message `rid` and light it for a moment."""
        target = next((w for w in self.query(ChatMessage) if rid in (w.m["rid"], w.m.get("mid"))), None)
        if target is None:
            self.notify(f"#{rid} is not on this page - scroll up for older messages", timeout=6)
            return
        target.scroll_visible(animate=False)
        target.add_class("flash")
        self.set_timer(1.5, lambda: target.remove_class("flash"))

    def action_open_task(self, tid: int) -> None:
        self.app.push_screen(TaskDetail(tid))

    def reply_to(self, msg: ChatMessage | None) -> None:
        for old in self.query(".replying"):
            old.remove_class("replying")
            old.show_id(False)
        self.replying = msg
        if msg is not None:
            msg.add_class("replying")
            msg.show_id(True)
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

    def on_text_box_send(self, event: TextBox.Send) -> None:
        event.stop()
        box = self.query_one("#compose", ChatBox)
        body = box.text
        sg = self._highlighted()
        if sg is not None:
            self.apply(sg)
            return
        if not body.strip():
            return
        what = slash_action(body)
        if what is not None:
            self.run_slash(what)
            return
        rid = mid_of(self.replying.m) if self.replying else None
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
        save_history(self.agent, self.history)
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
        if not ok:      # a send that went says nothing: the message shows it
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
