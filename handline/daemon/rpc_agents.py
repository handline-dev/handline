"""agents.list, agent.get and agent.timeline: the stored view of each agent, served as the spec defines it.
They serve `hl ps` (agents.list, agent.get) and the agent's chat page (agent.timeline)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .. import actions, db
from ..infra import gitrun, paths, textsafe
from ..infra.yamlcompat import ConfigError
from ..session import launch, promptparse
from ..team import agents
from . import agentview, inputhold, lifecycle, rpc, taskholds
from .screenstate import screen_state

DEFAULT_LIMIT, MAX_LIMIT = 50, 200
CAN_VERBS = ("terminal", "wake", "close", "restart", "park", "fold", "move")


def _named(con, params) -> str:
    name = params.get("agent")
    if not isinstance(name, str) or not name:
        raise rpc.bad_request("agent is a name", "agent", rpc.INVALID_PARAMS)
    if name not in paths.list_agents():
        raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no such agent: {name}", what="agent", name=name)
    return name


def _view(con, name: str) -> dict:
    """The stored view; one made now for an agent registered since the last tick."""
    return agentview.stored(con, name) or agentview.compute(con, name)


def _status_line(con, name: str) -> dict:
    """What `hl team` draws of an agent beyond its view, read now and not from the tick's stored view: the screen word,
    the requests not yet reached, the task holds it waits on, the wall on its wake, and whether it ever started."""
    row = db.get_agent_state(con, name)
    try:
        has_repo = bool(agents.read(name).get("repo"))
    except Exception:  # noqa: BLE001 - an unreadable agent.yaml is doctor's to report
        has_repo = False
    out = {"started": row is not None, "has_repo": has_repo, "has_terminal": bool(row and row["terminal"]), "screen_word": None,
           "screen_detail": "", "unreached": 0, "task_waits": [], "wake_wait_reason": None}
    if row is None:
        return out
    out["screen_word"], out["screen_detail"] = screen_state(con, row)
    out["unreached"] = db.unreached_count(con, name)
    out["task_waits"] = [{"task": tid, "words": why} for tid, why in taskholds.agent_waits(con, name)]
    if lifecycle.would_wake(con, row):
        out["wake_wait_reason"] = launch.wake_wait_reason(name, row, con) or None
    return out


def _agents_list(ctx, params) -> dict:
    status = params.get("status", False)
    if not isinstance(status, bool):
        raise rpc.bad_request("status is true or false", "status", rpc.INVALID_PARAMS)
    con = db.connect()
    try:
        have = {v["name"]: v for v in agentview.stored_all(con)}
        views = [have.get(n) or agentview.compute(con, n) for n in paths.list_agents()]
        return {"agents": [{**v, **_status_line(con, v["name"])} for v in views] if status else views}
    finally:
        con.close()


ESC = {"key": "esc", "label": "Cancel (Esc)", "answer": "other", "highlighted": False}


def options_of(block: dict) -> list[dict]:
    """The choices `prompt.answer` takes as `choice`, by what the screen read: yes, no and Esc for a permission prompt that
    a hook record pins (the others are answered in the terminal), the numbered options of a question or a dialog, and Esc."""
    detail = block.get("detail") or {}
    if block["kind"] == "permission":
        if not block.get("answerable"):
            return []
        return [{"key": "yes", "label": "Yes", "answer": "yes", "highlighted": False},
                {"key": "no", "label": "No", "answer": "no", "highlighted": False}, dict(ESC)]
    if block["kind"] == "question":
        return [{"key": str(i + 1), "label": o["label"], "answer": "other", "highlighted": bool(o.get("checked"))}
                for i, o in enumerate(detail.get("options") or [])] + [dict(ESC)]
    return [{"key": str(n), "label": label, "answer": "other", "highlighted": False}
            for n, label in detail.get("options") or []] + [dict(ESC)]


def prompt_of(block: dict) -> dict:
    """An open prompt as the spec's `Prompt`."""
    name = block["agent"]
    detail = block.get("detail") or {}
    kind = block["kind"]
    if kind == "dialog":
        kind = promptparse.dialog_kind(detail.get("options") or [])
    return {"id": block["fingerprint"], "agent": name, "kind": kind, "tool": detail.get("tool") or None,
            "command": detail.get("command") or None, "reason": detail.get("reason") or None,
            "lines": list(block["panel"]), "options": options_of(block),
            "since": agentview._ts(block.get("first_seen") or block["last_seen"]),
            "multi": bool(detail.get("multi_select"))}


def _prompt(con, name: str) -> dict | None:
    block = actions.open_block_of(con, name)
    return prompt_of(block) if block else None


def _can(con, name: str) -> dict:
    """Whether each verb of the chat page's menu is worth offering for this agent, and why not (`actions.can`)."""
    out = {}
    for verb in CAN_VERBS:
        ok, why = actions.can(con, name, verb)
        out[verb] = {"ok": ok, "why": why or None}
    return out


def _agent_get(ctx, params) -> dict:
    con = db.connect()
    try:
        name = _named(con, params)
        row = db.get_agent_state(con, name)
        path = row["worktree"] if row else None
        branch = None
        if path:
            rc, out, _ = gitrun.run(path, "rev-parse", "--abbrev-ref", "HEAD")
            branch = textsafe.oneline(out) if rc == 0 else None
        try:
            account = launch.find_room(name)
        except (OSError, ConfigError):      # an unreadable configuration is a finding of the view (config_error), not a failed read
            account = None
        return {**_view(con, name),
                "worktree": {"path": path, "branch": branch or ""} if path else None,
                "terminal": row["terminal"] if row else None,
                "session_id": row["resume_session"] if row else None,
                "session_open": bool(row and row["terminal"] and not row["closed_at"]),
                "input_holders": inputhold.view(con, name),
                "account_pick": {"account": account,
                                 "reason": "the account a session start would be placed on" if account
                                 else "no account can take work, or the configuration cannot be read"},
                "prompt": _prompt(con, name),
                "cwd": actions.cwd_for(con, name),
                "can": _can(con, name)}
    finally:
        con.close()


def _entry(row) -> dict:
    payload = json.loads(row["payload"]) if row["payload"] and row["payload"][0] == "{" else {}
    actor = payload.get("actor")
    return {"id": row["id"], "at": agentview._ts(row["ts"]), "kind": row["kind"],
            "actor": actor if isinstance(actor, dict) else None,
            "text": str(payload.get("text") or payload.get("detail") or payload.get("method") or row["kind"]),
            "data": payload}


def _agent_timeline(ctx, params) -> dict:
    limit = params.get("limit", DEFAULT_LIMIT)
    before = params.get("before")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise rpc.bad_request("limit is a positive integer", "limit", rpc.INVALID_PARAMS)
    if before is not None and (isinstance(before, bool) or not isinstance(before, int)):
        raise rpc.bad_request("before is an event id", "before", rpc.INVALID_PARAMS)
    limit = min(limit, MAX_LIMIT)
    con = db.connect()
    try:
        name = _named(con, params)
        rows = con.execute("SELECT * FROM events WHERE agent = ? AND (? IS NULL OR id < ?) ORDER BY id DESC LIMIT ?",
                           (name, before, before, limit + 1)).fetchall()
        return {"items": [_entry(r) for r in rows[:limit]], "more": len(rows) > limit}
    finally:
        con.close()


rpc.METHODS["agents.list"] = rpc.Method(_agents_list, "agent")
rpc.METHODS["agent.get"] = rpc.Method(_agent_get, "agent")
rpc.METHODS["agent.timeline"] = rpc.Method(_agent_timeline, "member")
