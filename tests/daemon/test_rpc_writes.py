"""Tests: what every write method gets from the connection (contract/protocol.md, "Request id" and
"Roles"): a request_id, a reply kept a day and replayed, the actor on its event, whoami, and not_self.

Run on its own: python3 tests/daemon/test_rpc_writes.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.daemon import rpc  # noqa: E402
from handline.infra import sessiontoken  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _code, _hp  # noqa: E402


class FakeWrite:
    """A write method for the tests: counts its runs, and fails or refuses on demand."""

    def __init__(self, role="agent") -> None:
        self.runs, self.mode, self.pause = 0, None, 0.0
        rpc.METHODS["t.write"] = rpc.Method(self, role, write=True)

    def __call__(self, ctx, params):
        self.runs += 1
        time.sleep(self.pause)
        if self.mode == "refuse":
            raise rpc.refused("not now", gate="state")
        if self.mode == "break":
            raise RuntimeError("x")
        ctx.require_self(params.get("agent"))
        return {"accepted": True, "run": self.runs}

    def done(self) -> None:
        del rpc.METHODS["t.write"]


def _hello(w):
    w.call("hello", _hp())


def _as_agent(name):
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: name
    return real


def test_a_write_needs_a_request_id_and_a_retry_gets_the_first_reply(home: Path) -> None:
    use_home(home)
    f = FakeWrite()
    try:
        w = Wire()
        _hello(w)
        r = w.call("t.write", {"x": 1})
        check("request id: a write without one is bad_request naming the field",
              _code(r) == "bad_request" and r["error"]["data"]["field"] == "request_id" and f.runs == 0, str(r))
        first = w.call("t.write", {"x": 1, "request_id": "r1"})
        again = w.call("t.write", {"request_id": "r1", "x": 1}, id=2)
        check("request id: the first call runs and its reply is not marked", first["result"] == {"accepted": True, "run": 1}, str(first))
        check("request id: a retry returns the first reply, marked replayed, and repeats nothing",
              again["result"] == {"accepted": True, "run": 1, "replayed": True} and f.runs == 1, str(again))
        other = w.call("t.write", {"x": 2, "request_id": "r1"})
        check("request id: the same id with other params is bad_request", _code(other) == "bad_request" and f.runs == 1, str(other))
        w.close()
        w = Wire()
        _hello(w)
        check("request id: it holds on another connection too", w.call("t.write", {"x": 1, "request_id": "r1"})["result"]["replayed"])
        w.close()
    finally:
        f.done()


def test_refusals_and_errors_are_not_kept_so_a_retry_is_judged_again(home: Path) -> None:
    use_home(home)
    f = FakeWrite()
    try:
        w = Wire()
        _hello(w)
        f.mode = "refuse"
        r = w.call("t.write", {"request_id": "r2"})
        check("request id: a refusal reaches the caller", _code(r) == "refused" and r["error"]["data"]["gate"] == "state", str(r))
        f.mode = "break"
        check("request id: so does an internal error", _code(w.call("t.write", {"request_id": "r2"})) == "internal")
        f.mode = None
        r = w.call("t.write", {"request_id": "r2"})
        check("request id: the retry then runs, since nothing was kept", r["result"]["run"] == 3 and "replayed" not in r["result"], str(r))
        w.close()
    finally:
        f.done()


def test_a_reply_is_kept_a_day_and_no_longer(home: Path) -> None:
    use_home(home)
    f = FakeWrite()
    try:
        w = Wire()
        _hello(w)
        w.call("t.write", {"request_id": "r3"})
        con = db.connect()
        con.execute("UPDATE rpc_replies SET ts = ?", (int(time.time()) - rpc.REPLY_KEEP_S - 5,))
        con.close()
        r = w.call("t.write", {"request_id": "r3"})
        check("request id: past a day the id is free again and the write runs", r["result"]["run"] == 2 and f.runs == 2, str(r))
        con = db.connect()
        check("request id: and the old row is gone", con.execute("SELECT COUNT(*) FROM rpc_replies").fetchone()[0] == 1)
        con.close()
        w.close()
    finally:
        f.done()


def test_retries_at_once_run_the_write_once(home: Path) -> None:
    use_home(home)
    f = FakeWrite()
    f.pause = 0.3
    replies = []
    try:
        hp = _hp()
        def go():
            w = Wire()
            w.call("hello", hp)
            replies.append(w.call("t.write", {"request_id": "r4"}))
            w.close()
        threads = [threading.Thread(target=go) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        f.done()
    check("request id: six retries at once run the write once", f.runs == 1, str(f.runs))
    check("request id: and all six get the same reply", len(replies) == 6 and
          all(r["result"]["run"] == 1 for r in replies), str(replies))


def test_every_write_leaves_an_event_naming_its_actor(home: Path) -> None:
    use_home(home)
    f = FakeWrite()
    try:
        w = Wire()
        _hello(w)
        w.call("t.write", {"request_id": "a1"})
        w.close()
        real = _as_agent("backend")
        try:
            w = Wire()
            _hello(w)
            w.call("t.write", {"agent": "backend", "request_id": "a2"})
            w.call("t.write", {"agent": "backend", "request_id": "a2"})        # a replay writes no second event
            w.close()
        finally:
            sessiontoken.agent_of = real
    finally:
        f.done()
    con = db.connect()
    rows = {json.loads(r["payload"])["request_id"]: r for r in con.execute("SELECT * FROM events WHERE kind = 'rpc_write'")}
    con.close()
    check("actor: the founder's write names the user and is made as human",
          rows["a1"]["agent"] == db.HUMAN and json.loads(rows["a1"]["payload"])["actor"]["kind"] == "person", str(dict(rows["a1"])))
    check("actor: an agent's write names the agent",
          rows["a2"]["agent"] == "backend" and json.loads(rows["a2"]["payload"])["actor"] == {"kind": "agent", "name": "backend"})
    check("actor: one event per write, not per retry", len(rows) == 2)


def test_an_agent_cannot_write_as_another_agent_and_the_founder_can_name_any(home: Path) -> None:
    use_home(home)
    f = FakeWrite()
    try:
        real = _as_agent("backend")
        try:
            w = Wire()
            _hello(w)
            r = w.call("t.write", {"agent": "frontend", "request_id": "n1"})
            ok = w.call("t.write", {"agent": "backend", "request_id": "n2"})
            w.close()
        finally:
            sessiontoken.agent_of = real
        w = Wire()
        _hello(w)
        founder = w.call("t.write", {"agent": "frontend", "request_id": "n3"})
        w.close()
    finally:
        f.done()
    check("not_self: another agent's name is refused at the not_self gate", _code(r) == "refused"
          and r["error"]["data"]["gate"] == "not_self", str(r))
    check("not_self: its own name is served", "result" in ok)
    check("not_self: the founder may name any agent", "result" in founder)


def test_whoami_says_who_and_what_they_may_call(home: Path) -> None:
    use_home(home)
    f = FakeWrite(role="founder")
    try:
        real = _as_agent("backend")
        try:
            w = Wire()
            check("whoami: before hello it is bad_request like any call", _code(w.call("whoami", {})) == "bad_request")
            _hello(w)
            r = w.call("whoami", {})["result"]
            w.close()
        finally:
            sessiontoken.agent_of = real
    finally:
        f.done()
    allowed = {m["name"]: m["allowed"] for m in r["methods"]}
    check("whoami: the caller and role", r["role"] == "agent" and r["caller"] == {"kind": "agent", "name": "backend"}, str(r))
    check("whoami: an agent may call whoami and hello, not a founder's write",
          allowed["whoami"] and allowed["hello"] and not allowed["t.write"], str(allowed))


def test_two_callers_may_use_the_same_request_id_and_each_gets_its_own_reply(home: Path) -> None:
    use_home(home)
    f = FakeWrite()
    try:
        w = Wire()
        _hello(w)
        founder = w.call("t.write", {"request_id": "same"})
        w.close()
        real = _as_agent("backend")
        try:
            w = Wire()
            _hello(w)
            agent = w.call("t.write", {"agent": "backend", "request_id": "same"})
            w.close()
        finally:
            sessiontoken.agent_of = real
    finally:
        f.done()
    check("request id: an agent and the founder using one id both run", f.runs == 2, str(f.runs))
    check("request id: and neither gets the other's reply",
          founder["result"] == {"accepted": True, "run": 1} and agent["result"] == {"accepted": True, "run": 2}, str((founder, agent)))


def test_a_database_made_before_the_reply_store_gets_it_at_migration(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    p = home / "v1.db"
    con = db.connect(p)
    con.execute("DROP TABLE rpc_replies")
    con.execute("DELETE FROM schema_version WHERE version = 2")
    con.close()
    con = db.connect(p, migrating=True)
    has = con.execute("SELECT 1 FROM sqlite_master WHERE name = 'rpc_replies'").fetchone()
    check("store: a version-1 database gets the table from migration 2", has is not None and db.schema_version(con) == db.LATEST_VERSION)
    con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_a_write_needs_a_request_id_and_a_retry_gets_the_first_reply(Path(td) / "w1"))
        run(lambda: test_refusals_and_errors_are_not_kept_so_a_retry_is_judged_again(Path(td) / "w2"))
        run(lambda: test_a_reply_is_kept_a_day_and_no_longer(Path(td) / "w3"))
        run(lambda: test_retries_at_once_run_the_write_once(Path(td) / "w4"))
        run(lambda: test_every_write_leaves_an_event_naming_its_actor(Path(td) / "w5"))
        run(lambda: test_an_agent_cannot_write_as_another_agent_and_the_founder_can_name_any(Path(td) / "w6"))
        run(lambda: test_whoami_says_who_and_what_they_may_call(Path(td) / "w7"))
        run(lambda: test_two_callers_may_use_the_same_request_id_and_each_gets_its_own_reply(Path(td) / "w9"))
        run(lambda: test_a_database_made_before_the_reply_store_gets_it_at_migration(Path(td) / "w8"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
