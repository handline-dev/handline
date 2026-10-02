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
        # One of ours that never gives it: left out of the table like another user's, and the reading
        # stays readable - one such process must not blank every agent's session (it did, for an hour).
        obsmod._ps_command = lambda pid, flags: argv
        check("environment: our own process that withholds it for good is left out, the reading stays readable",
              obsmod._env_or_reason(900) == (None, True) and obsmod.live_sessions() == {})
        obsmod.process_table = lambda: [(900, "claude --resume abc"), (901, "claude --resume def")]
        envs = {900: argv, 901: "claude --resume def\nOCP_AGENT=reviewer X=1\n"}

        def one_unreadable(pid, flags):
            if flags == "ww":
                return argv if pid == 900 else "claude --resume def\n"
            return envs[pid] if pid == 901 else argv
        obsmod._ps_command = one_unreadable
        check("environment: so another agent's session is still listed beside the one nobody could read",
              obsmod.live_sessions() == {"reviewer": 901}, str(obsmod.live_sessions()))
        obsmod._owner_uid = lambda pid: None
        check("environment: an owner that cannot be read is what it always was: left out",
              obsmod._env_or_reason(900) == (None, True))
    finally:
        obsmod._ps_command, obsmod._owner_uid, obsmod.process_table, obsmod.ENV_RETRY_S = real


def _table(listed: bool | None):
    """What the process table says about backend: listed, absent, or unreadable."""
    obsmod.live_sessions = (lambda: None) if listed is None else (lambda: {"backend": 4200} if listed else {})
    dmn_delivery._seen.clear()
    dmn_delivery._drawn.clear()


def test_a_session_is_present_if_the_table_lists_it_or_the_screen_draws_it(home: Path) -> None:
    con, screen = _world(home, COMPOSER_EMPTY, table=True)
    reads: list[int] = []
    real_read = screen.read
    screen.read = lambda handle, limit=40: (reads.append(1), real_read(handle, limit))[1]
    orcamod._screen = screen.read
    try:
        _table(True)
        check("present: a session the table lists is present, and the screen is not read for it",
              dmn_delivery.session_present("backend") and reads == [], str(reads))
        _table(False)
        check("present: the table lists none but the screen draws a session: present",
              dmn_delivery.session_present("backend"))
        check("present: and the screen answer is held a few seconds, not read for every ask",
              dmn_delivery.session_present("backend") and dmn_delivery.session_present("backend") and len(reads) == 1,
              str(reads))
        _table(None)
        check("present: a table that could not be read, with a session drawn: present",
              dmn_delivery.session_present("backend"))
        screen.rows = list(SHELL)
        _table(False)
        check("present: none listed and a shell drawn: not present", not dmn_delivery.session_present("backend"))
        _table(None)
        check("present: an unreadable table and a shell: still not (fail closed)", not dmn_delivery.session_present("backend"))
        orcamod._screen = lambda h, limit=40: None
        _table(False)
        check("present: none listed and a screen that cannot be read: not present", not dmn_delivery.session_present("backend"))
        db.set_terminal(con, "backend", None, "wt")
        orcamod._screen = real_read
        screen.rows = list(COMPOSER_EMPTY)
        _table(False)
        check("present: no terminal, nothing drawn", not dmn_delivery.session_present("backend"))
    finally:
        con.close()


def test_work_reaches_a_session_the_table_missed(home: Path) -> None:
    """The starvation: a live idle session the table did not list was never typed at, and its queue waited."""
    con, screen = _world(home, COMPOSER_EMPTY, table=False)
    _table(False)
    try:
        ok, said = dmn_messaging.send_message(con, "backend", "please look at this", lambda m: None)
        check("starved: a message to a session the table missed is typed, not queued with 'no session'",
              ok and "please look at this" in screen.sent and "typed" in said, f"{ok} {said} {screen.sent}")
        screen.sent.clear()
        screen.rows = list(SHELL)
        _table(False)
        ok, said = dmn_messaging.send_message(con, "backend", "and this", lambda m: None)
        check("starved: and at a shell with no session listed nothing is typed",
              "and this" not in screen.sent, f"{ok} {said} {screen.sent}")
    finally:
        con.close()


def test_a_session_the_table_misses_is_a_block_the_founder_sees_with_how_the_table_read_it(home: Path) -> None:
    con, screen = _world(home, COMPOSER_EMPTY, table=False)
    _table(False)
    real_diag = obsmod.diagnose
    obsmod.diagnose = lambda: ["pid 77: OCP_AGENT=harbor.other", "pid 78: its environment could not be read"]
    try:
        now = int(time.time())
        lines: list[str] = []
        blockedmod._probed.clear()
        blockedmod._checked.clear()
        con.execute("UPDATE requests SET state = 'landed'")      # nothing waiting: the block is not about work
        blockedmod.sweep(con, lines.append, now)
        rec = db.block_get(con, "backend")
        check("miss block: with the table and the screen disagreeing a block is opened for the table",
              rec is not None and rec["kind"] == blockedmod.TABLE_MISS and rec["origin"] == blockedmod.TABLE_ORIGIN,
              str(dict(rec) if rec else None))
        check("miss block: it says how each session-looking process was read, and nothing of the environment",
              "pid 77: OCP_AGENT=harbor.other" in rec["detail"] and "could not be read" in rec["detail"], rec["detail"])
        check("miss block: it is on the founder's list after the usual wait, not at once",
              rec["show_at"] == now + blockedmod.SHOW_AFTER_S and "table misses" in "".join(lines), str(lines))
        check("miss block: and its words are the bridge's own label and advice",
              blockedmod.headline(rec["kind"], "").startswith("process table misses a live session")
              and "on the screen's word" in blockedmod.ADVICE[blockedmod.TABLE_MISS])
        blockedmod.sweep(con, lines.append, now + blockedmod.DECISION_AFTER_S + 1)
        rec = db.block_get(con, "backend")
        check("miss block: if it stands it becomes a request to the founder, like any block",
              rec["request_id"] is not None, str(dict(rec)))
        # The table lists it again: the block ends.
        _table(True)
        blockedmod._checked.clear()
        blockedmod.sweep(con, lines.append, now + blockedmod.DECISION_AFTER_S + 30)
        check("miss block: once the table lists the session again it ends", db.block_get(con, "backend") is None, str(lines[-2:]))
    finally:
        obsmod.diagnose = real_diag
        con.close()


def test_the_diagnosis_names_each_session_looking_process_and_leaks_no_environment(home: Path) -> None:
    _daemon_home(home, 20)
    real = (obsmod.process_table, obsmod._env_or_reason)
    try:
        obsmod.process_table = lambda: [(1, "claude one"), (2, "claude two"), (3, "claude three"), (4, "vim notes"),
                                        (5, "claude five")]
        envs = {1: ("OCP_AGENT=backend ANTHROPIC_SECRET=sk-ant-hunter2 X=1", False), 2: (None, True),
                3: (None, False), 5: ("PATH=/bin HOME=/h", False)}
        obsmod._env_or_reason = lambda pid: envs[pid]
        got = obsmod.diagnose()
        check("diagnose: each session-looking process is named with how it was read, others left out",
              got == ["pid 1: OCP_AGENT=backend", "pid 2: another user's process",
                      "pid 3: its environment could not be read", "pid 5: OCP_AGENT=unset"], str(got))
        check("diagnose: and nothing of the rest of an environment is in it", "hunter2" not in " ".join(got))
        obsmod.process_table = lambda: None
        check("diagnose: an unreadable table says so", obsmod.diagnose() == ["the process table could not be read"])
        obsmod.process_table = lambda: [(4, "vim notes")]
        check("diagnose: and none looking like a session says that", obsmod.diagnose() == ["no process looks like a session"])
    finally:
        obsmod.process_table, obsmod._env_or_reason = real


def main() -> int:
    def tests(td):
        run(lambda: test_the_screen_says_whether_a_session_is_drawn(Path(td) / "g1"))
        run(lambda: test_a_close_is_not_recorded_while_a_session_is_still_drawn(Path(td) / "g2"))
        run(lambda: test_no_launch_line_is_typed_at_a_drawn_session_by_any_path(Path(td) / "g3"))
        run(lambda: test_the_sweep_does_not_call_a_drawn_session_gone(Path(td) / "g4"))
        run(lambda: test_a_session_end_event_alone_marks_nothing_closed(Path(td) / "g5"))
        run(lambda: test_a_process_whose_environment_ps_withholds_is_not_ours_only_if_it_is_not_ours(Path(td) / "g6"))
        run(lambda: test_a_session_is_present_if_the_table_lists_it_or_the_screen_draws_it(Path(td) / "g7"))
        run(lambda: test_work_reaches_a_session_the_table_missed(Path(td) / "g8"))
        run(lambda: test_a_session_the_table_misses_is_a_block_the_founder_sees_with_how_the_table_read_it(Path(td) / "g9"))
        run(lambda: test_the_diagnosis_names_each_session_looking_process_and_leaks_no_environment(Path(td) / "g10"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
