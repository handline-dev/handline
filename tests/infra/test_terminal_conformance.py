"""Tests: the one terminal-host contract (terminal_conformance.py) run against every backend: an in-memory
host, Orca over a stateful fake of its CLI, and tmux over a fake tmux. A new host adds one harness here.

Run on its own: tools/test.sh tests/infra/test_terminal_conformance.py   (all areas: tools/suite.sh)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
import terminal_conformance as tc  # noqa: E402
import test_terminal_backends as tb  # noqa: E402
from handline.session.backends import base, orca_cli as orcacli, tmux as tmuxbackend  # noqa: E402


def _repos(home: Path) -> tuple[str, str]:
    """(a git repository with a commit on main, a folder that is no repository)."""
    repo, plain = home / "proj", home / "plain"
    repo.mkdir(parents=True)
    plain.mkdir()
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@example.com", GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null")
    for args in (["init", "-q", "-b", "main"], ["commit", "-q", "--allow-empty", "-m", "base"]):
        subprocess.run(["git", *args], cwd=repo, capture_output=True, env=env, timeout=30)
    return str(repo), str(plain)


class MemHost(base.Backend):
    """The reference host: every ability of the contract, held in memory."""
    name = "mem"

    def __init__(self) -> None:
        self.alive: dict[str, list[str]] = {}
        self.dead = False
        self.n = 0

    def available(self): return True
    def own_terminal(self): return None, None
    def create_terminal(self, command, cwd=None, title=None):
        if self.dead:
            return None, "mem is down"
        self.n += 1
        self.alive[f"m{self.n}"] = [f"$ {command}"]
        return f"m{self.n}", ""
    def close_terminal(self, handle, tab=True):
        if self.dead or handle not in self.alive:
            return False, f"no terminal {handle}"
        del self.alive[handle]
        return True, ""
    def list_terminals(self): return None if self.dead else set(self.alive)
    def read_screen(self, handle, limit=40):
        if self.dead or handle not in self.alive:
            return None
        return {"handle": handle, "tail": self.alive[handle][-limit:], "source": "screen"}
    def send(self, handle, text, enter=True, wait_submit_s=None):
        if self.dead or handle not in self.alive:
            return False, f"no terminal {handle}"
        self.alive[handle].append(text)
        return True, ""
    def wait_idle(self, handle, timeout_ms, turn_on_screen=None):
        return not self.dead and handle in self.alive
    def focus(self, handle):
        return (not self.dead and handle in self.alive), ""
    def ensure_repo(self, path, present=True):
        if os.path.exists(os.path.join(path, ".git")):
            return "known", f"{path} is a git repository"
        return base.not_a_repo_root(path)
    def create_worktree(self, repo, name, base_ref):
        return f"{repo}.worktrees/{name}", ""


def test_the_in_memory_reference_host_conforms(home: Path) -> None:
    use_home(home)
    repo, plain = _repos(home)
    host = MemHost()
    flip = lambda dead: setattr(host, "dead", dead)
    tc.conform("mem", tc.Harness(host, "m404", lambda: flip(True), lambda: flip(False), str(home), repo, plain))


def _fake_orca(home: Path):
    """Orca's CLI as one function over a state dict: the verbs OrcaBackend uses, answered as Orca does."""
    st = {"terms": {}, "n": 0, "down": False, "repos": set()}

    def fail(code: str, msg: str = "") -> tuple[int, str, str]:
        return 1, json.dumps({"ok": False, "error": {"code": code, "message": msg}}), ""

    def flag(argv, name):
        return argv[argv.index(name) + 1] if name in argv else None

    def fake(argv, timeout=120):
        if st["down"]:
            raise OSError("orca is not running")
        verb, args = " ".join(argv[1:3]), argv
        term = flag(args, "--terminal")
        if verb == "terminal create":
            st["n"] += 1
            st["terms"][f"term_{st['n']}"] = [f"$ {flag(args, '--command')}"]
            return tb._ok(terminal={"handle": f"term_{st['n']}"})
        if verb == "terminal list":
            return tb._ok(terminals=[{"handle": h} for h in st["terms"]])
        if verb in ("terminal read", "terminal send", "terminal wait", "terminal close", "terminal switch") \
                and term not in st["terms"]:
            return fail("terminal_not_found")
        if verb == "terminal read":
            rows = st["terms"][term][-int(flag(args, "--limit")):]
            return tb._ok(terminal={"handle": term, "tail": rows, "source": "screen"})
        if verb == "terminal send":
            st["terms"][term].append(flag(args, "--text"))
            return 0, "", ""
        if verb == "terminal wait":
            return tb._ok(wait={"satisfied": True})
        if verb == "terminal close":
            del st["terms"][term]
            return 0, "", ""
        if verb == "terminal switch":
            return 0, "", ""
        if verb == "repo list":
            return tb._ok(repos=[{"path": p} for p in sorted(st["repos"])])
        if verb == "repo add":
            st["repos"].add(os.path.realpath(flag(args, "--path")))
            return 0, json.dumps({"ok": True}), ""
        if verb == "worktree create":
            return 0, json.dumps({"ok": True, "result": {"worktree": {
                "path": f"{flag(args, '--repo')[5:]}.worktrees/{flag(args, '--name')}"}}}), ""
        return fail("unknown_verb", verb)
    return st, fake


def test_the_orca_backend_conforms_over_a_stateful_fake_cli(home: Path) -> None:
    use_home(home)
    repo, plain = _repos(home)
    st, fake = _fake_orca(home)
    real_exec, real_which = orcacli._exec, orcacli.shutil.which
    orcacli._exec, orcacli.shutil.which = fake, lambda name: "/fake/orca"
    try:
        def flip(down: bool) -> None:
            st["down"] = down
        tc.conform("orca", tc.Harness(orcacli.OrcaBackend(), "term_404", lambda: flip(True), lambda: flip(False),
                                      str(home), repo, plain))
    finally:
        orcacli._exec, orcacli.shutil.which = real_exec, real_which


def test_the_tmux_backend_conforms_over_a_fake_tmux(home: Path) -> None:
    use_home(home)
    repo, plain = _repos(home)
    real_path, real_delay = os.environ["PATH"], tmuxbackend.ENTER_DELAY_S
    saved = (tmuxbackend.IDLE_SETTLE_S, tmuxbackend.IDLE_POLL_S)
    tb._fake_tmux(home)
    tmuxbackend.ENTER_DELAY_S = 0
    tmuxbackend.IDLE_SETTLE_S, tmuxbackend.IDLE_POLL_S = 0.2, 0.1
    try:
        def down() -> None:
            os.environ["FAKE_TMUX_DOWN"] = "1"

        def up() -> None:
            os.environ.pop("FAKE_TMUX_DOWN", None)
        tc.conform("tmux", tc.Harness(tmuxbackend.TmuxBackend(), "%404", down, up, str(home), repo, plain))
    finally:
        os.environ["PATH"] = real_path
        os.environ.pop("FAKE_TMUX_DOWN", None)
        tmuxbackend.ENTER_DELAY_S = real_delay
        tmuxbackend.IDLE_SETTLE_S, tmuxbackend.IDLE_POLL_S = saved


def main() -> int:
    def tests(td):
        run(lambda: test_the_in_memory_reference_host_conforms(Path(td) / "c1"))
        run(lambda: test_the_orca_backend_conforms_over_a_stateful_fake_cli(Path(td) / "c2"))
        run(lambda: test_the_tmux_backend_conforms_over_a_fake_tmux(Path(td) / "c3"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
