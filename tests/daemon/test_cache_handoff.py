"""Tests: an agent writes its own handoff before its prompt cache expires. The cache's real life is read
off the session's last response (an hour, or five minutes after an overage); an open idle session is
asked once at 90% of an hour's life, never for a five-minute one, never twice for the same stretch, and
a fresh session after the cache has lapsed gets the note only if nothing happened after it.

Run on its own: tools/test.sh tests/daemon/test_cache_handoff.py   (all areas: tools/suite.sh)
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
from handline.daemon import handoff_note  # noqa: E402
from handline.session import cachettl  # noqa: E402
from handline.session import handoff as handoffmod  # noqa: E402

H1, M5 = cachettl.LONG_TTL_S, cachettl.SHORT_TTL_S


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _response(at: float, ttl: int | None, sidechain: bool = False, pad: int = 0) -> str:
    """One assistant line of a Claude transcript, written at `at`, whose request wrote the cache with
    life `ttl` (None: it only read the cache and wrote nothing)."""
    made = {"ephemeral_1h_input_tokens": 500 if ttl == H1 else 0,
            "ephemeral_5m_input_tokens": 500 if ttl == M5 else 0}
    line = {"type": "assistant", "timestamp": _iso(at), "isSidechain": sidechain,
            "message": {"id": f"m{at}", "usage": {"input_tokens": 2, "cache_read_input_tokens": 90000,
                                                  "cache_creation": made}}}
    if pad:
        line["pad"] = "x" * pad
    return json.dumps(line)


def _write(path: Path, *lines: str) -> Path:
    path.write_text("\n".join(lines) + "\n")
    return path


def test_the_cache_clock_is_the_last_responses_time_and_the_ttl_the_nearest_that_wrote_the_cache(home: Path) -> None:
    use_home(home)
    t = time.time()
    one = _write(home / "a.jsonl", _response(t - 900, H1), json.dumps({"type": "user", "timestamp": _iso(t - 800)}))
    c = cachettl.read(str(one))
    check("ttl: a response that wrote an hour's cache is an hour, clocked from that response",
          c is not None and c.ttl == H1 and abs(c.at - (t - 900)) < 2, str(c))
    five = _write(home / "b.jsonl", _response(t - 100, M5))
    check("ttl: one that wrote a five-minute cache is five minutes (an account over its limit)",
          cachettl.read(str(five)).ttl == M5)
    reads = _write(home / "c.jsonl", _response(t - 3000, H1), _response(t - 2000, None), _response(t - 60, None))
    c = cachettl.read(str(reads))
    check("ttl: responses that only read the cache restart the clock and leave the ttl to the last writer",
          c.ttl == H1 and abs(c.at - (t - 60)) < 2, str(c))
    mixed = _write(home / "d.jsonl", _response(t - 3000, H1), _response(t - 500, M5))
    check("ttl: the drop to five minutes is followed as soon as a response shows it", cachettl.read(str(mixed)).ttl == M5)
    side = _write(home / "e.jsonl", _response(t - 3000, H1), _response(t - 10, M5, sidechain=True))
    c = cachettl.read(str(side))
    check("ttl: a subagent's response is another conversation's cache and does not move this one's",
          c.ttl == H1 and abs(c.at - (t - 3000)) < 2, str(c))
    check("ttl: no clock where there is no transcript or nothing in it",
          cachettl.read(None) is None and cachettl.read(str(home / "none.jsonl")) is None
          and cachettl.read(str(_write(home / "f.jsonl", "not json", json.dumps({"type": "user"})))) is None)
    nowrite = _write(home / "g.jsonl", *[_response(t - 100 + i, None) for i in range(cachettl._LOOK_BACK + 3)])
    c = cachettl.read(str(nowrite))
    check("ttl: a clock with no ttl when no recent response says one", c is not None and c.ttl is None and c.left(t) is None)
    big = _write(home / "h.jsonl", _response(t - 2500, H1), _response(t - 30, None, pad=400_000))
    c = cachettl.read(str(big))
    check("ttl: a last response larger than the first read is still found, with the ttl behind it",
          c is not None and c.ttl == H1 and abs(c.at - (t - 30)) < 2, str(c))
    with open(one, "a") as f:
        f.write(_response(t - 5, M5) + "\n")
    check("ttl: an appended response is seen (the answer is kept only while the file is unchanged)",
          cachettl.read(str(one)).ttl == M5)
    check("ttl: its left is the life less the age, negative once it has lapsed",
          abs(cachettl.Clock(t - 600, H1).left(t) - 3000) < 1 and cachettl.Clock(t - 4000, H1).left(t) < 0
          and cachettl.margin(H1) == 300 and cachettl.margin(M5) == 25)


def _world(home: Path):
    """An idle open agent with a transcript of its own, and the ask it is typed into `asks`."""
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
    st = types.SimpleNamespace(transcript_path=str(tpath), session_id="sess-1")
    return con, asks, real, tpath, st


def test_an_open_idle_session_is_asked_at_ninety_percent_of_an_hours_cache_and_not_before_or_after(home: Path) -> None:
    con, asks, real, tpath, st = _world(home)
    try:
        t = time.time()
        _write(tpath, _response(t - 3000, H1))
        log = lambda _m: None  # noqa: E731
        ask = lambda now: handoff_note.at_cache_edge(con, "backend", int(now), st, log)   # noqa: E731
        check("cache ask: an idle session half way through its cache is left alone", not ask(t - 3000 + 1800) and not asks)
        check("cache ask: not before ninety percent of the hour (53 minutes)", not ask(t - 3000 + 3180) and not asks)
        check("cache ask: at ninety percent (54 minutes since the last response) it is asked, once",
              ask(t - 3000 + 3240) and len(asks) == 1, str(asks))
        check("cache ask: the ask names the file, the 30 lines and what belongs in it",
              str(handoff_note.note_path("backend")) in asks[0] and "30 lines" in asks[0]
              and "T<n>/#<n>" in asks[0] and "deciding" in asks[0] and "wait on" in asks[0], asks[0][:300])
        check("cache ask: and is remembered as an ask made for the cache, not for a close",
              handoff_note.asked(con, "backend") is not None and not handoff_note.answered_turn(con, "backend"))

        handoff_note.clear(con, "backend")
        asks.clear()
        check("cache ask: not once so little of the cache is left that a turn could not reach it (59 minutes)",
              not ask(t - 3000 + 3560) and not asks)
        check("cache ask: nor once it has lapsed", not ask(t - 3000 + 3700) and not asks)
    finally:
        dmn_delivery.type_notice = real
        con.close()


def test_a_five_minute_cache_gets_no_ask_and_neither_does_a_session_with_no_clock(home: Path) -> None:
    con, asks, real, tpath, st = _world(home)
    try:
        t = time.time()
        log = lambda _m: None  # noqa: E731
        _write(tpath, _response(t - 280, M5))
        check("short ttl: a five-minute cache is never asked, at ninety percent or any time",
              not any(handoff_note.at_cache_edge(con, "backend", int(t - 280 + s), st, log) for s in (270, 275, 290, 3300))
              and not asks)
        _write(tpath, *[_response(t - 3300 + i, None) for i in range(5)])
        check("short ttl: and none whose responses never say how long the cache lives",
              not handoff_note.at_cache_edge(con, "backend", int(t), st, log) and not asks)
        check("short ttl: nor one with no transcript at all",
              not handoff_note.at_cache_edge(con, "backend", int(t), types.SimpleNamespace(transcript_path=str(home / "no.jsonl")), log)
              and not asks)
        # The close-time ask follows the same rule: a five-minute cache is not worth the turn.
        _write(tpath, _response(t - 100, M5))
        db.add_event(con, "backend", "Stop", {})
        check("short ttl: the ask before a close is skipped too, so the record is the facts alone",
              not handoff_note.hold(con, "backend", int(t), log) and not asks)
        _write(tpath, _response(t - 100, H1))
        check("short ttl: while an hour's cache with a minute used is asked before a close, as before",
              handoff_note.hold(con, "backend", int(t), log) and len(asks) == 1)
    finally:
        dmn_delivery.type_notice = real
        con.close()


def test_three_idle_hours_write_exactly_one_handoff_and_real_activity_earns_the_next(home: Path) -> None:
    con, asks, real, tpath, st = _world(home)
    try:
        t0 = time.time() - 4 * 3600          # the last real response, four hours ago
        log = lambda _m: None  # noqa: E731
        ask = lambda now: handoff_note.at_cache_edge(con, "backend", int(now), st, log)   # noqa: E731
        lines = [_response(t0, H1)]
        _write(tpath, *lines)
        db.add_event(con, "backend", "Stop", {})
        check("no repeat: the session sits idle to 54 minutes and is asked", ask(t0 + 3240) and len(asks) == 1)

        # The handoff turn: a prompt, the file, a response (which restarts the cache clock), a stop.
        db.add_event(con, "backend", "UserPromptSubmit", {})
        handoff_note.note_path("backend").write_text("deciding: the cache edge, because the founder asked")
        lines.append(_response(t0 + 3300, H1))
        _write(tpath, *lines)
        db.add_event(con, "backend", "Stop", {})
        later = [t0 + 3300 + 3240, t0 + 3300 + 3300, t0 + 2 * 3600 + 3300, t0 + 3 * 3600 + 3300]
        check("no repeat: three more idle hours, checked at every edge, ask nothing more",
              not any(ask(n) for n in later) and len(asks) == 1, str(asks))
        check("no repeat: the handoff turn did not count as activity, so the session is not closed for it",
              not handoff_note.answered_turn(con, "backend"))
        check("no repeat: and what it wrote is the note, kept for the stretch",
              handoff_note.text(con, "backend") == "deciding: the cache edge, because the founder asked")

        # Real activity: a message is delivered and a turn works. The next idle stretch asks again.
        db.add_event(con, "backend", "UserPromptSubmit", {})
        lines.append(_response(t0 + 3 * 3600 + 3400, H1))
        _write(tpath, *lines)
        db.add_event(con, "backend", "Stop", {})
        check("no repeat: after a working turn the old note is no longer the stretch's",
              handoff_note.text(con, "backend") is None and handoff_note.asked(con, "backend") is None)
        check("no repeat: and the next idle stretch is asked again, once, over a clean file",
              not ask(t0 + 3 * 3600 + 3400 + 600) and ask(t0 + 3 * 3600 + 3400 + 3240) and len(asks) == 2
              and not handoff_note.note_path("backend").exists(), str(asks))
    finally:
        dmn_delivery.type_notice = real
        con.close()


def test_every_session_state_has_an_answer(home: Path) -> None:
    con, asks, real, tpath, st = _world(home)
    try:
        t = time.time()
        log = lambda _m: None  # noqa: E731
        _write(tpath, _response(t - 3300, H1))
        edge = int(t)

        # (b) working: a line that cannot be typed now (a turn is running, a dialog is open) makes no ask,
        # remembers nothing, and is tried again on the next tick while the window lasts.
        dmn_delivery.type_notice = lambda c, a, text: False
        check("states: working - nothing is typed at a busy session, and nothing is remembered of it",
              not handoff_note.at_cache_edge(con, "backend", edge, st, log)
              and handoff_note.asked(con, "backend") is None)
        dmn_delivery.type_notice = lambda c, a, text: (asks.append(text), True)[1]
        check("states: idle - the next tick that can type asks", handoff_note.at_cache_edge(con, "backend", edge + 30, st, log)
              and len(asks) == 1)

        # (d) died before writing: the turn never began. Nothing asks again and the record is the facts.
        check("states: died or failed - no second ask for the same stretch",
              not handoff_note.at_cache_edge(con, "backend", edge + 60, st, log) and len(asks) == 1)
        text, missing = handoffmod.capture(con, "backend")
        check("states: and the record is the three facts alone, without a line of the agent's",
              "previous session: sess-1" in text and "written by the agent" not in text, text)
        db.add_event(con, "backend", "UserPromptSubmit", {})
        handoff_note.note_path("backend").write_text("   \n")
        db.add_event(con, "backend", "Stop", {})
        text, _ = handoffmod.capture(con, "backend")
        check("states: a turn that wrote an empty file is the same: facts alone", "written by the agent" not in text)

        # (c) already closed, or not idle, or at the wall: nothing to type at.
        handoff_note.clear(con, "backend")
        asks.clear()
        db.set_closed(con, "backend", "sess-1")
        check("states: closed - not asked", not handoff_note.at_cache_edge(con, "backend", edge, st, log) and not asks)
        con.execute("UPDATE agent_state SET closed_at = NULL, resume_session = NULL WHERE agent = 'backend'")
        db.set_agent_state(con, "backend", dmn_states.RUNNING)
        check("states: working (the daemon's own state says so) - not asked",
              not handoff_note.at_cache_edge(con, "backend", edge, st, log) and not asks)
        db.set_agent_state(con, "backend", dmn_states.IDLE)

        # background work behind the prompt: not idle, not asked.
        db.set_cursor(con, "bgwork:backend", 1)
        check("states: working in the background - not asked", not handoff_note.at_cache_edge(con, "backend", edge, st, log) and not asks)
    finally:
        dmn_delivery.type_notice = real
        con.close()


def test_the_switch_is_per_agent_with_the_global_one_behind_it(home: Path) -> None:
    con, asks, real, tpath, st = _world(home)
    try:
        t = time.time()
        _write(tpath, _response(t - 3300, H1))
        log = lambda _m: None  # noqa: E731
        check("switch: on by default", handoff_note.enabled("backend"))
        cfg = agents.read("backend")
        cfg["handoff_note"] = False
        agents.write("backend", cfg)
        check("switch: the agent's own off stops the ask", not handoff_note.enabled("backend")
              and not handoff_note.at_cache_edge(con, "backend", int(t), st, log) and not asks)
        g = loadmod.config_for_write()
        g["handoff_note"] = False
        loadmod.write_config(g)
        cfg["handoff_note"] = True
        agents.write("backend", cfg)
        check("switch: and the agent's own on beats the global off", handoff_note.enabled("backend")
              and handoff_note.at_cache_edge(con, "backend", int(t), st, log) and len(asks) == 1)
        cfg.pop("handoff_note")
        agents.write("backend", cfg)
        check("switch: with no word of its own the agent follows the global one", not handoff_note.enabled("backend"))
    finally:
        dmn_delivery.type_notice = real
        con.close()


def test_a_closed_session_resumes_while_its_real_cache_lives_and_starts_fresh_with_the_note_after(home: Path) -> None:
    con, asks, real, tpath, st = _world(home)
    try:
        t = time.time()
        root = vendors.for_account("alpha").memory_root(vendors.config_dir("alpha"))
        proj = root / "proj"
        proj.mkdir(parents=True, exist_ok=True)
        tr = proj / "sess-1.jsonl"
        expired = lambda: dmn_lifecycle.cache_expired(con, "backend", "sess-1", int(t))   # noqa: E731
        _write(tr, _response(t - 1800, H1))
        check("expiry: thirty minutes into an hour's cache a resume is warm", not expired())
        _write(tr, _response(t - 3400, H1))
        check("expiry: past the hour less the margin it is cold, so it starts fresh", expired())
        _write(tr, _response(t - 200, M5))
        check("expiry: a five-minute cache is warm for under five minutes...", not expired())
        _write(tr, _response(t - 290, M5))
        check("expiry: ...and cold after, where the fixed rule would have called it warm for 55 minutes", expired())
        _write(tr, _response(t - 4000, None))
        check("expiry: with no ttl in the transcript the old clock decides, from the hook events",
              (db.add_event(con, "backend", "Stop", {}) or True) and not expired())
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'", (int(t) - dmn_states.RESUME_WARM_S - 5,))
        check("expiry: and calls it cold when that clock is over the fixed margin", expired())

        # The note: written at the edge, carried into the fresh session's record, and only while nothing happened after.
        _write(tr, _response(t - 3300, H1))
        _write(tpath, _response(t - 3300, H1))
        handoff_note.at_cache_edge(con, "backend", int(t - 3300 + 3240), st, lambda _m: None)
        db.add_event(con, "backend", "UserPromptSubmit", {})
        handoff_note.note_path("backend").write_text("next: look at the cache edge test")
        db.add_event(con, "backend", "Stop", {})
        rec, _ = handoffmod.capture(con, "backend")
        check("fresh: nothing happened after the note, so the fresh session's record carries it under the facts",
              "written by the agent just before the close:\n  next: look at the cache edge test" in rec
              and rec.index("previous session") < rec.index("written by"), rec)
        db.add_event(con, "backend", "UserPromptSubmit", {})
        rec, _ = handoffmod.capture(con, "backend")
        check("fresh: but once anything happened after it, the note is old and is not carried",
              "written by the agent" not in rec and "previous session: sess-1" in rec, rec)
    finally:
        dmn_delivery.type_notice = real
        con.close()


def test_the_countdown_runs_on_the_real_ttl(home: Path) -> None:
    con, asks, real, tpath, st = _world(home)
    try:
        t = time.time()
        db.add_event(con, "backend", "Stop", {"transcript_path": str(tpath), "session_id": "sess-1"})
        _write(tpath, _response(t - 600, H1))
        left = handoff_note.cache_left(con, "backend", t)
        check("countdown: an hour's cache ten minutes after its last response has fifty minutes left",
              left is not None and abs(left - 3000) < 3, str(left))
        _write(tpath, _response(t - 100, M5))
        left = handoff_note.cache_left(con, "backend", t)
        check("countdown: a five-minute cache shows what five minutes has left, not an hour",
              left is not None and abs(left - 200) < 3, str(left))
        check("countdown: the screen's cell reads it", ar_cell(con, t) == "3:20")
    finally:
        dmn_delivery.type_notice = real
        con.close()


def test_the_daemon_tick_asks_an_agent_that_stays_open_and_leaves_it_open(home: Path) -> None:
    """Through the real tick: an agent set never to close, idle until its cache is nearly spent, is asked
    once and is not closed by having answered."""
    con, asks, real, tpath, st = _world(home)
    real_o = (orcamod.available, orcamod.alive, orcamod.live_terminals, orcamod.wait_idle)
    try:
        orca_seen()
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        sessions_are("backend")
        idle_close.set_agent_close_timeout("backend", 0)
        t = time.time()
        _write(tpath, _response(t - 3250, H1))
        db.add_event(con, "backend", "Stop", {})
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'", (int(t) - 3250,))
        lines: list[str] = []
        for _ in range(3):
            dmn_evaluate.supervise(con, "backend", lines.append, int(t), {}, [])
        check("tick: the idle agent is asked once however many ticks pass in the window",
              len(asks) == 1 and any("before its cache expires" in m for m in lines), f"{asks} {lines}")
        db.add_event(con, "backend", "UserPromptSubmit", {})
        handoff_note.note_path("backend").write_text("next: nothing yet")
        db.add_event(con, "backend", "Stop", {})
        dmn_evaluate.supervise(con, "backend", lines.append, int(t) + 60, {}, [])
        row = db.get_agent_state(con, "backend")
        check("tick: having answered, it is not closed for it and not asked again",
              not row["closed_at"] and len(asks) == 1, str(dict(row)))
    finally:
        orcamod.available, orcamod.alive, orcamod.live_terminals, orcamod.wait_idle = real_o
        dmn_delivery.type_notice = real
        con.close()


def ar_cell(con, now: float) -> str:
    from handline.ui.components import agent_rows
    return re.sub(r"\x1b\[[0-9;]*m", "", agent_rows.cache_cell(agent_rows.cache_left(con, "backend", int(now))))


def main() -> int:
    def tests(td):
        run(lambda: test_the_cache_clock_is_the_last_responses_time_and_the_ttl_the_nearest_that_wrote_the_cache(Path(td) / "c1"))
        run(lambda: test_an_open_idle_session_is_asked_at_ninety_percent_of_an_hours_cache_and_not_before_or_after(Path(td) / "c2"))
        run(lambda: test_a_five_minute_cache_gets_no_ask_and_neither_does_a_session_with_no_clock(Path(td) / "c3"))
        run(lambda: test_three_idle_hours_write_exactly_one_handoff_and_real_activity_earns_the_next(Path(td) / "c4"))
        run(lambda: test_every_session_state_has_an_answer(Path(td) / "c5"))
        run(lambda: test_the_switch_is_per_agent_with_the_global_one_behind_it(Path(td) / "c6"))
        run(lambda: test_a_closed_session_resumes_while_its_real_cache_lives_and_starts_fresh_with_the_note_after(Path(td) / "c7"))
        run(lambda: test_the_countdown_runs_on_the_real_ttl(Path(td) / "c8"))
        run(lambda: test_the_daemon_tick_asks_an_agent_that_stays_open_and_leaves_it_open(Path(td) / "c9"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
