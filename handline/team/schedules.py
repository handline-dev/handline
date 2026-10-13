"""An agent's schedules as its agent.yaml holds them: a JSON list of {"cron", "message"} under `schedules`."""
from __future__ import annotations

import hashlib
import json

from ..infra import cron
from . import agents


def parse(text) -> list[dict]:
    """The schedules in an agent's `schedules` value, validated; ValueError says what is wrong with it."""
    if text in (None, ""):
        return []
    try:
        items = json.loads(text) if isinstance(text, str) else text
    except ValueError as e:
        raise ValueError(f"schedules is not JSON: {e}") from e
    if not isinstance(items, list):
        raise ValueError("schedules is a list of {cron, message}")
    out = []
    for i, it in enumerate(items, 1):
        if not isinstance(it, dict) or set(it) != {"cron", "message"}:
            raise ValueError(f"schedule {i} is {{cron, message}} and nothing else")
        cron.parse(str(it["cron"]))
        msg = " ".join(str(it["message"]).split())
        if not msg:
            raise ValueError(f"schedule {i} has no message")
        out.append({"cron": " ".join(str(it["cron"]).split()), "message": msg})
    return out


def load(agent: str) -> list[dict]:
    """This agent's schedules; ValueError when its value is bad, and whatever agents.read raises."""
    return parse((agents.read(agent) or {}).get("schedules"))


def of(agent: str) -> list[dict]:
    """`load`, and none for an agent whose agent.yaml or value cannot be read - the sweep's view."""
    try:
        return load(agent)
    except Exception:  # noqa: BLE001 - a bad agent.yaml is doctor's to report, not this sweep's
        return []


def sid(item: dict) -> str:
    """What identifies a schedule: editing its cron or its message makes it a new one."""
    return hashlib.sha1(f"{item['cron']}\n{item['message']}".encode()).hexdigest()[:10]
