"""terminal.attach, terminal.detach, terminal.history and terminal.resize: the terminal channel (daemon/termchannel.py). They
serve the IDE and the web UI, which no longer read terminals themselves; no hl command serves them.

`attach` replies with the snapshot (the frame and one page of history) and then, once that reply is sent, the connection is
pushed `frame`, `resize`, `marker` and `holder` notices until it detaches or hangs up. Many connections watch one terminal with
one poller, which stops with the last of them. `resize` is the input holder's alone, by the holder gate (the daemon holding
is refused too: nobody holds it to resize it), and where the host cannot resize it says so (gate `unsupported`); the size
before it is given back when the holder leaves. A terminal `account.login` opened belongs to no agent: it can be watched,
has no holder, and is not resized; the daemon forgets it when it restarts."""
from __future__ import annotations

import base64
import binascii

from .. import db
from ..infra import paths
from . import inputhold, rpc, rpc_gates, termbytes, termchannel

DEFAULT_HISTORY, MAX_HISTORY, MAX_COLS, MAX_ROWS = 100, 2000, 500, 200
LOGIN_TERMINALS: set[str] = set()       # handles of the terminals account.login opened


def _int(params, key, lo, hi):
    v = params.get(key)
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise rpc.bad_request(f"{key} is an integer from {lo} to {hi}", key, rpc.INVALID_PARAMS)
    return v


MAX_INPUT = 16384


def _format(params) -> str:
    fmt = params.get("format", "text")
    if fmt not in ("text", "bytes", "styled"):
        raise rpc.bad_request("format is text, bytes or styled", "format", rpc.INVALID_PARAMS)
    return fmt


def _serves_bytes() -> None:
    """Refuse, gate `unsupported`, a bytes request on a terminal host that cannot serve it: nothing is attached."""
    from ..session import backends
    host = backends.current()
    if not host.serves_bytes or not host.available():
        raise rpc.refused(f"the terminal host ({host.name}) does not serve the bytes format: ask for text", gate="unsupported")


def _serves_styled() -> None:
    """Refuse, gate `unsupported`, a styled request on a terminal host whose screen read has no cell attributes: nothing is attached."""
    from ..session import backends
    host = backends.current()
    if not host.serves_styled or not host.available():
        raise rpc.refused(f"the terminal host ({host.name}) does not serve the styled format: ask for text", gate="unsupported")


def _handle(con, params) -> tuple[str, str | None]:
    """(terminal handle, the agent that has it, None for a login terminal) for `agent` or `terminal`, never both; not_found for one nobody has."""
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
    if handle in LOGIN_TERMINALS:
        return handle, None
    raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no terminal {handle}", what="terminal", name=handle)


def _attach(ctx, params) -> dict:
    history = params.get("history", DEFAULT_HISTORY)
    if isinstance(history, bool) or not isinstance(history, int) or not 0 <= history <= MAX_HISTORY:
        raise rpc.bad_request(f"history is an integer from 0 to {MAX_HISTORY}", "history", rpc.INVALID_PARAMS)
    fmt = _format(params)
    con = db.connect()
    try:
        handle, agent = _handle(con, params)
        holders = inputhold.view(con, agent) if agent else []
    finally:
        con.close()
    if fmt == "bytes":
        return _attach_bytes(ctx, handle, history, holders)
    if fmt == "styled":
        _serves_styled()
    client = termchannel.Client(ctx.send_line, ctx.hang_up)
    ch = termchannel.HUB.attach(handle, client, fmt)
    ctx.after_reply.append(lambda: ch.begin(client))
    _hold(ctx, handle, fmt, ch, client)
    return ch.snapshot(history, holders)


def _hold(ctx, handle: str, fmt: str, ch, client) -> None:
    """Keep the attach of `handle` in `fmt` on this connection: one a terminal and format, so a second attach replaces the first (its client leaves
    the channel at once, and is sent nothing after this reply). The other format is independent, and closing the connection ends them all."""
    if not hasattr(ctx, "terminals"):
        ctx.terminals = {}
        ctx.on_close.append(lambda: [och.detach(oc) for och, oc in list(ctx.terminals.values())])
    old = ctx.terminals.get((handle, fmt))
    ctx.terminals[(handle, fmt)] = (ch, client)
    if old is not None:
        old[0].detach(old[1])


def _attach_bytes(ctx, handle: str, history: int, holders: list[dict]) -> dict:
    _serves_bytes()
    link = termbytes.link()
    if not link.start():
        raise rpc.refused("the terminal host could not be reached", gate="state")
    client = termbytes.BytesClient(ctx.send_line, ctx.hang_up)
    ch = termbytes.HUB.channel(handle, link)
    snap = ch.attach(client, history)
    if snap is None:
        raise rpc.refused("the terminal's screen could not be read", gate="state")
    ctx.after_reply.append(lambda: ch.begin(client))
    _hold(ctx, handle, "bytes", ch, client)
    return {"terminal": handle, "format": "bytes", "seq": client.seq, **termbytes._encode(snap), "history": snap["history"],
            "holder": holders[0] if holders else inputhold.DAEMON, "holders": holders}


def _detach(ctx, params) -> dict:
    handle = params.get("terminal")
    if not isinstance(handle, str) or not handle:
        raise rpc.bad_request("terminal is a handle", "terminal", rpc.INVALID_PARAMS)
    fmt = params.get("format")
    if fmt is not None and fmt not in ("text", "bytes", "styled"):
        raise rpc.bad_request("format is text, bytes or styled", "format", rpc.INVALID_PARAMS)
    con = db.connect()
    try:
        _handle(con, {"terminal": handle})          # not_found for a handle that does not exist; nothing to end is not an error
    finally:
        con.close()
    held = getattr(ctx, "terminals", {})
    for key in [k for k in held if k[0] == handle and fmt in (None, k[1])]:
        ch, client = held.pop(key)
        ch.detach(client)
    return {}


def _history(ctx, params) -> dict:
    before, limit = _int(params, "before", 0, 2**40), _int(params, "limit", 1, MAX_HISTORY)
    fmt = _format(params)
    con = db.connect()
    try:
        handle, _ = _handle(con, {"terminal": params.get("terminal")})
    finally:
        con.close()
    if fmt == "bytes":
        _serves_bytes()
        link = termbytes.link()
        if not link.start():
            raise rpc.refused("the terminal host could not be reached", gate="state")
        page = termbytes.history_page(link, handle, before, limit)
        if page is None:
            raise rpc.refused("the terminal's scrollback could not be read", gate="state")
        return page
    if fmt == "styled":
        _serves_styled()
    ch = termchannel.HUB.open_for(handle, fmt)       # scrollback is kept while someone watches: none for a terminal nobody does
    return ch.history_page(before, limit) if ch else {"first": 0, "lines": [], "more": False}


def _resize(ctx, params) -> dict:
    cols, rows = _int(params, "cols", 10, MAX_COLS), _int(params, "rows", 3, MAX_ROWS)
    con = db.connect()
    try:
        handle, agent = _handle(con, {"terminal": params.get("terminal")})
        if agent is None:
            raise rpc.refused("a login terminal has no input holder to resize it", gate="input_holder")
        holder = inputhold.holder(con, agent)
        if holder is None or holder["name"] != ctx.caller.info["name"]:
            raise rpc.refused("only whoever holds the terminal's input may resize it" + (f"; {holder['name']} does" if holder else "; the daemon does"),
                              gate="input_holder")
        before = termchannel.read_size(handle)
        ok, why = termchannel.do_resize(handle, cols, rows)
        if not ok:
            raise rpc.refused(f"{agent}'s terminal host cannot resize it: {why}", gate="unsupported")
        if before:
            termchannel.HUB.restore.setdefault(handle, before)
        for ch in termchannel.HUB.open_all(handle):
            ch.cols, ch.rows = cols, rows
            ch.notify("resize", cols=cols, rows=rows)
        termbytes.resized(handle)       # a bytes attach is sent a snapshot, reason resize, and no resize notice
        inputhold.touch(con, agent, ctx.caller.info["name"])
        return {"accepted": True, "said": f"{agent}: terminal is {cols} by {rows}"}
    finally:
        con.close()


def _input(ctx, params) -> dict:
    """Keystrokes and pasted bytes, from the input holder alone: refused while the daemon holds the input, and for a login terminal, which has
    none. Recorded as an event of the agent (kind `type`) by who sent it, with the byte count and never the bytes."""
    data = params.get("data")
    if not isinstance(data, str):
        raise rpc.bad_request("data is base64", "data", rpc.INVALID_PARAMS)
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise rpc.bad_request("data is base64", "data", rpc.INVALID_PARAMS) from None
    if not 1 <= len(raw) <= MAX_INPUT:
        raise rpc.bad_request(f"data is 1 to {MAX_INPUT} bytes", "data", rpc.INVALID_PARAMS)
    con = db.connect()
    try:
        handle, agent = _handle(con, {"terminal": params.get("terminal")})
        if agent is None:
            raise rpc.refused("a login terminal has no input holder to take its keystrokes", gate="input_holder")
        rpc_gates.terminal_lock(agent)
        holder = inputhold.holder(con, agent)
        if holder is None:
            raise rpc.refused(f"{agent}'s input is held by the daemon: take it first", gate="input_holder")
        if holder["name"] != ctx.caller.info["name"]:
            raise rpc.refused(inputhold.held_why(agent, holder["name"]), gate="input_holder")
        from ..session import backends
        host = backends.current()
        if not host.available() or not host.serves_bytes:
            raise rpc.refused(f"the terminal host ({host.name}) cannot send raw bytes", gate="unsupported")
        from ..infra import lock
        with lock.terminal(agent) as mine:
            if not mine:
                raise rpc.refused(f"{agent}: typing is going on elsewhere", gate="terminal_lock")
            ok, why = host.send_bytes(handle, raw)
        if not ok:
            raise rpc.refused(f"{agent}: nothing sent - {why}", gate="state")
        db.add_event(con, agent, "type", {"actor": ctx.caller.actor_info, "bytes": len(raw)})
        inputhold.touch(con, agent, ctx.caller.info["name"])
        result = {"accepted": True, "said": f"{agent}: {len(raw)} bytes sent"}
        db.record_effect(con, ctx.caller.writer, params["request_id"], "terminal.input", result)
        return result
    finally:
        con.close()


rpc.METHODS["terminal.attach"] = rpc.Method(_attach, "member")
rpc.METHODS["terminal.detach"] = rpc.Method(_detach, "member")
rpc.METHODS["terminal.history"] = rpc.Method(_history, "member")
rpc.METHODS["terminal.resize"] = rpc.Method(_resize, "member", write=True)
rpc.METHODS["terminal.input"] = rpc.Method(_input, "member", write=True)
