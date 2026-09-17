"""Account status and assignability (design 01-infra.md section 4).

The governing rule is fail-closed. From section 4:

  "If pct_5h is null that is not a full tank, it is a refusal to assign,
   plus a red light."

Reading a missing value as 0 makes every account look perfectly fresh, which
makes the 90% trigger false forever: the feature is dead while the dashboard
stays green. So anything we do not know is treated as a reason to refuse.

Not knowing is not the same as nothing having happened, though, and conflating
the two cost us five needless handoffs. Claude Code reports a rate-limit window
from what the session itself has learned, so a session that has made no request
since the window rolled over sends no five_hour block at all. That absence says
the account is quiet, not that the collector is dead - and the two have
opposite right answers. This module names four cases instead of two:

    live      this payload reported the window
    carried   an earlier reading, still inside the window it describes
    quiet     a live collector, and nobody has spent in this window -> 0%
    unknown   no snapshot, unreadable, or too old to trust -> refuse

Only `unknown` is fail-closed. Treating `quiet` as unknown parks idle agents,
which costs a fold each and buys nothing.
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
    session_id: str | None = None
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
    def pct_5h_source(self) -> str:
        """Which of the four cases this reading is: see the module docstring."""
        if not self.exists or self.error or self.stale or not self.pct_5h_readable:
            return "unknown"
        if self.pct_5h is None:
            return "quiet"
        return "carried" if self.rl_carried else "live"

    @property
    def pct_5h_inferred(self) -> bool:
        return self.pct_5h_source == "quiet"

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
        if self.pct_5h_source == "quiet":
            return True, "5h window quiet - nothing spent in it (inferred 0%)"
        if self.pct_5h_source == "carried":
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
    st.session_id = raw.get("session_id")
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
    confidence = {"live": 2, "carried": 1, "quiet": 0}
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
