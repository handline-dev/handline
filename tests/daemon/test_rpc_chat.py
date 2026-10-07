"""Tests: chat.messages, chat.transcript, chat.locate and chat.search, and the chat topic (contract/protocol.md): a chat's messages
newest first in pages, where one sits, a search, the agent's side as ordered items, and what the detector says when any of it
changes.

Run on its own: python3 tests/daemon/test_rpc_chat.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline import actions  # noqa: E402
import importlib  # noqa: E402
stuck = importlib.import_module("handline.actions.stuck")   # not the function the package re-exports under that name
from handline.daemon import stream  # noqa: E402
from handline.infra import sessiontoken  # noqa: E402
from handline.team import ask as askmod  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _code, _hp  # noqa: E402
from specschema import RESULT, _invalid  # noqa: E402


def _call(method: str, params: dict | None = None, as_agent: str | None = None) -> dict:
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: as_agent
    try:
        w = Wire()
        w.call("hello", _hp() if as_agent is None else {"client": "t", "protocol": 1})
        r = w.call(method, params or {})
        w.close()
        return r
    finally:
        sessiontoken.agent_of = real


def _send(requester: str, target: str, body: str, kind: str = "say") -> int:
    con = db.connect()
    try:
        return db.add_request(con, requester, target, body, None, None, None, kind, 0, None)
    finally:
        con.close()


def _land(rid: int, answered: str | None = None) -> None:
    con = db.connect()
    try:
        now = int(time.time())
        con.execute("UPDATE requests SET typed_at = ?, delivered_at = ?, landed_at = ?, state = 'landed', state_at = ? WHERE id = ?",
                    (now, now, now, now, rid))
        if answered:
            db.add_answer(con, rid, answered)
    finally:
        con.close()


def _quiet_world() -> None:
    """No Orca and no process table: what the reasons a message is still sending read is taken as already read."""
    stuck._WORLD.update(at=time.time() + 10 ** 9, sessions=None, handles=None)


def _world(home: Path) -> dict:
    support._board_world(home)
    _quiet_world()
    ids = {}
    ids["hi"] = _send(db.HUMAN, "worker", "hello worker")
    ids["ask"] = _send("worker", db.HUMAN, "which branch do you want?", "ask")
    ids["side"] = _send("manager", "worker", "between two agents")
    ids["alone"] = _send(db.HUMAN, "worker", "taken back")
    con = db.connect()
    con.execute("UPDATE requests SET state = 'withdrawn' WHERE id = ?", (ids["alone"],))
    con.close()
    _land(ids["hi"], "hello back")
    return ids


def test_messages_come_newest_first_in_pages_in_the_spec_schema(home: Path) -> None:
    ids = _world(home)
    r = _call("chat.messages", {"agent": "worker", "scope": "mine"})
    why = _invalid(r["result"], RESULT["chat.messages"]) if "result" in r else str(r)
    check("chat.messages: the result is the spec's schema", why is None, str(why))
    got = r["result"]["messages"]
    check("chat.messages: the person's chat with the agent, newest first, nothing withdrawn and nothing between two others",
          [m["id"] for m in got] == [ids["ask"], ids["hi"]] and r["result"]["more"] is False, str([m["id"] for m in got]))
    hi = got[1]
    check("chat.messages: a delivered message carries its answer and says it is answered", hi["landing"] == "answered" and hi["answer"]["text"] == "hello back"
          and hi["answer"]["id"] != hi["id"] and hi["delivered_at"] and hi["from"] == "human", str(hi))
    check("chat.messages: one not yet typed is queued, with why", got[0]["landing"] == "queued" and got[0]["answer"] is None, str(got[0]))
    allc = _call("chat.messages", {"agent": "worker", "scope": "all"})["result"]["messages"]
    check("chat.messages: the all-chat holds what the agent got from another agent", ids["side"] in [m["id"] for m in allc], str([m["id"] for m in allc]))
    page = _call("chat.messages", {"agent": "worker", "scope": "all", "limit": 1})["result"]
    check("chat.messages: a limit makes a page, and more says older remain", len(page["messages"]) == 1 and page["more"] is True, str(page))
    older = _call("chat.messages", {"agent": "worker", "scope": "all", "before": page["messages"][0]["id"], "limit": 1})["result"]
    check("chat.messages: before reads the page just older", older["messages"][0]["id"] < page["messages"][0]["id"], str(older))
    newer = _call("chat.messages", {"agent": "worker", "scope": "all", "since": ids["hi"]})["result"]["messages"]
    check("chat.messages: since reads what came after, newest first, without that one", [m["id"] for m in newer] == sorted([m["id"] for m in newer], reverse=True)
          and ids["hi"] not in [m["id"] for m in newer] and ids["ask"] in [m["id"] for m in newer], str([m["id"] for m in newer]))
    for params, want in (({"agent": "ghost", "scope": "mine"}, "not_found"), ({"agent": "worker"}, "bad_request"), ({"agent": "worker", "scope": "x"}, "bad_request"),
                         ({"agent": "worker", "scope": "mine", "limit": 0}, "bad_request"), ({"agent": "worker", "scope": "mine", "limit": 201}, "bad_request"),
                         ({"agent": "worker", "scope": "mine", "before": 1, "since": 1}, "bad_request"), ({"scope": "mine"}, "bad_request"),
                         ({"agent": 5, "scope": "mine"}, "bad_request")):
        check(f"chat.messages: {params} is {want}", _code(_call("chat.messages", params)) == want)
    r = _call("chat.messages", {"agent": "worker", "scope": "mine"}, as_agent="manager")
    check("chat.messages: an agent session is refused, gate role", _code(r) == "refused" and r["error"]["data"]["gate"] == "role", str(r))


def test_chat_requests_are_the_threads_with_their_task_changes_sessions_and_a_version(home: Path) -> None:
    ids = _world(home)
    ok, msg, tid = actions.task_new("manager", "worker", "the work", "do it", timeout_s=3600)
    con = db.connect()
    delivery = int(db.first_linked_request(con, tid)["id"])
    con.close()
    _land(delivery)
    con = db.connect()
    actions.task_handoff(con, "worker", tid, "manager", "back to you: the sha is x")
    now = int(time.time())
    con.execute("INSERT INTO session_starts(agent, ts, session_id, cause, resumed, text, prompt, actor) VALUES('worker', ?, 's1', 'restart', 0, 'the handoff', '', '')", (now,))
    con.execute("INSERT INTO session_starts(agent, ts, session_id, cause, resumed, text, prompt, actor) VALUES('worker', ?, 's0', 'wake', 1, '', '', '')", (now,))
    con.close()
    r = _call("chat.requests", {"agent": "worker", "scope": "all"})
    why = _invalid(r["result"], RESULT["chat.requests"]) if "result" in r else str(r)
    check("chat.requests: the result is the spec's schema", why is None, str(why))
    got = r["result"]
    reqs = [i for i in got["items"] if i["type"] == "request"]
    order = [(i["at"], i["message"]["id"]) for i in reqs]      # oldest first by `at` (an answered request's is its answer's), ties by number
    check("chat.requests: the requests are oldest first and each carries the chat.messages message, its dismissal and its task state",
          order == sorted(order) and ids["hi"] in [i["message"]["id"] for i in reqs]
          and all(set(i) >= {"message", "dismissal", "task_state"} for i in reqs), str(reqs))
    msgs = {m["id"]: m for m in _call("chat.messages", {"agent": "worker", "scope": "all"})["result"]["messages"]}
    check("chat.requests: the message is what chat.messages says of it", all(i["message"] == msgs[i["message"]["id"]] for i in reqs if i["message"]["id"] in msgs))
    card = next(i for i in reqs if i["message"]["id"] == delivery)
    check("chat.requests: a task delivery has the live state of its task", card["message"]["tasks"] == [{"id": tid, "title": "the work"}] and card["task_state"], str(card))
    events = [i for i in got["items"] if i["type"] == "task_event"]
    check("chat.requests: the handoff is a task_event naming the delivery that carries it, its note and who", any(e["change"] == "handed to manager" and e["note"].startswith("back to you")
          and e["task"] == tid and e["who"] == "worker" and e["request"] for e in events), str(events))
    check("chat.requests: it also says the delivery put the task in its owner's hands", any(e["change"] == "queued -> doing" for e in events), str(events))
    sessions = [i for i in got["items"] if i["type"] == "session"]
    check("chat.requests: a new conversation is a session item with its handoff; one that resumed the old one is not", [(x["cause"], x["handoff"]) for x in sessions] == [("restart", "the handoff")], str(sessions))
    check("chat.requests: items are in time order", [i["at"] for i in got["items"]] == sorted(i["at"] for i in got["items"]), str([i["at"] for i in got["items"]]))
    check("chat.requests: a version and the stream cursor", isinstance(got["version"], str) and got["version"] and isinstance(got["cursor"], int))
    again = _call("chat.requests", {"agent": "worker", "scope": "all"})["result"]["version"]
    check("chat.requests: the version is the same when nothing changed", again == got["version"])
    _send(db.HUMAN, "worker", "one more")
    changed = _call("chat.requests", {"agent": "worker", "scope": "all"})["result"]["version"]
    check("chat.requests: and different after a new request", changed != got["version"])
    _land(_send(db.HUMAN, "worker", "and landed"))
    landed = _call("chat.requests", {"agent": "worker", "scope": "all"})["result"]["version"]
    check("chat.requests: and after a landing", landed != changed)
    con = db.connect()
    edited, why = actions.edit_task(con, tid, "manager", title="the work, renamed")
    con.close()
    check("chat.requests: and after a task edit, which adds a task_event", edited and _call("chat.requests", {"agent": "worker", "scope": "all"})["result"]["version"] != landed, str(why))
    page = _call("chat.requests", {"agent": "worker", "scope": "all", "limit": 2})["result"]
    page_reqs = [i["message"]["id"] for i in page["items"] if i["type"] == "request"]
    check("chat.requests: a limit makes a page of the newest requests and more says older remain", len(page_reqs) == 2 and page["more"] is True, str(page_reqs))
    older = _call("chat.requests", {"agent": "worker", "scope": "all", "before": page_reqs[0], "limit": 50})["result"]
    check("chat.requests: before reads the requests older than that one", all(i["message"]["id"] < page_reqs[0] for i in older["items"] if i["type"] == "request") and older["more"] is False)
    newer = _call("chat.requests", {"agent": "worker", "scope": "all", "since": page_reqs[0]})["result"]
    check("chat.requests: since reads from that request on, with no more", page_reqs[0] in [i["message"]["id"] for i in newer["items"] if i["type"] == "request"] and newer["more"] is False)
    for params, want in (({"agent": "ghost", "scope": "mine"}, "not_found"), ({"agent": "worker"}, "bad_request"), ({"agent": "worker", "scope": "x"}, "bad_request"),
                         ({"agent": "worker", "scope": "mine", "limit": 201}, "bad_request"), ({"agent": "worker", "scope": "mine", "before": 1, "since": 1}, "bad_request")):
        check(f"chat.requests: {params} is {want}", _code(_call("chat.requests", params)) == want)
    r = _call("chat.requests", {"agent": "worker", "scope": "mine"}, as_agent="manager")
    check("chat.requests: an agent session is refused, gate role", _code(r) == "refused" and r["error"]["data"]["gate"] == "role", str(r))


def test_a_task_delivery_names_its_task_and_a_withdrawn_message_is_not_a_message(home: Path) -> None:
    support._board_world(home)
    _quiet_world()
    ok, msg, tid = actions.task_new("manager", "worker", "the work", "do it and show the result", timeout_s=3600)
    got = _call("chat.messages", {"agent": "worker", "scope": "all"})["result"]["messages"]
    card = next(m for m in got if m["kind"] == "task")
    check("chat.messages: a task delivery lists its task, with the title", card["tasks"] == [{"id": tid, "title": "the work"}], str(card))
    check("chat.messages: it is not a task question", card["is_task_question"] is False)


def test_locate_finds_the_chat_a_message_is_drawn_in_and_the_page_around_it(home: Path) -> None:
    ids = _world(home)
    for n in range(12):
        _send(db.HUMAN, "worker", f"filler {n}")
    r = _call("chat.locate", {"request": ids["hi"], "limit": 4})
    why = _invalid(r["result"], RESULT["chat.locate"]) if "result" in r else str(r)
    check("chat.locate: the result is the spec's schema", why is None, str(why))
    got = r["result"]
    check("chat.locate: the conversation is found from the message itself", got["agent"] == "worker" and got["scope"] == "mine" and got["message"]["id"] == ids["hi"], str(got["agent"]))
    check("chat.locate: the page holds it, newest first, with older and newer to read", ids["hi"] in [m["id"] for m in got["messages"]]
          and got["more_newer"] is True, str([m["id"] for m in got["messages"]]))
    answered = db.connect().execute("SELECT id FROM requests WHERE settles = ?", (ids["hi"],)).fetchone()["id"]
    check("chat.locate: the number of an answer is its request's message", _call("chat.locate", {"request": answered})["result"]["message"]["id"] == ids["hi"])
    side = _call("chat.locate", {"request": ids["side"], "agent": "worker"})["result"]
    check("chat.locate: a message between two agents is in the all-chat", side["scope"] == "all", str(side["scope"]))
    for params in ({"request": 99999}, {"request": ids["alone"]}):
        check(f"chat.locate: {params} is not_found", _code(_call("chat.locate", params)) == "not_found")
    data = _call("chat.locate", {"request": ids["alone"]})["error"]["data"]
    check("chat.locate: a message no chat draws says what it was", (data.get("from"), data.get("to"), data.get("body")) == (db.HUMAN, "worker", "taken back"), str(data))
    con = db.connect()
    answer = db.add_request(con, "worker", db.HUMAN, "the answer to a message never read", None, None, None, "say", 0, None)
    con.execute("UPDATE requests SET settles = ? WHERE id = ?", (ids["alone"], answer))
    con.close()
    for params in ({"request": answer}, {"request": answer, "agent": "worker"}):
        d = _call("chat.locate", params)["error"]["data"]
        check(f"chat.locate: {params} names the answer asked for, not the message it settles", d.get("name") == str(answer) and d.get("body") == "the answer to a message never read", str(d))
    check("chat.locate: a message that does not exist says nothing of one", "body" not in _call("chat.locate", {"request": 99999})["error"]["data"])
    check("chat.locate: a message that is not a number is bad_request", _code(_call("chat.locate", {"request": "1"})) == "bad_request")


def test_search_finds_text_and_says_where_each_hit_is_drawn(home: Path) -> None:
    ids = _world(home)
    _send(db.HUMAN, "worker", "100% sure about the_branch?")
    got = _call("chat.search", {"query": "branch"})
    why = _invalid(got["result"], RESULT["chat.search"]) if "result" in got else str(got)
    check("chat.search: the result is the spec's schema", why is None, str(why))
    hits = got["result"]["messages"]
    check("chat.search: hits, newest first, each with the agent and scope that show it", [h["id"] for h in hits][0] > ids["ask"]
          and all(h["agent"] == "worker" and h["scope"] == "mine" for h in hits), str(hits))
    check("chat.search: a wildcard is text: % and _ match themselves only", len(_call("chat.search", {"query": "%"})["result"]["messages"]) == 1
          and len(_call("chat.search", {"query": "the_branch"})["result"]["messages"]) == 1 and not _call("chat.search", {"query": "the-branch"})["result"]["messages"])
    check("chat.search: an answer's words find its request", ids["hi"] in [h["id"] for h in _call("chat.search", {"query": "hello back"})["result"]["messages"]])
    check("chat.search: an agent named narrows it, a withdrawn message is never found",
          not _call("chat.search", {"query": "taken back"})["result"]["messages"]
          and ids["side"] not in [h["id"] for h in _call("chat.search", {"query": "between", "agent": "worker", "scope": "mine"})["result"]["messages"]]
          and ids["side"] in [h["id"] for h in _call("chat.search", {"query": "between", "agent": "worker", "scope": "all"})["result"]["messages"]])
    for params in ({"query": " "}, {}, {"query": "x", "limit": 101}, {"query": "x", "scope": "everything"}):
        check(f"chat.search: {params} is bad_request", _code(_call("chat.search", params)) == "bad_request")
    check("chat.search: an agent that is not there is not_found", _code(_call("chat.search", {"query": "x", "agent": "ghost"})) == "not_found")


def test_the_transcript_is_the_agents_side_as_ordered_items(home: Path) -> None:
    ids = _world(home)
    ok, msg, tid = actions.task_new("manager", "worker", "the work", "do it", timeout_s=3600)
    con = db.connect()
    now = int(time.time())
    con.execute("INSERT INTO prompt_blocks(agent, kind, fingerprint, title, panel, detail, first_seen, last_seen) VALUES(?,?,?,?,?,?,?,?)",
                ("worker", "permission", "fp1", "Run a command?", json.dumps(["Run ls?", "1. Yes", "2. No"]), json.dumps({"tool": "Bash", "command": "ls"}), now, now))
    con.close()
    r = _call("chat.transcript", {"agent": "worker"})
    why = _invalid(r["result"], RESULT["chat.transcript"]) if "result" in r else str(r)
    check("chat.transcript: the result is the spec's schema", why is None, str(why))
    items = r["result"]["items"]
    kinds = [i["kind"] for i in items]
    check("chat.transcript: the agent's message to the person and its answer to theirs are replies", kinds.count("reply") == 2
          and {i["text"] for i in items if i["kind"] == "reply"} == {"which branch do you want?", "hello back"}, str(items))
    check("chat.transcript: a prompt it opened, still open", any(i["kind"] == "prompt" and i["open"] is True and i["prompt"]["tool"] == "Bash" for i in items), str(items))
    check("chat.transcript: the task it was given", any(i["kind"] == "task_event" and i["task_event"]["event"] == "opened" and i["task_event"]["task"] == tid for i in items), str(items))
    check("chat.transcript: oldest first, ids that only grow", [i["id"] for i in items] == sorted({i["id"] for i in items}), str([i["id"] for i in items]))
    check("chat.transcript: ids a browser can hold", all(i["id"] < 2 ** 53 for i in items))
    last = items[-1]["id"]
    ok, _, rid = True, 0, _send("worker", db.HUMAN, "a new word from the agent")
    after = _call("chat.transcript", {"agent": "worker", "since": last})["result"]["items"]
    check("chat.transcript: since reads from the second it is in, so nothing that arrived in it is missed", "a new word from the agent" in [i["text"] for i in after if i["kind"] == "reply"]
          and all(i["id"] >= last // 1_000_000 * 1_000_000 for i in after), str(after))
    page = _call("chat.transcript", {"agent": "worker", "limit": 2})["result"]
    older = _call("chat.transcript", {"agent": "worker", "limit": 2, "before": page["items"][0]["id"]})["result"]
    check("chat.transcript: a limit pages back with before, and more says so", len(page["items"]) == 2 and page["more"] is True
          and older["items"][-1]["id"] < page["items"][0]["id"], str((page, older)))
    check("chat.transcript: the working state is there", set(r["result"]["working"]) == {"turn_running", "line", "since"})
    for params, want in (({"agent": "ghost"}, "not_found"), ({}, "bad_request"), ({"agent": "worker", "limit": 0}, "bad_request"),
                         ({"agent": "worker", "before": 1, "since": 1}, "bad_request")):
        check(f"chat.transcript: {params} is {want}", _code(_call("chat.transcript", params)) == want)


def test_the_chat_topic_says_what_changes_once(home: Path) -> None:
    support._board_world(home)
    _quiet_world()
    stream.HUB.subs.clear()
    con = db.connect()
    watcher = stream.Watcher(lambda m: None)
    watcher.scan(con)
    check("chat topic: what was there before the first look is not news", stream.since(con, 0, ["chat"]) == [])
    rid = _send(db.HUMAN, "worker", "hello worker")
    watcher.scan(con)
    got = stream.since(con, 0, ["chat"])
    check("chat topic: a new message is said, to the chat of the agent it is with", [(e["type"], e["agent"], e["data"]["id"]) for e in got] == [("chat.message", "worker", rid)], str(got))
    watcher.scan(con)
    check("chat topic: and not again", len(stream.since(con, 0, ["chat"])) == 1)
    _land(rid, "hello back")
    watcher.scan(con)
    types = [(e["type"], e["data"].get("landing") or e["data"].get("kind")) for e in stream.since(con, 0, ["chat"])[1:]]
    check("chat topic: its landing and its answer are the same message changed", ("chat.message", "answered") in types, str(types))
    side = _send("manager", "worker", "between two agents")
    watcher.scan(con)
    new = [e for e in stream.since(con, 0, ["chat"]) if e["type"] == "chat.message" and e["data"]["id"] == side]
    check("chat topic: a message between two agents is said to both their chats", sorted(e["agent"] for e in new) == ["manager", "worker"], str(new))
    asked = _send("worker", db.HUMAN, "which branch?", "ask")
    watcher.scan(con)
    got = stream.since(con, 0, ["chat"])
    check("chat topic: what the agent says to the person is also a transcript reply", any(e["type"] == "chat.transcript" and e["data"]["kind"] == "reply"
                                                                                         and e["data"]["text"] == "which branch?" and e["agent"] == "worker" for e in got), str(got))
    ok, msg, tid = actions.task_new("manager", "worker", "the work", "do it", timeout_s=3600)
    watcher.scan(con)
    got = stream.since(con, 0, ["chat"])
    check("chat topic: a task event is a transcript item for the owner", any(e["type"] == "chat.transcript" and e["data"]["kind"] == "task_event"
                                                                           and e["data"]["task_event"]["task"] == tid and e["agent"] == "worker" for e in got), str(got))
    now = int(time.time())
    con.execute("INSERT INTO prompt_blocks(agent, kind, fingerprint, title, panel, detail, first_seen, last_seen) VALUES(?,?,?,?,?,?,?,?)",
                ("worker", "permission", "fp9", "Run?", json.dumps(["Run ls?"]), json.dumps({"tool": "Bash"}), now, now))
    watcher.scan(con)
    watcher.scan(con)
    prompts = [e for e in stream.since(con, 0, ["chat"]) if e["type"] == "chat.transcript" and e["data"]["kind"] == "prompt"]
    check("chat topic: a prompt it opens is said once, open", len(prompts) == 1 and prompts[0]["data"]["open"] is True, str(prompts))
    con.execute("UPDATE prompt_blocks SET gone_at = ? WHERE fingerprint = 'fp9'", (now + 1,))
    watcher.scan(con)
    prompts = [e for e in stream.since(con, 0, ["chat"]) if e["type"] == "chat.transcript" and e["data"]["kind"] == "prompt"]
    check("chat topic: and again, closed, when it goes", len(prompts) == 2 and prompts[1]["data"]["open"] is False, str(prompts))
    con.execute("INSERT OR REPLACE INTO agent_view(agent, view, updated) VALUES('worker', ?, ?)",
                (json.dumps({"state": "RUNNING", "state_detail": "", "state_since": "2026-01-01T00:00:00Z", "blocker": None}), now))
    watcher.scan(con)
    working = [e for e in stream.since(con, 0, ["chat"]) if e["type"] == "chat.working"]
    check("chat topic: an agent's working state, when it changes", working and working[-1]["agent"] == "worker" and working[-1]["data"]["turn_running"] is True, str(working))
    con.close()


def test_the_chat_detector_reads_only_what_was_touched_since_and_remembers_prompts_across_a_restart(home: Path) -> None:
    support._board_world(home)
    _quiet_world()
    con = db.connect()
    plan = " | ".join(r["detail"] for r in con.execute("EXPLAIN QUERY PLAN " + stream.CHAT_TOUCHED_SQL, {"last": 5}))
    check("chat topic: the touched-since query searches an index and never scans the requests", "SEARCH" in plan and "SCAN requests" not in plan
          and "SCAN TABLE requests" not in plan, plan)
    for n in range(300):
        _send(db.HUMAN, "worker", f"old {n}")
    con.execute("UPDATE requests SET ts = ts - 1000, state_at = NULL")
    rid = _send(db.HUMAN, "worker", "now")
    done = _send("worker", db.HUMAN, "answered", "ask")
    db.add_answer(con, done, "an answer")
    last = int(time.time()) - 5
    got = [r["id"] for r in con.execute(stream.CHAT_TOUCHED_SQL, {"last": last})]
    check("chat topic: it gives the requests changed or answered since, and the old ones are not read", rid in got and done in got and len(got) == 2, str(got))
    now = int(time.time())
    con.execute("INSERT INTO prompt_blocks(agent, kind, fingerprint, title, panel, detail, first_seen, last_seen) VALUES(?,?,?,?,?,?,?,?)",
                ("worker", "permission", "fpr", "Run?", json.dumps(["Run ls?"]), json.dumps({"tool": "Bash"}), now, now))
    stream.HUB.subs.clear()
    first = stream.Watcher(lambda m: None)
    first.scan(con)             # a daemon that starts with the prompt open: not news
    second = stream.Watcher(lambda m: None)         # and one that starts again (a restart): the prompt is still open
    second.scan(con)
    con.execute("UPDATE prompt_blocks SET gone_at = ? WHERE fingerprint = 'fpr'", (now + 1,))
    second.scan(con)
    prompts = [e for e in stream.since(con, 0, ["chat"]) if e["type"] == "chat.transcript" and e["data"]["kind"] == "prompt"]
    check("chat topic: a prompt open before a restart is told closed when it closes", len(prompts) == 1 and prompts[0]["data"]["open"] is False, str(prompts))
    for word, expect in (("running", True), ("RUNNING", True), ("idle", False), ("IDLE", False)):
        from handline.daemon import rpc_chat
        check(f"chat working: the stored state word {word} gives turn_running {expect}", rpc_chat.working_from({"state": word})["turn_running"] is expect)
    con.close()


def test_the_chat_fixtures_pass_against_this_daemon(home: Path) -> None:
    import socket
    import threading
    from handline.daemon import socket_server
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "contract"))
    import conformance

    def connector():
        def connect():
            a, b = socket.socketpair()
            threading.Thread(target=socket_server._serve_one, args=(b, lambda m: None), daemon=True).start()
            return a
        return connect
    support._board_world(home)
    real = sessiontoken.agent_of
    results = []
    try:
        for role in ("founder", "agent"):
            sessiontoken.agent_of = (lambda pid: "manager") if role == "agent" else (lambda pid: None)
            results += [(*r, role) for r in conformance.run_all([Path("contract/fixtures/chat.json")], {role: connector()},
                                                                 {"founder_key": _hp()["key"]}) if r[0] != "skip"]
    finally:
        sessiontoken.agent_of = real
    bad = [(n, why) for st, why, n, _ in results if st != "pass"]
    check("fixtures: every chat fixture passes against this daemon, each as its own role", not bad and len({n for _, _, n, _ in results}) == 13, str(bad))


def main() -> int:
    def tests(td):
        run(lambda: test_messages_come_newest_first_in_pages_in_the_spec_schema(Path(td) / "c1"))
        run(lambda: test_chat_requests_are_the_threads_with_their_task_changes_sessions_and_a_version(Path(td) / "cq"))
        run(lambda: test_a_task_delivery_names_its_task_and_a_withdrawn_message_is_not_a_message(Path(td) / "c2"))
        run(lambda: test_locate_finds_the_chat_a_message_is_drawn_in_and_the_page_around_it(Path(td) / "c3"))
        run(lambda: test_search_finds_text_and_says_where_each_hit_is_drawn(Path(td) / "c4"))
        run(lambda: test_the_transcript_is_the_agents_side_as_ordered_items(Path(td) / "c5"))
        run(lambda: test_the_chat_topic_says_what_changes_once(Path(td) / "c6"))
        run(lambda: test_the_chat_detector_reads_only_what_was_touched_since_and_remembers_prompts_across_a_restart(Path(td) / "c8"))
        run(lambda: test_the_chat_fixtures_pass_against_this_daemon(Path(td) / "c7"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
