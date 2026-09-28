"""T351 phase 2: `ask` and `say` as socket verbs. The caller's process writes
nothing; the daemon runs the same checks and the same `team.ask`/`say` the CLI
used to, writes the row, returns its id at once, and then - on a thread of its
own, so the caller never waits for a wake - types the notice.

Who is asking travels in the payload (`requester`, and `speaker`, the caller's
OCP_AGENT): the daemon has no such environment, and the rule that nobody may
send as the human is judged against the speaker, not against the daemon.
"""
from __future__ import annotations

import threading

from .. import actions, db
from ..team import ask as askmod

from . import messaging


def _tell(rid: int, target: str, log) -> None:
    """After the reply: a task keeps the one-at-a-time head rule (`ask.push`),
    everything else is typed at once (`messaging.tell_now`)."""
    con = db.connect()
    try:
        row = db.get_request(con, rid)
        if row is None or row["state"] != "queued":
            return
        if row["kind"] == "task":
            askmod.push(con, target)
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


def ask_verb(con, log, **kw) -> tuple[bool, str]:
    """`ocp ask`, after the caller has read its body: the pre-checks that name
    a task, the ask itself, and the task link."""
    me, speaker = _who(kw)
    if me is None:
        return False, _NO_WHO
    body, target = kw["body"], kw["target"]
    task, no_task = kw.get("task"), kw.get("no_task")
    fyi = bool(kw.get("fyi"))
    named = actions.task_ids_named(body)
    if task is None and not no_task and named:
        return False, (f"this text names {', '.join(f'T{n}' for n in named)}: add --task T<n> so the "
                       "request is linked and the task shows it is being worked, or "
                       "--no-task 'reason' if it does not belong to one")
    if task is not None:
        row = db.get_task(con, task)
        if row is None:
            return False, f"no task T{task}"
        state = actions.task_state(con, row)
        if state in ("done", "closed", "discarded", "merged") and not fyi:
            again = (f"`ocp task reopen T{task} <why>` first" if state == "done"
                     else "it cannot be worked on again; open a new task")
            return False, (f"task T{task} is {state}: a request linked to it would not reopen it - "
                           f"{again}. A report (--fyi) may still be linked.")
    ok, msg, rid = askmod.ask(target, body, files=kw.get("files"), parent_id=kw.get("parent"),
                              mergeable=bool(kw.get("mergeable")), first=bool(kw.get("first")),
                              replaces=kw.get("replaces"), fyi=fyi, reason=kw.get("reason"),
                              timebox_s=kw.get("timebox"), requester=me, speaker=speaker,
                              notify=False)
    if not ok:
        return False, msg
    if target == askmod.HUMAN and "[escalation" not in msg:
        msg += "\n  it is on the founder's screen (`ocp bridge`)"
    if task is not None and rid is not None:
        db.link_request(con, task, rid)
        msg += f"\nlinked to T{task}, which now reads {actions.task_state(con, db.get_task(con, task))}"
    _after(rid, target, log)
    return True, msg


def say_verb(con, log, **kw) -> tuple[bool, str]:
    """`ocp say TARGET BODY`, and `ocp say --needs-task ID` (#1657), which is a
    say back to whoever sent the one it names."""
    me, speaker = _who(kw)
    if me is None:
        return False, _NO_WHO
    needs = kw.get("needs_task")
    if needs is not None:
        row = db.get_request(con, needs)
        why = actions.needs_task_refusal(row, me)
        if why:
            return False, why
        command = actions.needs_task_command(me, row["body"])
        ok, msg, rid = askmod.say(row["requester"], actions.flag_notice(row["id"], command),
                                  requester=me, parent_id=row["id"], speaker=speaker, notify=False)
        if ok:
            _after(rid, row["requester"], log)
        return ok, msg
    ok, msg, rid = askmod.say(kw["target"], kw["body"], requester=me, parent_id=kw.get("parent"),
                              speaker=speaker, notify=False)
    if not ok:
        return False, msg
    _after(rid, kw["target"], log)
    return True, msg + ("\n" + actions.delivery_line(rid) if rid is not None else "")
