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

from . import db

# Which hook events we record. PostToolUse is deliberately absent.
CAPTURED = {
    "SessionStart", "SessionEnd", "Stop", "StopFailure",
    "SubagentStop", "PostCompact", "UserPromptSubmit",
}

# Per event, the stdin fields worth keeping.
ALLOWLIST = (
    "session_id", "hook_event_name", "source", "reason", "cwd",
    "prompt_length", "transcript_path", "model", "trigger",
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
            out[k] = v if not isinstance(v, str) else v[:400]
        elif isinstance(v, dict) and k == "model":
            out[k] = v.get("id")
    # A prompt's text is never stored; its length is enough signal.
    if "prompt" in raw and isinstance(raw["prompt"], str):
        out["prompt_length"] = len(raw["prompt"])
    return out


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
    if not agent or event not in CAPTURED:
        # Not one of ours, or not an event we keep. Stay silent and succeed.
        print("{}")
        return 0

    try:
        con = db.connect()
        try:
            db.add_event(con, agent, event, _slim(raw), account=account)
        finally:
            con.close()
    except Exception:  # noqa: BLE001 - a hook must never break the session
        pass
    print("{}")
    return 0


def record_commit(agent: str, repo: str, sha: str, subject: str, diffstat: str) -> None:
    """git post-commit: commit and diffstat, no model (section 5)."""
    con = db.connect()
    try:
        db.add_event(con, agent, "commit", {
            "repo": repo, "sha": sha, "subject": subject[:200], "diffstat": diffstat[:300],
        }, dedup_key=f"commit:{repo}:{sha}")
    finally:
        con.close()


POST_COMMIT_HOOK = """#!/bin/sh
# Installed by `ocp hook install-git`. Records the commit as an ocp event.
# Never fails the commit: every path exits 0.
[ -n "$OCP_AGENT" ] || exit 0
command -v ocp >/dev/null 2>&1 || exit 0
ocp hook commit >/dev/null 2>&1 || true
exit 0
"""
