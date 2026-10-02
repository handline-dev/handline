"""One agent's settings as a form: the Settings tab of its chat page and the HR tab's
editor are this one component. Saving writes through `team.agent_settings`, the
founder's own path."""
from __future__ import annotations

from rich.text import Text
from textual.app import ComposeResult
from textual.message import Message
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Input, Select, Static, Switch

from ... import actions
from ...session import idle_close
from ...team import agents
from ...team.agent_settings import (BROWSER_CUSTOM, BROWSER_MODES, HANDOFF_NOTES, WEIGHTS, save_settings,
                                    settings_changes, settings_of, settings_problem)
from .action_bar import ActionBar
from .confirm import Confirm
from .ime import ImeInput
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

    class Loaded(Message):
        """The form was filled from the file: whatever sits in `extra` reloads too."""

    def __init__(self, agent: str, extra=None, **kw) -> None:
        super().__init__(**kw)
        self.agent = agent
        self.settings: dict | None = None
        self._extra = extra

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
                yield ImeInput(id="s_model", compact=True)
            with Horizontal(classes="frow"):
                yield Static("effort", classes="flabel")
                yield Select([(e, e) for e in agents.VALID_EFFORT], id="s_effort", allow_blank=False, compact=True)
            with Horizontal(classes="frow"):
                yield Static("weight", classes="flabel")
                yield Select([(w, w) for w in WEIGHTS], id="s_weight", allow_blank=False, compact=True)
            with Horizontal(classes="frow"):
                yield Static("hand off at", classes="flabel")
                yield ImeInput(id="s_context_k", compact=True, restrict=r"[0-9]*", max_length=6)
                yield Static("k tokens", classes="unit")
                yield Static(id="s_context_share", classes="fvalue")
            with Horizontal(classes="frow"):
                yield Static("handoff note", classes="flabel")
                yield Select(list(HANDOFF_NOTES), id="s_handoff_note", allow_blank=False, compact=True)
            yield SectionHead("work")
            with Horizontal(classes="frow"):
                yield Static("repo", classes="flabel")
                yield ImeInput(id="s_repo", compact=True, placeholder="where it works")
            yield SectionHead("session")
            yield SwitchRow("skip prompts", "claude --dangerously-skip-permissions, from the next launch", "s_skip")
            yield SectionHead("browser")
            with Horizontal(classes="frow"):
                yield Static("browser", classes="flabel")
                yield Select([(label, key) for label, key in BROWSER_MODES], id="s_browser", allow_blank=False, compact=True)
                yield Static("from the next launch", classes="unit")
            with Horizontal(classes="frow sites"):
                yield Static("sites", classes="flabel")
                yield SitesList(id="s_browser_sites")
            with Horizontal(classes="frow"):
                yield Static("idle close", classes="flabel")
                yield ImeInput(id="s_idle_close", compact=True, restrict=r"[0-9]*", max_length=5)
                yield Static("min, 0 = never", classes="unit")
            if self._extra is not None:
                yield self._extra()
        with ActionBar(id="s_buttons"):
            yield Button("Save", id="s_save", variant="primary", compact=True)
            yield Button("Reset", id="s_reset", compact=True)

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
        follows = "on" if actions.global_switches()["handoff_note"] else "off"
        self.query_one("#s_handoff_note", Select).set_options(
            [(f"{label} ({follows})" if value == "default" else label, value) for label, value in HANDOFF_NOTES])
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

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "s_browser":
            self.query_one("#s_browser_sites", SitesList).disabled = event.value != "playwright"

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id in ("s_context_k", "s_model") and self.settings is not None:
            self.show_context_share()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "s_save":
            event.stop()
            self.save()
        elif event.button.id == "s_reset":
            event.stop()
            self.load()

    def save(self) -> None:
        """Save what the form changes, after showing exactly what that is."""
        from ..chat_page import log_action, safe
        if self.settings is None:
            return
        form = self.form()
        problem = settings_problem(form)
        if problem:
            self.app.notify(safe(problem), severity="error", timeout=10, markup=False)
            return
        changes = settings_changes(self.settings, form)
        if not changes:
            self.app.notify("nothing changed", timeout=5, markup=False)
            return
        detail = "\n".join(f"{k}:  {old or '(none)'}  →  {new}" for k, old, new in changes)
        agent = self.agent

        def answered(yes: bool | None) -> None:
            log_action(f"settings {agent}: answered {'yes' if yes else 'no'} - {detail!r}")
            if not yes:
                return
            ok, said = save_settings(agent, changes)
            log_action(f"settings {agent}: {'ok' if ok else 'refused'} - {said}")
            later = ok and any(k in ("skip_permissions", "browser", "browser_sites") for k, _, _ in changes)
            self.app.notify(safe(said) + ("\n(a running session is not restarted"
                                          + ("; skip prompts and the browser apply from its next launch)" if later else ")") if ok else ""),
                            title=f"settings {agent}", severity="information" if ok else "error",
                            timeout=10, markup=False)
            self.load()
        self.app.push_screen(Confirm(f"Change {agent}'s settings", detail,
                                     yes="Save", no="Cancel",
                                     danger=any(k == "skip_permissions" and new == "on" for k, _, new in changes)),
                             answered)
