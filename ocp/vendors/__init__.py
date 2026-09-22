"""The driver for each vendor an agent can run in, keyed by agent.yaml's `vendor`
and by the vendor an account records."""
from __future__ import annotations

from pathlib import Path

from .. import agents, paths
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


def known() -> list[Driver]:
    return list(_REGISTRY.values())


def for_agent(name: str) -> Driver:
    return get(agent_vendor(name))


def agent_vendor(name: str) -> str:
    return agents.parts(agents.read(name))[0]


def _vendor_file(acct: str) -> Path:
    return paths.account_dir(acct) / "vendor"


def account_vendor(acct: str) -> str:
    """The vendor this account is an account of. An account that records none
    predates vendors and is the default's; a file that is there but empty is
    not a vendor, so it names none and every driver lookup refuses it."""
    try:
        return _vendor_file(acct).read_text().strip()
    except FileNotFoundError:
        return agents.DEFAULT_VENDOR


def record_vendor(acct: str, vendor: str) -> None:
    get(vendor)
    _vendor_file(acct).write_text(vendor + "\n")


def for_account(acct: str) -> Driver:
    return get(account_vendor(acct))


def config_dir(acct: str) -> Path:
    """The directory the account's `config_dir_env` names."""
    return paths.account_dir(acct) / for_account(acct).config_dir_name


def of_vendor(names: list[str], vendor: str) -> list[str]:
    """The accounts among `names` that an agent of `vendor` can run on. An
    agent runs as its own vendor's binary against the account's credentials,
    so any other account is never a place for it, whatever its headroom."""
    return [a for a in names if account_vendor(a) == vendor]
