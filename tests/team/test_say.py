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
        check("say: delivered ahead of the queue - marked send_now",
              row["send_now"] is not None)
        check("say: the fixed 30-minute reply window, not the routing edge's own default",
              row["timebox_s"] == askmod.SAY_TIMEBOX_S)
        check("say: the requester never parks on it",
              db.get_agent_state(con, "manager") is None
              or db.get_agent_state(con, "manager")["state"] != "PARKED")
    finally:
        con.close()


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
              original["state"] == "done" and original["verdict"] == "close - one file left",
              str(dict(original)))
    finally:
        con.close()
    con = db.connect()
    real_available = orcamod.available
    try:
        orcamod.available = lambda: False   # stuck() asks orca only if it must
        check("say: closing it silently means no timebox notice fires for an "
              "already-answered say",
              not any(r["id"] == rid for r in db.timeboxed_open(con)))
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
  - { from: "*", to: "human", kind: escalate, creates_gate: true }

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
              row["state"] == "done" and row["verdict"] == "yes", str(dict(row)))
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
    check("tell_text: it says it needs no answer instead", "needs no answer" in text
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
        ok, msg, tid = actions.task_new("manager", "worker", "the blocked head")
        check("setup: worker holds the task", ok, msg)
        head = db.task_requests(con, tid)[0]["id"]
        check("unblocked: inbox finds the plain head",
              [r["id"] for r in askmod.inbox("worker")] == [head])

        ok, msg, t_blocker = actions.task_new("worker", "manager", "unrelated")
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
              and db.get_request(con, rid)["state"] == "done", str(dict(db.get_request(con, rid))))
    finally:
        orcamod.alive, orcamod.send, orcamod.available, orcamod.typeable_now = real
        con.close()


def test_say_to_the_founder_is_not_refused_for_a_timebox_it_never_asked_for(home: Path) -> None:
    """A say to the founder carries no timebox at all, so `ask`'s own refusal never fires."""
    _say_world(home)
    hand_edit_team("""
agents:
  manager: {}
  worker:  {}

edges:
  - { from: "manager", to: "worker", relation: supervisor, response: fire_and_forget }
  - { from: "worker", to: "manager", relation: subordinate, response: fire_and_forget }
  - { from: "*", to: "human", kind: escalate, creates_gate: true }

limits:
  max_chain_depth: 4
  ask_rate_per_hour: 50
""")
    ok, msg, rid = askmod.say(askmod.HUMAN, "is this ready to ship?", requester="manager")
    check("say: to the founder is not refused", ok, msg)
    con = db.connect()
    try:
        check("say: carries no timebox to the founder - there is no park to end",
              db.get_request(con, rid)["timebox_s"] is None)
    finally:
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
        run(lambda: test_say_survives_enforce_edges_own_kind_recheck(Path(td) / 'sayedges'))
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
        run(lambda: test_say_to_the_founder_is_not_refused_for_a_timebox_it_never_asked_for(
            Path(td) / 'sayhuman'))
        run(lambda: test_stuck_never_reads_a_send_now_row_as_waiting_behind_the_head(
            Path(td) / 'saystuck'))
        run(lambda: test_is_lookup_bash_matches_the_allowlist_by_prefix(Path(td) / 'saylookup'))
        run(lambda: test_is_lookup_bash_refuses_a_shell_operator_before_the_prefix_check(
            Path(td) / 'sayshellop'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
