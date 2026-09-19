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
    """Account-wide snapshot: rate limits, and whichever session wrote last."""
    return account_dir(acct) / "status.json"


def agent_status_file(name: str) -> Path:
    """This agent's own session snapshot.

    Rate limits are account-wide, but context usage is per session. When two
    agents share an account the account file only holds one of their contexts,
    so each agent keeps its own copy and the context trigger reads that.
    """
    return agent_dir(name) / "status.json"


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


def removing(acct: str) -> bool:
    """Is this account mid-removal? Set between the confirmation and the
    logout, so nothing is started on an account about to go."""
    return (accounts_dir() / acct / ".removing").exists()


def list_agents() -> list[str]:
    d = agents_dir()
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if (p / "agent.yaml").exists())
