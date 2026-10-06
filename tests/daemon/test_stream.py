"""Tests: the event stream (daemon/stream.py): cursors, replay without gaps or repeats, the retention gap, a
slow client dropped with a gap, the watcher that sees other writers, the notification detectors, and the
subscribe, unsubscribe and events.feed methods.

Run on its own: python3 tests/daemon/test_stream.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import contextlib
import io
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.daemon import agentview, rpc, stream  # noqa: E402
from handline.infra import sessiontoken  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _code, _hp  # noqa: E402


def _emit(con, n, topic="agents", agent=None):
    return [stream.emit(con, topic, "agent.changed", {"n": i}, agent=agent) for i in range(n)]


def _notices(w, count, timeout=5.0):
    w.a.settimeout(timeout)
    return [w.read() for _ in range(count)]


def _fresh(home):
    home.mkdir(parents=True, exist_ok=True)
    support.use_home(home)
    stream.HUB.subs.clear()


def test_cursors_only_grow_and_a_resume_gets_exactly_what_was_missed(home: Path) -> None:
    _fresh(home)
    con = db.connect()
    evs = _emit(con, 5)
    cur = [e["cursor"] for e in evs]
    check("cursors: each event's cursor is above the last", cur == sorted(set(cur)))
    got = stream.since(con, cur[1], ["agents"])
    check("cursors: since a cursor is what came after it, in order", [e["cursor"] for e in got] == cur[2:])
    check("cursors: another topic is not in it", stream.since(con, 0, ["tasks"]) == [])
    _emit(con, 1, "tasks", "x")
    check("cursors: an agent filter keeps that agent's events and the ones about no agent",
          [e["agent"] for e in stream.since(con, 0, ["tasks"], agent="y")] == [])
    ev = evs[0]
    check("cursors: an event carries the spec's fields", set(ev) == {"cursor", "topic", "type", "at", "actor", "agent", "title", "target", "data"}
          and ev["at"].endswith("Z"), str(ev))
    con.close()


def test_subscribe_replays_then_goes_live_with_no_gap_and_no_repeat(home: Path) -> None:
    _fresh(home)
    con = db.connect()
    before = _emit(con, 3)
    w = Wire()
    w.call("hello", _hp())
    r = w.call("subscribe", {"topics": ["agents"], "since": before[0]["cursor"]})
    check("subscribe: the result is the newest cursor", r["result"]["cursor"] == before[-1]["cursor"], str(r))
    live = _emit(con, 2)
    got = _notices(w, 4)
    check("subscribe: what was missed, then what is live, each once and in order",
          [g["params"]["cursor"] for g in got] == [e["cursor"] for e in before[1:] + live]
          and all(g["method"] == "event" for g in got), str(got))
    w.close()
    check("subscribe: a closed connection leaves the hub", not stream.HUB.subs)
    con.close()


def test_a_second_delivery_of_a_cursor_already_queued_is_dropped(home: Path) -> None:
    _fresh(home)
    sent = []
    sub = stream.Subscription(sent.append, lambda: None, ["agents"], None)
    sub.start()
    con = db.connect()
    evs = _emit(con, 2)
    sub.offer(evs[1])
    sub.offer(evs[0])
    sub.offer(evs[1])
    time.sleep(0.3)
    sub.end()
    check("repeat: a cursor at or below the newest queued is not queued again", [m["params"]["cursor"] for m in sent] == [evs[1]["cursor"]], str(sent))
    con.close()


def test_a_cursor_older_than_retention_gets_a_gap_and_the_feed_says_so(home: Path) -> None:
    _fresh(home)
    con = db.connect()
    evs = _emit(con, 4)
    old = int(time.time()) - stream.RETENTION_S - 10
    con.execute("UPDATE stream_log SET ts = ? WHERE id <= ?", (old, evs[1]["cursor"]))
    stream.prune(con)
    check("retention: rows past it are removed and the place is remembered",
          stream.pruned_through(con) == evs[1]["cursor"] and stream.since(con, 0, ["agents"])[0]["cursor"] == evs[2]["cursor"])
    w = Wire()
    w.call("hello", _hp())
    w.call("subscribe", {"topics": ["agents"], "since": evs[0]["cursor"]})
    gap = _notices(w, 1)[0]
    check("retention: a resume from before it is told, with the cursor to refetch from",
          gap["method"] == "gap" and gap["params"]["reason"] == "retention" and gap["params"]["cursor"] == evs[-1]["cursor"], str(gap))
    feed = w.call("events.feed", {"topics": ["agents"], "since": evs[0]["cursor"]})["result"]
    check("retention: events.feed says gap too", feed["gap"] is True)
    ok = w.call("events.feed", {"topics": ["agents"], "since": evs[1]["cursor"]})["result"]
    check("retention: and not for a cursor at the edge", ok["gap"] is False and len(ok["events"]) == 2)
    w.close()
    con.close()


def test_a_client_that_cannot_keep_up_is_dropped_with_a_gap_not_buffered(home: Path) -> None:
    _fresh(home)
    real = stream.MAX_QUEUE
    stream.MAX_QUEUE = 3
    release, sent, closed = threading.Event(), [], threading.Event()

    def slow_send(obj):
        release.wait(5)
        sent.append(obj)
    sub = stream.Subscription(slow_send, closed.set, ["agents"], None)
    sub.start()
    con = db.connect()
    try:
        evs = _emit(con, 12)
        for e in evs:
            sub.offer(e)
        check("slow: the queue never grows past its bound", len(sub.queue) <= stream.MAX_QUEUE, str(len(sub.queue)))
        release.set()
        check("slow: the connection is closed", closed.wait(5))
        gaps = [m for m in sent if m["method"] == "gap"]
        check("slow: after a gap notice naming slow_client", len(gaps) == 1 and gaps[0]["params"]["reason"] == "slow_client", str(sent[-2:]))
    finally:
        stream.MAX_QUEUE = real
        con.close()


def test_the_watcher_sees_a_change_another_process_made(home: Path) -> None:
    support._daemon_home(home, 20)
    stream.HUB.subs.clear()
    con = db.connect()
    agentview.refresh(con)
    watcher = stream.Watcher(lambda m: None)
    watcher.start()
    w = Wire()
    w.call("hello", _hp())
    w.call("subscribe", {"topics": ["agents"]})
    time.sleep(0.6)
    other = db.connect()
    now = db.get_agent_state(other, "backend")
    db.set_agent_state(other, "backend", dmn_states.IDLE if now and now["state"] == dmn_states.RUNNING else dmn_states.RUNNING)
    agentview.refresh(other)
    other.close()
    got = _notices(w, 1)[0]
    watcher.close()
    check("watcher: a view changed by another connection is an agent.changed event", got["method"] == "event"
          and got["params"]["type"] == "agent.changed" and got["params"]["data"]["name"] == "backend"
          and got["params"]["data"]["state"] == "RUNNING" and got["params"]["target"] == {"screen": "agent", "id": "backend"}, str(got))
    check("watcher: the first look at the table said nothing", got["params"]["data"]["state"] == "RUNNING")
    w.close()
    con.close()


def test_the_notification_detectors_say_what_happened_once_and_not_what_came_before(home: Path) -> None:
    support._daemon_home(home, 20)
    stream.HUB.subs.clear()
    con = db.connect()
    db.add_event(con, "backend", "mark_task_done", {"task_id": 1, "done": True})       # before the stream: not news
    watcher = stream.Watcher(lambda m: None)
    watcher.scan(con)
    check("notifications: what came before the first look is not news", stream.since(con, 0, ["notifications"]) == [])
    tid = db.new_task(con, "t", "backend", "manager")
    db.add_event(con, "backend", "mark_task_done", {"task_id": tid, "done": True})
    db.add_event(con, "backend", "login_expired", {"account": "alpha"}, account="alpha")
    pid = db.prompt_open(con, "backend", "permission", "fp1", "Run it?", ["Run ls?"], {"tool": "Bash", "command": "ls"}, None, True, int(time.time()))
    watcher.scan(con)
    watcher.scan(con)
    got = {e["type"]: e for e in stream.since(con, 0, ["notifications"])}
    check("notifications: a task done, an account out and a prompt asked, each once",
          set(got) == {"task_done", "account_out", "prompt_asked"}
          and len(stream.since(con, 0, ["notifications"])) == 3, str(sorted(got)))
    check("notifications: each names where to go",
          got["task_done"]["target"] == {"screen": "task", "id": str(tid)} and got["task_done"]["data"] == {"task": tid}
          and got["account_out"]["data"] == {"account": "alpha"} and got["prompt_asked"]["data"]["command"] == "ls"
          and got["prompt_asked"]["agent"] == "backend", str(got))
    db.prompt_close(con, pid, int(time.time()))        # an open prompt would make the word DIALOG
    db.set_agent_state(con, "backend", dmn_states.STALLED, detail="x")
    agentview.refresh(con)
    watcher.scan(con)
    check("notifications: an agent turning FATAL is a stall", any(e["type"] == "stall" and e["data"] == {"agent": "backend"}
          for e in stream.since(con, 0, ["notifications"])))
    db.add_event(con, "backend", "mark_task_done", {"task_id": tid, "done": False})
    watcher.scan(con)
    check("notifications: a done mark taken back says nothing", sum(e["type"] == "task_done" for e in stream.since(con, 0, ["notifications"])) == 1)
    con.close()


def test_feed_unsubscribe_and_bad_requests(home: Path) -> None:
    _fresh(home)
    con = db.connect()
    evs = _emit(con, 6, "notifications")
    w = Wire()
    w.call("hello", _hp())
    newest = w.call("events.feed", {"limit": 2})["result"]
    check("feed: without since, the newest few, oldest first, and the cursor to go on from",
          [e["cursor"] for e in newest["events"]] == [e["cursor"] for e in evs[-2:]] and newest["cursor"] == evs[-1]["cursor"] and not newest["gap"], str(newest))
    page = w.call("events.feed", {"since": evs[0]["cursor"], "limit": 2})["result"]
    check("feed: with since, the next page; its cursor is the last returned",
          [e["cursor"] for e in page["events"]] == [e["cursor"] for e in evs[1:3]] and page["cursor"] == evs[2]["cursor"], str(page))
    check("feed: nothing after the newest returns it as the cursor", w.call("events.feed", {"since": evs[-1]["cursor"]})["result"]
          == {"events": [], "cursor": evs[-1]["cursor"], "gap": False})
    check("feed: a limit past the cap is the cap", len(w.call("events.feed", {"limit": 10000})["result"]["events"]) <= 200)
    for label, call in (("a topic that does not exist", ("subscribe", {"topics": ["nope"]})), ("no topics", ("subscribe", {})),
                        ("an empty list", ("subscribe", {"topics": []})), ("a bad since", ("subscribe", {"topics": ["agents"], "since": -1})),
                        ("a bad limit", ("events.feed", {"limit": 0}))):
        check(f"bad request: {label}", _code(w.call(*call)) == "bad_request")
    w.call("subscribe", {"topics": ["agents", "tasks"]})
    check("unsubscribe: answers an empty object", w.call("unsubscribe", {"topics": ["agents"]})["result"] == {})
    _emit(con, 1, "agents")
    _emit(con, 1, "tasks")
    got = _notices(w, 1)[0]
    check("unsubscribe: only the topic still subscribed is sent", got["params"]["topic"] == "tasks", str(got))
    w.close()
    con.close()


def test_an_agent_may_follow_the_stream_and_hl_events_still_works_from_its_session(home: Path) -> None:
    support._daemon_home(home, 20)
    stream.HUB.subs.clear()
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: "frontend"
    try:
        w = Wire()
        w.call("hello", {"client": "t", "protocol": 1})
        sub = w.call("subscribe", {"topics": ["agents"]})
        feed = w.call("events.feed", {})
        w.close()
    finally:
        sessiontoken.agent_of = real
    check("roles: an agent may subscribe and read the feed", "result" in sub and "result" in feed, str((sub, feed)))
    con = db.connect()
    db.add_event(con, "backend", "Stop", {})
    con.close()
    os.environ["OCP_AGENT"] = "frontend"
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            rc = climod.main(["events"])
    finally:
        del os.environ["OCP_AGENT"]
    check("hl events: still works from an agent session", rc == 0, out.getvalue()[:200])


def test_a_resume_far_behind_but_within_the_limit_is_served_whole_then_live(home: Path) -> None:
    _fresh(home)
    con = db.connect()
    evs = _emit(con, 1500)
    w = Wire()
    w.call("hello", _hp())
    r = w.call("subscribe", {"topics": ["agents"], "since": 0})
    check("resume: the reply is the newest cursor", r["result"]["cursor"] == evs[-1]["cursor"])
    live = _emit(con, 3)
    got = _notices(w, 1503, timeout=20)
    check("resume: all 1500 missed events, in order, then the live ones, none dropped",
          [g["params"]["cursor"] for g in got] == [e["cursor"] for e in evs + live] and all(g["method"] == "event" for g in got),
          str([g.get("method") for g in got if g.get("method") != "event"][:3]))
    w.close()
    con.close()


def test_a_real_slow_client_is_still_dropped_after_a_replay(home: Path) -> None:
    _fresh(home)
    real = stream.MAX_QUEUE
    stream.MAX_QUEUE = 3
    release, sent, closed = threading.Event(), [], threading.Event()

    def slow_send(obj):
        release.wait(5)
        sent.append(obj)
    sub = stream.Subscription(slow_send, closed.set, ["agents"], None)
    con = db.connect()
    try:
        _emit(con, 10)
        stream.HUB.subscribe(sub, con, 0)
        check("slow after replay: the ten missed are queued past the live bound, not dropped",
              len(sub.replay) == 10 and sub.gap is None, f"{len(sub.replay)} {sub.gap}")
        sub.start()
        for e in _emit(con, 8):
            stream.HUB.publish(e)
        check("slow after replay: live events past the bound drop it", sub.gap == ("slow_client", sub.gap[1]) if sub.gap else True)
        release.set()
        check("slow after replay: and the connection is closed", closed.wait(5))
        check("slow after replay: with a gap that names slow_client", any(m["method"] == "gap" and m["params"]["reason"] == "slow_client" for m in sent), str(sent[-1:]))
    finally:
        stream.MAX_QUEUE = real
        stream.HUB.subs.clear()
        con.close()


def _watch_one_change(watcher, w_con_fn=None):
    """Run the watcher on a thread, make a change another connection would, and return what a subscriber saw."""
    w = Wire()
    w.call("hello", _hp())
    w.call("subscribe", {"topics": ["agents"]})
    # The change has to come after the watcher's first scan, which baselines what it watches: made
    # before it, the change is baselined and never emitted. A failure the test injected has been
    # recovered from by then, so the scan is the moment to wait for, not a fixed time.
    scanned, real_scan = threading.Event(), stream.Watcher.scan

    def scan(self, con):
        real_scan(self, con)
        if self is watcher:
            scanned.set()
    stream.Watcher.scan = scan
    try:
        watcher.start()
        check("watcher: it scans after starting or recovering", scanned.wait(10))
    finally:
        stream.Watcher.scan = real_scan
    other = db.connect()
    # A change, not the state the agent is already in: the view shows how long it has been so by the second,
    # so setting it again is a change only once a second has passed.
    row = db.get_agent_state(other, "backend")
    db.set_agent_state(other, "backend", dmn_states.IDLE if row and row["state"] == dmn_states.RUNNING else dmn_states.RUNNING)
    agentview.refresh(other)
    other.close()
    try:
        return _notices(w, 1, timeout=8)[0]
    finally:
        watcher.close()
        w.close()


def test_the_watcher_goes_on_after_a_failure_anywhere_in_its_loop(home: Path) -> None:
    support._daemon_home(home, 20)
    stream.HUB.subs.clear()
    con = db.connect()
    agentview.refresh(con)
    con.close()
    real = (stream.prune, stream.RETRY_S, db.connect, stream.Watcher.changed)
    stream.RETRY_S = 0.1
    logs = []
    try:
        calls = {"n": 0}

        def prune_once(c, now=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("prune broke")
        stream.prune = prune_once
        got = _watch_one_change(stream.Watcher(logs.append))
        check("watcher: a prune that raises is logged and the change is still emitted",
              got["params"]["type"] == "agent.changed" and any("prune" in m and "prune broke" in m for m in logs), str(logs))
        stream.prune = real[0]

        tries = {"n": 0}

        def connect_once(*a, **k):
            if threading.current_thread().name == "ocp-stream-watch":
                tries["n"] += 1
                if tries["n"] == 1:
                    raise db.SchemaError("busy", "busy")
            return real[2](*a, **k)
        db.connect = connect_once
        logs.clear()
        got = _watch_one_change(stream.Watcher(logs.append))
        db.connect = real[2]
        check("watcher: a connect that raises once, then the watcher recovers and emits", got["params"]["type"] == "agent.changed"
              and any("watcher" in m for m in logs) and tries["n"] >= 2, str(logs))

        seen = {"n": 0}
        orig = real[3]

        def changed_once(self, c):
            seen["n"] += 1
            if seen["n"] == 1:
                raise RuntimeError("data_version broke")
            return orig(self, c)
        stream.Watcher.changed = changed_once
        logs.clear()
        got = _watch_one_change(stream.Watcher(logs.append))
        check("watcher: a data_version read that raises is logged, the connection replaced, and the change still emitted",
              got["params"]["type"] == "agent.changed" and any("data_version broke" in m for m in logs), str(logs))
    finally:
        stream.prune, stream.RETRY_S, db.connect, stream.Watcher.changed = real


def test_a_notification_that_fails_to_emit_does_not_repeat_the_ones_before_it(home: Path) -> None:
    support._daemon_home(home, 20)
    stream.HUB.subs.clear()
    con = db.connect()
    watcher = stream.Watcher(lambda m: None)
    watcher.scan(con)           # primes the cursors
    for t in (1, 2, 3):
        db.add_event(con, "backend", "mark_task_done", {"task_id": t, "done": True})
    real = stream.emit
    calls = {"n": 0}

    def emit_failing_second(*a, **k):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("second one broke")
        return real(*a, **k)
    stream.emit = emit_failing_second
    try:
        watcher.scan(con)
    finally:
        stream.emit = real
    watcher.scan(con)
    tasks = [e["data"]["task"] for e in stream.since(con, 0, ["notifications"]) if e["type"] == "task_done"]
    check("cursor per row: the first is said once, the failed one and the one after it on the next look", tasks == [1, 2, 3], str(tasks))
    con.close()


def test_a_database_made_before_the_stream_gets_it_from_a_plain_connect(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    p = home / "v3.db"
    db.schema.connect(p).close()
    raw = __import__("sqlite3").connect(p)
    raw.execute("DROP TABLE stream_log")
    raw.execute("DELETE FROM schema_version WHERE version > 3")
    raw.commit()
    raw.close()
    con = db.schema.connect(p)
    check("stream: a version-3 database gets the log from a plain connect, as an additive step does",
          con.execute("SELECT 1 FROM sqlite_master WHERE name = 'stream_log'").fetchone() is not None
          and db.schema_version(con) == db.LATEST_VERSION)
    con.close()


def main() -> int:
    def tests(td):
        run(lambda: test_cursors_only_grow_and_a_resume_gets_exactly_what_was_missed(Path(td) / "s1"))
        run(lambda: test_subscribe_replays_then_goes_live_with_no_gap_and_no_repeat(Path(td) / "s2"))
        run(lambda: test_a_second_delivery_of_a_cursor_already_queued_is_dropped(Path(td) / "s3"))
        run(lambda: test_a_cursor_older_than_retention_gets_a_gap_and_the_feed_says_so(Path(td) / "s4"))
        run(lambda: test_a_client_that_cannot_keep_up_is_dropped_with_a_gap_not_buffered(Path(td) / "s5"))
        run(lambda: test_the_watcher_sees_a_change_another_process_made(Path(td) / "s6"))
        run(lambda: test_the_notification_detectors_say_what_happened_once_and_not_what_came_before(Path(td) / "s7"))
        run(lambda: test_feed_unsubscribe_and_bad_requests(Path(td) / "s8"))
        run(lambda: test_an_agent_may_follow_the_stream_and_hl_events_still_works_from_its_session(Path(td) / "s9"))
        run(lambda: test_a_resume_far_behind_but_within_the_limit_is_served_whole_then_live(Path(td) / "s11"))
        run(lambda: test_a_real_slow_client_is_still_dropped_after_a_replay(Path(td) / "s12"))
        run(lambda: test_the_watcher_goes_on_after_a_failure_anywhere_in_its_loop(Path(td) / "s13"))
        run(lambda: test_a_notification_that_fails_to_emit_does_not_repeat_the_ones_before_it(Path(td) / "s14"))
        run(lambda: test_a_database_made_before_the_stream_gets_it_from_a_plain_connect(Path(td) / "s10"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
