"""Roster proposals (`hl hr`) waiting on the founder, and the view that lets them
be approved or rejected: the same `hr.decide` the chat card and `hl hr approve` call."""
from __future__ import annotations


from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Button, DataTable, Static

from ... import actions
from ...infra import paths
from ...team import agents, hr
from ...infra import textsafe, timefmt
from .confirm import Confirm
from .action_bar import ActionBar
from .table_sync import sync_rows
from .agent_settings_screen import AgentSettingsScreen
from .section_head import SectionHead
from . import hr_card

# Proposal fields worth showing after the kind and agent, in this order.
_FIELDS = ("vendor", "model", "effort", "team", "repo", "to")


DECIDE, APPROVED = "decide", "approved, not applied"


def open_proposals(con=None) -> list[dict]:
    """The proposals that are the founder's move, oldest first: each proposal's own fields plus
    `asked` (when it was raised) and `status` - DECIDE while it waits on the founder, APPROVED
    when it was approved but the change did not go through (approving again retries it)."""
    out = [{**prop, "asked": int(prop.get("created") or 0),
            "status": DECIDE if prop["state"] == "proposed" else APPROVED}
           for prop in hr.listing() if prop.get("state") in ("proposed", "approved")]
    return sorted(out, key=lambda p: (p["asked"], p["id"]))


def describe(p: dict) -> Text:
    """One proposal for the detail pane; every value came from an agent, so each is
    made one safe line and the reason a safe block."""
    out = Text(f"{p['kind']} {textsafe.oneline(str(p['agent']))}", style="bold")
    out.append(f"   {p['id']} from {textsafe.oneline(str(p.get('requested_by') or '?'))}, "
               f"{timefmt.ago_short(p['asked'])} ago\n", style="dim")
    for key in _FIELDS:
        if p.get(key):
            out.append(f"{key:<8}", style="dim")
            out.append(textsafe.oneline(str(p[key])) + "\n")
    for key, value in hr._sets_of(p).items():
        out.append(f"{'set ' + key:<18}", style="dim")
        out.append(textsafe.oneline(value) + "\n")
    out.append("why\n", style="dim")
    out.append("\n".join(textsafe.drawable(str(p.get("why") or ""))))
    if p.get("error"):
        out.append("\n\napproved, but it did not go through\n", style="red")
        out.append("\n".join(textsafe.drawable(str(p["error"])))[:400])
    return out


class ProposalsView(Vertical):
    """A table of the proposals awaiting a decision, the selected one in full, and
    Approve / Reject. `refresh_rows` redraws it and is called on the page's tick."""

    BINDINGS = [Binding("a", "approve", "approve"), Binding("A", "approve_all", "approve all"),
                Binding("r", "reject", "reject"),
                Binding("w", "withdraw", "withdraw"), Binding("e", "edit", "edit settings")]
    DEFAULT_CSS = """
    ProposalsView { height: 1fr; }
    ProposalsView #hr_table { height: auto; max-height: 30%; }
    ProposalsView #hr_detail { height: 1fr; padding: 1 3; }
    ProposalsView #hr_agents { height: auto; max-height: 40%; }
    """

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.rows: list[dict] = []
        self.cards: dict[str, dict] = {}

    def compose(self) -> ComposeResult:
        yield DataTable(id="hr_table", cursor_type="row", zebra_stripes=True)
        yield VerticalScroll(id="hr_detail")
        yield SectionHead("agents", "Enter or e edits one's settings")
        yield DataTable(id="hr_agents", cursor_type="row", zebra_stripes=True)
        with ActionBar():
            yield Button("Approve all  A", id="hr_approve_all", compact=True)
            yield Button("Withdraw  w", id="hr_withdraw", compact=True)
            yield Button("Edit settings  e", id="hr_edit", compact=True)

    def on_mount(self) -> None:
        self.query_one("#hr_table", DataTable).add_columns("#", "kind", "agent", "from", "asked", "")
        self.query_one("#hr_agents", DataTable).add_columns("agent", "model", "effort", "browser", "mode", "sandbox")
        self.refresh_rows()

    @property
    def current(self) -> dict | None:
        row = self.query_one("#hr_table", DataTable).cursor_row
        return self.rows[row] if 0 <= row < len(self.rows) else None

    def refresh_rows(self) -> None:
        table = self.query_one("#hr_table", DataTable)
        keep = self.current["id"] if self.current else None
        self.rows = open_proposals()
        self.cards = {c["id"]: c for c in hr_card.pending()}
        rebuilt = sync_rows(table, [(p["id"], (
            Text(p["id"], style="cyan"), Text(p["kind"]),
            Text(textsafe.oneline(str(p["agent"]))),
            Text(textsafe.oneline(str(p.get("requested_by") or "?")), style="dim"),
            Text(timefmt.ago_short(p["asked"]), style="dim"),
            Text(p["status"], style="yellow" if p["status"] == DECIDE else "red"))) for p in self.rows])
        if rebuilt and self.rows:
            table.move_cursor(row=next((i for i, p in enumerate(self.rows) if p["id"] == keep), 0),
                              scroll=False)
        self._sync_agents()
        self._show()

    def _sync_agents(self) -> None:
        rows = []
        for name in sorted(paths.list_agents()):
            try:
                cfg = agents.read(name)
                vendor, model, effort = agents.parts(cfg)
                cells = (model, effort, agents.browser_mode(cfg),
                         agents.mode_of(name, cfg), "on" if agents.sandbox_on(name, cfg) else "off")
            except Exception:  # noqa: BLE001 - one unreadable file must not hide the rest
                cells = ("unreadable", "", "", "", "")
            rows.append((name, (Text(textsafe.oneline(name)), *(Text(c) for c in cells))))
        sync_rows(self.query_one("#hr_agents", DataTable), rows)

    def _show(self) -> None:
        p = self.current
        detail = self.query_one("#hr_detail", VerticalScroll)
        card = self.card
        entry = self.cards.get(p["id"]) if p else None
        if entry is None:
            detail.remove_children()
            detail.mount(Static(Text("no proposal is waiting for your decision", style="dim")))
        elif card is not None and card.p["id"] == p["id"]:
            if card.p != entry and not card.has_class("rejecting"):
                card.show(entry)
        else:
            detail.remove_children()
            detail.mount(hr_card.HrCard(entry))
        self.query_one("#hr_withdraw", Button).disabled = p is None
        self.query_one("#hr_approve_all", Button).disabled = not self.cards

    @property
    def card(self) -> hr_card.HrCard | None:
        found = self.query(hr_card.HrCard)
        return found.first() if found else None

    def on_hr_card_decided(self, event: hr_card.HrCard.Decided) -> None:
        event.stop()
        self._say(event.ok, event.said)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        event.stop()
        self._show()

    def _say(self, ok: bool, said: str) -> None:
        self.app.notify(textsafe.oneline(said), severity="information" if ok else "error",
                        timeout=10, markup=False)
        self.refresh_rows()

    def action_approve(self) -> None:
        if self.card is not None:
            self.card.approve()

    def action_approve_all(self) -> None:
        """Every proposal waiting on the founder, read as the cards show them now, in one decision: they
        are applied one after another with no agent in the way, and each proposer is told once."""
        entries = [(c["id"], c["subject"]) for c in hr.pending()]
        if not entries:
            self.app.notify("nothing is waiting for your decision", timeout=5, markup=False)
            return
        self.app.push_screen(
            Confirm(f"Approve all {len(entries)}",
                    "Each is applied as the card shows it; one that fails stays waiting, and every proposer "
                    "gets one message with the results.", yes="Approve all"),
            lambda yes: yes and self._approve_all(entries))

    def _approve_all(self, entries: list[tuple[str, str]]) -> None:
        ok, lines = hr.decide_many(entries, "approved", actions.HUMAN)
        self._say(ok, f"{len(entries)} approved" if ok else "; ".join(lines))

    def action_withdraw(self) -> None:
        p = self.current
        if p is None:
            return
        pid = p["id"]
        self.app.push_screen(
            Confirm(f"Withdraw {p['kind']} {textsafe.oneline(str(p['agent']))}",
                    "Takes the proposal back; whoever raised it can raise it again.",
                    yes="Withdraw", danger=True),
            lambda yes: yes and self._withdraw(pid))

    def _withdraw(self, pid: str) -> None:
        self._say(*hr.withdraw(pid, actions.HUMAN))

    def action_reject(self) -> None:
        if self.card is not None:
            self.card.reject()

    def action_edit(self) -> None:
        """The settings of the agent under the cursor: in the agents table, that one;
        in the proposals, the one the proposal is about."""
        table = self.query_one("#hr_agents", DataTable)
        if self.app.focused is table and 0 <= table.cursor_row < table.row_count:
            name = table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        else:
            name = self.current["agent"] if self.current else None
        if name not in paths.list_agents():
            self.app.notify("pick an agent in the agents table", timeout=5, markup=False)
            return
        self.app.push_screen(AgentSettingsScreen(name), lambda _: self.refresh_rows())

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "hr_agents":
            event.stop()
            self.action_edit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        {"hr_withdraw": self.action_withdraw, "hr_edit": self.action_edit,
         "hr_approve_all": self.action_approve_all}[event.button.id]()
