"""Tasks and closing work: progress, close, `ocp task ...`."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .. import actions
from ..infra import timefmt
from ..team import agents
from ..team import ask as askmod
from ..daemon import lifecycle as dmn_lifecycle, taskholds
from .. import db
from ..session import handoff as handoffmod
from ..infra import paths
from ..usage import status
from ..infra import textsafe

from .ask import _text_arg, _who_refusal


def cmd_progress(args) -> int:
    """Keep or show the agent's note on its request in progress."""
    agent = askmod.current_agent()
    if not agent:
        print("cannot tell who is writing: OCP_AGENT is unset", file=sys.stderr)
        return 1
    con = db.connect()
    try:
        if args.note is None:
            print(textsafe.text(handoffmod.get_progress(con, agent) or "no progress note"))
            return 0
        ok, said = handoffmod.set_progress(con, agent, args.note)
        if ok:
            print(f"progress on {said} kept; the handoff and the wake prompt carry it")
        else:
            print(said, file=sys.stderr)
        return 0 if ok else 1
    finally:
        con.close()


def _ambiguous_number(raw: str) -> str:
    return actions.parse_task_ref_error(raw)


def _task_arg(text: str) -> int:
    """argparse `type` for a task: T79 only. A bare number is refused with
    both of its readings, so a request id typed here cannot act on a task."""
    try:
        return actions.parse_task_ref(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


def _priority_arg(text: str) -> int:
    """argparse `type` for a priority: P1, P2 or P3."""
    try:
        return actions.parse_priority(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


def _expects_arg(text: str) -> str:
    """argparse `type` for --expects: sha, verdict, decision or report (T292)."""
    try:
        return actions.parse_expects(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


def _timeout_arg(text: str) -> int:
    """argparse `type` for a task timeout (T286): `90m`, `2h` - at most
    actions.MAX_TASK_TIMEOUT_S."""
    try:
        return actions.parse_task_timeout(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


def cmd_close(args) -> int:
    """End an idle agent's session by its name, keeping its terminal. A task is
    not closed: it ends when it is done (`ocp task done`), and its asker
    reopens it if the result was wrong."""
    raw = args.target
    if raw.isdigit() or actions.TASK_REF.fullmatch(raw):
        print(f"'{raw}' is not an agent. A task has no close: `ocp task done {raw}` ends it, "
              f"and `ocp task reopen {raw}` undoes a wrong result.")
        return 1
    args.agent = raw
    con = db.connect()
    try:
        st = status.read_for_agent(args.agent, agents.current_account(args.agent, con))
        ok, msg = dmn_lifecycle.close_agent(con, args.agent, print, st)
        print(msg)
    finally:
        con.close()
    return 0 if ok else 1


def cmd_task_done(args) -> int:
    """Mark a task done (#71) - the builder's own evidence, not the end of
    it. Exactly one of a sha, --request N, or a reason; `actions.mark_task_done`
    is where both that and who counts as the builder are actually enforced,
    so the parsing here only shapes whatever was given into the right
    keyword."""
    me = askmod.asking_agent()
    if me is None:
        print(_who_refusal("mark a task done"))
        return 1
    con = db.connect()
    try:
        if db.get_task(con, args.task) is None:
            print(f"no task T{args.task}")
            return 1
        sha, reason = None, None
        if args.evidence is not None:
            # Words are a reason; hex is a sha and must resolve to one, or it
            # is refused - a sha mistyped must not quietly become a reason
            # and skip the ancestor check. --reason forces the words reading.
            sha = None if args.reason or not actions.looks_like_sha(args.evidence) else args.evidence
            reason = None if sha else args.evidence
        ok, msg = actions.mark_task_done(con, args.task, me, sha=sha, request_id=args.request,
                                         reason=reason,
                                         repo=Path(args.repo) if args.repo else None)
        print(msg)
    finally:
        con.close()
    return 0 if ok else 1


def cmd_task_discard(args) -> int:
    """Throw away a task that is not done - the asker's or the founder's call, with a
    one-line reason."""
    me = askmod.asking_agent()
    if me is None:
        print(_who_refusal("discard a task"))
        return 1
    con = db.connect()
    try:
        ok, msg = actions.discard_task(con, args.task, me, args.reason)
        print(msg)
    finally:
        con.close()
    return 0 if ok else 1


def _cmd_task_queue(args, queued: bool) -> int:
    me = askmod.asking_agent()
    if me is None:
        print(_who_refusal("queue a task" if queued else "draft a task"))
        return 1
    con = db.connect()
    try:
        failed = 0
        for tid in args.tasks:
            ok, msg = actions.set_queued(con, tid, me, queued)
            print(msg)
            failed += not ok
    finally:
        con.close()
    return 1 if failed else 0


def cmd_task_queue(args) -> int:
    """Approve draft tasks to run - the founder or the owner's manager."""
    return _cmd_task_queue(args, True)


def cmd_task_draft(args) -> int:
    """Send queued tasks back to draft - the founder or the owner's manager."""
    return _cmd_task_queue(args, False)


def cmd_task_reopen(args) -> int:
    """Send a done task back to doing (#71) - the asker's or the founder's call, needing
    a one-line reason."""
    me = askmod.asking_agent()
    if me is None:
        print(_who_refusal("reopen a task"))
        return 1
    con = db.connect()
    try:
        if db.get_task(con, args.task) is None:
            print(f"no task T{args.task}")
            return 1
        ok, msg = actions.reopen_task(con, args.task, me, args.reason)
        print(msg)
    finally:
        con.close()
    return 0 if ok else 1


def cmd_task_release(args) -> int:
    """T286: take a doing task back to queued/draft - the opener's or the
    founder's answer, besides `task edit --timeout`, to a timeout notice."""
    me = askmod.asking_agent()
    if me is None:
        print(_who_refusal("release a task"))
        return 1
    con = db.connect()
    try:
        if db.get_task(con, args.task) is None:
            print(f"no task T{args.task}")
            return 1
        ok, msg = actions.release_task(con, args.task, me)
        print(msg)
    finally:
        con.close()
    return 0 if ok else 1


def _task_owner_ok(name: str) -> bool:
    # T292 C3: a task owned by the founder is a decision task, so HUMAN is a
    # valid owner too, not only a registered agent.
    return name == askmod.HUMAN or name in paths.list_agents()


def cmd_task_new(args) -> int:
    """T292: open and deliver in one step (`actions.task_new`, C2 routing-
    checked). --blocked-by runs after, on its own connection, so its
    refusal leaves the task opened and delivered, not rolled back."""
    me = askmod.asking_agent()
    if me is None:
        print(_who_refusal("open a task"))
        return 1
    if me == askmod.HUMAN:
        print("a task is opened by an agent, never in the founder's name")
        return 1
    if not _task_owner_ok(args.owner):
        print(f"no agent named {args.owner}")
        return 1
    if args.timeout is None:
        print("a task needs -t/--timeout (90m, 2h; at most "
              f"{timefmt.span(actions.MAX_TASK_TIMEOUT_S)})")
        return 1
    priority = args.priority if args.priority is not None else db.DEFAULT_PRIORITY
    title = textsafe.oneline(args.title).strip()
    if not title:
        print("a task needs a title")
        return 1
    body = textsafe.text(args.body).strip() if args.body else None
    if args.blocked_by is not None:
        # Precheck (auditor's #2065 SHOULD-FIX C): a bad blocker must refuse
        # before anything is opened, since there is no shared transaction to
        # roll back afterward.
        con = db.connect()
        try:
            why = actions._blocker_refusal(con, args.blocked_by)
        finally:
            con.close()
        if why:
            print(why)
            return 1
    ok, msg, tid = actions.task_new(me, args.owner, title, body, parent=args.parent,
                                    expects=args.expects, priority=priority, timeout_s=args.timeout)
    if not ok:
        print(msg)
        return 1
    if args.blocked_by is not None:
        con = db.connect()
        try:
            bok, bmsg = actions.add_block(con, tid, args.blocked_by, me)
        finally:
            con.close()
        print(f"{msg}\n{bmsg}")
        return 0 if bok else 1
    print(msg)
    return 0


def cmd_task_begin(args) -> int:
    """T297: open a task for yourself, and start it, on a founder message."""
    me = askmod.asking_agent()
    if me is None:
        print(_who_refusal("begin a task"))
        return 1
    title = textsafe.oneline(args.title).strip()
    if not title:
        print("a task needs a title")
        return 1
    con = db.connect()
    try:
        ok, msg, _ = actions.task_begin(con, me, title, args.chat, args.why,
                                        priority=args.priority, expects=args.expects)
        print(msg)
    finally:
        con.close()
    return 0 if ok else 1


def cmd_task_handoff(args) -> int:
    """T377: pass T<n> to another agent with a note that travels with it."""
    me = askmod.asking_agent()
    if me is None:
        print(_who_refusal("hand a task over"))
        return 1
    note, err = _text_arg(args.note, args.file, "note")
    if err:
        print(err)
        return 1
    con = db.connect()
    try:
        ok, msg = actions.task_handoff(con, me, args.task, args.to, note)
        print(msg)
    finally:
        con.close()
    return 0 if ok else 1


def cmd_task_start(args) -> int:
    """T292 bullet 3: pick up T<n> as your current task explicitly, for a
    task delivered while you already held one."""
    me = askmod.asking_agent()
    if me is None:
        print(_who_refusal("start a task"))
        return 1
    con = db.connect()
    try:
        ok, msg = actions.task_start(con, me, args.task)
        print(msg)
    finally:
        con.close()
    return 0 if ok else 1


def cmd_task_note(args) -> int:
    """T292/#1655 (1a): a mid-task correction to whoever holds T<n>, not new
    work - `task new --parent` is that. Delivered as an ordinary task-kind
    ask until T293's `say` gives it its own, lighter path."""
    me = askmod.asking_agent()
    if me is None:
        print(_who_refusal("send a task note"))
        return 1
    con = db.connect()
    try:
        ok, msg = actions.task_note(con, me, args.task, args.text)
        print(msg)
    finally:
        con.close()
    return 0 if ok else 1


def cmd_task_memo(args) -> int:
    """T493: `ocp task memo T<n> list | add TEXT | edit ID TEXT | delete ID`. Anyone reads and
    adds; the author or the founder edits and deletes. A change on a doing task is told to its
    holder at once."""
    me = askmod.asking_agent()
    if me is None:
        print(_who_refusal("write a memo"))
        return 1
    verb, rest = args.verb, args.rest
    shape = {"list": 0, "add": 1, "edit": 2, "delete": 1}[verb]
    if len(rest) != shape:
        print({"list": "list takes nothing more", "add": "add takes the memo text",
               "edit": "edit takes the memo id, then the new text",
               "delete": "delete takes the memo id"}[verb])
        return 1
    con = db.connect()
    try:
        if db.get_task(con, args.task) is None:
            print(f"no task T{args.task}")
            return 1
        if verb == "list":
            memos = actions.memos_of(con, args.task)
            for m in memos:
                edited = f", edited {timefmt.stamp(m['edited_at'])}" if m["edited_at"] else ""
                print(f"memo {m['id']}  {m['author']}  {timefmt.stamp(m['created_at'])}{edited}\n  {m['body']}")
            if not memos:
                print(f"T{args.task} has no memos")
            return 0
        if verb == "add":
            ok, msg, _ = actions.memo_add(con, args.task, me, rest[0])
        else:
            try:
                mid = int(rest[0])
            except ValueError:
                print(f"{rest[0]!r} is not a memo id - `ocp task memo T{args.task} list` shows them")
                return 1
            memo = db.get_memo(con, mid)
            if memo is None or memo["task_id"] != args.task:
                print(f"T{args.task} has no memo {mid}")
                return 1
            ok, msg = (actions.memo_edit(con, mid, me, rest[1]) if verb == "edit"
                       else actions.memo_delete(con, mid, me))
        print(msg)
        return 0 if ok else 1
    finally:
        con.close()


def cmd_task_link(args) -> int:
    con = db.connect()
    try:
        if db.get_task(con, args.task) is None:
            print(f"no task T{args.task}")
            return 1
        if db.get_request(con, args.request) is None:
            print(f"no request #{args.request}")
            return 1
        db.link_request(con, args.task, args.request)
    finally:
        con.close()
    print(f"request #{args.request} linked to task T{args.task}")
    return 0


def cmd_task_edit(args) -> int:
    """Every part named (`--owner`/`--title`, `--blocked-by`, `--unblock`) is
    one transaction, so a later part's refusal never leaves an earlier one
    applied (auditor's #2042 SHOULD-FIX 5: `--owner X --blocked-by <cycle>`
    used to rename the task and then refuse, exit 1 reading as nothing
    happened). Stops at the first refusal rather than trying the rest.

    Messages are held and printed only once COMMIT or ROLLBACK has actually
    happened (auditor's #2065 SHOULD-FIX B) - printing a part's message as
    soon as it runs said a change had taken before the transaction as a
    whole was known to hold; a later refusal then rolled it back while the
    line claiming it stayed on the screen.
    """
    me = askmod.asking_agent()
    if me is None:
        print(_who_refusal("edit a task"))
        return 1
    body = None
    if args.body is not None or args.body_file:
        body, err = _text_arg(args.body, args.body_file, "body")
        if err:
            print(err)
            return 1
    if (args.owner is None and args.title is None and args.priority is None and body is None
            and args.blocked_by is None and args.unblock is None and args.timeout is None):
        print("edit needs a new owner, a new title, --body, --priority, --blocked-by, --unblock, "
              "or --timeout")
        return 1
    con = db.connect()
    try:
        con.execute("BEGIN IMMEDIATE")
        ok = True
        msgs: list[str] = []
        unblocked_owner = None
        was_owner = (db.get_task(con, args.task) or {"owner": None})["owner"]
        try:
            if (args.owner is not None or args.title is not None or args.priority is not None
                    or args.timeout is not None or body is not None):
                ok, msg = actions.edit_task(con, args.task, me, owner=args.owner,
                                            title=args.title, priority=args.priority,
                                            timeout_s=args.timeout, body=body)
                msgs.append(msg)
            if ok and args.blocked_by is not None:
                ok, msg = actions.add_block(con, args.task, args.blocked_by, me)
                msgs.append(msg)
            if ok and args.unblock is not None:
                ok, msg = actions.unblock(con, args.task, args.unblock, me)
                msgs.append(msg)
                if ok and not db.task_blockers(con, args.task):
                    unblocked_owner = db.get_task(con, args.task)["owner"]
        except Exception:
            con.execute("ROLLBACK")
            raise
        con.execute("COMMIT" if ok else "ROLLBACK")
    finally:
        con.close()
    # A refusal's own message is the last one appended (every part before it
    # took, or there was only the one that failed) - the only one still true
    # after a ROLLBACK, so only it is shown.
    for msg in (msgs if ok else msgs[-1:]):
        print(msg)
    if ok and body is not None:
        con = db.connect()
        try:
            actions.tell_body_edited(con, args.task, me)
        finally:
            con.close()
    if ok and args.owner is not None:
        # T545: the task goes to its new owner now, not at some later tick - and not inside the transaction.
        con = db.connect()
        try:
            actions.send_moved_task(con, args.task, was_owner, me)
        finally:
            con.close()
    if unblocked_owner is not None:
        # T325: clearing the last blocker is the task's turn to be
        # reconsidered, same as a blocker closing - once this transaction has
        # committed, never inside it (`advance_current_task` writes through
        # its own connection).
        con = db.connect()
        try:
            if actions.advance_current_task(con, unblocked_owner):
                askmod.push(con, unblocked_owner)
        finally:
            con.close()
    return 0 if ok else 1


def cmd_task_merge(args) -> int:
    """Fold tasks into one: `ocp task merge T9 T10 T11` keeps T9."""
    me = askmod.asking_agent()
    if me is None:
        print(_who_refusal("merge tasks"))
        return 1
    con = db.connect()
    try:
        ok, msg = actions.merge_tasks(con, args.keep, args.tasks, me)
    finally:
        con.close()
    print(msg)
    return 0 if ok else 1


def _print_board(con, tasks: list) -> None:
    """Each task, then the requests linked to it as they stand now: state and
    date are read from the request, never held on the task. `state` (#71) is
    one of draft, queued, doing, done, discarded - doing reads live
    off those same requests, the rest off the task's own record."""
    if not tasks:
        print("no open tasks.")
        return
    open_requests = con.execute("SELECT * FROM requests WHERE state = 'queued'").fetchall()
    for t in tasks:
        parent = f" (under T{t['parent_task']})" if t["parent_task"] else ""
        edited = f"  edited by {textsafe.oneline(t['edited_by'])}" if t["edited_by"] else ""
        state = actions.task_state(con, t)
        done = f"  DONE: {textsafe.oneline(t['done_evidence'])}" if t["done_evidence"] else ""
        closed = (f"  DISCARDED by {textsafe.oneline(t['discarded_by'])}: "
                  f"{textsafe.oneline(t['discard_reason'])}" if t["discarded_by"]
                  else "")
        blockers = [b["id"] for b in db.task_blockers(con, t["id"])]
        waits = f"  blocked by {', '.join(f'T{b}' for b in blockers)}" if blockers else ""
        active = timefmt.stamp(db.task_last_activity(con, t["id"]), timefmt.DATE_TIME)
        print(f"{actions.task_ref(t['id']):<5} {textsafe.oneline(t['title'])}  [{t['owner']}] {state}"
              f"{parent}{edited}{done}{closed}  "
              f"{actions.priority_ref(t['priority'])}  may reopen: {actions.task_asker(con, t)}"
              f"{waits}  active {active}")
        odd = actions.task_mismatch(con, t, state, open_requests)
        if odd:
            print(f"        ! {odd}")
        wait = taskholds.wait_line(con, t, state)
        if wait:
            print(f"        {wait}")
        for r in db.task_requests(con, t["id"]):
            when = timefmt.stamp(r["ts"], timefmt.DATE_TIME)
            print(f"        request #{r['id']} {r['requester']} → {r['target']} "
                  f"{r['state']} {when}")


def _visible_owners(args) -> set[str] | None:
    """Whose tasks this call shows: the caller and its reports, or everyone for
    the founder and for `--everyone` (T256)."""
    return None if args.everyone else actions.reach(askmod.asking_agent())


def cmd_task_list(args) -> int:
    owners = _visible_owners(args)
    if args.owner is not None and owners is not None and args.owner not in owners:
        print(f"{args.owner} is not you or one of your reports: --everyone shows every team's tasks")
        return 1
    con = db.connect()
    try:
        rows = actions.task_rows(con, owner=args.owner, state=args.state,
                                 include_ended=args.all, owners=owners)
        _print_board(con, [r["row"] for r in rows])
        gaps = actions.reason_gaps(con)
        print(f"reason (last 30d): {gaps['retirements_blank']}/{gaps['retirements']} "
              f"retirements carry no reason, {gaps['closures_sha']} closures by sha, "
              f"{gaps['closures_request']} by request, {gaps['closures_reason']} by reason")
    finally:
        con.close()
    return 0


def _fit100(line: str) -> str:
    """Plain text, one screen wide: what the room reading it over is stuck
    with."""
    return line if len(line) <= 100 else line[:99] + "…"


def cmd_task_board(args) -> int:
    """The asking agent's own open tasks, and who holds each one now.

    Ports `status-sync.sh`'s logic (a script that polled a status file)
    rather than rederiving it: a task's own linked requests, read live,
    never a status copied onto the task row. `held` is who a still-queued
    request is with. Absent that, an open child task's own queued request
    means the work moved down a level. Absent that too, the last linked
    request having come back `done` from someone other than me or the
    founder means the result is in and it is my turn to read it. Nothing
    linked, or the last exchange already closed some other way (withdrawn,
    rejected, replaced), is nobody holding it - the same as never asked.
    A hold is an open child task sharing a request with
    its parent, so the parent reads `held as sub-task T<n>` while it lasts -
    below a blocker, which is the older and firmer wait. A child opened any
    other way is no hold and changes nothing here.
    Waiting on an open blocker (task #21/#1884) reads as `waiting on T<n>`
    instead of either of those two - it is neither nobody's turn nor mine
    while the thing it waits on is still open.

    """
    me = askmod.asking_agent()
    if me is None:
        print(_who_refusal("board"))
        return 1
    con = db.connect()
    try:
        # The founder's board is the tasks it asked for, as before; the full view is `list`.
        owners = {me} if me == askmod.HUMAN and not args.everyone else _visible_owners(args)
        tasks = [t for t in db.open_tasks(con) if owners is None or t["owner"] in owners]
        if not tasks:
            print("no open tasks.")
            return 0
        open_requests = con.execute("SELECT * FROM requests WHERE state = 'queued'").fetchall()
        for t in tasks:
            reqs = db.task_requests(con, t["id"])
            held = sorted({r["target"] for r in reqs if actions._asks(r)})
            kids = sorted({r["target"] for c in db.open_child_tasks(con, t["id"])
                          for r in db.task_requests(con, c["id"]) if actions._asks(r)})
            last = reqs[-1] if reqs else None
            back = (last is not None and last["state"] == "done"
                    and last["target"] not in (me, askmod.HUMAN))
            blockers = db.task_blockers(con, t["id"])
            linked_rids = {r["id"] for r in reqs}
            holds = [c for c in db.open_child_tasks(con, t["id"])
                     if linked_rids & {r["id"] for r in db.task_requests(con, c["id"])}]
            now = ("waiting on " + ", ".join(f"T{b['id']}" for b in blockers) if blockers
                  else ", ".join(held) if held
                  else "sub-task: " + ", ".join(kids) if kids
                  else "held as sub-task " + ", ".join(
                      f"T{c['id']} ({c['owner']})" for c in holds) if holds
                  else "you - a result is in" if back
                  else "nobody holds it")
            parent = f" (under T{t['parent_task']})" if t["parent_task"] else ""
            print(_fit100(f"{actions.task_ref(t['id']):<5} {textsafe.oneline(t['title'])}{parent}"))
            print(_fit100(f"      now: {now}"))
            wait = taskholds.wait_line(con, t, actions.task_state(con, t))
            if wait:
                print(_fit100(f"      {wait}"))
            odd = actions.task_mismatch(con, t, actions.task_state(con, t), open_requests)
            if odd:
                print(_fit100(f"      ! {odd}"))
    finally:
        con.close()
    return 0
