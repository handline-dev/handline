"""Which background commands an agent has running, and for how long (T481, T450).

The footer says only how many ("2 shells"). The Bash hook, after a call made with
`run_in_background`, has the command; Claude writes each job's output to
`<session>/tasks/<id>.output` and ends it with `[exited with code N]`. `note` records the
command when the call returns; `running` is what is still going: started since the
footer last showed none, no exit line in its output, and no more of them than the
footer counts (a job that was killed leaves no exit line).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .. import db

KIND = "background_started"
_OUTPUT = re.compile(r"(/[^\s\"'\\]+/tasks/([A-Za-z0-9]+)\.output)")
_ID = re.compile(r"(?:backgroundTaskId|shellId|bash_id|task_id)\"\s*:\s*\"([A-Za-z0-9]+)\"|ID:\s*([A-Za-z0-9]+)")
_EXIT = "[exited with code"
_COUNT = re.compile(r"\d+")
COMMAND_WIDTH = 100


def note(agent: str, raw: dict) -> None:
    """Record a Bash call that started something in the background. Every tool call runs
    this, so a call that is not one returns before anything is opened."""
    tool_input = raw.get("tool_input")
    if raw.get("tool_name") != "Bash" or not isinstance(tool_input, dict) \
            or tool_input.get("run_in_background") is not True:
        return
    resp = raw.get("tool_response")
    text = resp if isinstance(resp, str) else json.dumps(resp, ensure_ascii=False)
    path = _OUTPUT.search(text)
    found = _ID.search(text)
    payload = {"command": str(tool_input.get("command") or ""),
               "job": path.group(2) if path else (found.group(1) or found.group(2) if found else None),
               "output": path.group(1) if path else None,
               "tool_use_id": raw.get("tool_use_id"), "session": raw.get("session_id")}
    con = db.connect()
    try:
        db.add_event(con, agent, "background_started", payload)
    finally:
        con.close()


def footer_count(text: str | None) -> int:
    """How many things the footer says are running: "2 shells, 1 monitor" is 3."""
    return sum(int(n) for n in _COUNT.findall(text or ""))


def _ended(output: str | None) -> bool:
    if not output:
        return False
    try:
        with open(output, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 200))
            return _EXIT in f.read().decode("utf-8", "replace")
    except OSError:
        return False


def running(con, agent: str, count: int | None = None) -> list[dict]:
    """The recorded jobs still going, oldest first: {command, started, job}."""
    since = max(db.last_event_ts(con, agent, "background_work_ended") or 0,
                db.last_event_ts(con, agent, "SessionStart") or 0)
    rows = con.execute("SELECT ts, payload FROM events WHERE agent = ? AND kind = ? AND ts >= ? "
                       "ORDER BY id", (agent, KIND, since)).fetchall()
    jobs = []
    for r in rows:
        p = json.loads(r["payload"] or "{}")
        if not _ended(p.get("output")):
            jobs.append({"command": p.get("command", ""), "started": int(r["ts"]), "job": p.get("job")})
    return jobs[-count:] if count else jobs


def line(job: dict, now: int, ago) -> str:
    cmd = " ".join(job["command"].split())
    cmd = cmd if len(cmd) <= COMMAND_WIDTH else cmd[:COMMAND_WIDTH - 1] + "…"
    return f"`{cmd}` for {ago(max(0, now - job['started']))}"
