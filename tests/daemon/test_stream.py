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
from handline import actions  # noqa: E402
from handline.daemon import agentview, rpc, stream  # noqa: E402
from handline.infra import sessiontoken  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _code, _hp  # noqa: E402


def _emit(con, n, topic="agents", agent=None):
    return [stream.emit(con, topic, "agent.changed", {"n": i}, agent=agent) for i in range(n)]


def _notices(w, count, timeout=5.0):
    w.a.settimeout(timeout)
    return [w.read() for _ in range(count)]


def _until(pred, secs: float = 30.0) -> bool:
    """Wait on a condition, not for a time: the limit only ends a test that would never finish."""
    end = time.monotonic() + secs
    while not pred():
        if time.monotonic() > end:
            return False
        time.sleep(0.005)
    return True


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
    _until(lambda: sent and not sub.queue)      # offers dedupe as they are made, so what is queued is all there will be: the writer has sent it
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
    scanned, real_scan = threading.Event(), stream.Watcher.scan

    def scan(self, con):
        real_scan(self, con)
        if self is watcher:
            scanned.set()
    stream.Watcher.scan = scan
    try:
        watcher.start()
        w = Wire()
        w.call("hello", _hp())
        w.call("subscribe", {"topics": ["agents"]})
        check("watcher: it has scanned once, which baselines what it watches, before the change", scanned.wait(30))
    finally:
        stream.Watcher.scan = real_scan
    other = db.connect()
    now = db.get_agent_state(other, "backend")
    db.set_agent_state(other, "backend", dmn_states.IDLE if now and now["state"] == dmn_states.RUNNING else dmn_states.RUNNING)
    agentview.refresh(other)
    other.close()
    got = _notices(w, 1)[0]
    watcher.close()
    check("watcher: a view changed by another connection is an agent.changed event", got["method"] == "event"
          and got["params"]["type"] == "agent.changed" and got["params"]["data"]["name"] == "backend"
          and got["params"]["data"]["state"] == "running" and got["params"]["target"] == {"screen": "agent", "id": "backend"}, str(got))
    check("watcher: the first look at the table said nothing", got["params"]["data"]["state"] == "running")
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
    db.add_event(con, "backend", "mark_task_done", {"done": True})
    db.add_event(con, "backend", "mark_task_done", {"task_id": None, "done": True})
    watcher.scan(con)
    said = [e for e in stream.since(con, 0, ["notifications"]) if e["type"] == "task_done"]
    check("notifications: a done mark that names no task says nothing, never a task 'None'", len(said) == 1 and "None" not in json.dumps(said), str(said))
    con.close()


def test_the_tasks_and_inbox_topics_say_what_changed_once_and_not_what_came_before(home: Path) -> None:
    support._daemon_home(home, 20)
    stream.HUB.subs.clear()
    con = db.connect()
    before = db.new_task(con, "old", "backend", "manager")
    watcher = stream.Watcher(lambda m: None)
    watcher.scan(con)
    check("tasks and inbox: what came before the first look is not news", stream.since(con, 0, ["tasks", "inbox"]) == [])
    tid = db.new_task(con, "fresh", "backend", "manager")
    rid = db.add_request(con, "manager", "backend", "do the fresh thing", None, None, "fire_and_forget", "task", 0, None)
    watcher.scan(con)
    watcher.scan(con)
    got = [e for e in stream.since(con, 0, ["tasks"]) if e["data"]["id"] == tid]
    check("tasks: a task made is task.changed once, as the spec's Task, naming where to go",
          len(got) == 1 and got[0]["type"] == "task.changed" and got[0]["data"]["title"] == "fresh" and got[0]["agent"] == "backend"
          and got[0]["target"] == {"screen": "tasks", "id": str(tid)} and not [e for e in stream.since(con, 0, ["tasks"]) if e["data"]["id"] == before],
          str(got))
    n = len(stream.since(con, 0, ["tasks"]))
    ok, msg, _ = actions.memo_add(con, tid, "manager", "a note", tell=False)
    watcher.scan(con)
    check("tasks: a memo is a change of its task", len(stream.since(con, 0, ["tasks"])) == n + 1, msg)
    inbox = stream.since(con, 0, ["inbox"])
    check("inbox: the delivery to the worker arrived as inbox.added", any(e["type"] == "inbox.added" and e["agent"] == "backend" and e["data"]["read"] is False
          for e in inbox), str(inbox))
    con.execute("UPDATE requests SET read_at = ? WHERE id = ?", (int(time.time()), rid))
    con.commit()
    watcher.scan(con)
    check("inbox: reading it is inbox.changed, with the item as read", any(e["type"] == "inbox.changed" and e["data"]["id"] == rid and e["data"]["read"] is True
          for e in stream.since(con, 0, ["inbox"])))
    con.close()


def test_an_ask_to_the_founder_that_is_answered_is_inbox_changed_with_the_item_answered(home: Path) -> None:
    support._daemon_home(home, 20)
    stream.HUB.subs.clear()
    con = db.connect()
    watcher = stream.Watcher(lambda m: None)
    watcher.scan(con)
    rid = db.add_request(con, "backend", "human", "may I?", None, None, "async", "ask", 0, None)
    watcher.scan(con)
    added = [e for e in stream.since(con, 0, ["inbox"]) if e["data"]["id"] == rid]
    check("inbox: an ask to the founder arrives as inbox.added", [e["type"] for e in added] == ["inbox.added"] and added[0]["data"]["answered"] is False, str(added))
    ok, msg = actions.answer_ok(con, rid, "yes")
    watcher.scan(con)
    got = [e for e in stream.since(con, 0, ["inbox"]) if e["data"]["id"] == rid]
    check("inbox: answering it is inbox.changed, the item answered and read, said once",
          ok and [e["type"] for e in got] == ["inbox.added", "inbox.changed"] and got[1]["data"]["answered"] is True and got[1]["data"]["read"] is True
          and got[1]["agent"] == "backend", f"{msg} {got}")
    watcher.scan(con)
    check("inbox: and not again", len([e for e in stream.since(con, 0, ["inbox"]) if e["data"]["id"] == rid]) == 2)
    # what leaves the list is not always an answer: a say seen, an ask dismissed
    say = db.add_request(con, "backend", "human", "fyi", None, None, "fire_and_forget", "say", 0, None, state="landed")
    con.execute("UPDATE requests SET landed_at = ts WHERE id = ?", (say,))
    ask = db.add_request(con, "backend", "human", "may I again?", None, None, "async", "ask", 0, None)
    con.commit()
    watcher.scan(con)
    actions.for_you_seen(con, "backend", 10 ** 9)
    ok, msg = actions.dismiss(con, ask, "human", "not now")
    con.execute("UPDATE requests SET read_at = ? WHERE id = ?", (int(time.time()), say))
    con.commit()
    watcher.scan(con)
    left = {e["data"]["id"]: e["data"] for e in stream.since(con, 0, ["inbox"]) if e["type"] == "inbox.changed" and e["data"]["id"] in (say, ask)}
    check("inbox: a dismissed ask is read, not answered", ok and left.get(ask, {}).get("answered") is False and left[ask]["read"] is True, f"{msg} {left}")
    check("inbox: a say that was seen is read, not answered", left.get(say, {}).get("answered") is False, str(left))
    con.close()


def test_the_inbox_detector_reads_only_what_was_touched_since_its_last_pass(home: Path) -> None:
    support._daemon_home(home, 20)
    real_time, pinned = time.time, int(time.time()) + 0.5       # a clock that crosses a second mid-test puts two of the old rows in one second
    time.time = lambda: pinned
    try:
        _the_inbox_detector_reads_only_what_was_touched(home)
    finally:
        time.time = real_time


def _the_inbox_detector_reads_only_what_was_touched(home: Path) -> None:
    stream.HUB.subs.clear()
    con = db.connect()
    for i in range(300):        # a long history: none of it is touched again
        db.add_request(con, "manager", "backend", f"old {i}", None, None, "fire_and_forget", "task", 0, None)
    con.execute("UPDATE requests SET ts = ts - 100000 - id, state_at = ts - 100000 - id")      # each in a second of its own, as a real history is
    con.commit()
    watcher = stream.Watcher(lambda m: None)
    watcher.scan(con)
    from handline.daemon import rpc_inbox
    built = []
    real = rpc_inbox._item
    rpc_inbox._item = lambda *a, **k: (built.append(k["id"]), real(*a, **k))[1]
    try:
        watcher.scan(con)
        check("inbox: a pass with nothing touched builds at most the one request of the second it stands at, whatever the history", len(built) <= 1, str(built))
        built.clear()
        rid = db.add_request(con, "manager", "backend", "one new", None, None, "fire_and_forget", "task", 0, None)
        watcher.scan(con)
        check("inbox: a request touched builds that item and the boundary one, no more", rid in built and len(built) <= 3, str(built))
        check("inbox: none of the old history was said again", not [e for e in stream.since(con, 0, ["inbox"]) if e["data"]["id"] != rid], "")
    finally:
        rpc_inbox._item = real
    for sql in (stream.CHAT_TOUCHED_SQL, stream._READ_TOUCHED_SQL):
        plan = " ".join(r[3] for r in con.execute("EXPLAIN QUERY PLAN " + sql, {"last": 0, "floor": 0}).fetchall())
        check("inbox: the touched queries search requests by index, never scan it", "SCAN requests" not in plan and "SCAN a" not in plan, plan)
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
        member_only = [w.call(m, {"topics": [t]}) for m in ("subscribe", "events.feed") for t in ("chat", "operations")]
        w.close()
    finally:
        sessiontoken.agent_of = real
    check("roles: an agent may subscribe and read the feed", "result" in sub and "result" in feed, str((sub, feed)))
    check("roles: an agent is refused the chat and operations topics, whose reads are a member's",
          all(_code(r) == "refused" for r in member_only), str(member_only))
    w = Wire()
    w.call("hello", _hp())
    member = [w.call(m, {"topics": [t]}) for m in ("subscribe", "events.feed") for t in ("chat", "operations")]
    w.close()
    check("roles: a member still follows them", all("result" in r for r in member), str(member))
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


def test_an_agent_is_sent_only_what_its_own_reads_would_return(home: Path) -> None:
    """A topic delivers to a role only what that role's read returns: prompt.list is a member's, inbox.list is the agent's own, tasks are its own
    and its reports'. The stream and the feed cut the events to that, and drop what no rule names."""
    support._daemon_home(home, 20)
    stream.HUB.subs.clear()
    con = db.connect()
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: "frontend"
    asked = {"agent": "backend", "kind": "permission", "command": "rm -rf /secret"}
    try:
        w = Wire()
        w.call("hello", {"client": "t", "protocol": 1})
        topics = ["notifications", "inbox", "tasks", "agents", "hr"]
        w.call("subscribe", {"topics": topics})
        sent = [stream.emit(con, "notifications", "prompt_asked", asked, agent="backend", title="backend asks"),
                stream.emit(con, "notifications", "stall", {"agent": "frontend"}, agent="frontend", title="stalled"),
                stream.emit(con, "notifications", "something_new", {"x": 1}, agent="frontend"),
                stream.emit(con, "inbox", "inbox.added", {"id": "a"}, agent="backend"),
                stream.emit(con, "inbox", "inbox.added", {"id": "b"}, agent="frontend"),
                stream.emit(con, "tasks", "task.changed", {"id": 1}, agent="backend"),
                stream.emit(con, "tasks", "task.changed", {"id": 2}, agent="frontend"),
                stream.emit(con, "hr", "hr.changed", {}, agent=None)]
        got = _notices(w, 4)
        feed = w.call("events.feed", {"topics": topics, "since": sent[0]["cursor"] - 1})["result"]
        w.close()
    finally:
        sessiontoken.agent_of = real
    seen = [(n["params"]["topic"], n["params"]["type"], n["params"]["agent"]) for n in got]
    want = [("notifications", "stall", "frontend"), ("inbox", "inbox.added", "frontend"), ("tasks", "task.changed", "frontend"), ("hr", "hr.changed", None)]
    check("an agent is sent its own inbox and tasks, an open topic and the notifications about itself", seen == want, str(seen))
    check("and nothing else: no prompt contents, no other agent's inbox or tasks, no event no rule names",
          [(e["topic"], e["type"], e["agent"]) for e in feed["events"]] == want and "rm -rf" not in str(got) + str(feed), str(feed["events"]))
    check("the feed's cursor is where the log was read to", feed["cursor"] == sent[-1]["cursor"], str(feed["cursor"]))
    w = Wire()
    w.call("hello", _hp())
    full = w.call("events.feed", {"topics": topics, "since": sent[0]["cursor"] - 1})["result"]["events"]
    w.close()
    check("a member still gets all of it", len(full) == len(sent) and any("rm -rf" in str(e) for e in full), str(len(full)))
    con.close()


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
        run(lambda: test_the_tasks_and_inbox_topics_say_what_changed_once_and_not_what_came_before(Path(td) / "ti"))
        run(lambda: test_the_tasks_and_inbox_topics_say_what_changed_once_and_not_what_came_before(Path(td) / "s7b"))
        run(lambda: test_an_ask_to_the_founder_that_is_answered_is_inbox_changed_with_the_item_answered(Path(td) / "s7d"))
        run(lambda: test_the_inbox_detector_reads_only_what_was_touched_since_its_last_pass(Path(td) / "s7c"))
        run(lambda: test_feed_unsubscribe_and_bad_requests(Path(td) / "s8"))
        run(lambda: test_an_agent_may_follow_the_stream_and_hl_events_still_works_from_its_session(Path(td) / "s9"))
        run(lambda: test_an_agent_is_sent_only_what_its_own_reads_would_return(Path(td) / "s9b"))
        run(lambda: test_a_resume_far_behind_but_within_the_limit_is_served_whole_then_live(Path(td) / "s11"))
        run(lambda: test_a_real_slow_client_is_still_dropped_after_a_replay(Path(td) / "s12"))
        run(lambda: test_the_watcher_goes_on_after_a_failure_anywhere_in_its_loop(Path(td) / "s13"))
        run(lambda: test_a_notification_that_fails_to_emit_does_not_repeat_the_ones_before_it(Path(td) / "s14"))
        run(lambda: test_a_database_made_before_the_stream_gets_it_from_a_plain_connect(Path(td) / "s10"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
