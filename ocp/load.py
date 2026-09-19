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

from . import agents, db, paths, status, triggers
from .yamlcompat import ConfigError, dump, load as load_yaml


@dataclass
class Load:
    account: str
    active: int              # agents mid-turn on it
    cap: int
    burn: float | None       # percent of the 5h window per second, None unmeasured

    @property
    def at_cap(self) -> bool:
        return self.active >= self.cap


def _config() -> dict:
    try:
        data = load_yaml(paths.config_file().read_text())
    except (OSError, ConfigError):
        return {}
    return data if isinstance(data, dict) else {}


def cap_of(acct: str, cfg: dict | None = None) -> int:
    cfg = _config() if cfg is None else cfg
    for key in (f"active_cap_{acct}", "active_cap"):
        v = cfg.get(key)
        if isinstance(v, int) and not isinstance(v, bool) and v >= 1:
            return v
    return triggers.DEFAULT_ACTIVE_CAP


def set_cap(acct: str, n: int) -> None:
    """Write the cap for one account, keeping every other setting. A cap of
    less than one would be an account that can never take work, which is what
    removing it is for."""
    if n < 1:
        raise ValueError("a cap is at least 1")
    cfg = _config()
    cfg[f"active_cap_{acct}"] = n
    paths.config_file().write_text(dump(cfg))


def active_agents(con, exclude: str | None = None) -> dict[str, int]:
    """Agents mid-turn, by the account each is running on. An agent that has
    just moved is mid-turn on its handback before the daemon has seen it run,
    so it counts from the move until its next Stop; without that, agents that
    wall together all see the same empty destination. `exclude` leaves one
    agent out, for asking what is on an account besides it."""
    counts: dict[str, int] = {}
    rows = con.execute(
        "SELECT agent FROM agent_state WHERE closed_at IS NULL AND (state = 'RUNNING' OR "
        "(state = 'QUEUED' AND EXISTS (SELECT 1 FROM events m WHERE m.agent = agent_state.agent "
        "AND m.kind = 'moved_account' AND m.id > COALESCE((SELECT MAX(s.id) FROM events s "
        "WHERE s.agent = agent_state.agent AND s.kind = 'Stop'), 0))))").fetchall()
    for row in rows:
        if row["agent"] == exclude:
            continue
        try:
            acct = agents.current_account(row["agent"], con)
        except Exception:  # noqa: BLE001 - one agent's bad config is not the pick's problem
            continue
        counts[acct] = counts.get(acct, 0) + 1
    return counts


def read_all(con=None) -> dict[str, Load]:
    own = con is None
    con = db.connect() if own else con
    try:
        active = active_agents(con)
    finally:
        if own:
            con.close()
    cfg = _config()
    return {a: Load(a, active.get(a, 0), cap_of(a, cfg), status.read_burn(a))
            for a in paths.list_accounts()}
