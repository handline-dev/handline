"""Tests: the daemon's JSON-RPC connection (contract/protocol.md): framing, hello first, typed errors,
roles, and the legacy `{verb}` adapter that still answers once and hangs up.

Run on its own: python3 tests/daemon/test_rpc.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import socket
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.infra import sessiontoken  # noqa: E402


def _hp(**extra) -> dict:
    """hello params from the founder: the key only the founder's own process can read."""
    sessiontoken.ensure_founder_key()
    return {"client": "t", "protocol": 1, "key": sessiontoken.founder_key_held(), **extra}


def _code(reply) -> str:
    return reply["error"]["data"]["code"]


def test_the_founder_key_is_made_private_in_a_folder_sessions_cannot_read(home: Path) -> None:
    use_home(home)
    check("founder key: none before the daemon makes one", sessiontoken.founder_key_held() is None)
    check("founder key: the daemon makes it", sessiontoken.ensure_founder_key())
    key = sessiontoken.founder_key_held()
    file = sessiontoken.protected.root() / sessiontoken.FOUNDER_NS / "key"
    check("founder key: the file is 0600 and the folder 0700", oct(file.stat().st_mode & 0o777) == "0o600"
          and oct(file.parent.stat().st_mode & 0o777) == "0o700", oct(file.stat().st_mode))
    check("founder key: a second start keeps it", sessiontoken.ensure_founder_key() and sessiontoken.founder_key_held() == key)
    check("founder key: its folder is one a launch hides", file.parent in sessiontoken.hidden_folders())


def main() -> int:
    def tests(td):
        run(lambda: test_the_founder_key_is_made_private_in_a_folder_sessions_cannot_read(Path(td) / "r11"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
