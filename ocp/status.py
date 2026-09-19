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
rises, so the true number is at least that. The same is true, unmarked, of a
live figure that lost to the high-water mark - the number is real and current,
but it was reported by an earlier payload than this one. Only its age is
understated, which is why it carries no mark of its own. It is old the moment the session
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
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import paths, triggers

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


def _ago(secs: float) -> str:
    """A duration in the largest unit that still says something."""
    secs = int(secs)
    if secs < 120:
        return f"{secs}s"
    if secs < 5400:
        return f"{secs // 60}m"
    if secs < 172800:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


# Above these nothing can run at all. New work stops a reserve short of them,
# at the lines a move trigger fires at (triggers.MOVE_LINE_*).
PCT_5H_BLOCK = 90
PCT_7D_BLOCK = 100

# Clear 5h headroom a move destination needs below the block, so it does not
# bounce straight back. A plain launch has no such requirement.
MOVE_MIN_HEADROOM_PCT = 15


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
    # Which session this reading came from, and where that session's own record
    # lives. Both belong to the session, so read_for_agent carries the agent's
    # own or nothing - never the account file's, which is whichever agent on
    # this account rendered last.
    session_id: str | None = None
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
    def pct_7d_source(self) -> str:
        """Where the weekly figure comes from, on the same terms as the 5h one.

        The two are not trusted the same way and should not be, but they were
        not drawn the same way either: the 5h figure went to "?" the moment its
        snapshot went stale while the weekly figure from that same file was
        drawn as a measurement, unmarked.

        Inside one window weekly usage only rises, so an old reading is a floor
        and a floor is worth having - marked as carried, not as measured. Once
        `reset_7d` has passed it is not even that: it describes a window that no
        longer exists, and reading it as the truth is how an account reads 60%
        for a week after the week ended.
        """
        if not self.exists or self.error or self.rl7_lost or self.pct_7d is None:
            return "unknown"
        now = int(time.time())
        if self.reset_7d and self.reset_7d <= now:
            return "unknown"
        if not self.stale and not self.rl7_carried:
            return "live"
        age = None if self.rl7_at is None else now - self.rl7_at
        return "carried" if age is not None and age <= CARRY_MAX_AGE_S else "carried_old"

    @property
    def trusted_pct_7d(self) -> int | None:
        """The weekly figure we may act on: a floor, or None when there is none."""
        return None if self.pct_7d_source == "unknown" else self.pct_7d

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
            # In the units a reader thinks in. "59213s > 900s" is two numbers
            # nobody subtracts; the line is on a dashboard, and what it has to
            # say is how long ago anything ran on this account.
            shown = "unknown" if self.age_s is None else _ago(self.age_s)
            return False, (f"nothing has run on it for {shown} - the snapshot is "
                           f"stale (fail-closed)")
        pct = self.trusted_pct_5h
        if pct is None:
            return False, "pct_5h is null - usage unknown (fail-closed)"
        if pct >= PCT_5H_BLOCK:
            return False, f"5h usage {pct}% >= {PCT_5H_BLOCK}%"
        week = self.trusted_pct_7d
        if week is not None and week >= PCT_7D_BLOCK:
            return False, f"7d usage {week}% >= {PCT_7D_BLOCK}%"
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

    def takes_work(self, limit_5h: int = triggers.MOVE_LINE_5H,
                   limit_7d: int = triggers.MOVE_LINE_7D) -> tuple[bool, str]:
        """May a session be started here: assignable, or a rolled-over stale
        account, and under the lines a move trigger fires at. The one rule the
        pick, the panel and `ocp account list` all use."""
        ok, why = self.assignable()
        week = self.trusted_pct_7d
        if ok:
            pct = self.trusted_pct_5h
            if pct is not None and pct >= limit_5h:
                return False, f"5h usage {pct}% >= {limit_5h}%"
        elif self.rolled_over()[0]:
            # Its weekly figure is a floor unless the week itself has passed.
            ok, why = True, self.rolled_over()[1]
            week = None if self.reset_7d and self.reset_7d <= time.time() else self.pct_7d
        if ok and week is not None and week >= limit_7d:
            return False, f"7d usage {week}% >= {limit_7d}%"
        return ok, why

    def rolled_over(self) -> tuple[bool, str]:
        """A stale account whose 5h window has passed can take work if its last
        weekly reading had room: the session that starts there writes a fresh
        reading, and the move trigger takes the agent off if it is full.
        Returns (ok, reason)."""
        if (not self.exists or self.error or self.rl7_lost or not self.stale
                or self.age_s is None):
            return False, ""
        now = time.time()
        if self.reset_5h:
            passed = self.reset_5h <= now
        else:
            passed = self.age_s >= 5 * 3600
        if not passed:
            return False, ""
        week = self.pct_7d
        week_over = bool(self.reset_7d and self.reset_7d <= now)
        if week is None and not week_over:
            return False, ""
        if week is not None and week >= PCT_7D_BLOCK and not week_over:
            return False, ""
        shown = "its week has rolled over" if week_over else f"7d {week}%"
        return True, f"stale, window rolled over, {shown}"

    def headroom(self) -> int | None:
        """Remaining 5h headroom in percent, or None when unknown."""
        pct = self.trusted_pct_5h
        if pct is None:
            return None
        return max(0, PCT_5H_BLOCK - pct)

    def headroom_7d(self) -> int | None:
        """Remaining weekly headroom in percent, or None when unknown."""
        week = self.trusted_pct_7d
        if week is None:
            return None
        return max(0, PCT_7D_BLOCK - week)


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
        # No snapshot of its own means no session of its own, so its context is
        # unknown - not whatever the last session on this account happened to
        # have. An agent that has never run was being shown another agent's
        # context, and judged on it.
        acct_status.ctx_pct = None
        acct_status.session_id = None
        acct_status.transcript_path = None
        return acct_status
    own = read(account, agent_file)
    if own.error or own.stale:
        # Its own snapshot is unusable, so these are unknown rather than
        # borrowed from whatever else last wrote the account file. This is not
        # an edge: nothing refreshes an agent's status file while its session is
        # closed, so by wake time its own snapshot is older than STALE_AFTER_S
        # by construction - and the account file then belongs to whichever other
        # agent is live on the account. Carrying its session id through here
        # told a woken agent to grep a stranger's transcript, in a stranger's
        # repository, in a system whose whole premise is per-agent isolation.
        acct_status.ctx_pct = None
        acct_status.session_id = None
        acct_status.transcript_path = None
        return acct_status
    acct_status.ctx_pct = own.ctx_pct
    acct_status.session_id = own.session_id
    acct_status.transcript_path = own.transcript_path
    return acct_status


def reported_account(agent: str) -> str | None:
    """Which account this agent's own session last rendered its status line
    under, or None when there is nothing fresh enough to say.

    Every render writes its own `account` under its own env (`live_cost`
    already trusts this same field, the same way, to decide whose cost a
    snapshot counts towards) - so a fresh one is a session reporting where it
    actually is, not a preference. Stale is refused on the same terms
    `read_for_agent` already refuses one for context and session id: nothing
    rewrites this file once a session closes, so an old snapshot belongs to
    whichever account happened to be current when it stopped, not to now.
    """
    f = paths.agent_status_file(agent)
    if not f.exists():
        return None
    st = read("", f)
    if st.error or st.stale:
        return None
    acct = st.raw.get("account")
    return acct if isinstance(acct, str) and acct else None


def live_cost(account: str) -> float | None:
    """What the sessions we can currently see have spent on this account.

    `cost.total_cost_usd` belongs to one session, and the account file is
    written by whichever session rendered last - so reading it from there makes
    the account's cost flip between sessions every second, which is what it was
    doing. Summing the live per-agent snapshots is a number that is at least
    about the account.

    It is not the account's spend: a session that has ended takes its cost out
    of the sum, a live one whose status line is lagging drops out of it until
    the snapshot catches up, and work done outside ocp was never in it.
    """
    total, seen = 0.0, False
    for agent in paths.list_agents():
        f = paths.agent_status_file(agent)
        if not f.exists():
            continue
        st = read(account, f)
        if st.error or st.stale or st.raw.get("account") != account:
            continue
        if st.cost_usd is not None:
            total += st.cost_usd
            seen = True
    return total if seen else None


# Between two accounts whose deadline and headroom tie, prefer the one whose
# figure we actually saw. An inferred 0% is sound and still a weaker claim
# than a reported 0%.
_CONFIDENCE = {"live": 3, "carried": 2, "quiet": 1}


# A burn reading is the daemon's, taken from agents running on the account; one
# older than this describes a pace nobody is keeping any more.
BURN_MAX_AGE_S = 600


def _burn_path(acct: str) -> Path:
    return paths.accounts_dir() / acct / "burn.json"


def write_burn(acct: str, burn: float, at: int) -> None:
    """Record the burn rate the daemon measured (percent per second), so the
    screens show the number the triggers act on instead of re-deriving one.
    `at` is when the rate was measured (the sample's own time), not when this
    is written."""
    path = _burn_path(acct)
    if not path.parent.is_dir():
        return
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"burn_per_s": burn, "at": at}))
    os.replace(tmp, path)


def read_burn(acct: str, now: float | None = None) -> float | None:
    """The recorded burn in percent per second, or None when there is none or
    it is too old to describe the present."""
    try:
        data = json.loads(_burn_path(acct).read_text())
        at, burn = int(data["at"]), float(data["burn_per_s"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return burn if (now or time.time()) - at <= BURN_MAX_AGE_S else None


def _hm(secs: float) -> str:
    secs = int(secs)
    return f"{secs // 3600}h{(secs % 3600) // 60:02d}" if secs >= 3600 else f"{max(1, secs // 60)}m"


@dataclass
class Runway:
    left_5h: int              # points of 5h usage left before the move line
    left_7d: int | None
    eta_s: float | None       # seconds to the move line at the current burn
    burning: bool             # False: no burn reading, or an idle account
    past_reset: bool          # the 5h window rolls over before the line is reached


def runway(st: "AccountStatus", burn: float | None, now: float | None = None) -> Runway | None:
    """An estimate of how long this account's 5h headroom lasts at the current
    burn. None when its reading is stale or unknown: no figure is better than
    one drawn from a reading that stopped describing the account."""
    pct = st.trusted_pct_5h
    if pct is None:
        return None
    now = now or time.time()
    week = st.trusted_pct_7d
    left = max(0, triggers.MOVE_LINE_5H - pct)
    left7 = None if week is None else max(0, triggers.MOVE_LINE_7D - week)
    if not burn or burn <= 0:
        return Runway(left, left7, None, False, False)
    eta = left / burn
    return Runway(left, left7, eta, True,
                  bool(st.reset_5h and now + eta > st.reset_5h))


def describe_runway(r: Runway | None) -> str:
    if r is None:
        return ""
    line = triggers.MOVE_LINE_5H
    if r.left_5h == 0:
        return f"past {line}%"
    if not r.burning:
        return "no recent burn"
    return "lasts past reset" if r.past_reset else f"~{_hm(r.eta_s)} to {line}%"


def team_outlook(names: list[str] | None = None, now: float | None = None) -> str:
    """One line on whether the team's combined 5h headroom lasts to the next
    reset at the pace it is burning now. Only accounts with a current reading
    count; blank when none has one."""
    now = now or time.time()
    counted = []
    for n in (names if names is not None else paths.list_accounts()):
        st = read(n)
        burn = read_burn(n, now)
        r = runway(st, burn, now)
        if r is not None:
            counted.append((st, r, burn or 0.0))
    if not counted:
        return ""
    room = sum(r.left_5h for _, r, _ in counted)
    pace = sum(b for _, _, b in counted)
    resets = [st.reset_5h for st, _, _ in counted if st.reset_5h and st.reset_5h > now]
    nxt = min(resets) if resets else None
    clock = lambda t: time.strftime("%H:%M", time.localtime(t))  # noqa: E731
    n = f"{len(counted)} account{'s' if len(counted) != 1 else ''}"
    if pace <= 0:
        return f"estimate: {room} points of 5h headroom across {n}, no recent burn"
    stops = now + room / pace
    if nxt and stops >= nxt:
        return f"estimate: at this pace {n} last past the next reset ({clock(nxt)})"
    tail = f"; next reset {clock(nxt)}" if nxt else ""
    return f"estimate: at this pace {n} hit the move line around {clock(stops)}{tail}"


def deadline_7d(st: "AccountStatus") -> float:
    """When this account's weekly headroom is lost if it goes unspent.

    Infinity, not now, for an account whose reset_7d we do not know or that is
    already past (that window rolled over, so its headroom just refreshed): it
    is not more urgent than one we can see approaching its own reset, and it
    still has to sort somewhere among accounts tied on urgency.
    """
    return float(st.reset_7d) if st.reset_7d and st.reset_7d > time.time() else float("inf")


def _pick_reason(st: "AccountStatus", picked: bool) -> str:
    """Why this account sits where it does in the ordering."""
    week = st.trusted_pct_7d
    week_s = "unknown" if week is None else f"{week}%"
    rolled = st.rolled_over()
    if rolled[0]:
        return rolled[1] + (" <- picked: earliest deadline" if picked else "")
    when = "unknown" if not st.reset_7d else _ago(max(0.0, st.reset_7d - time.time()))
    base = f"5h {st.trusted_pct_5h}%, 7d {week_s} (resets in {when})"
    return base + " <- picked: earliest deadline" if picked else base


def pick_account(candidates: list[str] | None = None,
                 limit_5h: int = triggers.MOVE_LINE_5H,
                 limit_7d: int = triggers.MOVE_LINE_7D,
                 bootstrap: bool = False) -> tuple[str | None, list[tuple[str, str]]]:
    """Pick the account whose weekly headroom expires soonest, among those that
    can take the work.

    Returns (choice, [(account, reason)]). When none can, the choice is None
    and the caller must refuse - that is the fail-closed path.

    Accounts that fail `assignable`, or read at or over `limit_5h` or
    `limit_7d` (by default the lines a move trigger fires at), are dropped
    before any ranking: an agent started on one would be moved straight off.
    The deadline order then only compares accounts that would last: reset_7d
    soonest first; 5h headroom breaks ties and unknowns.

    `bootstrap=True`: a logged-in account with no status.json at all - never
    run, so nothing else could ever give it one - is pickable as a last
    resort, in list_accounts order, once nothing with an actual reading
    qualifies. Every other refusal (stale, at the wall, unreadable) still
    refuses; a stale account that has run once and gone quiet is not
    "unread", and needs its own explicit preference to be picked again.
    """
    names = candidates if candidates is not None else paths.list_accounts()
    reasons: list[tuple[str, str]] = []
    usable: list[tuple[str, AccountStatus]] = []
    blank: list[str] = []
    for n in names:
        if paths.removing(n):
            reasons.append((n, "being removed"))
            continue
        st = read(n)
        allowed, why = st.takes_work(limit_5h, limit_7d)
        if allowed:
            usable.append((n, st))
        elif bootstrap and not st.exists:
            blank.append(n)
            reasons.append((n, "no reading yet - a session has never run on it "
                              "(bootstrap candidate, used only if nothing else qualifies)"))
        else:
            reasons.append((n, why))
    if not usable:
        return (blank[0] if blank else None), reasons

    def sort_key(item: tuple[str, AccountStatus]):
        _, st = item
        return (deadline_7d(st), -(st.headroom() or 0),
                -_CONFIDENCE.get(st.pct_5h_source, 0))

    usable.sort(key=sort_key)
    picked = usable[0][0]
    for n, st in usable:
        reasons.append((n, _pick_reason(st, picked=(n == picked))))
    order = {n: i for i, n in enumerate(names)}
    reasons.sort(key=lambda t: order[t[0]])
    return picked, reasons
