"""The terminal boundary: everything ocp asks of the program that hosts an agent's terminal.

`ocp.session.orca` (the terminal layer: dialogs, idle, clear, close, relaunch) and
`ocp.team.spawn` reach the host only through a `Backend`. Orca is one; tmux is another.
A backend knows how to run a command in a terminal, list the live ones, read the
rendered screen, type into one, wait for it to go quiet, and make a worktree. It knows
nothing about agents, vendors or dialogs: reading what the screen means stays above it.

Every method answers rather than raises, and "could not ask" is never "nothing there":
`list_terminals` and `read_screen` return None when they could not answer.
"""
from __future__ import annotations


class Backend:
    name = ""
    # Whether the host draws a team: orchestration runs and tasks with workers under them.
    # Without it the run and task methods are never called by anything that matters.
    supports_runs = False
    # Whether the host keeps labels and parent links on worktrees that `ocp rename` and a worktree move
    # must carry over, and answers `host_cli` about them.
    supports_labels = False

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

    def wait_idle(self, handle: str, timeout_ms: int) -> bool:
        """Whether the terminal's program is quiet, within `timeout_ms`. A timeout is False."""
        raise NotImplementedError

    def focus(self, handle: str) -> tuple[bool, str]:
        raise NotImplementedError

    # -- repos and worktrees
    def ensure_repo(self, path: str, present: bool = True) -> tuple[str, str]:
        """Make sure the host knows `path` as a repo: (state, what to tell the founder). State is
        'known', 'added', 'not_git', 'no_orca' (the host is missing) or 'failed'. `present` is the
        caller's word on whether the host is there, so it is asked once, in one place."""
        raise NotImplementedError

    def create_worktree(self, repo: str, name: str, base_ref: str) -> tuple[str | None, str]:
        """(path of a new worktree named `name` branched from `base_ref`, "") or (None, why not)."""
        raise NotImplementedError

    def host_cli(self, args: list[str], timeout: int = 180) -> tuple[bool, str]:
        """One command to the host's own CLI, as (ok, its text). Only a host with `supports_labels` has
        one; the others say so."""
        return False, f"{self.name} has no CLI to ask"

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
