"""The terminal boundary: everything ocp asks of the program that hosts an agent's terminal.

`handline.session.term` (the terminal layer: dialogs, idle, clear, close, relaunch) and
`handline.team.spawn` reach the host only through a `Backend`. Orca is one; tmux is another.
A backend knows how to run a command in a terminal, list the live ones, read the
rendered screen, type into one, wait for it to go quiet, and make a worktree. It knows
nothing about agents, vendors or dialogs: reading what the screen means stays above it.

Every method answers rather than raises, and "could not ask" is never "nothing there":
`list_terminals` and `read_screen` return None when they could not answer.
"""
from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from ...infra import textsafe


def enclosing_repo(path: str) -> str | None:
    """The top folder of the git repository that holds `path`, or None when none does. A `.git`
    file (a linked worktree, a submodule) counts as a top, as a folder does."""
    here = os.path.realpath(path)
    while True:
        if os.path.exists(os.path.join(here, ".git")):
            return here
        up = os.path.dirname(here)
        if up == here:
            return None
        here = up


def _tracked_in(top: str, path: str) -> bool:
    """Whether `top`'s repository tracks files under `path`: the folder belongs to it."""
    try:
        r = subprocess.run(["git", "-C", top, "ls-files", "--", os.path.relpath(path, top)],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and bool(textsafe.oneline(r.stdout.strip()))


def not_a_repo_root(path: str) -> tuple[str, str]:
    """('not_root', why) for a folder whose files a repository above it tracks, else ('not_git', why).
    A host takes only a repo's top folder, so `git init` is the wrong advice for the first; a
    folder merely sitting untracked in a repo is not part of it and does need its own."""
    top = enclosing_repo(path)
    if top and top != path and _tracked_in(top, path):
        return "not_root", (f"{path} is part of the git repository {top}, not its top folder, "
                            f"so it cannot be a repo: set `repo:` to {top}")
    return "not_git", f"{path} is not a git repository: run `git init` there"


@dataclass(frozen=True)
class WorktreeInfo:
    """What a host keeps about one worktree: its id, label, comment, parent (an id), status and
    child ids. A field the host does not have is None, or empty for the children."""
    id: str | None = None
    label: str | None = None
    comment: str | None = None
    parent: str | None = None
    status: str | None = None
    children: tuple[str, ...] = ()


class Unreadable:
    """The answer of `worktree_info` when the host is there but did not give a usable one: not
    the same as "no such worktree", which is None."""

    def __repr__(self) -> str:
        return "UNREADABLE"


UNREADABLE = Unreadable()


class Backend:
    name = ""
    # Whether the host draws a team: orchestration runs and tasks with workers under them.
    # Without it the run and task methods are never called by anything that matters.
    supports_runs = False
    # Whether the host keeps labels and parent links on worktrees that `ocp rename` and a worktree move
    # must carry over, and answers the worktree methods about them.
    supports_labels = False
    # Whether the host installs Claude hooks into ~/.claude/settings.json that `StateHooks` mirrors. A host
    # without them has the agent's own `hl hook` entries as its witness, and the doctor does not look.
    supports_agent_hooks = False

    # -- the host itself
    def available(self) -> bool:
        raise NotImplementedError

    def knows_handle(self, handle: str) -> bool:
        """Whether `handle` has the shape this host gives its terminals: a handle saved under another
        host is not one, and reads as gone here."""
        return True

    def own_terminal(self) -> tuple[str | None, str | None]:
        """(handle, worktree id) of the terminal this process runs in, when the host started it."""
        raise NotImplementedError

    def manual(self, op: str, **a: str) -> str:
        """The host's own words for what to do by hand when ocp could not: `terminal` (`worktree`,
        `command`), `start` (`wt` or None, `repo`, `name`, `base_ref`, `command`: its steps, one per
        line), `label` (`path`, `label`) and `parent` (`child`, `path`)."""
        if op == "terminal":
            return f'{self.name}: a terminal in {a["worktree"]} running "{a["command"]}"'
        if op == "start":
            if a["wt"]:
                return f'{self.name}: a terminal there running "{a["command"]}"'
            return (f'{self.name}: a worktree of {a["repo"]} named {a["name"]} from {a["base_ref"]}, '
                    f'then a terminal in it running "{a["command"]}"')
        if op == "label":
            return f'{self.name}: label {a["path"]} {a["label"]}'
        if op == "parent":
            return f'{self.name}: make {a["child"]} a child of {a["path"]}'
        raise ValueError(f"no manual for {op!r}")

    # -- terminals
    def create_terminal(self, command: str, cwd: str | None = None,
                        title: str | None = None) -> tuple[str | None, str]:
        """A new terminal whose shell runs `command`, in `cwd` (None: where this process is):
        (its handle, "") or (None, why not). A terminal that started but whose handle could not be
        read is (None, ""): it is there, and nothing can address it."""
        raise NotImplementedError

    def close_terminal(self, handle: str, tab: bool = True) -> tuple[bool, str]:
        raise NotImplementedError

    def list_terminals(self) -> set[str] | None:
        raise NotImplementedError

    def read_screen(self, handle: str, limit: int = 40) -> dict | None:
        """{"tail": [bottom `limit` rows of the rendered frame], "source": "screen", ...} or None."""
        raise NotImplementedError

    def send(self, handle: str, text: str, enter: bool = True,
             wait_submit_s: int | None = None) -> tuple[bool, str]:
        """Type `text` (raw bytes pass through, escape sequences included), then Enter if asked.
        `wait_submit_s` asks the host to watch the prompt land, where it can."""
        raise NotImplementedError

    def size(self, handle: str) -> tuple[int, int] | None:
        """(columns, rows) of the terminal, or None when this host does not tell."""
        return None

    def resize(self, handle: str, cols: int, rows: int) -> tuple[bool, str]:
        """Make the terminal cols by rows. A host that cannot says so; the terminal channel refuses a resize then."""
        return False, f"{self.name} cannot resize a terminal"

    def wait_idle(self, handle: str, timeout_ms: int,
                  turn_on_screen: Callable[[list[str]], bool] | None = None) -> bool:
        """Whether the terminal's program is quiet, within `timeout_ms`. A timeout is False.
        `turn_on_screen` is the vendor's reading of whether a rendered screen shows a turn in
        progress, for a host that must read the screen to tell; a host with its own witness ignores it."""
        raise NotImplementedError

    def focus(self, handle: str) -> tuple[bool, str]:
        raise NotImplementedError

    # -- repos and worktrees
    def ensure_repo(self, path: str, present: bool = True) -> tuple[str, str]:
        """Make sure the host knows `path` as a repo: (state, what to tell the founder). State is
        'known', 'added', 'not_git', 'not_root' (inside a repo, not its top), 'no_orca' (the host is
        missing) or 'failed'. `present` is the
        caller's word on whether the host is there, so it is asked once, in one place."""
        raise NotImplementedError

    def create_worktree(self, repo: str, name: str, base_ref: str) -> tuple[str | None, str]:
        """(path of a new worktree named `name` branched from `base_ref`, "") or (None, why not)."""
        raise NotImplementedError

    def worktree_info(self, path: str, timeout: int = 180) -> WorktreeInfo | None | Unreadable:
        """The host's entry for the checkout at `path`; None when it has no such worktree;
        UNREADABLE when it would not say. Only a host with `supports_labels` keeps any."""
        return UNREADABLE

    def set_worktree(self, path: str, label: str | None = None, comment: str | None = None,
                     parent: str | None = None, status: str | None = None,
                     timeout: int = 180) -> tuple[bool, str]:
        """Set the label, comment, parent (a worktree id) or status of the worktree at `path`:
        (took, the host's reply)."""
        return False, f"{self.name} keeps no worktree labels"

    def adopt_worktree(self, child: str, path: str, timeout: int = 180) -> tuple[bool, str]:
        """Make the worktree with id `child` a child of the one at `path`: (took, the host's reply)."""
        return False, f"{self.name} keeps no worktree parents"

    def terminals_in(self, path: str, worktree_id: str | None = None) -> list[str] | None:
        """The handles of the terminals open in the worktree at `path` (or with id `worktree_id`);
        None when the host would not list them."""
        return None

    # -- orchestration (only a host with supports_runs)
    def run_current(self) -> str | None:
        return None

    def run_create(self, objective: str) -> str | None:
        return None

    def run_exists(self, run: str) -> bool:
        return False

    def run_use(self, run: str) -> bool:
        return False

    def task_create(self, spec: str, title: str, display_name: str, run: str,
                    parent: str | None = None) -> str | None:
        return None

    def dispatch(self, task: str, handle: str, run: str) -> str | None:
        return None
