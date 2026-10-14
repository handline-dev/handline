"""Tests: the auto-accept setting reads back what was written.

Run on its own: python3 tests/session/test_autoaccept.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.session import autoaccept  # noqa: E402


RULE_TITLE = "Enable auto mode for this session?"


def test_auto_accept_config_round_trips(home: Path) -> None:
    _daemon_home(home, 20)
    out = support._capture(lambda: climod.main(["auto-accept"]))
    check("auto-accept: shows on with nothing set", out.startswith("auto-accept: on") and autoaccept.enabled(), out)
    out = support._capture(lambda: climod.main(["auto-accept", "off"]))
    check("auto-accept: off is set and shown", out.startswith("auto-accept: off") and not autoaccept.enabled(), out)
    out = support._capture(lambda: climod.main(["auto-accept", "on"]))
    check("auto-accept: on again", out.startswith("auto-accept: on") and autoaccept.enabled(), out)


MCP_DIALOG = [
    "  Claude Code", "▔" * 60, "   2 new MCP servers found in .mcp.json",
    "   ❯ 1. Use this and all future MCP servers in this project", "     2. Use this MCP server",
    "     3. Continue without using this MCP server",
    "   Enter to confirm · Esc to cancel"]
MCP_TITLE = "2 new MCP servers found in .mcp.json"


PERMISSION_DIALOG = [
    "  earlier output", "▔" * 60, "   Bash command", "   ❯ 1. Yes", "     2. No",
    "   Enter to confirm · Esc to cancel"]


def main() -> int:
    def tests(td):
        run(lambda: test_auto_accept_config_round_trips(Path(td) / 'aa6'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
