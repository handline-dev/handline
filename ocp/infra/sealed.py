"""team.yaml and agent.yaml, sealed: only the founder's own path can change what they say.

Both files sit under OCP_HOME, which every agent's sandbox can write, so their
location is no lock. Each is instead sealed: the hash of the text the founder's
session last wrote is kept in the protected store, and a reader refuses a file
that no longer matches. An agent can still scribble on the file, but nothing
runs on what it wrote, and `ocp doctor` names it. Writing goes through
`require_founder` first and `seal` after, so an agent's session never gets as far
as a write.

A file with no seal yet (an installation from before seals) is sealed by the
first read from a founder-side session; an agent's read just accepts it.
"""
from __future__ import annotations

import hashlib
import re

from . import protected
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
    protected.put(NS, key, _digest(text))


def forget(key: str) -> None:
    protected.delete(NS, key)


def mismatch(key: str, text: str) -> bool:
    want = protected.get(NS, key)
    return want is not None and want.strip() != _digest(text)


def check(key: str, text: str, where) -> None:
    """ConfigError when `text`, read from `where`, is not what was sealed."""
    want = protected.get(NS, key)
    if want is None:
        if not protected.in_agent_session():
            try:
                seal(key, text)
            except OSError:
                pass
        return
    if want.strip() != _digest(text):
        raise ConfigError(
            f"{where} was changed outside the founder's path and is not trusted. Roster "
            f"and routing change only through an approved `ocp hr` proposal or the "
            f"founder's own session; if this edit is the founder's, run `ocp team seal`.")
