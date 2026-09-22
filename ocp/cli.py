"""ocp - command dispatch.

Logic lives here, not in a library nobody reads: SKILL.md stays a stub and
detail is pulled on demand through `ocp guide <topic>` (KICKOFF rule 3). The
exception is what a human can also do from `ocp bridge` - `ocp/actions.py` holds
those, so the screen and the command line cannot come to differ about what
unstalling or closing an agent means.
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

from . import (accounts, actions, agents, ask as askmod, daemon as daemonmod, db,
               tui,
               digest, doctor,
               drift, events, fold as foldmod, guide, handoff as handoffmod, hooks, launch,
               load, observe as obs, paths, probe as probemod, service,
               hr, orca, status, spawn as spawnmod, team, textsafe, triggers, waiting)


# ---------------------------------------------------------------- login
def cmd_login(args) -> int:
    """Log an account in, or say who is logged in.

    `claude /login` is interactive - a browser and a code typed back - so this
    hands the terminal to claude with CLAUDE_CONFIG_DIR pointed at the account
    and gets out of the way. Where it cannot do that (no terminal, or claude
    not on PATH) it prints the command to run instead of pretending.
    """
    names = paths.list_accounts()
    if not args.account:
        if not names:
            print("no accounts registered. `ocp account add <name>` first.")
            return 1
        for a in names:
            state = "logged in" if accounts.logged_in(a) else "NOT logged in"
            print(f"{a:<12}{state:<14}{paths.account_claude(a)}")
        print(f"\nLog one in with: ocp login <account>")
        return 0

    action, message = accounts.login_plan(args.account, force=args.force)
    if action == "unknown":
        print(message, file=sys.stderr)
        return 1
    if action == "already":
        print(message)
        return 0

    claude = shutil.which("claude")
    if not claude or not sys.stdin.isatty():
        print(f"run this where you can type into it:\n\n  {message}\n")
        return 1
    print(f"handing the terminal to claude for '{args.account}' - "
          f"finish the login there, then come back.")
    env = accounts.env_for(args.account)
    os.execve(claude, [claude, "/login"], env)
    return 0     # unreachable: execve replaces this process


# ---------------------------------------------------------------- init
def cmd_init(args) -> int:
    h = paths.home()
    h.mkdir(parents=True, exist_ok=True)
    (h / "agents").mkdir(exist_ok=True)
    (h / "accounts").mkdir(exist_ok=True)
    accounts.ensure_shared()

    gitignore = h / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("accounts/\ndb.sqlite\nlocks/\nlogs/\n*.tmp\n")
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
    print("next: ocp account add <name> - it registers the account and logs it in")
    return 0


# ---------------------------------------------------------------- account
def cmd_account_register(args) -> int:
    d = accounts.register(args.name, force=args.force)
    print(f"registered account '{args.name}': {d}")
    if not accounts.logged_in(args.name):
        print(f"\nnot logged in yet. From a terminal you can type into:\n\n"
              f"  ocp login {args.name}\n")
    return 0


def _run_claude(argv: list[str], env: dict) -> int | None:
    claude = shutil.which(argv[0])
    if not claude:
        return None
    return subprocess.run([claude, *argv[1:]], env=env).returncode


def cmd_account_add(args) -> int:
    """Register an account and log it in, in one command."""
    if not args.no_login and (not shutil.which("claude") or not sys.stdin.isatty()):
        print(f"logging in needs a terminal you can type into. From one, run:\n\n"
              f"  ocp account add {args.name}\n", file=sys.stderr)
        return 1
    if not args.no_login:
        print("use an Anthropic account no other account here uses. "
              "Finish the login in claude, then exit it (/exit) to come back.\n")
    ok, msg = accounts.add_account(args.name, _run_claude, no_login=args.no_login)
    print(msg, file=None if ok else sys.stderr)
    return 0 if ok else 1


def cmd_account_remove(args) -> int:
    """Log an account out and take it off the roster."""
    def confirm() -> bool:
        if args.yes:
            return True
        if not sys.stdin.isatty():
            print("refusing without a terminal to confirm in - pass --yes", file=sys.stderr)
            return False
        return input(f"remove account '{args.name}'? type its name to confirm: "
                     ).strip() == args.name

    con = db.connect()
    try:
        ok, msg = accounts.remove_account(args.name, confirm, _run_claude, con)
    finally:
        con.close()
    print(msg, file=None if ok else sys.stderr)
    return 0 if ok else 1


def cmd_account_cap(args) -> int:
    if args.n is None:
        print(f"{args.name}: at most {load.cap_of(args.name)} agents mid-turn at once")
        return 0
    try:
        load.set_cap(args.name, args.n)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"{args.name}: at most {args.n} agents mid-turn at once")
    return 0


def _can_start(st, ld) -> tuple[bool, str]:
    """takes_work, and what the pick adds to it: a slot and a stretch worth starting."""
    ok, why = st.takes_work(ld.active if ld else 0)
    bad = status.unfit(st, ld) if ok else None
    return (False, bad) if bad else (ok, why)


def cmd_account_list(args) -> int:
    names = paths.list_accounts()
    if not names:
        print("no accounts registered.")
        return 0
    loads = load.read_all()
    con = db.connect()
    try:
        roster, unplaced = load.roster(con)
    finally:
        con.close()
    if args.json:
        out = []
        for a in names:
            st = status.read(a)
            allowed, why = _can_start(st, loads.get(a))
            assigned, running = roster.get(a, (0, 0))
            out.append({
                "account": a, "logged_in": accounts.logged_in(a),
                "agents_assigned": assigned, "agents_running": running,
                "agents_unplaced": unplaced,
                "pct_5h": st.pct_5h, "pct_5h_effective": st.trusted_pct_5h,
                "pct_5h_source": st.pct_5h_source,
                "pct_7d": st.pct_7d, "reset_7d": st.reset_7d, "ctx_pct": st.ctx_pct,
                "age_s": st.age_s, "assignable": allowed, "reason": why,
                "subscription_type": accounts.subscription_type(a),
            })
        print(json.dumps(out, indent=2))
        return 0

    aw = tui.ACCT_W["agents"]
    print(f"{'ACCOUNT':<14}{'LOGIN':<8}{'PLAN':<6}{tui.ACCT_HEAD['agents'].upper():<{aw}}{'5h':>5}{'7d':>5}  "
          f"{'7d resets':<12}{'ctx':>6}{'AGE':>8}  {'RUNWAY (est.)':<16}ASSIGN")
    for a in names:
        st = status.read(a)
        allowed, why = _can_start(st, loads.get(a))
        cell = lambda v: "  -" if v is None else f"{v:>3}"  # noqa: E731
        age = status.ago(st.age_s)
        resets = "-" if not st.reset_7d else time.strftime("%m-%d %H:%M", time.localtime(st.reset_7d))
        plan = accounts.subscription_type(a) or "-"
        # A figure we inferred is marked, never dressed up as one we read.
        shown, src = st.shown_pct_5h()
        five = f"{cell(shown)}{src}"
        # The bridge's cell, in words only: one definition, one format.
        agents_ = textsafe.oneline(tui.agents_cell(roster.get(a, (0, 0)), unplaced))
        print(f"{a:<14}{'yes' if accounts.logged_in(a) else 'no':<8}{plan:<6}{agents_:<{aw}}"
              f"{five:>5}{cell(st.pct_7d):>5}  {resets:<12}{cell(st.ctx_pct):>6}{age:>8}  "
              f"{status.describe_runway(status.runway(st, status.read_burn(a), None, loads.get(a))):<16}"
              f"{'yes' if allowed else 'NO':<4} {why}")
    outlook = status.team_outlook()
    if outlook:
        print("  " + outlook)
    if unplaced:
        print(f"  ? {unplaced} agent(s) have no readable account and are counted on none")
    print("  " + status.SOURCE_LEGEND)
    return 0


def cmd_account_probe(args) -> int:
    """`ocp account probe <acct>` - the manual form of what the daemon does
    when an account's window has passed and nothing has run on it since."""
    if args.account not in paths.list_accounts():
        print(f"no such account: {args.account}")
        return 1
    print(f"probing {args.account} (one small turn, up to {probemod.PROBE_TIMEOUT_S}s)...")
    st = probemod.run(args.account)
    ld = load.read_all().get(args.account)
    allowed, why = st.takes_work(ld.active if ld else 0)
    age = status.ago(st.age_s)
    print(f"{args.account}: 5h {st.trusted_pct_5h}% ({st.pct_5h_source}), "
          f"7d {st.trusted_pct_7d}%, reading {age} old")
    print(f"takes work: {'yes' if allowed else 'NO'} - {why}")
    return 0


# ---------------------------------------------------------------- agent
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
        if input(f"move {args.agent} to '{args.account}'? this records its handoff, ends its session, and makes "
                 f"'{args.account}' its preferred account (y/N): ").strip().lower() != "y":
            print("not moved")
            return 1
    con = db.connect()
    try:
        ok, msg = daemonmod.force_move(con, args.agent, args.account, "human (cli)", print)
    finally:
        con.close()
    print(msg, file=None if ok else sys.stderr)
    return 0 if ok else 1


def cmd_agent_register(args) -> int:
    if args.account not in paths.list_accounts():
        print(f"warning: account '{args.account}' is not registered yet. "
              f"Run `ocp account add {args.account}` first.")
    d = agents.register(args.name, args.account, role=args.role, model=args.model,
                        effort=args.effort, weight=args.weight, role_file=args.role_file,
                        repo=args.repo)
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
        "pct_5h": st.pct_5h, "pct_5h_effective": st.trusted_pct_5h,
        "pct_5h_source": st.pct_5h_source, "ctx_pct": st.ctx_pct,
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


def cmd_close(args) -> int:
    """Close an agent's session by hand, keeping its terminal."""
    con = db.connect()
    try:
        st = status.read_for_agent(args.agent, agents.current_account(args.agent, con))
        ok, msg = daemonmod.close_agent(con, args.agent, print, st)
        print(msg)
    finally:
        con.close()
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
    loads = load.read_all()
    for n in names:
        try:
            acct = agents.account_of(n)
        except Exception as e:  # noqa: BLE001
            print(f"{n:<14} <error: {e}>")
            continue
        ld = loads.get(acct)
        allowed, _ = status.read(acct).takes_work(ld.active if ld else 0)
        cfg = agents.read(n)
        budget = " ".join(f"{fn}:{size}B{'!' if over else ''}"
                          for fn, size, _, over in agents.budget_report(n))
        print(f"{n:<11} -> {acct:<7} {textsafe.oneline(str(cfg.get('model'))):<7} "
              f"{textsafe.oneline(str(cfg.get('effort') or agents.DEFAULT_EFFORT)):<7} "
              f"{'ok' if allowed else 'BLOCKED':<8} {budget}")
    return 0


# ---------------------------------------------------------------- launch
def cmd_launch(args) -> int:
    launch.exec_agent(args.agent, args.extra, force=args.force,
                      prompt_file=args.prompt_file)
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
    picked, reasons = status.pick_account()
    # What the pick's "one more agent lasts" rests on: each kind's price and
    # whether it was measured or is still the starting value.
    priced = load.costs().by_kind(paths.list_agents())
    if args.json:
        print(textsafe.json_dumps({"picked": picked,
                                  "candidates": [{"account": a, "reason": w} for a, w in reasons],
                                  "agent_costs": priced}, indent=2))
        return 0 if picked else 1
    for a, why in reasons:
        print(f"  {a:<14} {why}")
    if priced:
        print("  one more agent costs, per minute of the 5h window: " + "; ".join(priced))
    print()
    print(f"picked: {picked}" if picked
          else "picked: none - no account may take work (fail-closed)")
    return 0 if picked else 1


def cmd_drift(args) -> int:
    # Filenames come from git, which C-quotes a control byte in a path - but
    # only by default (core.quotePath=false, standard advice for readable
    # Korean/Japanese names, turns that off), so this prints its own way.
    d = drift.check(Path(args.repo).resolve(), base=args.base, fetch=not args.no_fetch)
    print(textsafe.oneline(d.summary()))
    if d.overlap:
        print("\noverlapping files:")
        for f in d.overlap[:20]:
            print(f"  {textsafe.oneline(f)}")
    print(f"\nresume header:\n  {d.resume_header()}")
    return 0 if d.ok and not d.significant else 1


def cmd_spawn(args) -> int:
    ok, msg = spawnmod.spawn(args.agent, Path(args.repo).resolve(), base=args.base,
                             prompt=args.prompt, dry_run=args.dry_run)
    print(msg)
    return 0 if ok else 1


def cmd_bridge(args) -> int:
    return tui.run(interval=args.interval, once=args.once)


def cmd_guide(args) -> int:
    return guide.show(args.topic)


# ---------------------------------------------------------------- daemon
def cmd_daemon(args) -> int:
    return daemonmod.run(once=args.once, verbose=not args.quiet,
                         max_ticks=args.max_ticks)


def _print_waits() -> None:
    """The founder's waits above everything else `ps` prints, in words: the same
    list the bridge pins in red."""
    con = db.connect()
    try:
        waits = actions.founder_waits(con)
    finally:
        con.close()
    if not waits:
        return
    now = time.time()
    print(f"{tui.WAITING_ON_YOU} ({len(waits)})")
    for w in waits:
        print(f"  {textsafe.oneline(w['who'])} · {textsafe.oneline(w['what'])} · {tui.age(now - w['since'])}")
    print()


def cmd_daemon_status(args) -> int:
    _print_waits()
    pid = daemonmod.running_elsewhere()
    supervisor = service.supervisor_of(pid)
    print(f"daemon: {'running, pid ' + str(pid) if pid else 'not running'} - {supervisor}")
    live = daemonmod.processes()
    if len(live) > 1:
        print(f"  WARNING: {len(live)} daemons are running (pid "
              f"{', '.join(str(x) for x in live)}). Two supervisors notify, park and wake "
              f"the same agent twice - end all but one.")
    elif live and not pid:
        print(f"  (pid {live[0]} is an `ocp daemon` process that does not hold the lock)")
    con = db.connect()
    try:
        rows = db.all_agent_states(con)
        if not rows:
            print("no agent state recorded yet")
        else:
            w = _name_col([r["agent"] for r in rows], 14)
            waits = {r["agent"]: waiting.of(r) for r in rows}
            sw = len(tui.WAITING_ON_YOU) + 1 if any(waits.values()) else 11
            print(f"\n{'AGENT':<{w}}{'STATE':<{sw}}{'WAKE':<8}{'fail':>5}{'cont':>6}{'thr':>5}  DETAIL")
            now = int(time.time())
            sessions = obs.live_sessions()
            for r in rows:
                wake = "-" if not r["wake_at"] else time.strftime("%H:%M", time.localtime(r["wake_at"]))
                if r["wake_at"] and r["wake_at"] <= now:
                    wake += "!"
                # A running session with a stale launch is not closed, so it
                # needs its own mark; see `launch.fingerprint`.
                drifted = bool(
                    not r["closed_at"] and sessions and sessions.get(r["agent"])
                    and launch.drifted(r["agent"], r))
                shown = r["state"] + ("·closed" if r["closed_at"] else "") \
                    + ("·drift" if drifted else "")
                detail = textsafe.oneline(r["detail"] or "")
                held = waits[r["agent"]]
                if held:
                    shown = tui.WAITING_ON_YOU
                    detail = textsafe.oneline(f"{held['tool']}: {held['command']}")
                walled = (daemonmod.would_wake(con, r) and
                          launch.wake_wait_reason(r["agent"], r, con))
                if walled:
                    # The reason is budgeted first and the agent's own detail
                    # yields room to it, not the other way around: a detail
                    # sitting ahead of an undelivered answer is the ordinary
                    # case this column exists to surface.
                    room = max(0, 70 - len(walled) - 3)
                    detail = f"{detail[:room]} · {walled}" if detail else walled
                else:
                    detail = detail[:70]
                print(f"{r['agent']:<{w}}{shown:<{sw}}{wake:<8}"
                      f"{r['failed_attempts']:>5}{r['continuations']:>6}{r['throttle_count']:>5}  "
                      f"{detail}")
        flight = db.in_flight_handoffs(con)
        if flight:
            print("\nin-flight handoffs (resume from these steps):")
            for h in flight:
                print(f"  {h['agent']:<14} step={h['step']:<11} state={h['state']:<10} "
                      f"deadline={time.strftime('%H:%M:%S', time.localtime(h['deadline']))}")
    finally:
        con.close()
    return 0


# ---------------------------------------------------------------- service
def cmd_service_install(args) -> int:
    pid = daemonmod.running_elsewhere()
    if pid:
        print(f"note: pid {pid} is already running by hand - the launchd job will "
              f"refuse to start (it cannot have the daemon lock) until it exits")
    ok, msg = service.install()
    print(msg)
    return 0 if ok else 1


def cmd_service_uninstall(args) -> int:
    ok, msg = service.uninstall()
    print(msg)
    return 0 if ok else 1


def cmd_service_status(args) -> int:
    st = service.status()
    print(f"label:       {service.LABEL}")
    print(f"plist:       {service.plist_path()}")
    print(f"installed:   {st['installed']}")
    print(f"loaded:      {st['loaded']}")
    print(f"pid:         {st['pid'] or '-'}")
    print(f"last exit:   {st['last_exit_code'] or '-'}")
    if service.crash_loop_detected():
        print("warning:     crash-looping - see `ocp service logs`")
    return 0


def cmd_service_logs(args) -> int:
    path = service.err_log() if args.err else service.out_log()
    print(textsafe.text(service.tail(path, lines=args.lines)))
    return 0


def cmd_unstall(args) -> int:
    """A human clearing a stall - every kind but a wake stall the sweep has
    refuted by observing a session (section 8.6)."""
    con = db.connect()
    try:
        row = db.get_agent_state(con, args.agent)
        if not row:
            print(f"no state for {args.agent}")
            return 1
        if row["state"] != daemonmod.STALLED:
            print(f"{args.agent} is {row['state']}, not STALLED")
            return 1
        print(actions.unstall(con, args.agent))
    finally:
        con.close()
    return 0


def _name_col(names, least: int = 12) -> int:
    """How wide a column of agent names has to be. Sized to the names there
    are: a fixed width ran `orcapipe-manager` into the column beside it."""
    return max(least, max((len(str(n)) for n in names), default=0) + 2)


def cmd_triggers(args) -> int:
    """Show what the daemon would decide right now, without acting."""
    w = _name_col(paths.list_agents(), 14)
    print(f"{'AGENT':<{w}}{'ACCT':<10}{'5h':>5}{'ctx':>5}  FIRES  REASON")
    con = db.connect()
    try:
        for a in paths.list_agents():
            try:
                # The account the daemon is watching, which is where the
                # session went - not the preference in agent.yaml. A moved
                # agent read on its preference prints the old account's figure
                # and FIRES YES while the daemon, reading the new one, does
                # nothing: the dashboard arguing with the daemon.
                acct = agents.current_account(a, con)
            except Exception as e:  # noqa: BLE001
                print(f"{a:<{w}} config error: {e}")
                continue
            st = status.read_for_agent(a, acct)
            t = triggers.evaluate(st.trusted_pct_5h, st.trusted_ctx_pct)
            cell = lambda v: "  -" if v is None else f"{v:>3}"  # noqa: E731
            # The figure the trigger used, not the raw one: a column saying "-"
            # beside a reason saying "0%" is the dashboard arguing with the daemon.
            shown, src = st.shown_pct_5h()
            five = f"{cell(shown)}{src}"
            row = db.get_agent_state(con, a)
            if daemonmod.triggers_apply(row):
                fires, reason = "YES" if t.fired else "no ", t.reason
            else:
                fires = "no "
                walled = daemonmod.would_wake(con, row) and launch.wake_wait_reason(a, row, con)
                reason = walled or "session closed; nothing to hand off"
            print(f"{a:<{w}}{acct:<10}{five:>5}{cell(st.ctx_pct):>5}  "
                  f"{fires:<6} {reason}")
    finally:
        con.close()
    print("  " + status.SOURCE_LEGEND)
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
        print(textsafe.text(msg))
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
    print(textsafe.text(p.read_text()))
    return 0


def cmd_handoff(args) -> int:
    con = db.connect()
    try:
        text = db.get_handoff(con, args.agent)
    finally:
        con.close()
    if not text:
        print(f"no handoff waiting for {args.agent}")
        return 1
    print(textsafe.text(text))
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
        if not args.all:
            # The hook events that only mark the edges of a turn are what an
            # agent's state column says already, and there are six of them for
            # every decision. `ocp bridge` used to filter them on screen; this is
            # where someone asking what the daemon did actually looks.
            rows = [r for r in rows if r["kind"] not in events.TURN_BOUNDARIES]
        if not rows:
            print("no events" if args.all else "no decisions (--all includes turn edges)")
            return 0
        for r in rows:
            when = time.strftime("%m-%d %H:%M", time.localtime(r["ts"]))
            print(f"{r['id']:<6} {when} {r['agent']:<12} {textsafe.oneline(r['kind']):<16} "
                  f"{textsafe.oneline(r['payload'] or '')[:70]}")
        # With --after the whole table is not the population being paged, and
        # saying it is misleads in the same direction as the bug this replaced.
        total = db.count_events(con, agent=args.agent, after=args.after)
        scope = f" after {args.after}" if args.after else ""
        print(f"\nshowing {len(rows)} of {total} events{scope}; "
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


# ---------------------------------------------------------------- team / ask
def cmd_hr(args) -> int:
    """Propose a change to the roster, see the proposals, or apply an approved one.

    Any agent may propose; only the founder approves; the change is made from
    the file the proposal was written to. Hiring is the one thing an agent must
    not be able to do on its own - an agent that can hire can spend an account
    into the ground with nobody having decided to.
    """
    me = askmod.asking_agent()
    if args.hr_cmd == "list":
        skipped: list[str] = []
        rows = hr.listing(skipped)
        for why in skipped:
            print(f"skipped, cannot be read - {why}", file=sys.stderr)
        if not rows:
            print("no proposals")
            return 0
        for p_ in rows:
            print(f"{p_['id']}  {p_['kind']:<7}{p_['agent']:<{_name_col([r['agent'] for r in rows])}}"
                  f"{p_.get('state', '?'):<10}"
                  f"#{p_.get('request', '-')}  by {p_.get('requested_by', '?')}")
        return 0
    if args.hr_cmd == "apply":
        ok, said = hr.apply(args.id)
        print(said)
        return 0 if ok else 1
    if not me:
        print(_who_refusal("propos"))
        return 1
    kw = dict(why=args.why, account=args.account, team_name=args.team,
              repo=args.repo, model=args.model, to=getattr(args, "to", None),
              effort=args.effort)
    # The founder is the approval, so their own change is made, not proposed.
    if me == askmod.HUMAN:
        ok, said = hr.enact(args.hr_cmd, args.name, **kw)
    else:
        ok, said = hr.propose(args.hr_cmd, args.name, requester=me, **kw)
    print(said)
    return 0 if ok else 1


def cmd_org(args) -> int:
    """Put every live agent in the team's Run, under whoever it reports to.

    A terminal Orca knows nothing about is a tab; a worker in a Run is a child
    card with a name, a parent and its own output. Agents started before this
    existed are tabs, and this is what adopts them - supervisors first, so a
    report's parent task exists by the time the report is registered.
    """
    con = db.connect()
    try:
        if not orca.available():
            print("orca is not on PATH - nothing to register with")
            return 1
        run = spawnmod.ensure_run(con)
        if not run:
            print("could not create an orchestration Run (is the Orca runtime up?)")
            return 1
        print(f"run {run}")
        # Supervisors before their reports: `--parent` needs the parent task to
        # exist, and a tree built child-first is a list.
        order = [name for name, _depth in actions.agent_tree()]
        w = _name_col(order)
        for name in order:
            row = db.get_agent_state(con, name)
            handle = row["terminal"] if row else None
            if not handle:
                print(f"  {name:<{w}} no terminal - nothing to register")
                continue
            if not orca.alive(handle):
                print(f"  {name:<{w}} terminal {handle} is gone")
                continue
            got = spawnmod.register_worker(con, name, handle)
            fresh = db.get_agent_state(con, name)
            print(f"  {name:<{w}} task {(fresh['task'] if fresh else None) or '-'}"
                  + (f" dispatch {got}" if got else " (not dispatched)"))
        return 0
    finally:
        con.close()


def cmd_team(args) -> int:
    """Who you may ask, who may ask you, and what they are doing right now.

    An agent that cannot see its own team has one way to get work done by
    somebody else: spawn a subagent, which dies with its session, holds no
    account, has no inbox and appears on no screen. The roster is in team.yaml
    and the states are in the database; neither was ever put in front of the
    agent whose team it is.
    """
    me = args.agent or askmod.asking_agent()
    if not me:
        print(_who_refusal("ask"))
        return 1
    try:
        t = team.Team.load()
    except Exception as e:  # noqa: BLE001
        print(f"team.yaml is invalid: {e}")
        return 1
    con = db.connect()
    try:
        mine, theirs = [], []
        for other in paths.list_agents():
            if other == me:
                continue
            out = t.route(me, other)
            back = t.route(other, me)
            if out.allowed:
                mine.append((other, _in_your_words(out.relation), _doing(con, other)))
            if back.allowed:
                theirs.append((other, _in_your_words(back.relation, sending=False)))
        w = _name_col([n for n, _r, _d in mine] + [n for n, _r in theirs])
        print(f"you are {me}")
        if mine:
            print("\nyou may ask:")
            for name, rel, doing in mine:
                print(f"  {name:<{w}}{rel:<12}{doing}")
        else:
            print("\nthere is nobody you may ask.")
        if theirs:
            print("\nmay ask you: "
                  + ", ".join(f"{n} ({rel})" for n, rel in theirs))
        print("\n`ocp ask <name> \"<task>\"` sends one. It is the only way to give an "
              "agent work:\na subagent you spawn yourself dies with this session and "
              "nobody can see it.")
        return 0
    finally:
        con.close()


def _in_your_words(relation: str | None, sending: bool = True) -> str:
    """The edge's relation from the reader's side.

    team.yaml names the relation of the *sender*: `manager -> dev.*` is a
    supervisor edge, so read straight back it told the manager that its own
    developers were its supervisors.
    """
    if relation == "supervisor":
        return "your report" if sending else "your manager"
    if relation == "subordinate":
        return "your manager" if sending else "your report"
    return "peer"


def _doing(con, agent: str) -> str:
    """One line on what an agent is doing, for the team list."""
    row = db.get_agent_state(con, agent)
    if not row:
        return "never started"
    state = row["state"] + (" · closed" if row["closed_at"] else "")
    waiting = len(db.queued_for(con, agent))
    if waiting:
        state += f" · {waiting} waiting"
    if daemonmod.would_wake(con, row):
        walled = launch.wake_wait_reason(agent, row, con)
        if walled:
            state += f" · {walled}"
    if not row["terminal"]:
        state += " · no terminal yet"
    return state


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


def _text_arg(inline: str | None, file_path: str | None, label: str) -> tuple[str | None, str | None]:
    """(text, error). One of `inline` or `file_path` - never neither, never
    both. A shell expands backticks and $() inside double quotes before this
    process ever sees argv, so a message with either in it is silently
    rewritten - or, at a live terminal, run - by the caller's own shell. A
    file the caller wrote with a quoted heredoc, `Write`, or any tool that
    does not itself go through a shell has no such risk; `--file` exists so
    that is always an option instead of inline text being the only one.
    """
    if file_path and inline is not None:
        return None, f"give {label} inline or with --file, not both"
    if file_path:
        try:
            return Path(file_path).read_text(), None
        except OSError as e:
            return None, f"could not read {file_path}: {e}"
    if inline is None:
        return None, f"{label} is required (or --file PATH)"
    return inline, None


def cmd_ask(args) -> int:
    body, err = _text_arg(args.body, args.file, "body")
    if err:
        print(err)
        return 1
    ok, msg, _ = askmod.ask(args.target, body, files=args.files,
                            requester=args.of, parent_id=args.parent,
                            mergeable=args.mergeable, first=args.first,
                            replaces=args.replaces, fyi=args.fyi)
    # ask() already phrases its own refusals; do not prefix them again.
    print(msg)
    return 0 if ok else 1


def cmd_withdraw(args) -> int:
    ok, msg = askmod.withdraw(args.id, requester=args.of)
    print(msg)
    return 0 if ok else 1


def cmd_cancel(args) -> int:
    """Take a request out of an agent's queue, as the founder.

    Human only: an agent that could cancel a request could close the work
    someone else handed it. Says what will happen and who will be told, and
    asks, because it cannot be reopened - the row is closed, never deleted.
    """
    if askmod.asking_agent() != askmod.HUMAN:
        print("cancel is the founder's: an agent takes back its own request with "
              "`ocp withdraw`, and answers one it was handed with `ocp resolve`.")
        return 1
    con = db.connect()
    try:
        ok, plan = actions.cancel_plan(con, args.id)
        if not ok:
            print(f"not cancelled - {plan}")
            return 1
        if not args.yes:
            print(plan)
            if not sys.stdin.isatty():
                print("refusing without a terminal to confirm in - pass --yes", file=sys.stderr)
                return 1
            if input("go ahead? [y/N] ").strip().lower() not in ("y", "yes"):
                print("left alone")
                return 1
        print(actions.cancel(con, args.id))
    finally:
        con.close()
    return 0


# A blocking wait polls; these are how often, and for how long by default. Ten
# minutes is a while to hold a terminal, and an answer that has not come in ten
# is usually one to go and look at.
WAIT_POLL_S = 3
WAIT_DEFAULT_S = 600


def _wait_for(check, timeout: int, what: str) -> bool:
    """Block until `check(con)` is true, or the timeout runs out. True if it came.

    Polling, not a notification: what this waits on is another process writing
    a row, and sqlite has nowhere to hang a listener. One connection serves the
    whole wait - `db.connect` runs the schema and its migrations on every call.
    """
    deadline = time.time() + timeout
    con = db.connect()
    try:
        while True:
            if check(con):
                return True
            if time.time() >= deadline:
                print(f"nothing after {timeout}s: {what}", file=sys.stderr)
                return False
            time.sleep(WAIT_POLL_S)
    finally:
        con.close()


def cmd_inbox(args) -> int:
    agent = args.agent or askmod.asking_agent()
    if not agent:
        print("cannot tell whose inbox this is: OCP_AGENT is unset and this is not "
              "an interactive shell. Name the agent.")
        return 1
    if args.wait:
        con = db.connect()
        try:
            seen = _waiting_ids(con, agent)
        finally:
            con.close()
        if not _wait_for(lambda c: _waiting_ids(c, agent) - seen, args.timeout,
                         f"nothing new for {agent}"):
            return 0  # nothing new is an outcome, not a failure

    if agent == actions.HUMAN:
        # The human's mailbox is a place you read *and answer* things, and that
        # is the screen: two places to do it is two places to keep in step, and
        # the one you are not looking at is the one holding the escalation. A
        # pipe still gets text - a script asking for the list is not a human
        # asking for somewhere to sit.
        if sys.stdout.isatty() and not args.all and not args.wait:
            return tui.run()
        return _human_inbox(args.all)
    if args.all:
        rows = askmod.inbox(agent, unread_only=False)
        if not rows:
            print(f"{agent}: inbox empty")
            return 0
        print(f"{len(rows)} total. `ocp read <id>` shows one whole.")
        for r in rows:
            print(f"#{r['id']:<5} from {r['requester']:<12} {r['state']:<9} "
                  f"depth={r['chain_depth']} {textsafe.oneline(r['body'])[:60]}")
        return 0

    con = db.connect()
    try:
        head = db.head_of_queue(con, agent)
        if not head:
            print(f"{agent}: inbox empty")
            return 0
        more = db.queue_depth(con, agent) - 1
        # Only a look at your own queue counts as being told - a foreign
        # `ocp inbox <name>` must not spend the requester's withdraw/replace.
        if agent == askmod.asking_agent():
            db.mark_delivered(con, int(head["id"]))
    finally:
        con.close()
    print(f"#{head['id']} from {head['requester']}  depth={head['chain_depth']}")
    print()
    print(textsafe.text(head["body"] or "(empty)"))
    if more:
        print(f"\n{more} more waiting - the next comes when you resolve or dismiss this one.")
    print("\n`ocp resolve <id> \"<verdict>\"` answers it. `ocp inbox --all` shows the whole "
          "queue and its history.")
    return 0


def _human_inbox(show_all: bool = False) -> int:
    """The human's mailbox, which holds one more thing than an agent's.

    An agent is told what came back to it by the daemon typing into its
    terminal. Nobody types into a human, so an answer to something the human
    asked has to be somewhere they look - and this command and `ocp bridge` have
    to hold the same rows, or clearing one leaves the other still asking.

    `--all` adds what has already been dealt with, the way it does everywhere
    else. It used to fall through to the agent view, which has no answers in
    it, so asking for more showed less.
    """
    con = db.connect()
    try:
        rows = actions.messages(con)
        if not rows and not show_all:
            print("nothing waiting on you")
            return 0
        # A count of messages, not of lines: read the same unfolded rows a
        # fold draws from, not the fold's own line count.
        print(f"{len(actions.mailbox_rows(con))} waiting. "
              f"`ocp read <id>` shows one whole.")
        for m in rows:
            mark = "!" if m["gate"] else ("<" if m["answer"] else ".")
            behind = f" (+{m['unread'] - 1})" if m["unread"] > 1 else ""
            print(f"{mark} #{m['id']:<5} {m['from']:<12} {(m['kind'] or 'ask'):<9} "
                  f"{textsafe.oneline(actions.first_line(m['body']))[:60]}{behind}")
        print("`ocp resolve <id> \"<text>\"` answers one - on a `<` row that goes back "
              "as a reply in its thread; `ocp dismiss <id>` takes it off the list.")
        if show_all:
            # Not the ones printed above: an answer that is still on the list is
            # not "already dealt with", and printing it twice would say it was.
            waiting = {m["id"] for m in rows}
            done = con.execute(
                "SELECT id, ts, requester, target, state, body, verdict FROM requests "
                "WHERE (target = ? OR requester = ?) AND state != 'queued' "
                "ORDER BY id DESC LIMIT 20", (actions.HUMAN, actions.HUMAN)).fetchall()
            done = [r for r in done if r["id"] not in waiting]
            print(f"\n{len(done)} most recent, already dealt with:")
            for r in done:
                other = r["target"] if r["requester"] == actions.HUMAN else r["requester"]
                arrow = "→" if r["requester"] == actions.HUMAN else "←"
                print(f"  #{r['id']:<5} {arrow} {other:<12} {r['state']:<9} "
                      f"{textsafe.oneline(actions.first_line(r['verdict'] or r['body']))[:60]}")
        return 0
    finally:
        con.close()


def _who_refusal(verb: str) -> str:
    """Whose row it is cannot be checked against a caller we cannot name.

    The same fail-closed rule `ocp ask` uses: a human has a terminal, a script
    that dropped OCP_AGENT does not, and promoting one to the other is how an
    agent ends up clearing the founder's mailbox.
    """
    return (f"cannot tell who is {verb}ing this: OCP_AGENT is unset and this is not "
            f"an interactive shell. Agents are started with `ocp launch <agent>`, "
            f"which sets it.")


def cmd_dismiss(args) -> int:
    """Take one of your own rows off your list.

    The two kinds there need two different writes - a request addressed to you
    is closed with "read", an answer you asked for is only marked told - and
    which one this is is read off the row, so clearing your list never means
    knowing which. Somebody else's row is neither: dismissing it would resolve
    a request they are still waiting on, with a verdict they never wrote.
    """
    con = db.connect()
    try:
        row = db.get_request(con, args.id)
        if row is None:
            print(f"no request #{args.id}")
            return 1
        me = askmod.asking_agent()
        if me is None:
            print(_who_refusal("dismiss"))
            return 1
        mine = row["target"] == me or (row["requester"] == me and row["state"] != "queued")
        if not mine:
            print(f"#{args.id} is {row['requester']} → {row['target']}, not yours to "
                  f"dismiss. `ocp read {args.id}` shows it.")
            return 1
        print(actions.dismiss(con, args.id, me=me))
        return 0
    finally:
        con.close()


# Every state in which no verdict is coming: the three that never reached the
# target, plus the two that settled one. A wait that does not know them waits
# out its whole timeout for an answer nobody is going to write.
SETTLED = ("done", "rejected", *db.NEVER_REACHED_TARGET)


def _waiting_ids(con, agent: str) -> set:
    """The request ids currently on this agent's list, human or not.

    The human's own list is read unfolded (`mailbox_rows`, not `messages`):
    a wait is for a new row landing, and a fold can leave the id set
    unchanged - a new row ties the folded representative's time and loses the
    tiebreak - although something real just arrived.
    """
    if agent == actions.HUMAN:
        return {r["id"] for r in actions.mailbox_rows(con)}
    return {r["id"] for r in askmod.inbox(agent, unread_only=False)
            if r["state"] not in SETTLED}


def _answered(con, request_id: int) -> bool:
    """Is there anything more to wait for on this row?

    A row that does not exist is answered in the only sense that matters here:
    waiting ten minutes for a typo'd id helps nobody, and the caller says so.
    Delivery is not an answer - it is a column, not a state, and it only means
    the target was told the row exists.
    """
    row = db.get_request(con, request_id)
    return row is None or bool(row["verdict"]) or row["state"] in SETTLED


def cmd_read(args) -> int:
    """Print one request whole - what was asked, and what came back.

    `ocp inbox` shows a line each - one per agent on the human's own, cut to
    fit - and the only way to see the rest was to know where the raw JSON
    lives. An agent that cannot read the whole of what it was asked answers
    the half it could see.

    Any agent can read any request, which was already true of the database and
    of `ocp inbox`, `ocp digest` and `ocp handoff` - this makes it convenient
    rather than new. Nothing typed into a request is private to two agents.
    """
    if args.wait and not _wait_for(
            lambda c: _answered(c, args.id), args.timeout,
            f"#{args.id} has no answer yet"):
        return 1
    con = db.connect()
    try:
        row = db.get_request(con, args.id)
        if row is None:
            print(f"no request #{args.id}")
            return 1
        if askmod.asking_agent() == actions.HUMAN:
            actions.opened_by_human(con, args.id)
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(row["ts"]))
        print(f"#{row['id']}  {row['requester']} → {row['target']}  "
              f"{row['kind'] or 'ask'}  {row['state']}  {when}")
        print(f"relation {row['relation']}   response {row['response']}   "
              f"depth {row['chain_depth']}")
        print()
        print(textsafe.text(row["body"] or "(empty)"))
        if row["verdict"]:
            print()
            # A denied row never reached its target, so nothing came back from
            # it: the verdict there is this system refusing to send it.
            print(f"--- refused ---" if row["state"] == "denied"
                  else f"--- answered by {row['target']} ---")
            print(textsafe.text(row["verdict"]))
        return 0
    finally:
        con.close()


def cmd_resolve(args) -> int:
    """Answer a request - whichever end of it you are on.

    Resolving writes the verdict on the row, so aimed at a request you made
    and someone has already answered it overwrites their answer with your own
    words and sends nothing: the record then says the agent said what you said.
    The screen's answer key has always branched on that; this is the same
    branch, because the two must not disagree about what "answer" means.
    """
    verdict, err = _text_arg(args.verdict, args.file, "verdict")
    if err:
        print(err)
        return 1
    con = db.connect()
    try:
        row = db.get_request(con, args.id)
        if row is None:
            print(f"no request #{args.id}")
            return 1
        me = askmod.asking_agent()
        if me is None:
            print(_who_refusal("answer"))
            return 1
        if row["requester"] == me and row["state"] != "queued":
            print(actions.answer(con, args.id, verdict))
            return 0
        if row["target"] != me:
            print(f"#{args.id} is {row['requester']} → {row['target']}, not yours to "
                  f"answer. `ocp read {args.id}` shows it.")
            return 1
        if row["state"] != "queued":
            # Closed already - answered, or cancelled by the founder while this
            # was being worked on. A second verdict would overwrite the first
            # and be sent to the requester as though it were the answer.
            print(f"#{args.id} is already {row['state']}"
                  + (f" ({textsafe.oneline(row['verdict'])[:120]})" if row["verdict"] else "")
                  + " - nothing to resolve.")
            return 1
    finally:
        con.close()
    ok, msg = askmod.resolve(args.id, verdict, state=args.state)
    print(msg)
    return 0 if ok else 1


# ---------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    from . import __version__

    ap = argparse.ArgumentParser(prog="ocp", description="orcapipe - multi-account agent pipeline")
    ap.add_argument("--version", action="version", version=f"ocp {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create ~/.orcapipe and install shared settings").set_defaults(fn=cmd_init)

    p = sub.add_parser("login", help="log an account in (interactive), or list who is")
    p.add_argument("account", nargs="?")
    p.add_argument("--force", action="store_true",
                   help="log in again even if credentials are already there")
    p.set_defaults(fn=cmd_login)

    acc = sub.add_parser("account", help="accounts").add_subparsers(dest="sub", required=True)
    p = acc.add_parser("add", help="register an account and log it in")
    p.add_argument("name")
    p.add_argument("--no-login", action="store_true",
                   help="register only; log in later with `ocp login <name>`")
    p.set_defaults(fn=cmd_account_add)
    p = acc.add_parser("remove", help="log an account out and take it off the roster")
    p.add_argument("name")
    p.add_argument("--yes", action="store_true", help="do not ask to confirm")
    p.set_defaults(fn=cmd_account_remove)
    p = acc.add_parser("register", help="create the account dir and link it to shared")
    p.add_argument("name")
    p.add_argument("--force", action="store_true", help="replace existing files with links")
    p.set_defaults(fn=cmd_account_register)
    p = acc.add_parser("cap", help="show or set the most agents mid-turn on an account")
    p.add_argument("name")
    p.add_argument("n", type=int, nargs="?", default=None)
    p.set_defaults(fn=cmd_account_cap)
    p = acc.add_parser("list", help="accounts with usage")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_account_list)
    p = acc.add_parser("probe", help="spend one small turn to learn where an account stands")
    p.add_argument("account")
    p.set_defaults(fn=cmd_account_probe)

    ag = sub.add_parser("agent", help="agents").add_subparsers(dest="sub", required=True)
    p = ag.add_parser("move", help="move a live session to another account now")
    p.add_argument("agent")
    p.add_argument("account")
    p.add_argument("--yes", action="store_true", help="skip the confirmation")
    p.set_defaults(fn=cmd_agent_move)
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
    p.add_argument("--repo", default=None,
                   help="where it works; lets the daemon start it when work arrives")
    p.set_defaults(fn=cmd_agent_register)
    ag.add_parser("list", help="list agents").set_defaults(fn=cmd_agent_list)
    p = ag.add_parser("show", help="everything known about one agent")
    p.add_argument("name")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_agent_show)

    p = sub.add_parser("launch", help="exec claude as this agent's account")
    p.add_argument("agent")
    p.add_argument("--force", action="store_true", help="start even when over the usage limit")
    p.add_argument("--prompt-file", help="a file holding the session's first prompt; "
                   "read and deleted at launch")
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
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("hr", help="propose a roster change; the founder approves it")
    hrsub = p.add_subparsers(dest="hr_cmd", required=True)
    for kind in ("add", "retire", "rename", "move"):
        q = hrsub.add_parser(kind, help=f"propose to {kind} an agent")
        q.add_argument("name")
        q.add_argument("--why", required=True, help="what the founder is deciding")
        if kind == "add":
            q.add_argument("--account", required=True)
            q.add_argument("--team", default=None, help="team in team.yaml to join")
            q.add_argument("--repo", default=None, help="where it works")
            q.add_argument("--model", default=None)
            q.add_argument("--effort", default=None, choices=sorted(agents.VALID_EFFORT))
        elif kind == "rename":
            q.add_argument("--to", required=True, help="the new name")
            q.add_argument("--team", default=None, help="team it should be in after")
            q.set_defaults(account=None, repo=None, model=None, effort=None)
        elif kind == "move":
            q.add_argument("--account", required=True, help="the account it should prefer next")
            q.set_defaults(team=None, repo=None, model=None, effort=None, to=None)
        else:
            q.set_defaults(account=None, team=None, repo=None, model=None, effort=None)
    q = hrsub.add_parser("list", help="proposals and what became of them")
    q.set_defaults(name=None, why=None, account=None, team=None, repo=None,
                   model=None, effort=None)
    q = hrsub.add_parser("apply", help="carry out an approved proposal")
    q.add_argument("id")
    q.set_defaults(name=None, why=None, account=None, team=None, repo=None,
                   model=None, effort=None)
    p.set_defaults(fn=cmd_hr)

    p = sub.add_parser("org", help="register live agents as workers in the team's Run")
    p.set_defaults(fn=cmd_org)

    p = sub.add_parser("team", help="who you may ask, and what they are doing")
    p.add_argument("--agent", default=None, help="whose team to show (default: yours)")
    p.set_defaults(fn=cmd_team)
    tm = p.add_subparsers(dest="sub")
    p = tm.add_parser("init", help="write a starter team.yaml")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_team_init)
    p = tm.add_parser("check", help="validate team.yaml")
    p.add_argument("--matrix", action="store_true", help="print the full routing matrix")
    p.set_defaults(fn=cmd_team_check)

    p = sub.add_parser("ask", help="send a request to another agent")
    p.add_argument("target")
    p.add_argument("body", nargs="?", default=None)
    p.add_argument("--file", default=None, metavar="PATH",
                   help="read the body from this file instead of the inline argument - "
                        "safe against a shell expanding backticks or $() in the text")
    p.add_argument("--files", default=None, help="comma-separated paths")
    p.add_argument("--of", default=None,
                   help="requester (defaults to $OCP_AGENT, or to the human "
                        "when a human types this at a terminal)")
    p.add_argument("--parent", type=int, default=None, help="parent request id, for chain depth")
    p.add_argument("--mergeable", action="store_true",
                   help="fold into an already-queued ask to the same target")
    p.add_argument("--first", action="store_true",
                   help="put this at the head of the target's queue")
    p.add_argument("--replaces", type=int, default=None, metavar="ID",
                   help="correct your own request ID before it has been delivered; "
                        "ID is retired and this takes its place in line")
    p.add_argument("--fyi", action="store_true",
                   help="a report to the human that asks nothing of them: not red, not "
                        "counted as waiting on them, gone once read or when your next "
                        "report arrives")
    p.set_defaults(fn=cmd_ask)

    p = sub.add_parser("withdraw", help="take back your own request before it is delivered")
    p.add_argument("id", type=int)
    p.add_argument("--of", default=None, help="requester (defaults to $OCP_AGENT)")
    p.set_defaults(fn=cmd_withdraw)

    p = sub.add_parser("cancel", help="the founder takes a request out of an agent's queue")
    p.add_argument("id", type=int)
    p.add_argument("--yes", action="store_true", help="do not ask to confirm")
    p.set_defaults(fn=cmd_cancel)

    p = sub.add_parser("inbox", help="the one request currently waiting for an agent")
    p.add_argument("agent", nargs="?")
    p.add_argument("--all", action="store_true", help="the whole queue and its history")
    p.add_argument("--wait", action="store_true", help="block until something new arrives, then print it")
    p.add_argument("--timeout", type=int, default=WAIT_DEFAULT_S, metavar="SECONDS",
                   help=f"how long --wait waits before giving up (default {WAIT_DEFAULT_S}s)")
    p.set_defaults(fn=cmd_inbox)

    p = sub.add_parser("read", help="print one request whole, with its answer")
    p.add_argument("id", type=int)
    p.add_argument("--wait", action="store_true", help="block until it has been answered, then print it")
    p.add_argument("--timeout", type=int, default=WAIT_DEFAULT_S, metavar="SECONDS",
                   help=f"how long --wait waits before giving up (default {WAIT_DEFAULT_S}s)")
    p.set_defaults(fn=cmd_read)

    p = sub.add_parser("resolve", help="close a request with a verdict")
    p.add_argument("id", type=int)
    p.add_argument("verdict", nargs="?", default=None)
    p.add_argument("--file", default=None, metavar="PATH",
                   help="read the verdict from this file instead of the inline argument - "
                        "safe against a shell expanding backticks or $() in the text")
    p.add_argument("--state", default="done", choices=["done", "rejected"])
    p.set_defaults(fn=cmd_resolve)

    p = sub.add_parser("dismiss", help="take one row off your mailbox without replying")
    p.add_argument("id", type=int)
    p.set_defaults(fn=cmd_dismiss)

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

    p = sub.add_parser("handoff", help="print the handoff waiting for an agent")
    p.add_argument("agent")
    p.set_defaults(fn=cmd_handoff)

    p = sub.add_parser("events", help="list recorded events")
    p.add_argument("--agent", default=None)
    p.add_argument("--after", type=int, default=0)
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--all", action="store_true",
                   help="include the hook events that only mark turn edges")
    p.set_defaults(fn=cmd_events)

    p = sub.add_parser("hook", help="internal: record a hook event from stdin")
    p.add_argument("event")
    p.set_defaults(fn=cmd_hook)

    p = sub.add_parser("install-hooks", help="add ocp event hooks to shared settings")
    p.set_defaults(fn=cmd_hook_install)

    p = sub.add_parser("daemon", help="run the supervisor loop")
    p.add_argument("--once", action="store_true", help="one tick, then exit")
    p.add_argument("--max-ticks", type=int, default=None)
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--foreground", action="store_true",
                   help="accepted for symmetry; the daemon always runs in the foreground")
    p.set_defaults(fn=cmd_daemon)

    p = sub.add_parser("ps", help="daemon and agent state")
    p.set_defaults(fn=cmd_daemon_status)

    sv = sub.add_parser("service", help="run the daemon as a launchd LaunchAgent").add_subparsers(
        dest="sub", required=True)
    p = sv.add_parser("install", help="write the plist and load it into launchd")
    p.set_defaults(fn=cmd_service_install)
    p = sv.add_parser("uninstall", help="unload the job and remove the plist")
    p.set_defaults(fn=cmd_service_uninstall)
    p = sv.add_parser("status", help="installed / loaded / pid / crash-looping")
    p.set_defaults(fn=cmd_service_status)
    p = sv.add_parser("logs", help="tail the daemon's launchd-captured output")
    p.add_argument("-n", "--lines", type=int, default=60)
    p.add_argument("--err", action="store_true", help="show stderr instead of stdout")
    p.set_defaults(fn=cmd_service_logs)

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

    p = sub.add_parser("bridge", aliases=["top", "watch"],
                       help="the screen the team is run from")
    p.add_argument("--interval", type=float, default=2.0)
    p.add_argument("--once", action="store_true", help="print one frame and exit")
    p.set_defaults(fn=cmd_bridge)

    bs = sub.add_parser("base", help="the per-session fixed context").add_subparsers(
        dest="sub", required=True)
    p = bs.add_parser("promote", help="add a fact to an agent's base.md")
    p.add_argument("agent")
    p.add_argument("--item", required=True)
    p.add_argument("--force", action="store_true", help="accept going over budget")
    p.set_defaults(fn=cmd_base_promote)

    p = sub.add_parser("write-handoff", help="record a handoff now")
    p.add_argument("agent")
    p.set_defaults(fn=cmd_handoff_write)

    p = sub.add_parser("close", help="end an idle agent's session, keeping its terminal")
    p.add_argument("agent")
    p.set_defaults(fn=cmd_close)

    p = sub.add_parser("resume", help="hand a parked agent back its handoff")
    p.add_argument("agent")
    p.set_defaults(fn=cmd_resume)

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
