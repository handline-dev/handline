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

from ocp import (agents, ask as askmod, daemon as dmn, db, digest, drift,
                 events as evmod, fold as foldmod, orca as orcamod, paths,
                 spawn as spawnmod, status, team, triggers, yamlcompat)  # noqa: E402

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
    good = digest.render(digest.Digest(
        date=day, agent="backend", updated="10:00", folded_through=top,
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
    check("burn: measured from samples", abs(rate * 60 - 4.0) < 0.01, str(rate * 60))
    check("burn: a reset does not produce a negative rate",
          T.burn_rate_per_s([(0, 88), (60, 2)]) == 0.0)
    check("burn: too few samples means zero", T.burn_rate_per_s([(0, 10)]) == 0.0)


# ---------------------------------------------------------------- daemon
def _daemon_home(home: Path, pct: int | None) -> None:
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    (home / "team.yaml").write_text(DESIGN_TEAM)
    agents.register("backend", "alpha")
    write_status(home, "alpha", pct_5h=pct, pct_7d=10, ctx_pct=5,
                 reset_5h=int(time.time()) + 1800)


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
    real_clear, real_alive = orcamod.clear_context, orcamod.alive
    try:
        orcamod.alive = lambda h: True
        orcamod.clear_context = lambda h, wait=True: (cleared.append(h), (True, "context cleared"))[1]

        # No terminal recorded yet: it must say so rather than pretend.
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
    finally:
        orcamod.clear_context, orcamod.alive = real_clear, real_alive
        con.close()


def test_wake_injects_handoff(home: Path) -> None:
    _daemon_home(home, 50)
    con = db.connect()
    sent = []
    real_resume, real_alive = orcamod.resume_with, orcamod.alive
    try:
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
        test_yaml_differential()
        test_yaml_rejects_ambiguity()
        test_yaml_booleans()
        test_team_validation()
        test_db_dedup(home)
        test_ask_flow(Path(td) / 'askhome')
        test_ask_rate_limit(Path(td) / 'ratehome')
        test_digest()
        test_event_allowlist()
        test_fold(Path(td) / 'foldhome')
        test_rollover(Path(td) / 'rollhome')
        test_triggers()
        test_daemon_evaluates_parked(Path(td) / 'd0')
        test_daemon_park(Path(td) / 'd1')
        test_daemon_resume(Path(td) / 'd2')
        test_daemon_kill_recovery(Path(td) / 'd3')
        test_daemon_handoff_timeout(Path(td) / 'd4')
        test_daemon_stall_escalation(Path(td) / 'd5')
        test_daemon_success_clears_streak(Path(td) / 'd5b')
        test_daemon_degraded_recovers(Path(td) / 'd5c')
        test_daemon_tick_expires_handoff(Path(td) / 'd5d')
        test_daemon_ignores_stale_status(Path(td) / 'd5e')
        test_daemon_poll_uses_hottest(Path(td) / 'd5f')
        test_status_edge_cases(Path(td) / 'se')
        test_team_deny_validation()
        test_ask_respects_terminal_state(Path(td) / 'term')
        test_launch_refuses_stale(Path(td) / 'stale')
        test_collector_account_named_like_user(Path(td) / 'named')
        test_shared_account_context_isolation(Path(td) / 'shared')
        test_db_dedup_only_swallows_dedup(Path(td) / 'strict')
        test_agent_rewrite_uses_parser(Path(td) / 'rw')
        test_daemon_pidfile(Path(td) / 'd6')
        test_drift(Path(td) / 'drift')
        test_spawn_base_selection(Path(td) / 'spawn')
        test_orca_idle_parsing()
        test_clear_refuses_mid_turn()
        test_handoff_reclaims_context(Path(td) / 'reclaim')
        test_wake_injects_handoff(Path(td) / 'wake')
        test_handoff_drift_header(Path(td) / 'hdhome')
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"  FAILED: {f}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
