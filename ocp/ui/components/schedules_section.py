from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Static

from ... import actions
from ...infra import textsafe
from ...team import agents
from .confirm import Confirm
from .schedule_add import ScheduleAdd
from .section_head import SectionHead


class SchedulesSection(Vertical):
    """One agent's schedules on its settings page: a line each (when, next run, message) with a ✎ and a ✕,
    and an Add button. Add, edit and remove write agent.yaml at once through `actions`, not with Save."""

    DEFAULT_CSS = """
    SchedulesSection { height: auto; }
    SchedulesSection .sc_row { height: 1; }
    SchedulesSection .sc_text { width: 1fr; height: 1; text-wrap: nowrap; text-overflow: ellipsis; }
    SchedulesSection .sc_x { width: 3; min-width: 0; margin-left: 1; }
    SchedulesSection #sc_add { width: auto; min-width: 0; margin-top: 1; }
    """

    def __init__(self, agent: str, **kw) -> None:
        super().__init__(**kw)
        self.agent = agent

    def compose(self) -> ComposeResult:
        yield SectionHead("schedules", "messages typed at this agent on a schedule")
        yield Vertical(id="sc_rows")
        yield Button("+ Add", id="sc_add", compact=True)

    def on_mount(self) -> None:
        self.load()

    def load(self) -> None:
        box = self.query_one("#sc_rows", Vertical)
        box.remove_children()
        try:
            rows = actions.schedule_rows(self.agent)
        except (ValueError, OSError) as e:
            box.mount(Static(Text(f"cannot read the schedules: {textsafe.oneline(str(e))}", style="red")))
            return
        if not rows:
            box.mount(Static(Text("none", style="dim")))
        for i, it in enumerate(rows, 1):
            when = actions.schedule_words(it["cron"])
            line = Text.assemble((f"{when:<20}", "bold"), (f"next {it['next']}   ", "dim"),
                                 textsafe.oneline(it["message"]))
            if when != it["cron"]:
                line.append(f"   {it['cron']}", style="dim")
            box.mount(Horizontal(Static(line, classes="sc_text"),
                                 Button("✎", id=f"sc_ed_{i}", compact=True, classes="sc_x"),
                                 Button("✕", id=f"sc_rm_{i}", variant="error", compact=True, classes="sc_x"),
                                 classes="sc_row"))

    def _call(self, verb: str, call) -> None:
        try:
            call()
            ok, said = True, f"schedule {verb}"
        except (ValueError, OSError, agents.ConfigError, PermissionError) as e:
            ok, said = False, f"not saved: {textsafe.oneline(str(e))}"
        self.notify(said, title=f"schedules {self.agent}", severity="information" if ok else "error",
                    timeout=8, markup=False)
        self.load()

    def _add(self) -> None:
        def answered(got: "tuple[str, str] | None") -> None:
            if got is not None:
                self._call("added", lambda: actions.schedule_add(self.agent, *got))
        self.app.push_screen(ScheduleAdd(), answered)

    def _edit(self, index: int) -> None:
        rows = actions.schedule_rows(self.agent)
        if not 1 <= index <= len(rows):
            return

        def answered(got: "tuple[str, str] | None") -> None:
            if got is not None and got != (rows[index - 1]["cron"], rows[index - 1]["message"]):
                self._call("edited", lambda: actions.schedule_edit(self.agent, index, *got))
        self.app.push_screen(ScheduleAdd((rows[index - 1]["cron"], rows[index - 1]["message"])), answered)

    def _remove(self, index: int) -> None:
        Confirm.ask(self.app, lambda: self._call("removed", lambda: actions.schedule_remove(self.agent, index)),
                    "Remove this schedule", "It stops being sent; the agent is not touched.",
                    yes="Remove", danger=True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "sc_add":
            event.stop()
            self._add()
        elif bid.startswith("sc_ed_") and bid[6:].isdigit():
            event.stop()
            self._edit(int(bid[6:]))
        elif bid.startswith("sc_rm_") and bid[6:].isdigit():
            event.stop()
            self._remove(int(bid[6:]))
