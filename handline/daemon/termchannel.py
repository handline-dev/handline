"""The terminal channel: one stream per terminal, served by the daemon, so the IDE and the web UI read no terminal of their own.

A `Channel` is one terminal while someone is attached. One poller thread reads its frame at most `RATE_S` apart however many
clients watch, and ends when the last one leaves; detaching never touches the agent. A frame is text rows (the backends
give text; `Row.spans` is set only on a `styled` channel, whose rows come from the host with their SGR codes). Each client has a writer thread of its own and a bounded queue of the notices that
must not be lost (markers, holders, resizes) beside one flag for the frame: a client that falls behind on frames is sent the
newest frame whole, never a backlog, and one that falls `CLIENT_QUEUE` notices behind is hung up to attach again, since no
frame can make those good. Rows that scroll off the top are kept (`HISTORY_MAX`) under absolute line indexes, found by
matching the old frame's tail against the new one's head; a redraw that scrolls nothing adds nothing.

`seq` counts every notice of the terminal (frame, resize, marker, holder), so a client can tell what it missed."""
from __future__ import annotations

import collections
import threading
import time
import unicodedata

from . import agentview

RATE_S = 0.2                    # at most ~5 reads a second per terminal
ROWS = 50                       # the rows of a frame when the host does not say how many the terminal has
HISTORY_MAX = 5000
CLIENT_QUEUE = 200
FULL_ABOVE = 0.5                # a frame that changes more than this share of its rows goes whole

# What the host gives, replaceable by a test: rows of the terminal's bottom `rows` lines (None: unreadable), its size,
# and a resize.
def read_rows(handle: str, rows: int) -> list[str] | None:
    from ..session import backends
    term = backends.current().read_screen(handle, rows)
    tail = term.get("tail") if isinstance(term, dict) else None
    return [str(r) for r in tail] if isinstance(tail, list) and term.get("source") == "screen" else None


def read_styled(handle: str, rows: int) -> list[dict] | None:
    """The same rows as read_rows with their style runs: `{"text", "spans"}`, spans left out of a row that has none."""
    from ..infra import ansi
    from ..session import backends
    raw = backends.current().read_screen_ansi(handle, rows)
    if raw is None:
        return None
    out = []
    for r in raw:
        text, spans = ansi.parse(r)
        out.append({"text": text, **({"spans": spans} if spans else {})})
    return out


def read_cursor(handle: str) -> dict | None:
    from ..session import backends
    return backends.current().cursor(handle)


def read_size(handle: str) -> tuple[int, int] | None:
    from ..session import backends
    return backends.current().size(handle)


def do_resize(handle: str, cols: int, rows: int) -> tuple[bool, str]:
    from ..session import backends
    return backends.current().resize(handle, cols, rows)


def _row(line) -> dict:
    return line if isinstance(line, dict) else {"text": line}


def _code_points(text: str, cells: int) -> int:
    """How many code points of `text` the first `cells` terminal cells hold (a wide character is two cells, a combining one none); cells past
    the end of the text are blanks, one code point each."""
    used = i = 0
    while i < len(text) and used < cells:
        used += _width(text[i])
        i += 1
        while i < len(text) and _width(text[i]) == 0:
            i += 1
    return i + max(cells - used, 0)


def _width(ch: str) -> int:
    return 0 if unicodedata.combining(ch) else 2 if unicodedata.east_asian_width(ch) in "WF" else 1


def _text(line) -> str:
    return line["text"] if isinstance(line, dict) else line


class Client:
    """One attached connection: a writer thread draining its notices, and the newest frame it still owes."""

    def __init__(self, send, close) -> None:
        self.send, self.close = send, close
        self.cond = threading.Condition()
        self.notices: collections.deque = collections.deque()
        self.frame_owed = False
        self.sent_seq = 0                   # the frame seq this client last got
        self.dead = False
        self.channel: "Channel | None" = None

    def start(self, channel: "Channel") -> None:
        self.channel = channel
        threading.Thread(target=self._run, daemon=True, name="ocp-terminal-writer").start()

    def notice(self, method: str, params: dict) -> None:
        with self.cond:
            if self.dead:
                return
            if len(self.notices) >= CLIENT_QUEUE:
                # A frame carries neither the holders nor the size, so what was dropped cannot be made good by one: the client
                # is hung up and attaches again for a snapshot, as a slow subscriber is.
                self.notices.clear()
                self.dead = True
                self.cond.notify()
                threading.Thread(target=self._drop, daemon=True).start()
                return
            self.notices.append({"jsonrpc": "2.0", "method": method, "params": params})
            self.cond.notify()

    def _drop(self) -> None:
        """Hang the connection up and take this client off its channel, so a dead one does not keep the poll alive."""
        self.close()
        if self.channel is not None:
            self.channel.detach(self)

    def owe_frame(self) -> None:
        with self.cond:
            self.frame_owed = True
            self.cond.notify()

    def end(self) -> None:
        with self.cond:
            self.dead = True
            self.cond.notify()

    def _run(self) -> None:
        while True:
            with self.cond:
                while not (self.notices or self.frame_owed or self.dead):
                    self.cond.wait()
                if self.dead:
                    return
                msg = self.notices.popleft() if self.notices else None
                if msg is None:
                    self.frame_owed = False
            try:
                self.send(msg if msg is not None else self.channel.frame_for(self))
            except OSError:
                self.end()
                self.channel.detach(self)
                return


class Channel:
    def __init__(self, handle: str, hub: "Hub", fmt: str = "text") -> None:
        self.handle, self.hub, self.fmt = handle, hub, fmt
        self.lock = threading.RLock()
        self.clients: list[Client] = []
        self.seq = 0
        self.cur: dict | None = None        # the host's cursor as read; `cursor` gives it in the frame's own rows
        self.lines: list = []               # str on a text channel, a {"text", "spans"} row on a styled one
        self.prev: list = []           # the frame before the newest, for the one diff a client one step behind is owed
        self.changed: list[int] = []
        self.cols, self.rows = 80, ROWS
        self.history: list = []
        self.history_first = 0              # the absolute index of history[0]
        self.thread: threading.Thread | None = None
        self.reads = 0

    # -- the poller
    def _start_poller(self) -> None:
        with self.lock:         # two attaches at once must leave one poller
            if self.thread is None or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._poll, daemon=True, name="ocp-terminal-poll")
                self.thread.start()

    def _poll(self) -> None:
        while True:
            started = time.monotonic()
            with self.lock:
                if not self.clients:
                    self.thread = None
                    break
            self.reads += 1
            try:
                rows = self._read()
            except Exception:  # noqa: BLE001 - a host that breaks is a frame not read, not a dead poller
                rows = None
            if rows is not None:
                self._offer(rows, self._read_cursor())
            wait = RATE_S - (time.monotonic() - started)
            if wait > 0:
                time.sleep(wait)
        self.hub.reap(self.handle, self.fmt)      # nobody watches: the channel and its scrollback are dropped

    def _read(self) -> list | None:
        return (read_styled if self.fmt == "styled" else read_rows)(self.handle, self.rows)

    def _read_cursor(self) -> dict | None:
        try:
            return read_cursor(self.handle)
        except Exception:  # noqa: BLE001 - no cursor is an answer, as for a host that does not say
            return None

    @property
    def cursor(self) -> dict | None:
        """`{x, y}` of the cursor in the frame's own rows, x counted in code points of the row's text as spans are (the host's cells: a wide
        character is two). None when the host does not say, the cursor is hidden, or it is above the frame. y may be len(lines) or more: the
        cursor on a blank row the frame drops."""
        c, n = self.cur, len(self.lines)
        if not c or not c["visible"]:
            return None
        y = c["y"] - (c["height"] - 1 - c["blank_below"]) + (n - 1)
        return {"x": _code_points(_text(self.lines[y]) if 0 <= y < n else "", c["x"]), "y": y} if y >= 0 else None

    def _offer(self, rows: list, cur: dict | None = None) -> None:
        with self.lock:
            if rows == self.lines:
                before = self.cursor
                self.cur = cur
                if self.cursor != before:   # the cursor moved over the same rows: a frame with nothing changed, only the cursor
                    self.prev, self.changed = self.lines, []
                    self.seq += 1
                    for c in list(self.clients):
                        c.owe_frame()
                return
            self.cur = cur
            self._scrolled(self.lines, rows)
            self.prev, self.lines = self.lines, rows
            n = max(len(rows), len(self.prev))
            self.changed = [y for y in range(n) if (rows[y] if y < len(rows) else None) != (self.prev[y] if y < len(self.prev) else None)]
            self.seq += 1
            self.cols = max([self.cols, *[len(_text(r)) for r in rows]])
            for c in list(self.clients):
                c.owe_frame()

    def _scrolled(self, old: list, new: list) -> None:
        """Keep the rows that left the top: the smallest k such that the old frame from row k on is the new frame's start. A
        heuristic: a screen that happens to redraw with the old tail as its head is read as a scroll."""
        if not old or not new:
            return
        for k in range(1, len(old)):
            tail = old[k:]
            if new[:len(tail)] == tail and any(_text(r).strip() for r in old[:k]):
                self.history.extend(old[:k])
                if len(self.history) > HISTORY_MAX:
                    drop = len(self.history) - HISTORY_MAX
                    del self.history[:drop]
                    self.history_first += drop
                return

    # -- what a client is sent
    def frame_for(self, client: Client) -> dict:
        with self.lock:
            incremental = client.sent_seq == self.seq - 1 and self.seq > 1 and len(self.changed) <= FULL_ABOVE * max(len(self.lines), 1)
            client.sent_seq = self.seq
            params = {"terminal": self.handle, "seq": self.seq, "full": not incremental, "cursor": self.cursor,
                      **({"format": "styled"} if self.fmt == "styled" else {})}
            if incremental:
                params["changed"] = [{"y": y, "row": _row(self.lines[y] if y < len(self.lines) else "")} for y in self.changed]
            else:
                params["lines"] = [_row(r) for r in self.lines]
            return {"jsonrpc": "2.0", "method": "frame", "params": params}

    def snapshot(self, history: int, holders: list[dict]) -> dict:
        with self.lock:
            lines = self.history[-history:] if history > 0 else []
            first = self.history_first + len(self.history) - len(lines)
            return {"terminal": self.handle, "format": self.fmt, "seq": self.seq, "cols": self.cols, "rows": self.rows,
                    "lines": [_row(r) for r in self.lines], "cursor": self.cursor,
                    "history": {"first": first, "lines": [_row(r) for r in lines], "more": first > self.history_first},
                    "holder": holders[0], "holders": holders}

    def history_page(self, before: int, limit: int) -> dict:
        with self.lock:
            end = max(min(before, self.history_first + len(self.history)), self.history_first)
            start = max(end - limit, self.history_first)
            lines = self.history[start - self.history_first:end - self.history_first]
            return {"first": start, "lines": [_row(r) for r in lines], "more": start > self.history_first}

    # -- clients
    def attach(self, client: Client) -> None:
        """`client` is on `clients` already (`Hub.attach`); read the first frame when it is the only one."""
        with self.lock:
            first = len(self.clients) == 1
            client.sent_seq = self.seq
        if first or not self.lines:
            size = read_size(self.handle)
            if size:
                self.cols, self.rows = size
            rows = self._read()
            if rows is not None:
                self._offer(rows, self._read_cursor())
        with self.lock:
            client.sent_seq = self.seq         # the snapshot already carries it
            client.frame_owed = False

    def begin(self, client: Client) -> None:
        """Once the reply to `terminal.attach` is out: start the writer and the poller."""
        client.start(self)
        self._start_poller()

    def detach(self, client: Client) -> None:
        with self.lock:
            if client in self.clients:
                self.clients.remove(client)
            client.end()

    def notify(self, method: str, **params) -> int:
        """A notice to every client, in the terminal's own sequence; returns its seq."""
        with self.lock:
            self.seq += 1
            for c in list(self.clients):
                c.notice(method, {"terminal": self.handle, "seq": self.seq, **params})
            return self.seq


class Hub:
    """The open channels by terminal handle, text ones in `channels` and styled ones in `styled`. A channel lives while someone is
    attached and goes with its poller (`reap`)."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.channels: dict[str, Channel] = {}
        self.styled: dict[str, Channel] = {}
        self.restore: dict[str, tuple[int, int]] = {}      # handle -> the size before a resize, put back when its holder leaves

    def _store(self, fmt: str) -> dict[str, Channel]:
        return self.styled if fmt == "styled" else self.channels

    def channel(self, handle: str, fmt: str = "text") -> Channel:
        with self.lock:
            return self._store(fmt).setdefault(handle, Channel(handle, self, fmt))

    def open_for(self, handle: str, fmt: str = "text") -> Channel | None:
        with self.lock:
            ch = self._store(fmt).get(handle)
        if ch is None:
            return None
        with ch.lock:
            return ch if ch.clients else None

    def open_all(self, handle: str) -> list[Channel]:
        """The text and the styled channel of `handle`, those with someone attached."""
        return [ch for fmt in ("text", "styled") if (ch := self.open_for(handle, fmt)) is not None]

    def attach(self, handle: str, client: Client, fmt: str = "text") -> Channel:
        """The channel of `handle` in `fmt` with `client` on it, made and joined under the hub's lock so `reap` cannot drop it between."""
        with self.lock:
            ch = self._store(fmt).setdefault(handle, Channel(handle, self, fmt))
            with ch.lock:
                ch.clients.append(client)
        ch.attach(client)
        return ch

    def reap(self, handle: str, fmt: str = "text") -> None:
        with self.lock:
            store = self._store(fmt)
            ch = store.get(handle)
            if ch is not None:
                with ch.lock:
                    if not ch.clients and ch.thread is None:
                        del store[handle]


HUB = Hub()


def marker(con, agent: str, kind: str, actor: dict, text: str = "") -> None:
    """Tell whoever watches `agent`'s terminal that the daemon typed (`daemon_typed`) or a control call happened (`control`)."""
    from .. import db
    handle = db.get_terminal(con, agent)
    for ch in HUB.open_all(handle) if handle else []:
        ch.notify("marker", at=agentview._ts(time.time()), kind=kind, actor=actor, format=ch.fmt, **({"text": text} if text else {}))
    if handle:
        from . import termbytes
        termbytes.notify(handle, "marker", at=agentview._ts(time.time()), kind=kind, actor=actor, **({"text": text} if text else {}))


def holders_changed(con, agent: str, holders: list[dict]) -> None:
    """`holder` to every client of `agent`'s terminal; a size taken for a holder that left is given back. This runs wherever the
    hold is found to have changed, an idle expiry noticed by a gate check included, so a host resize can run from a read path."""
    from .. import db
    handle = db.get_terminal(con, agent)
    if not handle:
        return
    from . import termbytes
    open_channels = HUB.open_all(handle)
    for ch in open_channels:
        ch.notify("holder", holders=holders, format=ch.fmt)
    termbytes.notify(handle, "holder", holders=holders)
    if holders[0]["kind"] == "daemon" and handle in HUB.restore:
        cols, rows = HUB.restore.pop(handle)
        ok, _ = do_resize(handle, cols, rows)
        if ok:
            for ch in open_channels:
                ch.rows, ch.cols = rows, cols
                ch.notify("resize", cols=cols, rows=rows)
        if ok:
            termbytes.resized(handle)
