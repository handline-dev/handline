from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Select, Static

from .dialog import Dialog
from .ime import ImeInput

_KINDS = (("trust a folder", "folder"), ("answer a dialog", "dialog"))


class AnswerAdd(Dialog):
    """The form for one new auto-answer: a folder to trust, or a dialog rule (the prompt's vendor
    and title, and the option to choose). Dismisses with the fields for `actions.add_answer`, or
    None."""

    CLOSE_CORNER = False        # Esc is the Cancel button's key

    FOCUS = "#ans_folder"
    DEFAULT_CSS = """
    AnswerAdd #title { margin-bottom: 1; }
    AnswerAdd .frow { height: 1; margin-bottom: 1; }
    AnswerAdd .flabel { width: 14; color: $text-muted; }
    AnswerAdd .frow ImeInput { width: 1fr; }
    AnswerAdd #ans_buttons { height: 1; margin-top: 1; }
    AnswerAdd #ans_buttons Button { width: auto; min-width: 0; margin-right: 2; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text("add an auto-answer", style="bold"), id="title")
            yield Select(_KINDS, id="ans_kind", allow_blank=False, compact=True, value="folder")
            with Horizontal(classes="frow", id="row_folder"):
                yield Static("folder", classes="flabel")
                yield ImeInput(id="ans_folder", compact=True, placeholder="/path/to/folder")
            for key, label, hint in (("vendor", "vendor", "claude"), ("title", "prompt title", "the prompt's title"),
                                     ("label", "choose", "the option to choose")):
                with Horizontal(classes="frow dialog_row"):
                    yield Static(label, classes="flabel")
                    yield ImeInput(id=f"ans_{key}", compact=True, placeholder=hint)
            with Horizontal(id="ans_buttons"):
                yield Button("Add", id="ans_add", variant="primary", compact=True)
                yield Button("Cancel", id="ans_cancel", compact=True)

    def on_mount(self) -> None:
        self._show_kind("folder")

    def _show_kind(self, kind: str) -> None:
        self.query_one("#row_folder").display = kind == "folder"
        for row in self.query(".dialog_row"):
            row.display = kind == "dialog"

    def on_select_changed(self, event: Select.Changed) -> None:
        event.stop()
        self._show_kind(str(event.value))

    def fields(self) -> dict:
        """What is typed, for the kind chosen; the other kind's fields are left out."""
        if self.query_one("#ans_kind", Select).value == "folder":
            return {"folder": self.query_one("#ans_folder", ImeInput).value}
        return {k: self.query_one(f"#ans_{k}", ImeInput).value for k in ("vendor", "title", "label")}

    def on_input_submitted(self, event) -> None:
        event.stop()
        self.finish(self.fields())

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.finish(self.fields() if event.button.id == "ans_add" else None)
