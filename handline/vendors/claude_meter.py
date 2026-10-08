"""Claude's Meter: where an account stands, asked at Anthropic's usage endpoint
with the account's own OAuth credential.


It asks the question the way Orca does: the account's own OAuth credential
against Anthropic's usage endpoint. A quiet account costs one HTTPS GET, no
session and no quota. Measured against a probe run on the same account six
minutes apart (#1364), the two agreed on every figure including the reset
instant.

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

from ..team import accounts
from .base import Reading, Window
from . import opencode
from .claude import keychain_service
from .. import vendors

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
FIVE_HOUR_MINUTES = 300
SEVEN_DAY_MINUTES = 10080


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


def _access_token_detailed(acct: str) -> tuple[str | None, bool]:
    """The account's OAuth access token, for one request, plus whether the
    credential is actually gone.

    The only place in ocp that reads the secret. It is never returned to a
    caller outside this module, never logged and never written.

    `missing` is True only when the Keychain item itself is not there
    (`security` exits 44, `errSecItemNotFound`) or the item is there but
    carries no `accessToken` - the credential really is gone. Every other
    failure (a timeout, a Keychain daemon hiccup, a malformed stdout) is
    "could not read it", not "it is not there", and carries missing=False -
    the same distinction `_read_claude` makes for the HTTPS side.
    """
    service = keychain_service(vendors.config_dir(acct))
    try:
        r = subprocess.run(["security", "find-generic-password", "-w", "-s", service],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None, False
    if r.returncode != 0:
        return None, r.returncode == 44
    try:
        token = (json.loads(r.stdout).get("claudeAiOauth") or {}).get("accessToken")
    except (json.JSONDecodeError, AttributeError):
        return None, False
    if isinstance(token, str) and token.strip():
        return token, False
    return None, True


def _read_claude(acct: str, refresh: bool) -> tuple[Reading | None, bool]:
    """Claude's endpoint, asked with claude's credential.

    The second element of the return is True only for a 401/403 (`urlopen`
    raises `HTTPError`, a `URLError` subclass, for any non-2xx status - it
    never reaches the `resp.status` check below) or a credential the
    Keychain itself says is gone (`_access_token_detailed`'s `missing`).
    Every other failure (timeout, DNS, a 429/5xx, a malformed body, a
    Keychain read that merely failed rather than came back empty) is
    transient and carries False. `recover.recover` is the only reader of
    this: it marks needs-relogin, so it stops retrying, only on True -
    fail-closed itself never looks at that mark.
    """
    if refresh:
        refresh_credential(acct)
    token, missing = _access_token_detailed(acct)
    if not token:
        return None, missing
    req = urllib.request.Request(
        USAGE_URL, headers={"Authorization": f"Bearer {token}", **USAGE_HEADERS})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            if resp.status != 200:
                return None, False
            payload = json.load(resp)
    except urllib.error.HTTPError as e:
        return None, e.code in (401, 403)
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None, False
    if not isinstance(payload, dict):
        return None, False
    five, seven = payload.get(FIVE_HOUR), payload.get(SEVEN_DAY)
    return Reading(
        account=acct,
        windows=(Window(FIVE_HOUR_MINUTES, _pct(five), _reset(five)),
                 Window(SEVEN_DAY_MINUTES, _pct(seven), _reset(seven))),
        at=int(time.time()),
    ), False


class ClaudeMeter:
    """`Claude.meter()`: the module's read, as the Meter Protocol names it."""

    # A dead token is renewed by spending one real turn on the account.
    renews_by_turn = True

    def read_usage(self, acct: str, refresh: bool = True) -> tuple[Reading | None, bool]:
        # Temporary, by the founder's word: an account that runs GLM spends the coding plan, not claude's.
        if opencode.zai_key(vendors.config_dir(acct)):
            return vendors.get("opencode").meter().read_usage(acct, refresh)
        return _read_claude(acct, refresh)
