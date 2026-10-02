"""One agent's settings as a form: the Settings tab of its chat page and the HR tab's
editor are this one component. Saving writes through `team.agent_settings`, the
founder's own path."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.message import Message
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Input, OptionList, Select, Static, Switch

from ... import actions
from ...session import idle_close
from ...team import agents
from ...team.agent_settings import (BROWSER_CUSTOM, BROWSER_MODES, HANDOFF_NOTES, NOTHING, WEIGHTS, changes_for,
                                    known_models, save_settings, settings_of)
from .confirm import Confirm
from .ime import ImeInput
from .field_note import FieldNote
from .pick_or_type import PickOrType
from .section_head import SectionHead
from .sites_list import SitesList
from .switch_row import SwitchRow


class AgentSettings(Vertical):
    """The form and its Save / Reset. `extra` is drawn under the last field, inside the scroll."""

    DEFAULT_CSS = """
    AgentSettings { height: 1fr; }
    AgentSettings #settings_pane { height: 1fr; padding: 1 3; }
    AgentSettings .frow { height: 1; margin-bottom: 1; }
    AgentSettings .frow.sites { height: auto; }
    AgentSettings .frow SitesList { width: 60; }
    AgentSettings .flabel { width: 18; color: $text-muted; }
    AgentSettings .fvalue { width: 1fr; }
    AgentSettings .fhint { color: $text-muted; }
    AgentSettings .frow Input, AgentSettings .frow Select { width: 40; }
    AgentSettings .frow #s_context_k { width: 10; }
    AgentSettings .frow #s_idle_close { width: 10; }
    AgentSettings .frow .unit { width: auto; padding-left: 1; }
    """

    # What each control writes: the fields one commit takes together.
    FIELDS = {"s_model": ("model",), "s_effort": ("effort",), "s_weight": ("weight",), "s_repo": ("repo",),
              "s_context_k": ("context_k",), "s_handoff_note": ("handoff_note",), "s_skip": ("skip_permissions",),
              "s_browser": ("browser", "browser_sites"), "s_browser_sites": ("browser", "browser_sites"),
              "s_idle_close": ("idle_close",)}
    NOTES = {"s_browser_sites": "s_browser"}      # the sites share the browser's note
    TEXT = ("s_model", "s_repo", "s_context_k", "s_idle_close")

    class Loaded(Message):
        """The form was filled from the file: whatever sits in `extra` reloads too."""

    def __init__(self, agent: str, extra=None, **kw) -> None:
        super().__init__(**kw)
        self.agent = agent
        self.settings: dict | None = None
        self._extra = extra

    @staticmethod
    def handoff_choices() -> list[tuple[str, str]]:
        """The handoff note's choices, "follow the global switch" naming what that switch says now."""
        follows = "on" if actions.global_switches()["handoff_note"] else "off"
        return [(f"{label} ({follows})" if value == "default" else label, value) for label, value in HANDOFF_NOTES]

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="settings_pane"):
            yield SectionHead("agent")
            for key, label in (("session_account", "session runs on"),
                               ("vendor", "vendor"), ("enabled", "enabled")):
                with Horizontal(classes="frow"):
                    yield Static(label, classes="flabel")
                    yield Static(id=f"s_{key}", classes="fvalue")
            yield SectionHead("model")
            with Horizontal(classes="frow"):
                yield Static("model", classes="flabel")
                yield PickOrType(known_models(), "s_model", id="s_model_box")
                yield FieldNote(id="n_s_model")
            with Horizontal(classes="frow"):
                yield Static("effort", classes="flabel")
                yield Select([(e, e) for e in agents.VALID_EFFORT], id="s_effort", allow_blank=False, compact=True)
                yield FieldNote(id="n_s_effort")
            with Horizontal(classes="frow"):
                yield Static("weight", classes="flabel")
                yield Select([(w, w) for w in WEIGHTS], id="s_weight", allow_blank=False, compact=True)
                yield FieldNote(id="n_s_weight")
            with Horizontal(classes="frow"):
                yield Static("hand off at", classes="flabel")
                yield ImeInput(id="s_context_k", compact=True, restrict=r"[0-9]*", max_length=6)
                yield Static("k tokens", classes="unit")
                yield Static(id="s_context_share", classes="fvalue")
                yield FieldNote(id="n_s_context_k")
            with Horizontal(classes="frow"):
                yield Static("handoff note", classes="flabel")
                yield Select(self.handoff_choices(), id="s_handoff_note", allow_blank=False, compact=True)
                yield FieldNote(id="n_s_handoff_note")
            yield SectionHead("work")
            with Horizontal(classes="frow"):
                yield Static("repo", classes="flabel")
                yield ImeInput(id="s_repo", compact=True, placeholder="where it works")
                yield FieldNote(id="n_s_repo")
            yield SectionHead("session")
            yield SwitchRow("skip prompts", "claude --dangerously-skip-permissions, from the next launch", "s_skip")
            yield SectionHead("browser")
            with Horizontal(classes="frow"):
                yield Static("browser", classes="flabel")
                yield Select([(label, key) for label, key in BROWSER_MODES], id="s_browser", allow_blank=False, compact=True)
                yield Static("from the next launch", classes="unit")
                yield FieldNote(id="n_s_browser")
            with Horizontal(classes="frow sites"):
                yield Static("sites", classes="flabel")
                yield SitesList(id="s_browser_sites")
            with Horizontal(classes="frow"):
                yield Static("idle close", classes="flabel")
                yield ImeInput(id="s_idle_close", compact=True, restrict=r"[0-9]*", max_length=5)
                yield Static("min, 0 = never", classes="unit")
                yield FieldNote(id="n_s_idle_close")
            if self._extra is not None:
                yield self._extra()

    def load(self, agent: str | None = None) -> None:
        """Fill the form from the agent's file (another agent's, when one is given)."""
        from ..chat_page import safe
        self.agent = agent or self.agent
        try:
            self.settings = cfg = settings_of(self.agent)
        except (OSError, agents.ConfigError) as e:
            self.settings = None
            self.app.notify(f"cannot read this agent's settings: {safe(e)}", severity="error", timeout=10, markup=False)
            return
        for note in self.query(FieldNote):
            note.clear()
        for key in ("session_account", "vendor"):
            self.query_one(f"#s_{key}", Static).update(Text(safe(cfg[key])))
        self.query_one("#s_enabled", Static).update("yes" if cfg["enabled"] else "no")
        self.query_one("#s_model", Input).value = safe(cfg["model"])
        self.query_one("#s_repo", Input).value = safe(cfg["repo"])
        self.query_one("#s_context_k", Input).value = cfg["context_k"]
        self.query_one("#s_skip", Switch).value = cfg["skip_permissions"] == "on"
        modes = BROWSER_MODES + (BROWSER_CUSTOM,) if cfg["browser"] == "custom" else BROWSER_MODES
        self.query_one("#s_browser", Select).set_options(modes)
        self.query_one("#s_browser", Select).value = cfg["browser"]
        self.query_one("#s_browser_sites", SitesList).set_sites(cfg["browser_sites"].split())
        self.query_one("#s_browser_sites", SitesList).disabled = cfg["browser"] != "playwright"
        self.query_one("#s_idle_close", Input).value = cfg["idle_close"]
        self.query_one("#s_idle_close", Input).placeholder = actions.idle_close_minutes(idle_close.default_close_timeout_s())
        self.show_context_share()
        self.query_one("#s_effort", Select).value = cfg["effort"] if cfg["effort"] in agents.VALID_EFFORT else "medium"
        self.query_one("#s_weight", Select).value = cfg["weight"] if cfg["weight"] in WEIGHTS else "light"
        self.query_one("#s_handoff_note", Select).set_options(self.handoff_choices())
        self.query_one("#s_handoff_note", Select).value = cfg["handoff_note"]
        self.post_message(self.Loaded())

    def form(self) -> dict:
        return {"model": self.query_one("#s_model", Input).value,
                "effort": str(self.query_one("#s_effort", Select).value),
                "weight": str(self.query_one("#s_weight", Select).value),
                "repo": self.query_one("#s_repo", Input).value,
                "skip_permissions": "on" if self.query_one("#s_skip", Switch).value else "off",
                "browser": str(self.query_one("#s_browser", Select).value),
                "browser_sites": " ".join(self.query_one("#s_browser_sites", SitesList).sites),
                "context_k": self.query_one("#s_context_k", Input).value,
                "handoff_note": str(self.query_one("#s_handoff_note", Select).value),
                "idle_close": self.query_one("#s_idle_close", Input).value}

    def show_context_share(self) -> None:
        self.query_one("#s_context_share", Static).update(
            Text("= " + actions.context_share(self.query_one("#s_context_k", Input).value,
                                              self.settings["model_id"] or self.query_one("#s_model", Input).value)
                 if self.query_one("#s_context_k", Input).value else ""))

    # ------------------------------------------------------------------ saving

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "s_browser":
            self.query_one("#s_browser_sites", SitesList).disabled = event.value != "playwright"
        if event.select.id in self.FIELDS:
            self.commit(event.select.id)

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.switch.id in self.FIELDS:
            self.commit(event.switch.id)

    def on_sites_list_changed(self, event: SitesList.Changed) -> None:
        event.stop()
        self.commit("s_browser_sites")

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id in ("s_context_k", "s_model") and self.settings is not None:
            self.show_context_share()

    def on_pick_or_type_picked(self, event: PickOrType.Picked) -> None:
        event.stop()
        self.commit(event.input.id)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in self.TEXT:
            event.stop()
            self.commit(event.input.id)

    def on_descendant_blur(self, event) -> None:
        wid = getattr(event.widget, "id", None)
        if wid in self.TEXT and not isinstance(self.app.focused, OptionList):
            self.commit(wid)

    def commit(self, control: str) -> bool:
        """Save what the control's field changes, at once. A value the agent file would refuse
        is not written: its note says why, and it stays until the value is right."""
        from ..chat_page import log_action
        if self.settings is None:
            return True
        keys = self.FIELDS[control]
        form = self.form()
        note = self.query_one(f"#n_{self.NOTES.get(control, control)}", FieldNote)
        changes, problem = changes_for(self.agent, {k: form[k] for k in keys})
        if problem.startswith(NOTHING):
            note.clear()
            return True
        if problem:
            note.error(problem)
            return False
        ok, said = save_settings(self.agent, changes)
        log_action(f"settings {self.agent}: {'ok' if ok else 'refused'} - {said}")
        if not ok:
            note.error(said)
            return False
        self.settings = settings_of(self.agent)
        later = any(k in ("skip_permissions", "browser", "browser_sites") for k, _, _ in changes)
        note.saved("saved, from the next launch" if later else "saved")
        if control in ("s_context_k", "s_model"):
            self.show_context_share()
        return True

    def settle(self) -> list[str]:
        """Try every field whose value differs from the file and return the reasons any
        could not be saved: what going back must ask about."""
        if self.settings is None:
            return []
        form = self.form()
        controls = [c for c in self.FIELDS if c not in self.NOTES]
        for control in controls:
            if any(form[k] != self.settings[k] for k in self.FIELDS[control]):
                self.commit(control)
            else:       # typed back to what is saved: an old refusal no longer holds anything
                self.query_one(f"#n_{control}", FieldNote).clear()
        return [note.problem for c in controls if (note := self.query_one(f"#n_{c}", FieldNote)).problem]
