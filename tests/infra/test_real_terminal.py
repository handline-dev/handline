"""Tests: the terminal channel on a real daemon and a real tmux, through the Go gateway: attach in the styled and bytes formats,
the colour a program painted, keys in, a resize, detach. The world is tools/gateway_smoke.py's scratch home (a private tmux
server, a fake `claude`, a gateway on a free port); nothing touches the real home or port 7443.

Needs tmux, git and a Go toolchain: without them the tests are skipped.

Run on its own: tools/test.sh tests/infra/test_real_terminal.py   (all areas: tools/suite.sh)
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "tools"))
import gateway_smoke as gs  # noqa: E402

AGENT = "worker"
# What a shell prints for this: "A", a red "red", "B".
PAINT = b"printf 'A\\033[31mred\\033[0mB\\n'\n"
THE: "Real"


class ColouredWorld(gs.World):
    """The scratch world whose `claude` paints its banner green."""
    FAKE_CLAUDE = '#!/bin/sh\nprintf "\\033[32mfake claude ready\\033[0m\\n"\nwhile IFS= read -r line; do echo "got: $line"; done\n'


def _texts(lines: list) -> list[str]:
    return [(x["text"] if isinstance(x, dict) else x) for x in lines]


def _have_tools() -> str | None:
    for tool in ("tmux", "git", "go"):
        if not shutil.which(tool):
            return f"{tool} is not installed"
    # The scratch world's daemons bind socket files in a temp directory; a sandboxed process may not.
    try:
        with tempfile.TemporaryDirectory() as d, socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
            probe.bind(os.path.join(d, "s"))
    except OSError as e:
        return f"this process cannot bind a unix socket ({e})"
    return None


class Real:
    """The scratch world up, a paired connection, and the worker's pane at a shell prompt."""

    def __init__(self, make_world=lambda: gs.World(False, None), leave_program: bool = False) -> None:
        self.ws = None
        os.environ["HANDLINE_TEST_REAL_TMUX"] = "1"      # the suite's stand-in tmux would refuse every call
        self.world = make_world()
        try:
            self.world.setup()
            self.world.start_daemon()
            self.world.start_gateway()
            self.token, _device = gs.pair(self.world, self.world.founder_key())
            self.ws = gs.connect("127.0.0.1", self.world.port, self.token)
            # The daemon starts the worker by itself (it has a task waiting) in the fake claude. Ending that leaves a shell
            # at its prompt, which takes whatever a test types, as the contract's terminal_bytes fixtures do.
            self.wait_screen(lambda rows: any("ready" in t for t in rows))
            if not leave_program:
                self.type(b"\x04")
                self.wait_screen(lambda rows: bool(rows) and rows[-1].rstrip().endswith("$"))
        except BaseException:      # SystemExit included: the world's daemons, tmux server and scratch home must not outlive a failed start
            self.close()
            raise

    def call(self, method: str, params: dict | None = None) -> dict:
        p = dict(params or {})
        if method in ("input.take", "input.release", "terminal.resize", "terminal.input"):
            p["request_id"] = secrets.token_hex(6)
        return self.ws.call(method, p)

    def screen(self) -> list[str]:
        """The nonblank rows of the worker's screen, read by a text attach."""
        r = self.call("terminal.attach", {"agent": AGENT, "format": "text"}).get("result") or {}
        if r.get("terminal"):
            self.call("terminal.detach", {"terminal": r["terminal"], "format": "text"})
        return [t for t in _texts(r.get("lines") or []) if t.strip()]

    def wait_screen(self, pred, seconds: float = 25.0) -> None:
        end = time.time() + seconds
        while time.time() < end:
            if pred(self.screen()):
                return
            time.sleep(0.3)
        raise AssertionError(f"the worker's terminal never got there; it shows {self.screen()}")

    def type(self, data: bytes) -> None:
        """Keys into the worker's pane as a phone or the web app sends them: take the input, terminal.input, release."""
        assert "result" in self.call("input.take", {"agent": AGENT})
        r = self.call("terminal.attach", {"agent": AGENT, "format": "text"}).get("result") or {}
        sent = self.call("terminal.input", {"terminal": r["terminal"], "data": base64.b64encode(data).decode()})
        assert "result" in sent, sent
        self.call("terminal.detach", {"terminal": r["terminal"], "format": "text"})
        self.call("input.release", {"agent": AGENT})

    def pane_size(self) -> tuple[int, int]:
        out = subprocess.run(["tmux", "-L", self.world.env["OCP_TMUX_SOCKET"], "list-panes", "-a", "-F", "#{pane_width}x#{pane_height}"],
                             capture_output=True, text=True, env=self.world.env).stdout.split()
        w, h = out[0].split("x")
        return int(w), int(h)

    def close(self) -> None:
        try:
            if self.ws:
                self.ws.close()
        finally:
            self.world.stop()
            os.environ.pop("HANDLINE_TEST_REAL_TMUX", None)


def test_styled_attach_shows_the_colour_the_program_painted() -> None:
    r = THE
    r.type(PAINT)
    r.wait_screen(lambda rows: "AredB" in rows)
    snap = r.call("terminal.attach", {"agent": AGENT, "format": "styled"})
    res = snap.get("result") or {}
    check("styled attach is served on tmux", res.get("format") == "styled", str(snap)[:300])
    rows = [x for x in res.get("lines", []) if isinstance(x, dict) and x.get("text") == "AredB"]
    check("the painted row is on the screen", len(rows) == 1, str(res.get("lines"))[-300:])
    spans = rows[0].get("spans") if rows else []
    check("only the word red has a run, and it is red", [(s["from"], s["to"]) for s in spans] == [(1, 4)] and spans[0]["style"].get("fg") in (1, "1"), str(spans))
    r.call("terminal.detach", {"terminal": res.get("terminal"), "format": "styled"})


def test_keys_in_come_back_as_a_styled_frame() -> None:
    r = THE
    snap = (r.call("terminal.attach", {"agent": AGENT, "format": "styled"})).get("result") or {}
    handle = snap["terminal"]
    since = time.time()
    check("input.take", "result" in r.call("input.take", {"agent": AGENT}), "")
    typed = r.call("terminal.input", {"terminal": handle, "data": base64.b64encode(b"printf 'C\\033[32mgreen\\033[0m\\n'\n").decode()})
    check("terminal.input accepted", "result" in typed, str(typed)[:300])
    got = r.ws.wait_note(since, lambda m: m.get("method") == "frame" and "Cgreen" in str(m.get("params")), 15)
    check("a frame carries the printed row", got is not None, str([m.get("method") for m in r.ws.notes_since(since)]))
    if got:
        p = got["params"]
        check("the frame is styled", p.get("format") == "styled", str(p)[:300])
        row = next((x for x in p.get("changed") or p.get("lines") or [] if x.get("text") == "Cgreen"), {})
        check("the word green has a green run", any(s["style"].get("fg") in (2, "2") and (s["from"], s["to"]) == (1, 6) for s in row.get("spans", [])), str(p)[:600])
    r.call("input.release", {"agent": AGENT})
    r.call("terminal.detach", {"terminal": handle, "format": "styled"})


def test_bytes_attach_snapshot_and_output() -> None:
    r = THE
    snap = (r.call("terminal.attach", {"agent": AGENT, "format": "bytes"})).get("result") or {}
    data = base64.b64decode(snap.get("data", ""))
    check("the snapshot carries the red escape", b"\x1b[31m" in data or b"\x1b[38;5;1m" in data, repr(data[:200]))
    handle = snap["terminal"]
    since = time.time()
    r.call("input.take", {"agent": AGENT})
    r.call("terminal.input", {"terminal": handle, "data": base64.b64encode(b"echo bytes-line\n").decode()})
    out = r.ws.wait_note(since, lambda m: m.get("method") == "output" and b"bytes-line" in base64.b64decode(m["params"].get("data", "")), 15)
    check("an output notice carries the echo", out is not None, "")
    r.call("input.release", {"agent": AGENT})
    r.call("terminal.detach", {"terminal": handle, "format": "bytes"})


def test_resize_by_the_holder_changes_the_pane_and_repaints() -> None:
    r = THE
    snap = (r.call("terminal.attach", {"agent": AGENT, "format": "styled"})).get("result") or {}
    handle = snap["terminal"]
    check("resize without the input is refused", "error" in r.call("terminal.resize", {"terminal": handle, "cols": 90, "rows": 30}), "")
    r.call("input.take", {"agent": AGENT})
    since = time.time()
    done = r.call("terminal.resize", {"terminal": handle, "cols": 90, "rows": 30})
    check("the holder's resize is accepted", "result" in done, str(done)[:300])
    note = r.ws.wait_note(since, lambda m: (m.get("params") or {}).get("cols") == 90, 10)
    check("a notice announces the new size", note is not None, str([m.get("method") for m in r.ws.notes_since(since)]))
    check("the tmux pane is 90x30", r.pane_size() == (90, 30), str(r.pane_size()))
    bad = r.call("terminal.resize", {"terminal": handle, "cols": 5, "rows": 30})
    check("cols 5 is refused naming the field", (bad.get("error") or {}).get("data", {}).get("field") == "cols", str(bad)[:300])
    r.call("input.release", {"agent": AGENT})
    r.call("terminal.detach", {"terminal": handle, "format": "styled"})


def test_the_shared_client_drives_the_terminal_end_to_end() -> None:
    """The client the web app and the phone share (DaemonClient, TerminalView): attach, frames, scrollback, resize."""
    r = THE
    node = shutil.which("node")
    if not node or not (Path(__file__).resolve().parents[2] / "packages" / "client-node" / "node_modules" / "@handline" / "client").exists():
        skip(test_the_shared_client_drives_the_terminal_end_to_end, "node or the workspace packages (pnpm install) are missing")
        return
    env = {**r.world.env, "HANDLINE_URL": f"ws://127.0.0.1:{r.world.port}/rpc", "HANDLINE_TOKEN": r.token, "HANDLINE_AGENT": AGENT}
    ran = subprocess.run([node, str(Path(__file__).resolve().parents[2] / "packages" / "client-node" / "test" / "drive-real-terminal.ts")],
                         env=env, capture_output=True, text=True, timeout=120, cwd=str(Path(__file__).resolve().parents[2] / "packages" / "client-node"))
    try:
        seen = json.loads(ran.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        seen = {"error": f"no result: {ran.stdout[-300:]} {ran.stderr[-600:]}"}
    check("the client ran to the end", seen.get("ok") is True, str(seen.get("error")))
    check("the client attached in styled", seen.get("format") == "styled", str(seen.get("formats")))
    painted = seen.get("afterTyping") or []
    check("a frame put the painted row in the view with its run", bool(painted) and painted[-1].get("spans") == [{"from": 4, "to": 9, "style": {"fg": 2}}], str(painted))
    h = seen.get("history") or {}
    check("older scrollback loads", h.get("count", 0) > 0, str(h))
    check("the view follows the resize", seen.get("resized") == [100, 30, 30], str(seen.get("resized")))
    again = seen.get("afterResize") or []
    check("the painted row keeps its colour after the resize", bool(again) and bool(again[-1].get("spans")), str(again))


def test_the_pane_the_daemon_launches_runs_the_fake_claude_the_test_put_in_place() -> None:
    r = Real(lambda: ColouredWorld(False, None), leave_program=True)
    try:
        res = r.call("terminal.attach", {"agent": AGENT, "format": "styled"}).get("result") or {}
        rows = [x for x in res.get("lines", []) if isinstance(x, dict) and x.get("text") == "fake claude ready"]
        check("the banner of the test's fake is on the pane", len(rows) == 1, str(res.get("lines"))[-300:])
        spans = rows[0].get("spans") if rows else []
        check("the whole banner is green", [(s["from"], s["to"]) for s in spans] == [(0, 17)] and spans[0]["style"].get("fg") in (2, "2"), str(spans))
        r.call("terminal.detach", {"terminal": res.get("terminal"), "format": "styled"})
    finally:
        r.close()


def test_a_failed_start_stops_the_world() -> None:
    class Doomed:
        stopped = False
        def setup(self): pass
        def start_daemon(self): raise SystemExit("the daemon did not come up")
        def stop(self): Doomed.stopped = True
    try:
        Real(Doomed)
        raised = False
    except SystemExit:
        raised = True
    check("the failed start still raises", raised, "")
    check("the world was stopped", Doomed.stopped, "")


def main() -> int:
    def tests(td):
        run(lambda: test_a_failed_start_stops_the_world())
        missing = _have_tools()
        if missing:
            for t in (test_styled_attach_shows_the_colour_the_program_painted, test_keys_in_come_back_as_a_styled_frame,
                      test_bytes_attach_snapshot_and_output, test_resize_by_the_holder_changes_the_pane_and_repaints,
                      test_the_shared_client_drives_the_terminal_end_to_end,
                      test_the_pane_the_daemon_launches_runs_the_fake_claude_the_test_put_in_place):
                skip(t, missing)
            return
        run(lambda: test_the_pane_the_daemon_launches_runs_the_fake_claude_the_test_put_in_place())
        global THE
        THE = Real()
        try:
            run(lambda: test_styled_attach_shows_the_colour_the_program_painted())
            run(lambda: test_keys_in_come_back_as_a_styled_frame())
            run(lambda: test_bytes_attach_snapshot_and_output())
            run(lambda: test_resize_by_the_holder_changes_the_pane_and_repaints())
            run(lambda: test_the_shared_client_drives_the_terminal_end_to_end())
        finally:
            THE.close()
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
