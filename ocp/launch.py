"""ocp launch - the execution layer (follows from Phase 0 verdict J4).

The design intended to inject CLAUDE_CONFIG_DIR through a worktree .envrc via
direnv. Phase 0 changed that premise:

  - Orca launches terminals with CLAUDE_CONFIG_DIR empty, so it is ours to set
  - `orca terminal create --command` takes an arbitrary shell string
  - `environmentRecipes` turned out to be for disposable remote machines, so it
    was never an env-injection fallback
  - direnv is not installed on this machine

So we use a launcher wrapper instead. Orca preserves the launch command across
hibernation and restore, which makes this recover better than .envrc would.

    orca terminal create --worktree path:<wt> --command "ocp launch backend"
"""
from __future__ import annotations

import os
import shutil
import sys

from . import accounts, agents, paths, status


def build(agent: str, extra: list[str] | None = None, force: bool = False) -> tuple[list[str], dict]:
    """Return (argv, env) ready to hand to execve."""
    acct = agents.account_of(agent)

    if not accounts.logged_in(acct):
        raise SystemExit(
            f"account '{acct}' is not logged in.\n"
            f"A human has to do this once (ocp never touches credentials):\n\n"
            f"  {accounts.login_command(acct)}\n"
        )

    # Only a *current* reading may block a launch.
    #
    # Neither a missing nor a stale snapshot can, and for the same reason: the
    # session we are about to start is the only thing that writes one. An idle
    # account goes stale after STALE_AFTER_S, so refusing on staleness meant
    # nothing could be started after a quarter hour of quiet - the same
    # deadlock as refusing on first boot, just delayed.
    #
    # Staleness is still fail-closed where it belongs, in status.assignable():
    # the daemon will not *assign* work on a reading it cannot trust. And an
    # account that really is full self-corrects in seconds - the status line
    # refreshes as soon as the session starts, and the daemon's trigger then
    # hands off on fresh data.
    st = status.read(acct)
    if st.exists and st.stale:
        age = "unknown age" if st.age_s is None else f"{int(st.age_s)}s"
        print(f"warning: account '{acct}' usage is {age} old and may be out of date; "
              f"the status line will refresh it on start.", file=sys.stderr)
    elif not force and st.exists and st.pct_5h is not None \
            and st.pct_5h >= status.PCT_5H_BLOCK:
        raise SystemExit(
            f"account '{acct}' is at {st.pct_5h}% of the 5h limit "
            f"(>= {status.PCT_5H_BLOCK}%) - refusing to start.\n"
            f"resets at {st.reset_5h}; use --force to override"
        )

    claude = shutil.which("claude")
    if not claude:
        raise SystemExit("cannot find claude on PATH")

    cfg = agents.read(agent)
    argv = [claude]
    model = cfg.get("model")
    if model:
        argv += ["--model", str(model)]
    # Always pass effort explicitly. Left unset it comes from whatever the
    # environment carries, which is how an agent ends up silently running at a
    # far more expensive level than intended.
    effort = str(cfg.get("effort") or agents.DEFAULT_EFFORT)
    if effort not in agents.VALID_EFFORT:
        raise SystemExit(
            f"agent '{agent}' has effort={effort!r}; must be one of "
            f"{', '.join(agents.VALID_EFFORT)}"
        )
    argv += ["--effort", effort]

    # The agent's standing context (team model section 1). base.md is injected
    # every session - that is what its 2KB budget is a budget for - and the
    # handoff is injected on resume so the agent picks up where it left off.
    preamble = context_for(agent)
    if preamble:
        argv += ["--append-system-prompt", preamble]
    argv += list(extra or [])

    return argv, accounts.env_for(acct, agent)


def context_for(agent: str) -> str:
    """ROLE.md + base.md + the latest handoff, as one system-prompt appendix."""
    d = paths.agent_dir(agent)
    parts = [f"You are `{agent}`, one agent in an orcapipe team. "
             f"Your name is also in $OCP_AGENT."]
    for fn, label in (("ROLE.md", "role"), ("base.md", "standing context")):
        p = d / fn
        if p.exists():
            text = p.read_text().strip()
            if text:
                parts.append(f"# Your {label} ({fn})\n{text}")
    handoff = d / "handoff" / "latest.md"
    if handoff.exists():
        text = handoff.read_text().strip()
        if text:
            parts.append(
                "# Where you left off (handoff/latest.md)\n"
                "You were interrupted and resumed. This is your own record.\n" + text
            )
    return "\n\n".join(parts)


def exec_agent(agent: str, extra: list[str] | None = None, force: bool = False) -> None:
    argv, env = build(agent, extra, force)
    sys.stdout.flush()
    os.execve(argv[0], argv, env)


def command_for(agent: str) -> str:
    """The --command string to hand to Orca."""
    exe = os.environ.get("OCP_BIN") or shutil.which("ocp") or "ocp"
    return f"{exe} launch {agent}"
