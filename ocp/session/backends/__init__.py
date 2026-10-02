"""Which terminal host ocp drives: Orca (the default) or tmux.

`OCP_TERMINAL=tmux` in the environment, or `terminal: tmux` in config.yaml, picks tmux; the
environment wins. A name nobody knows gives a backend that is never available, so a typo
stops the team with a message instead of quietly running on the other host.
"""
from __future__ import annotations

import os

from ...infra import names, paths, yamlcompat
from .base import Backend
from .orca_cli import OrcaBackend
from .tmux import TmuxBackend

DEFAULT = "orca"
KNOWN = {"orca": OrcaBackend, "tmux": TmuxBackend}


class Unknown(Backend):
    """What a configured name nobody knows becomes: nothing is available and nothing answers."""

    def __init__(self, name: str) -> None:
        self.name = name

    def available(self) -> bool:
        return False

    def own_terminal(self) -> tuple[str | None, str | None]:
        return None, None

    def create_terminal(self, command, cwd=None, title=None):
        return None, f"unknown terminal backend {self.name!r}"

    def close_terminal(self, handle, tab=True):
        return False, f"unknown terminal backend {self.name!r}"

    def list_terminals(self):
        return None

    def read_screen(self, handle, limit=40):
        return None

    def send(self, handle, text, enter=True, wait_submit_s=None):
        return False, f"unknown terminal backend {self.name!r}"

    def wait_idle(self, handle, timeout_ms):
        return False

    def focus(self, handle):
        return False, f"unknown terminal backend {self.name!r}"

    def ensure_repo(self, path, present=True):
        return "failed", f"unknown terminal backend {self.name!r} (known: {', '.join(sorted(KNOWN))})"

    def create_worktree(self, repo, name, base_ref):
        return None, f"unknown terminal backend {self.name!r}"


_forced: Backend | None = None
_memo: dict = {}
_name: dict = {}


def configured_name() -> str:
    env = (names.getenv("OCP_TERMINAL") or "").strip().lower()
    if env:
        return env
    # Every screen read asks, so config.yaml is parsed again only when it has changed.
    f = paths.config_file()
    try:
        key = (str(f), f.stat().st_mtime_ns)
    except OSError:
        return DEFAULT
    if _name.get("key") != key:
        try:
            data = yamlcompat.load(f.read_text())
        except (OSError, yamlcompat.ConfigError):
            data = {}
        value = data.get("terminal") if isinstance(data, dict) else None
        _name.update(key=key, name=str(value).strip().lower() if value else DEFAULT)
    return _name["name"]


def current() -> Backend:
    """The backend in force. One instance per name; a test can pin one with `use`."""
    if _forced is not None:
        return _forced
    name = configured_name()
    if name not in _memo:
        _memo[name] = KNOWN[name]() if name in KNOWN else Unknown(name)
    return _memo[name]


def use(backend: Backend | None) -> Backend | None:
    """Pin `backend` (None unpins); returns what was pinned before, for a test to restore."""
    global _forced
    before, _forced = _forced, backend
    return before
