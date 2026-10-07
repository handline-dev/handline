"""The terminal channel's bytes format on tmux (contract x-terminal-bytes): the pane's own escape-coded output, as base64 notices.

One control-mode client (`tmux -C`) is the daemon's window onto the server: its `%output` lines are the panes' output in order, and the
commands it is given are answered in the same stream. That ordering is what makes a snapshot exact: the screen is captured by a command
on that client, the reply stands at one place among the `%output` lines, and a bytes attach starts streaming from there, so no byte is
lost or repeated between the snapshot and the first notice.

An attach has one counter, `seq`, that goes up by exactly one with each notice it is sent (`output`, `snapshot`, `marker`, `holder`);
its snapshot's `seq` is the last notice the screen includes, which is 0 for a fresh attach. Output is queued per client and sent in
notices of at most 64 KiB, at most one every 50 ms. Nothing is dropped from the middle: when one client's unsent output reaches
1 MiB it is all discarded and a `snapshot` notice, reason `slow_client`, takes its place (counting bytes, not seconds)."""
from __future__ import annotations

import base64
import collections
import re
import subprocess
import threading
import time

from . import agentview

OUTPUT_EVERY_S = 0.05
OUTPUT_MAX = 64 * 1024
SLOW_BYTES = 1024 * 1024
SNAPSHOT_MAX = 1024 * 1024
COMMAND_TIMEOUT_S = 15
_SGR = re.compile(r"\x1b\[[0-9;:]*m")


def unescape_output(raw: bytes) -> bytes:
    """The bytes of a control-mode `%output` payload: tmux writes bytes below 0x20 and the backslash as `\\ooo` (octal), every other byte as it is (so a
    payload may end inside a multi-byte character, and is read as bytes, never as text)."""
    out = bytearray()
    i, n = 0, len(raw)
    while i < n:
        c = raw[i]
        if c == 0x5C and n - i >= 4 and all(0x30 <= x <= 0x37 for x in raw[i + 1:i + 4]):
            out.append(int(raw[i + 1:i + 4], 8) & 0xFF)
            i += 4
        else:
            out.append(c)
            i += 1
    return bytes(out)


class ControlLink:
    """A control-mode connection: commands out, replies and `%output` in, in the order tmux sends them. `spawn` returns the process (a test passes a
    fake)."""

    def __init__(self, spawn=None) -> None:
        self.spawn = spawn or _spawn_tmux
        self.proc = None
        self.lock = threading.Lock()
        self.pending: collections.deque = collections.deque()      # (callback, group) of the commands sent and not yet answered, in order
        self.handlers: dict[str, list] = {}                       # pane -> callables taking the bytes of one %output
        self.reader: threading.Thread | None = None
        self.alive = False

    def start(self) -> bool:
        with self.lock:
            if self.alive:
                return True
            try:
                self.proc = self.spawn()
            except OSError:
                return False
            self.alive = True
            self.reader = threading.Thread(target=self._read, daemon=True, name="ocp-tmux-control")
            self.reader.start()
            return True

    def stop(self) -> None:
        with self.lock:
            self.alive = False
            proc, self.proc = self.proc, None
        if proc is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass
            try:
                proc.terminate()
            except OSError:
                pass

    def command_async(self, command: str, callback) -> None:
        """Send `command`; `callback(lines)` is called on the reader thread, in order with every `%output`, with the reply's lines (None on an error or a
        dead link)."""
        self.batch_async([command], [callback])

    def batch_async(self, commands: list[str], callbacks: list) -> None:
        """Send `commands` as one line (`a ; b ; c`), which tmux runs without taking any output between them: what they read is the pane at one place in
        the stream. Each has its own reply, in order; when one fails tmux runs none of the rest, and each of those is answered None."""
        group = object() if len(commands) > 1 else None
        with self.lock:
            if not self.alive:
                dead = list(callbacks)
            else:
                dead = []
                self.pending.extend((cb, group) for cb in callbacks)
                try:
                    self.proc.stdin.write((" ; ".join(commands) + "\n").encode("utf-8"))
                    self.proc.stdin.flush()
                except (OSError, ValueError):
                    for _ in callbacks:
                        self.pending.pop()
                    dead = list(callbacks)
        for cb in dead:
            cb(None)

    def command(self, command: str, timeout: float = COMMAND_TIMEOUT_S) -> list[str] | None:
        got: list = []
        done = threading.Event()
        self.command_async(command, lambda lines: (got.append(lines), done.set()))
        return got[0] if done.wait(timeout) else None

    def watch(self, pane: str, handler) -> None:
        with self.lock:
            self.handlers.setdefault(pane, []).append(handler)

    def unwatch(self, pane: str, handler) -> None:
        with self.lock:
            hs = self.handlers.get(pane, [])
            if handler in hs:
                hs.remove(handler)
            if not hs:
                self.handlers.pop(pane, None)

    def _read(self) -> None:
        block: list[str] | None = None
        proc = self.proc
        try:
            for raw in proc.stdout:
                raw = raw.rstrip(b"\n")
                if block is not None:
                    if raw.startswith(b"%end ") or raw.startswith(b"%error "):
                        failed = raw.startswith(b"%error ")
                        with self.lock:
                            cb, group = self.pending.popleft() if self.pending else (None, None)
                            skipped = []
                            while failed and group is not None and self.pending and self.pending[0][1] is group:
                                skipped.append(self.pending.popleft()[0])       # tmux ran none of the rest of the line
                        lines, block = block, None
                        if cb is not None:
                            cb(None if failed else lines)
                        for skip in skipped:
                            skip(None)
                    else:
                        block.append(raw.decode("utf-8", "replace"))
                elif raw.startswith(b"%begin "):
                    block = []
                elif raw.startswith(b"%output "):
                    _, pane, data = (raw.split(b" ", 2) + [b""])[:3]
                    for h in list(self.handlers.get(pane.decode("ascii", "replace"), ())):
                        h(unescape_output(data))
        except (OSError, ValueError):
            pass
        finally:
            with self.lock:
                self.alive = False
                pending, self.pending = list(self.pending), collections.deque()
            for cb, _ in pending:
                cb(None)
            for hs in list(self.handlers.values()):
                for h in list(hs):
                    h(None)         # the link ended: each channel snapshots again or ends its clients


def _spawn_tmux():
    from ..session.backends import tmux
    return subprocess.Popen(["tmux", "-L", tmux._socket(), "-C", "attach-session", "-t", tmux.SESSION],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


LINK = ControlLink()


def link() -> ControlLink:
    return LINK


# ---------------------------------------------------------------- the snapshot
MODE_FORMAT = ("#{alternate_on} #{cursor_x} #{cursor_y} #{cursor_flag} #{pane_width} #{pane_height} #{keypad_cursor_flag} "
               "#{mouse_any_flag} #{mouse_button_flag} #{mouse_standard_flag} #{mouse_sgr_flag} #{history_size}")


def parse_modes(lines: list[str] | None) -> dict | None:
    parts = (lines[0].split() if lines else [])
    if len(parts) != 12 or not all(p.isdigit() for p in parts):
        return None
    n = [int(p) for p in parts]
    return {"alt_screen": bool(n[0]), "cursor": {"x": n[1], "y": n[2]}, "cursor_visible": bool(n[3]), "cols": n[4], "rows": n[5],
            "app_cursor": bool(n[6]), "mouse_any": bool(n[7]), "mouse_button": bool(n[8]), "mouse_standard": bool(n[9]), "mouse_sgr": bool(n[10]),
            "history_size": n[11]}


def snapshot_data(modes: dict, rows: list[str]) -> bytes:
    """The bytes that paint the screen on a freshly reset terminal of the pane's size: the alternate screen if it is on, the rows with their styling,
    the modes that change what a client sends, then the cursor. Styling is left out of a screen whose coded form passes SNAPSHOT_MAX."""
    def build(lines: list[str]) -> bytes:
        parts = ["\x1b[?1049h" if modes["alt_screen"] else "", "\r\n".join(r + "\x1b[0m" for r in lines)]
        if modes["app_cursor"]:
            parts.append("\x1b[?1h")
        if modes["mouse_any"]:
            parts.append("\x1b[?1003h")
        elif modes["mouse_button"]:
            parts.append("\x1b[?1002h")
        elif modes["mouse_standard"]:
            parts.append("\x1b[?1000h")
        if modes["mouse_sgr"]:
            parts.append("\x1b[?1006h")
        parts.append(f"\x1b[{modes['cursor']['y'] + 1};{modes['cursor']['x'] + 1}H")
        parts.append("\x1b[?25h" if modes["cursor_visible"] else "\x1b[?25l")
        return "".join(parts).encode("utf-8", "replace")
    data = build(rows)
    return data if len(data) <= SNAPSHOT_MAX else build([_SGR.sub("", r) for r in rows])


def capture(link_: ControlLink, handle: str, history: int, on_ready, on_live=None) -> None:
    """The screen of `handle` now, asked on the control link so that it stands in order among the output: `on_ready(snapshot dict or None)` runs on the
    reader thread. The modes, the screen and the scrollback are asked in one command line, so they are the pane at one place in the stream; `on_live()`
    runs at the screen's reply, in order with the output, which is where a client owed the stream starts. `history` is how many scrollback lines to
    ask for."""
    commands = [f"display-message -p -t {handle} '{MODE_FORMAT}'", f"capture-pane -p -e -t {handle}"]
    if history > 0:
        commands.append(f"capture-pane -p -e -t {handle} -S -{history} -E -1")
    got: dict = {}

    def modes_reply(lines):
        got["modes"] = parse_modes(lines)

    def screen_reply(rows):
        got["rows"] = rows
        if rows is not None and got.get("modes") is not None and on_live is not None:
            on_live()
        if len(commands) == 2:
            done(None)

    def history_reply(lines):
        done(lines)

    def done(hist):
        modes, rows = got.get("modes"), got.get("rows")
        if modes is None or rows is None:
            on_ready(None)
            return
        screen = rows[-modes["rows"]:] if len(rows) >= modes["rows"] else rows + [""] * (modes["rows"] - len(rows))
        snap = {"modes": modes, "data": snapshot_data(modes, screen)}
        total = modes["history_size"]
        n = 0 if modes["alt_screen"] else min(history, total)
        lines = [ln + "\x1b[0m" for ln in (hist or [])][-n:] if n else []
        snap["history"] = {"first": total - len(lines), "lines": lines, "more": total - len(lines) > 0 and not modes["alt_screen"]}
        on_ready(snap)
    link_.batch_async(commands, [modes_reply, screen_reply, history_reply][:len(commands)])


def history_page(link_: ControlLink, handle: str, before: int, limit: int) -> dict | None:
    """Scrollback lines [before - limit, before) by absolute index (0 is the oldest line the pane keeps), as the host keeps them, trimmed to 1 MiB."""
    # The size of the scrollback and the lines of it are read in one command line, at one place in the stream: the lines are indexed from the end, so
    # output that scrolled between two reads would shift every index. The whole scrollback is read (tmux keeps at most its history-limit lines).
    got: dict = {}
    done = threading.Event()
    link_.batch_async([f"display-message -p -t {handle} '{MODE_FORMAT}'", f"capture-pane -p -e -t {handle} -S - -E -1"],
                      [lambda lines: got.__setitem__("modes", parse_modes(lines)), lambda lines: (got.__setitem__("lines", lines), done.set())])
    if not done.wait(COMMAND_TIMEOUT_S) or got.get("modes") is None or got.get("lines") is None:
        return None
    modes, scroll = got["modes"], got["lines"]
    if modes["alt_screen"]:
        return {"first": 0, "lines": [], "more": False}
    total = modes["history_size"]
    scroll = scroll[-total:] if total else []
    end = max(min(before, total), 0)
    start = max(end - limit, 0)
    if end <= start:
        return {"first": start, "lines": [], "more": start > 0}
    lines = scroll[start:end]
    out, used = [], 0
    for ln in reversed([ln + "\x1b[0m" for ln in lines]):      # the newest of the page first: a page trimmed to size keeps what is nearest `before`
        used += len(ln.encode("utf-8", "replace"))
        if used > SNAPSHOT_MAX:
            break
        out.append(ln)
    out.reverse()
    first = end - len(out)
    return {"first": first, "lines": out, "more": first > 0}


# ---------------------------------------------------------------- clients and channels
class BytesClient:
    """One bytes attach: the queue of what it is owed, its counter, and the writer thread that turns the queue into notices."""

    def __init__(self, send, close) -> None:
        self.send, self.close = send, close
        self.cond = threading.Condition()
        self.queue: collections.deque = collections.deque()       # ("output", bytes) | ("notice", method, params) | ("snapshot", params)
        self.unsent = 0                                            # decoded bytes of output in the queue
        self.seq = 0
        self.dead = False
        self.channel: "BytesChannel | None" = None
        self.live = False               # owed the output that comes next: set at the place in the stream the snapshot was taken
        self.resyncing = False

    def start(self, channel: "BytesChannel") -> None:
        self.channel = channel
        threading.Thread(target=self._run, daemon=True, name="ocp-terminal-bytes-writer").start()

    def end(self) -> None:
        with self.cond:
            self.dead = True
            self.cond.notify()

    def put_output(self, data: bytes) -> bool:
        """Queue output; True when this client has fallen a megabyte behind and must resync."""
        with self.cond:
            if self.dead or self.resyncing or not self.live:
                return False
            if self.queue and self.queue[-1][0] == "output":
                self.queue[-1] = ("output", self.queue[-1][1] + data)
            else:
                self.queue.append(("output", data))
            self.unsent += len(data)
            self.cond.notify()
            return self.unsent >= SLOW_BYTES

    def put_notice(self, method: str, params: dict) -> None:
        with self.cond:
            if not self.dead and self.live:
                self.queue.append(("notice", method, params))
                self.cond.notify()

    def resync_with(self, params: dict) -> None:
        """Everything unsent goes, and `params` (a snapshot) takes its place; markers and holder changes stay, after it."""
        with self.cond:
            kept = [item for item in self.queue if item[0] == "notice"]
            self.queue = collections.deque([("snapshot", params), *kept])
            self.unsent = 0
            self.resyncing = False
            self.cond.notify()

    def _run(self) -> None:
        last = 0.0
        while True:
            with self.cond:
                while not self.queue and not self.dead:
                    self.cond.wait()
                if self.dead:
                    return
                item = self.queue[0]
                if item[0] == "output":
                    wait = last + OUTPUT_EVERY_S - time.monotonic()
                    if wait > 0:
                        self.cond.wait(wait)
                        continue
                    data = item[1]
                    chunk, rest = data[:OUTPUT_MAX], data[OUTPUT_MAX:]
                    if rest:
                        self.queue[0] = ("output", rest)
                    else:
                        self.queue.popleft()
                    self.unsent -= len(chunk)
                    self.seq += 1
                    msg = {"jsonrpc": "2.0", "method": "output", "params": {"terminal": self.channel.handle, "seq": self.seq,
                                                                             "data": base64.b64encode(chunk).decode("ascii")}}
                    last = time.monotonic()
                else:
                    self.queue.popleft()
                    self.seq += 1
                    method = item[1] if item[0] == "notice" else "snapshot"
                    params = item[2] if item[0] == "notice" else item[1]
                    msg = {"jsonrpc": "2.0", "method": method, "params": {"terminal": self.channel.handle, "seq": self.seq, **params}}
            try:
                self.send(msg)
            except OSError:
                self.end()
                self.channel.detach(self)
                return


def _encode(snap: dict) -> dict:
    modes = snap["modes"]
    return {"cols": modes["cols"], "rows": modes["rows"], "data": base64.b64encode(snap["data"]).decode("ascii"),
            "alt_screen": modes["alt_screen"], "cursor": modes["cursor"], "cursor_visible": modes["cursor_visible"]}


class BytesChannel:
    """The bytes attaches of one pane: it watches the pane's output on the control link while anyone is attached."""

    def __init__(self, handle: str, hub: "BytesHub", link_: ControlLink) -> None:
        self.handle, self.hub, self.link = handle, hub, link_
        self.lock = threading.RLock()
        self.clients: list[BytesClient] = []
        self.watching = False

    def _on_output(self, data: bytes | None) -> None:
        if data is None:               # the control link ended: every client attaches again
            for c in list(self.clients):
                c.close()
            return
        for c in list(self.clients):
            if c.put_output(data):
                self._resync(c, "slow_client")

    def _resync(self, client: BytesClient, reason: str) -> None:
        with client.cond:
            if client.resyncing:
                return
            client.resyncing = True
        capture(self.link, self.handle, 0, lambda snap: client.resync_with({"reason": reason, **_encode(snap)}) if snap else setattr(client, "resyncing", False))

    def attach(self, client: BytesClient, history: int) -> dict | None:
        """The snapshot for `client`, with the stream starting at the place the screen was taken: None when the host could not say."""
        got: list = []
        done = threading.Event()
        with self.lock:
            if not self.watching:
                self.link.watch(self.handle, self._on_output)
                self.watching = True
            self.clients.append(client)
            client.channel = self

        def live():
            client.live = True          # on the reader thread, in order with the output: from the screen's place the client is owed what comes next

        def ready(snap):
            got.append(snap)
            done.set()
        capture(self.link, self.handle, history, ready, live)
        if not done.wait(COMMAND_TIMEOUT_S) or got[0] is None:
            self.detach(client)
            return None
        return got[0]

    def begin(self, client: BytesClient) -> None:
        client.start(self)

    def detach(self, client: BytesClient) -> None:
        with self.lock:
            if client in self.clients:
                self.clients.remove(client)
            client.end()
            if not self.clients and self.watching:
                self.link.unwatch(self.handle, self._on_output)
                self.watching = False
        if not self.clients:
            self.hub.reap(self.handle)

    def notify(self, method: str, **params) -> None:
        with self.lock:
            for c in list(self.clients):
                c.put_notice(method, params)

    def resized(self) -> None:
        """The size changed (a holder's or the restore): every client is sent a snapshot, reason `resize`, and no `resize` notice."""
        for c in list(self.clients):
            self._resync(c, "resize")


class BytesHub:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.channels: dict[str, BytesChannel] = {}

    def channel(self, handle: str, link_: ControlLink | None = None) -> BytesChannel:
        with self.lock:
            return self.channels.setdefault(handle, BytesChannel(handle, self, link_ or LINK))

    def open_for(self, handle: str) -> BytesChannel | None:
        with self.lock:
            ch = self.channels.get(handle)
        return ch if ch is not None and ch.clients else None

    def reap(self, handle: str) -> None:
        with self.lock:
            ch = self.channels.get(handle)
            if ch is not None and not ch.clients:
                del self.channels[handle]


HUB = BytesHub()


def notify(handle: str, method: str, **params) -> None:
    """A `marker` or `holder` notice to the bytes attaches of `handle`, in each one's counter."""
    ch = HUB.open_for(handle)
    if ch is not None:
        ch.notify(method, **params)


def resized(handle: str) -> None:
    ch = HUB.open_for(handle)
    if ch is not None:
        ch.resized()
