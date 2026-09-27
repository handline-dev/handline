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
    finally:
        con.close()


def test_inbox_unread_only_falls_back_to_a_marked_say_with_no_head(home: Path) -> None:
    """Code review finding: `askmod.inbox(agent, unread_only=True)` read only
    `head_of_queue`, so a say with nothing else queued for its target - the
    exact case `head_of_queue` now excludes - read as an empty inbox, against
    its own docstring ("the one request currently waiting for `agent`")."""
    _say_world(home)
    check("inbox: empty before anything is sent", askmod.inbox("worker") == [])
    ok, msg, rid = askmod.say("worker", "quick one", requester="manager")
    check("say: opens", ok, msg)
    unread = askmod.inbox("worker")
    check("inbox: a say with no ordinary head is still what unread_only finds",
          len(unread) == 1 and unread[0]["id"] == rid, unread)


def test_say_delivery_line_and_needs_task(home: Path) -> None:
    """#1657: every say prints its own way out, and `say --needs-task`
    replies to the sender with a ready `task new` command instead of
    answering - only the receiver may send it, and only on a live say."""
    _say_world(home)
    rc, out = _cli(["say", "--needs-task", "1", "worker", "also this"], as_agent="manager")
    check("say --needs-task: refused with a target/body of its own, not silently "
          "dropped (code review finding)", rc == 1 and "no target or body" in out, out)

    rc, out = _cli(["say", "worker", "can you take a look at the flaky test?"],
                   as_agent="manager")
    check("say: opens", rc == 0, out)
    rid = int(out.split("queued as #", 1)[1].split()[0])
    check("say: prints its own delivery line, the receiver's way out",
          actions.delivery_line(rid) in out, out)
    rc, out = _cli(["read", str(rid)], as_agent="worker")
    check("say: `ocp read` prints the same delivery line for an unanswered say",
          rc == 0 and actions.delivery_line(rid) in out, out)

    rc, out = _cli(["say", "--needs-task", str(rid)], as_agent="manager")
    check("say --needs-task: refused for anyone but the say's own receiver",
          rc == 1 and "not sent to you" in out, out)
    rc, out = _cli(["say", "--needs-task", str(rid), "--of", "manager"], as_agent="worker")
    check("say --needs-task: --of is honored, not just $OCP_AGENT - the same "
          "receiver-only check refuses it for a different --of too",
          rc == 1 and "not sent to you" in out, out)
    rc, out = _cli(["say", "--needs-task", str(rid)], as_agent="worker")
    check("say --needs-task: the receiver may flag it, delivered back to the sender",
          rc == 0, out)
    flag_rid = int(out.split("queued as #", 1)[1].split()[0])
    con = db.connect()
    try:
        flag = db.get_request(con, flag_rid)
        check("say --needs-task: the reply goes to the original sender",
              flag["target"] == "manager", flag["target"])
        check("say --needs-task: it carries a ready `task new` command",
              "task new --owner worker" in flag["body"], flag["body"])
    finally:
        con.close()

    ok, msg, real_task_rid = askmod.ask("worker", "not a say", requester="manager")
    con = db.connect()
    try:
        row = db.get_request(con, real_task_rid)
    finally:
        con.close()
    why = actions.needs_task_refusal(row, "worker")
    check("say --needs-task: refused on a request that is not a say at all",
          why is not None and "not a say" in why, why)
    why = actions.needs_task_refusal(None, "worker")
    check("say --needs-task: refused on an id that names no request", why is not None, why)


def test_a_say_with_no_ordinary_head_still_gets_typed(home: Path) -> None:
    """Code review finding: `head_of_queue` skipping kind='say' rows made a
    say-only queue invisible to `deliver_queued`, `waiting_prompt` and
    `lifecycle.waiting_for` alike, since all three read a bare `head_of_queue`
    result and gave up the moment it came back None - the opposite of a say's
    own promise to always be delivered."""
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
        check("say: deliver_queued tells it anyway, with no head to hang off",
              dmn_delivery.deliver_queued(con, "backend", lambda _m: None))
        check("say: it was actually typed at backend's terminal",
              any(f"#{rid}" in t for h, t in sent), str(sent))
        check("say: marked delivered", db.get_request(con, rid)["delivered_at"] is not None)
    finally:
        orcamod.alive, orcamod.send, orcamod.available, orcamod.typeable_now = real
        con.close()


def test_say_to_the_founder_is_not_refused_for_a_timebox_it_never_asked_for(home: Path) -> None:
    """Code review finding: `say` always passed its own fixed timebox into
    `ask`, which refuses any timebox to the founder outright - so every say
    addressed to human failed, blaming a flag the caller never passed."""
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
    """Code review finding: `stuck()`'s per-row diagnostic named the head a
    send_now row was "waiting behind", the opposite of true - `queue_reason`
    (same file) already checks `send_now` first; `stuck()` did not."""
    con = _blocked_world(home)
    real_typeable = orcamod.typeable_now
    try:
        orcamod.live_terminals = lambda: {"term_x"}
        orcamod.typeable_now = lambda h: False  # already mid-send to the head; say only queues
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
        orcamod.typeable_now = real_typeable


def test_needs_task_command_quotes_a_title_with_its_own_quote(home: Path) -> None:
    """Code review finding: the ready-made `task new` command was built with
    a bare `'...'` wrap, so a title with its own apostrophe produced an
    unbalanced-quote command - exactly the text the receiver is meant to
    copy-paste verbatim."""
    command = actions.needs_task_command("worker", "check the worker's flaky test")
    check("needs_task_command: the apostrophe is escaped, not left to break the quoting",
          command == "ocp task new --owner worker "
                     "'check the worker'\"'\"'s flaky test'", command)
    import shlex
    check("needs_task_command: shlex agrees it is one well-formed command line, "
          "the title one word despite its own quote",
          shlex.split(command) == ["ocp", "task", "new", "--owner", "worker",
                                   "check the worker's flaky test"],
          shlex.split(command))


def test_is_lookup_bash_matches_the_allowlist_by_prefix(home: Path) -> None:
    """#1657: the read-only Bash allowlist, matched by command prefix so a
    flag on a listed command is still a look-up, but an unlisted command
    is not, whatever its own flags."""
    for cmd in ("git log", "git log --oneline -5", "git show HEAD", "git diff --stat",
               "git status", "git branch -a", "ls", "ls -la", "cat foo.py",
               "head -n 20 bar.py", "ocp read 42", "ocp inbox --all", "ocp task list",
               "ocp team", "ocp task board"):
        check(f"is_lookup_bash: {cmd!r} is a look-up", actions.is_lookup_bash(cmd))
    for cmd in ("git commit -m x", "git push", "rm -rf /", "curl http://x", "pytest",
               "npm test", "gitlog", "cats foo.py"):
        check(f"is_lookup_bash: {cmd!r} is not a look-up", not actions.is_lookup_bash(cmd))


def main() -> int:
    def tests(td):
        home = Path(td) / "orcapipe"
        home.mkdir()
        run(lambda: test_say_delivers_ahead_of_the_queue_and_never_parks(Path(td) / 'sayqueue'))
        run(lambda: test_say_survives_enforce_edges_own_kind_recheck(Path(td) / 'sayedges'))
        run(lambda: test_a_reply_to_a_say_is_a_say_in_its_own_thread(Path(td) / 'sayreply'))
        run(lambda: test_inbox_unread_only_falls_back_to_a_marked_say_with_no_head(
            Path(td) / 'sayinbox'))
        run(lambda: test_say_delivery_line_and_needs_task(Path(td) / 'sayneedstask'))
        run(lambda: test_a_say_with_no_ordinary_head_still_gets_typed(Path(td) / 'saynohead'))
        run(lambda: test_say_to_the_founder_is_not_refused_for_a_timebox_it_never_asked_for(
            Path(td) / 'sayhuman'))
        run(lambda: test_stuck_never_reads_a_send_now_row_as_waiting_behind_the_head(
            Path(td) / 'saystuck'))
        run(lambda: test_needs_task_command_quotes_a_title_with_its_own_quote(Path(td) / 'sayquote'))
        run(lambda: test_is_lookup_bash_matches_the_allowlist_by_prefix(Path(td) / 'saylookup'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
