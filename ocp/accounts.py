"""Account registration: directories, shared links, collector install.

Credentials are never touched (KICKOFF rule 1). A refresh token rotates on
single use, so two copies of one credential invalidate each other. Logging in
is a one-time human action:

    CLAUDE_CONFIG_DIR=~/.orcapipe/accounts/<acct>/.claude claude /login

All we do is create the directory, symlink the shared settings, and install
the collector.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from . import paths

# What each account directory links back to shared. Nothing credential-shaped
# appears here, deliberately.
LINKED = ["settings.json", "CLAUDE.md", "skills", "agents", "commands", "plugins"]

# Named explicitly as a guard: these are never created, linked, read or copied.
NEVER_TOUCH = {".credentials.json", ".claude.json", "statsig", "backups"}


def ensure_shared() -> Path:
    """Create shared/.claude and install the collector into it."""
    shared = paths.shared_claude()
    shared.mkdir(parents=True, exist_ok=True)
    for sub in ("skills", "agents", "commands"):
        (shared / sub).mkdir(exist_ok=True)

    src = paths.repo_root() / "libexec" / "statusline-collect.sh"
    dst = paths.collector()
    if src.exists():
        shutil.copyfile(src, dst)
        dst.chmod(0o755)

    settings = shared / "settings.json"
    current = {}
    if settings.exists():
        try:
            current = json.loads(settings.read_text())
        except json.JSONDecodeError:
            current = {}
    if not isinstance(current, dict):
        current = {}

    # statusLine is our data source (section 4), so it always points at us.
    current["statusLine"] = {
        "type": "command",
        "command": str(dst),
        "padding": 0,
        "refreshInterval": int(os.environ.get("OCP_STATUSLINE_REFRESH", "10")),
    }
    # Ours overlaps Claude Code's own auto-continue, and section 4 says to pick
    # one. We pick ours, and doctor enforces that the choice stays made.
    current.setdefault("autoContinueAtUsageLimit", False)
    settings.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n")
    return shared


def register(acct: str, force: bool = False) -> Path:
    """Create the account directory and link it to shared. Does not log in."""
    if not acct or "/" in acct or acct.startswith("."):
        raise ValueError(f"bad account name: {acct!r}")
    ensure_shared()
    cdir = paths.account_claude(acct)
    cdir.mkdir(parents=True, exist_ok=True)

    shared = paths.shared_claude()
    for item in LINKED:
        if item in NEVER_TOUCH:
            continue
        target = shared / item
        link = cdir / item
        if not target.exists():
            continue
        if link.is_symlink():
            if os.readlink(link) == str(target) and not force:
                continue
            link.unlink()
        elif link.exists():
            if not force:
                continue
            shutil.rmtree(link) if link.is_dir() else link.unlink()
        link.symlink_to(target)
    return cdir


def logged_in(acct: str) -> bool:
    """Existence check only. The file is never opened."""
    return (paths.account_claude(acct) / ".credentials.json").exists()


def login_command(acct: str) -> str:
    return f'CLAUDE_CONFIG_DIR="{paths.account_claude(acct)}" claude /login'


def env_for(acct: str, agent: str | None = None) -> dict[str, str]:
    """Environment for launching claude as this account."""
    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = str(paths.account_claude(acct))
    env["OCP_HOME"] = str(paths.home())
    env["OCP_ACCOUNT"] = acct
    if agent:
        env["OCP_AGENT"] = agent
    return env
