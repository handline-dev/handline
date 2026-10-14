"""Tests: whether an agent is structured is read as the Go daemon reads it (contract/agent-kind-cases.json).
Run on its own: python3 tests/daemon/test_structured_skip.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

CASES = Path(__file__).resolve().parents[2] / "contract" / "agent-kind-cases.json"


def test_the_kind_is_read_as_the_go_daemon_reads_it(home: Path) -> None:
    _daemon_home(home, 20)
    cases = json.loads(CASES.read_text())["cases"]
    bad = []
    for i, c in enumerate(cases):
        agents.register("probe") if "probe" not in paths.list_agents() else None
        agents.agent_file("probe").write_text(c["yaml"])
        if agents.is_structured("probe") != c["structured"]:
            bad.append((i, c["yaml"]))
    check("kind: the shared cases read the same as the Go daemon reads them", not bad, str(bad))
    check("kind: an agent that does not exist is not structured", not agents.is_structured("nobody"))
    agents.agent_file("probe").write_text("structured: true\n")
    check("kind: `driven` leaves the structured agent out", "probe" not in agents.driven() and "backend" in agents.driven())
    check("kind: but the listing still has it, for the UI, hl and reads", "probe" in paths.list_agents())
    cfg = agents.read("backend")
    cfg["mode"] = "plan"
    agents.write("backend", cfg)
    check("kind: a permission mode is not the switch", not agents.is_structured("backend"))


def main() -> int:
    def tests(td):
        run(lambda: test_the_kind_is_read_as_the_go_daemon_reads_it(Path(td) / "k1"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
