"""Account registration: directories, shared links, collector install.

Credentials are never touched (KICKOFF rule 1). A refresh token rotates on
single use, so two copies of one credential invalidate each other. Logging in
is a one-time human action:

    CLAUDE_CONFIG_DIR=~/.orcapipe/accounts/<acct>/.claude claude /login

All we do is create the directory, symlink the shared settings, and install
the collector.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import paths
from .yamlcompat import is_plain_text

# What each account directory links back to shared. Nothing credential-shaped
# appears here, deliberately.
LINKED = ["settings.json", "CLAUDE.md", "skills", "agents", "commands", "plugins"]

# Named explicitly as a guard: these are never created, linked, read or copied.
NEVER_TOUCH = {".credentials.json", ".claude.json", "statsig", "backups"}


def ensure_shared() -> Path:
    """Create shared/.claude and install the collector into it."""
    shared = paths.shared_claude()
    shared.mkdir(parents=True, exist_ok=True)
    for sub in ("skills", "agents", "commands"):
        (shared / sub).mkdir(exist_ok=True)

    src = paths.repo_root() / "libexec" / "statusline-collect.sh"
    dst = paths.collector()
    if src.exists():
        shutil.copyfile(src, dst)
        dst.chmod(0o755)

    settings = shared / "settings.json"
    current = {}
    if settings.exists():
        try:
            current = json.loads(settings.read_text())
        except json.JSONDecodeError:
            current = {}
    if not isinstance(current, dict):
        current = {}

    # statusLine is our data source (section 4), so it always points at us.
    current["statusLine"] = {
        "type": "command",
        "command": str(dst),
        "padding": 0,
        "refreshInterval": int(os.environ.get("OCP_STATUSLINE_REFRESH", "10")),
    }
    # Ours overlaps Claude Code's own auto-continue, and section 4 says to pick
    # one. We pick ours, and doctor enforces that the choice stays made.
    current.setdefault("autoContinueAtUsageLimit", False)
    settings.write_text(json.dumps(current, indent=2, ensure_ascii=False) + "\n")
    return shared


def register(acct: str, force: bool = False) -> Path:
    """Create the account directory and link it to shared. Does not log in."""
    if not acct or "/" in acct or acct.startswith("."):
        raise ValueError(f"bad account name: {acct!r}")
    # It is written as `account: <name>` in every agent.yaml and as a key in
    # config.yaml, so it must be text there.
    if not is_plain_text(acct):
        raise ValueError(f"bad account name: {acct!r}. It would not be read back as text "
                         "from the YAML files it is written into; choose another.")
    ensure_shared()
    cdir = paths.account_claude(acct)
    cdir.mkdir(parents=True, exist_ok=True)

    shared = paths.shared_claude()
    for item in LINKED:
        if item in NEVER_TOUCH:
            continue
        target = shared / item
        link = cdir / item
        if not target.exists():
            continue
        if link.is_symlink():
            if os.readlink(link) == str(target) and not force:
                continue
            link.unlink()
        elif link.exists():
            if not force:
                continue
            shutil.rmtree(link) if link.is_dir() else link.unlink()
        link.symlink_to(target)
    return cdir


def keychain_service(config_dir: Path) -> str:
    """The macOS Keychain service name Claude Code uses for this config dir.

    Credentials are not a file on macOS. Claude Code keys them per config dir:
    the default ~/.claude uses "Claude Code-credentials", and any other dir gets
    that name plus the first 8 hex of the sha256 of its path. Verified against a
    live login: ~/.orcapipe/accounts/alpha/.claude -> ...-c4b0821c.
    """
    if config_dir == Path.home() / ".claude":
        return "Claude Code-credentials"
    digest = hashlib.sha256(str(config_dir).encode()).hexdigest()[:8]
    return f"Claude Code-credentials-{digest}"


def logged_in(acct: str) -> bool:
    """Whether this account has credentials, checked by existence only.

    Never reads the secret: `security find-generic-password` is called without
    -w, so it reports the item's presence and nothing else.
    """
    cdir = paths.account_claude(acct)
    # File-backed storage (Linux, and macOS installs that opt out of Keychain).
    if (cdir / ".credentials.json").exists():
        return True
    if sys.platform != "darwin":
        return False
    try:
        r = subprocess.run(
            ["security", "find-generic-password", "-s", keychain_service(cdir)],
            capture_output=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def login_command(acct: str) -> str:
    return f'CLAUDE_CONFIG_DIR="{paths.account_claude(acct)}" claude /login'


def _subscription_file(acct: str) -> Path:
    return paths.account_dir(acct) / "subscription.json"


def subscription_type(acct: str, refresh: bool = False) -> str | None:
    """This account's plan, as `claude auth status` reports it. Recorded, not
    weighted in any pick.

    `claude auth status --json` is claude's own record of what it is signed in
    as; it touches no credential file, the same distinction `logged_in` above
    already draws for `security find-generic-password`. The
    answer is cached to disk once found, because it does not change between
    ticks and the accounts panel re-reads this on every refresh - pass
    refresh=True to ask again after a human changes a plan.
    """
    f = _subscription_file(acct)
    cached = None
    if f.exists():
        try:
            data = json.loads(f.read_text())
            cached = data.get("subscriptionType") if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError):
            cached = None
    if not refresh and cached:
        return str(cached)
    # A live reading could not be taken - not logged in, or the call itself
    # failed - and that must not erase what was already recorded: this account
    # is still that plan, whatever went wrong just now asking again.
    if not logged_in(acct):
        return str(cached) if cached else None
    try:
        out = subprocess.run(
            ["claude", "auth", "status", "--json"],
            env={**os.environ, "CLAUDE_CONFIG_DIR": str(paths.account_claude(acct))},
            capture_output=True, text=True, timeout=10,
        ).stdout
        data = json.loads(out)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, ValueError):
        return str(cached) if cached else None
    sub = data.get("subscriptionType") if isinstance(data, dict) else None
    if not isinstance(sub, str) or not sub:
        return str(cached) if cached else None
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"subscriptionType": sub, "recorded_at": int(time.time())}))
    return sub


def memory_project_dir(repo: str) -> str:
    """The `projects/<this>/memory/` folder Claude Code keeps one repo's
    memory under - its own path encoding, every non-alphanumeric character
    mapped to '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", repo)


_INDEX_TARGET = re.compile(r"\]\(([^)\s]+)\)")


def _merge_memory_index(src_index: Path, dst_index: Path) -> str | None:
    """The union of two `MEMORY.md` indexes, one line per file, or None when
    the destination already says everything the source does.

    Two accounts can describe the same file differently, so the line is keyed
    on the file it links to. The line kept is the one from the side whose file
    the copy will leave in place (the newer one), so the description matches
    the content. A line with no link is kept once, verbatim."""
    def target(line: str) -> str | None:
        m = _INDEX_TARGET.search(line)
        return m.group(1) if m else None

    lines = dst_index.read_text().splitlines()
    have = {target(ln): i for i, ln in enumerate(lines) if target(ln)}
    changed = False
    for ln in src_index.read_text().splitlines():
        if not ln.strip():
            continue
        t = target(ln)
        if t is None or t not in have:
            if ln not in lines:
                lines.append(ln)
                if t:
                    have[t] = len(lines) - 1
                changed = True
            continue
        s_file, d_file = src_index.parent / t, dst_index.parent / t
        if (s_file.is_file() and d_file.is_file()
                and s_file.stat().st_mtime > d_file.stat().st_mtime and lines[have[t]] != ln):
            lines[have[t]] = ln
            changed = True
    return "\n".join(lines) + "\n" if changed else None


def migrate_memory(from_acct: str, to_acct: str, project: str | None = None) -> list[str]:
    """Carry what an agent has learned from one account's config dir to
    another. Which account a fact happened to be written under is an
    accident of which one was logged in at the time, so a move has to bring
    the facts along.

    `project` (see `memory_project_dir`) scopes the copy to one repo's
    memory - the moving agent's own, not every repo anyone has ever worked
    in on the old account. None copies every project there is, for a
    caller that cannot resolve one.

    Newest wins, by mtime (`copy2` preserves it): the source account is
    where the agent was actually working last, so its copy of a file is
    never older there than a copy already at the destination unless the
    destination is itself the newer side - which a straight round trip
    (an agent moved back to an account it had already been on) makes routine.
    `MEMORY.md` is the exception: it is the index Claude Code loads, so the
    two sides' entries are unioned, one per file, rather than one replacing
    the other, or a file on disk would stop being loaded. The memory is the repo's, not one
    agent's, so every agent's notes for it travel together.
    Only ever copies regular files into projects/*/memory/, matching the same
    restriction KICKOFF rule 1 states for the whole module.
    Deletions do not propagate: a fact removed as wrong on one account can
    still come back from the other on a later move.

    Returns the project names actually carried over, for the caller to log.
    """
    src_root = paths.account_claude(from_acct) / "projects"
    if not src_root.is_dir():
        return []
    project_dirs = [src_root / project] if project else sorted(src_root.iterdir())
    carried = []
    for project_dir in project_dirs:
        if not project_dir.is_dir():
            continue
        src_mem = project_dir / "memory"
        if not src_mem.is_dir():
            continue
        dst_mem = paths.account_claude(to_acct) / "projects" / project_dir.name / "memory"
        copied_any = False
        # The index is merged against the files' mtimes as they stand before
        # any copy moves them, so it goes first by rule, not by name order.
        for f in sorted(src_mem.rglob("*"), key=lambda f: (f.name != "MEMORY.md", str(f))):
            if f.is_symlink() or not f.is_file():
                continue
            dst_file = dst_mem / f.relative_to(src_mem)
            if f.name == "MEMORY.md" and dst_file.is_file():
                merged = _merge_memory_index(f, dst_file)
                if merged is not None:
                    dst_file.write_text(merged)
                    copied_any = True
                continue
            if dst_file.exists() and dst_file.stat().st_mtime >= f.stat().st_mtime:
                continue
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst_file)
            copied_any = True
        if copied_any:
            carried.append(project_dir.name)
    return carried


def login_plan(acct: str, force: bool = False) -> tuple[str, str]:
    """What `ocp login <acct>` should do, and the line to print about it.

    Separated from the doing because the doing is an exec: the human has to
    type the code into claude's own screen, and nothing about that can be
    tested or automated. Credentials stay where claude puts them (KICKOFF
    rule 1) - ocp only points the session at the right config directory.
    """
    if acct not in paths.list_accounts():
        return "unknown", (f"no account '{acct}'. Add it first:\n\n"
                           f"  ocp account add {acct}\n")
    if logged_in(acct) and not force:
        return "already", (f"account '{acct}' is already logged in.\n"
                           f"Use --force to log in again (a second copy of one "
                           f"credential invalidates the first).")
    return "login", login_command(acct)


def identity(acct: str) -> str | None:
    """The Anthropic login behind this account, as claude reports it.

    Asked of `claude auth status`, which answers from its own records - ocp
    reads no credential and no .claude.json to learn it (KICKOFF rule 1). None
    when claude cannot say.
    """
    claude = shutil.which("claude")
    if not claude:
        return None
    try:
        r = subprocess.run([claude, "auth", "status"], env=env_for(acct),
                           capture_output=True, text=True, timeout=30)
        return json.loads(r.stdout).get("email") or None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def same_login_as(acct: str) -> list[str]:
    """Other accounts logged in as the same Anthropic account as this one.

    Two of them are one usage limit under two names - adding the second buys
    no headroom - and two copies of one credential invalidate each other.
    """
    mine = identity(acct)
    if not mine:
        return []
    return [a for a in paths.list_accounts()
            if a != acct and logged_in(a) and identity(a) == mine]


def users_of(acct: str, con=None) -> list[str]:
    """Agents that would lose their account if this one went away.

    Both halves of "on this account": a session that actually went there, and
    agent.yaml naming it as the preference. They differ when the preferred
    account had no room, and removing either one strands an agent. An agent
    that cannot be read is counted as a user: what is unknown is not free.
    """
    from . import agents

    def on(name: str) -> bool:
        for get in (lambda: agents.current_account(name, con),
                    lambda: agents.account_of(name)):
            try:
                if get() == acct:
                    return True
            except Exception:  # noqa: BLE001 - unreadable means in use
                return True
        return False

    return [name for name in paths.list_agents() if on(name)]


def remove_plan(acct: str, con=None) -> tuple[bool, str]:
    """Whether this account may be removed, and the line to say about it.

    Refused while any agent is on it. Moving them is a roster decision with
    somewhere to go - which account, and whether that one has room - and a
    removal that made it silently would leave an agent whose next start fails.
    """
    if acct not in paths.list_accounts():
        return False, f"no account '{acct}'"
    users = users_of(acct, con)
    if users:
        return False, (f"account '{acct}' is in use by {', '.join(users)} - move "
                       f"{'it' if len(users) == 1 else 'them'} to another account first")
    return True, ""


def add_account(name: str, run, no_login: bool = False) -> tuple[bool, str]:
    """The one add sequence, for the CLI and the screen: register, log in, check.

    `run(argv, env)` hands the terminal to claude and returns its exit code, or
    None if it could not run. The login is claude's own screen, so this checks
    afterwards rather than trusting the exit code: closing claude without
    finishing exits 0 too. Two accounts on one Anthropic login are one usage
    limit under two names, and their credentials invalidate each other.
    """
    if name in paths.list_accounts():
        return False, f"account '{name}' already exists"
    try:
        register(name)
    except ValueError as e:
        return False, str(e)
    if no_login:
        return True, f"registered account '{name}'; log it in with: ocp login {name}"
    argv, env = ["claude", "/login"], env_for(name)
    run(argv, env)
    if not logged_in(name):
        return False, f"'{name}' is registered but not logged in - ocp login {name}"
    twins = same_login_as(name)
    if twins:
        return False, (f"'{name}' is logged in as the same Anthropic account as "
                       f"{', '.join(twins)}: one usage limit under two names, and the "
                       f"credentials invalidate each other. `ocp account remove {name}` "
                       f"and add it with another login.")
    return True, (f"'{name}' is logged in. It takes work once a session has run on it "
                  f"and reported its usage.")


def remove_account(name: str, confirm, run, con=None) -> tuple[bool, str]:
    """The one remove sequence, for the CLI and the screen.

    `confirm()` asks the human; `run(argv, env)` runs claude's logout and
    returns its exit code (None if it could not run). Refused while an agent is
    on the account, checked again after the confirmation since that waits on a
    person. Logged out by claude first: moving the directory of a logged-in
    account would orphan the credential, whose Keychain name derives from the
    directory's path. The directory is moved aside, not deleted.
    """
    ok, why = remove_plan(name, con)
    if not ok:
        return False, why
    if not confirm():
        return False, "not removed"
    marker = paths.accounts_dir() / name / ".removing"
    marker.touch()
    try:
        ok, why = remove_plan(name, con)
        if not ok:
            return False, why
        argv, env = logout_argv(name)
        if argv:
            code = run(argv, env)
            if code != 0 or logged_in(name):
                return False, (f"logout failed - '{name}' is still logged in, so it stays. "
                               f"Run `claude auth logout` with CLAUDE_CONFIG_DIR="
                               f"{paths.account_claude(name)} and try again.")
        marker.unlink(missing_ok=True)
        dest = retire(name)
        # The name can be added again: a probe remembered against the old
        # account would keep the new one from ever being read.
        from . import db
        own = con is None
        c = con or db.connect()
        try:
            db.clear_cursor(c, f"probe:{name}")
            db.clear_cursor(c, f"login-check:{name}")
        finally:
            if own:
                c.close()
    finally:
        marker.unlink(missing_ok=True)
    return True, f"removed account '{name}' (logged out; directory kept at {dest})"


def logout_argv(acct: str) -> tuple[list[str], dict]:
    """claude's own logout, run against this account's config directory.

    claude removes its own credential; ocp never touches it (KICKOFF rule 1).
    Empty when there is nothing logged in to remove.
    """
    if not logged_in(acct):
        return [], {}
    return ["claude", "auth", "logout"], env_for(acct)


def retire(acct: str) -> Path:
    """Take the account off the roster by moving its directory aside.

    Aside, not deleted: its transcripts are the only record of what was spent
    on it, and a removal typed by mistake comes back with one `mv`. A dot
    directory is invisible to `list_accounts`, so nothing reads it as live.
    """
    src = paths.accounts_dir() / acct
    dest_dir = paths.accounts_dir() / ".retired"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{acct}-{time.strftime('%Y%m%d-%H%M%S')}"
    src.rename(dest)
    return dest


def env_for(acct: str, agent: str | None = None) -> dict[str, str]:
    """Environment for launching claude as this account."""
    env = dict(os.environ)
    env["CLAUDE_CONFIG_DIR"] = str(paths.account_claude(acct))
    env["OCP_HOME"] = str(paths.home())
    env["OCP_ACCOUNT"] = acct
    if agent:
        env["OCP_AGENT"] = agent
    return env
