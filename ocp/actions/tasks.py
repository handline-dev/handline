"""Tasks: refs, findings, done/close/discard/reopen, edit, and what blocks what."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from ..team import ask as askmod
from .. import db
from ..infra import gitrun
from ..infra import paths
from ..team import team
from ..infra import textsafe
from ..infra import timefmt
from ..infra.yamlcompat import ConfigError

from .routing import HUMAN, supervisor_of


TASK_REF = re.compile(r"[Tt](\d+)")


def task_ref(tid) -> str:
    """How a task is written wherever it is printed or typed: T79. A request
    is a bare number, so the two can never be read for each other."""
    return f"T{int(tid)}"


def parse_task_ref_error(text) -> str:
    """Why `text` is not a task reference: the refusal `parse_task_ref` raises."""
    try:
        parse_task_ref(text)
    except ValueError as e:
        return str(e)
    return ""


def parse_task_ref(text) -> int:
    """The task number in `T79` (or `t79`). A bare number is refused, naming
    both things it could mean; anything else is not a task at all."""
    text = str(text).strip()
    m = TASK_REF.fullmatch(text)
    if m:
        return int(m.group(1))
    if text.isdigit():
        raise ValueError(
            f"'{text}' could be task {text} or request {text}: a task is written "
            f"{task_ref(text)}, a request is the bare number (`ocp read {text}` reads "
            f"request {text}). Write {task_ref(text)} for the task.")
    raise ValueError(f"'{text}' is not a task - write it T<number>, like {task_ref(79)}")


def _block_parent_on_child(con, parent: int | None, tid: int, by: str) -> None:
    """T294 (orcapipe's #1910): a child task always blocks its parent,
    explicitly - not only through the structural open-child-task check
    `close_task` and `stop_wait_reason` already had. Shared by every place a
    child task comes into being (`task_new`), so the
    board's "waiting on" line and the unblock notice on close see it
    wherever it is actually true, not only on the one path that happened to
    write it."""
    if parent is not None:
        db.add_block(con, parent, tid)
        db.add_event(con, by, "block_task", {"task_id": parent, "blocks_on": tid})


def looks_like_sha(text: str) -> bool:
    """Hex of a length a person types a sha at - short ones included."""
    return re.fullmatch(r"[0-9a-fA-F]{4,40}", text) is not None


def _sha_on_main(repo: Path, sha: str) -> tuple[str | None, str]:
    """The full sha `sha` names in `repo`, when it is reachable from `main`;
    otherwise None and why.

    `done_evidence` is evidence, not a claim: a sha typed by hand that never
    merged - or that names nothing here - must not mark a task done, so this
    runs before anything is written, and what is stored is the full sha it
    resolved to, whatever length was typed.
    """
    rc, out, err = gitrun.run(repo, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    if rc is None:
        return None, err
    if rc != 0:
        return None, "no such commit in this repo"
    full = out.strip()
    rc, out, err = gitrun.run(repo, "merge-base", "--is-ancestor", full, "main")
    if rc is None:
        return None, err
    if rc == 0:
        return full, ""
    return None, textsafe.oneline((err or out).strip())


# task #21/#1884: the requester an unblock notice carries. Never a real
# agent and never HUMAN, on purpose - `push_answer` then has nothing to
# deliver an answer to (it treats an unrecognised requester the same as
# nothing to tell), so resolving or dismissing the notice never routes a
# reply back to whoever closed the blocker, and it never misreads as
# something the founder typed himself.
_UNBLOCK_REQUESTER = "task-board"


def _asker_of(row, requesters) -> str:
    """The rule of `task_asker`, given who made each request linked to the task."""
    for who in requesters:
        if who == HUMAN:
            return HUMAN
    return row["opened_by"]


def task_asker(con, row) -> str:
    """Who task `row` answers to when it closes (#71): the founder, if any
    request linked to it came from the founder - the task exists because the
    founder asked for something. Otherwise whoever opened the task itself: a
    task the manager opens on its own (an auditor finding, internal cleanup)
    is the manager's own to close, never its own superior's."""
    return _asker_of(row, (r["requester"] for r in db.task_requests(con, row["id"])))


def _asks(r) -> bool:
    """Is this linked request work still being asked for? A report (`--fyi`) asks
    nothing, so linking one never makes a task doing (T266)."""
    return r["state"] == "queued" and not r["fyi"]


def _begun(con, tid: int) -> bool:
    """Was this task begun on a founder's message (`task_begin`)? Its work is
    under way from then until it is done or closed, whatever became of the
    chat request that is its only link."""
    return con.execute(
        f"SELECT 1 FROM events WHERE kind = 'task_begin' AND {db.EVENT_TASK} = ? "
        "LIMIT 1", (tid,)).fetchone() is not None


def open_task_count(con, agent: str) -> int:
    """How many tasks `agent` has to do: queued plus doing, plus its current
    task whatever its state - one it picked up with `task start` is being
    worked even while still a draft (T317). A draft that is not current is not
    approved yet and does not count; a done or closed task is over. The state
    comes from `task_state`, so this cannot disagree with the board."""
    current = db.current_task(con, agent)
    rows = con.execute(
        "SELECT * FROM tasks WHERE owner = ? AND closed_by IS NULL AND done_evidence IS NULL",
        (agent,)).fetchall()
    return sum(1 for r in rows if r["id"] == current or task_state(con, r) != "draft")


def reads_doing(con, tid: int) -> bool:
    """Whether task `tid` is being worked, whatever its own done/closed columns
    say: a request is queued against it or an open child, or it was begun on a
    founder message (`_begun`). `task_state` builds "doing" from this, and the
    timeout clock asks it of a task that is just being marked done or reopened."""
    if _begun(con, tid):
        return True
    if any(_asks(r) for r in db.task_requests(con, tid)):
        return True
    return any(_asks(r)
              for c in db.open_child_tasks(con, tid)
              for r in db.task_requests(con, c["id"]))


def holds_current(con, row) -> bool:
    """Is this task the one its owner holds? An agent holds one task at a time (T444), so
    only that one can read as doing (an agent holding none, or only a finished one, is
    working whatever has work out); the founder holds none and every task of theirs
    that is being asked reads doing."""
    if row["owner"] == HUMAN:
        return True
    held = db.current_task(con, row["owner"])
    if held is None or held == row["id"]:
        return True
    cur = db.get_task(con, held)
    return cur is None or bool(cur["closed_by"] or cur["done_evidence"])


def is_doing(con, tid: int) -> bool:
    """`reads_doing` and the one its owner holds: what makes a task doing. Anything else
    with work out is queued behind the current one until `task start` or a finish."""
    row = db.get_task(con, tid)
    return row is not None and reads_doing(con, tid) and holds_current(con, row)


def _state_of(row, doing: bool, asked: bool = False) -> str:
    """The ladder of `task_state`, given whether the task reads as being worked and
    whether work is out on it at all: one asked but held back behind its owner's
    current task is queued, not a draft."""
    if row["merged_into"]:
        return "merged"
    if row["discarded_by"]:
        return "discarded"
    if row["closed_by"]:
        return "closed"
    if row["done_evidence"]:
        return "done"
    if doing:
        return "doing"
    return "queued" if row["queued_by"] or asked else "draft"


def task_state(con, row) -> str:
    """One of draft, queued, doing, done, closed (#71), discarded (#85) or merged (T68).
    done, closed and discarded are written, once each, and so is queued (T82: approved
    to run; without it the task is a draft). doing is never stored - read live from
    whether a request is currently queued against this task or an open child
    of it, the same live read the board already does for "who holds it now",
    so there is nothing here that a write path must remember to keep in
    sync."""
    if row["merged_into"] or row["discarded_by"] or row["closed_by"] or row["done_evidence"]:
        return _state_of(row, False)        # written states: nothing to read
    asked = reads_doing(con, row["id"])
    return _state_of(row, asked and holds_current(con, row), asked)


def stop_wait_reason(con, agent: str) -> str | None:
    """Why `agent` should not stop right now (T288, #1748): it holds a
    current task with no recorded wait. None if it holds none, or has one."""
    tid = db.current_task(con, agent)
    if tid is None:
        return None
    if db.open_child_tasks(con, tid):
        return None
    if db.task_blockers(con, tid):
        return None
    if db.open_decision_tasks_of(con, agent):
        return None
    return f"T{tid} is current with no recorded wait"


def child_blocker_of(con, tid: int):
    """The open child task of someone else that blocks `tid`, or None - whatever it is for (a
    review, a merge, a piece of the work): the flow that uses one is project guidance (T443). A child
    the owner holds itself is not waited on: it is the next thing to do, and holding the parent on it
    would leave the owner waiting on itself."""
    owner = db.get_task(con, tid)["owner"]
    return next((b for b in db.task_blockers(con, tid)
                 if b["parent_task"] == tid and b["owner"] != owner), None)


def child_wait(con, agent: str) -> str | None:
    """T443: what `agent` is waiting on when its current task is held by a child of its own, as
    the sentence a screen shows: `waiting for T13 (orcapipe.auditor)`; None when it waits on no
    such child. It is waiting on the child, not on a reply, and is given no other work meanwhile."""
    tid = db.current_task(con, agent)
    row = db.get_task(con, tid) if tid is not None else None
    if row is None or row["closed_by"] or row["done_evidence"]:
        return None
    child = child_blocker_of(con, tid)
    return f"waiting for T{child['id']} ({child['owner']})" if child else None


def block_wait(con, agent: str) -> str | None:
    """T563: what `agent` is held up by when its current task is blocked (`--blocked-by`), as the
    sentence a screen shows: `blocked by T12 (orcapipe.auditor), T14 (orcapipe)`; None when it is not blocked."""
    tid = db.current_task(con, agent)
    row = db.get_task(con, tid) if tid is not None else None
    if row is None or row["closed_by"] or row["done_evidence"]:
        return None
    blockers = db.task_blockers(con, tid)
    return "blocked by " + ", ".join(f"T{b['id']} ({b['owner']})" for b in blockers) if blockers else None


def task_mismatch(con, row, state: str, open_requests: list) -> str | None:
    """Where a task's state and its requests disagree, or None (T250). A draft
    with a request out was never approved, and a queued one has nobody working on
    it. `open_requests` is the queued requests."""
    if state in ("done", "closed", "discarded", "merged"):
        return None
    linked = {r["id"] for r in db.task_requests(con, row["id"])}
    open_linked = [r for r in open_requests if r["id"] in linked and not r["fyi"]]
    if not row["queued_by"] and open_linked:
        return f"draft with work out (#{open_linked[0]['id']}) - its opener has not queued it"
    if state == "queued" and not open_linked:
        return "approved, but no request is out"
    return None


def _request_answered(row) -> bool:
    """Has this request been told something back, as opposed to still
    sitting queued? Mirrors cli._answered's own reading (verdict set, or a
    settled state) without importing cli, which imports this module."""
    return bool(row["verdict"]) or row["state"] != "queued"


def _task_done_targets(con, tid: int) -> set[str]:
    """Who besides the task's owner may mark it done (#71 SHOULD-FIX 1,
    orcapipe's #2195 decision): the target of any request linked to the
    task that is still open (queued) or came back done - #1914 means every
    orcapipe task is owned by orcapipe itself, so the owner alone would
    leave only orcapipe able to mark anything done, never the builder
    founder #2154 actually asked. A request some other way settled
    (rejected, withdrawn, replaced, denied) named nobody real for this."""
    return {r["target"] for r in db.task_requests(con, tid) if r["state"] in ("queued", "done")}


def open_decision(me: str, body: str) -> tuple[bool, str, int | None]:
    """A question only the founder can answer, as a task of theirs - a decision
    card - rather than a request nobody tracks: (ok, message, the id of the
    request that carries the card). The title is the body's first line."""
    title = textsafe.oneline((body or "").strip().split("\n", 1)[0])[:80] or "a decision"
    ok, msg, tid = task_new(me, HUMAN, title, body, expects="decision")
    if not ok:
        return False, msg, None
    con = db.connect()
    try:
        card = next((r["id"] for r in db.task_requests(con, tid) if r["target"] == HUMAN), None)
    finally:
        con.close()
    return True, msg, card


def decide(con, tid: int, text: str) -> tuple[bool, str]:
    """The founder's decision on a task they own (a decision card): the text is
    recorded as the task's evidence, the task is closed, and the opener is told
    it - one act, however it was typed (a card's reply, a command). Nothing
    else marks a question answered.

    The request that delivered the card is settled with the same text, which
    is what tells the asker and takes the card off the founder's screen.
    """
    text = (text or "").strip()
    if not text:
        return False, "nothing sent: no decision typed"
    row = db.get_task(con, tid)
    if row is None:
        return False, f"no task T{tid}"
    if row["owner"] != HUMAN:
        return False, f"task T{tid} is not the founder's to decide"
    if row["closed_by"] or row["done_evidence"]:
        return False, f"task T{tid} is already decided"
    kids = db.open_child_tasks(con, tid)
    if kids and not _closes_whole(con, tid):
        # What `close_task` would refuse, said before anything is written: a
        # decision that settled the card and then could not close the task
        # would leave it done but open, and nothing could retry it.
        ids = ", ".join(f"T{k['id']}" for k in kids)
        return False, f"task T{tid} still has open child tasks ({ids}) - close them first"
    from .requests import check_open_proposal
    card = db.decision_card_of(con, tid)
    cards = [card] if card is not None and card["state"] == "queued" and card["target"] == HUMAN else []
    for r in cards:
        refusal = check_open_proposal(r["id"], r, text)     # an unclear answer decides nothing
        if refusal:
            return False, refusal
    for r in cards:
        ok, msg = askmod.resolve(r["id"], text)
        if not ok:
            return False, msg
    # Settling the card settled the task too (`db.settle_decision_tasks`); only a
    # card that was already closed some other way leaves it here to be decided.
    if not db.get_task(con, tid)["closed_by"]:
        ok, msg = mark_task_done(con, tid, HUMAN, reason=text)
        if not ok:
            return False, msg
        ok, closed = close_task(con, tid, HUMAN)
        if not ok:
            return False, f"T{tid} decided ({closed})"
    return True, f"T{tid} decided"


def mark_task_done(con, tid: int, by: str, sha: str | None = None,
                   request_id: int | None = None, reason: str | None = None,
                   repo: Path | None = None) -> tuple[bool, str]:
    """Mark a task done (#71) - the builder's evidence, not the end of it.
    Exactly one of a merge sha (checked against `main`), the id of a request
    where the result was already reported (must exist and be answered - not
    a claim taken on trust), or a one-line fact for work that leaves no
    commit (a plan, a decision, a duplicate).

    Only the task's own owner, or the target of a request linked to it that
    is open or done (`_task_done_targets`), may - the one actually doing the
    work, never the opener standing in for them. Refused if the task is
    already done or already closed. Nothing here checks open child tasks or
    an open blocker on this task itself - that gate belongs to `close_task`
    now: the asker is the one who decides, at sign-off, whether what is
    underneath is enough (#1008 stays enforced, one stage later).
    """
    row = db.get_task(con, tid)
    if row is None:
        return False, f"no task T{tid}"
    if row["closed_by"]:
        return False, f"task T{tid} is already closed ({row['closed_by']})"
    if row["done_evidence"]:
        return False, f"task T{tid} is already done ({row['done_evidence']})"
    # T292 (#1655 section 4): a sha-expecting task's opener or the founder
    # may close it another way when the sha is never going to land - the
    # one exception to #2195's rule that the opener alone is not enough.
    sha_override = by == row["opened_by"] and row["expects"] == "sha"
    if by not in (row["owner"], HUMAN) and by not in _task_done_targets(con, tid) \
            and not sha_override:
        return False, (f"task T{tid} can only be marked done by its owner "
                       f"({row['owner']}), the target of a request linked to it, or the founder")
    given = [x for x in (sha, request_id, reason) if x is not None]
    if len(given) != 1:
        return False, "done needs exactly one of a sha, --request, or a reason"
    if sha:
        full, why = _sha_on_main(repo or Path.cwd(), sha)
        if full is None:
            return False, f"{sha} is not an ancestor of main" + (f" ({why})" if why else "")
        mark, via = full, "sha"
    elif request_id is not None:
        r = db.get_request(con, request_id)
        if r is None:
            return False, f"no request #{request_id}"
        if not _request_answered(r):
            return False, f"request #{request_id} is still queued - not answered yet"
        mark, via = f"request #{request_id}", "request"
    else:
        mark, via = reason, "reason"
    # T292 (#1655 section 4): a sha task can't close on a reason unless the
    # opener or the founder does it; verdict/decision/report are just labels.
    if row["expects"] == "sha" and via != "sha" and by not in (row["opened_by"], HUMAN):
        return False, (f"task T{tid} expects a sha - `ocp task done T{tid} --sha <sha>`, "
                       f"or its opener ({row['opened_by']}) or the founder may close it "
                       "another way")
    if not db.mark_task_done(con, tid, mark, via):
        return False, f"task T{tid} was marked done by someone else just now"
    db.add_event(con, by, "mark_task_done", {"task_id": tid, "done": mark})
    _retire_task_requests(con, tid, f"task T{tid} done ({mark})")
    if advance_current_task(con, row["owner"]):
        askmod.push(con, row["owner"])
    _deliver_unblocked(con, *_free_blocked_by(con, tid, by))
    self_close(con, tid)
    return True, f"task T{tid} done ({mark})"


def self_close(con, tid: int) -> None:
    """A done child whose asker is an agent needs no separate sign-off (T555): close
    it as its asker. Refused while it holds an open child or blocker of its own; the
    close of whatever held it tries again (`close_task`)."""
    row = db.get_task(con, tid)
    if row is None or not row["parent_task"] or not row["done_evidence"] or row["closed_by"]:
        return
    asker = task_asker(con, row)
    if asker != HUMAN:
        close_task(con, tid, asker)


def _closes_whole(con, tid: int) -> bool:
    """Would the founder's close of `tid` go through: every open child done, and so on down."""
    return all(k["done_evidence"] and _closes_whole(con, k["id"]) for k in db.open_child_tasks(con, tid))


def close_task(con, tid: int, by: str) -> tuple[bool, str]:
    """Close a task on the board (#71) - the asker's or the founder's sign-off, final.
    See `task_asker` for who that is.

    Refused before the task is done (nothing to sign off on yet), while a
    child task is open - a five-part ask closed as one row hid that parts 4
    and 5 were not done (#1008) - or while this task itself is waiting on an
    open blocker (task #21/#1884), otherwise closing it directly is a back
    door around the wait the block exists to enforce. Once it does close,
    clears every block naming it and, for a task that leaves with no open
    blocker left, tells its owner once - a durable request, not a
    typed-only notice.

    The close, its event, every cleared block, and any resulting notice's
    database row are one transaction (auditor's #2042 BLOCKING): decoupled,
    a crash between them could lose the notice for good (the block row that
    would have triggered it already gone) or double it (two blockers closed
    at once each finding the other's clearing not yet visible). The inbox
    file for a notice is written only after COMMIT - the database row is
    what the daemon schedules delivery from, so a crash before that file
    lands is still recoverable, same as any other `ask.deliver`.
    """
    row = db.get_task(con, tid)
    if row is None:
        return False, f"no task T{tid}"
    if row["closed_by"]:
        return False, f"task T{tid} is already closed ({row['closed_by']})"
    if not row["done_evidence"]:
        return False, f"task T{tid} is not done yet - `ocp task done` first"
    asker = task_asker(con, row)
    if by not in (asker, HUMAN):
        return False, f"task T{tid} can only be closed by {asker} - whoever asked for it - or the founder"
    kids = db.open_child_tasks(con, tid)
    if by == HUMAN and _closes_whole(con, tid):     # the founder's close takes done children along
        kids = [k for k in kids if not close_task(con, k["id"], HUMAN)[0]]
    if kids:
        ids = ", ".join(f"T{k['id']}" for k in kids)
        return False, f"task T{tid} still has open child tasks ({ids}) - close them first"
    blockers = db.task_blockers(con, tid)
    if blockers and by != HUMAN:
        ids = ", ".join(f"T{b['id']}" for b in blockers)
        return False, f"task T{tid} is waiting on {ids} - it closes only once they do"
    con.execute("BEGIN IMMEDIATE")
    try:
        if not db.close_task(con, tid, by):
            con.execute("ROLLBACK")
            return False, f"task T{tid} was closed by someone else just now"
        db.add_event(con, by, "close_task", {"task_id": tid, "closed_by": by})
        for b in blockers:  # only the founder gets here with any
            db.remove_block(con, tid, b["id"])
        released = [d["task_id"] for d in db.tasks_blocked_by(con, tid)]
        notices, freed_owners = _clear_blocks_on(con, tid, by)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    _retire_task_requests(con, tid, f"task T{tid} closed by {by}")
    if advance_current_task(con, row["owner"]):
        askmod.push(con, row["owner"])
    _deliver_unblocked(con, notices, freed_owners)
    for waiting in [row["parent_task"], *released]:     # a done child that was held by this one
        if waiting:
            self_close(con, waiting)
    return True, f"task T{tid} closed by {by}"


def discard_task(con, tid: int, by: str, reason: str, stop: bool = False) -> tuple[bool, str]:
    """Throw a task away before it is done (task #85) - the asker's or the founder's call
    (see `task_asker`), in one step with a one-line reason. It is recorded as
    discarded (who, why, and when in closed_at), never as finished work: no
    board, list or 'waiting for check' group reads it as done.

    close_task's guards hold: refused while a child task is open or while this
    task waits on an open blocker, and refused once it is done or already
    closed. A request still queued against it also refuses: discarding
    leaves nothing on the board pointing at that request, so its target
    would keep working on it. Every block naming it clears in the same transaction, and a task
    left with no open blocker is told, as close does.

    `stop` (the founder's, on a doing task): the open requests are cancelled
    (`ask.cancel`, which tells their target to stop) instead of refusing, once
    every other guard has passed."""
    row = db.get_task(con, tid)
    if row is None:
        return False, f"no task T{tid}"
    if row["closed_by"]:
        what = "discarded" if row["discarded_by"] else "closed"
        return False, f"task T{tid} is already {what} ({row['closed_by']})"
    if row["done_evidence"]:
        return False, f"task T{tid} is done - `ocp close T{tid}` signs it off"
    asker = task_asker(con, row)
    if by not in (asker, HUMAN):
        return False, f"task T{tid} can only be discarded by {asker} - whoever asked for it - or the founder"
    said = textsafe.oneline(reason).strip()
    if not said:
        return False, "discarding needs a one-line reason"
    kids = db.open_child_tasks(con, tid)
    if kids:
        ids = ", ".join(f"T{k['id']}" for k in kids)
        return False, f"task T{tid} still has open child tasks ({ids}) - deal with them first"
    blockers = db.task_blockers(con, tid)
    if blockers and by != HUMAN:
        ids = ", ".join(f"T{b['id']}" for b in blockers)
        return False, f"task T{tid} is waiting on {ids} - it cannot be discarded until they close"
    live = [r for r in db.task_requests(con, tid) if _asks(r)]
    in_hand = stop and by == HUMAN and (db.current_task(con, row["owner"]) == tid
                                        or any(r["delivered_at"] for r in live))
    if live and stop and by == HUMAN:
        cancelled = []
        for r in live:
            ok, said_cancel = askmod.cancel(r["id"])
            if not ok:
                was = f" (already cancelled: {', '.join(f'#{c}' for c in cancelled)})" if cancelled else ""
                return False, f"task T{tid} not discarded: {said_cancel}{was}"
            cancelled.append(r["id"])
    elif live:
        ids = ", ".join(f"#{r['id']} (with {r['target']})" for r in live)
        return False, (f"task T{tid} still has a request open: {ids} - withdraw or cancel it "
                       "first (the founder's `ocp cancel <id>`), or its target keeps working "
                       "on thrown-away work")
    con.execute("BEGIN IMMEDIATE")
    try:
        if not db.discard_task(con, tid, by, said):
            con.execute("ROLLBACK")
            return False, f"task T{tid} was done or closed by someone else just now"
        db.add_event(con, by, "discard_task", {"task_id": tid, "reason": said})
        for b in blockers:  # only the founder gets here with any: the wait ends with the task
            db.remove_block(con, tid, b["id"])
        released = [d["task_id"] for d in db.tasks_blocked_by(con, tid)]
        notices, freed_owners = _clear_blocks_on(con, tid, by, "discarded")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    # `live` only guaranteed nothing was asking *then* - a lingering fyi
    # report could still be queued, so retire it too.
    _retire_task_requests(con, tid, f"task T{tid} discarded by {by}: {said}", state="rejected")
    if in_hand and row["owner"] != HUMAN and not any(r["delivered_at"] for r in live):
        # a delivered one was told to stop by the cancel; one only held (its `task start`) was not
        _type_say(con, row["owner"], f"T{tid} discarded by {by} - stop work on it", "task discarded")
    if advance_current_task(con, row["owner"]):
        askmod.push(con, row["owner"])
    _deliver_unblocked(con, notices, freed_owners)
    for waiting in [row["parent_task"], *released]:     # a done child that was held by this one
        if waiting:
            self_close(con, waiting)
    return True, f"task T{tid} discarded by {by}: {said}"


def reopen_task(con, tid: int, by: str, reason: str) -> tuple[bool, str]:
    """Send a done task back to doing (#71) - the asker's or the founder's call, the
    opposite of closing it. Needs a one-line reason, the same as any other
    undo in this system. Refused for a task not currently done (nothing to
    undo) or already closed (closing is final - see close_task)."""
    row = db.get_task(con, tid)
    if row is None:
        return False, f"no task T{tid}"
    if row["closed_by"]:
        return False, f"task T{tid} is closed - closing is final, nothing to reopen"
    if not row["done_evidence"]:
        return False, f"task T{tid} is not done - nothing to reopen"
    asker = task_asker(con, row)
    if by not in (asker, HUMAN):
        return False, f"task T{tid} can only be reopened by {asker} - whoever asked for it - or the founder"
    said = textsafe.oneline(reason).strip()
    if not said:
        return False, "reopening needs a one-line reason"
    # T286: whether it still has a request out decides whether its timeout
    # clock resumes (reads_doing, not task_state - done_evidence is still set
    # on `row` here, and task_state would misread that as "done", never doing).
    if not db.reopen_task(con, tid, is_doing(con, tid)):
        return False, f"task T{tid} was closed or reopened by someone else just now"
    db.add_event(con, by, "reopen_task",
                 {"task_id": tid, "reason": said, "after_request": _newest_request(con)})
    return True, f"task T{tid} reopened by {by}: {said}"


def release_task(con, tid: int, by: str) -> tuple[bool, str]:
    """Answer to a timeout notice besides extending it: retire the task's own open
    requests (so whoever is mid-way is told to stop) and take it back to queued or
    draft. Opener or founder only; a task doing through a child is released there."""
    row = db.get_task(con, tid)
    if row is None:
        return False, f"no task T{tid}"
    guard = _task_edit_guard(row, tid, by)
    if guard:
        return False, guard
    state = task_state(con, row)
    if state != "doing":
        return False, f"task T{tid} is {state}, not doing - nothing to release"
    direct = [r for r in db.task_requests(con, tid) if _asks(r)]
    if not direct:
        child = next((c for c in db.open_child_tasks(con, tid)
                     if any(_asks(r) for r in db.task_requests(con, c["id"]))), None)
        return False, (f"task T{tid} is doing through its child T{child['id']} - "
                       f"release it there" if child else
                       f"task T{tid} was begun on a founder message: it is doing until it is done")
    # Best-effort, not all-or-nothing: each retire_open call is its own
    # atomic close, so one losing a race to some other close (an agent
    # resolving it at the same instant) must not throw away what the others
    # already did - the task and its clock still have to end up matching
    # whatever did get retired.
    retired, said, failed = [], [], []
    for r in direct:
        ok, msg = askmod.retire_open(con, r["id"], f"T{tid} released by {by}")
        if ok:
            retired.append(r["id"])
            said.append(msg.split("\n", 1)[0])
        else:
            failed.append(f"#{r['id']}: {msg}")
    if not retired:
        return False, f"task T{tid} released nothing: {'; '.join(failed)}"
    db.add_event(con, by, "release_task",
                {"task_id": tid, "retired": retired, **({"failed": failed} if failed else {})})
    detail = "; ".join(said)
    tail = f" - not retired: {'; '.join(failed)}" if failed else ""
    if is_doing(con, tid):
        # Every direct request just closed: what is left is a child's, or a founder-begun task.
        child = next((c for c in db.open_child_tasks(con, tid)
                      if any(_asks(r) for r in db.task_requests(con, c["id"]))), None)
        why = (f"through its child T{child['id']} - release it there too" if child
               else "because it was begun on a founder message")
        return True, (f"task T{tid} released by {by}: {detail}, but it is still doing {why}; "
                      f"its own timeout keeps running until then{tail}")
    db.pause_task_timeout(con, tid)
    back = "queued" if row["queued_by"] else "draft"
    return True, (f"task T{tid} released by {by}: {detail}, back to {back}, "
                  f"its timeout paused{tail}")


def _notify_unblocked(con, dep_tid: int, owner: str, closer: str, closed_tid: int,
                      how: str = "closed") -> tuple[int, str, str]:
    """task #21/#1884: told once, as a durable request linked to the task -
    not a typed-only notice that a park or a crash between here and the
    agent reading its screen would lose. Only the database row (and the
    event and link that make it durable) happen here, inside the caller's
    transaction; the returned `(rid, owner, text)` is what `close_task`
    delivers to the inbox after COMMIT.

    Never called when `closer == owner` (`_clear_blocks_on` checks before
    calling): the owner who just closed the blocker themselves does not need
    telling."""
    text = f"task T{dep_tid} is unblocked - T{closed_tid}, its last open blocker, {how}"
    closed = db.get_task(con, closed_tid)
    if closed["done_evidence"] and closed["parent_task"] != dep_tid:
        text += f": {closed['done_evidence']}"     # a child's result is told on its own, by _child_result_notice
    rid = db.add_request(con, _UNBLOCK_REQUESTER, owner, text, None, None,
                         "fire_and_forget", "say", 0, None)
    db.link_request(con, dep_tid, rid)
    db.add_event(con, closer, "task_unblocked",
                 {"task_id": dep_tid, "request_id": rid, "closed_task": closed_tid,
                  "after_request": _newest_request(con)})
    return rid, owner, text


def _child_result_notice(con, dep_row, by: str, tid: int) -> tuple[int, str, str] | None:
    """T443: when a child `tid` of `dep_row`'s task ends, the task's owner is told how, whether or
    not other blockers remain - the "unblocked" notice is for the last one going, and says nothing
    of the result. Once per finish (a reopen voids it), whichever of the done and the close
    comes first."""
    child = db.get_task(con, tid)
    if child is None or child["parent_task"] != dep_row["id"] or dep_row["owner"] == by:
        return None
    since = con.execute(f"SELECT MAX(id) FROM events WHERE kind = 'reopen_task' AND {db.EVENT_TASK} = ?",
                        (tid,)).fetchone()[0] or 0
    if con.execute(f"SELECT 1 FROM events WHERE kind = 'child_result_told' AND {db.EVENT_TASK} = ? "
                   "AND json_extract(payload, '$.child') = ? AND id > ? LIMIT 1",
                   (dep_row["id"], tid, since)).fetchone():
        return None
    if child["done_evidence"]:
        text = f"T{tid} done by {child['owner']}: " + " ".join(child["done_evidence"].split())
    elif child["discarded_by"]:
        text = f"T{tid} discarded by {child['discarded_by']}: " + " ".join((child["discard_reason"] or "").split())
    else:
        text = f"T{tid} closed by {child['closed_by']}"
    text = text.rstrip(": ")
    rid = db.add_request(con, _UNBLOCK_REQUESTER, dep_row["owner"], text, None, None,
                         "fire_and_forget", "say", 0, None)
    db.link_request(con, dep_row["id"], rid)
    db.add_event(con, by, "child_result_told", {"task_id": dep_row["id"], "child": tid, "request_id": rid})
    return rid, dep_row["owner"], text


def _clear_blocks_on(con, tid: int, closer: str,
                     how: str = "closed") -> tuple[list[tuple[int, str, str]], set[str]]:
    """After `tid` closes: drop every block row naming it, and for each task
    that leaves with no open blocker left, write a notice for its owner
    (skipped for the closer's own task - it does not need telling) and
    collect that owner to advance (T325: every one of them, told or not -
    its queued, undelivered task is not reconsidered on its own once
    skipped over blocked). Dropping the row here - not on read - is what
    makes "once" true: the row that would trigger a second notice is gone
    before anything can re-read it, and both the drop and the write happen
    inside the caller's one transaction, so a concurrent close clearing a
    dependent's other blocker either commits first (seen here) or not yet
    (seen by it instead) - never both at once. Returns the notices to
    deliver, and the owners to advance, once that transaction commits."""
    notices = []
    freed_owners = set()
    for dep in db.tasks_blocked_by(con, tid):
        db.remove_block(con, dep["task_id"], tid)
        dep_row = db.get_task(con, dep["task_id"])
        if dep_row is None or dep_row["closed_by"]:
            continue
        told_result = _child_result_notice(con, dep_row, closer, tid)
        if told_result:
            notices.append(told_result)
        if db.task_blockers(con, dep["task_id"]):
            continue
        freed_owners.add(dep_row["owner"])
        if dep_row["owner"] == closer:
            # No notice for the closer, but the task still comes due again.
            db.add_event(con, closer, "unblock_task", {
                "task_id": dep["task_id"], "blocks_on": tid, "after_request": _newest_request(con)})
            continue
        if _told_at_done(con, tid, dep["task_id"]):
            continue
        notices.append(_notify_unblocked(con, dep["task_id"], dep_row["owner"], closer, tid, how))
    return notices, freed_owners


def _free_blocked_by(con, tid: int, by: str) -> tuple[list[tuple[int, str, str]], set[str]]:
    """`tid` was just marked done: the tasks it blocked and that now have no open
    blocker left are released, as a close would have (T397) - a notice for their owner
    (not `by`'s own) and the owner to advance. The block rows stay, so a reopened
    `tid` blocks them again; `_clear_blocks_on` drops them at the close."""
    notices, freed_owners = [], set()
    for dep in db.tasks_blocked_by(con, tid):
        dep_row = db.get_task(con, dep["task_id"])
        if dep_row is None or dep_row["closed_by"]:
            continue
        told_result = _child_result_notice(con, dep_row, by, tid)
        if told_result:
            notices.append(told_result)
        if db.task_blockers(con, dep["task_id"]):
            continue
        freed_owners.add(dep_row["owner"])
        told = dep_row["owner"] != by       # the one who finished it does not need telling
        db.add_event(con, by, "released_at_done", {"task_id": dep["task_id"], "blocks_on": tid,
                                                    "told": told})
        if told:
            notices.append(_notify_unblocked(con, dep["task_id"], dep_row["owner"], by, tid, "done"))
    return notices, freed_owners


def _told_at_done(con, tid: int, dep: int) -> bool:
    """Was `dep`'s owner already told, since blocker `tid`'s most recent reopen
    (if any), that it is free? Then the close of `tid` does not tell it again -
    but a reopen voids the earlier release, since `tid` blocks `dep` again from
    that point and the owner is told afresh once it finishes a second time."""
    since = con.execute(
        f"SELECT MAX(id) FROM events WHERE kind = 'reopen_task' AND {db.EVENT_TASK} = ?",
        (tid,)).fetchone()[0] or 0
    return con.execute(
        f"SELECT 1 FROM events WHERE kind = 'released_at_done' AND {db.EVENT_TASK} = ? "
        "AND json_extract(payload, '$.blocks_on') = ? "
        "AND json_extract(payload, '$.told') = 1 AND id > ? LIMIT 1",
        (dep, tid, since)).fetchone() is not None


def _deliver_unblocked(con, notices, freed_owners) -> None:
    """The half of a release that happens after the write: tell the owners, and give
    each freed owner its next task now (T325) rather than at some later tick."""
    for rid, owner, text in notices:
        db.mark_send_now(con, rid)
        askmod.deliver(rid, _UNBLOCK_REQUESTER, owner, text, None,
                       team.Decision(True, "task unblocked", kind="say",
                                     response="fire_and_forget", creates_gate=False,
                                     chain_depth=0))
        askmod.push(con, owner, rid)
    for owner in freed_owners:
        if advance_current_task(con, owner):
            askmod.push(con, owner)


def release_closed_blockers(con, log=lambda _m: None) -> list[int]:
    """T577: the tasks a closed or discarded task was still blocking are released, the way a close
    releases them. A decision task settles with its card (`db.settle_decision_tasks`), at the db
    layer, which cannot run the release: its dependents were left with a block row, no notice and
    no next turn - a doing one never told, a queued one never sent. A level, read from the block
    rows left behind, so it also frees what an earlier settle left. One blocker that fails is
    logged and left for the next tick, and the others are still released (T588): its rows are
    the same next time, so it must not stop the sweep that delivers every agent's tasks.
    Returns the tasks released."""
    freed: list[int] = []
    for (tid,) in con.execute("SELECT DISTINCT b.blocks_on FROM task_blocks b JOIN tasks t "
                              "ON t.id = b.blocks_on WHERE t.closed_by IS NOT NULL").fetchall():
        try:
            row = db.get_task(con, tid)
            con.execute("BEGIN IMMEDIATE")
            try:
                waiting = [d["task_id"] for d in db.tasks_blocked_by(con, tid)]
                notices, freed_owners = _clear_blocks_on(con, tid, row["closed_by"],
                                                         "discarded" if row["discarded_by"] else "closed")
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
            freed += waiting
            _deliver_unblocked(con, notices, freed_owners)
        except Exception as e:  # noqa: BLE001 - one blocker's failure is not the queue's
            log(f"  T{tid}: releasing the tasks it blocked failed: {type(e).__name__}: {e}")
    return freed


def _task_edit_guard(row, tid: int, by: str) -> str | None:
    """None if `by` may reshape task `tid` (its opener, or the founder) - a
    task is not a request, it has no `target` a supervisor could redirect -
    otherwise the refusal to show. Shared by every verb that changes a task
    without changing whose it is (edit_task, add_block, unblock)."""
    if by != row["opened_by"] and by != HUMAN:
        return (f"task T{tid} can only be edited by whoever opened it "
               f"({row['opened_by']}) or the founder")
    return None


def _link_delivery(con, tid: int, owner: str, rid: int) -> None:
    """Link a task to the request that delivers it, and make it the owner's
    current task if it holds none yet - shared by `task_new`'s immediate
    delivery and `set_queued`'s delivery of an approved draft (T325), so
    approving a task is the same act as opening one, not a second message
    someone has to remember to send."""
    db.link_request(con, tid, rid)
    if owner != HUMAN and db.current_task(con, owner) is None:
        db.set_current_task(con, owner, tid)


def set_queued(con, tid: int, by: str, queued: bool) -> tuple[bool, str]:
    """Approve a draft task to run (`queued`), or send a queued one back to
    draft (T82). The founder or the manager of the task's owner - not its
    opener, so an agent cannot approve its own work; refused for a task that
    is done or ended, or that already has a request out (that is doing, and
    is not taken back by a word).

    T325: queuing also delivers, through `advance_current_task` - right away
    if the owner is free, or once its turn comes if not. `task_state` after
    the call, not `advance_current_task`'s own True/False, says which: a
    busy owner moves on to nothing just as validly as a free one delivers.
    """
    row = db.get_task(con, tid)
    if row is None:
        return False, f"no task T{tid}"
    if by != HUMAN:
        try:
            boss = supervisor_of(team.Team.load(), row["owner"])
        except (FileNotFoundError, ConfigError):
            boss = None
        if by != boss:
            return False, (f"task T{tid} can only be queued or drafted by the founder "
                           f"or {row['owner']}'s manager")
    state = task_state(con, row)
    want, other = ("queued", "draft") if queued else ("draft", "queued")
    if state == want:
        return False, f"task T{tid} is already {want}"
    if state != other:
        return False, f"task T{tid} is {state} - only a {other} task can be made {want}"
    if not db.set_task_queued(con, tid, by if queued else None):
        return False, f"task T{tid} changed under this call - read it again"
    db.add_event(con, by, "queue_task" if queued else "draft_task",
                 {"task_id": tid, "after_request": _newest_request(con)})
    if queued:
        # advance_current_task's own True/False says whether the owner's
        # current task moved on at all, not whether it landed on `tid` -
        # a busy owner moves on to nothing, and an owner with no other work
        # can move on by clearing its current task to None just as well as
        # by delivering `tid`. `task_state` says which actually happened.
        refused: dict = {}
        changed = advance_current_task(con, row["owner"], refused)
        if changed:
            askmod.push(con, row["owner"])
        if task_state(con, db.get_task(con, tid)) == "doing":
            return True, (f"task T{tid} is queued ({by}) - "
                          f"{row['owner']} was free, so it went out now")
        if tid in refused:      # T333: queued, but not sent, and the approver is told why
            return True, f"task T{tid} is queued ({by}) but was not sent to {row['owner']}: {refused[tid]}"
    return True, f"task T{tid} is {'queued' if queued else 'draft'} ({by})"


EXPECTS = ("sha", "verdict", "decision", "report")


def parse_expects(text) -> str:
    """--expects, validated: sha, verdict, decision or report - what `task
    done` (auditor #1655 section 4) must be given to close the task.
    'report' is the default `task_new` writes when none is given; a bad
    value here is still refused rather than silently read as it."""
    t = str(text).strip().lower()
    if t not in EXPECTS:
        raise ValueError(f"--expects must be one of {', '.join(EXPECTS)}, not '{text}'")
    return t


def _current_task_still_asking(con, agent: str) -> bool:
    """Does `agent`'s current task still have its own (not a child's) request
    queued and not a mere fyi report - the one thing left to deliver? A begun
    task (`_begun`) always does, until it is done."""
    tid = db.current_task(con, agent)
    if tid is None:
        return False
    row = db.get_task(con, tid)
    if row is None or row["closed_by"] or row["done_evidence"]:
        return False
    # T443: an owner waiting on a child of its own is held on it, whatever became of its request.
    return (_begun(con, tid) or child_blocker_of(con, tid) is not None
            or any(_asks(r) for r in db.task_requests(con, tid)))


# Events after which a task's earlier answers no longer mean it was handled: it
# was reopened, unblocked or queued (again), and so goes out to its owner anew.
_RESET_EVENTS = ("reopen_task", "unblock_task", "task_unblocked", "queue_task")


def _newest_request(con) -> int:
    """The `after_request` a `_RESET_EVENTS` event carries. Ids order a request
    against the event where clock seconds cannot: an answer and a reopen in the
    same second are still one before the other."""
    return con.execute("SELECT COALESCE(MAX(id), 0) FROM requests").fetchone()[0]


def _reset_after(con, tid: int) -> int:
    """The highest request id a `_RESET_EVENTS` event on task `tid` has covered."""
    marks = ",".join("?" * len(_RESET_EVENTS))
    return con.execute(
        "SELECT COALESCE(MAX(CAST(json_extract(payload, '$.after_request') AS INTEGER)), 0) "
        f"FROM events WHERE kind IN ({marks}) AND {db.EVENT_TASK} = ?",
        (*_RESET_EVENTS, tid)).fetchone()[0]


def _waiting_tasks(con, agent: str):
    """`agent`'s open tasks that could become current, by priority then age,
    each as (row, its own queued request or None). Skips one blocked, still
    a draft, or with nothing of its own left to deliver. `None` in place of
    the request is a queued task never delivered (T325) - `advance_current_task`
    delivers it there and then, the same way `task_new` would, just later. A
    task whose request reached its owner and was answered since it last came
    due (`_RESET_EVENTS`) is not that (T352)."""
    for row in db.open_tasks(con):
        if row["owner"] != agent or row["done_evidence"]:
            continue
        if db.task_blockers(con, row["id"]):
            continue
        linked = db.task_requests(con, row["id"])
        req = next((r for r in linked if _asks(r)), None)
        after = _reset_after(con, row["id"]) if req is None else 0
        if req is None and any(r["target"] == agent and not r["fyi"] and r["id"] > after
                               and r["state"] not in db.NEVER_REACHED_TARGET for r in linked):
            continue  # delivered and answered since it last came due: not a reason to send it again
        if req is not None or row["queued_by"] is not None:
            yield row, req


def _note_refusal(con, agent: str, tid: int, msg: str, refused: dict | None = None) -> None:
    """A delivery of `tid` was refused: `refused` (the caller's, if it wants to say so) gets the text, and the
    event log gets it once - not again while the task's latest event is this same refusal (T334), since
    every advance for its owner retries it and a permanent refusal would otherwise fill the log."""
    if refused is not None:
        refused[tid] = msg
    last = con.execute(f"SELECT kind, payload FROM events WHERE {db.EVENT_TASK} = ? ORDER BY id DESC LIMIT 1",
                       (tid,)).fetchone()
    if last and last["kind"] == "task_deliver_refused" and json.loads(last["payload"]).get("why") == msg:
        return
    db.add_event(con, agent, "task_deliver_refused", {"task_id": tid, "why": msg})


def advance_current_task(con, agent: str, refused: dict | None = None) -> bool:
    """T318/T325: moves `agent` on to its next open task, by priority then
    age, once the current one stops being worth holding. True if anything
    changed - the caller's cue to push a delivery now. The founder (C3)
    holds no current-task row at all, so this is always a no-op for it.

    A candidate with nothing out yet is delivered here, on the spot,
    through the same `ask` `task_new` used - `on_behalf_of` and `park=False`
    because the requester is `queued_by`, read from our own db, not whoever's
    live session this delivery happens to run in and possibly long after it
    approved this. A refusal (the edge or the owner changed since) is
    recorded and skipped, not left to silently stall everything behind it;
    `refused`, if given, is filled with {task id: the refusal text} for a caller
    that owes it to whoever queued the task (T333)."""
    if agent == HUMAN or _current_task_still_asking(con, agent):
        return False
    before = db.current_task(con, agent)
    for row, req in _waiting_tasks(con, agent):
        if req is not None:
            db.set_current_task(con, agent, row["id"])
            db.promote_to_front(con, req["id"])
            return True
        ok, msg, rid = askmod.ask(agent, row["title"], requester=row["queued_by"], kind="task",
                                  on_behalf_of=True, park=False)
        if not ok:
            _note_refusal(con, agent, row["id"], msg, refused)
            continue
        _link_delivery(con, row["id"], agent, rid)
        db.set_current_task(con, agent, row["id"])
        return True
    # Nothing to advance to. A current task that is merely blocked, and not
    # yet done, stays current - `_waiting_tasks` left it out for that reason,
    # not because it is finished, and clearing it would drop the assignment
    # over a wait that is about to end. A done task can carry a stale
    # blocker too (only `close_task` checks one), and that one really is
    # finished - held onto, it would freeze the owner behind its own
    # already-done work until someone closes it.
    before_row = db.get_task(con, before) if before is not None else None
    if before_row is not None and not before_row["done_evidence"] and db.task_blockers(con, before):
        return False
    db.set_current_task(con, agent, None)
    return before is not None


def _retire_task_requests(con, tid: int, verdict: str, state: str = "done") -> None:
    """Retire every one of `tid`'s own linked requests still queued, through
    the ordinary `resolve` path, so none is left stuck at a target's head."""
    for r in db.task_requests(con, tid):
        if r["state"] == "queued":
            askmod.resolve(r["id"], verdict, state=state)


def task_new(me: str, owner: str, title: str, body: str | None = None,
            parent: int | None = None, expects: str | None = None,
            priority: int | None = None,
            timeout_s: int | None = None) -> tuple[bool, str, int | None]:
    """T292 (auditor #1655 section 4): opens the task and asks its owner in
    one call, through the routing table (`askmod.ask`, C2) - a refused owner
    leaves no task row behind. The request is kind='task' except for a human
    owner (C3), whose card is the founder's gate while the task is open.

    No `con`: like `ask`, this manages its own connection(s); the task row
    and its link are one write, only the call into `ask` is separate.

    The owner's current task (bullet 1) is set to this one only if it holds
    none yet - never for a human owner, who holds no current task at all.

    T294: a child task always blocks its parent (orcapipe's #1910: every
    `--parent` adds the block, no --expects gate, even when the parent's own
    owner opens the child for itself) - close_task already refuses to close
    while any child is open, so this only makes that true in `task_blockers`,
    the board's "waiting on" line, and the notice `_clear_blocks_on` sends
    once the child closes.
    """
    # A task of the founder's is a decision unless it says otherwise.
    expects = expects or ("decision" if owner == HUMAN else "report")
    if not (body or "").strip():
        return False, ("a task needs a body: the work itself, what done looks like - "
                       "the title alone is not a brief"), None
    if priority is None:
        priority = db.DEFAULT_PRIORITY
    if parent is not None:
        con = db.connect()
        try:
            if db.get_task(con, parent) is None:
                return False, f"no task T{parent}", None
        finally:
            con.close()
    kind = None if owner == HUMAN else "task"
    ok, msg, rid = askmod.ask(owner, body, requester=me, kind=kind, card=False)
    if not ok:
        return False, msg, None
    con = db.connect()
    try:
        tid = db.new_task(con, title, owner, me, parent, priority, expects=expects,
                          timeout_s=timeout_s)
        _link_delivery(con, tid, owner, rid)
        db.add_event(con, me, "task_new", {"task_id": tid, "request_id": rid, "owner": owner,
                                           "expects": expects})
        _block_parent_on_child(con, parent, tid, me)
    finally:
        con.close()
    limit = f", timeout {timefmt.span(timeout_s)}" if timeout_s else ""
    return True, (f"task T{tid} opened for {owner} ({priority_ref(priority)}, "
                  f"expects {expects}{limit}): {msg}"), tid


def task_begin(con, me: str, title: str, chat: int, why: str, priority: int | None = None,
               expects: str = "report") -> tuple[bool, str, int | None]:
    """T297 (T291, founder #1648 (a)): the founder's own message to `me` is
    approval enough - `me` opens a task for itself and starts it, with no
    manager to queue it. What stands in for the approval is the record: `why`
    (what led to it) on the event, and request #`chat` linked to the task, so
    the board shows the founder's words behind it. Each message opens one task:
    a message already linked to a task cannot open another."""
    if me == HUMAN:
        return False, "a task is begun by an agent, never in the founder's name", None
    said = textsafe.oneline(why).strip()
    if not said:
        return False, "say what led to this (--why): the record is the approval", None
    row = db.get_request(con, chat)
    if row is None:
        return False, f"no request #{chat}", None
    if row["requester"] != HUMAN or row["target"] != me:
        return False, (f"request #{chat} is not a message from the founder to {me}: "
                       "only that lets you start a task without approval"), None
    used = db.tasks_for_request(con, chat)
    if used:
        return False, f"request #{chat} already opened T{used[0]['id']}", None
    if priority is None:
        priority = db.DEFAULT_PRIORITY
    tid = db.new_task(con, title, me, me, None, priority, expects=expects)
    db.link_request(con, tid, chat)
    held = db.current_task(con, me)
    if held is None:
        db.set_current_task(con, me, tid)
    db.add_event(con, me, "task_begin", {"task_id": tid, "chat_request": chat, "why": said})
    return True, (f"task T{tid} begun from founder message #{chat}" +
                  (f" and is now your current task" if held is None else
                   f"; T{held} is still current - `ocp task start T{tid}` when you switch")), tid


def task_start(con, me: str, tid: int) -> tuple[bool, str]:
    """T292 bullet 3, the other way a current task is set: `me` picking up
    T<n> explicitly, for when it was not made current at delivery because
    `me` already held one then. Only the task's own owner may - the current
    task is whose work it names, not the opener's to hand out - and only
    while it is open."""
    row = db.get_task(con, tid)
    if row is None:
        return False, f"no task T{tid}"
    if row["owner"] != me:
        return False, f"task T{tid} is {row['owner']}'s, not yours to start"
    if row["closed_by"]:
        return False, f"task T{tid} is closed - nothing to start"
    if row["done_evidence"]:
        return False, f"task T{tid} is already done - nothing to start"
    held = db.current_task(con, me)
    db.set_current_task(con, me, tid)
    for r in db.task_requests(con, tid):
        if _asks(r) and r["target"] == me and not r["delivered_at"]:
            db.promote_to_front(con, r["id"])
    db.add_event(con, me, "task_start", {"task_id": tid, "was_current": held})
    back = ""
    if held is not None and held != tid:
        prev = db.get_task(con, held)
        if prev is not None and not prev["closed_by"] and not prev["done_evidence"]:
            back = f"; T{held} goes back to queued (your `ocp progress` note is kept)"
    memos = memo_summary(con, tid)
    return True, f"T{tid} is now {me}'s current task{back}" + (f"\n{memos}" if memos else "")


def task_note(con, me: str, tid: int, text: str) -> tuple[bool, str]:
    """A mid-task correction to whoever holds T<n> - "the sha moved", "use branch Y" - is a
    memo on the task (T521, founder #6225): the same author and time rules as `ocp task memo
    T<n> add`, typed to the holder at once while the task is doing and silent otherwise. New
    work is `task new --parent`, not a note."""
    row = db.get_task(con, tid)
    if row is None:
        return False, f"no task T{tid}"
    if row["closed_by"]:
        what = "discarded" if row["discarded_by"] else "closed"
        return False, f"task T{tid} is already {what} - nothing left to note"
    if row["done_evidence"]:
        return False, f"task T{tid} is already done - nothing left to note"
    ok, msg, _ = memo_add(con, tid, me, textsafe.oneline(text).strip())
    return ok, msg


def memo_summary(con, tid: int, cap: int = 500) -> str:
    """The task's memos in one short line - author, time, body - for a delivery or a start
    (T521); "" when it has none. Oldest first, cut at `cap` characters."""
    parts = [f"{m['author']} {timefmt.stamp(m['edited_at'] or m['created_at'], timefmt.SHORT)}: "
             f"{textsafe.oneline(m['body'])[:160]}" for m in db.task_memos(con, tid)]
    if not parts:
        return ""
    text = "Memos - " + " | ".join(parts)
    return text if len(text) <= cap else text[:cap].rstrip() + "…"


def _handoff_refusal(con, row, me: str, to: str, note: str) -> str | None:
    """Why `me` may not hand this task to `to`, or None. A task moves to another
    owner when its owner (or the founder) says so, with a note. Who reviews and who
    merges is the project's guideline, not a rule here: the owner keeps its task
    and waits on a child task it opens for them."""
    tid = row["id"]
    if row["closed_by"] or row["done_evidence"]:
        return f"task T{tid} is already finished - nothing to hand over"
    if not note:
        return "a handoff needs a note: what changed, and what the receiver should know"
    if to != HUMAN and to not in paths.list_agents():
        return f"no agent named {to}"
    if to == row["owner"]:
        return f"T{tid} is already {to}'s"
    if me != HUMAN and me != row["owner"]:
        return f"T{tid} is {row['owner']}'s: only its owner hands it on"
    return None


ASSIGN_HOWS = ("note", "handoff")


def assign_task(con, tid: int, to: str, how: str, text: str) -> tuple[bool, str]:
    """The founder answering from a task's page: `how` "note" sends `text` to whoever holds it
    (`task_note`), "handoff" moves it to `to` with `text` as the note (`task_handoff`)."""
    if how not in ASSIGN_HOWS:
        return False, f"assign is one of {', '.join(ASSIGN_HOWS)}"
    if how == "handoff":
        return task_handoff(con, HUMAN, tid, to, textsafe.oneline(text).strip())
    return task_note(con, HUMAN, tid, text)


def task_handoff(con, me: str, tid: int, to: str, note: str) -> tuple[bool, str]:
    """One verb for passing a task along: its owner changes to `to`, the note goes
    to `to` with the task, and both the note and the handoff stay in the task's
    record (`db.task_handoffs`, in the order they happened). A required note
    replaces the separate ask: nothing else is sent."""
    row = db.get_task(con, tid)
    if row is None:
        return False, f"no task T{tid}"
    said = textsafe.text(note or "").strip()
    why = _handoff_refusal(con, row, me, to, said)
    if why:
        return False, why
    # The delivery is the only thing that can be refused past this point (the
    # routing table), so it goes first: a refusal leaves the task where it was.
    ok, msg, rid = askmod.ask(to, f"T{tid} handed to you by {me}: {row['title']}\n\n{said}",
                              requester=me, kind="task", park=False)
    if not ok:
        return False, msg
    old = row["owner"]
    for r in db.task_requests(con, tid):
        if r["state"] == "queued" and r["target"] == old and r["id"] != rid:
            askmod.resolve(r["id"], f"handed to {to}", notify=False)
    db.edit_task(con, tid, me, owner=to)
    db.link_request(con, tid, rid)
    db.mark_send_now(con, rid)      # T596: a handoff is typed on arrival, whatever its holder is doing
    db.add_event(con, me, "task_handoff", {"task_id": tid, "from": old, "to": to, "note": said,
                                            "request_id": rid})
    if db.current_task(con, old) == tid:
        db.set_current_task(con, old, None)
        if advance_current_task(con, old):
            askmod.push(con, old)
    if to != HUMAN:                 # the founder holds no current task and is not typed at
        if db.current_task(con, to) is None:
            db.set_current_task(con, to, tid)
        askmod.push(con, to, rid)
    return True, f"T{tid} handed to {to}: {msg}"


PRIORITIES = (1, 2, 3)


def priority_ref(n: int) -> str:
    """How a priority is written wherever it is printed or typed: P2."""
    return f"P{int(n)}"


def parse_priority(text) -> int:
    """The priority in `P1`, `p2` or a bare `3`; anything but 1 to 3 is refused."""
    t = str(text).strip().upper()
    if t.startswith("P"):
        t = t[1:]
    if t.isdigit() and int(t) in PRIORITIES:
        return int(t)
    raise ValueError(f"'{text}' is not a priority - write P1 (high), P2 (default) or P3 (low)")


# T286: `ocp task new` requires -t/--timeout; `ocp task edit --timeout`
# extends it. Both refuse anything longer than this.
MAX_TASK_TIMEOUT_S = 4 * 3600
# What a task opened inside ocp (a finding, a hold, `task begin`) gets when it
# becomes doing: those never pass `task new -t`, and "every task has a timeout".
DEFAULT_TASK_TIMEOUT_S = MAX_TASK_TIMEOUT_S


def parse_task_timeout(text) -> int:
    """A task timeout (`90m`, `2h`) in seconds, refusing anything past
    MAX_TASK_TIMEOUT_S - the one parser both `-t/--timeout` (new) and
    `--timeout` (edit) use, so the max is enforced in one place."""
    secs = timefmt.parse_span(text)
    if secs > MAX_TASK_TIMEOUT_S:
        raise ValueError(f"a task timeout is at most {timefmt.span(MAX_TASK_TIMEOUT_S)} "
                          f"({text} is longer)")
    return secs


def memos_of(con, tid: int) -> list[dict]:
    """T493: a task's memos as data, oldest first: `id`, `author`, `body`, `created_at`,
    `edited_at` (None until edited). Everyone may read them."""
    return [dict(m) for m in db.task_memos(con, tid)]


def task_timeline(con, tid: int) -> list[dict]:
    """T501: what happened on a task as one list, oldest first, for its page: memos and handoffs (a
    note is a memo), each {ts, who, kind, text, memo (a memo's id, else None), edited_at}."""
    out = [{"ts": m["created_at"], "who": m["author"], "kind": "memo", "text": m["body"],
            "memo": m["id"], "edited_at": m["edited_at"]} for m in memos_of(con, tid)]
    for h in db.task_handoffs(con, tid):
        out.append({"ts": h["ts"], "who": f"{h['from']} → {h['to']}", "kind": "handoff", "text": h["note"],
                    "memo": None, "edited_at": None})
    return sorted(out, key=lambda e: e["ts"])


def _memo_guard(row, by: str) -> str | None:
    """A memo is anyone's to add; only its author or the founder edits or deletes it."""
    if by == HUMAN or by == row["author"]:
        return None
    return f"memo {row['id']} is {row['author']}'s: only its author or the founder edits or deletes it"


def _tell_memo_holder(con, tid: int, by: str, what: str, text: str) -> None:
    """T493: a memo changing on a task that is doing is told to its holder at once, typed like
    a say; on any other task, or when the holder made the change, nothing is sent."""
    row = db.get_task(con, tid)
    if row is None or row["owner"] in (HUMAN, by) or task_state(con, row) != "doing":
        return
    _type_say(con, row["owner"], f"T{tid} memo {what} by {by}"
              + (f": {textsafe.oneline(text)[:400]}" if text else ""), "task memo")


def _type_say(con, owner: str, body: str, why: str) -> None:
    """A say from the task board to `owner`, typed at once."""
    rid = db.add_request(con, _UNBLOCK_REQUESTER, owner, body, None, None,
                         "fire_and_forget", "say", 0, None)
    db.mark_send_now(con, rid)
    askmod.deliver(rid, _UNBLOCK_REQUESTER, owner, body, None,
                   team.Decision(True, why, kind="say", response="fire_and_forget",
                                 creates_gate=False, chain_depth=0))
    askmod.push(con, owner, rid)


def memo_add(con, tid: int, by: str, body: str) -> tuple[bool, str, int | None]:
    """Add `by`'s memo to task `tid`. (ok, message, memo id)."""
    if db.get_task(con, tid) is None:
        return False, f"no task T{tid}", None
    if not body.strip():
        return False, "a memo needs some text", None
    mid = db.add_memo(con, tid, by, body)
    _tell_memo_holder(con, tid, by, "added", body)
    return True, f"memo {mid} added to T{tid}", mid


def memo_edit(con, mid: int, by: str, body: str) -> tuple[bool, str]:
    """Replace a memo's text and stamp `edited_at`; the same text is no edit."""
    row = db.get_memo(con, mid)
    if row is None:
        return False, f"no memo {mid}"
    refusal = _memo_guard(row, by)
    if refusal:
        return False, refusal
    if not body.strip():
        return False, "a memo needs some text"
    if body == row["body"]:
        return False, f"memo {mid} already reads that way - nothing to edit"
    db.edit_memo(con, mid, body)
    _tell_memo_holder(con, row["task_id"], by, "edited", body)
    return True, f"memo {mid} on T{row['task_id']} edited"


def memo_delete(con, mid: int, by: str) -> tuple[bool, str]:
    row = db.get_memo(con, mid)
    if row is None:
        return False, f"no memo {mid}"
    refusal = _memo_guard(row, by)
    if refusal:
        return False, refusal
    db.delete_memo(con, mid)
    _tell_memo_holder(con, row["task_id"], by, "deleted", "")
    return True, f"memo {mid} deleted from T{row['task_id']}"


def edit_task(con, tid: int, by: str, owner: str | None = None,
              title: str | None = None, priority: int | None = None,
              timeout_s: int | None = None) -> tuple[bool, str]:
    """Change a task's owner, title, priority and/or timeout; opener or founder only
    (an owner change fixes a wrong name, it is not a handoff - #1914; a caller that changes
    the owner follows it, once committed, with `send_moved_task`). Refused when
    nothing changes, except --timeout, which always restarts the deadline (only while
    doing) so extending by the same length works."""
    row = db.get_task(con, tid)
    if row is None:
        return False, f"no task T{tid}"
    guard = _task_edit_guard(row, tid, by)
    if guard:
        return False, guard
    if owner is None and title is None and priority is None and timeout_s is None:
        return False, ("edit needs a new owner, a new title, a new priority, --timeout, "
                        "or any of them")
    changes: dict = {}
    new_owner = None
    if owner is not None and owner != row["owner"]:
        if owner not in paths.list_agents():
            return False, f"no agent named {owner}"
        new_owner = owner
        changes["owner"] = {"old": row["owner"], "new": new_owner}
    new_title = None
    if title is not None:
        t = textsafe.oneline(title).strip()
        if not t:
            return False, "a task needs a title"
        if t != row["title"]:
            new_title = t
            changes["title"] = {"old": row["title"], "new": new_title}
    new_priority = None
    if priority is not None:
        if priority not in PRIORITIES:
            return False, f"{priority} is not a priority - P1, P2 or P3"
        if priority != row["priority"]:
            new_priority = priority
            changes["priority"] = {"old": priority_ref(row["priority"]),
                                   "new": priority_ref(priority)}
    if timeout_s is not None:
        if timeout_s <= 0:
            return False, "a task timeout is at least 1s - it counts time still to run, not none"
        if timeout_s > MAX_TASK_TIMEOUT_S:
            return False, (f"a task timeout is at most {timefmt.span(MAX_TASK_TIMEOUT_S)} - "
                           f"{timefmt.span(timeout_s)} is longer")
        doing_now = is_doing(con, tid)
        changes["timeout"] = {"old": timefmt.span(row["timeout_s"]) if row["timeout_s"] else "none",
                              "new": f"{timefmt.span(timeout_s)}, from now" if doing_now
                              else f"{timefmt.span(timeout_s)}, once it is doing"}
    else:
        doing_now = False
    if not changes:
        return False, f"task T{tid} already reads that way - nothing to edit"
    db.edit_task(con, tid, by, owner=new_owner, title=new_title, priority=new_priority,
                 timeout_s=timeout_s, is_doing=doing_now)
    db.add_event(con, by, "edit_task", {"task_id": tid, **changes})
    said = ", ".join(f"{k} {v['old']!r} -> {v['new']!r}" for k, v in changes.items())
    return True, f"task T{tid} edited by {by}: {said}"


def send_moved_task(con, tid: int, old_owner: str, by: str) -> None:
    """T545: after `edit_task` gave `tid` to a new owner - once its write has committed, since
    `resolve` and the delivery write through their own connections. The old owner's request for it
    is taken back and the new owner is asked the same brief now, as `task_new` would (the task row
    keeps a title only, so the body is the request's); an old owner that had the task in hand is told
    to stop. Left as it was, the request stayed queued for the old owner and the new one saw a task
    already being asked. No-op if the owner did not change."""
    row = db.get_task(con, tid)
    if row is None or row["owner"] == old_owner:
        return
    new_owner = row["owner"]
    taken = [r for r in db.task_requests(con, tid) if r["state"] == "queued" and r["target"] == old_owner]
    in_hand = db.current_task(con, old_owner) == tid or any(r["delivered_at"] for r in taken)
    for r in taken:
        askmod.resolve(r["id"], f"moved to {new_owner}", state="withdrawn", notify=False)
    if db.current_task(con, old_owner) == tid:
        db.set_current_task(con, old_owner, None)
        if advance_current_task(con, old_owner):
            askmod.push(con, old_owner)
    if in_hand and old_owner != HUMAN:
        _type_say(con, old_owner, f"T{tid} moved to {new_owner} by {by} - stop work on it", "task moved")
    if taken and new_owner != HUMAN:
        ok, msg, rid = askmod.ask(new_owner, taken[0]["body"], requester=taken[0]["requester"],
                                  kind="task", on_behalf_of=True, park=False)
        if ok:
            _link_delivery(con, tid, new_owner, rid)
            askmod.push(con, new_owner)
            return
        _note_refusal(con, new_owner, tid, msg)
        db.set_task_queued(con, tid, taken[0]["requester"])     # the tick tries again, with the title
    if advance_current_task(con, new_owner):
        askmod.push(con, new_owner)


def _under(con, tid: int, ancestor: int) -> bool:
    """Is `ancestor` `tid` itself or somewhere up its parent chain."""
    seen: set[int] = set()
    while tid and tid not in seen:
        if tid == ancestor:
            return True
        seen.add(tid)
        row = db.get_task(con, tid)
        tid = row["parent_task"] if row else None
    return False


def _merge_refusal(con, keep_row, row, by: str) -> str | None:
    """Why `row` cannot be folded into `keep_row`, or None."""
    tid, keep = row["id"], keep_row["id"]
    guard = _task_edit_guard(row, tid, by)
    if guard:
        return guard
    if row["closed_by"]:
        return f"task T{tid} is already closed"
    if row["done_evidence"]:
        return f"task T{tid} is done - `ocp close T{tid}` signs it off, it is not merged"
    # T294's auto-block on a child (parent_task == tid) is not a reason to
    # refuse: `move_task_links` already reparents that child to `keep`, same
    # as before the auto-block existed. Only a blocker that is not also a
    # child - an explicit `--blocked-by`, or a wait it inherited some other
    # way - still refuses.
    blockers = [b for b in db.task_blockers(con, tid) if b["parent_task"] != tid]
    if blockers:
        ids = ", ".join(f"T{b['id']}" for b in blockers)
        return f"task T{tid} is waiting on {ids} - it cannot be merged until they are done"
    if _under(con, keep, tid):
        return f"task T{keep} is under T{tid} - folding T{tid} into it would loop them"
    for dep in db.tasks_blocked_by(con, tid):
        if dep["task_id"] != keep and _blocked_by_cycle(con, dep["task_id"], keep):
            return (f"T{dep['task_id']} waits on T{tid}; waiting on T{keep} instead "
                    "would be a cycle")
    return None


def _merge_checks(con, keep: int, others: list[int], by: str):
    """(refusal, keep_row, rows) for folding `others` into `keep`. Read inside
    the merge's own transaction, so what is checked is what is written."""
    keep_row = db.get_task(con, keep)
    if keep_row is None:
        return f"no task T{keep}", None, []
    if not others:
        return "merge needs at least one task to fold in", None, []
    if len(set(others)) != len(others):
        return "a task is named twice", None, []
    if keep in others:
        return f"task T{keep} cannot merge into itself", None, []
    guard = _task_edit_guard(keep_row, keep, by)
    if guard:
        return guard, None, []
    if keep_row["closed_by"]:
        return f"task T{keep} is closed - nothing can merge into it", None, []
    if keep_row["done_evidence"]:
        return f"task T{keep} is done - nothing can merge into it", None, []
    rows = []
    for tid in others:
        row = db.get_task(con, tid)
        if row is None:
            return f"no task T{tid}", None, []
        why = _merge_refusal(con, keep_row, row, by)
        if why:
            return why, None, []
        rows.append(row)
    return None, keep_row, rows


def merge_tasks(con, keep: int, others: list[int], by: str) -> tuple[bool, str]:
    """Fold `others` into `keep` (T68): each one's title is added to the kept
    title, its requests, sub-tasks and the tasks waiting on it move to `keep`,
    and it closes as merged into `keep`. Whoever may edit a task may merge it
    (its opener, or the founder), and the same goes for `keep`. All or none:
    one refusal leaves every task as it was. Every check runs inside the
    write's transaction, so a blocker or parent that changes meanwhile is seen."""
    con.execute("BEGIN IMMEDIATE")
    try:
        why, keep_row, rows = _merge_checks(con, keep, others, by)
        if why:
            con.execute("ROLLBACK")
            return False, why
        for row in rows:
            if not db.merge_task(con, row["id"], keep, by):
                con.execute("ROLLBACK")
                return False, f"task T{row['id']} was done or closed by someone else just now"
            # keep waiting on the folded task is no wait on itself: that edge goes.
            dropped = row["id"] in db.blocks_on_ids(con, keep)
            db.move_task_links(con, row["id"], keep)
            db.add_event(con, by, "merge_task",
                         {"task_id": row["id"], "into": keep,
                          **({"dropped_wait": True} if dropped else {})})
        title = keep_row["title"] + "".join(
            f" (+ T{r['id']}: {r['title']})" for r in rows)
        db.edit_task(con, keep, by, title=title)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    ids = ", ".join(f"T{r['id']}" for r in rows)
    return True, f"{ids} merged into T{keep} by {by}"


def task_rows(con, owner: str | None = None, state: str | list[str] | None = None,
              include_closed: bool = False, owners: set[str] | None = None) -> list[dict]:
    """The board as data (T68): one dict per task, ordered by priority then by
    number. What a screen or `ocp task list` draws from, so both say the same.

    `ref` is T79, `priority` 1 to 3, `state` as `task_state`, `closer` who may
    close it (`task_asker`), `blocked_by` the open tasks it waits on, and
    `last_activity` the latest time anything happened to it or a request
    linked to it. Closed, discarded and merged tasks are left out unless
    `include_closed`, or unless `state` names one. `owner` and `state` narrow
    the rows; `state` may be one name or several. `owners`, when given, is every
    owner that may appear (see `reach`).
    """
    states = None if state is None else {state} if isinstance(state, str) else set(state)
    ended = ("closed", "discarded", "merged")
    show_ended = include_closed or bool(states and states & set(ended))
    tasks = db.all_tasks(con) if show_ended else db.open_tasks(con)
    facts = _board_facts(con, [t for t in tasks if not t["closed_by"]])
    out = []
    for t in tasks:
        if owner is not None and t["owner"] != owner:
            continue
        if owners is not None and t["owner"] not in owners:
            continue
        st = facts.state(t)
        if states is not None and st not in states:
            continue
        out.append({
            "id": t["id"], "ref": task_ref(t["id"]), "title": t["title"],
            "owner": t["owner"], "opened_by": t["opened_by"],
            "parent_task": t["parent_task"], "priority": t["priority"],
            "priority_ref": priority_ref(t["priority"]), "state": st,
            "closer": facts.asker(t),
            "blocked_by": facts.blockers.get(t["id"], []),
            "last_activity": facts.last_activity(t),
            "row": t,
        })
    return out


class _board_facts:
    """What `task_rows` needs about every task, read in a fixed handful of
    queries instead of four or five per task: the requests linked to each task,
    the tasks begun on a founder's message, each task's open children, and what
    blocks each. `state`, `asker` and `last_activity` are the same rules as
    `task_state`, `task_asker` and `db.task_last_activity`, on this data - a test
    holds the two paths to the same answers."""

    def __init__(self, con, open_tasks: list):
        self.links: dict[int, list] = {}
        for r in con.execute(
                "SELECT l.task_id AS tid, r.id, r.state, r.fyi, r.requester, r.ts, r.resolved_at "
                "FROM task_requests l JOIN requests r ON r.id = l.request_id "
                "ORDER BY l.task_id, r.id"):
            self.links.setdefault(r["tid"], []).append(r)
        self.tasks = {r["id"]: r for r in con.execute(
            "SELECT id, closed_by, done_evidence FROM tasks WHERE id IN "
            "(SELECT current_task FROM agent_state WHERE current_task IS NOT NULL)")}
        self.current = {r["agent"]: r["current_task"] for r in con.execute(
            "SELECT agent, current_task FROM agent_state WHERE current_task IS NOT NULL")}
        self.begun = {r[0] for r in con.execute(
            f"SELECT DISTINCT {db.EVENT_TASK} FROM events WHERE kind = 'task_begin'") if r[0] is not None}
        self.children: dict[int, list[int]] = {}
        for c in open_tasks:
            if c["parent_task"] is not None:
                self.children.setdefault(c["parent_task"], []).append(c["id"])
        self.blockers: dict[int, list[int]] = {}
        for r in con.execute(
                "SELECT b.task_id AS tid, t.id AS blocker FROM task_blocks b "
                "JOIN tasks t ON t.id = b.blocks_on "
                "WHERE t.closed_by IS NULL AND t.done_evidence IS NULL ORDER BY b.task_id, t.id"):
            self.blockers.setdefault(r["tid"], []).append(r["blocker"])

    def _holds(self, row) -> bool:
        """`holds_current` on the loaded facts."""
        held = self.current.get(row["owner"])
        if row["owner"] == HUMAN or held is None or held == row["id"]:
            return True
        cur = self.tasks.get(held)
        return cur is None or bool(cur["closed_by"] or cur["done_evidence"])

    def _asked(self, tid: int) -> bool:
        return (tid in self.begun
                or any(_asks(r) for r in self.links.get(tid, []))
                or any(_asks(r) for c in self.children.get(tid, []) for r in self.links.get(c, [])))

    def state(self, row) -> str:
        asked = self._asked(row["id"])
        return _state_of(row, asked and self._holds(row), asked)

    def asker(self, row) -> str:
        return _asker_of(row, (r["requester"] for r in self.links.get(row["id"], [])))

    def last_activity(self, row) -> int:
        seen = [row[k] or 0 for k in ("created_at", "edited_at", "done_at", "closed_at")]
        for r in self.links.get(row["id"], []):
            seen += [r["ts"] or 0, r["resolved_at"] or 0]
        return int(max(seen))


TASK_STATES = ("draft", "queued", "doing", "done", "closed", "discarded", "merged")
OPEN_STATES = TASK_STATES[:4]
DELEGATED_STATES = ("queued", "doing")     # handed to an owner, not yet done
STALE_AFTER_S = 3 * 86400
BATCH_VERBS = ("close", "discard", "owner", "priority")


def task_idle_s(row: dict, now: float) -> int:
    """Seconds since anything happened to this `task_rows` row."""
    return max(0, int(now - row["last_activity"]))


def task_stale(row: dict, now: float) -> bool:
    """A task waiting to be approved or to run that nothing has touched for 3 days: the screen highlights it."""
    return row["state"] in ("draft", "queued") and task_idle_s(row, now) >= STALE_AFTER_S


def task_related_to(row: dict, who: str) -> bool:
    """Does `who` own this `task_rows` row, open it, or have to close it?"""
    return who in (row["owner"], row["opened_by"], row["closer"])


TASK_SORTS = ("priority", "number", "state", "title", "owner", "idle")
_STATE_ORDER = {"draft": 0, "queued": 1, "doing": 2, "done": 3, "closed": 4, "discarded": 5, "merged": 6}


def sort_task_rows(rows: list[dict], by: str, reverse: bool = False, now: float | None = None) -> list[dict]:
    """`rows` ordered by one of TASK_SORTS: `priority` (P1 first), `number` (T9 before
    T10), `state` (drafts first), `title`, `owner`, `idle` (longest first when reversed);
    ties fall to the number."""
    now = time.time() if now is None else now
    keys = {"priority": lambda r: (r["priority"], r["id"]),
            "number": lambda r: r["id"],
            "state": lambda r: (_STATE_ORDER.get(r["state"], 9), r["id"]),
            "title": lambda r: (r["title"].lower(), r["id"]),
            "owner": lambda r: (r["owner"].lower(), r["id"]),
            "idle": lambda r: (task_idle_s(r, now), r["id"])}
    return sorted(rows, key=keys.get(by, keys["priority"]), reverse=reverse)


def batch_task(con, verb: str, tids: list[int], by: str, arg: str = "") -> list[tuple[int, bool, str]]:
    """`verb` on each task in `tids`, one (id, ok, message) per task: a refusal
    carries its reason and does not stop the others. `arg` is the reason for
    discard, the agent for owner, the priority for priority. A bad `arg` refuses
    every task the same way, before any is touched."""
    if verb not in BATCH_VERBS:
        return [(t, False, f"no such action: {verb}") for t in tids]
    if verb == "discard" and not arg.strip():
        return [(t, False, "discard needs a one-line reason") for t in tids]
    if verb == "owner" and not arg.strip():
        return [(t, False, "owner needs an agent name") for t in tids]
    prio = None
    if verb == "priority":
        try:
            prio = parse_priority(arg)
        except ValueError as e:
            return [(t, False, str(e)) for t in tids]
    out = []
    for tid in tids:
        if verb == "close":
            ok, msg = close_task(con, tid, by)
        elif verb == "discard":
            ok, msg = discard_task(con, tid, by, arg)
        elif verb == "owner":
            was = (db.get_task(con, tid) or {"owner": None})["owner"]
            ok, msg = edit_task(con, tid, by, owner=arg.strip())
            if ok:
                send_moved_task(con, tid, was, by)
        else:
            ok, msg = edit_task(con, tid, by, priority=prio)
        out.append((tid, ok, msg))
    return out


def _blocked_by_cycle(con, tid: int, blocks_on: int, seen: set[int] | None = None) -> bool:
    """True if `tid` waiting on `blocks_on` would close a loop: walking from
    `blocks_on` through what it itself waits on reaches `tid` again. Walks
    every edge `task_blocks` holds - `add_block` refuses to write one naming
    an already-closed task on either end, and `close_task` clears a task's
    own incoming edges in the same transaction that closes it, so a live row
    should never name a closed task, but the walk does not depend on that:
    it follows whatever is there."""
    if blocks_on == tid:
        return True
    seen = seen if seen is not None else set()
    if blocks_on in seen:
        return False
    seen.add(blocks_on)
    return any(_blocked_by_cycle(con, tid, nxt, seen)
              for nxt in db.blocks_on_ids(con, blocks_on))


def _cycle_path(con, tid: int, blocks_on: int) -> list[int] | None:
    """The waits from `blocks_on` back to `tid` that `tid` waiting on `blocks_on` would close into
    a loop (`[blocks_on, ..., last]`, where `last` waits on `tid`), or None when it would not.
    The same walk as `_blocked_by_cycle`, keeping the way it went so the refusal can name it."""
    seen: set[int] = set()

    def walk(node: int) -> list[int] | None:
        if node in seen:
            return None
        seen.add(node)
        nexts = db.blocks_on_ids(con, node)
        if tid in nexts:
            return [node]
        for nxt in nexts:
            found = walk(nxt)
            if found:
                return [node, *found]
        return None

    return walk(blocks_on)


def _blocker_refusal(con, blocks_on: int) -> str | None:
    """Why `blocks_on` cannot be waited on: missing or already closed."""
    row = db.get_task(con, blocks_on)
    if row is None:
        return f"no task T{blocks_on}"
    if row["closed_by"]:
        return f"task T{blocks_on} is closed"
    return None


def add_block(con, tid: int, blocks_on: int, by: str) -> tuple[bool, str]:
    """`tid` cannot be worked until `blocks_on` closes (task #21/#1884).
    Refused for a task already closed on either end - a row naming one would
    never be cleared, since only closing `blocks_on` clears it, and closing
    an already-closed task is not a thing this board does."""
    row = db.get_task(con, tid)
    if row is None:
        return False, f"no task T{tid}"
    guard = _task_edit_guard(row, tid, by)
    if guard:
        return False, guard
    if db.get_task(con, blocks_on) is None:
        return False, f"no task T{blocks_on}"
    if row["closed_by"]:
        return False, f"task T{tid} is closed"
    gone = _blocker_refusal(con, blocks_on)
    if gone:
        return False, gone
    if blocks_on == tid:
        return False, f"task T{tid} cannot block itself"
    loop = _cycle_path(con, tid, blocks_on)
    if loop:
        ring = " -> ".join(f"T{t}" for t in [tid, *loop, tid])
        return False, f"task T{tid} cannot wait on T{blocks_on}: that would be a cycle, {ring}"
    db.add_block(con, tid, blocks_on)
    db.add_event(con, by, "block_task", {"task_id": tid, "blocks_on": blocks_on})
    return True, f"task T{tid} now waits on T{blocks_on}"


def unblock(con, tid: int, blocks_on: int, by: str) -> tuple[bool, str]:
    """Remove one blocking edge - the counterpart of `add_block`. Never
    advances `tid`'s owner itself: its only caller (`cmd_task_edit`) runs
    inside a shared transaction that may still be open, and `advance_current_task`
    writes through a second connection - the caller does that once its own
    transaction has committed (T325: otherwise a task skipped over blocked
    is never reconsidered once its last blocker clears)."""
    row = db.get_task(con, tid)
    if row is None:
        return False, f"no task T{tid}"
    guard = _task_edit_guard(row, tid, by)
    if guard:
        return False, guard
    if not db.remove_block(con, tid, blocks_on):
        return False, f"task T{tid} is not waiting on T{blocks_on}"
    db.add_event(con, by, "unblock_task",
                 {"task_id": tid, "blocks_on": blocks_on, "after_request": _newest_request(con)})
    return True, f"task T{tid} no longer waits on T{blocks_on}"


def reason_gaps(con, window_days: int = 30) -> dict:
    """#1349/#1366: how many retirements carry no reason, counted from the
    exact shapes those verbs write - not a grep over free text, which is the
    measurement orcapipe refused (today's evidence: purser could not size
    duplicated work this way with any confidence). A withdraw with none
    stores a NULL verdict; a dismiss or a replace with none stores the fixed
    phrase with no ": " suffix.

    Two corrections from auditor #1366's should-fix 2, measured against the
    live board: `fyi=0` excludes the rows ask.py's own supersede loop
    replaces - nobody was ever asked for a reason there, so counting them as
    a human omission overstated the gap (20% of one bucket, that day). And
    `resolved_at >= cutoff` bounds the count to the last `window_days`: with
    no window the line reports history forever, dominated by rows that
    predate `--reason` existing rather than current practice.

    Closures are reported as their own counts, not folded into a "gap":
    `ocp task done` requires exactly one of a sha, a request, or a reason, so
    a done task is never blank, and counting a sha as a missing reason (the
    previous shape here) pointed a reader at the wrong thing to fix. Which
    one a row was is read from `done_via`, set when the evidence was given
    from the argument the caller actually gave - not guessed back from the
    text, which a --reason forced past looks_like_sha (hex-looking words,
    still a reason) would misread as a sha. #71 moved this evidence from the
    close to the done stage; the counts still measure the same thing.
    """
    cutoff = int(time.time()) - window_days * 86400
    def one(where: str) -> int:
        return con.execute(
            f"SELECT COUNT(*) FROM requests WHERE fyi = 0 AND resolved_at >= ? AND {where}",
            (cutoff,)).fetchone()[0]
    withdrawn_total = one("state = 'withdrawn'")
    withdrawn_blank = one("state = 'withdrawn' AND verdict IS NULL")
    dismissed_total = one("(dismissed_by IS NOT NULL OR verdict = 'read - no reply' "
                          "OR verdict LIKE 'read - no reply: %')")
    dismissed_blank = one("((dismissed_by IS NOT NULL AND dismissed_reason IS NULL) "
                          "OR verdict = 'read - no reply')")
    replaced_total = one("state = 'replaced'")
    replaced_blank = one("state = 'replaced' AND verdict NOT LIKE '%: %'")
    def closures(via: str) -> int:
        return con.execute(
            "SELECT COUNT(*) FROM tasks WHERE done_via = ? AND done_at >= ?",
            (via, cutoff)).fetchone()[0]
    return {
        "retirements": withdrawn_total + dismissed_total + replaced_total,
        "retirements_blank": withdrawn_blank + dismissed_blank + replaced_blank,
        "closures_sha": closures("sha"),
        "closures_request": closures("request"),
        "closures_reason": closures("reason"),
    }
