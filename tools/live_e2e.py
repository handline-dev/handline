#!/usr/bin/env python3
"""Drive one real Claude Code agent through a test daemon and time how fast the daemon's event stream reflects each step.

  HANDLINE_LIVE=1 tools/live-e2e.sh [--daemon BINARY] [--json] [--terminal]

Spends real tokens: run it only on purpose, never from tools/suite.sh, check.sh or CI. Any agent may run it. It spends the
account tools/live_account.py picks (the most usage headroom now). It never touches the live daemon: it starts its own
on a scratch home under a temp test root, with one haiku agent in permission mode `default` (the permission step needs a
real prompt), the real shared settings, and the picked account's real config dir (HANDLINE_LIVE_ACCOUNTS: the keychain
keeps the login under that path). The agent's own hl and hooks reach the test daemon. --daemon is the handlined to run;
by default this checkout's is built.
It answers yes to every permission prompt the agent raises outside the permission step, so the run is unattended:
the agent works in a scratch checkout, but on this machine and with the real account's config dir.

It subscribes to the agents, tasks, chat and notifications topics and waits on each step's event with a deadline; nothing
is polled. A step the daemon reflects within 5 s is `pass`, later is `slow`, never is `missing`; `merged` is a state the
stream skipped because the next one came in the same scan. A step paced by the model
(the answer, the agent's own message, the task closing) gets MODEL_S instead, and its time is mostly the model's. A missing
step is followed by one agent.get so the table says whether the thing happened without an event. A missing step is a
finding for the table, not a failure: exit 1 only when a step FAILs.

--terminal runs a terminal agent instead, on a daemon with -turns-from-hooks and the terminal tick, in a tmux server of the
run's own: a normal turn, an Esc pressed mid-turn, and the CLI killed hard mid-turn. Its checkout is a temporary worktree of
this repository, which every account already trusts, so no folder-trust dialog shows and no account's .claude.json is written.
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
from datetime import datetime
from pathlib import Path

import live_account
import tmuxsock

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

    def __init__(self, sock: Path, key: str, topics: list[str] = TOPICS, into: Stream | None = None) -> None:
        """into: keep this connection's events in that stream's list, so one wait sees both (the transcript topic is subscribed
        on a connection of its own)."""
        self.conn = Daemon(sock, key)
        self.conn.call("subscribe", {"topics": topics})
        self.events: list[tuple[float, dict]] = into.events if into else []
        self.cond = into.cond if into else threading.Condition()
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

    def __init__(self, sock: Path, key: str, stream: Stream, agent: str, choose=yes) -> None:
        self.d, self.s, self.agent, self.choose = Daemon(sock, key), stream, agent, choose
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
                    self.d.write("prompt.answer", {"agent": self.agent, "prompt": prompt["id"], "choice": self.choose(prompt)})
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

    def step(self, name: str, since: float, pred, model: bool = False, judge=None) -> dict | None:
        """Wait for one step's event and record it; a missing one is explained by one agent.get. judge(event) may return
        (result, note) to record in place of the timed result."""
        got = self.s.wait(since, pred, since + (MODEL_S if model else DEADLINE_S + SLOW_S))
        row = {"step": name, "paced_by": "model" if model else "daemon", "result": "missing", "seconds": None}
        if got:
            took = got[0] - since
            row.update(result="pass" if model or took <= DEADLINE_S else "slow", seconds=round(took, 2))
            if judge and (verdict := judge(got[1])):
                row["result"], row["note"] = verdict
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
    # The tasks topic sends a task's state as of each watcher scan (every 250 ms), and the daemon hands a new task to its owner
    # at once, so queued and doing usually fall in one scan: the first task.changed already says doing.
    first_seen = lambda ev: None if ev["data"]["state"] == "queued" else \
        ("merged", f"first seen {ev['data']['state']}: delivered within one scan")
    r.step("task queued", t, is_task("queued", "doing", "reported", "done"), judge=first_seen)
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


def turn_end_at(config_dir: str, root: Path) -> float | None:
    """When the last turn's Stop hook ran, from the transcript's stop_hook_summary entry (millisecond timestamps; the hook rows
    in the database keep whole seconds)."""
    mark = re.sub(r"[^A-Za-z0-9]", "-", root.name)
    files = [f for f in Path(config_dir, "projects").glob(f"*{mark}*/*.jsonl")]
    if not files:
        return None
    at = None
    for line in max(files, key=lambda f: f.stat().st_mtime).read_text().splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("subtype") == "stop_hook_summary" and e.get("timestamp"):
            at = e["timestamp"]
    return datetime.fromisoformat(at.replace("Z", "+00:00")).timestamp() if at else None


def pane(sock: str) -> tuple[str, int | None]:
    """The agent's pane and the CLI under it: the pane runs a shell, and the launch typed claude into it."""
    pane_id, shell = subprocess.run(["tmux", "-L", sock, "list-panes", "-a", "-F", "#{pane_id} #{pane_pid}"],
                                    capture_output=True, text=True).stdout.split()[:2]
    kids = subprocess.run(["pgrep", "-P", shell], capture_output=True, text=True).stdout.split()
    return pane_id, int(kids[0]) if kids else None


def terminal_turns(r: Run, sock: str, config_dir: str, root: Path, group: int) -> None:
    """Working and idle from hooks, timed: a normal turn, an Esc mid-turn, and a CLI killed hard mid-turn."""
    d, a = r.d, r.agent
    t = time.time()
    d.write("session.start", {"agent": a})
    r.step("starting shown until ready", t, agent_changed(a, "starting"))
    if not r.step("ready (idle)", t, agent_changed(a, "idle"), model=True):
        return r.fail("terminal turns", "the terminal agent never came up idle; nothing after it can be timed")

    t = time.time()
    req = d.write("chat.send", {"agent": a, "body": "Reply with the single word PONG and nothing else."}).get("request")
    r.step("message landed", t, landed(req))
    r.step("working after the message was sent", t, agent_changed(a, "running"))
    idle = r.s.wait(t, agent_changed(a, "idle"), time.time() + MODEL_S)
    replied = turn_end_at(config_dir, root)
    if idle and replied:
        took = round(idle[0] - replied, 2)
        r.record({"step": "idle after the Stop hook (transcript time to agent.changed)", "paced_by": "daemon",
                  "result": "pass" if took <= DEADLINE_S else "slow", "seconds": took})
    else:
        r.record({"step": "idle after the Stop hook (transcript time to agent.changed)", "paced_by": "daemon", "result": "missing",
                  "seconds": None, "note": f"idle event {'seen' if idle else 'never'}, Stop in the transcript {'found' if replied else 'not found'}"})

    long_turn = "Write a 1500-word short story about a lighthouse keeper, in plain prose, with no tools."
    t = time.time()
    d.write("chat.send", {"agent": a, "body": long_turn})
    running = r.s.wait(t, agent_changed(a, "running"), t + MODEL_S)
    time.sleep(3)
    if not running or r.s.wait(running[0], agent_changed(a, "idle"), time.time()):
        r.fail("Esc: idle after the key", "the turn was not running when the Esc was due")
    else:
        t = time.time()
        subprocess.run(["tmux", "-L", sock, "send-keys", "-t", pane(sock)[0], "Escape"], check=True)
        r.step("Esc: idle after the key", t, agent_changed(a, "idle"))

    t = time.time()
    d.write("chat.send", {"agent": a, "body": long_turn})
    running = r.s.wait(t, agent_changed(a, "running"), t + MODEL_S)
    pid = pane(sock)[1]
    if not running or not pid:
        return r.fail("hard kill mid-turn", f"running {bool(running)}, CLI pid {pid}")
    time.sleep(2)
    t = time.time()
    os.kill(pid, signal.SIGKILL)
    gone = r.s.wait(t, lambda ev: agent_changed(a, "idle")(ev) or agent_changed(a, "no_terminal")(ev), t + DEADLINE_S + SLOW_S)
    held = subprocess.run(["lsof", "-g", str(group)], capture_output=True, text=True).stdout
    fds = sum(1 for line in held.splitlines() if line.endswith(".jsonl") and "/projects/" in line)
    r.record({"step": "hard kill mid-turn: leaves running", "paced_by": "daemon",
              "result": ("pass" if gone[0] - t <= DEADLINE_S else "slow") if gone else "missing",
              "seconds": round(gone[0] - t, 2) if gone else None,
              "note": f"states seen: {', '.join(r.s.states_since(a, t)) or 'none'}; transcript fds the daemon still holds: {fds}"})


def scratch(root: Path, account: dict, terminal: bool = False) -> tuple[Path, str]:
    """The test daemon's home under root, with its founder key where the daemon looks for one under a test root: one account
    (the picked one, with the reading the live daemon last took of it), the real shared settings, and the agent in a git
    checkout of its own (for a terminal agent, a worktree of this repository: see the module doc)."""
    home, real = root / "state", Path(account["home"])
    store = root / ".config" / "orcapipe" / "protected" / re.sub(r"[^A-Za-z0-9]", "-", str(home)) / "founder-keys"
    store.mkdir(parents=True, mode=0o700)
    key = secrets.token_urlsafe(32)
    (store / "key").write_text(key)
    (store / "key").chmod(0o600)
    if terminal:     # the pane's `hl launch` is the Go hl's only once this switch is on (cmd/hl/launch.go)
        (store.parent / "switches").mkdir(mode=0o700)
        (store.parent / "switches" / "go_launch").write_text("on")
    acct = home / "accounts" / account["account"]
    acct.mkdir(parents=True)
    for f in ("status.json", "subscription.json"):
        if (real / "accounts" / account["account"] / f).exists():
            shutil.copy(real / "accounts" / account["account"] / f, acct / f)
    (home / "shared" / ".claude").mkdir(parents=True)
    shutil.copy(real / "shared" / ".claude" / "settings.json", home / "shared" / ".claude" / "settings.json")
    work = root / "work"
    if terminal:
        subprocess.run(["git", "worktree", "add", "-q", "--detach", str(work), "HEAD"], cwd=REPO, check=True)
    else:
        work.mkdir()
        for args in (["init", "-q"], ["commit", "-q", "--allow-empty", "-m", "live e2e"]):
            subprocess.run(["git", "-c", "user.name=live-e2e", "-c", "user.email=live-e2e@localhost", *args], cwd=work, check=True)
    (home / "agents" / AGENT).mkdir(parents=True)
    kind = "" if terminal else "structured: true\n"
    (home / "agents" / AGENT / "agent.yaml").write_text(f"model: haiku\nmode: default\n{kind}repo: {work}\n")
    (home / "team.yaml").write_text(f"agents:\n  {AGENT}: {{}}\nedges:\n"
                                    '  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }\n')
    return home, key


def start_daemon(binary: str, root: Path, home: Path, account: dict, tmux: str | None = None,
                 agents: dict[str, Path] | None = None, env_extra: dict[str, str] | None = None) -> int:
    """The test daemon as production runs it (structured sessions, the tick, the cutover), in a process group of its own so its
    sessions go with it; returns that group. Like launchd's daemon it has no parent: an agent's hl counts the Claude Codes
    above it, and the one running this run must not be one. The agent's row names its checkout: the daemon makes no worktrees yet."""
    env = {k: v for k, v in os.environ.items() if k not in ("HANDLINE_AGENT", "OCP_AGENT", "HANDLINE_HOME", "OCP_HOME")}
    env |= {"HANDLINE_TEST_ROOT": str(root), "HANDLINE_LIVE_ACCOUNTS": account["home"]} | (env_extra or {})
    terminal = []
    if tmux:     # a tmux server of the run's own: the live one is never touched
        hl = str(REPO / "bin" / "hl")      # this checkout's hl, whose launch takes the real config dir under a test root
        subprocess.run([str(REPO / "tools" / "build-hl.sh")], check=True, stdout=subprocess.DEVNULL)
        env |= {"HANDLINE_TMUX_SOCKET": tmux, "OCP_TMUX_SOCKET": tmux, "HANDLINE_HOME": str(home), "OCP_HOME": str(home),
                "HANDLINE_BIN": hl, "OCP_BIN": hl}
        terminal = ["-terminal-tick", "-turns-from-hooks"]
    claude = shutil.which("claude")
    if not claude:
        raise Refused("no claude on PATH")
    version = subprocess.run([claude, "--version"], capture_output=True, text=True).stdout.split()[0]
    sh = subprocess.Popen(["/bin/sh", "-c", '"$@" >/dev/null 2>"$LOG" & echo $!', "sh", binary, "-home", str(home), "-structured-cli",
                           claude, "-claude-version", version, "-structured-tick", "-cutover", *terminal],
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
    for name, work in (agents or {AGENT: root / "work"}).items():
        con.execute("INSERT OR REPLACE INTO agent_state(agent, state, updated, worktree) VALUES(?, 'IDLE', 0, ?)", (name, str(work)))
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
    ap.add_argument("--terminal", action="store_true", help="a terminal agent: working and idle from hooks, a normal turn, an Esc, a hard kill")
    args = ap.parse_args()
    if os.environ.get("HANDLINE_LIVE") != "1":
        print("live-e2e: spends real tokens; HANDLINE_LIVE=1 runs it", file=sys.stderr)
        return 2

    account = live_account.pick_live()
    print(f"live-e2e: account {account['account']}: {account['why']}", flush=True)
    root = Path(tempfile.mkdtemp(prefix="live-e2e.")).resolve()
    daemon, r = None, None
    tmux = f"live-e2e-{os.getpid()}" if args.terminal else None
    try:
        binary = args.daemon
        if not binary:
            binary = str(root / "handlined")
            subprocess.run(["go", "build", "-o", binary, "./cmd/handlined"], cwd=REPO / "go", check=True)
        home, key = scratch(root, account, args.terminal)
        daemon = start_daemon(binary, root, home, account, tmux)
        d = Daemon(home / "daemon.sock", key)
        s = Stream(home / "daemon.sock", key)
        r = Run(d, s, Answerer(home / "daemon.sock", key, s, AGENT), AGENT)
        print(f"live-e2e: {AGENT} on a test daemon {d.hello.get('daemon')}, home {home}", flush=True)
        if args.terminal:
            terminal_turns(r, tmux, account["config_dir"], root, daemon)
        else:
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
        if tmux and args.keep:
            print(f"live-e2e: kept the tmux server (tmux -L {tmux}) and the worktree {root / 'work'}", file=sys.stderr)
        elif tmux:
            tmuxsock.stop(tmux)
            subprocess.run(["git", "worktree", "remove", "--force", str(root / "work")], cwd=REPO, capture_output=True)
        if args.keep:
            print(f"live-e2e: kept {root}", file=sys.stderr)
        else:
            shutil.rmtree(root, ignore_errors=True)
    if args.json:
        print(json.dumps(r.rows, indent=1))
    return 1 if any(row["result"] == "FAIL" for row in r.rows) else 0


if __name__ == "__main__":
    sys.exit(main())
