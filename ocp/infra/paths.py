"""Layout of ~/.orcapipe (design 01-infra.md section 3)."""
from __future__ import annotations

import os
import pwd
import shutil
from pathlib import Path

# Set by tests/support.py to a temp directory that stands in for the user's
# home. While it is set, nothing ocp does may reach the real one.
TEST_ROOT_ENV = "OCP_TEST_ROOT"


class RealHomeInTest(RuntimeError):
    pass


def _real_user_home() -> Path:
    # From the password database, not $HOME, so a test that sets HOME cannot
    # move what the guard protects.
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def user_home() -> Path:
    """The user's home directory, or its stand-in under the test suite.

    Every path ocp builds under ~ starts here, never at Path.home()."""
    test_root = os.environ.get(TEST_ROOT_ENV)
    return Path(test_root) if test_root else Path.home()


def default_home() -> Path:
    """Where OCP_HOME points when nothing has set it."""
    return user_home() / ".orcapipe"


def home() -> Path:
    h = Path(os.environ.get("OCP_HOME", default_home())).expanduser()
    if os.environ.get(TEST_ROOT_ENV):
        real = (_real_user_home() / ".orcapipe").resolve()
        if real == h.resolve() or real in h.resolve().parents:
            raise RealHomeInTest(f"a test reached the real OCP_HOME: {h}")
    return h


def socket_file() -> Path:
    """The one unix socket ocp's processes talk over, under the home."""
    return home() / "ocp.sock"


def config_file() -> Path:
    return home() / "config.yaml"


def team_file() -> Path:
    return home() / "team.yaml"


def db_file() -> Path:
    return home() / "db.sqlite"


def socket_file() -> Path:
    """The daemon's control socket (T351): the only way in to type at an
    agent's terminal, answer a dialog, or wake/close/park/fold/move/start
    one - one JSON request per connection, one {ok, said} reply. A refused
    connection means the daemon is down; there is no other path in."""
    return home() / "daemon.sock"


def mcp_config() -> Path:
    """The MCP server registry every launch passes via `--mcp-config`.

    One file ocp owns, the founder fills in - never generated from
    ~/.claude.json, which AGENTS.md rule 1 forbids touching."""
    return home() / "shared" / "mcp.json"


def accounts_dir() -> Path:
    return home() / "accounts"


def account_dir(acct: str) -> Path:
    return accounts_dir() / acct


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


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def ocp_exe() -> str:
    """The command a hook or launch line should call `ocp` with."""
    return os.environ.get("OCP_BIN") or shutil.which("ocp") or str(repo_root() / "bin" / "ocp")


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
