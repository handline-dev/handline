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
from textual.containers import Vertical
from textual.widgets import DataTable, Static

from ... import actions, db
from ...infra import textsafe
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


class TasksView(Vertical):
    """`mine` limits it to what concerns the founder; `f` steps through the owners."""

    BINDINGS = [
        Binding("space", "check", "mark"),
        Binding("c", "act('close')", "close"),
        Binding("d", "act('discard')", "discard"),
        Binding("o", "act('owner')", "owner"),
        Binding("p", "act('priority')", "priority"),
        Binding("m", "act('merge')", "merge"),
        Binding("s", "sort", "sort"),
        Binding("a", "closed", "closed"),
        Binding("f", "owner_filter", "agent"),
    ]

    def __init__(self, mine: bool = False, **kw) -> None:
        super().__init__(**kw)
        self.mine = mine
        self.checked: set[int] = set()
        self.by = "priority"
        self.show_closed = False
        self.owner: str | None = None
        self.owners: list[str] = []
        self.rows: list[dict] = []
        self.flash = ""
        self.results = Text()

    def compose(self) -> ComposeResult:
        yield Static(id="tasks_title")
        yield DataTable(id="tasks_table", cursor_type="row", zebra_stripes=True)
        yield Static(id="tasks_results")

    def on_mount(self) -> None:
        self.query_one(DataTable).add_columns(
            "", "#", "P", "title", "owner", "state", "idle", "may close", "blocked by")
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
            rows = actions.task_rows(con, include_closed=self.show_closed)
        finally:
            con.close()
        if self.mine:
            rows = [r for r in rows if actions.task_related_to(r, actions.HUMAN)]
        self.owners = sorted({r["owner"] for r in rows})
        if self.owner not in self.owners:
            self.owner = None
        if self.owner:
            rows = [r for r in rows if r["owner"] == self.owner]
        self.rows = actions.sort_task_rows(rows, self.by)
        now = time.time()
        table = self.table
        row = table.cursor_row
        table.clear()
        for r in self.rows:
            stale = actions.task_stale(r, now)
            cells = ("[x]" if r["id"] in self.checked else "[ ]", r["ref"], r["priority_ref"],
                     textsafe.oneline(r["title"]), r["owner"], r["state"],
                     idle_text(r, now) + (" !" if stale else ""), r["closer"],
                     " ".join(actions.task_ref(b) for b in r["blocked_by"]) or "-")
            table.add_row(*(Text(c, style="yellow" if stale else "") for c in cells), key=str(r["id"]))
        if self.rows:
            table.move_cursor(row=min(row, len(self.rows) - 1))
        other = "priority" if self.by == "number" else "number"
        self.query_one("#tasks_title", Static).update(
            f"{'MY TASKS' if self.mine else 'TASKS'}  {'all' if self.show_closed else 'open'} "
            f"{len(self.rows)} · agent: {self.owner or 'all'} (f) · sort: {self.by} (s: {other}) · "
            f"a: {'hide' if self.show_closed else 'show'} closed · ↵ opens")
        shown = Text(self.flash + "\n" if self.flash else "", "yellow")
        shown.append_text(self.results)
        self.query_one("#tasks_results", Static).update(shown)

    def _targets(self) -> list[int]:
        """The marked tasks, or the one under the cursor."""
        if self.checked:
            return sorted(self.checked)
        return [self.rows[self.cur]["id"]] if self.cur < len(self.rows) else []

    def action_check(self) -> None:
        if self.cur < len(self.rows):
            self.checked.symmetric_difference_update({self.rows[self.cur]["id"]})
        self.refresh_rows()

    def action_sort(self) -> None:
        self.by = "number" if self.by == "priority" else "priority"
        self.refresh_rows()

    def action_closed(self) -> None:
        self.show_closed = not self.show_closed
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
