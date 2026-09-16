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

from ocp import agents, ask as askmod, db, status, team, yamlcompat  # noqa: E402

PASS, FAIL = [], []


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

    write_status(home, "nulls", pct_5h=None, pct_7d=None)
    allowed, why = status.read("nulls").assignable()
    check("null pct_5h refuses assignment", not allowed, why)
    check("null pct_5h is not treated as 0", "null" in why)
    check("headroom is None when unknown", status.read("nulls").headroom() is None)

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
    picked, _ = status.pick_account(["nulls", "fresh", "hot"])
    check("pick_account chooses the healthy one", picked == "fresh", str(picked))
    picked, reasons = status.pick_account(["nulls", "hot", "stale"])
    check("pick_account returns None when nothing qualifies", picked is None, str(picked))
    check("pick_account explains every candidate", len(reasons) == 3)

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

    # A payload with no rate_limits must yield null, not 0 - this is the
    # difference between fail-closed and a silently dead trigger.
    thin = {"session_id": "x", "model": {"id": "m"}}
    subprocess.run([str(ROOT / "libexec" / "statusline-collect.sh")],
                   input=json.dumps(thin), capture_output=True, text=True, env=env)
    st = status.read("alpha", home / "accounts" / "alpha" / "status.json")
    check("missing rate_limits becomes null, not 0", st.pct_5h is None, repr(st.pct_5h))
    allowed, _ = st.assignable()
    check("that null then refuses assignment", not allowed)


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
        check("ask: async requester parks", st["state"] == "PARKED_AWAITING", str(dict(st)))
    finally:
        con.close()

    ok, msg = askmod.resolve(rid, "looks good")
    check("resolve: closes the request", ok, msg)
    check("resolve: drains the inbox file", not inbox_file.exists())
    con = db.connect()
    try:
        st = db.get_agent_state(con, "backend")
        check("resolve: requester becomes wakeable", st["state"] == "WAKEABLE", str(dict(st)))
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


def main() -> int:
    with tempfile.TemporaryDirectory() as td:
        home = Path(td) / "orcapipe"
        home.mkdir()
        test_failclosed(home)
        test_collector(home)
        test_yaml()
        test_agents(home)
        test_launch(home)
        test_team_routing()
        test_team_validation()
        test_db_dedup(home)
        test_ask_flow(Path(td) / 'askhome')
        test_ask_rate_limit(Path(td) / 'ratehome')
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
