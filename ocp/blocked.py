"""An agent ocp has work for and cannot give it does not sit silently.

Every path that types at an agent refuses when the screen says a line would not
land, and refusing is right. It is also all that happened: the agent read as
idle, the work stayed queued, and the daemon did not type, tick after tick,
while nobody was told. This is the part that tells them.

A block is a fact about an agent and its work, not a state of the agent. The
daemon keeps trying and carries on the tick the obstacle goes, so nothing here
is a state a person has to clear (STALLED is that); a `delivery_blocks` row
stands while the work waits and the same obstacle holds, is on the founder's
list from SHOW_AFTER_S, and becomes one request to the founder at
ESCALATE_AFTER_S. It is written by the daemon and read by the bridge and
`ocp ps`, which are other processes, so it lives in the database.

ocp names what is in the way, and presses at most one key at a dialog: Esc, and
only where `orca.declining_key` admits it - the dialog says Esc cancels and
offers an option that just declines, or it is the one dialog whose Esc was
measured. It never chooses: a press lands on the dialog or changes nothing,
except where a person has typed into that agent's input box, which Esc can
clear. What it cleared it reports, and a dialog that keeps coming back is a
loop the founder is told about, not one it keeps pressing.

A dialog is also in the way of ocp's own exit and clear, which no waiting
outlasts: `past_dialog` gets past it by the same rules before either is typed,
and one it cannot get past stops the agent (daemon `_stalled_on_dialog`).
"""
from __future__ import annotations

import time

from . import ask as askmod, db, orca, paths, status, waiting

# The same obstacle, with work waiting, this long before it is said. A dialog
# does not clear itself, so the wait is only for the person who is at the
# terminal and answers within a minute or two; longer and an agent sits on the
# founder's own answer with the founder not knowing.
SHOW_AFTER_S = 180
# Long enough that someone at the bridge has seen the list and acted, short
# enough that nobody learns of it from an agent that has been silent for an
# hour. Later than SHOW_AFTER_S: a request stays until someone closes it if
# the daemon cannot, and the list clears itself.
ESCALATE_AFTER_S = 600
# What each check costs an agent is a screen read and a terminal listing, so a
# block is re-read this often and no more; a block that is not due is left as it was.
RECHECK_S = 10
# A person at the terminal reads a dialog for a minute before it is theirs to
# answer; ocp's own `/exit` has no person in it and is pressed at once. Shorter
# than SHOW_AFTER_S, so a dialog ocp clears is never on the founder's list at
# all - it is reported once, as done.
PRESS_AFTER_S = 60
# A dialog that returns after ocp cleared it is a loop. Idle agents are closed
# every IDLE_CLOSE_S (10 minutes), so a close that meets background shells
# raises the same dialog on each pass: one clear is an event, two could be
# chance, and the third within the window is the pattern. Then ocp stops
# pressing and the founder is told at once, not at ESCALATE_AFTER_S.
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

NO_TERMINAL, NO_SESSION, STALLED, ORCA_DOWN = "no terminal", "no session", "stalled", "orca down"

# What to do about each, in the words of the bridge's own keys.
ADVICE = {
    orca.MODAL: "answer it in its terminal (t on the agents panel)",
    orca.BACKGROUNDED: "open its terminal (t on the agents panel) and press Esc to return to the conversation",
    orca.UNREADABLE: "check its terminal (t on the agents panel); ocp cannot read its screen",
    NO_TERMINAL: "t on the agents panel starts one",
    NO_SESSION: "w on the agents panel wakes it",
    STALLED: "u on the agents panel clears it",
    ORCA_DOWN: "check Orca and PATH; ocp cannot reach it at all",
}
LABEL = {orca.MODAL: "dialog open", orca.BACKGROUNDED: "conversation in the background",
         orca.UNREADABLE: "screen unreadable",
         ORCA_DOWN: "orca unreachable",
         NO_TERMINAL: "no terminal", NO_SESSION: "no session", STALLED: "stalled"}

_checked: dict[str, int] = {}


def reason(kind: str, detail: str) -> str:
    """What is in the way, as a sentence."""
    if kind in (orca.MODAL, orca.BACKGROUNDED, orca.UNREADABLE):
        return orca.Blocker(kind, detail).reason()
    return f"{LABEL.get(kind, kind)}: {detail}" if detail else LABEL.get(kind, kind)


def headline(kind: str, detail: str) -> str:
    """The short form for a row with little room: the label and, for a dialog,
    only its title."""
    head = detail.split(" - ")[0] if kind == orca.MODAL else detail
    return f"{LABEL.get(kind, kind)}: {head}" if head else LABEL.get(kind, kind)


def work_waiting(con, agent: str, row, now: int) -> str:
    """What ocp wants to type at this agent and has not: the head of its queue
    while it is untold, answers it asked for (`would_wake`'s own terms), and the
    reminder to resolve a request it has sat idle on. Empty when there is nothing."""
    from . import daemon

    parts = []
    head = db.head_of_queue(con, agent)
    if head and (db.get_cursor(con, f"notify:{agent}") != int(head["id"])
                 or daemon._session_changed(con, agent)):
        behind = db.queue_depth(con, agent) - 1
        parts.append(f"request #{head['id']}" + (f" (+{behind} behind)" if behind > 0 else ""))
    answers = db.unannounced_answers(con, agent)
    if answers:
        parts.append("answer " + ", ".join(f"#{r['id']}" for r in answers[:3])
                     + (f" (+{len(answers) - 3})" if len(answers) > 3 else ""))
    idle = daemon.forgotten_resolve(con, agent, row, now)
    if idle and idle["idle_s"] >= daemon.NUDGE_AFTER_S \
            and db.get_cursor(con, f"nudge:{agent}") != int(idle["head"]["id"]):
        parts.append(f"reminder to resolve #{idle['head']['id']}")
    return "; ".join(parts)


def classify(con, agent: str, row) -> tuple[str, str, str] | None:
    """(kind, detail, title) of what is in the way of giving this agent its
    work, or None when nothing is. One screen read at most. `title` is the
    dialog's own title, raw - empty for every kind but MODAL.

    Left out on purpose: busy, which is what an agent working is and which
    `STUCK_RUNNING_S` already judges; a usage wall, which resolves when the
    window does; a closed session, which the wake path brings back and whose
    failures end in STALLED; and a permission prompt, which the hooks already
    put on the founder's list.
    """
    from . import daemon

    if not row or row["closed_at"] or waiting.of(row):
        return None
    if row["state"] == daemon.STALLED:
        return STALLED, row["detail"] or "", ""
    handle = row["terminal"]
    if not handle:
        return NO_TERMINAL, "", ""
    if not orca.available():
        # The sibling of UNREADABLE below: silently skipping this starves
        # every agent with queued work the same way, with nobody told, for as
        # long as Orca stays off PATH.
        return ORCA_DOWN, "", ""
    if not orca.alive(handle):
        return NO_TERMINAL, "its terminal is gone - Orca no longer has it", ""
    if daemon.wall_reason(con, agent, row):
        return None
    if row["state"] not in (daemon.RUNNING, daemon.HANDOFF) and not daemon.session_present(agent):
        return NO_SESSION, "", ""
    seen = orca.screen_blocker(handle)
    if seen is None:
        return None
    return seen.kind, seen.detail, seen.title


def note_close_failure(con, agent: str, log) -> None:
    """ocp typed `/exit` and the session is still there: if the screen shows why,
    it is on the list at once, since the dialog is ocp's own doing and nobody has
    been given the minutes the list otherwise waits for."""
    handle = db.get_terminal(con, agent)
    seen = orca.screen_blocker(handle) if handle else None
    if not seen or seen.kind != orca.MODAL:
        return
    now = int(time.time())
    if db.block_get(con, agent) is None:
        db.block_open(con, agent, seen.kind, seen.detail,
                      work_waiting(con, agent, db.get_agent_state(con, agent), now), now,
                      show_at=now, origin="close", title=seen.title)
        log(f"  {agent}: {reason(seen.kind, seen.detail)} - ocp's own /exit raised it")


def past_dialog(con, agent: str, log, now: int | None = None,
                only_limit: bool = False) -> tuple[str, str]:
    """(why not, the dialog's title): get past a dialog standing between ocp and
    an exit or clear it is about to type. `why not` is '' when none is open or
    the one that was is now declined.

    The key and the rules are `_decline`'s - Esc, only where `declining_key`
    admits it, never for a dialog that has stopped being pressed - and the
    press is recorded in the same history, so a dialog that keeps returning is
    still the loop `stopped_pressing` counts. The caller holds the agent's
    terminal; unlike the sweep this does not wait out PRESS_AFTER_S, since
    the exit is ocp's own and no person is answering a dialog it is about to
    dismiss.

    That warrant holds where the caller is committed to ending the session -
    and one ending it has already tried `choose_exit`, so Esc here is only what
    is left when no 'Exit and stop tasks' option was there. A
    caller that is only deciding whether to begin passes `only_limit`: then
    nothing is pressed for a dialog that waiting may clear because a person is
    answering it (a permission prompt on a working agent), and it reads as no
    obstacle at all."""
    handle = db.get_terminal(con, agent)
    seen = orca.screen_blocker(handle) if handle else None
    if seen is None or seen.kind != orca.MODAL:
        return "", ""
    if only_limit and not orca.usage_limit_open(handle):
        return "", ""
    now = int(time.time()) if now is None else now
    stopped = stopped_pressing(con, agent, {"title": seen.title}, now)
    if stopped:
        return f"{reason(seen.kind, seen.detail)}; {stopped}", seen.title
    outcome, title, why = orca.decline_dialog(handle)
    if outcome == orca.NOT_PRESSED:
        return f"{reason(seen.kind, seen.detail)}; ocp did not press it: {why}", seen.title
    db.press_add(con, agent, now, title, outcome, why)
    if outcome == orca.STILL_OPEN:
        return f'{reason(seen.kind, seen.detail)}; Esc did not close it', seen.title
    db.add_event(con, agent, "dialog_declined", {"title": title, "key": "Esc", "work": "exit"})
    log(f'  {agent}: pressed Esc on "{title}" to get to its exit')
    return "", ""


def choose_exit(con, agent: str, log) -> bool:
    """Choose 'Exit and stop tasks' on this agent's dialog, when there is one.
    True when the key was sent.

    Only for a caller that has decided to end the session: there, declining a
    dialog that offers to end it is choosing to keep the session. Nothing else
    calls this - the sweep may be facing a person's own answer - and a dialog
    without that option is left to `past_dialog`'s rules."""
    handle = db.get_terminal(con, agent)
    if not handle:
        return False
    outcome, title, why = orca.choose_exit(handle)
    if outcome != orca.CHOSEN:
        return False
    db.add_event(con, agent, "dialog_exit_chosen", {"title": title})
    log(f'  {agent}: chose "Exit and stop tasks" on "{title}"')
    return True


def dialog_note(con, agent: str) -> str:
    """' - <the dialog on its screen>' when that is what ocp's own close met,
    else '': what a stall on repeated failed handoffs adds so its reason is not
    only a count."""
    rec = db.block_get(con, agent)
    return f" - {reason(rec['kind'], rec['detail'])}" if rec and rec["kind"] == orca.MODAL else ""


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
            return (f"ocp cleared it {all_cleared} times in {status.ago(PRESS_WINDOW_S)} and it "
                    f"came back each time, so it has stopped pressing")
        return (f"ocp cleared {all_cleared} dialogs on this agent in {status.ago(PRESS_WINDOW_S)} "
                f"({len(titles)} different titles), so it has stopped pressing")
    history = [p for p in all_history if p["title"] == title]
    cleared = sum(1 for p in history if p["outcome"] == "cleared")
    if cleared >= MAX_PRESSES:
        return (f"ocp cleared it {cleared} times in {status.ago(PRESS_WINDOW_S)} and it "
                f"came back each time, so it has stopped pressing")
    pressed = [p for p in history if p["outcome"] != "not-pressed"]
    if len(pressed) >= MAX_IGNORED and all(p["outcome"] == "still-open" for p in pressed[-MAX_IGNORED:]):
        return "Esc did not close it, so ocp has stopped pressing"
    return ""


def press_note(con, agent: str, rec, now: int) -> str:
    """What ocp says about not having cleared this block's dialog, or ''."""
    if rec["kind"] != orca.MODAL:
        return ""
    stopped = stopped_pressing(con, agent, rec, now)
    if stopped:
        return stopped
    said = [p for p in db.presses_since(con, agent, rec["since"]) if p["outcome"] == "not-pressed"]
    return f"ocp did not press it: {said[-1]['note']}" if said else ""


def _decline(con, agent: str, rec, now: int, log):
    """Press Esc at this block's dialog when the rules allow, and say what came
    of it. The block, or None when the dialog is gone."""
    press_at = rec["since"] if rec["origin"] == "close" else rec["since"] + PRESS_AFTER_S
    handle = db.get_terminal(con, agent)
    if now < press_at or not handle or stopped_pressing(con, agent, rec, now):
        return rec
    outcome, title, why = orca.decline_dialog(handle)
    if outcome == orca.NOT_PRESSED:
        last = [p for p in db.presses_since(con, agent, rec["since"]) if p["outcome"] == "not-pressed"]
        if not last or last[-1]["note"] != why or last[-1]["title"] != title:
            db.press_add(con, agent, now, title, outcome, why)
            log(f"  {agent}: ocp did not press its dialog - {why}")
        return rec
    db.press_add(con, agent, now, title, outcome, why)
    if outcome == orca.STILL_OPEN:
        log(f"  {agent}: pressed Esc on \"{title}\" and it is still open")
        return rec
    how = f'cleared by ocp: pressed Esc on "{title}"'
    db.add_event(con, agent, "dialog_declined", {"title": title, "key": "Esc", "work": rec["work"]})
    askmod.report(con, agent,
                  f"{how}.\n{agent} could not be given its work ({rec['work'] or 'nothing yet'}) "
                  f"while it was open: {reason(rec['kind'], rec['detail'])}.\nThe dialog's own footer "
                  f"says Esc cancels it, and Esc is a key that only declines it, so that was the "
                  f"only key ocp pressed; nothing was chosen. If it comes back {MAX_PRESSES} times "
                  f"in {status.ago(PRESS_WINDOW_S)} ocp stops pressing and tells you.",
                  "dialog declined")
    _end(con, agent, rec, how, log)
    return None


def sweep(con, log, now: int | None = None) -> None:
    """Once per tick, after the agents: open, keep and end each agent's block,
    and raise the request for those that have outlasted ESCALATE_AFTER_S - one
    request for all that come due in the same pass."""
    now = int(time.time()) if now is None else now
    due = []
    registered = set(paths.list_agents())
    for row in db.all_agent_states(con):
        agent = row["agent"]
        # The human is never typed at, and a row for an agent that is not on the
        # team has nobody to be given anything.
        if agent not in registered:
            continue
        try:
            rec = _track(con, agent, row, now, log)
        except Exception as e:  # noqa: BLE001 - one agent's unreadable screen is not the tick's failure
            log(f"  {agent}: block check failed: {e}")
            continue
        if rec is not None and rec["request_id"] is None and (
                now - rec["since"] >= ESCALATE_AFTER_S
                or (rec["kind"] == orca.MODAL and stopped_pressing(con, agent, rec, now))):
            due.append(rec)
    if due:
        _raise(con, due, now, log)


def _track(con, agent: str, row, now: int, log):
    from . import daemon

    rec = db.block_get(con, agent)
    work = work_waiting(con, agent, row, now)
    if not work and not (rec and rec["origin"] == "close"):
        if rec:
            _end(con, agent, rec, "nothing is waiting for it now", log)
        return None
    if rec is not None and now - _checked.get(agent, 0) < RECHECK_S:
        return rec
    _checked[agent] = now
    found = classify(con, agent, row)
    if found is None:
        if rec:
            _end(con, agent, rec, "it can be given its work again", log)
        return None
    kind, detail, title = found
    if rec is None:
        db.block_open(con, agent, kind, detail, work, now, show_at=now + SHOW_AFTER_S, title=title)
        log(f"  {agent}: work is waiting and cannot be given - {reason(kind, detail)}")
    else:
        if rec["kind"] != kind:
            log(f"  {agent}: still cannot be given its work - {reason(kind, detail)}")
        db.block_update(con, agent, kind, detail, work or rec["work"], now, title=title)
    rec = db.block_get(con, agent)
    # An agent that is working may be waiting on a person for that very dialog;
    # only an idle one's is pressed.
    if rec["kind"] == orca.MODAL and row["state"] not in (daemon.RUNNING, daemon.HANDOFF):
        return _decline(con, agent, rec, now, log)
    return rec


def _end(con, agent: str, rec, why: str, log) -> None:
    db.block_clear(con, agent)
    log(f"  {agent}: no longer blocked ({LABEL.get(rec['kind'], rec['kind'])}) - {why}")
    rid = rec["request_id"]
    if rid and not any(b["request_id"] == rid for b in db.blocks(con)):
        row = db.get_request(con, rid)
        if row is not None and row["state"] == "queued":
            askmod.resolve(rid, f"cleared - {why}", state="done", notify=False)


def _raise(con, recs, now: int, log) -> None:
    lines = []
    for r in recs:
        held = now - r["since"]
        note = press_note(con, r["agent"], r, now)
        lines.append(f"{r['agent']} cannot be given its work ({status.ago(held)}).\n"
                     f"  In the way: {reason(r['kind'], r['detail'])}\n"
                     f"  Waiting: {r['work'] or 'nothing yet'}\n"
                     + (f"  {note[0].upper() + note[1:]}.\n" if note else "")
                     + f"  To clear it: {ADVICE.get(r['kind'], 'see `ocp ps`')}.")
    text = "[blocked] " + ("\n\n".join(lines)) + \
        "\n\nThis closes itself when it can be given its work."
    rid = askmod.escalate(con, recs[0]["agent"], text, "delivery blocked")
    db.block_set_request(con, [r["agent"] for r in recs], rid)
    log(f"  blocked: raised #{rid} for {', '.join(r['agent'] for r in recs)}")


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
