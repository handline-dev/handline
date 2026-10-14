"""Tests: the input holder (input.take, input.release), prompt.list and prompt.answer (contract/protocol.md):
who holds a terminal's input, the daemon typing nothing at a held one, the idle hold, the prompt a screen shows and the
answer to it, and the gates each refusal names (role, terminal lock, input holder, stale prompt).

The screen actions are replaced by recorders: they have their own tests; this file asks what the methods decide.

Run on its own: python3 tests/daemon/test_rpc_input.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import getpass
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline import actions  # noqa: E402
from handline.actions import prompt_blocks  # noqa: E402
from handline.daemon import delivery, inputhold  # noqa: E402
from handline.infra import lock, sessiontoken  # noqa: E402
from handline.session import autoaccept  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))

ME = getpass.getuser()


def _code(r: dict):
    return r.get("error", {}).get("data", {}).get("code")


def _gate(r: dict):
    return r.get("error", {}).get("data", {}).get("gate")


def _hold(agent: str, name: str, kind: str = "person", ago: int = 0) -> None:
    con = db.connect()
    con.execute("DELETE FROM input_holders WHERE agent = ?", (agent,))
    inputhold.take(con, agent, kind, name, int(time.time()) - ago)
    con.close()


def _holders(agent: str) -> list:
    con = db.connect()
    try:
        return inputhold.view(con, agent)
    finally:
        con.close()


def _events(kind: str) -> list:
    con = db.connect()
    try:
        return con.execute("SELECT agent, payload FROM events WHERE kind = ? ORDER BY id", (kind,)).fetchall()
    finally:
        con.close()


def test_a_hold_that_goes_idle_is_lost_and_typing_refreshes_it(home: Path) -> None:
    support._board_world(home)
    _hold("worker", ME, ago=inputhold.idle_s() - 5)
    check("idle: a hold not yet idle stands", _holders("worker")[0]["name"] == ME)
    _hold("worker", ME, ago=inputhold.idle_s() + 5)
    con = db.connect()
    inputhold.holder(con, "worker")
    con.close()
    check("idle: past input_hold_idle_s the hold is the daemon's again", _holders("worker") == [inputhold.DAEMON])
    check("idle: and the loss is an event like a take", json.loads(_events("input_holder")[-1]["payload"])["holders"] == [inputhold.DAEMON])
    _hold("worker", ME, ago=inputhold.idle_s() - 5)
    con = db.connect()
    inputhold.touch(con, "worker", ME)
    con.close()
    _hold("worker", ME)  # a take of one's own hold keeps `since`
    con = db.connect()
    row = con.execute("SELECT since, last_typed FROM input_holders WHERE agent = 'worker'").fetchone()
    con.close()
    check("idle: typing counts the hold from now, and keeps when it was taken", row["last_typed"] >= row["since"] and row["last_typed"] > int(time.time()) - 3, str(dict(row)))
    cfg = loadmod.config_for_write()
    cfg[inputhold.IDLE_KEY] = 30
    loadmod.write_config(cfg)
    check("idle: input_hold_idle_s is the setting", inputhold.idle_s() == 30)
    _hold("worker", ME, ago=31)
    con = db.connect()
    check("idle: and a shorter one is honoured", inputhold.holder(con, "worker") is None)
    con.close()


def _block(agent: str, kind: str, detail: dict, panel=("a panel",), fingerprint="fp1", answerable: bool = True) -> None:
    con = db.connect()
    db.prompt_open(con, agent, kind, fingerprint, "title", list(panel), detail, 7 if answerable else None, answerable, int(time.time()))
    con.close()


def _watch_sees(agent: str) -> None:
    """The screen watch refreshing the open prompt: a row not refreshed for STALE_S is no prompt on screen, and a test that ran past that
    between opening it and answering it (the daemon calls in between are slow on a loaded machine) found none."""
    con = db.connect()
    db.prompt_seen(con, db.open_prompt(con, agent)["id"], int(time.time()))
    con.close()


class Answers:
    """`prompt_blocks.answer` replaced by a recorder."""

    def __enter__(self):
        self.calls: list = []
        self.reply = (True, "worker: answered")
        self.real = prompt_blocks.answer
        prompt_blocks.answer = lambda con, bid, choice: (self.calls.append((bid, choice)), self.reply)[1]
        return self

    def __exit__(self, *exc):
        prompt_blocks.answer = self.real


def main() -> int:
    def tests(td):
        run(lambda: test_a_hold_that_goes_idle_is_lost_and_typing_refreshes_it(Path(td) / "i1"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
