"""Claude Code hooks: hooks sync/status, `ocp hook`, install-hooks."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

from .. import ask as askmod
from .. import drift
from .. import events
from .. import hooks
from .. import paths


# ---------------------------------------------------------------- hooks
def cmd_hooks_sync(args) -> int:
    if args.watch:
        print(f"watching {hooks.source_settings()} - Ctrl-C to stop")
        hooks.watch(interval=args.interval)
        return 0
    if not hooks.sync():
        print("no change - already in sync.")
    return 0


def cmd_hooks_status(args) -> int:
    src, dst, drift = hooks.plan()
    print(f"source: {hooks.source_settings()}")
    print(f"  {len(src)} Orca hook events: {', '.join(sorted(src)) or '(none)'}")
    print(f"target: {hooks.target_settings()}")
    print(f"  {len(dst)} Orca hook events: {', '.join(sorted(dst)) or '(none)'}")
    print(f"drift: {'yes - sync needed' if drift else 'no'}")
    return 1 if drift else 0


def cmd_hook(args) -> int:
    if args.event == "install":
        print("`ocp hook` records one event from stdin. To add ocp's hooks to the "
              "shared settings, run `ocp install-hooks`.", file=sys.stderr)
        return 1
    if args.event == "commit":
        agent = askmod.current_agent()
        if not agent:
            return 0
        repo = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True,
                              text=True).stdout.strip()
        sha = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                             text=True).stdout.strip()
        subject = subprocess.run(["git", "log", "-1", "--pretty=%s"], capture_output=True,
                                 text=True).stdout.strip()
        stat = subprocess.run(["git", "show", "--stat", "--oneline", "HEAD"], capture_output=True,
                              text=True).stdout.strip().splitlines()
        events.record_commit(agent, repo, sha, subject, stat[-1] if stat else "")
        return 0
    return events.record_from_stdin([args.event])


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


def cmd_hook_install(args) -> int:
    """Add our event hooks to shared settings, next to Orca's mirrored ones."""
    target = hooks.target_settings()
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(target.read_text()) if target.exists() else {}
    exe = os.environ.get("OCP_BIN") or shutil.which("ocp") or str(paths.repo_root() / "bin" / "ocp")
    block = data.setdefault("hooks", {})
    added, pruned = [], 0
    for event in sorted(events.CAPTURED | events.ANSWERS | set(block)):
        groups = block.get(event)
        if groups is None:
            if event not in events.CAPTURED | events.ANSWERS:
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
            keep.append(g)
        groups[:] = keep
        if not seen and event in events.CAPTURED | events.ANSWERS:
            groups.append({"hooks": [{"type": "command",
                                      "command": f"{exe} hook {event}", "timeout": 10}]})
            added.append(event)
    hooks.write_settings(target, data)
    msg = []
    if added:
        msg.append(f"installed ocp event hooks for {len(added)} events")
    if pruned:
        msg.append(f"removed {pruned} duplicate entries")
    print("; ".join(msg) if msg else "already installed")
    return 0
