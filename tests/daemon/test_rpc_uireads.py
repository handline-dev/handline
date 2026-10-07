"""Tests: agent.strip and chat.refs (contract/protocol.md): each answers what its spec says, who may call it, and what a bad
parameter says. The Textual chat page's strip and its `#` menu read them.

Run on its own: python3 tests/daemon/test_rpc_uireads.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.infra import sessiontoken  # noqa: E402
from handline.session import term as termmod  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _code, _hp  # noqa: E402
from test_rpc_lists import RESULT, _call, _invalid  # noqa: E402


def _world(home: Path) -> None:
    support._board_world(home)
    termmod.live_terminals = lambda: set()
    support.write_status(home, "alpha", pct_5h=40, pct_7d=10, ctx_pct=5, reset_5h=int(time.time()) + 1800)
    con = db.connect()
    try:
        db.set_account(con, "worker", "alpha")
        db.set_agent_state(con, "worker", "RUNNING")
    finally:
        con.close()


def test_the_strip_says_the_account_the_cache_and_the_screen_word(home: Path) -> None:
    _world(home)
    r = _call("agent.strip", {"agent": "worker"})
    got = r["result"]
    check("agent.strip: the result is the spec's schema", _invalid(got, RESULT["agent.strip"]) is None, str(_invalid(got, RESULT["agent.strip"])))
    check("agent.strip: the account it is on and the word the screens draw", got["agent"] == "worker" and got["account"] == "alpha"
          and got["screen_word"] == "RUNNING", str(got))
    check("agent.strip: the context limit of the model it would run", isinstance(got["ctx_limit"], int), str(got))
    con = db.connect()
    db.set_account(con, "manager", None)
    con.close()
    bare = _call("agent.strip", {"agent": "manager"})["result"]
    check("agent.strip: with no account the context figures are null, not guessed", bare["account"] is None and bare["model"] is None
          and bare["ctx_pct"] is None and bare["ctx_used"] is None and bare["ctx_limit"] is None, str(bare))
    check("agent.strip: an agent that is not there is not_found", _code(_call("agent.strip", {"agent": "nobody"})) == "not_found")
    check("agent.strip: a missing name is bad_request", _code(_call("agent.strip", {})) == "bad_request")
    r = _call("agent.strip", {"agent": "worker"}, as_agent="worker")
    check("agent.strip: an agent session is refused, gate role", _code(r) == "refused" and r["error"]["data"]["gate"] == "role", str(r))


def test_refs_are_the_agents_newest_requests_and_leave_out_the_withdrawn(home: Path) -> None:
    _world(home)
    _, _, first = askmod.ask("worker", "first line\nsecond line", requester="manager")
    _, _, second = askmod.ask("manager", "a question back", requester="worker")
    _, _, other = askmod.ask("manager", "not about worker", requester="human")
    con = db.connect()
    con.execute("UPDATE requests SET state = 'withdrawn' WHERE id = ?", (second,))
    con.close()
    got = _call("chat.refs", {"agent": "worker"})["result"]
    check("chat.refs: the result is the spec's schema", _invalid(got, RESULT["chat.refs"]) is None, str(_invalid(got, RESULT["chat.refs"])))
    by = {x["request"]: x for x in got["requests"]}
    check("chat.refs: a request it received, who to whom and its first line only", by.get(first, {}).get("from") == "manager"
          and by[first]["to"] == "worker" and by[first]["line"] == "first line", str(got))
    check("chat.refs: a withdrawn one and one of other agents are not there", second not in by and other not in by, str(got))
    check("chat.refs: newest first", [x["request"] for x in got["requests"]] == sorted(by, reverse=True))
    check("chat.refs: an agent that is not there is not_found", _code(_call("chat.refs", {"agent": "nobody"})) == "not_found")
    check("chat.refs: a missing name is bad_request", _code(_call("chat.refs", {})) == "bad_request")


def test_the_ui_read_fixtures_pass_against_this_daemon(home: Path) -> None:
    import socket
    import threading
    from handline.daemon import socket_server
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "contract"))
    import conformance

    def connect():
        a, b = socket.socketpair()
        threading.Thread(target=socket_server._serve_one, args=(b, lambda m: None), daemon=True).start()
        return a
    _world(home)
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: None
    try:
        results = [r for role in ("founder", "anonymous") for r in conformance.run_all(
            [Path("contract/fixtures/ui_reads.json")], {role: connect}, {"founder_key": _hp()["key"]}) if r[0] != "skip"]
    finally:
        sessiontoken.agent_of = real
    bad = [(n, why) for st, why, n in results if st != "pass"]
    check("fixtures: every ui read fixture passes against this daemon", not bad and len({n for _, _, n in results}) == 6, str(bad))


def main() -> int:
    def tests(td):
        run(lambda: test_the_strip_says_the_account_the_cache_and_the_screen_word(Path(td) / "u1"))
        run(lambda: test_refs_are_the_agents_newest_requests_and_leave_out_the_withdrawn(Path(td) / "u2"))
        run(lambda: test_the_ui_read_fixtures_pass_against_this_daemon(Path(td) / "u3"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
