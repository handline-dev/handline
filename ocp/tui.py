"""ocp top - the full-screen dashboard (design section 11).

A read-only join view with no logic of its own: everything drawn is read from
the same state the daemon decides on, so the two cannot disagree. If a number
looks wrong here, the daemon is acting on that same wrong number.

Two drawing rules are deliberate. Every figure carries a mark for where it came
from - inferred, carried, or carried past its bound - so an inference is never
drawn as a measurement, and what we genuinely do not know is drawn as "?" and
never as 0. And an account's usage and an agent's context come from different
files - rate limits belong to the account, context to the session - so they are
shown as what they are rather than blended into one number.

It runs on the alternate screen, so quitting leaves the scrollback as it was.
"""
from __future__ import annotations

import os
import re
import select
import shutil
import sys
import termios
import time
import tty

from . import agents, daemon, db, paths, status, triggers

ALT_ON, ALT_OFF = "\033[?1049h", "\033[?1049l"
CUR_OFF, CUR_ON = "\033[?25l", "\033[?25h"
HOME = "\033[H"
CLEAR_EOL = "\033[K"

R = "\033[0m"
DIM, BOLD = "\033[2m", "\033[1m"
RED, YEL, GRN, CYA, BLU = ("\033[31m", "\033[33m", "\033[32m", "\033[36m", "\033[34m")

_ANSI = re.compile(r"\033\[[0-9;?]*[a-zA-Z]")

STATE_COLOR = {
    "RUNNING": GRN, "QUEUED": CYA, "IDLE": DIM, "DONE": DIM,
    "HANDOFF": YEL, "PARKED": YEL, "THROTTLED": YEL,
    "DEGRADED": RED, "STALLED": RED,
}


def vlen(s: str) -> int:
    """Visible width, ignoring the escape sequences."""
    return len(_ANSI.sub("", s))


def fit(s: str, width: int) -> str:
    """Truncate to a visible width without cutting an escape sequence in half."""
    if vlen(s) <= width:
        return s
    out, seen = [], 0
    i = 0
    while i < len(s):
        m = _ANSI.match(s, i)
        if m:
            out.append(m.group())
            i = m.end()
            continue
        if seen >= width - 1:
            break
        out.append(s[i])
        seen += 1
        i += 1
    return "".join(out) + "…" + R


def pad(s: str, width: int) -> str:
    return s + " " * max(0, width - vlen(s))


def bar(pct: int | None, width: int = 10) -> str:
    if pct is None:
        return DIM + "·" * width + R
    filled = max(0, min(width, round(pct / 100 * width)))
    color = RED if pct >= 85 else (YEL if pct >= 60 else GRN)
    return color + "█" * filled + DIM + "░" * (width - filled) + R


def pct(v: int | None) -> str:
    return "  ?" if v is None else f"{v:>3}"


def countdown(ts: int | None) -> str:
    if not ts:
        return "   -"
    left = ts - int(time.time())
    if left <= 0:
        return " due"
    if left >= 3600:
        return f"{left // 3600}h{(left % 3600) // 60:02d}"
    return f"{left // 60:>2}m{left % 60:02d}"


def age(secs: float | None) -> str:
    if secs is None:
        return "?"
    if secs < 90:
        return f"{int(secs)}s"
    if secs < 5400:
        return f"{int(secs / 60)}m"
    return f"{int(secs / 3600)}h"


class Panel:
    """A titled box that draws exactly the rows it is given."""

    def __init__(self, title: str, width: int):
        self.title, self.width = title, width
        self.rows: list[str] = []

    def add(self, row: str = "") -> None:
        self.rows.append(row)

    def draw(self, limit: int | None = None) -> list[str]:
        inner = self.width - 2
        head = f"{DIM}┌─{R} {BOLD}{self.title}{R} "
        # Pad with the rule to exactly `width` visible columns. Mixing vlen()
        # with len() of an escape sequence is how this ended up one character
        # too long and truncating its own title.
        head += DIM + "─" * max(0, self.width - vlen(head) - 1) + "┐" + R
        out = [head]
        body = self.rows if limit is None else self.rows[:limit]
        for row in body:
            out.append(f"{DIM}│{R}" + pad(fit(row, inner), inner) + f"{DIM}│{R}")
        if limit is not None and len(self.rows) > limit:
            more = f"{DIM}  … {len(self.rows) - limit} more{R}"
            out[-1] = f"{DIM}│{R}" + pad(fit(more, inner), inner) + f"{DIM}│{R}"
        out.append(DIM + "└" + "─" * inner + "┘" + R)
        return out


def build(width: int) -> tuple[list[str], list[list[str]]]:
    """Return (fixed header lines, panels-as-line-lists) for one frame."""
    con = db.connect()
    try:
        pid = daemon.running_elsewhere()
        state = f"{GRN}running{R} pid {pid}" if pid else f"{RED}NOT RUNNING{R}"
        # The legend earns its place only if it fits whole. Truncated, it cuts
        # the last marks off entirely and glosses the first one mid-word, which
        # is worse than not drawing it - and every mark it explains is also
        # explained by `ocp status`.
        bar_ = (f"{BOLD}ocp{R} {DIM}·{R} daemon {state} {DIM}·{R} "
                f"{time.strftime('%H:%M:%S')}  {DIM}q quit · r refresh{R}")
        with_legend = f"{bar_}{DIM} · 5h {status.SOURCE_LEGEND_SHORT}{R}"
        header = [
            fit(with_legend if vlen(with_legend) <= width else bar_, width),
            "",
        ]

        # ---- accounts ------------------------------------------------
        acc = Panel("ACCOUNTS", width)
        acc.add(f"{DIM}{'account':<10}{'5h usage':<22}{'reset':<7}"
                f"{'7d':<16}{'snapshot':<10}{'cost':<9}assign{R}")
        for name in paths.list_accounts():
            st = status.read(name)
            allowed, why = st.assignable()
            mark = f"{GRN}✔{R}" if allowed else f"{RED}✘{R}"
            cost = "?" if st.cost_usd is None else f"${st.cost_usd:.2f}"
            # Show the figure decisions are made on, with a mark saying where
            # it came from. Drawing a quiet window as "?" while the daemon acts
            # on 0% is the dashboard and the daemon disagreeing.
            five = st.trusted_pct_5h
            src = status.mark(st.pct_5h_source)
            acc.add(
                f"{BOLD}{name:<10}{R}{bar(five)} {pct(five)}%{src} "
                f"{countdown(st.reset_5h):<7}{bar(st.pct_7d, 6)} {pct(st.pct_7d)}%  "
                f"{age(st.age_s):<10}{cost:<9}{mark} {DIM}{why[:30]}{R}"
            )
        if not paths.list_accounts():
            acc.add(f"{DIM}  no accounts registered{R}")

        # ---- agents --------------------------------------------------
        ag = Panel("AGENTS", width)
        ag.add(f"{DIM}{'agent':<11}{'acct':<8}{'model':<8}{'eff':<7}{'state':<11}"
               f"{'ctx':<7}{'wake':<7}{'in':<4}trigger{R}")
        for name in paths.list_agents():
            try:
                acct = agents.account_of(name)
                cfg = agents.read(name)
            except Exception as e:  # noqa: BLE001
                ag.add(f"{name:<11}{RED}config error: {e}{R}")
                continue
            st = status.read_for_agent(name, acct)
            row = db.get_agent_state(con, name)
            s = row["state"] if row else "IDLE"
            t = triggers.evaluate(st.trusted_pct_5h, st.trusted_ctx_pct)
            fire = f"{YEL}⚠ {t.kind}{R}" if t.fired else f"{DIM}—{R}"
            ctxs = f"{pct(st.ctx_pct)}%"
            if st.ctx_pct is not None and st.ctx_pct >= triggers.CONTEXT_PCT:
                ctxs = YEL + ctxs + R
            inbox = len(db.queued_for(con, name))
            ag.add(
                f"{BOLD}{name:<11}{R}{acct:<8}{str(cfg.get('model')):<8}"
                f"{str(cfg.get('effort') or agents.DEFAULT_EFFORT):<7}"
                f"{STATE_COLOR.get(s, '')}{s:<11}{R}{ctxs:<7}"
                f"{countdown(row['wake_at'] if row else None):<7}"
                f"{(str(inbox) if inbox else '·'):<4}{fire}"
            )
            if row and row["detail"]:
                ag.add(f"{DIM}{'':<11}{row['detail']}{R}")
        if not paths.list_agents():
            ag.add(f"{DIM}  no agents registered{R}")

        # ---- in flight -----------------------------------------------
        fl = Panel("IN FLIGHT", width)
        flight = db.in_flight_handoffs(con)
        queued = con.execute(
            "SELECT id, requester, target, body FROM requests WHERE state='queued' ORDER BY id"
        ).fetchall()
        for h in flight:
            fl.add(f"{YEL}handoff{R} {h['agent']:<11}step {h['step']:<12}"
                   f"{DIM}deadline {countdown(h['deadline'])}{R}")
        for q in queued:
            fl.add(f"{CYA}request{R} #{q['id']:<5}{q['requester']} → {q['target']:<12}"
                   f"{DIM}{q['body'].splitlines()[0] if q['body'] else ''}{R}")
        if not flight and not queued:
            fl.add(f"{DIM}  nothing queued, no handoff in progress{R}")

        # ---- recent --------------------------------------------------
        rc = Panel("RECENT", width)
        for e in con.execute(
            "SELECT ts, agent, kind, payload FROM events ORDER BY id DESC LIMIT 40"
        ).fetchall():
            when = time.strftime("%H:%M:%S", time.localtime(e["ts"]))
            rc.add(f"{DIM}{when}{R}  {e['agent']:<11}{e['kind']:<18}"
                   f"{DIM}{(e['payload'] or '')[:width - 46]}{R}")
        if not rc.rows:
            rc.add(f"{DIM}  no events yet{R}")
        return header, [acc, ag, fl, rc]
    finally:
        con.close()


def frame(width: int, height: int) -> str:
    header, panels = build(width)
    # Everything but RECENT gets the room it needs; RECENT takes what is left,
    # so the frame always fills the screen exactly and never scrolls it.
    fixed = list(header)
    for p in panels[:-1]:
        fixed += p.draw()
    room = height - len(fixed) - 1
    recent = panels[-1].draw(limit=max(1, room - 2)) if room > 3 else []
    lines = fixed + recent
    lines = lines[:height]
    while len(lines) < height:
        lines.append("")
    return "\n".join(line + CLEAR_EOL for line in lines)


def _key(timeout: float) -> str | None:
    if not sys.stdin.isatty():
        time.sleep(timeout)
        return None
    if select.select([sys.stdin], [], [], timeout)[0]:
        return sys.stdin.read(1)
    return None


def run(interval: float = 2.0, once: bool = False) -> int:
    if once or not sys.stdout.isatty():
        size = shutil.get_terminal_size((100, 40))
        print(frame(min(size.columns, 200), size.lines))
        return 0

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd) if sys.stdin.isatty() else None
    try:
        if saved:
            tty.setcbreak(fd)
        sys.stdout.write(ALT_ON + CUR_OFF)
        while True:
            size = shutil.get_terminal_size((100, 40))
            sys.stdout.write(HOME + frame(size.columns, size.lines))
            sys.stdout.flush()
            k = _key(interval)
            if k in ("q", "Q", "\x03"):
                return 0
    except KeyboardInterrupt:
        return 0
    finally:
        sys.stdout.write(CUR_ON + ALT_OFF)
        sys.stdout.flush()
        if saved:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)
