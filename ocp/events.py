"""Event capture (design section 5, "record timing").

Events are inserted by hooks at a few coarse moments, never on a timer and
never from PostToolUse: that fires constantly and its signal-to-noise is poor.

Nothing here uses a model. The hook reads the JSON Claude Code puts on stdin,
takes an allowlisted handful of fields, and inserts one row.
"""
from __future__ import annotations

import json
import os
import sys

from . import db, waiting

# Which hook events we record. PostToolUse is deliberately absent.
CAPTURED = {
    "SessionStart", "SessionEnd", "Stop", "StopFailure",
    "SubagentStop", "PostCompact", "UserPromptSubmit",
    # An agent waiting on a permission prompt is neither working nor idle: it
    # emits no Stop, so it reads RUNNING forever, and its status line stops
    # refreshing, so the account's usage reading goes stale behind it. On
    # 09-17 that took out every agent on the account for fail-closed
    # usage_unknown, fifteen minutes after keeper stopped at a prompt nobody
    # could see. Orca installs the hook; we simply were not listening.
    "PermissionRequest",
    # A second witness that an agent went quiet. Stop was the only one, and
    # when Stop had no transition the whole system went blind for hours.
    "TeammateIdle",
}

# Hooks that say a permission prompt was answered. They are installed but
# never recorded - PostToolUse fires on every tool call - and all they do is
# take away the wait a PermissionRequest raised (`waiting.clear`).
ANSWERS = {"PostToolUse", "PostToolUseFailure"}

# The events after which no prompt can still be pending: a new prompt, or the
# turn or the session ending.
ENDS_WAIT = {"UserPromptSubmit", "Stop", "StopFailure", "SessionStart", "SessionEnd"}

# Per event, the stdin fields worth keeping.
ALLOWLIST = (
    "session_id", "hook_event_name", "source", "reason", "cwd",
    "prompt_length", "transcript_path", "model", "trigger",
    "tool_name", "error", "error_details",
)


def _slim(raw: dict) -> dict:
    out = {}
    for k in ALLOWLIST:
        v = raw.get(k)
        if v is None:
            continue
        if k == "transcript_path":
            out[k] = str(v)
        elif isinstance(v, (str, int, float, bool)):
            out[k] = v if not isinstance(v, str) else v[:200 if k == "error_details" else 400]
        elif isinstance(v, dict) and k == "model":
            out[k] = v.get("id")
    # A prompt's text is never stored; its length is enough signal.
    if "prompt" in raw and isinstance(raw["prompt"], str):
        out["prompt_length"] = len(raw["prompt"])
    return out


# StopFailure's `error` is the error type claude reports; a usage limit is
# this one. Classified on that field only - never on text, which on this team
# is full of talk about usage limits.
LIMIT_ERROR = "rate_limit"


def limit_reason(raw: dict) -> tuple[str, str] | None:
    """(text, where it was read from) when this StopFailure is a usage limit."""
    if raw.get("error") != LIMIT_ERROR:
        return None
    details = raw.get("error_details")
    text = details if isinstance(details, str) and details else LIMIT_ERROR
    return text[:200], "payload.error"


def at_limit(con, agent: str) -> dict | None:
    """The limit this agent's last turn ended on, if nothing has run since.

    Latest of StopFailure and UserPromptSubmit: a prompt after the failure means
    the agent is working again (a move's wake types one). Carries the event's
    `ts` as `at`.
    """
    row = con.execute(
        "SELECT kind, payload, ts FROM events WHERE agent = ? AND kind IN "
        "('StopFailure', 'UserPromptSubmit') ORDER BY id DESC LIMIT 1", (agent,)).fetchone()
    if not row or row["kind"] != "StopFailure":
        return None
    try:
        data = json.loads(row["payload"])
    except (TypeError, ValueError):
        return None
    if not (isinstance(data, dict) and data.get("limit")):
        return None
    return dict(data, at=int(row["ts"]))


def record_from_stdin(argv: list[str] | None = None) -> int:
    """Entry point for `ocp hook <event>`. Always exits 0.

    A hook that fails must never take the agent down with it, so every error
    path here is swallowed after the fact is recorded.
    """
    argv = argv if argv is not None else sys.argv[1:]
    event = argv[0] if argv else ""
    try:
        text = sys.stdin.read()
    except (OSError, ValueError):
        text = ""
    try:
        raw = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}

    event = raw.get("hook_event_name") or event
    agent = os.environ.get("OCP_AGENT")
    account = os.environ.get("OCP_ACCOUNT")
    if agent and event in ANSWERS:
        try:
            waiting.clear(agent, raw.get("tool_name"), raw.get("tool_input"))
        except Exception:  # noqa: BLE001 - a hook must never break the session
            pass
        print("{}")
        return 0
    if not agent or event not in CAPTURED:
        # Not one of ours, or not an event we keep. Stay silent and succeed.
        print("{}")
        return 0

    try:
        con = db.connect()
        try:
            payload = _slim(raw)
            if event == "StopFailure":
                lim = limit_reason(raw)
                if lim:
                    payload["limit"], payload["limit_source"] = lim
            if event == "PermissionRequest":
                payload["command"] = waiting.summary(raw.get("tool_input"))
                waiting.note(agent, raw.get("tool_name"), raw.get("tool_input"))
            elif event in ENDS_WAIT:
                waiting.clear(agent)
            db.add_event(con, agent, event, payload, account=account)
            _mark_activity(con, agent, event, payload)
        finally:
            con.close()
    except Exception:  # noqa: BLE001 - a hook must never break the session
        pass
    print("{}")
    return 0


# What counts as "this agent is working".
#
# Only a prompt does. A session starting does not: /clear ends one session and
# starts another, so treating SessionStart as activity let the daemon's own
# context reclaim look like the agent waking up. That cleared the park it had
# just set, the usage trigger fired again on the unchanged number, and the
# agent handed off in a loop - 42 folds in seven minutes before it was caught.
# The hook events that only mark the edges of a turn. `ocp bridge` counts these
# rather than drawing them: the agent's own state column says which side of a
# turn it is on, and they crowd out the rows that say what was decided. What is
# NOT in here is deliberate - PermissionRequest and TeammateIdle are the two
# events that mean a human is wanted, which is the one thing that panel exists
# to show.
TURN_BOUNDARIES = {"SessionStart", "SessionEnd", "Stop", "StopFailure",
                   "SubagentStop", "PostCompact", "UserPromptSubmit"}

ACTIVE_EVENTS = {"UserPromptSubmit"}

# And which one says it stopped. Stop fires when the agent has finished
# responding and is back at its prompt; SubagentStop is deliberately absent,
# because a subagent finishing says nothing about what the agent itself is doing.
QUIET_EVENTS = {"Stop"}

# The daemon owns these; an agent must not move itself out of them.
PROTECTED_STATES = {"STALLED", "HANDOFF"}


def _mark_activity(con, agent: str, event: str, payload: dict | None = None) -> None:
    """Let the daemon know whether the agent is working.

    Without this, agent_state only ever changes when the daemon itself changes
    it, so an agent that was parked and then given work directly still reads as
    PARKED - and the daemon would "wake" it mid-task by injecting a handoff it
    never needed. The hooks already fire on every turn; they just have to say so.

    Both ends matter, and only one of them was recorded. RUNNING is the state
    that stops work being delivered and stops the session being closed, so an
    agent that entered it and never left was silently taken out of service: it
    was never told about a single request again and never gave its memory back.
    Three agents sat that way for over two hours.
    """
    row = db.get_agent_state(con, agent)
    if row and row["state"] in PROTECTED_STATES:
        return
    if event in ACTIVE_EVENTS:
        if row and row["state"] == "RUNNING" and not row["wake_at"]:
            return  # already correct; do not rewrite on every prompt
        db.set_agent_state(con, agent, "RUNNING", wake_at=None, detail=f"active ({event})")
    elif event == "StopFailure" and payload and payload.get("limit"):
        # Out of usage is not working: the turn ended and nothing will run.
        if row and row["state"] == "RUNNING":
            db.set_agent_state(con, agent, "IDLE", wake_at=None,
                               detail=f"at its usage limit: {payload['limit'][:80]}")
    elif event in QUIET_EVENTS and row and row["state"] == "RUNNING":
        db.set_agent_state(con, agent, "IDLE", wake_at=None,
                           detail="finished its turn")


def record_commit(agent: str, repo: str, sha: str, subject: str, diffstat: str) -> None:
    """git post-commit: commit and diffstat, no model (section 5)."""
    con = db.connect()
    try:
        db.add_event(con, agent, "commit", {
            "repo": repo, "sha": sha, "subject": subject[:200], "diffstat": diffstat[:300],
        }, dedup_key=f"commit:{repo}:{sha}")
    finally:
        con.close()
