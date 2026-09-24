"""`ocp account ...`: register, add, remove, cap, recover, list."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys

from .. import accounts
from .. import timefmt
from .. import actions
from .. import agents
from .. import db
from .. import load
from .. import orca
from .. import paths
from .. import recover as recovermod
from .. import status
from .. import textsafe
from .. import tui
from .. import vendors


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
    vendor = args.vendor or agents.DEFAULT_VENDOR
    try:
        drv = vendors.get(vendor)
    except vendors.UnknownVendor as e:
        print(e, file=sys.stderr)
        return 1
    if not args.no_login and not drv.binary():
        print(f"{drv.login_argv()[0]} was not found on PATH. Install it, then run:\n\n"
              f"  ocp account add {args.name}"
              f"{'' if vendor == agents.DEFAULT_VENDOR else ' --vendor ' + vendor}\n",
              file=sys.stderr)
        return 1
    if not args.no_login and not sys.stdin.isatty():
        print(f"logging in needs a terminal you can type into. From one, run:\n\n"
              f"  ocp account add {args.name}"
              f"{'' if vendor == agents.DEFAULT_VENDOR else ' --vendor ' + vendor}\n",
              file=sys.stderr)
        return 1
    if not args.no_login:
        print(f"use a {drv.name} login no other account here uses. "
              f"Finish the login in {drv.name}, then exit it to come back.\n")
    ok, msg = accounts.add_account(args.name, _run_claude, no_login=args.no_login,
                                   vendor=args.vendor)
    print(msg, file=None if ok else sys.stderr)
    if not ok:
        return 1

    # 156 item 1c: without this, the first real session this account runs for
    # an existing agent meets claude's folder-trust screen cold. `no_login`
    # leaves nothing yet to start a session on.
    # The trust screen is claude's own.
    if not args.no_login and vendor == agents.DEFAULT_VENDOR and orca.available():
        _offer_to_trust_worktrees(args.name, input)
    return 0


def _offer_to_trust_worktrees(acct: str, ask) -> None:
    """Ask, listing exactly the worktrees a yes would trust, then do it."""
    declared, skipped = actions.eligible_worktrees()
    if not declared:
        if skipped:
            print(f"\n{len(skipped)} agent worktree(s) exist but are outside any "
                  f"declared root - not offering to trust them: "
                  f"{', '.join(a for a, _ in skipped)}")
        return
    listed = "\n".join(f"  {a}: {w}" for a, w in declared)
    skip_note = (f"\n(skipping {len(skipped)} outside declared roots: "
                f"{', '.join(a for a, _ in skipped)})" if skipped else "")
    if ask(f"\nTrust these worktrees on '{acct}'?\n{listed}{skip_note}\n[y/N] "
          ).strip().lower() in ("y", "yes"):
        for line in actions.trust_worktrees(acct, declared):
            print(f"  {line}")


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


def cmd_account_recover(args) -> int:
    """#1885's per-account switch: whether a dead read may spend one real turn
    to fix itself. With no on/off/--now, says where it stands, needs-re-login
    mark included since that is the other way this stops running on its own.
    `--now` is the only caller of `recover.recover` until #76 wires daemon.py's
    poll into it - the one way to run this by hand before then."""
    if args.clear_relogin:
        recovermod.clear_relogin(args.name)
    if args.state is not None:
        load.set_usage_recover(args.name, args.state == "on")
    if args.now:
        ok, msg = recovermod.recover(args.name)
        print(msg)
        return 0 if ok else 1
    on = load.usage_recover_enabled(args.name)
    print(f"{args.name}: usage recovery is {'on' if on else 'off'}")
    if recovermod.needs_relogin(args.name):
        print(f"{args.name}: marked needs re-login - recovery refuses it until "
              f"that mark is cleared (`ocp account recover {args.name} --clear-relogin`)")
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
        age = timefmt.ago(st.age_s)
        resets = "-" if not st.reset_7d else timefmt.stamp(st.reset_7d, timefmt.SHORT)
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
