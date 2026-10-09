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
from ..session import blocked as blockedmod, idle_close, launch, term, triggers
from ..team import agents
from ..usage import load as loadmod, status
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


def screen_word(view: dict) -> tuple[str, str]:
    """A view's state word and detail as the screens draw them (RUNNING, IDLE, PENDING, MOVING, DIALOG, FATAL): the way back from the spec's
    lower-case words, which are what the view holds."""
    state, detail = view["state"], view.get("state_detail", "")
    if state == "parked" and detail == "moving":
        return "MOVING", ""
    return {"running": "RUNNING", "queued": "PENDING", "parked": "PENDING", "waiting": "DIALOG", "stalled": "FATAL"}.get(state, "IDLE"), detail


_READ = object()


def _stuck_by_agent(con) -> dict[str, dict]:
    """`actions.stuck`'s rows by the agent each is about, the first of each. It asks Orca and the process table what runs, at most every
    `WORLD_FRESH_S` (stuck.py caches that), so it is read once for every agent, never once per agent."""
    out: dict[str, dict] = {}
    for b in actions.stuck(con):
        if b["agent"]:
            out.setdefault(b["agent"], b)
    return out


def _attention(con, name: str, row, word: str, shown, wall: str | None, stuck: dict) -> str | None:
    """What this agent alone asks of the founder, the most urgent of ask, alert, stuck and running: the screen's fold_urgency for one agent,
    without the fold, which is the screen's."""
    if con.execute("SELECT 1 FROM requests WHERE requester = ? AND target = ? AND state = 'queued'", (name, actions.HUMAN)).fetchone():
        return "ask"
    if word in ("DIALOG", "FATAL") or shown:
        return "alert"
    if name in stuck or wall:
        return "stuck"
    return "running" if loadmod.is_running(row) else None


def _fire(row, cfg: dict, st) -> str | None:
    """The trigger the daemon would hand this agent off for at its next tick, as `supervise` evaluates it (both windows and the context);
    None when its triggers do not apply or none has fired. The stored view is rewritten every tick, so this is as fresh as the tick."""
    if not cfg or not lifecycle.triggers_apply(row):
        return None
    t = triggers.evaluate(st.short_trusted, st.trusted_ctx_used, long_pct=st.long_trusted,
                          ctx_limit=status.effective_context_limit(cfg, st.model_id))
    return t.kind if t.fired else None


def _wall(con, name: str, row, would_wake: bool) -> dict | None:
    """What holds back a wake that is due, the way the Rail's wall cell draws it: the account that would wake the agent, when its
    window rolls over (unix seconds; None when unknown or when the wait is for a slot, not a window), and whether the account is a guess."""
    w = launch.wake_wait(name, row, con) if would_wake else None
    if not w:
        return None
    return {"account": w.account, "clock": None if w.full or not w.reset else int(w.reset), "assumed": bool(w.assumed)}


def compute(con, name: str, now: int | None = None, table=_READ, counts=_READ, stuck=_READ) -> dict:
    """`table`, `counts` (open tasks per owner) and `stuck` (the delivery findings by agent) are what the caller has read already (once for
    every agent)."""
    now = int(time.time()) if now is None else now
    if table is _READ:
        table = actions.routing()
    if counts is _READ:
        counts = actions.open_task_counts(con, name)
    if stuck is _READ:
        stuck = _stuck_by_agent(con)
    row = db.get_agent_state(con, name)
    word, detail = screen_state(con, row, now)
    wire, detail = wire_state(con, row, word, detail)
    config_error = None
    try:
        cfg = agents.read(name)
    except (OSError, ConfigError) as e:
        cfg, config_error = {}, oneline(str(e))
    _, model, effort = agents.parts(cfg) if cfg else (None, None, None)
    block = db.block_get(con, name)
    shown = block if block and now >= block["show_at"] else None
    left = handoff_note.cache_left(con, name, now)
    task = db.current_task(con, name)
    task_row = db.get_task(con, task) if task is not None else None
    account = agents.current_account(name, con)
    st = status.read_for_agent(name, account) if account else None
    wall_reason = delivery.wall_reason(con, name, row)
    would_wake = bool(lifecycle.would_wake(con, row))
    ready = (task_row is not None and word == "IDLE" and actions.task_state(con, task_row) == "queued"
             and not db.task_blockers(con, task))
    view = {
        "name": name,
        "role": _role_line(name),
        "model": model,
        "effort": effort,
        "state": wire,
        "state_detail": detail,
        "state_since": _ts(row["updated"]) if row else None,
        "block": {"kind": shown["kind"], "since": _ts(shown["since"])} if shown else None,
        "wall_reason": wall_reason,
        "background_since": _ts(delivery.bg_since(con, name)),
        "would_wake": would_wake,
        "blocker": oneline(blockedmod.headline(shown["kind"], shown["detail"]))[:DETAIL_CAP] if shown else None,
        "account": account,
        "task": {"id": task, "title": task_row["title"]} if task_row else None,
        "cache_until": None if left is None else _ts(now + left),
        "ctx_pct": st.trusted_ctx_pct if st else None,
        "has_dialog": word == "DIALOG",
        "parent": actions.supervisor_of(table, name) if table else None,
        "task_count": counts.get(name, 0),
        "ready": ready,
        "wall": _wall(con, name, row, would_wake),
        "fire": _fire(row, cfg, st) if st else None,
        "attention": _attention(con, name, row, word, shown, wall_reason, stuck),
        "cache_ignored": idle_close.ignore_cache_expiry(name),
        "config_error": config_error,
    }
    if config_error is None:
        view["kind"] = agents.kind(cfg)
    return view


def refresh(con, now: int | None = None, log=lambda m: None) -> None:
    """Store a fresh view for every registered agent, writing only what changed, and drop a gone agent's.
    The view is a read model: an agent whose view cannot be worked out keeps its last one, and the others go on."""
    names = paths.list_agents()
    driven = agents.driven()  # a structured agent's row is the Go daemon's: not written, and not dropped while the agent exists
    kept = {r["agent"]: r["view"] for r in con.execute("SELECT agent, view FROM agent_view")}
    table, counts, stuck = actions.routing(), actions.open_task_counts(con), _stuck_by_agent(con)
    for name in driven:
        try:
            view = json.dumps(compute(con, name, now, table, counts, stuck), sort_keys=True)
        except Exception as e:  # noqa: BLE001 - one agent's failure must not stale every agent after it
            log(f"  agent view: {name}: {type(e).__name__}: {e}")
            continue
        if kept.get(name) != view:
            con.execute("INSERT INTO agent_view(agent, view, updated) VALUES(?, ?, ?) "
                        "ON CONFLICT(agent) DO UPDATE SET view = excluded.view, updated = excluded.updated",
                        (name, view, int(time.time()) if now is None else now))
    for gone in set(kept) - set(names):
        con.execute("DELETE FROM agent_view WHERE agent = ?", (gone,))


def refresh_one(con, name: str, log=lambda m: None) -> None:
    """Store a fresh view for one agent now, for a caller that has just changed what it is made of (the sweep recording a gone session) and
    would otherwise leave the stored view a whole tick behind. A view that cannot be worked out keeps the last one.
    A structured agent's row is the Go daemon's and is left alone."""
    if agents.is_structured(name):
        return
    try:
        view = json.dumps(compute(con, name, None, actions.routing()), sort_keys=True)
    except Exception as e:  # noqa: BLE001 - the tick's refresh tries again
        log(f"  agent view: {name}: {type(e).__name__}: {e}")
        return
    con.execute("INSERT INTO agent_view(agent, view, updated) VALUES(?, ?, ?) "
                "ON CONFLICT(agent) DO UPDATE SET view = excluded.view, updated = excluded.updated WHERE view != excluded.view",
                (name, view, int(time.time())))


def stored(con, name: str) -> dict | None:
    row = con.execute("SELECT view FROM agent_view WHERE agent = ?", (name,)).fetchone()
    return json.loads(row["view"]) if row else None


def stored_all(con) -> list[dict]:
    return [json.loads(r["view"]) for r in con.execute("SELECT view FROM agent_view ORDER BY agent")]
