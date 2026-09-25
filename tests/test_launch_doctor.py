"""Tests: launching, the doctor, the venv, init and jobs.

Run on its own: python3 tests/test_launch_doctor.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from ocp import hooks  # noqa: E402






# ---------------------------------------------------------------- status line
def test_init_leaves_the_status_line_slot_empty(home: Path) -> None:
    """ocp installs no status line (D11): the slot is the human's. `ocp init`
    removes the ones ocp used to install, and the settings entry pointing at
    them, and leaves anyone else's statusLine where it is."""
    use_home(home)
    orcamod.live_terminals = lambda: None
    shared = vendors.shared_dir("claude")
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
    use_home(home)
    shared = vendors.shared_dir("claude")
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
    use_home(home)
    shared = vendors.shared_dir("claude")
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
    use_home(home)
    shared = vendors.shared_dir("claude")
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

    agents.register("launchtest", model="sonnet")
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
    check("the refusal names the account that frees up first",
          "waiting for gamma's 5h window" in r.stderr, r.stderr)
    r = subprocess.run([str(ROOT / "bin" / "ocp"), "launch", "launchtest", "--force"],
                       capture_output=True, text=True, env=env)
    check("--force does not place an agent where no account has room",
          r.returncode != 0, r.stdout)

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
        dmn_evaluate._SAID.clear()
        logged: list[str] = []
        for _ in range(4):
            dmn_evaluate.supervise(con, "backend", logged.append, int(time.time()), {}, [])
        wall = [m for m in logged if "5h window" in m]
        check("wall wake: the account really is the reason, so the fixture reaches the wall",
              wall, str(logged))
        check("wall wake: it is said once across four ticks, not once per tick",
              len(wall) == 1, str(logged))
        ok, why = dmn_lifecycle.wake_agent(con, "backend", logged.append)
        check("wall wake: and the wake itself still refuses and returns the reason",
              not ok and "5h window" in why, why)
        logged.clear()
        dmn_lifecycle.wake_agent(con, "backend", logged.append)
        check("wall wake: a direct wake does not log it either - its caller has the reason",
              not [m for m in logged if "5h window" in m], str(logged))
    finally:
        orcamod.available, orcamod.alive, orcamod._run = real_avail, real_alive, real_run
        dmn_evaluate._SAID.clear()
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
    agents.register("zaitest")
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


def test_a_launch_given_a_destination_lands_there_not_where_the_pick_would(home: Path) -> None:
    """A move names its destination. The relaunch has to carry it: the pick over
    every account would send the session to the roomier one."""
    _daemon_home(home, 20)
    accounts.register("beta")
    agents.register("keeper")
    credentialed(home, "alpha", "beta")
    now = int(time.time())
    write_status(home, "alpha", pct_5h=5, pct_7d=5, reset_7d=now + 90000, reset_5h=now + 1800)
    write_status(home, "beta", pct_5h=60, pct_7d=10, reset_7d=now + 90000, reset_5h=now + 1800)
    check("dest: the pick alone chooses the roomier account",
          launch.find_room("keeper") == "alpha", str(launch.find_room("keeper")))
    env = launch.build("keeper", account="beta")[1]
    check("dest: a launch given beta starts on beta", env.get("OCP_ACCOUNT") == "beta", str(env))
    cmd = launch.command_for("keeper", account="beta")
    check("dest: the command a move relaunches with names it",
          cmd.endswith("launch keeper --account beta"), cmd)
    check("dest: an ordinary launch's command does not",
          "--account" not in launch.command_for("keeper"))
    write_status(home, "beta", pct_5h=100, pct_7d=10, reset_7d=now + 90000, reset_5h=now + 1800)
    try:
        launch.build("keeper", account="beta")
        check("dest: a destination at the wall is still refused", False, "built")
    except SystemExit as e:
        check("dest: a destination at the wall is still refused", "refusing to start" in str(e), str(e))


def test_an_agent_is_never_launched_on_another_vendors_account(home: Path) -> None:
    """The pick never offers another vendor's account, and the last line before
    the exec still refuses a resume whose recorded account is one: a session is
    the agent's vendor's binary against that account's credentials."""
    os.environ["OCP_HOME"] = str(home)
    with second_vendor():
        accounts.register("alpha")
        (paths.account_dir("alpha") / ".claude" / ".credentials.json").write_text("{}")
        accounts.register("zeta", vendor="zai")
        agents.register("backend")
        agents.register("glm", vendor="zai")
        check("pick: a claude agent is placed on the claude account",
              launch.find_room("backend") == "alpha", str(launch.find_room("backend")))
        check("pick: a zai agent is not placed on the claude account",
              launch.find_room("glm") != "alpha", str(launch.find_room("glm")))
        con = db.connect()
        try:
            db.set_account(con, "backend", "zeta")
        finally:
            con.close()
        try:
            launch.build("backend", ["--resume", "s1"])
            check("launch: a resume on the other vendor's account is refused", False, "built")
        except SystemExit as e:
            check("launch: a resume on the other vendor's account is refused",
                  "agent 'backend' is a claude agent and account 'zeta' is a zai account"
                  in str(e), str(e))
        agents.register("orphan", vendor="opencode")
        r = doctor.Report()
        doctor._check_agents(r)
        names = [n for _lv, n, _d in r.rows]
        check("doctor: an agent whose vendor has no account is named",
              "agent orphan: no opencode account is registered" in names, str(names))


def test_a_launch_does_not_inherit_an_opencode_sessions_config(home: Path) -> None:
    """#43: accounts.env_for starts from the launching process's environment,
    and an opencode session's own tools carry OPENCODE_CONFIG_CONTENT - another
    session's whole config, preamble and denials, which a claude launch would
    keep. env_for drops that one variable. An inherited OPENCODE_CONFIG_DIR
    stays, whatever the launch's vendor: Orca sets it in every terminal it
    opens for its opencode status plugin (busy/idle), and dropping it would
    strip that plugin from every relaunch. The opencode launch's own
    OPENCODE_CONFIG_CONTENT comes back from its driver's launch_env."""
    use_home(home)
    accounts.register("alpha")
    accounts.register("zai", vendor="opencode")
    agents.register("backend")
    agents.register("glm", model="zai-coding-plan/glm-5.3-flash")
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
    use_home(home)
    bindir = home / "stub"
    bindir.mkdir(parents=True, exist_ok=True)
    (bindir / "claude").write_text("#!/bin/sh\necho started\n")
    (bindir / "claude").chmod(0o755)
    agents.register("st")
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


def test_init_keeps_the_lock_file_out_of_the_home_repo(home: Path) -> None:
    """The lock file's pid changes on every daemon start; tracked, it is a
    change `git status` shows forever."""
    os.environ["OCP_HOME"] = str(home)
    with contextlib.redirect_stdout(io.StringIO()):
        clisetup.cmd_init(argparse.Namespace())
    lines = (home / ".gitignore").read_text().split()
    check("init: locks/ is ignored", "locks/" in lines, str(lines))
    check("init: and nothing names the pidfile that no longer exists",
          "daemon.pid" not in lines, str(lines))


def test_doctor_service_check(home: Path) -> None:
    use_home(home)
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
        with lock.singleton(dmn_locks.DAEMON_LOCK):
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
    use_home(home)
    agents.register("late")
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
    use_home(home)
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
    agents.register("spawny")

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


def test_spawn_keeps_the_worktree_an_agent_still_has(home: Path) -> None:
    """`worktree create --name keeper` on a name already taken makes keeper-2
    beside it, stranding the branch and whatever is uncommitted in the first.
    With the terminal record gone but the checkout on disk, spawn must start
    the agent in the checkout it has and create nothing."""
    os.environ["OCP_HOME"] = str(home)
    home.mkdir(parents=True, exist_ok=True)
    agents.register("keeper", "alpha")
    wt = home / "keeper-wt"
    wt.mkdir()

    calls: list[list[str]] = []

    def fake_run(args, cwd=None):
        calls.append(args)
        if args[:3] == ["orca", "worktree", "create"]:
            return 0, json.dumps({"result": {"worktree": {"path": "/tmp/keeper-2"}}}), ""
        if args[:3] == ["orca", "terminal", "create"]:
            return 0, json.dumps({"result": {"terminal": {"handle": "term_k"}}}), ""
        return 0, "", ""

    real = (spawnmod._run, spawnmod.shutil.which, orcamod.run_current, orcamod.run_create)
    con = db.connect()
    try:
        spawnmod.shutil.which = lambda _n: "/usr/bin/orca"
        spawnmod._run = fake_run
        orcamod.run_current = lambda: None
        orcamod.run_create = lambda objective: None
        # The selector `ocp launch` records, not a plain path.
        db.set_terminal(con, "keeper", None, f"abc123::{wt}")

        ok, msg = spawnmod.spawn("keeper", ROOT, base="main", dry_run=True)
        check("spawn: a dry run names the worktree it would keep", ok and str(wt) in msg, msg)
        check("spawn: and plans no worktree create", "worktree create" not in msg, msg)

        ok, msg = spawnmod.spawn("keeper", ROOT, base="main")
        check("spawn: an agent with a worktree and no terminal starts", ok, msg)
        check("spawn: no second worktree is created",
              not any(a[:3] == ["orca", "worktree", "create"] for a in calls), str(calls))
        check("spawn: the terminal opens in the worktree it had",
              [f"path:{wt}"] == [a[a.index("--worktree") + 1] for a in calls
                                 if a[:3] == ["orca", "terminal", "create"]], str(calls))

        # Gone from disk, there is nothing to keep: a fresh one is made.
        calls.clear()
        db.set_terminal(con, "keeper", None, f"abc123::{wt}-missing")
        ok, msg = spawnmod.spawn("keeper", ROOT, base="main")
        check("spawn: a worktree that is gone is made anew",
              any(a[:3] == ["orca", "worktree", "create"] for a in calls), str(calls))
    finally:
        con.close()
        spawnmod._run, spawnmod.shutil.which, orcamod.run_current, orcamod.run_create = real


def test_installing_hooks_twice_does_not_double_them(home: Path) -> None:
    """`ocp` is on PATH as a symlink into the repo, so one installation spells
    itself two ways. Matching the exact string added a second entry the first
    time the spelling changed, and then every hook fired twice."""
    use_home(home)
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


def test_init_installs_the_hooks_and_lets_agents_write_the_home(home: Path) -> None:
    """A fresh home needs both before an agent can run in a sandbox: ocp's state
    hooks, and write access to the whole home (SQLite makes its own files there,
    and `ask` writes agents/, locks/ and wake/), which no denyWrite may cover."""
    use_home(home)
    target = home / "shared" / ".claude" / "settings.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"sandbox": {"enabled": True, "filesystem": {
        "allowWrite": ["/elsewhere", str(home / "db.sqlite"), str(home / "locks" / "x")],
        "denyWrite": [str(home.parent), str(home), "/backups"]}}}))
    env = dict(os.environ, OCP_HOME=str(home), OCP_BIN="/usr/local/bin/ocp")
    outs = [subprocess.run([str(ROOT / "bin" / "ocp"), "init"], capture_output=True, text=True,
                           env=env).stdout for _ in range(2)]
    data = json.loads(target.read_text())
    fs = data["sandbox"]["filesystem"]
    check("init: the hooks ocp install-hooks writes are there",
          {ev for ev in data["hooks"]
           if any(" hook " in h.get("command", "") for g in data["hooks"][ev]
                  for h in g.get("hooks", []))} >= set(evmod.CAPTURED | evmod.ANSWERS),
          str(sorted(data.get("hooks", {}))))
    check("init: the whole home is writable, once, and per-file entries under it are gone",
          fs["allowWrite"] == ["/elsewhere", str(home)], str(fs))
    check("init: a denyWrite that is exactly the home is removed; a parent's and another are kept",
          fs["denyWrite"] == [str(home.parent), "/backups"], str(fs))
    check("init: and the parent's deny is named, each time, for the founder to decide",
          all(f"denyWrite '{home.parent}' holds" in o for o in outs), str(outs))
    check("init: the rest of the sandbox block is left alone", data["sandbox"]["enabled"] is True)
    r = subprocess.run([str(ROOT / "bin" / "ocp"), "hook", "install"], capture_output=True,
                       text=True, env=env, stdin=subprocess.DEVNULL, timeout=20)
    check("hook install: points to install-hooks instead of reading stdin",
          r.returncode != 0 and "ocp install-hooks" in r.stderr, r.stdout + r.stderr)


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
        printed = _capture(lambda: clidaemon.cmd_daemon_status(None))
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
        printed = _capture(lambda: clidaemon.cmd_daemon_status(None))
        check("ps: and the screen marks it the same way",
              any(l.startswith("backend") and "·drift" in l for l in printed.splitlines()),
              printed)
    finally:
        obsmod.live_sessions = real
        con.close()


def test_a_file_added_to_shared_reaches_every_existing_account(home: Path) -> None:
    """The actual gap (founder #596, orcapipe-manager #668): `register()` skips
    a LINKED item whose shared target did not exist yet, and it only runs when
    an account is created or re-registered by hand - so alpha, beta and gamma
    had no CLAUDE.md link at all, silently, because the file did not exist
    when they were made. A fixture that creates the accounts after the file
    exists would pass on the broken code; this creates them before it, the way
    they actually are today."""
    use_home(home)
    accounts.register("alpha")
    accounts.register("beta")
    accounts.register("gamma")
    alpha_claude = vendors.config_dir("alpha") / "CLAUDE.md"
    beta_claude = vendors.config_dir("beta") / "CLAUDE.md"
    gamma_claude = vendors.config_dir("gamma") / "CLAUDE.md"
    check("relink: matches today - no CLAUDE.md link exists yet",
          not alpha_claude.exists() and not beta_claude.exists() and not gamma_claude.exists())

    # A deliberate override: a real file, not a link - what a human's own fix
    # or customization looks like on disk.
    gamma_claude.write_text("gamma's own")

    # The file is added the way the founder actually will: dropped straight
    # into shared/.claude, no `ocp` command run at all afterward.
    shared_target = vendors.shared_dir("claude") / "CLAUDE.md"
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
          (vendors.config_dir("delta") / "CLAUDE.md").is_symlink())

    # And the daemon does this without being told to, every tick - the actual
    # fix for "a human has to remember a command per account": nobody has to
    # remember anything, because the daemon is already running.
    accounts.register("epsilon")
    # Simulate epsilon predating CLAUDE.md ever landing in shared, the same
    # way alpha and beta did above - unlink what register() just linked, so
    # the tick has something of its own to catch up on.
    (vendors.config_dir("epsilon") / "CLAUDE.md").unlink()
    orcamod.live_terminals = lambda: None    # tick's sweep asks Orca; this is about relink
    logs: list[str] = []
    con = db.connect()
    try:
        dmn_evaluate.tick(con, logs.append)
    finally:
        con.close()
    check("relink: the daemon's own tick catches up an account with nothing to run for it",
          (vendors.config_dir("epsilon") / "CLAUDE.md").is_symlink()
          and any("relinked epsilon" in ln for ln in logs), str(logs))


def test_jobs_refuses_a_busy_target_and_a_full_queue(home: Path) -> None:
    """`ocp/jobs.py` (task #45): a second action on a target already running one
    is refused with what is running and for how long, not queued or dropped;
    a fifth action while `MAX_JOBS` already run is refused for the same reason
    a screen has no room to show more than a few at once."""
    _daemon_home(home, 20)
    agents.register("reviewer")
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
    agents.register("reviewer")
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
    agents.register("reviewer")
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


def test_a_test_never_reaches_the_real_home(home: Path) -> None:
    """support.py stands a temp directory in for the user's home before ocp is
    imported, and paths.home() refuses the real ~/.orcapipe while it does."""
    real = paths._real_user_home() / ".orcapipe"
    check("paths: under the suite the user's home is the stand-in",
          paths.user_home() == TEST_ROOT, str(paths.user_home()))
    check("paths: the default home is inside the stand-in",
          TEST_ROOT in paths.default_home().parents, str(paths.default_home()))
    check("paths: hooks and the launchd plist resolve inside the stand-in",
          TEST_ROOT in hooks.source_settings().parents
          and TEST_ROOT in service.plist_path().parents)
    before = os.environ.get("OCP_HOME")
    try:
        for label, value in (("the real home", real), ("a folder inside it", real / "agents")):
            os.environ["OCP_HOME"] = str(value)
            try:
                paths.home()
                check(f"paths: OCP_HOME at {label} is refused under the suite", False)
            except paths.RealHomeInTest:
                check(f"paths: OCP_HOME at {label} is refused under the suite", True)
        os.environ.pop("OCP_HOME")
        check("paths: no OCP_HOME falls back to the stand-in, not the real home",
              paths.home() == TEST_ROOT / ".orcapipe", str(paths.home()))
        os.environ["OCP_HOME"] = str(home)
        check("paths: a test's own OCP_HOME is used as given", paths.home() == home)
    finally:
        if before is None:
            os.environ.pop("OCP_HOME", None)
        else:
            os.environ["OCP_HOME"] = before


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
        run(lambda: test_launch(home))
        run(lambda: test_doctor_checks_shared_mcp_config(home))
        run(lambda: test_a_walled_wake_is_said_once_not_every_tick(Path(td) / 'wallonce'))
        run(lambda: test_the_relabel_recovery_command_quotes_what_it_names(Path(td) / 'relabelquote'))
        run(lambda: test_a_vendor_with_no_driver_refuses_the_launch(Path(td) / 'novendor'))
        run(lambda: test_doctor_looks_for_the_vendors_executable_not_its_name(Path(td) / 'doctorbin'))
        run(lambda: test_a_launch_given_a_destination_lands_there_not_where_the_pick_would(Path(td) / 'destlaunch'))
        run(lambda: test_an_agent_is_never_launched_on_another_vendors_account(Path(td) / 'vendorlaunch'))
        run(lambda: test_a_launch_does_not_inherit_an_opencode_sessions_config(Path(td) / 'ocleak'))
        run(lambda: test_launch_refuses_stale(Path(td) / 'stale'))
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
        run(lambda: test_spawn_keeps_the_worktree_an_agent_still_has(Path(td) / 'spawnkeep'))
        run(lambda: test_installing_hooks_twice_does_not_double_them(Path(td) / 'hookdup'))
        run(lambda: test_init_installs_the_hooks_and_lets_agents_write_the_home(Path(td) / 'initw'))
        run(lambda: test_doctor_says_when_the_record_and_the_process_table_disagree(Path(td) / 'docsess'))
        run(lambda: test_doctor_and_ps_report_a_drifted_launch(Path(td) / 'docdrift'))
        run(lambda: test_a_file_added_to_shared_reaches_every_existing_account(Path(td) / 'relink'))
        run(lambda: test_jobs_refuses_a_busy_target_and_a_full_queue(Path(td) / 'jobsbusy'))
        run(lambda: test_jobs_stops_a_hung_action_at_its_declared_bound_not_past_it(
            Path(td) / 'jobsbound'))
        run(lambda: test_jobs_kills_the_group_when_a_grandchild_outlives_the_leader(
            Path(td) / 'jobsgrandchild'))
        run(lambda: test_jobs_lines_reports_state_honestly(Path(td) / 'jobslines'))
        run(lambda: test_venv(Path(td) / 'venvhome'))
        run(lambda: test_a_test_never_reaches_the_real_home(Path(td) / 'realhome'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
