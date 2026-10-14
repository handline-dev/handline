"""Tests: the live runs spend real tokens, so no required check turns them on, and tools/live_account.py picks the account
with the most headroom, ties going to the first name.

Run on its own: tools/test.sh tests/infra/test_live_runs.py   (all areas: tools/suite.sh)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import live_account  # noqa: E402


def test_no_required_check_runs_a_live_test() -> None:
    files = [ROOT / "tools" / f for f in ("suite.sh", "check.sh", "test.sh", "test-go.sh")]
    files += sorted((ROOT / "tools" / "githooks").glob("*")) + sorted((ROOT / ".github" / "workflows").glob("*"))
    for f in files:
        text = f.read_text()
        for bad in ("HANDLINE_LIVE=", "live-e2e", "live_e2e", "live_account"):
            check(f"live runs: {f.relative_to(ROOT)} does not have {bad!r}", bad not in text)
    check("live runs: tools/test-go.sh unsets HANDLINE_LIVE, so an exported one runs no live Go test",
          "unset HANDLINE_LIVE" in (ROOT / "tools" / "test-go.sh").read_text())


def _acct(name: str, *used, login="ok", assignable=True) -> dict:
    return {"name": name, "login_state": login, "assignable": assignable, "assign_reason": "full",
            "windows": [{"effective": u} for u in used]}


def test_the_pick_takes_the_most_headroom_and_breaks_ties_by_name() -> None:
    name, why = live_account.pick([_acct("sigma", 10, 70), _acct("gamma", 40, 20), _acct("beta", 5, 60)])
    check("live account: the fuller window counts, so gamma (60% left) beats sigma (30%) and beta (40%)", name == "gamma", why)
    check("live account: the reason ranks every account", "gamma 60%, beta 40%, sigma 30%" in why, why)
    name, _ = live_account.pick([_acct("sigma", 20), _acct("alpha", 20)])
    check("live account: a tie goes to the name first in order", name == "alpha", name)
    name, why = live_account.pick([_acct("a", 0, login="expired"), _acct("b", 0, assignable=False), _acct("c")])
    check("live account: logged out, unassignable or unread accounts are skipped, and the reason says so",
          name is None and "a not logged in" in why and "b takes no agent" in why and "c has no usage reading" in why, why)


def main() -> int:
    def tests(td):
        run(lambda: test_no_required_check_runs_a_live_test())
        run(lambda: test_the_pick_takes_the_most_headroom_and_breaks_ties_by_name())
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
