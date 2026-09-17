"""Account status and assignability (design 01-infra.md section 4).

The governing rule is fail-closed. Section 4 puts it as: a pct_5h we cannot
trust is not a full tank, it is a refusal to assign, plus a red light.

Reading a missing value as 0 makes every account look perfectly fresh, which
makes the 90% trigger false forever: the feature is dead while the dashboard
stays green. So anything we do not know is treated as a reason to refuse.

Not knowing is not the same as nothing having happened, though, and conflating
the two cost us five needless handoffs. Claude Code reports a rate-limit window
from what the session itself has learned, so a session that has made no request
since the window rolled over sends no five_hour block at all. That absence says
the account is quiet, not that the collector is dead - and the two have
opposite right answers. This module names five cases instead of two:

    live         this payload reported the window
    carried      an earlier reading, still inside its window and recent enough
    carried_old  the same, but observed longer ago than we will assign on
    quiet        a live collector, and nobody has spent in this window -> 0%
    unknown      no snapshot, unreadable, too old, or a reading we lost

`quiet` and `unknown` are the pair that matters: treating quiet as unknown
parks idle agents, which costs a fold each and buys nothing, while treating
unknown as quiet hands out work on an account that may be at the wall.

A carried figure is a floor, not a measurement: usage inside a window only
rises, so the true number is at least that. It is old the moment the session
that reported it goes idle, and collected_at cannot say so - an idle session
keeps refreshing collected_at while reporting nothing new. rl_at is when the
figure was actually observed, and it, not collected_at, bounds the carry.
Past that bound we still use the floor for triggers, because an idle session
cannot be racing toward the wall, but we refuse to assign new work on it.

The quiet inference has one known exception, and it is ours: `ocp fold` runs
`claude -p`, which is headless and has no statusLine, so a fold spends quota
that no collector will ever see. It is one small call, but it means quiet
reads "no session reported spending", not "nothing was spent".
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import paths

# statusLine only refreshes while a session is alive, so a snapshot older than
# this is not trusted as "current" (section 4 constraint - refreshInterval
# helps but cannot cover a session that has exited).
STALE_AFTER_S = 900

# Small tolerance for ordinary clock jitter before a future timestamp is
# treated as unreadable rather than fresh.
CLOCK_SKEW_GRACE_S = 60

# How long a carried figure may stand in for a measurement. Same bound as a
# stale snapshot, for the same reason: past it we do not know what has happened.
CARRY_MAX_AGE_S = STALE_AFTER_S

# How a figure's provenance is drawn, in one place: four screens used to spell
# this out separately, and one of them was left behind.
SOURCE_MARK = {"live": " ", "carried": "~", "carried_old": "!", "quiet": "*", "unknown": " "}
SOURCE_LEGEND = ("* inferred: nothing spent in this window · ~ carried: an earlier "
                 "reading · ! carried past its bound, no new work")
# For a header that has to survive an 80-column terminal, where the long form
# truncates mid-word and loses ~ and ! entirely.
SOURCE_LEGEND_SHORT = "* inferred · ~ carried · ! too old"


def mark(src: str) -> str:
    return SOURCE_MARK.get(src, " ")


# Above these, stop handing out new work.
PCT_5H_BLOCK = 90
PCT_7D_BLOCK = 95


@dataclass
class AccountStatus:
    account: str
    exists: bool = False
    raw: dict = field(default_factory=dict)
    error: str | None = None

    # None when unknown - never coerced to 0.
    pct_5h: int | None = None
    pct_7d: int | None = None
    ctx_pct: int | None = None
    reset_5h: int | None = None
    reset_7d: int | None = None
    cost_usd: float | None = None
    # When pct_5h was actually observed, and whether it came from an earlier
    # payload than this one. collected_at only says the collector is alive.
    rl_at: int | None = None
    rl_carried: bool = False
    # False when the field is missing entirely, or present but unreadable (NaN,
    # a string, a bool). An explicit null is readable: it is how the collector
    # writes "this payload reported no such window".
    pct_5h_readable: bool = True
    # True when the collector had a figure it could not carry because it could
    # not tell whether that figure's window had ended. That is ignorance, and
    # it must not read as a quiet window.
    rl_lost: bool = False
    # The same three for the weekly window. It has no age bound - a week-wide
    # window barely moves in the minutes a 5h figure goes stale in - but a 7d
    # figure we lost still has to refuse, because the 95% gate is written as
    # "block if we have a figure and it is high", so a missing one is not a
    # refusal, it is the absence of one.
    rl7_at: int | None = None
    rl7_carried: bool = False
    rl7_lost: bool = False
    session_id: str | None = None
    # Where this session's own record lives. It belongs to the session, so
    # read_for_agent carries the agent's, never the account file's.
    transcript_path: str | None = None
    model_id: str | None = None
    collected_at: int | None = None

    @property
    def age_s(self) -> float | None:
        """Age of the snapshot, or None when it cannot be trusted.

        A timestamp in the future means a skewed clock or wrong units (a
        milliseconds value is the likely bug). Clamping that to age 0 made it
        look permanently fresh, which is fail-open. Unknown is the right answer.
        """
        if self.collected_at is None:
            return None
        age = time.time() - self.collected_at
        if age < -CLOCK_SKEW_GRACE_S:
            return None
        return max(0.0, age)

    @property
    def stale(self) -> bool:
        age = self.age_s
        return age is None or age > STALE_AFTER_S

    @property
    def rl_age_s(self) -> float | None:
        """How long ago the 5h figure was observed, not when it was copied."""
        if self.rl_at is None:
            return None
        age = time.time() - self.rl_at
        if age < -CLOCK_SKEW_GRACE_S:
            return None
        return max(0.0, age)

    @property
    def pct_5h_source(self) -> str:
        """Which of the five cases this reading is: see the module docstring."""
        if not self.exists or self.error or self.stale or not self.pct_5h_readable:
            return "unknown"
        if self.rl_lost:
            return "unknown"
        if self.pct_5h is None:
            return "quiet"
        if not self.rl_carried:
            return "live"
        age = self.rl_age_s
        return "carried" if age is not None and age <= CARRY_MAX_AGE_S else "carried_old"

    @property
    def trusted_pct_5h(self) -> int | None:
        """What we may act on, or None when we genuinely do not know.

        A frozen status.json (collector died, jq removed, session exited) keeps
        reporting its last value forever. Reading that as the truth is the same
        failure as reading a null as 0, so a stale snapshot reads as unknown.

        A quiet window reads as 0 instead, because a live collector reporting no
        five_hour block is a positive observation: no session on this account
        has spent inside the current window. The hole in that - usage from
        outside ocp, on a config dir we do not collect from - is the same hole
        every other reading has.
        """
        src = self.pct_5h_source
        if src == "unknown":
            return None
        return 0 if src == "quiet" else self.pct_5h

    @property
    def trusted_ctx_pct(self) -> int | None:
        return None if self.stale else self.ctx_pct

    def assignable(self) -> tuple[bool, str]:
        """May we give this account new work? Returns (allowed, reason)."""
        if not self.exists:
            return False, "no status.json - no session has run yet (fail-closed)"
        if self.error:
            return False, f"status.json unreadable: {self.error} (fail-closed)"
        if self.stale:
            age = self.age_s
            shown = "unknown" if age is None else f"{int(age)}s"
            return False, f"snapshot is stale ({shown} > {STALE_AFTER_S}s) (fail-closed)"
        pct = self.trusted_pct_5h
        if pct is None:
            return False, "pct_5h is null - usage unknown (fail-closed)"
        if pct >= PCT_5H_BLOCK:
            return False, f"5h usage {pct}% >= {PCT_5H_BLOCK}%"
        if self.pct_7d is not None and self.pct_7d >= PCT_7D_BLOCK:
            return False, f"7d usage {self.pct_7d}% >= {PCT_7D_BLOCK}%"
        if self.rl7_lost:
            return False, "7d usage was lost - the weekly gate cannot be checked (fail-closed)"
        src = self.pct_5h_source
        if src == "carried_old":
            age = self.rl_age_s
            shown = "unknown" if age is None else f"{int(age / 60)}m"
            return False, (f"5h {pct}% was observed {shown} ago and only ever rises "
                           f"(carried past {CARRY_MAX_AGE_S}s)")
        if src == "quiet":
            return True, "5h window quiet - nothing spent in it (inferred 0%)"
        if src == "carried":
            return True, f"5h {pct}% (carried, still inside the window)"
        return True, f"5h {pct}%"

    def headroom(self) -> int | None:
        """Remaining 5h headroom in percent, or None when unknown."""
        pct = self.trusted_pct_5h
        if pct is None:
            return None
        return max(0, PCT_5H_BLOCK - pct)


def _as_pct(v):
    """Coerce a usage percentage to an int, rounding UP.

    The API returns these as floats, and not always clean ones: an observed
    sample was 28.999999999999996 for what is plainly 29. Truncating would
    report 28, and would turn 89.6 into 89, delaying the hard stop. Every
    rounding error here should push toward acting sooner, so this rounds up.
    """
    if v is None or isinstance(v, bool):
        return None
    if not isinstance(v, (int, float)):
        return None
    # json.loads accepts bare NaN/Infinity. math.ceil(nan) raises, which would
    # take the daemon down; unknown is the fail-closed answer, not a crash.
    if not math.isfinite(v):
        return None
    return int(math.ceil(v))


def _as_int(v):
    if v is None or isinstance(v, bool):
        return None
    return int(v) if isinstance(v, (int, float)) else None


def read(acct: str, path: Path | None = None) -> AccountStatus:
    p = path or paths.status_file(acct)
    st = AccountStatus(account=acct)
    if not p.exists():
        return st
    st.exists = True
    try:
        raw = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as e:
        st.error = str(e)
        return st
    if not isinstance(raw, dict):
        st.error = "top level is not an object"
        return st
    st.raw = raw
    st.pct_5h = _as_pct(raw.get("pct_5h"))
    # An explicit null means "no window in this payload", which is a reading.
    # A missing key or an unreadable value means the snapshot cannot be
    # believed at all, and that is the fail-closed case.
    st.pct_5h_readable = "pct_5h" in raw and (raw["pct_5h"] is None or st.pct_5h is not None)
    st.pct_7d = _as_pct(raw.get("pct_7d"))
    st.ctx_pct = _as_pct(raw.get("ctx_pct"))
    st.reset_5h = _as_int(raw.get("reset_5h"))
    st.reset_7d = _as_int(raw.get("reset_7d"))
    st.collected_at = _as_int(raw.get("collected_at"))
    st.rl_at = _as_int(raw.get("rl_at"))
    st.rl_carried = raw.get("rl_carried") is True
    st.rl_lost = raw.get("rl_lost") is True
    st.rl7_at = _as_int(raw.get("rl7_at"))
    st.rl7_carried = raw.get("rl7_carried") is True
    st.rl7_lost = raw.get("rl7_lost") is True
    st.session_id = raw.get("session_id")
    st.transcript_path = raw.get("transcript_path")
    st.model_id = raw.get("model_id")
    cost = raw.get("cost_usd")
    st.cost_usd = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None
    return st


def read_for_agent(agent: str, account: str) -> AccountStatus:
    """Usage as it applies to one agent.

    Rate limits come from the account, because that is what they belong to.
    Context comes from the agent's own session file when it has one, so two
    agents sharing an account are not judged by each other's context.
    """
    acct_status = read(account)
    agent_file = paths.agent_status_file(agent)
    if not agent_file.exists():
        return acct_status
    own = read(account, agent_file)
    if own.error or own.stale:
        # Its own snapshot is unusable, so context is unknown rather than
        # borrowed from whatever else last wrote the account file.
        acct_status.ctx_pct = None
        return acct_status
    acct_status.ctx_pct = own.ctx_pct
    acct_status.session_id = own.session_id
    acct_status.transcript_path = own.transcript_path
    return acct_status


def read_all() -> list[AccountStatus]:
    return [read(a) for a in paths.list_accounts()]


def pick_account(candidates: list[str] | None = None) -> tuple[str | None, list[tuple[str, str]]]:
    """Pick the assignable account with the most headroom.

    Returns (choice, [(account, reason)]). When nothing is assignable the
    choice is None and the caller must refuse - that is the fail-closed path.
    """
    names = candidates if candidates is not None else paths.list_accounts()
    reasons, usable = [], []
    # Headroom decides, but between two accounts with the same headroom take the
    # one whose figure we actually saw. An inferred 0% is sound and still a
    # weaker claim than a reported 0%.
    confidence = {"live": 3, "carried": 2, "quiet": 1}
    for n in names:
        st = read(n)
        allowed, why = st.assignable()
        reasons.append((n, why))
        if allowed:
            usable.append((st.headroom() or 0, confidence.get(st.pct_5h_source, 0), n))
    if not usable:
        return None, reasons
    usable.sort(reverse=True)
    return usable[0][2], reasons
