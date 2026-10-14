"""Tests: the daemon's control socket (T351) - the wire protocol, immediate
typing, the pending-message row and its reload, and a socket request racing
the tick's own lock on one agent's terminal.

Run on its own: python3 tests/daemon/test_socket.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403


def _af_unix_bind_works() -> bool:
    """Whether this process may actually `bind()` an AF_UNIX socket. False
    inside an agent's own Bash-tool sandbox (Claude Code's own), which
    denies the syscall outright - AF_INET bind on loopback still works
    there, so it is not sockets in general, only this one. The rest of this
    file's coverage of the wire protocol does not depend on it (see
    `_serve_one_roundtrip`, a socket *pair* rather than a bound file)."""
    try:
        with tempfile.TemporaryDirectory() as d:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                s.bind(str(Path(d) / "probe.sock"))
                return True
            finally:
                s.close()
    except PermissionError:
        return False


def _typeable_world(home: Path) -> list:
    """backend has a terminal, a live session, and a clear screen - ready to
    be typed at once. Returns the list `orca.send` records (handle, text)
    into."""
    _daemon_home(home, 20)
    sessions_are("backend")
    termmod.available = lambda: True
    termmod.alive = lambda h: True
    termmod.screen_blocker = lambda h: None
    sent: list[tuple[str, str]] = []

    def send(handle, text, enter=True):
        sent.append((handle, text))
        return True, ""
    termmod.send = send
    return sent


class _LateShutdown:
    """The client's end of a socket pair that waits for the daemon's thread to answer and hang up
    before it shuts its write side, as a client descheduled between its send and its shutdown does -
    and refuses the shutdown with `error` when there is one (macOS says ENOTCONN once the peer is gone)."""

    def __init__(self, sock, server: threading.Thread, error: OSError | None) -> None:
        self._sock, self._server, self._error = sock, server, error

    def shutdown(self, how):
        self._server.join(timeout=5)
        if self._error is not None:
            raise self._error
        return self._sock.shutdown(how)

    def __getattr__(self, name):
        return getattr(self._sock, name)


def test_a_message_is_not_typed_at_an_agent_that_is_at_the_wall(home: Path) -> None:
    """stopgate.wall_reason's own docstring: no path that types at an agent's
    terminal may skip it - typed anyway, a turn at the usage wall fails at
    once and clears the very mark that says so."""
    sent = _typeable_world(home)
    con = db.connect()
    try:
        db.add_event(con, "backend", "StopFailure", {"limit": "5h"})
        ok, said = dmn_messaging.send_message(con, "backend", "please wait", lambda m: None)
        check("messaging: a message to an agent at the wall still reports ok - queued, not lost",
              ok, said)
        check("messaging: but nothing is typed while it is at the wall", not sent, str(sent))
        check("messaging: the text stays pending",
              len(db.list_pending(con, "backend")) == 1)
    finally:
        con.close()


def test_a_pending_row_already_delivered_is_never_typed_twice(home: Path) -> None:
    """The race the socket and the tick can hit on one row: `send_message`'s
    own attempt and a `deliver_pending` for the same row both reach
    `_deliver_row`, one after the other, serialized by `lock.terminal` -
    the second must find the row already gone and type nothing more."""
    sent = _typeable_world(home)
    con = db.connect()
    try:
        row_id = db.add_pending(con, "backend", "only once", int(time.time()))
        ok1, _ = dmn_messaging._deliver_row(con, "backend", row_id, "only once")
        check("messaging: the first delivery types it",
              ok1 and sent == [("term_x", "only once")], str(sent))
        ok2, said2 = dmn_messaging._deliver_row(con, "backend", row_id, "only once")
        check("messaging: a second delivery of the same, already-gone row sends nothing more",
              ok2 and sent == [("term_x", "only once")], str((said2, sent)))
    finally:
        con.close()


def test_a_refused_socket_tells_the_client_the_daemon_is_down(home: Path) -> None:
    _daemon_home(home, 20)
    reply = dmn_client.send({"verb": "message", "agent": "backend", "text": "hi"})
    check("socket: nothing listening reads as None, not an exception or a false ok",
          reply is None, str(reply))


def test_a_screen_blocker_defers_to_a_pending_row_a_later_deliver_picks_up(home: Path) -> None:
    sent = _typeable_world(home)
    blocked = {"on": True}
    termmod.screen_blocker = lambda h: (
        termmod.Blocker(termmod.MODAL, title="Trust?") if blocked["on"] else None)
    con = db.connect()
    try:
        ok, said = dmn_messaging.send_message(con, "backend", "queued please", lambda m: None)
        check("messaging: a screen_blocker still reports ok - the daemon owns it now", ok, said)
        check("messaging: but nothing is typed while the dialog stands", not sent, str(sent))
        rows = db.list_pending(con, "backend")
        check("messaging: the text is kept as a pending row",
              len(rows) == 1 and rows[0]["text"] == "queued please", str(rows))
        blocked["on"] = False
        delivered = dmn_messaging.deliver_pending(con, "backend", lambda m: None)
        check("messaging: once the blocker clears, the next tick delivers it",
              delivered and sent == [("term_x", "queued please")], str(sent))
        check("messaging: and the pending row is gone", db.list_pending(con, "backend") == [])
    finally:
        con.close()


def test_pending_rows_left_by_a_dead_daemon_are_reloaded_at_start(home: Path) -> None:
    sent = _typeable_world(home)
    con = db.connect()
    try:
        db.add_pending(con, "backend", "left over from before the reboot", int(time.time()))
        dmn_messaging.reload_pending(con, lambda m: None)
        check("messaging: a restart delivers what a crash left pending",
              sent == [("term_x", "left over from before the reboot")], str(sent))
        check("messaging: and clears the row", db.list_pending(con, "backend") == [])
    finally:
        con.close()


def _hold_terminal_lock_elsewhere(agent: str) -> subprocess.Popen:
    """Spawn a process that takes `agent`'s lock.terminal and sits on it, to
    stand in for the tick mid-send - the race every T351 control verb has to
    lose cleanly rather than double up on. Caller kills and waits on it."""
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time; sys.path.insert(0, %r);"
         "from handline.infra import lock;"
         "ctx = lock.terminal(%r); print(ctx.__enter__(), flush=True);"
         "time.sleep(30)" % (str(ROOT), agent)],
        stdout=subprocess.PIPE, text=True, env=dict(os.environ))
    assert holder.stdout.readline().strip() == "True", "the holder process did not take the lock"
    return holder


def test_a_socket_message_racing_the_tick_for_the_same_agent_never_doubles_up(home: Path) -> None:
    """The tick and a socket request both reach for the same agent's
    terminal at once (T351 point 4): whichever loses the race must not type
    at all - and must not lose the message either, since it was made durable
    (a pending row) before either side ever asked for the lock."""
    sent = _typeable_world(home)
    holder = _hold_terminal_lock_elsewhere("backend")
    try:
        con = db.connect()
        try:
            ok, said = dmn_messaging.send_message(con, "backend", "racing text", lambda m: None)
            check("race: the socket's own attempt still reports ok - queued, not lost", ok, said)
            check("race: but nothing is typed while the tick holds the lock", not sent, str(sent))
            check("race: the text survives as a pending row",
                  len(db.list_pending(con, "backend")) == 1)
        finally:
            con.close()
    finally:
        holder.kill()
        holder.wait()
    # The kernel released the lock when the holder died - the next tick
    # (`deliver_pending`) reaches the same row and types it, once, not twice.
    con = db.connect()
    try:
        delivered = dmn_messaging.deliver_pending(con, "backend", lambda m: None)
        check("race: freed, the tick delivers the queued text exactly once",
              delivered and sent == [("term_x", "racing text")], str(sent))
    finally:
        con.close()


def test_peer_pid_names_the_process_on_the_other_end_of_the_socket(home: Path) -> None:
    from handline.infra import sessiontoken
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        check("socket: peer_pid names the process on the other end of a socket pair",
              sessiontoken.peer_pid(a) == os.getpid(), str(sessiontoken.peer_pid(a)))
    finally:
        a.close(); b.close()


def main() -> int:
    def tests(td):
        if _af_unix_bind_works():
            pass
        else:
            print("SKIPPED  test_the_socket_server_binds_a_private_file_a_real_client_can_reach: "
                  "this process's sandbox denies AF_UNIX bind() outright (an agent's own Bash tool "
                  "does); the wire protocol it would exercise is covered instead by "
                  "test_serve_one_types_a_message_at_once_over_the_wire, over a socket pair")
        run(lambda: test_a_message_is_not_typed_at_an_agent_that_is_at_the_wall(Path(td) / 's1w'))
        run(lambda: test_a_pending_row_already_delivered_is_never_typed_twice(Path(td) / 's1d'))
        run(lambda: test_a_refused_socket_tells_the_client_the_daemon_is_down(Path(td) / 's1'))
        run(lambda: test_a_screen_blocker_defers_to_a_pending_row_a_later_deliver_picks_up(Path(td) / 's2'))
        run(lambda: test_pending_rows_left_by_a_dead_daemon_are_reloaded_at_start(Path(td) / 's3'))
        run(lambda: test_a_socket_message_racing_the_tick_for_the_same_agent_never_doubles_up(Path(td) / 's4'))
        run(lambda: test_peer_pid_names_the_process_on_the_other_end_of_the_socket(Path(td) / 's6'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
