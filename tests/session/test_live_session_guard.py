"""Tests: a session the process table fails to list is still a live session. The screen is a second witness
before a close is recorded, before a launch line is typed, and before the sweep calls a session gone; and a
process whose environment `ps` withholds is only "not ours" when it is another user's.

Run on its own: tools/test.sh tests/session/test_live_session_guard.py   (all areas: tools/suite.sh)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

SHELL = ["$ "]


class _Screen:
    """What the terminal draws, settable by the test, and every line typed at it."""

    def __init__(self, rows) -> None:
        self.rows, self.sent = list(rows), []

    def read(self, handle, limit=40):
        return _screen_of(self.rows)

    def send(self, handle, text, enter=True):
        self.sent.append(text)
        return True, ""


def _world(home: Path, rows, table: bool):
    """backend is idle in term_x, whose screen draws `rows`; the process table lists it or not."""
    con = _blocked_world(home)
    screen = _Screen(rows)
    orcamod._screen = screen.read
    orcamod.send = screen.send
    orcamod.wait_idle = lambda h, timeout_ms=0: True
    orcamod.screen_blocker = REAL_ORCA["screen_blocker"]
    orcamod.close_terminal = lambda h, tab=True: (True, "closed")
    db.set_terminal(con, "backend", "term_x", "wt")
    if not table:
        obsmod.live_sessions = lambda: {}
    dmn_delivery.END_WAIT_S, dmn_delivery.END_POLL_S = 0.4, 0.05
    return con, screen


def _exiting_but_still_drawn(screen: _Screen, table: dict) -> None:
    """`/exit` takes the session out of the process table and leaves its screen as it was."""
    def send(handle, text, enter=True):
        screen.sent.append(text)
        if text == "/exit":
            table.pop("backend", None)
        return True, ""
    orcamod.send = send


def test_the_screen_says_whether_a_session_is_drawn(home: Path) -> None:
    _daemon_home(home, 20)
    for name, rows, want in (
            ("a session at its prompt", COMPOSER_EMPTY, True),
            ("a dialog", EXIT_DIALOG, True),
            ("a shell prompt", SHELL, False),
            ("an empty screen", [], False)):
        orcamod._screen = lambda h, limit=40, r=rows: _screen_of(r)
        check(f"screen: {name} reads as {'a session' if want else 'no session'}",
              orcamod.session_on_screen("term_x") is want, str(rows[:2]))
    orcamod._screen = lambda h, limit=40: None
    check("screen: one that cannot be read is neither: None, so the table's word stands",
          orcamod.session_on_screen("term_x") is None and dmn_delivery.session_still_drawn("term_x") is False)
    check("screen: no terminal, no session drawn", dmn_delivery.session_still_drawn(None) is False)


def test_a_close_is_not_recorded_while_a_session_is_still_drawn(home: Path) -> None:
    """The process table lists no session after /exit, but the screen still draws one: the table missed a live
    process, and a closed mark over it would have a wake type a launch line at it."""
    con, screen = _world(home, COMPOSER_EMPTY, table=True)
    _exiting_but_still_drawn(screen, support._SESSION_TABLE["t"])
    try:
        st = types.SimpleNamespace(session_id="sess-1", ctx_pct=8)
        ok, why = dmn_lifecycle._end_session(con, "backend", lambda m: None, st, resume="sess-1")
        row = db.get_agent_state(con, "backend")
        check("close: with a session drawn the close is refused and says why",
              not ok and "still drawn on the terminal's screen" in why and row["closed_at"] is None, why)
        check("close: it is on record as a failed close, so the screens report it",
              db.last_event_ts(con, "backend", "close_failed") is not None)
        check("close: and /exit was typed once, not again", screen.sent == ["/exit"], str(screen.sent))
        # The session was just slow to repaint: the screen clears inside the wait and the close is recorded.
        screen.rows = list(COMPOSER_EMPTY)
        support._SESSION_TABLE["t"]["backend"] = 4200

        def shell_soon(handle, limit=40, _n=[0]):
            _n[0] += 1
            return _screen_of(SHELL if _n[0] > 3 else COMPOSER_EMPTY)
        orcamod._screen = shell_soon
        ok, why = dmn_lifecycle._end_session(con, "backend", lambda m: None, st, resume="sess-1")
        row = db.get_agent_state(con, "backend")
        check("close: a screen that clears within the wait is a session that ended",
              ok and row["closed_at"] is not None, why)
    finally:
        con.close()


def test_no_launch_line_is_typed_at_a_drawn_session_by_any_path(home: Path) -> None:
    con, screen = _world(home, COMPOSER_EMPTY, table=False)
    try:
        ok, why = orcamod.relaunch("term_x", "ocp launch backend")
        check("relaunch: refused at a drawn session, and nothing typed",
              not ok and "not typing a launch line" in why and screen.sent == [], f"{why} {screen.sent}")
        screen.rows = list(SHELL)
        ok, _ = orcamod.relaunch("term_x", "ocp launch backend")
        check("relaunch: typed at a shell", ok and screen.sent[0] == "ocp launch backend", str(screen.sent))

        # Through the wake: the closed mark is there, the table lists nothing, the screen draws a session.
        screen.sent.clear()
        screen.rows = list(COMPOSER_EMPTY)
        db.set_closed(con, "backend", "sess-1")
        con.execute("UPDATE requests SET state = 'queued', delivered_at = NULL, typed_at = NULL")
        support.something_waiting(con, "backend")
        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("wake: with a session drawn it types nothing and says so",
              not ok and "drawn on the terminal's screen" in why and screen.sent == [], f"{why} {screen.sent}")
        check("wake: and counts nothing against the agent - it is the table that is wrong, not the agent",
              row["wake_failures"] == 0 and row["state"] != dmn_states.STALLED, str(dict(row)))
    finally:
        con.close()


def test_the_sweep_does_not_call_a_drawn_session_gone(home: Path) -> None:
    con, screen = _world(home, COMPOSER_EMPTY, table=False)
    orcamod.live_terminals = lambda: {"term_x"}
    try:
        lines: list[str] = []
        for _ in range(3):
            dmn_reconciler.sweep(con, lines.append, [])
        row = db.get_agent_state(con, "backend")
        check("sweep: a session drawn on its terminal is not recorded closed on the table's say-so alone",
              row["closed_at"] is None, str(dict(row)))
        check("sweep: and it says so once, not every tick", sum("is drawn on its terminal" in m for m in lines) == 1,
              str(lines))
        screen.rows = list(SHELL)
        dmn_reconciler.sweep(con, lines.append, [])
        check("sweep: once the screen shows a shell the session is recorded gone",
              db.get_agent_state(con, "backend")["closed_at"] is not None)
    finally:
        con.close()


def test_a_session_end_event_alone_marks_nothing_closed(home: Path) -> None:
    """A /clear fires SessionEnd and the process lives on; only the table and the screen say a session is gone."""
    con, screen = _world(home, COMPOSER_EMPTY, table=True)
    orcamod.live_terminals = lambda: {"term_x"}
    try:
        db.add_event(con, "backend", "SessionEnd", {"reason": "clear"})
        db.add_event(con, "backend", "SessionStart", {})
        dmn_reconciler.sweep(con, lambda _m: None, [])
        check("session end: a SessionEnd with the process alive leaves the agent open",
              db.get_agent_state(con, "backend")["closed_at"] is None)
        db.add_event(con, "backend", "SessionEnd", {"reason": "other"})
        dmn_reconciler.sweep(con, lambda _m: None, [])
        check("session end: so does a bare SessionEnd with nothing else to go on",
              db.get_agent_state(con, "backend")["closed_at"] is None)
    finally:
        con.close()


def test_a_process_whose_environment_ps_withholds_is_not_ours_only_if_it_is_not_ours(home: Path) -> None:
    _daemon_home(home, 20)
    obsmod.live_sessions = REAL_OBSERVE["live_sessions"]
    real = (obsmod._ps_command, obsmod._owner_uid, obsmod.process_table, obsmod.ENV_RETRY_S)
    me = os.getuid()
    calls: list[str] = []
    try:
        obsmod.ENV_RETRY_S = 0
        argv = "claude --resume abc\n"
        obsmod.process_table = lambda: [(900, "claude --resume abc")]

        def ps(pid, flags, env_after=None):
            calls.append(flags)
            if flags == "ww":
                return argv
            return argv if (env_after is None or calls.count("eww") < env_after) else argv + "OCP_AGENT=backend X=1\n"
        # Another user's process: ps withholds the environment for good - left out, as it always was.
        obsmod._ps_command = ps
        obsmod._owner_uid = lambda pid: me + 1
        check("environment: another user's process is not ours, and the table is still readable",
              obsmod._env_or_reason(900) == (None, True) and obsmod.live_sessions() == {})
        # One of ours that comes back without an environment once: looked at again, and found.
        calls.clear()
        obsmod._owner_uid = lambda pid: me
        obsmod._ps_command = lambda pid, flags: ps(pid, flags, env_after=2)
        env, notours = obsmod._env_or_reason(900)
        check("environment: our own process that withheld it once is read on the second look",
              notours is False and env and "OCP_AGENT=backend" in env, str((env, notours)))
        calls.clear()
        check("environment: and the live session is listed", obsmod.live_sessions() == {"backend": 900})
        # One of ours that never gives it: unknown, never "not an agent" - so the whole reading is unknown.
        obsmod._ps_command = lambda pid, flags: argv
        check("environment: our own process that withholds it for good is unknown, not skipped",
              obsmod._env_or_reason(900) == (None, False) and obsmod.live_sessions() is None)
        obsmod._owner_uid = lambda pid: None
        check("environment: an owner that cannot be read is what it always was: left out",
              obsmod._env_or_reason(900) == (None, True))
    finally:
        obsmod._ps_command, obsmod._owner_uid, obsmod.process_table, obsmod.ENV_RETRY_S = real


def main() -> int:
    def tests(td):
        run(lambda: test_the_screen_says_whether_a_session_is_drawn(Path(td) / "g1"))
        run(lambda: test_a_close_is_not_recorded_while_a_session_is_still_drawn(Path(td) / "g2"))
        run(lambda: test_no_launch_line_is_typed_at_a_drawn_session_by_any_path(Path(td) / "g3"))
        run(lambda: test_the_sweep_does_not_call_a_drawn_session_gone(Path(td) / "g4"))
        run(lambda: test_a_session_end_event_alone_marks_nothing_closed(Path(td) / "g5"))
        run(lambda: test_a_process_whose_environment_ps_withholds_is_not_ours_only_if_it_is_not_ours(Path(td) / "g6"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
