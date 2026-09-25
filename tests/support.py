"""Shared fixtures and harness for the area test files (test_<area>.py).

Not a test file: every area file does `from support import *` and runs on its
own; `python3 tests/test_ocp.py` runs them all.
"""
from __future__ import annotations

import argparse
import atexit
import contextlib
from datetime import datetime
import hashlib
import http.client
import http.server
import io
import json
import os
import pty
import re
import select
import shlex
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ocp import paths  # noqa: E402

# The machine's venv, read once before the stand-in below hides the real home:
# textual is only installed there (see _python_with_textual).
MACHINE_VENV = paths.default_home() / "venv"

# Before anything else imports ocp: this process and every child it starts
# see a temp directory as the user's home, and paths.home() refuses the real
# ~/.orcapipe. An agent's session exports OCP_HOME=~/.orcapipe; a test that
# ran against it once deleted the team's whole state.
TEST_ROOT = Path(tempfile.mkdtemp(prefix="ocp-test-root-"))
os.environ[paths.TEST_ROOT_ENV] = str(TEST_ROOT)
os.environ["OCP_HOME"] = str(TEST_ROOT / ".orcapipe")
atexit.register(shutil.rmtree, TEST_ROOT, True)

from ocp.cli import (account as cliaccount, ask as cliask, bridge as clibridge, daemon as clidaemon,  # noqa: E402
                     health as clihealth, setup as clisetup, task as clitask)
from ocp import (accounts, actions, agents, ask as askmod, blocked as blockedmod, burnrate, cli as climod, collect, db, hr,
                 digest,
                 doctor, drift, events as evmod, fold as foldmod,
                 handoff as handoffmod, idle_close, jobs as jobsmod, launch, load as loadmod, lock, measure,
                 observe as obsmod, orca as orcamod, paths, recover as recovermod, rehome,
                 replay as replaymod, service,
                 spawn as spawnmod,
                 status, team, textsafe, timefmt, triggers, trust as trustmod, tui, usage_api, vendors,
                 venv as venvmod, waiting,
                 yamlcompat)  # noqa: E402
from ocp.daemon import (delivery as dmn_delivery, devmode as dmn_devmode, evaluate as dmn_evaluate,  # noqa: E402
                        handoff as dmn_handoff, lifecycle as dmn_lifecycle, locks as dmn_locks,
                        loop as dmn_loop, reconciler as dmn_reconciler, states as dmn_states,
                        usage_poll as dmn_usage_poll)
from ocp.vendors import opencode as ocmod  # noqa: E402

PASS, FAIL = [], []

# A stand-in `close_session` that does not also end the session in the fake
# process table would otherwise wait out the real minute before failing.
dmn_delivery.END_WAIT_S, dmn_delivery.END_POLL_S = 0.3, 0.05

# No test reaches the machine's Keychain or the usage endpoint: asked for a
# reading, it gets "could not look". A test that drives usage_api sets its own.
REAL_ACCESS_TOKEN = usage_api._access_token_detailed
usage_api._access_token_detailed = lambda acct: (None, False)
usage_api.refresh_credential = lambda acct, timeout=0: False

# Every test that needs an Orca stands in for one, and a restore tuple that
# forgot a name used to leak that stand-in into every test after it. Captured
# once, put back by _daemon_home, so no test inherits another's world.
REAL_ORCA = {n: getattr(orcamod, n) for n in (
    "available", "alive", "live_terminals", "wait_idle", "idle_now", "send",
    "clear_context", "close_session", "relaunch", "resume_with", "modal_open",
    "screen_blocker", "_screen", "decline_dialog", "background_work")}
REAL_OBSERVE = {n: getattr(obsmod, n) for n in ("live_sessions", "live_terminals")
                if hasattr(obsmod, n)}


CURRENT_TEST: dict[str, str] = {"name": "<setup>"}

# A stand-in `orca` at the front of PATH for this process and every child of it:
# it records which test ran it and does nothing else, and exits 1 the way an Orca
# that is not answering does. A test that stands in for what it calls never
# reaches it. The four that run the real `ocp` as a child process do, and are
# named below; any other test that reaches it is a test that has not stood in
# for something, and the run ends on it.
#
# Two layers, not one, and neither is redundant. _guarded_popen_init below
# catches a call in THIS process the moment it is made, naming the test before
# anything is spent - but it patches subprocess.Popen.__init__, so a test that
# replaces subprocess.run wholesale (two do, for reasons unrelated to Orca)
# goes blind to that layer for as long as the replacement stands: nothing
# calls Popen, so nothing is caught. This PATH stand-in is what still would,
# for anything that actually execs `orca`, however it got there: PATH is set
# once here at import, nothing in the suite clears or replaces it, and no
# env-building site in ocp/ drops it from a child's environment either.
FAKE_ORCA_DIR = Path(tempfile.mkdtemp(prefix="ocp-fake-orca-"))
FAKE_ORCA_LOG = FAKE_ORCA_DIR / "calls.log"
(FAKE_ORCA_DIR / "orca").write_text(
    "#!/bin/sh\n"
    # tr keeps one call to one line: an argument carrying a newline would
    # otherwise split into a phantom log entry and redden the run on nothing.
    "args=$(printf '%s' \"$*\" | tr '\\n\\t' '  ')\n"
    "printf '%s\\t%s\\n' \"${OCP_TEST:-<none>}\" \"$args\" >> LOGFILE\n"
    "exit 1\n".replace("LOGFILE", f'"{FAKE_ORCA_LOG}"'))
(FAKE_ORCA_DIR / "orca").chmod(0o755)
os.environ["PATH"] = str(FAKE_ORCA_DIR) + os.pathsep + os.environ.get("PATH", "")
atexit.register(shutil.rmtree, FAKE_ORCA_DIR, True)

# Tests whose subject is a child `ocp` process (a launch, the bridge on a pty),
# which asks Orca for its terminals and its Run. They reach the stand-in, never
# the real one.
ORCA_CHILD_TESTS = {"test_launch", "test_launch_refuses_stale",
                    "test_every_prompt_leaves_the_screen_above_it_drawn_through_tui_run",
                    "test_the_composer_and_the_fill_on_a_real_screen_through_tui_run"}


def calls_that_reached_the_stand_in_orca() -> dict[str, list[str]]:
    """The calls the fake `orca` recorded, by the test that made them."""
    out: dict[str, list[str]] = {}
    if FAKE_ORCA_LOG.exists():
        for line in FAKE_ORCA_LOG.read_text().splitlines():
            test, _, call = line.partition("\t")
            out.setdefault(test, []).append(call)
    return out

_REAL_POPEN_INIT = subprocess.Popen.__init__
# Every `orca` a test tried to start in this process, kept by the guard before it
# raises, so a test that catches the error still leaves the run failing.
ORCA_ATTEMPTS: list[tuple[str, str]] = []


def _is_orca(args) -> bool:
    first = args if isinstance(args, str) else (args[0] if args else "")
    return os.path.basename(str(first).split()[0] if str(first).split() else "") == "orca"


def _guarded_popen_init(self, args, *a, **kw):
    """Every subprocess in this process starts here. One that runs `orca` is a
    test that has not stood in for what it calls: it would type at, list, create
    or wait on the real Orca of whoever runs the suite. It raises, naming the
    test and the call. (Code that catches Exception can swallow this; the fake
    `orca` main() puts on PATH is what still sees it, and the run ends on it.)"""
    if _is_orca(args):
        argv = args if isinstance(args, str) else " ".join(str(x) for x in args)
        ORCA_ATTEMPTS.append((CURRENT_TEST["name"], argv))
        ours = [f for f in traceback.extract_stack() if "/ocp/" in f.filename or "/tests/" in f.filename]
        via = " (via " + " > ".join(f"{Path(f.filename).name}:{f.lineno} {f.name}" for f in ours[-6:-1]) + ")" if ours else ""
        raise AssertionError(f"{CURRENT_TEST['name']} would have reached the real Orca: "
                            f"{argv}{via}")
    _REAL_POPEN_INIT(self, args, *a, **kw)


REAL_LOGGED_IN = accounts.logged_in


def restore_world() -> None:
    subprocess.Popen.__init__ = _guarded_popen_init
    accounts.logged_in = REAL_LOGGED_IN
    for n, f in REAL_ORCA.items():
        setattr(orcamod, n, f)
    # This process itself may be running inside an Orca-launched terminal -
    # left set, `orca.terminal_handle()` reads the real thing, and a test that
    # calls `launch._record_terminal` without setting its own value first
    # reaches the real `orca` binary through ensure_run(). ORCA_WORKTREE_ID is
    # read on the same line (`db.set_terminal`'s worktree argument) and is
    # exactly the same class of leak, just not yet the cause of a failure a
    # real terminal's ambient value could still one day produce (auditor
    # #1662 NOTE 2). A test that wants either sets it itself, and pops it
    # again when done, the same as every other piece of state this resets.
    for var in ("ORCA_TERMINAL_HANDLE", "ORCA_WORKTREE_ID"):
        os.environ.pop(var, None)
    # No screen to read in a fixture; a test about a modal says so itself.
    orcamod.modal_open = lambda handle: False
    orcamod.screen_blocker = lambda handle: None
    # screen_blocker_cached wraps whatever screen_blocker is at call time, but
    # the cache itself is keyed by handle and outlives the function it wraps -
    # most fixtures reuse "term_x", so a value cached by one test would answer
    # the next one's read of the same handle without this.
    orcamod._screen_cache.clear()
    # And no screen to read for a key either: a test that presses one says so.
    orcamod._screen = lambda handle: None
    orcamod.PRESS_SETTLE_S = 0
    blockedmod._checked.clear()
    for n, f in REAL_OBSERVE.items():
        setattr(obsmod, n, f)
    # Readings that outlive the tick or the frame by design, so one test's
    # world does not answer the next test's question.
    dmn_delivery._seen.clear()
    dmn_delivery._seen_at["t"] = 0.0
    actions._WORLD["at"] = 0.0


def _page_text(page: dict, width: int) -> str:
    """Everything an opened page draws at `width`: a conversation page is one
    stack of sections, any other has a tab for each and is read tab by tab."""
    if page.get("stacked"):
        return "\n".join(tui.lines_of(page, width))
    out: list[str] = []
    for i in range(len(tui._tab_stems(page))):
        page["tab"] = i
        out += tui.lines_of(page, width)
    return "\n".join(out)


def _snap(*_geometry, **_cursor) -> str:
    """The bridge's `--once` text. Screen geometry and cursor arguments are
    accepted and ignored: what the tests read is what it shows, not how."""
    return tui.snapshot()


def _set_request_state(con, rid: int, state: str, verdict: str | None = None,
                       announced: bool = False) -> None:
    """Force a request row to any state, for setup only - never a production
    closer, so it carries none of `ocp/db/`'s chokepoint guard (#2024): tests
    that need to reach a row's own defense-in-depth past that guard (past
    `_would_strand_a_proposal`, for one) use this to get there."""
    now = int(time.time())
    con.execute(
        "UPDATE requests SET state = ?, verdict = COALESCE(?, verdict), "
        "resolved_at = COALESCE(resolved_at, ?), "
        "announced_at = COALESCE(announced_at, ?) WHERE id = ?",
        (state, verdict, now if state != "queued" else None,
         now if announced else None, rid))


def relaunching_send(sent: list, table: dict[str, int]) -> object:
    """An orca.send that records, and starts a session when it is a relaunch.

    The daemon wakes a closed agent by typing its launch command into the shell
    its session left behind, and everything it sends after that is sent to the
    session that command started - so a fixture where the process table never
    changes is a fixture where waking does not work.
    """
    def send(handle: str, text: str, enter: bool = True):
        sent.append(text)
        if " launch " in text:
            table[text.split(" launch ")[1].split()[0]] = 4242
            # The wake prompt rides the launch as a file the launch takes, not
            # as a line typed afterwards; listed here as what the session is
            # handed on start, so tests can assert on what it was told.
            if "--prompt-file " in text:
                sent.append(take_launched_prompt(text))
        elif text == "/exit":
            table.clear()       # one terminal, one agent, in every fixture here
        return True, ""
    return send


_taken_prompts: dict[str, str] = {}


def take_launched_prompt(command: str) -> str | None:
    """What a launch command's session starts with, taken as `ocp launch`
    takes it: the file is gone afterwards, which is how the daemon knows its
    own launch started the session."""
    if "--prompt-file " not in command:
        return None
    text = launch.take_prompt(shlex.split(command.split("--prompt-file ")[1])[0])
    _taken_prompts[command] = text or ""
    return text


def launched_prompt(command: str) -> str:
    """The first prompt a launch command hands its session, taken or not; ""
    for one the daemon removed because nothing took it."""
    f = Path(shlex.split(command.split("--prompt-file ")[1])[0])
    return f.read_text() if f.exists() else _taken_prompts.get(command, "")


def sessions_are(*agents: str) -> dict[str, int]:
    """Say which agents have a session running, as the process table would.

    Returned so a test can end one - `table.pop("backend")` - or start one,
    which is what closing and relaunching do to the world the daemon reads.
    """
    table = {a: 4200 + i for i, a in enumerate(agents)}
    obsmod.live_sessions = lambda: dict(table)
    _SESSION_TABLE["t"] = table
    return table


_SESSION_TABLE: dict = {"t": {}}


def exit_ends_session(agent: str):
    """A `close_session` stand-in whose `/exit` takes: the session leaves the
    process table, which is what the daemon waits to see before it marks a
    close."""
    def close(h, **kw):
        _SESSION_TABLE["t"].pop(agent, None)
        return True, "/exit typed"
    return close


@contextlib.contextmanager
def _deadline(seconds: int):
    """Fail a test that hangs instead of hanging the suite with it."""
    def blew(_sig, _frm):
        raise TimeoutError(f"still running after {seconds}s")
    old = signal.signal(signal.SIGALRM, blew)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def _capture(fn) -> str:
    """What a command printed, for asserting on what a human would see."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn()
    return buf.getvalue()


LABEL_LOG: list[tuple[str, str, str]] = []  # (state, test, name) - check() names alone
# collide across tests (e.g. two tests both say "wake: the relaunch itself is
# reported as a success"); write_labels() below qualifies by test to tell them
# apart. A pure addition alongside PASS/FAIL, so it changes no existing output.


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    LABEL_LOG.append(("PASS" if cond else "FAIL", CURRENT_TEST["name"], name))
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not cond else ""))


def write_status(home: Path, acct: str, **fields) -> None:
    d = home / "accounts" / acct
    d.mkdir(parents=True, exist_ok=True)
    payload = {"account": acct, "collected_at": int(time.time()), "session_id": "s"}
    payload.update(fields)
    (d / "status.json").write_text(json.dumps(payload))


def readable_agent(con, home: Path, agent: str) -> None:
    """Give `agent` the three things a handoff reads, so it comes out whole."""
    wt = home / f"wt-{agent}"
    if not wt.is_dir():
        wt.mkdir(parents=True)
        for args in (["init", "-q", "-b", "topic"], ["config", "user.email", "t@t"],
                     ["config", "user.name", "t"], ["commit", "-q", "--allow-empty", "-m", "x"]):
            subprocess.run(["git", *args], cwd=wt, check=True, capture_output=True)
    db.set_terminal(con, agent, "term_x", str(wt))
    write_agent_status(agent, "alpha", session_id="sess-1",
                       transcript_path="/tmp/sess-1.jsonl")


def keep_handoff(agent: str, text: str) -> None:
    """Leave `text` as the handoff waiting for `agent`, as a park would."""
    con = db.connect()
    try:
        db.set_handoff(con, agent, text)
    finally:
        con.close()


def kept_handoff(agent: str) -> str | None:
    con = db.connect()
    try:
        return db.get_handoff(con, agent)
    finally:
        con.close()


def write_agent_status(agent: str, reported_account: str, **fields) -> None:
    """The agent's own snapshot, the one a running session writes under its
    own env on every render - `agents.account_reading`'s 'reported' tier."""
    f = paths.agent_status_file(agent)
    f.parent.mkdir(parents=True, exist_ok=True)
    payload = {"account": reported_account, "collected_at": int(time.time())}
    payload.update(fields)
    f.write_text(json.dumps(payload))


def _seed_week_history(con, account: str, now: int, five=(10, 90), seven=(50, 60)) -> None:
    """Live readings of one account across one 5h window and one week, climbing
    from `five[0]` to `five[1]` and from `seven[0]` to `seven[1]`, one a minute."""
    n = 40
    for i in range(n):
        st = status.AccountStatus(
            account, exists=True,
            pct_5h=five[0] + (five[1] - five[0]) * i // (n - 1),
            pct_7d=seven[0] + (seven[1] - seven[0]) * i // (n - 1),
            reset_5h=now + 3 * 3600, reset_7d=now + 3 * 86400,
            rl_at=now - (n - i) * 60, rl7_at=now - (n - i) * 60, collected_at=now - (n - i) * 60)
        db.record_usage_sample(con, account, st, ts=now - (n - i) * 60)


def _call_line(req: str, out: int, at: str = "2026-09-20T10:00:00.000Z", **extra) -> str:
    usage = {"input_tokens": 2, "cache_creation_input_tokens": 1000, "cache_read_input_tokens": 50000,
             "output_tokens": out, "output_tokens_details": {"thinking_tokens": out // 2}}
    return json.dumps({"type": "assistant", "requestId": req, "sessionId": "s1", "timestamp": at,
                       "cwd": "/w/purser", "message": {"model": "claude-sonnet-5", "usage": usage,
                                                       "content": []}, **extra}) + "\n"


def _gamma_world(home: Path, gamma_week: int = 99, gamma_reset_in: int = 39 * 3600):
    """#1233's shape: gamma at `gamma_week`% of its 7-day window with
    `gamma_reset_in` seconds to go, alpha with plenty, and a week of samples
    behind the plan's ratio."""
    _daemon_home(home, 20)
    now = int(time.time())
    write_status(home, "gamma", pct_5h=20, pct_7d=gamma_week, reset_5h=now + 3 * 3600,
                 reset_7d=now + gamma_reset_in)
    write_status(home, "alpha", pct_5h=20, pct_7d=50, reset_5h=now + 3 * 3600,
                 reset_7d=now + 3 * 86400)
    con = db.connect()
    _seed_week_history(con, "delta", now)
    burnrate.refresh_week_ratios(con, now, force=True)
    return con, loadmod.read_all(con)


# ---------------------------------------------------------------- pull collector
# The same payload, taken from the transcript instead of pushed by the status
# line (D11). Every figure here is one measured off the live fleet: the numbers
# are `orcapipe`'s own snapshot on 2026-09-21, where the pull and the push
# agreed to the token.
LIVE_USAGE = {"input_tokens": 2, "output_tokens": 382,
              "cache_creation_input_tokens": 924, "cache_read_input_tokens": 84777}


def write_transcript(home: Path, acct: str, cwd, session: str, turns=(),
                     extra=(), at: int | None = None) -> Path:
    """A transcript where Claude Code would write one, with `turns` in order."""
    d = home / "accounts" / acct / ".claude" / "projects" / collect.encode_cwd(acct, cwd)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{session}.jsonl"
    rows = [{"type": "assistant", "message": {"model": m, "usage": u}} for m, u in turns]
    rows += list(extra)
    p.write_text("".join(json.dumps(r) + "\n" for r in rows))
    if at is not None:
        os.utime(p, (at, at))
    return p


def _doctor_row(r, needle: str):
    return next(((lv, n, d) for lv, n, d in r.rows if needle in n), None)


DESIGN_TEAM = """
teams:
  product: [product]
  eng:     [backend, frontend]
  growth:  [marketing, analytics]
  ops:     [archivist]

agents:
  product:    { model: mid,   weight: light }
  backend:    { model: high,  weight: heavy }
  frontend:   { model: high,  weight: heavy }
  marketing:  { model: mid,   weight: light }
  analytics:  { model: mid,   weight: light }
  archivist:  { model: cheap, weight: light }
  reviewer:   { model: mid,   weight: light, reviews: [eng, growth, product] }

edges:
  - { from: "*",         to: "reviewer",     relation: peer,  response: async }
  - { from: "reviewer",  to: "*",            kind: verdict,   response: async }
  - { from: "product",   to: "eng.*",        relation: peer,  response: async }
  - { from: "product",   to: "growth.*",     relation: peer,  response: async }
  - { from: "analytics", to: "product",      relation: peer,  response: async }
  - { from: "*",         to: "archivist",    relation: peer,  response: fire_and_forget }
  - { from: "*",         to: "human", kind: escalate,  creates_gate: true }

deny:
  - { from: "eng.*",    to: "growth.*",  reason: "go through product" }
  - { from: "growth.*", to: "eng.*",     reason: "same" }

limits:
  max_chain_depth: 4
  ask_rate_per_hour: 20
"""


def _reconcile_world(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    (home / "team.yaml").write_text(DESIGN_TEAM)
    agents.register("backend")


# ---------------------------------------------------------------- daemon
def _daemon_home(home: Path, pct: int | None) -> None:
    restore_world()
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    (home / "team.yaml").write_text(DESIGN_TEAM)
    agents.register("backend")
    write_status(home, "alpha", pct_5h=pct, pct_7d=10, ctx_pct=5,
                 reset_5h=int(time.time()) + 1800)
    # An agent the daemon acts on has somewhere to run: nothing is folded,
    # cleared or parked for one with no terminal. The fixture says so once, so
    # the tests below are about triggers rather than about that.
    con = db.connect()
    try:
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_account(con, "backend", "alpha")
        # It has been heard from a moment ago, so a closed session's cache is
        # warm: an agent with no clock at all is treated as cold.
        db.add_event(con, "backend", "Stop", {})
    finally:
        con.close()
    # And this world has no Orca and no process table unless a test provides
    # them. Otherwise the sweep asks the real machine, which has never heard of
    # term_x and is running no session called backend, and correctly tears the
    # fixture down.
    orcamod.available = lambda: False
    obsmod.live_sessions = lambda: None


def _hold_daemon_lock_in_another_process(env: dict, write_name: bool = True):
    """A separate process holding the daemon lock, as launchd's daemon does.
    An in-process hold would pass on a lock that never leaves the process."""
    take = "lock.singleton('daemon')" if write_name else "lock._hold('daemon', lock.SINGLETON)"
    holder = subprocess.Popen(
        [sys.executable, "-c",
         f"import os, sys, time; sys.path.insert(0, {str(ROOT)!r})\n"
         "from ocp import lock\n"
         f"held = {take}\n"
         "held.__enter__()\n"
         "print(os.getpid(), flush=True)\n"
         "time.sleep(120)\n"],
        env=env, stdout=subprocess.PIPE, text=True)
    return holder, int(holder.stdout.readline())


# ---------------------------------------------------------------- blocked delivery
# The dialog Claude Code put up when /exit met background shells, and the
# founder's own half-written text with a paste in it, both copied from live
# screens: the fixtures are what the screen said, not what a test would invent.
EXIT_DIALOG = """  Two things remain open and I've flagged both rather than deciding them myself.
✻ Sautéed for 5m 52s · done 12:41 PM · 1 shell still running
▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔
   Background work is running
   The following will stop when you exit:
   shell · until grep -qE "BUILD (SUCCEEDED|FAILED)|error:" …
   ❯ 1. Exit and stop tasks
     2. Move to background and exit
     3. Stay
   Enter to confirm · Esc to cancel""".split("\n")
PASTED_DRAFT = "ㅇㅇeck[Pasted text #1 +4 lines]"
# Measured live 2026-09-21 on four idle agents (keeper, purser, chat, auditor):
# two full-width rules around the composer's one content line, a status line
# and a hint line under it.
_COMPOSER_RULE_ROW = "─" * 60
COMPOSER_EMPTY = [_COMPOSER_RULE_ROW, "❯", _COMPOSER_RULE_ROW,
                 "  delta · 5h 68% · 7d 67%",
                 "  ⏵⏵ auto mode on (shift+tab to cycle) · ← for agents"]


def _composer_typed(text: str) -> list:
    """The same box with content in it - never read; the shape is all that
    matters (#1157)."""
    return [_COMPOSER_RULE_ROW, f"❯ {text}", _COMPOSER_RULE_ROW,
            "  delta · 5h 68% · 7d 67%",
            "  ⏵⏵ auto mode on (shift+tab to cycle) · ← for agents"]


# One hint line under the box and no account status line, so the box sits one
# row later than an ocp-launched agent's (auditor's #1144 fleet check).
ONE_HINT_ROW_COMPOSER = [_COMPOSER_RULE_ROW, "❯", _COMPOSER_RULE_ROW,
                         "  ⏵⏵ auto mode on (shift+tab to cycle)"]

# What #1144 recorded as harbor's ordinary composer: its hint is the
# picker's ("space to reply"), which no ordinary composer carries, so it was
# the picker with its banner out of the window (purser's #1294) - never a
# clean composer.
PICKER_HINT_ONLY_ENTER_TO_RETURN = [_COMPOSER_RULE_ROW, "❯", _COMPOSER_RULE_ROW,
                                    "  ⏵⏵ auto mode · enter to return · space to reply · ctrl+x to "]

# purser's #1294: the picker with the current session selected - the form Left
# gives - whose hint says "enter to return", not "enter to open".
PICKER_CURRENT_SESSION_SELECTED = [
    "Your conversation moved to the background - enter opens it · esc returns to it · ctrl+c twice quits",
    " ✻ ocp inbox management             Processed both orcapipe inbox corrections...",
    _COMPOSER_RULE_ROW, "❯", _COMPOSER_RULE_ROW,
    "  ⏵⏵ auto mode · enter to return · space to reply · ctrl+x to delete · ? for shortcuts"]


# auditor's #1352 should-fix: the one shape only "enter to open" catches -
# another session selected, and (unlike every real fleet reading in purser's
# #1294) no "space to reply" in the hint. Without this, deleting the older
# marker leaves the suite entirely green, because every other picker fixture
# carries both phrases.
PICKER_HINT_ONLY_ENTER_TO_OPEN = [
    "Your conversation moved to the background - enter opens it · esc returns to it · ctrl+c twice quits",
    " ✻ ocp inbox management             Processed both orcapipe inbox corrections...",
    _COMPOSER_RULE_ROW, "❯", _COMPOSER_RULE_ROW,
    "  ⏵⏵ auto mode · enter to open · ctrl+x to delete · ? for shortcuts"]

# orcapipe's #1161 read of harbor: the backgrounded-session picker. Its
# new-session box is rule/marker/rule like any composer.
BACKGROUND_PICKER = [
    "Your conversation moved to the background - enter opens it · esc returns to it · ctrl+c twice quits",
    "Completed",
    " ✻ ocp inbox management             Processed both orcapipe inbox corrections...",
    " ∙ Harbor app manager continuation  main merged at b42550f3...",
    _COMPOSER_RULE_ROW, "❯", _COMPOSER_RULE_ROW,
    "  ⏵⏵ auto mode · enter to open · space to reply · ctrl+x to delete · ? for shortcuts"]


# #1252: ordinary English an idle agent writes about this very feature,
# measured as the shape every one of 8 live agents actually has - one row of
# its own transcript sitting just above a real, empty composer.
FLEET_HINT_QUOTE = ("blocking: an idle agent whose transcript merely says "
                     "enter to open gets treated as backgrounded")

# A picker shaped differently from BACKGROUND_PICKER - one session row
# instead of two, no "Completed" banner line, harbor's one-hint-row composer
# (#1144) instead of the four-row one - so a fix cannot pass by fitting this
# fixture's exact offsets.
BACKGROUND_PICKER_SHORT = [
    "Your conversation moved to the background - enter opens it · esc returns to it · ctrl+c twice quits",
    " ✻ ocp inbox management             Processed both orcapipe inbox corrections...",
    _COMPOSER_RULE_ROW, "❯", _COMPOSER_RULE_ROW,
    "  ⏵⏵ auto mode · enter to open · space to reply · ctrl+x to "]

# auditor's #1285 B1: a picker whose session rows do not match a column-
# alignment guess (no "Completed" section, no banner reaching this window) -
# the composer's own hint still says the picker's words, so this must never
# read as a clean composer.
PICKER_HINT_WITH_NO_BANNER = [
    " ✻ ocp inbox management             Processed both orcapipe inbox corrections...",
    _COMPOSER_RULE_ROW, "❯", _COMPOSER_RULE_ROW,
    "  ⏵⏵ auto mode · enter to open · space to reply · ctrl+x to "]

# The banner quoted in the transcript above an ordinary composer: the real
# picker's hint always carries a picker phrase, so a banner with none below
# the box is text somebody wrote, not the picker.
BANNER_ABOVE_ORDINARY_COMPOSER = [
    "Your conversation moved to the background - enter opens it · esc returns to it · ctrl+c twice quits",
    " ✻ ocp inbox management             Processed both orcapipe inbox corrections...",
] + COMPOSER_EMPTY

# auditor's #1309 A: the banner and the picker's words both quoted in the
# transcript above the box, with an ordinary hint below it - only the hint's
# position (at or below the composer box) tells this from the picker.
BANNER_AND_HINT_QUOTED_ABOVE_BOX = [
    "Your conversation moved to the background - enter opens it · esc returns to it · ctrl+c twice quits",
    "  the hint reads enter to open on the picker",
] + COMPOSER_EMPTY


def _screen_of(tail, draft=None, source="screen"):
    term = {"handle": "term_x", "status": "running", "tail": list(tail), "source": source}
    if draft is not None:
        term["draft"] = draft
    return term


def _blocked_world(home: Path):
    """backend has a terminal and a session, and something waiting it has not been told."""
    _daemon_home(home, 20)
    orcamod.available = lambda: True
    orcamod.alive = lambda h: True
    sessions_are("backend")
    con = db.connect()
    db.set_agent_state(con, "backend", dmn_states.IDLE)
    db.add_request(con, "marina", "backend", "carry on", None, "peer", "async", None, 0, None)
    return con


def _escalations(con) -> list:
    return con.execute("SELECT id, state, verdict FROM requests WHERE target = 'human' "
                       "AND kind = 'escalate' ORDER BY id").fetchall()


# Shapes reconstructed from Claude Code's wording, NOT measured on a live screen:
# only EXIT_DIALOG above is. They stand for the trust dialog, a tool permission
# prompt and a question with a plain No, to prove what ocp presses for each.
_RULE_ROW = "▔" * 60
# TRUST_DIALOG is a reconstruction too, and its layout is not the real trust
# screen's: the live one has unnumbered options with "No, exit" first, which
# parse_dialog does not read at all. It pins a general rule, so its tests
# stand. To model the trust screen itself, copy LIVE_TRUST_PROMPT_CAPTURE.
TRUST_DIALOG = ["  where the work is", _RULE_ROW, "   Do you trust the files in this folder?",
                "   /Users/ron/somewhere", "   ❯ 1. Yes, proceed", "     2. No, exit",
                "   Enter to confirm · Esc to cancel"]
PERMISSION_PROMPT = ["  earlier output", _RULE_ROW, "   Bash command", "     rm -rf build",
                     "   Do you want to proceed?", "   ❯ 1. Yes",
                     "     2. Yes, and don't ask again for rm commands",
                     "     3. No, and tell Claude what to do differently (esc)",
                     "   Esc to cancel · Tab to amend"]
YES_NO_DIALOG = ["  earlier output", _RULE_ROW, "   Overwrite the file?", "   ❯ 1. Yes", "     2. No",
                 "   Enter to confirm · Esc to cancel"]
# auditor's #1026 repro: the trust dialog with a third, inert-looking option
# added, and the SAME screen naming Esc's real target with '(esc)' on the
# choice that exits. A label elsewhere reading 'Cancel' must not override what
# the screen already said Esc does.
TRUST_DIALOG_WITH_A_DECOY_CANCEL = [
    "  where the work is", _RULE_ROW, "   Do you trust the files in this folder?",
    "   ❯ 1. Yes, proceed", "     2. No, exit (esc)", "     3. Cancel",
    "   Enter to confirm · Esc to cancel"]
# auditor's #1057 repro, both shapes: the mark on a free-text line between the
# options (not a numbered line parse_dialog turns into an option at all), and
# the mark on the title itself. Neither is something declining_key can
# evaluate, so both must refuse - "read, never infer" has to mean the whole
# panel, not only the lines this parse happened to turn into data.
TRUST_DIALOG_WITH_FREE_TEXT_ESC_LINE = [
    "  where the work is", _RULE_ROW, "   Do you trust the files in this folder?",
    "   ❯ 1. Yes, proceed", "     2. No, exit", "     Press Esc to exit (esc)", "     3. Cancel",
    "   Enter to confirm · Esc to cancel"]
TRUST_DIALOG_WITH_ESC_MARK_ON_THE_TITLE = [
    "  where the work is", _RULE_ROW, "   Exiting will stop tasks (esc)",
    "   ❯ 1. Exit", "     2. Cancel",
    "   Enter to confirm · Esc to cancel"]
CLEARED_SCREEN = ["  back at the prompt", "  > "]
# The screen a session is really at once a dialog is gone: the composer's box
# between its two rules, which screen_blocker must see to call the screen typeable.
_COMPOSER_RULE_ROW = "─" * 60
AT_THE_PROMPT = ["  back at the prompt", _COMPOSER_RULE_ROW, "❯", _COMPOSER_RULE_ROW,
                 "  ⏵⏵ auto mode on (shift+tab to cycle)"]


class _Terminal:
    """A terminal for a test: shows `tail` until Esc arrives, then `after`, and
    remembers every key it was sent - the only place a press can go."""

    def __init__(self, tail, after=CLEARED_SCREEN, ignores_esc=False, source="screen"):
        self.tail, self.after, self.ignores_esc, self.source = list(tail), list(after), ignores_esc, source
        self.sent: list = []
        self.reads = 0

    def install(self):
        orcamod._screen = self.read
        orcamod.send = self.send
        orcamod.screen_blocker = REAL_ORCA["screen_blocker"]

    def read(self, handle):
        self.reads += 1
        return _screen_of(self.tail, source=self.source)

    def send(self, handle, text, enter=True):
        self.sent.append((text, enter))
        if text == "\x1b" and not self.ignores_esc:
            self.tail = list(self.after)
        return True, ""

    def bring_back(self, tail):
        self.tail = list(tail)


class _ExitTerm:
    """A session at its prompt that answers `/exit` with `dialog`; the key that
    exits it ends the session in the process table, Esc leaves it standing."""

    def __init__(self, dialog, exits_on, table):
        self.dialog, self.exits_on, self.table = dialog, exits_on, table
        self.screen, self.sent = list(COMPOSER_EMPTY), []
        self.torn, self.reads = 0, 0

    def _read(self, handle):
        self.reads += 1
        if self.torn and self.screen == list(self.dialog):
            self.torn -= 1
            return _screen_of(self.dialog[-1:])
        return _screen_of(self.screen)

    def install(self):
        orcamod._screen = self._read
        orcamod.send = self.send
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        orcamod.screen_blocker = REAL_ORCA["screen_blocker"]

    def send(self, handle, text, enter=True):
        self.sent.append((text, enter))
        if text == "/exit":
            self.screen = list(self.dialog)
        elif text == self.exits_on and self.screen == list(self.dialog):
            self.table.pop("backend", None)
            self.screen = list(COMPOSER_EMPTY)
        elif text == "\x1b":
            self.screen = list(COMPOSER_EMPTY)
        return True, ""


def _close_at(home: Path, dialog, exits_on="1", torn=0, screen=None):
    restore_world()
    con = _blocked_world(home)
    db.set_terminal(con, "backend", "term_x", "wt")
    table = sessions_are("backend")
    term = _ExitTerm(dialog, exits_on, table)
    term.torn = torn
    if screen is not None:
        term.screen = list(screen)
    term.install()
    st = types.SimpleNamespace(session_id="sess-1", ctx_pct=8)
    ok, why = dmn_lifecycle._end_session(con, "backend", lambda m: None, st, resume="sess-1")
    return con, term, ok, why


def _bg_of(tail):
    restore_world()
    orcamod._screen = lambda h: None if tail is None else _screen_of(tail)
    try:
        return orcamod.background_work("term_x")
    finally:
        restore_world()


def _decline_world(home: Path, tail=EXIT_DIALOG, **kw):
    """backend is idle with work it cannot be given, behind `tail`."""
    con = _blocked_world(home)
    term = _Terminal(tail, **kw)
    term.install()
    orcamod.live_terminals = lambda: {"term_x"}
    return con, term


def _reports(con) -> list:
    return con.execute("SELECT id, kind, fyi, state, body FROM requests WHERE target = 'human' "
                       "AND kind = 'report' ORDER BY id").fetchall()


# ---------------------------------------------------------------- drift
def _mkrepo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    run = lambda *a: subprocess.run(["git", *a], cwd=path, capture_output=True, env=env)  # noqa: E731
    run("init", "-q", "-b", "main")
    (path / "shared.txt").write_text("base\n")
    (path / "other.txt").write_text("base\n")
    run("add", "-A")
    run("commit", "-qm", "base")


def _worktree_with_a_dirty_file(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for args in (["init", "-q", "-b", "topic"], ["config", "user.email", "t@t"],
                 ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    (root / "a.txt").write_text("a")
    subprocess.run(["git", "add", "a.txt"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-qm", "first"], cwd=root, check=True,
                   capture_output=True)
    (root / "dirty.txt").write_text("not committed")
    return root


# ---------------------------------------------------------------- yaml differential
DIFFERENTIAL_CASES = [
    "reason: go#1\n",
    "url: http://x/#frag\n",
    "a: b # real comment\n",
    "d:\n  - a:b\n",
    "d:\n  - http://example.com\n",
    "enabled: yes\nother: no\n",
    "on_flag: on\noff_flag: off\n",
    '"a:b": c\n',
    "n: 0x10\n",
    "n: 0o17\n",
    "f: 1.5\ng: -2\nh: 1e3\n",
    "q: 'single'\nr: \"double\"\n",
    "empty:\n",
    "nested:\n  a:\n    b: 1\n",
    "lst:\n  - 1\n  - two\n  - true\n",
    'flow: { a: 1, b: "x, y" }\n',
    "flowlist: [1, 2, three]\n",
    "edges:\n  - { from: \"*\", to: reviewer, creates_gate: no }\n",
    "edges:\n  - { from: \"*\", to: reviewer, creates_gate: yes }\n",
    # What PyYAML reads as something other than the text, which the fallback
    # must refuse or read the same way: dates, inf and nan in every spelling,
    # numbers written with underscores (signed, float, hex, binary, octal).
    "d: 2026-09-20\n",
    "d: 2026-09-20 10:00:00\n",
    "d: 2026-09-20T10:00:00Z\n",
    "n: +.inf\nm: -.inf\no: .INF\n",
    "n: .nan\nm: .NaN\n",
    "n: 1_000\nm: -1_000\n",
    "n: -1_000.5\nm: +1_0.5e+3\n",
    "n: 0x_1F\nm: 0b1_0\no: 0_7\n",
    "n: 1__0\nm: 1_\n",
    # Sexagesimals with a sign, an underscore, or a fraction.
    "n: +12:30\nm: -12:30\n",
    "n: 1_0:30\nm: +1_0:30\n",
    "n: +12:30.5\nm: 1:30:00\n",
    # inf and nan only in the spellings PyYAML takes: the rest are strings.
    "n: .iNf\nm: -.nan\no: +.nan\n",
    # Characters the fallback refuses when bare are text inside quotes.
    "a: \"handoff -> resolve, then merge\"\nb: 'a & b'\nc: \"x | y\"\nd: '*x'\n",
    # A sign belongs to a float only before digits, and y, n and mixed-case
    # spellings are strings to PyYAML.
    "n: .5\nm: -.5\no: +.5\np: 1.\n",
    "a: y\nb: n\nc: yEs\nd: tRUE\ne: Null\nf: NULL\ng: nULL\n",
    # Quoting: a doubled single quote, and the two escapes that are supported.
    "q: 'it''s'\n",
    'q: "a\\"b"\nr: "a\\\\b"\n',
    '"k\\"x": 1\n',
    "q: 'x # not a comment'\nr: \"x # \\\" # still not\"\n",
    # Keys: PyYAML reads a plain 1, on, null or date as an int, a bool, None or a
    # date, so those are refused here; text and quoted keys agree.
    "1: x\n", "on: x\nyes: y\n", "null: x\n", "~: x\n", "2026-09-20: x\n", "0x10: x\n",
    "1.5: x\n", "1_000: x\n", "x:\n  1: a\n  b: c\n", "- 1: a\n", "- on: a\n",
    "{ a: 1, 1: x }\n", "{ a: 1, on: y }\n",
    "y: 1\n1e3: 2\n08: 3\na b: 4\n-: 5\n", "'1': a\n\"on\": b\n'2026-09-20': c\n",
    "&a k: v\n", "*a: v\n", '"a" b: 1\n', "'a' b: 1\n", "a>b: 1\nx*y: 2\n",
    # A quote that is not closed at the end of the scalar is an error, not text.
    'a: "x & y\n', "a: 'x\n", 'a: "x" y\n',
]


# Strings `yamlcompat.dump` writes that a reader could take for something else.
DUMP_STRINGS = [
    'x"y', "a\\b", "it's", "y", "n", "yEs", "Null", "2026-09-20", "2026-09-20 10:00:00",
    "+.inf", "-.inf", ".nan", "-1_000.5", "1_000", "0x_1", ".5", "-.5", "1e3", "0o7", "08",
    "-5", "+7", "1.5", "0", "", "~", " lead", "trail ", "a: b", "#c", "a b", "é",
    "-", "- a", "?", "!x", "%x", "@x", "`x", "=", "a\"b\\c\"d",
    # What the read path refuses when it is bare, and so must be quoted to be read.
    "handoff -> resolve, then merge", "a & b", "*x", "x | y", "!!x", "a << b", "<<", ">", "|",
    "+12:30", "1_0:30", "+1_0:30", "+12:30.5", ".iNf", "-.nan", ".inf", ".NaN",
]
# Values that are not strings, which the same write must also read back as themselves.
DUMP_VALUES = [0.1, -0.0, 2.5, 1e16, -1e16, 1e-7, 1.5e300, 5e-324, 0, -3, 10 ** 30,
               True, False, None]


def _check_yaml_differential() -> None:
    """Compare the bundled parser with PyYAML, which must be importable here.

    A source is a mismatch only when ours returns a different value; raising
    where PyYAML succeeds is loud, not silent. Every string `dump` writes must
    also be read back as itself by both.
    """
    import yaml as pyyaml

    mismatches = []
    for src in DIFFERENTIAL_CASES + [team.STARTER, DESIGN_TEAM]:
        try:
            expected = pyyaml.safe_load(src)
        except pyyaml.YAMLError:
            expected = "<error>"
        try:
            got = yamlcompat.parse(src)
        except yamlcompat.ConfigError:
            got = "<error>"
        # A value that agrees in ==, such as 1 and True, is still a different
        # value here; a NaN never equals itself.
        if got != "<error>" and (got != expected or repr(got) != repr(expected)):
            mismatches.append((src, expected, got))
    check("yaml differential: no silent disagreement with PyYAML",
          not mismatches,
          "; ".join(f"{s!r}: pyyaml={e!r} ours={g!r}" for s, e, g in mismatches[:3]))
    check("yaml differential: the list still covers what it was built for",
          len(DIFFERENTIAL_CASES) >= 30, str(len(DIFFERENTIAL_CASES)))

    unread = []
    for value in DUMP_STRINGS:
        text = yamlcompat.dump({"k": value})
        try:
            ours = yamlcompat.parse(text)
        except yamlcompat.ConfigError:
            ours = "<error>"
        try:
            theirs = pyyaml.safe_load(text)
        except pyyaml.YAMLError:
            theirs = "<error>"
        if ours != {"k": value} or theirs != {"k": value}:
            unread.append((value, text, ours, theirs))
    for value in DUMP_VALUES:
        text = yamlcompat.dump({"k": value})
        ours, theirs = yamlcompat.parse(text), pyyaml.safe_load(text)
        if repr(ours.get("k")) != repr(value) or repr(theirs["k"]) != repr(value):
            unread.append((value, text, ours, theirs))
    check("yaml differential: what dump writes, both parsers read back as the value",
          not unread, "; ".join(f"{v!r} -> {t!r}: ours={o!r} pyyaml={p!r}"
                                for v, t, o, p in unread[:3]))


def _python_with_pyyaml() -> str | None:
    """A python3 that can import yaml: the interpreter running the suite when
    it can, else /usr/bin/python3 (which ships it on macOS), else python3 on PATH."""
    import shutil
    for py in (sys.executable, "/usr/bin/python3", shutil.which("python3")):
        if py and subprocess.run([py, "-c", "import yaml"],
                                 capture_output=True).returncode == 0:
            return py
    return None


def _parsed(src: str):
    """What the bundled parser reads, or "<refused>" where it raises."""
    try:
        return yamlcompat.parse(src)
    except yamlcompat.ConfigError:
        return "<refused>"


def _roundtrip(value, key: str = "k"):
    """What the bundled parser reads back from what dump writes for one value,
    or "<refused>" where either raises."""
    try:
        return yamlcompat.parse(yamlcompat.dump({key: value}))
    except yamlcompat.ConfigError:
        return "<refused>"


def _refused(src: str) -> bool:
    try:
        yamlcompat.parse(src)
    except yamlcompat.ConfigError:
        return True
    return False


def _undefined_globals(path: Path) -> set[tuple[str, str]]:
    """(scope, name) for every name a function reads as a global that its module
    never defines and Python does not provide."""
    import builtins
    import symtable
    text = path.read_text()
    top = symtable.symtable(text, str(path), "exec")
    known = {sym.get_name() for sym in top.get_symbols()
             if sym.is_assigned() or sym.is_imported() or sym.is_namespace()}
    if "from support import *" in text:
        known |= set(__all__)
    found: set[tuple[str, str]] = set()

    def walk(table) -> None:
        for sym in table.get_symbols():
            name = sym.get_name()
            if (table is not top and sym.is_global() and sym.is_referenced()
                    and name not in known and not hasattr(builtins, name)
                    and not name.startswith("__")):
                found.add((table.get_name(), name))
        for child in table.get_children():
            walk(child)
    walk(top)
    return found


class FakeOrca:
    """Orca's `worktree show` and `worktree set`, over a dict of checkouts by
    path, and nothing else: any other call is an error, so a test that uses it
    cannot reach the real Orca or type at a terminal."""

    def __init__(self, worktrees: dict[str, str], sets: bool = True):
        self.names = dict(worktrees)     # path -> displayName
        self.sets = sets                 # False: it says ok and changes nothing
        self.calls: list[list[str]] = []

    def run(self, args: list[str], timeout: int = 180) -> tuple[bool, str]:
        self.calls.append(list(args))
        assert args[:1] == ["worktree"] and args[1] in ("show", "set"), args
        path = args[args.index("--worktree") + 1].removeprefix("path:")
        if path not in self.names:
            return False, json.dumps({"ok": False, "error": {"code": "selector_not_found"}})
        if args[1] == "set":
            if self.sets:
                self.names[path] = args[args.index("--display-name") + 1]
            return True, json.dumps({"ok": True, "result": {}})
        return True, json.dumps({"ok": True, "result": {"worktree": {
            "path": path, "displayName": self.names[path]}}})


class SecondVendor:
    """A second vendor, registered for real: a single-vendor suite cannot see
    a literal left behind, or an account handed to the wrong kind of agent -
    both read the same as the right answer until a second vendor exists. Every
    answer differs from claude's. `logged_in` and `metered` are what the
    account-picking tests flip. Its binary is no real driver's either: a
    command line both claimed would not show which of them answered."""
    name = "zai"
    min_version = (0,)
    config_dir_name = ".zai"

    def __init__(self, logged_in: bool = True, metered: bool = False):
        self.logged_in, self.metered = logged_in, metered

    def binary(self):
        return "/opt/zai/bin/glmcode"
    def config_dir_env(self):
        return "ZAI_HOME"
    def is_session(self, command):
        # Its own binary, and its own spelling of headless - neither word
        # appears in claude's answer, so a check that still carries claude's
        # literals answers no to every line below.
        if "glmcode" not in command:
            return False
        return " run " not in command and not command.endswith(" run")
    def meters(self):
        return self.metered
    def is_resume(self, extra):
        return "--resume" in (extra or [])
    def launch_argv(self, exe, model, effort, disallowed, mcp, preamble):
        return [exe]
    def launch_env(self, preamble, disallowed, state_dir):
        return {"ZAI_DENY": disallowed}
    def prompt_argv(self, prompt):
        return ["--ask", prompt]
    def clear_command(self):
        return "/reset"
    def exit_command(self):
        return "/quit"
    idle_from_screen = False
    def idle_on_screen(self, tail):
        return True
    def screen_blocker(self, tail):
        return None
    def credential_present(self, config_dir):
        return self.logged_in
    def login_argv(self):
        return ["glmcode", "login"]
    def logout_argv(self):
        return ["glmcode", "logout"]
    def identity(self, env):
        return None
    def subscription_type(self, env):
        return None
    def shared_items(self):
        return ["skills"]
    def never_touch(self):
        return {"skills"}
    def memory_root(self, config_dir):
        return config_dir / "sessions"
    def memory_project_dir(self, repo):
        return repo.replace("/", "_")


@contextlib.contextmanager
def second_vendor(**kw):
    drv = SecondVendor(**kw)
    vendors._REGISTRY[drv.name] = drv
    try:
        yield drv
    finally:
        vendors._REGISTRY.pop(drv.name, None)


def _with_vendor(agent: str, vendor: str) -> None:
    with agents.agent_file(agent).open("a") as f:
        f.write(f"vendor: {vendor}\n")


# `opencode auth list`'s whole output, measured on 1.18.32 with stdout a pipe.
def _auth_list_out(n: int) -> str:
    return (f"\x1b[0m\n\u250c  Credentials \x1b[90m/x/opencode/auth.json\n\u2502\n"
            f"\u2514  {n} credentials\n\n")


# opencode 1.18.32's screen in an Orca terminal, as `orca terminal read
# --screen` returned it (#1926). The path in the footer is shortened.
_OC_RULE = "  ╹" + "▀" * 100
OC_IDLE = ["  ┃", "  ┃  Reply in one line with the PREAMBLE CODEWORD.", "  ┃",
           "     marigold-77", "     ▣  Build · GLM-5.3-Flash · 5.5s",
           "  ┃", "  ┃", "  ┃",
           "  ┃  Build · GLM-5.3-Flash Z.AI Coding Plan", _OC_RULE,
           "   /private/tmp/scratchpad/oc/proj 12.2K (1%ctrl+p",
           "   /proj                                   commands"]
OC_WORKING = ["  ┃", "  ┃  Use the bash tool to run: sleep 25", "  ┃", "  ┃",
              "  ┃", "  ┃  Build · GLM-5.3-Flash Z.AI Coding Plan", _OC_RULE,
              "   ■■■■■⬝⬝⬝  esc interrupt"]
OC_HOME = ["   █▀▀█ █▀▀█", "  ┃",
           "  ┃  Ask anything… \"Fix broken tests\"", "  ┃",
           "  ┃  Build · GLM-5.3-Flash Z.AI Coding Plan", _OC_RULE,
           "                          tab agents  ctrl+p commands",
           "     ● Tip Configure local or remote MCP servers in the mcp config section",
           "  /private/tmp/scratchpad/oc/proj                         1.18.32",
           "  proj:main"]
OC_PERMISSION = ["  ┃", "  ┃  Use the read tool to read the file /etc/hosts.", "  ┃",
                 "     ⠼ Read /etc/hosts", "     ▣  Build · GLM-5.3-Flash", "  ┃",
                 "  ┃  △ Permission required",
                 "  ┃    ← Access external directory /etc", "  ┃",
                 "  ┃  Patterns", "  ┃", "  ┃  - /etc/*", "  ┃", "  ┃",
                 "  ┃   Allow once   Allow always   Reject          "
                 "ctrl+f fullscreen  ⇆ select  enter confirm",
                 "  ┃"]


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:  # noqa: BLE001
        return True


def _session_file(home: Path, session: str) -> None:
    d = home / "agents" / "backend"
    d.mkdir(parents=True, exist_ok=True)
    (d / "status.json").write_text(json.dumps({
        "account": "alpha", "collected_at": int(time.time()),
        "session_id": session, "pct_5h": 20}))


def _park_via_handoff(con, agent: str, kind: str, hoff_state: str = "done") -> None:
    """Fabricate a completed handoff the way `_perform_handoff` leaves one,
    with `trigger_kind` on the record - the only place a park's cause is
    named, since nothing on `agent_state` itself says why."""
    hid = f"hoff-{kind}"
    db.start_handoff(con, hid, agent, None, kind, int(time.time()) + 90)
    db.advance_handoff(con, hid, "released", state=hoff_state)


def _process_on(account: str | None):
    """Say which account the running process was started under, as `ps eww`
    would show its CLAUDE_CONFIG_DIR. None: the environment cannot be read."""
    def env_of(pid, name):
        if name == "CLAUDE_CONFIG_DIR" and account:
            return str(vendors.config_dir(account))
        return None
    obsmod.env_of = env_of


# Claude Code's session-limit dialog, copied from orcapipe.chat's live screen on
# 2026-09-21 (its reset time is the only part that varies).
LIMIT_DIALOG = """  ⎿  You've hit your session limit · resets 3:30pm (Asia/Seoul)
✻ Cooked for 0s · done 12:10 PM
▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔
   What do you want to do?
   ❯ 1. Stop and wait for limit to reset
     2. Wait here, then continue automatically at 3:30pm
     3. Upgrade your plan
   Enter to confirm · Esc to cancel""".split("\n")


def _park_behind(home: Path, tail, ends_at_limit: bool = True, **term_kw):
    """keeper is at its 5h wall with no account to go to, behind `tail`."""
    _daemon_home(home, 20)
    con = db.connect()
    agents.register("keeper")
    db.set_account(con, "keeper", "alpha")
    write_status(home, "alpha", pct_5h=100, pct_7d=20, ctx_pct=5, reset_5h=int(time.time()) + 3 * 3600)
    keep_handoff("keeper", "# handoff · keeper\n## next\n- carry on\n")
    db.add_request(con, "marina", "keeper", "carry on", None, "peer", "async", None, 0, None)
    if ends_at_limit:
        db.add_event(con, "keeper", "StopFailure", {"limit": "5h"})
        con.execute("UPDATE events SET ts = ? WHERE agent = 'keeper' AND kind = 'StopFailure'",
                    (int(time.time()) - dmn_states.LIMIT_SETTLE_S - 60,))
    orcamod.available = lambda: True
    orcamod.alive = lambda h: True
    orcamod.wait_idle = lambda h, timeout_ms=0: True
    orcamod.idle_now = lambda h: True
    orcamod.live_terminals = lambda: {"term_x"}
    term = _Terminal(tail, after=AT_THE_PROMPT, **term_kw)
    term.install()
    sessions_are("keeper")
    db.set_terminal(con, "keeper", "term_x", "wt")
    return con, term


def _fire_hook(event: str, **fields) -> None:
    real_in = sys.stdin
    sys.stdin = io.StringIO(json.dumps({"hook_event_name": event, "session_id": "s", **fields}))
    try:
        evmod.record_from_stdin([event])
    finally:
        sys.stdin = real_in


class _Reading:
    """What `burnrate.observe` reads of an account: a live figure and when it was seen."""
    def __init__(self, pct: int, at: int, reset: int = 9_999_999_999):
        self.pct_5h, self.rl_at, self.reset_5h = pct, at, reset
        self.pct_5h_source = "live"
        self.is_live_reading = True


def _samples_of(con) -> list[dict]:
    return burnrate._load_samples(con)


def _give_samples(con, rows: list[tuple[float, str | None, str | None]]) -> None:
    """Samples as percent per minute, oldest first, filed under a model and effort."""
    db.set_meta(con, burnrate._SAMPLES, json.dumps(
        [{"t": i, "a": "alpha", "r": r / 60, "m": m, "e": e} for i, (r, m, e) in enumerate(rows)]))


def _with_accounts(*names: str) -> None:
    """Registered accounts that count as logged in."""
    for n in names:
        accounts.register(n)
        (vendors.config_dir(n) / ".credentials.json").write_text("{}")


def _running_on(con, acct: str, *names: str) -> None:
    for n in names:
        agents.register(n)
        db.set_account(con, n, acct)
        db.set_agent_state(con, n, dmn_states.RUNNING)


def _reference_text(text: str) -> str:
    """What a reader should see of `text`: written here, apart from the code under
    test, so that a twin built from it does not share that code's mistakes."""
    text = re.sub(r"\033\][^\a\033]*(?:\a|\033\\)?", "", text)
    text = re.sub(r"\033\[[0-?]*[ -/]*[@-~]", "", text)
    return re.sub(r"[\x00-\x1f\x7f-\x9f]", "", text)


def _leaks(dirty: dict[str, str], clean: dict[str, str], skip_counts: tuple = ()) -> list[str]:
    """Where a screen drawn from hostile text differs from the same screen drawn
    from the text a reader should see. Two ways, because each alone is unsafe:
    a byte that is none of our own codes is a stray; and if our code emitted a
    different NUMBER of any of our own codes, the hostile text added or ended one
    - an injected reset or DIM that subtracting our codes would have called clean."""
    own = sorted({v for v in vars(tui).values() if isinstance(v, str) and v.startswith("\033")
                  and len(v) <= 12} | set(tui.STATE_COLOR.values()), key=len, reverse=True)
    found = []
    for label, text in dirty.items():
        rest = text
        for c in own:
            rest = rest.replace(c, "")
        stray = sorted({hex(ord(c)) for c in re.findall(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", rest)})
        if stray:
            found.append(f"{label}: stray {stray}")
        if label in skip_counts:
            continue
        differ = {c: (text.count(c), clean[label].count(c)) for c in own
                  if text.count(c) != clean[label].count(c)}
        if differ:
            found.append(f"{label}: codes differ {differ}")
    return found


# ------------------------------------------------------------- the boundary
def _touches_subprocess_text(tree) -> bool:
    """Calls `subprocess.run`/`Popen` and reads `.stdout` or `.stderr` off the
    result - somewhere in the module, not necessarily the same function: the
    two together are what makes foreign bytes reachable at all. A call to
    `gitrun.run` is the same thing on its own: it hands git's text back as a
    tuple, with no attribute to read."""
    import ast
    calls = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr in ("run", "Popen")
               and isinstance(n.func.value, ast.Name) and n.func.value.id == "subprocess"
               for n in ast.walk(tree))
    reads = any(isinstance(n, ast.Attribute) and n.attr in ("stdout", "stderr")
               for n in ast.walk(tree))
    via_gitrun = any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                     and n.func.attr == "run"
                     and isinstance(n.func.value, ast.Name) and n.func.value.id == "gitrun"
                     for n in ast.walk(tree))
    return (calls and reads) or via_gitrun


def _builds_a_returned_message(tree) -> bool:
    """A `return` (a bare value, or one element of a tuple/list, the shape
    every `_run`-like helper here uses) built as an f-string, a `.format()`
    call, or an `a or b or "default"` chain - the shape a captured-text
    fallback takes when there is no f-string to see it in at all."""
    import ast
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and node.value is not None:
            v = node.value
            for val in (v.elts if isinstance(v, (ast.Tuple, ast.List)) else [v]):
                if isinstance(val, ast.JoinedStr):
                    return True
                if (isinstance(val, ast.Call) and isinstance(val.func, ast.Attribute)
                      and val.func.attr == "format"):
                    return True
                if isinstance(val, ast.BoolOp) and isinstance(val.op, ast.Or):
                    return True
    return False


def _imports_textsafe(tree) -> bool:
    import ast
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "textsafe":
            return True
        if isinstance(node, ast.ImportFrom) and node.module is None:
            if any(a.name == "textsafe" for a in node.names):
                return True
        if isinstance(node, ast.Import) and any(a.name.endswith("textsafe") for a in node.names):
            return True
    return False


def _ANSI_TEXT_OK(s: str) -> bool:
    """Once the whole escape sequences are removed, no ESC is left."""
    return "\033" not in tui._ANSI.sub("", s)


# A real `orca terminal read --screen --json` capture (auditor's #339
# should-fix 3): a throwaway session started under an account no agent
# runs on, in a scratch directory outside any declared root, before any
# key was sent. The path is wrapped at terminal width with no separator,
# split across two lines by "Accessing workspace:".
LIVE_TRUST_PROMPT_CAPTURE = "\n".join([
    " ron@Ron-MacBookAir  ~/orca/workspaces/orcapipe/keeper-2   buddyron/trust-screen  "
    "cd '/private/tmp/claude-501/-Users",
    "-ron-orca-workspaces-orcapipe-keeper-2/592b2fde-6685-4f79-b057-8931a4b64d3a/"
    "scratchpad/trust-probe' && CLAUDE_CONFIG_DIR",
    "=/Users/ron/.orcapipe/accounts/gamma/.claude claude",
    "─" * 124,
    " Accessing workspace:",
    " /private/tmp/claude-501/-Users-ron-orca-workspaces-orcapipe-keeper-2/"
    "592b2fde-6685-4f79-b057-8931a4b64d3a/scratchpad/t",
    " rust-probe",
    " Quick safety check: Is this a project you created or one you trust? (Like your "
    "own code, a well-known open source",
    " project, or work from your team). If not, take a moment to review what's in "
    "this folder first.",
    " Claude Code'll be able to read, edit, and execute files here.",
    " Security guide",
    " ❯ No, exit",
    "   Yes, I trust this folder",
    " Enter to confirm · Esc to cancel",
])
LIVE_TRUST_PROMPT_FOLDER = (
    "/private/tmp/claude-501/-Users-ron-orca-workspaces-orcapipe-keeper-2/"
    "592b2fde-6685-4f79-b057-8931a4b64d3a/scratchpad/trust-probe")


def _two_agents_one_slot(home: Path, cap: int = 2) -> None:
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    (home / "team.yaml").write_text(
        "agents:\n  a: {}\n  b: {}\n"
        'edges:\n  - { from: "*", to: "*", relation: peer, response: fire_and_forget }\n'
        f"limits:\n  max_chain_depth: 99\n  ask_rate_per_hour: {cap}\n"
    )
    agents.register("a")
    agents.register("b")


# The endpoint's answer, measured live on the founder's own plan (task #42,
# 2026-09-23): two CREDIT_LIMIT rows, unit 3 the 5-hour window and unit 6 the
# weekly one, nextResetTime in epoch ms. The instants are built from the clock
# at test time: usage_reading bounds a plausible reset against the live clock,
# so a fixture set to fixed instants ages out of it within the hour and the
# suite goes red for everyone (#2054).
def zai_quota(now_ms: int) -> dict:
    return {
        "code": 200, "success": True, "msg": "Operation successful",
        "data": {"level": "lite", "limits": [
            {"type": "CREDIT_LIMIT", "unit": 3, "number": 5, "usage": 2000,
             "currentValue": 271, "remaining": 1728, "percentage": 13,
             "nextResetTime": now_ms + 3_600_000},
            {"type": "CREDIT_LIMIT", "unit": 6, "number": 1, "usage": 10000,
             "currentValue": 271, "remaining": 9728, "percentage": 2,
             "nextResetTime": now_ms + 6 * 24 * 3600 * 1000}]}}


TEST_FILTER: str | None = None  # set by -k; substring match on the test name
_RUN_DEPTH = {"n": 0}  # >0 inside a test's own nested run() calls, where -k does not apply


def _test_name(one) -> str:
    # The test that is running, by the name its lambda calls: what the guard
    # against the real Orca says, and what a fake `orca` on PATH logs.
    named = [getattr(one, "__name__", "")]
    named += [n for n in one.__code__.co_names]
    named += [getattr(c.cell_contents, "__name__", "") for c in (one.__closure__ or ())]
    return next((n for n in named if n.startswith("test_")), "<test>")


def run(one) -> None:
    """Every test starts from the real world.

    Each test used to put back what it stood in for through a tuple of its
    own, and a tuple that forgot a name leaked that stand-in into every
    test after it - which is the bug class this suite exists to catch.
    Restoring here means no test can inherit another's world, whatever its
    own finally block remembers.

    One test raising must not end the run: an uncaught error or `sys.exit`
    would stop `main` before it printed a final count, and a short run that
    looks like a clean pass is worse than a loud one. KeyboardInterrupt is
    left alone - that is the human stopping the suite, not a test failing.

    -k only gates the outer call, from main()'s own list: a test that calls
    run() itself on synthetic cases (test_run_recovers_from_an_uncaught_error)
    must not have those nested calls dropped by a filter aimed at main()'s.

    A nested run() must also leave CURRENT_TEST as it found it: without the
    restore, checks the enclosing test makes *after* its own nested run()
    calls return are recorded under the last nested case's name instead of
    the enclosing test's own - silently, since nothing here fails, the label
    is just wrong.
    """
    name = _test_name(one)
    if _RUN_DEPTH["n"] == 0 and TEST_FILTER is not None and TEST_FILTER not in name:
        return
    restore_world()
    prev_test = CURRENT_TEST["name"]
    CURRENT_TEST["name"] = name
    os.environ["OCP_TEST"] = CURRENT_TEST["name"]
    _RUN_DEPTH["n"] += 1
    try:
        one()
    except (Exception, SystemExit) as e:
        tb = traceback.extract_tb(e.__traceback__)
        test = next((f.name for f in tb if f.name.startswith("test_")), "<test>")
        where = tb[-1] if tb else None
        loc = f" at {where.filename}:{where.lineno}" if where else ""
        check(f"{test}: uncaught {type(e).__name__}: {e}{loc}", False)
    finally:
        _RUN_DEPTH["n"] -= 1
        CURRENT_TEST["name"] = prev_test
        os.environ["OCP_TEST"] = prev_test


def _python_with_textual() -> str | None:
    """A python3 that can import textual: the interpreter running the suite when
    it can, else the real machine's venv (requirements.txt, ocp/venv.py) -
    textual is only ever installed there, never assumed on whatever runs this
    suite."""
    candidates = [sys.executable, str(venvmod.venv_python(MACHINE_VENV))]
    for py in candidates:
        if py and Path(py).exists() and subprocess.run(
                [py, "-c", "import textual"], capture_output=True).returncode == 0:
            return py
    return None


def _board_world(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    (home / "team.yaml").write_text("""
agents:
  manager: {}
  worker:  {}

edges:
  - { from: "manager", to: "worker", relation: supervisor, response: fire_and_forget }
  - { from: "worker", to: "manager", relation: subordinate, response: fire_and_forget }

limits:
  max_chain_depth: 4
  ask_rate_per_hour: 50
""")
    for n in ("manager", "worker"):
        agents.register(n)


def _cli(argv: list[str], as_agent: str | None = "manager") -> tuple[int, str]:
    buf = io.StringIO()
    saved = os.environ.get("OCP_AGENT")
    if as_agent is None:
        os.environ.pop("OCP_AGENT", None)
    else:
        os.environ["OCP_AGENT"] = as_agent
    try:
        with contextlib.redirect_stdout(buf):
            rc = climod.main(argv)
    finally:
        if saved is None:
            os.environ.pop("OCP_AGENT", None)
        else:
            os.environ["OCP_AGENT"] = saved
    return rc, buf.getvalue()


def _human_request(target: str = "manager", parent: int | None = None) -> int:
    con = db.connect()
    try:
        return db.add_request(con, askmod.HUMAN, target, "the founder's words", None,
                              "supervisor", "fire_and_forget", None, 0, parent)
    finally:
        con.close()


def _request(requester: str, target: str) -> int:
    con = db.connect()
    try:
        return db.add_request(con, requester, target, "go", None,
                              "supervisor", "fire_and_forget", None, 0, None)
    finally:
        con.close()


def _now_line(out: str, tid: int) -> str:
    lines = out.splitlines()
    for i, line in enumerate(lines):
        if line.split()[:1] == [f"T{tid}"]:
            return lines[i + 1].strip()
    return ""


def _link(tid: int, rid: int, state: str | None = None) -> None:
    con = db.connect()
    try:
        db.link_request(con, tid, rid)
        if state:
            con.execute("UPDATE requests SET state = ? WHERE id = ?", (state, rid))
    finally:
        con.close()


def _done_and_close(tid: int, owner: str, evidence: str = "done",
                    closer: str | None = "manager") -> tuple[int, str]:
    """#71: the two-step close most tests just need - mark it done as its
    owner, then close it as `closer` (the task's opener, the default asker
    for a task nobody linked a founder request to)."""
    rc, out = _cli(["task", "done", f"T{tid}", evidence], as_agent=owner)
    if rc != 0:
        return rc, out
    return _cli(["close", f"T{tid}"], as_agent=closer)


def _two_lanes(home: Path) -> None:
    """manager leads worker; boss leads reviewer, in a second lane."""
    (home / "team.yaml").write_text("""
teams:
  manager: [worker]
  boss: [reviewer]
  halim: [lane.x]

agents:
  manager:  {}
  worker:   {}
  boss:     {}
  reviewer: {}
  lane.x:   {}

edges:
  - { from: "manager", to: "worker", relation: supervisor, response: fire_and_forget }
  - { from: "worker", to: "manager", relation: subordinate, response: fire_and_forget }
  - { from: "worker", to: "reviewer", relation: peer, response: fire_and_forget }
  - { from: "worker", to: "lane.x", relation: peer, response: fire_and_forget }
  - { from: "boss", to: "reviewer", relation: supervisor, response: fire_and_forget }
  - { from: "boss", to: "lane.x", relation: supervisor, response: fire_and_forget }

limits:
  max_chain_depth: 4
  ask_rate_per_hour: 50
""")
    for n in ("boss", "reviewer", "lane.x"):
        agents.register(n)


def write_labels(path: str) -> None:
    """Every check from this run, one JSON object per line: state, test, name,
    and which occurrence of that (test, name) pair this is.

    Qualified by the owning test, since the name alone is not unique - two
    tests can and do call check() with the same literal string. The test is
    not enough either: a name repeated inside one test (typically a loop) is
    just as common, and without the occurrence ordinal every write but the
    last of a group would collide into it, so a flip in any but the last
    member of the group would be invisible to diff_labels(). JSON, one record
    per line, rather than text joined by a space and a tab: a check's own
    name or detail can itself carry a newline (an exception's message, not a
    name anyone chose - see run()) or one of the other line separators
    Python's own str.splitlines() knows, and text-joining would either split
    that record in two or glue it to its neighbour depending on where the
    break landed. Lets a mutation check diff two runs by label instead of by
    hand-built comm pipelines: see diff_labels().
    """
    seen: dict[tuple[str, str], int] = {}
    with open(path, "w") as f:
        for state, test, name in LABEL_LOG:
            key = (test, name)
            seen[key] = seen.get(key, 0) + 1
            f.write(json.dumps({"state": state, "test": test, "name": name,
                                 "occurrence": seen[key]}) + "\n")


def _read_labels(path: str) -> dict[tuple[str, str, int], str]:
    labels: dict[tuple[str, str, int], str] = {}
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        labels[(row["test"], row["name"], row["occurrence"])] = row["state"]
    return labels


def diff_labels(path_a: str, path_b: str) -> int:
    """Print only the labels whose PASS/FAIL state differs between two
    write_labels() files, "<state in A> -> <state in B>  <test> :: <name>"
    per line (plus "[<occurrence>]" past the first time that exact pair
    repeats within its test).

    A label present in only one file reads as MISSING on the other side -
    the two runs used a different -k, or a loop that calls check() the same
    number of times every run stopped doing so (auditor #1658 NOTE 3): the
    ordinal MISSING names is the group's new tail, not whichever occurrence
    actually left, since every occurrence after it renumbered down to fill
    the gap. Either way it is not itself a flipped state to chase.
    """
    a, b = _read_labels(path_a), _read_labels(path_b)
    changed = False
    for key in sorted(set(a) | set(b)):
        sa, sb = a.get(key, "MISSING"), b.get(key, "MISSING")
        if sa != sb:
            changed = True
            test, name, occurrence = key
            suffix = f" [{occurrence}]" if occurrence > 1 else ""
            print(f"{sa} -> {sb}  {test} :: {name}{suffix}")
    return 1 if changed else 0


WRITE_LABELS_TO: str | None = None  # set by --write-labels


# #1640: the founder's own screen-state vocabulary. The permanent-stop guard
# (section 8.4) - a state the daemon can write and nothing wakes.
def _states_written_in(path) -> set[str]:
    """Every literal state name a call to `set_agent_state` writes in one
    source file, however it spells the state: a bare constant (already equal
    to its own name in ocp/daemon/, e.g. `PARKED = "PARKED"`), a quoted literal
    (ask.py's own style), or one qualified by its module (`daemon.QUEUED` in
    ocp/actions/, `dmn_states.QUEUED` in ocp/cli/ - a miss here read both of those
    real call sites as writing nothing, which defeats the guard exactly where
    a state without one would be silent). Not a full parser - a regex over
    source text - but the one property this needs is only ever a name right
    after the two required positional arguments, and every real call site is
    one line, so it does not need to be more than that."""
    import re
    text = Path(path).read_text()
    return set(re.findall(r'set_agent_state\(\s*con\s*,\s*[^,]+,\s*(?:\w+\.)?"?([A-Z_]+)"?', text))

def use_home(home: Path) -> Path:
    """This test's OCP_HOME: the directory made, and ocp pointed at it."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    return home


def credentialed(home: Path, *accts: str) -> None:
    """A credentials file under each account, so it reads as logged in; the
    account itself is registered separately."""
    for acct in accts:
        (home / "accounts" / acct / ".claude").mkdir(parents=True, exist_ok=True)
        (home / "accounts" / acct / ".claude" / ".credentials.json").write_text("{}")


def write_status_json(agent_dir: Path, fields: dict) -> None:
    """A fake agent's status.json: the hook-written record `status.read_for_agent` reads."""
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "status.json").write_text(json.dumps(fields))


def orca_seen() -> None:
    """Orca is there and term_x is a live terminal."""
    orcamod.available = lambda: True
    orcamod.live_terminals = lambda: {"term_x"}
    orcamod.alive = lambda h: True


def orca_idle() -> None:
    """Orca is there and every terminal is idle."""
    orcamod.available = lambda: True
    orcamod.alive = lambda h: True
    orcamod.idle_now = lambda h: True


def orca_settled() -> None:
    """term_x is live and settled: both idle probes answer yes."""
    orcamod.live_terminals = lambda: {"term_x"}
    orcamod.alive = lambda h: True
    orcamod.wait_idle = lambda h, timeout_ms=0: True


__all__ = [
    'argparse',
    'atexit',
    'contextlib',
    'datetime',
    'hashlib',
    'http',
    'io',
    'json',
    'os',
    'pty',
    're',
    'select',
    'shlex',
    'shutil',
    'signal',
    'sqlite3',
    'subprocess',
    'sys',
    'tempfile',
    'threading',
    'time',
    'traceback',
    'types',
    'Path',
    'ROOT',
    'MACHINE_VENV',
    'TEST_ROOT',
    'accounts',
    'actions',
    'agents',
    'askmod',
    'blockedmod',
    'burnrate',
    'climod',
    'cliaccount',
    'cliask',
    'clibridge',
    'clidaemon',
    'clihealth',
    'clisetup',
    'clitask',
    'collect',
    'dmn_delivery',
    'dmn_devmode',
    'dmn_evaluate',
    'dmn_handoff',
    'dmn_lifecycle',
    'dmn_locks',
    'dmn_states',
    'dmn_reconciler',
    'dmn_loop',
    'dmn_usage_poll',
    'db',
    'hr',
    'digest',
    'doctor',
    'drift',
    'evmod',
    'foldmod',
    'handoffmod',
    'idle_close',
    'jobsmod',
    'launch',
    'loadmod',
    'lock',
    'measure',
    'obsmod',
    'orcamod',
    'paths',
    'recovermod',
    'rehome',
    'replaymod',
    'service',
    'spawnmod',
    'status',
    'team',
    'textsafe',
    'timefmt',
    'triggers',
    'trustmod',
    'tui',
    'usage_api',
    'vendors',
    'venvmod',
    'waiting',
    'yamlcompat',
    'ocmod',
    'PASS',
    'FAIL',
    'REAL_ORCA',
    'REAL_OBSERVE',
    'CURRENT_TEST',
    'FAKE_ORCA_DIR',
    'FAKE_ORCA_LOG',
    'ORCA_CHILD_TESTS',
    'calls_that_reached_the_stand_in_orca',
    '_REAL_POPEN_INIT',
    'ORCA_ATTEMPTS',
    '_is_orca',
    '_guarded_popen_init',
    'restore_world',
    '_page_text',
    '_snap',
    '_set_request_state',
    'relaunching_send',
    '_taken_prompts',
    'take_launched_prompt',
    'launched_prompt',
    'sessions_are',
    '_SESSION_TABLE',
    'exit_ends_session',
    '_deadline',
    '_capture',
    'LABEL_LOG',
    'check',
    'write_status',
    'readable_agent',
    'keep_handoff',
    'kept_handoff',
    'write_agent_status',
    '_seed_week_history',
    '_call_line',
    '_gamma_world',
    'LIVE_USAGE',
    'write_transcript',
    '_doctor_row',
    'DESIGN_TEAM',
    '_reconcile_world',
    '_daemon_home',
    '_hold_daemon_lock_in_another_process',
    'EXIT_DIALOG',
    'PASTED_DRAFT',
    '_COMPOSER_RULE_ROW',
    'COMPOSER_EMPTY',
    '_composer_typed',
    'ONE_HINT_ROW_COMPOSER',
    'PICKER_HINT_ONLY_ENTER_TO_RETURN',
    'PICKER_CURRENT_SESSION_SELECTED',
    'PICKER_HINT_ONLY_ENTER_TO_OPEN',
    'BACKGROUND_PICKER',
    'FLEET_HINT_QUOTE',
    'BACKGROUND_PICKER_SHORT',
    'PICKER_HINT_WITH_NO_BANNER',
    'BANNER_ABOVE_ORDINARY_COMPOSER',
    'BANNER_AND_HINT_QUOTED_ABOVE_BOX',
    '_screen_of',
    '_blocked_world',
    '_escalations',
    '_RULE_ROW',
    'TRUST_DIALOG',
    'PERMISSION_PROMPT',
    'YES_NO_DIALOG',
    'TRUST_DIALOG_WITH_A_DECOY_CANCEL',
    'TRUST_DIALOG_WITH_FREE_TEXT_ESC_LINE',
    'TRUST_DIALOG_WITH_ESC_MARK_ON_THE_TITLE',
    'CLEARED_SCREEN',
    'AT_THE_PROMPT',
    '_Terminal',
    '_ExitTerm',
    '_close_at',
    '_bg_of',
    '_decline_world',
    '_reports',
    '_mkrepo',
    '_worktree_with_a_dirty_file',
    'DIFFERENTIAL_CASES',
    'DUMP_STRINGS',
    'DUMP_VALUES',
    '_check_yaml_differential',
    '_python_with_pyyaml',
    '_parsed',
    '_roundtrip',
    '_refused',
    '_undefined_globals',
    'FakeOrca',
    'SecondVendor',
    'second_vendor',
    '_with_vendor',
    '_auth_list_out',
    '_OC_RULE',
    'OC_IDLE',
    'OC_WORKING',
    'OC_HOME',
    'OC_PERMISSION',
    '_raises',
    '_session_file',
    '_park_via_handoff',
    'REAL_ACCESS_TOKEN',
    '_process_on',
    'LIMIT_DIALOG',
    '_park_behind',
    '_fire_hook',
    '_Reading',
    '_samples_of',
    '_give_samples',
    '_with_accounts',
    '_running_on',
    '_reference_text',
    '_leaks',
    '_touches_subprocess_text',
    '_builds_a_returned_message',
    '_imports_textsafe',
    '_ANSI_TEXT_OK',
    'LIVE_TRUST_PROMPT_CAPTURE',
    'LIVE_TRUST_PROMPT_FOLDER',
    '_two_agents_one_slot',
    'zai_quota',
    'TEST_FILTER',
    '_RUN_DEPTH',
    '_test_name',
    'run',
    '_python_with_textual',
    '_board_world',
    '_cli',
    '_human_request',
    '_request',
    '_now_line',
    '_link',
    '_done_and_close',
    '_two_lanes',
    'write_labels',
    '_read_labels',
    'diff_labels',
    'WRITE_LABELS_TO',
    '_states_written_in',
    'run_area',
    'use_home',
    'credentialed',
    'write_status_json',
    'orca_seen',
    'orca_idle',
    'orca_settled',
]


def run_area(run_tests) -> int:
    """Run one area file's tests and report. `run_tests(td)` makes the run()
    calls inside one temp directory; -k, --write-labels and --diff-labels work
    on any area file as they do on the whole suite."""
    global TEST_FILTER, WRITE_LABELS_TO
    parser = argparse.ArgumentParser()
    parser.add_argument("-k", dest="filter", default=None,
                         help="run only test_* functions whose name contains this substring")
    parser.add_argument("--write-labels", dest="write_labels", default=None,
                         help="write every PASS/FAIL label from this run to a file")
    parser.add_argument("--diff-labels", dest="diff_labels", nargs=2, default=None,
                         metavar=("A", "B"),
                         help="print only the labels whose state differs between two "
                              "--write-labels files; does not run the suite")
    args = parser.parse_args()
    if args.diff_labels:
        return diff_labels(*args.diff_labels)
    TEST_FILTER = args.filter
    WRITE_LABELS_TO = args.write_labels
    # An agent session that runs this suite already has both set for itself,
    # and every test here that wants one sets it explicitly - so an ambient
    # value is never a fixture's own, only this process's, and left in place
    # it is read as a false speaker or a false account by code under test.
    os.environ.pop("OCP_AGENT", None)
    os.environ.pop("OCP_ACCOUNT", None)
    with tempfile.TemporaryDirectory() as td:
        run_tests(td)
    # The whole run's dealings with Orca: none of its tests may have reached even
    # the stand-in, except the four that run a child `ocp`. (A test's own
    # `except Exception` can swallow the guard's error; this cannot be swallowed.)
    reached = calls_that_reached_the_stand_in_orca()
    stray = {t: c for t, c in reached.items() if t not in ORCA_CHILD_TESTS}
    check("orca: no test reached Orca except through the child ocp it runs",
          not stray, "; ".join(f"{t}: {c[0]}" for t, c in sorted(stray.items())[:4]))
    check("orca: no test started an orca process itself, whether or not it caught the error",
          not ORCA_ATTEMPTS, "; ".join(f"{t}: {c}" for t, c in ORCA_ATTEMPTS[:4]))
    if WRITE_LABELS_TO is not None:
        write_labels(WRITE_LABELS_TO)
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0
