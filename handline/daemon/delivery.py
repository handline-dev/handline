"""Telling an agent things: seeing its session, typing to it, delivering queued requests and answers, nudging a forgotten resolve."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from ..team import agents, ask as askmod, team as teammod
from ..infra.yamlcompat import ConfigError
from ..infra import paths, protected, sealed, timefmt
from ..session import bgjobs, blocked, idle_close, stopgate
from .. import db
from ..infra import lock
from ..infra.waits import END_WAIT_S, WAKE_SESSION_TIMEOUT_S  # noqa: F401  (tests patch them here)
from ..session import observe as obs
from ..session import term
from ..usage import status
from ..infra import textsafe

from . import inputhold, screensamples, termchannel
from .states import PROMPT_CACHE_S, STUCK_RUNNING_S
from ..session.states import HANDOFF, IDLE, QUEUED, RUNNING, STALLED


# ---------------------------------------------------------------- sending
# What the sweep saw this tick, so the paths that type into a terminal can ask
# without paying for their own process listing.
_seen: dict[str, obs.Observed] = {}
_seen_at = {"t": 0.0}

# How old that reading may be before a send looks again. A tick is 2s, so
# anything older came from another tick or from no tick at all - `hl say`
# delivers through here too - and one listing is cheap next to what a stray
# send does.
SEEN_FRESH_S = 10


def table_lists_session(agent: str) -> bool | None:
    """What the process table says: True it lists a session for this agent, False it does not, None
    when it could not be read."""
    o = _seen.get(agent)
    if o is not None and time.time() - _seen_at["t"] <= SEEN_FRESH_S:
        return (o.session_pid is not None) if o.session_seen else None
    sessions = obs.live_sessions()
    return None if sessions is None else bool(sessions.get(agent))


def seen_world(con) -> tuple[dict | None, set | None]:
    """The (sessions, handles) `actions.stuck` takes, from what the sweep saw this tick: (None, None), which asks nothing, when it saw
    nothing fresh. Handles are the live ones among the handles the agents hold; None when any could not be read."""
    if time.time() - _seen_at["t"] > SEEN_FRESH_S or not _seen:
        return None, None
    seen = list(_seen.items())
    sessions = {a: o.session_pid for a, o in seen if o.session_pid is not None} if all(o.session_seen for _, o in seen) else None
    handles: set | None = set()
    for a, o in seen:
        h = db.get_terminal(con, a)
        if not h:
            continue
        if o.terminal_alive is None:
            handles = None
            break
        if o.terminal_alive:
            handles.add(h)
    return sessions, handles


# A screen read costs a call to the terminal host, so one answer holds this long.
DRAWN_FRESH_S = 5
_drawn: dict[str, tuple[float, bool]] = {}


def screen_draws_session(agent: str) -> bool:
    """Does this agent's terminal draw a session right now? The screen's word, held DRAWN_FRESH_S."""
    hit = _drawn.get(agent)
    if hit is not None and time.time() - hit[0] <= DRAWN_FRESH_S:
        return hit[1]
    con = db.connect()
    try:
        handle = db.get_terminal(con, agent)
    finally:
        con.close()
    answer = bool(handle and term.available() and session_still_drawn(handle, wait=False))
    _drawn[agent] = (time.time(), answer)
    return answer


def session_present(agent: str) -> bool:
    """Is there a session to type into, or only the shell it left behind?

    Orca runs claude as a login shell's child, so when the session ends the
    terminal stays and our handle keeps working - `send` succeeds and the text
    lands at a bash prompt. Handoffs and notices are not inert there: backticks
    run, `>` redirects, and the agent is not the one reading any of it.

    Fail-closed, with two witnesses. A process the table positively lists is a yes. So is a session
    drawn on the agent's terminal: a composer box is not what a shell shows, and the table has missed
    a live session before (work waited on it for hours). "The table could not be read" with nothing
    drawn is a no, because the whole point is to stop sending on a maybe.
    """
    if table_lists_session(agent):
        return True
    return screen_draws_session(agent)


# How long after typing `/exit` to look for the dialog it may raise.
EXIT_DIALOG_WAIT_S = 6
END_POLL_S = 1


def _wait_session_gone(agent: str) -> bool:
    """Is this agent's session out of a fresh reading of the process table?

    Always a fresh reading - the sweep's snapshot is what read the old process
    as alive. An unreadable table is a no, the same fail-closed rule as
    `session_present`: not knowing that it ended is not evidence that it did.
    """
    deadline = time.time() + END_WAIT_S
    while True:
        sessions = obs.live_sessions()
        if sessions is not None and not sessions.get(agent):
            _seen.pop(agent, None)
            return True
        if time.time() >= deadline:
            return False
        time.sleep(END_POLL_S)


def session_still_drawn(handle: str | None, wait: bool = True) -> bool:
    """Is an agent's session still drawn on this terminal's screen?

    The second witness to the process table, whose one costly mistake is a live session missing from
    it. With `wait` the screen is given END_WAIT_S to clear, since a session that has just exited
    repaints within moments. A screen that cannot be read is not a yes: the table's word stands."""
    if not handle:
        return False
    deadline = time.time() + (END_WAIT_S if wait else 0)
    while True:
        if not term.session_on_screen(handle):
            return False
        if time.time() >= deadline:
            return True
        time.sleep(END_POLL_S)




def wait_for_session(agent: str, timeout_s: int, prompt: Path | None = None) -> bool:
    """Did the launch we typed actually start a session?

    `relaunch` reports on having sent the command, which is not the same thing:
    `hl launch` refuses over the 5h limit and exits when the account is not
    logged in, leaving the shell exactly as it was. The reading is taken here
    rather than from the tick's cache, which predates the command.

    With `prompt`, the session counts only once that file is gone: the launch
    removes it just before it becomes the session. A launch typed by someone
    else starts a session without taking it, and counting that one as ours
    loses the prompt.
    """
    deadline = time.time() + timeout_s
    while True:
        sessions = obs.live_sessions()
        if (sessions is not None and sessions.get(agent)
                and not (prompt and prompt.exists())):
            return True
        if time.time() >= deadline:
            return False
        time.sleep(1)


def _past_dialog(con, agent: str, log, only_limit: bool = False) -> str:
    """Get past a dialog before typing an exit or a clear at this agent: '' when
    nothing stands in the way, else why. The caller holds the agent's terminal.
    While a reason stands, when this dialog first proved unpassable is kept, by
    its title: a different dialog starts its own clock, so a caller that can
    wait measures the wait of the dialog that is there. Nothing is pressed at a
    terminal someone holds (`inputhold`): the hold is what stands in the way."""
    held = inputhold.held_by_other(con, agent)
    if held:
        return inputhold.held_why(agent, held)
    why, title = blocked.past_dialog(con, agent, log, only_limit=only_limit)
    since_key, title_key = f"dialog_since:{agent}", f"dialog_title:{agent}"
    if not why:
        db.clear_cursor(con, since_key)
        db.clear_cursor(con, title_key)
        return ""
    # A dialog whose title did not parse cannot be told from another one, so it
    # is never the same as the last: its clock restarts, which errs toward
    # not stalling.
    mark = int(hashlib.sha1(title.encode()).hexdigest()[:8], 16) if title else 0
    if not mark or db.get_cursor(con, title_key) != mark or not db.get_cursor(con, since_key):
        db.set_cursor(con, title_key, mark)
        db.set_cursor(con, since_key, int(time.time()))
    return why


DIALOG_STALL = "cannot get past a dialog"


def _stalled_on_dialog(con, agent: str, why: str, log) -> bool:
    """A dialog ocp cannot get past, standing as long as a block takes to be
    raised with the founder, stops the agent: it is reported with the reason
    and left alone until a person clears it, not retried every tick."""
    since = db.get_cursor(con, f"dialog_since:{agent}")
    if not since or int(time.time()) - since < blocked.DECISION_AFTER_S:
        return False
    db.set_agent_state(con, agent, STALLED, detail=f"{DIALOG_STALL}: {why}")
    log(f"  {agent}: STALLED - {DIALOG_STALL}: {why}")
    return True


def _reclaim_context(con, agent: str, log) -> tuple[bool, str]:
    """Clear the agent's session so the handoff actually costs it its context.

    Called only from inside `perform_handoff`, which already holds this agent's
    terminal - taking it again here would fail rather than nest.
    """
    handle = db.get_terminal(con, agent)
    if not handle:
        return False, ("no terminal recorded; start the agent with `hl spawn` or "
                       "`hl launch` inside an Orca terminal so its context can be reclaimed")
    if not term.available():
        return False, term.missing()
    if not session_present(agent):
        return False, f"no session process; not typing {term.clear_command(handle)} at a shell prompt"
    if _past_dialog(con, agent, log):
        return False, ("a dialog is open on its screen that ocp cannot get past; "
                       f"not typing {term.clear_command(handle)} at it")
    running = term.background_work(handle)
    if running:
        return _clear_over_background(con, agent, handle, running, log)
    return _clear_now(con, agent, handle, log)


# T415: a clear that background work holds off is owed, not failed. The agent is told once
# (CLEAR_NOTICE), and the clear is typed when its work ends or, at the latest, this long
# after: long enough for a turn or two - stop the shells, write `hl progress` - and short
# against a context that was climbing ~2k tokens a minute when the auditor was cleared late.
CLEAR_WAIT_S = 300
CLEAR_NOTICE = (
    "ocp: your context is full ({context}), so this session will be cleared in {minutes} minutes. "
    "No reply is needed. Before then: 1. Stop your background shells. Anything still running at "
    "that time is cut off. 2. If you were waiting on a result, write what was running and what is "
    "left in: hl progress \"done: ... left: ...\" - the next session starts from that note. "
    "3. Do not start new work or new background jobs. When your background work has ended, the "
    "clear happens at once.")


def clear_owed(con, agent: str) -> int:
    """When the agent was told its clear is coming, or 0 when none is owed."""
    return db.get_cursor(con, f"clear_owed:{agent}")


def clear_hold(con, agent: str) -> str | None:
    """Nothing new is typed at an agent whose clear is owed: it would be lost in it."""
    if clear_owed(con, agent):
        return f"{agent} is about to be cleared; nothing is typed at it until then"
    return None


def typing_hold(con, agent: str) -> str | None:
    """Why nothing may be typed at this agent's session now: someone holds its input, the wall, or a clear owed."""
    held = inputhold.held_by_other(con, agent)
    return inputhold.held_why(agent, held) if held else stopgate.wall_reason(con, agent) or clear_hold(con, agent)


def _cache_lapsed(con, agent: str, now: int) -> bool:
    """Its prompt cache is gone (an hour since it last did anything, and it has a record of
    that): nothing it holds is warm, so a clear costs it nothing live."""
    last = db.last_activity(con, agent)
    return last is not None and now - last >= PROMPT_CACHE_S


def _clear_now(con, agent: str, handle: str, log) -> tuple[bool, str]:
    ok, why = term.clear_context(handle, timeout_ms=term.IDLE_PROBE_MS)
    log(f"  {agent}: {why}")
    if ok:
        db.clear_cursor(con, f"clear_owed:{agent}")
    return ok, why


def _clear_over_background(con, agent: str, handle: str, running: str, log) -> tuple[bool, str]:
    """A clear with work still running behind the prompt: at once when the agent's cache has
    lapsed; otherwise the agent is told, and the clear waits for its work to end or for
    CLEAR_WAIT_S. Neither is a failure (False here is "not yet")."""
    now = int(time.time())
    since = clear_owed(con, agent)
    if _cache_lapsed(con, agent, now):
        log(f"  {agent}: background work is running ({running}) but its cache has lapsed - clearing")
        return _clear_now(con, agent, handle, log)
    if not since:
        st = status.read_for_agent(agent, agents.current_account(agent, con))
        limit = status.effective_context_limit(agents.read(agent), st.model_id)
        used = "?" if st.trusted_ctx_used is None else f"{st.trusted_ctx_used:,}"
        ok, err = term.send_to_session(handle, CLEAR_NOTICE.format(context=f"{used} of {limit:,}",
                                                                   minutes=CLEAR_WAIT_S // 60))
        db.set_cursor(con, f"clear_owed:{agent}", now)      # owed even if the notice did not go
        log(f"  {agent}: background work is running ({running}); told it will be cleared in "
            f"{CLEAR_WAIT_S // 60} min{'' if ok else f' (the notice failed: {err[:80]})'}")
        return False, f"background work is running ({running}); {term.clear_command(handle)} is owed"
    if now - since < CLEAR_WAIT_S:
        return False, f"background work is running ({running}); {term.clear_command(handle)} is owed"
    log(f"  {agent}: {CLEAR_WAIT_S // 60} min are up with background work still running ({running}) - clearing")
    ok, why = _clear_now(con, agent, handle, log)
    if ok:
        # Only once it has landed: a clear that waits for the agent's turn to end is tried again next tick.
        rec = db.get_handoff(con, agent) or ""
        db.set_handoff(con, agent, f"{rec}\n\n## Cut off by the clear\nThis session was cleared with "
                       f"background work still running ({running}); it was told {timefmt.ago(now - since)} "
                       f"earlier and asked to stop it. That work is gone - rerun it if it still matters.\n")
    return ok, why


def _live_session(con, agent: str) -> str | None:
    st = status.read_for_agent(agent, agents.current_account(agent, con))
    return st.session_id


def _session_changed(con, agent: str) -> bool:
    """Is the session on screen a different one from the one the head was typed into?

    A delivery belongs to the session that read it. A resume keeps its id, so
    it is not told twice; a fresh start has a new one and has read nothing. A
    session that was closed and comes back is told anyway: `clear_closed`
    voids what it was told. An id that cannot be read is not a difference -
    nothing is retyped on a guess.
    """
    # What voids it on a resume that keeps the id is `db.clear_closed`
    # (`forget_told`), and every caller of that leans on this: a wake, the
    # reconciler finding a session running, a moved account's wake. A new
    # caller that is not a restart would re-tell what its session already read.
    live = _live_session(con, agent)
    return bool(live) and live != db.get_notified_session(con, agent)


# #1973, measured in daemon.out.log: a notify and a context handoff fired in
# the same tick, against the same agent - the handoff read idle from a probe
# taken before the notify landed, and typed /clear into the session the
# notify had just started, which sat unsent behind the turn instead of
# running. `lock.terminal` alone does not close this: it only keeps two
# writers from holding the terminal at once, and the notify had already
# released it by the time the handoff took it. The screen itself lags a
# moment behind a line that just landed - Orca's own idle probe can still
# read "idle" for a beat after Enter is sent - so a *destructive* type (one
# that throws work away if it lands mid-turn: a handoff's /clear or /exit)
# may not follow any type at the same agent, from any path or process,
# within this long - ordinary notifies are not held to it: the vendor this
# was measured on (#1973, Claude Code) keeps text typed mid-turn and runs it
# after the turn ends, rather than discarding it, which is what the incident
# above shows. A vendor that discards or interrupts on a mid-turn type would
# need notifies held to the window too; that has not been measured for any
# other vendor.
TYPE_SETTLE_S = 15


def _type_gate(con, agent: str, handle: str | None, *, require_idle: bool = True,
               require_settled: bool = False) -> str | None:
    """The re-read every path that types at an agent's terminal must do once
    its lock is actually held (see #1973 above) - never trust a reading taken
    before asking for the lock. None means clear to type; anything else is
    why not.

    `require_idle` is False at a site whose own act (`_end_session`) does its
    own fresh idle read inline, where it actually types - `term.typeable_now`
    here would only be a second, redundant read of the same thing.

    `require_settled` is True wherever the type that follows is destructive
    (a handoff's `/clear` or `/exit`) or ends a session (a close, a parked
    move): the settle window above is what catches a stale idle reading that
    `typeable_now`, read fresh a moment too soon, still cannot.
    """
    held = inputhold.held_by_other(con, agent)
    if held:
        return inputhold.held_why(agent, held)
    if require_settled:
        last = db.get_cursor(con, f"typed_at:{agent}")
        if last and int(time.time()) - last < TYPE_SETTLE_S:
            return f"{agent} was typed at moments ago; letting the screen catch up"
    if require_idle and handle and term.available() and not term.typeable_now(handle):
        return f"{agent} is mid-turn or has a dialog open"
    if handle and term.available() and _own_text_in_box(con, agent, handle):
        return f"{agent} has text we typed still unsent in its input box"
    return None


# Text typed at an agent whose Enter was lost stays in its input box: the agent reads idle, and
# anything typed after it joins the same box. What was typed is kept (`unsent:<agent>`) until the
# prompt line is seen without it, and the box alone decides whether it is still unsent - the
# transcript above it showing the text means it went, and it is not sent again.
UNSENT_GRACE_S = 3          # a send that has only just landed is still being drawn
UNSENT_ENTER_WAIT_S = 10    # an Enter that has had this long and left the text there did not take
_typed_text: dict[str, str] = {}      # handle -> what `_send` last typed, until `_mark_typed` records it


def _unsent_key(agent: str) -> str:
    return f"unsent:{agent}"


def _unsent_record(con, agent: str) -> dict | None:
    raw = db.get_meta(con, _unsent_key(agent))
    try:
        rec = json.loads(raw) if raw else None
    except json.JSONDecodeError:
        rec = None
    return rec if isinstance(rec, dict) and isinstance(rec.get("text"), str) else None


def _own_text_in_box(con, agent: str, handle: str) -> bool:
    rec = _unsent_record(con, agent)
    box = term.composer_on_screen(handle) if rec else None
    return bool(rec and box and term.is_text_typed(box, rec["text"]))


def recover_unsent(con, agent: str, log) -> bool:
    """Press Enter on text we typed that is still in the agent's box; True while it is (the tick
    types nothing else at it). The first Enter comes on the tick after the type; if the box still
    holds the text UNSENT_ENTER_WAIT_S after it, the box is cleared and the text typed once more;
    if that is not submitted either the agent is reported blocked and nothing more is pressed.
    A box with anything but our text in it - a person's draft - is never sent."""
    if agents.is_structured(agent):
        return False  # the Go daemon drives it
    rec = _unsent_record(con, agent)
    handle = db.get_terminal(con, agent)
    if not rec or not handle or not term.available():
        return False
    if not _own_text_in_box(con, agent, handle):
        if term.composer_on_screen(handle) is not None:      # unreadable keeps the record
            db.clear_meta(con, _unsent_key(agent))
            note_hold(agent, None)
        return False
    now = int(time.time())
    age = now - int(rec.get("at", now))
    if rec.get("blocked"):
        note_hold(agent, "text we typed is still unsent in its input box and Enter does not send it")
        return True
    if (rec.get("enters") and age < UNSENT_ENTER_WAIT_S) or age < UNSENT_GRACE_S:
        return True
    if term.screen_blocker(handle) is not None:
        return True                                          # an open dialog would take the Enter
    with lock.terminal(agent) as mine:
        if not mine or typing_hold(con, agent) or not _own_text_in_box(con, agent, handle):
            return True
        if not rec.get("enters"):
            # Enter is safe in a turn: it only queues the text. Ctrl-C and a retype are not.
            term.send(handle, "")
            log(f"  {agent}: our text was left unsent in its input box; pressed Enter")
            rec.update(enters=1, at=now)
        elif not term.typeable_now(handle):
            return True               # Ctrl-C would cancel a running turn: wait for it to end
        elif not rec.get("retyped"):
            term.send(handle, term.CTRL_C, enter=False)
            time.sleep(term.SHELL_SETTLE_S)
            box = term.composer_on_screen(handle)
            if box is None or box:
                rec.update(blocked=True)        # the box did not clear: typing again would append
                log(f"  {agent}: Enter did not send our text and the box would not clear; giving up")
            else:
                _send(handle, rec["text"])
                log(f"  {agent}: Enter did not send our text; cleared the box and typed it again")
                rec.update(enters=0, at=now, retyped=True)
        else:
            rec.update(blocked=True)
            log(f"  {agent}: our text is still unsent after a second try; giving up")
        if rec.get("blocked"):
            db.add_event(con, agent, "unsent_input_blocked", {})
        _typed_text.pop(handle, None)
        db.set_meta(con, _unsent_key(agent), json.dumps(rec))
    return True


def _mark_typed(con, agent: str, by: dict | None = None) -> None:
    """Called after every successful type at `agent`'s terminal - the other
    half of `_type_gate`'s settle window. Whoever watches the terminal is told, as
    the daemon typing unless `by` names the person whose control call it was."""
    db.set_cursor(con, f"typed_at:{agent}", int(time.time()))
    text = _typed_text.pop(db.get_terminal(con, agent) or "", None)
    if text is not None:
        db.set_meta(con, _unsent_key(agent), json.dumps({"text": text, "at": int(time.time()), "enters": 0,
                                                         "retyped": False, "blocked": False}))
    termchannel.marker(con, agent, "control" if by else "daemon_typed", by or {"kind": "daemon", "name": "daemon"})


def enforce_edges(con, agent: str, log) -> bool:
    """Check every request still waiting for `agent` against the founder's routing
    table, here where the seal on team.yaml can be read (an agent's own session
    cannot read it, so its `ask` checks a file it cannot verify). A request whose
    edge that table does not allow is denied before anyone is told of it. False
    when the table cannot be trusted - changed, unsealed or unreadable: nothing
    is delivered until the founder has looked (T202). A request from a name that
    is not a registered agent (the system's own) is not an agent's edge."""
    if agents.is_structured(agent):
        return True  # the Go daemon drives it
    hold = f"edgehold:{agent}"
    try:
        t = teammod.Team.load()
    except FileNotFoundError:
        for r in db.queued_for(con, agent):
            db.clear_edge(con, int(r["id"]))
        return True
    except (ConfigError, OSError) as e:
        if not db.get_cursor(con, hold):
            db.set_cursor(con, hold, 1)
            log(f"  {agent}: nothing delivered - team.yaml is not trusted ({e})")
        return False
    if protected.in_agent_session() and \
            sealed.state(sealed.TEAM, paths.team_file().read_text()) != "ok":
        # An agent's own push cannot read the seal, so it cannot clear anything.
        return False
    if db.get_cursor(con, hold):
        db.set_cursor(con, hold, 0)
    for r in db.queued_for(con, agent):
        if r["edge_ok"]:
            continue
        if r["requester"] not in t.agents:
            db.clear_edge(con, int(r["id"]))
            continue
        reply = askmod.replies_to(db.get_request(con, r["parent_id"]) if r["parent_id"] else None,
                                  r["requester"], r["target"])
        d = t.route(r["requester"], r["target"], chain_depth=int(r["chain_depth"] or 0), reply=reply)
        if d.allowed:
            db.clear_edge(con, int(r["id"]))
            continue
        db.retire_queued(con, int(r["id"]), "denied", f"refused at delivery: {d.reason}")
        log(f"  {agent}: #{r['id']} from {r['requester']} refused - {d.reason}")
    return True


def head_task_blocked(con, agent: str, head) -> bool:
    """Is `head`'s own task on record as waited on (T337): blocked by another
    still-open task (`hl task edit T<n> --blocked-by T<m>`), or held up on an
    ask `agent` itself sent that is linked to the same task. Built on what
    already exists, not a new kind of wait nothing else reads - a head with
    no task at all is never this, since there is nothing to check it
    against."""
    for t in db.tasks_for_request(con, int(head["id"])):
        tid = int(t["id"])
        if db.task_blockers(con, tid):
            return True
        if any(r["requester"] == agent and int(r["id"]) != int(head["id"])
               and r["state"] == "queued" for r in db.task_requests(con, tid)):
            return True
    return False


def effective_head(con, agent: str):
    """`db.head_of_queue`, unless it is blocked on record (T337) - then the
    next request behind it instead, so the one-at-a-time rule falls on
    whatever the agent can actually act on, not on what it is stuck waiting
    on.

    Read this, not `db.head_of_queue` directly, by anything asking what the
    agent is working on or was told - delivery, `forgotten_resolve`,
    `blocked.work_waiting`, a cold session's own close, a closed agent's wake,
    the resume prompt, the handoff and the progress note, and `hl inbox`'s
    unread view and the stuck screen's own reason for a specific request
    (T337 round 4, auditor #2028's sweep). `db.head_of_queue` itself is for a
    different question - what is structurally first, whatever is or is not
    reachable right now - and every one of its own readers (`hl inbox`
    reading its own queue, the board, a plain queue-not-empty check) keeps
    asking it directly: the true head is never touched by any of this, so it
    is exactly what they keep finding, the moment the block clears.

    Only one level: a request that jumps a blocked head and is itself
    blocked is not chased further. T337 was written for one open head with
    ordinary work queued behind it, not a chain of them."""
    head = db.head_of_queue(con, agent)
    if head and head_task_blocked(con, agent, head):
        return db.head_of_queue(con, agent, skip=int(head["id"]))
    return head


def effective_head_or_marked(con, agent: str):
    """`effective_head`, or - with none - the oldest `send_now_pending` row
    (`db.head_of_queue_or_marked`'s own fallback, T293): a say is never a
    head, but with nothing else queued it is still the one thing waiting -
    what a resumed session opens on, and `hl inbox`'s own unread view."""
    return effective_head(con, agent) or next(iter(db.send_now_pending(con, agent)), None)


def effective_task_head(con, agent: str):
    """`db.task_head`, unless it is blocked on record (`head_task_blocked`, T337):
    then the next task behind it."""
    head = db.task_head(con, agent)
    if head and head_task_blocked(con, agent, head):
        return db.task_head(con, agent, skip=int(head["id"]))
    return head


# What each agent's next task was last said to be held for, so the reason is logged when it
# changes and not on every tick (T456): a stall must never be silent.
_HELD: dict[str, str] = {}
# What stopped each agent's delivery or wake on its latest try, and when (T721): the daemon records
# it per waiting task (go/internal/supervisor/holds.go). A reason not renewed within HELD_FRESH_S has stopped being true.
_NOTED: dict[str, tuple[str, int, bool]] = {}
# A wake or start that failed, kept apart so the delivery that follows in the same tick ("no live
# terminal") does not hide the failure behind it.
_FAILED: dict[str, tuple[str, int]] = {}
HELD_FRESH_S = 30


def note_failure(agent: str, why: str | None) -> None:
    """Remember why `agent`'s wake or start failed this tick; None forgets it."""
    if why:
        _FAILED[agent] = (why, int(time.time()))
    else:
        _FAILED.pop(agent, None)


def note_hold(agent: str, why: str | None, contestable: bool = False) -> None:
    """Remember why work could not reach `agent` this tick; None forgets it. `contestable` is for a
    reason that a screen read gives and the agent's own state may contradict - not one that is a fact."""
    if why:
        _NOTED[agent] = (why, int(time.time()), contestable)
    else:
        _NOTED.pop(agent, None)


def failed_reason(agent: str, now: int) -> str | None:
    """Why `agent`'s latest wake or start failed, or None when that is not recent."""
    failed = _FAILED.get(agent)
    return failed[0] if failed and now - failed[1] <= HELD_FRESH_S else None


def held_reason(agent: str, now: int) -> tuple[str, bool] | None:
    """(why, contestable) `agent`'s work was not typed on its latest try, or None when that is not recent."""
    noted = _NOTED.get(agent)
    return (noted[0], noted[2]) if noted and now - noted[1] <= HELD_FRESH_S else None


def _held(agent: str, head, why: str, log, contestable: bool = False) -> bool:
    """Say once per reason why `head` is not typed at `agent` yet; always False, the delivery's answer."""
    note_hold(agent, why, contestable)
    if _HELD.get(agent) != why:
        _HELD[agent] = why
        log(f"  {agent}: #{head['id']} is held - {why}")
    return False


def _untold_behind(con, agent: str, head) -> list:
    """The tasks behind `head` that nobody has told the agent about and that are not themselves
    blocked on record, in queue order."""
    return [r for r in db.queued_for(con, agent)
            if r["kind"] == "task" and r["edge_ok"] and int(r["id"]) != int(head["id"])
            and not r["delivered_at"] and not head_task_blocked(con, agent, r)]


# What is queued for an agent goes out together, once a minute, not one task at a time
# behind an idle wait. When the last batch went out is `batch:<agent>` in the db, so a restart neither
# sends early nor forgets what is still owed: whether a batch is due is read from it, never from a timer.
BATCH_EVERY_S = 60


def _urgent(r) -> bool:
    """A batch holding this goes out at once: the founder's own request, or one marked send-now."""
    return r["requester"] == db.HUMAN or db.tells_now(r)


def _batch_due(con, agent: str, rows, fresh_session: bool) -> bool:
    if fresh_session or any(_urgent(r) for r in rows):
        return True          # a session that knows nothing of its queue (new, or voided): not a batch
    return int(time.time()) - (db.get_cursor(con, f"batch:{agent}") or 0) >= BATCH_EVERY_S


def deliver_queued(con, agent: str, log) -> bool:
    """Tell an agent about the tasks queued for it - only tasks: a message is typed at once
    (`messaging.tell_now`) and never comes here. Everything not yet told goes out as one
    message once a minute, or at once when it holds the founder's request or one marked send-now;
    only one task is the agent's to do at a time, the rest are told so it knows what is queued.
    Told once per task: the cursor holds the id last announced. Whatever holds the batch back is
    logged, once per reason.
    """
    if agents.is_structured(agent):
        return False  # the Go daemon drives it
    if not enforce_edges(con, agent, log):
        return False
    head = effective_task_head(con, agent)
    if not head:
        _HELD.pop(agent, None)
        note_hold(agent, None)
        return False
    head_due = db.get_cursor(con, f"notify:{agent}") != int(head["id"]) \
        or _session_changed(con, agent)
    if head_due and db.told_ahead(con, agent, int(head["id"])) and not _session_changed(con, agent):
        # Told already, out of turn, to the session that is running; becoming the head is not news.
        db.set_cursor(con, f"notify:{agent}", int(head["id"]))
        head_due = False
    ahead_of = None
    if head_due:
        batch = [head] + _untold_behind(con, agent, head)
    else:
        # The head was told and is still open: what is behind it is told as a batch.
        ahead_of = head
        batch = _untold_behind(con, agent, head)
        if not batch:
            _HELD.pop(agent, None)
            note_hold(agent, None)
            return False
        head = batch[0]
    fresh = head_due and (_session_changed(con, agent) or not db.get_cursor(con, f"notify:{agent}"))
    if not _batch_due(con, agent, batch, fresh):
        return False                                  # collected; goes out when the minute is up
    why = typing_hold(con, agent)
    if why:
        return _held(agent, head, why, log)          # stays queued; told when it wakes or moves
    handle = db.get_terminal(con, agent)
    if not handle or not term.available() or not term.alive(handle):
        return _held(agent, head, "it has no live terminal", log)
    if not session_present(agent):
        # Nothing here is read by an agent that is not running; at a shell
        # prompt this line is four commands in backticks and a redirection.
        # Leaving it queued loses nothing: the sweep records the agent closed
        # within a tick, and work arriving at a closed agent wakes it.
        return _held(agent, head, "no session is running in its terminal", log)
    # Typing at an agent mid-turn leaves the text sitting in its input box
    # unsent, which is how three agents once came up having been told nothing.
    if not term.typeable_now(handle):
        # An open dialog is a reason the agent cannot take it, not a delivery gone wrong: only a screen
        # that is busy with nothing to explain it (the row says idle) is worth contesting.
        blocker = term.screen_blocker(handle)
        if blocker is not None and blocker.kind != term.UNREADABLE:
            return _held(agent, head, blocker.reason(), log)
        return _held(agent, head, "its screen is not idle (mid-turn, or unreadable)", log, contestable=True)
    depth = sum(1 for r in db.queued_for(con, agent) if r["kind"] == "task")
    with lock.terminal(agent) as mine:
        if not mine:
            return _held(agent, head, "its terminal is locked by another send", log, contestable=True)
        if _type_gate(con, agent, handle):
            return _held(agent, head, "its screen changed before the send", log, contestable=True)
        told = _tell_batch(con, agent, batch, depth, handle, log, ahead_of)
        if told:
            db.set_cursor(con, f"batch:{agent}", int(time.time()))
            _HELD.pop(agent, None)
            note_hold(agent, None)
        return told


# An agent is asked to resolve the request at the head of its queue once it has
# been idle this long with nothing of its own outstanding. Ten minutes: a turn
# that ended and left the request open is not thinking, and a real wait on
# something that is not an ask (a long job) can still say so when it is asked;
# shorter would nag an agent between two turns of a task.
NUDGE_AFTER_S = 600
# Once reminded, an agent still idle on the request this long after it last did
# anything is what the human is shown. The nudge is typed as a prompt, so the
# agent's answer to it counts as activity and this runs from there. Thirty
# minutes is the longest silence the daemon calls healthy (STUCK_RUNNING_S), so
# the screen and the RUNNING label agree on what stuck is.
FORGOTTEN_STUCK_S = STUCK_RUNNING_S
# A nudge that could not be typed (mid-turn, a modal) is not asked again for
# this long, because the idle probe that says no costs its whole timeout.
NUDGE_RETRY_S = 60
# How many times a forgotten head is nudged, and how long it must have sat
# idle for each: ten minutes, thirty, then two hours - not evenly spaced,
# because being asked once already and still not answering is worth waiting
# longer on before asking again. Past the last one nothing more is typed at
# the agent (T337 founder #2002/#2003: repeating this forever at an agent
# that turns out to be genuinely stuck, not forgetful, has a real cost of its
# own); `timebox.sweep_forgotten_resolve_capped` tells its supervisor once
# instead.
NUDGE_SCHEDULE_S = (NUDGE_AFTER_S, FORGOTTEN_STUCK_S, 2 * 3600)


def track_background_work(con, agent: str, row) -> None:
    """Record whether an open session's own footer reports work still running
    behind its prompt, in whatever state it is waiting.

    A session with such work is not idle: the display shows it working, and
    nothing that acts on idleness (the resolve nudge, the idle close) acts on
    it. The session reports this, so it is read every tick, never assumed from
    a clock - RUNNING included (founder #1975/#2125): `screen_state` shows the
    same background detail while RUNNING, and a cursor frozen from whenever
    background work was first seen, however long ago, would make that detail
    wrong for as long as the turn runs. When the footer stops reporting it,
    the idle clock starts there (`idle_since`), not at its last hook event;
    the end is not a model turn, so it stays out of `db.last_activity`, which
    the prompt cache is judged by."""
    if agents.is_structured(agent):
        return None  # the Go daemon drives it
    key = f"bgwork:{agent}"
    if row and (row["closed_at"] or not row["terminal"]):
        db.clear_cursor(con, key)      # the session is gone, and so is what ran behind its prompt
        db.clear_cursor(con, f"bgcount:{agent}")
        return
    if not row or row["state"] in (HANDOFF, STALLED) or not term.available():
        return
    running = term.background_work(row["terminal"])
    if running is None:
        return                # unreadable is not "ended"
    if running:
        db.set_cursor(con, f"bgcount:{agent}", max(1, bgjobs.footer_count(running)))
        screensamples.record(con, agent, row, running, term.last_tail(row["terminal"]))
    if running and not db.get_cursor(con, key):
        db.set_cursor(con, key, 1)
    elif not running and db.get_cursor(con, key):
        db.clear_cursor(con, key)
        db.clear_cursor(con, f"bgcount:{agent}")
        db.add_event(con, agent, "background_work_ended", {})


def idle_since(con, agent: str) -> int | None:
    """When this agent last did anything, its background work last ended, or its session was last
    brought back: a resume nobody typed at starts no turn, and its idle window still starts then."""
    times = [t for t in (db.last_activity(con, agent),
                         db.last_event_ts(con, agent, "background_work_ended"),
                         db.last_event_ts(con, agent, "wake")) if t is not None]
    return max(times) if times else None


def has_background_work(con, agent: str) -> bool:
    """A closed session runs nothing, whatever the cursor still says from before it closed."""
    row = db.get_agent_state(con, agent)
    return bool(db.get_cursor(con, f"bgwork:{agent}")) and not (row and row["closed_at"])


# Background work the agent has shown nothing beside this long is stale: forgotten
# rather than being waited on. Under the hour a prompt cache lives (RESUME_WARM_S),
# so it is caught while a resume would still have been warm (founder #435).
STALE_BG_S = 3000
# Once the supervisor has been told, this long passes before the work may be
# stopped: room to say no, by giving the agent something to do or with
# `hl idle-timeout never --agent`. (An agent that owes nothing is not asked about at
# all: `bg_owes_nothing`, `idle_close.bg_notice_s`, `idle_close.bg_grace_s`.)
STALE_BG_VETO_S = 600


def bg_owes_nothing(con, agent: str) -> bool:
    """No request queued for it, none it asked and still waits on, no task it is doing."""
    from .. import actions            # imports this module
    if db.queued_for(con, agent) or db.open_ask_ids_of(con, agent):
        return False
    tid = db.current_task(con, agent)
    return not (tid is not None and actions.reads_doing(con, tid))


def bg_prompted_at(con, agent: str) -> int:
    """When the agent was told to finish or stop its background work, in this stretch."""
    return db.get_cursor(con, f"bg_prompt:{agent}")


def bg_since(con, agent: str) -> int | None:
    """When the daemon first saw this agent's session report background work
    in this stretch, or None when it does not."""
    return db.cursor_updated(con, f"bgwork:{agent}") if has_background_work(con, agent) else None


def bg_last_shown(con, agent: str) -> int | None:
    """When the agent last showed anything, not counting the turns that answered a T450
    check: each check asks for an answer, and an agent that gives one every N minutes would
    otherwise never be silent long enough to be told to stop, or to be stale. A check's
    answer turn runs from its typing to the first Stop after it - that turn only."""
    since = bg_since(con, agent) or 0
    checks = [r["id"] for r in con.execute(
        "SELECT id FROM events WHERE agent = ? AND kind = 'background_checked' AND ts >= ? ORDER BY id",
        (agent, since))]
    if not checks:
        return db.last_activity(con, agent)
    marks = ",".join("?" * len(db.AGENT_ACTIVITY_KINDS))
    rows = con.execute(f"SELECT id, ts, kind FROM events WHERE agent = ? AND id > ? "
                       f"AND kind IN ({marks}) ORDER BY id", (agent, checks[0], *db.AGENT_ACTIVITY_KINDS)).fetchall()
    first = con.execute(f"SELECT MAX(ts) AS t FROM events WHERE agent = ? AND id < ? AND kind IN ({marks})",
                        (agent, checks[0], *db.AGENT_ACTIVITY_KINDS)).fetchone()["t"]
    shown, answering, i = (int(first) if first is not None else None), False, 0
    for r in rows:
        while i < len(checks) and checks[i] < r["id"]:      # by event order, not by clock
            answering, i = True, i + 1
        if not answering:
            shown = int(r["ts"])
        elif r["kind"] in ("Stop", "StopFailure"):
            answering = False
    return shown


def bg_silent_s(con, agent: str, now: int) -> int | None:
    """How long this agent has shown nothing while its session reports
    background work, or None without any. Work that notifies the model or is
    waited on shows up as activity; hours without it is a shell nobody reads."""
    since = bg_since(con, agent)
    if since is None:
        return None
    return max(0, now - (bg_last_shown(con, agent) or since))


def bg_stale(con, agent: str, now: int) -> bool:
    """Forgotten background work: shown nothing beside it for STALE_BG_S. Never while the agent is
    RUNNING (T359) - one foreground step can run that long with no hook event, and a turn under
    way is not forgotten work; a turn stuck that long is `unasked_running`'s to report."""
    row = db.get_agent_state(con, agent)
    if row and row["state"] == RUNNING:
        return False
    silent = bg_silent_s(con, agent, now)
    return silent is not None and silent >= idle_close.config_seconds("stale_background_s", STALE_BG_S)


def bg_may_stop(con, agent: str, now: int) -> bool:
    """An agent that owes nothing: told to finish or stop the work, and shown nothing
    for its grace window since. Any other: stale, and told to its supervisor at least
    STALE_BG_VETO_S ago. Never an agent whose close timeout is NEVER, the veto."""
    if idle_close.close_timeout_s(agent) == idle_close.NEVER:
        return False
    if bg_owes_nothing(con, agent):
        prompted = bg_prompted_at(con, agent)
        return bool(prompted) and has_background_work(con, agent) and now - max(
            prompted, db.last_activity(con, agent) or 0) >= idle_close.bg_grace_s(agent)
    told = db.get_cursor(con, f"stale_bg_told:{agent}")
    return bool(told) and now - told >= STALE_BG_VETO_S and bg_stale(con, agent, now)


def bg_holds(con, agent: str, now: int) -> bool:
    """Does background work keep this agent from being closed or typed past?"""
    return has_background_work(con, agent) and not bg_may_stop(con, agent, now)


def _waiting_on_ask(con, agent: str, head) -> bool:
    """Does `agent` have an open ask it may be waiting on instead of `head`?
    Not the ones that are why a task ahead of `head` is blocked (`head_task_blocked`,
    T337): `head` stands in for that task, so the agent is meant to be working it
    while they are open (T343)."""
    asks = set(db.open_ask_ids_of(con, agent))
    true_head = db.task_head(con, agent)
    if asks and true_head and int(true_head["id"]) != int(head["id"]) \
            and head_task_blocked(con, agent, true_head):
        for t in db.tasks_for_request(con, int(true_head["id"])):
            asks -= {int(r["id"]) for r in db.task_requests(con, int(t["id"]))}
    return bool(asks)


def forgotten_resolve(con, agent: str, row, now: int) -> dict | None:
    """What the agent is holding, if it looks like a resolve nobody wrote: its next
    task (`effective_task_head`) when that was told and is still open.

    IDLE or QUEUED with a live terminal, delivered and told and still open, and
    nothing the agent asked that is still unanswered - which is what separates a
    request forgotten from one being waited on, since an agent waiting on an answer is
    also IDLE (or QUEUED) with its request open. Idle time counts from the later of its
    last activity and the delivery.

    QUEUED counts as IDLE here: it only means something is queued, and only a
    fresh prompt clears it, so a QUEUED agent whose task was told in its current
    session is the same forgotten resolve. `blocked.work_waiting` reads this function,
    so it also decides whether that agent is reported.

    A task blocked on record (T337) is never this on its own account:
    `effective_task_head` already looked past it.
    """
    if not row or row["state"] not in (IDLE, QUEUED) or row["closed_at"] or not row["terminal"]:
        return None
    if has_background_work(con, agent) and not bg_stale(con, agent, now):
        return None           # not idle: work it started is still running
    head = effective_task_head(con, agent)
    if not head or not head["delivered_at"] or db.get_cursor(con, f"notify:{agent}") != int(head["id"]):
        return None
    if _waiting_on_ask(con, agent, head) or db.unannounced_answers(con, agent):
        return None
    idle = now - max(int(head["delivered_at"]), idle_since(con, agent) or 0)
    return {"head": head, "idle_s": idle, "behind": db.queue_depth(con, agent) - 1}


# Claude Code hands typed text of four or more lines to the model as a paste,
# inside <pasted_content>, whose instructions it follows only where the user's
# own message asks. Every delivery of four or more lines in the agents'
# transcripts arrived wrapped (9 of 9), none of three or fewer (66 of 66), and
# orcapipe.ui declined a wrapped task as untrusted (T105).
MAX_TYPED_LINES = 3


def record_cleared(handle: str, rows: list[str]) -> None:
    """Before `term.send_to_session` sends Ctrl-C at `handle`: an event under the agent last recorded
    there, with the screen rows that read as a shell, so a Ctrl-C that interrupted a live turn is found
    from the record."""
    con = db.connect()
    try:
        who = db.agent_of_terminal(con, handle)
        if who:
            db.add_event(con, who, "shell_cleared", {"terminal": handle, "screen": rows})
    finally:
        con.close()


term.on_cleared = record_cleared


def _send(handle: str, text: str) -> tuple[bool, str]:
    """`term.send` for text an agent is meant to act on: never more than
    `MAX_TYPED_LINES` lines. Past that the middle lines are joined into one; a
    sender still builds its own three lines so nothing important is squeezed."""
    lines = text.split("\n")
    if len(lines) > MAX_TYPED_LINES:
        keep = MAX_TYPED_LINES - 2
        lines = lines[:keep] + [" ".join(lines[keep:-1])] + lines[-1:]
    text = "\n".join(lines)
    ok, out = term.send_to_session(handle, text)
    if ok:
        _typed_text[handle] = text
    return ok, out


def type_notice(con, agent: str, text: str) -> bool:
    """Type one line at an agent's open session, if it may be typed at now.

    The gates every unprompted line goes through: not at the wall, a live
    session on a live terminal, nothing on screen that a line would answer, the
    terminal lock held and the screen read again once it is. False for any of
    them, or a send that failed; the caller decides when to try again. Nothing
    here touches an attempt counter, so a notice cannot stall an agent.
    """
    if agents.is_structured(agent):
        return False  # the Go daemon drives it
    handle = db.get_terminal(con, agent)
    if typing_hold(con, agent) or not handle or not term.available() \
            or not term.alive(handle) or not session_present(agent) \
            or not term.typeable_now(handle):
        return False
    with lock.terminal(agent) as mine:
        if not mine or _type_gate(con, agent, handle):
            return False
        ok, _ = _send(handle, text)
        if ok:
            _mark_typed(con, agent)
    return ok


def nudge_count(con, agent: str, head_id: int) -> int:
    """How many times the current head has already been nudged - 0 for a
    head that is not the one the `nudge:{agent}` cursor names, since that
    count belongs to whatever head earned it, not this one."""
    return db.get_cursor(con, f"nudge_count:{agent}") \
        if db.get_cursor(con, f"nudge:{agent}") == int(head_id) else 0


def nudge_exhausted(con, agent: str, head_id: int) -> bool:
    """True once `NUDGE_SCHEDULE_S` is used up for this head - nothing more
    will be typed at the agent about it; `timebox.sweep_forgotten_resolve_capped`
    is what tells its supervisor from here."""
    return nudge_count(con, agent, head_id) >= len(NUDGE_SCHEDULE_S)


def nudge_forgotten_resolve(con, agent: str, row, now: int, log) -> bool:
    """Type a reminder at an agent that has left its head request open, again
    at each rung of `NUDGE_SCHEDULE_S` for as long as that stays true, up to
    its last one.

    A single reminder used to be the last thing the daemon ever did about it:
    past that, only `actions.stuck` (the founder's own screen) would notice,
    and nothing typed at the agent again - so a forgotten head, and whatever
    piled up queued behind it, stayed silent until a human read the screen and
    acted (T337, founder #2002/#2003). The same silence that earns the first
    nudge earns the next rung too, up to the cap `NUDGE_SCHEDULE_S` sets.

    `forgotten_resolve` already reads the queue through `effective_task_head`,
    falling back to the oldest typed open ask, so `found["head"]` is never a
    task blocked on record - only one that is not, or a message, is ever
    this. The `head_task_blocked` check below is for a task behind it turning
    out to be blocked too (`effective_task_head` looks past one level, not a
    chain): nothing is typed about it either, the same as the true head - not
    forgotten, just not reachable from here.

    Never resolves for it: the verdict is the agent's to write. Gated like a
    delivery (not at the wall, nothing typed at a modal or a working agent),
    and nothing here touches an attempt counter, so it cannot stall an agent.
    """
    if agents.is_structured(agent):
        return False  # the Go daemon drives it
    found = forgotten_resolve(con, agent, row, now)
    if not found:
        return False
    head = found["head"]
    if head_task_blocked(con, agent, head):
        return False
    count = nudge_count(con, agent, int(head["id"]))
    if count >= len(NUDGE_SCHEDULE_S) or found["idle_s"] < NUDGE_SCHEDULE_S[count]:
        return False
    if now - db.get_cursor(con, f"nudge_try:{agent}") < NUDGE_RETRY_S:
        return False
    db.set_cursor(con, f"nudge_try:{agent}", now)
    behind = found["behind"]
    idle = timefmt.ago(found["idle_s"])
    after = f" with {behind} more waiting behind it" if behind else ""
    task = _task_of(con, head) if head["kind"] == "task" else None
    if task is not None:
        tid = task["id"]
        text = (f"T{tid} (#{head['id']}) is still yours and you have been idle {idle}{after}. "
                f"Done: hand it on (`hl task handoff T{tid} ...`) or `hl task done T{tid}`. "
                f"Waiting on something: record it - `hl task edit T{tid} --blocked-by T<m>`, "
                f"or a child or decision task.")
    else:
        what = (f"If the work is done, {answer_verb(con, head)}; if it is not, say what you are "
                f"waiting on.")
        text = (f"#{head['id']} was delivered to you and is still open, and you have been "
                f"idle {idle}{after}. {what}")
    ok = type_notice(con, agent, text)
    if ok:
        db.set_cursor(con, f"nudge:{agent}", int(head["id"]))
        db.set_cursor(con, f"nudge_count:{agent}", count + 1)
        db.add_event(con, agent, "nudged", {"request_id": head["id"],
                                            "idle_s": found["idle_s"], "behind": behind})
        log(f"  {agent}: idle {timefmt.ago(found['idle_s'])} with #{head['id']} open and "
            f"nothing outstanding - nudged to resolve it")
    return ok


def notify_cancelled(con, row, verdict: str) -> tuple[bool, str]:
    """Type at an agent that a request it was handed has been closed under it.

    It may be mid-work on it, so it is told the way a delivery is - the same
    gates - and (typed, why not) says whether that happened. Not telling it is
    safe: the row is closed, so an answer to it is refused.
    """
    agent = row["target"]
    handle = db.get_terminal(con, agent)
    held = typing_hold(con, agent)
    if held:
        return False, held
    if not handle or not term.available() or not term.alive(handle):
        return False, f"{agent} has no terminal"
    if not session_present(agent):
        return False, f"{agent} has no running session"
    if not term.typeable_now(handle):
        return False, f"{agent} is mid-work or at a prompt"
    with lock.terminal(agent) as mine:
        if not mine:
            return False, f"something else is typing at {agent}"
        why = _type_gate(con, agent, handle)
        if why:
            return False, why
        held = ""
        task = _task_of(con, row) if row["kind"] == "task" else None
        if task is not None:
            from .. import actions
            held = (f", and T{task['id']} is {actions.task_state(con, task)} - "
                    f"`hl task list` shows what you hold")
        ok, _ = _send(
            handle,
            f"#{row['id']} from {row['requester']} was cancelled: {verdict}. Stop work "
            f"on it; it is closed and needs no resolve{held}. `hl read {row['id']}` shows it.")
        if ok:
            _mark_typed(con, agent)
    return ok, "" if ok else "the send failed"


# The body `actions.tasks.task_handoff` sends: "T<n> handed to you by X: <title>\n\n<note>".
_HANDOFF_BODY = re.compile(r"\AT(\d+) handed to you by (\S+?): (.*?)\n\n(.*)\Z", re.S)
TITLE_SHOWN = 60


def handoff_parts(body: str | None) -> tuple[int, str, str, str] | None:
    """(task id, who handed it, title, note) of a task handoff's body, or None for any other."""
    m = _HANDOFF_BODY.match(body or "")
    return (int(m[1]), m[2], m[3].strip(), m[4].strip()) if m else None


def _task_of(con, req):
    """The task a kind-task request is about: the one it is linked to, else the one its body names."""
    linked = db.tasks_for_request(con, int(req["id"]))
    if linked:
        return linked[-1]
    parts = handoff_parts(req["body"])
    return db.get_task(con, parts[0]) if parts else None


def answer_verb(con, head) -> str:
    """How the agent answers what it was just told (T361). Everything is a message or a task:
    a task is handed on or finished; the founder is always answered through ocp, since a reply
    typed in the terminal reaches no one (T361, founder #6166); another agent's request is
    answered by a reply that names it; a say or a report asks nothing."""
    if head["kind"] == "task":
        task = _task_of(con, head)
        if task is not None:
            return (f"you own T{task['id']} now: hand it on with "
                    f"`hl task handoff T{task['id']} <agent> \"<note>\"`, or "
                    f"`hl task done T{task['id']}` if you are its last step")
    if head["requester"] == db.HUMAN:
        return (f"answer the founder now, before other work: `hl say human --parent "
                f"{head['id']} \"...\" --task T<n>` (or `--no-task \"<why>\"`) - if it is work, "
                f"one line on what you will do or who has it")
    if head["kind"] == "say":
        return "it asks nothing"
    return f"answer with `hl say {head['requester']} --parent {head['id']} \"<answer>\"`"


def more_waiting(kind: str, more: int) -> str | None:
    """What an agent is told about the queue behind the request it reads, or None. A message
    needs no answer, so it gates nothing and says nothing; a task's queue moves on when the
    task is handed on or finished."""
    if not more or kind == "say":
        return None
    if kind == "task":
        return f"{more} more waiting - the next comes when you hand this on or finish it."
    return f"{more} more waiting - the next comes when you answer this one."


# What one typed delivery carries of the notices waiting for its target (T935): a notice is never
# typed on its own turn, so these ride on the next real delivery, and the rest stay for the one after.
NOTICES_SHOWN = 3
NOTICE_SHOWN = 160


def folded_notices(con, agent: str) -> list:
    """The notices the next typed delivery to `agent` carries; `tell_text` shows them,
    and the caller marks them shown (`db.mark_notices_shown`) once the send went through."""
    return db.pending_notices(con, agent)[:NOTICES_SHOWN]


def notices_text(con, agent: str, folded) -> str:
    if not folded:
        return ""
    lines = []
    for r in folded:
        t = textsafe.oneline(r["body"])
        # A cut notice names its id: the cut part held the result's paths and caveats.
        lines.append(t if len(t) <= NOTICE_SHOWN else f"{t[:NOTICE_SHOWN].rstrip()}… (hl read {r['id']})")
    left = len(db.pending_notices(con, agent)) - len(folded)
    return " Also: " + " | ".join(lines) + (f" (+{left} more: `hl inbox --all`)" if left else "")


def tell_text(con, head, depth: int, ahead_of=None, parked: bool = False, folded=()) -> str:
    """The message that tells an agent about `head`, as it is typed.

    `ahead_of` is the head a "send now" request is jumping, or, with `parked`, the open head the
    task is queued behind. `folded` is `folded_notices`, carried on the same line."""
    more = depth - 1
    # One line; `hl read` has the rest. The message is three lines at most
    # (`MAX_TYPED_LINES`), so what follows the body rides on its line. A task handoff
    # shows its whole note first and cuts only the title: every handoff of a long-titled
    # task starts with the same title, so a cap over both hid the note (#2377).
    parts = handoff_parts(head["body"]) if head["kind"] == "task" else None
    if parts:
        tid, _, title, note = parts
        note = textsafe.oneline(note) or "(no note)"
        title = textsafe.oneline(title)
        shown = (f"T{tid} handed to you. Note: {note} "
                 f"Title: {title if len(title) <= TITLE_SHOWN else title[:TITLE_SHOWN].rstrip() + '…'}")
    else:
        body = textsafe.oneline(head["body"] or "").strip() or "(empty)"
        # A reply to an earlier request is a verdict or an answer: cut, it hides the verdict.
        whole = head["parent_id"] is not None
        whole = whole or head["requester"] == db.NOTICE_REQUESTER and body.startswith(db.DIGEST_HEAD)
        shown = body if whole or len(body) <= 400 else body[:400] + "…"
    if head["kind"] == "task":
        from ..actions import tasks as taskmod      # actions -> daemon
        task = _task_of(con, head)
        memos = taskmod.memo_summary(con, task["id"]) if task is not None else ""
        if memos:
            shown += f" {memos}"
    shown += notices_text(con, head["target"], folded)
    waiting = more_waiting(head["kind"], more)
    tail = f" [{waiting}]" if waiting else ""
    if parked:
        tail = f" [Queued behind #{ahead_of['id']}, which is still open: the next task comes when you hand it on or finish it.]"
    elif ahead_of is not None:
        tail = (f" [A follow-up to #{ahead_of['id']}, which is still open.]"
                if head["parent_id"] == ahead_of["id"] else
                f" [Sent now, ahead of #{ahead_of['id']}, which is still open.]")
    reply = in_reply_to(con, head)
    return (f"#{head['id']} from {head['requester']}{f' ({reply})' if reply else ''}, waiting for you:\n{shown}{tail}\n"
            f"`hl read {head['id']}` shows it whole; {answer_verb(con, head)}"
            + ("." if head["requester"] == db.HUMAN or head["kind"] == "say"
               else ", or `hl inbox` any time."))


def _record_told(con, agent: str, head, depth: int, log, session: str | None,
                 ahead_of=None, parked: bool = False) -> None:
    """What a delivered tell leaves behind, once it is known to have landed."""
    from . import landing        # imports this module
    more = depth - 1
    db.mark_typed(con, int(head["id"]), session or _live_session(con, agent))
    landing.note_typed(int(head["id"]))
    if ahead_of is None:
        db.set_cursor(con, f"notify:{agent}", int(head["id"]))
        db.set_notified_session(con, agent, session)
    else:
        db.mark_told_ahead(con, agent, int(head["id"]), [int(r["id"]) for r in db.queued_for(con, agent)])
    db.add_event(con, agent, "notified", {"request_id": head["id"], "more_waiting": more})
    log(f"  {agent}: told about #{head['id']}"
        + (f" (behind #{ahead_of['id']})" if parked else " (send now)" if ahead_of is not None else "")
        + (f" ({more} more waiting)" if more else ""))


def _tell(con, agent: str, head, depth: int, handle: str, log, ahead_of=None, parked: bool = False) -> bool:
    """Tell `agent` about the one task `head`."""
    return _tell_batch(con, agent, [head], depth, handle, log, ahead_of, parked)


BATCH_LINE = 160


def batch_text(con, batch, folded, ahead_of=None) -> str:
    """One message for several queued tasks: a line each, then how to work them. The three-line cap
    (`_send`) joins the middle lines, so the header and the footer stay apart."""
    lines = []
    for r in batch:
        parts = handoff_parts(r["body"])
        text = (f"T{parts[0]} handed to you: {parts[2]} - {parts[3] or '(no note)'}" if parts
                else r["body"] or "(empty)")
        text = textsafe.oneline(text).strip()
        if len(text) > BATCH_LINE:
            text = f"{text[:BATCH_LINE].rstrip()}…"
        lines.append(f"#{r['id']} from {r['requester']}: {text}")
    head = (f"{len(batch)} more tasks queued behind #{ahead_of['id']}, which is still open:"
            if ahead_of is not None else f"{len(batch)} tasks waiting for you:")
    foot = ("`hl read <id>` shows one whole. You do one at a time, in this order: each ends with "
            "`hl task done T<n>` or `hl task handoff T<n> <agent> \"<note>\"`."
            + notices_text(con, batch[0]["target"], folded))
    return "\n".join([head, *lines, foot])


def _tell_batch(con, agent: str, batch, depth: int, handle: str, log, ahead_of=None,
                parked: bool | None = None) -> bool:
    """Type `batch` (the tasks no one has told the agent about, in queue order) as one message and
    record each as told. One task is `_tell`'s own message. Each is marked delivered before the send
    (as `_tell` is: it closes withdraw/replace's race) and put back as owed when the send fails.
    `ahead_of` is the open head the batch is queued behind; `parked` says so (the default when it is
    given), else it is a "send now" request jumping that head."""
    from . import landing        # imports this module
    parked = ahead_of is not None if parked is None else parked
    live, landed = [], False
    for r in batch:
        if not db.mark_delivered(con, int(r["id"])):
            continue                 # retired in the gap since it was listed
        if landing.landed_already(con, agent, int(r["id"])):
            # the transcript first - a line it already holds is not typed again.
            db.mark_typed(con, int(r["id"]), _live_session(con, agent))
            db.mark_landed(con, int(r["id"]))
            _record_told(con, agent, r, depth, log, None, ahead_of, parked)
            landed = True
            continue
        live.append(r)
    if not live:
        return landed
    folded = folded_notices(con, agent)
    if len(live) == 1:
        text = tell_text(con, live[0], depth, ahead_of, parked, folded)
    else:
        text = batch_text(con, live, folded, ahead_of)
    ok, _ = _send(handle, text)
    if not ok:
        for r in live:
            db.clear_delivered(con, int(r["id"]))
        return False
    _mark_typed(con, agent)
    session = _live_session(con, agent)
    db.mark_notices_shown(con, [int(r["id"]) for r in folded], session)
    for n, r in enumerate(live):
        if n == 0:
            _record_told(con, agent, r, depth, log, session if ahead_of is None else None,
                         ahead_of, parked)
            continue
        # Told with the first, to the session that is running: becoming the head is not news.
        db.mark_typed(con, int(r["id"]), session)
        landing.note_typed(int(r["id"]))
        db.mark_told_ahead(con, agent, int(r["id"]), [int(q["id"]) for q in db.queued_for(con, agent)])
        db.add_event(con, agent, "notified", {"request_id": r["id"], "more_waiting": depth - 1})
        log(f"  {agent}: told about #{r['id']} (batched with #{live[0]['id']})")
    return True


def waiting_prompt(con, agent: str, log):
    """The message waiting for a parked agent, as the first prompt of the
    session that resumes it, and what to record once that session is up -
    or None when nothing waits.

    It is the tell `deliver_queued` and `deliver_answers` would type on the
    next tick, so the resume does not say "carry on" and then say the message.
    Marked delivered here, before the launch, for the reason `_tell` is."""
    if not enforce_edges(con, agent, log):
        return None
    head = effective_head_or_marked(con, agent)
    if head:
        if not db.mark_delivered(con, int(head["id"])):
            return None
        depth = db.queue_depth(con, agent)

        def told(session):
            from . import messaging      # imports this module
            _record_told(con, agent, head, depth, log, session)
            if head["kind"] == "say":
                messaging.finish_say(head)
        folded = folded_notices(con, agent)

        def told_with_notices(session):
            told(session)
            db.mark_notices_shown(con, [int(r["id"]) for r in folded], session)
        return tell_text(con, head, depth, folded=folded), told_with_notices
    answered = db.unannounced_answers(con, agent)
    if answered:
        return answers_text(answered), lambda session: _record_answers(con, agent, answered, log)
    # T351: a pending message (messaging.py) is the same kind of "owed" as a
    # head or an answer - `lifecycle.waiting_for` already counts it, and a
    # resume it woke for has to have this in step or "nothing to resume it
    # for" fires on every one of them. Typed as-is, not wrapped like a
    # request's tell: `wake_prompt`'s `resumed` branch returns it unchanged.
    pending = db.list_pending(con, agent)
    if pending:
        row = pending[0]
        return row["text"], lambda session: db.delete_pending(con, int(row["id"]))
    return None


# How much of the original question an answer line quotes.
ASKED_SHOWN = 40


def in_reply_to(con, row) -> str:
    """`in reply to #M: <first line of M>` for a request with a parent, so a
    bare "#N from human" says what it answers; "" for one without, or whose
    parent is gone."""
    if row["parent_id"] is None:
        return ""
    parent = db.get_request(con, int(row["parent_id"]))
    if parent is None:
        return ""
    lines = (parent["body"] or "").splitlines()
    first = textsafe.oneline(lines[0]).strip() if lines else ""
    if len(first) > ASKED_SHOWN:
        first = first[:ASKED_SHOWN].rstrip() + "…"
    return f"in reply to #{parent['id']}{': ' + first if first else ''}"


def answer_line(r) -> tuple[str, bool]:
    """One answered request as the line typed at its asker, and whether the
    answer has more than that line to read. A dismissal is said as a label.

    The start of the question rides along: a bare number is not something an
    agent can place once it has asked several things."""
    text = db.shown_verdict(r) or ""
    said = (text.splitlines() or [r["state"]])[0]
    asked = textsafe.oneline(r["body"] or "").strip()
    if len(asked) > ASKED_SHOWN:
        asked = asked[:ASKED_SHOWN].rstrip() + "…"
    parts = handoff_parts(r["body"]) if r["kind"] == "task" else None
    if parts:
        head = f"#{r['id']} (your handoff of T{parts[0]} to {r['target']})"
    else:
        head = f"#{r['id']} (you asked: {asked})" if asked else f"#{r['id']}"
    return f"{head} {r['target']}: {said[:160]}", len(said) > 160 or "\n" in text


def answers_text(answered) -> str:
    """What is typed at an agent about its answered requests."""
    shown, rest = answered[-3:], answered[:-3]
    lines, cut = [], False
    for r in shown:
        line, more = answer_line(r)
        cut = cut or more
        lines.append(line)
    if rest:
        # Named, not counted: every id that is marked told has to have been
        # said, or this is the same silence with a number on it.
        lines.append("also answered: " + ", ".join(f"#{r['id']}" for r in rest))
        cut = True
    # Only when there is more to read. A one-line "ok go" followed by an
    # instruction to go and read it whole is three lines of ceremony around two
    # words, typed into an agent that was in the middle of something.
    tail = "\nRead one whole with `hl read <id>`." if cut else ""
    return "Answers to what you asked:\n" + " | ".join(lines) + tail


def _record_answers(con, agent: str, answered, log) -> None:
    db.mark_announced(con, [r["id"] for r in answered])
    db.add_event(con, agent, "answered", {"count": len(answered)})
    log(f"  {agent}: told about {len(answered)} answer(s)")


def deliver_answers(con, agent: str, log) -> bool:
    """Tell an agent what came back from something it asked.

    Answering wrote the verdict on the row and stopped there, so the agent that
    asked - and that may have parked waiting - was never told. The founder
    answering a decision on the screen, and the answer never reaching the
    agent it was for, is the same hole from the other end.

    Told once, and marked on the row rather than counted off with a cursor: two
    answers can come back in either order, and a high-water mark then loses the
    older one for good. A refusal counts as an answer - it is the one that means
    do something else - while a `denied` ask was refused by routing before
    anything was sent, and the agent read that reason on its own terminal.
    """
    if agents.is_structured(agent):
        return False  # the Go daemon drives it
    answered = db.unannounced_answers(con, agent)
    if not answered:
        return False
    if typing_hold(con, agent):
        return False              # stays unannounced; told when it wakes or moves
    handle = db.get_terminal(con, agent)
    if not handle or not term.available() or not term.alive(handle):
        return False
    if not session_present(agent):
        return False
    if not term.typeable_now(handle):
        return False
    text = answers_text(answered)
    with lock.terminal(agent) as mine:
        if not mine:
            return False
        if _type_gate(con, agent, handle):
            return False
        ok, _ = _send(handle, text)
        if ok:
            _mark_typed(con, agent)
    if ok:
        _record_answers(con, agent, answered, log)
    return ok
