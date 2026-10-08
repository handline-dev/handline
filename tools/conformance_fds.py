#!/usr/bin/env python3
"""Run contract/conformance.py against the built Go daemon without a socket file: tools/conformance_fds.py BINARY [conformance args]

A sandbox may refuse to bind a socket, so the daemon (`-serve-fds`) serves connections this script made as socketpairs and
passes to it, one per fixture. It starts on a scratch home with a founder key minted where it reads it, which is given to
the harness as --var founder_key. The network listener is on (loopback, port 0) and `-serve-net-fds` serves a second set of
socketpairs as its connections: the role `unpaired`. Roles with no connection of their own (agent, member) are reported
SKIP, as in a real run."""
import os
import re
import json
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "contract"))
import conformance  # noqa: E402

PAIRS = 192
# The words a view stores (the spec's x-state-words that fixtures pin): one agent each, as the Python daemon's tick would have left it.
STATES = ["running", "idle", "queued", "parked", "waiting", "stalled", "no_terminal"]


def seed_states(home: Path, daemon: subprocess.Popen) -> dict[str, str]:
    """An agent per state word: its agent.yaml and a stored view, which the daemon serves as it is. The schema is the daemon's,
    so the views go in once it has made the database. Returns the --var for each."""
    names = {w: f"s.{w}" for w in STATES}
    for n in names.values():
        (home / "agents" / n).mkdir(parents=True)
        (home / "agents" / n / "agent.yaml").write_text("role: seeded by conformance_fds\n")
    for _ in range(200):
        try:
            con = sqlite3.connect(home / "db.sqlite", timeout=5)
            for w, n in names.items():
                view = {"name": n, "role": "seeded by conformance_fds", "model": None, "effort": None, "state": w, "state_detail": "",
                        "state_since": None, "block": None, "wall_reason": None, "background_since": None, "would_wake": False,
                        "blocker": None, "account": None, "task": None, "cache_until": None, "has_dialog": w == "waiting",
                        "parent": None}
                con.execute("INSERT OR REPLACE INTO agent_view(agent, view, updated) VALUES(?, ?, 0)", (n, json.dumps(view)))
            con.commit()
            con.close()
            return {f"state_{w}": n for w, n in names.items()}
        except sqlite3.Error:
            if daemon.poll() is not None:
                break
            time.sleep(0.05)
    raise RuntimeError("the daemon's database did not come up to seed")


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
            seeded = seed_states(home, daemon)
            rc = conformance.main(["--allow-skip", "--var", f"founder_key={key}",
                                   *(a for k, v in seeded.items() for a in ("--var", f"{k}={v}")), *rest],
                                  connectors={"founder": local, "anonymous": local, "unpaired": taker(net_pairs)})
            return rc
        finally:
            daemon.terminate()
            err = daemon.communicate()[1].decode(errors="replace")
            if rc and err.strip():
                print("daemon log:\n" + err, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
