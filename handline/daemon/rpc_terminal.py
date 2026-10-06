"""terminal.attach, terminal.detach, terminal.history and terminal.resize: the terminal channel (daemon/termchannel.py). They
serve the IDE and the web UI, which no longer read terminals themselves; no hl command serves them.

`attach` replies with the snapshot (the frame and one page of history) and then, once that reply is sent, the connection is
pushed `frame`, `resize`, `marker` and `holder` notices until it detaches or hangs up. Many connections watch one terminal with
one poller, which stops with the last of them. `resize` is the input holder's alone, by the holder gate (the daemon holding
is refused too: nobody holds it to resize it), and where the host cannot resize it says so (gate `unsupported`); the size
before it is given back when the holder leaves."""
from __future__ import annotations

from .. import db
from ..infra import paths
from . import inputhold, rpc, termchannel

DEFAULT_HISTORY, MAX_HISTORY, MAX_COLS, MAX_ROWS = 100, 2000, 500, 200


def _int(params, key, lo, hi):
    v = params.get(key)
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise rpc.bad_request(f"{key} is an integer from {lo} to {hi}", key, rpc.INVALID_PARAMS)
    return v


def _handle(con, params) -> tuple[str, str | None]:
    """(terminal handle, the agent that has it) for `agent` or `terminal`, never both; not_found for one nobody has."""
    agent, handle = params.get("agent"), params.get("terminal")
    if (agent is None) == (handle is None):
        raise rpc.bad_request("give agent or terminal, not both", "agent", rpc.INVALID_PARAMS)
    if agent is not None:
        if not isinstance(agent, str) or not agent:
            raise rpc.bad_request("agent is a name", "agent", rpc.INVALID_PARAMS)
        if agent not in paths.list_agents():
            raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no such agent: {agent}", what="agent", name=agent)
        handle = db.get_terminal(con, agent)
        if not handle:
            raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"{agent} has no terminal", what="terminal", name=agent)
        return handle, agent
    if not isinstance(handle, str) or not handle:
        raise rpc.bad_request("terminal is a handle", "terminal", rpc.INVALID_PARAMS)
    for name in paths.list_agents():
        if db.get_terminal(con, name) == handle:
            return handle, name
    raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no terminal {handle}", what="terminal", name=handle)


def _attach(ctx, params) -> dict:
    history = params.get("history", DEFAULT_HISTORY)
    if isinstance(history, bool) or not isinstance(history, int) or not 0 <= history <= MAX_HISTORY:
        raise rpc.bad_request(f"history is an integer from 0 to {MAX_HISTORY}", "history", rpc.INVALID_PARAMS)
    con = db.connect()
    try:
        handle, agent = _handle(con, params)
        holders = inputhold.view(con, agent)
    finally:
        con.close()
    ch = termchannel.HUB.channel(handle)
    client = termchannel.Client(ctx.send_line, ctx.hang_up)
    ch.attach(client)
    ctx.after_reply.append(lambda: ch.begin(client))
    ctx.on_close.append(lambda: ch.detach(client))
    ctx.terminals = getattr(ctx, "terminals", {})
    ctx.terminals.setdefault(handle, []).append(client)
    return ch.snapshot(history, holders)


def _detach(ctx, params) -> dict:
    handle = params.get("terminal")
    if not isinstance(handle, str) or not handle:
        raise rpc.bad_request("terminal is a handle", "terminal", rpc.INVALID_PARAMS)
    ch = termchannel.HUB.channels.get(handle)
    for client in getattr(ctx, "terminals", {}).pop(handle, []):
        if ch is not None:
            ch.detach(client)
    return {}


def _history(ctx, params) -> dict:
    before, limit = _int(params, "before", 0, 2**40), _int(params, "limit", 1, MAX_HISTORY)
    con = db.connect()
    try:
        handle, _ = _handle(con, {"terminal": params.get("terminal")})
    finally:
        con.close()
    return termchannel.HUB.channel(handle).history_page(before, limit)


def _resize(ctx, params) -> dict:
    cols, rows = _int(params, "cols", 10, MAX_COLS), _int(params, "rows", 3, MAX_ROWS)
    con = db.connect()
    try:
        handle, agent = _handle(con, {"terminal": params.get("terminal")})
        holder = inputhold.holder(con, agent)
        if holder is None or holder["name"] != ctx.caller.info["name"]:
            raise rpc.refused("only whoever holds the terminal's input may resize it" + (f"; {holder['name']} does" if holder else "; the daemon does"),
                              gate="input_holder")
        ch = termchannel.HUB.channel(handle)
        before = termchannel.read_size(handle)
        ok, why = termchannel.do_resize(handle, cols, rows)
        if not ok:
            raise rpc.refused(f"{agent}'s terminal host cannot resize it: {why}", gate="unsupported")
        if ch.restore is None and before:
            ch.restore = before
        ch.cols, ch.rows = cols, rows
        if ch.clients:
            ch.notify("resize", cols=cols, rows=rows)
        inputhold.touch(con, agent, ctx.caller.info["name"])
        return {"accepted": True, "said": f"{agent}: terminal is {cols} by {rows}"}
    finally:
        con.close()


rpc.METHODS["terminal.attach"] = rpc.Method(_attach, "member")
rpc.METHODS["terminal.detach"] = rpc.Method(_detach, "member")
rpc.METHODS["terminal.history"] = rpc.Method(_history, "member")
rpc.METHODS["terminal.resize"] = rpc.Method(_resize, "member", write=True)
