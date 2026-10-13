"""T351 phase 2: `ask` and `say` as socket verbs. The caller's process writes
nothing; the daemon runs the same checks and the same `team.ask`/`say` the CLI
used to, writes the row, returns its id at once, and then - on a thread of its
own, so the caller never waits for a wake - types the notice.

Who is asking travels in the payload (`requester`, and `speaker`, the caller's
OCP_AGENT): the daemon has no such environment, so the rule that nobody may
send as the human is applied to what the caller says it is - taken at its word,
exactly as OCP_AGENT always was; the socket is private to the user (0600).
"""
from __future__ import annotations

import shlex
import threading

from .. import actions, db
from ..infra import paths
from ..infra.yamlcompat import ConfigError
from ..team import ask as askmod, team

from . import messaging


def _tell(rid: int, target: str, log) -> None:
    """After the reply: a task keeps the one-at-a-time head rule (`ask.push_here`),
    everything else is typed at once (`messaging.tell_now`)."""
    con = db.connect()
    try:
        row = db.get_request(con, rid)
        if row is None or row["state"] != "queued":
            return
        if row["kind"] == "task":
            askmod.push_here(con, target)
        else:
            messaging.tell_now(con, rid, log)
    except Exception as e:  # noqa: BLE001 - the row is written; the tick is the net
        log(f"  telling {target} about #{rid} failed: {e}")
    finally:
        con.close()


# The suite runs the telling inline so a test sees its effect; the daemon never does.
RUN_INLINE = False


def _after(rid: int | None, target: str, log) -> None:
    if rid is None or target == askmod.HUMAN:
        return
    if RUN_INLINE:
        _tell(rid, target, log)
    else:
        threading.Thread(target=_tell, args=(rid, target, log), daemon=True,
                         name=f"ocp-tell-{rid}").start()


def _who(kw) -> tuple[str | None, str | None]:
    """(requester, speaker) as the caller sent them. Never guessed here: the
    daemon's own stdin says nothing about who is typing at the caller."""
    return kw.get("requester") or None, kw.get("speaker")


_NO_WHO = ("cannot tell who is asking: the request named nobody. Agents are started with "
           "`hl launch <agent>`, which sets OCP_AGENT.")


def _is_report_of(me: str, target: str) -> bool:
    """Is `target` one of `me`'s reports, by the supervisor edge in team.yaml?"""
    try:
        return actions.supervisor_of(team.Team.load(), target) == me
    except (ConfigError, OSError):      # an unreadable table refuses nothing here
        return False


def _say_cmd(target: str, body: str, parent: int | None, *flags: str) -> str:
    """A `hl say` line ready to paste: the body as sent, quoted, with the flags that would have worked."""
    parts = ["hl say", target, shlex.quote(body)]
    if parent is not None:
        parts.append(f"--parent {parent}")
    return " ".join(parts + list(flags))


def _working_say(con, me: str, target: str, body: str, parent: int | None, task: int | None) -> str:
    """The line to suggest for a say to `target` the checks below refused: keep a --task the target
    holds; to a report with no task, the task that would reach it; else the plain say."""
    row = db.get_task(con, task) if task is not None else None
    if row is not None and row["owner"] == target:
        return _say_cmd(target, body, parent, f"--task T{task}")
    if parent is None and _is_report_of(me, target):
        return f"hl task new --assign {target} -t 2h '<title>' {shlex.quote(body)}"
    return _say_cmd(target, body, parent)


def say_verb(con, log, **kw) -> tuple[bool, str]:
    """`hl say TARGET BODY`: a message that needs no answer, typed at once. `effect(con, message, request id)`, when given, is
    the daemon's record of the write, made in the transaction that writes the message and what is written with it."""
    effect = kw.get("effect")
    me, speaker = _who(kw)
    if me is None:
        return False, _NO_WHO
    target, task = kw["target"], kw.get("task")
    no_task = kw.get("no_task")
    parent = db.get_request(con, kw["parent"]) if kw.get("parent") is not None else None
    # A reply to the founder's own open request says what it becomes, as `resolve` did (T361).
    founders = (parent is not None and parent["requester"] == actions.HUMAN
                and parent["state"] == "queued" and parent["target"] == me
                and target == actions.HUMAN and parent["kind"] != "task")
    if founders:
        # An answer to the founder's question on a task is no work to name: it joins that task.
        asked_on = [t["id"] for t in db.tasks_for_request(con, parent["id"])] if parent["kind"] == "say" else []
        plain = bool(asked_on) and not task and no_task is None
        defaulted = False
        if not plain and not task and no_task is None:
            # Exactly one open task of the sender's is what the reply is about; with none or
            # several the choice is theirs, and the refusal below names them.
            mine = [t["id"] for t in db.open_tasks(con) if t["owner"] == me]
            if len(mine) == 1:
                task, defaulted = mine[0], True
        refusal = None if plain else actions.check_human_resolve(con, parent, [task] if task else None, no_task,
                                                                 me, one_task=True)
        if refusal:
            mine = [t["id"] for t in db.open_tasks(con) if t["owner"] == me]
            tries = [_say_cmd(target, kw["body"], kw["parent"], f"--task T{t}") for t in mine[:3]]
            tries.append(_say_cmd(target, kw["body"], kw["parent"], "--no-task '<why>'"))
            return False, refusal + "\nTry:\n  " + "\n  ".join(tries)
        def within(c, note, rid):
            if plain and rid is not None:
                for tid in asked_on:
                    db.link_request(c, tid, rid)
            elif db.get_request(c, kw["parent"])["state"] != "queued":     # it settled, not lost a race
                actions.record_human_resolve(c, kw["parent"], [task] if task else None, no_task, me)
            if effect:
                effect(c, note, rid)
        ok, msg, rid = askmod.say(target, kw["body"], requester=me, parent_id=kw["parent"],
                                  speaker=speaker, notify=False, within=within)
        if ok:
            _after(rid, kw["target"], log)
            if defaulted:
                msg += f"\nattached to T{task}, your only open task"
        return ok, msg
    if no_task is not None and target != actions.HUMAN:
        return False, ("--no-task is for replying to the founder (hl say human ...); to anyone else it "
                       "means nothing. Try:\n  "
                       + _working_say(con, me, target, kw["body"], kw.get("parent"), task))
    if task is None and kw.get("parent") is None and _is_report_of(me, target):
        return False, ("work cannot reach a report by message: make it a task (`hl task new`), "
                       "or say --task T<n> to memo one. Try:\n  "
                       f"hl task new --assign {target} -t 2h '<title>' {shlex.quote(kw['body'])}")
    if task is not None:
        # Structural, not read from the text: a say to a report about a task is
        # that task's memo, delivered as one.
        row = db.get_task(con, task)
        if row is None:
            return False, (f"no task T{task}. Try:\n  "
                           + _working_say(con, me, target, kw["body"], kw.get("parent"), None))
        if target == actions.HUMAN and row["opened_by"] == me:
            # A reply to the founder naming the task it became: it is theirs to read, not a memo,
            # and the task is named whoever holds it now.
            def within(c, note, rid):
                db.link_request(c, task, rid)
                if effect:
                    effect(c, note, rid)
            ok, msg, rid = askmod.say(target, kw["body"], requester=me, parent_id=kw.get("parent"),
                                      speaker=speaker, notify=False, within=within)
            if ok and rid is not None:
                _after(rid, kw["target"], log)
            return ok, msg
        if row["owner"] != target:
            return False, (f"T{task} is with {row['owner']}, not {target}. Try:\n  "
                           + _say_cmd(row["owner"], kw["body"], kw.get("parent"), f"--task T{task}"))
        if effect is None:
            ok, msg, _ = actions.memo_add(con, task, me, kw["body"])
            return ok, msg
        con.execute("BEGIN IMMEDIATE")      # the memo and the daemon's record of it are one write; the holder is told after
        try:
            ok, msg, _ = actions.memo_add(con, task, me, kw["body"], tell=False)
            if ok:
                effect(con, msg, None)
            con.execute("COMMIT" if ok else "ROLLBACK")
        except BaseException:
            if con.in_transaction:
                con.execute("ROLLBACK")
            raise
        if ok:
            actions.tell_memo_holder(con, task, me, "added", kw["body"])
        return ok, msg
    ok, msg, rid = askmod.say(target, kw["body"], requester=me, parent_id=kw.get("parent"),
                              speaker=speaker, notify=False, within=effect)
    if ok:
        _after(rid, kw["target"], log)
    return ok, msg


# T303: what a command wrote and then tried to type, from a process that cannot (an agent's sandbox has
# no process table, no Orca and no `ps`). The command writes its row and asks the daemon to type the
# notice; the daemon does it the way the tick would, on a thread so the caller never waits for a wake.
def _background(name: str, fn, log) -> None:
    def run() -> None:
        con = db.connect()
        try:
            fn(con)
        except Exception as e:  # noqa: BLE001 - the row is written; the tick is the net
            log(f"  {name} failed: {e}")
        finally:
            con.close()

    if RUN_INLINE:
        run()
    else:
        threading.Thread(target=run, daemon=True, name=f"ocp-{name}").start()


_HANDED = "handed to the daemon; it is typed as soon as the agent can take it"


def push_check(target: str) -> str | None:
    """Why `target` cannot be pushed to, or None."""
    if target == askmod.HUMAN or target not in paths.list_agents():
        return f"{target} is not an agent"
    return None


def push_act(target: str, rid: int | None, log) -> str:
    """Tell `target` about what was queued for it: request `rid` when one is named (a message is typed at once, a task keeps the
    one-at-a-time rule), else its next task."""
    if rid is not None:
        _after(int(rid), target, log)
    else:
        _background(f"push-{target}", lambda c: askmod.push_here(c, target), log)
    return _HANDED


def push_verb(con, log, **kw) -> tuple[bool, str]:
    """Tell `target` about what was queued for it: request `rid` when the caller names one
    (a message is typed at once, a task keeps the one-at-a-time rule), else its next task."""
    why = push_check(kw["target"])
    return (False, why) if why else (True, push_act(kw["target"], kw.get("rid"), log))


def push_answer_check(requester: str) -> str | None:
    if requester == askmod.HUMAN or requester not in paths.list_agents():
        return f"{requester} is not an agent"
    return None


def push_answer_act(requester: str, log) -> str:
    _background(f"push-answer-{requester}", lambda c: askmod.push_answer_here(c, requester), log)
    return _HANDED


def push_answer_verb(con, log, **kw) -> tuple[bool, str]:
    """Put an answer in front of the agent that asked (`ask.push_answer_here`)."""
    why = push_answer_check(kw["requester"])
    return (False, why) if why else (True, push_answer_act(kw["requester"], log))


def notify_cancelled_check(con, rid: int):
    """The request `notify_cancelled_verb` would tell about and why not: (row, None) or (None, why). Pure reads."""
    row = db.get_request(con, rid)
    if row is None:
        return None, f"no request #{rid}"
    if row["target"] == askmod.HUMAN or row["target"] not in paths.list_agents():
        return None, f"{row['target']} is not an agent"
    if row["state"] != "landed" or not row["delivered_at"] or not db.closed_under(con, row["id"]):
        return None, f"#{row['id']} was not cancelled after it was delivered"
    return row, None


def notify_cancelled_act(row, log) -> str:
    from . import delivery
    _background(f"notify-cancelled-{row['id']}", lambda c: delivery.notify_cancelled(c, row, str(db.shown_verdict(row) or "")), log)
    return _HANDED


def notify_cancelled_verb(con, log, **kw) -> tuple[bool, str]:
    """Tell an agent a request it was handed was closed under it (`delivery.notify_cancelled`).

    Only a delivered request already closed as rejected, and only with the verdict
    on its row: what the daemon types is never the caller's words."""
    row, why = notify_cancelled_check(con, int(kw["rid"]))
    return (False, why) if why else (True, notify_cancelled_act(row, log))
