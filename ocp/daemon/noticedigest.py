"""Results told to whoever opened the work reach them in one digest, not one wake each.

A result notice (a task done or discarded, a child's result) is information: it rides on the
target's next real delivery and never wakes anyone. Left at that, an opener that sits idle
learns of a finished task only when something else happens to be typed at it. So the first
notice starts a clock, and when it has run `idle_close.notice_digest_s` (15 minutes unless
config.yaml's, or the agent's own, `notice_digest_s` says otherwise) the notices still waiting
are typed once, as a single message of one line each. A message typed before then carries them
and the clock has nothing left to run on.

What goes wrong is not information. A task that ends FAIL, blocked or FATAL, and an agent the
daemon finds FATAL or blocked, are told to the agents waiting on them at once, as their own
message, not held for a digest. Both are read as levels from the database each tick.
"""
from __future__ import annotations

import re
import time

from .. import db
from ..infra import paths, textsafe
from ..session import idle_close
from . import delivery, messaging

DIGEST_MAX_LINES = 20
RESULT_SHOWN = 80
_RESULT = re.compile(r"^T(\d+) (done|discarded) by (.+?):\s*(.*)$", re.S)


def _say(con, to: str, text: str, reason: str) -> int:
    """One message to `to` from the task board, written and queued: not a notice, so it wakes a
    closed agent once told."""
    from ..team import ask as askmod, team

    rid = db.add_request(con, db.NOTICE_REQUESTER, to, text, None, None, "fire_and_forget", "say", 0, None)
    askmod.deliver(rid, db.NOTICE_REQUESTER, to, text, None,
                   team.Decision(True, reason, kind="say", response="fire_and_forget", chain_depth=0))
    db.add_event(con, db.NOTICE_REQUESTER, "notice_told", {"request_id": rid, "to": to, "reason": reason})
    return rid


def _tell_now(con, to: str, text: str, reason: str, log) -> int:
    rid = _say(con, to, text, reason)
    messaging.tell_now(con, rid, log)
    return rid


def digest_line(row) -> str:
    """`T<n> · who · result (#id)` for a result notice; any other notice is its own text."""
    body = " ".join(textsafe.oneline(row["body"]).split())
    m = _RESULT.match(body)
    if not m:
        return f"{body[:RESULT_SHOWN + 30]} (#{row['id']})"
    tid, how, who, result = m.groups()
    result = result if how == "done" else f"discarded: {result}"
    if len(result) > RESULT_SHOWN:
        result = result[:RESULT_SHOWN].rstrip() + "…"
    return f"T{tid} · {who} · {result} (#{row['id']})"


def digest_text(rows) -> str:
    shown = rows[:DIGEST_MAX_LINES]
    left = len(rows) - len(shown)
    head = f"{db.DIGEST_HEAD}{len(rows)} result{'s' if len(rows) != 1 else ''} since {time.strftime('%H:%M', time.localtime(int(rows[0]['landed_at'])))}" \
           " - `ocp read <id>` shows one whole:"
    return "\n".join([head, *(digest_line(r) for r in shown), *([f"(+{left} more: `ocp inbox --all`)"] if left else [])])


def sweep_digests(con, log, now: int) -> None:
    """Type one digest to each agent whose oldest waiting notice has waited its window out."""
    registered = set(paths.list_agents())
    for agent, since in db.agents_with_notices(con):
        if agent not in registered or now - since < idle_close.notice_digest_s(agent):
            continue
        rows = db.pending_notices(con, agent)
        if not rows:
            continue
        # Written and marked before it is told: a tell that raises leaves the digest queued for
        # `tell_undelivered`, and no second digest is built of the same results.
        rid = _say(con, agent, digest_text(rows), "notice digest")
        db.mark_notices_digested(con, [int(r["id"]) for r in rows])
        db.add_event(con, db.NOTICE_REQUESTER, "notices_digested",
                     {"request_id": rid, "to": agent, "notices": [int(r["id"]) for r in rows]})
        messaging.tell_now(con, rid, log)
        log(f"  {agent}: {len(rows)} waiting result(s) told in one digest (#{rid})")


def _waiting_on(con, agent: str) -> list[str]:
    """Who is waiting on `agent`: whoever opened a task it owns, or asked it something still queued."""
    known = set(paths.list_agents())
    who = [t["opened_by"] for t in db.open_tasks(con) if t["owner"] == agent]
    who += [r["requester"] for r in db.queued_for(con, agent)]
    return [w for w in dict.fromkeys(who) if w in known and w != agent]


def _tell_waiting(con, agent: str, text: str, reason: str, log) -> None:
    for who in _waiting_on(con, agent):
        _tell_now(con, who, text, reason, log)
        log(f"  {who}: told at once that {agent} {reason}")


def sweep_stopped(con, log, now: int) -> None:
    """An agent that is FATAL, or whose work cannot be given (it is on the founder's list), is
    told once per spell to whoever waits on it; the mark ends with the spell."""
    from . import states

    stalled = {r["agent"]: r for r in db.all_agent_states(con) if r["state"] == states.STALLED}
    blocks = {b["agent"]: b for b in db.blocks(con) if now >= int(b["show_at"])}
    for kind, now_in in (("fatal", stalled), ("blocked", blocks)):
        for agent in set(paths.list_agents()):
            key = f"stopped_told:{kind}:{agent}"
            if agent not in now_in:
                if db.get_cursor(con, key):
                    db.set_cursor(con, key, 0)
                continue
            if db.get_cursor(con, key):
                continue
            db.set_cursor(con, key, 1)
            detail = " ".join(textsafe.oneline((stalled[agent]["detail"] if kind == "fatal"
                                                else blocks[agent]["detail"]) or "").split())[:300]
            what = ("blocked" if kind == "blocked"
                    else "stalled" if detail.startswith(delivery.DIALOG_STALL) else "FATAL")
            _tell_waiting(con, agent, f"{agent} is {what}: {detail}", f"is {what}", log)


def sweep(con, log, now: int) -> None:
    sweep_stopped(con, log, now)
    sweep_digests(con, log, now)
