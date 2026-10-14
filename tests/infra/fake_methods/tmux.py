"""A tmux-hosted terminal for the bytes fixtures: one agent (a.bytes) at a shell prompt that understands the few commands the
fixtures type (an echo with arithmetic, printf of the alternate-screen switch, a flood of x). It keeps what the spec asks of a
daemon: one seq counter per attach, a snapshot that holds what was printed, output notices after the reply, a holder, a
resize told to watchers as a snapshot, the size owed back to a holder and to
the viewers, and a backlog over 1 MiB replaced by a snapshot. a.orca is a terminal whose host
serves text only. The state is per FakeDaemon, so one test's terminal is not another's."""
from __future__ import annotations

import base64
import collections
import json
import re
import threading

from . import err

AGENT, HANDLE, TEXT_ONLY = "a.bytes", "t-a.bytes", "a.orca"
BACKLOG, CHUNK, SINCE = 1 << 20, 1 << 16, "2026-10-06T12:00:00Z"
REFUSED = {"code": -32002, "message": "refused", "data": {"code": "refused", "gate": "input_holder"}}
b64 = lambda b: base64.b64encode(b).decode()


class Attach:
    def __init__(self, ctx, fmt):
        self.ctx, self.fmt, self.seq, self.queue, self.queued = ctx, fmt, 100, collections.deque(), 0
        self.cond, self.go, self.dead = threading.Condition(), False, False

    def writer(self):
        while True:
            with self.cond:
                while not self.dead and not (self.go and self.queue):
                    self.cond.wait()
                if self.dead:
                    return
                msg, size = self.queue.popleft()
            self.ctx.send(msg)
            with self.cond:
                self.queued -= size


class World:
    def __init__(self):
        self.lock, self.attaches, self.holder = threading.RLock(), [], None
        self.size, self.before, self.alt, self.line = (80, 24), None, False, ""
        self.unheld = None      # the size before the viewers' first resize, given back when the last attach goes unheld
        self.rows, self.scroll = ["$ "], ["\x1b[32mwelcome\x1b[0m"]

    def snapshot_fields(self):
        rows = [] if self.alt else self.rows
        data = ("\x1b[?1049h" if self.alt else "") + "\r\n".join(rows) + f"\x1b[{max(len(rows), 1)};{len(rows[-1]) + 1 if rows else 1}H\x1b[?25h"
        return {"cols": self.size[0], "rows": self.size[1], "data": b64(data.encode()), "alt_screen": self.alt,
                "cursor": {"x": len(rows[-1]) if rows else 0, "y": max(len(rows) - 1, 0)}, "cursor_visible": True}

    def push(self, att, method, **params):
        """Give one notice the attach's next seq; a backlog past the limit is replaced by a snapshot, never thinned.
        marker and holder are the one method for both formats, so each says which attach it belongs to."""
        with att.cond:
            att.seq += 1
            if method in ("marker", "holder"):
                params["format"] = att.fmt
            msg = {"jsonrpc": "2.0", "method": method, "params": {"terminal": HANDLE, "seq": att.seq, **params}}
            size = len(json.dumps(msg))
            if method == "output" and att.queued + size >= BACKLOG:
                att.queue.clear()
                att.queued = 0
                att.seq += 1
                msg = {"jsonrpc": "2.0", "method": "snapshot", "params": {"terminal": HANDLE, "seq": att.seq, "reason": "slow_client", **self.snapshot_fields()}}
                size = len(json.dumps(msg))
            att.queue.append((msg, size))
            att.queued += size
            att.cond.notify()

    def tell(self, method, **params):
        """Output and snapshot notices are bytes attaches' (a text attach is told of output by a frame, below); resize is a text or
        styled attach's (a bytes attach is told by a snapshot); the rest go to every attach."""
        for att in list(self.attaches):
            if method == "resize" and att.fmt == "bytes":
                continue
            if method in ("output", "snapshot") and att.fmt != "bytes":
                if method == "output":
                    self.push(att, "frame", full=True, lines=[{"text": r} for r in self.rows], cursor=None, **({"format": "styled"} if att.fmt == "styled" else {}))
            else:
                self.push(att, method, **params)

    def holders(self):
        return [{"kind": "person", "name": "x", "since": SINCE}] if self.holder else [{"kind": "daemon", "name": "daemon", "since": None}]

    def out(self, data: bytes):
        self.tell("output", data=b64(data))

    def run(self, cmd: str):
        m = re.fullmatch(r"echo (\S+)", cmd)
        flood = re.fullmatch(r"head -c (\d+) /dev/zero \| tr '\\0' x", cmd)
        alt = re.fullmatch(r"printf '\\033\[\?1049([hl])'", cmd)
        if m:
            text = re.sub(r"\$\(\((\d+)\+(\d+)\)\)", lambda s: str(int(s[1]) + int(s[2])), m[1])
            self.out(("\r\n" + text + "\r\n$ ").encode())
            self.rows[-1:] = [f"$ {cmd}", text, "$ "]
        elif alt:
            self.alt = alt[1] == "h"
            self.out(("\r\n\x1b[?1049" + alt[1] + "$ ").encode())
        elif flood:
            self.out(b"\r\n")
            for _ in range(int(flood[1]) // CHUNK):
                self.out(b"x" * CHUNK)
        else:
            self.out(f"\r\nsh: {cmd}: command not found\r\n$ ".encode())


def world(ctx) -> World:
    return ctx.state.setdefault("tmux", World())


def attach(ctx, params):
    fmt = params.get("format", "text")
    if ctx is None or (fmt != "bytes" and params.get("agent") not in (AGENT, TEXT_ONLY if fmt == "styled" else AGENT) and params.get("terminal") != HANDLE):
        return None             # a connection with no tmux world (the fake network's) serves text only; other text terminals are static
    w = world(ctx)
    agent = params.get("agent")
    if agent == TEXT_ONLY and fmt in ("bytes", "styled"):
        return "fail", {"code": -32002, "message": "refused", "data": {"code": "refused", "gate": "unsupported", "reason": "this terminal's host serves text only"}}
    if agent not in (None, AGENT) or params.get("terminal") not in (None, HANDLE):
        return None
    n = params.get("history", 100)
    with w.lock:
        detach(w, ctx, fmt)         # one attach per terminal and format: a second one replaces the first
        att = Attach(ctx, fmt)
        w.attaches.append(att)
        lines = [] if w.alt or fmt != "bytes" else w.scroll[-n:] if n else []
        if fmt != "bytes":
            result = {"terminal": HANDLE, "format": fmt, "seq": att.seq, "cols": w.size[0], "rows": w.size[1], "lines": [{"text": r} for r in w.rows], "cursor": None,
                      "history": {"first": 0, "lines": [], "more": False}, "holder": w.holders()[0], "holders": w.holders()}
        else:
            result = {"terminal": HANDLE, "format": "bytes", "seq": att.seq, **w.snapshot_fields(), "history": {"first": len(w.scroll) - len(lines), "lines": lines, "more": False},
                      "holder": w.holders()[0], "holders": w.holders()}

    def begin():
        att.go = True
        threading.Thread(target=att.writer, daemon=True).start()
        with att.cond:
            att.cond.notify()
    ctx.after.append(begin)
    watch(w, ctx)
    return "ok", result


def watch(w, ctx):
    if not getattr(ctx, "leaves", False):
        ctx.leaves = True
        ctx.on_close.append(lambda: leave(w, ctx))


def leave(w, ctx):
    """A connection that closes stops watching and gives up the input it held."""
    detach(w, ctx)
    if w.holder == ctx.id:
        take(ctx, "input.release")
    last_left(w)


def last_left(w):
    """With no attach left and nobody holding the input, the size from before the viewers' first resize comes back."""
    with w.lock:
        if not w.attaches and w.holder is None and w.unheld:
            w.size, w.unheld = w.unheld, None


def detach(w, ctx, fmt=None):
    """End this connection's attach in `fmt`, or every attach of the terminal when none is named."""
    with w.lock:
        for att in [a for a in w.attaches if a.ctx is ctx and fmt in (None, a.fmt)]:
            w.attaches.remove(att)
            with att.cond:
                att.dead = True
                att.cond.notify()


def take(ctx, method):
    w = world(ctx)
    with w.lock:
        if method == "input.take":
            if w.holder not in (None, ctx.id):
                return "fail", REFUSED
            w.holder = ctx.id
            watch(w, ctx)
            w.tell("holder", holders=w.holders())
        elif w.holder == ctx.id:
            w.holder = None
            if w.before:
                w.size, w.before = w.before, None
                w.tell("snapshot", reason="resize", **w.snapshot_fields())
                w.tell("resize", cols=w.size[0], rows=w.size[1])
            w.tell("holder", holders=w.holders())
            last_left(w)
    return "ok", {"accepted": True, "said": "done"}


def terminal(ctx, method, params):
    """terminal.* on the one tmux handle, after the shared checks of the caller (a known handle, a well-formed call)."""
    w = world(ctx)
    with w.lock:
        if method == "terminal.detach":
            detach(w, ctx, params.get("format"))
            last_left(w)
            return "ok", {}
        if method == "terminal.history":
            if params.get("format") not in ("bytes", "styled"):
                return None
            if params["format"] == "styled":
                return "ok", {"first": 0, "lines": [], "more": False}
            lines = [] if w.alt else w.scroll[-params["limit"]:]
            return "ok", {"first": len(w.scroll) - len(lines), "lines": lines, "more": False}
        free = w.holder is None and method == "terminal.resize" and any(a.ctx is ctx for a in w.attaches)
        if w.holder != ctx.id and not free:
            return "fail", REFUSED
        if method == "terminal.resize":
            if free and (params["cols"] < 80 or params["rows"] < 24):
                return "fail", REFUSED
            if free and w.unheld is None:
                w.unheld = w.size
            elif not free and w.before is None:
                w.before = w.size
            w.size = (params["cols"], params["rows"])
            w.tell("snapshot", reason="resize", **w.snapshot_fields())
            w.tell("resize", cols=w.size[0], rows=w.size[1])
            return "ok", {"accepted": True, "said": f"terminal is {params['cols']} by {params['rows']}"}
        echo = b""
        for byte in base64.b64decode(params["data"]):      # terminal.input: typed characters are echoed, a CR runs the line, ctrl-c drops it
            if byte == 13:
                line, w.line = w.line, ""
                w.out(echo)
                echo = b""
                w.run(line)
            elif byte == 3:
                w.line = ""
                w.out(echo + b"^C\r\n$ ")
                echo = b""
            else:
                w.line += chr(byte)
                echo += bytes([byte])
        if echo:
            w.out(echo)
        return "ok", {"accepted": True, "said": "sent"}


__all__ = ["attach", "take", "terminal", "err", "AGENT", "HANDLE", "TEXT_ONLY"]
