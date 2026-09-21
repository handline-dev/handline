"""Layout of ~/.orcapipe (design 01-infra.md section 3)."""
from __future__ import annotations

import os
from pathlib import Path


def default_home() -> Path:
    """Where OCP_HOME points when nothing has set it."""
    return Path.home() / ".orcapipe"


def home() -> Path:
    return Path(os.environ.get("OCP_HOME", default_home())).expanduser()


def config_file() -> Path:
    return home() / "config.yaml"


def team_file() -> Path:
    return home() / "team.yaml"


def db_file() -> Path:
    return home() / "db.sqlite"


def shared_claude() -> Path:
    return home() / "shared" / ".claude"


def mcp_config() -> Path:
    """The MCP server registry every launch passes via `--mcp-config`.

    One file ocp owns, the founder fills in - never generated from
    ~/.claude.json, which KICKOFF rule 1 forbids touching."""
    return home() / "shared" / "mcp.json"


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


def agent_line_file(name: str) -> Path:
    """The line this agent's status line prints (`statusline.py`).

    Written by whoever last knew something worth showing - the daemon's tick,
    or a hook the session itself fired - and only read by the shell command in
    the session's terminal.
    """
    return agent_dir(name) / "line.txt"


def agents_dir() -> Path:
    return home() / "agents"


def agent_dir(name: str) -> Path:
    return agents_dir() / name


def statusline() -> Path:
    """The single installed status line command that every account points at."""
    return shared_claude() / "statusline.sh"


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
