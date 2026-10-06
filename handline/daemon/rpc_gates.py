"""The gates the session and terminal methods share, each a refusal naming its gate (contract/protocol.json x-gates)."""
from __future__ import annotations

from ..infra import lock
from . import inputhold, lifecycle, rpc


def terminal_lock(agent: str) -> None:
    """Refuse at once while whoever types at the agent holds the lock the tick takes, before anything is done."""
    with lock.terminal(agent) as mine:
        if not mine:
            raise rpc.refused(f"{agent}: {lifecycle.TYPING_ELSEWHERE}", gate="terminal_lock")


def input_holder(ctx, con, agent: str) -> None:
    """Refuse a call while a person or device other than the caller holds the input. The daemon holding it is a
    short hold, not a refusal."""
    row = inputhold.holder(con, agent)
    if row is not None and row["name"] != ctx.caller.info["name"]:
        raise rpc.refused(inputhold.held_why(agent, row["name"]), gate="input_holder")


def holder_kind(ctx) -> str:
    """The spec's holder kind of the caller: a person, or a device."""
    return "device" if ctx.caller.info["kind"] == "device" else "person"
