"""One control for a value that is usually one of a known few but may be anything: a line to
type in, with the known values listed under it while it has the focus. Picking one fills the
line and commits it; typing narrows the list."""
from __future__ import annotations

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import Input, OptionList

from .ime import ImeInput


class PickOrType(Vertical):
    DEFAULT_CSS = """
    PickOrType { height: auto; width: 40; }
    PickOrType OptionList { height: auto; max-height: 6; display: none; border: none; padding: 0 1; }
    PickOrType.-open OptionList { display: block; }
    """

    class Picked(Message):
        """A known value was picked: the line holds it and it is meant to be saved."""

        def __init__(self, input: Input) -> None:
            super().__init__()
            self.input = input

    def __init__(self, known: list[str], input_id: str, **kw) -> None:
        super().__init__(**kw)
        self.known, self.input_id = list(known), input_id

    def compose(self) -> ComposeResult:
        yield ImeInput(id=self.input_id, compact=True)
        yield OptionList()

    @property
    def input(self) -> Input:
        return self.query_one(f"#{self.input_id}", Input)

    def _show(self) -> None:
        text = self.input.value.strip().lower()
        shown = [m for m in self.known if text in m.lower()] if text not in [m.lower() for m in self.known] else self.known
        box = self.query_one(OptionList)
        box.clear_options()
        box.add_options(shown)
        self.set_class(bool(shown), "-open")

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        if isinstance(event.widget, Input):
            self._show()

    def on_descendant_blur(self, event: events.DescendantBlur) -> None:
        if isinstance(event.widget, Input) and not self.query_one(OptionList).has_focus:
            self.remove_class("-open")

    def on_input_changed(self, event: Input.Changed) -> None:
        if self.input.has_focus:
            self._show()

    def on_key(self, event: events.Key) -> None:
        box = self.query_one(OptionList)
        if event.key == "down" and self.input.has_focus and self.has_class("-open"):
            event.stop()
            box.focus()
            box.highlighted = 0
        elif event.key in ("escape", "up") and box.has_focus and (event.key == "escape" or box.highlighted == 0):
            event.stop()
            self.input.focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.input.value = str(event.option.prompt)
        self.remove_class("-open")
        self.input.focus()
        self.post_message(self.Picked(self.input))
