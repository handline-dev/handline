"""Ask an account where it stands without spending any of it.

`probe.py` explains why a quiet account has no reading: the status line only
runs while a session runs, so an account nobody is using goes silent, and the
fail-closed rule then refuses to start the one session that would break the
silence. Its answer is to spend a little of the account - a pty, a session
boot, one Haiku turn, up to 90s.

This module asks the same question the way Orca does: the account's own OAuth
credential against Anthropic's usage endpoint. A quiet account costs one HTTPS
GET, no session and no quota. Measured against a `probe.py` run on the same
account six minutes apart (#1364), the two agreed on every figure including the
reset instant.

Two measurements make it work, and both are worth stating because neither is
obvious (#1360, #1364):

  - The endpoint answers for **any** registered account. Orca reports only the
    one it calls "active", but that ceiling is Orca's, not the API's.
  - The access token expires about eight hours after it was last renewed, and
    Claude Code renews it **only while a session runs** - so a quiet account,
    the one we are asking about, is exactly the one whose token has died.
    `claude auth status` renews it and writes it back itself, which is why
    nothing here ever stores a credential. On a token that is still fresh it
    is a no-op, so calling it every time is free.

The token is read into memory for the one request and never returned, logged
or written anywhere. `accounts.logged_in` deliberately avoids the secret
entirely; this module cannot, because the request needs it, so it keeps the
exposure to a single function.
"""
from __future__ import annotations

import datetime
import json
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from . import accounts, paths

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
# What Claude Code itself sends. The endpoint is part of its OAuth surface, not
# the public API, and it is versioned by this header rather than by a path.
USAGE_HEADERS = {
    "anthropic-beta": "oauth-2025-04-20",
    "User-Agent": "claude-code/2.1.0",
}
HTTP_TIMEOUT_S = 15
# `claude auth status` has to start the CLI, so it is not instant.
REFRESH_TIMEOUT_S = 40

# The two windows ocp gates on, by the duration the endpoint keys them under.
FIVE_HOUR = "five_hour"
SEVEN_DAY = "seven_day"


@dataclass(frozen=True)
class Reading:
    """One account's usage as the endpoint reported it.

    `pct_*` is None when the endpoint returned no such window, which is a
    reading and not an absence - it means nothing has been spent in it. The
    collector's snapshot draws the same distinction, so the two agree.
    """
    account: str
    pct_5h: int | None
    reset_5h: int | None
    pct_7d: int | None
    reset_7d: int | None
    at: int


def _reset(window) -> int | None:
    """A window's `resets_at` as a unix second.

    None is normal here: a window nothing has been spent in reports no reset,
    which is how the endpoint says the window is untouched rather than absent.
    """
    if not isinstance(window, dict):
        return None
    value = window.get("resets_at")
    if not isinstance(value, str) or not value.strip():
        return None
    # `fromisoformat` learned to read a trailing Z only in 3.11, and ocp runs
    # under `env python3` - 3.9 on this machine. Live traffic sends +00:00, so
    # a Z would parse here and return None on the account that actually runs
    # it. None is not an inert failure either: it is how this reports a window
    # nothing has been spent in, so a misparse would read as a *reading*.
    if value.endswith(("Z", "z")):
        value = value[:-1] + "+00:00"
    try:
        return int(datetime.datetime.fromisoformat(value).timestamp())
    except (ValueError, OverflowError):
        return None


def _pct(window) -> int | None:
    """A window's utilisation as a whole percent, or None when it is absent.

    The endpoint sends `utilization` as a float; every reader in ocp compares
    whole percents, and `status.py` already rounds up for the same reason (raw
    floats carry noise like 28.999999999999996).
    """
    if not isinstance(window, dict):
        return None
    v = window.get("utilization")
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return None
    return max(0, min(100, int(v + 0.5)))


def refresh_credential(acct: str, timeout: int = REFRESH_TIMEOUT_S) -> bool:
    """Renew the account's access token if it is near or past expiry.

    This is `claude auth status` against the account's own config dir. Claude
    Code decides whether a renewal is needed and performs the Keychain write,
    so ocp neither judges expiry nor stores anything. Measured: on a token
    valid for another eight hours it changed nothing; on one that had expired
    two hours earlier it renewed it (#1364). It makes no model call, so it
    costs no quota.

    True when the CLI answered, which is not a promise that a renewal happened
    - only that nothing is in the way of one.
    """
    claude = shutil.which("claude")
    if not claude:
        return False
    try:
        r = subprocess.run([claude, "auth", "status", "--json"],
                           capture_output=True, text=True, timeout=timeout,
                           env=accounts.env_for(acct))
    except (OSError, subprocess.SubprocessError):
        return False
    if r.returncode != 0:
        return False
    try:
        return json.loads(r.stdout).get("loggedIn") is True
    except (json.JSONDecodeError, AttributeError):
        return False


def _access_token(acct: str) -> str | None:
    """The account's OAuth access token, for one request.

    The only place in ocp that reads the secret. It is never returned to a
    caller outside this module, never logged and never written.
    """
    service = accounts.keychain_service(paths.account_claude(acct))
    try:
        r = subprocess.run(["security", "find-generic-password", "-w", "-s", service],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    try:
        token = (json.loads(r.stdout).get("claudeAiOauth") or {}).get("accessToken")
    except (json.JSONDecodeError, AttributeError):
        return None
    return token if isinstance(token, str) and token.strip() else None


def read(acct: str, refresh: bool = True) -> Reading | None:
    """Where this account stands now, or None when it could not be asked.

    None is "could not look", never "nothing has been spent" - the same
    distinction `orca.live_terminals` is careful about, and the reason this
    returns an object only when the endpoint actually answered.

    `refresh=False` skips the renewal, for a caller that already knows the
    token is live - an account with a session running on it, say, where Claude
    Code is renewing it anyway and two writers would race.
    """
    if refresh:
        refresh_credential(acct)
    token = _access_token(acct)
    if not token:
        return None
    req = urllib.request.Request(
        USAGE_URL, headers={"Authorization": f"Bearer {token}", **USAGE_HEADERS})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            if resp.status != 200:
                return None
            payload = json.load(resp)
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    five, seven = payload.get(FIVE_HOUR), payload.get(SEVEN_DAY)
    return Reading(
        account=acct,
        pct_5h=_pct(five), reset_5h=_reset(five),
        pct_7d=_pct(seven), reset_7d=_reset(seven),
        at=int(time.time()),
    )
