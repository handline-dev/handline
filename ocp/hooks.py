"""Keep Orca's Claude agent hooks mirrored into our shared settings.

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
import time
from pathlib import Path

from . import paths

# How we recognize a hook Orca installed. Every Orca hook command mentions one
# of these paths or variables.
ORCA_MARKERS = ("orca/agent-hooks", ".orca/agent-hooks", "ORCA_AGENT_HOOK", "orca-hook")


def source_settings() -> Path:
    """Where Orca installs its hooks: the user's default profile."""
    return paths.user_home() / ".claude" / "settings.json"


def target_settings() -> Path:
    return paths.shared_claude() / "settings.json"


def _read_json(p: Path) -> dict:
    try:
        value = json.loads(p.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def write_settings(target: Path, data: dict) -> None:
    """Replace the settings file in one step.

    Both writers of this file - Orca's mirror and our own hook install - go
    through here, so a half-written settings.json is not a state either of them
    can produce.
    """
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(target)


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
