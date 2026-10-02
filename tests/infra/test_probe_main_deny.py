"""Tests: tools/probe_main_deny.py builds a scratch repository, the settings a session gets, and judges
what its five writes left on disk. The sandboxed run itself needs claude; a stand-in runs the script here.

Run on its own: python3 tests/infra/test_probe_main_deny.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

_spec = importlib.util.spec_from_file_location("probe_main_deny", ROOT / "tools" / "probe_main_deny.py")
pm = importlib.util.module_from_spec(_spec)
sys.modules["probe_main_deny"] = pm
_spec.loader.exec_module(pm)


def _run_script(s: "pm.Scratch") -> None:
    if not (s.top / "probe.sh").exists():
        (s.top / "probe.sh").write_text(pm.probe_script(s))
    subprocess.run(["sh", str(s.top / "probe.sh")], cwd=s.wt, env=pm.GIT_ENV, capture_output=True)


def _stand_in(path: Path, record: Path | None = None, refuse_control: bool = False) -> Path:
    """A `claude` that runs the probe script, as the sandboxed one is asked to. It records its cwd and
    arguments (one per line) in `record`; `refuse_control` shuts the scratch folder for the run, which
    is what a sandbox does to the control file."""
    lines = ["#!/bin/sh"]
    if record:
        lines += [f"pwd > {record}", f'printf "%s\\n" "$@" >> {record}']
    lines += ["chmod 555 .." if refuse_control else "true", "sh ../probe.sh", "chmod 755 ..", "echo done"]
    path.write_text("\n".join(lines) + "\n")
    path.chmod(0o755)
    return path


def _shut(s: "pm.Scratch", mode: int) -> None:
    s.top.chmod(mode)


def test_the_settings_are_the_ones_a_session_gets_for_the_scratch_worktree(home: Path) -> None:
    s = pm.build_scratch(home / "s")
    settings = pm.probe_settings(s)
    want = [str(s.common / p) for p in ("refs/heads/main", "refs/heads/main.lock", "packed-refs", "packed-refs.lock")]
    want.append(str(s.control))
    fs = settings["sandbox"]["filesystem"]
    check("probe: denyWrite is the four main-ref files of the scratch repository, then the control file", fs["denyWrite"] == want, str(fs))
    check("probe: the scratch .git is writable and the sandbox is on",
          fs["allowWrite"] == [str(s.common)] and settings["sandbox"]["enabled"] is True, str(settings["sandbox"]))
    check("probe: nothing names a path outside the scratch folder",
          all(str(s.top) in p for p in fs["denyWrite"] + fs["allowWrite"]) and str(ROOT) not in json.dumps(settings))
    check("probe: main is on one commit and each op has its own other commit",
          pm.git(s.repo, "rev-parse", "main") == s.start and len({s.start, *s.commits.values()}) == 4)


def test_an_unsandboxed_run_is_inconclusive_because_the_control_was_written(home: Path) -> None:
    s = pm.build_scratch(home / "open")
    _run_script(s)
    wrote, verdict = pm.judge(s, pm.read_log(s))
    check("probe: with no sandbox every op, the control too, is seen to write", all(wrote.values()), str(wrote))
    check("probe: so the verdict is inconclusive, not a gap", verdict.startswith("INCONCLUSIVE") and "not applied" in verdict,
          verdict)


def test_refused_writes_are_judged_as_held_and_one_that_got_through_is_named(home: Path) -> None:
    s = pm.build_scratch(home / "shut")
    heads = s.common / "refs" / "heads"
    try:
        (s.top / "probe.sh").write_text(pm.probe_script(s))
        heads.chmod(0o555)
        s.common.chmod(0o555)
        _shut(s, 0o555)
        _run_script(s)
        wrote, verdict = pm.judge(s, pm.read_log(s))
        check("probe: with the folders shut no op wrote", not any(wrote.values()), str(wrote))
        check("probe: the verdict is HOLDS", verdict == "HOLDS", verdict)
        check("probe: main is where it started", pm.git(s.repo, "rev-parse", "main") == s.start)
    finally:
        _shut(s, 0o755)
        s.common.chmod(0o755)
        heads.chmod(0o755)
    # Only the lock gets through: the .git folder is open, refs/heads is shut.
    s2 = pm.build_scratch(home / "lock")
    heads2 = s2.common / "refs" / "heads"
    try:
        (s2.top / "probe.sh").write_text(pm.probe_script(s2))
        heads2.chmod(0o555)
        _shut(s2, 0o555)
        _run_script(s2)
        wrote, verdict = pm.judge(s2, pm.read_log(s2))
        check("probe: only the packed-refs lock was written, the control was refused", [i for i, w in wrote.items() if w] == [2], str(wrote))
        check("probe: and the verdict names it", verdict == "GAP: create packed-refs.lock wrote", verdict)
    finally:
        _shut(s2, 0o755)
        heads2.chmod(0o755)


def test_a_link_made_but_not_written_through_holds_with_a_note_and_a_run_that_did_not_finish_is_inconclusive(
        home: Path) -> None:
    s = pm.build_scratch(home / "link")
    log = {i: ("1", s.start) for i in pm.WHAT}
    ref = s.common / "refs" / "heads"
    os.link(ref / "main", ref / "zz-probe2")
    wrote, verdict = pm.judge(s, log)
    check("probe: a link and no write through it holds, with a note",
          wrote[4] and verdict.startswith("HOLDS") and "link" in verdict, verdict + str(wrote))
    wrote, verdict = pm.judge(s, {1: ("1", s.start), 2: ("1", s.start)})
    check("probe: ops that never ran make it inconclusive", verdict.startswith("INCONCLUSIVE: ops 3, 4, 5, 6"), verdict)


def _probe(*args: str, tmp: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(ROOT / "tools" / "probe_main_deny.py"), *args], capture_output=True,
                          text=True, env=dict(os.environ, TMPDIR=tmp))


def test_the_command_launches_claude_with_the_settings_in_the_worktree_and_exits_by_the_verdict(home: Path) -> None:
    home.mkdir(parents=True)
    tmp = home / "tmp"
    tmp.mkdir()
    record = home / "record"
    r = _probe("--claude", str(_stand_in(home / "claude", record, refuse_control=True)), tmp=str(tmp))
    cwd, *args = record.read_text().splitlines()
    scratch = Path(cwd).parent
    s = pm.Scratch(scratch, scratch / "repo", Path(cwd), scratch / "repo" / ".git", {}, "")
    check("probe: claude runs in the scratch worktree", cwd == str(scratch / "wt") and scratch.parent == tmp.resolve(), cwd)
    sent = json.loads(args[args.index("--settings") + 1])
    check("probe: and gets --settings built for that scratch, the deny and the control in it",
          sent == pm.probe_settings(s) and str(scratch / "control.deny") in sent["sandbox"]["filesystem"]["denyWrite"], str(sent))
    check("probe: non-interactively, with the sandbox's own permission mode off",
          args[0] == "-p" and "--dangerously-skip-permissions" in args, str(args))
    check("probe: a refused control with writes through ends in GAP and exit 1",
          r.returncode == 1 and r.stdout.rstrip().endswith("write through the link wrote"), r.stdout + r.stderr)
    check("probe: one line per op, the scratch folder named, claude's reply shown",
          all(f"\n{i} " in r.stdout for i in range(1, 7)) and "scratch: " + str(scratch) in r.stdout
          and "claude said: done" in r.stdout, r.stdout)
    r = _probe("--claude", str(_stand_in(home / "open-claude")), tmp=str(tmp))
    check("probe: an unsandboxed run ends INCONCLUSIVE and exit 2, not GAP",
          r.returncode == 2 and "INCONCLUSIVE: the control write went through" in r.stdout, r.stdout + r.stderr)
    r = _probe("--claude", "/nonexistent/claude", tmp=str(tmp))
    check("probe: a missing claude is a failure, not a verdict", r.returncode not in (0, 1), r.stdout + r.stderr)


def test_a_claude_that_times_out_is_inconclusive_with_no_traceback(home: Path) -> None:
    home.mkdir(parents=True)
    (home / "tmp").mkdir()
    slow = home / "slow"
    slow.write_text("#!/bin/sh\nexec sleep 30\n")
    slow.chmod(0o755)
    r = _probe("--claude", str(slow), "--timeout", "2", tmp=str(home / "tmp"))
    check("probe: a timeout ends INCONCLUSIVE and exit 2", r.returncode == 2 and "INCONCLUSIVE" in r.stdout
          and "Traceback" not in r.stderr, r.stdout + r.stderr)
    check("probe: it says claude timed out and lists the ops as never run",
          "timed out after 2s" in r.stdout and r.stdout.count("never ran rc") == 6, r.stdout)


def main() -> int:
    def tests(td):
        run(lambda: test_the_settings_are_the_ones_a_session_gets_for_the_scratch_worktree(Path(td) / "a"))
        run(lambda: test_an_unsandboxed_run_is_inconclusive_because_the_control_was_written(Path(td) / "b"))
        run(lambda: test_refused_writes_are_judged_as_held_and_one_that_got_through_is_named(Path(td) / "c"))
        run(lambda: test_a_link_made_but_not_written_through_holds_with_a_note_and_a_run_that_did_not_finish_is_inconclusive(
            Path(td) / "d"))
        run(lambda: test_the_command_launches_claude_with_the_settings_in_the_worktree_and_exits_by_the_verdict(Path(td) / "e"))
        run(lambda: test_a_claude_that_times_out_is_inconclusive_with_no_traceback(Path(td) / "f"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
