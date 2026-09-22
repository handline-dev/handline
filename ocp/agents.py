"""Agent registration (design section 3, team model section 7).

The agent name is the join key across every system (section 2): the worktree
name, the Orca task label and the folder name are all the same string.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import paths, textsafe, triggers
from .yamlcompat import ConfigError, dump, is_plain_text, load, refuse_unsafe_values

DEFAULT_MODEL = "sonnet"

# Effort is a cost lever as real as the model, and leaving it unset means it is
# inherited from whatever the environment happens to carry. Each agent declares
# its own, so a journaling agent cannot quietly run at the same effort as one
# doing architecture.
VALID_EFFORT = ("low", "medium", "high", "xhigh", "max")
DEFAULT_EFFORT = "medium"
# Which tool an agent is. Everything here drives `claude` today, and the field
# exists so a second one does not have to be guessed from the model name.
DEFAULT_VENDOR = "claude"
# The effort names, short enough for a column. Not abbreviations of whatever
# arrives: an unknown value is shown whole, because a name nobody recognises is
# the thing a reader needs to see, not something to dress up as one of ours.
EFFORT_SHORT = {"low": "low", "medium": "mid", "high": "high",
                "xhigh": "xhi", "max": "max"}


def parts(cfg: dict) -> tuple[str, str, str]:
    """(vendor, model, effort) as configured, defaults filled in."""
    return (str(cfg.get("vendor") or DEFAULT_VENDOR),
            str(cfg.get("model") or DEFAULT_MODEL),
            str(cfg.get("effort") or DEFAULT_EFFORT))


def context_tokens(cfg: dict) -> int:
    """The agent's own context-handoff threshold, configured or the default.

    Absent or falsy (missing, null, 0, "") reads as the default, the same as
    every other agent.yaml field here. Garbage - a string, a negative number,
    a bool - also reads as the default rather than as whatever `int()` would
    coerce it to: a value this permissive that read a bad config as 0 would
    hand off on every single tick, which is worse than ignoring it.
    """
    v = cfg.get("context_tokens")
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
        return triggers.CONTEXT_TOKENS
    return v


def kind(cfg: dict) -> str:
    """What an agent is, for a column: the model and its effort.

    The vendor is drawn only when it is not the one everything here has been
    until now - a column that says "claude" on every row of a single-vendor
    team is a column that trains a reader not to look at it, and the day a
    codex agent appears is the day the word starts carrying information.
    """
    vendor, model, effort = parts(cfg)
    out = f"{model} {EFFORT_SHORT.get(effort, effort)}"
    return out if vendor == DEFAULT_VENDOR else f"{vendor} {out}"


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
    # REFUSE, not strip (#1571's ruling): a value dump() would not have
    # written - a control character in repo, or in any field - must not
    # silently run as something other than what the file says. Safe now that
    # one agent's ConfigError here stalls that agent by name instead of
    # taking the daemon down with it (#1571's own fix, its prerequisite).
    refuse_unsafe_values(data, p)
    return data


# Said wherever a name is refused for what YAML would make of it. A name is
# written unquoted into team.yaml and agent.yaml, and `no`, `on`, `1` and a date
# are not text there: one would make the file refuse to load or load as a bool.
NOT_TEXT = ("It would not be read back as text from the YAML files it is written into "
            "(no, on, null, numbers and dates are not); choose another.")


def register(name: str, account: str, role: str = "", model: str | None = None,
             effort: str | None = None, weight: str | None = None,
             role_file: str | None = None, repo: str | None = None) -> Path:
    # A name is stored, drawn on every screen and typed into terminals: a control
    # byte in it (an escape, a newline) is refused here, where it would stay.
    # `textsafe.UNSAFE_IN_VALUE` is the same set this spelled out for itself.
    if not name or "/" in name or name.startswith(".") or textsafe.has_unsafe(name):
        raise ValueError(f"bad agent name: {name!r}")
    if not is_plain_text(name):
        raise ValueError(f"bad agent name: {name!r}. {NOT_TEXT}")
    if effort and effort not in VALID_EFFORT:
        raise ValueError(f"effort must be one of {', '.join(VALID_EFFORT)}, not {effort!r}")
    d = paths.agent_dir(name)
    f = agent_file(name)
    text = None
    if not f.exists():
        # Built as a map and written by dump, which reads every line back: a
        # model of 4.5, or a weight of on, is quoted and stays the text typed.
        # Built before the folder exists, so a value it refuses leaves nothing.
        text = dump({"name": name, "account": account, "model": model or DEFAULT_MODEL,
                     "effort": effort or DEFAULT_EFFORT, "weight": weight or "light",
                     "enabled": True, **({"repo": repo} if repo else {})})
    for sub in ("inbox", "journal"):
        (d / sub).mkdir(parents=True, exist_ok=True)

    if text is not None:
        f.write_text(text)
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
        if repo:
            cfg["repo"] = repo
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


def current_account(name: str, con=None) -> str:
    """The account a resume of this agent's session must run under.

    `account_of` is the preference in agent.yaml; this is where the session
    actually went, when that is recorded - and a session id belongs to one
    account's config directory, so a resume has no other answer to give
    regardless of what the running account might otherwise turn out to be.
    Falls back to the preference when nothing is recorded, the same silent
    substitution `account_reading` exists to stop happening anywhere that is
    asking about usage rather than about where `--resume` must point.
    """
    from . import db
    if con is not None:
        row = db.get_agent_state(con, name)
        return (row["account"] if row else None) or account_of(name)
    own = db.connect()
    try:
        row = db.get_agent_state(own, name)
        return (row["account"] if row else None) or account_of(name)
    finally:
        own.close()


@dataclass
class AccountReading:
    account: str
    # "recorded" - a launch, a move or the process check wrote it (the tick
    # reconciles it with the process); "reported" - the agent's own
    # fresh snapshot named it, better evidence than a merely assumed value;
    # "assumed" - a guess (agent.yaml's preference, or one written before a
    # launch had a chance to confirm it), never a destination, never a fact.
    source: str


def account_reading(name: str, con=None) -> AccountReading:
    """Which account this agent's usage is actually landing on right now,
    and how sure of it we are - for reading, never for choosing where a
    launch or a resume goes (`choose_account`, `current_account` do that).

    Three sources, tried in order: the db column when it is itself recorded
    rather than merely assumed there; failing that, the agent's own status
    snapshot, which reports where its running session actually is - fresher
    evidence than a column nothing has confirmed yet, and unavailable in
    exactly the way `reported_account` already refuses a stale or absent one
    (a closed agent's snapshot among them, stale by construction); failing
    that, whatever the column already holds if something is there at all;
    failing that, agent.yaml's preference. Only the first counts as fact.
    """
    from . import db, status
    if con is not None:
        row = db.get_agent_state(con, name)
    else:
        own = db.connect()
        try:
            row = db.get_agent_state(own, name)
        finally:
            own.close()
    if row and row["account"] and row["account_source"] != "assumed":
        return AccountReading(row["account"], "recorded")
    reported = status.reported_account(name)
    if reported:
        return AccountReading(reported, "reported")
    if row and row["account"]:
        return AccountReading(row["account"], "assumed")
    return AccountReading(account_of(name), "assumed")


def repo_of(name: str) -> str | None:
    """Where this agent works, when it has been told.

    The one fact the daemon is missing to start an agent that has none: it can
    make a worktree and a terminal, but not choose the repository. Without it,
    work sent to an agent with no terminal waits for a human to press a key,
    which is the one thing an agent cannot do for another agent.
    """
    try:
        repo = (read(name) or {}).get("repo")
    except Exception:  # noqa: BLE001
        return None
    return str(repo) if repo else None


def placed(name: str, con=None) -> tuple[str, dict]:
    """(the account the agent is on, its config), or the error that stops
    either. The AGENTS panel draws an agent's row only if this holds (else "config
    error") and `load.roster` counts one only if it does, so the two cannot
    place an agent differently."""
    return current_account(name, con), read(name)


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
