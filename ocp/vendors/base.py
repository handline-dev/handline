"""What ocp asks of the tool an agent runs in (design/03-vendor-driver.md).

Only the questions whose answer differs per vendor belong here. This holds the
methods something in ocp calls today; the rest of the design's interface
arrives with the step that first calls it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol


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

    def is_session(self, command: str) -> bool:
        """Is this `ps` command line one of this vendor's agent sessions?

        Both halves are the vendor's own: which binary its sessions run, and
        how it spells a headless run. A headless one is not a session - ocp's
        fold is one, and it inherits its caller's OCP_AGENT, so counting it
        would report that agent alive for as long as the fold runs and closed
        the moment it ends.
        """

    def meters(self) -> bool:
        """Whether ocp can read how much of the account's limit is spent. An
        account whose vendor cannot be read is unmetered: never picked, never
        polled, and only its own agents' agent.yaml sends work there."""

    def is_resume(self, extra: list[str] | None) -> bool:
        """Does this argv carry a session to resume?"""

    def launch_argv(self, exe: str, model: str | None, effort: str, disallowed: str,
                    mcp: list[str], preamble: str | None) -> list[str]:
        """argv for one session, before any caller-supplied extras."""

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

    def credential_present(self, config_dir: Path) -> bool:
        """Whether a credential exists. Never reads or returns it."""

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
        one exception, KICKOFF rule 1 as amended (#2010): a driver's meter
        path may read the account's credential in memory for its one usage
        request, and the secret must reach no agent - not in an agent's env,
        prompt, terminal, logs, db, or any file ocp writes."""

    def memory_root(self, config_dir: Path) -> Path | None:
        """The folder holding one directory per repo, each with a memory/ and
        the repo's session transcripts. None for a vendor that keeps neither."""

    def memory_project_dir(self, repo: str) -> str:
        """The name of `repo`'s directory under `memory_root`."""
