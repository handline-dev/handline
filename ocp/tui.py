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

import json
import os
import re
import select
import shlex
import shutil
import subprocess
import sys
import tempfile
import termios
import time
import tty
import unicodedata
from pathlib import Path

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

# The panels a cursor can sit in, in tab order. STUCK is one of them because a
# row there names the agent that is stuck, and the keys that unstick it are the
# agent keys - pressing them where the problem is read beats finding the same
# name again in the list above.
ACCOUNTS, AGENTS, MESSAGES, STUCK = 0, 1, 2, 3
PANELS = (ACCOUNTS, AGENTS, MESSAGES, STUCK)
PANEL_NAME = {ACCOUNTS: "accounts", AGENTS: "agents", MESSAGES: "messages",
              STUCK: "stuck"}

# Keys, per panel. Anything that cannot be undone asks first; the rest happen
# on the keypress, because a confirmation nobody reads is just a second key.
AGENT_KEYS = [
    ("↵", "open"), ("w", "wake"), ("c", "close"), ("p", "park"),
    ("f", "fold"), ("m", "message"), ("s", "start"), ("u", "unstall"),
]
ACCOUNT_KEYS = [("↵", "open"), ("l", "login")]
MESSAGE_KEYS = [("↵", "read"), ("a", "answer"), ("e", "answer in $EDITOR"),
                ("x", "dismiss")]
# A stuck row is about an agent, so it answers to the agent keys.
STUCK_KEYS = AGENT_KEYS
CONFIRM = {"close", "park", "unstall", "fold"}


def cwidth(ch: str) -> int:
    """How many columns one character takes.

    A Korean or Japanese glyph is drawn two columns wide, and counting it as
    one put every box border a column short per glyph: a handoff written in
    Korean tore the panel it was drawn in. Combining marks take none.
    """
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def vlen(s: str) -> int:
    """Visible width, ignoring the escape sequences."""
    return sum(cwidth(c) for c in _ANSI.sub("", s))


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
        w = cwidth(s[i])
        if seen + w > width - 1:
            break
        out.append(s[i])
        seen += w
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
        self.extra: list[bool] = []
        self.selected: object = None

    def add(self, row: str = "", key: object = None, extra: bool = False) -> None:
        """`extra` marks a row that says more about the one above it.

        The team is the one list that is never shortened - an agent missing
        from it reads as an agent that is not there - so when the screen is
        short it is these that go, and the row they belong to stays.
        """
        self.rows.append(row)
        self.keys.append(key)
        self.extra.append(extra)

    def draw(self, limit: int | None = None, extras: bool = True) -> list[str]:
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
        body = [(r, k) for r, k, x in zip(self.rows, self.keys, self.extra)
                if extras or not x]
        held_back = 0
        if limit is not None and len(body) > limit:
            held_back = len(body) - limit
            body = body[:limit]
        for row, key in body:
            gutter = f"{CYA}▸{R} " if key is not None and key == self.selected else "  "
            out.append(f"{DIM}│{R}" + pad(fit(gutter + row, inner), inner) + f"{DIM}│{R}")
        if held_back:
            more = f"{DIM}  … {held_back} more{R}"
            out[-1] = f"{DIM}│{R}" + pad(fit(more, inner), inner) + f"{DIM}│{R}"
        out.append(DIM + "└" + "─" * inner + "┘" + R)
        return out


# What a payload is worth saying out loud. The rest of it is ids and bookkeeping
# that answer no question a reader of this screen has.
_SAY = {"request_id": "#", "count": "×", "through": "through #", "state": "",
        "target": "→ ", "requester": "from ", "why": "", "handle": "",
        "ctx_pct": "ctx ", "events": "events ", "model": "", "account": "on "}


def _detail_of(payload: str | None) -> str:
    """One readable phrase out of an event's payload.

    It used to be printed as the raw JSON it is stored as, so half of every row
    was a session uuid: "wake {"resumed_session": "ccaa1bb9-6a12-4737-..."}".
    Nobody reads a uuid; what they want to know is that it resumed.
    """
    if not payload:
        return ""
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return str(payload)
    if not isinstance(data, dict):
        return str(data)
    out = []
    for k, v in data.items():
        if v in (None, "", [], {}):
            continue
        if k.endswith("session") or k == "session":
            out.append("resumed" if k.startswith("resumed") else "session")
            continue
        if k in _SAY:
            out.append(f"{_SAY[k]}{v}")
        else:
            out.append(f"{k} {v}")
    return " · ".join(out)


def keybar(panel: int, width: int, hidden: int = 0) -> str:
    """The keys this panel answers to, in as much room as there is.

    Half an action's name is worse than no name: `u u…` tells a reader nothing
    and looks like a bug. So the line sheds whole pieces - the navigation
    first, since it is the same on every panel, then keys from the end - and
    says how many it dropped.
    """
    keys = {ACCOUNTS: ACCOUNT_KEYS, AGENTS: AGENT_KEYS, MESSAGES: MESSAGE_KEYS,
            STUCK: STUCK_KEYS}[panel]
    # A panel that is not drawn and a panel with nothing in it look the same to
    # a reader, so that notice is part of the head and is never what gets
    # dropped to make room.
    note = f"{YEL}{hidden} panel(s) need a taller window{R} {DIM}·{R} " if hidden else ""
    head = f"{DIM}{PANEL_NAME[panel]}:{R} " + note
    nav = f" {DIM}· tab panel · ↑↓ move · v events · q quit{R}"
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


def build(width: int, sel: dict | None = None,
          verbose: bool = False) -> tuple[list[str], list[Panel]]:
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
        # The legend explains three marks. It was drawn whether or not any of
        # them was on the screen, which is most of the time.
        marks = {status.mark(status.read(a).pct_5h_source) for a in paths.list_accounts()}
        with_legend = f"{bar_}{DIM} · 5h {status.SOURCE_LEGEND_SHORT}{R}"
        show_legend = any(m.strip() for m in marks) and vlen(with_legend) <= width
        header = [fit(with_legend if show_legend else bar_, width), ""]

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
        # No heading for the last column: it is empty unless something is about
        # to happen, and then it says what, in words.
        ag.add(f"{DIM}{'agent':<11}{'acct':<8}{'model':<8}{'eff':<7}{'state':<15}"
               f"{'ctx':>4}   {'wake':<7}{'in':<4}{R}")
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
            # Only when something is actually about to happen. Measured on a
            # live team: nothing firing, six rows reading "— closed" - which
            # the state column says already, with the same word - and two
            # reading "—". A column that is empty or duplicated is a column
            # that trains a reader not to look at it.
            t = triggers.evaluate(st.trusted_pct_5h, st.trusted_ctx_pct)
            fire = (f"{YEL}⚠ {t.kind}{R}"
                    if t.fired and daemon.triggers_apply(row) else "")
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
                ag.add(f"{DIM}{'':<11}{row['detail']}{R}", extra=True)
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
        counts = [(gates, "to decide"), (parked, "asked for a reply"),
                  (len(waiting) - gates - parked, "to read")]
        said = ", ".join(f"{n} {what}" for n, what in counts if n)
        title = f"FOR YOU — {said}" if said else "FOR YOU"
        ms = Panel(title, width)
        ms.selected = sel.get(MESSAGES)
        for m in waiting:
            when = time.strftime("%H:%M", time.localtime(m["ts"]))
            # Same rule as the reading view: what a message says is its text.
            # An escape here is invisible to the width count, so it is never
            # truncated, and `esc [ 2J` clears the screen drawing it.
            first = _ANSI.sub("", (m["body"] or "").splitlines()[0] if m["body"] else "")
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

        # ---- stuck -----------------------------------------------------
        # This was "in flight", and almost nothing ever is: a handoff takes
        # seconds, and what it actually drew was requests that had sat for a
        # day and a half because their target had no terminal to be told with.
        # Traffic drawn as traffic said the system was busy while it was stuck.
        blocked = actions.stuck(con)
        fl = Panel("STUCK" if blocked else "STUCK — nothing is waiting on anything", width)
        fl.selected = sel.get(STUCK)
        for b in blocked:
            fl.add(pad(f"{YEL}{b['what']}{R}", 34)
                   + pad(f"{DIM}{age(b['age']) if b['age'] else ''}{R}", 7)
                   + f"{DIM}{b['why']}{R}", key=b["agent"])

        # ---- what was decided ------------------------------------------
        rows, hooks = actions.decisions(con, 40, everything=verbose)
        title = "EVERY EVENT" if verbose else "DECISIONS"
        if hooks:
            title += f" — {hooks} hook events hidden (v shows them)"
        rc = Panel(title, width)
        for e in rows:
            when = time.strftime("%H:%M:%S", time.localtime(e["ts"]))
            rc.add(f"{DIM}{when}{R}  {e['agent']:<11}{e['kind']:<18}"
                   f"{DIM}{fit(_detail_of(e['payload']), max(0, width - 46))}{R}")
        if not rows:
            rc.add(f"{DIM}  nothing decided yet{R}")
        return header, [acc, ag, ms, fl, rc]
    finally:
        con.close()


def page(title: str, sections: list[tuple[str, list[str]]], width: int,
         panel: int = AGENTS, key: object = None) -> dict:
    """A detail page laid out as the screen's own panels, not as a wall of text.

    The sections are drawn once, here, into the lines the reader scrolls: what
    is open has to be a list of lines for paging, and it should still look like
    the screen it was opened from.
    """
    lines: list[str] = []
    for heading, rows in sections:
        if heading:
            box = Panel(heading, width)
            for r in rows:
                box.add(r)
            lines += box.draw()
        else:
            lines += [fit(r, width) for r in rows] + [""]
    return opened(title, lines, panel, key)


def opened(title: str, lines: list[str], panel: int = AGENTS, key: object = None) -> dict:
    """One thing held open on the screen, and where it came from.

    It carries its own row rather than just its text: what is open is the thing
    a key then acts on, and a reader answering a message must be answering the
    one they are reading.
    """
    return {"title": title, "lines": lines, "panel": panel, "key": key, "top": 0}


def frame(width: int, height: int, sel: dict | None = None, panel: int = AGENTS,
          flash: str = "", detail: dict | None = None, verbose: bool = False) -> str:
    if detail is not None:
        return _detail_frame(width, height, detail, flash)
    header, panels = build(width, sel, verbose)
    room = height - len(header) - 2                    # the two footer rows
    limits, hidden = _layout([len(p.rows) for p in panels], room)
    # When anything had to be shortened, the team list gives up the lines that
    # explain its rows before any panel gives up a row of its own.
    extras = all(lim is None for lim in limits) and not hidden
    body: list[str] = []
    for p, lim in zip(panels, limits):
        if lim == 0:
            continue
        body += p.draw(limit=lim, extras=extras or p is not panels[1])
    foot = [fit(flash, width) if flash else "", keybar(panel, width, hidden)]
    lines = header + body
    lines = lines[:max(0, height - len(foot))]
    while len(lines) < height - len(foot):
        lines.append("")
    lines = (lines + foot)[:height]
    return "\n".join(line + CLEAR_EOL for line in lines)


# A panel that cannot have a border, one real row, its "… N more" marker and
# its other border says nothing at all - and an empty box reads as "nothing
# here" while the thing it would have shown is what the reader is looking for.
# Three was not enough: the one row a panel got was overwritten by its own
# marker, so the screen drew four boxes with no content between them.
PANEL_MIN = 4


def _layout(want: list[int], room: int) -> tuple[list[int | None], int]:
    """How many rows each panel may draw, and how many panels do not fit.

    Panels are in the order they matter. Everything fits, or every panel that
    is drawn at all gets one row and then they grow together a row at a time -
    so a long list cannot starve a short one, and the screen never draws a box
    it has nothing to put in.
    """
    if sum(w + 2 for w in want) <= room:
        return [None] * len(want), 0
    keep = len(want)
    while keep and keep * PANEL_MIN > room:
        keep -= 1
    # Two rows each to begin with: one to say something, one for the marker
    # that says how much is not being said.
    limits = [2 if i < keep else 0 for i in range(len(want))]
    left = room - keep * PANEL_MIN
    while left > 0 and any(limits[i] < want[i] for i in range(keep)):
        for i in range(keep):
            if left <= 0:
                break
            if limits[i] < want[i]:
                limits[i] += 1
                left -= 1
    return [None if limits[i] >= want[i] and limits[i] else limits[i]
            for i in range(len(want))], len(want) - keep


def wrap(lines: list[str], width: int) -> list[str]:
    """Break long lines to the terminal's width, at spaces where there are any.

    A message body arrives as whatever the sender typed, and an agent's report
    is often one paragraph with no newline in it at all. Fitting that to the
    width shows its first sentence and drops the rest - which is not reading
    the message.
    """
    width = max(1, width)
    out: list[str] = []
    for line in lines:
        # A body is whatever the sender typed. Escapes in it are invisible to
        # vlen, so they are never counted and never truncated: a pasted colour
        # runs past its line, and a pasted `esc [ 2J` clears the screen this is
        # drawing. What a message says is its text.
        line = _ANSI.sub("", line).rstrip()
        if not line:
            out.append("")
            continue
        while vlen(line) > width:
            # Cut on width, not on characters: with wide glyphs the two are not
            # the same number, and cutting on the second wraps a line that is
            # already too long back onto itself.
            room, cut = 0, 0
            for i, ch in enumerate(line):
                if room + cwidth(ch) > width:
                    break
                room += cwidth(ch)
                cut = i + 1
            space = line.rfind(" ", 0, cut + 1)
            if space > 0:
                cut = space
            out.append(line[:cut].rstrip())
            line = line[cut:].lstrip()
        out.append(line)
    return out


def _detail_frame(width: int, height: int, detail: dict, flash: str = "") -> str:
    """One thing, opened, with what is off the bottom said rather than cut.

    The panels shorten a list and count what they held back; a message is not a
    list, so this wraps and scrolls instead - and a message read half way is
    exactly the one someone is about to answer, which is why it can be answered
    from here.
    """
    body = wrap(detail["lines"], width)
    room = max(1, height - 4)
    top = max(0, min(detail.get("top", 0), max(0, len(body) - room)))
    detail["top"] = top
    shown = body[top:top + room]
    while len(shown) < room:
        shown.append("")
    where = (f"{DIM}  line {top + 1}-{min(len(body), top + room)} of {len(body)}{R}"
             if len(body) > room else "")
    keys = "esc back · ↑↓ scroll · space page"
    if detail.get("panel") == MESSAGES:
        keys += " · a answer · e answer in $EDITOR · x dismiss"
    elif detail.get("panel") in (AGENTS, STUCK):
        keys += " · " + " · ".join(f"{k} {label}" for k, label in AGENT_KEYS
                                   if label != "open")
    lines = ([fit(f"{BOLD}{detail['title']}{R}{where}", width), ""]
             + [fit(ln, width) for ln in shown]
             + [fit(flash, width) if flash else "", fit(f"{DIM}{keys} · q quit{R}", width)])
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


def _suspend(argv: list[str], env: dict, fd, saved) -> tuple[int | None, str]:
    """Hand the whole terminal to another program, then take it back.

    Logging in is a human typing a code into claude's own screen: nothing about
    that can happen behind an alternate screen with the cursor hidden.
    """
    # Only a screen that is up has to be taken down. Without a terminal there
    # is no alternate screen to leave, and writing the escapes anyway puts them
    # in whatever is reading our output.
    on_screen = sys.stdout.isatty()
    if on_screen:
        sys.stdout.write(CUR_ON + ALT_OFF)
        sys.stdout.flush()
    if saved:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
    code, out = None, ""
    try:
        r = subprocess.run(argv, env=env)
        code, out = r.returncode, f"{argv[0]} exited {r.returncode}"
    except (OSError, subprocess.SubprocessError) as e:
        out = f"could not run {argv[0]}: {e}"
    finally:
        if saved:
            tty.setcbreak(fd)
        if on_screen:
            sys.stdout.write(ALT_ON + CUR_OFF)
            sys.stdout.flush()
    return code, out


def run(interval: float = 2.0, once: bool = False) -> int:
    if once or not sys.stdout.isatty():
        size = shutil.get_terminal_size((100, 40))
        print(frame(min(size.columns, 200), size.lines))
        return 0

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd) if sys.stdin.isatty() else None
    panel, sel, flash, detail, verbose = AGENTS, {}, "", None, False
    try:
        if saved:
            tty.setcbreak(fd)
        sys.stdout.write(ALT_ON + CUR_OFF)
        while True:
            size = shutil.get_terminal_size((100, 40))
            width, height = size.columns, size.lines
            sys.stdout.write(HOME + frame(width, height, sel, panel, flash, detail,
                                          verbose))
            sys.stdout.flush()
            k = _key(interval)
            if k is None:
                continue
            if k in ("q", "Q", "\x03"):
                return 0
            if detail is not None:
                try:
                    detail, flash = _in_detail(detail, k, width, height, fd, saved)
                except Exception as e:  # noqa: BLE001
                    flash = f"{RED}{type(e).__name__}: {e}{R}"
                continue
            flash = ""
            if k == "\t":
                panel = (panel + 1) % len(PANELS)
                continue
            if k in ("r", "R"):
                continue
            if k == "v":
                verbose = not verbose
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


def _in_detail(detail: dict, k: str, width: int, height: int,
               fd=None, saved=None) -> tuple[dict | None, str]:
    """One keypress while something is open. Returns (what is open now, line).

    Closing is not the only thing a reader wants to do with a message: the one
    they have just read is the one they are about to answer, and making them
    back out to the panel to do it is how an answer gets put off.
    """
    room = max(1, height - 4)
    if k in ("\033", "\x1b"):
        return None, ""
    if k in ("j", "\033[B", "\033OB"):
        detail["top"] += 1
        return detail, ""
    if k in ("k", "\033[A", "\033OA"):
        detail["top"] = max(0, detail["top"] - 1)
        return detail, ""
    if k in (" ", "\033[6~"):
        detail["top"] += room
        return detail, ""
    if k in ("b", "\033[5~"):
        detail["top"] = max(0, detail["top"] - room)
        return detail, ""
    if k == "g":
        detail["top"] = 0
        return detail, ""
    if k == "G":
        detail["top"] = max(0, len(wrap(detail["lines"], width)) - room)
        return detail, ""
    if detail.get("panel") in (AGENTS, STUCK):
        # An agent's page is opened to decide something about that agent, and
        # making the reader back out to the list to press the key is how a
        # decision gets put off.
        label = dict(AGENT_KEYS).get(k)
        if not label or label == "open":
            return detail, ""
        name = str(detail["key"])
        con = db.connect()
        try:
            if not actions.can(con, name, label):
                return detail, f"{name}: {label} does not apply right now"
            if label == "message":
                body = _line(f"message to {name}:", width, height)
                return detail, (actions.message(con, name, body) if body is not None
                                else "nothing sent")
            if label == "start":
                repo = _line(f"repo for {name} [{os.getcwd()}]:", width, height)
                if repo is None:
                    return detail, "not started"
                _say(f"starting {name}", width, height)
                return detail, actions.start(con, name, repo.strip() or os.getcwd())
            if label in CONFIRM and not _confirm(f"{label} {name}", width, height):
                return detail, "left alone"
            _say(f"{label} {name}", width, height)
            said = getattr(actions, label)(con, name)
            # What it says about itself has just changed.
            fresh = page(detail["title"], actions.agent_detail(con, name), width,
                         detail["panel"], name)
            fresh["top"] = detail["top"]
            return fresh, said
        finally:
            con.close()
    if detail.get("panel") != MESSAGES or k not in ("a", "e", "x"):
        return detail, ""

    rid = int(detail["key"])
    con = db.connect()
    try:
        return _answer_key(con, detail, rid, k, width, height, fd, saved)
    except Exception as e:  # noqa: BLE001
        # Answering can fail in ways reading cannot - an editor that will not
        # start, a request resolved from elsewhere between the read and the
        # reply. That is this key's failure, not the screen's.
        return detail, f"{RED}{type(e).__name__}: {e}{R}"
    finally:
        con.close()


def _answer_key(con, detail: dict, rid: int, k: str, width: int, height: int,
                fd, saved) -> tuple[dict | None, str]:
    if k == "x":
        return None, actions.dismiss(con, rid)
    text = _compose(rid, k, width, height, fd, saved)
    if text is None:
        return detail, _nothing_sent(k)
    _say(f"answering #{rid}", width, height)
    return None, actions.answer(con, rid, text)


def _compose(rid: int, key: str, width: int, height: int, fd, saved) -> str | None:
    """An answer, typed on the line or written in $EDITOR. None means none."""
    if key == "e":
        return _editor(f"# answering #{rid}. Lines starting with # are dropped.\n",
                       fd, saved)
    return _line(f"answer #{rid}:", width, height)


def _nothing_sent(key: str) -> str:
    if key == "e" and not (os.environ.get("EDITOR") or os.environ.get("VISUAL")):
        return "nothing sent - $EDITOR is not set"
    return "nothing sent"


def _editor(seed: str, fd=None, saved=None) -> str | None:
    """Write a long answer where long answers are written.

    A one-line prompt is enough to say yes; it is not enough to say why, and an
    answer to an escalation is usually the why. None when nothing was written,
    so backing out of the editor sends nothing.
    """
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL")
    if not editor:
        return None
    try:
        argv = shlex.split(editor)
    except ValueError:
        return None                    # $EDITOR with an unclosed quote
    fd_, path = tempfile.mkstemp(prefix="ocp-answer-", suffix=".md")
    os.close(fd_)
    Path(path).write_text(seed)
    try:
        # `saved` is the terminal as the human's shell had it. Handing an
        # editor the cbreak mode this screen runs in gives it a terminal in a
        # state it did not ask for and does not put back.
        code, _out = _suspend([*argv, path], dict(os.environ), fd, saved)
        if code != 0:
            # Non-zero is how an editor says "abandon this" - `:cq` in vim, and
            # what `git commit` honours. Sending the buffer anyway would send
            # half an answer.
            return None
        body = "\n".join(ln for ln in Path(path).read_text().splitlines()
                         if not ln.startswith("#")).strip()
        return body or None
    except OSError:
        return None                    # the editor removed the file under us
    finally:
        Path(path).unlink(missing_ok=True)


def _say(what: str, width: int, height: int) -> None:
    """Put one line on the bottom row now, without waiting for a redraw.

    An action can take half a minute - waking waits for a session to appear,
    parking pays for a fold - and a screen still showing the frame from before
    the keypress, or the confirmation that was just answered, reads as a hang.
    The next redraw repaints every row, so this cannot outlive its action.
    """
    if not sys.stdout.isatty():
        return                      # no screen to put it on
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
        if panel == MESSAGES:
            return [m["id"] for m in actions.messages(con)]
        # The rows about an account have no agent to act on, and a name that
        # appears twice is one place to put the cursor, not two.
        seen: list = []
        for b in actions.stuck(con):
            if b["agent"] and b["agent"] not in seen:
                seen.append(b["agent"])
        return seen
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
            return "", page(f"account {target}",
                            actions.account_detail(con, str(target)), width,
                            ACCOUNTS, target)
        finally:
            con.close()
    if panel == ACCOUNTS and key == "l":
        line, argv, env = actions.login_argv(str(target))
        if not argv:
            return line, None
        return _suspend(argv, env, fd, saved)[1], None

    con = db.connect()
    try:
        if panel in (AGENTS, STUCK):
            name = str(target)
            if key in ("\r", "\n"):
                return "", page(f"agent {name}", actions.agent_detail(con, name),
                                width, panel, name)
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
            if label in CONFIRM:
                if not _confirm(f"{label} {name}", width, height):
                    return "left alone", None
                # A confirmation waits on a human for as long as the human
                # takes, and the daemon ticks every two seconds: work can
                # arrive and a turn can start inside that wait. What was true
                # when the key was pressed is not what this acts on.
                if not actions.can(con, name, label):
                    return f"{name}: {label} no longer applies - it changed while you looked", None
            _say(f"{label} {name}", width, height)
            return getattr(actions, label)(con, name), None

        if panel == MESSAGES:
            rid = int(target)
            row = con.execute("SELECT * FROM requests WHERE id = ?", (rid,)).fetchone()
            if not row:
                return f"#{rid} is gone", None
            if key in ("\r", "\n"):
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(row["ts"]))
                title = (f"#{rid} from {row['requester']} · {row['kind'] or 'ask'} · {when}")
                return "", opened(title, (row["body"] or "").splitlines() or ["(empty)"],
                                  MESSAGES, rid)
            if key in ("a", "e"):
                text = _compose(rid, key, width, height, fd, saved)
                if text is None:
                    return _nothing_sent(key), None
                _say(f"answering #{rid}", width, height)
                return actions.answer(con, rid, text), None
            if key == "x":
                return actions.dismiss(con, rid), None
        return "", None
    finally:
        con.close()
