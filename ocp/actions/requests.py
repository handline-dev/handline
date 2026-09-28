"""Requests as a screen shows and answers them: messages, the founder's mailbox, answer, dismiss, cancel."""
from __future__ import annotations

import time

from ..team import ask as askmod
from ..infra import timefmt
from ..session import blocked
from .. import db
from ..team import hr
from ..session import waiting

from .routing import HUMAN


def message(con, agent: str, body: str) -> str:
    """Send an agent work, as the human."""
    return message_ok(con, agent, body)[1]


def message_ok(con, agent: str, body: str) -> tuple[bool, str]:
    """`message`, and whether it went: what is typed is kept unless it did."""
    if not body.strip():
        return False, "nothing sent: no message typed"
    ok, msg, _ = askmod.ask(agent, body, requester=HUMAN)
    return ok, msg


def answer(con, rid: int, verdict: str) -> str:
    return answer_ok(con, rid, verdict)[1]


def answer_ok(con, rid: int, verdict: str) -> tuple[bool, str]:
    """Reply to one row of the human's mailbox, whichever way it is pointing.

    Two rows land in that mailbox and the same key has to work on both. A
    request *to* the human is closed with a verdict, which is what the agent
    parked on. An answer to something the human asked is already closed, so
    replying to it is a new request back to the agent that answered - in that
    answer's thread, which is the one place in this system where a parent id is
    known for certain rather than guessed at.
    """
    if not verdict.strip():
        return False, "nothing sent: no answer typed"
    row = db.get_request(con, rid)
    if row is None:
        return False, f"#{rid} is gone"
    if _is_answer_to_human(row):
        ok, msg, _ = askmod.ask(row["target"], verdict, requester=HUMAN, parent_id=rid)
        if ok:
            # Replied to is read: leaving it in the mailbox would ask the human
            # to dismiss what they have just answered.
            db.mark_announced(con, [rid])
        return ok, msg
    refusal = check_open_proposal(rid, row, verdict)
    if refusal:
        return False, refusal
    return askmod.resolve(rid, verdict)


def check_open_proposal(rid: int, row, verdict: str) -> str | None:
    """The refusal for closing a queued roster-proposal escalation with
    anything that is not a decision; None when it may go ahead.

    `ocp resolve` reaches a queued row the same way `answer()` does - the
    founder targeting a row that is theirs to close - and a proposal's
    requester is the proposing agent, never HUMAN, so `check_human_resolve`
    does not see it. Both callers need this same check, or one of them is
    the silent non-answer this guard exists to prevent (#1770, #1893 SF1).
    """
    if row["state"] == "queued" and hr.is_open_proposal(rid) \
            and hr.decision(verdict) == "unclear":
        return f"#{rid} is a roster proposal - answer `approve` or `reject`"
    return None


def record_human_resolve(con, rid: int, tasks: list[int] | None, no_task: str | None,
                         me: str | None) -> None:
    """What a guarded close leaves behind: the links (--task), or the
    reason nothing was built (--no-task)."""
    for tid in tasks or []:
        db.link_request(con, tid, rid)
    if no_task:
        db.add_event(con, me or "", "no_task", {"request_id": rid, "reason": no_task})


def dismiss(con, rid: int, me: str | None = None, reason: str | None = None,
            tasks: list[int] | None = None, no_task: str | None = None) -> tuple[bool, str]:
    """Take one row off the mailbox without replying to it. A still-queued
    row is closed as dismissed - by `me`, with `reason` as typed, at the
    time resolved_at holds - and carries no verdict, so nothing draws it as
    anyone's words; a row that already carries a real verdict keeps it.

    A row that has already left 'queued' carries somebody's real verdict.
    Read by whoever asked for it (`me`, when known), it is marked told - the
    same thing the daemon does when it reads an answer out to an agent - and
    that marking is idempotent, so dismissing the same row twice is as
    harmless as once. Read instead by the one who *wrote* that verdict (the
    target dismissing its own answered request), there is nothing left to
    do: stamping it told would announce it on the real requester's behalf,
    and the requester would never actually be told.

    Returns (ok, message): a guard refusal is the only `False` here, so a
    caller reading it as an exit code does not conclude a founder's row was
    handled when it is still sitting in the queue.
    """
    row = db.get_request(con, rid)
    if row is None:
        return True, f"#{rid} is gone"
    guarded = row["state"] == "queued" and row["requester"] == HUMAN
    if guarded or tasks:
        refusal = check_human_resolve(con, row, tasks, no_task, me)
        if refusal:
            return False, refusal
    if row["state"] == "queued" and hr.is_open_proposal(rid):
        # "read" would close it with a verdict that is neither approve nor
        # reject, leaving a proposal nobody can apply or clear.
        return False, (f"#{rid} is a roster proposal - answer `approve` or `reject`, "
                       f"it cannot be dismissed")
    if row["state"] != "queued":
        if me is not None and me == row["target"]:
            return True, f"#{rid} was already answered - nothing more for you to do"
        db.mark_announced(con, [rid])
        return True, f"#{rid} marked read"
    by = me or "unknown"
    if guarded or tasks or no_task:
        # Linked before the row is closed: a failure between the two leaves a
        # link on a still-queued row, never an answered row attached to nothing.
        record_human_resolve(con, rid, tasks, no_task, me)
    # Closed, not announced: a dismissal is not an answer, so nothing is typed
    # at the asker. An async asker that was parked goes back to idle
    # (`_after_close`), and resolve() stamps announced_at in the same write as
    # the state change.
    ok, msg = askmod.resolve(rid, None, state="done", notify=False,
                             dismissed_by=by, dismissed_reason=reason)
    return ok, f"{msg} (dismissed; {row['requester']} is not told)"


def waiting_ids(con, agent: str) -> list[int]:
    """Every request queued for an agent, which is what /cancel can name."""
    return [int(r["id"]) for r in db.queued_for(con, agent)]


def cancel_plan(con, rid: int) -> tuple[bool, str]:
    """What cancelling this request would do, and who is told: the sentence a
    confirm shows before anything is closed."""
    row = db.get_request(con, rid)
    if row is None:
        return False, f"#{rid} is gone"
    if row["state"] != "queued":
        return False, f"#{rid} is already {row['state']}"
    target, requester = row["target"], row["requester"]
    if requester == HUMAN and not row["delivered_at"]:
        return True, f"withdraw #{rid} to {target}: not delivered yet, so nobody is told"
    behind = [q for q in db.queued_for(con, target) if q["id"] != rid]
    told = ([f"{target} is told to stop"] if row["delivered_at"] else []) + [
        "you are not told" if requester == HUMAN
        else f"{requester} is told '{askmod.CANCEL_VERDICT}'"]
    return True, (f"cancel #{rid} ({'delivered to' if row['delivered_at'] else 'queued for'} "
                  f"{target}): closed as rejected, '{askmod.CANCEL_VERDICT}'; "
                  + ", ".join(told)
                  + (f"; #{behind[0]['id']} delivers next" if row["delivered_at"] and behind
                     else ""))


def cancel(con, rid: int) -> str:
    """Take a request out of an agent's queue as the founder. See `ask.cancel`."""
    ok, msg = askmod.cancel(rid)
    return msg if ok else f"not cancelled - {msg}"


def send_now(con, rid: int) -> str:
    """The founder's queued message is told to its agent now if the agent can be typed
    at, else as soon as it can, even with a request already open ahead of it."""
    row = db.get_request(con, rid)
    if not row or row["requester"] != HUMAN or not db.mark_send_now(con, rid):
        return "not sent - it is no longer waiting"
    con.commit()
    return askmod.push(con, row["target"]).strip()


def check_human_resolve(con, row, tasks: list[int] | None, no_task: str | None,
                        me: str | None = None) -> str | None:
    """The refusal for resolving a request the human made without saying what
    it becomes; None when it may go ahead. Any --task id is checked on every
    row, before anything is written, so a bad id never follows an answer that
    has already gone out.

    The guard is on the answer, not on the ask: agent-to-agent and
    manager-to-agent rows are conversation and never reach it, and it covers
    replies inside a running thread, because that is how the founder attaches
    the next piece of work to a report. Every path that closes such a row on
    an agent's behalf goes through here (`ocp resolve`, `ocp dismiss`); the
    founder's own hands - the bridge answer key, cancel, withdraw - are the
    person the guard exists to serve, so `me == HUMAN` passes.
    """
    for tid in tasks or []:
        t = db.get_task(con, tid)
        if t is None:
            return f"no task T{tid}"
        if t["closed_by"]:
            return f"task T{tid} is already closed ({t['closed_by']})"
    if row["requester"] != HUMAN or me == HUMAN:
        return None
    if bool(tasks) == bool(no_task):
        return (f"#{row['id']} is the founder's: say what it becomes. --task T<n> [T<n> ...] "
                "names the tasks this answer belongs to (one per deliverable "
                "promised), --no-task 'reason' says nothing needs building. "
                "`ocp task new --owner NAME 'title' -t 2h` makes a task.")
    return None


def _is_answer_to_human(row) -> bool:
    """Is this row an answer that came back to the human, rather than a request?

    The human asked it and it is no longer queued, so what it holds now is the
    agent's verdict. The distinction decides what the reply key does and what
    dismissing writes, so it is read off the row rather than inferred from
    which panel the cursor was in.
    """
    return row["requester"] == HUMAN and row["state"] != "queued"


def mailbox_rows(con) -> list[dict]:
    """The human's whole mailbox, one row per request or answer - unfolded.

    Three things arrive here and they are not the same errand. An escalation is
    a decision the routing says only a human makes, and nothing else in this
    system will ever clear it. A request sent `async` asked for a reply and its
    sender parked on one. A report asked for neither. Which of those a row is
    comes from what the routing recorded when it was made, not from reading the
    text.

    The fourth is not a request at all: it is an answer to something the human
    asked. `ocp resolve` writes the verdict on the row and the daemon reads it
    out to the agent that asked; the human has no terminal to be read out to, so
    their answers have to be read here or nowhere. Half a mailbox teaches the
    team to talk around it - with this one drawing only one direction, two
    agents had learnt to send a second, unrelated request repeating what they
    had already answered.
    """
    out = []
    for r in con.execute(
        "SELECT id, ts, requester, target, kind, response, body, parent_id, fyi "
        "FROM requests WHERE target = ? AND state = 'queued' ORDER BY id",
        (HUMAN,),
    ).fetchall():
        # Both rows are built by hand and to one shape: a reader of this list
        # must not have to know which query a row came out of, and "from" is
        # the sender of a request one way and the answerer the other.
        #
        # `gate` and `waiting` are facts the routing recorded when the request
        # was made. A gate is a decision the design says only a human makes.
        # `waiting` says the sender asked for a reply and parked on it - which
        # is what it did at the time; whether it is still parked now is the
        # agent's own state, and the screen says what the row records rather
        # than reading one as the other. A report is `fyi` by definition
        # asking for no reply, whatever response the routing happened to pick
        # for that sender's other traffic - `response == "async"` alone would
        # let one hand-edited routing line pin every report from an agent and
        # switch the fold off with nothing failing.
        out.append({
            "id": r["id"], "ts": r["ts"], "from": r["requester"],
            "kind": r["kind"], "body": r["body"], "parent_id": r["parent_id"],
            "gate": r["kind"] == "escalate",
            "waiting": r["response"] == "async" and not r["fyi"],
            "answer": False,
        })

    for r in db.unannounced_answers(con, HUMAN):
        out.append({
            "id": r["id"],
            # When the answer landed, not when the question was asked. Those
            # are two days apart on this machine's oldest rows.
            "ts": r["resolved_at"] or r["ts"],
            "from": r["target"],
            # What the row's own state says came back. A refusal is an answer -
            # it is the one that means do something else - and drawing it as an
            # answer would lose the only word that distinguishes them.
            "kind": "answer" if r["state"] == "done" else "refused",
            "body": db.shown_verdict(r) or "(answered with nothing written)",
            "gate": False,
            "waiting": False,
            "answer": True,
            "parent_id": r["parent_id"],
        })
    return out


def fold_key(agent: str) -> str:
    """The stable key a folded line for `agent` carries - see `messages`."""
    return f"agent:{agent}"


def messages(con) -> list[dict]:
    """The human's mailbox, folded to one line per agent - like a chat app,
    not a mail client, so the list is as long as the team rather than as long
    as the traffic.

    A gate is a decision only the human makes, and a row parked waiting on a
    reply is owed one - both stop work until he acts, so both keep their own
    line even when the same agent has said something newer. Everything else -
    a report that asked nothing, an answer to what he asked - is not a decision
    pending on him the way those two are, so it folds: one line per agent,
    carrying that agent's latest, with `unread` counting what it stands in for.

    Every row also carries `key`, a string stable across a fold that its `id`
    is not: `id` is still the latest thing in the row, since that is what
    opening or acting on it means, but a screen that tracked a row by `id`
    would fall off it the moment a newer message became the latest - the row
    is still there, only its `id` changed under the cursor. `key` names the
    row itself - the agent, for a folded line, since folding is what made an
    agent one line in the first place - so it survives being updated in place.

    Decisions first, then the ones that asked for a reply, then the rest -
    oldest first inside each, because the order things arrived in is not the
    order they matter in. A folded row sorts by the latest thing in it, which
    is what a reader who has not looked in a while cares about.
    """
    raw = mailbox_rows(con)
    # A pinned row is never folded, so its id is already its stable identity -
    # the key that changes is only the one a fold can hand a new id.
    pinned = [dict(m, unread=1, key=m["id"]) for m in raw if m["gate"] or m["waiting"]]
    groups: dict[str, list[dict]] = {}
    for m in raw:
        if m["gate"] or m["waiting"]:
            continue
        groups.setdefault(m["from"], []).append(m)
    folded = []
    for agent, items in groups.items():
        # (ts, id): ties - two rows landing in the same second - break toward
        # the row created after, since a later id is a later fact whatever the
        # clock's resolution says.
        items.sort(key=lambda m: (m["ts"], m["id"]))
        row = dict(items[-1], unread=len(items), key=fold_key(agent))
        folded.append(row)
    out = pinned + folded
    return sorted(out, key=lambda m: (not m["gate"], not m["waiting"], m["ts"], m["id"]))


def founder_waits(con) -> list[dict]:
    """Everything that stops until the founder does something, oldest first.

    Each is {kind, who, what, since, red}. A permission prompt and an
    unanswered escalation are the founder's move (red). A roster proposal is
    shown from the moment it is raised until it is applied or refused: once
    approved it is waiting on whoever applies it, so it stays but not in red.
    """
    out: list[dict] = []
    for row in db.all_agent_states(con):
        w = waiting.of(con, row)
        if w:
            out.append({"kind": "permission", "who": row["agent"], "red": True,
                        "what": " ".join(x for x in (f"{w.get('tool') or 'tool'}:",
                                                      w.get("command")) if x),
                        "since": int(w["since"])})

    out += blocked.founder_rows(con, int(time.time()))
    # Its request is the same fact a second time, said where the founder is not
    # looking; the row above already is.
    proposed: set[int] = {int(b["request_id"]) for b in db.blocks(con) if b["request_id"]}
    for prop in hr.listing():
        if prop.get("state") != "proposed":
            continue
        req = db.get_request(con, int(prop.get("request") or 0))
        if req is None:
            continue
        proposed.add(int(req["id"]))
        if req["state"] == "queued":
            how, red = "awaiting your decision", True
        elif req["state"] == "done":
            call = hr.decision(req["verdict"])
            if call == "rejected":
                continue
            how, red = (("approved, not yet applied", False) if call == "approved"
                        else ("answered, but not approve or reject", True))
        else:
            continue
        out.append({"kind": "proposal", "who": prop.get("requested_by") or "?", "red": red,
                    "what": f"hr {prop['kind']} {prop['agent']} - {how}",
                    "since": int(req["ts"])})

    for r in con.execute(
        "SELECT id, ts, requester, body FROM requests WHERE target = ? "
        "AND kind = 'escalate' AND state = 'queued' ORDER BY id", (HUMAN,)).fetchall():
        if int(r["id"]) in proposed:
            continue
        out.append({"kind": "escalation", "who": r["requester"], "red": True,
                    "what": f"#{r['id']} {first_line(r['body'])}", "since": int(r["ts"])})
    return sorted(out, key=lambda w: (not w["red"], w["since"]))


def opened_by_human(con, rid: int) -> None:
    """Reading a report or an answer is what clears it: neither asks anything.

    No verdict is written. A report is not a question, so being read is not an
    answer - and a verdict here would be one the sending agent can read back
    (`ocp read`, its own conversation), making it look as though the human had
    said something they never typed. An answer to the human is already closed;
    reading it only stops it being listed, as `dismiss` does for a closed row.
    """
    row = db.get_request(con, rid)
    if row and row["fyi"] and row["target"] == HUMAN:
        db.retire_queued(con, rid, "done", None)
    elif row and _is_answer_to_human(row):
        db.mark_announced(con, [rid])


def message_detail(con, rid: int) -> tuple[str, list[tuple[str, list[str]]]] | None:
    """One message opened, with what it is a reply to underneath it.

    A verdict is unreadable alone: "**MERGE.**" is a complete answer and says
    nothing without the branch it is about. What a message answers is known
    exactly for a row the human asked - it is that row's own body - and for
    anything else the exchange with that agent, in order, is the nearest true
    thing: who said what to whom, not a guess at which sentence replies to
    which.
    """
    row = db.get_request(con, rid)
    if row is None:
        return None
    answer = _is_answer_to_human(row)
    other = row["target"] if answer else row["requester"]
    when = time.strftime("%Y-%m-%d %H:%M",
                         time.localtime((row["resolved_at"] if answer else None) or row["ts"]))
    kind = ("answer" if row["state"] == "done" else "refusal") if answer \
        else (row["kind"] or "ask")
    title = f"#{rid} from {other} · {kind} · {when}"
    text = (db.shown_verdict(row) if answer else row["body"]) or ""
    sections: list[tuple[str, list[str]]] = [("", text.splitlines() or ["(empty)"])]

    quoted = {rid}
    if answer:
        asked = timefmt.stamp(row["ts"], timefmt.SHORT)
        sections.append((f"WHAT YOU ASKED · {asked}",
                         _clip((row["body"] or "").splitlines(), rid)))
    elif row["parent_id"]:
        parent = db.get_request(con, row["parent_id"])
        if parent is not None:
            quoted.add(int(parent["id"]))
            sections.append((f"IN REPLY TO #{parent['id']}",
                             _clip((parent["body"] or "").splitlines(), parent["id"])))

    # What is already quoted above is not also listed below: the same message
    # twice on one page reads as two messages.
    earlier = _exchange(con, other, quoted)
    if earlier:
        sections.append((f"EARLIER WITH {other.upper()}", earlier))
    return title, sections


# How much of a quoted message is shown before it is standing in the way of the
# one being read. `ocp read` shows any of them whole.
QUOTE_LINES = 20


def _clip(lines: list[str], rid: int) -> list[str]:
    if len(lines) <= QUOTE_LINES:
        return lines or ["(empty)"]
    return lines[:QUOTE_LINES] + [f"… {len(lines) - QUOTE_LINES} more lines - `ocp read {rid}`"]


# How far back the exchange with one agent is drawn. Enough to place a verdict;
# not so much that the message being read scrolls off its own page.
EXCHANGE_ROWS = 6


# How much of a line of the exchange is drawn. These are index lines - one per
# thing said - and an agent's report is often one paragraph with no newline in
# it, so its "first line" is the whole report and would wrap across the page it
# is meant to be an index of.
EXCHANGE_WIDTH = 60


def _exchange(con, other: str, skip: set[int]) -> list[str]:
    """What the human and this agent have said to each other, oldest last.

    Both directions out of one query: the pair is the thread. A row that was
    answered is two things said, so its verdict is drawn as its own line - the
    reply is what makes the exchange read as a conversation rather than a list
    of things the human sent.
    """
    rows = con.execute(
        "SELECT id, ts, resolved_at, requester, target, state, body, verdict, "
        "dismissed_by, dismissed_reason "
        "FROM requests WHERE ((requester = ? AND target = ?) OR (requester = ? AND target = ?)) "
        "AND state != 'denied' ORDER BY id DESC LIMIT ?",
        (HUMAN, other, other, HUMAN, EXCHANGE_ROWS + len(skip)),
    ).fetchall()
    out: list[str] = []
    for r in reversed(rows):
        if int(r["id"]) in skip:
            continue
        mine = r["requester"] == HUMAN
        arrow = f"you → {other}" if mine else f"{other} → you"
        out.append(f"#{r['id']:<5} {arrow:<24} {_at(r['ts'])}  {_gist(r['body'])}")
        shown = db.shown_verdict(r)
        if shown:
            who = other if mine else "you"
            out.append(f"{'':<6} ↩ {who:<22} {_at(r['resolved_at'] or r['ts'])}  "
                       f"{_gist(shown)}")
    return out


def _gist(text: str | None) -> str:
    line = first_line(text)
    return line if len(line) <= EXCHANGE_WIDTH else line[:EXCHANGE_WIDTH - 1] + "…"


def _at(ts) -> str:
    return timefmt.stamp(ts or 0, timefmt.SHORT)


def first_line(text: str | None) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return "(empty)"
