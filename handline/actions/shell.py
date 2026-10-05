"""A shell command the founder types into the screen's chat box as `!command`.

It is not sent to the agent: zsh runs it, in the open agent's worktree (home when there is none),
and the screen draws the command, its exit code and what it printed. Nothing here is stored.
Only the founder's own session may run one; an agent's session is refused, so no message can
make a command run.
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from ..infra import paths, protected, textsafe

KEEP_LINES = 20000      # the lines kept of one command's output; the oldest go first
GRACE_S = 2             # SIGTERM -> SIGKILL when a command is stopped
KEEP_FILES = 50         # the output files kept under <home>/shell; the oldest go first


def command_of(text: str) -> str | None:
    """The command a chat line asks for: `!ls` is `ls`. None when the line is not one, or has nothing after the `!`."""
    t = text.strip()
    return t[1:].strip() or None if t.startswith("!") else None


def cwd_for(con, agent: str | None) -> str:
    """The agent's worktree when it has one on disk, else the home directory."""
    if agent:
        from .details import agent_worktree
        tree = agent_worktree(con, agent)
        if Path(tree).is_dir():
            return tree
    return str(Path.home())


class ShellRun:
    """One command, running or done. `lines` is what it printed (stdout and stderr together), `code` its
    exit status (negative: the signal that ended it), None while it runs."""

    def __init__(self, command: str, cwd: str) -> None:
        if protected.in_agent_session():
            raise protected.NotTheFounder("a shell command is the founder's to run, not an agent's")
        self.command, self.cwd = command, cwd
        self.lines: list[str] = []
        self.dropped = 0            # lines past KEEP_LINES that were let go
        self.code: int | None = None
        self.stopped = False
        self.sent = False           # its result went to the agent
        self.started = time.time()
        self.ended: float | None = None
        self._lock = threading.Lock()
        self._file: Path | None = None
        try:
            self._proc = subprocess.Popen(["zsh", "-c", command], cwd=cwd, stdin=subprocess.DEVNULL,
                                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                          start_new_session=True)
        except OSError as e:        # no zsh, or the directory is gone
            self._proc = None
            self.lines, self.code, self.ended = [f"{type(e).__name__}: {e}"], 127, time.time()
            return
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        for raw in self._proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            with self._lock:
                self.lines.append(line)
                if len(self.lines) > KEEP_LINES:
                    del self.lines[0]
                    self.dropped += 1
        code = self._proc.wait()
        with self._lock:
            self.code, self.ended = code, time.time()

    @property
    def running(self) -> bool:
        return self.code is None

    def snapshot(self) -> tuple[list[str], int, int | None]:
        """(lines, dropped, code) as of now, consistent with each other."""
        with self._lock:
            return list(self.lines), self.dropped, self.code

    def seconds(self) -> float:
        return (self.ended or time.time()) - self.started

    def stop(self) -> None:
        """Ask the command, and what it started, to end; kill it if it has not within GRACE_S."""
        if not self.running or self._proc is None:
            return
        self.stopped = True
        pg = self._proc.pid
        self._signal(pg, signal.SIGTERM)
        threading.Timer(GRACE_S, lambda: self.running and self._signal(pg, signal.SIGKILL)).start()

    @staticmethod
    def _signal(pg: int, sig: int) -> None:
        try:
            os.killpg(pg, sig)
        except (ProcessLookupError, PermissionError):
            pass

    def save_output(self) -> Path:
        """Everything kept of the output, written to a file the agent can read; the same file on a second call."""
        if self._file is None:
            folder = paths.home() / "shell"
            folder.mkdir(parents=True, exist_ok=True)
            for old in sorted(folder.glob("*.txt"), key=lambda f: f.stat().st_mtime)[:-(KEEP_FILES - 1)]:
                old.unlink(missing_ok=True)
            self._file = folder / f"{int(self.started)}-{os.getpid()}-{id(self) % 10000}.txt"
        lines, dropped, _ = self.snapshot()
        kept = ([f"... {dropped} earlier lines left out"] if dropped else []) + lines
        self._file.write_text("".join(f"{safe}\n" for ln in kept for safe in textsafe.drawable(ln, tabs=None)))     # no escape codes for the agent to read
        return self._file

    def as_message(self) -> str:
        """The command, how it ended and the file its output is in, as the words that go to the agent."""
        lines, dropped, code = self.snapshot()
        status = "still running" if code is None else f"exit {code}"
        n = len(lines) + dropped
        return (f"I ran `{self.command}` in {self.cwd} ({status}). Its output ({n} line{'' if n == 1 else 's'}) "
                f"is in {self.save_output()}; read it there.")
