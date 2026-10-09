"""Claude Code hooks: hooks gate, `hl hook`, install-hooks."""
from __future__ import annotations

import subprocess
import sys

from ..team import ask as askmod
from ..session import edittask, events, stopgate
from ..infra import paths, protected
from .. import vendors

# Every gate T298 rolled out, by the name `ocp hooks gate` takes; on since T480.
GATES = {"stop-wait": stopgate, "edit-task": edittask}


# ---------------------------------------------------------------- hooks
def cmd_hooks_gate(args) -> int:
    """Show or set one of T298's gates: on refuses, off (the founder's choice) only logs."""
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


def cmd_hooks_go(args) -> int:
    """The founder's rollout switch for the Go hook (go/cmd/hl/hook.go): only an entry reading "on" lets it act."""
    if args.state is not None:
        try:
            protected.put("switches", "go_hook", args.state)
        except protected.NotTheFounder as e:
            print(e, file=sys.stderr)
            return 1
    on = (protected.get("switches", "go_hook") or "").strip() == "on"
    print(f"go hook: {'on - the Go hl answers the tool-call hooks' if on else 'off - Python answers every hook'}")
    return 0


def cmd_hook(args) -> int:
    if args.event == "install":
        print("`hl hook` records one event from stdin. To add ocp's hooks to the "
              "shared settings, run `hl install-hooks`.", file=sys.stderr)
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
    """Add our event hooks to shared settings."""
    exe = paths.ocp_exe()
    added, pruned = [], 0
    for h in vendors.state_hooks():
        a, p = h.install(exe, events.CAPTURED | events.ANSWERS | events.GUARDS)
        added += a
        pruned += p
    msg = []
    if added:
        msg.append(f"installed ocp event hooks for {len(added)} events")
    if pruned:
        msg.append(f"removed {pruned} duplicate or Orca entries")
    print("; ".join(msg) if msg else "already installed")
    return 0
