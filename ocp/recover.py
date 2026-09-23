"""#1885: a stale or dead account earns itself back with one real turn.

Measured on omega (#2148): `usage_api.refresh_credential` is `claude auth
status`, and that can answer `loggedIn: true` without renewing a token that
has already expired - the CLI's own judgement of "near enough to renewal" is
not this endpoint's. Every read after that keeps hitting the same 401
forever, because nothing here has ever made Claude Code actually renew it.
Done by hand on theta in #1885 (401 became 200): one real turn does, because
that is the one thing that makes Claude Code renew the token it is about to
spend. `fold.run_model` already is that one turn, on the account's own
credential, spent once (KICKOFF rule 4) - reused here rather than opening a
second way to do the same spend.

This module is the mechanism only: what one recovery attempt does, and the
two things that must stop it (the weekly wall, the account's own switch). Ocp
has to notice a call is worth making, and that is `daemon.py` (a poll whose
read still fails after a renewal, or whose 5h window just ended) - a decision
that belongs where the poll already runs, and is not made here.
"""
from __future__ import annotations

from pathlib import Path

from . import accounts, fold, load, paths, status, usage_api, vendors
from .vendors import claude as claudemod

RECOVER_MODEL = "haiku"
# One line, no content anything downstream would have to think about - the
# turn exists to be spent, not to be read.
RECOVER_PROMPT = "hi"


def _relogin_file(acct: str) -> Path:
    return paths.account_dir(acct) / "needs_relogin"


def needs_relogin(acct: str) -> bool:
    """Set once a real turn still left the credential refused (401/403) -
    the one case nothing left to try would ever clear, so `recover` itself
    keeps refusing rather than retrying forever. Fail-closed (status.py,
    `takes_work`) does not read this; the account stays refused there the
    same way any stale account is, by the ordinary staleness rule."""
    return _relogin_file(acct).exists()


def mark_relogin(acct: str, why: str) -> None:
    p = _relogin_file(acct)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(why + "\n")


def clear_relogin(acct: str) -> None:
    _relogin_file(acct).unlink(missing_ok=True)


def recover(acct: str) -> tuple[bool, str]:
    """Spend one real turn on `acct` and re-read it. (ok, message).

    Refuses on its own before spending anything: an account already marked
    needing re-login (nothing here would clear that without a human), one
    #1885 has been turned off for, one no vendor here reads usage for, one not
    logged in, and one at its weekly wall - spending a turn there is the exact
    thing the wall exists to stop, whatever state its 5h window is in.
    """
    if needs_relogin(acct):
        return False, f"{acct} needs re-login - not retrying"
    if not load.usage_recover_enabled(acct):
        return False, f"{acct}: usage recovery is turned off"
    if vendors.account_vendor(acct) != claudemod.Claude.name:
        return False, f"{acct} is not a claude account"
    if not accounts.logged_in(acct):
        return False, f"{acct} is not logged in"
    # trusted_pct_7d carries a stale reading as a floor until its own reset
    # passes (status.py), so this still catches an account genuinely at the
    # wall even though this whole module exists for the case where the
    # reading is stale. When the figure is truly unknown (never read, or its
    # window has already rolled over) trusted_pct_7d is None and a turn is
    # spent - the account is exactly the one #1885 exists to unstick.
    week = status.read(acct).trusted_pct_7d
    if week is not None and week >= status.PCT_7D_BLOCK:
        return False, f"{acct}: 7d {week}% is at the wall - not spending a turn on it"

    ok, out = fold.run_model(RECOVER_PROMPT, acct, RECOVER_MODEL)
    if not ok:
        return False, f"{acct}: recovery turn failed: {out}"

    reading, auth_failed = usage_api.read_detailed(acct, refresh=False)
    if reading is None:
        if auth_failed:
            mark_relogin(acct, "a real turn ran but the credential was still refused (401/403)")
            return False, f"{acct}: still unreadable after a real turn - marked needs re-login"
        return False, f"{acct}: still unreadable after a real turn - not marked, may be transient"
    clear_relogin(acct)
    usage_api.record(reading)
    return True, f"{acct}: recovered - 5h {reading.pct_5h}% 7d {reading.pct_7d}%"
