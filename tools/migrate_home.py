#!/usr/bin/env python3
"""Move the home ~/.orcapipe to ~/.handline, with its protected store, and undo it.

  tools/migrate_home.py check    what would move, and what stops it (changes nothing)
  tools/migrate_home.py apply    the move; refuses while the daemon runs
  tools/migrate_home.py undo     put everything back as it was

Run it from your own terminal with the daemon stopped (`hl service uninstall`, or stop the job), not
from an agent: the protected store is not writable from a sandbox. The steps, in order:
  1. ~/.orcapipe is renamed to ~/.handline and a link is left at ~/.orcapipe, so old paths keep working.
  2. The protected store folder is renamed to match. Its name comes from the home's real path, so
     the folder for ~/.orcapipe is not the one the code looks in once the home is ~/.handline;
     without this step every decision, trust record and session token would seem lost.
  3. Links inside the home that point at the old path (each account's .claude links into shared/) are
     re-aimed at the new one: doctor and `hl account` compare those links as text.
  4. The shared settings.json gets the new path beside each old one (additionalDirectories,
     sandbox allowWrite and allowUnixSockets): a sandbox matches real paths, and the real path changed.
     The original is kept as settings.json.pre-migration in the new home.
`undo` reverses each step that is still in place, refuses while the daemon runs, and can be run more than
once. The launchd job keeps OCP_HOME=~/.orcapipe: it works through the link and resolves to the new home.
Afterwards restart the daemon and relaunch the agent sessions (`hl close` on each idle one): a running session keeps the
old path in its sandbox settings.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

OLD_NAME, NEW_NAME = ".orcapipe", ".handline"
BACKUP = "settings.json.pre-migration"


def slug(path: str) -> str:
    """The protected store's folder name for a home: its real path with every other character a dash
    (`handline/infra/protected.py: root_for`; a test keeps the two equal)."""
    return re.sub(r"[^A-Za-z0-9]", "-", path)


@dataclass
class Plan:
    user: Path
    old: Path
    new: Path
    store_old: Path
    store_new: Path

    @property
    def settings(self) -> Path:
        return self.new / "shared" / ".claude" / "settings.json"

    @property
    def settings_before(self) -> Path:
        return self.old / "shared" / ".claude" / "settings.json"

    @property
    def backup(self) -> Path:
        return self.new / "shared" / ".claude" / BACKUP


def plan_for(user: Path) -> Plan:
    user = Path(os.path.realpath(user))
    old, new = user / OLD_NAME, user / NEW_NAME
    base = user / ".config" / "orcapipe" / "protected"
    return Plan(user, old, new, base / slug(str(old)), base / slug(str(new)))


def problems(p: Plan, daemon_running: Callable[[], bool]) -> list[str]:
    out = []
    if p.old.is_symlink():
        out.append(f"{p.old} is already a link: it looks moved ({os.readlink(p.old)})")
    elif not p.old.is_dir():
        out.append(f"{p.old} is not a folder: nothing to move")
    if os.path.lexists(p.new):
        out.append(f"{p.new} already exists")
    if os.path.lexists(p.store_new):
        out.append(f"{p.store_new} already exists")
    if daemon_running():
        out.append("the daemon is running: stop it first")
    return out


def _add_new_paths(node, old: str, new: str) -> bool:
    """In every list under `node`, a string at or below `old` gets its `new` twin added after it."""
    changed = False
    if isinstance(node, dict):
        for v in node.values():
            changed |= _add_new_paths(v, old, new)
    elif isinstance(node, list):
        extra = []
        for item in node:
            if isinstance(item, (dict, list)):
                changed |= _add_new_paths(item, old, new)
            elif isinstance(item, str) and (item == old or item.startswith(old + "/")):
                twin = new + item[len(old):]
                if twin not in node and twin not in extra:
                    extra.append(twin)
        if extra:
            node.extend(extra)
            changed = True
    return changed


def _repoint(root: Path, frm: Path, to: Path) -> int:
    """Re-aim every link under `root` whose target is at or below `frm` at the same place under `to`.
    The account folders hold absolute links into the shared folder, and code compares those as text."""
    n = 0
    for d, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            link = Path(d) / name
            if not link.is_symlink():
                continue
            target = os.readlink(link)
            if target == str(frm) or target.startswith(str(frm) + "/"):
                tmp = link.with_name(link.name + ".repoint")
                os.symlink(str(to) + target[len(str(frm)):], tmp)
                os.replace(tmp, link)
                n += 1
    return n


def apply(p: Plan, daemon_running: Callable[[], bool]) -> list[str]:
    """The steps done, in order. Raises RuntimeError naming what stopped it, with the earlier steps
    still in place for `undo`."""
    stop = problems(p, daemon_running)
    if stop:
        raise RuntimeError("; ".join(stop))
    done = []
    os.rename(p.old, p.new)
    done.append(f"moved {p.old} to {p.new}")
    os.symlink(p.new, p.old)
    done.append(f"left a link at {p.old}")
    done.append(f"re-aimed {_repoint(p.new, p.old, p.new)} links inside it at the new path")
    if p.store_old.is_dir():
        os.rename(p.store_old, p.store_new)
        done.append(f"renamed the protected store to {p.store_new.name}")
    else:
        done.append("no protected store for the old home: nothing to rename")
    if p.settings.is_file():
        data = json.loads(p.settings.read_text())
        shutil.copy2(p.settings, p.backup)
        if _add_new_paths(data, str(p.old), str(p.new)):
            tmp = p.settings.with_name(p.settings.name + ".new")
            tmp.write_text(json.dumps(data, indent=2) + "\n")
            os.replace(tmp, p.settings)
            done.append("added the new paths next to the old in settings.json (original kept)")
        else:
            done.append("settings.json names the old home nowhere: unchanged")
    return done


def undo(p: Plan, daemon_running: Callable[[], bool] = lambda: False) -> list[str]:
    """Reverse what is still in place; a second run does nothing. Refuses while the daemon runs."""
    if daemon_running():
        raise RuntimeError("the daemon is running: stop it first")
    done = []
    if p.backup.is_file():
        shutil.copy2(p.backup, p.settings)
        p.backup.unlink()
        done.append("restored settings.json")
    if p.store_new.is_dir() and not os.path.lexists(p.store_old):
        os.rename(p.store_new, p.store_old)
        done.append("renamed the protected store back")
    if p.old.is_symlink() and os.path.realpath(p.old) == os.path.realpath(p.new):
        p.old.unlink()
        done.append(f"removed the link at {p.old}")
    if p.new.is_dir() and not os.path.lexists(p.old):
        n = _repoint(p.new, p.new, p.old)
        os.rename(p.new, p.old)
        done.append(f"re-aimed {n} links back and moved {p.new} back to {p.old}")
    return done or ["nothing to undo"]


def _daemon_running_for(home: Path) -> Callable[[], bool]:
    def check() -> bool:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        os.environ["OCP_HOME"] = str(home)
        os.environ.pop("HANDLINE_HOME", None)
        from handline.daemon import locks
        return locks.running_elsewhere() is not None
    return check


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=("check", "apply", "undo"))
    ap.add_argument("--user-home", default=str(Path.home()), help="the folder that holds .orcapipe (a copy, to try it)")
    args = ap.parse_args(argv)
    p = plan_for(Path(args.user_home))
    running = _daemon_running_for(p.old)
    if args.action == "check":
        print(f"move   {p.old} -> {p.new} (link left at {p.old})")
        print(f"store  {p.store_old.name} -> {p.store_new.name}  ({'present' if p.store_old.is_dir() else 'none'})")
        stop = problems(p, running)
        print("ready" if not stop else "blocked:\n  " + "\n  ".join(stop))
        return 0 if not stop else 1
    try:
        for line in (apply(p, running) if args.action == "apply" else undo(p, running)):
            print(line)
    except (RuntimeError, OSError, ValueError) as e:
        print(f"stopped: {e}\nrun `{sys.argv[0]} undo` to put back what was done", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
