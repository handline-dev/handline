"""The tasks screen (T69): every open task with its priority, owner, state, idle time,
who may close it and what blocks it. What a row says and what an action does or
refuses is `actions`'s (`task_rows`, `batch_task`, `merge_tasks`); this only draws
it and asks for the one thing an action needs (a reason, a name, a priority)."""
from __future__ import annotations

import time

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from ... import actions, db
from ...infra import textsafe
from ..components.jobs_aware import JobsAware
from ..components.text_prompt import TextPrompt

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


class TasksScreen(JobsAware, Screen):
    BINDINGS = [
        Binding("space", "check", "mark"),
        Binding("c", "act('close')", "close"),
        Binding("d", "act('discard')", "discard"),
        Binding("o", "act('owner')", "owner"),
        Binding("p", "act('priority')", "priority"),
        Binding("m", "act('merge')", "merge"),
        Binding("s", "sort", "sort"),
        Binding("a", "closed", "closed"),
        Binding("escape", "app.pop_screen", "back"),
        Binding("q,ctrl+c", "app.quit", "quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.checked: set[int] = set()
        self.by = "priority"
        self.show_closed = False
        self.rows: list[dict] = []
        self.results = Text()

    def compose(self) -> ComposeResult:
        yield Static(id="title")
        yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
        yield Static(id="results")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("", "#", "P", "title", "owner", "state", "idle", "may close", "blocked by")
        table.focus()
        self._retime()
        self._redraw()

    @property
    def cur(self) -> int:
        return self.query_one(DataTable).cursor_row

    def _redraw(self) -> None:
        con = db.connect()
        try:
            self.rows = actions.sort_task_rows(
                actions.task_rows(con, include_closed=self.show_closed), self.by)
        finally:
            con.close()
        now = time.time()
        table = self.query_one(DataTable)
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
        self.query_one("#title", Static).update(
            f"TASKS  {'all' if self.show_closed else 'open'} {len(self.rows)} (a: "
            f"{'hide' if self.show_closed else 'show'} closed) · sort: "
            f"{self.by} (s: {'priority' if self.by == 'number' else 'number'})")
        shown = Text()
        for jl in self.jobs.lines():
            shown.append(jl.text + "\n")
        if self.flash:
            shown.append(self.flash + "\n", "yellow")
        shown.append_text(self.results)
        self.query_one("#results", Static).update(shown)

    def _targets(self) -> list[int]:
        """The marked tasks, or the one under the cursor."""
        if self.checked:
            return sorted(self.checked)
        return [self.rows[self.cur]["id"]] if self.cur < len(self.rows) else []

    def action_check(self) -> None:
        if self.cur < len(self.rows):
            self.checked.symmetric_difference_update({self.rows[self.cur]["id"]})
        self._redraw()

    def action_sort(self) -> None:
        self.by = "number" if self.by == "priority" else "priority"
        self._redraw()

    def action_closed(self) -> None:
        self.show_closed = not self.show_closed
        self._redraw()

    def action_act(self, verb: str) -> None:
        self.run_worker(self._act(verb), exclusive=True, group="act")

    async def _act(self, verb: str) -> None:
        self.flash = ""
        tids = self._targets()
        if not tids:
            self.flash = "no tasks"
            self._redraw()
            return
        arg = ""
        if verb in _ASKS:
            arg = await self.app.push_screen_wait(TextPrompt(_ASKS[verb])) or ""
            if not arg.strip():
                self.flash = "left alone"
                self._redraw()
                return
        con = db.connect()
        try:
            if verb == "merge":
                keep = self.rows[self.cur]["id"]
                others = [t for t in tids if t != keep]
                ok, msg = actions.merge_tasks(con, keep, others, actions.HUMAN)
                results = [(keep, ok, msg)]
            else:
                results = actions.batch_task(con, verb, tids, actions.HUMAN, arg)
        finally:
            con.close()
        self.results = result_lines(results)
        self.checked -= {t for t, ok, _ in results if ok}
        if verb == "merge" and results[0][1]:
            self.checked.clear()
        self._redraw()
