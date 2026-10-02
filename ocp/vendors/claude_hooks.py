"""Claude's StateHooks: how RUNNING and IDLE reach ocp, and the shared settings
file that carries them.

Keep Orca's Claude agent hooks mirrored into our shared settings.

Orca integrates with Claude Code through hooks in ~/.claude/settings.json,
not through statusLine (established in Phase 0, probe J0). Those hooks report
to the Orca runtime over the ORCA_AGENT_HOOK_* environment variables that Orca
injects into each terminal it launches.

Our agents run with a per-account CLAUDE_CONFIG_DIR, so unless that directory's
settings.json carries the same hooks, our agents vanish from Orca's dashboard.
We therefore copy Orca's hook block into shared/.claude/settings.json and
re-apply it whenever the source changes.

Each account's settings.json is a symlink to the shared file, so fixing the
one shared copy propagates to every account.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ..infra import atomic, paths
from .claude import Claude

# ocp's own status line scripts, which it no longer installs (D11).
OLD_STATUSLINES = ("statusline.sh", "statusline-collect.sh")

# Claude Code's own notification switches, off in the shared settings only.
QUIET_NOTIFICATIONS = ("inputNeededNotifEnabled", "agentPushNotifEnabled")

# The tools a hook of ours is limited to, for the events that fire on every
# call: PreToolUse is ours for the memory budget (T247), the edit gate (T295)
# and ocp commands, so it looks at file writes and Bash only.
MATCHERS = {"PreToolUse": "Write|Edit|MultiEdit|NotebookEdit|Bash"}

# How we recognize a hook Orca installed. Every Orca hook command mentions one
# of these paths or variables.
ORCA_MARKERS = ("orca/agent-hooks", ".orca/agent-hooks", "ORCA_AGENT_HOOK", "orca-hook")


def source_settings() -> Path:
    """Where Orca installs its hooks: the user's default profile."""
    return paths.user_home() / ".claude" / "settings.json"


def _shared() -> Path:
    return paths.home() / "shared" / Claude.config_dir_name


def target_settings() -> Path:
    return _shared() / "settings.json"


def _read_json(p: Path) -> dict:
    try:
        value = json.loads(p.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _read_object(p: Path) -> dict:
    """The settings file as an object; {} if there is none. Raises ValueError,
    changing nothing, when it exists but is not readable JSON or not an object:
    it holds the human's settings too, and a rewrite from {} would lose them."""
    if not p.exists():
        return {}
    try:
        value = json.loads(p.read_text())
    except (OSError, ValueError) as e:
        raise ValueError(f"{p} could not be read ({e}); ocp changed nothing") from e
    if not isinstance(value, dict):
        raise ValueError(f"{p} is not a JSON object; ocp changed nothing")
    return value


def write_settings(target: Path, data: dict) -> None:
    """Replace the settings file in one step.

    Both writers of this file - Orca's mirror and our own hook install - go
    through here, so a half-written settings.json is not a state either of them
    can produce.
    """
    atomic.write_text(target, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def _within(child: Path, parent: Path) -> bool:
    return child == parent or parent in child.parents


def _as_path(entry: str) -> Path:
    """A settings path as Claude Code reads it: `~` expanded, symlinks followed."""
    return Path(entry).expanduser().resolve()


def _block(parent: dict, key: str) -> dict:
    """parent[key] as an object, replacing anything else (a string, a list) with
    an empty one: a hand-edited settings file must not crash the sandbox edits."""
    if not isinstance(parent.get(key), dict):
        parent[key] = {}
    return parent[key]


def allow_home_writes() -> tuple[bool, list[str]]:
    """Let a sandboxed agent write anywhere under OCP_HOME: (changed, blockers).

    The whole directory, not a list of files: SQLite creates its journal and
    WAL files beside the database, and `ask` writes into agents/, locks/ and
    wake/. Per-file entries fail on the first file nobody listed. A `denyWrite`
    that is exactly the home would win over the allow, so it goes. One on a
    parent guards more than the home: it stays, and is returned as a blocker
    for the founder to decide.
    """
    home = paths.home()
    real = home.resolve()
    target = target_settings()
    data = _read_object(target)
    fs = _block(_block(data, "sandbox"), "filesystem")
    before = json.dumps(data, sort_keys=True)
    allow = [e for e in fs.get("allowWrite", [])
             if not (isinstance(e, str) and _within(_as_path(e), real) and _as_path(e) != real)]
    if not any(isinstance(e, str) and _as_path(e) == real for e in allow):
        allow.append(str(home))
    fs["allowWrite"] = allow
    if "denyWrite" in fs:
        fs["denyWrite"] = [e for e in fs["denyWrite"]
                           if not (isinstance(e, str) and _as_path(e) == real)]
    blockers = [e for e in fs.get("denyWrite", [])
                if isinstance(e, str) and _within(real, _as_path(e))]
    if json.dumps(data, sort_keys=True) == before:
        return False, blockers
    target.parent.mkdir(parents=True, exist_ok=True)
    write_settings(target, data)
    return True, blockers


# The one list of commands run without asking (`permissions.allow`): read-only
# tools in full, and ocp per subcommand. Never a blanket `ocp:*` - merge,
# `hr apply` and the like must still ask.
#
# A `Bash(x:*)` rule allows every flag after the prefix, so a tool is listed
# only if no flag makes it run or write anything. No git prefix is allowed:
# `git grep -O<cmd>` runs a program and `git log/show/diff --output=<file>`
# writes a file. Only the exact forms below, which take no flags, are. The ocp
# reporting verbs take `--file <path>`, which reads a file the agent could
# already read.
SAFE_COMMANDS = (
    *(f"Bash({c}:*)" for c in ("tail", "head", "grep", "wc", "cat", "ls")),
    "Bash(git status)", "Bash(git log)", "Bash(git diff)",
    # read-only
    *(f"Bash(ocp {c}:*)" for c in ("inbox", "read", "status", "task list")),
    "Bash(ocp team)",
    # reporting
    *(f"Bash(ocp {c}:*)" for c in ("say", "progress", "task new")),
)


def allow_safe_commands() -> bool:
    """Add SAFE_COMMANDS to the shared settings' `permissions.allow`: True if it
    changed. Entries already there, and every other key, are kept; a second run
    changes nothing."""
    target = target_settings()
    data = _read_object(target)
    perms = _block(data, "permissions")
    allow = perms.get("allow", [])
    if not isinstance(allow, list):
        allow = []
    missing = [c for c in SAFE_COMMANDS if c not in allow]
    if not missing:
        return False
    perms["allow"] = [*allow, *missing]
    target.parent.mkdir(parents=True, exist_ok=True)
    write_settings(target, data)
    return True


def allow_home_socket() -> bool:
    """Let a sandboxed agent connect to ocp's unix socket: True if it changed.

    Connecting to a socket under the home fails with EPERM in a sandbox unless
    `sandbox.network.allowUnixSockets` names it. Only that one path is added;
    entries already there are kept, and a second run changes nothing.
    """
    sock = paths.socket_file()
    target = target_settings()
    data = _read_object(target)
    net = _block(_block(data, "sandbox"), "network")
    allowed = net.get("allowUnixSockets", [])
    if not isinstance(allowed, list):
        allowed = []
    if any(isinstance(e, str) and _as_path(e) == sock.resolve() for e in allowed):
        return False
    net["allowUnixSockets"] = [*allowed, str(sock)]
    target.parent.mkdir(parents=True, exist_ok=True)
    write_settings(target, data)
    return True


def unsandboxed_commands() -> list[str]:
    """The shared settings' `sandbox.excludedCommands` entries that start with the ocp command: those
    run outside every agent's sandbox, so the session-token folder's read-deny and the protected
    store's write protection do not bind them."""
    try:
        sandbox = _read_object(target_settings()).get("sandbox")
    except ValueError:
        return []
    excluded = sandbox.get("excludedCommands") if isinstance(sandbox, dict) else None
    return [e for e in excluded or [] if isinstance(e, str) and e.split(" ", 1)[0] == "ocp"]


def _is_orca_entry(entry: dict) -> bool:
    cmd = entry.get("command")
    return isinstance(cmd, str) and any(m in cmd for m in ORCA_MARKERS)


def _orca_groups(settings: dict) -> dict[str, list]:
    """event name -> matcher groups, keeping only Orca's hook entries."""
    out: dict[str, list] = {}
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return out
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            continue
        keep = []
        for g in groups:
            if not isinstance(g, dict):
                continue
            entries = [h for h in g.get("hooks", []) if isinstance(h, dict) and _is_orca_entry(h)]
            if entries:
                ng = {k: v for k, v in g.items() if k != "hooks"}
                ng["hooks"] = entries
                keep.append(ng)
        if keep:
            out[event] = keep
    return out


def _strip_orca(settings: dict) -> dict:
    """Drop Orca hooks we mirrored previously, leaving everything else alone.

    Identification is structural (the command marker), so this stays idempotent
    without tagging entries with keys Claude Code might reject.
    """
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings
    new_hooks = {}
    for event, groups in hooks.items():
        if not isinstance(groups, list):
            new_hooks[event] = groups
            continue
        keep = []
        for g in groups:
            if not isinstance(g, dict):
                keep.append(g)
                continue
            entries = [
                h for h in g.get("hooks", []) if not (isinstance(h, dict) and _is_orca_entry(h))
            ]
            if entries:
                ng = {k: v for k, v in g.items() if k != "hooks"}
                ng["hooks"] = entries
                keep.append(ng)
        if keep:
            new_hooks[event] = keep
    if new_hooks:
        settings["hooks"] = new_hooks
    else:
        settings.pop("hooks", None)
    return settings


def plan() -> tuple[dict, dict, bool]:
    """Return (orca hooks at source, orca hooks in shared, drifted?)."""
    src = _orca_groups(_read_json(source_settings()))
    dst = _orca_groups(_read_json(target_settings()))
    return src, dst, src != dst


def sync(quiet: bool = False) -> bool:
    """Mirror Orca's hooks into shared settings. Returns True if it changed."""
    src, _, drift = plan()
    if not drift:
        return False

    target = target_settings()
    target.parent.mkdir(parents=True, exist_ok=True)
    settings = _strip_orca(_read_json(target) if target.exists() else {})

    if src:
        hooks = settings.setdefault("hooks", {})
        for event, groups in src.items():
            hooks[event] = list(groups) + list(hooks.get(event, []))

    write_settings(target, settings)
    if not quiet:
        groups = sum(len(g) for g in src.values())
        print(f"synced Orca hooks: {len(src)} events, {groups} groups -> {target}")
    return True


def watch(interval: float = 5.0, once: bool = False) -> None:
    """Watch the source settings and re-apply whenever Orca's hooks change.

    This polls mtime. Hook changes are rare and the file is small, so polling
    is enough and avoids pulling in an fsevents dependency.
    """
    src = source_settings()
    last = None
    while True:
        try:
            current = src.stat().st_mtime_ns if src.exists() else None
        except OSError:
            current = None
        if current != last:
            last = current
            if sync(quiet=True):
                print(f"[{time.strftime('%H:%M:%S')}] Orca hooks changed - mirrored into shared")
        if once:
            return
        time.sleep(interval)


def _is_our_hook(command: object, event: str) -> bool:
    """Is this entry ours, whatever path it was written with?

    `ocp` is on PATH as a symlink into the repo, so the same installation spells
    itself two ways. Matching the exact string meant a second entry was added
    the first time the spelling changed, and then every hook fired twice - two
    events recorded per turn, and two of everything downstream.
    """
    if not isinstance(command, str):
        return False
    parts = command.split()
    return (len(parts) >= 3 and parts[-2:] == ["hook", event]
            and os.path.basename(parts[0]) == "ocp")


def ensure_shared() -> Path:
    """Create shared/.claude and its settings.json.

    Refuses, changing nothing, when settings.json exists but cannot be read
    as a JSON object: it holds the human's settings too, and rewriting it
    from {} would lose them.
    """
    shared = _shared()
    settings = shared / "settings.json"
    current = {}
    if settings.exists():
        try:
            current = json.loads(settings.read_text())
        except (OSError, ValueError) as e:
            raise ValueError(f"{settings} could not be read ({e}); ocp changed nothing. "
                             "Fix or move it, then run this again.") from e
        if not isinstance(current, dict):
            raise ValueError(f"{settings} is not a JSON object; ocp changed nothing. "
                             "Fix or move it, then run this again.")

    shared.mkdir(parents=True, exist_ok=True)
    for sub in ("skills", "agents", "commands"):
        (shared / sub).mkdir(exist_ok=True)

    before = json.loads(json.dumps(current))
    ours = {str(shared / name) for name in OLD_STATUSLINES}

    # A statusLine pointing at a script just deleted would error in every
    # session. Anyone else's is left alone.
    line = current.get("statusLine")
    if isinstance(line, dict) and line.get("command") in ours:
        del current["statusLine"]
    # Ours overlaps Claude Code's own auto-continue, and section 4 says to pick
    # one. We pick ours, and doctor enforces that the choice stays made.
    current.setdefault("autoContinueAtUsageLimit", False)
    # Orca surfaces these for the human's own sessions; ours are worked through
    # ocp, so they stay off here unless someone turns them on in this file.
    for key in QUIET_NOTIFICATIONS:
        current.setdefault(key, False)
    if not settings.exists() or current != before:
        write_settings(settings, current)
    # Only once no setting points at them: deleted first, a failed write
    # would leave every session running a script that is gone.
    for cmd in ours:
        Path(cmd).unlink(missing_ok=True)
    return shared


def install(exe: str, wanted: set[str]) -> tuple[list[str], int]:
    """Add `exe hook <event>` for each of `wanted` to the shared settings, next
    to Orca's mirrored hooks: (events added, duplicate entries removed)."""
    target = target_settings()
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(target.read_text()) if target.exists() else {}
    block = data.setdefault("hooks", {})
    added, pruned = [], 0
    for event in sorted(wanted | set(block)):
        groups = block.get(event)
        if groups is None:
            if event not in wanted:
                continue
            groups = block.setdefault(event, [])
        # Ours, in whatever spelling, collapses to exactly one entry.
        keep, seen = [], False
        for g in groups:
            if not isinstance(g, dict):
                keep.append(g)
                continue
            entries = g.get("hooks", [])
            mine = [h for h in entries
                    if isinstance(h, dict) and _is_our_hook(h.get("command"), event)]
            if not mine:
                keep.append(g)
                continue
            if seen or len(entries) > len(mine):
                g["hooks"] = [h for h in entries if h not in mine]
                pruned += len(mine)
                if g["hooks"]:
                    keep.append(g)
                continue
            seen = True
            if event in MATCHERS:
                g["matcher"] = MATCHERS[event]
            keep.append(g)
        groups[:] = keep
        if not seen and event in wanted:
            groups.append({**({"matcher": MATCHERS[event]} if event in MATCHERS else {}),
                           "hooks": [{"type": "command",
                                      "command": f"{exe} hook {event}", "timeout": 10}]})
            added.append(event)
    write_settings(target, data)
    return added, pruned


def missing(wanted: set[str]) -> set[str]:
    """Which of `wanted` have no entry of ours in the shared settings -
    read-only, using the same recognition `install()` writes with
    (`_is_our_hook`)."""
    data = _read_json(target_settings())
    have = set()
    for event, groups in data.get("hooks", {}).items():
        if event not in wanted or not isinstance(groups, list):
            continue
        for g in groups:
            if not isinstance(g, dict):
                continue
            if any(isinstance(h, dict) and _is_our_hook(h.get("command"), event)
                   for h in g.get("hooks", [])):
                have.add(event)
                break
    return wanted - have


def shared_problems() -> list[tuple[str, str]]:
    """(what, detail) for each way the shared settings would break a session."""
    s = target_settings()
    if not s.exists():
        return [("shared/.claude/settings.json missing", "")]
    try:
        line = json.loads(s.read_text()).get("statusLine", {})
    except json.JSONDecodeError:
        line = {}
    # ocp installs no status line (D11); one of its old ones left in the slot
    # points at a script `ocp init` deletes.
    old = {str(_shared() / n) for n in OLD_STATUSLINES}
    if isinstance(line, dict) and line.get("command") in old:
        return [("shared statusLine is an old ocp script", "run `ocp init` to remove it")]
    return []


class ClaudeHooks:
    """`Claude.state_hooks()`: the module's functions, as the StateHooks
    Protocol names them."""

    source_settings = staticmethod(source_settings)
    target_settings = staticmethod(target_settings)
    plan = staticmethod(plan)
    sync = staticmethod(sync)
    watch = staticmethod(watch)
    install = staticmethod(install)
    missing = staticmethod(missing)
    allow_home_writes = staticmethod(allow_home_writes)
    allow_home_socket = staticmethod(allow_home_socket)
    unsandboxed_commands = staticmethod(unsandboxed_commands)
    allow_safe_commands = staticmethod(allow_safe_commands)
    ensure_shared = staticmethod(ensure_shared)
    shared_problems = staticmethod(shared_problems)
