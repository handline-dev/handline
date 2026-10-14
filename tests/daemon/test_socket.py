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


def _serve_one_roundtrip(payload: dict, log=lambda m: None) -> dict | None:
    """Drive `socket_server._serve_one` - the exact request/dispatch/reply
    code the real listener runs per connection - over a connected socket
    pair, so the wire format is exercised without a real `bind()`."""
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    t = threading.Thread(target=dmn_socket_server._serve_one, args=(a, log))
    t.start()
    try:
        data = dmn_client._exchange(b, payload).strip()      # the client's own half, as the real one runs it
    finally:
        t.join(timeout=5)
        b.close()
    return json.loads(data) if data else None


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


def test_serve_one_types_a_message_at_once_over_the_wire(home: Path) -> None:
    sent = _typeable_world(home)
    reply = _serve_one_roundtrip({"verb": "message", "agent": "backend", "text": "hi there"})
    check("socket: a typeable agent's message is typed at once",
          reply == {"ok": True, "said": "backend: typed"}, str(reply))
    check("socket: and it actually reached the terminal",
          sent == [("term_x", "hi there")], str(sent))
    con = db.connect()
    try:
        check("socket: nothing is left pending once it is typed",
              db.list_pending(con, "backend") == [])
    finally:
        con.close()


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


def _answered_before_the_shutdown(payload: dict, error: OSError | None):
    a, b = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    t = threading.Thread(target=dmn_socket_server._serve_one, args=(a, lambda m: None))
    t.start()
    try:
        return dmn_client._exchange(_LateShutdown(b, t, error), payload)
    finally:
        t.join(timeout=5)
        b.close()


def test_a_reply_already_waiting_when_the_client_shuts_its_write_side_is_still_the_reply(home: Path) -> None:
    """The cause of a flaky wire test, and a real fault: the daemon answers on the newline and hangs up, so a
    client that is slow between its send and its shutdown finds the peer gone. macOS refuses that shutdown
    with ENOTCONN (OSError 57), which `send` read as "no daemon" - telling the caller nothing was sent about a
    message that had been typed."""
    import errno
    sent = _typeable_world(home)
    payload = {"verb": "message", "agent": "backend", "text": "typed once"}
    raw = _answered_before_the_shutdown(payload, OSError(errno.ENOTCONN, "Socket is not connected"))
    check("late shutdown: the refusal (ENOTCONN) after the answer is not an error, and the reply is read",
          json.loads(raw) == {"ok": True, "said": "backend: typed"} and sent == [("term_x", "typed once")],
          f"{raw!r} {sent}")
    sent.clear()
    raw = _answered_before_the_shutdown({"verb": "message", "agent": "backend", "text": "again"}, None)
    check("late shutdown: and with the platform's own shutdown, whatever it does here, the same reply",
          json.loads(raw) == {"ok": True, "said": "backend: typed"} and sent == [("term_x", "again")], f"{raw!r}")
    try:
        _answered_before_the_shutdown(payload, OSError(errno.ECONNRESET, "Connection reset by peer"))
        refused = False
    except OSError as e:
        refused = e.errno == errno.ECONNRESET
    check("late shutdown: any other refusal is still an error, which `send` reports as no daemon", refused)


def test_the_socket_server_binds_a_private_file_a_real_client_can_reach(home: Path) -> None:
    """The one piece `_serve_one` alone cannot cover: `SocketServer.start()`
    actually creating the private socket file and a client finding it there
    by path, through `client.send` exactly as core/ui will call it."""
    sent = _typeable_world(home)
    srv = dmn_socket_server.SocketServer(lambda m: None)
    srv.start()
    try:
        mode = oct(paths.socket_file().stat().st_mode & 0o777)
        check("socket: the socket file is private (0600)", mode == "0o600", mode)
        reply = dmn_client.send({"verb": "message", "agent": "backend", "text": "over the real socket"})
        check("socket: the thin client reaches it and gets typed back",
              reply == {"ok": True, "said": "backend: typed"} and sent == [("term_x", "over the real socket")],
              str((reply, sent)))
    finally:
        srv.stop()


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


def test_control_start_refuses_when_the_lock_is_held_elsewhere(home: Path) -> None:
    """control.start took lock.terminal in this phase (T351) - it had none
    before. Held elsewhere, it must refuse rather than spawn a second
    terminal for the same agent."""
    _daemon_home(home, 20)
    con = db.connect()
    con.execute("UPDATE agent_state SET terminal = NULL WHERE agent = 'backend'")
    holder = _hold_terminal_lock_elsewhere("backend")
    try:
        said = actions.start(con, "backend")
        check("start: refused while something else holds the terminal",
              "someone else" in said, said)
        row = db.get_agent_state(con, "backend")
        check("start: nothing was spawned - still no terminal",
              row is None or not row["terminal"], str(dict(row or {})))
    finally:
        holder.kill()
        holder.wait()
        con.close()


def test_an_unknown_verb_and_a_malformed_request_reply_ok_false_not_a_crash(home: Path) -> None:
    _typeable_world(home)
    reply = _serve_one_roundtrip({"verb": "not-a-real-verb", "agent": "backend"})
    check("socket: an unknown verb is refused in words, not a dropped connection",
          reply is not None and reply["ok"] is False, str(reply))
    reply2 = _serve_one_roundtrip({"verb": "message", "agent": "backend"})  # no "text"
    check("socket: a malformed request for a real verb still replies ok:false",
          reply2 is not None and reply2["ok"] is False, str(reply2))


def test_the_accept_loop_outlives_running_out_of_descriptors(home: Path) -> None:
    """EMFILE from accept() is an OSError too, but the listener is still good: the loop waits and
    takes the next connection instead of leaving, which left the socket deaf until a restart."""
    import errno as _errno
    srv = dmn_socket_server.SocketServer(lambda m: None)
    served, said = [], []
    srv._log = said.append
    a, b = socket.socketpair()

    class Listener:
        calls = 0

        def accept(self):
            Listener.calls += 1
            if Listener.calls <= 3:
                raise OSError(_errno.EMFILE, "Too many open files")
            if Listener.calls == 4:
                return a, None
            srv._stop.set()
            raise socket.timeout()

    srv._sock = Listener()
    srv._serve = served.append
    srv._stop.wait = lambda s: None     # no half-second waits in a test
    srv._accept_loop()
    b.close()
    a.close()
    check("socket: a connection after EMFILE is still served", served == [a], str(served))
    check("socket: EMFILE is said once for the spell, not per retry", len(said) == 1, str(said))


def test_the_accept_loop_leaves_a_dead_listener_and_retries_only_what_can_come_back(home: Path) -> None:
    """Only EMFILE, ENFILE and ECONNABORTED can succeed on a later accept(); any other OSError means the listener is gone, and the loop
    says so and leaves instead of spinning every half second in silence."""
    import errno as _errno
    for code, name, leaves in ((_errno.EBADF, "EBADF", True), (None, "a bare OSError", True), (_errno.ECONNABORTED, "ECONNABORTED", False)):
        srv = dmn_socket_server.SocketServer(lambda m: None)
        said, calls = [], []
        srv._log = said.append

        class Listener:
            def accept(self, code=code):
                calls.append(1)
                if len(calls) > 3:
                    srv._stop.set()
                    raise socket.timeout()
                raise OSError(code, "x") if code else OSError("closed")
        srv._sock = Listener()
        srv._stop.wait = lambda s: None
        srv._accept_loop()
        check(f"socket: {name} {'ends the accept loop at once' if leaves else 'is retried'}",
              (len(calls) == 1) == leaves, str(len(calls)))
        check(f"socket: {name} {'is said' if leaves else 'is not said'}", bool(said) == leaves, str(said))


def test_start_raises_the_descriptor_limit_and_warns_when_it_stays_near_the_cap(home: Path) -> None:
    """launchd's 256 against a cap of 256 is the cause: start() itself must raise the limit, and say so when it could not."""
    _daemon_home(home, 20)
    real, calls = dmn_socket_server.raise_fd_limit, []
    for limit, warns in ((4096, False), (256, True)):
        dmn_socket_server.raise_fd_limit = lambda limit=limit: calls.append(limit) or limit
        said = []
        srv = dmn_socket_server.SocketServer(said.append)
        try:
            try:
                srv.start()
            except OSError:
                pass        # a sandbox that denies the bind: the limit was handled before it
            finally:
                srv.stop()
        finally:
            dmn_socket_server.raise_fd_limit = real
        check(f"socket: start() with a limit of {limit} {'warns' if warns else 'is quiet'}",
              any("file descriptors" in m for m in said) == warns, str(said))
    check("socket: start() asked for the limit to be raised, both times", calls == [4096, 256], str(calls))


def test_the_daemon_raises_its_descriptor_soft_limit_past_the_connection_cap(home: Path) -> None:
    """launchd hands a job 256 descriptors, as many as MAX_CONNECTIONS: the soft limit is raised, never past the hard one."""
    res, real = dmn_socket_server.resource, dmn_socket_server.resource.getrlimit
    seen = []
    for soft, hard, want in ((256, res.RLIM_INFINITY, 4096), (256, 1024, 1024), (10240, 20000, 10240)):
        res_get, res_set = res.getrlimit, res.setrlimit
        res.getrlimit = lambda _k, s=soft, h=hard: (s, h)
        res.setrlimit = lambda _k, v: seen.append(v)
        try:
            got = dmn_socket_server.raise_fd_limit()
        finally:
            res.getrlimit, res.setrlimit = res_get, res_set
        check(f"socket: soft {soft} hard {hard} ends at {want}", got == want, str(got))
    check("socket: a soft limit already above the target is left alone", len(seen) == 2, str(seen))
    check("socket: the real getrlimit is put back", res.getrlimit is real)


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
        run(lambda: test_serve_one_types_a_message_at_once_over_the_wire(Path(td) / 's0'))
        run(lambda: test_a_reply_already_waiting_when_the_client_shuts_its_write_side_is_still_the_reply(Path(td) / 's0b'))
        if _af_unix_bind_works():
            run(lambda: test_the_socket_server_binds_a_private_file_a_real_client_can_reach(Path(td) / 's0b'))
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
        run(lambda: test_control_start_refuses_when_the_lock_is_held_elsewhere(Path(td) / 's4a'))
        run(lambda: test_an_unknown_verb_and_a_malformed_request_reply_ok_false_not_a_crash(Path(td) / 's5'))
        run(lambda: test_peer_pid_names_the_process_on_the_other_end_of_the_socket(Path(td) / 's6'))
        run(lambda: test_the_accept_loop_outlives_running_out_of_descriptors(Path(td) / 's7'))
        run(lambda: test_the_daemon_raises_its_descriptor_soft_limit_past_the_connection_cap(Path(td) / 's8'))
        run(lambda: test_the_accept_loop_leaves_a_dead_listener_and_retries_only_what_can_come_back(Path(td) / 's9'))
        run(lambda: test_start_raises_the_descriptor_limit_and_warns_when_it_stays_near_the_cap(Path(td) / 's10'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
