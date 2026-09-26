"""The founder's answer to a roster or trust proposal, kept where no agent can write it.

The request row an escalation is answered on lives in the db, which every agent
can write, so a verdict read from it is no proof the founder gave it. The
answer is therefore recorded here as it is given - by the founder's own
session, in the protected store - and `hr.apply` and `trust.apply` act on this
record, never on the row alone. A proposal answered before this record existed
has none and is raised again.
"""
from __future__ import annotations

import hashlib
import json
import time

from ..infra import protected


def _ns(kind: str) -> str:
    return f"{kind}-decisions"


def digest(fields: dict) -> str:
    """What was decided on, as one comparable string."""
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def record(kind: str, pid: str, request_id: int, call: str, subject: str) -> None:
    """`call` is "approved" or "rejected"; `subject` is what it was decided on
    (the module's `subject_of` for the draft the founder was shown).
    PermissionError from an agent's session."""
    protected.put(_ns(kind), pid, json.dumps(
        {"request": int(request_id), "call": call, "subject": subject,
         "at": int(time.time())}))


def get(kind: str, pid: str) -> dict | None:
    raw = protected.get(_ns(kind), pid)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def refusal(kind: str, pid: str, request_id: int, subject: str) -> str | None:
    """Why the founder's answer to `request_id` cannot be relied on, or None
    when the record names this request, a decision, and the draft as it is now."""
    got = get(kind, pid)
    if got is None:
        return (f"no answer from the founder is on record for proposal {pid} - it was "
                f"answered before answers were recorded, or not by the founder; raise it again")
    if got.get("request") != int(request_id) or got.get("call") not in ("approved", "rejected"):
        return f"the founder's recorded answer for proposal {pid} is not for #{request_id}"
    if got.get("subject") != subject:
        return (f"proposal {pid} was changed after the founder answered it - what they "
                f"approved is not what is in the draft now; raise it again")
    return None
