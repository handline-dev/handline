"""Move an agent's worktree to follow its name, taking its history with it.

`hr.label` (from `_rename` or `hl hr relabel`) points Orca's label at the
agent's name without moving anything - the founder's sidebar reads right
even while the folder still carries the old one. This is the other half:
`hl hr rehome` actually moves the folder, once nothing still depends on it
being where it is.

The folder is the key Claude keeps the agent's transcripts under
(`accounts.memory_project_dir`), so moving it without those leaves the agent
to wake with no transcript to resume. Agent memory is not keyed by it.

Three things move together, or none does: the checkout (`git worktree move`, so
git's own records follow and uncommitted work stays where it is), each
account's project directory for it, and the agent's recorded worktree.

Orca does not follow the move. It re-discovers the checkout as a new worktree
in automatic mode, labelled by the git branch, with the comment, parent and
fixed name of the old entry gone, and its children lose their parent link;
`_relabel` sets them on the new one.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

from . import accounts
from .. import db, vendors
from ..session import term
from ..session.backends.base import UNREADABLE, WorktreeInfo
from ..infra import paths, textsafe


def split_recorded(recorded: str | None) -> tuple[str, str]:
    """(prefix, path) of what agent_state.worktree holds. `hl launch` records
    an Orca selector - the repo id, `::`, the path - and `spawn` a plain path;
    the prefix is kept so a moved row reads as it did."""
    if not recorded:
        return "", ""
    head, sep, path = recorded.rpartition("::")
    return head + sep, path


def _live(agent: str) -> bool | None:
    from ..session import observe as obs
    live = obs.live_sessions()
    return None if live is None else bool(live.get(agent))


def plan(con, agent: str, old: str) -> tuple[Path | None, Path | None, str]:
    """(source, destination, refusal). `agent` is the name it has now, `old`
    the name its worktree folder still carries."""
    row = db.get_agent_state(con, agent)
    _, src_s = split_recorded(row["worktree"] if row else None)
    if not src_s:
        return None, None, f"{agent} has no worktree recorded - nothing to move"
    src = Path(src_s)
    if src.name == agent:
        return None, None, f"{src} already carries the name {agent}"
    if src.name != old:
        return None, None, (f"{src} is not named {old}, so the rename did not leave "
                            f"the old name on it - not moving it")
    if not src.is_dir():
        return None, None, f"{src} is gone"
    dest = src.parent / agent
    if dest.exists():
        return None, None, f"{dest} already exists"
    for who in (agent, old):
        live = _live(who)
        if live is None:
            return None, None, "cannot tell whether its session is running - not moving it"
        if live:
            return None, None, (f"{who} has a session running in {src}. Close it first "
                                f"(`hl close {agent}`); it moves only while closed.")
    return src, dest, ""


def _project_dirs(src: Path, dest: Path) -> list[tuple[Path, Path]]:
    """(from, to) for each account's `projects/<encoded src>` directory."""
    pairs = []
    for acct in paths.list_accounts():
        try:
            root = vendors.for_account(acct).memory_root(vendors.config_dir(acct))
        except vendors.UnknownVendor:
            continue
        if root is None:
            continue
        old_key = accounts.memory_project_dir(acct, str(src))
        new_key = accounts.memory_project_dir(acct, str(dest))
        if (root / old_key).is_dir():
            pairs.append((root / old_key, root / new_key))
    return pairs


def _rename_all(pairs: list[tuple[Path, Path]]) -> None:
    """Rename every directory, or none: on a failure the ones already done are
    put back, in reverse. Whole directories, never read or copied."""
    done: list[tuple[Path, Path]] = []
    try:
        for a, b in pairs:
            a.rename(b)
            done.append((a, b))
    except OSError:
        for a, b in reversed(done):
            b.rename(a)
        raise


def _orca_entry(path: Path) -> WorktreeInfo | None:
    """Orca's entry for the checkout at `path`, or None."""
    if not term.has_labels():
        return None
    info = term.worktree_info(path)
    return info if isinstance(info, WorktreeInfo) else None


def _orca_unreadable(path: Path) -> bool:
    """True when Orca is there but did not give a usable answer about `path`.
    An answer that says it has no such worktree is an answer."""
    return term.has_labels() and term.worktree_info(path) is UNREADABLE


def _reparent_cmd(child: str, dest: Path) -> str:
    return f"`{term.manual('parent', child=child, path=str(dest))}`"


def _relabel(old: WorktreeInfo | None, dest: Path, agent: str) -> str:
    """Name Orca's new entry for the moved checkout `agent`, fixed, and carry
    over what the old entry held. "" when done or when Orca is not there."""
    if not term.has_labels():
        return ""
    children = old.children if old else ()
    for _ in range(10):
        if _orca_entry(dest):
            break
        time.sleep(1)
    else:
        said = (f" Orca has not picked up {dest} yet, so its label is not set: "
                f"`{term.manual('label', path=str(dest), label=agent)}`.")
        for child in children:
            said += f" Its child {child} is still unparented: {_reparent_cmd(child, dest)}."
        return said
    said = ""
    took, out = term.set_worktree(dest, label=agent, comment=old.comment if old else None,
                                  parent=old.parent if old else None,
                                  status=old.status if old else None)
    if not took:
        said = (f" Orca would not set the label ({out.strip()[:160]}). "
                f"`{term.manual('label', path=str(dest), label=agent)}`.")
    # Orca drops the parent link of every child of a moved worktree.
    for child in children:
        took, out = term.adopt_worktree(child, dest)
        if not took:
            said += (f" Orca would not re-parent {child} ({out.strip()[:160]}): "
                     f"{_reparent_cmd(child, dest)}.")
    return said


def _orca_blockers(src: Path) -> str:
    """Why Orca's own records forbid moving this checkout right now, or "".

    A terminal open in it would be left in a folder that is gone. Its children
    are not a reason: `_relabel` points each at the new path."""
    if _orca_unreadable(src):
        return "Orca is there but would not say what it holds for it, so its terminals are unknown"
    entry = _orca_entry(src)
    if entry is None:
        return ""
    tabs = term.terminals_in(src, entry.id)
    if tabs is None:
        return "Orca's terminals could not be read, so whether one is open in it is unknown"
    if not tabs:
        return ""
    return f"{len(tabs)} terminal(s) are open in it - a move would leave them in a folder that is gone"


def move(con, agent: str, old: str) -> tuple[bool, str]:
    """Move `agent`'s worktree from the folder named `old` to one named `agent`."""
    src, dest, why = plan(con, agent, old)
    if src is None:
        return False, why
    blocked = _orca_blockers(src)
    if blocked:
        return False, f"not moving {src}: {blocked}. `hl hr relabel {agent}` renames it in Orca without moving it."
    entry = _orca_entry(src)
    pairs = _project_dirs(src, dest)
    # Refused, not merged: a merge moves files one by one and cannot be undone.
    for _a, b in pairs:
        if b.exists():
            return False, f"{b} already exists - not merging into it; nothing moved"
    git = subprocess.run(["git", "-C", str(src), "worktree", "move", str(src), str(dest)],
                         capture_output=True, text=True)
    if git.returncode != 0:
        return False, (f"git would not move it: "
                       f"{textsafe.oneline((git.stderr or git.stdout).strip())[:200]}")
    try:
        _rename_all(pairs)
    except OSError as e:
        back = subprocess.run(["git", "-C", str(dest), "worktree", "move", str(dest), str(src)],
                              capture_output=True, text=True)
        if back.returncode != 0:
            return False, (f"moving a Claude project directory failed ({e}) and git would not "
                           f"move the worktree back: it is at {dest}, its project directories are unmoved "
                           f"({textsafe.oneline((back.stderr or back.stdout).strip())[:120]})")
        return False, f"moving a Claude project directory failed ({e}); nothing moved"
    row = db.get_agent_state(con, agent)
    prefix, _ = split_recorded(row["worktree"])
    # The terminal Orca kept for it sits in a folder that no longer exists.
    # Forgotten, so the next launch starts a fresh one in the moved worktree
    # (spawn.start_in) rather than typing at a shell with a dead cwd.
    con.execute("UPDATE agent_state SET worktree = ?, terminal = NULL WHERE agent = ?",
                (prefix + str(dest), agent))
    return True, (f"{agent}: worktree moved {src.name} -> {dest.name} (git's records followed; "
                  f"uncommitted work is where it was), transcripts carried on "
                  f"{len(pairs)} account(s); its old terminal is forgotten, the next launch "
                  f"opens one in {dest}." + _relabel(entry, dest, agent))
