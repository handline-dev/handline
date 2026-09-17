"""ocp - command dispatch.

All logic lives here in the CLI. SKILL.md stays a stub and detail is pulled on
demand through `ocp guide <topic>` (KICKOFF rule 3).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import (accounts, agents, ask as askmod, daemon as daemonmod, dashboard, db,
               tui,
               digest, doctor,
               drift, events, fold as foldmod, guide, hooks, launch, paths, status,
               spawn as spawnmod, team, triggers)


# ---------------------------------------------------------------- init
def cmd_init(args) -> int:
    h = paths.home()
    h.mkdir(parents=True, exist_ok=True)
    (h / "agents").mkdir(exist_ok=True)
    (h / "accounts").mkdir(exist_ok=True)
    accounts.ensure_shared()

    gitignore = h / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("accounts/\ndb.sqlite\ndaemon.pid\n*.tmp\n")
    cfg = paths.config_file()
    if not cfg.exists():
        cfg.write_text("version: 1\n")
    hooks.sync()

    # Section 3 keeps the whole home in git, so digest generations, rollback
    # and change history come for free. accounts/ stays ignored.
    if not (h / ".git").exists() and shutil.which("git"):
        subprocess.run(["git", "init", "-q"], cwd=h, check=False)
        subprocess.run(["git", "add", "-A"], cwd=h, check=False)
        subprocess.run(["git", "commit", "-q", "-m", "ocp init"], cwd=h, check=False,
                       env=dict(os.environ, GIT_AUTHOR_NAME="ocp", GIT_COMMITTER_NAME="ocp",
                                GIT_AUTHOR_EMAIL="ocp@localhost",
                                GIT_COMMITTER_EMAIL="ocp@localhost"))
        print(f"initialized git repo in {h}")

    print(f"initialized {h}")
    print("next: ocp account register <name>, then a human runs claude /login once")
    return 0


# ---------------------------------------------------------------- account
def cmd_account_register(args) -> int:
    d = accounts.register(args.name, force=args.force)
    print(f"registered account '{args.name}': {d}")
    if not accounts.logged_in(args.name):
        print("\nnot logged in yet. A human has to run this once:")
        print(f"  {accounts.login_command(args.name)}")
        print("\n(ocp does not read or copy credentials - refresh tokens rotate on use)")
    return 0


def cmd_account_list(args) -> int:
    names = paths.list_accounts()
    if not names:
        print("no accounts registered.")
        return 0
    if args.json:
        out = []
        for a in names:
            st = status.read(a)
            allowed, why = st.assignable()
            out.append({
                "account": a, "logged_in": accounts.logged_in(a),
                "pct_5h": st.pct_5h, "pct_5h_effective": st.trusted_pct_5h,
                "pct_5h_source": st.pct_5h_source,
                "pct_7d": st.pct_7d, "ctx_pct": st.ctx_pct,
                "age_s": st.age_s, "assignable": allowed, "reason": why,
            })
        print(json.dumps(out, indent=2))
        return 0

    print(f"{'ACCOUNT':<14}{'LOGIN':<8}{'5h':>4}{'7d':>5}{'ctx':>6}{'AGE':>8}  ASSIGN")
    for a in names:
        st = status.read(a)
        allowed, why = st.assignable()
        cell = lambda v: "  -" if v is None else f"{v:>3}"  # noqa: E731
        age = "-" if st.age_s is None else f"{int(st.age_s)}s"
        # A figure we inferred is marked, never dressed up as one we read.
        mark = {"quiet": "*", "carried": "~"}.get(st.pct_5h_source, "")
        five = f"{cell(st.trusted_pct_5h)}{mark}"
        print(f"{a:<14}{'yes' if accounts.logged_in(a) else 'no':<8}"
              f"{five:>5}{cell(st.pct_7d):>5}{cell(st.ctx_pct):>6}{age:>8}  "
              f"{'yes' if allowed else 'NO':<4} {why}")
    print("  * inferred: nothing spent in this window   ~ carried: "
          "an earlier reading, window still open")
    return 0


# ---------------------------------------------------------------- agent
def cmd_agent_register(args) -> int:
    if args.account not in paths.list_accounts():
        print(f"warning: account '{args.account}' is not registered yet. "
              f"Run `ocp account register {args.account}` first.")
    d = agents.register(args.name, args.account, role=args.role, model=args.model,
                        effort=args.effort, weight=args.weight, role_file=args.role_file)
    print(f"registered agent '{args.name}' -> account '{args.account}': {d}")
    print("launch it from Orca with:")
    print(f'  orca terminal create --worktree <selector> --command "{launch.command_for(args.name)}"')
    return 0


def cmd_agent_show(args) -> int:
    try:
        cfg = agents.read(args.name)
    except Exception as e:  # noqa: BLE001
        print(f"error: {e}")
        return 1
    acct = str(cfg.get("account", ""))
    st = status.read_for_agent(args.name, acct)
    con = db.connect()
    try:
        row = db.get_agent_state(con, args.name)
        queued = len(db.queued_for(con, args.name))
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
        "pct_5h": st.pct_5h, "pct_5h_source": st.pct_5h_source, "ctx_pct": st.ctx_pct,
        "inbox_queued": queued,
        "budget": {fn: {"bytes": size, "limit": lim, "over": over}
                   for fn, size, lim, over in agents.budget_report(args.name)},
    }
    if args.json:
        print(json.dumps(info, indent=2, ensure_ascii=False))
        return 0
    for k, v in info.items():
        print(f"{k:<17}{v}")
    return 0


def cmd_base_promote(args) -> int:
    ok, msg = agents.promote_to_base(args.agent, args.item, force=args.force)
    print(msg)
    return 0 if ok else 1


def cmd_handoff_write(args) -> int:
    """Write a handoff now, without waiting for a trigger."""
    ok, msg = foldmod.fold(args.agent, use_model=not args.no_model)
    print(msg)
    print(f"handoff: {foldmod.handoff_path(args.agent)}")
    return 0 if ok else 1


def cmd_resume(args) -> int:
    con = db.connect()
    try:
        ok, msg = daemonmod.wake_agent(con, args.agent, print)
        if ok:
            db.set_agent_state(con, args.agent, daemonmod.QUEUED, wake_at=None,
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
    for n in names:
        try:
            acct = agents.account_of(n)
        except Exception as e:  # noqa: BLE001
            print(f"{n:<14} <error: {e}>")
            continue
        allowed, _ = status.read(acct).assignable()
        cfg = agents.read(n)
        budget = " ".join(f"{fn}:{size}B{'!' if over else ''}"
                          for fn, size, _, over in agents.budget_report(n))
        print(f"{n:<11} -> {acct:<7} {str(cfg.get('model')):<7} "
              f"{str(cfg.get('effort') or agents.DEFAULT_EFFORT):<7} "
              f"{'ok' if allowed else 'BLOCKED':<8} {budget}")
    return 0


# ---------------------------------------------------------------- launch
def cmd_launch(args) -> int:
    launch.exec_agent(args.agent, args.extra, force=args.force)
    return 0  # unreachable when execve succeeds


def cmd_command(args) -> int:
    print(launch.command_for(args.agent))
    return 0


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


# ---------------------------------------------------------------- checks
def cmd_doctor(args) -> int:
    r = doctor.run()
    print(r.render(color=not args.no_color))
    return 1 if r.failed else 0


def cmd_status(args) -> int:
    if args.table:
        return tui.run(once=True)
    picked, reasons = status.pick_account()
    if args.json:
        print(json.dumps({"picked": picked,
                          "candidates": [{"account": a, "reason": w} for a, w in reasons]},
                         indent=2, ensure_ascii=False))
        return 0 if picked else 1
    for a, why in reasons:
        print(f"  {a:<14} {why}")
    print()
    print(f"picked: {picked}" if picked
          else "picked: none - no account may take work (fail-closed)")
    return 0 if picked else 1


def cmd_drift(args) -> int:
    d = drift.check(Path(args.repo).resolve(), base=args.base, fetch=not args.no_fetch)
    print(d.summary())
    if d.overlap:
        print("\noverlapping files:")
        for f in d.overlap[:20]:
            print(f"  {f}")
    print(f"\nresume header:\n  {d.resume_header()}")
    return 0 if d.ok and not d.significant else 1


def cmd_spawn(args) -> int:
    ok, msg = spawnmod.spawn(args.agent, Path(args.repo).resolve(), base=args.base,
                             prompt=args.prompt, dry_run=args.dry_run)
    print(msg)
    return 0 if ok else 1


def cmd_top(args) -> int:
    if args.plain:
        return dashboard.top(interval=args.interval, once=args.once)
    return tui.run(interval=args.interval, once=args.once)


def cmd_guide(args) -> int:
    return guide.show(args.topic)


# ---------------------------------------------------------------- daemon
def cmd_daemon(args) -> int:
    return daemonmod.run(once=args.once, use_model=not args.no_model,
                         verbose=not args.quiet, max_ticks=args.max_ticks)


def cmd_daemon_status(args) -> int:
    pid = daemonmod.running_elsewhere()
    print(f"daemon: {'running, pid ' + str(pid) if pid else 'not running'}")
    con = db.connect()
    try:
        rows = db.all_agent_states(con)
        if not rows:
            print("no agent state recorded yet")
        else:
            print(f"\n{'AGENT':<14}{'STATE':<11}{'WAKE':<8}{'fail':>5}{'cont':>6}{'thr':>5}  DETAIL")
            now = int(time.time())
            for r in rows:
                wake = "-" if not r["wake_at"] else time.strftime("%H:%M", time.localtime(r["wake_at"]))
                if r["wake_at"] and r["wake_at"] <= now:
                    wake += "!"
                print(f"{r['agent']:<14}{r['state']:<11}{wake:<8}"
                      f"{r['failed_attempts']:>5}{r['continuations']:>6}{r['throttle_count']:>5}  "
                      f"{(r['detail'] or '')[:46]}")
        flight = db.in_flight_handoffs(con)
        if flight:
            print("\nin-flight handoffs (resume from these steps):")
            for h in flight:
                print(f"  {h['agent']:<14} step={h['step']:<11} state={h['state']:<10} "
                      f"deadline={time.strftime('%H:%M:%S', time.localtime(h['deadline']))}")
    finally:
        con.close()
    return 0


def cmd_unstall(args) -> int:
    """STALLED is a task defect and only a human clears it (section 8.6)."""
    con = db.connect()
    try:
        row = db.get_agent_state(con, args.agent)
        if not row:
            print(f"no state for {args.agent}")
            return 1
        if row["state"] != daemonmod.STALLED:
            print(f"{args.agent} is {row['state']}, not STALLED")
            return 1
        db.set_agent_state(con, args.agent, daemonmod.QUEUED, detail="cleared by a human")
        db.reset_counter(con, args.agent, "failed_attempts")
        db.reset_counter(con, args.agent, "throttle_count")
        print(f"{args.agent}: STALLED -> QUEUED")
    finally:
        con.close()
    return 0


def cmd_triggers(args) -> int:
    """Show what the daemon would decide right now, without acting."""
    print(f"{'AGENT':<14}{'ACCT':<10}{'5h':>4}{'ctx':>5}  FIRES  REASON")
    for a in paths.list_agents():
        try:
            acct = agents.account_of(a)
        except Exception as e:  # noqa: BLE001
            print(f"{a:<14} config error: {e}")
            continue
        st = status.read_for_agent(a, acct)
        t = triggers.evaluate(st.trusted_pct_5h, st.trusted_ctx_pct)
        cell = lambda v: "  -" if v is None else f"{v:>3}"  # noqa: E731
        print(f"{a:<14}{acct:<10}{cell(st.pct_5h):>4}{cell(st.ctx_pct):>5}  "
              f"{'YES' if t.fired else 'no ':<6} {t.reason}")
    return 0


# ---------------------------------------------------------------- record
def cmd_fold(args) -> int:
    targets = [args.agent] if args.agent else paths.list_agents()
    if not targets:
        print("no agents registered.")
        return 1
    bad = 0
    for a in targets:
        ok, msg = foldmod.fold(a, use_model=not args.no_model, model=args.model,
                               fold_account=args.account,
                               repo=Path(args.repo).resolve() if args.repo else None)
        print(msg)
        bad += 0 if ok else 1
    return 1 if bad else 0


def cmd_rollover(args) -> int:
    targets = [args.agent] if args.agent else paths.list_agents()
    for a in targets:
        _, msg = foldmod.rollover(a)
        print(msg)
    return 0


def cmd_digest(args) -> int:
    p = foldmod.journal_path(args.agent, args.date or foldmod.today())
    if not p.exists():
        print(f"no digest at {p}")
        return 1
    print(p.read_text())
    return 0


def cmd_handoff(args) -> int:
    p = foldmod.handoff_path(args.agent)
    if not p.exists():
        print(f"no handoff at {p}")
        return 1
    print(p.read_text())
    return 0


def cmd_events(args) -> int:
    con = db.connect()
    try:
        # Without --after this is "what just happened", so it starts at the
        # newest end. Reporting the page size as the total, from the oldest
        # end, told an operator there were 50 events when there were 187.
        if args.after:
            rows = db.events_after(con, args.after, agent=args.agent, limit=args.limit)
        else:
            rows = db.latest_events(con, agent=args.agent, limit=args.limit)
        if not rows:
            print("no events")
            return 0
        for r in rows:
            when = time.strftime("%m-%d %H:%M", time.localtime(r["ts"]))
            print(f"{r['id']:<6} {when} {r['agent']:<12} {r['kind']:<16} {(r['payload'] or '')[:70]}")
        total = db.count_events(con, agent=args.agent)
        print(f"\nshowing {len(rows)} of {total} events; "
              f"cursor for fold:<agent> advances only after a valid fold")
    finally:
        con.close()
    return 0


def cmd_hook(args) -> int:
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
    target = hooks.target_settings()
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(target.read_text()) if target.exists() else {}
    exe = os.environ.get("OCP_BIN") or shutil.which("ocp") or str(paths.repo_root() / "bin" / "ocp")
    block = data.setdefault("hooks", {})
    added = []
    for event in sorted(events.CAPTURED):
        groups = block.setdefault(event, [])
        cmd = f"{exe} hook {event}"
        if any(cmd == h.get("command")
               for g in groups if isinstance(g, dict)
               for h in g.get("hooks", []) if isinstance(h, dict)):
            continue
        groups.append({"hooks": [{"type": "command", "command": cmd, "timeout": 10}]})
        added.append(event)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(target)
    print(f"installed ocp event hooks for {len(added)} events" if added else "already installed")
    return 0


# ---------------------------------------------------------------- team / ask
def cmd_team_init(args) -> int:
    p = paths.team_file()
    if p.exists() and not args.force:
        print(f"{p} already exists (use --force to overwrite)")
        return 1
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(team.STARTER)
    print(f"wrote {p}")
    return 0


def cmd_team_check(args) -> int:
    try:
        t = team.Team.load()
    except Exception as e:  # noqa: BLE001
        print(f"team.yaml is invalid: {e}")
        return 1
    names = sorted(set(t.agents) | {a for m in t.teams.values() for a in (m or [])})
    print(f"{len(t.teams)} teams, {len(names)} agents, {len(t.edges)} edges, {len(t.deny)} denies")
    print(f"max_chain_depth={t.max_chain_depth} ask_rate_per_hour={t.ask_rate_per_hour}")
    registered = set(paths.list_agents())
    for n in names:
        mark = "ok" if n in registered else "not registered with ocp"
        print(f"  {n:<14} intake={t.intake_of(n):<10} {mark}")
    if args.matrix:
        print("\nrouting matrix (rows ask columns):")
        width = max(len(n) for n in names) + 2
        print(" " * width + "".join(f"{n[:6]:>8}" for n in names))
        for a in names:
            cells = []
            for b in names:
                d = t.route(a, b)
                cells.append("     -  " if a == b else ("   allow" if d.allowed else "    DENY"))
            print(f"{a:<{width}}" + "".join(cells))
    return 0


def cmd_ask(args) -> int:
    ok, msg, _ = askmod.ask(args.target, args.body, files=args.files,
                            requester=args.of, parent_id=args.parent,
                            mergeable=args.mergeable)
    # ask() already phrases its own refusals; do not prefix them again.
    print(msg)
    return 0 if ok else 1


def cmd_inbox(args) -> int:
    agent = args.agent or askmod.current_agent()
    if not agent:
        print("no agent given and OCP_AGENT is unset")
        return 1
    rows = askmod.inbox(agent, unread_only=not args.all)
    if not rows:
        print(f"{agent}: inbox empty")
        return 0
    for r in rows:
        print(f"#{r['id']:<5} from {r['requester']:<12} {r['state']:<9} "
              f"depth={r['chain_depth']} {r['body'][:60]}")
    return 0


def cmd_resolve(args) -> int:
    ok, msg = askmod.resolve(args.id, args.verdict, state=args.state)
    print(msg)
    return 0 if ok else 1


# ---------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    from . import __version__

    ap = argparse.ArgumentParser(prog="ocp", description="orcapipe - multi-account agent pipeline")
    ap.add_argument("--version", action="version", version=f"ocp {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create ~/.orcapipe and install shared settings").set_defaults(fn=cmd_init)

    acc = sub.add_parser("account", help="accounts").add_subparsers(dest="sub", required=True)
    p = acc.add_parser("register", help="create the account dir and link it to shared")
    p.add_argument("name")
    p.add_argument("--force", action="store_true", help="replace existing files with links")
    p.set_defaults(fn=cmd_account_register)
    p = acc.add_parser("list", help="accounts with usage")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_account_list)

    ag = sub.add_parser("agent", help="agents").add_subparsers(dest="sub", required=True)
    p = ag.add_parser("register", help="register an agent with its fixed account")
    p.add_argument("name")
    p.add_argument("--account", required=True)
    p.add_argument("--role", default="")
    p.add_argument("--model", default=None,
                   help="model for this agent; on re-registration, omit to keep the current one")
    p.add_argument("--effort", default=None, choices=agents.VALID_EFFORT,
                   help=f"effort level (default {agents.DEFAULT_EFFORT}); a real cost lever")
    p.add_argument("--weight", default=None, choices=["light", "heavy"])
    p.add_argument("--role-file", default=None, help="file to install as ROLE.md")
    p.set_defaults(fn=cmd_agent_register)
    ag.add_parser("list", help="list agents").set_defaults(fn=cmd_agent_list)
    p = ag.add_parser("show", help="everything known about one agent")
    p.add_argument("name")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_agent_show)

    p = sub.add_parser("launch", help="exec claude as this agent's account")
    p.add_argument("agent")
    p.add_argument("--force", action="store_true", help="start even when over the usage limit")
    p.add_argument("extra", nargs="*", help="passed through to claude")
    p.set_defaults(fn=cmd_launch)

    p = sub.add_parser("command", help="print the Orca --command string")
    p.add_argument("agent")
    p.set_defaults(fn=cmd_command)

    hk = sub.add_parser("hooks", help="mirror Orca's agent hooks").add_subparsers(dest="sub", required=True)
    p = hk.add_parser("sync", help="apply Orca's hooks to shared settings")
    p.add_argument("--watch", action="store_true", help="watch and auto-apply")
    p.add_argument("--interval", type=float, default=5.0)
    p.set_defaults(fn=cmd_hooks_sync)
    hk.add_parser("status", help="show drift").set_defaults(fn=cmd_hooks_status)

    p = sub.add_parser("doctor", help="verify preconditions")
    p.add_argument("--no-color", action="store_true")
    p.set_defaults(fn=cmd_doctor)

    p = sub.add_parser("status", help="which account may take work")
    p.add_argument("--json", action="store_true")
    p.add_argument("--table", action="store_true", help="the dashboard view")
    p.set_defaults(fn=cmd_status)

    tm = sub.add_parser("team", help="routing policy").add_subparsers(dest="sub", required=True)
    p = tm.add_parser("init", help="write a starter team.yaml")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_team_init)
    p = tm.add_parser("check", help="validate team.yaml")
    p.add_argument("--matrix", action="store_true", help="print the full routing matrix")
    p.set_defaults(fn=cmd_team_check)

    p = sub.add_parser("ask", help="send a request to another agent")
    p.add_argument("target")
    p.add_argument("body")
    p.add_argument("--files", default=None, help="comma-separated paths")
    p.add_argument("--of", default=None, help="requester (defaults to $OCP_AGENT)")
    p.add_argument("--parent", type=int, default=None, help="parent request id, for chain depth")
    p.add_argument("--mergeable", action="store_true",
                   help="fold into an already-queued ask to the same target")
    p.set_defaults(fn=cmd_ask)

    p = sub.add_parser("inbox", help="show queued requests for an agent")
    p.add_argument("agent", nargs="?")
    p.add_argument("--all", action="store_true")
    p.set_defaults(fn=cmd_inbox)

    p = sub.add_parser("resolve", help="close a request with a verdict")
    p.add_argument("id", type=int)
    p.add_argument("verdict")
    p.add_argument("--state", default="done", choices=["done", "rejected"])
    p.set_defaults(fn=cmd_resolve)

    p = sub.add_parser("fold", help="fold events into the digest (the one model call)")
    p.add_argument("agent", nargs="?")
    p.add_argument("--no-model", action="store_true", help="deterministic fold only")
    p.add_argument("--model", default="haiku")
    p.add_argument("--account", default=None, help="account to run the fold call on")
    p.add_argument("--repo", default=None, help="repo to read git facts from")
    p.set_defaults(fn=cmd_fold)

    p = sub.add_parser("rollover", help="midnight rollover (no model)")
    p.add_argument("agent", nargs="?")
    p.set_defaults(fn=cmd_rollover)

    p = sub.add_parser("digest", help="print an agent's digest")
    p.add_argument("agent")
    p.add_argument("--date", default=None)
    p.set_defaults(fn=cmd_digest)

    p = sub.add_parser("handoff", help="print an agent's handoff")
    p.add_argument("agent")
    p.set_defaults(fn=cmd_handoff)

    p = sub.add_parser("events", help="list recorded events")
    p.add_argument("--agent", default=None)
    p.add_argument("--after", type=int, default=0)
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(fn=cmd_events)

    p = sub.add_parser("hook", help="internal: record a hook event from stdin")
    p.add_argument("event")
    p.set_defaults(fn=cmd_hook)

    p = sub.add_parser("install-hooks", help="add ocp event hooks to shared settings")
    p.set_defaults(fn=cmd_hook_install)

    p = sub.add_parser("daemon", help="run the supervisor loop")
    p.add_argument("--once", action="store_true", help="one tick, then exit")
    p.add_argument("--max-ticks", type=int, default=None)
    p.add_argument("--no-model", action="store_true", help="never spend tokens on folds")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--foreground", action="store_true",
                   help="accepted for symmetry; the daemon always runs in the foreground")
    p.set_defaults(fn=cmd_daemon)

    p = sub.add_parser("ps", help="daemon and agent state")
    p.set_defaults(fn=cmd_daemon_status)

    p = sub.add_parser("triggers", help="what the daemon would decide right now")
    p.set_defaults(fn=cmd_triggers)

    p = sub.add_parser("unstall", help="clear STALLED (humans only)")
    p.add_argument("agent")
    p.set_defaults(fn=cmd_unstall)

    p = sub.add_parser("drift", help="has the base moved under this agent?")
    p.add_argument("repo")
    p.add_argument("--base", default="main")
    p.add_argument("--no-fetch", action="store_true")
    p.set_defaults(fn=cmd_drift)

    p = sub.add_parser("spawn", help="give an agent its own worktree and start it there")
    p.add_argument("agent")
    p.add_argument("--repo", required=True)
    p.add_argument("--base", default="main")
    p.add_argument("--prompt", default=None, help="first task to send once it is up")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(fn=cmd_spawn)

    p = sub.add_parser("top", help="live full-screen dashboard")
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--once", action="store_true", help="print one frame and exit")
    p.add_argument("--plain", action="store_true", help="scrolling output, no full screen")
    p.set_defaults(fn=cmd_top)

    bs = sub.add_parser("base", help="the per-session fixed context").add_subparsers(
        dest="sub", required=True)
    p = bs.add_parser("promote", help="add a fact to an agent's base.md")
    p.add_argument("agent")
    p.add_argument("--item", required=True)
    p.add_argument("--force", action="store_true", help="accept going over budget")
    p.set_defaults(fn=cmd_base_promote)

    p = sub.add_parser("write-handoff", help="fold and write a handoff now")
    p.add_argument("agent")
    p.add_argument("--no-model", action="store_true")
    p.set_defaults(fn=cmd_handoff_write)

    p = sub.add_parser("resume", help="hand a parked agent back its handoff")
    p.add_argument("agent")
    p.set_defaults(fn=cmd_resume)

    p = sub.add_parser("watch", help="alias for top")
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--once", action="store_true")
    p.add_argument("--plain", action="store_true")
    p.set_defaults(fn=cmd_top)

    p = sub.add_parser("guide", help="load detail on demand")
    p.add_argument("topic", nargs="?")
    p.set_defaults(fn=cmd_guide)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except SystemExit:
        raise
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
