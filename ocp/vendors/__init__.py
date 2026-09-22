"""The driver for each vendor an agent can run in, keyed by agent.yaml's `vendor`."""
from __future__ import annotations

from .. import agents
from .base import Driver
from .claude import Claude

_REGISTRY: dict[str, Driver] = {d.name: d for d in (Claude(),)}


class UnknownVendor(ValueError):
    pass


def get(vendor: str) -> Driver:
    try:
        return _REGISTRY[vendor]
    except KeyError:
        raise UnknownVendor(f"no driver for vendor {vendor!r}; "
                            f"known: {', '.join(sorted(_REGISTRY))}") from None


def for_agent(name: str) -> Driver:
    return get(agents.parts(agents.read(name))[0])


def for_accounts() -> Driver:
    """The driver every account belongs to. An account records no vendor
    yet, so all of them are the default vendor's."""
    return get(agents.DEFAULT_VENDOR)
