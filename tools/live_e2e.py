#!/usr/bin/env python3
"""Drive one real Claude Code agent through the live daemon and time how fast the daemon's event stream reflects each step.

  tools/live-e2e.sh --account NAME --agent NAME [--json]

Spends real tokens: run it only on purpose, from the founder's own terminal (the founder key is hidden from agent sessions),
never from tools/suite.sh or check.sh. It drives an agent that already exists and never registers or removes one. It refuses
an agent whose model is not haiku, whose permission mode is not `default` (the permission step needs a real prompt), or
that is busy. The daemon picks the account a session starts on; when it picks another, the run moves the agent to
--account (agent.move) before the first turn.

It subscribes to the agents, tasks, chat and notifications topics and waits on each step's event with a deadline; nothing
is polled. A step the daemon reflects within 5 s is `pass`, later is `slow`, never is `missing`. A step paced by the model
(the answer, the agent's own message, the task closing) gets MODEL_S instead, and its time is mostly the model's. A missing
step is followed by one agent.get so the table says whether the thing happened without an event. A missing step is a
finding for the table, not a failure: exit 1 only when a step FAILs. A run cut short stops the session and discards its
task, so the next run's precheck finds the agent free.
"""
from __future__ import annotations

import argparse
import base64
import json
import socket
import struct
import sys
import threading
import time
import uuid
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from handline.infra import paths, sessiontoken  # noqa: E402
from handline.team import agents  # noqa: E402

DEADLINE_S = 5.0
SLOW_S = 30.0      # how long past the deadline a step still counts as slow rather than missing
MODEL_S = 120.0
TOPICS = ["agents", "tasks", "chat", "notifications"]


class Refused(Exception):
    pass


class Daemon:
    """One JSON-RPC connection to the daemon's socket, said hello to as the founder."""

    def __init__(self, key: str) -> None:
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.connect(str(paths.socket_file()))
        self.file = self.sock.makefile("rb")
        self.next_id = 0
        self.hello = self.call("hello", {"client": "live-e2e", "protocol": 1, "key": key})
        if self.hello.get("caller", {}).get("kind") != "founder":
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

    def __init__(self, key: str) -> None:
        self.conn = Daemon(key)
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


def solid_png(rgb: tuple[int, int, int], side: int = 32) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    rows = b"".join(b"\x00" + bytes(rgb) * side for _ in range(side))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


class Run:
    def __init__(self, daemon: Daemon, stream: Stream, agent: str) -> None:
        self.d, self.s, self.agent = daemon, stream, agent
        self.rows: list[dict] = []
        self.task: int | None = None  # the run's task while it is open

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

    def no_event(self, name: str, since: float) -> None:
        """A step the contract has no event for: missing, with what agent.get says happened."""
        self.record({"step": name, "paced_by": "daemon", "result": "missing", "seconds": None,
                     "note": "no event for it in the contract; " + self.explain(since)})

    def explain(self, since: float) -> str:
        v = self.d.call("agent.get", {"agent": self.agent})
        seen = ", ".join(self.s.states_since(self.agent, since)) or "none"
        return f"states seen: {seen}; agent.get: state {v.get('state')}, terminal {v.get('terminal')!r}, session_open {v.get('session_open')}"

    def clean_up(self) -> None:
        """After a run cut short: discard its open task and end the agent's session."""
        try:
            if self.task:
                self.d.write("task.discard", {"task": self.task, "reason": "live e2e run cut short"})
            if self.d.call("agent.get", {"agent": self.agent}).get("session_open"):
                self.d.write("session.stop", {"agent": self.agent, "mode": "exit"})
        except (Refused, OSError) as e:
            print(f"live-e2e: clean-up incomplete, check {self.agent} by hand: {e}", file=sys.stderr)

    def fail(self, name: str, why: str) -> None:
        self.record({"step": name, "paced_by": "daemon", "result": "FAIL", "seconds": None, "note": why})


def precheck(d: Daemon, agent: str) -> dict:
    view = d.call("agent.get", {"agent": agent})
    why = []
    if "haiku" not in (view.get("model") or ""):
        why.append(f"model is {view.get('model')!r}, not haiku")
    mode = agents.mode_of(agent, agents.read(agent))
    if mode != "default":
        why.append(f"permission mode is {mode!r}, not default")
    if view.get("state") not in ("idle", "no_terminal") or view.get("task_count"):
        why.append(f"it is not free: state {view.get('state')!r}, {view.get('task_count') or 0} open task(s)")
    if why:
        raise Refused(f"{agent}: " + "; ".join(why))
    return view


def lifecycle(r: Run, view: dict, account: str) -> None:
    d, a = r.d, r.agent
    if view.get("session_open"):  # start from no session, so the start itself is measured
        d.write("session.stop", {"agent": a, "mode": "exit"})
        if not r.s.wait(time.time() - 1, agent_changed(a, "no_terminal"), time.time() + 60):
            raise Refused(f"{a}: its open session did not exit within 60 s")

    t = time.time()
    d.write("session.start", {"agent": a})
    r.step("starting shown until ready", t, agent_changed(a, "starting"))
    r.step("ready (idle)", t, agent_changed(a, "idle"), model=True)
    r.no_event("terminal created", t)
    on = d.call("agent.get", {"agent": a}).get("account")
    if on != account:
        print(f"  moving {a} from {on} to {account}", flush=True)
        d.write("agent.move", {"agent": a, "account": account})
        moved = lambda ev: agent_changed(a, "idle")(ev) and ev["data"].get("account") == account
        if not r.s.wait(time.time(), moved, time.time() + MODEL_S):
            raise Refused(f"{a}: not idle on {account} within {MODEL_S:.0f} s of agent.move")

    t = time.time()
    d.write("session.input", {"agent": a, "text": "Reply with the single word PONG and nothing else."})
    r.step("working", t, agent_changed(a, "running"))
    r.step("idle when done", t, agent_changed(a, "idle"), model=True)
    r.step("text typed", t, lambda ev: ev["type"] == "agent.control" and ev.get("agent") == a)


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
    r.step("founder message landed in the agent's session", t,
           lambda ev: ev["type"] == "chat.message" and ev["data"].get("id") == req and bool(ev["data"].get("landed_at")))
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
    task = d.write("task.new", {"owner": a, "title": "live e2e: close this task",
                                "body": 'Close this task with hl task done, evidence "e2e", and do nothing else.'}).get("task")
    r.task = task
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
        # a terminal prompt takes yes; a structured one offers its own keys (allow, deny), so take the one that answers yes
        choice = next((o["key"] for o in open_prompt.get("options") or [] if o.get("answer") == "yes"), "yes")
        d.write("prompt.answer", {"agent": a, "prompt": open_prompt["id"], "choice": choice})
    closed = r.step("task done", t, is_task("done", "reported"), model=True)
    if closed and closed["data"].get("state") == "reported":
        d.write("task.accept", {"task": task})
    if closed:
        r.task = None
    r.s.wait(t, agent_changed(a, "idle"), time.time() + MODEL_S)


def kill_and_exit(r: Run) -> None:
    d, a = r.d, r.agent
    t = time.time()
    d.write("session.input", {"agent": a, "text": "Write the numbers from 1 to 3000, one per line, with no tools."})
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
    t = time.time()
    d.write("terminal.close", {"agent": a})
    r.no_event("terminal closed", t)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--account", required=True, help="the test account: the agent is moved there when the daemon starts it elsewhere")
    ap.add_argument("--agent", required=True, help="an existing haiku agent in permission mode default")
    ap.add_argument("--json", action="store_true", help="print the table as JSON at the end")
    args = ap.parse_args()

    key = sessiontoken.founder_key_held()
    if not key:
        print("live-e2e: no founder key here; run it from the founder's own terminal, not an agent session", file=sys.stderr)
        return 2
    r = None
    try:
        d = Daemon(key)
        view = precheck(d, args.agent)
        r = Run(d, Stream(key), args.agent)
        print(f"live-e2e: {args.agent} on {args.account}, daemon {d.hello.get('daemon')}")
        lifecycle(r, view, args.account)
        chat_and_attachment(r)
        task_and_permission(r)
        kill_and_exit(r)
    except (Refused, OSError, KeyboardInterrupt) as e:
        print(f"live-e2e: {e or 'interrupted'}", file=sys.stderr)
        if r:
            r.clean_up()
        return 2
    if args.json:
        print(json.dumps(r.rows, indent=1))
    return 1 if any(row["result"] == "FAIL" for row in r.rows) else 0


if __name__ == "__main__":
    sys.exit(main())
