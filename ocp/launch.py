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

from . import accounts, agents, status


def build(agent: str, extra: list[str] | None = None, force: bool = False) -> tuple[list[str], dict]:
    """Return (argv, env) ready to hand to execve."""
    acct = agents.account_of(agent)

    if not accounts.logged_in(acct):
        raise SystemExit(
            f"account '{acct}' is not logged in.\n"
            f"A human has to do this once (ocp never touches credentials):\n\n"
            f"  {accounts.login_command(acct)}\n"
        )

    # A missing status.json is the normal first boot and must not block, or
    # nothing could ever start: status.json is written by the session we are
    # trying to launch. A snapshot that exists but is stale is a different
    # thing - it is a frozen reading that may be hiding a full account.
    st = status.read(acct)
    if not force and st.exists:
        if st.stale:
            age = "unknown age" if st.age_s is None else f"{int(st.age_s)}s old"
            raise SystemExit(
                f"account '{acct}' has a stale status.json ({age}); its usage cannot be "
                f"trusted - refusing to start.\n"
                f"Run the account once to refresh it, or use --force to override."
            )
        if st.pct_5h is not None and st.pct_5h >= status.PCT_5H_BLOCK:
            raise SystemExit(
                f"account '{acct}' is at {st.pct_5h}% of the 5h limit "
                f"(>= {status.PCT_5H_BLOCK}%) - refusing to start.\n"
                f"resets at {st.reset_5h}; use --force to override"
            )

    claude = shutil.which("claude")
    if not claude:
        raise SystemExit("cannot find claude on PATH")

    argv = [claude]
    model = agents.read(agent).get("model")
    if model:
        argv += ["--model", str(model)]
    argv += list(extra or [])

    return argv, accounts.env_for(acct, agent)


def exec_agent(agent: str, extra: list[str] | None = None, force: bool = False) -> None:
    argv, env = build(agent, extra, force)
    sys.stdout.flush()
    os.execve(argv[0], argv, env)


def command_for(agent: str) -> str:
    """The --command string to hand to Orca."""
    exe = os.environ.get("OCP_BIN") or shutil.which("ocp") or "ocp"
    return f"{exe} launch {agent}"
