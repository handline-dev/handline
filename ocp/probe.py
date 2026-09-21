"""Ask an account where it stands, when nothing else can tell us.

A snapshot is written by the status line, and the status line only runs while a
session runs. So an account nobody is using has no current reading, and the
fail-closed rule then refuses to start one on it - which is the only thing that
would have given it a reading. Before the 5h window ends that is the right
answer anyway: the last figure we saw is a floor (`AccountStatus.idle_floor`).
Once the window has passed the floor describes a window that no longer exists,
and the only way to learn the truth is to spend a little on the account.

A probe is one short session and one trivial turn, killed the moment the status
line has written. It has to make a request, not merely start: a session that
has asked nothing has learned no rate limits and reports no five_hour block at
all, which reads as a quiet window - exactly the answer we must not guess. So
the keystrokes wait for the prompt to appear rather than being typed at a fixed
moment, and the probe reports whether a reading actually arrived.
"""

from __future__ import annotations

import os
import pty
import select
import shutil
import signal
import time

from . import accounts, status

# Rate limits are an account's, not a model's, so the cheapest model answers
# the question as well as any other. What takes the time is the session booting
# and the first response landing, not the turn itself.
PROBE_MODEL = "claude-haiku-4-5-20251001"
PROBE_TIMEOUT_S = 90
# A probe spends quota, so one account is not probed again inside this.
PROBE_COOLDOWN_S = 1800
PROMPT = "Reply with the single word: ok"
# How long the pty has to stay quiet before we believe the prompt is ready.
SETTLE_S = 2.0


def _spend(account: str, env: dict, timeout: int, since: int | None) -> bool:
    """Run one throwaway turn under a pty. True if a new measurement arrived.

    The status line is a TUI feature: `claude -p` is headless and writes no
    snapshot, which is why this cannot be a plain subprocess.
    """
    claude = shutil.which("claude")
    if not claude:
        raise RuntimeError("cannot find claude on PATH")
    pid, fd = pty.fork()
    if pid == 0:  # child
        # Nothing may fall out of this branch: after a failed exec the child
        # would go on to run the parent's body, and two processes would probe.
        try:
            os.environ.update(env)
            os.environ["TERM"] = "xterm-256color"
            os.environ["COLUMNS"] = "120"
            os.environ["LINES"] = "40"
            os.execv(claude, [claude, "--model", PROBE_MODEL, "--effort", "low",
                              "--disallowedTools", "Agent"])
        except BaseException:  # noqa: BLE001
            pass
        os._exit(127)
    fresh = False
    try:
        os.set_blocking(fd, False)
        started = time.time()
        last_out = None
        typed = False
        while time.time() - started < timeout:
            ready, _, _ = select.select([fd], [], [], 0.5)
            if ready:
                try:
                    if not os.read(fd, 65536):
                        break
                    last_out = time.time()
                except OSError:
                    break
            now = time.time()
            # The TUI draws itself and then goes quiet: that lull is the only
            # marker of "ready for input" we get without parsing the screen.
            # Typed before it, the keystrokes are dropped, no request is made,
            # and the session reports a quiet window it never measured.
            if not typed and last_out and now - last_out > SETTLE_S:
                os.write(fd, PROMPT.encode() + b"\r")
                typed = True
                continue
            # Done the moment the account reports a figure it measured in
            # this session - not merely a fresh snapshot. The status line
            # redraws as soon as the TUI paints, long before any request, and
            # that redraw carries the *previous* window's figure forward with
            # rl_carried set. Leaving on it is how a probe spends a session's
            # startup and learns nothing.
            if typed and _measured(status.read(account), since):
                fresh = True
                break
    finally:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.kill(pid, sig)
            except OSError:
                break
            time.sleep(0.5)
        try:
            os.waitpid(pid, 0)
        except OSError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass
    return fresh


def _measured(st: "status.AccountStatus", since: int | None) -> bool:
    """Has this account reported a 5h figure it observed since `since`?

    rl_at is when the figure was observed; collected_at is only when the line
    was drawn. A carried figure is the old window's, however new the snapshot.
    """
    if st.rl_lost or st.rl_at is None:
        return False
    return not st.rl_carried and (since is None or st.rl_at > since)


def due(st: "status.AccountStatus", last_probe: int | None) -> bool:
    """Is a probe the only way left to learn where this account stands?

    Only for an account whose 5h window has passed and whose snapshot is too
    old to describe it. A fresh reading answers the question by itself, and an
    open window is answered by the floor. Whether we could log in and ask is
    the caller's question, not this one's.
    """
    if not st.exists or st.error or not st.stale:
        return False
    # No reset to have passed is not a window that has: that is an account we
    # have never read a limit from, and probing it every half hour on the
    # chance forever is not asking a question, it is a habit.
    if not st.reset_5h or st.reset_5h > time.time():
        return False
    return not (last_probe and time.time() - last_probe < PROBE_COOLDOWN_S)


def unread(st: "status.AccountStatus", last_probe: int | None) -> bool:
    """Has this logged-in account never been read, and never been asked?

    A new account has no status.json until a session runs on it, and nothing
    ranks it until it has one, so it would wait for a use it never gets. One
    probe gives it its first reading. Once asked (`last_probe` is set) it is
    not asked again on this ground, whether or not a reading came back:
    `due` keeps refusing an absent snapshot so that an account which cannot
    answer is not probed every half hour forever.
    """
    return not st.exists and not last_probe


def run(account: str, timeout: int = PROBE_TIMEOUT_S) -> "status.AccountStatus":
    """Probe this account and return what it says afterwards.

    The reading is whatever the status line wrote; a probe that failed to write
    one leaves the old snapshot in place and the caller still sees it as stale,
    which is the same refusal it had before. Failing is therefore safe, and
    that is why nothing here raises on a bad turn: an account at the wall ends
    its turn on a limit error, and the status line has written by then.
    """
    before = status.read(account).rl_at
    try:
        _spend(account, accounts.env_for(account, None), timeout, before)
    except (OSError, RuntimeError):
        pass
    return status.read(account)
