"""The conversation with one agent: rows, replies, unread."""
from __future__ import annotations

import time

from ..team import ask as askmod
from ..infra import timefmt
from .. import db
from ..session import waiting

from .requests import _is_answer_to_human, answer, answer_ok, message_ok
from .routing import HUMAN


# ------------------------------------------------------------ the conversation
# Chains shown in the tab, newest last. A chain is a thread; an agent that has
# been at work for a week has more of them than a page can hold.
CHAT_CHAINS = 8
CHAT_TAB = "CONVERSATION"
OTHERS_TAB = "ALSO QUEUED FOR IT"
UNTOLD_TAB = "ANSWERS IT HAS NOT BEEN TOLD"


def _chat_rows(con, agent: str) -> list:
    """Every row of every chain the human and `agent` have a row in."""
    return con.execute(
        "SELECT id, ts, resolved_at, requester, target, state, body, verdict, "
        "dismissed_by, dismissed_reason, chain_id, delivered_at, fyi FROM requests "
        "WHERE state != 'denied' AND chain_id IN ("
        "  SELECT chain_id FROM requests WHERE "
        "  (requester = ? AND target = ?) OR (requester = ? AND target = ?)) "
        "ORDER BY id", (HUMAN, agent, agent, HUMAN)).fetchall()


class Row(str):
    """A row of an agent's page that the screen lays out from its fields.

    As a string it is the plain reading, which is all a caller that only
    searches needs. The fields are what the bridge draws from at the width the
    terminal has now, so a row is wrapped where it is drawn and its text never
    passes through anything that styles it.
    """


class Fact(Row):
    """Labelled values that belong together: `account alpha   model sonnet`."""
    pairs: list[tuple[str, str]]

    def __new__(cls, *pairs: tuple[str, str]):
        self = super().__new__(cls, "   ".join(f"{k} {v}" for k, v in pairs))
        self.pairs = list(pairs)
        return self


class Entry(Row):
    """A request as a block: its number, who it is with, and all of its text."""
    id: int
    who: str
    text: str
    ts: int

    def __new__(cls, id: int, who: str, text: str, ts: int):
        self = super().__new__(cls, f"#{id} from {who}: {text}")
        self.id, self.who, self.text, self.ts = id, who, text, ts or 0
        return self


class Event(Row):
    """One thing the daemon or the agent did, with its whole payload."""
    ts: int
    kind: str
    payload: str

    def __new__(cls, ts: int, kind: str, payload: str):
        self = super().__new__(
            cls, f"{timefmt.stamp(ts, '%H:%M:%S')}  {kind:<18}{payload}")
        self.ts, self.kind, self.payload = ts or 0, kind, payload
        return self


class Said(Row):
    """One row of a conversation: a message, or the rule that starts a thread.
    As a string it is who, then what."""
    kind: str          # "msg" or "thread"
    who: str           # the speaker; "a → b" between two agents
    side: str          # "you", "agent" (the one this tab is about) or "other"
    text: str
    ts: int
    tail: str          # its delivery state, or the request an answer closes
    waiting: bool      # the agent is waiting on the human for it
    rid: int | None    # the request it is the ask of (part 0) or the verdict of (part 1)
    part: int

    def __new__(cls, kind: str, who: str, text: str = "", side: str = "", ts: int = 0,
                tail: str = "", waiting: bool = False, rid: int | None = None, part: int = 0):
        self = super().__new__(cls, f"{who}  {text}" if kind == "msg" else who)
        self.kind, self.who, self.side, self.text = kind, who, side, text
        self.ts, self.tail, self.waiting = ts, tail, waiting
        self.rid, self.part = rid, part
        return self


def conversation_and_drawn(con, agent: str, hint: bool = True) -> tuple[list[str], set[int]]:
    """The human's conversation with `agent`, in time order, a thread at a time,
    and the ids of the requests it drew - only the newest CHAT_CHAINS chains
    are drawn, so a request in an older one is not in it.

    A thread is a chain_id. Every row of a chain is drawn, not only the rows
    between the two of them, so what the agent asked a third agent on the way to
    answering is visible where it happened. One row is two messages - the ask
    and, once closed, its verdict - and each is placed at its own time.

    `hint` is False for a screen whose own input box already says where a
    message goes - a trailing "write to ..." line drawn under the conversation
    would be a second, contradicting one.
    """
    chains: dict[str, list] = {}
    for r in _chat_rows(con, agent):
        chains.setdefault(str(r["chain_id"]), []).append(r)

    def said(speaker: str, to: str, text, ts, tail: str = "", waiting: bool = False,
             rid: int | None = None, part: int = 0) -> Said:
        # What is not between the human and this agent is neither's: the agent
        # asking a third agent is drawn as that, not as the agent speaking to them.
        between = HUMAN in (speaker, to)
        side = "you" if speaker == HUMAN else "agent" if between else "other"
        who = "you" if speaker == HUMAN else speaker if between else f"{speaker} → {to}"
        body = (text or "").strip("\n")
        return Said("msg", who, body if body.strip() else "(empty)", side, ts or 0, tail,
                    waiting, rid, part)

    threads = []
    for cid, rows in chains.items():
        msgs = []
        for r in rows:
            tail = ""
            if r["state"] == "queued":
                tail = ("waiting for you" if r["target"] == HUMAN else
                        f"delivered {timefmt.stamp(r['delivered_at'], timefmt.CLOCK)}"
                        if r["delivered_at"] else "queued")
                if r["target"] == agent:
                    tail += f" · #{r['id']}"       # what /cancel names
            msgs.append((r["ts"], r["id"], 0,
                         said(r["requester"], r["target"], r["body"], r["ts"], tail,
                              r["state"] == "queued" and r["target"] == HUMAN, r["id"], 0)))
            shown = db.shown_verdict(r)
            if shown:
                at = r["resolved_at"] or r["ts"]
                msgs.append((at, r["id"], 1, said(r["target"], r["requester"], shown,
                                                  at, f"answers #{r['id']}",
                                                  rid=r["id"], part=1)))
        msgs.sort(key=lambda m: m[:3])
        threads.append((max(m[0] for m in msgs), cid, msgs))
    threads.sort(key=lambda t: t[0])
    out: list[str] = []
    drawn: set[int] = set()
    for _, cid, msgs in threads[-CHAT_CHAINS:]:
        out.append(Said("thread", f"── chain {cid}"))
        out += [m[3] for m in msgs]
        drawn |= {m[1] for m in msgs}
    if not out:
        out.append(f"nothing yet between you and {agent}")
    if hint:
        how, rid = chat_target(con, agent)
        goes = {"answers": f" · answers #{rid}", "new": " · starts a new thread"}[how]
        out += ["", f"↵ write to {agent}{goes} · e in $EDITOR"]
    return out, drawn


def message_place(con, rid: int) -> tuple[str, int] | None:
    """Where a message of the mailbox sits in a conversation: the agent it is
    with, and whether it is the ask (0) or the verdict (1) of its row. None when
    the row is gone or the conversation does not draw it - it is in a chain older
    than the ones drawn, or it says nothing to draw."""
    row = db.get_request(con, rid)
    if row is None:
        return None
    answer = _is_answer_to_human(row)
    agent = row["target"] if answer else row["requester"]
    part = 1 if answer else 0
    rows, _ = conversation_and_drawn(con, agent)
    if any(getattr(r, "rid", None) == rid and r.part == part for r in rows):
        return agent, part
    return None


def chat_parent(con, agent: str) -> int | None:
    """The row a new message from the human goes under: the latest one between them."""
    row = con.execute(
        "SELECT MAX(id) AS m FROM requests WHERE state != 'denied' AND "
        "((requester = ? AND target = ?) OR (requester = ? AND target = ?))",
        (HUMAN, agent, agent, HUMAN)).fetchone()
    return int(row["m"]) if row and row["m"] else None


def chat_target(con, agent: str) -> tuple[str, int | None]:
    """The row a message from the human in this tab goes to, and what it does there.

    ('answers', id): the oldest question the agent has open for the human. It is
    parked on that row, so a reply is its verdict - an ask under it would leave
    it queued and the agent waiting. A report (fyi) is not a question, and
    neither is the human's own row, so neither can be what a reply answers.
    ('new', None): otherwise - a message typed without picking one to reply to (founder #2622)
    starts a thread of its own, under nothing.
    """
    q = con.execute(
        "SELECT MIN(id) AS m FROM requests WHERE requester = ? AND target = ? "
        "AND state = 'queued' AND fyi = 0", (agent, HUMAN)).fetchone()
    if q and q["m"]:
        return "answers", int(q["m"])
    return "new", None


def chat_reply(con, agent: str, body: str) -> str:
    """Send the human's message where `chat_target` says it goes."""
    return chat_reply_ok(con, agent, body)[1]


def chat_reply_ok(con, agent: str, body: str) -> tuple[bool, str]:
    """`chat_reply`, and whether it went."""
    if not body.strip():
        return False, "nothing sent: no message typed"
    how, rid = chat_target(con, agent)
    if how == "new":
        return message_ok(con, agent, body)
    return answer_ok(con, rid, body)


def _seen_key(agent: str) -> str:
    return f"seen:{agent}"


def chat_unread(con, agent: str) -> int:
    """What `agent` has said to the human since the tab was last open.

    Counted from a time, not a row id: an answer lands on an old row, so ids
    arrive out of order and an id cursor would step past the older one for good.
    Times are whole seconds, so what lands in the very second the tab was marked
    counts as read; the other way, what was just read would stay new. A
    dismissal counts: the conversation draws it as a new line.
    """
    seen = db.get_cursor(con, _seen_key(agent))
    row = con.execute(
        "SELECT COUNT(*) AS n FROM requests WHERE state != 'denied' AND "
        "((requester = ? AND target = ? AND ts > ?) OR "
        " (requester = ? AND target = ? AND (verdict IS NOT NULL OR dismissed_by IS NOT NULL) "
        "  AND resolved_at > ?))",
        (agent, HUMAN, seen, HUMAN, agent, seen)).fetchone()
    return int(row["n"])


def chat_seen(con, agent: str) -> None:
    db.set_cursor(con, _seen_key(agent), int(time.time()))
