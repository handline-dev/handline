#!/usr/bin/env python3
"""Run contract/conformance.py against the built Go daemon without a socket file: tools/conformance_fds.py BINARY [--claude CLI] [conformance args]

A sandbox may refuse to bind a socket, so the daemon (`-serve-fds`) serves connections this script made as socketpairs and
passes to it, one per fixture. It starts on a scratch home with a founder key minted where it reads it, which is given to
the harness as --var founder_key. The role `agent` gets socketpairs made by a child process recorded as an agent's session in
that home's store, so the daemon tells their peer to be that agent. The network listener is on (loopback, port 0) and
`-serve-net-fds` serves a second set of socketpairs as its connections: the role `unpaired`. A fixture of a role with no
connection here (member) is reported SKIP and fails the run."""
import fcntl
import os
import re
import json
import secrets
import socket
import sqlite3
import subprocess
import ctypes
import ctypes.util
import struct
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "contract"))
import conformance  # noqa: E402

SESSION_NS = "session-tokens"  # the protected store's folder of agent session records


def session_line(pid: int) -> str | None:
    """`pid` as a line of an agent's session record, "pid start" (the start time from the kernel); None when no such process runs."""
    if sys.platform == "darwin":
        libproc = ctypes.CDLL(ctypes.util.find_library("proc"))
        buf = ctypes.create_string_buffer(136)
        if libproc.proc_pidinfo(pid, 3, 0, buf, 136) != 136:  # PROC_PIDTBSDINFO
            return None
        start = struct.unpack_from("QQ", buf, 120)  # pbi_start_tvsec, pbi_start_tvusec
        return f"{pid} {start[0]}.{start[1]:06d}"
    try:
        with open(f"/proc/{pid}/stat") as f:
            rest = f.read().rsplit(")", 1)[1].split()
    except (OSError, IndexError):
        return None
    return f"{pid} {rest[19]}"  # starttime in ticks

PAIRS = 512
AGENT_PAIRS = 128
AGENT = "c.agent"  # the agent the role `agent` connects as
LEAD = "c"  # its team's manager, who a done it files for review waits on
# The words a view stores (the spec's x-state-words that fixtures pin): one agent each, as the Python daemon's tick would have left it.
STATES = ["running", "idle", "queued", "parked", "waiting", "stalled", "no_terminal"]


def seed_states(home: Path, daemon: subprocess.Popen) -> dict[str, str]:
    """An agent per state word: its agent.yaml and a stored view, which the daemon serves as it is. The schema is the daemon's,
    so the views go in once it has made the database. Returns the --var for each."""
    names = {w: f"s.{w}" for w in STATES}
    output_agent = "s.output"
    ask_agent = ASK_AGENT
    order = {"order_a": names["idle"], "order_b": names["running"], **ORDER}
    for n in [*names.values(), output_agent, ask_agent, DOING_AGENT, AGENT, LEAD, *RETIRE, *ORDER.values()]:
        (home / "agents" / n).mkdir(parents=True)
        (home / "agents" / n / "agent.yaml").write_text("role: seeded by conformance_fds\n")
    for _ in range(200):
        try:
            con = sqlite3.connect(home / "db.sqlite", timeout=5)
            for w, n in names.items():
                view = {"name": n, "role": "seeded by conformance_fds", "model": None, "effort": None, "state": w, "state_detail": "",
                        "state_since": None, "block": None, "wall_reason": None, "background_since": None, "would_wake": False,
                        "blocker": None, "account": None, "task": None, "cache_until": None, "has_dialog": w == "waiting",
                        "parent": None, "needs_you": None}
                con.execute("INSERT OR REPLACE INTO agent_view(agent, view, updated) VALUES(?, ?, 0)", (n, json.dumps(view)))
            seed_doing(con)
            seed_tool_items(con, output_agent)
            seed_session_start(con, output_agent)
            seed_workroom(home, con)
            seed_open_question(con, ask_agent)
            typing = seed_typing_gates(home, con)
            write_team(home, [*names.values(), output_agent, ask_agent, DOING_AGENT, AGENT, LEAD, *RETIRE, *ORDER.values(), *typing.values()])
            con.commit()
            con.close()
            return {**{f"state_{w}": n for w, n in names.items()}, "output_agent": output_agent, "session_agent": output_agent,
                    "ask_agent": ask_agent, "triggers_agent": names["idle"], "doing_agent": DOING_AGENT, "view_plain_agent": names["idle"], "retire_agent": RETIRE[0],
                    "retire_parent": RETIRE[1], **WORKROOM, **typing, **order, "order_rename_to": "o.new"}
        except sqlite3.Error:
            if daemon.poll() is not None:
                break
            time.sleep(0.05)
    raise RuntimeError("the daemon's database did not come up to seed")


# What agent_create.json and worktree_status.json need: a repo (a git directory with a commit), an agent that works in it, and the
# repo's path as that agent's worktree. create_name is not seeded: a new agent is started in a terminal, which this run has none of, so
# the done, failed and retry fixtures are named in go/conformance-needvar.txt and the Go tests play them over a fake host.
WORKROOM = {"create_repo": "", "create_parent": "c.parent", "status_worktree": ""}


def seed_workroom(home: Path, con: sqlite3.Connection) -> None:
    repo = home.parent / "workroom"
    repo.mkdir()
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
    for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "first"]):
        subprocess.run(["git", *args], cwd=repo, env=env, check=True)
    agent = home / "agents" / WORKROOM["create_parent"]
    agent.mkdir(parents=True)
    (agent / "agent.yaml").write_text(f"role: seeded by conformance_fds\nrepo: {repo}\n")
    con.execute("INSERT OR REPLACE INTO agent_state(agent, state, updated, worktree) VALUES(?, 'idle', 0, ?)", (WORKROOM["create_parent"], str(repo)))
    WORKROOM["create_repo"] = WORKROOM["status_worktree"] = str(repo.resolve())


def seed_open_question(con: sqlite3.Connection, agent: str) -> None:
    """One open question from the agent to the person, for chat_parent.json."""
    con.execute("INSERT INTO requests(ts, requester, target, body, response, kind, state) VALUES(?, ?, 'human', 'which one?', 'async', 'ask', 'delivered')",
                (int(time.time()), agent))


ASK_AGENT = "s.ask"
# agent.get.doing_names_the_tool_call: a running agent whose turn's newest tool call is Bash `sleep 30`, as the tick stores it.
DOING_AGENT = "s.doing"


def seed_doing(con: sqlite3.Connection) -> None:
    view = {"name": DOING_AGENT, "role": "seeded by conformance_fds", "model": None, "effort": None, "state": "running", "state_detail": "",
            "state_since": None, "block": None, "wall_reason": None, "background_since": None, "would_wake": False, "blocker": None,
            "account": None, "task": None, "cache_until": None, "has_dialog": False, "parent": None, "needs_you": None,
            "doing": {"text": "Bash sleep 30", "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}}
    con.execute("INSERT OR REPLACE INTO agent_view(agent, view, updated) VALUES(?, ?, 0)", (DOING_AGENT, json.dumps(view)))
# agent_retire.json: an agent nothing depends on, which it retires, and one with an agent under its name, which it may not.
RETIRE = ["r.gone", "r.boss", "r.boss.kid"]
# prefs.json's nav order fixtures: an agent they retire and one hr renames (to o.new); neither has a session.
ORDER = {"order_retire_agent": "o.gone", "order_rename_agent": "o.old"}


def write_team(home: Path, agents: list[str]) -> None:
    """A team where the person may say to any agent, which chat.send needs, and AGENT has LEAD for its lead."""
    (home / "team.yaml").write_text(f"teams:\n  {LEAD}: [{LEAD}, {AGENT}]\nagents:\n" + "".join(f"  {n}: {{}}\n" for n in agents) + """edges:
  - { from: "*", to: "*", relation: peer, response: async }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
""")


def seed_tool_items(con: sqlite3.Connection, agent: str) -> None:
    """Three Bash calls as the store holds them, for transcript_output.json: output only, output with eight lines cut, no result."""
    now = int(time.time())
    calls = [("printf 'a\\nb\\n'", {"text": "a\nb\n", "truncated": False}),
             ("seq 1 10", {"text": "1\n2\n", "truncated": True, "dropped_lines": 8}),
             ("true", None)]
    seq = 0
    for i, (command, result) in enumerate(calls):
        events = [("tool_use", {"tool_use": {"id": f"u{i}", "tool": "Bash", "input": {"command": command}}})]
        if result:
            events.append(("tool_result", {"tool_result": {"tool_use_id": f"u{i}", "ok": True, **result}}))
        for kind, body in events:
            seq += 1
            con.execute("INSERT OR REPLACE INTO transcript_events(agent, seq, session, turn, at, kind, item, body) VALUES(?,?,?,?,?,?,?,?)",
                        (agent, seq, "seed", None, now, kind, None, json.dumps(body)))


def seed_typing_gates(home: Path, con: sqlite3.Connection) -> dict[str, str]:
    """An agent whose terminal lock is held for the whole run (an flock on its lock file, the one go/internal/lock takes), and one
    whose input another person holds. Returns the --var for each."""
    locked, held = "s.locked", "s.held"
    for n in (locked, held):
        (home / "agents" / n).mkdir(parents=True, exist_ok=True)
        (home / "agents" / n / "agent.yaml").write_text("role: seeded by conformance_fds\n")
        con.execute("INSERT OR REPLACE INTO agent_state(agent, state, updated, terminal) VALUES(?, 'idle', 0, '%9')", (n,))
    (home / "locks").mkdir(exist_ok=True)
    f = open(home / "locks" / f"{locked}.terminal", "a")
    fcntl.flock(f, fcntl.LOCK_EX)
    HELD.append(f)  # kept open, so held, until this process ends
    now = int(time.time())
    con.execute("INSERT OR REPLACE INTO input_holders(agent, kind, name, since, last_typed) VALUES(?, 'person', 'someone else', ?, ?)",
                (held, now, now))
    return {"locked_agent": locked, "held_agent": held}


HELD: list = []


def seed_session_start(con: sqlite3.Connection, agent: str) -> None:
    """A conversation the agent began, bound to its session, for transcript_session.json."""
    con.execute("INSERT INTO session_starts(agent, ts, session_id, cause, resumed, text, prompt, actor) VALUES(?, ?, 'seed', 'launch', 0, '', '', '')",
                (agent, int(time.time())))


def agent_pairs(store: Path, n: int) -> tuple[list, socket.socket, int]:
    """n socketpairs made by a child process recorded in store as AGENT's session: the kernel names their maker as the peer of
    either end, so the daemon takes a connection on them for one from inside that session. The child lives until the
    returned socket closes, as the parent ends or dies; its pid is returned to be waited for."""
    ctl, child_end = socket.socketpair()
    pid = os.fork()
    if pid == 0:
        try:
            ctl.close()
            pairs = [socket.socketpair() for _ in range(n)]
            for i in range(0, n, 50):
                socket.send_fds(child_end, [b"p"], [s.fileno() for pair in pairs[i:i + 50] for s in pair])
            for pair in pairs:
                for s in pair:
                    s.close()
            child_end.recv(1)
        finally:
            os._exit(0)
    child_end.close()
    fds = []
    while len(fds) < 2 * n:
        _msg, got, _flags, _addr = socket.recv_fds(ctl, 1, 100)
        if not got:
            raise RuntimeError("the agent's session process ended before it made its connections")
        fds += got
    (store / SESSION_NS).mkdir(mode=0o700)
    (store / SESSION_NS / AGENT).write_text(session_line(pid))
    return [(socket.socket(fileno=fds[i]), socket.socket(fileno=fds[i + 1])) for i in range(0, 2 * n, 2)], ctl, pid


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__, file=sys.stderr)
        return 2
    binary, rest = argv[0], argv[1:]
    claude = []
    if rest[:1] == ["--claude"]:  # the daemon builds its agent side (the session methods, the supervisor) over this CLI
        claude, rest = ["-claude", rest[1]], rest[2:]
    # the service's defaults run the tick and the hook-fed views, which this run does not start
    claude += ["-terminal-tick=false", "-cutover=false", "-turns-from-hooks=false", "-notification-needs-you=false"]
    with tempfile.TemporaryDirectory(prefix="handlined.") as tmp:
        test_root = Path(tmp).resolve()
        home = test_root / "state"
        home.mkdir()
        store = test_root / ".config" / "orcapipe" / "protected" / re.sub(r"[^A-Za-z0-9]", "-", str(home.resolve()))
        (store / "founder-keys").mkdir(parents=True, mode=0o700)
        key = secrets.token_urlsafe(32)
        (store / "founder-keys" / "key").write_text(key)
        (store / "founder-keys" / "key").chmod(0o600)
        mine, session, holder = agent_pairs(store, AGENT_PAIRS)  # first: the child would hold a copy of any pair made before it
        pairs = [socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM) for _ in range(PAIRS)]
        net_pairs = [socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM) for _ in range(PAIRS)]
        theirs = [b.fileno() for _a, b in pairs + mine]
        net_theirs = [b.fileno() for _a, b in net_pairs]
        daemon = subprocess.Popen([binary, "-home", str(home), "-listen", "127.0.0.1:0", "-serve-fds", ",".join(map(str, theirs)),
                                   "-serve-net-fds", ",".join(map(str, net_theirs)), *claude], pass_fds=theirs + net_theirs,
                                  stderr=subprocess.PIPE,
                                  # the founder's daemon, even when an agent runs the check: it writes the protected store
                                  env={**os.environ, "HANDLINE_TEST_ROOT": str(test_root), "OCP_AGENT": "", "HANDLINE_AGENT": ""})
        for _a, b in pairs + net_pairs + mine:
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
            seeded |= {"add_account": "conf-added", "add_used_account": "conf-used", "add_retry_account": "conf-retried"}  # names no account has
            (home / "accounts" / "main" / ".claude").mkdir(parents=True)  # a logged-in account: it has a credential file
            (home / "accounts" / "main" / ".claude" / ".credentials.json").write_text("{}")
            seeded |= {"account": "main", "schedule_agent": ASK_AGENT, "task_owner": ASK_AGENT}
            rc = conformance.main(["--var", f"founder_key={key}", "--var", "dialogs_retired=1",
                                   *(a for k, v in seeded.items() for a in ("--var", f"{k}={v}")), *rest],
                                  connectors={"founder": local, "anonymous": local, "agent": taker(mine), "unpaired": taker(net_pairs)})
            return rc
        finally:
            daemon.terminate()
            err = daemon.communicate()[1].decode(errors="replace")
            session.close()
            os.waitpid(holder, 0)
            if rc and err.strip():
                print("daemon log:\n" + err, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
