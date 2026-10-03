"""The tmux backend: terminals are windows of one session on a server ocp owns.

The server is its own (`tmux -L ocp`), so ocp never touches a person's tmux, and a pane id
(`%12`) is a handle that means one thing. A window starts the default shell and the agent's
command is typed into it, as Orca does: the agent is a child of a shell that outlives it, so
`/exit` leaves the terminal standing and a wake types `hl launch` into it again.

What tmux cannot do that Orca does, and how each is covered here:
  - tell that a TUI is idle: `wait_idle` reads the screen until it has stopped changing and
    shows no turn in progress (WORKING_MARKERS, claude's wording);
  - watch a prompt land: `send` ignores `wait_submit_s`; the agent's own hook is the witness;
  - draw a team: there are no runs or tasks (`supports_runs` is False), so none are made;
  - register repos: there is no registry, a git repository is simply known.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from ...infra import names, textsafe
from .base import Backend, not_a_repo_root

SOCKET = "ocp"
SESSION = "ocp"
# A detached session has no client to size it, and a TUI drawn at 80x24 hides what the screen readers look for.
SESSION_SIZE = ("-x", "200", "-y", "50")
TMUX_TIMEOUT_S = 15

# Between typing a line and its Enter: a shell or TUI that gets both at once can take the Enter as part
# of a paste and not run the line.
ENTER_DELAY_S = 0.4
# How long the screen must hold still to count as idle, and how often it is looked at.
IDLE_SETTLE_S = 1.5
IDLE_POLL_S = 0.5
# Text a working claude draws in its status row and an idle one does not.
WORKING_MARKERS = ("esc to interrupt",)
WORKING_ROWS = 12

# A lone terminal escape sequence is sent as the key tmux names, not as bytes tmux may deliver one at a time.
_KEYS = {"\x1b[A": "Up", "\x1b[B": "Down", "\x1b[C": "Right", "\x1b[D": "Left", "\x1b": "Escape", "\x03": "C-c"}

_PANE = re.compile(r"^%\d+$")
_NO_SERVER = ("no server running", "error connecting to", "no sessions", "can't find session")


def _socket() -> str:
    return names.getenv("OCP_TMUX_SOCKET") or SOCKET


def _tmux(args: list[str], timeout: int = TMUX_TIMEOUT_S, stdin: str | None = None) -> tuple[int, str, str]:
    """`tmux -L <socket> <args>` as (exit code, stdout, stderr). A tmux that cannot run is (127, "", why)."""
    try:
        r = subprocess.run(["tmux", "-L", _socket(), *args], capture_output=True, text=True,
                           timeout=timeout, input=stdin)
    except (OSError, subprocess.SubprocessError) as e:
        return 127, "", textsafe.oneline(str(e))
    return r.returncode, r.stdout, textsafe.oneline(r.stderr.strip())


def _git(repo: str, *args: str) -> tuple[int, str]:
    try:
        r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return 127, textsafe.oneline(str(e))
    return r.returncode, textsafe.oneline((r.stderr or r.stdout).strip())


class TmuxBackend(Backend):
    name = "tmux"

    def available(self) -> bool:
        return shutil.which("tmux") is not None

    def knows_handle(self, handle: str) -> bool:
        return bool(_PANE.match(handle or ""))

    def own_terminal(self) -> tuple[str | None, str | None]:
        """The pane we run in, only when it is on our server: a pane id of someone else's tmux means
        nothing on ours."""
        pane, server = os.environ.get("TMUX_PANE"), os.environ.get("TMUX", "")
        if not pane or not _PANE.match(pane) or os.path.basename(server.split(",")[0]) != _socket():
            return None, None
        return pane, os.getcwd()

    def _server_up(self) -> bool:
        return _tmux(["has-session", "-t", SESSION])[0] == 0

    def create_terminal(self, command: str, cwd: str | None = None,
                        title: str | None = None) -> tuple[str | None, str]:
        place = ["-c", cwd] if cwd else []
        home = names.getenv("OCP_HOME")
        env = ["-e", f"OCP_HOME={home}", "-e", f"HANDLINE_HOME={home}"] if home else []
        name = ["-n", re.sub(r"[.:]", "_", title)] if title else []
        fmt = ["-P", "-F", "#{pane_id}"]
        if self._server_up():
            code, out, err = _tmux(["new-window", "-t", f"{SESSION}:", *name, *place, *env, *fmt])
        else:
            code, out, err = _tmux(["new-session", "-d", "-s", SESSION, *SESSION_SIZE, *name, *place, *env, *fmt])
        pane = out.strip()
        if code != 0 or not _PANE.match(pane):
            return None, err or f"tmux gave no pane id ({out.strip()[:80]!r})"
        ok, why = self.send(pane, command)
        if not ok:
            self.close_terminal(pane)
            return None, f"could not type the command into the new window: {why}"
        return pane, ""

    def close_terminal(self, handle: str, tab: bool = True) -> tuple[bool, str]:
        if not _PANE.match(handle):
            return False, f"not a tmux pane: {handle!r}"
        code, _out, err = _tmux(["kill-window" if tab else "kill-pane", "-t", handle])
        return code == 0, err

    def list_terminals(self) -> set[str] | None:
        code, out, err = _tmux(["list-panes", "-a", "-F", "#{pane_id}"])
        if code == 0:
            return {p for p in out.split() if _PANE.match(p)}
        # A server that is not running has no terminals: that is an answer. Anything else is not one.
        return set() if any(m in err.lower() for m in _NO_SERVER) else None

    def read_screen(self, handle: str, limit: int = 40) -> dict | None:
        """The bottom `limit` rows of the pane. The rows below the last drawn one are dropped, so the
        bottom of `tail` is the bottom of what is on screen, as the readers above expect. A `limit`
        taller than the pane reaches into its scrollback (`-S`), which is how a reader widens its read
        when a dialog's top border has scrolled out of the frame."""
        if not _PANE.match(handle):
            return None
        code, out, _err = _tmux(["capture-pane", "-p", "-t", handle, "-S", f"-{max(limit, 1)}"], timeout=30)
        if code != 0:
            return None
        rows = [r.rstrip() for r in out.split("\n")]
        while rows and not rows[-1]:
            rows.pop()
        return {"handle": handle, "status": "running", "tail": rows[-limit:], "source": "screen"}

    def send(self, handle: str, text: str, enter: bool = True,
             wait_submit_s: int | None = None) -> tuple[bool, str]:
        if not _PANE.match(handle):
            return False, f"not a tmux pane: {handle!r}"
        if text:
            if text in _KEYS:
                code, _o, err = _tmux(["send-keys", "-t", handle, _KEYS[text]])
            elif "\n" in text:
                # Several lines go as one bracketed paste, so a newline is text and not a submit.
                code, _o, err = _tmux(["load-buffer", "-b", "ocp-send", "-"], stdin=text)
                if code == 0:
                    code, _o, err = _tmux(["paste-buffer", "-p", "-d", "-b", "ocp-send", "-t", handle])
            else:
                code, _o, err = _tmux(["send-keys", "-t", handle, "-l", "--", text])
            if code != 0:
                return False, err
        if enter:
            if text:
                time.sleep(ENTER_DELAY_S)
            code, _o, err = _tmux(["send-keys", "-t", handle, "Enter"])
            if code != 0:
                return False, err
        return True, ""

    def wait_idle(self, handle: str, timeout_ms: int) -> bool:
        """Idle: the frame has not changed for IDLE_SETTLE_S and shows no turn in progress."""
        deadline = time.monotonic() + timeout_ms / 1000
        last, since = None, time.monotonic()
        while True:
            term = self.read_screen(handle)
            if term is None:
                return False
            tail = term["tail"]
            now = time.monotonic()
            if tail != last:
                last, since = tail, now
            elif now - since >= IDLE_SETTLE_S and not _working(tail):
                return True
            left = deadline - now
            if left <= 0:
                return False
            time.sleep(min(IDLE_POLL_S, left))

    def focus(self, handle: str) -> tuple[bool, str]:
        if not _PANE.match(handle):
            return False, f"not a tmux pane: {handle!r}"
        code, _out, err = _tmux(["select-window", "-t", handle])
        return code == 0, err

    def ensure_repo(self, path: str, present: bool = True) -> tuple[str, str]:
        path = os.path.realpath(os.path.expanduser(path))
        if os.path.exists(os.path.join(path, ".git")):
            return "known", f"{path} is a git repository"
        return not_a_repo_root(path)

    def create_worktree(self, repo: str, name: str, base_ref: str) -> tuple[str | None, str]:
        """A sibling directory `<repo>.worktrees/<name>` on a new branch `<name>`; a name already
        taken becomes `<name>-2`, as Orca does, rather than touching the first."""
        root = Path(repo).resolve()
        parent = root.parent / f"{root.name}.worktrees"
        for n in range(1, 10):
            leaf = name if n == 1 else f"{name}-{n}"
            path = parent / leaf
            if path.exists():
                continue
            code, why = _git(str(root), "worktree", "add", "--no-track", "-b", leaf, str(path), base_ref)
            if code == 0:
                return str(path), ""
            if "already exists" in why:      # the branch name is taken; try the next
                continue
            return None, f"worktree create failed: {why}"
        return None, f"worktree create failed: {name} through {name}-9 are all taken"


def _working(tail: list[str]) -> bool:
    return any(m in row.lower() for row in tail[-WORKING_ROWS:] for m in WORKING_MARKERS)
