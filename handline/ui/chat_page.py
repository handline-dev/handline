"""The new agent chat page, Textual widgets only. Run it alone with
`~/.orcapipe/venv/bin/python3 -m handline.ui.chat_page [agent]`; the layout lives in
chat_page.tcss."""
from __future__ import annotations

import asyncio
import difflib
import json
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from rich.style import Style
from rich.text import Text
from textual import events
from textual.message import Message
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.binding import Binding
from textual.coordinate import Coordinate
from textual.screen import Screen
from textual.worker import get_current_worker
from textual.widgets import Button, DataTable, Header, Input, OptionList, Select, Static, Tabs, TextArea
from textual.widgets.option_list import Option

from .. import actions, db
from ..actions.prompt_blocks import PERMISSION, QUESTION
from ..daemon import client, landing as landingmod
from ..team import agents, ask as askmod
from ..team.agent_settings import (BROWSER_CUSTOM, BROWSER_MODES, EDITABLE, WEIGHTS,   # noqa: F401 - the tests read them here
                                   save_settings, settings_changes, settings_of, settings_problem)
from ..infra import paths, textsafe
from ..session import idle_close
from ..usage import status
from . import api, stall
from ..view import tui
from .clipboard_app import ClipboardApp
from .theme import follow_os_theme
from ..infra import timefmt
from .components.ime import ImeInput
from .components.confirm import Confirm
from .components import hr_card
from .components.shell_card import ShellCard
from .components.dialog import Dialog
from .components.dialog_modal import DialogModal
from .components.permission_modal import PermissionModal
from .components.question_modal import QuestionModal
from .components.jobs_aware import JobsAware, dispatch_agent_verb
from .components.ime import TextBox
from .components.text_prompt import TextPrompt
from .components.action_bar import ActionBar
from .components.agent_strip import AgentStrip
from .components.sandbox_banner import SandboxBanner
from .components.page_header import page_header
from .components.schedules_section import SchedulesSection
from .components.agent_settings import AgentSettings
from .components.task_assign import TaskAssign
from .components.write_guard import WriteGuard

from ..actions.chatreads import (_GONE, PAGE, Located, _scope, _with_task_state, locate_message)  # noqa: F401 - the screen's own names

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


# Said in the founder's terms; only a start of a new conversation is drawn, so "wake" is a fresh
# start after the agent was closed for idle.
START_WORDS = {"restart": "restarted fresh", "wake": "after idle close", "fresh close": "after a fresh close",
               "move": "moved to another account", "handoff": "handoff", "launch": "opened by hand"}


def session_head(r: dict) -> str:
    """Line 1 of a session start: when, then why."""
    return f"{timefmt.stamp(r['ts'], timefmt.CLOCK)} · {START_WORDS.get(r['cause'], r['cause'])}"


def session_line(r: dict) -> Text:
    """A new session as two lines: when and why, then "new session" - with "handoff" a link to read
    it, where there is one."""
    out = Text(session_head(r) + "\nnew session", style="dim")
    if r["text"]:
        out.append(" with ")
        out.append("handoff", style=Style(underline=True, meta={"@click": "open"}))
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


def hr_concerns(p: dict) -> set[str]:
    """The agents an HR request concerns: whoever proposed it, the agent it adds, retires,
    renames, links or changes (with the name it takes or points to), the manager of the team it
    is for, and a new team's members."""
    named = (p["agent"], p.get("requested_by"), p.get("team"), p.get("to"), *(p.get("members") or "").split(","))
    return {n for n in named if n}


def hr_cards(agent: str, msgs: list[dict]) -> list[dict]:
    """The HR requests that concern `agent` (`hr_concerns`), in its chat and in its all-chat alike,
    as entries for the chat: {hr: the request, when}. A request waiting stays; a decided one stays
    from the first message drawn."""
    since = min((m["when"] for m in msgs if m["when"]), default=0)
    return [{"hr": p, "when": p["ts"]} for p in hr_card.pending(since) if agent in hr_concerns(p)]


def messages_of(items: list[dict], agent: str) -> list[dict]:
    """One dict per message of these `chat.requests` items, in time order: who, when, text,
    the request it belongs to (`rid`), its own number (`mid`: the request's for an
    ask, the answer's for a verdict), whether it is the ask (part 0) or the verdict
    (part 1), which side said it, and whether the human can reply to it. Nothing here tracks
    whether a message was answered (founder #2659)."""
    human = actions.HUMAN
    msgs = []
    for item in items:
        if item["type"] != "request":
            continue
        r = item["message"]
        shown = (r["answer"] or {}).get("text") or None
        answered_at = api.epoch((r["answer"] or {}).get("at"))
        landed, typed = api.epoch(r["landed_at"]), api.epoch(r["typed_at"])
        for part, speaker, to, text, at in (
                (0, r["from"], r["to"], r["body"], api.epoch(r["at"])),
                (1, r["to"], r["from"], shown, answered_at)):
            cancelled = r["state"] in ("landed", "withdrawn") and shown == askmod.CANCEL_VERDICT
            if part == 1 and (not shown or cancelled or r["dismissed"]):
                continue
            if part == 0 and r["is_task_question"]:
                continue
            side = "you" if speaker == human else "agent" if speaker == agent else "other"
            queued = part == 0 and speaker == human and r["to"] != human and r["state"] == "queued"
            held = (part == 0 and speaker != human and r["to"] != human
                    and r["state"] == "queued" and not r["delivered_at"])
            landing = "delivered" if landed else "typed" if typed else "queued"
            # last, marked unread: only what this page's agent has yet to read; what it sent
            # (or two others') sits at its own time
            waiting_to_go = held and r["to"] == agent or queued and landing != "delivered"
            if part == 0 and landed:
                at = landed      # a message sits where its agent read it, not where it queued
            who = ("you" if speaker == human else speaker) + (
                "" if human in (speaker, to) else f" → {to}")
            tasks = sorted(t["id"] for t in r["tasks"])
            msgs.append({"who": who, "when": at or 0, "rid": r["id"], "part": part, "side": side,
                         "mid": r["id"] if part == 0 else r["answer"]["id"],
                         "text": (text or "").strip("\n") or "(empty)",
                         "replyable": True,      # any message, also one between two other agents in All chat
                         "queued": queued,
                         "held": held,
                         "unread": waiting_to_go,
                         "to": to,
                         "agent": r["to"] if r["from"] == human else r["from"],
                         "kind": r["kind"] or None,
                         "tasks": tasks,
                         "task_title": r["tasks"][0]["title"] if r["tasks"] else None, "task_state": item["task_state"],
                         "sending_reason": r["sending_reason"] or "",
                         "delivered": landing == "delivered",
                         "landing": landing,
                         "reply_to": r["parent"] if part == 0 else r["id"] if speaker == human else None,   # the founder's reply is linked
                         "cancelled": part == 0 and cancelled,
                         "_key": (float("inf") if waiting_to_go else at or 0, r["id"], part)})
    msgs.sort(key=lambda m: m["_key"])
    for m in msgs:
        del m["_key"]
    return msgs


def read_requests(agent: str, everyone: bool, before: int | None = None, since: int | None = None, want: int | None = None,
                  limit: int | None = None) -> tuple[list[dict], bool, str]:
    """A page of a chat from the daemon (`chat.requests`): its items, whether older requests remain, and its version. With `want`
    (a request number), the newest page and as many before it as it takes to have that request among them."""
    params = {"agent": agent, "scope": "all" if everyone else "mine", **{k: v for k, v in
              (("before", before), ("since", since), ("limit", limit)) if v is not None}}
    got = api.call("chat.requests", params)
    items, more = got["items"], got["more"]
    while want and more:
        ids = [i["message"]["id"] for i in items if i["type"] == "request"]
        if not ids or min(ids) <= want:
            break
        older = api.call("chat.requests", {**params, "before": min(ids)})
        items, more = older["items"] + items, older["more"]
    return items, more, got["version"]


def changes_of(items: list[dict], agent: str, msgs: list[dict]) -> list[dict]:
    """The task changes and session starts among `chat.requests` items, as entries for the chat: {change, note, who,
    side (as a message's, from `agent`'s page), tasks, task_title, when} and {session: {cause, text}, when}. A handoff
    delivers its task to the new owner: it is drawn as ONE card, the delivery's, which takes the change and the note."""
    cards = {m["rid"]: m for m in msgs if is_card(m)}
    out = []
    for i in items:
        if i["type"] == "task_event":
            who = i["who"]
            if i["request"] in cards:
                cards[i["request"]]["change"], cards[i["request"]]["note"] = i["change"], i["note"] or ""
                continue
            out.append({"change": i["change"], "note": i["note"] or "", "who": who,
                        "side": "you" if who == actions.HUMAN else "agent" if who == agent else "other",
                        "tasks": [i["task"]], "task_title": i["title"], "when": api.epoch(i["at"])})
        elif i["type"] == "session":
            out.append({"session": {"cause": i["cause"], "text": i["handoff"], "ts": api.epoch(i["at"])}, "when": api.epoch(i["at"])})
    return out


def reply_link(parent: int) -> Text:
    """A reply icon and the number of what it answers, ahead of the reply's text; a click goes there."""
    return Text(f"↩ #{parent}", style=Style(dim=True, underline=True, meta={"@click": f"screen.show_message({parent})"}))


def mid_of(m: dict) -> int:
    """The number a message goes by: its own row's (an answer has one), else its request's."""
    return m.get("mid") or m["rid"]


def tasks_of(agent: str) -> list[tuple]:
    """The tasks not yet done or discarded that `agent` owns or opened, as (id,
    role, state, title, the other party, last update). A task it opened for
    itself is owned. The last update is `last_activity` (`tasks.list`)."""
    seen: dict[int, dict] = {}
    for key in ("owner", "from"):
        before = None
        while True:
            got = api.call("tasks.list", {key: agent, "limit": 200, **({"before": before} if before else {})})
            seen.update((t["id"], t) for t in got["items"])
            if not got["more"] or not got["items"]:
                break
            before = min(t["id"] for t in got["items"])
    return [(t["id"], "owns" if t["to"] == agent else "opened", t["state"], t["title"],
             t["from"] if t["to"] == agent else t["to"], api.epoch(t["last_activity"]) or 0)
            for t in sorted(seen.values(), key=lambda t: t["id"])]


def task_detail(tid: int) -> dict | None:
    """A task as what its page shows (`task.get`): its own fields, the sub-tasks and
    requests tied to it, and which of reopen and discard the human may do now.
    None when there is no such task."""
    try:
        t = api.call("task.get", {"task": tid})
    except client.RpcError as e:
        if e.code == "not_found":
            return None
        raise
    state = t["state"]
    brief = (t["body"] or "").strip()      # the one `edit_task` edits
    if brief.split("\n", 1)[0].strip() == t["title"].strip():
        brief = brief.split("\n", 1)[1].strip("\n") if "\n" in brief else ""      # the title is drawn above
    open_ = state in ("draft", "queued", "doing")
    gone = t["discarded"]
    held = dict.fromkeys([t["from"], *(n for h in t["handoffs"] for n in (h["from"], h["to"]))])
    return {"id": tid, "title": t["title"], "brief": brief, "state": state, "owner": t["to"],
            "opened_by": t["from"], "created_at": api.epoch(t["created"]),
            "parent": (t["parent"], t["parent_title"]) if t["parent"] else None,
            "done": (t["evidence"], api.epoch(t["done"])) if t["evidence"] else None,
            "discarded": (gone["by"], gone["reason"], api.epoch(gone["at"])) if gone else None,
            "merged_into": t["merged_into"],
            "brief_text": (t["body"] or "") if t["deliveries"] else None,
            "timeout": clock_of(t["time"], state),
            "children": [(k["id"], k["title"], k["state"]) for k in t["subtasks"]],
            "timeline": [entry_of(e) for e in t["timeline"]],
            "asker": t["asker"],
            "can_done": open_, "can_reopen": state == "done" and t["asker"] == actions.HUMAN,
            "can_discard": open_, "can_assign": open_,
            "can_queue": state in ("draft", "queued"),      # one button: Queue on a draft, Draft on a queued task
            "assignees": [a for a in held if a != actions.HUMAN and a != t["to"]],
            "people": (t["people"]["recent"], t["people"]["team"], t["people"]["others"])}


REQUEST_ENTRY = re.compile(r"(#\d+)  (\S+)  (.*)", re.S)


def entry_of(e: dict) -> dict:
    """A `task.get` timeline entry as `timeline_text` draws it. A request's text is "#<id>  <state>  <first line>"."""
    at = api.epoch(e["at"])
    out = {"ts": at, "at": at, "who": e["who"], "kind": e["kind"], "text": e["text"], "memo": e["memo"],
           "edited_at": api.epoch(e["edited_at"])}
    got = REQUEST_ENTRY.fullmatch(e["text"]) if e["kind"] == "request" else None
    if e["kind"] == "request":
        out.update(zip(("ref", "state", "summary"), got.groups() if got else ("", "", e["text"])))
    return out


def clock_of(c: dict | None, state: str) -> dict | None:
    """A task's time box as `actions.timeout_lines` reads it, from the daemon's `time` and this clock."""
    if c is None:
        return None
    now = time.time()
    out = {"length": c["length_s"], "default": c["default"], "state": "length", "start": api.epoch(c["start"]),
           "end": api.epoch(c["end"]), "told": c["told"], "left": None, "over": None}
    if out["start"] is not None and c["overdue_since"] is not None:
        out.update(state="over", over=max(0, int(now - api.epoch(c["overdue_since"]))))
    elif out["start"] is not None:
        out.update(state="left", left=max(0, int(out["end"] - now)))
    return out


def send_reply(agent: str, rid: int | None, body: str) -> tuple[bool, str]:
    """Send `body` to `agent`, under message `rid` when it replies to one: the daemon says whether that is its answer or a
    new message under it."""
    return api.write("chat.send", {"agent": agent, "body": body, **({"parent": rid} if rid is not None else {})})


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


def wait_reason(msgs: list[dict], running: bool) -> str:
    """Why the first unread message waits: the human's own that is not typed yet
    (unless the spinner already says it), else one to `agent` from another agent, which waits
    on its recipient. What the agent sent is not waiting."""
    waiting = [m for m in msgs if m["queued"] and m["landing"] == "queued"]
    held = [m for m in msgs if m["held"] and m["unread"]]
    if waiting and not running:
        return waiting[0]["sending_reason"]
    if not waiting and held:
        return f"{held[0]['to']}: {held[0]['sending_reason']}"
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


def link_task_refs(text: Text) -> Text:
    """Every `T<n>` in `text` becomes a link that opens that task (a click runs `open_task`)."""
    for start, end, tid in actions.task_refs_in(text.plain):
        text.stylize(Style(underline=True, meta={"@click": f"screen.open_task({tid})"}), start, end)
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
    body = link_paths(link_task_refs(link_refs(
        Text(safe_block(m["text"]), style="strike dim" if m.get("cancelled") else ""))), m["who"])
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
    if d.get("timeout"):
        first, *rest = actions.timeout_lines(d["timeout"])
        facts.append(("timeout", Text(first.removeprefix("timeout "))))
        for line in rest:
            facts.append(("", Text(line, style="bold red" if d["timeout"]["state"] == "over" else "")))
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


class SessionMark(Static, can_focus=True):
    """Where a session started, drawn as a system line, not as anyone's message. Where it was given a
    handoff, a click on its word (or Enter) reads it."""

    BINDINGS = [("enter", "open", "read the handoff")]

    def __init__(self, m: dict) -> None:
        super().__init__(session_line(m["session"]), classes="sessionmark")
        self.m = m

    def action_open(self) -> None:
        r = self.m["session"]
        if r["text"]:
            self.app.push_screen(HandoffModal(session_head(r), r["text"]))


class HandoffModal(Dialog):
    """The handoff a session was started with, whole and scrollable; Esc closes."""

    FOCUS = "#handoff_body"
    DEFAULT_CSS = """
    HandoffModal #dialog { width: 90; height: 80%; }
    HandoffModal #handoff_title { text-style: bold; margin-bottom: 1; }
    HandoffModal #handoff_body { height: 1fr; }
    """

    def __init__(self, title: str, text: str) -> None:
        super().__init__()
        self.title_text, self.text = title, text

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text(textsafe.oneline(self.title_text)), id="handoff_title")
            with VerticalScroll(id="handoff_body"):
                yield Static(Text("\n".join(textsafe.drawable(self.text))))


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
        if "session" in m:
            out.append(SessionMark(m))
            continue
        if "hr" in m:
            out.append(hr_card.HrCard(m["hr"]))
            continue
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
                 ("exit", "Quit", "exit"))


MENU_MAX = 8
VERB_WHAT = {'terminal': 'go to its terminal', 'wake': 'wake it now', 'close': 'close its session', 'restart': 'restart its session, resuming the conversation',
             'park': 'park it', 'fold': 'fold it into its handoff', 'move': 'move it to another account'}

# The home page's agent keys that make sense typed in the agent's own chat: `open` is this page,
# and `message` and `editor` are the box itself.
CHAT_VERBS = ('terminal', 'wake', 'close', 'restart', 'park', 'fold', 'move')


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
        if word == "restart" and arg not in ("", "fresh"):
            return ("error", "/restart takes only 'fresh' (a new conversation)")
        return ("error", f"/{word} takes no argument") if arg and word not in ("move", "restart") else ("verb", word, arg)
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
    typed: bool = True      # it came from the box; a refusal puts it back there


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
                   for v in verbs if v.startswith(name)] \
                + [Suggestion("verb", "restart fresh", "/restart fresh", ("/restart fresh", "   restart as a new conversation"))
                   for _ in ("restart",) if "restart" in verbs and "restart fresh".startswith(name)]
        arg = arg.strip().lower()
        if name == "task":
            hits = [t for t in tasks if not arg or str(t[0]).startswith(arg.lstrip("#").lstrip("t"))
                    or arg in t[3].lower()]
            hits.sort(key=lambda t: t[5], reverse=True)
            return [Suggestion("open", str(t[0]), f"/task {t[0]}", (actions.task_ref(t[0]), f"  {t[2]}  {safe(t[3])}"))
                    for t in hits[:MENU_MAX]]
        if name == "restart" and "restart" in verbs:
            return [Suggestion("verb", "restart fresh", "/restart fresh", ("fresh", "   restart as a new conversation"))
                    for _ in ("fresh",) if "fresh".startswith(arg)]
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


def refs_of(agent: str) -> tuple[list[tuple], list[tuple]]:
    """What a `#` can point at for `agent`: its open tasks and its newest
    requests (every one it sent or received, `chat.refs`)."""
    got = api.call("chat.refs", {"agent": agent})["requests"]
    return tasks_of(agent), [(r["request"], f"{r['from']} → {r['to']}", r["line"]) for r in got]


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


def read_strip(agent: str) -> dict:
    """The line under the header and the agent's state as the six screen words (`agent.strip`)."""
    return api.call("agent.strip", {"agent": agent})


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

    @property
    def write_guard(self) -> WriteGuard:
        return self.__dict__.setdefault("_write_guard", WriteGuard())

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
            if self.write_guard.refuse(self):       # another handoff is still writing: it reports first
                return
            log_action(f"task {ref} assign to {to}")

            def write() -> tuple[bool, str]:
                with stall.timed(f"assign {ref}: task.handoff"):
                    return api.write("task.handoff", {"task": self.tid, "to": to, "note": textsafe.oneline(text).strip()})

            async def assign() -> None:
                try:
                    ok, said = await asyncio.to_thread(write)      # a handoff is ~60 ms and more: not on the loop
                finally:
                    self.write_guard.release()
                self.report("assign", ok, said)
                stall.crumb(f"assign {ref}: callback end ({'ok' if ok else 'refused'})")
                self.app.call_after_refresh(stall.snapshot, self.app, f"assign {ref}: after the callback")
            self.write_guard.take(1)        # now, not when the worker starts: a second answer in between is refused
            try:
                self.run_worker(assign(), exclusive=True, group="task_assign")
            except Exception:
                self.write_guard.release()
                raise
        recent = self.d["assignees"]
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
                ok, said = api.write("task.done", {"task": self.tid, "evidence": reason.strip(), "reason": True})
                self.report("done", ok, said)
        self.app.push_screen(TextPrompt("done - what came of it?"), answered)

    def action_queue_task(self) -> None:
        """A draft is approved to run, a queued task goes back to draft (`actions.set_queued`, as `hl task queue`)."""
        if not (self.d and self.d["can_queue"]):
            self.say("only a draft can be queued, and only a queued task drafted")
            return
        queue = self.d["state"] == "draft"
        ok, said = api.write("task.queue" if queue else "task.draft", {"tasks": [self.tid]})
        self.report("queue" if queue else "draft", ok, said)

    def action_reopen_task(self) -> None:
        if not (self.d and self.d["can_reopen"]):
            self.say("only a done task that you asked for can be reopened")
            return

        def answered(reason: str | None) -> None:
            log_action(f"task {actions.task_ref(self.tid)} reopen: reason {'given' if reason and reason.strip() else 'not given'}")
            if reason and reason.strip():
                ok, said = api.write("task.reopen", {"task": self.tid, "note": reason})
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
                ok, said = api.write("task.cancel" if doing else "task.discard", {"task": self.tid, "reason": reason})
                self.report("discard", ok, said)
        self.app.push_screen(TextPrompt("stop it and discard - why?" if doing else "discard - why?"), answered)


class TaskText(Static):
    """A `Static` on a task's page whose links open another task's page, wherever the page is."""

    def action_open_task(self, tid: int) -> None:
        open_task_page(self.app, tid)


MEMO_ONLY = ("memo (no one)", "")


class TaskBody(TaskActions, Vertical):
    """One task as the human sees it: title, state, the facts, sub-tasks, requests, the timeline
    with a memo box, and the calls on it. The task page and the side view of the task list are
    both this, so what one can do the other can. `show(tid)` draws a task; the host redraws
    its own lists when `Changed` arrives."""

    DEFAULT_CSS = """
    TaskBody { height: 1fr; }
    TaskBody #detail_pane { height: 1fr; padding: 1 3; }
    /* a Vertical is 1fr, the pane's own height: the pane could not grow past one screen */
    TaskBody #t_memos { height: auto; }
    TaskBody .tl_row { height: auto; }
    TaskBody .tl_text { width: 1fr; height: auto; }
    TaskBody .tl_icon { width: 3; min-width: 0; margin-left: 1; }
    TaskBody #t_memo_new { height: 1; margin-top: 1; }
    TaskBody #t_memo_to { width: 28; margin-right: 1; }
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
        self._memo_options: list = []      # the recipients the memo box offers, and the task they were drawn for
        self._memo_task = 0

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
                yield Select([MEMO_ONLY], id="t_memo_to", allow_blank=False, compact=True)
                yield ImeInput(id="t_memo_box", compact=True, placeholder="a memo on this task, or a question to the one picked")
                yield Button("Add", id="memo_add", variant="primary", compact=True)
        with ActionBar(id="t_actions"):
            yield Button(label("Assign", "a"), id="assign", variant="primary", compact=True)
            yield Button(label("Done", "x"), id="done", variant="success", compact=True)
            yield Button(label("Queue", "u"), id="queue", variant="primary", compact=True)
            yield Button(label("Reopen", "r"), id="reopen", variant="warning", compact=True)
            yield Button(label("Discard", "d"), id="discard", variant="error", compact=True)

    def on_mount(self) -> None:
        self.load()
        self._poller = self.set_interval(self.POLL_S, self.poll)

    def show(self, tid: int | None) -> None:
        """Another task: the page is blank until its read lands, so no action can run on the new id
        with the old task's data."""
        tid = tid or 0
        if tid != self.tid:
            self.tid, self.d = tid, None
            self._blank("loading" if tid else "no task")
        self.load()

    def poll(self) -> None:
        """What an agent adds while the page is open (a memo, a handoff, a state change) shows
        without reopening it; a page whose task has not changed is left as it is."""
        if not self.tid or not self.display:
            return
        self._read(changed_only=True)

    def load(self) -> None:
        if not self.tid:
            self.d = None
            self._blank("no task")
            return
        self._read(changed_only=False)

    def _read(self, changed_only: bool) -> None:
        """The task is read on a worker thread (opening the database can wait on a writer), the newest
        read replacing one still running; the page is drawn when it lands."""
        tid = self.tid

        def work() -> None:
            try:
                d = task_detail(tid)
            except (client.RpcError, client.DaemonDown, OSError) as e:
                self.app.call_from_thread(self.say, f"the task cannot be read: {e}")
                return
            if not get_current_worker().is_cancelled:
                self.app.call_from_thread(self._arrived, tid, d, changed_only)
        self.run_worker(work, thread=True, exclusive=True, group="task_body", exit_on_error=False)

    def _arrived(self, tid: int, d: dict | None, changed_only: bool) -> None:
        if tid != self.tid or (changed_only and d == self.d):
            return
        self._draw(d)

    def _draw(self, d: dict | None) -> None:
        self.d = d
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
        for name in ("assign", "done", "queue", "reopen", "discard"):
            self.query_one(f"#{name}", Button).disabled = not d[f"can_{name}"]
        self.query_one("#queue", Button).label = ("Draft" if d["state"] == "queued" else "Queue") + ("  u" if self.keys else "")
        self.draw_memo_to(d)

    def _blank(self, said: str) -> None:
        self.query_one("#t_title", Static).update(Text(said, style="dim"))
        for name in ("badge", "brief", "meta", "kids", "memo_head"):
            self.query_one(f"#t_{name}", Static).update("")
        self.query_one("#t_brief", Static).display = False
        self.query_one("#t_brief_edit", Button).display = False
        self.query_one("#t_memos", Vertical).remove_children()
        self._tl_rows = []
        for name in ("assign", "done", "queue", "reopen", "discard"):
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

    def draw_memo_to(self, d: dict) -> None:
        """The memo box's recipient list: no one (a plain memo), then who the founder last chatted
        with, the task's team, the rest. The holder is picked when the task first shows."""
        recent, team, rest = d["people"]
        options = ([MEMO_ONLY] + [(f"{safe(a)}  (recent)", a) for a in recent]
                   + [(safe(a), a) for a in team + rest])
        to = self.query_one("#t_memo_to", Select)
        if options == self._memo_options and self._memo_task == self.tid:
            return
        keep = to.value if self._memo_task == self.tid else None
        self._memo_options, self._memo_task = options, self.tid
        to.set_options(options)
        known = [v for _, v in options]
        to.value = keep if keep in known else d["owner"] if d["owner"] in known else ""

    def action_memo_add(self) -> None:
        box = self.query_one("#t_memo_box", ImeInput)
        text, to = box.value, self.query_one("#t_memo_to", Select).value
        if not text.strip():
            return
        box.value = ""
        if to:
            log_action(f"task {actions.task_ref(self.tid)} ask {to}")
            self.memo_call("question", lambda: api.write("task.ask", {"task": self.tid, "to": str(to), "text": text}))
        else:
            self.memo_call("add", lambda: api.write("task.memo.add", {"task": self.tid, "text": text}))

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
                self.memo_call("edit", lambda: api.write("task.memo.edit", {"task": self.tid, "memo": mid, "text": text}))
        self.app.push_screen(TextPrompt("edit the memo", multi=True, initial=old), answered)

    def memo_delete(self, mid: int) -> None:
        Confirm.ask(self.app, lambda: self.memo_call("delete", lambda: api.write("task.memo.delete", {"task": self.tid, "memo": mid})),
                    "Delete the memo", "It is removed from the task.", yes="Delete", danger=True)

    def action_edit_brief(self) -> None:
        if not (self.d and self.d["brief_text"] is not None):
            return

        def answered(text: str | None) -> None:
            if text is None:
                return
            ok, said = api.write("task.edit", {"task": self.tid, "body": text})
            self.report("edit text", ok, said)
        self.app.push_screen(TextPrompt("edit the task's text", multi=True, initial=self.d["brief_text"]), answered)

    def memo_call(self, verb: str, call) -> None:
        ok, said = call()
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
                "done": self.action_done_task, "queue": self.action_queue_task, "reopen": self.action_reopen_task, "discard": self.action_discard_task}.get(bid)
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


def open_task_page(app, tid: int) -> None:
    """Open task `tid`'s page; a number that is no task says so instead of opening an empty page."""
    try:
        known = task_detail(tid) is not None
    except (client.RpcError, client.DaemonDown, OSError) as e:
        app.notify(f"the task cannot be read: {e}", severity="error", timeout=6)
        return
    if known:
        app.push_screen(TaskDetail(tid))
    else:
        app.notify(f"{actions.task_ref(tid)} is not a task", timeout=6)


class TaskDetail(Screen):
    """One task's page: the shared `TaskBody` under a Back button, with a key for each call the
    human may make on it."""
    BINDINGS = [Binding("escape", "app.pop_screen", "back"),
                Binding("x", "done_task", "done"),
                Binding("u", "queue_task", "queue/draft"),
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
        open_task_page(self.app, tid)

    def _body(self) -> TaskBody:
        return self.query_one(TaskBody)

    def action_done_task(self) -> None:
        self._body().action_done_task()

    def action_queue_task(self) -> None:
        self._body().action_queue_task()

    def action_reopen_task(self) -> None:
        self._body().action_reopen_task()

    def action_assign_task(self) -> None:
        self._body().action_assign_task()

    def action_discard_task(self) -> None:
        self._body().action_discard_task()


class NewBar(Static):
    """The bar above the box while the transcript is scrolled up from its end: how many messages
    arrived since, or a plain "↓ bottom". A click is a jump to the newest."""

    def on_click(self) -> None:
        self.screen.action_jump_bottom()


class ChatPage(JobsAware, Screen):
    BINDINGS = [("escape", "back", "back"), ("ctrl+r", "history", "history"),
                Binding("end", "jump_bottom", "newest", show=False, priority=True)]

    def __init__(self, agent: str, focus: tuple[int, int] | None = None,
                 reveal: tuple[int, int] | None = None, tab: str | None = None) -> None:
        super().__init__(classes="chat_page")
        self.agent = agent
        self._focus = focus      # (request id, part) to pick as the reply target once it is drawn
        self._reveal = reveal    # (message number, its request) to page back to, scroll to and light
        self._reveal_tries = 0
        self._fill_waits = 0
        self._tabbed = False     # the opening tab has been activated (that clears any reply target)
        self.replying: ChatMessage | None = None
        self._seen = 0
        self._esc_at: float | None = None     # when the first Esc over typed text was pressed
        self.everyone = False
        self.showing_tasks = False
        self.showing_settings = False
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
        self.shells: list[actions.ShellRun] = []    # the `!command`s run on this page, oldest first
        self.pending: list[Pending] = []
        self._spinning, self._frame = False, 0
        self._send_lock = threading.Lock()  # one send at a time, in the order typed
        self.last_chat = "mine"     # the chat tab Esc returns to from Tasks
        self._scope_wait: str | None = tab or load_scope()   # the remembered tab, until it has been shown
        self.oldest: int | None = None      # id of the oldest request on screen
        self.more = False                   # older requests than `oldest` exist
        self._sig: tuple | None = None
        self._unread_daemon = False        # the daemon could not be asked for the chat, and the page has said so
        self._inflight: set[str] = set()    # the reads on a worker thread, one per kind
        self._again: set[str] = set()       # asked for while one was in flight: run once more after it
        self._pools_at = float("-inf")      # when the menus' pools were last read
        self._read_error: str | None = None     # why the last chat read failed, for the loop to say
        self._gen = 0                       # bumped when the scope changes: a read begun before it is dropped
        self._force_convo = False
        self._live: tuple | None = None             # the spinner, wait and reason the widgets show now
        self._transcript_key: tuple | None = None   # what the transcript widgets were last built from
        self._block_id: int | None = None      # the block the modal on screen shows
        self._block_modal = None
        self._block_pressing = False      # an answer is in flight: the block going away is its own doing
        self._blocks_done: set[int] = set()      # blocks closed by hand or answered: not offered again
        self._newest: int | None = None          # the newest message number drawn so far, None before the first draw
        self._rebuilding = False
        self._unseen = 0                         # messages that arrived while scrolled up, not yet scrolled to

    def compose(self) -> ComposeResult:
        yield from page_header([("My chat", "mine"), ("All chat", "all"), ("Tasks", "tasks"),
                                ("Settings", "settings")], back=True)
        yield SandboxBanner(self.agent, id="sandbox_banner")
        yield AgentStrip(id="agent_strip")
        with Horizontal(id="body"):
            yield VerticalScroll(id="transcript")
        from .components.tasks_view import TasksView    # it draws on this module
        yield TasksView(agent=self.agent, id="tasks_view")
        with Vertical(id="settings_view"):
            yield AgentSettings(self.agent, extra=lambda: SchedulesSection(self.agent, id="s_schedules"), id="settings_form")
        with Vertical(id="dock"):
            yield OptionList(id="menu")
            yield NewBar("", id="newbar")
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
        self.query_one("#newbar").display = False
        self.watch(self.query_one("#transcript", VerticalScroll), "scroll_y", lambda *_: self._sync_newbar())
        self.query_one("#compose", ChatBox).menu = self.query_one("#menu", OptionList)
        self.query_one("#compose", ChatBox).focus()
        if self._scope_wait == "all":
            self.query_one("#scope", Tabs).active = "all"
        self.refresh_view()
        self.set_interval(2, self.refresh_view)
        if self._reveal:
            self.set_timer(0.2, self._reveal_step)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "leave":
            event.stop()
            self.leave()

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
                or isinstance(self.app.screen, (DialogModal, PermissionModal, QuestionModal, TextPrompt, HandoffModal)):
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

    def _set_agent_state(self, state: tuple[str, str]) -> None:
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

    def _off_loop(self, kind: str, read, apply) -> None:
        """Run `read` on a worker thread and `apply` its result on the loop: the screen never waits on the daemon.
        One read per `kind` at a time; asked again meanwhile, it runs once more when the first lands."""
        if kind in self._inflight:
            self._again.add(kind)
            return
        self._inflight.add(kind)

        def done(got) -> None:
            self._inflight.discard(kind)
            if self.is_attached:
                apply(got)
                if kind in self._again:
                    self._again.discard(kind)
                    self._off_loop(kind, read, apply)

        def work() -> None:
            self.app.call_from_thread(done, read())
        self.run_worker(work, thread=True, group=f"read_{kind}", exit_on_error=False)

    POOLS_S = 10        # the # and / menus' lists change slowly: read them this often, not on every refresh

    def refresh_pools(self) -> None:
        if time.monotonic() - self._pools_at < self.POOLS_S:
            return
        self._pools_at = time.monotonic()
        self._off_loop("can", self._read_can, self._apply_can)
        self._off_loop("refs", self._read_refs, self._apply_refs)

    def _read_refs(self) -> tuple | None:
        try:
            return refs_of(self.agent)
        except (client.RpcError, client.DaemonDown, OSError):
            return None

    def _apply_refs(self, got: tuple | None) -> None:
        if got is not None:
            self.tasks_pool, self.requests_pool = got

    def _read_can(self) -> dict | None:
        try:
            return api.call("agent.get", {"agent": self.agent})["can"]
        except (client.RpcError, client.DaemonDown, OSError):
            return None

    def _apply_can(self, can: dict | None) -> None:
        if can is not None:
            self.verbs_pool = tuple(v for v in CHAT_VERBS if can[v]["ok"])

    def refresh_agent_strip(self) -> None:
        self.query_one(SandboxBanner).refresh_state(self.agent, True)
        self._off_loop("strip", self._read_strip, self._apply_strip)

    def _read_strip(self) -> dict | None:
        try:
            return read_strip(self.agent)
        except (client.RpcError, client.DaemonDown, OSError):
            return None

    def _apply_strip(self, got: dict | None) -> None:
        if got is not None:
            self.query_one(AgentStrip).show(got)
            self._set_agent_state((got["screen_word"] or "IDLE", got["screen_detail"]))

    def refresh_view(self) -> None:
        self.refresh_agent_strip()
        self.refresh_block()
        self.refresh_pools()
        if self.showing_settings:
            return
        if not self.showing_tasks:
            self.refresh_conversation()

    def _read_requests(self, before: int | None = None, since: int | None = None, want: int | None = None,
                       limit: int | None = None) -> tuple[list[dict], bool, str] | None:
        """A page of this chat from the daemon: its items, whether older requests remain, and its version. None when the
        daemon cannot be read: what is on screen then stays."""
        try:
            got = read_requests(self.agent, self.everyone, before, since, want, limit)
        except (client.RpcError, client.DaemonDown, OSError) as e:
            self._read_error = str(e)
            return None
        self._read_error = None
        return got

    def _report_unread(self) -> None:
        """On the loop, after a read: say once that the chat cannot be read, not on every refresh."""
        if self._read_error is None:
            self._unread_daemon = False
        elif not self._unread_daemon:
            self._unread_daemon = True
            self.notify(f"the chat cannot be read: {self._read_error}", severity="error")

    def read_if_changed(self, hr: tuple, force: bool = False) -> tuple[list[dict], bool | None, str] | None:
        """The items to draw, whether older requests remain (None: not asked) and the chat's version, or None to leave the
        screen as it is: the daemon cannot be read, or the version (one item read) and the pending HR cards are what
        `_sig` has. Reads only: it runs on a worker thread."""
        if self.oldest is not None and not force and self._sig is not None:
            peek = self._read_requests(limit=1)
            if peek is None or (peek[2], hr) == self._sig:
                return None
        if self.oldest is None:
            # Opened on a message: page back until it is on screen.
            want = self._reveal[1] if self._reveal else self._focus[0] if self._focus else None
            got = self._read_requests(want=want)
            if got is None:
                return None
            items, more, version = got
        else:
            got = self._read_requests(since=self.oldest)
            if got is None:
                return None
            items, _, version = got
            more = None
        if (version, hr) == self._sig and not force:
            return None
        return items, more, version

    def refresh_conversation(self, force: bool = False) -> None:
        """Bring what is on screen up to date. Unchanged, this is one
        one-item read for the chat's version; changed, it re-reads only the requests already on screen and
        anything newer, never the whole history.

        `_sig` is forced dirty by the agent's word flipping between RUNNING
        and IDLE (`refresh_agent_state`), which happens often while it works
        and says nothing about what is on screen. So the transcript is only
        actually torn down and remounted when `_transcript_key` - what its
        rows, their states and the work line actually say - has changed,
        and the scroll only jumps to the bottom when the viewer was already
        there (founder #1975/T309): moving it while someone has scrolled up
        to read older messages would yank the page out from under them."""
        self._force_convo = self._force_convo or force
        self._off_loop("convo", self._read_convo, self._draw_convo)

    def _read_convo(self) -> tuple:
        """On a worker thread: the chat read for `refresh_conversation`."""
        force, self._force_convo = self._force_convo, False
        gen = self._gen
        hr = tuple((p["id"], p["state"]) for p in hr_card.pending())
        return gen, force, hr, self.read_if_changed(hr, force)

    def _draw_convo(self, got: tuple) -> None:
        gen, force, hr, read = got
        self._report_unread()
        if gen != self._gen:        # the scope changed under the read: it is of another view
            self.refresh_conversation(force)
            return
        if read is None:
            return
        items, more, version = read
        if more is not None:
            self.more = more
        self._sig = (version, hr)
        asks = [i["message"] for i in items if i["type"] == "request"]
        self.oldest = min((m["id"] for m in asks), default=None)
        msgs = messages_of(items, self.agent)
        # What is drawn is seen: it leaves FOR YOU. An answer counts by its own row.
        seen = max((max(m["id"], (m["answer"] or {}).get("id") or 0) for m in asks), default=0)
        if seen > self._seen:      # a write only when something new is drawn
            self._mark_seen(seen)
        why = wait_reason(msgs, self.agent_state[0] == "RUNNING")
        changes = changes_of(items, self.agent, msgs) + hr_cards(self.agent, msgs)
        state, detail = self.agent_state
        wait = detail if detail.startswith(WAITS) else ""
        key = (tuple((m["id"], m["state"], m["answer"] and m["answer"]["at"], m["delivered_at"], m["landed_at"], m["typed_at"],
                      m["dismissed"])
                     for m in asks),
               tuple((c["tasks"][0], c["when"], c["change"]) for c in changes if "change" in c),
               tuple((c["when"], c["session"]["ts"], c["session"]["cause"], bool(c["session"]["text"]))
                     for c in changes if "session" in c),
               tuple((c["hr"]["id"], c["hr"]["state"]) for c in changes if "hr" in c))
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
        self._rebuilding = True         # the emptied pane reads as "at its end" until laid out again
        pane.remove_children()
        widgets = transcript_widgets(with_task_changes(msgs, changes), state == "RUNNING", why, wait)
        pane.mount(*widgets, *(ShellCard(r) for r in self.shells))
        if at_bottom:
            pane.anchor()       # stays at its end as the new messages are laid out, until the reader scrolls
        mids = [mid_of(w.m) for w in widgets if isinstance(w, ChatMessage)]
        if self._newest is not None and not at_bottom:
            self._unseen += sum(1 for m in mids if m > self._newest)    # older pages loaded above are not news
        self._newest = max(mids + [self._newest or 0])
        self.call_after_refresh(self._rebuilt)
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

    def _mark_seen(self, seen: int) -> None:
        def write() -> None:
            if api.write("chat.seen", {"agent": self.agent, "up_to": seen})[0]:
                self._seen = max(self._seen, seen)      # a refused write is sent again on the next refresh
        self.run_worker(write, thread=True, group="seen", exit_on_error=False)

    def _rebuilt(self) -> None:
        self._rebuilding = False
        self._sync_newbar()
        self._fill_waits = 0
        self._fill_older()

    def _fill_older(self) -> None:
        """Short newest messages leave the view part empty: read older pages above them until it is full,
        or there are none (scrolling up is what loads them otherwise, and a view that fits cannot scroll)."""
        if self.showing_tasks or self.showing_settings:
            return
        pane = self.query_one("#transcript", VerticalScroll)
        shown = [w for w in pane.children if w.display]
        if not shown or any(w.outer_size.height == 0 for w in shown):
            if self._fill_waits < 20:       # just mounted: not laid out yet, so not measurable
                self._fill_waits += 1
                self.set_timer(0.05, self._fill_older)
            return
        # virtual_size is never below the view's own size, so the content is measured by its children
        if actions.needs_older(self.more, sum(w.outer_size.height for w in shown), pane.size.height):
            self.load_older()
            self.set_timer(0.05, self._fill_older)

    def _sync_newbar(self) -> None:
        """Shown only while the transcript is scrolled up from its end; reaching the end by hand clears the count."""
        if self._rebuilding:
            return
        pane = self.query_one("#transcript", VerticalScroll)
        up = not self.showing_tasks and not self.showing_settings and not pane.is_vertical_scroll_end
        if not up:
            self._unseen = 0
        bar = self.query_one("#newbar", NewBar)
        bar.display = up
        if up:
            n = self._unseen
            bar.update(f"{n} new message{'s' * (n != 1)} (click) ↓" if n else "↓ bottom")
            bar.set_class(bool(n), "new")

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        if action == "jump_bottom":
            return self.query_one("#newbar").display      # otherwise End is the box's own
        return True

    def action_jump_bottom(self) -> None:
        self._unseen = 0
        pane = self.query_one("#transcript", VerticalScroll)
        pane.scroll_end(animate=False)
        pane.anchor()
        self._sync_newbar()

    def load_older(self) -> None:
        """Read the page of requests just older than the oldest on screen and
        put it above, leaving the view where it was."""
        if not self.more or self.oldest is None:
            return
        got = self._read_requests(before=self.oldest)
        self._report_unread()
        if got is None:
            return
        items, self.more, _ = got
        ids = [i["message"]["id"] for i in items if i["type"] == "request"]
        if not ids:
            return
        self.oldest = min(ids)
        pane = self.query_one("#transcript", VerticalScroll)
        was_height, was_y = pane.virtual_size.height, pane.scroll_y
        first = pane.children[0] if pane.children else None
        pane.mount(*(ChatMessage(m) for m in messages_of(items, self.agent)), before=first)

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
            label, _, arg = sg.arg.partition(" ")
            self.run_verb(label, arg)
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
        """A `/` line is a command of the screen: do it, or say why not and keep the text."""
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
                Confirm.ask(self.app, self.app.exit, "Quit", f"{self.sending} message(s) still being sent.",
                            yes="Quit", no="Stay", danger=True)
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
        Confirm.ask(self.app, self.app.pop_screen, "Go back to the main screen",
                    "This leaves " + " and ".join(lost) + ".", yes="Go back", no="Stay")

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        self.everyone = event.tab.id == "all"
        self.showing_tasks = event.tab.id == "tasks"
        self.showing_settings = event.tab.id == "settings"
        self.refresh_agent_strip()
        chat = not (self.showing_tasks or self.showing_settings)
        if chat:
            self.last_chat = event.tab.id
            if self._scope_wait is None:
                save_scope(event.tab.id)
            elif event.tab.id == self._scope_wait:
                self._scope_wait = None     # the remembered tab is up; from here a pick is the human's
        self.oldest, self.more, self._sig, self._transcript_key = None, False, None, None
        self._gen += 1
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
        self.query_one("#settings_form", AgentSettings).load()

    def on_agent_settings_loaded(self, event: AgentSettings.Loaded) -> None:
        event.stop()
        self.query_one("#s_schedules", SchedulesSection).load()

    def action_show_message(self, rid: int) -> None:
        """Take the reader to message `rid`: scrolled to and lit for a moment here, paging back for it
        when it is older; in another agent's chat, pushed over this one; shown alone when no chat has it."""
        if self._light(rid):
            return
        try:
            at = self._located(rid)
        except (client.RpcError, client.DaemonDown, OSError) as e:
            self.notify(f"the message cannot be found: {e}", severity="error", timeout=6, markup=False)
            return
        if at.kind == "chat":
            self.app.push_screen(ChatPage(at.agent, reveal=(rid, at.request), tab=at.tab))
        elif at.kind == "here":
            self._reveal, self._reveal_tries = (rid, at.request), 0
            if at.tab == "all" and not self.everyone:
                self._scope_wait = "all"     # the tab is followed for this message, not remembered as a pick
                self.query_one("#scope", Tabs).active = "all"
            else:
                self.oldest, self._sig, self._transcript_key = None, None, None
                self._gen += 1
                self.refresh_conversation(force=True)
            self.set_timer(0.2, self._reveal_step)
        else:
            self._show_alone(rid)

    def _located(self, rid: int) -> Located:
        """Where `chat.locate` puts message `rid`, from this chat's point of view."""
        try:
            got = api.call("chat.locate", {"request": rid, "agent": self.agent, "limit": 1})
        except client.RpcError as e:
            if e.code == "not_found":
                return Located("alone")
            raise
        return Located("here" if got["agent"] == self.agent else "chat", got["message"]["id"], got["agent"], got["scope"])

    def _light(self, mid: int) -> bool:
        """Scroll to the message drawn for number `mid` and light it for a moment; False when none is."""
        target = next((w for w in self.query(ChatMessage) if mid in (w.m["rid"], w.m.get("mid"))), None)
        if target is None:
            return False
        target.scroll_visible(animate=False)
        target.add_class("flash")
        self.set_timer(1.5, lambda: target.remove_class("flash"))
        return True

    def _reveal_step(self) -> None:
        """The message a click asked for, once the page is drawn with it: the widgets mount a moment after
        the read, so this looks again a few times before it gives the message up as one no chat draws."""
        if not self._reveal:
            return
        mid = self._reveal[0]
        if self._light(mid):
            self._reveal = None
            return
        self._reveal_tries += 1
        if self._reveal_tries > 25:
            self._reveal = None
            self._show_alone(mid)
            return
        self.set_timer(0.2, self._reveal_step)

    def _show_alone(self, mid: int) -> None:
        """Message `mid` in a modal, for one no chat draws: locate's not_found carries it, and a found one
        that no widget drew arrives as the located message."""
        try:
            m = api.call("chat.locate", {"request": mid, "agent": self.agent, "limit": 1})["message"]
        except client.RpcError as e:
            if e.code != "not_found" or "body" not in e.data:
                self.notify(f"#{mid}: there is no such message", severity="warning")
                return
            m = e.data
        except (client.DaemonDown, OSError) as e:
            self.notify(f"the message cannot be found: {e}", severity="error", timeout=6, markup=False)
            return
        if m.get("id", mid) == mid:
            who, body = (m["from"], m["to"]), m["body"]
        elif (m.get("answer") or {}).get("id") == mid:
            who, body = (m["to"], m["from"]), m["answer"]["text"]
        else:
            self.notify(f"#{mid}: it is in this chat but cannot be drawn", severity="warning")
            return
        self.app.push_screen(HandoffModal(f"#{mid}  {who[0]} \u2192 {who[1]}", body))

    def action_open_task(self, tid: int) -> None:
        open_task_page(self.app, tid)

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

    def _to_chat(self) -> None:
        self.query_one("#scope", Tabs).active = self.last_chat

    def action_back(self) -> None:
        menu = self.query_one("#menu", OptionList)
        if self.showing_settings:
            problems = self.query_one("#settings_form", AgentSettings).settle()
            if problems:
                Confirm.ask(self.app, self._to_chat, "Leave the settings without saving", "Not saved: " + "; ".join(problems),
                            yes="Leave", no="Stay")
            else:
                self._to_chat()
        elif self.showing_tasks:
            self.query_one("#scope", Tabs).active = self.last_chat
        elif menu.display or self.history_mode:
            self._menu_closed = True
            self.history_mode = False
            menu.display = False
        elif self.replying is not None:
            self.reply_to(None)
        elif self.query_one("#compose", ChatBox).text.strip():
            box = self.query_one("#compose", ChatBox)
            now = time.monotonic()
            if actions.esc_pressed_twice(self._esc_at, now):
                self._esc_at = None
                box.clear()
            else:
                self._esc_at = now
                self.notify("press Esc again to clear what you typed", timeout=actions.ESC_CLEAR_WINDOW_S)
        elif not self.query_one("#compose", ChatBox).text.strip():
            running = [r for r in self.shells if r.running]
            if running:         # Esc stops what is running before it leaves the page
                for r in running:
                    r.stop()
                return
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
        shell = actions.command_of(body)
        if shell is not None:
            self.run_shell(body, shell)
            return
        what = slash_action(body)
        if what is not None:
            self.run_slash(what)
            return
        rid = mid_of(self.replying.m) if self.replying else None
        self.start_send(body, rid, agent=self.replying.m["agent"] if self.replying else None)

    def run_shell(self, body: str, command: str) -> None:
        """A `!` line is not sent: zsh runs it in the agent's worktree and a card shows the result."""
        cwd = api.call("agent.get", {"agent": self.agent})["cwd"]
        try:
            run = actions.ShellRun(command, cwd)
        except PermissionError as e:
            self.notify(safe(str(e)), severity="error", timeout=6, markup=False)
            return
        self.history = remember(self.history, body.strip())
        save_history(self.agent, self.history)
        self.hist_pos, self.history_mode = None, False
        self.query_one("#compose", ChatBox).text = ""
        self.shells.append(run)
        pane = self.query_one("#transcript", VerticalScroll)
        pane.mount(ShellCard(run))
        pane.scroll_end(animate=False)

    def on_unmount(self) -> None:
        for r in self.shells:       # a command left running would outlive the page that shows it
            r.stop()

    def on_shell_card_send(self, event: ShellCard.Send) -> None:
        event.stop()
        event.run.sent = True
        self.start_send(event.run.as_message(), None, typed=False)

    def send_now(self, rid: int) -> None:
        def work() -> None:
            ok, said = api.write("chat.send_now", {"request": rid})
            self.app.call_from_thread(self._sent_now, ok, said)
        self.run_worker(work, thread=True, group="send_now", exit_on_error=False)

    def _sent_now(self, ok: bool, said: str) -> None:
        if self.is_attached:
            self.toast(said, ok)
            self.refresh_conversation(force=True)

    def on_hr_card_decided(self, event: hr_card.HrCard.Decided) -> None:
        self.toast(textsafe.oneline(event.said), event.ok)
        self.refresh_conversation(force=True)

    def cancel_message(self, rid: int, body: str) -> None:
        """Take request `rid` back and put its text in the box. Not yet
        delivered, nobody is told and it just goes; delivered, the agent is
        told to stop, so the daemon's plan is shown and asked first."""
        self._cancel(rid, body, False)

    def _cancel(self, rid: int, body: str, confirm: bool) -> None:
        def work() -> None:
            state, said = api.write_asking("chat.cancel", {"request": rid, **({"confirm": True} if confirm else {})})
            self.app.call_from_thread(self._cancel_answered, rid, body, state, said)
        self.run_worker(work, thread=True, group="cancel", exit_on_error=False)

    def _cancel_answered(self, rid: int, body: str, state: str, said: str) -> None:
        if not self.is_attached:
            return
        if state == "confirm":
            Confirm.ask(self.app, lambda: self._cancel(rid, body, True), f"Cancel #{rid}", said,
                        yes="Cancel it", no="Keep", danger=True)
        else:
            self._cancelled(said if state == "ok" else f"not cancelled - {said}", body)

    def _restore(self, body: str) -> None:
        box = self.query_one("#compose", ChatBox)
        box.text = body
        box.move_cursor(box.document.end)
        box.focus()

    def _cancelled(self, said: str, body: str) -> None:
        ok = not said.startswith("not cancelled")
        self.toast(said, ok)
        if ok:
            box = self.query_one("#compose", ChatBox)
            if not box.text.strip():
                self._restore(body)
        self.refresh_conversation(force=True)

    def start_send(self, body: str, rid: int | None, typed: bool = True, agent: str | None = None) -> None:
        """Hand `body` to a worker thread and give the box back at once: a send
        can wait a long while on an agent that is off or restarting, and the
        screen must not wait with it. The message shows as pending until the
        worker answers; a refusal puts the text back in the box. A `body` that was
        not typed (a shell result) leaves the box, its history and the reply alone."""
        if typed:
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
        rec = Pending(pending, body, typed)
        self.pending.append(rec)
        self.sending += 1
        self._spin()

        def work() -> None:
            with self._send_lock:
                ok, said = send_reply(agent or self.agent, rid, body)
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
        if not ok and rec.typed:
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
