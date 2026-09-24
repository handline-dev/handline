"""Actions run off the key loop (task #45, taking over #1615's bridge-working
branch as a requirement of the Textual port).

A key that acts hands the verb to a process of its own (`ocp.actrun`, one
per action) and returns at once; the caller's event loop keeps reading keys
and redrawing while it runs. `Jobs.lines()` is what a screen shows for it:
what it is, what it can be waiting for, and how long - past `actions.WAITS`'s
own bound for that verb it is stopped, not just flagged.

The old curses bridge (`buddyron/bridge-working`, unmerged) declared a verb's
longest wait and then let it run another `GRACE_S` (30s) past that before
even asking it to stop - dismiss said "60s" and nothing killed it before 90.
Here the declared bound *is* the stop: `terminate()` fires the moment a job
is older than its own `longest`, and `GRACE_S` is only the short window given
to exit cleanly before `kill()` - what a screen says and what it does agree.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from . import actions

ACT_COMMAND = [sys.executable, "-m", "ocp.actrun"]
GRACE_S = 2            # SIGTERM -> SIGKILL, once a job is past its own `longest`
QUIET_S = 0.4          # a job done sooner than this is never drawn
SLOW_S = 15            # past this a running job reads as taking a while
MAX_JOBS = 4

# What the line says an action is doing, by the key's name for it (agent verbs
# that are not already a gerund).
GERUND = {"wake": "waking", "close": "closing", "park": "parking", "fold": "folding",
          "unstall": "unstalling", "terminal": "going to",
          "cancel": "cancelling", "dismiss": "dismissing", "answer": "answering",
          "message": "messaging", "chat_reply": "messaging"}


def _took(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s}s" if s < 60 else f"{s // 60}m{s % 60:02d}s"


class Job:
    """One action running for the screen: what it is, who it is on, its process."""

    def __init__(self, verb: str, target: str, what: str, proc, started: float,
                 on_result: Callable[[bool, str], None] | None = None) -> None:
        self.verb, self.target, self.what, self.proc, self.started = verb, target, what, proc, started
        self.waits, self.longest = actions.WAITS[verb]
        self.stopped_at: float | None = None       # when `terminate()` was sent
        # Called once, when `reap()` collects the answer, with whether it
        # actually went and what it said - a screen that needs more than the
        # line `reap()` already returns (the chat box restoring a draft that
        # failed to send) hooks in here rather than every caller re-parsing
        # `lines()`'s text back into a bool.
        self.on_result = on_result


class JobLine:
    """One row `lines()` hands a screen to draw: the text and how urgent it is.
    `level` is 'normal', 'slow' (running a while) or 'danger' (past its bound
    or the way it ended), never a colour - drawing is the caller's."""

    def __init__(self, text: str, level: str = "normal") -> None:
        self.text, self.level = text, level


class Jobs:
    """The actions a screen has running. Nothing here draws or reads keys."""

    def __init__(self) -> None:
        self.running: list[Job] = []

    def start(self, verb: str, target: str, what: str, args: tuple = (),
              kwargs: dict | None = None,
              on_result: Callable[[bool, str], None] | None = None) -> str:
        """Begin `verb` in a process of its own. Returns a line only if it could
        not be begun - an empty string means it is on its way and will speak for
        itself once `reap()` collects it."""
        busy = next((j for j in self.running if j.target == target), None)
        if busy:
            return (f"{target}: still {busy.what} ({int(time.time() - busy.started)}s) - "
                    "one thing at a time on it")
        if len(self.running) >= MAX_JOBS:
            return f"{len(self.running)} actions are already running - wait for one to finish"
        # The child's stdin is a pipe closed right after the request, so nothing
        # it starts can read the keys this loop is reading.
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(Path(__file__).resolve().parents[1])] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
        try:
            proc = subprocess.Popen(
                ACT_COMMAND, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, env=env, start_new_session=True)
            proc.stdin.write(json.dumps({"verb": verb, "args": list(args), "kwargs": kwargs or {}}))
            proc.stdin.close()
            proc.stdin = None          # closed here; `communicate` must not close it again
        except (OSError, ValueError) as e:
            return f"could not start {what}: {e}"
        self.running.append(Job(verb, target, what, proc, time.time(), on_result))
        return ""

    def reap(self) -> list[str]:
        """What the finished ones said. Stops (SIGTERM, then SIGKILL after
        `GRACE_S`) any job whose age has passed its own declared `longest` -
        that bound is what a screen says, so it is the one that is kept."""
        said, now = [], time.time()
        for job in list(self.running):
            code = job.proc.poll()
            if code is None:
                age = now - job.started
                if job.stopped_at is None and age > job.longest:
                    self._signal(job, signal.SIGTERM)
                    job.stopped_at = now
                elif job.stopped_at is not None and now - job.stopped_at > GRACE_S:
                    self._signal(job, signal.SIGKILL)
                continue
            self.running.remove(job)
            if job.stopped_at is not None:
                # The leader (`job.proc`) exiting on SIGTERM does not mean the
                # group did: a grandchild that ignores SIGTERM, or is slow to
                # clean up, keeps the pipes `communicate()` reads from open
                # until it too is gone - SIGKILL the whole group once there is
                # no leader left to relay a gentler signal to.
                self._signal(job, signal.SIGKILL)
            out, err = job.proc.communicate()
            ok, text = self._said(job, code, out, err, now)
            said.append(text)
            if job.on_result:
                job.on_result(ok, text)
        return said

    def _signal(self, job: Job, sig: int) -> None:
        """Stops the whole process group `start_new_session` made `job.proc`
        the leader of, not only that one process: a verb that runs something
        of its own past the bound (fold's `claude -p`, an `orca` call) would
        otherwise outlive the "declared bound is the stop" a screen shows -
        and `communicate()` above would then block until that grandchild, not
        `job.proc`, lets go of the pipes it inherited."""
        try:
            os.killpg(job.proc.pid, sig)
        except ProcessLookupError:
            pass

    def _said(self, job: Job, code: int, out: str, err: str, now: float) -> tuple[bool, str]:
        """(ok, text). `ok` is `actions.perform`'s own bool (true for every
        verb but `chat_reply` - see its docstring); a job stopped for running
        past its bound answered nothing, so that is false outright."""
        if job.stopped_at is not None:
            return False, (f"{job.what}: no answer after {_took(job.longest)} - stopped. It may "
                           "have got part of the way; the row above is what is true.")
        answer = None
        for ln in reversed(out.splitlines()):
            try:
                answer = json.loads(ln)
                break
            except ValueError:
                continue
        if isinstance(answer, dict) and "said" in answer:
            text, ok = str(answer["said"]), bool(answer.get("ok"))
            return ok, (text if ok else f"{job.what} failed: {text}")
        tail = (err.strip().splitlines() or [f"exit {code}"])[-1]
        return False, f"{job.what} failed: {tail}"

    def lines(self, now: float | None = None) -> list[JobLine]:
        """One row per action that has been running long enough to be seen,
        oldest first, capped at 3 with a count for the rest."""
        now = time.time() if now is None else now
        rows: list[JobLine] = []
        for job in sorted(self.running, key=lambda j: j.started):
            age = now - job.started
            if age < QUIET_S:
                continue
            waits = f" - waiting for {job.waits}" if job.waits else ""
            if job.stopped_at is not None:
                rows.append(JobLine(f"{job.what}: no answer, stopping it ({_took(age)})", "danger"))
            elif age > job.longest:
                rows.append(JobLine(
                    f"{job.what}{waits} - {_took(age)}, past the longest that wait can take "
                    f"({_took(job.longest)}); stopping it", "danger"))
            else:
                level = "slow" if age > SLOW_S else "normal"
                rows.append(JobLine(
                    f"{job.what}{waits} · {_took(age)} · longest {_took(job.longest)}", level))
        if len(rows) > 3:
            rows = rows[:3] + [JobLine(f"+ {len(rows) - 3} more running")]
        return rows

    def note_on_quit(self) -> str:
        if not self.running:
            return ""
        return ("left running (they finish on their own): "
                + "; ".join(j.what for j in self.running))
