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

from . import accounts, agents, doctor, guide, hooks, launch, paths, status


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
                "pct_5h": st.pct_5h, "pct_7d": st.pct_7d, "ctx_pct": st.ctx_pct,
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
        print(f"{a:<14}{'yes' if accounts.logged_in(a) else 'no':<8}"
              f"{cell(st.pct_5h):>4}{cell(st.pct_7d):>5}{cell(st.ctx_pct):>6}{age:>8}  "
              f"{'yes' if allowed else 'NO':<4} {why}")
    return 0


# ---------------------------------------------------------------- agent
def cmd_agent_register(args) -> int:
    if args.account not in paths.list_accounts():
        print(f"warning: account '{args.account}' is not registered yet. "
              f"Run `ocp account register {args.account}` first.")
    d = agents.register(args.name, args.account, role=args.role, model=args.model)
    print(f"registered agent '{args.name}' -> account '{args.account}': {d}")
    print("launch it from Orca with:")
    print(f'  orca terminal create --worktree <selector> --command "{launch.command_for(args.name)}"')
    return 0


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
        budget = " ".join(f"{fn}:{size}B{'!' if over else ''}"
                          for fn, size, _, over in agents.budget_report(n))
        print(f"{n:<14} -> {acct:<10} {'ok' if allowed else 'BLOCKED':<8} {budget}")
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
    picked, reasons = status.pick_account()
    for a, why in reasons:
        print(f"  {a:<14} {why}")
    print()
    print(f"picked: {picked}" if picked
          else "picked: none - no account may take work (fail-closed)")
    return 0 if picked else 1


def cmd_guide(args) -> int:
    return guide.show(args.topic)


# ---------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="ocp", description="orcapipe - multi-account agent pipeline")
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
    p.add_argument("--model", default="sonnet")
    p.set_defaults(fn=cmd_agent_register)
    ag.add_parser("list", help="list agents").set_defaults(fn=cmd_agent_list)

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

    sub.add_parser("status", help="which account may take work").set_defaults(fn=cmd_status)

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
