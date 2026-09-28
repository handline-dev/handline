"""Tests: the daemon's control socket (T351) - the wire protocol, immediate
typing, the pending-message row and its reload, and a socket request racing
the tick's own lock on one agent's terminal.

Run on its own: python3 tests/daemon/test_socket.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import tempfile
import threading
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
        b.sendall(json.dumps(payload).encode("utf-8") + b"\n")
        b.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            chunk = b.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        t.join(timeout=5)
        b.close()
    data = b"".join(chunks).strip()
    return json.loads(data) if data else None


def _typeable_world(home: Path) -> list:
    """backend has a terminal, a live session, and a clear screen - ready to
    be typed at once. Returns the list `orca.send` records (handle, text)
    into."""
    _daemon_home(home, 20)
    sessions_are("backend")
    orcamod.available = lambda: True
    orcamod.alive = lambda h: True
    orcamod.screen_blocker = lambda h: None
    sent: list[tuple[str, str]] = []

    def send(handle, text, enter=True):
        sent.append((handle, text))
        return True, ""
    orcamod.send = send
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


def test_a_refused_socket_tells_the_client_the_daemon_is_down(home: Path) -> None:
    _daemon_home(home, 20)
    reply = dmn_client.send({"verb": "message", "agent": "backend", "text": "hi"})
    check("socket: nothing listening reads as None, not an exception or a false ok",
          reply is None, str(reply))


def test_a_screen_blocker_defers_to_a_pending_row_a_later_deliver_picks_up(home: Path) -> None:
    sent = _typeable_world(home)
    blocked = {"on": True}
    orcamod.screen_blocker = lambda h: (
        orcamod.Blocker(orcamod.MODAL, title="Trust?") if blocked["on"] else None)
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


def test_a_socket_message_racing_the_tick_for_the_same_agent_never_doubles_up(home: Path) -> None:
    """The tick and a socket request both reach for the same agent's
    terminal at once (T351 point 4): whichever loses the race must not type
    at all - and must not lose the message either, since it was made durable
    (a pending row) before either side ever asked for the lock."""
    sent = _typeable_world(home)
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time; sys.path.insert(0, %r);"
         "from ocp.infra import lock;"
         "ctx = lock.terminal('backend'); print(ctx.__enter__(), flush=True);"
         "time.sleep(30)" % str(ROOT)],
        stdout=subprocess.PIPE, text=True, env=dict(os.environ))
    try:
        check("race: the other process (standing in for the tick) holds the lock",
              holder.stdout.readline().strip() == "True")
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


def test_an_unknown_verb_and_a_malformed_request_reply_ok_false_not_a_crash(home: Path) -> None:
    _typeable_world(home)
    reply = _serve_one_roundtrip({"verb": "not-a-real-verb", "agent": "backend"})
    check("socket: an unknown verb is refused in words, not a dropped connection",
          reply is not None and reply["ok"] is False, str(reply))
    reply2 = _serve_one_roundtrip({"verb": "message", "agent": "backend"})  # no "text"
    check("socket: a malformed request for a real verb still replies ok:false",
          reply2 is not None and reply2["ok"] is False, str(reply2))


def main() -> int:
    def tests(td):
        run(lambda: test_serve_one_types_a_message_at_once_over_the_wire(Path(td) / 's0'))
        if _af_unix_bind_works():
            run(lambda: test_the_socket_server_binds_a_private_file_a_real_client_can_reach(Path(td) / 's0b'))
        else:
            print("SKIPPED  test_the_socket_server_binds_a_private_file_a_real_client_can_reach: "
                  "this process's sandbox denies AF_UNIX bind() outright (an agent's own Bash tool "
                  "does); the wire protocol it would exercise is covered instead by "
                  "test_serve_one_types_a_message_at_once_over_the_wire, over a socket pair")
        run(lambda: test_a_refused_socket_tells_the_client_the_daemon_is_down(Path(td) / 's1'))
        run(lambda: test_a_screen_blocker_defers_to_a_pending_row_a_later_deliver_picks_up(Path(td) / 's2'))
        run(lambda: test_pending_rows_left_by_a_dead_daemon_are_reloaded_at_start(Path(td) / 's3'))
        run(lambda: test_a_socket_message_racing_the_tick_for_the_same_agent_never_doubles_up(Path(td) / 's4'))
        run(lambda: test_an_unknown_verb_and_a_malformed_request_reply_ok_false_not_a_crash(Path(td) / 's5'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
