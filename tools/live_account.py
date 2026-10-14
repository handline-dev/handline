#!/usr/bin/env python3
"""The account a live run spends: of the accounts the live daemon would give an agent to, the one with the most usage headroom now.

  python3 tools/live_account.py      prints {"account", "home", "config_dir", "why"} as JSON

Headroom is 100 less the fuller of an account's windows, as the daemon acts on them; a tie goes to the name first in order.
It reads the live daemon (accounts.list, which an agent may call) and writes nothing. tools/live_e2e.py and the Go live
tests (go/internal/livetest) both pick through it.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def pick(accounts: list[dict]) -> tuple[str | None, str]:
    """The account with the most headroom and why, or None and why none may be spent."""
    room, out = [], []
    for a in accounts:
        used = [w["effective"] for w in a.get("windows") or [] if w.get("effective") is not None]
        if a.get("login_state") != "ok":
            out.append(f"{a['name']} not logged in")
        elif not a.get("assignable"):
            out.append(f"{a['name']} takes no agent ({a.get('assign_reason')})")
        elif not used:
            out.append(f"{a['name']} has no usage reading")
        else:
            room.append((100 - max(used), a["name"]))
    room.sort(key=lambda r: (-r[0], r[1]))
    skipped = f"; skipped: {', '.join(out)}" if out else ""
    if not room:
        return None, "no account may be spent" + skipped
    ranked = ", ".join(f"{n} {h}%" for h, n in room)
    return room[0][1], f"{room[0][1]} has the most headroom ({ranked}){skipped}"


def pick_live() -> dict:
    """{account, home, config_dir, why} for the account to spend; SystemExit when none may be."""
    os.environ["HANDLINE_ALLOW_LIVE"] = "1"  # it reads the live daemon, which is its job
    from handline.cli import client
    from handline.infra import paths

    name, why = pick(client.call("accounts.list", {"usage": True}, start=False)["accounts"])
    if name is None:
        raise SystemExit(f"live-account: {why}")
    return {"account": name, "home": str(paths.home()), "config_dir": str(paths.account_dir(name) / ".claude"), "why": why}


def main() -> int:
    print(json.dumps(pick_live()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
