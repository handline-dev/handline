"""What ocp asks of the tool an agent runs in (design/03-vendor-driver.md).

Only the questions whose answer differs per vendor belong here. This holds the
methods something in ocp calls today; the rest of the design's interface
arrives with the step that first calls it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Window:
    """One usage window the vendor reports, named by its length and nothing else:
    ocp finds the shortest and the longest and never asks what they are called.

    `pct` is None when the endpoint returned no such window, which is a reading
    and not an absence - it means nothing has been spent in it.
    """
    minutes: int
    pct: int | None
    reset: int | None


@dataclass(frozen=True)
class Reading:
    """One account's usage as its vendor's endpoint reported it. A vendor with
    one window reports one, and one with three reports three."""
    account: str
    windows: tuple[Window, ...]
    at: int

    @property
    def short(self) -> Window | None:
        """The shortest window it reports, or None when it reports none."""
        return min(self.windows, key=lambda w: w.minutes, default=None)

    @property
    def long(self) -> Window | None:
        """The longest window, or None when it reports fewer than two."""
        return max(self.windows, key=lambda w: w.minutes) if len(self.windows) > 1 else None


@dataclass(frozen=True)
class Snapshot:
    """What one session's transcript says: the model and its context. Where the
    transcript is, and when it was last written, are the reader's to add
    (`usage/collect.py`). `ctx_used` is the vendor's own count breakdown, whose
    total is the sum of its values - so a collector keeps only the counts that
    make up the context (claude's `slim_usage`), and a dict written by hand
    into status.json is summed as it stands. `ctx_pct` is None when unknown."""
    model_id: str | None
    ctx_used: dict | None
    ctx_size: int | None
    ctx_pct: int | None
    cost_usd: float | None
    lines_added: int | None
    lines_removed: int | None


class Collector(Protocol):
    """How ocp reads one session's context from what the vendor writes."""

    def collect(self, transcript: Path, config_dir: Path) -> Snapshot | None:
        """The transcript's snapshot, or None when it cannot be read."""


class Meter(Protocol):
    """How ocp reads one vendor's usage for one account, without spending it."""

    # Whether one real turn on the account renews a dead credential (claude's
    # token expires and is renewed only by a running session); `recover` spends
    # that turn only where this is True.
    renews_by_turn: bool

    def read_usage(self, acct: str, refresh: bool = True) -> tuple[Reading | None, bool]:
        """(the reading, or None when it could not be asked; whether that
        failure was the credential itself being refused). None is "could not
        look", never "nothing spent". `refresh=False` skips a renewal for a
        caller that knows the credential is live."""


class StateHooks(Protocol):
    """How a vendor's agents report RUNNING and IDLE: hooks ocp installs into a
    settings file every account of the vendor shares. A vendor without them has
    no StateHooks (`Driver.state_hooks()` is None) and its state is read off the
    screen instead."""

    def source_settings(self) -> Path:
        """Where the host application (Orca) installs its own hooks."""

    def target_settings(self) -> Path:
        """The shared settings file every account of this vendor links to."""

    def plan(self) -> tuple[dict, dict, bool]:
        """(Orca's hooks at the source, those in the shared file, drifted?)."""

    def sync(self, quiet: bool = False) -> bool:
        """Mirror Orca's hooks into the shared file; True if it changed."""

    def watch(self, interval: float = 5.0, once: bool = False) -> None:
        """Re-apply `sync` whenever the source changes."""

    def install(self, exe: str, wanted: set[str]) -> tuple[list[str], int]:
        """Put `exe hook <event>` for each of `wanted` in the shared file, once
        each: (events added, duplicate entries removed)."""

    def allow_home_writes(self) -> tuple[bool, list[str]]:
        """Let a sandboxed agent write under OCP_HOME: (changed, blockers)."""

    def ensure_shared(self) -> Path:
        """Create the shared config dir and its settings file; refuses, changing
        nothing, when the settings file is not a JSON object."""

    def shared_problems(self) -> list[tuple[str, str]]:
        """(what, detail) for each way the shared settings would break a session."""


class Driver(Protocol):
    name: str
    min_version: tuple[int, ...]
    # What `config_dir_env` points at, under the account's own directory.
    config_dir_name: str

    def binary(self) -> str | None:
        """The executable's path, or None when it is not installed."""

    def config_dir_env(self) -> str:
        """The variable that pins a session to one account's config dir."""

    def headless_argv(self, exe: str, model: str | None, prompt: str) -> list[str] | None:
        """argv for one one-shot, non-interactive run of `prompt`.

        None: this vendor has no such run, and a caller that needs one (fold)
        refuses cleanly rather than guessing at a flag nobody has measured."""

    def can_headless(self) -> bool:
        """Whether `headless_argv` ever gives an argv: this vendor has a
        one-shot run a fold can use. A capability the caller asks directly,
        never one it infers by probing `headless_argv` with placeholders."""

    def headless_model(self, model: str | None) -> str:
        """The model name `headless_argv(exe, model, ...)` actually runs:
        `model` itself, or this vendor's own cheap default when none was
        asked for by name. A caller that needs the model it is about to spend
        (fold's own spend record) asks this rather than assuming `model` is
        it. Never called for a vendor whose `headless_argv` is always None."""

    def is_session(self, command: str) -> bool:
        """Is this `ps` command line one of this vendor's agent sessions?

        Both halves are the vendor's own: which binary its sessions run, and
        how it spells a headless run. A headless one is not a session - ocp's
        fold is one, and it inherits its caller's OCP_AGENT, so counting it
        would report that agent alive for as long as the fold runs and closed
        the moment it ends.
        """

    def meter(self) -> Meter | None:
        """This vendor's Meter, or None when its usage cannot be read. An
        account whose vendor has none is unmetered: never picked, never
        polled, and only its own agents' agent.yaml sends work there."""

    def collector(self) -> Collector | None:
        """This vendor's Collector, or None when it writes no transcript ocp
        can read."""

    def context_window(self, model_id: str | None) -> int | None:
        """The context window of `model_id` in tokens, None when this vendor
        does not know it. Never a guess: a wrong size mis-fires the context
        trigger."""

    def state_hooks(self) -> StateHooks | None:
        """This vendor's StateHooks, or None when its agents do not report
        their state through hooks."""

    def is_resume(self, extra: list[str] | None) -> bool:
        """Does this argv carry a session to resume?"""

    def launch_argv(self, exe: str, model: str | None, effort: str, disallowed: str,
                    mcp: list[str], preamble: str | None,
                    hide: list[str] | None = None) -> list[str]:
        """argv for one session, before any caller-supplied extras. `hide` is
        paths the session may not read."""

    def skip_permissions_argv(self) -> list[str]:
        """argv that makes this vendor's session stop asking before it acts,
        empty when it has no such switch."""

    def transcript_dir(self, config_dir: Path, worktree: str) -> Path | None:
        """The folder holding the transcripts of sessions run in `worktree`,
        or None when the vendor keeps none we can name."""

    def launch_env(self, preamble: str | None, disallowed: str, state_dir: Path) -> dict[str, str]:
        """Environment one session needs beyond its account's, for what its
        argv cannot carry. `state_dir` is the agent's own, for a file the
        vendor has to be pointed at."""

    def prompt_argv(self, prompt: str) -> list[str]:
        """argv that submits `prompt` as the session's first message."""

    def clear_command(self) -> str:
        """Typed to start a fresh session in the same process."""

    def exit_command(self) -> str:
        """Typed to end the session and leave the shell standing."""

    # Whether Orca's tui-idle answer needs the screen's agreement. Orca reads
    # claude's working state; it reports an opencode turn idle throughout.
    idle_from_screen: bool

    def idle_on_screen(self, tail: list[str]) -> bool:
        """With `idle_from_screen`, whether the rendered screen shows the
        session waiting for input."""

    def working_on_screen(self, tail: list[str]) -> bool | None:
        """Whether the rendered screen shows a turn in progress, with
        positive evidence both ways: True working, False idle at its prompt,
        None when the screen carries no evidence either way - a shell, a
        start screen - and the reader keeps its own state. For a driver whose
        working state Orca reads (idle_from_screen False), the answer is
        None: Orca's word is that vendor's witness, not its screen."""

    def screen_blocker(self, tail: list[str]):
        """What on this vendor's rendered screen a typed line would answer or
        merge into, as an `orca.Blocker`, or None when its prompt is ready."""

    def trust_screen(self, tail: list[str]) -> str | None:
        """The folder this vendor's own folder-trust dialog names, when
        `tail` shows it. None: not showing it, or this vendor has no such
        dialog. "": showing it, but the folder could not be parsed out (a
        cut-off or mid-redraw screen) - still showing, so a caller telling
        "showing" from "not showing" checks `is not None`, never truthiness,
        or an unparseable screen reads as no dialog and a wake types blind
        into it."""

    def trust_answer(self) -> str:
        """Keys sent, then Enter, to accept the folder `trust_screen` named.
        Never called for a vendor whose `trust_screen` never returns one."""

    def accepts_tool(self, tool: str | None) -> bool:
        """Is a permission prompt for this tool call one `accepting_key` may
        say yes to? Asked of the hook's word before any screen is read."""

    def accepting_key(self, dialog, tool: str | None) -> tuple[str | None, str]:
        """(key, why not) - the key that says yes, once, to this vendor's
        prompt to run `tool` (the tool call the agent's hook reported), when
        `dialog` is exactly that prompt and the option highlighted is the plain
        one-time yes. Never a key that says yes for good ("don't ask again"
        writes the vendor's settings) and never for a dialog that is anything
        else: no key, and the reason. A vendor with no such dialog returns no
        key."""

    def credential_present(self, config_dir: Path) -> bool | None:
        """Whether a credential exists. Never reads or returns it. None when
        this process cannot tell, which is not the same as no."""

    def login_argv(self) -> list[str]:
        """The login a human finishes, run with `config_dir_env` set."""

    def logout_argv(self) -> list[str]:
        """The vendor's own logout, run with `config_dir_env` set."""

    def identity(self, env: dict) -> str | None:
        """Who the account in `env` is signed in as, asked of the vendor."""

    def subscription_type(self, env: dict) -> str | None:
        """The plan the account in `env` is on, asked of the vendor."""

    def shared_items(self) -> list[str]:
        """Names under shared/ linked into every account's config dir."""

    def never_touch(self) -> set[str]:
        """Names inside an account's config dir ocp never creates, links,
        reads or copies - the vendor's own credential and machine state. The
        one exception, AGENTS.md rule 1 as amended (#2010): a driver's meter
        path may read the account's credential in memory for its one usage
        request, and the secret must reach no agent - not in an agent's env,
        prompt, terminal, logs, db, or any file ocp writes."""

    def memory_root(self, config_dir: Path) -> Path | None:
        """The folder holding one directory per repo, each with a memory/ and
        the repo's session transcripts. None for a vendor that keeps neither."""

    def memory_project_dir(self, repo: str) -> str:
        """The name of `repo`'s directory under `memory_root`."""
