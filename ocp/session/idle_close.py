"""How long an idle agent's session may sit before the daemon closes it
(design section 11-b), and whether a resume may reuse one past its prompt
cache's expiry instead of starting fresh (`daemon.lifecycle.cache_expired`) -
config.yaml's global default for every agent, and agent.yaml's own override
for the one agent that should behave differently, e.g. never close its window.

Not `load.py`: that module is what an *account* can take, keyed by account
name. This is what one *agent's* idle window looks like, and the override
that matters is the agent that never wants to be closed, not the account it
happens to be running on.
"""
from __future__ import annotations

from ..team import agents
from ..infra import paths
from ..daemon.states import IDLE_CLOSE_S
from ..infra.yamlcompat import ConfigError, dump, load as load_yaml

# A `park_timeout_s` of 0 - global or per-agent - is the explicit "never
# close this window" override (T52): a real answer, not an absent key, so it
# is read as itself rather than falling through to the next default.
NEVER = 0


def _config() -> dict:
    try:
        data = load_yaml(paths.config_file().read_text())
    except (OSError, ConfigError):
        return {}
    return data if isinstance(data, dict) else {}


def config_seconds(key: str, default: int) -> int:
    """A positive number of seconds from config.yaml, or `default` when it is
    absent, not an integer, or not above zero."""
    v = _config().get(key)
    return v if isinstance(v, int) and not isinstance(v, bool) and v > 0 else default


def config_flag(key: str, default: bool) -> bool:
    """A true/false setting from config.yaml, or `default` when it is absent
    or not one."""
    v = _config().get(key)
    return v if isinstance(v, bool) else default


def set_config_flag(key: str, value: bool) -> None:
    """One global setting, keeping every other."""
    cfg = _config()
    cfg[key] = value
    paths.config_file().write_text(dump(cfg))


def _agent_cfg(agent: str) -> dict:
    """The agent's own config, or {} for one that cannot be read - closing
    must not stall on a bad agent.yaml; it just follows the global setting."""
    try:
        return agents.read(agent) or {}
    except Exception:  # noqa: BLE001
        return {}


def default_close_timeout_s(cfg: dict | None = None) -> int:
    """The park timeout every agent without its own override follows."""
    v = (_config() if cfg is None else cfg).get("park_timeout_s")
    if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
        return v
    return IDLE_CLOSE_S


def close_timeout_s(agent: str, cfg: dict | None = None) -> int:
    """Seconds this agent may sit idle before the daemon closes it; NEVER (0)
    means it never does. The agent's own `park_timeout_s` wins when it is
    set, then config.yaml's global one, then IDLE_CLOSE_S's measured default
    - today's behaviour, unchanged until either is set."""
    v = _agent_cfg(agent).get("park_timeout_s")
    if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
        return v
    return default_close_timeout_s(cfg)


def set_close_timeout(seconds: int) -> None:
    """The global park timeout, keeping every other setting."""
    if seconds < 0:
        raise ValueError("a timeout is at least 0 (0 = never close)")
    cfg = _config()
    cfg["park_timeout_s"] = seconds
    paths.config_file().write_text(dump(cfg))


def set_agent_close_timeout(agent: str, seconds: int | None) -> None:
    """This agent's own park timeout; None drops the override and goes back
    to the global one. Raises if the agent is not registered or its
    agent.yaml cannot be read - unlike the read side, a set is a human
    action and its failure should say so, not fall through quietly."""
    if seconds is not None and seconds < 0:
        raise ValueError("a timeout is at least 0 (0 = never close)")
    cfg = agents.read(agent)
    if seconds is None:
        cfg.pop("park_timeout_s", None)
    else:
        cfg["park_timeout_s"] = seconds
    agents.agent_file(agent).write_text(dump(cfg))


def default_ignore_cache_expiry(cfg: dict | None = None) -> bool:
    """Whether a resume may ignore the prompt cache's expiry when nothing
    agent-specific says otherwise. Off (today's behaviour) by default."""
    return (_config() if cfg is None else cfg).get("ignore_cache_expiry") is True


def ignore_cache_expiry(agent: str, cfg: dict | None = None) -> bool:
    """Whether this agent's resume may reuse its session past RESUME_WARM_S
    instead of starting fresh. The agent's own `ignore_cache_expiry` wins
    when it is set, then config.yaml's global one, off otherwise."""
    v = _agent_cfg(agent).get("ignore_cache_expiry")
    if isinstance(v, bool):
        return v
    return default_ignore_cache_expiry(cfg)


def set_ignore_cache_expiry(on: bool) -> None:
    """The global switch, keeping every other setting."""
    cfg = _config()
    if on:
        cfg["ignore_cache_expiry"] = True
    else:
        cfg.pop("ignore_cache_expiry", None)
    paths.config_file().write_text(dump(cfg))


def set_agent_ignore_cache_expiry(agent: str, on: bool | None) -> None:
    """This agent's own switch; None drops the override and goes back to the
    global one. Raises if the agent is not registered or unreadable."""
    cfg = agents.read(agent)
    if on is None:
        cfg.pop("ignore_cache_expiry", None)
    else:
        cfg["ignore_cache_expiry"] = on
    agents.agent_file(agent).write_text(dump(cfg))
