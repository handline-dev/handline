"""An account whose login expired: marked from a terminal's screen, its agents moved off it.

Claude Code draws "Login expired · Please run /login" when a turn is refused,
and fires no hook the daemon reads: the agent stays RUNNING on an account that
can run nothing, and the account still looks logged in, since its credential is
there. The screen is the only witness, so it is read here and the account is
marked (`accounts.mark_login_expired`), which is what stops launches onto it
and makes the bridge offer a re-login. The move is level-triggered on that
mark, not on the screen: every agent on a marked account leaves it.
"""
from __future__ import annotations

import time

from ..team import accounts, agents
from .. import db
from ..session import orca
from ..session import triggers
from . import handoff
from .states import QUEUED, RUNNING, IDLE, STALLED

LINE = "Login expired · Please run /login"
# Only the rows nearest the prompt count, and only a row that is nothing but the
# line: a quote of it in prose, or in this team's own talk about it, is not it.
SCREEN_ROWS = 14
BULLETS = "⏺●"
# One screen read per agent this often: the tick is two seconds and a read costs a call to Orca.
CHECK_EVERY_S = 20
# A session this young has its own screen on: a line left over from the one before is not its.
FRESH_SESSION_S = 120

LOGIN_STALL = "login expired"

_checked: dict[str, float] = {}


def shown(text: str) -> bool:
    rows = [r.strip() for r in text.splitlines() if r.strip()][-SCREEN_ROWS:]
    return any(r[0] in BULLETS and r[1:].strip() == LINE for r in rows)


def _session_row(con, agent: str):
    return con.execute("SELECT MAX(id) AS id, MAX(ts) AS ts FROM events WHERE agent = ? AND kind IN "
                       "('SessionStart', 'moved_account')", (agent,)).fetchone()


def _session_age_s(con, agent: str) -> float | None:
    row = _session_row(con, agent)
    return time.time() - int(row["ts"]) if row and row["ts"] else None


def _lifted_key(agent: str) -> str:
    return f"login_lifted:{agent}"


def _lifted_in_this_session(con, agent: str) -> bool:
    """A stall was lifted in the session still running. Its screen keeps the line it drew
    before the login, and a read of it would mark the account again."""
    return db.cursor_updated(con, _lifted_key(agent)) is not None \
        and db.get_cursor(con, _lifted_key(agent)) == int(_session_row(con, agent)["id"] or 0)


def _mark_from_screen(con, agent: str, row, acct: str, log) -> dict | None:
    """The account's mark, made now when this agent's screen shows the line."""
    handle = row["terminal"]
    now = time.time()
    if not handle or now - _checked.get(agent, 0) < CHECK_EVERY_S or not orca.available():
        return None
    _checked[agent] = now
    age = _session_age_s(con, agent)
    if age is not None and age < FRESH_SESSION_S or _lifted_in_this_session(con, agent):
        return None
    text = orca.screen_text(handle)
    if text is None or not shown(text):
        return None
    why = f"{agent}'s terminal showed \"{LINE}\""
    if accounts.mark_login_expired(acct, why):
        db.add_event(con, agent, "login_expired", {"account": acct, "why": why}, account=acct)
        log(f"  {acct}: login expired ({why}) - marked logged out, no launch lands on it")
    return accounts.login_expired(acct)


def check(con, agent: str, row, acct: str | None, log, actions: list[str]) -> bool:
    """True when this agent was dealt with and the tick has nothing more to do for it."""
    if not acct or not row or row["closed_at"] or not row["terminal"] \
            or row["state"] not in (RUNNING, IDLE, QUEUED):
        return False
    marked = accounts.login_expired(acct) or _mark_from_screen(con, agent, row, acct, log)
    if not marked:
        return False
    dest, why = handoff._move_plan(con, agent, handoff.LOGIN_TRIGGER)
    if dest is None:
        # Nothing is waiting out a window here, so a retry loop would only fail again:
        # the agent is shown as stopped, and `lift` releases it once the account logs in.
        detail = f"{LOGIN_STALL} on {acct} and no other account can take it ({why}) - ocp login {acct}"
        db.set_agent_state(con, agent, STALLED, wake_at=None, detail=detail[:300])
        actions.append(f"{agent}: STALLED, {LOGIN_STALL} on {acct}")
        log(f"  {agent}: {detail}")
        return True
    trigger = triggers.Trigger(True, handoff.LOGIN_TRIGGER, f"{LOGIN_STALL} on {acct}: {marked.get('why')}")
    ok = handoff.perform_handoff(con, agent, trigger, log, dest=dest)
    actions.append(f"{agent}: handoff (login){'' if ok else ' -> degraded'}")
    return True


def lift(con, agent: str, row, log, actions: list[str]) -> None:
    """A stall raised here ends once its account is logged in again."""
    if row["state"] != STALLED or LOGIN_STALL not in (row["detail"] or ""):
        return
    acct = agents.current_account(agent, con)
    if acct and accounts.login_expired(acct):
        return
    db.set_cursor(con, _lifted_key(agent), int(_session_row(con, agent)["id"] or 0))
    db.set_agent_state(con, agent, IDLE, wake_at=None,
                       detail=f"{acct or 'its account'} is logged in again; the stall is lifted")
    actions.append(f"{agent}: STALLED -> IDLE ({acct} logged in again)")
    log(f"  {agent}: {acct} is logged in again; the stall is lifted")
