"""Event capture (design section 5, "record timing").

Events are inserted by hooks at a few coarse moments, never on a timer and
never from PostToolUse: that fires constantly and its signal-to-noise is poor.

Nothing here uses a model. The hook reads the JSON Claude Code puts on stdin and
hands it to the daemon's hook.record, which keeps an allowlisted handful of fields in one row.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid

from ..usage import collect
from .. import db
from ..infra import names, sessiontoken
from . import bgjobs, memoryuse, shellguard, waiting

# Which hook events we record. PostToolUse is deliberately absent.
CAPTURED = {
    "SessionStart", "SessionEnd", "Stop", "StopFailure",
    "SubagentStop", "PostCompact", "UserPromptSubmit",
    # An agent waiting on a permission prompt is neither working nor idle: it
    # emits no Stop, so it reads RUNNING forever, and its status line stops
    # refreshing, so the account's usage reading goes stale behind it. Orca
    # installs the hook; we simply were not listening.
    "PermissionRequest",
    # A second witness that an agent went quiet. Stop was the only one, and
    # when Stop had no transition the whole system went blind for hours.
    "TeammateIdle",
}

# Hooks that say a permission prompt was answered. They are installed but
# never recorded - PostToolUse fires on every tool call - and all they do is
# take away the wait a PermissionRequest raised (`waiting.clear`), and note a
# background command (`bgjobs.note`).
ANSWERS = {"PostToolUse", "PostToolUseFailure"}

# Hooks that can refuse a tool call: an ocp command that would substitute in
# double quotes (`shellguard.guard`) and the edit gate, matched to the
# tools they look at rather than firing on every call.
GUARDS = {"PreToolUse"}


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


def _record(agent: str, event: str, raw: dict) -> None:
    """Hand the event to the daemon, which writes it down and decides what it means for the agent's state. With
    the daemon down it goes to the spool, which only records. A hook never fails its session over this."""
    try:
        from ..infra import daemonclient as client, hookspool
        request_id = uuid.uuid4().hex
        at = int(time.time())
        try:
            sock = client.connect(2.0)
            if sock is None:
                raise OSError("no daemon")
            client.call(sock, "hook.record", {"agent": agent, "kind": event, "at": at, "data": raw,
                                              "request_id": request_id}, client_name="hook")
        except (OSError, client.RpcError):
            hookspool.append(agent, event, raw, request_id, at)
    except Exception:  # noqa: BLE001 - a hook must never break the session
        pass


def record_from_stdin(argv: list[str] | None = None) -> int:
    """Entry point for `hl hook <event>`. Always exits 0.

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
    agent = names.getenv("OCP_AGENT")
    account = names.getenv("OCP_ACCOUNT")
    if agent and sessiontoken.nested():
        # A Claude Code another process started inside the agent's session inherited its name and its
        # hooks, but it is not the agent: its turns and tool calls are not the agent's to record or guard.
        print("{}")
        return 0
    if agent and event in GUARDS:
        try:
            why = shellguard.guard(raw.get("tool_name"), raw.get("tool_input"))
            if why is None:
                from . import edittask
                why = edittask.guard(agent, raw.get("tool_name"), raw.get("tool_input"))
        except Exception:  # noqa: BLE001 - a hook must never break the session
            why = None
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": event, "permissionDecision": "deny", "permissionDecisionReason": why}})
            if why else "{}")
        return 0
    if agent and event in ANSWERS:
        try:
            waiting.clear(agent, raw.get("tool_name"), raw.get("tool_input"))
        except Exception:  # noqa: BLE001 - a hook must never break the session
            pass
        _record(agent, event, raw)
        if event == "PostToolUse":
            try:
                memoryuse.note_use(agent, raw.get("tool_name"), raw.get("tool_input"))
            except Exception:  # noqa: BLE001 - same
                pass
            try:
                bgjobs.note(agent, raw)
            except Exception:  # noqa: BLE001 - same
                pass
        print("{}")
        return 0
    if agent and event == "Stop":
        # T288: a block means the turn is not really ending, so this returns
        # before the ordinary Stop capture below would mark the agent IDLE.
        try:
            from . import stopgate
            block, reason = stopgate.decide(agent, raw)
        except Exception:  # noqa: BLE001 - a hook must never break the session
            block, reason = False, None
        if block:
            print(json.dumps({"decision": "block", "reason": reason}))
            return 0
    if not agent or event not in CAPTURED:
        # Not one of ours, or not an event we keep. Stay silent and succeed.
        print("{}")
        return 0

    _record(agent, event, raw)
    if event in COLLECT_EVENTS:
        try:
            collect.collect(agent, raw.get("cwd") or os.getcwd(), session_id=raw.get("session_id"))
        except Exception:  # noqa: BLE001 - same: a snapshot is not worth a session
            pass
    print("{}")
    return 0


# The moments at which this session's own snapshot is refreshed (D11).
#
# This is the statusLine's job, done by the hook that was already installed:
# these events fire on the same boundaries, they already carry the `cwd` the
# transcript is found under, and the work is one tail read. A `Stop` reaching
# here is also the heartbeat the statusLine used to be - the session is alive
# and has just finished a turn - which is what a pull alone cannot say.
#
# `SessionStart` is in here for the case that caught us out: `/clear` starts a
# new transcript, and until something reads it the agent's context reads as
# whatever it was before the clear. `SessionEnd` is deliberately absent - a
# snapshot written as the session exits would say a dead session is fresh.
COLLECT_EVENTS = {"SessionStart", "UserPromptSubmit", "Stop", "StopFailure", "PostCompact"}


# The hook events that only mark the edges of a turn. `hl` counts these
# rather than drawing them: the agent's own state column says which side of a
# turn it is on, and they crowd out the rows that say what was decided. What is
# NOT in here is deliberate - PermissionRequest and TeammateIdle are the two
# events that mean a human is wanted, which is the one thing that panel exists
# to show.
TURN_BOUNDARIES = {"SessionStart", "SessionEnd", "Stop", "StopFailure",
                   "SubagentStop", "PostCompact", "UserPromptSubmit"}

# Records kept for audit, not anything an agent did or a turn's edge: hidden from `hl events` unless
# --all, and left out of what a fold tells.
AUDIT_KINDS = {"verb_chain"}


def record_commit(agent: str, repo: str, sha: str, subject: str, diffstat: str) -> None:
    """git post-commit: commit and diffstat, no model (section 5)."""
    con = db.connect()
    try:
        db.add_event(con, agent, "commit", {
            "repo": repo, "sha": sha, "subject": subject[:200], "diffstat": diffstat[:300],
        }, dedup_key=f"commit:{repo}:{sha}")
    finally:
        con.close()
