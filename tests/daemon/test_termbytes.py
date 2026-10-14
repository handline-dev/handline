"""Tests: the tmux backend's send_bytes: hex in chunks, to a pane only.
Run on its own: python3 tests/daemon/test_termbytes.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import queue
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
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


def _failing():
    p = fake_proc(auto=False)
    p.on_command = lambda cmd: p.answer(None)
    return p


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
        run(lambda: test_tmux_send_bytes_sends_hex_in_chunks_to_a_pane_only())
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
