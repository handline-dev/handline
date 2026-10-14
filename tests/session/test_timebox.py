"""Tests: `ocp ask --timebox` and the notice to a supervisor about an agent running with nothing asked of it (T83).

Run on its own: python3 tests/session/test_timebox.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import types

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.session import bgjobs  # noqa: E402

TEAM = """
teams:
  dev: [marina, dohyun]
agents:
  manager: {}
  marina:  {}
  dohyun:  {}
edges:
  - { from: "manager", to: "dev.*", relation: supervisor, response: async }
  - { from: "dev.*", to: "manager", relation: peer, response: async }
  - { from: "*", to: "human", kind: task }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
"""


REAL_SESSION_PRESENT = dmn_delivery.session_present


def _world(home: Path) -> list[str]:
    """Three agents with open sessions, and a `send` that records what is typed."""
    _daemon_home(home, 20)
    hand_edit_team(TEAM)
    idle_close.set_bg_seconds("bg_check_s", 10**6)   # T450's check is tested on its own
    sent: list[tuple[str, str]] = []
    orca_seen()
    dmn_delivery.session_present = lambda a: True
    termmod.typeable_now = lambda h: True
    termmod.send = lambda h, text, enter=True: (sent.append((h, text)), (True, ""))[1]
    con = db.connect()
    try:
        for a in ("manager", "marina", "dohyun"):
            agents.register(a)
            db.set_terminal(con, a, f"term_{a}", "wt")
        con.commit()
    finally:
        con.close()
    return sent


def _delivered(con, requester: str, target: str, ago_s: int, kind: str | None = "task", **kw) -> int:
    """A request `target` was told, left open: a task delivery, the only thing an agent is nudged about."""
    rid = db.add_request(con, requester, target, "do the thing", None, "peer", "async",
                         kind, 0, None)
    db.mark_delivered(con, rid)
    db.set_cursor(con, f"notify:{target}", rid)
    at = int(time.time()) - ago_s
    con.execute("UPDATE requests SET delivered_at = ?, typed_at = ?, landed_at = ? WHERE id = ?", (at, at, at, rid))
    return rid


def test_a_duration_needs_its_unit(home: Path) -> None:
    check("timebox: 90m, 2h, 45s and 1d parse",
          [timefmt.parse_span(t) for t in ("90m", "2h", "45s", "1d")] == [5400, 7200, 45, 86400])
    bad = []
    for t in ("90", "m", "0m", "-5m", "1.5h", "2w", ""):
        try:
            timefmt.parse_span(t)
        except ValueError:
            continue
        bad.append(t)
    check("timebox: no unit, no number, zero, negative or fractional is refused", not bad, str(bad))


def _doing(con, tid: int, target: str) -> int:
    """Link a queued, non-fyi request to `tid` so it reads doing - T286's
    clock only runs in that state, the same live read `task_state` makes."""
    rid = db.add_request(con, askmod.HUMAN, target, "look at it", None, "supervisor",
                         "fire_and_forget", None, 0, None)
    db.link_request(con, tid, rid)
    return rid


def _stale_bg(con, agent: str, silent_s: int, told_ago: int | None = None) -> int:
    """`agent` reports background work, seen 3h ago, and has shown nothing for
    `silent_s`; the supervisor was told `told_ago` seconds ago, if at all."""
    now = int(time.time())
    con.execute("DELETE FROM events WHERE agent = ?", (agent,))
    db.add_event(con, agent, "Stop", {})
    con.execute("UPDATE events SET ts = ? WHERE agent = ?", (now - silent_s, agent))
    db.set_cursor(con, f"bgwork:{agent}", 1)
    con.execute("UPDATE cursors SET updated = ? WHERE name = ?", (now - 3 * 3600, f"bgwork:{agent}"))
    if told_ago is None:
        db.clear_cursor(con, f"stale_bg_told:{agent}")
    else:
        db.set_cursor(con, f"stale_bg_told:{agent}", now - told_ago)
    return now


def _owe(con, agent: str) -> None:
    """`agent` has asked something and waits on the answer: it owes nothing, and is owed nothing."""
    db.add_request(con, agent, "manager", "is this right?", None, "peer", "async", None, 0, None)


def test_background_work_is_stale_at_the_limit_and_may_stop_after_the_veto(home: Path) -> None:
    _world(home)
    con = db.connect()
    try:
        _owe(con, "marina")
        S, V = dmn_delivery.STALE_BG_S, dmn_delivery.STALE_BG_VETO_S
        st = types.SimpleNamespace(session_id="s")
        db.set_agent_state(con, "marina", dmn_states.IDLE, detail="finished its turn")

        def probe(silent, told_ago=None):
            now = _stale_bg(con, "marina", silent, told_ago)
            row = db.get_agent_state(con, "marina")
            return (dmn_delivery.bg_stale(con, "marina", now), dmn_delivery.bg_may_stop(con, "marina", now),
                    dmn_lifecycle.should_close(con, "marina", "IDLE", row, now, st))

        check("stale: a second short of the limit is not stale, and holds the close",
              probe(S - 1, told_ago=V + 5) == (False, False, False), str(probe(S - 1, V + 5)))
        check("stale: at the limit it is stale, but not told, so still held",
              probe(S) == (True, False, False), str(probe(S)))
        check("stale: told a second short of the veto window is still held",
              probe(S, told_ago=V - 1) == (True, False, False), str(probe(S, V - 1)))
        check("stale: told and the window over, it may be stopped and the close is no longer held",
              probe(S, told_ago=V) == (True, True, True), str(probe(S, V)))
        idle_close.set_agent_close_timeout("marina", idle_close.NEVER)
        check("stale: an agent set to never close is never stopped, whichever path asks (the veto)",
              probe(S, told_ago=V) == (True, False, False)
              and dmn_delivery.bg_holds(con, "marina", _stale_bg(con, "marina", S, V)), str(probe(S, V)))
        idle_close.set_agent_close_timeout("marina", None)
        idle_close.paths.config_file().write_text("stale_background_s: 100\n")
        check("stale: config.yaml's stale_background_s moves the limit",
              probe(99)[0] is False and probe(100)[0] is True)
    finally:
        con.close()


def _forgotten(con, agent: str, silent_s: int) -> int:
    """`agent` has background work, nothing owed, and has shown nothing for `silent_s`."""
    return _stale_bg(con, agent, silent_s)


def test_a_turn_that_ends_before_a_check_is_written_is_not_its_answer(home: Path) -> None:
    """Checks and activity are paired by event order, not by clock: a tick's check is
    written seconds after the tick began, and a Stop in that gap still came before it
    (auditor #6383, #6430, T540)."""
    _world(home)
    con = db.connect()
    try:
        t0 = int(time.time())
        db.set_cursor(con, "bgwork:marina", 1)
        con.execute("UPDATE cursors SET updated = ? WHERE name = 'bgwork:marina'", (t0,))

        def event(kind: str, ts: int) -> None:
            rid = db.add_event(con, "marina", kind, {})
            con.execute("UPDATE events SET ts = ? WHERE id = ?", (ts, rid))

        T = t0 + 300
        for gap in (0, 5, 10):
            con.execute("DELETE FROM events WHERE agent = 'marina'")
            event("Stop", T + gap)                        # its real turn ends ...
            event("background_checked", T)                # ... before the check, stamped as the tick began
            event("UserPromptSubmit", T + 20)
            event("Stop", T + 25)                         # the answer turn
            shown = dmn_delivery.bg_last_shown(con, "marina")
            check(f"order: a Stop {gap}s into the tick is shown, its answer turn is not",
                  shown == T + gap, str(shown))
        event("UserPromptSubmit", T + 60)                 # real work after the answer
        check("order: work after the answer turn is shown", dmn_delivery.bg_last_shown(con, "marina") == T + 60)
    finally:
        con.close()


def test_the_background_times_follow_the_agent_then_the_global_setting_then_the_default(home: Path) -> None:
    _world(home)
    check("times: with nothing set, the notice is the idle close and the grace is 5 minutes",
          (idle_close.bg_notice_s("marina"), idle_close.bg_grace_s("marina")) == (600, 300))
    idle_close.set_close_timeout(1200)
    check("times: the notice follows the idle close", idle_close.bg_notice_s("marina") == 1200)
    idle_close.set_bg_seconds("bg_notice_s", 400)
    idle_close.set_bg_seconds("bg_grace_s", 120)
    check("times: the global settings win over the defaults",
          (idle_close.bg_notice_s("dohyun"), idle_close.bg_grace_s("dohyun")) == (400, 120))
    idle_close.set_bg_seconds("bg_grace_s", 60, "marina")
    check("times: an agent's own wins over the global one, and only for it",
          idle_close.bg_grace_s("marina") == 60 and idle_close.bg_grace_s("dohyun") == 120
          and idle_close.bg_notice_s("marina") == 400)
    idle_close.set_bg_seconds("bg_grace_s", None, "marina")
    check("times: clearing it goes back to the global one", idle_close.bg_grace_s("marina") == 120)
    bad = []
    for args in (("bg_grace_s", 0), ("bg_grace_s", -5), ("other", 5), ("bg_notice_s", None)):
        try:
            idle_close.set_bg_seconds(*args)
            bad.append(args)
        except ValueError:
            pass
    check("times: zero, negative, another key and a global clear are refused", not bad, str(bad))


def test_a_stale_agent_is_nudged_to_resolve_but_a_fresh_one_is_not(home: Path) -> None:
    _world(home)
    con = db.connect()
    try:
        rid = _delivered(con, "manager", "marina", 9000)
        db.set_cursor(con, "notify:marina", rid)
        db.set_agent_state(con, "marina", dmn_states.IDLE, detail="finished its turn")
        row = db.get_agent_state(con, "marina")
        now = _stale_bg(con, "marina", dmn_delivery.STALE_BG_S - 1)
        check("stale: background work not yet stale means it is not forgotten",
              dmn_delivery.forgotten_resolve(con, "marina", row, now) is None)
        now = _stale_bg(con, "marina", dmn_delivery.STALE_BG_S)
        check("stale: stale background work does not hide a forgotten resolve",
              dmn_delivery.forgotten_resolve(con, "marina", row, now) is not None)
    finally:
        con.close()


def test_the_row_says_how_long_and_when_stale(home: Path) -> None:
    _world(home)
    con = db.connect()
    try:
        db.set_agent_state(con, "marina", dmn_states.IDLE, detail="finished its turn")
        _stale_bg(con, "marina", 60)
        con.execute("UPDATE cursors SET updated = ? WHERE name = 'bgwork:marina'", (int(time.time()) - 720,))
        word, detail = screenstate.screen_state(con, db.get_agent_state(con, "marina"))
        check("row: idle, background and how long", (word, detail) == ("IDLE", "background 12m"),
              str((word, detail)))
        _stale_bg(con, "marina", dmn_delivery.STALE_BG_S + 5)
        word, detail = screenstate.screen_state(con, db.get_agent_state(con, "marina"))
        check("row: and stale once the agent has shown nothing that long",
              word == "IDLE" and detail == "background 3h, stale", detail)
    finally:
        con.close()


def test_the_exit_dialog_is_answered_by_stopping_only_over_stale_work_it_can_read(home: Path) -> None:
    bg_hint = "  ⏵⏵ auto mode on · 1 shell · ← for agents · ↓ to manage"
    composer_bg = COMPOSER_EMPTY[:-1] + [bg_hint]

    def stale(told_ago):
        return lambda con: _stale_bg(con, "backend", dmn_delivery.STALE_BG_S + 5, told_ago)

    told = dmn_delivery.STALE_BG_VETO_S + 5
    con, term, ok, why = _close_at(home / "a", EXIT_DIALOG, exits_on="", screen=composer_bg, prep=stale(told))
    check("stop: stale work past the veto is stopped with Enter on 'Exit and stop tasks', and closes",
          ok and term.sent == [("/exit", True), ("", True)], f"{ok} {why} {term.sent}")
    check("stop: and the choice is on the record",
          con.execute("SELECT 1 FROM events WHERE agent = 'backend' AND kind = 'stale_background_stopped'"
                      ).fetchone() is not None)
    con.close()

    con, term, ok, why = _close_at(home / "b", EXIT_DIALOG, exits_on="", screen=composer_bg, prep=stale(None))
    check("stop: stale but the supervisor not yet told is refused, nothing typed",
          not ok and term.sent == [] and "background work is running" in why, f"{ok} {why} {term.sent}")
    con.close()

    con, term, ok, why = _close_at(home / "c", EXIT_DIALOG, exits_on="", screen=composer_bg,
                                   prep=lambda c: _stale_bg(c, "backend", 60, told))
    check("stop: work that is not stale is refused however long ago its supervisor was told",
          not ok and term.sent == [], f"{ok} {why} {term.sent}")
    con.close()

    def never(con):
        idle_close.set_agent_close_timeout("backend", idle_close.NEVER)
        return stale(told)(con)

    con, term, ok, why = _close_at(home / "g", EXIT_DIALOG, exits_on="", screen=composer_bg, prep=never)
    check("stop: an agent set to never close is refused by the close itself, nothing typed",
          not ok and term.sent == [] and "background work is running" in why, f"{ok} {why} {term.sent}")
    con.close()

    other = [ln.replace("❯ 1. Exit and stop tasks", "  1. Exit and stop tasks")
             .replace("  2. Move to background and exit", "❯ 2. Move to background and exit")
             for ln in EXIT_DIALOG]
    variants = (("d", other, "a dialog whose highlighted option is not 'Exit and stop tasks'"),
                ("e", [ln.replace("Exit and stop tasks", "Exit and stop all tasks") for ln in EXIT_DIALOG],
                 "one whose label is not exactly the measured one"),
                ("f", [ln.replace("Background work is running", "Something else is running")
                       for ln in EXIT_DIALOG], "a different title"))
    for tag, dialog, what in variants:
        con, term, ok, why = _close_at(home / tag, dialog, exits_on="", screen=composer_bg, prep=stale(told))
        keys = [t for t, _ in term.sent if t != "/exit"]
        check(f"stop: {what} is declined with Esc, never chosen", not ok and keys == ["\x1b"],
              f"{ok} {why} {term.sent}")
        con.close()


def test_stopping_key_presses_only_the_measured_dialog(home: Path) -> None:
    def dialog(title="Background work is running", footer="Enter to confirm \u00b7 Esc to cancel",
               marked=("\u276f 1. Exit and stop tasks", "  2. Move to background and exit", "  3. Stay")):
        return termmod.Dialog(title, (), footer, (title, *marked))

    check("stopping_key: the measured dialog is pressed with Enter",
          termmod.stopping_key(dialog()) == (termmod.ENTER, ""))
    near = (
        ("a different title", dialog(title="Something else is running")),
        ("a footer without Enter to confirm", dialog(footer="Esc to cancel")),
        ("the keep option highlighted", dialog(marked=("  1. Exit and stop tasks", "\u276f 3. Stay"))),
        ("a highlighted option that only resembles it", dialog(marked=("\u276f 1. Exit and stop all tasks",))),
        ("two marked lines", dialog(marked=("\u276f 1. Exit and stop tasks", "\u276f 3. Stay"))),
        ("no marked line", dialog(marked=("  1. Exit and stop tasks", "  3. Stay"))),
    )
    for what, d in near:
        key, why = termmod.stopping_key(d)
        check(f"stopping_key: {what} presses nothing", key is None and why, f"{key!r} {why}")


def test_a_cancelled_task_handoff_names_the_task_state(home: Path) -> None:
    sent = _world(home)
    con = db.connect()
    try:
        tid = db.new_task(con, "ship it", "marina", "manager", timeout_s=3600)
        rid = _delivered(con, "manager", "marina", 60, kind="task")
        db.link_request(con, tid, rid)
        row = db.get_request(con, rid)
        ok, why = dmn_delivery.notify_cancelled(con, row, "no longer needed")
        check("cancel: the notice says what state the task is left in", ok and sent
              and f"T{tid} is" in sent[-1][1] and "hl task list" in sent[-1][1], str((ok, why, sent)))
        plain = _delivered(con, "manager", "marina", 60)
        sent.clear()
        dmn_delivery.notify_cancelled(con, db.get_request(con, plain), "no longer needed")
        check("cancel: an ordinary request's notice is unchanged", sent and "hl task list" not in sent[-1][1], str(sent))
    finally:
        con.close()


def test_a_block_that_clears_hands_the_head_back_and_it_is_the_one_nudged(home: Path) -> None:
    """T344/T347: once the ask is answered the true head is re-told, and it - not the
    stand-in left open behind it - is what a later silence nudges."""
    _world(home)
    con = db.connect()
    try:
        span = dmn_delivery.NUDGE_SCHEDULE_S[0] + 60
        t_head = db.new_task(con, "needs a mockup", "marina", "manager", timeout_s=3600)
        r_head = _delivered(con, "manager", "marina", span + 60, kind="task")
        db.link_request(con, t_head, r_head)
        t_next = db.new_task(con, "other work", "marina", "manager", timeout_s=3600)
        r_next = _delivered(con, "manager", "marina", span, kind="task")
        db.link_request(con, t_next, r_next)
        ask = db.add_request(con, "marina", "dohyun", "does this mockup work?", None, "peer", "async",
                             None, 0, None)
        db.link_request(con, t_head, ask)
        db.set_cursor(con, "notify:marina", r_next)
        db.set_agent_state(con, "marina", dmn_states.IDLE, detail="finished its turn")
        support._set_request_state(con, ask, "landed", "yes", announced=True)
        check("cleared: the true head is the one to work again",
              dmn_delivery.effective_task_head(con, "marina")["id"] == r_head)
        db.set_cursor(con, "notify:marina", r_head)     # what `deliver_queued` does when it re-tells it
        now = int(time.time())
        found = dmn_delivery.forgotten_resolve(con, "marina", db.get_agent_state(con, "marina"), now)
        check("cleared: the re-told head is what is nudged, the stand-in is behind it",
              found and found["head"]["id"] == r_head and found["behind"] == 1, str(found))
    finally:
        con.close()

def main() -> int:
    def tests(td):
        try:
            run(lambda: test_a_duration_needs_its_unit(Path(td) / 'tb0'))
            run(lambda: test_a_cancelled_task_handoff_names_the_task_state(Path(td) / 'tb19'))
            run(lambda: test_a_block_that_clears_hands_the_head_back_and_it_is_the_one_nudged(Path(td) / 'tb21'))
            run(lambda: test_background_work_is_stale_at_the_limit_and_may_stop_after_the_veto(Path(td) / 'tb10'))
            run(lambda: test_a_turn_that_ends_before_a_check_is_written_is_not_its_answer(Path(td) / 'tb10p'))
            run(lambda: test_the_background_times_follow_the_agent_then_the_global_setting_then_the_default(Path(td) / 'tb10c'))
            run(lambda: test_a_stale_agent_is_nudged_to_resolve_but_a_fresh_one_is_not(Path(td) / 'tb11'))
            run(lambda: test_the_row_says_how_long_and_when_stale(Path(td) / 'tb13'))
            run(lambda: test_the_exit_dialog_is_answered_by_stopping_only_over_stale_work_it_can_read(Path(td) / 'tb14'))
            run(lambda: test_stopping_key_presses_only_the_measured_dialog(Path(td) / 'tb16'))
        finally:
            dmn_delivery.session_present = REAL_SESSION_PRESENT
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
