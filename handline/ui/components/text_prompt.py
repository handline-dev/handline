"""A one-line or multi-line text prompt over the current page."""
from __future__ import annotations

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Input, Static, TextArea

from ...infra import textsafe
from .dialog import Dialog
from .ime import LineBox, TextBox


class TextPrompt(Dialog):
    """One line (or, `multi`, several) read with the screen still up, in a
    Textual `Input` or `TextArea`. Enter sends and Esc backs out (None); a paste
    is kept whole, minus anything a terminal would obey. `context` is what is being answered, shown
    whole above the box: wrapped, every line, scrolling when it outgrows the space."""

    FOCUS = "#box"
    BINDINGS = [Binding("ctrl+c", "finish(None)", "cancel", show=False),
                Binding("pageup", "page(-1)", "message up", show=False, priority=True),
                Binding("pagedown", "page(1)", "message down", show=False, priority=True)]
    DEFAULT_CSS = """
    TextPrompt #context { height: auto; margin-bottom: 1; border-left: wide $primary;
                          padding-left: 1; }
    TextPrompt #prompt { height: auto; margin-bottom: 1; }
    TextPrompt #box { height: auto; min-height: 3; max-height: 12; }
    TextPrompt #hint { margin-top: 1; color: $text-muted; }
    """

    def __init__(self, prompt: str, multi: bool = False, initial: str = "", context: str = "") -> None:
        super().__init__()
        self.prompt, self.multi, self.initial, self.context = prompt, multi, initial, context

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            if self.context:
                with VerticalScroll(id="context"):
                    yield Static(Text("\n".join(textsafe.drawable(self.context))))
            yield Static(Text(textsafe.oneline(self.prompt), style="bold"), id="prompt")
            yield TextBox(id="box") if self.multi else LineBox(id="box")
            yield Static("enter sends" + (" · shift+enter new line" if self.multi else "")
                         + (" · pgup/pgdn scroll the message" if self.context else "")
                         + " · esc cancels", id="hint")

    # Under this many rows the dialog drops its padding and gaps to keep its frame on screen.
    COMPACT_BELOW = 16

    def on_resize(self) -> None:
        self._fit()

    def on_text_area_changed(self) -> None:
        self.call_after_refresh(self._fit)      # the box grew or shrank with what was typed

    def _fit(self) -> None:
        """Everything must stay on screen: the box is capped to what the message leaves it (at
        least 3 rows, 2 for the message), and the message gets what the box really takes."""
        height = self.size.height
        gap = 0 if height < self.COMPACT_BELOW else 1
        dialog, box = self.query_one("#dialog"), self.query_one("#box")
        dialog.styles.padding = (gap, 2)
        self.query_one("#prompt").styles.margin = (0, 0, gap, 0)
        self.query_one("#hint").styles.margin = (gap, 0, 0, 0)
        # frame 2, padding 2*gap, prompt 1+gap, hint as drawn (it wraps) +gap
        chrome = 2 + 2 * gap + 1 + gap + max(1, self.query_one("#hint").size.height) + gap
        if not self.context:
            box.styles.max_height = max(3, min(12, height - chrome))
            return
        chrome += gap       # between the message and the prompt
        box.styles.max_height = max(3, min(12, height - chrome - 2))
        ctx = self.query_one("#context")
        ctx.styles.margin = (0, 0, gap, 0)
        ctx.styles.max_height = max(1, min(height // 2, height - chrome - max(3, box.outer_size.height)))

    def check_action(self, action: str, parameters: tuple) -> bool | None:
        return bool(self.context) if action == "page" else True

    def action_page(self, direction: int) -> None:
        pane = self.query_one("#context", VerticalScroll)
        (pane.scroll_page_down if direction > 0 else pane.scroll_page_up)(animate=False)

    def on_mount(self) -> None:
        self.call_after_refresh(self._fit)
        box = self.query_one("#box")
        if self.initial and self.multi:
            box.text = self.initial
            box.move_cursor(box.document.end)
        elif self.initial:
            box.value = self.initial

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.finish(event.value)

    def on_text_box_send(self, event: "TextBox.Send") -> None:
        event.stop()
        self.finish(self.query_one("#box", TextArea).text)
