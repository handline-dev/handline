"""Layout of ~/.orcapipe (design 01-infra.md section 3)."""
from __future__ import annotations

import os
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("OCP_HOME", Path.home() / ".orcapipe")).expanduser()


def config_file() -> Path:
    return home() / "config.yaml"


def team_file() -> Path:
    return home() / "team.yaml"


def db_file() -> Path:
    return home() / "db.sqlite"


def daemon_pid() -> Path:
    return home() / "daemon.pid"


def shared_claude() -> Path:
    return home() / "shared" / ".claude"


def accounts_dir() -> Path:
    return home() / "accounts"


def account_dir(acct: str) -> Path:
    return accounts_dir() / acct


def account_claude(acct: str) -> Path:
    """This path is the account's CLAUDE_CONFIG_DIR.

    The collector derives the account name from the grandparent directory:
      accounts/<acct>/.claude  ->  <acct>
    """
    return account_dir(acct) / ".claude"


def status_file(acct: str) -> Path:
    return account_dir(acct) / "status.json"


def agents_dir() -> Path:
    return home() / "agents"


def agent_dir(name: str) -> Path:
    return agents_dir() / name


def collector() -> Path:
    """The single installed collector that every account points at."""
    return shared_claude() / "statusline-collect.sh"


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def list_accounts() -> list[str]:
    d = accounts_dir()
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_dir() and not p.name.startswith("."))


def list_agents() -> list[str]:
    d = agents_dir()
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if (p / "agent.yaml").exists())
