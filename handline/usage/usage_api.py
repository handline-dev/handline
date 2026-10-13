"""Ask an account where it stands without spending any of it.

Rate limits ride on API responses, so an account nobody is using cannot say
where it stands, and the fail-closed rule then refuses to start the one session
that would have told us. ocp used to answer that with `probe.py`: a pty, a
session boot and one Haiku turn, up to 90 seconds, spending the very quota it
was asking about. That module is deleted (#1447); this is what replaced it.

How one vendor's endpoint is asked is that vendor's Meter (`Driver.meter()`;
claude's is `vendors/claude_meter.py`). This module asks the account's own
driver, and keeps what is the same for every vendor: the reading's shape, and
`record`, which leaves it where every existing reader already looks.
"""
from __future__ import annotations

import json

from ..infra import atomic, paths
from . import windows
from .. import vendors
from ..vendors.base import Reading

# How far a `resets_at` may be in the past before the window counts as ended.
# `resets_at` is the server's and the comparison is against a local clock, so
# the slack is for ordinary skew, not for a judgement call.
CLOCK_SKEW_S = 60



def read(acct: str, refresh: bool = True) -> Reading | None:
    """Where this account stands now, or None when it could not be asked.

    None is "could not look", never "nothing has been spent" - the same
    distinction `term.live_terminals` is careful about, and the reason this
    returns an object only when the endpoint actually answered.

    `refresh=False` skips the renewal, for a caller that already knows the
    token is live - an account with a session running on it, say, where Claude
    Code is renewing it anyway and two writers would race. (It means nothing
    to a vendor whose credential does not expire; it is simply ignored.)

    Each vendor is asked at its own endpoint with its own credential, read in
    memory for the one request by the driver's meter path (AGENTS.md rule 1 as
    amended, #2010). A vendor with no meter path is not asked.
    """
    return read_detailed(acct, refresh)[0]


def read_detailed(acct: str, refresh: bool = True) -> tuple[Reading | None, bool]:
    """`read`, plus whether a failure was the credential itself being refused.

    The second element is only ever True for the claude vendor, and only for
    a 401/403 or a Keychain item that is genuinely gone - the one class of
    failure a caller like `recover.recover` needs to tell apart from a
    timeout, a DNS blip, a Keychain read that merely failed, or a 429/5xx
    right after a real turn, none of which say the credential is dead.
    """
    try:
        meter = vendors.for_account(acct).meter()
    except vendors.UnknownVendor:
        return None, False
    if meter is None:
        return None, False
    return meter.read_usage(acct, refresh)


def describe(reading: Reading) -> str:
    """The reading as one line for a log: each window by length, shortest first."""
    return " ".join(f"{windows.label(w.minutes)} {w.pct}%"
                    for w in sorted(reading.windows, key=lambda w: w.minutes))


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

    A first-hand reading is the authority for its window: it replaces the held
    figure even when lower with the same reset, since the vendor can reset a
    limit early and keep reporting the old reset time. Only a figure observed
    after this reading is kept, so a late reading cannot undo a newer one.
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
    old = {e["minutes"]: e for e in windows.entries(prev)}
    out = []
    for w in sorted(reading.windows, key=lambda w: w.minutes):
        value = w.pct
        if w.pct is not None:
            before = old.get(w.minutes, {})
            old_pct, old_reset = before.get("used_percent"), before.get("resets_at")
            old_at = before.get("observed_at")
            same_window = (isinstance(old_reset, int) and old_reset == w.reset
                           and old_reset > now - CLOCK_SKEW_S)
            newer = isinstance(old_at, int) and old_at > now
            if isinstance(old_pct, int) and same_window and newer and old_pct > w.pct:
                value = old_pct
        # Observed, now, first-hand: not carried, and not lost.
        out.append(windows.entry(w.minutes, value, w.reset, now))
    snap["windows"] = out
    snap["collected_at"] = now
    # So a reader can tell where a figure came from without guessing at the
    # absence of a session_id.
    snap["rl_source"] = "api"

    try:
        atomic.write_text(p, json.dumps(snap, indent=2, ensure_ascii=False) + "\n")
    except OSError:
        return False
    return True
