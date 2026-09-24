"""What a human can do to an agent, in one place.

`ocp bridge` is the screen the team is run from, so what it does has to live
somewhere that is not the drawing code: a screen that decided anything for
itself would be a second opinion, and this file exists so there is only ever
one.

Two halves. The acting half - wake, close, unstall, park, fold, start, message,
answer, dismiss - is a line of glue each over the call the daemon or the CLI
makes, and returns the one line to show for what happened. The reading half -
messages, stuck, and the details behind a row - asks the database
the questions a screen asks, and asks the daemon's own predicates for anything
it would otherwise have to judge for itself.

Two of them cannot be done from here at all. Logging in needs a human to type a
code into claude's own screen, and starting an agent needs a terminal to start
it in - so those return the command to run and let the caller hand the terminal
over.
"""
from __future__ import annotations

import os
import re
import shlex
import subprocess
import time
from pathlib import Path

from . import (accounts, agents, ask as askmod, blocked, daemon, db, events as evmod,
               fold as foldmod, hr, load as loadmod, observe as obs, orca, paths,
               spawn as spawnmod, status, team, textsafe, triggers, trust, vendors, waiting)
from .yamlcompat import ConfigError

# The name a human's own requests are sent under, and the inbox `ocp bridge`
# shows. Taken from `ask`, which is where escalations are addressed: a second
# copy of the name would empty the panel the day it changed.
HUMAN = askmod.HUMAN


def wake(con, agent: str) -> str:
    """Bring a closed or parked agent back - or, on a FATAL (STALLED) one,
    clear the stall (founder #1620/#1631: unstall folded into wake, behind
    the same confirmation `can()` already asks for a STALLED row)."""
    row = db.get_agent_state(con, agent)
    if row and row["state"] == daemon.STALLED:
        return unstall(con, agent)
    ok, why = daemon.wake_agent(con, agent, lambda _m: None)
    return f"{agent}: {why}" if ok else f"{agent}: not woken - {why}"


def close(con, agent: str) -> str:
    """Give the machine back an idle agent's memory, keeping its terminal."""
    st = status.read_for_agent(agent, agents.current_account(agent, con))
    ok, why = daemon.close_agent(con, agent, lambda _m: None, st)
    return why if ok else f"{agent}: not closed - {why}"


def unstall(con, agent: str) -> str:
    """Clear a stall, which is the one state the daemon will not leave alone."""
    row = db.get_agent_state(con, agent)
    if not row or row["state"] != daemon.STALLED:
        return f"{agent}: not STALLED ({row['state'] if row else 'no state'})"
    db.set_agent_state(con, agent, daemon.QUEUED, detail="cleared by a human")
    for counter in ("failed_attempts", "throttle_count", "wake_failures"):
        db.reset_counter(con, agent, counter)
    db.clear_cursor(con, f"dialog_since:{agent}")
    db.clear_cursor(con, f"dialog_title:{agent}")
    return f"{agent}: STALLED -> QUEUED"


def park(con, agent: str) -> str:
    """Record a handoff, clear the context, and park until the window rolls over.

    `perform_handoff` returns True for three things that are not a handoff -
    merged into one already in flight, too soon after the last, and deferred
    because the agent is mid-turn - so its own log line is the answer, not the
    state afterwards. Reporting the state read RUNNING as though it were a
    result.
    """
    t = triggers.Trigger(True, daemon.BY_HAND_MARK, "parked by a human from the screen")
    said: list[str] = []
    ok = daemon.perform_handoff(con, agent, t, said.append)
    if said:
        return said[-1].strip()
    row = db.get_agent_state(con, agent)
    state = row["state"] if row else "?"
    return f"{agent}: {state}" if ok else f"{agent}: handoff degraded ({state})"


def fold(con, agent: str) -> str:
    """Fold today's events into the digest - the one model call in the system."""
    ok, msg = foldmod.fold(agent)
    return msg if ok else f"{agent}: fold rejected - {msg}"


# How long to wait for a launched agent to show up in the process table before
# saying it did not. `ocp launch` execs claude, which takes a few seconds to be
# visible; the daemon's own wake waits about this long for the same reason.
STARTED_TIMEOUT_S = 20


def start(con, agent: str, repo: str | None = None, base: str = "main") -> str:
    """Give an agent a worktree and start it there.

    The one thing the daemon cannot do for an agent with no terminal: there is
    nothing to type into, so work queued for it waits forever. Everything else
    on this screen assumes a terminal already exists; this is where one comes
    from.
    """
    row = db.get_agent_state(con, agent)
    if row and row["terminal"]:
        return f"{agent}: already has a terminal ({row['terminal']}) - wake it instead"
    wt = worktree_path(row["worktree"] if row else None)
    if wt:
        # It has somewhere to run already; it lost the window it ran in.
        ok, msg = spawnmod.start_in(agent, wt)
    else:
        ok, msg = spawnmod.spawn(agent, Path(repo or os.getcwd()).resolve(), base=base)
    if not ok:
        return msg
    # A terminal is not a session. `ocp launch` execs claude, and claude can
    # exit on its own - a bad flag, a config it will not accept, an account
    # mid-login - leaving the shell prompt the command was typed at. That looks
    # exactly like a terminal where nothing ran, and saying "started" over it
    # sends the reader looking for an agent that is not there.
    if not daemon.wait_for_session(agent, STARTED_TIMEOUT_S):
        return (f"{msg}\n  but no session is running in it after {STARTED_TIMEOUT_S}s - "
                f"the terminal is there and claude is not. Go and look (t): "
                f"whatever it printed is on that shell.")
    return msg


def worktree_path(recorded: str | None) -> str | None:
    """Where this agent works, out of what its row holds. See `spawn`."""
    return spawnmod.worktree_path(recorded)


def terminal(con, agent: str, repo: str | None = None) -> str:
    """Put the reader in this agent's terminal, starting one if there is none.

    The screen is for the decisions that are one keypress wide. The rest - read
    the diff with it, argue about an approach, watch it work - is a
    conversation, and the place to have one is the agent's own session. Typing
    a paragraph into `m` to avoid going there is how a decision gets made
    badly.

    "It has no terminal" is not an answer to "take me to it": an agent with
    nowhere to run is exactly the one a human is going there to fix, and being
    told to press a different key is a step this can take itself.
    """
    row = db.get_agent_state(con, agent)
    handle = row["terminal"] if row else None
    said = ""
    if not handle:
        said = start(con, agent, repo) + " · "
        row = db.get_agent_state(con, agent)
        handle = row["terminal"] if row else None
        if not handle:
            return said.rstrip(" ·")       # `start` said why
    if not orca.available():
        return said + "orca is not on PATH - nothing to switch to"
    ok, why = orca.focus(handle)
    return said + (f"in {agent}'s terminal - come back to this tab when you are done"
                   if ok else f"could not switch to {agent}: {why}")


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
    if row["response"] != "async":
        # Nobody is waiting on this one, so nobody is told: no push_answer, no
        # wake. Typing "read" at an agent as though it were an answer costs it
        # a turn to discover that it is not one - manager was mid-sentence
        # when a dismissal arrived as "#60 from human: read". resolve() itself
        # stamps announced_at, in the same write as the state change.
        ok, msg = askmod.resolve(rid, None, state="done", notify=False,
                                 dismissed_by=by, dismissed_reason=reason)
        return ok, f"{msg} (dismissed; {row['requester']} is not waiting on it)"
    # An `async` sender parked on a reply and nothing else will ever unpark it,
    # so it is told - and what it is told says it was read, not answered.
    ok, msg = askmod.resolve(rid, None, state="done", dismissed_by=by,
                             dismissed_reason=reason)
    return ok, msg


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


def _git(repo: Path, *args: str):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=15)


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
    try:
        r = _git(repo, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
        if r.returncode != 0:
            return None, "no such commit in this repo"
        full = r.stdout.strip()
        r = _git(repo, "merge-base", "--is-ancestor", full, "main")
    except (OSError, subprocess.SubprocessError) as e:
        return None, str(e)
    if r.returncode == 0:
        return full, ""
    return None, textsafe.oneline((r.stderr or r.stdout).strip())


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
    """One of waiting, doing, done, closed (#71), or discarded (#85). done,
    closed and discarded are written, once each; waiting and doing are never stored - read live from
    whether a request is currently queued against this task or an open child
    of it, the same live read the board already does for "who holds it now",
    so there is nothing here that a write path must remember to keep in
    sync."""
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


def edit_task(con, tid: int, by: str, owner: str | None = None,
              title: str | None = None) -> tuple[bool, str]:
    """Change a task's owner and/or title. Only the task's opener or the
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
    if owner is None and title is None:
        return False, "edit needs a new owner, a new title, or both"
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
    if not changes:
        return False, f"task T{tid} already reads that way - nothing to edit"
    db.edit_task(con, tid, by, owner=new_owner, title=new_title)
    db.add_event(con, by, "edit_task", {"task_id": tid, **changes})
    said = ", ".join(f"{k} {v['old']!r} -> {v['new']!r}" for k, v in changes.items())
    return True, f"task T{tid} edited by {by}: {said}"


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
                "`ocp task new --owner NAME 'title'` makes a task.")
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
    """Reading a report is what closes it: it asked nothing, so nothing is left.

    No verdict is written. A report is not a question, so being read is not an
    answer - and a verdict here would be one the sending agent can read back
    (`ocp read`, its own conversation), making it look as though the human had
    said something they never typed.
    """
    row = db.get_request(con, rid)
    if row and row["fyi"] and row["target"] == HUMAN:
        db.retire_queued(con, rid, "done", None)


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
        asked = time.strftime("%m-%d %H:%M", time.localtime(row["ts"]))
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
    return time.strftime("%m-%d %H:%M", time.localtime(ts or 0))


def first_line(text: str | None) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return "(empty)"


# A reason that resolves itself has to be old before it counts as stuck: a
# closed agent is woken on the next tick, and a long turn is what this system
# is for - the daemon calls an agent healthy up to STUCK_RUNNING_S with nothing
# heard. A reason that does not resolve itself counts at once.
STUCK_AFTER_S = daemon.STUCK_RUNNING_S


def _delivery_block(con, agent: str, sessions: dict | None,
                    handles: set | None, alive: bool = True) -> tuple[str, bool]:
    """Why work is not reaching this agent. (reason, does it resolve itself)

    The checks are the daemon's gates - `supervise`'s, then `deliver_queued`'s -
    in the order the daemon applies them, so the panel gives the daemon's reason
    rather than a state the reader has to interpret. The two
    readings it needs - Orca's terminals and the process table - are taken once
    for the whole panel and handed in: asking per row would cost a process
    listing each, and the idle probe is not asked at all, because a "no" from
    it costs its whole timeout and RUNNING already says it on the agent's row.
    """
    if agent not in paths.list_agents():
        return f"{agent} is not registered - nothing will ever take this", False
    row = db.get_agent_state(con, agent)
    if not row:
        return f"{agent} has never been started (t starts it)", False
    if row["state"] == daemon.STALLED:
        # First, because `supervise` returns on STALLED before delivery is
        # considered at all. Reporting a missing terminal ahead of it sent the
        # reader to start one, which would not have moved the request.
        return f"{agent} is FATAL - the daemon will not touch it (w clears it)", False
    handle = row["terminal"]
    if not handle:
        return f"{agent} has no terminal - nothing can be typed to it (t starts one)", False
    if handles is not None and handle not in handles:
        return f"{agent}'s terminal is gone - Orca no longer has it", False
    try:
        acct = agents.current_account(agent, con)
        pct = status.read(acct).trusted_pct_5h
    except Exception:  # noqa: BLE001
        acct, pct = "?", None
    if row["closed_at"]:
        if pct is not None and pct >= status.PCT_5H_BLOCK:
            return (f"{agent} is closed and {acct} is at {pct}% - it cannot be "
                    f"launched into", False)
        if not alive:
            return f"{agent} is closed and no daemon is running to wake it", False
        return f"{agent} is closed; the next tick wakes it", True
    if sessions is not None and not sessions.get(agent):
        return f"no session is running in {agent}'s terminal", False
    if row["state"] == daemon.RUNNING:
        return f"{agent} is mid-turn - nothing is typed at a working agent", True
    if row["state"] == daemon.HANDOFF:
        return f"{agent} is mid-handoff", True
    walled = daemon.wall_reason(con, agent, row)
    if walled:
        return f"{walled} - delivered when it wakes or moves", True
    return "", True


# What the world looked like, and when we last looked. A frame is drawn every
# two seconds; asking Orca and scanning the process table that often costs a
# fifth of a second each time, for readings that do not change that fast - and
# the daemon is already asking the same two questions on its own tick.
_WORLD: dict = {"at": 0.0, "sessions": None, "handles": None}
WORLD_FRESH_S = 10


def _world(ttl: int = WORLD_FRESH_S) -> tuple[dict | None, set | None]:
    """Orca's terminals and the running sessions, at most `ttl` seconds old.

    Only taken when something on the screen turns on them: a screen with
    nothing queued asks nothing and costs nothing.
    """
    if time.time() - _WORLD["at"] > ttl:
        _WORLD["sessions"] = obs.live_sessions()
        _WORLD["handles"] = orca.live_terminals() if orca.available() else None
        _WORLD["at"] = time.time()
    return _WORLD["sessions"], _WORLD["handles"]


def stuck(con) -> list[dict]:
    """What is not moving, and what is in the way. (what, age_s, why)

    This is the panel that used to draw "in flight". Almost nothing ever is: a
    handoff takes seconds, and everything else it showed was a request that had
    been sitting for a day and a half because its target had no terminal to be
    told with. Drawing those as traffic said the system was busy when it was
    stuck.

    Membership is "the daemon cannot deliver this", not "this is old". Age is a
    column, except for the reasons that clear themselves - being told and not
    having answered yet is the ordinary state of a queue, and it only becomes a
    finding when it has outlasted the longest turn the daemon calls healthy.

    Nothing clears itself while the daemon is down, so with no daemon the
    grace those reasons are given is not given: a request that is one minute
    old and waiting on a tick that will never come is stuck now, not in half
    an hour.
    """
    now = int(time.time())
    alive = bool(daemon.running_elsewhere())
    out: list[dict] = []

    def add(what: str, age: int, why: str, agent: str | None = None) -> None:
        # Every row that is about an agent carries which one, so the keys that
        # fix it can be pressed where the problem is read rather than after
        # finding the same name again in the list above.
        out.append({"what": what, "age": age, "why": why, "agent": agent})

    # First, because it is the reason for most of what follows. With no daemon
    # nothing is delivered, nothing is woken and nothing is parked: every other
    # row here is then a symptom, and "the next tick wakes it" is a sentence
    # about a tick that is not coming.
    if not alive:
        add("the daemon is not running", 0,
            "nothing here moves on its own - `ocp daemon`, or `ocp service install`")

    for h in db.in_flight_handoffs(con):
        left = (h["deadline"] or now) - now
        add(f"handoff {h['agent']} at '{h['step']}'", 0,
            f"deadline in {status.ago(left)}" if left > 0 else "past its deadline",
            h["agent"])

    queued = con.execute(
        "SELECT id, requester, target, ts FROM requests WHERE state = 'queued' "
        "AND target != ? ORDER BY ts", (HUMAN,),
    ).fetchall()
    sessions, handles = _world() if queued else (None, None)
    # An agent ocp has work for and cannot give it, with what is in the way read
    # off its screen: one row for it says so, where a row per request would each
    # give the state's guess ("mid-turn") and none of them the dialog.
    blks = {b["agent"]: b for b in db.blocks(con)}
    # An agent that has been asked to resolve its head request and has not: one
    # row for it says which request and what waits behind it, where the rows
    # below would each say that a request "was told and has not answered" - true
    # of the head, and false of everything queued behind it, which is waiting
    # for the head and not for anyone's attention.
    forgotten: dict[str, dict] = {}
    for st_row in db.all_agent_states(con):
        who = st_row["agent"]
        found = daemon.forgotten_resolve(con, who, st_row, now)
        if found and found["idle_s"] >= daemon.FORGOTTEN_STUCK_S \
                and db.get_cursor(con, f"nudge:{who}") == int(found["head"]["id"]) \
                and (sessions is None or sessions.get(who)):
            forgotten[who] = found
            ids = [f"#{q['id']}" for q in db.queued_for(con, who)[1:4]]
            behind = (f"; {found['behind']} waiting behind it ({', '.join(ids)}"
                      f"{', ...' if found['behind'] > len(ids) else ''})") if found["behind"] else ""
            add(f"{who} idle on #{found['head']['id']}", found["idle_s"],
                f"delivered, nudged, still open and it asked nothing{behind}", who)
    for r in queued:
        if r["target"] in forgotten or r["target"] in blks:
            continue
        age, what = now - int(r["ts"]), f"#{r['id']} {r['requester']} → {r['target']}"
        why, self_clearing = _delivery_block(con, r["target"], sessions, handles, alive)
        if not why:
            # The daemon can deliver it. Either it is about to, or it has
            # already been told and has not answered - which is a queue working
            # until it has gone on too long.
            if age < STUCK_AFTER_S and alive:
                continue
            head = db.head_of_queue(con, r["target"])
            told = db.get_cursor(con, f"notify:{r['target']}") >= int(r["id"])
            if told:
                add(what, age, f"{r['target']} was told and has not answered", r["target"])
            elif head is not None and int(head["id"]) != int(r["id"]):
                # Only the head of a queue is told; the rest wait their turn.
                add(what, age, f"waiting behind #{head['id']}", r["target"])
            else:
                # Nothing structural is in the way and it still has not been
                # told, which the checks above cannot explain: the idle probe
                # never said yes - a permission prompt, a wedged TUI, a RUNNING
                # its hooks never cleared - or the send itself failed. Silence
                # here was the panel saying nothing is wrong for two days.
                add(what, age, f"{r['target']} has not been told yet - if this stays, "
                               f"its idle probe or the send is failing", r["target"])
            continue
        if self_clearing and alive:
            # Already told and now working on it is a queue doing its job,
            # whatever the request's age: age counts from when it was asked,
            # and an agent that has just been handed a two-day-old request is
            # not stuck on it.
            if db.get_cursor(con, f"notify:{r['target']}") >= int(r["id"]):
                continue
            if age < STUCK_AFTER_S:
                continue
        add(what, age, why, r["target"])

    for b in blks.values():
        add(f"{b['agent']} cannot be given its work", now - int(b["since"]),
            f"{blocked.reason(b['kind'], b['detail'])} - waiting: {b['work'] or 'nothing yet'}",
            b["agent"])

    for row in db.all_agent_states(con):
        if row["state"] in (daemon.STALLED, daemon.DEGRADED) \
                and not (row["agent"] in blks and blks[row["agent"]]["kind"] == blocked.STALLED):
            # FATAL, not the internal name STALLED - the agents panel below
            # reads the same row through tui.screen_state and must not disagree.
            word = "FATAL" if row["state"] == daemon.STALLED else row["state"]
            add(f"{row['agent']} is {word}", now - int(row["updated"]),
                row["detail"] or "", row["agent"])
    loads = loadmod.read_all(con)
    for acct in paths.list_accounts():
        ld = loads.get(acct)
        allowed, why = status.read(acct).assignable(ld.active if ld else 0)
        if not allowed:
            add(f"account {acct} takes no work", 0, why)
    return out


# team.yaml as the screen reads it, re-parsed only when the file changes. A
# frame is drawn every two seconds and the routing table is a human-edited file
# that changes a few times a week. Keyed on the file, not just its clock: one
# process can read two homes, and two homes' files are the same age.
_TEAM: dict = {"key": None, "team": None}


def routing() -> "team.Team | None":
    """The routing table, or None when it cannot be read.

    None rather than an exception: the screen draws a team that has no
    hierarchy the same way it drew every team before there was one, and a typo
    in team.yaml must not take the dashboard down with it - `ocp doctor` is
    where a broken routing table is a finding.
    """
    p = paths.team_file()
    try:
        st = p.stat()
        key = (str(p), st.st_mtime_ns, st.st_size)
    except OSError:
        return None
    if _TEAM["key"] != key:
        try:
            _TEAM["team"] = team.Team.load()
        except Exception:  # noqa: BLE001
            _TEAM["team"] = None
        _TEAM["key"] = key
    return _TEAM["team"]


def supervisor_of(t, agent: str) -> str | None:
    """Which agent this one answers to, out of the routing table.

    A supervisor edge is the relation the team model already writes down -
    `manager -> dev.*` is what makes the devs the manager's - so the tree is
    read from the policy the daemon routes on rather than from a second list
    that would drift from it.

    Only an edge whose `from` names one agent counts. A wildcard source is a
    permission, not a chain of command: `* -> reviewer` says anyone may ask the
    reviewer, and the human's own `human -> *` would otherwise make the
    whole team its children and draw a tree that says nothing.
    """
    if t is None:
        return None
    best: tuple[int, str] | None = None
    for e in t.edges:
        if e.get("relation") != "supervisor":
            continue
        src, dst = str(e.get("from", "")), str(e.get("to", ""))
        if "*" in src or src == agent or src == HUMAN:
            continue
        if not t.matches(dst, agent):
            continue
        # The most specific edge wins, the way a route does: a name beats a
        # team, and a team beats everyone.
        rank = 0 if dst == agent else (1 if dst.endswith(".*") else 2)
        if best is None or rank < best[0]:
            best = (rank, src)
    return best[1] if best else None


def agent_tree() -> list[tuple[str, int]]:
    """Every registered agent as (name, depth), supervisors above their reports.

    An agent whose supervisor is not registered, or that is in a cycle, is
    drawn at the root: a name missing from this list reads as an agent that is
    not there, so nothing may drop out of it.
    """
    names = paths.list_agents()
    t = routing()
    boss = {}
    for n in names:
        s = supervisor_of(t, n)
        boss[n] = s if s in names and s != n else None
    # A cycle would recurse forever. Anything that cannot be walked up to a
    # root in as many steps as there are agents is one, and it is drawn flat.
    for n in list(boss):
        seen, cur = {n}, boss[n]
        while cur is not None:
            if cur in seen:
                boss[n] = None
                break
            seen.add(cur)
            cur = boss.get(cur)

    out: list[tuple[str, int]] = []

    def walk(parent: str | None, depth: int) -> None:
        for n in names:
            if boss[n] == parent:
                out.append((n, depth))
                walk(n, depth + 1)

    walk(None, 0)
    return out


def login_argv(acct: str, force: bool = False) -> tuple[str, list[str], dict]:
    """(what happened, argv, env) for handing the terminal to claude.

    Returns an empty argv when there is nothing to run, so the caller shows the
    line and stays where it is.
    """
    action, msg = accounts.login_plan(acct, force=force)
    if action != "login":
        return msg, [], {}
    return (f"logging '{acct}' in - finish in claude, then come back",
            vendors.for_account(acct).login_argv(), accounts.env_for(acct))


def move(con, agent: str, account: str) -> str:
    """Force this agent's live session onto `account`, recording its handoff first."""
    ok, msg = daemon.force_move(con, agent, account, "human (bridge)", lambda _m: None)
    return msg


def can(con, agent: str, key: str) -> tuple[bool, str]:
    """Whether an action is worth offering for this agent, and why not.

    It asks the same rows the action does, so the two cannot drift into
    disagreeing about what is possible - and it says which row said no. A
    refusal that does not explain itself sends the reader to press the key
    again, or to the wrong fix: "does not apply right now" on an agent with no
    terminal is the one screen that could have said `t starts one`.
    """
    row = db.get_agent_state(con, agent)
    if not row:
        if key in ("message", "start", "terminal"):
            return True, ""
        return False, f"{agent} has never been started (t starts it and takes you there)"
    closed = bool(row["closed_at"])
    state = row["state"]
    # The states nothing may be done to from here. RUNNING and HANDOFF are
    # mid-something, and STALLED is the one state the daemon deliberately does
    # not touch - handing a stalled agent off would clear the stall a human was
    # meant to read.
    busy = state in (daemon.RUNNING, daemon.HANDOFF, daemon.STALLED)
    busy_why = f"{agent} is {state}" + (" - only a human clears that (w)"
                                        if state == daemon.STALLED else "")
    if key == "wake":
        if closed or state == daemon.STALLED:
            return True, ""
        if state in (daemon.PARKED, daemon.THROTTLED, daemon.DEGRADED):
            return daemon.park_wakeable(row)
        return False, f"{agent} is {state}, not parked or closed - there is nothing to wake"
    if key == "close":
        # Work waiting is the one case where closing only adds delay: the very
        # next tick wakes it again. `should_close` refuses for the same reason.
        if closed:
            return False, f"{agent} is already closed (w brings it back)"
        if not row["terminal"]:
            return False, f"{agent} has no terminal - there is no session to close (t starts one)"
        if busy:
            return False, busy_why
        waiting = db.queued_for(con, agent)
        if waiting:
            return False, (f"{agent} has {len(waiting)} request(s) waiting - closing it now "
                           f"only delays them, the next tick wakes it again")
        return True, ""
    if key == "park":
        if closed:
            return False, f"{agent} is closed - its context is already gone (w brings it back)"
        if not row["terminal"]:
            return False, f"{agent} has no terminal - there is no session to park (t starts one)"
        if busy:
            return False, busy_why
        return True, ""
    if key == "fold":
        # A fold reads events and runs `claude -p`; it never types into the
        # terminal. An agent whose terminal is gone is one of the likelier ones
        # to want folded.
        return (False, busy_why) if busy else (True, "")
    if key == "move":
        if closed or not row["terminal"]:
            return False, (f"{agent} has no live session to move - it starts on its "
                           f"preferred account")
        if state in (daemon.HANDOFF, daemon.STALLED):
            return False, f"{agent} is {state}"
        return True, ""
    if key == "start":
        if row["terminal"]:
            return False, (f"{agent} already has a terminal ({row['terminal']}) - "
                           f"t goes to it, w wakes it")
        return True, ""
    if key == "terminal":
        # Always. Going to watch an agent work is reasonable whatever it is
        # doing, including mid-turn, and an agent with no terminal gets one on
        # the way - the action does that, so there is nothing to refuse.
        return True, ""
    return True, ""


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
            cls, f"{time.strftime('%H:%M:%S', time.localtime(ts))}  {kind:<18}{payload}")
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
                        f"delivered {time.strftime('%H:%M', time.localtime(r['delivered_at']))}"
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
        goes = {"answers": f" · answers #{rid}", "under": f" · goes under #{rid}",
                "new": " · starts a new thread"}[how]
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
    ('under', id): otherwise a new ask in the thread of the latest row between
    them. ('new', None): nothing between them yet.
    """
    q = con.execute(
        "SELECT MIN(id) AS m FROM requests WHERE requester = ? AND target = ? "
        "AND state = 'queued' AND fyi = 0", (agent, HUMAN)).fetchone()
    if q and q["m"]:
        return "answers", int(q["m"])
    rid = chat_parent(con, agent)
    return ("under", rid) if rid else ("new", None)


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
    row = db.get_request(con, rid)
    if how == "answers" or _is_answer_to_human(row):
        return answer_ok(con, rid, body)
    ok, msg, _ = askmod.ask(agent, body, requester=HUMAN, parent_id=rid)
    return ok, msg


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


def agent_detail(con, agent: str, hint: bool = True) -> list[tuple[str, list[str]]]:
    """What is behind an agent's row, in sections a screen can draw.

    The head, then the conversation the human has with it, then whatever is
    waiting on it or owed to it that is not part of that conversation - a
    request from another agent is not something the human said to it. What it
    knows and what it has done are read on request (`agent_knows`, `agent_done`).

    `hint`: see `conversation_and_drawn`.
    """
    row = db.get_agent_state(con, agent)
    try:
        cfg = agents.read(agent)
        acct = agents.current_account(agent, con)
    except Exception as e:  # noqa: BLE001
        return [("", [f"config error: {e}"])]
    st = status.read_for_agent(agent, acct)
    # Where it is, and - when they differ - where it would rather be. agent.yaml
    # is a preference: a session started while that account was at the wall is
    # on another one, and a reader who only saw the preference would be watching
    # an account the agent is not spending on.
    prefers = agents.account_of(agent)
    where = acct if acct == prefers else f"{acct} (prefers {prefers})"
    head = [Fact(("account", where), ("model", str(cfg.get("model"))),
                 ("effort", cfg.get("effort") or agents.DEFAULT_EFFORT),
                 ("ctx", "?" if st.ctx_pct is None else str(st.ctx_pct) + "%"))]
    if row:
        closed = "  ·closed" if row["closed_at"] else ""
        head.append(Fact(("state", f"{row['state']}{closed}"),
                         *([("wakes", time.strftime("%H:%M", time.localtime(row["wake_at"])))]
                           if row["wake_at"] else [])))
        if row["detail"]:
            head.append(row["detail"])

    # What the conversation drew is not repeated; every other queued request
    # and untold answer is listed under it, including one in a chain too old
    # for the conversation to draw.
    chat, drawn = conversation_and_drawn(con, agent, hint)
    others = [Entry(r["id"], r["requester"], r["body"] or "", r["ts"])
              for r in db.queued_for(con, agent) if r["id"] not in drawn]
    untold = [Entry(r["id"], r["target"], db.shown_verdict(r) or r["state"], r["resolved_at"] or r["ts"])
              for r in db.unannounced_answers(con, agent) if r["id"] not in drawn]
    if others or untold:
        # Where the reader is left, at the end of the conversation.
        chat += [f"below: {len(others)} also queued for it, {len(untold)} "
                 "answers it has not been told - press G to read them"]
    sections = [("", head)]
    n = chat_unread(con, agent)
    sections.append((CHAT_TAB + (f" ({n if n < 100 else '99+'} new)" if n else ""), chat))
    if others:
        sections.append((f"{OTHERS_TAB} ({len(others)})", others))
    if untold:
        sections.append((f"{UNTOLD_TAB} ({len(untold)})", untold))
    return sections


def agent_knows(con, agent: str) -> list[str]:
    """The handoff an agent would be resumed with."""
    kept = db.get_handoff(con, agent)
    return kept.splitlines() if kept else ["no handoff waiting - it has not been parked"]


def agent_done(con, agent: str) -> list:
    """The last events recorded for an agent, newest first."""
    recent = [Event(e["ts"], e["kind"], e["payload"] or "")
              for e in con.execute(
                  "SELECT ts, kind, payload FROM events WHERE agent = ? "
                  "ORDER BY id DESC LIMIT 12", (agent,)).fetchall()]
    return recent or ["nothing recorded yet"]


def agent_where(con, agent: str) -> str:
    """The terminal handle, and the session it would resume if closed - a
    technical id, never on the head where a person reads at a glance, but
    still one keystroke away for the one thing it is for: an agent stuck on
    a screen only the handle can reach."""
    row = db.get_agent_state(con, agent)
    if not row:
        return f"{agent} has never been started"
    said = f"terminal {row['terminal'] or 'none'}"
    if row["closed_at"]:
        said += f"  ·  resumes session {row['resume_session'] or 'unknown'}"
    return said


def agent_worktree(con, agent: str) -> str:
    """Where this agent's repo lives on disk - off the head for the same
    reason as the terminal handle: nothing a person types, only reads when
    they need to go there themselves."""
    row = db.get_agent_state(con, agent)
    if not row or not row["worktree"]:
        return f"{agent} has no worktree"
    return worktree_path(row["worktree"]) or row["worktree"]


def _config_line(acct: str) -> str:
    try:
        return f"{vendors.config_dir(acct)} ({vendors.account_vendor(acct)})"
    except vendors.UnknownVendor as e:
        return str(e)


def account_detail(con, acct: str, logged_in=None) -> list[tuple[str, list[str]]]:
    """What is behind an account's row, in the same shape as an agent's.

    `logged_in` is the caller's login check: the bridge redraws this page every
    couple of seconds and passes its cached one, since the real one asks the
    keychain.
    """
    logged_in = logged_in or accounts.logged_in
    st = status.read(acct)
    loads = loadmod.read_all(con)
    # Ranked for this account's own vendor: the page holds an account, not an
    # agent, and claude's ranking has one thing to say about another vendor's
    # account - that it is not claude's.
    _, ranked = status.pick_account([a for a in paths.list_accounts() if logged_in(a)],
                                    load=loads, vendor=vendors.account_vendor(acct))
    is_in = logged_in(acct)
    allowed, why = status.account_verdict(acct, st, loads.get(acct), ranked, is_in)
    head = [f"login    {'logged in' if is_in else 'NOT logged in'}",
            f"config   {_config_line(acct)}",
            f"5h       {st.trusted_pct_5h}% ({st.pct_5h_source})   7d {st.pct_7d}%",
            f"snapshot {'-' if st.age_s is None else status.ago(st.age_s) + ' old'}"
            f"   session {st.session_id or '-'}",
            f"assign   {'yes' if allowed else 'no'} - {why}"]
    live = status.live_cost(acct)
    head.append("spend    " + ("nothing running to read it from" if live is None else
                               f"${live:.2f} across the sessions running now - not the "
                               f"account's total, which nothing here can see"))
    mine = [a for a in paths.list_agents() if _account_of(a, con) == acct]
    rows = []
    for a in mine:
        row = db.get_agent_state(con, a)
        closed = " ·closed" if row and row["closed_at"] else ""
        rows.append(f"{a:<12}{(row['state'] if row else 'never started')}{closed}"
                    f"   {(row['detail'] if row else '') or ''}")
    return [("", head), (f"AGENTS ON IT ({len(mine)})", rows or ["none"])]


def _account_of(agent: str, con=None) -> str | None:
    """Where this agent's session is, for the list of who is on an account.

    The live account, like every other runtime reading: an agent that moved is
    on the account it moved to, and listing it under the one it left puts it a
    keystroke away from its own page saying otherwise.
    """
    try:
        return agents.current_account(agent, con)
    except Exception:  # noqa: BLE001 - an unreadable agent belongs to no account here
        return None


def eligible_worktrees() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Every registered agent's worktree, split (declared, undeclared).

    A y/N that presses "Yes" to whatever it finds means what it says only
    when it lists exactly what it will press it for - so a worktree outside
    a declared root is never included, only reported as skipped.
    """
    con = db.connect()
    try:
        found = []
        for name in paths.list_agents():
            row = db.get_agent_state(con, name)
            wt = spawnmod.worktree_path(row["worktree"]) if row else None
            if wt:
                found.append((name, wt))
    finally:
        con.close()
    declared = [(a, w) for a, w in found if trust.is_declared(w)]
    undeclared = [(a, w) for a, w in found if not trust.is_declared(w)]
    return declared, undeclared


def trust_worktrees(acct: str, worktrees: list[tuple[str, str]]) -> list[str]:
    """Pre-authorize `acct` for each (agent, worktree), one throwaway session
    at a time, so a real session there never meets the trust screen cold."""
    return [_trust_one_worktree(acct, name, wt) for name, wt in worktrees]


def _trust_one_worktree(acct: str, agent: str, worktree: str) -> str:
    drv = vendors.for_account(acct)
    cmd = (f"cd {shlex.quote(worktree)} && {drv.config_dir_env()}="
           f"{shlex.quote(str(vendors.config_dir(acct)))} {shlex.quote(drv.binary() or drv.name)}")
    handle = orca.create_terminal(cmd, title=f"trust-{acct}-{agent}")
    if not handle:
        return f"{agent} ({worktree}): could not start a session to trust it"
    try:
        # Booting, not mid-turn: `wait_idle`'s probe wants a running session,
        # and this one has none yet, so a bounded poll for the one screen
        # this cares about instead.
        deadline = time.time() + 20
        while time.time() < deadline and not orca.trust_prompt_showing(handle):
            if orca.idle_now(handle):
                break
            time.sleep(1)
        if orca.trust_prompt_showing(handle):
            ok, why = orca.confirm_trust(handle)
            if not ok:
                return f"{agent} ({worktree}): {why}"
            orca.wait_idle(handle)
            result = f"{agent} ({worktree}): trusted"
        else:
            result = f"{agent} ({worktree}): no trust prompt appeared - already trusted?"
        orca.send(handle, "/exit")
        return result
    finally:
        orca.close_terminal(handle)


# ------------------------------------------------- acting without holding a screen
# Everything above returns when it is done, and some of it is not quick: it waits
# for an agent to finish its turn, for a session to appear, for a model. The
# bridge runs a verb in a process of its own (`ocp.actrun`) and says, while it
# runs, what it can be waiting for and how long the longest of those waits is.
# The verbs are the ones a key on the bridge acts with; nothing else can be asked.
ASYNC_VERBS = ("wake", "close", "park", "fold", "unstall", "terminal", "message", "answer",
               "dismiss", "cancel", "move", "chat_reply")

_IDLE_WAIT_S = orca.IDLE_TIMEOUT_MS // 1000 + 30       # wait_idle's own subprocess bound
_DELIVERY_S = daemon.WAKE_SESSION_TIMEOUT_S + _IDLE_WAIT_S + 60

# verb -> (what it can be waiting for, the longest that can take in seconds). The
# longest is the sum of the waits on the path, each bounded by the constant that
# bounds it; it is not a promise about a typical run, it is the point past which
# something is wrong - and the point `ocp.actrun` is stopped, not just flagged
# (jobs.py). Verbs that only write the record say nothing and are given a minute.
WAITS: dict[str, tuple[str, int]] = {
    "wake": ("its session to start and settle", _DELIVERY_S),
    "close": ("it to finish its turn, then its session to end",
              _IDLE_WAIT_S + 30 + daemon.END_WAIT_S + 15),
    "park": ("its handoff to be recorded and its context cleared", _DELIVERY_S),
    "move": ("its handoff, then its session to end and start elsewhere", 2 * _DELIVERY_S),
    "fold": ("the model to fold the day's events", foldmod.FOLD_TIMEOUT_S + 30),
    "terminal": ("a terminal to be made and its session to start",
                 120 + STARTED_TIMEOUT_S + _IDLE_WAIT_S),
    "message": ("its terminal to be idle, then the message to be typed", _DELIVERY_S),
    "chat_reply": ("its terminal to be idle, then the message to be typed", _DELIVERY_S),
    "answer": ("the terminal it is delivered to to be idle", _DELIVERY_S),
    "cancel": ("both ends to be told", _DELIVERY_S),
    "dismiss": ("", 60),
    "unstall": ("", 60),
}


def perform(verb: str, *args, **kwargs) -> tuple[bool, str]:
    """Run one of `ASYNC_VERBS` on a connection of its own. Returns (ok, said).

    Every verb here says what happened in words, and `ok` is True for all but
    `chat_reply`: their refusal is already in the words ("is not yours to
    dismiss"), and `ocp.actrun`'s "ok" turning that into a red "failed" line
    would misdescribe a well-formed answer. `chat_reply` is the one place a
    screen needs the real bool - the box it was typed in has to know whether
    to clear itself or keep what was typed - so it alone crosses through
    `chat_reply_ok` rather than the plain wrapper the others use.
    """
    if verb not in ASYNC_VERBS:
        raise ValueError(f"not an action: {verb!r}")
    con = db.connect()
    try:
        if verb == "chat_reply":
            return chat_reply_ok(con, *args, **kwargs)
        result = globals()[verb](con, *args, **kwargs)
        return True, (result[1] if isinstance(result, tuple) else result)
    finally:
        con.close()

