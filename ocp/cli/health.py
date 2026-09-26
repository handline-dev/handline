"""Reads of the whole system: doctor, status, drift, triggers, events, fold, rollover, backup."""
from __future__ import annotations

from pathlib import Path

from .. import actions
from ..infra import timefmt
from ..team import agents
from ..team import ask as askmod
from ..infra import backup as backupmod
from ..daemon import lifecycle as dmn_lifecycle
from .. import db
from ..infra import doctor
from ..session import drift
from ..session import events
from ..session import fold as foldmod
from ..session import launch
from ..usage import load
from ..infra import paths
from ..usage import status
from ..infra import textsafe
from ..session import triggers

from .ask import _refused_listing


# ---------------------------------------------------------------- checks
def cmd_doctor(args) -> int:
    r = doctor.run()
    print(r.render(color=not args.no_color))
    return 1 if r.failed else 0


def cmd_status(args) -> int:
    picked, reasons = status.pick_account()
    # What the pick's "one more agent lasts" rests on: each kind's price and
    # whether it was measured or is still the starting value.
    priced = load.costs().by_kind(paths.list_agents())
    if args.json:
        print(textsafe.json_dumps({"picked": picked,
                                  "candidates": [{"account": a, "reason": w} for a, w in reasons],
                                  "agent_costs": priced}, indent=2))
        return 0 if picked else 1
    for a, why in reasons:
        print(f"  {a:<14} {why}")
    if priced:
        print("  one more agent costs, per minute of the 5h window: " + "; ".join(priced))
    print()
    print(f"picked: {picked}" if picked
          else "picked: none - no account may take work (fail-closed)")
    return 0 if picked else 1


def cmd_drift(args) -> int:
    # Filenames come from git, which C-quotes a control byte in a path - but
    # only by default (core.quotePath=false, standard advice for readable
    # Korean/Japanese names, turns that off), so this prints its own way.
    d = drift.check(Path(args.repo).resolve(), base=args.base, fetch=not args.no_fetch)
    print(textsafe.oneline(d.summary()))
    if d.overlap:
        print("\noverlapping files:")
        for f in d.overlap[:20]:
            print(f"  {textsafe.oneline(f)}")
    print(f"\nresume header:\n  {d.resume_header()}")
    return 0 if d.ok and not d.significant else 1


def _name_col(names, least: int = 12) -> int:
    """How wide a column of agent names has to be. Sized to the names there
    are: a fixed width ran `orcapipe-manager` into the column beside it."""
    return max(least, max((len(str(n)) for n in names), default=0) + 2)


def cmd_triggers(args) -> int:
    """Show what the daemon would decide right now, without acting."""
    w = _name_col(paths.list_agents(), 14)
    print(f"{'AGENT':<{w}}{'ACCT':<10}{'5h':>5}{'ctx':>5}  FIRES  REASON")
    con = db.connect()
    try:
        for a in paths.list_agents():
            try:
                # The account the daemon is watching, which is where the
                # session went. Read anywhere else, it prints another
                # account's figure and FIRES YES while the daemon, reading
                # the right one, does nothing: the dashboard arguing with the
                # daemon.
                acct = agents.current_account(a, con)
                cfg = agents.read(a)
            except Exception as e:  # noqa: BLE001
                print(f"{a:<{w}} config error: {e}")
                continue
            st = status.read_for_agent(a, acct)
            ctx_limit = status.effective_context_limit(cfg, st.model_id)
            t = triggers.evaluate(st.short_trusted, st.trusted_ctx_used, ctx_limit=ctx_limit)
            cell = lambda v: "  -" if v is None else f"{v:>3}"  # noqa: E731
            # The figure the trigger used, not the raw one: a column saying "-"
            # beside a reason saying "0%" is the dashboard arguing with the daemon.
            shown, src = st.shown_short()
            five = f"{cell(shown)}{src}"
            row = db.get_agent_state(con, a)
            if dmn_lifecycle.triggers_apply(row):
                fires, reason = "YES" if t.fired else "no ", t.reason
            else:
                fires = "no "
                walled = dmn_lifecycle.would_wake(con, row) and launch.wake_wait_reason(a, row, con)
                reason = walled or (f"{row['state'].lower()}; judged again at its wake" if row and row["terminal"]
                                    and not row["closed_at"] else "session closed; nothing to hand off")
            print(f"{a:<{w}}{acct or '-':<10}{five:>5}{cell(st.ctx_pct):>5}  "
                  f"{fires:<6} {reason}")
    finally:
        con.close()
    print("  " + status.SOURCE_LEGEND)
    return 0


# ---------------------------------------------------------------- record
def cmd_fold(args) -> int:
    targets = [args.agent] if args.agent else paths.list_agents()
    if not targets:
        print("no agents registered.")
        return 1
    bad = 0
    for a in targets:
        ok, msg = foldmod.fold(a, use_model=not args.no_model, model=args.model,
                               fold_account=args.account,
                               repo=Path(args.repo).resolve() if args.repo else None)
        print(textsafe.text(msg))
        bad += 0 if ok else 1
    return 1 if bad else 0


def cmd_rollover(args) -> int:
    targets = [args.agent] if args.agent else paths.list_agents()
    for a in targets:
        _, msg = foldmod.rollover(a)
        print(msg)
    return 0


def cmd_backup(args) -> int:
    if args.action == "install":
        ok, msg = backupmod.install()
        print(msg)
        return 0 if ok else 1
    out = backupmod.snapshot()
    print(f"wrote {out}" if out else "unchanged since the last snapshot")
    return 0


def cmd_events(args) -> int:
    if _refused_listing(args.agent):
        return 1
    only = None if args.agent else actions.listable_agents(askmod.asking_agent())
    con = db.connect()
    try:
        # Without --after this is "what just happened", so it starts at the
        # newest end. Reporting the page size as the total, from the oldest
        # end, told an operator there were 50 events when there were 187.
        if args.after:
            rows = db.events_after(con, args.after, agent=args.agent, limit=args.limit,
                                    only=only)
        else:
            rows = db.latest_events(con, agent=args.agent, limit=args.limit, only=only)
        if not args.all:
            # The hook events that only mark the edges of a turn are what an
            # agent's state column says already, and there are six of them for
            # every decision. `ocp bridge` used to filter them on screen; this is
            # where someone asking what the daemon did actually looks.
            rows = [r for r in rows if r["kind"] not in events.TURN_BOUNDARIES]
        if not rows:
            print("no events" if args.all else "no decisions (--all includes turn edges)")
            return 0
        for r in rows:
            when = timefmt.stamp(r["ts"], timefmt.SHORT)
            print(f"{r['id']:<6} {when} {r['agent']:<12} {textsafe.oneline(r['kind']):<16} "
                  f"{textsafe.oneline(r['payload'] or '')[:70]}")
        # With --after the whole table is not the population being paged, and
        # saying it is misleads in the same direction as the bug this replaced.
        total = db.count_events(con, agent=args.agent, after=args.after, only=only)
        scope = f" after {args.after}" if args.after else ""
        print(f"\nshowing {len(rows)} of {total} events{scope}; "
              f"cursor for fold:<agent> advances only after a valid fold")
    finally:
        con.close()
    return 0
