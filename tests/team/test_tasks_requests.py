"""Tests: requests, asks, tasks, answers and the inbox.

Run on its own: python3 tests/team/test_tasks_requests.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import functools
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.actions import stuck as stuckmod  # noqa: E402
from handline.session import stopgate  # noqa: E402



def _reading(test):
    """`test`, with `hl read` answered from the database (`support.request_read`)."""
    @functools.wraps(test)
    def run_it(home: Path) -> None:
        with support.daemon_says("request.read", support.request_read):
            test(home)
    return run_it

def test_the_quota_request_never_follows_a_redirect(home: Path) -> None:
    """auditor #2025's note: urllib's redirect handler copies Authorization
    onto the redirect, to any host. The quota request is sent with the
    account's key, so a redirect must be refused outright - exercised here
    against a real local server that answers the first request with a 301 to
    a second one holding a perfectly good quota answer. Refused, the read is
    could-not-look and the second endpoint is never asked; followed, it would
    be a reading and the key would have arrived there."""
    use_home(home)
    accounts.register("zai", vendor="opencode")
    cdir = vendors.config_dir("zai")
    (cdir / "opencode").mkdir(parents=True, exist_ok=True)
    (cdir / "opencode" / "auth.json").write_text(
        '{"zai-coding-plan": {"type": "api", "key": "SECRET-ZAI-KEY"}}')

    hits: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = self.path.split("?")[0]
            hits.append(path)
            if path == "/elsewhere":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(zai_quota(time.time() * 1000)).encode())
            else:
                self.send_response(301)
                self.send_header("Location", f"/elsewhere?from={self.path}")
                self.end_headers()

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    drv = vendors.get("opencode")
    real_url = drv.QUOTA_URL
    drv.QUOTA_URL = f"http://127.0.0.1:{srv.server_port}/quota"
    try:
        check("zai redirect: a redirect is could-not-look, not a reading",
              drv.usage_reading(cdir) is None)
        check("zai redirect: and the redirect target was never asked",
              hits == ["/quota"], str(hits))
    finally:
        drv.QUOTA_URL = real_url
        srv.shutdown()
        srv.server_close()


def test_db_dedup(home: Path) -> None:
    con = db.connect(home / "dedup.sqlite")
    try:
        first = db.add_event(con, "backend", "usage", {"in": 10}, dedup_key="msg_abc")
        second = db.add_event(con, "backend", "usage", {"in": 10}, dedup_key="msg_abc")
        check("db: first usage event stored", first is not None)
        check("db: duplicate message.id is dropped", second is None)
        check("db: no dedup key means always stored",
              db.add_event(con, "backend", "note") is not None)

        db.set_cursor(con, "fold", 0)
        for i in range(5):
            db.add_event(con, "backend", "e", {"i": i})
        rows = db.events_after(con, db.get_cursor(con, "fold"), agent="backend")
        check("db: cursor reads by row id", len(rows) >= 5)
        top = max(r["id"] for r in rows)
        db.set_cursor(con, "fold", top)
        check("db: advanced cursor yields nothing new",
              db.events_after(con, db.get_cursor(con, "fold"), agent="backend") == [])
    finally:
        con.close()


def test_ask_flow(home: Path) -> None:
    use_home(home)
    hand_edit_team(DESIGN_TEAM)
    for name in ("backend", "reviewer", "archivist", "marketing"):
        agents.register(name)

    ok, msg, rid = askmod.ask("reviewer", "review this", requester="backend")
    check("ask: allowed edge queues", ok and rid is not None, msg)
    inbox_file = home / "agents" / "reviewer" / "inbox" / f"{rid:06d}.json"
    check("ask: writes an inbox file", inbox_file.exists())
    payload = json.loads(inbox_file.read_text())
    check("ask: inbox payload carries routing",
          payload["response"] == "async" and "intake" not in payload, str(payload))
    check("ask: the queued line names no intake", "intake" not in msg, msg)

    ok, msg, _ = askmod.ask("marketing", "do this", requester="backend")
    check("ask: denied edge refuses", not ok and "product" in msg, msg)

    ok, msg, _ = askmod.ask("frontend", "do this", requester="backend")
    check("ask: unlisted edge refuses", not ok, msg)

    con = db.connect()
    try:
        denied = con.execute("SELECT COUNT(*) c FROM requests WHERE state='denied'").fetchone()["c"]
        check("ask: denials are recorded, not silent", denied == 2, str(denied))
        check("ask: a refusal by the routing table is written as a say (T766)",
              {r["kind"] for r in con.execute("SELECT kind FROM requests WHERE state = 'denied'")} == {"say"})
        st = db.get_agent_state(con, "backend")
        check("ask: an async requester is never parked on the reply (T628)",
              st is None or st["state"] != "PARKED", str(dict(st) if st else None))
    finally:
        con.close()

    ok, msg = askmod.resolve(rid, "looks good")
    check("resolve: closes the request", ok, msg)
    check("resolve: drains the inbox file", not inbox_file.exists())
    con = db.connect()
    try:
        st = db.get_agent_state(con, "backend")
        check("resolve: the reply queues the requester", st["state"] == "QUEUED", str(dict(st)))
        check("resolve: queueing clears the fallback wake_at", st["wake_at"] is None)
    finally:
        con.close()

    # fire_and_forget must not park the requester. Asked from marketing, whose
    # first matching edge really is `* -> archivist`. (From reviewer it would
    # not be: `reviewer -> *` appears earlier and edges are first-match-wins.)
    ok, _, _ = askmod.ask("archivist", "log this", requester="marketing")
    check("ask: fire_and_forget ask is accepted", ok)
    con = db.connect()
    try:
        st = db.get_agent_state(con, "marketing")
        check("ask: fire_and_forget does not park the requester", st is None, str(st and dict(st)))
    finally:
        con.close()

    # Edge order is significant, and the design's own policy relies on it.
    t = team.Team(yamlcompat.parse(DESIGN_TEAM))
    check("ask: earlier edge wins over a later one",
          t.route("reviewer", "archivist").response == "async",
          t.route("reviewer", "archivist").reason)

    # Chain depth must actually stop a loop.
    parent = rid
    depth_blocked = False
    for _ in range(6):
        ok, msg, new = askmod.ask("reviewer", "again", requester="backend", parent_id=parent)
        if not ok and "chain depth" in msg:
            depth_blocked = True
            break
        parent = new
    check("ask: chain depth stops runaway loops", depth_blocked)


def test_one_request_at_a_time(home: Path) -> None:
    """Request #189: an agent is given one request at a time - the head of
    its queue - and the next only once it resolves or dismisses this one.
    Before delivery its own requester may still replace or withdraw it;
    once delivered, neither applies."""
    use_home(home)
    hand_edit_team(DESIGN_TEAM)
    for name in ("backend", "reviewer"):
        agents.register(name)
    con = db.connect()
    try:
        ok, msg, first = askmod.ask("reviewer", "task one", requester="backend")
        check("one-at-a-time: the first ask queues", ok, msg)
        check("one-at-a-time: the head is the first request",
              db.head_of_queue(con, "reviewer")["id"] == first,
              db.head_of_queue(con, "reviewer")["id"])
        # Delivery itself - the daemon actually typing this into a terminal -
        # is exercised in daemon tests; here the head is marked delivered
        # directly, the way `deliver_queued` or a self-view of `hl inbox`
        # would, to set up what withdraw/replace are gated on.
        db.mark_delivered(con, first)

        ok, msg, second = askmod.ask("reviewer", "task two", requester="backend")
        check("one-at-a-time: a second ask still queues", ok, msg)
        check("one-at-a-time: the head is still the first - the second waits behind it",
              db.head_of_queue(con, "reviewer")["id"] == first,
              db.head_of_queue(con, "reviewer")["id"])
        check("one-at-a-time: the second was never marked delivered",
              db.get_request(con, second)["delivered_at"] is None)
        check("one-at-a-time: two are queued for reviewer",
              db.queue_depth(con, "reviewer") == 2, db.queue_depth(con, "reviewer"))

        ok, msg, third = askmod.ask("reviewer", "task three", requester="backend")
        check("one-at-a-time: a third is accepted", ok, msg)

        ok, msg = askmod.withdraw(third, requester="backend")
        check("one-at-a-time: withdrawing an undelivered request is allowed", ok, msg)
        check("one-at-a-time: it is gone from the queue", db.get_request(con, third)["state"],
              db.get_request(con, third)["state"])
        check("one-at-a-time: as 'withdrawn'", db.get_request(con, third)["state"] == "withdrawn")

        ok, msg = askmod.withdraw(first, requester="backend")
        check("one-at-a-time: a delivered request cannot be withdrawn",
              not ok and "already delivered" in msg, msg)

        # Resolving the head hands delivery to the next one, never a withdrawn row.
        ok, msg = askmod.resolve(first, "done")
        check("one-at-a-time: resolving the head succeeds", ok, msg)
        new_head = db.head_of_queue(con, "reviewer")
        check("one-at-a-time: the next queued row becomes the head",
              new_head["id"] == second, new_head["id"])
        check("one-at-a-time: withdrawn rows never become the head",
              new_head["state"] == "queued", new_head["state"])
    finally:
        con.close()


def test_own_withdraw_is_not_an_answer(home: Path) -> None:
    """A requester's own withdraw stores 'withdrawn' and is marked told, so it
    never comes back to them as an answer or shows in the founder's For you."""
    use_home(home)
    hand_edit_team(DESIGN_TEAM)
    for name in ("backend", "reviewer"):
        agents.register(name)
    con = db.connect()
    try:
        for who in ("backend", db.HUMAN):
            ok, msg, rid = askmod.ask("reviewer", f"from {who}", requester=who)
            check(f"own withdraw: {who} asks", ok, msg)
            ok, msg = askmod.withdraw(rid, requester=who)
            check(f"own withdraw: {who} withdraws", ok, msg)
            check(f"own withdraw: {who}'s row is withdrawn",
                  db.get_request(con, rid)["state"] == "withdrawn")
            check(f"own withdraw: not an unannounced answer to {who}",
                  rid not in [r["id"] for r in db.unannounced_answers(con, who)])
            check(f"own withdraw: not in the mailbox for {who}",
                  rid not in [m["id"] for m in actions.mailbox_rows(con)])
    finally:
        con.close()


def test_migration_marks_only_each_targets_head_delivered(home: Path) -> None:
    """Review of buddyron/one-task-at-a-time: the first version of this
    migration marked every existing queued row delivered, so none was left
    to promote ahead of the rest."""
    import sqlite3
    home.mkdir(parents=True, exist_ok=True)
    p = home / "old.sqlite"
    old = sqlite3.connect(p)
    old.execute("""
        CREATE TABLE requests (
          id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,
          requester TEXT NOT NULL, target TEXT NOT NULL, body TEXT NOT NULL,
          files TEXT, relation TEXT, response TEXT, kind TEXT,
          chain_depth INTEGER NOT NULL DEFAULT 0, parent_id INTEGER,
          chain_id TEXT, state TEXT NOT NULL DEFAULT 'queued', verdict TEXT,
          announced_at INTEGER, resolved_at INTEGER
        )
    """)
    now = int(time.time())
    for i, body in enumerate(["first", "second", "third"]):
        old.execute(
            "INSERT INTO requests(ts, requester, target, body, state, chain_id) "
            "VALUES(?,?,?,?,?,?)", (now, "backend", "keeper", body, "queued", str(i + 1)))
    old.commit()
    old.close()

    con = support.upgrade(p)
    try:
        rows = con.execute(
            "SELECT id, body, queue_order, delivered_at FROM requests ORDER BY id").fetchall()
        check("migration: queue_order backfilled to id",
              [r["queue_order"] for r in rows] == [r["id"] for r in rows], str([dict(r) for r in rows]))
        check("migration: only the head is marked delivered",
              rows[0]["delivered_at"] is not None and rows[1]["delivered_at"] is None
              and rows[2]["delivered_at"] is None, str([dict(r) for r in rows]))
    finally:
        con.close()


def test_retire_if_undelivered_is_the_gate_against_the_delivery_race(home: Path) -> None:
    """Review of buddyron/one-task-at-a-time: a plain read-then-write let a
    delivery land between checking delivered_at and retiring the row. The
    atomic conditional update is the only thing withdraw/replace may trust."""
    use_home(home)
    con = db.connect()
    try:
        rid = db.add_request(con, "backend", "reviewer", "task", None,
                             "peer", "async", None, 0, None)
        check("retire_if_undelivered: succeeds on an undelivered row",
              db.retire_if_undelivered(con, rid, "withdrawn"))
        check("retire_if_undelivered: the row actually moved",
              db.get_request(con, rid)["state"] == "withdrawn")

        rid2 = db.add_request(con, "backend", "reviewer", "task two", None,
                              "peer", "async", None, 0, None)
        # A delivery "landing" between a caller's own check and its write is
        # exactly db.mark_delivered running first.
        db.mark_delivered(con, rid2)
        check("retire_if_undelivered: refuses once delivered_at is set",
              not db.retire_if_undelivered(con, rid2, "withdrawn"))
        check("retire_if_undelivered: and does not touch the row",
              db.get_request(con, rid2)["state"] == "queued")
    finally:
        con.close()


def test_asks_have_no_hourly_cap(home: Path) -> None:
    """There is no limit on asks an hour: a team.yaml that still names the old ask_rate_per_hour loads, and
    every ask past it goes out, its reply says nothing of a count, and the agent is never THROTTLED."""
    use_home(home)
    hand_edit_team(
        "agents:\n  a: {}\n  b: {}\n"
        'edges:\n  - { from: "*", to: "*", relation: peer, response: fire_and_forget }\n'
        "limits:\n  max_chain_depth: 99\n  ask_rate_per_hour: 3\n"
    )
    agents.register("a")
    agents.register("b")
    results = [askmod.ask("b", f"n{i}", requester="a") for i in range(25)]
    check("no cap: 25 asks in a row all go out", all(ok for ok, _, _ in results), str([m for ok, m, _ in results if not ok]))
    check("no cap: no reply counts asks", not any("this hour" in m for _, m, _ in results), results[-1][1])
    con = db.connect()
    try:
        row = db.get_agent_state(con, "a")
        check("no cap: the agent is not THROTTLED", row is None or row["state"] != "THROTTLED", str(row and dict(row)))
    finally:
        con.close()


def test_a_runaway_is_stopped_and_raised_to_the_founder(home: Path) -> None:
    """A runaway must be stopped AND raised to the founder - a request that silently
    vanishes leaves the team stuck with nobody knowing why."""
    use_home(home)
    hand_edit_team(DESIGN_TEAM)
    for n in ("backend", "reviewer", "product", "marketing", "archivist"):
        agents.register(n)

    # A -> B, then B -> A inside the same chain is a cycle.
    ok, _, first = askmod.ask("reviewer", "please review", requester="backend")
    check("chain: the first ask starts a chain", ok)
    ok, msg, _ = askmod.ask("backend", "here are my notes", requester="reviewer",
                            parent_id=first)
    check("chain: A->B->A is stopped", not ok and "cycle" in msg, msg)
    check("chain: and raised to the founder, not dropped", "Raised to the founder" in msg, msg)

    con = db.connect()
    try:
        esc = con.execute(
            "SELECT * FROM requests WHERE target='human' AND kind='task'"
        ).fetchall()
        check("chain: the decision task's card is a real queued request", len(esc) == 1, str(len(esc)))
        check("chain: it carries the original body",
              "here are my notes" in esc[0]["body"], esc[0]["body"][:80])
        chain = db.chain_members(con, str(first))
        check("chain: everything shares one chain_id", len(chain) >= 2, str(len(chain)))
    finally:
        con.close()


def test_review_pingpong(home: Path) -> None:
    use_home(home)
    hand_edit_team(DESIGN_TEAM)
    for n in ("backend", "reviewer", "product"):
        agents.register(n)

    parent = None
    outcomes = []
    for i in range(5):
        ok, msg, rid = askmod.ask("reviewer", f"round {i}", files="src/auth.ts",
                                  requester="backend", parent_id=parent)
        outcomes.append((ok, msg))
        if not ok:
            break
        # The reviewer's answer continues the same chain without being a cycle.
        parent = rid
        con = db.connect()
        try:
            _set_request_state(con, rid, "landed", "ok")
        finally:
            con.close()

    stopped = [m for ok, m in outcomes if not ok]
    check("review: ping-pong on one file is eventually stopped", stopped, str(outcomes[-1]))
    check("review: the reason names the file",
          stopped and "src/auth.ts" in stopped[0], stopped[0] if stopped else "")
    check("review: and says it is probably a spec question",
          stopped and "spec question" in stopped[0], stopped[0] if stopped else "")


def test_a_resolved_ask_does_not_stay_in_progress_across_a_park(home: Path) -> None:
    """The event that would tell a fold an outstanding ask closed only ever
    carries a count (handline/daemon/'s `answered` event), never which id - and for
    an agent that was parked when the answer landed, there may be no event at
    all yet. Either way the store already knows; the fold has to check it
    directly rather than wait for an event that may never name the id."""
    use_home(home)
    hand_edit_team(DESIGN_TEAM)
    agents.register("backend")

    con = db.connect()
    try:
        rid = db.add_request(con, "backend", "auditor", "review this", None,
                             "peer", "fire_and_forget", None, 0, None)
        db.add_event(con, "backend", "ask", {"request_id": rid, "target": "auditor"})
        top = db.max_event_id(con)
        # A fold already ran and published the ask as in progress, before the
        # park - the cursor is past it, same as in production.
        db.set_cursor(con, "fold:backend", top)
    finally:
        con.close()

    day = foldmod.today()
    previous = digest.Digest(
        date=day, agent="backend", updated="09:00", folded_through=top,
        sections={"done": ["set up the branch"], "in_progress": [f"Auditor request {rid} dispatched"],
                  "decided": [], "blocked": [], "next": [], "base_candidates": []})
    foldmod.journal_path("backend", day).write_text(digest.render(previous))

    # Answered while backend was parked: the store is updated, but nothing
    # tells the agent's own event log which id it was - the daemon's delivery
    # path only fires that event into a live session (design section 5).
    con = db.connect()
    try:
        _set_request_state(con, rid, "landed", verdict="looks good", announced=True)
    finally:
        con.close()

    # --- no new event at all: the "nothing to fold" path still checks the store
    ok, msg = foldmod.fold("backend", use_model=True, fold_account="alpha")
    check("fold: an unchanged cursor still says there was nothing new", "nothing new" in msg, msg)
    handed = digest.parse(foldmod.journal_path("backend", day).read_text())
    check("fold: the resolved ask is not still in progress after an event-free refresh",
          not any(str(rid) in i for i in handed.items("in_progress")), handed.items("in_progress"))
    check("fold: and it reads as done", any(str(rid) in i for i in handed.items("done")),
          handed.items("done"))

    # --- a fold with a model that fails to reconcile it is still corrected
    # (the refresh above already corrected the journal, so put the stale line back)
    foldmod.journal_path("backend", day).write_text(digest.render(previous))
    con = db.connect()
    try:
        db.add_event(con, "backend", "commit", {"sha": "abc", "subject": "more work"})
        top2 = db.max_event_id(con)
    finally:
        con.close()
    stuck = digest.render(digest.Digest(
        date=day, agent="backend", updated="09:05", folded_through=top2,
        sections={"done": ["set up the branch"], "in_progress": [f"Auditor request {rid} dispatched"],
                  "decided": [], "blocked": [], "next": [], "base_candidates": []}))
    real = foldmod.run_model
    try:
        foldmod.run_model = lambda *a, **k: (True, stuck)
        ok, msg = foldmod.fold("backend", use_model=True, fold_account="alpha")
        check("fold: succeeds even though the model carried the resolved ask forward", ok, msg)
    finally:
        foldmod.run_model = real
    published = digest.parse(foldmod.journal_path("backend", day).read_text())
    check("fold: the store overrules a model that left a resolved ask in progress",
          not any(str(rid) in i for i in published.items("in_progress")),
          published.items("in_progress"))
    check("fold: completed work named earlier is not lost in the correction",
          "set up the branch" in published.items("done"))


def test_reconcile_resolved_needs_an_explicit_reference_not_a_bare_number(home: Path) -> None:
    """auditor's #499/#1573: a fold's own errors are one-way - build_prompt
    forbids the very next fold from ever moving a line back out of Done - so
    a wrong close is permanent in the document handline/daemon/ tells an agent with
    no handoff to read. \\d+ read every number in a line as a request id, and
    request ids are small integers that nearly all resolve. Reproduced with
    requests 1-8 done: "Phase 4 daemon rewrite, step 2 of 7" (ids 4, 2, 7,
    all done), "rebase onto main, 3 conflicts left" and "7d reset tracking
    needs founder call" (auditor's exact three) all moved to Done under the
    old regex. Only #N or "request N" is close enough to certain."""
    _reconcile_world(home)
    con = db.connect()
    try:
        for _ in range(8):
            rid = db.add_request(con, "backend", "auditor", "x", None, "peer",
                                 "fire_and_forget", None, 0, None)
            _set_request_state(con, rid, "landed", verdict="ok")

        prose = ["Phase 4 daemon rewrite, step 2 of 7",
                "rebase onto main, 3 conflicts left",
                "7d reset tracking needs founder call"]
        d = digest.Digest(date="2026-09-22", agent="backend", updated="09:00",
                          folded_through=1,
                          sections={"in_progress": prose[:2], "blocked": prose[2:],
                                   "done": [], "decided": [], "next": [], "base_candidates": []})
        out = foldmod.reconcile_resolved(con, d)
        check("reconcile: a bare number in prose is not read as a request id",
              out.items("in_progress") == prose[:2] and out.items("blocked") == prose[2:],
              str(out.sections))
        check("reconcile: nothing moved to Done on prose alone",
              out.items("done") == [], out.items("done"))

        # The control: an explicit #N to that same resolved request IS closed.
        explicit = digest.Digest(date="2026-09-22", agent="backend", updated="09:00",
                                 folded_through=1,
                                 sections={"in_progress": [f"waiting on #{rid}"],
                                          "blocked": [], "done": [], "decided": [],
                                          "next": [], "base_candidates": []})
        out2 = foldmod.reconcile_resolved(con, explicit)
        check("reconcile: an explicit #N to a done request is closed",
              out2.items("in_progress") == [] and f"waiting on #{rid}" in out2.items("done"),
              str(out2.sections))
    finally:
        con.close()


def test_reconcile_resolved_only_state_done_counts(home: Path) -> None:
    """auditor's #499 should-fix, raised to blocking by #1573: `state !=
    'queued'` let denied, withdrawn and replaced asks - work that never
    happened - count as done. Only 'done' does."""
    _reconcile_world(home)
    con = db.connect()
    try:
        never_happened = {}
        for state in ("denied", "withdrawn", "replaced", "rejected"):
            rid = db.add_request(con, "backend", "auditor", "x", None, "peer",
                                 "fire_and_forget", None, 0, None)
            _set_request_state(con, rid, state)
            never_happened[state] = rid
        done_rid = db.add_request(con, "backend", "auditor", "y", None, "peer",
                                  "fire_and_forget", None, 0, None)
        _set_request_state(con, done_rid, "landed", verdict="ok")
        queued_rid = db.add_request(con, "backend", "auditor", "z", None, "peer",
                                    "fire_and_forget", None, 0, None)

        d = digest.Digest(date="2026-09-22", agent="backend", updated="09:00", folded_through=1,
                          sections={"in_progress": [f"waiting on #{rid}" for rid in never_happened.values()]
                                   + [f"waiting on #{done_rid}", f"waiting on #{queued_rid}"],
                                   "blocked": [], "done": [], "decided": [], "next": [],
                                   "base_candidates": []})
        out = foldmod.reconcile_resolved(con, d)
        for state, rid in never_happened.items():
            line = f"waiting on #{rid}"
            check(f"reconcile: {state} is not done - the ask never reached its target",
                  line in out.items("in_progress") and line not in out.items("done"),
                  str(out.sections))
        check("reconcile: a done request is closed",
              f"waiting on #{done_rid}" in out.items("done"), str(out.sections))
        check("reconcile: a still-queued one stays put",
              f"waiting on #{queued_rid}" in out.items("in_progress"), str(out.sections))
    finally:
        con.close()


def test_fold_degraded_path_also_reconciles_resolved_asks(home: Path) -> None:
    """#499 should-fix: only the no-event and model paths were exercised.
    _degraded carries previous's in_progress/blocked lines forward verbatim
    (design section 5's deterministic fallback), and reconcile_resolved runs
    on that too - a fold that could not reach a model must not leave a
    provably-done line stuck forever either."""
    _reconcile_world(home)
    con = db.connect()
    try:
        rid = db.add_request(con, "backend", "auditor", "review this", None,
                             "peer", "fire_and_forget", None, 0, None)
        db.add_event(con, "backend", "ask", {"request_id": rid, "target": "auditor"})
    finally:
        con.close()

    day = foldmod.today()
    previous = digest.Digest(
        date=day, agent="backend", updated="09:00", folded_through=0,
        sections={"done": ["set up the branch"], "in_progress": [f"waiting on #{rid}"],
                  "decided": [], "blocked": [], "next": [], "base_candidates": []})
    foldmod.journal_path("backend", day).write_text(digest.render(previous))

    con = db.connect()
    try:
        _set_request_state(con, rid, "landed", verdict="looks good", announced=True)
        # A second, unrelated event so the fold is not the no-event path.
        db.add_event(con, "backend", "commit", {"sha": "abc", "subject": "more work"})
    finally:
        con.close()

    ok, msg = foldmod.fold("backend", use_model=False, fold_account="alpha")
    check("fold: the degraded path runs (no model requested)", not ok, msg)
    published = digest.parse(foldmod.journal_path("backend", day).read_text())
    check("fold: the degraded fold also closes a provably-done ask",
          f"waiting on #{rid}" in published.items("done")
          and f"waiting on #{rid}" not in published.items("in_progress"),
          str(published.sections))
    check("fold: and does not lose completed work already on the page",
          "set up the branch" in published.items("done"), published.items("done"))


def test_agents_stays_a_leaf_no_cycle_through_collect_or_vendors() -> None:
    """#1784: agents.py -> collect -> vendors -> agents was a real cycle,
    surviving only because vendors/__init__.py happens to touch `agents.X`
    inside function bodies rather than at module level - an accident holding
    a door shut, not a guarantee. effective_context_limit moved to status.py
    (whose three callers already import status) so agents.py would not have
    to reach upward at all. This is the guard that keeps it that way: nothing
    else needs agents to import collect or vendors, so if either import
    reappears at module level, this is the fastest possible failure, not a
    hang or a partial-module AttributeError three imports later.

    auditor #1813: the first cut of this guard collected `n.module` off
    every ImportFrom node - which is None for `from . import collect, ...`
    (a relative import with no module name; the imported names live in
    `n.names` instead), so it saw only {None, ...} and passed whatever
    agents.py imported, the exact cycle #1784 asked removed. Also walked the
    whole tree, which would have caught agents.py's own deliberate
    function-body `from . import db, status` (already on main, unrelated to
    this cycle) had it named collect or vendors. Fixed: only `tree.body`
    (module level), and both shapes of a relative import - `from . import X`
    (X in `n.names`) and `from .X import Y` (X is `n.module`)."""
    import ast
    tree = ast.parse((ROOT / "handline" / "team" / "agents.py").read_text())
    imported: set[str] = set()
    for n in tree.body:
        if not isinstance(n, ast.ImportFrom):
            continue
        if n.module:
            imported.add(n.module.split(".")[0])
        else:
            imported.update(a.name for a in n.names)
    check("agents.py imports neither collect nor vendors",
          not ({"collect", "vendors"} & imported), imported)


def test_a_reply_to_a_peers_ask_needs_no_edge_back(home: Path) -> None:
    """T582: with resolve gone, `say --parent` is the only way to answer, so a reply
    to a request someone sent you goes back to them without an edge; a message of
    your own still needs one, and an explicit deny still wins."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    hand_edit_team("""
agents:
  asker: {}
  worker: {}
  other: {}
edges:
  - { from: "asker", to: "worker", relation: peer, response: async }
  - { from: "asker", to: "other", relation: peer, response: async }
deny:
  - { from: "other", to: "asker", reason: "no replies" }
limits:
  max_chain_depth: 4
""")
    for n in ("asker", "worker", "other"):
        agents.register(n)
    ok, msg, rid = askmod.ask("worker", "please look", requester="asker")
    check("setup: asker asks worker", ok, msg)
    ok, msg, plain = askmod.say("asker", "unprompted", requester="worker")
    check("reply: a message of the worker's own to the asker is still denied without an edge",
          not ok and "no edge declares worker -> asker" in msg, msg)
    ok, msg, reply = askmod.say("asker", "looked, fine", requester="worker", parent_id=rid)
    check("reply: the answer to the ask goes back without an edge", ok and reply is not None, msg)
    con = db.connect()
    try:
        check("reply: it settles the ask it answers",
              db.get_request(con, rid)["state"] == "landed"
              and db.shown_verdict(db.get_request(con, rid)) == "looked, fine")
    finally:
        con.close()
    # An agent's own session cannot verify team.yaml: the daemon re-checks its request at
    # delivery, and must let the reply through too.
    con = db.connect()
    try:
        con.execute("UPDATE requests SET edge_ok = NULL WHERE id = ?", (reply,))
        dmn_delivery.enforce_edges(con, "asker", lambda _m: None)
        row = db.get_request(con, reply)
        check("reply: the daemon's re-check at delivery lets it through",
              row["state"] != "denied" and row["edge_ok"] == 1, str(dict(row)))
    finally:
        con.close()
    ok, msg, rid2 = askmod.ask("other", "please look too", requester="asker")
    ok, msg, _ = askmod.say("asker", "no thanks", requester="other", parent_id=rid2)
    check("reply: an explicit deny still refuses it", not ok and "no replies" in msg, msg)
    ok, msg, _ = askmod.say("other", "wrong way round", requester="worker", parent_id=rid)
    check("reply: only the asker's own thread counts - not a third agent",
          not ok and "no edge declares" in msg, msg)


def test_ask_respects_terminal_state(home: Path) -> None:
    use_home(home)
    hand_edit_team(DESIGN_TEAM)
    for n in ("backend", "reviewer"):
        agents.register(n)
    con = db.connect()
    try:
        db.set_agent_state(con, "backend", "STALLED", detail="needs a human")
    finally:
        con.close()
    askmod.ask("reviewer", "review this", requester="backend")
    con = db.connect()
    try:
        st = db.get_agent_state(con, "backend")
        check("ask: an agent cannot park its own STALLED away",
              st["state"] == "STALLED", str(dict(st)))
    finally:
        con.close()


def test_the_config_dir_and_its_layout_are_the_drivers_answers(home: Path) -> None:
    """Step 2 of design/03 §7: which variable pins a session to one account,
    and how that directory is laid out inside, are the vendor's answers - here
    the vendor the account records, not the default's.
    """
    os.environ["OCP_HOME"] = str(home)
    real = obsmod.env_of
    try:
        with second_vendor(logged_in=False):
            accounts.register("gamma", vendor="zai")
            cdir = vendors.config_dir("gamma")
            check("accounts: the config dir is named by the account's vendor",
                  cdir == paths.account_dir("gamma") / ".zai" and cdir.is_dir(), str(cdir))
            env = accounts.env_for("gamma")
            check("accounts: the launch environment sets the driver's variable to it",
                  env.get("ZAI_HOME") == str(cdir), str({k: v for k, v in env.items() if "HOME" in k}))
            os.environ["CLAUDE_CONFIG_DIR"] = str(paths.account_dir("alpha") / ".claude")
            try:
                env = accounts.env_for("gamma")
            finally:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            check("accounts: and drops another vendor's variable inherited from the caller",
                  "CLAUDE_CONFIG_DIR" not in env, env.get("CLAUDE_CONFIG_DIR", ""))
            check("accounts: the login command is the driver's, with its variable",
                  accounts.login_command("gamma") == f'ZAI_HOME="{cdir}" glmcode login',
                  accounts.login_command("gamma"))

            # account_of_process: every vendor's variable is asked; the layout
            # under accounts/ is ours, so the account still falls out of the path.
            asked: list[str] = []

            def env_of(pid, name):
                asked.append(name)
                return str(cdir) if name == "ZAI_HOME" else None

            obsmod.env_of = env_of
            got = obsmod.account_of_process(1)
            check("observe: the config-dir variable read off the process is each driver's",
                  "ZAI_HOME" in asked and "CLAUDE_CONFIG_DIR" in asked, str(asked))
            check("observe: and the account still comes out of the path under accounts/",
                  got == "gamma", repr(got))

            # The layout inside that directory: what a working directory is called
            # there, and where its transcripts hang off.
            check("collect: a working directory is named the vendor's way",
                  collect.encode_cwd("gamma", "/tmp/a/b") == "_tmp_a_b",
                  collect.encode_cwd("gamma", "/tmp/a/b"))
            check("collect: and the transcript directory hangs off the driver's memory root",
                  collect.project_dir("gamma", "/tmp/a/b") == cdir / "sessions" / "_tmp_a_b",
                  str(collect.project_dir("gamma", "/tmp/a/b")))

            # What is linked into an account's config dir is the vendor's list,
            # less what it never touches - here "skills", which claude shares.
            shared = vendors.shared_dir("claude")
            (shared / "skills").mkdir(parents=True, exist_ok=True)
            # gamma's own shared dir has one too, so only never_touch keeps
            # "skills" out of unlinked() - not the item's absence there.
            gshared = vendors.shared_dir(vendors.for_account("gamma").name)
            (gshared / "skills").mkdir(parents=True, exist_ok=True)
            check("accounts: setup - the item exists in gamma's shared dir, and gamma never touches it",
                  (gshared / "skills").exists()
                  and "skills" in vendors.for_account("gamma").never_touch())
            accounts.register("gamma")
            check("accounts: an item the driver never touches is not linked",
                  not (cdir / "skills").exists())
            check("accounts: and it is not reported as an account's to relink either",
                  ("gamma", "skills") not in accounts.unlinked(), str(accounts.unlinked()))
            accounts.relink_all()
            check("accounts: nor linked by the catch-up pass",
                  not (cdir / "skills").exists())

            # The transcript search runs down the same root, one level deep.
            tdir = cdir / "sessions" / "_tmp_a_b"
            tdir.mkdir(parents=True, exist_ok=True)
            (tdir / "sess1.jsonl").write_text("{}\n")
            check("handoff: a transcript is looked for under the driver's memory root",
                  handoffmod.find_transcript("sess1", "gamma") == tdir / "sess1.jsonl",
                  str(handoffmod.find_transcript("sess1", "gamma")))

            # The token log walks that root too: the decoy sits where claude keeps
            # its transcripts, and must not be read for a vendor that does not.
            from handline.usage import tokenlog
            (tdir / "s2.jsonl").write_text(_call_line("req_driver", 10))
            decoy = cdir / "projects" / "-w-x"
            decoy.mkdir(parents=True, exist_ok=True)
            (decoy / "s3.jsonl").write_text(_call_line("req_claudes_place", 10))
            con = db.connect()
            try:
                tokenlog.ingest(con, "gamma")
                reqs = {r["req"] for r in con.execute("SELECT req FROM token_calls")}
            finally:
                con.close()
            check("tokenlog: the transcripts read are the ones under the driver's root",
                  reqs == {"req_driver"}, str(reqs))

            # The command the founder is handed to trust a worktree names the
            # variable their tool reads, and their tool, not claude's.
            made: list[str] = []
            real_ct = termmod.create_terminal
            try:
                termmod.create_terminal = lambda cmd, **k: (made.append(cmd), None)[1]
                actions.trust_worktrees("gamma", [("backend", "/w/backend")])
            finally:
                termmod.create_terminal = real_ct
            check("actions: the trust command sets the driver's config-dir variable",
                  made and "ZAI_HOME=" in made[0] and "CLAUDE_CONFIG_DIR" not in made[0]
                  and made[0].endswith(" /opt/zai/bin/glmcode"), str(made))

            # And the name doctor prints, so its reader is told which variable to
            # go and set rather than claude's.
            cdir.rename(cdir.with_name(".zai-gone"))
            r = doctor.Report()
            doctor._check_accounts(r)
            lines = [name for _lv, name, _d in r.rows]
            check("doctor: the missing-config-dir line names the driver's variable",
                  "account gamma: ZAI_HOME missing" in lines, str(lines))
    finally:
        obsmod.env_of = real


def test_db_dedup_only_swallows_dedup(home: Path) -> None:
    con = db.connect(home / "strict.sqlite")
    try:
        check("db: a real constraint violation is not silently swallowed",
              _raises(lambda: con.execute(
                  "INSERT INTO events(ts, agent, kind) VALUES(?,NULL,?)", (1, "k"))))
    finally:
        con.close()


def test_blocked_agents_come_due_in_one_request(home: Path) -> None:
    """Three at once - what the first tick after this ships would find - are one
    request, and it closes when the last of them clears, not the first."""
    con = _blocked_world(home)
    for name in ("worker", "reviewer"):
        agents.register(name)
        db.set_terminal(con, name, f"term_{name}", "wt")
        db.set_agent_state(con, name, dmn_states.IDLE)
        db.add_request(con, "marina", name, "carry on", None, "peer", "async", None, 0, None)
    sessions_are("backend", "worker", "reviewer")
    screens = {"backend": termmod.Blocker(termmod.MODAL, '"A"'),
               "worker": termmod.Blocker(termmod.MODAL, '"B"'),
               "reviewer": termmod.Blocker(termmod.UNREADABLE)}
    handles = {"term_x": "backend", "term_worker": "worker", "term_reviewer": "reviewer"}
    termmod.screen_blocker = lambda h: screens[handles[h]]
    t0 = int(time.time())
    try:
        blockedmod.sweep(con, lambda _m: None, t0)
        blockedmod.sweep(con, lambda _m: None, t0 + blockedmod.DECISION_AFTER_S)
        esc = _decisions(con)
        check("blocked: three due in the same pass are one request", len(esc) == 1, str(esc))
        body = db.get_request(con, esc[0]["id"])["body"]
        check("blocked: naming each", all(n in body for n in ("backend", "worker", "reviewer")), body)

        screens["backend"] = None
        blockedmod._checked.clear()
        blockedmod.sweep(con, lambda _m: None, t0 + blockedmod.DECISION_AFTER_S + 20)
        check("blocked: one clearing leaves the request open for the others",
              _decisions(con)[0]["state"] == "queued", str([dict(r) for r in _decisions(con)]))
        screens["worker"] = screens["reviewer"] = None
        blockedmod._checked.clear()
        blockedmod.sweep(con, lambda _m: None, t0 + blockedmod.DECISION_AFTER_S + 40)
        check("blocked: the last one closes it", _decisions(con)[0]["state"] == "landed")
    finally:
        con.close()


def test_a_trust_screen_is_one_founder_request(home: Path) -> None:
    """The wake raises the trust screen and stalls the agent; the sweep then
    sees a STALLED block with work waiting. Past DECISION_AFTER_S that is still
    one request - and the block row stays, for the bridge. Once the founder
    closes the trust request with the agent still stuck, the block is raised."""
    con = _blocked_world(home)
    wt = home / "worktree"
    wt.mkdir(parents=True, exist_ok=True)
    db.set_terminal(con, "backend", "term_x", str(wt))
    real = (termmod.trust_prompt_showing, termmod.screen_text, trustmod.is_declared)
    termmod.trust_prompt_showing = lambda h: True
    termmod.screen_text = lambda h: (f"Accessing workspace:\n{wt}\nQuick safety check: Is this "
                                     f"a project you created or one you trust?\n")
    trustmod.is_declared = lambda p: False
    termmod.screen_blocker = lambda h: termmod.Blocker(termmod.MODAL, '"trust"')
    t0 = int(time.time()) - 2 * blockedmod.DECISION_AFTER_S - 3000
    try:
        keep_handoff("backend", "# handoff · backend\n## next\n- carry on\n")
        ok, _why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        check("trust once: the wake raises and stalls",
              not ok and len(_decisions(con)) == 1
              and db.get_agent_state(con, "backend")["state"] == dmn_states.STALLED,
              str((ok, _why, [dict(r) for r in _decisions(con)])))
        for at in (0, blockedmod.DECISION_AFTER_S, blockedmod.DECISION_AFTER_S + 900):
            blockedmod._checked.clear()
            blockedmod.sweep(con, lambda _m: None, t0 + at)
        check("trust once: past DECISION_AFTER_S, still one founder request",
              len(_decisions(con)) == 1, str([dict(r) for r in _decisions(con)]))
        rec = db.block_get(con, "backend")
        check("trust once: the block row stays, for the bridge",
              rec is not None and rec["kind"] == blockedmod.STALLED, str(rec and dict(rec)))

        askmod.resolve(_decisions(con)[0]["id"], "looked", state="done", notify=False)
        blockedmod._checked.clear()
        blockedmod.sweep(con, lambda _m: None, t0 + blockedmod.DECISION_AFTER_S + 1000)
        check("trust once: closed while it is still stuck, the block is raised",
              len(_decisions(con)) == 2, str([dict(r) for r in _decisions(con)]))

        # Only a stall is taken as already said: a dialog is a different
        # obstacle from whatever the agent's open decision is about.
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        db.block_clear(con, "backend")
        con.execute("UPDATE requests SET state = 'landed' WHERE kind = 'task' AND target = 'human'")
        askmod.raise_decision(con, "backend", "something else", "unrelated")
        blockedmod._checked.clear()
        blockedmod.sweep(con, lambda _m: None, t0 + 5000)
        blockedmod.sweep(con, lambda _m: None, t0 + 5000 + blockedmod.DECISION_AFTER_S)
        check("trust once: a dialog is raised beside an unrelated open decision",
              len(_decisions(con)) == 4, str([dict(r) for r in _decisions(con)]))
    finally:
        (termmod.trust_prompt_showing, termmod.screen_text, trustmod.is_declared) = real
        con.close()


def test_a_forgotten_message_or_a_task_behind_one_is_still_nudged(home: Path) -> None:
    """T398 (auditor #2427): a message is typed at once and never gets the tick's
    cursor, so the forgotten-resolve check judges it on its own delivery; a task told
    behind a typed ask is judged as the task."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    try:
        orca_seen()
        termmod.typeable_now = lambda h: True
        termmod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        sessions_are("backend")
        agents.register("backend")
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="finished its turn")
        now = int(time.time())
        def idle_for(secs: int) -> None:
            con.execute("DELETE FROM events WHERE agent = 'backend'")
            db.add_event(con, "backend", "Stop", {})
            con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'", (now - secs,))
            con.execute("UPDATE requests SET delivered_at = ?", (now - secs,))

        def nudge(at: int) -> bool:
            return dmn_delivery.nudge_forgotten_resolve(
                con, "backend", db.get_agent_state(con, "backend"), at, lambda _m: None)

        sent.clear()
        task = db.add_request(con, "marina", "backend", "the work", None, "peer", "async", "task", 0, None)
        db.mark_delivered(con, task)
        db.set_cursor(con, "notify:backend", task)
        idle_for(dmn_delivery.NUDGE_AFTER_S + 60)
        found = dmn_delivery.forgotten_resolve(con, "backend", db.get_agent_state(con, "backend"), now)
        check("forgotten task: a task told and left open is what is judged",
              found is not None and found["head"]["id"] == task, str(found))
        check("forgotten task: and it is nudged", nudge(now + dmn_delivery.NUDGE_RETRY_S + 1)
              and any(f"#{task}" in t for t in sent), str(sent))
    finally:
        con.close()


def test_an_idle_agent_delivers_past_a_head_whose_task_is_blocked(home: Path) -> None:
    """T337 (founder #2002/#2003, orcapipe's design on #2024, auditor #2028
    finding 1): the one-at-a-time rule is for what an agent is doing, not for
    what it is stuck waiting on. A head whose own task is recorded blocked is
    left queued exactly where it is, and `effective_head` - what
    `deliver_queued`, `forgotten_resolve` and `blocked.work_waiting` read the
    queue through now, instead of `db.head_of_queue` directly - is the
    request behind it instead. That request IS the head from there on: told
    once, nudged if forgotten, and nothing else delivered while it stays
    open - the same one-at-a-time rule as an ordinary head, not the whole
    queue draining past the block a tick at a time."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    try:
        orca_seen()
        termmod.idle_now = lambda h: True
        termmod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        sessions_are("backend")
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.IDLE)

        ok, msg, tid = actions.task_new("product", "backend", "do the blocked thing", "do the blocked thing")
        check("setup: backend holds the task", ok, msg)
        head = db.task_requests(con, tid)[0]["id"]
        check("setup: task_new already delivered it - backend was idle and typeable",
              sent and "do the blocked thing" in sent[-1], str(sent))
        sent.clear()

        first = db.add_request(con, "reviewer", "backend", "first thing behind it", None,
                               "peer", "async", "task", 0, None)
        second = db.add_request(con, "reviewer", "backend", "second thing behind it", None,
                                "peer", "async", "task", 0, None)

        check("blocked: not blocked yet - nothing has recorded a wait",
              not dmn_delivery.head_task_blocked(con, "backend", db.get_request(con, head)))
        check("blocked: nothing new to deliver while the head is merely open",
              not dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and not sent)

        ok, msg, t_blocker = actions.task_new("product", "reviewer", "unrelated", "unrelated")
        check("setup: a task to block on", ok, msg)
        ok, msg = actions.add_block(con, tid, t_blocker, "product")
        check("setup: the task is now blocked", ok, msg)
        check("blocked: the head's task is blocked on record",
              dmn_delivery.head_task_blocked(con, "backend", db.get_request(con, head)))
        check("blocked: effective_head is the first request behind it, not the true head",
              dmn_delivery.effective_head(con, "backend")["id"] == first)

        db.set_cursor(con, "batch:backend", 0)            # the minute is up
        check("blocked: delivered the ordinary way - its own cursor, not the head's, with the one behind it",
              dmn_delivery.deliver_queued(con, "backend", lambda _m: None)
              and "first thing" in sent[-1] and f"#{first}" in sent[-1]
              and "second thing" in sent[-1], str(sent))
        check("blocked: the notify cursor moved to it, not the blocked head",
              db.get_cursor(con, "notify:backend") == first)
        sent.clear()

        # the second is told once, with the first, and
        # is not typed again on the next tick.
        check("blocked: not told twice",
              not dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and not sent)
        check("blocked: the true head is untouched underneath it all - still "
              "queued, still what head_of_queue finds",
              db.get_request(con, head)["state"] == "queued"
              and db.head_of_queue(con, "backend")["id"] == head)

        # The first, left open past its own nudge interval, is what gets
        # nudged - not the blocked true head, and not the second either.
        now = int(time.time())
        con.execute("UPDATE requests SET delivered_at = ? WHERE id = ?",
                    (now - dmn_delivery.NUDGE_AFTER_S - 60, first))
        con.execute("DELETE FROM events WHERE agent = 'backend'")
        db.add_event(con, "backend", "Stop", {})
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'",
                    (now - dmn_delivery.NUDGE_AFTER_S - 60,))
        check("blocked: the open first is what gets nudged",
              dmn_delivery.nudge_forgotten_resolve(
                  con, "backend", db.get_agent_state(con, "backend"), now, lambda _m: None)
              and f"#{first}" in sent[-1], str(sent))
        sent.clear()

        _set_request_state(con, first, "landed", verdict="did it")
        check("blocked: the second, told with the first, is not told again once the first resolves",
              not dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and not sent, str(sent))

        db.remove_block(con, tid, t_blocker)
        check("blocked: clearing the block is read immediately",
              not dmn_delivery.head_task_blocked(con, "backend", db.get_request(con, head)))
        check("blocked: the head returns to the front on its own - head_of_queue never left it",
              db.head_of_queue(con, "backend")["id"] == head)
        check("blocked: and effective_head agrees, once the block is gone",
              dmn_delivery.effective_head(con, "backend")["id"] == head)
    finally:
        con.close()


def test_a_landed_task_delivery_is_the_task_the_agent_holds(home: Path) -> None:
    """T797 6b: a task delivery whose line reached the agent's transcript is `landed`, not `queued` -
    and the agent still holds that task: it is the inbox head, the one-task-at-a-time gate keeps
    the next task behind it, a forgotten one is nudged, and the task ending frees the next."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    try:
        orca_seen()
        termmod.idle_now = lambda h: True
        termmod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        sessions_are("backend")
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        ok, _, first = actions.task_new("product", "backend", "first held task", "first held task")
        rid1 = db.task_requests(con, first)[0]["id"]
        db.mark_delivered(con, rid1)
        db.mark_landed(con, rid1)
        ok, _, second = actions.task_new("product", "backend", "second waits", "second waits")
        rid2 = db.task_requests(con, second)[0]["id"]
        sent.clear()

        check("landed task: its delivery reads landed, not queued", db.get_request(con, rid1)["state"] == "landed")
        check("landed task: its landing is not an answer - the asker is not told one",
              rid1 not in [r["id"] for r in db.unannounced_answers(con, "product")])
        check("landed task: it is still sent and being worked",
              db.get_task(con, first)["sent_at"] is not None and actions.task_state(con, db.get_task(con, first)) == "doing")
        check("landed task: it is the inbox head, not the task waiting behind it",
              (db.head_of_queue(con, "backend") or {})["id"] == rid1 and dmn_delivery.effective_head(con, "backend")["id"] == rid1,
              str(db.head_of_queue(con, "backend") and db.head_of_queue(con, "backend")["id"]))
        check("landed task: the next is collected, not told inside the minute",
              not dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and not sent, str(sent))

        now = int(time.time())
        con.execute("UPDATE requests SET delivered_at = ? WHERE id = ?", (now - dmn_delivery.NUDGE_AFTER_S - 60, rid1))
        con.execute("DELETE FROM events WHERE agent = 'backend'")
        db.add_event(con, "backend", "Stop", {})
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'", (now - dmn_delivery.NUDGE_AFTER_S - 60,))
        found = dmn_delivery.forgotten_resolve(con, "backend", db.get_agent_state(con, "backend"), now)
        check("landed task: left open and idle, it is the forgotten one", found is not None and found["head"]["id"] == rid1,
              str(found))

        check("landed task: it is what the agent's inbox shows, as the task it is on",
              [m["id"] for m in askmod.inbox("backend")] == [rid1] and db.current_task(con, "backend") == first,
              str([m["id"] for m in askmod.inbox("backend")]))
        stuckmod._WORLD["at"] = 0.0
        check("landed task: it is not on the stuck list - it reached its agent",
              not any(r["what"].startswith(f"#{rid1} ") for r in stuckmod.stuck(con)), str(stuckmod.stuck(con)))
        db.mark_task_done(con, first, "finished")
        db.set_cursor(con, "batch:backend", 0)            # the minute is up
        check("landed task: once it is done the next is told",
              dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and "second waits" in sent[-1], str(sent))
    finally:
        con.close()


def test_a_landed_task_can_be_handed_on_discarded_and_reopened(home: Path) -> None:
    """T797 6b: the ways a held (landed) task delivery ends or moves - its old holder keeps no hold,
    the new one is asked, a discarded one is nobody's, a reopened one goes out again."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        orca_seen()
        termmod.idle_now = lambda h: True
        termmod.send = lambda h, text, enter=True: (True, "")
        agents.register("reviewer")
        for a in ("backend", "reviewer"):
            sessions_are(a)
            db.set_terminal(con, a, f"term_{a}", "wt")
            db.set_agent_state(con, a, dmn_states.IDLE)
        ok, _, t1 = actions.task_new("product", "backend", "to hand on", "to hand on")
        r1 = db.task_requests(con, t1)[0]["id"]
        db.mark_delivered(con, r1)
        db.mark_landed(con, r1)
        ok, msg = actions.task_handoff(con, "backend", t1, "reviewer", "yours now")
        check("handed on: a landed task can be handed to another agent", ok, msg)
        mine = db.get_task(con, t1)
        check("handed on: it is the new owner's and asked of them, queued",
              mine["owner"] == "reviewer" and db.live_delivery(con, t1, "reviewer")["state"] == "queued")
        check("handed on: the old holder holds nothing - nothing left in its line",
              not db.queued_for(con, "backend"), str([r["id"] for r in db.queued_for(con, "backend")]))

        from handline.actions import tasks as taskacts
        said: list = []
        real_say = taskacts._type_say
        taskacts._type_say = lambda _con, owner, body, why: said.append((owner, body))
        try:
            ok, msg = actions.task_handoff(con, "reviewer", t1, "backend", "back to you")
            check("hand back: a task can go back to a previous holder", ok, msg)
            held = [r["id"] for r in db.queued_for(con, "backend")]
            check("hand back: only the new delivery is in the previous holder's line, not its old landed one",
                  held == [db.live_delivery(con, t1, "backend")["id"]] and r1 not in held and db.live_delivery(con, t1, "backend")["id"] != r1,
                  str(held))
            ok, _, t3 = actions.task_new("product", "reviewer", "to release", "to release")
            r3 = db.task_requests(con, t3)[0]["id"]
            db.mark_delivered(con, r3)
            db.mark_landed(con, r3)
            ok, msg = actions.release_task(con, t3, "product", "stuck")
            check("release: a landed task is released, its agent told to stop", ok and said and said[-1][0] == "reviewer" and "stop work" in said[-1][1], f"{msg} {said}")
            check("release: and nothing is left in its agent's line", not db.queued_for(con, "reviewer"),
                  str([r["id"] for r in db.queued_for(con, "reviewer")]))
            ok, _, t4 = actions.task_new("product", "reviewer", "to finish and reopen", "to finish and reopen")
            r4 = db.task_requests(con, t4)[0]["id"]
            db.mark_delivered(con, r4)
            db.mark_landed(con, r4)
            db.mark_task_done(con, t4, "done it")
            ok, msg = actions.reopen_task(con, t4, "product", "not right")
            check("reopen: a done task is reopened", ok, msg)
            check("reopen: its old delivery is not held again", r4 not in [r["id"] for r in db.queued_for(con, "reviewer")],
                  str([r["id"] for r in db.queued_for(con, "reviewer")]))
            ok, _, t5 = actions.task_new("product", "reviewer", "to move", "to move")
            r5 = db.task_requests(con, t5)[0]["id"]
            db.mark_delivered(con, r5)
            db.mark_landed(con, r5)
            ok, msg = actions.edit_task(con, t5, "product", owner="backend")
            actions.send_moved_task(con, t5, "reviewer", "product")
            check("move: edit --owner leaves the old holder nothing in its line and asks the new one",
                  ok and r5 not in [r["id"] for r in db.queued_for(con, "reviewer")]
                  and db.live_delivery(con, t5, "backend")["state"] == "queued", msg)
            ok, msg = actions.edit_task(con, t5, "product", owner="reviewer")
            actions.send_moved_task(con, t5, "backend", "product")
            check("move: and moved back, the first holder's old landed delivery is not held again - the new one is",
                  ok and r5 not in [r["id"] for r in db.queued_for(con, "reviewer")]
                  and db.live_delivery(con, t5, "reviewer")["id"] != r5, msg)
        finally:
            taskacts._type_say = real_say

        ok, _, t2 = actions.task_new("product", "backend", "to discard", "to discard")
        r2 = db.task_requests(con, t2)[0]["id"]
        db.mark_delivered(con, r2)
        db.mark_landed(con, r2)
        ok, msg = actions.discard_task(con, t2, "product", "not needed")
        check("discard: the asker is refused while the task is held - a request is open", not ok, msg)
        ok, msg = actions.discard_task(con, t2, db.HUMAN, "not needed", stop=True)
        check("discard: the founder stops and discards a held task", ok, msg)
        check("discard: a discarded landed task leaves nothing in the line", r2 not in [r["id"] for r in db.queued_for(con, "backend")],
              str([r["id"] for r in db.queued_for(con, "backend")]))
        check("discard: and reads not sent", db.get_task(con, t2)["sent_at"] is None)
    finally:
        con.close()


def test_any_request_that_landed_is_landed(home: Path) -> None:
    """T797 6c: not only task deliveries - a say, or an old row with no kind, that reached its agent is
    landed and told, can no longer be cancelled, and is no open ask; the connect sweep does the same
    to a row left queued with its landing recorded."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        rows = {}
        for kind in ("say", None):
            rid = db.add_request(con, "product", "backend", f"a {kind} request", None, "peer", "async", kind, 0, None)
            db.mark_delivered(con, rid)
            db.mark_landed(con, rid)
            rows[kind] = rid
            r = db.get_request(con, rid)
            check(f"landed any: a {kind} request reads landed and is marked told",
                  r["state"] == "landed" and r["state_at"] is not None, str(dict(r)))
            ok, msg = askmod.cancel(rid)
            check(f"landed any: a landed {kind} request cannot be cancelled", not ok and "already landed" in msg, msg)
            check(f"landed any: and is not an open ask of its requester", rid not in db.open_ask_ids_of(con, "product"))
        swept = db.add_request(con, "product", "backend", "left queued", None, "peer", "async", "say", 0, None)
        con.execute("UPDATE requests SET landed_at = 777 WHERE id = ?", (swept,))
        con.commit()
        con.close()
        con = support.upgrade()
        r = db.get_request(con, swept)
        check("landed any: the connect sweep lands a queued row that has its landing recorded",
              r["state"] == "landed" and r["state_at"] == 777, str(dict(r)))
    finally:
        con.close()


def test_a_head_told_before_a_session_closed_is_told_again_when_it_runs_again(home: Path) -> None:
    """T797 7b (auditor #8852): `clear_closed` voids what the earlier session was told, and a resume keeps
    its id - so the head, delivered before the close, is typed again; and a task told out of turn is
    not told a second time only while that same session runs."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    try:
        orca_seen()
        termmod.idle_now = lambda h: True
        termmod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        sessions_are("backend")
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        ok, _, tid = actions.task_new("product", "backend", "the head", "the head")
        rid = db.task_requests(con, tid)[0]["id"]
        check("resume: the head was typed once when it arrived", len(sent) == 1 and db.get_cursor(con, "notify:backend") == rid, str(sent))
        check("resume: and the tick does not repeat it", not dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and len(sent) == 1, str(sent))
        db.set_closed(con, "backend", "sess-1")
        db.clear_closed(con, "backend")               # the same session id runs again: told afresh
        check("resume: told about its queue afresh after a close and a same-id resume",
              dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and len(sent) == 2 and f"#{rid}" in sent[-1], str(sent))

        # a task told out of turn (a handoff) is not told again when it becomes the head - until a resume
        sent.clear()
        t2 = db.new_task(con, "handed in", "product", "product")
        ok, msg = actions.task_handoff(con, "product", t2, "backend", "second one")
        r2 = db.get_task(con, t2) and db.live_delivery(con, t2, "backend")["id"]
        check("out of turn: the handoff is typed on arrival, past the open head", ok and len(sent) == 1 and "second one" in sent[0], f"{msg} {sent}")
        ok, _, sid = askmod.say("backend", "a word in passing", requester="product")
        dmn_messaging.tell_now(con, sid, lambda _m: None)          # a later out-of-turn tell must not erase the earlier mark
        check("out of turn: a say told after it is typed at once too", len(sent) == 2 and "a word in passing" in sent[1], str(sent))
        con.execute("UPDATE requests SET queue_order = -1 WHERE id = ?", (r2,))     # now first in line: the head
        sent.clear()
        check("out of turn: becoming the head is not news in the same session",
              not dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and not sent, str(sent))
        check("out of turn: marks are per request and pruned once a request leaves the line",
              db.told_ahead(con, "backend", r2) and db.told_ahead(con, "backend", sid), "")
        db.mark_told_ahead(con, "backend", 99999, [99999])
        check("out of turn: ...so one that is no longer in the line is dropped at the next mark",
              not db.told_ahead(con, "backend", r2) and not db.told_ahead(con, "backend", sid)
              and db.told_ahead(con, "backend", 99999), "")
        db.set_cursor(con, "ahead:backend:%d" % r2, r2)
        db.set_closed(con, "backend", "sess-1")
        db.clear_closed(con, "backend")
        check("out of turn: a resume voids it - the head is told again",
              dmn_delivery.deliver_queued(con, "backend", lambda _m: None) and sent and f"#{r2}" in sent[-1], str(sent))
    finally:
        con.close()


def test_head_task_blocked_by_the_agents_own_linked_ask(home: Path) -> None:
    """T337, auditor #2028 finding 2: `head_task_blocked`'s other form - an
    outgoing ask `agent` itself sent, linked to its head's own task - had no
    test. This is the founder's actual case (#1685/T304: 'mockup to the
    founder first'), not the --blocked-by one the other test covers."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        ok, msg, tid = actions.task_new("product", "backend", "needs a mockup first", "needs a mockup first")
        check("setup: backend holds the task", ok, msg)
        head = db.task_requests(con, tid)[0]["id"]
        check("setup: not blocked before anything is asked",
              not dmn_delivery.head_task_blocked(con, "backend", db.get_request(con, head)))

        ok, msg, ask_id = askmod.ask("reviewer", "does this mockup work?", requester="backend")
        check("setup: backend's own ask goes out", ok, msg)
        check("blocked: an unlinked ask of its own does not block the head",
              not dmn_delivery.head_task_blocked(con, "backend", db.get_request(con, head)))

        db.link_request(con, tid, ask_id)
        check("blocked: linked to the head's own task, the open ask blocks it",
              dmn_delivery.head_task_blocked(con, "backend", db.get_request(con, head)))

        _set_request_state(con, ask_id, "landed", verdict="looks right", announced=True)
        check("blocked: answered, it no longer blocks",
              not dmn_delivery.head_task_blocked(con, "backend", db.get_request(con, head)))
    finally:
        con.close()


def test_a_blocked_head_is_not_shown_as_a_pending_reminder(home: Path) -> None:
    """T337, auditor #2028 finding 3: `blocked.work_waiting` reads the queue
    through `effective_head` now too - a head blocked on record, with
    nothing behind it to take its place, is not shown as a pending "reminder
    to resolve" either, since nothing typed at the agent about it ever will
    be (`nudge_forgotten_resolve` skips it the same way)."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        ok, msg, tid = actions.task_new("product", "backend", "do the blocked thing", "do the blocked thing")
        check("setup: backend holds the task", ok, msg)
        head = db.task_requests(con, tid)[0]["id"]
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        now = int(time.time())
        con.execute("UPDATE requests SET delivered_at = ? WHERE id = ?",
                    (now - dmn_delivery.NUDGE_AFTER_S - 60, head))
        db.set_cursor(con, "notify:backend", head)
        db.add_event(con, "backend", "Stop", {})
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'",
                    (now - dmn_delivery.NUDGE_AFTER_S - 60,))
        row = db.get_agent_state(con, "backend")
        check("setup: an ordinary open head would show as a pending reminder",
              f"reminder to resolve #{head}" in blockedmod.work_waiting(con, "backend", row, now))

        ok, msg, t_blocker = actions.task_new("product", "reviewer", "unrelated", "unrelated")
        check("setup: a task to block on", ok, msg)
        ok, msg = actions.add_block(con, tid, t_blocker, "product")
        check("setup: the task is now blocked", ok, msg)
        check("blocked: no longer a pending reminder - nothing behind it to take its place",
              blockedmod.work_waiting(con, "backend", row, now) == "")
    finally:
        con.close()


def test_a_closed_agents_wake_reads_the_blocked_head_through_effective_head(home: Path) -> None:
    """T337 round 4, auditor #2028's sweep: `lifecycle.waiting_for` (gates
    whether a closed agent is woken at all) and `delivery.waiting_prompt`
    (what that wake is told) both read `db.head_of_queue` directly - a
    closed agent with only a blocked head and nothing behind it would be
    woken for nothing (a session spent to be told nothing, the T146
    problem this whole design exists to avoid), and one woken for the
    request behind it would be told about the blocked head instead, on the
    wrong cursor."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        ok, msg, tid = actions.task_new("product", "backend", "the blocked head", "the blocked head")
        check("setup: backend holds the task", ok, msg)
        head = db.task_requests(con, tid)[0]["id"]
        check("unblocked: waiting_for sees the plain head",
              dmn_lifecycle.waiting_for(con, "backend"))

        ok, msg, t_blocker = actions.task_new("product", "reviewer", "unrelated", "unrelated")
        check("setup: a task to block on", ok, msg)
        ok, msg = actions.add_block(con, tid, t_blocker, "product")
        check("setup: the task is now blocked", ok, msg)
        check("blocked, nothing behind: waiting_for is False - nothing to wake it for",
              not dmn_lifecycle.waiting_for(con, "backend"))
        check("blocked, nothing behind: waiting_prompt has nothing to say either",
              dmn_delivery.waiting_prompt(con, "backend", lambda _m: None) is None)

        nxt = db.add_request(con, "reviewer", "backend", "fix it instead", None,
                             "peer", "async", None, 0, None)
        check("blocked, something behind: waiting_for is True again",
              dmn_lifecycle.waiting_for(con, "backend"))
        told = dmn_delivery.waiting_prompt(con, "backend", lambda _m: None)
        check("blocked, something behind: waiting_prompt tells the one behind it",
              told is not None and f"#{nxt} from reviewer" in told[0], told)
        check("blocked, something behind: marked delivered on its own row, the true head untouched",
              db.get_request(con, nxt)["delivered_at"] is not None
              and db.get_request(con, head)["delivered_at"] is None)
    finally:
        con.close()


def test_a_withdraw_landing_before_the_send_stops_the_send(home: Path) -> None:
    """Review of buddyron/one-task-at-a-time, fix #5: deliver_queued reads
    head_of_queue, then waits on idle_now and lock.terminal before _tell marks
    the row and sends it - a withdraw or replace fitting in that gap must not
    still get typed into the target's terminal."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = termmod.send
    try:
        termmod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        rid = db.add_request(con, "reviewer", "backend", "look at this", None,
                             "peer", "async", None, 0, None)
        head = db.head_of_queue(con, "backend")
        # The gap: something retires the row after deliver_queued already read
        # it as the head, but before _tell gets to mark_delivered.
        check("race: the withdraw lands",
              db.retire_if_undelivered(con, rid, "withdrawn"))
        check("race: _tell refuses instead of sending a dead request",
              not dmn_delivery._tell(con, "backend", head, 1, "term_x", lambda _m: None) and not sent,
              str(sent))
        check("race: the row was not marked delivered underneath the withdraw",
              db.get_request(con, rid)["delivered_at"] is None)
    finally:
        termmod.send = real
        con.close()


def test_withdraw_too_late_points_at_a_message_and_a_task_release(home: Path) -> None:
    """T401 item 20: `ocp ask` is the wrong verb to suggest now - a reply
    goes as a message, and a task-kind request also names `hl task release`."""
    _board_world(home)
    con = db.connect()
    try:
        rid = db.add_request(con, "manager", "worker", "just a note", None,
                             "supervisor", "fire_and_forget", None, 0, None)
        db.mark_delivered(con, rid)
        ok, msg = askmod.withdraw(rid, requester="manager")
        check("a plain message: no task release is offered",
              not ok and "hl say worker" in msg and "task release" not in msg, msg)

        tid = db.new_task(con, "do the thing", "worker", "manager")
        trid = db.add_request(con, "manager", "worker", f"T{tid} handed to you by manager: do the thing",
                              None, "supervisor", "fire_and_forget", "task", 0, None)
        db.link_request(con, tid, trid)
        db.mark_delivered(con, trid)
        ok, msg = askmod.withdraw(trid, requester="manager")
        check("a task handoff: named with hl say and hl task release, not ocp ask",
              not ok and "hl say worker" in msg and f"hl task release T{tid}" in msg
              and "ocp ask" not in msg, msg)
    finally:
        con.close()


def test_a_request_is_answered_by_a_reply_that_names_it(home: Path) -> None:
    """T57: any request handed to an agent says to answer it with a reply that names it."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = termmod.send
    try:
        termmod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        rid = db.add_request(con, "reviewer", "backend", "look at this", None,
                             "peer", "async", None, 0, None)
        dmn_delivery._tell(con, "backend", db.head_of_queue(con, "backend"), 1, "term_x", lambda _m: None)
        check("report notice: any other request is answered by a reply that names it",
              f"`hl say reviewer --parent {rid} " in sent[-1] and "ocp resolve" not in sent[-1]
              and "ocp dismiss" not in sent[-1], sent[-1])
    finally:
        termmod.send = real
        con.close()


def test_spawn_confirms_the_task_landed(home: Path) -> None:
    """Orca reports input as accepted even when the TUI never submitted it.

    Three agents once came up with the prompt sitting unsent in the input box
    while the spawn output claimed success, so the only acceptable proof is the
    agent's own UserPromptSubmit.
    """
    os.environ["OCP_HOME"] = str(home)
    con = db.connect()
    try:
        since = db.max_event_id(con)
        check("spawn: no prompt event yet means not landed",
              spawnmod.prompt_landed("scout", since, timeout_s=0) is False)
        db.add_event(con, "scout", "SessionStart", {})
        check("spawn: a session starting is not a task arriving",
              spawnmod.prompt_landed("scout", since, timeout_s=0) is False)
        db.add_event(con, "other", "UserPromptSubmit", {})
        check("spawn: another agent's prompt does not count",
              spawnmod.prompt_landed("scout", since, timeout_s=0) is False)
        db.add_event(con, "scout", "UserPromptSubmit", {})
        check("spawn: the agent's own prompt event confirms it",
              spawnmod.prompt_landed("scout", since, timeout_s=0) is True)
    finally:
        con.close()


def test_a_cold_wake_with_no_handoff_still_tells_the_agent_its_transcript_and_request(home: Path) -> None:
    """The handoff was never taken (the close failed), the snapshot is stale, and
    the cache is gone: the fresh session is still handed the request in hand and
    the transcript's path, found by the session id on the row."""
    _daemon_home(home, 20)
    credentialed(home, "alpha")
    con = db.connect()
    sent: list[str] = []
    seen: list[str] = []
    real = (termmod.alive, termmod.wait_idle, termmod.idle_now, termmod.send,
            termmod.available, termmod.clear_context)
    try:
        orca_seen()
        termmod.wait_idle = lambda h, timeout_ms=0: True
        termmod.idle_now = lambda h: True
        table = sessions_are("backend")
        relaunch = relaunching_send(sent, table)

        def send(handle, text, enter=True):
            if " launch " in text:
                seen.append(launch.context_for("backend"))
            return relaunch(handle, text, enter)
        termmod.send = send
        wt = home / "wt"
        wt.mkdir()
        subprocess.run(["git", "init", "-q", str(wt)], check=True)
        transcript = vendors.config_dir("alpha") / "projects" / "-wt" / "sess-7.jsonl"
        transcript.parent.mkdir(parents=True)
        transcript.write_text("{}\n")
        db.set_terminal(con, "backend", "term_x", str(wt))
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        rid = db.add_request(con, "reviewer", "backend", "review the picker branch", None,
                             "peer", "async", None, 0, None)
        db.mark_delivered(con, rid)

        def wake(idle_s: int | None) -> list[str]:
            con.execute("DELETE FROM events WHERE agent = 'backend'")
            if idle_s is not None:
                db.add_event(con, "backend", "Stop", {})
                con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'",
                            (int(time.time()) - idle_s,))
            db.set_closed(con, "backend", "sess-7")
            support.something_waiting(con, "backend")
            db.set_handoff(con, "backend", None)
            table.clear()
            sent.clear()
            seen.clear()
            ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
            check(f"cache: the wake after {idle_s}s with no handoff goes through", ok, why)
            table["backend"] = 4242
            return list(sent)

        for idle in (dmn_timing.RESUME_WARM_S + 60, None):
            label = "past the window" if idle else "with no clock"
            s1 = wake(idle)
            check(f"cache: {label}, no handoff, it is not resumed",
                  s1 and not any("--resume" in t for t in s1), str(s1))
            check(f"cache: {label}, the new session's system prompt has the request and transcript",
                  seen and f"request: #{rid}" in seen[0] and str(transcript) in seen[0]
                  and "sess-7" in seen[0], str(seen))
            check(f"cache: {label}, the request is still its own",
                  (db.head_of_queue(con, "backend") or {"id": 0})["id"] == rid)
        # Control: inside the window, no handoff needed, resumed as before.
        s2 = wake(dmn_timing.RESUME_WARM_S - 120)
        check("cache: inside the window it resumes and takes no handoff",
              any("--resume sess-7" in t for t in s2) and not seen[0].count("Where you left off"),
              str(s2))
    finally:
        (termmod.alive, termmod.wait_idle, termmod.idle_now, termmod.send,
         termmod.available, termmod.clear_context) = real
        con.close()


def test_an_answer_reaches_the_agent_that_asked(home: Path) -> None:
    """Answering wrote the verdict on the row and stopped. The agent that asked
    - which may have parked waiting for it - was never told, so a founder
    answering a decision on the screen and the agent never hearing it is the
    same hole seen from the other end. An answer is delivered the way work is,
    once, through a cursor of its own."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (termmod.available, termmod.alive, termmod.idle_now, termmod.send)
    try:
        orca_seen()
        termmod.idle_now = lambda h: True
        termmod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        sessions_are("backend")
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.IDLE)

        rid = db.add_request(con, "backend", "reviewer", "is this ready?", None,
                             "peer", "async", None, 0, None)
        check("answered: nothing is announced while it is still open",
              not dmn_delivery.deliver_answers(con, "backend", lambda _m: None) and not sent)

        # Resolving is reporting: the answer goes to whoever asked as part of
        # the same command, and the tick is the safety net rather than the only
        # path. So by the time resolve returns there is nothing left to deliver.
        askmod.resolve(rid, "ship it, with the one nit fixed")
        check("answered: once it comes back, the agent is told",
              sent and not dmn_delivery.deliver_answers(con, "backend", lambda _m: None),
              str(sent))
        check("answered: with who answered and what they said",
              "reviewer" in sent[-1] and "ship it" in sent[-1], sent[-1][:120])
        # Only when there is more than it was shown. A two-word verdict with an
        # instruction to go and read it whole is ceremony typed at an agent
        # that was in the middle of something.
        check("answered: a short answer is said and nothing else",
              "hl read" not in sent[-1], sent[-1][:160])
        askmod.resolve(db.add_request(con, "backend", "reviewer", "and this?", None,
                                      "peer", "async", None, 0, None),
                       "a verdict long enough to be cut: " + "x" * 200)
        dmn_delivery.deliver_answers(con, "backend", lambda _m: None)
        check("answered: one that was cut says where the rest is",
              "hl read" in sent[-1], sent[-1][-80:])

        sent.clear()
        check("answered: and it is not told twice",
              not dmn_delivery.deliver_answers(con, "backend", lambda _m: None) and not sent)

        # Answering is not monotonic in id: two asks, the later one answered
        # first. A high-water cursor put the earlier answer behind it forever,
        # which is the hole this path exists to close, one layer down.
        early = db.add_request(con, "backend", "reviewer", "the one it parked on",
                               None, "peer", "async", None, 0, None)
        late = db.add_request(con, "backend", "keeper", "a second question",
                              None, "peer", "async", None, 0, None)
        askmod.resolve(late, "keeper answers first")
        dmn_delivery.deliver_answers(con, "backend", lambda _m: None)
        sent.clear()
        askmod.resolve(early, "and the one it was waiting for")
        check("answered: an answer that comes back late is still delivered",
              sent and "waiting for" in sent[-1], str(sent[-1:])[:120])

        # A refusal is the answer that means do something else; a routing denial
        # was refused before anything was sent and the agent already read it.
        rejected = db.add_request(con, "backend", "reviewer", "may I?", None,
                                  "peer", "async", None, 0, None)
        sent.clear()
        askmod.resolve(rejected, "no, not this week", state="rejected")
        check("answered: a refusal is delivered", sent and "not this week" in sent[-1],
              str(sent[-1:])[:120])
        denied = db.add_request(con, "backend", "nobody", "unroutable", None,
                                "peer", "async", None, 0, None)
        _set_request_state(con, denied, "denied", "unknown target")
        dmn_delivery.deliver_answers(con, "backend", lambda _m: None)
        check("answered: an ask routing refused is not typed back at it",
              "unroutable" not in sent[-1] and "unknown target" not in sent[-1],
              sent[-1][:160])

        # Everything marked told has to have been said: the ones not shown in
        # full are named, not counted. They pile up while the agent cannot be
        # reached - resolving delivers its own answer when it can - so this is
        # the tick catching up after a session was away.
        sessions_are()
        for i in range(5):
            rid_i = db.add_request(con, "backend", "reviewer", f"q{i}", None,
                                   "peer", "async", None, 0, None)
            askmod.resolve(rid_i, f"a{i}")
        sessions_are("backend")
        sent.clear()
        dmn_delivery.deliver_answers(con, "backend", lambda _m: None)
        unshown = [r["id"] for r in db.unannounced_answers(con, "backend")]
        check("answered: nothing is marked told that was not named",
              not unshown and "also answered: #" in sent[-1], sent[-1][:200])

        # The same gates as any other send: nothing is typed at a shell.
        rid2 = db.add_request(con, "backend", "reviewer", "and this?", None,
                              "peer", "async", None, 0, None)
        askmod.resolve(rid2, "no")
        sent.clear()
        sessions_are()
        check("answered: an answer is not typed at a terminal with no session",
              not dmn_delivery.deliver_answers(con, "backend", lambda _m: None) and not sent)
    finally:
        (termmod.available, termmod.alive, termmod.idle_now, termmod.send) = real
        con.close()


@_reading
def test_t80_a_reply_says_which_request_it_answers(home: Path) -> None:
    """T80: the delivery notice and `ocp read` name the parent of a request
    that has one - first line only, cut at ASKED_SHOWN - and say nothing extra
    for one without."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = termmod.send
    try:
        termmod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        parent = db.add_request(con, "backend", "human", "which port?\nsecond line", None,
                                "peer", "async", None, 0, None)
        reply = db.add_request(con, "human", "backend", "8080", None,
                               "peer", "async", None, 0, parent)
        dmn_delivery._tell(con, "backend", db.head_of_queue(con, "backend"), 1, "term_x", lambda _m: None)
        check("reply notice: typed as 'from human (in reply to #M: first line)'",
              f"#{reply} from human (in reply to #{parent}: which port?), waiting for you" in sent[-1],
              sent[-1])
        out = _capture(lambda: climod.main(["read", str(reply)]))
        check("hl read: shows what it is in reply to, as its own line",
              f"\nin reply to #{parent}: which port?\n" in out, out)

        con.execute("UPDATE requests SET state = 'landed' WHERE id = ?", (reply,))
        plain = db.add_request(con, "human", "backend", "hello", None,
                               "peer", "async", None, 0, None)
        dmn_delivery._tell(con, "backend", db.head_of_queue(con, "backend"), 1, "term_x", lambda _m: None)
        check("reply notice: nothing extra for a request with no parent",
              f"#{plain} from human, waiting for you" in sent[-1] and "in reply to" not in sent[-1],
              sent[-1])

        long_parent = db.add_request(con, "backend", "human", "q" * 100, None,
                                     "peer", "async", None, 0, None)
        cut = dmn_delivery.in_reply_to(con, {"parent_id": long_parent})
        verdict = db.add_request(con, "human", "backend", "v" * 900, None,
                                 "peer", "async", None, 0, parent)
        dmn_delivery._tell(con, "backend", db.get_request(con, verdict), 1, "term_x", lambda _m: None)
        check("a reply is typed whole, a verdict is not cut",
              "v" * 900 in sent[-1] and "…" not in sent[-1], sent[-1][:200])
        con.execute("UPDATE requests SET state = 'landed' WHERE id = ?", (verdict,))
        check("reply notice: a long first line is cut at the limit with an ellipsis",
              cut == f"in reply to #{long_parent}: {'q' * dmn_delivery.ASKED_SHOWN}…", cut)
    finally:
        termmod.send = real
        con.close()


def test_an_answer_line_says_what_it_answers(home: Path) -> None:
    """`#85 from human: done` cannot be placed by an agent that has asked
    several things; the start of its own question rides along."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        def answered(body: str, verdict: str = "done"):
            rid = db.add_request(con, "backend", "reviewer", body, None,
                                 "peer", "async", None, 0, None)
            askmod.resolve(rid, verdict)
            return [r for r in db.unannounced_answers(con, "backend") if r["id"] == rid][0]

        line, more = dmn_delivery.answer_line(answered("T18 on main, supervisor restarted"))
        rid = int(line.split()[0][1:])
        check("answer line: a short question is quoted whole, then who answered and what",
              line == f"#{rid} (you asked: T18 on main, supervisor restarted) reviewer: done", line)
        check("answer line: and nothing more to read", more is False)

        line, _ = dmn_delivery.answer_line(answered("q" * 100))
        check("answer line: a long question is cut at the limit with an ellipsis",
              f"(you asked: {'q' * dmn_delivery.ASKED_SHOWN}…)" in line and "q" * (dmn_delivery.ASKED_SHOWN + 1) not in line,
              line)

        line, _ = dmn_delivery.answer_line(answered("first line\n\nsecond\tline \x1b[31mred"))
        check("answer line: a multi-line question is one line, controls blanked",
              "\n" not in line and "\x1b" not in line and "first line" in line, repr(line))

        line, _ = dmn_delivery.answer_line(answered("", "ok"))
        check("answer line: no question, no empty parenthesis",
              "you asked" not in line and line.endswith("reviewer: ok"), line)

        line, more = dmn_delivery.answer_line(answered("why?", "v" * 200))
        check("answer line: a long verdict is still cut and flagged as having more",
              more and line.endswith("reviewer: " + "v" * 160) and "(you asked: why?)" in line, line)
    finally:
        con.close()


@_reading
def test_a_request_can_be_read_whole(home: Path) -> None:
    """`hl inbox` shows a line each, cut to fit, and the rest was only
    reachable by knowing where the raw JSON lives - which an agent found out by
    trial. An agent that cannot read the whole of what it was asked answers the
    half it could see."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        body = "line one of the order\n" + ("x" * 200) + "\nlast line of the order"
        rid = db.add_request(con, actions.HUMAN, "backend", body, None,
                             "peer", "async", None, 0, None)
        con.close()
        out = _capture(lambda: climod.main(["read", str(rid)]))
        check("read: the whole body is printed, not a cut line",
              "last line of the order" in out and "x" * 200 in out, out[:120])
        check("read: with who asked whom, and in what state",
              f"{actions.HUMAN} → backend" in out and "queued" in out, out[:120])

        askmod.resolve(rid, "done, and here is why: " + "y" * 120)
        out = _capture(lambda: climod.main(["read", str(rid)]))
        check("read: and the answer comes with it", "y" * 120 in out, out[-80:])
        check("read: an id nobody has says so, without pretending",
              "no request" in _capture(lambda: climod.main(["read", "99999"])))
    finally:
        pass


@_reading
def test_an_agent_may_not_list_another_agents_messages(home: Path) -> None:
    """T39: inbox and events naming another agent are refused
    to an agent, unless it is that agent's supervisor; the human is exempt."""
    _daemon_home(home, 20)
    hand_edit_team("""
teams:
  dev: [marina, dohyun]
agents:
  manager: {}
  marina:  {}
  dohyun:  {}
edges:
  - { from: "manager", to: "dev.*", relation: supervisor, response: fire_and_forget }
  - { from: "*", to: "human", kind: task }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
""")
    for n in ("manager", "marina", "dohyun"):
        agents.register(n)
    con = db.connect()
    try:
        db.add_request(con, "manager", "marina", "secret of marina", None,
                       "peer", "async", None, 0, None)
    finally:
        con.close()
    for cmd in (["inbox", "marina"], ["inbox", "marina", "--all"],
                ["events", "--agent", "marina"]):
        rc, out = _cli(cmd, as_agent="dohyun")
        check(f"T39: a peer is refused `ocp {' '.join(cmd)}`",
              rc == 1 and "may not list marina's messages" in out
              and "secret of marina" not in out, out)
    con = db.connect()
    try:
        for who in ("marina", "dohyun", "manager"):
            con.execute("INSERT INTO events (ts, agent, kind, payload) VALUES (?, ?, 'decision', ?)",
                        (int(time.time()), who, f"note of {who}"))
        con.commit()
    finally:
        con.close()
    rc, out = _cli(["events"], as_agent="dohyun")
    check("T39: plain `ocp events` as a peer shows only its own rows",
          rc == 0 and "note of dohyun" in out and "note of marina" not in out
          and "note of manager" not in out, out)
    rc, out = _cli(["events"], as_agent="manager")
    check("T39: plain `ocp events` as a supervisor adds its reports",
          "note of marina" in out and "note of dohyun" in out and "note of manager" in out, out)
    rc, out = _cli(["events"], as_agent=askmod.HUMAN)
    check("T39: plain `ocp events` as the human shows everyone",
          "note of marina" in out and "note of dohyun" in out, out)
    with support.daemon_says("inbox.queue", support.queue_head):
        rc, out = _cli(["inbox", "marina"], as_agent="manager")
        check("listing: a supervisor lists its report's queue", rc == 0 and "secret of marina" in out, out)
        rc, out = _cli(["inbox", "marina"], as_agent=askmod.HUMAN)
        check("listing: the human lists any agent's queue", rc == 0 and "secret of marina" in out, out)
        rc, out = _cli(["inbox", "marina"], as_agent="marina")
        check("listing: an agent lists its own inbox", rc == 0 and "secret of marina" in out, out)
    rc, out = _cli(["inbox", "manager"], as_agent="marina")
    check("T39: a report may not list its supervisor's queue",
          rc == 1 and "may not list manager's messages" in out, out)
    for cmd in (["inbox", "marina"], ["events"]):
        rc, out = _cli(cmd, as_agent=None)
        check(f"T39: a caller with no name is refused `ocp {' '.join(cmd)}`",
              rc == 1 and "cannot tell who is asking" in out and "secret of marina" not in out, out)
    con = db.connect()
    try:
        first = con.execute("SELECT id FROM requests ORDER BY id LIMIT 1").fetchone()[0]
    finally:
        con.close()
    rc, out = _cli(["read", str(first)], as_agent="dohyun")
    check("T39: `ocp read <id>` by number stays allowed", rc == 0 and "secret of marina" in out, out)


def test_inbox_and_withdraw_cli(home: Path) -> None:
    """`ocp inbox` shows the head and a count, not the whole queue (request
    #189); `ocp withdraw` takes back an undelivered request of your own."""
    _daemon_home(home, 20)
    agents.register("reviewer")
    con = db.connect()
    try:
        rid1 = db.add_request(con, "backend", "reviewer", "task one", None,
                              "peer", "async", None, 0, None)
        rid2 = db.add_request(con, "backend", "reviewer", "task two", None,
                              "peer", "async", None, 0, None)
    finally:
        con.close()

    real_agent = os.environ.get("OCP_AGENT")
    try:
        os.environ["OCP_AGENT"] = "reviewer"
        with support.daemon_says("inbox.queue", support.queue_head):
            out = _capture(lambda: climod.main(["inbox"]))
        check("inbox: shows the head's body", "task one" in out, out)
        check("inbox: not the one behind it", "task two" not in out, out)

        # The withdrawal itself is the Go daemon's (request.withdraw); the command sends the id and says what came back.
        os.environ["OCP_AGENT"] = "backend"
        asked = []
        took = lambda p: asked.append(p) or {"ok": True, "said": f"request #{p['id']} to reviewer withdrawn"}  # noqa: E731
        with support.daemon_says("request.withdraw", took):
            rc, out = _cli(["withdraw", str(rid2), "--reason", "asked twice"], as_agent="backend")
        check("withdraw: the daemon is asked with the id and the reason, under a request_id",
              len(asked) == 1 and asked[0]["id"] == rid2 and asked[0]["reason"] == "asked twice" and asked[0]["request_id"], str(asked))
        check("withdraw: what the daemon said is printed", rc == 0 and out.strip() == f"request #{rid2} to reviewer withdrawn", out)
        with support.daemon_says("request.withdraw", lambda p: asked.append(p) or {"ok": False, "said": "too late to withdraw"}):
            rc, out = _cli(["withdraw", str(rid1)], as_agent="backend")
        check("withdraw: a refusal is printed and fails, and no reason is sent when none was given",
              rc == 1 and "too late to withdraw" in out and "reason" not in asked[-1], f"{rc} {out} {asked[-1]}")
    finally:
        if real_agent is None:
            os.environ.pop("OCP_AGENT", None)
        else:
            os.environ["OCP_AGENT"] = real_agent


def test_only_one_thing_types_at_an_agent_at_a_time(home: Path) -> None:
    """A wake holds an agent's terminal for as long as a session takes to
    appear - up to fifteen seconds - and the daemon ticks every two. Seven
    ticks fit in that window, so the daemon and a human pressing `w` both typed
    `hl launch` into the same terminal: the second lands beside the session
    the first started, or inside its input box, unsent.

    Everything else in this system is settled by writing to the database. The
    terminal is the one place two writers can spoil, so the paths that type
    take a lock, and the one that cannot have it does nothing rather than
    waiting - a tick must not queue up behind a human mid-confirmation."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (termmod.available, termmod.alive, termmod.wait_idle, termmod.send)
    try:
        orca_seen()
        termmod.wait_idle = lambda h, timeout_ms=0: True
        termmod.idle_now = lambda h: True
        table = sessions_are()          # closed below - no session running yet
        termmod.send = relaunching_send(sent, table)
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_closed(con, "backend", "sess-1")
        support.something_waiting(con, "backend")

        # Another process is mid-wake on this agent.
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import sys, time; sys.path.insert(0, %r);"
             "from handline.infra import lock;"
             "ctx = lock.terminal('backend'); print(ctx.__enter__(), flush=True);"
             "time.sleep(30)" % str(ROOT)],
            stdout=subprocess.PIPE, text=True, env=dict(os.environ))
        try:
            check("lock: the other process has it", holder.stdout.readline().strip() == "True")
            ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
            check("lock: a second wake types nothing at all",
                  not ok and not sent and "someone else" in why, f"{why} {sent}")
            row = db.get_agent_state(con, "backend")
            check("lock: and it stays closed, so the next attempt still has its way back",
                  row["closed_at"] is not None and row["resume_session"] == "sess-1",
                  str(dict(row)))
        finally:
            holder.kill()
            holder.wait()

        # The kernel releases it when the holder dies, so a daemon killed
        # mid-wake does not leave an agent unreachable.
        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        check("lock: once the holder is gone the wake goes through",
              ok and any("--resume sess-1" in t for t in sent), f"{why} {sent}")
    finally:
        (termmod.available, termmod.alive, termmod.wait_idle, termmod.send) = real
        con.close()


def test_growing_the_team_needs_a_person(home: Path) -> None:
    """A team that cannot grow has one way to take on more work: each agent
    quietly making helpers that die with the session that made them. So growing
    is a first-class operation - and the one with the least business being
    automatic, because an agent that can hire can spend an account into the
    ground with nobody having decided to.

    Any agent proposes, only the founder approves, and the change is made from
    the file the proposal wrote. The approval is read, never interpreted: a
    model taking "maybe next week" for a yes is the failure this prevents."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        accounts.register("beta")
        agents.register("manager")
        hand_edit_team("""
teams:
  dev: [marina]
agents:
  manager: {}
  marina:  {}
  hr:      {}
edges:
  - { from: "manager", to: "dev.*", relation: supervisor, response: fire_and_forget }
  - { from: "*", to: "hr", relation: peer, response: async }
  - { from: "*", to: "human", kind: task }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
""")
        agents.register("marina")
        agents.register("hr")

        # Nothing happens on the proposal itself.
        ok, said = hr.propose("add", "dohyun", requester="hr", why="two branches are "
                              "waiting on one developer", team_name="dev",
                              repo=str(home / "harbor"))
        pid = said.split()[1]
        # A why the record cannot hold is refused before anything is raised.
        before = len(actions.messages(con))
        ok2, said2 = hr.propose("add", "twoline", requester="hr", why="one\ntwo", team_name="dev", repo=str(home / "harbor"))
        check("hr: a why with a line break is refused, and nothing reaches the founder",
              not ok2 and "nothing was raised" in said2
              and len(actions.messages(con)) == before
              and not any("twoline" in (m["body"] or "") for m in actions.messages(con)), said2)
        check("hr: proposing raises it to the founder and changes nothing",
              ok and "dohyun" not in paths.list_agents(), said)
        card = hr.card(hr.load_proposal(pid))
        check("hr: and it is in front of the founder, with the words to decide it with",
              f"hl hr approve {pid}" in card["text"], said)

        # Applied: the two writes a human would have made.
        ok, said = apply_hr(pid)
        table = yamlcompat.load((home / "team.yaml").read_text())
        check("hr: an approved proposal registers the agent", ok and "dohyun" in paths.list_agents(), said)
        check("hr: and puts it in the team, which is what the routing is keyed on",
              "dohyun" in (table["teams"]["dev"] or []) and "dohyun" in table["agents"],
              str(table["teams"]))
        check("hr: the manager may now ask it, because the wildcard covers it",
              team.Team(table).route("manager", "dohyun").allowed)

        # Retiring archives rather than deletes: the journal is the record of
        # work that was actually done.
        ok, said = hr.propose("retire", "dohyun", requester="hr", why="branch landed")
        pid2 = said.split()[1]
        ok, said = apply_hr(pid2)
        table = yamlcompat.load((home / "team.yaml").read_text())
        archived = list((home / "agents" / "archive").glob("*dohyun*"))
        check("hr: retiring takes it out of the table", ok and "dohyun" not in table["agents"], said)
        check("hr: and keeps its folder", archived and (archived[0] / "journal").exists(),
              str(archived))
        check("hr: and takes its scheduling row with it - nothing lists an agent "
              "that is not there",
              db.get_agent_state(con, "dohyun") is None,
              str(db.get_agent_state(con, "dohyun")))

        # A rename takes the whole identity with it - folder, table, and every
        # row that ever named it - and not while a session is running under it.
        db.add_request(con, "marina", "manager", "a report from before", None,
                       "subordinate", "fire_and_forget", None, 0, None)
        sessions_are("manager")
        ok, said = hr.propose("rename", "manager", requester="hr", to="harbor-manager",
                              why="it only ever runs the harbor project", team_name=None)
        pid_r = said.split()[1]
        ok, said = apply_hr(pid_r)
        check("hr: an identity does not change under a running session",
              not ok and "session running" in said, said)
        sessions_are()
        ok, said = apply_hr(pid_r)
        table = yamlcompat.load((home / "team.yaml").read_text())
        check("hr: with the session closed, the rename lands",
              ok and "harbor-manager" in paths.list_agents()
              and "manager" not in table["agents"], said)
        check("hr: and the edges name it too - the renamed manager still "
              "supervises its team",
              team.Team(table).route("harbor-manager", "marina").allowed
              and "\"manager\"" not in (home / "team.yaml").read_text(),
              (home / "team.yaml").read_text())
        check("hr: and its folder came with it",
              (home / "agents" / "harbor-manager").is_dir()
              and not (home / "agents" / "manager").exists())
        check("hr: as did every row that named it",
              con.execute("SELECT COUNT(*) c FROM requests WHERE target = ?",
                          ("harbor-manager",)).fetchone()["c"] == 1
              and con.execute("SELECT COUNT(*) c FROM requests WHERE target = ?",
                              ("manager",)).fetchone()["c"] == 0)

        # An edge the text edit cannot rewrite is refused, not guessed at: the
        # table is what routing runs on, and a rename that dropped an edge
        # would leave the renamed supervisor supervising nobody.
        hand_edit_team("""
teams:
  dev: [marina]
agents:
  harbor-manager: {}
  marina:         {}
edges:
  - from: harbor-manager
    to: marina
    relation: supervisor
    response: fire_and_forget
  - { from: "*", to: "human", kind: task }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
""")
        ok, said = hr.enact("rename", "harbor-manager", to="boss",
                            why="an edge this cannot rewrite")
        table = yamlcompat.load((home / "team.yaml").read_text())
        check("hr: a rename that would drop an edge is refused, and writes nothing",
              not ok and "by hand" in said and "harbor-manager" in table["agents"],
              said)
        check("hr: and the agent is left where it was",
              "harbor-manager" in paths.list_agents() and "boss" not in paths.list_agents())

    finally:
        con.close()


def test_the_founder_does_not_approve_their_own_request(home: Path) -> None:
    """HR gates *agents*, and the founder is the thing on the other side of the
    gate. Asked to propose, they got "an agent cannot ask itself" - the one
    person allowed to change the roster could not use the tool that changes it,
    and the first agent could never be made, since a proposal needs an agent to
    raise it. Who is routed where is the whole risk: a shell that merely lost
    OCP_AGENT must not be taken for the founder and hire at will."""
    _daemon_home(home, 20)
    con = db.connect()

    class FakeStdin:
        def __init__(self, tty: bool) -> None:
            self.tty = tty

        def isatty(self) -> bool:
            return self.tty

    real_env, real_stdin = os.environ.pop("OCP_AGENT", None), sys.stdin
    try:
        accounts.register("beta")
        hand_edit_team("""
teams:
  dev: [marina]
agents:
  marina:  {}
edges:
  - { from: "*", to: "human", kind: task }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
""")
        agents.register("marina")
        hire = ["hr", "add", "dohyun", "--team", "dev", "--repo", str(home / "harbor"),
                "--model", "opus", "--effort", "high",
                "--why", "a second pair of hands on the sea work"]

        # A process with no identity and no terminal: an agent's tool shell,
        # a hook, a cron job. It is nobody, and nobody hires.
        sys.stdin = FakeStdin(False)
        rc = climod.main(hire)
        check("hr: a shell that lost its identity is refused, not taken for the founder",
              rc != 0 and "dohyun" not in paths.list_agents()
              and not proposals.listing(), str(rc))

        # An agent proposes; nothing changes until the founder answers.
        os.environ["OCP_AGENT"] = "marina"
        rc = climod.main(hire)
        check("hr: an agent's request is a proposal, and changes nothing",
              rc == 0 and "dohyun" not in paths.list_agents()
              and [p_["agent"] for p_ in proposals.listing() if p_["state"] == "proposed"] == ["dohyun"], str(rc))
        del os.environ["OCP_AGENT"]
        for p_ in proposals.listing():
            hr.path_of(p_["id"]).unlink()

        # The founder, at their own prompt.
        sys.stdin = FakeStdin(True)
        before = len(actions.messages(con))
        rc = climod.main(hire)
        check("hr: the founder's own change lands without a proposal to approve",
              rc == 0 and "dohyun" in paths.list_agents(), str(rc))
        check("hr: and raises nothing to themselves",
              len(actions.messages(con)) == before)
        cfg = yamlcompat.load(agents.agent_file("dohyun").read_text())
        check("hr: with the model and the effort it was hired at",
              cfg.get("model") == "opus" and cfg.get("effort") == "high", str(cfg))
        applied = [p_ for p_ in proposals.listing() if p_["agent"] == "dohyun"]
        check("hr: and the record is still a file",
              len(applied) == 1 and applied[0]["state"] == "applied"
              and applied[0]["requested_by"] == "human", str(applied))

        # A change that fails halfway is recorded as having failed, and leaves
        # no agent the routing table does not know.
        hand_edit_team("teams:\n  dev: [marina]\n")
        ok, said = hr.enact("add", "halfborn", repo="/repo", why="w", team_name="dev")
        rec = [p_ for p_ in proposals.listing() if p_["agent"] == "halfborn"]
        check("hr: a refused edit registers nobody",
              not ok and "halfborn" not in paths.list_agents(), said)
        check("hr: and says so in the record",
              len(rec) == 1 and rec[0]["state"] == "failed", str(rec))

        check("hr: and the founder is refused the same nonsense an agent is",
              not hr.enact("add", "x", why="w")[0]
              and not hr.enact("retire", "nobody", why="w")[0]
              and not hr.enact("add", "y", why="")[0]
              and not hr.enact("add", "z", why="w", effort="ultra")[0])
    finally:
        sys.stdin = real_stdin
        os.environ.pop("OCP_AGENT", None)
        if real_env is not None:
            os.environ["OCP_AGENT"] = real_env
        con.close()


def test_classify_reads_the_screen_again_after_waiting_of_hides_a_wait_by_it(home: Path) -> None:
    """waiting.of()'s own screen check, just above classify()'s, answers a
    different question - is the founder's own prompt still up - and hiding a
    stale one there does not excuse classify()'s read: a dialog can be on
    screen the instant after, and nothing else would catch it this tick."""
    _daemon_home(home, 20)
    termmod.available = lambda: True
    termmod.alive = lambda h: True
    reads: list[str] = []
    blocker = termmod.Blocker(termmod.MODAL, "a different dialog")

    def fake_screen_blocker(h):
        reads.append(h)
        return None if len(reads) == 1 else blocker

    termmod.screen_blocker = fake_screen_blocker
    con = db.connect()
    try:
        db.set_agent_state(con, "backend", dmn_states.RUNNING, detail="active")
        waiting.note("backend", "Bash", {"command": "rm -rf /x"})
        row = db.get_agent_state(con, "backend")
        found = blockedmod.classify(con, "backend", row)
        check("classify: the permission wait hidden, but a fresh dialog still shows",
              found == (termmod.MODAL, "a different dialog", ""), str(found))
        check("classify: two live reads - waiting.of()'s own and classify()'s own",
              reads == ["term_x", "term_x"], str(reads))
    finally:
        con.close()


def test_a_stale_cached_read_does_not_hide_a_later_prompt(home: Path) -> None:
    """auditor #1721 BLOCKING: screen_blocker_cached's own TTL means a read
    taken while an earlier prompt was up, or just after it was answered, can
    still be "fresh enough" the instant the very next prompt is raised - the
    two can land in the same few-second window when an agent runs several
    commands back to back, which is the ordinary shape of a batch of tool
    calls, not a corner case. Answering the new prompt from a cache entry
    timestamped before it even existed would hide a wait that was never
    actually shown false - reproduced directly: a cache entry timestamped
    well before this wait's own `since`, with the screen itself (once read
    fresh) showing the dialog the whole time."""
    _daemon_home(home, 20)
    os.environ["OCP_AGENT"], os.environ["OCP_ACCOUNT"] = "backend", "alpha"
    con = db.connect()
    try:
        db.set_agent_state(con, "backend", dmn_states.RUNNING, detail="active")

        def waiting_now() -> bool:
            return waiting.of(con, db.get_agent_state(con, "backend")) is not None

        # Within SCREEN_CACHE_TTL_S of "now" - the old code would trust it -
        # but strictly older than the wait `note()` is about to write below,
        # by more than the whole-second `since` this compares against ever
        # ties on: an earlier prompt's own "no dialog" answer, cached.
        termmod._screen_cache["term_x"] = (time.time() - 3, None)
        termmod.screen_blocker = lambda handle: termmod.Blocker(termmod.MODAL, "genuinely up")

        waiting.note("backend", "Bash", {"command": "rm -rf /y"})
        check("stale: a genuinely later prompt is not answered by an older cached absence",
              waiting_now())
    finally:
        os.environ.pop("OCP_AGENT", None); os.environ.pop("OCP_ACCOUNT", None)
        con.close()


def test_a_proposal_and_an_decisionion_wait_on_the_founder_too(home: Path) -> None:
    """An hr proposal shows from the moment it is raised until it is applied or
    refused; a decision shows until it is answered."""
    _daemon_home(home, 20)
    accounts.register("beta")
    con = db.connect()
    try:
        def waits(kind):
            return [w for w in actions.founder_waits(con) if w["kind"] == kind]

        ok, said = hr.propose("retire", "backend", requester="backend", why="done with it")
        pid = said.split()[1]
        w = waits("proposal")
        check("hr wait: a raised proposal is waiting on the founder, in red",
              ok and len(w) == 1 and w[0]["red"] and "awaiting your decision" in w[0]["what"]
              and "backend" in w[0]["what"], str(w))
        check("hr wait: it is not also drawn as a bare decision", not waits("decision"))

        decisions.record("hr", pid, 0, "approved", hr.subject_of(hr.load_proposal(pid)))
        hr.save({**hr.load_proposal(pid), "state": "approved"})       # approved, apply not yet run
        w = waits("proposal")
        check("hr wait: approved but not applied it stays, no longer red",
              len(w) == 1 and not w[0]["red"] and "not yet applied" in w[0]["what"], str(w))
        hr.save({**hr.load_proposal(pid), "state": "applied"})
        check("hr wait: applied, it is gone", not waits("proposal"))

        agents.register("marina")
        ok, said = hr.propose("retire", "marina", requester="marina", why="w")
        hr.save({**hr.load_proposal(said.split()[1]), "state": "rejected"})
        check("hr wait: refused, it is gone", not waits("proposal"), str(waits("proposal")))

        askmod.ask(actions.HUMAN, "which branch do we cut?", requester="marina")
        w = waits("decision")
        check("decision: an unanswered one is waiting on the founder, in red",
              len(w) == 1 and w[0]["red"] and w[0]["who"] == "marina"
              and "which branch" in w[0]["what"], str(w))
        for r in con.execute("SELECT id FROM requests WHERE body LIKE 'which branch%'").fetchall():
            support._set_request_state(con, r["id"], "landed", "the release one")
        con.commit()
        check("decision: answered, it is gone", not waits("decision"))
    finally:
        con.close()


@_reading
def test_a_wait_with_no_answer_says_so_and_fails(home: Path) -> None:
    """`read --wait` ends in an answer or in saying there is none; the hold until the answer lands is the Go daemon's
    request.read (go/internal/reads/inboxget.go)."""
    from handline import cli
    _daemon_home(home, 20)
    agents.register("reviewer")
    parser = cli.build_parser()
    ok, _, rid = askmod.ask("reviewer", "an order", requester=actions.HUMAN)
    check("wait: the order is sent", ok)
    check("wait: an unanswered row times out and says so, rather than printing a half row",
          cliask.cmd_read(parser.parse_args(
              ["read", str(rid), "--wait", "--timeout", "1"])) == 1)

    check("wait: --wait sits next to the positional instead of swallowing it",
          parser.parse_args(["inbox", "reviewer", "--wait"]).agent == "reviewer")


def test_an_answer_to_the_human_is_in_the_human_mailbox(home: Path) -> None:
    """The reply loop was open at the human's end.

    An agent is told what came back by the daemon typing into its terminal.
    Nobody types into a human, so a verdict written on a row the founder asked
    for landed nowhere any screen drew - measured on the live database: twenty
    consecutive orders answered with `ocp resolve`, and the two agents that
    wanted to be seen had learnt to send a second, unrelated request saying the
    same thing. The mailbox holds both directions, and the same two keys work
    on both."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        agents.register("reviewer")
        ok, said, rid = askmod.ask("reviewer", "audit branch keeper",
                                   requester=actions.HUMAN)
        check("mailbox: the human's own order is sent", ok, said)
        check("mailbox: and is not in their own mailbox",
              rid not in [m["id"] for m in actions.messages(con)])

        askmod.resolve(rid, "MERGE.")
        back = [m for m in actions.messages(con) if m["id"] == rid]
        check("mailbox: a verdict on it comes back to the human", len(back) == 1,
              str(actions.messages(con)))
        check("mailbox: from the agent that answered, as an answer",
              back[0]["from"] == "reviewer" and back[0]["kind"] == "answer"
              and back[0]["answer"], str(back[0]))
        check("mailbox: carrying what was said, not what was asked",
              back[0]["body"] == "MERGE.", back[0]["body"])
        check("mailbox: timed by when it came back, not when it was asked",
              back[0]["ts"] >= db.get_request(con, rid)["ts"])

        # Reading it is reading a verdict, which is unreadable on its own:
        # "MERGE." says nothing without the branch it is about.
        title, sections = actions.message_detail(con, rid)
        headings = [h for h, _rows in sections]
        check("mailbox: an opened answer shows what was asked underneath it",
              any("WHAT YOU ASKED" in h for h in headings), str(headings))
        check("mailbox: and the answer itself is the body",
              "MERGE." in "\n".join(sections[0][1]))

        # Replying to an answer is a new request in that answer's thread. It is
        # the one place a parent id is known rather than guessed.
        answer = db.answer_row_of(con, rid)
        check("T421: the answer is a message of its own - its own id, under the request, from the one asked",
              answer is not None and answer["id"] != rid and answer["parent_id"] == rid
              and answer["kind"] == "say" and answer["settles"] == rid and answer["body"].startswith("MERGE.")
              and answer["requester"] == "reviewer" and answer["target"] == actions.HUMAN,
              str(dict(answer) if answer else None))
        said = actions.answer(con, rid, "merged, thank you")
        follow = db.get_request(con, answer["id"] + 1)
        check("mailbox: replying sends it back to the agent that answered",
              follow["target"] == "reviewer" and follow["requester"] == actions.HUMAN,
              said)
        check("mailbox: in the thread it replies to - under the answer, by its own id",
              follow["parent_id"] == answer["id"]
              and follow["chain_id"] == db.get_request(con, rid)["chain_id"],
              f"parent={follow['parent_id']} chain={follow['chain_id']}")
        check("mailbox: and what was replied to leaves the mailbox",
              rid not in [m["id"] for m in actions.messages(con)])
        check("mailbox: without overwriting what the agent said",
              db.shown_verdict(db.get_request(con, rid)) == "MERGE.")

        # A conversation with a human cannot be a runaway: the limit exists to
        # reach a human, and one is typing. Five turns is past max_chain_depth.
        parent = follow["id"]
        for turn in range(5):
            askmod.resolve(parent, f"done {turn}")
            ok, said, parent = askmod.ask("reviewer", f"and now {turn}",
                                          requester=actions.HUMAN, parent_id=parent)
            check(f"mailbox: turn {turn + 1} of the conversation is allowed", ok, said)
        check("mailbox: and it is still one thread",
              db.get_request(con, parent)["chain_id"]
              == db.get_request(con, rid)["chain_id"])
        check("mailbox: with the depth reset by the human's turn",
              db.get_request(con, parent)["chain_depth"] == 0)

        # Dismissing an answer is not resolving it a second time.
        askmod.resolve(parent, "last word")
        actions.dismiss(con, parent)
        check("mailbox: dismissing an answer takes it off the list",
              parent not in [m["id"] for m in actions.messages(con)])
        check("mailbox: and leaves the verdict standing",
              db.shown_verdict(db.get_request(con, parent)) == "last word")

        # The human's name is the human's. It reaches every agent and
        # it is exempt from the loop guards, so an agent that could borrow it
        # would hold both privileges - one spoofed hop makes a refused
        # A->B->A legal.
        os.environ["OCP_AGENT"] = "backend"
        spoofed, why, _rid = askmod.ask("reviewer", "as you", requester=actions.HUMAN)
        check("mailbox: an agent may not ask in the human's name",
              not spoofed and "may not ask as" in why, why)
        os.environ.pop("OCP_AGENT", None)

        # The human is not an agent: no account, no session, nothing to park
        # and nothing that will ever wake them.
        check("mailbox: asking does not park the human",
              db.get_agent_state(con, actions.HUMAN) is None,
              str(dict(db.get_agent_state(con, actions.HUMAN) or {})))

        # Dismissing is not answering: no sender is told, parked or not.
        # Typing "dismissed" at an agent costs it a turn for nothing.
        ff = db.add_request(con, "reviewer", actions.HUMAN, "FYI, shipped it", None,
                            "peer", "fire_and_forget", None, 0, None)
        actions.dismiss(con, ff)
        check("mailbox: a dismissal nobody waited on is not read out to anyone",
              ff not in [r["id"] for r in db.unannounced_answers(con, "reviewer")],
              str(dict(db.get_request(con, ff))))
        parked = db.add_request(con, "reviewer", actions.HUMAN, "which one ships?", None,
                                "peer", "async", None, 0, None)
        actions.dismiss(con, parked)
        check("mailbox: nor is one that parked on a reply - the close is all it gets",
              parked not in [r["id"] for r in db.unannounced_answers(con, "reviewer")],
              str(dict(db.get_request(con, parked))))

        # #185: `resolve` used to call push_answer unconditionally, so a
        # fire_and_forget dismissal delivered and even woke the requester
        # before dismiss ever got to check whether anyone was waiting. Proven
        # here against both an open requester (deliver_answers) and a closed
        # one (wake_agent), by watching the daemon calls dismiss must not make.
        woken: list = []
        delivered: list = []
        real_wake, real_deliver = dmn_lifecycle.wake_agent, dmn_delivery.deliver_answers
        dmn_lifecycle.wake_agent = lambda con_, a, log_: (woken.append(a), (True, "woken"))[1]
        dmn_delivery.deliver_answers = lambda con_, a, log_: (delivered.append(a), True)[1]
        try:
            agents.register("marina")
            db.set_terminal(con, "marina", "term_marina", None)
            ff_open = db.add_request(con, "marina", actions.HUMAN, "FYI, open", None,
                                     "peer", "fire_and_forget", None, 0, None)
            actions.dismiss(con, ff_open)
            check("mailbox: a fire_and_forget dismissal tells an open requester nothing",
                  not delivered and not woken, f"woken={woken} delivered={delivered}")

            db.set_closed(con, "marina", "sess_marina")
            ff_closed = db.add_request(con, "marina", actions.HUMAN, "FYI, closed", None,
                                       "peer", "fire_and_forget", None, 0, None)
            actions.dismiss(con, ff_closed)
            check("mailbox: nor wakes one whose session is closed",
                  not delivered and not woken, f"woken={woken} delivered={delivered}")

            parked2 = db.add_request(con, "marina", actions.HUMAN, "which one?", None,
                                     "peer", "async", None, 0, None)
            actions.dismiss(con, parked2)
            check("mailbox: nor is a parked one told; with nothing else waiting it is idle",
                  not woken and not delivered
                  and db.get_agent_state(con, "marina")["state"] == dmn_states.IDLE,
                  f"woken={woken} delivered={delivered}")
            other = db.add_request(con, "marina", "reviewer", "another?", None,
                                   "peer", "async", None, 0, None)
            db.close_if_queued(con, other, "done", "yes")
            parked3 = db.add_request(con, "marina", actions.HUMAN, "which now?", None,
                                     "peer", "async", None, 0, None)
            actions.dismiss(con, parked3)
            check("mailbox: with another answer untold it reads QUEUED, and only for that",
                  db.get_agent_state(con, "marina")["state"] == dmn_states.QUEUED
                  and [r["id"] for r in db.unannounced_answers(con, "marina")] == [other],
                  str(dict(db.get_agent_state(con, "marina"))))

            # #264: resolve(notify=False) committed 'done' with announced_at
            # still NULL, and dismiss stamped it afterwards with its own
            # db.mark_announced() call on a separate connection - a daemon
            # sweep landing in that gap found the row 'done' with
            # announced_at unset and announced it as news. Called directly,
            # not through dismiss, so this proves resolve() itself does the
            # stamping rather than relying on a caller's follow-up call.
            direct = db.add_request(con, "marina", actions.HUMAN, "direct check", None,
                                    "peer", "fire_and_forget", None, 0, None)
            askmod.resolve(direct, "read - no reply", state="done", notify=False)
            check("resolve: notify=False writes its answer already told, in its own close",
                  db.answer_row_of(con, direct)["delivered_at"] is not None,
                  str(dict(db.get_request(con, direct))))
            check("resolve: ...so it is never in unannounced_answers, even transiently",
                  direct not in [r["id"] for r in db.unannounced_answers(con, "marina")])

            # Skipping the requester's own answer is not the same as skipping
            # the target's queue: one-task-at-a-time (#189) means resolving or
            # dismissing the head of a target's queue is what makes the next
            # request in it deliverable, and that must happen whether or not
            # the requester was told anything.
            woken.clear()
            delivered.clear()
            pushed: list = []
            real_deliver_queued = dmn_delivery.deliver_queued
            dmn_delivery.deliver_queued = lambda con_, a, log_: (pushed.append(a), True)[1]
            try:
                agents.register("nora")
                db.set_terminal(con, "nora", "term_nora", None)
                head_rid = db.add_request(con, "marina", "nora", "first", None,
                                          "peer", "fire_and_forget", None, 0, None)
                db.add_request(con, "marina", "nora", "second", None,
                               "peer", "fire_and_forget", None, 0, None)
                actions.dismiss(con, head_rid)
                check("mailbox: dismissing the head still skips the requester's own answer",
                      not woken and not delivered, f"woken={woken} delivered={delivered}")
                check("mailbox: ...but pushes the target's next queued request",
                      pushed == ["nora"], str(pushed))
            finally:
                dmn_delivery.deliver_queued = real_deliver_queued
        finally:
            dmn_lifecycle.wake_agent, dmn_delivery.deliver_answers = real_wake, real_deliver

        # A refusal is an answer too - the one that means do something else.
        ok, _said, rid2 = askmod.ask("reviewer", "rebase it", requester=actions.HUMAN)
        askmod.resolve(rid2, "no - the base moved", state="rejected")
        refused = [m for m in actions.messages(con) if m["id"] == rid2]
        check("mailbox: a refusal comes back, and says it is one",
              len(refused) == 1 and refused[0]["kind"] == "refused", str(refused))

        actions.dismiss(con, rid2)
        check("mailbox: a dismiss clears it from both",
              rid2 not in [m["id"] for m in actions.messages(con)])
    finally:
        con.close()


def test_the_founder_can_cancel_a_request_in_an_agents_queue(home: Path) -> None:
    """A delivered head its agent never resolved held everything behind it for
    an hour, and `withdraw` refuses anything already delivered. `cancel` closes
    it as rejected - never deletes it - and tells everyone it touches."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[tuple[str, str]] = []
    real = (termmod.alive, termmod.send, termmod.available, termmod.typeable_now,
            askmod.asking_agent)
    try:
        termmod.available = lambda: True
        termmod.live_terminals = lambda: {"term_b", "term_m"}
        termmod.alive = lambda h: True
        termmod.typeable_now = lambda h: True
        termmod.send = lambda h, text, enter=True: (sent.append((h, text)), (True, ""))[1]
        sessions_are("backend", "marina")
        for name, term in (("backend", "term_b"), ("marina", "term_m")):
            agents.register(name)
            db.set_terminal(con, name, term, "wt")
            db.set_agent_state(con, name, dmn_states.IDLE)

        def ask(requester: str, body: str) -> int:
            return db.add_request(con, requester, "backend", body, None,
                                  "peer", "async", "task", 0, None)

        head = ask("marina", "the stuck one")
        mine = ask(askmod.HUMAN, "founder's own, behind it")
        other = ask("marina", "marina's second")
        db.mark_delivered(con, head)
        db.set_cursor(con, "notify:backend", head)

        # Case 2/3: a delivered head, another agent's request.
        ok, why = actions.cancel_plan(con, head)
        check("cancel: the plan says what happens and who is told",
              ok and "closed as rejected" in why and "backend is told to stop" in why
              and "marina is told" in why and f"#{mine} delivers next" in why, why)
        said = actions.cancel(con, head)
        row = db.get_request(con, head)
        check("cancel: a delivered head is closed as rejected with the verdict",
              row["state"] == "landed" and db.shown_verdict(row) == "withdrawn by the founder",
              str(dict(row)))
        check("cancel: and is still in the log, not deleted", row is not None)
        check("cancel: the target is told to stop, at its own terminal",
              any(h == "term_b" and f"#{head}" in t and "cancelled" in t and "Stop work" in t
                  for h, t in sent), str(sent))
        check("cancel: the requester is told through the answer path",
              any(h == "term_m" and f"#{head} (you asked: " in t and ") backend: withdrawn by the founder" in t
                  for h, t in sent), str(sent))
        check("cancel: the item behind it is now the head and is delivered",
              db.head_of_queue(con, "backend")["id"] == mine
              and any(h == "term_b" and f"#{mine} from human" in t for h, t in sent), str(sent))
        check("cancel: the cancel is on the record",
              con.execute("SELECT 1 FROM events WHERE kind = 'cancel'").fetchone() is not None)
        check("cancel: and the result says what was done", "cancelled" in said, said)

        # The founder's own request, delivered: closed like any other, but the
        # founder is not handed their own cancellation as news.
        db.mark_delivered(con, mine)
        sent.clear()
        actions.cancel(con, mine)
        check("cancel: the founder's own delivered request is closed as rejected",
              db.get_request(con, mine)["state"] == "landed")
        check("cancel: and the founder is not told about their own cancel",
              not db.unannounced_answers(con, askmod.HUMAN), str(db.unannounced_answers(con, askmod.HUMAN)))

        # Case 3, undelivered: the target never saw it, so nothing is typed at it.
        # (`other` became the head when the ones before it were cancelled, so it
        # is a request behind it that has not been shown.)
        check("cancel: what was behind a cancelled head was delivered in turn",
              db.get_request(con, other)["delivered_at"] is not None)
        tail = ask("marina", "marina's third, behind a delivered one")
        sent.clear()
        actions.cancel(con, tail)
        other = tail
        check("cancel: an undelivered request of another agent's is closed and its requester told",
              db.get_request(con, other)["state"] == "withdrawn"
              and any(h == "term_m" and f"#{other}" in t for h, t in sent), str(sent))
        check("cancel: with nothing typed at a target that never saw it",
              not any(h == "term_b" and "cancelled" in t for h, t in sent), str(sent))

        # Case 1: the founder's own, not yet delivered - a plain withdraw.
        undelivered = ask(askmod.HUMAN, "changed my mind")
        sent.clear()
        ok, why = actions.cancel_plan(con, undelivered)
        check("cancel: an undelivered request of the founder's is planned as a withdraw",
              ok and why.startswith("withdraw") and "nobody is told" in why, why)
        actions.cancel(con, undelivered)
        check("cancel: it is withdrawn, exactly as `hl withdraw` does",
              db.get_request(con, undelivered)["state"] == "withdrawn")
        check("cancel: and nothing is typed anywhere", not sent, str(sent))

        # An agent mid-work cannot be typed at; the row closes anyway, and the
        # agent cannot undo it by resolving it later.
        busy = ask("marina", "worked on right now")
        db.mark_delivered(con, busy)
        db.set_cursor(con, "notify:backend", busy)
        termmod.typeable_now = lambda h: False
        sent.clear()
        said = actions.cancel(con, busy)
        check("cancel: a target it cannot type at is not told now, and the founder is told so",
              db.get_request(con, busy)["state"] == "landed"
              and "could not be told" in said and not any(h == "term_b" for h, _t in sent), said)
        termmod.typeable_now = lambda h: True

        racer = ask("marina", "resolved at the same instant")
        check("cancel: the close is one write - it loses to a resolve that got there first",
              db.close_if_queued(con, racer, "done", "agent's own answer")
              and not db.close_if_queued(con, racer, "rejected", "withdrawn by the founder")
              and db.shown_verdict(db.get_request(con, racer)) == "agent's own answer")

        # The other direction: cancel's own close wins, and the resolve that
        # lands after it - the target typing an answer to a row the founder
        # just cancelled - is refused rather than overwriting the verdict.
        overtaken = ask("marina", "cancelled just before its answer lands")
        db.close_if_queued(con, overtaken, "rejected", askmod.CANCEL_VERDICT)
        ok, msg = askmod.resolve(overtaken, "did it anyway")
        check("cancel: a resolve landing after the close is refused, not applied",
              not ok and "already withdrawn" in msg
              and db.shown_verdict(db.get_request(con, overtaken)) == askmod.CANCEL_VERDICT, msg)
        check("cancel: a request that is already closed says so",
              "already" in actions.cancel(con, head), actions.cancel(con, head))
        ok, why = actions.cancel_plan(con, 99999)
        check("cancel: and one that is not there says so", not ok, why)

        # The terminal's version: the founder only.
        again = ask("marina", "for the CLI")
        os.environ["OCP_AGENT"] = "marina"
        try:
            out = _capture(lambda: climod.main(["cancel", str(again), "--yes"]))
        finally:
            del os.environ["OCP_AGENT"]
        check("cancel: `hl cancel` refuses an agent", "founder's" in out
              and db.get_request(con, again)["state"] == "queued", out)
        # For the founder it is the Go daemon's request.cancel: a dry run for the plan, then the call that is the go-ahead.
        askmod.asking_agent = lambda: askmod.HUMAN
        asked = []

        def cancels(p):
            asked.append(p)
            return {"ok": True, "plan": f"cancel #{p['id']}: the plan"} if p.get("dry_run") else {"ok": True, "said": "cancelled"}
        with support.daemon_says("request.cancel", cancels):
            out = _capture(lambda: climod.main(["cancel", str(again), "--yes"]))
        check("cancel: with --yes the daemon is asked once, with no dry run, and its words are printed",
              [p.get("dry_run") for p in asked] == [None] and asked[0]["id"] == again and out.strip() == "cancelled", f"{asked} {out}")
        del asked[:]
        with support.daemon_says("request.cancel", cancels):
            rc, out = _cli(["cancel", str(again)], as_agent=None)
        check("cancel: without --yes the plan is shown from a dry run, and with no terminal to confirm in nothing more is asked",
              rc == 1 and [p.get("dry_run") for p in asked] == [True] and "the plan" in out, f"{asked} {out}")
        with support.daemon_says("request.cancel", lambda p: {"ok": False, "plan": "it is gone"}):
            rc, out = _cli(["cancel", "99999"], as_agent=None)
        check("cancel: a request the dry run cannot cancel says why", rc == 1 and out.strip() == "not cancelled - it is gone", out)
        with support.daemon_says("request.cancel", lambda p: {"ok": False, "said": "not cancelled - it is gone"}):
            rc, out = _cli(["cancel", "99999", "--yes"], as_agent=None)
        check("cancel: and a refused cancel prints the daemon's words as they come and fails",
              rc == 1 and out.strip() == "not cancelled - it is gone", out)
    finally:
        (termmod.alive, termmod.send, termmod.available, termmod.typeable_now,
         askmod.asking_agent) = real
        con.close()


def test_a_queued_agent_with_a_told_head_gets_the_same_forgotten_resolve_nudge_as_idle(
        home: Path) -> None:
    """#83 (founder #2284/#2286/#2287), the production trace: keeper's head
    (#2200) was already delivered and told, then a later wake moved it from
    PARKED to QUEUED without retyping anything - QUEUED means only "something
    is queued for this agent", not that the current head is untold. Idle for
    six hours after that with nothing else outstanding is exactly the
    forgotten-resolve case `forgotten_resolve` already catches for IDLE - it
    only excluded QUEUED, so nothing ever reminded it and it never surfaced
    on `blocked.py` either, which reads this same function. Auditor #2296
    measured it live: keeper's queue never moved because of this, not
    because of any silent wake."""
    _daemon_home(home, 20)
    con = db.connect()
    real = (termmod.available, termmod.alive, termmod.typeable_now, termmod.send)
    sent: list[str] = []
    sessions_are("backend")
    try:
        orca_seen()
        termmod.typeable_now = lambda h: True
        termmod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        db.set_terminal(con, "backend", "term_x", "wt")
        now = int(time.time())
        head = db.add_request(con, "marina", "backend", "carry on", None,
                              "peer", "async", "task", 0, None)
        db.add_request(con, "marina", "backend", "and this too", None,
                       "peer", "async", "task", 0, None)
        db.mark_delivered(con, head)
        db.set_cursor(con, "notify:backend", head)
        db.add_event(con, "backend", "Stop", {})
        con.execute("UPDATE events SET ts = ? WHERE agent = 'backend'",
                    (now - dmn_delivery.NUDGE_AFTER_S - 60,))
        con.execute("UPDATE requests SET delivered_at = ? WHERE id = ?",
                    (now - dmn_delivery.NUDGE_AFTER_S - 60, head))
        # QUEUED, not IDLE - a wake, a move, an unstall or a reply can all
        # leave an agent here with its own head already told.
        db.set_agent_state(con, "backend", dmn_states.QUEUED)

        ok = dmn_delivery.nudge_forgotten_resolve(con, "backend", db.get_agent_state(con, "backend"),
                                         now, lambda _m: None)
        check("queued nudge: it is typed for a QUEUED agent, same as an IDLE one",
              ok and sent, str(sent))
        check("queued nudge: names the head and the one behind it",
              sent and f"#{head}" in sent[0] and "1 more waiting" in sent[0], str(sent))
        check("queued nudge: the state and the queue are untouched by the nudge itself",
              db.get_agent_state(con, "backend")["state"] == dmn_states.QUEUED
              and db.get_request(con, head)["state"] == "queued")
    finally:
        termmod.available, termmod.alive, termmod.typeable_now, termmod.send = real
        con.close()


def test_handoff_rate_limited(home: Path) -> None:
    """Two handoffs in quick succession is never right: the usage that caused
    the first cannot have meaningfully changed. A fold is not free."""
    _daemon_home(home, 100)
    con = db.connect()
    real_alive, real_idle, real_clear = termmod.alive, termmod.idle_now, termmod.clear_context
    try:
        orca_idle()
        termmod.clear_context = lambda h, wait=True, timeout_ms=0: (True, "context cleared")
        db.set_terminal(con, "backend", "term_x", "wt")
        t = triggers.Trigger(True, "usage", "pct_5h 100% - at the wall")

        dmn_handoff.perform_handoff(con, "backend", t, lambda _m: None)
        first = con.execute("SELECT COUNT(*) c FROM handoff WHERE agent='backend'").fetchone()["c"]

        # Simulate something un-parking it and the trigger firing again at once.
        db.set_agent_state(con, "backend", dmn_states.RUNNING, detail="un-parked somehow")
        dmn_handoff.perform_handoff(con, "backend", t, lambda _m: None)
        second = con.execute("SELECT COUNT(*) c FROM handoff WHERE agent='backend'").fetchone()["c"]

        check("handoff: a second handoff right after the first is skipped",
              second == first, f"{first} -> {second}")
    finally:
        termmod.alive, termmod.idle_now, termmod.clear_context = real_alive, real_idle, real_clear
        con.close()


def test_an_agent_cannot_make_itself_a_done_target_of_a_task_it_does_not_hold(home: Path) -> None:
    """T792: dismiss/resolve --task links a task to a request; it may link only a task the actor owns or
    was handed, and the target of a request merely linked (a question, a report) can not close the task."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    hand_edit_team("""
agents:
  manager: {}
  worker: {}
  hr: {}
edges:
  - { from: "*", to: "*", relation: peer, response: fire_and_forget }
  - { from: "*", to: "human", kind: task }
limits:
  max_chain_depth: 4
""")
    for n in ("manager", "worker", "hr"):
        agents.register(n)
    con = db.connect()
    try:
        ok, _, founders = actions.task_new("manager", "human", "which name?", "pick one", expects="decision")
        ask_rid = db.add_request(con, actions.HUMAN, "hr", "a side question", None, None, None, "say", 0, None)
        ok, msg = actions.dismiss(con, ask_rid, me="hr", tasks=[founders])
        check("link: an agent cannot link the founder's task to a request it is the target of",
              not ok and "not yours" in msg and not db.task_requests(con, founders)[1:], msg)
        ok, _, mine = actions.task_new("manager", "hr", "hr's own", "w")
        ask2 = db.add_request(con, actions.HUMAN, "hr", "another", None, None, None, "say", 0, None)
        ok, msg = actions.dismiss(con, ask2, me="hr", tasks=[mine])
        check("link: a task the agent was handed can be named", ok, msg)
        ok, _, others = actions.task_new("manager", "worker", "worker's", "w")
        ask3 = db.add_request(con, actions.HUMAN, "hr", "third", None, None, None, "say", 0, None)
        ok, msg = actions.dismiss(con, ask3, me="hr", tasks=[others])
        check("link: and another agent's task cannot", not ok and "not yours" in msg, msg)
        ok, msg = actions.dismiss(con, ask3, me=actions.HUMAN, tasks=[others])
        check("link: the founder may name any task", ok, msg)

        # a link made by hand, as an older ocp or a direct write could: it is no right to close
        db.link_request(con, founders, ask_rid)
        ok, msg = actions.mark_task_done(con, founders, "hr", reason="approve")
        check("done: the target of a linked question can not mark the founder's task done", not ok, msg)
        ok, msg = actions.mark_task_done(con, others, "hr", reason="not mine")
        check("done: nor another agent's task", not ok, msg)
        ok, msg = actions.mark_task_done(con, mine, "hr", reason="mine")
        check("done: the holder can", ok, msg)
        ok, msg = actions.mark_task_done(con, others, "worker", reason="its owner")
        check("done: the owner can", ok, msg)
        ok, msg = actions.mark_task_done(con, founders, actions.HUMAN, reason="the founder")
        check("done: the founder can", ok, msg)

        # the usual reply: a manager answers a founder request naming the task it opened for a report
        ok, _, report = actions.task_new("manager", "worker", "opened for a report", "w")
        fq = db.add_request(con, actions.HUMAN, "manager", "please look at it", None, None, None, "say", 0, None)
        refusal = actions.check_human_resolve(con, db.get_request(con, fq), [report], None, "manager")
        ok1, msg1 = actions.mark_task_done(con, report, "manager", reason="not mine to close")
        check("link: the opener may name the task it opened for someone else, and still cannot close it",
              refusal is None and not ok1, f"{refusal} / {msg1}")

        # a task handed on: the one who gave it away is no longer its holder
        ok, _, handed = actions.task_new("manager", "worker", "to be handed on", "w")
        ok, msg = actions.task_handoff(con, "worker", handed, "hr", "yours now")
        old_link = db.add_request(con, actions.HUMAN, "worker", "after the handoff", None, None, None, "say", 0, None)
        ok1, msg1 = actions.dismiss(con, old_link, me="worker", tasks=[handed])
        ok2, msg2 = actions.mark_task_done(con, handed, "worker", reason="not mine any more")
        check("handoff: the old holder can neither name the task with --task nor close it",
              ok and not ok1 and "not yours" in msg1 and not ok2, f"{msg} / {msg1} / {msg2}")
        new_link = db.add_request(con, actions.HUMAN, "hr", "for the new holder", None, None, None, "say", 0, None)
        ok3, msg3 = actions.dismiss(con, new_link, me="hr", tasks=[handed])
        ok4, msg4 = actions.mark_task_done(con, handed, "hr", reason="it is mine")
        check("handoff: the new holder can do both", ok3 and ok4, f"{msg3} / {msg4}")
    finally:
        con.close()


def test_a_refused_ask_is_never_announced_as_an_answer(home: Path) -> None:
    """A refused ask reached nobody, so nothing can come back from it. Told
    otherwise, an agent reads 34 of its own refusals as replies from the agent
    it never got to ask."""
    _two_agents(home, a_may_ask_b=False)
    askmod.ask("b", "n0", requester="a")

    con = db.connect()
    try:
        denied = con.execute("SELECT COUNT(*) c FROM requests WHERE state = 'denied'").fetchone()
        check("announce: the refusal is on the record", denied["c"] == 1, str(denied["c"]))
        waiting = db.unannounced_answers(con, "a")
        check("announce: but it is not an answer to anything",
              not waiting, str([dict(r) for r in waiting]))
    finally:
        con.close()


@_reading
def test_a_refusal_is_not_read_back_as_an_answer(home: Path) -> None:
    """`hl read` labels a verdict with who answered. On a denied row the
    verdict is this system's refusal, and the row reached no one."""
    _two_agents(home, a_may_ask_b=False)
    askmod.ask("b", "n0", requester="a")
    con = db.connect()
    try:
        rid = con.execute("SELECT id FROM requests WHERE state = 'denied'").fetchone()["id"]
    finally:
        con.close()

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cliask.cmd_read(climod.build_parser().parse_args(["read", str(rid)]))
    out = buf.getvalue()
    check("read: a refused request does not name an answerer",
          "answered by b" not in out, out)
    check("read: it says it was refused", "refused" in out, out)


@_reading
def test_a_withdrawn_or_replaced_request_is_not_read_back_as_an_answer(home: Path) -> None:
    """The same mislabel as the refused case, for the other two states in
    db.NEVER_REACHED_TARGET: #1348 gave a withdrawn row's verdict its own
    label ('withdrawn: <reason>'), but `hl read` still printed '--- answered
    by <target> ---' above it - a row the target never received, read back
    as though it had answered (auditor's #1565, the residual left open at
    #1348)."""
    _two_agents(home)
    ok, _, a = askmod.ask("b", "first", requester="a")
    askmod.withdraw(a, requester="a", reason="typo")

    for rid, label in ((a, "withdrawn"),):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cliask.cmd_read(climod.build_parser().parse_args(["read", str(rid)]))
        out = buf.getvalue()
        check(f"read: a {label} request does not name an answerer",
              "answered by b" not in out, out)
        check(f"read: it says it was {label}", f"--- {label} ---" in out, out)

    # auditor's #1565 NOTE 1: the header dict in cliask.cmd_read and this test's
    # own labels both hardcode the three state names; neither reads
    # db.NEVER_REACHED_TARGET, so a state added to that constant later gets
    # "answered by {target}" again and nothing here would notice. This does
    # not derive the header from the constant (the wording genuinely differs
    # - "denied" prints "refused") but it does make an unlabelled state fail
    # here rather than pass silently: every name the constant holds must
    # appear as a state in cliask.cmd_read's own header keys.
    check("read: every db.NEVER_REACHED_TARGET state has its own header, "
          "not just the ones hardcoded above",
          set(db.NEVER_REACHED_TARGET) <= cliask._NEVER_ANSWERED_HEADERS.keys(),
          str(set(db.NEVER_REACHED_TARGET) - cliask._NEVER_ANSWERED_HEADERS.keys()))


def test_the_done_close_migration_upgrades_an_old_tasks_table(home: Path) -> None:
    """Auditor's #2187 SHOULD-FIX 2: nothing built the pre-#71 schema and ran
    the migration against it. Without the backfill this drives (`closed_by =
    done_evidence, closed_at = done_at`), every existing closed task would
    come back reading as done-but-not-closed - on the founder's board, every
    one of them in the 'done - waiting for check' group."""
    import sqlite3
    home.mkdir(parents=True, exist_ok=True)
    p = home / "old.sqlite"
    old = sqlite3.connect(p)
    old.execute("""
        CREATE TABLE tasks (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          title TEXT NOT NULL, owner TEXT NOT NULL,
          parent_task INTEGER, opened_by TEXT NOT NULL,
          closed_by TEXT, closed_at INTEGER, closed_via TEXT,
          edited_by TEXT, edited_at INTEGER, created_at INTEGER NOT NULL
        )
    """)
    now = int(time.time())
    old.execute(
        "INSERT INTO tasks(title, owner, opened_by, closed_by, closed_at, closed_via, "
        "created_at) VALUES (?,?,?,?,?,?,?)",
        ("shipped it", "worker", "manager", "abc123def456", now - 100, "sha", now - 200))
    old.execute(
        "INSERT INTO tasks(title, owner, opened_by, created_at) VALUES (?,?,?,?)",
        ("still open", "worker", "manager", now - 50))
    old.execute(
        "INSERT INTO tasks(title, owner, opened_by, created_at) VALUES (?,?,?,?)",
        ("already doing", "worker", "manager", now - 30))
    # T286 (4): a minimal old-shape requests/task_requests, so one open task
    # can be linked to an open request - the pre-migration snapshot of
    # `requests` test_migration_adds_the_timebox_columns_... already builds;
    # fyi and the rest _add_missing_columns' own requests-block ALTERs in,
    # ahead of the tasks-timeout backfill that reads it.
    old.execute("""
        CREATE TABLE requests (
          id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,
          requester TEXT NOT NULL, target TEXT NOT NULL, body TEXT NOT NULL,
          files TEXT, relation TEXT, response TEXT, kind TEXT,
          chain_depth INTEGER NOT NULL DEFAULT 0, parent_id INTEGER,
          chain_id TEXT, state TEXT NOT NULL DEFAULT 'queued', verdict TEXT,
          announced_at INTEGER, resolved_at INTEGER, delivered_at INTEGER
        )
    """)
    old.execute("""
        CREATE TABLE task_requests (
          task_id INTEGER NOT NULL, request_id INTEGER NOT NULL,
          PRIMARY KEY (task_id, request_id)
        )
    """)
    old.execute("INSERT INTO requests(ts, requester, target, body, state) VALUES (?,?,?,?,?)",
                (now - 40, "human", "worker", "please", "queued"))
    old.execute("INSERT INTO task_requests(task_id, request_id) VALUES (3, 1)")
    old.commit()
    old.close()

    con = support.upgrade(p)
    try:
        cols = tuple(r["name"] for r in con.execute("PRAGMA table_info(tasks)"))
        check("migration: the new column set, exactly",
              set(cols) == set(db.TASK_FIELDS), str(cols))
        rows = {r["title"]: r for r in con.execute("SELECT * FROM tasks").fetchall()}

        closed = rows["shipped it"]
        check("migration: the old evidence reads as done_evidence/done_via/done_at",
              closed["done_evidence"] == "abc123def456" and closed["done_via"] == "sha"
              and closed["done_at"] == now - 100, str(dict(closed)))
        check("migration: closed_by is backfilled from the evidence, closed_at from done_at",
              closed["closed_by"] == "abc123def456" and closed["closed_at"] == now - 100,
              str(dict(closed)))
        check("migration: it reads as done - a closed task is a done one (T568)",
              actions.task_state(con, closed) == "done", actions.task_state(con, closed))

        open_row = rows["still open"]
        check("migration: an old open row is untouched - done_evidence and closed_by both NULL",
              open_row["done_evidence"] is None and open_row["closed_by"] is None,
              str(dict(open_row)))
        check("migration: it reads as a draft", actions.task_state(con, open_row) == "draft",
              actions.task_state(con, open_row))
        check("migration: T286 (4), founder #1627's revision - a draft (not doing) gets no "
              "timeout from the migration, only a doing task does",
              open_row["timeout_s"] is None and open_row["timeout_at"] is None, str(dict(open_row)))
        check("migration: T286 - the closed task got no timeout either, it was never open here",
              closed["timeout_s"] is None and closed["timeout_at"] is None, str(dict(closed)))

        doing_row = rows["already doing"]
        check("migration: it reads as doing - the request linked to it is still queued",
              actions.task_state(con, doing_row) == "doing", actions.task_state(con, doing_row))
        check("migration: T286 (4) - a task already doing gets a 4h timeout counted from the "
              "migration, not from its own created_at",
              doing_row["timeout_s"] == 14400
              and abs(doing_row["timeout_at"] - (now + 14400)) < 30, str(dict(doing_row)))

        # A second connect (every ocp command, and the daemon) re-runs the
        # migration guard - it must be a no-op the second time, not a second
        # rename onto columns that no longer exist.
        con2 = support.upgrade(p)
        try:
            check("migration: idempotent - a second connect changes nothing",
                  dict(db.get_task(con2, closed["id"])) == dict(closed))
        finally:
            con2.close()
    finally:
        con.close()


def test_a_task_is_done_only_once_its_children_and_blockers_are(home: Path) -> None:
    """T568: there is no close - done is final, so it carries the gates close had: an
    open child task or an open blocker holds a task from being done (the founder may
    pass a blocker), and the founder's decision on a card follows the same rule."""
    _board_world(home)
    con = db.connect()
    try:
        p = db.new_task(con, "P", "worker", "manager")
        c = db.new_task(con, "C", "worker", "manager", p)
        ok, msg = actions.mark_task_done(con, p, "worker", reason="looks done")
        check("done: an open child holds the parent", not ok and f"T{c}" in msg, msg)
        check("done: and nothing was written", not db.get_task(con, p)["done_evidence"])
        ok, msg = actions.mark_task_done(con, c, "worker", reason="done")
        check("done: the child is done", ok, msg)
        ok, msg = actions.mark_task_done(con, p, "worker", reason="looks done")
        check("done: the parent is done once its child is", ok, msg)

        x = db.new_task(con, "X", "worker", "manager")
        bl = db.new_task(con, "BL", "worker", "manager")
        actions.add_block(con, x, bl, "manager")
        ok, msg = actions.mark_task_done(con, x, "worker", reason="done anyway")
        check("done: an open blocker holds the task", not ok and f"T{bl}" in msg, msg)
        ok, msg = actions.mark_task_done(con, x, actions.HUMAN, reason="founder passes it")
        check("done: the founder may pass an open blocker", ok, msg)

        d = db.new_task(con, "which name?", actions.HUMAN, "worker")
        kid = db.new_task(con, "kid", "worker", "manager", d)
        ok, said = actions.decide(con, d, "the first")
        check("decide: an open child refuses it, and nothing is written",
              not ok and f"T{kid}" in said and not db.get_task(con, d)["done_evidence"], said)
        actions.mark_task_done(con, kid, "worker", reason="done")
        ok, said = actions.decide(con, d, "the first")
        check("decide: with the child done, it decides", ok and
              actions.task_state(con, db.get_task(con, d)) == "done", said)
    finally:
        con.close()


def test_a_say_to_the_founder_asks_for_no_reply(home: Path) -> None:
    """Step 5: the status report the guidance now teaches, `ocp say
    human`, is a message that needs no answer - it is not pinned, not a gate, and
    folds like the fyi report it replaces."""
    _board_world(home)
    hand_edit_team("""
agents:
  manager: {}
  worker:  {}

edges:
  - { from: "manager", to: "worker", relation: supervisor, response: fire_and_forget }
  - { from: "worker", to: "manager", relation: subordinate, response: fire_and_forget }
  - { from: "*", to: "human", kind: task }
""")
    con = db.connect()
    try:
        ok, said, rid = askmod.say(askmod.HUMAN, "status: step 5 is up for review", requester="worker")
        check("say to the founder: sent", ok, said)
        row = next(m for m in actions.mailbox_rows(con) if m["id"] == rid)
        check("say to the founder: not a gate and not waiting on them",
              not row["gate"] and not row["waiting"], str(row))
        ok, said, ask_rid = askmod.ask(askmod.HUMAN, "which colour?", requester="worker")
        check("contrast: an ask to the founder is queued for them", ok and "for human" in said, said)
        rows = {m["id"]: m for m in actions.mailbox_rows(con)}
        check("contrast: and it is the one waiting", rows[ask_rid]["gate"] and not rows[rid]["waiting"], str(rows))
        listed = actions.messages(con)
        check("say to the founder: the list folds it into worker's line, unpinned, counted to read",
              any(m["from"] == "worker" and not m["gate"] and not m["waiting"] and m["unread"] >= 1
                  for m in listed) and all(not (m["from"] == "worker" and m["id"] == rid and m["waiting"])
                                            for m in listed), str(listed))
    finally:
        con.close()


def test_asks_reports_and_decisions_become_says_and_queued_ones_become_tasks(home: Path) -> None:
    """T361 step A: no row keeps the kind ask, report or raise that is over, and
    none that is still open is lost - it is a task for its target, or a say."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    con = db.connect()
    support.legacy_shape(con)
    try:
        def row(kind, state, target="worker", requester="manager", fyi=0, body="do the thing\nmore"):
            cur = con.execute(
                "INSERT INTO requests(ts, requester, target, body, kind, state, fyi, chain_depth, edge_ok) "
                "VALUES(?,?,?,?,?,?,?,0,1)", (1000, requester, target, body, kind, state, fyi))
            con.execute("UPDATE requests SET chain_id = ?, queue_order = ? WHERE id = ?",
                        (str(cur.lastrowid), cur.lastrowid, cur.lastrowid))
            return cur.lastrowid

        h_ask, h_rep, h_esc = row(None, "done"), row("report", "done"), row("escalate", "done", target="human")
        h_rej = row(None, "rejected")
        q_delivered = row(None, "queued", target="reviewer")     # already shown to its target, mid-answer
        con.execute("UPDATE requests SET delivered_at = 1001 WHERE id = ?", (q_delivered,))
        q_ask, q_human = row(None, "queued"), row(None, "queued", target="human", requester="worker",
                                                  body="which name?")
        q_fyi = row(None, "queued", target="human", requester="worker", fyi=1)      # only shown to the founder
        q_rep = row("report", "queued")             # an unblock notice: waits for an agent to read it
        q_null_card = row(None, "queued", target="human", requester="worker", body="linked, no kind")
        linked_task = db.new_task(con, "already a task", "worker", "manager")
        q_linked = row(None, "queued")
        db.link_request(con, linked_task, q_linked)
        card_task = db.new_task(con, "already a card", "human", "worker")
        null_task = db.new_task(con, "a card with no kind", "human", "worker")
        db.link_request(con, null_task, q_null_card)
        q_card = row("escalate", "queued", target="human", requester="worker")
        db.link_request(con, card_task, q_card)
        answer, plain_say, task_row = row("answer", "done"), row("say", "done"), row("task", "done")
        tasks_before = con.execute("SELECT COUNT(*) c FROM tasks").fetchone()["c"]
        db.set_cursor(con, "migrated:kinds_to_say", 0)
        con.close()
        con = support.upgrade()

        def kind(rid):
            return db.get_request(con, rid)["kind"]

        check("kinds: a finished ask, report, decision and refusal are says",
              [kind(r) for r in (h_ask, h_rep, h_esc, h_rej)] == ["say"] * 4,
              str([kind(r) for r in (h_ask, h_rep, h_esc, h_rej)]))
        check("kinds: a queued fyi to the founder is a say and opens no task",
              kind(q_fyi) == "say" and not db.tasks_for_request(con, q_fyi))
        check("kinds: a queued report to an agent is left as it is - an unblock notice must still be delivered",
              kind(q_rep) == "report" and db.get_request(con, q_rep)["state"] == "queued"
              and not db.tasks_for_request(con, q_rep) and db.head_of_queue(con, "worker") is not None)
        check("kinds: a linked queued row to the founder with no kind is a card",
              kind(q_null_card) == "escalate")
        check("kinds: a queued ask already linked to a task is that task's delivery",
              kind(q_linked) == "task" and db.tasks_for_request(con, q_linked)[0]["id"] == linked_task)
        check("kinds: a queued decision already a card stays one",
              kind(q_card) == "escalate" and db.tasks_for_request(con, q_card)[0]["id"] == card_task)
        t_ask = db.tasks_for_request(con, q_ask)
        check("kinds: a queued ask that was already delivered is left as it is - it is being answered as an ask",
              kind(q_delivered) is None and not db.tasks_for_request(con, q_delivered)
              and db.get_request(con, q_delivered)["state"] == "queued")
        askmod.resolve(q_delivered, "looks good")
        check("kinds: answered, it leaves no open task behind",
              not db.tasks_for_request(con, q_delivered)
              and db.get_request(con, q_delivered)["state"] == "landed")
        check("kinds: a queued ask becomes a task for its target, the row its delivery",
              len(t_ask) == 1 and t_ask[0]["owner"] == "worker" and t_ask[0]["opened_by"] == "manager"
              and t_ask[0]["title"] == "do the thing" and kind(q_ask) == "task"
              and db.get_request(con, q_ask)["state"] == "queued", str([dict(t) for t in t_ask]))
        t_human = db.tasks_for_request(con, q_human)
        check("kinds: a queued ask to the founder becomes a decision task with the row as its card",
              len(t_human) == 1 and t_human[0]["owner"] == "human" and t_human[0]["expects"] == "decision"
              and kind(q_human) == "escalate" and t_human[0]["title"] == "which name?",
              str([dict(t) for t in t_human]))
        check("kinds: answers, says and tasks are left as they were",
              (kind(answer), kind(plain_say), kind(task_row)) == ("answer", "say", "task"))
        check("kinds: exactly two tasks were opened (the two unlinked queued asks; the card was linked already)",
              con.execute("SELECT COUNT(*) c FROM tasks").fetchone()["c"] == tasks_before + 2)
        check("kinds: no finished ask, report or decision is left over, and no undelivered queued ask without a task",
              con.execute("SELECT COUNT(*) c FROM requests WHERE state != 'queued' AND id != ? AND "
                          "(kind IS NULL OR kind IN ('report', 'escalate'))", (q_delivered,)).fetchone()["c"] == 0
              and con.execute("SELECT COUNT(*) c FROM requests WHERE state = 'queued' AND kind IS NULL "
                              "AND fyi = 0 AND delivered_at IS NULL").fetchone()["c"] == 0)
        ans = db.add_request(con, "worker", "manager", "q", None, None, None, None, 0, None, state="done")
        old_ans = db.add_request(con, "manager", "worker", "the answer", None, None, None, "answer", 0, ans, state="done")
        old_kinds = {}
        for k in (None, "", "answer", "escalate", "report", "task_note"):
            rid_k = db.add_request(con, "worker", "manager", f"old {k}", None, None, None, k, 0, None, state="denied")
            old_kinds[k] = rid_k
        q_old = db.add_request(con, "worker", "manager", "still waiting", None, None, None, "report", 0, None)
        con.execute("DELETE FROM cursors WHERE name = 'migrated:kinds_are_say'")
        con.close()
        con = support.upgrade()
        check("kinds: every kind but say and task, on a finished row, becomes say (T766)",
              all(kind(r) == "say" for r in old_kinds.values()) and kind(task_row) == "task"
              and con.execute("SELECT 1 FROM cursors WHERE name = 'migrated:kinds_are_say'").fetchone() is not None)
        check("kinds: and a queued row is left as it is", kind(q_old) == "report")
        check("kinds: an answer written after the answers became says now settles its request (T766)",
              kind(old_ans) == "say" and db.get_request(con, old_ans)["settles"] == ans)
        con.close()
        con = support.upgrade()
        check("kinds: a second connect changes nothing",
              con.execute("SELECT COUNT(*) c FROM tasks").fetchone()["c"] == tasks_before + 2)
    finally:
        con.close()


def test_task_rows_reads_the_board_in_a_fixed_number_of_queries_and_agrees_with_the_per_task_path(home: Path) -> None:
    """T437: the board is built from a handful of set-based queries, however many
    tasks there are, and says exactly what asking each task one by one says."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    con = db.connect()

    def request(requester, target, state="queued", say=False, ts=None):
        rid = db.add_request(con, requester, target, "body", None, None, None, "say" if say else None,
                             0, None, state="landed" if say else state)
        if ts:
            con.execute("UPDATE requests SET ts = ?, state_at = ? WHERE id = ?", (ts, ts + 5, rid))
        return rid

    def build(n_extra: int) -> None:
        base = db.new_task(con, "parent", "worker", "manager")
        kid = db.new_task(con, "child", "worker", "manager", parent_task=base)
        db.link_request(con, kid, request("manager", "worker"))          # doing through its child
        drafts = db.new_task(con, "a draft", "worker", "manager")
        queued = db.new_task(con, "queued", "worker", "manager")
        db.set_task_queued(con, queued, "human")
        doing = db.new_task(con, "doing", "worker", "manager")
        db.link_request(con, doing, request("manager", "worker"))
        report = db.new_task(con, "only a report", "worker", "manager")
        db.link_request(con, report, request("manager", "worker", say=True))    # asks nothing
        begun = db.new_task(con, "begun on a founder message", "worker", "manager")
        db.add_event(con, "worker", "task_begin", {"task_id": begun})
        founder = db.new_task(con, "founder's own", "worker", "manager")
        db.link_request(con, founder, request(actions.HUMAN, "worker", state="done", ts=1000))
        done = db.new_task(con, "done", "worker", "manager")
        db.mark_task_done(con, done, "evidence")
        gone = db.new_task(con, "discarded", "worker", "manager")
        db.discard_task(con, gone, "manager", "no")
        merged = db.new_task(con, "merged", "worker", "manager")
        db.merge_task(con, merged, drafts, "manager")
        late = db.new_task(con, "late answer", "worker", "manager")
        late_rid = request("manager", "worker", state="done")
        stamp = int(time.time())
        con.execute("UPDATE requests SET ts = ?, state_at = ? WHERE id = ?", (stamp - 10, stamp + 100, late_rid))
        db.link_request(con, late, late_rid)        # answered after every time the task itself has
        blocked = db.new_task(con, "blocked", "worker", "manager")
        db.add_block(con, blocked, doing)
        db.add_block(con, blocked, done)       # a done blocker no longer blocks
        for i in range(n_extra):
            t = db.new_task(con, f"extra {i}", "worker", "manager")
            if i % 3 == 0:
                db.link_request(con, t, request("manager", "worker"))
            if i % 5 == 0:
                db.add_block(con, t, base)

    try:
        build(0)
        for include_ended in (False, True):
            rows = actions.task_rows(con, include_ended=include_ended)
            check(f"task_rows: the fixture covers every state (include_ended={include_ended})",
                  {"draft", "queued", "doing"} <= {r["state"] for r in rows}
                  and (not include_ended or {"done", "discarded"} <= {r["state"] for r in rows}),
                  str(sorted({r["state"] for r in rows})))
            wrong = []
            for r in rows:
                t = r["row"]
                single = {"state": actions.task_state(con, t), "closer": actions.task_asker(con, t),
                          "blocked_by": [b["id"] for b in db.task_blockers(con, t["id"])],
                          "last_activity": db.task_last_activity(con, t["id"])}
                got = {k: r[k] for k in single}
                if got != single:
                    wrong.append((r["ref"], got, single))
            check(f"task_rows: every row equals the per-task answer (include_ended={include_ended})",
                  not wrong, str(wrong[:2]))
        late_row = next(r for r in actions.task_rows(con) if r["title"] == "late answer")
        check("task_rows: a request answered after everything else on the task is its last activity, in both paths",
              late_row["last_activity"] == db.task_last_activity(con, late_row["id"])
              and late_row["last_activity"] > late_row["row"]["created_at"] + 50, str(late_row["last_activity"]))
        check("task_rows: a done blocker does not block, an open one does",
              next(r for r in actions.task_rows(con) if r["title"] == "blocked")["blocked_by"] != [], "")

        seen: list[str] = []
        con.set_trace_callback(seen.append)
        actions.task_rows(con)
        few = len(seen)
        con.set_trace_callback(None)
        build(60)
        seen.clear()
        con.set_trace_callback(seen.append)
        many_rows = actions.task_rows(con)
        many = len(seen)
        con.set_trace_callback(None)
        check("task_rows: the number of queries does not grow with the number of tasks",
              many == few and few <= 8 and len(many_rows) > 60, f"{few} queries for the small board, "
              f"{many} for {len(many_rows)} tasks")
    finally:
        con.close()


def test_an_decisionion_is_a_decision_task_and_the_gate_is_the_open_task(home: Path) -> None:
    """T361 step B2: what the daemon raises for the founder opens a decision task,
    once per shape, with its request as the card; the founder's gate is an open
    decision task, not a kind of row; an old queued decision opens one on migrate."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    con = db.connect()
    support.legacy_shape(con)
    try:
        rid = askmod.raise_decision(con, "backend", "[wait-for] T1 waits on T2\nmore", "wait-for graph")
        tasks = db.tasks_for_request(con, rid)
        check("raise: opens one decision task of the founder's, opened by the agent it is about",
              len(tasks) == 1 and tasks[0]["owner"] == "human" and tasks[0]["opened_by"] == "backend"
              and tasks[0]["expects"] == "decision", str([dict(t) for t in tasks]))
        check("raise: its card is a task row, not an raise one", db.get_request(con, rid)["kind"] == "task")
        check("raise: exactly the same text again is that decision, not a second",
              askmod.raise_decision(con, "backend", "[wait-for] T1 waits on T2\nmore", "wait-for graph") == rid
              and con.execute("SELECT COUNT(*) c FROM tasks WHERE owner = 'human'").fetchone()["c"] == 1)
        other = askmod.raise_decision(con, "backend", "[wait-for] T1 waits on T2\nother", "wait-for graph")
        prefix = askmod.raise_decision(con, "backend", "[wait-for] T1 waits", "wait-for graph")
        check("raise: different text is its own card, kept whole - nothing an agent wrote is lost",
              len({rid, other, prefix}) == 3 and "other" in db.get_request(con, other)["body"]
              and con.execute("SELECT COUNT(*) c FROM tasks WHERE owner = 'human'").fetchone()["c"] == 3)
        one = askmod._stop_runaway(con, "backend", "reviewer", "NOTES ONE", None, "c1", 2, None, "loop")
        two = askmod._stop_runaway(con, "backend", "reviewer", "NOTES TWO different", None, "c1", 2, None, "loop")
        check("raise: two different stopped requests in one chain are two cards, both bodies kept",
              one != two and "NOTES ONE" in db.get_request(con, one)["body"]
              and "NOTES TWO different" in db.get_request(con, two)["body"])
        for extra in (other, prefix, one, two):
            actions.decide(con, db.tasks_for_request(con, extra)[0]["id"], "seen")
        check("gate: the card is the founder's gate and shows in their waits",
              next(m for m in actions.mailbox_rows(con) if m["id"] == rid)["gate"]
              and any(w["kind"] == "decision" and w["who"] == "backend" for w in actions.founder_waits(con)))
        check("gate: open_decisions_of counts it", db.open_decisions_of(con, "backend") == 1)
        ok, msg = actions.decide(con, tasks[0]["id"], "fine")
        check("gate: decided, it is no gate", ok and not db.open_decisions_of(con, "backend"), msg)
        loose = con.execute(
            "INSERT INTO requests(ts, requester, target, body, kind, state, fyi, chain_depth, edge_ok) "
            "VALUES(1000, 'worker', 'human', 'which name?', 'escalate', 'queued', 0, 0, 1)").lastrowid
        db.set_cursor(con, "migrated:escalations_are_tasks", 0)
        con.close()
        con = support.upgrade()
        t = db.tasks_for_request(con, loose)
        check("migrate: a queued decision no task carries opens one, and the row is a task row",
              len(t) == 1 and t[0]["owner"] == "human" and t[0]["opened_by"] == "worker"
              and db.get_request(con, loose)["kind"] == "task", str([dict(x) for x in t]))
        check("migrate: and it is a gate", loose in db.open_decision_card_ids(con))
    finally:
        con.close()


def test_the_per_task_event_lookups_use_the_index_not_a_scan(home: Path) -> None:
    """The task pages look events up by task once per task; each of those queries,
    as the code really runs it, is answered from `events_kind_task` - a scan of
    the events table there is what made every page slow (T430)."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    con = db.connect()
    try:
        for i in range(300):
            db.add_event(con, "worker", "task_begin" if i % 3 == 0 else "reopen_task", {"task_id": i % 7})
        seen: list[tuple[str, tuple]] = []
        calls = (
            lambda: actions.tasks._begun(con, 3),
            lambda: db.task_handoffs(con, 3),
            lambda: actions.tasks._reset_after(con, 3),
        )
        for call in calls:
            con.set_trace_callback(lambda sql: seen.append(sql))
            call()
            con.set_trace_callback(None)
        lookups = [q for q in seen if db.EVENT_TASK in q and "FROM events" in q]
        check("index: every per-task events query was captured", len(lookups) >= 3, str(len(lookups)))
        for q in lookups:
            plan = " | ".join(r[3] for r in con.execute("EXPLAIN QUERY PLAN " + q))
            check(f"index: {q[:60]!r} is a search on events_kind_task, not a scan of events",
                  "events_kind_task" in plan and "SCAN" not in plan, plan)
        # A payload that is not JSON is a row the index skips, never a failed write or build.
        con.execute("INSERT INTO events (ts, agent, kind, payload) VALUES (?, 'w', 'decision', 'not json')",
                    (int(time.time()),))
        con.execute("DROP INDEX events_kind_task")
        con.close()
        con = support.upgrade()          # rebuilds the index over a table that holds the bad row
        check("index: a table holding a non-JSON payload still builds the index and answers",
              con.execute("SELECT name FROM sqlite_master WHERE name = 'events_kind_task'").fetchone()
              is not None and actions.tasks._begun(con, 3))
    finally:
        con.close()


def test_an_answer_is_not_an_ask_for_the_cycle_check(home: Path) -> None:
    """T421 (auditor #2828): an answer row is a message in the chain, not an ask
    in it - so a follow-up under an answer is not a cycle."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    hand_edit_team("""
agents:
  a: {}
  b: {}

edges:
  - { from: "a", to: "b", response: fire_and_forget }
  - { from: "b", to: "a", response: fire_and_forget }

limits:
  max_chain_depth: 8
""")
    for n in ("a", "b"):
        agents.register(n)
    con = db.connect()
    try:
        rids = []
        for i in range(3):
            ok, msg, rid = askmod.ask("b", f"question {i}", requester="a")
            check(f"setup: a's ask {i} goes", ok, msg)
            rids.append(rid)
        for rid in rids:
            askmod.resolve(rid, "answered")
        check("answers: three answers were written as rows", len([1 for r in rids if db.answer_row_of(con, r)]) == 3)
        ok, msg, _ = askmod.ask("a", "b's own ask", requester="b")
        check("answers: b's own ask goes through after answering", ok, msg)

        hand_edit_team("""
agents:
  a: {}
  b: {}

edges:
  - { from: "a", to: "b", response: fire_and_forget }
  - { from: "b", to: "a", response: fire_and_forget }

limits:
  max_chain_depth: 8
""")
        answer = db.answer_row_of(con, rids[0])
        ok, msg, _ = askmod.ask("b", "under the answer", requester="a", parent_id=answer["id"])
        check("cycle: a follow-up under the answer row is not a cycle", ok and "Raised to the founder" not in msg, msg)
        ok, msg, _ = askmod.ask("b", "under its own request", requester="a", parent_id=rids[1])
        check("cycle: nor one under the request that was answered", ok and "Raised to the founder" not in msg, msg)
    finally:
        con.close()


def test_an_answer_is_a_message_of_its_own_with_its_own_number(home: Path) -> None:
    """T421: what answers a request is stored as a row of its own - its own id,
    under the request in its thread - so the chat can number it and a reply can
    name it. A dismissal is no answer, an old verdict is given a row once, and
    the conversation draws each message once."""
    _board_world(home)
    con = db.connect()
    support.legacy_shape(con)
    try:
        ok, msg, rid = askmod.ask("manager", "quick q?", requester="worker")
        check("setup: a request", ok, msg)
        askmod.resolve(rid, "the answer")
        ans = db.answer_row_of(con, rid)
        check("answer row: its own id, under the request, from the one asked to the asker",
              ans is not None and ans["id"] != rid and ans["parent_id"] == rid
              and ans["requester"] == "manager" and ans["target"] == "worker"
              and ans["body"] == "the answer" and ans["kind"] == "say" and ans["settles"] == rid
              and ans["state"] == "landed"
              and ans["chain_id"] == db.get_request(con, rid)["chain_id"], str(dict(ans) if ans else None))
        check("answer row: it is how the asker is told - unshown until then, and listed meanwhile",
              ans["delivered_at"] is None and rid in [r["id"] for r in db.unannounced_answers(con, "worker")])
        db.append_verdict(con, rid, "and one more line")
        check("answer row: a line added to the verdict is added to the message",
              db.shown_verdict(db.get_request(con, rid)).endswith("one more line")
              and db.answer_row_of(con, rid)["body"] == "the answer\n\nand one more line")

        ok, msg, rid2 = askmod.ask("manager", "another?", requester="worker")
        actions.dismiss(con, rid2, me="manager", reason="not needed")
        check("dismissal: no answer row - it is a state, not words", db.answer_row_of(con, rid2) is None)

        # An old verdict, written before answer rows existed, is given one - once.
        old = db.add_request(con, "worker", "manager", "old question", None, None, None, None, 0,
                             None, verdict="old verdict")
        con.execute("UPDATE requests SET state = 'done', resolved_at = 1000, verdict = 'old verdict' WHERE id = ?", (old,))      # the old shape
        db.set_cursor(con, "migrated:answer_rows", 0)
        con.close()
        con = support.upgrade()
        made = db.answer_row_of(con, old)
        check("migration: an old verdict becomes an answer row, at the time it was answered",
              made is not None and made["body"] == "old verdict" and made["ts"] == 1000,
              str(dict(made) if made else None))
        count = con.execute("SELECT COUNT(*) c FROM requests WHERE settles IS NOT NULL").fetchone()["c"]
        con.close()
        con = support.upgrade()
        check("migration: a second connect adds nothing, and the earlier answers are not doubled",
              con.execute("SELECT COUNT(*) c FROM requests WHERE settles IS NOT NULL").fetchone()["c"] == count
              and len([1 for r in con.execute("SELECT id FROM requests WHERE settles = ?", (rid,))]) == 1)

        # A row written before this step, with the kind answer, becomes a say that settles its request.
        legacy = db.add_request(con, "worker", "manager", "q", None, None, None, None, 0, None,
                                state="done", verdict="old answer")
        aid = con.execute("INSERT INTO requests(ts, requester, target, body, kind, state, parent_id, chain_id, "
                          "edge_ok) VALUES (5, 'manager', 'worker', 'old answer', 'answer', 'done', ?, ?, 1)",
                          (legacy, str(legacy))).lastrowid
        db.set_cursor(con, "migrated:answers_are_says", 0)
        con.close()
        con = support.upgrade()
        got = db.get_request(con, aid)
        check("migration: an old answer-kind row becomes a say that settles the request it answered",
              got["kind"] == "say" and got["settles"] == legacy and got["parent_id"] == legacy
              and db.answer_row_of(con, legacy)["id"] == aid, str(dict(got)))
        check("migration: an interim reply from the target is a say but not the answer",
              db.answer_row_of(con, rid)["id"] != db.add_request(
                  con, "manager", "worker", "still on it", None, None, None, "say", 0, rid, state="done"))
    finally:
        con.close()


def test_the_answer_lookups_use_the_settles_index_not_a_scan(home: Path) -> None:
    """T361 B1 (auditor #5476): the chat page finds the answer of every row it
    draws by `settles`; that is a search on requests_settles, never a scan of the
    requests table (which made the page 350 times slower)."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    con = db.connect()
    try:
        for i in range(200):
            rid = db.add_request(con, "worker", "manager", f"q{i}", None, None, None, None, 0, None)
            support._set_request_state(con, rid, "landed")
            if i % 2 == 0:
                db.add_answer(con, rid, "a")
        check("settles: the index exists on a database built now",
              con.execute("SELECT 1 FROM sqlite_master WHERE name = 'requests_settles'").fetchone() is not None)
        for label, sql in (
                ("answer_row_of", "SELECT * FROM requests WHERE settles = ? ORDER BY id LIMIT 1"),
                ("requests_page answer_id", "SELECT (SELECT a.id FROM requests a WHERE a.settles = requests.id "
                                            "ORDER BY a.id LIMIT 1) AS answer_id FROM requests WHERE id = ?"),
                ("append_verdict", "UPDATE requests SET body = body || 'x' WHERE settles = ?")):
            plan = " | ".join(r[3] for r in con.execute("EXPLAIN QUERY PLAN " + sql, (5,)))
            check(f"settles: {label} is a search on requests_settles", "requests_settles" in plan
                  and "SCAN requests" not in plan, plan)
        # An old database, built before the column: the index is made after the column is.
        con.execute("DROP INDEX requests_settles")
        con.close()
        con = support.upgrade()
        check("settles: reconnecting an old database creates the index too",
              con.execute("SELECT 1 FROM sqlite_master WHERE name = 'requests_settles'").fetchone() is not None)
    finally:
        con.close()


def test_decide_records_the_founders_decision_and_closes_the_task(home: Path) -> None:
    """T361 step 2: `actions.decide` is the one way a founder-owned task is
    decided; every refusal comes before anything is written."""
    _board_world(home)
    hand_edit_team("""
agents:
  manager: {}
  worker:  {}

edges:
  - { from: "manager", to: "worker", relation: supervisor, response: fire_and_forget }
  - { from: "worker", to: "manager", relation: subordinate, response: fire_and_forget }
  - { from: "*", to: "human", kind: task }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }

limits:
  max_chain_depth: 4
""")
    ok, msg, tid = actions.task_new("worker", askmod.HUMAN, "which name?", "which name should it have?")
    check("setup: a decision task", ok, msg)
    ok, msg, plain = actions.task_new("manager", "worker", "build it", "build it")
    check("setup: a task that is not the founder's", ok, msg)
    con = db.connect()
    try:
        card = next(r for r in db.task_requests(con, tid) if r["target"] == askmod.HUMAN)["id"]
        for text in ("", "   \n"):
            ok, said = actions.decide(con, tid, text)
            check(f"decide {text!r}: refused as no decision typed", not ok and "no decision" in said, said)
        ok, said = actions.decide(con, 99999, "x")
        check("decide: a missing task is refused", not ok and "no task T99999" in said, said)
        ok, said = actions.decide(con, plain, "x")
        check("decide: a task that is not the founder's is refused", not ok and "not the founder's" in said, said)
        check("decide: the refusals wrote nothing",
              db.get_request(con, card)["state"] == "queued" and not db.get_task(con, tid)["done_evidence"]
              and not db.get_task(con, plain)["done_evidence"])

        ok, msg, kid = actions.task_new("worker", "manager", "sub-question", "a part of it", parent=tid)
        check("setup: an open child task", ok, msg)
        ok, said = actions.decide(con, tid, "call it x")
        check("decide: an open child refuses before anything is written",
              not ok and f"T{kid}" in said and "open child" in said, said)
        check("decide: so the card is still open and the task undecided",
              db.get_request(con, card)["state"] == "queued" and not db.get_task(con, tid)["done_evidence"])
        db.mark_task_done(con, kid, "done")

        ok, said = actions.decide(con, tid, "  call it x  ")
        row = db.get_task(con, tid)
        check("decide: the retry decides", ok, said)
        check("decide: the text is the evidence and the task is done",
              row["done_evidence"] == "call it x", str(dict(row)))
        req = db.get_request(con, card)
        check("decide: the card's request is settled with the same text",
              req["state"] == "landed" and db.shown_verdict(req) == "call it x")
        ok, said = actions.decide(con, tid, "again")
        check("decide: a task already decided is refused and keeps the first decision",
              not ok and "already decided" in said and db.get_task(con, tid)["done_evidence"] == "call it x", said)
    finally:
        con.close()


def test_advance_current_task_never_touches_the_founder(home: Path) -> None:
    """Auditor #1873 finding 1: a decision task (owner human) ending must
    never set a current-task row or reorder a queue for the founder - it
    holds neither."""
    _board_world(home)
    hand_edit_team("""
agents:
  manager: {}
  worker:  {}

edges:
  - { from: "manager", to: "worker", relation: supervisor, response: fire_and_forget }
  - { from: "worker", to: "manager", relation: subordinate, response: fire_and_forget }
  - { from: "*", to: "human", kind: task }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }

limits:
  max_chain_depth: 4
""")
    ok, msg, t_done = actions.task_new("worker", askmod.HUMAN, "decision: done path", "decision: done path")
    check("setup: first decision task opens", ok, msg)
    ok, msg, t_discard = actions.task_new("worker", askmod.HUMAN, "decision: discard path", "decision: discard path")
    check("setup: third decision task opens", ok, msg)
    ok, msg, t_untouched = actions.task_new("worker", askmod.HUMAN, "decision: left queued", "decision: left queued")
    check("setup: fourth decision task opens, its own request left queued throughout", ok, msg)

    con = db.connect()
    try:
        rid_untouched = db.task_requests(con, t_untouched)[0]["id"]
        order_before = db.get_request(con, rid_untouched)["queue_order"]

        ok, msg = actions.mark_task_done(con, t_done, askmod.HUMAN, reason="decided")
        check("mark_task_done: succeeds for a decision task", ok, msg)
        check("finding 1: done - the founder still holds no current-task row",
              db.current_task(con, askmod.HUMAN) is None, db.current_task(con, askmod.HUMAN))

        rid_discard = db.task_requests(con, t_discard)[0]["id"]
        askmod.cancel(rid_discard)
        gone = db.get_task(con, t_discard)
        check("discard_task: cancelling a decision's card discards the task with it",
              gone["discarded_by"] and not gone["done_evidence"], str(dict(gone)))
        ok, msg = actions.discard_task(con, t_discard, askmod.HUMAN, "no longer needed")
        check("discard_task: and it is not discarded twice", not ok and "already discarded" in msg, msg)
        check("finding 1: discard - the founder still holds no current-task row",
              db.current_task(con, askmod.HUMAN) is None, db.current_task(con, askmod.HUMAN))

        order_after = db.get_request(con, rid_untouched)["queue_order"]
        check("finding 1: an untouched decision request's own queue order never moves",
              order_after == order_before, (order_before, order_after))
    finally:
        con.close()


def test_task_memos_anyone_adds_author_or_founder_changes(home: Path) -> None:
    """T493: anyone adds a memo (author recorded), only the author or the founder edits or
    deletes it, edited_at moves only on a real edit, memos_of reads oldest first, and merging
    a task carries its memos to the one kept."""
    _board_world(home)
    con = db.connect()
    try:
        check("memo: the table exists on any db, an old one included",
              con.execute("SELECT name FROM sqlite_master WHERE name = 'task_memos'").fetchone() is not None)
        tid = db.new_task(con, "with memos", "worker", "manager", timeout_s=5400)
        ok, msg, m1 = actions.memo_add(con, tid, actions.HUMAN, "first")
        check("memo: the founder adds one", ok and m1 is not None, msg)
        ok, msg, m2 = actions.memo_add(con, tid, "manager", "second")
        rows = actions.memos_of(con, tid)
        check("memo: an agent adds one; read oldest first, author recorded, never edited",
              ok and [(r["author"], r["body"]) for r in rows] == [(actions.HUMAN, "first"), ("manager", "second")]
              and all(r["edited_at"] is None and r["created_at"] for r in rows), str(rows))
        check("memo: another agent cannot edit or delete it",
              not actions.memo_edit(con, m2, "worker", "x")[0] and not actions.memo_delete(con, m2, "worker")[0])
        check("memo: no memo on a task that is not there, or edit/delete of one that is not",
              not actions.memo_add(con, 999, "manager", "x")[0]
              and not actions.memo_edit(con, 999, actions.HUMAN, "x")[0]
              and not actions.memo_delete(con, 999, actions.HUMAN)[0])
        check("memo: empty text is refused", not actions.memo_add(con, tid, "manager", "  ")[0]
              and not actions.memo_edit(con, m2, "manager", "")[0])
        ok, msg = actions.memo_edit(con, m2, "manager", "second, changed")
        row = db.get_memo(con, m2)
        check("memo: the author edits it, and edited_at is stamped", ok and row["body"] == "second, changed"
              and row["edited_at"] is not None, msg)
        check("memo: the other memo is untouched", db.get_memo(con, m1)["edited_at"] is None)
        check("memo: the same text is no edit", not actions.memo_edit(con, m2, "manager", "second, changed")[0])
        check("memo: the founder edits an agent's memo", actions.memo_edit(con, m2, actions.HUMAN, "by founder")[0])
        ok, msg = actions.memo_delete(con, m1, "manager")
        check("memo: an agent cannot delete the founder's", not ok and "founder" in msg, msg)
        keep = db.new_task(con, "kept", "worker", "manager", timeout_s=5400)
        db.move_task_links(con, tid, keep)
        check("memo: merging carries the memos to the task kept",
              [r["id"] for r in actions.memos_of(con, keep)] == [m1, m2] and not actions.memos_of(con, tid))
        check("memo: the author deletes their own", actions.memo_delete(con, m2, "manager")[0]
              and [r["id"] for r in actions.memos_of(con, keep)] == [m1])
        check("memo: deleting a gone memo is refused", not actions.memo_delete(con, m2, actions.HUMAN)[0])
    finally:
        con.close()


def test_a_memo_change_is_told_to_the_holder_only_while_doing(home: Path) -> None:
    """T493 (founder #6075): a memo added, edited or deleted on a doing task is typed to its
    holder at once, like a say; on a task not doing, or when the holder wrote it, it is silent."""
    _board_world(home)
    con = db.connect()

    def told() -> list[str]:
        return [r["body"] for r in con.execute(
            "SELECT body FROM requests WHERE requester = 'task-board' AND target = 'worker' "
            "AND kind = 'say' ORDER BY id")]
    try:
        ok, msg, tid = actions.task_new("manager", "worker", "doing it", "doing it")
        check("setup: the task is delivered and doing",
              ok and actions.task_state(con, db.get_task(con, tid)) == "doing", msg)
        _, _, mid = actions.memo_add(con, tid, "manager", "mind the edge case")
        check("memo told: an add on a doing task reaches the holder, with who and what",
              told() == [f"T{tid} memo added by manager: mind the edge case"], str(told()))
        rid = con.execute("SELECT MAX(id) m FROM requests WHERE kind = 'say'").fetchone()["m"]
        check("memo told: as a say marked to be typed at once", db.get_request(con, rid)["send_now"] is not None)
        actions.memo_edit(con, mid, "manager", "mind the other edge case")
        actions.memo_delete(con, mid, "manager")
        check("memo told: an edit and a delete are told too",
              len(told()) == 3 and "edited by manager" in told()[1]
              and told()[2] == f"T{tid} memo deleted by manager", str(told()))
        actions.memo_add(con, tid, "worker", "my own note")
        check("memo told: not to the holder about its own change", len(told()) == 3, str(told()))
        actions.memo_add(con, tid, actions.HUMAN, "founder adds")
        check("memo told: the founder's change is told", len(told()) == 4)
        idle = db.new_task(con, "not doing", "worker", "manager", timeout_s=5400)
        actions.memo_add(con, idle, "manager", "quiet")
        check("memo told: silent when the task is not doing", len(told()) == 4, str(told()))
    finally:
        con.close()


def test_a_task_shows_its_memos_when_delivered_and_started_and_old_notes_became_memos(home: Path) -> None:
    """T521 (founder #6225): the delivery of a task lists its memos - author, time, body - in
    one short line, so does `task start`; and a note sent before this became a memo of its task."""
    _board_world(home)
    ok, msg, tid = actions.task_new("manager", "worker", "with memos", "do the thing")
    check("setup: a task is delivered", ok, msg)
    con = db.connect()
    try:
        head = db.head_of_queue(con, "worker")
        check("delivery: a task with no memo says nothing of memos",
              "Memos" not in dmn_delivery.tell_text(con, head, 1))
        actions.memo_add(con, tid, "manager", "use branch y")
        actions.memo_add(con, tid, actions.HUMAN, "the founder says: keep it small")
        text = dmn_delivery.tell_text(con, head, 1)
        check("delivery: every memo is listed short, author and body",
              "Memos - manager " in text and "use branch y" in text
              and f"{actions.HUMAN} " in text and "keep it small" in text, text)
        check("delivery: still three lines at most", len(text.splitlines()) <= 3, text)
        actions.memo_add(con, tid, "manager", "x" * 900)
        check("delivery: a long memo is cut, the line stays short",
              len(actions.memo_summary(con, tid)) <= 501)
        actions.memo_add(con, tid, "manager", "hi\x1b]0;pwned\x07 \x1b[2J\x03 there")
        ok, msg = actions.task_start(con, "worker", tid)
        text = dmn_delivery.tell_text(con, head, 1)
        check("a memo's escape, bell and ^C never reach the terminal, in a delivery or a start",
              "hi" in text and not any(c in text + msg for c in "\x1b\x07\x03"), repr(text))
        check("task start: prints the memos", ok and "Memos - manager " in msg and "use branch y" in msg, msg)
        # an old note: an event from before memos existed, migrated once
        db.add_event(con, "manager", "task_note", {"task_id": tid, "text": "an old note"})
        db.add_event(con, "manager", "task_note", {"task_id": 9999, "text": "for a gone task"})
        con.execute("DELETE FROM cursors WHERE name = 'migrated:notes_are_memos'")
        db.schema._notes_are_memos(con)
        bodies = [m["body"] for m in actions.memos_of(con, tid)]
        check("migration: an old note became a memo of its task, author and text kept",
              "an old note" in bodies and not any(m["task_id"] == 9999 for m in con.execute("SELECT * FROM task_memos")),
              str(bodies))
        before = len(actions.memos_of(con, tid))
        db.schema._notes_are_memos(con)
        check("migration: ledgered, it does not run twice", len(actions.memos_of(con, tid)) == before)
    finally:
        con.close()


def test_one_doing_task_per_agent(home: Path) -> None:
    """T444: an agent holds one doing task. A second task with work out reads queued
    until `task start` makes it current, which sends the first back to queued."""
    _board_world(home)
    con = db.connect()
    try:
        a = db.new_task(con, "first", "worker", "manager", timeout_s=5400)
        b = db.new_task(con, "second", "worker", "manager", timeout_s=5400)
        for t in (a, b):
            db.link_request(con, t, db.add_request(con, "manager", "worker", "go", None, "supervisor",
                                                   "fire_and_forget", None, 0, None))
        db.set_current_task(con, "worker", a)
        check("one doing: the current task reads doing", actions.task_state(con, db.get_task(con, a)) == "doing")
        check("one doing: another with work out reads queued, not draft",
              actions.task_state(con, db.get_task(con, b)) == "queued")
        check("one doing: the board says queued too, and lists it under --state queued",
              [r["state"] for r in actions.task_rows(con) if r["id"] == b] == ["queued"]
              and [r["id"] for r in actions.task_rows(con, state="queued")] == [b])
        ok, msg = actions.task_start(con, "worker", b)
        check("one doing: task start puts the second's request at the head of the queue",
              db.undelivered_for(con, "worker")[0]["id"] == db.task_requests(con, b)[0]["id"])
        check("one doing: task start makes the second current", ok and db.current_task(con, "worker") == b, msg)
        check("one doing: and says the first goes back to queued", f"T{a} goes back to queued" in msg, msg)
        check("one doing: the second now reads doing", actions.task_state(con, db.get_task(con, b)) == "doing")
        check("one doing: the first reads queued", actions.task_state(con, db.get_task(con, a)) == "queued")
        db.mark_task_done(con, b, "ok")
        check("one doing: once the current one is done, the other reads doing again",
              actions.task_state(con, db.get_task(con, a)) == "doing")
    finally:
        con.close()


def test_one_doing_migration(home: Path) -> None:
    """T444: the migration makes the newest asked task current where an agent holds
    none of several, once, and leaves an agent that holds one of them alone."""
    _board_world(home)
    con = db.connect()
    support.legacy_shape(con)
    try:
        ids = {}
        for who in ("worker", "manager"):
            ids[who] = [db.new_task(con, f"{who} {i}", who, "human", timeout_s=5400) for i in range(2)]
            for t in ids[who]:
                db.link_request(con, t, db.add_request(con, "human", who, "go", None, "supervisor",
                                                       "fire_and_forget", None, 0, None))
        db.set_current_task(con, "manager", ids["manager"][0])
        con.execute("DELETE FROM cursors WHERE name = 'migrated:one_doing_each'")
        db.schema._one_doing_task_each(con)
        check("migration: an agent holding none of several gets the newest",
              db.current_task(con, "worker") == ids["worker"][1], db.current_task(con, "worker"))
        check("migration: one already holding one of them is left alone",
              db.current_task(con, "manager") == ids["manager"][0])
        events = [r for r in con.execute("SELECT * FROM events WHERE kind = 'demoted_doing'")]
        check("migration: one demoted_doing event, for the agent changed", len(events) == 1
              and events[0]["agent"] == "worker", str([dict(e) for e in events]))
        db.set_current_task(con, "worker", ids["worker"][0])
        db.schema._one_doing_task_each(con)
        check("migration: ledgered, it does not run twice", db.current_task(con, "worker") == ids["worker"][0])
    finally:
        con.close()


def test_reopening_a_timed_task_resumes_its_clock_only_if_still_doing(home: Path) -> None:
    """T286: whether a reopened task's clock resumes depends on whether it
    still has a request out (`reads_doing`, not the row's own done_evidence,
    which task_state would still misread as "done" here) - resumed from now,
    never from an old, possibly long-passed deadline; paused, same as any
    other not-doing task, if it does not."""
    _board_world(home)
    con = db.connect()
    try:
        doing = db.new_task(con, "ship it", "worker", "manager", timeout_s=3600)
        # A non-human requester, so task_asker still reads "manager" (the
        # opener) - a founder-linked request would make it HUMAN instead,
        # and only the founder could then reopen it.
        rid = db.add_request(con, "worker", "manager", "go", None, "supervisor",
                             "fire_and_forget", None, 0, None)
        db.link_request(con, doing, rid)
        con.execute("UPDATE tasks SET timeout_at = ? WHERE id = ?",
                    (int(time.time()) - 500000, doing))
        db.mark_task_done(con, doing, "shipped", via="reason")
        ok, msg = actions.reopen_task(con, doing, "manager", "not quite done")
        check("reopen: succeeds", ok, msg)
        row = db.get_task(con, doing)
        check("reopen: still doing - timeout_at resumes from now, not its old, passed deadline",
              abs(row["timeout_at"] - (int(time.time()) + 3600)) < 5, str(dict(row)))

        not_doing = db.new_task(con, "no request out", "worker", "manager", timeout_s=3600)
        con.execute("UPDATE tasks SET timeout_at = ? WHERE id = ?",
                    (int(time.time()) + 3600, not_doing))
        db.mark_task_done(con, not_doing, "shipped", via="reason")
        actions.reopen_task(con, not_doing, "manager", "reopen")
        check("reopen: not doing (nothing linked) - the clock is paused, not resumed",
              db.get_task(con, not_doing)["timeout_at"] is None)

        untimed = db.new_task(con, "no timeout on this one", "worker", "manager")
        db.mark_task_done(con, untimed, "shipped", via="reason")
        actions.reopen_task(con, untimed, "manager", "reopen")
        check("reopen: a task with no timeout stays with none",
              db.get_task(con, untimed)["timeout_at"] is None)
    finally:
        con.close()


def test_advance_records_and_skips_a_refused_delivery(home: Path) -> None:
    """T325 (auditor's #1939 MUST-FIX 2): a refused delivery (the edge or
    the owner changed since a task was queued) must not silently stall
    every other queued task behind it - it is recorded as an event, and
    `advance_current_task` moves on to the next candidate instead."""
    _board_world(home)
    hand_edit_team("""
agents:
  manager: {}
  worker:  {}

edges:
  - { from: "manager", to: "worker", relation: supervisor, response: fire_and_forget }
  - { from: "worker", to: "manager", relation: subordinate, response: fire_and_forget }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }

deny:
  - { from: "manager", to: "worker", reason: "simulates the edge changing after a was queued" }

limits:
  max_chain_depth: 4
""")
    con = db.connect()
    try:
        a = db.new_task(con, "a", "worker", "manager", priority=1)
        db.set_task_queued(con, a, "manager")
        # b's own queued_by is not denied - only manager -> worker (a's) is.
        b = db.new_task(con, "b", "worker", askmod.HUMAN, priority=2)
        db.set_task_queued(con, b, askmod.HUMAN)
    finally:
        con.close()

    con = db.connect()
    try:
        changed = actions.advance_current_task(con, "worker")
        check("advance: something changed - it moved past the refused a to b", changed)
        check("advance: b is now worker's current task, delivered",
              db.current_task(con, "worker") == b
              and actions.task_state(con, db.get_task(con, b)) == "doing")
        check("advance: a stays queued, not delivered, not silently dropped",
              actions.task_state(con, db.get_task(con, a)) == "queued")
        events = [json.loads(r["payload"]) for r in con.execute(
            "SELECT payload FROM events WHERE kind = 'task_deliver_refused'").fetchall()]
        check("advance: the refusal is recorded as an event naming the task",
              any(e.get("task_id") == a for e in events), events)
    finally:
        con.close()


def test_ocp_close_ends_a_session_and_never_a_task(home: Path) -> None:
    """T568: a task has no close - done is final. `ocp close T<n>` says so and
    points at `task done` and `task reopen`, and writes nothing."""
    _board_world(home)
    con = db.connect()
    try:
        t = db.new_task(con, "a task", "worker", "manager")
        actions.mark_task_done(con, t, "worker", reason="finished")
    finally:
        con.close()
    rc, out = _cli(["close", f"T{t}"], as_agent="manager")
    check("close: a task is refused, and the way to end or undo one is named",
          rc == 1 and "task done" in out and "task reopen" in out, out)
    rc, out = _cli(["close", "T99999"], as_agent="manager")
    check("close: a task that does not exist is refused the same way", rc == 1 and "task" in out, out)
    rc, out = _cli(["close", str(t)], as_agent="manager")
    check("close: a bare number is not an agent either", rc == 1 and "not an agent" in out, out)
    con = db.connect()
    try:
        check("close: the task is as it was",
              actions.task_state(con, db.get_task(con, t)) == "done")
    finally:
        con.close()


@_reading
def test_replace_withdraw_and_dismiss_store_the_reason_typed(home: Path) -> None:
    """#1337: what was typed is what the record holds, and without a reason the
    old verdicts are unchanged."""
    _board_world(home)
    con = db.connect()
    try:
        ok, _, e = askmod.ask("worker", "third", requester="manager")
        ok, msg = askmod.withdraw(e, requester="manager", reason="not needed after all")
        row = db.get_request(con, e)
        check("reason: a withdraw stores it, labelled like the other two", ok
              and row["state"] == "withdrawn"
              and db.shown_verdict(row) == "withdrawn: not needed after all", str(dict(row)))
        ok, _, f = askmod.ask("worker", "fourth", requester="manager")
        askmod.withdraw(f, requester="manager")
        check("reason: a withdraw with none stores none", db.shown_verdict(db.get_request(con, f)) is None)
        g = db.add_request(con, "worker", "manager", "q", None, "peer", "async", None, 0, None)
        actions.dismiss(con, g, me="manager", reason="duplicate of #1")
        check("reason: a dismiss stores it, as a state and no verdict",
              (db.dismissal(db.get_request(con, g))[2], db.get_request(con, g)["answer"])
              == ("duplicate of #1", None))
        h = db.add_request(con, "worker", "manager", "q", None, "peer", "async", None, 0, None)
        actions.dismiss(con, h, me="manager")
        check("reason: a dismiss with none stores no reason",
              db.dismissal(db.get_request(con, h))[1:]
              == ("manager", None))
    finally:
        con.close()
    # #1348 should-fix: pin how a withdrawn row READS, not only what it
    # stores - `hl read` draws it under "answered by <target>" the same as
    # any other verdict, so a bare typed reason there would look like the
    # target's own words rather than a record that it never received it.
    rc, out = _cli(["read", str(e)])
    check("reason: a withdrawn row's reason reads as a label, not the target's answer",
          rc == 0 and "withdrawn: not needed after all" in out, out)
    # #1348 note 1: a typed empty --reason is not "no reason" - taking it as
    # one would silently drop what was typed.
    ok, _, i = askmod.ask("worker", "fifth", requester="manager")
    rc, out = _cli(["withdraw", str(i), "--reason", ""])
    check("reason: an empty --reason on withdraw is refused", rc == 1 and "empty" in out, out)


@_reading
def test_a_dismissal_is_a_state_and_never_reads_as_words(home: Path) -> None:
    """Task #70: a dismissal is recorded as who dismissed it and why, with no
    verdict text, and every place that draws a verdict says dismissed as a
    state label instead of a quote. The rows the old build wrote, holding the
    literal, read the same way."""
    _board_world(home)
    con = db.connect()
    try:
        new = _human_request()
        ok, msg = actions.dismiss(con, new, me="manager", reason="dup of #9",
                                  no_task="nothing to build")
        row = db.get_request(con, new)
        check("dismissed: state done, who and why recorded, no verdict",
              ok and row["state"] == "landed" and row["answer"] is None
              and db.dismissal(row) == (True, "manager", "dup of #9")
              and row["queue_order"] is None, str(dict(row)))
        old = db.add_request(con, askmod.HUMAN, "manager", "q2", None, "supervisor",
                             "fire_and_forget", None, 0, None, state="done",
                             verdict="read - no reply: dup of #9")
        old_blank = db.add_request(con, askmod.HUMAN, "manager", "q3", None, "supervisor",
                                   "fire_and_forget", None, 0, None, state="done",
                                   verdict="read - no reply")
        real = db.add_request(con, askmod.HUMAN, "manager", "q4", None, "supervisor",
                              "fire_and_forget", None, 0, None, state="done",
                              verdict="read - no reply is not what I mean")
        check("shown_verdict: a new dismissal is a label naming who and why",
              db.shown_verdict(db.get_request(con, new)) == "dismissed by manager - dup of #9")
        check("shown_verdict: the old literal reads as a dismissal too",
              db.shown_verdict(db.get_request(con, old)) == "dismissed - dup of #9"
              and db.shown_verdict(db.get_request(con, old_blank)) == "dismissed")
        check("shown_verdict: a real answer that only starts alike is left alone",
              db.shown_verdict(db.get_request(con, real))
              == "read - no reply is not what I mean")
        # T835 step 2: the say that answered a request is what is shown; the verdict is the fallback.
        said = db.add_answer(con, real, "what was really said")
        check("shown_verdict: the answering say wins over the verdict copy",
              db.shown_verdict(db.get_request(con, real)) == "what was really said")
        con.execute("UPDATE requests SET ts = ts + 777 WHERE id = ?", (said,))
        check("answered_at: the time of the answering say, not the request's own state_at",
              db.answered_at(db.get_request(con, real)) == db.get_request(con, said)["ts"]
              != db.get_request(con, real)["state_at"])
        con.execute("DELETE FROM requests WHERE id = ?", (said,))
        check("answered_at: with no answering say, the time it entered its state",
              db.answered_at(db.get_request(con, real)) == db.get_request(con, real)["state_at"])
        check("shown_verdict: with no answering say the reason it closed with is shown",
              db.shown_verdict(db.get_request(con, real)) == "read - no reply is not what I mean")

        for rid, label in ((new, "new"), (old, "old")):
            rc, out = _cli(["read", str(rid)])
            check(f"dismissed ({label}): hl read shows a state, not an answer",
                  rc == 0 and "--- dismissed" in out and "answered by" not in out
                  and "read - no reply" not in out, out)

        for legacy in (old, old_blank):         # as the upgrade leaves an untold old answer: it has a say
            db.add_answer(con, legacy, db.get_request(con, legacy)["answer"])
        mail = {m["id"]: m for m in actions.mailbox_rows(con)}
        check("dismissed: the founder's mailbox draws the label, not the literal",
              all(rid in mail and "dismissed" in mail[rid]["body"]
                  and "read - no reply" not in mail[rid]["body"] for rid in (old, old_blank)),
              str({k: v["body"] for k, v in mail.items()}))
        thread = actions._exchange(con, "manager", set())
        check("dismissed: the exchange lines carry no verdict text",
              thread and all("read - no reply" not in l for l in thread
                             if "what I mean" not in l)
              and sum("dismissed" in l for l in thread) == 3, str(thread))
        title, sections = actions.message_detail(con, old)
        detail = "\n".join(l for _, ls in sections[:1] for l in ls)
        check("dismissed: an opened message shows the label",
              detail == "dismissed - dup of #9", detail)
        nk = db.add_request(con, "worker", "manager", "no kind", None, "peer", "async", None, 0, None)
        check("detail: a row with no kind is called a say", " · say · " in actions.message_detail(con, nk)[0],
              actions.message_detail(con, nk)[0])
        w = db.add_request(con, "worker", "manager", "q5", None, "peer", "async", None, 0, None)
        actions.dismiss(con, w, me="manager", reason="not now")
        check("dismissed: nothing is left to tell the asker",
              not db.unannounced_answers(con, "worker"),
              str([dict(r) for r in db.unannounced_answers(con, "worker")]))
        check("dismissed: the label is still how the row reads",
              db.shown_verdict(db.get_request(con, w)) == "dismissed by manager - not now")
        gaps = actions.reason_gaps(con)
        check("dismissed: the reason gap still counts both shapes (4 dismissals, 1 blank)",
              (gaps["retirements"], gaps["retirements_blank"]) == (4, 1), str(gaps))
    finally:
        con.close()


def test_dismissing_an_agents_own_answered_request_keeps_its_verdict(home: Path) -> None:
    """Auditor's should-fix on #283: a requester may clear its
    own row once it has left 'queued', but `actions.dismiss` only special-cased
    that for the human - an agent's own answered request fell through to
    `resolve`, which stamped "read - no reply" over the verdict the target
    actually wrote. Dismissing must never rewrite an answer, whoever asked for
    it, and doing it twice must not either."""
    _two_agents(home)
    ok, said, rid = askmod.ask("b", "spec?", requester="a")
    check("dismiss: the ask went through", ok, said)
    askmod.resolve(rid, "yes, ship it")

    con = db.connect()
    try:
        actions.dismiss(con, rid)
        check("dismiss: an agent's own answered request keeps its verdict",
              db.shown_verdict(db.get_request(con, rid)) == "yes, ship it",
              db.shown_verdict(db.get_request(con, rid)))
        check("dismiss: and is marked read", db.answer_row_of(con, rid)["delivered_at"] is not None)

        actions.dismiss(con, rid)
        check("dismiss: dismissing it a second time still keeps the verdict",
              db.shown_verdict(db.get_request(con, rid)) == "yes, ship it",
              db.shown_verdict(db.get_request(con, rid)))
    finally:
        con.close()


def test_dismissing_your_own_answer_does_not_announce_it_for_the_requester(
        home: Path) -> None:
    """Auditor's should-fix on #313: a TARGET dismissing a row it already
    answered must not stamp announced_at on the requester's behalf - that
    would mark the row as told when the requester has never actually seen
    it."""
    _two_agents(home)
    ok, said, rid = askmod.ask("b", "spec?", requester="a")
    check("dismiss: the ask went through", ok, said)
    askmod.resolve(rid, "yes, ship it")

    con = db.connect()
    try:
        check("dismiss: unannounced to start",
              rid in [r["id"] for r in db.unannounced_answers(con, "a")])
        ok, out = actions.dismiss(con, rid, me="b")
        check("dismiss: the target is told there is nothing more to do",
              ok and "nothing more" in out, out)
        check("dismiss: the verdict is untouched",
              db.shown_verdict(db.get_request(con, rid)) == "yes, ship it")
        check("dismiss: ...and the requester is still owed the answer",
              rid in [r["id"] for r in db.unannounced_answers(con, "a")],
              str(dict(db.get_request(con, rid))))
    finally:
        con.close()


def test_bin_ocp_resolves_a_symlink(home: Path) -> None:
    """handline/cli/ and handline/infra/service.py both describe the installed `hl` as a
    symlink into the repo (~/.local/bin/ocp on the founder's own machine) -
    the one path every real invocation runs through, and one every other
    test in this suite skips by calling bin/ocp at its real location. Auditor
    #2096 should-fix 1: an unresolved BASH_SOURCE broke exactly this, silently,
    with the rest of the suite still green."""
    home.mkdir(parents=True, exist_ok=True)
    real_ocp = paths.repo_root() / "bin" / "hl"

    direct = home / "direct-link"
    direct.symlink_to(real_ocp)
    r = subprocess.run([str(direct), "--version"], capture_output=True, text=True, timeout=30)
    check("symlink: a direct symlink to bin/hl still finds _ocp_entry.py",
          r.returncode == 0 and r.stdout.strip() == "hl 0.1.0", f"out={r.stdout!r} err={r.stderr!r}")

    chained = home / "chained-link"
    chained.symlink_to(Path("direct-link"))  # relative, same directory
    r = subprocess.run([str(chained), "--version"], capture_output=True, text=True, timeout=30)
    check("symlink: a link to a link resolves the same way",
          r.returncode == 0 and r.stdout.strip() == "hl 0.1.0", f"out={r.stdout!r} err={r.stderr!r}")


def test_bin_hl_runs_go_hl_only_while_it_is_built_from_this_go_source(home: Path) -> None:
    """bin/hl execs go/hl only while git names the trees go/hl.src recorded for what the build compiled (a pull that
    changed one makes it Python's until a rebuild; a change to other Go does not), with no git call while the stamp is
    newer than git's index; git not answering is Python too; doctor says which."""
    from handline.infra import doctor
    repo = home / "repo"
    (repo / "bin").mkdir(parents=True)
    for pkg in ("cmd/hl", "internal/rpc", "internal/daemon"):
        (repo / "go" / pkg).mkdir(parents=True)
        (repo / "go" / pkg / "a.go").write_text("package a\n")
    shutil.copy(paths.repo_root() / "bin" / "hl", repo / "bin" / "hl")
    (repo / "bin" / "_ocp_entry.py").write_text("print('python')\n")
    (repo / "go" / "hl").write_text("#!/bin/sh\necho go\n")
    (repo / "go" / "hl").chmod(0o755)
    git = lambda *a: subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True, check=True).stdout.strip()  # noqa: E731
    commit = lambda pkg: (  # noqa: E731
        (repo / "go" / pkg / "a.go").write_text((repo / "go" / pkg / "a.go").read_text() + "// changed\n"),
        git("add", "go"), git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", pkg))
    git("init", "-q")
    git("add", "go/cmd", "go/internal")
    git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "one")
    hl = lambda **env: subprocess.run([str(repo / "bin" / "hl")], capture_output=True, text=True, timeout=30,  # noqa: E731
                                      env={**os.environ, **env}).stdout.strip()
    stamp = repo / "go" / "hl.src"
    check("go/hl: no stamp, so Python answers", hl() == "python")
    stamp.write_text(git("rev-parse", "HEAD:go") + "\n")
    os.utime(stamp, (1, 1))
    check("go/hl: a whole-tree stamp from before is not read as current", hl() == "python")
    revs = "HEAD:go/cmd/hl HEAD:go/internal/rpc"
    stamp.write_text(f"{revs}\n{git('rev-parse', *revs.split())}\n")
    os.utime(stamp, (1, 1))                      # older than git's index: the stamp is checked against git
    check("go/hl: built from these trees, so it runs", hl() == "go")
    check("go/hl: and the stamp is touched, so the next run asks git nothing",
          stamp.stat().st_mtime > (repo / ".git" / "index").stat().st_mtime)
    check("go/hl: HANDLINE_HL_GO=0 keeps it on Python", hl(HANDLINE_HL_GO="0") == "python")
    os.utime(stamp, (1, 1))
    check("go/hl: git not answering is Python's, not the build unchecked", hl(GIT_DIR=str(home / "nowhere")) == "python")
    report = doctor.Report()
    real_root, paths.repo_root = paths.repo_root, lambda: repo
    try:
        doctor._check_go_hl(report)
        commit("internal/daemon")
        os.utime(stamp, (1, 1))
        check("go/hl: a commit to Go the build did not compile leaves it on", hl() == "go")
        commit("internal/rpc")
        check("go/hl: a commit to a package it compiled makes it Python's", hl() == "python")
        doctor._check_go_hl(report)
        os.environ["GIT_DIR"] = str(home / "nowhere")
        try:
            doctor._check_go_hl(report)
        finally:
            del os.environ["GIT_DIR"]
        (repo / ".git").rename(repo / "git.off")
        check("go/hl: outside a git checkout the build runs", hl() == "go")
        doctor._check_go_hl(report)
    finally:
        paths.repo_root = real_root
    said = [(level, what) for level, what, *_ in report.rows]
    check("doctor: fine while current, then says to rebuild, then that git gave no answer, then runs as built",
          [lvl for lvl, _ in said] == [doctor.OK, doctor.WARN, doctor.WARN, doctor.OK]
          and "until it is rebuilt" in said[1][1] and "no answer" in said[2][1] and "not a git checkout" in said[3][1],
          said)


def test_closed_tasks_migrate_to_done_and_merged_ones_to_discarded(home: Path) -> None:
    """T568: a task ends only by done or discard. A closed row already carries its
    evidence, so it reads done; a merged row becomes a discard whose reason names
    what it was folded into; an old discard keeps its time in `discarded_at`."""
    home.mkdir(parents=True, exist_ok=True)
    p = home / "t568.db"
    con = db.connect(p)
    keep = db.new_task(con, "keep", "worker", "manager")
    shipped = db.new_task(con, "shipped", "worker", "manager")
    folded = db.new_task(con, "folded", "worker", "manager")
    thrown = db.new_task(con, "thrown away", "worker", "manager")
    db.mark_task_done(con, shipped, "abc123")
    con.execute("UPDATE tasks SET closed_by = 'manager', closed_at = 500 WHERE id = ?", (shipped,))
    con.execute("UPDATE tasks SET closed_by = 'manager', closed_at = 600, merged_into = ? WHERE id = ?",
                (keep, folded))
    con.execute("UPDATE tasks SET closed_by = 'manager', closed_at = 700, discarded_by = 'manager', "
                "discard_reason = 'no' WHERE id = ?", (thrown,))
    con.execute("ALTER TABLE tasks DROP COLUMN discarded_at")
    con.commit()
    con.close()

    con = support.upgrade(p)
    try:
        st = lambda t: actions.task_state(con, db.get_task(con, t))
        check("T568 migration: a closed task reads done, its evidence kept",
              st(shipped) == "done" and db.get_task(con, shipped)["done_evidence"] == "abc123")
        row = db.get_task(con, folded)
        check("T568 migration: a merged task is a discard that says what it went into",
              st(folded) == "discarded" and row["discarded_by"] == "manager"
              and row["discard_reason"] == f"merged into T{keep}" and row["discarded_at"] == 600
              and row["merged_into"] == keep, str(dict(row)))
        row = db.get_task(con, thrown)
        check("T568 migration: an old discard keeps its reason and its time",
              st(thrown) == "discarded" and row["discard_reason"] == "no" and row["discarded_at"] == 700,
              str(dict(row)))
        check("T568 migration: only the open task is open",
              [r["id"] for r in db.open_tasks(con)] == [keep])
    finally:
        con.close()
    con = support.upgrade(p)         # a second connect leaves it as it is
    try:
        check("T568 migration: running again changes nothing",
              actions.task_state(con, db.get_task(con, folded)) == "discarded"
              and db.get_task(con, folded)["discarded_at"] == 600)
    finally:
        con.close()


def test_the_priority_columns_migrate_an_old_tasks_table(home: Path) -> None:
    """T68: a database made before priority and merged_into gains both, and
    every existing task reads P2."""
    home.mkdir(parents=True, exist_ok=True)
    p = home / "old.db"
    old = sqlite3.connect(p)
    old.executescript("""
        CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
          owner TEXT NOT NULL, parent_task INTEGER, opened_by TEXT NOT NULL,
          done_evidence TEXT, done_at INTEGER, done_via TEXT, edited_by TEXT,
          edited_at INTEGER, created_at INTEGER NOT NULL, closed_by TEXT, closed_at INTEGER,
          discarded_by TEXT, discard_reason TEXT);
        INSERT INTO tasks(title, owner, opened_by, created_at) VALUES ('old', 'w', 'm', 5);
    """)
    old.commit()
    old.close()
    con = support.upgrade(p)
    try:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(tasks)")}
        check("migration: priority and merged_into are added",
              set(db.TASK_FIELDS) == cols, str(cols))
        check("migration: an old task is P2", db.get_task(con, 1)["priority"] == 2)
    finally:
        con.close()


def test_task_screen_logic_sorts_flags_idle_and_batches_with_reasons(home: Path) -> None:
    """T69: what the tasks screen shows and does is decided here."""
    _board_world(home)
    con = db.connect()
    try:
        ids = [db.new_task(con, f"t{i}", "worker", "manager") for i in range(11)]
        a, b = ids[9], ids[10]
        actions.edit_task(con, b, "manager", priority=1)
        db.set_task_queued(con, ids[1], "manager")
        rows = actions.task_rows(con)
        by_num = [r["id"] for r in actions.sort_task_rows(rows, "number")]
        by_prio = [r["id"] for r in actions.sort_task_rows(rows, "priority")]
        check("sort: number is numeric, T9 before T10", by_num == sorted(by_num))
        check("sort: priority puts P1 first, then by number", by_prio[0] == b and by_prio[1:] == sorted(by_prio[1:]))
        by_num_rev = [r["id"] for r in actions.sort_task_rows(rows, "number", True)]
        check("sort: reverse flips the order", by_num_rev == by_num[::-1])
        for col in actions.TASK_SORTS:
            got = [r["id"] for r in actions.sort_task_rows(rows, col)]
            check(f"sort: {col} keeps every row once", sorted(got) == by_num)
        old = rows[0]
        check("sort: idle puts the longest idle last, and first when reversed",
              actions.sort_task_rows(rows, "idle", True, old["last_activity"] + 9)[0]["last_activity"]
              == min(r["last_activity"] for r in rows))
        by_state = [r["state"] for r in actions.sort_task_rows(rows, "state")]
        check("sort: state puts drafts before queued", by_state == ["draft"] * 10 + ["queued"], str(by_state))
        now = old["last_activity"] + 3 * 86400
        queued = next(r for r in rows if r["state"] == "queued")
        check("stale: a draft idle 3 days is flagged", old["state"] == "draft" and actions.task_stale(old, now))
        check("stale: a queued task idle 3 days is flagged", actions.task_stale(queued, queued["last_activity"] + 3 * 86400))
        check("stale: not at 2 days", not actions.task_stale(old, old["last_activity"] + 2 * 86400)
              and not actions.task_stale(queued, queued["last_activity"] + 2 * 86400))
        check("stale: a task that is doing or done is never flagged",
              not actions.task_stale({**old, "state": "doing"}, now) and not actions.task_stale({**old, "state": "done"}, now))
        mine = [r["id"] for r in rows if actions.task_related_to(r, "manager")]
        check("related: a task its opener or owner is named is related", set(mine) == set(ids), mine)
        check("related: a stranger has none", not any(actions.task_related_to(r, "nobody") for r in rows))
        res = actions.batch_task(con, "priority", [a, b, 9999], "manager", "P3")
        check("batch: each task gets its own result, a refusal says why",
              [ok for _, ok, _ in res] == [True, True, False] and "no task T9999" in res[2][2], str(res))
        check("batch: the priority was written", db.get_task(con, a)["priority"] == 3)
        res = actions.batch_task(con, "priority", [a], "manager", "P9")
        check("batch: a bad argument refuses without touching anything", not res[0][1] and "not a priority" in res[0][2], str(res))
        res = actions.batch_task(con, "discard", [a], "manager", " ")
        check("batch: discard needs a reason", not res[0][1] and "reason" in res[0][2], str(res))
        res = actions.batch_task(con, "owner", [a], "manager", "nobody-here")
        check("batch: an unknown owner is refused with the reason", not res[0][1] and "no agent" in res[0][2], str(res))
        res = actions.batch_task(con, "discard", [a, b], "worker", "dup")
        check("batch: the same permission rule as one at a time (a worker did not open them)",
              all(not ok for _, ok, _ in res), str(res))
        res = actions.batch_task(con, "discard", [a, b], "manager", "dup")
        check("batch: discard writes for the opener", all(ok for _, ok, _ in res) and
              a not in {r["id"] for r in actions.task_rows(con)}, str(res))
    finally:
        con.close()


def test_a_merge_says_when_it_drops_the_kept_tasks_wait_on_a_folded_one(home: Path) -> None:
    """T129: keep waiting on a task folded into it would wait on itself, so
    that edge goes - and the merge event names it."""
    _board_world(home)
    con = db.connect()
    try:
        keep = db.new_task(con, "keep", "worker", "manager")
        gone = db.new_task(con, "gone", "worker", "manager")
        plain = db.new_task(con, "plain", "worker", "manager")
        db.add_block(con, keep, gone)
        ok, msg = actions.merge_tasks(con, keep, [gone], "manager")
        check("merge wait: the merge goes through", ok, msg)
        check("merge wait: the wait is gone", db.blocks_on_ids(con, keep) == [])
        ok, msg = actions.merge_tasks(con, keep, [plain], "manager")
        ev = [json.loads(r["payload"]) for r in con.execute(
            "SELECT payload FROM events WHERE kind = 'merge_task' ORDER BY id")]
        check("merge wait: the event names the dropped wait, and only where there was one",
              ev[0].get("dropped_wait") is True and "dropped_wait" not in ev[1], str(ev))
    finally:
        con.close()


def test_merge_refusals_leave_everything_as_it_was(home: Path) -> None:
    """T68 (auditor #616): a loop, a blocked folded task, a cycle through a
    waiting task, and a done keep are each refused with nothing changed."""
    _board_world(home)
    con = db.connect()
    try:
        keep = db.new_task(con, "keep", "worker", "manager")
        good = db.new_task(con, "good", "worker", "manager")
        parent = db.new_task(con, "parent", "worker", "manager")
        under = db.new_task(con, "under", "worker", "manager", parent_task=parent)
        gate = db.new_task(con, "gate", "worker", "manager")
        blocked = db.new_task(con, "blocked", "worker", "manager")
        db.add_block(con, blocked, gate)
        folded = db.new_task(con, "folded", "worker", "manager")
        dep = db.new_task(con, "dep", "worker", "manager")
        db.add_block(con, dep, folded)
        db.add_block(con, keep, dep)
        donek = db.new_task(con, "done keep", "worker", "manager")
        db.mark_task_done(con, donek, "finished")

        def snap():
            return ([tuple(r) for r in con.execute("SELECT * FROM tasks ORDER BY id")],
                    [tuple(r) for r in con.execute("SELECT * FROM task_blocks ORDER BY task_id")],
                    [tuple(r) for r in con.execute("SELECT * FROM task_requests")])

        for label, args_, want in (
                ("keep under a folded task", (under, [good, parent]), "would loop"),
                ("a folded task that is blocked", (keep, [good, blocked]), "is waiting on"),
                ("a waiting task would cycle", (keep, [good, folded]), "cycle"),
                ("a done keep", (donek, [good]), "is done")):
            before = snap()
            ok, msg = actions.merge_tasks(con, args_[0], args_[1], "manager")
            check(f"merge refusal: {label}", not ok and want in msg, msg)
            check(f"merge refusal: {label} - nothing changed", snap() == before)
    finally:
        con.close()


def test_the_open_task_count_is_queued_plus_doing(home: Path) -> None:
    """T371: the home screen's number is what an agent has to do. A draft is
    not approved yet, and a done or closed task is over."""
    _board_world(home)
    con = db.connect()
    try:
        count = lambda who="worker": actions.open_task_count(con, who)
        check("count: nothing yet is nothing", count() == 0)
        draft = db.new_task(con, "just a draft", "worker", "manager")
        check("count: a draft that is not current is not counted", count() == 0)
        ok, _, doing = actions.task_new("manager", "worker", "in hand", "in hand")
        check("count: a delivered task is doing, and counted",
              ok and actions.task_state(con, db.get_task(con, doing)) == "doing" and count() == 1)
        queued = db.new_task(con, "next up", "worker", "manager")
        actions.set_queued(con, queued, "manager", True)
        check("count: a queued task behind it is counted too", count() == 2, str(count()))
        finished = db.new_task(con, "finished", "worker", "manager")
        actions.set_queued(con, finished, "manager", True)
        actions.mark_task_done(con, finished, "worker", reason="done")
        check("count: a done task is not", count() == 2, str(count()))
        other = db.new_task(con, "someone else's", "manager", "worker")
        actions.set_queued(con, other, "manager", True)
        check("count: another agent's tasks are not worker's",
              count() == 2 and count("manager") >= 0 and actions.task_state(con, db.get_task(con, draft)) == "draft")
        actions.task_start(con, "worker", draft)
        check("count: a draft the agent picked up (its current task) is being worked, so it counts",
              db.current_task(con, "worker") == draft and count() == 3, str(count()))
        actions.set_queued(con, draft, "manager", True)
        check("count: and is counted once, when it is approved too", count() == 3, str(count()))
    finally:
        con.close()


def test_a_task_is_a_draft_until_queued_and_delivered_when_its_turn_comes(home: Path) -> None:
    """T82/T325 (founder #1907): draft -> queued -> doing. A new task is a
    draft; its owner's manager or the founder queues it - and queuing alone
    now delivers it too, through the same picker T318's advance_current_task
    already used (`_next_waiting_task`/`_link_delivery`): right away if the
    owner is free, or once its turn comes if the owner is busy - never a
    second `ask` to actually get it out."""
    _board_world(home)
    con = db.connect()
    try:
        t = db.new_task(con, "t", "worker", "manager")
        st = lambda: actions.task_state(con, db.get_task(con, t))
        check("states: a new task is a draft", st() == "draft", st())
        ok, msg = actions.set_queued(con, t, "worker", True)
        check("states: the owner cannot queue its own task", not ok and "manager" in msg, msg)
        opened = db.new_task(con, "u", "manager", "worker")
        ok, msg = actions.set_queued(con, opened, "worker", True)
        check("states: an opener that is not the owner's manager cannot queue it",
              not ok and "manager" in msg, msg)

        # worker is already busy with a first, delivered task - queuing `t`
        # must not jump the line: it stays queued, nothing sent for it yet.
        fok, fmsg, first = actions.task_new("manager", "worker", "first", "first")
        check("setup: worker's first task is delivered and current",
              fok and db.current_task(con, "worker") == first, fmsg)

        ok, msg = actions.set_queued(con, t, "manager", True)
        check("states: the owner's manager queues it, but worker is busy - "
              "nothing delivered yet", ok and st() == "queued"
              and "was free" not in msg, msg)
        check("states: queuing twice is refused", not actions.set_queued(con, t, "manager", True)[0])
        check("states: the filter finds it, still just queued",
              [r["id"] for r in actions.task_rows(con, state="queued")] == [t])
        check("states: no request went out for it yet", db.task_requests(con, t) == [])

        ok, msg = actions.set_queued(con, t, actions.HUMAN, False)
        check("states: the founder sends it back to draft, cleanly - there was "
              "never a request to retract", ok and st() == "draft", msg)
        actions.set_queued(con, t, "manager", True)
        check("states: queued again, worker's turn still has not come", st() == "queued", st())

        # worker's first task finishes - its turn moves on, and T325's picker
        # delivers `t` on its own; nothing in this test ever calls `ask`.
        ok, msg = actions.mark_task_done(con, first, "worker", reason="done")
        check("setup: worker's first task is done", ok, msg)
        check("states: marking the first task done delivered the queued one - "
              "doing now, with nobody having sent it", st() == "doing", st())
        check("states: it is worker's current task now", db.current_task(con, "worker") == t)

        ok, msg = actions.set_queued(con, t, "manager", False)
        check("states: a doing task is not drafted by a word", not ok and "doing" in msg, msg)

        # The plain case: an owner already idle when queued gets it right away.
        actions.mark_task_done(con, t, "worker", reason="done")
        idle = db.new_task(con, "idle case", "worker", "manager")
        ok, msg = actions.set_queued(con, idle, "manager", True)
        check("states: queuing for an idle owner delivers right away, no ask needed",
              ok and "was free" in msg
              and actions.task_state(con, db.get_task(con, idle)) == "doing", msg)

        evs = [r["kind"] for r in con.execute("SELECT kind FROM events WHERE kind LIKE '%_task'")]
        check("states: each move is an event",
              evs.count("queue_task") >= 3 and "draft_task" in evs, evs)
    finally:
        con.close()


def test_an_answer_that_does_not_close_a_queued_task_does_not_send_it_again(home: Path) -> None:
    """T352: T205 went out three times, each answered "already fixed" - the
    answered request left the task with no request of its own queued, which
    the picker read as a queued task never delivered."""
    _board_world(home)
    con = db.connect()
    try:
        t = db.new_task(con, "t", "worker", "manager")
        ok, msg = actions.set_queued(con, t, "manager", True)
        (first,) = db.task_requests(con, t)
        check("setup: queuing an idle owner's task delivered it", ok and first["state"] == "queued", msg)
        ok, msg = askmod.resolve(first["id"], "already fixed")
        check("setup: the owner answers without closing the task", ok, msg)
        check("resend: the task has still only the one request",
              [r["id"] for r in db.task_requests(con, t)] == [first["id"]],
              str([dict(r) for r in db.task_requests(con, t)]))
        check("resend: nothing is queued for the owner", db.queued_for(con, "worker") == [])
        actions.advance_current_task(con, "worker")
        check("resend: nor does another advance send it",
              len(db.task_requests(con, t)) == 1)
        check("resend: the task is still open, not done", db.get_task(con, t)["done_evidence"] is None)
    finally:
        con.close()


def test_a_reopened_or_unblocked_task_goes_out_again_after_an_answer(home: Path) -> None:
    """T352 (auditor #2136): the answered request must not stay a reason to keep
    a task from its owner for good - reopening it, or clearing a blocker, is."""
    _board_world(home)
    con = db.connect()
    try:
        def to_worker(tid):
            return [r for r in db.task_requests(con, tid)
                    if r["target"] == "worker" and r["state"] == "queued"]

        t = db.new_task(con, "reopen me", "worker", "manager")
        actions.set_queued(con, t, "manager", True)
        (first,) = db.task_requests(con, t)
        askmod.resolve(first["id"], "done")
        ok, msg = actions.mark_task_done(con, t, "worker", reason="done")
        ok3, msg3 = actions.reopen_task(con, t, "manager", "not really")
        check("reopen: setup", ok and ok3, f"{msg} {msg3}")
        actions.advance_current_task(con, "worker")
        check("reopen: it is delivered once more", len(to_worker(t)) == 1, str(len(to_worker(t))))
        actions.advance_current_task(con, "worker")
        check("reopen: and only once", len(to_worker(t)) == 1)

        for r in to_worker(t):
            askmod.resolve(r["id"], "done again")
        actions.mark_task_done(con, t, "worker", reason="done again")
        u = db.new_task(con, "wait on it", "worker", "manager")
        b = db.new_task(con, "the blocker", "manager", "manager")
        actions.set_queued(con, u, "manager", True)
        (ufirst,) = db.task_requests(con, u)
        ok, msg = actions.add_block(con, u, b, "manager")
        check("unblock: setup", ok, msg)
        askmod.resolve(ufirst["id"], "blocked on it")
        actions.advance_current_task(con, "worker")
        check("unblock: still blocked, nothing goes out", not to_worker(u))
        ok, msg = actions.unblock(con, u, b, "manager")
        actions.advance_current_task(con, "worker")
        check("unblock: once the blocker clears it goes out again", ok and len(to_worker(u)) == 1, msg)
        actions.advance_current_task(con, "worker")
        check("unblock: and only once", len(to_worker(u)) == 1)

        # The dependent's owner finishes its blocker (it asked for it): no notice is written, but the task
        # still comes due again.
        for r in to_worker(u):
            askmod.resolve(r["id"], "done")
        actions.mark_task_done(con, u, "worker", reason="done")
        v = db.new_task(con, "finished by its own owner", "worker", "manager")
        c = db.new_task(con, "asked by worker", "manager", "worker")
        actions.set_queued(con, v, "manager", True)
        (vfirst,) = db.task_requests(con, v)
        ok, msg = actions.add_block(con, v, c, "manager")
        check("own blocker: setup", ok, msg)
        askmod.resolve(vfirst["id"], "blocked")
        ok, msg = actions.mark_task_done(con, c, "manager", reason="done")
        actions.advance_current_task(con, "worker")
        check("own blocker: the blocker being done sends the task out again, once",
              ok and len(to_worker(v)) == 1, f"{msg} {len(to_worker(v))}")
    finally:
        con.close()


def test_the_agent_handing_a_task_on_is_not_parked(home: Path) -> None:
    """T522 (auditor T518): a handoff over an async edge waits on nothing, so the giver stays free."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    hand_edit_team("""
agents:
  manager: {}
  worker: {}
edges:
  - { from: "*", to: "*", relation: peer, response: async }
limits:
  max_chain_depth: 4
""")
    for n in ("manager", "worker"):
        agents.register(n)
    con = db.connect()
    try:
        ok, _, t = actions.task_new("manager", "worker", "the change", "the change")
        ok, msg = actions.task_handoff(con, "worker", t, "manager", "back to you")
        st = db.get_agent_state(con, "worker")
        check("handoff: the giver is not parked over an async edge",
              ok and (st is None or st["state"] != "PARKED"), msg + str(st and dict(st)))
    finally:
        con.close()


def test_a_task_knows_when_its_owner_had_it(home: Path) -> None:
    """T720 (step A): tasks.delivered_at - set when the delivery lands with the owner, cleared when the
    owner changes, backfilled once, and the ghost deliveries settled by the migration."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    hand_edit_team("""
agents:
  manager: {}
  worker: {}
  helper: {}
edges:
  - { from: "*", to: "*", relation: peer, response: fire_and_forget }
  - { from: "*", to: "human", kind: task }
limits:
  max_chain_depth: 4
""")
    for n in ("manager", "worker", "helper"):
        agents.register(n)
    con = db.connect()
    support.legacy_shape(con)
    try:
        ok, _, t = actions.task_new("manager", "worker", "stamp me", "work")
        rid = db.task_requests(con, t)[0]["id"]
        check("delivered_at: not before the delivery lands", db.get_task(con, t)["delivered_at"] is None)
        db.mark_delivered(con, rid)
        check("delivered_at: typed is not landed", db.get_task(con, t)["delivered_at"] is None)
        db.mark_landed(con, rid)
        first = db.get_task(con, t)["delivered_at"]
        check("delivered_at: stamped when it lands with its owner", first is not None)
        db.mark_landed(con, rid)
        check("delivered_at: once - the first stamp stays", db.get_task(con, t)["delivered_at"] == first)
        ok, msg = actions.task_handoff(con, "worker", t, "helper", "yours")
        check("delivered_at: cleared when the owner changes", ok and db.get_task(con, t)["delivered_at"] is None, msg)
        old = [r for r in db.task_requests(con, t) if r["id"] == rid][0]
        db.mark_landed(con, old["id"])
        check("delivered_at: a landing of the old holder's request does not stamp the new owner's task",
              db.get_task(con, t)["delivered_at"] is None)
        ok, _, em = actions.task_new("manager", "worker", "edit the owner", "w")
        emr = db.task_requests(con, em)[0]["id"]
        db.mark_landed(con, emr)
        ok, msg = actions.edit_task(con, em, "manager", owner="helper")
        actions.send_moved_task(con, em, "worker", "manager")      # what every caller does once the edit commits
        reqs = db.task_requests(con, em)
        check("delivered_at: edit --owner settles the old holder's delivery and sends the new owner theirs",
              ok and db.get_request(con, emr)["state"] != "queued"
              and [r["target"] for r in reqs if r["state"] == "queued"] == ["helper"]
              and db.get_task(con, em)["delivered_at"] is None, msg)
        ok, _, sn = actions.task_new("manager", "worker", "sent at once", "w")
        sent = db.get_task(con, sn)["sent_at"]
        check("sent_at: set the moment the delivery is out - before anything lands, and delivered_at is not",
              sent is not None and db.get_task(con, sn)["delivered_at"] is None)
        srid = db.task_requests(con, sn)[0]["id"]
        db.close_if_queued(con, srid, "done", None)
        check("sent_at: cleared when no request that asks is left open on it",
              db.get_task(con, sn)["sent_at"] is None)
        ok, _, fy = actions.task_new("manager", "worker", "only a report linked", "w")
        db.close_if_queued(con, db.task_requests(con, fy)[0]["id"], "done", None)
        rep = db.add_request(con, "worker", "human", "fyi", None, None, "fire_and_forget", "say", 0, None)
        db.link_request(con, fy, rep)
        check("sent_at: an fyi report linked to a task does not send it", db.get_task(con, fy)["sent_at"] is None)
        con.execute("UPDATE tasks SET sent_at = NULL")
        con.execute("DELETE FROM cursors WHERE name = 'migrated:task_sent_at'")
        from handline.db import schema as dbschema2
        dbschema2._task_sent_at(con)
        check("sent_at: the migration backfills it from the requests open now",
              db.get_task(con, t)["sent_at"] is not None and db.get_task(con, sn)["sent_at"] is None)
        ok, _, mk = actions.task_new("manager", "worker", "kept by the merge", "w")
        ok, _, mg = actions.task_new("manager", "worker", "merged away", "w")
        check("sent_at: the task to be merged away is sent", db.get_task(con, mg)["sent_at"] is not None)
        ok, msg = actions.merge_tasks(con, mk, [mg], "manager")
        check("sent_at: a task merged away has no request left on it, so it is no longer sent",
              ok and db.get_task(con, mg)["sent_at"] is None and db.get_task(con, mk)["sent_at"] is not None, msg)
        # T751: the triggers are gone; the task's own events keep sent_at, a ghost delivery cannot hold it
        trig = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'task_sent%'")]
        check("sent_at: no trigger keeps it any more", not trig, str(trig))
        ok, _, gk = actions.task_new("manager", "worker", "open and sent", "w")
        ok, _, gm = actions.task_new("manager", "worker", "stamped before the rule", "w")
        db.mark_task_done(con, gm, "x")
        con.execute("UPDATE tasks SET sent_at = 5 WHERE id = ?", (gm,))
        con.execute("DELETE FROM cursors WHERE name = 'migrated:task_sent_at_events'")
        dbschema2._task_sent_at_from_events(con)
        check("sent_at: the migration clears an ended task's stamp and keeps an open one's",
              db.get_task(con, gm)["sent_at"] is None and db.get_task(con, gk)["sent_at"] is not None)
        for how in ("done", "discard"):
            ok, _, gh = actions.task_new("manager", "worker", f"ghost on {how}", "w")
            if how == "done":
                db.mark_task_done(con, gh, "x")
            else:
                db.discard_task(con, gh, "manager", "x")
            gr = db.task_requests(con, gh)[0]
            check(f"sent_at: a task {how} is not sent, whatever is still queued against it",
                  gr["state"] == "queued" and db.get_task(con, gh)["sent_at"] is None)
            db.link_request(con, gh, gr["id"])
            db.refresh_sent(con, gh)
            check(f"sent_at: and a refresh re-derives it from the request still out (what reopen does)",
                  db.get_task(con, gh)["sent_at"] is not None)
        # T751 D3, the #7789 shape: a done task whose delivery is still queued reaches nobody
        ok, _, g1 = actions.task_new("manager", "worker", "ghost first", "w")
        ghost = db.task_requests(con, g1)[0]["id"]
        ok, _, g2 = actions.task_new("manager", "worker", "waits behind the ghost", "w")
        behind = db.task_requests(con, g2)[0]["id"]
        before_head = db.head_of_queue(con, "worker")["id"]
        db.mark_task_done(con, g1, "done elsewhere")          # the task ends; its delivery is left queued
        check("ghost: the delivery is still queued (the #7789 shape)", db.get_request(con, ghost)["state"] == "queued")
        head = db.head_of_queue(con, "worker")
        check("ghost: it is never the inbox head, and what waited behind it is",
              head is not None and head["id"] != ghost
              and ghost in [r["id"] for r in db.task_requests(con, g1)] and behind in [r["id"] for r in db.queued_for(con, "worker")],
              str(head and head["id"]))
        check("ghost: nor counted in the queue, nor handed out by the delivery reads",
              ghost not in [r["id"] for r in db.queued_for(con, "worker")]
              and ghost not in [r["id"] for r in db.undelivered_for(con, "worker")]
              and ghost not in [r["id"] for r in db.send_now_pending(con, "worker")])
        con.execute("UPDATE requests SET ts = ts - 100000 WHERE id = ?", (ghost,))
        from handline.session import observe as obsmod, term as termmod
        was = (termmod.available, obsmod.live_sessions)
        termmod.available, obsmod.live_sessions = (lambda: False), (lambda: {})
        stuckmod._WORLD["at"] = 0.0
        try:
            stuck_now = stuckmod.stuck(con)
        finally:
            termmod.available, obsmod.live_sessions = was
        check("ghost: and the stuck list does not carry it, however old",
              not any(f"#{ghost} " in str(i) for i in stuck_now) and any(f"#{behind} " in str(i) for i in stuck_now),
              str(stuck_now))
        check("ghost: `hl doctor` lists it", ghost in [r["id"] for r in db.leftover_deliveries(con)])
        fg = db.new_task(con, "founder's, ended", "worker", actions.HUMAN)
        frid = db.add_request(con, actions.HUMAN, "worker", "from the founder", None, None, "fire_and_forget",
                              "task", 0, None)
        db.link_request(con, fg, frid)
        check("ghost: the founder's delivery of an open task is pending for the agent",
              frid in [r["id"] for r in db.founder_pending(con, "worker")])
        db.mark_task_done(con, fg, "x")
        con.execute("UPDATE requests SET delivered_at = ts WHERE id IN (?, ?)", (frid, ghost))
        check("ghost: the founder's delivery of an ended task is not told past an open head",
              frid not in [r["id"] for r in db.founder_pending(con, "worker")])
        from handline.infra import doctor as doctormod
        import inspect
        rep = doctormod.Report()
        doctormod._check_leftover_deliveries(rep)
        check("doctor: warns of each leftover, and does not point at `hl cancel`",
              len(rep.rows) >= 2 and all(lv == doctormod.WARN for lv, _, _ in rep.rows)
              and not any("hl cancel" in d for _, _, d in rep.rows), str(rep.rows))
        full = doctormod.run()
        check("doctor: the check is part of the run - `doctor.run()` reports the leftover as a WARN",
              any(lv == doctormod.WARN and f"request #{ghost} " in str(t) for lv, t, _ in full.rows), str(full.rows))
        settled = db.settle_leftover_deliveries(con)
        check("sweep: the leftovers are settled quietly - closed, nobody told, their tasks untouched",
              {frid, ghost} <= set(settled) and db.get_request(con, frid)["state"] == "landed"
              and db.get_task(con, fg)["done_evidence"] == "x" and not db.leftover_deliveries(con))
        rep2 = doctormod.Report()
        doctormod._check_leftover_deliveries(rep2)
        check("doctor: and says nothing when there are none", rep2.rows == [])
        check("ghost: closed, it is no leftover", ghost not in [r["id"] for r in db.leftover_deliveries(con)])
        ok, _, g3 = actions.task_new("manager", "worker", "handed on", "w")
        away = db.task_requests(con, g3)[0]["id"]
        con.execute("UPDATE tasks SET owner = 'helper' WHERE id = ?", (g3,))
        check("ghost: a delivery whose task is with someone else is a leftover too",
              away in [r["id"] for r in db.leftover_deliveries(con)]
              and away not in [r["id"] for r in db.queued_for(con, "worker")])
        check("sweep: a task moved to someone else is not settled - only an ended one is",
              away not in db.settle_leftover_deliveries(con) and db.get_request(con, away)["state"] == "queued")
        con.execute("UPDATE tasks SET owner = 'worker' WHERE id = ?", (g3,))
        ok, _, lo = actions.task_new("manager", "worker", "ended with a delivery left", "w")
        lor = db.task_requests(con, lo)[0]["id"]
        db.mark_task_done(con, lo, "x")
        import argparse, contextlib, io
        from handline.cli import health
        from handline.cli import parser as parsermod
        parsed = parsermod.build_parser().parse_args(["doctor", "--settle-leftovers"])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            health.cmd_doctor(parsed)
        check("doctor --settle-leftovers: closes an ended task's delivery quietly, and says which",
              db.get_request(con, lor)["state"] == "landed" and f"#{lor}" in out.getvalue(), out.getvalue()[:200])
        ok, _, up = actions.task_new("manager", "worker", "ended before the upgrade", "w")
        upr = db.task_requests(con, up)[0]["id"]
        db.mark_task_done(con, up, "x")
        ok, _, upm = actions.task_new("manager", "worker", "moved on before the upgrade", "w")
        upmr = db.task_requests(con, upm)[0]["id"]
        con.execute("UPDATE tasks SET owner = 'helper' WHERE id = ?", (upm,))
        con.execute("DELETE FROM cursors WHERE name = 'migrated:task_leftovers_settled'")
        con2 = support.upgrade()                             # the upgrade: the daemon's start runs the migrations
        try:
            gone = db.get_request(con2, upr)
            check("upgrade migration: an ended task's delivery is closed quietly, once",
                  gone["state"] == "landed" and gone["queue_order"] is None
                  and db.get_cursor(con2, "migrated:task_leftovers_settled") == 1)
            check("upgrade migration: a delivery whose task moved on is left queued",
                  db.get_request(con2, upmr)["state"] == "queued")
        finally:
            con2.close()
        ok, _, mv = actions.task_new("manager", "worker", "moved between commit and send", "w")
        mvr = db.task_requests(con, mv)[0]["id"]
        ok, msg = actions.edit_task(con, mv, "manager", owner="helper")
        db.settle_leftover_deliveries(con)                  # a daemon tick in the window after the commit
        actions.send_moved_task(con, mv, "worker", "manager")
        check("sweep: a tick between edit_task and send_moved_task still leaves the new owner asked",
              ok and [r["target"] for r in db.task_requests(con, mv) if r["state"] == "queued"] == ["helper"]
              and db.get_request(con, mvr)["state"] == "withdrawn", msg)
        check("ghost: and an open task with its owner is never one",
              away not in [r["id"] for r in db.leftover_deliveries(con)])
        # a founder's card is delivered when drawn
        ok, _, card = actions.task_new("manager", "human", "decide", "decide")
        crid = db.task_requests(con, card)[0]["id"]
        kind = db.get_request(con, crid)["kind"]
        db.mark_delivered(con, crid)
        check("delivered_at: a founder's card, when drawn",
              db.get_task(con, card)["delivered_at"] is not None, f"kind={kind} {dict(db.get_task(con, card))}")
        ok, _, fyi_t = actions.task_new("manager", "human", "a card with a say linked", "decide")
        ok, _, say_rid = askmod.ask("human", "just so you know", requester="manager", kind="say")
        db.link_request(con, fyi_t, say_rid)
        db.mark_delivered(con, say_rid)
        check("delivered_at: a say to the founder linked to his task does not stamp it",
              db.get_task(con, fyi_t)["delivered_at"] is None)
        # the migration: backfill, and ghosts settled
        ok, _, landed = actions.task_new("manager", "worker", "landed long ago", "w")
        lrid = db.task_requests(con, landed)[0]["id"]
        con.execute("UPDATE requests SET landed_at = 1234, state = 'landed' WHERE id = ?", (lrid,))
        ok, _, ghost = actions.task_new("manager", "worker", "ended with a delivery left", "w")
        grid = db.task_requests(con, ghost)[0]["id"]
        db.mark_task_done(con, ghost, "done the old way", "reason")
        ok, _, moved = actions.task_new("manager", "worker", "handed on, old one left", "w")
        mrid = db.task_requests(con, moved)[0]["id"]
        con.execute("UPDATE tasks SET owner = 'helper' WHERE id = ?", (moved,))
        ok, _, two_a = actions.task_new("manager", "worker", "shares a delivery - ended", "w")
        ok, _, two_b = actions.task_new("manager", "worker", "shares a delivery - open", "w")
        shared = db.task_requests(con, two_a)[0]["id"]
        db.link_request(con, two_b, shared)
        db.mark_task_done(con, two_a, "done", "reason")
        con.execute("DELETE FROM cursors WHERE name = 'migrated:task_deliveries_settled'")
        con.execute("UPDATE tasks SET delivered_at = NULL")
        from handline.db import schema as dbschema
        dbschema._task_deliveries_settled(con)
        check("delivered_at: the migration backfills from a landed delivery",
              db.get_task(con, landed)["delivered_at"] == 1234)
        check("delivered_at: and leaves an unlanded one empty", db.get_task(con, t)["delivered_at"] is None)
        check("delivered_at: a delivery left queued on an ended task is settled",
              db.get_request(con, grid)["state"] == "landed")
        check("delivered_at: so is one left with the old holder after a hand-on",
              db.get_request(con, mrid)["state"] == "landed")
        check("delivered_at: and a live delivery is left alone - landed, and still the owner's to hold",
              db.get_request(con, lrid)["state"] == "landed" and db.live_delivery(con, landed, "worker") is not None)
        check("delivered_at: a delivery shared with a task still open is not a ghost",
              db.get_request(con, shared)["state"] == "queued")
        dbschema._task_deliveries_settled(con)
        check("delivered_at: the migration runs once", db.get_task(con, landed)["delivered_at"] == 1234)
    finally:
        con.close()


def test_a_review_is_an_ordinary_child_the_owner_waits_on(home: Path) -> None:
    """T442: no review or merge command - the owner opens a child task for the auditor
    (`task new --parent`), which blocks its task while it stays the owner's and doing;
    the child is done with its result, and the owner is told it in the unblock notice."""
    home.mkdir(parents=True, exist_ok=True)
    os.environ["OCP_HOME"] = str(home)
    hand_edit_team("""
agents:
  manager: {}
  dev.worker: {}
  dev.auditor: {}
edges:
  - { from: "*", to: "*", relation: peer, response: async }
limits:
  max_chain_depth: 4
""")
    for n in ("manager", "dev.worker", "dev.auditor"):
        agents.register(n)
    con = db.connect()
    try:
        ok, _, t = actions.task_new("manager", "dev.worker", "the change", "the change")
        ok, msg, kid = actions.task_new("dev.worker", "dev.auditor", "review T%d @abc1234" % t,
                                        "branch b: fixed it", parent=t, expects="verdict")
        check("review: the child opens for the auditor", ok, msg)
        check("review: T stays its owner's and is blocked by the child",
              db.get_task(con, t)["owner"] == "dev.worker"
              and [b["id"] for b in db.task_blockers(con, t)] == [kid])
        ok, msg = actions.mark_task_done(con, kid, "dev.auditor", reason="FAIL: retry loops; repro x")
        check("review: the child is done with the result", ok, msg)
        told = [r["body"] for r in db.task_requests(con, t) if "unblocked" in r["body"]]
        result = [r["body"] for r in db.task_requests(con, t) if "FAIL: retry loops; repro x" in r["body"]]
        check("review: the block lifts; the result is told once, on its own, and not in the notice",
              ok and not db.task_blockers(con, t) and len(told) == 1 and "FAIL" not in told[0]
              and result == ["T%d done by dev.auditor: FAIL: retry loops; repro x" % kid],
              f"{told} {result}")
        check("review: T is still the worker's",
              db.get_task(con, t)["owner"] == "dev.worker" and not db.get_task(con, t)["done_evidence"])
    finally:
        con.close()


def test_a_begun_task_reads_doing_the_same_way_everywhere(home: Path) -> None:
    """T286 review: `task_state` and `reads_doing` (the timeout clock's read) must
    agree that a task begun on a founder message is doing, and releasing one says
    so instead of failing."""
    _board_world(home)
    con = db.connect()
    try:
        chat = _human_request("worker")
        ok, msg, tid = actions.task_begin(con, "worker", "begun work", chat, "the founder asked")
        row = db.get_task(con, tid)
        check("begun: task_state says doing", ok and actions.task_state(con, row) == "doing", msg)
        check("begun: and reads_doing agrees", actions.reads_doing(con, tid))
        db.set_task_queued(con, tid, "worker")
        con.execute("UPDATE requests SET state = 'landed' WHERE id = ?", (chat,))
        check("begun: still doing after its chat request is answered, in both",
              actions.task_state(con, db.get_task(con, tid)) == "doing" and actions.reads_doing(con, tid))
        ok, msg = actions.release_task(con, tid, askmod.HUMAN, "stuck")
        check("begun: releasing it says it stays doing, without crashing",
              not ok and "begun on a founder message" in msg, msg)
        plain = db.new_task(con, "plain draft", "worker", "manager")
        check("begun: a task that was not begun and has no request is not doing in either",
              actions.task_state(con, db.get_task(con, plain)) == "draft"
              and not actions.reads_doing(con, plain))
    finally:
        con.close()


def test_releasing_a_task_the_founder_holds_returns_it_to_draft_not_to_the_bin(home: Path) -> None:
    """Retiring the founder's card on a release must not settle the task with it:
    a discarded task cannot be reopened."""
    _board_world(home)
    hand_edit_team("""
agents:
  manager: {}
  worker:  {}

edges:
  - { from: "manager", to: "worker", relation: supervisor, response: fire_and_forget }
  - { from: "worker", to: "manager", relation: subordinate, response: fire_and_forget }
  - { from: "*", to: "human", kind: task }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }

limits:
  max_chain_depth: 4
""")
    ok, msg, tid = actions.task_new("worker", askmod.HUMAN, "which name?", "which name should it have?")
    check("release: setup, a founder-held task", ok, msg)
    con = db.connect()
    try:
        ok, msg = actions.release_task(con, tid, askmod.HUMAN, "stuck")
        row = db.get_task(con, tid)
        check("release: the founder's own task goes back to draft, not discarded",
              ok and not row["discarded_at"] and actions.task_state(con, row) == "draft", msg)
        ok, msg = actions.set_queued(con, tid, askmod.HUMAN, True)
        check("release: and the founder can queue it again, which puts its card back on their board",
              ok and actions.task_state(con, db.get_task(con, tid)) == "doing"
              and any(r["target"] == askmod.HUMAN and r["state"] == "queued" for r in db.task_requests(con, tid)), msg)
    finally:
        con.close()


def test_requests_parent_id_is_indexed(home: Path) -> None:
    """T92: the replied EXISTS looks a request up by parent_id once per row on
    the page; without an index that is a table scan each."""
    home.mkdir(parents=True, exist_ok=True)
    con = db.connect(home / "idx.db")
    try:
        plan = " ".join(str(tuple(r)) for r in con.execute(
            "EXPLAIN QUERY PLAN SELECT 1 FROM requests WHERE parent_id = 1"))
        check("index: looking a request up by parent_id uses an index",
              "requests_parent" in plan, plan)
    finally:
        con.close()


def test_connect_to_a_migrated_database_takes_no_write_lock(home: Path) -> None:
    """The UI connects every 2s; a connect whose migration finds nothing to do must not wait
    for the write lock another process holds (it died on 'database is locked')."""
    import sqlite3
    home.mkdir(parents=True, exist_ok=True)
    path = home / "nolock.db"
    db.connect(path).close()
    writer = sqlite3.connect(path, isolation_level=None)
    writer.execute("BEGIN IMMEDIATE")
    real = sqlite3.connect
    sqlite3.connect = lambda p, timeout=10.0, **k: real(p, timeout=0.2, **k)
    try:
        try:
            db.connect(path).close()
            got = "connected"
        except sqlite3.OperationalError as e:
            got = str(e)
    finally:
        sqlite3.connect = real
        writer.execute("ROLLBACK")
        writer.close()
    check("connect: a held write lock does not stop a connect with nothing to migrate",
          got == "connected", got)
    # Reading first must not stop the fixup running when a row needs it.
    con = db.connect(path)
    try:
        con.execute("INSERT INTO requests(ts, requester, target, kind, state, landed_at, body) "
                    "VALUES(1, 'a', 'b', 'say', 'queued', 5, 'x')")
        con.close()
        con = support.upgrade(path)
        row = con.execute("SELECT state FROM requests").fetchone()
        check("connect: a queued row that had landed is still moved to landed", row[0] == "landed", tuple(row))
    finally:
        con.close()


def test_stop_wait_reason_reads_the_task_boards_own_wait(home: Path) -> None:
    """`stopgate.stop_wait_reason`, checked branch by branch."""
    _board_world(home)
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
""")
    con = db.connect()
    try:
        check("stop_wait_reason: no current task at all is None",
              stopgate.stop_wait_reason(con, "worker") is None)

        tid = db.new_task(con, "build the thing", "worker", "manager")
        db.set_current_task(con, "worker", tid)
        reason = stopgate.stop_wait_reason(con, "worker")
        check("stop_wait_reason: current task, nothing recorded, refuses the stop",
              reason is not None and f"T{tid}" in reason, reason)

        # An open child task is a recorded wait; done, it is not.
        kid = db.new_task(con, "sub part", "worker", "manager", parent_task=tid)
        check("stop_wait_reason: an open child task is a recorded wait",
              stopgate.stop_wait_reason(con, "worker") is None)
        db.mark_task_done(con, kid, "done")
        check("stop_wait_reason: a done child is no longer a wait",
              stopgate.stop_wait_reason(con, "worker") is not None)

        # A --blocked-by task is a recorded wait; removed, it is not.
        blocker = db.new_task(con, "blocker task", "worker", "manager")
        db.add_block(con, tid, blocker)
        check("stop_wait_reason: a --blocked-by task is a recorded wait",
              stopgate.stop_wait_reason(con, "worker") is None)
        db.remove_block(con, tid, blocker)
        check("stop_wait_reason: removing the block restores the refusal",
              stopgate.stop_wait_reason(con, "worker") is not None)

        # An open decision task to human is a recorded wait - answered, it
        # is not.
        ok, msg, dec = actions.task_new("worker", askmod.HUMAN, "founder decision needed", "founder decision needed")
        check("setup: a decision task", ok, msg)
        dec_rid = db.task_requests(con, dec)[0]["id"]
        check("stop_wait_reason: an open decision task to human is a recorded wait",
              stopgate.stop_wait_reason(con, "worker") is None)
        db.close_if_queued(con, dec_rid, "done", "approved")
        check("stop_wait_reason: once the founder answers, it is no longer a wait "
              "(not task_state's OPEN_STATES, which counts 'done' as open)",
              stopgate.stop_wait_reason(con, "worker") is not None)

        # An open say of the agent's own is never a wait (T401 item 18: a
        # message needs no answer, so it cannot gate the stop).
        ok, msg, _ = askmod.say("manager", "quick one?", requester="worker")
        check("setup: a say", ok, msg)
        check("stop_wait_reason: an open say is not a recorded wait",
              stopgate.stop_wait_reason(con, "worker") is not None)
    finally:
        con.close()


def test_siblings_give_each_other_work_and_their_manager_hears_only_its_own(home: Path) -> None:
    """The founder's rule: the manager is not the team's message hub. Siblings passing work tell it nothing;
    it hears of a task it only passed along when the result is a problem."""
    use_home(home)
    hand_edit_team("agents:\n  shop: {}\n  shop.a: {}\n  shop.b: {}\n  other: {}\n  other.c: {}\n")
    for n in ("shop", "shop.a", "shop.b", "other", "other.c"):
        agents.register(n)
    con = db.connect()
    try:
        ok, msg, tid = actions.task_new("shop.a", "shop.b", "review it", "review the branch")
        check("sibling: a report opens a task for its sibling", ok, msg)
        ok, msg = actions.task_handoff(con, "shop.b", tid, "shop.a", "reviewed, yours again")
        check("sibling: and the sibling hands it back", ok, msg)
        check("sibling: the manager is told nothing of either move", db.pending_notices(con, "shop") == [],
              str([r["body"] for r in db.pending_notices(con, "shop")]))
        ok, msg, _ = actions.task_new("shop.a", "other.c", "x", "work for another team")
        check("sibling: an agent of another team is still refused a task", not ok, msg)
        ok, msg = actions.task_handoff(con, "shop.a", tid, "other.c", "yours")
        check("sibling: and a handoff to it", not ok and db.get_task(con, tid)["owner"] == "shop.a", msg)
        ok, msg = actions.task_handoff(con, "shop.a", tid, "shop", "back to you")
        ok2, msg2 = actions.task_handoff(con, "shop", tid, "shop.b", "finish it")
        check("sibling: the manager passes it on", ok and ok2, msg + msg2)

        def told(to: str) -> list[str]:
            return [r["body"] for r in con.execute(
                "SELECT body FROM requests WHERE target = ? AND requester = 'task-board' AND body LIKE ?",
                (to, f"T{tid} done by%"))]
        ok, msg = actions.mark_task_done(con, tid, "shop.b", reason="shipped it")
        check("done: the opener is told", ok and told("shop.a") == [f"T{tid} done by shop.b: shipped it"],
              f"{msg} {told('shop.a')}")
        check("done: the manager that only passed it along is not", told("shop") == [], str(told("shop")))
        ok, msg = actions.reopen_task(con, tid, "shop.a", "again")
        ok2, msg2 = actions.mark_task_done(con, tid, "shop.b", reason="FAIL: the build is red")
        check("done: a result that is a problem reaches it", ok and ok2 and told("shop")[-1:]
              == [f"T{tid} done by shop.b: FAIL: the build is red"], f"{msg} {msg2} {told('shop')}")
    finally:
        con.close()


def test_a_task_has_a_reporter_apart_from_its_owner(home: Path) -> None:
    """The reporter is who a task answers to - told when it ends, and may edit it. Its owner may be changed
    by its opener, its reporter, the owner's manager or the founder, not by anyone else."""
    use_home(home)
    hand_edit_team("agents:\n  shop: {}\n  shop.a: {}\n  shop.b: {}\n  other: {}\n  other.c: {}\n"
                   "edges:\n  - { from: \"*\", to: \"*\", relation: peer, response: fire_and_forget }\n")
    for n in ("shop", "shop.a", "shop.b", "other", "other.c"):
        agents.register(n)
    con = db.connect()
    try:
        ok, msg, plain = actions.task_new("shop.a", "shop.b", "plain", "no reporter given")
        row = db.get_task(con, plain)
        check("reporter: none given, it is the opener", ok and row["reporter"] is None and db.task_reporter(row) == "shop.a", msg)
        ok, msg, tid = actions.task_new("other.c", "shop.b", "for other", "work", reporter="other")
        row = db.get_task(con, tid)
        check("reporter: given, it is kept apart from the opener",
              ok and row["opened_by"] == "other.c" and db.task_reporter(row) == "other", msg)
        check("reporter: it is who the task answers to", actions.task_asker(con, row) == "other")
        ok, msg, _ = actions.task_new("shop.a", "shop.b", "x", "x", reporter="nobody")
        check("reporter: an unknown one is refused", not ok and "nobody" in msg, msg)
        ok, msg = actions.edit_task(con, tid, "other", title="for other, renamed")
        check("reporter: may edit the task", ok, msg)
        ok, msg = actions.edit_task(con, tid, "shop.a", owner="shop.a")
        check("owner: someone else's agent may not take it", not ok and "manager" in msg, msg)
        ok, msg = actions.edit_task(con, tid, "shop", owner="shop.a")
        check("owner: the owner's manager may give it to another agent", ok and db.get_task(con, tid)["owner"] == "shop.a", msg)
        ok, msg = actions.edit_task(con, tid, "shop", reporter="shop")
        check("reporter: the manager may not change the reporter", not ok, msg)
        ok, msg = actions.edit_task(con, tid, "shop", owner="shop.b", title="both")
        check("owner: nor change more than the owner", not ok, msg)
        ok, msg = actions.edit_task(con, tid, "other.c", reporter="other.c")
        check("reporter: the opener may change it", ok and db.task_reporter(db.get_task(con, tid)) == "other.c", msg)
        ok, msg = actions.edit_task(con, tid, "other", owner="shop.b")
        check("reporter: and the old reporter may no longer edit", not ok, msg)
        ok, msg = actions.edit_task(con, tid, actions.HUMAN, reporter="other")
        check("reporter: the founder may change it", ok, msg)
        ok, msg = actions.mark_task_done(con, tid, "shop.a", reason="did it")
        told = [r["target"] for r in con.execute("SELECT target FROM requests WHERE body LIKE ?", (f"T{tid} done by%",))]
        check("done: the reporter is told, not the opener", ok and told == ["other"], f"{msg} {told}")
    finally:
        con.close()


def test_a_reported_task_reads_as_awaiting_who_it_answers_to() -> None:
    from handline.cli import parser as cli_parser, task as clitask
    t = {"id": 7, "title": "fix it", "state": "reported", "from": "a.one", "to": "b.two", "asker": "c", "priority": 2, "parent": None,
         "created": None, "time": None, "evidence": None, "body": "", "memos": [],
         "report": {"by": "b.two", "evidence": "built", "at": "2026-10-02T11:30:00Z"}}
    lines = clitask._detail_lines(t, 0)
    check("task show: a reported task names whom it awaits, and its report",
          lines[1].startswith("state reported, awaiting c   assignee b.two") and lines[2].startswith("reported ") and lines[2].endswith(": built"),
          str(lines))
    check("task show: an older daemon's Task without an asker awaits its opener",
          clitask._state({**t, "asker": None}) == "reported, awaiting a.one")
    box = {"length_s": 7200, "default": False, "start": None, "end": None, "overdue_since": None, "told": 0, "left_s": 4800}
    held = {**t, "state": "doing", "time": box, "blockers": [{"task": 9, "owner": "c", "title": "first"}],
            "subtasks": [{"id": 8, "owner": "d", "state": "doing", "title": "child"}, {"id": 6, "owner": "d", "state": "done", "title": "old"}]}
    check("task show: a paused clock names the blocker and what is left",
          clitask._detail_lines(held, 0)[2:4] == ["timeout 2h00m", "paused - blocked by T9, 1h20m left"], str(clitask._detail_lines(held, 0)[2:4]))
    check("task show: with no blocker it names the open children, and with neither only what is left",
          clitask._detail_lines({**held, "blockers": []}, 0)[3] == "paused - waiting on T8, 1h20m left"
          and clitask._detail_lines({**held, "blockers": [], "subtasks": []}, 0)[3] == "paused, 1h20m left")
    p = cli_parser.build_parser()
    check("task new parses without -t", p.parse_args(["task", "new", "--assign", "b.two", "x"]).timeout is None)
    check("task accept and reject parse, reject with its note",
          p.parse_args(["task", "accept", "T7"]).task == 7 and p.parse_args(["task", "reject", "T7", "no tests"]).note == "no tests")


def main() -> int:
    def tests(td):
        home = Path(td) / "orcapipe"
        home.mkdir()
        run(lambda: test_the_quota_request_never_follows_a_redirect(Path(td) / 'zairedir'))
        run(lambda: test_db_dedup(home))
        run(lambda: test_ask_flow(Path(td) / 'askhome'))
        run(lambda: test_siblings_give_each_other_work_and_their_manager_hears_only_its_own(Path(td) / 'siblings'))
        run(lambda: test_a_task_has_a_reporter_apart_from_its_owner(Path(td) / 'reporter'))
        run(lambda: test_one_request_at_a_time(Path(td) / 'onetask'))
        run(lambda: test_a_landed_task_delivery_is_the_task_the_agent_holds(Path(td) / 'landedtask'))
        run(lambda: test_a_head_told_before_a_session_closed_is_told_again_when_it_runs_again(Path(td) / 'resume'))
        run(lambda: test_any_request_that_landed_is_landed(Path(td) / 'landedany'))
        run(lambda: test_a_landed_task_can_be_handed_on_discarded_and_reopened(Path(td) / 'landedmove'))
        run(lambda: test_own_withdraw_is_not_an_answer(Path(td) / 'ownwithdraw'))
        run(lambda: test_migration_marks_only_each_targets_head_delivered(Path(td) / 'oldmig'))
        run(lambda: test_retire_if_undelivered_is_the_gate_against_the_delivery_race(
            Path(td) / 'retirerace'))
        run(lambda: test_asks_have_no_hourly_cap(Path(td) / 'ratehome'))
        run(lambda: test_a_runaway_is_stopped_and_raised_to_the_founder(Path(td) / 'cyc'))
        run(lambda: test_review_pingpong(Path(td) / 'pingpong'))
        run(lambda: test_a_resolved_ask_does_not_stay_in_progress_across_a_park(Path(td) / 'foldparkhome'))
        run(lambda: test_reconcile_resolved_needs_an_explicit_reference_not_a_bare_number(Path(td) / 'reconid'))
        run(lambda: test_reconcile_resolved_only_state_done_counts(Path(td) / 'reconstate'))
        run(lambda: test_fold_degraded_path_also_reconciles_resolved_asks(Path(td) / 'reconderaded'))
        run(lambda: test_agents_stays_a_leaf_no_cycle_through_collect_or_vendors())
        run(lambda: test_a_reply_to_a_peers_ask_needs_no_edge_back(Path(td) / 'replynoedge'))
        run(lambda: test_ask_respects_terminal_state(Path(td) / 'term'))
        run(lambda: test_the_config_dir_and_its_layout_are_the_drivers_answers(
            Path(td) / 'vendorcfg'))
        run(lambda: test_db_dedup_only_swallows_dedup(Path(td) / 'strict'))
        run(lambda: test_blocked_agents_come_due_in_one_request(Path(td) / 'blk4'))
        run(lambda: test_a_trust_screen_is_one_founder_request(Path(td) / 'trustonce'))
        run(lambda: test_a_forgotten_message_or_a_task_behind_one_is_still_nudged(Path(td) / 'forgottenask'))
        run(lambda: test_an_idle_agent_delivers_past_a_head_whose_task_is_blocked(Path(td) / 'blockednext'))
        run(lambda: test_head_task_blocked_by_the_agents_own_linked_ask(Path(td) / 'blockedask'))
        run(lambda: test_a_blocked_head_is_not_shown_as_a_pending_reminder(Path(td) / 'blockedreminder'))
        run(lambda: test_a_closed_agents_wake_reads_the_blocked_head_through_effective_head(
            Path(td) / 'blockedwake'))
        run(lambda: test_a_withdraw_landing_before_the_send_stops_the_send(Path(td) / 'withdrawrace'))
        run(lambda: test_a_withdraw_landing_before_the_send_stops_the_send(Path(td) / 'withdrawrace'))
        run(lambda: test_withdraw_too_late_points_at_a_message_and_a_task_release(Path(td) / 'withdrawtoolate'))
        run(lambda: test_a_request_is_answered_by_a_reply_that_names_it(Path(td) / 'reportnotice'))
        run(lambda: test_spawn_confirms_the_task_landed(Path(td) / 'spawnland'))
        run(lambda: test_a_cold_wake_with_no_handoff_still_tells_the_agent_its_transcript_and_request(Path(td) / 'coldnone2'))
        run(lambda: test_an_answer_reaches_the_agent_that_asked(Path(td) / 'answered'))
        run(lambda: test_t80_a_reply_says_which_request_it_answers(Path(td) / 't80'))
        run(lambda: test_an_answer_line_says_what_it_answers(Path(td) / 'ansline'))
        run(lambda: test_a_request_can_be_read_whole(Path(td) / 'readone'))
        run(lambda: test_an_agent_may_not_list_another_agents_messages(Path(td) / 'nopeek'))
        run(lambda: test_inbox_and_withdraw_cli(Path(td) / 'inboxcli'))
        run(lambda: test_only_one_thing_types_at_an_agent_at_a_time(Path(td) / 'locking'))
        run(lambda: test_growing_the_team_needs_a_person(Path(td) / 'hr'))
        run(lambda: test_the_founder_does_not_approve_their_own_request(Path(td) / 'hrself'))
        run(lambda: test_classify_reads_the_screen_again_after_waiting_of_hides_a_wait_by_it(
            Path(td) / 'classifyreuse'))
        run(lambda: test_a_stale_cached_read_does_not_hide_a_later_prompt(Path(td) / 'stalecache'))
        run(lambda: test_a_proposal_and_an_decisionion_wait_on_the_founder_too(Path(td) / 'founderwaits'))
        run(lambda: test_a_wait_with_no_answer_says_so_and_fails(Path(td) / 'waitrow'))
        run(lambda: test_an_answer_to_the_human_is_in_the_human_mailbox(Path(td) / 'mailbox'))
        run(lambda: test_the_founder_can_cancel_a_request_in_an_agents_queue(Path(td) / 'cancel'))
        run(lambda: test_a_queued_agent_with_a_told_head_gets_the_same_forgotten_resolve_nudge_as_idle(
            Path(td) / 'queuednudge'))
        run(lambda: test_handoff_rate_limited(Path(td) / 'hrate'))
        run(lambda: test_an_agent_cannot_make_itself_a_done_target_of_a_task_it_does_not_hold(Path(td) / 'doneT'))
        run(lambda: test_a_refused_ask_is_never_announced_as_an_answer(Path(td) / 'noannounce'))
        run(lambda: test_a_refusal_is_not_read_back_as_an_answer(Path(td) / 'readref'))
        run(lambda: test_a_withdrawn_or_replaced_request_is_not_read_back_as_an_answer(Path(td) / 'readwdrn'))
        run(lambda: test_the_done_close_migration_upgrades_an_old_tasks_table(Path(td) / 'taskmigrate'))
        run(lambda: test_a_dismissal_is_a_state_and_never_reads_as_words(Path(td) / 'dismissstate'))
        run(lambda: test_dismissing_an_agents_own_answered_request_keeps_its_verdict(Path(td) / 'dismissverdict'))
        run(lambda: test_dismissing_your_own_answer_does_not_announce_it_for_the_requester(Path(td) / 'dismissownanswer'))
        run(lambda: test_a_task_is_done_only_once_its_children_and_blockers_are(Path(td) / 'doneonce'))
        run(lambda: test_a_say_to_the_founder_asks_for_no_reply(Path(td) / 'sayhuman'))
        run(lambda: test_task_rows_reads_the_board_in_a_fixed_number_of_queries_and_agrees_with_the_per_task_path(Path(td) / 'boardrows'))
        run(lambda: test_the_per_task_event_lookups_use_the_index_not_a_scan(Path(td) / 'evindex'))
        run(lambda: test_an_answer_is_not_an_ask_for_the_cycle_check(Path(td) / 'answerrate'))
        run(lambda: test_asks_reports_and_decisions_become_says_and_queued_ones_become_tasks(Path(td) / 'kindsay'))
        run(lambda: test_an_decisionion_is_a_decision_task_and_the_gate_is_the_open_task(Path(td) / 'escgate'))
        run(lambda: test_an_answer_is_a_message_of_its_own_with_its_own_number(Path(td) / 'answerrow'))
        run(lambda: test_the_answer_lookups_use_the_settles_index_not_a_scan(Path(td) / 'settlesidx'))
        run(lambda: test_decide_records_the_founders_decision_and_closes_the_task(Path(td) / 'decide'))
        run(lambda: test_advance_current_task_never_touches_the_founder(Path(td) / 'nexttaskhuman'))
        run(lambda: test_task_memos_anyone_adds_author_or_founder_changes(Path(td) / 'memos'))
        run(lambda: test_a_memo_change_is_told_to_the_holder_only_while_doing(Path(td) / 'memotold'))
        run(lambda: test_a_task_shows_its_memos_when_delivered_and_started_and_old_notes_became_memos(Path(td) / 'memoshow'))
        run(lambda: test_one_doing_task_per_agent(Path(td) / 'onedoing'))
        run(lambda: test_one_doing_migration(Path(td) / 'onedoingmig'))
        run(lambda: test_reopening_a_timed_task_resumes_its_clock_only_if_still_doing(
            Path(td) / 'taskreopentimeout'))
        run(lambda: test_a_task_is_a_draft_until_queued_and_delivered_when_its_turn_comes(Path(td) / 'taskstates'))
        run(lambda: test_the_open_task_count_is_queued_plus_doing(Path(td) / 'taskcount'))
        run(lambda: test_an_answer_that_does_not_close_a_queued_task_does_not_send_it_again(Path(td) / 'resend'))
        run(lambda: test_a_reopened_or_unblocked_task_goes_out_again_after_an_answer(Path(td) / 'resend2'))
        run(lambda: test_a_task_knows_when_its_owner_had_it(Path(td) / 'delivered'))
        run(lambda: test_the_agent_handing_a_task_on_is_not_parked(Path(td) / 'handoffpark'))
        run(lambda: test_a_review_is_an_ordinary_child_the_owner_waits_on(Path(td) / 'reviewkid'))
        run(lambda: test_a_begun_task_reads_doing_the_same_way_everywhere(Path(td) / 'begundoing'))
        run(lambda: test_releasing_a_task_the_founder_holds_returns_it_to_draft_not_to_the_bin(Path(td) / 'relhuman'))
        run(lambda: test_merge_refusals_leave_everything_as_it_was(Path(td) / 'taskmergeref'))
        run(lambda: test_task_screen_logic_sorts_flags_idle_and_batches_with_reasons(Path(td) / 'taskscreenlogic'))
        run(lambda: test_a_merge_says_when_it_drops_the_kept_tasks_wait_on_a_folded_one(Path(td) / 'mergewait'))
        run(lambda: test_requests_parent_id_is_indexed(Path(td) / 'parentidx'))
        run(lambda: test_stop_wait_reason_reads_the_task_boards_own_wait(Path(td) / 'stopwait'))
        run(lambda: test_closed_tasks_migrate_to_done_and_merged_ones_to_discarded(Path(td) / 't568mig'))
        run(lambda: test_the_priority_columns_migrate_an_old_tasks_table(Path(td) / 'taskprimig'))
        run(lambda: test_advance_records_and_skips_a_refused_delivery(
            Path(td) / 'advanceskip'))
        run(lambda: test_ocp_close_ends_a_session_and_never_a_task(Path(td) / 'taskclose'))
        run(lambda: test_replace_withdraw_and_dismiss_store_the_reason_typed(Path(td) / 'taskreason'))
        run(lambda: test_bin_ocp_resolves_a_symlink(Path(td) / 'symlinkhome'))
        run(lambda: test_bin_hl_runs_go_hl_only_while_it_is_built_from_this_go_source(Path(td) / 'gohlstamp'))
        run(lambda: test_connect_to_a_migrated_database_takes_no_write_lock(Path(td) / 'nolock'))
        run(lambda: test_a_reported_task_reads_as_awaiting_who_it_answers_to())
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
