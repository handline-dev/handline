"""The proof that a request comes from an agent's own session.

`ocp launch` mints a fresh random token for the agent it becomes and keeps it in a file only that
agent can read: every other agent's session is launched with that file on its sandbox's read-deny and
write-deny lists (`launch.hidden_tokens`). The daemon, outside the sandbox, compares what a request
carries with the file. A name in the request proves nothing; a token an agent cannot read, guess or
overwrite does. Sessions launched before this existed are not under the deny lists and hold no
token until they are launched again.
"""
from __future__ import annotations

import hmac
import os
import secrets
import tempfile
from pathlib import Path

from . import paths

NAME = "session.token"


def path_of(agent: str) -> Path:
    return paths.agent_dir(agent) / NAME


def mint(agent: str) -> str | None:
    """A new token for `agent`, or None when its file cannot be written (not this agent's to write)."""
    token = secrets.token_urlsafe(32)
    target = path_of(agent)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".token-")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(token)
            os.chmod(tmp, 0o600)
            os.replace(tmp, target)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError:
        return None
    return token


def read(agent: str) -> str | None:
    try:
        return path_of(agent).read_text().strip() or None
    except OSError:
        return None


def verify(agent: str | None, token: str | None) -> bool:
    """Whether `token` is the one minted for `agent`'s current session."""
    if not agent or not token:
        return False
    kept = read(agent)
    return kept is not None and hmac.compare_digest(kept, str(token))


def others(agent: str) -> list[str]:
    """Every other agent's token file: what `agent`'s session may neither read nor write."""
    return [str(path_of(a)) for a in paths.list_agents() if a != agent]
