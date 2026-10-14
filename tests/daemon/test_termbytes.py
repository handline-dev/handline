"""Tests: the terminal channel's bytes format on tmux (daemon/termbytes.py, terminal.input, the tmux backend's send_bytes): the unescaping of
`%output`, the control link that matches replies to commands among the output, the seam between a snapshot and the stream, the pacing of notices,
a slow client resynced alone, input from the holder alone, a resize as a snapshot, detach, and the capabilities by host.

The tmux server is a script: a fake control-mode process the daemon reads `%output` and replies from and writes commands to, and a fake terminal host
that records the bytes it is sent. Nothing here starts tmux.

Run on its own: python3 tests/daemon/test_termbytes.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import base64
import queue
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.daemon import inputhold, termbytes, termchannel  # noqa: E402
from handline.infra import lock  # noqa: E402
from handline.session import backends  # noqa: E402
from handline.session.backends import tmux  # noqa: E402
from handline.session.backends.base import Backend  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

MODES = "0 3 1 1 80 24 0 0 0 0 0 0"        # alternate_on cursor_x cursor_y cursor_flag cols rows ... history_size
_EOF = object()


def esc(data: bytes) -> bytes:
    """What tmux writes for `data` in an `%output` line: bytes below 0x20 and the backslash as octal."""
    return b"".join(b"\\%03o" % b if b < 0x20 or b == 0x5C else bytes([b]) for b in data)


class _In:
    def __init__(self, proc) -> None:
        self.proc = proc

    def write(self, raw: bytes) -> None:
        if self.proc.dead:
            raise BrokenPipeError()
        self.proc.commands.append(raw.decode().rstrip("\n"))
        self.proc.on_command(self.proc.commands[-1])

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.proc.die()


class FakeProc:
    """A control-mode process: `answer` puts a reply into its stdout, `output` a `%output`, `die` ends it. With `auto` every command is answered at once
    (modes, a one-row screen); a command whose prefix is in `hold` is left for the test to answer."""

    def __init__(self, auto: bool = True, hold: tuple[str, ...] = ()) -> None:
        self.q: queue.Queue = queue.Queue()
        self.commands: list[str] = []
        self.dead = False
        self.auto, self.hold = auto, hold
        self.stdin, self.stdout = _In(self), iter(self.q.get, _EOF)
        self.screen = ["$ ready"]
        self.modes = MODES
        self.scroll: list[str] = []

    def on_command(self, cmd: str) -> None:
        """A line is one or more commands joined by ` ; `, each with its own reply, in order."""
        if self.auto and not cmd.startswith(self.hold):
            for sub in cmd.split(" ; "):
                self.answer(self.reply_for(sub))

    def reply_for(self, cmd: str) -> list[str]:
        if cmd.startswith("display-message"):
            return [self.modes]
        return list(self.scroll) if " -S " in cmd else list(self.screen)

    def answer(self, lines: list[str] | None) -> None:
        if lines is None:
            self.q.put(b"%begin 1 1 0\n%error 1 1 0\n")
            return
        self.q.put(("%begin 1 1 0\n" + "".join(ln + "\n" for ln in lines) + "%end 1 1 0\n").encode())

    def output(self, pane: str, data: bytes) -> None:
        self.q.put(b"%output " + pane.encode() + b" " + esc(data) + b"\n")

    def die(self) -> None:
        self.dead = True
        self.q.put(_EOF)

    def terminate(self) -> None:
        self.die()


def _lines(proc: FakeProc):
    """The process's stdout as lines, which the queue holds as blocks of them."""
    for blk in iter(proc.q.get, _EOF):
        yield from blk.splitlines(keepends=True)


def fake_proc(**kw) -> FakeProc:
    p = FakeProc(**kw)
    p.stdout = _lines(p)
    return p


def new_link(**kw) -> tuple[termbytes.ControlLink, FakeProc]:
    p = fake_proc(**kw)
    link = termbytes.ControlLink(spawn=lambda: p)
    return link, p


def test_tmux_greets_with_an_empty_reply_that_no_command_asked_for(home: Path) -> None:
    p = fake_proc()
    p.q.put(b"%begin 1 1 0\n%end 1 1 0\n")        # what tmux -C writes as it attaches
    link = termbytes.ControlLink(spawn=lambda: p, greeted=True)
    check("link: it starts", link.start() is True)
    got = link.command("display-message -p x")
    check("link: the first command gets its own reply, not the greeting's empty one", got == [MODES], str(got))
    link.stop()


def _until(pred, secs: float = 5.0) -> bool:
    end = time.monotonic() + secs
    while not pred():
        if time.monotonic() > end:
            return False
        time.sleep(0.005)
    return True


class Sink:
    """What a bytes client is sent: its notices with when each arrived; `gate` holds the writer inside a send to make a slow client."""

    def __init__(self, gate: threading.Event | None = None) -> None:
        self.msgs: list[dict] = []
        self.at: list[float] = []
        self.gate = gate
        self.closed = False

    def send(self, msg: dict) -> None:
        if self.gate is not None:
            self.gate.wait(20)
        self.msgs.append(msg)
        self.at.append(time.monotonic())

    def close(self) -> None:
        self.closed = True

    def data(self) -> bytes:
        return b"".join(base64.b64decode(m["params"]["data"]) for m in self.msgs if m["method"] == "output")

    def seqs(self) -> list[int]:
        return [m["params"]["seq"] for m in self.msgs]


def _channel(link, handle: str = "%1") -> termbytes.BytesChannel:
    return termbytes.BytesChannel(handle, termbytes.BytesHub(), link)


def _attached(ch: termbytes.BytesChannel, sink: Sink) -> termbytes.BytesClient:
    c = termbytes.BytesClient(sink.send, sink.close)
    ch.link.start()
    assert ch.attach(c, 0) is not None
    ch.begin(c)
    return c


# ---------------------------------------------------------------- (1) unescape_output
def test_output_is_unescaped_as_bytes() -> None:
    u = termbytes.unescape_output
    check("unescape: octal escapes are the bytes they name", u(b"a\\033[1mb\\015\\012") == b"a\x1b[1mb\r\n")
    check("unescape: an escaped backslash is one backslash", u(b"x\\134y") == b"x\\y")
    check("unescape: a backslash not followed by three octal digits stays", u(b"\\12") == b"\\12" and u(b"a\\") == b"a\\" and u(b"\\8ab") == b"\\8ab"
          and u(b"\\0a1") == b"\\0a1")
    check("unescape: only three digits are taken", u(b"\\0339") == b"\x1b9")
    check("unescape: an escape that names a byte past 0xff wraps to one byte, never an error", len(u(b"\\777")) == 1)
    half = "€".encode()[:2]
    check("unescape: a payload ending inside a multi-byte character is returned as it is", u(half) == half and u(b"ok" + half) == b"ok" + half)
    check("unescape: and the next payload completes it", (u(half) + u("€".encode()[2:])).decode() == "€")
    check("unescape: empty", u(b"") == b"")


# ---------------------------------------------------------------- (2) ControlLink
def test_the_control_link_matches_replies_to_commands_in_order_among_output() -> None:
    link, p = new_link(auto=False)
    got: list = []
    pane: list[bytes | None] = []
    link.watch("%1", pane.append)
    check("link: it starts", link.start() and link.alive)
    link.command_async("one", lambda lines: got.append(("one", lines)))
    link.command_async("two", lambda lines: got.append(("two", lines)))
    link.command_async("three", lambda lines: got.append(("three", lines)))
    p.output("%1", b"before")
    p.answer(["a", "b"])
    p.output("%1", b"\x1b[0m")
    p.q.put(b"%begin 1 2 0\nbad\n%error 1 2 0\n")
    p.output("%2", b"another pane")
    p.answer([])
    check("link: replies are matched to the commands in the order they were sent, an %error as None, an empty reply as []",
          _until(lambda: len(got) == 3) and got == [("one", ["a", "b"]), ("two", None), ("three", [])], str(got))
    check("link: %output reaches the handler of its pane only, as bytes, in order", _until(lambda: len(pane) == 2) and pane == [b"before", b"\x1b[0m"], str(pane))
    check("link: the commands were written one to a line", p.commands == ["one", "two", "three"], str(p.commands))
    link.stop()


def test_a_command_waits_for_its_reply_and_a_dead_link_releases_everything() -> None:
    link, p = new_link(auto=False)
    ended: list = []
    link.watch("%1", ended.append)
    link.start()
    waiting: list = []
    threading.Thread(target=lambda: waiting.append(link.command("slow")), daemon=True).start()
    _until(lambda: p.commands == ["slow"])
    p.die()
    check("link: the process dying ends the link and releases the waiter with None", _until(lambda: waiting == [None]) and not link.alive, str(waiting))
    check("link: and tells each watcher with None", ended == [None], str(ended))
    got: list = []
    link.command_async("after", got.append)
    check("link: a command on a dead link is answered None at once", got == [None])
    link2, p2 = new_link(auto=False)
    link2.start()
    p2.dead = True
    got = []
    link2.command_async("x", got.append)
    check("link: a write to a broken pipe is answered None and not left pending", got == [None] and not link2.pending, str(got))

    def broken():
        raise OSError("no tmux")
    check("link: a process that cannot be started is a False start", termbytes.ControlLink(spawn=broken).start() is False)


def _failing():
    p = fake_proc(auto=False)
    p.on_command = lambda cmd: p.answer(None)
    return p


# ---------------------------------------------------------------- (3) the seam
def _attach_in_thread(ch, c, history):
    out: list = []
    t = threading.Thread(target=lambda: out.append(ch.attach(c, history)), daemon=True)
    t.start()
    return t, out


def test_output_before_the_screen_is_in_it_and_output_after_is_the_first_notice() -> None:
    link, p = new_link(auto=True, hold=("display-message",))
    p.modes = "0 3 1 1 80 24 0 0 0 0 0 50"
    p.scroll = [f"old {n}" for n in range(50)]
    ch = _channel(link)
    sink = Sink()
    c = termbytes.BytesClient(sink.send, sink.close)
    link.start()
    t, snap = _attach_in_thread(ch, c, 10)
    check("seam: the modes, the screen and the scrollback are asked in one command line, so they stand at one place", _until(lambda: len(p.commands) == 1)
          and p.commands[0].count(" ; ") == 2 and p.commands[0].startswith("display-message") and "capture-pane" in p.commands[0], str(p.commands))
    p.output("%1", b"BEFORE")                  # in the stream ahead of the replies: the screen holds it already
    p.answer([p.modes])
    p.answer(["$ screen"])
    p.output("%1", b"BETWEEN")                 # after the screen's reply and before the scrollback's: owed to the stream, once
    p.answer(p.scroll[-10:])
    p.output("%1", b"AFTER")
    t.join(5)
    check("seam: the attach returns a snapshot with the screen, the cursor of the modes and the last 10 scrollback lines",
          bool(snap) and snap[0] is not None and snap[0]["data"].startswith(b"$ screen") and snap[0]["modes"]["cursor"] == {"x": 3, "y": 1}
          and [x.removesuffix("\x1b[0m") for x in snap[0]["history"]["lines"]] == p.scroll[-10:] and snap[0]["history"]["first"] == 40, str(snap))
    check("seam: the snapshot is seq 0 and nothing was sent yet", c.seq == 0 and not sink.msgs)
    ch.begin(c)
    p.output("%1", b"-1")
    p.output("%1", b"-2")
    check("seam: what came after the screen is sent once, in order, what came before is not", _until(lambda: sink.data() == b"BETWEENAFTER-1-2"), str(sink.data()))
    check("seam: seq goes up by one for each notice", sink.seqs() == list(range(1, len(sink.msgs) + 1)), str(sink.seqs()))
    ch.detach(c)
    link.stop()


def test_a_history_page_is_read_at_one_place_by_absolute_index() -> None:
    link, p = new_link()
    p.modes = "0 0 0 1 80 24 0 0 0 0 0 50"
    p.scroll = [f"old {n}" for n in range(50)]
    link.start()
    page = termbytes.history_page(link, "%1", 30, 10)
    check("history: lines [before - limit, before) by absolute index, from one command line", page is not None and page["first"] == 20 and page["more"] is True
          and [x.removesuffix("\x1b[0m") for x in page["lines"]] == [f"old {n}" for n in range(20, 30)] and len(p.commands) == 1 and " ; " in p.commands[0], str((page, p.commands)))
    page = termbytes.history_page(link, "%1", 500, 100)
    check("history: a page past the end is cut to the scrollback", page["first"] == 0 and len(page["lines"]) == 50 and page["more"] is False, str(page))
    link.stop()


def test_a_failed_command_of_a_line_skips_the_rest_and_the_next_reply_is_still_the_next_command_s() -> None:
    link, p = new_link(auto=False)
    link.start()
    got: list = []
    link.batch_async(["a", "b", "c"], [lambda x: got.append(("a", x)), lambda x: got.append(("b", x)), lambda x: got.append(("c", x))])
    link.command_async("next", lambda x: got.append(("next", x)))
    p.answer(None)              # a failed: tmux runs neither b nor c, and gives them no reply
    p.answer(["fine"])
    check("link: when the first command of a line fails the rest are answered None and the next line's reply is its own",
          _until(lambda: len(got) == 4) and got == [("a", None), ("b", None), ("c", None), ("next", ["fine"])], str(got))
    link.stop()


def test_a_pane_that_cannot_be_read_gives_no_snapshot_and_no_stream() -> None:
    link, p = new_link(auto=False)
    link.start()
    got: list = []
    link_live: list = []
    termbytes.capture(link, "%1", 10, got.append, lambda: link_live.append(1))
    p.answer(None)
    check("seam: a failed first command is no snapshot, and the client is never owed the stream", _until(lambda: got == [None]) and not link_live, str((got, link_live)))
    link.stop()


def test_a_screen_the_host_cannot_read_leaves_no_client_behind() -> None:
    link, p = new_link(auto=False)
    link.start()
    ch = _channel(link)
    c = termbytes.BytesClient(Sink().send, Sink().close)
    res: list = []
    t = threading.Thread(target=lambda: res.append(ch.attach(c, 0)), daemon=True)
    t.start()
    _until(lambda: p.commands)
    p.answer(None)                  # display-message failed: the pane is not there
    t.join(5)
    check("seam: an attach the host cannot answer is None, the client is gone and the pane no longer watched",
          res == [None] and not ch.clients and not ch.watching and not link.handlers, str((res, ch.clients)))
    link.stop()


# ---------------------------------------------------------------- (4) pacing
def test_notices_are_at_most_64_kib_and_at_most_one_every_50_ms() -> None:
    link, p = new_link()
    ch = _channel(link)
    sink = Sink()
    c = _attached(ch, sink)
    big = bytes(range(32, 127)) * 3000              # 285000 bytes, in one piece
    ch.link.handlers["%1"][0](big)
    check("pacing: all of it arrives", _until(lambda: len(sink.data()) == len(big), 10) and sink.data() == big, str(len(sink.data())))
    sizes = [len(base64.b64decode(m["params"]["data"])) for m in sink.msgs]
    check("pacing: no notice carries more than 64 KiB, and 285000 bytes are five notices", max(sizes) <= 64 * 1024 and len(sizes) == 5, str(sizes))
    gaps = [b - a for a, b in zip(sink.at, sink.at[1:])]
    check("pacing: no two notices are closer than about 50 ms", all(g >= 0.04 for g in gaps), str(gaps))
    check("pacing: and the counter is continuous", sink.seqs() == [1, 2, 3, 4, 5], str(sink.seqs()))
    ch.detach(c)
    link.stop()


# ---------------------------------------------------------------- (5) a slow client
def test_a_client_a_megabyte_behind_is_resynced_alone() -> None:
    link, p = new_link()
    ch = _channel(link)
    gate = threading.Event()
    slow, fast = Sink(gate), Sink()
    cs = _attached(ch, slow)
    cf = _attached(ch, fast)
    chunk = b"x" * (64 * 1024)
    sent = 0
    for _ in range(18):                 # 1.1 MiB, at about the rate the fast client is drained
        p.output("%1", chunk)
        sent += len(chunk)
        time.sleep(0.06)
    check("slow: the client over a megabyte behind has its backlog replaced by one snapshot, reason slow_client",
          _until(lambda: any(c.queue and c.queue[0][0] == "snapshot" for c in (cs,)) or cs.unsent == 0, 5) and cs.unsent < 1024 * 1024)
    gate.set()
    check("slow: it is sent that snapshot", _until(lambda: any(m["method"] == "snapshot" for m in slow.msgs)), str(slow.seqs()))
    snaps = [m for m in slow.msgs if m["method"] == "snapshot"]
    check("slow: exactly one, with the reason, the screen and a size", len(snaps) == 1 and snaps[0]["params"]["reason"] == "slow_client"
          and snaps[0]["params"]["cols"] == 80 and base64.b64decode(snaps[0]["params"]["data"]).startswith(b"$ ready"), str(snaps))
    check("slow: its counter has no gap", slow.seqs() == list(range(1, len(slow.msgs) + 1)), str(slow.seqs()))
    check("slow: and it was spared what it could not take", len(slow.data()) < sent, f"{len(slow.data())} of {sent}")
    check("slow: the other client on the pane gets all of it and no snapshot", _until(lambda: len(fast.data()) == sent, 10)
          and not any(m["method"] == "snapshot" for m in fast.msgs) and fast.seqs() == list(range(1, len(fast.msgs) + 1)), str(len(fast.data())))
    ch.detach(cs)
    ch.detach(cf)
    link.stop()


# ---------------------------------------------------------------- the host and the world
class _FakeHost(Backend):
    """A terminal host that serves bytes and records what it is sent."""
    name = "tmux"
    serves_bytes = True

    def __init__(self) -> None:
        self.sent: list = []
        self.ok = True

    def available(self) -> bool:
        return True

    def send_bytes(self, handle, data):
        self.sent.append((handle, data))
        return (True, "") if self.ok else (False, "no such pane")


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


# ---------------------------------------------------------------- (8) the tmux backend
def test_tmux_send_bytes_sends_hex_in_chunks_to_a_pane_only() -> None:
    calls: list = []
    real = tmux._tmux
    code = [0]

    def fake(args, *a, **kw):
        calls.append(args)
        return code[0], "", "boom"
    tmux._tmux = fake
    try:
        b = tmux.TmuxBackend()
        data = bytes(range(256)) + b"\x00\xff" * 150            # 556 bytes
        ok, why = b.send_bytes("%12", data)
        check("send_bytes: 556 bytes go in three send-keys, 256 + 256 + 44", ok and [len(c) - 4 for c in calls] == [256, 256, 44], str([len(c) for c in calls]))
        check("send_bytes: each is `send-keys -t <pane> -H` and a two-digit hex number per byte", all(c[:4] == ["send-keys", "-t", "%12", "-H"] for c in calls)
              and calls[0][4:7] == ["00", "01", "02"] and calls[0][-1] == "ff" and calls[2][-1] == "ff" and all(len(x) == 2 for c in calls for x in c[4:]), str(calls[0][:8]))
        calls.clear()
        for bad in ("worker", "t-worker", "%1; kill-server", "%", "", "%1 ", "$1", None):
            ok, why = b.send_bytes(bad, b"x")
            check(f"send_bytes: {bad!r} is not a pane and is refused, nothing run", not ok and not calls and why, str((ok, why)))
        code[0] = 1
        ok, why = b.send_bytes("%3", b"x" * 600)
        check("send_bytes: a tmux that fails stops at the first chunk and says why", not ok and why == "boom" and len(calls) == 1, str((ok, why, len(calls))))
        check("send_bytes: the host serves bytes, and the base host does not", tmux.TmuxBackend.serves_bytes and not Backend.serves_bytes
              and not Backend.send_bytes(Backend.__new__(Backend), "%1", b"x")[0])
    finally:
        tmux._tmux = real


def main() -> int:
    def tests(td):
        run(lambda: test_output_is_unescaped_as_bytes())
        run(lambda: test_tmux_greets_with_an_empty_reply_that_no_command_asked_for(Path(td) / "greet"))
        run(lambda: test_the_control_link_matches_replies_to_commands_in_order_among_output())
        run(lambda: test_a_command_waits_for_its_reply_and_a_dead_link_releases_everything())
        run(lambda: test_output_before_the_screen_is_in_it_and_output_after_is_the_first_notice())
        run(lambda: test_a_history_page_is_read_at_one_place_by_absolute_index())
        run(lambda: test_a_failed_command_of_a_line_skips_the_rest_and_the_next_reply_is_still_the_next_command_s())
        run(lambda: test_a_pane_that_cannot_be_read_gives_no_snapshot_and_no_stream())
        run(lambda: test_a_screen_the_host_cannot_read_leaves_no_client_behind())
        run(lambda: test_notices_are_at_most_64_kib_and_at_most_one_every_50_ms())
        run(lambda: test_a_client_a_megabyte_behind_is_resynced_alone())
        run(lambda: test_tmux_send_bytes_sends_hex_in_chunks_to_a_pane_only())
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
