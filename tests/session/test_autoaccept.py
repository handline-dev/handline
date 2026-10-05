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


def test_the_setting_defaults_on_and_a_stuck_prompt_is_not_pressed_every_tick(home: Path) -> None:
    """T249: this used to run through a waiting browser-tool prompt, the only
    tool call Claude's driver ever said yes to; Claude.launch_argv now passes
    --no-chrome, leaving no prompt of that shape to accept. Uses the rule
    mechanism instead - a live path sharing the same stuck/backoff cursor."""
    con, term = _rule_world(home, RULE_DIALOG, term=_HighlightTerm(RULE_DIALOG, ignores_enter=True))
    try:
        check("setting: on by default", autoaccept.enabled())
        now = int(time.time())
        autoaccept.sweep(con, lambda _m: None, now)
        check("stuck: a press that leaves the dialog open is reported",
              term.sent == [("2", False), ("", True)] and "dialog_accept_failed" in _kinds(con), str(term.sent))
        autoaccept.sweep(con, lambda _m: None, now + 10)
        check("stuck: and not pressed again within the backoff", len(term.sent) == 2, str(term.sent))
        autoaccept.sweep(con, lambda _m: None, now + autoaccept.STUCK_BACKOFF_S)
        check("stuck: pressed again once the backoff is over", len(term.sent) == 4, str(term.sent))
    finally:
        con.close()


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

RULE_DIALOG = [
    "  earlier output", "▔" * 60, "   Enable auto mode for this session?",
    "   ❯ 1. Yes, enable", "     2. No, not now",
    "   Enter to confirm · Esc to cancel"]
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
        termmod.screen_blocker = REAL_ORCA["screen_blocker"]

    def send(self, handle, text, enter=True):
        self.sent.append((text, enter))
        if text == self.clears:
            self.tail = list(CLEARED_SCREEN)
        return True, ""


class _HighlightTerm(_KeyTerm):
    """A terminal where a digit only moves the highlight, and Enter confirms it
    (or, with `ignores_enter`, does nothing)."""

    def __init__(self, tail, ignores_enter=False, closes_after_reads=None):
        super().__init__(tail)
        self.ignores_enter, self.closes_after_reads, self.reads_at_send = ignores_enter, closes_after_reads, None

    def install(self):
        super().install()
        plain = termmod._screen

        def read(handle):
            if (self.closes_after_reads is not None and self.reads_at_send is not None
                    and self.reads - self.reads_at_send >= self.closes_after_reads):
                self.tail = list(CLEARED_SCREEN)
            return plain(handle)
        termmod._screen = read

    def send(self, handle, text, enter=True):
        self.sent.append((text, enter))
        if text.isdigit():
            self.reads_at_send = self.reads
            self.tail = [ln.replace("❯ ", "  ") if "❯" in ln else ln for ln in self.tail]
            self.tail = [ln.replace(f"  {text}. ", f"❯ {text}. ", 1) for ln in self.tail]
        elif text == "" and enter and not self.ignores_enter:
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


def test_a_dialog_a_rule_names_is_answered_by_its_number_on_the_screen(home: Path) -> None:
    con, term = _rule_world(home, RULE_DIALOG)
    try:
        autoaccept.sweep(con, lambda _m: None)
        check("rule: the number of the rule's label is pressed, alone", term.sent == [("2", False)], str(term.sent))
        row = con.execute("SELECT payload FROM events WHERE kind = 'dialog_accepted'").fetchone()
        check("rule: the answer is on the record with the title and label",
              row is not None and RULE_TITLE in row[0] and "No, not now" in row[0] and "rule" in row[0],
              str(row and row[0]))
    finally:
        con.close()
    reordered = RULE_DIALOG[:3] + ["   ❯ 1. No, not now", "     2. Ask me later", "     3. Yes, enable"] + RULE_DIALOG[-1:]
    con, term = _rule_world(home / "reordered", reordered,
                             rules=(("claude", RULE_TITLE, "Yes, enable"),), clears="3")
    try:
        autoaccept.sweep(con, lambda _m: None)
        check("rule: the number is the live screen's, not a remembered one", term.sent == [("3", False)], str(term.sent))
    finally:
        con.close()


def test_the_exit_over_background_work_dialog_is_answered_by_a_standing_rule(home: Path) -> None:
    """T270 (#1528/#1542): a standing rule presses 'Exit and stop tasks' on
    the live-measured exit dialog, found by label wherever it sits."""
    con, term = _rule_world(home, EXIT_DIALOG, rules=(("claude", "Background work is running",
                                                        "Exit and stop tasks"),), clears="1")
    try:
        autoaccept.sweep(con, lambda _m: None)
        check("exit dialog: 'Exit and stop tasks' is pressed, already highlighted or not",
              term.sent == [("1", False)], str(term.sent))
        row = con.execute("SELECT payload FROM events WHERE kind = 'dialog_accepted'").fetchone()
        check("exit dialog: the answer is on the record", row is not None and "Exit and stop tasks" in row[0])
        check("exit dialog: and it is gone, not left waiting", term.tail == list(CLEARED_SCREEN), term.tail)
    finally:
        con.close()
    # Same dialog, the highlight and the numbering both different: the rule
    # still finds "Exit and stop tasks" by its label, now option 3, not by
    # the 1 it was pressed as above or the ❯ now sitting on "Stay".
    reordered = EXIT_DIALOG[:6] + ["   ❯ 1. Stay", "     2. Move to background and exit",
                                   "     3. Exit and stop tasks"] + EXIT_DIALOG[-1:]
    con, term = _rule_world(home / "reordered", reordered,
                            rules=(("claude", "Background work is running", "Exit and stop tasks"),),
                            clears="3")
    try:
        autoaccept.sweep(con, lambda _m: None)
        check("exit dialog: found by label at its new position, not the old '1' or the new highlight",
              term.sent == [("3", False)], str(term.sent))
        row = con.execute("SELECT payload FROM events WHERE kind = 'dialog_accepted'").fetchone()
        check("exit dialog: reordered, the answer is still on the record",
              row is not None and "Exit and stop tasks" in row[0])
        check("exit dialog: reordered, and it is gone too", term.tail == list(CLEARED_SCREEN), term.tail)
    finally:
        con.close()


def test_a_dialog_no_rule_covers_is_left_alone_and_costs_no_read(home: Path) -> None:
    con, term = _rule_world(home / "none", RULE_DIALOG, rules=())
    try:
        autoaccept.sweep(con, lambda _m: None)
        check("rule: with no rule no screen is read and nothing is pressed",
              term.sent == [] and term.reads == 0, f"{term.sent} {term.reads}")
    finally:
        con.close()
    near = (
        ("another title", (("claude", "Something else?", "No, not now"),)),
        ("another vendor", (("opencode", RULE_TITLE, "No, not now"),)),
        ("a label the dialog does not offer", (("claude", RULE_TITLE, "Maybe"),)),
    )
    for what, rules in near:
        con, term = _rule_world(home / what.replace(" ", "_"), RULE_DIALOG, rules=rules)
        try:
            autoaccept.sweep(con, lambda _m: None)
            check(f"rule: {what} presses nothing", term.sent == [], str(term.sent))
        finally:
            con.close()
    con, term = _rule_world(home / "off", RULE_DIALOG)
    try:
        autoaccept.set_enabled(False)
        autoaccept.sweep(con, lambda _m: None)
        check("rule: the setting off presses nothing", term.sent == [], str(term.sent))
    finally:
        con.close()


def test_a_rule_never_answers_a_folder_trust_screen_or_a_number_over_nine(home: Path) -> None:
    trust = ["  earlier output", "▔" * 60, "   Quick safety check: Is this a project you created or one you trust?",
             "   ❯ 1. Yes, I trust this folder", "     2. No, exit", "   Enter to confirm · Esc to cancel"]
    title = "Quick safety check: Is this a project you created or one you trust?"
    con, term = _rule_world(home / "trust", trust, rules=(("claude", title, "Yes, I trust this folder"),), clears="1")
    try:
        autoaccept.sweep(con, lambda _m: None)
        check("rule: the trust screen stays on the trust roots", term.sent == [], str(term.sent))
    finally:
        con.close()
    many = RULE_DIALOG[:3] + [f"     {n}. Option {n}" for n in range(1, 11)] + RULE_DIALOG[-1:]
    con, term = _rule_world(home / "ten", many, rules=(("claude", RULE_TITLE, "Option 10"),), clears="10")
    try:
        autoaccept.sweep(con, lambda _m: None)
        check("rule: option 10 is not one key and is not typed", term.sent == [], str(term.sent))
    finally:
        con.close()


def test_a_rule_press_that_does_not_close_the_dialog_is_not_repeated(home: Path) -> None:
    con, term = _rule_world(home, RULE_DIALOG, clears="never")
    try:
        autoaccept.sweep(con, lambda _m: None)
        autoaccept.sweep(con, lambda _m: None)
        check("rule: a dialog still open after the press is pressed once, not every tick",
              term.sent == [("2", False)], str(term.sent))
        check("rule: and the failure is on the record", _kinds(con).count("dialog_accept_failed") == 1, str(_kinds(con)))
    finally:
        con.close()

def test_a_digit_that_only_moves_the_highlight_is_followed_by_one_enter(home: Path) -> None:
    con, term = _rule_world(home, RULE_DIALOG, term=_HighlightTerm(RULE_DIALOG))
    try:
        autoaccept.sweep(con, lambda _m: None)
        check("rule: the highlight moved to the ruled option, Enter is pressed once",
              term.sent == [("2", False), ("", True)], str(term.sent))
        check("rule: and it closed, so it is on the record as accepted",
              _kinds(con)[1:] == ["dialog_accepted"], str(_kinds(con)))
    finally:
        con.close()
    con, term = _rule_world(home / "deaf", RULE_DIALOG, term=_HighlightTerm(RULE_DIALOG, ignores_enter=True))
    try:
        autoaccept.sweep(con, lambda _m: None)
        autoaccept.sweep(con, lambda _m: None)
        check("rule: an Enter that does not close it is not repeated, on this tick or the next",
              term.sent == [("2", False), ("", True)], str(term.sent))
        check("rule: and the failure is recorded once", _kinds(con)[1:] == ["dialog_accept_failed"], str(_kinds(con)))
    finally:
        con.close()
    con, term = _rule_world(home / "same", RULE_DIALOG, clears="never")
    try:
        autoaccept.sweep(con, lambda _m: None)
        check("rule: a digit that changed nothing does not earn an Enter",
              term.sent == [("2", False)], str(term.sent))
    finally:
        con.close()

def test_the_confirming_enter_needs_the_footer_to_say_so_and_a_vanished_dialog_is_cleared(home: Path) -> None:
    no_enter = [ln.replace("Enter to confirm · Esc to cancel", "Esc to cancel") for ln in RULE_DIALOG]
    con, term = _rule_world(home, no_enter, term=_HighlightTerm(no_enter))
    try:
        autoaccept.sweep(con, lambda _m: None)
        check("rule: a footer that does not say Enter confirms earns no Enter",
              term.sent == [("2", False)], str(term.sent))
        check("rule: and it is still open, so the failure is recorded",
              _kinds(con)[1:] == ["dialog_accept_failed"], str(_kinds(con)))
    finally:
        con.close()
    # The settle reads after the digit still show it; the read before a confirming Enter does not.
    gone = _HighlightTerm(RULE_DIALOG, closes_after_reads=termmod.PRESS_SETTLE_READS)
    con, term = _rule_world(home / "gone", RULE_DIALOG, term=gone)
    try:
        autoaccept.sweep(con, lambda _m: None)
        check("rule: a dialog that closed between the two reads gets no Enter",
              term.sent == [("2", False)], str(term.sent))
        check("rule: and counts as accepted, with no hold set",
              _kinds(con)[1:] == ["dialog_accepted"] and db.get_cursor(con, "autoaccept_stuck:backend") == 0,
              str(_kinds(con)))
    finally:
        con.close()


def test_auto_accept_config_round_trips(home: Path) -> None:
    _daemon_home(home, 20)
    out = support._capture(lambda: climod.main(["auto-accept"]))
    check("auto-accept: shows on with nothing set", out.startswith("auto-accept: on") and autoaccept.enabled(), out)
    out = support._capture(lambda: climod.main(["auto-accept", "off"]))
    check("auto-accept: off is set and shown", out.startswith("auto-accept: off") and not autoaccept.enabled(), out)
    out = support._capture(lambda: climod.main(["auto-accept", "on"]))
    check("auto-accept: on again", out.startswith("auto-accept: on") and autoaccept.enabled(), out)


def main() -> int:
    def tests(td):
        run(lambda: test_the_setting_defaults_on_and_a_stuck_prompt_is_not_pressed_every_tick(Path(td) / 'aa3'))
        run(lambda: test_a_declared_folders_trust_screen_is_accepted_by_the_blocked_sweep(Path(td) / 'aa4'))
        run(lambda: test_the_trust_screen_is_left_alone_when_consent_or_the_setting_is_missing(Path(td) / 'aa5'))
        run(lambda: test_a_wake_meeting_a_declared_trust_screen_decisiones_with_the_setting_off(Path(td) / 'aa7'))
        run(lambda: test_a_dialog_a_rule_names_is_answered_by_its_number_on_the_screen(Path(td) / 'aa9'))
        run(lambda: test_the_exit_over_background_work_dialog_is_answered_by_a_standing_rule(Path(td) / 'aa15'))
        run(lambda: test_a_dialog_no_rule_covers_is_left_alone_and_costs_no_read(Path(td) / 'aa10'))
        run(lambda: test_a_rule_never_answers_a_folder_trust_screen_or_a_number_over_nine(Path(td) / 'aa11'))
        run(lambda: test_a_rule_press_that_does_not_close_the_dialog_is_not_repeated(Path(td) / 'aa12'))
        run(lambda: test_a_digit_that_only_moves_the_highlight_is_followed_by_one_enter(Path(td) / 'aa13'))
        run(lambda: test_the_confirming_enter_needs_the_footer_to_say_so_and_a_vanished_dialog_is_cleared(Path(td) / 'aa14'))
        run(lambda: test_auto_accept_config_round_trips(Path(td) / 'aa6'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
