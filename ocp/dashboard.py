"""ocp top - the read-only join view (design section 11).

No logic lives here. Everything shown is read from the same state the daemon
decides on, so the dashboard cannot disagree with the daemon: if a number looks
wrong here, the daemon is acting on that same wrong number.

Two things are deliberate. Unknown is drawn as "?" and never as 0, because the
whole point of fail-closed is that a missing reading is not a full tank. And an
account's own usage and an agent's context come from different files (rate
limits are account-wide, context is per session), so they are labelled as what
they are rather than blended.
"""
from __future__ import annotations

import os
import shutil
import time

from . import agents, daemon, db, paths, status, triggers

RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
RED = "\033[31m"
YELLOW = "\033[33m"
GREEN = "\033[32m"
CYAN = "\033[36m"

STATE_COLOR = {
    "RUNNING": GREEN, "QUEUED": CYAN, "IDLE": DIM,
    "HANDOFF": YELLOW, "PARKED": YELLOW, "THROTTLED": YELLOW,
    "DEGRADED": RED, "STALLED": RED, "DONE": DIM,
}


def _bar(pct: int | None, width: int = 5) -> str:
    if pct is None:
        return DIM + "?" * width + RESET
    filled = max(0, min(width, round(pct / 100 * width)))
    color = RED if pct >= 85 else (YELLOW if pct >= 60 else GREEN)
    return color + "█" * filled + DIM + "░" * (width - filled) + RESET


def _pct(v: int | None) -> str:
    return " ?%" if v is None else f"{v:>2}%"


def _countdown(ts: int | None) -> str:
    if not ts:
        return "  -  "
    left = ts - int(time.time())
    if left <= 0:
        return "due  "
    return f"{left // 3600}:{(left % 3600) // 60:02d}↻"


def _age(secs: float | None) -> str:
    if secs is None:
        return "?"
    if secs < 90:
        return f"{int(secs)}s"
    if secs < 5400:
        return f"{int(secs / 60)}m"
    return f"{int(secs / 3600)}h"


def render() -> str:
    width = min(shutil.get_terminal_size((100, 40)).columns, 108)
    out: list[str] = []
    con = db.connect()
    try:
        pid = daemon.running_elsewhere()
        head = f"ocp  {time.strftime('%H:%M:%S')}"
        head += f"  daemon pid {pid}" if pid else f"  {RED}daemon not running{RESET}"
        out.append(BOLD + head + RESET)
        out.append(DIM + "─" * width + RESET)

        # ---- accounts -------------------------------------------------
        out.append(BOLD + "ACCOUNTS" + RESET)
        for acct in paths.list_accounts():
            st = status.read(acct)
            allowed, why = st.assignable()
            mark = f"{GREEN}✔{RESET}" if allowed else f"{RED}✘{RESET}"
            out.append(
                f"  {acct:<8} 5h {_bar(st.pct_5h)} {_pct(st.pct_5h)} {_countdown(st.reset_5h)}"
                f"   7d {_bar(st.pct_7d)} {_pct(st.pct_7d)}"
                f"   {mark} {DIM}{why[:38]}{RESET}"
            )
            if st.exists:
                cost = "?" if st.cost_usd is None else f"${st.cost_usd:.2f}"
                out.append(f"  {DIM}{'':<8} snapshot {_age(st.age_s)} old · session cost {cost}{RESET}")
        out.append("")

        # ---- agents ---------------------------------------------------
        out.append(BOLD + "AGENTS" + RESET)
        out.append(DIM + f"  {'name':<11}{'acct':<7}{'model':<8}{'eff':<7}{'state':<11}"
                         f"{'ctx':<6}{'wake':<7}{'trigger'}" + RESET)
        for name in paths.list_agents():
            try:
                acct = agents.account_of(name)
                cfg = agents.read(name)
            except Exception as e:  # noqa: BLE001
                out.append(f"  {name:<11}{RED}config error: {e}{RESET}")
                continue
            st = status.read_for_agent(name, acct)
            row = db.get_agent_state(con, name)
            state = row["state"] if row else "IDLE"
            color = STATE_COLOR.get(state, "")
            t = triggers.evaluate(st.trusted_pct_5h, st.trusted_ctx_pct)
            fire = f"{YELLOW}⚠ {t.kind}{RESET}" if t.fired else f"{DIM}—{RESET}"
            ctx = _pct(st.ctx_pct)
            if st.ctx_pct is not None and st.ctx_pct >= triggers.CONTEXT_PCT:
                ctx = YELLOW + ctx + RESET
            out.append(
                f"  {name:<11}{acct:<7}{str(cfg.get('model')):<8}"
                f"{str(cfg.get('effort') or agents.DEFAULT_EFFORT):<7}"
                f"{color}{state:<11}{RESET}{ctx:<6} "
                f"{_countdown(row['wake_at'] if row else None):<7}{fire}"
            )
            if row and row["detail"]:
                out.append(f"  {DIM}{'':<11}{row['detail'][:width - 16]}{RESET}")
        out.append("")

        # ---- work in flight -------------------------------------------
        flight = db.in_flight_handoffs(con)
        queued = con.execute(
            "SELECT id, requester, target, body FROM requests WHERE state='queued' ORDER BY id"
        ).fetchall()
        out.append(BOLD + "IN FLIGHT" + RESET)
        if not flight and not queued:
            out.append(f"  {DIM}nothing queued, no handoff in progress{RESET}")
        for h in flight:
            out.append(f"  {YELLOW}handoff{RESET} {h['agent']:<11} step={h['step']:<11} "
                       f"{DIM}deadline {time.strftime('%H:%M:%S', time.localtime(h['deadline']))}{RESET}")
        for r in queued:
            out.append(f"  {CYAN}request{RESET} #{r['id']:<4} {r['requester']} → {r['target']:<11} "
                       f"{DIM}{r['body'][:width - 40]}{RESET}")
        out.append("")

        # ---- recent events --------------------------------------------
        rows = con.execute(
            "SELECT ts, agent, kind FROM events ORDER BY id DESC LIMIT 6"
        ).fetchall()
        out.append(BOLD + "RECENT" + RESET)
        for e in rows:
            when = time.strftime("%H:%M:%S", time.localtime(e["ts"]))
            out.append(f"  {DIM}{when}{RESET}  {e['agent']:<11}{e['kind']}")
        if not rows:
            out.append(f"  {DIM}no events yet{RESET}")
    finally:
        con.close()
    return "\n".join(out)


def top(interval: float = 3.0, once: bool = False) -> int:
    if once or not os.isatty(1):
        print(render())
        return 0
    try:
        print("\033[?25l", end="")  # hide cursor
        while True:
            # Redraw from the top rather than clearing, so the screen does not
            # flash between frames.
            print("\033[H\033[J" + render(), flush=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        return 0
    finally:
        print("\033[?25h", end="", flush=True)
