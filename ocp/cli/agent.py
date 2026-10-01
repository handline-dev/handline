"""Commands on one agent: register, move, launch, resume, spawn, unstall."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .. import actions
from ..team import agents
from ..daemon import handoff as dmn_handoff, lifecycle as dmn_lifecycle, states as dmn_states
from .. import db
from ..session import launch
from ..infra import protected
from ..team import spawn as spawnmod


def cmd_agent_move(args) -> int:
    """Move an agent's live session to another account now (a human's call)."""
    if os.environ.get("OCP_AGENT"):
        print("a forced move ends a session: it is the founder's, from a terminal or "
              "the bridge, not an agent's", file=sys.stderr)
        return 1
    if not args.yes:
        if not sys.stdin.isatty():
            print("refusing without a terminal to confirm in - pass --yes", file=sys.stderr)
            return 1
        if input(f"move {args.agent} to '{args.account}'? this records its handoff, "
                 f"ends its session, and starts a new one there (y/N): ").strip().lower() != "y":
            print("not moved")
            return 1
    con = db.connect()
    try:
        ok, msg = dmn_handoff.force_move(con, args.agent, args.account, "human (cli)", print)
    finally:
        con.close()
    print(msg, file=None if ok else sys.stderr)
    return 0 if ok else 1


def cmd_agent_register(args) -> int:
    if args.skip_permissions and protected.in_agent_session():
        print("skipping permission prompts is the founder's to turn on or off, from a "
              "terminal or the bridge, not an agent's", file=sys.stderr)
        return 1
    if args.chrome and protected.in_agent_session():
        print("the browser integration is the founder's to turn on or off, from a "
              "terminal or the bridge, not an agent's", file=sys.stderr)
        return 1
    try:
        has_repo = bool(agents.read(args.name).get("repo"))
    except Exception:  # noqa: BLE001 - a new agent has no agent.yaml yet
        has_repo = False
    if not args.repo and not has_repo:
        print(f"{args.name} needs a repo: `ocp agent register {args.name} --repo <path>`. Orca "
              f"makes its worktree there, and the daemon cannot start it without one.",
              file=sys.stderr)
        return 1
    d = agents.register(args.name, role=args.role, model=args.model,
                        effort=args.effort, weight=args.weight, role_file=args.role_file,
                        repo=args.repo, vendor=args.vendor,
                        skip_permissions=None if args.skip_permissions is None
                        else args.skip_permissions == "on",
                        chrome=None if args.chrome is None else args.chrome == "on")
    print(f"registered agent '{args.name}': {d}")
    if args.skip_permissions:
        print(f"skip-permissions {args.skip_permissions}: applies from its next launch")
    if args.chrome:
        print(f"chrome {args.chrome}: applies from its next launch")
    print("launch it from Orca with:")
    print(f'  orca terminal create --worktree <selector> --command "{launch.command_for(args.name)}"')
    return 0


def cmd_resume(args) -> int:
    con = db.connect()
    try:
        ok, msg = dmn_lifecycle.wake_agent(con, args.agent, print)
        if ok:
            db.set_agent_state(con, args.agent, dmn_states.QUEUED, wake_at=None,
                               detail="resumed by hand")
        print(msg)
    finally:
        con.close()
    return 0 if ok else 1


# ---------------------------------------------------------------- launch
def cmd_launch(args) -> int:
    launch.exec_agent(args.agent, args.extra, force=args.force,
                      prompt_file=args.prompt_file, account=args.account)
    return 0  # unreachable when execve succeeds


def cmd_spawn(args) -> int:
    ok, msg = spawnmod.spawn(args.agent, Path(args.repo).resolve(), base=args.base,
                             prompt=args.prompt, dry_run=args.dry_run)
    print(msg)
    return 0 if ok else 1


def cmd_unstall(args) -> int:
    """A human clearing a stall - every kind but a wake stall the sweep has
    refuted by observing a session (section 8.6)."""
    con = db.connect()
    try:
        row = db.get_agent_state(con, args.agent)
        if not row:
            print(f"no state for {args.agent}")
            return 1
        if row["state"] != dmn_states.STALLED:
            print(f"{args.agent} is {row['state']}, not STALLED")
            return 1
        print(actions.unstall(con, args.agent))
    finally:
        con.close()
    return 0
