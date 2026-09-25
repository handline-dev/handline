"""load - how full each account is with agents, as opposed to with usage.

Two facts the pick needs beyond an account's own reading: how many agents are
mid-turn on it, and how fast it is filling. The second is the account's own
measured burn - every agent on an account spends the one window, so the
account's rate already is the combined rate of the agents on it.

The cap is a hard limit on the first: most agents mid-turn on one account at
once. It is a preference about hardware and plan (a Pro window feeds two
agents, not five), so it is set per account in config.yaml as
`active_cap_<account>: n`, over an `active_cap: n` default, and from the
bridge. Running agents never move because of it; it only decides where the next
one may start.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..team import agents
from . import burnrate, status
from .. import db
from ..infra import paths
from ..session import triggers
from ..infra.yamlcompat import ConfigError, dump, load as load_yaml


@dataclass
class Load:
    account: str
    active: int              # agents mid-turn on it
    cap: int
    burn: float | None       # percent of the 5h window per second, None unmeasured
    costs: "burnrate.Costs | None" = None   # what one more agent costs (one estimate for every account) and the week ratios

    @property
    def at_cap(self) -> bool:
        return self.active >= self.cap


def _config() -> dict:
    try:
        data = load_yaml(paths.config_file().read_text())
    except (OSError, ConfigError):
        return {}
    return data if isinstance(data, dict) else {}


def own_cap(acct: str, cfg: dict | None = None) -> int | None:
    """The cap set for this account alone, or None when it follows the default."""
    v = (_config() if cfg is None else cfg).get(f"active_cap_{acct}")
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 1 else None


def cap_of(acct: str, cfg: dict | None = None) -> int:
    cfg = _config() if cfg is None else cfg
    own = own_cap(acct, cfg)
    return own if own is not None else default_cap(cfg)


def set_cap(acct: str, n: int) -> None:
    """Write the cap for one account, keeping every other setting. A cap of
    less than one would be an account that can never take work, which is what
    removing it is for."""
    if n < 1:
        raise ValueError("a cap is at least 1")
    cfg = _config()
    cfg[f"active_cap_{acct}"] = n
    paths.config_file().write_text(dump(cfg))


def set_default_cap(n: int) -> None:
    """The cap of every account that has none of its own."""
    if n < 1:
        raise ValueError("a cap is at least 1")
    cfg = _config()
    cfg["active_cap"] = n
    paths.config_file().write_text(dump(cfg))


def default_cap(cfg: dict | None = None) -> int:
    v = (_config() if cfg is None else cfg).get("active_cap")
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 1 else triggers.DEFAULT_ACTIVE_CAP


def label_of(acct: str, cfg: dict | None = None) -> str:
    """The name the screens show for an account; the account's own name until one is set."""
    v = (_config() if cfg is None else cfg).get(f"label_{acct}")
    return v if isinstance(v, str) and v.strip() else acct


def set_label(acct: str, text: str) -> None:
    """Show this account under another name; blank goes back to its own. The account
    is still what agents and the CLI call it."""
    cfg = _config()
    if text.strip():
        cfg[f"label_{acct}"] = text.strip()
    else:
        cfg.pop(f"label_{acct}", None)
    paths.config_file().write_text(dump(cfg))


def usage_recover_enabled(acct: str, cfg: dict | None = None) -> bool:
    """Whether #1885's recovery turn may run on this account - on by default,
    off only where config.yaml's `usage_recover_<account>: false` says so."""
    cfg = _config() if cfg is None else cfg
    return cfg.get(f"usage_recover_{acct}") is not False


def set_usage_recover(acct: str, on: bool) -> None:
    """Write the per-account switch, keeping every other setting."""
    cfg = _config()
    if on:
        cfg.pop(f"usage_recover_{acct}", None)
    else:
        cfg[f"usage_recover_{acct}"] = False
    paths.config_file().write_text(dump(cfg))


def active_by_account(con) -> dict[str, list[str]]:
    """Agents mid-turn, by the account each is running on. An agent that has
    just moved is mid-turn on its handback before the daemon has seen it run,
    so it counts from the move until its next Stop; without that, agents that
    wall together all see the same empty destination."""
    found: dict[str, list[str]] = {}
    rows = con.execute(
        "SELECT agent FROM agent_state WHERE closed_at IS NULL AND (state = 'RUNNING' OR "
        "EXISTS (SELECT 1 FROM events m WHERE m.agent = agent_state.agent "
        "AND m.kind = 'moved_account' AND m.id > COALESCE((SELECT MAX(s.id) FROM events s "
        "WHERE s.agent = agent_state.agent AND s.kind = 'Stop'), 0)))").fetchall()
    for row in rows:
        acct = agents.current_account(row["agent"], con)
        if acct:
            found.setdefault(acct, []).append(row["agent"])
    return found


def is_running(row) -> bool:
    """Whether an agent's state row says it is running: the AGENTS panel's
    RUNNING, and not a closed session that still carries the state."""
    return bool(row) and row["state"] == "RUNNING" and not row["closed_at"]


def roster(con) -> tuple[dict[str, tuple[int, int]], int]:
    """(assigned, running) per account, and how many agents could not be read.

    An agent belongs to the account its session is on; one that has never run
    is on none. Assigned is every agent there, running or not; running is those
    of them `is_running`. An agent whose config cannot be read is drawn as a
    config error and counted on no account: it is returned as a count of its
    own, every figure is then a floor, and the caller says so."""
    found: dict[str, list[int]] = {}
    unplaced = 0
    for name in paths.list_agents():
        try:
            agents.read(name)
        except Exception:  # noqa: BLE001 - the AGENTS panel draws it as a config error
            unplaced += 1
            continue
        acct = agents.current_account(name, con)
        if not acct:
            continue
        counts = found.setdefault(acct, [0, 0])
        counts[0] += 1
        counts[1] += is_running(db.get_agent_state(con, name))
    return {a: (c[0], c[1]) for a, c in found.items()}, unplaced


def first_turn_agents(con) -> set[str]:
    """Agents whose current mid-turn stretch is their very first since the
    SessionStart or moved_account that began it - the turn `measure.py` already
    prices as a cold start (about 13% of the window, `triggers.MIN_STRETCH_S`'s
    own figure), so a burn sample must not price it again as steady work."""
    rows = con.execute(
        "SELECT DISTINCT e1.agent FROM events e1 "
        "WHERE e1.kind IN ('SessionStart', 'moved_account') "
        "AND e1.id = (SELECT MAX(e2.id) FROM events e2 WHERE e2.agent = e1.agent "
        "AND e2.kind IN ('SessionStart', 'moved_account')) "
        "AND NOT EXISTS (SELECT 1 FROM events e3 WHERE e3.agent = e1.agent "
        "AND e3.kind = 'Stop' AND e3.id > e1.id)").fetchall()
    return {row["agent"] for row in rows}


def active_agents(con, exclude: str | None = None) -> dict[str, int]:
    """How many agents are mid-turn on each account. `exclude` leaves one agent
    out, for asking what is on an account besides it."""
    return {a: n for a, n in
            ((a, len([x for x in names if x != exclude]))
             for a, names in active_by_account(con).items()) if n}


def costs(con=None) -> "burnrate.Costs":
    """What one more agent costs, the same estimate for every account, and the
    plan's week-per-5h ratios; `read_all` puts one object on each `Load`."""
    own = con is None
    con = db.connect() if own else con
    try:
        return burnrate.costs(con)
    finally:
        if own:
            con.close()


def read_all(con=None) -> dict[str, Load]:
    own = con is None
    con = db.connect() if own else con
    try:
        active = active_agents(con)
        shared_costs = costs(con)
    finally:
        if own:
            con.close()
    cfg = _config()
    return {a: Load(a, active.get(a, 0), cap_of(a, cfg), status.read_burn(a), shared_costs)
            for a in paths.list_accounts()}
