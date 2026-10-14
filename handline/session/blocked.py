"""What is in the way of giving an agent its work, as the founder's list shows it.

The daemon writes a `delivery_blocks` row while work waits behind the same obstacle, with `show_at` set for when
it goes on the list, and records each Esc it pressed at a dialog. This reads those rows for the bridge and `hl ps`:
the obstacle in a few words and, for a dialog, why ocp did not clear it or has stopped pressing it.
"""
from __future__ import annotations

from .. import db
from . import term
from ..infra import timefmt

# A dialog that returns after ocp cleared it is a loop. Idle agents are closed
# every IDLE_CLOSE_S (10 minutes), so a close that meets background shells
# raises the same dialog on each pass: one clear is an event, two could be
# chance, and the third within the window is the pattern. Then ocp stops
# pressing and the founder is told at once.
MAX_PRESSES = 3
PRESS_WINDOW_S = 6 * 3600
# Esc that leaves the dialog standing is not pressed again and again.
MAX_IGNORED = 2
# The per-dialog count above is keyed on the dialog's own title, which is
# text an agent's screen paints - a counter or a timestamp in it (no malice
# required) outruns MAX_PRESSES by never repeating. This is the absolute stop
# that does not depend on the key: however many different titles, ocp does
# not clear more than this many dialogs on one agent in the same window.
# Well above MAX_PRESSES, so an agent that genuinely meets a few different,
# legitimate dialogs is not the case this catches.
MAX_PRESSES_AGENT = 10

NO_TERMINAL, NO_SESSION, STALLED, HOST_DOWN = "no terminal", "no session", "stalled", "host down"
# The process table lists no session for an agent whose terminal draws one. Work is typed at it on the
# screen's word, so this is not an obstacle; it is a fault in the table that the founder is told about
# (the bridge, `hl ps`, and a request once it has lasted), with how each session-looking
# process was read, since the table missing a live session is what starved the auditor for hours.
TABLE_MISS = "table miss"

LABEL = {term.MODAL: "dialog open", term.BACKGROUNDED: "conversation in the background",
         term.UNREADABLE: "screen unreadable",
         HOST_DOWN: "host unreachable",
         NO_TERMINAL: "no terminal", NO_SESSION: "no session", STALLED: "stalled",
         TABLE_MISS: "process table misses a live session"}


def headline(kind: str, detail: str) -> str:
    """The short form for a row with little room: the label and, for a dialog,
    only its title."""
    head = detail.split(" - ")[0] if kind == term.MODAL else detail
    return f"{LABEL.get(kind, kind)}: {head}" if head else LABEL.get(kind, kind)


def stopped_pressing(con, agent: str, rec, now: int) -> str:
    """Why ocp will not press this agent's dialog again, or '' when it may.
    `rec` is the block it is asked about - the history is this one dialog's,
    by its own raw title (rec['title'], from parse_dialog - never
    reconstructed from `detail`, a rendering that a title with a quote in it
    or over DETAIL_CHARS long does not round-trip through), not the agent's
    whole 6-hour past."""
    title = rec["title"]
    all_history = db.presses_since(con, agent, now - PRESS_WINDOW_S)
    # The absolute stop, checked first and independent of any title matching:
    # a title an agent varies (a counter, an elapsed time - no malice needed)
    # would otherwise never trip the per-dialog count below, since it never
    # repeats. Checking this first must not hand a single looping dialog the
    # per-agent wording - the count alone does not say whether it was one
    # dialog or several, so the titles actually seen decide what is said.
    all_cleared = sum(1 for p in all_history if p["outcome"] == "cleared")
    if all_cleared >= MAX_PRESSES_AGENT:
        titles = {p["title"] for p in all_history if p["outcome"] == "cleared"}
        if len(titles) == 1:
            return (f"ocp cleared it {all_cleared} times in {timefmt.ago(PRESS_WINDOW_S)} and it "
                    f"came back each time, so it has stopped pressing")
        return (f"ocp cleared {all_cleared} dialogs on this agent in {timefmt.ago(PRESS_WINDOW_S)} "
                f"({len(titles)} different titles), so it has stopped pressing")
    history = [p for p in all_history if p["title"] == title]
    cleared = sum(1 for p in history if p["outcome"] == "cleared")
    if cleared >= MAX_PRESSES:
        return (f"ocp cleared it {cleared} times in {timefmt.ago(PRESS_WINDOW_S)} and it "
                f"came back each time, so it has stopped pressing")
    pressed = [p for p in history if p["outcome"] != "not-pressed"]
    if len(pressed) >= MAX_IGNORED and all(p["outcome"] == "still-open" for p in pressed[-MAX_IGNORED:]):
        return "Esc did not close it, so ocp has stopped pressing"
    return ""


def press_note(con, agent: str, rec, now: int) -> str:
    """What ocp says about not having cleared this block's dialog, or ''."""
    if rec["kind"] != term.MODAL:
        return ""
    stopped = stopped_pressing(con, agent, rec, now)
    if stopped:
        return stopped
    said = [p for p in db.presses_since(con, agent, rec["since"]) if p["outcome"] == "not-pressed"]
    return f"ocp did not press it: {said[-1]['note']}" if said else ""


def founder_rows(con, now: int) -> list[dict]:
    """The blocks that are on the founder's list, in `founder_waits`' shape."""
    rows = []
    for r in db.blocks(con):
        if now < r["show_at"]:
            continue
        note = press_note(con, r["agent"], r, now)
        rows.append({"kind": "blocked", "who": r["agent"], "red": True, "since": int(r["since"]),
                     "what": f"{headline(r['kind'], r['detail'])} · waiting: "
                             f"{r['work'] or 'nothing yet'}" + (f" · {note}" if note else "")})
    return rows
