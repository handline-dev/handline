"""Tests: text the daemon typed at an agent whose Enter was lost - found in the agent's input box
and sent on the next tick, never twice, never a person's own draft.

Run on its own: python3 tests/daemon/test_unsent_input.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

RULE = "─" * 60
TEXT = "#41 from product: please look at the retry loop"


def _screen(box: list[str], above: list[str] | None = None) -> list[str]:
    return (above or ["some transcript"]) + [RULE, *box, RULE, "  ⏵⏵ auto mode on", "  ? for shortcuts"]


class _World:
    """An agent that can be typed at, a screen the test sets, and every key sent to it."""

    def __init__(self, home: Path, screen: list[str]):
        _daemon_home(home, 20)
        sessions_are("backend")
        self.screen, self.sent = screen, []
        termmod.available = lambda: True
        termmod.alive = lambda h: True
        termmod.screen_blocker = lambda h: None
        termmod.typeable_now = lambda h: True
        termmod._screen_tail = lambda h: self.screen
        termmod.SHELL_SETTLE_S = 0

        self.enter_works, self.ctrl_c_clears = False, True

        def send(handle, text, enter=True):
            self.sent.append((text, enter))
            if text == "\x03":
                if self.ctrl_c_clears:
                    self.screen = _screen(["❯"])
            elif text:
                self.screen = _screen(["❯" if enter and self.enter_works else f"❯ {text}"])
            elif self.enter_works:
                self.screen = _screen(["❯"], above=[TEXT])
            return True, ""
        termmod.send = send
        termmod.send_to_session = lambda handle, text, *a, **k: send(handle, text)
        self.con = db.connect()
        db.set_terminal(self.con, "backend", "term_x", "wt")
        db.set_agent_state(self.con, "backend", dmn_states.IDLE)

    def typed(self, text: str = TEXT, ago: int = 5) -> None:
        dmn_delivery._send("term_x", text)
        dmn_delivery._mark_typed(self.con, "backend")
        self.age(ago)
        self.sent.clear()

    def age(self, seconds: int) -> None:
        rec = json.loads(db.get_meta(self.con, "unsent:backend"))
        rec["at"] -= seconds
        db.set_meta(self.con, "unsent:backend", json.dumps(rec))

    def tick(self) -> bool:
        return dmn_delivery.recover_unsent(self.con, "backend", lambda _m: None)

    def record(self) -> dict | None:
        raw = db.get_meta(self.con, "unsent:backend")
        return json.loads(raw) if raw else None


def test_text_left_in_the_box_is_submitted_on_the_next_tick(home: Path) -> None:
    w = _World(home, _screen(["❯"]))
    try:
        w.typed()
        w.screen = _screen([f"❯ {TEXT}"])
        check("unsent: our text in the box is held for, and Enter is pressed", w.tick() and w.sent == [("", True)],
              str(w.sent))
        check("unsent: the gate refuses more typing while it sits there",
              "unsent" in (dmn_delivery._type_gate(w.con, "backend", "term_x") or ""))
        w.screen = _screen(["❯"], above=[TEXT])
        check("unsent: once the box is empty nothing is held and the record is gone",
              not w.tick() and w.record() is None and dmn_delivery._type_gate(w.con, "backend", "term_x") is None)
    finally:
        w.con.close()


def test_text_visible_only_above_the_prompt_is_not_sent_again(home: Path) -> None:
    w = _World(home, _screen(["❯"]))
    try:
        w.typed()
        w.screen = _screen(["❯"], above=[f"❯ {TEXT}", "● on it"])
        check("unsent: a submitted line in the transcript is not pressed or retyped",
              not w.tick() and w.sent == [] and w.record() is None, str(w.sent))
    finally:
        w.con.close()


def test_a_persons_draft_in_the_box_is_never_sent(home: Path) -> None:
    w = _World(home, _screen(["❯"]))
    try:
        w.typed()
        w.screen = _screen(["❯ remind me to look at the mi"])
        check("unsent: a draft that is not our text gets no Enter", not w.tick() and w.sent == [], str(w.sent))
        w.screen = _screen([f"❯ {TEXT} and also this"])
        check("unsent: ours with a person's words after it is not ours either",
              not w.tick() and w.sent == [], str(w.sent))
    finally:
        w.con.close()


def test_an_enter_that_does_not_take_retypes_once_then_stops(home: Path) -> None:
    w = _World(home, _screen([f"❯ {TEXT}"]))
    try:
        w.ctrl_c_clears = False
        w.typed()
        w.tick()
        check("unsent: the first tick presses Enter", w.sent == [("", True)], str(w.sent))
        check("unsent: it is not pressed again before the wait is up", w.tick() and w.sent == [("", True)])
        w.age(11)
        # the Ctrl-C leaves the box as it was: the text must not be typed on top of it
        check("unsent: a box that will not clear is blocked, not typed into",
              w.tick() and w.record()["blocked"] and w.sent[-1] == ("\x03", False), str(w.sent))
        n = len(w.sent)
        w.age(60)
        check("unsent: blocked presses and types nothing more, and says so",
              w.tick() and len(w.sent) == n and dmn_delivery.held_reason("backend", int(time.time())) is not None)
        # a box that does clear: typed again once, Enter once, then blocked
        db.clear_meta(w.con, "unsent:backend")
        w.ctrl_c_clears = True
        w.screen = _screen([f"❯ {TEXT}"])
        w.typed()
        w.tick()
        w.age(11)
        w.sent.clear()
        w.tick()
        check("unsent: a cleared box gets the text typed once", w.sent == [("\x03", False), (TEXT, True)], str(w.sent))
        w.age(5)
        w.tick()
        w.age(11)
        w.sent.clear()
        w.tick()
        check("unsent: still unsent after the retry's Enter: blocked, nothing sent",
              w.record()["blocked"] and w.sent == [], str(w.sent))
    finally:
        w.con.close()


def test_the_composer_is_read_wrapped_and_collapsed(home: Path) -> None:
    long_text = "word " * 30
    box = ["❯ " + long_text[:70], "  " + long_text[70:140], "  " + long_text[140:].strip()]
    got = termmod.composer_text(_screen(box))
    check("composer: a wrapped box reads as one text", got == "".join(long_text.split()), got)
    check("composer: an empty box is empty", termmod.composer_text(_screen(["❯"])) == "")
    check("composer: no box is None", termmod.composer_text(["just a shell", "$ "]) is None)
    check("composer: wrapped typed text is ours", termmod.is_text_typed(got, long_text))
    paste = "a\nb\nc\nd\ne"
    check("composer: a paste placeholder is ours when the lines match",
          termmod.is_text_typed("[Pastedtext#1+4lines]", paste))
    check("composer: and not when typed text was one line",
          not termmod.is_text_typed("[Pastedtext#1+4lines]", "one line"))


def main() -> int:
    def tests(td):
        run(lambda: test_text_left_in_the_box_is_submitted_on_the_next_tick(Path(td) / "u1"))
        run(lambda: test_text_visible_only_above_the_prompt_is_not_sent_again(Path(td) / "u2"))
        run(lambda: test_a_persons_draft_in_the_box_is_never_sent(Path(td) / "u3"))
        run(lambda: test_an_enter_that_does_not_take_retypes_once_then_stops(Path(td) / "u4"))
        run(lambda: test_the_composer_is_read_wrapped_and_collapsed(Path(td) / "u5"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
