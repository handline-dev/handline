"""Requests between agents: ask, withdraw, cancel, inbox, read, resolve, dismiss, and the digest and handoff listings."""
from __future__ import annotations

import sys
from pathlib import Path

from .. import actions
from ..infra import names, timefmt
from ..team import ask as askmod
from .. import db
from ..infra import textsafe

from ..infra import daemonclient as dmn_client
from ..infra.daemonclient import TIMEOUT_S, DaemonDown, RpcError

from . import client


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
    """Take back a request of your own before it is delivered (the daemon's request.withdraw)."""
    if args.reason == "":
        print(_EMPTY_REASON)
        return 1
    if askmod.asking_agent() is None:
        print(_who_refusal("withdraw"))
        return 1
    made, said, got = client.written("request.withdraw", {"id": args.id, **({"reason": args.reason} if args.reason else {})})
    return client.said(made and bool(got.get("ok")), said)


def cmd_cancel(args) -> int:
    """Take a request out of an agent's queue, as the founder (the daemon's request.cancel).

    Human only: an agent that could cancel a request could close the work
    someone else handed it. Says what will happen and who will be told, and
    asks, because it cannot be reopened - the row is closed, never deleted.
    """
    if askmod.asking_agent() != askmod.HUMAN:
        print("cancel is the founder's: an agent takes back its own request with "
              "`hl withdraw`, and answers one it was handed with `hl say <them> --parent <id>`.")
        return 1
    if not args.yes:
        made, why, got = client.written("request.cancel", {"id": args.id, "dry_run": True})
        if not made:
            return client.said(False, why)
        if not got.get("ok"):
            return client.said(False, f"not cancelled - {got.get('plan', '')}")
        print(got.get("plan", ""))
        if not sys.stdin.isatty():
            print("refusing without a terminal to confirm in - pass --yes", file=sys.stderr)
            return 1
        if input("go ahead? [y/N] ").strip().lower() not in ("y", "yes"):
            print("left alone")
            return 1
    made, said, got = client.written("request.cancel", {"id": args.id})
    return client.said(made and bool(got.get("ok")), said)


# How long a --wait waits by default: ten minutes is a while to hold a terminal, and an answer that has not come in
# ten is usually one to go and look at. The daemon holds one call for WAIT_MAX_S at most; a longer wait asks again.
WAIT_DEFAULT_S = 600
WAIT_MAX_S = 600


def cmd_inbox(args) -> int:
    """An inbox as the daemon lists it (inbox.queue): an agent's is the head of its queue, the founder's the mailbox
    folded to a line per agent; `--all` adds the history, or what the founder already dealt with."""
    agent = args.agent or askmod.asking_agent()
    if not agent:
        print(_who_refusal("read the inbox") if names.getenv("OCP_AGENT") else
              "cannot tell whose inbox this is: OCP_AGENT is unset and this is not "
              "an interactive shell. Name the agent.")
        return 1
    if _refused_listing(agent):
        return 1
    params = {"whose": agent, "all": bool(args.all)}
    try:
        got = client.call("inbox.queue", params)
        left, since = (args.timeout, got["newest"]) if args.wait else (0, 0)
        while left > 0:
            hold = min(left, WAIT_MAX_S)
            got = client.call("inbox.queue", {**params, "since": since, "wait_s": hold}, timeout=hold + TIMEOUT_S)
            if got["newest"] > since:
                break
            left -= hold
    except RpcError as e:
        print(e)
        return 1
    except (DaemonDown, OSError) as e:
        print(f"could not read the inbox: {e}", file=sys.stderr)
        return 1
    if args.wait and got["newest"] <= since:
        print(f"nothing after {args.timeout}s: nothing new for {agent}", file=sys.stderr)
        return 0  # nothing new is an outcome, not a failure
    if agent == actions.HUMAN:
        return _human_inbox(got, args.all)
    if args.all:
        rows = got["history"]
        if not rows:
            print(f"{agent}: inbox empty")
            return 0
        print(f"{len(rows)} total. `hl read <id>` shows one whole.")
        for r in rows:
            print(f"#{r['id']:<5} from {r['requester']:<12} {r['state']:<9} "
                  f"depth={r['chain_depth']} {textsafe.oneline(r['line'])}")
        return 0
    head = got["head"]
    if not head:
        print(f"{agent}: inbox empty")
        return 0
    print(f"#{head['id']} from {head['requester']}  depth={head['chain_depth']}")
    print()
    print(textsafe.text(head["body"] or "(empty)"))
    if head["more_waiting_line"]:
        print(f"\n{textsafe.oneline(head['more_waiting_line'])}")
    print(f"\n{textsafe.oneline(head['how_to_close'])}. `hl inbox --all` shows the whole queue and its history.")
    # Only a look at your own queue counts as being told - a foreign `hl inbox <name>` must not spend the
    # requester's withdraw/replace. Sent after the item is out, so a daemon slow to answer does not hold it back.
    if agent == askmod.asking_agent():
        sys.stdout.flush()
        ok, why = client.write("inbox.mark_read", {"ids": [int(head["id"])]})
        if not ok:
            print(f"#{head['id']} is shown but not marked read: {why}", file=sys.stderr)
    return 0


def _human_inbox(got: dict, show_all: bool) -> int:
    """The human's mailbox, which holds one more thing than an agent's: nobody types into a human, so an answer to
    something the human asked is listed here too (`<`). `--all` adds what has already been dealt with."""
    if not got["lines"] and not show_all:
        print("nothing waiting on you")
        return 0
    print(f"{got['waiting']} waiting. `hl read <id>` shows one whole.")
    for m in got["lines"]:
        mark = "!" if m["decision"] else ("<" if m["answer"] else ".")
        behind = f" (+{m['unread'] - 1})" if m["unread"] > 1 else ""
        print(f"{mark} #{m['id']:<5} {m['from']:<12} {m['kind']:<9} {textsafe.oneline(m['line'])}{behind}")
    print("`hl say <who> --parent <id> \"<text>\"` answers one - a reply settles what it "
          "answers, and on a `<` row it goes back in that thread.")
    if show_all:
        print(f"\n{len(got['dealt'])} most recent, already dealt with:")
        for r in got["dealt"]:
            print(f"  #{r['id']:<5} {'→' if r['outgoing'] else '←'} {r['agent']:<12} {r['state']:<9} {textsafe.oneline(r['line'])}")
    return 0


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


def cmd_read(args) -> int:
    """Print one request whole - what was asked, and what came back (the daemon's request.read).

    `hl inbox` shows a line each - one per agent on the human's own, cut to
    fit. An agent that cannot read the whole of what it was asked answers
    the half it could see. Any agent can read any request: nothing typed into
    a request is private to two agents.
    """
    left = args.timeout if args.wait else 0
    while True:
        hold = min(left, WAIT_MAX_S)        # the method holds a call that long at most: a longer wait asks again
        try:
            row = client.call("request.read", {"id": args.id, "wait_s": hold}, timeout=hold + TIMEOUT_S)
        except RpcError as e:
            if e.code == "not_found":
                print(f"no request #{args.id}")
            else:
                print(f"could not read #{args.id}: {e}", file=sys.stderr)
            return 1
        except (DaemonDown, OSError) as e:
            print(f"could not read #{args.id}: {e}", file=sys.stderr)
            return 1
        left -= hold
        if row["state"] in SETTLED or left <= 0:
            break
    if args.wait and row["state"] not in SETTLED:
        print(f"nothing after {args.timeout}s: #{args.id} has no answer yet", file=sys.stderr)
        return 1
    when = timefmt.stamp(client.epoch(row["created"]), timefmt.DATE_TIME)
    print(f"#{row['id']}  {row['requester']} → {row['target']}  {row['kind']}  {row['state']}  {when}")
    parent = row["parent"]
    if parent:
        print(f"in reply to #{parent['id']}" + (f": {textsafe.oneline(parent['line'])}" if parent["line"] else ""))
    print(f"relation {row['relation']}   response {row['response']}   depth {row['chain_depth']}")
    print()
    print(textsafe.text(row["body"] or "(empty)"))
    if row["dismissal"]:
        by, reason = row["dismissal"]["by"], row["dismissal"]["reason"]
        print()
        print("--- dismissed" + (f" by {textsafe.oneline(by)}" if by else "") + " ---")
        if reason:
            print(textsafe.text(reason))
    elif row["answer"]:
        print()
        # A state that never reached the target did not come back as an answer, so it is not headed "answered by".
        print(f"--- {_NEVER_ANSWERED_HEADERS[row['state']]} ---" if row["state"] in _NEVER_ANSWERED_HEADERS
              else f"--- answered by {row['target']} ---")
        print(textsafe.text(row["answer"]))
    return 0
