"""Reads of the whole system: doctor, status, triggers, events, fold, backup."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from .. import actions
from ..infra import timefmt
from ..team import agents
from ..team import ask as askmod
from ..infra import backup as backupmod, daemonclient as dmn_client
from .. import db
from ..infra import doctor
from ..session import events
from ..session import fold as foldmod
from ..infra import paths
from ..usage import status
from ..infra import textsafe

from . import client
from .ask import _refused_listing


# ---------------------------------------------------------------- checks
def _started_at(pid: int, record: Path) -> float | None:
    """When `pid` started running its current code, as epoch seconds: the mtime of `record`, the file it
    wrote its pid to when it took its place. A daemon or bridge that restarts onto new code execs into the
    same pid, so the process table's start stays at the first fork; the record is rewritten by each exec.
    The table is the answer only where there is no record, and an agent's sandbox may not read it.
    None when neither says."""
    try:
        return record.stat().st_mtime
    except OSError:
        pass
    try:
        out = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=10, env={**os.environ, "LC_ALL": "C"}).stdout.strip()
        return time.mktime(time.strptime(out, "%a %b %d %H:%M:%S %Y"))
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def stale_long_lived(started_at=None) -> list[str]:
    """What stops `--drop-dead-columns`: the daemon, if up now and started before the
    newest code file on disk (they run older code, and a connect of theirs would re-add the columns a drop
    removed), or whose start cannot be read. Each is named with its pid."""
    started_at = started_at or _started_at
    from ..daemon import devmode
    from ..infra import daemonlock, lock
    code = max((f.stat().st_mtime for f in devmode._watched_files(paths.repo_root()) if f.exists()), default=0)
    stale = []
    pid = daemonlock.running_elsewhere()
    if pid:
        began = started_at(pid, paths.home() / "locks" / f"{daemonlock.DAEMON_LOCK}.{lock.SINGLETON}")
        if began is None:
            stale.append(f"daemon (pid {pid}; cannot tell when it started)")
        elif began < code:
            stale.append(f"daemon (pid {pid})")
    return stale


def _dry_run_drop_dead_columns(stale: list[str]) -> int:
    """What `--drop-dead-columns` would do, changing nothing; 1 when the real run would be refused."""
    from ..db import schema
    con = db.connect()
    try:
        cols = schema.unread_request_columns(con)
    finally:
        con.close()
    print("would drop from the requests table: " + (", ".join(cols) if cols else "nothing (no dead columns left)"))
    if cols:
        print("  first moving what a process on older code still holds only in them (answers, reasons, "
              "dismissals) to where the readers look")
    refused = []
    if stale:
        refused.append(f"{' and '.join(stale)} started before the code now on disk or not known to have started "
                       "after it; restart it first")
    gate = backupmod.require_recent()
    print(backupmod.describe_newest())
    if gate:
        refused.append(gate)
    for why in refused:
        print(f"would refuse: {why}")
    if not refused:
        print("backup gate and long-lived processes: passed")
    return 1 if refused else 0


def cmd_doctor(args) -> int:
    if args.settle_leftovers:
        con = db.connect()
        try:
            for rid in db.settle_leftover_deliveries(con):
                print(f"#{rid}: settled, quietly")
        finally:
            con.close()
    if args.dry_run and not args.drop_dead_columns:
        print("--dry-run is for --drop-dead-columns", file=sys.stderr)
        return 2
    if args.drop_dead_columns:
        from ..db import schema
        stale = stale_long_lived()
        if args.dry_run:
            return _dry_run_drop_dead_columns(stale)
        if stale:
            print(f"not dropping: {' and '.join(stale)} - started before the code now on disk or not known to have "
                  "started after it; restart it, then run this again", file=sys.stderr)
            return 1
        no_backup = backupmod.require_recent()
        if no_backup:
            print(f"not dropping: {no_backup}", file=sys.stderr)
            return 1
        con = db.connect()
        try:
            gone = schema.drop_unread_request_columns(con)
        finally:
            con.close()
        print(f"dropped: {', '.join(gone)}" if gone else "no dead columns left")
    r = doctor.run()
    print(r.render(color=not args.no_color))
    return 1 if r.failed else 0


def cmd_status(args) -> int:
    try:
        got = client.call("status.pick")
    except (dmn_client.DaemonDown, dmn_client.RpcError, OSError) as e:
        print(f"could not read the pick: {e}", file=sys.stderr)
        return 1
    picked = got["picked"]
    if args.json:
        print(textsafe.json_dumps(got, indent=2))
        return 0 if picked else 1
    for c in got["candidates"]:
        print(f"  {c['account']:<14} {c['reason']}")
    # What the pick's "one more agent lasts" rests on: each kind's price and
    # whether it was measured or is still the starting value.
    if got["agent_costs"]:
        print("  one more agent costs, per minute of the 5h window: " + "; ".join(got["agent_costs"]))
    print()
    print(f"picked: {picked}" if picked
          else "picked: none - no account may take work (fail-closed)")
    return 0 if picked else 1


def _name_col(names, least: int = 12) -> int:
    """How wide a column of agent names has to be. Sized to the names there
    are: a fixed width ran `orcapipe-manager` into the column beside it."""
    return max(least, max((len(str(n)) for n in names), default=0) + 2)


def cmd_triggers(args) -> int:
    """Show what the daemon would decide right now, without acting."""
    try:
        verdicts = client.call("agent.triggers")["agents"]
    except (dmn_client.DaemonDown, dmn_client.RpcError, OSError) as e:
        print(f"could not read the triggers: {e}", file=sys.stderr)
        return 1
    w = _name_col([v["agent"] for v in verdicts], 14)
    print(f"{'AGENT':<{w}}{'ACCT':<10}{'5h':>5}{'ctx':>5}  FIRES  REASON")
    con = db.connect()
    try:
        for v in verdicts:
            a = v["agent"]
            # The account the daemon is watching, which is where the
            # session went. Read anywhere else, it prints another
            # account's figure and FIRES YES while the daemon, reading
            # the right one, does nothing: the dashboard arguing with the
            # daemon.
            acct = agents.current_account(a, con)
            st = status.read_for_agent(a, acct)
            cell = lambda n: "  -" if n is None else f"{n:>3}"  # noqa: E731
            # The figure the trigger used, not the raw one: a column saying "-"
            # beside a reason saying "0%" is the dashboard arguing with the daemon.
            shown, src = st.shown_short()
            five = f"{cell(shown)}{src}"
            print(f"{a:<{w}}{acct or '-':<10}{five:>5}{cell(st.ctx_pct):>5}  "
                  f"{'YES' if v['fired'] else 'no ':<6} {textsafe.oneline(v['reason'])}")
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
            # every decision. `hl` used to filter them on screen; this is
            # where someone asking what the daemon did actually looks.
            rows = [r for r in rows if r["kind"] not in events.TURN_BOUNDARIES | events.AUDIT_KINDS]
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
