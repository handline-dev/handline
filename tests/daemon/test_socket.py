"""Tests: the control socket from the client's side: a refused socket reads as the daemon down, and the peer pid
names the process on the other end.
Run on its own: python3 tests/daemon/test_socket.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
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


def test_a_refused_socket_tells_the_client_the_daemon_is_down(home: Path) -> None:
    _daemon_home(home, 20)
    reply = dmn_client.send({"verb": "message", "agent": "backend", "text": "hi"})
    check("socket: nothing listening reads as None, not an exception or a false ok",
          reply is None, str(reply))


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
        run(lambda: test_a_refused_socket_tells_the_client_the_daemon_is_down(Path(td) / 's1'))
        run(lambda: test_peer_pid_names_the_process_on_the_other_end_of_the_socket(Path(td) / 's6'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
