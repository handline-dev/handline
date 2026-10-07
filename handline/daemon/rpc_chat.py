"""chat.messages, chat.transcript, chat.locate and chat.search: what an agent's chat page draws, read by the daemon.

They wrap what the page reads today (`actions/chatreads.py`: the requests between the person and an agent, or all of an agent's with
scope `all`). A message is one request, newest first in a page; `before` reads older ones, `since` newer. The transcript is the agent's
side of the same chat as ordered items: its replies (the requests it sent the person and the verdicts it answered), the prompts it
opened (`prompt_blocks`) and the events of the tasks it owns; there is no tool item, for nothing records tool activity. An item's id is
its time and its source's row number, so it never changes; items that arrive in a second a client has read up to may sort before the last one it has, so `since` reads from that second and a client drops the ids it has. A request that no chat draws (withdrawn, never
reached its target) is not found.

The chat topic's detector is in stream.py; `message_of`, `item_of` and `working_of` are what it sends."""
from __future__ import annotations

import json

from .. import actions, db
from ..actions import chatreads, prompt_blocks
from ..infra import paths, textsafe
from . import agentview, landing, rpc, rpc_agents

DEFAULT_LIMIT, MAX_LIMIT = 50, 200
SEARCH_LIMIT, SEARCH_MAX = 20, 100
_TS = agentview._ts
_ASKS = ("say",)        # a message of this kind expects no answer: delivered is the end of it


def _agent(con, params, required: bool = True) -> str | None:
    name = params.get("agent")
    if name is None and not required:
        return None
    if not isinstance(name, str) or not name:
        raise rpc.bad_request("agent is an agent's name", "agent", rpc.INVALID_PARAMS)
    if name not in paths.list_agents():
        raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no such agent: {name}", what="agent", name=name)
    return name


def _scope(params, required: bool = True) -> bool | None:
    """Whether the chat is the all-chat."""
    scope = params.get("scope")
    if scope is None and not required:
        return None
    if scope not in ("mine", "all"):
        raise rpc.bad_request("scope is mine or all", "scope", rpc.INVALID_PARAMS)
    return scope == "all"


def _int(params, key: str, lo: int | None = None, hi: int | None = None) -> int | None:
    v = params.get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or (lo is not None and v < lo) or (hi is not None and v > hi):
        raise rpc.bad_request(f"{key} is an integer" + (f" from {lo} to {hi}" if hi is not None else ""), key, rpc.INVALID_PARAMS)
    return v


def _landing(r: dict) -> str:
    if r["state"] in db.NEVER_REACHED_TARGET:
        return "withdrawn"
    if db.dismissal(r)[0]:
        return "dismissed"
    if r["answer"] is not None:
        return "answered"
    state = landing.state(r)
    return "delivered_unanswered" if state == "delivered" and (r["kind"] or "say") not in _ASKS else state


def message_of(con, r: dict) -> dict:
    """One request of a chat page (`chatreads.requests_page`) as the spec's ChatMessage."""
    state = _landing(r)
    shown = db.shown_verdict(r)
    tasks = [{"id": int(t), "title": (db.get_task(con, int(t)) or {"title": ""})["title"]}
             for t in sorted((r["tasks"] or "").split(","), key=lambda x: int(x) if x else 0) if t]
    why = None
    if state in ("queued", "typed") and r["target"] != db.HUMAN:
        why = actions.queue_reason(con, r["target"], r["id"]) or None
    return {"id": r["id"], "at": _TS(r["ts"]), "state_at": _TS(r["state_at"] or r["ts"]), "from": r["requester"], "to": r["target"],
            "state": r["state"], "landing": state, "sending_reason": why, "body": r["body"] or "",
            "answer": {"id": r["answer_id"], "text": shown or "", "at": _TS(db.answered_at(r))} if r["answer_id"] and shown else None,
            "dismissed": bool(db.dismissal(r)[0]), "delivered_at": _TS(r["delivered_at"]), "typed_at": _TS(r["typed_at"]),
            "landed_at": _TS(r["landed_at"]), "parent": r["parent_id"], "kind": r["kind"] or "", "tasks": tasks,
            "is_task_question": bool(r["task_question"])}


def _messages(ctx, params) -> dict:
    everyone = _scope(params)
    before, since, limit = _int(params, "before"), _int(params, "since"), _int(params, "limit", 1, MAX_LIMIT) or DEFAULT_LIMIT
    if before is not None and since is not None:
        raise rpc.bad_request("before and since are one or the other", "since", rpc.INVALID_PARAMS)
    con = db.connect()
    try:
        agent = _agent(con, params)
        if since is not None:
            rows, more = chatreads.requests_page(con, agent, everyone, since=since + 1)
        else:
            rows, more = chatreads.requests_page(con, agent, everyone, before=before, limit=limit)
        return {"messages": [message_of(con, r) for r in reversed(rows[-limit:] if since is not None else rows)], "more": more}
    finally:
        con.close()


# What a task event moves a task to; `doing` is not stored, so the two events that put a task in its owner's hands stand for it.
_STATE_AFTER = {"draft_task": "draft", "queue_task": "queued", "task_begin": "doing", "task_start": "doing", "mark_task_done": "done",
                "close_task": "closed", "discard_task": "discarded", "reopen_task": "queued"}
PAGE = 10


def _edit_notes(payload: dict) -> list[str]:
    notes = []
    if "owner" in payload:
        notes.append(f"assigned to {payload['owner']['new']}")
    if "title" in payload:
        notes.append("title changed")
    if "priority" in payload:
        notes.append(f"priority {payload['priority']['old']} -> {payload['priority']['new']}")
    return notes


def _when(r: dict) -> int:
    """Where a request sits in the chat: where its agent read it, else when it was made."""
    return int(r["landed_at"] or r["ts"] or 0)


_EVENT_KINDS = (*_STATE_AFTER, "task_handoff", "edit_task")


def _task_events(con, rows: list[dict]) -> list[dict]:
    """The changes, since the first of `rows`, of the tasks delivered in them: a state it reached, a handoff, an edit. A task starts as queued, and
    a delivery that landed puts it in its owner's hands (doing)."""
    cards = {sorted(int(t) for t in r["tasks"].split(","))[0]: r["task_title"] for r in rows if r["kind"] == "task" and r["tasks"]}
    if not cards:
        return []
    since = min((_when(r) for r in rows if _when(r)), default=0)
    kinds = _EVENT_KINDS
    events = con.execute(f"SELECT ts, agent, kind, payload, {db.EVENT_TASK} AS tid FROM events WHERE kind IN ({','.join('?' * len(kinds))}) "
                         f"AND {db.EVENT_TASK} IN ({','.join('?' * len(cards))}) ORDER BY id", (*kinds, *cards)).fetchall()
    steps = []      # (time, task, who, the state it reaches or None, what to say instead, the note with it, the delivery it belongs to)
    for e in events:
        if e["kind"] in _STATE_AFTER:
            steps.append((e["ts"], e["tid"], e["agent"], _STATE_AFTER[e["kind"]], None, "", None))
            continue
        payload = json.loads(e["payload"])
        if e["kind"] == "task_handoff":
            steps.append((e["ts"], e["tid"], e["agent"], None, f"handed to {payload['to']}", payload.get("note", ""), payload.get("request_id")))
        else:
            steps += [(e["ts"], e["tid"], e["agent"], None, note, "", None) for note in _edit_notes(payload)]
    for r in rows:
        if r["kind"] == "task" and r["tasks"] and landing.state(r) == "delivered" and _when(r):
            steps.append((_when(r), sorted(int(t) for t in r["tasks"].split(","))[0], r["target"], "doing", None, "", None))
    state: dict[int, str] = {}
    out = []
    for ts, tid, who, after, note, said, request in sorted(steps, key=lambda step: step[0]):
        if after is not None:
            before = state.get(tid, "queued")
            state[tid] = after
            if before == after:
                continue
            note = f"{before} -> {after}"
        if ts >= since:
            out.append({"type": "task_event", "at": _TS(ts), "task": tid, "title": cards[tid], "who": who, "change": note, "note": said,
                        "request": request, "_ts": ts})
    return out


def _sessions(con, agent: str, rows: list[dict]) -> list[dict]:
    since = min((_when(r) for r in rows if _when(r)), default=0)
    return [{"type": "session", "at": _TS(r["ts"]), "cause": r["cause"], "handoff": r["text"] or None, "_ts": r["ts"]}
            for r in db.session_starts(con, agent) if r["ts"] >= since and not r["resumed"]]


def version_of(con, agent: str, everyone: bool) -> str:
    """One string that changes whenever something in the chat does: a request made, answered, closed or changed, a task event, a session start."""
    import hashlib
    where, params = chatreads._scope(agent, everyone)
    row = con.execute(
        f"SELECT COUNT(*), COALESCE(MAX(id), 0), COALESCE(MAX({db.touched_sql('requests')}), 0), COALESCE(MAX(delivered_at), 0), COUNT(delivered_at), "
        "COALESCE(MAX(landed_at), 0), COUNT(landed_at), COALESCE(MAX(typed_at), 0), COALESCE(SUM(state = 'queued'), 0) "
        f"FROM requests WHERE state NOT IN {chatreads._GONE} AND {where}", params).fetchone()
    events = con.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM events WHERE kind IN "
                         f"({','.join('?' * len(_EVENT_KINDS))})", _EVENT_KINDS).fetchone()
    starts = con.execute("SELECT COUNT(*), COALESCE(MAX(id), 0) FROM session_starts WHERE agent = ?", (agent,)).fetchone()
    return hashlib.sha256(repr((*row, *events, *starts)).encode()).hexdigest()[:24]


def _request_item(con, r: dict) -> dict:
    dismissed, by, reason = db.dismissal(r)
    return {"type": "request", "at": _TS(_when(r)), "message": message_of(con, r), "dismissal": {"by": by, "reason": reason} if dismissed else None,
            "task_state": r.get("task_state"), "_ts": _when(r), "_id": r["id"]}


def _requests(ctx, params) -> dict:
    from . import stream
    everyone = _scope(params)
    before, since, limit = _int(params, "before"), _int(params, "since"), _int(params, "limit", 1, MAX_LIMIT) or PAGE
    if before is not None and since is not None:
        raise rpc.bad_request("before and since are one or the other", "since", rpc.INVALID_PARAMS)
    con = db.connect()
    try:
        agent = _agent(con, params)
        cursor = stream.newest(con)
        rows, more = (chatreads.requests_page(con, agent, everyone, since=since) if since is not None
                      else chatreads.requests_page(con, agent, everyone, before=before, limit=limit))
        items = [_request_item(con, r) for r in rows] + _task_events(con, rows) + _sessions(con, agent, rows)
        items.sort(key=lambda i: (i["_ts"], 0 if i["type"] == "request" else 1, i.get("_id", 0)))
        for i in items:
            i.pop("_ts", None)
            i.pop("_id", None)
        return {"items": items, "more": more, "version": version_of(con, agent, everyone), "cursor": cursor}
    finally:
        con.close()


def _owner_of_chat(row) -> str | None:
    """The agent whose chat a request is part of when none is named: its non-person end, the target first."""
    return next((n for n in (row["target"], row["requester"]) if n != db.HUMAN), None)


def _alone(row) -> rpc.RpcError:
    """The message exists but no chat draws it (withdrawn, or never reached its target): not_found that says what it was."""
    return rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no chat shows message {row['id']}", what="message", name=str(row["id"]),
                        **{"from": row["requester"], "to": row["target"], "body": textsafe.text(row["body"] or "")})


def _locate(ctx, params) -> dict:
    mid = params.get("request")
    if isinstance(mid, bool) or not isinstance(mid, int):
        raise rpc.bad_request("request is a message number", "request", rpc.INVALID_PARAMS)
    limit = _int(params, "limit", 1, MAX_LIMIT) or DEFAULT_LIMIT
    con = db.connect()
    try:
        row = db.get_request(con, mid)
        if row is None:
            raise rpc.RpcError(rpc.NOT_FOUND, "not_found", f"no message {mid}", what="message", name=str(mid))
        agent = _agent(con, params, required=False)
        if agent is None:
            owner = db.get_request(con, row["settles"]) or row if row["settles"] else row
            agent = _owner_of_chat(owner)
        if agent is None:
            raise _alone(row)
        found = chatreads.locate_message(con, mid, agent, False)
        if found.kind == "alone":
            raise _alone(row)
        chat_agent, everyone = (agent, found.tab == "all") if found.kind == "here" else (found.agent, found.tab == "all")
        back, older = chatreads.requests_page(con, chat_agent, everyone, before=found.request + 1, limit=limit // 2 + 1)
        ahead, newer = chatreads.requests_after(con, chat_agent, everyone, found.request, limit - len(back))
        page = [message_of(con, r) for r in reversed(back + ahead)]
        return {"agent": chat_agent, "scope": "all" if everyone else "mine", "message": next(m for m in page if m["id"] == found.request),
                "messages": page, "more_older": older, "more_newer": newer}
    finally:
        con.close()


def _search(ctx, params) -> dict:
    query = params.get("query")
    if not isinstance(query, str) or not query.strip():
        raise rpc.bad_request("query is text and not empty", "query", rpc.INVALID_PARAMS)
    limit, everyone = _int(params, "limit", 1, SEARCH_MAX) or SEARCH_LIMIT, _scope(params, required=False)
    con = db.connect()
    try:
        agent = _agent(con, params, required=False)
        like = "%" + query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where, scope_params = (chatreads._scope(agent, bool(everyone)) if agent else ("1 = 1", ()))
        sql = ("SELECT id FROM requests WHERE state NOT IN " + chatreads._GONE + " AND settles IS NULL AND " + where
               + " AND (body LIKE ? ESCAPE '\\' OR EXISTS (SELECT 1 FROM requests a WHERE a.settles = requests.id AND a.body LIKE ? ESCAPE '\\'))"
               " ORDER BY id DESC LIMIT ?")
        out = []
        for hit in con.execute(sql, (*scope_params, like, like, limit)).fetchall():
            row = db.get_request(con, hit["id"])
            chat_agent = agent or _owner_of_chat(row)
            if chat_agent is None:
                continue
            found = chatreads.locate_message(con, hit["id"], chat_agent, bool(everyone))
            if found.kind == "alone":
                continue
            shown_agent, tab = (chat_agent, found.tab) if found.kind == "here" else (found.agent, found.tab)
            out.append({**message_of(con, chatreads.request_row(con, hit["id"])), "agent": shown_agent, "scope": tab})
        return {"messages": out}
    finally:
        con.close()


# ---------------------------------------------------------------- transcript
# An item's id: its time, its source and the source's row number, small enough for a JavaScript number (under 2**53).
_REPLY, _PROMPT, _TASK = 0, 1, 2
_PER_SOURCE = 500
_TASK_EVENTS = {"task_new": "opened", "task_begin": "changed", "task_start": "changed", "edit_task": "changed", "task_handoff": "changed",
                "queue_task": "changed", "draft_task": "changed", "reopen_task": "changed", "release_task": "changed",
                "merge_task": "changed", "mark_task_done": "done", "discard_task": "withdrawn", "close_task": "withdrawn"}


def _item_id(ts: int, source: int, row: int) -> int:
    return int(ts) * 1_000_000 + source * 100_000 + row % 100_000


def replies_of(r: dict, agent: str) -> list[dict]:
    """What `agent` said in this request of its chat with the person: its message to them, and its answer to theirs."""
    out = []
    if r["requester"] == agent and r["target"] == db.HUMAN and r["body"]:
        at = r["landed_at"] or r["ts"]
        out.append({"id": _item_id(at, _REPLY, r["id"] * 2), "at": _TS(at), "kind": "reply", "text": r["body"]})
    shown = db.shown_verdict(r)
    if r["requester"] == db.HUMAN and r["target"] == agent and shown and r["answer_id"] and not db.dismissal(r)[0]:
        at = db.answered_at(r)
        out.append({"id": _item_id(at, _REPLY, r["id"] * 2 + 1), "at": _TS(at), "kind": "reply", "text": shown})
    return out


def _reply_items(con, agent: str) -> list[dict]:
    rows, _ = chatreads.requests_page(con, agent, False, limit=_PER_SOURCE)
    return [i for r in rows for i in replies_of(r, agent)]


def prompt_item_of(row) -> dict:
    block = prompt_blocks.of_row(row)
    return {"id": _item_id(row["first_seen"], _PROMPT, row["id"]), "at": _TS(row["first_seen"]), "kind": "prompt",
            "prompt": rpc_agents.prompt_of(block), "open": row["gone_at"] is None}


def _prompt_items(con, agent: str) -> list[dict]:
    return [prompt_item_of(r) for r in con.execute("SELECT * FROM prompt_blocks WHERE agent = ? ORDER BY id DESC LIMIT ?", (agent, _PER_SOURCE))]


def task_item_of(con, row) -> dict | None:
    """One task event of the events table as a transcript item, or None when it is not one the chat shows."""
    event = _TASK_EVENTS.get(row["kind"])
    try:
        payload = json.loads(row["payload"] or "{}")
    except ValueError:
        return None
    tid = payload.get("task_id")
    if event is None or not isinstance(tid, int):
        return None
    evidence = payload.get("done") if event == "done" else payload.get("reason") if event == "withdrawn" else None
    return {"id": _item_id(row["ts"], _TASK, row["id"]), "at": _TS(row["ts"]), "kind": "task_event",
            "task_event": {"event": event, "task": tid, "request": payload.get("request_id"), "evidence": evidence}}


def _task_items(con, agent: str) -> list[dict]:
    kinds = tuple(_TASK_EVENTS)
    rows = con.execute(f"SELECT id, ts, agent, kind, payload FROM events WHERE kind IN ({','.join('?' * len(kinds))}) AND "
                       f"{db.EVENT_TASK} IN (SELECT id FROM tasks WHERE owner = ?) ORDER BY id DESC LIMIT ?", (*kinds, agent, _PER_SOURCE)).fetchall()
    return [i for i in (task_item_of(con, r) for r in rows) if i]


def working_from(view: dict) -> dict:
    """An agent's view (`agentview.compute`, or the stored one) as the spec's Working. The state word is read in either case: the stored view
    says RUNNING today and `running` once the spec's lower-case words are stored."""
    return {"turn_running": str(view["state"]).upper() == "RUNNING", "line": view.get("blocker") or view.get("state_detail") or None,
            "since": view.get("state_since")}


def working_of(con, agent: str) -> dict:
    return working_from(agentview.compute(con, agent))


def _transcript(ctx, params) -> dict:
    before, since, limit = _int(params, "before"), _int(params, "since"), _int(params, "limit", 1, MAX_LIMIT) or DEFAULT_LIMIT
    if before is not None and since is not None:
        raise rpc.bad_request("before and since are one or the other", "since", rpc.INVALID_PARAMS)
    con = db.connect()
    try:
        agent = _agent(con, params)
        items = sorted(_reply_items(con, agent) + _prompt_items(con, agent) + _task_items(con, agent), key=lambda i: i["id"])
        if since is not None:       # from the second `since` is in: a source that wrote later in it has an id below it
            items, more = [i for i in items if i["id"] >= since // 1_000_000 * 1_000_000][:MAX_LIMIT], False
        else:
            items = [i for i in items if before is None or i["id"] < before]
            more, items = len(items) > limit, items[-limit:]
        return {"items": items, "more": more, "working": working_of(con, agent)}
    finally:
        con.close()


rpc.METHODS["chat.messages"] = rpc.Method(_messages, "member")
rpc.METHODS["chat.requests"] = rpc.Method(_requests, "member")
rpc.METHODS["chat.transcript"] = rpc.Method(_transcript, "member")
rpc.METHODS["chat.locate"] = rpc.Method(_locate, "member")
rpc.METHODS["chat.search"] = rpc.Method(_search, "member")
