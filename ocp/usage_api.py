"""Ask an account where it stands without spending any of it.

Rate limits ride on API responses, so an account nobody is using cannot say
where it stands, and the fail-closed rule then refuses to start the one session
that would have told us. ocp used to answer that with `probe.py`: a pty, a
session boot and one Haiku turn, up to 90 seconds, spending the very quota it
was asking about. That module is deleted (#1447); this is what replaced it.

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
from dataclasses import dataclass

from . import accounts, paths, vendors
from .vendors import claude as claudemod
from .vendors import opencode as ocmod

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

# How far a `resets_at` may be in the past before the window counts as ended.
# `resets_at` is the server's and the comparison is against a local clock, so
# the slack is for ordinary skew, not for a judgement call.
CLOCK_SKEW_S = 60

# The two windows ocp gates on, by the duration the endpoint keys them under.
FIVE_HOUR = "five_hour"
SEVEN_DAY = "seven_day"


@dataclass(frozen=True)
class Reading:
    """One account's usage as the endpoint reported it.

    `pct_*` is None when the endpoint returned no such window, which is a
    reading and not an absence - it means nothing has been spent in it.
    `status.py` draws the same distinction when it reads the file back.
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
    service = claudemod.keychain_service(paths.account_claude(acct))
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
    Code is renewing it anyway and two writers would race. (It means nothing
    to a vendor whose credential does not expire; it is simply ignored.)

    Each vendor is asked at its own endpoint with its own credential, read in
    memory for the one request by the driver's meter path (KICKOFF rule 1 as
    amended, #2010). A vendor with no meter path is not asked.
    """
    return read_detailed(acct, refresh)[0]


def read_detailed(acct: str, refresh: bool = True) -> tuple[Reading | None, bool]:
    """`read`, plus whether a failure was the credential itself being refused.

    The second element is only ever True for the claude vendor, and only for
    a 401/403 or a refresh that still leaves no token at all - the one class
    of failure a caller like `recover.recover` needs to tell apart from a
    timeout, a DNS blip or a 429/5xx right after a real turn, none of which
    say the credential is dead.
    """
    vendor = vendors.account_vendor(acct)
    if vendor == claudemod.Claude.name:
        return _read_claude(acct, refresh)
    if vendor == ocmod.Opencode.name:
        drv = vendors.get(vendor)
        windows = drv.usage_reading(vendors.config_dir(acct))
        if windows is None:
            return None, False
        pct_5h, reset_5h, pct_7d, reset_7d = windows
        return Reading(account=acct, pct_5h=pct_5h, reset_5h=reset_5h,
                       pct_7d=pct_7d, reset_7d=reset_7d, at=int(time.time())), False
    return None, False


def _read_claude(acct: str, refresh: bool) -> tuple[Reading | None, bool]:
    """Claude's endpoint, asked with claude's credential.

    The second element of the return is True only for a 401/403 (`urlopen`
    raises `HTTPError`, a `URLError` subclass, for any non-2xx status - it
    never reaches the `resp.status` check below) or a refresh that still
    leaves no token: the credential itself was refused. Every other failure
    (timeout, DNS, a 429/5xx, a malformed body) is transient and carries
    False, so it is never mistaken for the one case fail-closed should keep
    refusing forever.
    """
    if refresh:
        refresh_credential(acct)
    token = _access_token(acct)
    if not token:
        return None, True
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
        pct_5h=_pct(five), reset_5h=_reset(five),
        pct_7d=_pct(seven), reset_7d=_reset(seven),
        at=int(time.time()),
    ), False


def record(reading: Reading, path=None) -> bool:
    """Leave the reading where every existing reader already looks.

    A fresh snapshot the next tick reads like any other, so nothing downstream
    has to learn about this module. It **merges**: a session's `session_id`, `transcript_path` and
    context figures are `collect`'s and are none of our business, and a reading
    that replaced the file wholesale would take the handoff's transcript
    pointer with it.

    `collected_at` is set because the probe this replaced set it by running a
    real session. Without it `AccountStatus.stale` fails the account
    closed before it ever reads the figures, which is the very refusal being
    lifted - `beta` sat at "nothing has run on it for 4h37m" while its 5h
    window was empty.

    The one rule carried over verbatim from the collector this replaced: inside
    one window usage only rises, so a figure is never lowered while its own
    window is still open. Across a rollover it is replaced outright - which is the case
    that matters, because the stale high-water is exactly what made `alpha`
    unassignable eighteen seconds after its window reset.
    """
    p = path or paths.status_file(reading.account)
    if not p.parent.is_dir():
        return False
    try:
        prev = json.loads(p.read_text()) if p.exists() else {}
    except (OSError, json.JSONDecodeError):
        prev = {}
    if not isinstance(prev, dict):
        prev = {}

    snap = dict(prev)
    now = reading.at
    for pct, reset, pk, rk, at_key, carried_key, lost_key in (
            (reading.pct_5h, reading.reset_5h, "pct_5h", "reset_5h",
             "rl_at", "rl_carried", "rl_lost"),
            (reading.pct_7d, reading.reset_7d, "pct_7d", "reset_7d",
             "rl7_at", "rl7_carried", "rl7_lost")):
        value = pct
        if pct is not None:
            old, old_reset = prev.get(pk), prev.get(rk)
            same_window = (isinstance(old_reset, int) and old_reset == reset
                           and old_reset > now - CLOCK_SKEW_S)
            if isinstance(old, int) and same_window and old > pct:
                value = old
        snap[pk] = value
        snap[rk] = reset
        # Observed, now, first-hand: not carried, and not lost.
        snap[at_key] = now
        snap[carried_key] = False
        snap[lost_key] = False
    snap["collected_at"] = now
    # So a reader can tell where a figure came from without guessing at the
    # absence of a session_id.
    snap["rl_source"] = "api"

    tmp = p.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(snap, indent=2, ensure_ascii=False) + "\n")
        tmp.replace(p)
    except OSError:
        return False
    return True
