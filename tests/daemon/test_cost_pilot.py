"""Tests: the cost pilot. Cache renewal and the idle fold are each off until the agent's own
agent.yaml turns them on; a renewal is one handoff-note ask in the last two minutes of an hour's cache,
at most twice per idle stretch and never over 150k of context; a fold closes an idle session fresh, only
between tasks and only over its size; each is an event carrying the figures it was decided on.

Run on its own: tools/test.sh tests/daemon/test_cost_pilot.py   (all areas: tools/suite.sh)
"""
from __future__ import annotations

import json
import sys
import time
import types
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.daemon import costpilot, handoff_note  # noqa: E402
from handline.session import cachettl  # noqa: E402

H1, M5 = cachettl.LONG_TTL_S, cachettl.SHORT_TTL_S
LOG = lambda _m: None  # noqa: E731


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _response(at: float, ttl: int | None) -> str:
    made = {"ephemeral_1h_input_tokens": 500 if ttl == H1 else 0,
            "ephemeral_5m_input_tokens": 500 if ttl == M5 else 0}
    return json.dumps({"type": "assistant", "timestamp": _iso(at), "isSidechain": False,
                       "message": {"id": f"m{at}", "usage": {"input_tokens": 2, "cache_read_input_tokens": 90000,
                                                             "cache_creation": made}}})


def _set(agent: str, **keys) -> None:
    cfg = agents.read(agent)
    cfg.update(keys)
    agents.write(agent, cfg)


def _world(home: Path):
    """An idle open agent with an hour's cache last written at `t0`, and the asks typed into `asks`."""
    _daemon_home(home, 20)
    cfg = loadmod.config_for_write()
    cfg.pop("handoff_note", None)
    loadmod.write_config(cfg)
    con = db.connect()
    readable_agent(con, home, "backend")
    tpath = home / "sess-1.jsonl"
    write_agent_status("backend", "alpha", session_id="sess-1", transcript_path=str(tpath))
    asks: list[str] = []
    real = dmn_delivery.type_notice
    dmn_delivery.type_notice = lambda c, a, text: (asks.append(text), True)[1]
    db.set_agent_state(con, "backend", dmn_states.IDLE)
    t0 = time.time() - 4000
    tpath.write_text(_response(t0, H1) + "\n")
    st = types.SimpleNamespace(transcript_path=str(tpath), session_id="sess-1", trusted_ctx_used=80_000)
    return con, asks, real, tpath, st, t0


def _due(con, st, now: float) -> bool:
    return costpilot.renewal_due(con, "backend", db.get_agent_state(con, "backend"), int(now), st)


def _renew(con, st, now: float) -> bool:
    return costpilot.renew(con, "backend", db.get_agent_state(con, "backend"), int(now), st, LOG)


def _events(con, kind: str) -> list[dict]:
    return [json.loads(r["payload"]) for r in
            con.execute("SELECT payload FROM events WHERE agent = 'backend' AND kind = ? ORDER BY id", (kind,))]


def test_both_are_off_until_the_agent_turns_them_on(home: Path) -> None:
    con, asks, real, tpath, st, t0 = _world(home)
    try:
        at = t0 + H1 - 100
        check("switch: nothing is on by default", not costpilot.renewal_on("backend") and not costpilot.fold_on("backend"))
        check("switch: so no renewal is due in the very window it would be", not _due(con, st, at) and not _renew(con, st, at)
              and not asks)
        _set("backend", cache_renewal="yes")
        check("switch: only the boolean true counts, not any value", not costpilot.renewal_on("backend"))
        _set("backend", cache_renewal=True)
        check("switch: the agent's own true turns the renewal on, and then it is due", costpilot.renewal_on("backend")
              and _due(con, st, at))
        g = loadmod.config_for_write()
        g["cache_renewal"] = True
        g["idle_fold"] = True
        loadmod.write_config(g)
        check("switch: there is no global switch; config.yaml cannot turn it on for another agent",
              not costpilot.fold_on("backend"))
    finally:
        dmn_delivery.type_notice = real
        con.close()


def test_a_renewal_is_asked_in_the_last_two_minutes_of_the_hour_and_logged(home: Path) -> None:
    con, asks, real, tpath, st, t0 = _world(home)
    try:
        _set("backend", cache_renewal=True)
        check("window: not at 54 minutes (the ordinary ask's place) or at 57", not _due(con, st, t0 + 3240)
              and not _due(con, st, t0 + 3420))
        check("window: not with under a minute left, when a turn could not reach its first request",
              not _due(con, st, t0 + H1 - 40))
        check("window: not once it has lapsed", not _due(con, st, t0 + H1 + 5))
        check("window: from 150 seconds left to 60 it is due (57.5 to 59 minutes)",
              _due(con, st, t0 + H1 - 140) and _due(con, st, t0 + H1 - 61) and not _due(con, st, t0 + H1 - 160))
        check("window: a 30-second tick cannot step over it", (150 - 60) >= 30 * 3)
        check("renew: the ask is typed, once", _renew(con, st, t0 + H1 - 100) and len(asks) == 1
              and str(handoff_note.note_path("backend")) in asks[0], str(asks))
        ev = _events(con, "cache_renewal")
        check("renew: it is an event carrying the count, the context and what was left of the cache",
              len(ev) == 1 and ev[0]["n"] == 1 and ev[0]["of"] == 2 and ev[0]["ctx"] == 80_000
              and 95 <= ev[0]["cache_left_s"] <= 100, str(ev))
        check("renew: asked as a cache ask, so answering it is no reason to close",
              handoff_note.asked(con, "backend") is not None and not handoff_note.answered_turn(con, "backend"))
        check("renew: the ask is waiting for its turn, so the next tick does not ask again",
              not _due(con, st, t0 + H1 - 70) and not _renew(con, st, t0 + H1 - 70) and len(asks) == 1)
    finally:
        dmn_delivery.type_notice = real
        con.close()


def test_a_stretch_has_at_most_two_renewals_and_a_message_starts_a_new_one(home: Path) -> None:
    check("stretch: none yet", costpilot.renewals_in_stretch([]) == 0)
    R, P, S = "cache_renewal", "UserPromptSubmit", "SessionStart"
    check("stretch: a renewal and its own turn count one, and the turn does not end the stretch",
          costpilot.renewals_in_stretch([(5, P), (4, R)]) == 1)
    check("stretch: two renewals each with its turn count two",
          costpilot.renewals_in_stretch([(9, P), (8, R), (7, P), (6, R), (5, S)]) == 2)
    check("stretch: a message someone typed after a renewal ends the stretch",
          costpilot.renewals_in_stretch([(9, P), (8, P), (7, R)]) == 0
          and costpilot.renewals_in_stretch([(9, R), (8, P), (7, P), (6, R)]) == 1)
    check("stretch: a session start ends it", costpilot.renewals_in_stretch([(9, R), (8, S), (7, R), (6, R)]) == 1)

    con, asks, real, tpath, st, t0 = _world(home)
    try:
        _set("backend", cache_renewal=True)
        lines = [_response(t0, H1)]
        db.add_event(con, "backend", "UserPromptSubmit", {})          # the message the stretch began at
        at = t0
        for n in (1, 2):
            at = at + H1 - 100
            tpath.write_text("\n".join(lines) + "\n")
            check(f"stretch: renewal {n} is asked", _renew(con, st, at) and len(asks) == n, str(asks))
            db.add_event(con, "backend", "UserPromptSubmit", {})      # the ask's own turn
            lines.append(_response(at + 5, H1))                       # which restarts the hour
            tpath.write_text("\n".join(lines) + "\n")
            db.add_event(con, "backend", "Stop", {})
            at = at + 5
        check("stretch: a third, a whole hour later, is not asked", not _due(con, st, at + H1 - 100)
              and not _renew(con, st, at + H1 - 100) and len(asks) == 2)
        db.add_event(con, "backend", "UserPromptSubmit", {})          # someone writes to it
        lines.append(_response(at + 200, H1))
        tpath.write_text("\n".join(lines) + "\n")
        db.add_event(con, "backend", "Stop", {})
        check("stretch: after a message the next idle stretch has its renewals again",
              _due(con, st, at + 200 + H1 - 100) and _renew(con, st, at + 200 + H1 - 100) and len(asks) == 3)
        check("stretch: and counts one", _events(con, "cache_renewal")[-1]["n"] == 1)
    finally:
        dmn_delivery.type_notice = real
        con.close()


def test_no_renewal_over_150k_or_with_no_reading_and_none_for_a_cache_or_state_that_cannot_use_it(home: Path) -> None:
    con, asks, real, tpath, st, t0 = _world(home)
    try:
        _set("backend", cache_renewal=True)
        at = t0 + H1 - 100
        for ctx, want in ((150_000, True), (150_001, False), (None, False)):
            st.trusted_ctx_used = ctx
            check(f"context: {ctx} -> {'asked' if want else 'skipped'}", _due(con, st, at) is want)
        st.trusted_ctx_used = 80_000
        tpath.write_text(_response(t0, M5) + "\n")
        check("cache: a five-minute cache is never renewed", not _due(con, st, t0 + M5 - 70))
        tpath.write_text(_response(t0, H1) + "\n")
        check("cache: no transcript, no renewal",
              not costpilot.renewal_due(con, "backend", db.get_agent_state(con, "backend"), int(at),
                                        types.SimpleNamespace(transcript_path=str(home / "no.jsonl"),
                                                              trusted_ctx_used=1)))
        _set("backend", handoff_note=False)
        check("note: an agent that turned its handoff note off is not asked for one", not _due(con, st, at))
        _set("backend", handoff_note=True)
        db.set_cursor(con, "bgwork:backend", 1)
        check("state: background work behind its prompt - not renewed", not _due(con, st, at))
        db.clear_cursor(con, "bgwork:backend")
        db.set_agent_state(con, "backend", dmn_states.RUNNING)
        check("state: mid-turn - not renewed", not _due(con, st, at))
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        con.execute("UPDATE agent_state SET closed_at = ? WHERE agent = 'backend'", (int(at),))
        check("state: a closed session has nothing to renew", not _due(con, st, at))
        con.execute("UPDATE agent_state SET closed_at = NULL WHERE agent = 'backend'")
        check("state: and once open and idle again it is due", _due(con, st, at) and not asks)
    finally:
        dmn_delivery.type_notice = real
        con.close()


def _fold_world(home: Path):
    con, asks, real, tpath, st, t0 = _world(home)
    _set("backend", idle_fold=True)
    st.trusted_ctx_used = 200_000
    now = int(time.time())
    db.add_event(con, "backend", "Stop", {})
    con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'", (now - 700,))
    return con, asks, real, tpath, st, now


def _fold_due(con, st, now: int) -> bool:
    return costpilot.fold_due(con, "backend", db.get_agent_state(con, "backend"), now, st)


def test_a_fold_is_due_only_idle_between_tasks_and_over_its_size(home: Path) -> None:
    con, asks, real, tpath, st, now = _fold_world(home)
    try:
        check("fold: idle ten minutes over 150k with no task and nothing queued - due", _fold_due(con, st, now))
        st.trusted_ctx_used = 149_999
        check("fold: under the size - not", not _fold_due(con, st, now))
        st.trusted_ctx_used = None
        check("fold: no reading of the context - not", not _fold_due(con, st, now))
        st.trusted_ctx_used = 150_000
        check("fold: exactly the size - due", _fold_due(con, st, now))
        check("fold: idle under ten minutes - not", not _fold_due(con, st, now - 150))
        _set("backend", idle_fold_after_s=300)
        check("fold: the agent's own wait applies", _fold_due(con, st, now - 150))
        _set("backend", idle_fold_after_s=300, idle_fold_tokens=250_000)
        check("fold: and its own size", not _fold_due(con, st, now))
        _set("backend", idle_fold_after_s=300, idle_fold_tokens=100_000)
        check("fold: a smaller size folds sooner", _fold_due(con, st, now))
        tid = db.new_task(con, "a task", "backend", "orcapipe")
        db.set_current_task(con, "backend", tid)
        check("fold: holding a task - not at a task boundary", not _fold_due(con, st, now))
        db.set_current_task(con, "backend", None)
        check("fold: the task done, due again", _fold_due(con, st, now))
        rid = db.add_request(con, "orcapipe", "backend", "a task to do", None, "peer", "async", None, 0, None)
        check("fold: something queued for it - not", not _fold_due(con, st, now))
        con.execute("DELETE FROM requests WHERE id = ?", (rid,))
        db.set_cursor(con, "bgwork:backend", 1)
        check("fold: background work behind its prompt - not", not _fold_due(con, st, now))
        db.clear_cursor(con, "bgwork:backend")
        st.session_id = None
        check("fold: no session to take the record from - not", not _fold_due(con, st, now))
        st.session_id = "sess-1"
        db.add_event(con, "backend", "close_failed", {})
        check("fold: a close that failed a moment ago is not retried every tick", not _fold_due(con, st, now))
        con.execute("DELETE FROM events WHERE kind = 'close_failed'")
        _set("backend", idle_fold=False)
        check("fold: switched off - not", not _fold_due(con, st, now))
    finally:
        dmn_delivery.type_notice = real
        con.close()


def test_the_note_the_fold_waited_for_ends_the_wait_and_the_fold_is_an_event(home: Path) -> None:
    con, asks, real, tpath, st, now = _fold_world(home)
    closed: list[str] = []
    real_close = dmn_lifecycle.close_fresh
    dmn_lifecycle.close_fresh = lambda c, a, log, s, **kw: (closed.append(a), (True, "closed"))[1]
    try:
        tpath.write_text(_response(now - 700, H1) + "\n")
        check("note: due, so the close is held for the note, which is asked once",
              _fold_due(con, st, now) and handoff_note.hold(con, "backend", now, LOG) and len(asks) == 1)
        db.add_event(con, "backend", "UserPromptSubmit", {})           # the ask's own turn
        handoff_note.note_path("backend").write_text("next: carry on with T9")
        db.add_event(con, "backend", "Stop", {})
        check("note: its turn restarted the idle clock, but that answer is what the fold waited for",
              _fold_due(con, st, now) and not handoff_note.hold(con, "backend", now, LOG))
        ok, why = costpilot.fold(con, "backend", now, st, LOG)
        ev = _events(con, "idle_fold")
        check("fold: the session is closed fresh, once, and recorded with the figures and whether a note went",
              ok and closed == ["backend"] and len(ev) == 1 and ev[0]["ctx"] == 200_000 and ev[0]["note"] is True
              and ev[0]["idle_s"] >= 0, f"{closed} {ev}")
        closed.clear()
        dmn_lifecycle.close_fresh = lambda c, a, log, s, **kw: (False, "someone else is typing at this agent")
        ok, why = costpilot.fold(con, "backend", now, st, LOG)
        check("fold: a close that did not happen is no fold and no event", not ok and len(_events(con, "idle_fold")) == 1
              and "typing" in why)
    finally:
        dmn_lifecycle.close_fresh = real_close
        dmn_delivery.type_notice = real
        con.close()


def test_through_the_tick_the_pilot_agent_renews_and_folds_and_a_control_agent_is_unchanged(home: Path) -> None:
    con, asks, real, tpath, st, t0 = _world(home)
    real_o = (orcamod.available, orcamod.alive, orcamod.live_terminals, orcamod.wait_idle)
    closed: list[str] = []
    real_close = dmn_lifecycle.close_fresh
    dmn_lifecycle.close_fresh = lambda c, a, log, s, **kw: (closed.append(a), (True, "closed"))[1]
    try:
        orca_seen()
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        sessions_are("backend")
        idle_close.set_agent_close_timeout("backend", 0)
        write_agent_status("backend", "alpha", session_id="sess-1", transcript_path=str(tpath), ctx_used=80_000)
        db.add_event(con, "backend", "Stop", {})
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'", (int(t0),))
        lines: list[str] = []
        sup = lambda at: dmn_evaluate.supervise(con, "backend", lines.append, int(at), {}, [])   # noqa: E731

        sup(t0 + 3240)
        check("control: with the switches off the ordinary ask at 54 minutes is made, as before",
              len(asks) == 1 and not _events(con, "cache_renewal"), str(asks))
        handoff_note.clear(con, "backend")
        asks.clear()

        _set("backend", cache_renewal=True)
        sup(t0 + 3240)
        check("pilot: with the renewal on the 54-minute ask is not made", not asks and not _events(con, "cache_renewal"))
        sup(t0 + H1 - 100)
        check("pilot: it is made in the last two minutes, and logged",
              len(asks) == 1 and len(_events(con, "cache_renewal")) == 1, f"{asks} {lines}")
        sup(t0 + H1 - 70)
        check("pilot: and not again by the next tick", len(asks) == 1)

        db.add_event(con, "backend", "UserPromptSubmit", {})
        handoff_note.note_path("backend").write_text("next: nothing")
        tpath.write_text(_response(t0 + H1 - 60, H1) + "\n")
        db.add_event(con, "backend", "Stop", {})
        write_agent_status("backend", "alpha", session_id="sess-1", transcript_path=str(tpath), ctx_used=160_000)
        _set("backend", idle_fold=True)
        db.add_event(con, "backend", "Stop", {})
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'", (int(t0 + H1 - 60),))
        later = t0 + H1 - 60 + 700
        sup(later)
        row = db.get_agent_state(con, "backend")
        check("pilot: over 150k, idle and between tasks, the tick folds it, and the fold is an event",
              closed == ["backend"] and len(_events(con, "idle_fold")) == 1, f"{closed} {lines}")
    finally:
        dmn_lifecycle.close_fresh = real_close
        orcamod.available, orcamod.alive, orcamod.live_terminals, orcamod.wait_idle = real_o
        dmn_delivery.type_notice = real
        con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_both_are_off_until_the_agent_turns_them_on(Path(td) / "c1"))
        run(lambda: test_a_renewal_is_asked_in_the_last_two_minutes_of_the_hour_and_logged(Path(td) / "c2"))
        run(lambda: test_a_stretch_has_at_most_two_renewals_and_a_message_starts_a_new_one(Path(td) / "c3"))
        run(lambda: test_no_renewal_over_150k_or_with_no_reading_and_none_for_a_cache_or_state_that_cannot_use_it(Path(td) / "c4"))
        run(lambda: test_a_fold_is_due_only_idle_between_tasks_and_over_its_size(Path(td) / "c5"))
        run(lambda: test_the_note_the_fold_waited_for_ends_the_wait_and_the_fold_is_an_event(Path(td) / "c6"))
        run(lambda: test_through_the_tick_the_pilot_agent_renews_and_folds_and_a_control_agent_is_unchanged(Path(td) / "c7"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
