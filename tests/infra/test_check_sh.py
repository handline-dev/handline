"""Tests: tools/check.sh exits 1 when the app checks fail, whichever of turbo and tools/dupes.sh failed, and 0 when both pass.
The real script runs in a scratch repository whose other steps are stubs that pass.

Run on its own: tools/test.sh tests/infra/test_check_sh.py   (all areas: tools/suite.sh)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

ROOT = Path(__file__).resolve().parents[2]
STUBS = {"check-deprecated.sh": "exit 0", "test-go.sh": "exit 0", "test.sh": "exit 0", "suite.sh": "exit 0",
         "dupes.sh": 'exit "${DUPES_RC:-0}"', "leakcheck.py": '[ "$1" = begin ] && echo "$LEAK_RUN"; exit 0'}


def _repo(at: Path) -> Path:
    """A repository on main with check.sh and the stubs, and HEAD one commit on with an app change."""
    (at / "tools").mkdir(parents=True)
    shutil.copy(ROOT / "tools" / "check.sh", at / "tools" / "check.sh")
    for name, body in STUBS.items():
        (at / "tools" / name).write_text(f"#!/bin/sh\n{body}\n")
        (at / "tools" / name).chmod(0o755)
    (at / "node_modules").mkdir()
    (at / "bin").mkdir()
    (at / "bin" / "pnpm").write_text('#!/bin/sh\nexit "${PNPM_RC:-0}"\n')
    (at / "bin" / "pnpm").chmod(0o755)
    (at / ".gitignore").write_text("bin/\nnode_modules/\n")
    git = ["git", "-c", "user.name=check-test", "-c", "user.email=check-test@localhost"]
    for args in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "base"], ["switch", "-q", "-c", "change"]):
        subprocess.run([*git, *args], cwd=at, check=True)
    (at / "apps").mkdir()
    (at / "apps" / "app.ts").write_text("export {}\n")
    for args in (["add", "-A"], ["commit", "-q", "-m", "app change"]):
        subprocess.run([*git, *args], cwd=at, check=True)
    return at


def _check(repo: Path, **env: str) -> subprocess.CompletedProcess:
    (repo / "run").mkdir(exist_ok=True)
    return subprocess.run(["tools/check.sh", "main"], cwd=repo, capture_output=True, text=True, timeout=60,
                          env={**os.environ, "PATH": f"{repo / 'bin'}:{os.environ['PATH']}", "HANDLINE_HEAVY_SLOT": "1",
                               "LEAK_RUN": str(repo / "run"), **env})


def test_a_failing_app_check_fails_the_run(home: Path) -> None:
    repo = _repo(home)
    res = _check(repo, CHECK_PLAN="1")
    check("check.sh: the scratch change plans the Go run with turbo", res.stdout.strip() == "kind=go apps=turbo", res.stdout + res.stderr)
    res = _check(repo, PNPM_RC="1")
    check("check.sh: a turbo failure exits 1 though tools/dupes.sh passes", res.returncode == 1, res.stdout + res.stderr)
    res = _check(repo, DUPES_RC="1")
    check("check.sh: a tools/dupes.sh failure exits 1", res.returncode == 1, res.stdout + res.stderr)
    res = _check(repo)
    check("check.sh: passing app checks exit 0", res.returncode == 0, res.stdout + res.stderr)


def main() -> int:
    def tests(td):
        run(lambda: test_a_failing_app_check_fails_the_run(Path(td) / "a"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
