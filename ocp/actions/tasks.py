"""Tasks: refs, findings, done/close/discard/reopen, edit, and what blocks what."""
from __future__ import annotations

import re
import time
from pathlib import Path

from ..team import ask as askmod
from .. import db
from ..infra import gitrun
from ..infra import paths
from ..team import team
from ..infra import textsafe
from ..infra.yamlcompat import ConfigError

from .routing import HUMAN, supervisor_of


def finding_owner(me: str) -> str | None:
    """The manager `me` answers to by the supervisor edges the daemon routes
    on, or `me` when it is itself some agent's supervisor. None when that is
    not a registered agent, so a task never lands on a name nobody answers to."""
    try:
        t = team.Team.load()
    except (FileNotFoundError, ConfigError):
        return None
    owner = supervisor_of(t, me)
    if owner is None and any(e.get("relation") == "supervisor" and e.get("from") == me
                             for e in t.edges):
        owner = me
    return owner if owner in paths.list_agents() else None


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


def open_finding_tasks(con, rid: int, findings: list[str], owner: str) -> list[int]:
    """Task #73 (founder #2150, #2154 point 2): a finding the resolver is
    deferring rather than blocking the review on becomes a task in the same
    step, linked to the request it came out of. The task is owned and opened
    by the resolver's manager (#1914), never by the resolver."""
    tids = []
    for title in findings:
        tid = db.new_task(con, textsafe.oneline(title).strip(), owner, owner)
        db.link_request(con, tid, rid)
        tids.append(tid)
    return tids


def hold_refusal(con, rid: int, blocks_on: list[int]) -> str | None:
    """The reason a hold on request `rid` cannot wait on these tasks, or None.
    A task the hold sits under can never be one of them: the parent cannot
    close over an open child, so the two would wait on each other."""
    under = set()
    for t in db.tasks_for_request(con, rid):
        while t is not None and t["id"] not in under:
            under.add(t["id"])
            t = db.get_task(con, t["parent_task"]) if t["parent_task"] else None
    for b in blocks_on:
        why = _blocker_refusal(con, b)
        if why:
            return why
        if b in under:
            return f"task T{b} is what this hold sits under - it cannot wait on it"
    return None


def open_hold_task(con, rid: int, title: str, holder: str, blocks_on: list[int]) -> list[int]:
    """An answer that puts the work on hold keeps it as an open task owned by
    the one holding it, linked to the request and under the task that request
    belongs to, so the parent cannot close over it. Waiting on other tasks is
    the same block edge `ocp task link --blocks-on` writes: closing the last
    one notifies the holder. Callers check `hold_refusal` first."""
    linked = [t for t in db.tasks_for_request(con, rid) if not t["closed_by"]]
    tid = db.new_task(con, textsafe.oneline(title).strip(), holder, holder,
                      parent_task=linked[0]["id"] if linked else None)
    db.link_request(con, tid, rid)
    for b in blocks_on:
        ok, msg = add_block(con, tid, b, holder)
        if not ok:
            raise ValueError(msg)
    return [tid]


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


def task_asker(con, row) -> str:
    """Who task `row` answers to when it closes (#71): the founder, if any
    request linked to it came from the founder - the task exists because the
    founder asked for something. Otherwise whoever opened the task itself: a
    task the manager opens on its own (an auditor finding, internal cleanup)
    is the manager's own to close, never its own superior's."""
    for r in db.task_requests(con, row["id"]):
        if r["requester"] == HUMAN:
            return HUMAN
    return row["opened_by"]


def task_state(con, row) -> str:
    """One of waiting, doing, done, closed (#71), discarded (#85) or merged (T68). done,
    closed and discarded are written, once each; waiting and doing are never stored - read live from
    whether a request is currently queued against this task or an open child
    of it, the same live read the board already does for "who holds it now",
    so there is nothing here that a write path must remember to keep in
    sync."""
    if row["merged_into"]:
        return "merged"
    if row["discarded_by"]:
        return "discarded"
    if row["closed_by"]:
        return "closed"
    if row["done_evidence"]:
        return "done"
    if any(r["state"] == "queued" for r in db.task_requests(con, row["id"])):
        return "doing"
    if any(r["state"] == "queued"
           for c in db.open_child_tasks(con, row["id"])
           for r in db.task_requests(con, c["id"])):
        return "doing"
    return "waiting"


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
    if by != row["owner"] and by not in _task_done_targets(con, tid):
        return False, (f"task T{tid} can only be marked done by its owner "
                       f"({row['owner']}) or the target of a request linked to it")
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
    if not db.mark_task_done(con, tid, mark, via):
        return False, f"task T{tid} was marked done by someone else just now"
    db.add_event(con, by, "mark_task_done", {"task_id": tid, "done": mark})
    return True, f"task T{tid} done ({mark})"


def close_task(con, tid: int, by: str) -> tuple[bool, str]:
    """Close a task on the board (#71) - the asker's own sign-off, final.
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
    if by != asker:
        return False, f"task T{tid} can only be closed by {asker} - whoever asked for it"
    kids = db.open_child_tasks(con, tid)
    if kids:
        ids = ", ".join(f"T{k['id']}" for k in kids)
        return False, f"task T{tid} still has open child tasks ({ids}) - close them first"
    blockers = db.task_blockers(con, tid)
    if blockers:
        ids = ", ".join(f"T{b['id']}" for b in blockers)
        return False, f"task T{tid} is waiting on {ids} - it closes only once they do"
    con.execute("BEGIN IMMEDIATE")
    try:
        if not db.close_task(con, tid, by):
            con.execute("ROLLBACK")
            return False, f"task T{tid} was closed by someone else just now"
        db.add_event(con, by, "close_task", {"task_id": tid, "closed_by": by})
        notices = _clear_blocks_on(con, tid, by)
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    for rid, owner, text in notices:
        askmod.deliver(rid, _UNBLOCK_REQUESTER, owner, text, None,
                       team.Decision(True, "task unblocked", kind="report",
                                     response="fire_and_forget", creates_gate=False,
                                     chain_depth=0))
    return True, f"task T{tid} closed by {by}"


def discard_task(con, tid: int, by: str, reason: str) -> tuple[bool, str]:
    """Throw a task away before it is done (task #85) - the asker's own call
    (see `task_asker`), in one step with a one-line reason. It is recorded as
    discarded (who, why, and when in closed_at), never as finished work: no
    board, list or 'waiting for check' group reads it as done.

    close_task's guards hold: refused while a child task is open or while this
    task waits on an open blocker, and refused once it is done or already
    closed. A request still queued against it also refuses: discarding
    leaves nothing on the board pointing at that request, so its target
    would keep working on it. Every block naming it clears in the same transaction, and a task
    left with no open blocker is told, as close does."""
    row = db.get_task(con, tid)
    if row is None:
        return False, f"no task T{tid}"
    if row["closed_by"]:
        what = "discarded" if row["discarded_by"] else "closed"
        return False, f"task T{tid} is already {what} ({row['closed_by']})"
    if row["done_evidence"]:
        return False, f"task T{tid} is done - `ocp close T{tid}` signs it off"
    asker = task_asker(con, row)
    if by != asker:
        return False, f"task T{tid} can only be discarded by {asker} - whoever asked for it"
    said = textsafe.oneline(reason).strip()
    if not said:
        return False, "discarding needs a one-line reason"
    kids = db.open_child_tasks(con, tid)
    if kids:
        ids = ", ".join(f"T{k['id']}" for k in kids)
        return False, f"task T{tid} still has open child tasks ({ids}) - deal with them first"
    blockers = db.task_blockers(con, tid)
    if blockers:
        ids = ", ".join(f"T{b['id']}" for b in blockers)
        return False, f"task T{tid} is waiting on {ids} - it cannot be discarded until they close"
    live = [r for r in db.task_requests(con, tid) if r["state"] == "queued"]
    if live:
        ids = ", ".join(f"#{r['id']} (with {r['target']})" for r in live)
        return False, (f"task T{tid} still has a request open: {ids} - withdraw or cancel it "
                       "first, or its target keeps working on thrown-away work")
    con.execute("BEGIN IMMEDIATE")
    try:
        if not db.discard_task(con, tid, by, said):
            con.execute("ROLLBACK")
            return False, f"task T{tid} was done or closed by someone else just now"
        db.add_event(con, by, "discard_task", {"task_id": tid, "reason": said})
        notices = _clear_blocks_on(con, tid, by, "discarded")
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise
    for rid, owner, text in notices:
        askmod.deliver(rid, _UNBLOCK_REQUESTER, owner, text, None,
                       team.Decision(True, "task unblocked", kind="report",
                                     response="fire_and_forget", creates_gate=False,
                                     chain_depth=0))
    return True, f"task T{tid} discarded by {by}: {said}"


def reopen_task(con, tid: int, by: str, reason: str) -> tuple[bool, str]:
    """Send a done task back to doing (#71) - the asker's own call, the
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
    if by != asker:
        return False, f"task T{tid} can only be reopened by {asker} - whoever asked for it"
    said = textsafe.oneline(reason).strip()
    if not said:
        return False, "reopening needs a one-line reason"
    if not db.reopen_task(con, tid):
        return False, f"task T{tid} was closed or reopened by someone else just now"
    db.add_event(con, by, "reopen_task", {"task_id": tid, "reason": said})
    return True, f"task T{tid} reopened by {by}: {said}"


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
    rid = db.add_request(con, _UNBLOCK_REQUESTER, owner, text, None, None,
                         "fire_and_forget", "report", 0, None)
    db.link_request(con, dep_tid, rid)
    db.add_event(con, closer, "task_unblocked",
                {"task_id": dep_tid, "request_id": rid, "closed_task": closed_tid})
    return rid, owner, text


def _clear_blocks_on(con, tid: int, closer: str, how: str = "closed") -> list[tuple[int, str, str]]:
    """After `tid` closes: drop every block row naming it, and for each task
    that leaves with no open blocker left, write a notice for its owner.
    Dropping the row here - not on read - is what makes "once" true: the row
    that would trigger a second notice is gone before anything can re-read
    it, and both the drop and the write happen inside the caller's one
    transaction, so a concurrent close clearing a dependent's other blocker
    either commits first (seen here) or not yet (seen by it instead) -
    never both at once. Returns the notices to deliver once that transaction
    commits."""
    notices = []
    for dep in db.tasks_blocked_by(con, tid):
        db.remove_block(con, dep["task_id"], tid)
        dep_row = db.get_task(con, dep["task_id"])
        if dep_row is None or dep_row["closed_by"]:
            continue
        if db.task_blockers(con, dep["task_id"]):
            continue
        if dep_row["owner"] == closer:
            continue
        notices.append(_notify_unblocked(con, dep["task_id"], dep_row["owner"], closer, tid, how))
    return notices


def _task_edit_guard(row, tid: int, by: str) -> str | None:
    """None if `by` may reshape task `tid` (its opener, or the founder) - a
    task is not a request, it has no `target` a supervisor could redirect -
    otherwise the refusal to show. Shared by every verb that changes a task
    without changing whose it is (edit_task, add_block, unblock)."""
    if by != row["opened_by"] and by != HUMAN:
        return (f"task T{tid} can only be edited by whoever opened it "
               f"({row['opened_by']}) or the founder")
    return None


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


def edit_task(con, tid: int, by: str, owner: str | None = None,
              title: str | None = None, priority: int | None = None) -> tuple[bool, str]:
    """Change a task's owner, title and/or priority. Only the task's opener or the
    founder may - #1914: every orcapipe task is owned by orcapipe itself,
    members only link requests to it, so an owner change here is for fixing
    who was named by mistake, not for handing work along.

    Refused if neither field actually changes anything, so a no-op edit
    cannot be used to touch edited_by/edited_at without saying what moved -
    the same reason close_task refuses a task already closed.
    """
    row = db.get_task(con, tid)
    if row is None:
        return False, f"no task T{tid}"
    guard = _task_edit_guard(row, tid, by)
    if guard:
        return False, guard
    if owner is None and title is None and priority is None:
        return False, "edit needs a new owner, a new title, a new priority, or any of them"
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
    if not changes:
        return False, f"task T{tid} already reads that way - nothing to edit"
    db.edit_task(con, tid, by, owner=new_owner, title=new_title, priority=new_priority)
    db.add_event(con, by, "edit_task", {"task_id": tid, **changes})
    said = ", ".join(f"{k} {v['old']!r} -> {v['new']!r}" for k, v in changes.items())
    return True, f"task T{tid} edited by {by}: {said}"


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
    if db.task_blockers(con, tid):
        ids = ", ".join(f"T{b['id']}" for b in db.task_blockers(con, tid))
        return f"task T{tid} is waiting on {ids} - it cannot be merged until they close"
    if _under(con, keep, tid):
        return f"task T{keep} is under T{tid} - folding T{tid} into it would loop them"
    for dep in db.tasks_blocked_by(con, tid):
        if dep["task_id"] != keep and _blocked_by_cycle(con, dep["task_id"], keep):
            return (f"T{dep['task_id']} waits on T{tid}; waiting on T{keep} instead "
                    "would be a cycle")
    return None


def merge_tasks(con, keep: int, others: list[int], by: str) -> tuple[bool, str]:
    """Fold `others` into `keep` (T68): each one's title is added to the kept
    title, its requests, sub-tasks and the tasks waiting on it move to `keep`,
    and it closes as merged into `keep`. Whoever may edit a task may merge it
    (its opener, or the founder), and the same goes for `keep`. All or none:
    one refusal leaves every task as it was."""
    keep_row = db.get_task(con, keep)
    if keep_row is None:
        return False, f"no task T{keep}"
    if not others:
        return False, "merge needs at least one task to fold in"
    if len(set(others)) != len(others):
        return False, "a task is named twice"
    if keep in others:
        return False, f"task T{keep} cannot merge into itself"
    guard = _task_edit_guard(keep_row, keep, by)
    if guard:
        return False, guard
    if keep_row["closed_by"]:
        return False, f"task T{keep} is closed - nothing can merge into it"
    if keep_row["done_evidence"]:
        return False, f"task T{keep} is done - nothing can merge into it"
    rows = []
    for tid in others:
        row = db.get_task(con, tid)
        if row is None:
            return False, f"no task T{tid}"
        why = _merge_refusal(con, keep_row, row, by)
        if why:
            return False, why
        rows.append(row)
    con.execute("BEGIN IMMEDIATE")
    try:
        for row in rows:
            if not db.merge_task(con, row["id"], keep, by):
                con.execute("ROLLBACK")
                return False, f"task T{row['id']} was done or closed by someone else just now"
            db.move_task_links(con, row["id"], keep)
            db.add_event(con, by, "merge_task", {"task_id": row["id"], "into": keep})
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
              include_closed: bool = False) -> list[dict]:
    """The board as data (T68): one dict per task, ordered by priority then by
    number. What a screen or `ocp task list` draws from, so both say the same.

    `ref` is T79, `priority` 1 to 3, `state` as `task_state`, `closer` who may
    close it (`task_asker`), `blocked_by` the open tasks it waits on, and
    `last_activity` the latest time anything happened to it or a request
    linked to it. Closed, discarded and merged tasks are left out unless
    `include_closed`, or unless `state` names one. `owner` and `state` narrow
    the rows; `state` may be one name or several.
    """
    states = None if state is None else {state} if isinstance(state, str) else set(state)
    ended = ("closed", "discarded", "merged")
    show_ended = include_closed or bool(states and states & set(ended))
    out = []
    for t in (db.all_tasks(con) if show_ended else db.open_tasks(con)):
        if owner is not None and t["owner"] != owner:
            continue
        st = task_state(con, t)
        if states is not None and st not in states:
            continue
        out.append({
            "id": t["id"], "ref": task_ref(t["id"]), "title": t["title"],
            "owner": t["owner"], "opened_by": t["opened_by"],
            "parent_task": t["parent_task"], "priority": t["priority"],
            "priority_ref": priority_ref(t["priority"]), "state": st,
            "closer": task_asker(con, t),
            "blocked_by": [b["id"] for b in db.task_blockers(con, t["id"])],
            "last_activity": db.task_last_activity(con, t["id"]),
            "row": t,
        })
    return out


STALE_AFTER_S = 3 * 86400
BATCH_VERBS = ("close", "discard", "owner", "priority")


def task_idle_s(row: dict, now: float) -> int:
    """Seconds since anything happened to this `task_rows` row."""
    return max(0, int(now - row["last_activity"]))


def task_stale(row: dict, now: float) -> bool:
    """A waiting task nothing has touched for 3 days: the screen highlights it."""
    return row["state"] == "waiting" and task_idle_s(row, now) >= STALE_AFTER_S


def task_related_to(row: dict, who: str) -> bool:
    """Does `who` own this `task_rows` row, open it, or have to close it?"""
    return who in (row["owner"], row["opened_by"], row["closer"])


TASK_SORTS = ("priority", "number", "state", "title", "owner", "idle")
_STATE_ORDER = {"waiting": 0, "doing": 1, "done": 2, "closed": 3, "discarded": 4, "merged": 5}


def sort_task_rows(rows: list[dict], by: str, reverse: bool = False, now: float | None = None) -> list[dict]:
    """`rows` ordered by one of TASK_SORTS: `priority` (P1 first), `number` (T9 before
    T10), `state` (waiting first), `title`, `owner`, `idle` (longest first when reversed);
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
            ok, msg = edit_task(con, tid, by, owner=arg.strip())
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
    if _blocked_by_cycle(con, tid, blocks_on):
        return False, f"task T{tid} cannot wait on T{blocks_on}: that would be a cycle"
    db.add_block(con, tid, blocks_on)
    db.add_event(con, by, "block_task", {"task_id": tid, "blocks_on": blocks_on})
    return True, f"task T{tid} now waits on T{blocks_on}"


def unblock(con, tid: int, blocks_on: int, by: str) -> tuple[bool, str]:
    """Remove one blocking edge - the counterpart of `add_block`."""
    row = db.get_task(con, tid)
    if row is None:
        return False, f"no task T{tid}"
    guard = _task_edit_guard(row, tid, by)
    if guard:
        return False, guard
    if not db.remove_block(con, tid, blocks_on):
        return False, f"task T{tid} is not waiting on T{blocks_on}"
    db.add_event(con, by, "unblock_task", {"task_id": tid, "blocks_on": blocks_on})
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
