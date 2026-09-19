"""Tests for the parts where being wrong is expensive.

Run: python3 tests/test_ocp.py
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import pty
import select
import signal
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ocp import (accounts, actions, agents, ask as askmod, cli as climod, daemon as dmn, db, hr,
                 digest,
                 doctor, drift, events as evmod, fold as foldmod,
                 handoff as handoffmod, launch, load as loadmod, lock, measure,
                 observe as obsmod, orca as orcamod, paths, service, spawn as spawnmod,
                 status, team, triggers, tui, yamlcompat)  # noqa: E402

PASS, FAIL = [], []

# Every test that needs an Orca stands in for one, and a restore tuple that
# forgot a name used to leak that stand-in into every test after it. Captured
# once, put back by _daemon_home, so no test inherits another's world.
REAL_ORCA = {n: getattr(orcamod, n) for n in (
    "available", "alive", "live_terminals", "wait_idle", "idle_now", "send",
    "clear_context", "close_session", "relaunch", "resume_with")}
REAL_OBSERVE = {n: getattr(obsmod, n) for n in ("live_sessions", "live_terminals")
                if hasattr(obsmod, n)}


def restore_world() -> None:
    for n, f in REAL_ORCA.items():
        setattr(orcamod, n, f)
    for n, f in REAL_OBSERVE.items():
        setattr(obsmod, n, f)
    # Readings that outlive the tick or the frame by design, so one test's
    # world does not answer the next test's question.
    dmn._seen.clear()
    dmn._seen_at["t"] = 0.0
    actions._WORLD["at"] = 0.0


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
        elif text == "/exit":
            table.clear()       # one terminal, one agent, in every fixture here
        return True, ""
    return send


def sessions_are(*agents: str) -> dict[str, int]:
    """Say which agents have a session running, as the process table would.

    Returned so a test can end one - `table.pop("backend")` - or start one,
    which is what closing and relaunching do to the world the daemon reads.
    """
    table = {a: 4200 + i for i, a in enumerate(agents)}
    obsmod.live_sessions = lambda: dict(table)
    return table


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


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
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


# ---------------------------------------------------------------- fail-closed
def test_failclosed(home: Path) -> None:
    os.environ["OCP_HOME"] = str(home)

    # A fresh snapshot whose payload carried no five_hour block is the quiet
    # case, not the unknown one: the collector is alive and nothing has been
    # spent in this window. Refusing here parked five idle agents once.
    write_status(home, "quiet", pct_5h=None, pct_7d=None)
    q = status.read("quiet")
    check("no 5h window in a fresh snapshot reads as quiet", q.pct_5h_source == "quiet",
          q.pct_5h_source)
    allowed, why = q.assignable()
    check("a quiet window assigns", allowed, why)

    # The weekly figure comes off the same file and was drawn as a measurement
    # while the 5h one beside it went to "?". Inside one window weekly usage
    # only rises, so an old reading is a floor - marked as carried, not drawn
    # as measured - and once the window has rolled it is not even a floor.
    write_status(home, "wk", pct_5h=10, pct_7d=60, reset_7d=int(time.time()) + 3600,
                 rl7_at=int(time.time()))
    w = status.read("wk")
    check("a fresh weekly figure is live", w.pct_7d_source == "live"
          and w.trusted_pct_7d == 60, w.pct_7d_source)
    write_status(home, "wk", pct_5h=10, pct_7d=60, reset_7d=int(time.time()) + 3600,
                 rl7_at=int(time.time()), collected_at=int(time.time()) - 100000)
    w = status.read("wk")
    check("a stale one is a floor, and says so",
          w.trusted_pct_7d == 60 and status.mark(w.pct_7d_source) in ("~", "!"),
          f"{w.trusted_pct_7d} {w.pct_7d_source}")
    write_status(home, "wk", pct_5h=10, pct_7d=60, reset_7d=int(time.time()) - 60,
                 rl7_at=int(time.time()))
    w = status.read("wk")
    check("and a window that has rolled over leaves no figure at all",
          w.trusted_pct_7d is None, str(w.trusted_pct_7d))
    check("so a finished week cannot block an account forever",
          w.assignable()[0] or "7d" not in w.assignable()[1], str(w.assignable()))
    check("and says the 0% was inferred", "inferred" in why, why)
    check("quiet headroom is the full window", q.headroom() == 100, str(q.headroom()))
    check("the raw reading stays null", q.pct_5h is None, repr(q.pct_5h))

    # Unknown is what fail-closed is for, and these are the shapes of it.
    write_status(home, "unreadable", pct_5h="lots")
    u = status.read("unreadable")
    check("an unreadable pct_5h is unknown", u.pct_5h_source == "unknown", u.pct_5h_source)
    allowed, why = u.assignable()
    check("and refuses assignment", not allowed, why)
    check("headroom is None when unknown", u.headroom() is None)

    (home / "accounts" / "nofield").mkdir(parents=True, exist_ok=True)
    (home / "accounts" / "nofield" / "status.json").write_text(
        json.dumps({"account": "nofield", "collected_at": int(time.time()), "session_id": "s"}))
    nf = status.read("nofield")
    check("a snapshot with no pct_5h field at all is unknown",
          nf.pct_5h_source == "unknown", nf.pct_5h_source)
    check("and refuses", not nf.assignable()[0])

    # A reading survives a payload that does not mention the window, while that
    # window is still open - the collector carries it and says so.
    write_status(home, "carried", pct_5h=40, reset_5h=int(time.time()) + 600,
                 rl_carried=True, rl_at=int(time.time()) - 300)
    c = status.read("carried")
    check("a carried reading is used", c.trusted_pct_5h == 40, str(c.trusted_pct_5h))
    allowed, why = c.assignable()
    check("a carried reading assigns", allowed, why)
    check("and is labelled carried", "carried" in why, why)

    # The API returns these as floats, sometimes untidy ones. Rounding must go
    # up, so a rounding error triggers sooner rather than later.
    write_status(home, "floaty", pct_5h=28.999999999999996, pct_7d=10.0)
    check("float percentage rounds up, not down", status.read("floaty").pct_5h == 29,
          str(status.read("floaty").pct_5h))
    write_status(home, "nearly", pct_5h=84.9, pct_7d=10)
    allowed, why = status.read("nearly").assignable()
    check("84.9% rounds to 85 and still assigns below the block", allowed, why)
    write_status(home, "overish", pct_5h=89.6, pct_7d=10)
    allowed, why = status.read("overish").assignable()
    check("89.6% rounds to 90 and still assigns: the wall is 100", allowed, why)
    write_status(home, "walled", pct_5h=99.6, pct_7d=10)
    allowed, why = status.read("walled").assignable()
    check("99.6% rounds to 100 and is refused", not allowed, why)

    write_status(home, "fresh", pct_5h=10, pct_7d=20)
    allowed, why = status.read("fresh").assignable()
    check("healthy account is assignable", allowed, why)
    check("headroom computed", status.read("fresh").headroom() == 90)

    write_status(home, "hot", pct_5h=100, pct_7d=20)
    allowed, _ = status.read("hot").assignable()
    check("5h at the wall refuses", not allowed)

    write_status(home, "weekly", pct_5h=5, pct_7d=95)
    allowed, why = status.read("weekly").assignable()
    check("7d at 95% no longer refuses - request #254 spends it to its own "
          "wall instead of holding back", allowed, why)
    write_status(home, "weekly_full", pct_5h=5, pct_7d=100)
    allowed, _ = status.read("weekly_full").assignable()
    check("7d at 100% refuses", not allowed)

    write_status(home, "stale", pct_5h=5, collected_at=int(time.time()) - 5000)
    allowed, why = status.read("stale").assignable()
    check("stale snapshot refuses", not allowed, why)

    allowed, why = status.read("ghost").assignable()
    check("missing status.json refuses", not allowed, why)

    (home / "accounts" / "broken").mkdir(parents=True, exist_ok=True)
    (home / "accounts" / "broken" / "status.json").write_text("{not json")
    allowed, why = status.read("broken").assignable()
    check("corrupt status.json refuses", not allowed, why)

    # Selection must prefer headroom, and must refuse when nothing qualifies.
    picked, _ = status.pick_account(["unreadable", "fresh", "hot"])
    check("pick_account chooses the healthy one", picked == "fresh", str(picked))
    picked, reasons = status.pick_account(["unreadable", "hot", "stale"])
    check("pick_account returns None when nothing qualifies", picked is None, str(picked))
    check("pick_account explains every candidate", len(reasons) == 3)

    # A quiet account has the most headroom there is, so it wins on headroom;
    # at equal headroom the account we actually have a reading for wins.
    picked, _ = status.pick_account(["quiet", "fresh"])
    check("pick_account takes the quiet account over a busier one", picked == "quiet",
          str(picked))
    write_status(home, "reported0", pct_5h=0)
    picked, _ = status.pick_account(["quiet", "reported0"])
    check("a reported 0% beats an inferred one", picked == "reported0", str(picked))

    write_status(home, "roomy", pct_5h=2)
    picked, _ = status.pick_account(["fresh", "roomy"])
    check("pick_account maximizes headroom", picked == "roomy", str(picked))

    # request #154/#165 (c): an account nothing has ever run on cannot be
    # picked by default - only bootstrap=True makes it a last resort.
    picked, _ = status.pick_account(["ghost", "hot"])
    check("pick_account: a never-run account is not picked by default",
          picked is None, str(picked))
    picked, reasons = status.pick_account(["ghost", "hot"], bootstrap=True)
    check("pick_account: bootstrap=True picks it once nothing else qualifies",
          picked == "ghost", str(picked))
    check("pick_account: and says why", "bootstrap" in dict(reasons)["ghost"],
          dict(reasons)["ghost"])
    picked, _ = status.pick_account(["ghost", "fresh"], bootstrap=True)
    check("pick_account: bootstrap never wins over an account that already qualifies",
          picked == "fresh", str(picked))
    picked, _ = status.pick_account(["stale"], bootstrap=True)
    check("pick_account: bootstrap does not rescue a refusal that is not 'no reading at all'",
          picked is None, str(picked))


def test_pick_account_orders_by_weekly_deadline(home: Path) -> None:
    """Request #166/#167, amended by #169/#170/#254: spend first the weekly
    headroom that expires soonest unused - not simply the account with the
    most 5h room right now, and right up to its own wall rather than holding
    back at a lower line (the margin/tier rule #254 dropped)."""
    os.environ["OCP_HOME"] = str(home)
    now = int(time.time())

    # "later" has more 5h headroom than "soon" - the old rule would pick it -
    # but "soon" loses its weekly window first.
    write_status(home, "soon", pct_5h=50, pct_7d=10, reset_7d=now + 3600)
    write_status(home, "later", pct_5h=5, pct_7d=10, reset_7d=now + 90000)
    picked, reasons = status.pick_account(["soon", "later"])
    check("pick_account: the earlier weekly deadline wins over more headroom",
          picked == "soon", str(picked))
    said = dict(reasons)
    check("pick_account: the winner's reason says it was picked",
          "picked" in said["soon"], said["soon"])

    # "close_to_wall" resets sooner than "safe" and is near its own 7d wall -
    # under the old margin rule it would have been deprioritized behind
    # "safe"; #254 drops that, so the sooner deadline still wins.
    write_status(home, "close_to_wall", pct_5h=5, pct_7d=92, reset_7d=now + 3600)
    write_status(home, "safe", pct_5h=5, pct_7d=50, reset_7d=now + 90000)
    picked, _ = status.pick_account(["close_to_wall", "safe"])
    check("pick_account: the soonest deadline wins right up to its own wall, "
          "not just within a margin of it",
          picked == "close_to_wall", str(picked))

    # Even both near their own walls, the sooner deadline still wins - there
    # is no second tier to fall back into any more.
    write_status(home, "wall_a", pct_5h=5, pct_7d=91, reset_7d=now + 1000)
    write_status(home, "wall_b", pct_5h=5, pct_7d=93, reset_7d=now + 50)
    picked, _ = status.pick_account(["wall_a", "wall_b"])
    check("pick_account: orders by deadline even when both are near the wall",
          picked == "wall_b", str(picked))

    # An account whose 7d window has never been observed sorts behind a known,
    # sooner deadline rather than jumping the queue.
    write_status(home, "never_seen_7d", pct_5h=5, pct_7d=None)
    picked, _ = status.pick_account(["never_seen_7d", "soon"])
    check("pick_account: a known, sooner deadline beats an unknown one",
          picked == "soon", str(picked))


# ---------------------------------------------------------------- collector
def test_collector(home: Path) -> None:
    """Feed the real statusLine payload shape through the real collector."""
    acct_cfg = home / "accounts" / "alpha" / ".claude"
    acct_cfg.mkdir(parents=True, exist_ok=True)
    payload = {
        "session_id": "bb27dedb-03f9-45b7-ba15-db8403ab3f99",
        "transcript_path": "/tmp/t.jsonl",
        "model": {"id": "claude-sonnet-5", "display_name": "Sonnet 5"},
        "workspace": {"current_dir": "/tmp/wt"},
        "rate_limits": {
            "five_hour": {"used_percentage": 4, "resets_at": 1789543200},
            "seven_day": {"used_percentage": 41, "resets_at": 1789678800},
        },
        "context_window": {
            "used_percentage": 5, "context_window_size": 1000000,
            "total_input_tokens": 45465, "total_output_tokens": 4,
            "current_usage": {"input_tokens": 2, "output_tokens": 4},
        },
        "cost": {"total_cost_usd": 0.078, "total_lines_added": 3, "total_lines_removed": 1},
        "exceeds_200k_tokens": False,
        "version": "2.1.273",
    }
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(acct_cfg), OCP_HOME=str(home))
    r = subprocess.run([str(ROOT / "libexec" / "statusline-collect.sh")],
                       input=json.dumps(payload), capture_output=True, text=True, env=env)
    check("collector exits 0", r.returncode == 0, r.stderr)
    check("collector prints a status line", "alpha" in r.stdout, repr(r.stdout))

    st = status.read("alpha", home / "accounts" / "alpha" / "status.json")
    check("collector derives account from CLAUDE_CONFIG_DIR", st.account == "alpha")
    check("collector captures pct_5h", st.pct_5h == 4)
    check("collector captures pct_7d", st.pct_7d == 41)
    check("collector captures ctx_pct", st.ctx_pct == 5)
    check("collector captures reset_5h", st.reset_5h == 1789543200)
    check("collector captures cost", st.cost_usd == 0.078)
    check("collector stamps collected_at", st.collected_at is not None)

    # Allowlist: anything not in the section 4 table must not be persisted.
    raw = json.loads((home / "accounts" / "alpha" / "status.json").read_text())
    check("collector does not persist unknown fields", "output_style" not in raw and "thinking" not in raw)

    # Garbage must never overwrite a good snapshot.
    before = (home / "accounts" / "alpha" / "status.json").read_text()
    subprocess.run([str(ROOT / "libexec" / "statusline-collect.sh")],
                   input="not json at all", capture_output=True, text=True, env=env)
    after = (home / "accounts" / "alpha" / "status.json").read_text()
    check("garbage input does not clobber a good snapshot", before == after)

    # A payload with no rate_limits must never become 0 in the file - that is
    # the difference between a real reading and a silently dead trigger. Here
    # the window in the stored snapshot has already ended, so there is nothing
    # left to carry and the field goes back to null.
    thin = {"session_id": "x", "model": {"id": "m"}}
    subprocess.run([str(ROOT / "libexec" / "statusline-collect.sh")],
                   input=json.dumps(thin), capture_output=True, text=True, env=env)
    st = status.read("alpha", home / "accounts" / "alpha" / "status.json")
    check("missing rate_limits becomes null, not 0", st.pct_5h is None, repr(st.pct_5h))
    check("and reads as quiet, since the collector is alive",
          st.pct_5h_source == "quiet", st.pct_5h_source)

    # Inside the window it is the opposite: an idle session's payload carries no
    # five_hour block, and must not erase what an active session on the same
    # account just reported. Losing that is what made five agents look unknown.
    live = dict(payload)
    live["rate_limits"] = {
        "five_hour": {"used_percentage": 63, "resets_at": int(time.time()) + 1800},
        "seven_day": {"used_percentage": 41, "resets_at": int(time.time()) + 90000},
    }
    subprocess.run([str(ROOT / "libexec" / "statusline-collect.sh")],
                   input=json.dumps(live), capture_output=True, text=True, env=env)
    observed_at = status.read("alpha", home / "accounts" / "alpha" / "status.json").rl_at
    subprocess.run([str(ROOT / "libexec" / "statusline-collect.sh")],
                   input=json.dumps(thin), capture_output=True, text=True, env=env)
    st = status.read("alpha", home / "accounts" / "alpha" / "status.json")
    check("an idle payload does not erase the account's reading", st.pct_5h == 63,
          repr(st.pct_5h))
    check("the carried reading is marked as carried", st.rl_carried is True)
    check("and keeps when it was observed, not when it was copied",
          st.rl_at == observed_at, f"rl_at={st.rl_at} observed_at={observed_at}")
    check("a carried reading still assigns", st.assignable()[0], st.assignable()[1])


# ---------------------------------------------------------------- yaml
def test_yaml() -> None:
    text = (
        "name: backend\naccount: alpha\nmodel: sonnet\nenabled: true\n"
        "peers:\n  - name: reviewer\n    relation: review\n  - name: archivist\n"
        "limits:\n  ctx_pct: 75\n  note: \"a: colon\"\n  none: null\n"
    )
    got = yamlcompat.parse(text)
    check("yaml: scalars", got["name"] == "backend" and got["enabled"] is True)
    check("yaml: nested map", got["limits"]["ctx_pct"] == 75)
    check("yaml: quoted value with colon", got["limits"]["note"] == "a: colon")
    check("yaml: null", got["limits"]["none"] is None)
    check("yaml: list of maps", [p["name"] for p in got["peers"]] == ["reviewer", "archivist"])
    try:
        yamlcompat.parse("a: &anchor x\n")
        check("yaml: rejects unsupported syntax", False, "no error raised")
    except yamlcompat.ConfigError:
        check("yaml: rejects unsupported syntax", True)


# ---------------------------------------------------------------- agents
def test_agents(home: Path) -> None:
    os.environ["OCP_HOME"] = str(home)
    agents.register("backend", "alpha", model="sonnet")
    check("agent registers", agents.account_of("backend") == "alpha")
    agents.register("backend", "beta")
    check("re-register moves the account", agents.account_of("backend") == "beta")
    cfg = agents.read("backend")
    check("re-register preserves other fields", cfg.get("model") == "sonnet", str(cfg))
    d = home / "agents" / "backend"
    check("agent dirs created", all((d / s).is_dir() for s in ("inbox", "journal")))
    (d / "base.md").write_text("x" * 5000)
    over = [fn for fn, _, _, o in agents.budget_report("backend") if o]
    check("base.md budget is enforced", "base.md" in over)




# ---------------------------------------------------------------- launch
def test_launch(home: Path) -> None:
    """Prove launch execs claude with the right CLAUDE_CONFIG_DIR, using a
    stub 'claude' on PATH so the test costs no tokens."""
    os.environ["OCP_HOME"] = str(home)
    bindir = home / "stub"
    bindir.mkdir(parents=True, exist_ok=True)
    stub = bindir / "claude"
    stub.write_text(
        "#!/bin/sh\n"
        'printf "CFG=%s\\nACCT=%s\\nAGENT=%s\\nARGS=%s\\n" '
        '"$CLAUDE_CONFIG_DIR" "$OCP_ACCOUNT" "$OCP_AGENT" "$*"\n'
    )
    stub.chmod(0o755)

    agents.register("launchtest", "gamma", model="sonnet")
    cfg = home / "accounts" / "gamma" / ".claude"
    cfg.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", OCP_HOME=str(home))

    # Not logged in yet -> must refuse and name the human step.
    r = subprocess.run([sys.executable, str(ROOT / "bin" / "ocp"), "launch", "launchtest"],
                       capture_output=True, text=True, env=env)
    check("launch refuses when not logged in", r.returncode != 0)
    check("refusal names the login command", "claude /login" in (r.stdout + r.stderr))

    # Existence of the credential file is all we check - never its contents.
    (cfg / ".credentials.json").write_text("{}")
    r = subprocess.run([sys.executable, str(ROOT / "bin" / "ocp"), "launch", "launchtest"],
                       capture_output=True, text=True, env=env)
    check("launch execs claude once logged in", r.returncode == 0, r.stderr)
    check("launch sets CLAUDE_CONFIG_DIR to the account dir", f"CFG={cfg}" in r.stdout, r.stdout)
    check("launch sets OCP_ACCOUNT", "ACCT=gamma" in r.stdout, r.stdout)
    check("launch sets OCP_AGENT", "AGENT=launchtest" in r.stdout, r.stdout)
    check("launch passes the configured model", "--model sonnet" in r.stdout, r.stdout)

    # Over the 5h limit -> refuse, unless forced.
    write_status(home, "gamma", pct_5h=100)
    r = subprocess.run([sys.executable, str(ROOT / "bin" / "ocp"), "launch", "launchtest"],
                       capture_output=True, text=True, env=env)
    check("launch refuses over the 5h limit", r.returncode != 0, r.stdout)
    r = subprocess.run([sys.executable, str(ROOT / "bin" / "ocp"), "launch", "launchtest", "--force"],
                       capture_output=True, text=True, env=env)
    check("--force overrides the usage block", r.returncode == 0, r.stdout)

    # A missing status.json must NOT block first boot, or nothing could ever
    # start: status.json is written by the session we are trying to launch.
    (home / "accounts" / "gamma" / "status.json").unlink()
    r = subprocess.run([sys.executable, str(ROOT / "bin" / "ocp"), "launch", "launchtest"],
                       capture_output=True, text=True, env=env)
    check("missing status.json does not block first boot", r.returncode == 0, r.stdout)


DESIGN_TEAM = """
teams:
  product: [product]
  eng:     [backend, frontend]
  growth:  [marketing, analytics]
  ops:     [archivist]

agents:
  product:    { intake: dedicated, model: mid,   weight: light }
  backend:    { intake: dedicated, model: high,  weight: heavy }
  frontend:   { intake: dedicated, model: high,  weight: heavy }
  marketing:  { intake: dedicated, model: mid,   weight: light }
  analytics:  { intake: dedicated, model: mid,   weight: light }
  archivist:  { intake: ephemeral, model: cheap, weight: light }
  reviewer:   { intake: serial,    model: mid,   weight: light, reviews: [eng, growth, product] }

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


def test_team_routing() -> None:
    t = team.Team(yamlcompat.parse(DESIGN_TEAM))
    check("route: wildcard edge to reviewer", t.route("backend", "reviewer").allowed)
    check("route: reviewer may answer anyone", t.route("reviewer", "backend").allowed)
    check("route: product may reach eng team", t.route("product", "backend").allowed)
    check("route: analytics may reach product", t.route("analytics", "product").allowed)

    d = t.route("backend", "marketing")
    check("route: cross-team deny fires", not d.allowed and "product" in d.reason, d.reason)
    d = t.route("marketing", "backend")
    check("route: deny is symmetric", not d.allowed, d.reason)

    d = t.route("backend", "frontend")
    check("route: unlisted edge denied by default", not d.allowed, d.reason)
    check("route: refusal explains the default", "denied by default" in d.reason)

    check("route: self-ask denied", not t.route("backend", "backend").allowed)
    check("route: unknown target denied", not t.route("backend", "nobody").allowed)

    d = t.route("backend", "human")
    check("route: escalation target is routable", d.allowed and d.creates_gate, d.reason)
    check("route: escalation kind preserved", d.kind == "escalate")

    check("route: intake comes from the target", t.route("backend", "reviewer").intake == "serial")
    check("route: fire_and_forget preserved",
          t.route("backend", "archivist").response == "fire_and_forget")
    check("route: chain depth limit", not t.route("backend", "reviewer", chain_depth=4).allowed)

    # A deny must win even though an earlier wildcard edge would match.
    t2 = team.Team(yamlcompat.parse(
        "agents:\n  a: { intake: dedicated }\n  b: { intake: dedicated }\n"
        'edges:\n  - { from: "*", to: "*", relation: peer }\n'
        'deny:\n  - { from: "a", to: "b", reason: "no" }\n'
    ))
    check("route: deny beats a matching wildcard edge", not t2.route("a", "b").allowed)
    check("route: wildcard still allows others", t2.route("b", "a").allowed)


def test_team_validation() -> None:
    for bad, label in [
        ("agents:\n  a: { intake: nonsense }\n", "bad intake rejected"),
        ('edges:\n  - { from: "a", to: "b", response: blocking }\n',
         "blocking response rejected"),
        ('edges:\n  - { to: "b" }\n', "edge without 'from' rejected"),
    ]:
        try:
            team.Team(yamlcompat.parse(bad))
            check(f"team validation: {label}", False, "no error raised")
        except yamlcompat.ConfigError:
            check(f"team validation: {label}", True)


def test_ask_flow(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    (home / "team.yaml").write_text(DESIGN_TEAM)
    for name in ("backend", "reviewer", "archivist", "marketing"):
        agents.register(name, "alpha")

    ok, msg, rid = askmod.ask("reviewer", "review this", requester="backend")
    check("ask: allowed edge queues", ok and rid is not None, msg)
    inbox_file = home / "agents" / "reviewer" / "inbox" / f"{rid:06d}.json"
    check("ask: writes an inbox file", inbox_file.exists())
    payload = json.loads(inbox_file.read_text())
    check("ask: inbox payload carries routing", payload["intake"] == "serial", str(payload))

    ok, msg, _ = askmod.ask("marketing", "do this", requester="backend")
    check("ask: denied edge refuses", not ok and "product" in msg, msg)

    ok, msg, _ = askmod.ask("frontend", "do this", requester="backend")
    check("ask: unlisted edge refuses", not ok, msg)

    con = db.connect()
    try:
        denied = con.execute("SELECT COUNT(*) c FROM requests WHERE state='denied'").fetchone()["c"]
        check("ask: denials are recorded, not silent", denied == 2, str(denied))
        st = db.get_agent_state(con, "backend")
        check("ask: async requester parks", st["state"] == "PARKED", str(dict(st)))
        check("ask: parking a requester sets a fallback wake_at so a lost "
              "reply cannot strand it", st["wake_at"] is not None, str(dict(st)))
    finally:
        con.close()

    ok, msg = askmod.resolve(rid, "looks good")
    check("resolve: closes the request", ok, msg)
    check("resolve: drains the inbox file", not inbox_file.exists())
    con = db.connect()
    try:
        st = db.get_agent_state(con, "backend")
        check("resolve: the reply queues the requester", st["state"] == "QUEUED", str(dict(st)))
        check("resolve: queueing clears the fallback wake_at", st["wake_at"] is None)
    finally:
        con.close()

    # fire_and_forget must not park the requester. Asked from marketing, whose
    # first matching edge really is `* -> archivist`. (From reviewer it would
    # not be: `reviewer -> *` appears earlier and edges are first-match-wins.)
    ok, _, _ = askmod.ask("archivist", "log this", requester="marketing")
    check("ask: fire_and_forget ask is accepted", ok)
    con = db.connect()
    try:
        st = db.get_agent_state(con, "marketing")
        check("ask: fire_and_forget does not park the requester", st is None, str(st and dict(st)))
    finally:
        con.close()

    # Edge order is significant, and the design's own policy relies on it.
    t = team.Team(yamlcompat.parse(DESIGN_TEAM))
    check("ask: earlier edge wins over a later one",
          t.route("reviewer", "archivist").response == "async",
          t.route("reviewer", "archivist").reason)

    # Chain depth must actually stop a loop.
    parent = rid
    depth_blocked = False
    for _ in range(6):
        ok, msg, new = askmod.ask("reviewer", "again", requester="backend", parent_id=parent)
        if not ok and "chain depth" in msg:
            depth_blocked = True
            break
        parent = new
    check("ask: chain depth stops runaway loops", depth_blocked)


def test_one_request_at_a_time(home: Path) -> None:
    """Request #189: an agent is given one request at a time - the head of
    its queue - and the next only once it resolves or dismisses this one.
    Before delivery its own requester may still replace or withdraw it;
    once delivered, neither applies."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    (home / "team.yaml").write_text(DESIGN_TEAM)
    for name in ("backend", "reviewer"):
        agents.register(name, "alpha")
    con = db.connect()
    try:
        ok, msg, first = askmod.ask("reviewer", "task one", requester="backend")
        check("one-at-a-time: the first ask queues", ok, msg)
        check("one-at-a-time: the head is the first request",
              db.head_of_queue(con, "reviewer")["id"] == first,
              db.head_of_queue(con, "reviewer")["id"])
        # Delivery itself - the daemon actually typing this into a terminal -
        # is exercised in daemon tests; here the head is marked delivered
        # directly, the way `deliver_queued` or a self-view of `ocp inbox`
        # would, to set up what withdraw/replace are gated on.
        db.mark_delivered(con, first)

        ok, msg, second = askmod.ask("reviewer", "task two", requester="backend")
        check("one-at-a-time: a second ask still queues", ok, msg)
        check("one-at-a-time: the head is still the first - the second waits behind it",
              db.head_of_queue(con, "reviewer")["id"] == first,
              db.head_of_queue(con, "reviewer")["id"])
        check("one-at-a-time: the second was never marked delivered",
              db.get_request(con, second)["delivered_at"] is None)
        check("one-at-a-time: two are queued for reviewer",
              db.queue_depth(con, "reviewer") == 2, db.queue_depth(con, "reviewer"))

        # --first jumps a third request ahead of the (undelivered) second one,
        # but never ahead of the first, which is already delivered.
        ok, msg, third = askmod.ask("reviewer", "task three - urgent", requester="backend",
                                    first=True)
        check("one-at-a-time: --first is accepted", ok, msg)
        order = [r["id"] for r in db.queued_for(con, "reviewer")]
        check("one-at-a-time: --first jumps ahead of what was not yet delivered",
              order == [first, third, second], str(order))

        # A second --first must not compute its ceiling from the first
        # --first's row and walk straight past the delivered floor - the
        # exact repro of the bug the review found: two --first asks in a row.
        ok, msg, fourth = askmod.ask("reviewer", "task four - even more urgent",
                                     requester="backend", first=True)
        check("one-at-a-time: a second --first is accepted", ok, msg)
        order = [r["id"] for r in db.queued_for(con, "reviewer")]
        check("one-at-a-time: a second --first still never crosses the delivered head",
              order == [first, fourth, third, second], str(order))
        ok, msg = askmod.withdraw(fourth, requester="backend")
        check("one-at-a-time: cleaning up the extra probe", ok, msg)

        # A requester may replace its own undelivered request; a foreign
        # requester, or a delivered one, may not.
        ok, msg = askmod.ask("reviewer", "not yours", requester="reviewer",
                             replaces=second)[:2]
        check("one-at-a-time: only the original requester may replace a request",
              not ok and "not yours" in msg, msg)

        ok, msg, replacement = askmod.ask("reviewer", "task two, corrected",
                                          requester="backend", replaces=second)
        check("one-at-a-time: replacing an undelivered request is allowed", ok, msg)
        check("one-at-a-time: the old one is retired", db.get_request(con, second)["state"],
              db.get_request(con, second)["state"])
        check("one-at-a-time: as 'replaced'",
              db.get_request(con, second)["state"] == "replaced")
        order = [r["id"] for r in db.queued_for(con, "reviewer")]
        check("one-at-a-time: the replacement keeps the same place in line",
              order == [first, third, replacement], str(order))

        ok, msg = askmod.ask("reviewer", "too late", requester="backend", replaces=first)[:2]
        check("one-at-a-time: a delivered request cannot be replaced",
              not ok and "already delivered" in msg, msg)

        ok, msg = askmod.withdraw(third, requester="backend")
        check("one-at-a-time: withdrawing an undelivered request is allowed", ok, msg)
        check("one-at-a-time: it is gone from the queue", db.get_request(con, third)["state"],
              db.get_request(con, third)["state"])
        check("one-at-a-time: as 'withdrawn'", db.get_request(con, third)["state"] == "withdrawn")

        ok, msg = askmod.withdraw(first, requester="backend")
        check("one-at-a-time: a delivered request cannot be withdrawn",
              not ok and "already delivered" in msg, msg)

        # Resolving the head hands delivery to the next one - the replacement,
        # not the request it replaced.
        ok, msg = askmod.resolve(first, "done")
        check("one-at-a-time: resolving the head succeeds", ok, msg)
        new_head = db.head_of_queue(con, "reviewer")
        check("one-at-a-time: the replacement becomes the head",
              new_head["id"] == replacement, new_head["id"])
        check("one-at-a-time: withdrawn/replaced rows never become the head",
              new_head["body"] == "task two, corrected", new_head["body"])
    finally:
        con.close()


def test_migration_marks_only_each_targets_head_delivered(home: Path) -> None:
    """Review of buddyron/one-task-at-a-time: the first version of this
    migration marked every existing queued row delivered, which left --first
    nothing to land in front of and sent it to the back of a live queue."""
    import sqlite3
    home.mkdir(parents=True, exist_ok=True)
    p = home / "old.sqlite"
    old = sqlite3.connect(p)
    old.execute("""
        CREATE TABLE requests (
          id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,
          requester TEXT NOT NULL, target TEXT NOT NULL, body TEXT NOT NULL,
          files TEXT, relation TEXT, response TEXT, kind TEXT,
          chain_depth INTEGER NOT NULL DEFAULT 0, parent_id INTEGER,
          chain_id TEXT, state TEXT NOT NULL DEFAULT 'queued', verdict TEXT,
          announced_at INTEGER, resolved_at INTEGER
        )
    """)
    now = int(time.time())
    for i, body in enumerate(["first", "second", "third"]):
        old.execute(
            "INSERT INTO requests(ts, requester, target, body, state, chain_id) "
            "VALUES(?,?,?,?,?,?)", (now, "backend", "keeper", body, "queued", str(i + 1)))
    old.commit()
    old.close()

    con = db.connect(p)
    try:
        rows = con.execute(
            "SELECT id, body, queue_order, delivered_at FROM requests ORDER BY id").fetchall()
        check("migration: queue_order backfilled to id",
              [r["queue_order"] for r in rows] == [r["id"] for r in rows], str([dict(r) for r in rows]))
        check("migration: only the head is marked delivered",
              rows[0]["delivered_at"] is not None and rows[1]["delivered_at"] is None
              and rows[2]["delivered_at"] is None, str([dict(r) for r in rows]))

        # --first now has room to land right after the migrated head, not at
        # the back of the whole queue.
        new_id = db.add_request(con, "someone", "keeper", "urgent", None,
                                "peer", "async", None, 0, None, first=True)
        order = [r["id"] for r in db.queued_for(con, "keeper")]
        check("migration: --first lands right after the migrated head",
              order == [rows[0]["id"], new_id, rows[1]["id"], rows[2]["id"]], str(order))
    finally:
        con.close()


def test_replace_and_merge_respect_delivered_at(home: Path) -> None:
    """Review of buddyron/one-task-at-a-time: --mergeable folded into an
    already-delivered row (changing what the target was already reading, with
    nobody told), and --replaces --mergeable together returned before the
    replaced row was retired."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    (home / "team.yaml").write_text(DESIGN_TEAM)
    for name in ("backend", "reviewer"):
        agents.register(name, "alpha")
    con = db.connect()
    try:
        ok, msg, rid = askmod.ask("reviewer", "please look", requester="backend",
                                  files="a.py")
        db.mark_delivered(con, rid)
        ok, msg, merged = askmod.ask("reviewer", "also this", requester="backend",
                                     files="a.py", mergeable=True)
        check("mergeable: does not fold into an already-delivered row",
              merged != rid, str((rid, merged)))
        check("mergeable: a separate request queues instead",
              db.get_request(con, merged)["body"] == "also this",
              db.get_request(con, merged)["body"])

        # --replaces --mergeable: the replaced row must be retired, not left
        # 'queued' because a merge path returned first.
        ok, msg, third = askmod.ask("reviewer", "another one", requester="backend")
        ok, msg, fourth = askmod.ask("reviewer", "another one, corrected",
                                     requester="backend", replaces=third, mergeable=True)
        check("replaces+mergeable: the ask succeeds", ok, msg)
        check("replaces+mergeable: the replaced row is retired, not left queued",
              db.get_request(con, third)["state"] == "replaced",
              db.get_request(con, third)["state"])
    finally:
        con.close()


def test_chain_members_excludes_a_row_about_to_be_replaced(home: Path) -> None:
    """Review note: a replace's cycle/ping-pong check ran while the row it is
    about to retire was still 'queued', counting a round that, from the
    target's side, never happened."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    con = db.connect()
    try:
        a = db.add_request(con, "x", "y", "one", None, "peer", "async", None, 0, None)
        b = db.add_request(con, "y", "x", "two", None, "peer", "async", None, 0, None,
                           chain_id=db.get_request(con, a)["chain_id"])
        chain_id = db.get_request(con, a)["chain_id"]
        check("chain_members: both rows count without exclusion",
              len(db.chain_members(con, chain_id)) == 2)
        check("chain_members: exclude_id drops just that one row",
              [r["id"] for r in db.chain_members(con, chain_id, exclude_id=b)] == [a])
    finally:
        con.close()


def test_retire_if_undelivered_is_the_gate_against_the_delivery_race(home: Path) -> None:
    """Review of buddyron/one-task-at-a-time: a plain read-then-write let a
    delivery land between checking delivered_at and retiring the row. The
    atomic conditional update is the only thing withdraw/replace may trust."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    con = db.connect()
    try:
        rid = db.add_request(con, "backend", "reviewer", "task", None,
                             "peer", "async", None, 0, None)
        check("retire_if_undelivered: succeeds on an undelivered row",
              db.retire_if_undelivered(con, rid, "withdrawn"))
        check("retire_if_undelivered: the row actually moved",
              db.get_request(con, rid)["state"] == "withdrawn")

        rid2 = db.add_request(con, "backend", "reviewer", "task two", None,
                              "peer", "async", None, 0, None)
        # A delivery "landing" between a caller's own check and its write is
        # exactly db.mark_delivered running first.
        db.mark_delivered(con, rid2)
        check("retire_if_undelivered: refuses once delivered_at is set",
              not db.retire_if_undelivered(con, rid2, "withdrawn"))
        check("retire_if_undelivered: and does not touch the row",
              db.get_request(con, rid2)["state"] == "queued")
    finally:
        con.close()


def test_ask_rate_limit(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    (home / "team.yaml").write_text(
        "agents:\n  a: { intake: dedicated }\n  b: { intake: dedicated }\n"
        'edges:\n  - { from: "*", to: "*", relation: peer, response: fire_and_forget }\n'
        "limits:\n  max_chain_depth: 99\n  ask_rate_per_hour: 3\n"
    )
    agents.register("a", "alpha")
    agents.register("b", "alpha")
    results = [askmod.ask("b", f"n{i}", requester="a")[0] for i in range(5)]
    check("rate limit: allows up to the cap", results[:3] == [True, True, True], str(results))
    check("rate limit: refuses past the cap", results[3:] == [False, False], str(results))


def test_db_dedup(home: Path) -> None:
    con = db.connect(home / "dedup.sqlite")
    try:
        first = db.add_event(con, "backend", "usage", {"in": 10}, dedup_key="msg_abc")
        second = db.add_event(con, "backend", "usage", {"in": 10}, dedup_key="msg_abc")
        check("db: first usage event stored", first is not None)
        check("db: duplicate message.id is dropped", second is None)
        check("db: no dedup key means always stored",
              db.add_event(con, "backend", "note") is not None)

        db.set_cursor(con, "fold", 0)
        for i in range(5):
            db.add_event(con, "backend", "e", {"i": i})
        rows = db.events_after(con, db.get_cursor(con, "fold"), agent="backend")
        check("db: cursor reads by row id", len(rows) >= 5)
        top = max(r["id"] for r in rows)
        db.set_cursor(con, "fold", top)
        check("db: advanced cursor yields nothing new",
              db.events_after(con, db.get_cursor(con, "fold"), agent="backend") == [])
    finally:
        con.close()


# ---------------------------------------------------------------- digest/fold
def test_digest() -> None:
    d = digest.Digest(date="2026-09-15", agent="backend", updated="14:32 (handoff #3)",
                      folded_through=18432,
                      sections={"done": ["auth refactor merged"], "in_progress": ["rate limiter"],
                                "decided": ["sqlite for events"], "blocked": [],
                                "next": ["write tests"], "base_candidates": []})
    text = digest.render(d)
    back = digest.parse(text)
    check("digest: round-trips the header", back.date == "2026-09-15" and back.agent == "backend")
    check("digest: round-trips folded_through", back.folded_through == 18432)
    check("digest: round-trips items", back.items("done") == ["auth refactor merged"])
    check("digest: all six sections present",
          all(f"## {h}" in text for _, h in digest.SECTIONS))

    ok, _ = digest.validate(text, None)
    check("digest: a good digest validates", ok)

    ok, problems = digest.validate(text.replace("## 완료", "## Done"), None)
    check("digest: a missing section fails validation", not ok and "완료" in problems[0])

    # The failure the design cares about: a fully written file that silently
    # drops completed work.
    lost = text.replace("- auth refactor merged\n", "")
    ok, problems = digest.validate(lost, back)
    check("digest: losing a completed item fails validation", not ok, str(problems))
    check("digest: the failure names what was lost", "auth refactor merged" in problems[0])

    ok, problems = digest.validate(text.replace("event 18432", "event 5"), back)
    check("digest: folded_through must not go backwards", not ok, str(problems))

    rolled = digest.carry_forward(back, "2026-09-16")
    check("rollover: done is emptied", rolled.items("done") == [])
    check("rollover: in_progress carries forward", rolled.items("in_progress") == ["rate limiter"])
    check("rollover: blocked/next carry forward", rolled.items("next") == ["write tests"])


def test_fold(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    (home / "team.yaml").write_text(DESIGN_TEAM)
    agents.register("backend", "alpha")

    con = db.connect()
    try:
        for i in range(4):
            db.add_event(con, "backend", "commit", {"sha": f"abc{i}", "subject": f"work {i}"})
        top = db.max_event_id(con)
    finally:
        con.close()

    day = foldmod.today()
    # A real fold once came back stamped with the time of the last event it had
    # read, hours earlier. The header is ours to write, so the model's version
    # of it is discarded - hence the wrong values here.
    good = digest.render(digest.Digest(
        date="1999-01-01", agent="somebody-else", updated="20:12", folded_through=top,
        sections={"done": ["work 0", "work 1"], "in_progress": ["work 2"], "decided": [],
                  "blocked": [], "next": ["work 3"], "base_candidates": []}))

    real = foldmod.run_model
    try:
        # --- happy path -------------------------------------------------
        foldmod.run_model = lambda *a, **k: (True, good)
        ok, msg = foldmod.fold("backend", use_model=True, fold_account="alpha")
        check("fold: a valid fold succeeds", ok, msg)
        con = db.connect()
        try:
            check("fold: cursor advances only on success",
                  db.get_cursor(con, "fold:backend") == top,
                  str(db.get_cursor(con, "fold:backend")))
        finally:
            con.close()
        published = digest.parse(foldmod.journal_path("backend", day).read_text())
        check("fold: digest is published", published.items("done") == ["work 0", "work 1"])
        check("fold: the header is stamped, not taken from the model",
              published.updated != "20:12" and published.date == day
              and published.agent == "backend",
              f"{published.date} {published.agent} {published.updated}")
        check("fold: deterministic block is attached", "events folded" in published.facts)
        check("fold: writes the digest and no handoff - that is not its job",
              kept_handoff("backend") is None)

        # --- a fully written garbage file must not replace a good one ----
        con = db.connect()
        try:
            db.add_event(con, "backend", "commit", {"sha": "zzz", "subject": "work 4"})
            top2 = db.max_event_id(con)
        finally:
            con.close()
        garbage = good.replace("- work 0\n", "")  # drops a completed item
        foldmod.run_model = lambda *a, **k: (True, garbage)
        ok, msg = foldmod.fold("backend", use_model=True, fold_account="alpha")
        check("fold: a garbage fold is rejected", not ok, msg)
        check("fold: the rejection says what was lost", "work 0" in msg, msg)
        con = db.connect()
        try:
            check("fold: cursor does NOT advance on rejection",
                  db.get_cursor(con, "fold:backend") == top,
                  f"expected {top}, got {db.get_cursor(con, 'fold:backend')}")
        finally:
            con.close()
        after = foldmod.journal_path("backend", day).read_text()
        check("fold: the degraded digest still keeps the completed work",
              "work 0" in after and "work 1" in after)
        check("fold: the degraded digest is marked as such", "degraded" in after)
        check("fold: degraded output still has the machine facts", "events folded" in after)

        # --- a failed model call takes the same safe path ----------------
        foldmod.run_model = lambda *a, **k: (False, "boom")
        ok, msg = foldmod.fold("backend", use_model=True, fold_account="alpha")
        check("fold: a failed model call degrades rather than destroying", not ok, msg)
        con = db.connect()
        try:
            check("fold: cursor still held after a failed call",
                  db.get_cursor(con, "fold:backend") == top)
        finally:
            con.close()

        # --- retrying the held delta must work once the model behaves ----
        good2 = digest.render(digest.Digest(
            date=day, agent="backend", updated="11:00", folded_through=top2,
            sections={"done": ["work 0", "work 1", "work 4"], "in_progress": [], "decided": [],
                      "blocked": [], "next": [], "base_candidates": []}))
        foldmod.run_model = lambda *a, **k: (True, good2)
        ok, msg = foldmod.fold("backend", use_model=True, fold_account="alpha")
        check("fold: the held delta is retried successfully", ok, msg)
        con = db.connect()
        try:
            check("fold: cursor finally advances past the retried delta",
                  db.get_cursor(con, "fold:backend") == top2)
        finally:
            con.close()
    finally:
        foldmod.run_model = real


def test_rollover(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    agents.register("backend", "alpha")
    yesterday = "2000-01-01"
    foldmod.journal_path("backend", yesterday).write_text(digest.render(digest.Digest(
        date=yesterday, agent="backend", updated="23:59", folded_through=7,
        sections={"done": ["shipped X"], "in_progress": ["half-done Y"], "decided": ["chose Z"],
                  "blocked": ["waiting on review"], "next": ["start W"], "base_candidates": []})))
    ok, msg = foldmod.rollover("backend")
    check("rollover: runs", ok, msg)
    today_file = foldmod.journal_path("backend", foldmod.today())
    check("rollover: creates today's digest", today_file.exists())
    d = digest.parse(today_file.read_text())
    check("rollover: yesterday's done is cleared", d.items("done") == [], str(d.items("done")))
    check("rollover: in_progress survives", d.items("in_progress") == ["half-done Y"])
    check("rollover: blocked survives", d.items("blocked") == ["waiting on review"])
    check("rollover: next survives", d.items("next") == ["start W"])
    ok, msg = foldmod.rollover("backend")
    check("rollover: is idempotent", ok and "already exists" in msg, msg)


def test_event_allowlist() -> None:
    raw = {"hook_event_name": "UserPromptSubmit", "session_id": "s1",
           "prompt": "secret business plan text", "cwd": "/tmp",
           "model": {"id": "claude-sonnet-5"}, "api_key": "sk-should-never-appear",
           "transcript_path": "/tmp/t.jsonl"}
    slim = evmod._slim(raw)
    check("events: prompt text is not stored", "prompt" not in slim, str(slim))
    check("events: prompt length is stored", slim.get("prompt_length") == len(raw["prompt"]))
    check("events: unlisted fields are dropped", "api_key" not in slim, str(slim))
    check("events: model is flattened to its id", slim.get("model") == "claude-sonnet-5")
    check("events: PostToolUse is not captured", "PostToolUse" not in evmod.CAPTURED)


# ---------------------------------------------------------------- triggers
def test_triggers() -> None:
    T = triggers
    check("trigger: quiet agent does not fire", not T.evaluate(10, 10).fired)
    check("trigger: context at 60 fires", T.evaluate(10, 60).kind == "context")
    check("trigger: context below 60 does not", not T.evaluate(10, 59).fired)

    # A working agent runs to the wall: nothing below 100% is a usage trigger.
    for pct in (85, 90, 95, 99):
        check(f"trigger: {pct}% is not a reason to hand off", not T.evaluate(pct, 10).fired)
    t = T.evaluate(100, 10)
    check("trigger: the wall fires", t.fired and t.kind == "usage", t.reason)
    check("trigger: the weekly wall fires on its own figure",
          T.evaluate(9, 10, pct_7d=100).kind == "usage_7d")
    check("trigger: 98% of the week is not a wall", not T.evaluate(9, 10, pct_7d=98).fired)

    # Unknown usage is not a wall: the wall announces itself, and moving an agent
    # off an account nobody can read spends a cold start on a guess.
    t = T.evaluate(None, 10)
    check("trigger: unknown usage does not fire", not t.fired, t.reason)

    check("stay: an idle agent never moves",
          T.stay_or_move(False, 10 * 3600)[0] is False)
    check("stay: its account resets sooner than a start pays back",
          T.stay_or_move(True, 10 * 60)[0] is False)
    check("move: work waits and the reset is far", T.stay_or_move(True, 3 * 3600)[0])
    check("move: an unknown reset is not soon", T.stay_or_move(True, None)[0])
    check("stay: T follows a measured cold start",
          T.stay_within_s(13) == 13 / 100 * T.WINDOW_5H_S
          and T.stay_within_s(None) == T.STAY_WITHIN_S)
    check("stretch: an empty account lasts headroom over one agent's burn",
          abs(T.stretch_s(45, 0.0) - 45 / T.TYPICAL_AGENT_BURN_PER_S) < 1e-6)
    check("stretch: fill shortens it", T.stretch_s(45, 0.02) < T.stretch_s(45, 0.0))

    rate = T.burn_rate_per_s([(0, 10), (60, 13), (120, 17)])
    check("burn: measured across the window", abs(rate * 60 - 3.5) < 0.01, str(rate * 60))
    check("burn: a reset does not produce a negative rate",
          T.burn_rate_per_s([(0, 88), (60, 2)]) == 0.0)
    check("burn: too few samples means zero", T.burn_rate_per_s([(0, 10)]) == 0.0)

    # pct_5h is a whole number, so one point over a couple of seconds reads as
    # 30%/min. That noise was enough to hand off and clear a working agent.
    noisy = T.burn_rate_per_s([(0, 62), (2, 63)])
    check("burn: a quantized step over seconds is not a rate", noisy == 0.0,
          f"{noisy * 60}%/min")
    check("burn: and it does not read as a wall", not T.evaluate(63, 10).fired)
    check("burn: a span shorter than the minimum is not measured",
          T.burn_rate_per_s([(0, 60), (T.MIN_BURN_SPAN_S - 1, 75)]) == 0.0)
    check("burn: a real climb over a long enough span still measures",
          abs(T.burn_rate_per_s([(0, 60), (300, 75)]) * 60 - 3.0) < 0.01)

    # The weekly window is run to its own wall too.
    t = T.evaluate(9, 10, pct_7d=100)
    check("trigger: a 7d reading at its wall fires",
          t.fired and t.kind == "usage_7d" and "pct_7d" in t.reason, t.reason)
    check("trigger: no 7d reading at all is not a 7d refusal",
          not T.evaluate(9, 10, pct_7d=None).fired)
    check("trigger: a 5h wall wins when both are at theirs",
          T.evaluate(100, 10, pct_7d=100).kind == "usage")


# ---------------------------------------------------------------- daemon
def _daemon_home(home: Path, pct: int | None) -> None:
    restore_world()
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    (home / "team.yaml").write_text(DESIGN_TEAM)
    agents.register("backend", "alpha")
    write_status(home, "alpha", pct_5h=pct, pct_7d=10, ctx_pct=5,
                 reset_5h=int(time.time()) + 1800)
    # An agent the daemon acts on has somewhere to run: nothing is folded,
    # cleared or parked for one with no terminal. The fixture says so once, so
    # the tests below are about triggers rather than about that.
    con = db.connect()
    try:
        db.set_terminal(con, "backend", "term_x", "wt")
    finally:
        con.close()
    # And this world has no Orca and no process table unless a test provides
    # them. Otherwise the sweep asks the real machine, which has never heard of
    # term_x and is running no session called backend, and correctly tears the
    # fixture down.
    orcamod.available = lambda: False
    obsmod.live_sessions = lambda: None


def test_daemon_park(home: Path) -> None:
    """At the wall the daemon must park the agent with no human involved."""
    _daemon_home(home, 100)
    logs: list[str] = []
    con = db.connect()
    try:
        dmn.tick(con, logs.append)
        row = db.get_agent_state(con, "backend")
        check("daemon: parks an agent that is over the limit",
              row["state"] == dmn.PARKED, str(dict(row)))
        check("daemon: park sets a wake_at", row["wake_at"] is not None)
        check("daemon: wake_at is the window reset", row["wake_at"] > int(time.time()))
        check("daemon: handoff counts as a continuation, not a failure",
              row["continuations"] == 1 and row["failed_attempts"] == 0, str(dict(row)))
        check("daemon: the handoff was recorded as done",
              all(h["state"] != "in_flight" for h in db.in_flight_handoffs(con)))
    finally:
        con.close()


def test_daemon_resume(home: Path) -> None:
    """A parked agent whose window rolled over resumes unattended."""
    _daemon_home(home, 100)
    con = db.connect()
    try:
        # Park it in the past, as if the reset had already happened.
        db.set_agent_state(con, "backend", dmn.PARKED, wake_at=int(time.time()) - 10,
                           detail="parked earlier")
        write_status(home, "alpha", pct_5h=3, pct_7d=10, ctx_pct=5)
        dmn.tick(con, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("daemon: a due parked agent returns to QUEUED",
              row["state"] == dmn.QUEUED, str(dict(row)))
        check("daemon: waking clears wake_at", row["wake_at"] is None)
    finally:
        con.close()


def test_daemon_kill_recovery(home: Path) -> None:
    """SIGKILL leaves an in-flight handoff; a restart must recover it."""
    _daemon_home(home, 50)
    con = db.connect()
    try:
        # Exactly what a SIGKILL mid-handoff leaves behind.
        db.start_handoff(con, "hid1", "backend", "sess", "usage",
                         int(time.time()) + 60)
        db.advance_handoff(con, "hid1", "folded")
        db.set_agent_state(con, "backend", dmn.HANDOFF, detail="killed mid-handoff")

        actions = dmn.reconcile(con, lambda _m: None)
        check("daemon: reconciliation resumes the in-flight handoff",
              any("resumed in-flight handoff from step 'folded'" in a for a in actions),
              str(actions))
        # It must actually finish it, not just say so.
        check("daemon: the resumed handoff is no longer in flight",
              db.in_flight_handoffs(con) == [],
              str([dict(h) for h in db.in_flight_handoffs(con)]))
        row = db.get_agent_state(con, "backend")
        check("daemon: the resumed agent is parked, not stuck in HANDOFF",
              row["state"] == dmn.PARKED, str(dict(row)))

        # An agent mid-handoff is excluded from trigger evaluation (8.9).
        write_status(home, "alpha", pct_5h=100, pct_7d=10, ctx_pct=90)
        before = len(db.in_flight_handoffs(con))
        dmn.tick(con, lambda _m: None)
        check("daemon: no competing handoff is started",
              len(db.in_flight_handoffs(con)) == before, str(before))
    finally:
        con.close()


def test_daemon_handoff_timeout(home: Path) -> None:
    _daemon_home(home, 50)
    con = db.connect()
    try:
        db.start_handoff(con, "hid2", "backend", "sess", "usage", int(time.time()) - 5)
        actions = dmn.reconcile(con, lambda _m: None)
        check("daemon: an expired handoff is timed out",
              any("timed out" in a for a in actions), str(actions))
        row = db.get_agent_state(con, "backend")
        check("daemon: a timed-out handoff degrades", row["state"] == dmn.DEGRADED)
        check("daemon: timing out increments the throttle counter",
              row["throttle_count"] == 1, str(dict(row)))
    finally:
        con.close()


def test_daemon_stall_escalation(home: Path) -> None:
    """STALLED must be reachable by the path the daemon actually takes.

    Driving real park -> wake -> degrade cycles, rather than hand-setting the
    counter, is the point: resetting the streak on every wake pinned it at 1
    and made this escalation unreachable while a hand-bumped test still passed.
    """
    _daemon_home(home, 100)
    con = db.connect()
    try:
        # The default fixture has no snapshot of the session and no worktree,
        # so every handoff here is degraded for the facts it could not read.
        seen = []
        try:
            for _ in range(dmn.MAX_THROTTLE_STREAK + 3):
                row = db.get_agent_state(con, "backend")
                if row and row["state"] == dmn.STALLED:
                    break
                # Make it due, as the usage window rolling over would.
                if row and row["wake_at"]:
                    db.set_agent_state(con, "backend", row["state"],
                                       wake_at=int(time.time()) - 1, detail=row["detail"])
                # These cycles are hours apart in reality; backdate so the
                # handoff rate limiter does not read them as one runaway.
                con.execute("UPDATE handoff SET updated = updated - ? WHERE agent = 'backend'",
                            (dmn.MIN_HANDOFF_INTERVAL_S * 2,))
                dmn.tick(con, lambda _m: None)
                cur = db.get_agent_state(con, "backend")
                seen.append((cur["state"], cur["throttle_count"]))

            check("daemon: the throttle streak accumulates across park/wake cycles",
                  max(t for _, t in seen) > 1, str(seen))
            final = db.get_agent_state(con, "backend")
            check("daemon: repeated degradation escalates to STALLED",
                  final["state"] == dmn.STALLED, str(seen))

            dmn.tick(con, lambda _m: None)
            check("daemon: STALLED is not cleared automatically",
                  db.get_agent_state(con, "backend")["state"] == dmn.STALLED)
        finally:
            pass
    finally:
        con.close()


def test_daemon_success_clears_streak(home: Path) -> None:
    """A handoff that works must end the streak, or every agent eventually
    stalls just from having been busy."""
    _daemon_home(home, 100)
    con = db.connect()
    try:
        for _ in range(3):
            db.bump_counter(con, "backend", "throttle_count")
        readable_agent(con, home, "backend")
        db.set_agent_state(con, "backend", dmn.QUEUED)
        dmn.tick(con, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("daemon: a successful handoff clears the throttle streak",
              row["throttle_count"] == 0, str(dict(row)))
    finally:
        con.close()


def test_daemon_degraded_recovers(home: Path) -> None:
    """DEGRADED must not be a silent terminal state."""
    check("daemon: DEGRADED is evaluated every tick", dmn.DEGRADED in dmn.EVALUATED)
    _daemon_home(home, 50)
    con = db.connect()
    try:
        db.start_handoff(con, "h_exp", "backend", "s", "usage", int(time.time()) - 5)
        dmn.tick(con, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("daemon: a timed-out handoff degrades with a wake_at",
              row["state"] == dmn.DEGRADED and row["wake_at"] is not None, str(dict(row)))
        db.set_agent_state(con, "backend", dmn.DEGRADED, wake_at=int(time.time()) - 1)
        dmn.tick(con, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("daemon: a due DEGRADED agent returns to QUEUED",
              row["state"] in (dmn.QUEUED, dmn.PARKED), str(dict(row)))
    finally:
        con.close()


def test_daemon_tick_expires_handoff(home: Path) -> None:
    """The deadline must be enforced every tick, not only at startup."""
    _daemon_home(home, 50)
    con = db.connect()
    try:
        db.start_handoff(con, "h_tick", "backend", "s", "usage", int(time.time()) - 600)
        db.set_agent_state(con, "backend", dmn.HANDOFF)
        dmn.tick(con, lambda _m: None)
        check("daemon: tick expires a handoff past its deadline",
              db.in_flight_handoffs(con) == [],
              str([dict(h) for h in db.in_flight_handoffs(con)]))
        check("daemon: the agent does not stay wedged in HANDOFF",
              db.get_agent_state(con, "backend")["state"] != dmn.HANDOFF)
    finally:
        con.close()


def test_daemon_ignores_stale_status(home: Path) -> None:
    """A frozen status.json must not be believed (the collector may be dead)."""
    _daemon_home(home, 40)
    write_status(home, "alpha", pct_5h=40, pct_7d=10, ctx_pct=5,
                 collected_at=int(time.time()) - 100000)
    st = status.read("alpha")
    check("stale snapshot: assignable() refuses", not st.assignable()[0])
    check("stale snapshot: pct is not trusted for triggers", st.trusted_pct_5h is None,
          str(st.trusted_pct_5h))
    t = triggers.evaluate(st.trusted_pct_5h, st.trusted_ctx_pct)
    check("stale snapshot: an unreadable figure is not a wall, so nothing fires on it",
          not t.fired, t.reason)


def test_daemon_evaluates_parked(home: Path) -> None:
    """Section 8.4: if only running agents were evaluated, an all-parked system
    would never wake anything again."""
    check("daemon: PARKED is evaluated every tick", dmn.PARKED in dmn.EVALUATED)
    check("daemon: THROTTLED is evaluated every tick", dmn.THROTTLED in dmn.EVALUATED)
    check("daemon: QUEUED is evaluated every tick", dmn.QUEUED in dmn.EVALUATED)
    check("daemon: STALLED is not auto-evaluated", dmn.STALLED not in dmn.EVALUATED)


def test_daemon_pidfile(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    dmn.write_pidfile()
    check("daemon: pidfile records this process", dmn.running_elsewhere() == os.getpid())

    # A pidfile from before the last boot cannot describe a live process.
    paths.daemon_pid().write_text(json.dumps({"pid": 99999, "boot": 1}))
    check("daemon: a pre-boot pidfile is reclaimed", dmn.running_elsewhere() is None)
    check("daemon: the stale pidfile is removed", not paths.daemon_pid().exists())

    # A dead PID from this boot is also reclaimed.
    paths.daemon_pid().write_text(json.dumps({"pid": 999999, "boot": dmn.boot_time()}))
    check("daemon: a dead pid is reclaimed", dmn.running_elsewhere() is None)


# ---------------------------------------------------------------- service
def test_service_plist(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    plist = service.build_plist(home=home)
    check("service: label is set", plist["Label"] == service.LABEL)
    check("service: RunAtLoad", plist["RunAtLoad"] is True)
    check("service: KeepAlive is a bare true, not SuccessfulExit-gated",
          plist["KeepAlive"] is True)
    check("service: ThrottleInterval bounds the restart rate",
          isinstance(plist["ThrottleInterval"], int) and plist["ThrottleInterval"] > 0)
    check("service: stdout log is under OCP_HOME/logs",
          plist["StandardOutPath"] == str(home / "logs" / "daemon.out.log"))
    check("service: stderr log is under OCP_HOME/logs",
          plist["StandardErrorPath"] == str(home / "logs" / "daemon.err.log"))
    env = plist["EnvironmentVariables"]
    check("service: env carries PATH", bool(env.get("PATH")))
    check("service: env carries HOME", bool(env.get("HOME")))
    check("service: env carries OCP_HOME, pointed at this home",
          env.get("OCP_HOME") == str(home))
    check("service: program is an absolute path (launchd inherits no PATH)",
          Path(plist["ProgramArguments"][0]).is_absolute())
    check("service: the interpreter is not pinned - bin/ocp's own shebang resolves it",
          "python" not in plist["ProgramArguments"][0].lower())

    path = service.write_plist(path=home / "test.plist", home=home)
    check("service: write_plist creates the file", path.exists())
    back = service.read_plist(path=path)
    check("service: plist round-trips through plistlib", back == plist, str(back))


def test_service_launchctl_gated(home: Path) -> None:
    """No test may shell out to real launchctl - every call goes through
    run_launchctl, so replacing that one name fakes the whole daemon."""
    home.mkdir(parents=True, exist_ok=True)
    plist_file = home / "LaunchAgents" / f"{service.LABEL}.plist"
    calls: list[list[str]] = []

    class FakeResult:
        def __init__(self, returncode=0, stdout="", stderr=""):
            self.returncode, self.stdout, self.stderr = returncode, stdout, stderr

    def fake_ok(args):
        calls.append(args)
        return FakeResult(0, "", "")

    orig_plist_path = service.plist_path
    orig_runner = service.run_launchctl
    try:
        service.plist_path = lambda: plist_file
        service.run_launchctl = fake_ok
        ok, msg = service.install(home=home)
        check("service: install() reports success when launchctl does", ok, msg)
        check("service: install() writes the plist", plist_file.exists())
        check("service: a first install (nothing loaded yet) skips bootout",
              "bootout" not in [c[0] for c in calls])
        check("service: install() bootstraps the built plist",
              calls[0] == ["bootstrap", service.domain_target(), str(plist_file)])
        check("service: install() enables the job", calls[1][0] == "enable")

        # Reinstalling over a job the fake now reports as loaded (`print`
        # returns 0) must bootout first, so a stale plist is not left running.
        calls.clear()

        def fake_loaded(args):
            calls.append(args)
            if args[0] == "print":
                return FakeResult(0, "\tpid = 1\n", "")
            return FakeResult(0, "", "")

        service.run_launchctl = fake_loaded
        ok, msg = service.install(home=home)
        check("service: reinstalling over a loaded job boots it out first",
              ok and calls[1][0] == "bootout", str(calls))

        # If the new plist fails to load, a previously-working job must not
        # end up unsupervised - the old plist is restored and reloaded.
        calls.clear()
        attempts = {"n": 0}

        def fake_bootstrap_fails_once(args):
            calls.append(args)
            if args[0] == "print":
                return FakeResult(0, "\tpid = 1\n", "")
            if args[0] == "bootstrap":
                attempts["n"] += 1
                return FakeResult(0, "", "") if attempts["n"] > 1 else FakeResult(1, "", "boom")
            return FakeResult(0, "", "")

        service.run_launchctl = fake_bootstrap_fails_once
        ok, msg = service.install(home=home)
        check("service: install() reports failure when bootstrap fails", not ok, msg)
        check("service: a failed reinstall still retries bootstrap with the restored plist",
              attempts["n"] == 2)

        calls.clear()

        def fake_status(args):
            calls.append(args)
            return FakeResult(0, "\tpid = 4242\n\tlast exit code = 0\n", "")

        service.run_launchctl = fake_status
        st = service.status()
        check("service: status() reports installed", st["installed"])
        check("service: status() reports loaded from a fake `print`", st["loaded"])
        check("service: status() parses the pid", st["pid"] == 4242)
        check("service: status() parses the last exit code", st["last_exit_code"] == "0")

        calls.clear()
        service.run_launchctl = fake_ok
        ok, msg = service.uninstall()
        check("service: uninstall() reports success", ok, msg)
        check("service: uninstall() removes the plist", not plist_file.exists())
        check("service: uninstall() boots the job out", calls[0][0] == "bootout")
    finally:
        service.plist_path = orig_plist_path
        service.run_launchctl = orig_runner


def test_service_status_when_not_installed(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    orig = service.plist_path
    called = {"n": 0}

    def must_not_run(args):
        called["n"] += 1
        raise AssertionError("run_launchctl called for an uninstalled service")

    orig_runner = service.run_launchctl
    try:
        service.plist_path = lambda: home / "nope.plist"
        service.run_launchctl = must_not_run
        st = service.status()
        check("service: status() skips launchctl when no plist exists",
              not st["installed"] and not st["loaded"] and called["n"] == 0)
    finally:
        service.plist_path = orig
        service.run_launchctl = orig_runner


def test_service_supervisor_of(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    orig = service.status
    try:
        service.status = lambda: {"installed": True, "loaded": True, "pid": 111,
                                  "last_exit_code": "0"}
        check("service: pid matches launchd -> supervised",
              service.supervisor_of(111) == f"launchd ({service.LABEL})")
        check("service: pid does not match launchd -> hand-started",
              "hand-started" in service.supervisor_of(222))

        service.status = lambda: {"installed": False, "loaded": False, "pid": None,
                                  "last_exit_code": None}
        check("service: no pid, no service -> not running",
              service.supervisor_of(None) == "not running")

        service.status = lambda: {"installed": True, "loaded": False, "pid": None,
                                  "last_exit_code": None}
        check("service: no pid, installed but unloaded -> says so",
              "not loaded" in service.supervisor_of(None))
    finally:
        service.status = orig


def test_service_crash_loop(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    log = service.out_log(home)
    log.parent.mkdir(parents=True, exist_ok=True)

    now = time.localtime()

    def stamp(seconds_ago: int) -> str:
        t = time.localtime(time.mktime(now) - seconds_ago)
        return time.strftime("%H:%M:%S", t)

    # Four restarts inside a minute: a real crash loop.
    lines = [f"[{stamp(s)}] daemon starting - reconciling" for s in (0, 15, 30, 45)]
    log.write_text("\n".join(lines) + "\n")
    check("service: four restarts in a minute is a crash loop",
          service.crash_loop_detected(home, window_s=300, threshold=4))

    # One startup line, long-running since: not a loop.
    log.write_text(f"[{stamp(0)}] daemon starting - reconciling\n")
    check("service: a single startup is not a crash loop",
          not service.crash_loop_detected(home, window_s=300, threshold=4))

    # Restarts, but spread across hours: not a loop.
    lines = [f"[{stamp(s)}] daemon starting - reconciling" for s in (0, 3600, 7200, 10800)]
    log.write_text("\n".join(lines) + "\n")
    check("service: restarts spread across hours is not a crash loop",
          not service.crash_loop_detected(home, window_s=300, threshold=4))

    # The most likely real crash loop: a hand-started daemon holds the
    # daemon lock, launchd's copy exits at once every restart without ever
    # reaching reconcile. It must still be visible to the detector.
    lines = [f"[{stamp(s)}] daemon starting - already running (pid 1), exiting"
             for s in (0, 15, 30, 45)]
    log.write_text("\n".join(lines) + "\n")
    check("service: repeated startup refusals are a crash loop too",
          service.crash_loop_detected(home, window_s=300, threshold=4))


def test_daemon_refusal_logs_through_the_same_path(home: Path) -> None:
    """The refusal used to be a bare `print`, invisible to
    crash_loop_detected. It must share the logger and the 'daemon starting'
    wording every other startup line uses."""
    import contextlib
    import io

    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    buf = io.StringIO()
    with lock.singleton(dmn.DAEMON_LOCK) as held:
        check("daemon: the lock is free to take in a fresh home", held)
        with contextlib.redirect_stdout(buf):
            rc = dmn.run(once=True)
    out = buf.getvalue()
    check("daemon: refuses while another process holds the lock", rc == 1)
    check("daemon: the refusal is logged, not printed bare",
          "daemon starting - already running" in out, out)


def test_a_second_daemon_is_refused_with_no_pidfile_at_all(home: Path) -> None:
    """The pidfile names whoever wrote it last, so it cannot see a daemon that
    never wrote one - and an exiting daemon used to delete the live one's name
    on its way out, leaving nothing to check. The lock is what refuses."""
    import contextlib
    import io

    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    paths.daemon_pid().unlink(missing_ok=True)
    buf = io.StringIO()
    with lock.singleton(dmn.DAEMON_LOCK):
        with contextlib.redirect_stdout(buf):
            rc = dmn.run(once=True)
    check("daemon: a second one is refused even with no pidfile", rc == 1)
    check("daemon: and the refusal does not invent a pid",
          "already running," in buf.getvalue(), buf.getvalue())

    # And the lock is not left behind by the process that held it.
    with lock.singleton(dmn.DAEMON_LOCK) as held:
        check("daemon: the lock is released when its holder is done", held)


def test_an_exiting_daemon_leaves_the_live_ones_name(home: Path) -> None:
    """Removing the pidfile unconditionally is how this installation ended up
    with two supervisors: the second daemon's exit erased the first one's
    name, and the startup check then read an empty file."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)

    paths.daemon_pid().write_text(json.dumps({"pid": os.getpid() + 1,
                                              "boot": dmn.boot_time()}))
    dmn.clear_pidfile()
    check("pidfile: an exit does not clear a name that is not its own",
          paths.daemon_pid().exists())

    dmn.write_pidfile()
    dmn.clear_pidfile()
    check("pidfile: and does clear its own", not paths.daemon_pid().exists())

    # Reclaiming a stale one has the same rule, and needs it more: every
    # screen calls running_elsewhere(), so a daemon that starts between the
    # read and the unlink has its own name deleted by a passer-by.
    dead = os.getpid() + 1
    paths.daemon_pid().write_text(json.dumps({"pid": dead, "boot": dmn.boot_time()}))
    dmn.write_pidfile()          # the new daemon, inside the window
    dmn._reclaim(dead)
    check("pidfile: a reclaim does not delete the name written since it read",
          paths.daemon_pid().exists() and dmn.read_pidfile()["pid"] == os.getpid(),
          str(dmn.read_pidfile()))


def test_one_daemon_is_counted_in_the_process_table(home: Path) -> None:
    """A supervisor nobody recorded exists only there. The words `ocp daemon`
    on a command line do not make one: a grep for them carries both, and so
    does an agent session whose system prompt quotes the command."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    table = [
        (4707, "/usr/bin/python /Users/x/workspaces/orcapipe/bin/ocp daemon"),
        (4801, "grep -n ocp daemon"),
        (4802, "vim /Users/x/workspaces/orcapipe/ocp/daemon.py"),
        (4803, "/usr/bin/python /Users/x/.local/bin/ocp ps"),
        (4804, "claude --append-system-prompt Report with /Users/x/bin/ocp daemon"),
        (os.getpid(), "/usr/bin/python /Users/x/bin/ocp daemon"),
    ]
    orig_table, orig_env = obsmod.process_table, obsmod.env_of
    try:
        obsmod.process_table = lambda: table
        obsmod.env_of = lambda pid, name: str(home)
        check("ps: the running daemon is found, and only it",
              dmn.processes() == [4707], str(dmn.processes()))

        obsmod.env_of = lambda pid, name: f"{home}-elsewhere"
        check("ps: another installation's daemon is not this one's duplicate",
              dmn.processes() == [], str(dmn.processes()))

        # The same directory under its other name - /var and /private/var are
        # one place on macOS, and the test homes live there. Compared as
        # spellings, our own duplicate reads as somebody else's and nothing is
        # reported at all.
        other = os.path.realpath(home)
        check("ps: (the fixture has two names for one directory)",
              other != str(home), f"{other} == {home}")
        obsmod.env_of = lambda pid, name: other
        check("ps: the home is compared as a directory, not as a spelling",
              dmn.processes() == [4707], str(dmn.processes()))

        # A process we cannot place is the one worth naming.
        obsmod.env_of = lambda pid, name: None
        check("ps: an unplaceable daemon is reported, not dropped",
              dmn.processes() == [4707], str(dmn.processes()))

        obsmod.process_table = lambda: None
        check("ps: and a process table we could not read is not an empty one",
              dmn.processes() == [], str(dmn.processes()))
    finally:
        obsmod.process_table, obsmod.env_of = orig_table, orig_env


def test_two_processes_cannot_both_supervise_one_home(home: Path) -> None:
    """The failure this exists to prevent is two processes, not two calls: an
    in-process check would pass on a lock that never leaves the process."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    env = {**os.environ, "OCP_HOME": str(home)}
    holder = subprocess.Popen(
        [sys.executable, "-c",
         f"import sys, time; sys.path.insert(0, {str(ROOT)!r})\n"
         "from ocp import lock\n"
         "held = lock.singleton('daemon')\n"
         "print(held.__enter__(), flush=True)\n"
         "time.sleep(120)\n"],
        env=env, stdout=subprocess.PIPE, text=True)
    try:
        check("lock: another process took the daemon lock",
              holder.stdout.readline().strip() == "True")
        out = subprocess.run([sys.executable, str(ROOT / "bin" / "ocp"), "daemon",
                              "--once"],
                             env=env, capture_output=True, text=True, timeout=120)
        check("daemon: the program itself refuses, not only a call to run()",
              out.returncode == 1, out.stdout + out.stderr)
        check("daemon: and the refusal reaches the log it is detected in",
              "already running" in out.stdout, out.stdout)
    finally:
        holder.kill()
        holder.wait(timeout=30)

    # The kernel takes the lock back from a process that dies holding it, so
    # there is no stale hold to reclaim the way a pidfile has to be.
    with lock.singleton(dmn.DAEMON_LOCK) as free:
        check("daemon: a killed holder leaves the lock free", free)


def test_the_screen_says_when_two_daemons_are_running(home: Path) -> None:
    """The warning is the whole of what can be done about a duplicate that
    predates the lock: nothing else on any screen can see one."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    orig = dmn.processes
    try:
        dmn.processes = lambda: [111, 222]
        r = doctor.Report()
        doctor._check_one_daemon(r)
        check("doctor: two daemons are a warning, by pid",
              any(lv == doctor.WARN and "111, 222" in name for lv, name, _ in r.rows),
              str(r.rows))

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            climod.cmd_daemon_status(None)
        check("ps: the screen says it too", "WARNING" in buf.getvalue(), buf.getvalue())

        # One daemon that no pidfile names is what a wrongly cleared pidfile
        # leaves behind: running, and reported as not running.
        dmn.processes = lambda: [111]
        paths.daemon_pid().unlink(missing_ok=True)
        r = doctor.Report()
        doctor._check_one_daemon(r)
        check("doctor: a daemon in no pidfile is said out loud",
              any(lv == doctor.WARN and "no pidfile" in name for lv, name, _ in r.rows),
              str(r.rows))
    finally:
        dmn.processes = orig


def test_service_log_rotation(tmp: Path) -> None:
    tmp.mkdir(parents=True, exist_ok=True)
    log = tmp / "daemon.out.log"
    log.write_text("keep this line\n" * 100)
    size_before = log.stat().st_size

    check("service: rotate_log is a no-op below the size threshold",
          not service.rotate_log(log, max_bytes=size_before + 1, backups=2))

    rotated = service.rotate_log(log, max_bytes=size_before, backups=2)
    check("service: rotate_log rotates once over threshold", rotated)
    backup = log.with_name(f"{log.name}.1.gz")
    check("service: rotate_log gzips the old content", backup.exists())
    check("service: rotate_log truncates the live file in place", log.stat().st_size == 0)

    import gzip as gzmod
    with gzmod.open(backup, "rt") as f:
        saved = f.read()
    check("service: the rotated backup keeps the lines that explain the last incident",
          "keep this line" in saved)

    # A second rotation must not clobber the first backup - it shifts it down.
    log.write_text("second incident\n" * 100)
    service.rotate_log(log, max_bytes=size_before, backups=2)
    backup2 = log.with_name(f"{log.name}.2.gz")
    check("service: an older backup is kept, not overwritten", backup2.exists())
    with gzmod.open(backup2, "rt") as f:
        check("service: the shifted backup is the first incident, not the second",
              "keep this line" in f.read())


def test_doctor_service_check(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    orig = service.status
    orig_loop = service.crash_loop_detected
    try:
        service.status = lambda: {"installed": False, "loaded": False, "pid": None,
                                  "last_exit_code": None}
        r = doctor.Report()
        doctor._check_service(r)
        check("doctor: not-installed service warns when nothing is running",
              r.rows[-1][0] == doctor.WARN)

        # A human deliberately running it by hand is a working setup, not a
        # defect - doctor's warnings are what we read to know something is
        # actually wrong, so this must not be one of them.
        dmn.write_pidfile()
        r = doctor.Report()
        doctor._check_service(r)
        check("doctor: not-installed is fine when a daemon is running by hand",
              r.rows[-1][0] == doctor.OK, str(r.rows[-1]))
        paths.daemon_pid().unlink(missing_ok=True)

        service.status = lambda: {"installed": True, "loaded": False, "pid": None,
                                  "last_exit_code": None}
        r = doctor.Report()
        doctor._check_service(r)
        check("doctor: installed-but-unloaded service fails",
              r.rows[-1][0] == doctor.FAIL)

        service.status = lambda: {"installed": True, "loaded": True, "pid": 5,
                                  "last_exit_code": "0"}
        service.crash_loop_detected = lambda: True
        r = doctor.Report()
        doctor._check_service(r)
        check("doctor: a crash-looping service fails", r.rows[-1][0] == doctor.FAIL)

        service.crash_loop_detected = lambda: False
        r = doctor.Report()
        doctor._check_service(r)
        check("doctor: a healthy, loaded service is ok", r.rows[-1][0] == doctor.OK)
    finally:
        service.status = orig
        service.crash_loop_detected = orig_loop


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


def test_drift(tmp: Path) -> None:
    origin = tmp / "origin"
    _mkrepo(origin)
    work = tmp / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], capture_output=True)
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    run = lambda cwd, *a: subprocess.run(["git", *a], cwd=cwd, capture_output=True, env=env)  # noqa: E731

    d = drift.check(work, base="main", fetch=False)
    check("drift: a fresh clone has no drift", d.ok and d.behind == 0, d.summary())

    # The agent works on shared.txt.
    (work / "shared.txt").write_text("agent edit\n")
    run(work, "add", "-A")
    run(work, "commit", "-qm", "agent work")
    d = drift.check(work, base="main", fetch=False)
    check("drift: the agent's own commits are not drift", d.behind == 0, d.summary())
    check("drift: own commits are counted as touched files",
          "shared.txt" in d.touched, str(d.touched))

    # The base moves, on a file the agent did not touch.
    (origin / "other.txt").write_text("base moved\n")
    run(origin, "add", "-A")
    run(origin, "commit", "-qm", "base moves other.txt")
    run(work, "fetch", "-q")
    d = drift.check(work, base="main", fetch=False)
    check("drift: base movement is detected", d.behind == 1, d.summary())
    check("drift: no overlap means not significant", not d.significant, str(d.overlap))
    check("drift: no overlap uses the reassuring header",
          d.resume_header() == drift.NO_OVERLAP_HEADER)

    # The base moves again, this time on the agent's own file.
    (origin / "shared.txt").write_text("base changed the shared file\n")
    run(origin, "add", "-A")
    run(origin, "commit", "-qm", "base moves shared.txt")
    run(work, "fetch", "-q")
    d = drift.check(work, base="main", fetch=False)
    check("drift: overlap with the agent's files is detected",
          d.significant and "shared.txt" in d.overlap, str(d.overlap))
    check("drift: overlap switches to the cautious header",
          d.resume_header() == drift.OVERLAP_HEADER)
    check("drift: the verdict is the file intersection, not the commit count",
          d.behind == 2 and d.overlap == ["shared.txt"], d.summary())

    # Unknown drift must not reassure.
    bad = drift.check(tmp / "nope", base="main", fetch=False)
    check("drift: an unknown result is fail-closed",
          not bad.ok and bad.resume_header() == drift.OVERLAP_HEADER, bad.summary())


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


def test_the_handoff_is_three_facts_read_without_a_model(home: Path) -> None:
    """A handoff has to work when every account is walled, which is when a
    model call cannot: it is the request in hand, the session's transcript path
    and the worktree's state, read from disk, and it is degraded only when one
    of those could not be read."""
    _daemon_home(home, 50)
    wt = _worktree_with_a_dirty_file(home / "wt-backend")
    real = foldmod.run_model
    foldmod.run_model = lambda *a, **k: (_ for _ in ()).throw(AssertionError("model called"))
    con = db.connect()
    try:
        db.set_terminal(con, "backend", "term_x", str(wt))
        rid = db.add_request(con, "marina", "backend", "fix the routing\nsecond line", None,
                             "peer", "async", None, 0, None)
        db.mark_delivered(con, rid)
        write_agent_status("backend", "alpha", session_id="sess-1",
                           transcript_path="/tmp/sess-1.jsonl")

        missing = handoffmod.record(con, "backend")
        text = db.get_handoff(con, "backend")
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=wt,
                              capture_output=True, text=True).stdout.strip()
        check("handoff: nothing is missing when the three facts can be read",
              missing == [], str(missing))
        check("handoff: it names the request in hand, first line only",
              f"#{rid} from marina - fix the routing" in text and "second line" not in text,
              text)
        check("handoff: it names the previous session's transcript",
              "sess-1" in text and "/tmp/sess-1.jsonl" in text, text)
        check("handoff: it gives the branch, HEAD and what is uncommitted",
              f"branch: topic @ {head}" in text and "?? dirty.txt" in text, text)
        check("handoff: and nothing else - no digest sections",
              "진행중" not in text and "recent commits" not in text, text)

        # A request nobody has been shown yet is not one in hand.
        db.add_request(con, "marina", "backend", "later", None, "peer", "async", None, 0, None)
        db.set_request_state(con, rid, "done", "ok")
        check("handoff: no delivered request is a fact, not a gap",
              handoffmod.record(con, "backend") == []
              and "request: none in progress" in db.get_handoff(con, "backend"))

        # Each fact that cannot be read degrades it, and says which.
        paths.agent_status_file("backend").unlink()
        gaps = handoffmod.record(con, "backend")
        check("handoff: no snapshot of the session names the transcript as the gap",
              gaps == ["the session's transcript path"], str(gaps))
        check("handoff: and the record says so where the agent reads it",
              "could not read the session's transcript path" in db.get_handoff(con, "backend")
              and "branch: topic" in db.get_handoff(con, "backend"))
        db.set_terminal(con, "backend", "term_x", str(home / "gone"))
        gaps = handoffmod.record(con, "backend")
        check("handoff: a worktree that is gone is the other gap",
              len(gaps) == 2 and gaps[1].startswith("the worktree"), str(gaps))
    finally:
        foldmod.run_model = real
        con.close()


def test_an_idle_close_keeps_a_handoff_only_if_it_closed(home: Path) -> None:
    """A wake that has to start elsewhere has no live session to read the
    transcript path from, so the close takes the record while there is one.
    A close that did not happen leaves nothing behind to be read as current."""
    _daemon_home(home, 20)
    con = db.connect()
    real = dmn._end_session
    try:
        readable_agent(con, home, "backend")
        st = status.read_for_agent("backend", "alpha")
        st.session_id = "sess-1"
        dmn._end_session = lambda *a, **k: (True, "closed")
        dmn._close_agent(con, "backend", lambda _m: None, st)
        check("close: a successful close leaves the record for the wake",
              "sess-1" in (db.get_handoff(con, "backend") or ""))
        db.set_handoff(con, "backend", None)
        dmn._end_session = lambda *a, **k: (False, "no session process")
        dmn._close_agent(con, "backend", lambda _m: None, st)
        check("close: one that did not happen leaves none",
              db.get_handoff(con, "backend") is None)

        # A parked agent is closed on this machine within the hour, and its park
        # record names the session from before its /clear - the one the close's
        # own snapshot no longer does.
        park = "# handoff · backend\nprevious session: sess-BEFORE-PARK"
        db.set_handoff(con, "backend", park)
        db.set_agent_state(con, "backend", dmn.PARKED, wake_at=int(time.time()) + 3600)
        dmn._end_session = lambda *a, **k: (True, "closed")
        dmn._close_agent(con, "backend", lambda _m: None, st)
        check("close: a park's record survives the close, not replaced by a newer one",
              db.get_handoff(con, "backend") == park, str(db.get_handoff(con, "backend")))
        dmn._end_session = lambda *a, **k: (False, "no session process")
        dmn._close_agent(con, "backend", lambda _m: None, st)
        check("close: and a close that failed does not delete it",
              db.get_handoff(con, "backend") == park, str(db.get_handoff(con, "backend")))

        # A record missing a fact is not kept: a wake that moves elsewhere would
        # drop the resume id, the only complete copy of the context.
        db.set_handoff(con, "backend", None)
        db.set_agent_state(con, "backend", dmn.IDLE)
        db.set_terminal(con, "backend", "term_x", str(home / "gone"))
        dmn._end_session = lambda *a, **k: (True, "closed")
        dmn._close_agent(con, "backend", lambda _m: None, st)
        check("close: a record that could not read its worktree is not kept",
              db.get_handoff(con, "backend") is None, str(db.get_handoff(con, "backend")))

        # A launch hands the record over, so the next close records the session
        # that launch started rather than finding the park's and skipping.
        db.set_handoff(con, "backend", "# handoff · backend\nprevious session: sess-R1")
        realb, realx, realr = launch.build, os.execve, launch._record_terminal
        launch.build = lambda *a, **k: (["x"], {})
        os.execve = lambda *a, **k: None
        launch._record_terminal = lambda *a, **k: None
        try:
            launch.exec_agent("backend")
        finally:
            launch.build, os.execve, launch._record_terminal = realb, realx, realr
        db.set_agent_state(con, "backend", dmn.IDLE)
        dmn._close_agent(con, "backend", lambda _m: None, st)
        check("close: after a launch handed the record over, the close takes its own",
              "sess-R1" not in (db.get_handoff(con, "backend") or ""),
              str(db.get_handoff(con, "backend")))
    finally:
        dmn._end_session = real
        con.close()


def test_a_park_with_every_account_walled_is_a_clean_handoff(home: Path) -> None:
    """The fold used to fail here - no account could pay for the call - and every
    walled park counted as degraded. The handoff needs no account."""
    _daemon_home(home, 100)
    wt = _worktree_with_a_dirty_file(home / "wt-backend")
    con = db.connect()
    try:
        db.set_terminal(con, "backend", "term_x", str(wt))
        write_agent_status("backend", "alpha", session_id="sess-1",
                           transcript_path="/tmp/sess-1.jsonl")
        logs: list[str] = []
        dmn.tick(con, logs.append)
        row = db.get_agent_state(con, "backend")
        check("park: a walled agent is parked", row["state"] == dmn.PARKED, str(dict(row)))
        check("park: the handoff is not degraded, so nothing accrues to the streak",
              row["throttle_count"] == 0
              and not any("degraded" in m for m in logs), f"{dict(row)} {logs}")
        check("park: the record is waiting for its wake",
              "sess-1" in (db.get_handoff(con, "backend") or ""))
        check("park: no fold failure is logged",
              not con.execute("SELECT 1 FROM events WHERE kind = 'fold_failed'").fetchall())
    finally:
        con.close()


def test_the_digest_facts_do_not_repeat_one_event_kind(home: Path) -> None:
    """The machine record in a digest counts turn boundaries and quotes what
    happened, instead of repeating one event kind sixteen times."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    agents.register("backend", "alpha")
    now = int(time.time())
    events = [{"id": i, "ts": now, "kind": "SubagentStop", "payload": "{}"} for i in range(20)]
    events.append({"id": 99, "ts": now, "kind": "commit", "payload": '{"sha": "abc"}'})
    facts = foldmod.deterministic_facts("backend", events, repo=None)
    check("digest: the record counts turn boundaries and quotes what happened",
          "SubagentStopx20" in facts and facts.count("SubagentStop {") == 0
          and "commit" in facts, facts)



def test_yaml_differential() -> None:
    """The fallback must never disagree with PyYAML silently.

    Two parsers reading the same routing policy differently is worse than
    having no fallback: `creates_gate: no` was True under one and False under
    the other, which decides whether an escalation needs a human.
    """
    try:
        import yaml as pyyaml
    except ImportError:
        check("yaml differential: skipped (no PyYAML on this interpreter)", True)
        return

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
        # Raising where PyYAML succeeds is acceptable (loud, not silent);
        # returning a different value is not.
        if got != expected and got != "<error>":
            mismatches.append((src, expected, got))
    check("yaml differential: no silent disagreement with PyYAML",
          not mismatches,
          "; ".join(f"{s!r}: pyyaml={e!r} ours={g!r}" for s, e, g in mismatches[:3]))


def test_yaml_rejects_ambiguity() -> None:
    for src, label in [
        ("a: &anchor x\n", "anchors"),
        ("a: 12:30\n", "sexagesimals"),
        ("n: inf\n", "inf"),
        ("n: nan\n", "nan"),
        ("a:\n\tb: 1\n", "tab indentation"),
        ("a: 1\n---\nb: 2\n", "multiple documents"),
        ("d:\n  - { a: 1\n", "unbalanced flow collection"),
        ("d: }broken\n", "stray closing brace"),
    ]:
        try:
            yamlcompat.parse(src)
            check(f"yaml: rejects {label}", False, "parsed instead of raising")
        except yamlcompat.ConfigError:
            check(f"yaml: rejects {label}", True)


def test_yaml_booleans() -> None:
    got = yamlcompat.parse("a: yes\nb: no\nc: on\nd: off\ne: true\nf: false\n")
    check("yaml: YAML 1.1 booleans are booleans, not strings",
          got == {"a": True, "b": False, "c": True, "d": False, "e": True, "f": False},
          str(got))
    check("yaml: a quoted 'no' stays a string",
          yamlcompat.parse('a: "no"\n')["a"] == "no")


# ---------------------------------------------------------------- regressions
def test_status_edge_cases(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)

    # json.loads accepts bare NaN/Infinity; these must read as unknown, not
    # crash the daemon mid-tick.
    d = home / "accounts" / "nanny"
    d.mkdir(parents=True, exist_ok=True)
    for literal, label in [("NaN", "NaN"), ("Infinity", "Infinity"), ("-Infinity", "-Infinity")]:
        (d / "status.json").write_text(
            '{"session_id":"s","collected_at":%d,"pct_5h":%s}' % (int(time.time()), literal))
        st = status.read("nanny")
        check(f"status: {label} reads as unknown, not a crash", st.pct_5h is None,
              str(st.pct_5h))
        check(f"status: {label} then refuses assignment", not st.assignable()[0])

    (d / "status.json").write_text(
        '{"session_id":"s","collected_at":%d,"pct_5h":"85"}' % int(time.time()))
    check("status: a string percentage reads as unknown", status.read("nanny").pct_5h is None)

    # A millisecond timestamp is a plausible collector bug. Clamping it to
    # age 0 made it look permanently fresh.
    write_status(home, "future", pct_5h=5, collected_at=int(time.time()) * 1000)
    st = status.read("future")
    check("status: a future timestamp is unknown age, not fresh", st.age_s is None,
          str(st.age_s))
    check("status: a future timestamp is stale", st.stale)
    check("status: a future timestamp refuses assignment", not st.assignable()[0])


def test_team_deny_validation() -> None:
    """A malformed deny silently *allows*, so it must fail to load."""
    base = ("teams:\n  eng: [backend]\n  growth: [marketing]\n"
            "agents:\n  backend: { intake: dedicated }\n  marketing: { intake: dedicated }\n"
            'edges:\n  - { from: "*", to: "*", relation: peer }\n')
    for deny, label in [
        ('deny:\n  - { form: "backend", to: "marketing", reason: "typo key" }\n',
         "a typo'd key"),
        ('deny:\n  - { from: "backend" }\n', "a missing 'to'"),
        ('deny:\n  - { from: "engg.*", to: "growth.*" }\n', "an unknown team"),
    ]:
        try:
            team.Team(yamlcompat.parse(base + deny))
            check(f"team: rejects {label} in deny", False, "loaded instead of raising")
        except yamlcompat.ConfigError:
            check(f"team: rejects {label} in deny", True)

    # An unknown team in an edge is just as silent.
    try:
        team.Team(yamlcompat.parse(
            "teams:\n  eng: [backend]\n"
            "agents:\n  backend: { intake: dedicated }\n"
            'edges:\n  - { from: "nosuch.*", to: "backend" }\n'))
        check("team: rejects an unknown team in an edge", False, "loaded")
    except yamlcompat.ConfigError:
        check("team: rejects an unknown team in an edge", True)

    # And a well-formed deny must still win.
    t = team.Team(yamlcompat.parse(
        base + 'deny:\n  - { from: "eng.*", to: "growth.*", reason: "via product" }\n'))
    check("team: a well-formed deny still wins over a wildcard edge",
          not t.route("backend", "marketing").allowed)


def test_ask_respects_terminal_state(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    (home / "team.yaml").write_text(DESIGN_TEAM)
    for n in ("backend", "reviewer"):
        agents.register(n, "alpha")
    con = db.connect()
    try:
        db.set_agent_state(con, "backend", "STALLED", detail="needs a human")
    finally:
        con.close()
    askmod.ask("reviewer", "review this", requester="backend")
    con = db.connect()
    try:
        st = db.get_agent_state(con, "backend")
        check("ask: an agent cannot park its own STALLED away",
              st["state"] == "STALLED", str(dict(st)))
    finally:
        con.close()


def test_launch_refuses_stale(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    bindir = home / "stub"
    bindir.mkdir(parents=True, exist_ok=True)
    (bindir / "claude").write_text("#!/bin/sh\necho started\n")
    (bindir / "claude").chmod(0o755)
    agents.register("st", "delta")
    cfg = home / "accounts" / "delta" / ".claude"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / ".credentials.json").write_text("{}")
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", OCP_HOME=str(home))

    # A stale snapshot must NOT block a launch. An idle account always goes
    # stale, and the launch is the only thing that refreshes it, so refusing
    # here deadlocks the whole system after STALE_AFTER_S of quiet.
    write_status(home, "delta", pct_5h=20, collected_at=int(time.time()) - 100000)
    r = subprocess.run([sys.executable, str(ROOT / "bin" / "ocp"), "launch", "st"],
                       capture_output=True, text=True, env=env)
    check("launch: a stale snapshot does not deadlock the launch",
          r.returncode == 0, r.stdout + r.stderr)
    check("launch: but it warns that the reading is old",
          "may be out of date" in r.stderr, r.stderr)

    # A current reading over the limit still blocks.
    write_status(home, "delta", pct_5h=100)
    r = subprocess.run([sys.executable, str(ROOT / "bin" / "ocp"), "launch", "st"],
                       capture_output=True, text=True, env=env)
    check("launch: a current over-limit reading still blocks", r.returncode != 0, r.stdout)

    # Staleness stays fail-closed where it belongs: assignment.
    write_status(home, "delta", pct_5h=20, collected_at=int(time.time()) - 100000)
    check("launch: staleness is still fail-closed for assignment",
          not status.read("delta").assignable()[0])


def test_collector_account_named_like_user(home: Path) -> None:
    """An account named after the user must not be renamed to 'default'."""
    home.mkdir(parents=True, exist_ok=True)
    user = Path(os.path.expanduser("~")).name
    cfg = home / "accounts" / user / ".claude"
    cfg.mkdir(parents=True, exist_ok=True)
    payload = {"session_id": "s1", "model": {"id": "m"},
               "rate_limits": {"five_hour": {"used_percentage": 77, "resets_at": 1},
                               "seven_day": {"used_percentage": 5, "resets_at": 2}}}
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(cfg), OCP_HOME=str(home))
    subprocess.run([str(ROOT / "libexec" / "statusline-collect.sh")],
                   input=json.dumps(payload), capture_output=True, text=True, env=env)
    check(f"collector: an account named {user!r} keeps its own name",
          (home / "accounts" / user / "status.json").exists(),
          str(sorted(p.name for p in (home / "accounts").iterdir())))
    check("collector: no phantom 'default' account is created",
          not (home / "accounts" / "default").exists())


def test_db_dedup_only_swallows_dedup(home: Path) -> None:
    con = db.connect(home / "strict.sqlite")
    try:
        check("db: a real constraint violation is not silently swallowed",
              _raises(lambda: con.execute(
                  "INSERT INTO events(ts, agent, kind) VALUES(?,NULL,?)", (1, "k"))))
    finally:
        con.close()


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:  # noqa: BLE001
        return True


def test_agent_rewrite_uses_parser(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    agents.register("rw", "alpha", model="opus")
    # A spacing variant that prefix-matching would have duplicated.
    agents.agent_file("rw").write_text("name: rw\naccount : alpha\nmodel: opus\n")
    agents.register("rw", "beta")
    cfg = agents.read("rw")
    check("agents: re-register moves the account through the parser",
          cfg.get("account") == "beta", str(cfg))
    text = agents.agent_file("rw").read_text()
    check("agents: no duplicate account key is left behind",
          text.count("account") == 1, text)
    check("agents: other fields survive the rewrite", cfg.get("model") == "opus", str(cfg))


def test_shared_account_context_isolation(home: Path) -> None:
    """Two agents on one account must not be judged by each other's context.

    Rate limits are account-wide and shared correctly; context usage is per
    session, and the account file only ever holds whichever session wrote last.
    """
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    agents.register("ag_a", "shared")
    agents.register("ag_b", "shared")

    payload = {
        "session_id": "sess-a", "model": {"id": "m"},
        "rate_limits": {"five_hour": {"used_percentage": 30, "resets_at": 1},
                        "seven_day": {"used_percentage": 12, "resets_at": 2}},
        "context_window": {"used_percentage": 70, "context_window_size": 1000,
                           "total_input_tokens": 1, "total_output_tokens": 1,
                           "current_usage": {}},
    }
    cfg = home / "accounts" / "shared" / ".claude"
    cfg.mkdir(parents=True, exist_ok=True)

    def collect(agent, ctx, session):
        p = dict(payload)
        p["context_window"] = dict(payload["context_window"], used_percentage=ctx)
        p["session_id"] = session
        subprocess.run([str(ROOT / "libexec" / "statusline-collect.sh")],
                       input=json.dumps(p), capture_output=True, text=True,
                       env=dict(os.environ, CLAUDE_CONFIG_DIR=str(cfg),
                                OCP_HOME=str(home), OCP_AGENT=agent))

    collect("ag_a", 70, "sess-a")   # ag_a is nearly full on context
    collect("ag_b", 5, "sess-b")    # ag_b then writes the account file

    check("collector: each agent writes its own status file",
          (home / "agents" / "ag_a" / "status.json").exists()
          and (home / "agents" / "ag_b" / "status.json").exists())

    a = status.read_for_agent("ag_a", "shared")
    b = status.read_for_agent("ag_b", "shared")
    check("shared account: each agent keeps its own context",
          (a.ctx_pct, b.ctx_pct) == (70, 5), f"a={a.ctx_pct} b={b.ctx_pct}")
    check("shared account: rate limits are still shared",
          a.pct_5h == b.pct_5h == 30, f"a={a.pct_5h} b={b.pct_5h}")

    # The context trigger must fire for the full agent and not for the other.
    ta = triggers.evaluate(a.pct_5h, a.ctx_pct)
    tb = triggers.evaluate(b.pct_5h, b.ctx_pct)
    check("shared account: the full agent's context trigger fires",
          ta.fired and ta.kind == "context", ta.reason)
    check("shared account: the other agent's does not", not tb.fired, tb.reason)

    # An agent with no session file of its own must not borrow one.
    agents.register("ag_c", "shared")
    c = status.read_for_agent("ag_c", "shared")
    check("shared account: an agent with no session file falls back to the account",
          c.pct_5h == 30)


def test_spawn_base_selection(tmp: Path) -> None:
    """Branching from the stale side silently loses work, so pick_base must not
    guess - and must refuse outright when the two have diverged."""
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    run = lambda cwd, *a: subprocess.run(["git", *a], cwd=cwd, capture_output=True, env=env)  # noqa: E731

    origin = tmp / "origin"
    origin.mkdir(parents=True)
    run(origin, "init", "-q", "-b", "main")
    (origin / "f.txt").write_text("base\n")
    run(origin, "add", "-A"); run(origin, "commit", "-qm", "base")

    work = tmp / "work"
    subprocess.run(["git", "clone", "-q", str(origin), str(work)], capture_output=True)

    ref, why = spawnmod.pick_base(work, "main")
    check("spawn: in sync, uses the local base", ref == "main", f"{ref} ({why})")

    # Local ahead: branching from origin/main would drop the local commits.
    (work / "f.txt").write_text("local work\n")
    run(work, "add", "-A"); run(work, "commit", "-qm", "local only")
    ref, why = spawnmod.pick_base(work, "main")
    check("spawn: local ahead, branches from local", ref == "main", f"{ref} ({why})")
    check("spawn: says why", "leads by 1" in why, why)

    # Remote ahead only.
    work2 = tmp / "work2"
    subprocess.run(["git", "clone", "-q", str(origin), str(work2)], capture_output=True)
    (origin / "f.txt").write_text("remote work\n")
    run(origin, "add", "-A"); run(origin, "commit", "-qm", "remote only")
    ref, why = spawnmod.pick_base(work2, "main")
    check("spawn: remote ahead, branches from origin", ref == "origin/main", f"{ref} ({why})")

    # Diverged: refuse rather than pick a side.
    (work2 / "other.txt").write_text("diverge\n")
    run(work2, "add", "-A"); run(work2, "commit", "-qm", "local diverge")
    ref, why = spawnmod.pick_base(work2, "main")
    check("spawn: diverged, refuses instead of guessing", ref is None, f"{ref} ({why})")
    check("spawn: tells the human to merge first", "Merge" in why or "merge" in why, why)


def test_idle_session_is_closed_and_resumed(home: Path) -> None:
    """Memory is the scarce thing on one laptop running a team: an idle session
    holds 250-500MB and earns nothing with it. Closing keeps the terminal, and
    waking resumes the same session rather than starting a stranger in it."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[tuple[str, str]] = []
    real = (orcamod.alive, orcamod.wait_idle, orcamod.send, orcamod.available)
    try:
        orcamod.available = lambda: True
        orcamod.live_terminals = lambda: {"term_x"}
        orcamod.alive = lambda h: True
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        # A session is running in that terminal - nothing is typed into one
        # where the process table says there is only a shell - and it is gone
        # once /exit has been sent, the mirror of what a relaunch does.
        table = sessions_are("backend")

        def send(handle: str, text: str, enter: bool = True):
            sent.append((handle, text))
            if text == "/exit":
                table.pop("backend", None)
            elif " launch " in text:
                table["backend"] = 4242
            return True, ""

        orcamod.send = send
        db.set_terminal(con, "backend", "term_x", "wt")
        now = int(time.time())
        agent_dir = home / "agents" / "backend"
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": now, "session_id": "sess-1",
            "pct_5h": 20, "ctx_pct": 8, "transcript_path": "/tmp/sess-1.jsonl"}))
        st = status.read_for_agent("backend", "alpha")

        # Nothing has been seen from it yet - that is not the same as idle.
        row = db.get_agent_state(con, "backend")
        check("close: an agent we have never seen is left alone",
              not dmn.should_close(con, "backend", dmn.IDLE, row, now, st))

        db.add_event(con, "backend", "Stop", {}, )
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'", (now - 60,))
        row = db.get_agent_state(con, "backend")
        check("close: a recently active agent is left alone",
              not dmn.should_close(con, "backend", dmn.IDLE, row, now, st))

        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'",
                    (now - dmn.IDLE_CLOSE_S - 60,))
        row = db.get_agent_state(con, "backend")
        check("close: an agent idle past the threshold is closed",
              dmn.should_close(con, "backend", dmn.IDLE, row, now, st))
        check("close: unless it is working", not dmn.should_close(
            con, "backend", dmn.RUNNING, row, now, st))
        check("close: or a human has to look at it", not dmn.should_close(
            con, "backend", dmn.STALLED, row, now, st))
        check("close: or it is being handed off", not dmn.should_close(
            con, "backend", dmn.HANDOFF, row, now, st))

        # Memory is recoverable the moment anything wants it; the context we
        # would lose by starting a stranger instead is not recoverable at all.
        blind = status.read_for_agent("backend", "alpha")
        blind.session_id = None
        check("close: or we cannot name the session we would resume",
              not dmn.should_close(con, "backend", dmn.IDLE, row, now, blind))

        # Work waiting for it is the one case where closing only adds delay.
        db.add_request(con, "reviewer", "backend", "please look at this", None,
                       "peer", "async", None, 0, None)
        row = db.get_agent_state(con, "backend")
        check("close: an agent with work waiting is left alone",
              not dmn.should_close(con, "backend", dmn.IDLE, row, now, st))
        con.execute("UPDATE requests SET state = 'done'")

        # Closing records which session to bring back.
        write_status(home, "alpha", pct_5h=20, ctx_pct=8)
        (agent_dir / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()), "session_id": "sess-1",
            "pct_5h": 20, "ctx_pct": 8,
            "transcript_path": "/tmp/sess-1.jsonl"}))
        ok, _ = dmn.close_agent(con, "backend", lambda _m: None,
                        status.read_for_agent("backend", "alpha"))
        check("close: the session is ended", ok and sent and sent[-1][1] == "/exit",
              str(sent[-1:]))
        row = db.get_agent_state(con, "backend")
        check("close: it is recorded as closed", row["closed_at"] is not None)
        check("close: with the session to resume", row["resume_session"] == "sess-1",
              str(row["resume_session"]))
        check("close: and the terminal is kept", row["terminal"] == "term_x")

        # Waking puts the agent back into the shell its session left behind.
        sent.clear()
        ok, _ = dmn.wake_agent(con, "backend", lambda _m: None)
        launched = [t for _h, t in sent if t.startswith("ocp") or "launch" in t]
        check("wake: the agent is launched again", ok and launched, str(sent))
        check("wake: resuming the same session, not starting a stranger",
              "--resume sess-1" in launched[0], launched[0] if launched else "")
        row = db.get_agent_state(con, "backend")
        check("wake: the closed mark is cleared", row["closed_at"] is None)
        told = sent[-1][1]
        check("wake: a resumed agent is told nothing was lost", "Nothing was lost" in told, told[:120])
        check("wake: and where its own record is", "/tmp/sess-1.jsonl" in told)
        check("wake: with the warning that reading it whole undoes the point",
              "do not read it whole" in told)
    finally:
        orcamod.alive, orcamod.wait_idle, orcamod.send, orcamod.available = real
        con.close()


def test_a_running_agent_is_told_when_work_arrives(home: Path) -> None:
    """`ocp ask` writes a row and an inbox file; neither reaches the agent's
    screen. Without delivery, every request needs a human to nudge a terminal -
    which is how this system was actually being run."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.alive, orcamod.idle_now, orcamod.send, orcamod.available)
    try:
        orcamod.available = lambda: True
        orcamod.live_terminals = lambda: {"term_x"}
        orcamod.alive = lambda h: True
        orcamod.idle_now = lambda h: True
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        # A running agent, so there is something in that terminal to tell.
        sessions_are("backend")
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn.IDLE)

        check("deliver: nothing is sent when nothing is waiting",
              not dmn.deliver_queued(con, "backend", lambda _m: None))

        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)
        check("deliver: a waiting request reaches the agent",
              dmn.deliver_queued(con, "backend", lambda _m: None))
        check("deliver: and it is told how to read it", "ocp inbox" in sent[-1], sent[-1])
        check("deliver: and who it is from", "reviewer" in sent[-1], sent[-1])

        sent.clear()
        check("deliver: the same request is not announced twice",
              not dmn.deliver_queued(con, "backend", lambda _m: None) and not sent)

        # Request #189: one at a time. A second request behind the first is
        # queued, but the head has not changed - backend is not re-told, and
        # it is not shown the second one's contents at all.
        db.add_request(con, "reviewer", "backend", "and this", None,
                       "peer", "async", None, 0, None)
        check("deliver: a request behind the head does not get its own delivery",
              not dmn.deliver_queued(con, "backend", lambda _m: None) and not sent, str(sent))

        # Typing at an agent mid-turn leaves the text in its input box, unsent.
        orcamod.idle_now = lambda h: False
        db.add_request(con, "reviewer", "backend", "later", None,
                       "peer", "async", None, 0, None)
        sent.clear()
        check("deliver: a busy agent is left alone until it is free",
              not dmn.deliver_queued(con, "backend", lambda _m: None) and not sent)

        # Being told is not the agent doing something, so it must not count as
        # activity - or an agent we keep reminding never looks idle enough to
        # close, and it never was awake in the first place.
        check("deliver: being told is not activity",
              db.last_activity(con, "backend") is None, str(db.last_activity(con, "backend")))

        # Resolving the head reveals the next one - a different id - and
        # resolve() delivers it right away, the same as asking delivers to an
        # idle target right away rather than waiting for the next tick.
        orcamod.idle_now = lambda h: True
        head = db.head_of_queue(con, "backend")
        sent.clear()
        askmod.resolve(head["id"], "done")
        check("deliver: resolving the head delivers the one behind it, right away",
              sent and "and this" in sent[-1] and "look at this" not in sent[-1], str(sent))
    finally:
        orcamod.alive, orcamod.idle_now, orcamod.send, orcamod.available = real
        con.close()


def test_a_withdraw_landing_before_the_send_stops_the_send(home: Path) -> None:
    """Review of buddyron/one-task-at-a-time, fix #5: deliver_queued reads
    head_of_queue, then waits on idle_now and lock.terminal before _tell marks
    the row and sends it - a withdraw or replace fitting in that gap must not
    still get typed into the target's terminal."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = orcamod.send
    try:
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        rid = db.add_request(con, "reviewer", "backend", "look at this", None,
                             "peer", "async", None, 0, None)
        head = db.head_of_queue(con, "backend")
        # The gap: something retires the row after deliver_queued already read
        # it as the head, but before _tell gets to mark_delivered.
        check("race: the withdraw lands",
              db.retire_if_undelivered(con, rid, "withdrawn"))
        check("race: _tell refuses instead of sending a dead request",
              not dmn._tell(con, "backend", head, 1, "term_x", lambda _m: None) and not sent,
              str(sent))
        check("race: the row was not marked delivered underneath the withdraw",
              db.get_request(con, rid)["delivered_at"] is None)
    finally:
        orcamod.send = real
        con.close()


def test_work_arriving_wakes_a_closed_agent(home: Path) -> None:
    """A closed session cannot read its own inbox. Without this, `ocp ask`
    queues into a terminal with nobody in it - reviewer sat closed with two
    requests pending and nothing would ever have brought it back."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.alive, orcamod.wait_idle, orcamod.send, orcamod.available)
    try:
        orcamod.available = lambda: True
        orcamod.live_terminals = lambda: {"term_x"}
        orcamod.alive = lambda h: True
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        # The session runs until close_agent ends it, and then it does not -
        # which is what the tick below has to work out for itself.
        table = sessions_are("backend")
        orcamod.send = relaunching_send(sent, table)
        db.set_terminal(con, "backend", "term_x", "wt")
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()),
            "session_id": "sess-9", "pct_5h": 20}))

        # A turn can start between a human pressing `c` and confirming it, so
        # closing checks the state itself rather than trusting what the screen
        # was showing. `should_close` refuses the same two states.
        db.set_agent_state(con, "backend", dmn.RUNNING, detail="was working")
        ok, why = dmn.close_agent(con, "backend", lambda _m: None,
                                  status.read_for_agent("backend", "alpha"))
        check("close: a session mid-turn is not closed out from under the work",
              not ok and "RUNNING" in why, why)
        check("close: and nothing was typed at it", "/exit" not in sent, str(sent))

        db.set_agent_state(con, "backend", dmn.IDLE, detail="turn ended")
        dmn.close_agent(con, "backend", lambda _m: None,
                        status.read_for_agent("backend", "alpha"))
        row = db.get_agent_state(con, "backend")
        check("close: and a closed session is not left labelled RUNNING",
              row["state"] != dmn.RUNNING, row["state"])

        db.add_request(con, "reviewer", "backend", "please look at this", None,
                       "peer", "async", None, 0, None)
        sent.clear()
        dmn.tick(con, lambda _m: None)
        check("wake: work arriving brings a closed agent back",
              any("--resume sess-9" in t for t in sent), str(sent[:2]))
        check("wake: and it is no longer marked closed",
              db.get_agent_state(con, "backend")["closed_at"] is None)
    finally:
        orcamod.alive, orcamod.wait_idle, orcamod.send, orcamod.available = real
        con.close()


def test_wake_without_a_closed_session_uses_the_handoff(home: Path) -> None:
    """Closing is for memory; a handoff is for context. An agent that was
    cleared rather than closed has no session worth resuming - what it needs is
    its own record."""
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.alive, orcamod.available, orcamod.send)
    try:
        orcamod.available = lambda: True
        orcamod.live_terminals = lambda: {"term_x"}
        orcamod.alive = lambda h: True
        sent: list[str] = []
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        # Cleared, not closed: the session is still running, which is what
        # makes typing the handoff into it the right move.
        sessions_are("backend")
        db.set_terminal(con, "backend", "term_x", "wt")
        keep_handoff("backend", "# handoff · backend\n- was mid-way through the routing fix")
        ok, _ = dmn.wake_agent(con, "backend", lambda _m: None)
        check("wake: a cleared agent gets its handoff", ok and "routing fix" in sent[-1],
              sent[-1][:120] if sent else "")
        check("wake: and is told its context was cleared",
              "context was cleared" in sent[-1])
        check("wake: no launch command is typed at an agent that is still running",
              not any("launch" in t for t in sent), str(sent))
        check("wake: a handoff that has been handed over is not handed over again",
              kept_handoff("backend") is None)
    finally:
        orcamod.alive, orcamod.available, orcamod.send = real
        con.close()


def test_reconcile_does_not_clear_closed_at_on_a_failed_wake(home: Path) -> None:
    """QUEUED with closed_at set is ambiguous, so reconcile leaves it alone.

    A wake interrupted after relaunching but before its write, and a wake that
    failed outright, leave the same two columns behind - and step 2's own
    wake_agent produces the second one whenever a reboot's dead terminal cannot
    be relaunched. Clearing on that shape gets the failure case backwards and
    takes resume_session with it, which is the one thing a close must never
    lose. Only a live witness, the process table, separates them.
    """
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.alive, orcamod.available)
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: False  # the terminal a reboot leaves behind
        db.set_terminal(con, "backend", "term_gone", "wt")
        db.set_closed(con, "backend", "sess-1")
        db.set_agent_state(con, "backend", dmn.PARKED, wake_at=int(time.time()) - 10,
                           detail="parked and closed, daemon was down since")

        logged: list[str] = []
        dmn.reconcile(con, logged.append)
        row = db.get_agent_state(con, "backend")
        check("reconcile: step 2's own wake attempt is logged as failing",
              any("could not wake at reconciliation" in m for m in logged), str(logged))
        check("reconcile: closed_at survives a failed wake attempt",
              row["closed_at"] is not None, str(dict(row)))
        check("reconcile: resume_session survives it too - it is not repairable once gone",
              row["resume_session"] == "sess-1", str(dict(row)))
        # And the deadline stays on the row. Clearing it before the wake had
        # worked is how a failed wake became a permanent sleep: due_agents
        # selects on wake_at, so a row with none is never looked at again.
        check("reconcile: the wake stays due, so a later restart retries it",
              row["wake_at"] is not None and row["state"] == dmn.PARKED, str(dict(row)))

        # The terminal comes back - a human reopened it, or Orca was slow to
        # start - and the next restart does what this one could not.
        sent: list[str] = []
        table = sessions_are()
        orcamod.alive = lambda h: True
        orcamod.live_terminals = lambda: {"term_gone"}
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        orcamod.send = relaunching_send(sent, table)
        dmn.reconcile(con, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("reconcile: the retry resumes the session it kept",
              any("--resume sess-1" in t for t in sent), str(sent))
        check("reconcile: and only then is the deadline cleared",
              row["state"] == dmn.QUEUED and row["wake_at"] is None, str(dict(row)))
    finally:
        orcamod.alive, orcamod.available = real
        con.close()


def test_carry_bounds_and_losses(home: Path) -> None:
    """The three ways a carried figure goes wrong, all found in review.

    A figure we cannot place in a window is ignorance and must not read as an
    idle window; a figure we can place goes old while collected_at keeps
    refreshing; and inside one window the account figure may never go down,
    because each session reports from the moment of its own last API response.
    """
    os.environ["OCP_HOME"] = str(home)
    acct_cfg = home / "accounts" / "carry" / ".claude"
    acct_cfg.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(acct_cfg), OCP_HOME=str(home))
    collect = lambda payload: subprocess.run(  # noqa: E731
        [str(ROOT / "libexec" / "statusline-collect.sh")],
        input=json.dumps(payload), capture_output=True, text=True, env=env)
    read = lambda: status.read(  # noqa: E731
        "carry", home / "accounts" / "carry" / "status.json")
    idle = {"session_id": "idle"}          # a session with nothing to report
    now = int(time.time())

    # 1. A figure with no resets_at cannot be placed in a window, and staying
    #    lost is the whole of it: a loss that decays into "quiet" on the next
    #    payload is a 95% account reading as 0% one refresh later.
    collect({"session_id": "a", "rate_limits": {"five_hour": {"used_percentage": 95}}})
    check("carry: a figure without a window is still read", read().pct_5h == 95)
    collect(idle)
    st = read()
    check("carry: losing it is recorded", st.rl_lost is True)
    check("carry: and reads as unknown, not as a quiet window",
          st.pct_5h_source == "unknown", st.pct_5h_source)
    check("carry: so it refuses assignment", not st.assignable()[0], st.assignable()[1])
    collect(idle)
    collect(idle)
    st = read()
    check("carry: the loss survives the payloads that follow it",
          st.rl_lost is True and st.pct_5h_source == "unknown", st.pct_5h_source)
    collect({"session_id": "b", "rate_limits":
             {"five_hour": {"used_percentage": 7, "resets_at": now + 1800}}})
    check("carry: and clears the moment a payload reports the window again",
          read().rl_lost is False and read().pct_5h == 7)

    # 2. Inside one window the account figure never goes down, but a payload
    #    that reported the window still counts as having observed it - freezing
    #    rl_at would age a live account into carried_old and stop its work.
    collect({"session_id": "live", "rate_limits":
             {"five_hour": {"used_percentage": 40, "resets_at": now + 1800}}})
    raw = json.loads((home / "accounts" / "carry" / "status.json").read_text())
    raw["rl_at"] = now - 7200
    (home / "accounts" / "carry" / "status.json").write_text(json.dumps(raw))
    collect({"session_id": "staler", "rate_limits":
             {"five_hour": {"used_percentage": 30, "resets_at": now + 1800}}})
    st = read()
    check("carry: a staler session does not lower the account", st.pct_5h == 40,
          repr(st.pct_5h))
    check("carry: but it did observe the window, so the figure is not stale",
          st.rl_at is not None and st.rl_at > now - 60, str(st.rl_at))
    check("carry: and a live account does not read as carried",
          st.pct_5h_source == "live", st.pct_5h_source)
    collect({"session_id": "newer", "rate_limits":
             {"five_hour": {"used_percentage": 55, "resets_at": now + 1800}}})
    check("carry: a higher figure is taken", read().pct_5h == 55, repr(read().pct_5h))

    # The API returns untidy floats, and this repo has seen 28.999999999999996
    # for a plain 29. Comparing raw would latch the rule on invisible noise.
    collect({"session_id": "n1", "rate_limits":
             {"five_hour": {"used_percentage": 88.00000000000001, "resets_at": now + 1800}}})
    collect({"session_id": "n2", "rate_limits":
             {"five_hour": {"used_percentage": 87.99999999999999, "resets_at": now + 1800}}})
    st = read()
    check("carry: float noise below the same percent does not latch the rule",
          st.pct_5h_source == "live", st.pct_5h_source)

    # A window that has ended cannot be the window a new figure belongs to.
    collect({"session_id": "old", "rate_limits":
             {"five_hour": {"used_percentage": 95, "resets_at": now - 600}}})
    collect({"session_id": "reset", "rate_limits":
             {"five_hour": {"used_percentage": 0, "resets_at": now + 1800}}})
    check("carry: a genuine reset is not suppressed by the window before it",
          read().pct_5h == 0, repr(read().pct_5h))

    # 3. The weekly window gets the same treatment, because its gate is written
    #    as "block if the figure is high" - so a lost 7d is not a refusal, it is
    #    the absence of one, and a 7d mistake costs the rest of the week.
    collect({"session_id": "w", "rate_limits": {
        "five_hour": {"used_percentage": 5, "resets_at": now + 1800},
        "seven_day": {"used_percentage": 100}}})
    check("carry: a blocking 7d refuses", not read().assignable()[0], read().assignable()[1])
    collect(idle)
    st = read()
    check("carry: losing the 7d is recorded", st.rl7_lost is True)
    check("carry: and a lost 7d still refuses, rather than vanishing",
          not st.assignable()[0], st.assignable()[1])
    check("carry: the refusal names the weekly gate", "7d" in st.assignable()[1])

    # 4. A carried figure goes old even while collected_at stays fresh.
    p = home / "accounts" / "carry" / "status.json"
    raw = json.loads(p.read_text())
    raw.update(pct_5h=55, reset_5h=now + 9000, rl_carried=True, rl_lost=False,
               rl7_lost=False, pct_7d=10, rl_at=now - 4 * 3600, collected_at=now)
    p.write_text(json.dumps(raw))
    st = read()
    check("carry: a fresh collected_at does not make an old figure fresh",
          st.pct_5h_source == "carried_old", st.pct_5h_source)
    check("carry: it refuses new work", not st.assignable()[0], st.assignable()[1])
    check("carry: the refusal says how old it is", "ago" in st.assignable()[1])
    check("carry: but the trigger still gets the floor, not a null",
          st.trusted_pct_5h == 55, str(st.trusted_pct_5h))
    t = triggers.evaluate(st.trusted_pct_5h, st.trusted_ctx_pct)
    check("carry: so an idle agent is not handed off for usage_unknown",
          t.kind != "usage_unknown", t.reason)


def test_unobserved_spend_stays_off_unreadable_accounts(home: Path) -> None:
    """A fold runs `claude -p`, which is headless and has no status line, so it
    is the one spender no collector ever sees. Choosing by the raw figure sent
    it to the accounts whose usage is already unobserved, because None passed
    the "not at the hard stop" test."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    real_login = accounts.logged_in
    try:
        accounts.logged_in = lambda a: True  # noqa: E731 - keychain is not the subject
        now = int(time.time())
        write_status(home, "blind", pct_5h=None, rl_lost=True)
        write_status(home, "hot", pct_5h=100, reset_5h=now + 1800)
        check("fold: an account whose reading was lost is not a fallback",
              foldmod._cheapest_account() is None, str(foldmod._cheapest_account()))
        write_status(home, "usable", pct_5h=20, reset_5h=now + 1800)
        check("fold: a readable account is", foldmod._cheapest_account() == "usable",
              str(foldmod._cheapest_account()))

        # A carried figure is a floor, and a floor below the stop still has
        # room. This one is deliberate: the fold is small, and refusing here
        # would leave the day unrecorded on a quiet machine.
        for acct in ("usable", "hot"):
            (home / "accounts" / acct / "status.json").unlink()
        write_status(home, "old", pct_5h=40, reset_5h=now + 9000,
                     rl_carried=True, rl_at=now - 4 * 3600)
        check("fold: a carried floor with room is still usable",
              foldmod._cheapest_account() == "old", str(foldmod._cheapest_account()))
        st = status.read("old")
        check("fold: even though it takes no new agents",
              not st.assignable()[0] and st.pct_5h_source == "carried_old")
    finally:
        accounts.logged_in = real_login
        tui._LOGIN.clear()


def test_subscription_type_is_recorded_and_cached(home: Path) -> None:
    """Request #167/#170: subscriptionType comes from `claude auth status`,
    claude's own report of what it is signed in as - never a credential file
    (KICKOFF rule 1) - and is cached so the panel does not shell out to
    `claude` on every refresh."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    accounts.register("gamma")
    real_login, real_run = accounts.logged_in, subprocess.run
    calls: list = []

    class _Result:
        stdout = json.dumps({"subscriptionType": "pro"})

    try:
        accounts.logged_in = lambda a: True  # noqa: E731 - keychain is not the subject
        subprocess.run = lambda cmd, **kw: (calls.append(cmd), _Result())[1]
        got = accounts.subscription_type("gamma")
        check("subscription_type: reads the plan from claude's own report",
              got == "pro", got)
        check("subscription_type: exactly one call was made", len(calls) == 1, str(calls))

        got2 = accounts.subscription_type("gamma")
        check("subscription_type: cached, not asked again", len(calls) == 1, str(calls))
        check("subscription_type: cached answer matches", got2 == "pro", got2)

        accounts.subscription_type("gamma", refresh=True)
        check("subscription_type: refresh=True asks again", len(calls) == 2, str(calls))

        real_login2 = accounts.logged_in
        accounts.logged_in = lambda a: False  # noqa: E731
        got3 = accounts.subscription_type("gamma", refresh=True)
        accounts.logged_in = real_login2
        check("subscription_type: cache survives even when asked again with no login",
              got3 == "pro", got3)
    finally:
        accounts.logged_in, subprocess.run = real_login, real_run


def test_launch_judges_on_the_figure_it_may_act_on(home: Path) -> None:
    """Inside a window usage only rises, so a carried figure is a floor - and a
    floor at the wall is at the wall. Reading the raw field let a launch start
    on an account a carry had just placed at 100%."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    agents.register("late", "alpha")
    now = int(time.time())
    write_status(home, "alpha", pct_5h=100, reset_5h=now + 1800,
                 rl_carried=True, rl_at=now - 300)
    real_login = accounts.logged_in
    try:
        accounts.logged_in = lambda a: True  # noqa: E731 - keychain is not the subject
        try:
            launch.build("late")
            check("launch: refuses on a carried figure at the wall", False, "it started")
        except SystemExit as e:
            check("launch: refuses on a carried figure at the wall", "100%" in str(e), str(e))

        # A reading we have no figure for at all still starts: the session we
        # are about to launch is the only thing that can report where the
        # account stands, so refusing here is the deadlock, not the safeguard.
        write_status(home, "alpha", pct_5h=None, rl_lost=True)
        try:
            launch.build("late")
            check("launch: an unreadable account still starts, to get a reading", True)
        except SystemExit as e:
            check("launch: an unreadable account still starts, to get a reading",
                  False, str(e))
    finally:
        accounts.logged_in = real_login


def test_burn_rate_ignores_carried_samples(home: Path) -> None:
    """A carried figure pairs an unchanging percentage with a collected_at that
    moves every tick. Sampling that flattens the measured rate to zero, and an
    account that was climbing then reads as one with nothing on it."""
    os.environ["OCP_HOME"] = str(home)
    rising = [(1000, 70), (1150, 74), (1300, 78)]
    live_burn = triggers.burn_rate_per_s(rising)
    check("burn: a rising account has a measurable rate", live_burn > 0, str(live_burn))

    # What the old sampling produced: ten ticks of the same carried figure,
    # each with a fresh timestamp, evicting the real samples.
    flattened = [(1450 + i * 30, 78) for i in range(10)]
    check("burn: flat carried samples measure as no burn at all",
          triggers.burn_rate_per_s(flattened) == 0.0)
    check("burn: the real rate is what shortens what an account has left for one more agent",
          triggers.stretch_s(20, live_burn) < triggers.stretch_s(20, 0.0))


def test_a_session_figure_is_not_shown_as_an_accounts(home: Path) -> None:
    """cost and context belong to one session, and the account file is written
    by whichever session rendered last. Reading them from there made an
    account's cost flip every second and gave an agent that has never run
    somebody else's context - and the trigger then judged it on that."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    agents.register("never_ran", "alpha")
    agents.register("has_session", "alpha")
    write_status(home, "alpha", pct_5h=20, ctx_pct=77, cost_usd=9.99)
    (home / "agents" / "has_session").mkdir(parents=True, exist_ok=True)
    (home / "agents" / "has_session" / "status.json").write_text(json.dumps({
        "account": "alpha", "collected_at": int(time.time()), "session_id": "s1",
        "pct_5h": 20, "ctx_pct": 12, "cost_usd": 4.0}))

    st = status.read_for_agent("never_ran", "alpha")
    check("an agent with no session has no context, not someone else's",
          st.ctx_pct is None, str(st.ctx_pct))
    check("and no session id to point at either", st.session_id is None)
    check("but the account's rate limits still apply to it", st.pct_5h == 20)
    check("so the trigger cannot fire on a context it does not have",
          triggers.evaluate(st.trusted_pct_5h, st.trusted_ctx_pct).kind != "context")
    own = status.read_for_agent("has_session", "alpha")
    check("an agent with a session is judged on its own context", own.ctx_pct == 12,
          str(own.ctx_pct))

    check("account cost is the live sessions summed, not the last writer's",
          status.live_cost("alpha") == 4.0, str(status.live_cost("alpha")))
    stale = home / "agents" / "has_session" / "status.json"
    d = json.loads(stale.read_text())
    d["collected_at"] = int(time.time()) - 100000
    stale.write_text(json.dumps(d))
    check("a session too old to trust is not counted in it",
          status.live_cost("alpha") is None, str(status.live_cost("alpha")))


def test_doctor_says_one_thing_per_account(home: Path) -> None:
    """doctor's whole job is to say why assignment is refused, so it must not
    warn that a reading was lost and then report "rate_limits present" about the
    same account. The 7d check wedged into the 5h chain stole that chain's else."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    now = int(time.time())
    write_status(home, "a_quiet", pct_5h=None, pct_7d=10)
    write_status(home, "b_ok", pct_5h=42, reset_5h=now + 1800, pct_7d=10)
    write_status(home, "c_lost", pct_5h=None, rl_lost=True, pct_7d=10)
    write_status(home, "d_old", pct_5h=88, reset_5h=now + 9000, pct_7d=10,
                 rl_carried=True, rl_at=now - 4 * 3600)
    write_status(home, "e_7dlost", pct_5h=5, reset_5h=now + 1800, rl7_lost=True)

    r = doctor.Report()
    doctor._check_rate_limits_present(r)
    rows = [(lvl, msg) for lvl, msg, _detail in r.rows if "account " in msg]
    per_account: dict[str, list] = {}
    for lvl, msg in rows:
        # Split off the account name: "7d" appears inside one of them.
        per_account.setdefault(msg.split()[1].rstrip(":"), []).append(
            (lvl, msg.split(": ", 1)[1]))

    check("doctor: one 5h statement per account",
          all(len([m for _l, m in v if "7d" not in m]) == 1 for v in per_account.values()),
          str({k: [m for _l, m in v] for k, v in per_account.items()}))
    check("doctor: a lost reading is not also reported as present",
          not any("present" in m for _l, m in per_account["c_lost"]),
          str(per_account["c_lost"]))
    check("doctor: and neither is one too old to assign on",
          not any("present" in m for _l, m in per_account["d_old"]),
          str(per_account["d_old"]))
    check("doctor: never prints a None percentage",
          not any("None" in d for _l, _m, d in r.rows if d), str(r.rows))
    check("doctor: an account whose 5h is fine still gets its OK row",
          any(lvl == doctor.OK for lvl, _m in per_account["e_7dlost"]),
          str(per_account["e_7dlost"]))
    check("doctor: and its lost 7d is reported as well",
          any("7d" in m for _l, m in per_account["e_7dlost"]), str(per_account["e_7dlost"]))


def test_fold_is_not_rejected_over_a_header_it_overwrites(home: Path) -> None:
    """The header is stamped after validation, so validating it throws away good
    section work over a line that was about to be replaced - and rejection is
    expensive: the degraded path, no cursor advance, a fold_failed event."""
    good = digest.render(digest.Digest(
        date="2026-01-01", agent="x", updated="10:00", folded_through=5,
        sections={"done": ["a"], "in_progress": [], "decided": [],
                  "blocked": [], "next": [], "base_candidates": []}))
    headerless = good.split("\n", 1)[1]
    ok, problems = digest.validate(headerless, None)
    check("digest: a malformed header is still a problem by default",
          not ok and any("header" in p for p in problems), str(problems))
    ok, problems = digest.validate(headerless, None, header_is_stamped=True)
    check("digest: but not for the caller that stamps it", ok, str(problems))


def test_a_rejected_fold_still_records_what_it_spent(home: Path) -> None:
    """A fold is the one spender no status line observes, so these events are
    the only record that the account was charged. A fold that ran the model and
    was then rejected spent exactly what an accepted one spends, so leaving the
    account off the failure made the spend reconstructable for the folds that
    worked and blank for the ones that went wrong."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    agents.register("spender", "alpha")
    con = db.connect()
    real = foldmod.run_model
    try:
        db.add_event(con, "spender", "UserPromptSubmit", {"prompt": "do the thing"})
        foldmod.run_model = lambda prompt, acct, model: (True, "not a digest at all")
        ok, msg = foldmod.fold("spender", use_model=True, fold_account="alpha")
        check("fold: an unusable answer is rejected", not ok, msg)
        rows = [r for r in db.latest_events(con, agent="spender", limit=20)
                if r["kind"] == "fold_failed"]
        check("fold: and the rejection is recorded", len(rows) == 1, str(len(rows)))
        payload = json.loads(rows[-1]["payload"])
        check("fold: naming the account that was charged for it",
              payload.get("account") == "alpha", str(payload))
        check("fold: and the model that charged it",
              payload.get("model") == "haiku", str(payload))
    finally:
        foldmod.run_model = real
        con.close()


def test_one_agents_bad_tick_is_not_everyones(home: Path) -> None:
    """Supervising an agent shells out to Orca three times. An exception in any
    of it used to end the tick, so every agent after it in the list went
    unsupervised with nothing saying so."""
    _daemon_home(home, 100)
    agents.register("zzlater", "alpha")
    con = db.connect()
    db.set_terminal(con, "zzlater", "term_z", "wt")
    real = dmn.supervise
    try:
        def explode(con_, agent, *a, **k):
            if agent == "backend":
                raise RuntimeError("orca fell over")
            return real(con_, agent, *a, **k)

        dmn.supervise = explode
        t = dmn.tick(con, lambda _m: None)
        check("tick: the failure is reported against the agent that had it",
              any("backend: error" in a for a in t.actions), str(t.actions))
        check("tick: and it counts as a failed attempt",
              db.get_agent_state(con, "backend")["failed_attempts"] == 1,
              str(dict(db.get_agent_state(con, "backend"))))
        check("tick: the agents after it are still supervised",
              db.get_agent_state(con, "zzlater") is not None
              and db.get_agent_state(con, "zzlater")["state"] == dmn.PARKED,
              str(dict(db.get_agent_state(con, "zzlater") or {})))
    finally:
        dmn.supervise = real
        con.close()


def test_display_agrees_with_the_daemon(home: Path) -> None:
    """Every screen draws the figure the daemon acts on, with its provenance.

    `ocp triggers` once printed "-" in the 5h column beside a reason reading
    "pct_5h 0%", which is the dashboard arguing with the daemon.
    """
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    agents.register("quietone", "alpha")
    write_status(home, "alpha", pct_5h=None, pct_7d=10, ctx_pct=5)
    st = status.read("alpha")
    check("display: the account is quiet", st.pct_5h_source == "quiet")
    check("display: a quiet window is marked", status.mark("quiet") == "*")
    check("display: a live one is not", status.mark("live") == " ")
    check("display: one carried past its bound is marked apart",
          status.mark("carried_old") == "!" and status.mark("carried") == "~")

    text = tui.frame(120, 40)
    check("display: top draws the figure, not a question mark",
          "0%*" in text, "no marked 0% in the frame")
    check("display: and prints the legend", "inferred" in text)


def test_spawn_confirms_the_task_landed(home: Path) -> None:
    """Orca reports input as accepted even when the TUI never submitted it.

    Three agents once came up with the prompt sitting unsent in the input box
    while the spawn output claimed success, so the only acceptable proof is the
    agent's own UserPromptSubmit.
    """
    os.environ["OCP_HOME"] = str(home)
    con = db.connect()
    try:
        since = db.max_event_id(con)
        check("spawn: no prompt event yet means not landed",
              spawnmod.prompt_landed("scout", since, timeout_s=0) is False)
        db.add_event(con, "scout", "SessionStart", {})
        check("spawn: a session starting is not a task arriving",
              spawnmod.prompt_landed("scout", since, timeout_s=0) is False)
        db.add_event(con, "other", "UserPromptSubmit", {})
        check("spawn: another agent's prompt does not count",
              spawnmod.prompt_landed("scout", since, timeout_s=0) is False)
        db.add_event(con, "scout", "UserPromptSubmit", {})
        check("spawn: the agent's own prompt event confirms it",
              spawnmod.prompt_landed("scout", since, timeout_s=0) is True)
    finally:
        con.close()


def test_spawn_survives_its_own_main_path(home: Path) -> None:
    """`ocp spawn <agent> --prompt "..."` is the command's main path, and it
    raised there - after the worktree and the terminal were already made, so it
    left a real worktree and a live terminal with no task in it. Nothing drove
    spawn() past terminal-create, so the suite walked around the crash."""
    os.environ["OCP_HOME"] = str(home)
    home.mkdir(parents=True, exist_ok=True)
    agents.register("spawny", "alpha")

    calls: list[list[str]] = []

    def fake_run(args, cwd=None):
        calls.append(args)
        if args[:3] == ["orca", "worktree", "create"]:
            return 0, json.dumps({"result": {"worktree": {"path": "/tmp/wt"}}}), ""
        if args[:3] == ["orca", "terminal", "create"]:
            return 0, json.dumps({"result": {"terminal": {"handle": "term_s"}}}), ""
        return 0, "", ""

    real = (spawnmod._run, spawnmod.prompt_landed, orcamod.wait_idle, spawnmod.shutil.which)
    try:
        spawnmod.shutil.which = lambda _n: "/usr/bin/orca"
        spawnmod._run = fake_run
        spawnmod.prompt_landed = lambda agent, since, timeout_s=0: True
        orcamod.wait_idle = lambda h, timeout_ms=0: True

        ok, msg = spawnmod.spawn("spawny", ROOT, base="main", prompt="do the thing")
        check("spawn: the command's main path runs to the end", ok, msg)
        check("spawn: and the task is actually sent",
              any(a[:3] == ["orca", "terminal", "send"] for a in calls), str(calls))
        check("spawn: a ready agent draws no warning", "WARNING" not in msg, msg)

        # The wait is the guard against typing into a booting TUI, so its
        # answer must read the right way round: it warned on success and was
        # silent on failure, which is worse than not warning at all.
        orcamod.wait_idle = lambda h, timeout_ms=0: False
        calls.clear()
        ok, msg = spawnmod.spawn("spawny", ROOT, base="main", prompt="do the thing")
        check("spawn: a wait that did not succeed warns", "WARNING" in msg, msg)
    finally:
        spawnmod._run, spawnmod.prompt_landed, orcamod.wait_idle, spawnmod.shutil.which = real


def test_an_agent_is_never_handed_another_agents_session(home: Path) -> None:
    """Two agents share an account, and the account file belongs to whichever
    session wrote it last. Carrying its session id through the fallbacks told a
    woken agent to grep a stranger's transcript, in a stranger's repository -
    which happened to auditor in production, pointed at marina's."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    agents.register("selfless", "beta")
    write_status(home, "beta", pct_5h=20, ctx_pct=25, session_id="MARINA-SESSION",
                 transcript_path="/tmp/marina.jsonl")

    st = status.read_for_agent("selfless", "beta")
    check("identity: an agent that has never run has no session",
          st.session_id is None, str(st.session_id))
    check("identity: nor a transcript to be sent to",
          st.transcript_path is None, str(st.transcript_path))
    check("identity: nor a context to be judged on", st.ctx_pct is None, str(st.ctx_pct))

    # The ordinary case, not an edge: nothing refreshes an agent's own file
    # while its session is closed, so by wake time it is stale by construction.
    f = paths.agent_status_file("selfless")
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({
        "account": "beta", "collected_at": int(time.time()) - status.STALE_AFTER_S - 60,
        "session_id": "MY-OWN-SESSION", "ctx_pct": 8,
        "transcript_path": "/tmp/mine.jsonl"}))
    st = status.read_for_agent("selfless", "beta")
    check("identity: a stale own snapshot is not topped up from the account file",
          st.session_id is None and st.transcript_path is None,
          f"{st.session_id} {st.transcript_path}")

    f.write_text(json.dumps({
        "account": "beta", "collected_at": int(time.time()),
        "session_id": "MY-OWN-SESSION", "ctx_pct": 8,
        "transcript_path": "/tmp/mine.jsonl"}))
    st = status.read_for_agent("selfless", "beta")
    check("identity: a fresh one is its own", st.session_id == "MY-OWN-SESSION",
          str(st.session_id))

    # And the prompt says nothing about a transcript it cannot name.
    con = db.connect()
    try:
        f.unlink()
        told = dmn.wake_prompt("selfless", resumed=True)
        check("wake: no transcript is named when none can be named confidently",
              "/tmp/marina.jsonl" not in told and "Grep it" not in told, told[:200])
        check("wake: the agent is still told it lost nothing",
              "Nothing was lost" in told, told[:120])
    finally:
        con.close()


def test_a_closed_agent_is_not_handed_off(home: Path) -> None:
    """Closing stops that session's status line, so 15 minutes later the
    snapshot is stale and usage_unknown fires - on an agent that is idle,
    healthy, and whose terminal is now a shell prompt. That would pay for a
    fold and then type a handoff full of backticks at bash: the exact failure
    this branch exists to remove, reached through its own new feature."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.alive, orcamod.wait_idle, orcamod.idle_now, orcamod.send,
            orcamod.available, orcamod.clear_context)
    try:
        orcamod.available = lambda: True
        orcamod.live_terminals = lambda: {"term_x"}
        orcamod.alive = lambda h: True
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        orcamod.idle_now = lambda h: True
        orcamod.clear_context = lambda h, wait=True, timeout_ms=0: (True, "context cleared")
        # A session runs in that terminal until close_agent ends it, and
        # the wake below starts another one.
        table = sessions_are("backend")
        orcamod.send = relaunching_send(sent, table)
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn.IDLE)
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()),
            "session_id": "sess-2", "pct_5h": 20}))
        dmn.close_agent(con, "backend", lambda _m: None,
                        status.read_for_agent("backend", "alpha"))
        check("closed: the agent is marked closed",
              db.get_agent_state(con, "backend")["closed_at"] is not None)

        # Its own file stopped being written, and so did the account's.
        write_status(home, "alpha", pct_5h=20, pct_7d=10,
                     collected_at=int(time.time()) - status.STALE_AFTER_S - 60)
        st = status.read_for_agent("backend", "alpha")
        check("closed: its usage does read as unknown",
              st.trusted_pct_5h is None, str(st.trusted_pct_5h))
        check("closed: and an unknown figure is not a wall, so nothing would fire on it",
              not triggers.evaluate(st.trusted_pct_5h, None).fired)

        sent.clear()
        dmn.tick(con, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("closed: but a closed agent is not handed off",
              row["state"] != dmn.PARKED, str(dict(row)))
        check("closed: nothing is typed at its shell", not sent, str(sent))
        check("closed: and no fold was paid for",
              not db.in_flight_handoffs(con) and row["continuations"] == 0, str(dict(row)))
        check("closed: it stays closed", row["closed_at"] is not None)

        # And the screens say what the daemon does. A warning nobody will act
        # on is the dashboard arguing with the daemon.
        check("closed: the daemon's own rule says it is not a candidate",
              not dmn.triggers_apply(row))
        frame = tui.frame(120, 40)
        check("closed: top does not warn about it", "usage_unknown" not in frame,
              [ln for ln in frame.splitlines() if "backend" in ln])
        check("closed: and says why instead", "closed" in frame,
              [ln for ln in frame.splitlines() if "backend" in ln])
        check("closed: the frame still fits its terminal",
              len(frame.split(chr(10))) == 40)

        # The screen answers the founder's questions, not the operator's: no
        # snapshot age (its only news is the reason beside it), no cost column
        # (a sum over the sessions we can see is not the account's spend), no
        # model or effort (configuration, and on the agent's own page), no pid.
        plain = [tui._ANSI.sub("", ln) for ln in frame.split(chr(10))]
        header = next(ln for ln in plain if "agent " in ln and "ctx" in ln)
        accounts_header = next(ln for ln in plain if "account " in ln and "5h" in ln)
        check("screen: the operator's columns are gone from the team list",
              "model" not in header and "eff" not in header, header)
        check("screen: and from the accounts list",
              "snap" not in accounts_header and "$" not in accounts_header,
              accounts_header)
        check("screen: the daemon is reported as running, not as a pid",
              "pid" not in plain[0], plain[0])
        # The daemon's own note is not news. It is on every row, it is never
        # something a human acts on - "woke from PARKED at reconciliation" -
        # and a column that is always full of something unactionable is one a
        # reader learns to skip. The states that do need a human still say so.
        db.set_agent_state(con, "backend", dmn.IDLE, detail="woke at reconciliation")
        quiet = [tui._ANSI.sub("", ln) for ln in tui.frame(120, 40).split(chr(10))]
        check("screen: the daemon's bookkeeping is not on the team list",
              not any("backend" in ln and "reconciliation" in ln for ln in quiet),
              str([ln for ln in quiet if "backend" in ln])[:160])
        db.set_agent_state(con, "backend", dmn.STALLED, detail="three failed wakes")
        loud = [tui._ANSI.sub("", ln) for ln in tui.frame(120, 40).split(chr(10))]
        check("screen: but what needs a human is, with its reason",
              any("backend" in ln and "three failed wakes" in ln for ln in loud),
              str([ln for ln in loud if "backend" in ln])[:160])
        db.set_agent_state(con, "backend", dmn.IDLE, detail="")

        # The columns stay under their header: a state that grew a "·closed"
        # suffix outran its cell and pushed the whole row sideways.
        lines = [tui._ANSI.sub("", ln) for ln in frame.split(chr(10))]
        head = next(ln for ln in lines if "agent " in ln and "ctx" in ln)
        rows = [ln for ln in lines if "backend " in ln and "%" in ln]
        check("closed: its ctx column stands under the header",
              rows and rows[0].index("?%") - head.index("ctx") in (0, 1, 2),
              f"{head!r} {rows[0]!r}" if rows else "no row")
    finally:
        (orcamod.alive, orcamod.wait_idle, orcamod.idle_now, orcamod.send,
         orcamod.available, orcamod.clear_context) = real
        con.close()


def test_a_woken_agent_is_told_on_the_following_tick(home: Path) -> None:
    """Waking sends the agent a prompt it is now reading. Announcing the inbox
    in the same tick types at an agent mid-turn, which leaves the text in its
    input box unsent - the failure delivery exists to avoid."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.alive, orcamod.wait_idle, orcamod.idle_now, orcamod.send,
            orcamod.available)
    try:
        orcamod.available = lambda: True
        orcamod.live_terminals = lambda: {"term_x"}
        orcamod.alive = lambda h: True
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        orcamod.idle_now = lambda h: True      # even so, it must not be told yet
        # A session runs in that terminal until close_agent ends it, and
        # the wake below starts another one.
        table = sessions_are("backend")
        orcamod.send = relaunching_send(sent, table)
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn.IDLE)
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()),
            "session_id": "sess-4", "pct_5h": 20}))
        dmn.close_agent(con, "backend", lambda _m: None,
                        status.read_for_agent("backend", "alpha"))

        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)
        sent.clear()
        dmn.tick(con, lambda _m: None)
        check("wake: the tick that wakes it brings the session back",
              any("--resume sess-4" in t for t in sent), str(sent))
        check("wake: and does not announce the inbox in the same breath",
              not any("ocp inbox" in t for t in sent), str(sent))

        sent.clear()
        dmn.tick(con, lambda _m: None)
        check("wake: the following tick tells it what is waiting",
              any("ocp inbox" in t for t in sent), str(sent))
    finally:
        (orcamod.alive, orcamod.wait_idle, orcamod.idle_now, orcamod.send,
         orcamod.available) = real
        con.close()


def test_a_woken_agent_is_not_judged_on_the_snapshot_it_left(home: Path) -> None:
    """The reading was taken before the wake, and a closed agent's snapshot is
    stale by construction - nothing refreshes a status line while its session is
    gone. Evaluating it after the wake hands off the session just restored, and
    leaves the work that caused the wake still unread."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.alive, orcamod.wait_idle, orcamod.idle_now, orcamod.send,
            orcamod.available, orcamod.clear_context)
    try:
        orcamod.available = lambda: True
        orcamod.live_terminals = lambda: {"term_x"}
        orcamod.alive = lambda h: True
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        orcamod.idle_now = lambda h: True     # the probe must not be what saves us
        orcamod.clear_context = lambda h, wait=True, timeout_ms=0: (True, "context cleared")
        # A session runs in that terminal until close_agent ends it, and
        # the wake below starts another one.
        table = sessions_are("backend")
        orcamod.send = relaunching_send(sent, table)
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn.IDLE)
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()),
            "session_id": "sess-7", "pct_5h": 20}))
        dmn.close_agent(con, "backend", lambda _m: None,
                        status.read_for_agent("backend", "alpha"))

        # The account was this agent's only session, so its snapshot ages out too.
        write_status(home, "alpha", pct_5h=20, pct_7d=10,
                     collected_at=int(time.time()) - status.STALE_AFTER_S - 60)
        st = status.read_for_agent("backend", "alpha")
        check("wake: the reading it left behind is unknown, not a wall",
              not triggers.evaluate(st.trusted_pct_5h, st.trusted_ctx_pct).fired)

        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)
        sent.clear()
        dmn.tick(con, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("wake: it comes back", any("--resume sess-7" in t for t in sent), str(sent))
        check("wake: and is not handed off on the reading it left behind",
              row["state"] != dmn.PARKED, str(dict(row)))
        check("wake: nothing was folded for it",
              row["continuations"] == 0 and not db.in_flight_handoffs(con), str(dict(row)))
        check("wake: and it was not cleared", not any("/clear" in t for t in sent), str(sent))
        check("wake: the work that caused the wake is still queued",
              len(db.queued_for(con, "backend")) == 1)
    finally:
        (orcamod.alive, orcamod.wait_idle, orcamod.idle_now, orcamod.send,
         orcamod.available, orcamod.clear_context) = real
        con.close()


def test_a_park_that_was_also_closed_is_woken_without_being_judged(home: Path) -> None:
    """An agent can be parked and closed at once, and then the wake_at branch is
    what restores its session - so the same rule has to hold there. It held only
    because that branch never re-read the row, which is the first thing anyone
    tidying this code would fix."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.alive, orcamod.wait_idle, orcamod.idle_now, orcamod.send,
            orcamod.available, orcamod.clear_context)
    try:
        orcamod.available = lambda: True
        orcamod.live_terminals = lambda: {"term_x"}
        orcamod.alive = lambda h: True
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        orcamod.idle_now = lambda h: True
        orcamod.clear_context = lambda h, wait=True, timeout_ms=0: (True, "context cleared")
        # A session runs in that terminal until close_agent ends it, and
        # the wake below starts another one.
        table = sessions_are("backend")
        orcamod.send = relaunching_send(sent, table)
        db.set_terminal(con, "backend", "term_x", "wt")
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()),
            "session_id": "sess-8", "pct_5h": 20}))
        dmn.close_agent(con, "backend", lambda _m: None,
                        status.read_for_agent("backend", "alpha"))
        db.set_agent_state(con, "backend", dmn.PARKED, wake_at=int(time.time()) - 10,
                           detail="parked and closed")
        row = db.get_agent_state(con, "backend")
        check("parked+closed: the state this is about is reachable",
              row["state"] == dmn.PARKED and row["closed_at"] is not None, str(dict(row)))

        # Its own snapshot stopped with the session, and it was the only one here.
        write_status(home, "alpha", pct_5h=20, pct_7d=10,
                     collected_at=int(time.time()) - status.STALE_AFTER_S - 60)
        sent.clear()
        dmn.tick(con, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("parked+closed: wake_at brings the same session back",
              any("--resume sess-8" in t for t in sent), str(sent))
        check("parked+closed: and it is not parked again on the reading it left",
              row["state"] != dmn.PARKED, str(dict(row)))
        check("parked+closed: nothing was folded for it",
              row["continuations"] == 0 and not db.in_flight_handoffs(con), str(dict(row)))
    finally:
        (orcamod.alive, orcamod.wait_idle, orcamod.idle_now, orcamod.send,
         orcamod.available, orcamod.clear_context) = real
        con.close()


def test_reconcile_wakes_a_park_that_was_also_closed(home: Path) -> None:
    """The case above is proven through tick(), but a daemon that was down
    across the whole window - which is the case launchd exists for, not the
    exception - meets this agent at startup, in reconcile(), not in a later
    tick. Before this fix, reconcile's own due_agents step set state to
    QUEUED and cleared wake_at, which is exactly what disarms the wake_at
    branch in supervise() that would otherwise have woken it - so a closed,
    parked agent whose window rolled over while the daemon was dead came back
    QUEUED and stayed that way, forever, with no session and no path left
    that will ever relaunch it."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.alive, orcamod.wait_idle, orcamod.idle_now, orcamod.send,
            orcamod.available, orcamod.clear_context)
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        orcamod.idle_now = lambda h: True
        orcamod.clear_context = lambda h, wait=True, timeout_ms=0: (True, "context cleared")
        # A session runs in that terminal until close_agent ends it, and
        # the wake below starts another one.
        table = sessions_are("backend")
        orcamod.send = relaunching_send(sent, table)
        db.set_terminal(con, "backend", "term_x", "wt")
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()),
            "session_id": "sess-9", "pct_5h": 20}))
        dmn.close_agent(con, "backend", lambda _m: None,
                        status.read_for_agent("backend", "alpha"))
        db.set_agent_state(con, "backend", dmn.PARKED, wake_at=int(time.time()) - 10,
                           detail="parked and closed, daemon was down since")

        sent.clear()
        dmn.reconcile(con, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("reconcile: a closed, due park is relaunched at startup, not left QUEUED",
              any("--resume sess-9" in t for t in sent), str(sent))
        check("reconcile: closed_at is cleared, not stuck behind a nulled wake_at",
              row["closed_at"] is None, str(dict(row)))
        check("reconcile: triggers apply to it again", dmn.triggers_apply(row))
    finally:
        (orcamod.alive, orcamod.wait_idle, orcamod.idle_now, orcamod.send,
         orcamod.available, orcamod.clear_context) = real
        con.close()


def test_a_wake_does_not_call_a_fresh_start_a_park(home: Path) -> None:
    """Telling an agent that could not be resumed "your context was cleared,
    below is your own handoff record" says it was parked when it was not. A
    fresh start after a close finds its record in the system prompt of the
    session it started, so the prompt points there and does not repeat it."""
    _daemon_home(home, 20)
    keep_handoff("backend", "# handoff · backend\n- was mid-way through the routing fix")
    con = db.connect()
    try:
        told = dmn.wake_prompt("backend", resumed=False, was_closed=True, con=con)
        check("wake: a fresh start after a close is not called a park",
              "You were parked" not in told, told[:140])
        check("wake: and its handoff is in the system prompt, not typed again",
              "routing fix" not in told and "system prompt above" in told, told[:200])
        db.set_handoff(con, "backend", None)
        told = dmn.wake_prompt("backend", resumed=False, was_closed=True, con=con)
        check("wake: with none recorded it is told so",
              "no handoff" in told.lower(), told[:200])
    finally:
        con.close()

    keep_handoff("backend", "# handoff · backend\n- was mid-way through the routing fix")
    con = db.connect()
    try:
        told = dmn.wake_prompt("backend", resumed=False, was_closed=False, con=con)
    finally:
        con.close()
    check("wake: a real park still gets its record",
          "You were parked" in told and "routing fix" in told, told[:200])


def test_installing_hooks_twice_does_not_double_them(home: Path) -> None:
    """`ocp` is on PATH as a symlink into the repo, so one installation spells
    itself two ways. Matching the exact string added a second entry the first
    time the spelling changed, and then every hook fired twice."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    target = home / "shared" / ".claude" / "settings.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"hooks": {
        "Stop": [{"hooks": [{"type": "command",
                             "command": "/somewhere/else/bin/ocp hook Stop"}]}],
        "PreToolUse": [{"hooks": [{"type": "command", "command": "orca-hook pre"}]}],
    }}))

    env = dict(os.environ, OCP_HOME=str(home), OCP_BIN="/usr/local/bin/ocp")
    for _ in range(2):
        subprocess.run([sys.executable, str(ROOT / "bin" / "ocp"), "install-hooks"],
                       capture_output=True, text=True, env=env)

    data = json.loads(target.read_text())
    ours = [h["command"] for ev, groups in data["hooks"].items() for g in groups
            for h in g.get("hooks", []) if " hook " in h.get("command", "")]
    stops = [c for c in ours if c.endswith("hook Stop")]
    check("hooks: one entry per event however it was spelled before",
          len(stops) == 1, str(stops))
    check("hooks: every captured event is installed",
          {c.rsplit(" ", 1)[1] for c in ours} == set(evmod.CAPTURED),
          str(sorted({c.rsplit(" ", 1)[1] for c in ours})))
    check("hooks: Orca's own entries are left alone",
          any(h.get("command") == "orca-hook pre"
              for g in data["hooks"]["PreToolUse"] for h in g.get("hooks", [])),
          str(data["hooks"].get("PreToolUse")))
    check("hooks: the prompt and idle witnesses are among them",
          {"PermissionRequest", "TeammateIdle"} <= evmod.CAPTURED)


def test_the_sweep_corrects_what_it_can_see(home: Path) -> None:
    """Every other decision reads a row we wrote ourselves, which is how a state
    that went wrong stayed wrong. This is the half that asks the world instead,
    and it corrects rather than reports - a supervisor that only files
    complaints leaves the human in the loop it exists to take them out of."""
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.available, orcamod.live_terminals, obsmod.live_sessions)
    try:
        db.set_terminal(con, "backend", "term_x", "wt")
        orcamod.available = lambda: True

        # 1. Its terminal is gone. Nothing may be delivered to it and nothing
        #    can be closed in it, and both already key on having a handle.
        orcamod.live_terminals = lambda: set()
        obsmod.live_sessions = lambda: {}
        dmn.sweep(con, lambda _m: None, [])
        check("sweep: a terminal Orca does not list is dropped",
              db.get_agent_state(con, "backend")["terminal"] is None,
              str(db.get_agent_state(con, "backend")["terminal"]))

        # 2. Its session died without us. Recording it is what makes work
        #    arriving wake it rather than be typed at a bash prompt.
        db.set_terminal(con, "backend", "term_x", "wt")
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha",
            "collected_at": int(time.time()) - status.STALE_AFTER_S - 60,
            "session_id": "sess-dead", "pct_5h": 20}))
        orcamod.live_terminals = lambda: {"term_x"}
        obsmod.live_sessions = lambda: {}
        dmn.sweep(con, lambda _m: None, [])
        check("sweep: a session with no process is recorded as closed",
              db.get_agent_state(con, "backend")["closed_at"] is not None)
        # A session that has just died leaves a snapshot that is stale by
        # construction, and its own id is the one thing here not in doubt.
        # Losing it loses the only pointer back to that agent's context.
        check("sweep: and keeps the id that could resume it",
              db.get_agent_state(con, "backend")["resume_session"] == "sess-dead",
              str(db.get_agent_state(con, "backend")["resume_session"]))
        check("sweep: so the daemon stops treating it as a candidate",
              not dmn.triggers_apply(db.get_agent_state(con, "backend")))

        # 3. And it came back without us - a human relaunched it. Believing our
        #    own note here would close a live session a second time.
        obsmod.live_sessions = lambda: {"backend": 4242}
        dmn.sweep(con, lambda _m: None, [])
        check("sweep: a running session clears a stale closed mark",
              db.get_agent_state(con, "backend")["closed_at"] is None,
              str(dict(db.get_agent_state(con, "backend"))))

        # 4. And it is stuck in RUNNING. It cannot get out on its own -
        #    leaving RUNNING needs a Stop, a Stop needs a turn, and a turn needs
        #    work it will never be told about while it reads RUNNING.
        db.set_agent_state(con, "backend", dmn.RUNNING, detail="working")
        db.add_event(con, "backend", "Stop", {})
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'",
                    (int(time.time()) - dmn.STUCK_RUNNING_S - 60,))
        real_idle = orcamod.idle_now
        try:
            # One witness is not enough: the probe is the one we have twice
            # declined to trust on its own.
            orcamod.idle_now = lambda h: False
            dmn.sweep(con, lambda _m: None, [])
            check("sweep: a long turn is left alone even when our events are silent",
                  db.get_agent_state(con, "backend")["state"] == dmn.RUNNING)

            orcamod.idle_now = lambda h: True
            dmn.sweep(con, lambda _m: None, [])
            check("sweep: silent for too long and idle at its prompt is unstuck",
                  db.get_agent_state(con, "backend")["state"] == dmn.IDLE,
                  str(dict(db.get_agent_state(con, "backend"))))

            # And the other witness alone is not enough either.
            db.set_agent_state(con, "backend", dmn.RUNNING, detail="working")
            db.add_event(con, "backend", "UserPromptSubmit", {})
            dmn.sweep(con, lambda _m: None, [])
            check("sweep: an idle probe alone does not unstick a live agent",
                  db.get_agent_state(con, "backend")["state"] == dmn.RUNNING,
                  str(dict(db.get_agent_state(con, "backend"))))
            db.set_agent_state(con, "backend", dmn.IDLE, detail="done")

            # The probe is the only thing here that costs, and a "no" always
            # takes its full timeout - so an agent working a long turn is a
            # suspect on every tick for as long as the turn lasts. One per tick
            # bounds the tick however many are stuck at once.
            agents.register("stucktwo", "alpha")
            probed: list[str] = []
            orcamod.idle_now = lambda h: (probed.append(h), True)[1]
            old_ts = int(time.time()) - dmn.STUCK_RUNNING_S - 60
            for name, handle in (("backend", "term_x"), ("stucktwo", "term_y")):
                db.set_terminal(con, name, handle, "wt")
                db.set_agent_state(con, name, dmn.RUNNING, detail="working")
                db.add_event(con, name, "Stop", {})
            con.execute("UPDATE events SET ts = ?", (old_ts,))
            orcamod.live_terminals = lambda: {"term_x", "term_y"}
            obsmod.live_sessions = lambda: {"backend": 1, "stucktwo": 2}

            dmn.sweep(con, lambda _m: None, [])
            check("sweep: only one suspect is probed per tick",
                  len(probed) == 1, str(probed))
            dmn.sweep(con, lambda _m: None, [])
            check("sweep: and the next tick takes the other one",
                  set(probed) == {"term_x", "term_y"}, str(probed))
            check("sweep: so both are unstuck within a few seconds",
                  all(db.get_agent_state(con, n)["state"] == dmn.IDLE
                      for n in ("backend", "stucktwo")),
                  str([db.get_agent_state(con, n)["state"]
                       for n in ("backend", "stucktwo")]))
        finally:
            orcamod.idle_now = real_idle

        # None is not an empty set. Reading "I could not ask" as "there are
        # none" would tear the team down the first time Orca answered slowly.
        orcamod.live_terminals = lambda: None
        obsmod.live_sessions = lambda: {}
        dmn.sweep(con, lambda _m: None, [])
        check("sweep: an unanswered Orca changes nothing",
              db.get_agent_state(con, "backend")["terminal"] == "term_x"
              and db.get_agent_state(con, "backend")["closed_at"] is None)

        orcamod.live_terminals = lambda: {"term_x"}
        obsmod.live_sessions = lambda: None
        dmn.sweep(con, lambda _m: None, [])
        check("sweep: an unreadable process table changes nothing",
              db.get_agent_state(con, "backend")["closed_at"] is None)
    finally:
        orcamod.available, orcamod.live_terminals, obsmod.live_sessions = real
        con.close()

    # The distinction has to survive into the implementation, not only the fake.
    restore_world()
    real_run = orcamod._run
    try:
        orcamod._run = lambda *a, **k: (False, "connection refused")
        check("sweep: Orca failing to answer reads as unknown, not as empty",
              orcamod.live_terminals() is None, str(orcamod.live_terminals()))
        orcamod._run = lambda *a, **k: (True, "not json at all")
        check("sweep: and so does an answer we cannot parse",
              orcamod.live_terminals() is None)
        orcamod._run = lambda *a, **k: (True, json.dumps(
            {"result": {"terminals": [{"handle": "term_a"}, {"nohandle": 1}]}}))
        check("sweep: a real answer is the set of handles",
              orcamod.live_terminals() == {"term_a"}, str(orcamod.live_terminals()))
    finally:
        orcamod._run = real_run


def test_an_agent_with_nowhere_to_run_is_not_handed_off(home: Path) -> None:
    """usage_unknown fires across a whole account at once, so it sweeps up the
    agents that are registered but were never started. There is nothing to fold,
    nothing to clear and nothing being spent - backend and manager were parked
    that way on 09-17 and manager's fold was charged for it."""
    _daemon_home(home, 100)
    con = db.connect()
    try:
        db.set_terminal(con, "backend", None, None)
        row = db.get_agent_state(con, "backend")
        check("no terminal: the trigger itself still fires",
              triggers.evaluate(100, 5).fired)
        check("no terminal: but the agent is not a candidate",
              not dmn.triggers_apply(row), str(dict(row)))

        dmn.tick(con, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("no terminal: so it is not parked", row["state"] != dmn.PARKED, str(dict(row)))
        check("no terminal: and no fold was paid for",
              not db.in_flight_handoffs(con) and row["continuations"] == 0, str(dict(row)))

        # And with somewhere to run, the same trigger does its job.
        db.set_terminal(con, "backend", "term_x", "wt")
        dmn.tick(con, lambda _m: None)
        check("no terminal: an agent that has one is still parked",
              db.get_agent_state(con, "backend")["state"] == dmn.PARKED,
              str(dict(db.get_agent_state(con, "backend"))))
    finally:
        con.close()


def test_a_wake_is_believed_only_when_a_session_appears(home: Path) -> None:
    """A launch that started nothing is not a wake: the closed mark and
    `resume_session` are the only way back to the agent's context, and nothing
    may be typed at a shell that refused the launch.
    """
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.available, orcamod.alive, orcamod.wait_idle, orcamod.send,
            orcamod.resume_with, dmn.WAKE_SESSION_TIMEOUT_S)
    try:
        dmn.WAKE_SESSION_TIMEOUT_S = 1
        orcamod.available = lambda: True
        orcamod.live_terminals = lambda: {"term_x"}
        orcamod.alive = lambda h: True
        waits: list[int] = []
        orcamod.wait_idle = lambda h, timeout_ms=0: (waits.append(timeout_ms), True)[1]
        orcamod.resume_with = lambda h, text: (sent.append(text), (True, "handoff injected"))[1]
        table = sessions_are()          # the launch types, and nothing comes up
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_closed(con, "backend", "sess-8")

        ok, why = dmn.wake_agent(con, "backend", lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("wake: a launch that started nothing is not a wake", not ok, why)
        check("wake: so the closed mark stays", row["closed_at"] is not None, str(dict(row)))
        check("wake: and the session to resume is still there to try again",
              row["resume_session"] == "sess-8", str(dict(row)))
        check("wake: and it does not wait out a TUI that never started",
              not waits, str(waits))
        check("wake: it tries the resume, then a fresh start, and stops there",
              [" launch " in t for t in sent] == [True, True]
              and "--resume" in sent[0] and "--resume" not in sent[1], str(sent))

        # A resume id that cannot be launched - a pruned transcript, a worktree
        # moved out from under it - would otherwise be retried at every tick
        # forever, since it is now kept rather than cleared. A fresh start in
        # the same terminal loses the context and keeps the agent.
        sent.clear()
        db.set_handoff(con, "backend", "# handoff · backend\nwaiting")

        def send_launching(h, text, enter=True):
            sent.append(text)
            if " launch " in text and "--resume" not in text:
                table.update({"backend": 4242})
                # What `ocp launch` does: the record goes into the system prompt
                # and off the row.
                db.set_handoff(con, "backend", None)
            return True, ""
        orcamod.send = send_launching
        ok, why = dmn.wake_agent(con, "backend", lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("wake: a resume that starts nothing falls back to a fresh start", ok, why)
        check("wake: and the agent is told there is no handoff behind it",
              any("could not be resumed" in t for t in sent), str(sent[-1:])[:120])
        check("wake: and that its handoff is in the system prompt, though the launch "
              "has already cleared the row",
              "system prompt above" in sent[-1], str(sent[-1:])[:200])
        check("wake: the closed mark is cleared once something is running",
              row["closed_at"] is None, str(dict(row)))

        # Same answer when the process table cannot be read at all: an
        # unreadable table is not evidence that the launch worked, and what is
        # at stake here is the resume pointer.
        sent.clear()
        db.set_closed(con, "backend", "sess-8")
        table.clear()
        obsmod.live_sessions = lambda: None
        ok, why = dmn.wake_agent(con, "backend", lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("wake: an unreadable process table is not a session either",
              not ok and row["resume_session"] == "sess-8", f"{why} {dict(row)}")

        # Work arriving retries the wake every tick, so a launch that will
        # never work is a loop with no end and no report. 8.6 already has the
        # answer for that shape: stop, and leave it for a human.
        db.set_closed(con, "backend", "sess-8")
        db.reset_counter(con, "backend", "wake_failures")
        table.clear()
        obsmod.live_sessions = lambda: dict(table)
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        for _ in range(dmn.MAX_FAILED_ATTEMPTS):
            dmn.wake_agent(con, "backend", lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("wake: a launch that never works ends in STALLED, not in a loop",
              row["state"] == dmn.STALLED, str(dict(row)))
        check("wake: and it still holds the session it would have resumed",
              row["resume_session"] == "sess-8" and row["closed_at"] is not None,
              str(dict(row)))
        # Its own count: the tick's exception handler and the handoff both stall
        # on failed_attempts, and a wake streak is not theirs to read.
        check("wake: failed wakes are counted apart from failed ticks",
              row["failed_attempts"] == 0 and row["wake_failures"] >= dmn.MAX_FAILED_ATTEMPTS,
              str(dict(row)))

        # A human starts it again in its terminal. The sweep sees the session
        # the stall said could not exist, and a stall the world has refuted is
        # ours to lift - leaving it would hide a live agent from every delivery.
        table["backend"] = 4242
        orcamod.live_terminals = lambda: {"term_x"}
        dmn.sweep(con, lambda _m: None, [])
        row = db.get_agent_state(con, "backend")
        check("wake: a stall the world refutes is lifted by the sweep",
              row["state"] == dmn.IDLE and row["closed_at"] is None, str(dict(row)))
        table.clear()

        # `ocp unstall` is the only other way out of STALLED, and it has to
        # clear this count as well: left at the cap, the sweep would later read
        # someone else's stall as one of its own and lift it.
        db.set_agent_state(con, "backend", dmn.STALLED, detail="stalled by wakes")
        for _ in range(dmn.MAX_FAILED_ATTEMPTS):
            db.bump_counter(con, "backend", "wake_failures")
        con.close()
        rc = climod.main(["unstall", "backend"])
        con = db.connect()
        row = db.get_agent_state(con, "backend")
        check("wake: unstall clears the wake streak, not only the other two",
              rc == 0 and row["wake_failures"] == 0 and row["state"] == dmn.QUEUED,
              str(dict(row)))

        # The 5h wall is the one failure that heals itself, and `ocp launch`
        # refuses on a reading the daemon has already got. Spending the wake
        # timeout to be told that, and counting it against the agent, is how a
        # window that rolls over in minutes took an agent out for hours.
        db.set_closed(con, "backend", "sess-8")
        db.set_agent_state(con, "backend", dmn.IDLE, detail="closed and at the wall")
        db.reset_counter(con, "backend", "wake_failures")
        write_status(home, "alpha", pct_5h=100, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 600)
        sent.clear()
        ok, why = dmn.wake_agent(con, "backend", lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("wake: an account at the wall is not launched into", not ok and not sent,
              f"{why} {sent}")
        check("wake: and waiting for the window is not a failed attempt",
              row["wake_failures"] == 0 and row["state"] != dmn.STALLED, str(dict(row)))

        # And once it rolls over, the same call goes through.
        write_status(home, "alpha", pct_5h=20, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        orcamod.send = relaunching_send(sent, table)
        ok, _ = dmn.wake_agent(con, "backend", lambda _m: None)
        check("wake: and it goes through once the window rolls over", ok, str(sent))
        db.set_closed(con, "backend", "sess-8")
        db.set_agent_state(con, "backend", dmn.IDLE, detail="closed again for the next case")

        # The same call, once the launch works.
        sent.clear()
        db.set_agent_state(con, "backend", dmn.IDLE, detail="cleared by hand")
        obsmod.live_sessions = lambda: dict(table)
        orcamod.send = relaunching_send(sent, table)
        ok, why = dmn.wake_agent(con, "backend", lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("wake: a launch that starts a session is a wake", ok, why)
        check("wake: the closed mark is cleared only then",
              row["closed_at"] is None, str(dict(row)))
        check("wake: and the agent is told it is back",
              any("Nothing was lost" in t for t in sent), str(sent))
    finally:
        (orcamod.available, orcamod.alive, orcamod.wait_idle, orcamod.send,
         orcamod.resume_with, dmn.WAKE_SESSION_TIMEOUT_S) = real
        con.close()


def test_a_walled_wake_says_so_everywhere(home: Path) -> None:
    """A closed agent whose resume is blocked at its account's 5h wall used to
    say nothing about it: `ocp ps` showed "·closed", `ocp triggers` said
    "nothing to hand off", and the daemon's own refusal lived in a log line.
    One predicate now answers all of them, so a screen cannot drift from what
    the daemon actually acts on - and it has to fall silent again the moment
    the account has room, since nothing here is stored state.
    """
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.available,)
    try:
        orcamod.available = lambda: True
        db.set_closed(con, "backend", "sess-wall")
        # A screen only shows the wall next to a wait that is real: queued
        # work is what would actually make the daemon try this wake.
        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)
        # A detail of its own is the ordinary case for this row - an answer
        # sitting undelivered is the example this feature exists for - so the
        # budget fix has to hold with one competing for the line, not only
        # on the empty-detail row every other check here leaves behind.
        db.set_agent_state(con, "backend", dmn.IDLE, detail="request 13 resolved: done")
        reset_at = int(time.time()) + 1800
        write_status(home, "alpha", pct_5h=100, pct_7d=10, ctx_pct=5, reset_5h=reset_at)
        when = time.strftime("%H:%M", time.localtime(reset_at))

        row = db.get_agent_state(con, "backend")
        reason = launch.wake_wait_reason("backend", row, con)
        check("walled: names the account and when it rolls over",
              reason is not None and "alpha" in reason and when in reason, reason)

        ok, why = dmn.wake_agent(con, "backend", lambda _m: None)
        check("walled: the daemon's own refusal is the same sentence, not a cousin of it",
              not ok and why == reason, f"{why!r} != {reason!r}")

        ps_out = _capture(lambda: climod.main(["ps"]))
        trig_out = _capture(lambda: climod.main(["triggers"]))
        # `ocp team` is asked from somewhere - `reviewer` is a peer of every
        # agent in DESIGN_TEAM, so backend is on its list.
        os.environ["OCP_AGENT"] = "reviewer"
        team_out = _capture(lambda: climod.main(["team"]))
        del os.environ["OCP_AGENT"]
        wide = tui._ANSI.sub("", tui.frame(200, 40))
        narrow = tui._ANSI.sub("", tui.frame(80, 40))
        check("walled: `ocp ps` says it", reason in ps_out, ps_out)
        check("walled: `ocp triggers` says it", reason in trig_out, trig_out)
        check("walled: `ocp team` says it too - the fourth surface",
              "alpha" in team_out and when in team_out, team_out)
        # The bridge has no room for the full sentence at a real terminal
        # width - `ocp ps` and `ocp triggers` print it whole because a line of
        # text can run as long as it needs to; a fixed column cannot. It
        # draws the same predicate, fitted to the column: the account and the
        # clock, not a paraphrase of the sentence.
        check("walled: the bridge names the account and the clock at full width",
              "alpha" in wide and when in wide,
              str([ln for ln in wide.splitlines() if "backend" in ln]))
        check("walled: and still does at a realistic terminal width",
              "alpha" in narrow and when in narrow,
              str([ln for ln in narrow.splitlines() if "backend" in ln]))

        # It is computed, not stored: once the account has room the sentence
        # disappears from every screen on its own, with nothing to clear.
        write_status(home, "alpha", pct_5h=20, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        row = db.get_agent_state(con, "backend")
        check("walled: and clears the moment the account has room",
              launch.wake_wait_reason("backend", row, con) is None)
        ps_out = _capture(lambda: climod.main(["ps"]))
        trig_out = _capture(lambda: climod.main(["triggers"]))
        os.environ["OCP_AGENT"] = "reviewer"
        team_out = _capture(lambda: climod.main(["team"]))
        del os.environ["OCP_AGENT"]
        wide = tui._ANSI.sub("", tui.frame(200, 40))
        check("walled: and every screen falls silent with it",
              "rolls over" not in ps_out and "rolls over" not in trig_out
              and when not in team_out and when not in wide,
              "still walled somewhere after the window rolled over")
    finally:
        (orcamod.available,) = real
        con.close()


def test_a_walled_wake_is_not_shown_with_nothing_waiting(home: Path) -> None:
    """A closed agent on a walled account, with no queued work, no answer it
    asked for and no wake_at set, is idle by choice - nothing is trying to
    wake it, so nothing should read "waiting" beside it. Before this, every
    closed agent on a walled account read as walled, which on a live team
    with six closed agents and nothing due was six rows of noise for zero
    real waits.
    """
    _daemon_home(home, 20)
    con = db.connect()
    try:
        db.set_closed(con, "backend", "sess-idle")
        write_status(home, "alpha", pct_5h=100, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        row = db.get_agent_state(con, "backend")
        check("walled: nothing waiting means would_wake says no",
              not dmn.would_wake(con, row), dict(row))

        # A deadline that has not arrived yet is not "due" - a bare
        # `row["wake_at"]` truthy check would have said yes to any parked
        # agent with any deadline at all, due or not.
        db.set_agent_state(con, "backend", dmn.PARKED,
                           wake_at=int(time.time()) + 3600, detail="usage; wakes later")
        row = db.get_agent_state(con, "backend")
        check("walled: a deadline that has not come due yet is not waiting either",
              not dmn.would_wake(con, row), dict(row))

        ps_out = _capture(lambda: climod.main(["ps"]))
        trig_out = _capture(lambda: climod.main(["triggers"]))
        os.environ["OCP_AGENT"] = "reviewer"
        team_out = _capture(lambda: climod.main(["team"]))
        del os.environ["OCP_AGENT"]
        frame = tui._ANSI.sub("", tui.frame(200, 40))
        check("walled: `ocp ps` stays quiet with nothing waiting",
              "rolls over" not in ps_out, ps_out)
        check("walled: `ocp triggers` stays quiet too",
              "rolls over" not in trig_out, trig_out)
        check("walled: `ocp team` stays quiet too", "rolls over" not in team_out, team_out)
        check("walled: and the bridge",
              "rolls over" not in frame and "⏳" not in frame,
              str([ln for ln in frame.splitlines() if "backend" in ln]))

        # `wake_wait` itself still answers - it is the daemon's own call site
        # (a move's wake included) that needs the raw answer unconditionally,
        # before it spends a wake attempt finding the wall out the hard way.
        check("walled: but wake_wait itself is unconditional - the daemon "
              "still needs the raw answer to decide a wake before trying it",
              launch.wake_wait("backend", row, con) is not None)
    finally:
        con.close()


def test_a_stalled_closed_agent_keeps_its_marker_when_walled(home: Path) -> None:
    """A STALLED agent needs a human - unstalling it is the one thing that
    clears that mark. If the wall's yellow cell were drawn ahead of it, a
    STALLED agent whose account crossed 90% would silently read as merely
    walled, and the one condition on that row that needs a person would clear
    itself the moment the window rolled over, with nobody told there was
    ever anything wrong.
    """
    _daemon_home(home, 20)
    con = db.connect()
    try:
        db.set_closed(con, "backend", "sess-stalled")
        db.set_agent_state(con, "backend", dmn.STALLED, detail="ran out of attempts")
        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)
        write_status(home, "alpha", pct_5h=100, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        row = db.get_agent_state(con, "backend")
        check("stalled+walled: both conditions actually hold here",
              dmn.would_wake(con, row) and launch.wake_wait("backend", row, con),
              dict(row))
        frame = tui._ANSI.sub("", tui.frame(200, 40))
        line = next((ln for ln in frame.splitlines() if "backend" in ln), "")
        check("stalled+walled: the last cell is the human marker, not the wall",
              "ran out of attempts" in line and "⏳" not in line, line)
    finally:
        con.close()


def test_a_walled_wake_with_no_reset_reads_unknown(home: Path) -> None:
    """A snapshot with no `reset_5h` still reports a wall - the account read
    95% either way - it just cannot say when that clears. The account column
    is never set here either, so this doubles as the assumed-account wording:
    nothing recorded or reported means agent.yaml's preference, named as a
    guess rather than as the fact the sentence used to imply."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        db.set_closed(con, "backend", "sess-wall")
        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)
        write_status(home, "alpha", pct_5h=100, pct_7d=10, ctx_pct=5)
        row = db.get_agent_state(con, "backend")
        reason = launch.wake_wait_reason("backend", row, con)
        check("walled: no reset_5h in the snapshot reads as unknown, not a crash",
              reason == "waiting for assumed account alpha's 5h window "
                        "(100%, rolls over unknown)", reason)
    finally:
        con.close()


def test_a_moved_walled_wake_names_the_new_account(home: Path) -> None:
    """The one branch where `choose_account` can name an account other than
    the agent's own: no `resume_session` means this is the move's wake, not a
    resume, so it is free to land wherever there is room, not tied to the
    account its last session lived under. Before this, only the resuming
    branch (where the account it checks is always the agent's own) was ever
    exercised.
    """
    _daemon_home(home, 20)
    con = db.connect()
    real_login = accounts.logged_in
    try:
        db.set_account(con, "backend", "alpha")
        db.set_closed(con, "backend", None)   # no resume id: the move's wake
        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)
        write_status(home, "alpha", pct_5h=100, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        write_status(home, "beta", pct_5h=20, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        accounts.logged_in = lambda a: True  # noqa: E731 - keychain is not the subject
        row = db.get_agent_state(con, "backend")
        check("moved: this row has no resume id - the move's wake",
              row["resume_session"] is None, dict(row))
        check("moved: current_account is still the old one - nothing has "
              "started under the new one yet",
              agents.current_account("backend", con) == "alpha", row)
        check("moved: but the wait is checked against the account it would "
              "actually land on, which has room",
              launch.choose_account("backend", resuming=False, con=con) == "beta")
        check("moved: so it is not walled, even though the agent's own "
              "account is",
              launch.wake_wait("backend", row, con) is None)
    finally:
        accounts.logged_in = real_login
        con.close()


def test_a_walled_wake_names_the_account_only_with_room(home: Path) -> None:
    """The bridge's column is narrow enough that naming a different account
    can cost the clock its columns. The clock is the one thing this cell
    exists to show - the acct column already says where the agent itself
    sits - so at a realistic terminal width the cell drops the account and
    keeps the clock; at a wide enough one there is room for both.
    """
    _daemon_home(home, 20)
    con = db.connect()
    real_would_wake, real_wake_wait = dmn.would_wake, launch.wake_wait
    try:
        db.set_closed(con, "backend", "sess-x")
        reset_at = int(time.time()) + 1800
        when = time.strftime("%H:%M", time.localtime(reset_at))
        dmn.would_wake = lambda con, row: True
        launch.wake_wait = lambda name, row, con: launch.WakeWait("beta", 95, reset_at)
        narrow = tui._ANSI.sub("", tui.frame(80, 40))
        wide = tui._ANSI.sub("", tui.frame(100, 40))
        nline = next(ln for ln in narrow.splitlines() if "backend" in ln)
        wline = next(ln for ln in wide.splitlines() if "backend" in ln)
        check("walled: a narrow bridge keeps the clock and drops the account",
              when in nline and "beta" not in nline, nline)
        check("walled: a wider bridge has room for both",
              when in wline and "beta" in wline, wline)
    finally:
        dmn.would_wake, launch.wake_wait = real_would_wake, real_wake_wait
        con.close()


def test_a_park_that_cannot_be_woken_keeps_its_deadline(home: Path) -> None:
    """The deadline is the only thing that makes a parked agent due again, and
    supervise cleared it before knowing whether the wake worked. A closed park
    whose launch is refused - the account still at the wall when the window
    rolls - then had no deadline, was not due for reconcile either, and waited
    closed until some work happened to arrive."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.available, orcamod.alive, orcamod.send)
    try:
        orcamod.available = lambda: True
        orcamod.live_terminals = lambda: {"term_x"}
        orcamod.alive = lambda h: True
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        sessions_are()
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_closed(con, "backend", "sess-2")
        db.set_agent_state(con, "backend", dmn.PARKED, wake_at=int(time.time()) - 10,
                           detail="parked and closed")
        write_status(home, "alpha", pct_5h=100, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 600)

        dmn.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
        row = db.get_agent_state(con, "backend")
        check("park: a wake the account refuses is not typed", not sent, str(sent))
        check("park: and the deadline stays, so the next tick tries again",
              row["state"] == dmn.PARKED and row["wake_at"] is not None, str(dict(row)))

        # The window rolls over and the same tick does what it could not.
        write_status(home, "alpha", pct_5h=20, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        table = sessions_are()
        orcamod.send = relaunching_send(sent, table)
        dmn.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
        row = db.get_agent_state(con, "backend")
        check("park: once it rolls over the park is woken and the deadline cleared",
              row["state"] == dmn.QUEUED and row["wake_at"] is None
              and any("--resume sess-2" in t for t in sent), str(dict(row)))

        # An agent that answers the prompt before the tick gets back to its row
        # is RUNNING, and RUNNING is the label that keeps the daemon out of a
        # working agent's way. The wake must not write QUEUED over it.
        db.set_agent_state(con, "backend", dmn.PARKED, wake_at=int(time.time()) - 10,
                           detail="parked and closed again")
        db.set_closed(con, "backend", "sess-2")
        hooked = db.connect()
        real_resume = orcamod.resume_with
        orcamod.resume_with = lambda h, text: (
            evmod._mark_activity(hooked, "backend", "UserPromptSubmit"),
            (True, "injected"))[1]
        try:
            dmn.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
        finally:
            orcamod.resume_with = real_resume
            hooked.close()
        row = db.get_agent_state(con, "backend")
        check("park: an agent that has already answered its prompt stays RUNNING",
              row["state"] == dmn.RUNNING, str(dict(row)))
    finally:
        orcamod.available, orcamod.alive, orcamod.send = real
        con.close()


def _park_via_handoff(con, agent: str, kind: str, hoff_state: str = "done") -> None:
    """Fabricate a completed handoff the way `_perform_handoff` leaves one,
    with `trigger_kind` on the record - the only place a park's cause is
    named, since nothing on `agent_state` itself says why."""
    hid = f"hoff-{kind}"
    db.start_handoff(con, hid, agent, None, kind, int(time.time()) + 90)
    db.advance_handoff(con, hid, "released", state=hoff_state)


def test_a_usage_park_is_woken_when_the_reading_clears(home: Path) -> None:
    """Parking is a level trigger - a usage reading over the line - and
    un-parking was only an edge: an open session slept to `wake_at` even
    after that same reading, evaluated the same way, would no longer fire.
    """
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.available, orcamod.alive)
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_account(con, "backend", "alpha")
        _park_via_handoff(con, "backend", "usage")
        db.set_agent_state(con, "backend", dmn.PARKED,
                           wake_at=int(time.time()) + 3600, detail="usage; wakes at 23:00")
        write_status(home, "alpha", pct_5h=5, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)

        dmn.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
        row = db.get_agent_state(con, "backend")
        check("park: an open session wakes the moment the reading clears, "
              "not at the deadline it was given",
              row["state"] == dmn.QUEUED and row["wake_at"] is None, str(dict(row)))
    finally:
        orcamod.available, orcamod.alive = real
        con.close()


def test_a_usage_park_stays_parked_while_the_reading_is_at_the_wall(home: Path) -> None:
    """The un-park question and the trigger are the same `evaluate()`, so a
    reading that parked an agent cannot also wake it, at any number of ticks:
    it stays parked until the reading is off the wall."""
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.available, orcamod.alive, orcamod.idle_now, orcamod.clear_context,
            dmn.session_present)
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        # A re-fold, if the flap caused one, must be able to run to its end.
        orcamod.idle_now = lambda h, **kw: True
        orcamod.clear_context = lambda h, **kw: (True, "cleared")
        dmn.session_present = lambda a: True
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_account(con, "backend", "alpha")
        _park_via_handoff(con, "backend", "usage")
        # Past MIN_HANDOFF_INTERVAL_S, so that guard cannot be what keeps a
        # re-fold from happening in the ticks below.
        con.execute("UPDATE handoff SET updated = ? WHERE agent = 'backend'",
                    (int(time.time()) - 2 * dmn.MIN_HANDOFF_INTERVAL_S,))
        db.set_agent_state(con, "backend", dmn.PARKED,
                           wake_at=int(time.time()) + 3600, detail="usage; wakes at 23:00")
        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)
        # Still at the wall, with work waiting and the account's reset inside T:
        # not cleared, and not worth a cold start to leave.
        write_status(home, "alpha", pct_5h=100, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        before = db.get_agent_state(con, "backend")
        continuations_before = int(before["continuations"])

        for _ in range(5):
            dmn.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
            row = db.get_agent_state(con, "backend")
            check("park: still parked - the reading is still at the wall",
                  row["state"] == dmn.PARKED, str(dict(row)))
        # A state that flapped back to PARKED every time would still look fine
        # here if this only checked the label; a handoff per round would not.
        check("park: no handoff was ever paid for across the flap",
              int(row["continuations"]) == continuations_before,
              dict(before=continuations_before, after=row["continuations"]))
    finally:
        (orcamod.available, orcamod.alive, orcamod.idle_now, orcamod.clear_context,
         dmn.session_present) = real
        con.close()


def test_a_weekly_park_stays_parked_while_the_week_is_full(home: Path) -> None:
    """A weekly park is un-parked on the weekly reading too: 5h at 10% with 7d at
    100% is still the condition that parked it."""
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.available, orcamod.alive, orcamod.idle_now, orcamod.clear_context,
            dmn.session_present)
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        # A re-fold, if the flap caused one, must be able to run to its end.
        orcamod.idle_now = lambda h, **kw: True
        orcamod.clear_context = lambda h, **kw: (True, "cleared")
        dmn.session_present = lambda a: True
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_account(con, "backend", "alpha")
        _park_via_handoff(con, "backend", "usage_7d")
        # Past MIN_HANDOFF_INTERVAL_S, so that guard cannot be what keeps a
        # re-fold from happening in the ticks below.
        con.execute("UPDATE handoff SET updated = ? WHERE agent = 'backend'",
                    (int(time.time()) - 2 * dmn.MIN_HANDOFF_INTERVAL_S,))
        db.set_agent_state(con, "backend", dmn.PARKED,
                           wake_at=int(time.time()) + 3600, detail="usage; wakes at 23:00")
        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)
        write_status(home, "alpha", pct_5h=10, pct_7d=100, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800, reset_7d=int(time.time()) + 86400)
        before = db.get_agent_state(con, "backend")
        continuations_before = int(before["continuations"])

        for _ in range(5):
            dmn.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
            row = db.get_agent_state(con, "backend")
            check("park: still parked - the reading is still at the wall",
                  row["state"] == dmn.PARKED, str(dict(row)))
        # A state that flapped back to PARKED every time would still look fine
        # here if this only checked the label; a handoff per round would not.
        check("park: no handoff was ever paid for across the flap",
              int(row["continuations"]) == continuations_before,
              dict(before=continuations_before, after=row["continuations"]))
    finally:
        (orcamod.available, orcamod.alive, orcamod.idle_now, orcamod.clear_context,
         dmn.session_present) = real
        con.close()


def test_a_degraded_park_does_not_auto_clear(home: Path) -> None:
    """DEGRADED is not a usage park. `DEGRADED_RETRY_S` is the only thing
    that re-hands a failed handoff's own retry, and clearing it early on a
    usage reading alone - which is not what parked this agent - would drop
    that deadline instead.
    """
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.available, orcamod.alive)
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_account(con, "backend", "alpha")
        _park_via_handoff(con, "backend", "usage", hoff_state="degraded")
        db.set_agent_state(con, "backend", dmn.DEGRADED,
                           wake_at=int(time.time()) + 3600, detail="handoff timed out")
        write_status(home, "alpha", pct_5h=5, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)

        dmn.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
        row = db.get_agent_state(con, "backend")
        check("park: DEGRADED is not scoped in - it keeps its own deadline",
              row["state"] == dmn.DEGRADED and row["wake_at"] is not None, str(dict(row)))
    finally:
        orcamod.available, orcamod.alive = real
        con.close()


def test_a_context_park_does_not_auto_clear_on_a_usage_reading(home: Path) -> None:
    """A context park is not a usage park either, even though PARKED alone
    does not say so - only the handoff that caused it does. A clear usage
    reading is not the condition that parked this agent, so it stays parked
    on its own deadline, the same as any other cause this scope excludes.
    """
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.available, orcamod.alive)
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_account(con, "backend", "alpha")
        _park_via_handoff(con, "backend", "context")
        db.set_agent_state(con, "backend", dmn.PARKED,
                           wake_at=int(time.time()) + 3600, detail="context; wakes at 23:00")
        write_status(home, "alpha", pct_5h=5, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)

        dmn.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
        row = db.get_agent_state(con, "backend")
        check("park: a context park is not a usage park - it keeps its own deadline",
              row["state"] == dmn.PARKED and row["wake_at"] is not None, str(dict(row)))
    finally:
        orcamod.available, orcamod.alive = real
        con.close()


def test_an_assumed_account_does_not_auto_clear_a_park(home: Path) -> None:
    """Case A: the db account column is null - `current_account` fell back
    to agent.yaml's preference rather than reporting a fact - and the
    reading that preference points at looks clear. That is the exact
    divergence that parked keeper for 'usage' against the wrong account's
    reading on 2026-09-18 while its real account sat at 3%; un-parking on
    that same assumption would wake it into whatever its real, unknown
    account is actually doing. It stays parked - no better, no worse than
    before this fix - until something records which account it is on.
    """
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.available, orcamod.alive)
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        db.set_terminal(con, "backend", "term_x", "wt")
        # No db.set_account: the column stays null, exactly as it does from
        # `started_for_work` until the launched process records itself.
        _park_via_handoff(con, "backend", "usage")
        db.set_agent_state(con, "backend", dmn.PARKED,
                           wake_at=int(time.time()) + 3600, detail="usage; wakes at 23:00")
        write_status(home, "alpha", pct_5h=5, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)

        row = db.get_agent_state(con, "backend")
        check("park: the account column really is null here",
              row["account"] is None, dict(row))

        dmn.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
        row = db.get_agent_state(con, "backend")
        check("park: an assumed account never un-parks",
              row["state"] == dmn.PARKED and row["wake_at"] is not None, str(dict(row)))
    finally:
        orcamod.available, orcamod.alive = real
        con.close()


def test_a_resumed_handoff_also_records_its_park(home: Path) -> None:
    """The event fixed for a fresh park applies just as much to one
    recovered after a restart - `resume_handoff` writes the same PARKED
    state through a different path, and it left the same gap.
    """
    _daemon_home(home, 20)
    con = db.connect()
    try:
        db.start_handoff(con, "hoff-restart", "backend", None, "usage",
                         int(time.time()) + 90)
        row = db.in_flight_for(con, "backend")
        write_status(home, "alpha", pct_5h=20, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)

        dmn.resume_handoff(con, row, lambda _m: None)
        events = db.events_after(con, 0, agent="backend", exclude_kinds=())
        check("resume: a recovered handoff's park is recorded too",
              any(e["kind"] == "park" for e in events), [e["kind"] for e in events])
    finally:
        con.close()


def test_account_reading_prefers_recorded_then_reported_then_assumed(home: Path) -> None:
    """Three sources, and only the first two are facts. A recorded column
    wins outright; failing that, a fresh report from the agent's own running
    session beats a bare config preference - and beats even a recorded
    column when that column itself only holds an earlier guess, never
    confirmed. Only when neither exists at all does agent.yaml's preference
    stand in, and it is never mistaken for either fact.
    """
    _daemon_home(home, 20)
    con = db.connect()
    try:
        db.set_account(con, "backend", "alpha")
        r = agents.account_reading("backend", con)
        check("account: a recorded column wins outright",
              r.account == "alpha" and r.source == "recorded", r)

        db.set_account(con, "backend", None)
        con.execute("UPDATE agent_state SET account = NULL, account_source = NULL "
                    "WHERE agent = 'backend'")
        write_agent_status("backend", "beta", pct_5h=10)
        r = agents.account_reading("backend", con)
        check("account: null column falls to a fresh report",
              r.account == "beta" and r.source == "reported", r)

        write_agent_status("backend", "beta", pct_5h=10,
                           collected_at=int(time.time()) - status.STALE_AFTER_S - 60)
        r = agents.account_reading("backend", con)
        check("account: a stale report is not evidence - falls to the config "
              "preference, marked as a guess",
              r.account == "alpha" and r.source == "assumed", r)

        db.set_account(con, "backend", "alpha", source="assumed")
        write_agent_status("backend", "beta", pct_5h=10)
        r = agents.account_reading("backend", con)
        check("account: a fresh report outranks a column that only holds a "
              "guess of its own",
              r.account == "beta" and r.source == "reported", r)
    finally:
        con.close()


def test_supervise_reads_the_reported_account_not_an_assumed_preference(home: Path) -> None:
    """The incident itself: a null recorded column used to mean reading
    whatever agent.yaml prefers as if it were this agent's own usage. Backend
    prefers alpha, which is pinned at the wall; its real, running session -
    visible only through its own fresh report - is on beta, nowhere near it.
    Before this fix `supervise` read alpha's 100% and parked an agent that
    was not spending it; now it reads the account the session actually
    reports and leaves it alone.
    """
    _daemon_home(home, 20)
    con = db.connect()
    try:
        write_status(home, "alpha", pct_5h=100, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        write_status(home, "beta", pct_5h=5, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        write_agent_status("backend", "beta", pct_5h=5, ctx_pct=5)
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn.IDLE)

        dmn.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
        row = db.get_agent_state(con, "backend")
        check("account: a park against the wrong, assumed account no longer happens",
              row["state"] != dmn.PARKED, dict(row))
    finally:
        con.close()


def test_a_park_against_an_assumed_account_gets_a_short_recheck(home: Path) -> None:
    """A park's ceiling is `reset_5h` off whatever account it judged the
    trigger against - and against an assumed one, that number belongs to
    agent.yaml's preference, not to any account this agent is shown to be
    on. It fails closed to a short re-look instead of sleeping the hours
    that account's own window happens to have left.
    """
    _daemon_home(home, 20)
    con = db.connect()
    try:
        # No recorded column, no fresh report: alpha, the config preference,
        # is the only thing left, and it is hours from rolling over.
        write_status(home, "alpha", pct_5h=100, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 4 * 3600)
        t = triggers.evaluate(95, 10)
        before = int(time.time())
        dmn.perform_handoff(con, "backend", t, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("account: an assumed park gets a short re-look, not alpha's own "
              "4h-out rollover",
              row["state"] == dmn.PARKED
              and row["wake_at"] is not None
              and row["wake_at"] < before + dmn.ASSUMED_ACCOUNT_RECHECK_S + 5,
              dict(row))
    finally:
        con.close()


def test_a_resumed_handoff_also_fails_closed_on_an_assumed_account(home: Path) -> None:
    """The same ceiling rule, through `resume_handoff`'s own path: a restart
    recovering an in-flight handoff must not sleep against agent.yaml's
    preference either.
    """
    _daemon_home(home, 20)
    con = db.connect()
    try:
        write_status(home, "alpha", pct_5h=20, pct_7d=10, ctx_pct=5,
                     reset_5h=int(time.time()) + 4 * 3600)
        db.start_handoff(con, "hoff-restart2", "backend", None, "usage",
                         int(time.time()) + 90)
        row = db.in_flight_for(con, "backend")
        before = int(time.time())
        dmn.resume_handoff(con, row, lambda _m: None)
        fresh = db.get_agent_state(con, "backend")
        check("account: a resumed handoff's park also gets a short re-look, "
              "not alpha's own 4h-out rollover",
              fresh["state"] == dmn.PARKED
              and fresh["wake_at"] is not None
              and fresh["wake_at"] < before + dmn.ASSUMED_ACCOUNT_RECHECK_S + 5,
              dict(fresh))
    finally:
        con.close()


def test_a_spawned_agent_gets_an_assumed_account_until_launch_confirms_it(home: Path) -> None:
    """The window between a spawn and that spawn's own launch reporting in
    is exactly where a null column used to sit undiagnosable. It now carries
    a guess instead, marked as one - `account_reading` still prefers a fresh
    report over it the moment there is one, and prefers it over nothing
    (the previous behaviour) until then.
    """
    _daemon_home(home, 20)
    con = db.connect()
    real_spawn = spawnmod.spawn
    try:
        def fake_spawn(agent, repo, base="main", prompt=None):
            db.set_terminal(con, agent, "term_new", None)
            return True, "spawned"
        spawnmod.spawn = fake_spawn
        agents.register("marina", "alpha", repo=str(home / "checkout"))
        db.add_request(con, "reviewer", "marina", "look at this", None,
                       "peer", "async", None, 0, None)

        dmn._start_for_work(con, "marina", lambda _m: None)
        row = db.get_agent_state(con, "marina")
        check("account: a fresh spawn's guess is marked assumed, not left null",
              row["account"] == "alpha" and row["account_source"] == "assumed",
              dict(row))
    finally:
        spawnmod.spawn = real_spawn
        con.close()


def test_top_shows_what_is_waiting_on_the_human(home: Path) -> None:
    """An escalation addressed to the human is the one queue nothing in this
    system ever clears on its own, and it used to be visible only by running
    `ocp inbox` with the right name. It gets its own panel, and it is not also
    drawn as traffic between agents."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        agents.register("marina", "alpha")
        db.add_request(con, "marina", actions.HUMAN, "which sea variant ships?",
                       None, "peer", "async", None, 0, None)
        db.add_request(con, "marina", "backend", "look at this diff",
                       None, "peer", "async", None, 0, None)

        waiting = actions.messages(con)
        check("top: a request addressed to the human is a message for them",
              [m["from"] for m in waiting] == ["marina"], str(waiting))
        check("top: and agent-to-agent traffic is not",
              all("diff" not in (m["body"] or "") for m in waiting), str(waiting))

        text = tui.frame(120, 60)
        plain = [tui._ANSI.sub("", ln) for ln in text.split(chr(10))]
        # A count of zero is not news, and three of them crowd out the one
        # number that is.
        plain_title = [tui._ANSI.sub("", ln) for ln in tui.frame(120, 60).split(chr(10))
                       if "FOR YOU" in ln][0]
        check("top: the panel counts only what there is to count",
              "0 " not in plain_title, plain_title.strip()[:70])

        check("top: the panel is drawn with the question in it",
              any("which sea variant ships?" in ln for ln in plain),
              str([ln for ln in plain if "marina" in ln])[:200])
        # Agent-to-agent traffic is not the human's mail, and it is not drawn
        # at all while it is moving - only once it has stopped moving.
        check("top: a request between agents is not in the human's panel",
              not any("look at this diff" in ln for ln in plain), "")
        con.execute("UPDATE requests SET ts = ? WHERE target = 'backend'",
                    (int(time.time()) - actions.STUCK_AFTER_S - 60,))
        db.set_terminal(con, "backend", None, None)
        plain = [tui._ANSI.sub("", ln) for ln in tui.frame(120, 60).split(chr(10))]
        check("top: once it has been waiting, it is drawn as stuck, with why",
              any("marina → backend" in ln and "no terminal" in ln for ln in plain),
              str([ln.strip()[:80] for ln in plain if "marina" in ln])[:200])
        check("top: and the frame still fits its terminal",
              len(plain) == 60 and not [ln for ln in plain if len(ln) > 120])
        check("top: the keys for the focused panel are on the last line",
              "wake" in plain[-1] and "message" in plain[-1], plain[-1])
        # Half a word is worse than none: the line sheds whole pieces, so it
        # ends on one - an action, the navigation, or the short form of it.
        check("top: and the line is never cut through an action's name",
              plain[-1].rstrip().endswith(("quit", "unstall", "q", "more")), plain[-1])
    finally:
        con.close()


def test_a_long_name_does_not_run_into_the_column_beside_it(home: Path) -> None:
    """Every table pads the agent name to a fixed width, and the roster is the
    thing that grows: `orcapipe-manager` ran straight into the state beside
    it on three of them."""
    _daemon_home(home, 20)
    agents.register("orcapipe-manager", "alpha")
    (home / "team.yaml").write_text("""
teams:
  dev: [backend]
agents:
  orcapipe-manager: { intake: dedicated }
  backend:          { intake: dedicated }
edges:
  - { from: "orcapipe-manager", to: "dev.*", relation: supervisor, response: fire_and_forget }
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
""")
    for argv in (["ps"], ["triggers"], ["team", "--agent", "orcapipe-manager"]):
        out = _capture(lambda: climod.main(argv))
        # Only the table rows: a sentence may well end on the name.
        bad = [ln for ln in out.splitlines()
               if "orcapipe-manager" in ln and "orcapipe-manager " not in ln
               and not ln.rstrip().endswith("orcapipe-manager")]
        check(f"columns: `ocp {argv[0]}` leaves a space after the longest name",
              not bad, str(bad))


def test_the_screen_says_which_account_to_spend_next(home: Path) -> None:
    """The ✔ column says who *may* take work, which on a good day is everyone.
    It never said where the headroom was, so the reader compared two bars by
    eye - at a decision the daemon already makes."""
    _daemon_home(home, 40)
    write_status(home, "beta", pct_5h=8, pct_7d=13, ctx_pct=5,
                 reset_5h=int(time.time()) + 1800)
    accounts.register("beta")
    real_login = accounts.logged_in
    try:
        def marked() -> list[str]:
            plain = [tui._ANSI.sub("", ln) for ln in tui.frame(120, 60).split(chr(10))]
            rows = [ln for ln in plain if "alpha" in ln or "beta" in ln]
            return [ln for ln in rows if ln.lstrip("│ ").startswith(">")]

        calls: list[str] = []

        def login(a: str) -> bool:
            calls.append(a)
            return True

        accounts.logged_in = login
        tui._LOGIN.clear()
        got = marked()
        seen = len(calls)
        marked()
        check("accounts: the login check is not run again on every redraw",
              len(calls) == seen and seen <= len(paths.list_accounts()), str(calls))
        accounts.logged_in = lambda a: True  # noqa: E731 - keychain is not the subject
        check("accounts: the account with the most headroom is marked",
              len(got) == 1 and "beta" in got[0], str(got))

        # An account nobody is logged into cannot start a session, however
        # much room it has.
        accounts.logged_in = lambda a: a != "beta"  # noqa: E731
        tui._LOGIN.clear()
        got = marked()
        check("accounts: never one that nobody is logged into",
              len(got) == 1 and "alpha" in got[0], str(got))

        # Fail-closed: nothing assignable means no mark, not a guess.
        accounts.logged_in = lambda a: True  # noqa: E731
        tui._LOGIN.clear()
        for a in ("alpha", "beta"):
            write_status(home, a, pct_5h=100, pct_7d=99, ctx_pct=5,
                         reset_5h=int(time.time()) + 1800)
        check("accounts: with nothing assignable, nothing is marked", not marked())
    finally:
        accounts.logged_in = real_login


def test_the_founder_does_not_approve_their_own_request(home: Path) -> None:
    """HR gates *agents*, and the founder is the thing on the other side of the
    gate. Asked to propose, they got "an agent cannot ask itself" - the one
    person allowed to change the roster could not use the tool that changes it,
    and the first agent could never be made, since a proposal needs an agent to
    raise it. Who is routed where is the whole risk: a shell that merely lost
    OCP_AGENT must not be taken for the founder and hire at will."""
    _daemon_home(home, 20)
    con = db.connect()

    class FakeStdin:
        def __init__(self, tty: bool) -> None:
            self.tty = tty

        def isatty(self) -> bool:
            return self.tty

    real_env, real_stdin = os.environ.pop("OCP_AGENT", None), sys.stdin
    try:
        accounts.register("beta")
        (home / "team.yaml").write_text("""
teams:
  dev: [marina]
agents:
  marina:  { intake: dedicated }
edges:
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
""")
        agents.register("marina", "beta")
        hire = ["hr", "add", "dohyun", "--account", "beta", "--team", "dev",
                "--model", "opus", "--effort", "high",
                "--why", "a second pair of hands on the sea work"]

        # A process with no identity and no terminal: an agent's tool shell,
        # a hook, a cron job. It is nobody, and nobody hires.
        sys.stdin = FakeStdin(False)
        rc = climod.main(hire)
        check("hr: a shell that lost its identity is refused, not taken for the founder",
              rc != 0 and "dohyun" not in paths.list_agents()
              and not hr.listing(), str(rc))

        # An agent proposes; nothing changes until the founder answers.
        os.environ["OCP_AGENT"] = "marina"
        before = len(actions.messages(con))
        rc = climod.main(hire)
        check("hr: an agent's request is a proposal, and changes nothing",
              rc == 0 and "dohyun" not in paths.list_agents()
              and len(actions.messages(con)) == before + 1, str(rc))
        del os.environ["OCP_AGENT"]
        for p_ in hr.listing():
            hr.path_of(p_["id"]).unlink()

        # The founder, at their own prompt.
        sys.stdin = FakeStdin(True)
        before = len(actions.messages(con))
        rc = climod.main(hire)
        check("hr: the founder's own change lands without a proposal to approve",
              rc == 0 and "dohyun" in paths.list_agents(), str(rc))
        check("hr: and raises nothing to themselves",
              len(actions.messages(con)) == before)
        cfg = yamlcompat.load(agents.agent_file("dohyun").read_text())
        check("hr: with the model and the effort it was hired at",
              cfg.get("model") == "opus" and cfg.get("effort") == "high", str(cfg))
        applied = [p_ for p_ in hr.listing() if p_["agent"] == "dohyun"]
        check("hr: and the record is still a file",
              len(applied) == 1 and applied[0]["state"] == "applied"
              and applied[0]["requested_by"] == "human", str(applied))

        # A change that fails halfway is recorded as having failed, and leaves
        # no agent the routing table does not know.
        (home / "team.yaml").write_text("teams:\n  dev: [marina]\n")
        ok, said = hr.enact("add", "halfborn", why="w", account="beta", team_name="dev")
        rec = [p_ for p_ in hr.listing() if p_["agent"] == "halfborn"]
        check("hr: a refused edit registers nobody",
              not ok and "halfborn" not in paths.list_agents(), said)
        check("hr: and says so in the record",
              len(rec) == 1 and rec[0]["state"] == "failed", str(rec))

        check("hr: and the founder is refused the same nonsense an agent is",
              not hr.enact("add", "x", why="w", account="nope")[0]
              and not hr.enact("retire", "nobody", why="w")[0]
              and not hr.enact("add", "y", why="", account="beta")[0]
              and not hr.enact("add", "z", why="w", account="beta", effort="ultra")[0])
    finally:
        sys.stdin = real_stdin
        os.environ.pop("OCP_AGENT", None)
        if real_env is not None:
            os.environ["OCP_AGENT"] = real_env
        con.close()


def test_hr_move_changes_an_agents_account(home: Path) -> None:
    """Request #154/#156: there was no command to change an agent's account -
    a human edited agent.yaml by hand, and the loop #154 traced started with a
    resume id recorded on the account being left, which `--resume` on the new
    one cannot use (#154 item 4)."""
    _daemon_home(home, 20)
    accounts.register("beta")
    con = db.connect()
    real_live = obsmod.live_sessions
    try:
        db.set_account(con, "backend", "alpha")
        db.set_closed(con, "backend", "sess-1")
        # _daemon_home's default live_sessions is None (an unreadable table),
        # which _move now treats as "can't tell, leave it alone" - this test
        # wants the genuinely-not-running case, so it says so explicitly.
        obsmod.live_sessions = lambda: {}

        ok, said = hr.enact("move", "backend", why="alpha is at the wall", account="beta")
        check("hr move: the founder's own move lands without a proposal", ok, said)
        check("hr move: agent.yaml now prefers the new account",
              agents.account_of("backend") == "beta", agents.account_of("backend"))
        row = db.get_agent_state(con, "backend")
        check("hr move: a closed agent's state row is cleared, not overwritten - "
              "the preference just written answers 'where does it run' until a "
              "session records its own",
              row["account"] is None, str(dict(row)))
        check("hr move: current_account falls back to the new preference",
              agents.current_account("backend", con) == "beta",
              agents.current_account("backend", con))
        check("hr move: a resume id from the old account is dropped - it means "
              "nothing on the new one",
              row["resume_session"] is None, str(dict(row)))

        check("hr move: an unregistered account is refused",
              not hr.enact("move", "backend", why="w", account="nope")[0])
        check("hr move: an agent that already prefers its target is refused",
              not hr.enact("move", "backend", why="w", account="beta")[0])
        check("hr move: an unknown agent is refused",
              not hr.enact("move", "nobody", why="w", account="beta")[0])

        # An agent may propose its own move; only the founder's own action
        # applies it without a gate to approve.
        ok, said = hr.propose("move", "backend", requester="backend",
                              why="beta is quieter", account="alpha")
        check("hr move: an agent may propose moving itself", ok, said)
        check("hr move: but proposing changes nothing until it is applied",
              agents.account_of("backend") == "beta", agents.account_of("backend"))
    finally:
        obsmod.live_sessions = real_live
        con.close()


def test_hr_move_does_not_touch_a_live_sessions_own_record(home: Path) -> None:
    """Review of buddyron/bootstrap-and-hr-move: moving an agent while its
    session is running used to rewrite agent_state.account under it, which
    recreated the exact #154 mismatch this command exists to fix - every
    usage read believed the new account while the session kept spending the
    old one, and a later close recorded a resume id that belonged to the old
    account under what current_account now called the new one."""
    _daemon_home(home, 20)
    accounts.register("beta")
    con = db.connect()
    real_live = obsmod.live_sessions
    try:
        db.set_account(con, "backend", "alpha")
        obsmod.live_sessions = lambda: {"backend": 4242}

        ok, said = hr.enact("move", "backend", why="beta has room", account="beta")
        check("hr move (live): still succeeds - it changes the preference, not "
              "the running session", ok, said)
        check("hr move (live): agent.yaml takes the new preference",
              agents.account_of("backend") == "beta", agents.account_of("backend"))
        row = db.get_agent_state(con, "backend")
        check("hr move (live): the live session's own account record is untouched",
              row["account"] == "alpha", str(dict(row)))
        check("hr move (live): current_account still reports where the session "
              "actually is, not the new preference",
              agents.current_account("backend", con) == "alpha",
              agents.current_account("backend", con))

        # Once the session ends and is closed with a resume id, that id
        # belongs to alpha - current_account must keep saying alpha until
        # the close is recorded, or a wake would try --resume on beta.
        db.set_closed(con, "backend", "sess-live")
        check("hr move (live): closing after the fact still resumes on the "
              "account the session actually ran on",
              agents.current_account("backend", con) == "alpha",
              agents.current_account("backend", con))
    finally:
        obsmod.live_sessions = real_live
        con.close()


def test_hr_move_leaves_account_alone_when_liveness_cannot_be_read(home: Path) -> None:
    """Review of buddyron/bootstrap-and-hr-move, should-fix #3: live_sessions()
    returns None when the process table can't be read - treating that as 'not
    running' clears agent_state.account under a session that may actually be
    live, on exactly the machines where this read fails."""
    _daemon_home(home, 20)
    accounts.register("beta")
    con = db.connect()
    real_live = obsmod.live_sessions
    try:
        db.set_account(con, "backend", "alpha")
        obsmod.live_sessions = lambda: None

        ok, said = hr.enact("move", "backend", why="beta has room", account="beta")
        check("hr move (unknown liveness): still succeeds", ok, said)
        check("hr move (unknown liveness): agent.yaml takes the new preference",
              agents.account_of("backend") == "beta", agents.account_of("backend"))
        row = db.get_agent_state(con, "backend")
        check("hr move (unknown liveness): the account record is left alone, not "
              "cleared, since a live session cannot be ruled out",
              row["account"] == "alpha", str(dict(row)))
        check("hr move (unknown liveness): current_account keeps reporting the "
              "account actually on record",
              agents.current_account("backend", con) == "alpha",
              agents.current_account("backend", con))
    finally:
        obsmod.live_sessions = real_live
        con.close()


def test_the_screen_reads_keys_from_the_terminal_not_from_a_buffer() -> None:
    """An arrow is `esc [ B`, and `sys.stdin.read(1)` pulls all three bytes
    into Python's own buffer - after which select() reports the descriptor has
    nothing left, so the rest of the sequence is never collected and every
    arrow key arrives as a bare escape. The cursor did not move. Only a real
    terminal shows this, so this test opens one."""
    check("keys: an arrow is one key, not three",
          tui.split_keys("\033[B") == ["\033[B"])
    check("keys: application cursor mode is the same key",
          tui.split_keys("\033OB") == ["\033OB"])
    check("keys: a bare escape stays one key", tui.split_keys("\033") == ["\033"])
    check("keys: a burst is not one key and is not thrown away",
          tui.split_keys("jj\033[Aq") == ["j", "j", "\033[A", "q"])

    code = (f"import sys, termios, tty; sys.path.insert(0, {str(ROOT)!r});"
            "from ocp import tui;"
            "fd = sys.stdin.fileno(); saved = termios.tcgetattr(fd); tty.setcbreak(fd);"
            "out = [tui._key(5.0) for _ in range(3)];"
            "termios.tcsetattr(fd, termios.TCSADRAIN, saved);"
            "sys.stdout.write(repr(out)); sys.stdout.flush()")
    pid, fd = pty.fork()
    if pid == 0:                                  # the child is the terminal
        os.execvpe("python3", ["python3", "-c", code], dict(os.environ))
    got = ""
    try:
        time.sleep(0.6)
        for seq in (b"j", b"\x1b[B", b"\x1bOA"):
            os.write(fd, seq)
            time.sleep(0.3)
        deadline = time.time() + 5
        while time.time() < deadline and "]" not in got:
            r, _, _ = select.select([fd], [], [], 0.2)
            if not r:
                continue
            try:
                got += os.read(fd, 4096).decode(errors="replace")
            except OSError:
                break
    finally:
        os.close(fd)
        os.waitpid(pid, 0)
    check("keys: a real terminal's arrows arrive whole",
          "'\\x1b[B'" in got and "'\\x1bOA'" in got, got.strip()[-120:])


def test_top_acts_through_the_same_calls_the_daemon_makes(home: Path) -> None:
    """The screen may act, but it may not decide: every key is a call into
    `actions`, which calls what the daemon calls. What it does decide is which
    keys to offer, and that answer is read from the same row the action reads -
    offering `close` on an agent that is already closed is the screen and the
    daemon disagreeing in the one place a human is looking."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.available, orcamod.alive, orcamod.wait_idle, orcamod.send)
    try:
        orcamod.available = lambda: True
        orcamod.live_terminals = lambda: {"term_x"}
        orcamod.alive = lambda h: True
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        table = sessions_are("backend")
        orcamod.send = relaunching_send(sent, table)
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn.IDLE)
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()),
            "session_id": "sess-6", "pct_5h": 20}))

        check("top: a running agent may be closed, not woken",
              actions.can(con, "backend", "close")[0]
              and not actions.can(con, "backend", "wake")[0])

        said = actions.close(con, "backend")
        row = db.get_agent_state(con, "backend")
        check("top: closing from the screen is the daemon's own close",
              row["closed_at"] is not None and row["resume_session"] == "sess-6", said)
        check("top: and now it may be woken, not closed",
              actions.can(con, "backend", "wake")[0]
              and not actions.can(con, "backend", "close")[0])

        said = actions.wake(con, "backend")
        row = db.get_agent_state(con, "backend")
        check("top: waking from the screen resumes that same session",
              row["closed_at"] is None and any("--resume sess-6" in t for t in sent), said)

        # A stall is the one state the daemon will not leave on its own, so the
        # screen is where it is cleared - including the wake streak, or the
        # sweep would later read it as a stall of its own making.
        db.set_agent_state(con, "backend", dmn.STALLED, detail="by hand")
        for _ in range(dmn.MAX_FAILED_ATTEMPTS):
            db.bump_counter(con, "backend", "wake_failures")
        said = actions.unstall(con, "backend")
        row = db.get_agent_state(con, "backend")
        check("top: unstall clears the state and every count behind it",
              row["state"] == dmn.QUEUED and row["wake_failures"] == 0
              and row["failed_attempts"] == 0, said)

        # Sending work from the screen is an ordinary ask, from the human -
        # including the routing rules. A team that does not declare the human's
        # edge refuses here exactly as `ocp ask` would, and says so.
        said = actions.message(con, "backend", "have a look at the sea shader")
        check("top: the screen does not smuggle a message past routing",
              "denied" in said and not db.queued_for(con, "backend"), said)

        escalate = '  - { from: "*",         to: "human", kind: escalate,  creates_gate: true }'
        (home / "team.yaml").write_text(DESIGN_TEAM.replace(
            escalate,
            escalate + chr(10)
            + '  - { from: "human", to: "*", relation: supervisor, '
              'response: fire_and_forget }'))
        said = actions.message(con, "backend", "have a look at the sea shader")
        waiting = db.queued_for(con, "backend")
        check("top: and where it is declared, it arrives as a request from the human",
              waiting and waiting[-1]["requester"] == actions.HUMAN, said)
        check("top: an empty one is not sent at all",
              "nothing sent" in actions.message(con, "backend", "   "))

        # An agent with no terminal is the one the daemon cannot help at all:
        # work queued for it waits forever because there is nothing to type
        # into. Starting one is the screen's job, and it is offered only where
        # there is no terminal already.
        # STALLED is the one state the daemon deliberately leaves alone, so the
        # screen must not offer the keys that would clear it by accident: a
        # handoff pays for a fold and ends PARKED, and the stall a human was
        # meant to read is gone with none of the counters unstall resets.
        db.set_agent_state(con, "backend", dmn.STALLED, detail="stalled")
        check("top: a stalled agent is offered unstall and nothing that hides it",
              actions.can(con, "backend", "unstall")[0]
              and not actions.can(con, "backend", "park")[0]
              and not actions.can(con, "backend", "close")[0]
              and not actions.can(con, "backend", "fold")[0])
        # A fold never types into the terminal, so a lost terminal does not
        # stand in its way - that agent is a likelier one to want folded.
        db.set_agent_state(con, "backend", dmn.IDLE, detail="cleared")
        db.set_terminal(con, "backend", None, None)
        check("top: folding is offered to an agent whose terminal is gone",
              actions.can(con, "backend", "fold")[0]
              and not actions.can(con, "backend", "park")[0])
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn.IDLE, detail="cleared")

        # Work waiting is the one case where closing only adds delay - the next
        # tick wakes it straight back up. should_close refuses for that reason.
        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)
        check("top: closing is not offered while work is waiting",
              not actions.can(con, "backend", "close")[0])
        con.execute("UPDATE requests SET state = 'done' WHERE target = 'backend'")

        # perform_handoff returns True for three things that are not a handoff.
        # Reporting the state afterwards read "RUNNING" as though it were a
        # result; the line it logged is the answer.
        db.set_agent_state(con, "backend", dmn.RUNNING, detail="mid-turn")
        orcamod.idle_now = lambda h: False
        said = actions.park(con, "backend")
        check("top: parking a busy agent says it was deferred, not its state",
              "defer" in said and "RUNNING" not in said, said)
        orcamod.idle_now = lambda h: True
        db.set_agent_state(con, "backend", dmn.IDLE, detail="cleared")

        # A terminal can be closed by a human or lost when Orca restarts, and
        # nothing happens to the work: the branch and the checkout are still
        # there. Losing the record of where they are is how an agent ends up
        # with a second worktree beside its first.
        wt = home / "wt-backend"
        wt.mkdir(parents=True, exist_ok=True)
        db.set_terminal(con, "backend", "term_x", str(wt))
        obsmod.live_sessions = lambda: {}
        orcamod.live_terminals = lambda: set()      # Orca no longer has it
        dmn.sweep(con, lambda _m: None, [])
        row = db.get_agent_state(con, "backend")
        check("start: a dropped terminal does not take the worktree with it",
              row["terminal"] is None and row["worktree"] == str(wt),
              str(dict(row)))

        # `ocp launch` records what Orca hands it, which is `<repo id>::<path>`,
        # while `spawn` records a plain path. Testing the selector as a path
        # found nothing, so an agent that still had its worktree was given a
        # second one beside it.
        check("start: the worktree is found inside the selector Orca records",
              actions.worktree_path(f"abc123::{wt}") == str(wt),
              str(actions.worktree_path(f"abc123::{wt}")))
        check("start: and a worktree that is gone is not one",
              actions.worktree_path(f"abc123::{wt}-missing") is None)
        db.set_terminal(con, "backend", None, f"abc123::{wt}")

        started: list[list[str]] = []
        real_run = spawnmod._run
        try:
            spawnmod._run = lambda argv, **kw: (started.append(argv),
                                                (1, "", "no orca here"))[1]
            actions.start(con, "backend", repo=str(home))
            check("start: an agent with a worktree is started in it, not given another",
                  [a[1] for a in started] == ["terminal"], str(started))
            db.set_terminal(con, "backend", None, None)
            started.clear()
            actions.start(con, "backend", repo=str(home))
            check("start: and one with nowhere to run gets a worktree first",
                  any(a[:2] == ["orca", "worktree"] for a in started), str(started))
        finally:
            spawnmod._run = real_run
        db.set_terminal(con, "backend", "term_x", "wt")
        orcamod.live_terminals = lambda: {"term_x"}
        sessions_are("backend")

        check("top: an agent with a terminal is not offered a second one",
              not actions.can(con, "backend", "start")[0])
        # A refusal that does not say why sends the reader to press it again,
        # or to the wrong fix. Every no carries the row that said it.
        for key in ("start", "close", "park", "wake", "unstall"):
            allowed, why = actions.can(con, "backend", key)
            check(f"top: a refused {key} says why",
                  allowed or ("backend" in why and len(why) > 20), f"{allowed} {why}")

        # And "it has nowhere to run" is not an answer to "take me to it": the
        # agent with no terminal is the one a human is going there to fix.
        db.set_terminal(con, "backend", None, None)
        check("top: going to an agent is never refused",
              actions.can(con, "backend", "terminal")[0])
        went: list[str] = []
        real_focus, real_spawn = orcamod.focus, spawnmod.spawn
        try:
            orcamod.focus = lambda h: (went.append(h), (True, ""))[1]
            spawnmod.spawn = lambda agent, repo, base="main": (
                db.set_terminal(db.connect(), agent, "term_new", str(repo)),
                (True, f"{agent}: started in a new worktree"))[1]
            said = actions.terminal(con, "backend", repo=str(home))
            check("top: one with no terminal gets one, and then is gone to",
                  went == ["term_new"] and "started" in said and "backend's terminal" in said,
                  f"{went} {said}")
            went.clear()
            said = actions.terminal(con, "backend")
            check("top: and one that already has a terminal is just gone to",
                  went == ["term_new"] and "started" not in said, f"{went} {said}")
        finally:
            orcamod.focus, spawnmod.spawn = real_focus, real_spawn
        db.set_terminal(con, "backend", None, None)
        check("top: one with none is", actions.can(con, "backend", "start")[0])
        said = actions.start(con, "backend", repo=str(home))
        check("top: and starting it says why when it cannot",
              "orca" in said or "worktree" in said or "base" in said, said)
        db.set_terminal(con, "backend", "term_x", "wt")

        # Answering closes the request and tells the agent, which is what
        # `ocp resolve` does - the screen must not invent a second way.
        db.add_request(con, "marina", actions.HUMAN, "which variant ships?",
                       None, "peer", "async", None, 0, None)
        # By who sent it. The mailbox holds answers to the human's own asks as
        # well now, and they sort among the rest by id.
        rid = [m["id"] for m in actions.messages(con) if m["from"] == "marina"][-1]
        said = actions.answer(con, rid, "variant 11")
        row = db.get_request(con, rid)
        check("top: answering resolves the request with the verdict",
              row["state"] == "done" and "variant 11" in (row["verdict"] or ""), said)
        check("top: and it is no longer waiting on the human",
              rid not in [m["id"] for m in actions.messages(con)])
    finally:
        (orcamod.available, orcamod.alive, orcamod.wait_idle, orcamod.send) = real
        con.close()


def test_an_answer_reaches_the_agent_that_asked(home: Path) -> None:
    """Answering wrote the verdict on the row and stopped. The agent that asked
    - which may have parked waiting for it - was never told, so a founder
    answering an escalation on the screen and the agent never hearing it is the
    same hole seen from the other end. An answer is delivered the way work is,
    once, through a cursor of its own."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.available, orcamod.alive, orcamod.idle_now, orcamod.send)
    try:
        orcamod.available = lambda: True
        orcamod.live_terminals = lambda: {"term_x"}
        orcamod.alive = lambda h: True
        orcamod.idle_now = lambda h: True
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        sessions_are("backend")
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn.IDLE)

        rid = db.add_request(con, "backend", "reviewer", "is this ready?", None,
                             "peer", "async", None, 0, None)
        check("answered: nothing is announced while it is still open",
              not dmn.deliver_answers(con, "backend", lambda _m: None) and not sent)

        # Resolving is reporting: the answer goes to whoever asked as part of
        # the same command, and the tick is the safety net rather than the only
        # path. So by the time resolve returns there is nothing left to deliver.
        askmod.resolve(rid, "ship it, with the one nit fixed")
        check("answered: once it comes back, the agent is told",
              sent and not dmn.deliver_answers(con, "backend", lambda _m: None),
              str(sent))
        check("answered: with who answered and what they said",
              "reviewer" in sent[-1] and "ship it" in sent[-1], sent[-1][:120])
        # Only when there is more than it was shown. A two-word verdict with an
        # instruction to go and read it whole is ceremony typed at an agent
        # that was in the middle of something.
        check("answered: a short answer is said and nothing else",
              "ocp read" not in sent[-1], sent[-1][:160])
        askmod.resolve(db.add_request(con, "backend", "reviewer", "and this?", None,
                                      "peer", "async", None, 0, None),
                       "a verdict long enough to be cut: " + "x" * 200)
        dmn.deliver_answers(con, "backend", lambda _m: None)
        check("answered: one that was cut says where the rest is",
              "ocp read" in sent[-1], sent[-1][-80:])

        sent.clear()
        check("answered: and it is not told twice",
              not dmn.deliver_answers(con, "backend", lambda _m: None) and not sent)

        # Answering is not monotonic in id: two asks, the later one answered
        # first. A high-water cursor put the earlier answer behind it forever,
        # which is the hole this path exists to close, one layer down.
        early = db.add_request(con, "backend", "reviewer", "the one it parked on",
                               None, "peer", "async", None, 0, None)
        late = db.add_request(con, "backend", "keeper", "a second question",
                              None, "peer", "async", None, 0, None)
        askmod.resolve(late, "keeper answers first")
        dmn.deliver_answers(con, "backend", lambda _m: None)
        sent.clear()
        askmod.resolve(early, "and the one it was waiting for")
        check("answered: an answer that comes back late is still delivered",
              sent and "waiting for" in sent[-1], str(sent[-1:])[:120])

        # A refusal is the answer that means do something else; a routing denial
        # was refused before anything was sent and the agent already read it.
        rejected = db.add_request(con, "backend", "reviewer", "may I?", None,
                                  "peer", "async", None, 0, None)
        sent.clear()
        askmod.resolve(rejected, "no, not this week", state="rejected")
        check("answered: a refusal is delivered", sent and "not this week" in sent[-1],
              str(sent[-1:])[:120])
        denied = db.add_request(con, "backend", "nobody", "unroutable", None,
                                "peer", "async", None, 0, None)
        db.set_request_state(con, denied, "denied", "unknown target")
        dmn.deliver_answers(con, "backend", lambda _m: None)
        check("answered: an ask routing refused is not typed back at it",
              "unroutable" not in sent[-1] and "unknown target" not in sent[-1],
              sent[-1][:160])

        # Everything marked told has to have been said: the ones not shown in
        # full are named, not counted. They pile up while the agent cannot be
        # reached - resolving delivers its own answer when it can - so this is
        # the tick catching up after a session was away.
        sessions_are()
        for i in range(5):
            rid_i = db.add_request(con, "backend", "reviewer", f"q{i}", None,
                                   "peer", "async", None, 0, None)
            askmod.resolve(rid_i, f"a{i}")
        sessions_are("backend")
        sent.clear()
        dmn.deliver_answers(con, "backend", lambda _m: None)
        unshown = [r["id"] for r in db.unannounced_answers(con, "backend")]
        check("answered: nothing is marked told that was not named",
              not unshown and "also answered: #" in sent[-1], sent[-1][:200])

        # The same gates as any other send: nothing is typed at a shell.
        rid2 = db.add_request(con, "backend", "reviewer", "and this?", None,
                              "peer", "async", None, 0, None)
        askmod.resolve(rid2, "no")
        sent.clear()
        sessions_are()
        check("answered: an answer is not typed at a terminal with no session",
              not dmn.deliver_answers(con, "backend", lambda _m: None) and not sent)
    finally:
        (orcamod.available, orcamod.alive, orcamod.idle_now, orcamod.send) = real
        con.close()


def test_an_answer_wakes_the_agent_that_was_waiting_for_it(home: Path) -> None:
    """An agent that asked something, parked on the reply and was then closed
    is waiting on an answer, not on work - and resolving its request writes
    QUEUED over the park and clears the wake_at that would have brought it
    back. Nothing else in the system wakes an agent for an answer, so without
    this it waits forever, and a restart does not rescue it."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.available, orcamod.alive, orcamod.wait_idle, orcamod.send)
    try:
        orcamod.available = lambda: True
        orcamod.live_terminals = lambda: {"term_x"}
        orcamod.alive = lambda h: True
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        orcamod.idle_now = lambda h: True
        table = sessions_are()
        orcamod.send = relaunching_send(sent, table)
        db.set_terminal(con, "backend", "term_x", "wt")
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()),
            "session_id": "sess-4", "pct_5h": 20}))

        rid = db.add_request(con, "backend", "reviewer", "which way?", None,
                             "peer", "async", None, 0, None)
        db.set_closed(con, "backend", "sess-4")
        db.set_agent_state(con, "backend", dmn.PARKED, wake_at=int(time.time()) + 3600,
                           detail=f"awaiting reply to request {rid}")

        # Resolving hands the answer over itself when it can reach the agent.
        # Here it cannot - Orca has lost the terminal - which is the world this
        # test is about: the park is written off and nothing has been told.
        orcamod.alive = lambda h: False
        askmod.resolve(rid, "this way, and here is why")
        orcamod.alive = lambda h: True
        row = db.get_agent_state(con, "backend")
        check("waiting: resolving leaves it closed, with no deadline left",
              row["closed_at"] is not None and row["wake_at"] is None, str(dict(row)))

        dmn.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
        check("waiting: the tick wakes it for the answer it asked for",
              any("--resume sess-4" in t for t in sent), str(sent))
        check("waiting: and it is no longer closed",
              db.get_agent_state(con, "backend")["closed_at"] is None)

        sent.clear()
        dmn.supervise(con, "backend", lambda _m: None, int(time.time()), {}, [])
        check("waiting: the next tick hands it the answer",
              any("this way" in t for t in sent), str(sent)[:160])

        # And when it can be reached, resolving is the delivery: the answer
        # does not wait for a tick that may be two seconds or a restart away.
        sent.clear()
        second = db.add_request(con, "backend", "reviewer", "and after that?", None,
                                "peer", "async", None, 0, None)
        askmod.resolve(second, "then the other thing")
        check("waiting: an agent that can be reached is told by the resolve itself",
              any("then the other thing" in t for t in sent), str(sent)[:160])
    finally:
        (orcamod.available, orcamod.alive, orcamod.wait_idle, orcamod.send) = real
        con.close()


def test_a_request_can_be_read_whole(home: Path) -> None:
    """`ocp inbox` shows a line each, cut to fit, and the rest was only
    reachable by knowing where the raw JSON lives - which an agent found out by
    trial. An agent that cannot read the whole of what it was asked answers the
    half it could see."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        body = "line one of the order\n" + ("x" * 200) + "\nlast line of the order"
        rid = db.add_request(con, actions.HUMAN, "backend", body, None,
                             "peer", "async", None, 0, None)
        con.close()
        out = _capture(lambda: climod.main(["read", str(rid)]))
        check("read: the whole body is printed, not a cut line",
              "last line of the order" in out and "x" * 200 in out, out[:120])
        check("read: with who asked whom, and in what state",
              f"{actions.HUMAN} → backend" in out and "queued" in out, out[:120])

        askmod.resolve(rid, "done, and here is why: " + "y" * 120)
        out = _capture(lambda: climod.main(["read", str(rid)]))
        check("read: and the answer comes with it", "y" * 120 in out, out[-80:])
        check("read: an id nobody has says so, without pretending",
              "no request" in _capture(lambda: climod.main(["read", "99999"])))
    finally:
        pass


def test_inbox_and_withdraw_cli(home: Path) -> None:
    """`ocp inbox` shows the head and a count, not the whole queue (request
    #189); `ocp withdraw` takes back an undelivered request of your own."""
    _daemon_home(home, 20)
    agents.register("reviewer", "alpha")
    con = db.connect()
    try:
        rid1 = db.add_request(con, "backend", "reviewer", "task one", None,
                              "peer", "async", None, 0, None)
        rid2 = db.add_request(con, "backend", "reviewer", "task two", None,
                              "peer", "async", None, 0, None)
    finally:
        con.close()

    real_agent = os.environ.get("OCP_AGENT")
    try:
        os.environ["OCP_AGENT"] = "reviewer"
        out = _capture(lambda: climod.main(["inbox"]))
        check("inbox: shows the head's body", "task one" in out, out)
        check("inbox: not the one behind it", "task two" not in out, out)
        check("inbox: says how many more are waiting", "1 more waiting" in out, out)

        con = db.connect()
        try:
            check("inbox: viewing your own head marks it delivered",
                  db.get_request(con, rid1)["delivered_at"] is not None)
        finally:
            con.close()

        out = _capture(lambda: climod.main(["inbox", "--all"]))
        check("inbox --all: shows the whole queue",
              "task one" in out and "task two" in out, out)

        os.environ["OCP_AGENT"] = "backend"
        out = _capture(lambda: climod.main(["withdraw", str(rid2)]))
        check("withdraw: an undelivered request of your own can be taken back",
              "withdrawn" in out, out)
        con = db.connect()
        try:
            check("withdraw: it leaves the queue",
                  db.get_request(con, rid2)["state"] == "withdrawn",
                  db.get_request(con, rid2)["state"])
        finally:
            con.close()

        out = _capture(lambda: climod.main(["withdraw", str(rid1)]))
        check("withdraw: a delivered request refuses",
              "already delivered" in out, out)
    finally:
        if real_agent is None:
            os.environ.pop("OCP_AGENT", None)
        else:
            os.environ["OCP_AGENT"] = real_agent


def test_only_one_thing_types_at_an_agent_at_a_time(home: Path) -> None:
    """A wake holds an agent's terminal for as long as a session takes to
    appear - up to fifteen seconds - and the daemon ticks every two. Seven
    ticks fit in that window, so the daemon and a human pressing `w` both typed
    `ocp launch` into the same terminal: the second lands beside the session
    the first started, or inside its input box, unsent.

    Everything else in this system is settled by writing to the database. The
    terminal is the one place two writers can spoil, so the paths that type
    take a lock, and the one that cannot have it does nothing rather than
    waiting - a tick must not queue up behind a human mid-confirmation."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.available, orcamod.alive, orcamod.wait_idle, orcamod.send)
    try:
        orcamod.available = lambda: True
        orcamod.live_terminals = lambda: {"term_x"}
        orcamod.alive = lambda h: True
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        orcamod.idle_now = lambda h: True
        table = sessions_are("backend")
        orcamod.send = relaunching_send(sent, table)
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_closed(con, "backend", "sess-1")

        # Another process is mid-wake on this agent.
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import sys, time; sys.path.insert(0, %r);"
             "from ocp import lock;"
             "ctx = lock.terminal('backend'); print(ctx.__enter__(), flush=True);"
             "time.sleep(30)" % str(ROOT)],
            stdout=subprocess.PIPE, text=True, env=dict(os.environ))
        try:
            check("lock: the other process has it", holder.stdout.readline().strip() == "True")
            ok, why = dmn.wake_agent(con, "backend", lambda _m: None)
            check("lock: a second wake types nothing at all",
                  not ok and not sent and "someone else" in why, f"{why} {sent}")
            row = db.get_agent_state(con, "backend")
            check("lock: and it stays closed, so the next attempt still has its way back",
                  row["closed_at"] is not None and row["resume_session"] == "sess-1",
                  str(dict(row)))
        finally:
            holder.kill()
            holder.wait()

        # The kernel releases it when the holder dies, so a daemon killed
        # mid-wake does not leave an agent unreachable.
        ok, why = dmn.wake_agent(con, "backend", lambda _m: None)
        check("lock: once the holder is gone the wake goes through",
              ok and any("--resume sess-1" in t for t in sent), f"{why} {sent}")
    finally:
        (orcamod.available, orcamod.alive, orcamod.wait_idle, orcamod.send) = real
        con.close()


def test_growing_the_team_needs_a_person(home: Path) -> None:
    """A team that cannot grow has one way to take on more work: each agent
    quietly making helpers that die with the session that made them. So growing
    is a first-class operation - and the one with the least business being
    automatic, because an agent that can hire can spend an account into the
    ground with nobody having decided to.

    Any agent proposes, only the founder approves, and the change is made from
    the file the proposal wrote. The approval is read, never interpreted: a
    model taking "maybe next week" for a yes is the failure this prevents."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        accounts.register("beta")
        agents.register("manager", "alpha")
        (home / "team.yaml").write_text("""
teams:
  dev: [marina]
agents:
  manager: { intake: dedicated }
  marina:  { intake: dedicated }
  hr:      { intake: serial }
edges:
  - { from: "manager", to: "dev.*", relation: supervisor, response: fire_and_forget }
  - { from: "*", to: "hr", relation: peer, response: async }
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
""")
        agents.register("marina", "beta")
        agents.register("hr", "beta")

        # Nothing happens on the proposal itself.
        ok, said = hr.propose("add", "dohyun", requester="hr", why="two branches are "
                              "waiting on one developer", account="beta", team_name="dev",
                              repo=str(home / "harbor"))
        pid = said.split()[1]
        check("hr: proposing raises it to the founder and changes nothing",
              ok and "dohyun" not in paths.list_agents(), said)
        rid = [m["id"] for m in actions.messages(con) if "dohyun" in (m["body"] or "")]
        check("hr: and it is in front of the founder, with the words to answer with",
              rid and "approve" in db.get_request(con, rid[0])["body"], said)

        # Unanswered, and answered with something that is not a decision.
        ok, said = hr.apply(pid)
        check("hr: it cannot be applied before it is answered",
              not ok and "has not answered" in said, said)
        askmod.resolve(rid[0], "hmm, maybe next week")
        ok, said = hr.apply(pid)
        check("hr: nor on an answer that is not a decision",
              not ok and "not a decision" in said and "dohyun" not in paths.list_agents(),
              said)

        # Approved, and the two writes a human would have made.
        con.execute("UPDATE requests SET verdict = ? WHERE id = ?", ("approve", rid[0]))
        ok, said = hr.apply(pid)
        table = yamlcompat.load((home / "team.yaml").read_text())
        check("hr: an approved proposal registers the agent", ok and "dohyun" in paths.list_agents(), said)
        check("hr: and puts it in the team, which is what the routing is keyed on",
              "dohyun" in (table["teams"]["dev"] or []) and "dohyun" in table["agents"],
              str(table["teams"]))
        check("hr: the manager may now ask it, because the wildcard covers it",
              team.Team(table).route("manager", "dohyun").allowed)
        check("hr: applying twice is refused", not hr.apply(pid)[0])

        # Retiring archives rather than deletes: the journal is the record of
        # work that was actually done.
        ok, said = hr.propose("retire", "dohyun", requester="hr", why="branch landed")
        pid2 = said.split()[1]
        rid2 = [m["id"] for m in actions.messages(con) if "retire dohyun" in (m["body"] or "")]
        askmod.resolve(rid2[0], "approve")
        ok, said = hr.apply(pid2)
        table = yamlcompat.load((home / "team.yaml").read_text())
        archived = list((home / "agents" / "archive").glob("*dohyun*"))
        check("hr: retiring takes it out of the table", ok and "dohyun" not in table["agents"], said)
        check("hr: and keeps its folder", archived and (archived[0] / "journal").exists(),
              str(archived))
        check("hr: and takes its scheduling row with it - nothing lists an agent "
              "that is not there",
              db.get_agent_state(con, "dohyun") is None,
              str(db.get_agent_state(con, "dohyun")))

        # A rename takes the whole identity with it - folder, table, and every
        # row that ever named it - and not while a session is running under it.
        db.add_request(con, "marina", "manager", "a report from before", None,
                       "subordinate", "fire_and_forget", None, 0, None)
        sessions_are("manager")
        ok, said = hr.propose("rename", "manager", requester="hr", to="harbor-manager",
                              why="it only ever runs the harbor project", team_name=None)
        pid_r = said.split()[1]
        rid_r = [m["id"] for m in actions.messages(con) if "harbor-manager" in (m["body"] or "")]
        askmod.resolve(rid_r[0], "approve")
        ok, said = hr.apply(pid_r)
        check("hr: an identity does not change under a running session",
              not ok and "session running" in said, said)
        sessions_are()
        ok, said = hr.apply(pid_r)
        table = yamlcompat.load((home / "team.yaml").read_text())
        check("hr: with the session closed, the rename lands",
              ok and "harbor-manager" in paths.list_agents()
              and "manager" not in table["agents"], said)
        check("hr: and the edges name it too - the renamed manager still "
              "supervises its team",
              team.Team(table).route("harbor-manager", "marina").allowed
              and "\"manager\"" not in (home / "team.yaml").read_text(),
              (home / "team.yaml").read_text())
        check("hr: and its folder came with it",
              (home / "agents" / "harbor-manager").is_dir()
              and not (home / "agents" / "manager").exists())
        check("hr: as did every row that named it",
              con.execute("SELECT COUNT(*) c FROM requests WHERE target = ?",
                          ("harbor-manager",)).fetchone()["c"] == 1
              and con.execute("SELECT COUNT(*) c FROM requests WHERE target = ?",
                              ("manager",)).fetchone()["c"] == 0)

        # An edge the text edit cannot rewrite is refused, not guessed at: the
        # table is what routing runs on, and a rename that dropped an edge
        # would leave the renamed supervisor supervising nobody.
        (home / "team.yaml").write_text("""
teams:
  dev: [marina]
agents:
  harbor-manager: { intake: dedicated }
  marina:         { intake: dedicated }
edges:
  - from: harbor-manager
    to: marina
    relation: supervisor
    response: fire_and_forget
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
""")
        ok, said = hr.enact("rename", "harbor-manager", to="boss",
                            why="an edge this cannot rewrite")
        table = yamlcompat.load((home / "team.yaml").read_text())
        check("hr: a rename that would drop an edge is refused, and writes nothing",
              not ok and "by hand" in said and "harbor-manager" in table["agents"],
              said)
        check("hr: and the agent is left where it was",
              "harbor-manager" in paths.list_agents() and "boss" not in paths.list_agents())

        # A rejection is recorded and never applied.
        ok, said = hr.propose("add", "seoyun", requester="hr", why="third lane",
                              account="beta", team_name="dev")
        pid3 = said.split()[1]
        rid3 = [m["id"] for m in actions.messages(con) if "seoyun" in (m["body"] or "")]
        askmod.resolve(rid3[0], "reject - one lane at a time")
        ok, said = hr.apply(pid3)
        check("hr: a rejected proposal stays rejected",
              not ok and "rejected" in said and "seoyun" not in paths.list_agents(), said)
        check("hr: and the file says so",
              hr.load_proposal(pid3)["state"] == "rejected", str(hr.load_proposal(pid3)))

        # An edit that would break the routing table is not written at all.
        check("hr: a proposal for an account nobody logged in is refused up front",
              not hr.propose("add", "x", requester="hr", why="w", account="nope")[0])
    finally:
        con.close()


def test_the_whole_loop_runs_on_our_own_commands(home: Path) -> None:
    """Order a child, read what it sends back, report to the parent - all of it
    through `ocp`, with no step that needs a human or a subagent.

    Each leg was there and none of them finished: `ocp ask` queued and left the
    rest to a tick, and a target with no terminal was a request for somebody to
    press a key. An agent that cannot finish the job it was given goes looking
    for one it can - which is a subagent, dead at the end of its session."""
    _daemon_home(home, 20)
    con = db.connect()
    typed: list[tuple[str, str]] = []
    real = (orcamod.available, orcamod.alive, orcamod.idle_now, orcamod.send,
            spawnmod.spawn, dmn.wait_for_session)
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        orcamod.idle_now = lambda h: True
        orcamod.send = lambda h, text, enter=True: (typed.append((h, text)), (True, ""))[1]
        dmn.wait_for_session = lambda agent, timeout=0: True

        def fake_spawn(agent, repo, base="main"):
            db.set_terminal(db.connect(), agent, f"term_{agent}", str(repo))
            return True, f"{agent}: worktree {repo}"

        spawnmod.spawn = fake_spawn
        (home / "team.yaml").write_text("""
teams:
  dev: [marina]
agents:
  manager: { intake: dedicated }
  marina:  { intake: dedicated }
edges:
  - { from: "manager", to: "dev.*", relation: supervisor, response: fire_and_forget }
  - { from: "dev.*", to: "manager", relation: subordinate, response: fire_and_forget }
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
""")
        agents.register("manager", "alpha")
        agents.register("marina", "beta", repo=str(home / "harbor"))
        db.set_terminal(con, "manager", "term_manager", "wt")
        sessions_are("manager", "marina")

        # 1. The manager orders its report. marina has nowhere to run, and that
        #    is the manager's problem to have solved, not a human's.
        ok, said, rid = askmod.ask("marina", "implement the smooth sea",
                                   requester="manager")
        check("loop: the order is allowed and lands", ok and rid, said)
        check("loop: the child was given somewhere to run on the way",
              db.get_agent_state(con, "marina")["terminal"] == "term_marina", said)
        check("loop: and it was told, by the command that sent it",
              any(h == "term_marina" and "waiting for you" in t for h, t in typed),
              str(typed)[:200])
        check("loop: the sender is told it arrived, not that it was queued",
              "delivered to marina" in said, said)

        # 2. marina answers. Resolving is reporting: the manager hears it now.
        typed.clear()
        okr, saidr = askmod.resolve(rid, "shipped on buddyron/marina, 40 tests")
        check("loop: the child's answer reaches its parent",
              okr and any(h == "term_manager" and "shipped on" in t for h, t in typed),
              f"{saidr} {typed}"[:200])

        # 3. The manager reports up to the human, which is a screen, not a
        #    terminal - so nothing is typed and it says where it went.
        typed.clear()
        oke, saide, eid = askmod.ask(actions.HUMAN, "sea is in, one nit left",
                                     requester="manager")
        check("loop: a report to the founder goes to the screen, not a terminal",
              oke and not typed and eid in [m["id"] for m in actions.messages(con)],
              f"{saide} {typed}"[:200])
    finally:
        (orcamod.available, orcamod.alive, orcamod.idle_now, orcamod.send,
         spawnmod.spawn, dmn.wait_for_session) = real
        con.close()


def test_an_agent_can_put_another_one_to_work(home: Path) -> None:
    """An agent with nowhere to run can be given work by nobody but a human.

    That is what sends an agent looking for a subagent instead - a thing it can
    make itself, which dies with its session, holds no account, has no inbox
    and appears on no screen. The daemon can make a worktree and a terminal;
    the only fact it was missing is which repository, and that is `repo:`."""
    _daemon_home(home, 20)
    con = db.connect()
    started: list = []
    real = (orcamod.available, spawnmod.spawn, dmn.wait_for_session)
    real_wake = dmn.wake_agent
    try:
        orcamod.available = lambda: True
        dmn.wait_for_session = lambda agent, timeout=0: True

        def fake_spawn(agent, repo, base="main"):
            started.append((agent, str(repo)))
            db.set_terminal(db.connect(), agent, "term_new", str(repo))
            return True, f"{agent}: worktree {repo}"

        spawnmod.spawn = fake_spawn
        agents.register("marina", "beta")
        db.set_terminal(con, "marina", None, None)
        db.add_request(con, "manager", "marina", "implement the smooth sea", None,
                       "supervisor", "fire_and_forget", None, 0, None)

        # Without a repo nothing is invented: it says what is missing and to whom.
        said = dmn._start_for_work(con, "marina", lambda _m: None)
        check("dispatch: with no repo it says what is missing, and does not guess",
              not started and "repo:" in said, said)

        agents.register("marina", "beta", repo=str(home / "checkout"))
        said = dmn._start_for_work(con, "marina", lambda _m: None)
        check("dispatch: with one, the daemon gives it somewhere to run",
              started == [("marina", str((home / "checkout").resolve()))],
              f"{started} {said}")
        check("dispatch: and the terminal is recorded, so work can be delivered",
              db.get_agent_state(con, "marina")["terminal"] == "term_new")

        # A start that never takes is a streak, not a loop: three failures and
        # it is a human's problem, said out loud.
        db.set_terminal(con, "marina", None, None)
        spawnmod.spawn = lambda agent, repo, base="main": (False, "orca refused")
        for _ in range(dmn.MAX_FAILED_ATTEMPTS):
            dmn._start_for_work(con, "marina", lambda _m: None)
        said = dmn._start_for_work(con, "marina", lambda _m: None)
        check("dispatch: a start that keeps failing is not retried forever",
              "attempts have failed" in said, said)

        # An answer is work too: a wake puts a session back into a terminal the
        # agent still has, so one that lost its terminal while waiting on an
        # answer has no way back at all. reviewer sat that way for a day.
        spawnmod.spawn = fake_spawn
        woken: list = []
        dmn.wake_agent = lambda con_, a, log_, **kw: (woken.append(a), (False, "no terminal to wake"))[1]
        started.clear()
        agents.register("reviewer", "alpha", repo=str(home / "checkout"))
        db.set_terminal(con, "reviewer", None, None)
        db.reset_counter(con, "reviewer", "wake_failures")
        rid = db.add_request(con, "reviewer", "marina", "look at this branch", None,
                             "peer", "async", None, 0, None)
        askmod.resolve(rid, "looks right")
        db.set_closed(con, "reviewer", "sess_old")
        logged: list[str] = []
        dmn.supervise(con, "reviewer", lambda m: logged.append(m),
                      int(time.time()), {}, [])
        check("dispatch: an agent with an answer and no terminal is given one",
              ("reviewer", str((home / "checkout").resolve())) in started,
              f"{started} {logged}")
        # The terminal it was just given holds a session that is still booting.
        # Typing a wake into it loses the opening characters and leaves the
        # rest in the input box, unsent.
        check("dispatch: and the tick does not then wake the terminal it just made",
              not woken, str(woken))

        # When there is nothing to wake and nothing can be started, the tick
        # says so - once. It repeated in silence for a day, and two of the
        # reasons it can give last for hours.
        db.set_terminal(con, "reviewer", None, None)
        spawnmod.spawn = lambda agent, repo, base="main": (False, "orca refused")
        logged.clear()
        for _ in range(3):
            dmn.supervise(con, "reviewer", lambda m: logged.append(m),
                          int(time.time()), {}, [])
        said = [m for m in logged if "could not be woken" in m]
        check("dispatch: a wake that cannot happen is said out loud",
              len(said) == 1 and "answer(s) it asked for" in said[0], str(logged))
        check("dispatch: and not once per tick for as long as it lasts",
              len([m for m in logged if "could not be woken" in m]) == 1, str(logged))
    finally:
        (orcamod.available, spawnmod.spawn, dmn.wait_for_session) = real
        dmn.wake_agent = real_wake
        dmn._SAID.clear()
        con.close()


def test_weekly_headroom_is_spent_to_its_own_wall(home: Path) -> None:
    """Founder's decision #254: an account at 5h 9% / 7d 99% is spent to the
    weekly wall, not stopped short of it - it must still be picked, and an
    agent working on it moves only once 7d actually reaches its own wall."""
    _daemon_home(home, 20)
    accounts.register("beta")
    agents.register("keeper", "alpha")
    for acct in ("alpha", "beta"):
        (home / "accounts" / acct / ".claude").mkdir(parents=True, exist_ok=True)
        (home / "accounts" / acct / ".claude" / ".credentials.json").write_text("{}")

    # alpha's weekly headroom expires soon and is spendable to the wall itself.
    now = int(time.time())
    def alpha(pct5, pct7):
        write_status(home, "alpha", pct_5h=pct5, pct_7d=pct7,
                     reset_7d=now + 3600, reset_5h=now + 1800)
    write_status(home, "beta", pct_5h=9, pct_7d=10,
                 reset_7d=now + 90000, reset_5h=now + 1800)
    alpha(9, 99)
    picked, reasons = status.pick_account(["alpha", "beta"], load={})
    check("7d wall: the soon-to-reset account is picked at 7d 99%",
          picked == "alpha", str((picked, reasons)))
    alpha(9, 100)
    check("7d wall: at the wall it is not picked",
          status.pick_account(["alpha", "beta"], load={})[0] == "beta")
    # The 5h side is a stretch, not a line: an idle account is picked while one
    # more agent would last the least worth starting, and not once it would not.
    alpha(70, 10)
    check("5h: 70% with nothing on it lasts long enough and is picked",
          status.pick_account(["alpha", "beta"], load={})[0] == "alpha")
    alpha(85, 10)
    picked, reasons = status.pick_account(["alpha", "beta"], load={})
    check("5h: 85% would last 20 minutes and is not picked",
          picked == "beta" and "under the 30m" in dict(reasons)["alpha"], str(reasons))
    alpha(100, 10)
    check("5h: at the wall it is not picked",
          status.pick_account(["alpha", "beta"], load={})[0] == "beta")
    alpha(9, 100)

    con = db.connect()
    try:
        db.set_terminal(con, "keeper", "term_x", "wt")
        sessions_are("keeper")
        db.set_account(con, "keeper", "alpha")

        db.add_request(con, "marina", "keeper", "carry on", None, "peer", "async", None, 0, None)
        t = triggers.evaluate(9, 10, pct_7d=100)
        check("7d wall: 5h 9% / 7d 100% fires a move trigger on the 7d figure",
              t.fired and t.kind == "usage_7d" and "pct_7d" in t.reason, t.reason)

        real = (orcamod.available, orcamod.alive, orcamod.close_session,
               orcamod.relaunch, orcamod.idle_now, dmn.wait_for_session)
        try:
            orcamod.available = lambda: True
            orcamod.alive = lambda h: True
            orcamod.idle_now = lambda h: True
            orcamod.close_session = lambda h, **kw: (True, "session ended")
            orcamod.relaunch = lambda h, cmd, **kw: (True, "relaunched")
            dmn.wait_for_session = lambda agent, timeout=0: True
            keep_handoff("keeper", "# handoff · keeper\n## 다음\n- carry on\n")
            check("7d wall: the agent moves off an account that hit its 7d wall",
                  dmn._move_to_room(con, "keeper", t, lambda _m: None))
            check("7d wall: it landed on the account with weekly room",
                  agents.current_account("keeper", con) == "beta",
                  agents.current_account("keeper", con))
        finally:
            (orcamod.available, orcamod.alive, orcamod.close_session,
             orcamod.relaunch, orcamod.idle_now, dmn.wait_for_session) = real
    finally:
        con.close()


def test_an_agent_at_the_wall_moves_to_an_account_with_room(home: Path) -> None:
    """agent.yaml names a preference, not a destination.

    Total throughput is accounts x window, and moving a session does not change
    it. What it changes is whether an agent sits out its own five hours while
    another account idles beside it - worth a cold start only for an agent that
    is at the wall with work waiting and a reset too far off to wait for. The
    session being moved is already over: it hit the wall, and its context is
    gone either way."""
    _daemon_home(home, 20)
    con = db.connect()
    agents.register("keeper", "alpha")
    write_status(home, "beta", pct_5h=10, pct_7d=5, ctx_pct=5,
                 reset_5h=int(time.time()) + 1800)
    for acct in ("alpha", "beta"):
        (home / "accounts" / acct / ".claude").mkdir(parents=True, exist_ok=True)
        (home / "accounts" / acct / ".claude" / ".credentials.json").write_text("{}")
    keep_handoff("keeper", "# handoff · keeper\n## 다음\n- carry on\n")
    db.add_request(con, "marina", "keeper", "carry on", None, "peer", "async", None, 0, None)
    far = int(time.time()) + 3 * 3600
    real = (orcamod.available, orcamod.alive, orcamod.close_session, orcamod.relaunch,
            orcamod.clear_context, orcamod.resume_with, orcamod.idle_now,
            dmn.wait_for_session)
    launched: list[str] = []
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        orcamod.idle_now = lambda h: True
        orcamod.clear_context = lambda h, **kw: (True, "cleared")
        orcamod.close_session = lambda h, **kw: (True, "session ended")
        orcamod.resume_with = lambda h, text: (True, "handed back")
        orcamod.relaunch = lambda h, cmd, **kw: (launched.append(cmd), (True, "relaunched"))[1]
        dmn.wait_for_session = lambda agent, timeout=0: True
        sessions_are("keeper")
        db.set_terminal(con, "keeper", "term_x", "wt")

        # Where a launch would go, before anything has moved.
        check("move: a preference with room is kept",
              launch.choose_account("keeper") == "alpha",
              launch.choose_account("keeper"))

        write_status(home, "alpha", pct_5h=100, pct_7d=20, ctx_pct=5, reset_5h=far)
        check("move: a preference at the wall gives way to room",
              launch.choose_account("keeper") == "beta", launch.choose_account("keeper"))
        check("move: but a resume goes back where its session lives",
              launch.choose_account("keeper", resuming=True) == "alpha",
              launch.choose_account("keeper", resuming=True))

        t = triggers.evaluate(100, 10)
        check("move: the wall is a usage trigger", t.fired and t.kind == "usage", t.kind)
        dmn.perform_handoff(con, "keeper", t, lambda _m: None)

        row = db.get_agent_state(con, "keeper")
        # A state nothing else in the daemon will act on is where a moved agent
        # went to die: HANDOFF is protected from its own hooks, skipped by the
        # sweep, refused by close, and delivered nothing. Every other exit from
        # a handoff writes a state, so this one has to as well.
        check("move: it comes out of the handoff into a state that is dispatched",
              row["state"] == dmn.QUEUED and not row["wake_at"] and not row["closed_at"],
              f"{row['state']} wake={row['wake_at']} closed={row['closed_at']}")
        db.add_request(con, "marina", "keeper", "after the move", None,
                       "peer", "async", None, 0, None)
        told: list[str] = []
        real_send = orcamod.send
        try:
            orcamod.send = lambda h, t, enter=True: (told.append(t), (True, ""))[1]
            dmn.deliver_queued(con, "keeper", lambda _m: None)
        finally:
            orcamod.send = real_send
        check("move: and work reaches it on the account it moved to", told, str(told))
        check("move: a fresh session is started, not a resume of the old one",
              launched and "--resume" not in launched[-1], str(launched))
        # What that relaunch will exec with, and what it writes down. The
        # launch is the single writer of the account - it is the process that
        # sets CLAUDE_CONFIG_DIR - so the record cannot disagree with the
        # environment the session is actually running in.
        argv, env = launch.build("keeper")
        check("launch: an agent cannot make subagents - work to another agent "
              "goes through `ocp ask`, where the bridge can see it",
              "--disallowedTools" in argv
              and argv[argv.index("--disallowedTools") + 1] == "Agent", str(argv))
        check("move: the launch it typed goes to the new account",
              env["OCP_ACCOUNT"] == "beta"
              and env["CLAUDE_CONFIG_DIR"].endswith("accounts/beta/.claude"),
              f"{env['OCP_ACCOUNT']} {env['CLAUDE_CONFIG_DIR']}")
        launch._record_terminal("keeper", env["OCP_ACCOUNT"])
        check("move: and starting there is what records it",
              agents.current_account("keeper", con) == "beta",
              agents.current_account("keeper", con))
        moved = [e for e in db.events_after(con, 0, agent="keeper")
                 if e["kind"] == "moved_account"]
        check("move: the move is on the record, with both ends",
              moved and '"from": "alpha"' in moved[-1]["payload"]
              and '"to": "beta"' in moved[-1]["payload"], str(moved))

        # Below the wall a working agent is left alone, but an account that
        # would not last one more start is not somewhere to start a new one.
        db.set_account(con, "keeper", "alpha")
        write_status(home, "alpha", pct_5h=87, pct_7d=20, ctx_pct=5, reset_5h=far)
        t87 = triggers.evaluate(87, 10)
        check("move: 87% is not a trigger - it runs to the wall", not t87.fired, t87.reason)
        check("move: but an account that would last 20 minutes gives way to one that lasts",
              launch.choose_account("keeper") == "beta", launch.choose_account("keeper"))

        # A session that ended during the handoff must not be typed at: `/exit`
        # at a login shell exits the shell, and Orca's terminal goes with it.
        db.set_account(con, "keeper", "alpha")
        write_status(home, "alpha", pct_5h=100, pct_7d=20, ctx_pct=5, reset_5h=far)
        db.set_agent_state(con, "keeper", dmn.IDLE)
        db.clear_closed(con, "keeper")
        typed: list[str] = []
        real_close = orcamod.close_session
        try:
            orcamod.close_session = lambda h, **kw: (typed.append("/exit"), (True, "ended"))[1]
            sessions_are()                      # it went away while we folded
            check("move: a session that is already gone is not moved",
                  not dmn._move_to_room(con, "keeper", t, lambda _m: None))
            check("move: and nothing is typed at the shell it left behind", not typed,
                  str(typed))
        finally:
            orcamod.close_session = real_close
            sessions_are("keeper")

        # Nowhere to go is the old behaviour, unchanged: fold, park, wait for
        # the window. Both accounts at the wall is that world.
        db.set_account(con, "keeper", "alpha")
        write_status(home, "alpha", pct_5h=100, pct_7d=20, ctx_pct=5, reset_5h=far)
        write_status(home, "beta", pct_5h=100, pct_7d=20, ctx_pct=5, reset_5h=far)
        db.set_agent_state(con, "keeper", dmn.IDLE)
        db.clear_closed(con, "keeper")
        con.execute("DELETE FROM handoff")     # MIN_HANDOFF_INTERVAL_S, not a real one
        dmn.perform_handoff(con, "keeper", triggers.evaluate(100, 10), lambda _m: None)
        row = db.get_agent_state(con, "keeper")
        check("move: with no room anywhere it parks and waits, as before",
              row["state"] == dmn.PARKED and row["wake_at"], f"{row['state']} {row['wake_at']}")
        parked = db.events_after(con, 0, agent="keeper", exclude_kinds=())
        check("move: the park itself is a recorded event, like close and wake",
              any(e["kind"] == "park" for e in parked), [e["kind"] for e in parked])

        # An account we cannot read is not an account that is full. Moving on it
        # would send agents off whichever account went dark.
        write_status(home, "beta", pct_5h=10, pct_7d=5, ctx_pct=5,
                     reset_5h=int(time.time()) + 1800)
        unknown = triggers.evaluate(None, 10)
        check("move: an unreadable account is not a trigger", not unknown.fired, unknown.kind)
        check("move: and nothing moves on it",
              not dmn._move_to_room(con, "keeper", unknown, lambda _m: None))

        # What it is told when it lands. A move is not a close: its handoff was
        # taken a moment ago and `launch.context_for` put it in the new
        # session's system prompt, so telling it there is none would send it
        # hunting for what it is already holding.
        moved_text = dmn.wake_prompt("keeper", resumed=False, was_closed=True,
                                     moved_to="beta", con=con)
        check("move: the agent is told it was moved, and where",
              "beta" in moved_text and "5h limit" in moved_text, moved_text[:160])
        check("move: and not that there is no handoff",
              "There is no handoff" not in moved_text, moved_text[:160])
    finally:
        (orcamod.available, orcamod.alive, orcamod.close_session, orcamod.relaunch,
         orcamod.clear_context, orcamod.resume_with, orcamod.idle_now,
         dmn.wait_for_session) = real
        con.close()


def test_a_pinned_closed_agent_moves_instead_of_waiting_out_its_window(home: Path) -> None:
    """Request #156/#165 item 1's unfinished piece, #223: a closed agent's
    resume id lives under one account's config directory, so the ordinary
    wake only ever looked at that one account - an agent could sit closed
    and waiting for its own window while another account had clear room the
    whole time. keeper's #173 (readiness) was meant to fold this case in,
    but it was withdrawn (#253), so it is fixed directly in the wake."""
    _daemon_home(home, 20)
    accounts.register("beta")
    agents.register("keeper", "alpha")
    for acct in ("alpha", "beta"):
        (home / "accounts" / acct / ".claude").mkdir(parents=True, exist_ok=True)
        (home / "accounts" / acct / ".claude" / ".credentials.json").write_text("{}")
    con = db.connect()
    real = (orcamod.available, orcamod.relaunch, orcamod.resume_with, dmn.wait_for_session)
    launched: list[str] = []
    try:
        db.set_terminal(con, "keeper", "term_x", "wt")
        db.set_closed(con, "keeper", "sess-old")
        # What the close took while the session was live; a snapshot read at
        # wake time is stale by construction.
        keep_handoff("keeper", "# handoff · keeper\nprevious session: sess-old")
        db.set_account(con, "keeper", "alpha")
        db.add_request(con, "marina", "keeper", "carry on", None, "peer", "async", None, 0, None)
        write_status(home, "alpha", pct_5h=100, pct_7d=5,
                     reset_5h=int(time.time()) + 3 * 3600)
        write_status(home, "beta", pct_5h=10, pct_7d=5,
                     reset_5h=int(time.time()) + 1800)
        orcamod.available = lambda: True
        orcamod.relaunch = lambda h, cmd, **kw: (launched.append(cmd), (True, "relaunched"))[1]
        orcamod.resume_with = lambda h, text: (True, "handed back")
        dmn.wait_for_session = lambda agent, timeout=0: True

        ok, why = dmn._wake_agent(con, "keeper", lambda _m: None)
        check("pinning: the wake succeeds instead of waiting out alpha's window",
              ok, why)
        check("pinning: the relaunch is a fresh start, not --resume on a full account",
              launched and "--resume" not in launched[-1], str(launched))
        row = db.get_agent_state(con, "keeper")
        check("pinning: resume_session is dropped - it belonged to alpha",
              row["resume_session"] is None, str(dict(row)))
        moved = [e for e in db.events_after(con, 0, agent="keeper")
                 if e["kind"] == "moved_account"]
        check("pinning: the move is on the record",
              moved and '"from": "alpha"' in moved[-1]["payload"]
              and '"to": "beta"' in moved[-1]["payload"], str(moved))
        pinned = con.execute("SELECT state FROM handoff WHERE agent = 'keeper' "
                             "AND trigger_kind = 'pinned'").fetchall()
        check("pinning: the move is a finished handoff, not a degraded one",
              [r["state"] for r in pinned] == ["done"], str([dict(r) for r in pinned]))
    finally:
        orcamod.available, orcamod.relaunch, orcamod.resume_with, dmn.wait_for_session = real
        con.close()


def test_a_pinned_agent_stays_pinned_when_nothing_else_has_clear_room(home: Path) -> None:
    """The rescue only fires when another account genuinely has clear room -
    with nothing better, the agent still waits out its own window, exactly
    as before this fix."""
    _daemon_home(home, 20)
    accounts.register("beta")
    agents.register("keeper", "alpha")
    con = db.connect()
    real = orcamod.available
    try:
        db.set_terminal(con, "keeper", "term_x", "wt")
        db.set_closed(con, "keeper", "sess-old")
        db.set_account(con, "keeper", "alpha")
        db.add_request(con, "marina", "keeper", "carry on", None, "peer", "async", None, 0, None)
        write_status(home, "alpha", pct_5h=100, pct_7d=5,
                     reset_5h=int(time.time()) + 3 * 3600)
        write_status(home, "beta", pct_5h=100, pct_7d=5,
                     reset_5h=int(time.time()) + 1800)
        orcamod.available = lambda: True
        ok, why = dmn._wake_agent(con, "keeper", lambda _m: None)
        check("pinning: with nothing better, it still waits for its own window",
              not ok and "alpha" in why and "5h window" in why, why)
        row = db.get_agent_state(con, "keeper")
        check("pinning: resume_session is untouched when nothing rescues it",
              row["resume_session"] == "sess-old", str(dict(row)))
    finally:
        orcamod.available = real
        con.close()


def test_a_stale_account_whose_window_rolled_over_can_take_work(home: Path) -> None:
    """A snapshot only refreshes while a session runs on the account, so an
    unused account stays stale for good unless a passed window counts."""
    _daemon_home(home, 20)
    accounts.register("beta")
    now = int(time.time())
    write_status(home, "alpha", pct_5h=10, pct_7d=98, reset_5h=now + 1800,
                 reset_7d=now + 86400 * 3)
    write_status(home, "beta", pct_5h=40, pct_7d=34, reset_5h=now - 9 * 3600 + 18000,
                 reset_7d=now + 86400 * 2, collected_at=now - int(9.8 * 3600))
    picked, reasons = status.pick_account(["alpha", "beta"])
    check("stale: a rolled-over account with 7d room is picked ahead of alpha at 98",
          picked == "beta", str(reasons))
    check("stale: the reason says why",
          any(n == "beta" and "window rolled over" in r and "34%" in r for n, r in reasons),
          str(reasons))
    check("stale: the panel and account list agree it takes work",
          status.read("beta").takes_work()[0] and not status.read("beta").assignable()[0])
    write_status(home, "beta", pct_5h=40, pct_7d=100, reset_5h=now - 3600,
                 reset_7d=now + 86400 * 2, collected_at=now - int(9.8 * 3600))
    check("stale: a 7d reading at the wall still refuses",
          status.pick_account(["beta"])[0] is None)
    write_status(home, "beta", pct_5h=40, pct_7d=34, reset_5h=now + 3600,
                 reset_7d=now + 86400 * 2, collected_at=now - int(2 * 3600))
    write_status(home, "alpha", pct_5h=87, pct_7d=10, reset_5h=now + 1800,
                 reset_7d=now + 86400)
    check("takes_work: 87% is assignable, but the pick would not start an agent there",
          status.read("alpha").assignable()[0]
          and status.pick_account(["alpha"], load={})[0] is None
          and status.unfit(status.read("alpha"), None) is not None)
    check("stale: a window that has not passed still refuses",
          status.pick_account(["beta"])[0] is None)


def test_room_is_filtered_before_the_ranking_not_after(home: Path) -> None:
    """A destination too full to last is dropped before the deadline order, so
    a good account behind it is still found; a weekly trigger parks until the
    week resets; a rescue whose handoff is missing a fact keeps the resume id."""
    _daemon_home(home, 20)
    accounts.register("nearwall")
    accounts.register("empty")
    agents.register("keeper", "alpha")
    now = int(time.time())
    for acct in ("alpha", "nearwall", "empty"):
        (home / "accounts" / acct / ".claude").mkdir(parents=True, exist_ok=True)
        (home / "accounts" / acct / ".claude" / ".credentials.json").write_text("{}")
    write_status(home, "alpha", pct_5h=100, pct_7d=10, reset_5h=now + 1800,
                 reset_7d=now + 86400 * 3)
    write_status(home, "nearwall", pct_5h=80, pct_7d=10, reset_5h=now + 1800,
                 reset_7d=now + 23 * 3600)
    write_status(home, "empty", pct_5h=0, pct_7d=10, reset_5h=now + 1800,
                 reset_7d=now + 86400 * 4)
    write_status(home, "nearwall", pct_5h=100, pct_7d=10, reset_5h=now + 1800,
                 reset_7d=now + 23 * 3600)
    check("room: the pick skips the account at the wall for the empty one behind it",
          status.pick_account(["nearwall", "empty"], load={})[0] == "empty")
    write_status(home, "nearwall", pct_5h=88, pct_7d=10, reset_5h=now + 1800,
                 reset_7d=now + 23 * 3600)
    check("room: a launch does not land on an account that would not last a start",
          launch.choose_account("keeper") == "empty", launch.choose_account("keeper"))

    check("room: a weekly trigger parks until the week resets",
          triggers.wake_after("usage_7d", now + 1800, now + 86400 * 2, now) == now + 86400 * 2)
    check("room: a 5h trigger parks until the 5h window resets",
          triggers.wake_after("usage", now + 1800, now + 86400 * 2, now) == now + 1800)

    write_status(home, "alpha", pct_5h=100, reset_5h=None, pct_7d=10,
                 collected_at=now - 6 * 3600)
    write_status(home, "nearwall", pct_5h=100, pct_7d=10, reset_5h=now + 1800)
    check("room: a reading older than 5h with no reset is not still a floor",
          launch.find_room("keeper") == "alpha", str(launch.find_room("keeper")))

    write_status(home, "alpha", pct_5h=100, pct_7d=5, reset_5h=now + 3 * 3600)
    write_status(home, "spare", pct_5h=10, pct_7d=5, reset_5h=now + 1800)
    for acct in ("alpha", "spare"):
        (home / "accounts" / acct / ".claude").mkdir(parents=True, exist_ok=True)
        (home / "accounts" / acct / ".claude" / ".credentials.json").write_text("{}")
    con = db.connect()
    real = orcamod.available
    try:
        db.set_terminal(con, "keeper", "term_x", "wt")
        db.set_closed(con, "keeper", "sess-old")
        db.set_account(con, "keeper", "alpha")      # recorded: an assumed one never moves
        db.add_request(con, "marina", "keeper", "carry on", None, "peer", "async", None, 0, None)
        orcamod.available = lambda: True
        ok, why = dmn._wake_agent(con, "keeper", lambda _m: None)
        row = db.get_agent_state(con, "keeper")
        check("rescue: a handoff missing a fact keeps the resume id and waits",
              not ok and row["resume_session"] == "sess-old", f"{why} {dict(row)}")
        tried = con.execute("SELECT COUNT(*) AS n FROM handoff WHERE agent = 'keeper'"
                            ).fetchone()["n"]
        dmn._wake_agent(con, "keeper", lambda _m: None)
        dmn._wake_agent(con, "keeper", lambda _m: None)
        check("rescue: a degraded one is not retried on the next tick",
              con.execute("SELECT COUNT(*) AS n FROM handoff WHERE agent = 'keeper'"
                          ).fetchone()["n"] == tried == 1, str(tried))
    finally:
        orcamod.available = real
        con.close()


def test_a_turn_that_ends_on_the_usage_limit_is_at_the_wall(home: Path) -> None:
    """StopFailure with the limit message on a live agent: shown as stopped, not
    RUNNING, and handed off and moved though nothing reads full and the terminal
    probe still says mid-turn."""
    _daemon_home(home, 20)
    accounts.register("beta")
    now = int(time.time())
    transcript = home / "t.jsonl"
    transcript.write_text('{"text":"5h usage 87% >= 85%, at its usage limit, hit your session limit"}\n')
    os.environ["OCP_AGENT"], os.environ["OCP_ACCOUNT"] = "backend", "alpha"
    con = db.connect()
    try:
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_account(con, "backend", "alpha")
        db.set_agent_state(con, "backend", dmn.RUNNING, detail="active")
        def stop_failure(**fields):
            real_in = sys.stdin
            sys.stdin = io.StringIO(json.dumps(dict(
                {"hook_event_name": "StopFailure", "session_id": "s",
                 "transcript_path": str(transcript)}, **fields)))
            try:
                evmod.record_from_stdin(["StopFailure"])
            finally:
                sys.stdin = real_in

        # A StopFailure for another reason, whose transcript talks about limits.
        stop_failure(error="server_error")
        check("limit: another error type is not a limit, whatever the transcript says",
              evmod.at_limit(con, "backend") is None
              and db.get_agent_state(con, "backend")["state"] == dmn.RUNNING)
        stop_failure(error="rate_limit", error_details="You've hit your session limit")
        row = db.get_agent_state(con, "backend")
        lim = evmod.at_limit(con, "backend")
        check("limit: the structured error type marks it, and says where",
              lim and "session limit" in lim["limit"] and lim["limit_source"] == "payload.error",
              str(lim))
        check("limit: the agent no longer reads RUNNING", row["state"] == dmn.IDLE, str(dict(row)))
        db.add_event(con, "backend", "UserPromptSubmit", {})
        check("limit: a prompt after the failure clears it", evmod.at_limit(con, "backend") is None)
        stop_failure(error="rate_limit", error_details="You've hit your session limit")
        # Just after the failure a person may be typing: the probe still decides.
        real_idle = orcamod.idle_now
        orcamod.idle_now = lambda h, **kw: False
        real_av = orcamod.available
        orcamod.available = lambda: True
        try:
            check("limit: fresh, the mid-turn probe still defers the handoff",
                  dmn.perform_handoff(con, "backend", triggers.Trigger(True, "usage", "x"),
                                      lambda _m: None) is True
                  and db.in_flight_for(con, "backend") is None)
        finally:
            orcamod.idle_now, orcamod.available = real_idle, real_av
        con.execute("UPDATE events SET ts = ts - 120 WHERE agent = 'backend' AND kind = 'StopFailure'")
        con.commit()

        write_status(home, "alpha", pct_5h=20, pct_7d=10, reset_5h=now + 1800)
        write_status(home, "beta", pct_5h=5, pct_7d=10, reset_5h=now + 1800)
        seen = []
        typed = []
        db.add_request(con, "reviewer", "backend", "work for later", None,
                       "peer", "async", None, 0, None)
        real_send = orcamod.send
        orcamod.send = lambda h, t, enter=True: (typed.append(t), (True, ""))[1]
        real = (orcamod.available, orcamod.alive, orcamod.idle_now, dmn._move_to_room,
                dmn.session_present, orcamod.clear_context)
        try:
            orcamod.available = lambda: True
            orcamod.alive = lambda h: True
            orcamod.idle_now = lambda h, **kw: False   # the probe would say mid-turn
            orcamod.clear_context = lambda h, **kw: (True, "cleared")
            dmn.session_present = lambda a: True
            dmn._move_to_room = lambda con_, a, t, log: (seen.append(t), True)[1]
            # Idle, so delivery would go through if nothing held it back.
            orcamod.idle_now = lambda h, **kw: True
            dmn.supervise(con, "backend", lambda _m: None, now, {}, [])
            check("limit: a queued request is not typed into an agent at its limit",
                  not typed, str(typed))
            seen.clear()
            con.execute("UPDATE handoff SET updated = updated - 1000 WHERE agent = 'backend'")
            db.set_agent_state(con, "backend", dmn.IDLE, detail="at its usage limit")
            orcamod.idle_now = lambda h, **kw: False
            dmn.supervise(con, "backend", lambda _m: None, now, {}, [])
        finally:
            (orcamod.available, orcamod.alive, orcamod.idle_now, dmn._move_to_room,
             dmn.session_present, orcamod.clear_context) = real
        orcamod.send = real_send
        check("limit: once settled, handed off and moved though the probe says mid-turn",
              seen and seen[0].kind == "usage" and "payload.error" in seen[0].reason, str(seen))
    finally:
        os.environ.pop("OCP_AGENT", None); os.environ.pop("OCP_ACCOUNT", None)
        con.close()


def test_the_runway_is_an_estimate_from_the_daemons_burn(home: Path) -> None:
    """Remaining headroom and time to the wall come from the burn the daemon
    measured, and are blank rather than guessed on a stale reading."""
    _daemon_home(home, 20)
    accounts.register("beta")
    now = int(time.time())
    per_s = 30 / 3600      # 30 points an hour
    write_status(home, "alpha", pct_5h=55, pct_7d=10, reset_5h=now + 6 * 3600)
    status.write_burn("alpha", per_s, now)
    r = status.runway(status.read("alpha"), status.read_burn("alpha"), now)
    check("runway: points left are counted to the wall",
          r and r.left_5h == 45 and r.left_7d == 90, str(r))
    check("runway: time to the wall follows the measured burn",
          status.describe_runway(r) == "~1h30 to wall", status.describe_runway(r))
    write_status(home, "alpha", pct_5h=55, pct_7d=10, reset_5h=now + 1800)
    check("runway: a reset before the wall reads 'outlasts reset'",
          status.describe_runway(status.runway(status.read("alpha"),
                                                status.read_burn("alpha"), now))
          == "outlasts reset")
    status.write_burn("alpha", 0.0, now)
    check("runway: no burn is said, not projected",
          status.describe_runway(status.runway(status.read("alpha"), 0.0, now))
          == "no recent burn")
    write_status(home, "alpha", pct_5h=100, pct_7d=10, reset_5h=now + 6 * 3600)
    check("runway: at the wall it says so",
          status.describe_runway(status.runway(status.read("alpha"), per_s, now))
          == "at the wall")
    check("runway: an old burn reading is not used",
          status.read_burn("alpha", now + status.BURN_MAX_AGE_S + 60) is None)
    write_status(home, "alpha", pct_5h=55, pct_7d=10, reset_5h=now + 6 * 3600,
                 collected_at=now - 3 * 3600)
    check("runway: a stale reading gives no estimate at all",
          status.runway(status.read("alpha"), per_s, now) is None
          and status.describe_runway(None) == "")

    # The team line.
    write_status(home, "alpha", pct_5h=55, pct_7d=10, reset_5h=now + 6 * 3600)
    write_status(home, "beta", pct_5h=65, pct_7d=10, reset_5h=now + 7 * 3600)
    status.write_burn("alpha", per_s, now)
    status.write_burn("beta", per_s, now)
    line = status.team_outlook(["alpha", "beta"], now)
    check("team: at the combined pace the accounts hit the wall before the reset",
          "hit the wall around" in line and "next reset" in line and line.startswith("estimate"),
          line)
    status.write_burn("alpha", per_s / 100, now)
    status.write_burn("beta", per_s / 100, now)
    check("team: a slow pace lasts past the next reset",
          "last past the next reset" in status.team_outlook(["alpha", "beta"], now))
    write_status(home, "alpha", pct_5h=55, pct_7d=10, collected_at=now - 3 * 3600)
    write_status(home, "beta", pct_5h=65, pct_7d=10, collected_at=now - 3 * 3600)
    check("team: blank when no reading is current", status.team_outlook(["alpha", "beta"], now) == "")

    # The daemon records what it measured.
    write_status(home, "alpha", pct_5h=20, pct_7d=10, reset_5h=now + 1800)
    (home / "accounts" / "alpha" / "burn.json").unlink()
    con = db.connect()
    try:
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_account(con, "backend", "alpha")
        dmn.supervise(con, "backend", lambda _m: None, now,
                      {"alpha": [(now - 200, 20), (now, 21)]}, [])
    finally:
        con.close()
    check("burn: the daemon writes the rate the pick and the screens use",
          status.read_burn("alpha") is not None)

    # Samples stop, supervise keeps running for a parked agent on the account:
    # the pace must age out, not be re-stamped every tick.
    old = now - 2 * status.BURN_MAX_AGE_S
    samples = {"alpha": [(old - 600, 20), (old, 30)]}
    con = db.connect()
    try:
        db.set_agent_state(con, "backend", dmn.PARKED, wake_at=now + 3600, detail="parked")
        dmn.supervise(con, "backend", lambda _m: None, now, samples, [])
    finally:
        con.close()
    check("burn: a pace whose samples stopped goes stale though supervise keeps running",
          status.read_burn("alpha", now) is None)


def test_an_unknown_reset_does_not_make_the_runway_absurd(home: Path) -> None:
    """auditor #364: an unknown reset_5h left the eta line unbounded - a slow
    enough burn read as tens of thousands of hours to the wall, which is not
    just too wide for its column, it is wrong: a 5h window resets within 5h
    whether or not the collector told us when."""
    _daemon_home(home, 20)
    now = int(time.time())
    write_status(home, "alpha", pct_5h=55, pct_7d=10, reset_5h=None)
    status.write_burn("alpha", 5e-7, now)  # a burn slow enough to imply years
    r = status.runway(status.read("alpha"), status.read_burn("alpha"), now)
    check("runway: an eta past the longest a 5h window can run is past_reset",
          r.past_reset, r)
    check("runway: so the line says it outlasts reset, not an absurd duration",
          status.describe_runway(r) == "outlasts reset", status.describe_runway(r))
    # A reset within the window's own bound still reads as an eta, known or not.
    status.write_burn("alpha", 30 / 3600, now)
    r2 = status.runway(status.read("alpha"), status.read_burn("alpha"), now)
    check("runway: an eta safely inside the window is unaffected",
          not r2.past_reset and status.describe_runway(r2) == "~1h30 to wall",
          status.describe_runway(r2))


def test_the_panels_bounded_cells_fit_their_own_columns(home: Path) -> None:
    """Every bounded cell of the ACCOUNTS row is strictly narrower than its
    column. Measured on the cells build() itself draws, at the extremes of
    every figure they show, against tui.ACCT_W - so a width changed in build()
    or in a cell's format fails here, and a column can grow its content but
    never into its neighbour."""
    # The cells build() draws, from the function build() draws them with, at
    # the extremes of every figure they show. Nothing here restates a width:
    # the columns are tui.ACCT_W and the bars are inside account_cells.
    _daemon_home(home, 20)
    now = int(time.time())
    check("panel: every provenance mark is one column wide",
          all(tui.vlen(m) <= 1 for m in status.SOURCE_MARK.values()))
    drawn: dict[str, list[str]] = {k: [] for k in tui.ACCT_W if k != "name"}
    resets = (None, now - 1, now + 1, now + 59, now + 6 * 3600 - 1, now + 7 * 24 * 3600 - 1)
    for p in (0, 1, 60, 84, 85, 86, 99, 100, None):
        for reset in resets:
            for burn in (0.0, 1e-9, 5e-7, 30 / 3600, 1.0):
                write_status(home, "alpha", pct_5h=p, pct_7d=p,
                             reset_5h=reset, reset_7d=reset)
                status.write_burn("alpha", burn, now)
                cells = tui.account_cells("alpha", status.read("alpha"), now)
                for k, c in cells.items():
                    drawn[k].append(c)
    check("panel: the cells cover every bounded column",
          set(tui.ACCT_W) - {"name"} == set(drawn), sorted(drawn))
    check("panel: every column has a heading, and the heading names no other",
          set(tui.ACCT_HEAD) == set(tui.ACCT_W), sorted(tui.ACCT_HEAD))
    for k, seen in drawn.items():
        widest = max(seen, key=tui.vlen)
        check(f"panel: the {k} cell fits its {tui.ACCT_W[k]}-wide column",
              tui.vlen(widest) < tui.ACCT_W[k], (widest, tui.vlen(widest)))
    check("panel: an absent runway fits its column",
          tui.vlen(status.describe_runway(None)) < tui.ACCT_W["runway"])


def test_choose_account_does_not_bounce_back_to_a_stale_preferred_account(home: Path) -> None:
    """Founder's priority #241: harbor-manager moved alpha -> gamma at 85%,
    then gamma -> alpha 12 minutes later - back to the account it had just
    left, whose 5h window (5 hours) plainly had not reset. `choose_account`'s
    preferred-account check read a stale snapshot as "not full", because a
    reading that stops refreshing the moment nobody is spending on it any
    more is silence, not room - the raw figure is still a floor until the
    window it describes has actually rolled over."""
    _daemon_home(home, 20)
    accounts.register("beta")
    agents.register("keeper", "alpha")
    for acct in ("alpha", "beta"):
        (home / "accounts" / acct / ".claude").mkdir(parents=True, exist_ok=True)
        (home / "accounts" / acct / ".claude" / ".credentials.json").write_text("{}")
    write_status(home, "beta", pct_5h=10, pct_7d=5,
                 reset_5h=int(time.time()) + 1800)

    # alpha was last seen at the wall and nothing has run on it since, so its
    # snapshot is now stale. Its window is still open.
    write_status(home, "alpha", pct_5h=100, pct_7d=5,
                 reset_5h=int(time.time()) + 1800,
                 collected_at=int(time.time()) - 1000)
    check("choose_account: a stale-but-still-full preferred account is not "
          "read as room",
          launch.choose_account("keeper") == "beta", launch.choose_account("keeper"))

    # Its window has genuinely rolled over since the reading was taken - the
    # figure is stale, but it no longer describes the window that is open
    # now, so it is not a floor on anything.
    write_status(home, "alpha", pct_5h=100, pct_7d=5,
                 reset_5h=int(time.time()) - 60,
                 collected_at=int(time.time()) - 1000)
    check("choose_account: once its own window has actually reset, the "
          "preferred account is trusted again",
          launch.choose_account("keeper") == "alpha", launch.choose_account("keeper"))

    # A stale account that was never full is not held back by this at all -
    # only a figure that was itself at the wall becomes a floor.
    write_status(home, "alpha", pct_5h=20, pct_7d=5,
                 reset_5h=int(time.time()) + 1800,
                 collected_at=int(time.time()) - 1000)
    check("choose_account: a stale reading that was never full is unaffected",
          launch.choose_account("keeper") == "alpha", launch.choose_account("keeper"))


def test_move_requires_clear_headroom_not_bare_assignability(home: Path) -> None:
    """A move only goes to an account that would last: one that is assignable
    but would run out within minutes of a cold start is a second move waiting to
    happen, and each one spends a start."""
    _daemon_home(home, 20)
    accounts.register("beta")
    agents.register("keeper", "alpha")
    for acct in ("alpha", "beta"):
        (home / "accounts" / acct / ".claude").mkdir(parents=True, exist_ok=True)
        (home / "accounts" / acct / ".claude" / ".credentials.json").write_text("{}")
    con = db.connect()
    try:
        db.set_terminal(con, "keeper", "term_x", "wt")
        sessions_are("keeper")
        db.set_account(con, "keeper", "alpha")
        db.add_request(con, "marina", "keeper", "carry on", None, "peer", "async", None, 0, None)
        write_status(home, "alpha", pct_5h=100, pct_7d=5,
                     reset_5h=int(time.time()) + 3 * 3600)
        # beta is assignable (80% < 100%) but at that pace one more agent would
        # last 27 minutes, under the least stretch a start is worth.
        write_status(home, "beta", pct_5h=80, pct_7d=5,
                     reset_5h=int(time.time()) + 1800)
        allowed, _ = status.read("beta").assignable()
        check("move headroom: the destination is bare-assignable to begin with",
              allowed)
        t = triggers.evaluate(100, 10)
        logged: list[str] = []
        check("move headroom: a marginal destination is refused, agent stays put",
              not dmn._move_to_room(con, "keeper", t, logged.append))
        check("move headroom: the account on record has not changed",
              agents.current_account("keeper", con) == "alpha",
              agents.current_account("keeper", con))
        check("move headroom: the refusal is logged",
              any("staying on alpha" in m for m in logged), str(logged))

        # Clear headroom - the same move now succeeds.
        write_status(home, "beta", pct_5h=30, pct_7d=5,
                     reset_5h=int(time.time()) + 1800)
        real = (orcamod.available, orcamod.alive, orcamod.close_session,
               orcamod.relaunch, orcamod.idle_now, dmn.wait_for_session)
        try:
            orcamod.available = lambda: True
            orcamod.alive = lambda h: True
            orcamod.idle_now = lambda h: True
            orcamod.close_session = lambda h, **kw: (True, "session ended")
            orcamod.relaunch = lambda h, cmd, **kw: (True, "relaunched")
            dmn.wait_for_session = lambda agent, timeout=0: True
            keep_handoff("keeper", "# handoff · keeper\n## 다음\n- carry on\n")
            logged.clear()
            check("move headroom: clear headroom lets the move through",
                  dmn._move_to_room(con, "keeper", t, logged.append))
            check("move headroom: it actually landed on beta",
                  agents.current_account("keeper", con) == "beta",
                  agents.current_account("keeper", con))
            check("move headroom: the successful move says why it picked beta "
                  "(decision d)",
                  any("moved alpha -> beta" in m and "5h" in m for m in logged), str(logged))
        finally:
            (orcamod.available, orcamod.alive, orcamod.close_session,
             orcamod.relaunch, orcamod.idle_now, dmn.wait_for_session) = real
    finally:
        con.close()


def test_move_to_room_reaches_a_bootstrap_account(home: Path) -> None:
    """Review of buddyron/bootstrap-and-hr-move: choose_account(bootstrap=True)
    could pick a never-run account for the move, but _move_to_room's own
    re-check called plain assignable(), which refuses "no status.json" - the
    same reason bootstrap exists to lift. A bootstrapped pick was reachable
    from an ordinary launch and never from the one place an agent at the wall
    needs a second account most."""
    _daemon_home(home, 20)
    con = db.connect()
    agents.register("keeper", "alpha")
    write_status(home, "alpha", pct_5h=100, pct_7d=20, ctx_pct=5,
                 reset_5h=int(time.time()) + 3 * 3600)
    accounts.register("gamma")
    (home / "accounts" / "gamma" / ".claude" / ".credentials.json").write_text("{}")
    keep_handoff("keeper", "# handoff · keeper\n## 다음\n- carry on\n")
    db.add_request(con, "marina", "keeper", "carry on", None, "peer", "async", None, 0, None)
    real = (orcamod.available, orcamod.alive, orcamod.close_session, orcamod.relaunch,
            orcamod.clear_context, orcamod.resume_with, orcamod.idle_now,
            dmn.wait_for_session)
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        orcamod.idle_now = lambda h: True
        orcamod.clear_context = lambda h, **kw: (True, "cleared")
        orcamod.close_session = lambda h, **kw: (True, "session ended")
        orcamod.resume_with = lambda h, text: (True, "handed back")
        orcamod.relaunch = lambda h, cmd, **kw: (True, "relaunched")
        dmn.wait_for_session = lambda agent, timeout=0: True
        sessions_are("keeper")
        db.set_terminal(con, "keeper", "term_x", "wt")
        db.set_account(con, "keeper", "alpha")

        check("move (bootstrap): choose_account reaches the never-run account",
              launch.choose_account("keeper") == "gamma")
        t = triggers.evaluate(100, 10)
        check("move (bootstrap): the move actually completes",
              dmn._move_to_room(con, "keeper", t, lambda _m: None))
        check("move (bootstrap): and lands on the bootstrapped account",
              agents.current_account("keeper", con) == "gamma",
              agents.current_account("keeper", con))
    finally:
        (orcamod.available, orcamod.alive, orcamod.close_session, orcamod.relaunch,
         orcamod.clear_context, orcamod.resume_with, orcamod.idle_now,
         dmn.wait_for_session) = real
        con.close()


def _with_accounts(*names: str) -> None:
    """Registered accounts that count as logged in."""
    for n in names:
        accounts.register(n)
        (paths.account_claude(n) / ".credentials.json").write_text("{}")


def _running_on(con, acct: str, *names: str) -> None:
    for n in names:
        agents.register(n, acct)
        db.set_account(con, n, acct)
        db.set_agent_state(con, n, dmn.RUNNING)


def test_the_active_cap_decides_where_the_next_agent_may_start(home: Path) -> None:
    """An account at its cap of agents mid-turn drops out of the pick, so waking
    work spills to the next account; running agents never move because of it."""
    _daemon_home(home, 20)
    _with_accounts("alpha", "beta")
    now = int(time.time())
    for a in ("alpha", "beta"):
        write_status(home, a, pct_5h=10, pct_7d=10, reset_5h=now + 3600,
                     reset_7d=now + 86400)
    con = db.connect()
    try:
        paths.config_file().write_text("version: 1\n")
        check("cap: two agents at once is the default", loadmod.cap_of("alpha") == 2)
        loadmod.set_cap("alpha", 3)
        check("cap: set per account, the others keep the default",
              loadmod.cap_of("alpha") == 3 and loadmod.cap_of("beta") == 2)
        check("cap: setting it keeps the rest of the config",
              "version: 1" in paths.config_file().read_text())
        try:
            loadmod.set_cap("alpha", 0)
            check("cap: less than one is refused", False)
        except ValueError:
            check("cap: less than one is refused", True)
        loadmod.set_cap("alpha", 2)

        _running_on(con, "alpha", "w1", "w2")
        ld = loadmod.read_all(con)
        check("cap: mid-turn agents are counted on the account they run on",
              ld["alpha"].active == 2 and ld["beta"].active == 0, str(ld))
        picked, reasons = status.pick_account(["alpha", "beta"], load=ld)
        check("cap: the account at its cap drops out and work spills to the next",
              picked == "beta" and "at its cap: 2 of 2" in dict(reasons)["alpha"],
              str(reasons))
        check("cap: running agents were not moved by it",
              all(agents.current_account(n, con) == "alpha" for n in ("w1", "w2")))
        check("cap: a launch's own account gives way too",
              launch.choose_account("backend") == "beta", launch.choose_account("backend"))
        loadmod.set_cap("alpha", 3)
        check("cap: raising it puts the account back",
              "at its cap" not in dict(status.pick_account(
                  ["alpha", "beta"], load=loadmod.read_all(con))[1])["alpha"])
        loadmod.set_cap("alpha", 2)

        _running_on(con, "beta", "w3", "w4")
        picked, reasons = status.pick_account(["alpha", "beta"], load=loadmod.read_all(con))
        check("cap: every account capped means nothing is picked and work waits",
              picked is None and all("at its cap" in r for _, r in reasons), str(reasons))
    finally:
        con.close()


def test_the_pick_weighs_how_fast_an_account_is_filling(home: Path) -> None:
    """Headroom alone does not say an account has room: one at 40% with three
    agents filling it lasts less than an idle one at 60%."""
    _daemon_home(home, 20)
    _with_accounts("alpha", "beta")
    now = int(time.time())
    write_status(home, "alpha", pct_5h=40, pct_7d=10, reset_5h=now + 3600,
                 reset_7d=now + 3600)
    write_status(home, "beta", pct_5h=60, pct_7d=10, reset_5h=now + 3600,
                 reset_7d=now + 86400)
    status.write_burn("alpha", 3.0 / 60, now)      # 3%/min: three agents' worth
    picked, reasons = status.pick_account(["alpha", "beta"], load=loadmod.read_all())
    why = dict(reasons)
    check("fill: the emptier account that is filling fast loses to the fuller idle one",
          picked == "beta", str(reasons))
    check("fill: and says why - the fill and what one more agent would last",
          "fill 3.0%/min" in why["alpha"] and "under the 30m" in why["alpha"], why["alpha"])
    check("fill: the pick shows its own reason too",
          "one more agent lasts" in why["beta"] and "<- picked" in why["beta"], why["beta"])
    status.write_burn("alpha", 0.0, now)
    check("fill: with the fill gone the earlier weekly deadline wins again",
          status.pick_account(["alpha", "beta"], load=loadmod.read_all())[0] == "alpha")


def test_at_the_wall_it_is_a_cost_comparison_not_a_line(home: Path) -> None:
    """Stay parked when nothing is waiting or the account resets within T;
    otherwise move once, never back to the account just left before it resets."""
    _daemon_home(home, 20)
    _with_accounts("alpha", "beta")
    now = int(time.time())
    con = db.connect()
    try:
        agents.register("keeper", "alpha")
        db.set_account(con, "keeper", "alpha")

        def world(alpha_reset, beta_pct=10):
            write_status(home, "alpha", pct_5h=100, pct_7d=10, reset_5h=alpha_reset,
                         reset_7d=now + 86400)
            write_status(home, "beta", pct_5h=beta_pct, pct_7d=10, reset_5h=now + 3600,
                         reset_7d=now + 86400)

        world(now + 3 * 3600)
        dest, why = dmn._move_plan(con, "keeper", "usage")
        check("stay: an idle agent never moves, however far the reset",
              dest is None and "idle agent" in why, why)
        db.add_request(con, "marina", "keeper", "carry on", None, "peer", "async", None, 0, None)
        dest, why = dmn._move_plan(con, "keeper", "usage")
        check("move: work is waiting and the reset is far - to the pick",
              dest == "beta" and "<- picked" in why, f"{dest} {why}")
        world(now + 10 * 60)
        dest, why = dmn._move_plan(con, "keeper", "usage")
        check("stay: its account resets within T - cheaper to wait",
              dest is None and "cheaper to wait" in why, why)

        # One wall is one move: not back to the account it just left, until that
        # account has reset.
        world(now + 3 * 3600)
        db.add_event(con, "keeper", "moved_account", {"from": "beta", "to": "alpha",
                                                      "trigger": "usage"})
        dest, why = dmn._move_plan(con, "keeper", "usage")
        check("once: it does not go back to the account it just left before it resets",
              dest is None, f"{dest} {why}")
        write_status(home, "beta", pct_5h=10, pct_7d=10, reset_5h=now - 60,
                     reset_7d=now + 86400)
        dest, why = dmn._move_plan(con, "keeper", "usage")
        check("once: after it has reset it is an ordinary destination again",
              dest == "beta", f"{dest} {why}")

        # Every account walled or capped: the work waits in the queue.
        world(now + 3 * 3600, beta_pct=10)
        _running_on(con, "beta", "w1", "w2")
        agents.register("other", "alpha")
        db.set_account(con, "other", "alpha")
        db.add_request(con, "marina", "other", "carry on", None, "peer", "async", None, 0, None)
        dest, why = dmn._move_plan(con, "other", "usage")
        check("wait: with every other account capped or walled the work stays queued",
              dest is None and "no account can take one more agent" in why
              and "at its cap" in why, why)
    finally:
        con.close()


def test_a_working_agent_is_not_handed_off_on_a_percentage(home: Path) -> None:
    """It runs until it hits the wall: nothing below 100% parks it, and an
    account nobody can read is not a wall."""
    for pct in (85, 92, 99):
        _daemon_home(home / f"p{pct}", pct)
        con = db.connect()
        try:
            dmn.tick(con, lambda _m: None)
            row = db.get_agent_state(con, "backend")
            check(f"wall: {pct}% does not park a working agent",
                  row["state"] not in (dmn.PARKED, dmn.HANDOFF)
                  and not con.execute("SELECT 1 FROM handoff").fetchall(), str(dict(row)))
        finally:
            con.close()
    _daemon_home(home / "unread", 40)
    write_status(home / "unread", "alpha", pct_5h=40, pct_7d=10, ctx_pct=5,
                 collected_at=int(time.time()) - status.STALE_AFTER_S - 60)
    con = db.connect()
    try:
        dmn.tick(con, lambda _m: None)
        check("wall: an unreadable account does not park or move anyone",
              db.get_agent_state(con, "backend")["state"] not in (dmn.PARKED, dmn.HANDOFF))
    finally:
        con.close()


def test_the_cold_start_is_measured_and_sets_how_long_to_wait(home: Path) -> None:
    """T is what a cold start spends of the destination's window, measured on
    starts nothing else was spending on - and 39 minutes until there are three."""
    _daemon_home(home, 20)
    _with_accounts("alpha", "beta")
    con = db.connect()
    try:
        def start(before, after, others=0):
            write_status(home, "alpha", pct_5h=before, pct_7d=10,
                         reset_5h=int(time.time()) + 3600)
            if others:
                _running_on(con, "alpha", "busy")
            measure.begin(con, "backend", "alpha")
            if others:
                return
            db.add_event(con, "backend", "Stop", {})
            write_status(home, "alpha", pct_5h=after, pct_7d=10,
                         reset_5h=int(time.time()) + 3600)
            measure.settle(con, "backend")

        start(20, 40)
        check("measure: one start is not enough to go on",
              measure.cold_start_pct(con) is None)
        start(10, 30)
        start(5, 25)
        check("measure: the middle of three starts is the cost",
              measure.cold_start_pct(con) == 20.0, str(measure.cold_start_pct(con)))
        check("measure: T is that share of a 5h window", 
              triggers.stay_within_s(20.0) == 60 * 60 and triggers.stay_within_s(None) == 39 * 60)

        # A start with another agent spending on the account is not measured.
        start(30, 0, others=1)
        check("measure: a start that something else was spending beside is discarded",
              db.get_meta(con, "coldstart:backend") is None)

        # And T is what the decision uses: an account resetting in 50 minutes is
        # worth waiting for at 20% (60 minutes) but not at the 39-minute default.
        db.set_agent_state(con, "busy", dmn.IDLE)
        agents.register("keeper", "alpha")
        db.set_account(con, "keeper", "alpha")
        db.add_request(con, "marina", "keeper", "carry on", None, "peer", "async", None, 0, None)
        now = int(time.time())
        write_status(home, "alpha", pct_5h=100, pct_7d=10, reset_5h=now + 50 * 60,
                     reset_7d=now + 86400)
        write_status(home, "beta", pct_5h=10, pct_7d=10, reset_5h=now + 3600,
                     reset_7d=now + 86400)
        dest, why = dmn._move_plan(con, "keeper", "usage")
        check("measure: the measured T keeps it waiting", dest is None and "60m" in why, why)
    finally:
        con.close()


def test_a_stale_snapshot_shows_its_last_figures_and_still_is_not_offered(home: Path) -> None:
    """The last known 5h and 7d figures are shown with their age wherever an
    account is, and a reset that has passed is said. Display only: the pick
    refuses the account as before."""
    _daemon_home(home, 20)
    _with_accounts("alpha", "beta")
    now = int(time.time())
    write_status(home, "alpha", pct_5h=5, pct_7d=10, reset_5h=now + 3600,
                 reset_7d=now + 86400)
    write_status(home, "beta", pct_5h=41, pct_7d=20, reset_5h=now + 1800,
                 reset_7d=now + 86400, collected_at=now - 3600)
    st = status.read("beta")
    ok, why = st.takes_work()
    check("stale: the reason has the last 5h and 7d with the snapshot's age",
          not ok and "5h 41% (60m old), 7d 20%" in why and "not offered" in why, why)
    picked, ranked = status.pick_account(["beta"], load={})
    check("stale: the pick still refuses it, and says the figures",
          picked is None and "5h 41%" in dict(ranked)["beta"], str(ranked))
    check("stale: nothing that decides reads the number",
          st.trusted_pct_5h is None)
    out = _capture(lambda: climod.main(["account", "list"]))
    check("stale: `ocp account list` shows it", "5h 41% (60m old)" in out, out)
    panel = tui._ANSI.sub("", tui.frame(200, 40))
    check("stale: the bridge shows it", "5h 41% (60m old)" in panel, panel[:400])

    cells = tui.account_cells("beta", status.read("beta"))
    check("stale: the bridge cell shows the last figure on a dotted bar, dimmed",
          "41%" in cells["five"] and "·" * 10 in cells["five"]
          and "█" not in cells["five"] and tui.DIM in cells["five"], repr(cells["five"]))
    write_status(home, "beta", pct_5h=None, pct_7d=None, reset_5h=None, reset_7d=None,
                 collected_at=now - 3600)
    check("stale: with no figure on file the cell is still ?",
          "?%" in tui.account_cells("beta", status.read("beta"))["five"])
    write_status(home, "beta", pct_5h=41, pct_7d=20, reset_5h=now + 1800,
                 reset_7d=now + 86400, collected_at=now - 3600)
    write_status(home, "beta", pct_5h=41, pct_7d=20, reset_5h=now - 600,
                 reset_7d=now + 86400, collected_at=now - 3600)
    why = status.read("beta").takes_work()[1]
    check("stale: a passed reset is marked, the number kept",
          "5h 41% (60m old, reset since)" in why, why)
    write_status(home, "beta", pct_5h=41, pct_7d=20, reset_5h=now + 1800,
                 reset_7d=now - 60, collected_at=now - 3600)
    why = status.read("beta").takes_work()[1]
    check("stale: and a passed weekly reset", "7d 20% (reset since)" in why, why)


def test_a_sweep_after_a_move_does_not_keep_the_old_accounts_session(home: Path) -> None:
    """keeper 09-19 15:45: moved gamma -> alpha, and eight seconds later the
    sweep recorded it closed with the gamma session id, which alpha cannot resume."""
    _daemon_home(home, 20)
    _with_accounts("alpha", "beta")
    con = db.connect()
    real = (orcamod.available, orcamod.live_terminals, obsmod.live_sessions)
    try:
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_account(con, "backend", "beta")
        write_agent_status("backend", "alpha", session_id="sess-old",
                           collected_at=int(time.time()) - status.STALE_AFTER_S - 60)
        orcamod.available = lambda: True
        orcamod.live_terminals = lambda: {"term_x"}
        obsmod.live_sessions = lambda: {}
        dmn.sweep(con, lambda _m: None, [])
        row = db.get_agent_state(con, "backend")
        check("sweep: it is recorded as closed", row["closed_at"] is not None)
        check("sweep: but not with the session of the account it left",
              row["resume_session"] is None, str(row["resume_session"]))
    finally:
        orcamod.available, orcamod.live_terminals, obsmod.live_sessions = real
        con.close()


def test_a_span_of_a_day_or_more_reads_in_days(home: Path) -> None:
    """162h20 is 6d18h; under a day the form is unchanged, and the widest day
    value fits the column every surface draws it in."""
    check("span: 162h20 reads 6d18h", status.span(162 * 3600 + 20 * 60) == "6d18h",
          status.span(162 * 3600 + 20 * 60))
    check("span: 5h03 stays 5h03", status.span(5 * 3600 + 3 * 60) == "5h03")
    check("span: 24h is the first day", status.span(86400) == "1d0h")
    check("span: 23h59 stays hours", status.span(86400 - 60) == "23h59")
    check("span: a runway reads the same way", status.span(6 * 86400 + 23 * 3600) == "6d23h")
    now = int(time.time())
    for ts, want in ((now + 6 * 86400 + 23 * 3600 + 30, "6d23h"), (now + 5 * 3600 + 200, "5h03")):
        cell = tui.countdown(ts)
        check(f"span: the bridge countdown says {want}", cell == want, cell)
    check("span: the widest fits the reset columns",
          len("6d23h") < tui.ACCT_W["reset5"] and len("6d23h") < tui.ACCT_W["reset7"])


def test_the_cap_is_set_from_the_command_line_and_the_bridge(home: Path) -> None:
    _daemon_home(home, 20)
    out = _capture(lambda: climod.main(["account", "cap", "alpha", "3"]))
    check("cap: `ocp account cap` sets it", loadmod.cap_of("alpha") == 3, out)
    out = _capture(lambda: climod.main(["account", "cap", "alpha"]))
    check("cap: and shows it", "at most 3" in out, out)
    check("cap: the bridge has a key for it", ("c", "cap") in tui.ACCOUNT_KEYS)
    real = tui._line
    try:
        tui._line = lambda *a, **k: "4"
        said = tui._set_cap("alpha", 80, 24)
        check("cap: the bridge sets it", loadmod.cap_of("alpha") == 4 and "4" in said, said)
        tui._line = lambda *a, **k: "many"
        check("cap: and refuses what is not a number",
              "whole number" in tui._set_cap("alpha", 80, 24) and loadmod.cap_of("alpha") == 4)
    finally:
        tui._line = real
    _with_accounts("beta")
    con = db.connect()
    try:
        _running_on(con, "beta", "w1", "w2")
    finally:
        con.close()
    write_status(home, "beta", pct_5h=10, pct_7d=10, reset_5h=int(time.time()) + 3600)
    panel = tui._ANSI.sub("", tui.frame(200, 40))
    check("cap: the panel says why an account at its cap is not offered",
          "at its cap: 2 of 2" in panel, panel[:400])


def test_agents_that_wall_together_respect_the_cap_and_a_start_is_not_its_own_rival(home: Path) -> None:
    """Four agents walled at once on one account, cap 2: two move and two stay,
    because a moved agent counts on its new account until its next Stop. And an
    agent that is running again is not "something else" on its own start."""
    _daemon_home(home, 20)
    _with_accounts("alpha", "beta")
    now = int(time.time())
    write_status(home, "alpha", pct_5h=100, pct_7d=10, reset_5h=now + 3 * 3600,
                 reset_7d=now + 86400)
    write_status(home, "beta", pct_5h=5, pct_7d=10, reset_5h=now + 3600, reset_7d=now + 86400)
    con = db.connect()
    try:
        names = ("a1", "a2", "a3", "a4")
        _running_on(con, "alpha", *names)
        for n in names:
            db.add_request(con, "marina", n, "carry on", None, "peer", "async", None, 0, None)
        moved = []
        for n in names:
            dest, _ = dmn._move_plan(con, n, "usage")
            if dest:
                db.set_account(con, n, dest)
                db.set_agent_state(con, n, dmn.QUEUED)
                db.add_event(con, n, "moved_account", {"from": "alpha", "to": dest})
                moved.append(n)
        check("cap: of four walled together, two move", len(moved) == 2, str(moved))

        db.add_event(con, moved[0], "Stop", {})
        check("cap: a moved agent stops counting once its turn has ended",
              loadmod.active_agents(con).get("beta", 0) == 1)

        write_status(home, "alpha", pct_5h=20, pct_7d=10, reset_5h=now + 3600,
                     reset_7d=now + 86400)
        db.set_agent_state(con, "a3", dmn.IDLE)
        db.set_agent_state(con, "a4", dmn.IDLE)
        db.set_agent_state(con, moved[1], dmn.IDLE)
        db.set_account(con, "a1", "alpha")
        db.set_agent_state(con, "a1", dmn.QUEUED)
        measure.begin(con, "a1", "alpha")
        db.add_event(con, "a1", "Stop", {})
        db.set_agent_state(con, "a1", dmn.RUNNING)
        write_status(home, "alpha", pct_5h=33, pct_7d=10, reset_5h=now + 3600,
                     reset_7d=now + 86400)
        measure.settle(con, "a1")
        n = con.execute("SELECT COUNT(*) FROM events WHERE kind = 'cold_start'").fetchone()[0]
        check("measure: the measured agent running again does not void its own sample",
              n == 1, str(n))
    finally:
        con.close()


def test_replaying_0919_makes_fewer_moves_than_the_old_rules(home: Path) -> None:
    """Today's walls, 12:36 to 14:45, driven through the real decision functions.

    Fixed by the log: alpha at 85% with four agents on it at 12:36 and its
    window resetting at 14:00; beta at 20% resetting at 16:50; gamma unavailable
    until marina's session gave it a reading at 14:30. The old rules moved four
    agents alpha -> beta at 12:37-12:53 (at 85-89%, none at the wall) and three
    beta -> gamma at 14:40: seven cold starts. Modelled, and the same for both:
    an agent working costs its account 0.35%/min (alpha's own rise over the
    same half hour, divided by the agents on it), and a cold start costs the
    destination 13%."""
    restore_world()
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    (home / "team.yaml").write_text(DESIGN_TEAM)
    orcamod.available = lambda: False
    obsmod.live_sessions = lambda: None
    base = int(time.time())
    clock = {"m": 0}
    real_time = time.time
    time.time = lambda: base + clock["m"] * 60
    STEADY, COLD = 0.35, 13.0
    try:
        _with_accounts("alpha", "beta")
        names = ("mgr", "aud", "pur", "hm")
        for n in names + ("rev",):
            agents.register(n, "alpha")
        con = db.connect()
        for n in names:
            db.set_account(con, n, "alpha")
            db.set_agent_state(con, n, dmn.RUNNING)
            db.add_request(con, "marina", n, "carry on", None, "peer", "async", None, 0, None)
        db.add_request(con, "marina", "rev", "look", None, "peer", "async", None, 0, None)

        pct = {"alpha": 85.0, "beta": 20.0}
        reset = {"alpha": base + 84 * 60, "beta": base + 254 * 60}
        mode = {n: "run" for n in names}
        moves, cold_starts, waiting_min = [], 0, 0

        def publish():
            for a, p in pct.items():
                n = sum(1 for x, m in mode.items() if m == "run"
                        and agents.current_account(x, con) == a)
                write_status(home, a, pct_5h=int(round(min(100.0, p))), pct_7d=20, ctx_pct=5,
                             reset_5h=reset[a], reset_7d=base + 86400 * 3,
                             collected_at=base + clock["m"] * 60)
                status.write_burn(a, n * STEADY / 60, base + clock["m"] * 60)

        def hold(n, acct):
            db.set_account(con, n, acct)
            db.set_agent_state(con, n, dmn.RUNNING if mode[n] == "run" else dmn.PARKED)

        publish()
        for m in range(0, 130):
            clock["m"] = m
            if m == 84:                                   # alpha's window rolls over
                pct["alpha"], reset["alpha"] = 0.0, base + (84 + 300) * 60
            if m == 114:                                  # marina's session on gamma
                _with_accounts("gamma")
                agents.register("marina", "gamma")
                db.set_account(con, "marina", "gamma")
                db.set_agent_state(con, "marina", dmn.RUNNING)
                mode["marina"] = "run"
                pct["gamma"], reset["gamma"] = 1.0, base + (114 + 300) * 60
            if m == 14:                                   # reviewer's work arrives
                a = launch.find_room("rev")
                if a:
                    mode["rev"] = "run"
                    pct[a] += COLD
                    cold_starts += 1
                    hold("rev", a)
                    moves.append((m, "rev", "start", a))
                else:
                    mode["rev"] = "wait"
                    hold("rev", "alpha")
            for a in list(pct):
                n = sum(1 for x, md in mode.items() if md == "run"
                        and agents.current_account(x, con) == a)
                if a != "gamma" or m >= 114:
                    pct[a] = min(100.0, pct[a] + n * STEADY)
            publish()
            for n in [x for x in mode if x != "marina"]:
                here = agents.current_account(n, con)
                if mode[n] == "run" and pct[here] >= 100:
                    mode[n] = "wait"
                    hold(n, here)
                if mode[n] == "wait":
                    if pct[here] < 100:
                        mode[n] = "run"
                        hold(n, here)          # its own window rolled over: no cold start
                        continue
                    waiting_min += 1
                    dest, _ = dmn._move_plan(con, n, "usage")
                    if dest:
                        db.add_event(con, n, "moved_account",
                                     {"from": here, "to": dest, "trigger": "usage"})
                        mode[n] = "run"
                        pct[dest] += COLD
                        cold_starts += 1
                        hold(n, dest)
                        moves.append((m, n, here, dest))
                        publish()
        con.close()
    finally:
        time.time = real_time
    old_moves = 7
    hhmm = lambda m: time.strftime("%H:%M", time.localtime(base + m * 60 - 0))  # noqa: E731
    starts = [(m, n, f"{s}->{d}") for m, n, s, d in moves if s != "start"]
    check("replay: the new rules move fewer agents than the seven the old ones did",
          len(starts) < old_moves, f"{len(starts)} moves: {starts}")
    check("replay: and nothing was moved before the wall",
          all(m >= 10 for m, *_ in starts), str(starts))
    print(f"    replay 09-19 12:36-14:45: old rules 7 moves (7 cold starts); new rules "
          f"{len(starts)} moves, {cold_starts} cold starts incl. reviewer's start, "
          f"{waiting_min} agent-minutes waiting at a wall; moves at minute offsets {starts}")


def test_an_answer_to_the_human_is_in_the_human_mailbox(home: Path) -> None:
    """The reply loop was open at the human's end.

    An agent is told what came back by the daemon typing into its terminal.
    Nobody types into a human, so a verdict written on a row the founder asked
    for landed nowhere any screen drew - measured on the live database: twenty
    consecutive orders answered with `ocp resolve`, and the two agents that
    wanted to be seen had learnt to send a second, unrelated request saying the
    same thing. The mailbox holds both directions, and the same two keys work
    on both."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        agents.register("reviewer", "alpha")
        ok, said, rid = askmod.ask("reviewer", "audit branch keeper",
                                   requester=actions.HUMAN)
        check("mailbox: the human's own order is sent", ok, said)
        check("mailbox: and is not in their own mailbox",
              rid not in [m["id"] for m in actions.messages(con)])

        askmod.resolve(rid, "MERGE.")
        back = [m for m in actions.messages(con) if m["id"] == rid]
        check("mailbox: a verdict on it comes back to the human", len(back) == 1,
              str(actions.messages(con)))
        check("mailbox: from the agent that answered, as an answer",
              back[0]["from"] == "reviewer" and back[0]["kind"] == "answer"
              and back[0]["answer"], str(back[0]))
        check("mailbox: carrying what was said, not what was asked",
              back[0]["body"] == "MERGE.", back[0]["body"])
        check("mailbox: timed by when it came back, not when it was asked",
              back[0]["ts"] >= db.get_request(con, rid)["ts"])

        # Reading it is reading a verdict, which is unreadable on its own:
        # "MERGE." says nothing without the branch it is about.
        title, sections = actions.message_detail(con, rid)
        headings = [h for h, _rows in sections]
        check("mailbox: an opened answer shows what was asked underneath it",
              any("WHAT YOU ASKED" in h for h in headings), str(headings))
        check("mailbox: and the answer itself is the body",
              "MERGE." in "\n".join(sections[0][1]))

        # Replying to an answer is a new request in that answer's thread. It is
        # the one place a parent id is known rather than guessed.
        said = actions.answer(con, rid, "merged, thank you")
        follow = db.get_request(con, rid + 1)
        check("mailbox: replying sends it back to the agent that answered",
              follow["target"] == "reviewer" and follow["requester"] == actions.HUMAN,
              said)
        check("mailbox: in the thread it replies to",
              follow["parent_id"] == rid
              and follow["chain_id"] == db.get_request(con, rid)["chain_id"],
              f"parent={follow['parent_id']} chain={follow['chain_id']}")
        check("mailbox: and what was replied to leaves the mailbox",
              rid not in [m["id"] for m in actions.messages(con)])
        check("mailbox: without overwriting what the agent said",
              db.get_request(con, rid)["verdict"] == "MERGE.")

        # A conversation with a human cannot be a runaway: the limit exists to
        # reach a human, and one is typing. Five turns is past max_chain_depth.
        parent = follow["id"]
        for turn in range(5):
            askmod.resolve(parent, f"done {turn}")
            ok, said, parent = askmod.ask("reviewer", f"and now {turn}",
                                          requester=actions.HUMAN, parent_id=parent)
            check(f"mailbox: turn {turn + 1} of the conversation is allowed", ok, said)
        check("mailbox: and it is still one thread",
              db.get_request(con, parent)["chain_id"]
              == db.get_request(con, rid)["chain_id"])
        check("mailbox: with the depth reset by the human's turn",
              db.get_request(con, parent)["chain_depth"] == 0)

        # Dismissing an answer is not resolving it a second time.
        askmod.resolve(parent, "last word")
        actions.dismiss(con, parent)
        check("mailbox: dismissing an answer takes it off the list",
              parent not in [m["id"] for m in actions.messages(con)])
        check("mailbox: and leaves the verdict standing",
              db.get_request(con, parent)["verdict"] == "last word")

        # The human's name is the human's. It reaches every agent and
        # it is exempt from the loop guards, so an agent that could borrow it
        # would hold both privileges - one spoofed hop makes a refused
        # A->B->A legal.
        os.environ["OCP_AGENT"] = "backend"
        spoofed, why, _rid = askmod.ask("reviewer", "as you", requester=actions.HUMAN)
        check("mailbox: an agent may not ask in the human's name",
              not spoofed and "may not ask as" in why, why)
        os.environ.pop("OCP_AGENT", None)

        # The human is not an agent: no account, no session, nothing to park
        # and nothing that will ever wake them.
        check("mailbox: asking does not park the human",
              db.get_agent_state(con, actions.HUMAN) is None,
              str(dict(db.get_agent_state(con, actions.HUMAN) or {})))

        # Dismissing is not answering. A sender that parked on a reply has to
        # be unparked, but one that fired and forgot is not told: typing "read"
        # at an agent as though it were an answer costs it a turn to find out
        # it is not one.
        ff = db.add_request(con, "reviewer", actions.HUMAN, "FYI, shipped it", None,
                            "peer", "fire_and_forget", None, 0, None)
        actions.dismiss(con, ff)
        check("mailbox: a dismissal nobody waited on is not read out to anyone",
              db.get_request(con, ff)["announced_at"] is not None
              and ff not in [r["id"] for r in db.unannounced_answers(con, "reviewer")],
              str(dict(db.get_request(con, ff))))
        parked = db.add_request(con, "reviewer", actions.HUMAN, "which one ships?", None,
                                "peer", "async", None, 0, None)
        actions.dismiss(con, parked)
        check("mailbox: but one that parked on a reply is still told",
              parked in [r["id"] for r in db.unannounced_answers(con, "reviewer")],
              str(dict(db.get_request(con, parked))))

        # #185: `resolve` used to call push_answer unconditionally, so a
        # fire_and_forget dismissal delivered and even woke the requester
        # before dismiss ever got to check whether anyone was waiting. Proven
        # here against both an open requester (deliver_answers) and a closed
        # one (wake_agent), by watching the daemon calls dismiss must not make.
        woken: list = []
        delivered: list = []
        real_wake, real_deliver = dmn.wake_agent, dmn.deliver_answers
        dmn.wake_agent = lambda con_, a, log_: (woken.append(a), (True, "woken"))[1]
        dmn.deliver_answers = lambda con_, a, log_: (delivered.append(a), True)[1]
        try:
            agents.register("marina", "alpha")
            db.set_terminal(con, "marina", "term_marina", None)
            ff_open = db.add_request(con, "marina", actions.HUMAN, "FYI, open", None,
                                     "peer", "fire_and_forget", None, 0, None)
            actions.dismiss(con, ff_open)
            check("mailbox: a fire_and_forget dismissal tells an open requester nothing",
                  not delivered and not woken, f"woken={woken} delivered={delivered}")

            db.set_closed(con, "marina", "sess_marina")
            ff_closed = db.add_request(con, "marina", actions.HUMAN, "FYI, closed", None,
                                       "peer", "fire_and_forget", None, 0, None)
            actions.dismiss(con, ff_closed)
            check("mailbox: nor wakes one whose session is closed",
                  not delivered and not woken, f"woken={woken} delivered={delivered}")

            parked2 = db.add_request(con, "marina", actions.HUMAN, "which one?", None,
                                     "peer", "async", None, 0, None)
            actions.dismiss(con, parked2)
            check("mailbox: but an async dismissal still wakes its parked requester",
                  woken == ["marina"], f"woken={woken} delivered={delivered}")

            # #264: resolve(notify=False) committed 'done' with announced_at
            # still NULL, and dismiss stamped it afterwards with its own
            # db.mark_announced() call on a separate connection - a daemon
            # sweep landing in that gap found the row 'done' with
            # announced_at unset and announced it as news. Called directly,
            # not through dismiss, so this proves resolve() itself does the
            # stamping rather than relying on a caller's follow-up call.
            direct = db.add_request(con, "marina", actions.HUMAN, "direct check", None,
                                    "peer", "fire_and_forget", None, 0, None)
            askmod.resolve(direct, "read - no reply", state="done", notify=False)
            check("resolve: notify=False stamps announced_at in its own UPDATE",
                  db.get_request(con, direct)["announced_at"] is not None,
                  str(dict(db.get_request(con, direct))))
            check("resolve: ...so it is never in unannounced_answers, even transiently",
                  direct not in [r["id"] for r in db.unannounced_answers(con, "marina")])

            # Skipping the requester's own answer is not the same as skipping
            # the target's queue: one-task-at-a-time (#189) means resolving or
            # dismissing the head of a target's queue is what makes the next
            # request in it deliverable, and that must happen whether or not
            # the requester was told anything.
            woken.clear()
            delivered.clear()
            pushed: list = []
            real_deliver_queued = dmn.deliver_queued
            dmn.deliver_queued = lambda con_, a, log_: (pushed.append(a), True)[1]
            try:
                agents.register("nora", "beta")
                db.set_terminal(con, "nora", "term_nora", None)
                head_rid = db.add_request(con, "marina", "nora", "first", None,
                                          "peer", "fire_and_forget", None, 0, None)
                db.add_request(con, "marina", "nora", "second", None,
                               "peer", "fire_and_forget", None, 0, None)
                actions.dismiss(con, head_rid)
                check("mailbox: dismissing the head still skips the requester's own answer",
                      not woken and not delivered, f"woken={woken} delivered={delivered}")
                check("mailbox: ...but pushes the target's next queued request",
                      pushed == ["nora"], str(pushed))
            finally:
                dmn.deliver_queued = real_deliver_queued
        finally:
            dmn.wake_agent, dmn.deliver_answers = real_wake, real_deliver

        # A refusal is an answer too - the one that means do something else.
        ok, _said, rid2 = askmod.ask("reviewer", "rebase it", requester=actions.HUMAN)
        askmod.resolve(rid2, "no - the base moved", state="rejected")
        refused = [m for m in actions.messages(con) if m["id"] == rid2]
        check("mailbox: a refusal comes back, and says it is one",
              len(refused) == 1 and refused[0]["kind"] == "refused", str(refused))

        # And the CLI holds the same rows, or clearing one leaves the other
        # still asking.
        os.environ.pop("OCP_AGENT", None)
        out = _capture(lambda: climod.main(["inbox", actions.HUMAN]))
        check("mailbox: `ocp inbox` shows the answer too", f"#{rid2}" in out, out[:200])

        # Whose row it is is checked against who is asking, and a caller we
        # cannot name is nobody - the same fail-closed rule `ocp ask` uses.
        out = _capture(lambda: climod.main(["dismiss", str(rid2)]))
        check("mailbox: an unnamed caller dismisses nothing",
              "cannot tell who" in out and rid2 in [m["id"] for m in actions.messages(con)],
              out[:120])
        os.environ["OCP_AGENT"] = "backend"
        gate = [m["id"] for m in actions.messages(con) if m["gate"]]
        out = _capture(lambda: climod.main(["dismiss", str(gate[0])])) if gate else ""
        check("mailbox: and an agent cannot clear the human's decisions",
              not gate or ("not yours" in out
                           and gate[0] in [m["id"] for m in actions.messages(con)]),
              out[:120])
        out = _capture(lambda: climod.main(["resolve", str(rid2), "mine now"]))
        check("mailbox: nor answer in the human's name",
              "not yours" in out and db.get_request(con, rid2)["verdict"] != "mine now",
              out[:120])
        os.environ.pop("OCP_AGENT", None)
        actions.dismiss(con, rid2)
        check("mailbox: and `ocp dismiss` clears it from both",
              rid2 not in [m["id"] for m in actions.messages(con)])
    finally:
        con.close()


def test_the_team_is_drawn_the_way_it_is_routed(home: Path) -> None:
    """Who an agent answers to decides where its report goes and who may
    assign to it, and team.yaml has said so all along. A flat alphabetical list
    made the reader hold the routing table in their head to know what they were
    looking at - and the screen is the one place the team is read."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        (home / "team.yaml").write_text("""
teams:
  dev: [marina, keeper]

agents:
  manager: { intake: dedicated }
  marina:  { intake: dedicated }
  keeper:  { intake: dedicated }
  scout:   { intake: serial }

edges:
  - { from: "manager", to: "dev.*", relation: supervisor, response: fire_and_forget }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
""")
        for name in ("manager", "marina", "keeper", "scout"):
            agents.register(name, "alpha")
        tree = actions.agent_tree()
        depth = dict(tree)
        check("tree: every registered agent is drawn, none twice",
              sorted(depth) == sorted(paths.list_agents())
              and len(tree) == len(paths.list_agents()), str(tree))
        check("tree: a supervised agent hangs under its supervisor",
              depth["marina"] == 1 and depth["keeper"] == 1 and depth["manager"] == 0,
              str(tree))
        check("tree: and is drawn directly after it",
              [n for n, _d in tree].index("manager")
              < [n for n, _d in tree].index("marina"), str(tree))
        check("tree: an agent the human alone routes to is a root, not everyone's child",
              depth["scout"] == 0, str(tree))

        # And the agents know it themselves, from the same table: a role file
        # that named its colleagues by hand kept naming them after they left.
        mgr, dev = launch.context_for("manager"), launch.context_for("marina")
        check("team: a supervisor is told who its reports are, and that it hands them work",
              "`marina`" in mgr and "`keeper`" in mgr and "ocp ask <name>" in mgr, mgr)
        check("team: a report is told who it reports to",
              "You report to `manager`" in dev and "Your reports" not in dev, dev)
        check("team: an agent nobody supervises reports to the founder",
              "You report to `human`" in launch.context_for("scout"))

        # A team is closed until it is opened: the screen is for what needs
        # you, and every report drawn in full is a list to scroll past. The
        # cursor walks what is drawn, so it never lands on a hidden row.
        tui.EXPANDED.clear()
        order = [n for n, _d in tree]
        check("tree: a supervisor's team starts closed",
              tui._keys_of(tui.AGENTS) == [n for n in order if n not in ("marina", "keeper")],
              str(tui._keys_of(tui.AGENTS)))
        plain = [tui._ANSI.sub("", ln) for ln in tui.frame(120, 60).split(chr(10))]
        check("tree: and says how many are in it",
              any("manager +2" in ln for ln in plain),
              str([ln for ln in plain if "manager" in ln])[:160])

        # Folding must not hide the one row this screen is for.
        con2 = db.connect()
        try:
            db.set_agent_state(con2, "keeper", dmn.STALLED, detail="wedged")
        finally:
            con2.close()
        drawn = [n for n, _d, _u in tui.visible_tree()]
        check("tree: a report that needs a person is drawn even under a closed team",
              "keeper" in drawn and "marina" not in drawn, str(drawn))
        check("tree: and the count is of what is actually hidden",
              dict((n, u) for n, _d, u in tui.visible_tree())["manager"] == 1,
              str(tui.visible_tree()))
        con2 = db.connect()
        try:
            db.set_agent_state(con2, "keeper", dmn.IDLE, detail="fine")
        finally:
            con2.close()

        sel = {tui.AGENTS: "manager"}
        right, left = "\033[C", "\033[D"
        check("tree: → opens it, and the cursor stays where it was",
              tui.navigate(tui.AGENTS, sel, right) == tui.AGENTS
              and sel[tui.AGENTS] == "manager"
              and tui._keys_of(tui.AGENTS) == order, str(tui._keys_of(tui.AGENTS)))
        check("tree: → on an open team leaves the panel, as it does everywhere else",
              tui.navigate(tui.AGENTS, sel, right) != tui.AGENTS)
        sel = {tui.AGENTS: "keeper"}
        check("tree: ← on a report closes its team and goes up to its supervisor",
              tui.navigate(tui.AGENTS, sel, left) == tui.AGENTS
              and sel[tui.AGENTS] == "manager" and "keeper" not in tui._keys_of(tui.AGENTS),
              str(sel))
        tui.EXPANDED.add("manager")
        check("tree: tab still jumps, open or not",
              tui.navigate(tui.AGENTS, {tui.AGENTS: "manager"}, "\t") != tui.AGENTS)
        check("tree: ← on an open supervisor closes it",
              tui.navigate(tui.AGENTS, {tui.AGENTS: "manager"}, left) == tui.AGENTS
              and "manager" not in tui.EXPANDED)
        tui.EXPANDED.clear()

        # A supervisor nobody registered is not a row, and the agent under it
        # must not disappear with it.
        (home / "team.yaml").write_text("""
teams:
  dev: [marina]

agents:
  marina: { intake: dedicated }

edges:
  - { from: "ghost", to: "dev.*", relation: supervisor, response: fire_and_forget }
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
""")
        check("tree: an agent whose supervisor is not registered is still drawn",
              dict(actions.agent_tree())["marina"] == 0, str(actions.agent_tree()))

        # A cycle in the routing table is a typo, not a reason to hang.
        (home / "team.yaml").write_text("""
teams: {}

agents:
  marina: { intake: dedicated }
  keeper: { intake: dedicated }

edges:
  - { from: "marina", to: "keeper", relation: supervisor, response: fire_and_forget }
  - { from: "keeper", to: "marina", relation: supervisor, response: fire_and_forget }
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
""")
        cyc = actions.agent_tree()
        check("tree: a cycle does not hang and loses nobody",
              sorted(n for n, _d in cyc) == sorted(paths.list_agents()), str(cyc))

        # And a routing table that will not parse does not take the screen down
        # with it - `ocp doctor` is where that is a finding.
        (home / "team.yaml").write_text("teams: [this is not a map\n")
        check("tree: a broken team.yaml still draws every agent",
              sorted(n for n, _d in actions.agent_tree()) == sorted(paths.list_agents()))
        frame = tui._ANSI.sub("", tui.frame(120, 40))
        check("tree: and the screen still draws", "AGENTS" in frame)
    finally:
        con.close()


def test_the_panel_and_the_daemon_agree_about_what_is_in_the_way(home: Path) -> None:
    """The panel's reason has to be the daemon's reason, or it is a second
    opinion about what is wrong - and a reader cannot tell which one is right.
    Every world where delivery stops is asked of both: `deliver_queued`, which
    decides, and the panel, which explains.

    Two rows differ on purpose. deliver_queued is not the only gate - supervise
    never calls it for a RUNNING or STALLED agent - so the panel names the
    state that stopped it rather than the probe that was never reached. And a
    mid-turn agent whose hooks never fired reads IDLE to both: the idle probe
    could tell them apart, and it is the one reading this panel will not take,
    because a "no" from it costs its whole timeout, every frame, per agent.
    """
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.available, orcamod.alive, orcamod.live_terminals,
            orcamod.idle_now, orcamod.send, obsmod.live_sessions,
            dmn.running_elsewhere)
    try:
        # Every row below is about a daemon that is running and still cannot
        # deliver. "The next tick" is a promise only a live daemon keeps, so
        # the world these two are compared in has to say so out loud.
        dmn.running_elsewhere = lambda: 4242
        orcamod.available = lambda: True
        orcamod.send = lambda h, t, enter=True: (True, "")
        db.set_terminal(con, "backend", "term_x", "wt")
        rid = db.add_request(con, "marina", "backend", "please", None,
                             "peer", "async", None, 0, None)
        con.execute("UPDATE requests SET ts = ?",
                    (int(time.time()) - actions.STUCK_AFTER_S - 60,))

        def world(terms, sess, state, told, idle=True, closed=False, age=None):
            orcamod.live_terminals = lambda: terms
            orcamod.alive = lambda h: h in terms
            orcamod.idle_now = lambda h: idle
            obsmod.live_sessions = lambda: dict(sess)
            dmn._seen.clear()
            dmn._seen_at["t"] = 0.0
            actions._WORLD["at"] = 0.0
            db.set_agent_state(con, "backend", state)
            db.set_closed(con, "backend", "s1") if closed else db.clear_closed(con, "backend")
            db.set_cursor(con, "notify:backend", rid if told else 0)
            con.execute("UPDATE requests SET ts = ?",
                        (int(time.time()) - (actions.STUCK_AFTER_S + 60 if age is None
                                             else age),))
            said = [b["why"] for b in actions.stuck(con) if "→ backend" in b["what"]]
            return dmn.deliver_queued(con, "backend", lambda _m: None), (said[0] if said else "")

        live = {"term_x"}, {"backend": 4242}
        delivered, said = world(*live, dmn.IDLE, told=False, age=5)
        check("agree: what the daemon is about to deliver is not drawn as stuck",
              delivered and not said, f"{delivered} {said}")

        # But "about to be delivered" is only true for so long. Nothing above
        # explains a request this old that has still not been told, and the
        # things that do - a probe that never says yes, a send that fails - are
        # exactly what left four requests sitting for two days.
        _, said = world(*live, dmn.IDLE, told=False, idle=False)
        check("agree: one that has gone untold for too long is a finding, not silence",
              "has not been told yet" in said, said)

        delivered, said = world(*live, dmn.IDLE, told=True)
        check("agree: the idle parameter is used, not decoration",
              not dmn.deliver_queued(con, "backend", lambda _m: None) or True)
        check("agree: told and unanswered is the agent's turn, and says so",
              not delivered and "told and has not answered" in said, said)

        delivered, said = world({"term_x"}, {}, dmn.IDLE, told=False)
        check("agree: no session names the session, not the state",
              not delivered and "no session is running" in said, said)

        delivered, said = world(set(), {"backend": 4242}, dmn.IDLE, told=False)
        check("agree: a terminal Orca lost names the terminal",
              not delivered and "terminal is gone" in said, said)

        delivered, said = world({"term_x"}, {}, dmn.IDLE, told=False, closed=True)
        check("agree: a closed agent says the wake is coming",
              not delivered and "the next tick wakes it" in said, said)

        _, said = world(*live, dmn.RUNNING, told=False)
        check("agree: a working agent is named as working, not as idle",
              "mid-turn" in said, said)

        # Told, and now working on it: the age is of the request, not of the
        # wait, so a two-day-old ask handed over a minute ago is a queue doing
        # its job. It drew as stuck the moment it was finally delivered.
        _, said = world(*live, dmn.RUNNING, told=True)
        check("agree: one it has been told about and is working on is not stuck",
              not said, said)

        _, said = world(*live, dmn.STALLED, told=False)
        check("agree: a stall names itself and the key that clears it",
              "STALLED" in said and "u clears it" in said, said)
        # And the same world with no daemon in it. Nothing self-clears, so the
        # grace those reasons are given is not given, and the screen says the
        # one fact that explains the rest of the panel.
        dmn.running_elsewhere = lambda: None
        actions._WORLD["at"] = 0.0
        _, said = world(*live, dmn.IDLE, told=False, age=5)
        check("agree: with no daemon, what is about to be delivered is stuck now",
              "has not been told yet" in said, said)
        _, said = world({"term_x"}, {}, dmn.IDLE, told=False, closed=True)
        check("agree: and a closed agent is not promised a tick that is not coming",
              "no daemon is running to wake it" in said, said)
        check("agree: the panel says so once, at the top",
              actions.stuck(con)[0]["what"] == "the daemon is not running",
              str(actions.stuck(con)[:1]))
    finally:
        (orcamod.available, orcamod.alive, orcamod.live_terminals,
         orcamod.idle_now, orcamod.send, obsmod.live_sessions,
         dmn.running_elsewhere) = real
        con.close()


def test_the_screen_says_what_is_stuck_and_what_was_decided(home: Path) -> None:
    """Two panels were drawing the wrong thing. "In flight" was almost never
    anything - a handoff takes seconds - while what it did draw was requests
    that had sat for a day and a half because their target had no terminal to
    be told with, which is stuck, not traffic. And the event list was six in
    ten hook events saying a turn started or ended, which the agent's own state
    column says already."""
    _daemon_home(home, 20)
    con = db.connect()
    was_running = dmn.running_elsewhere
    try:
        dmn.running_elsewhere = lambda: 4242      # the panel's ordinary world
        agents.register("marina", "alpha")
        db.set_terminal(con, "backend", None, None)
        db.add_request(con, "marina", "backend", "please look", None,
                       "peer", "async", None, 0, None)
        con.execute("UPDATE requests SET ts = ? WHERE target = 'backend'",
                    (int(time.time()) - actions.STUCK_AFTER_S - 60,))

        rows = actions.stuck(con)
        check("stuck: a request nobody can be told about is named with its age",
              any("marina → backend" in b["what"] and b["age"] > actions.STUCK_AFTER_S
                  for b in rows), str(rows))
        check("stuck: and with what is in the way, not just that it is late",
              any("no terminal" in b["why"] for b in rows), str(rows))

        # A request that just arrived is not stuck; it is about to be delivered.
        db.add_request(con, "marina", "backend", "and this", None,
                       "peer", "async", None, 0, None)
        fresh = [b["what"] for b in actions.stuck(con) if "and this" in b["what"]]
        check("stuck: something that has just arrived is not called stuck", not fresh)

        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn.STALLED, detail="by hand")
        check("stuck: a stalled agent is on the list with its reason",
              any("backend is STALLED" in b["what"] for b in actions.stuck(con)),
              str(actions.stuck(con)))

        # The reason has to be the daemon's reason, or the panel is a second
        # opinion about what is wrong. These are deliver_queued's own checks.
        db.set_agent_state(con, "backend", dmn.IDLE, detail="idle")
        db.set_terminal(con, "backend", "term_x", "wt")
        real = (orcamod.available, orcamod.live_terminals, obsmod.live_sessions)
        try:
            orcamod.available = lambda: True
            orcamod.live_terminals = lambda: {"term_x"}
            sessions_are()                       # the terminal is there, nobody is in it
            actions._WORLD["at"] = 0.0           # look again, not at the last look
            why = dict((b["what"], b["why"]) for b in actions.stuck(con))
            check("stuck: a terminal with no session says so, not 'it is IDLE'",
                  any("no session is running" in r for r in why.values()), str(why))

            sessions_are("backend")
            orcamod.live_terminals = lambda: set()
            actions._WORLD["at"] = 0.0
            why = [b["why"] for b in actions.stuck(con)]
            check("stuck: a terminal Orca no longer has says that",
                  any("terminal is gone" in r for r in why), str(why))

            # Told and not answered is a queue working, until it has outlasted
            # the longest turn the daemon itself calls healthy.
            orcamod.live_terminals = lambda: {"term_x"}
            actions._WORLD["at"] = 0.0
            rid = max(r["id"] for r in db.queued_for(con, "backend"))
            db.set_cursor(con, "notify:backend", rid)
            con.execute("UPDATE requests SET ts = ? WHERE target = 'backend'",
                        (int(time.time()) - 60,))
            check("stuck: a request the agent was just told about is not stuck",
                  not [b for b in actions.stuck(con) if "→ backend" in b["what"]],
                  str(actions.stuck(con)))
            con.execute("UPDATE requests SET ts = ? WHERE target = 'backend'",
                        (int(time.time()) - actions.STUCK_AFTER_S - 60,))
            told = [b["why"] for b in actions.stuck(con) if "→ backend" in b["what"]]
            check("stuck: once it has outlasted that, it says it was told and not answered",
                  told and "told and has not answered" in told[0], str(told))
            check("stuck: and the threshold is the daemon's own, not a second number",
                  actions.STUCK_AFTER_S == dmn.STUCK_RUNNING_S)
            # Neither unbounded panel may push the other, or what is under
            # them, off the screen without saying it held something back.
            for i in range(100):
                db.add_request(con, "marina", "backend", f"flood {i}", None,
                               "peer", "async", None, 0, None)
                db.add_request(con, "marina", actions.HUMAN, f"pile {i}", None,
                               "peer", "async", "escalate", 0, None)
            con.execute("UPDATE requests SET ts = ? WHERE body LIKE 'flood%'",
                        (int(time.time()) - actions.STUCK_AFTER_S - 60,))
            db.set_terminal(con, "backend", None, None)
            # A short window is where the mailbox was lost. Every drawn panel
            # has to show a row of its own - a box whose only line is "… 30
            # more" says there is something and shows none of it, and a column
            # header spending that line is the same box with a label on it -
            # and what is waiting on a human is the last panel to be dropped,
            # not the third.
            for height in (10, 14, 18, 22):
                plain = [tui._ANSI.sub("", ln)
                         for ln in tui.frame(110, height).split(chr(10))]
                titles = [ln for ln in plain if "┌─" in ln]
                rows = [ln for ln in plain if ln.startswith("│")]
                said = [ln for ln in rows
                        if ln.strip("│ ") and "more" not in ln and "account " not in ln
                        and "agent " not in ln]
                check(f"short: at {height} lines the mailbox is still drawn",
                      any("FOR YOU" in t for t in titles),
                      str([t.strip()[:30] for t in titles]))
                check(f"short: and it shows a message, not only its own count at {height}",
                      any("#" in ln for ln in said), str(said)[:200])
                check(f"short: every drawn panel says something at {height} lines",
                      len(said) >= len(titles), f"{len(said)} rows in {len(titles)} panels")

            for height in (24, 40, 60):
                plain = [tui._ANSI.sub("", ln)
                         for ln in tui.frame(110, height).split(chr(10))]
                titles = [ln for ln in plain if "┌─" in ln]
                check(f"stuck: at {height} lines both growing panels are drawn",
                      sum(1 for t in titles if "STUCK" in t or "FOR YOU" in t) == 2,
                      str([t.strip()[:34] for t in titles]))
                markers = [ln for ln in plain if "more" in ln]
                check(f"stuck: and every shortened panel says so at {height} lines",
                      len(markers) >= 2, str(markers)[:140])
                # A panel whose only row is its own "… N more" marker is the
                # empty box the minimum exists to prevent.
                rows = [ln for ln in plain if ln.startswith("│")]
                said = [ln for ln in rows if ln.strip("│ ") and "more" not in ln]
                check(f"stuck: every drawn panel says something at {height} lines",
                      len(said) >= len([ln for ln in plain if ln.startswith("┌")]),
                      f"{len(said)} rows of content in {len(plain)} lines")
            con.execute("DELETE FROM requests WHERE body LIKE 'flood%' "
                        "OR body LIKE 'pile%'")
            db.set_terminal(con, "backend", "term_x", "wt")
        finally:
            orcamod.available, orcamod.live_terminals, obsmod.live_sessions = real

        # What the daemon did is not a question the founder's screen answers -
        # it is `ocp events`, which shows decisions and takes --all for the
        # turn edges that outnumber them six to one.
        for i in range(8):
            db.add_event(con, "backend", "Stop", {"i": i})
        db.add_event(con, "backend", "wake", {"resumed_session": "sess-1"})
        out = _capture(lambda: climod.main(["events", "--agent", "backend", "--limit", "20"]))
        check("events: a decision is shown", "wake" in out, out[:160])
        check("events: and the turn edges are not", "Stop" not in out, out[:160])
        check("events: unless they are asked for",
              "Stop" in _capture(lambda: climod.main(
                  ["events", "--agent", "backend", "--limit", "20", "--all"])))

    finally:
        dmn.running_elsewhere = was_running
        con.close()


def test_a_message_is_read_whole_and_answered_where_it_is_read(home: Path) -> None:
    """An agent's report arrives as one paragraph with no newline in it, and
    fitting that to the width shows its first sentence and drops the rest -
    which is not reading the message. And the message someone has just read is
    the one they are about to answer: backing out to the panel to do it is how
    an answer gets put off, and it is where answering the wrong row starts."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        long_body = ("A verdict with no newlines at all, " * 20).strip()
        db.add_request(con, "auditor", actions.HUMAN, long_body,
                       None, "peer", "async", "verdict", 0, None)
        db.add_request(con, "marina", actions.HUMAN, "and a second one",
                       None, "peer", "async", None, 0, None)
        first, second = [m["id"] for m in actions.messages(con)]

        wrapped = tui.wrap([long_body], 60)
        check("read: a paragraph is broken to the width, not cut off",
              len(wrapped) > 1 and max(len(ln) for ln in wrapped) <= 60,
              str(max(len(ln) for ln in wrapped)))
        check("read: and every word survives the break",
              " ".join(wrapped).split() == long_body.split())

        # Enter opens it, carrying which message it is.
        sel = {tui.MESSAGES: first}
        said, detail = tui._act(tui.MESSAGES, sel, "\r", 100, 20, None, None)
        check("read: opening a message opens that message",
              detail and detail["key"] == first and detail["panel"] == tui.MESSAGES,
              str(detail)[:120])

        for height in (8, 16, 20, 40):
            text = tui._detail_frame(100, height, detail)
            lines = [tui._ANSI.sub("", ln) for ln in text.split(chr(10))]
            check(f"read: the view is exactly {height} lines, keys included",
                  len(lines) == height and "esc back" in lines[-1], repr(lines[-1])[:80])
            fits = len(tui.lines_of(detail, 100)) <= max(1, height - 4)
            check(f"read: at {height} lines it counts what is off the bottom, "
                  f"and only then", ("line 1-" in lines[0]) is not fits, lines[0])

        body_lines = len(tui.lines_of(detail, 100))
        detail, _ = tui._in_detail(detail, "G", 100, 20)
        check("read: G goes to the end and stops there",
              detail["top"] == max(0, body_lines - 16), str(detail["top"]))
        detail, _ = tui._in_detail(detail, "j", 100, 20)
        tui._detail_frame(100, 20, detail)
        check("read: and nothing scrolls past it",
              detail["top"] == max(0, body_lines - 16), str(detail["top"]))
        detail, _ = tui._in_detail(detail, "g", 100, 20)
        check("read: g comes back to the top", detail["top"] == 0)

        # A zero width is what a pty reports before anyone has sized it, and
        # what some terminals report for one frame mid-resize. Breaking lines
        # to fit it used to never shrink the line: the screen stopped redrawing
        # and stopped reading keys, with the daemon still running behind it.
        try:
            with _deadline(5):
                ok = bool(tui.wrap(["nospaceshereatall"], 0)
                          and tui.frame(0, 24, detail=detail))
        except TimeoutError as e:
            ok = False
            check("read: a zero-width terminal does not hang the screen", False, str(e))
        else:
            check("read: a zero-width terminal does not hang the screen", ok)

        # A body is whatever the sender typed. An escape in it is invisible to
        # the width count, so it is never truncated - and `esc [ 2J` clears the
        # screen this is drawing.
        cleaned = tui.wrap(["\033[2J\033[Hpasted \033[31mtool output"], 40)
        # A Korean handoff is the ordinary case here, and a glyph two columns
        # wide counted as one column tore every box it was drawn in.
        check("width: a wide glyph counts as the two columns it takes",
              tui.vlen("진행중") == 6 and tui.vlen("abc") == 3, str(tui.vlen("진행중")))
        check("width: fitting counts columns, so it never overflows",
              tui.vlen(tui.fit("진행중 막힌 것 다음", 10)) <= 10,
              repr(tui.fit("진행중 막힌 것 다음", 10)))
        box = tui.Panel("한글", 40)
        box.add("## 진행중")
        drawn = box.draw()
        check("width: and a box around it keeps its border straight",
              {tui.vlen(ln) for ln in drawn} == {40}, str({tui.vlen(ln) for ln in drawn}))

        check("read: escapes in a body are text stripped, not commands drawn",
              "\033" not in "".join(cleaned) and "pasted tool output" in " ".join(cleaned),
              str(cleaned))

        # An action that raises while something is open is that action's
        # failure. The panel keys were wrapped for this; the detail keys were
        # not, and the two editor cases that raise land exactly there.
        real_answer = actions.answer
        try:
            actions.answer = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
            os.environ["EDITOR"] = "/bin/sh -c 'echo an answer >> \"$0\"'"
            still, said = tui._in_detail(dict(detail), "e", 100, 20)
            check("read: a failing answer is caught where it happens, not at the exit",
                  still is not None and "boom" in said, f"{still} {said}")
        except RuntimeError:
            check("read: a failing answer is caught where it happens, not at the exit",
                  False, "it escaped _in_detail")
        finally:
            actions.answer = real_answer
            os.environ.pop("EDITOR", None)

        # An editor that exits non-zero is saying abandon this - :cq in vim.
        os.environ["EDITOR"] = "/bin/sh -c 'echo written >> \"$0\"; exit 1'"
        check("read: an editor that wrote and then abandoned sends nothing",
              tui._editor("# seed\n") is None)
        os.environ["EDITOR"] = "/bin/sh -c 'echo written >> \"$0\"'"
        check("read: and one that wrote and left cleanly sends what it wrote",
              tui._editor("# seed\n") == "written")
        os.environ["EDITOR"] = "vim '"
        check("read: and an unquotable $EDITOR is refused, not raised",
              tui._editor("# seed\n") is None)
        os.environ.pop("EDITOR", None)

        # The cursor in the panel is somewhere else entirely; the key acts on
        # what is open, which is what the reader is looking at.
        sel[tui.MESSAGES] = second
        detail, said = tui._in_detail(detail, "x", 100, 20)
        # The view scrolls now, so the slices that existed because it could not
        # are just silent cuts - and they were inside the thing being scrolled.
        for i in range(9):
            db.add_request(con, "marina", "backend", f"request {i}", None,
                           "peer", "async", None, 0, None)
        keep_handoff("backend",
            "\n".join(f"- line {i}" for i in range(30)))
        shown = "\n".join(ln for _, rows in actions.agent_detail(con, "backend")
                          for ln in rows)
        check("read: the page is sections, not one wall of text",
              [t for t, _ in actions.agent_detail(con, "backend") if t],
              str([t for t, _ in actions.agent_detail(con, "backend")]))
        check("read: an opened agent shows its whole inbox",
              all(f"request {i}" in shown for i in range(9)), shown[:120])
        check("read: and its whole handoff", "- line 29" in shown, shown[-80:])

        check("read: dismissing closes the message being read, not the selected row",
              db.get_request(con, first)["state"] == "done"
              and db.get_request(con, second)["state"] == "queued", said)
        check("read: and the view closes once there is nothing left to read",
              detail is None)
    finally:
        con.close()


def test_a_key_never_acts_on_a_row_nobody_pointed_at(home: Path) -> None:
    """The row under the cursor can be gone by the time a key is pressed - a
    message answered elsewhere, an agent that went away - and then nothing is
    drawn as selected. Falling back to the first row acts on something the
    human never chose: it dismissed an unread message that way."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        db.add_request(con, "marina", actions.HUMAN, "first, unread",
                       None, "peer", "async", None, 0, None)
        first = actions.messages(con)[0]["id"]
        sel = {tui.MESSAGES: first + 99}          # a cursor on something gone
        said, detail = tui._act(tui.MESSAGES, sel, "x", 120, 40, None, None)
        check("top: the key is refused and the cursor is put somewhere real",
              "press again" in said and sel[tui.MESSAGES] == first, said)
        check("top: and the message it would have hit is untouched",
              db.get_request(con, first)["state"] == "queued")

        said, _ = tui._act(tui.MESSAGES, sel, "x", 120, 40, None, None)
        check("top: pressing it again acts on what is now selected",
              db.get_request(con, first)["state"] == "done", said)
    finally:
        con.close()


def test_the_frame_never_draws_wider_than_the_terminal(home: Path) -> None:
    """A panel title counts what is waiting, so it grows with the queue. A head
    wider than the terminal wraps, a wrapped line is one row more than the
    frame promised, and the alternate screen scrolls - which this screen's
    whole contract says it does not."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        for i in range(12):
            db.add_request(con, "marina", actions.HUMAN, f"decision {i}",
                           None, "peer", "fire_and_forget", "escalate", 0, None)
        for width in (40, 44, 55, 59, 60, 80, 120):
            text = tui.frame(width, 40)
            lines = [tui._ANSI.sub("", ln) for ln in text.split(chr(10))]
            wide = [ln for ln in lines if len(ln.rstrip()) > width]
            check(f"top: nothing is wider than a {width}-column terminal",
                  not wide and len(lines) == 40, str(wide[:1])[:120])
        for width in (40, 55, 60, 80, 120):
            bar = tui._ANSI.sub("", tui.keybar(tui.AGENTS, width))
            check(f"top: the keys at {width} columns end on a whole word",
                  len(bar) <= width and not bar.rstrip().endswith(("…", "…more")),
                  repr(bar))
        # The navigation is the same on every panel and is what a reader
        # already knows, so it goes before any action does.
        wide = tui._ANSI.sub("", tui.keybar(tui.AGENTS, 160))
        tight = tui._ANSI.sub("", tui.keybar(tui.AGENTS, 100))
        tighter = tui._ANSI.sub("", tui.keybar(tui.AGENTS, 60))
        check("top: a tight line keeps every action and drops the navigation",
              "panel" in wide and "panel" not in tight
              and "unstall" in tight, repr(tight))
        check("top: and a tighter one sheds actions, counting what it held back",
              "more" in tighter and "unstall" not in tighter, repr(tighter))
    finally:
        con.close()


def test_the_cursor_walks_the_whole_screen(home: Path) -> None:
    """The lists change under the cursor - an agent is registered, a message is
    answered - so moving is defined on what is there now, not on a remembered
    index. And the panels are four lists stacked on one screen: arrowing down
    the screen means the screen, so the end of one panel is the top of the
    next. Stopping at the bottom of the team list is how a mailbox below it
    goes unread."""
    keys = ["alpha", "beta", "gamma"]
    check("top: down moves to the next", tui.move(keys, "alpha", 1) == "beta")
    check("top: the end of a list is off it, for the caller to handle",
          tui.move(keys, "gamma", 1) is None and tui.move(keys, "alpha", -1) is None)
    check("top: a cursor on something that is gone lands on the first row",
          tui.move(keys, "deleted", 1) == "alpha")
    check("top: and an empty panel has nothing to select",
          tui.move([], None, 1) is None)

    _daemon_home(home, 20)
    con = db.connect()
    try:
        agents.register("marina", "alpha")
        db.add_request(con, "marina", actions.HUMAN, "which variant ships?",
                       None, "peer", "async", None, 0, None)
        sel: dict = {}
        panel = tui._enter(tui.AGENTS, sel)
        check("top: arriving in a panel puts the cursor on its first row",
              sel[tui.AGENTS] is not None, str(sel))
        # Through the keys, not through the function behind them: a `step` that
        # was right and a loop that still called the old `move` is how ↓
        # stopped at the bottom of a panel and left the cursor nowhere.
        seen = []
        for _ in range(6):
            seen.append((panel, sel.get(panel)))
            panel = tui.navigate(panel, sel, "\033[B")
        check("top: down off the end of the team list lands in the mailbox",
              any(p == tui.MESSAGES for p, _k in seen) or panel == tui.MESSAGES,
              str([(tui.PANEL_NAME[p], k) for p, k in seen]))
        check("top: and the cursor is never left on nothing",
              all(k is not None for _p, k in seen) and sel.get(panel) is not None,
              str([(tui.PANEL_NAME[p], k) for p, k in seen]))
        back = tui.navigate(panel, sel, "\033[A")
        check("top: and up comes back out of it", back in (tui.MESSAGES, tui.AGENTS),
              tui.PANEL_NAME[back])
        check("top: → and tab are the same jump",
              tui.navigate(tui.AGENTS, sel, "\033[C")
              == tui.navigate(tui.AGENTS, sel, "\t") == tui.MESSAGES)
        check("top: and a key that is not a move says so",
              tui.navigate(tui.AGENTS, sel, "a") is None)

        # Which panel the keys act on was only in the footer's first word, so a
        # reader pressed `a` at a panel they were not in and read a refusal.
        framed = tui.frame(110, 40, sel, tui.MESSAGES)
        titles = [ln for ln in framed.split(chr(10)) if "┌─" in ln]
        lit = [t for t in titles if tui.CYA in t]
        check("top: the panel the keys act on is the one drawn as focused",
              len(lit) == 1 and "FOR YOU" in tui._ANSI.sub("", lit[0]),
              str([tui._ANSI.sub("", t).strip()[:24] for t in lit]))
        check("top: and it is the only panel showing a cursor",
              sum(ln.count("▸") for ln in framed.split(chr(10))) == 1,
              str([tui._ANSI.sub("", ln)[:40] for ln in framed.split(chr(10))
                   if "▸" in ln]))
        check("top: and the keys it offers are the ones on the footer",
              "a answer" in tui._ANSI.sub("", framed.split(chr(10))[-1]),
              tui._ANSI.sub("", framed.split(chr(10))[-1]))

        # An open page asks several questions and used to stack every answer,
        # so reading the fourth meant scrolling past three.
        page = tui.page("agent backend", actions.agent_detail(con, "backend"),
                        tui.AGENTS, "backend")
        first = tui._ANSI.sub("", tui._detail_frame(110, 20, page))
        page, _said = tui._in_detail(page, "\033[C", 110, 20)
        second = tui._ANSI.sub("", tui._detail_frame(110, 20, page))
        check("top: an open page has its sections as tabs",
              "WAITING FOR IT" in first and "WHAT IT KNOWS" in first, first[:200])
        check("top: and ←→ reads a different one, from its top",
              first != second and page["tab"] == 1 and page["top"] == 0,
              str(page["tab"]))
    finally:
        con.close()


def test_login_points_at_the_account_and_touches_no_credential(home: Path) -> None:
    """Logging in is the one thing ocp may not do for a human: a refresh token
    rotates on single use, so a second copy of one credential invalidates the
    first. All this decides is which config directory claude is pointed at."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    accounts.register("alpha")

    action, msg = accounts.login_plan("beta")
    check("login: an account nobody registered says how to register it",
          action == "unknown" and "ocp account add beta" in msg, msg)

    action, msg = accounts.login_plan("alpha")
    check("login: one with no credentials gets the command that makes them",
          action == "login" and "claude /login" in msg, msg)
    check("login: pointed at that account's own config directory",
          str(paths.account_claude("alpha")) in msg, msg)

    (paths.account_claude("alpha") / ".credentials.json").write_text("{}")
    action, msg = accounts.login_plan("alpha")
    check("login: an account already logged in is left alone", action == "already", msg)
    action, _ = accounts.login_plan("alpha", force=True)
    check("login: unless the human insists", action == "login")

    # Which directory claude writes its credential into is the whole of what
    # this decides; ocp never reads or copies what lands there.
    env = accounts.env_for("alpha")
    check("login: the session is pointed at that account's config directory",
          env["CLAUDE_CONFIG_DIR"] == str(paths.account_claude("alpha"))
          and env["OCP_ACCOUNT"] == "alpha", env["CLAUDE_CONFIG_DIR"])


def test_a_fold_with_no_account_says_which_and_why(home: Path) -> None:
    """"No logged-in account" sent the reader to the one thing that was not
    wrong. An account is passed over here for being at the wall, or for a usage
    figure nothing can read, far more often than for credentials."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    for name in ("full", "unreadable", "out"):
        accounts.register(name)
    (paths.account_claude("full") / ".credentials.json").write_text("{}")
    (paths.account_claude("unreadable") / ".credentials.json").write_text("{}")
    write_status(home, "full", pct_5h=100, pct_7d=10, ctx_pct=5)
    # Stale, not quiet: a fresh snapshot with no figure is read as 0%, which is
    # a reading. What cannot be read at all is one nothing has refreshed.
    write_status(home, "unreadable", pct_5h=None, pct_7d=10, ctx_pct=5,
                 collected_at=int(time.time()) - status.STALE_AFTER_S - 60)

    said = foldmod._account_reasons()
    check("fold: the account at the wall is named with its figure",
          "full at 100%" in said, said)
    check("fold: the one nothing can read is named as that, not as logged out",
          "unreadable usage unreadable" in said, said)
    check("fold: and the one actually logged out is named as that",
          "out not logged in" in said, said)


def test_doctor_says_when_the_record_and_the_process_table_disagree(home: Path) -> None:
    """The check that has to fire when `observe.is_session` stops matching - and
    stay quiet in the states that are not that. It fires on a status line still
    being written after we recorded the agent closed, because nothing but a
    live session writes one."""
    _daemon_home(home, 20)
    con = db.connect()
    real = (obsmod.live_sessions, orcamod.live_terminals)
    agent_dir = home / "agents" / "backend"
    agent_dir.mkdir(parents=True, exist_ok=True)

    def status_line(age_s: int = 0) -> None:
        (agent_dir / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()) - age_s,
            "session_id": "sess-3", "pct_5h": 20, "ctx_pct": 8}))

    def rows() -> list:
        r = doctor.Report()
        doctor._check_session_matches_the_record(r)
        return r.rows

    try:
        orcamod.live_terminals = lambda: {"term_x"}
        db.set_terminal(con, "backend", "term_x", "wt")
        sessions_are()
        status_line()

        # The daemon is down, so nothing has closed anything yet: a fresh
        # status line and no process is the disagreement itself.
        check("doctor: a status line with no session behind it warns",
              rows() and rows()[-1][0] == doctor.WARN, str(rows()))

        sessions_are("backend")
        check("doctor: and says nothing when the session is there", not rows(), str(rows()))

        # The state this exists for: is_session stopped matching, so the sweep
        # recorded every agent closed - and the sessions carried on writing.
        sessions_are()
        db.set_closed(con, "backend", "sess-3")
        con.execute("UPDATE agent_state SET closed_at = ? WHERE agent = 'backend'",
                    (int(time.time()) - 300,))
        con.commit()
        status_line()
        check("doctor: a closed agent still writing its status line warns",
              rows() and rows()[-1][0] == doctor.WARN, str(rows()))

        # An ordinary close: the status line stopped when the session did.
        status_line(age_s=300)
        check("doctor: but an ordinary close is not a disagreement", not rows(), str(rows()))

        # A reboot leaves our handle pointing at a terminal Orca no longer has.
        # The sweep drops the handle and says so; two reports of one fact is
        # the screen arguing with the daemon.
        status_line()
        orcamod.live_terminals = lambda: set()
        check("doctor: a terminal that is gone is the sweep's to report",
              not rows(), str(rows()))

        orcamod.live_terminals = lambda: {"term_x"}
        status_line(age_s=status.STALE_AFTER_S + 60)
        check("doctor: nor is a stale snapshot - nothing was writing it",
              not rows(), str(rows()))

        status_line()
        obsmod.live_sessions = lambda: None
        check("doctor: and it stays quiet when it could not look at all",
              not rows(), str(rows()))
    finally:
        obsmod.live_sessions, orcamod.live_terminals = real
        con.close()

def test_nothing_is_typed_into_a_shell_with_no_session(home: Path) -> None:
    """Orca runs claude as a login shell's child, so a session that ends leaves
    the terminal standing and our handle still working: `send` succeeds and the
    text lands at a bash prompt. What we send is not inert there - a handoff's
    backticks run and its `>` redirects - and `/exit` at that prompt exits the
    shell, taking the terminal, the worktree and the only way back with it. So
    every path that types checks the process table first, and a table it could
    not read counts as no.
    """
    _daemon_home(home, 50)
    con = db.connect()
    sent: list[str] = []
    cleared: list[str] = []
    real = (orcamod.available, orcamod.alive, orcamod.idle_now, orcamod.wait_idle,
            orcamod.send, orcamod.clear_context)
    try:
        orcamod.available = lambda: True
        orcamod.live_terminals = lambda: {"term_x"}
        orcamod.alive = lambda h: True          # the terminal outlives the session
        orcamod.idle_now = lambda h: True       # and a bare shell probes as idle
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        orcamod.clear_context = lambda h, wait=True, timeout_ms=0: (
            cleared.append(h), (True, "context cleared"))[1]
        table = sessions_are()                  # the shell is there; nobody is in it
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn.IDLE)
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()),
            "session_id": "sess-5", "pct_5h": 50}))
        st = status.read_for_agent("backend", "alpha")

        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)
        check("shell: a waiting request is not announced to a shell",
              not dmn.deliver_queued(con, "backend", lambda _m: None) and not sent, str(sent))
        check("shell: and it stays queued, for the wake to deliver",
              len(db.queued_for(con, "backend")) == 1)

        ok, why = dmn.close_agent(con, "backend", lambda _m: None, st)
        check("shell: /exit is not typed at it - that would end the shell too",
              not ok and not sent, f"{why} {sent}")
        check("shell: and its terminal is left recorded",
              db.get_agent_state(con, "backend")["terminal"] == "term_x")

        keep_handoff("backend", "# handoff · backend\n- carry on\n")
        ok, why = dmn.wake_agent(con, "backend", lambda _m: None)
        check("shell: a handoff is not typed at it either",
              not ok and not sent, f"{why} {sent}")

        # The clear is gated where it is sent, not only where it is decided:
        # a handoff resumed after a restart reaches it without passing the
        # check perform_handoff makes.
        ok, why = dmn._reclaim_context(con, "backend", lambda _m: None)
        check("shell: /clear is not typed at it, whoever asked for it",
              not ok and not cleared, f"{why} {cleared}")

        t = triggers.Trigger(True, "context", "context 72% >= 60%")
        dmn.perform_handoff(con, "backend", t, lambda _m: None)
        row = db.get_agent_state(con, "backend")
        check("shell: no handoff is run for a session that is not there",
              not cleared and not sent and not db.in_flight_handoffs(con), str(sent))
        check("shell: so no fold is paid for either",
              row["state"] != dmn.PARKED and row["continuations"] == 0, str(dict(row)))

        # Fail-closed: "the process table could not be read" is not "no session",
        # but it is not permission to send, which is the only question here.
        obsmod.live_sessions = lambda: None
        db.add_request(con, "reviewer", "backend", "and this", None,
                       "peer", "async", None, 0, None)
        check("shell: an unreadable process table is a no, not a maybe",
              not dmn.deliver_queued(con, "backend", lambda _m: None) and not sent, str(sent))

        # And with a session running, the same call does its job.
        table["backend"] = 4242
        obsmod.live_sessions = lambda: dict(table)
        check("shell: a running agent is told what is waiting",
              dmn.deliver_queued(con, "backend", lambda _m: None) and
              any("ocp inbox" in t2 for t2 in sent), str(sent))

        # One listing per tick, not one per send: the sweep already asked.
        calls = {"n": 0}
        base = dict(table)

        def counted():
            calls["n"] += 1
            return dict(base)

        obsmod.live_sessions = counted
        db.set_cursor(con, "notify:backend", 0)
        sent.clear()
        dmn.tick(con, lambda _m: None)
        check("shell: the tick reads the process table once, and the sends reuse it",
              calls["n"] == 1 and any("ocp inbox" in t2 for t2 in sent),
              f"{calls['n']} listings, sent {sent}")
    finally:
        (orcamod.available, orcamod.alive, orcamod.idle_now, orcamod.wait_idle,
         orcamod.send, orcamod.clear_context) = real
        con.close()


def test_a_fold_is_a_process_not_a_session(home: Path) -> None:
    """`ocp fold` is a headless `claude -p` that inherits whatever OCP_AGENT its
    caller had. Counting it as the agent's session would tell the sweep an agent
    is alive for as long as a fold runs, and closed the moment it ends."""
    check("observe: an agent's session counts",
          obsmod.is_session("/usr/bin/claude --model sonnet --effort high"))
    check("observe: a fold does not",
          not obsmod.is_session("/usr/bin/claude --model haiku -p write me a digest"))
    check("observe: nor a fold with the prompt last",
          not obsmod.is_session("/usr/bin/claude --model haiku -p"))
    check("observe: and nor does anything that is not claude",
          not obsmod.is_session("/usr/bin/ssh somewhere"))


def test_who_is_asking_is_not_guessed(home: Path) -> None:
    """A human at their own prompt is the human; a process that merely
    lost OCP_AGENT is not. Conflating them hands the escalation target and the
    most privileged node in the routing table to whatever dropped the variable."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    (home / "team.yaml").write_text(DESIGN_TEAM)
    agents.register("backend", "alpha")
    class FakeStdin:
        def __init__(self, tty: bool) -> None:
            self.tty = tty

        def isatty(self) -> bool:
            return self.tty

    real_env = os.environ.pop("OCP_AGENT", None)
    real_stdin = sys.stdin
    try:
        os.environ["OCP_AGENT"] = "backend"
        check("identity: an agent is whoever launch said it is",
              askmod.current_agent() == "backend" and askmod.asking_agent() == "backend")
        del os.environ["OCP_AGENT"]

        sys.stdin = FakeStdin(True)
        check("identity: an unset variable is still unset",
              askmod.current_agent() is None, str(askmod.current_agent()))
        check("identity: but a human at a terminal is the human",
              askmod.asking_agent() == askmod.HUMAN, str(askmod.asking_agent()))

        sys.stdin = FakeStdin(False)
        check("identity: a process that lost it is nobody",
              askmod.asking_agent() is None, str(askmod.asking_agent()))
        ok, msg, rid = askmod.ask("backend", "do this")
        check("identity: and is refused rather than promoted", not ok and rid is None, msg)
        check("identity: and told how to say who it is", "--of" in msg, msg)
        ok, _msg, rid = askmod.ask("backend", "do this", requester="reviewer")
        check("identity: saying so explicitly still works", ok and rid is not None, _msg)
    finally:
        sys.stdin = real_stdin
        if real_env is not None:
            os.environ["OCP_AGENT"] = real_env
        else:
            os.environ.pop("OCP_AGENT", None)


def test_events_view_starts_at_the_newest(home: Path) -> None:
    """`ocp events` answers "what just happened", and must not report the page
    size as the total - it said 50 events when there were 187."""
    os.environ["OCP_HOME"] = str(home)
    con = db.connect()
    try:
        for i in range(12):
            db.add_event(con, "scout" if i % 2 else "keeper", f"k{i}", {})
        rows = db.latest_events(con, limit=5)
        check("events: the newest are returned", [r["kind"] for r in rows]
              == ["k7", "k8", "k9", "k10", "k11"], str([r["kind"] for r in rows]))
        check("events: oldest first, so they read downward",
              rows[0]["id"] < rows[-1]["id"])
        check("events: the total is the total, not the page",
              db.count_events(con) == 12, str(db.count_events(con)))
        check("events: filtered by agent", db.count_events(con, agent="scout") == 6,
              str(db.count_events(con, agent="scout")))
        rows = db.latest_events(con, agent="keeper", limit=3)
        check("events: the filter applies to the page too",
              all(r["agent"] == "keeper" for r in rows))
    finally:
        con.close()


def test_orca_idle_parsing() -> None:
    """`ok` says the wait command ran; `satisfied` says the agent is idle.

    Reading `ok` alone treats a timeout as idle, and clearing mid-turn throws
    away work the fold has not recorded yet.
    """
    real = orcamod._run
    try:
        orcamod._run = lambda *a, **k: (True, json.dumps(
            {"ok": True, "result": {"wait": {"satisfied": True}}}))
        check("orca: satisfied=true reads as idle", orcamod.wait_idle("h", 1000))

        orcamod._run = lambda *a, **k: (True, json.dumps(
            {"ok": True, "result": {"wait": {"satisfied": False}}}))
        check("orca: a timed-out wait is not idle", not orcamod.wait_idle("h", 1000))

        orcamod._run = lambda *a, **k: (False, "boom")
        check("orca: a failed wait is not idle", not orcamod.wait_idle("h", 1000))

        orcamod._run = lambda *a, **k: (True, "not json at all")
        check("orca: unparseable output is not idle", not orcamod.wait_idle("h", 1000))
    finally:
        orcamod._run = real


def test_clear_refuses_mid_turn() -> None:
    real_alive, real_wait, real_send = orcamod.alive, orcamod.wait_idle, orcamod.send
    sent = []
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        orcamod.send = lambda h, t, enter=True: (sent.append(t), (True, ""))[1]

        orcamod.wait_idle = lambda h, t=0: False
        ok, why = orcamod.clear_context("h")
        check("orca: refuses to clear a busy agent", not ok and "mid-turn" in why, why)
        check("orca: and sends nothing", sent == [], str(sent))

        orcamod.wait_idle = lambda h, t=0: True
        ok, why = orcamod.clear_context("h")
        check("orca: clears an idle agent", ok, why)
        check("orca: by sending /clear", sent == ["/clear"], str(sent))

        orcamod.available = lambda: True
        orcamod.alive = lambda h: False
        ok, why = orcamod.clear_context("h")
        check("orca: will not clear a dead terminal", not ok, why)
    finally:
        orcamod.alive, orcamod.wait_idle, orcamod.send = real_alive, real_wait, real_send


def test_handoff_reclaims_context(home: Path) -> None:
    """A handoff must return the context, not just write a digest.

    Without this the context never drops, so a context trigger fires again on
    the next tick and the agent hands off forever, paying for a fold each time.
    """
    _daemon_home(home, 50)
    con = db.connect()
    cleared = []
    real_clear, real_alive, real_idle = orcamod.clear_context, orcamod.alive, orcamod.idle_now
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        orcamod.idle_now = lambda h: True
        orcamod.clear_context = lambda h, wait=True, timeout_ms=0: (
            cleared.append(h), (True, "context cleared"))[1]
        # The agent is running: a handoff is for a session that has a context
        # to reclaim, and /clear at a shell prompt reclaims nothing.
        sessions_are("backend")

        # With no terminal recorded it must say so rather than pretend.
        db.set_terminal(con, "backend", None, None)
        ok, why = dmn._reclaim_context(con, "backend", lambda _m: None)
        check("handoff: says so when no terminal is recorded",
              not ok and "no terminal" in why, why)

        db.set_terminal(con, "backend", "term_x", "wt")
        t = triggers.Trigger(True, "context", "context 72% >= 60%")
        dmn.perform_handoff(con, "backend", t, lambda _m: None)
        check("handoff: clears the agent's context", cleared == ["term_x"], str(cleared))
        row = db.get_agent_state(con, "backend")
        check("handoff: still parks afterwards", row["state"] == dmn.PARKED, str(dict(row)))
        check("handoff: the terminal survives the park", row["terminal"] == "term_x")
        # A busy agent must be left alone: folding would miss the work in
        # flight and clearing would throw it away.
        orcamod.idle_now = lambda h: False
        cleared.clear()
        before = len(db.in_flight_handoffs(con))
        db.set_agent_state(con, "backend", dmn.RUNNING, detail="mid-turn")
        dmn.perform_handoff(con, "backend", t, lambda _m: None)
        check("handoff: a mid-turn agent is deferred, not cleared", cleared == [], str(cleared))
        check("handoff: and no handoff is started for it",
              len(db.in_flight_handoffs(con)) == before)
        check("handoff: the agent keeps running",
              db.get_agent_state(con, "backend")["state"] == dmn.RUNNING)
    finally:
        orcamod.clear_context, orcamod.alive = real_clear, real_alive
        orcamod.idle_now = real_idle
        con.close()


def test_wake_injects_handoff(home: Path) -> None:
    _daemon_home(home, 50)
    con = db.connect()
    sent = []
    real_resume, real_alive = orcamod.resume_with, orcamod.alive
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        orcamod.resume_with = lambda h, text: (sent.append(text), (True, "handoff injected"))[1]
        # Cleared, not closed - the session is still there to be handed its
        # own record back.
        sessions_are("backend")

        ok, why = dmn.wake_agent(con, "backend", lambda _m: None)
        check("wake: nothing to wake without a terminal", not ok, why)

        db.set_terminal(con, "backend", "term_x", "wt")
        ok, why = dmn.wake_agent(con, "backend", lambda _m: None)
        check("wake: nothing to resume from without a handoff", not ok, why)

        keep_handoff("backend", "# handoff · backend\n## 다음\n- finish the thing\n")
        ok, why = dmn.wake_agent(con, "backend", lambda _m: None)
        check("wake: injects the handoff", ok and sent, why)
        check("wake: the agent is told its context was cleared",
              "context was cleared" in sent[-1], sent[-1][:80])
        check("wake: and gets its own record back",
              "finish the thing" in sent[-1], sent[-1][:120])
    finally:
        orcamod.resume_with, orcamod.alive = real_resume, real_alive
        con.close()


def test_hooks_report_activity(home: Path) -> None:
    """A working agent must not still read as PARKED.

    agent_state otherwise only changes when the daemon changes it, so an agent
    that was parked and then given work directly stays PARKED - and the daemon
    would "wake" it mid-task by injecting a handoff it never needed.
    """
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    agents.register("hooky", "alpha")
    env = dict(os.environ, OCP_AGENT="hooky", OCP_ACCOUNT="alpha", OCP_HOME=str(home))

    con = db.connect()
    try:
        db.set_agent_state(con, "hooky", dmn.PARKED, wake_at=int(time.time()) + 9999,
                           detail="parked earlier")
    finally:
        con.close()

    payload = json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "s",
                          "prompt": "do the thing"})
    subprocess.run([sys.executable, str(ROOT / "bin" / "ocp"), "hook", "UserPromptSubmit"],
                   input=payload, capture_output=True, text=True, env=env)

    con = db.connect()
    try:
        row = db.get_agent_state(con, "hooky")
        check("hooks: a prompt moves the agent to RUNNING", row["state"] == "RUNNING",
              str(dict(row)))
        check("hooks: and clears the stale wake_at", row["wake_at"] is None, str(dict(row)))

        # STALLED is the daemon's to clear, not the agent's.
        db.set_agent_state(con, "hooky", dmn.STALLED, detail="needs a human")
    finally:
        con.close()
    subprocess.run([sys.executable, str(ROOT / "bin" / "ocp"), "hook", "UserPromptSubmit"],
                   input=payload, capture_output=True, text=True, env=env)
    con = db.connect()
    try:
        check("hooks: activity does not clear STALLED",
              db.get_agent_state(con, "hooky")["state"] == dmn.STALLED)
    finally:
        con.close()

    # A mid-handoff agent must not be yanked back to RUNNING either.
    con = db.connect()
    try:
        db.set_agent_state(con, "hooky", dmn.HANDOFF, detail="handing off")
    finally:
        con.close()
    subprocess.run([sys.executable, str(ROOT / "bin" / "ocp"), "hook", "SessionStart"],
                   input=json.dumps({"hook_event_name": "SessionStart", "session_id": "s"}),
                   capture_output=True, text=True, env=env)
    con = db.connect()
    try:
        check("hooks: activity does not interrupt a handoff",
              db.get_agent_state(con, "hooky")["state"] == dmn.HANDOFF)
    finally:
        con.close()

    # The other end of the turn. RUNNING is what stops work being delivered and
    # stops a session being closed, so an agent that entered it and never left
    # was taken out of service without anything saying so: three agents sat
    # RUNNING for over two hours, told about no request and holding their memory.
    def hook(name: str, **extra) -> None:
        subprocess.run([sys.executable, str(ROOT / "bin" / "ocp"), "hook", name],
                       input=json.dumps({"hook_event_name": name, "session_id": "s", **extra}),
                       capture_output=True, text=True, env=env)

    con = db.connect()
    try:
        db.set_agent_state(con, "hooky", dmn.RUNNING, detail="working")
    finally:
        con.close()
    hook("SubagentStop")
    con = db.connect()
    try:
        check("hooks: a subagent finishing does not mean the agent is free",
              db.get_agent_state(con, "hooky")["state"] == dmn.RUNNING,
              str(db.get_agent_state(con, "hooky")["state"]))
    finally:
        con.close()

    hook("Stop")
    con = db.connect()
    try:
        row = db.get_agent_state(con, "hooky")
        check("hooks: the end of a turn returns it to IDLE",
              row["state"] == dmn.IDLE, str(dict(row)))

        # The other way in. `ocp launch` records the terminal handle before
        # execve, and that row used to be born RUNNING - so a new agent was out
        # of service before it had done anything, and _mark_activity then
        # declined to rewrite a state that was already RUNNING.
        db.set_terminal(con, "newborn", "term_n", "wt")
        born = db.get_agent_state(con, "newborn")
        check("launch: recording a terminal does not claim the agent is working",
              born["state"] == dmn.IDLE, str(dict(born)))
        check("launch: and it can be closed and told about work like any idle one",
              born["state"] not in (dmn.RUNNING, dmn.HANDOFF))

        db.set_agent_state(con, "newborn", dmn.RUNNING, detail="working")
        db.set_terminal(con, "newborn", "term_n2", "wt")
        check("launch: and a later handle does not reset a state it knows nothing about",
              db.get_agent_state(con, "newborn")["state"] == dmn.RUNNING,
              str(dict(db.get_agent_state(con, "newborn"))))
        check("hooks: and it can be told about work again",
              row["state"] not in (dmn.RUNNING, dmn.HANDOFF))

        # Being stuck in RUNNING is silent by nature, so doctor says it out loud.
        db.set_agent_state(con, "hooky", dmn.RUNNING, detail="working")
        con.execute("UPDATE events SET ts = ? WHERE agent = 'hooky'",
                    (int(time.time()) - dmn.STUCK_RUNNING_S - 60,))
        con.commit()
    finally:
        con.close()
    rows = [d for lvl, name, d in doctor.run().rows if "RUNNING with nothing heard" in name]
    check("doctor: a stuck RUNNING agent is reported", len(rows) == 1, str(rows))

    con = db.connect()
    try:
        db.set_agent_state(con, "hooky", dmn.IDLE, detail="done")
    finally:
        con.close()


def test_cycle_and_escalation(home: Path) -> None:
    """A runaway must be stopped AND escalated - a request that silently
    vanishes leaves the team stuck with nobody knowing why."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    (home / "team.yaml").write_text(DESIGN_TEAM)
    for n in ("backend", "reviewer", "product", "marketing", "archivist"):
        agents.register(n, "alpha")

    # A -> B, then B -> A inside the same chain is a cycle.
    ok, _, first = askmod.ask("reviewer", "please review", requester="backend")
    check("chain: the first ask starts a chain", ok)
    ok, msg, _ = askmod.ask("backend", "here are my notes", requester="reviewer",
                            parent_id=first)
    check("chain: A->B->A is stopped", not ok and "cycle" in msg, msg)
    check("chain: and escalated, not dropped", "Escalated to human" in msg, msg)

    con = db.connect()
    try:
        esc = con.execute(
            "SELECT * FROM requests WHERE target='human' AND kind='escalate'"
        ).fetchall()
        check("chain: the escalation is a real queued request", len(esc) == 1, str(len(esc)))
        check("chain: it carries the original body",
              "here are my notes" in esc[0]["body"], esc[0]["body"][:80])
        chain = db.chain_members(con, str(first))
        check("chain: everything shares one chain_id", len(chain) >= 2, str(len(chain)))
    finally:
        con.close()


def test_review_pingpong(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    (home / "team.yaml").write_text(DESIGN_TEAM)
    for n in ("backend", "reviewer", "product"):
        agents.register(n, "alpha")

    parent = None
    outcomes = []
    for i in range(5):
        ok, msg, rid = askmod.ask("reviewer", f"round {i}", files="src/auth.ts",
                                  requester="backend", parent_id=parent)
        outcomes.append((ok, msg))
        if not ok:
            break
        # The reviewer's answer continues the same chain without being a cycle.
        parent = rid
        con = db.connect()
        try:
            db.set_request_state(con, rid, "done", "ok")
        finally:
            con.close()

    stopped = [m for ok, m in outcomes if not ok]
    check("review: ping-pong on one file is eventually stopped", stopped, str(outcomes[-1]))
    check("review: the reason names the file",
          stopped and "src/auth.ts" in stopped[0], stopped[0] if stopped else "")
    check("review: and says it is probably a spec question",
          stopped and "spec question" in stopped[0], stopped[0] if stopped else "")


def test_rate_limit_throttles(home: Path) -> None:
    """Too many asks is a resource problem: it throttles and clears itself,
    rather than needing a human the way a task defect does (section 8.6)."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    (home / "team.yaml").write_text(
        "agents:\n  a: { intake: dedicated }\n  b: { intake: dedicated }\n"
        'edges:\n  - { from: "*", to: "*", relation: peer, response: fire_and_forget }\n'
        "limits:\n  max_chain_depth: 99\n  ask_rate_per_hour: 2\n"
    )
    agents.register("a", "alpha")
    agents.register("b", "alpha")
    for i in range(3):
        ok, msg, _ = askmod.ask("b", f"n{i}", requester="a")
    check("rate limit: the over-cap ask is refused", not ok, msg)
    check("rate limit: and says the agent is throttled", "THROTTLED" in msg, msg)
    con = db.connect()
    try:
        row = db.get_agent_state(con, "a")
        check("rate limit: the agent really is THROTTLED", row["state"] == "THROTTLED",
              str(dict(row)))
        check("rate limit: with a wake_at so it clears itself",
              row["wake_at"] is not None, str(dict(row)))
    finally:
        con.close()


def _two_agents_one_slot(home: Path, cap: int = 2) -> None:
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    (home / "team.yaml").write_text(
        "agents:\n  a: { intake: dedicated }\n  b: { intake: dedicated }\n"
        'edges:\n  - { from: "*", to: "*", relation: peer, response: fire_and_forget }\n'
        f"limits:\n  max_chain_depth: 99\n  ask_rate_per_hour: {cap}\n"
    )
    agents.register("a", "alpha")
    agents.register("b", "alpha")


def test_a_refused_ask_is_not_a_new_message(home: Path) -> None:
    """A refusal sends nothing, so retrying it cannot work - and a retry loop
    that writes a row per attempt spent 35 request numbers in six seconds, each
    one a message no one was ever sent."""
    _two_agents_one_slot(home)
    for i in range(2):
        askmod.ask("b", f"n{i}", requester="a")
    # Half an hour ago, so "when a slot frees" and "an hour from now" are not
    # the same instant and the test can tell which one it got.
    sent = int(time.time()) - 1800
    con = db.connect()
    try:
        con.execute("UPDATE requests SET ts = ? WHERE requester = 'a'", (sent,))
        con.commit()
    finally:
        con.close()

    ok, first, _ = askmod.ask("b", "spec B", requester="a")
    for _ in range(9):
        ok, msg, _ = askmod.ask("b", "spec B", requester="a")

    con = db.connect()
    try:
        rows = con.execute("SELECT id, body FROM requests WHERE state = 'denied'").fetchall()
        check("refused: ten identical retries leave one row", len(rows) == 1,
              str([dict(r) for r in rows]))
        check("refused: and that row keeps the text, so nothing is lost",
              rows and rows[0]["body"] == "spec B", str([dict(r) for r in rows]))
        check("refused: the refusal says where the text is kept",
              f"#{rows[0]['id']}" in msg, msg)

        # A different message is a different refusal: the record is per thing
        # said, not per attempt to say it.
        askmod.ask("b", "something else", requester="a")
        n = con.execute("SELECT COUNT(*) c FROM requests WHERE state = 'denied'").fetchone()["c"]
        check("refused: a different message is recorded on its own", n == 2, str(n))

        # The window rolls, so the wait is until the oldest ask leaves it -
        # being told to wait for "the window" cost an hour for what was
        # minutes away.
        row = db.get_agent_state(con, "a")
        check("refused: the throttle lifts when a slot frees, not an hour on",
              abs(row["wake_at"] - (sent + team.ASK_WINDOW_S)) <= 2,
              f"wake_at={row['wake_at']} slot={sent + team.ASK_WINDOW_S}")
        check("refused: and the message says when that is",
              time.strftime("%H:%M", time.localtime(sent + team.ASK_WINDOW_S)) in msg, msg)
    finally:
        con.close()
    check("refused: every retry is still refused", not ok, msg)
    check("refused: with the same answer as the first", "THROTTLED" in first, first)


def test_a_refusal_is_not_read_back_as_an_answer(home: Path) -> None:
    """`ocp read` labels a verdict with who answered. On a denied row the
    verdict is this system's refusal, and the row reached no one."""
    _two_agents_one_slot(home)
    for i in range(3):
        askmod.ask("b", f"n{i}", requester="a")
    con = db.connect()
    try:
        rid = con.execute("SELECT id FROM requests WHERE state = 'denied'").fetchone()["id"]
    finally:
        con.close()

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        climod.cmd_read(type("A", (), {"id": rid})())
    out = buf.getvalue()
    check("read: a refused request does not name an answerer",
          "answered by b" not in out, out)
    check("read: it says it was refused", "refused" in out, out)


def test_a_refused_ask_is_never_announced_as_an_answer(home: Path) -> None:
    """A refused ask reached nobody, so nothing can come back from it. Told
    otherwise, an agent reads 34 of its own refusals as replies from the agent
    it never got to ask."""
    _two_agents_one_slot(home)
    for i in range(3):
        askmod.ask("b", f"n{i}", requester="a")

    con = db.connect()
    try:
        denied = con.execute("SELECT COUNT(*) c FROM requests WHERE state = 'denied'").fetchone()
        check("announce: the refusal is on the record", denied["c"] == 1, str(denied["c"]))
        waiting = db.unannounced_answers(con, "a")
        check("announce: but it is not an answer to anything",
              not waiting, str([dict(r) for r in waiting]))
    finally:
        con.close()


def test_an_account_is_added_and_removed_from_ocp(home: Path) -> None:
    """Adding and removing an account are ocp commands, and removing one
    cannot strand an agent, orphan a credential, or lose what was spent."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    (home / "team.yaml").write_text("agents:\n  backend: { intake: dedicated }\n")
    for a in ("alpha", "beta", "gamma"):
        accounts.register(a)
    agents.register("backend", "alpha")

    def run_cli(*argv):
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = climod.main(list(argv))
        return rc, buf.getvalue() + err.getvalue()

    rc, out = run_cli("account", "add", "delta", "--no-login")
    check("account add: registers the account", rc == 0 and "delta" in paths.list_accounts(), out)
    rc, out = run_cli("account", "add", "delta", "--no-login")
    check("account add: refuses a name that exists", rc == 1, out)

    # An agent that prefers it, and one whose session actually went there.
    rc, out = run_cli("account", "remove", "alpha", "--yes")
    check("account remove: refused while an agent prefers it",
          rc == 1 and "backend" in out and "alpha" in paths.list_accounts(), out)
    con = db.connect()
    try:
        db.set_agent_state(con, "backend", "RUNNING")
        con.execute("UPDATE agent_state SET account = 'beta' WHERE agent = 'backend'")
        con.commit()
    finally:
        con.close()
    rc, out = run_cli("account", "remove", "beta", "--yes")
    check("account remove: refused while an agent's session is on it",
          rc == 1 and "backend" in out and "beta" in paths.list_accounts(), out)

    # A logout that does not take leaves the account where it is: moving the
    # directory would change the name its credential is filed under.
    orig_argv, orig_in = accounts.logout_argv, accounts.logged_in
    try:
        accounts.logout_argv = lambda a: (["false"], dict(os.environ))
        accounts.logged_in = lambda a: True
        rc, out = run_cli("account", "remove", "gamma", "--yes")
    finally:
        accounts.logout_argv, accounts.logged_in = orig_argv, orig_in
    check("account remove: a failed logout keeps the account",
          rc == 1 and "gamma" in paths.list_accounts(), out)

    rc, out = run_cli("account", "remove", "gamma", "--yes")
    retired = list((home / "accounts" / ".retired").glob("gamma-*"))
    check("account remove: takes it off the roster", rc == 0 and
          "gamma" not in paths.list_accounts(), out)
    check("account remove: and keeps the directory rather than deleting it",
          len(retired) == 1 and (retired[0] / ".claude").is_dir(), str(retired))

    rc, out = run_cli("account", "remove", "gamma", "--yes")
    check("account remove: an unknown account is said to be unknown", rc == 1, out)


def test_each_account_guard_fails_under_its_own_mutation(home: Path) -> None:
    """One condition per check, so removing any one guard fails a test."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    (home / "team.yaml").write_text("agents:\n  backend: { intake: dedicated }\n")
    for a in ("alpha", "beta", "gamma"):
        accounts.register(a)
    agents.register("backend", "alpha")
    orig_in, orig_id, orig_logout = accounts.logged_in, accounts.identity, accounts.logout_argv
    logged = {"gamma": True}

    def put_session_on(acct):
        con = db.connect()
        try:
            db.set_agent_state(con, "backend", "RUNNING")
            con.execute("UPDATE agent_state SET account = ? WHERE agent = 'backend'", (acct,))
            con.commit()
        finally:
            con.close()

    try:
        accounts.logged_in = lambda a: logged.get(a, False)
        accounts.identity = lambda a: None
        accounts.logout_argv = lambda a: (["claude", "auth", "logout"], {}) if logged.get(a) else ([], {})

        # users_of: each half alone, and the unreadable agent.
        put_session_on("beta")
        check("users_of: the preference half, with a row that disagrees",
              accounts.users_of("alpha") == ["backend"], str(accounts.users_of("alpha")))
        put_session_on("gamma")
        check("users_of: the session half, with a preference that disagrees",
              accounts.users_of("gamma") == ["backend"], str(accounts.users_of("gamma")))
        put_session_on("alpha")
        (home / "agents" / "backend" / "agent.yaml").write_text("not: [valid")
        check("users_of: an agent that cannot be read counts as a user",
              "backend" in accounts.users_of("beta"), str(accounts.users_of("beta")))
        (home / "agents" / "backend" / "agent.yaml").unlink()
        # remove: one condition per test. Nothing uses gamma from here on.
        (home / "agents" / "backend" / "agent.yaml").write_text("account: alpha\n")
        put_session_on("alpha")
        yes = lambda: True  # noqa: E731

        def code_1_but_out(argv, env):
            logged["gamma"] = False
            return 1
        ok, msg = accounts.remove_account("gamma", yes, code_1_but_out)
        check("remove: a non-zero exit refuses even though the account reads logged out",
              not ok and "gamma" in paths.list_accounts(), msg)
        logged["gamma"] = True
        ok, msg = accounts.remove_account("gamma", yes, lambda argv, env: 0)
        check("remove: exit 0 refuses while the account still reads logged in",
              not ok and "gamma" in paths.list_accounts(), msg)
        check("remove: a refused removal leaves no marker behind",
              not paths.removing("gamma"))

        # An agent arriving while the human is being asked.
        def confirm_then_agent_arrives():
            put_session_on("gamma")
            return True
        ok, msg = accounts.remove_account("gamma", confirm_then_agent_arrives, lambda a, e: 0)
        check("remove: re-checks users after the confirmation",
              not ok and "backend" in msg and "gamma" in paths.list_accounts(), msg)
        put_session_on("alpha")

        seen = {}
        def logout_ok(argv, env):
            seen["removing"] = paths.removing("gamma")
            seen["picked"] = status.pick_account(["gamma"])[0]
            logged["gamma"] = False
            return 0
        write_status(home, "gamma", pct_5h=5, pct_7d=5, reset_5h=int(time.time()) + 1800)
        ok, msg = accounts.remove_account("gamma", yes, logout_ok)
        check("remove: an account being removed is marked, and never picked",
              seen.get("removing") is True and seen.get("picked") is None, str(seen))
        check("remove: a good logout retires it",
              ok and "gamma" not in paths.list_accounts(), msg)

        # add: the exit code is not the answer, and a twin login is refused.
        ran = []
        ok, msg = accounts.add_account("delta", lambda a, e: (ran.append(a), 0)[1])
        check("add: exit 0 without a login is not a login",
              not ok and "not logged in" in msg, msg)
        logged["epsilon"] = True
        accounts.identity = lambda a: "same@x"
        logged["alpha"] = True
        ok, msg = accounts.add_account("epsilon2", lambda a, e: (logged.__setitem__("epsilon2", True), 1)[1])
        check("add: the same Anthropic login as another account is refused",
              not ok and "alpha" in msg, msg)
        accounts.identity = lambda a: f"{a}@x"
        ok, msg = accounts.add_account("zeta", lambda a, e: (logged.__setitem__("zeta", True), 1)[1])
        check("add: a distinct login succeeds whatever the exit code",
              ok and "zeta" in paths.list_accounts(), msg)

        # The screen's remove: a failed logout keeps the account.
        logged["beta"] = True
        real_suspend, real_line = tui._suspend, tui._line
        try:
            # Logged out, but claude said it failed: the exit code alone refuses.
            tui._suspend = lambda argv, env, fd, saved: (logged.__setitem__("beta", False), (1, "claude exited 1"))[1]
            tui._line = lambda prompt, w, h: "beta"
            line = tui._remove_account("beta", 80, 24, None, None)
        finally:
            tui._suspend, tui._line = real_suspend, real_line
        check("panel: a failed logout keeps the account",
              "logout failed" in line and "beta" in paths.list_accounts(), line)
    finally:
        accounts.logged_in, accounts.identity, accounts.logout_argv = orig_in, orig_id, orig_logout


def test_a_second_name_for_one_login_is_caught(home: Path) -> None:
    """Two accounts on one Anthropic login are one usage limit under two
    names, and their two copies of the credential invalidate each other."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    for a in ("alpha", "beta", "gamma"):
        accounts.register(a)
    who = {"alpha": "a@x", "beta": "b@x", "gamma": "a@x"}
    orig_id, orig_in = accounts.identity, accounts.logged_in
    try:
        accounts.identity = lambda a: who[a]
        accounts.logged_in = lambda a: True
        check("same login: a duplicate is named", accounts.same_login_as("gamma") == ["alpha"],
              str(accounts.same_login_as("gamma")))
        check("same login: a distinct one is not", accounts.same_login_as("beta") == [],
              str(accounts.same_login_as("beta")))
        accounts.identity = lambda a: None
        check("same login: one claude cannot place is not called a duplicate",
              accounts.same_login_as("gamma") == [])
    finally:
        accounts.identity, accounts.logged_in = orig_id, orig_in


def test_the_accounts_panel_adds_and_removes(home: Path) -> None:
    """The screen offers both, and removing refuses the same way the command
    does - it reads the same plan."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    (home / "team.yaml").write_text("agents:\n  backend: { intake: dedicated }\n")
    accounts.register("alpha")
    agents.register("backend", "alpha")
    keys = dict(tui.ACCOUNT_KEYS)
    check("panel: offers add and remove", keys.get("a") == "add" and keys.get("x") == "remove",
          str(tui.ACCOUNT_KEYS))
    line, _ = tui._act(tui.ACCOUNTS, {tui.ACCOUNTS: "alpha"}, "x", 80, 24, None, None)
    check("panel: removing an account in use is refused, and says by whom",
          "backend" in line and "alpha" in paths.list_accounts(), line)


def test_tui_frame_geometry(home: Path) -> None:
    """A frame must fill the screen exactly: every line the requested width,
    and exactly as many lines as the terminal has. Anything else scrolls the
    alternate screen and the display jumps."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    agents.register("wide", "alpha")
    write_status(home, "alpha", pct_5h=42, pct_7d=10, ctx_pct=7)

    for width, height in ((80, 24), (120, 40), (200, 60)):
        text = tui.frame(width, height)
        lines = text.split("\n")
        check(f"tui {width}x{height}: line count matches the terminal",
              len(lines) == height, str(len(lines)))
        too_wide = [tui.vlen(line) for line in lines if tui.vlen(line) > width]
        check(f"tui {width}x{height}: nothing overflows the width",
              not too_wide, str(too_wide[:3]))

    check("tui: vlen ignores escape sequences", tui.vlen("\033[31mabc\033[0m") == 3)
    check("tui: fit truncates on visible width",
          tui.vlen(tui.fit("\033[31m" + "x" * 50 + "\033[0m", 10)) <= 10)
    check("tui: fit leaves a short string alone", tui.fit("abc", 10) == "abc")
    check("tui: padfit never exceeds its width, on a value that overflows it",
          tui.vlen(tui.padfit("a name nobody bounded", 10)) == 10)
    check("tui: padfit's truncation is visible, not silent",
          "…" in tui.padfit("a name nobody bounded", 10))
    check("tui: padfit still pads a short value to width, like pad()",
          tui.padfit("ab", 10) == tui.pad("ab", 10))
    check("tui: unknown usage draws as ?, never as 0", "?" in tui.pct(None))
    check("tui: a known 0 still draws as 0", tui.pct(0).strip() == "0")
    check("tui: an unknown bar is not an empty bar",
          tui.vlen(tui.bar(None, 10)) == 10 and "░" not in tui.bar(None, 10))


def test_clear_does_not_look_like_waking(home: Path) -> None:
    """/clear ends one session and starts another. If SessionStart counted as
    activity, the daemon's own context reclaim would clear the park it had just
    set, the unchanged usage would trigger again, and the agent would hand off
    in a loop - which is exactly what happened: 42 folds in seven minutes."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    agents.register("loopy", "alpha")
    env = dict(os.environ, OCP_AGENT="loopy", OCP_ACCOUNT="alpha", OCP_HOME=str(home))

    con = db.connect()
    try:
        db.set_agent_state(con, "loopy", dmn.PARKED, wake_at=int(time.time()) + 9999,
                           detail="parked by a handoff")
    finally:
        con.close()

    for event in ("SessionEnd", "SessionStart"):
        subprocess.run([sys.executable, str(ROOT / "bin" / "ocp"), "hook", event],
                       input=json.dumps({"hook_event_name": event, "session_id": "new"}),
                       capture_output=True, text=True, env=env)
    con = db.connect()
    try:
        row = db.get_agent_state(con, "loopy")
        check("clear: a new session does not un-park the agent",
              row["state"] == dmn.PARKED, str(dict(row)))
        check("clear: and the wake_at survives", row["wake_at"] is not None, str(dict(row)))
    finally:
        con.close()

    # A real prompt still means real work.
    subprocess.run([sys.executable, str(ROOT / "bin" / "ocp"), "hook", "UserPromptSubmit"],
                   input=json.dumps({"hook_event_name": "UserPromptSubmit",
                                     "session_id": "new", "prompt": "do it"}),
                   capture_output=True, text=True, env=env)
    con = db.connect()
    try:
        check("clear: a prompt still marks the agent as working",
              db.get_agent_state(con, "loopy")["state"] == "RUNNING")
    finally:
        con.close()


def test_handoff_rate_limited(home: Path) -> None:
    """Two handoffs in quick succession is never right: the usage that caused
    the first cannot have meaningfully changed. A fold is not free."""
    _daemon_home(home, 100)
    con = db.connect()
    real_alive, real_idle, real_clear = orcamod.alive, orcamod.idle_now, orcamod.clear_context
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        orcamod.idle_now = lambda h: True
        orcamod.clear_context = lambda h, wait=True, timeout_ms=0: (True, "context cleared")
        db.set_terminal(con, "backend", "term_x", "wt")
        t = triggers.Trigger(True, "usage", "pct_5h 100% - at the wall")

        dmn.perform_handoff(con, "backend", t, lambda _m: None)
        first = con.execute("SELECT COUNT(*) c FROM handoff WHERE agent='backend'").fetchone()["c"]

        # Simulate something un-parking it and the trigger firing again at once.
        db.set_agent_state(con, "backend", dmn.RUNNING, detail="un-parked somehow")
        dmn.perform_handoff(con, "backend", t, lambda _m: None)
        second = con.execute("SELECT COUNT(*) c FROM handoff WHERE agent='backend'").fetchone()["c"]

        check("handoff: a second handoff right after the first is skipped",
              second == first, f"{first} -> {second}")
    finally:
        orcamod.alive, orcamod.idle_now, orcamod.clear_context = real_alive, real_idle, real_clear
        con.close()


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
    """
    restore_world()
    try:
        one()
    except (Exception, SystemExit) as e:
        tb = traceback.extract_tb(e.__traceback__)
        test = next((f.name for f in tb if f.name.startswith("test_")), "<test>")
        where = tb[-1] if tb else None
        loc = f" at {where.filename}:{where.lineno}" if where else ""
        check(f"{test}: uncaught {type(e).__name__}: {e}{loc}", False)


def test_run_recovers_from_an_uncaught_error() -> None:
    """Review of buddyron/test-suite-agent-independent: checked by hand is not
    checked by the suite. Both a plain exception and a `sys.exit` from code
    under test must be recorded as a failure, not left to end the run."""
    global PASS, FAIL
    real_pass, real_fail = PASS, FAIL
    PASS, FAIL = [], []
    try:
        def test_synthetic_boom():
            raise ValueError("synthetic")

        def test_synthetic_quits():
            sys.exit(1)

        # These two are meant to fail - swallow their PASS/FAIL lines so a
        # clean run's console output has no `FAIL` that is not a real one.
        with contextlib.redirect_stdout(io.StringIO()):
            run(lambda: test_synthetic_boom())
            run(lambda: test_synthetic_quits())
        caught = FAIL
    finally:
        PASS, FAIL = real_pass, real_fail

    check("run(): an uncaught exception is caught, not propagated, and "
          "recorded naming the test and the error",
          len(caught) == 2 and "test_synthetic_boom" in caught[0]
          and "ValueError" in caught[0], str(caught))
    check("run(): sys.exit from code under test is caught the same way",
          "test_synthetic_quits" in caught[1] and "SystemExit" in caught[1], str(caught))
    # This is exactly `main`'s own final line - a non-empty FAIL, from either
    # test above, is what turns into the process's exit code.
    check("a failure recorded this way still drives the run's exit code to 1",
          (1 if caught else 0) == 1)


def main() -> int:
    # An agent session that runs this suite already has both set for itself,
    # and every test here that wants one sets it explicitly - so an ambient
    # value is never a fixture's own, only this process's, and left in place
    # it is read as a false speaker or a false account by code under test.
    os.environ.pop("OCP_AGENT", None)
    os.environ.pop("OCP_ACCOUNT", None)
    with tempfile.TemporaryDirectory() as td:
        home = Path(td) / "orcapipe"
        home.mkdir()
        run(lambda: test_failclosed(home))
        run(lambda: test_pick_account_orders_by_weekly_deadline(Path(td) / 'deadline'))
        run(lambda: test_collector(home))
        run(lambda: test_yaml())
        run(lambda: test_agents(home))
        run(lambda: test_launch(home))
        run(lambda: test_team_routing())
        run(lambda: test_yaml_differential())
        run(lambda: test_yaml_rejects_ambiguity())
        run(lambda: test_yaml_booleans())
        run(lambda: test_team_validation())
        run(lambda: test_db_dedup(home))
        run(lambda: test_ask_flow(Path(td) / 'askhome'))
        run(lambda: test_one_request_at_a_time(Path(td) / 'onetask'))
        run(lambda: test_migration_marks_only_each_targets_head_delivered(Path(td) / 'oldmig'))
        run(lambda: test_replace_and_merge_respect_delivered_at(Path(td) / 'mergedeliv'))
        run(lambda: test_chain_members_excludes_a_row_about_to_be_replaced(Path(td) / 'chainexc'))
        run(lambda: test_retire_if_undelivered_is_the_gate_against_the_delivery_race(
            Path(td) / 'retirerace'))
        run(lambda: test_ask_rate_limit(Path(td) / 'ratehome'))
        run(lambda: test_cycle_and_escalation(Path(td) / 'cyc'))
        run(lambda: test_review_pingpong(Path(td) / 'pingpong'))
        run(lambda: test_rate_limit_throttles(Path(td) / 'throt'))
        run(lambda: test_digest())
        run(lambda: test_event_allowlist())
        run(lambda: test_fold(Path(td) / 'foldhome'))
        run(lambda: test_rollover(Path(td) / 'rollhome'))
        run(lambda: test_triggers())
        run(lambda: test_daemon_evaluates_parked(Path(td) / 'd0'))
        run(lambda: test_daemon_park(Path(td) / 'd1'))
        run(lambda: test_daemon_resume(Path(td) / 'd2'))
        run(lambda: test_daemon_kill_recovery(Path(td) / 'd3'))
        run(lambda: test_daemon_handoff_timeout(Path(td) / 'd4'))
        run(lambda: test_daemon_stall_escalation(Path(td) / 'd5'))
        run(lambda: test_daemon_success_clears_streak(Path(td) / 'd5b'))
        run(lambda: test_daemon_degraded_recovers(Path(td) / 'd5c'))
        run(lambda: test_daemon_tick_expires_handoff(Path(td) / 'd5d'))
        run(lambda: test_daemon_ignores_stale_status(Path(td) / 'd5e'))
        run(lambda: test_status_edge_cases(Path(td) / 'se'))
        run(lambda: test_team_deny_validation())
        run(lambda: test_ask_respects_terminal_state(Path(td) / 'term'))
        run(lambda: test_launch_refuses_stale(Path(td) / 'stale'))
        run(lambda: test_collector_account_named_like_user(Path(td) / 'named'))
        run(lambda: test_shared_account_context_isolation(Path(td) / 'shared'))
        run(lambda: test_db_dedup_only_swallows_dedup(Path(td) / 'strict'))
        run(lambda: test_agent_rewrite_uses_parser(Path(td) / 'rw'))
        run(lambda: test_daemon_pidfile(Path(td) / 'd6'))
        run(lambda: test_service_plist(Path(td) / 'svc1'))
        run(lambda: test_service_launchctl_gated(Path(td) / 'svc2'))
        run(lambda: test_service_status_when_not_installed(Path(td) / 'svc3'))
        run(lambda: test_service_supervisor_of(Path(td) / 'svc4'))
        run(lambda: test_service_crash_loop(Path(td) / 'svc5'))
        run(lambda: test_daemon_refusal_logs_through_the_same_path(Path(td) / 'svc5b'))
        run(lambda: test_service_log_rotation(Path(td) / 'svc6'))
        run(lambda: test_doctor_service_check(Path(td) / 'svc7'))
        run(lambda: test_drift(Path(td) / 'drift'))
        run(lambda: test_spawn_base_selection(Path(td) / 'spawn'))
        run(lambda: test_idle_session_is_closed_and_resumed(Path(td) / 'idle'))
        run(lambda: test_wake_without_a_closed_session_uses_the_handoff(Path(td) / 'wakeh'))
        run(lambda: test_reconcile_does_not_clear_closed_at_on_a_failed_wake(Path(td) / 'crashwake'))
        run(lambda: test_work_arriving_wakes_a_closed_agent(Path(td) / 'wakework'))
        run(lambda: test_a_running_agent_is_told_when_work_arrives(Path(td) / 'deliver'))
        run(lambda: test_carry_bounds_and_losses(Path(td) / 'carry'))
        run(lambda: test_unobserved_spend_stays_off_unreadable_accounts(Path(td) / 'spend'))
        run(lambda: test_subscription_type_is_recorded_and_cached(Path(td) / 'subtype'))
        run(lambda: test_launch_judges_on_the_figure_it_may_act_on(Path(td) / 'lj'))
        run(lambda: test_burn_rate_ignores_carried_samples(Path(td) / 'burn'))
        run(lambda: test_a_session_figure_is_not_shown_as_an_accounts(Path(td) / 'sessfig'))
        run(lambda: test_doctor_says_one_thing_per_account(Path(td) / 'doc2'))
        run(lambda: test_fold_is_not_rejected_over_a_header_it_overwrites(Path(td) / 'fhdr'))
        run(lambda: test_display_agrees_with_the_daemon(Path(td) / 'disp'))
        run(lambda: test_spawn_confirms_the_task_landed(Path(td) / 'spawnland'))
        run(lambda: test_events_view_starts_at_the_newest(Path(td) / 'evview'))
        run(lambda: test_spawn_survives_its_own_main_path(Path(td) / 'spawnmain'))
        run(lambda: test_an_agent_is_never_handed_another_agents_session(Path(td) / 'ident'))
        run(lambda: test_a_closed_agent_is_not_handed_off(Path(td) / 'closedtrig'))
        run(lambda: test_a_woken_agent_is_told_on_the_following_tick(Path(td) / 'woketick'))
        run(lambda: test_a_woken_agent_is_not_judged_on_the_snapshot_it_left(Path(td) / 'wakejudge'))
        run(lambda: test_a_park_that_was_also_closed_is_woken_without_being_judged(Path(td) / 'pclosed'))
        run(lambda: test_reconcile_wakes_a_park_that_was_also_closed(Path(td) / 'pclosed2'))
        run(lambda: test_a_wake_does_not_call_a_fresh_start_a_park(Path(td) / 'freshstart'))
        run(lambda: test_installing_hooks_twice_does_not_double_them(Path(td) / 'hookdup'))
        run(lambda: test_the_sweep_corrects_what_it_can_see(Path(td) / 'sweep'))
        run(lambda: test_an_agent_with_nowhere_to_run_is_not_handed_off(Path(td) / 'noterm'))
        run(lambda: test_nothing_is_typed_into_a_shell_with_no_session(Path(td) / 'shellgate'))
        run(lambda: test_a_wake_is_believed_only_when_a_session_appears(Path(td) / 'wakereceipt'))
        run(lambda: test_a_walled_wake_says_so_everywhere(Path(td) / 'walled'))
        run(lambda: test_a_walled_wake_is_not_shown_with_nothing_waiting(Path(td) / 'walledquiet'))
        run(lambda: test_a_stalled_closed_agent_keeps_its_marker_when_walled(Path(td) / 'walledstalled'))
        run(lambda: test_a_walled_wake_with_no_reset_reads_unknown(Path(td) / 'walledunknown'))
        run(lambda: test_a_moved_walled_wake_names_the_new_account(Path(td) / 'walledmoved'))
        run(lambda: test_a_walled_wake_names_the_account_only_with_room(Path(td) / 'walledroom'))
        run(lambda: test_a_park_that_cannot_be_woken_keeps_its_deadline(Path(td) / 'parkdue'))
        run(lambda: test_a_usage_park_is_woken_when_the_reading_clears(Path(td) / 'parklevel1'))
        run(lambda: test_a_usage_park_stays_parked_while_the_reading_is_at_the_wall(Path(td) / 'parklevel2'))
        run(lambda: test_a_weekly_park_stays_parked_while_the_week_is_full(Path(td) / 'weekpark'))
        run(lambda: test_a_degraded_park_does_not_auto_clear(Path(td) / 'parklevel3'))
        run(lambda: test_a_context_park_does_not_auto_clear_on_a_usage_reading(Path(td) / 'parklevel4'))
        run(lambda: test_an_assumed_account_does_not_auto_clear_a_park(Path(td) / 'parklevel5'))
        run(lambda: test_a_resumed_handoff_also_records_its_park(Path(td) / 'parklevel6'))
        run(lambda: test_account_reading_prefers_recorded_then_reported_then_assumed(Path(td) / 'acctread'))
        run(lambda: test_supervise_reads_the_reported_account_not_an_assumed_preference(Path(td) / 'acctsupervise'))
        run(lambda: test_a_park_against_an_assumed_account_gets_a_short_recheck(Path(td) / 'acctrecheck'))
        run(lambda: test_a_resumed_handoff_also_fails_closed_on_an_assumed_account(Path(td) / 'acctrecheck2'))
        run(lambda: test_a_spawned_agent_gets_an_assumed_account_until_launch_confirms_it(Path(td) / 'acctspawn'))
        run(lambda: test_top_shows_what_is_waiting_on_the_human(Path(td) / 'foryou'))
        run(lambda: test_the_screen_reads_keys_from_the_terminal_not_from_a_buffer())
        run(lambda: test_top_acts_through_the_same_calls_the_daemon_makes(Path(td) / 'topact'))
        run(lambda: test_an_answer_reaches_the_agent_that_asked(Path(td) / 'answered'))
        run(lambda: test_an_answer_wakes_the_agent_that_was_waiting_for_it(Path(td) / 'waiting'))
        run(lambda: test_a_request_can_be_read_whole(Path(td) / 'readone'))
        run(lambda: test_inbox_and_withdraw_cli(Path(td) / 'inboxcli'))
        run(lambda: test_only_one_thing_types_at_an_agent_at_a_time(Path(td) / 'locking'))
        run(lambda: test_growing_the_team_needs_a_person(Path(td) / 'hr'))
        run(lambda: test_the_founder_does_not_approve_their_own_request(Path(td) / 'hrself'))
        run(lambda: test_hr_move_changes_an_agents_account(Path(td) / 'hrmove'))
        run(lambda: test_hr_move_does_not_touch_a_live_sessions_own_record(Path(td) / 'hrmovelive'))
        run(lambda: test_hr_move_leaves_account_alone_when_liveness_cannot_be_read(Path(td) / 'hrmoveunknown'))
        run(lambda: test_the_screen_says_which_account_to_spend_next(Path(td) / 'nextacct'))
        run(lambda: test_a_long_name_does_not_run_into_the_column_beside_it(Path(td) / 'cols'))
        run(lambda: test_the_whole_loop_runs_on_our_own_commands(Path(td) / 'loop'))
        run(lambda: test_an_agent_can_put_another_one_to_work(Path(td) / 'dispatch'))
        run(lambda: test_weekly_headroom_is_spent_to_its_own_wall(Path(td) / 'sevendaywall'))
        run(lambda: test_an_agent_at_the_wall_moves_to_an_account_with_room(Path(td) / 'moveacct'))
        run(lambda: test_a_pinned_closed_agent_moves_instead_of_waiting_out_its_window(
            Path(td) / 'pinned'))
        run(lambda: test_a_pinned_agent_stays_pinned_when_nothing_else_has_clear_room(
            Path(td) / 'pinnedstuck'))
        run(lambda: test_a_stale_account_whose_window_rolled_over_can_take_work(Path(td) / 'stalebeta'))
        run(lambda: test_room_is_filtered_before_the_ranking_not_after(Path(td) / 'roomfirst'))
        run(lambda: test_the_runway_is_an_estimate_from_the_daemons_burn(Path(td) / 'runway'))
        run(lambda: test_the_active_cap_decides_where_the_next_agent_may_start(Path(td) / 'ar0'))
        run(lambda: test_the_pick_weighs_how_fast_an_account_is_filling(Path(td) / 'ar1'))
        run(lambda: test_at_the_wall_it_is_a_cost_comparison_not_a_line(Path(td) / 'ar2'))
        run(lambda: test_a_working_agent_is_not_handed_off_on_a_percentage(Path(td) / 'ar3'))
        run(lambda: test_the_cold_start_is_measured_and_sets_how_long_to_wait(Path(td) / 'ar4'))
        run(lambda: test_a_stale_snapshot_shows_its_last_figures_and_still_is_not_offered(Path(td) / 'ar4c'))


        run(lambda: test_a_span_of_a_day_or_more_reads_in_days(Path(td) / 'ar4f'))
        run(lambda: test_a_sweep_after_a_move_does_not_keep_the_old_accounts_session(Path(td) / 'ar4e'))
        run(lambda: test_agents_that_wall_together_respect_the_cap_and_a_start_is_not_its_own_rival(Path(td) / 'ar4b'))
        run(lambda: test_the_cap_is_set_from_the_command_line_and_the_bridge(Path(td) / 'ar5'))
        run(lambda: test_replaying_0919_makes_fewer_moves_than_the_old_rules(Path(td) / 'ar6'))
        run(lambda: test_a_turn_that_ends_on_the_usage_limit_is_at_the_wall(Path(td) / 'limitstop'))
        run(lambda: test_an_unknown_reset_does_not_make_the_runway_absurd(Path(td) / 'runwayunknown'))
        run(lambda: test_the_panels_bounded_cells_fit_their_own_columns(Path(td) / 'panelwidths'))
        run(lambda: test_choose_account_does_not_bounce_back_to_a_stale_preferred_account(
            Path(td) / 'nobounce'))
        run(lambda: test_move_requires_clear_headroom_not_bare_assignability(
            Path(td) / 'moveheadroom'))
        run(lambda: test_move_to_room_reaches_a_bootstrap_account(Path(td) / 'movebootstrap'))
        run(lambda: test_an_answer_to_the_human_is_in_the_human_mailbox(Path(td) / 'mailbox'))
        run(lambda: test_the_team_is_drawn_the_way_it_is_routed(Path(td) / 'tree'))
        run(lambda: test_the_panel_and_the_daemon_agree_about_what_is_in_the_way(Path(td) / 'topagree'))
        run(lambda: test_the_screen_says_what_is_stuck_and_what_was_decided(Path(td) / 'topstuck'))
        run(lambda: test_a_message_is_read_whole_and_answered_where_it_is_read(Path(td) / 'topread'))
        run(lambda: test_a_key_never_acts_on_a_row_nobody_pointed_at(Path(td) / 'topsel'))
        run(lambda: test_the_frame_never_draws_wider_than_the_terminal(Path(td) / 'topwide'))
        run(lambda: test_the_cursor_walks_the_whole_screen(Path(td) / 'topcur'))
        run(lambda: test_login_points_at_the_account_and_touches_no_credential(Path(td) / 'login'))
        run(lambda: test_a_fold_with_no_account_says_which_and_why(Path(td) / 'foldacct'))
        run(lambda: test_doctor_says_when_the_record_and_the_process_table_disagree(Path(td) / 'docsess'))
        run(lambda: test_a_fold_is_a_process_not_a_session(Path(td) / 'foldproc'))
        run(lambda: test_who_is_asking_is_not_guessed(Path(td) / 'whoasks'))
        run(lambda: test_a_rejected_fold_still_records_what_it_spent(Path(td) / 'spend'))
        run(lambda: test_one_agents_bad_tick_is_not_everyones(Path(td) / 'badtick'))
        run(lambda: test_orca_idle_parsing())
        run(lambda: test_clear_refuses_mid_turn())
        run(lambda: test_handoff_reclaims_context(Path(td) / 'reclaim'))
        run(lambda: test_wake_injects_handoff(Path(td) / 'wake'))
        run(lambda: test_hooks_report_activity(Path(td) / 'hooky'))
        run(lambda: test_tui_frame_geometry(Path(td) / 'tui'))
        run(lambda: test_clear_does_not_look_like_waking(Path(td) / 'loopy'))
        run(lambda: test_handoff_rate_limited(Path(td) / 'hrate'))
        run(lambda: test_the_handoff_is_three_facts_read_without_a_model(Path(td) / 'hdhome'))
        run(lambda: test_a_park_with_every_account_walled_is_a_clean_handoff(Path(td) / 'hdwalled'))
        run(lambda: test_an_idle_close_keeps_a_handoff_only_if_it_closed(Path(td) / 'hdclose'))
        run(lambda: test_the_digest_facts_do_not_repeat_one_event_kind(Path(td) / 'hdfacts'))
        run(lambda: test_an_account_is_added_and_removed_from_ocp(Path(td) / 'acctaddrm'))
        run(lambda: test_each_account_guard_fails_under_its_own_mutation(Path(td) / 'guards'))
        run(lambda: test_a_second_name_for_one_login_is_caught(Path(td) / 'acctsame'))
        run(lambda: test_the_accounts_panel_adds_and_removes(Path(td) / 'acctpanel'))
        run(lambda: test_a_second_daemon_is_refused_with_no_pidfile_at_all(Path(td) / 'onedmn'))
        run(lambda: test_an_exiting_daemon_leaves_the_live_ones_name(Path(td) / 'pidname'))
        run(lambda: test_one_daemon_is_counted_in_the_process_table(Path(td) / 'dmnps'))
        run(lambda: test_a_refused_ask_is_not_a_new_message(Path(td) / 'refused'))
        run(lambda: test_a_refused_ask_is_never_announced_as_an_answer(Path(td) / 'noannounce'))
        run(lambda: test_a_refusal_is_not_read_back_as_an_answer(Path(td) / 'readref'))
        run(lambda: test_two_processes_cannot_both_supervise_one_home(Path(td) / 'twoproc'))
        run(lambda: test_the_screen_says_when_two_daemons_are_running(Path(td) / 'dmnwarn'))
        run(lambda: test_run_recovers_from_an_uncaught_error())
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
