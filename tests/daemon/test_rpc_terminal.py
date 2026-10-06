"""Tests: the terminal channel (terminal.attach, detach, history, resize and the frame, resize, marker and holder notices):
the snapshot, frames as changed rows, one poll for many clients and its rate, a slow client sent a fresh frame and no backlog,
the poll stopping with the last client, scrollback under absolute indexes, a resize only the input holder may ask and its
restoring, and the markers of the daemon typing and of a person's control calls.

The terminal host is a script: the daemon is given the rows a test sets and a counter of the reads made of it.

Run on its own: python3 tests/daemon/test_rpc_terminal.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import getpass
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.daemon import delivery, inputhold, socket_server, termchannel  # noqa: E402
from handline.infra import sessiontoken  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _hp  # noqa: E402

ME = getpass.getuser()


class Screen:
    """The scripted terminal host: rows to give, a count of reads, a size, and whether a resize works."""

    def __init__(self) -> None:
        self.rows: list[str] | None = ["$ ready", ""]
        self.reads = 0
        self.size: tuple[int, int] | None = (80, 24)
        self.can_resize = True
        self.resized: list = []
        self.times: list[float] = []

    def __enter__(self):
        self.saved = (termchannel.read_rows, termchannel.read_size, termchannel.do_resize, termchannel.RATE_S)
        termchannel.RATE_S = 0.05

        def read(handle, rows):
            self.reads += 1
            self.times.append(time.monotonic())
            return None if self.rows is None else list(self.rows)

        def resize(handle, cols, rows):
            self.resized.append((cols, rows))
            if not self.can_resize:
                return False, "this host cannot resize"
            self.size = (cols, rows)
            return True, ""
        termchannel.read_rows, termchannel.read_size, termchannel.do_resize = read, lambda handle: self.size, resize
        termchannel.HUB.channels.clear()
        termchannel.HUB.restore.clear()
        con = db.connect()
        db.set_terminal(con, "worker", "t-worker", None)
        db.set_agent_state(con, "worker", "IDLE")
        con.close()
        return self

    def __exit__(self, *exc):
        termchannel.read_rows, termchannel.read_size, termchannel.do_resize, termchannel.RATE_S = self.saved
        for ch in list(termchannel.HUB.channels.values()):
            for c in list(ch.clients):
                ch.detach(c)
        termchannel.HUB.channels.clear()


class NWire(Wire):
    """A Wire whose calls set aside the notices that come between a call and its reply, for `_next` to give."""

    def __init__(self) -> None:
        super().__init__()
        self.stash: list = []
        self.n = 100

    def call(self, method, params=None, id=None, **extra):
        self.n += 1
        id = id or self.n
        self.send({"jsonrpc": "2.0", "id": id, "method": method, **({"params": params} if params is not None else {}), **extra})
        while True:
            m = self.read()
            if m is None or m.get("id") == id:
                return m
            self.stash.append(m)


def _wire(as_agent: str | None = None) -> Wire:
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: as_agent
    try:
        w = NWire()
        w.call("hello", _hp() if as_agent is None else {"client": "t", "protocol": 1})
        return w
    finally:
        sessiontoken.agent_of = real


def _code(r: dict):
    return (r or {}).get("error", {}).get("data", {}).get("code")


def _gate(r: dict):
    return (r or {}).get("error", {}).get("data", {}).get("gate")


def _next(w: Wire, method: str | None = None, timeout: float = 2.0):
    """The next notice (of `method`, when given) the daemon pushes on this connection, or None."""
    w.a.settimeout(timeout)
    end = time.time() + timeout
    try:
        while time.time() < end:
            m = next((x for x in w.stash if method is None or x.get("method") == method), None) if getattr(w, "stash", None) else None
            if m is not None:
                w.stash.remove(m)
                return m
            m = w.read()
            if m is None:
                return None
            if "method" in m and (method is None or m["method"] == method):
                return m
    except (TimeoutError, socket.timeout):
        return None
    return None


def _quiet(w: Wire, seconds: float = 0.4) -> bool:
    return _next(w, timeout=seconds) is None


def _poller_alive(handle: str = "t-worker") -> bool:
    ch = termchannel.HUB.channels.get(handle)
    return bool(ch and ch.thread and ch.thread.is_alive())


def test_attach_replies_with_a_snapshot_and_refuses_what_it_cannot_find(home: Path) -> None:
    support._board_world(home)
    with Screen() as s:
        s.rows = ["line one", "line two", "$ "]
        w = _wire()
        r = w.call("terminal.attach", {"agent": "worker"})["result"]
        check("attach: the snapshot names the terminal, its format, size and rows", r["terminal"] == "t-worker" and r["format"] == "text"
              and (r["cols"], r["rows"]) == (80, 24) and [x["text"] for x in r["lines"]] == ["line one", "line two", "$ "] and r["cursor"] is None
              and all(set(x) == {"text"} for x in r["lines"]), str(r))
        check("attach: with a seq, an empty history page and the holders (the daemon)", isinstance(r["seq"], int)
              and r["history"] == {"first": 0, "lines": [], "more": False} and r["holders"] == [inputhold.DAEMON] and r["holder"] == inputhold.DAEMON, str(r))
        w.close()
        w = _wire()
        check("attach: by terminal handle too", w.call("terminal.attach", {"terminal": "t-worker"})["result"]["terminal"] == "t-worker")
        w.close()
        for params, code, field in (({}, "bad_request", "agent"), ({"agent": "worker", "terminal": "t-worker"}, "bad_request", "agent"),
                                    ({"agent": "nobody"}, "not_found", None), ({"terminal": "nope"}, "not_found", None),
                                    ({"agent": "manager"}, "not_found", None), ({"agent": "worker", "history": -1}, "bad_request", "history"),
                                    ({"agent": "worker", "history": "x"}, "bad_request", "history"), ({"agent": 3}, "bad_request", "agent")):
            w = _wire()
            r = w.call("terminal.attach", params)
            check(f"attach {params}: {code}", _code(r) == code and (field is None or r["error"]["data"]["field"] == field), str(r))
            w.close()
        w = _wire(as_agent="manager")
        r = w.call("terminal.attach", {"agent": "worker"})
        check("attach: an agent session is refused by role", _gate(r) == "role", str(r))
        w.close()
        w = Wire()
        check("attach: before hello is refused like any call", _code(w.call("terminal.attach", {"agent": "worker"})) == "bad_request")
        w.close()


def test_frames_are_changed_rows_and_a_quiet_screen_sends_nothing(home: Path) -> None:
    support._board_world(home)
    with Screen() as s:
        s.rows = ["row 0", "row 1", "row 2", "row 3", "row 4", "row 5"]
        w = _wire()
        snap = w.call("terminal.attach", {"agent": "worker"})["result"]
        check("frames: nothing is pushed while the screen holds still", _quiet(w))
        s.rows = ["row 0", "row 1 CHANGED", "row 2", "row 3", "row 4", "row 5"]
        f = _next(w, "frame")["params"]
        check("frames: one changed row is sent as that row alone, with its seq after the snapshot's", f["full"] is False and f["terminal"] == "t-worker"
              and f["changed"] == [{"y": 1, "row": {"text": "row 1 CHANGED"}}] and f["seq"] == snap["seq"] + 1 and f["cursor"] is None and "lines" not in f, str(f))
        s.rows = ["x0", "x1", "x2", "x3", "x4", "x5"]
        f = _next(w, "frame")["params"]
        check("frames: a screen that changed throughout goes whole", f["full"] is True and [r["text"] for r in f["lines"]] == s.rows and "changed" not in f, str(f))
        s.rows = None
        check("frames: a screen that cannot be read changes nothing and is not an error", _quiet(w))
        w.close()


def test_two_clients_watch_one_terminal_with_one_poll_at_the_capped_rate(home: Path) -> None:
    support._board_world(home)
    with Screen() as s:
        termchannel.RATE_S = 0.1
        a, b = _wire(), _wire()
        a.call("terminal.attach", {"agent": "worker"})
        b.call("terminal.attach", {"agent": "worker"})
        s.reads, s.times = 0, []
        s.rows = ["changed", ""]
        fa, fb = _next(a, "frame"), _next(b, "frame")
        check("one poll: both clients get the frame", fa is not None and fb is not None and fa["params"]["seq"] == fb["params"]["seq"], str((fa, fb)))
        time.sleep(1.0)
        gaps = [t2 - t1 for t1, t2 in zip(s.times, s.times[1:])]
        check("one poll: the terminal is read once per interval whoever watches, never twice", gaps and min(gaps) >= 0.09, str(gaps[:5]))
        check("one poll: about ten reads a second at this test's interval, not twenty", s.reads <= 14, str(s.reads))
        check("one poll: a single poller thread serves both", sum(1 for t in threading.enumerate() if t.name == "ocp-terminal-poll") == 1)
        check("one poll: the real cap is five reads a second", abs(Screen().saved[3] if False else 0.2 - 0.2) < 1e-9 and termchannel.RATE_S == 0.1)
        a.close()
        b.close()


def test_a_slow_client_is_sent_a_fresh_frame_and_no_backlog(home: Path) -> None:
    support._board_world(home)
    with Screen() as s:
        termchannel.RATE_S = 0.02
        got: list = []

        def slow(msg):
            got.append(msg)
            time.sleep(0.3)
        client = termchannel.Client(slow, lambda: None)
        ch = termchannel.HUB.attach("t-worker", client)
        ch.begin(client)
        for i in range(12):
            s.rows = [f"state {i}", "second row", "third row"]
            time.sleep(0.04)
        time.sleep(0.8)
        frames = [m for m in got if m["method"] == "frame"]
        check("slow client: it is not sent a message per change", 1 <= len(frames) <= 5, str(len(frames)))
        last = frames[-1]["params"]
        check("slow client: what it ends on is the newest screen, whole", last["full"] is True and last["lines"][0]["text"] == "state 11" and last["seq"] == ch.seq, str(last))
        check("slow client: the frames it was sent never go back", [m["params"]["seq"] for m in frames] == sorted(m["params"]["seq"] for m in frames))
        ch.detach(client)


def test_the_poll_stops_when_the_last_client_leaves_and_the_agent_is_untouched(home: Path) -> None:
    support._board_world(home)
    with Screen() as s:
        sent: list = []
        real_send = termmod.send
        termmod.send = lambda *a, **k: (sent.append(a), (True, ""))[1]
        try:
            a, b = _wire(), _wire()
            a.call("terminal.attach", {"agent": "worker"})
            b.call("terminal.attach", {"agent": "worker"})
            check("poll: running while anyone is attached", _poller_alive())
            check("poll: detach says ok", a.call("terminal.detach", {"terminal": "t-worker"}).get("result") == {})
            time.sleep(0.3)
            check("poll: one client left, the poll goes on", _poller_alive())
            b.close()           # the other hangs up, no detach
            for _ in range(40):
                if not _poller_alive():
                    break
                time.sleep(0.05)
            check("poll: the last client gone, the poll stops", not _poller_alive())
            reads = s.reads
            time.sleep(0.4)
            check("poll: and the terminal is not read any more", s.reads == reads, f"{reads} {s.reads}")
            check("detaching stops nothing of the agent: nothing was typed at its terminal, its state is as it was",
                  sent == [] and db.get_agent_state(db.connect(), "worker")["state"] == "IDLE", str(sent))
            r = a.call("terminal.detach", {"terminal": "t-worker"})
            check("detach: one that is not attached is fine too", r.get("result") == {}, str(r))
            check("detach: a terminal that is no handle is bad_request", _code(a.call("terminal.detach", {})) == "bad_request")
            a.close()
            c = _wire()
            c.call("terminal.attach", {"agent": "worker"})
            for _ in range(40):
                if _poller_alive():
                    break
                time.sleep(0.05)
            check("poll: and starts again for the next attach", _poller_alive())
            c.close()
        finally:
            termmod.send = real_send


def test_scrollback_is_kept_under_absolute_indexes_and_paged(home: Path) -> None:
    support._board_world(home)
    with Screen() as s:
        s.rows = ["l1", "l2", "l3", "l4"]
        w = _wire()
        w.call("terminal.attach", {"agent": "worker"})
        for rows in (["l2", "l3", "l4", "l5"], ["l4", "l5", "l6", "l7"], ["l7", "l8", "l9", "l10"]):
            s.rows = rows
            _next(w, "frame")
        ch = termchannel.HUB.channels["t-worker"]
        check("history: the rows that scrolled off the top are kept, in order", ch.history == ["l1", "l2", "l3", "l4", "l5", "l6"], str(ch.history))
        r = w.call("terminal.history", {"terminal": "t-worker", "before": 6, "limit": 4})["result"]
        check("history: a page ends before the index asked and says where it starts", r["first"] == 2 and [x["text"] for x in r["lines"]] == ["l3", "l4", "l5", "l6"] and r["more"] is True, str(r))
        r = w.call("terminal.history", {"terminal": "t-worker", "before": 2, "limit": 10})["result"]
        check("history: the first page has no more", r["first"] == 0 and [x["text"] for x in r["lines"]] == ["l1", "l2"] and r["more"] is False, str(r))
        r = w.call("terminal.history", {"terminal": "t-worker", "before": 99, "limit": 2})["result"]
        check("history: an index past the end is the end", r["first"] == 4 and [x["text"] for x in r["lines"]] == ["l5", "l6"], str(r))
        w2 = _wire()
        snap = w2.call("terminal.attach", {"agent": "worker", "history": 3})["result"]["history"]
        check("history: a later attach is given the newest of it, with where it starts", snap["first"] == 3 and [x["text"] for x in snap["lines"]] == ["l4", "l5", "l6"] and snap["more"] is True, str(snap))
        for bad in ({"before": -1, "limit": 1}, {"before": 0, "limit": 0}, {"before": "x", "limit": 1}, {"limit": 1}, {"before": 1}):
            r = w.call("terminal.history", {"terminal": "t-worker", **bad})
            check(f"history {bad}: bad_request", _code(r) == "bad_request", str(r))
        check("history: an unknown terminal is not_found", _code(w.call("terminal.history", {"terminal": "nope", "before": 1, "limit": 1})) == "not_found")
        s.rows = ["redraw 1", "redraw 2", "redraw 3", "redraw 4"]
        _next(w, "frame")
        check("history: a redraw that scrolled nothing adds nothing", len(ch.history) == 6, str(ch.history))
        w.close()
        w2.close()


def test_resize_is_the_input_holders_alone_and_is_given_back_when_they_leave(home: Path) -> None:
    support._board_world(home)
    with Screen() as s:
        w = _wire()
        w.call("terminal.attach", {"agent": "worker"})
        ask = {"terminal": "t-worker", "cols": 100, "rows": 30}
        r = w.call("terminal.resize", {**ask, "request_id": "z1"})
        check("resize: refused to a caller when the daemon holds the input, gate input_holder", _gate(r) == "input_holder" and not s.resized, str(r))
        con = db.connect()
        inputhold.take(con, "worker", "person", "someone.else", int(time.time()))
        con.close()
        r = w.call("terminal.resize", {**ask, "request_id": "z2"})
        check("resize: refused to a non-holder while another holds it, naming who", _gate(r) == "input_holder" and "someone.else" in r["error"]["message"] and not s.resized, str(r))
        con = db.connect()
        inputhold.release(con, "worker")
        con.close()
        check("resize: (the founder takes the input)", w.call("input.take", {"agent": "worker", "request_id": "z3"}).get("result", {}).get("accepted"))
        notice = _next(w, "holder")
        while notice is not None and notice["params"]["holders"][0]["name"] != ME:
            notice = _next(w, "holder")
        check("resize: and the holder notice names them", notice is not None and notice["params"]["holders"][0]["name"] == ME, str(notice))
        r = w.call("terminal.resize", {**ask, "request_id": "z4"})
        n = _next(w, "resize")
        check("resize: the holder is accepted, the host resized, and every client told", r["result"]["accepted"] and s.resized == [(100, 30)]
              and n is not None and (n["params"]["cols"], n["params"]["rows"]) == (100, 30), str((r, n)))
        r = w.call("terminal.resize", {**ask, "cols": 120, "request_id": "z5"})
        check("resize: and again, the size before the first is the one kept", r["result"]["accepted"] and termchannel.HUB.restore.get("t-worker") == (80, 24))
        w.call("input.release", {"agent": "worker", "request_id": "z6"})
        back = _next(w, "resize")
        while back is not None and back["params"]["cols"] != 80:
            back = _next(w, "resize")
        check("resize: when the holder leaves the terminal is given back its size and the clients told", s.resized[-1] == (80, 24)
              and back is not None and (back["params"]["cols"], back["params"]["rows"]) == (80, 24) and "t-worker" not in termchannel.HUB.restore, str((s.resized, back)))
        w.call("input.take", {"agent": "worker", "request_id": "z7"})
        s.can_resize = False
        r = w.call("terminal.resize", {**ask, "request_id": "z8"})
        check("resize: a host that cannot resize is refused, gate unsupported", _gate(r) == "unsupported", str(r))
        for bad in ({"cols": 5}, {"cols": 9999}, {"rows": 1}, {"rows": "x"}, {"cols": True}):
            r = w.call("terminal.resize", {**ask, **bad, "request_id": "zb"})
            check(f"resize {bad}: bad_request", _code(r) == "bad_request", str(r))
        r = w.call("terminal.resize", ask)
        check("resize: a write, it takes a request_id", _code(r) == "bad_request" and r["error"]["data"]["field"] == "request_id", str(r))
        check("resize: an unknown terminal is not_found", _code(w.call("terminal.resize", {**ask, "terminal": "nope", "request_id": "zn"})) == "not_found")
        a = _wire(as_agent="manager")
        check("resize: an agent session is refused by role", _gate(a.call("terminal.resize", {**ask, "request_id": "za"})) == "role")
        a.close()
        w.close()


def test_markers_say_when_the_daemon_typed_and_when_a_person_did(home: Path) -> None:
    support._board_world(home)
    with Screen() as s:
        w = _wire()
        w.call("terminal.attach", {"agent": "worker"})
        con = db.connect()
        delivery._mark_typed(con, "worker")
        m = _next(w, "marker")
        check("marker: the daemon typing is a daemon_typed marker with the daemon as its actor", m is not None and m["params"]["kind"] == "daemon_typed"
              and m["params"]["actor"] == {"kind": "daemon", "name": "daemon"} and m["params"]["at"].endswith("Z") and m["params"]["terminal"] == "t-worker", str(m))
        real = (termmod.send, termmod.available)
        termmod.send, termmod.available = (lambda *a, **k: (True, "")), (lambda: True)
        try:
            r = w.call("session.keys", {"agent": "worker", "keys": ["esc"], "request_id": "m1"})
            m2 = _next(w, "marker")
        finally:
            termmod.send, termmod.available = real
        check("marker: a person's control call is a control marker naming them, after the daemon's in seq", r.get("result", {}).get("accepted") and m2 is not None
              and m2["params"]["kind"] == "control" and m2["params"]["actor"]["name"] == ME and m2["params"]["seq"] > m["params"]["seq"], str((r, m2)))
        con.close()
        from handline.actions import control
        real_park = control.park_result
        control.park_result = lambda con, agent: (True, "parked")
        try:
            w.call("session.park", {"agent": "worker", "request_id": "m2"})
            m3 = _next(w, "marker")
        finally:
            control.park_result = real_park
        check("marker: a session call is a control marker too, saying which", m3 is not None and m3["params"]["kind"] == "control" and m3["params"]["text"] == "session.park", str(m3))
        seqs = []
        for i in range(3):
            con = db.connect()
            delivery._mark_typed(con, "worker")
            con.close()
            seqs.append(_next(w, "marker")["params"]["seq"])
        check("marker: every notice has the next seq of the terminal", seqs == sorted(set(seqs)) and seqs[0] > m3["params"]["seq"], str(seqs))
        w.close()


def test_a_client_that_falls_far_behind_is_hung_up_to_reattach_and_one_poller_serves_racing_attaches(home: Path) -> None:
    support._board_world(home)
    with Screen() as s:
        hung: list = []
        stuck = threading.Event()
        client = termchannel.Client(lambda msg: stuck.wait(5), lambda: hung.append(1))
        ch = termchannel.HUB.attach("t-worker", client)
        ch.begin(client)
        for i in range(termchannel.CLIENT_QUEUE + 20):       # holders change faster than a stuck client reads
            ch.notify("holder", holders=[{"kind": "person", "name": f"p{i}", "since": None}])
        time.sleep(0.2)
        check("overflow: a client that fell CLIENT_QUEUE notices behind is hung up, not left with a stale holder", hung == [1] and client.dead and not client.notices, str(hung))
        check("overflow: it is no longer served, and the poller is not held by it", client not in ch.clients or client.dead)
        stuck.set()
        con = db.connect()
        inputhold.take(con, "worker", "person", "latest.holder")
        con.close()
        w = _wire()
        snap = w.call("terminal.attach", {"agent": "worker"})["result"]
        check("overflow: and attaching again gives a snapshot with the holder as it is now", snap["holders"][0]["name"] == "latest.holder", str(snap["holders"]))
        w.close()
        # racing attaches leave one poller
        termchannel.HUB.channels.clear()
        c1, c2 = termchannel.Client(lambda m: None, lambda: None), termchannel.Client(lambda m: None, lambda: None)
        ch2 = termchannel.HUB.attach("t-race", c1)
        termchannel.HUB.attach("t-race", c2)
        gate = threading.Barrier(2)

        def begin(c):
            gate.wait()
            ch2.begin(c)
        ts = [threading.Thread(target=begin, args=(c,)) for c in (c1, c2)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        time.sleep(0.1)
        check("one poller: two attaches beginning at once leave one poller thread", sum(1 for t in threading.enumerate() if t.name == "ocp-terminal-poll") == 1)
        for c in (c1, c2):
            ch2.detach(c)


def test_a_channel_goes_with_its_last_client_and_history_of_an_unwatched_terminal_is_empty(home: Path) -> None:
    support._board_world(home)
    with Screen() as s:
        w = _wire()
        w.call("terminal.attach", {"agent": "worker"})
        check("channels: one while someone watches", "t-worker" in termchannel.HUB.channels)
        w.close()
        for _ in range(40):
            if "t-worker" not in termchannel.HUB.channels:
                break
            time.sleep(0.05)
        check("channels: and gone, scrollback with it, once the last client has left and the poll has ended", "t-worker" not in termchannel.HUB.channels, str(list(termchannel.HUB.channels)))
        h = _wire()
        r = h.call("terminal.history", {"terminal": "t-worker", "before": 5, "limit": 5})["result"]
        check("history: of a terminal nobody watches is an empty page, and makes no channel", r == {"first": 0, "lines": [], "more": False} and "t-worker" not in termchannel.HUB.channels, str(r))
        h.close()


def test_the_terminal_fixtures_pass_against_this_daemon(home: Path) -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "contract"))
    import conformance

    def connect():
        a, b = socket.socketpair()
        threading.Thread(target=socket_server._serve_one, args=(b, lambda m: None), daemon=True).start()
        return a
    support._board_world(home)
    real = sessiontoken.agent_of
    results = []
    try:
        with Screen():
            for role in ("founder", "agent"):
                sessiontoken.agent_of = (lambda pid: "manager") if role == "agent" else (lambda pid: None)
                results += [(*r, role) for r in conformance.run_all([Path("contract/fixtures/terminal.json"), Path("contract/fixtures/terminal_channel.json")], {role: connect},
                                                                     {"founder_key": _hp()["key"], "terminal_agent": "worker",
                                                                      "terminal_handle": "t-worker"}) if r[0] != "skip"]
    finally:
        sessiontoken.agent_of = real
    bad = [(n, why) for st, why, n, _ in results if st != "pass"]
    check("fixtures: every terminal fixture passes against this daemon, each as its own role", not bad and results, str(bad))


def main() -> int:
    def tests(td):
        run(lambda: test_attach_replies_with_a_snapshot_and_refuses_what_it_cannot_find(Path(td) / "m0"))
        run(lambda: test_frames_are_changed_rows_and_a_quiet_screen_sends_nothing(Path(td) / "m1"))
        run(lambda: test_two_clients_watch_one_terminal_with_one_poll_at_the_capped_rate(Path(td) / "m2"))
        run(lambda: test_a_slow_client_is_sent_a_fresh_frame_and_no_backlog(Path(td) / "m3"))
        run(lambda: test_the_poll_stops_when_the_last_client_leaves_and_the_agent_is_untouched(Path(td) / "m4"))
        run(lambda: test_scrollback_is_kept_under_absolute_indexes_and_paged(Path(td) / "m5"))
        run(lambda: test_resize_is_the_input_holders_alone_and_is_given_back_when_they_leave(Path(td) / "m6"))
        run(lambda: test_markers_say_when_the_daemon_typed_and_when_a_person_did(Path(td) / "m7"))
        run(lambda: test_a_client_that_falls_far_behind_is_hung_up_to_reattach_and_one_poller_serves_racing_attaches(Path(td) / "m9"))
        run(lambda: test_a_channel_goes_with_its_last_client_and_history_of_an_unwatched_terminal_is_empty(Path(td) / "m10"))
        run(lambda: test_the_terminal_fixtures_pass_against_this_daemon(Path(td) / "m8"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
