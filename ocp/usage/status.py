"""Account status and assignability (design 01-infra.md section 4).

The governing rule is fail-closed. Section 4 puts it as: a shortest-window
figure we cannot trust is not a full tank, it is a refusal to assign, plus a
red light.

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
    unknown      no snapshot, unreadable, a reading we lost, or too old to trust

`quiet` and `unknown` are the pair that matters: treating quiet as unknown
parks idle agents, which buys nothing, while treating
unknown as quiet hands out work on an account that may be at the wall.

A snapshot too old to trust as a live figure is not automatically unknown,
though: while its window has not rolled over, the last reading is a floor -
a lower bound on usage, never an upper one (`idle_floor`). There is an
unbounded invisible spender even on an idle account - a human running
`claude -p` by hand, or anything else no collector sees - so the floor is
judged against a line clear of the wall, not the wall itself, to cover spend
we cannot see. Once the window rolls over the floor stops holding at all,
because what happened since is unaccounted for; only asking the endpoint
answers for it then (`usage_api`).

A carried figure is a floor, not a measurement: usage inside a window only
rises, so the true number is at least that. The same is true, unmarked, of a
live figure that lost to the high-water mark - the number is real and current,
but it was reported by an earlier payload than this one. Only its age is
understated, which is why it carries no mark of its own. It is old the moment the session
that reported it goes idle, and collected_at cannot say so - an idle session
keeps refreshing collected_at while reporting nothing new. the window's `at` is when the
figure was actually observed, and it, not collected_at, bounds the carry.
Past that bound we still use the floor for triggers, because an idle session
cannot be racing toward the wall, but we refuse to assign new work on it.

The quiet inference has one known exception, and it is ours: `ocp fold` runs
its one model call headless, with no statusLine, so a fold spends quota that
no collector will ever see. It is one small call, but it means quiet reads
"no session reported spending", not "nothing was spent".
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

from ..team import accounts, agents
from . import collect, cycle, usage_api, windows
from ..infra import atomic, paths, timefmt
from ..session import triggers

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
# Not one of the five cases: a figure nothing may act on as a measurement, but
# the last thing anyone saw on an account that has since gone quiet. A column
# drawn empty there says "we know nothing", and 96% is not nothing.
IDLE_MARK = "?"
SOURCE_LEGEND = ("* inferred: nothing spent in this window · ~ carried: an earlier "
                 "reading · ! carried past its bound, no new work · ? last seen "
                 "before the account went quiet, window still open")
# For a header that has to survive an 80-column terminal, where the long form
# truncates mid-word and loses ~ and ! entirely.
SOURCE_LEGEND_SHORT = "* inferred · ~ carried · ! too old · ? last seen"


def mark(src: str) -> str:
    return SOURCE_MARK.get(src, " ")


# The wall: at these nothing can run at all, and nothing is handed work.
# Below them an account is judged by how long it would last (`pick_account`),
# not by a line.
SHORT_BLOCK = triggers.WALL_PCT
LONG_BLOCK = triggers.WALL_PCT

# A floor is a lower bound, not a measurement, and there is an unbounded
# invisible spender even on an idle account: a human running `claude -p` by
# hand, or any other call no collector sees (fold's own risk is closed
# separately - it never pays with a stale reading at all, see
# fold._cheapest_account). idle_floor judges the floor this far below the
# wall to cover that - main's admission line (70), carried over as-is:
# main's MOVE_LINE_5H (85) minus its MOVE_MIN_HEADROOM_PCT (15). Against the
# wall this stack uses (100) that line is thirty points.
IDLE_FLOOR_MARGIN_PCT = 30

# How many candidates pick_account(live_refresh=True) may ask usage_api about
# in one call. _move_plan/find_room pass every logged-in candidate, and a
# fleet-wide outage (#1811's own scenario) can make several of them read
# carried_old or stale-idle-floored at once; without a cap this call blocks on
# that many sequential HTTP_TIMEOUT_S timeouts on the same 30s daemon tick
# poll_usage's own docstring budgets for - one success per tick, mirrored here
# as one attempt per call (auditor #1821 should-fix).
LIVE_REFRESH_MAX_PER_CALL = 1


def effective_context_limit(cfg: dict, model_id: str | None) -> int:
    """`agents.context_tokens(cfg)`, clamped to a fraction of the model's own
    window when collect.ctx_size recognizes it - the ceiling of last resort
    described at triggers.CONTEXT_CLAMP_FRACTION. An unrecognized model has no
    window to take a fraction of, so the configured-or-default figure stands
    alone.

    Lives here, not in agents.py: every caller (cli, daemon, tui) already
    imports status, and agents.py stays a leaf - it must not reach up into
    collect or vendors just to resolve a limit its own callers can resolve
    themselves (#1784, orcapipe/auditor).
    """
    limit = agents.context_tokens(cfg)
    size = collect.ctx_size(model_id)
    if size:
        limit = min(limit, int(size * triggers.CONTEXT_CLAMP_FRACTION))
    return limit


@dataclass
class Window:
    """One usage window as read back: a figure, when it resets, and how far to
    trust it. None is unknown - never coerced to 0."""
    minutes: int = 0
    pct: int | None = None
    reset: int | None = None
    # When the figure was actually observed, and whether it came from an earlier
    # payload than this one. `collected_at` only says the collector is alive.
    at: int | None = None
    carried: bool = False
    # True when the collector had a figure it could not carry because it could
    # not tell whether that figure's window had ended. That is ignorance, and
    # it must not read as a quiet window.
    lost: bool = False
    # False when the window is missing entirely, or present but unreadable (NaN,
    # a string, a bool). An explicit null is readable: it is how the collector
    # writes "this payload reported no such window".
    readable: bool = True

    @property
    def label(self) -> str:
        return windows.label(self.minutes)


@dataclass
class AccountStatus:
    account: str
    exists: bool = False
    # Its vendor reports no usage ocp can read (`Driver.meter()` is None). Not "no
    # reading yet": that is a claude account nothing has run on, a bootstrap
    # candidate, and nothing ever will give this one a reading.
    unmetered: bool = False
    raw: dict = field(default_factory=dict)
    error: str | None = None

    # The shortest and the longest window the vendor reported, found by length.
    # A vendor with one window has no `long`; the core never asks either what
    # it is called, only how long it is (`Window.label` when a person reads it).
    short: "Window" = field(default_factory=lambda: Window())
    long: "Window" = field(default_factory=lambda: Window())
    ctx_pct: int | None = None
    ctx_used: int | None = None
    cost_usd: float | None = None
    # Which session this reading came from, and where that session's own record
    # lives. Both belong to the session, so read_for_agent carries the agent's
    # own or nothing - never the account file's, which is whichever agent on
    # this account rendered last.
    session_id: str | None = None
    transcript_path: str | None = None
    model_id: str | None = None
    collected_at: int | None = None
    # The moment to judge ages against; None is the wall clock. A reader that
    # must stay pure (stats) or replay a past tick sets it instead of patching
    # time.time.
    as_of: float | None = None
    # Month cycle (`cycle`): when a one-month subscription stops being usable,
    # and whether the account is spent first while its last week is short.
    sub_end: float | None = None
    last_week_first: bool = True

    def _now(self) -> float:
        return time.time() if self.as_of is None else self.as_of

    @property
    def age_s(self) -> float | None:
        """Age of the snapshot, or None when it cannot be trusted.

        A timestamp in the future means a skewed clock or wrong units (a
        milliseconds value is the likely bug). Clamping that to age 0 made it
        look permanently fresh, which is fail-open. Unknown is the right answer.
        """
        if self.collected_at is None:
            return None
        age = self._now() - self.collected_at
        if age < -CLOCK_SKEW_GRACE_S:
            return None
        return max(0.0, age)

    @property
    def stale(self) -> bool:
        age = self.age_s
        return age is None or age > STALE_AFTER_S

    @property
    def short_age_s(self) -> float | None:
        """How long ago the shortest window's figure was observed, not when it was copied."""
        if self.short.at is None:
            return None
        age = self._now() - self.short.at
        if age < -CLOCK_SKEW_GRACE_S:
            return None
        return max(0.0, age)

    @property
    def is_live_reading(self) -> bool:
        """A figure this payload actually reported, timestamped, not carried
        forward from an earlier one - the only kind a rate can be measured
        from."""
        return self.short_source == "live" and self.short.pct is not None and bool(self.short.at)

    @property
    def short_source(self) -> str:
        """Which of the five cases this reading is: see the module docstring.

        `carried` says a *later* write found nothing new to report; it does
        not age on its own, and the one writer that sets it (`usage_api`)
        always clears it back to False the moment a poll succeeds - so an
        account whose poll then goes quiet for hours (the endpoint itself
        421/429s at the wall, or the token is dead) keeps a False flag
        forever. `at` is what actually bounds the carry (module docstring),
        so it is checked first: past `CARRY_MAX_AGE_S` this is carried_old
        regardless of the flag, collected_at's own freshness included - a
        session merely idling on the account must not keep an hours-old rate
        limit reading dressed as `live`.
        """
        if not self.exists or self.error or self.stale or not self.short.readable:
            return "unknown"
        if self.short.lost:
            return "unknown"
        if self.short.pct is None:
            return "quiet"
        age = self.short_age_s
        if age is not None and age > CARRY_MAX_AGE_S:
            return "carried_old"
        if self.short.carried:
            # A carried figure with no age at all cannot be judged inside the
            # bound above - unknown age is not the same claim as a fresh one,
            # so it reads as too old to trust rather than as live (auditor
            # #1805 note 3; unreached today, since usage_api's only writer
            # always sets `at` alongside `carried`, but a guard against a
            # payload that does not is cheap to keep honest).
            return "carried_old" if age is None else "carried"
        return "live"

    @property
    def short_trusted(self) -> int | None:
        """What we may act on, or None when we genuinely do not know.

        A frozen status.json (the poll cannot reach the endpoint, the account's
        token is dead, the daemon is down) keeps reporting its last value
        forever. Reading that as the truth is the same failure as reading a null
        as 0, so a stale snapshot reads as unknown.

        A quiet window reads as 0 instead, because a reading that reports no
        five_hour window is a positive observation: nothing has been spent on
        this account inside the current window. The hole in that - usage from
        outside ocp, on a config dir we do not collect from - is the same hole
        every other reading has.
        """
        src = self.short_source
        if src == "unknown":
            return None
        return 0 if src == "quiet" else self.short.pct

    @property
    def long_source(self) -> str:
        """Where the longest window's figure comes from, on the same terms as
        the shortest window's.

        The two are not trusted the same way and should not be, but they were
        not drawn the same way either: the shortest figure went to "?" the
        moment its snapshot went stale while the longest, from that same file,
        was drawn as a measurement, unmarked.

        Inside one window usage only rises, so an old reading is a floor and a
        floor is worth having - marked as carried, not as measured. Once the
        longest window's reset has passed it is not even that: it describes a
        window that no longer exists, and reading it as the truth is how an
        account reads 60% for a week after the week ended.

        The longest's `at`, not `collected_at`, bounds the carry here too (see
        `short_source`): checked before the `live` case, so a poll gone quiet
        for hours cannot pass for fresh just because something else is still
        pinging `collected_at`.
        """
        if not self.exists or self.error or self.long.lost or self.long.pct is None:
            return "unknown"
        now = int(self._now())
        if self.long.reset and self.long.reset <= now:
            return "unknown"
        age = None if self.long.at is None else now - self.long.at
        if age is not None and age > CARRY_MAX_AGE_S:
            return "carried_old"
        if self.stale or self.long.carried:
            # Same unknown-age case as short_source (auditor #1805 note 3).
            return "carried_old" if age is None else "carried"
        return "live"

    @property
    def long_trusted(self) -> int | None:
        """The weekly figure we may act on: a floor, or None when there is none."""
        return None if self.long_source == "unknown" else self.long.pct

    @property
    def trusted_ctx_pct(self) -> int | None:
        return None if self.stale else self.ctx_pct

    @property
    def trusted_ctx_used(self) -> int | None:
        return None if self.stale else self.ctx_used

    def last_known(self) -> str:
        """The figures of a stale snapshot, with their age, for a reader.

        Display only: nothing that decides reads this. A window whose reset has
        passed is marked, since the real figure is then lower than the one shown.
        """
        if not self.exists or self.error or self.short.pct is None:
            return ""
        now = self._now()
        age = "unknown age" if self.age_s is None else f"{timefmt.ago(self.age_s)} old"
        gone = ", reset since" if self.short.reset and self.short.reset <= now else ""
        text = f"{self.short.label} {self.short.pct}% ({age}{gone})"
        if self.long.pct is not None:
            week_gone = " (reset since)" if self.long.reset and self.long.reset <= now else ""
            text += f", {self.long.label} {self.long.pct}%{week_gone}"
        return text

    def assignable(self, active: int = 0) -> tuple[bool, str]:
        """May we give this account new work? Returns (allowed, reason).

        `active` is agents of ours already mid-turn on this account
        (`load.Load.active`); it only matters to a stale reading
        (`idle_floor`) - a live one is judged on what it reports, agents
        mid-turn on it and all."""
        if self.unmetered:
            return False, UNMETERED_WHY
        if not self.exists:
            return False, "no status.json - no session has run yet (fail-closed)"
        if self.error:
            return False, f"status.json unreadable: {self.error} (fail-closed)"
        if self.stale:
            idle_ok, idle_why = self.idle_floor(active)
            if idle_why:
                return idle_ok, idle_why
            # In the units a reader thinks in. "59213s > 900s" is two numbers
            # nobody subtracts; the line is on a dashboard, and what it has to
            # say is how long ago anything ran on this account.
            shown = "unknown" if self.age_s is None else timefmt.ago(self.age_s)
            return False, (f"nothing has run on it for {shown} - the snapshot is "
                           f"stale (fail-closed)")
        pct = self.short_trusted
        if pct is None:
            return False, f"{self.short.label} usage unknown (fail-closed)"
        if pct >= SHORT_BLOCK:
            return False, f"{self.short.label} usage {pct}% >= {SHORT_BLOCK}%"
        week = self.long_trusted
        if week is not None and week >= LONG_BLOCK:
            return False, f"{self.long.label} usage {week}% >= {LONG_BLOCK}%"
        if self.long.lost:
            return False, f"{self.long.label} usage was lost - the longest window's gate cannot be checked (fail-closed)"
        src = self.short_source
        if src == "carried_old":
            age = self.short_age_s
            shown = "unknown" if age is None else f"{int(age / 60)}m"
            return False, (f"{self.short.label} {pct}% was observed {shown} ago and only ever rises "
                           f"(carried past {CARRY_MAX_AGE_S}s)")
        if src == "quiet":
            return True, f"{self.short.label} window quiet - nothing spent in it (inferred 0%)"
        if src == "carried":
            return True, f"{self.short.label} {pct}% (carried, still inside the window)"
        return True, f"{self.short.label} {pct}%"

    def takes_work(self, active: int = 0) -> tuple[bool, str]:
        """May a session be started here: assignable, or a rolled-over stale
        account, and under the weekly wall. The usage half of the question; `unfit`
        adds the cap and the stretch. `active` - see `assignable`."""
        ok, why = self.assignable(active)
        week = self.long_trusted
        if not ok and self.rolled_over()[0]:
            # Its weekly figure is a floor unless the week itself has passed.
            ok, why = True, self.rolled_over()[1]
            week = None if self.long.reset and self.long.reset <= self._now() else self.long.pct
        if ok and week is not None and week >= LONG_BLOCK:
            return False, f"{self.long.label} usage {week}% >= {LONG_BLOCK}%"
        return ok, why

    def rolled_over(self) -> tuple[bool, str]:
        """A stale account whose 5h window has passed can take work if its last
        weekly reading had room: the session that starts there writes a fresh
        reading, and the move trigger takes the agent off if it is full.
        Returns (ok, reason)."""
        if (not self.exists or self.error or self.long.lost or not self.stale
                or self.age_s is None):
            return False, ""
        now = self._now()
        if self.short.reset:
            passed = self.short.reset <= now
        else:
            passed = bool(self.short.minutes) and self.age_s >= self.short.minutes * 60
        if not passed:
            return False, ""
        week = self.long.pct
        week_over = bool(self.long.reset and self.long.reset <= now)
        if week is None and not week_over:
            return False, ""
        if week is not None and week >= LONG_BLOCK and not week_over:
            return False, ""
        last = self.last_known()
        if last:
            return True, f"{last} - stale, window rolled over"
        shown = "its week has rolled over" if week_over else f"{self.long.label} {week}%"
        return True, f"stale, window rolled over, {shown}"

    def idle_floor(self, active: int = 0) -> tuple[bool, str]:
        """A stale account still inside its 5h window, with no agent of ours
        mid-turn on it, is idle rather than unknown, and keeps its floor.
        Returns (ok, reason).

        Ruled by the founder on #474: a stale reading inside an unrolled
        window is a floor, not unknown - that is what it is, not an
        exception written around a stricter rule. Three independent gates
        hold it to that:

        - No agent of ours is mid-turn on it (`active`). This one is defence
          against a premise, not an observed case: accounts.py sets a 10s
          refreshInterval, so a live session keeps collected_at fresh and an
          account cannot actually read as stale while an agent of ours is
          running on it (auditor, #475). It stays because it costs nothing
          and it is what keeps every reader of a stale reading agreeing -
          `pick_account`, `doctor`, the bridge - not because the case it
          guards has been seen.
        - The shortest window's reset has not passed. Past it the figure describes a window
          that no longer exists - `rolled_over` and the usage poll answer for
          that case, not this one.
        - The floor is a lower bound, not a measurement - there is an
          unbounded invisible spender even with no agent of ours running (a
          human running `claude -p` by hand; `ocp fold`'s own risk is closed
          separately, see fold._cheapest_account) - so it is judged this far
          below the wall (`IDLE_FLOOR_MARGIN_PCT`), never against the wall
          itself.
        """
        if (not self.exists or self.error or self.short.lost or not self.stale
                or self.short.pct is None or not self.short.readable or active > 0):
            return False, ""
        now = self._now()
        # Well-formed data cannot have its reset ahead of us while the snapshot
        # itself is already older than a window is wide - only a rewound clock
        # or a hand-edited file gets here, and that is not idle, it is unknown.
        if self.age_s is not None and self.age_s >= self.short.minutes * 60:
            return False, ""
        if not self.short.reset or self.short.reset <= now:
            return False, ""  # rolled_over's case, not this one
        last = self.last_known()
        text = last or f"{self.short.label} {self.short.pct}%"
        if self.short.pct >= SHORT_BLOCK - IDLE_FLOOR_MARGIN_PCT:
            return False, (f"{text} when last seen - too close to "
                           f"{SHORT_BLOCK}% to start on a stale reading")
        return True, f"{text} - stale but idle"

    def shown_short(self) -> tuple[int | None, str]:
        """What the shortest window's column draws: the figure and its mark.

        The trusted figure where there is one. Otherwise the floor an idle
        account was last seen at, marked `?` - nothing may act on it as a
        measurement, and a reader still needs to see whether the account they
        cannot assign was last at 1% or at 96%.
        """
        if self.short_trusted is not None:
            return self.short_trusted, mark(self.short_source)
        if (self.exists and not self.error and not self.short.lost and self.stale
                and self.short.pct is not None and self.short.readable
                and self.short.reset and self.short.reset > self._now()):
            return self.short.pct, IDLE_MARK
        return None, " "

    def window_report(self) -> list[dict]:
        """The shortest and, when the vendor has one, the longest window as a
        machine reader wants them: the figure, what we may act on, and why."""
        out = []
        for w, trusted, source in ((self.short, self.short_trusted, self.short_source),
                                   (self.long, self.long_trusted, self.long_source)):
            if w is self.long and not w.minutes:
                continue
            out.append({"window": w.label, "minutes": w.minutes, "pct": w.pct,
                        "effective": trusted, "source": source, "reset": w.reset})
        return out

    def short_headroom(self) -> int | None:
        """Remaining headroom in the shortest window, in percent, or None when unknown."""
        pct = self.short_trusted
        if pct is None:
            return None
        return max(0, SHORT_BLOCK - pct)

    def long_headroom(self) -> int | None:
        """Remaining weekly headroom in percent, or None when unknown."""
        week = self.long_trusted
        if week is None:
            return None
        return max(0, LONG_BLOCK - week)


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


UNMETERED_WHY = "unmetered - its vendor reports no usage; only agents that name it run there"


def _window_of(e: dict) -> Window:
    pct = _as_pct(e.get("used_percent"))
    # An explicit null means "no window in this payload", which is a reading.
    # An unreadable value means the window cannot be believed at all, and that
    # is the fail-closed case.
    readable = (e.get("readable") is not False
                and (e.get("used_percent") is None or pct is not None))
    return Window(minutes=e["minutes"], pct=pct, reset=_as_int(e.get("resets_at")),
                  at=_as_int(e.get("observed_at")), carried=e.get("carried") is True,
                  lost=e.get("lost") is True, readable=readable)


def windows_of(raw: dict) -> tuple[Window, Window]:
    """The shortest and the longest window a record holds. A vendor with a single
    window has no longest; a record with none has no shortest either, and that
    reads as unreadable, never as a quiet window."""
    got = [_window_of(e) for e in windows.entries(raw)]
    if not got:
        return Window(readable=False), Window()
    return got[0], (got[-1] if len(got) > 1 else Window())


def metered(acct: str) -> bool:
    """False only for an account whose vendor's driver says it cannot be read.
    A vendor with no driver is left to `logged_in`, which refuses it."""
    from .. import vendors
    try:
        return vendors.for_account(acct).meter() is not None
    except vendors.UnknownVendor:
        return True


def not_for(acct: str, vendor: str) -> str | None:
    """Why no agent of `vendor` is ever placed on this account, whatever its
    room: it is another vendor's (the agent would run its own binary against
    that vendor's credentials), or it cannot be read. None when it may be."""
    from .. import vendors
    theirs = vendors.account_vendor(acct)
    if theirs != vendor:
        return f"a {theirs} account, not {vendor}"
    if not metered(acct):
        return UNMETERED_WHY
    return cycle.ended_why(acct, time.time())


def read(acct: str, path: Path | None = None) -> AccountStatus:
    p = path or paths.status_file(acct)
    st = AccountStatus(account=acct)
    cfg = cycle.config()
    st.sub_end, st.last_week_first = cycle.end_of(acct, cfg), cycle.last_week_first(cfg)
    if not metered(acct):
        st.unmetered = True
        return st
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
    st.short, st.long = windows_of(raw)
    st.ctx_pct = _as_pct(raw.get("ctx_pct"))
    # The snapshot's own field is the raw usage breakdown (collect.snapshot's
    # "ctx_used"), not a total - sum it the same way collect.tokens does, or
    # leave it None when nothing was written, never 0. A bare number here
    # (an older or hand-written payload) is read as-is.
    raw_ctx_used = raw.get("ctx_used")
    st.ctx_used = (collect.tokens(raw_ctx_used) if isinstance(raw_ctx_used, dict)
                  else _as_int(raw_ctx_used))
    st.collected_at = _as_int(raw.get("collected_at"))
    st.session_id = raw.get("session_id")
    st.transcript_path = raw.get("transcript_path")
    st.model_id = raw.get("model_id")
    cost = raw.get("cost_usd")
    st.cost_usd = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None
    return st


def read_for_agent(agent: str, account: str | None) -> AccountStatus:
    """Usage as it applies to one agent. An agent on no account has run
    nothing, so nothing about it is known.

    Rate limits come from the account, because that is what they belong to.
    Context comes from the agent's own session file when it has one, so two
    agents sharing an account are not judged by each other's context.
    """
    if not account:
        return AccountStatus(account="")
    acct_status = read(account)
    agent_file = paths.agent_status_file(agent)
    if not agent_file.exists():
        # No snapshot of its own means no session of its own, so its context is
        # unknown - not whatever the last session on this account happened to
        # have. An agent that has never run was being shown another agent's
        # context, and judged on it.
        acct_status.ctx_pct = None
        acct_status.ctx_used = None
        acct_status.session_id = None
        acct_status.transcript_path = None
        acct_status.model_id = None
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
        acct_status.ctx_used = None
        acct_status.session_id = None
        acct_status.transcript_path = None
        acct_status.model_id = None
        return acct_status
    acct_status.ctx_pct = own.ctx_pct
    acct_status.ctx_used = own.ctx_used
    acct_status.session_id = own.session_id
    acct_status.transcript_path = own.transcript_path
    acct_status.model_id = own.model_id
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
    atomic.write_text(path, json.dumps({"burn_per_s": burn, "at": at}))


def read_burn(acct: str, now: float | None = None) -> float | None:
    """The recorded burn in percent per second, or None when there is none or
    it is too old to describe the present."""
    try:
        data = json.loads(_burn_path(acct).read_text())
        at, burn = int(data["at"]), float(data["burn_per_s"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return burn if (now or time.time()) - at <= BURN_MAX_AGE_S else None


@dataclass
class Runway:
    left_short: int           # points of the shortest window left before the wall
    left_long: int | None
    eta_s: float | None       # seconds to the wall at the current burn
    burning: bool             # False: no burn reading, or an idle account
    past_reset: bool          # the shortest window rolls over before the line is reached
    # How long the week lasts (`long_runway_s`) when that is known and the week
    # does not roll over first; None otherwise, and then the week is not what
    # runs out first.
    long_s: float | None = None
    long_label: str = ""      # what the longest window is called, for describe_runway


def runway(st: "AccountStatus", burn: float | None, now: float | None = None,
           ld=None) -> Runway | None:
    """An estimate of how long this account's headroom lasts, whichever window
    runs out first: the shortest at the current burn, the week by `long_runway_s`
    (`ld` is the account's load, which carries the plan's ratio; without it the
    week is not estimated). None when its reading is stale or
    unknown: no figure is better than one drawn from a reading that stopped
    describing the account."""
    pct = st.short_trusted
    if pct is None:
        return None
    now = now or time.time()
    week = st.long_trusted
    left = max(0, SHORT_BLOCK - pct)
    left7 = None if week is None else max(0, LONG_BLOCK - week)
    long_s = long_runway_s(st, ld, now)
    if not burn or burn <= 0:
        return Runway(left, left7, None, False, False, long_s, st.long.label)
    eta = left / burn
    # A window resets within its own length whether or not the collector told us when -
    # an eta beyond that is not a long wait, it is the reset arriving first.
    if st.short.reset:
        past_reset = now + eta > st.short.reset
    else:
        past_reset = eta > st.short.minutes * 60 if st.short.minutes else False
    return Runway(left, left7, eta, True, past_reset, long_s, st.long.label)


def describe_runway(r: Runway | None) -> str:
    if r is None:
        return ""
    if r.left_short == 0:
        return "at the wall"
    if r.left_long == 0:
        return f"at the {r.long_label} wall"
    # The window that runs out first is the one shown; a window whose reset
    # comes first does not run out at all.
    five = r.eta_s if r.burning and not r.past_reset else None
    if r.long_s is not None and (five is None or r.long_s < five):
        return f"~{timefmt.span(r.long_s)} to {r.long_label}"
    if not r.burning:
        return "no recent burn"
    return "outlasts reset" if r.past_reset else f"~{timefmt.span(r.eta_s)} to wall"


def team_outlook(names: list[str] | None = None, now: float | None = None) -> str:
    """One line on whether the team's combined headroom lasts to the next
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
    room = sum(r.left_short for _, r, _ in counted)
    pace = sum(b for _, _, b in counted)
    resets = [st.short.reset for st, _, _ in counted if st.short.reset and st.short.reset > now]
    nxt = min(resets) if resets else None
    clock = lambda t: timefmt.stamp(t, timefmt.CLOCK)  # noqa: E731
    n = f"{len(counted)} account{'s' if len(counted) != 1 else ''}"
    if pace <= 0:
        return f"estimate: {room} points of headroom across {n}, no recent burn"
    stops = now + room / pace
    if nxt and stops >= nxt:
        return f"estimate: at this pace {n} last past the next reset ({clock(nxt)})"
    tail = f"; next reset {clock(nxt)}" if nxt else ""
    return f"estimate: at this pace {n} hit the wall around {clock(stops)}{tail}"


# A tier for "cannot realistically be exhausted" was tried here and dropped
# (#1085): telling an abundant account from a thrifty one needs to know plan
# size, and nothing this module reads carries it: headroom is a percent of a
# window, capped at 100 whatever the plan, and agent_burn is one estimate
# shared by every account (load.py: "shared by every account, built once at
# load.py:154"). The only per-account figure is the week-per-5h ratio
# (`long_stretch_s`), which converts one window's points into the other's and
# says nothing of how many agents a plan can carry. So the
# only thing that varied between accounts in that computation was the cap,
# and "cap running flat out would not spend the headroom before reset" reduces
# to "both windows reset soon" - the earliest-deadline rule this change exists
# to replace, reinstated above the rule that replaced it, and confirmed by its
# own test: the fixture that was supposed to prove tier 1 bound something used
# a reset 30 minutes out, and picked the account with less headroom and the
# sooner deadline over the one with more headroom and a pace far behind. Pace
# alone carries the rule now.


# A pace this close to another's is noise, not a real difference in how far
# behind two accounts are - two readings a point apart on a 5h window jitter
# by more than this from one poll to the next. Without a dead zone the sort
# would relitigate the same near-tie every tick as rounding moved it back and
# forth, moving new work between two accounts that are, for this purpose,
# equally behind. Ties inside it fall through to account name, which never
# changes - so a run of picks either stays on one account or crosses to the
# other exactly once, on the poll where the gap actually opens past this line.
PACE_HYSTERESIS = 0.02


def week_ends_in(st: "AccountStatus", now: float | None = None) -> float | None:
    """Seconds until the week stops mattering: its reset, or the end of the
    subscription when that comes first. None where the reset is unknown and no
    end is set."""
    ends = [t for t in (st.long.reset, st.sub_end) if t]
    return min(ends) - (st._now() if now is None else now) if ends else None


def long_pace(st: "AccountStatus") -> float:
    """This account's weekly usage against its weekly timer: its longest window's figure as a
    multiple of the usage a uniform pace through the week would have reached by
    now. Below 1 is behind that pace - the "use it or lose it" case, since
    whatever headroom is left unspent here is not carried past the reset - and
    is what `pick_account` spends first, on the same reasoning the old
    earliest-7d-deadline rule was right about: an account that is not going to
    catch up on its own gets the work while the work is still worth something
    to it.

    Infinity for a figure or a reset we do not know, or a week already
    rolled over: never sorted as low usage, which reading it as 0 - or as
    ratio 0/0 - would do.
    """
    week = st.long_trusted
    if week is None or not st.long.reset:
        return float("inf")
    remaining = st.long.reset - st._now()
    if remaining <= 0:
        return float("inf")
    length = st.long.minutes * 60
    if st.last_week_first and st.sub_end and st._now() < st.sub_end < st.long.reset:
        # The subscription ends inside this week, so the week is the shorter
        # span from its start to that end, with all of its allowance still
        # spendable: the same account sits further behind its pace, and goes first.
        length = st.sub_end - (st.long.reset - length)
        remaining = st.sub_end - st._now()
    elapsed_frac = max(length - remaining, 1.0) / length
    return week / (elapsed_frac * 100)


def account_verdict(name: str, st: "AccountStatus", ld, ranked: list[tuple[str, str]],
                    logged_in: bool) -> tuple[bool, str]:
    """Whether a session may start on this account, and the sentence for it.

    The accounts panel draws the tick from this and the account's page draws
    the sentence, so the two cannot say different things. `ranked` is
    `pick_account`'s reasons, ranked for this account's own vendor; an account
    outside the ranking falls back to what `takes_work` says.
    """
    if not logged_in:
        return False, "not logged in"
    from .. import vendors
    theirs = vendors.account_vendor(name)
    try:
        vendors.get(theirs)
    except vendors.UnknownVendor as e:
        # Nothing can start on an account whose vendor has no driver, and
        # `not_for` cannot say so here: its cross-vendor clause never fires
        # once the vendor asked is the account's own.
        return False, str(e)
    never = not_for(name, theirs)
    if never:
        return False, never
    allowed, why = st.takes_work(ld.active if ld else 0)
    return allowed and unfit(st, ld) is None, dict(ranked).get(name, why)


def unfit(st: "AccountStatus", ld, agent: str | None = None) -> str | None:
    """Why a session should not be started here, when what is known says so.

    Only a reading that says so counts: a missing or stale one is the account
    nobody has run on lately, and the session about to start is the only thing
    that writes a fresh one. `pick_account` is the stricter question, for
    choosing between accounts; this is the one for an agent whose own account
    is not known to be a bad place.
    """
    pct, week = st.short_trusted, st.long_trusted
    full = pct is not None and pct >= SHORT_BLOCK
    # A stale reading at the wall is still a floor until its window has rolled
    # over, but one older than 5h cannot describe the current window.
    if not full and st.short.pct is not None and st.short.pct >= SHORT_BLOCK:
        now = st._now()
        full = st.short.reset > now if st.short.reset else (
            st.age_s is not None and st.age_s < st.short.minutes * 60)
    if full:
        return f"{st.short.label} usage at the wall ({st.short.pct}%)"
    if week is not None and week >= LONG_BLOCK:
        return f"{st.long.label} usage {week}% at the wall"
    if pct is not None and not st.stale:
        return _no_room(st, ld, pct, agent)[0]
    return _no_room(st, ld, None, agent)[0]


def agent_burn(ld, agent: str | None) -> float:
    """What one more agent costs, in percent of the 5h window per second: the
    measured cost of `agent`'s kind, of a typical agent when none is named, and
    the starting value where nothing measured is to hand."""
    return ld.costs.per_s(agent) if ld and ld.costs else triggers.TYPICAL_AGENT_BURN_PER_S


def long_stretch_s(st: "AccountStatus", ld, agent: str | None = None) -> float | None:
    """How long the week's headroom lasts with one more agent on this account,
    the 5h stretch's own arithmetic on the weekly window: its fill and the
    newcomer's cost, both measured in the 5h window's points, converted by the
    plan's measured ratio (`burnrate.Costs.long_per_short`). With no recent fill it
    is the newcomer's cost alone. None where the week or the ratio is unknown:
    no figure is better than one that guesses the plan."""
    week = st.long_trusted
    ratio = ld.costs.long_per_short(ld.account) if ld and ld.costs else None
    if week is None or ratio is None:
        return None
    fill = (ld.burn or 0.0) * ratio
    return triggers.stretch_s(LONG_BLOCK - week, fill, agent_burn(ld, agent) * ratio)


def long_runway_s(st: "AccountStatus", ld, now: float | None = None) -> float | None:
    """How long the week lasts, for the runway column: at the current fill when
    the account is filling, the question the 5h side answers. An idle account
    has no fill to run out at, so it shows the week only where the pick shut it
    out on the week (`_no_long_room`, one more agent's cost). None where the
    week or the ratio is unknown, or the week rolls over first."""
    now = now or time.time()
    week = st.long_trusted
    ratio = ld.costs.long_per_short(ld.account) if ld and ld.costs else None
    if week is None or ratio is None:
        return None
    fill = (ld.burn or 0.0) * ratio
    if fill > 0:
        lasts = (LONG_BLOCK - week) / fill
    elif _no_long_room(st, ld):
        lasts = long_stretch_s(st, ld)
    else:
        return None
    # A window resets within its own length whether or not the collector told us when.
    reset_in = week_ends_in(st, now)
    if reset_in is None:
        reset_in = st.long.minutes * 60
    return None if reset_in <= lasts else lasts


def _no_long_room(st: "AccountStatus", ld, agent: str | None = None) -> str | None:
    """Why the week cannot carry one more agent: it would run out in less than
    a start is worth (`MIN_STRETCH_S`, the same line the 5h window is held to)
    and before the week rolls over. 99% of a week with a day and a half to go
    is such a week; 99% with minutes to go is not, because the reset arrives
    first. A stale week reading is unknown here (`long_trusted`), so the week
    is not judged and only the 5h window is: no figure over a stale one."""
    lasts = long_stretch_s(st, ld, agent)
    if lasts is None or lasts >= triggers.MIN_STRETCH_S:
        return None
    left = week_ends_in(st)
    if left is not None and left <= lasts:
        return None
    ends = "ends" if st.sub_end and (not st.long.reset or st.sub_end < st.long.reset) else "resets"
    reset = f", {ends} in {timefmt.span(left)}" if left and left > 0 else ""
    return (f"{st.long.label} {st.long_trusted}%{reset}: one more agent lasts ~{timefmt.span(lasts)} of it - "
            f"under the {timefmt.span(triggers.MIN_STRETCH_S)} a start is worth")


def _no_room(st: "AccountStatus", ld, pct: int | None,
             agent: str | None = None) -> tuple[str | None, float | None]:
    """(why it cannot take one more agent, how long it would last). The cap,
    the week's stretch, then the 5h stretch when the fill `pct` is known; the
    one test `unfit` and `pick_account` share. `agent` prices the newcomer by
    its own kind."""
    if ld and ld.at_cap:
        return f"at its cap: {ld.active} of {ld.cap} agents mid-turn", None
    weekly = _no_long_room(st, ld, agent)
    if weekly:
        return weekly, None
    if pct is None:
        return None, None
    lasts = triggers.stretch_s(SHORT_BLOCK - pct, ld.burn if ld and ld.burn else 0.0,
                               agent_burn(ld, agent))
    if lasts < triggers.MIN_STRETCH_S:
        return (_stretch_reason(st, ld, lasts, pct, agent) + f" - under the "
                f"{timefmt.span(triggers.MIN_STRETCH_S)} a start is worth"), lasts
    return None, lasts


def _per_min(rate: float, places: int = 1) -> str:
    return f"{rate * 60:.{places}f}%/min"


def _stretch_reason(st: "AccountStatus", ld, lasts: float, pct: int,
                    agent: str | None = None) -> str:
    """`pct` is the figure the stretch was computed on - the trusted one for a
    live reading, or the floor idle_floor admitted for a stale one. Printing
    `st.short_trusted` instead would read "None%" for exactly the accounts
    that reach here on a floor, since that property is None while stale."""
    fill = ld.burn if ld and ld.burn else 0.0
    agents_ = f", {ld.active}/{ld.cap} agents" if ld else ""
    return (f"{st.short.label} {pct}%, fill {_per_min(fill)}{agents_}, one more agent "
            f"({_per_min(agent_burn(ld, agent), 2)}) lasts ~{timefmt.span(lasts)}")


def _pick_reason(st: "AccountStatus", ld, lasts: float | None, picked: bool,
                 agent: str | None = None) -> str:
    """Why this account sits where it does in the ordering."""
    week = st.long_trusted
    week_s = "unknown" if week is None else f"{week}%"
    tag = " <- picked" if picked else ""
    for stale_ok, stale_why in (st.rolled_over(), st.idle_floor(ld.active if ld else 0)):
        if stale_ok:
            return stale_why + tag
    when = "unknown" if not st.long.reset else timefmt.ago(max(0.0, st.long.reset - st._now()))
    pace = long_pace(st)
    pace_s = "unknown pace" if pace == float("inf") else f"{pace:.2f}x its weekly pace"
    # Neither early return above fired, so this account is live, not stale -
    # short_trusted is never None here.
    base = (_stretch_reason(st, ld, lasts, st.short_trusted, agent) if lasts is not None
            else f"{st.short.label} {st.short_trusted}%") + f"; {st.long.label} {week_s} (resets in {when}, {pace_s})"
    return base + tag


def pick_account(candidates: list[str] | None = None, bootstrap: bool = False,
                 exclude: dict[str, str] | None = None,
                 load: dict | None = None,
                 agent: str | None = None,
                 prefer: str | None = None,
                 read_status=None,
                 live_refresh: bool = False,
                 vendor: str | None = None) -> tuple[str | None, list[tuple[str, str]]]:
    """Pick among the accounts that can take one more agent - any account that
    will not hit its own wall, the same test `unfit` already answers - the one
    furthest behind its own weekly pace (`long_pace`) goes first, since headroom
    a slower account leaves unspent is lost at its reset and one already ahead
    of pace is not going to run out on its own.

    An earlier version of this also ranked a top tier of accounts "abundant"
    enough to be spent without limit, ahead of pace. Dropped (#1085): telling
    an abundant account from a thrifty one needs to know plan size, and
    nothing here reads it - headroom is a percent of a window, capped at 100
    whatever the plan, and the per-agent cost this module has is one estimate
    shared by every account (the week-per-5h ratio is per account but only
    converts one window's points into the other's). What that tier actually
    computed was "does this account's window reset before its cap could
    plausibly spend the headroom", which - at the one burn rate available -
    reduces to "resets soon", the earliest-deadline rule this change exists to
    replace. Pace alone carries the rule now.

    `prefer` is the account a repeated caller picked last time - the accounts
    panel redrawing, or a wait re-checked every tick. Passed back in, it keeps
    its seat inside the pace dead zone `PACE_HYSTERESIS` already gives every
    other pair: without memory of the last pick, two accounts a hair apart in
    pace still swap the lead back and forth as rounding tips first one way then
    the other while both climb, which is exactly the flicker a repeated caller
    must not show. A real gap past that zone still moves it - `prefer` only
    breaks a tie that would otherwise come down to which side of a bucket
    boundary rounding landed on this time.

    Returns (choice, [(account, reason)]). When none can, the choice is None
    and the caller must refuse - that is the fail-closed path.

    An account can take one more agent when it is not at the wall, is under its
    cap of agents mid-turn (`load`), and lasts at least `MIN_STRETCH_S` before
    its wall at its current fill plus one more agent of the kind `agent` names
    (a typical one when it names none), and the same holds of its longest
    window (`_no_long_room`, at the plan's measured long-per-short ratio)
    unless that window rolls over first. How fast it is filling, not a fixed
    line, is what says it has room: an account at 60% and idle has more than
    one at 30% with three agents on it.

    `exclude` names accounts to skip and why (the one an agent just left, until
    it resets). `load` defaults to reading it.

    `bootstrap=True`: a logged-in account with no status.json at all - never
    run, so nothing else could ever give it one - is pickable as a last
    resort, in list_accounts order, once nothing with an actual reading
    qualifies. Every other refusal (stale, at the wall, unreadable) still
    refuses; a stale account that has run once and gone quiet is not
    "unread", and needs its own explicit preference to be picked again.

    `vendor` is which vendor to pick for when no `agent` names one - what a
    caller holding an account rather than an agent asks, so that the reasons
    it gets back are the ones that vendor's agents would be given. `agent`
    wins where both are passed: an agent's own vendor is not in doubt.

    `read_status` replaces `read` as the source of each candidate's
    AccountStatus - what `replay.py` passes, to ask this same function about
    an account as a recorded sample had it, not as its live status.json
    reads today.

    `live_refresh=True` (founder #1811 part 2, #1816): before a candidate is
    refused for being carried_old, or admitted only on an idle floor that
    predates whatever it now stands for, ask the account's own OAuth
    credential directly (`usage_api`, one HTTPS GET, no session) and use the
    answer if it came back. Without this, a decision that turns on a figure
    nobody has refreshed either parks an agent beside an account that could
    in fact take it (a carried_old destination read as "unknown" and refused,
    though the background poller would have found room within minutes), or
    admits one on a floor stale enough that the account has since crossed the
    wall in a window this call never re-checked. `replay.py` never passes
    this - a recorded sample cannot be asked anything, live or not - and
    every caller that is answering "what would happen" rather than "give an
    agent somewhere to go right now" (a screen redraw, `ocp status`) leaves it
    False: a display must not cost a network call just by being drawn.
    """
    from ..team import agents
    from .. import vendors
    from ..infra.yamlcompat import ConfigError
    read_one = read_status or read
    names = candidates if candidates is not None else paths.list_accounts()
    try:
        picking_for = (vendors.agent_vendor(agent) if agent
                       else vendor or agents.DEFAULT_VENDOR)
    except (OSError, ConfigError) as e:  # an unreadable agent.yaml picks nowhere
        return None, [(n, f"agent.yaml unreadable ({e})") for n in names]
    if load is None:
        from . import load as loadmod
        load = loadmod.read_all()
    reasons: list[tuple[str, str]] = []
    usable: list[tuple[str, AccountStatus, object, float | None]] = []
    blank: list[str] = []
    live_refresh_left = LIVE_REFRESH_MAX_PER_CALL
    for n in names:
        if paths.removing(n):
            reasons.append((n, "being removed"))
            continue
        if exclude and n in exclude:
            reasons.append((n, exclude[n]))
            continue
        # Before anything about room: neither kind has any to weigh, and both
        # would otherwise reach the bootstrap list below, having no status.json.
        never = not_for(n, picking_for)
        if never:
            reasons.append((n, never))
            continue
        st = read_one(n)
        ld = load.get(n)
        active = ld.active if ld else 0
        if live_refresh and live_refresh_left > 0 and accounts.logged_in(n):
            # Same two figures the rest of this function already treats
            # specially - a carried_old reading (refused below) and a floor
            # idle_floor would admit on (sized below) - asked fresh exactly
            # once, in place, before either decision is made on the old one.
            stale_here = (st.short_source == "carried_old"
                          or (st.short_trusted is None and st.idle_floor(active)[0]))
            if stale_here:
                live_refresh_left -= 1
                fresh = usage_api.read(n, refresh=False)
                if fresh is not None:
                    usage_api.record(fresh)
                    st = read_one(n)
        allowed, why = st.takes_work(active)
        if not allowed:
            if bootstrap and not st.exists:
                blank.append(n)
                reasons.append((n, "no reading yet - a session has never run on it "
                                  "(bootstrap candidate, used only if nothing else qualifies)"))
            else:
                reasons.append((n, why))
            continue
        # A stale account whose window has rolled over reads as a fresh one; a
        # stale account still inside its window is judged on the floor it was
        # admitted on, not on a fill of 0 - that would prefer the account we
        # cannot see over one we can see has real room.
        if st.short_trusted is not None:
            pct = st.short_trusted
        elif st.idle_floor(active)[0]:
            pct = st.short.pct
        else:
            pct = 0
        no_room, lasts = _no_room(st, ld, None if st.rolled_over()[0] else pct, agent)
        if no_room:
            reasons.append((n, no_room))
            continue
        usable.append((n, st, ld, lasts))
    if not usable:
        return (blank[0] if blank else None), reasons

    def sort_key(item):
        n, st, ld, _ = item
        pace = long_pace(st)
        # A dead zone, not the raw figure: two paces this close are noise, not
        # a real difference in how far behind the accounts are (PACE_HYSTERESIS
        # docstring). Account name is the final tiebreak precisely because it
        # never moves - two accounts landing in the same bucket keep the same
        # order every time, rather than the pick drifting with rounding.
        bucket = pace if pace == float("inf") else round(pace / PACE_HYSTERESIS) * PACE_HYSTERESIS
        if prefer == n and bucket != float("inf"):
            # A full step's head start: a challenger only unseats the last
            # pick by clearing the dead zone outright, not by tying it - a
            # tie is what rounding alone produces as both accounts climb.
            bucket -= PACE_HYSTERESIS
        # Pace ties at infinity whenever the weekly figure is unknown for every
        # candidate - most tests, and any account nobody has tracked a week on
        # yet - so headroom is still the tiebreak under it, not dropped: the
        # old "maximizes headroom" rule survives exactly where pace has nothing
        # to say. The same fallback also settles two known, finite paces that
        # land in the same bucket without being equal - a real if narrow case
        # (auditor's sweep found it in 15 of 2774 two-account picks) - and
        # that is deliberate too: PACE_HYSTERESIS says a gap this small is
        # noise, so headroom, not an arbitrary order, breaks what pace no
        # longer distinguishes. `prefer` breaks a bucket tie before headroom
        # does, though - the head start above can land exactly on a challenger's bucket, and
        # that tie must still go to the incumbent, not to whichever of them
        # happens to have a hair more headroom this tick.
        return (bucket, 0 if n == prefer else 1,
                -(st.short_headroom() or 0), -_CONFIDENCE.get(st.short_source, 0), n)

    usable.sort(key=sort_key)
    picked = usable[0][0]
    for n, st, ld, lasts in usable:
        reasons.append((n, _pick_reason(st, ld, lasts, picked=(n == picked), agent=agent)))
    order = {n: i for i, n in enumerate(names)}
    reasons.sort(key=lambda t: order[t[0]])
    return picked, reasons
