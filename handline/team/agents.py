"""Agent registration (design section 3, team model section 7).

The agent name is the join key across every system (section 2): the worktree
name, the Orca task label and the folder name are all the same string.
"""
from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

from ..infra import paths, protected, sealed, textsafe
from ..session import triggers
from ..infra.yamlcompat import ConfigError, dump, is_plain_text, load, refuse_unsafe_values

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


MAX_EXTRA_WORKTREES = 2


def extra_worktrees(cfg: dict) -> int:
    """How many worktrees an agent has besides its first (0 to MAX_EXTRA_WORKTREES), from agent.yaml `extra_worktrees`. Only a real
    count turns one on: an absent, null, negative or garbage value reads as none, and a large one as the most there is."""
    v = cfg.get("extra_worktrees")
    if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
        return 0
    return min(v, MAX_EXTRA_WORKTREES)


def skip_permissions(cfg: dict) -> bool:
    """Whether this agent's sessions start without asking before they act.
    Only a real true turns it on: an absent, null, or garbage value ("yes", 1,
    "false") reads as off, so a bad config never grants what was not asked."""
    return cfg.get("skip_permissions") is True


def chrome(cfg: dict) -> bool:
    """Whether this agent's sessions start with the browser integration
    (`--chrome`) instead of `--no-chrome`. Only a real true turns it on."""
    return cfg.get("chrome") is True


def mcp_config_text(text: str) -> str:
    """`text` as one line of JSON, if it is a {"mcpServers": {...}} object (what
    `--mcp-config` takes); ValueError otherwise."""
    data = json.loads(text)
    if not (isinstance(data, dict) and isinstance(data.get("mcpServers"), dict)):
        raise ValueError('not a {"mcpServers": {...}} object')
    return json.dumps(data, separators=(",", ":"))


def mcp_config_args(cfg: dict) -> list[str]:
    """This agent's own MCP servers (T885), as `--mcp-config <json>`. They come
    from agent.yaml, which is sealed, not from a project .mcp.json: claude holds
    those until someone approves them in each account's own config, and an
    agent-writable file would be a way to run any command outside the sandbox.
    A value that is not a valid config adds nothing."""
    v = cfg.get("mcp_config")
    if not isinstance(v, str):
        return []
    try:
        return ["--mcp-config", mcp_config_text(v)]
    except ValueError:        # JSONDecodeError is one
        return []


BROWSER_SERVER = "browser"
DEFAULT_BROWSER_PROFILE = "Profile 17"
_ORIGIN = re.compile(r"https?://[A-Za-z0-9.-]+(:[0-9]+)?")
_ORIGINS_ARG = re.compile(r'--allowed-origins "([^"]*)"')
_PROFILE_ARG = re.compile(r'--profile-dir-name "([^"]*)"')


def _browser_command(cfg: dict) -> str:
    """The shell line of the agent's own `browser` MCP server, or ""."""
    try:
        servers = json.loads(cfg.get("mcp_config") or "{}")["mcpServers"]
        return str(servers[BROWSER_SERVER]["args"][-1])
    except (ValueError, KeyError, IndexError, TypeError):
        return ""


def browser_mode(cfg: dict) -> str:
    """How this agent browses: "chrome" (Claude in Chrome), "playwright" (its
    own sandboxed `browser` MCP server with a site list), "custom" (a `browser`
    server set by hand that has none, which is left alone) or "off"."""
    if chrome(cfg):
        return "chrome"
    command = _browser_command(cfg)
    if not command:
        return "off"
    return "playwright" if _ORIGINS_ARG.search(command) else "custom"


def browser_origins(cfg: dict) -> list[str]:
    """The sites its Playwright server may open."""
    m = _ORIGINS_ARG.search(_browser_command(cfg))
    return [o for o in m.group(1).split(";") if o] if m else []


def parse_origins(text: str) -> list[str]:
    """The origins in `text` (separated by spaces, commas, semicolons or lines);
    ValueError for one that is not http(s)://host[:port]. They end up inside a
    shell line, so nothing else is let through."""
    out = []
    for o in text.replace(",", " ").replace(";", " ").split():
        o = o.rstrip("/")
        if not _ORIGIN.fullmatch(o):
            raise ValueError(f"{o!r} is not a site like https://example.com")
        if o not in out:
            out.append(o)
    return out


def check_origin(text: str, others: list[str]) -> str:
    """`text` as one origin, if it is one and `others` does not have it; ValueError saying why not."""
    sites = parse_origins(text)
    if len(sites) != 1:
        raise ValueError("write one site, like https://example.com")
    if sites[0].lower() in (o.lower() for o in others):
        raise ValueError(f"{sites[0]} is already in the list")
    return sites[0]


def browser_mcp_config(cfg: dict, mode: str, origins: list[str]) -> str:
    """The `mcp_config` text for `mode`: the agent's other MCP servers as they
    are, and its `browser` server set to `origins` for "playwright" or removed
    otherwise. "" when nothing is left."""
    try:
        data = json.loads(cfg.get("mcp_config") or "{}")
    except ValueError:
        data = {}
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    servers = dict(servers) if isinstance(servers, dict) else {}
    old = _browser_command(cfg)
    found = _PROFILE_ARG.search(old)
    profile = found.group(1) if found else DEFAULT_BROWSER_PROFILE
    if mode == "custom":
        raise ValueError("a custom browser config is set in a terminal, not here")
    servers.pop(BROWSER_SERVER, None)    # any mode picked replaces a server set by hand
    if mode == "playwright":
        if not origins:
            raise ValueError("name at least one site for the sandboxed browser")
        servers[BROWSER_SERVER] = {"command": "sh", "args": ["-c", (
            f'PLAYWRIGHT_MCP_EXTENSION_TOKEN=$(cat {shlex.quote(str(paths.home() / "browser" / "token"))}) '
            f'exec {shlex.quote(str(paths.home() / "browser" / "node_modules" / ".bin" / "playwright-mcp"))} --extension '
            f'--profile-dir-name "{profile}" --allowed-origins "{";".join(origins)}"')]}
    return mcp_config_text(json.dumps({"mcpServers": servers})) if servers else ""


_SKIP_NS = "skip_permissions"


def skip_permissions_granted(agent: str) -> bool:
    """Whether the founder turned it on, through `register` from a terminal or
    the screen. agent.yaml alone never does: a file every agent's sandbox can
    write is not a record of who asked, so the grant is kept in the protected
    store, where an agent cannot write."""
    return protected.get(_SKIP_NS, agent) == "granted"


def skip_permissions_in_effect(agent: str, cfg: dict) -> bool:
    """What a launch will do: configured, and granted."""
    return skip_permissions(cfg) and skip_permissions_granted(agent)


# Claude's permission modes. "bypass" is the old skip_permissions switch: `mode: bypass` is what
# `register` writes as `skip_permissions: true`, so one switch and one founder's grant stand behind both.
MODES = ("default", "auto", "acceptEdits", "plan", "bypass")
DEFAULT_MODE = "auto"
_SANDBOX_NS = "sandbox_off"


def mode_of(agent: str, cfg: dict) -> str:
    """The permission mode a launch will use: bypass when skipping is configured and granted, else the
    agent.yaml `mode` when it names one, else auto (what every launch used before there was a choice).
    Anything else - an unknown name, a bypass the founder never granted - reads as the default, never as
    more than was asked."""
    if skip_permissions_in_effect(agent, cfg):
        return "bypass"
    v = cfg.get("mode")
    return v if isinstance(v, str) and v in MODES and v != "bypass" else DEFAULT_MODE


SANDBOX_OFF_GRANT = "granted"


def sandbox_off_granted(agent: str) -> bool:
    """Whether the founder turned this agent's sandbox off, from the protected store (an agent can write
    its own agent.yaml, so the file alone never turns it off). It stays until someone turns it back on:
    a session ending or a restart does not end it. (Stores written while it was a one-session lease hold
    `active:<launch stamp>`; any value is the grant.)"""
    return bool(protected.get(_SANDBOX_NS, agent))


def sandbox_on(agent: str, cfg: dict) -> bool:
    """Whether the sandbox is on: off only with `sandbox: false` in agent.yaml and the founder's grant."""
    return not (cfg.get("sandbox") is False and sandbox_off_granted(agent))


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


def is_structured(name: str) -> bool:
    """Is this agent a structured session, driven by the Go daemon (agent.yaml `structured: true`)?

    The Python daemon does not act on such an agent: no tick, delivery, wake, close or handoff. Everything that only reads or lists
    agents still sees it. The key is not `mode`, which is the permission mode (auto, plan, ...) a person sets and changes: a structured
    switch under that name would be overwritten by choosing a mode. Read the same way go/internal/compose KindFromYAML reads it, so the
    two daemons agree on whose the agent is: the first top-level `structured:` line, a trailing comment and quotes dropped, true or not.
    An agent.yaml that cannot be read is not structured."""
    try:
        text = (paths.agents_dir() / name / "agent.yaml").read_text()
    except (OSError, UnicodeDecodeError):
        return False
    for line in text.split("\n"):
        if line.startswith("structured:"):
            value = line[len("structured:"):].split("#", 1)[0].strip().strip("\"'")
            return value.lower() == "true"
    return False


def driven() -> list[str]:
    """The registered agents this daemon acts on: every one but the structured ones, which the Go daemon drives."""
    return [a for a in paths.list_agents() if not is_structured(a)]


def agent_file(name: str) -> Path:
    return paths.agent_dir(name) / "agent.yaml"


def write(name: str, cfg: dict) -> None:
    """Replace agent.yaml with `cfg` and seal it. The founder's session only."""
    sealed.require_founder()
    text = dump(cfg)
    agent_file(name).write_text(text)
    sealed.seal(sealed.agent_key(name), text)


def read(name: str) -> dict:
    p = agent_file(name)
    if not p.exists():
        raise FileNotFoundError(f"agent '{name}' is not registered ({p})")
    try:
        text = p.read_text()
    except UnicodeDecodeError as e:
        raise ConfigError(f"{p}: not valid UTF-8 ({e.reason})") from e
    sealed.check(sealed.agent_key(name), text, p)
    data = load(text)
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


def register(name: str, role: str = "", model: str | None = None,
             effort: str | None = None, weight: str | None = None,
             role_file: str | None = None, repo: str | None = None,
             vendor: str | None = None, context_tokens: int | None = None,
             skip_permissions: bool | None = None,
             chrome: bool | None = None,
             mcp_config: str | None = None,
             mode: str | None = None,
             sandbox: bool | None = None,
             extra_worktrees: int | None = None) -> Path:
    # A name is stored, drawn on every screen and typed into terminals: a control
    # byte in it (an escape, a newline) is refused here, where it would stay.
    # `textsafe.UNSAFE_IN_VALUE` is the same set this spelled out for itself.
    if not name or "/" in name or name.startswith(".") or textsafe.has_unsafe(name):
        raise ValueError(f"bad agent name: {name!r}")
    if not is_plain_text(name):
        raise ValueError(f"bad agent name: {name!r}. {NOT_TEXT}")
    if effort and effort not in VALID_EFFORT:
        raise ValueError(f"effort must be one of {', '.join(VALID_EFFORT)}, not {effort!r}")
    if vendor:
        from .. import vendors
        vendors.get(vendor)
    # First, so an agent's refusal leaves nothing half-written: the grant is
    # the founder's alone (infra/protected.py), and the key below is only
    # what a launch reads once it is there.
    sealed.require_founder()
    if mode is not None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {', '.join(MODES)}, not {mode!r}")
        skip_permissions = mode == "bypass"      # bypass is the old switch; any other mode turns it off
    if skip_permissions:
        protected.put(_SKIP_NS, name, "granted")
    elif skip_permissions is not None:
        protected.delete(_SKIP_NS, name)
    if sandbox is False:
        protected.put(_SANDBOX_NS, name, SANDBOX_OFF_GRANT)
    elif sandbox is not None:
        protected.delete(_SANDBOX_NS, name)
    d = paths.agent_dir(name)
    f = agent_file(name)
    text = None
    if not f.exists():
        # Built as a map and written by dump, which reads every line back: a
        # model of 4.5, or a weight of on, is quoted and stays the text typed.
        # Built before the folder exists, so a value it refuses leaves nothing.
        text = dump({"name": name, "model": model or DEFAULT_MODEL,
                     "effort": effort or DEFAULT_EFFORT, "weight": weight or "light",
                     "enabled": True, **({"repo": repo} if repo else {}),
                     **({"vendor": vendor} if vendor and vendor != DEFAULT_VENDOR else {}),
                     **({"skip_permissions": True} if skip_permissions else {}),
                     **({"mode": mode} if mode not in (None, "bypass", DEFAULT_MODE) else {}),
                     **({"sandbox": False} if sandbox is False else {}),
                     **({"chrome": True} if chrome else {}),
                     **({"extra_worktrees": min(extra_worktrees, MAX_EXTRA_WORKTREES)} if extra_worktrees and extra_worktrees > 0 else {}),
                     **({"mcp_config": mcp_config_text(mcp_config)} if mcp_config else {})})
    for sub in ("inbox", "journal", "memory"):
        (d / sub).mkdir(parents=True, exist_ok=True)

    if text is not None:
        f.write_text(text)
        sealed.seal(sealed.agent_key(name), text)
    else:
        # Already registered: update what was actually asked for and leave the
        # rest of the mapping alone. Re-serialize from the parsed document
        # rather than splicing lines - matching a key as a prefix broke on
        # "model : opus" and on any nested key of the same name.
        cfg = load(f.read_text())
        if not isinstance(cfg, dict):
            raise ConfigError(f"{f}: top level is not a map")
        if model:
            cfg["model"] = model
        if effort:
            cfg["effort"] = effort
        if weight:
            cfg["weight"] = weight
        if repo:
            cfg["repo"] = repo
        if context_tokens:
            cfg["context_tokens"] = context_tokens
        if extra_worktrees is not None:
            if extra_worktrees < 0 or extra_worktrees > MAX_EXTRA_WORKTREES:
                raise ValueError(f"extra_worktrees is 0 to {MAX_EXTRA_WORKTREES}, not {extra_worktrees}")
            if extra_worktrees:
                cfg["extra_worktrees"] = extra_worktrees
            else:
                cfg.pop("extra_worktrees", None)
        if skip_permissions is not None:
            if skip_permissions:
                cfg["skip_permissions"] = True
            else:
                cfg.pop("skip_permissions", None)
        if mode is not None:
            if mode in ("bypass", DEFAULT_MODE):
                cfg.pop("mode", None)
            else:
                cfg["mode"] = mode
        if sandbox is not None:
            if sandbox:
                cfg.pop("sandbox", None)
            else:
                cfg["sandbox"] = False
        if chrome is not None:
            if chrome:
                cfg["chrome"] = True
            else:
                cfg.pop("chrome", None)
        if mcp_config is not None:
            if mcp_config:
                cfg["mcp_config"] = mcp_config_text(mcp_config)
            else:
                cfg.pop("mcp_config", None)
        if vendor and vendor != DEFAULT_VENDOR:
            cfg["vendor"] = vendor
        cfg.setdefault("effort", DEFAULT_EFFORT)
        write(name, cfg)

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


def current_account(name: str, con=None) -> str | None:
    """The account this agent's session is on, or None when none is recorded.

    A session id belongs to one account's config directory, so a resume has no
    other answer to give than this. Placement is the daemon's pick at launch
    and is recorded then; an agent that has never run has no account.
    """
    from .. import db
    if con is not None:
        row = db.get_agent_state(con, name)
        return (row["account"] if row else None) or None
    own = db.connect()
    try:
        return current_account(name, own)
    finally:
        own.close()


@dataclass
class AccountReading:
    account: str
    # "recorded" - a launch, or the process check wrote it (the tick
    # reconciles it with the process); "reported" - the agent's own
    # fresh snapshot named it, better evidence than a merely assumed value;
    # "assumed" - a guess written before a launch had a chance to confirm it,
    # never a destination, never a fact.
    source: str


def account_reading(name: str, con=None) -> AccountReading | None:
    """Which account this agent's usage is actually landing on right now,
    and how sure of it we are - for reading, never for choosing where a
    launch or a resume goes (`choose_account`, `current_account` do that).
    None when nothing says.

    Three sources, tried in order: the db column when it is itself recorded
    rather than merely assumed there; failing that, the agent's own status
    snapshot, which reports where its running session actually is - fresher
    evidence than a column nothing has confirmed yet, and unavailable in
    exactly the way `reported_account` already refuses a stale or absent one
    (a closed agent's snapshot among them, stale by construction); failing
    that, whatever the column already holds if something is there at all.
    Only the first counts as fact.
    """
    from .. import db
    from ..usage import status
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
    return None


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


def budget_report(name: str) -> list[tuple[str, int, int, bool]]:
    """(file, bytes, budget, over?) for the per-session fixed overhead."""
    d = paths.agent_dir(name)
    out = []
    for fn, budget in (("base.md", BASE_BUDGET_BYTES), ("ROLE.md", ROLE_BUDGET_BYTES)):
        p = d / fn
        size = p.stat().st_size if p.exists() else 0
        out.append((fn, size, budget, size > budget))
    return out
