"""Tests: auto-accepting the dialogs a working agent meets all day (T64).

Run on its own: python3 tests/session/test_autoaccept.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.session import autoaccept  # noqa: E402


def _kinds(con) -> list[str]:
    return [r["kind"] for r in con.execute("SELECT kind FROM events WHERE agent = 'backend' ORDER BY id")]


def _trust_world(home: Path, consented: bool = True):
    """backend is idle with work waiting, behind the live folder-trust screen."""
    con, term = _decline_world(home, LIVE_TRUST_PROMPT_CAPTURE.split("\n"))
    pressed: list = []

    def confirm(handle):
        pressed.append(handle)
        term.tail = list(CLEARED_SCREEN)
        return True, "trusted"

    termmod.confirm_trust = confirm
    termmod.wait_idle = lambda h, timeout_ms=0: True
    spawnmod.worktree_path = lambda w: LIVE_TRUST_PROMPT_FOLDER
    trustmod.is_declared = lambda p: consented
    return con, term, pressed


def test_a_declared_folders_trust_screen_is_accepted_by_the_blocked_sweep(home: Path) -> None:
    con, term, pressed = _trust_world(home)
    try:
        blockedmod._checked.clear()
        blockedmod.sweep(con, lambda _m: None, int(time.time()))
        check("trust: the unparsed 'Security guide' screen is answered for a declared, recorded folder",
              pressed == ["term_x"] and db.block_get(con, "backend") is None, str(pressed))
        check("trust: and it is on the record", "trust_confirmed" in _kinds(con), str(_kinds(con)))
    finally:
        con.close()


def test_the_trust_screen_is_left_alone_when_consent_or_the_setting_is_missing(home: Path) -> None:
    con, term, pressed = _trust_world(home / "undeclared", consented=False)
    try:
        blockedmod._checked.clear()
        blockedmod.sweep(con, lambda _m: None, int(time.time()))
        check("trust: an undeclared folder is not accepted, and the sweep does not stall the agent",
              pressed == [] and term.sent == []
              and db.get_agent_state(con, "backend")["state"] != dmn_states.STALLED, str(pressed))
    finally:
        con.close()
    con, term, pressed = _trust_world(home / "off")
    try:
        autoaccept.set_enabled(False)
        blockedmod._checked.clear()
        blockedmod.sweep(con, lambda _m: None, int(time.time()))
        check("trust: with auto-accept off a declared folder is not accepted either",
              pressed == [] and db.block_get(con, "backend") is not None, str(pressed))
    finally:
        con.close()


def test_a_wake_meeting_a_declared_trust_screen_decisiones_with_the_setting_off(home: Path) -> None:
    con, term, pressed = _trust_world(home)
    try:
        ok, _why = dmn_lifecycle._handle_trust_prompt(con, "backend", "term_x", lambda _m: None)
        check("trust: with the setting on a wake presses a declared, recorded folder",
              ok and pressed == ["term_x"], str(pressed))
    finally:
        con.close()
    con, term, pressed = _trust_world(home / "off")
    try:
        autoaccept.set_enabled(False)
        ok, _why = dmn_lifecycle._handle_trust_prompt(con, "backend", "term_x", lambda _m: None)
        check("trust: with the setting off the same wake raises and does not confirm",
              not ok and pressed == []
              and db.get_agent_state(con, "backend")["state"] == dmn_states.STALLED, str(pressed))
    finally:
        con.close()


RULE_TITLE = "Enable auto mode for this session?"


class _KeyTerm:
    """A terminal showing `tail` until the key `clears` is sent, counting reads."""

    def __init__(self, tail, clears=None):
        self.tail, self.clears, self.sent, self.reads = list(tail), clears, [], 0

    def install(self):
        def read(handle):
            self.reads += 1
            return support._screen_of(self.tail)
        termmod._screen = read
        termmod.send = self.send
        termmod.screen_blocker = REAL_TERMINAL["screen_blocker"]

    def send(self, handle, text, enter=True):
        self.sent.append((text, enter))
        if text == self.clears:
            self.tail = list(CLEARED_SCREEN)
        return True, ""


def _rule_world(home: Path, tail, rules=(("claude", RULE_TITLE, "No, not now"),), clears="2", term=None):
    """backend is running with no tool waiting, behind `tail`."""
    from handline.team import dialog_rules
    _daemon_home(home, 20)
    termmod.available = lambda: True
    termmod.alive = lambda h: True
    con = db.connect()
    db.set_terminal(con, "backend", "term_x", "wt")
    db.set_agent_state(con, "backend", dmn_states.RUNNING)
    for rule in rules:
        check(f"rule world: {rule[2]!r} is added", dialog_rules.add(*rule)[0])
    term = term or _KeyTerm(tail, clears)
    term.install()
    return con, term


def test_auto_accept_config_round_trips(home: Path) -> None:
    _daemon_home(home, 20)
    out = support._capture(lambda: climod.main(["auto-accept"]))
    check("auto-accept: shows on with nothing set", out.startswith("auto-accept: on") and autoaccept.enabled(), out)
    out = support._capture(lambda: climod.main(["auto-accept", "off"]))
    check("auto-accept: off is set and shown", out.startswith("auto-accept: off") and not autoaccept.enabled(), out)
    out = support._capture(lambda: climod.main(["auto-accept", "on"]))
    check("auto-accept: on again", out.startswith("auto-accept: on") and autoaccept.enabled(), out)


MCP_DIALOG = [
    "  Claude Code", "▔" * 60, "   2 new MCP servers found in .mcp.json",
    "   ❯ 1. Use this and all future MCP servers in this project", "     2. Use this MCP server",
    "     3. Continue without using this MCP server",
    "   Enter to confirm · Esc to cancel"]
MCP_TITLE = "2 new MCP servers found in .mcp.json"


def _startup_world(home: Path, tail, rules=(), clears="3"):
    """backend's launch has just stopped at `tail`, and the wake asks the screen."""
    con, term = _rule_world(home, tail, rules=rules, clears=clears)
    termmod.bypass_prompt_showing = lambda h: False
    termmod.trust_prompt_showing = lambda h: False
    return con, term


def test_a_launch_stopping_at_a_dialog_no_rule_covers_presses_nothing_and_shows_the_founder_the_dialog(home: Path) -> None:
    con, term = _startup_world(home, MCP_DIALOG)
    try:
        ok, why = dmn_lifecycle._clear_trust_prompt(con, "backend", "term_x", lambda _m: None, launched=True)
        row = db.get_agent_state(con, "backend")
        check("start-up dialog: the wake does not go on", not ok and MCP_TITLE in why, why)
        check("start-up dialog: nothing is typed or pressed", term.sent == [], str(term.sent))
        check("start-up dialog: the agent is STALLED, with the dialog in its detail",
              row["state"] == dmn_states.STALLED and dmn_lifecycle.DIALOG_STALL in row["detail"]
              and MCP_TITLE in row["detail"], str(dict(row)))
        decisions = db.open_decision_ids(con, "backend", dmn_lifecycle.DIALOG_DECISION)
        check("start-up dialog: one decision for the founder", len(decisions) == 1, str(decisions))
        dmn_lifecycle._clear_trust_prompt(con, "backend", "term_x", lambda _m: None, launched=True)
        check("start-up dialog: the next wake does not raise a second",
              db.open_decision_ids(con, "backend", dmn_lifecycle.DIALOG_DECISION) == decisions)
        # With the setting off a rule that covers it is not pressed either.
        from handline.team import dialog_rules
        dialog_rules.add("claude", MCP_TITLE, "Continue without using this MCP server")
        autoaccept.set_enabled(False)
        db.set_agent_state(con, "backend", dmn_states.QUEUED, detail="reset")
        ok, _why = dmn_lifecycle._clear_trust_prompt(con, "backend", "term_x", lambda _m: None, launched=True)
        check("start-up dialog: with the setting off a rule is not pressed", not ok and term.sent == [], str(term.sent))
    finally:
        con.close()


PERMISSION_DIALOG = [
    "  earlier output", "▔" * 60, "   Bash command", "   ❯ 1. Yes", "     2. No",
    "   Enter to confirm · Esc to cancel"]


def test_a_wake_in_place_meeting_a_permission_prompt_neither_stalls_nor_raises_a_decision(home: Path) -> None:
    con, term = _startup_world(home, PERMISSION_DIALOG)
    try:
        ok, why = dmn_lifecycle._clear_trust_prompt(con, "backend", "term_x", lambda _m: None)
        check("in-place wake: a mid-work dialog is left to the hooks and the wake goes on to its own check",
              ok and term.sent == [], f"{ok} {why} {term.sent}")
        check("in-place wake: no stall and no decision",
              db.get_agent_state(con, "backend")["state"] != dmn_states.STALLED
              and not db.open_decision_ids(con, "backend", dmn_lifecycle.DIALOG_DECISION))
    finally:
        con.close()


def test_a_launch_stopping_at_a_dialog_a_rule_covers_is_answered_and_goes_on(home: Path) -> None:
    con, term = _startup_world(home, MCP_DIALOG,
                               rules=(("claude", MCP_TITLE, "Continue without using this MCP server"),))
    try:
        ok, why = dmn_lifecycle._clear_trust_prompt(con, "backend", "term_x", lambda _m: None, launched=True)
        check("start-up rule: the rule's number is pressed and the wake goes on",
              ok and term.sent == [("3", False)], f"{ok} {why} {term.sent}")
        check("start-up rule: no stall and no decision",
              db.get_agent_state(con, "backend")["state"] != dmn_states.STALLED
              and not db.open_decision_ids(con, "backend", dmn_lifecycle.DIALOG_DECISION))
    finally:
        con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_a_declared_folders_trust_screen_is_accepted_by_the_blocked_sweep(Path(td) / 'aa4'))
        run(lambda: test_the_trust_screen_is_left_alone_when_consent_or_the_setting_is_missing(Path(td) / 'aa5'))
        run(lambda: test_a_wake_meeting_a_declared_trust_screen_decisiones_with_the_setting_off(Path(td) / 'aa7'))
        run(lambda: test_auto_accept_config_round_trips(Path(td) / 'aa6'))
        run(lambda: test_a_launch_stopping_at_a_dialog_no_rule_covers_presses_nothing_and_shows_the_founder_the_dialog(Path(td) / 'aa16'))
        run(lambda: test_a_launch_stopping_at_a_dialog_a_rule_covers_is_answered_and_goes_on(Path(td) / 'aa17'))
        run(lambda: test_a_wake_in_place_meeting_a_permission_prompt_neither_stalls_nor_raises_a_decision(Path(td) / 'aa19'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
