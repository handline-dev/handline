"""Tests: claude's bypass-permissions warning, raised on the first launch with skipped
permissions in a config directory that has not accepted it.

Run on its own: python3 tests/session/test_bypass_prompt.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

# The founder's capture, whole: unnumbered options, the cursor on "No, exit".
_RULE = "─" * 150


def _screen(cursor: str = "No, exit") -> list[str]:
    def row(label: str) -> str:
        return f"  ❯ {label}" if label == cursor else f"    {label}"
    return ["", _RULE,
            "  WARNING: Claude Code running in Bypass Permissions mode",
            "",
            "  In Bypass Permissions mode, Claude Code will not ask for your approval before running "
            "potentially dangerous commands.",
            "  This mode should only be used in a sandboxed container/VM that has restricted internet "
            "access and can easily be restored if damaged.",
            "",
            "  By proceeding, you accept all responsibility for actions taken while running in "
            "Bypass Permissions mode.",
            "",
            "  https://code.claude.com/docs/en/security",
            "",
            row("No, exit"),
            row("Yes, I accept"),
            "",
            "  Enter to confirm · Esc to cancel"]


class _BypassTerm(support._Terminal):
    """The warning as it behaves: Down moves the cursor, Enter takes the option under it - on
    "No, exit" that ends the session - and the prompt is behind it."""

    def __init__(self, cursor: str = "No, exit", down_works: bool = True, enter_works: bool = True):
        super().__init__(_screen(cursor))
        self.cursor, self.down_works, self.enter_works, self.exited = cursor, down_works, enter_works, False

    def send(self, handle, text, enter=True):
        self.sent.append((text, enter))
        if text == "\x1b[B" and self.down_works:
            self.cursor = "Yes, I accept"
            self.tail = _screen(self.cursor)
        elif text == "" and enter and self.enter_works:
            if self.cursor == "No, exit":
                self.exited = True
                self.tail = ["$ "]
            else:
                self.tail = list(AT_THE_PROMPT)
        return True, ""


def _world(home: Path, allowed: bool, **kw):
    con, term = support._decline_world(home)
    bt = _BypassTerm(**kw)
    bt.install()
    termmod.wait_idle = lambda h, timeout_ms=0: True
    termmod.time.sleep = lambda s: None
    support.agents.register("backend", skip_permissions=allowed)
    if not allowed:
        support.agents.register("backend", skip_permissions=False)
    return con, bt


def test_the_founders_screen_is_recognised_and_its_highlight_read(home: Path) -> None:
    from handline.vendors import claude
    check("screen: the cursor on 'No, exit' is read as that option",
          claude._bypass_screen(_screen("No, exit")) == "No, exit")
    check("screen: and on 'Yes, I accept'", claude._bypass_screen(_screen("Yes, I accept")) == "Yes, I accept")
    check("screen: no cursor row is showing-but-unread, not absent", claude._bypass_screen(_screen("")) == "")
    check("screen: the folder-trust screen is not it",
          claude._bypass_screen(LIVE_TRUST_PROMPT_CAPTURE.split("\n")) is None)
    cut = [r for r in _screen("No, exit") if "No, exit" not in r and "Yes, I accept" not in r]
    check("screen: the warning with its options cut off is not read as one to answer",
          claude._bypass_screen(cut) is None)
    quoted = ["  an agent quoting: WARNING: Claude Code running in Bypass Permissions mode"] + AT_THE_PROMPT
    check("screen: a transcript that quotes the words is not it", claude._bypass_screen(quoted) is None)


def test_a_granted_agent_has_it_accepted_by_a_down_and_an_enter(home: Path) -> None:
    con, term = _world(home, allowed=True)
    try:
        ok, why = dmn_lifecycle._clear_trust_prompt(con, "backend", "term_x", lambda _m: None)
        check("allowed: the wake gate clears it", ok, why)
        check("allowed: Down first, then Enter - never Enter on 'No, exit'",
              term.sent == [("\x1b[B", False), ("", True)] and not term.exited, str(term.sent))
        check("allowed: and it is on the record",
              "bypass_accepted" in [r["kind"] for r in con.execute("SELECT kind FROM events WHERE agent = 'backend'")])
    finally:
        con.close()


def test_an_agent_not_granted_gets_nothing_typed_and_the_wake_fails_with_the_reason(home: Path) -> None:
    con, term = _world(home / "off", allowed=False)
    try:
        ok, why = dmn_lifecycle._clear_trust_prompt(con, "backend", "term_x", lambda _m: None)
        check("not allowed: the gate refuses, saying why", not ok and "not granted" in why
              and "nothing was typed" in why, why)
        check("not allowed: no key of any kind was sent", term.sent == [] and not term.exited, str(term.sent))
        blockedmod._checked.clear()
        blockedmod.sweep(con, lambda _m: None, int(time.time()))
        check("not allowed: nor by the blocked sweep, which leaves the block for the founder",
              term.sent == [] and db.block_get(con, "backend") is not None, str(term.sent))
    finally:
        con.close()


def test_the_blocked_sweep_accepts_it_for_a_granted_agent(home: Path) -> None:
    con, term = _world(home / "sweep", allowed=True)
    try:
        blockedmod._checked.clear()
        blockedmod.sweep(con, lambda _m: None, int(time.time()))
        check("sweep: accepted for a granted agent, and the block ends",
              term.sent == [("\x1b[B", False), ("", True)] and db.block_get(con, "backend") is None, str(term.sent))
    finally:
        con.close()


def test_enter_is_never_pressed_with_the_cursor_where_it_would_exit(home: Path) -> None:
    con, term = _world(home / "stuck", allowed=True, down_works=False)
    try:
        ok, why = dmn_lifecycle._clear_trust_prompt(con, "backend", "term_x", lambda _m: None)
        check("stuck: a Down that does not move the cursor ends in a refusal, with no Enter",
              not ok and ("", True) not in term.sent and not term.exited, f"{why} {term.sent}")
    finally:
        con.close()
    con, term = _world(home / "yes", allowed=True, cursor="Yes, I accept")
    try:
        ok, why = dmn_lifecycle._clear_trust_prompt(con, "backend", "term_x", lambda _m: None)
        check("cursor already on Yes: only Enter", ok and term.sent == [("", True)], str(term.sent))
    finally:
        con.close()
    con, term = _world(home / "enter", allowed=True, enter_works=False)
    try:
        ok, why = dmn_lifecycle._clear_trust_prompt(con, "backend", "term_x", lambda _m: None)
        check("an Enter that leaves the warning up is reported, not claimed", not ok and "still on screen" in why, why)
    finally:
        con.close()


class _Both(_BypassTerm):
    """A first launch on a config directory that has run nothing: the folder-trust screen and the
    bypass warning, in `order`, then the prompt. Neither is answered but by its own key."""

    def __init__(self, order):
        super().__init__()
        self.order = list(order)
        self._show()

    def _show(self):
        if not self.order:
            self.tail = list(AT_THE_PROMPT)
        elif self.order[0] == "trust":
            self.tail = LIVE_TRUST_PROMPT_CAPTURE.split("\n")
        else:
            self.cursor = "No, exit"
            self.tail = _screen(self.cursor)

    def send(self, handle, text, enter=True):
        self.sent.append((text, enter))
        if not self.order or self.order[0] != "bypass":
            return True, ""
        if text == "\x1b[B":
            self.cursor = "Yes, I accept"
            self.tail = _screen(self.cursor)
        elif text == "" and enter:
            if self.cursor == "No, exit":
                self.exited = True
                self.tail = ["$ "]
                self.order = []
            else:
                self.order.pop(0)
                self._show()
        return True, ""

    def confirm_trust(self, handle):
        if self.order and self.order[0] == "trust":
            self.order.pop(0)
            self._show()
        return True, "trusted"


def _both_world(home: Path, order):
    con, _ = support._decline_world(home)
    term = _Both(order)
    term.install()
    termmod.confirm_trust = term.confirm_trust
    termmod.wait_idle = lambda h, timeout_ms=0: True
    termmod.time.sleep = lambda s: None
    spawnmod.worktree_path = lambda w: LIVE_TRUST_PROMPT_FOLDER
    trustmod.is_declared = lambda p: True
    support.agents.register("backend", skip_permissions=True)
    return con, term


def test_both_screens_are_answered_whichever_comes_first(home: Path) -> None:
    for order in (("trust", "bypass"), ("bypass", "trust")):
        con, term = _both_world(home / "-".join(order), order)
        try:
            ok, why = dmn_lifecycle._clear_trust_prompt(con, "backend", "term_x", lambda _m: None)
            check(f"both, {order[0]} then {order[1]}: the gate clears them both and leaves the prompt",
                  ok and not term.order and not term.exited and term.tail == list(AT_THE_PROMPT),
                  f"{why} {term.order} {term.sent}")
        finally:
            con.close()
    con, term = _both_world(home / "denied", ("trust", "bypass"))
    try:
        support.agents.register("backend", skip_permissions=False)
        ok, why = dmn_lifecycle._clear_trust_prompt(con, "backend", "term_x", lambda _m: None)
        check("both: trust answered, then a bypass warning the agent may not accept stops the gate, untouched",
              not ok and "not granted" in why and term.order == ["bypass"]
              and ("", True) not in term.sent and not term.exited, f"{why} {term.sent}")
    finally:
        con.close()


def test_a_vendor_with_no_bypass_warning_wakes_as_before(home: Path) -> None:
    from handline.vendors import opencode
    con, term = _both_world(home / "oc", ())
    try:
        real = termmod._driver_for
        termmod._driver_for = lambda handle: opencode.Opencode()
        try:
            term.tail = _screen("No, exit")
            check("opencode: the bypass question is no, whatever is on screen", not termmod.bypass_prompt_showing("term_x"))
            ok, why = dmn_lifecycle._clear_trust_prompt(con, "backend", "term_x", lambda _m: None)
        finally:
            termmod._driver_for = real
        check("opencode: the wake gate clears and sends nothing", ok and term.sent == [], f"{why} {term.sent}")
    finally:
        con.close()


def test_an_unreadable_agent_yaml_is_told_apart_from_no_grant(home: Path) -> None:
    con, term = _world(home / "bad", allowed=False)
    try:
        with support.agents.agent_file("backend").open("a") as f:
            f.write("tampered: true\n")        # not what the seal was made over, so it cannot be read
        ok, why = dmn_lifecycle._clear_trust_prompt(con, "backend", "term_x", lambda _m: None)
        check("unreadable: refused, saying the file could not be read rather than that no grant exists",
              not ok and "could not be read (" in why and term.sent == [], why)
    finally:
        con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_the_founders_screen_is_recognised_and_its_highlight_read(Path(td) / 'bp0'))
        run(lambda: test_a_granted_agent_has_it_accepted_by_a_down_and_an_enter(Path(td) / 'bp1'))
        run(lambda: test_an_agent_not_granted_gets_nothing_typed_and_the_wake_fails_with_the_reason(Path(td) / 'bp2'))
        run(lambda: test_the_blocked_sweep_accepts_it_for_a_granted_agent(Path(td) / 'bp3'))
        run(lambda: test_enter_is_never_pressed_with_the_cursor_where_it_would_exit(Path(td) / 'bp4'))
        run(lambda: test_both_screens_are_answered_whichever_comes_first(Path(td) / 'bp5'))
        run(lambda: test_a_vendor_with_no_bypass_warning_wakes_as_before(Path(td) / 'bp6'))
        run(lambda: test_an_unreadable_agent_yaml_is_told_apart_from_no_grant(Path(td) / 'bp7'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
