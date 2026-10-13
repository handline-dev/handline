"""settings.set: the founder's timings and switches. It serves the Settings screen and `hl dev-mode`, `hl idle` and `hl agent set` for
the keys they write (an agent's own `hl agent set` stays agent.set).

settings.set writes every key of `changes` in one step: config.yaml, or with `agent` that agent's agent.yaml, is read and
written once, so nothing is written if any key is refused. A key set to null is removed (back to the default). The keys are
config.yaml's own names, read where they are today (session/idle_close.py and the modules that name them). token_prices
is refused with a value, for config.yaml writes no nested value, and null removes it. It writes what a retry writes again, so a
retry after a lost reply repeats nothing."""
from __future__ import annotations

from .. import db
from ..infra import paths
from ..infra.yamlcompat import ConfigError
from ..team import agents
from ..usage import load
from . import rpc

INT1, INT0, FLAG = "int>=1", "int>=0", "bool"
KEYS = {"active_cap": INT1, "park_timeout_s": INT0, "handoff_note": FLAG, "auto_accept_dialogs": FLAG, "ignore_cache_expiry": FLAG,
        "dev_mode": FLAG, "bg_notice_s": INT1, "bg_grace_s": INT1, "bg_check_s": INT1, "notice_digest_s": INT1,
        "stop_wait_backstop_s": INT1, "input_hold_idle_s": INT1}
PER_AGENT = ("park_timeout_s", "ignore_cache_expiry", "bg_notice_s", "bg_grace_s", "bg_check_s", "notice_digest_s")


def _value(key: str, v):
    """`v` if it is what `key` takes; bad_request naming the key otherwise. None is not checked: it removes the key."""
    kind = KEYS[key]
    ok = (isinstance(v, int) and not isinstance(v, bool) and v >= (0 if kind == INT0 else 1) if kind in (INT1, INT0)
          else isinstance(v, bool))
    if not ok:
        what = {INT1: "a whole number, at least 1", INT0: "a whole number, at least 0", FLAG: "true or false"}[kind]
        raise rpc.bad_request(f"{key} is {what}, or null", key, rpc.INVALID_PARAMS)
    return v


def _set(ctx, params) -> dict:
    changes, agent = params.get("changes"), params.get("agent")
    if not isinstance(changes, dict) or not changes:
        raise rpc.bad_request("changes is an object of settings", "changes", rpc.INVALID_PARAMS)
    for key, v in changes.items():
        if key == "token_prices" and v is not None:
            raise rpc.bad_request("token_prices is not served yet: config.yaml holds no nested value", key, rpc.INVALID_PARAMS)
        if key not in KEYS and key != "token_prices":
            raise rpc.bad_request(f"{key} is not a setting", "changes", rpc.INVALID_PARAMS)
        if v is not None:
            _value(key, v)
        if agent is not None and key not in PER_AGENT:
            raise rpc.bad_request(f"{key} has no per-agent value", "changes", rpc.INVALID_PARAMS)
    if agent is not None:
        if not isinstance(agent, str) or not agent:
            raise rpc.bad_request("agent is an agent's name", "agent", rpc.INVALID_PARAMS)
        if agent not in paths.list_agents():
            raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no such agent: {agent}", what="agent", name=agent)
    try:
        cfg = agents.read(agent) if agent else load.config_for_write()
        for key, v in changes.items():
            if v is None:
                cfg.pop(key, None)
            else:
                cfg[key] = v
        if agent:
            agents.write(agent, cfg)
        else:
            load.write_config(cfg)
    except (ConfigError, OSError) as e:
        raise rpc.refused(str(e), gate="state") from None
    result = {"accepted": True, "said": ", ".join(f"{k} {'cleared' if v is None else 'set'}" for k, v in changes.items())
              + (f" for {agent}" if agent else "")}
    con = db.connect()
    try:
        db.record_effect(con, ctx.caller.writer, params["request_id"], "settings.set", result)
    finally:
        con.close()
    return result


rpc.METHODS["settings.set"] = rpc.Method(_set, "founder", write=True)
