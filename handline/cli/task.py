"""Tasks and closing work: progress, close, `hl task ...`."""
from __future__ import annotations

import argparse
import collections
import shlex
import sys
import time
from pathlib import Path

from .. import actions
from ..infra import timefmt
from ..team import ask as askmod
from ..daemon import client as dmn_client
from .. import db
from . import client
from ..infra import gitrun, names, paths
from ..infra import textsafe

from .ask import _text_arg, _who_refusal


def cmd_progress(args) -> int:
    """Keep or show the agent's note on its request in progress."""
    agent = askmod.current_agent()
    if not agent:
        print(_who_refusal("write") if names.getenv("OCP_AGENT") else "cannot tell who is writing: OCP_AGENT is unset",
              file=sys.stderr)
        return 1
    if args.note is None:
        try:
            note = client.call("progress.get")["note"]
        except (dmn_client.DaemonDown, dmn_client.RpcError, OSError) as e:
            print(f"could not read the progress note: {e}", file=sys.stderr)
            return 1
        print(textsafe.text(note or "no progress note"))
        return 0
    ok, said = client.write("task.progress", {"note": args.note})
    print(said, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


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
    not closed: it ends when it is done (`hl task done`), and its asker
    reopens it if the result was wrong."""
    raw = args.target
    if raw.isdigit() or actions.TASK_REF.fullmatch(raw):
        print(f"'{raw}' is not an agent. A task has no close: `hl task done {raw}` ends it, "
              f"and `hl task reopen {raw}` undoes a wrong result.")
        return 1
    # The daemon does it: it can read the process table and the terminal, which an agent's own
    # sandbox cannot - closed from here, a live session read as "no session process".
    reply = dmn_client.send({"verb": "close", "agent": raw, "fresh": bool(getattr(args, "fresh", False))})
    if reply is None:
        print("daemon not running, nothing closed")
        return 1
    print(reply.get("said", ""))
    return 0 if reply.get("ok") else 1


def cmd_restart(args) -> int:
    """`cmd_close`'s way - through the daemon - and then the session starts again in the same terminal."""
    raw = args.target
    if raw.isdigit() or actions.TASK_REF.fullmatch(raw):
        print(f"'{raw}' is not an agent.")
        return 1
    reply = dmn_client.send({"verb": "restart", "agent": raw, "fresh": bool(args.fresh),
                             "force": bool(args.force), "by": askmod.asking_agent() or db.HUMAN})
    if reply is None:
        print("daemon not running, nothing restarted")
        return 1
    print(reply.get("said", ""))
    return 0 if reply.get("ok") else 1


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
    # Words are a reason; hex is a sha and must resolve to one, or it is refused - a sha mistyped must not
    # quietly become a reason and skip the ancestor check. --reason forces the words reading. The daemon
    # decides which; a sha is checked against the repo this is typed in unless --repo names another.
    params = {"task": args.task, "repo": str(Path(args.repo) if args.repo else Path.cwd())}
    if args.evidence is not None:
        params["evidence"] = args.evidence
        if args.reason:
            params["reason"] = True
    if args.request is not None:
        params["request"] = args.request
    return client.said(*client.write("task.done", params))


def cmd_task_discard(args) -> int:
    """Throw away a task that is not done - the asker's or the founder's call, with a
    one-line reason."""
    me = askmod.asking_agent()
    if me is None:
        print(_who_refusal("discard a task"))
        return 1
    return client.said(*client.write("task.discard", {"task": args.task, "reason": args.reason}))


def _cmd_task_queue(args, queued: bool) -> int:
    me = askmod.asking_agent()
    if me is None:
        print(_who_refusal("queue a task" if queued else "draft a task"))
        return 1
    failed = 0
    for tid in args.tasks:          # one call each: a refusal of one does not hold back the rest
        ok, msg = client.write("task.queue" if queued else "task.draft", {"tasks": [tid]})
        print(msg)
        failed += not ok
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
    return client.said(*client.write("task.reopen", {"task": args.task, "note": args.reason}))


def cmd_task_release(args) -> int:
    """T286: take a doing task back to queued/draft - the opener's or the
    founder's answer, besides `task edit --timeout`, to a timeout notice."""
    me = askmod.asking_agent()
    if me is None:
        print(_who_refusal("release a task"))
        return 1
    return client.said(*client.write("task.release", {"task": args.task}))


def _task_owner_ok(name: str) -> bool:
    # T292 C3: a task owned by the founder is a decision task, so HUMAN is a
    # valid owner too, not only a registered agent.
    return name == askmod.HUMAN or name in paths.list_agents()


def _new_cmd(args, title: str) -> str:
    """The `task new` line as typed, with -t added: what to paste when only the timeout was missing."""
    secs = args.timeout
    typed = "2h" if secs is None else f"{secs // 60}m" if secs % 60 == 0 else f"{secs}s"
    parts = ["hl task new", "--owner", args.owner, "-t", typed, shlex.quote(title)]
    if args.body not in (None, "-"):
        parts.append(shlex.quote(args.body))
    if args.file:
        parts += ["--file", shlex.quote(args.file)]
    if args.parent is not None:
        parts.append(f"--parent T{args.parent}")
    if args.blocked_by is not None:
        parts.append(f"--blocked-by T{args.blocked_by}")
    if args.priority is not None:
        parts.append(f"--priority P{args.priority}")
    if args.expects is not None:
        parts.append(f"--expects {args.expects}")
    if args.reply:
        parts.append("--reply " + " ".join(shlex.quote(x) for x in args.reply))
    return " ".join(parts)


def cmd_task_new(args) -> int:
    """Open and deliver in one step, through `task.new`; --blocked-by is set in the same transaction,
    so a bad blocker opens nothing."""
    me = askmod.asking_agent()
    if me is None:
        print(_who_refusal("open a task"))
        return 1
    if not _task_owner_ok(args.owner):
        print(f"no agent named {args.owner}")
        return 1
    if args.timeout is None:
        print("a task needs -t/--timeout (90m, 2h; at most "
              f"{timefmt.span(actions.MAX_TASK_TIMEOUT_S)}). Try:\n"
              f"  {_new_cmd(args, args.title)}")
        return 1
    title = textsafe.oneline(args.title).strip()
    if not title:
        print(f"a task needs a title. Try:\n  {_new_cmd(args, '<title>')}")
        return 1
    inline = args.body
    if args.file or inline == "-":
        inline, err = _text_arg(inline, args.file, "body")
        if err:
            print(err)
            return 1
    body = textsafe.text(inline).strip() if inline else None
    params = {"owner": args.owner, "title": title, "time_box": f"{args.timeout}s", "body": body or None,
              "parent": args.parent, "blocked_by": args.blocked_by, "priority": args.priority,
              "expects": args.expects, "reporter": args.reporter}
    ok, msg, got = client.written("task.new", {k: v for k, v in params.items() if v is not None})
    print(msg)
    if not ok:
        return 1
    tid, rc = got["task"], 0
    if args.reply:
        from .ask import _send
        mid, text = args.reply
        rc = _send("say", target=askmod.HUMAN, body=textsafe.text(text.replace("{task}", f"T{tid}")),
                   parent=int(mid), task=tid, no_task=None)
    print(f"T{tid}")        # the number this opened, alone on the last line, for a script to read
    return rc


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
    params = {"title": title, "message": args.chat, "why": args.why}
    if args.priority is not None:
        params["priority"] = args.priority
    if args.expects is not None:
        params["expects"] = args.expects
    return client.said(*client.write("task.begin", params))


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
    ok, msg = client.write("task.handoff", {"task": args.task, "to": args.to, "note": note})
    print(msg)
    behind = gitrun.commits_behind_main(Path.cwd()) if ok else None
    if behind is not None and behind > gitrun.BEHIND_WARN:
        print(f"warning (T935): this branch was forked {behind} commits before main's tip. "
              f"If its diff or merge looks wrong, rebase onto `main` (not `orcapipe`) first.")
    return 0 if ok else 1


def cmd_task_start(args) -> int:
    """T292 bullet 3: pick up T<n> as your current task explicitly, for a
    task delivered while you already held one."""
    me = askmod.asking_agent()
    if me is None:
        print(_who_refusal("start a task"))
        return 1
    return client.said(*client.write("task.start", {"task": args.task}))


def cmd_task_note(args) -> int:
    """The old name of `hl task memo T<n> add TEXT`, kept hidden for one release for the
    prompts of agents already running; it adds the same memo."""
    me = askmod.asking_agent()
    if me is None:
        print(_who_refusal("write a memo"))
        return 1
    return client.said(*client.write("task.memo.add", {"task": args.task, "text": args.text}))


def cmd_task_memo(args) -> int:
    """T493: `ocp task memo T<n> list | add TEXT | edit ID TEXT | delete ID`. Anyone reads and
    adds; the author or the founder edits and deletes. A change on a doing task is told to its
    holder at once."""
    me = askmod.asking_agent()
    if me is None:
        print(_who_refusal("write a memo"))
        return 1
    verb, rest = args.verb, list(args.rest)
    if verb in ("add", "edit") and (args.file or rest[-1:] == ["-"]):
        text, err = _text_arg(rest.pop() if rest[-1:] == ["-"] else None, args.file, "memo text")
        if err:
            print(err)
            return 1
        rest.append(text)
    shape = {"list": 0, "add": 1, "edit": 2, "delete": 1}[verb]
    if len(rest) != shape:
        print({"list": "list takes nothing more", "add": "add takes the memo text",
               "edit": "edit takes the memo id, then the new text",
               "delete": "delete takes the memo id"}[verb])
        return 1
    if verb == "list":
        try:
            memos = client.call("task.get", {"task": args.task})["memos"]
        except dmn_client.RpcError as e:
            print(e)
            return 1
        except (dmn_client.DaemonDown, OSError) as e:
            print(f"could not read T{args.task}: {e}")
            return 1
        for m in memos:
            edited = f", edited {timefmt.stamp(client.epoch(m['edited_at']))}" if m["edited_at"] else ""
            print(f"memo {m['id']}  {m['author']}  {timefmt.stamp(client.epoch(m['at']))}{edited}\n  {m['text']}")
        if not memos:
            print(f"T{args.task} has no memos")
        return 0
    if verb == "add":
        return client.said(*client.write("task.memo.add", {"task": args.task, "text": rest[0]}))
    try:
        mid = int(rest[0])
    except ValueError:
        print(f"{rest[0]!r} is not a memo id - `hl task memo T{args.task} list` shows them")
        return 1
    if verb == "edit":
        return client.said(*client.write("task.memo.edit", {"task": args.task, "memo": mid, "text": rest[1]}))
    return client.said(*client.write("task.memo.delete", {"task": args.task, "memo": mid}))


def cmd_task_edit(args) -> int:
    """Every part named (`--owner`/`--title`, `--blocked-by`, `--unblock`, ...) is one `task.edit`, one
    transaction: a later part's refusal leaves nothing of the earlier ones applied, and only that refusal
    is said. The new owner, the body's readers and an
    unblocked owner are told by the daemon once it has committed."""
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
    params = {"task": args.task, "owner": args.owner, "reporter": args.reporter, "title": args.title,
              "priority": args.priority, "body": body,
              "time_box": None if args.timeout is None else f"{args.timeout}s",
              "blocked_by": args.blocked_by, "unblock": args.unblock}
    if all(v is None for k, v in params.items() if k != "task"):
        print("edit needs a new owner, a new reporter, a new title, --body, --priority, --blocked-by, --unblock, "
              "or --timeout")
        return 1
    return client.said(*client.write("task.edit", {k: v for k, v in params.items() if v is not None}))


def cmd_task_merge(args) -> int:
    """Fold tasks into one: `ocp task merge T9 T10 T11` keeps T9."""
    me = askmod.asking_agent()
    if me is None:
        print(_who_refusal("merge tasks"))
        return 1
    return client.said(*client.write("task.merge", {"into": args.keep, "tasks": args.tasks}))


def _print_board(tasks: list[dict]) -> None:
    """Each task (the wire's `Task`), then the requests linked to it as they stand now: state and date are
    the request's, never held on the task. `state` (#71) is one of draft, queued, doing, done, discarded.
    The Go daemon's Task may leave out edited_by, discarded, mismatch, requests, last_activity and asker, so
    each is optional, as in the Go hl's printBoard."""
    if not tasks:
        print("no open tasks.")
        return
    for t in tasks:
        parent = f" (under T{t['parent']})" if t["parent"] else ""
        edited = f"  edited by {textsafe.oneline(t['edited_by'])}" if t.get("edited_by") else ""
        done = f"  DONE: {textsafe.oneline(t['evidence'])}" if t["evidence"] else ""
        gone = t.get("discarded")
        closed = f"  DISCARDED by {textsafe.oneline(gone['by'])}: {textsafe.oneline(gone['reason'])}" if gone else ""
        waits = f"  {t['block_reason']}" if t["block_reason"] else ""
        active = timefmt.stamp(client.epoch(t.get("last_activity")), timefmt.DATE_TIME)
        print(f"{actions.task_ref(t['id']):<5} {textsafe.oneline(t['title'])}  [{t['to']}] {t['state']}"
              f"{parent}{edited}{done}{closed}  "
              f"{actions.priority_ref(t['priority'])}  may reopen: {t.get('asker', '')}"
              f"{waits}  active {active}")
        if t.get("mismatch"):
            print(f"        ! {t['mismatch']}")
        if t["hold_reason"]:
            print(f"        {t['hold_reason']}")
        for r in t.get("requests", []):
            print(f"        request #{r['id']} {r['from']} → {r['to']} "
                  f"{r['state']} {timefmt.stamp(client.epoch(r['at']), timefmt.DATE_TIME)}")


def _clock(time_box: dict, now: float) -> dict:
    """The wire's `time` as the dict actions.timeout_lines reads."""
    start, end, over = (client.epoch(time_box[k]) for k in ("start", "end", "overdue_since"))
    t = {"length": time_box["length_s"], "default": time_box["default"], "start": start, "end": end,
         "told": time_box["told"], "state": "length", "left": None, "over": None}
    if over is not None:
        t.update(state="over", over=int(now - over))
    elif end is not None:
        t.update(state="left", left=int(end - now))
    return t


def _detail_lines(t: dict, now: float) -> list[str]:
    """One task (the wire's task.get) as lines to read: where it stands, its brief, memos, what it waits on, its
    children and its handoffs, each note whole. The Go daemon's task.get may leave out asker, discarded, blockers,
    subtasks and handoffs."""
    out = [f"T{t['id']}  {t['title']}",
           f"state {t['state']}   owner {t['to']}   opened by {t['from']}"
           + (f"   reporter {t['asker']}" if t.get("asker") not in (None, t["from"]) else "")
           + f"   P{t['priority']}"
           + (f"   parent T{t['parent']}" if t["parent"] else "")
           + f"   created {timefmt.stamp(client.epoch(t['created']))}"]
    if t["time"] is not None:
        out += actions.timeout_lines(_clock(t["time"], now))
    if t["evidence"]:
        out.append(f"done {timefmt.stamp(client.epoch(t['done']))}: {t['evidence']}")
    if t.get("discarded"):
        out.append(f"discarded by {t['discarded']['by']}: {t['discarded']['reason'] or 'no reason given'}")
    out += ["", "brief", t["body"] or "(none)"]
    if t["memos"]:
        out += ["", "memos"]
        out += [f"  memo {m['id']}  {m['author']}  {timefmt.stamp(client.epoch(m['at']))}\n    {m['text']}" for m in t["memos"]]
    if t.get("blockers"):
        out += ["", "waiting on"] + [f"  T{b['task']} ({b['owner']})  {b['title']}" for b in t["blockers"]]
    if t.get("subtasks"):
        out += ["", "child tasks"] + [f"  T{k['id']} ({k['owner']}, {k['state']})  {k['title']}" for k in t["subtasks"]]
    if t.get("handoffs"):
        out += ["", "handoffs"]
        out += [f"  {timefmt.stamp(client.epoch(h['at']))}  {h['from']} -> {h['to']}\n    {h['note']}" for h in t["handoffs"]]
    return out


def cmd_task_show(args) -> int:
    try:
        t = client.call("task.get", {"task": args.task})
    except dmn_client.RpcError as e:
        print(e)
        return 1
    except (dmn_client.DaemonDown, OSError) as e:
        print(f"could not read T{args.task}: {e}")
        return 1
    print(textsafe.text("\n".join(_detail_lines(t, time.time()))))     # titles, briefs and notes are other agents' text
    return 0


def cmd_task_list(args) -> int:
    """The caller's tasks and its reports', or every team's with --everyone, by priority then number."""
    params = {k: v for k, v in (("owner", args.owner), ("state", args.state)) if v is not None}
    params.update(all=bool(args.all), everyone=bool(args.everyone), limit=200)
    rows, gaps = [], None
    try:
        while True:
            page = client.call("tasks.list", {**params, "reason_gaps": gaps is None})
            rows += page["items"]
            gaps = gaps or page.get("reason_gaps")
            if not page["more"]:
                break
            params["before"] = page["items"][-1]["id"]
    except dmn_client.RpcError as e:
        print(e)
        if args.owner is not None and not args.everyone:
            print("`hl task list --everyone` shows every team's tasks")
        return 1
    except (dmn_client.DaemonDown, OSError) as e:
        print(f"could not read the tasks: {e}")
        return 1
    _print_board(sorted(rows, key=lambda t: (t["priority"], t["id"])))
    gaps = collections.defaultdict(int, gaps or {})      # the Go daemon sends none: zeros, as the Go hl prints
    print(f"reason (last 30d): {gaps['retirements_blank']}/{gaps['retirements']} "
          f"retirements carry no reason, {gaps['closures_sha']} closures by sha, "
          f"{gaps['closures_request']} by request, {gaps['closures_reason']} by reason")
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
    try:
        board = client.call("task.board", {"everyone": bool(args.everyone)})
    except (dmn_client.DaemonDown, dmn_client.RpcError, OSError) as e:
        print(f"could not read the board: {e}")
        return 1
    rows = sorted(board["mine"] + board["reports"], key=lambda r: (r["task"]["priority"], r["task"]["id"]))
    if not rows:
        print("no open tasks.")
        return 0
    for r in rows:
        t, now = r["task"], r["now"]
        said = {"waiting": "waiting on " + ", ".join(f"T{n}" for n in now["tasks"]),
                "held": ", ".join(now["agents"]),
                "sub_task": "sub-task: " + ", ".join(now["agents"]),
                "held_as_sub_task": "held as sub-task " + ", ".join(f"T{n} ({a})" for n, a in zip(now["tasks"], now["agents"])),
                "result_in": "you - a result is in"}.get(now["kind"], "nobody holds it")
        parent = f" (under T{t['parent']})" if t["parent"] else ""
        print(_fit100(f"{actions.task_ref(t['id']):<5} {textsafe.oneline(t['title'])}{parent}"))
        print(_fit100(f"      now: {said}"))
        if t["hold_reason"]:
            print(_fit100(f"      {t['hold_reason']}"))
        if t["mismatch"]:
            print(_fit100(f"      ! {t['mismatch']}"))
    return 0
