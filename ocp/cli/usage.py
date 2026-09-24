"""`ocp usage` and `ocp collect`."""
from __future__ import annotations

import time

from .. import collect
from .. import timefmt
from .. import db
from .. import observe as obs
from .. import paths
from .. import spawn as spawnmod
from .. import status
from .. import usage_api


# ---------------------------------------------------------------- usage

def cmd_usage(args) -> int:
    """What the usage endpoint says, beside what the snapshot says.

    Read-only, and it decides nothing: this exists so the two can be compared
    before anything is wired to prefer one. An account with a session running
    is asked without a renewal - Claude Code is already renewing its token, and
    two writers would race.
    """
    live = set()
    for _agent, pid in (obs.live_sessions() or {}).items():
        acct = obs.account_of_process(pid)
        if acct:
            live.add(acct)
    known = paths.list_accounts()
    if args.account and args.account not in known:
        print(f"no such account: {args.account} (have: {', '.join(known) or 'none'})")
        return 1
    accts = [args.account] if args.account else known
    print(f"{'account':10} {'':4} {'5h':>5} {'7d':>5}  {'7d resets':16}  note")
    for acct in accts:
        st = status.read(acct)
        if st.unmetered:
            print(f"{acct:10} {'':4} unmetered")
            continue
        age = timefmt.span(time.time() - st.collected_at) if st.collected_at else "never"
        trusted5 = str(st.trusted_pct_5h) if st.trusted_pct_5h is not None else "?"
        trusted7 = str(st.trusted_pct_7d) if st.trusted_pct_7d is not None else "?"
        print(f"{acct:10} {'ocp':>4} {trusted5:>5} {trusted7:>5}  "
              f"{timefmt.stamp(st.reset_7d, timefmt.SHORT, '-'):16}  snapshot {age} old"
              f" ({st.pct_5h_source}/{st.pct_7d_source})")
        t0 = time.time()
        r = usage_api.read(acct, refresh=acct not in live)
        if r is None:
            print(f"{'':10} {'api':>4} {'could not ask':>11}")
            continue
        flags = []
        if r.pct_5h != st.pct_5h:
            flags.append(f"5h differs ({st.pct_5h} -> {r.pct_5h})")
        if r.pct_7d != st.pct_7d:
            flags.append(f"7d differs ({st.pct_7d} -> {r.pct_7d})")
        if acct in live:
            flags.append("no renewal - session live")
        print(f"{'':10} {'api':>4} {str(r.pct_5h):>5} {str(r.pct_7d):>5}  "
              f"{timefmt.stamp(r.reset_7d, timefmt.SHORT, '-'):16}  {(time.time()-t0)*1000:.0f}ms"
              + ("  " + "; ".join(flags) if flags else ""))
    return 0


# ---------------------------------------------------------------- collect

def cmd_collect(args) -> int:
    """What the transcript says about each agent's session, beside the snapshot.

    The daemon does this every tick for every agent whose session is open
    (`daemon.collect_snapshots`). By hand it is a comparison: `differs` names
    every field where the pull and the file on disk disagree, which is how the
    two mechanisms were checked against each other over the fleet (D11).
    """
    con = db.connect()
    try:
        rows = {r["agent"]: r for r in db.all_agent_states(con)}
    finally:
        con.close()
    names = [args.agent] if args.agent else paths.list_agents()
    print(f"{'agent':22}{'account':9}{'session':10}{'ctx':>5}{'tokens':>9}"
          f"{'age':>7}  differs")
    for agent in names:
        row = rows.get(agent)
        cwd = spawnmod.worktree_path(row["worktree"] if row else None)
        if not cwd:
            print(f"{agent:22}(no worktree recorded)")
            continue
        snap = collect.snapshot(agent, cwd)
        if snap is None:
            print(f"{agent:22}(no transcript under {cwd})")
            continue
        if args.write:
            collect.write(agent, snap)
        have = status.read("", paths.agent_status_file(agent)).raw
        differs = [k for k in ("account", "session_id", "ctx_pct")
                   if have and have.get(k) != snap.get(k)]
        print(f"{agent:22}{snap['account']:9}{snap['session_id'][:8]:10}"
              f"{str(snap['ctx_pct']):>5}{collect.tokens(snap['ctx_used']):>9}"
              f"{timefmt.ago(time.time() - snap['collected_at']):>7}  "
              f"{', '.join(differs) if differs else '-'}")
    return 0
