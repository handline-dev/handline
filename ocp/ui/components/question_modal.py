"""An agent's pending AskUserQuestion, offered to the founder from its chat page (T304) instead
of the raw JSON its tool call shows there: each option numbered (a checkbox when multiSelect),
and its "Type something" control opening the ordinary TextPrompt for free text. What is pressed,
and what keys that sends to the terminal, is `actions.answer_question`'s - this only ever offers
what `actions.pending_question` read off the real screen, never a standing rule of its own (an
AskUserQuestion prompt is a one-off Claude asked, the same reasoning PermissionModal's docstring
gives for having no "always")."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Static

from ...infra import textsafe
from .text_prompt import TextPrompt


class QuestionModal(ModalScreen["tuple[tuple[int, ...], str | None] | None"]):
    """Dismisses with (choices, text) - `choices` the 0-based option indexes
    picked, `text` Other's own typed answer, or None when closed by hand: the
    terminal is left as it is. On a single-select question Other submits by
    itself (choices then empty) the moment its text is typed, matching the
    real screen's own one-shot number-then-text-then-Enter. A multiSelect
    question's Other only records the text and returns to the checklist -
    Submit picked is what dismisses, with whatever boxes are checked at that
    point (T322 follow-up, founder capture 2026-09-28: checked boxes used to
    be silently dropped the moment Other was answered)."""

    BINDINGS = [Binding("escape", "dismiss(None)", "close")]
    DEFAULT_CSS = """
    QuestionModal { align: center middle; background: $background 60%; }
    QuestionModal #dialog { width: 76; max-width: 92%; height: auto; padding: 1 2;
                            border: thick $primary; background: $panel; }
    QuestionModal #header { color: $text-muted; }
    QuestionModal #question { margin: 1 0 1 0; text-style: bold; }
    QuestionModal Button { width: 100%; margin-bottom: 1; }
    QuestionModal #submit { margin-top: 1; }
    QuestionModal #hint { color: $text-muted; }
    """

    def __init__(self, found: dict) -> None:
        super().__init__()
        self.found = found
        self._checked: set[int] = set(i for i, opt in enumerate(found["options"]) if opt.get("checked"))
        self._other_text: str | None = None

    def compose(self) -> ComposeResult:
        d = self.found
        with Vertical(id="dialog"):
            yield Static(Text(f"{textsafe.oneline(d['agent'])} is asking - {textsafe.oneline(d['header'])}",
                              style="dim"), id="header")
            yield Static(Text(textsafe.oneline(d["question"])), id="question")
            for i, opt in enumerate(d["options"]):
                label = f"{i + 1}. {textsafe.oneline(opt['label'])}"
                if opt.get("description"):
                    label += f"  ({textsafe.oneline(opt['description'])})"
                if d["multi_select"]:
                    yield Checkbox(label, id=f"opt_{i}", value=i in self._checked, compact=True)
                else:
                    yield Button(Text(label), id=f"opt_{i}", compact=True)
            if d["other_number"] is not None:
                other = f"{d['other_number']}. " + (f"Other: {textsafe.oneline(d['other_text'])}"
                                                    if d["other_text"] else "Other (type your own)")
                yield Button(Text(other), id="other", compact=True)
            if d["multi_select"]:
                yield Button("Submit picked", id="submit", variant="warning", compact=True)
            yield Static("click an option or press its number · esc closes (the terminal is left as it is)",
                        id="hint")

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "other":
            await self._other()
        elif event.button.id == "submit":
            self.dismiss((tuple(sorted(self._checked)), self._other_text))
        else:
            self.dismiss(((int(event.button.id.split("_")[1]),), None))

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        event.stop()
        i = int(event.checkbox.id.split("_")[1])
        self._checked.add(i) if event.value else self._checked.discard(i)

    def on_key(self, event) -> None:
        ch = event.character
        if not ch:
            return
        d = self.found
        if ch.isdigit():
            n = int(ch)
            if 1 <= n <= len(d["options"]):
                event.stop()
                if d["multi_select"]:
                    self.query_one(f"#opt_{n - 1}", Checkbox).toggle()
                else:
                    self.dismiss(((n - 1,), None))
            elif n == d["other_number"]:
                event.stop()
                self.run_worker(self._other())
        elif ch.lower() == "o" and d["other_number"] is not None:
            event.stop()
            self.run_worker(self._other())

    async def _other(self) -> None:
        text = await self.app.push_screen_wait(TextPrompt(f"{self.found['question']} - your own answer:"))
        if text is None:
            return
        if not self.found["multi_select"]:
            self.dismiss(((), text))
            return
        self._other_text = text
        label = f"{self.found['other_number']}. Other: {textsafe.oneline(text)}"
        self.query_one("#other", Button).label = Text(label)
