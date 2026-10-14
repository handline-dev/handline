"""session.input, keys, type and interrupt: what a person at the terminal does. They serve the IDE and the web UI; no hl
command does (the daemon's own typing is `message` and the tick's). Every call takes the agent's terminal lock for as long as it
types, so the tick types nothing beside it, and passes the gates of the plan's table in order: role (the method's own,
member), terminal lock, input holder, and for `input` the wall and an open dialog. `keys`, `type` and `interrupt` pass
neither of the last two, as a person pressing a key has looked at the screen. A result is recorded in `write_effects`
the moment the keys are sent, so a retry after a lost reply replays it and types nothing twice.

`input` sends a prompt line (Enter by default) to a session, never to a shell. `type` sends printable characters and tabs
as typed. `keys` are the named keys of `term.KEY_BYTES`. `interrupt` is Esc, and the daemon sends it on no other
occasion. The one who types counts as typing for the idle hold."""
from __future__ import annotations


from .. import db
from ..infra import lock, paths
from ..session import stopgate, term
from . import delivery, inputhold, lifecycle, rpc, rpc_gates

MAX_TEXT, MAX_KEYS = 8000, 64


def _agent(params) -> str:
    name = params.get("agent")
    if not isinstance(name, str) or not name:
        raise rpc.bad_request("agent is a name", "agent", rpc.INVALID_PARAMS)
    if name not in paths.list_agents():
        raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no such agent: {name}", what="agent", name=name)
    return name


def _text(params, allowed: str) -> str:
    """The text of a call, refused when it holds a control character outside `allowed`: Esc and the rest are keys."""
    t = params.get("text")
    if not isinstance(t, str) or not t or len(t) > MAX_TEXT:
        raise rpc.bad_request(f"text is 1 to {MAX_TEXT} characters", "text", rpc.INVALID_PARAMS)
    if any(not (c.isprintable() or c in allowed) for c in t):
        raise rpc.bad_request("text holds a control character: those are session.keys", "text", rpc.INVALID_PARAMS)
    return t


def _act(ctx, name: str, params, send, wall: bool = False) -> dict:
    """Take the lock, pass the gates, send with `send(handle)` -> (ok, why), and record the effect."""
    agent = _agent(params)
    con = db.connect()
    try:
        with lock.terminal(agent) as mine:
            if not mine:
                raise rpc.refused(f"{agent}: {lifecycle.TYPING_ELSEWHERE}", gate="terminal_lock")
            rpc_gates.input_holder(ctx, con, agent)
            handle = db.get_terminal(con, agent)
            if not handle or not term.available():
                raise rpc.refused(f"{agent} has no terminal", gate="state")
            if wall:
                why = stopgate.wall_reason(con, agent)
                if why:
                    raise rpc.refused(why, gate="wall")
                blocker = term.screen_blocker(handle)
                if blocker is not None:
                    raise rpc.refused(f"{blocker.reason()}; use session.keys or prompt.answer", gate="screen_blocker")
            ok, why = send(handle)
            if not ok:
                raise rpc.refused(f"{agent}: nothing sent - {why}", gate="state")
            delivery._mark_typed(con, agent, ctx.caller.actor_info)
            inputhold.touch(con, agent, ctx.caller.info["name"])
            result = {"accepted": True, "said": f"{agent}: {name} sent"}
            db.record_effect(con, ctx.caller.writer, params["request_id"], name, result)
            return result
    finally:
        con.close()


def _input(ctx, params) -> dict:
    text, enter = _text(params, "\n\t"), params.get("enter", True)
    if not isinstance(enter, bool):
        raise rpc.bad_request("enter is true or false", "enter", rpc.INVALID_PARAMS)
    return _act(ctx, "session.input", params, lambda handle: term.send_to_session(handle, text, enter=enter), wall=True)


def _type(ctx, params) -> dict:
    text = _text(params, "\t")
    return _act(ctx, "session.type", params, lambda handle: term.send(handle, text, enter=False))


def _keys(ctx, params) -> dict:
    keys = params.get("keys")
    if not isinstance(keys, list) or not 1 <= len(keys) <= MAX_KEYS or any(not isinstance(k, str) or k not in term.KEY_BYTES for k in keys):
        raise rpc.bad_request(f"keys is 1 to {MAX_KEYS} of {', '.join(term.KEY_BYTES)}", "keys", rpc.INVALID_PARAMS)
    return _act(ctx, "session.keys", params, lambda handle: term.send_keys(handle, keys))


def _interrupt(ctx, params) -> dict:
    return _act(ctx, "session.interrupt", params, lambda handle: term.send_keys(handle, ["esc"]))


rpc.METHODS["session.input"] = rpc.Method(_input, "member", write=True)
rpc.METHODS["session.keys"] = rpc.Method(_keys, "member", write=True)
rpc.METHODS["session.type"] = rpc.Method(_type, "member", write=True)
rpc.METHODS["session.interrupt"] = rpc.Method(_interrupt, "member", write=True)
