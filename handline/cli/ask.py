"""Requests between agents: ask, withdraw, cancel, inbox, read, resolve, dismiss, and the digest and handoff listings."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from .. import actions
from ..infra import names, timefmt
from ..team import ask as askmod
from .. import db
from ..infra import textsafe
from ..session import waiting

from ..daemon import delivery as dmn_delivery
from ..infra import daemonclient as dmn_client


def _refused_listing(target: str | None) -> bool:
    """Print why and return True when the caller may not list `target`."""
    caller = askmod.asking_agent()
    if caller is None:
        print(_who_refusal("list") + ", so nothing is listed" if names.getenv("OCP_AGENT") else
              "cannot tell who is asking: OCP_AGENT is unset and this is not an "
              "interactive shell, so nothing is listed.")
        return True
    if not target:
        return False
    why = actions.listing_refusal(caller, target)
    if why:
        print(why)
    return bool(why)


def _text_arg(inline: str | None, file_path: str | None, label: str) -> tuple[str | None, str | None]:
    """(text, error). `inline` or `file_path`, never both; neither reads stdin, and so does `-` as
    either one (T946: the safe way to pass text a shell would expand). A shell expands backticks and $() inside double quotes before this
    process ever sees argv, so a message with either in it is silently
    rewritten - or, at a live terminal, run - by the caller's own shell. A
    file the caller wrote with a quoted heredoc, `Write`, or any tool that
    does not itself go through a shell has no such risk; `--file` exists so
    that is always an option instead of inline text being the only one.
    """
    if file_path and inline is not None:
        return None, f"give {label} inline or with --file, not both"
    if file_path == "-" or inline == "-":
        text = sys.stdin.read()
        return (text, None) if text.strip() else (None, f"{label} is required, and stdin (-) was empty")
    if file_path:
        try:
            return Path(file_path).read_text(), None
        except OSError as e:
            return None, f"could not read {file_path}: {e}"
    if inline is None:
        text = "" if sys.stdin.isatty() else sys.stdin.read()
        if not text.strip():
            return None, f"{label} is required (inline, --file PATH, or on stdin)"
        return text, None
    return inline, None


# A `--reason` given as an empty string is typed, not omitted: taking it as
# "no reason" would silently drop what was typed, on ask, withdraw and dismiss
# alike (#1348 note 1).
_EMPTY_REASON = "--reason was given empty; type one or leave the flag off"


def _send(verb: str, **payload) -> int:
    """T351: the daemon writes the row and tells the target; this only says who
    is asking, sends, and prints what came back. Nothing is written here, and
    with the daemon down nothing is sent - there is no other path."""
    me = askmod.asking_agent()
    if me is None:
        print(_who_refusal(f"{verb}"))
        return 1
    reply = dmn_client.send({"verb": verb, "requester": me, "speaker": askmod.current_agent(),
                             **payload})
    if reply is None:
        print("daemon not running, nothing sent")
        return 1
    print(reply.get("said", ""))
    return 0 if reply.get("ok") else 1


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
              "`hl withdraw`, and answers one it was handed with `hl say <them> --parent <id>`.")
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
        print(_who_refusal("read the inbox") if names.getenv("OCP_AGENT") else
              "cannot tell whose inbox this is: OCP_AGENT is unset and this is not "
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
        return _human_inbox(args.all)
    if args.all:
        rows = askmod.inbox(agent, unread_only=False)
        if not rows:
            print(f"{agent}: inbox empty")
            return 0
        print(f"{len(rows)} total. `hl read <id>` shows one whole.")
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
        # `hl inbox <name>` must not spend the requester's withdraw/replace.
        if agent == askmod.asking_agent():
            db.mark_delivered(con, int(head["id"]))
            db.mark_landed(con, int(head["id"]))       # it has read it
    finally:
        con.close()
    print(f"#{head['id']} from {head['requester']}  depth={head['chain_depth']}")
    print()
    print(textsafe.text(head["body"] or "(empty)"))
    from ..daemon import delivery
    waiting = delivery.more_waiting(head["kind"], more)
    if waiting:
        print(f"\n{waiting}")
    if head["kind"] == "task":
        con = db.connect()
        try:
            closes = delivery.answer_verb(con, head)
        finally:
            con.close()
    else:
        closes = f"`hl say {head['requester']} --parent <id> \"<answer>\"` answers it"
    print(f"\n{closes}. `hl inbox --all` shows the whole queue and its history.")
    return 0


def _human_inbox(show_all: bool = False) -> int:
    """The human's mailbox, which holds one more thing than an agent's.

    An agent is told what came back to it by the daemon typing into its
    terminal. Nobody types into a human, so an answer to something the human
    asked has to be somewhere they look - and this command and `hl` have
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
              f"`hl read <id>` shows one whole.")
        for m in rows:
            mark = "!" if m["gate"] else ("<" if m["answer"] else ".")
            behind = f" (+{m['unread'] - 1})" if m["unread"] > 1 else ""
            print(f"{mark} #{m['id']:<5} {m['from']:<12} {(m['kind'] or 'ask'):<9} "
                  f"{textsafe.oneline(actions.first_line(m['body']))[:60]}{behind}")
        print("`hl say <who> --parent <id> \"<text>\"` answers one - a reply settles what it "
              "answers, and on a `<` row it goes back in that thread.")
        if show_all:
            # Not the ones printed above: an answer that is still on the list is
            # not "already dealt with", and printing it twice would say it was.
            waiting = {m["id"] for m in rows}
            done = con.execute(
                "SELECT id, ts, requester, target, state, body, "
                f"{db.DISMISSED_SQL}, {db.ANSWER_SQL} FROM requests "
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

    The same fail-closed rule `hl say` uses: a human has a terminal, a script
    that dropped OCP_AGENT does not, and promoting one to the other is how an
    agent ends up clearing the founder's mailbox.
    """
    if names.getenv("OCP_AGENT"):
        return (f"refused: this runs under a second Claude Code started inside an agent's session, which "
                f"inherited the name {names.getenv('OCP_AGENT')} without being that agent; only the agent's "
                f"own session can {verb} as it")
    return (f"cannot tell who is {verb}ing this: OCP_AGENT is unset and this is not "
            f"an interactive shell. Agents are started with `hl launch <agent>`, "
            f"which sets it.")


# Every state in which no verdict is coming: the three that never reached the
# target, plus the two that settled one. A wait that does not know them waits
# out its whole timeout for an answer nobody is going to write.
SETTLED = ("landed", *db.NEVER_REACHED_TARGET)

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
    return row is None or row["state"] in SETTLED


def cmd_read(args) -> int:
    """Print one request whole - what was asked, and what came back.

    `hl inbox` shows a line each - one per agent on the human's own, cut to
    fit - and the only way to see the rest was to know where the raw JSON
    lives. An agent that cannot read the whole of what it was asked answers
    the half it could see.

    Any agent can read any request, which was already true of the database and
    of `hl inbox` - this makes it convenient
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
        viewer = askmod.asking_agent()
        if viewer == row["target"] and viewer != actions.HUMAN and not row["edge_ok"]:
            # Not yet checked against the routing table: not the target's to read.
            print(f"no request #{args.id}")
            return 1
        if viewer == actions.HUMAN:
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
        dismissed, by, reason = db.dismissal(row)
        if dismissed:
            print()
            print("--- dismissed" + (f" by {textsafe.oneline(by)}" if by else "") + " ---")
            if reason:
                print(textsafe.text(reason))
        elif db.shown_verdict(row):
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
            print(textsafe.text(db.shown_verdict(row)))
        return 0
    finally:
        con.close()
