"""Tests for the parts where being wrong is expensive.

Run: python3 tests/test_ocp.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ocp import (accounts, agents, ask as askmod, daemon as dmn, db, digest, doctor, drift,
                 events as evmod, fold as foldmod, launch, observe as obsmod,
                 orca as orcamod, paths, service, spawn as spawnmod, status, team, triggers,
                 tui,
                 yamlcompat)  # noqa: E402

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


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f"  ({detail})" if detail and not cond else ""))


def write_status(home: Path, acct: str, **fields) -> None:
    d = home / "accounts" / acct
    d.mkdir(parents=True, exist_ok=True)
    payload = {"account": acct, "collected_at": int(time.time()), "session_id": "s"}
    payload.update(fields)
    (d / "status.json").write_text(json.dumps(payload))


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
    check("and says the 0% was inferred", "inferred" in why, why)
    check("quiet headroom is the full window", q.headroom() == 90, str(q.headroom()))
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
    check("89.6% rounds to 90 and is refused", not allowed, why)

    write_status(home, "fresh", pct_5h=10, pct_7d=20)
    allowed, why = status.read("fresh").assignable()
    check("healthy account is assignable", allowed, why)
    check("headroom computed", status.read("fresh").headroom() == 80)

    write_status(home, "hot", pct_5h=90, pct_7d=20)
    allowed, _ = status.read("hot").assignable()
    check("5h at 90% refuses", not allowed)

    write_status(home, "weekly", pct_5h=5, pct_7d=95)
    allowed, _ = status.read("weekly").assignable()
    check("7d at 95% refuses", not allowed)

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
    check("agent dirs created", all((d / s).is_dir() for s in ("inbox", "journal", "handoff")))
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
    write_status(home, "gamma", pct_5h=95)
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
  - { from: "*",         to: "orchestrator", kind: escalate,  creates_gate: true }

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

    d = t.route("backend", "orchestrator")
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
        hp = foldmod.handoff_path("backend")
        check("fold: handoff is written", hp.exists())
        check("fold: handoff carries the machine facts", "events folded" in hp.read_text())

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

    t = T.evaluate(85, 10)
    check("trigger: usage at 90-reserve fires", t.fired and t.kind == "usage", t.reason)
    check("trigger: usage at 84 does not fire", not T.evaluate(84, 10).fired)
    check("trigger: hard stop fires", T.evaluate(90, 10).kind == "usage")

    # Unknown usage must fire, not be treated as headroom (fail-closed).
    t = T.evaluate(None, 10)
    check("trigger: unknown usage fires fail-closed", t.fired and t.kind == "usage_unknown")

    # Projection: a heavy agent can jump 88 -> 97 between ticks.
    check("trigger: projection fires below the threshold when burn is high",
          T.evaluate(82, 10, burn=0.10).kind == "usage_projected")
    check("trigger: projection does not fire when burn is low",
          not T.evaluate(82, 10, burn=0.01).fired)
    # Projection applies at any level once burn is high enough. Gating it on a
    # fixed hot mark meant an agent at 76% burning 0.3%/s sailed past the
    # reserve during one normal 30s sleep without ever firing.
    check("trigger: a fast burner fires below the hot mark",
          T.evaluate(76, 10, burn=0.3).kind == "usage_projected")
    check("trigger: a slow burner well below the line does not fire",
          not T.evaluate(40, 10, burn=0.01).fired)
    check("trigger: a fast burner is also polled fast",
          T.poll_interval(76, 0.3) == T.POLL_HOT_S)
    check("trigger: a quiet agent is still polled slowly",
          T.poll_interval(40, 0.0) == T.POLL_NORMAL_S)

    check("trigger: polling tightens when hot", T.poll_interval(85) == T.POLL_HOT_S)
    check("trigger: polling tightens when usage is unknown",
          T.poll_interval(None) == T.POLL_HOT_S)
    check("trigger: polling relaxes when quiet", T.poll_interval(10) == T.POLL_NORMAL_S)

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
    check("burn: and it does not fire a projection",
          not T.evaluate(63, 10, burn=noisy).fired)
    check("burn: a span shorter than the minimum is not measured",
          T.burn_rate_per_s([(0, 60), (T.MIN_BURN_SPAN_S - 1, 75)]) == 0.0)
    check("burn: a real climb over a long enough span still measures",
          abs(T.burn_rate_per_s([(0, 60), (300, 75)]) * 60 - 3.0) < 0.01)
    check("burn: and a real climb near the line still fires",
          T.evaluate(83, 10, burn=0.05).kind == "usage_projected")


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
    """At 95% the daemon must park the agent with no human involved."""
    _daemon_home(home, 95)
    logs: list[str] = []
    con = db.connect()
    try:
        dmn.tick(con, logs.append, use_model=False)
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
    _daemon_home(home, 95)
    con = db.connect()
    try:
        # Park it in the past, as if the reset had already happened.
        db.set_agent_state(con, "backend", dmn.PARKED, wake_at=int(time.time()) - 10,
                           detail="parked earlier")
        write_status(home, "alpha", pct_5h=3, pct_7d=10, ctx_pct=5)
        dmn.tick(con, lambda _m: None, use_model=False)
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
        db.start_handoff(con, "hid1", "backend", "sess", "usage", 0,
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
        write_status(home, "alpha", pct_5h=99, pct_7d=10, ctx_pct=90)
        before = len(db.in_flight_handoffs(con))
        dmn.tick(con, lambda _m: None, use_model=False)
        check("daemon: no competing handoff is started",
              len(db.in_flight_handoffs(con)) == before, str(before))
    finally:
        con.close()


def test_daemon_handoff_timeout(home: Path) -> None:
    _daemon_home(home, 50)
    con = db.connect()
    try:
        db.start_handoff(con, "hid2", "backend", "sess", "usage", 0, int(time.time()) - 5)
        actions = dmn.reconcile(con, lambda _m: None)
        check("daemon: an expired handoff is timed out",
              any("timed out" in a for a in actions), str(actions))
        row = db.get_agent_state(con, "backend")
        check("daemon: a timed-out handoff degrades", row["state"] == dmn.DEGRADED)
        check("daemon: timing out increments the throttle counter",
              row["throttle_count"] == 1, str(dict(row)))
        check("daemon: the cursor is not advanced by a timeout",
              db.get_cursor(con, "fold:backend") == 0)
    finally:
        con.close()


def test_daemon_stall_escalation(home: Path) -> None:
    """STALLED must be reachable by the path the daemon actually takes.

    Driving real park -> wake -> degrade cycles, rather than hand-setting the
    counter, is the point: resetting the streak on every wake pinned it at 1
    and made this escalation unreachable while a hand-bumped test still passed.
    """
    _daemon_home(home, 95)
    con = db.connect()
    try:
        real = foldmod.fold
        foldmod.fold = lambda *a, **k: (False, "fold keeps failing")
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
                dmn.tick(con, lambda _m: None, use_model=False)
                cur = db.get_agent_state(con, "backend")
                seen.append((cur["state"], cur["throttle_count"]))

            check("daemon: the throttle streak accumulates across park/wake cycles",
                  max(t for _, t in seen) > 1, str(seen))
            final = db.get_agent_state(con, "backend")
            check("daemon: repeated degradation escalates to STALLED",
                  final["state"] == dmn.STALLED, str(seen))

            dmn.tick(con, lambda _m: None, use_model=False)
            check("daemon: STALLED is not cleared automatically",
                  db.get_agent_state(con, "backend")["state"] == dmn.STALLED)
        finally:
            foldmod.fold = real
    finally:
        con.close()


def test_daemon_success_clears_streak(home: Path) -> None:
    """A handoff that works must end the streak, or every agent eventually
    stalls just from having been busy."""
    _daemon_home(home, 95)
    con = db.connect()
    try:
        for _ in range(3):
            db.bump_counter(con, "backend", "throttle_count")
        real = foldmod.fold
        foldmod.fold = lambda *a, **k: (True, "ok")
        try:
            db.set_agent_state(con, "backend", dmn.QUEUED)
            dmn.tick(con, lambda _m: None, use_model=False)
        finally:
            foldmod.fold = real
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
        db.start_handoff(con, "h_exp", "backend", "s", "usage", 0, int(time.time()) - 5)
        dmn.tick(con, lambda _m: None, use_model=False)
        row = db.get_agent_state(con, "backend")
        check("daemon: a timed-out handoff degrades with a wake_at",
              row["state"] == dmn.DEGRADED and row["wake_at"] is not None, str(dict(row)))
        db.set_agent_state(con, "backend", dmn.DEGRADED, wake_at=int(time.time()) - 1)
        dmn.tick(con, lambda _m: None, use_model=False)
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
        db.start_handoff(con, "h_tick", "backend", "s", "usage", 0, int(time.time()) - 600)
        db.set_agent_state(con, "backend", dmn.HANDOFF)
        dmn.tick(con, lambda _m: None, use_model=False)
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
    check("stale snapshot: the trigger fires fail-closed rather than trusting it",
          t.fired and t.kind == "usage_unknown", t.reason)


def test_daemon_poll_uses_hottest(home: Path) -> None:
    """The pace is set by the hottest account, not the quietest."""
    check("poll: a hot account polls fast", triggers.poll_interval(88) == triggers.POLL_HOT_S)
    check("poll: max() over accounts is what matters",
          triggers.poll_interval(max([10, 88])) == triggers.POLL_HOT_S)
    check("poll: min() would have been wrong",
          triggers.poll_interval(min([10, 88])) == triggers.POLL_NORMAL_S)
    check("poll: unknown usage polls fast", triggers.poll_interval(None) == triggers.POLL_HOT_S)


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
    # pidfile, launchd's copy exits at once every restart without ever
    # reaching reconcile. It must still be visible to the detector.
    lines = [f"[{stamp(s)}] daemon starting - already running (pid 1), exiting"
             for s in (0, 15, 30, 45)]
    log.write_text("\n".join(lines) + "\n")
    check("service: repeated pidfile refusals are a crash loop too",
          service.crash_loop_detected(home, window_s=300, threshold=4))


def test_daemon_refusal_logs_through_the_same_path(home: Path) -> None:
    """The pidfile refusal used to be a bare `print`, invisible to
    crash_loop_detected. It must share the logger and the 'daemon starting'
    wording every other startup line uses."""
    import contextlib
    import io

    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    orig = dmn.running_elsewhere
    buf = io.StringIO()
    try:
        dmn.running_elsewhere = lambda: 999999999  # never our own pid
        with contextlib.redirect_stdout(buf):
            rc = dmn.run(once=True)
    finally:
        dmn.running_elsewhere = orig
    out = buf.getvalue()
    check("daemon: refuses when another pid holds the pidfile", rc == 1)
    check("daemon: the refusal is logged, not printed bare",
          "daemon starting - already running" in out, out)


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


def test_handoff_drift_header(home: Path) -> None:
    """The resume header must actually reach handoff/latest.md."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    agents.register("backend", "alpha")
    d = digest.Digest(date=foldmod.today(), agent="backend", updated="10:00", folded_through=1,
                      sections={"done": ["shipped the thing"], "in_progress": [], "decided": [],
                                "blocked": [], "next": [], "base_candidates": []})
    foldmod._write_handoff("backend", d, "facts here", repo=None)
    text = foldmod.handoff_path("backend").read_text()
    check("handoff: completed items appear", "shipped the thing" in text)
    check("handoff: a drift header is attached above them",
          drift.OVERLAP_HEADER in text or drift.NO_OVERLAP_HEADER in text, text[:200])
    check("handoff: unknown drift uses the cautious header",
          drift.OVERLAP_HEADER in text, text[:300])


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
]


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
    write_status(home, "delta", pct_5h=95)
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
        orcamod.send = lambda h, text, enter=True: (sent.append((h, text)), (True, ""))[1]
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

        db.add_request(con, "reviewer", "backend", "and this", None,
                       "peer", "async", None, 0, None)
        check("deliver: but a new one is", dmn.deliver_queued(con, "backend", lambda _m: None))

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
    finally:
        orcamod.alive, orcamod.idle_now, orcamod.send, orcamod.available = real
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
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        db.set_terminal(con, "backend", "term_x", "wt")
        # A closed session that was left labelled RUNNING says the opposite of
        # what happened on every screen, and the daemon skips RUNNING agents.
        db.set_agent_state(con, "backend", dmn.RUNNING, detail="was working")
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()),
            "session_id": "sess-9", "pct_5h": 20}))
        dmn.close_agent(con, "backend", lambda _m: None,
                        status.read_for_agent("backend", "alpha"))
        row = db.get_agent_state(con, "backend")
        check("close: a closed session is not left labelled RUNNING",
              row["state"] != dmn.RUNNING, row["state"])

        db.add_request(con, "reviewer", "backend", "please look at this", None,
                       "peer", "async", None, 0, None)
        sent.clear()
        dmn.tick(con, lambda _m: None, use_model=False)
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
        db.set_terminal(con, "backend", "term_x", "wt")
        hp = foldmod.handoff_path("backend")
        hp.parent.mkdir(parents=True, exist_ok=True)
        hp.write_text("# handoff · backend\n- was mid-way through the routing fix")
        ok, _ = dmn.wake_agent(con, "backend", lambda _m: None)
        check("wake: a cleared agent gets its handoff", ok and "routing fix" in sent[-1],
              sent[-1][:120] if sent else "")
        check("wake: and is told its context was cleared",
              "context was cleared" in sent[-1])
        check("wake: no launch command is typed at an agent that is still running",
              not any("launch" in t for t in sent), str(sent))
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
        "seven_day": {"used_percentage": 96}}})
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
        write_status(home, "hot", pct_5h=93, reset_5h=now + 1800)
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


def test_launch_judges_on_the_figure_it_may_act_on(home: Path) -> None:
    """Inside a window usage only rises, so a carried figure is a floor - and a
    floor at the wall is at the wall. Reading the raw field let a launch start
    on an account a carry had just placed at 95%."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    agents.register("late", "alpha")
    now = int(time.time())
    write_status(home, "alpha", pct_5h=95, reset_5h=now + 1800,
                 rl_carried=True, rl_at=now - 300)
    real_login = accounts.logged_in
    try:
        accounts.logged_in = lambda a: True  # noqa: E731 - keychain is not the subject
        try:
            launch.build("late")
            check("launch: refuses on a carried figure at the wall", False, "it started")
        except SystemExit as e:
            check("launch: refuses on a carried figure at the wall", "95%" in str(e), str(e))

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
    moves every tick. Sampling that flattens the measured rate to zero and
    usage_projected stops firing for an account that was climbing."""
    os.environ["OCP_HOME"] = str(home)
    rising = [(1000, 70), (1150, 74), (1300, 78)]
    live_burn = triggers.burn_rate_per_s(rising)
    check("burn: a rising account has a measurable rate", live_burn > 0, str(live_burn))

    # What the old sampling produced: ten ticks of the same carried figure,
    # each with a fresh timestamp, evicting the real samples.
    flattened = [(1450 + i * 30, 78) for i in range(10)]
    check("burn: flat carried samples measure as no burn at all",
          triggers.burn_rate_per_s(flattened) == 0.0)
    fired = triggers.evaluate(83, None, burn=live_burn, poll_s=30)
    check("burn: the real rate is what makes the projection fire",
          fired.fired and fired.kind == "usage_projected", fired.reason)
    check("burn: and a flattened one never would",
          not triggers.evaluate(83, None, burn=0.0, poll_s=30).fired)


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
    _daemon_home(home, 95)
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
        t = dmn.tick(con, lambda _m: None, use_model=False)
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
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
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
        check("closed: which on a live agent would fire",
              triggers.evaluate(st.trusted_pct_5h, None).fired)

        sent.clear()
        dmn.tick(con, lambda _m: None, use_model=False)
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
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
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
        dmn.tick(con, lambda _m: None, use_model=False)
        check("wake: the tick that wakes it brings the session back",
              any("--resume sess-4" in t for t in sent), str(sent))
        check("wake: and does not announce the inbox in the same breath",
              not any("ocp inbox" in t for t in sent), str(sent))

        sent.clear()
        dmn.tick(con, lambda _m: None, use_model=False)
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
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
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
        check("wake: the reading it left behind does fire",
              triggers.evaluate(st.trusted_pct_5h, st.trusted_ctx_pct).fired)

        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)
        sent.clear()
        dmn.tick(con, lambda _m: None, use_model=False)
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
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
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
        dmn.tick(con, lambda _m: None, use_model=False)
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
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
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
    """Closing writes no handoff, because closing is not a handoff. Telling an
    agent that could not be resumed "your context was cleared, below is your own
    handoff record" hands it whatever an earlier park left behind, read as
    current - the failure this branch exists to remove."""
    _daemon_home(home, 20)
    hp = foldmod.handoff_path("backend")
    hp.parent.mkdir(parents=True, exist_ok=True)
    hp.write_text("# handoff · backend\n- was mid-way through the routing fix")

    told = dmn.wake_prompt("backend", resumed=False, was_closed=True)
    check("wake: a fresh start after a close is not called a park",
          "You were parked" not in told, told[:140])
    check("wake: and is not handed a leftover handoff",
          "routing fix" not in told, told[:200])
    check("wake: it is told there is nothing folded to read",
          "no handoff" in told, told[:200])

    told = dmn.wake_prompt("backend", resumed=False, was_closed=False)
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
    _daemon_home(home, 95)
    con = db.connect()
    try:
        db.set_terminal(con, "backend", None, None)
        row = db.get_agent_state(con, "backend")
        check("no terminal: the trigger itself still fires",
              triggers.evaluate(95, 5).fired)
        check("no terminal: but the agent is not a candidate",
              not dmn.triggers_apply(row), str(dict(row)))

        dmn.tick(con, lambda _m: None, use_model=False)
        row = db.get_agent_state(con, "backend")
        check("no terminal: so it is not parked", row["state"] != dmn.PARKED, str(dict(row)))
        check("no terminal: and no fold was paid for",
              not db.in_flight_handoffs(con) and row["continuations"] == 0, str(dict(row)))

        # And with somewhere to run, the same trigger does its job.
        db.set_terminal(con, "backend", "term_x", "wt")
        dmn.tick(con, lambda _m: None, use_model=False)
        check("no terminal: an agent that has one is still parked",
              db.get_agent_state(con, "backend")["state"] == dmn.PARKED,
              str(dict(db.get_agent_state(con, "backend"))))
    finally:
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
    """A human at their own prompt is the orchestrator; a process that merely
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
        check("identity: but a human at a terminal is the orchestrator",
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

        # With no terminal recorded it must say so rather than pretend.
        db.set_terminal(con, "backend", None, None)
        ok, why = dmn._reclaim_context(con, "backend", lambda _m: None)
        check("handoff: says so when no terminal is recorded",
              not ok and "no terminal" in why, why)

        db.set_terminal(con, "backend", "term_x", "wt")
        t = triggers.Trigger(True, "context", "context 72% >= 60%")
        dmn.perform_handoff(con, "backend", t, lambda _m: None, use_model=False)
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
        dmn.perform_handoff(con, "backend", t, lambda _m: None, use_model=False)
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

        ok, why = dmn.wake_agent(con, "backend", lambda _m: None)
        check("wake: nothing to wake without a terminal", not ok, why)

        db.set_terminal(con, "backend", "term_x", "wt")
        ok, why = dmn.wake_agent(con, "backend", lambda _m: None)
        check("wake: nothing to resume from without a handoff", not ok, why)

        foldmod.handoff_path("backend").write_text("# handoff · backend\n## 다음\n- finish the thing\n")
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
    check("chain: and escalated, not dropped", "Escalated to orchestrator" in msg, msg)

    con = db.connect()
    try:
        esc = con.execute(
            "SELECT * FROM requests WHERE target='orchestrator' AND kind='escalate'"
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
    _daemon_home(home, 95)
    con = db.connect()
    real_alive, real_idle, real_clear = orcamod.alive, orcamod.idle_now, orcamod.clear_context
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        orcamod.idle_now = lambda h: True
        orcamod.clear_context = lambda h, wait=True, timeout_ms=0: (True, "context cleared")
        db.set_terminal(con, "backend", "term_x", "wt")
        t = triggers.Trigger(True, "usage", "pct_5h 95% >= 85%")

        dmn.perform_handoff(con, "backend", t, lambda _m: None, use_model=False)
        first = con.execute("SELECT COUNT(*) c FROM handoff WHERE agent='backend'").fetchone()["c"]

        # Simulate something un-parking it and the trigger firing again at once.
        db.set_agent_state(con, "backend", dmn.RUNNING, detail="un-parked somehow")
        dmn.perform_handoff(con, "backend", t, lambda _m: None, use_model=False)
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
    """
    restore_world()
    one()


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        home = Path(td) / "orcapipe"
        home.mkdir()
        run(lambda: test_failclosed(home))
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
        run(lambda: test_daemon_poll_uses_hottest(Path(td) / 'd5f'))
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
        run(lambda: test_handoff_drift_header(Path(td) / 'hdhome'))
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
