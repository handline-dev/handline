"""settings.set and settings.trust: the founder's timings and switches, and the trust of worktree folders for an account.
settings.set serves the Settings screen and `hl dev-mode`, `hl idle` and `hl agent set` for the keys they write (an agent's
own `hl agent set` stays agent.set); settings.trust is the legacy `trust` verb, answering claude's folder-trust screen once
per worktree in a throwaway session.

settings.set writes every key of `changes` in one step: config.yaml, or with `agent` that agent's agent.yaml, is read and
written once, so nothing is written if any key is refused. A key set to null is removed (back to the default). The keys are
config.yaml's own names, read where they are today (session/idle_close.py and the modules that name them). token_prices
is refused with a value, for config.yaml writes no nested value, and null removes it. settings.trust
trusts only the worktrees an agent's record names under a declared root; any other is refused before a session starts. It
takes as long as the sessions do, so it is served on a thread of its own. Both write what a retry writes again or record their
result once done, so a retry after a lost reply answers and repeats nothing."""
from __future__ import annotations

from .. import db
from ..actions import details
from ..infra import paths
from ..infra.yamlcompat import ConfigError
from ..team import agents
from ..usage import load
from . import rpc

INT1, INT0, FLAG = "int>=1", "int>=0", "bool"
KEYS = {"active_cap": INT1, "park_timeout_s": INT0, "handoff_note": FLAG, "auto_accept_dialogs": FLAG, "ignore_cache_expiry": FLAG,
        "dev_mode": FLAG, "bg_notice_s": INT1, "bg_grace_s": INT1, "bg_check_s": INT1, "notice_digest_s": INT1,
        "unasked_running_s": INT1, "stop_wait_backstop_s": INT1, "stale_background_s": INT1, "input_hold_idle_s": INT1}
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
        db.record_effect(con, ctx.caller.actor, params["request_id"], "settings.set", result)
    finally:
        con.close()
    return result


def _trust(ctx, params) -> dict:
    acct, given = params.get("account"), params.get("worktrees")
    if not isinstance(acct, str) or not acct:
        raise rpc.bad_request("account is a name", "account", rpc.INVALID_PARAMS)
    if acct not in paths.list_accounts():
        raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no such account: {acct}", what="account", name=acct)
    if not isinstance(given, list) or not given or not all(isinstance(w, dict) and isinstance(w.get("agent"), str)
                                                          and isinstance(w.get("path"), str) for w in given):
        raise rpc.bad_request("worktrees is a list of {agent, path}", "worktrees", rpc.INVALID_PARAMS)
    declared, _ = details.eligible_worktrees()
    for w in given:
        if (w["agent"], w["path"]) not in declared:
            raise rpc.refused(f"{w['path']} is not {w['agent']}'s worktree under a declared root", gate="state")
    ok, said = details.trust_worktrees(acct, [(w["agent"], w["path"]) for w in given])
    if not ok:
        raise rpc.refused(said)
    result = {"accepted": True, "said": said}
    con = db.connect()
    try:
        db.record_effect(con, ctx.caller.actor, params["request_id"], "settings.trust", result)
    finally:
        con.close()
    return result


rpc.METHODS["settings.set"] = rpc.Method(_set, "founder", write=True)
rpc.METHODS["settings.trust"] = rpc.Method(_trust, "founder", write=True, waits=True)
