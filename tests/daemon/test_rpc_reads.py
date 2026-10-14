"""Tests: daemon.status, tasks.list, task.get and inbox.list (contract/protocol.md): the shape each returns, what it
filters and pages, who sees what, a refusal each, and the commands they serve still working from an agent session.

Run on its own: python3 tests/daemon/test_rpc_reads.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.infra import paths, sessiontoken  # noqa: E402
from handline.session import term as termmod  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _task(title: str, owner: str = "worker", by: str = "manager") -> int:
    ok, msg, tid = actions.task_new(by, owner, title, f"{title}: do it and show the result", timeout_s=3600)
    assert ok, msg
    return tid


def test_task_done_sends_the_commit_and_shots_and_task_show_prints_them(home: Path) -> None:
    """The daemon that keeps them is Go's, so the client's call and write stand in for it here."""
    from handline.cli import client as cli_client
    support._board_world(home)
    sent, got = [], {"id": 7, "title": "Fix it", "state": "done", "to": "worker", "from": "manager", "priority": 2, "parent": None,
                     "created": "2026-10-01T10:00:00Z", "time": None, "evidence": "abc123", "done": "2026-10-02T11:30:00Z",
                     "body": "b", "memos": [], "result": {"commit": "abc1234def", "on_main": True,
                                                          "shots": [{"n": 1, "bytes": 2048}]}}
    real = cli_client.write, cli_client.call
    cli_client.write = lambda method, params: (sent.append((method, params)), (True, "done"))[1]
    cli_client.call = lambda method, params: got
    try:
        rc, out = support._cli(["task", "done", "T7", "w", "--repo", "/r", "--commit", "abc1234", "--shot", "a/../b.png",
                                "--shot", "/s/c.png"], as_agent="worker")
        rc2, shown = support._cli(["task", "show", "T7"])
    finally:
        cli_client.write, cli_client.call = real
    check("task done --commit/--shot: sent as given, shots made absolute",
          rc == 0 and sent == [("task.done", {"task": 7, "repo": "/r", "evidence": "w", "commit": "abc1234",
                                              "shots": [os.path.abspath("b.png"), "/s/c.png"]})], str(sent))
    check("task show: the commit, where main stands and each shot's file",
          rc2 == 0 and "commit abc1234def (on main)\n" in shown
          and f"shot {paths.home() / 'shots' / 'T7' / '1.png'} (2048 bytes)\n" in shown, shown)


def main() -> int:
    def tests(td):
        run(lambda: test_task_done_sends_the_commit_and_shots_and_task_show_prints_them(Path(td) / "r10"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
