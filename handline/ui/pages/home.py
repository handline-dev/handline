"""The bridge's home page: Home, HR and Settings tabs under the page header. The Home tab
is three panels - ACCOUNTS, AGENTS, FOR YOU - with the cursor kept as `panel` and `sel`
and the row keys in `RowActions`; what only this page shows is in `ui.components`
(`gauge`, `agent_rows`, `summary`, `proposals`)."""
from __future__ import annotations

import time
from dataclasses import dataclass

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.errors import NoWidget
from textual.screen import Screen
from textual.worker import get_current_worker
from textual.widgets import Button, DataTable, Input, Static, Switch, Tabs
from textual.widgets._data_table import CellDoesNotExist

from ... import actions, db
from ...infra import textsafe
from ...session import idle_close
from ...usage import load as loadmod, status
from .. import tui
from ..components import agent_rows, gauge
from ..components.answer_add import AnswerAdd
from ..components.ime import ImeInput
from ..components.jobs_aware import JobsAware
from ..components.page_header import page_header
from ..components.proposals import ProposalsView, open_proposals
from ..components.row_actions import RowActions
from ..components.section_head import SectionHead
from ..components.field_note import FieldNote
from ..components.switch_row import SwitchRow
from ..components.table_sync import sync_rows
from ..components.key_guide import Key, KeyGuide, KeyHelp
from ..components.outlook_view import OutlookView
from ..components.tasks_view import TasksView
from ..components.summary import clock, daemon_running, marks_on_screen, summarize, summary_text
from ..keys import key_token
from .stats import StatsScreen

DOUBLE_PRESS_S = 0.5     # two presses on one row this close are a double press
_PANEL_OF_TABLE = {"accounts": tui.ACCOUNTS, "agents": tui.AGENTS, "messages": tui.MESSAGES}
_LEVEL_STYLE = {"normal": "dim", "slow": "yellow", "danger": "red"}


@dataclass
class Read:
    """Everything the Home tab draws that comes from the database, the disk or a subprocess,
    read on a worker thread: painting it is only widget work."""
    summary: object
    agent_keys: object
    daemon: bool
    accounts: tuple
    usage: dict
    agents: tuple
    messages: tuple
    proposals: int
    marks: bool


# Each tab's widgets: what it shows and the others hide.
_TAB_WIDGETS = {
    "home": ("#header", "#accounts-box", "#agents-box", "#messages-box", "#foot", "#home_keys"),
    "hr": ("#hr_view",),
    "tasks": ("#tasks_view",),
    "stats": ("#stats_view",),
    "settings": ("#settings_view",),
}
# The ACCOUNTS columns from the richest layout to the barest; the first that fits the
# width is drawn. The bars carry a `│` for how far through the window we are, so a
# narrow layout drops the reset countdowns, then the percentages.
_ACCT_TIERS = (
    (("five", "reset5", "agents", "seven", "reset7", "runway"), True),
    (("five", "agents", "seven", "runway"), True),
    (("five", "agents", "seven", "runway"), False),
)
# The keys every panel answers to, after the panel's own.
_GLOBAL_KEYS = (Key("←→", "panel", 3, "move between the panels"), Key("↑↓", "row", 3, "move between the rows"),
                Key("q", "quit", 2, "quit"),
                Key("T", "tasks", 2, "every task"), Key("S", "stats", 2, "the week left, recorded and guessed"))
_AGENT_HEADS = ("agent", "acct", "model", "state", "cache", "ctx", "in", "task", "note")
_MESSAGE_HEADS = ("", "", "from", "message")


def _set_columns(table: DataTable, heads: tuple) -> None:
    """Give a table these columns, and again only when the set changes."""
    if getattr(table, "_heads", None) != heads:
        table.clear(columns=True)
        table.add_columns(*heads)
        table._heads = heads


class HomePage(RowActions, JobsAware, Screen):
    AUTO_FOCUS = ""     # the keys act on the panels until the tab bar takes the focus
    # The row cursor is the selection. DataTable's hover highlight restarts at row 0
    # each redraw, so it reads as a second, wrong selection.
    DEFAULT_CSS = """
    HomePage #header { height: auto; }
    HomePage #accounts-box { height: auto; max-height: 40%; border: round $panel; }
    HomePage #agents-box { height: auto; max-height: 50%; border: round $panel; }
    HomePage #messages-box { height: 1fr; border: round $panel; }
    HomePage #accounts-box.focused, HomePage #agents-box.focused,
    HomePage #messages-box.focused { border: round $accent; }
    HomePage #accounts, HomePage #agents { height: auto; }
    HomePage #acct-notes, HomePage #agent-notes, HomePage #message-notes { height: auto; padding: 0 1; }
    HomePage #foot { height: auto; }
    HomePage DataTable > .datatable--hover { background: transparent; }
    HomePage DataTable { overflow-x: hidden; }
    HomePage #settings_view { height: 1fr; padding: 1 3; }
    HomePage #answers { height: auto; max-height: 12; margin-top: 1; }
    HomePage #settings_pane { height: 1fr; }
    HomePage #answer_buttons { height: 1; margin-top: 1; }
    HomePage #answer_buttons Button { width: auto; min-width: 0; margin-right: 2; }
    HomePage .frow { height: 1; margin-bottom: 1; }
    HomePage .flabel { width: 21; color: $text-muted; }
    HomePage .fhint { color: $text-muted; }
    HomePage #default_cap, HomePage #idle_close { width: 8; }
    """
    # What is stuck is said on the agent's row, on the account's mark and in the header's
    # daemon word, so there is no STUCK panel.
    PANELS = (tui.ACCOUNTS, tui.AGENTS, tui.MESSAGES)
    BLINK_S = 0.6

    def __init__(self) -> None:
        super().__init__()
        self.panel = tui.AGENTS
        self.sel: dict = {}
        tui._enter(self.panel, self.sel)
        self.tab = "home"
        self._answers: list[dict] = []
        self._checks_shown = False      # the Tasks tab is showing a header link's view now
        self._focus_tab = False         # the tab being opened takes the focus into its content
        self._checks: str | None = None  # the next Tasks tab opens on "delegated"
        self._hr_dot = False            # whether the HR tab wears its dot
        self._snapshot: Read | None = None  # what was last read; every redraw paints it
        self._on = True                 # the blink phase
        self._pressed: tuple | None = None      # the last row a mouse press picked, and when
        self._pressed_at = 0.0
        self._blinking: dict = {}       # agent -> (its AgentRow, its Extra), for the rows that blink

    def compose(self) -> ComposeResult:
        yield from page_header([("Home", "home"), ("Tasks", "tasks"), ("Stats", "stats"), ("HR", "hr"), ("Settings", "settings")])
        yield Static(id="header")
        with Vertical(id="accounts-box") as box:
            box.border_title = "ACCOUNTS"
            yield DataTable(id="accounts", cursor_type="row", show_cursor=False)
            yield Static(id="acct-notes")
        with Vertical(id="agents-box") as box:
            box.border_title = "AGENTS"
            yield DataTable(id="agents", cursor_type="row", show_cursor=False)
            yield Static(id="agent-notes")
        with Vertical(id="messages-box"):
            yield DataTable(id="messages", cursor_type="row", show_cursor=False)
            yield Static(id="message-notes")
        yield Static(id="foot")
        yield KeyGuide(id="home_keys")
        yield TasksView(mine=True, id="tasks_view")
        yield OutlookView(id="stats_view")
        yield ProposalsView(id="hr_view")
        with Vertical(id="settings_view"):
            with VerticalScroll(id="settings_pane"):
                switches = {key: SwitchRow(label, hint, f"g_{key}") for key, label, hint in actions.GLOBAL_SWITCHES}
                yield SectionHead("concurrency")
                with Horizontal(classes="frow"):
                    yield Static("default cap", classes="flabel")
                    yield ImeInput(id="default_cap", compact=True, restrict=r"[0-9]*")
                    yield FieldNote(id="n_default_cap")
                    yield Static("  agents working at once on an account", classes="fhint")
                yield SectionHead("closing windows")
                with Horizontal(classes="frow"):
                    yield Static("idle close", classes="flabel")
                    yield ImeInput(id="idle_close", compact=True, restrict=r"[0-9]*")
                    yield FieldNote(id="n_idle_close")
                    yield Static("  minutes idle before the window closes, 0 = never", classes="fhint")
                yield switches["ignore_cache_expiry"]
                yield SectionHead("automation")
                yield switches["handoff_note"]
                yield switches["auto_accept"]
                yield DataTable(id="answers", cursor_type="row", show_header=False)
                with Horizontal(id="answer_buttons"):
                    yield Button("Add", id="answer_add", compact=True)
                    yield Button("Remove selected", id="answer_remove", compact=True)

    def on_mount(self) -> None:
        self.sub_title = "home"
        for table in ("#accounts", "#agents", "#messages"):
            self.query_one(table, DataTable).can_focus = False
        self.set_interval(self.BLINK_S, self._blink)
        self.query_one("#hr_view").display = False
        self.query_one("#tasks_view").display = False
        self.query_one("#stats_view").display = False
        self.query_one("#settings_view").display = False
        self._retime()
        self._redraw()

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        self.tab = event.tab.id
        shown = set(_TAB_WIDGETS[self.tab])
        for sel in {sel for widgets in _TAB_WIDGETS.values() for sel in widgets}:
            self.query_one(sel).display = sel in shown
        if self.tab == "hr":
            self.query_one("#hr_view", ProposalsView).refresh_rows()
        if self.tab == "tasks":
            view = self.query_one("#tasks_view", TasksView)
            if self._checks or self._checks_shown:      # the view is left as the founder set it
                self._show_tasks(view, self._checks)    # unless a header link sent us here, or it did last time
            else:
                view.refresh_rows()
            self._checks_shown, self._checks = bool(self._checks), None
            if self._focus_tab:
                self._enter_tab()
            self._focus_tab = False
        if self.tab == "stats":
            self.query_one("#stats_view", OutlookView).refresh_rows()
        if self.tab == "settings":
            self._load_settings()

    def _load_settings(self) -> None:
        """Fill the settings form from what is saved, dropping what was typed or marked."""
        self.query_one("#default_cap", Input).value = str(loadmod.default_cap())
        self.query_one("#idle_close", Input).value = actions.idle_close_minutes(idle_close.default_close_timeout_s())
        for key, on in actions.global_switches().items():
            self.query_one(f"#g_{key}", Switch).value = on
        for note in self.query("#settings_pane FieldNote"):
            note.clear()
        self._draw_answers()

    @staticmethod
    def _show_tasks(view: TasksView, which: str | None) -> None:
        if which == "delegated":
            view.show_delegated()
        else:
            view.show_open()

    def _go_tasks(self, which: str) -> None:
        self._checks = which
        tabs = self.query_one("#scope", Tabs)
        if tabs.active == "tasks":
            self._show_tasks(self.query_one("#tasks_view", TasksView), which)
            self._checks = None
            self._checks_shown = True
        else:
            tabs.active = "tasks"

    def action_delegated(self) -> None:
        """The header's `for you`: the Tasks tab filtered to the tasks handed to the founder."""
        self._go_tasks("delegated")

    def _draw_answers(self) -> None:
        table = self.query_one("#answers", DataTable)
        table.clear(columns=True)
        table.add_column("", key="text")
        try:
            self._answers = actions.standing_answers()
        except (OSError, ImportError) as e:
            self._answers = []
            self.toast(f"cannot read the auto-answers: {e}", False)
        for i, a in enumerate(self._answers):
            table.add_row(Text(textsafe.oneline(a["text"])), key=str(i))
        if not self._answers:
            table.add_row(Text("none - ocp asks you when a dialog shows", style="dim"), key="none")

    def _answer_added(self, fields: "dict | None") -> None:
        """The modal's Add adds it at once, not with Save: the form is the confirmation."""
        if fields is not None and any(v.strip() for v in fields.values()):
            ok, said = actions.add_answer(**fields)
            self.toast(said, ok)
            self._draw_answers()

    def _remove_answer(self) -> None:
        """Remove the answer under the cursor, at once."""
        table = self.query_one("#answers", DataTable)
        row = table.cursor_row
        if not self._answers or row >= len(self._answers):
            return
        ok, said = actions.remove_answer(self._answers[row]["kind"], self._answers[row]["key"])
        self.toast(said, ok)
        self._draw_answers()
        table.move_cursor(row=min(row, max(len(self._answers) - 1, 0)))

    def _commit_input(self, box: Input) -> None:
        """Save a typed setting: on Enter or on leaving the field. A value that is refused is
        not written; the note beside it says why and stays until the value is right."""
        note = self.query_one(f"#n_{box.id}", FieldNote)
        shown = {"default_cap": lambda: str(loadmod.default_cap()),
                 "idle_close": lambda: actions.idle_close_minutes(idle_close.default_close_timeout_s())}[box.id]()
        if box.value.strip() == shown:
            note.clear()
            return
        ok, said = (actions.set_default_cap if box.id == "default_cap" else actions.set_idle_close)(box.value)
        note.saved() if ok else note.error(said)

    def on_switch_changed(self, event: Switch.Changed) -> None:
        key = (event.switch.id or "")[2:]
        if (event.switch.id or "").startswith("g_") and key in actions.global_switches():
            event.stop()
            note = self.query_one(f"#n_g_{key}", FieldNote)
            ok, said = actions.save_global_switches({key: event.value})
            if not ok:
                note.error(said)
            elif said:
                note.saved()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in ("default_cap", "idle_close"):
            event.stop()
            self._commit_input(event.input)

    def on_descendant_blur(self, event) -> None:
        if getattr(event.widget, "id", None) in ("default_cap", "idle_close"):
            self._commit_input(event.widget)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "answer_add":
            event.stop()
            self.app.push_screen(AnswerAdd(), self._answer_added)
        elif event.button.id == "answer_remove":
            event.stop()
            self._remove_answer()

    # ------------------------------------------------------------------ drawing

    def _redraw(self) -> None:
        """Paint what was last read, at once, and ask for a fresh read: nothing here waits on the
        database, the disk or a subprocess."""
        if self._snapshot is not None:
            self._paint(self._snapshot)
        self._refresh()

    def _refresh(self) -> None:
        target = self.sel.get(self.panel)
        self.run_worker(lambda: self._read_and_paint(self.panel, target), thread=True,
                        exclusive=True, group="home_read", exit_on_error=False)

    def _read_and_paint(self, panel: int, target) -> None:
        got = self._fetch(panel, target)
        if not get_current_worker().is_cancelled:
            self.app.call_from_thread(self._arrived, got)

    def _fetch(self, panel: int, target) -> Read:
        """The reads of one redraw: on a worker thread, with a connection of its own."""
        con = db.connect()
        try:
            agent_keys = (tui.agent_footer_keys(con, str(target) if target is not None else None)
                          if panel == tui.AGENTS else None)
            summary = summarize(con)
            accounts = tui.accounts_data(con)
            rows, notes = tui.agents_data(con)
            agents = (rows, notes, agent_rows.collect(con, rows))
            messages = tui.messages_data(con)
            proposals = len(open_proposals(con))
        finally:
            con.close()
        usage = {r.name: status.read(r.name) for r in accounts[0]}
        return Read(summary, agent_keys, daemon_running(), accounts, usage, agents, messages, proposals,
                    marks_on_screen())

    def _arrived(self, got: Read) -> None:
        self._snapshot = got
        self._paint(got)

    def _paint(self, got: Read) -> None:
        self.query_one("#header", Static).update(summary_text(got.daemon, clock(), got.summary))
        self._draw_accounts(*got.accounts, got.usage)
        self._draw_agents(*got.agents)
        self._draw_messages(*got.messages)
        self.query_one("#accounts-box").border_subtitle = " · ".join(
            ["│ time passed in the window"] + ([status.SOURCE_LEGEND_SHORT] if got.marks else []))
        foot = Text()
        for jl in self.jobs.lines():
            foot.append(jl.text + "\n", _LEVEL_STYLE.get(jl.level, ""))
        self.query_one("#foot", Static).update(foot)
        self.query_one("#home_keys", KeyGuide).set_keys(self._keys(got.agent_keys))
        self._show_hr_dot(got.proposals)
        if self.tab == "hr":
            self.query_one("#hr_view", ProposalsView).refresh_rows()

    def _draw_accounts(self, rows, notes, usage) -> None:
        table = self.query_one("#accounts", DataTable)
        meters = {r.name: gauge.usage_cells(usage[r.name], True) for r in rows}
        room = table.scrollable_content_region.width or self.size.width - 4
        for cols, with_pct in _ACCT_TIERS:
            heads = ("account", *(tui.ACCT_HEAD[c] for c in cols), "")
            body = []
            for r in rows:
                cells = dict(r.cells)
                if meters[r.name]:
                    five, seven = gauge.usage_cells(usage[r.name], with_pct)
                    cells["five"], cells["seven"] = five, seven
                body.append([Text.from_ansi(f"{tui.BOLD}{tui.oneline(loadmod.label_of(r.name))}{tui.R}"),
                             *(Text.from_ansi(cells[c]) for c in cols),
                             Text.from_ansi(r.mark + r.next)])
            widths = [max(len(h), *(c.cell_len for c in col)) + 2 for h, col in zip(heads, zip(*body))] \
                if body else [0]
            if sum(widths) <= room:
                break
        _set_columns(table, heads)
        sync_rows(table, [(r.name, tuple(cells)) for r, cells in zip(rows, body)])
        self._sync_cursor(table, "#accounts-box", tui.ACCOUNTS, [r.name for r in rows])
        self._notes("#acct-notes", notes)

    def _draw_agents(self, rows, notes, extra) -> None:
        table = self.query_one("#agents", DataTable)
        _set_columns(table, _AGENT_HEADS)
        out = []
        self._blinking = {}
        for r in rows:
            x = extra.get(r.name)
            if x is None:       # a config error: no cells beyond the message
                name = Text.from_ansi(r.twig + f"{tui.BOLD}{r.shown}{tui.R}"
                                      + tui.fold_suffix(r.under, r.urgency))
                out.append((r.name, (name, "", "", Text(f"config error: {tui.oneline(r.error or '')}", style="red"),
                                     "", "", "", "", "")))
                continue
            wall = ""
            if r.wall:
                who = f"{r.wall.account}{r.wall.mark} " if r.wall.elsewhere else ""
                wall = f"{tui.YEL}⏳ {who}{r.wall.when}{r.wall.mark if not who else ''}{tui.R}"
            out.append((r.name, (self._name_cell(r, x, self._on), Text(r.acct), Text(x.kind_short, style="dim"),
                                 self._state_cell(r, x, self._on),
                                 Text.from_ansi(agent_rows.cache_cell(x.cache_left, x.cache_ignored)),
                                 Text.from_ansi(r.ctx), Text(str(r.inbox) if r.inbox else "·"),
                                 Text(str(r.tasks) if r.tasks else "·"),
                                 self._note_cell(r, x, wall))))
            if self._alarm(r, x) or self._fold_errs(r, x) or r.ready:
                self._blinking[r.name] = (r, x)
        sync_rows(table, out)
        self._sync_cursor(table, "#agents-box", tui.AGENTS, [r.name for r in rows])
        self._notes("#agent-notes", notes)
        self.call_after_refresh(self._fit, "#agents", len(_AGENT_HEADS) - 1)

    def _draw_messages(self, title, rows, notes) -> None:
        # The glyph already says what kind of message a row is (⚠ decide, ◷ asked for a
        # reply, ↩ came back, · to read), so there is no kind column and no id; `refused`,
        # the one kind the glyph shares with `answer`, is said in the text.
        self.query_one("#messages-box").border_title = title
        table = self.query_one("#messages", DataTable)
        _set_columns(table, _MESSAGE_HEADS)
        out = []
        for r in rows:
            glyph, _ = tui.message_mark(r.flavour)
            text = Text.from_ansi(r.first + r.behind)
            if r.kind == "refused":
                text = Text.assemble(("refused ", "red"), text)
            text.no_wrap, text.overflow = True, "ellipsis"
            out.append((r.key, (Text(r.when, style="dim"), Text.from_ansi(glyph),
                                Text(r.sender, style="dim" if r.flavour == "read" else "bold"), text)))
        sync_rows(table, out)
        self._sync_cursor(table, "#messages-box", tui.MESSAGES, [r.key for r in rows])
        self._notes("#message-notes", notes)
        self.call_after_refresh(self._fit, "#messages", 3)

    def on_resize(self) -> None:
        self._redraw()
        self._fit("#messages", 3)
        self._fit("#agents", len(_AGENT_HEADS) - 1)

    def _sync_cursor(self, table: DataTable, box: str, panel: int, names: list) -> None:
        """A panel's table is display only: its row cursor is the page's own selection,
        and is drawn only while that panel is the one the keys act on."""
        focused = self.panel == panel
        table.show_cursor = focused
        want = self.sel.get(panel)
        if focused and want is not None and want in names and table.cursor_row != names.index(want):
            table.move_cursor(row=names.index(want))
        self.query_one(box).set_class(focused, "focused")

    def _notes(self, widget: str, notes: list[str]) -> None:
        w = self.query_one(widget, Static)
        w.update(Text.from_ansi("\n".join(f"{tui.DIM}{n}{tui.R}" for n in notes)))
        w.display = bool(notes)

    def _fit(self, table_id: str, col: int) -> None:
        """Give column `col` of a table the width the others leave, so its text is
        cut with an ellipsis instead of scrolling the table sideways."""
        table = self.query_one(table_id, DataTable)
        cols = table.ordered_columns
        if col >= len(cols):
            return
        taken = sum(c.get_render_width(table) for i, c in enumerate(cols) if i != col)
        width = max(10, table.scrollable_content_region.width - taken - 2 * table.cell_padding)
        if cols[col].auto_width or cols[col].width != width:
            cols[col].auto_width, cols[col].width = False, width
            table._require_update_dimensions = True
            table.refresh(layout=True)

    @staticmethod
    def _alarm(r, x) -> bool:
        """Does this row need the founder: a dialog or a fatal state, or a block."""
        return r.state in tui.NEEDS_A_HUMAN or bool(x.block)

    @staticmethod
    def _fold_errs(r, x) -> bool:
        """Does the fold on this row hide an error?"""
        return bool(r.under and x.urgency and x.urgency[0] == "!" and x.urgency[2] == tui.RED)

    def _name_cell(self, r, x, on: bool) -> Text:
        """The name, its fold count, and a warning icon that blinks: on the row itself
        when it needs the founder, on the count when the reports it hides do."""
        out = Text.from_ansi(r.twig + f"{tui.BOLD}{r.shown}{tui.R}")
        if r.under:
            out.append(f" +{r.under}", style="dim")
            if x.urgency:
                glyph, n, colour = x.urgency
                if glyph == "!" and colour == tui.RED:      # hides an error: blinks
                    out.append(f" ⚠{n}" if on else " " * (len(str(n)) + 2), style="bold red")
                elif glyph == "!":                          # hides a limit or a delay
                    out.append(f" ⚠{n}", style="yellow")
                else:                                       # ✉ asks of the founder, ▶ running
                    out.append(f" {glyph}{n}", style="yellow" if glyph == "✉" else "green")
        if x.asks:
            out.append(f" ✉{x.asks}", style="yellow")
        if self._alarm(r, x):
            out.append(" ⚠" if on else "  ", style="bold red")
        if r.ready:
            out.append(" ⚑" if on else "  ", style="bold red")
        return out

    def _note_cell(self, r, x, wall: str) -> Text:
        """What the state column used to carry beyond the word (what it is until, what blocks
        it, what it is stuck on), then what the last column already said."""
        note = Text()
        parts = [Text(r.until, style="dim")] if r.until else []
        if x.block and r.state not in tui.NEEDS_A_HUMAN:
            parts.append(Text(x.block, style="red"))
        elif x.stuck:
            parts.append(Text(f"⚠ {x.stuck}", style="yellow"))
        if r.fire or wall:
            parts.append(Text.from_ansi(r.fire or wall))
        for i, part in enumerate(parts):
            note.append(" · " if i else "")
            note.append_text(part)
        note.no_wrap, note.overflow = True, "ellipsis"
        return note

    def _state_cell(self, r, x, on: bool) -> Text:
        cell = Text.from_ansi(tui.state_cell(r.state, ""))
        if self._alarm(r, x) and on:
            cell.stylize("reverse")
        return cell

    def _blink(self) -> None:
        """Flip the phase and repaint only the cells that blink; a redraw would read
        every agent again."""
        self._on = not self._on
        table = self.query_one("#agents", DataTable)
        keys = [c.key for c in table.ordered_columns]
        for name, (r, x) in self._blinking.items():
            try:
                table.update_cell(name, keys[0], self._name_cell(r, x, self._on))
                table.update_cell(name, keys[3], self._state_cell(r, x, self._on))
            except CellDoesNotExist:
                pass

    def _show_hr_dot(self, n: int) -> None:
        """The HR tab wears a red dot while a proposal waits on the founder, so it is
        seen from the other tabs."""
        if (n > 0) != self._hr_dot:
            self._hr_dot = n > 0
            self.query_one("#scope", Tabs).get_tab("hr").label = (
                Text.assemble("HR", (" ●", "bold red")) if n else Text("HR"))

    # --------------------------------------------------------------------- keys

    def on_key(self, event: events.Key) -> None:
        """↑ from the first row of a tab's content puts the focus on the tab bar, where
        ←→ change tab and ↓ goes back down. With the bar focused, or off the Home tab,
        only `q` is the page's: the row keys would act on rows that are not on screen,
        and a tab's own keys are its widgets'."""
        k = key_token(event)
        if isinstance(self.focused, Tabs):
            if k in tui.NEXT_ROW:
                event.stop()
                self._enter_tab()
            elif k in ("q", "Q", "\x03"):
                event.stop()
                self.app.exit()
            return
        if k in tui.PREV_ROW and self._on_top_row():
            event.stop()
            self.query_one("#scope", Tabs).focus()
            return
        if k in ("q", "Q", "\x03"):
            event.stop()
            self.app.exit()
            return
        if self.tab != "home" or k is None:
            return
        event.stop()
        if k == "?":
            self.app.push_screen(KeyHelp(f"{tui.PANEL_NAME[self.panel]} keys", self._keys()))
            return
        if k == "S":     # T65b: token use per agent and account
            self.app.push_screen(StatsScreen())
            return
        if k == "T":     # every open task: the Tasks tab, free on every panel
            tabs = self.query_one("#scope", Tabs)
            if tabs.active == "tasks":
                self._enter_tab()
            else:
                self._focus_tab = True      # the table takes the keys once the tab is shown, as the tasks screen did
                tabs.active = "tasks"
            return
        if k in tui.NEXT_ROW and self._on_bottom_row():
            return      # the last row is the end: ↓ does not wrap round to the top
        moved = tui.navigate(self.panel, self.sel, k, self.PANELS)
        if moved is not None:
            self.panel = moved
            self._redraw()
            return
        self._press(k)

    def _keys(self, agent_keys=None) -> list[Key]:
        """The panel's own keys, then the ones every panel has; `agent_keys` are the
        ones the row under the cursor allows, none meaning all the panel has."""
        own = [(k, label) for k, label in tui.panel_keys(self.panel, agent_keys) if k != "?"]
        return [Key(k, label, 1 if i < 3 else 3, label) for i, (k, label) in enumerate(own)] + list(_GLOBAL_KEYS)

    def _press(self, k: str) -> None:
        # `_act` can open a modal (`push_screen_wait`), which Textual only allows from
        # inside a worker - not straight from an event handler.
        self.run_worker(self._act_and_redraw(k), exclusive=True, group="act")

    def _on_top_row(self) -> bool:
        """Is the cursor on the first row of the tab, so that ↑ has nowhere to go?"""
        if self.tab == "hr":
            return self.query_one("#hr_table", DataTable).cursor_row == 0
        if self.tab == "tasks":
            return self.query_one("#tasks_table", DataTable).cursor_row == 0
        if self.tab == "stats":
            return self.query_one("#outlook_table", DataTable).cursor_row == 0
        if self.tab != "home":
            return True
        first = next((p for p in self.PANELS if tui._keys_of(p)), None)
        keys = tui._keys_of(first) if first is not None else []
        return first is None or (self.panel == first and self.sel.get(first) == keys[0])

    def _on_bottom_row(self) -> bool:
        """Is the cursor on the last row of the last panel that has one?"""
        last = next((p for p in reversed(self.PANELS) if tui._keys_of(p)), None)
        return last is not None and self.panel == last and self.sel.get(last) == tui._keys_of(last)[-1]

    def _enter_tab(self) -> None:
        """Take the focus from the tab bar into the tab's content."""
        if self.tab == "hr":
            self.query_one("#hr_table").focus()
        elif self.tab == "tasks":
            self.query_one("#tasks_table").focus()
        elif self.tab == "stats":
            self.query_one("#outlook_table").focus()
        else:
            self.set_focus(None)

    def _row_key(self, table: DataTable, row: int):
        """The selection key of a table row, or None for a row the cursor cannot sit on."""
        if not 0 <= row < table.row_count:
            return None
        return table.coordinate_to_cell_key((row, 0)).row_key.value

    def on_mouse_down(self, event: events.MouseDown) -> None:
        """Pressing on a row puts the cursor there, as the arrow keys would; pressing twice opens it, as Enter does. Not on
        Click: the focused panel's DataTable moves its own cursor on Click and stops
        the event, so the screen never sees it and the next redraw undoes the move."""
        row = event.style.meta.get("row")
        # A real MouseDown carries no widget (only a Click does): ask the screen.
        try:
            table = self.get_widget_at(event.screen_x, event.screen_y)[0]
        except NoWidget:
            return
        table = next((w for w in table.ancestors_with_self if isinstance(w, DataTable)), None)
        panel = _PANEL_OF_TABLE.get(table.id) if table is not None else None
        if panel is None or row is None:
            return
        key = self._row_key(table, row)
        if key is None:
            return
        event.stop()
        self.set_focus(None)
        self.panel = panel
        self.sel[panel] = key
        self._redraw()
        now = time.monotonic()
        if self._pressed == (panel, key) and now - self._pressed_at <= DOUBLE_PRESS_S:
            self._pressed = None
            self._press("\r")
        else:
            self._pressed, self._pressed_at = (panel, key), now
