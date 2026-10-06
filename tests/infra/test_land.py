"""Tests: tools/land.sh lands a branch without main's checkout holding a half-merged tree.

Run on its own: python3 tests/infra/test_land.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

LAND = ROOT / "tools" / "land.sh"
AUDITOR = "alpha.auditor"
ENV = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@x", GIT_COMMITTER_NAME="t",
           GIT_COMMITTER_EMAIL="t@x", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, env=ENV, timeout=30)
    assert r.returncode == 0, f"git {' '.join(args)}: {r.stderr}"
    return r.stdout.strip()


def _land(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(LAND), *args], cwd=cwd, capture_output=True, text=True, env=ENV, timeout=60)


def _repo(td: Path) -> Path:
    """A main checkout with a module and a suite that is green unless RED exists."""
    main = td / "main"
    main.mkdir(parents=True)
    _git(main, "init", "-q", "-b", "main")
    (main / "a.py").write_text("x = 1\ny = 2\n")
    (main / "tools").mkdir()
    suite = main / "tools" / "suite.sh"
    # Each run adds a line to $SUITE_RUNS when it is set, so a test can count the suite runs.
    suite.write_text('#!/usr/bin/env bash\n[ -z "${SUITE_RUNS:-}" ] || echo run >> "$SUITE_RUNS"\n'
                     '[ ! -e "$(dirname "$0")/../RED" ]\n')
    suite.chmod(0o755)
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "base")
    return main


def _branch(main: Path, name: str, filename: str, text: str, *more: tuple[str, str]) -> str:
    _git(main, "checkout", "-q", "-b", name, "main")
    for f, t in ((filename, text), *more):
        (main / f).parent.mkdir(parents=True, exist_ok=True)
        (main / f).write_text(t)
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", name)
    sha = _git(main, "rev-parse", "HEAD")
    _git(main, "checkout", "-q", "main")
    return sha


def test_a_branch_lands_by_fast_forward_and_a_conflict_never_touches_the_checkout(home: Path) -> None:
    """T390: every agent's ocp runs from main's checkout, so a merge in it that
    stops at conflict markers breaks ocp for everyone until it is resolved."""
    main = _repo(home)
    before = _git(main, "rev-parse", "HEAD")
    clean = _branch(main, "clean", "b.py", "z = 3\n")
    r = _land(main, clean, "Merge clean")
    merged = _git(main, "rev-parse", "HEAD")
    check("land: a clean branch lands and prints the merge sha", r.returncode == 0
          and r.stdout.strip() == merged and merged != before, r.stdout + r.stderr)
    check("land: it is a merge commit with the message given, and the file is in main's checkout",
          _git(main, "log", "-1", "--format=%s") == "Merge clean" and (main / "b.py").read_text() == "z = 3\n"
          and len(_git(main, "log", "-1", "--format=%P").split()) == 2)
    check("land: the scratch worktree is gone", len(_git(main, "worktree", "list").splitlines()) == 1,
          _git(main, "worktree", "list"))

    theirs = _branch(main, "clash", "a.py", "x = 1\ny = 30\n")
    (main / "a.py").write_text("x = 1\ny = 20\n")
    _git(main, "commit", "-qam", "main edits a.py")
    head, text = _git(main, "rev-parse", "HEAD"), (main / "a.py").read_bytes()
    r = _land(main, theirs, "Merge clash")
    check("land: a conflict exits 1 and names the file", r.returncode == 1 and "a.py" in r.stderr, r.stderr)
    check("land: main's checkout is exactly as it was - same commit, same bytes, no markers, no merge state",
          _git(main, "rev-parse", "HEAD") == head and (main / "a.py").read_bytes() == text
          and b"<<<<<<<" not in (main / "a.py").read_bytes() and not (main / ".git" / "MERGE_HEAD").exists()
          and _git(main, "status", "--porcelain") == "", _git(main, "status", "--porcelain"))
    check("land: and the scratch worktree is gone after a conflict too",
          len(_git(main, "worktree", "list").splitlines()) == 1)


def test_a_red_suite_or_a_dirty_checkout_lands_nothing(home: Path) -> None:
    main = _repo(home)
    red = _branch(main, "red", "RED", "")   # a branch that turns the suite red once merged
    head = _git(main, "rev-parse", "HEAD")
    r = _land(main, "--test", red, "Merge red")
    check("land: --test with a red suite exits 1 and lands nothing",
          r.returncode == 1 and "red" in r.stderr and _git(main, "rev-parse", "HEAD") == head
          and not (main / "RED").exists(), r.stderr)
    sha = _branch(main, "feat", "b.py", "z = 3\n")
    r = _land(main, "--test", sha, "Merge feat")
    check("land: --test with a green suite lands", r.returncode == 0 and (main / "b.py").exists(), r.stderr)

    other = _branch(main, "other", "c.py", "w = 4\n")
    head = _git(main, "rev-parse", "HEAD")
    (main / "b.py").write_text("dirty\n")
    _git(main, "add", "b.py")
    (main / "c.py").write_text("in the way\n")
    r = _land(main, other, "Merge other")
    check("land: a main checkout the fast-forward cannot update is refused, and left as it was",
          r.returncode == 1 and _git(main, "rev-parse", "HEAD") == head
          and (main / "c.py").read_text() == "in the way\n", r.stdout + r.stderr)


def _check(main: Path, sha: str) -> str:
    r = subprocess.run([str(LAND), "--check", sha], cwd=main, capture_output=True, text=True, env=ENV, timeout=60)
    return r.stdout.strip().splitlines()[-1] if r.returncode == 0 and r.stdout.strip() else f"rc={r.returncode} {r.stderr}"


def test_a_check_with_a_pass_skips_the_suite_only_for_the_tree_the_auditor_passed(home: Path) -> None:
    """The suite the auditor ran on <sha> is the suite of the merge when main has not moved past it
    (the merge's tree is <sha>'s own); once main moved the merge is a new tree and the suite runs.
    The suite here is red whenever RED is in the tree, so a rerun shows as a failure."""
    main = _repo(home)
    red = _branch(main, "red", "RED", "x\n")

    def check_run(*flags: str) -> subprocess.CompletedProcess:
        return subprocess.run([str(LAND), "--check", *flags, red], cwd=main, capture_output=True, text=True,
                              env=ENV, timeout=60)
    r = check_run()
    check("check: without a PASS the suite runs, and red stops it", r.returncode == 1, r.stdout + r.stderr)
    r = check_run("--passed")
    check("check: with a PASS and main not moved the suite is not rerun, and the tree is printed",
          r.returncode == 0 and r.stdout.strip() == _git(main, "rev-parse", f"{red}^{{tree}}"), r.stdout + r.stderr)
    (main / "later.py").write_text("v = 1\n")
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "main moved")
    r = check_run("--passed")
    check("check: once main moved, the merge is a new tree and the suite runs once on it",
          r.returncode == 1, r.stdout + r.stderr)


def test_a_pass_skips_the_suite_for_one_sha_and_never_for_a_train(home: Path) -> None:
    """--passed and a train together: the train's last merge holds every branch, so it is never the
    passed sha's own tree and the suite runs on it, unless what the train changes is only docs."""
    main = _repo(home)
    runs = home / "runs"
    red = _branch(main, "red", "RED", "x\n")
    other = _branch(main, "other", "b.py", "z = 3\n")

    def check_run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run([str(LAND), "--check", *args], cwd=main, capture_output=True, text=True,
                              env={**ENV, "SUITE_RUNS": str(runs)}, timeout=60)
    r = check_run("--passed", red)
    check("passed: one passed sha with main not moved skips the suite", r.returncode == 0 and _runs(runs) == 0,
          r.stdout + r.stderr)
    r = check_run("--passed", red, other)
    check("passed: a train with --passed still runs the suite on its last merge, and red stops it",
          r.returncode == 1 and _runs(runs) == 1, r.stdout + r.stderr)
    r = check_run("--passed", other, red)
    check("passed: in either order", r.returncode == 1 and _runs(runs) == 1, r.stdout + r.stderr)
    d1, d2 = _branch(main, "d1", "docs/a.md", "# a\n"), _branch(main, "d2", "docs/b.md", "# b\n")
    r = check_run("--passed", d1, d2)
    check("passed: a docs-only train runs none, and prints a tree for each",
          r.returncode == 0 and _runs(runs) == 0 and len(r.stdout.split()) == 2, r.stdout + r.stderr)


def test_the_daemon_lands_a_tested_and_passed_sha_once_and_runs_no_branch_code(home: Path) -> None:
    """The merger tests in its own sandbox (`land.sh --check`) and records the tree it saw; the auditor
    records a PASS; both only from the process of that agent's session. The daemon merges with git alone
    and fast-forwards only to the tree that was tested."""
    from handline.daemon import lands, socket_server
    from handline import vendors
    from handline.infra import protected, sessiontoken
    main = _repo(home / "repo")
    sent = []
    keep = {k: os.environ.get(k) for k in ("OCP_LAND_REPO", *ENV)}
    real = lands._tell
    os.environ["OCP_LAND_REPO"] = str(main)
    os.environ.update({k: v for k, v in ENV.items() if k.startswith("GIT_")})
    lands._tell = lambda con, to, body, log: sent.append((to, body))
    con = db.connect()
    try:
        # A suite.sh on the branch that would leave a mark if anything ran it.
        feat = _branch(main, "feat", "b.py", "z = 3\n")
        _git(main, "checkout", "-q", "feat")
        (main / "tools" / "suite.sh").write_text('#!/usr/bin/env bash\ntouch "$(dirname "$0")/../RAN"\n')
        _git(main, "commit", "-q", "-am", "suite leaves a mark")
        feat = _git(main, "rev-parse", "HEAD")
        _git(main, "checkout", "-q", "main")
        tree = _check(main, feat)
        (main / "RAN").unlink(missing_ok=True)
        check("check: the merger's test names the tree of the merge it tested", len(tree) == 40, tree)

        import subprocess
        quiet = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        sleepers = [subprocess.Popen(["sleep", "300"], **quiet) for _ in range(3)]   # stand-ins for three sessions
        p_merger, p_auditor, p_core = (s.pid for s in sleepers)
        check("session: recorded in the protected store, one process per agent",
              all(sessiontoken.record(a, p) for a, p in ((lands.MERGER, p_merger), (AUDITOR, p_auditor),
                                                         ("backend", p_core)))
              and sessiontoken.verify(lands.MERGER, p_merger))
        was = os.environ.get("OCP_AGENT")
        os.environ["OCP_AGENT"] = "backend"
        try:
            check("session: an agent's session cannot record one, so it cannot replace another agent's",
                  not sessiontoken.record(lands.MERGER, p_core) and sessiontoken.verify(lands.MERGER, p_merger))
        finally:
            os.environ.pop("OCP_AGENT") if was is None else os.environ.__setitem__("OCP_AGENT", was)
        agents.register("backend")
        launch_src = (ROOT / "handline" / "session" / "launch.py").read_text()
        check("session: no secret goes into the session's environment",
              "OCP_SESSION_TOKEN" not in launch_src and not hasattr(sessiontoken, "mint"))
        check("session: an unrelated process, pid 1 and another agent's session are not the merger's session",
              sessiontoken.verify(lands.MERGER, os.getpid()) is False
              and sessiontoken.verify(lands.MERGER, 1) is False
              and sessiontoken.verify(lands.MERGER, p_auditor) is False
              and sessiontoken.verify(lands.MERGER, None) is False)
        child = subprocess.Popen(["sh", "-c", "sleep 300 >/dev/null 2>&1 & echo $!; wait"],
                                 stdout=subprocess.PIPE, text=True)
        grand = child.stdout.readline().strip()
        sessiontoken.record("backend.parent", child.pid)
        check("session: a grandchild of the recorded process counts",
              grand.isdigit() and sessiontoken.verify("backend.parent", int(grand)))
        child.kill()
        child.stdout.close()
        os.kill(int(grand), 9)
        check("session: a recorded pid with another start time (a reused pid) is refused",
              (protected.put(sessiontoken.NS, "backend.stale", f"{p_core} Thu Jan  1 00:00:00 1970"),
               not sessiontoken.verify("backend.stale", p_core))[1])

        from handline.daemon import landauth

        def ask_daemon(verb, agent, peer, vouched=True, **kw):
            if vouched:
                landauth.grant(agent, verb, feat)
            return socket_server._handle_request({"verb": verb, "sha": feat, "message": "m", "tree": tree,
                                                  "agent": agent, **kw}, lambda m: None, peer)
        said = ask_daemon("land", lands.MERGER, os.getpid())
        check("land: a process that only names itself the merger (any cmdline, any OCP_AGENT) is refused",
              not said["ok"] and "not this request's session" in said["said"], str(said))
        said = ask_daemon("land", lands.MERGER, p_core)
        check("land: another agent's session cannot land as the merger", not said["ok"], str(said))
        said = ask_daemon("land_pass", AUDITOR, p_merger)
        check("land: the merger's session cannot record the auditor's PASS", not said["ok"], str(said))
        import socket
        a, b = socket.socketpair()
        check("peer: the kernel names the process on the other end of a unix socket",
              sessiontoken.peer_pid(a) == os.getpid(), str(sessiontoken.peer_pid(a)))
        a.close(); b.close()

        def over_the_wire(agent):
            """One request through the daemon's own connection handler; the peer is this process."""
            landauth.grant(agent, "land", feat)
            client, server = socket.socketpair()
            client.sendall(json.dumps({"verb": "land", "sha": feat, "message": "m", "tree": tree,
                                       "agent": agent}).encode() + b"\n")
            socket_server._serve_one(server, lambda m: None)
            return json.loads(client.recv(65536).decode())
        sessiontoken.record("backend", os.getpid())
        said = over_the_wire("backend")
        check("peer: a request is judged by the process that sent it (this one is backend's session)",
              "may not land" in said["said"], str(said))
        said = over_the_wire(lands.MERGER)
        check("peer: the same process naming itself the merger is refused",
              "not this request's session" in said["said"], str(said))
        import time
        landauth._grants.clear()            # grants an earlier refusal left unspent
        said = ask_daemon("land_pass", AUDITOR, p_auditor, vouched=False)
        check("vouch: the auditor's own session, but nothing vouched for the command, is refused",
              not said["ok"] and "own `hl land` command" in said["said"], str(said))
        landauth.grant(AUDITOR, "land_pass", feat)
        first = ask_daemon("land_pass", AUDITOR, p_auditor, vouched=False)
        again = ask_daemon("land_pass", AUDITOR, p_auditor, vouched=False)
        check("vouch: a vouched request is accepted once", first["ok"] and not again["ok"], f"{first} {again}")
        landauth.grant(AUDITOR, "land_pass", "0" * 40)
        said = ask_daemon("land_pass", AUDITOR, p_auditor, vouched=False)
        check("vouch: it covers the sha in the command and no other", not said["ok"], str(said))
        landauth.grant(AUDITOR, "land_pass", feat, now=time.time() - 3600)
        said = ask_daemon("land_pass", AUDITOR, p_auditor, vouched=False)
        check("vouch: it expires", not said["ok"], str(said))
        landauth.grant(AUDITOR, "land", feat)
        said = ask_daemon("land_pass", AUDITOR, p_auditor, vouched=False)
        check("vouch: and covers the verb in the command, not the other land verb", not said["ok"], str(said))
        landauth.take(AUDITOR, "land", feat)

        # A branch's test run is a child of the session; only the hook, which holds the key, can vouch.
        test_run = subprocess.Popen(["sleep", "300"], **quiet)
        sleepers.append(test_run)
        key_set = sessiontoken.mint_key(AUDITOR)
        key = sessiontoken.key_held(AUDITOR)
        check("vouch: the hook's key is minted into the protected store", key_set and key and len(key) > 20)
        authorize = {"verb": "land_authorize", "agent": AUDITOR, "land_verb": "land_pass", "sha": feat}
        for label, extra in (("no key", {}), ("a wrong key", {"key": "x" * 43}), ("a key of another agent",
                              {"key": sessiontoken.key_held("backend") or "y"})):
            said = socket_server._handle_request({**authorize, **extra}, lambda m: None)
            check(f"vouch: land_authorize with {label} vouches for nothing", not said["ok"], str(said))
        said = ask_daemon("land_pass", AUDITOR, p_auditor, vouched=False)
        check("vouch: so a land_pass from the session's own process is still refused", not said["ok"], str(said))
        logged: list[str] = []
        said = socket_server._handle_request({**authorize, "key": key}, logged.append)
        check("vouch: the right key vouches", said["ok"], str(said))
        check("vouch: and the daemon's log says who was vouched for, so a refusal later can be told from a vouch never sent",
              any(AUDITOR in m and "vouched for land_pass" in m for m in logged), str(logged))
        said = ask_daemon("land_pass", AUDITOR, p_auditor, vouched=False)
        check("vouch: and then the session's land_pass is accepted", said["ok"], str(said))

        said = ask_daemon("land", "backend", p_core)
        check("land: an agent with its own valid session that is not the merger is refused",
              not said["ok"] and "may not land" in said["said"], str(said))
        said = ask_daemon("land_pass", AUDITOR, p_auditor)
        check("land: the auditor's own session records a PASS", said["ok"], str(said))
        other = subprocess.Popen(["sleep", "300"], **quiet)
        sleepers.append(other)
        sessiontoken.record("beta.auditor", other.pid)
        said = ask_daemon("land_pass", "beta.auditor", other.pid)
        check("land: another team's auditor, in its own session, records a PASS too", said["ok"], str(said))
        said = ask_daemon("land_pass", "beta.auditor", p_core)
        check("land: and only from its own session", not said["ok"], str(said))
        con.execute("DELETE FROM land_passes")

        ok, said = lands.request(con, feat, "Merge feat", tree, lands.MERGER)
        check("land: no PASS, no land", not ok and "no auditor PASS" in said, said)
        other = _branch(main, "other", "o.py", "o = 1\n")
        lands.record_pass(con, other, AUDITOR)
        ok, said = lands.request(con, feat, "Merge feat", tree, lands.MERGER)
        check("land: a PASS for another sha does not count", not ok and "no auditor PASS" in said, said)
        ok, said = lands.record_pass(con, feat, AUDITOR)
        ok, said = lands.request(con, feat, "Merge feat", tree, lands.MERGER)
        check("land: a passed, tested sha is queued", ok and "queued" in said, said)
        ok, said = lands.request(con, feat, "again", tree, lands.MERGER)
        check("land: the same sha while queued is refused", not ok and "already queued" in said, said)
        lands.sweep(con, lambda m: None)
        lands._worker["thread"].join(60)
        row = con.execute("SELECT * FROM lands WHERE sha = ?", (feat,)).fetchone()
        check("land: the daemon fast-forwards main to the tested tree",
              row["state"] == "landed" and row["merged"] == _git(main, "rev-parse", "HEAD")
              and _git(main, "rev-parse", "HEAD^{tree}") == tree, str(dict(row)))
        check("land: the daemon ran nothing the branch carries", not (main / "RAN").exists())
        check("land: the merger is told", sent and sent[-1][0] == lands.MERGER and "is on main" in sent[-1][1], str(sent))
        ok, said = lands.request(con, feat, "again", tree, lands.MERGER)
        check("land: a landed sha is refused", not ok and "landed as" in said, said)

        # Main moves after the merger's test: the daemon's merge is a different tree, so nothing lands.
        late = _branch(main, "late", "l.py", "l = 1\n")
        lands.record_pass(con, late, AUDITOR)
        stale = _check(main, late)
        mover = _branch(main, "mover", "m.py", "m = 1\n")
        _git(main, "merge", "-q", "--ff-only", mover)
        head = _git(main, "rev-parse", "HEAD")
        lands.request(con, late, "Merge late", stale, lands.MERGER)
        lands.sweep(con, lambda m: None)
        lands._worker["thread"].join(60)
        row = con.execute("SELECT state, output FROM lands WHERE sha = ?", (late,)).fetchone()
        check("land: a merge that is not the tree tested lands nothing, main untouched",
              row["state"] == "failed" and "test again" in row["output"] and _git(main, "rev-parse", "HEAD") == head,
              str(dict(row)))
        ok, said = lands.request(con, late, "retry", _check(main, late), lands.MERGER)
        check("land: a failed sha may be recorded again", ok, said)
    finally:
        con.close()
        lands._tell = real
        for sl in locals().get("sleepers", []):
            sl.kill()
        for k, v in keep.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def test_personal_data_or_a_local_id_stops_a_check_and_a_merge_message(home: Path) -> None:
    """A branch whose diff adds an email fails the merger's `--check`, and the daemon refuses a merge
    message carrying a task id, though the branch itself is clean."""
    import shutil
    from handline.daemon import lands
    main = _repo(home / "repo")
    shutil.copy(ROOT / "tools" / "localrefs.py", main / "tools" / "localrefs.py")
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "localrefs")
    dirty = _branch(main, "dirty", "d.py", "owner = 'someone@" + "gmail.com'\n")
    clean = _branch(main, "clean", "c.py", "z = 3\n")
    r = subprocess.run([str(LAND), "--check", dirty], cwd=main, capture_output=True, text=True, env=ENV, timeout=60)
    check("check: a diff that adds an email fails the merger's test and prints no tree",
          r.returncode == 1 and "email" in r.stderr and not r.stdout.strip(), r.stdout + r.stderr)
    check("check: a clean branch still passes it", len(_check(main, clean)) == 40, _check(main, clean))

    keep = os.environ.get("OCP_LAND_REPO")
    os.environ["OCP_LAND_REPO"] = str(main)
    con = db.connect()
    try:
        lands.record_pass(con, clean, AUDITOR)
        tree = _check(main, clean)
        for bad in ("Merge clean for T" + "1234", "#" + "1234 is why", "mail me at someone@" + "gmail.com"):
            ok, said = lands.request(con, clean, bad, tree, lands.MERGER)
            check(f"land: the merge message {bad[:24]!r} is refused", not ok and "personal data or a local id" in said, said)
        real_run = lands.subprocess.run

        def hung(*a, **kw):
            raise subprocess.TimeoutExpired("localrefs", 30)
        lands.subprocess.run = hung
        try:
            ok, said = lands.request(con, clean, "Merge clean: states what changed", tree, lands.MERGER)
        finally:
            lands.subprocess.run = real_run
        check("land: a checker that hangs is a refusal to try again, not an exception",
              not ok and "could not be checked" in said, said)
        ok, said = lands.request(con, clean, "Merge clean: states what changed", tree, lands.MERGER)
        check("land: a message in words is queued", ok and "queued" in said, said)
    finally:
        con.close()
        os.environ.pop("OCP_LAND_REPO") if keep is None else os.environ.__setitem__("OCP_LAND_REPO", keep)


def test_the_hook_vouches_only_for_a_command_that_is_nothing_but_one_land_send(home: Path) -> None:
    from handline.session import landgate
    from handline.daemon import client as dmn_client
    from handline.infra import sessiontoken
    tree, sha = "a" * 40, "abc1234"
    ok = {
        f"hl land --pass {sha}": ("land_pass", sha),
        f"bin/ocp land --pass {sha}": ("land_pass", sha),
        f"handline land {sha} 'Merge feat (x & y)' --tree {tree}": ("land", sha),
        f"hl land {sha} \"Merge feat\" --tree {tree}": ("land", sha),
        f"hl land --tree {tree} {sha} msg": ("land", sha),
        "hl land --pass abc#x": ("land_pass", "abc#x"),
    }
    no = [f"hl land --check {sha}", "hl land --list", f"hl land {sha} msg", f"hl land --pass",
          f"hl land --pass {sha} && tools/suite.sh", f"tools/suite.sh; hl land --pass {sha}",
          f"hl land --pass {sha} | cat", f"hl land --pass $(cat x)", f"hl land --pass `cat x`",
          f"hl land {sha} \"$(cat x)\" --tree {tree}", f"hl land --pass {sha}\nrm x",
          f"python3 tests/x.py hl land --pass {sha}", f"echo hl land --pass {sha}", f"hl land --pass a b",
          f"hl land --tree {tree} {sha} #x", f"hl land --pass {sha} #x", f"hl land --pass {sha} # note", "hl land #c --pass abc1234",
          f"hl land --bogus {sha}", f"hl land --pass {sha} > out", "hl land"]
    for cmd, want in ok.items():
        check(f"gate: vouches for {cmd[:50]!r}", landgate.parse("Bash", {"command": cmd}) == want,
              str(landgate.parse("Bash", {"command": cmd})))
    for cmd in no:
        check(f"gate: vouches for nothing in {cmd[:50]!r}", landgate.parse("Bash", {"command": cmd}) is None,
              str(landgate.parse("Bash", {"command": cmd})))
    check("gate: and for no tool but Bash", landgate.parse("Write", {"command": f"hl land --pass {sha}"}) is None)
    sent, real = [], dmn_client.send
    dmn_client.send = lambda payload, timeout=0: (sent.append(payload), {"ok": True})[1]
    try:
        check("gate: with no key (the sandbox's view) it sends nothing",
              not landgate.authorize("backend", "Bash", {"command": f"hl land --pass {sha}"}) and not sent)
        sessiontoken.mint_key("backend")
        done = landgate.authorize("backend", "Bash", {"command": f"hl land --pass {sha}"})
        check("gate: with the key it sends the agent, verb, sha and key",
              done and sent and sent[0]["land_verb"] == "land_pass" and sent[0]["sha"] == sha
              and sent[0]["key"] == sessiontoken.key_held("backend") and sent[0]["agent"] == "backend", str(sent))
        sent.clear()
        check("gate: a command that is not a land sends nothing",
              not landgate.authorize("backend", "Bash", {"command": "tools/suite.sh"}) and not sent)
    finally:
        dmn_client.send = real


def test_ocp_land_checks_and_sends_in_two_steps(home: Path) -> None:
    import argparse
    import contextlib
    import io
    from handline.cli import land as cli_land
    sent, real_tree, real_send = [], cli_land._tested_trees, cli_land.dmn_client.send
    cli_land._tested_trees = lambda shas: ["t" * 40]
    cli_land.dmn_client.send = lambda payload, timeout=0: (sent.append(payload), {"ok": True, "said": "ok"})[1]

    def run_land(**kw):
        args = argparse.Namespace(**{"sha": None, "message": None, "passed": False, "list": False,
                                     "check": None, "tree": None, **kw})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli_land.cmd_land(args)
        return rc, out.getvalue()
    try:
        rc, out = run_land(check=["abc1234"])
        check("land cli: --check prints the tree and sends nothing", rc == 0 and out.strip() == "t" * 40 and not sent, out)
        rc, out = run_land(sha="abc1234", message="m")
        check("land cli: a land without the tree sends nothing and says how", rc == 1 and "--check" in out and not sent, out)
        rc, out = run_land(sha="abc1234", message="m", tree="t" * 40)
        check("land cli: with the tree it sends the land and runs no test",
              rc == 0 and sent == [{"verb": "land", "sha": "abc1234", "message": "m", "tree": "t" * 40,
                                    "agent": sent[0]["agent"]}], str(sent))
    finally:
        cli_land._tested_trees, cli_land.dmn_client.send = real_tree, real_send


def test_the_pretooluse_hook_vouches_for_the_session_s_own_land_command(home: Path) -> None:
    import contextlib
    import io
    from handline.daemon import client as dmn_client
    from handline.infra import sessiontoken
    from handline.session import events
    sessiontoken.mint_key("backend")
    sent, real, was, was_in = [], dmn_client.send, os.environ.get("OCP_AGENT"), sys.stdin
    dmn_client.send = lambda payload, timeout=0: (sent.append(payload), {"ok": True})[1]
    os.environ["OCP_AGENT"] = "backend"
    try:
        for command in ("tools/suite.sh", "hl land --pass abc1234"):
            sys.stdin = io.StringIO(json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Bash",
                                                "tool_input": {"command": command}}))
            with contextlib.redirect_stdout(io.StringIO()):
                events.record_from_stdin(["PreToolUse"])
        check("hook: of two Bash calls, only the land command is vouched for",
              len(sent) == 1 and sent[0]["verb"] == "land_authorize" and sent[0]["sha"] == "abc1234", str(sent))
    finally:
        dmn_client.send, sys.stdin = real, was_in
        os.environ.pop("OCP_AGENT") if was is None else os.environ.__setitem__("OCP_AGENT", was)


def test_a_pass_comes_from_the_auditor_of_any_team_and_from_no_one_else(home: Path) -> None:
    from handline.daemon import lands
    main = _repo(home / "repo")
    keep = os.environ.get("OCP_LAND_REPO")
    os.environ["OCP_LAND_REPO"] = str(main)
    con = db.connect()
    try:
        sha = _git(main, "rev-parse", "HEAD")
        for who in ("alpha.auditor", "beta.auditor", "gamma.sea.auditor"):
            ok, said = lands.record_pass(con, sha, who)
            check(f"pass: {who} records one", ok, said)
        for who in ("alpha.merger", "alpha.core", "auditor", "alpha.auditors", "alpha.auditor.helper",
                    "alpha.Auditor", "", None, "human"):
            ok, said = lands.record_pass(con, sha, who)
            check(f"pass: {who!r} may not", not ok and "may not record a PASS" in said, said)
        check("pass: the one who recorded it is kept",
              con.execute("SELECT by FROM land_passes WHERE sha = ?", (sha,)).fetchone()["by"] == "gamma.sea.auditor")
    finally:
        con.close()
        os.environ.pop("OCP_LAND_REPO") if keep is None else os.environ.__setitem__("OCP_LAND_REPO", keep)


def _checked(main: Path, shas: list[str], runs: Path) -> list[str] | None:
    """`land.sh --check` on `shas`, counting suite runs in `runs`: the trees it printed, or None."""
    r = subprocess.run([str(LAND), "--check", *shas], cwd=main, capture_output=True, text=True,
                       env={**ENV, "SUITE_RUNS": str(runs)}, timeout=60)
    out = r.stdout.split()
    return out if r.returncode == 0 and len(out) == len(shas) else None


def _runs(runs: Path) -> int:
    n = len(runs.read_text().splitlines()) if runs.exists() else 0
    runs.unlink(missing_ok=True)
    return n


def test_a_documentation_only_merge_runs_no_suite(home: Path) -> None:
    main = _repo(home / "repo")
    runs = home / "runs"
    docs = _branch(main, "docs", "docs/guide.md", "# guide\n", ("README.md", "read me\n"), ("design/a.png", "png"))
    trees = _checked(main, [docs], runs)
    check("check: a branch that changes only docs/, design/ and .md files is tested without the suite",
          trees is not None and _runs(runs) == 0, str(trees))
    mixed = _branch(main, "mixed", "docs/other.md", "# other\n", ("b.py", "z = 3\n"))
    trees = _checked(main, [mixed], runs)
    check("check: code next to a doc still runs the suite", trees is not None and _runs(runs) == 1, str(trees))
    for path in ("contract/protocol.md", "skill/SKILL.md"):
        sha = _branch(main, path.split("/")[0], path, "# x\n")
        trees = _checked(main, [sha], runs)
        check(f"check: a .md the tests or the product read ({path}) runs the suite",
              trees is not None and _runs(runs) == 1, str(trees))
    red_doc = _branch(main, "reddoc", "docs/x.md", "# x\n", ("RED", ""))
    check("check: a doc next to anything else is tested, and a red suite still fails it",
          _checked(main, [red_doc], runs) is None and _runs(runs) == 1)


def _land_in_order(con, main: Path, kept: list[tuple[str, str]]) -> list:
    """What the merger does with a train's output: one `hl land` per sha, in order; the daemon lands them."""
    from handline.daemon import lands
    rows = []
    for sha, tree in kept:
        lands.record_pass(con, sha, AUDITOR)
        ok, said = lands.request(con, sha, f"Merge {sha[:8]}", tree, lands.MERGER)
        check(f"train: {sha[:8]} is queued", ok, said)
    for sha, _tree in kept:
        lands.sweep(con, lambda m: None)
        lands._worker["thread"].join(60)
        rows.append(con.execute("SELECT state FROM lands WHERE sha = ?", (sha,)).fetchone()["state"])
    return rows


def test_a_train_is_tested_once_and_lands_in_order_and_a_bad_branch_is_dropped(home: Path) -> None:
    from handline.cli import land as cli_land
    from handline.daemon import lands
    main = _repo(home / "repo")
    runs = home / "runs"
    keep = {k: os.environ.get(k) for k in ("OCP_LAND_REPO", *ENV)}
    real_tell = lands._tell
    os.environ["OCP_LAND_REPO"] = str(main)
    os.environ.update({k: v for k, v in ENV.items() if k.startswith("GIT_")})
    lands._tell = lambda con, to, body, log: None
    con = db.connect()
    con.execute("DELETE FROM lands")        # rows earlier tests queued for their own repos
    try:
        a, b, c = (_branch(main, n, f"{n}.py", f"{n} = 1\n") for n in ("a", "b", "c"))
        kept, dropped = cli_land.train([a, b, c], check=lambda shas: _checked(main, shas, runs))
        check("train: three green branches are tested together with one suite run",
              [s for s, _ in kept] == [a, b, c] and not dropped and _runs(runs) == 1, f"{kept} {dropped}")
        states = _land_in_order(con, main, kept)
        check("train: the daemon lands all three, each to the tree the one check saw",
              states == ["landed"] * 3 and _git(main, "rev-parse", "HEAD^{tree}") == kept[-1][1], str(states))

        d, bad, e = (_branch(main, n, f"{n}.py", f"{n} = 1\n") for n in ("d", "bad", "e"))
        _git(main, "checkout", "-q", "bad")
        (main / "RED").write_text("")
        _git(main, "add", "-A")
        _git(main, "commit", "-q", "-m", "turns the suite red")
        bad = _git(main, "rev-parse", "HEAD")
        _git(main, "checkout", "-q", "main")
        kept, dropped = cli_land.train([d, bad, e], check=lambda shas: _checked(main, shas, runs))
        check("train: a red batch is split, the branch that breaks it is dropped and the others kept in order",
              [s for s, _ in kept] == [d, e] and dropped == [bad], f"{kept} {dropped}")
        states = _land_in_order(con, main, kept)
        check("train: the two land and the dropped one never reaches main",
              states == ["landed"] * 2 and not (main / "RED").exists()
              and _git(main, "rev-parse", "HEAD^{tree}") == kept[-1][1], str(states))
        rc = subprocess.run(["git", "merge-base", "--is-ancestor", bad, "main"], cwd=main, env=ENV).returncode
        check("train: and no land of it was asked for", rc == 1
              and con.execute("SELECT 1 FROM lands WHERE sha = ?", (bad,)).fetchone() is None)
    finally:
        con.close()
        lands._tell = real_tell
        for k, v in keep.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def main() -> int:
    def tests(td):
        run(lambda: test_a_branch_lands_by_fast_forward_and_a_conflict_never_touches_the_checkout(Path(td) / "l1"))
        run(lambda: test_a_red_suite_or_a_dirty_checkout_lands_nothing(Path(td) / "l2"))
        run(lambda: test_a_check_with_a_pass_skips_the_suite_only_for_the_tree_the_auditor_passed(Path(td) / "l2b"))
        run(lambda: test_a_pass_skips_the_suite_for_one_sha_and_never_for_a_train(Path(td) / "l2c"))
        run(lambda: test_the_daemon_lands_a_tested_and_passed_sha_once_and_runs_no_branch_code(Path(td) / "l3"))
        run(lambda: test_a_pass_comes_from_the_auditor_of_any_team_and_from_no_one_else(Path(td) / "l3a"))
        run(lambda: test_personal_data_or_a_local_id_stops_a_check_and_a_merge_message(Path(td) / "l4"))
        run(lambda: test_the_hook_vouches_only_for_a_command_that_is_nothing_but_one_land_send(Path(td) / "l4"))
        run(lambda: test_ocp_land_checks_and_sends_in_two_steps(Path(td) / "l5"))
        run(lambda: test_a_documentation_only_merge_runs_no_suite(Path(td) / "l7"))
        run(lambda: test_a_train_is_tested_once_and_lands_in_order_and_a_bad_branch_is_dropped(Path(td) / "l8"))
        run(lambda: test_the_pretooluse_hook_vouches_for_the_session_s_own_land_command(Path(td) / "l6"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
