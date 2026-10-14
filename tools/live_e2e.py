#!/usr/bin/env python3
"""Drive one real Claude Code agent through a test daemon and time how fast the daemon's event stream reflects each step.

  HANDLINE_LIVE=1 tools/live-e2e.sh [--daemon BINARY] [--json]

Spends real tokens: run it only on purpose, never from tools/suite.sh, check.sh or CI. Any agent may run it. It spends the
account tools/live_account.py picks (the most usage headroom now). It never touches the live daemon: it starts its own
on a scratch home under a temp test root, with one haiku agent in permission mode `default` (the permission step needs a
real prompt), the real shared settings, and the picked account's real config dir (HANDLINE_LIVE_ACCOUNTS: the keychain
keeps the login under that path). The agent's own hl and hooks reach the test daemon. --daemon is the handlined to run;
by default this checkout's is built.
It answers yes to every permission prompt the agent raises outside the permission step, so the run is unattended:
the agent works in a scratch checkout, but on this machine and with the real account's config dir.

It subscribes to the agents, tasks, chat and notifications topics and waits on each step's event with a deadline; nothing
is polled. A step the daemon reflects within 5 s is `pass`, later is `slow`, never is `missing`. A step paced by the model
(the answer, the agent's own message, the task closing) gets MODEL_S instead, and its time is mostly the model's. A missing
step is followed by one agent.get so the table says whether the thing happened without an event. A missing step is a
finding for the table, not a failure: exit 1 only when a step FAILs.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zlib
from pathlib import Path

import live_account

DEADLINE_S = 5.0
SLOW_S = 30.0      # how long past the deadline a step still counts as slow rather than missing
MODEL_S = 120.0
TOPICS = ["agents", "tasks", "chat", "notifications"]
AGENT = "live.e2e"
REPO = Path(__file__).resolve().parent.parent


class Refused(Exception):
    pass


class Daemon:
    """One JSON-RPC connection to the test daemon's socket, said hello to as its founder."""

    def __init__(self, sock: Path, key: str) -> None:
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.connect(str(sock))
        self.file = self.sock.makefile("rb")
        self.next_id = 0
        self.hello = self.call("hello", {"client": "live-e2e", "protocol": 1, "key": key})
        if self.hello.get("role") != "founder":
            raise Refused(f"the daemon took this connection as {self.hello.get('caller')}, not the founder")

    def read(self) -> dict | None:
        line = self.file.readline()
        return json.loads(line) if line else None

    def call(self, method: str, params: dict) -> dict:
        self.next_id += 1
        mid = self.next_id
        self.sock.sendall((json.dumps({"jsonrpc": "2.0", "id": mid, "method": method, "params": params}) + "\n").encode())
        while (msg := self.read()) is not None:
            if msg.get("id") == mid:
                if "error" in msg:
                    raise Refused(f"{method}: {msg['error'].get('message')}")
                return msg["result"]
        raise Refused(f"{method}: the daemon closed the connection")

    def write(self, method: str, params: dict) -> dict:
        return self.call(method, {**params, "request_id": f"live-e2e-{uuid.uuid4()}"})


class Stream:
    """A subscribed connection: a reader thread keeps every event with its arrival time and wakes whoever waits."""

    def __init__(self, sock: Path, key: str) -> None:
        self.conn = Daemon(sock, key)
        self.conn.call("subscribe", {"topics": TOPICS})
        self.events: list[tuple[float, dict]] = []
        self.cond = threading.Condition()
        self.closed = False
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        try:
            while (msg := self.conn.read()) is not None:
                if msg.get("method") == "event":
                    with self.cond:
                        self.events.append((time.time(), msg["params"]))
                        self.cond.notify_all()
        finally:
            with self.cond:
                self.closed = True
                self.cond.notify_all()

    def wait(self, since: float, pred, until: float) -> tuple[float, dict] | None:
        """The first event at or after `since` that `pred` takes, waiting until `until` for it."""
        i = 0
        with self.cond:
            while True:
                for at, ev in self.events[i:]:
                    if at >= since and pred(ev):
                        return at, ev
                i = len(self.events)
                left = until - time.time()
                if left <= 0 or self.closed:
                    return None
                self.cond.wait(left)

    def states_since(self, agent: str, since: float) -> list[str]:
        with self.cond:
            return [ev["data"].get("state") for at, ev in self.events
                    if at >= since and ev["type"] == "agent.changed" and ev.get("agent") == agent]


def agent_changed(agent: str, state: str):
    return lambda ev: ev["type"] == "agent.changed" and ev.get("agent") == agent and ev["data"].get("state") == state


def landed(req: int | None):
    """A founder message the daemon delivered into the agent's session (chat.send is how a structured session takes input)."""
    return lambda ev: ev["type"] == "chat.message" and ev["data"].get("id") == req and bool(ev["data"].get("landed_at"))


def solid_png(rgb: tuple[int, int, int], side: int = 32) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\x00" + bytes(rgb) * side for _ in range(side))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def yes(prompt: dict) -> str:
    """The option that answers yes: a structured prompt offers its own keys (allow, deny)."""
    return next((o["key"] for o in prompt.get("options") or [] if o.get("answer") == "yes"), "yes")


class Answerer:
    """Answers yes to the agent's permission prompts outside the permission step, on a connection of its own: a haiku agent in
    mode default asks before almost any command it improvises, and those prompts are not what a step measures."""

    def __init__(self, sock: Path, key: str, stream: Stream, agent: str) -> None:
        self.d, self.s, self.agent = Daemon(sock, key), stream, agent
        self.on = threading.Event()
        self.on.set()
        self.answered: set[str] = set()
        threading.Thread(target=self.loop, daemon=True).start()

    def loop(self) -> None:
        seen, since = set(), 0.0
        asks = lambda ev: (ev.get("agent") == self.agent or (ev.get("data") or {}).get("agent") == self.agent) and id(ev) not in seen \
            and (ev["type"] == "prompt_asked" or agent_changed(self.agent, "waiting")(ev))
        while got := self.s.wait(since, asks, time.time() + 3600):
            since, ev = got
            seen.add(id(ev))
            self.on.wait()
            try:
                prompt = self.d.call("agent.get", {"agent": self.agent}).get("prompt")
                if prompt and prompt["id"] not in self.answered:
                    self.answered.add(prompt["id"])
                    self.d.write("prompt.answer", {"agent": self.agent, "prompt": prompt["id"], "choice": yes(prompt)})
            except (Refused, OSError):
                pass


class Run:
    def __init__(self, daemon: Daemon, stream: Stream, answerer: Answerer, agent: str) -> None:
        self.d, self.s, self.answerer, self.agent = daemon, stream, answerer, agent
        self.rows: list[dict] = []

    def record(self, row: dict) -> None:
        self.rows.append(row)
        took = f"  {row['seconds']}s" if row.get("seconds") is not None else ""
        print(f"  {row['result']:8} {row['step']}{took}" + (f"  ({row['note']})" if row.get("note") else ""), flush=True)

    def step(self, name: str, since: float, pred, model: bool = False) -> dict | None:
        """Wait for one step's event and record it; a missing one is explained by one agent.get."""
        got = self.s.wait(since, pred, since + (MODEL_S if model else DEADLINE_S + SLOW_S))
        row = {"step": name, "paced_by": "model" if model else "daemon", "result": "missing", "seconds": None}
        if got:
            took = got[0] - since
            row.update(result="pass" if model or took <= DEADLINE_S else "slow", seconds=round(took, 2))
        else:
            row["note"] = self.explain(since)
        self.record(row)
        return got[1] if got else None

    def explain(self, since: float) -> str:
        v = self.d.call("agent.get", {"agent": self.agent})
        seen = ", ".join(self.s.states_since(self.agent, since)) or "none"
        return f"states seen: {seen}; agent.get: state {v.get('state')}, terminal {v.get('terminal')!r}, session_open {v.get('session_open')}"

    def fail(self, name: str, why: str) -> None:
        self.record({"step": name, "paced_by": "daemon", "result": "FAIL", "seconds": None, "note": why})


def lifecycle(r: Run) -> None:
    d, a = r.d, r.agent
    t = time.time()
    d.write("session.start", {"agent": a})
    r.step("starting shown until ready", t, agent_changed(a, "starting"))
    r.step("ready (idle)", t, agent_changed(a, "idle"), model=True)

    t = time.time()
    req = d.write("chat.send", {"agent": a, "body": "Reply with the single word PONG and nothing else."}).get("request")
    r.step("message landed", t, landed(req))
    r.step("working", t, agent_changed(a, "running"))
    r.step("idle when done", t, agent_changed(a, "idle"), model=True)


def chat_and_attachment(r: Run) -> None:
    d, a = r.d, r.agent
    png = solid_png((220, 20, 20))
    t = time.time()
    att = d.write("attachment.put", {"name": "e2e-red.png", "mime": "image/png", "bytes": len(png),
                                     "data": base64.b64encode(png).decode()}).get("attachment") or {}
    back = d.call("attachment.get", {"id": att["id"]}) if att.get("id") else {}
    if base64.b64decode(back.get("data") or "") == png:
        r.record({"step": "attachment round-trip (put, get)", "paced_by": "daemon", "result": "pass", "seconds": round(time.time() - t, 2)})
    else:
        r.fail("attachment round-trip (put, get)", f"put gave {att!r}; get gave {back.get('bytes')} bytes")
    nonce = uuid.uuid4().hex[:8]
    params = {"agent": a, "body": f'Look at the attached image, then run: hl say human "e2e {nonce} <its colour in one word>"'}
    if att.get("id"):
        params["attachments"] = [att["id"]]
    t = time.time()
    req = d.write("chat.send", params).get("request")
    r.step("founder message landed in the agent's session", t, landed(req))
    said = r.step("agent's message reached the founder chat", t,
                  lambda ev: ev["type"] == "chat.message" and ev["data"].get("from") == a and nonce in (ev["data"].get("body") or ""),
                  model=True)
    if said and "red" not in said["data"].get("body", "").lower():
        r.fail("agent saw the attached image", f"it said {said['data'].get('body')!r}")
    r.s.wait(t, agent_changed(a, "idle"), time.time() + MODEL_S)


def task_and_permission(r: Run) -> None:
    """A task goes queued, doing, done; the agent closes it with `hl task done`, which mode default asks permission for."""
    d, a = r.d, r.agent
    t = time.time()
    r.answerer.on.clear()
    task = d.write("task.new", {"owner": a, "title": "live e2e: close this task",
                                "body": 'Close this task with hl task done, evidence "e2e", and do nothing else.'}).get("task")
    is_task = lambda *st: lambda ev: ev["type"] == "task.changed" and ev["data"].get("id") == task and ev["data"].get("state") in st
    r.step("task queued", t, is_task("queued"))
    r.step("task doing", t, is_task("doing"), model=True)
    asked = r.step("permission prompt shown as needs_you", t,
                   lambda ev: agent_changed(a, "waiting")(ev) and (ev["data"].get("needs_you") or {}).get("kind") == "permission",
                   model=True)
    if asked:
        r.rows[-1]["note"] = f"needs_you {asked['data']['needs_you']!r}"
    prompt = r.step("prompt_asked notice with kind and lines", t,
                    lambda ev: ev["type"] == "prompt_asked" and ev["data"].get("agent") == a and ev["data"].get("lines"),
                    model=True)
    if prompt:
        r.rows[-1]["note"] = f"kind {prompt['data'].get('kind')!r}, lines {prompt['data']['lines'][:3]!r}"
    open_prompt = prompt["data"] if prompt else d.call("agent.get", {"agent": a}).get("prompt")
    if open_prompt:
        r.answerer.answered.add(open_prompt["id"])
        d.write("prompt.answer", {"agent": a, "prompt": open_prompt["id"], "choice": yes(open_prompt)})
    r.answerer.on.set()
    closed = r.step("task done", t, is_task("done", "reported"), model=True)
    if closed and closed["data"].get("state") == "reported":
        d.write("task.accept", {"task": task})
    r.s.wait(t, agent_changed(a, "idle"), time.time() + MODEL_S)


def kill_and_exit(r: Run) -> None:
    d, a = r.d, r.agent
    t = time.time()
    d.write("chat.send", {"agent": a, "body": "Write the numbers from 1 to 3000, one per line, with no tools."})
    running = r.s.wait(t, agent_changed(a, "running"), t + MODEL_S)
    if not running:
        r.fail("kill switch: turn stopped (idle)", "the turn never showed as running")
    elif r.s.wait(running[0], agent_changed(a, "idle"), time.time()):
        r.fail("kill switch: turn stopped (idle)", "the turn ended on its own before the interrupt was sent")
    else:
        t = time.time()
        d.write("session.interrupt", {"agent": a})
        r.step("kill switch: turn stopped (idle)", t, agent_changed(a, "idle"))

    t = time.time()
    d.write("session.stop", {"agent": a, "mode": "exit"})
    r.step("Claude Code exited", t, agent_changed(a, "no_terminal"))


def scratch(root: Path, account: dict) -> tuple[Path, str]:
    """The test daemon's home under root, with its founder key where the daemon looks for one under a test root: one account
    (the picked one, with the reading the live daemon last took of it), the real shared settings, and the agent in a git
    checkout of its own."""
    home, real = root / "state", Path(account["home"])
    store = root / ".config" / "orcapipe" / "protected" / re.sub(r"[^A-Za-z0-9]", "-", str(home)) / "founder-keys"
    store.mkdir(parents=True, mode=0o700)
    key = secrets.token_urlsafe(32)
    (store / "key").write_text(key)
    (store / "key").chmod(0o600)
    acct = home / "accounts" / account["account"]
    acct.mkdir(parents=True)
    for f in ("status.json", "subscription.json"):
        if (real / "accounts" / account["account"] / f).exists():
            shutil.copy(real / "accounts" / account["account"] / f, acct / f)
    (home / "shared" / ".claude").mkdir(parents=True)
    shutil.copy(real / "shared" / ".claude" / "settings.json", home / "shared" / ".claude" / "settings.json")
    work = root / "work"
    work.mkdir()
    for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "live e2e"]):
        subprocess.run(["git", "-c", "user.name=live-e2e", "-c", "user.email=live-e2e@localhost", *args], cwd=work, check=True)
    (home / "agents" / AGENT).mkdir(parents=True)
    (home / "agents" / AGENT / "agent.yaml").write_text(f"model: haiku\nmode: default\nstructured: true\nrepo: {work}\n")
    (home / "team.yaml").write_text(f"agents:\n  {AGENT}: {{}}\nedges:\n"
                                    '  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }\n')
    return home, key


def start_daemon(binary: str, root: Path, home: Path, account: dict) -> int:
    """The test daemon as production runs it (structured sessions, the tick, the cutover), in a process group of its own so its
    sessions go with it; returns that group. Like launchd's daemon it has no parent: an agent's hl counts the Claude Codes
    above it, and the one running this run must not be one. The agent's row names its checkout: the daemon makes no worktrees yet."""
    env = {k: v for k, v in os.environ.items() if k not in ("HANDLINE_AGENT", "OCP_AGENT", "HANDLINE_HOME", "OCP_HOME")}
    env |= {"HANDLINE_TEST_ROOT": str(root), "HANDLINE_LIVE_ACCOUNTS": account["home"]}
    claude = shutil.which("claude")
    if not claude:
        raise Refused("no claude on PATH")
    version = subprocess.run([claude, "--version"], capture_output=True, text=True).stdout.split()[0]
    sh = subprocess.Popen(["/bin/sh", "-c", '"$@" >/dev/null 2>"$LOG" & echo $!', "sh", binary, "-home", str(home), "-structured-cli",
                           claude, "-claude-version", version, "-structured-tick", "-cutover"],
                          env=env | {"LOG": str(root / "daemon.log")}, stdout=subprocess.PIPE, start_new_session=True)
    pid = int(sh.stdout.readline())
    sh.wait()
    sock = home / "daemon.sock"
    until = time.time() + 30
    while not sock.exists():
        if not alive(pid) or time.time() > until:
            raise Refused(f"the test daemon did not come up; its log: {root / 'daemon.log'}")
        time.sleep(0.1)
    con = sqlite3.connect(home / "db.sqlite", timeout=10)
    con.execute("INSERT OR REPLACE INTO agent_state(agent, state, updated, worktree) VALUES(?, 'queued', 0, ?)", (AGENT, str(root / "work")))
    con.commit()
    con.close()
    return sh.pid


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def stop_daemon(group: int) -> None:
    try:
        os.killpg(group, signal.SIGTERM)
    except ProcessLookupError:
        return
    until = time.time() + 30
    while time.time() < until:
        try:
            os.killpg(group, 0)
        except ProcessLookupError:
            return
        time.sleep(0.2)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--daemon", help="the handlined binary to test; by default this checkout's is built")
    ap.add_argument("--json", action="store_true", help="print the table as JSON at the end")
    ap.add_argument("--keep", action="store_true", help="keep the test root (home, daemon log) for a look afterwards")
    args = ap.parse_args()
    if os.environ.get("HANDLINE_LIVE") != "1":
        print("live-e2e: spends real tokens; HANDLINE_LIVE=1 runs it", file=sys.stderr)
        return 2

    account = live_account.pick_live()
    print(f"live-e2e: account {account['account']}: {account['why']}", flush=True)
    root = Path(tempfile.mkdtemp(prefix="live-e2e.")).resolve()
    daemon, r = None, None
    try:
        binary = args.daemon
        if not binary:
            binary = str(root / "handlined")
            subprocess.run(["go", "build", "-o", binary, "./cmd/handlined"], cwd=REPO / "go", check=True)
        home, key = scratch(root, account)
        daemon = start_daemon(binary, root, home, account)
        d = Daemon(home / "daemon.sock", key)
        s = Stream(home / "daemon.sock", key)
        r = Run(d, s, Answerer(home / "daemon.sock", key, s, AGENT), AGENT)
        print(f"live-e2e: {AGENT} on a test daemon {d.hello.get('daemon')}, home {home}", flush=True)
        lifecycle(r)
        chat_and_attachment(r)
        task_and_permission(r)
        kill_and_exit(r)
        print(f"live-e2e: {len(r.answerer.answered)} permission prompt(s) answered yes, the permission step's own among them", flush=True)
    except (Refused, OSError, subprocess.CalledProcessError, KeyboardInterrupt) as e:
        print(f"live-e2e: {str(e) or 'interrupted'}", file=sys.stderr)
        return 2
    finally:
        if daemon:
            stop_daemon(daemon)
        if args.keep:
            print(f"live-e2e: kept {root}", file=sys.stderr)
        else:
            shutil.rmtree(root, ignore_errors=True)
    if args.json:
        print(json.dumps(r.rows, indent=1))
    return 1 if any(row["result"] == "FAIL" for row in r.rows) else 0


if __name__ == "__main__":
    sys.exit(main())
