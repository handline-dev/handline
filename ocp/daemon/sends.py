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
    target, task, fyi = kw["target"], kw.get("task"), kw.get("fyi")
    if fyi is not None and not str(fyi).strip():
        return False, "--fyi says why this is not work: give the reason"
    if task is not None and fyi:
        return False, "--task and --fyi contradict each other: use one"
    if task is not None:
        # Structural, not read from the text: a say to a report about a task is
        # that task's note, delivered as one.
        row = db.get_task(con, task)
        if row is None:
            return False, f"no task T{task}"
        if row["owner"] != target:
            return False, f"T{task} is with {row['owner']}, not {target}"
        return actions.task_note(con, me, task, kw["body"])
    if kw.get("parent") is None and not fyi and _is_report_of(me, target):
        return False, ("make it a task (`ocp task new`), or say --task T<n> to note one, "
                       "or say --fyi 'why' to say it is not work")
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
    if row["state"] != "rejected" or not row["delivered_at"]:
        return False, f"#{row['id']} was not cancelled after it was delivered"
    _background(f"notify-cancelled-{row['id']}",
                lambda c: delivery.notify_cancelled(c, row, str(row["verdict"] or "")), log)
    return True, _HANDED

