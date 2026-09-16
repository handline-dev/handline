"""Account status and assignability (design 01-infra.md section 4).

The governing rule is fail-closed. From section 4:

  "If pct_5h is null that is not a full tank, it is a refusal to assign,
   plus a red light."

Reading a missing value as 0 makes every account look perfectly fresh, which
makes the 90% trigger false forever: the feature is dead while the dashboard
stays green. So anything we do not know is treated as a reason to refuse.
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

# Above these, stop handing out new work.
PCT_5H_BLOCK = 90
PCT_7D_BLOCK = 95


@dataclass
class AccountStatus:
    account: str
    exists: bool = False
    raw: dict = field(default_factory=dict)
    error: str | None = None

    # Values used for decisions. None when unknown - never coerced to 0.
    pct_5h: int | None = None
    pct_7d: int | None = None
    ctx_pct: int | None = None
    reset_5h: int | None = None
    reset_7d: int | None = None
    cost_usd: float | None = None
    session_id: str | None = None
    model_id: str | None = None
    collected_at: int | None = None

    @property
    def age_s(self) -> float | None:
        if self.collected_at is None:
            return None
        return max(0.0, time.time() - self.collected_at)

    @property
    def stale(self) -> bool:
        age = self.age_s
        return age is None or age > STALE_AFTER_S

    def assignable(self) -> tuple[bool, str]:
        """May we give this account new work? Returns (allowed, reason)."""
        if not self.exists:
            return False, "no status.json - no session has run yet (fail-closed)"
        if self.error:
            return False, f"status.json unreadable: {self.error} (fail-closed)"
        if self.pct_5h is None:
            return False, "pct_5h is null - usage unknown (fail-closed)"
        if self.stale:
            age = self.age_s
            shown = "unknown" if age is None else f"{int(age)}s"
            return False, f"snapshot is stale ({shown} > {STALE_AFTER_S}s) (fail-closed)"
        if self.pct_5h >= PCT_5H_BLOCK:
            return False, f"5h usage {self.pct_5h}% >= {PCT_5H_BLOCK}%"
        if self.pct_7d is not None and self.pct_7d >= PCT_7D_BLOCK:
            return False, f"7d usage {self.pct_7d}% >= {PCT_7D_BLOCK}%"
        return True, f"5h {self.pct_5h}%"

    def headroom(self) -> int | None:
        """Remaining 5h headroom in percent. None when unknown - not 0, not 100."""
        if self.pct_5h is None:
            return None
        return max(0, PCT_5H_BLOCK - self.pct_5h)


def _as_pct(v):
    """Coerce a usage percentage to an int, rounding UP.

    The API returns these as floats, and not always clean ones: an observed
    sample was 28.999999999999996 for what is plainly 29. Truncating would
    report 28, and would turn 89.6 into 89, which delays the hard stop. Every
    rounding error here should push toward acting sooner, not later, so this
    rounds up. Timestamps and counts use _as_int instead.
    """
    if v is None or isinstance(v, bool):
        return None
    if not isinstance(v, (int, float)):
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
    st.pct_7d = _as_pct(raw.get("pct_7d"))
    st.ctx_pct = _as_pct(raw.get("ctx_pct"))
    st.reset_5h = _as_int(raw.get("reset_5h"))
    st.reset_7d = _as_int(raw.get("reset_7d"))
    st.collected_at = _as_int(raw.get("collected_at"))
    st.session_id = raw.get("session_id")
    st.model_id = raw.get("model_id")
    cost = raw.get("cost_usd")
    st.cost_usd = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None
    return st


def read_all() -> list[AccountStatus]:
    return [read(a) for a in paths.list_accounts()]


def pick_account(candidates: list[str] | None = None) -> tuple[str | None, list[tuple[str, str]]]:
    """Pick the assignable account with the most headroom.

    Returns (choice, [(account, reason)]). When nothing is assignable the
    choice is None and the caller must refuse - that is the fail-closed path.
    """
    names = candidates if candidates is not None else paths.list_accounts()
    reasons, usable = [], []
    for n in names:
        st = read(n)
        allowed, why = st.assignable()
        reasons.append((n, why))
        if allowed:
            usable.append((st.headroom() or 0, n))
    if not usable:
        return None, reasons
    usable.sort(reverse=True)
    return usable[0][1], reasons
