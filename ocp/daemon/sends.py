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
           "`ocp launch <agent>`, which sets OCP_AGENT.")


def _is_report_of(me: str, target: str) -> bool:
    """Is `target` one of `me`'s reports, by the supervisor edge in team.yaml?"""
    try:
        return actions.supervisor_of(team.Team.load(), target) == me
    except (ConfigError, OSError):      # an unreadable table refuses nothing here
        return False


def say_verb(con, log, **kw) -> tuple[bool, str]:
    """`ocp say TARGET BODY`: a message that needs no answer, typed at once."""
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
        refusal = actions.check_human_resolve(con, parent, [task] if task else None, no_task, me,
                                              one_task=True)
        if refusal:
            return False, refusal
        ok, msg, rid = askmod.say(target, kw["body"], requester=me, parent_id=kw["parent"],
                                  speaker=speaker, notify=False)
        if ok:
            if db.get_request(con, kw["parent"])["state"] != "queued":     # it settled, not lost a race
                actions.record_human_resolve(con, kw["parent"], [task] if task else None, no_task, me)
            _after(rid, kw["target"], log)
        return ok, msg
    if no_task is not None:
        return False, ("--no-task is for replying to the founder's request (--parent <its id>): "
                       "say what it becomes there")
    if task is None and kw.get("parent") is None and _is_report_of(me, target):
        return False, ("work cannot reach a report by message: make it a task (`ocp task new`), "
                       "or say --task T<n> to memo one")
    if task is not None:
        # Structural, not read from the text: a say to a report about a task is
        # that task's memo, delivered as one.
        row = db.get_task(con, task)
        if row is None:
            return False, f"no task T{task}"
        if row["owner"] != target:
            return False, f"T{task} is with {row['owner']}, not {target}"
        ok, msg, _ = actions.memo_add(con, task, me, kw["body"])
        return ok, msg
    ok, msg, rid = askmod.say(target, kw["body"], requester=me, parent_id=kw.get("parent"),
                              speaker=speaker, notify=False)
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


def push_verb(con, log, **kw) -> tuple[bool, str]:
    """Tell `target` about what was queued for it: request `rid` when the caller names one
    (a message is typed at once, a task keeps the one-at-a-time rule), else its next task."""
    target = kw["target"]
    if target == askmod.HUMAN or target not in paths.list_agents():
        return False, f"{target} is not an agent"
    rid = kw.get("rid")
    if rid is not None:
        _after(int(rid), target, log)
    else:
        _background(f"push-{target}", lambda c: askmod.push_here(c, target), log)
    return True, _HANDED


def push_answer_verb(con, log, **kw) -> tuple[bool, str]:
    """Put an answer in front of the agent that asked (`ask.push_answer_here`)."""
    requester = kw["requester"]
    if requester == askmod.HUMAN or requester not in paths.list_agents():
        return False, f"{requester} is not an agent"
    _background(f"push-answer-{requester}", lambda c: askmod.push_answer_here(c, requester), log)
    return True, _HANDED


def notify_cancelled_verb(con, log, **kw) -> tuple[bool, str]:
    """Tell an agent a request it was handed was closed under it (`delivery.notify_cancelled`).

    Only a delivered request already closed as rejected, and only with the verdict
    on its row: what the daemon types is never the caller's words."""
    from . import delivery
    row = db.get_request(con, int(kw["rid"]))
    if row is None:
        return False, f"no request #{kw['rid']}"
    if row["target"] == askmod.HUMAN or row["target"] not in paths.list_agents():
        return False, f"{row['target']} is not an agent"
    if row["state"] != "landed" or not row["delivered_at"] or db.answer_row_of(con, row["id"]) is not None:
        return False, f"#{row['id']} was not cancelled after it was delivered"
    _background(f"notify-cancelled-{row['id']}",
                lambda c: delivery.notify_cancelled(c, row, str(row["verdict"] or "")), log)
    return True, _HANDED

