"""Tests: trust.propose, trust.list and trust.remove (contract/protocol.md): the store's own calls and words through the socket, who may call
which, and the fixtures.

Run on its own: python3 tests/daemon/test_rpc_trust.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.daemon import rpc_trust  # noqa: E402
from handline.team import ask as askmod  # noqa: E402
from handline.team import trust  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc_tasks import _call  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def _world(home: Path) -> None:
    """Two agents who may ask the founder, which a trust proposal is."""
    use_home(home)
    hand_edit_team('agents:\n  manager: {}\n  worker: {}\nedges:\n  - { from: "*", to: "human", kind: task }\n')
    for n in ("manager", "worker"):
        agents.register(n)


def _code(r) -> str:
    return r["error"]["data"]["code"]


def test_an_agent_proposes_and_the_founder_declares_as_the_commands_do(home: Path) -> None:
    _world(home)
    repo = home / "repos" / "widget"
    repo.mkdir(parents=True)
    r = _call("trust.propose", {"root": str(repo), "why": "it builds there", "request_id": "t1"}, as_agent="manager")
    res = r.get("result", {})
    props = trust.listing()
    check("propose: an agent's raises a proposal for the founder; the answer names it", props and props[0]["id"] == res.get("proposal") and props[0]["requested_by"] == "manager"
          and not trust.list_roots(), str(r))
    r = _call("trust.propose", {"root": str(repo), "request_id": "t0"}, as_agent="manager")
    check("propose: no why is the store's refusal", _code(r) == "refused" and "say why" in r["error"]["data"]["reason"], str(r))
    r = _call("trust.propose", {"root": str(repo), "request_id": "t2"}, as_agent=None)
    check("propose: the founder's declares the root now, why defaulted, and answers no proposal",
          r["result"]["accepted"] and "proposal" not in r["result"] and trust.is_declared(repo) and trust.list_roots()[0]["why"] == "added by the founder", str(r))
    r = _call("trust.propose", {"root": str(repo / "sub"), "why": "w", "request_id": "t3"}, as_agent="manager")
    check("propose: a root inside a declared one is refused with the store's words", _code(r) == "refused" and "already inside a declared root" in r["error"]["data"]["reason"], str(r))
    device = SimpleNamespace(caller=SimpleNamespace(role="member", actor=askmod.HUMAN, writer="d"))
    try:
        rpc_trust._propose(device, {"root": "/elsewhere", "why": "w", "request_id": "t4"})
        got = "accepted"
    except Exception as e:      # noqa: BLE001
        got = getattr(e, "data", {}).get("gate")
    check("propose: a paired device is refused, gate role, and nothing is declared", got == "role" and len(trust.list_roots()) == 1, str(got))


def test_a_root_that_is_not_an_absolute_path_is_refused_before_the_store_reads_it(home: Path) -> None:
    _world(home)
    for method, who in (("trust.propose", "manager"), ("trust.propose", None), ("trust.remove", None)):
        for root in (".", "sub", "../x", "~/widget"):
            r = _call(method, {"root": root, "why": "w", "request_id": f"rel-{method}-{who}-{root}"}, as_agent=who)
            check(f"{method} as {who or 'founder'}: {root!r} is bad_request naming root", _code(r) == "bad_request" and r["error"]["data"].get("field") == "root", str(r))
    check("a relative root declared nothing and raised nothing", not trust.list_roots() and not trust.listing())


def test_list_shows_the_roots_and_with_proposals_the_proposals(home: Path) -> None:
    _world(home)
    trust.enact(str(home / "a"), "first")
    ok, said = trust.propose(str(home / "b"), "second", "manager")
    r = _call("trust.list", {}, as_agent="manager")["result"]
    check("list: roots, the same with why and who, and no proposals unless asked",
          r["roots"] == [str((home / "a").resolve())] and r["declared"][0]["why"] == "first" and r["declared"][0]["added_by"] == askmod.HUMAN and r["proposals"] == [], str(r))
    r = _call("trust.list", {"proposals": True}, as_agent="manager")["result"]
    p = trust.listing()[0]
    check("list: with proposals, each as the command prints it: id, state, request, root, who",
          r["proposals"] == [{"id": p["id"], "root": p["root"], "state": "proposed", "request": p["request"], "requested_by": "manager"}], str(r))


def test_remove_is_the_founders_and_says_what_the_command_says(home: Path) -> None:
    _world(home)
    root = home / "a"
    trust.enact(str(root), "first")
    r = _call("trust.remove", {"root": str(root), "request_id": "r1"}, as_agent="manager")
    check("remove: an agent is refused, gate role, and the root stays", _code(r) == "refused" and r["error"]["data"]["gate"] == "role" and trust.is_declared(root), str(r))
    r = _call("trust.remove", {"root": str(root), "request_id": "r2"}, as_agent=None)
    check("remove: the founder's drops it, with the store's sentence", r["result"]["said"] == f"{root.resolve()} is no longer a declared root" and not trust.is_declared(root), str(r))
    r = _call("trust.remove", {"root": str(root), "request_id": "r3"}, as_agent=None)
    check("remove: a root not declared is not_found, with the store's words", _code(r) == "not_found" and "is not a declared root" in r["error"]["message"], str(r))


def test_the_trust_fixtures_pass_against_this_daemon(home: Path) -> None:
    import socket
    import threading
    from handline.daemon import socket_server
    from handline.infra import sessiontoken
    sys.path.insert(0, str(ROOT / "contract"))
    import conformance
    from test_rpc import _hp
    _world(home)
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: None

    def connect():
        a, b = socket.socketpair()
        threading.Thread(target=socket_server._serve_one, args=(b, lambda m: None), daemon=True).start()
        return a
    try:
        results = conformance.run_all([ROOT / "contract" / "fixtures" / "trust.json"], {"founder": connect}, {"founder_key": _hp()["key"]})
    finally:
        sessiontoken.agent_of = real
    bad = [(n, why) for s, why, n in results if s not in ("pass", "skip")]
    check("fixtures: every trust fixture passes against the Python daemon (the agent one needs an agent socket)", results and not bad, str(bad))


def main() -> int:
    def tests(td):
        run(lambda: test_an_agent_proposes_and_the_founder_declares_as_the_commands_do(Path(td) / "t1"))
        run(lambda: test_list_shows_the_roots_and_with_proposals_the_proposals(Path(td) / "t2"))
        run(lambda: test_remove_is_the_founders_and_says_what_the_command_says(Path(td) / "t3"))
        run(lambda: test_a_root_that_is_not_an_absolute_path_is_refused_before_the_store_reads_it(Path(td) / "t5"))
        run(lambda: test_the_trust_fixtures_pass_against_this_daemon(Path(td) / "t4"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
