"""Commands on one agent: register, move, show, launch, resume, spawn, unstall."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .. import actions
from ..team import agents
from ..daemon import handoff as dmn_handoff, lifecycle as dmn_lifecycle, states as dmn_states
from .. import db
from ..session import handoff as handoffmod
from ..session import launch
from ..usage import load
from ..infra import paths
from ..team import spawn as spawnmod
from ..usage import status
from ..infra import textsafe


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
    if args.skip_permissions and os.environ.get("OCP_AGENT"):
        print("skipping permission prompts is the founder's to turn on or off, from a "
              "terminal or the bridge, not an agent's", file=sys.stderr)
        return 1
    d = agents.register(args.name, role=args.role, model=args.model,
                        effort=args.effort, weight=args.weight, role_file=args.role_file,
                        repo=args.repo, vendor=args.vendor,
                        skip_permissions=None if args.skip_permissions is None
                        else args.skip_permissions == "on")
    print(f"registered agent '{args.name}': {d}")
    if args.skip_permissions:
        print(f"skip-permissions {args.skip_permissions}: applies from its next launch")
    print("launch it from Orca with:")
    print(f'  orca terminal create --worktree <selector> --command "{launch.command_for(args.name)}"')
    return 0


def cmd_agent_show(args) -> int:
    try:
        cfg = agents.read(args.name)
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}")
        return 1
    con = db.connect()
    try:
        row = db.get_agent_state(con, args.name)
        queued = len(db.queued_for(con, args.name))
        st = status.read_for_agent(args.name, agents.current_account(args.name, con))
    finally:
        con.close()
    info = {
        "agent": args.name, "config": cfg,
        "state": (row["state"] if row else "IDLE"),
        "wake_at": (row["wake_at"] if row else None),
        "terminal": (row["terminal"] if row else None),
        "failed_attempts": (row["failed_attempts"] if row else 0),
        "continuations": (row["continuations"] if row else 0),
        "throttle_count": (row["throttle_count"] if row else 0),
        "windows": st.window_report(), "ctx_pct": st.ctx_pct,
        "inbox_queued": queued,
        "budget": {fn: {"bytes": size, "limit": lim, "over": over}
                   for fn, size, lim, over in agents.budget_report(args.name)},
    }
    if args.json:
        print(textsafe.json_dumps(info, indent=2))
        return 0
    for k, v in info.items():
        print(f"{k:<17}{textsafe.oneline(str(v))}")
    return 0


def cmd_base_promote(args) -> int:
    ok, msg = agents.promote_to_base(args.agent, args.item, force=args.force)
    print(msg)
    return 0 if ok else 1


def cmd_handoff_write(args) -> int:
    """Record a handoff now, without waiting for a trigger."""
    con = db.connect()
    try:
        missing = handoffmod.record(con, args.agent)
        print(textsafe.text(db.get_handoff(con, args.agent) or ""))
    finally:
        con.close()
    if missing:
        print(f"could not read: {', '.join(missing)}")
    return 1 if missing else 0


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


def cmd_agent_list(args) -> int:
    names = paths.list_agents()
    if not names:
        print("no agents registered.")
        return 0
    loads = load.read_all()
    con = db.connect()
    try:
        for n in names:
            try:
                cfg = agents.read(n)
            except Exception as e:  # noqa: BLE001
                print(f"{n:<14} <error: {e}>")
                continue
            acct = agents.current_account(n, con)
            ld = loads.get(acct)
            allowed = acct and status.read(acct).takes_work(ld.active if ld else 0)[0]
            budget = " ".join(f"{fn}:{size}B{'!' if over else ''}"
                              for fn, size, _, over in agents.budget_report(n))
            print(f"{n:<11} -> {acct or '-':<7} {textsafe.oneline(str(cfg.get('model'))):<7} "
                  f"{textsafe.oneline(str(cfg.get('effort') or agents.DEFAULT_EFFORT)):<7} "
                  f"{('ok' if allowed else 'BLOCKED') if acct else '-':<8} {budget}")
    finally:
        con.close()
    return 0


# ---------------------------------------------------------------- launch
def cmd_launch(args) -> int:
    launch.exec_agent(args.agent, args.extra, force=args.force,
                      prompt_file=args.prompt_file, account=args.account)
    return 0  # unreachable when execve succeeds


def cmd_command(args) -> int:
    print(launch.command_for(args.agent))
    return 0


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
