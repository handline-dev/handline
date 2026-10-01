"""Tests: `ocp say` (T293, auditor #1655/#1657).

Run on its own: python3 tests/team/test_say.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403


def _say_world(home: Path) -> None:
    _board_world(home)


def test_say_delivers_ahead_of_the_queue_and_never_parks(home: Path) -> None:
    """T293: a say is delivered as its own row, kind='say', past whatever the
    target already holds (send_now) and never the head of its queue - and the
    requester never parks on it, only the fixed 30-minute reply window."""
    _say_world(home)
    con = db.connect()
    try:
        ok, msg, first = askmod.ask("worker", "do the real thing", requester="manager")
        check("setup: an ordinary ask queues first", ok, msg)
        check("setup: it is worker's head", db.head_of_queue(con, "worker")["id"] == first)

        ok, msg, rid = askmod.say("worker", "is T1 close to done?", requester="manager")
        check("say: opens", ok, msg)
        row = db.get_request(con, rid)
        check("say: kind is 'say'", row["kind"] == "say", row["kind"])
        check("say: no files, ever - the row carries none", row["files"] is None)
        check("say: state is queued - not yet a decided answer",
              row["state"] == "queued", row["state"])
        check("say: never the head of the queue - the ordinary ask still is",
              db.head_of_queue(con, "worker")["id"] == first)
        check("say: delivered ahead of the queue - by its kind, with no mark", db.tells_now(row))
        check("say: the requester never parks on it",
              db.get_agent_state(con, "manager") is None
              or db.get_agent_state(con, "manager")["state"] != "PARKED")
    finally:
        con.close()


def test_a_manager_says_to_a_report_without_a_task(home: Path) -> None:
    """T835: a say to a report needs no task and no reason - a message is a message."""
    _say_world(home)
    rc, out = _cli(["say", "worker", "please build the board"], as_agent="manager")
    check("a bare say to a report is a plain say", rc == 0 and "queued as #" in out, out)
    rc, out = _cli(["say", "manager", "done, nothing left"], as_agent="worker")
    check("a bare say from a report to its manager is a plain say", rc == 0 and "queued as #" in out, out)
    hand_edit_team("""
agents:
  manager: {}
  worker:  {}
  peer:    {}

edges:
  - { from: "manager", to: "worker", relation: supervisor, response: fire_and_forget }
  - { from: "worker", to: "manager", relation: subordinate, response: fire_and_forget }
  - { from: "worker", to: "peer", relation: peer, response: fire_and_forget }

limits:
  max_chain_depth: 4
  ask_rate_per_hour: 50
""")
    agents.register("peer")
    rc, out = _cli(["say", "peer", "a bare hello"], as_agent="worker")
    check("a bare say between agents with no supervisor edge is a plain say",
          rc == 0 and "queued as #" in out, out)
    ok, msg, tid = actions.task_new("manager", "worker", "the board", "build the board")
    check("setup: a task for worker", ok, msg)
    rc, out = _cli(["say", "worker", "use the v2 layout", "--task", f"T{tid}"], as_agent="manager")
    check("--task: lands as a memo on that task (T521)", rc == 0 and "memo" in out.lower(), out)
    rc, out = _cli(["say", "manager", "use the v2 layout", "--task", f"T{tid}"], as_agent="manager")
    check("--task: the task must be with the one it is said to", rc == 1 and "is with worker" in out, out)
    rc, out = _cli(["say", "worker", "x", "--task", "T99999"], as_agent="manager")
    check("--task: an unknown task is refused", rc == 1 and "no task T99999" in out, out)
    ok, msg, rid = askmod.say("manager", "a question", requester="worker")
    rc, out = _cli(["say", "worker", "the answer", "--parent", str(rid)], as_agent="manager")
    check("a reply to a say is a say, whatever its direction", rc == 0, out)
    rc, out = _cli(["task", "new", "-t", "1h", "--owner", "worker", "no brief"], as_agent="manager")
    check("task new without a body is refused", rc == 1 and "needs a body" in out, out)


def test_say_survives_enforce_edges_own_kind_recheck(home: Path) -> None:
    """T293, the same regression class T292's kind override hit (auditor
    #1694): the 'say' kind must be exempt from enforce_edges' delivery-time
    kind re-check, or every say would be silently denied the moment it
    reaches the target - `push` runs it synchronously inside the CLI call
    itself, so this is provable without a daemon tick."""
    _say_world(home)
    rc, out = _cli(["say", "worker", "quick question"], as_agent="manager")
    check("say: delivers via the CLI", rc == 0, out)
    rid = int(out.split("queued as #", 1)[1].split()[0])
    con = db.connect()
    try:
        check("say: still queued after enforce_edges' own re-check at delivery, "
              "not silently denied", db.get_request(con, rid)["state"] == "queued")
    finally:
        con.close()


def test_a_reply_settles_the_request_it_answers_as_one_message(home: Path) -> None:
    """T361 step 1: `say --parent N` settles any open request addressed to the replier, and the
    reply row is itself the answer (`settles`) - one message, no second answer row. A reply
    from anyone else, or to a roster proposal, settles nothing."""
    _say_world(home)
    con = db.connect()
    ok, msg, rid = askmod.ask("worker", "please look at the migration", requester="manager")
    check("setup: an ask to worker", ok, msg)
    con.close()
    rc, out = _cli(["say", "manager", "third party", "--parent", str(rid)], as_agent="reviewer")
    con = db.connect()
    check("reply: from someone the ask was not addressed to settles nothing",
          db.get_request(con, rid)["state"] == "queued", out)
    con.close()
    rc, out = _cli(["say", "manager", "looked: fine", "--parent", str(rid)], as_agent="worker")
    check("reply: the target's reply is sent", rc == 0, out)
    con = db.connect()
    try:
        ask_row = db.get_request(con, rid)
        rows = [dict(r) for r in con.execute(
            "SELECT id, requester, target, kind, settles, parent_id FROM requests WHERE id > ?", (rid,))]
        said = [r for r in rows if r["requester"] == "worker" and r["target"] == "manager"]
        check("reply: the ask is settled, with the reply as its verdict",
              ask_row["state"] == "landed" and ask_row["verdict"] == "looked: fine", str(dict(ask_row)))
        check("reply: exactly one row from the answerer, and it is the settling answer",
              len(said) == 1 and said[0]["settles"] == rid and said[0]["kind"] == "say", str(rows))
    finally:
        con.close()


def test_a_reply_to_the_founder_says_what_it_becomes_and_the_tail_says_how(home: Path) -> None:
    """T361 step 1 (founder #6166): a message from the founder is answered through ocp - its
    delivery tail says so, never that it needs no answer - and the reply names the tasks it
    became or says nothing needs building, as `resolve` did."""
    _say_world(home)
    hand_edit_team("""
agents:
  manager: {}
  worker:  {}

edges:
  - { from: "manager", to: "worker", relation: supervisor, response: fire_and_forget }
  - { from: "worker", to: "manager", relation: subordinate, response: fire_and_forget }
  - { from: "*", to: "human", kind: task }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
""")
    con = db.connect()
    rid = db.add_request(con, "human", "manager", "how is the migration?", None, "supervisor",
                         "fire_and_forget", None, 0, None)
    sid = db.add_request(con, "human", "manager", "fyi: the board is live", None, "supervisor",
                         "fire_and_forget", "say", 0, None)
    con.commit()
    for r in (rid, sid):
        text = dmn_delivery.tell_text(con, db.get_request(con, r), 1)
        check(f"tail: #{r} from the founder says to answer with ocp say human --parent",
              f"if it asks something: `ocp say human --parent {r} " in text
              and "needs no answer" not in text and "ocp resolve" not in text
              and "inbox" not in text and len(text.splitlines()) == 3, text)
    con.close()
    from ocp.session import launch
    team_text = launch._your_team("worker")
    check("your team: every agent is told, in one line, to talk to people only through ocp",
          "Talk to people only through ocp; what you write in your terminal reaches no one." in team_text
          and "a message that only informs needs no reply" in team_text, team_text)
    rc, out = _cli(["say", "human", "fine", "--parent", str(rid)], as_agent="manager")
    check("founder reply: without --task or --no-task it is refused, saying why",
          rc == 1 and "say whether work follows it" in out and "--no-task" in out, out)
    con = db.connect()
    check("founder reply: and nothing was settled", db.get_request(con, rid)["state"] == "queued")
    con.close()
    rc, out = _cli(["say", "human", "fine, done", "--parent", str(rid), "--no-task", "a status question"],
                   as_agent="manager")
    check("founder reply: --no-task with a reason sends it", rc == 0, out)
    con = db.connect()
    try:
        row = db.get_request(con, rid)
        answer = con.execute("SELECT * FROM requests WHERE settles = ?", (rid,)).fetchone()
        check("founder reply: the ask is settled and the reply is its one answer",
              row["state"] == "landed" and answer is not None and answer["target"] == "human"
              and con.execute("SELECT COUNT(*) c FROM requests WHERE settles = ?", (rid,)).fetchone()["c"] == 1)
        check("founder reply: the reason is recorded", con.execute(
            "SELECT 1 FROM events WHERE kind = 'no_task'").fetchone() is not None)
    finally:
        con.close()


def test_a_reply_never_settles_a_task_and_the_founder_guard_follows_the_target(home: Path) -> None:
    """T361 step 1 (auditor #6216): a reply in a task's thread leaves the task's request queued
    and the task doing; the founder guard runs only for a reply that goes to the founder;
    --no-task means nothing on any other reply."""
    _say_world(home)
    hand_edit_team("""
agents:
  manager: {}
  worker:  {}

edges:
  - { from: "manager", to: "worker", relation: supervisor, response: fire_and_forget }
  - { from: "worker", to: "manager", relation: subordinate, response: fire_and_forget }
  - { from: "*", to: "human", kind: task }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
""")
    ok, msg, tid = actions.task_new("manager", "worker", "build it", "build it")
    check("setup: a task is delivered to worker", ok, msg)
    con = db.connect()
    trid = [r["id"] for r in db.task_requests(con, tid) if r["target"] == "worker"][0]
    con.close()
    rc, out = _cli(["say", "manager", "done I think?", "--parent", str(trid)], as_agent="worker")
    con = db.connect()
    try:
        check("task reply: sent", rc == 0, out)
        check("task reply: the task's request stays queued and the task keeps doing",
              db.get_request(con, trid)["state"] == "queued"
              and actions.task_state(con, db.get_task(con, tid)) == "doing")
        check("task reply: nothing is marked as settling it",
              con.execute("SELECT COUNT(*) c FROM requests WHERE settles = ?", (trid,)).fetchone()["c"] == 0)
        frid = db.add_request(con, "human", "manager", "how is it?", None, "supervisor",
                              "fire_and_forget", None, 0, None)
        con.commit()
    finally:
        con.close()
    rc, out = _cli(["say", "worker", "fyi", "--parent", str(frid), "--no-task", "x"], as_agent="manager")
    con = db.connect()
    try:
        check("wrong target: a reply to someone else runs no founder guard and records nothing",
              rc == 1 and con.execute("SELECT 1 FROM events WHERE kind = 'no_task'").fetchone() is None
              and db.get_request(con, frid)["state"] == "queued", out)
    finally:
        con.close()
    rc, out = _cli(["say", "worker", "plain", "--no-task", "x"], as_agent="manager")
    check("--no-task on a say that is not a founder reply is refused, not dropped",
          rc == 1 and "--no-task is for replying to the founder" in out, out)
    rc, out = _cli(["say", "human", "hm", "--parent", str(frid)], as_agent="manager")
    check("founder reply refusal names the single --task form", rc == 1 and "--task T<n> names" in out
          and "[T<n> ...]" not in out, out)


def test_a_reply_to_a_say_is_a_say_in_its_own_thread(home: Path) -> None:
    """T293/#1655 (1b): a reply to a say is a say, threaded with --parent -
    the same chain, not a fresh one, and still never a head."""
    _say_world(home)
    rc, out = _cli(["say", "worker", "where are we on the migration?"], as_agent="manager")
    check("say: opens", rc == 0, out)
    rid = int(out.split("queued as #", 1)[1].split()[0])
    rc, out = _cli(["say", "manager", "close - one file left", "--parent", str(rid)],
                   as_agent="worker")
    check("say: a reply threads with --parent", rc == 0, out)
    reply_rid = int(out.split("queued as #", 1)[1].split()[0])
    con = db.connect()
    try:
        original, reply = db.get_request(con, rid), db.get_request(con, reply_rid)
        check("say: the reply is kind 'say' too", reply["kind"] == "say", reply["kind"])
        check("say: the reply shares the original's chain",
              reply["chain_id"] == original["chain_id"])
        check("say: the reply is never a head either",
              db.head_of_queue(con, "manager") is None
              or db.head_of_queue(con, "manager")["id"] != reply_rid)
        check("say: the reply closes the say it answers, recorded as its verdict "
              "(auditor #1742 must-change 1) - otherwise the table only grows",
              original["state"] == "landed" and original["verdict"] == "close - one file left",
              str(dict(original)))
    finally:
        con.close()
    con = db.connect()
    real_available = orcamod.available
    try:
        orcamod.available = lambda: False   # stuck() asks orca only if it must
        check("say: stuck() lists nothing for an answered say",
              not any(f"#{rid}" in r["what"] for r in actions.stuck(con)), actions.stuck(con))
    finally:
        orcamod.available = real_available
        con.close()


def test_only_the_says_own_target_can_close_it_by_replying(home: Path) -> None:
    """Only the say's own target's reply closes it - naming its id as --parent from anywhere else must not."""
    _say_world(home)
    hand_edit_team("""
agents:
  manager: {}
  worker:  {}

edges:
  - { from: "manager", to: "worker", relation: supervisor, response: fire_and_forget }
  - { from: "worker", to: "manager", relation: subordinate, response: fire_and_forget }
  - { from: "*", to: "human", kind: task }

limits:
  max_chain_depth: 4
  ask_rate_per_hour: 50
""")
    rc, out = _cli(["say", "worker", "is X true?"], as_agent="manager")
    check("setup: a say to worker opens", rc == 0, out)
    rid = int(out.split("queued as #", 1)[1].split()[0])
    # manager - the say's own sender, not its target - tries to close it by
    # naming it as --parent on an unrelated say of its own.
    rc, out = _cli(["say", askmod.HUMAN, "unrelated", "--parent", str(rid)], as_agent="manager")
    check("attack: the unrelated say still delivers", rc == 0, out)
    con = db.connect()
    try:
        row = db.get_request(con, rid)
        check("attack: but the original say to worker is not closed by it",
              row["state"] == "queued", str(dict(row)))
    finally:
        con.close()
    # worker - the actual target - replies for real, and that does close it.
    rc, out = _cli(["say", "manager", "yes", "--parent", str(rid)], as_agent="worker")
    check("real reply: worker's own reply delivers", rc == 0, out)
    con = db.connect()
    try:
        row = db.get_request(con, rid)
        check("real reply: and it does close the say, this time",
              row["state"] == "landed" and row["verdict"] == "yes", str(dict(row)))
    finally:
        con.close()


def test_tell_text_gives_a_say_its_own_way_out_not_ocp_resolve(home: Path) -> None:
    """What is typed at the receiver's terminal for a say is its own delivery line, not `ocp resolve`."""
    _say_world(home)
    ok, msg, rid = askmod.say("worker", "is the deploy done?", requester="manager")
    check("say: opens", ok, msg)
    con = db.connect()
    try:
        row = db.get_request(con, rid)
        text = dmn_delivery.tell_text(con, row, 1)
    finally:
        con.close()
    check("tell_text: a say is not told to `ocp resolve`", "ocp resolve" not in text, text)
    check("tell_text: it says it asks nothing instead", "asks nothing" in text
          and "ocp say" not in text and "--needs-task" not in text, text)


def test_inbox_unread_only_falls_back_to_a_marked_say_with_no_head(home: Path) -> None:
    """`inbox(unread_only=True)` still finds a say with nothing else queued for its target."""
    _say_world(home)
    check("inbox: empty before anything is sent", askmod.inbox("worker") == [])
    ok, msg, rid = askmod.say("worker", "quick one", requester="manager")
    check("say: opens", ok, msg)
    unread = askmod.inbox("worker")
    check("inbox: a say with no ordinary head is still what unread_only finds",
          len(unread) == 1 and unread[0]["id"] == rid, unread)


def test_inbox_unread_only_reads_the_blocked_head_through_effective_head(home: Path) -> None:
    """T337 round 4, auditor #2028's sweep: inbox(unread_only=True) read
    db.head_of_queue_or_marked directly - a true head blocked on record is
    not what is actually waiting for the agent; the request behind it is."""
    _say_world(home)
    con = db.connect()
    try:
        ok, msg, tid = actions.task_new("manager", "worker", "the blocked head", "the blocked head")
        check("setup: worker holds the task", ok, msg)
        head = db.task_requests(con, tid)[0]["id"]
        check("unblocked: inbox finds the plain head",
              [r["id"] for r in askmod.inbox("worker")] == [head])

        ok, msg, t_blocker = actions.task_new("worker", "manager", "unrelated", "unrelated")
        check("setup: a task to block on, owned elsewhere so its own delivery "
              "does not land in worker's queue too", ok, msg)
        ok, msg = actions.add_block(con, tid, t_blocker, "manager")
        check("setup: the task is now blocked", ok, msg)
        check("blocked, nothing behind: inbox is empty, not the blocked head",
              askmod.inbox("worker") == [])

        ok, msg, nxt = askmod.ask("worker", "fix it instead", requester="manager")
        check("setup: another ask goes out", ok, msg)
        check("blocked, something behind: inbox finds it, not the blocked head",
              [r["id"] for r in askmod.inbox("worker")] == [nxt])
    finally:
        con.close()


def test_a_say_prints_no_way_out_and_needs_task_is_gone(home: Path) -> None:
    """T398 (founder: messages need no answer, work is a task): a say shows its
    sender no reply hint, `ocp read` shows its receiver none, and `--needs-task` no
    longer exists."""
    _say_world(home)
    rc, out = _cli(["say", "worker", "can you take a look at the flaky test?"],
                   as_agent="manager")
    check("say: opens", rc == 0, out)
    rid = int(out.split("queued as #", 1)[1].split()[0])
    check("say: prints nothing about replying or flagging as a task to its sender",
          "reply with" not in out and "needs-task" not in out and "investigation" not in out, out)
    con = db.connect()
    try:
        con.execute("UPDATE requests SET state = 'queued', verdict = NULL WHERE id = ?", (rid,))
    finally:
        con.close()
    rc, out = _cli(["read", str(rid)], as_agent="worker")
    check("say: `ocp read` shows its receiver no way out either",
          rc == 0 and "reply with" not in out and "needs-task" not in out, out)
    try:
        rc, out = _cli(["say", "--needs-task", str(rid)], as_agent="worker")
    except SystemExit as e:
        rc, out = e.code, "argparse"
    check("say --needs-task: gone", rc == 2, out)

def test_a_say_with_no_ordinary_head_still_gets_typed(home: Path) -> None:
    """A say-only queue (no ordinary head) still reaches deliver_queued, waiting_prompt and waiting_for."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[tuple[str, str]] = []
    real = (orcamod.alive, orcamod.send, orcamod.available, orcamod.typeable_now)
    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        orcamod.typeable_now = lambda h: True
        orcamod.live_terminals = lambda: {"term_x"}
        orcamod.send = lambda h, text, enter=True: (sent.append((h, text)), (True, ""))[1]
        # No live session yet: `say`'s own `push` (it calls `deliver_queued`
        # synchronously, same as any `ask`) cannot deliver it immediately, so
        # this proves the daemon's later, ordinary tick - not just the push -
        # can still find and tell a say with no head to hang off.

        check("say: waiting_for is False before anything is sent",
              not dmn_lifecycle.waiting_for(con, "backend"))
        ok, msg, rid = askmod.say("backend", "quick one - is the migration close?",
                                  requester="reviewer")
        check("say: opens with nothing else queued for backend", ok, msg)
        check("say: head_of_queue is still None - a say is never the head",
              db.head_of_queue(con, "backend") is None)
        check("say: but waiting_for now sees it",
              dmn_lifecycle.waiting_for(con, "backend"))
        check("say: push could not deliver it synchronously - no session yet",
              db.get_request(con, rid)["delivered_at"] is None)

        sessions_are("backend")
        dmn_messaging.tell_undelivered(con, "backend", lambda _m: None)
        check("say: the tick tells it once a session is up, with no head to hang off",
              any(f"#{rid}" in t for h, t in sent), str(sent))
        check("say: it was actually typed at backend's terminal",
              any(f"#{rid}" in t for h, t in sent), str(sent))
        check("say: marked delivered, and finished - a typed say owes nothing",
              db.get_request(con, rid)["delivered_at"] is not None
              and db.get_request(con, rid)["state"] == "landed", str(dict(db.get_request(con, rid))))
    finally:
        orcamod.alive, orcamod.send, orcamod.available, orcamod.typeable_now = real
        con.close()


def test_stuck_never_reads_a_send_now_row_as_waiting_behind_the_head(home: Path) -> None:
    """`stuck()` never calls a send_now row "waiting behind" the head."""
    con = _blocked_world(home)
    real_typeable, real_available = orcamod.typeable_now, orcamod.available
    try:
        orcamod.live_terminals = lambda: {"term_x"}
        orcamod.typeable_now = lambda h: False
        orcamod.available = lambda: False       # nothing can be typed: the say stays queued, held
        agents.register("product")   # DESIGN_TEAM: product -> eng.* (backend) is peer/async
        head = db.head_of_queue(con, "backend")
        con.execute("INSERT OR REPLACE INTO cursors(name, row_id, updated) VALUES(?,?,?)",
                    ("notify:backend", int(head["id"]), int(time.time())))
        ok, msg, rid = askmod.say("backend", "quick one", requester="product")
        check("setup: the say opens behind the real head, by id", ok, msg)
        con.execute("UPDATE requests SET ts = ? WHERE id = ?",
                    (int(time.time()) - actions.STUCK_AFTER_S - 60, rid))
        rows = [r for r in actions.stuck(con) if r["agent"] == "backend" and f"#{rid}" in r["what"]]
        check("stuck: a send_now row is never read as waiting behind the head",
              rows and all("waiting behind" not in r["why"] for r in rows), str(rows))
    finally:
        orcamod.typeable_now, orcamod.available = real_typeable, real_available


def test_is_lookup_bash_matches_the_allowlist_by_prefix(home: Path) -> None:
    """#1657: the read-only Bash allowlist, matched by command prefix so a
    flag on a listed command is still a look-up, but an unlisted command
    is not, whatever its own flags."""
    for cmd in ("git log", "git log --oneline -5", "git show HEAD", "git diff --stat",
               "git status", "ls", "ls -la", "cat foo.py",
               "head -n 20 bar.py", "ocp read 42", "ocp inbox --all", "ocp task list",
               "ocp team", "ocp task board"):
        check(f"is_lookup_bash: {cmd!r} is a look-up", actions.is_lookup_bash(cmd))
    for cmd in ("git commit -m x", "git push", "rm -rf /", "curl http://x", "pytest",
               "npm test", "gitlog", "cats foo.py",
               "git branch", "git branch -a", "git branch -D main"):
        check(f"is_lookup_bash: {cmd!r} is not a look-up", not actions.is_lookup_bash(cmd))


def test_is_lookup_bash_refuses_a_shell_operator_before_the_prefix_check(home: Path) -> None:
    """A pipe, redirect or chain is refused outright, whatever command it names."""
    for cmd in ("cat > x", "git log | sh", "head -1 a && make", "ls; rm -rf /",
               "cat foo `whoami`", "git log $(echo x)", "cat foo\nrm bar"):
        check(f"is_lookup_bash: {cmd!r} is refused for its shell operator, not "
              f"read as a look-up", not actions.is_lookup_bash(cmd))


def main() -> int:
    def tests(td):
        home = Path(td) / "orcapipe"
        home.mkdir()
        run(lambda: test_say_delivers_ahead_of_the_queue_and_never_parks(Path(td) / 'sayqueue'))
        run(lambda: test_a_manager_says_to_a_report_without_a_task(Path(td) / 'sayrule'))
        run(lambda: test_say_survives_enforce_edges_own_kind_recheck(Path(td) / 'sayedges'))
        run(lambda: test_a_reply_settles_the_request_it_answers_as_one_message(Path(td) / 'saysettle'))
        run(lambda: test_a_reply_to_the_founder_says_what_it_becomes_and_the_tail_says_how(Path(td) / 'sayfounder'))
        run(lambda: test_a_reply_never_settles_a_task_and_the_founder_guard_follows_the_target(Path(td) / 'saytask'))
        run(lambda: test_a_reply_to_a_say_is_a_say_in_its_own_thread(Path(td) / 'sayreply'))
        run(lambda: test_only_the_says_own_target_can_close_it_by_replying(
            Path(td) / 'sayclosetarget'))
        run(lambda: test_tell_text_gives_a_say_its_own_way_out_not_ocp_resolve(
            Path(td) / 'saytelltext'))
        run(lambda: test_inbox_unread_only_falls_back_to_a_marked_say_with_no_head(
            Path(td) / 'sayinbox'))
        run(lambda: test_inbox_unread_only_reads_the_blocked_head_through_effective_head(
            Path(td) / 'sayinboxblocked'))
        run(lambda: test_a_say_prints_no_way_out_and_needs_task_is_gone(Path(td) / 'sayneedstask'))
        run(lambda: test_a_say_with_no_ordinary_head_still_gets_typed(Path(td) / 'saynohead'))
        run(lambda: test_stuck_never_reads_a_send_now_row_as_waiting_behind_the_head(
            Path(td) / 'saystuck'))
        run(lambda: test_is_lookup_bash_matches_the_allowlist_by_prefix(Path(td) / 'saylookup'))
        run(lambda: test_is_lookup_bash_refuses_a_shell_operator_before_the_prefix_check(
            Path(td) / 'sayshellop'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
