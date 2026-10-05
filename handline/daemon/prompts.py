"""The screen watch (T395): what prompt each agent's screen shows, kept as a `prompt_blocks` row.

A job of its own, apart from the tick: it does nothing but read screens, so it can
run every couple of seconds without dragging the tick's slower work with it. Each
pass reads every agent's screen once (`term.prompt_on_screen`) and reconciles the
result with the agent's open row, level-triggered: the same prompt refreshes
`last_seen`, a different one closes the old row and opens a new one, none closes it.
A screen that cannot be read changes nothing - a row is never closed or refreshed
on a guess - and a row nobody has refreshed for `prompt_blocks.STALE_S` is not shown
by `open_blocks`, so a stopped watch fails closed.

Nothing here decides anything about the agent, and there is no model call. It does
not press keys: answering is `actions.prompt_blocks.answer`, under the terminal lock."""
from __future__ import annotations

import threading
import time

from .. import db
from ..actions import prompt_blocks
from ..session import term, waiting

WATCH_S = 2.0
def _watched(con, agent_row) -> str | None:
    """The terminal to read for this agent, or None: none, closed, or ocp's own /exit dialog
    (`delivery_blocks` origin 'close') which the tick already handles."""
    if not agent_row["terminal"] or agent_row["closed_at"]:
        return None
    block = db.block_get(con, agent_row["agent"])
    return None if block is not None and block["origin"] == "close" else agent_row["terminal"]


def watch_agent(con, agent: str, handle: str, now: int) -> str:
    """Bring `agent`'s open row in line with its screen; returns what it did, for the log."""
    kind, payload = term.prompt_on_screen(handle)
    if kind == term.PROMPT_UNREADABLE:
        return "unreadable"
    open_row = db.open_prompt(con, agent)
    if kind == term.PROMPT_NONE:
        if open_row is not None:
            db.prompt_close(con, open_row["id"], now)
            return "closed"
        return "none"
    seen = prompt_blocks.describe(agent, kind, payload, waiting.read(agent))
    fp = prompt_blocks.fingerprint(seen["kind"], seen["panel"])
    if open_row is not None:
        if open_row["fingerprint"] == fp and open_row["wait_id"] == seen["wait_id"]:
            db.prompt_seen(con, open_row["id"], now)
            return "seen"
        db.prompt_close(con, open_row["id"], now)
    db.prompt_open(con, agent, seen["kind"], fp, seen["title"], seen["panel"], seen["detail"],
                   seen["wait_id"], seen["answerable"], now)
    return "opened"


def watch_once(con, log) -> float:
    """One pass over every agent; the seconds it took."""
    started = time.time()
    for row in db.all_agent_states(con):
        handle = _watched(con, row)
        if handle is None:
            open_row = db.open_prompt(con, row["agent"])
            if open_row is not None:      # its terminal is gone: so is the prompt
                db.prompt_close(con, open_row["id"], int(started))
            continue
        try:
            did = watch_agent(con, row["agent"], handle, int(started))
        except Exception as e:  # noqa: BLE001 - one agent's screen is not the pass's failure
            log(f"prompts: {row['agent']}: {type(e).__name__}: {e}")
            continue
        if did in ("opened", "closed"):
            log(f"prompts: {row['agent']}: {did}")
    return time.time() - started


def run(stopping: dict, log) -> None:
    """Loop until `stopping["now"]`. Waits the longer of `WATCH_S` and the pass's own length, so a
    slow Orca is read from at most half the time however many agents there are."""
    con = db.connect()
    try:
        while not stopping["now"]:
            try:
                took = watch_once(con, log)
            except Exception as e:  # noqa: BLE001 - a bad pass is not the end of the watch
                log(f"prompts: pass failed: {type(e).__name__}: {e}")
                took = 0.0
            deadline = time.time() + max(WATCH_S, took)
            while not stopping["now"] and time.time() < deadline:
                time.sleep(0.2)
    finally:
        con.close()


def start(stopping: dict, log) -> threading.Thread:
    t = threading.Thread(target=run, args=(stopping, log), name="prompt-watch", daemon=True)
    t.start()
    return t
