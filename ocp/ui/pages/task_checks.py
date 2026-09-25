"""The founder's own 'done - waiting for check' list."""
from __future__ import annotations

import time

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Static

from ... import actions, db
from ...usage import status
from ...infra import textsafe
from ..components.text_prompt import TextPrompt
from ..components.jobs_aware import JobsAware


class TaskChecks(JobsAware, Screen):
    """Task #72 (founder #2154/#2155): the founder's own 'done - waiting for
    check' list (#71) - `db.tasks_needing_close` filtered to the ones
    `actions.task_asker` says are his, the same set `cmd_task_board` prints
    under that heading for `me == HUMAN` (the bridge always acts as the
    founder - the same `actions.HUMAN` the MESSAGES panel already answers,
    dismisses and delivers as). `c` closes the row under the cursor, `r`
    reopens it with a typed one-line reason (#71's own rule for any undo),
    and space/`C` mark and then close several at once, for a day with more
    of these than are worth doing one at a time."""

    BINDINGS = [
        Binding("c", "act('c')", "close"),
        Binding("r", "act('r')", "reopen"),
        Binding("space", "check", "check"),
        Binding("C", "act('C')", "close checked"),
        Binding("escape", "app.pop_screen", "back"),
        Binding("q,ctrl+c", "app.quit", "quit", show=False),
    ]
    _STATUS_STYLE = {"normal": "dim", "slow": "yellow", "danger": "red"}

    def __init__(self) -> None:
        super().__init__()
        self.checked: set[int] = set()
        self.flash = ""
        self.rows: list = []

    def compose(self) -> ComposeResult:
        yield Static("done - waiting for check", id="title")
        yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
        yield Static(id="status")
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.add_columns("", "#", "task", "owner", "done")
        table.focus()
        self._retime()
        self._redraw()

    @property
    def cur(self) -> int:
        return self.query_one(DataTable).cursor_row

    def _rows(self, con) -> list:
        return [t for t in db.tasks_needing_close(con)
                if actions.task_asker(con, t) == actions.HUMAN]

    def _redraw(self) -> None:
        con = db.connect()
        try:
            self.rows = self._rows(con)
        finally:
            con.close()
        table = self.query_one(DataTable)
        row = table.cursor_row
        table.clear()
        for t in self.rows:
            parent = f" (under {actions.task_ref(t['parent_task'])})" if t["parent_task"] else ""
            cells = ("[x]" if t["id"] in self.checked else "[ ]", actions.task_ref(t['id']),
                     textsafe.oneline(t["title"]) + parent, t["owner"],
                     textsafe.oneline(t["done_evidence"]))
            table.add_row(*(Text(c) for c in cells), key=str(t["id"]))
        if self.rows:
            table.move_cursor(row=min(row, len(self.rows) - 1))
        status = Text(no_wrap=True, overflow="ellipsis")
        for jl in self.jobs.lines():
            status.append(jl.text + "\n", self._STATUS_STYLE.get(jl.level, ""))
        if self.flash:
            status.append(self.flash, "yellow")
        self.query_one("#status", Static).update(status)

    def action_check(self) -> None:
        if self.cur < len(self.rows):
            self.checked.symmetric_difference_update({self.rows[self.cur]["id"]})
        self._redraw()

    def action_act(self, key: str) -> None:
        self.run_worker(self._act(key), exclusive=True, group="act")

    async def _act(self, key: str) -> None:
        if key == "C":
            await self._close_checked()
        else:
            tid = self.rows[self.cur]["id"] if self.cur < len(self.rows) else None
            if tid is None:
                self.flash = "nothing waiting"
            elif key == "c":
                await self._close(tid)
            else:
                await self._reopen(tid)
        self._redraw()

    async def _close(self, tid: int) -> None:
        con = db.connect()
        try:
            ok, msg = actions.close_task(con, tid, actions.HUMAN)
        finally:
            con.close()
        self.flash = msg
        self.checked.discard(tid)

    async def _reopen(self, tid: int) -> None:
        reason = await self.app.push_screen_wait(TextPrompt(f"reopen {actions.task_ref(tid)} - why:"))
        if reason is None or not reason.strip():
            self.flash = "left alone"
            return
        con = db.connect()
        try:
            ok, msg = actions.reopen_task(con, tid, actions.HUMAN, reason)
        finally:
            con.close()
        self.flash = msg
        self.checked.discard(tid)

    async def _close_checked(self) -> None:
        if not self.checked:
            self.flash = "nothing checked - space marks a row"
            return
        ids = sorted(self.checked)
        said = []
        con = db.connect()
        try:
            for tid in ids:
                ok, msg = actions.close_task(con, tid, actions.HUMAN)
                said.append(msg)
        finally:
            con.close()
        self.flash = "  ·  ".join(said)
        self.checked.clear()
