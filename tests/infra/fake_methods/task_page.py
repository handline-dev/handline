"""task.ask, and the one task the fake keeps for task.get and tasks.list once an agent has opened it."""
from . import CURRENT, err, fake

TS = "2026-01-01T00:00:00Z"


def row() -> dict:
    return {"id": 1, "from": "a.one", "to": "a.one", "title": "the page task", "state": "doing", "relation": "", "response": "",
            "time_box": "1h", "parent": None, "created": TS, "delivered": None, "done": None, "hold_reason": None, "block_reason": None,
            "evidence": None, "priority": 2, "last_activity": TS}


def detail() -> dict:
    return {**row(), "body": "a brief the page shows", "deliveries": [], "events": [], "memos": [], "blockers": [], "children": [], "handoffs": [],
            "asker": "a.one", "discarded": None, "merged_into": None,
            "time": {"length_s": 3600, "default": CURRENT.ctx.state["task_page"]["default"], "start": TS, "end": TS, "overdue_since": None, "told": 0, "left_s": None},
            "parent_title": None, "subtasks": [], "timeline": [], "people": {"recent": [], "team": [], "others": []}}


def opened() -> bool:
    return bool(CURRENT.ctx.state.get("task_page"))


@fake("task.ask")
def task_ask(role, method, params):
    if not isinstance(params.get("request_id"), str) or not params["request_id"]:
        return "fail", err(-32600, "bad_request", field="request_id")
    if not isinstance(params.get("text"), str) or not params["text"].strip():
        return "fail", err(-32602, "bad_request", field="text")
    return "fail", {"code": -32001, "message": "not_found", "data": {"code": "not_found", "what": "task"}}
