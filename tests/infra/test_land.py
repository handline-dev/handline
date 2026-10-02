"""Tests: tools/land.sh lands a branch without main's checkout holding a half-merged tree.

Run on its own: python3 tests/infra/test_land.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

LAND = ROOT / "tools" / "land.sh"
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
    suite.write_text('#!/usr/bin/env bash\n[ ! -e "$(dirname "$0")/../RED" ]\n')
    suite.chmod(0o755)
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "base")
    return main


def _branch(main: Path, name: str, filename: str, text: str) -> str:
    _git(main, "checkout", "-q", "-b", name, "main")
    (main / filename).write_text(text)
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


def test_the_daemon_lands_a_tested_and_passed_sha_once_and_runs_no_branch_code(home: Path) -> None:
    """The merger tests in its own sandbox (`land.sh --check`) and records the tree it saw; the auditor
    records a PASS; both only from the process of that agent's session. The daemon merges with git alone
    and fast-forwards only to the tree that was tested."""
    from ocp.daemon import lands, peer, socket_server
    from ocp.session import observe
    main = _repo(home / "repo")
    sent = []
    keep = {k: os.environ.get(k) for k in ("OCP_LAND_REPO", *ENV)}
    real = (lands._tell, observe.live_sessions)
    os.environ["OCP_LAND_REPO"] = str(main)
    os.environ.update({k: v for k, v in ENV.items() if k.startswith("GIT_")})
    lands._tell = lambda con, to, body, log: sent.append((to, body))
    con = db.connect()
    child = subprocess.Popen(["sleep", "30"])
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

        import socket
        a, b = socket.socketpair(socket.AF_UNIX)
        check("peer: the socket gives the caller's real pid", peer.peer_pid(a) == os.getpid(), str(peer.peer_pid(a)))
        a.close(), b.close()
        check("peer: a process under a session is that session's agent; one outside none is nobody",
              peer.agent_of(child.pid, {lands.MERGER: os.getpid()}) == lands.MERGER
              and peer.agent_of(child.pid, {lands.MERGER: 1}) is None
              and peer.agent_of(None, {lands.MERGER: os.getpid()}) is None)

        observe.live_sessions = lambda: {"orcapipe.core": os.getpid()}
        said = socket_server._handle_request({"verb": "land", "sha": feat, "message": "m", "tree": tree,
                                              "speaker": lands.MERGER, "caller": lands.MERGER},
                                             lambda m: None, child.pid)
        check("land: an agent that names itself the merger is refused", not said["ok"] and "may not land" in said["said"],
              str(said))
        said = socket_server._handle_request({"verb": "land_pass", "sha": feat, "speaker": lands.AUDITOR},
                                             lambda m: None, child.pid)
        check("land: an agent that names itself the auditor cannot record a PASS", not said["ok"], str(said))
        said = socket_server._handle_request({"verb": "land", "sha": feat, "message": "m", "tree": tree},
                                             lambda m: None, None)
        check("land: a caller that is no session is refused", not said["ok"], str(said))

        ok, said = lands.request(con, feat, "Merge feat", tree, lands.MERGER)
        check("land: no PASS, no land", not ok and "no auditor PASS" in said, said)
        other = _branch(main, "other", "o.py", "o = 1\n")
        lands.record_pass(con, other, lands.AUDITOR)
        ok, said = lands.request(con, feat, "Merge feat", tree, lands.MERGER)
        check("land: a PASS for another sha does not count", not ok and "no auditor PASS" in said, said)
        ok, said = lands.record_pass(con, feat, lands.AUDITOR)
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
        lands.record_pass(con, late, lands.AUDITOR)
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
        child.kill()
        con.close()
        lands._tell, observe.live_sessions = real
        for k, v in keep.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def main() -> int:
    def tests(td):
        run(lambda: test_a_branch_lands_by_fast_forward_and_a_conflict_never_touches_the_checkout(Path(td) / "l1"))
        run(lambda: test_a_red_suite_or_a_dirty_checkout_lands_nothing(Path(td) / "l2"))
        run(lambda: test_the_daemon_lands_a_tested_and_passed_sha_once_and_runs_no_branch_code(Path(td) / "l3"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
