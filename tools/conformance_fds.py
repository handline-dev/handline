#!/usr/bin/env python3
"""Run contract/conformance.py against the built Go daemon without a socket file: tools/conformance_fds.py BINARY [conformance args]

A sandbox may refuse to bind a socket, so the daemon (`-serve-fds`) serves connections this script made as socketpairs and
passes to it, one per fixture. It starts on a scratch home with a founder key minted where it reads it, which is given to
the harness as --var founder_key. The network listener is on (loopback, port 0) and `-serve-net-fds` serves a second set of
socketpairs as its connections: the role `unpaired`. Roles with no connection of their own (agent, member) are reported
SKIP, as in a real run."""
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "contract"))
import conformance  # noqa: E402

PAIRS = 128


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    binary, rest = argv[0], argv[1:]
    with tempfile.TemporaryDirectory(prefix="handlined.") as tmp:
        test_root = Path(tmp).resolve()
        home = test_root / "state"
        home.mkdir()
        store = test_root / ".config" / "orcapipe" / "protected" / re.sub(r"[^A-Za-z0-9]", "-", str(home.resolve())) / "founder-keys"
        store.mkdir(parents=True, mode=0o700)
        key = secrets.token_urlsafe(32)
        (store / "key").write_text(key)
        (store / "key").chmod(0o600)
        pairs = [socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM) for _ in range(PAIRS)]
        net_pairs = [socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM) for _ in range(PAIRS)]
        theirs = [b.fileno() for _a, b in pairs]
        net_theirs = [b.fileno() for _a, b in net_pairs]
        daemon = subprocess.Popen([binary, "-home", str(home), "-listen", "127.0.0.1:0", "-serve-fds", ",".join(map(str, theirs)),
                                   "-serve-net-fds", ",".join(map(str, net_theirs))], pass_fds=theirs + net_theirs,
                                  stderr=subprocess.PIPE, env={**os.environ, "HANDLINE_TEST_ROOT": str(test_root)})
        for _a, b in pairs + net_pairs:
            b.close()

        def taker(pool):
            near = [a for a, _b in pool]

            def connect():
                if not near:
                    raise RuntimeError("out of connections: raise PAIRS")
                return near.pop(0)
            return connect

        local = taker(pairs)
        rc = 1
        try:
            rc = conformance.main(["--allow-skip", "--var", f"founder_key={key}", *rest],
                                  connectors={"founder": local, "anonymous": local, "unpaired": taker(net_pairs)})
            return rc
        finally:
            daemon.terminate()
            err = daemon.communicate()[1].decode(errors="replace")
            if rc and err.strip():
                print("daemon log:\n" + err, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
