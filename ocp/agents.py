"""Agent registration (design section 3, team model section 7).

The agent name is the join key across every system (section 2): the worktree
name, the Orca task label and the folder name are all the same string.
"""
from __future__ import annotations

from pathlib import Path

from . import paths
from .yamlcompat import ConfigError, dump, load

BASE_BUDGET_BYTES = 2048  # base.md <= 2KB (team model section 1)
ROLE_BUDGET_BYTES = 1024  # ROLE.md <= 1KB


def agent_file(name: str) -> Path:
    return paths.agent_dir(name) / "agent.yaml"


def read(name: str) -> dict:
    p = agent_file(name)
    if not p.exists():
        raise FileNotFoundError(f"agent '{name}' is not registered ({p})")
    data = load(p.read_text())
    if not isinstance(data, dict):
        raise ConfigError(f"{p}: top level is not a map")
    return data


def register(name: str, account: str, role: str = "", model: str = "sonnet") -> Path:
    if not name or "/" in name or name.startswith("."):
        raise ValueError(f"bad agent name: {name!r}")
    d = paths.agent_dir(name)
    for sub in ("inbox", "journal", "handoff"):
        (d / sub).mkdir(parents=True, exist_ok=True)

    f = agent_file(name)
    if not f.exists():
        f.write_text(
            f"name: {name}\naccount: {account}\nmodel: {model}\nenabled: true\n"
        )
    else:
        # Already registered: only the account moves. Re-serialize from the
        # parsed document rather than splicing lines - matching "account:" as a
        # prefix broke on "account : alpha" (appending a duplicate key) and on
        # any nested key of the same name.
        cfg = load(f.read_text())
        if not isinstance(cfg, dict):
            raise ConfigError(f"{f}: top level is not a map")
        cfg["account"] = account
        f.write_text(dump(cfg))

    role_file = d / "ROLE.md"
    if not role_file.exists():
        role_file.write_text(f"# {name}\n\n{role or '(describe the role, under 1KB)'}\n")
    base_file = d / "base.md"
    if not base_file.exists():
        base_file.write_text(
            f"# base - {name}\n\n"
            "(injected into every session, so it is fixed overhead. Keep it under 2KB.)\n"
        )
    return d


def account_of(name: str) -> str:
    acct = read(name).get("account")
    if not acct:
        raise ConfigError(f"agent '{name}' has no account")
    return str(acct)


def budget_report(name: str) -> list[tuple[str, int, int, bool]]:
    """(file, bytes, budget, over?) for the per-session fixed overhead."""
    d = paths.agent_dir(name)
    out = []
    for fn, budget in (("base.md", BASE_BUDGET_BYTES), ("ROLE.md", ROLE_BUDGET_BYTES)):
        p = d / fn
        size = p.stat().st_size if p.exists() else 0
        out.append((fn, size, budget, size > budget))
    return out
