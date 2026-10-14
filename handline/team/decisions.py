"""The founder's answer to a roster or trust proposal, kept where no agent can write it.

The request row a decision is answered on lives in the db, which every agent
can write, so a verdict read from it is no proof the founder gave it. The
answer is therefore recorded here as it is given - by the founder's own
session, in the protected store - and the Go daemon's hr.decide and `trust.apply` act on this
record, never on the row alone. A proposal answered before this record existed
has none and is raised again.
"""
from __future__ import annotations

import hashlib
import json
import time

from .. import db
from ..infra import protected


def _ns(kind: str) -> str:
    return f"{kind}-decisions"


def digest(fields: dict) -> str:
    """What was decided on, as one comparable string."""
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def record(kind: str, pid: str, task_id: int, call: str, subject: str) -> None:
    """`call` is "approved" or "rejected"; `subject` is what it was decided on
    (the module's `subject_of` for the draft the founder was shown); `task_id` is the
    decision task it was answered on (T785: a proposal is keyed by its task).
    PermissionError from an agent's session."""
    protected.put(_ns(kind), pid, json.dumps(
        {"task": int(task_id), "call": call, "subject": subject,
         "at": int(time.time())}))


def get(kind: str, pid: str) -> dict | None:
    raw = protected.get(_ns(kind), pid)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def refusal(kind: str, pid: str, task_id: int, subject: str, card_id: int | None = None) -> str | None:
    """Why the founder's answer to decision task `task_id` cannot be relied on, or None
    when the record names this task, a decision, and the draft as it is now. A record written
    before proposals were keyed by task names the card request instead: it counts for the task
    whose card that is (`card_id`)."""
    got = get(kind, pid)
    if got is None:
        return (f"no answer from the founder is on record for proposal {pid} - it was "
                f"answered before answers were recorded, or not by the founder; raise it again")
    names_it = (got.get("task") == int(task_id) if "task" in got
                else card_id is not None and got.get("request") == int(card_id))
    if not names_it or got.get("call") not in ("approved", "rejected"):
        return f"the founder's recorded answer for proposal {pid} is not for T{task_id}"
    if got.get("subject") != subject:
        return (f"proposal {pid} was changed after the founder answered it - what they "
                f"approved is not what is in the draft now; raise it again")
    return None


# ---------------------------------------------------------------- decision tasks
# What the founder types to answer a decision task, read by the first word. Trust proposals are
# still raised as such a task; roster proposals are not (`hr.decide`).
LEGACY_APPROVE = ("승인",)
LEGACY_REJECT = ("반려",)
APPROVE = ("approve", "approved", "yes", "ok", "go", *LEGACY_APPROVE)
REJECT = ("reject", "rejected", "no", "deny", "denied", *LEGACY_REJECT)


def call_of(verdict: str | None) -> str:
    """approved / rejected / unclear, from the first word of the verdict."""
    first = (verdict or "").strip().split()
    if not first:
        return "unclear"
    word = first[0].strip(".,!:;\"'`").lower()
    if word in APPROVE:
        return "approved"
    if word in REJECT:
        return "rejected"
    return "unclear"


def task_of_card(con, rid: int) -> int | None:
    """The decision task whose card is request `rid`, in whatever state it is now."""
    for t in db.tasks_for_request(con, rid):
        first = db.first_linked_request(con, t["id"])
        if t["expects"] == "decision" and first is not None and int(first["id"]) == int(rid):
            return int(t["id"])
    return None


def card_of(con, tid: int):
    """The request the founder was shown: the decision's own first request."""
    return db.first_linked_request(con, tid)


def answer_of(con, prop: dict) -> tuple[str, int | None, str | None]:
    """(state, task id, verdict) of the founder's answer to a proposal, read off its decision task:
    "none" (no task to read), "open" (not answered), "discarded" (the card was withdrawn, handed
    back, thrown away or marked done by someone but the founder: nothing was decided) or
    "answered" with the verdict he gave."""
    tid = int(prop.get("task") or 0) or task_of_card(con, int(prop.get("request") or 0))
    task = db.get_task(con, tid) if tid else None
    if task is None:
        return "none", None, None
    if task["discarded_by"]:
        return "discarded", tid, None
    if task["done_evidence"]:
        # Only the founder's own close is his answer: an agent that was handed the task and marked it
        # done wrote evidence, not a decision, so it reads like a hand-back.
        last = con.execute(
            f"SELECT kind, agent FROM events WHERE {db.EVENT_TASK} = ? AND kind IN "
            "('settle_decision_task', 'mark_task_done', 'reopen_task') ORDER BY id DESC LIMIT 1",
            (tid,)).fetchone()
        if task["owner"] == db.HUMAN and last is not None and (last["kind"] == "settle_decision_task"
                                 or (last["kind"] == "mark_task_done" and last["agent"] == db.HUMAN)):
            return "answered", tid, task["done_evidence"]
        return "discarded", tid, None
    return "open", tid, None
