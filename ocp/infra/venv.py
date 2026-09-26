"""The interpreter ocp itself runs under (`ocp venv ...`).

bin/ocp used to run under whichever python3 was on PATH - macOS ships one
with PyYAML, Homebrew's has none, and the system one is externally managed,
which is the whole reason ocp/infra/yamlcompat.py exists. This is the other side of
that: a venv at $OCP_HOME/venv, one per machine, that every account's bin/ocp
invocation runs under instead.

`install` is the only thing that runs `python3 -m venv` or pip; it goes
through `_run` alone so tests never spawn a real venv or touch the network.
The venv's own bin/python3 is what bin/ocp execs once it is ready, falling
back to PATH's python3 otherwise (task #54) - that check has to happen in
bash, before any of our python runs, so `status` here is a second copy of the
same sha256 comparison the wrapper makes, kept honest by a test that runs
both and compares.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from . import paths, textsafe

STAMP_NAME = ".ocp_deps_sha256"


def requirements_file() -> Path:
    return paths.repo_root() / "requirements.txt"


def venv_dir() -> Path:
    return paths.home() / "venv"


def venv_python(vdir: Path | None = None) -> Path:
    return (vdir or venv_dir()) / "bin" / "python3"


def _canonical_requirements(path: Path) -> bytes:
    """What actually gets `pip install`ed - blank lines and comments change
    nothing installed, so they must not flip the venv from ready to stale
    (task #54, founder #2134: a comment-only requirements.txt edit left the
    founder silently running PATH's python3). bin/ocp's bash wrapper runs the
    same strip-then-filter over the same file before hashing it."""
    kept = [line.strip() for line in path.read_text().splitlines()
            if line.strip() and not line.strip().startswith("#")]
    # Empty, not "\n": bash's `grep -v` emits zero bytes when nothing
    # survives the filter (an all-comments or blank requirements.txt), and
    # `shasum` of "\n" and of nothing hash differently - the two sides must
    # agree on this edge too.
    return ("\n".join(kept) + "\n").encode() if kept else b""


def _sha256(path: Path) -> str:
    return hashlib.sha256(_canonical_requirements(path)).hexdigest()


def _run(args: list[str]) -> subprocess.CompletedProcess:
    """The only place that shells out to build or fill a venv - tests replace
    this name, the way service.run_launchctl and orca._run are replaced."""
    return subprocess.run(args, capture_output=True, text=True, timeout=300)


def status(vdir: Path | None = None) -> tuple[bool, str]:
    """(ok, reason). False for a venv that does not exist, cannot be run, or
    was installed against a requirements.txt that has since changed - existing
    is not the same as working. This only reports; bin/ocp's own copy of the
    same check is what decides whether to run the venv's python or fall back
    to PATH's (founder #2069)."""
    vdir = vdir or venv_dir()
    py = venv_python(vdir)
    if not (py.is_file() and os.access(py, os.X_OK)):
        return False, f"no venv at {vdir}"
    stamp = vdir / STAMP_NAME
    have = stamp.read_text().strip() if stamp.exists() else None
    reqs = requirements_file()
    if not reqs.is_file():
        return False, f"no requirements.txt at {reqs}"
    want = _sha256(reqs)
    if have != want:
        return False, f"venv at {vdir} is stale (requirements.txt changed since it was installed)"
    return True, ""


def install(vdir: Path | None = None, log=print) -> tuple[bool, str]:
    """Create the venv if it is not there, install requirements.txt into it,
    and stamp it - only on success, so a failed install leaves it reading as
    stale (or missing) rather than falsely fresh."""
    vdir = vdir or venv_dir()
    py = venv_python(vdir)
    if not py.is_file():
        log(f"creating venv at {vdir}")
        r = _run([sys.executable, "-m", "venv", str(vdir)])
        if r.returncode != 0:
            return False, f"could not create the venv: {textsafe.oneline(r.stderr or r.stdout)}"
    reqs = requirements_file()
    log(f"installing {reqs} into {vdir}")
    r = _run([str(py), "-m", "pip", "install", "-q", "-r", str(reqs)])
    if r.returncode != 0:
        return False, f"pip install failed: {textsafe.oneline(r.stderr or r.stdout)}"
    (vdir / STAMP_NAME).write_text(_sha256(reqs) + "\n")
    return True, f"venv at {vdir} is ready"
