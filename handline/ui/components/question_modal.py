"""An agent's pending AskUserQuestion, offered to the founder from its chat page (T304) instead
of the raw JSON its tool call shows there: each option numbered (a checkbox when multiSelect),
and its "Type something" control opening the ordinary TextPrompt for free text. What is pressed
is sent through `actions.request_block_answer` - this only ever offers what the daemon's block
holds, never a standing rule of its own (an
AskUserQuestion prompt is a one-off Claude asked, the same reasoning PermissionModal's docstring
gives for having no "always")."""
from __future__ import annotations

import asyncio

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Static

from ...infra import textsafe
from .close_bar import CloseCorner
from .text_prompt import TextPrompt


class QuestionModal(CloseCorner, ModalScreen["tuple[bool, str] | None"]):
    """Dismisses with `press`'s own (ok, said) once it has actually run, or
    None when closed by hand before anything was sent - the terminal is left
    as it is either way. Pressing an option runs `press` on a thread; every
    control here disables and the hint says so until it returns, since it is
    a real subprocess call to `orca` and can take a moment. On a single-select
    question Other submits by itself the moment its text is typed. A
    multiSelect question's Other only records the text and returns to the
    checklist - Submit picked is what sends, with whatever boxes are checked
    at that point."""

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

    def on_mount(self) -> None:
        self.mark_close_corner()

    def close(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.close()

    def __init__(self, found: dict, press, on_start=lambda: None) -> None:
        super().__init__()
        self.found = found
        self._press = press
        self._on_start = on_start
        self._checked: set[int] = set(i for i, opt in enumerate(found["options"]) if opt.get("checked"))
        self._other_text: str | None = None
        self._sending = False

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
        if self._sending:
            return
        if event.button.id == "other":
            await self._other()
        elif event.button.id == "submit":
            await self._submit(tuple(sorted(self._checked)), self._other_text)
        else:
            await self._submit((int(event.button.id.split("_")[1]),), None)

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        event.stop()
        if self._sending:
            return
        i = int(event.checkbox.id.split("_")[1])
        self._checked.add(i) if event.value else self._checked.discard(i)

    async def on_key(self, event) -> None:
        ch = event.character
        if not ch or self._sending:
            return
        d = self.found
        if ch.isdigit():
            n = int(ch)
            if 1 <= n <= len(d["options"]):
                event.stop()
                if d["multi_select"]:
                    self.query_one(f"#opt_{n - 1}", Checkbox).toggle()
                else:
                    await self._submit((n - 1,), None)
            elif n == d["other_number"]:
                event.stop()
                await self._other()
        elif ch.lower() == "o" and d["other_number"] is not None:
            event.stop()
            await self._other()

    async def _other(self) -> None:
        if self._sending:
            return
        # A callback, not push_screen_wait: that needs a worker, which this handler only has
        # when the modal happened to be pushed from one.
        self.app.push_screen(TextPrompt(f"{self.found['question']} - your own answer:"), self._typed)

    async def _typed(self, text: str | None) -> None:
        if text is None:
            return
        if not self.found["multi_select"]:
            await self._submit((), text)
            return
        self._other_text = text
        label = f"{self.found['other_number']}. Other: {textsafe.oneline(text)}"
        self.query_one("#other", Button).label = Text(label)

    async def _submit(self, choices: tuple[int, ...], text: str | None) -> None:
        self._sending = True
        self._on_start()
        self.query_one("#hint", Static).update("sending to the terminal - this can take a moment")
        for w in self.query(Button):
            w.disabled = True
        for w in self.query(Checkbox):
            w.disabled = True
        try:
            result = await asyncio.to_thread(self._press, choices, text)
        except Exception as e:  # noqa: BLE001 - a press that raised must still let the modal go
            result = (False, f"{type(e).__name__}: {e}")
        self.dismiss(result)

    async def action_dismiss(self, result=None) -> None:
        if self._sending:
            return      # answering already: esc would race its own dismiss once press() returns
        await super().action_dismiss(result)
