#!/usr/bin/env python3
"""Smoke the PC app's methods through the Go gateway: tools/gateway_smoke.py [--keep] [--port N] [--only NAME[,NAME]]

Starts a SCRATCH home (never the real one, never a credential): the Python daemon (`bin/hl daemon`) on a private tmux
server with a fake `claude`, the Go gateway (`handlined -gateway`) on 127.0.0.1, and a world to read and write: two
agents, a task, four requests waiting on the founder. It pairs a device on the gateway's own socket
(device.pair with the scratch home's founder key), trades the code for a token over the websocket (device.redeem),
reconnects with the token, calls each method the PC app uses with valid params, subscribes to the topics, attaches to a
terminal for bytes, and checks the gateway refuses an unpaired connection and a revoked device.

  python3 tools/gateway_smoke.py                       # the whole table, scratch home, prints the command to keep it
  python3 tools/gateway_smoke.py --start-only          # set the scratch world up, print how to pair a client, stay up
  python3 tools/gateway_smoke.py --readonly --url ws://HOST:PORT/rpc --token TOKEN
                                                       # reads only, against a gateway and a device the founder paired

The table says, per method: `pass` (the call returned a result), `refused` (the daemon answered with a refusal that is
its own answer to this call: `not_found`, `refused`, `bad_request` with a gate or a field: the path worked, the fake
world did not allow it), `FAIL` (the gateway, the relay or the transport failed: no reply, daemon_busy, gate role or
unpaired on a paired device, a malformed reply) or `missing`. Exit 0 when nothing FAILs.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import queue
import secrets
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = 1


# ---------------------------------------------------------------- clients
class Closed(Exception):
    pass


class Conn:
    """A JSON-RPC connection with a reader thread: replies by id, everything else (notifications) in `notes`."""

    def __init__(self) -> None:
        self.waiting: dict[int, queue.Queue] = {}
        self.notes: list[tuple[float, dict]] = []
        self.lock = threading.Lock()
        self.next_id = 1
        self.closed = threading.Event()

    def _deliver(self, msg: dict) -> None:
        mid = msg.get("id")
        with self.lock:
            q = self.waiting.pop(mid, None) if mid is not None else None
            if q is None:
                self.notes.append((time.time(), msg))
        if q is not None:
            q.put(msg)

    def call(self, method: str, params: dict | None = None, timeout: float = 15.0) -> dict:
        """The reply message ({result} or {error}); a dict with `transport` set when there was none."""
        with self.lock:
            mid = self.next_id
            self.next_id += 1
            q: queue.Queue = queue.Queue()
            self.waiting[mid] = q
        req = {"jsonrpc": "2.0", "id": mid, "method": method}
        if params is not None:
            req["params"] = params
        try:
            self._send(json.dumps(req))
        except OSError as e:
            return {"transport": f"send failed: {e}"}
        try:
            return q.get(timeout=timeout)
        except queue.Empty:
            return {"transport": "closed without a reply" if self.closed.is_set() else f"no reply in {timeout:.0f}s"}

    def notes_since(self, since: float, method: str | None = None) -> list[dict]:
        with self.lock:
            return [m for t, m in self.notes if t >= since and (method is None or m.get("method") == method)]

    def wait_note(self, since: float, pred, timeout: float = 10.0) -> dict | None:
        end = time.time() + timeout
        while time.time() < end:
            for m in self.notes_since(since):
                if pred(m):
                    return m
            if self.closed.is_set():
                break
            time.sleep(0.05)
        return None


class Line(Conn):
    """The gateway's own socket: one JSON object per line."""

    def __init__(self, path: str) -> None:
        super().__init__()
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(10)
        self.sock.connect(path)
        self.sock.settimeout(None)
        threading.Thread(target=self._read, daemon=True).start()

    def _send(self, text: str) -> None:
        self.sock.sendall(text.encode() + b"\n")

    def _read(self) -> None:
        buf = b""
        try:
            while True:
                chunk = self.sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        self._deliver(json.loads(line))
        except (OSError, ValueError):
            pass
        self.closed.set()

    def close(self) -> None:
        self.sock.close()


def ws_encode(opcode: int, payload: bytes) -> bytes:
    """A client frame: FIN, masked (RFC 6455 section 5.2)."""
    head = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([0x80 | n])
    elif n < 65536:
        head += bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        head += bytes([0x80 | 127]) + struct.pack(">Q", n)
    mask = os.urandom(4)
    return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


def ws_decode(read) -> tuple[int, bool, bytes]:
    """One server frame from `read(n)`: (opcode, fin, payload). Server frames are not masked."""
    b1, b2 = read(2)
    opcode, fin, n = b1 & 0x0F, bool(b1 & 0x80), b2 & 0x7F
    if n == 126:
        n = struct.unpack(">H", read(2))[0]
    elif n == 127:
        n = struct.unpack(">Q", read(8))[0]
    if b2 & 0x80:
        mask = read(4)
        return opcode, fin, bytes(b ^ mask[i % 4] for i, b in enumerate(read(n)))
    return opcode, fin, read(n)


class WS(Conn):
    """A websocket client on the standard library, with no Origin (a native client)."""

    def __init__(self, host: str, port: int, path: str = "/rpc") -> None:
        super().__init__()
        self.sock = socket.create_connection((host, port), timeout=10)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise Closed("closed during the handshake")
            head += chunk
        head, self.pending = head.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise Closed("not upgraded: " + head.split(b"\r\n", 1)[0].decode(errors="replace"))
        self.sock.settimeout(None)
        self.send_lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()

    def _recv_exact(self, n: int) -> bytes:
        out = b""
        while len(out) < n:
            if self.pending:
                take, self.pending = self.pending[:n - len(out)], self.pending[n - len(out):]
                out += take
                continue
            chunk = self.sock.recv(65536)
            if not chunk:
                raise Closed("closed")
            self.pending = chunk
        return out

    def _send(self, text: str) -> None:
        with self.send_lock:
            self.sock.sendall(ws_encode(0x1, text.encode()))

    def _read(self) -> None:
        text = b""
        try:
            while True:
                opcode, fin, payload = ws_decode(self._recv_exact)
                if opcode == 0x8:
                    break
                if opcode == 0x9:
                    with self.send_lock:
                        self.sock.sendall(ws_encode(0xA, payload))
                    continue
                if opcode in (0x1, 0x2, 0x0):
                    text += payload
                    if fin:
                        self._deliver(json.loads(text))
                        text = b""
        except (OSError, Closed, ValueError):
            pass
        self.closed.set()

    def close(self) -> None:
        try:
            with self.send_lock:
                self.sock.sendall(ws_encode(0x8, b""))
        except OSError:
            pass
        self.sock.close()


# ---------------------------------------------------------------- the scratch world
class World:
    """The scratch home and the processes on it."""

    def __init__(self, keep: bool, port: int | None) -> None:
        self.keep = keep
        self.root = Path(tempfile.mkdtemp(prefix="gwsmoke.")).resolve()
        self.home = self.root / "home"
        self.user = self.root / "user"
        self.port = port or free_port()
        self.procs: list[subprocess.Popen] = []
        self.seed: dict = {}
        self.env = {**os.environ, "HANDLINE_HOME": str(self.home), "OCP_HOME": str(self.home),
                    "HANDLINE_TEST_ROOT": str(self.user), "OCP_TEST_ROOT": str(self.user), "OCP_TERMINAL": "tmux",
                    "HANDLINE_TERMINAL": "tmux", "OCP_TMUX_SOCKET": f"gwsmoke{os.getpid()}", "HANDLINE_TMUX_SOCKET": f"gwsmoke{os.getpid()}",
                    "PATH": f"{self.root / 'bin'}:{os.environ['PATH']}", "SHELL": "/bin/sh"}
        for k in ("OCP_AGENT", "HANDLINE_AGENT", "OCP_ACCOUNT", "HANDLINE_ACCOUNT", "CLAUDE_CONFIG_DIR", "SANDBOX_RUNTIME"):
            self.env.pop(k, None)

    def log(self, name: str) -> Path:
        return self.root / f"{name}.log"

    def setup(self) -> None:
        for d in (self.home, self.user, self.root / "bin"):
            d.mkdir(parents=True, exist_ok=True)
        # A fake claude: it prints a banner and answers every line it is typed, so a terminal has bytes to show and to take.
        fake = self.root / "bin" / "claude"
        fake.write_text('#!/bin/sh\necho "fake claude ready"\nwhile IFS= read -r line; do echo "got: $line"; done\n')
        fake.chmod(0o755)
        repo = self.root / "repo"
        repo.mkdir()
        for args in (["init", "-q", "-b", "main"], ["-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--allow-empty", "-m", "x"]):
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
        self._seed_world(repo)

    def _seed_world(self, repo: Path) -> None:
        """The agents, an account that looks logged in (its credential file is only a placeholder), a task and requests for the
        founder, written by the handline code itself under the scratch environment."""
        code = r'''
import json, sys
sys.path.insert(0, %r)
from handline import actions, db
from handline.team import accounts, agents
home = %r
(__import__("pathlib").Path(home) / "team.yaml").write_text("""teams:
  smoke: [manager, worker]
agents:
  manager: {}
  worker: {}
edges:
  - { from: "*", to: "human", kind: task }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
  - { from: "manager", to: "worker", relation: supervisor }
  - { from: "worker", to: "manager", relation: subordinate }
deny: []
""")
agents.register("manager", role="Leads the smoke team.")
agents.register("worker", role="Does the smoke work.", repo=%r)
accounts.register("alpha")
cdir = __import__("pathlib").Path(home) / "accounts" / "alpha" / ".claude"
cdir.mkdir(parents=True, exist_ok=True)
(cdir / ".credentials.json").write_text("{}")
ok, msg, tid = actions.task_new("manager", "worker", "smoke task", "do it and show the result", timeout_s=3600)
assert ok, msg
con = db.connect()
say = db.add_request(con, "worker", db.HUMAN, "a report\nwith a second line", None, None, None, "say", 0, None)
con.execute("UPDATE requests SET landed_at = ts, state = 'landed' WHERE id = ?", (say,))
ask1 = db.add_request(con, "worker", db.HUMAN, "may I? (inbox.answer)", None, "subordinate", "async", "ask", 0, None)
ask2 = db.add_request(con, "worker", db.HUMAN, "may I? (chat.answer)", None, "subordinate", "async", "ask", 0, None)
ask3 = db.add_request(con, "worker", db.HUMAN, "may I? (inbox.reply)", None, "subordinate", "async", "ask", 0, None)
ask4 = db.add_request(con, "worker", db.HUMAN, "may I? (the inbox topic)", None, "subordinate", "async", "ask", 0, None)
con.close()
print(json.dumps({"task": tid, "say": say, "ask_inbox": ask1, "ask_chat": ask2, "ask_reply": ask3, "ask_topic": ask4}))
''' % (str(ROOT), str(self.home), str(repo))
        r = subprocess.run([sys.executable, "-c", code], env=self.env, capture_output=True, text=True, timeout=120)
        if r.returncode:
            raise SystemExit("could not seed the scratch world:\n" + r.stdout + r.stderr)
        self.seed = json.loads(r.stdout.strip().splitlines()[-1])
        self.seed["repo"] = str(repo)

    def start_daemon(self) -> None:
        out = open(self.log("python-daemon"), "w")
        p = subprocess.Popen([str(ROOT / "bin" / "hl"), "daemon", "--quiet"], env=self.env, stdout=out, stderr=subprocess.STDOUT, cwd=str(self.user))
        self.procs.append(p)
        self.wait_for(lambda: (self.home / "daemon.sock").exists() and (self.home / "relay.sock").exists(), 60, "the Python daemon's sockets", p, "python-daemon")

    def start_gateway(self) -> None:
        binary = self.root / "handlined"
        built = subprocess.run([str(ROOT / "tools" / "build-go.sh"), str(binary)], capture_output=True, text=True)
        if built.returncode:
            raise SystemExit("could not build the Go daemon:\n" + built.stdout + built.stderr)
        out = open(self.log("gateway"), "w")
        p = subprocess.Popen([str(binary), "-gateway", "-home", str(self.home), "-listen", f"127.0.0.1:{self.port}"], env=self.env, stdout=out, stderr=subprocess.STDOUT)
        self.procs.append(p)
        self.wait_for(lambda: (self.home / "gateway.sock").exists() and port_open(self.port), 30, "the gateway", p, "gateway")

    def wait_for(self, ok, seconds: float, what: str, proc: subprocess.Popen, log: str) -> None:
        end = time.time() + seconds
        while time.time() < end:
            if ok():
                return
            if proc.poll() is not None:
                break
            time.sleep(0.2)
        tail = "".join(self.log(log).read_text(errors="replace").splitlines(keepends=True)[-15:])
        raise SystemExit(f"{what} did not come up ({'exited ' + str(proc.returncode) if proc.poll() is not None else 'after ' + str(seconds) + 's'}); {log} log:\n{tail}")

    def founder_key(self) -> str:
        code = ("import sys\nsys.path.insert(0, %r)\nfrom handline.infra import sessiontoken\nsessiontoken.ensure_founder_key()\n"
                "print(sessiontoken.founder_key_held() or '')\n" % str(ROOT))
        r = subprocess.run([sys.executable, "-c", code], env=self.env, capture_output=True, text=True, timeout=60)
        key = r.stdout.strip()
        if not key:
            raise SystemExit("no founder key in the scratch home: " + r.stderr)
        return key

    def stop(self) -> None:
        for p in reversed(self.procs):
            p.terminate()
        for p in self.procs:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        subprocess.run(["tmux", "-L", self.env["OCP_TMUX_SOCKET"], "kill-server"], capture_output=True)
        if self.keep:
            print(f"kept {self.root} (logs: *.log)")
        else:
            shutil.rmtree(self.root, ignore_errors=True)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def port_open(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- the table
Row = tuple[str, str, str]      # method, verdict, detail

REFUSALS = ("refused", "not_found", "bad_request", "stale_prompt", "unavailable")


def verdict(reply: dict, allow_refusal: bool = False) -> tuple[str, str]:
    """pass / refused / FAIL for a reply. A refusal is the daemon's own answer; the transport failures are the gateway's."""
    if "transport" in reply:
        return "FAIL", reply["transport"]
    if "result" in reply:
        return "pass", ""
    err = reply.get("error") or {}
    data = err.get("data") or {}
    code = data.get("code") or err.get("message") or "?"
    detail = f"{code}" + (f" gate={data['gate']}" if data.get("gate") else "") + (f" field={data['field']}" if data.get("field") else "") + \
             (f": {data.get('reason') or err.get('message')}" if (data.get("reason") or err.get("message")) else "")
    if data.get("gate") in ("unpaired", "role") or code in ("daemon_busy", "internal", "unknown_method") or err.get("code") in (-32601, -32603):
        return "FAIL", detail
    return ("refused" if allow_refusal or code in REFUSALS else "FAIL"), detail


class Smoke:
    def __init__(self, conn: Conn, seed: dict, agent: str, writes: bool) -> None:
        self.c, self.seed, self.agent, self.writes = conn, seed, agent, writes
        self.rows: list[Row] = []
        self.ids = 0

    def rid(self) -> str:
        self.ids += 1
        return f"smoke-{os.getpid()}-{self.ids}-{secrets.token_hex(3)}"

    def row(self, method: str, params: dict | None, label: str | None = None, allow_refusal: bool = False) -> dict:
        reply = self.c.call(method, params)
        v, detail = verdict(reply, allow_refusal)
        self.rows.append((label or method, v, detail))
        return reply.get("result") or {}

    def note(self, label: str, v: str, detail: str = "") -> None:
        self.rows.append((label, v, detail))


def run_reads(s: Smoke) -> dict:
    """The reads the app opens with. Returns what later steps need."""
    agents = s.row("agents.list", {})
    names = [a.get("name") for a in agents.get("agents", [])]
    agent = s.agent if s.agent in names else (names[0] if names else s.agent)
    s.agent = agent
    s.row("agent.get", {"agent": agent})
    s.row("chat.messages", {"agent": agent, "scope": "mine"})
    s.row("chat.transcript", {"agent": agent})
    s.row("prompt.list", {"agent": agent})
    s.row("inbox.list", {})
    tasks = s.row("tasks.list", {})
    first = (tasks.get("items") or [{}])[0].get("id")
    if first is not None:
        s.row("task.get", {"task": first})
    else:
        s.note("task.get", "missing", "no task to read")
    return {"agent": agent, "task": first}


def run_writes(s: Smoke, world: dict) -> None:
    seed, agent, rid = s.seed, s.agent, s.rid
    sent = s.row("chat.send", {"agent": agent, "body": "smoke: hello", "request_id": rid()})
    req = sent.get("request")
    if isinstance(req, int):
        s.row("chat.send_now", {"request": req, "request_id": rid()})
    else:
        s.note("chat.send_now", "missing", "chat.send returned no request")
    s.row("chat.answer", {"request": seed["ask_chat"], "text": "yes (chat)", "request_id": rid()})
    s.row("prompt.answer", {"agent": agent, "prompt": "no-such-prompt", "choice": "1", "request_id": rid()}, allow_refusal=True)
    s.row("inbox.mark_read", {"ids": [seed["say"]], "request_id": rid()})
    s.row("inbox.answer", {"id": seed["ask_inbox"], "text": "yes (inbox)", "request_id": rid()})
    s.row("inbox.reply", {"id": seed["ask_reply"], "text": "reply from the smoke", "request_id": rid()})
    s.row("task.new", {"owner": agent, "title": "smoke task from the app", "body": "do it", "time_box": "1h", "request_id": rid()})
    s.row("task.memo.add", {"task": seed["task"], "text": "a note from the smoke", "request_id": rid()})
    s.row("task.done", {"task": seed["task"], "evidence": "smoke: done in words", "request_id": rid()})
    s.row("session.start", {"agent": agent, "repo": seed["repo"], "request_id": rid()})
    time.sleep(3)
    s.row("session.wake", {"agent": agent, "request_id": rid()})
    s.row("input.take", {"agent": agent, "request_id": rid()})
    s.row("session.input", {"agent": agent, "text": "smoke line", "request_id": rid()})
    s.row("session.keys", {"agent": agent, "keys": ["enter"], "request_id": rid()})
    s.row("input.release", {"agent": agent, "request_id": rid()})


def run_terminal(s: Smoke) -> None:
    since = time.time()
    snap = s.c.call("terminal.attach", {"agent": s.agent, "format": "bytes"})
    v, detail = verdict(snap)
    res = snap.get("result") or {}
    handle = res.get("terminal")
    if v == "pass" and not (isinstance(res.get("data"), str) and "cols" in res and "rows" in res):
        v, detail = "FAIL", f"not a bytes snapshot: keys {sorted(res)}"
    s.note("terminal.attach (bytes snapshot)", v, detail)
    if v != "pass" or not handle:
        s.note("terminal output frames", "missing", "no attach to watch")
        s.note("terminal.detach (bytes)", "missing", "no attach to end")
        return
    if s.writes:
        s.c.call("input.take", {"agent": s.agent, "request_id": s.rid()})
        s.c.call("terminal.input", {"terminal": handle, "data": base64.b64encode(b"echo smoke frame\n").decode(), "request_id": s.rid()})
    out = s.c.wait_note(since, lambda m: m.get("method") == "output" and (m.get("params") or {}).get("terminal") == handle, 10) if s.writes else None
    if not s.writes:
        s.note("terminal output frames", "missing", "read-only run types nothing")
    elif out and base64.b64decode(out["params"]["data"]):
        s.note("terminal output frames", "pass", f"seq {out['params'].get('seq')}")
    else:
        s.note("terminal output frames", "FAIL", "no output notice after typing")
    if s.writes:
        s.c.call("input.release", {"agent": s.agent, "request_id": s.rid()})
    reply = s.c.call("terminal.detach", {"terminal": handle, "format": "bytes"})
    s.note("terminal.detach (bytes)", *verdict(reply))


def run_topics(s: Smoke, triggers: dict[str, callable]) -> None:
    """Subscribe to each topic, make a change, and wait for an event of that topic."""
    spec_topics = ("agents", "chat", "inbox", "tasks")
    reply = s.c.call("subscribe", {"topics": list(spec_topics)})
    v, detail = verdict(reply)
    s.note("subscribe " + "+".join(spec_topics), v, detail)
    if v != "pass":
        return
    for topic in spec_topics:
        since = time.time()
        trigger = triggers.get(topic)
        if trigger is None:
            s.note(f"topic {topic}", "missing", "read-only run changes nothing")
            continue
        trigger()
        got = s.c.wait_note(since, lambda m, t=topic: m.get("method") == "event" and (m.get("params") or {}).get("topic") == t, 45 if topic in ("agents", "inbox") else 12)
        s.note(f"topic {topic}", "pass" if got else "FAIL", (got or {}).get("params", {}).get("type", "no event after a change"))
    s.note("topic prompts", "missing", "the spec has no prompts topic; a prompt change arrives as agent.changed on `agents`")
    s.c.call("unsubscribe", {"topics": list(spec_topics)})


def print_table(rows: list[Row]) -> int:
    width = max(len(r[0]) for r in rows)
    print(f"{'method'.ljust(width)}  result   detail")
    for m, v, detail in rows:
        print(f"{m.ljust(width)}  {v.ljust(7)}  {detail}")
    fails = sum(1 for r in rows if r[1] == "FAIL")
    print(f"\n{sum(1 for r in rows if r[1] == 'pass')} pass, {sum(1 for r in rows if r[1] == 'refused')} refused by the daemon (path worked), "
          f"{fails} FAIL, {sum(1 for r in rows if r[1] == 'missing')} missing")
    return 1 if fails else 0


# ---------------------------------------------------------------- the runs
def pair(world: World, key: str) -> tuple[str, str]:
    """(token, device id): device.pair on the gateway's socket, device.redeem over the websocket."""
    local = Line(str(world.home / "gateway.sock"))
    hello = local.call("hello", {"client": "gateway-smoke", "protocol": PROTOCOL, "key": key})
    if "result" not in hello:
        raise SystemExit(f"the gateway's socket refused the founder key: {hello}")
    paired = local.call("device.pair", {"name": "smoke", "request_id": secrets.token_hex(6)})
    code = (paired.get("result") or {}).get("code")
    if not code:
        raise SystemExit(f"device.pair gave no code: {paired}")
    anon = WS("127.0.0.1", world.port)
    redeemed = anon.call("device.redeem", {"code": code, "name": "smoke", "request_id": secrets.token_hex(6)})
    anon.close()
    token, device = (redeemed.get("result") or {}).get("token"), (redeemed.get("result") or {}).get("device")
    if not token:
        raise SystemExit(f"device.redeem gave no token: {redeemed}")
    pair.local = local
    return token, device


def connect(host: str, port: int, token: str) -> WS:
    ws = WS(host, port)
    hello = ws.call("hello", {"client": "gateway-smoke", "protocol": PROTOCOL, "token": token})
    if "result" not in hello:
        raise SystemExit(f"hello with the device token failed: {hello}")
    return ws


def refusal_checks(s: Smoke, world: World, device: str, token: str) -> None:
    anon = WS("127.0.0.1", world.port)
    reply = anon.call("agents.list", {})
    err = (reply.get("error") or {}).get("data") or {}
    s.note("unpaired connection refused", "pass" if err.get("gate") == "unpaired" else "FAIL", json.dumps(reply)[:120])
    anon.close()
    local: Line = pair.local
    revoked = local.call("device.revoke", {"device": device, "request_id": secrets.token_hex(6)})
    s.note("device.revoke (gateway socket)", *verdict(revoked))
    time.sleep(1)
    again = WS("127.0.0.1", world.port)
    reply = again.call("hello", {"client": "gateway-smoke", "protocol": PROTOCOL, "token": token})
    s.note("revoked device refused", "pass" if "error" in reply and not reply.get("result") else "FAIL", json.dumps(reply)[:120])
    again.close()
    s.note("revoked device's open connection closed", "pass" if s.c.closed.wait(5) else "FAIL", "")


def scratch_run(args) -> int:
    world = World(args.keep, args.port)
    try:
        world.setup()
        world.start_daemon()
        world.start_gateway()
        key = world.founder_key()
        token, device = pair(world, key)
        ws = connect("127.0.0.1", world.port, token)
        s = Smoke(ws, world.seed, "worker", writes=True)
        run_reads(s)
        run_writes(s, {})
        run_terminal(s)
        triggers = {
            "chat": lambda: ws.call("chat.send", {"agent": s.agent, "body": "smoke: topic chat", "request_id": s.rid()}),
            "tasks": lambda: ws.call("task.memo.add", {"task": world.seed["task"], "text": "topic tasks", "request_id": s.rid()}),
            "inbox": lambda: ws.call("inbox.answer", {"id": world.seed["ask_topic"], "text": "topic inbox", "request_id": s.rid()}),
            "agents": lambda: ws.call("chat.send", {"agent": s.agent, "body": "smoke: topic agents", "request_id": s.rid()}),
        }
        run_topics(s, triggers)
        s.row("session.stop", {"agent": s.agent, "mode": "close", "request_id": s.rid()})
        refusal_checks(s, world, device, token)
        rows = wanted(s.rows, args.only)
        print(f"scratch home {world.root}  gateway 127.0.0.1:{world.port}")
        return print_table(rows)
    finally:
        world.stop()


def start_only(args) -> int:
    world = World(True, args.port)
    world.setup()
    world.start_daemon()
    world.start_gateway()
    key = world.founder_key()
    code_conn = Line(str(world.home / "gateway.sock"))
    code_conn.call("hello", {"client": "gateway-smoke", "protocol": PROTOCOL, "key": key})
    paired = code_conn.call("device.pair", {"name": "app", "ttl_s": 600, "request_id": secrets.token_hex(6)}).get("result") or {}
    print(f"scratch home {world.root}\ngateway ws://127.0.0.1:{world.port}/rpc\npairing code (600 s, once): {paired.get('code')}\n"
          f"pairing uri: {paired.get('uri')}\nagents: manager, worker (repo {world.root / 'repo'})\n"
          f"stop with ctrl-c; the scratch home stays for you to read: {world.root}", flush=True)
    try:
        while all(p.poll() is None for p in world.procs):
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        world.keep = True
        world.stop()
    return 0


def readonly_run(args) -> int:
    if not args.url or not args.token:
        raise SystemExit("--readonly needs --url ws://HOST:PORT/rpc and --token TOKEN (a device the founder paired)")
    rest = args.url.split("://", 1)[-1]
    hostport, _, path = rest.partition("/")
    host, _, port = hostport.rpartition(":")
    ws = connect(host or hostport, int(port or 80), args.token)
    s = Smoke(ws, {}, args.agent or "", writes=False)
    run_reads(s)
    run_terminal(s)
    run_topics(s, {})
    ws.close()
    return print_table(wanted(s.rows, args.only))


def wanted(rows: list[Row], only: str | None) -> list[Row]:
    if not only:
        return rows
    names = {n.strip() for n in only.split(",")}
    return [r for r in rows if r[0].split(" ")[0] in names]


def selftest() -> int:
    """The frame code against itself: what encode makes decode reads back, masked or not."""
    for n in (0, 5, 125, 126, 300, 70000):
        data = os.urandom(n)
        frame = ws_encode(0x2, data)
        pos = [0]

        def read(k, f=frame, p=pos):
            out = f[p[0]:p[0] + k]
            p[0] += k
            return out
        assert ws_decode(read) == (0x2, True, data), n
    print("frames ok")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--keep", action="store_true", help="leave the scratch home and its logs")
    ap.add_argument("--port", type=int, help="the gateway's port (default: a free one)")
    ap.add_argument("--only", help="comma-separated method names to print")
    ap.add_argument("--start-only", action="store_true", help="set the scratch world up, print how to pair, stay up")
    ap.add_argument("--readonly", action="store_true", help="reads only, against --url and --token")
    ap.add_argument("--url"), ap.add_argument("--token"), ap.add_argument("--agent")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.readonly:
        return readonly_run(args)
    if args.start_only:
        return start_only(args)
    return scratch_run(args)


if __name__ == "__main__":
    sys.exit(main())
