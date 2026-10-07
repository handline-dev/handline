"""First-run commands: login, init, guide."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

from ..team import accounts
from ..team import guide
from ..infra import paths
from .. import vendors

from .hooks import cmd_hook_install


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
            print("no accounts registered. `hl account add <name>` first.")
            return 1
        for a in names:
            state = accounts.login_word(accounts.login_state(a))
            try:
                where = str(vendors.config_dir(a))
            except vendors.UnknownVendor as e:
                where = str(e)
            print(f"{a:<12}{state:<14}{where}")
        print(f"\nLog one in with: hl login <account>")
        return 0

    try:
        action, message = accounts.login_plan(args.account, force=args.force)
    except vendors.UnknownVendor as e:
        print(f"account '{args.account}': {e}", file=sys.stderr)
        return 1
    if action == "unknown":
        print(message, file=sys.stderr)
        return 1
    if action == "already":
        print(message)
        return 0

    drv = vendors.for_account(args.account)
    exe = drv.binary()
    if not exe or not sys.stdin.isatty():
        print(f"run this where you can type into it:\n\n  {message}\n")
        return 1
    print(f"handing the terminal to {drv.name} for '{args.account}' - "
          f"finish the login there, then come back.")
    env = accounts.env_for(args.account)
    os.execve(exe, [exe, *drv.login_argv()[1:]], env)
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
    cmd_hook_install(args)
    for sh in vendors.state_hooks():
        changed, blockers = sh.allow_home_writes()
        if changed:
            print(f"sandbox: agents may write under {h}")
        for entry in blockers:
            print(f"sandbox: denyWrite '{entry}' holds {h} and still blocks agents from it; "
                  f"remove it from {sh.target_settings()} if that is not what you want")
        if sh.allow_home_socket():
            print(f"sandbox: agents may connect to {paths.socket_file()}")
        if sh.allow_safe_commands():
            print(f"permissions: safe commands run without asking ({sh.target_settings()})")

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
    print("next: hl account add <name> - it registers the account and logs it in")
    return 0


def cmd_guide(args) -> int:
    return guide.show(args.topic)
