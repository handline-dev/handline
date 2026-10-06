"""The stored per-agent view: every field the Rail and Session screens show about an agent, decided here
once per tick from the functions the daemon already acts on, so a screen reads one row and recomputes
nothing (contract/protocol.md, `AgentView`). `state_detail` is the text the state column draws beside the
word (a clock, what a wait is for); the spec's view has no field for it yet."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from .. import actions, db
from ..infra import paths
from ..infra.textsafe import oneline
from ..infra.yamlcompat import ConfigError
from ..session import blocked as blockedmod, term
from ..team import agents
from . import delivery, handoff_note, lifecycle
from .screenstate import DETAIL_CAP, screen_state, wire_state


def _ts(epoch) -> str | None:
    return None if epoch is None else datetime.fromtimestamp(int(epoch), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _role_line(name: str) -> str:
    """The first sentence-line of the agent's ROLE.md under its heading, one line."""
    try:
        text = (paths.agent_dir(name) / "ROLE.md").read_text()
    except OSError:
        return ""
    body = next((ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")), "")
    return oneline(body)[:160]


def on_the_wire(view: dict) -> dict:
    """A stored view as the spec's AgentView: the screens' own word and detail (kept for them) become the lower-case state word and its
    detail the contract names (x-state-words), and nothing else of the view changes."""
    out = {k: v for k, v in view.items() if k != "wire"}
    out.update(view.get("wire") or _LEGACY.get(view["state"], {"state": "idle", "state_detail": view.get("state_detail", "")}))
    return out


# A view stored before it carried its wire words (the tick makes a new one at once): the word alone says what it can.
_LEGACY = {w: {"state": lower} for w, lower in (("RUNNING", "running"), ("IDLE", "idle"), ("PENDING", "queued"), ("MOVING", "parked"),
                                                ("DIALOG", "waiting"), ("FATAL", "stalled"))}


_READ = object()


def compute(con, name: str, now: int | None = None, table=_READ) -> dict:
    """`table` is the routing table when the caller has read it already (once for every agent)."""
    now = int(time.time()) if now is None else now
    if table is _READ:
        table = actions.routing()
    row = db.get_agent_state(con, name)
    word, detail = screen_state(con, row)
    wire, wire_detail = wire_state(con, row, word, detail)
    try:
        cfg = agents.read(name)
    except (OSError, ConfigError):
        cfg = {}
    _, model, effort = agents.parts(cfg) if cfg else (None, None, None)
    block = db.block_get(con, name)
    shown = block if block and now >= block["show_at"] else None
    left = handoff_note.cache_left(con, name, now)
    task = db.current_task(con, name)
    task_row = db.get_task(con, task) if task is not None else None
    return {
        "name": name,
        "role": _role_line(name),
        "model": model,
        "effort": effort,
        "state": word,
        "state_detail": detail,
        "state_since": _ts(row["updated"]) if row else None,
        "block": {"kind": shown["kind"], "since": _ts(shown["since"])} if shown else None,
        "wall_reason": delivery.wall_reason(con, name, row),
        "background_since": _ts(delivery.bg_since(con, name)),
        "would_wake": bool(lifecycle.would_wake(con, row)),
        "blocker": oneline(blockedmod.headline(shown["kind"], shown["detail"]))[:DETAIL_CAP] if shown else None,
        "account": agents.current_account(name, con),
        "task": {"id": task, "title": task_row["title"]} if task_row else None,
        "cache_until": None if left is None else _ts(now + left),
        "has_dialog": word == "DIALOG",
        "wire": {"state": wire, "state_detail": wire_detail},
        "parent": actions.supervisor_of(table, name) if table else None,
    }


def refresh(con, now: int | None = None, log=lambda m: None) -> None:
    """Store a fresh view for every registered agent, writing only what changed, and drop a gone agent's.
    The view is a read model: an agent whose view cannot be worked out keeps its last one, and the others go on."""
    names = paths.list_agents()
    kept = {r["agent"]: r["view"] for r in con.execute("SELECT agent, view FROM agent_view")}
    table = actions.routing()
    for name in names:
        try:
            view = json.dumps(compute(con, name, now, table), sort_keys=True)
        except Exception as e:  # noqa: BLE001 - one agent's failure must not stale every agent after it
            log(f"  agent view: {name}: {type(e).__name__}: {e}")
            continue
        if kept.get(name) != view:
            con.execute("INSERT INTO agent_view(agent, view, updated) VALUES(?, ?, ?) "
                        "ON CONFLICT(agent) DO UPDATE SET view = excluded.view, updated = excluded.updated",
                        (name, view, int(time.time()) if now is None else now))
    for gone in set(kept) - set(names):
        con.execute("DELETE FROM agent_view WHERE agent = ?", (gone,))


def stored(con, name: str) -> dict | None:
    row = con.execute("SELECT view FROM agent_view WHERE agent = ?", (name,)).fetchone()
    return json.loads(row["view"]) if row else None


def stored_all(con) -> list[dict]:
    return [json.loads(r["view"]) for r in con.execute("SELECT view FROM agent_view ORDER BY agent")]
