"""Tests: the `styled` terminal format and its SGR reader. The rows a host gives with their codes become `Row.spans` in the
snapshot, the frames, the history and the marker and holder notices of a styled attach; a host that gives no codes is refused,
gate unsupported; text and styled attaches of one terminal on one connection are independent.

Run on its own: python3 tests/daemon/test_termstyled.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.daemon import termchannel  # noqa: E402
from handline.infra import ansi  # noqa: E402
from handline.session import backends  # noqa: E402
from handline.session.backends.base import Backend  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import _hp  # noqa: E402
from test_rpc_terminal import Screen, _code, _gate, _next, _wire  # noqa: E402

RED = "\x1b[1;31mred\x1b[0m plain"


def test_the_reader_turns_sgr_codes_into_runs() -> None:
    check("ansi: text without codes has no runs", ansi.parse("hello  ") == ("hello", []))
    check("ansi: bold red, then reset", ansi.parse(RED) == ("red plain", [{"from": 0, "to": 3, "style": {"bold": True, "fg": 1}}]))
    check("ansi: bright colours are palette 8-15", ansi.parse("\x1b[91;102ma")[1][0]["style"] == {"fg": 9, "bg": 10})
    check("ansi: 256 colours and true colour", ansi.parse("\x1b[38;5;208;48;2;1;2;255mx")[1][0]["style"] == {"fg": 208, "bg": "#0102ff"})
    check("ansi: the colon form with an empty colour space", ansi.parse("\x1b[38:2::1:2:3mx")[1][0]["style"] == {"fg": "#010203"})
    check("ansi: neighbours with one style are one run", len(ansi.parse("\x1b[4mab\x1b[4mcd")[1]) == 1)
    check("ansi: 22 clears bold and dim, 39 and 49 the default colours", ansi.parse("\x1b[1;2;31;41mA\x1b[22;39;49mB")[1] == [{"from": 0, "to": 1, "style": {"bold": True, "dim": True, "fg": 1, "bg": 1}}])
    text, spans = ansi.parse("ab\x1b[44m   \x1b[m")
    check("ansi: blank cells with a background run past the end of the text", text == "ab" and spans == [{"from": 2, "to": 5, "style": {"bg": 4}}], str(spans))
    check("ansi: other escapes and controls are dropped", ansi.parse("\x1b[?25h\x1b(B\x0ea\x1b]0;t\x07b") == ("ab", []))
    check("ansi: a wide character is one cell", ansi.parse("\x1b[3m漢字\x1b[m")[1] == [{"from": 0, "to": 2, "style": {"italic": True}}])


class _StyledHost(Backend):
    name = "tmux"
    serves_styled = True

    def available(self) -> bool:
        return True


def _serve(rows: list[str]) -> None:
    """The styled rows the host gives: `rows` as it has them now, with their codes."""
    def read(handle, n):
        parsed = [ansi.parse(r) for r in rows]
        return [{"text": t, **({"spans": sp} if sp else {})} for t, sp in parsed]
    termchannel.read_styled = read


def test_a_styled_attach_carries_spans_in_its_snapshot_frames_history_and_notices(home: Path) -> None:
    support._board_world(home)
    saved = termchannel.read_styled
    was = backends.use(_StyledHost())
    try:
        with Screen() as s:
            rows = ["$ ready", RED]
            _serve(rows)
            w = _wire()
            t = w.call("terminal.attach", {"agent": "worker", "format": "styled"})["result"]
            check("styled: the snapshot says styled and its rows carry spans", t["format"] == "styled" and t["lines"][1] == {
                "text": "red plain", "spans": [{"from": 0, "to": 3, "style": {"bold": True, "fg": 1}}]} and t["lines"][0] == {"text": "$ ready"}, str(t))
            check("styled: the text channel is not the styled one", "t-worker" in termchannel.HUB.styled and "t-worker" not in termchannel.HUB.channels)
            rows[0] = "\x1b[32m$ go\x1b[m"
            f = _next(w, "frame")
            check("styled: a changed row comes in a frame that says styled, with its spans", f is not None and f["params"]["format"] == "styled"
                  and f["params"]["changed"][0]["row"]["spans"][0]["style"] == {"fg": 2}, str(f))
            tx = w.call("terminal.attach", {"agent": "worker"})["result"]
            check("styled: a text attach of the same terminal is independent and has no spans", tx["format"] == "text" and all("spans" not in r for r in tx["lines"]), str(tx))
            con = db.connect()
            termchannel.marker(con, "worker", "control", {"kind": "person", "name": "someone"}, "hi")
            con.close()
            formats = sorted(m["params"]["format"] for m in (_next(w, "marker"), _next(w, "marker")) if m)
            check("styled: a marker is sent to each attach, saying which", formats == ["styled", "text"], str(formats))
            rows[:] = [f"\x1b[31mline {i}\x1b[m" for i in range(3)]
            _next(w, "frame")
            rows[:] = [f"\x1b[31mline {i}\x1b[m" for i in range(1, 4)]
            _next(w, "frame")
            h = w.call("terminal.history", {"terminal": "t-worker", "before": 100, "limit": 10, "format": "styled"})["result"]
            check("styled: history rows keep their spans", h["lines"] and h["lines"][0].get("spans") == [{"from": 0, "to": 6, "style": {"fg": 1}}], str(h))
            r = w.call("terminal.detach", {"terminal": "t-worker", "format": "styled"})
            check("styled: detach takes styled", "error" not in r, str(r))
            w.close()
    finally:
        termchannel.read_styled = saved
        backends.use(was)


def test_the_cursor_is_in_the_frames_own_rows_and_a_move_alone_sends_a_frame(home: Path) -> None:
    support._board_world(home)
    saved = termchannel.read_cursor
    cur = {"x": 2, "y": 1, "visible": True, "height": 24, "blank_below": 22}       # a 24-row pane with two drawn rows
    termchannel.read_cursor = lambda handle: dict(cur)
    try:
        with Screen() as s:
            s.rows = ["$ ready", "$ ab"]
            w = _wire()
            t = w.call("terminal.attach", {"agent": "worker"})["result"]
            check("cursor: the snapshot has the cursor on its row", t["cursor"] == {"x": 2, "y": 1}, str(t["cursor"]))
            cur.update(x=4)
            f = _next(w, "frame")
            check("cursor: a move over the same rows is a frame with no changed row and the new cursor",
                  f is not None and f["params"]["changed"] == [] and f["params"]["cursor"] == {"x": 4, "y": 1}, str(f))
            s.rows = ["$ ready", "$ abc", "", ""]
            cur.update(y=2, blank_below=20)
            _next(w, "frame")
            cur.update(y=1, blank_below=22)
            s.rows = ["$ ready", "$ abc"]
            _next(w, "frame")
            t2 = w.call("terminal.attach", {"agent": "worker"})["result"]
            check("cursor: rows count from the frame's bottom when the pane's blank rows are dropped", t2["cursor"] == {"x": 4, "y": 1}, str(t2["cursor"]))
            s.rows = ["$ ready", "한글 abc"]
            cur.update(x=5, y=1, blank_below=22)
            _next(w, "frame")
            t4 = w.call("terminal.attach", {"agent": "worker"})["result"]
            check("cursor: x counts code points of the row, a wide character is two cells but one", t4["cursor"] == {"x": 3, "y": 1}, str(t4["cursor"]))
            cur.update(x=12)
            _next(w, "frame")
            t5 = w.call("terminal.attach", {"agent": "worker"})["result"]
            check("cursor: past the text each blank cell is one", t5["cursor"] == {"x": 10, "y": 1}, str(t5["cursor"]))
            s.rows = ["$ ready", "$ abc"]
            cur.update(x=4)
            _next(w, "frame")
            cur.update(visible=False)
            _next(w, "frame")
            t3 = w.call("terminal.attach", {"agent": "worker"})["result"]
            check("cursor: a hidden cursor is null", t3["cursor"] is None, str(t3["cursor"]))
            cur.update(y=5)
            check("cursor: a hidden cursor that moves sends no frame", _next(w, "frame", 0.5) is None)
            cur.update(visible=True, y=0, blank_below=0, height=24)
            s.rows = ["$ x"]
            _next(w, "frame")
            check("cursor: above the frame is null", termchannel.HUB.channels["t-worker"].cursor is None, str(termchannel.HUB.channels["t-worker"].cur))
            w.close()
    finally:
        termchannel.read_cursor = saved


def test_a_host_without_codes_refuses_styled_and_hello_lists_what_it_serves(home: Path) -> None:
    support._board_world(home)
    was = backends.use(Backend())
    try:
        with Screen():
            w = _wire()
            r = w.call("terminal.attach", {"agent": "worker", "format": "styled"})
            check("styled: a host that gives no codes is refused, gate unsupported", _gate(r) == "unsupported" and not termchannel.HUB.styled, str(r))
            r = w.call("terminal.history", {"terminal": "t-worker", "before": 5, "limit": 5, "format": "styled"})
            check("styled: so is its history", _gate(r) == "unsupported", str(r))
            r = w.call("terminal.attach", {"agent": "worker", "format": "ansi"})
            check("styled: an unknown format is bad_request", _code(r) == "invalid_params" or "error" in r, str(r))
            w.close()
    finally:
        backends.use(was)


def test_the_styled_fixtures_pass_against_this_daemon(home: Path) -> None:
    import socket
    import threading
    from handline.daemon import socket_server
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "contract"))
    import conformance

    def connect():
        a, b = socket.socketpair()
        threading.Thread(target=socket_server._serve_one, args=(b, lambda m: None), daemon=True).start()
        return a
    support._board_world(home)
    saved, bad = termchannel.read_styled, []
    try:
        with Screen():
            _serve(["$ ready", RED])
            for host, only in ((_StyledHost(), "styled_snapshot_shape"), (Backend(), "a_host_without_attributes_refuses_styled")):
                was = backends.use(host)
                try:
                    res = conformance.run_all([Path("contract/fixtures/terminal_bytes.json")], {"founder": connect},
                                              {"founder_key": _hp()["key"], "styled_agent": "worker", "text_only_agent": "worker"}, only)
                finally:
                    backends.use(was)
                bad += [(n, why) for st, why, n in res if st != "pass"] or ([] if res else [(only, "not run")])
    finally:
        termchannel.read_styled = saved
    check("fixtures: the styled terminal fixtures pass against this daemon", not bad, str(bad))


def main() -> int:
    def tests(td):
        run(lambda: test_the_reader_turns_sgr_codes_into_runs())
        run(lambda: test_a_styled_attach_carries_spans_in_its_snapshot_frames_history_and_notices(Path(td) / "s1"))
        run(lambda: test_the_styled_fixtures_pass_against_this_daemon(Path(td) / "s3"))
        run(lambda: test_the_cursor_is_in_the_frames_own_rows_and_a_move_alone_sends_a_frame(Path(td) / "s4"))
        run(lambda: test_a_host_without_codes_refuses_styled_and_hello_lists_what_it_serves(Path(td) / "s2"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
