"""session.start, wake, stop, restart and park, and terminal.close: the session of an agent driven through the
daemon. They re-expose the legacy verbs `start`, `wake`, `close`, `restart` and `park` (hl wake, hl close, hl restart,
hl park, hl start), with the verbs' own state checks and no new one; `session.stop` adds `exit` and `kill`, and
`terminal.close` is the web UI's Close terminal.

A refusal names its gate: `terminal_lock` when whoever types at the agent holds the lock the tick takes, `state` for
every other reason the action gives. The action is the daemon's own and takes its time, as the verb did, so the
reply comes when it is done. A retry after a crash is judged by the agent's state, which the first run changed: a
session already woken, closed or parked refuses a second wake, close or park, and nothing is done twice.
`session.start` has no account to name yet and refuses one."""
from __future__ import annotations

import os
import signal

from .. import db
from ..actions import control
from ..infra import lock, paths
from ..session import observe as obs
from ..session import term
from . import delivery, lifecycle, rpc, states


def _noop(_m: str) -> None:
    return None


def _agent(params) -> str:
    name = params.get("agent")
    if not isinstance(name, str) or not name:
        raise rpc.bad_request("agent is a name", "agent", rpc.INVALID_PARAMS)
    if name not in paths.list_agents():
        raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no such agent: {name}", what="agent", name=name)
    return name


def _flag(params, key: str) -> bool:
    v = params.get(key, False)
    if not isinstance(v, bool):
        raise rpc.bad_request(f"{key} is true or false", key, rpc.INVALID_PARAMS)
    return v


def _refusal(why: str) -> rpc.RpcError:
    return rpc.refused(why, gate="terminal_lock" if lifecycle.TYPING_ELSEWHERE in why else "state")


def _done(ok: bool, said: str) -> dict:
    if not ok:
        raise _refusal(said)
    return {"accepted": True, "said": said}


def _locked(agent: str) -> None:
    """Refuse at once while someone else holds the agent's terminal lock, before anything is started."""
    with lock.terminal(agent) as mine:
        if not mine:
            raise rpc.refused(f"{agent}: {lifecycle.TYPING_ELSEWHERE}", gate="terminal_lock")


def _run(params, fn) -> dict:
    agent = _agent(params)
    _locked(agent)
    con = db.connect()
    try:
        return fn(con, agent)
    finally:
        con.close()


def _start(ctx, params) -> dict:
    if params.get("account") is not None:
        raise rpc.bad_request("account is not served yet: a session starts on the account the daemon picks", "account", rpc.INVALID_PARAMS)
    for k in ("repo", "base"):
        if params.get(k) is not None and not isinstance(params[k], str):
            raise rpc.bad_request(f"{k} is a string", k, rpc.INVALID_PARAMS)
    return _run(params, lambda con, agent: _done(*control.start_result(con, agent, params.get("repo"), params.get("base") or "main")))


def _wake(ctx, params) -> dict:
    def wake(con, agent):
        row = db.get_agent_state(con, agent)
        if row and row["state"] == states.STALLED:      # a wake of a stalled agent clears the stall, as the verb does
            return _done(True, control.unstall(con, agent))
        ok, why = lifecycle.wake_agent(con, agent, _noop)
        return _done(ok, f"{agent}: {why}" if ok else f"{agent}: not woken - {why}")
    return _run(params, wake)


def _restart(ctx, params) -> dict:
    fresh, force = _flag(params, "fresh"), _flag(params, "force")
    return _run(params, lambda con, agent: _done(*control.restart_result(con, agent, fresh, force, ctx.caller.actor)))


def _park(ctx, params) -> dict:
    return _run(params, lambda con, agent: _done(*control.park_result(con, agent)))


def _exit(con, agent: str) -> dict:
    handle = db.get_terminal(con, agent)
    if not handle or not term.available():
        raise rpc.refused(f"{agent} has no terminal to ask to quit", gate="state")
    if not delivery.session_present(agent):
        raise rpc.refused(f"{agent} has no session running", gate="state")
    ok, why = term.close_session(handle)
    return _done(ok, f"{agent}: asked to quit - {why}" if ok else f"{agent}: not asked to quit - {why}")


def _kill(con, agent: str) -> dict:
    sessions = obs.live_sessions()
    pid = sessions.get(agent) if sessions is not None else None
    if not pid:
        raise rpc.refused(f"{agent} has no session process" if sessions is not None else "the process table could not be read", gate="state")
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as e:
        raise rpc.refused(f"{agent}: could not end process {pid}: {e.strerror or e}", gate="state") from None
    gone = delivery._wait_session_gone(agent)
    return _done(True, f"{agent}: session process {pid} ended" if gone else f"{agent}: session process {pid} was told to end and still runs")


def _stop(ctx, params) -> dict:
    mode, fresh = params.get("mode"), _flag(params, "fresh")
    if mode not in ("exit", "close", "kill"):
        raise rpc.bad_request("mode is exit, close or kill", "mode", rpc.INVALID_PARAMS)
    if fresh and mode != "close":
        raise rpc.bad_request("fresh is for close only", "fresh", rpc.INVALID_PARAMS)
    if mode == "close":
        return _run(params, lambda con, agent: _done(*control.close_result(con, agent, fresh)))
    return _run(params, _exit if mode == "exit" else _kill)


def _terminal_close(ctx, params) -> dict:
    def close(con, agent):
        handle = db.get_terminal(con, agent)
        if not handle:
            raise rpc.refused(f"{agent} has no terminal", gate="state")
        row = db.get_agent_state(con, agent)
        if (row and not row["closed_at"]) or delivery.session_present(agent):
            raise rpc.refused(f"{agent} has a session running; stop it first", gate="state")
        ok, why = term.close_terminal(handle)
        if not ok:
            raise rpc.refused(f"{agent}: its terminal would not close: {why}", gate="state")
        db.set_terminal(con, agent, None, row["worktree"] if row else None)
        db.add_event(con, agent, "terminal_closed", {"handle": handle})
        return {"accepted": True, "said": f"{agent}: terminal closed"}
    return _run(params, close)


rpc.METHODS["session.start"] = rpc.Method(_start, "agent", write=True)
rpc.METHODS["session.wake"] = rpc.Method(_wake, "agent", write=True)
rpc.METHODS["session.stop"] = rpc.Method(_stop, "agent", write=True)
rpc.METHODS["session.restart"] = rpc.Method(_restart, "agent", write=True)
rpc.METHODS["session.park"] = rpc.Method(_park, "agent", write=True)
rpc.METHODS["terminal.close"] = rpc.Method(_terminal_close, "member", write=True)
