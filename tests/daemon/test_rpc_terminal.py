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
from handline.daemon import delivery, inputhold, termchannel  # noqa: E402
from handline.infra import sessiontoken  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

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
        termchannel.HUB.styled.clear()
        termchannel.HUB.restore.clear()
        con = db.connect()
        db.set_terminal(con, "worker", "t-worker", None)
        db.set_agent_state(con, "worker", "IDLE")
        con.close()
        return self

    def __exit__(self, *exc):
        termchannel.read_rows, termchannel.read_size, termchannel.do_resize, termchannel.RATE_S = self.saved
        for ch in [*termchannel.HUB.channels.values(), *termchannel.HUB.styled.values()]:
            for c in list(ch.clients):
                ch.detach(c)
        termchannel.HUB.channels.clear()
        termchannel.HUB.styled.clear()


def _code(r: dict):
    return (r or {}).get("error", {}).get("data", {}).get("code")


def _gate(r: dict):
    return (r or {}).get("error", {}).get("data", {}).get("gate")


def _poller_alive(handle: str = "t-worker") -> bool:
    ch = termchannel.HUB.channels.get(handle)
    return bool(ch and ch.thread and ch.thread.is_alive())


def _until(pred, secs: float = 30.0) -> bool:
    """Wait on a condition, not for a time: the limit only ends a test that would never finish."""
    end = time.monotonic() + secs
    while not pred():
        if time.monotonic() > end:
            return False
        time.sleep(0.005)
    return True


def test_a_slow_client_is_sent_a_fresh_frame_and_no_backlog(home: Path) -> None:
    support._board_world(home)
    with Screen() as s:
        termchannel.RATE_S = 0.02
        got: list = []
        gate = threading.Event()

        def slow(msg):          # a client that takes nothing more until the gate opens
            got.append(msg)
            gate.wait(30)
        client = termchannel.Client(slow, lambda: None)
        ch = termchannel.HUB.attach("t-worker", client)
        ch.begin(client)
        _until(lambda: got)
        for i in range(12):
            s.rows = [f"state {i}", "second row", "third row"]
            seen = s.reads
            _until(lambda: s.reads >= seen + 2)     # the poller has read this screen, whatever the machine's load
        gate.set()
        _until(lambda: any(m["method"] == "frame" and m["params"]["lines"][0]["text"] == "state 11" for m in got))
        frames = [m for m in got if m["method"] == "frame"]
        check("slow client: it is not sent a message per change", 1 <= len(frames) <= 5, str(len(frames)))
        last = frames[-1]["params"]
        check("slow client: what it ends on is the newest screen, whole", last["full"] is True and last["lines"][0]["text"] == "state 11" and last["seq"] == ch.seq, str(last))
        check("slow client: the frames it was sent never go back", [m["params"]["seq"] for m in frames] == sorted(m["params"]["seq"] for m in frames))
        ch.detach(client)


def main() -> int:
    def tests(td):
        run(lambda: test_a_slow_client_is_sent_a_fresh_frame_and_no_backlog(Path(td) / "m3"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
