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
from ..infra.yamlcompat import dump
from ..usage import load

# A `park_timeout_s` of 0 - global or per-agent - is the explicit "never
# close this window" override (T52): a real answer, not an absent key, so it
# is read as itself rather than falling through to the next default.
NEVER = 0


# The one reader of config.yaml, and the one guarded read for a write.
_config = load._config


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
    cfg = load.config_for_write()
    cfg[key] = value
    paths.config_file().write_text(dump(cfg))


def _agent_cfg(agent: str) -> dict:
    """The agent's own config, or {} for one that cannot be read - closing
    must not stall on a bad agent.yaml; it just follows the global setting."""
    try:
        return agents.read(agent) or {}
    except Exception:  # noqa: BLE001
        return {}


def default_close_timeout_s() -> int:
    """The park timeout every agent without its own override follows."""
    v = _config().get("park_timeout_s")
    if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
        return v
    return IDLE_CLOSE_S


def close_timeout_s(agent: str) -> int:
    """Seconds this agent may sit idle before the daemon closes it; NEVER (0)
    means it never does. The agent's own `park_timeout_s` wins when it is
    set, then config.yaml's global one, then IDLE_CLOSE_S's measured default
    - today's behaviour, unchanged until either is set."""
    v = _agent_cfg(agent).get("park_timeout_s")
    if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
        return v
    return default_close_timeout_s()


# T424: an agent still running background work when its idle window is over is told to
# finish or stop it, and is stopped this long after that if it shows nothing more.
BG_GRACE_S = 300


def _seconds_setting(agent: str, key: str, default: int) -> int:
    """A positive number of seconds: the agent's own `key` in agent.yaml wins, then
    config.yaml's, then `default` - the shape `close_timeout_s` reads its own in."""
    for v in (_agent_cfg(agent).get(key), _config().get(key)):
        if isinstance(v, int) and not isinstance(v, bool) and v > 0:
            return v
    return default


def bg_notice_s(agent: str) -> int:
    """How long an agent with background work may show nothing before it is told to finish
    or stop it: its idle-close window unless `bg_notice_s` says otherwise."""
    return _seconds_setting(agent, "bg_notice_s", close_timeout_s(agent))


def bg_grace_s(agent: str) -> int:
    """How long after that notice its work is stopped if it still shows nothing."""
    return _seconds_setting(agent, "bg_grace_s", BG_GRACE_S)


def set_bg_seconds(key: str, seconds: int | None, agent: str | None = None) -> None:
    """One of the two background times, keeping every other setting: the global one, or
    with `agent` that agent's own (None drops it, back to the global one)."""
    if key not in ("bg_notice_s", "bg_grace_s") or (seconds is not None and seconds <= 0):
        raise ValueError("a background time is a positive number of seconds")
    if agent:
        cfg = agents.read(agent)
        if seconds is None:
            cfg.pop(key, None)
        else:
            cfg[key] = seconds
        agents.write(agent, cfg)
        return
    if seconds is None:
        raise ValueError("--clear only applies with --agent")
    cfg = load.config_for_write()
    cfg[key] = seconds
    paths.config_file().write_text(dump(cfg))


def set_close_timeout(seconds: int) -> None:
    """The global park timeout, keeping every other setting."""
    if seconds < 0:
        raise ValueError("a timeout is at least 0 (0 = never close)")
    cfg = load.config_for_write()
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
    agents.write(agent, cfg)


def default_ignore_cache_expiry() -> bool:
    """Whether a resume may ignore the prompt cache's expiry when nothing
    agent-specific says otherwise. Off (today's behaviour) by default."""
    return _config().get("ignore_cache_expiry") is True


def ignore_cache_expiry(agent: str) -> bool:
    """Whether this agent's resume may reuse its session past RESUME_WARM_S
    instead of starting fresh. The agent's own `ignore_cache_expiry` wins
    when it is set, then config.yaml's global one, off otherwise."""
    v = _agent_cfg(agent).get("ignore_cache_expiry")
    if isinstance(v, bool):
        return v
    return default_ignore_cache_expiry()


def set_ignore_cache_expiry(on: bool) -> None:
    """The global switch, keeping every other setting."""
    cfg = load.config_for_write()
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
    agents.write(agent, cfg)
