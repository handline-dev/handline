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

    def binary(self) -> str | None:
        """The executable's path, or None when it is not installed."""

    def config_dir_env(self) -> str:
        """The variable that pins a session to one account's config dir."""

    def is_resume(self, extra: list[str] | None) -> bool:
        """Does this argv carry a session to resume?"""

    def launch_argv(self, exe: str, model: str | None, effort: str, disallowed: str,
                    mcp: list[str], preamble: str | None) -> list[str]:
        """argv for one session, before any caller-supplied extras."""

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
        reads or copies - the vendor's own credential and machine state."""

    def memory_root(self, config_dir: Path) -> Path:
        """The folder holding one directory per repo, each with a memory/."""

    def memory_project_dir(self, repo: str) -> str:
        """The name of `repo`'s directory under `memory_root`."""
