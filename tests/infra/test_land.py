"""Tests: tools/land.sh lands a branch without main's checkout holding a half-merged tree.

Run on its own: python3 tests/infra/test_land.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import os
import shutil
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
    """A main checkout with a module, the real tools/check.sh, and a suite, a Go suite and a test.sh, each green
    unless its own mark exists: RED, go/RED and go/PARITY_RED."""
    main = td / "main"
    main.mkdir(parents=True)
    _git(main, "init", "-q", "-b", "main")
    (main / "a.py").write_text("x = 1\ny = 2\n")
    (main / "tools").mkdir()
    shutil.copy(ROOT / "tools" / "check.sh", main / "tools" / "check.sh")
    # Each run adds a line to $SUITE_RUNS ($GO_RUNS for the Go suite and test.sh) when it is set, so a test can
    # count the runs of each. The Go suite's line says whether it was asked for -race.
    # test.sh is red on its mark for the first Go-only file alone, so a run that went on past a red file to a green
    # last one would read green.
    for name, mark, runs, green in (
            ("suite.sh", "run", "SUITE_RUNS", '[ ! -e "$top/RED" ]'),
            ("test-go.sh", 'go${HANDLINE_GO_RACE:+ -race}', "GO_RUNS", '[ ! -e "$top/go/RED" ]'),
            ("test.sh", 'test.sh "$*"', "GO_RUNS", '[ ! -e "$top/go/PARITY_RED" ] || [ "$1" != tests/daemon/test_go_reads.py ]')):
        stub = main / "tools" / name
        stub.write_text(f'#!/usr/bin/env bash\ntop="$(dirname "$0")/.."\n'
                        f'[ -z "${{{runs}:-}}" ] || echo {mark} >> "${runs}"\n{green}\n')
        stub.chmod(0o755)
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


def test_a_check_runs_the_python_suite_only_when_the_merge_changes_more_than_go_and_the_apps(home: Path) -> None:
    """The merged tree's tools/check.sh picks the tests from what the merge changes: an apps/ or a go/ change, or one to a
    tool only the Go checks use, runs the Go suite and the Python files that test only Go, and no Python logic tests;
    anything else runs the whole suite."""
    main = _repo(home)
    runs, go_runs = home / "runs", home / "go-runs"

    def check_run(*shas: str) -> subprocess.CompletedProcess:
        return subprocess.run([str(LAND), "--check", *shas], cwd=main, capture_output=True, text=True,
                              env={**ENV, "SUITE_RUNS": str(runs), "GO_RUNS": str(go_runs)}, timeout=60)

    def ran(path: Path) -> list[str]:
        out = path.read_text().splitlines() if path.exists() else []
        path.unlink(missing_ok=True)
        return out
    gos = ["go -race", "test.sh tests/daemon/test_go_reads.py", "test.sh tests/daemon/test_go_hooks.py",
           "test.sh tests/infra/test_go_schema.py", "test.sh tests/infra/test_go_tidy.py"]
    for name, files, python, go in (
            ("apps/mobile", [("apps/mobile/app.tsx", "x\n")], [], gos),
            ("go/", [("go/x.go", "package x\n")], [], gos),
            ("go/ and the apps' docs", [("go/y.go", "package y\n"), ("apps/README.md", "# r\n")], [], gos),
            ("go/ and a tool only the Go checks use", [("go/w.go", "package w\n"), ("tools/conformance_fds.py", "x = 1\n")], [], gos),
            ("Python and Go", [("handline/m.py", "m = 1\n"), ("go/z.go", "package z\n")], ["run"], []),
            ("tests/", [("tests/t.py", "t = 1\n")], ["run"], []),
            ("tools/", [("tools/land2.sh", "true\n")], ["run"], [])):
        sha = _branch(main, name.replace("/", "-").replace(" ", "-").replace("'", ""), *files[0], *files[1:])
        r = check_run(sha)
        check(f"check: a change to {name} runs {python or 'no Python suite'} and {go or 'nothing more'}",
              r.returncode == 0 and ran(runs) == python and ran(go_runs) == go, r.stdout + r.stderr)
    for name, mark in (("Go suite", "go/RED"), ("Go-only Python file", "go/PARITY_RED")):
        red = _branch(main, mark.replace("/", "-"), "go/red.go", "package r\n", (mark, "x\n"))
        r = check_run(red)
        check(f"check: a red {name} alone stops a go/-only merge", r.returncode == 1, r.stdout + r.stderr)


def test_a_housekeeping_fix_never_reaches_agent_instructions(home: Path) -> None:
    """A fix lands with no review, so it may not touch what every agent session reads as instructions, whatever
    the list says, and a glob's `*` stays inside one path component."""
    from handline.infra import housekeeping
    globs = housekeeping.read_globs((ROOT / "tools" / "housekeeping.paths").read_text())
    for path in ("CLAUDE.md", "handline/CLAUDE.md", "docs/claude.md", "CLAUDE.local.md", "AGENTS.md",
                 "docs/AGENTS.md", ".claude/agents/x.md", ".claude/commands/y.md", "docs/.claude/z.md",
                 "skill/SKILL.md", "skill/README.md"):
        check(f"instructions: {path} needs a PASS even if a glob allowed it",
              "instructions" in (housekeeping.refusal(path, globs + ["**/*.md", "**"]) or ""))
    for path in ("x.md", "handline/x.md", "go/sub/conformance-x.txt", "docs/x.py", "docs//x.md", "./README.md",
                 "docs/../x.md", "/README.md"):
        check(f"paths: {path} is not housekeeping", housekeeping.refusal(path, globs) is not None)
    for path in ("README.md", "apps/web/README.md", "docs/x.md", "docs/a/b.md", "design/02-team.md",
                 "go/conformance-missing.txt"):
        check(f"paths: {path} is housekeeping", housekeeping.refusal(path, globs) is None,
              str(housekeeping.refusal(path, globs)))
    check("glob: * does not cross a /", not housekeeping.matches("a/b.md", "*.md"))
    check("glob: ** is any number of whole components",
          housekeeping.matches("README.md", "**/README.md") and housekeeping.matches("a/b/README.md", "**/README.md")
          and not housekeeping.matches("a/xREADME.md", "**/README.md"))


def test_personal_data_or_a_local_id_stops_a_check(home: Path) -> None:
    """A branch whose diff adds an email fails the merger's `--check`."""
    import shutil
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


def test_the_hook_vouches_only_for_a_command_that_is_nothing_but_one_land_send(home: Path) -> None:
    from handline.session import landgate
    from handline.infra import daemonclient as dmn_client, sessiontoken
    tree, sha = "a" * 40, "abc1234"
    ok = {
        f"hl land --pass {sha}": ("land_pass", sha),
        f"bin/ocp land --pass {sha}": ("land_pass", sha),
        f"handline land {sha} 'Merge feat (x & y)' --tree {tree}": ("land", sha),
        f"hl land {sha} \"Merge feat\" --tree {tree}": ("land", sha),
        f"hl land --tree {tree} {sha} msg": ("land", sha),
        "hl land --pass abc#x": ("land_pass", "abc#x"),
        f"hl land {sha} msg --tree {tree} --fix /tmp/fix --squash": ("land", sha),
        f"hl land --squash {sha} msg --tree {tree}": ("land", sha),
    }
    no = [f"hl land --check {sha}", "hl land --list", f"hl land {sha} msg", f"hl land --pass",
          f"hl land --pass {sha} && tools/suite.sh", f"tools/suite.sh; hl land --pass {sha}",
          f"hl land --pass {sha} | cat", f"hl land --pass $(cat x)", f"hl land --pass `cat x`",
          f"hl land {sha} \"$(cat x)\" --tree {tree}", f"hl land --pass {sha}\nrm x",
          f"python3 tests/x.py hl land --pass {sha}", f"echo hl land --pass {sha}", f"hl land --pass a b",
          f"hl land --tree {tree} {sha} #x", f"hl land --pass {sha} #x", f"hl land --pass {sha} # note", "hl land #c --pass abc1234",
          f"hl land --bogus {sha}", f"hl land --pass {sha} > out", "hl land",
          f"hl land --pass {sha} --fix /tmp/fix", f"hl land --pass {sha} --squash"]
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


def test_a_land_with_more_around_it_is_stopped_before_it_runs_with_how_to_retype_it(home: Path) -> None:
    """Without this, a land typed with a 2>&1 or a cd in front ran, and the daemon refused it as coming from other code; the same
    table runs against session.Misses in go/internal/session/landgate_test.go."""
    from handline.session import landgate
    from handline.infra import sessiontoken
    use_home(home)
    cases = (("hl land --pass abc", False), ("hl land abc 'm' --tree def", False), ("hl land --pass abc 2>&1", True),
             ("cd /r && hl land abc 'm' --tree def", True), ("hl land abc 'costs $5' --tree def", True),
             ("hl land --pass abc | tail -3", True), ("hl land --check abc 2>&1", False), ("hl land --list | head", False),
             ("git log | grep land", False), ("echo hl landed --pass", False),
             ('hl task handoff T1 worker "PASS recorded; land it: hl land --pass abc1234"', False),
             ("hl say manager 'please run hl land --pass abc1234'", False),
             ("hl say b - <<'EOF'\nhl land --pass abc\nEOF", False), ("echo x; hl land --pass abc", True))
    bash = {"command": "hl land --pass abc 2>&1"}
    check("misses: a session with no key is not told to retype, as retyping would not help it", landgate.misses("backend", "Bash", bash) is None)
    sessiontoken.mint_key("backend")
    for cmd, misses in cases:
        got = landgate.misses("backend", "Bash", {"command": cmd})
        check(f"misses: {cmd!r} {'is stopped with how to retype it' if misses else 'goes on'}",
              (got == landgate.MISSES) if misses else got is None, str(got))
    check("misses: only a Bash call is read", landgate.misses("backend", "Write", bash) is None)


def test_ocp_land_checks_and_sends_in_two_steps(home: Path) -> None:
    import argparse
    import contextlib
    import io
    from handline.cli import land as cli_land
    sent, real_tree, real_send = [], cli_land._tested_trees, cli_land.dmn_client.send
    cli_land._tested_trees = lambda shas, fix=None: ["t" * 40]
    cli_land.dmn_client.send = lambda payload, timeout=0: (sent.append(payload), {"ok": True, "said": "ok"})[1]

    def run_land(**kw):
        args = argparse.Namespace(**{"sha": None, "message": None, "passed": False, "list": False,
                                     "check": None, "tree": None, "fix": None, "squash": False, **kw})
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


def test_a_pass_is_not_recorded_for_a_sha_the_merger_s_check_would_refuse(home: Path) -> None:
    """`hl land --pass` runs `land.sh --check --refs`, the merger's own check on the merge, judged by the merged
    tree's localrefs.py and allow file: a local id in a commit message or personal data in the diff prints the
    hits and sends nothing; a clean sha, or one that allows its own fixture, still sends its PASS; one that
    tightens a rule and breaks it does not. A check that cannot run, or fails saying nothing, sends nothing."""
    import argparse
    import contextlib
    import io
    import shutil
    from handline.cli import land as cli_land
    main = _repo(home / "repo")
    refs = (ROOT / "tools" / "localrefs.py").read_text()
    (main / "tools" / "localrefs.py").write_text(refs)
    shutil.copy(LAND, main / "tools" / "land.sh")
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "localrefs")
    _git(main, "checkout", "-q", "-b", "cited", "main")
    (main / "c.py").write_text("z = 3\n")
    _git(main, "add", "-A")
    _git(main, "commit", "-q", "-m", "fix what T" + "1234 asked for")
    cited = _git(main, "rev-parse", "HEAD")
    _git(main, "checkout", "-q", "main")
    mailed = _branch(main, "mailed", "d.py", "owner = 'someone@" + "gmail.com'\n")
    clean = _branch(main, "clean", "e.py", "w = 4\n")
    allows = _branch(main, "allows", "fixture.txt", "someone@" + "gmail.com\n", ("tools/localrefs.allow", "fixture.txt\n"))
    safe = '{"example.com", "example.org", "example.net"}'
    assert safe in refs
    tightens = _branch(main, "tightens", "f.py", "to = 'a@example.com'\n",
                       ("tools/localrefs.py", refs.replace(safe, '{"example.org", "example.net"}')))
    sent, real_send = [], cli_land.dmn_client.send
    cli_land.dmn_client.send = lambda payload, timeout=0: (sent.append(payload), {"ok": True, "said": "PASS recorded"})[1]
    real_root = cli_land.paths.repo_root
    cli_land.paths.repo_root = lambda: main
    runs = home / "suite-runs"
    os.environ["SUITE_RUNS"] = str(runs)

    def run_pass(sha):
        args = argparse.Namespace(sha=sha, message=None, passed=True, list=False, check=None, tree=None, fix=None,
                                  squash=False)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli_land.cmd_land(args)
        return rc, out.getvalue()
    try:
        rc, out = run_pass(cited)
        check("land --pass: an id in a commit message records no PASS and prints the hit",
              rc == 1 and "task id" in out and "no PASS recorded" in out and not sent, out)
        rc, out = run_pass(mailed)
        check("land --pass: an email the diff adds records no PASS and prints the hit",
              rc == 1 and "email" in out and not sent, out)
        rc, out = run_pass(clean)
        check("land --pass: a clean sha still sends its PASS",
              rc == 0 and [p["verb"] for p in sent] == ["land_pass"] and sent[0]["sha"] == clean, out + str(sent))
        rc, out = run_pass(allows)
        check("land --pass: a branch that names its own fixture in the allow file gets its PASS, as the merger "
              "would merge it", rc == 0 and [p["sha"] for p in sent] == [clean, allows], out + str(sent))
        rc, out = run_pass(tightens)
        check("land --pass: a branch that tightens a rule and breaks it gets no PASS",
              rc == 1 and "email" in out and len(sent) == 2, out + str(sent))
        real_run = cli_land.subprocess.run
        for name, stand_in, said in (
                ("times out", lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired(a[0], 1)), "try again"),
                ("exits 1 saying nothing", lambda *a, **k: subprocess.CompletedProcess(a[0], 1, "", ""), "exited 1")):
            cli_land.subprocess.run = stand_in
            try:
                rc, out = run_pass(clean)
            finally:
                cli_land.subprocess.run = real_run
            check(f"land --pass: a check that {name} records no PASS", rc == 1 and said in out and len(sent) == 2,
                  out + str(sent))
        check("land --pass: and its check never runs the suite", not runs.exists())
    finally:
        cli_land.dmn_client.send, cli_land.paths.repo_root = real_send, real_root
        os.environ.pop("SUITE_RUNS", None)


def test_the_pretooluse_hook_vouches_for_the_session_s_own_land_command(home: Path) -> None:
    import contextlib
    import io
    from handline.infra import daemonclient as dmn_client, sessiontoken
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


def main() -> int:
    def tests(td):
        run(lambda: test_a_branch_lands_by_fast_forward_and_a_conflict_never_touches_the_checkout(Path(td) / "l1"))
        run(lambda: test_a_red_suite_or_a_dirty_checkout_lands_nothing(Path(td) / "l2"))
        run(lambda: test_a_check_with_a_pass_skips_the_suite_only_for_the_tree_the_auditor_passed(Path(td) / "l2b"))
        run(lambda: test_a_pass_skips_the_suite_for_one_sha_and_never_for_a_train(Path(td) / "l2c"))
        run(lambda: test_a_check_runs_the_python_suite_only_when_the_merge_changes_more_than_go_and_the_apps(Path(td) / "l2d"))
        run(lambda: test_personal_data_or_a_local_id_stops_a_check(Path(td) / "l4"))
        run(lambda: test_a_housekeeping_fix_never_reaches_agent_instructions(Path(td) / "l4c"))
        run(lambda: test_the_hook_vouches_only_for_a_command_that_is_nothing_but_one_land_send(Path(td) / "l4"))
        run(lambda: test_a_land_with_more_around_it_is_stopped_before_it_runs_with_how_to_retype_it(Path(td) / "l4b"))
        run(lambda: test_ocp_land_checks_and_sends_in_two_steps(Path(td) / "l5"))
        run(lambda: test_a_pass_is_not_recorded_for_a_sha_the_merger_s_check_would_refuse(Path(td) / "l5b"))
        run(lambda: test_a_documentation_only_merge_runs_no_suite(Path(td) / "l7"))
        run(lambda: test_the_pretooluse_hook_vouches_for_the_session_s_own_land_command(Path(td) / "l6"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
