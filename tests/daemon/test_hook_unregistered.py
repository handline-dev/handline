"""Tests: a hook from a name no agent has moves nothing. A Stop hook from a session run by hand under an unregistered
name wrote an IDLE state row for it, which hl ps then listed as an agent for good, with the background work it last reported.

Run on its own: python3 tests/daemon/test_hook_unregistered.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.daemon import rpc_hooks  # noqa: E402


def test_a_hook_from_a_name_no_agent_has_writes_nothing_and_a_registered_agents_does(home: Path) -> None:
    support._daemon_home(home, 20)
    con = db.connect()
    try:
        for agent in ("ghost", "backend"):
            for kind in ("UserPromptSubmit", "Stop"):
                rpc_hooks.apply(con, agent, kind, {}, f"{agent}-{kind}", int(time.time()))
        check("hook: no state row, no event, no applied line for an unregistered name", db.get_agent_state(con, "ghost") is None
              and con.execute("SELECT COUNT(*) FROM events WHERE agent = 'ghost'").fetchone()[0] == 0
              and con.execute("SELECT 1 FROM hook_applied WHERE agent = 'ghost'").fetchone() is None)
        check("hook: a registered agent's Stop is still applied", db.get_agent_state(con, "backend")["state"] == "IDLE")
    finally:
        con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_a_hook_from_a_name_no_agent_has_writes_nothing_and_a_registered_agents_does(Path(td) / "h1"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
