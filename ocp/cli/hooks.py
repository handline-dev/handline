"""Claude Code hooks: hooks sync/status, `ocp hook`, install-hooks."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

from ..team import ask as askmod
from ..session import drift
from ..session import edittask, events, stopgate
from ..infra import paths, protected
from .. import vendors

# Every gate T298 rolls out warn-only, by the name `ocp hooks gate` takes.
GATES = {"stop-wait": stopgate, "edit-task": edittask}


# ---------------------------------------------------------------- hooks
def cmd_hooks_sync(args) -> int:
    for h in vendors.state_hooks():
        if args.watch:
            print(f"watching {h.source_settings()} - Ctrl-C to stop")
            h.watch(interval=args.interval)
            return 0
        if not h.sync():
            print("no change - already in sync.")
    return 0


def cmd_hooks_status(args) -> int:
    drifted = False
    for h in vendors.state_hooks():
        src, dst, drift = h.plan()
        print(f"source: {h.source_settings()}")
        print(f"  {len(src)} Orca hook events: {', '.join(sorted(src)) or '(none)'}")
        print(f"target: {h.target_settings()}")
        print(f"  {len(dst)} Orca hook events: {', '.join(sorted(dst)) or '(none)'}")
        print(f"drift: {'yes - sync needed' if drift else 'no'}")
        drifted = drifted or drift
    return 1 if drifted else 0


def cmd_hooks_gate(args) -> int:
    """Show or set one of T298's warn-only gates: refuse for real, or only log."""
    mod = GATES[args.name]
    if args.state is not None:
        try:
            mod.set_enforced(args.state == "on")
        except protected.NotTheFounder as e:
            print(e, file=sys.stderr)
            return 1
    on = mod.enforced()
    print(f"{args.name} gate: {'on' if on else 'off (warn-only)'}")
    return 0


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


def cmd_hook_install(args) -> int:
    """Add our event hooks to shared settings, next to Orca's mirrored ones."""
    exe = os.environ.get("OCP_BIN") or shutil.which("ocp") or str(paths.repo_root() / "bin" / "ocp")
    added, pruned = [], 0
    for h in vendors.state_hooks():
        a, p = h.install(exe, events.CAPTURED | events.ANSWERS | events.GUARDS)
        added += a
        pruned += p
    msg = []
    if added:
        msg.append(f"installed ocp event hooks for {len(added)} events")
    if pruned:
        msg.append(f"removed {pruned} duplicate entries")
    print("; ".join(msg) if msg else "already installed")
    return 0
