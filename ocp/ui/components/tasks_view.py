"""The tasks table (T69) as a widget, so the tasks screen and a home tab share it: every
open task with its priority, owner, state, idle time, who may close it and what blocks
it. What a row says and what an action does or refuses is `actions`'s (`task_rows`,
`batch_task`, `merge_tasks`); this draws it and asks for the one thing an action needs
(a reason, a name, a priority)."""
from __future__ import annotations

import time

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Select, SelectionList, Static

from ... import actions, db
from ...infra import textsafe
from ..chat_page import TaskPane, TaskTable
from .key_guide import Key, KeyGuide, KeyHelp
from .text_prompt import TextPrompt

_ASKS = {"discard": "discard - why:", "owner": "new owner (agent name):",
         "priority": "priority (1 high, 2, 3 low):"}


def result_lines(results: list[tuple[int, bool, str]]) -> Text:
    """One line per task: what was done, or the refusal and its reason."""
    out = Text()
    for i, (tid, ok, msg) in enumerate(results):
        out.append("\n" if i else "")
        out.append(f"{actions.task_ref(tid)} {'ok' if ok else 'refused'}: ", "green" if ok else "red")
        out.append(textsafe.oneline(msg))
    return out


def idle_text(row: dict, now: float) -> str:
    s = actions.task_idle_s(row, now)
    return f"{s // 86400}d" if s >= 86400 else f"{s // 3600}h" if s >= 3600 else "<1h"


class StatePicker(ModalScreen["set[str] | None"]):
    """The states to show, as checkboxes. Each change is applied at once (`on_change`); esc closes."""

    BINDINGS = [Binding("escape", "dismiss(None)", "close")]
    DEFAULT_CSS = """
    StatePicker { align: center middle; background: $background 60%; }
    StatePicker #dialog { width: 36; height: auto; padding: 1 2; border: thick $primary; background: $panel; }
    StatePicker #hint { margin-top: 1; color: $text-muted; }
    """

    def __init__(self, chosen: set[str], on_change) -> None:
        super().__init__()
        self.chosen = chosen
        self.on_change = on_change

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text("show tasks in state", style="bold"))
            yield SelectionList(*((s, s, s in self.chosen) for s in actions.TASK_STATES), id="states")
            yield Static("space or enter toggles, applied at once · esc closes", id="hint")

    def on_selection_list_selected_changed(self) -> None:
        self.on_change(set(self.query_one(SelectionList).selected))


class TasksView(Vertical):
    """`mine` limits it to what concerns the founder; `f` steps through the owners."""

    BINDINGS = [
        Binding("space", "check", "mark"),
        Binding("A", "check_all", "all"),
        Binding("c", "act('close')", "close"),
        Binding("d", "act('discard')", "discard"),
        Binding("o", "act('owner')", "owner"),
        Binding("p", "act('priority')", "priority"),
        Binding("m", "act('merge')", "merge"),
        Binding("a", "closed", "closed"),
        Binding("v", "states", "states"),
        Binding("f", "owner_filter", "agent"),
        Binding("question_mark", "key_help", "keys"),
    ]
    DEFAULT_CSS = """
    TasksView { height: 1fr; }
    TasksView #tasks_body { height: 1fr; }
    TasksView #tasks_table { width: 1fr; height: 1fr; }
    TasksView #tasks_results { height: auto; max-height: 6; }
    TasksView #tasks_filters { height: 1; }
    TasksView #tasks_filters Select { width: 1fr; }
    TasksView #tasks_filters Button { width: 1fr; }
    """
    KEYS = (
        Key("space", "mark", 1, "mark or unmark the row; c d o p act on the marked rows"),
        Key("A", "all", 3, "mark every row shown, or unmark them; the [ ] at the head of the column does the same"),
        Key("c", "close", 1, "close the task (a done one, by whoever may close it)"),
        Key("↵", "open", 1, "open the task's detail"),
        Key("d", "discard", 2, "discard a task nothing is running on, with a reason"),
        Key("o", "owner", 2, "change the owner to another agent"),
        Key("p", "priority", 2, "set the priority, 1 high to 3 low"),
        Key("m", "merge", 2, "fold the marked tasks into the row under the cursor"),
        Key("s", "sort", 3, "sort by the next column; S reverses; a click on a column head sorts by it"),
        Key("a", "closed", 3, "show or hide closed, discarded and merged tasks"),
        Key("v", "states", 3, "pick which states to show, with checkboxes"),
        Key("f", "agent", 3, "show one agent's tasks, and the next agent on each press; or use the agent dropdown"),
    )

    HEADS = (("", "mark"), ("#", "number"), ("P", "priority"), ("state", "state"), ("title", "title"),
             ("owner", "owner"), ("idle", "idle"), ("may close", "closer"), ("blocked by", "blocked"))

    def __init__(self, mine: bool = False, back: bool = False, **kw) -> None:
        super().__init__(**kw)
        self.mine = mine
        self.back = back
        self.checked: set[int] = set()
        self.by = "priority"
        self.rev = False
        self.states: set[str] = set(actions.OPEN_STATES)
        self.owner: str | None = None
        self.owners: list[str] = []
        self.rows: list[dict] = []
        self._mark_head = ""
        self.flash = ""
        self.results = Text()
        self._agent_owners: list[str] | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="tasks_filters"):
            yield Select([("agent: all", "")], id="f_agent", allow_blank=False, compact=True)
            yield Select([(f"sort: {c}", c) for c in actions.TASK_SORTS], id="f_sort",
                         allow_blank=False, compact=True)
            yield Button(self._state_label(), id="f_state", compact=True)
        with Horizontal(id="tasks_body"):
            yield TaskTable(id="tasks_table", cursor_type="row", zebra_stripes=True)
            yield TaskPane(id="tasks_pane")
        yield Static(id="tasks_results")
        yield KeyGuide(self._keys(), id="tasks_foot")

    def on_resize(self) -> None:
        self._show_pane()

    def _show_pane(self) -> None:
        pane = self.query_one(TaskPane)
        if pane.fit(self.size.width, self.table):
            pane.show(self.rows[self.cur]["id"] if self.cur < len(self.rows) else None)

    def on_task_pane_changed(self, event: TaskPane.Changed) -> None:
        event.stop()
        self.refresh_rows()

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        event.stop()
        self._show_pane()

    def _set_columns(self) -> None:
        """The heads, with an arrow on the sort column; a rebuild is how a head changes."""
        table = self.table
        table.clear(columns=True)
        self._mark_head = self._mark_label()
        for label, key in self.HEADS:
            label = self._mark_head if key == "mark" else label
            arrow = (" ▼" if self.rev else " ▲") if key == self.by else ""
            table.add_column(label + arrow, key=key)

    def _mark_label(self) -> str:
        ids = {r["id"] for r in self.rows}
        return "[x]" if ids and ids <= self.checked else "[ ]"

    def on_mount(self) -> None:
        self._set_columns()
        self.set_interval(2.0, self.refresh_rows)
        self.refresh_rows()

    @property
    def table(self) -> DataTable:
        return self.query_one("#tasks_table", DataTable)

    @property
    def cur(self) -> int:
        return self.table.cursor_row

    def refresh_rows(self) -> None:
        if not self.display:
            return
        con = db.connect()
        try:
            rows = actions.task_rows(con, state=sorted(self.states))
        finally:
            con.close()
        if self.mine:
            rows = [r for r in rows if actions.task_related_to(r, actions.HUMAN)]
        self.owners = sorted({r["owner"] for r in rows})
        if self.owner not in self.owners:
            self.owner = None
        if self.owner:
            rows = [r for r in rows if r["owner"] == self.owner]
        self.rows = actions.sort_task_rows(rows, self.by, self.rev)
        keep = self.table.cursor_row
        if self._mark_label() != self._mark_head:
            self._set_columns()
        now = time.time()
        table = self.table
        row = keep
        table.clear()
        for r in self.rows:
            stale = actions.task_stale(r, now)
            cells = ("[x]" if r["id"] in self.checked else "[ ]", r["ref"], r["priority_ref"],
                     r["state"], textsafe.oneline(r["title"]), r["owner"],
                     idle_text(r, now) + (" !" if stale else ""), r["closer"],
                     " ".join(actions.task_ref(b) for b in r["blocked_by"]) or "-")
            table.add_row(*(Text(c, style="yellow" if stale else "") for c in cells), key=str(r["id"]))
        if self.rows:
            table.move_cursor(row=min(row, len(self.rows) - 1))
        self._show_pane()
        self._sync_filters()
        self.query_one("#tasks_foot", KeyGuide).set_keys(self._keys())
        shown = Text(self.flash + "\n" if self.flash else "", "yellow")
        shown.append_text(self.results)
        self.query_one("#tasks_results", Static).update(shown)

    def _state_label(self) -> str:
        if self.states == set(actions.TASK_STATES):
            return "all states"
        if self.states == set(actions.OPEN_STATES):
            return "open"
        return ",".join(st for st in actions.TASK_STATES if st in self.states) or "no state"

    def _sync_filters(self) -> None:
        """Show the state in the dropdowns, touching a dropdown only when it differs, so an
        open one is not rebuilt under the user and no Changed is posted for our own state."""
        agent = self.query_one("#f_agent", Select)
        if self.owners != self._agent_owners:
            self._agent_owners = list(self.owners)
            agent.set_options([("agent: all", ""), *((f"agent: {o}", o) for o in self.owners)])
        for sel, want in ((agent, self.owner or ""), (self.query_one("#f_sort", Select), self.by)):
            if sel.value != want:
                sel.value = want
        self.query_one("#f_state", Button).label = f"state: {self._state_label()}"

    def on_select_changed(self, event: Select.Changed) -> None:
        event.stop()
        v = event.value
        if event.select.id == "f_agent":
            if (v or None) == self.owner:
                return
            self.owner = v or None
        else:
            if v == self.by:
                return
            self._sort_by(str(v))
            self._set_columns()
        self.table.focus()
        self.refresh_rows()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        if event.button.id == "f_state":
            self.action_states()

    def action_states(self) -> None:
        self.app.push_screen(StatePicker(set(self.states), self._set_states), lambda _: self.table.focus())

    def _set_states(self, chosen: set[str]) -> None:
        self.states = chosen
        self.refresh_rows()

    def _targets(self) -> list[int]:
        """The marked tasks, or the one under the cursor."""
        if self.checked:
            return sorted(self.checked)
        return [self.rows[self.cur]["id"]] if self.cur < len(self.rows) else []

    def action_check(self) -> None:
        if self.cur < len(self.rows):
            self.checked.symmetric_difference_update({self.rows[self.cur]["id"]})
        self.refresh_rows()

    def action_check_all(self) -> None:
        """The head of the mark column: mark every row shown, or unmark them when all are."""
        ids = {r["id"] for r in self.rows}
        self.checked = self.checked - ids if ids <= self.checked else self.checked | ids
        self.refresh_rows()

    def _keys(self) -> list[Key]:
        return [*self.KEYS, *([Key("esc", "back", 1, "back")] if self.back else [])]

    def action_key_help(self) -> None:
        self.app.push_screen(KeyHelp("MY TASKS keys" if self.mine else "TASKS keys", self._keys()))

    def on_task_table_sort(self, event: TaskTable.Sort) -> None:
        event.stop()
        names = list(actions.TASK_SORTS)
        if event.column == "mark":
            self.action_check_all()
            return
        if event.column == "reverse":
            self.rev = not self.rev
        elif event.column == "next":
            self._sort_by(names[(names.index(self.by) + 1) % len(names)])
        elif event.column == self.by:
            self.rev = not self.rev
        elif event.column in names:
            self._sort_by(event.column)
        self._set_columns()
        self.refresh_rows()

    def _sort_by(self, column: str) -> None:
        self.by, self.rev = column, column == "idle"

    def action_closed(self) -> None:
        ended = set(actions.TASK_STATES) - set(actions.OPEN_STATES)
        self.states = self.states - ended if self.states & ended else self.states | ended
        self.refresh_rows()

    def action_owner_filter(self) -> None:
        cycle = [None, *self.owners]
        self.owner = cycle[(cycle.index(self.owner) + 1) % len(cycle)] if self.owner in cycle else None
        self.refresh_rows()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        from ..chat_page import TaskDetail
        if event.row_key.value is not None:
            self.app.push_screen(TaskDetail(int(event.row_key.value)))

    def action_act(self, verb: str) -> None:
        self.run_worker(self._act(verb), exclusive=True, group="tasks_act")

    async def _act(self, verb: str) -> None:
        self.flash = ""
        tids = self._targets()
        if not tids:
            self.flash = "no tasks"
            self.refresh_rows()
            return
        arg = ""
        if verb in _ASKS:
            arg = await self.app.push_screen_wait(TextPrompt(_ASKS[verb])) or ""
            if not arg.strip():
                self.flash = "left alone"
                self.refresh_rows()
                return
        con = db.connect()
        try:
            if verb == "merge":
                keep = self.rows[self.cur]["id"]
                ok, msg = actions.merge_tasks(con, keep, [t for t in tids if t != keep], actions.HUMAN)
                results = [(keep, ok, msg)]
            else:
                results = actions.batch_task(con, verb, tids, actions.HUMAN, arg)
        finally:
            con.close()
        self.results = result_lines(results)
        self.checked -= {t for t, ok, _ in results if ok}
        if verb == "merge" and results[0][1]:
            self.checked.clear()
        self.refresh_rows()
