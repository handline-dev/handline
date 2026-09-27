"""Account registration: directories and shared links.

Credentials are never touched (AGENTS.md rule 1). A refresh token rotates on
single use, so two copies of one credential invalidate each other. Logging in
is a one-time human action:

    CLAUDE_CONFIG_DIR=~/.orcapipe/accounts/<acct>/.claude claude /login

All we do is create the directory and symlink the shared settings.
"""
from __future__ import annotations

import json
import filecmp
import os
import re
import shutil
import time
from pathlib import Path

from . import agents
from ..infra import paths
from .. import vendors
from ..infra.yamlcompat import is_plain_text

def ensure_shared() -> None:
    """Create each vendor's shared config dir and the settings ocp keeps in it."""
    for h in vendors.state_hooks():
        h.ensure_shared()


class AdoptRefused(ValueError):
    """A real file or directory in an account cannot be replaced by the shared
    link without losing something; nothing was changed."""


def _adopt_plan(src: Path, dest: Path, moves: list, clashes: list) -> None:
    """Walk `src` once: what moves into `dest`, and what clashes (path, why)."""
    for child in src.iterdir():
        there = dest / child.name
        if not there.exists() and not there.is_symlink():
            moves.append((child, there))
            continue
        here_dir = child.is_dir() and not child.is_symlink()
        there_dir = there.is_dir() and not there.is_symlink()
        if here_dir and there_dir:
            _adopt_plan(child, there, moves, clashes)
        elif here_dir != there_dir:
            clashes.append((child, "a directory here, a file in shared" if here_dir
                            else "a file here, a directory in shared"))
        else:
            clashes.append((child, "shared has a different item of that name"))


def _adopt(src: Path, moves: list) -> None:
    """Move what a real directory holds into the shared one so the link can
    replace it. Claude writes there (`skills/synced`) when it runs before
    `register` does.

    `moves` is the plan from `_adopt_plan`, already free of clashes. Nothing is
    deleted that was not moved, and a move that fails puts back the ones
    already made.
    """
    done: list = []
    try:
        for a, b in moves:
            shutil.move(str(a), str(b))
            done.append((a, b))
    except BaseException:
        for a, b in reversed(done):
            shutil.move(str(b), str(a))
        raise
    # What is left is only directories whose contents all moved; a leftover
    # file would mean the plan missed it, so removal stops rather than deletes.
    for d, _dirs, _files in os.walk(src, topdown=False):
        os.rmdir(d)


def _plan_item(drv, cdir: Path, shared: Path, item: str, force: bool = False,
                replace_link: bool = False) -> tuple[str, list[str], list]:
    """What linking one shared item would do, what stops it, and (for `adopt`)
    what moves, changing nothing.

    The action is `skip`, `link`, `replace` (a link elsewhere, or a real file
    identical to shared's) or `adopt` (a real directory whose contents move into
    shared). A real file or directory that would lose something is never
    replaced: the second value names why, and the action is `skip`.

    A link pointing elsewhere is a human's override or a break they are
    already looking at, so only a human's call may replace it: `register`
    passes `replace_link` (a foreign symlink) and `force` (a real file or dir).
    """
    if item in drv.never_touch():
        return "skip", [], []
    target = shared / item
    link = cdir / item
    if not target.exists():
        return "skip", [], []
    if link.is_symlink():
        if os.readlink(link) == str(target):
            return "skip", [], []
        return ("replace" if force or replace_link else "skip"), [], []
    if not link.exists():
        return "link", [], []
    if not force:
        return "skip", [], []
    if link.is_dir():
        if not target.is_dir():
            return "skip", [f"{item}: a real directory here, a file in shared"], []
        moves: list = []
        clashes: list = []
        _adopt_plan(link, target, moves, clashes)
        if clashes:
            return "skip", [f"{item}/{c.relative_to(link)}: {why}" for c, why in clashes], []
        return "adopt", [], moves
    if target.is_file() and filecmp.cmp(link, target, shallow=False):
        return "replace", [], []
    if target.is_dir():
        return "skip", [f"{item}: a real file here, a directory in shared"], []
    return "skip", [f"{item}: a real file that differs from shared's"], []


def _apply_item(action: str, moves: list, cdir: Path, shared: Path, item: str) -> bool:
    link = cdir / item
    if action == "skip":
        return False
    if action == "adopt":
        _adopt(link, moves)  # removes the emptied directory, or raises
    elif action == "replace":
        link.unlink()
    link.symlink_to(shared / item)
    return True


def _link_item(drv, cdir: Path, shared: Path, item: str, force: bool = False,
               replace_link: bool = False) -> bool:
    """Symlink one shared item into an account's .claude dir; True if it changed."""
    action, why, moves = _plan_item(drv, cdir, shared, item, force, replace_link)
    if why:
        raise AdoptRefused("; ".join(why))
    return _apply_item(action, moves, cdir, shared, item)


def register(acct: str, force: bool = False, vendor: str | None = None) -> Path:
    """Create the account directory and link it to shared. Does not log in.

    Everything is planned before anything is made, so a refusal leaves no
    account behind. `vendor` is then recorded before the links, so an account
    is never briefly the default vendor's; a vendor with no driver is refused."""
    if not acct or "/" in acct or acct.startswith("."):
        raise ValueError(f"bad account name: {acct!r}")
    # It is a key in config.yaml, so it must be text there.
    if not is_plain_text(acct):
        raise ValueError(f"bad account name: {acct!r}. It would not be read back as text "
                         "from the YAML files it is written into; choose another.")
    if vendor is not None:
        vendors.get(vendor)
        # Its credentials, config dir and every agent on it are that vendor's.
        was = vendors.account_vendor(acct)
        if paths.account_dir(acct).is_dir() and was != vendor:
            raise ValueError(f"account '{acct}' is already a {was} account")
    ensure_shared()
    drv = vendors.get(vendor) if vendor is not None else vendors.for_account(acct)
    cdir = paths.account_dir(acct) / drv.config_dir_name
    shared = vendors.shared_dir(drv.name)
    plans = [(item, *_plan_item(drv, cdir, shared, item, force, True))
             for item in drv.shared_items()]
    refused = [w for _i, _a, why, _m in plans for w in why]
    if refused:
        raise AdoptRefused(
            f"nothing was changed for '{acct}'. Move or remove one side by hand, "
            "then run this again: " + "; ".join(refused))
    paths.account_dir(acct).mkdir(parents=True, exist_ok=True)
    if vendor is not None and vendor != agents.DEFAULT_VENDOR:
        vendors.record_vendor(acct, vendor)
    cdir.mkdir(parents=True, exist_ok=True)
    done: list[str] = []
    for item, action, _why, moves in plans:
        try:
            if _apply_item(action, moves, cdir, shared, item):
                done.append(item)
        except OSError as e:
            raise OSError(f"{item} failed ({e}); already linked before it: "
                          f"{', '.join(done) or 'nothing'}; nothing was deleted") from e
    share_memory(acct)
    return cdir


def relink_all(log=None) -> dict[str, list[str]]:
    """Link every registered account to the shared items that gained a target
    since `register()` last ran on it; returns what it linked, by account.

    Leaves alone an entry that is not the shared link (see `_link_item`);
    `unlinked()` is how a human finds those. An account that errors is
    reported to `log` and skipped, so the rest are still caught up.
    """
    done: dict[str, list[str]] = {}
    for acct in paths.list_accounts():
        try:
            drv = vendors.for_account(acct)
        except vendors.UnknownVendor as e:
            if log:
                log(f"  relink: {acct}: {e}")
            continue
        cdir = vendors.config_dir(acct)
        if not cdir.is_dir():
            continue  # not an account `register()` ran on
        shared = vendors.shared_dir(drv.name)
        try:
            linked = [item for item in drv.shared_items() if _link_item(drv, cdir, shared, item)]
        except OSError as e:
            if log:
                log(f"  relink: {acct}: {e}")
            continue
        if linked:
            done[acct] = linked
    return done


def unlinked() -> list[tuple[str, str]]:
    """(account, item) for every shared item an account holds as something
    other than the shared link, which `relink_all` will not replace."""
    out = []
    for acct in paths.list_accounts():
        try:
            drv = vendors.for_account(acct)
        except vendors.UnknownVendor:
            continue
        cdir = vendors.config_dir(acct)
        if not cdir.is_dir():
            continue
        shared = vendors.shared_dir(drv.name)
        never = drv.never_touch()
        for item in drv.shared_items():
            if item in never or not (shared / item).exists():
                continue
            link = cdir / item
            if not (link.is_symlink() and os.readlink(link) == str(shared / item)):
                out.append((acct, item))
    return out


def login_state(acct: str) -> bool | None:
    """Whether this account has credentials, as its driver's
    `credential_present` answers: a file's existence for claude, a run of
    `opencode auth list` (about 0.6s) for opencode. An account whose vendor
    has no driver cannot be driven, so it is not. None when this process
    cannot tell (a sandboxed one cannot see the keychain)."""
    try:
        drv = vendors.for_account(acct)
    except vendors.UnknownVendor:
        return False
    return drv.credential_present(vendors.config_dir(acct))


def logged_in(acct: str) -> bool:
    """A confirmed login. Unknown is not one: a caller that would report or
    act on a login it cannot see uses this."""
    return login_state(acct) is True


def may_start(acct: str) -> bool:
    """False only for a confirmed absence: a launch is refused for that, and
    for nothing it merely cannot see."""
    return logged_in(acct) or login_state(acct) is None


def login_word(state: bool | None, short: bool = False) -> str:
    if state is None:
        return "?" if short else "unknown (sandboxed)"
    if short:
        return "yes" if state else "no"
    return "logged in" if state else "NOT logged in"


def login_command(acct: str) -> str:
    drv = vendors.for_account(acct)
    return f'{drv.config_dir_env()}="{vendors.config_dir(acct)}" {" ".join(drv.login_argv())}'


def _subscription_file(acct: str) -> Path:
    return paths.account_dir(acct) / "subscription.json"


def recorded_subscription_type(acct: str) -> str | None:
    """The plan already recorded for this account, read from disk alone: no
    login check and no `claude` call, so a screen redraw may ask it."""
    try:
        data = json.loads(_subscription_file(acct).read_text())
        cached = data.get("subscriptionType") if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None
    return str(cached) if cached else None


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
    cached = recorded_subscription_type(acct)
    if not refresh and cached:
        return cached
    # A live reading could not be taken - not logged in, or the call itself
    # failed - and that must not erase what was already recorded: this account
    # is still that plan, whatever went wrong just now asking again.
    if not logged_in(acct):
        return str(cached) if cached else None
    sub = vendors.for_account(acct).subscription_type(env_for(acct))
    if not sub:
        return str(cached) if cached else None
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"subscriptionType": sub, "recorded_at": int(time.time())}))
    return sub


def memory_project_dir(acct: str, repo: str) -> str:
    """The `<memory_root>/<this>/memory/` folder one repo's memory is kept under
    on this account."""
    return vendors.for_account(acct).memory_project_dir(repo)


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


def _copy_memory(src_mem: Path, dst_mem: Path) -> bool:
    """Copy `src_mem`'s files into `dst_mem`, newest winning, MEMORY.md
    unioned; True if anything changed."""
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
    return copied_any


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
    restriction AGENTS.md rule 1 states for the whole module.
    Deletions do not propagate: a fact removed as wrong on one account can
    still come back from the other on a later move.

    Returns the project names actually carried over, for the caller to log.
    """
    drv = vendors.for_account(from_acct)
    if vendors.account_vendor(to_acct) != drv.name:
        return []
    src_root = drv.memory_root(vendors.config_dir(from_acct))
    if src_root is None or not src_root.is_dir():
        return []
    project_dirs = [src_root / project] if project else sorted(src_root.iterdir())
    carried = []
    for project_dir in project_dirs:
        if not project_dir.is_dir():
            continue
        src_mem = project_dir / "memory"
        if not src_mem.is_dir():
            continue
        dst_mem = drv.memory_root(vendors.config_dir(to_acct)) / project_dir.name / "memory"
        if src_mem.resolve() == dst_mem.resolve():
            continue  # one shared folder (share_memory): nothing to carry
        copied_any = _copy_memory(src_mem, dst_mem)
        if copied_any:
            carried.append(project_dir.name)
    return carried


def share_memory(acct: str, repo: str | None = None) -> list[str]:
    """Make the memory of every project one folder under shared/, linked from
    each account (T74). An agent sees whichever memory its current account
    has, so per-account copies drift apart and duplicate; one folder cannot.

    A project's account folder that is still a real directory is merged into
    the shared one (see `_copy_memory`) and set aside as `memory.before-shared`,
    then replaced by a link. `repo`'s project is shared even before the shared
    side has it, and every project already shared is linked into this account.
    Returns the project names linked now."""
    drv = vendors.for_account(acct)
    root = drv.memory_root(vendors.config_dir(acct))
    if root is None:
        return []
    shared_root = paths.home() / "shared" / "memory" / drv.name
    names = {p.name for p in shared_root.iterdir()} if shared_root.is_dir() else set()
    if repo:
        names.add(drv.memory_project_dir(repo))
    linked = []
    for name in sorted(names):
        shared = shared_root / name
        mem = root / name / "memory"
        if mem.is_symlink() and mem.resolve() == shared.resolve():
            continue
        shared.mkdir(parents=True, exist_ok=True)
        mem.parent.mkdir(parents=True, exist_ok=True)
        if mem.is_symlink():
            mem.unlink()
        elif mem.is_dir():
            _copy_memory(mem, shared)
            aside = mem.with_name("memory.before-shared")
            if aside.exists():
                shutil.rmtree(aside)  # an earlier one, merged when it was set aside
            mem.rename(aside)
        mem.symlink_to(shared, target_is_directory=True)
        linked.append(name)
    return linked


def login_plan(acct: str, force: bool = False) -> tuple[str, str]:
    """What `ocp login <acct>` should do, and the line to print about it.

    Separated from the doing because the doing is an exec: the human has to
    type the code into claude's own screen, and nothing about that can be
    tested or automated. Credentials stay where claude puts them (AGENTS.md
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
    reads no credential and no .claude.json to learn it (AGENTS.md rule 1). None
    when claude cannot say.
    """
    return vendors.for_account(acct).identity(env_for(acct))


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
    """Agents with a session open on this account. A closed agent is not one:
    it has no session to strand, and its next start is a fresh pick."""
    from .. import db
    own = con is None
    c = con or db.connect()
    try:
        return [name for name in paths.list_agents()
                if agents.current_account(name, c) == acct
                and not (db.get_agent_state(c, name) or {})["closed_at"]]
    finally:
        if own:
            c.close()


def remove_plan(acct: str, con=None) -> tuple[bool, str]:
    """Whether this account may be removed, and the line to say about it.

    Refused while an agent has a session open on it: removing the account
    strands that session, and closing it (the bridge's `c`) frees the account.
    """
    if acct not in paths.list_accounts():
        return False, f"no account '{acct}'"
    users = users_of(acct, con)
    if users:
        return False, (f"account '{acct}' has a session open for {', '.join(users)} - "
                       f"close {'it' if len(users) == 1 else 'them'} in the bridge (c) first")
    return True, ""


def add_account(name: str, run, no_login: bool = False,
                vendor: str | None = None) -> tuple[bool, str]:
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
        register(name, vendor=vendor)
    except ValueError as e:
        return False, str(e)
    if no_login:
        return True, f"registered account '{name}'; log it in with: ocp login {name}"
    argv, env = vendors.for_account(name).login_argv(), env_for(name)
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
                drv = vendors.for_account(name)
                return False, (f"logout failed - '{name}' is still logged in, so it stays. "
                               f"Run `{' '.join(drv.logout_argv())}` with {drv.config_dir_env()}="
                               f"{vendors.config_dir(name)} and try again.")
        marker.unlink(missing_ok=True)
        dest = retire(name)
        # The name can be added again, and a poll remembered against the old
        # account would hold the new one in its cooldown.
        from .. import db
        own = con is None
        c = con or db.connect()
        try:
            for kind in ("usage-poll", "usage-refresh", "login-check"):
                db.clear_cursor(c, f"{kind}:{name}")
            # A closed agent's resume id lived under the directory just moved
            # aside: it starts fresh on whatever account has room.
            c.execute("UPDATE agent_state SET account = NULL, account_source = NULL, "
                      "resume_session = NULL WHERE account = ?", (name,))
            c.commit()
        finally:
            if own:
                c.close()
    finally:
        marker.unlink(missing_ok=True)
    return True, f"removed account '{name}' (logged out; directory kept at {dest})"


def logout_argv(acct: str) -> tuple[list[str], dict]:
    """claude's own logout, run against this account's config directory.

    claude removes its own credential; ocp never touches it (AGENTS.md rule 1).
    Empty when there is nothing logged in to remove.
    """
    if not logged_in(acct):
        return [], {}
    return vendors.for_account(acct).logout_argv(), env_for(acct)


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
    """Environment for launching this account's vendor as this account."""
    env = dict(os.environ)
    # Another vendor's variable inherited from whoever launched this would
    # name a second account on the same process (`observe.account_of_process`).
    # Only one pointing into an account goes: XDG_DATA_HOME is also the user's
    # own, and claude reads it.
    for drv in vendors.known():
        v = env.get(drv.config_dir_env())
        if v and vendors.account_named_by(v):
            del env[drv.config_dir_env()]
    # An inherited OPENCODE_CONFIG_CONTENT is another session's whole config -
    # its preamble path, its tool denials. It goes: an opencode launch is
    # handed its own back by the driver's launch_env, and no other vendor's
    # launch reads it. OPENCODE_CONFIG_DIR stays: Orca sets it in every
    # terminal it opens for its opencode status plugin (busy/idle), and
    # dropping it here would strip that plugin from every opencode relaunch.
    env.pop("OPENCODE_CONFIG_CONTENT", None)
    env[vendors.for_account(acct).config_dir_env()] = str(vendors.config_dir(acct))
    env["OCP_HOME"] = str(paths.home())
    env["OCP_ACCOUNT"] = acct
    if agent:
        env["OCP_AGENT"] = agent
    return env
