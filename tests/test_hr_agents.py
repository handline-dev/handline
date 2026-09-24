"""Tests: agents, teams, proposals, vendors, launch, trust and doctor.

Run on its own: python3 tests/test_hr_agents.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403




# ---------------------------------------------------------------- status line
def test_init_leaves_the_status_line_slot_empty(home: Path) -> None:
    """ocp installs no status line (D11): the slot is the human's. `ocp init`
    removes the ones ocp used to install, and the settings entry pointing at
    them, and leaves anyone else's statusLine where it is."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    orcamod.live_terminals = lambda: None
    shared = paths.shared_claude()
    shared.mkdir(parents=True, exist_ok=True)
    settings = shared / "settings.json"

    for old in accounts.OLD_STATUSLINES:
        (shared / old).write_text("#!/bin/sh\n")
        settings.write_text(json.dumps({"statusLine": {
            "type": "command", "command": str(shared / old)}, "theme": "dark"}))
        row = _doctor_row(doctor.run(), "shared statusLine")
        check(f"status line: doctor fails an old ocp {old} left in the slot",
              row and row[0] == doctor.FAIL, row)
        accounts.ensure_shared()
        got = json.loads(settings.read_text())
        check(f"status line: init removes the entry for {old}", "statusLine" not in got, got)
        check("status line: and keeps the rest of the settings", got.get("theme") == "dark", got)
        check(f"status line: and deletes {old}", not (shared / old).exists())
        check("status line: doctor is quiet once it is gone",
              _doctor_row(doctor.run(), "shared statusLine") is None)

    theirs = {"type": "command", "command": "npx ccstatusline"}
    settings.write_text(json.dumps({"statusLine": theirs}))
    accounts.ensure_shared()
    check("status line: someone else's is left alone",
          json.loads(settings.read_text()).get("statusLine") == theirs, settings.read_text())
    check("status line: and doctor does not flag it",
          _doctor_row(doctor.run(), "shared statusLine") is None)


def test_a_failed_settings_write_keeps_the_old_scripts(home: Path) -> None:
    """ensure_shared deletes ocp's old status line scripts only after the
    settings no longer point at them: a write that fails in between must leave
    every session with a script that still exists."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    shared = paths.shared_claude()
    shared.mkdir(parents=True, exist_ok=True)
    old = shared / accounts.OLD_STATUSLINES[0]
    old.write_text("#!/bin/sh\n")
    (shared / "settings.json").write_text(json.dumps(
        {"statusLine": {"type": "command", "command": str(old)}}))

    real_write = Path.write_text

    def failing_write(self, *a, **kw):
        if self.name.startswith("settings.json"):
            raise OSError("disk full")
        return real_write(self, *a, **kw)
    Path.write_text = failing_write
    try:
        accounts.ensure_shared()
        raised = False
    except OSError:
        raised = True
    finally:
        Path.write_text = real_write
    check("status line: a failed settings write is not swallowed", raised)
    check("status line: and the script the settings still point at is kept", old.exists())


def test_an_unreadable_settings_file_is_refused_and_left_alone(home: Path) -> None:
    """shared/settings.json holds the human's settings as well as ocp's. One
    that cannot be read as a JSON object is refused, naming the file, and
    nothing is changed - never rewritten from {}."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    shared = paths.shared_claude()
    shared.mkdir(parents=True, exist_ok=True)
    old = shared / accounts.OLD_STATUSLINES[0]
    old.write_text("#!/bin/sh\n")
    settings = shared / "settings.json"
    for bad, why in (('{"theme": "dark", "model": ', "malformed JSON"),
                     ('["a", "list"]', "JSON that is not an object")):
        settings.write_text(bad)
        try:
            accounts.ensure_shared()
            err = ""
        except ValueError as e:
            err = str(e)
        check(f"settings: {why} is refused, naming the file", str(settings) in err, err)
        check(f"settings: and {why} is left byte for byte", settings.read_text() == bad,
              settings.read_text())
        check(f"settings: and nothing else is touched for {why}", old.exists())


def test_a_settings_write_that_dies_leaves_the_old_file(home: Path) -> None:
    """The write goes through a temp file and a rename, so a write that dies
    half way leaves the previous settings.json whole, not half of the new one."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    shared = paths.shared_claude()
    shared.mkdir(parents=True, exist_ok=True)
    settings = shared / "settings.json"
    before = json.dumps({"theme": "dark", "statusLine": {"command": "npx ccstatusline"}})
    settings.write_text(before)

    real_write = Path.write_text

    def dies_half_way(self, data, *a, **kw):
        if self.name.startswith("settings.json"):
            real_write(self, data[: len(data) // 2], *a, **kw)
            raise OSError("disk full")
        return real_write(self, data, *a, **kw)
    Path.write_text = dies_half_way
    try:
        accounts.ensure_shared()
    except OSError:
        pass
    finally:
        Path.write_text = real_write
    check("settings: a write that dies half way leaves the old file whole",
          settings.read_text() == before, settings.read_text())


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


def test_trust_roots(home: Path) -> None:
    """Blocker 3 (auditor's #304): roots are an explicit list the founder
    sets, never inferred from the roster or Orca's own layout - propose/apply
    for an agent, enact directly for the founder, is_declared checks a path
    against exactly that list."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    (home / "team.yaml").write_text(
        "agents:\n  backend: { intake: dedicated }\n"
        'edges:\n  - { from: "*", to: "human", kind: escalate, creates_gate: true }\n')
    agents.register("backend", "alpha")
    root = home / "repos" / "widget"
    root.mkdir(parents=True, exist_ok=True)

    check("trust: nothing is declared to start", not trustmod.list_roots())
    check("trust: is_declared is false with nothing declared",
          not trustmod.is_declared(root))

    ok, msg = trustmod.enact(str(root), "the founder's own say-so")
    check("trust: the founder enacts directly, no proposal needed", ok, msg)
    check("trust: it is now declared", trustmod.is_declared(root))
    check("trust: a worktree under it counts too",
          trustmod.is_declared(root / "some-agent-worktree"))
    check("trust: a sibling directory does not",
          not trustmod.is_declared(root.parent / "other"))

    ok, msg = trustmod.enact(str(root), "again")
    check("trust: enacting an already-declared root is refused", not ok, msg)

    ok, msg = trustmod.propose(str(home / "repos" / "second"), "", requester="backend")
    check("trust: a proposal needs a reason", not ok, msg)

    ok, msg = trustmod.propose(str(home / "repos" / "second"), "needs it for work",
                               requester="backend")
    check("trust: an agent proposes rather than declaring directly", ok, msg)
    props = trustmod.listing()
    check("trust: the proposal is on record", len(props) == 1, str(props))
    pid, rid = props[0]["id"], props[0]["request"]

    ok, msg = trustmod.apply(pid)
    check("trust: applying before the founder answers is refused", not ok, msg)

    askmod.resolve(rid, "approve")
    ok, msg = trustmod.apply(pid)
    check("trust: applying an approved proposal declares the root", ok, msg)
    check("trust: it is now declared",
          trustmod.is_declared(home / "repos" / "second"))
    ok, msg = trustmod.apply(pid)
    check("trust: applying twice is refused", not ok, msg)

    ok, msg = trustmod.propose(str(home / "repos" / "third"), "for later",
                               requester="backend")
    pid3 = trustmod.listing()[-1]["id"]
    rid3 = trustmod.listing()[-1]["request"]
    askmod.resolve(rid3, "reject - not yet")
    ok, msg = trustmod.apply(pid3)
    check("trust: a rejected proposal is never applied", not ok, msg)
    check("trust: and the root stays undeclared",
          not trustmod.is_declared(home / "repos" / "third"))

    ok, msg = trustmod.remove(str(root))
    check("trust: the founder can remove a root, no gate needed", ok, msg)
    check("trust: it reads as undeclared again", not trustmod.is_declared(root))
    ok, msg = trustmod.remove(str(root))
    check("trust: removing an undeclared root is refused", not ok, msg)

    # note 6 (auditor's #339): is_declared and remove must agree on what a
    # root's own spelling is, or a symlinked path declared one way is never
    # removable the other way.
    real_root = home / "repos" / "real"
    real_root.mkdir(parents=True, exist_ok=True)
    link_root = home / "repos" / "linked"
    link_root.symlink_to(real_root)
    ok, msg = trustmod.enact(str(link_root), "via a symlink")
    check("trust: enacted through a symlinked spelling", ok, msg)
    check("trust: stored under the resolved (real) path",
          trustmod.list_roots()[-1]["root"] == str(real_root.resolve()),
          trustmod.list_roots()[-1])
    ok, msg = trustmod.remove(str(link_root))
    check("trust: removed through the same symlinked spelling", ok, msg)
    check("trust: gone, checked either way",
          not trustmod.is_declared(real_root) and not trustmod.is_declared(link_root))

    # note 7 (auditor's #339): apply refuses a proposal whose root was
    # changed after the founder approved a (different) one.
    ok, msg = trustmod.propose(str(home / "repos" / "fourth"), "for a test",
                               requester="backend")
    check("trust: the fourth proposal went through", ok, msg)

    def proposal_for(path) -> dict:
        # listing() is ordered by proposal id, which is random.
        want = trustmod._resolved(path)
        return next(p for p in trustmod.listing() if p["root"] == want)

    pid4 = proposal_for(home / "repos" / "fourth")["id"]
    rid4 = proposal_for(home / "repos" / "fourth")["request"]
    askmod.resolve(rid4, "approve")
    tampered = trustmod.load_proposal(pid4)
    tampered["root"] = str(home / "repos" / "not-what-was-approved")
    trustmod._save_proposal(tampered)
    ok, msg = trustmod.apply(pid4)
    check("trust: apply refuses a root that does not match what was approved",
          not ok, msg)
    check("trust: and nothing is declared from it",
          not trustmod.is_declared(home / "repos" / "not-what-was-approved"))

    # auditor's #384: a substring test let through a proposal edited to a
    # PARENT of the path the founder read, which widens consent to everything
    # under it.
    ok, msg = trustmod.propose(str(home / "repos" / "fifth" / "deep"), "for a test",
                               requester="backend")
    check("trust: the fifth proposal went through", ok, msg)
    pid5 = proposal_for(home / "repos" / "fifth" / "deep")["id"]
    askmod.resolve(proposal_for(home / "repos" / "fifth" / "deep")["request"], "approve")
    widened = trustmod.load_proposal(pid5)
    widened["root"] = str(home / "repos" / "fifth")
    trustmod._save_proposal(widened)
    ok, msg = trustmod.apply(pid5)
    check("trust: apply refuses a proposal edited to a parent of what was approved",
          not ok and "does not match" in msg, msg)
    check("trust: and neither the parent nor the child is declared from it",
          not trustmod.is_declared(home / "repos" / "fifth")
          and not trustmod.is_declared(home / "repos" / "fifth" / "deep"))
    widened["root"] = str(home / "repos" / "fifth" / "deep")
    trustmod._save_proposal(widened)
    ok, msg = trustmod.apply(pid5)
    check("trust: the path as approved still applies", ok, msg)


def test_ocp_trust_cli_is_wired(home: Path) -> None:
    """`ocp trust add/list/remove` reach the same trust.py the daemon reads,
    the founder's own path through the argparse layer."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    root = home / "repos" / "widget"
    root.mkdir(parents=True, exist_ok=True)

    class FakeStdin:
        def isatty(self) -> bool:
            return True

    real_env, real_stdin = os.environ.pop("OCP_AGENT", None), sys.stdin
    try:
        sys.stdin = FakeStdin()
        out = _capture(lambda: climod.main(
            ["trust", "add", str(root), "--why", "cli smoke"]))
        check("cli trust add: the founder declares it directly, no proposal",
              trustmod.is_declared(root), out)
        out = _capture(lambda: climod.main(["trust", "list"]))
        check("cli trust list: the new root is shown", str(root) in out, out)
        out = _capture(lambda: climod.main(["trust", "remove", str(root)]))
        check("cli trust remove: it is gone", not trustmod.is_declared(root), out)
    finally:
        sys.stdin = real_stdin
        if real_env is not None:
            os.environ["OCP_AGENT"] = real_env


def test_eligible_worktrees_splits_declared_from_not(home: Path) -> None:
    """Blocker 3 / should-fix 5 (auditor's #304): 1c only ever offers to
    trust a worktree inside a declared root, and the rest are reported as
    skipped rather than silently pressed too."""
    os.environ["OCP_HOME"] = str(home)
    agents.register("backend", "alpha")
    agents.register("frontend", "alpha")
    wt_in = home / "declared" / "backend-wt"
    wt_in.mkdir(parents=True, exist_ok=True)
    wt_out = home / "elsewhere" / "frontend-wt"
    wt_out.mkdir(parents=True, exist_ok=True)
    con = db.connect()
    try:
        db.set_terminal(con, "backend", "term_b", str(wt_in))
        db.set_terminal(con, "frontend", "term_f", str(wt_out))
    finally:
        con.close()
    trustmod.enact(str(home / "declared"), "test root")

    declared, skipped = actions.eligible_worktrees()
    check("eligible: the declared worktree is offered",
          declared == [("backend", str(wt_in))], declared)
    check("eligible: the undeclared one is only reported as skipped",
          skipped == [("frontend", str(wt_out))], skipped)


def test_trust_worktrees(home: Path) -> None:
    """1c's mechanism: one throwaway session per (agent, worktree) pair."""
    os.environ["OCP_HOME"] = str(home)
    created: list[tuple[str, str | None]] = []
    sent: list[tuple[str, str]] = []
    closed: list[str] = []
    real = (orcamod.create_terminal, orcamod.trust_prompt_showing, orcamod.confirm_trust,
            orcamod.wait_idle, orcamod.idle_now, orcamod.send, orcamod.close_terminal)
    try:
        orcamod.create_terminal = (
            lambda cmd, **k: (created.append((cmd, k.get("title"))), "term_probe")[1])
        orcamod.trust_prompt_showing = lambda h: True
        orcamod.confirm_trust = lambda h: (True, "trusted")
        orcamod.wait_idle = lambda h, *a, **k: True
        orcamod.idle_now = lambda h: False
        orcamod.send = lambda h, text, **k: (sent.append((h, text)), (True, "ok"))[1]
        orcamod.close_terminal = lambda h, **k: (closed.append(h), (True, "closed"))[1]

        result = actions.trust_worktrees("gamma", [("backend", "/w/backend")])
        check("trust: one line per worktree", len(result) == 1, str(result))
        check("trust: reports the trust", "trusted" in result[0], result[0])
        check("trust: starts the session in the worktree, under the new account",
              created and "/w/backend" in created[0][0] and "gamma" in created[0][0],
              str(created))
        check("trust: exits the throwaway session afterwards",
              sent == [("term_probe", "/exit")], sent)
        check("trust: and closes its terminal", closed == ["term_probe"], closed)

        created.clear(); sent.clear(); closed.clear()
        orcamod.trust_prompt_showing = lambda h: False
        result = actions.trust_worktrees("gamma", [("backend", "/w/backend")])
        check("trust: no prompt shown is reported, not claimed as a confirmed trust",
              not result[0].endswith(": trusted") and "no trust prompt" in result[0],
              result[0])
        check("trust: still exits and closes even then",
              sent == [("term_probe", "/exit")] and closed == ["term_probe"],
              str((sent, closed)))
    finally:
        (orcamod.create_terminal, orcamod.trust_prompt_showing, orcamod.confirm_trust,
         orcamod.wait_idle, orcamod.idle_now, orcamod.send, orcamod.close_terminal) = real


def test_trust_worktrees_quotes_the_path(home: Path) -> None:
    """Should-fix 6 (#304): a worktree path containing a quote must not
    break the command it is interpolated into."""
    os.environ["OCP_HOME"] = str(home)
    created: list[str] = []
    real = orcamod.create_terminal
    try:
        orcamod.create_terminal = lambda cmd, **k: (created.append(cmd), None)[1]
        actions.trust_worktrees("gamma", [("backend", "/tmp/a'b")])
        check("trust: the command still parses, quote and all",
              created and shlex.split(created[0].split("&&")[0])[-1] == "/tmp/a'b",
              str(created))
    finally:
        orcamod.create_terminal = real


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
    r = subprocess.run([str(ROOT / "bin" / "ocp"), "launch", "launchtest"],
                       capture_output=True, text=True, env=env)
    check("launch refuses when not logged in", r.returncode != 0)
    check("refusal names the login command", "claude /login" in (r.stdout + r.stderr))

    # Existence of the credential file is all we check - never its contents.
    (cfg / ".credentials.json").write_text("{}")
    r = subprocess.run([str(ROOT / "bin" / "ocp"), "launch", "launchtest"],
                       capture_output=True, text=True, env=env)
    check("launch execs claude once logged in", r.returncode == 0, r.stderr)
    check("launch sets CLAUDE_CONFIG_DIR to the account dir", f"CFG={cfg}" in r.stdout, r.stdout)
    check("launch sets OCP_ACCOUNT", "ACCT=gamma" in r.stdout, r.stdout)
    check("launch sets OCP_AGENT", "AGENT=launchtest" in r.stdout, r.stdout)
    check("launch passes the configured model", "--model sonnet" in r.stdout, r.stdout)

    # Over the 5h limit -> refuse, unless forced.
    write_status(home, "gamma", pct_5h=100)
    r = subprocess.run([str(ROOT / "bin" / "ocp"), "launch", "launchtest"],
                       capture_output=True, text=True, env=env)
    check("launch refuses over the 5h limit", r.returncode != 0, r.stdout)
    r = subprocess.run([str(ROOT / "bin" / "ocp"), "launch", "launchtest", "--force"],
                       capture_output=True, text=True, env=env)
    check("--force overrides the usage block", r.returncode == 0, r.stdout)

    # A missing status.json must NOT block first boot, or nothing could ever
    # start: status.json is written by the session we are trying to launch.
    (home / "accounts" / "gamma" / "status.json").unlink()
    r = subprocess.run([str(ROOT / "bin" / "ocp"), "launch", "launchtest"],
                       capture_output=True, text=True, env=env)
    check("missing status.json does not block first boot", r.returncode == 0, r.stdout)

    # Shared mcp.json: missing changes nothing, a valid one is passed through,
    # an invalid one (bad JSON, or JSON that is not {"mcpServers": {...}}) is
    # skipped (with a warning) rather than blocking launch.
    r = subprocess.run([str(ROOT / "bin" / "ocp"), "launch", "launchtest"],
                       capture_output=True, text=True, env=env)
    check("no shared mcp.json: no --mcp-config", "--mcp-config" not in r.stdout, r.stdout)

    mcp_json = home / "shared" / "mcp.json"
    mcp_json.parent.mkdir(parents=True, exist_ok=True)
    mcp_json.write_text('{"mcpServers": {"probe": {"command": "true"}}}')
    r = subprocess.run([str(ROOT / "bin" / "ocp"), "launch", "launchtest"],
                       capture_output=True, text=True, env=env)
    check("valid shared mcp.json is passed as --mcp-config",
          f"--mcp-config {mcp_json}" in r.stdout, r.stdout)

    mcp_json.write_text("{not json")
    r = subprocess.run([str(ROOT / "bin" / "ocp"), "launch", "launchtest"],
                       capture_output=True, text=True, env=env)
    check("invalid shared mcp.json does not block launch", r.returncode == 0, r.stdout)
    check("invalid shared mcp.json is skipped, not passed", "--mcp-config" not in r.stdout, r.stdout)
    check("invalid shared mcp.json is named in the warning", str(mcp_json) in r.stderr, r.stderr)

    # Well-formed JSON in the wrong shape is the case claude itself rejects at
    # startup (exit 1, before the session starts) - the exact wall-off the
    # skip exists to avoid, so it must be treated as invalid, not passed through.
    mcp_json.write_text('{"servers": {"probe": {"command": "true"}}}')
    r = subprocess.run([str(ROOT / "bin" / "ocp"), "launch", "launchtest"],
                       capture_output=True, text=True, env=env)
    check("wrong-shape shared mcp.json does not block launch", r.returncode == 0, r.stdout)
    check("wrong-shape shared mcp.json is skipped, not passed", "--mcp-config" not in r.stdout, r.stdout)

    mcp_json.unlink()
    r = subprocess.run([str(ROOT / "bin" / "ocp"), "launch", "launchtest"],
                       capture_output=True, text=True, env=env)
    check("removing shared mcp.json leaves the launch unchanged", r.returncode == 0
          and "--mcp-config" not in r.stdout, r.stdout)

    # fingerprint must move when the shared mcp.json's content does, or the
    # daemon reads a session launched before an edit as not drifted from it.
    before = launch.fingerprint("launchtest")
    check("fingerprint: no shared mcp.json is stable",
          launch.fingerprint("launchtest") == before)
    mcp_json.write_text('{"mcpServers": {"probe": {"command": "true"}}}')
    after_add = launch.fingerprint("launchtest")
    check("fingerprint: adding a shared mcp.json changes it", after_add != before)
    mcp_json.write_text('{"mcpServers": {"probe": {"command": "true"}, "x": {"command": "y"}}}')
    check("fingerprint: editing its contents changes it again",
          launch.fingerprint("launchtest") != after_add)
    mcp_json.write_text("{not json")
    check("fingerprint: an invalid file hashes the same as missing (nothing is passed either way)",
          launch.fingerprint("launchtest") == before)

    mcp_json.unlink()
    check("fingerprint: removing it again matches the original", launch.fingerprint("launchtest") == before)


def test_doctor_checks_shared_mcp_config(home: Path) -> None:
    os.environ["OCP_HOME"] = str(home)
    orcamod.live_terminals = lambda: None    # doctor asks Orca for its terminals; this is about mcp.json
    mcp_json = paths.mcp_config()
    mcp_json.parent.mkdir(parents=True, exist_ok=True)
    if mcp_json.exists():
        mcp_json.unlink()

    r = doctor.run()
    row = _doctor_row(r, "shared mcp.json")
    check("doctor is fine with no shared mcp.json", row and row[0] == doctor.OK, row)

    mcp_json.write_text('{"mcpServers": {}}')
    r = doctor.run()
    row = _doctor_row(r, "shared mcp.json")
    check("doctor is fine with a valid shared mcp.json",
          row and row[0] == doctor.OK and "valid" in row[1], row)

    mcp_json.write_text("{not json")
    r = doctor.run()
    row = _doctor_row(r, "shared mcp.json")
    check("doctor fails an invalid shared mcp.json",
          row and row[0] == doctor.FAIL and "invalid" in row[1], row)

    mcp_json.write_text('{"servers": {}}')
    r = doctor.run()
    row = _doctor_row(r, "shared mcp.json")
    check("doctor fails a wrong-shape shared mcp.json",
          row and row[0] == doctor.FAIL, row)

    mcp_json.chmod(0o000)
    try:
        r = doctor.run()
        row = _doctor_row(r, "shared mcp.json")
        check("doctor fails an unreadable shared mcp.json",
              row and row[0] == doctor.FAIL, row)
    finally:
        mcp_json.chmod(0o644)
    mcp_json.unlink()


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


def test_yaml_differential() -> None:
    """The fallback must never disagree with PyYAML silently.

    Two parsers reading the same routing policy differently is worse than
    having no fallback: `creates_gate: no` was True under one and False under
    the other, which decides whether an escalation needs a human.

    Without PyYAML on the interpreter running the suite the comparison runs
    under one that has it, and with none anywhere the test fails: a skip was
    how it went a day unexercised on the default python and then, when its
    cases were deleted, unnoticed.
    """
    try:
        import yaml  # noqa: F401
    except ImportError:
        py = _python_with_pyyaml()
        if py is None:
            check("yaml differential: a python3 with PyYAML to compare against", False,
                  "none found; pip install pyyaml, or use /usr/bin/python3")
            return
        r = subprocess.run(
            [py, "-c", "import sys; sys.path[:0] = [sys.argv[1], sys.argv[2]]; "
             "import test_ocp as t; t._check_yaml_differential(); "
             "sys.exit(1 if t.FAIL else 0)",
             str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parent.parent)],
            capture_output=True, text=True, env={k: v for k, v in os.environ.items()
                                                 if k != "OCP_AGENT"})
        check(f"yaml differential: under {py}, no silent disagreement with PyYAML",
              r.returncode == 0 and "PASS  yaml differential" in r.stdout,
              (r.stdout + r.stderr)[-600:])
        return
    _check_yaml_differential()


def test_the_bundled_parser_reads_what_pyyaml_reads_as_strings_and_refuses_the_rest() -> None:
    """Under any interpreter: what PyYAML reads as a non-string is refused, not
    returned as text, and what it reads as a string is one here."""
    for src, label in [
        ("d: 2026-09-20\n", "a date"), ("d: 2026-09-20 10:00:00\n", "a datetime"),
        ("d: 2026-9-2t1:02:03.5 +09:00\n", "a datetime with a zone"),
        ("n: +.inf\n", "+.inf"), ("n: -.inf\n", "-.inf"), ("n: .INF\n", ".INF"),
        ("n: .nan\n", ".nan"), ("n: 1_000\n", "an underscored int"),
        ("n: -1_000.5\n", "a signed underscored float"), ("n: +1_0.5e+3\n", "a signed exponent"),
        ("n: 0x_1F\n", "an underscored hex"), ("n: 0b1_0\n", "an underscored binary"),
        ('q: "a\\nb"\n', "an escape other than \\\\ and \\\""),
    ]:
        try:
            got = yamlcompat.parse(src)
            check(f"yaml: refuses {label}", False, f"parsed as {got!r}")
        except yamlcompat.ConfigError:
            check(f"yaml: refuses {label}", True)
    got = _parsed("a: y\nb: n\nc: yEs\nd: tRUE\ne: nULL\nf: -.5\ng: 08\nh: 1e3\n")
    check("yaml: y, n, mixed case, a signed .5, 08 and 1e3 are strings, as in PyYAML",
          got == {"a": "y", "b": "n", "c": "yEs", "d": "tRUE", "e": "nULL", "f": "-.5",
                  "g": "08", "h": "1e3"}, str(got))
    check("yaml: the spellings PyYAML takes are still booleans and null",
          _parsed("a: Yes\nb: OFF\nc: True\nd: ~\ne: NULL\n")
          == {"a": True, "b": False, "c": True, "d": None, "e": None})
    check("yaml: a doubled single quote, and the two escapes, read as one character",
          _parsed("a: 'it''s'\nb: \"x\\\"y\"\nc: \"p\\\\q\"\n")
          == {"a": "it's", "b": 'x"y', "c": "p\\q"})
    unread = [(v, _roundtrip(v)) for v in DUMP_STRINGS if _roundtrip(v) != {"k": v}]
    check("yaml: everything dump writes is read back as the same string", not unread,
          str(unread[:3]))
    check("yaml: every non-string dump writes is read back as the same value",
          all(repr(_roundtrip(v)) == repr({"k": v}) for v in DUMP_VALUES))
    check("yaml: a why with an arrow is written quoted and read back",
          _roundtrip("handoff -> resolve, then merge", "why")
          == {"why": "handoff -> resolve, then merge"})
    # _outside_quotes decides what _check may refuse, and breaking it only makes
    # _check refuse more, which the differential tolerates - so these two say
    # what must be read, not merely not misread.
    check("yaml: an indicator inside single quotes is text",
          _parsed("a: 'a & b'\n") == {"a": "a & b"}, str(_parsed("a: 'a & b'\n")))
    check("yaml: an escaped quote does not end the quoted text",
          _parsed('a: "x\\" & y"\n') == {"a": 'x" & y'}, str(_parsed('a: "x\\" & y"\n')))
    check("yaml: a quoted key is its text, whatever it looks like",
          _parsed("'1': a\n\"on\": b\n'2026-09-20': c\n") == {"1": "a", "on": "b", "2026-09-20": "c"})
    check("yaml: a plain key that is text to PyYAML too is read unchanged",
          _parsed("y: 1\n1e3: 2\n08: 3\na b: 4\n-: 5\n")
          == {"y": 1, "1e3": 2, "08": 3, "a b": 4, "-": 5})
    check("yaml: a plain key PyYAML reads as a number, bool, null or date is refused",
          all(_refused(f"{k}: x\n") for k in ("1", "on", "Yes", "true", "null", "~", "1.5", "0x10",
                                             "1_000", "2026-09-20", "+.inf", "12:30", "")),
          str([k for k in ("1", "on", "null", "~", "") if not _refused(f"{k}: x\n")]))
    check("yaml: in a list item, a flow map and a nested map too",
          all(_refused(src) for src in ("- on: a\n", "{a: 1, 1: x}\n", "x:\n  1: a\n",
                                        "x:\n  - { true: a }\n")))
    check("yaml: text after a quoted key's closing quote is refused, not read as the key",
          all(_refused(src) for src in ('"a" b: 1\n', "'a' b: 1\n", '{ "a" b: 1 }\n')))
    check("yaml: a key that only contains an indicator is text, as in PyYAML",
          _parsed("a>b: 1\nx*y: 2\nq&r: 3\n") == {"a>b": 1, "x*y": 2, "q&r": 3})
    try:
        yamlcompat.parse(">x: 1\n")
        why_key = ""
    except yamlcompat.ConfigError as e:
        why_key = str(e)
    check("yaml: a key that starts with an indicator says what it is and how to fix it",
          "key '>x'" in why_key and "quote it" in why_key and "has PyYAML" not in why_key, why_key)
    check("yaml: an anchor or an alias where a key starts is refused",
          _refused("&a k: v\n") and _refused("*a: v\n"))
    check("yaml: a quote opened and never closed is refused, not read with its quote",
          all(_refused(src) for src in ('a: "x & y\n', "a: 'x & y\n", 'a: "x" y\n',
                                        "a: 'x' 'y'\n", 'a: "x\\"\n', 'a: "\n')))
    check("yaml: bare, the same arrow is still refused, not read as anything else",
          _refused("why: handoff -> resolve\n"))
    for src, label in [("n: +12:30\n", "a signed sexagesimal"), ("n: 1_0:30\n", "an underscored one"),
                       ("n: +12:30.5\n", "a signed fractional one")]:
        check(f"yaml: refuses {label}", _refused(src))
    check("yaml: .iNf and -.nan are strings, as in PyYAML",
          _parsed("a: .iNf\nb: -.nan\n") == {"a": ".iNf", "b": "-.nan"})
    for value in (float("inf"), float("-inf"), float("nan")):
        try:
            yamlcompat.dump({"k": value})
            check(f"yaml: dump refuses {value!r}, which no parser reads back", False)
        except yamlcompat.ConfigError:
            check(f"yaml: dump refuses {value!r}, which no parser reads back", True)
    try:
        yamlcompat.dump({"k": "a\nb"})
        check("yaml: dump refuses a control character rather than write a broken line", False)
    except yamlcompat.ConfigError:
        check("yaml: dump refuses a control character rather than write a broken line", True)


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


def test_a_poisoned_proposal_field_is_refused_at_the_write_not_read_back_on_the_founders_screen(
        home: Path) -> None:
    """The real path: a CSI byte in --repo, through hr's own save/load/
    escalation. Before the fix this wrote, reloaded, and reached the text
    the founder's screen draws - reproduced end to end under the bundled
    parser (no PyYAML), which has no printable-character check at all,
    unlike real PyYAML's own Reader (a side effect of PyYAML's spec
    compliance, not a guard ocp controls - the bundled parser is what most
    of the fleet actually runs under)."""
    _daemon_home(home, 20)
    poisoned = "repo\u009b31mEVIL\u009bm"
    prop = {"id": "poisontest", "kind": "add", "agent": "newagent",
            "requested_by": "someone", "why": "test", "created": int(time.time()),
            "account": "alpha", "repo": poisoned}
    try:
        hr.save(prop)
        check("hr: a proposal with a C1 byte in a field is refused at save", False,
              "saved instead of raising")
    except yamlcompat.ConfigError:
        check("hr: a proposal with a C1 byte in a field is refused at save", True)
    check("hr: nothing was written", hr.path_of("poisontest").exists() is False)


def test_agents_read_refuses_a_value_a_hand_edit_wrote_with_a_control_character(
        home: Path) -> None:
    """#1571's ruling: REFUSE on read, not strip. A hand-edited agent.yaml -
    never written by dump(), so never checked before this - reaches
    agents.read unguarded until now.

    A C1 byte (0x9b) is refused either way, but under real PyYAML its own
    Reader (check_printable) refuses it first - a guard ocp does not
    control, and the same reason test_a_poisoned_proposal_field... above
    needs the bundled parser to show its own message at all. A tab inside a
    quoted scalar is different: PyYAML accepts it (measured: yaml.safe_load
    reads 'k: \"a\\tb\"' as the literal tab), so it always reaches
    refuse_unsafe_values, under either parser - that is what the file/key
    assertions below use, so they hold regardless of which python3 runs
    the suite."""
    _daemon_home(home, 20)
    af = agents.agent_file("backend")
    af.write_text(af.read_text().rstrip("\n") + '\nrepo: "before\u009bafter"\n')
    try:
        agents.read("backend")
        check("agents.read: refuses a C1 byte in repo", False, "read instead of raising")
    except yamlcompat.ConfigError:
        check("agents.read: refuses a C1 byte in repo", True)

    af.write_text(af.read_text().split("\nrepo:")[0] + '\nrepo: "before\tafter"\n')
    try:
        agents.read("backend")
        check("agents.read: refuses a tab in repo (reaches refuse_unsafe_values "
              "under either parser)", False, "read instead of raising")
    except yamlcompat.ConfigError as e:
        check("agents.read: refuses a tab in repo (reaches refuse_unsafe_values "
              "under either parser)", True)
        check("agents.read: names the file", str(af) in str(e), str(e))
        check("agents.read: names the key", "'repo'" in str(e), str(e))

    # Control: a clean agent.yaml still reads.
    agents.register("clean", "alpha")
    data = agents.read("clean")
    check("agents.read: a clean file still reads", isinstance(data, dict) and data, str(data))


def test_a_name_that_yaml_would_not_read_as_text_is_refused_where_it_is_typed(home: Path) -> None:
    """A name is written unquoted into team.yaml, agent.yaml and config.yaml. `no`
    is False to PyYAML and a refusal to the bundled parser, and a refusal on a
    key makes the whole file unloadable, so the name is turned away when it is
    typed and not when every command next reads the roster."""
    restore_world()
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    bad = ("no", "on", "off", "yes", "1", "2026-09-20", "null", "~", "true", "0x10", "a: b")
    good = ("harbor", "harbor.deck", "orcapipe.purser", "a-b_c", "y", "1e3", "é")
    check("names: what yaml reads as something else is not plain text",
          not any(yamlcompat.is_plain_text(n) for n in bad), str([n for n in bad if yamlcompat.is_plain_text(n)]))
    check("names: what ocp names its agents is",
          all(yamlcompat.is_plain_text(n) for n in good), str([n for n in good if not yamlcompat.is_plain_text(n)]))
    refused = []
    for n in bad:
        try:
            agents.register(n, "alpha")
            refused.append(n)
        except ValueError:
            pass
    check("names: agents.register turns each away and creates nothing",
          not refused and not any((paths.agent_dir(n)).exists() for n in bad), str(refused))
    for n in good:
        agents.register(n, "alpha")
    check("names: and takes each ordinary one", set(good) <= set(paths.list_agents()))
    # auditor #1774: is_plain_text passes a tab, so only register's own
    # unsafe-character check stands between it and the roster.
    try:
        agents.register("a\tb", "alpha")
        check("names: agents.register turns away a name holding a tab", False, "registered")
    except ValueError as e:
        check("names: agents.register turns away a name holding a tab",
              "bad agent name" in str(e) and not paths.agent_dir("a\tb").exists(), str(e))
    for kind in ("add", "rename"):
        ok, said = hr._draft(kind, "no" if kind == "add" else "harbor", "x", "why", "alpha",
                             None, None, None, "on" if kind == "rename" else None)
        check(f"names: hr {kind} is refused before anything is raised or written",
              ok == {} and "bad agent name" in said and "choose another" in said, str((ok, said)))
    con = db.connect()
    try:
        before = len(actions.messages(con))
        ok, said = hr.propose("add", "no", requester="harbor", why="w", account="alpha")
        check("names: hr.propose of one queues nothing for the founder",
              not ok and len(actions.messages(con)) == before, said)
    finally:
        con.close()
    text = "agents:\n  harbor: { intake: dedicated }\n"
    check("names: the line hr writes for an agent called no would make team.yaml unloadable",
          _refused(text + "  no: { intake: dedicated }\n"))
    try:
        hr._add_text(text, "no", None)
        check("names: so the writer refuses to write it", False)
    except yamlcompat.ConfigError:
        check("names: so the writer refuses to write it", True)
    # The team's name is written as a block key too, and the guard has to stand at
    # every door: proposing, and the write itself, which an old proposal reaches.
    paths.team_file().write_text(team.STARTER)
    before = paths.team_file().read_text()
    listed = yamlcompat.parse(team.STARTER)
    mover = next(iter(listed["agents"]))
    ok, said = hr._draft("add", "dev1", "x", "why", "alpha", "no", None, None, None)
    check("names: hr add of an agent into a team called no is refused at the proposal",
          ok == {} and "bad team name" in said and "choose another" in said, str((ok, said)))
    done, said = hr._in_team_yaml("dev1", "no", add=True)
    check("names: and at the write, leaving team.yaml as it was",
          not done and "bad team name" in said and paths.team_file().read_text() == before, said)
    for label, call in (
            ("a rename into a name yaml reads as a bool",
             lambda: hr._rename_text(team.STARTER, mover, "on", None)),
            ("a rename that joins a team called no",
             lambda: hr._rename_text(team.STARTER, mover, "newname", "no")),
            ("a join of an agent called no", lambda: hr._join_team(team.STARTER, "no", "eng"))):
        try:
            call()
            check(f"names: {label} is refused at the write", False)
        except yamlcompat.ConfigError:
            check(f"names: {label} is refused at the write", True)
    if yamlcompat.HAVE_PYYAML:
        lockout = "teams:\n  no: [dev1]\n"
        check("names: under PyYAML the same line loads, with the key False - which the "
              "bundled parser refuses, so only the guard stands between the two",
              False in yamlcompat.load(lockout)["teams"] and _refused(lockout))
    for n in bad[:6]:
        try:
            accounts.register(n)
            check(f"names: accounts.register turns away {n!r}", False)
        except ValueError:
            check(f"names: accounts.register turns away {n!r}", True)


def test_one_unreadable_proposal_does_not_take_the_hr_listing_down(home: Path) -> None:
    """`hr.listing()` feeds `ocp hr list` and the bridge's waiting list. A file
    that does not parse used to raise out of both, and the parser refuses more
    than it did, so the same one file would have hidden every proposal. It is
    left out and named, and a file that parses to something that is not a
    proposal is treated the same way."""
    restore_world()
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    d = hr.dir_()
    (d / "good.yaml").write_text("id: good\nkind: add\nagent: newone\nstate: proposed\n"
                                 "requested_by: x\n")
    (d / "broken.yaml").write_text("id: broken\nkind: add\nagent: [unclosed\n")
    (d / "alist.yaml").write_text("- just\n- a list\n")
    (d / "other.yaml").write_text("colour: blue\n")
    (d / "kindless.yaml").write_text("id: k\nagent: a\n")
    (d / "binary.yaml").write_bytes(b"id: \xff\xfe\n")
    skipped: list[str] = []
    rows = hr.listing(skipped)
    check("hr listing: the one good proposal is listed",
          [r["id"] for r in rows] == ["good"], str(rows))
    check("hr listing: each file left out is named, with the reason",
          sorted(x.split(":")[0] for x in skipped)
          == ["alist.yaml", "binary.yaml", "broken.yaml", "kindless.yaml", "other.yaml"]
          and sum("not a proposal" in x for x in skipped) == 3, str(skipped))
    check("hr listing: asked for no report, it still returns the good ones",
          [r["id"] for r in hr.listing()] == ["good"])
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = climod.main(["hr", "list"])
    check("hr list: prints the good proposal and says what it skipped, and succeeds",
          code == 0 and "good" in out.getvalue() and "skipped, cannot be read - broken.yaml" in err.getvalue()
          and "Traceback" not in err.getvalue(), out.getvalue() + err.getvalue())
    con = db.connect()
    try:
        waits = actions.founder_waits(con)
        check("the bridge's waiting list is built with the bad files present",
              isinstance(waits, list))
    finally:
        con.close()
    ok, said = hr.apply("broken")
    check("hr apply: a proposal that cannot be read says so instead of raising",
          not ok and "cannot be read" in said, said)
    ok, said = hr.apply("other")
    check("hr apply: a file that is not a proposal is said not to be one",
          not ok and "not a proposal" in said, said)
    ok, said = hr.apply("binary")
    check("hr apply: one that is not text says so too", not ok and "cannot be read" in said, said)
    for f in d.glob("*.yaml"):
        if f.name != "good.yaml":
            f.unlink()
    skipped.clear()
    check("hr listing: with the bad files gone nothing is reported",
          [r["id"] for r in hr.listing(skipped)] == ["good"] and skipped == [])


def test_a_walled_wake_is_said_once_not_every_tick(home: Path) -> None:
    """A closed agent with work waiting is retried every tick for as long as its
    account sits at the wall, and the wall is one reason that lasts for hours.
    `_wake_agent` returns it and the tick says it once; `_wake_agent` used to log
    it as well, on every retry, which is a line per tick for the same sentence."""
    _daemon_home(home, 100)
    con = db.connect()
    real_avail, real_alive, real_run = orcamod.available, orcamod.alive, orcamod._run

    def no_orca(args, timeout=180):
        raise AssertionError(f"reached the real orca: {args}")
    try:
        orcamod.available, orcamod.alive, orcamod._run = (lambda: True), (lambda h: True), no_orca
        db.set_closed(con, "backend", "sess-1")
        db.add_request(con, "marina", "backend", "please look", None, "peer", "async",
                       None, 0, None)
        dmn._SAID.clear()
        logged: list[str] = []
        for _ in range(4):
            dmn.supervise(con, "backend", logged.append, int(time.time()), {}, [])
        wall = [m for m in logged if "5h window" in m]
        check("wall wake: the account really is the reason, so the fixture reaches the wall",
              wall, str(logged))
        check("wall wake: it is said once across four ticks, not once per tick",
              len(wall) == 1, str(logged))
        ok, why = dmn.wake_agent(con, "backend", logged.append)
        check("wall wake: and the wake itself still refuses and returns the reason",
              not ok and "5h window" in why, why)
        logged.clear()
        dmn.wake_agent(con, "backend", logged.append)
        check("wall wake: a direct wake does not log it either - its caller has the reason",
              not [m for m in logged if "5h window" in m], str(logged))
    finally:
        orcamod.available, orcamod.alive, orcamod._run = real_avail, real_alive, real_run
        dmn._SAID.clear()
        con.close()


def test_a_rename_names_the_agents_orca_worktree(home: Path) -> None:
    """What the founder reads in Orca's sidebar is the worktree's display name,
    so a rename that left it alone looks like a rename that failed. The label is
    set, read back, and never moves anything: no folder, no memory, no terminal."""
    _daemon_home(home, 20)
    (home / "team.yaml").write_text("""
teams:
  dev: [oldname]
agents:
  oldname: { intake: dedicated }
  marina:  { intake: dedicated }
edges:
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
""")
    agents.register("oldname", "alpha")
    wt = home / "trees" / "oldname"
    wt.mkdir(parents=True)
    con = db.connect()
    real_run, real_avail = orcamod._run, orcamod.available
    fake = FakeOrca({str(wt): "oldname"})
    try:
        sessions_are()
        db.set_terminal(con, "oldname", "term_x", f"repo-id::{wt}")
        orcamod._run, orcamod.available = fake.run, lambda: True
        ok, said = hr.enact("rename", "oldname", to="newname", why="the name it should have had")
        check("label: the rename went through", ok and "newname" in paths.list_agents(), said)
        check("label: Orca's worktree is now named for the agent, and the message says so",
              fake.names[str(wt)] == "newname" and "Orca now labels" in said, said + str(fake.names))
        sets = [c for c in fake.calls if c[1] == "set"]
        check("label: exactly one set, on that path, with the new name and nothing else",
              len(sets) == 1 and sets[0] == ["worktree", "set", "--worktree", f"path:{wt}",
                                            "--display-name", "newname", "--json"], str(sets))
        check("label: the checkout was not moved and the recorded worktree is the same",
              wt.is_dir() and db.get_agent_state(con, "newname")["worktree"] == f"repo-id::{wt}")

        # `hr relabel` is the same call for a rename already applied.
        fake.names[str(wt)] = "stale"
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = climod.main(["hr", "relabel", "newname"])
        check("label: hr relabel names it again, for a rename that was applied before",
              code == 0 and fake.names[str(wt)] == "newname" and "Orca now labels" in out.getvalue(),
              out.getvalue() + err.getvalue())

        # Orca says ok and does not change the name: not reported as done.
        fake.sets, fake.names[str(wt)] = False, "stale"
        done, said = hr.label(con, "newname")
        check("label: an ok that changed nothing is not a label",
              not done and "would not set the label" in said
              and f"--display-name newname" in said, said)

        # Orca is not there: the rename stands, and the command to run is given.
        orcamod.available = lambda: False
        done, said = hr.label(con, "newname")
        check("label: with no Orca on PATH it says what to run instead",
              not done and "not on PATH" in said and f"path:{wt}" in said, said)
        orcamod.available = lambda: True

        # Nothing recorded, or a folder that is gone: nothing to label, no call made.
        n = len(fake.calls)
        agents.register("bare", "alpha")
        db.set_terminal(con, "marina", "term_y", f"repo-id::{home / 'gone'}")
        check("label: an agent with no worktree, or a folder that is gone, is left alone",
              hr.label(con, "bare") == (True, "") and hr.label(con, "marina") == (True, "")
              and len(fake.calls) == n, str(fake.calls[n:]))
        check("label: nothing but show and set ever reached Orca",
              all(c[0] == "worktree" and c[1] in ("show", "set") for c in fake.calls))
    finally:
        orcamod._run, orcamod.available = real_run, real_avail
        con.close()


def test_the_relabel_recovery_command_quotes_what_it_names(home: Path) -> None:
    """hr.label's recovery command was an f-string with no quoting - a worktree
    path or an agent name with a space in it produced a command that broke on
    the exact thing it exists to let a human paste and run."""
    _daemon_home(home, 20)
    con = db.connect()
    real_avail = orcamod.available
    try:
        wt = home / "trees" / "has space"
        wt.mkdir(parents=True)
        db.set_terminal(con, "has space", "term_x", f"repo-id::{wt}")
        orcamod.available = lambda: False
        done, said = hr.label(con, "has space")
        cmd = said.split("`")[1]
        parts = shlex.split(cmd)
        check("label: the path and the name each round-trip as one shell word",
              not done and parts[parts.index("--worktree") + 1] == f"path:{wt}"
              and parts[parts.index("--display-name") + 1] == "has space", said)
    finally:
        orcamod.available = real_avail
        con.close()


def test_a_renamed_agents_worktree_follows_it_with_its_work_and_memory(home: Path) -> None:
    """harbor #753: a rename leaves Orca's label right (`hr.label`, already
    covered by test_a_rename_names_the_agents_orca_worktree) but the folder,
    and so Claude's per-project memory, on the old name. `ocp hr rehome` is
    the other half - actually moving it. Driven on a throwaway git repo with
    an uncommitted file, two accounts' memory and a transcript."""
    _daemon_home(home, 20)
    accounts.register("beta")
    repo = home / "repo"
    repo.mkdir()
    def git(*a, cwd=repo):
        return subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True, check=True)
    git("init", "-q", "-b", "main")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
    old_wt = home / "wts" / "marina"
    git("worktree", "add", "-q", "-b", "work", str(old_wt))
    (old_wt / "Strings.swift").write_text("uncommitted\n")
    old_key = accounts.memory_project_dir("alpha", str(old_wt))
    new_wt = home / "wts" / "harbor.marina"
    new_key = accounts.memory_project_dir("alpha", str(new_wt))
    for acct in ("alpha", "beta"):
        proj = paths.account_claude(acct) / "projects" / old_key
        (proj / "memory").mkdir(parents=True)
        (proj / "memory" / "note.md").write_text(f"remember {acct}\n")
        (proj / "sess-1.jsonl").write_text("{}\n")
    agents.register("harbor.marina", "alpha")
    con = db.connect()
    real_live = obsmod.live_sessions
    try:
        # As `ocp launch` records it: an Orca selector, not a bare path.
        db.set_terminal(con, "harbor.marina", "term_m", f"repo-id::{old_wt}")
        obsmod.live_sessions = lambda: {"harbor.marina": 123}
        ok, said = rehome.move(con, "harbor.marina", "marina")
        check("rehome: refused while the session is live",
              not ok and "session running" in said and old_wt.is_dir(), said)
        obsmod.live_sessions = lambda: None
        ok, said = rehome.move(con, "harbor.marina", "marina")
        check("rehome: refused when liveness cannot be read", not ok and old_wt.is_dir(), said)
        obsmod.live_sessions = lambda: {}
        ok, said = rehome.move(con, "harbor.marina", "someone-else")
        check("rehome: refused when the folder does not carry the old name",
              not ok and old_wt.is_dir(), said)
        new_wt.mkdir()
        ok, said = rehome.move(con, "harbor.marina", "marina")
        check("rehome: refused when the destination exists", not ok and old_wt.is_dir(), said)
        new_wt.rmdir()

        # A destination in the way on one account: refused before anything moves.
        blocker = paths.account_claude("beta") / "projects" / new_key
        blocker.mkdir(parents=True)
        ok, said = rehome.move(con, "harbor.marina", "marina")
        check("rehome: a memory dir already at the destination refuses, nothing moved",
              not ok and old_wt.is_dir() and (old_wt / "Strings.swift").exists()
              and not new_wt.exists()
              and all((paths.account_claude(a) / "projects" / old_key).is_dir()
                      for a in ("alpha", "beta")), said)
        blocker.rmdir()
        # And a rename that fails on the SECOND account after the first went
        # through: the first is put back, and so is the worktree.
        real_rename = Path.rename
        def flaky(self, target):
            if "beta" in str(self) and self.name == old_key:
                raise OSError("simulated")
            return real_rename(self, target)
        Path.rename = flaky
        try:
            ok, said = rehome.move(con, "harbor.marina", "marina")
        finally:
            Path.rename = real_rename
        check("rehome: a failure on a later account undoes the earlier ones and the worktree",
              not ok and old_wt.is_dir() and (old_wt / "Strings.swift").exists()
              and not new_wt.exists()
              and all((paths.account_claude(a) / "projects" / old_key).is_dir()
                      and not (paths.account_claude(a) / "projects" / new_key).exists()
                      for a in ("alpha", "beta")), said)
        # Orca as measured: the old entry has a comment and a parent; after the
        # move it knows the new path only as an automatic entry.
        calls: list[list[str]] = []
        real_avail, real_run, real_sleep = orcamod.available, orcamod._run, time.sleep
        moved_yet = {"v": False}
        def fake_run(args, timeout=180):
            calls.append(args)
            if args[:2] == ["worktree", "show"]:
                if args[3] == f"path:{old_wt}":
                    w = {"id": f"repo-id::{old_wt}", "comment": "onboarding",
                         "parentWorktreeId": "repo-id::/p",
                         "workspaceStatus": "in-progress",
                         "childWorktreeIds": ["repo-id::/kid"]}
                elif moved_yet["v"]:
                    w = {"comment": "", "displayNameMode": "automatic"}
                else:
                    w = None
                return (w is not None), json.dumps({"ok": w is not None,
                                                    "result": {"worktree": w or {}}})
            if args[:2] == ["worktree", "list"]:
                return True, json.dumps({"ok": True, "result": {"worktrees": []}})
            if args[:2] == ["terminal", "list"]:
                return True, json.dumps({"ok": True, "result": {"terminals": tabs["v"]}})
            return True, json.dumps({"ok": True})
        tabs = {"v": []}
        orcamod.available = lambda: True
        orcamod._run = fake_run
        time.sleep = lambda s_: moved_yet.update(v=True)
        try:
            tabs["v"] = [{"worktreePath": str(old_wt), "handle": "term_1"}]
            ok, said = rehome.move(con, "harbor.marina", "marina")
            check("rehome: refused while a terminal is open in it",
                  not ok and "terminal" in said and old_wt.is_dir(), said)
            tabs["v"] = []
            ok, said = rehome.move(con, "harbor.marina", "marina")
        finally:
            orcamod.available, orcamod._run, time.sleep = real_avail, real_run, real_sleep
        sets = [c for c in calls if c[:2] == ["worktree", "set"]]
        check("rehome: Orca's new entry is named for the agent, fixed, with the old "
              "entry's comment, parent and status carried",
              len(sets) == 2 and sets[0][3] == f"path:{new_wt}"
              and sets[0][sets[0].index("--display-name") + 1] == "harbor.marina"
              and sets[0][sets[0].index("--comment") + 1] == "onboarding"
              and sets[0][sets[0].index("--parent-worktree") + 1] == "id:repo-id::/p"
              and sets[0][sets[0].index("--workspace-status") + 1] == "in-progress",
              str(calls))
        kid = [c for c in sets if c[3] == "id:repo-id::/kid"]
        check("rehome: a child of the moved worktree is pointed at the new path",
              len(kid) == 1 and kid[0][kid[0].index("--parent-worktree") + 1] == f"path:{new_wt}",
              str(calls))
        check("rehome: the move waited for Orca to see the new path before setting it",
              moved_yet["v"] and "not set" not in said, said)
        check("rehome: moves once closed", ok and new_wt.is_dir() and not old_wt.exists(), said)
        check("rehome: the uncommitted file survived the move",
              (new_wt / "Strings.swift").read_text() == "uncommitted\n")
        listed = git("worktree", "list", "--porcelain").stdout
        check("rehome: git's own records follow (worktree list, branch, status)",
              str(new_wt) in listed and str(old_wt) not in listed
              and "?? Strings.swift" in git("status", "--short", cwd=new_wt).stdout
              and git("branch", "--show-current", cwd=new_wt).stdout.strip() == "work")
        for acct in ("alpha", "beta"):
            root = paths.account_claude(acct) / "projects"
            check(f"rehome: {acct}'s memory and transcript are under the new key",
                  (root / new_key / "memory" / "note.md").read_text() == f"remember {acct}\n"
                  and (root / new_key / "sess-1.jsonl").is_file() and not (root / old_key).exists())
        row = db.get_agent_state(con, "harbor.marina")
        check("rehome: the recorded worktree keeps its selector form and names the new folder",
              row["worktree"] == f"repo-id::{new_wt}" and row["terminal"] is None,
              str(dict(row)))
        ok, said = rehome.move(con, "harbor.marina", "marina")
        check("rehome: run again it is a no-op that says so", not ok and "already" in said, said)

        # A blocked read of Orca is a refusal to move, not a licence to.
        for reply, want in ((lambda a, timeout=180: (False, "timeout"), True),
                            (lambda a, timeout=180: (False, json.dumps(
                                {"ok": False, "error": {"code": "selector_not_found"}})), False)):
            orcamod.available, orcamod._run = (lambda: True), reply
            try:
                blocked = rehome._orca_blockers(new_wt)
            finally:
                orcamod.available, orcamod._run = real_avail, real_run
            check(f"rehome: Orca {'unreadable' if want else 'not knowing the path'} "
                  f"{'refuses' if want else 'does not block'}", bool(blocked) == want, blocked)
        # A terminal list that fails after a good show is no licence to move.
        def show_only(args, timeout=180):
            if args[:2] == ["worktree", "show"]:
                return True, json.dumps({"ok": True, "result": {"worktree": {"id": "i"}}})
            return False, "timeout"
        orcamod.available, orcamod._run = (lambda: True), show_only
        try:
            blocked = rehome._orca_blockers(new_wt)
        finally:
            orcamod.available, orcamod._run = real_avail, real_run
        check("rehome: a failed terminal list refuses the move", "unknown" in blocked, blocked)
        # The move happened but Orca never shows the new path: the children's
        # commands are printed, not dropped.
        old_entry = {"id": "i", "childWorktreeIds": ["repo-id::/kid"]}
        orcamod.available = lambda: True
        orcamod._run = lambda a, timeout=180: (False, json.dumps(
            {"ok": False, "error": {"code": "selector_not_found"}}))
        time.sleep = lambda s_: None
        try:
            said = rehome._relabel(old_entry, Path("/new/place"), "harbor.marina")
        finally:
            orcamod.available, orcamod._run, time.sleep = real_avail, real_run, real_sleep
        check("rehome: when Orca never sees the new path, each child's re-parent command is printed",
              "orca worktree set --worktree id:repo-id::/kid --parent-worktree path:/new/place"
              in said and "--display-name harbor.marina" in said, said)
        # Orca seeing it but refusing a set: its reason is kept.
        def refuses(args, timeout=180):
            if args[:2] == ["worktree", "show"]:
                return True, json.dumps({"ok": True, "result": {"worktree": {"id": "n"}}})
            return False, json.dumps({"ok": False, "error": {"code": "busy_now"}})
        orcamod.available, orcamod._run = (lambda: True), refuses
        try:
            said = rehome._relabel(old_entry, Path("/new/place"), "harbor.marina")
        finally:
            orcamod.available, orcamod._run = real_avail, real_run
        check("rehome: a refused label and a refused re-parent both say why",
              said.count("busy_now") == 2 and "would not set the label" in said
              and "would not re-parent repo-id::/kid" in said, said)
    finally:
        obsmod.live_sessions = real_live
        con.close()


def test_hr_rehome_cli_moves_the_folder(home: Path) -> None:
    """The `ocp hr rehome` leaf itself, not just rehome.move()."""
    _daemon_home(home, 20)
    repo = home / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit",
                    "-q", "--allow-empty", "-m", "x"], cwd=repo, check=True)
    old_wt = home / "wts" / "old"
    subprocess.run(["git", "worktree", "add", "-q", "-b", "w", str(old_wt)],
                   cwd=repo, check=True)
    new_wt = home / "wts" / "newname"
    agents.register("newname", "alpha")
    con = db.connect()
    try:
        db.set_terminal(con, "newname", "term_x", str(old_wt))
    finally:
        con.close()
    real_live = obsmod.live_sessions
    obsmod.live_sessions = lambda: {}
    try:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = climod.main(["hr", "rehome", "newname", "--from", "old"])
        check("hr rehome: the CLI leaf moves the folder and reports it",
              code == 0 and new_wt.is_dir() and not old_wt.exists()
              and "worktree moved" in out.getvalue(), out.getvalue() + err.getvalue())
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = climod.main(["hr", "rehome", "newname", "--from", "old"])
        check("hr rehome: run again on an already-moved agent refuses, exit 1",
              code == 1 and "already" in out.getvalue(), out.getvalue())
    finally:
        obsmod.live_sessions = real_live


def test_agent_yaml_is_written_by_dump_so_what_was_typed_reads_back(home: Path) -> None:
    """`ocp hr add --model 4.5` wrote `model: 4.5`, which reads back as a float,
    and `--model 2026-09-20` wrote a file that did not load. The file is built as
    a map and written by dump, which quotes what a reader would take for
    something else, so each value reads back as the text that was typed."""
    restore_world()
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    agents.register("plain", "alpha")
    agents.register("full", "alpha", model="opus", effort="high", weight="heavy",
                    repo="/Users/ron/some repo")
    check("agent.yaml: the ordinary file is byte for byte what it was",
          agents.agent_file("plain").read_text()
          == f"name: plain\naccount: alpha\nmodel: {agents.DEFAULT_MODEL}\n"
             f"effort: {agents.DEFAULT_EFFORT}\nweight: light\nenabled: true\n"
          and agents.agent_file("full").read_text()
          == "name: full\naccount: alpha\nmodel: opus\neffort: high\nweight: heavy\n"
             "enabled: true\nrepo: /Users/ron/some repo\n")
    typed = [("model", "2026-09-20"), ("model", "no"), ("model", "4.5"), ("weight", "on"),
             ("repo", "1_000")]
    for i, (field, value) in enumerate(typed):
        agents.register(f"agent{i}", "alpha", **{field: value})
        cfg = agents.read(f"agent{i}")
        check(f"agent.yaml: --{field} {value} reads back as that text",
              cfg[field] == value and cfg["enabled"] is True and cfg["name"] == f"agent{i}",
              repr(cfg))
        if yamlcompat.HAVE_PYYAML:
            import yaml as pyyaml
            check(f"agent.yaml: and PyYAML reads --{field} {value} the same",
                  pyyaml.safe_load(agents.agent_file(f"agent{i}").read_text()) == cfg)
    # What the replay does: apply builds the agent from the saved proposal's fields.
    paths.team_file().write_text(team.STARTER)
    ok, said = hr._add({"agent": "replayed", "account": "alpha", "model": "4.5",
                        "effort": "high", "repo": "1_000", "why": "w",
                        "requested_by": "human", "created": int(time.time())}, None)
    cfg = agents.read("replayed")
    check("agent.yaml: an applied proposal carrying such values leaves a file that loads",
          ok and cfg["model"] == "4.5" and cfg["repo"] == "1_000" and cfg["effort"] == "high",
          f"{said} {cfg}")
    try:
        agents.register("ctrl", "alpha", model="a\nb")
        check("agent.yaml: a control character is refused", False)
    except yamlcompat.ConfigError:
        check("agent.yaml: a control character is refused, and leaves no folder behind",
              not paths.agent_dir("ctrl").exists())
    # A refusal leaves nothing behind: the effort is checked before the folder is made.
    try:
        agents.register("badeffort", "alpha", effort="bogus")
        check("agent.yaml: an effort that is not one of ours is refused", False)
    except ValueError:
        check("agent.yaml: an effort that is not one of ours is refused, and leaves no folder",
              not paths.agent_dir("badeffort").exists())
    # The account needs no guard of its own: dump quotes it, so an account called
    # no or 1 is the text it was typed as.
    agents.register("acctno", "no")
    agents.register("acct1", "1")
    check("agent.yaml: an account called no or 1 reads back as that text",
          agents.read("acctno")["account"] == "no" and agents.read("acct1")["account"] == "1",
          str((agents.read("acctno")["account"], agents.read("acct1")["account"])))
    agents.register("plain", "beta", model="4.5")
    check("agent.yaml: registering again updates through the same writer",
          agents.read("plain")["account"] == "beta" and agents.read("plain")["model"] == "4.5")


def test_yaml_booleans() -> None:
    got = yamlcompat.parse("a: yes\nb: no\nc: on\nd: off\ne: true\nf: false\n")
    check("yaml: YAML 1.1 booleans are booleans, not strings",
          got == {"a": True, "b": False, "c": True, "d": False, "e": True, "f": False},
          str(got))
    check("yaml: a quoted 'no' stays a string",
          yamlcompat.parse('a: "no"\n')["a"] == "no")


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


def test_a_vendor_with_no_driver_refuses_the_launch(home: Path) -> None:
    """auditor's #1691: an agent.yaml naming a vendor ocp has no driver for
    refuses the launch as a decision, a written line and no traceback or
    class name. It does so before anything is chosen or consumed, so nothing
    is exec'd and the handoff waiting for the agent is still there."""
    os.environ["OCP_HOME"] = str(home)
    bindir = home / "stub"
    bindir.mkdir(parents=True, exist_ok=True)
    stub = bindir / "claude"
    stub.write_text('#!/bin/sh\necho EXECED\n')
    stub.chmod(0o755)
    agents.register("zaitest", "gamma")
    with agents.agent_file("zaitest").open("a") as f:
        f.write("vendor: nosuch\n")
    cfg = home / "accounts" / "gamma" / ".claude"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / ".credentials.json").write_text("{}")
    keep_handoff("zaitest", "# handoff · zaitest\n- carry on\n")
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", OCP_HOME=str(home))
    env.pop("OCP_AGENT", None)
    r = subprocess.run([str(ROOT / "bin" / "ocp"), "launch", "zaitest"],
                       capture_output=True, text=True, env=env)
    out = r.stdout + r.stderr
    check("unknown vendor: the launch is refused", r.returncode != 0, out)
    check("unknown vendor: in a written line that names the agent and the vendor",
          "zaitest" in out and "no driver for vendor 'nosuch'" in out, out)
    check("unknown vendor: which reads as a decision, not a crash",
          not re.search(r"error: [A-Za-z]+: ", out) and "Traceback" not in out, out)
    check("unknown vendor: nothing is exec'd", "EXECED" not in out, out)
    con = db.connect()
    try:
        check("unknown vendor: the handoff is not consumed", db.get_handoff(con, "zaitest"))
    finally:
        con.close()


def test_doctor_looks_for_the_vendors_executable_not_its_name(home: Path) -> None:
    """auditor's #1691: the tool check asks the driver for its executable. A
    vendor is not named after its binary (Z.ai runs through opencode), so a
    lookup by name reports it missing wherever it is installed."""
    os.environ["OCP_HOME"] = str(home)
    with second_vendor():
        accounts.register("gamma", vendor="zai")
        r = doctor.Report()
        doctor._check_tools(r)
        row = [x for x in r.rows if x[1].startswith("zai")]
        check("doctor: a vendor whose binary is installed is found, under the vendor's name",
              row and row[0][0] == doctor.OK and row[0][2] == "/opt/zai/bin/glmcode", str(r.rows))


def test_a_claude_agent_is_never_placed_on_another_vendors_account(home: Path) -> None:
    """#1776: a second vendor's account that reads as logged in and has room
    is still never a place for a claude agent - it would run claude against
    that vendor's credentials. Until accounts carried a vendor the only thing
    keeping one out was `logged_in` asking claude's Keychain and getting a
    no, an accident. Here the account is logged in by its own driver and
    reads better than anything else, so each place that picks an account is
    asked, and only the vendor keeps it out."""
    _daemon_home(home, 99)
    now = int(time.time())
    # Far enough from its reset that a move beats waiting for it.
    write_status(home, "alpha", pct_5h=99, pct_7d=10, ctx_pct=5, reset_5h=now + 4 * 3600)
    real = (accounts.logged_in, orcamod.available)
    with second_vendor(metered=True):
        try:
            accounts.register("alpha")
            (paths.account_dir("alpha") / ".claude" / ".credentials.json").write_text("{}")
            accounts.register("zeta", vendor="zai")
            write_status(home, "zeta", pct_5h=5, pct_7d=5, reset_5h=now + 1800,
                         reset_7d=now + 90000, rl_at=now)
            check("vendor pick: the premise - zeta is logged in by its own driver",
                  accounts.logged_in("zeta"))
            check("vendor pick: and has room, so nothing but its vendor can keep it out",
                  status.read("zeta").takes_work()[0], str(status.read("zeta").takes_work()))

            check("vendor pick: find_room does not send the walled claude agent there",
                  launch.find_room("backend") != "zeta", str(launch.find_room("backend")))
            picked, ranked = status.pick_account(bootstrap=True, agent="backend")
            check("vendor pick: pick_account for the agent does not take it",
                  picked != "zeta" and "a zai account, not claude" in dict(ranked)["zeta"],
                  str((picked, ranked)))
            check("vendor pick: nor pick_account for no agent in particular",
                  status.pick_account()[0] != "zeta", str(status.pick_account()))
            con = db.connect()
            try:
                db.add_request(con, "orcapipe", "backend", "work", None, "supervisor",
                               "async", None, 0, None)
                plan = dmn._move_plan(con, "backend", "usage_5h")
                check("vendor pick: the daemon's move off the wall does not go there",
                      plan[0] is None and "zeta: a zai account, not claude" in plan[1], str(plan))
                check("vendor pick: nor does a person's forced move",
                      dmn._forced_plan(con, "backend", "zeta")[0] is None,
                      str(dmn._forced_plan(con, "backend", "zeta")))
                # The page and the panel carry no agent, so their verdict is
                # the account's own: judged for the vendor whose sessions are
                # the only ones that can ever run here (#1815). Drawn as a
                # refusal it said "assign no - a zai account, not claude" of an
                # account that is a perfectly good place for zai agents.
                page = dict(actions.account_detail(con, "zeta"))
                head = next(iter(page.values()))
                assign = next(ln for ln in head if ln.startswith("assign"))
                check("vendor pick: its page does not refuse it outright - the "
                      "account is judged for its own vendor",
                      assign.startswith("assign   yes")
                      and "not claude" not in assign, assign)
                check("vendor pick: and the page still names the vendor above it",
                      any(ln.startswith("config") and ln.endswith("(zai)")
                          for ln in head), str(head))
            finally:
                con.close()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                climod.cmd_status(argparse.Namespace(json=True))
            check("vendor pick: ocp status does not name it next",
                  json.loads(out.getvalue())["picked"] != "zeta", out.getvalue()[:200])
            accounts.logged_in = lambda a: True
            rows = [tui._ANSI.sub("", ln) for ln in _snap(120, 40).split(chr(10))]
            zrow = [ln for ln in rows if ln.lstrip("│ ").startswith("zeta ")]
            check("vendor pick: the accounts panel does not mark it next",
                  zrow and "next" not in zrow[0], str(zrow))
            check("vendor pick: its tick is its own vendor's answer, not a ✘ "
                  "that reads as a broken account",
                  zrow and "✔" in zrow[0], str(zrow))
            check("vendor pick: a fold is not sent there",
                  foldmod._cheapest_account() != "zeta", str(foldmod._cheapest_account()))

            # The control: a zai agent is placed there, so the filter is on
            # the vendor, not on the account.
            agents.register("glm", "zeta")
            _with_vendor("glm", "zai")
            check("vendor pick: a zai agent is placed on the zai account",
                  launch.find_room("glm") == "zeta"
                  and status.pick_account(agent="glm")[0] == "zeta",
                  str((launch.find_room("glm"), status.pick_account(agent="glm"))))
        finally:
            accounts.logged_in, orcamod.available = real


def test_another_vendors_account_never_spends_a_live_refresh(home: Path) -> None:
    """auditor #1863: the vendor refusal runs before live_refresh. A stale zai
    account listed ahead of a stale claude one must not be asked about, or it
    spends the call's only refresh on an account the agent can never use and
    the claude one is refused on its stale reading."""
    _daemon_home(home, None)
    now = int(time.time())
    real_read, real_login = usage_api.read, accounts.logged_in
    asked: list[str] = []
    with second_vendor(metered=True):
        try:
            accounts.register("zeta", vendor="zai")
            for n in ("zeta", "alpha"):
                write_status(home, n, pct_5h=90, reset_5h=now - 60, rl_carried=False,
                             rl_at=now - status.CARRY_MAX_AGE_S - 300, collected_at=now)
                check(f"vendor refresh: the premise - {n} is carried_old, so a refresh "
                      "would ask about it", status.read(n).pct_5h_source == "carried_old",
                      status.read(n).pct_5h_source)

            def fake_read(acct, refresh=True):
                asked.append(acct)
                return usage_api.Reading(acct, 10, now + 9000, 20, now + 90000, now)
            usage_api.read = fake_read
            accounts.logged_in = lambda acct: True

            picked, ranked = status.pick_account(["zeta", "alpha"], agent="backend",
                                                 live_refresh=True)
            check("vendor refresh: the zai account is never asked about",
                  "zeta" not in asked, asked)
            check("vendor refresh: it is refused as another vendor's",
                  dict(ranked).get("zeta") == "a zai account, not claude", ranked)
            check("vendor refresh: the claude account gets the refresh and the agent",
                  asked == ["alpha"] and picked == "alpha", (asked, picked, ranked))
        finally:
            usage_api.read, accounts.logged_in = real_read, real_login


def test_a_second_vendors_live_session_is_seen_by_the_sweep(home: Path) -> None:
    """#1815: `is_session` decided from claude's own literals, so a session of
    any other vendor matched nothing at all. `live_sessions` then left that
    agent's pid None, and `reconcile` reads "terminal alive, no process" as the
    session having died - so the daemon recorded a live, correctly placed agent
    closed, on every tick. Which binary a session runs, and how the vendor
    spells a headless run, are both the driver's answers now.

    Driven through real `ps` command lines rather than by patching
    `live_sessions`: every other test in this file replaces that function
    wholesale, so none of them reaches `is_session` at all."""
    _daemon_home(home, 20)
    with second_vendor(metered=True):
        check("is_session: a second vendor's own binary is a session",
              obsmod.is_session("/opt/zai/bin/glmcode --model glm-4.6"))
        check("is_session: claude's still is",
              obsmod.is_session("/usr/local/bin/claude --resume abc123"))
        check("is_session: the second vendor's own spelling of headless is not",
              not obsmod.is_session("/opt/zai/bin/glmcode run 'fold it'"))
        check("is_session: nor is claude's",
              not obsmod.is_session("/usr/local/bin/claude -p 'fold it'"))
        check("is_session: and nothing of ours is not a session either",
              not obsmod.is_session("/usr/bin/ssh somewhere"))
    check("is_session: with that driver gone its command line is not a session",
          not obsmod.is_session("/opt/zai/bin/glmcode --model glm-4.6"))

    # And the invariant the miss actually broke: the daemon's own record of a
    # running agent. Real `live_sessions`, over a process table we supply.
    real = (obsmod.live_sessions, obsmod.process_table, obsmod.process_env,
            orcamod.available, orcamod.live_terminals)
    obsmod.live_sessions = REAL_OBSERVE["live_sessions"]
    orcamod.available = lambda: True
    orcamod.live_terminals = lambda: {"term_x", "term_z"}
    with second_vendor(metered=True):
        try:
            accounts.register("zeta", vendor="zai")
            agents.register("glm", "zeta")
            _with_vendor("glm", "zai")
            con = db.connect()
            try:
                db.set_terminal(con, "glm", "term_z", "wt")
                db.set_agent_state(con, "glm", dmn.RUNNING, detail="working")
                table = [(4301, "/opt/zai/bin/glmcode --model glm-4.6")]
                obsmod.process_table = lambda: list(table)
                obsmod.process_env = lambda pid: f"OCP_AGENT={'glm' if pid == 4301 else 'x'}"
                check("vendor sweep: the premise - its pid is found",
                      obsmod.live_sessions() == {"glm": 4301},
                      str(obsmod.live_sessions()))
                said: list[str] = []
                dmn.sweep(con, lambda _m: None, said)
                row = db.get_agent_state(con, "glm")
                check("vendor sweep: a live second-vendor session is not recorded closed",
                      not row["closed_at"]
                      and not any("glm: its session is gone" in a for a in said),
                      str((dict(row), said)))

                # The control, on the same fixture: when its process really has
                # gone, the same sweep does record it closed - so the check
                # above is about the vendor and not about a sweep that never
                # reached this agent.
                table.clear()
                said = []
                dmn.sweep(con, lambda _m: None, said)
                row = db.get_agent_state(con, "glm")
                check("vendor sweep: and when it really is gone, it is recorded closed",
                      row["closed_at"]
                      and any("glm: its session is gone" in a for a in said),
                      str((dict(row), said)))
            finally:
                con.close()
        finally:
            (obsmod.live_sessions, obsmod.process_table, obsmod.process_env,
             orcamod.available, orcamod.live_terminals) = real


def test_an_accounts_verdict_is_asked_for_its_own_vendor(home: Path) -> None:
    """#1815: the panel's tick and the page's sentence arrive with no agent in
    hand, so a claude-only verdict said "no - a zai account, not claude" about
    an account that is a perfectly good place for the agents it is for, and a
    human reads that as an account gone wrong. It is judged for its own vendor
    now. What is genuinely absolute - unmetered, no driver, not logged in -
    still refuses, and no placement path is touched: those all hold an agent,
    and the test above is what pins them."""
    _daemon_home(home, 20)
    now = int(time.time())
    with second_vendor(metered=True):
        accounts.register("zeta", vendor="zai")
        write_status(home, "zeta", pct_5h=5, pct_7d=5, reset_5h=now + 1800,
                     reset_7d=now + 90000, rl_at=now)
        ok, why = status.account_verdict("zeta", status.read("zeta"), None, [], True)
        check("verdict: a healthy second-vendor account is not refused outright",
              ok and "not claude" not in why, str((ok, why)))

        _, ranked = status.pick_account(["alpha", "zeta"], vendor="zai")
        check("verdict: pick_account ranks for the vendor it is given",
              "a claude account, not zai" in dict(ranked)["alpha"]
              and "not zai" not in dict(ranked)["zeta"], str(ranked))
        ok, why = status.account_verdict("zeta", status.read("zeta"), None, ranked, True)
        check("verdict: and that ranking's words are the ones drawn",
              ok and why == dict(ranked)["zeta"], str((ok, why)))

        agents.register("glm", "zeta")
        _with_vendor("glm", "zai")
        _, ranked2 = status.pick_account(["alpha", "zeta"], agent="glm", vendor="claude")
        check("verdict: an agent's own vendor wins over a vendor named beside it",
              "a claude account, not zai" in dict(ranked2)["alpha"], str(ranked2))

        check("verdict: a claude account's own verdict is unchanged",
              status.account_verdict("alpha", status.read("alpha"), None, [], True)[0])
        check("verdict: and not logged in is still a no, vendor or no vendor",
              status.account_verdict("zeta", status.read("zeta"), None, [], False)
              == (False, "not logged in"))

    # The refusals that are true of every agent there is, its own included.
    with second_vendor(metered=False):
        ok, why = status.account_verdict("zeta", status.read("zeta"), None, [], True)
        check("verdict: an unmetered account is still refused, in its own words",
              not ok and why == status.UNMETERED_WHY, str((ok, why)))
    # No driver at all: `not_for` caught this only because the vendor asked
    # was always claude, so it needs its own refusal now.
    (paths.account_dir("zeta") / "vendor").write_text("nosuch\n")
    ok, why = status.account_verdict("zeta", status.read("zeta"), None, [], True)
    check("verdict: an account whose vendor has no driver is still refused",
          not ok and "no driver for vendor 'nosuch'" in why, str((ok, why)))


def test_an_unmetered_account_is_pinned_never_picked_or_polled(home: Path) -> None:
    """#1776: an account whose vendor reports no usage is its own state, not
    "no reading yet" - that one is a bootstrap candidate, and nothing will
    ever give this one a reading. Its own agents start there; nothing is
    moved onto it, it is never asked for usage, and it is drawn unmetered."""
    _daemon_home(home, 20)
    real = (usage_api.read, usage_api.refresh_credential, accounts.logged_in)
    with second_vendor(metered=False):
        try:
            accounts.register("alpha")
            (paths.account_dir("alpha") / ".claude" / ".credentials.json").write_text("{}")
            accounts.register("zeta", vendor="zai")
            accounts.register("zeta2", vendor="zai")
            agents.register("glm", "zeta")
            _with_vendor("glm", "zai")
            st = status.read("zeta")
            check("unmetered: read as its own state, not as no reading yet",
                  st.unmetered and st.assignable() == (False, status.UNMETERED_WHY),
                  str((st.unmetered, st.assignable())))
            picked, ranked = status.pick_account(["zeta2"], bootstrap=True, agent="glm")
            check("unmetered: never picked, not even as a bootstrap last resort",
                  picked is None and dict(ranked)["zeta2"] == status.UNMETERED_WHY,
                  str((picked, ranked)))
            check("unmetered: the agent that names it still starts there",
                  launch.find_room("glm") == "zeta", str(launch.find_room("glm")))
            argv, env = launch.build("glm")
            check("unmetered: and launches as its own vendor, into its own config dir",
                  argv[0] == "/opt/zai/bin/glmcode"
                  and env.get("ZAI_HOME") == str(vendors.config_dir("zeta")),
                  str((argv[:1], env.get("ZAI_HOME"))))

            asked: list[str] = []
            usage_api.read = lambda acct, refresh=True: asked.append(acct)
            real_recover = recovermod.recover
            recovermod.recover = lambda acct: (False, "stub: not under test here")
            con = db.connect()
            try:
                # One account per tick, each put on its cooldown once asked,
                # so four ticks walk past every account there is.
                for _ in range(4):
                    dmn.poll_usage(con, lambda _m: None)
            finally:
                con.close()
                recovermod.recover = real_recover
            check("unmetered: the usage poll never asks about it",
                  "alpha" in asked and not {"zeta", "zeta2"} & set(asked), str(asked))
            usage_api.read = real[0]
            renewed: list[str] = []
            usage_api.refresh_credential = lambda acct, **k: renewed.append(acct) or True
            check("unmetered: nor does the endpoint reader, and it renews nothing",
                  usage_api.read("zeta") is None and not renewed, str(renewed))

            accounts.logged_in = lambda a: True
            rows = [tui._ANSI.sub("", ln) for ln in _snap(120, 40).split(chr(10))]
            zrow = [ln for ln in rows if ln.lstrip("│ ").startswith("zeta ")]
            check("unmetered: the accounts panel says so",
                  zrow and "unmetered" in zrow[0], str(zrow))
            accounts.logged_in = real[2]
            r = doctor.Report()
            doctor._check_accounts(r)
            check("unmetered: doctor says so rather than warning of no status.json",
                  any(n == "account zeta: unmetered (zai)" for _lv, n, _d in r.rows)
                  and not any(n.startswith("account zeta: no status") for _lv, n, _d in r.rows),
                  str([n for _lv, n, _d in r.rows if "zeta" in n]))
        finally:
            usage_api.read, usage_api.refresh_credential, accounts.logged_in = real


def test_an_agent_is_never_launched_on_another_vendors_account(home: Path) -> None:
    """The last line before the exec: whatever chose the account - agent.yaml,
    a resume, a stale record - a session is the agent's vendor's binary
    against that account's credentials, so a mismatch refuses, in words."""
    os.environ["OCP_HOME"] = str(home)
    with second_vendor():
        accounts.register("alpha")
        (paths.account_dir("alpha") / ".claude" / ".credentials.json").write_text("{}")
        accounts.register("zeta", vendor="zai")
        agents.register("backend", "zeta")
        agents.register("glm", "alpha")
        _with_vendor("glm", "zai")
        for agent, want in (("backend", "agent 'backend' is a claude agent and account "
                                        "'zeta' is a zai account"),
                            ("glm", "agent 'glm' is a zai agent and account "
                                    "'alpha' is a claude account")):
            try:
                launch.build(agent)
                check(f"launch: {agent} on the other vendor's account is refused", False, "built")
            except SystemExit as e:
                check(f"launch: {agent} on the other vendor's account is refused",
                      want in str(e), str(e))
        r = doctor.Report()
        doctor._check_agents(r)
        names = [n for _lv, n, _d in r.rows]
        check("doctor: names the agent on the other vendor's account",
              "agent backend: a claude agent on zai account 'zeta'" in names, str(names))


def test_an_account_records_its_vendor(home: Path) -> None:
    """The vendor is written where the account is, once, and every account
    answer comes from it; one that predates vendors is claude's."""
    os.environ["OCP_HOME"] = str(home)
    accounts.register("alpha")
    check("vendor record: an account that records none is the default's",
          vendors.account_vendor("alpha") == "claude"
          and not (paths.account_dir("alpha") / "vendor").exists())
    try:
        accounts.register("nope", vendor="nosuch")
        check("vendor record: a vendor with no driver is refused", False, "registered")
    except ValueError as e:
        check("vendor record: a vendor with no driver is refused, making nothing",
              "no driver for vendor 'nosuch'" in str(e) and "nope" not in paths.list_accounts(),
              str(e))
    agents.register("broken", "alpha")
    agents.agent_file("broken").write_text("- not a map\n")
    picked, ranked = status.pick_account(["alpha"], agent="broken")
    check("vendor record: an agent whose vendor cannot be read is placed nowhere",
          picked is None and dict(ranked)["alpha"].startswith("agent.yaml unreadable"),
          str((picked, ranked)))
    with second_vendor():
        accounts.register("zeta", vendor="zai")
        check("vendor record: a second vendor is written down",
              vendors.account_vendor("zeta") == "zai")
        accounts.register("zeta")
        check("vendor record: re-registering without one keeps it",
              vendors.account_vendor("zeta") == "zai")
        try:
            accounts.register("zeta", vendor="claude")
            check("vendor record: an account's vendor is not changed under it", False, "changed")
        except ValueError as e:
            check("vendor record: an account's vendor is not changed under it",
                  "already a zai account" in str(e) and vendors.account_vendor("zeta") == "zai",
                  str(e))
        mem = paths.account_dir("alpha") / ".claude" / "projects" / "-w" / "memory"
        mem.mkdir(parents=True)
        (mem / "MEMORY.md").write_text("- a fact\n")
        check("vendor record: memory is not carried to another vendor's account",
              accounts.migrate_memory("alpha", "zeta") == []
              and not (vendors.config_dir("zeta") / "sessions").exists())
        (paths.account_dir("zeta") / "vendor").write_text("")
        check("vendor record: an empty record names no vendor, so the account is not usable",
              not accounts.logged_in("zeta") and status.pick_account(["zeta"], bootstrap=True)[0] is None)


def test_the_opencode_driver(home: Path) -> None:
    """The opencode driver's answers, each from what 1.18.32 was measured to
    do (design/03 §4.3). Nothing here runs opencode: `auth list` is replaced
    by its measured output and the binary by a path."""
    os.environ["OCP_HOME"] = str(home)
    drv = vendors.get("opencode")
    drv.binary = lambda: "/x/bin/opencode"
    runs: list[tuple[list, dict]] = []
    listed = {"out": _auth_list_out(1)}

    def fake_run(argv, **kw):
        runs.append((argv, kw.get("env") or {}))
        if isinstance(listed["out"], Exception):
            raise listed["out"]
        return subprocess.CompletedProcess(argv, 0, listed["out"], "")

    real_run = subprocess.run
    try:
        accounts.register("alpha")
        accounts.register("zai", vendor="opencode")
        cdir = vendors.config_dir("zai")
        check("opencode: its account's directory is XDG_DATA_HOME's, made at register",
              cdir == paths.account_dir("zai") / "xdg-data" and cdir.is_dir(), str(cdir))
        # Launched from inside a claude agent, whose own variable names its
        # account. auditor's #1906: XDG_DATA_HOME is also the user's own, and
        # claude reads it, so only a value pointing into one of our accounts
        # is dropped.
        saved = {k: os.environ.get(k) for k in ("XDG_DATA_HOME", "CLAUDE_CONFIG_DIR")}
        try:
            os.environ["CLAUDE_CONFIG_DIR"] = str(vendors.config_dir("alpha"))
            env = accounts.env_for("zai")
            check("opencode: a session's environment names it in XDG_DATA_HOME",
                  env.get("XDG_DATA_HOME") == str(cdir) and "CLAUDE_CONFIG_DIR" not in env,
                  str((env.get("XDG_DATA_HOME"), env.get("CLAUDE_CONFIG_DIR"))))
            os.environ["XDG_DATA_HOME"] = str(home / "user-share")
            check("opencode: a claude launch keeps the XDG_DATA_HOME it inherits",
                  accounts.env_for("alpha").get("XDG_DATA_HOME") == str(home / "user-share"),
                  str(accounts.env_for("alpha").get("XDG_DATA_HOME")))
            os.environ["XDG_DATA_HOME"] = str(cdir)
            check("opencode: but not one naming an opencode account",
                  "XDG_DATA_HOME" not in accounts.env_for("alpha"),
                  str(accounts.env_for("alpha").get("XDG_DATA_HOME")))
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        check("opencode: the login a human is handed is opencode's, under that directory",
              accounts.login_command("zai") == f'XDG_DATA_HOME="{cdir}" opencode auth login',
              accounts.login_command("zai"))
        check("opencode: the account is metered - the coding plan answers a usage endpoint",
              not status.read("zai").unmetered)

        # Logged in: the file first, then opencode's own count.
        subprocess.run = fake_run
        check("opencode: no auth.json is not logged in, and opencode is not asked",
              not accounts.logged_in("zai") and runs == [], str(runs))
        (cdir / "opencode").mkdir()
        (cdir / "opencode" / "auth.json").write_text("{}")
        check("opencode: one credential counted is logged in",
              accounts.logged_in("zai"), str(runs))
        check("opencode: and the count was asked of this account's directory",
              runs and runs[-1][0] == ["/x/bin/opencode", "auth", "list"]
              and runs[-1][1].get("XDG_DATA_HOME") == str(cdir), str(runs[-1:]))
        listed["out"] = _auth_list_out(0)
        check("opencode: the auth.json a logout leaves behind is not logged in",
              not accounts.logged_in("zai"))
        listed["out"] = subprocess.TimeoutExpired(["opencode"], 20)
        check("opencode: nor is one opencode cannot answer for",
              not accounts.logged_in("zai"))
        listed["out"] = _auth_list_out(1)

        # A session: the TUI's own command line, as `ps` shows it.
        tui = "/Users/ron/.opencode/bin/opencode -m zai-coding-plan/glm-5.2"
        for line, want in ((tui, True),
                           ("opencode", True),
                           ("/Users/ron/.opencode/bin/opencode -m m -s ses_1", True),
                           ("/Users/ron/.opencode/bin/opencode run fix it", False),
                           ("/Users/ron/.opencode/bin/opencode auth list", False),
                           ("/Users/ron/.opencode/bin/opencode serve", False),
                           (f"script -q /dev/null {tui}", False),
                           ("/usr/local/bin/claude --append-system-prompt see "
                            f"{tui}", False),
                           ("/opt/x/myopencode -m m", False)):
            check(f"opencode: is_session({line[:48]!r}) is {want}",
                  drv.is_session(line) is want)
        check("opencode: claude does not claim the TUI's line either",
              not vendors.get("claude").is_session(tui))

        for extra, want in ((["-s", "ses_1"], True), (["--session", "ses_1"], True),
                            (["--session=ses_1"], True), (["-c"], True),
                            (["--continue"], True), (["--fork"], False),
                            (["-m", "x"], False), (None, False)):
            check(f"opencode: is_resume({extra}) is {want}", drv.is_resume(extra) is want)

        agents.register("glm", "zai", model="zai-coding-plan/glm-5.2")
        _with_vendor("glm", "opencode")
        argv, env = launch.build("glm")
        check("opencode: a launch is the TUI with its model and nothing ocp cannot "
              "express in its argv",
              argv == ["/x/bin/opencode", "-m", "zai-coding-plan/glm-5.2"], str(argv))
        check("opencode: into the account's own data directory",
              env.get("XDG_DATA_HOME") == str(cdir) and env.get("OCP_AGENT") == "glm",
              str(env.get("XDG_DATA_HOME")))
        check("opencode: no model configured leaves the choice to opencode",
              drv.launch_argv("/x/bin/opencode", None, "high", "Agent", ["--mcp-config", "m"],
                              "a preamble") == ["/x/bin/opencode"])

        # No memory root: everything that walks one finds nothing on this
        # account, and still finds claude's.
        accounts.register("zai2", vendor="opencode")
        mem = paths.account_dir("alpha") / ".claude" / "projects" / "-tmp-a-b"
        (mem / "memory").mkdir(parents=True)
        (mem / "sess1.jsonl").write_text(_call_line("req_alpha", 10))
        check("opencode: memory moves between two of its accounts carry nothing",
              accounts.migrate_memory("zai", "zai2") == [])
        check("opencode: a transcript is still found on claude's account past it",
              handoffmod.find_transcript("sess1", "zai") == mem / "sess1.jsonl",
              str(handoffmod.find_transcript("sess1", "zai")))
        found = collect.locate("/tmp/a/b")
        check("opencode: and the collector's search passes over it to claude's",
              found is not None and found.account == "alpha", str(found))
        check("opencode: a worktree move renames only claude's directory",
              rehome._project_dirs(Path("/tmp/a/b"), Path("/tmp/c"))
              == [(mem, mem.parent / "-tmp-c")],
              str(rehome._project_dirs(Path("/tmp/a/b"), Path("/tmp/c"))))
        from ocp import tokenlog
        con = db.connect()
        try:
            check("opencode: the token log reads nothing for it",
                  tokenlog.ingest(con, "zai") == 0)
        finally:
            con.close()
    finally:
        subprocess.run = real_run
        del drv.binary


def test_an_opencode_session_under_ocp(home: Path) -> None:
    """#1926, each part measured on opencode 1.18.32 in an Orca terminal: the
    preamble reaches the session as an instructions file, the start prompt
    as --prompt, Agent's denial as opencode's task permission; /new and
    /exit are its commands; and since Orca's tui-idle reads an opencode turn
    as idle throughout, idle and what is in the way come from its screen."""
    os.environ["OCP_HOME"] = str(home)
    drv = vendors.get("opencode")
    drv.binary = lambda: "/x/bin/opencode"
    drv.credential_present = lambda config_dir: True
    real = {n: getattr(orcamod, n) for n in ("_run", "_screen", "alive", "send", "SCREEN_POLL_S")}
    real_execve, real_record = os.execve, launch._record_terminal
    try:
        accounts.register("alpha")
        accounts.register("zai", vendor="opencode")
        agents.register("glm", "zai", model="zai-coding-plan/glm-5.3-flash")
        _with_vendor("glm", "opencode")
        agents.register("backend", "alpha")
        (vendors.config_dir("alpha") / ".credentials.json").write_text("{}")
        paths.agent_dir("glm").joinpath("ROLE.md").write_text("Build the thing.\n")

        # The start prompt: the preamble in a file opencode is pointed at,
        # Agent denied as its task tool, and nothing of either in the argv.
        argv, env = launch.build("glm")
        cfg = json.loads(env.get("OPENCODE_CONFIG_CONTENT") or "{}")
        pre = paths.agent_dir("glm") / "opencode-preamble.md"
        check("opencode session: the preamble is an instructions file of its own",
              cfg.get("instructions") == [str(pre)] and pre.exists()
              and pre.read_text() == launch.context_for("glm"), str(cfg))
        check("opencode session: and carries the role",
              "Build the thing." in pre.read_text())
        check("opencode session: Agent is denied as opencode's task tool, and nothing else",
              cfg.get("permission") == {"task": "deny"}, str(cfg.get("permission")))
        check("opencode session: the argv carries neither",
              argv == ["/x/bin/opencode", "-m", "zai-coding-plan/glm-5.3-flash"], str(argv))
        check("opencode session: a claude launch gets no opencode config",
              "OPENCODE_CONFIG_CONTENT" not in launch.build("backend")[1])

        ran: list[list[str]] = []
        os.execve = lambda exe, a, e: ran.append(list(a))
        launch._record_terminal = lambda *a, **k: None
        for agent in ("glm", "backend"):
            f = home / f"prompt-{agent}"
            f.write_text("Your first task.")
            launch.exec_agent(agent, prompt_file=str(f))
        check("opencode session: the start prompt is submitted with --prompt, not as the "
              "project directory", ran[0][-2:] == ["--prompt", "Your first task."], str(ran[0]))
        check("opencode session: claude's is still its positional",
              ran[1][-1] == "Your first task." and "--prompt" not in ran[1], str(ran[1]))

        con = db.connect()
        try:
            db.set_terminal(con, "glm", "term_oc", None)
            db.set_terminal(con, "backend", "term_cl", None)
        finally:
            con.close()
        check("opencode session: a terminal is read as its agent's vendor",
              [orcamod._driver_for(h).name for h in ("term_oc", "term_cl", "term_nobody")]
              == ["opencode", "claude", "claude"])

        # What is in the way, read off opencode's own screen.
        orcamod.screen_blocker = REAL_ORCA["screen_blocker"]
        shown = {"tail": OC_IDLE}
        orcamod._screen = lambda h: _screen_of(shown["tail"])
        for name, tail, want in (("idle", OC_IDLE, None), ("start", OC_HOME, None),
                                 ("working", OC_WORKING, None),
                                 ("permission", OC_PERMISSION, orcamod.MODAL),
                                 ("no prompt box", ["loading"], orcamod.UNREADABLE)):
            shown["tail"] = tail
            b = orcamod.screen_blocker("term_oc")
            check(f"opencode session: its {name} screen has {want or 'nothing'} in the way",
                  (b.kind if b else None) == want, str(b))
        shown["tail"] = OC_PERMISSION
        check("opencode session: the permission request is quoted",
              orcamod.screen_blocker("term_oc").detail
              == "△ Permission required / ← Access external directory /etc / Patterns",
              orcamod.screen_blocker("term_oc").detail)
        shown["tail"] = OC_IDLE
        check("opencode session: claude's reading of the same screen is unchanged",
              orcamod.screen_blocker("term_cl").kind == orcamod.UNREADABLE)

        # Idle: Orca says yes throughout an opencode turn, so the screen decides.
        orcamod.SCREEN_POLL_S = 0
        orca_says = {"satisfied": True}
        waits: list[str] = []

        def fake_run(args, timeout=180):
            waits.append(args[0])
            return True, json.dumps({"result": {"wait": {"satisfied": orca_says["satisfied"]}}})
        orcamod._run = fake_run
        reads = iter([OC_WORKING, OC_WORKING, OC_IDLE])
        orcamod._screen = lambda h: _screen_of(next(reads))
        check("opencode session: idle once the screen stops offering an interrupt",
              orcamod.wait_idle("term_oc", 5000) and next(reads, "spent") == "spent")
        orcamod._screen = lambda h: _screen_of(OC_WORKING)
        check("opencode session: a turn still running is not idle, whatever Orca says",
              not orcamod.wait_idle("term_oc", 30))
        orcamod._screen = lambda h: _screen_of(OC_HOME)
        check("opencode session: its start screen is idle", orcamod.wait_idle("term_oc", 30))
        orcamod._screen = lambda h: _screen_of(OC_HOME[:1])
        check("opencode session: a screen still booting, no prompt box yet, is not",
              not orcamod.wait_idle("term_oc", 30))
        orca_says["satisfied"] = False
        orcamod._screen = lambda h: _screen_of(OC_HOME)
        check("opencode session: Orca's no stands over an idle screen",
              not orcamod.wait_idle("term_oc", 30))
        orca_says["satisfied"] = True
        orcamod._screen = lambda h: (_ for _ in ()).throw(AssertionError("claude screen read"))
        check("opencode session: a claude terminal is still Orca's word alone",
              orcamod.wait_idle("term_cl", 30))

        # /new and /exit, typed only where nothing is in the way.
        sent: list[tuple[str, str]] = []
        orcamod.alive = lambda h: True
        orcamod.send = lambda h, text, enter=True: (sent.append((h, text)), (True, ""))[1]
        orcamod._screen = lambda h: _screen_of(OC_IDLE if h == "term_oc" else COMPOSER_EMPTY)
        for h in ("term_oc", "term_cl"):
            orcamod.clear_context(h, wait=False)
            orcamod.close_session(h, wait=False)
        check("opencode session: its clear is /new and its exit /exit; claude's are unchanged",
              sent == [("term_oc", "/new"), ("term_oc", "/exit"),
                       ("term_cl", "/clear"), ("term_cl", "/exit")], str(sent))
        orcamod._screen = lambda h: _screen_of(OC_PERMISSION)
        sent.clear()
        ok, why = orcamod.clear_context("term_oc", wait=False)
        check("opencode session: nothing is typed at a permission request",
              not ok and sent == [] and "not typing /new" in why, why)
    finally:
        for n, f in real.items():
            setattr(orcamod, n, f)
        os.execve, launch._record_terminal = real_execve, real_record
        del drv.binary, drv.credential_present


def test_an_opencode_row_takes_its_state_word_from_orca(home: Path) -> None:
    """#59: claude agents report RUNNING and IDLE through hooks; opencode has
    no counterpart wired, so production writes IDLE at launch and the row
    sits there for the life of the session - an agent working right now reads
    IDLE. The state cell reads the pane's screen instead (tui-idle answers
    idle mid-turn for opencode, measured), and only on positive evidence both
    ways: the prompt box with "interrupt" in its footer is working, the box
    without it is idle, and anything else - a closed agent's shell prompt, a
    start screen - is no evidence, so the db state stands. A closed row is
    never screened at all. A claude row is never screened: its hooks already
    said."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    accounts.register("alpha")
    accounts.register("zai", vendor="opencode")
    agents.register("backend", "alpha")
    agents.register("glm", "zai")
    _with_vendor("glm", "opencode")
    con = db.connect()
    screens = {"term_oc": OC_WORKING}
    asked: list[list] = []
    real_screen = orcamod._screen
    orcamod._screen = lambda h: (asked.append([h]), {
        "source": "screen", "tail": screens[h]})[1]
    saved_seen = dict(tui._opencode_seen)
    tui._opencode_seen.clear()

    def row(agent):
        return db.get_agent_state(con, agent)

    try:
        db.set_agent_state(con, "backend", dmn.RUNNING)
        db.set_terminal(con, "backend", "term_cl", "wt")
        db.set_account(con, "backend", "alpha")
        # What production writes for an opencode agent: IDLE.
        db.set_agent_state(con, "glm", dmn.IDLE)
        db.set_terminal(con, "glm", "term_oc", "wt")
        db.set_account(con, "glm", "zai")

        word, detail = tui.screen_state(con, row("glm"))
        check("opencode row: a screen mid-turn reads RUNNING over the db's IDLE",
              (word, detail) == ("RUNNING", ""), f"{word!r} {detail!r}")
        check("opencode row: the claude row is never screened",
              asked == [["term_oc"]], str(asked))

        screens["term_oc"] = OC_IDLE
        tui._opencode_seen.clear()
        check("opencode row: an idle screen reads IDLE",
              tui.screen_state(con, row("glm"))[0] == "IDLE")

        # And the other direction: a RUNNING row over an idle screen.
        db.set_agent_state(con, "glm", dmn.RUNNING)
        check("opencode row: an idle screen keeps a RUNNING row honest",
              tui.screen_state(con, row("glm"))[0] == "IDLE")

        # A shell prompt is evidence of neither: the db word stands. This is
        # what a closed agent's pane shows (auditor #2157, measured live).
        shell = ["Resume this session with:",
                 "claude --resume 9f2289d7-3b45-4e86-9653-efb48c2d46da",
                 "ron@Ron-MacBookAir  ~/orca/workspaces/orcapipe/orcapipe.glm "]
        db.set_agent_state(con, "glm", dmn.IDLE)
        con.execute("UPDATE agent_state SET closed_at = ? WHERE agent = 'glm'",
                    (int(time.time()),))
        con.commit()
        screens["term_oc"] = shell
        tui._opencode_seen.clear()
        asked.clear()
        check("opencode row: a closed agent's shell prompt is not work in progress",
              tui.screen_state(con, row("glm"))[0] == "IDLE" and asked == [],
              str(asked))

        # A closed row is never screened at all, whatever its pane shows.
        asked.clear()
        screens["term_oc"] = OC_WORKING
        tui._opencode_seen.clear()
        tui.screen_state(con, row("glm"))
        check("opencode row: a closed row is never screened",
              asked == [], str(asked))
        con.execute("UPDATE agent_state SET closed_at = NULL WHERE agent = 'glm'")
        con.commit()

        orcamod._screen = lambda h: {"source": "stream", "tail": OC_WORKING}
        tui._opencode_seen.clear()
        check("opencode row: an unreadable screen leaves the db state standing",
              tui.screen_state(con, row("glm"))[0] == "IDLE")

        # The tri-state, at the driver: box + interrupt is working, box
        # without it is idle, no box is no evidence.
        drv = vendors.get("opencode")
        check("opencode row: the driver's screen answer is three-valued",
              drv.working_on_screen(OC_WORKING) is True
              and drv.working_on_screen(OC_IDLE) is False
              and drv.working_on_screen(shell) is None,
              str((drv.working_on_screen(OC_WORKING),
                   drv.working_on_screen(OC_IDLE),
                   drv.working_on_screen(shell))))

        orcamod._screen = real_screen
        tui._opencode_seen.clear()
        asked.clear()
        orcamod._screen = lambda h: (asked.append([h]), {
            "source": "screen", "tail": OC_WORKING})[1]
        first = tui.screen_state(con, row("glm"))[0]
        second = tui.screen_state(con, row("glm"))[0]
        check("opencode row: the cache holds within the ttl - one read per pane",
              first == second == "RUNNING" and asked == [["term_oc"]], str(asked))
    finally:
        orcamod._screen = real_screen
        tui._opencode_seen = saved_seen
        con.close()


def test_a_launch_does_not_inherit_an_opencode_sessions_config(home: Path) -> None:
    """#43: accounts.env_for starts from the launching process's environment,
    and an opencode session's own tools carry OPENCODE_CONFIG_CONTENT - another
    session's whole config, preamble and denials, which a claude launch would
    keep. env_for drops that one variable. An inherited OPENCODE_CONFIG_DIR
    stays, whatever the launch's vendor: Orca sets it in every terminal it
    opens for its opencode status plugin (busy/idle), and dropping it would
    strip that plugin from every relaunch. The opencode launch's own
    OPENCODE_CONFIG_CONTENT comes back from its driver's launch_env."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    accounts.register("alpha")
    accounts.register("zai", vendor="opencode")
    agents.register("backend", "alpha")
    agents.register("glm", "zai", model="zai-coding-plan/glm-5.3-flash")
    _with_vendor("glm", "opencode")
    (vendors.config_dir("alpha") / ".credentials.json").write_text("{}")
    cdrv, odrv = vendors.get("claude"), vendors.get("opencode")
    cdrv.binary = lambda: "/x/bin/claude"
    odrv.binary = lambda: "/x/bin/opencode"
    odrv.credential_present = lambda config_dir: True
    saved = {k: os.environ.get(k) for k in ("OPENCODE_CONFIG_CONTENT", "OPENCODE_CONFIG_DIR")}
    try:
        os.environ["OPENCODE_CONFIG_CONTENT"] = '{"instructions": ["/someone-else/preamble.md"]}'
        os.environ["OPENCODE_CONFIG_DIR"] = "/orca/opencode-hooks/shared"

        env = launch.build("backend")[1]
        check("launch: a claude launch drops the inherited config, keeps the dir",
              "OPENCODE_CONFIG_CONTENT" not in env
              and env.get("OPENCODE_CONFIG_DIR") == "/orca/opencode-hooks/shared",
              str({k: env.get(k) for k in ("OPENCODE_CONFIG_CONTENT", "OPENCODE_CONFIG_DIR")}))

        env = launch.build("glm")[1]
        own = odrv.launch_env(launch.context_for("glm"), launch.DISALLOWED_TOOLS,
                              paths.agent_dir("glm"))["OPENCODE_CONFIG_CONTENT"]
        check("launch: an opencode launch keeps the dir and its driver's own config",
              env.get("OPENCODE_CONFIG_DIR") == "/orca/opencode-hooks/shared"
              and env["OPENCODE_CONFIG_CONTENT"] == own,
              str({k: env.get(k) for k in ("OPENCODE_CONFIG_CONTENT", "OPENCODE_CONFIG_DIR")}))
    finally:
        del cdrv.binary, odrv.binary, odrv.credential_present
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


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
    r = subprocess.run([str(ROOT / "bin" / "ocp"), "launch", "st"],
                       capture_output=True, text=True, env=env)
    check("launch: a stale snapshot does not deadlock the launch",
          r.returncode == 0, r.stdout + r.stderr)
    check("launch: but it warns that the reading is old",
          "may be out of date" in r.stderr, r.stderr)

    # A current reading over the limit still blocks.
    write_status(home, "delta", pct_5h=100)
    r = subprocess.run([str(ROOT / "bin" / "ocp"), "launch", "st"],
                       capture_output=True, text=True, env=env)
    check("launch: a current over-limit reading still blocks", r.returncode != 0, r.stdout)

    # Staleness stays fail-closed where it belongs: assignment.
    write_status(home, "delta", pct_5h=20, collected_at=int(time.time()) - 100000)
    check("launch: staleness is still fail-closed for assignment",
          not status.read("delta").assignable()[0])


def test_shared_account_context_isolation(home: Path) -> None:
    """Two agents on one account must not be judged by each other's context.

    Rate limits are account-wide and shared correctly; context is per session,
    and each agent's comes from the transcript its own session is writing.
    """
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    agents.register("ag_a", "shared")
    agents.register("ag_b", "shared")
    accounts.register("shared")
    now = int(time.time())
    write_status(home, "shared", pct_5h=30, pct_7d=12, reset_5h=now + 1800,
                 rl_at=now, reset_7d=now + 99999, rl7_at=now)

    for agent, tokens in (("ag_a", 700_000), ("ag_b", 50_000)):
        cwd = home / f"wt-{agent}"
        cwd.mkdir(parents=True, exist_ok=True)
        write_transcript(home, "shared", cwd,
                         f"0000000{agent[-1]}-0000-4000-8000-00000000000a",
                         turns=[("claude-opus-5", {"cache_read_input_tokens": tokens})])
        collect.collect(agent, cwd)

    check("shared account: each agent writes its own status file",
          (home / "agents" / "ag_a" / "status.json").exists()
          and (home / "agents" / "ag_b" / "status.json").exists())
    a = status.read_for_agent("ag_a", "shared")
    b = status.read_for_agent("ag_b", "shared")
    check("shared account: each agent keeps its own context",
          (a.ctx_pct, b.ctx_pct) == (70, 5), f"a={a.ctx_pct} b={b.ctx_pct}")
    check("shared account: rate limits are still shared",
          a.pct_5h == b.pct_5h == 30, f"a={a.pct_5h} b={b.pct_5h}")

    # The context trigger must fire for the full agent and not for the other.
    ta = triggers.evaluate(a.pct_5h, a.ctx_used)
    tb = triggers.evaluate(b.pct_5h, b.ctx_used)
    check("shared account: the full agent's context trigger fires",
          ta.fired and ta.kind == "context", ta.reason)
    check("shared account: the other agent's does not", not tb.fired, tb.reason)

    # An agent with no session file of its own must not borrow one.
    agents.register("ag_c", "shared")
    c = status.read_for_agent("ag_c", "shared")
    check("shared account: an agent with no session file falls back to the account",
          c.pct_5h == 30)


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


def test_init_keeps_the_lock_file_out_of_the_home_repo(home: Path) -> None:
    """The lock file's pid changes on every daemon start; tracked, it is a
    change `git status` shows forever."""
    os.environ["OCP_HOME"] = str(home)
    with contextlib.redirect_stdout(io.StringIO()):
        climod.cmd_init(argparse.Namespace())
    lines = (home / ".gitignore").read_text().split()
    check("init: locks/ is ignored", "locks/" in lines, str(lines))
    check("init: and nothing names the pidfile that no longer exists",
          "daemon.pid" not in lines, str(lines))


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
        with lock.singleton(dmn.DAEMON_LOCK):
            r = doctor.Report()
            doctor._check_service(r)
            check("doctor: not-installed is fine when a daemon is running by hand",
                  r.rows[-1][0] == doctor.OK, str(r.rows[-1]))

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


def test_drift_git_gives_one_clean_entry_per_file(tmp: Path) -> None:
    """auditor's #1194: drift._git itself hands back sanitised records, one per
    file, rather than leaning on cmd_drift sanitising at the sink. Split on
    newlines only: a name holding U+0085 or U+2028 is still one file."""
    repo = tmp / "repo"
    repo.mkdir(parents=True)

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    git("init", "-q", "-b", "main")
    git("config", "core.quotePath", "false")      # names come back verbatim, as they do here
    names = ["a\u0085b.txt", "c\u2028d.txt", "e\x9b31mf.txt", "plain.txt"]
    for n in names:
        (repo / n).write_text("x")
    git("add", ".")
    ok, out = drift._git(["ls-files"], repo)
    entries = out.split("\n")
    check("drift._git: one entry per file, whatever the name holds",
          ok and len(entries) == len(names), repr(entries))
    check("drift._git: and nothing a terminal would obey reaches a caller",
          not any(c in out for c in "\x9b\x85\u2028"), repr(out))


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


def test_doctor_agrees_with_the_pick_about_an_account_with_an_agent_mid_turn(
        home: Path) -> None:
    """doctor is the Phase 1 criterion's own surface (KICKOFF §3), so it must
    not print "assignable" about a stale account the pick refuses for having
    an agent of ours mid-turn on it - the five call sites idle_floor's active
    gate does not reach by default (doctor, actions, tui, the two cli list
    commands) each need the same figure pick_account already gets."""
    _daemon_home(home, 20)
    _with_accounts("beta")
    now = int(time.time())
    write_status(home, "beta", pct_5h=40, pct_7d=10, reset_5h=now + 1800,
                 reset_7d=now + 86400, collected_at=now - 3600)
    con = db.connect()
    try:
        _running_on(con, "beta", "w1")
    finally:
        con.close()
    check("pick: refuses the account while an agent is mid-turn on it",
          status.pick_account(["beta"])[0] is None)
    r = doctor.Report()
    doctor._check_accounts(r)
    row = next(msg for _lvl, msg, _detail in r.rows if msg.startswith("account beta:")
              and ("assignable" in msg))
    check("doctor: agrees - not assignable, not the pick's blind spot",
          "not assignable" in row, row)


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
        orcamod.run_current = lambda: None       # register_worker looks for a Run to join;
        orcamod.run_create = lambda objective: None    # there is none, so the worker is not registered

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
        subprocess.run([str(ROOT / "bin" / "ocp"), "install-hooks"],
                       capture_output=True, text=True, env=env)

    data = json.loads(target.read_text())
    ours = [h["command"] for ev, groups in data["hooks"].items() for g in groups
            for h in g.get("hooks", []) if " hook " in h.get("command", "")]
    stops = [c for c in ours if c.endswith("hook Stop")]
    check("hooks: one entry per event however it was spelled before",
          len(stops) == 1, str(stops))
    check("hooks: every captured event is installed, and the ones that answer a prompt",
          {c.rsplit(" ", 1)[1] for c in ours} == set(evmod.CAPTURED | evmod.ANSWERS),
          str(sorted({c.rsplit(" ", 1)[1] for c in ours})))
    check("hooks: Orca's own entries are left alone",
          any(h.get("command") == "orca-hook pre"
              for g in data["hooks"]["PreToolUse"] for h in g.get("hooks", [])),
          str(data["hooks"].get("PreToolUse")))
    check("hooks: the prompt and idle witnesses are among them",
          {"PermissionRequest", "TeammateIdle"} <= evmod.CAPTURED)


def test_hr_add_takes_the_vendor_from_the_named_account(home: Path) -> None:
    """#1964 (task #41, founder #1880): `hr add` wrote no vendor into
    agent.yaml, so a claude agent registered onto an opencode account could
    not launch (`ocp launch` refuses the mismatch) - a human had to add
    `vendor: opencode` to agent.yaml by hand. The vendor is read from the
    account named, not typed; a --vendor that disagrees is refused before
    anything is written."""
    _daemon_home(home, 20)
    accounts.register("alpha")
    accounts.register("zai", vendor="opencode")
    con = db.connect()
    try:
        ok, said = hr.enact("add", "claudey", why="w", account="alpha")
        check("hr add: succeeds on a claude account", ok, said)
        cfg = yamlcompat.load(agents.agent_file("claudey").read_text())
        check("hr add: no vendor key for the default vendor, same as every "
              "existing agent.yaml", "vendor" not in cfg, str(cfg))
        check("hr add: agent_vendor still reads it as claude",
              vendors.agent_vendor("claudey") == "claude")

        ok, said = hr.enact("add", "glm", why="w", account="zai")
        check("hr add: succeeds on an opencode account", ok, said)
        cfg = yamlcompat.load(agents.agent_file("glm").read_text())
        check("hr add: the account's vendor is written", cfg.get("vendor") == "opencode",
              str(cfg))
        check("hr add: agent_vendor reads it as opencode",
              vendors.agent_vendor("glm") == "opencode")

        ok, said = hr.enact("add", "wrong", why="w", account="zai", vendor="claude")
        check("hr add: a --vendor that disagrees with the account is refused",
              not ok and "disagrees" in said, said)
        check("hr add: refused, nothing was registered", "wrong" not in paths.list_agents())
        check("hr add: refused, no proposal file left behind for the draft",
              not [p_ for p_ in hr.listing() if p_["agent"] == "wrong"])

        ok, said = hr.enact("add", "agree", why="w", account="zai", vendor="opencode")
        check("hr add: a --vendor that agrees with the account is not refused", ok, said)

        # A proposal (an agent asking, not the founder) raises with the
        # vendor named, so the founder is not asked to approve a fact about
        # the account they cannot see.
        (home / "team.yaml").write_text("""
agents:
  marina: { intake: dedicated }
edges:
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
""")
        agents.register("marina", "alpha")
        ok, said = hr.propose("add", "glm2", requester="marina", why="w", account="zai")
        check("hr add: a proposal on an opencode account raises", ok, said)
        pid = said.split()[1]
        prop = hr.load_proposal(pid)
        check("hr add: the proposal itself records the vendor",
              prop.get("vendor") == "opencode", str(prop))
        req = db.get_request(con, prop["request"])
        check("hr add: the escalation names the vendor to the founder",
              "vendor opencode" in req["body"], req["body"])

        # Applying it writes the same thing enact does.
        con.execute("UPDATE requests SET state = 'done', verdict = 'approve' WHERE id = ?",
                   (prop["request"],))
        ok, said = hr.apply(pid, con)
        check("hr add: applying an approved proposal succeeds", ok, said)
        cfg = yamlcompat.load(agents.agent_file("glm2").read_text())
        check("hr add: apply writes the vendor the proposal recorded",
              cfg.get("vendor") == "opencode", str(cfg))

        # auditor #1971's should-fix: a proposal saved before vendor was
        # recorded has no vendor key. It must still apply with the account's
        # real vendor, not silently as claude.
        ok, said = hr.propose("add", "glm3", requester="marina", why="w", account="zai")
        pid3 = said.split()[1]
        prop3 = hr.load_proposal(pid3)
        del prop3["vendor"]
        hr.save(prop3)
        con.execute("UPDATE requests SET state = 'done', verdict = 'approve' WHERE id = ?",
                   (prop3["request"],))
        ok, said = hr.apply(pid3, con)
        check("hr add: an old proposal with no vendor key still applies", ok, said)
        cfg = yamlcompat.load(agents.agent_file("glm3").read_text())
        check("hr add: it takes the vendor from the account, not claude",
              cfg.get("vendor") == "opencode", str(cfg))
        check("hr add: agent_vendor reads it as opencode",
              vendors.agent_vendor("glm3") == "opencode")
    finally:
        con.close()


def test_register_writes_vendor_onto_an_agent_that_already_exists(home: Path) -> None:
    """auditor #1971's surviving mutation: register's existing-agent branch
    (`if vendor and vendor != DEFAULT_VENDOR: cfg["vendor"] = vendor`) had no
    test of its own. This is the orcapipe.glm case: the agent's folder is
    made first, by hand or by an earlier register, and the vendor arrives on
    a later call - hr's own re-register among them."""
    _daemon_home(home, 20)
    accounts.register("alpha")
    agents.register("late", "alpha")
    cfg = yamlcompat.load(agents.agent_file("late").read_text())
    check("register existing: no vendor key yet", "vendor" not in cfg, str(cfg))

    agents.register("late", "alpha", vendor="opencode")
    cfg = yamlcompat.load(agents.agent_file("late").read_text())
    check("register existing: a later call writes the vendor onto the "
          "already-registered agent", cfg.get("vendor") == "opencode", str(cfg))
    check("register existing: agent_vendor reads it as opencode",
          vendors.agent_vendor("late") == "opencode")


def test_every_door_onto_an_open_proposal_also_needs_a_decision(home: Path) -> None:
    """#1893 SF1, #1908, #1919, #1938, #1943: `answer()` (the bridge's key)
    is not the only door onto a queued proposal escalation - `ocp resolve`,
    `--retires`, `ask.cancel`, `--replaces` and `ask.withdraw` each reach the
    same row, and only `answer()` had the guard. A verdict, a retirement, a
    cancel, a replace or a withdraw typed there that is not a decision used
    to close the row anyway, leaving the proposal stuck exactly as if the
    bridge had let it through."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        accounts.register("beta")
        agents.register("hr", "beta")
        (home / "team.yaml").write_text("""
agents:
  hr: { intake: serial }
edges:
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
""")
        ok, said = hr.propose("add", "dohyun", requester="hr", why="more hands",
                              account="beta")
        pid = said.split()[1]
        rid = int(said.split("#")[1].split(".")[0])
    finally:
        con.close()
    rc, out = _cli(["resolve", str(rid), "sounds good"], as_agent=askmod.HUMAN)
    con = db.connect()
    try:
        check("resolve: an open proposal refuses a non-decision here too",
              rc == 1 and "approve" in out and db.get_request(con, rid)["state"] == "queued",
              out)
    finally:
        con.close()
    rc, out = _cli(["resolve", str(rid), "approve"], as_agent=askmod.HUMAN)
    check("resolve: approve still goes through", rc == 0, out)
    ok, said = hr.apply(pid)
    check("resolve: and apply carries it out", ok and "dohyun" in paths.list_agents(), said)

    # #1908 should-fix: the same escalation can be closed a third way, through
    # --retires on an unrelated resolve - "replaced by #N" is not a decision
    # either.
    ok, said = hr.propose("add", "yerin", requester="hr", why="a second pair",
                          account="beta")
    pid2 = said.split()[1]
    rid2 = int(said.split("#")[1].split(".")[0])
    ok, _, rid3 = askmod.ask(askmod.HUMAN, "unrelated question", requester="hr")
    check("retires: the unrelated ask queued", ok and rid3 is not None)
    rc, out = _cli(["resolve", str(rid3), "covered", "--retires", str(rid2)],
                   as_agent=askmod.HUMAN)
    check("retires: an open proposal refuses to be retired away",
          rc == 1 and "approve" in out, out)
    con = db.connect()
    try:
        check("retires: and stays queued", db.get_request(con, rid2)["state"] == "queued")
    finally:
        con.close()

    # #1919: task #32's finding - ask.cancel is the same family, since
    # CANCEL_VERDICT is not a decision either.
    con = db.connect()
    try:
        said = actions.cancel(con, rid2)
    finally:
        con.close()
    check("cancel: an open proposal refuses to be cancelled away",
          "approve" in said, said)
    con = db.connect()
    try:
        check("cancel: and stays queued", db.get_request(con, rid2)["state"] == "queued")
    finally:
        con.close()

    # #1938 should-fix: a fifth door - the proposer's own `--replaces` passes
    # the ownership check (old["requester"] == requester) and would close the
    # escalation with "replaced by #N", a row the founder cannot tell apart
    # from the original and that approving does not enact.
    ok, said, rid4 = askmod.ask(askmod.HUMAN, "corrected: add yerin on gamma",
                                requester="hr", replaces=rid2)
    check("replaces: an open proposal refuses to be replaced away",
          not ok and "withdraw" in said, said)
    check("replaces: no new row was queued for it", rid4 is None, rid4)
    con = db.connect()
    try:
        check("replaces: and stays queued", db.get_request(con, rid2)["state"] == "queued")
    finally:
        con.close()

    # #1943: a sixth door - the code review that found the chokepoint's
    # should-fixes also found this one. The proposer owns its own escalation
    # (it is the requester), so `ocp withdraw` - not `ocp hr withdraw` - is
    # ownership-eligible too, and would close it as "withdrawn: ..." if the
    # chokepoint let it.
    ok, said = askmod.withdraw(rid2, requester="hr")
    check("withdraw: an open proposal points at `hr withdraw` instead",
          not ok and "hr withdraw" in said, said)
    con = db.connect()
    try:
        check("withdraw: and stays queued", db.get_request(con, rid2)["state"] == "queued")
    finally:
        con.close()

    # `hr withdraw` is the escape valve, and still works on the same row.
    ok, said = hr.withdraw(pid2, "hr")
    check("hr withdraw: the one real way out still works", ok, said)
    con = db.connect()
    try:
        check("hr withdraw: and the row is finally closed",
              db.get_request(con, rid2)["state"] != "queued")
    finally:
        con.close()


def test_the_chokepoint_alone_refuses_every_door_onto_an_open_proposal(home: Path) -> None:
    """#1943: the test above proves each door's own, friendlier check works.
    This one proves the check those doors share does too - by calling the
    three db.py functions that can move a request row out of 'queued'
    directly, the way each door's own write eventually does, with none of
    the doors' own checks anywhere in the call path to catch it first. If
    this passed only because a door's check ran, deleting that check would
    not fail it; deleting `_would_strand_a_proposal` instead does.

    `close_if_queued` is `answer()`, `ocp resolve` and `ask.cancel`'s write.
    `retire_queued` is `--retires`'s, and `--replaces`'s when the row being
    replaced targets the founder - true of every proposal escalation, since
    only the founder decides one. `retire_if_undelivered` is `--replaces`'s
    write for a row targeting an agent instead; no proposal escalation ever
    takes that path, but the function is part of the same family and gets
    the same guard, so it is exercised here too."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        accounts.register("beta")
        agents.register("hr", "beta")
        (home / "team.yaml").write_text("""
agents:
  hr: { intake: serial }
edges:
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
""")
        ok, said = hr.propose("add", "sooyoung", requester="hr", why="a third pair",
                              account="beta")
        rid = int(said.split("#")[1].split(".")[0])

        check("close_if_queued: refuses a non-decision verdict on an open proposal",
              db.close_if_queued(con, rid, "done", "looks good", announced=True) is False)
        check("close_if_queued: and stays queued", db.get_request(con, rid)["state"] == "queued")

        check("retire_queued: refuses to retire an open proposal away",
              db.retire_queued(con, rid, "replaced", "replaced by #9999") is False)
        check("retire_queued: and stays queued", db.get_request(con, rid)["state"] == "queued")

        check("retire_if_undelivered: refuses an open proposal too",
              db.retire_if_undelivered(con, rid, "replaced", "replaced") is False)
        check("retire_if_undelivered: and stays queued",
              db.get_request(con, rid)["state"] == "queued")

        check("close_if_queued: a real decision still goes through",
              db.close_if_queued(con, rid, "done", "approve") is True)
        check("close_if_queued: and the row is no longer queued",
              db.get_request(con, rid)["state"] == "done")

        # #2024: db.py once also held an unguarded state-setter, so the
        # docstring's "every function below" was false for it. That function
        # moved to this file as `_set_request_state`, test-only. Auditor
        # #2072 should-fix: pinning set_request_state's absence by name only
        # catches that name coming back, not a new unguarded door under a
        # different one - assert the property db.py must hold instead: every
        # function that writes requests.state calls _would_strand_a_proposal,
        # and the three that do are named here so this cannot pass by
        # finding none. Auditor #2094 should-fix: a raw substring match on
        # the function's source missed a writer whose SET clause orders
        # `state` after another column, and one written as two adjacent
        # string literals (a style db.py already uses) - Python folds
        # adjacent literals into one ast.Constant, so walk those instead of
        # the source text, and isolate the SET clause before looking for
        # `state =` in it, or a WHERE clause naming `state` (mark_delivered's
        # and _pre_schema_migrate's) reads as a writer too.
        import ast
        import re
        db_src = (ROOT / "ocp" / "db.py").read_text()
        db_tree = ast.parse(db_src)
        set_clause_re = re.compile(r"UPDATE\s+requests\s+SET\s+(.*?)(?:\bWHERE\b|$)",
                                   re.S | re.I)
        state_col_re = re.compile(r"(^|[\s,])state\s*=")
        guarded_writers, unguarded_writers = set(), set()
        for n in db_tree.body:
            if not isinstance(n, ast.FunctionDef):
                continue
            writes_state = False
            for c in ast.walk(n):
                if not (isinstance(c, ast.Constant) and isinstance(c.value, str)):
                    continue
                m = set_clause_re.search(c.value)
                if m and state_col_re.search(m.group(1)):
                    writes_state = True
                    break
            if not writes_state:
                continue
            calls_guard = any(
                isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                and c.func.id == "_would_strand_a_proposal"
                for c in ast.walk(n))
            (guarded_writers if calls_guard else unguarded_writers).add(n.name)
        check("chokepoint: every db.py function writing requests.state "
              "calls _would_strand_a_proposal",
              not unguarded_writers and guarded_writers ==
              {"retire_queued", "close_if_queued", "retire_if_undelivered"},
              f"guarded={guarded_writers} unguarded={unguarded_writers}")
    finally:
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


def test_doctor_and_ps_report_a_drifted_launch(home: Path) -> None:
    """The daemon relaunches a drifted session on its own before it would
    ever type into it again - `test_a_drifted_launch_relaunches_instead_of_
    waking_in_place` above. Between the drift and that next wake, a human
    reading `doctor` or `ps` should be able to see why a running agent still
    looks like it predates a recent change, instead of only finding out once
    it is woken.
    """
    _daemon_home(home, 20)
    con = db.connect()
    real = obsmod.live_sessions
    try:
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_launch_fingerprint(con, "backend", "stale-fingerprint")

        r = doctor.Report()
        doctor._check_launch_drift(r)
        check("doctor: says nothing without a session to be running the "
              "stale recipe", not r.rows, str(r.rows))

        sessions_are("backend")
        r = doctor.Report()
        doctor._check_launch_drift(r)
        check("doctor: a running session on a fingerprint `build()` would "
              "not produce now is reported",
              r.rows and r.rows[-1][0] == doctor.WARN
              and "backend" in r.rows[-1][1], str(r.rows))
        printed = _capture(lambda: climod.cmd_daemon_status(None))
        check("ps: the screen says it too",
              any(l.startswith("backend") and "·drift" in l for l in printed.splitlines()),
              printed)

        db.set_launch_fingerprint(con, "backend", launch.fingerprint("backend"))
        r = doctor.Report()
        doctor._check_launch_drift(r)
        check("doctor: and says nothing once the recorded fingerprint matches",
              not r.rows, str(r.rows))

        # A row from before `launch_fingerprint` existed has nothing recorded
        # at all - not evidence the recipe still matches, so it reads the
        # same as a real mismatch rather than being left alone.
        db.set_launch_fingerprint(con, "backend", None)
        r = doctor.Report()
        doctor._check_launch_drift(r)
        check("doctor: a session with no recorded fingerprint reads as drift too",
              r.rows and r.rows[-1][0] == doctor.WARN
              and "backend" in r.rows[-1][1], str(r.rows))
        printed = _capture(lambda: climod.cmd_daemon_status(None))
        check("ps: and the screen marks it the same way",
              any(l.startswith("backend") and "·drift" in l for l in printed.splitlines()),
              printed)
    finally:
        obsmod.live_sessions = real
        con.close()


def test_trust_prompt_folder_parses_a_live_capture() -> None:
    """auditor's #339 should-fix 3: `trust_prompt_folder` is what blocker 2's
    whole fix rests on, and nothing pinned its join-bare, wrapped-path
    behaviour against a real screen - only a synthetic one-liner. A parse
    that silently comes out wrong fails closed (escalate + STALLED), so a
    quiet regression here stalls every agent in a declared root."""
    check("trust_prompt_folder: the wrapped path is joined bare, no space",
          orcamod.trust_prompt_folder(LIVE_TRUST_PROMPT_CAPTURE) == LIVE_TRUST_PROMPT_FOLDER,
          orcamod.trust_prompt_folder(LIVE_TRUST_PROMPT_CAPTURE))
    check("trust_prompt_folder: trust_prompt_showing agrees this is the screen",
          orcamod.TRUST_PROMPT_MARKER in LIVE_TRUST_PROMPT_CAPTURE)
    check("trust_prompt_folder: no marker at all is None, not a guess",
          orcamod.trust_prompt_folder("ordinary TUI, nothing to see here") is None)
    check("trust_prompt_folder: the marker with nothing after it is None too",
          orcamod.trust_prompt_folder("Accessing workspace:") is None)


def test_a_declared_root_gets_standing_consent(home: Path) -> None:
    """Standing consent, and blocker 2 (auditor's #304): pressed only for the
    folder the dialog itself names, checked against both the declared roots
    and the recorded worktree, and only once the keys actually land.
    Anything else escalates, the same as with no standing consent at all."""
    _daemon_home(home, 50)
    con = db.connect()
    wt = home / "worktree"
    wt.mkdir(parents=True, exist_ok=True)
    other = home / "elsewhere"
    other.mkdir(parents=True, exist_ok=True)

    def screen_for(path) -> str:
        return (f"some prompt line\nAccessing workspace:\n{path}\n"
                f"Quick safety check: Is this a project you created or one you trust?\n"
                f"...\nNo, exit\nYes, I trust this folder\n")

    real = (orcamod.resume_with, orcamod.alive, orcamod.trust_prompt_showing,
            orcamod.confirm_trust, orcamod.wait_idle, orcamod.screen_text,
            trustmod.is_declared, orcamod.idle_now, orcamod.send, orcamod.screen_blocker,
            orcamod.time.sleep)
    sent: list[str] = []
    trusted: list[str] = []
    showing = {"v": True}

    def reset():
        showing["v"] = True
        sent.clear()
        trusted.clear()
        # A typed handoff is consumed, so each case parks with a fresh one.
        keep_handoff("backend", "# handoff · backend\n## next\n- carry on\n")
        db.set_agent_state(con, "backend", dmn.PARKED, detail="reset for the next case")

    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        orcamod.resume_with = lambda h, text: (sent.append(text), (True, "typed"))[1]
        orcamod.wait_idle = lambda h, *a, **k: True
        orcamod.trust_prompt_showing = lambda h: showing["v"]
        # As in production: the trust dialog is a modal to screen_blocker.
        orcamod.screen_blocker = lambda h: (orcamod.Blocker(orcamod.MODAL)
                                            if orcamod.trust_prompt_showing(h) else None)
        sessions_are("backend")
        db.set_terminal(con, "backend", "term_x", str(wt))
        keep_handoff("backend", "# handoff · backend\n## next\n- carry on\n")

        # 1. Not a declared root at all: escalates, nothing pressed.
        orcamod.screen_text = lambda h: screen_for(wt)
        trustmod.is_declared = lambda p: False
        orcamod.confirm_trust = lambda h: (trusted.append(h), (True, "trusted"))[1]
        ok, why = dmn.wake_agent(con, "backend", lambda _m: None)
        check("wake: an undeclared root escalates, nothing confirmed or typed",
              not ok and not sent and not trusted, str((ok, sent, trusted)))
        check("wake: ...and the agent is stalled",
              db.get_agent_state(con, "backend")["state"] == dmn.STALLED)

        # 2. Declared, but the dialog names a folder the record does not.
        reset()
        orcamod.screen_text = lambda h: screen_for(other)
        trustmod.is_declared = lambda p: True
        ok, why = dmn.wake_agent(con, "backend", lambda _m: None)
        check("wake: a recorded path that differs from the dialog's folder escalates",
              not ok and not sent and not trusted, str((ok, sent, trusted)))

        # 3. Declared and matching, but the keys never land.
        reset()
        orcamod.screen_text = lambda h: screen_for(wt)
        ok, why = dmn.wake_agent(con, "backend", lambda _m: None)
        check("wake: keys that did not land escalate rather than typing blind",
              not ok and not sent, str((ok, sent, trusted)))

        # 4. Declared, matching, and the keys land: pressed, then typed.
        reset()
        def confirm_and_close(h):
            trusted.append(h)
            showing["v"] = False
            return True, "trusted"
        orcamod.confirm_trust = confirm_and_close
        ok, why = dmn.wake_agent(con, "backend", lambda _m: None)
        check("wake: declared, matching, and the keys land: pressed",
              ok and trusted == ["term_x"], str((ok, trusted)))
        check("wake: ...and the handoff is still typed once it is",
              sent and "carry on" in sent[-1], sent)
        events = [dict(r) for r in db.events_after(con, 0, agent="backend")]
        check("wake: ...and standing consent is on the record",
              any(e["kind"] == "trust_confirmed" for e in events), events)

        # 5. The keys land but claude is still redrawing: the settle waits for
        # the screen to clear instead of reading the redraw as a refusal.
        reset()
        confirmed, reads = {"v": False}, {"n": 0}

        def slow_redraw(h):
            if not confirmed["v"]:
                return True
            reads["n"] += 1
            return reads["n"] < 3      # still drawing for the first two reads

        def confirm_landing(h):
            confirmed["v"] = True
            return True, "trusted"
        orcamod.trust_prompt_showing = slow_redraw
        orcamod.confirm_trust = confirm_landing
        orcamod.time.sleep = lambda s: None
        ok, why = dmn.wake_agent(con, "backend", lambda _m: None)
        check("wake: a redraw that outlasts the press is waited out, not escalated",
              ok and sent and reads["n"] >= 3
              and db.get_agent_state(con, "backend")["state"] != dmn.STALLED,
              str((ok, why, reads, sent)))
        orcamod.time.sleep = real[-1]
        orcamod.trust_prompt_showing = lambda h: showing["v"]

        # 6. The deliver paths meet the same screen: typeable_now refuses it,
        # so neither types nor presses - the sweep is what reports it.
        reset()
        orcamod.confirm_trust = lambda h: (trusted.append(h), (True, "trusted"))[1]
        orcamod.screen_text = lambda h: screen_for(wt)
        trustmod.is_declared = lambda p: False
        orcamod.idle_now = lambda h: True
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        db.add_request(con, "reviewer", "backend", "please look", None,
                       "peer", "fire_and_forget", None, 0, None)
        asked = db.add_request(con, "backend", "reviewer", "did it land?", None,
                               "peer", "async", None, 0, None)
        _set_request_state(con, asked, "done", "yes")
        db.set_agent_state(con, "backend", dmn.RUNNING)
        dmn.deliver_answers(con, "backend", lambda _m: None)
        dmn.deliver_queued(con, "backend", lambda _m: None)
        check("trust: the deliver paths neither type nor press at the screen",
              not sent and not trusted, str((sent, trusted)))
    finally:
        (orcamod.resume_with, orcamod.alive, orcamod.trust_prompt_showing,
         orcamod.confirm_trust, orcamod.wait_idle, orcamod.screen_text,
         trustmod.is_declared, orcamod.idle_now, orcamod.send, orcamod.screen_blocker,
         orcamod.time.sleep) = real
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


def test_account_add_login_preflight_names_the_actual_blocker(home: Path) -> None:
    """The login preflight refuses for two different reasons - the vendor's
    binary is not on PATH, or stdin is not a terminal. One message for both
    sends a reader without the binary hunting for a terminal, and a reader
    in a script off to install something they have."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)

    def run_cli(*argv):
        buf, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
            rc = climod.main(list(argv))
        return rc, buf.getvalue() + err.getvalue()

    drv = vendors.get("claude")
    real_stdin = sys.stdin

    class NoTty:
        def isatty(self) -> bool:
            return False

    try:
        drv.binary = lambda: None
        rc, out = run_cli("account", "add", "delta")
        check("account add: a missing binary is named and said to be off PATH",
              rc == 1 and "claude was not found on PATH" in out
              and "terminal" not in out, out)

        drv.binary = lambda: "/x/bin/claude"
        sys.stdin = NoTty()
        rc, out = run_cli("account", "add", "delta")
        check("account add: no terminal says terminal, not PATH",
              rc == 1 and "needs a terminal you can type into" in out
              and "not found on PATH" not in out, out)
    finally:
        del drv.binary
        sys.stdin = real_stdin


def test_offer_to_trust_worktrees_lists_paths_and_skips(home: Path) -> None:
    """Should-fix 5 (auditor's #304): the y/N question names exactly the
    worktrees a yes will press, and reports the rest as skipped rather than
    acting on them silently."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    agents.register("backend", "alpha")
    agents.register("frontend", "alpha")
    wt_in = home / "declared" / "backend-wt"
    wt_in.mkdir(parents=True, exist_ok=True)
    wt_out = home / "elsewhere" / "frontend-wt"
    wt_out.mkdir(parents=True, exist_ok=True)
    con = db.connect()
    try:
        db.set_terminal(con, "backend", "term_b", str(wt_in))
        db.set_terminal(con, "frontend", "term_f", str(wt_out))
    finally:
        con.close()
    trustmod.enact(str(home / "declared"), "test root")

    real_trust = actions.trust_worktrees
    asked: list[str] = []
    try:
        actions.trust_worktrees = lambda acct, wts: [f"{a}: trusted" for a, w in wts]
        out = _capture(lambda: climod._offer_to_trust_worktrees(
            "gamma", lambda p: (asked.append(p), "y")[1]))
    finally:
        actions.trust_worktrees = real_trust
    check("offer: the declared worktree is named in the question",
          any("backend" in p and str(wt_in) in p for p in asked), asked)
    check("offer: the undeclared one is named as skipped, not offered",
          any("skipping" in p and "frontend" in p for p in asked), asked)
    check("offer: a yes goes ahead and trusts only the declared one",
          "backend: trusted" in out, out)


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


def test_a_file_added_to_shared_reaches_every_existing_account(home: Path) -> None:
    """The actual gap (founder #596, orcapipe-manager #668): `register()` skips
    a LINKED item whose shared target did not exist yet, and it only runs when
    an account is created or re-registered by hand - so alpha, beta and gamma
    had no CLAUDE.md link at all, silently, because the file did not exist
    when they were made. A fixture that creates the accounts after the file
    exists would pass on the broken code; this creates them before it, the way
    they actually are today."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    accounts.register("alpha")
    accounts.register("beta")
    accounts.register("gamma")
    alpha_claude = paths.account_claude("alpha") / "CLAUDE.md"
    beta_claude = paths.account_claude("beta") / "CLAUDE.md"
    gamma_claude = paths.account_claude("gamma") / "CLAUDE.md"
    check("relink: matches today - no CLAUDE.md link exists yet",
          not alpha_claude.exists() and not beta_claude.exists() and not gamma_claude.exists())

    # A deliberate override: a real file, not a link - what a human's own fix
    # or customization looks like on disk.
    gamma_claude.write_text("gamma's own")

    # The file is added the way the founder actually will: dropped straight
    # into shared/.claude, no `ocp` command run at all afterward.
    shared_target = paths.shared_claude() / "CLAUDE.md"
    shared_target.write_text("policy text")

    done = accounts.relink_all()
    check("relink: it reaches every account that existed before it did",
          alpha_claude.is_symlink() and beta_claude.is_symlink()
          and os.readlink(alpha_claude) == str(shared_target)
          and os.readlink(beta_claude) == str(shared_target), str(done))
    check("relink: and says what it did, by account",
          done.get("alpha") == ["CLAUDE.md"] and done.get("beta") == ["CLAUDE.md"], str(done))
    check("relink: an account's own override is left alone, not overwritten",
          "gamma" not in done and gamma_claude.read_text() == "gamma's own", str(done))
    check("relink: the override is what doctor reports, and nothing else",
          accounts.unlinked() == [("gamma", "CLAUDE.md")], str(accounts.unlinked()))
    rr = doctor.Report()
    doctor._check_shared_links(rr)
    check("relink: doctor warns naming the account and item",
          any(lv == doctor.WARN and "gamma: CLAUDE.md" in d for lv, _, d in rr.rows))
    check("relink: idempotent - nothing left to do once everyone is caught up",
          accounts.relink_all() == {})

    # A link pointing elsewhere: the timed pass leaves it, a human's register replaces it.
    beta_claude.unlink()
    beta_claude.symlink_to(home / "elsewhere")
    accounts.relink_all()
    check("relink: a link pointing elsewhere is not replaced by the timed pass",
          os.readlink(beta_claude) == str(home / "elsewhere"))
    accounts.register("beta")
    check("relink: register() still replaces it, as before",
          os.readlink(beta_claude) == str(shared_target))

    # The ordinary path still works: a freshly registered account gets it
    # straight from `register()`, with no relink pass needed.
    accounts.register("delta")
    check("relink: a freshly registered account is linked by register() itself",
          (paths.account_claude("delta") / "CLAUDE.md").is_symlink())

    # And the daemon does this without being told to, every tick - the actual
    # fix for "a human has to remember a command per account": nobody has to
    # remember anything, because the daemon is already running.
    accounts.register("epsilon")
    # Simulate epsilon predating CLAUDE.md ever landing in shared, the same
    # way alpha and beta did above - unlink what register() just linked, so
    # the tick has something of its own to catch up on.
    (paths.account_claude("epsilon") / "CLAUDE.md").unlink()
    orcamod.live_terminals = lambda: None    # tick's sweep asks Orca; this is about relink
    logs: list[str] = []
    con = db.connect()
    try:
        dmn.tick(con, logs.append)
    finally:
        con.close()
    check("relink: the daemon's own tick catches up an account with nothing to run for it",
          (paths.account_claude("epsilon") / "CLAUDE.md").is_symlink()
          and any("relinked epsilon" in ln for ln in logs), str(logs))


def test_jobs_refuses_a_busy_target_and_a_full_queue(home: Path) -> None:
    """`ocp/jobs.py` (task #45): a second action on a target already running one
    is refused with what is running and for how long, not queued or dropped;
    a fifth action while `MAX_JOBS` already run is refused for the same reason
    a screen has no room to show more than a few at once."""
    _daemon_home(home, 20)
    agents.register("reviewer", "alpha")
    jobs = jobsmod.Jobs()
    now = time.time()
    busy = jobsmod.Job("close", "reviewer", "closing reviewer", None, now - 4)
    jobs.running = [busy]
    said = jobs.start("wake", "reviewer", "waking reviewer")
    check("jobs: a second action on a busy target is refused, naming what is running",
          "still closing reviewer" in said and "4s" in said, said)
    check("jobs: the refusal starts nothing new", jobs.running == [busy])

    jobs.running = [jobsmod.Job("close", f"agent{i}", "x", None, now) for i in range(jobsmod.MAX_JOBS)]
    said = jobs.start("wake", "reviewer", "waking reviewer")
    check("jobs: a full queue is refused too, saying how many are already running",
          str(jobsmod.MAX_JOBS) in said and "already running" in said, said)


def test_jobs_stops_a_hung_action_at_its_declared_bound_not_past_it(home: Path) -> None:
    """The bug task #45 names: the old curses branch declared a verb's longest
    wait and then let it run another 30s past that before even asking it to
    stop - dismiss said "60s" and nothing killed it before 90. Here the stop
    fires the moment a job is older than its own declared `longest`, so what a
    screen says and when it acts agree - checked against the wall clock, not
    trusted from the source."""
    _daemon_home(home, 20)
    agents.register("reviewer", "alpha")
    stub = home / "stub_hang.py"
    stub.write_text("import time\nwhile True:\n    time.sleep(60)\n")
    real_command, real_wait, real_grace = jobsmod.ACT_COMMAND, actions.WAITS.get("dismiss"), jobsmod.GRACE_S
    try:
        jobsmod.ACT_COMMAND = [sys.executable, str(stub)]
        actions.WAITS["dismiss"] = ("", 1)     # `longest` = 1s, so the bound is quick to observe
        jobs = jobsmod.Jobs()
        said = jobs.start("dismiss", "reviewer", "dismissing reviewer")
        check("jobs: the hung action starts", said == "", said)
        started = time.time()
        # Still running just before its declared bound: it is not stopped early.
        time.sleep(0.7)
        jobs.reap()
        check("jobs: it is not stopped before its own declared longest",
              jobs.running, "stopped early")
        deadline = started + 1 + jobsmod.GRACE_S + 3     # generous margin past the fix's own bound
        stopped_at = None
        while time.time() < deadline:
            if not jobs.reap() and not jobs.running:
                break
            if jobs.running and jobs.running[0].stopped_at is not None and stopped_at is None:
                stopped_at = jobs.running[0].stopped_at
            if not jobs.running:
                break
            time.sleep(0.05)
        took = time.time() - started
        check("jobs: it is killed close to its declared bound, not 30s past it",
              took < 1 + jobsmod.GRACE_S + 2, f"took {took:.1f}s for a 1s bound")
        check("jobs: `terminate()` itself fires at the declared bound, before any extra grace",
              stopped_at is not None and stopped_at - started < 1 + 1,
              f"stopped_at {stopped_at}, started {started}")
    finally:
        jobsmod.ACT_COMMAND, jobsmod.GRACE_S = real_command, real_grace
        if real_wait is not None:
            actions.WAITS["dismiss"] = real_wait


def test_jobs_kills_the_group_when_a_grandchild_outlives_the_leader(home: Path) -> None:
    """auditor #2035 SHOULD-FIX 1: `_signal`'s SIGKILL only ever fired from the
    `code is None` branch of `reap` - while the leader (`job.proc`) was still
    alive. A leader that dies on SIGTERM but leaves a grandchild that ignores
    it (or is just slow) is reaped at once, so that branch never runs, and
    `communicate()` then blocks on the pipes the grandchild still holds open
    until *it* exits. The fix SIGKILLs the whole group once the leader is
    gone and the job had been stopped - checked here against the wall clock,
    not trusted from the source."""
    _daemon_home(home, 20)
    agents.register("reviewer", "alpha")
    stub = home / "stub_grandchild.py"
    stub.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c',\n"
        "    'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(20)'])\n"
        "time.sleep(60)\n"      # the leader itself has no handler, so SIGTERM ends it at once
    )
    real_command, real_wait, real_grace = jobsmod.ACT_COMMAND, actions.WAITS.get("dismiss"), jobsmod.GRACE_S
    try:
        jobsmod.ACT_COMMAND = [sys.executable, str(stub)]
        actions.WAITS["dismiss"] = ("", 1)
        jobs = jobsmod.Jobs()
        said = jobs.start("dismiss", "reviewer", "dismissing reviewer")
        check("jobs: the action with a grandchild starts", said == "", said)
        started = time.time()
        deadline = started + 1 + jobsmod.GRACE_S + 8
        while time.time() < deadline and jobs.running:
            jobs.reap()
            time.sleep(0.05)
        took = time.time() - started
        check("jobs: a grandchild that ignores SIGTERM does not freeze reap for its own sleep",
              not jobs.running and took < 1 + jobsmod.GRACE_S + 5,
              f"took {took:.1f}s for a 1s bound + {jobsmod.GRACE_S}s grace")
    finally:
        jobsmod.ACT_COMMAND, jobsmod.GRACE_S = real_command, real_grace
        if real_wait is not None:
            actions.WAITS["dismiss"] = real_wait


def test_jobs_lines_reports_state_honestly(home: Path) -> None:
    """The line for a running action: not there for one just started, said in
    each state it can be in (ordinary, slow, past its bound, stopped), capped
    at three with the rest counted, never a colour of its own."""
    _daemon_home(home, 20)
    now = time.time()

    def job(target: str, age: float, longest: int = 100, stopped: bool = False) -> jobsmod.Job:
        j = jobsmod.Job("close", target, f"closing {target}", None, now - age)
        j.longest = longest
        j.stopped_at = now if stopped else None
        return j

    jobs = jobsmod.Jobs()
    jobs.running = [job("a", 0.1)]
    check("lines: an action that has only just started is not drawn", jobs.lines(now) == [])

    jobs.running = [job("a", 3)]
    rows = jobs.lines(now)
    check("lines: it says what it is, what it waits for, how long, and the longest",
          "closing a" in rows[0].text and "waiting for it to finish its turn" in rows[0].text
          and "3s" in rows[0].text and "longest 1m40s" in rows[0].text, rows[0].text)
    check("lines: normal while ordinary", rows[0].level == "normal", rows[0].level)

    jobs.running = [job("a", 20)]
    check("lines: slow once it has taken a while", jobs.lines(now)[0].level == "slow")

    jobs.running = [job("a", 130)]
    row = jobs.lines(now)[0]
    check("lines: danger past its own declared longest, and says so in words",
          row.level == "danger" and "past the longest" in row.text, row.text)

    jobs.running = [job("a", 130, stopped=True)]
    row = jobs.lines(now)[0]
    check("lines: one that has been told to stop says so, not a countdown",
          row.level == "danger" and "no answer, stopping it" in row.text, row.text)

    jobs.running = [job(n, 3 + i) for i, n in enumerate("abcde")]
    rows = jobs.lines(now)
    check("lines: at most three rows, and the rest are counted",
          len(rows) == 4 and "+ 2 more running" in rows[-1].text, str([r.text for r in rows]))


def test_venv(home: Path) -> None:
    """ocp/venv.py's own docstring: `status` is a second copy of the sha256
    comparison bin/ocp's bash wrapper makes, kept honest by a test that runs
    both and compares. Both sides must read the same requirements.txt - the
    real one, since neither status() nor the wrapper takes a path for it.

    Task #54 (founder #2069): a missing or stale venv used to make the
    wrapper refuse outright (bin/ocp-venv, since deleted). It now falls back
    to running under PATH's python3 instead, so `ocp venv status` still runs
    - and still reports the venv as not ready - rather than the command
    itself failing to start."""
    home.mkdir(parents=True, exist_ok=True)
    vdir = home / "venv"
    wrapper = paths.repo_root() / "bin" / "ocp"

    def run_wrapper(home_dir: Path):
        return subprocess.run([str(wrapper), "venv", "status"], capture_output=True, text=True,
                               env={**os.environ, "OCP_HOME": str(home_dir)}, timeout=30)

    def wrapper_ok(home_dir: Path) -> bool:
        return run_wrapper(home_dir).returncode == 0

    ok, reason = venvmod.status(vdir)
    check("venv: status() refuses a venv that does not exist", not ok and "no venv" in reason, reason)
    r = run_wrapper(home)
    check("venv: the wrapper falls back to PATH python3 and agrees the venv is not ready",
          r.returncode == 1 and "ready: False" in r.stdout, f"out={r.stdout!r} err={r.stderr!r}")

    calls = []

    def fake_run(args):
        calls.append(args)
        if "venv" in args:
            (vdir / "bin").mkdir(parents=True, exist_ok=True)
            py = venvmod.venv_python(vdir)
            py.write_text("#!/bin/sh\n")
            py.chmod(0o755)
        return subprocess.CompletedProcess(args, 0, "", "")

    real_run = venvmod._run
    try:
        venvmod._run = fake_run
        ok, msg = venvmod.install(vdir, log=lambda *a: None)
        check("venv: install() creates the venv and reports success", ok, msg)
        check("venv: install() runs `python3 -m venv` before pip", calls[0][1:3] == ["-m", "venv"])
        check("venv: install() then pip installs requirements.txt into it",
              calls[1][1:4] == ["-m", "pip", "install"])
    finally:
        venvmod._run = real_run

    ok, reason = venvmod.status(vdir)
    check("venv: status() reports ready right after a successful install", ok, reason)
    stamp = vdir / venvmod.STAMP_NAME
    check("venv: install() stamps the venv with requirements.txt's canonical hash",
          stamp.read_text().strip() ==
          hashlib.sha256(venvmod._canonical_requirements(venvmod.requirements_file())).hexdigest())
    check("venv: the bash wrapper agrees a freshly-stamped venv is ready", wrapper_ok(home))

    # Founder #2134: a comment-only edit to requirements.txt used to flip
    # both sides to stale, since the stamp hashed the file's raw bytes -
    # `_canonical_requirements` (python) and bin/ocp's own strip-then-filter
    # (bash) must both still call the venv ready. auditor #2171 should-fix 2:
    # this used to write the mutation into the tracked, shared
    # requirements.txt (a killed run, or another process reading it mid-test,
    # both leave real damage) - it now runs against a copy of bin/ocp and an
    # isolated requirements.txt of its own instead. `_ocp_entry.py` is
    # symlinked, not copied: it locates the real `ocp` package through its
    # own resolved path, and a copy of it would look for one beside itself.
    iso_root = home / "iso_reqs"
    (iso_root / "bin").mkdir(parents=True, exist_ok=True)
    real_bin = paths.repo_root() / "bin"
    shutil.copy2(real_bin / "ocp", iso_root / "bin" / "ocp")
    (iso_root / "bin" / "ocp").chmod(0o755)
    (iso_root / "bin" / "_ocp_entry.py").symlink_to((real_bin / "_ocp_entry.py").resolve())
    iso_reqs = iso_root / "requirements.txt"
    iso_reqs.write_text(venvmod.requirements_file().read_text())
    iso_wrapper = iso_root / "bin" / "ocp"
    original_stamp = stamp.read_text()

    def run_iso_wrapper():
        return subprocess.run([str(iso_wrapper), "venv", "status"], capture_output=True, text=True,
                              env={**os.environ, "OCP_HOME": str(home)}, timeout=30)

    real_reqs_file = venvmod.requirements_file
    try:
        venvmod.requirements_file = lambda: iso_reqs

        # `stamp` is left as install() wrote it, for iso_reqs's unedited
        # content - re-stamping from the edited file here would only prove
        # the two sides hash the same edited bytes as each other, not that
        # a comment edit leaves an install stamped for the original content
        # still reading as ready (auditor #2181 BLOCKING).
        iso_reqs.write_text("# a comment nobody installs\n" + iso_reqs.read_text() + "# another one\n")
        ok, reason = venvmod.status(vdir)
        check("venv: status() ignores a comment-only requirements.txt edit", ok, reason)
        r = run_iso_wrapper()
        # rc == 0 alone would pass even on a fallback, since the real PATH
        # python3 that lands on also exits 0 - the stub venv python's silent
        # no-op stdout is what proves the wrapper itself agreed and actually
        # picked it (auditor #2171 should-fix 1: the previous version of this
        # check could not tell the two apart, and passed unchanged with
        # bin/ocp's own hash line put back to a raw `shasum`).
        check("venv: the bash wrapper ignores the same comment-only edit",
              r.returncode == 0 and r.stdout == "" and r.stderr == "",
              f"rc={r.returncode} out={r.stdout!r} err={r.stderr!r}")

        # code-review finding: a requirements.txt that filters down to
        # nothing (all comments/blank) has python's `_canonical_requirements`
        # hash `b""`, and bash's `grep -v` pipeline must hash the same zero
        # bytes - not "\n", which `shasum` hashes to a different digest.
        iso_reqs.write_text("# nothing here\n\n# not one real requirement\n")
        stamp.write_text(venvmod._sha256(iso_reqs) + "\n")
        r = run_iso_wrapper()
        check("venv: an all-comments requirements.txt hashes the same on both sides",
              r.returncode == 0 and r.stdout == "" and r.stderr == "",
              f"rc={r.returncode} out={r.stdout!r} err={r.stderr!r}")
    finally:
        venvmod.requirements_file = real_reqs_file
        stamp.write_text(original_stamp)

    # Review #1597 note 1: python's stamp.read_text().strip() and bash's
    # `read -r have < stamp` must agree on more than a clean "<sha>\n" -
    # that is the one input a diagnostic disagreeing with the thing it
    # diagnoses would slip through on.
    good_hash = stamp.read_text().strip()
    stamp.write_text(f"  {good_hash}  \n")
    ok, _ = venvmod.status(vdir)
    check("venv: status() strips surrounding whitespace from the stamp", ok)
    check("venv: the bash wrapper strips the same whitespace the same way", wrapper_ok(home))

    # Review #1613 should-fix: `read -r have < stamp` returns 1 on a stamp
    # with no trailing newline (EOF before the newline it looks for), and
    # under set -e that used to exit the wrapper right there - silently,
    # before fail() ever ran. A ready venv must still report ready.
    stamp.write_text(good_hash)
    r = run_wrapper(home)
    # The stand-in venv python is a no-op shell stub (fake_run wrote it, not
    # a real python), so a reached-python rc 0 with no output is what
    # distinguishes "the wrapper exec'd the venv" from the silent-exit
    # regression, not the venv python's own (nonexistent) stdout.
    check("venv: the wrapper reaches the venv python on a stamp with no trailing newline, not a silent exit",
          r.returncode == 0 and r.stdout == "" and r.stderr == "",
          f"rc={r.returncode} out={r.stdout!r} err={r.stderr!r}")

    # Auditor #1673 note 1: an empty stamp (the write_text truncate-then-write
    # race, or a crash between them) is the other half of #1613's regression -
    # `read -r` on it also returns 1, and the fix must still surface it as
    # stale (via the PATH python3 fallback), not swallow it the way the
    # pre-fix line did.
    stamp.write_text("")
    r = run_wrapper(home)
    check("venv: the wrapper falls back and reports an empty stamp as stale, not a silent exit",
          r.returncode == 1 and "ready: False" in r.stdout and "stale" in r.stdout,
          f"rc={r.returncode} out={r.stdout!r} err={r.stderr!r}")
    stamp.write_text(good_hash + "\n")

    stamp.write_text("not a real hash\n")
    ok, reason = venvmod.status(vdir)
    check("venv: status() refuses a venv stamped against a different requirements.txt",
          not ok and "stale" in reason, reason)
    r = run_wrapper(home)
    check("venv: the wrapper falls back on the same stale stamp",
          r.returncode == 1 and "stale" in r.stdout, f"out={r.stdout!r} err={r.stderr!r}")

    def fake_run_fails(args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, "", "boom")

    venvmod._run = fake_run_fails
    try:
        ok, msg = venvmod.install(vdir, log=lambda *a: None)
    finally:
        venvmod._run = real_run
    check("venv: install() reports failure when pip fails", not ok and "boom" in msg, msg)
    check("venv: a failed install leaves the venv reading as stale, not falsely fresh",
          stamp.read_text().strip() == "not a real hash")

    # Review #1597 note 2: status()'s own docstring promises (ok, reason) for
    # any way the venv cannot be trusted - a requirements.txt that vanished
    # (a broken checkout) is that family too, not a crash.
    real_reqs_file = venvmod.requirements_file
    try:
        venvmod.requirements_file = lambda: home / "no-such-requirements.txt"
        ok, reason = venvmod.status(vdir)
        check("venv: status() reports (False, reason) for a missing requirements.txt, not a traceback",
              not ok and "requirements.txt" in reason, reason)
    finally:
        venvmod.requirements_file = real_reqs_file

    # Same case through the bash wrapper: copy it, _ocp_entry.py and the ocp
    # package itself beside a venv with no requirements.txt one level up, so
    # `script_dir/../requirements.txt` resolves to nothing - the wrapper must
    # fall back clean, not `set -e` out on shasum's own raw error (review
    # #1597 note 2, carried over). A staging copy, not the repo's own
    # requirements.txt, so a killed test can never leave the real file
    # renamed away. The ocp package has to be copied too, not just imported
    # via PYTHONPATH: repo_root() derives from the ocp package's own __file__,
    # so importing the real package would make the fallback python3 see the
    # real repo's requirements.txt and miss the case entirely.
    reqshome = home / "reqsmissing"
    (reqshome / "bin").mkdir(parents=True, exist_ok=True)
    shutil.copy(wrapper, reqshome / "bin" / "ocp")
    shutil.copy(paths.repo_root() / "bin" / "_ocp_entry.py", reqshome / "bin" / "_ocp_entry.py")
    (reqshome / "bin" / "ocp").chmod(0o755)
    shutil.copytree(paths.repo_root() / "ocp", reqshome / "ocp",
                     ignore=shutil.ignore_patterns("__pycache__"))
    reqs_vdir = reqshome / "home" / "venv" / "bin"
    reqs_vdir.mkdir(parents=True)
    (reqs_vdir / "python3").write_text("#!/bin/sh\n")
    (reqs_vdir / "python3").chmod(0o755)
    r = subprocess.run([str(reqshome / "bin" / "ocp"), "venv", "status"],
                        capture_output=True, text=True, timeout=30,
                        env={**os.environ, "OCP_HOME": str(reqshome / "home")})
    check("venv: the bash wrapper falls back clean (not a raw shasum error) on a missing requirements.txt",
          r.returncode == 1 and "no requirements.txt" in r.stdout and "shasum" not in r.stderr, f"out={r.stdout!r} err={r.stderr!r}")


def main() -> int:
    def tests(td):
        home = Path(td) / "orcapipe"
        home.mkdir()
        run(lambda: test_init_leaves_the_status_line_slot_empty(Path(td) / 'lineslot'))
        run(lambda: test_a_failed_settings_write_keeps_the_old_scripts(Path(td) / 'slotorder'))
        run(lambda: test_an_unreadable_settings_file_is_refused_and_left_alone(Path(td) / 'setbad'))
        run(lambda: test_a_settings_write_that_dies_leaves_the_old_file(Path(td) / 'setatomic'))
        run(lambda: test_yaml())
        run(lambda: test_agents(home))
        run(lambda: test_trust_roots(Path(td) / 'trust_roots'))
        run(lambda: test_ocp_trust_cli_is_wired(Path(td) / 'trust_cli'))
        run(lambda: test_eligible_worktrees_splits_declared_from_not(Path(td) / 'eligible_wt'))
        run(lambda: test_trust_worktrees(Path(td) / 'trust_wt'))
        run(lambda: test_trust_worktrees_quotes_the_path(Path(td) / 'trust_wt_quote'))
        run(lambda: test_launch(home))
        run(lambda: test_doctor_checks_shared_mcp_config(home))
        run(lambda: test_team_routing())
        run(lambda: test_yaml_differential())
        run(lambda: test_the_bundled_parser_reads_what_pyyaml_reads_as_strings_and_refuses_the_rest())
        run(lambda: test_yaml_rejects_ambiguity())
        run(lambda: test_a_poisoned_proposal_field_is_refused_at_the_write_not_read_back_on_the_founders_screen(
            Path(td) / 'poisonprop'))
        run(lambda: test_agents_read_refuses_a_value_a_hand_edit_wrote_with_a_control_character(
            Path(td) / 'readguard'))
        run(lambda: test_a_name_that_yaml_would_not_read_as_text_is_refused_where_it_is_typed(Path(td) / 'nameguard'))
        run(lambda: test_one_unreadable_proposal_does_not_take_the_hr_listing_down(Path(td) / 'hrlist'))
        run(lambda: test_a_walled_wake_is_said_once_not_every_tick(Path(td) / 'wallonce'))
        run(lambda: test_a_rename_names_the_agents_orca_worktree(Path(td) / 'renamelabel'))
        run(lambda: test_the_relabel_recovery_command_quotes_what_it_names(Path(td) / 'relabelquote'))
        run(lambda: test_a_renamed_agents_worktree_follows_it_with_its_work_and_memory(Path(td) / 'rehome'))
        run(lambda: test_hr_rehome_cli_moves_the_folder(Path(td) / 'rehomecli'))
        run(lambda: test_agent_yaml_is_written_by_dump_so_what_was_typed_reads_back(Path(td) / 'agentyaml'))
        run(lambda: test_yaml_booleans())
        run(lambda: test_team_validation())
        run(lambda: test_team_deny_validation())
        run(lambda: test_a_vendor_with_no_driver_refuses_the_launch(Path(td) / 'novendor'))
        run(lambda: test_doctor_looks_for_the_vendors_executable_not_its_name(Path(td) / 'doctorbin'))
        run(lambda: test_a_claude_agent_is_never_placed_on_another_vendors_account(Path(td) / 'vendorpick'))
        run(lambda: test_another_vendors_account_never_spends_a_live_refresh(
            Path(td) / 'vendorrefresh'))
        run(lambda: test_a_second_vendors_live_session_is_seen_by_the_sweep(
            Path(td) / 'vendorsweep'))
        run(lambda: test_an_accounts_verdict_is_asked_for_its_own_vendor(
            Path(td) / 'vendorverdict'))
        run(lambda: test_an_unmetered_account_is_pinned_never_picked_or_polled(Path(td) / 'unmetered'))
        run(lambda: test_an_agent_is_never_launched_on_another_vendors_account(Path(td) / 'vendorlaunch'))
        run(lambda: test_an_account_records_its_vendor(Path(td) / 'vendorrecord'))
        run(lambda: test_the_opencode_driver(Path(td) / 'opencode'))
        run(lambda: test_an_opencode_session_under_ocp(Path(td) / 'ocsession'))
        run(lambda: test_an_opencode_row_takes_its_state_word_from_orca(Path(td) / 'ocrow'))
        run(lambda: test_a_launch_does_not_inherit_an_opencode_sessions_config(Path(td) / 'ocleak'))
        run(lambda: test_launch_refuses_stale(Path(td) / 'stale'))
        run(lambda: test_shared_account_context_isolation(Path(td) / 'shared'))
        run(lambda: test_agent_rewrite_uses_parser(Path(td) / 'rw'))
        run(lambda: test_init_keeps_the_lock_file_out_of_the_home_repo(Path(td) / 'd6e'))
        run(lambda: test_doctor_service_check(Path(td) / 'svc7'))
        run(lambda: test_drift(Path(td) / 'drift'))
        run(lambda: test_drift_git_gives_one_clean_entry_per_file(Path(td) / 'driftgit'))
        run(lambda: test_spawn_base_selection(Path(td) / 'spawn'))
        run(lambda: test_launch_judges_on_the_figure_it_may_act_on(Path(td) / 'lj'))
        run(lambda: test_doctor_says_one_thing_per_account(Path(td) / 'doc2'))
        run(lambda: test_doctor_agrees_with_the_pick_about_an_account_with_an_agent_mid_turn(
            Path(td) / 'docactive'))
        run(lambda: test_spawn_survives_its_own_main_path(Path(td) / 'spawnmain'))
        run(lambda: test_installing_hooks_twice_does_not_double_them(Path(td) / 'hookdup'))
        run(lambda: test_hr_add_takes_the_vendor_from_the_named_account(Path(td) / 'hrvendor'))
        run(lambda: test_register_writes_vendor_onto_an_agent_that_already_exists(
            Path(td) / 'registervendor'))
        run(lambda: test_every_door_onto_an_open_proposal_also_needs_a_decision(
            Path(td) / 'hrresolveguard'))
        run(lambda: test_the_chokepoint_alone_refuses_every_door_onto_an_open_proposal(
            Path(td) / 'hrchokepoint'))
        run(lambda: test_hr_move_changes_an_agents_account(Path(td) / 'hrmove'))
        run(lambda: test_hr_move_does_not_touch_a_live_sessions_own_record(Path(td) / 'hrmovelive'))
        run(lambda: test_hr_move_leaves_account_alone_when_liveness_cannot_be_read(Path(td) / 'hrmoveunknown'))
        run(lambda: test_agents_that_wall_together_respect_the_cap_and_a_start_is_not_its_own_rival(Path(td) / 'ar4b'))
        run(lambda: test_login_points_at_the_account_and_touches_no_credential(Path(td) / 'login'))
        run(lambda: test_doctor_says_when_the_record_and_the_process_table_disagree(Path(td) / 'docsess'))
        run(lambda: test_doctor_and_ps_report_a_drifted_launch(Path(td) / 'docdrift'))
        run(lambda: test_trust_prompt_folder_parses_a_live_capture())
        run(lambda: test_a_declared_root_gets_standing_consent(Path(td) / 'trustroot'))
        run(lambda: test_an_account_is_added_and_removed_from_ocp(Path(td) / 'acctaddrm'))
        run(lambda: test_account_add_login_preflight_names_the_actual_blocker(Path(td) / 'acctaddmsg'))
        run(lambda: test_offer_to_trust_worktrees_lists_paths_and_skips(
            Path(td) / 'offertrust'))
        run(lambda: test_a_second_name_for_one_login_is_caught(Path(td) / 'acctsame'))
        run(lambda: test_a_file_added_to_shared_reaches_every_existing_account(Path(td) / 'relink'))
        run(lambda: test_jobs_refuses_a_busy_target_and_a_full_queue(Path(td) / 'jobsbusy'))
        run(lambda: test_jobs_stops_a_hung_action_at_its_declared_bound_not_past_it(
            Path(td) / 'jobsbound'))
        run(lambda: test_jobs_kills_the_group_when_a_grandchild_outlives_the_leader(
            Path(td) / 'jobsgrandchild'))
        run(lambda: test_jobs_lines_reports_state_honestly(Path(td) / 'jobslines'))
        run(lambda: test_venv(Path(td) / 'venvhome'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
