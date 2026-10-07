"""Tests: hr.list, the hr topic and the refusals of hr.decide (contract/protocol.md, Proposal): the proposals as the wire's states in the
order the spec gives, what is left out, the proposal in each refusal, and `hr.raised` / `hr.settled` once each.

Run on its own: python3 tests/daemon/test_rpc_hr.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.daemon import rpc_hr, stream  # noqa: E402
from handline.team import ask as askmod  # noqa: E402
from handline.team import hr  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _code, _hp  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def _raise(kind: str, name: str, who: str = "manager", **kw) -> str:
    ok, said = hr.propose(kind, name, who, kw.pop("why", "needs a hand"), **kw)
    assert ok, said
    return max(hr.listing(), key=lambda p: p.get("created") or 0)["id"] if False else said.split()[1]


def _set(pid: str, **fields) -> None:
    prop = hr.load_proposal(pid)
    prop.update(fields)
    hr.save(prop)


def _founder() -> Wire:
    w = Wire()
    w.call("hello", _hp())
    return w


def _seed(home: Path) -> dict:
    """One open proposal, one rejected, one applied, one apply_failed, one withdrawn, and one the founder made directly."""
    support._board_world(home)
    now = int(time.time())
    ids = {"open": _raise("add", "a.new", repo="/repo", model="sonnet", why="needs a hand"),
           "rej": _raise("add", "a.rej", repo="/repo"), "app": _raise("add", "a.app", repo="/repo"),
           "failed": _raise("add", "a.failed", repo="/repo"), "wd": _raise("add", "a.wd", repo="/repo"),
           "human": _raise("add", "a.human", repo="/repo")}
    _set(ids["open"], created=now - 500)
    _set(ids["rej"], state="rejected", decided=now - 400, created=now - 450)
    _set(ids["app"], state="applied", applied=now - 300, decided=now - 310, created=now - 350)
    _set(ids["failed"], state="approved", error="x" * 500, decided=now - 200, created=now - 480)
    _set(ids["wd"], state="withdrawn", decided=now - 100, created=now - 440)
    _set(ids["human"], requested_by=askmod.HUMAN)
    return ids


def _by(w: Wire, **params) -> list:
    return w.call("hr.list", params)["result"]["proposals"]


def test_the_list_maps_the_states_orders_waiting_first_and_leaves_the_founders_own_out(home: Path) -> None:
    ids = _seed(home)
    w = _founder()
    got = _by(w)
    n = {p["id"]: p for p in got}
    wid = {k: rpc_hr.num(v) for k, v in ids.items()}
    check("list: the store's states are the wire's (proposed open, approved apply_failed, applied approved, rejected, withdrawn)",
          {k: n[wid[k]]["state"] for k in ("open", "rej", "app", "failed", "wd")} == {"open": "open", "rej": "rejected", "app": "approved", "failed": "apply_failed", "wd": "withdrawn"}, str(got))
    check("list: what the founder made directly is not listed", wid["human"] not in n)
    check("list: the waiting come first, oldest created first (the open one is older), then the settled, newest settled first",
          [p["id"] for p in got] == [wid["open"], wid["failed"], wid["wd"], wid["app"], wid["rej"]], str([(p["id"], p["state"]) for p in got]))
    f = n[wid["failed"]]
    check("list: an apply_failed proposal carries its error, cut at 400 characters; the others have none", len(f["error"]) == 400 and n[wid["open"]]["error"] is None)
    o = n[wid["open"]]
    check("list: the shape: title, kind, one-line diff, asked, reason, who asked, subject, created and a null settled while open",
          o["title"] == "add a.new" and o["kind"] == "add" and o["agent"] == "a.new" and o["reason"] == "needs a hand" and o["requested_by"] == "manager"
          and o["asked"] == {"model": "sonnet", "repo": "/repo"} and o["settled"] is None and o["subject"] and o["created"].endswith("Z")
          and "\n" not in o["diff"] and "register a.new" in o["diff"], str(o))
    check("list: a settled one has when", n[wid["rej"]]["settled"].endswith("Z"))
    only = _by(w, state="rejected")
    check("list: with a state only that state", [p["id"] for p in only] == [wid["rej"]])
    check("list: a state nothing has is an empty list", _by(w, state="nope") == [])
    r = w.call("hr.list", {"state": 5})
    check("list: a state that is not text is bad_request naming it", _code(r) == "bad_request" and r["error"]["data"]["field"] == "state", str(r))
    w.close()


def test_recent_settled_are_seven_days_and_fifty_and_a_state_has_no_bound(home: Path) -> None:
    ids = _seed(home)
    now = int(time.time())
    _set(ids["rej"], decided=now - 8 * 24 * 3600)
    for n in range(55):
        pid = _raise("add", f"a.many{n}", repo="/repo")
        _set(pid, state="rejected", decided=now - 10 - n, created=now - 1000)
    w = _founder()
    got = _by(w)
    settled = [p for p in got if p["state"] not in rpc_hr.WAITING]
    check("list: of the settled, the last 7 days only (a week-old one is out) and at most 50", len(settled) == 50 and rpc_hr.num(ids["rej"]) not in [p["id"] for p in settled], str(len(settled)))
    check("list: the waiting are not counted in the 50", sum(p["state"] in rpc_hr.WAITING for p in got) == 2)
    check("list: with a state, no 7-day and no 50 bound", len(_by(w, state="rejected")) == 56)
    w.close()


def test_a_bundle_and_the_kinds_have_titles_and_asked_in_words(home: Path) -> None:
    support._board_world(home)
    ok, said = hr.propose_bundle([{"kind": "add", "name": "b.one", "repo": "/repo"}, {"kind": "link", "name": "manager", "to": "b.one"}], "manager", "grow")
    assert ok, said
    ok, said = hr.propose("settings", "worker", "manager", "talk", sets={"model": "haiku", "effort": "high"})
    assert ok, said
    ok, said = hr.propose("unlink", "manager", "worker", "no more", to="worker")
    assert ok, said
    w = _founder()
    got = {p["kind"]: p for p in _by(w)}
    b = got["bundle"]
    check("bundle: title is `bundle N changes`, agent is `N changes`, no asked, a line a change", b["title"] == "bundle 2 changes" and b["agent"] == "2 changes" and "asked" not in b
          and len(b["diff"].splitlines()) == 2, str(b))
    s = got["settings"]
    check("settings: asked.sets has each, the diff a line each", s["asked"]["sets"] == {"model": "haiku", "effort": "high"} and len(s["diff"].splitlines()) == 2, str(s))
    check("unlink: the title ends with the other end, asked.to has it", got["unlink"]["title"] == "unlink manager -> worker" and got["unlink"]["asked"]["to"] == "worker", str(got["unlink"]))
    w.close()


def test_a_decision_on_a_settled_or_changed_proposal_is_refused_with_it(home: Path) -> None:
    ids = _seed(home)
    w = _founder()
    n = 0

    def decide(key, decision="approve", **kw):
        nonlocal n
        n += 1
        return w.call("hr.decide", {"proposal": rpc_hr.num(ids[key]), "decision": decision, "request_id": f"d{n}", **kw})
    r = decide("rej", subject="not-the-digest")
    check("decide: a rejected one is refused, gate proposal_settled, and the refusal holds it as it is (the settled check comes first)",
          r["error"]["data"].get("gate") == "proposal_settled" and r["error"]["data"]["proposal"]["state"] == "rejected", str(r))
    for key in ("app", "wd"):
        check(f"decide: {key} is settled too", decide(key, "reject")["error"]["data"].get("gate") == "proposal_settled")
    r = decide("open", subject="not-the-digest")
    check("decide: an approve with another subject is refused, gate proposal_changed, holding the proposal as it is",
          r["error"]["data"].get("gate") == "proposal_changed" and r["error"]["data"]["proposal"]["state"] == "open"
          and r["error"]["data"]["proposal"]["subject"] == rpc_hr.wire(hr.load_proposal(ids["open"]))["subject"], str(r))
    check("decide: nothing was decided", hr.load_proposal(ids["open"])["state"] == "proposed")
    r = decide("open", "reject")
    check("decide: a reject of the open one is accepted", r.get("result", {}).get("accepted") is True, str(r))
    w.close()


def test_the_hr_topic_says_a_raise_and_a_settle_once_and_the_first_look_nothing(home: Path) -> None:
    support._board_world(home)
    stream.HUB.subs.clear()
    rpc_hr.reset()
    con = db.connect()
    watcher = stream.Watcher(print)
    first = _raise("add", "a.before", repo="/repo")
    watcher.scan(con)
    check("hr topic: what was there before the first look is not news", stream.since(con, 0, ["hr"]) == [])
    pid = _raise("add", "a.late", repo="/repo")
    watcher.scan(con)
    watcher.scan(con)
    got = stream.since(con, 0, ["hr"])
    check("hr topic: a proposal raised is hr.raised once, with the whole Proposal, its agent, title and target",
          len(got) == 1 and got[0]["type"] == "hr.raised" and got[0]["agent"] == "a.late" and got[0]["title"] == "add a.late"
          and got[0]["target"] == {"screen": "hr", "id": str(rpc_hr.num(pid))} and got[0]["data"]["proposal"]["state"] == "open", str(got))
    _set(pid, state="withdrawn", decided=int(time.time()))
    watcher.scan(con)
    got = stream.since(con, 0, ["hr"])[1:]
    check("hr topic: a withdraw is hr.settled", len(got) == 1 and got[0]["type"] == "hr.settled" and got[0]["data"]["proposal"]["state"] == "withdrawn"
          and got[0]["data"]["proposal"]["settled"], str(got))
    _set(first, state="approved", error="first", decided=int(time.time()))
    watcher.scan(con)
    _set(first, state="approved", error="second", decided=int(time.time()))
    watcher.scan(con)
    got = [e for e in stream.since(con, 0, ["hr"]) if e["data"]["proposal"]["id"] == rpc_hr.num(first)]
    check("hr topic: an apply that failed is settled, and its retry that fails again is settled again with the new error",
          [e["data"]["proposal"]["error"] for e in got] == ["first", "second"] and all(e["data"]["proposal"]["state"] == "apply_failed" for e in got), str(got))
    sig = rpc_hr.signature()
    _raise("add", "a.sig", repo="/repo")
    check("hr topic: the watcher notices a new file without a database change", rpc_hr.signature() != sig)
    con.close()


def test_the_hr_fixtures_pass_against_this_daemon_with_proposals_seeded(home: Path) -> None:
    import socket
    import threading
    from handline.daemon import socket_server
    from handline.infra import sessiontoken
    sys.path.insert(0, str(ROOT / "contract"))
    import conformance
    _seed(home)
    rpc_hr.reset()
    key = "k-founder"
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: None

    def connect():
        a, b = socket.socketpair()
        threading.Thread(target=socket_server._serve_one, args=(b, lambda m: None), daemon=True).start()
        return a
    try:
        results = conformance.run_all([ROOT / "contract" / "fixtures" / "hr.json"], {"founder": connect}, {"founder_key": _hp()["key"], "hr_seeded": "1"})
    finally:
        sessiontoken.agent_of = real
    bad = [(n, why) for s, why, n in results if s != "pass"]
    check("fixtures: every hr fixture passes against the Python daemon with one open and one rejected proposal", results and not bad, str(bad))


def main() -> int:
    def tests(td):
        run(lambda: test_the_list_maps_the_states_orders_waiting_first_and_leaves_the_founders_own_out(Path(td) / "h1"))
        run(lambda: test_recent_settled_are_seven_days_and_fifty_and_a_state_has_no_bound(Path(td) / "h2"))
        run(lambda: test_a_bundle_and_the_kinds_have_titles_and_asked_in_words(Path(td) / "h3"))
        run(lambda: test_a_decision_on_a_settled_or_changed_proposal_is_refused_with_it(Path(td) / "h4"))
        run(lambda: test_the_hr_topic_says_a_raise_and_a_settle_once_and_the_first_look_nothing(Path(td) / "h5"))
        run(lambda: test_the_hr_fixtures_pass_against_this_daemon_with_proposals_seeded(Path(td) / "h6"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
