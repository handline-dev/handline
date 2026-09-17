"""ocp top - the screen the team is run from (design section 11).

A join view over the same state the daemon decides on, so the two cannot
disagree: if a number looks wrong here, the daemon is acting on that same wrong
number. It draws; it does not decide. What a key does is a call into `actions`,
which calls the same function the daemon or the CLI calls - so the screen can
act without ever holding an opinion of its own.

Two drawing rules are deliberate. Every figure carries a mark for where it came
from - inferred, carried, or carried past its bound - so an inference is never
drawn as a measurement, and what we genuinely do not know is drawn as "?" and
never as 0. The one unmarked case is a figure held at the window's high-water
mark: a real number reported by an earlier payload than the one on screen. And
an account's usage and an agent's context come from different files - rate
limits belong to the account, context to the session - so they are shown as
what they are rather than blended into one number.

It runs on the alternate screen, so quitting leaves the scrollback as it was -
and hands that screen back whole for anything a human has to type into, which
is why logging in works from here at all.
"""
from __future__ import annotations

import os
import re
import select
import shutil
import subprocess
import sys
import termios
import time
import tty

from . import actions, agents, daemon, db, paths, status, triggers

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

# The three panels a cursor can sit in, in tab order.
ACCOUNTS, AGENTS, MESSAGES = 0, 1, 2
PANEL_NAME = {ACCOUNTS: "accounts", AGENTS: "agents", MESSAGES: "messages"}

# Keys, per panel. Anything that cannot be undone asks first; the rest happen
# on the keypress, because a confirmation nobody reads is just a second key.
AGENT_KEYS = [
    ("↵", "open"), ("w", "wake"), ("c", "close"), ("p", "park"),
    ("f", "fold"), ("m", "message"), ("s", "start"), ("u", "unstall"),
]
ACCOUNT_KEYS = [("↵", "open"), ("l", "login")]
MESSAGE_KEYS = [("↵", "read"), ("a", "answer"), ("x", "dismiss")]
CONFIRM = {"close", "park", "unstall", "fold"}


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
    """A titled box that draws exactly the rows it is given.

    Rows carry a key when they can be selected - an agent's name, a request's
    id - and the cursor is drawn in a gutter every row shares, so selecting
    something never moves the columns.
    """

    def __init__(self, title: str, width: int):
        self.title, self.width = title, width
        self.rows: list[str] = []
        self.keys: list[object] = []
        self.selected: object = None

    def add(self, row: str = "", key: object = None) -> None:
        self.rows.append(row)
        self.keys.append(key)

    def selectable(self) -> list[object]:
        return [k for k in self.keys if k is not None]

    def draw(self, limit: int | None = None) -> list[str]:
        inner = self.width - 2
        # The title is data now - it counts what is waiting - so it is fitted
        # like any other row. A head wider than the terminal wraps, and a
        # wrapped line is one row more than the frame promised, which is the
        # one thing that scrolls the alternate screen.
        head = f"{DIM}┌─{R} {BOLD}{fit(self.title, max(0, self.width - 6))}{R} "
        # Pad with the rule to exactly `width` visible columns. Mixing vlen()
        # with len() of an escape sequence is how this ended up one character
        # too long and truncating its own title.
        head += DIM + "─" * max(0, self.width - vlen(head) - 1) + "┐" + R
        out = [head]
        body = list(zip(self.rows, self.keys))
        if limit is not None:
            body = body[:limit]
        for row, key in body:
            gutter = f"{CYA}▸{R} " if key is not None and key == self.selected else "  "
            out.append(f"{DIM}│{R}" + pad(fit(gutter + row, inner), inner) + f"{DIM}│{R}")
        if limit is not None and len(self.rows) > limit:
            more = f"{DIM}  … {len(self.rows) - limit} more{R}"
            out[-1] = f"{DIM}│{R}" + pad(fit(more, inner), inner) + f"{DIM}│{R}"
        out.append(DIM + "└" + "─" * inner + "┘" + R)
        return out


def keybar(panel: int, width: int) -> str:
    """The keys this panel answers to, in as much room as there is.

    Half an action's name is worse than no name: `u u…` tells a reader nothing
    and looks like a bug. So the line sheds whole pieces - the navigation
    first, since it is the same on every panel, then keys from the end - and
    says how many it dropped.
    """
    keys = {ACCOUNTS: ACCOUNT_KEYS, AGENTS: AGENT_KEYS, MESSAGES: MESSAGE_KEYS}[panel]
    head = f"{DIM}{PANEL_NAME[panel]}:{R} "
    nav = f" {DIM}· tab panel · ↑↓ move · q quit{R}"
    sep = f" {DIM}·{R} "
    parts = [f"{BOLD}{k}{R} {DIM}{label}{R}" for k, label in keys]
    full = head + sep.join(parts)
    if vlen(full + nav) <= width:
        return full + nav
    if vlen(full) <= width:
        return full          # the navigation goes first: it is the same everywhere
    for n in range(len(parts) - 1, 0, -1):
        line = head + sep.join(parts[:n]) + f" {DIM}+{len(parts) - n} more{R}"
        if vlen(line) <= width:
            return line
    return fit(head, width)


def build(width: int, sel: dict | None = None) -> tuple[list[str], list[Panel]]:
    """Return (header lines, panels) for one frame.

    `sel` is {panel: key}: which row carries the cursor in each panel, so the
    caller can keep a cursor across redraws without this knowing how it moves.
    """
    sel = sel or {}
    con = db.connect()
    try:
        pid = daemon.running_elsewhere()
        state = f"{GRN}running{R} pid {pid}" if pid else f"{RED}NOT RUNNING{R}"
        # The legend earns its place only if it fits whole. Truncated, it cuts
        # the last marks off entirely and glosses the first one mid-word, which
        # is worse than not drawing it - and every mark it explains is also
        # explained by `ocp status`.
        bar_ = (f"{BOLD}ocp{R} {DIM}·{R} daemon {state} {DIM}·{R} "
                f"{time.strftime('%H:%M:%S')}")
        with_legend = f"{bar_}{DIM} · 5h {status.SOURCE_LEGEND_SHORT}{R}"
        header = [
            fit(with_legend if vlen(with_legend) <= width else bar_, width),
            "",
        ]

        # ---- accounts ------------------------------------------------
        acc = Panel("ACCOUNTS", width)
        acc.selected = sel.get(ACCOUNTS)
        acc.add(f"{DIM}{'account':<10}{'5h usage':<18}{'reset':<8}"
                f"{'7d':<13}{'snap':<7}{'live $':<9}assign{R}")
        for name in paths.list_accounts():
            st = status.read(name)
            allowed, why = st.assignable()
            mark = f"{GRN}✔{R}" if allowed else f"{RED}✘{R}"
            live = status.live_cost(name)
            cost = "?" if live is None else f"${live:.2f}"
            # Show the figure decisions are made on, with a mark saying where
            # it came from. Drawing a quiet window as "?" while the daemon acts
            # on 0% is the dashboard and the daemon disagreeing.
            five = st.trusted_pct_5h
            src = status.mark(st.pct_5h_source)
            # Every cell is padded on its visible width: the bars carry escape
            # sequences, so a plain :<n counts them and the columns drift out
            # from under their own headings.
            acc.add(
                pad(f"{BOLD}{name}{R}", 10)
                + pad(f"{bar(five)} {pct(five)}%{src}", 18)
                + pad(countdown(st.reset_5h), 8)
                + pad(f"{bar(st.pct_7d, 6)} {pct(st.pct_7d)}%", 13)
                + pad(age(st.age_s), 7)
                + pad(cost, 9)
                + f"{mark} {DIM}{why[:32]}{R}",
                key=name,
            )
        if not paths.list_accounts():
            acc.add(f"{DIM}  no accounts registered{R}")

        # ---- agents --------------------------------------------------
        ag = Panel("AGENTS", width)
        ag.selected = sel.get(AGENTS)
        ag.add(f"{DIM}{'agent':<11}{'acct':<8}{'model':<8}{'eff':<7}{'state':<15}"
               f"{'ctx':>4}   {'wake':<7}{'in':<4}trigger{R}")
        for name in paths.list_agents():
            try:
                acct = agents.account_of(name)
                cfg = agents.read(name)
            except Exception as e:  # noqa: BLE001
                ag.add(f"{name:<11}{RED}config error: {e}{R}", key=name)
                continue
            st = status.read_for_agent(name, acct)
            row = db.get_agent_state(con, name)
            s = row["state"] if row else "IDLE"
            # A closed session holds no memory and answers no one; showing it
            # as PARKED hides the one difference that matters on this machine.
            if row and row["closed_at"]:
                s += "·closed"
            t = triggers.evaluate(st.trusted_pct_5h, st.trusted_ctx_pct)
            if not daemon.triggers_apply(row):
                fire = f"{DIM}— closed{R}"
            else:
                fire = f"{YEL}⚠ {t.kind}{R}" if t.fired else f"{DIM}—{R}"
            ctxs = f"{pct(st.ctx_pct)}%"
            if st.ctx_pct is not None and st.ctx_pct >= triggers.CONTEXT_PCT:
                ctxs = YEL + ctxs + R
            inbox = len(db.queued_for(con, name))
            # Padded on visible width, like the accounts panel: a state that
            # grew a "·closed" suffix used to push every column after it right.
            ag.add(
                pad(f"{BOLD}{name}{R}", 11)
                + pad(acct, 8) + pad(str(cfg.get("model")), 8)
                + pad(str(cfg.get("effort") or agents.DEFAULT_EFFORT), 7)
                + pad(f"{STATE_COLOR.get(s, '')}{fit(s, 14)}{R}", 15)
                + pad(ctxs, 7)
                + pad(countdown(row["wake_at"] if row else None), 7)
                + pad(str(inbox) if inbox else "·", 4)
                + fire,
                key=name,
            )
            if row and row["detail"]:
                ag.add(f"{DIM}{'':<11}{row['detail']}{R}")
        if not paths.list_agents():
            ag.add(f"{DIM}  no agents registered{R}")

        # ---- messages ------------------------------------------------
        # What is waiting for the human, which is the one queue nothing else in
        # this system will ever clear: an agent's escalation sits here until it
        # is answered, and it used to be visible only by running `ocp inbox`
        # with the right name.
        waiting = actions.messages(con)
        gates = sum(1 for m in waiting if m["gate"])
        parked = sum(1 for m in waiting if m["waiting"] and not m["gate"])
        title = "FOR YOU"
        if waiting:
            title += (f" — {gates} to decide, {parked} asked for a reply, "
                      f"{len(waiting) - gates - parked} to read")
        ms = Panel(title, width)
        ms.selected = sel.get(MESSAGES)
        for m in waiting:
            when = time.strftime("%H:%M", time.localtime(m["ts"]))
            first = (m["body"] or "").splitlines()[0] if m["body"] else ""
            # An agent parked until this is answered is the only thing on the
            # screen that no one else will ever clear; it is not drawn like a
            # report that happens to be addressed here.
            if m["gate"]:
                mark, name = f"{YEL}⚠{R}", f"{BOLD}{m['requester']:<12}{R}"
            elif m["waiting"]:
                mark, name = f"{CYA}◷{R}", f"{BOLD}{m['requester']:<12}{R}"
            else:
                mark, name = f"{DIM}·{R}", f"{DIM}{m['requester']:<12}{R}"
            ms.add(f"{DIM}{when}{R} {mark} {CYA}#{m['id']:<5}{R}{name}"
                   f"{pad(m['kind'] or 'ask', 11)}{first}", key=m["id"])
        if not waiting:
            ms.add(f"{DIM}  nothing waiting on you{R}")

        # ---- in flight -----------------------------------------------
        fl = Panel("IN FLIGHT", width)
        flight = db.in_flight_handoffs(con)
        queued = con.execute(
            "SELECT id, requester, target, body FROM requests "
            "WHERE state='queued' AND target != ? ORDER BY id", (actions.HUMAN,)
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
        return header, [acc, ag, ms, fl, rc]
    finally:
        con.close()


def frame(width: int, height: int, sel: dict | None = None, panel: int = AGENTS,
          flash: str = "", detail: list[str] | None = None) -> str:
    if detail is not None:
        return _detail_frame(width, height, detail)
    header, panels = build(width, sel)
    acc, ms, ag, fl, rc = panels[0], panels[2], panels[1], panels[3], panels[4]
    foot = [fit(flash, width) if flash else "", keybar(panel, width)]
    # ACCOUNTS and AGENTS are the whole team and are always drawn whole. FOR
    # YOU is the one that grows without bound - nothing clears it but a human -
    # so it is the one that gives up rows, and it says how many it is holding
    # back. Otherwise it pushed IN FLIGHT off a short screen silently.
    fixed = list(header) + acc.draw() + ag.draw()
    room = height - len(fixed) - len(foot)
    keep = len(fl.rows) + 2 + 3                # in flight, and a line of recent
    limit = None if len(ms.rows) + 2 <= room - keep else max(1, room - keep - 2)
    middle = ms.draw(limit=limit) + fl.draw()
    room = height - len(fixed) - len(middle) - len(foot)
    recent = rc.draw(limit=max(1, room - 2)) if room > 3 else []
    lines = fixed + middle + recent
    lines = lines[:max(0, height - len(foot))]
    while len(lines) < height - len(foot):
        lines.append("")
    lines += foot
    lines = lines[:height]
    return "\n".join(line + CLEAR_EOL for line in lines)


def _detail_frame(width: int, height: int, detail: list[str]) -> str:
    lines = [fit(ln, width) for ln in detail][:height - 2]
    while len(lines) < height - 2:
        lines.append("")
    lines.append("")
    lines.append(f"{DIM}esc back · q quit{R}")
    return "\n".join(line + CLEAR_EOL for line in lines[:height])


def move(keys: list, current, delta: int):
    """The next key in a panel's list, staying inside it."""
    if not keys:
        return None
    if current not in keys:
        return keys[0]
    return keys[max(0, min(len(keys) - 1, keys.index(current) + delta))]


# Keys read ahead of the one being handled. One read can return several - a
# held-down arrow, or two keys typed inside a frame - and dropping the rest
# loses keypresses the human made.
_pending: list[str] = []


def split_keys(data: str) -> list[str]:
    """One read of the terminal, split into the keys it contains.

    An arrow is three bytes, `esc [ A`, or `esc O A` where the terminal has
    switched to application cursor keys; a bare escape is one byte and means
    the human backed out.
    """
    out, i = [], 0
    while i < len(data):
        if data[i] == "\033" and i + 2 < len(data) and data[i + 1] in "[O":
            out.append(data[i:i + 3])
            i += 3
        else:
            out.append(data[i])
            i += 1
    return out


def _key(timeout: float) -> str | None:
    if not sys.stdin.isatty():
        time.sleep(timeout)
        return None
    if _pending:
        return _pending.pop(0)
    fd = sys.stdin.fileno()
    if not select.select([fd], [], [], timeout)[0]:
        return None
    # Read the descriptor, not the buffered stream. `sys.stdin.read(1)` pulls
    # the whole escape sequence into Python's own buffer, and then select()
    # reports the fd has nothing left - so every arrow key arrived here as a
    # bare escape with its `[B` still sitting in the buffer.
    try:
        data = os.read(fd, 64).decode(errors="ignore")
    except OSError:
        return None
    if not data:
        return None
    keys = split_keys(data)
    _pending.extend(keys[1:])
    return keys[0]


def _line(prompt: str, width: int, height: int) -> str | None:
    """Read one line with the screen still up. None when the human backs out."""
    if not sys.stdin.isatty():
        return None
    buf = ""
    while True:
        sys.stdout.write(f"\033[{height};1H" + CLEAR_EOL
                         + fit(f"{BOLD}{prompt}{R} {buf}▏", width))
        sys.stdout.flush()
        ch = _key(30.0)
        if ch is None:
            continue
        if ch in ("\r", "\n"):
            return buf
        if ch == "\033":
            return None
        if ch in ("\x7f", "\b"):
            buf = buf[:-1]
            continue
        if ch == "\x03":
            return None
        if ch.isprintable():
            buf += ch


def _confirm(what: str, width: int, height: int) -> bool:
    if not sys.stdin.isatty():
        return False
    sys.stdout.write(f"\033[{height};1H" + CLEAR_EOL
                     + fit(f"{YEL}{what}?{R} {DIM}y / n{R}", width))
    sys.stdout.flush()
    while True:
        ch = _key(30.0)
        if ch is None:
            continue
        return ch in ("y", "Y")


def _suspend(argv: list[str], env: dict, fd, saved) -> str:
    """Hand the whole terminal to another program, then take it back.

    Logging in is a human typing a code into claude's own screen: nothing about
    that can happen behind an alternate screen with the cursor hidden.
    """
    sys.stdout.write(CUR_ON + ALT_OFF)
    sys.stdout.flush()
    if saved:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    try:
        r = subprocess.run(argv, env=env)
        out = f"{argv[0]} exited {r.returncode}"
    except (OSError, subprocess.SubprocessError) as e:
        out = f"could not run {argv[0]}: {e}"
    finally:
        if saved:
            tty.setcbreak(fd)
        sys.stdout.write(ALT_ON + CUR_OFF)
        sys.stdout.flush()
    return out


def run(interval: float = 2.0, once: bool = False) -> int:
    if once or not sys.stdout.isatty():
        size = shutil.get_terminal_size((100, 40))
        print(frame(min(size.columns, 200), size.lines))
        return 0

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd) if sys.stdin.isatty() else None
    panel, sel, flash, detail = AGENTS, {}, "", None
    try:
        if saved:
            tty.setcbreak(fd)
        sys.stdout.write(ALT_ON + CUR_OFF)
        while True:
            size = shutil.get_terminal_size((100, 40))
            width, height = size.columns, size.lines
            sys.stdout.write(HOME + frame(width, height, sel, panel, flash, detail))
            sys.stdout.flush()
            k = _key(interval)
            if k is None:
                continue
            if k in ("q", "Q", "\x03"):
                return 0
            if detail is not None:
                # One screen at a time: while something is open, the only
                # questions are "close it" and "quit".
                if k in ("\033", "\x1b"):
                    detail = None
                continue
            flash = ""
            if k == "\t":
                panel = (panel + 1) % 3
                continue
            if k in ("r", "R"):
                continue
            if k in ("j", "\033[B", "\033OB"):
                sel[panel] = move(_keys_of(panel), sel.get(panel), 1)
                continue
            if k in ("k", "\033[A", "\033OA"):
                sel[panel] = move(_keys_of(panel), sel.get(panel), -1)
                continue
            try:
                flash, detail = _act(panel, sel, k, width, height, fd, saved)
            except Exception as e:  # noqa: BLE001
                # One action failing is that action's failure, not the end of
                # the screen - the daemon wraps each agent for the same reason.
                flash = f"{RED}{type(e).__name__}: {e}{R}"
    except KeyboardInterrupt:
        return 0
    finally:
        sys.stdout.write(CUR_ON + ALT_OFF)
        sys.stdout.flush()
        if saved:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def _say(what: str, width: int, height: int) -> None:
    """Put one line on the bottom row now, without waiting for a redraw.

    An action can take half a minute - waking waits for a session to appear,
    parking pays for a fold - and a screen still showing the frame from before
    the keypress, or the confirmation that was just answered, reads as a hang.
    The next redraw repaints every row, so this cannot outlive its action.
    """
    sys.stdout.write(f"\033[{height};1H" + CLEAR_EOL + fit(f"{DIM}{what}…{R}", width))
    sys.stdout.flush()


def _keys_of(panel: int) -> list:
    """What the cursor can sit on in a panel, read fresh each time.

    The list changes under the cursor - an agent is registered, a message is
    answered - so it is asked for rather than remembered.
    """
    if panel == ACCOUNTS:
        return paths.list_accounts()
    if panel == AGENTS:
        return paths.list_agents()
    con = db.connect()
    try:
        return [m["id"] for m in actions.messages(con)]
    finally:
        con.close()


def _act(panel: int, sel: dict, key: str, width: int, height: int,
         fd, saved) -> tuple[str, list[str] | None]:
    """One keypress on the selected row. Returns (line to show, detail or None)."""
    keys = _keys_of(panel)
    target = sel.get(panel)
    if not keys:
        return "nothing to act on", None
    if target not in keys:
        # The row under the cursor is gone - a message answered elsewhere, an
        # agent that went away - and nothing is drawn as selected. Acting on
        # the first row would act on something the human never pointed at: it
        # dismissed an unread message that way.
        sel[panel] = keys[0]
        return "selection moved - press again", None

    if panel == ACCOUNTS and key in ("\r", "\n"):
        con = db.connect()
        try:
            return "", actions.account_detail(con, str(target))
        finally:
            con.close()
    if panel == ACCOUNTS and key == "l":
        line, argv, env = actions.login_argv(str(target))
        if not argv:
            return line, None
        return _suspend(argv, env, fd, saved), None

    con = db.connect()
    try:
        if panel == AGENTS:
            name = str(target)
            if key in ("\r", "\n"):
                return "", actions.agent_detail(con, name)
            label = dict(AGENT_KEYS).get(key)
            if not label:
                return "", None
            if not actions.can(con, name, label):
                return f"{name}: {label} does not apply right now", None
            if label == "start":
                repo = _line(f"repo for {name} [{os.getcwd()}]:", width, height)
                if repo is None:
                    return "not started", None
                _say(f"starting {name}", width, height)
                return actions.start(con, name, repo.strip() or os.getcwd()), None
            if label == "message":
                body = _line(f"message to {name}:", width, height)
                return (actions.message(con, name, body) if body is not None
                        else "nothing sent"), None
            if label in CONFIRM and not _confirm(f"{label} {name}", width, height):
                return "left alone", None
            _say(f"{label} {name}", width, height)
            return getattr(actions, label)(con, name), None

        if panel == MESSAGES:
            rid = int(target)
            row = con.execute("SELECT * FROM requests WHERE id = ?", (rid,)).fetchone()
            if not row:
                return f"#{rid} is gone", None
            if key in ("\r", "\n"):
                head = [f"{BOLD}#{rid} from {row['requester']}{R}  "
                        f"{row['kind'] or 'ask'}  "
                        f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(row['ts']))}", ""]
                return "", head + (row["body"] or "").splitlines()
            if key == "a":
                text = _line(f"answer #{rid}:", width, height)
                return (actions.answer(con, rid, text) if text is not None
                        else "nothing sent"), None
            if key == "x":
                return actions.dismiss(con, rid), None
        return "", None
    finally:
        con.close()
