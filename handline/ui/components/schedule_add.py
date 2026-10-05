from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Select, Static

from ... import actions
from .dialog import Dialog
from .ime import ImeInput


CUSTOM = "custom"


class ScheduleAdd(Dialog):
    """The form for one new schedule: when (every day, weekdays, a weekday, every hour, every N
    minutes), the time it needs, and a one-line message. It builds the cron with
    `actions.schedule_cron` and dismisses with (cron, message), or None."""

    CLOSE_CORNER = False        # Esc is the Cancel button's key

    FOCUS = "#sa_message"
    DEFAULT_CSS = """
    ScheduleAdd #title { margin-bottom: 1; }
    ScheduleAdd .frow { height: 1; margin-bottom: 1; }
    ScheduleAdd .flabel { width: 10; color: $text-muted; }
    ScheduleAdd .frow Select { width: 1fr; }
    ScheduleAdd .frow ImeInput { width: 1fr; }
    ScheduleAdd .num { width: 6; }
    ScheduleAdd .unit { width: auto; padding: 0 1; }
    ScheduleAdd #sa_buttons { height: 1; margin-top: 1; }
    ScheduleAdd #sa_buttons Button { width: auto; min-width: 0; margin-right: 2; }
    """

    def __init__(self, initial: "tuple[str, str] | None" = None) -> None:
        """`initial` is the (cron, message) of a schedule being edited; the form opens on it."""
        super().__init__()
        self.initial = initial
        self.fields = (actions.schedule_fields(initial[0]) if initial else None) or {}
        # A time the form cannot build is kept as it is unless another is picked.
        self.custom = bool(initial and not self.fields)
        self.kinds = ([(f"keep {initial[0]}", CUSTOM)] if self.custom else []) + [(k, k) for k in actions.SCHEDULE_KINDS]

    def compose(self) -> ComposeResult:
        f = self.fields
        with Vertical(id="dialog"):
            yield Static(Text("edit a schedule" if self.initial else "add a schedule", style="bold"), id="title")
            with Horizontal(classes="frow"):
                yield Static("when", classes="flabel")
                yield Select(self.kinds, id="sa_kind", allow_blank=False, compact=True, value=self.kinds[0][1]
                             if self.custom else f.get("kind", "every day"))
            with Horizontal(classes="frow", id="sa_day_row"):
                yield Static("day", classes="flabel")
                yield Select([(d, i) for i, d in enumerate(actions.WEEKDAYS)], id="sa_day", allow_blank=False,
                             compact=True, value=f.get("day", 1))
            with Horizontal(classes="frow", id="sa_time_row"):
                yield Static("time", classes="flabel")
                yield ImeInput(id="sa_hour", classes="num", compact=True, restrict=r"[0-9]*", max_length=2, value=f.get("hour", "9"))
                yield Static(":", classes="unit")
                yield ImeInput(id="sa_minute", classes="num", compact=True, restrict=r"[0-9]*", max_length=2, value=f.get("minute", "00"))
            with Horizontal(classes="frow", id="sa_every_row"):
                yield Static("every", classes="flabel")
                yield ImeInput(id="sa_every", classes="num", compact=True, restrict=r"[0-9]*", max_length=2, value=f.get("every", "15"))
                yield Static("minutes", classes="unit")
            with Horizontal(classes="frow"):
                yield Static("message", classes="flabel")
                yield ImeInput(id="sa_message", compact=True, placeholder="one line, typed at the agent",
                              value=self.initial[1] if self.initial else "")
            with Horizontal(id="sa_buttons"):
                yield Button("Save" if self.initial else "Add", id="sa_add", variant="primary", compact=True)
                yield Button("Cancel", id="sa_cancel", compact=True)

    def on_mount(self) -> None:
        self._show()

    def _kind(self) -> str:
        return str(self.query_one("#sa_kind", Select).value)

    def _show(self) -> None:
        kind = self._kind()
        if kind == CUSTOM:
            for row in ("#sa_time_row", "#sa_every_row", "#sa_day_row"):
                self.query_one(row).display = False
            return
        self.query_one("#sa_day_row").display = kind == "every week"
        self.query_one("#sa_every_row").display = kind == "every N minutes"
        self.query_one("#sa_time_row").display = kind != "every N minutes"
        self.query_one("#sa_hour").display = kind != "every hour"

    def on_select_changed(self, event: Select.Changed) -> None:
        event.stop()
        self._show()

    def result(self) -> "tuple[str, str]":
        """(cron, message); ValueError when a field is wrong, saying which."""
        value = lambda i: self.query_one(f"#sa_{i}", ImeInput).value      # noqa: E731
        cron = self.initial[0] if self._kind() == CUSTOM else actions.schedule_cron(self._kind(), value("hour"), value("minute"),
                                     int(self.query_one("#sa_day", Select).value), value("every"))
        message = " ".join(value("message").split())
        if not message:
            raise ValueError("write the message to send")
        return cron, message

    def _add(self) -> None:
        try:
            self.finish(self.result())
        except ValueError as e:
            self.notify(str(e), severity="error", timeout=8, markup=False)

    def on_input_submitted(self, event) -> None:
        event.stop()
        self._add()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "sa_add":
            self._add()
        else:
            self.finish(None)
