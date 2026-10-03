"""The product is moving from the name ocp to Handline. For a release every setting is read under
both names, the new one first: HANDLINE_HOME beside OCP_HOME, HANDLINE_AGENT beside OCP_AGENT, and so
on. Everything this process starts is given both, so a child on older code reads what it always did.
"""
from __future__ import annotations

import os

OLD, NEW = "OCP_", "HANDLINE_"

# The command, main name first. `hl` is what is printed and installed; the other two stay working
# aliases for at least a release, so a running agent or script that still types them is not cut off.
COMMANDS = ("hl", "handline", "ocp")


def getenv(name: str, default: str | None = None) -> str | None:
    """`name` (written in either form) read as HANDLINE_ first, then OCP_. A variable that is set
    but empty counts as set: whether empty is allowed is the reader's rule, not this one's."""
    suffix = name.removeprefix(OLD).removeprefix(NEW)
    for prefix in (NEW, OLD):
        value = os.environ.get(prefix + suffix)
        if value is not None:
            return value
    return default


def setenv(env: dict, name: str, value: str) -> None:
    """Set `name` in both spellings in `env`, so whichever a reader tries first reads this value and
    not one inherited from a parent."""
    suffix = name.removeprefix(OLD).removeprefix(NEW)
    env[OLD + suffix] = value
    env[NEW + suffix] = value


def both(env: dict) -> dict:
    """`env` with a HANDLINE_ twin for every OCP_ entry, and an OCP_ twin for every HANDLINE_ one,
    each taking the other's value only when it has none of its own."""
    out = dict(env)
    for key, value in env.items():
        if key.startswith(OLD):
            out.setdefault(NEW + key[len(OLD):], value)
        elif key.startswith(NEW):
            out.setdefault(OLD + key[len(NEW):], value)
    return out
