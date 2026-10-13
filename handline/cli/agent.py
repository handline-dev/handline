"""Commands on one agent: register, move, launch, resume, spawn, unstall."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from .. import actions
from ..team import agent_settings, agents, ask as askmod, hr
from ..daemon import handoff as dmn_handoff, lifecycle as dmn_lifecycle
from ..infra import daemonclient as dmn_client
from ..session import states as dmn_states
from .. import db
from ..session import launch, term
from ..infra import names, paths, protected
from ..team import spawn as spawnmod


def cmd_agent_move(args) -> int:
    """Move an agent to another account (a human's call): a live session now, a closed one at its next wake."""
    if names.getenv("OCP_AGENT"):
        print("a forced move ends a session: it is the founder's, from a terminal or "
              "the screen, not an agent's", file=sys.stderr)
        return 1
    if not args.yes:
        if not sys.stdin.isatty():
            print("refusing without a terminal to confirm in - pass --yes", file=sys.stderr)
            return 1
        if input(f"move {args.agent} to '{args.account}'? this records its handoff, "
                 f"ends its session if one runs, and starts a new one there (y/N): ").strip().lower() != "y":
            print("not moved")
            return 1
    con = db.connect()
    try:
        ok, msg = dmn_handoff.force_move(con, args.agent, args.account, "human (cli)", print)
    finally:
        con.close()
    print(msg, file=None if ok else sys.stderr)
    return 0 if ok else 1


def _propose_register(args) -> int:
    """`agent register` from an agent's session, where writing agent.yaml is the founder's: what it asks
    for is raised as HR proposals instead - a new agent as an `add`, with the settings an `add` does not
    take as a `settings` step of the same diff; an existing agent's as one `settings` proposal."""
    if args.mcp_config is not None:
        print("an agent's own MCP servers are the founder's to set, from a terminal or the screen, "
              "not an agent's", file=sys.stderr)
        return 1
    if args.role or args.role_file:
        print("a role is proposed with `hl hr context <name> --role-file FILE --why ...`", file=sys.stderr)
        return 1
    if not (args.why or "").strip():
        print("from an agent's session registering is the founder's to approve: say why with --why, "
              "and it is raised as a proposal", file=sys.stderr)
        return 1
    me, new = askmod.asking_agent(), args.name not in paths.list_agents()
    sets = {k: v for k, v in (("skip_permissions", args.skip_permissions), ("weight", args.weight),
                              ("browser", {"on": "chrome", "off": "off"}.get(args.chrome)))
            if v is not None}
    if not new:
        sets.update({k: v for k, v in (("model", args.model), ("effort", args.effort), ("repo", args.repo))
                     if v is not None})
        if not sets:
            print(f"{args.name} is already an agent and nothing to change was given", file=sys.stderr)
            return 1
        ok, said = hr.propose("settings", args.name, requester=me, why=args.why, sets=sets)
    elif sets:
        add = {k: v for k, v in (("kind", "add"), ("name", args.name), ("repo", args.repo), ("model", args.model),
                                 ("effort", args.effort), ("vendor", args.vendor)) if v is not None}
        ok, said = hr.propose_bundle([add, {"kind": "settings", "name": args.name, "sets": sets}], me, args.why)
    else:
        ok, said = hr.propose("add", args.name, requester=me, why=args.why, repo=args.repo, model=args.model,
                              effort=args.effort, vendor=args.vendor)
    print(said, file=None if ok else sys.stderr)
    return 0 if ok else 1


def cmd_agent_register(args) -> int:
    if protected.in_agent_session():
        return _propose_register(args)
    mcp_text = None
    if args.mcp_config is not None:
        if args.mcp_config == "off":
            mcp_text = ""
        else:
            try:
                mcp_text = agents.mcp_config_text(Path(args.mcp_config).expanduser().read_text())
            except (OSError, ValueError) as e:
                print(f"--mcp-config {args.mcp_config}: {e}", file=sys.stderr)
                return 1
    try:
        has_repo = bool(agents.read(args.name).get("repo"))
    except Exception:  # noqa: BLE001 - a new agent has no agent.yaml yet
        has_repo = False
    if not args.repo and not has_repo:
        print(f"{args.name} needs a repo: `hl agent register {args.name} --repo <path>`. Orca "
              f"makes its worktree there, and the daemon cannot start it without one.",
              file=sys.stderr)
        return 1
    d = agents.register(args.name, role=args.role, model=args.model,
                        effort=args.effort, weight=args.weight, role_file=args.role_file,
                        repo=args.repo, vendor=args.vendor,
                        skip_permissions=None if args.skip_permissions is None
                        else args.skip_permissions == "on",
                        chrome=None if args.chrome is None else args.chrome == "on",
                        mcp_config=mcp_text, extra_worktrees=args.extra_worktrees)
    print(f"registered agent '{args.name}': {d}")
    if args.skip_permissions:
        print(f"skip-permissions {args.skip_permissions}: applies from its next launch")
    if args.chrome:
        print(f"chrome {args.chrome}: applies from its next launch")
    if args.extra_worktrees is not None:
        print(f"extra-worktrees {args.extra_worktrees}: made when it is next started")
    if args.mcp_config:
        print(f"mcp-config {args.mcp_config}: applies from its next launch")
    print(f"launch it from {term.host_title()} with:")
    print("  " + term.manual("terminal", worktree="<selector>", command=launch.command_for(args.name)))
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
                      prompt_file=args.prompt_file, account=args.account, why=args.why,
                      by=args.by)
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


def cmd_agent_set(args) -> int:
    """Change an agent's settings. The founder's own change is written at once; an
    agent's is split by `agent_settings.ORDINARY`: those apply now, the rest wait in
    HR for the founder's approval."""
    values = {}
    for item in args.values:
        key, eq, value = item.partition("=")
        if not eq or not key:
            print(f"{item!r}: say it as setting=value, e.g. model=opus", file=sys.stderr)
            return 1
        values[key] = value
    if not protected.in_agent_session():
        ok, said = agent_settings.set_values(args.name, values)
        print(said)
        return 0 if ok else 1
    me = askmod.asking_agent()
    # Another agent's settings are never an agent's to write, so all of them wait for the founder.
    mine = args.name == askmod.current_agent()
    held = {k: v for k, v in values.items() if not mine or agent_settings.privileged(k)}
    now = {k: v for k, v in values.items() if k not in held}
    if held and not (args.why or "").strip():
        print(f"{', '.join(held)} needs the founder's approval: say why with --why", file=sys.stderr)
        return 1
    code = 0
    if now:
        reply = dmn_client.send({"verb": "agent_set", "agent": args.name, "values": now,
                                 "speaker": askmod.current_agent()})
        if reply is None:
            print("daemon not running, nothing changed")
            return 1
        print(reply.get("said", ""))
        code = 0 if reply.get("ok") else 1
    if held:
        ok, said = hr.propose("settings", args.name, requester=me, why=args.why, sets=held)
        print(said)
        code = code or (0 if ok else 1)
    return code
