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
from .action_bar import ActionBar
from .confirm import Confirm
from .key_guide import Key, KeyGuide, KeyHelp
from .text_prompt import TextPrompt

_ASKS = {"discard": "discard - why:", "owner": "new owner (agent name):",
         "priority": "priority (1 high, 2, 3 low):"}


_DONE = {"close": "closed", "discard": "discarded", "owner": "owner changed", "priority": "priority set"}


def result_notes(results: list[tuple[int, bool, str]], verb: str) -> list[tuple[str, str]]:
    """The toasts for a batch, as (text, severity): one for every task that worked, one for
    the refusals with a line each and its reason. A merge says what `merge_tasks` said."""
    if verb == "merge":
        _, ok, msg = results[0]
        return [(textsafe.oneline(msg), "information" if ok else "error")]
    out = []
    done = [actions.task_ref(t) for t, ok, _ in results if ok]
    if done:
        out.append((f"{_DONE.get(verb, verb)} {len(done)}: {' '.join(done)}", "information"))
    refused = [f"{actions.task_ref(t)}: {textsafe.oneline(msg)}" for t, ok, msg in results if not ok]
    if refused:
        out.append(("\n".join(refused), "error"))
    return out


def idle_text(row: dict, now: float) -> str:
    s = actions.task_idle_s(row, now)
    return f"{s // 86400}d" if s >= 86400 else f"{s // 3600}h" if s >= 3600 else "<1h"


class _StateList(SelectionList):
    """Enter applies the picker instead of toggling the row; space toggles."""

    BINDINGS = [Binding("enter", "screen.apply", "apply", show=False)]


class StatePicker(ModalScreen["set[str] | None"]):
    """The states to show, as checkboxes: space marks, enter applies, esc leaves it."""

    BINDINGS = [Binding("escape", "dismiss(None)", "cancel")]
    DEFAULT_CSS = """
    StatePicker { align: center middle; background: $background 60%; }
    StatePicker #dialog { width: 36; height: auto; padding: 1 2; border: thick $primary; background: $panel; }
    StatePicker #hint { margin-top: 1; color: $text-muted; }
    """

    def __init__(self, chosen: set[str]) -> None:
        super().__init__()
        self.chosen = chosen

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Static(Text("show tasks in state", style="bold"))
            yield _StateList(*((s, s, s in self.chosen) for s in actions.TASK_STATES), id="states")
            yield Static("space marks · enter applies · esc cancels", id="hint")

    def action_apply(self) -> None:
        self.dismiss(set(self.query_one(SelectionList).selected))


class TasksView(Vertical):
    """`mine` limits it to what concerns the founder, `closer` to the tasks that one signs off
    (`b`: the founder's done tasks); `f` steps through the owners. `agent` limits it to the
    tasks that agent owns or opened (its chat page), and then the agent filter is not offered."""

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

    def __init__(self, mine: bool = False, back: bool = False, states: set[str] | None = None,
                 closer: str | None = None, agent: str | None = None, **kw) -> None:
        super().__init__(**kw)
        self.agent = agent
        self.mine = mine
        self.closer = closer
        self.back = back
        self.checked: set[int] = set()
        self.by = "priority"
        self.rev = False
        self.states: set[str] = set(states or actions.OPEN_STATES)
        self.owner: str | None = None
        self.owners: list[str] = []
        self.rows: list[dict] = []
        self._mark_head = ""
        self._agent_owners: list[str] | None = None

    def compose(self) -> ComposeResult:
        with Horizontal(id="tasks_filters"):
            if not self.agent:
                yield Select([("agent: all", "")], id="f_agent", allow_blank=False, compact=True)
            yield Select([(f"sort: {c}", c) for c in actions.TASK_SORTS], id="f_sort",
                         allow_blank=False, compact=True)
            yield Button(self._state_label(), id="f_state", compact=True)
        with Horizontal(id="tasks_body"):
            yield TaskTable(id="tasks_table", cursor_type="row", zebra_stripes=True)
            yield TaskPane(id="tasks_pane")
        with ActionBar(id="tasks_actions"):
            for verb in ("close", "discard", "owner", "priority", "merge"):
                yield Button(verb.capitalize(), id=f"act_{verb}", compact=True)
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
        if self.closer:
            rows = [r for r in rows if r["closer"] == self.closer]
        if self.agent:
            rows = [r for r in rows if self.agent in (r["owner"], r["opened_by"])]
        self.owners = sorted({r["owner"] for r in rows})
        if self.owner not in self.owners:
            self.owner = None
        if self.owner:
            rows = [r for r in rows if r["owner"] == self.owner]
        self.rows = actions.sort_task_rows(rows, self.by, self.rev)
        self.checked &= {r["id"] for r in self.rows}
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

    def _state_label(self) -> str:
        if self.states == set(actions.TASK_STATES):
            return "all states"
        if self.states == set(actions.OPEN_STATES):
            return "open"
        return ",".join(st for st in actions.TASK_STATES if st in self.states) or "no state"

    def _sync_filters(self) -> None:
        """Show the state in the dropdowns, touching a dropdown only when it differs, so an
        open one is not rebuilt under the user and no Changed is posted for our own state."""
        sels = [(self.query_one("#f_sort", Select), self.by)]
        if not self.agent:
            agent = self.query_one("#f_agent", Select)
            if self.owners != self._agent_owners:
                self._agent_owners = list(self.owners)
                agent.set_options([("agent: all", ""), *((f"agent: {o}", o) for o in self.owners)])
            sels.append((agent, self.owner or ""))
        for sel, want in sels:
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
        bid = event.button.id or ""
        if bid == "f_state":
            self.action_states()
        elif bid.startswith("act_"):
            self.action_act(bid[4:])

    def action_states(self) -> None:
        self.app.push_screen(StatePicker(set(self.states)), self._set_states)

    def _set_states(self, chosen: "set[str] | None") -> None:
        if chosen is not None:
            self.states = chosen
            self.refresh_rows()
        self.table.focus()

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
        keys = [k for k in self.KEYS if not (self.agent and k.key == "f")]
        return [*keys, *([Key("esc", "back", 1, "back")] if self.back else [])]

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

    def show_checks(self, on: bool) -> None:
        """The founder's done tasks waiting for a sign-off (`on`), or the view as it opens."""
        self.closer = actions.HUMAN if on else None
        self.states = {"done"} if on else set(actions.OPEN_STATES)
        self.owner = None
        self.refresh_rows()

    def sort_named(self, column: str) -> None:
        """Sort by `column`, or reverse the order when it already is."""
        if column == self.by:
            self.rev = not self.rev
        else:
            self._sort_by(column)
        self._set_columns()
        self.refresh_rows()

    def action_owner_filter(self) -> None:
        if self.agent:
            return
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

    def _confirm(self, verb: str, tids: list[int], arg: str) -> Confirm:
        by_id = {r["id"]: r for r in self.rows}
        if verb == "merge":
            tids = [t for t in tids if t != self.rows[self.cur]["id"]]
        lines = [f"{actions.task_ref(t)}  {textsafe.oneline(by_id[t]['title'])}" if t in by_id
                 else actions.task_ref(t) for t in tids[:8]]
        if len(tids) > 8:
            lines.append(f"... and {len(tids) - 8} more")
        what = {"close": "Close", "discard": "Discard", "owner": "Change the owner of",
                "priority": "Set the priority of", "merge": "Merge"}[verb]
        into = f" into {actions.task_ref(self.rows[self.cur]['id'])}" if verb == "merge" else ""
        with_arg = f"\n{textsafe.oneline(arg)}" if arg else ""
        return Confirm(f"{what} {len(tids)} task{'s' if len(tids) != 1 else ''}{into}?",
                       "\n".join(lines) + with_arg, yes=what.split()[0], danger=verb in ("close", "discard", "merge"))

    async def _act(self, verb: str) -> None:
        tids = self._targets()
        if not tids:
            self.notify("no tasks", severity="warning")
            return
        arg = ""
        if verb in _ASKS:
            arg = await self.app.push_screen_wait(TextPrompt(_ASKS[verb])) or ""
            if not arg.strip():
                self.notify("left alone")
                return

        def merge_row_gone() -> bool:
            # a background refresh can empty or reorder self.rows while the modals above
            # are open, so the row under the cursor is checked again right before use.
            return verb == "merge" and self.cur >= len(self.rows)

        if merge_row_gone():
            self.notify("no row under the cursor to merge into", severity="warning")
            return
        if not await self.app.push_screen_wait(self._confirm(verb, tids, arg)):
            self.notify("left alone")
            return
        if merge_row_gone():
            self.notify("no row under the cursor to merge into", severity="warning")
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
        for text, severity in result_notes(results, verb):
            self.notify(text, title=verb, severity=severity, timeout=5 if severity == "information" else 12,
                        markup=False)
        self.checked -= {t for t, ok, _ in results if ok}
        if verb == "merge" and results[0][1]:
            self.checked.clear()
        self.refresh_rows()
