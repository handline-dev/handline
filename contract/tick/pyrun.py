#!/usr/bin/env python3
"""Run one tick fixture through the Python daemon and print the output as JSON.

  python3 contract/tick/pyrun.py FIXTURE.json > out.json

The format of both files is in contract/tick/README.md. Every input the tick reads is taken from
the fixture or injected here; whatever it reaches for that the fixture does not provide is
refused and listed under `unscripted`, so a stray read shows up as data, not as flakiness.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HOME_TOKEN = "{HOME}"
VOLATILE_TABLES = ("sqlite_sequence",)


class Clock:
    """time.time, time.monotonic and time.sleep on one fake line: sleeping moves it, nothing waits."""

    def __init__(self, start: float) -> None:
        self.now = float(start)

    def time(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.now += max(0.0, s)


class Scripted:
    """The fixture's terminal host. Built in `build_backend`, after handline is importable."""


def session_entry(spec: dict, handle: str, processes: list) -> dict:
    """A process-table row for a session a scripted terminal starts: a pid after the ones already there."""
    pid = spec.get("pid") or 5000 + sum(1 for p in processes if p["pid"] >= 5000)
    return {"command": "claude", **spec, "pid": pid, "terminal": handle}


def build_backend(spec: dict, clock: Clock, ops: list, processes: list):
    from handline.session.backends import base

    class ScriptedBackend(base.Backend):
        name = "scripted"
        supports_runs = supports_labels = supports_agent_hooks = False

        def __init__(self) -> None:
            self.terms = {h: dict(t) for h, t in spec.get("terminals", {}).items()}
            for t in self.terms.values():
                t["screen"] = list(t.get("screen", []))
            self.new = list(spec.get("new_terminals", []))
            self.worktrees = list(spec.get("new_worktrees", []))
            self.up = spec.get("host_up", True)

        def _op(self, op: str, handle: str | None, **a) -> None:
            ops.append({"op": op, "handle": handle, **a})

        def available(self) -> bool:
            return self.up

        def own_terminal(self):
            return None, None

        def knows_handle(self, handle: str) -> bool:
            return True

        def create_terminal(self, command, cwd=None, title=None):
            if not self.new:
                self._op("create_terminal", None, command=command, cwd=cwd, result="refused")
                return None, "scripted: no terminal left to hand out"
            new = self.new.pop(0)
            h, extra = (new, {}) if isinstance(new, str) else (new["handle"], new)
            self.terms[h] = {"screen": list(extra.get("screen", [])), "on_send": [dict(r) for r in extra.get("on_send", [])]}
            if extra.get("starts_session"):
                self._launch_takes_its_prompt(command)
                processes.append(session_entry(extra["starts_session"], h, processes))
            self._op("create_terminal", h, command=command, cwd=cwd)
            return h, ""

        @staticmethod
        def _launch_takes_its_prompt(command: str) -> None:
            """`hl launch --prompt-file F` reads F and removes it: that is how the daemon knows its own launch started the session."""
            if "--prompt-file " in command:
                import shlex
                from handline.session import launch
                launch.take_prompt(shlex.split(command.split("--prompt-file ")[1])[0])

        def close_terminal(self, handle, tab=True):
            self._op("close_terminal", handle)
            self.terms.pop(handle, None)
            return True, "closed"

        def list_terminals(self):
            return set(self.terms) if self.up else None

        def read_screen(self, handle, limit=40):
            t = self.terms.get(handle)
            if t is None or not self.up or t.get("unreadable"):
                return None
            return {"tail": t["screen"][-limit:], "source": "screen"}

        def send(self, handle, text, enter=True, wait_submit_s=None):
            self._op("send", handle, text=text, enter=bool(enter))
            t = self.terms.get(handle)
            if t is None:
                return False, "scripted: no such terminal"
            for rule in t.get("on_send", []):
                if (rule.get("text") == text or ("contains" in rule and rule["contains"] in text)) and not rule.get("used"):
                    if not rule.get("repeat"):
                        rule["used"] = True
                    if "screen" in rule:
                        t["screen"] = list(rule["screen"])
                    if rule.get("ends_session"):
                        processes[:] = [p for p in processes if p.get("terminal") != handle]
                    if rule.get("starts_session"):
                        processes.append(session_entry(rule["starts_session"], handle, processes))
                    break
            return (not t.get("refuse_send")), ("" if not t.get("refuse_send") else "scripted: refused")

        def wait_idle(self, handle, timeout_ms, turn_on_screen=None):
            t = self.terms.get(handle)
            idle = bool(t.get("idle", True)) if t else False
            self._op("wait_idle", handle, result=idle)
            clock.sleep(0 if idle else timeout_ms / 1000)
            return idle

        def focus(self, handle):
            self._op("focus", handle)
            return True, ""

        def ensure_repo(self, path, present=True):
            return "known", ""

        def create_worktree(self, repo, name, base_ref):
            if not self.worktrees:
                self._op("create_worktree", None, repo=repo, name=name, base=base_ref, result="refused")
                return None, "scripted: no worktrees"
            path = self.worktrees.pop(0)
            os.makedirs(path, exist_ok=True)
            self._op("create_worktree", None, repo=repo, name=name, base=base_ref, path=path)
            return path, ""

    return ScriptedBackend()


def dump_db(con) -> dict[str, list[dict]]:
    out = {}
    for (name,) in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name"):
        cur = con.execute(f'select rowid as "__rowid", * from "{name}" order by rowid')
        cols = [c[0] for c in cur.description]
        out[name] = [dict(zip(cols, r)) for r in cur.fetchall()]
    return out


def diff_db(before: dict, after: dict) -> dict:
    """Per table: rows added, rows removed, and the changed columns of rows kept (keyed by rowid)."""
    out = {}
    for t in sorted(set(before) | set(after)):
        b = {r["__rowid"]: r for r in before.get(t, [])}
        a = {r["__rowid"]: r for r in after.get(t, [])}
        d = {}
        added = [a[k] for k in sorted(a) if k not in b]
        removed = [b[k] for k in sorted(b) if k not in a]
        changed = []
        for k in sorted(a):
            if k in b and a[k] != b[k]:
                changed.append({"rowid": k, "before": {c: b[k].get(c) for c in a[k] if a[k][c] != b[k].get(c)},
                                "after": {c: a[k][c] for c in a[k] if a[k][c] != b[k].get(c)}})
        for key, rows in (("added", added), ("removed", removed), ("changed", changed)):
            if rows:
                d[key] = rows
        if d:
            out[t] = d
    return out


def _sub(value, home: str, back: bool = False):
    """{HOME} in, the real temp path out, in every string of a structure."""
    a, b = (home, HOME_TOKEN) if back else (HOME_TOKEN, home)
    if isinstance(value, str):
        return value.replace(a, b)
    if isinstance(value, list):
        return [_sub(v, home, back) for v in value]
    if isinstance(value, dict):
        return {k: _sub(v, home, back) for k, v in value.items()}
    return value


def run(fixture: dict) -> dict:
    tmp = tempfile.mkdtemp(prefix="tick-")
    try:
        return _run(fixture, Path(tmp).resolve())
    finally:
        shutil.rmtree(tmp, ignore_errors=True)      # one per fixture run: left behind, they filled the disk


def _run(fixture: dict, root: Path) -> dict:
    home = root / ".handline-tick"
    home.mkdir()
    os.environ.update(TZ="UTC", OCP_TEST_ROOT=str(root), OCP_HOME=str(home), HANDLINE_HOME=str(home), LANG="C", LC_ALL="C")
    for k in [k for k in os.environ if k.startswith(("HANDLINE_", "OCP_")) and k not in ("OCP_TEST_ROOT", "OCP_HOME", "HANDLINE_HOME")]:
        del os.environ[k]
    time.tzset()
    sys.path.insert(0, str(ROOT))
    fx = _sub(fixture, str(home))
    clock_spec = fx["clock"]
    clock = Clock(clock_spec["start"])
    unscripted: list[dict] = []
    ops: list[dict] = []
    unreadable = fx.get("processes", []) is None     # the process table could not be read
    processes: list[dict] = [dict(p) for p in (fx.get("processes") or [])]

    # ---- clock, ids, subprocesses
    time.time, time.monotonic, time.sleep = clock.time, clock.time, clock.sleep
    counter = {"n": 0}

    def uuid4():
        counter["n"] += 1
        return uuid.UUID(int=counter["n"])
    uuid.uuid4 = uuid4

    real_run, real_popen = subprocess.run, subprocess.Popen
    commands = fx.get("commands", [])
    os.makedirs(root / "bin", exist_ok=True)
    (root / "bin" / "claude").write_text("#!/bin/sh\nexit 0\n")
    (root / "bin" / "claude").chmod(0o755)
    os.environ["PATH"] = str(root / "bin")

    def fake_run(*a, **kw):
        argv = a[0] if a else kw.get("args")
        call = [argv] if isinstance(argv, str) else [os.path.basename(str(argv[0])), *map(str, argv[1:])]
        for c in commands:
            if call[:len(c["argv"])] == c["argv"]:
                out, err = c.get("out", ""), c.get("err", "")
                if not kw.get("text") and not kw.get("universal_newlines"):
                    out, err = out.encode(), err.encode()
                return subprocess.CompletedProcess(argv, c.get("rc", 0), out, err)
        unscripted.append({"input": "subprocess", "call": call,
                           "at": [f"{f.filename.rsplit('handline/', 1)[-1]}:{f.lineno}" for f in __import__("traceback").extract_stack()[-6:-1]]})
        raise OSError("tick harness: this command is not scripted")
    subprocess.run = fake_run

    class NoPopen:
        def __init__(self, *a, **kw):
            fake_run(*a, **kw)
            raise OSError("tick harness: Popen is not scripted")
    subprocess.Popen = NoPopen

    # The clock's other doors: a bare strftime/localtime reads the real clock, and fold asks datetime.
    real_localtime, real_gmtime, real_strftime = time.localtime, time.gmtime, time.strftime
    time.localtime = lambda t=None: real_localtime(clock.now if t is None else t)
    time.gmtime = lambda t=None: real_gmtime(clock.now if t is None else t)
    time.strftime = lambda fmt, t=None: real_strftime(fmt, real_localtime(clock.now) if t is None else t)

    from handline import db, vendors
    from handline.infra import sealed
    from handline.session import backends, observe as obs
    from handline.team import accounts
    from handline.usage import status as _status  # noqa: F401
    from handline.vendors import claude_meter
    from handline.daemon import evaluate, delivery, sends, subscriptions
    from handline.session import fold as foldmod
    import datetime as _dt

    class FakeDT(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(clock.now, tz)
    foldmod.datetime = FakeDT
    sends.RUN_INLINE = True

    # ---- the world: files, seals, db
    for rel in fx.get("dirs", []):
        (home / rel).mkdir(parents=True, exist_ok=True)
    for rel, text in fx.get("files", {}).items():
        p = home / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    if (home / "team.yaml").exists():
        sealed.seal(sealed.TEAM, (home / "team.yaml").read_text())
    for p in sorted(home.glob("agents/*/agent.yaml")):
        sealed.seal(sealed.agent_key(p.parent.name), p.read_text())
    con = db.connect()
    for table, rows in fx.get("db", {}).items():
        for row in rows:
            cols = [c for c in row if c != "__rowid"]
            names = (["rowid"] if "__rowid" in row else []) + cols
            vals = ([row["__rowid"]] if "__rowid" in row else []) + [row[c] for c in cols]
            con.execute(f'insert or replace into "{table}" ({",".join(chr(34) + n + chr(34) for n in names)}) values ({",".join("?" * len(names))})', vals)
    con.commit()

    # ---- the other inputs: process table, logins, git, meters, terminal host
    def env_text(pid: int):
        for p in processes:
            if p["pid"] == pid:
                env = {"OCP_AGENT": p["agent"]}
                if p.get("account"):
                    env[vendors.get("claude").config_dir_env()] = str(vendors.config_dir(p["account"]))
                return " ".join(f"{k}={v}" for k, v in env.items())
        return None
    obs.process_table = lambda: None if unreadable else [(p["pid"], p.get("command", "claude")) for p in processes]
    obs._env_or_reason = lambda pid: (env_text(pid), False)
    obs.process_env = env_text
    accounts.logged_in = lambda acct, *a, **k: bool(fx.get("logged_in", {}).get(acct, True))
    claude_meter._access_token_detailed = lambda acct: (None, False)
    claude_meter.refresh_credential = lambda acct, timeout=0: False

    backend = build_backend(fx, clock, ops, processes)
    backends.use(backend)
    delivery.END_WAIT_S, delivery.END_POLL_S = 0.3, 0.05

    # ---- ticks
    log: list[str] = []
    states = [dump_db(con)]
    out = {"format": 1, "name": fx["name"], "ticks": []}
    for i in range(clock_spec.get("ticks", 1)):
        if i:
            clock.now = clock_spec["start"] + i * clock_spec.get("step_s", 30)
        n_ops, n_log = len(ops), len(log)
        t = evaluate.tick(con, log.append)
        subscriptions.wait_for_plan_ask()
        con.commit()
        states.append(dump_db(con))
        out["ticks"].append({
            "now": t.now, "actions": list(t.actions),
            "db_diff": diff_db(states[-2], states[-1]),
            "terminal_ops": ops[n_ops:],
            "log": log[n_log:],
        })
    out["unscripted"] = unscripted
    con.close()
    subprocess.run, subprocess.Popen = real_run, real_popen
    time.localtime, time.gmtime, time.strftime = real_localtime, real_gmtime, real_strftime
    return _sub(out, str(home), back=True)


def main(argv: list[str]) -> int:
    fixture = json.loads(Path(argv[0]).read_text())
    json.dump(run(fixture), sys.stdout, indent=1, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
