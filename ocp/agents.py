"""Agent registration (design section 3, team model section 7).

The agent name is the join key across every system (section 2): the worktree
name, the Orca task label and the folder name are all the same string.
"""
from __future__ import annotations

from pathlib import Path

from . import paths
from .yamlcompat import ConfigError, dump, load

DEFAULT_MODEL = "sonnet"

# Effort is a cost lever as real as the model, and leaving it unset means it is
# inherited from whatever the environment happens to carry. Each agent declares
# its own, so a journaling agent cannot quietly run at the same effort as one
# doing architecture.
VALID_EFFORT = ("low", "medium", "high", "xhigh", "max")
DEFAULT_EFFORT = "medium"
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


def register(name: str, account: str, role: str = "", model: str | None = None,
             effort: str | None = None, weight: str | None = None,
             role_file: str | None = None) -> Path:
    if not name or "/" in name or name.startswith("."):
        raise ValueError(f"bad agent name: {name!r}")
    d = paths.agent_dir(name)
    for sub in ("inbox", "journal", "handoff"):
        (d / sub).mkdir(parents=True, exist_ok=True)

    f = agent_file(name)
    if effort and effort not in VALID_EFFORT:
        raise ValueError(f"effort must be one of {', '.join(VALID_EFFORT)}, not {effort!r}")
    if not f.exists():
        f.write_text(
            f"name: {name}\naccount: {account}\nmodel: {model or DEFAULT_MODEL}\n"
            f"effort: {effort or DEFAULT_EFFORT}\nweight: {weight or 'light'}\n"
            "enabled: true\n"
        )
    else:
        # Already registered: update what was actually asked for and leave the
        # rest of the mapping alone. Re-serialize from the parsed document
        # rather than splicing lines - matching "account:" as a prefix broke on
        # "account : alpha" and on any nested key of the same name.
        cfg = load(f.read_text())
        if not isinstance(cfg, dict):
            raise ConfigError(f"{f}: top level is not a map")
        cfg["account"] = account
        if model:
            cfg["model"] = model
        if effort:
            cfg["effort"] = effort
        if weight:
            cfg["weight"] = weight
        cfg.setdefault("effort", DEFAULT_EFFORT)
        f.write_text(dump(cfg))

    role_path = d / "ROLE.md"
    if role_file:
        src = Path(role_file).expanduser()
        if not src.exists():
            raise ValueError(f"--role-file not found: {src}")
        role_path.write_text(src.read_text())
    elif not role_path.exists():
        role_path.write_text(f"# {name}\n\n{role or '(describe the role, under 1KB)'}\n")
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


def promote_to_base(name: str, item: str, force: bool = False) -> tuple[bool, str]:
    """Add a fact to base.md (team model section 1).

    base.md is injected into every session, so anything added here is paid for
    on every turn of every session forever. That is why promotion is a human
    action with a budget rather than something an agent does to itself: left
    alone base.md always grows, because useful facts keep being discovered.
    """
    d = paths.agent_dir(name)
    if not d.is_dir():
        return False, f"agent '{name}' is not registered"
    p = d / "base.md"
    text = p.read_text() if p.exists() else f"# base - {name}\n"
    line = f"- {item.strip()}\n"
    if line in text:
        return False, "that item is already in base.md"
    new = text.rstrip("\n") + "\n" + line
    size = len(new.encode())
    if size > BASE_BUDGET_BYTES and not force:
        return False, (
            f"base.md would be {size}B, over the {BASE_BUDGET_BYTES}B budget by "
            f"{size - BASE_BUDGET_BYTES}B. This is per-session fixed cost, so take "
            f"something out first, or pass --force if it genuinely earns its place."
        )
    p.write_text(new)
    return True, f"promoted into {p} ({size}B / {BASE_BUDGET_BYTES}B budget)"


def budget_report(name: str) -> list[tuple[str, int, int, bool]]:
    """(file, bytes, budget, over?) for the per-session fixed overhead."""
    d = paths.agent_dir(name)
    out = []
    for fn, budget in (("base.md", BASE_BUDGET_BYTES), ("ROLE.md", ROLE_BUDGET_BYTES)):
        p = d / fn
        size = p.stat().st_size if p.exists() else 0
        out.append((fn, size, budget, size > budget))
    return out
