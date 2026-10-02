"""The proof that a request comes from an agent's own session.

`ocp launch` mints a fresh random token for the agent it becomes, keeps a copy in the protected store
(which no agent session can write) and hands the session its own through the environment it execs
with. Every session is launched with the store's token folder on its sandbox's read-deny list
(`launch.build`), so one agent cannot read another's copy, and the folder is a single path whatever
agents are added later. The daemon, outside the sandbox, compares what a request carries with the
stored copy. A name in the request proves nothing; a token an agent cannot read, guess or overwrite
does. A session launched before this existed holds no token and is not under the deny list until it is
launched again.
"""
from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

from . import names, protected

NS = "session-tokens"
ENV = "OCP_SESSION_TOKEN"


def folder() -> Path:
    return protected.root() / NS


def mint(agent: str) -> str | None:
    """A new token for `agent`, or None when the store cannot be written (an agent's session may not)."""
    token = secrets.token_urlsafe(32)
    try:
        protected.put(NS, agent, token)
    except (OSError, ValueError):       # PermissionError, NotTheFounder and a bad name all say no
        return None
    return token


def held() -> str | None:
    """The token this session was launched with."""
    return names.getenv(ENV) or None


def verify(agent: str | None, token: str | None) -> bool:
    """Whether `token` is the one minted for `agent`'s current session."""
    if not agent or not token:
        return False
    try:
        kept = protected.get(NS, agent)
    except ValueError:
        return False
    return kept is not None and hmac.compare_digest(kept.strip(), str(token))
