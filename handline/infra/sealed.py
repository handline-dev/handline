"""team.yaml and agent.yaml, sealed: only the founder's own path can change what they say.

Both files sit under OCP_HOME, which every agent's sandbox can write, so their
location is no lock. Each is instead sealed: the hash of the text the founder's
session last wrote is kept in the protected store, and a reader refuses a file
that no longer matches. An agent can still scribble on the file, but nothing
runs on what it wrote, and `ocp doctor` names it. Writing goes through
`require_founder` first and `seal` after, so an agent's session never gets as far
as a write.

Seals begin once: the first founder-side read on an installation from before
them seals what is there and leaves a marker (as trust roots do). After that a
file with no seal is refused, so a folder an agent plants for a new or retired
name is never adopted.
"""
from __future__ import annotations

import contextlib
import hashlib
import re

from . import paths, protected
from .yamlcompat import ConfigError

NS = "seal"
TEAM = "team"


def agent_key(name: str) -> str:
    return "agent-" + re.sub(r"[^A-Za-z0-9._-]", "_", name)


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def require_founder() -> None:
    protected._founder_only()


def seal(key: str, text: str) -> None:
    if not _migrated():
        seal_everything()
    protected.put(NS, key, _digest(text))


def forget(key: str) -> None:
    protected.delete(NS, key)


MIGRATED = "migrated"
_observing = 0


@contextlib.contextmanager
def observing():
    """Reads inside this seal nothing, not even the first time: `ocp doctor`."""
    global _observing
    _observing += 1
    try:
        yield
    finally:
        _observing -= 1


def _migrated() -> bool:
    return protected.get(NS, MIGRATED) is not None


def seal_everything() -> list[tuple[str, str]]:
    """Seal team.yaml and every agent.yaml as they are now, and mark the seals
    as begun. The founder's act, by `ocp team seal` or the first founder-side
    read of an installation from before seals. Returns what this took on that
    was not already sealed as it is: (label, "new" | "changed"), so the founder
    sees exactly what they are trusting."""
    adopted: list[tuple[str, str]] = []

    def take(label: str, key: str, text: str) -> None:
        digest, had = _digest(text), protected.get(NS, key)
        if had is None or had.strip() != digest:
            adopted.append((label, "new" if had is None else "changed"))
        protected.put(NS, key, digest)

    p = paths.team_file()
    if p.exists():
        take("team.yaml", TEAM, p.read_text())
    for name in paths.list_agents():
        take(f"agent {name}", agent_key(name), (paths.agent_dir(name) / "agent.yaml").read_text())
    protected.put(NS, MIGRATED, "1")
    return adopted


def state(key: str, text: str) -> str:
    """ok, changed (does not match its seal), unsealed (a file nothing sealed,
    after seals began) or legacy (seals have not begun)."""
    want = protected.get(NS, key)
    if want is not None:
        return "ok" if want.strip() == _digest(text) else "changed"
    return "unsealed" if _migrated() else "legacy"


def mismatch(key: str, text: str) -> bool:
    return state(key, text) in ("changed", "unsealed")


def follow(key: str, before: str, after: str) -> None:
    """A founder-side rewrite of a file by something other than a write path (a
    db migration): keep the seal only if it was holding `before`."""
    if not protected.in_agent_session() and state(key, before) == "ok":
        seal(key, after)


def check(key: str, text: str, where) -> None:
    """ConfigError when `text`, read from `where`, is not what was sealed."""
    st = state(key, text)
    if st == "legacy" and not protected.in_agent_session() and not _observing:
        try:
            seal_everything()
        except OSError:
            pass
        st = state(key, text)
    if st in ("ok", "legacy"):
        return
    why = "was changed outside the founder's path" if st == "changed" else "was never sealed"
    raise ConfigError(
        f"{where} {why} and is not trusted. Roster and routing change only through an "
        f"approved `ocp hr` proposal or the founder's own session; if this file is the "
        f"founder's, run `ocp team seal`.")
