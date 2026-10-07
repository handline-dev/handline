"""Layout of ~/.orcapipe (design 01-infra.md section 3)."""
from __future__ import annotations

import os
import pwd
import shutil
from pathlib import Path

from . import names

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
    test_root = names.getenv(TEST_ROOT_ENV)
    return Path(test_root) if test_root else Path.home()


def default_home() -> Path:
    """Where the home is when nothing has set it: ~/.handline once it exists, else ~/.orcapipe."""
    if (user_home() / ".handline").is_dir():
        return user_home() / ".handline"
    return user_home() / ".orcapipe"


def ensure_cwd() -> None:
    """Move to the repo when this process's working directory is gone (the folder it started in was
    removed): every child inherits it, and `orca`, `claude` and a shell all fail on it. The repo, not
    the home, since `start` with no repo and `task done <sha>` read the cwd and want a checkout."""
    try:
        os.getcwd()
    except OSError:
        os.chdir("/")      # resolving a path needs a live cwd
        os.chdir(repo_root())


def home() -> Path:
    raw = names.getenv("OCP_HOME")
    if raw is not None and not raw.strip():
        # Path("") is the current directory: `hl init` would set up wherever it was run.
        raise SystemExit("HANDLINE_HOME (or OCP_HOME) is set but empty: unset it to use the default home, or give it a folder")
    h = Path(raw or default_home()).expanduser()
    if h.is_symlink():
        h = h.resolve()     # after the home moved, the old path is a link: sandbox rules match real paths
    if names.getenv(TEST_ROOT_ENV):
        for real in ((_real_user_home() / d).resolve() for d in (".orcapipe", ".handline")):
            if real == h.resolve() or real in h.resolve().parents:
                raise RealHomeInTest(f"a test reached the real home: {h}")
    return h


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


def relay_socket_file() -> Path:
    """Where the gateway, the process that fronts the daemon for paired devices, relays their calls (daemon/relay.py)."""
    return home() / "relay.sock"


def gateway_socket_file() -> Path:
    """The gateway's local socket, for the founder's `hl device` commands (never the daemon's)."""
    return home() / "gateway.sock"


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


def handoff_note_file(agent: str) -> Path:
    """The one file an agent writes for its handoff note (T571, `daemon.handoff_note`)."""
    return agent_dir(agent) / "handoff.md"


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def live_homes() -> list[Path]:
    """The real user's homes, whatever OCP_HOME or the test root says."""
    return [(_real_user_home() / d).resolve() for d in (".orcapipe", ".handline")]


def ocp_exe() -> str:
    """The command a hook or launch line should call `hl` with."""
    return names.getenv("OCP_BIN") or next(filter(None, map(shutil.which, names.COMMANDS)), None) or str(repo_root() / "bin" / "hl")


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
