"""Roster proposals (`ocp hr`) waiting on the founder, and the view that lets them
be approved or rejected: the same answer the MESSAGES panel gives, in the
verdict words `hr.decision` reads."""
from __future__ import annotations

import time

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Static

from ... import actions, db, hr, textsafe
from ...bridge_textual import Confirm, TextPrompt
from .action_bar import ActionBar

# Proposal fields worth showing after the kind and agent, in this order.
_FIELDS = ("account", "vendor", "model", "effort", "team", "repo", "to")


def awaiting(con) -> list[dict]:
    """The proposals whose escalation is still queued for the founder, oldest
    first: each proposal's own fields plus `rid` and `asked` (the request's id and
    time). One already answered stays out even before the proposer applies it."""
    out = []
    for prop in hr.listing():
        if prop.get("state") != "proposed":
            continue
        req = db.get_request(con, int(prop.get("request") or 0))
        if req is None or req["state"] != "queued":
            continue
        out.append({**prop, "rid": int(req["id"]), "asked": int(req["ts"])})
    return sorted(out, key=lambda p: p["asked"])


def age(ts: int, now: float | None = None) -> str:
    secs = max(0, int((time.time() if now is None else now) - ts))
    return f"{secs // 86400}d" if secs >= 86400 else f"{secs // 3600}h" if secs >= 3600 \
        else f"{secs // 60}m"


def describe(p: dict) -> Text:
    """One proposal for the detail pane; every value came from an agent, so each is
    made one safe line and the reason a safe block."""
    out = Text(f"{p['kind']} {textsafe.oneline(str(p['agent']))}", style="bold")
    out.append(f"   #{p['rid']} from {textsafe.oneline(str(p.get('requested_by') or '?'))}, "
               f"{age(p['asked'])} ago\n", style="dim")
    for key in _FIELDS:
        if p.get(key):
            out.append(f"{key:<8}", style="dim")
            out.append(textsafe.oneline(str(p[key])) + "\n")
    out.append("why\n", style="dim")
    out.append("\n".join(textsafe.drawable(str(p.get("why") or ""))))
    return out


class ProposalsView(Vertical):
    """A table of the proposals awaiting a decision, the selected one in full, and
    Approve / Reject. `refresh_rows` redraws it and is called on the page's tick."""

    BINDINGS = [Binding("a", "approve", "approve"), Binding("r", "reject", "reject")]
    DEFAULT_CSS = """
    ProposalsView { height: 1fr; }
    ProposalsView #hr_table { height: auto; max-height: 40%; }
    ProposalsView #hr_detail { height: 1fr; padding: 1 3; }
    ProposalsView #hr_flash { height: 1; padding: 0 3; color: $text-muted; }
    """

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.rows: list[dict] = []

    def compose(self) -> ComposeResult:
        yield DataTable(id="hr_table", cursor_type="row", zebra_stripes=True)
        with VerticalScroll(id="hr_detail"):
            yield Static(id="hr_text")
        yield Static(id="hr_flash")
        with ActionBar():
            yield Button("Approve  a", id="hr_approve", variant="success", compact=True)
            yield Button("Reject  r", id="hr_reject", variant="error", compact=True)

    def on_mount(self) -> None:
        self.query_one("#hr_table", DataTable).add_columns("#", "kind", "agent", "from", "asked")
        self.refresh_rows()

    @property
    def current(self) -> dict | None:
        row = self.query_one("#hr_table", DataTable).cursor_row
        return self.rows[row] if 0 <= row < len(self.rows) else None

    def refresh_rows(self) -> None:
        table = self.query_one("#hr_table", DataTable)
        keep = self.current["rid"] if self.current else None
        con = db.connect()
        try:
            self.rows = awaiting(con)
        finally:
            con.close()
        table.clear()
        for p in self.rows:
            table.add_row(Text(f"#{p['rid']}", style="cyan"), Text(p["kind"]),
                          Text(textsafe.oneline(str(p["agent"]))),
                          Text(textsafe.oneline(str(p.get("requested_by") or "?")), style="dim"),
                          Text(age(p["asked"]), style="dim"), key=str(p["rid"]))
        if self.rows:
            table.move_cursor(row=next((i for i, p in enumerate(self.rows) if p["rid"] == keep), 0))
        self._show()

    def _show(self) -> None:
        p = self.current
        self.query_one("#hr_text", Static).update(
            describe(p) if p else Text("no proposal is waiting for your decision", style="dim"))
        for b in ("#hr_approve", "#hr_reject"):
            self.query_one(b, Button).disabled = p is None

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        event.stop()
        self._show()

    def _say(self, ok: bool, said: str) -> None:
        self.query_one("#hr_flash", Static).update(Text(textsafe.oneline(said)))
        self.app.notify(textsafe.oneline(said), severity="information" if ok else "error",
                        timeout=10, markup=False)
        self.refresh_rows()

    def _answer(self, rid: int, verdict: str) -> None:
        con = db.connect()
        try:
            ok, said = actions.answer_ok(con, rid, verdict)
        finally:
            con.close()
        self._say(ok, said)

    def action_approve(self) -> None:
        p = self.current
        if p is None:
            return
        rid = p["rid"]
        self.app.push_screen(
            Confirm(f"Approve {p['kind']} {textsafe.oneline(str(p['agent']))}",
                    "Your decision, and it is what the proposing agent acts on.", yes="Approve"),
            lambda yes: yes and self._answer(rid, "approve"))

    def action_reject(self) -> None:
        p = self.current
        if p is None:
            return
        rid = p["rid"]
        self.app.push_screen(
            TextPrompt(f"reject {p['kind']} {textsafe.oneline(str(p['agent']))} - why:"),
            lambda why: why and why.strip() and self._answer(rid, f"reject {why.strip()}"))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        (self.action_approve if event.button.id == "hr_approve" else self.action_reject)()
