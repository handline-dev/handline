#!/usr/bin/env python3
"""Run contract/conformance.py against the built Go daemon without a socket file: tools/conformance_fds.py BINARY [--structured-cli CLI] [conformance args]

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


# The structured agents structured_controls.json needs, by var name. Each is started over the wire (session.start, the fake CLI)
# and the busy ones are sent the text tools/fake_claude.py answers with a turn that stays open: "fake-hold" until interrupted,
# "fake-ask" with a permission prompt open.
STRUCTURED = {"structured_agent": None, "turn_agent": "fake-hold", "interrupt_agent": "fake-hold", "asking_agent": "fake-ask"}


def call(conn, rid, method, params):
    conn.sendall((json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}) + "\n").encode())
    buf = b""
    while True:
        chunk = conn.recv(65536)
        if not chunk:
            raise RuntimeError(f"{method}: connection closed")
        buf += chunk
        for line in buf.split(b"\n")[:-1]:
            msg = json.loads(line)
            if msg.get("id") == rid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg["result"]


def seed_structured(home: Path, daemon: subprocess.Popen, connect, key: str) -> dict[str, str]:
    """A running structured session per var of STRUCTURED, started as a client would. Returns the --var for each. The daemon does not
    make worktrees yet, and starts an agent in the one its row names, so each row is written with a directory."""
    names = {var: f"c.{var}" for var in STRUCTURED}
    (home / "accounts" / "main" / ".claude").mkdir(parents=True)  # a session starts on a logged-in account: this one has a credential file
    (home / "accounts" / "main" / ".claude" / ".credentials.json").write_text("{}")
    (home / "shared" / ".claude").mkdir(parents=True)  # a session is not started without the shell guard's hook in them
    (home / "shared" / ".claude" / "settings.json").write_text(json.dumps(
        {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "/usr/bin/handline hook PreToolUse"}]}]}}))
    for n in names.values():
        (home / "agents" / n).mkdir(parents=True)
        (home / "agents" / n / "agent.yaml").write_text("role: seeded by conformance_fds\nstructured: true\n")
        (home / "worktrees" / n).mkdir(parents=True)
    for _ in range(200):
        try:
            con = sqlite3.connect(home / "db.sqlite", timeout=5)
            for n in names.values():
                con.execute("INSERT OR REPLACE INTO agent_state(agent, state, updated, worktree) VALUES(?, 'queued', 0, ?)",
                            (n, str(home / "worktrees" / n)))
            con.commit()
            con.close()
            break
        except sqlite3.Error:
            if daemon.poll() is not None:
                raise
            time.sleep(0.05)
    else:
        raise RuntimeError("the daemon's database did not come up to seed")
    (home / "team.yaml").write_text("agents:\n" + "".join(f"  {n}: {{}}\n" for n in names.values()) + """edges:
  - { from: "*", to: "*", relation: peer, response: async }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
""")
    conn = connect()
    call(conn, 1, "hello", {"client": "conformance-seed/1", "protocol": 1, "key": key})
    for i, (var, text) in enumerate(STRUCTURED.items()):
        call(conn, 10 + 2 * i, "session.start", {"agent": names[var], "request_id": f"seed-start-{var}"})
        if text:
            call(conn, 11 + 2 * i, "chat.send", {"agent": names[var], "body": text, "request_id": f"seed-send-{var}"})
    conn.close()
    return {var: n for var, n in names.items()}


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    binary, rest = argv[0], argv[1:]
    structured_cli = []
    if rest[:1] == ["--structured-cli"]:  # the daemon serves the session.* methods over structured sessions run by this CLI
        structured_cli, rest = ["-structured-cli", rest[1]], rest[2:]
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
                                   "-serve-net-fds", ",".join(map(str, net_theirs)), *structured_cli], pass_fds=theirs + net_theirs,
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
            if structured_cli:
                seeded |= seed_structured(home, daemon, local, key)
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
