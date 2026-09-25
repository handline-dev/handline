"""Tests: auto-accepting the dialogs a working agent meets all day (T64).

Run on its own: python3 tests/session/test_autoaccept.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from ocp.session import autoaccept  # noqa: E402
from ocp.vendors.claude import Claude  # noqa: E402

TOOL = "mcp__claude-in-chrome__navigate"
# Reconstructed from Claude Code's wording for a tool permission prompt, not
# measured on a live screen (see vendors/claude.py): the shape PERMISSION_PROMPT
# already stands for, naming the browser server and tool.
BROWSER_PROMPT = [
    "  earlier output", "▔" * 60, "   Tool use",
    "     claude-in-chrome - navigate (MCP)(url: \"https://example.com\")",
    "   Do you want to proceed?", "   ❯ 1. Yes",
    "     2. Yes, and don't ask again for claude-in-chrome - navigate commands",
    "     3. No, and tell Claude what to do differently (esc)",
    "   Esc to cancel · Tab to amend"]


def _dialog(tail):
    return orcamod.parse_dialog(tail, orcamod._footer_row(tail))


def _swap(tail, old, new):
    return [ln.replace(old, new) for ln in tail]


class _EnterTerm:
    """A terminal showing `tail` until Enter is sent, then the cleared screen."""

    def __init__(self, tail, ignores_enter=False):
        self.tail, self.ignores_enter, self.sent = list(tail), ignores_enter, []

    def install(self):
        orcamod._screen = lambda h: support._screen_of(self.tail)
        orcamod.send = self.send
        orcamod.screen_blocker = REAL_ORCA["screen_blocker"]

    def send(self, handle, text, enter=True):
        self.sent.append((text, enter))
        if enter and text == "" and not self.ignores_enter:
            self.tail = list(CLEARED_SCREEN)
        return True, ""


def _world(home: Path, tail, tool=TOOL, **kw):
    """backend is RUNNING at a permission prompt for `tool`, behind `tail`."""
    _daemon_home(home, 20)
    orcamod.available = lambda: True
    orcamod.alive = lambda h: True
    con = db.connect()
    db.set_terminal(con, "backend", "term_x", "wt")
    db.set_agent_state(con, "backend", dmn_states.RUNNING)
    waiting.note("backend", tool, {"url": "https://example.com"})
    term = _EnterTerm(tail, **kw)
    term.install()
    return con, term


def _kinds(con) -> list[str]:
    return [r["kind"] for r in con.execute("SELECT kind FROM events WHERE agent = 'backend' ORDER BY id")]


def test_the_browser_prompt_is_said_yes_to_only_when_it_is_exactly_that(home: Path) -> None:
    drv = Claude()
    check("accept: the browser tool's prompt with the plain Yes highlighted is answered with Enter",
          drv.accepting_key(_dialog(BROWSER_PROMPT), TOOL) == (orcamod.ENTER, ""))
    near = (
        ("a tool that is not the browser's", BROWSER_PROMPT, "Bash"),
        ("no tool waiting", BROWSER_PROMPT, None),
        ("a footer that does not say Esc cancels", _swap(BROWSER_PROMPT, "Esc to cancel · Tab to amend",
                                                        "Tab to amend · Enter to confirm"), TOOL),
        ("no 'Do you want to proceed?'", _swap(BROWSER_PROMPT, "Do you want to proceed?", "Allow it?"), TOOL),
        ("a prompt naming another tool", _swap(BROWSER_PROMPT, "navigate (MCP)", "computer (MCP)"), TOOL),
        ("a prompt naming another server", _swap(BROWSER_PROMPT, "claude-in-chrome - navigate",
                                                "other-server - navigate"), TOOL),
        ("the 'No' option highlighted", _swap(_swap(BROWSER_PROMPT, "❯ 1. Yes", "  1. Yes"),
                                              "  3. No,", "❯ 3. No,"), TOOL),
        ("'don't ask again' highlighted", _swap(_swap(BROWSER_PROMPT, "❯ 1. Yes", "  1. Yes"),
                                                "  2. Yes, and", "❯ 2. Yes, and"), TOOL),
        ("two marked lines", _swap(BROWSER_PROMPT, "  3. No,", "❯ 3. No,"), TOOL),
    )
    for what, tail, tool in near:
        key, why = drv.accepting_key(_dialog(tail), tool)
        check(f"accept: {what} is not pressed", key is None and why, f"{key!r} {why}")
    check("accept: the tool pre-check takes only the browser's",
          drv.accepts_tool(TOOL) and not drv.accepts_tool("Bash") and not drv.accepts_tool(None)
          and not ocmod.Opencode().accepts_tool(TOOL))


def test_a_waiting_browser_tool_is_said_yes_to_once(home: Path) -> None:
    con, term = _world(home, BROWSER_PROMPT)
    try:
        autoaccept.sweep(con, lambda _m: None)
        check("sweep: Enter is pressed once on the prompt", term.sent == [("", True)], str(term.sent))
        check("sweep: and the answer is on the record",
              "dialog_accepted" in _kinds(con), str(_kinds(con)))
    finally:
        con.close()


def test_nothing_is_pressed_when_the_setting_is_off_or_the_wait_is_not_the_browsers(home: Path) -> None:
    con, term = _world(home / "off", BROWSER_PROMPT)
    try:
        autoaccept.set_enabled(False)
        check("setting: it reads off once set", not autoaccept.enabled())
        autoaccept.sweep(con, lambda _m: None)
        check("setting: off, no prompt is pressed", term.sent == [], str(term.sent))
    finally:
        con.close()
    con, term = _world(home / "bash", BROWSER_PROMPT, tool="Bash")
    try:
        autoaccept.sweep(con, lambda _m: None)
        check("sweep: a Bash wait is a person's to answer, even behind the browser's screen",
              term.sent == [], str(term.sent))
    finally:
        con.close()
    con, term = _world(home / "other", _swap(BROWSER_PROMPT, "navigate (MCP)", "computer (MCP)"))
    try:
        autoaccept.sweep(con, lambda _m: None)
        check("sweep: a screen that is not the waiting tool's prompt is not pressed",
              term.sent == [], str(term.sent))
    finally:
        con.close()


def test_a_stale_wait_is_not_pressed_on_a_later_prompt_that_looks_like_it(home: Path) -> None:
    """T100: the wait file was left by a browser prompt that was answered
    without its PostToolUse, and the agent has since gone on. A later prompt
    whose screen happens to pass every check must not be pressed on the word
    of that file."""
    con, term = _world(home, BROWSER_PROMPT)
    try:
        first = db.add_event(con, "backend", "PermissionRequest", {}) or 0
        waiting.note("backend", TOOL, {"url": "https://example.com"}, after_event_id=first)
        db.add_event(con, "backend", "commit", {})
        autoaccept.sweep(con, lambda _m: None)
        check("stale: a wait the agent has moved on from is not pressed", term.sent == [], str(term.sent))
        check("stale: and it is cleared, not left to be believed later", waiting.read("backend") is None)
    finally:
        con.close()


def test_the_setting_defaults_on_and_a_stuck_prompt_is_not_pressed_every_tick(home: Path) -> None:
    con, term = _world(home, BROWSER_PROMPT, ignores_enter=True)
    try:
        check("setting: on by default", autoaccept.enabled())
        now = int(time.time())
        autoaccept.sweep(con, lambda _m: None, now)
        check("stuck: Enter that leaves the prompt open is reported",
              term.sent == [("", True)] and "dialog_accept_failed" in _kinds(con), str(term.sent))
        autoaccept.sweep(con, lambda _m: None, now + 10)
        check("stuck: and not pressed again within the backoff", len(term.sent) == 1, str(term.sent))
        autoaccept.sweep(con, lambda _m: None, now + autoaccept.STUCK_BACKOFF_S)
        check("stuck: pressed again once the backoff is over", len(term.sent) == 2, str(term.sent))
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

    orcamod.confirm_trust = confirm
    orcamod.wait_idle = lambda h, timeout_ms=0: True
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


def test_a_wake_meeting_a_declared_trust_screen_escalates_with_the_setting_off(home: Path) -> None:
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
        check("trust: with the setting off the same wake escalates and does not confirm",
              not ok and pressed == []
              and db.get_agent_state(con, "backend")["state"] == dmn_states.STALLED, str(pressed))
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
        run(lambda: test_the_browser_prompt_is_said_yes_to_only_when_it_is_exactly_that(Path(td) / 'aa0'))
        run(lambda: test_a_waiting_browser_tool_is_said_yes_to_once(Path(td) / 'aa1'))
        run(lambda: test_nothing_is_pressed_when_the_setting_is_off_or_the_wait_is_not_the_browsers(Path(td) / 'aa2'))
        run(lambda: test_the_setting_defaults_on_and_a_stuck_prompt_is_not_pressed_every_tick(Path(td) / 'aa3'))
        run(lambda: test_a_stale_wait_is_not_pressed_on_a_later_prompt_that_looks_like_it(Path(td) / 'aa8'))
        run(lambda: test_a_declared_folders_trust_screen_is_accepted_by_the_blocked_sweep(Path(td) / 'aa4'))
        run(lambda: test_the_trust_screen_is_left_alone_when_consent_or_the_setting_is_missing(Path(td) / 'aa5'))
        run(lambda: test_a_wake_meeting_a_declared_trust_screen_escalates_with_the_setting_off(Path(td) / 'aa7'))
        run(lambda: test_auto_accept_config_round_trips(Path(td) / 'aa6'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
