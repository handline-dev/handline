"""Requests between agents: ask, withdraw, cancel, inbox, read, resolve, dismiss, and the digest and handoff listings."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from .. import actions
from ..infra import timefmt
from ..team import ask as askmod
from .. import db
from ..session import fold as foldmod
from ..team import hr
from ..infra import paths
from ..infra import textsafe
from ..session import waiting

from ..daemon import delivery as dmn_delivery
from .bridge import _bridge_textual_module


def _refused_listing(target: str | None) -> bool:
    """Print why and return True when the caller may not list `target`."""
    caller = askmod.asking_agent()
    if caller is None:
        print("cannot tell who is asking: OCP_AGENT is unset and this is not an "
              "interactive shell, so nothing is listed.")
        return True
    if not target:
        return False
    why = actions.listing_refusal(caller, target)
    if why:
        print(why)
    return bool(why)


def cmd_digest(args) -> int:
    if _refused_listing(args.agent):
        return 1
    p = foldmod.journal_path(args.agent, args.date or foldmod.today())
    if not p.exists():
        print(f"no digest at {p}")
        return 1
    print(textsafe.text(p.read_text()))
    return 0


def cmd_handoff(args) -> int:
    if _refused_listing(args.agent):
        return 1
    con = db.connect()
    try:
        text = db.get_handoff(con, args.agent)
    finally:
        con.close()
    if not text:
        print(f"no handoff waiting for {args.agent}")
        return 1
    print(textsafe.text(text))
    return 0


def _text_arg(inline: str | None, file_path: str | None, label: str) -> tuple[str | None, str | None]:
    """(text, error). One of `inline` or `file_path` - never neither, never
    both. A shell expands backticks and $() inside double quotes before this
    process ever sees argv, so a message with either in it is silently
    rewritten - or, at a live terminal, run - by the caller's own shell. A
    file the caller wrote with a quoted heredoc, `Write`, or any tool that
    does not itself go through a shell has no such risk; `--file` exists so
    that is always an option instead of inline text being the only one.
    """
    if file_path and inline is not None:
        return None, f"give {label} inline or with --file, not both"
    if file_path:
        try:
            return Path(file_path).read_text(), None
        except OSError as e:
            return None, f"could not read {file_path}: {e}"
    if inline is None:
        # A quoted heredoc on stdin is as safe as a file. A terminal has
        # nothing to read, so it falls through to the error.
        text = "" if sys.stdin.isatty() else sys.stdin.read()
        if not text.strip():
            return None, f"{label} is required (inline, --file PATH, or on stdin)"
        return text, None
    return inline, None


# A `--reason` given as an empty string is typed, not omitted: taking it as
# "no reason" would silently drop what was typed, on ask, withdraw and dismiss
# alike (#1348 note 1).
_EMPTY_REASON = "--reason was given empty; type one or leave the flag off"


def cmd_ask(args) -> int:
    body, err = _text_arg(args.body, args.file, "body")
    if err:
        print(err)
        return 1
    if args.reason == "":
        print(_EMPTY_REASON)
        return 1
    if args.reason and args.replaces is None:
        print("--reason says why a request was replaced: it needs --replaces ID")
        return 1
    if args.no_task == "":
        print("--no-task says why this names a task without belonging to it: give the reason")
        return 1
    if args.task is not None and args.no_task:
        print("--task and --no-task contradict each other: use one")
        return 1
    named = actions.task_ids_named(body)
    if args.task is None and not args.no_task and named:
        print(f"this text names {', '.join(f'T{n}' for n in named)}: add --task T<n> so the "
              "request is linked and the task shows it is being worked, or "
              "--no-task 'reason' if it does not belong to one")
        return 1
    if args.task is not None:
        con = db.connect()
        try:
            row = db.get_task(con, args.task)
            state = actions.task_state(con, row) if row else ""
        finally:
            con.close()
        if row is None:
            print(f"no task T{args.task}")
            return 1
        if state in ("done", "closed", "discarded", "merged") and not args.fyi:
            again = (f"`ocp task reopen T{args.task} <why>` first" if state == "done"
                     else "it cannot be worked on again; open a new task")
            print(f"task T{args.task} is {state}: a request linked to it would not reopen it - "
                  f"{again}. A report (--fyi) may still be linked.")
            return 1
    ok, msg, rid = askmod.ask(args.target, body, files=args.files,
                              parent_id=args.parent,
                              mergeable=args.mergeable, first=args.first,
                              replaces=args.replaces, fyi=args.fyi, reason=args.reason,
                              timebox_s=args.timebox)
    # ask() already phrases its own refusals; do not prefix them again.
    print(msg)
    if ok and args.task is not None and rid is not None:
        con = db.connect()
        try:
            db.link_request(con, args.task, rid)
        finally:
            con.close()
        print(f"linked to T{args.task}, which now reads {_task_now(args.task)}")
    return 0 if ok else 1


def _task_now(tid: int) -> str:
    con = db.connect()
    try:
        return actions.task_state(con, db.get_task(con, tid))
    finally:
        con.close()


def cmd_withdraw(args) -> int:
    if args.reason == "":
        print(_EMPTY_REASON)
        return 1
    if askmod.asking_agent() is None:
        print(_who_refusal("withdraw"))
        return 1
    ok, msg = askmod.withdraw(args.id, reason=args.reason)
    print(msg)
    return 0 if ok else 1


def cmd_cancel(args) -> int:
    """Take a request out of an agent's queue, as the founder.

    Human only: an agent that could cancel a request could close the work
    someone else handed it. Says what will happen and who will be told, and
    asks, because it cannot be reopened - the row is closed, never deleted.
    """
    if askmod.asking_agent() != askmod.HUMAN:
        print("cancel is the founder's: an agent takes back its own request with "
              "`ocp withdraw`, and answers one it was handed with `ocp resolve`.")
        return 1
    con = db.connect()
    try:
        ok, plan = actions.cancel_plan(con, args.id)
        if not ok:
            print(f"not cancelled - {plan}")
            return 1
        if not args.yes:
            print(plan)
            if not sys.stdin.isatty():
                print("refusing without a terminal to confirm in - pass --yes", file=sys.stderr)
                return 1
            if input("go ahead? [y/N] ").strip().lower() not in ("y", "yes"):
                print("left alone")
                return 1
        print(actions.cancel(con, args.id))
    finally:
        con.close()
    return 0


# A blocking wait polls; these are how often, and for how long by default. Ten
# minutes is a while to hold a terminal, and an answer that has not come in ten
# is usually one to go and look at.
WAIT_POLL_S = 3
WAIT_DEFAULT_S = 600


def _wait_for(check, timeout: int, what: str) -> bool:
    """Block until `check(con)` is true, or the timeout runs out. True if it came.

    Polling, not a notification: what this waits on is another process writing
    a row, and sqlite has nowhere to hang a listener. One connection serves the
    whole wait - `db.connect` runs the schema and its migrations on every call.
    """
    deadline = time.time() + timeout
    con = db.connect()
    try:
        while True:
            if check(con):
                return True
            if time.time() >= deadline:
                print(f"nothing after {timeout}s: {what}", file=sys.stderr)
                return False
            time.sleep(WAIT_POLL_S)
    finally:
        con.close()


def cmd_inbox(args) -> int:
    agent = args.agent or askmod.asking_agent()
    if not agent:
        print("cannot tell whose inbox this is: OCP_AGENT is unset and this is not "
              "an interactive shell. Name the agent.")
        return 1
    if _refused_listing(agent):
        return 1
    if args.wait:
        con = db.connect()
        try:
            seen = _waiting_ids(con, agent)
        finally:
            con.close()
        if not _wait_for(lambda c: _waiting_ids(c, agent) - seen, args.timeout,
                         f"nothing new for {agent}"):
            return 0  # nothing new is an outcome, not a failure

    if agent == actions.HUMAN:
        # The human's mailbox is a place you read *and answer* things, and that
        # is the screen: two places to do it is two places to keep in step, and
        # the one you are not looking at is the one holding the escalation. A
        # pipe still gets text - a script asking for the list is not a human
        # asking for somewhere to sit. It opens on the same screen `cmd_bridge`
        # does.
        if sys.stdout.isatty() and not args.all and not args.wait:
            bridge_textual = _bridge_textual_module()
            if bridge_textual:
                return bridge_textual.run()
        return _human_inbox(args.all)
    if args.all:
        rows = askmod.inbox(agent, unread_only=False)
        if not rows:
            print(f"{agent}: inbox empty")
            return 0
        print(f"{len(rows)} total. `ocp read <id>` shows one whole.")
        for r in rows:
            print(f"#{r['id']:<5} from {r['requester']:<12} {r['state']:<9} "
                  f"depth={r['chain_depth']} {textsafe.oneline(r['body'])[:60]}")
        return 0

    con = db.connect()
    try:
        head = db.head_of_queue(con, agent)
        if not head:
            print(f"{agent}: inbox empty")
            return 0
        more = db.queue_depth(con, agent) - 1
        # Only a look at your own queue counts as being told - a foreign
        # `ocp inbox <name>` must not spend the requester's withdraw/replace.
        if agent == askmod.asking_agent():
            db.mark_delivered(con, int(head["id"]))
    finally:
        con.close()
    print(f"#{head['id']} from {head['requester']}  depth={head['chain_depth']}")
    print()
    print(textsafe.text(head["body"] or "(empty)"))
    if more:
        print(f"\n{more} more waiting - the next comes when you resolve or dismiss this one.")
    closes = ("`ocp dismiss <id>` closes it, it asks nothing" if head["kind"] == "report"
              else "`ocp resolve <id> \"<verdict>\"` answers it")
    print(f"\n{closes}. `ocp inbox --all` shows the whole queue and its history.")
    return 0


def _human_inbox(show_all: bool = False) -> int:
    """The human's mailbox, which holds one more thing than an agent's.

    An agent is told what came back to it by the daemon typing into its
    terminal. Nobody types into a human, so an answer to something the human
    asked has to be somewhere they look - and this command and `ocp bridge` have
    to hold the same rows, or clearing one leaves the other still asking.

    `--all` adds what has already been dealt with, the way it does everywhere
    else. It used to fall through to the agent view, which has no answers in
    it, so asking for more showed less.
    """
    con = db.connect()
    try:
        rows = actions.messages(con)
        if not rows and not show_all:
            print("nothing waiting on you")
            return 0
        # A count of messages, not of lines: read the same unfolded rows a
        # fold draws from, not the fold's own line count.
        print(f"{len(actions.mailbox_rows(con))} waiting. "
              f"`ocp read <id>` shows one whole.")
        for m in rows:
            mark = "!" if m["gate"] else ("<" if m["answer"] else ".")
            behind = f" (+{m['unread'] - 1})" if m["unread"] > 1 else ""
            print(f"{mark} #{m['id']:<5} {m['from']:<12} {(m['kind'] or 'ask'):<9} "
                  f"{textsafe.oneline(actions.first_line(m['body']))[:60]}{behind}")
        print("`ocp resolve <id> \"<text>\"` answers one - on a `<` row that goes back "
              "as a reply in its thread; `ocp dismiss <id>` takes it off the list.")
        if show_all:
            # Not the ones printed above: an answer that is still on the list is
            # not "already dealt with", and printing it twice would say it was.
            waiting = {m["id"] for m in rows}
            done = con.execute(
                "SELECT id, ts, requester, target, state, body, verdict, "
                "dismissed_by, dismissed_reason FROM requests "
                "WHERE (target = ? OR requester = ?) AND state != 'queued' "
                "ORDER BY id DESC LIMIT 20", (actions.HUMAN, actions.HUMAN)).fetchall()
            done = [r for r in done if r["id"] not in waiting]
            print(f"\n{len(done)} most recent, already dealt with:")
            for r in done:
                other = r["target"] if r["requester"] == actions.HUMAN else r["requester"]
                arrow = "→" if r["requester"] == actions.HUMAN else "←"
                print(f"  #{r['id']:<5} {arrow} {other:<12} {r['state']:<9} "
                      f"{textsafe.oneline(actions.first_line(db.shown_verdict(r) or r['body']))[:60]}")
        return 0
    finally:
        con.close()


def _who_refusal(verb: str) -> str:
    """Whose row it is cannot be checked against a caller we cannot name.

    The same fail-closed rule `ocp ask` uses: a human has a terminal, a script
    that dropped OCP_AGENT does not, and promoting one to the other is how an
    agent ends up clearing the founder's mailbox.
    """
    return (f"cannot tell who is {verb}ing this: OCP_AGENT is unset and this is not "
            f"an interactive shell. Agents are started with `ocp launch <agent>`, "
            f"which sets it.")


def cmd_dismiss(args) -> int:
    """Take one of your own rows off your list.

    The two kinds there need two different writes - a request addressed to you
    is closed with "read", an answer you asked for is only marked told - and
    which one this is is read off the row, so clearing your list never means
    knowing which. Somebody else's row is neither: dismissing it would resolve
    a request they are still waiting on, with a verdict they never wrote.
    """
    if args.reason == "":
        print(_EMPTY_REASON)
        return 1
    con = db.connect()
    try:
        row = db.get_request(con, args.id)
        if row is None:
            print(f"no request #{args.id}")
            return 1
        me = askmod.asking_agent()
        if me is None:
            print(_who_refusal("dismiss"))
            return 1
        mine = row["target"] == me or (row["requester"] == me and row["state"] != "queued")
        if not mine and row["requester"] == me:
            take_back = "cancel" if me == askmod.HUMAN else "withdraw"
            print(f"#{args.id} is your own request, not one to dismiss. "
                  f"`ocp {take_back} {args.id}` takes it back.")
            return 1
        if not mine:
            print(f"#{args.id} is {row['requester']} → {row['target']}, not yours to "
                  f"dismiss. `ocp read {args.id}` shows it.")
            return 1
        ok, msg = actions.dismiss(con, args.id, me=me, reason=args.reason,
                                  tasks=args.task, no_task=args.no_task)
        print(msg)
        return 0 if ok else 1
    finally:
        con.close()


# Every state in which no verdict is coming: the three that never reached the
# target, plus the two that settled one. A wait that does not know them waits
# out its whole timeout for an answer nobody is going to write.
SETTLED = ("done", "rejected", *db.NEVER_REACHED_TARGET)

# cmd_read's header for a row in one of db.NEVER_REACHED_TARGET's states - a
# module-level name, not an inline literal, so a test can check every name
# that constant holds has a header here (auditor's #1565 NOTE 1: two
# hardcoded lists that happened to agree is the same gap raised on textsafe
# at #1566 - a fourth state added to NEVER_REACHED_TARGET without a header
# here silently reads as "answered by {target}" again).
_NEVER_ANSWERED_HEADERS = {"denied": "refused", "withdrawn": "withdrawn",
                           "replaced": "replaced"}


def _waiting_ids(con, agent: str) -> set:
    """The request ids currently on this agent's list, human or not.

    The human's own list is read unfolded (`mailbox_rows`, not `messages`):
    a wait is for a new row landing, and a fold can leave the id set
    unchanged - a new row ties the folded representative's time and loses the
    tiebreak - although something real just arrived.
    """
    if agent == actions.HUMAN:
        return {r["id"] for r in actions.mailbox_rows(con)}
    return {r["id"] for r in askmod.inbox(agent, unread_only=False)
            if r["state"] not in SETTLED}


def _answered(con, request_id: int) -> bool:
    """Is there anything more to wait for on this row?

    A row that does not exist is answered in the only sense that matters here:
    waiting ten minutes for a typo'd id helps nobody, and the caller says so.
    Delivery is not an answer - it is a column, not a state, and it only means
    the target was told the row exists.
    """
    row = db.get_request(con, request_id)
    return row is None or bool(row["verdict"]) or row["state"] in SETTLED


def cmd_read(args) -> int:
    """Print one request whole - what was asked, and what came back.

    `ocp inbox` shows a line each - one per agent on the human's own, cut to
    fit - and the only way to see the rest was to know where the raw JSON
    lives. An agent that cannot read the whole of what it was asked answers
    the half it could see.

    Any agent can read any request, which was already true of the database and
    of `ocp inbox`, `ocp digest` and `ocp handoff` - this makes it convenient
    rather than new. Nothing typed into a request is private to two agents.
    """
    if args.wait and not _wait_for(
            lambda c: _answered(c, args.id), args.timeout,
            f"#{args.id} has no answer yet"):
        return 1
    con = db.connect()
    try:
        row = db.get_request(con, args.id)
        if row is None:
            print(f"no request #{args.id}")
            return 1
        if askmod.asking_agent() == actions.HUMAN:
            actions.opened_by_human(con, args.id)
        when = timefmt.stamp(row["ts"], timefmt.DATE_TIME)
        print(f"#{row['id']}  {row['requester']} → {row['target']}  "
              f"{row['kind'] or 'ask'}  {row['state']}  {when}")
        reply = dmn_delivery.in_reply_to(con, row)
        if reply:
            print(reply)
        print(f"relation {row['relation']}   response {row['response']}   "
              f"depth {row['chain_depth']}")
        print()
        print(textsafe.text(row["body"] or "(empty)"))
        if row["kind"] == "say" and row["state"] == "queued":
            print()
            print(actions.delivery_line(row["id"]))
        dismissed, by, reason = db.dismissal(row)
        if dismissed:
            print()
            print("--- dismissed" + (f" by {textsafe.oneline(by)}" if by else "") + " ---")
            if reason:
                print(textsafe.text(reason))
        elif row["verdict"]:
            print()
            # db.NEVER_REACHED_TARGET (denied, withdrawn, replaced) never
            # reached the target, so none of them came back as an answer -
            # "answered by {target}" over a withdrawn row's own label was
            # wrong the same way for all three, not just the one state that
            # already had its own header (auditor's #1565, the residual left
            # from #1348: the label carries the correction, the header did
            # not).
            print(f"--- {_NEVER_ANSWERED_HEADERS[row['state']]} ---"
                  if row["state"] in _NEVER_ANSWERED_HEADERS
                  else f"--- answered by {row['target']} ---")
            print(textsafe.text(row["verdict"]))
        return 0
    finally:
        con.close()


def _resolve_tasks(args, owner, me):
    """What `resolve` opens in the same transaction as the close, or None."""
    if not (args.finding or args.hold):
        return None

    def opener(con):
        tids = []
        if args.finding:
            tids += actions.open_finding_tasks(con, args.id, args.finding, owner)
        if args.hold:
            tids += actions.open_hold_task(con, args.id, args.hold, me, args.on or [])
        return tids
    return opener


def cmd_resolve(args) -> int:
    """Answer a request - whichever end of it you are on.

    Resolving writes the verdict on the row, so aimed at a request you made
    and someone has already answered it overwrites their answer with your own
    words and sends nothing: the record then says the agent said what you said.
    The screen's answer key has always branched on that; this is the same
    branch, because the two must not disagree about what "answer" means.
    """
    verdict, err = _text_arg(args.verdict, args.file, "verdict")
    if err:
        print(err)
        return 1
    con = db.connect()
    try:
        row = db.get_request(con, args.id)
        if row is None:
            print(f"no request #{args.id}")
            return 1
        me = askmod.asking_agent()
        if me is None:
            print(_who_refusal("answer"))
            return 1
        if row["requester"] == me and row["state"] != "queued":
            if args.finding:
                print(f"#{args.id} is your own request - a finding is opened by "
                      f"whoever answers it, not by you")
                return 1
            print(actions.answer(con, args.id, verdict))
            return 0
        if row["target"] != me:
            if row["requester"] == me:
                take_back = "cancel" if me == askmod.HUMAN else "withdraw"
                print(f"#{args.id} is your own request, not one to answer. "
                      f"`ocp {take_back} {args.id}` takes it back.")
                return 1
            print(f"#{args.id} is {row['requester']} → {row['target']}, not yours to "
                  f"answer. `ocp read {args.id}` shows it.")
            return 1
        if row["state"] != "queued":
            # Closed already - answered, or cancelled by the founder while this
            # was being worked on. A second verdict would overwrite the first
            # and be sent to the requester as though it were the answer.
            print(f"#{args.id} is already {row['state']}"
                  + (f" ({textsafe.oneline(db.shown_verdict(row))[:120]})"
                     if db.shown_verdict(row) else "")
                  + " - nothing to resolve.")
            return 1
        refusal = actions.check_human_resolve(con, row, args.task, args.no_task, me)
        if refusal:
            print(refusal)
            return 1
        refusal = actions.check_open_proposal(args.id, row, verdict)
        if refusal:
            print(refusal)
            return 1
        if args.finding and row["requester"] not in paths.list_agents():
            print(f"#{args.id} came from {row['requester']}, not an agent - a finding "
                  f"cannot be tied to it; open it with `ocp task new --owner NAME`")
            return 1
        if args.on and not args.hold:
            print("--on names what a --hold waits on; give --hold TITLE too")
            return 1
        if args.hold and not textsafe.oneline(args.hold).strip():
            print("a hold needs a title")
            return 1
        if args.hold and args.state != "done":
            print("a hold is an answer - it cannot go with --state rejected")
            return 1
        if args.hold and args.on:
            refusal = actions.hold_refusal(con, args.id, args.on)
            if refusal:
                print(refusal)
                return 1
        owner = actions.finding_owner(me) if args.finding else None
        if args.finding and owner is None:
            print(f"no registered manager for {me}'s lane - a finding cannot be owned; "
                  f"open it with `ocp task new --owner NAME`")
            return 1
        if args.finding and any(not textsafe.oneline(f).strip() for f in args.finding):
            print("a finding needs a title")
            return 1
        if args.retires is not None:
            old = db.get_request(con, args.retires)
            if old is None:
                print(f"no request #{args.retires} to retire")
                return 1
            # #1565 BLOCKING: this pre-check tested existence, moot_by and
            # state but never whose row it is - so any agent could retire any
            # queued request in the system, including the founder's, with
            # db.retire_queued stamping announced_at as though it had already
            # been reported and check_human_resolve never running (it guards
            # the row being resolved, not the one being retired). `old` is
            # mine to retire the same way `--replaces` treats its own old
            # row as the requester's: here it is the row I was asked and am
            # now saying another answer already covers, so ownership is
            # target, not requester - matched against `me`, already
            # established above as the target of the row this resolve is
            # answering. A founder row narrows further: even a legitimate
            # target must still say what it becomes, same as answering one
            # directly (db.retire_queued has this new caller now; #1350's
            # enumeration did not, because this verb did not exist yet).
            if old["target"] != me and me != askmod.HUMAN:
                print(f"#{args.retires} is {old['requester']} -> {old['target']}, "
                     f"not yours to retire")
                return 1
            if old["requester"] == askmod.HUMAN:
                refusal = actions.check_human_resolve(con, old, args.task, args.no_task, me)
                if refusal:
                    print(refusal)
                    return 1
            if old["state"] == "queued" and hr.is_open_proposal(args.retires):
                # #1908 should-fix: --retires closes `old` too, the same
                # `answer`/`resolve` can - a proposal escalation retired this
                # way is answered with "replaced by #N", not a decision.
                print(f"#{args.retires} is a roster proposal - answer `approve` or "
                      f"`reject`, it cannot be retired")
                return 1
            if old["moot_by"] is not None:
                print(f"#{args.retires} is already moot (by #{old['moot_by']})")
                return 1
            if old["state"] != "queued":
                # Caught here, not left for db.retire_queued's WHERE to fail
                # on: that failure is worded for a race ("left 'queued'
                # before it could be retired"), which is the wrong story for
                # a row that was never queued to begin with - already
                # answered, its own way, with no moot_by set (a real answer,
                # not a retirement).
                print(f"#{args.retires} is already {old['state']} - nothing to retire")
                return 1
    finally:
        con.close()
    if args.task or args.no_task:
        con = db.connect()
        try:
            actions.record_human_resolve(con, args.id, args.task, args.no_task, me)
        finally:
            con.close()
    ok, msg = askmod.resolve(
        args.id, verdict, state=args.state,
        open_tasks=_resolve_tasks(args, owner, me))
    if ok and args.retires is not None:
        con = db.connect()
        try:
            # Actually take the named row out of its queue - moot_by said
            # "retiring #N" while leaving it queued, live and about to be
            # worked on, which is worse than not recording anything (#1366
            # BLOCKING). db.retire_queued is the same close the human
            # mailbox and the fyi supersede loop already use for a row a
            # newer one has superseded, delivered or not; its own WHERE
            # state='queued' is the atomic check against a race with
            # whatever else might close it between here and the check above.
            retired = db.retire_queued(con, args.retires, "replaced",
                                       f"replaced by #{args.id}")
            if retired:
                # set_moot_by's own CAS return is not read here (#1366 N1,
                # unchanged by this pass - the ownership guard above narrows
                # the race window but does not close it, and it stays a
                # note, not a fix, per orcapipe's #1572: "leave the second
                # and say you left it").
                db.set_moot_by(con, args.retires, args.id)
                msg += f", retiring #{args.retires}"
            else:
                msg += (f" (#{args.retires} was not retired - it is still queued)"
                        if db.is_still_queued(con, args.retires) else
                        f" (#{args.retires} left 'queued' before it could be retired)")
        finally:
            con.close()
    print(msg)
    return 0 if ok else 1
