"""accounts.list, team.get, schedules.list, stats.get and settings.get: what the Accounts, Team, HR, Schedules, Stats
and Settings screens draw, read by the daemon from the readers the commands use (usage, team, schedules, config.yaml); hr.list is `rpc_hr`'s.

They serve `hl status` and `hl account list` (accounts.list), `hl team` (team.get) and `hl schedule list`
(schedules.list); stats.get and settings.get have no command. A schedule's id is
`rpc_schedules.schedule_id`; its last_run is the newest `schedule_fired` event for its cron, and its next_run the first minute the
cron names after that (after now when it never fired). The settings the daemon keeps itself and the screens read:
`values` is every config.yaml key by name with the value in force, `timings` the seconds-valued ones, and `listener` what this
daemon has: none, the Go gateway being the network front door."""
from __future__ import annotations

import json
import re
import time

from .. import actions, db, vendors
from ..actions import details
from ..infra import cron, paths
from ..session import events as evmod, idle_close, term
from ..team import accounts, agents, team as teammod
from ..usage import cycle, load as loadmod, status
from . import agentview, devmode, delivery, inputhold, rpc, rpc_schedules, rpc_settings, rpc_status, timebox

_TS = agentview._ts
MOVES_SHOWN = 20
WINDOWS = {"24h": 24 * 3600, "7d": 7 * 24 * 3600}


def _window(w) -> dict | None:
    if w is None or w.pct is None:
        return None
    return {"used_pct": float(w.pct), "resets_at": _TS(w.reset), "source": "estimated" if w.carried else "measured"}


def _moves(con, acct: str) -> list[dict]:
    out = []
    for r in con.execute("SELECT ts, agent, payload FROM events WHERE kind = 'moved_account' ORDER BY id DESC LIMIT 500"):
        try:
            p = json.loads(r["payload"] or "{}")
        except ValueError:
            continue
        if acct in (p.get("from"), p.get("to")):
            out.append({"at": _TS(r["ts"]), "agent": r["agent"], "from": p.get("from"), "to": p.get("to") or "",
                        "reason": str(p.get("trigger") or "")})
            if len(out) == MOVES_SHOWN:
                break
    return out


def _login(acct: str) -> str:
    state = accounts.login_state(acct)
    return "unknown" if state is None else "ok" if state else "needs_login"


def _account(con, acct: str, names: list[str]) -> dict:
    st = status.read(acct)
    end = cycle.end_date(acct)
    return {"name": acct, "login_state": _login(acct), "five_hour": _window(st.short), "weekly": _window(st.long),
            "agents": [a for a in names if agents.current_account(a, con) == acct], "moves": _moves(con, acct),
            "label": loadmod.label_of(acct), "cap": loadmod.own_cap(acct), "cap_effective": loadmod.cap_of(acct),
            "recover": loadmod.usage_recover_enabled(acct),
            "cycle": {"on": cycle.enabled(acct), "end": end.isoformat() if end else None, "end_is_default": cycle.end_is_default(acct)}}


def _accounts(ctx, params) -> dict:
    con = db.connect()
    try:
        names = paths.list_agents()
        return {"accounts": [_account(con, a, names) for a in paths.list_accounts()]}
    finally:
        con.close()


def _file(path) -> dict:
    try:
        updated = _TS(path.stat().st_mtime)
    except OSError:
        updated = None
    return {"path": str(path), "updated": updated}


def _team(ctx, params) -> dict:
    try:
        table = teammod.Team.load()
    except (FileNotFoundError, ValueError):
        table = None
    out = []
    for name in paths.list_agents():
        d = paths.agent_dir(name)
        out.append({"name": name, "role": agentview._role_line(name), "parent": actions.supervisor_of(table, name) if table else None,
                    "role_file": _file(d / "ROLE.md"), "base_file": _file(d / "base.md")})
    edges = [{"from": str(e["from"]), "to": str(e["to"])} for e in (table.edges if table else [])]
    return {"agents": out, "edges": edges}


def _fired(con, agent: str, item: dict) -> int | None:
    for r in con.execute("SELECT ts, payload FROM events WHERE kind = 'schedule_fired' AND agent = ? ORDER BY id DESC LIMIT 200", (agent,)):
        try:
            if json.loads(r["payload"] or "{}").get("cron") == item["cron"]:
                return r["ts"]
        except ValueError:
            continue
    return None


def _schedules(ctx, params) -> dict:
    now, out = int(time.time()), []
    con = db.connect()
    try:
        for agent in paths.list_agents():
            for item in rpc_schedules.schedules.of(agent):
                last = _fired(con, agent, item)
                handled = db.get_cursor(con, f"schedule_at:{agent}:{rpc_schedules.schedules.sid(item)}")
                nxt = cron.next_after(cron.parse(item["cron"]), max(last or 0, handled or 0, now if not (last or handled) else 0))
                out.append({"id": rpc_schedules.schedule_id(agent, item), "target": agent, "cron": item["cron"], "message": item["message"],
                            "next_run": _TS(nxt), "last_run": _TS(last), "set_by": "human"})
    finally:
        con.close()
    return {"schedules": out}


def _worked(con, since: int, until: int) -> dict[str, int]:
    """Seconds each agent spent between a prompt and its stop, from the hook events in the window."""
    open_at: dict[str, int] = {}
    total: dict[str, int] = {}
    for r in con.execute("SELECT ts, agent, kind FROM events WHERE kind IN ('UserPromptSubmit', 'Stop', 'SessionEnd') AND ts <= ? ORDER BY id", (until,)):
        if r["kind"] == "UserPromptSubmit":
            open_at.setdefault(r["agent"], r["ts"])
        elif r["agent"] in open_at:
            start = open_at.pop(r["agent"])
            span = r["ts"] - max(start, since)
            if span > 0:
                total[r["agent"]] = total.get(r["agent"], 0) + span
    return total


def _count(con, kind: str, since: int, until: int) -> int:
    return con.execute("SELECT COUNT(*) FROM events WHERE kind = ? AND ts >= ? AND ts <= ?", (kind, since, until)).fetchone()[0]


def _stats(ctx, params) -> dict:
    window = params.get("window")
    if window not in WINDOWS:
        raise rpc.bad_request("window is 24h or 7d", "window", rpc.INVALID_PARAMS)
    until = int(time.time())
    since = until - WINDOWS[window]
    con = db.connect()
    try:
        done = con.execute("SELECT COUNT(*) FROM tasks WHERE done_at >= ? AND done_at <= ?", (since, until)).fetchone()[0]
        per = []
        for a in paths.list_accounts():
            st = status.read(a)
            per.append({"account": a, "five_hour_used_pct": None if st.short.pct is None else float(st.short.pct),
                        "weekly_used_pct": None if st.long.pct is None else float(st.long.pct)})
        return {"window": window, "since": _TS(since), "until": _TS(until), "tasks_done": done, "working_seconds": _worked(con, since, until),
                "moves": _count(con, "moved_account", since, until), "handoffs": _count(con, "task_handoff", since, until),
                "blocks": _count(con, "block_task", since, until), "per_account": per}
    finally:
        con.close()


def _values() -> dict:
    cfg = loadmod._config()
    sw = actions.global_switches()
    park = idle_close.default_close_timeout_s()
    sec = idle_close.config_seconds
    return {"active_cap": loadmod.default_cap(cfg), "park_timeout_s": park, "handoff_note": sw["handoff_note"],
            "auto_accept_dialogs": sw["auto_accept"], "ignore_cache_expiry": sw["ignore_cache_expiry"], "dev_mode": devmode.dev_mode_enabled(),
            "bg_notice_s": sec("bg_notice_s", park or 1), "bg_grace_s": sec("bg_grace_s", idle_close.BG_GRACE_S),
            "bg_check_s": sec("bg_check_s", idle_close.BG_CHECK_S), "notice_digest_s": sec("notice_digest_s", idle_close.NOTICE_DIGEST_S),
            "unasked_running_s": sec("unasked_running_s", timebox.UNASKED_RUNNING_S),
            "stop_wait_backstop_s": sec("stop_wait_backstop_s", timebox.STOP_WAIT_BACKSTOP_S),
            "stale_background_s": sec("stale_background_s", delivery.STALE_BG_S), "token_prices": dict(cfg.get("token_prices") or {}),
            "input_hold_idle_s": inputhold.idle_s()}


def _hooks_installed() -> bool:
    wanted = evmod.CAPTURED | evmod.ANSWERS | evmod.GUARDS
    try:
        return all(not h.missing(wanted) for h in vendors.state_hooks())
    except Exception:  # noqa: BLE001 - a settings file that cannot be read is "not installed", not a failed call
        return False


def _settings(ctx, params) -> dict:
    agent = params.get("agent")
    if agent is not None:
        if not isinstance(agent, str) or not agent:
            raise rpc.bad_request("agent is an agent's name", "agent", rpc.INVALID_PARAMS)
        if agent not in paths.list_agents():
            raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no such agent: {agent}", what="agent", name=agent)
    values = _values()
    declared, _ = details.eligible_worktrees()
    installed = _hooks_installed()
    names = paths.list_accounts()
    out = {"timings": {k: v for k, v in values.items() if k.endswith("_s")},
           "terminal_host": {"choice": term.host_name(), "available": [term.host_name()] if term.available() else [],
                             "health": rpc_status._host()["health"]},
           "listener": {"enabled": False, "bind": "loopback", "encryption": "none", "fingerprint": None},
           "trust_folders": [{"account": a, "agent": ag, "path": w} for a in names for ag, w in declared],
           "hooks": {"installed": installed, "accounts": [{"account": a, "installed": installed} for a in names]},
           "values": values}
    if agent is not None:
        cfg = agents.read(agent) or {}
        out["overrides"] = {k: cfg[k] for k in rpc_settings.PER_AGENT if k in cfg}
    return out


rpc.METHODS["accounts.list"] = rpc.Method(_accounts, "agent")
rpc.METHODS["team.get"] = rpc.Method(_team, "agent")
rpc.METHODS["schedules.list"] = rpc.Method(_schedules, "agent")
rpc.METHODS["stats.get"] = rpc.Method(_stats, "member")
rpc.METHODS["settings.get"] = rpc.Method(_settings, "founder")
