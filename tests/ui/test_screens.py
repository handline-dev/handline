"""Tests: screen data, key actions and textsafe.

Run on its own: python3 tests/ui/test_screens.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403




def test_dump_refuses_every_class_of_character_a_terminal_would_obey() -> None:
    """dump()'s control-character guard once checked only ord(c) < 32 or 127 -
    C0 and DEL - so a C1 byte (0x9b is CSI, one byte instead of two) reached
    agent.yaml unquoted and round-tripped: dump({'name': 'a\\u009bb'}) wrote
    `name: a\\x9bb` bare, no error, no quoting. The guard is now
    textsafe.CONTROL_CHARS (C0, DEL, C1 - the set the module's own docstring
    derives from what a terminal obeys instead of draws) plus tab and
    newline, which that set leaves out for a line-oriented caller to keep
    but a one-line config scalar has no use for. Each class is checked on
    its own: a guard rewritten to add C1 is exactly where C0 could quietly
    stop being covered, and only a class-by-class check would catch that."""
    classes = {
        "C0": "\x01",
        "DEL": "\x7f",
        "C1 (CSI, 0x9b)": "\u009b",
        "tab": "\t",
        "newline": "\n",
    }
    for label, ch in classes.items():
        try:
            yamlcompat.dump({"k": f"a{ch}b"})
            check(f"dump: refuses {label}", False, "wrote instead of raising")
        except yamlcompat.ConfigError as e:
            # Two guards in dump() can raise ConfigError on this line: the
            # control-character clause this test exists to pin, and the
            # round-trip self-check below it, which also refuses a bare
            # newline (it splits a line). Asserting only the type let that
            # second guard stand in for the first - deleting the clause left
            # this check green. The message names which one fired.
            check(f"dump: refuses {label}",
                  "does not write control characters" in str(e), str(e))
    # The class the guard already had, not only the one just added: proves
    # the rewrite did not narrow C0's own range (0x00-0x08, 0x0b-0x1f).
    #
    # auditor's #1580: asserting only the exception type here has the same
    # gap as the newline case above, for six codepoints out of this range -
    # 0x0b, 0x0c, 0x0d, 0x1c-0x1e are splitlines() separators, so the
    # round-trip self-check refuses them on its own too, and the "control
    # clause deleted" mutation left all six green. The message assertion is
    # the same fix, applied to every codepoint here, not only the one that
    # first showed it.
    for c in list(range(0x00, 0x09)) + list(range(0x0b, 0x20)):
        try:
            yamlcompat.dump({"k": f"a{chr(c)}b"})
            check(f"dump: refuses C0 {hex(c)}", False, "wrote instead of raising")
        except yamlcompat.ConfigError as e:
            check(f"dump: refuses C0 {hex(c)}",
                  "does not write control characters" in str(e), str(e))
    # And every C1 codepoint, not just CSI. Same gap, same fix: 0x85 (NEL) is
    # also a splitlines() separator.
    for c in range(0x7f, 0xa0):
        try:
            yamlcompat.dump({"k": f"a{chr(c)}b"})
            check(f"dump: refuses C1/DEL {hex(c)}", False, "wrote instead of raising")
        except yamlcompat.ConfigError as e:
            check(f"dump: refuses C1/DEL {hex(c)}",
                  "does not write control characters" in str(e), str(e))
    check("dump: still writes an ordinary character",
          "k: ab\n" == yamlcompat.dump({"k": "ab"}))


def test_the_panel_says_the_true_thing_about_a_queue(home: Path) -> None:
    """Only the head of a queue is told; what waits behind it is waiting its
    turn, not failing to be told."""
    con = _blocked_world(home)
    orcamod.live_terminals = lambda: {"term_x"}
    for _ in range(2):
        db.add_request(con, "marina", "backend", "and this", None, "peer", "async", None, 0, None)
    now = int(time.time())
    con.execute("UPDATE requests SET ts = ?", (now - actions.STUCK_AFTER_S - 60,))
    # The head has been told and has not answered; the two behind it were never told.
    head = db.head_of_queue(con, "backend")
    con.execute("INSERT OR REPLACE INTO cursors(name, row_id, updated) VALUES(?,?,?)",
                ("notify:backend", int(head["id"]), now))
    try:
        rows = [r for r in actions.stuck(con) if r["agent"] == "backend"]
        why = {r["what"].split()[0]: r["why"] for r in rows}
        check("panel: the head is told and has not answered", "was told" in why.get(f"#{head['id']}", ""), str(rows))
        behind = [r for r in rows if r["what"].split()[0] != f"#{head['id']}"]
        check("panel: what is behind it says so, and names the head",
              behind and all(r["why"] == f"waiting behind #{head['id']}" for r in behind), str(rows))
        check("panel: nothing says 'has not been told yet' of a request that is not the head",
              not any("has not been told yet" in r["why"] for r in behind), str(rows))
    finally:
        con.close()


def test_a_report_to_the_founder_is_not_a_wait(home: Path) -> None:
    """Red means the founder must act. `--fyi` is a report: listed, not red, not
    counted, closed once opened or when the sender's next report lands - and a
    correction retires a stale escalation even after the bridge showed it."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        def esc():
            return [w for w in actions.founder_waits(con) if w["kind"] == "escalation"]

        ok, _m, q = askmod.ask(actions.HUMAN, "merge keeper-3?", requester="backend")
        ok1, _m, f1 = askmod.ask(actions.HUMAN, "status: half done", requester="backend",
                                 fyi=True)
        check("fyi: accepted", ok and ok1)
        check("fyi: only the question is waiting on the founder, in red",
              [w["what"].startswith(f"#{q}") for w in esc()] == [True], str(esc()))
        row = next(m for m in actions.messages(con) if m["id"] == f1)
        check("fyi: it is in the mailbox, not a gate", not row["gate"], str(row))

        ok, _m, f2 = askmod.ask(actions.HUMAN, "status: done", requester="backend", fyi=True)
        check("fyi: the sender's next report closes the earlier one",
              db.get_request(con, f1)["state"] == "replaced"
              and db.get_request(con, f2)["state"] == "queued")
        ok, _m, other = askmod.ask(actions.HUMAN, "other's report", requester="frontend",
                                   fyi=True)
        check("fyi: another sender's report is left alone",
              db.get_request(con, f2)["state"] == "queued")

        real_who = askmod.asking_agent
        try:
            askmod.asking_agent = lambda: "backend"
            _capture(lambda: cliask.cmd_read(argparse.Namespace(id=f2, wait=False)))
            check("fyi: an agent reading it does not close it",
                  db.get_request(con, f2)["state"] == "queued")
            askmod.asking_agent = lambda: actions.HUMAN
            _capture(lambda: cliask.cmd_read(argparse.Namespace(id=f2, wait=False)))
            check("fyi: the founder's `ocp read` closes it",
                  db.get_request(con, f2)["state"] == "done")
            check("fyi: `ocp read` closing it leaves no verdict either",
                  db.get_request(con, f2)["verdict"] is None)
        finally:
            askmod.asking_agent = real_who
        _set_request_state(con, f2, "queued")
        actions.opened_by_human(con, f2)
        row = db.get_request(con, f2)
        check("fyi: opening it closes it", row["state"] == "done")
        check("fyi: opening it leaves no verdict", row["verdict"] is None, row["verdict"])
        check("fyi: opening it does not read back to the sender",
              f2 not in [r["id"] for r in db.unannounced_answers(con, "backend")])
        chat, _ = actions.conversation_and_drawn(con, "backend")
        msgs = [r for r in chat if getattr(r, "kind", None) == "msg"]
        check("fyi: its verdict is never drawn in the sender's own conversation",
              not any(r.rid == f2 and r.part == 1 for r in msgs), str(chat))
        check("fyi: 'read' does not appear as something said to the sender",
              not any(r.text == "read" for r in msgs), str(chat))
        actions.opened_by_human(con, q)
        check("fyi: opening a question does not answer it",
              db.get_request(con, q)["state"] == "queued")
        ok, msg, _ = askmod.ask("frontend", "x", requester="backend", fyi=True)
        check("fyi: only the founder gets one", not ok, msg)

        # A resolve reply to something the founder asked clears the same way.
        asked = db.add_request(con, actions.HUMAN, "backend", "is it done?", None, "peer",
                               "async", None, 0, None)
        askmod.resolve(asked, "yes, merged")
        check("answer: listed until read",
              asked in [r["id"] for r in db.unannounced_answers(con, actions.HUMAN)])
        actions.opened_by_human(con, asked)
        check("answer: reading it clears it, no dismiss needed",
              asked not in [r["id"] for r in db.unannounced_answers(con, actions.HUMAN)])
        check("answer: reading it leaves the verdict alone",
              db.get_request(con, asked)["verdict"] == "yes, merged")

        # A correction reaches an escalation the bridge already showed.
        db.mark_delivered(con, q)
        ok, msg, new = askmod.ask(actions.HUMAN, "merge keeper-3 - now with tests",
                                  requester="backend", replaces=q)
        check("replaces: an escalation already shown to the founder is retired",
              ok and db.get_request(con, q)["state"] == "replaced", msg)
        check("replaces: and only the replacement waits", [w["what"].startswith(f"#{new}")
              for w in esc()] == [True], str(esc()))
        # An agent's delivered request still cannot be replaced.
        rid = db.add_request(con, "backend", "frontend", "do it", None, "peer",
                             "async", None, 0, None)
        db.mark_delivered(con, rid)
        ok, msg, _ = askmod.ask("frontend", "do it better", requester="backend", replaces=rid)
        check("replaces: an agent's delivered request still cannot be", not ok, msg)
    finally:
        con.close()


def test_for_you_folds_to_one_line_per_agent(home: Path) -> None:
    """Founder #1458/#1460: FOR YOU reads like a chat app - one line per agent,
    not one per message. A gate and a row parked waiting on a reply are each a
    decision or an obligation on the human, so both keep their own line even
    when the same agent has said something newer; everything else from one
    agent folds to its latest, with a count of what it stands in for."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        def by(agent: str) -> dict:
            return next(m for m in actions.messages(con) if m["from"] == agent)

        # Several reports from one agent: one line, the latest, the right count.
        # Built the way production makes them - fire_and_forget, fyi - and left
        # all three queued at once (`ask()`'s one-live-report replace is its
        # own mechanism, not this one, and would leave only one row to fold
        # regardless of whether folding worked). Given ts out of arrival order,
        # so "latest" is proven to mean the newest thing said, not merely the
        # last one inserted.
        base = int(time.time())
        for body, ts in (("almost there", base), ("half done", base - 20),
                         ("still going", base - 10)):
            rid = db.add_request(con, "backend", actions.HUMAN, body, None, "peer",
                                 "fire_and_forget", "report", 0, None, fyi=True)
            con.execute("UPDATE requests SET ts=? WHERE id=?", (ts, rid))
        con.commit()
        rows = [m for m in actions.messages(con) if m["from"] == "backend"]
        check("fold: several reports from one agent become one line",
              len(rows) == 1, str(rows))
        check("fold: it carries the latest body", rows[0]["body"] == "almost there",
              rows[0]["body"])
        check("fold: the count is all three, not one",
              rows[0]["unread"] == 3, str(rows[0]))

        # Two rows landing in the same second: the id, not the clock, says
        # which one is shown. `mailbox_rows` lists every queued ask before
        # every answer regardless of when each was created, so an answer
        # older than a same-second report would still win a plain `ts` sort
        # by loop order alone - id breaks that tie by creation order instead.
        tie_ts = int(time.time())
        answered = db.add_request(con, actions.HUMAN, "punctual", "?", None, "peer",
                                  "async", None, 0, None)
        con.execute("UPDATE requests SET state='done', verdict='the answer', ts=?, "
                    "resolved_at=? WHERE id=?", (tie_ts, tie_ts, answered))
        newer_report = db.add_request(con, "punctual", actions.HUMAN, "the report", None,
                                      "peer", "fire_and_forget", "report", 0, None, fyi=True)
        con.execute("UPDATE requests SET ts=? WHERE id=?", (tie_ts, newer_report))
        con.commit()
        check("fold: a same-second tie shows the row created after",
              by("punctual")["body"] == "the report", str(by("punctual")))

        # An agent with several unannounced answers folds the same way, and the
        # count there is real: nothing here retires an old answer for a new one.
        for body in ("first answer", "second answer", "third answer"):
            rid = db.add_request(con, actions.HUMAN, "reviewer", "?", None, "peer",
                                 "async", None, 0, None)
            con.execute("UPDATE requests SET state='done', verdict=? WHERE id=?",
                       (body, rid))
        con.commit()
        rev = by("reviewer")
        check("fold: several answers from one agent become one line too",
              len([m for m in actions.messages(con) if m["from"] == "reviewer"]) == 1)
        check("fold: carrying the latest answer", rev["body"] == "third answer", rev["body"])
        check("fold: and counting all three", rev["unread"] == 3, str(rev))

        # A gate never folds, even when the same agent has said something newer.
        # Built via ask.escalate() - the production shape, fire_and_forget with
        # creates_gate=True, so `waiting` is false on it and `gate` is the only
        # thing pinning it. A hand-made async row would pin on `waiting` alone
        # and leave `gate` untested: dropping it from messages() stayed green
        # against that shape (auditor's #1500).
        askmod.escalate(con, "backend", "which branch do we cut?", "test escalation")
        ok, _m, r4 = askmod.ask(actions.HUMAN, "even later", requester="backend", fyi=True)
        backend_rows = [m for m in actions.messages(con) if m["from"] == "backend"]
        check("fold: the gate keeps its own line beside the folded report",
              len(backend_rows) == 2, str(backend_rows))
        check("fold: the gate is not folded away", any(m["gate"] for m in backend_rows),
              str(backend_rows))
        check("fold: the report line still carries its own latest",
              any(not m["gate"] and m["body"] == "even later" for m in backend_rows),
              str(backend_rows))

        # A row parked waiting on a reply keeps its own line the same way.
        wid = db.add_request(con, "frontend", actions.HUMAN, "merge keeper-3?", None,
                             "peer", "async", None, 0, None)
        ok, _m, f1 = askmod.ask(actions.HUMAN, "meanwhile: status", requester="frontend",
                                fyi=True)
        front_rows = [m for m in actions.messages(con) if m["from"] == "frontend"]
        check("fold: a waiting-for-reply row keeps its own line",
              len(front_rows) == 2 and any(m["waiting"] for m in front_rows), str(front_rows))

        # The fold does not reorder agents that were not folded against each other.
        before = [m["from"] for m in actions.messages(con)]
        ok, _m, other = askmod.ask(actions.HUMAN, "a third report", requester="other",
                                   fyi=True)
        after = [m["from"] for m in actions.messages(con) if m["from"] != "other"]
        check("fold: agents untouched by folding keep their relative order",
              before == after, f"{before} -> {after}")

        # A report is fyi by definition: nobody is waiting on it, whatever
        # response the routing table happens to give that agent's other
        # traffic. A hand-edited team.yaml carrying response=async for a human
        # edge must not silently pin every report from it and switch the fold
        # off - `fyi` is what says no reply is owed, not `response`.
        db.add_request(con, "hand-edited", actions.HUMAN, "a report", None, "peer",
                       "async", "report", 0, None, fyi=True)
        edited = by("hand-edited")
        check("fold: an async-routed report still folds - fyi outranks response",
              not edited["waiting"] and not edited["gate"], str(edited))

        # messages() backs `ocp inbox --wait` on the human's own mailbox too
        # (cli.ask._waiting_ids); a new report from an agent that already has a
        # folded row must still show up as a new id to wait for.
        before_ids = cliask._waiting_ids(con, actions.HUMAN)
        ok, _m, r5 = askmod.ask(actions.HUMAN, "one more", requester="backend", fyi=True)
        after_ids = cliask._waiting_ids(con, actions.HUMAN)
        check("fold: a new folded message is still a new id to wait for",
              after_ids - before_ids, f"{before_ids} -> {after_ids}")

        # The wait set holds every row behind a fold, not only its
        # representative - two rows from one agent, landing in the same
        # second so the fold's own tiebreak is not what saves this.
        now = int(time.time())
        m1 = db.add_request(con, "multi", actions.HUMAN, "one", None, "peer",
                            "fire_and_forget", None, 0, None)
        m2 = db.add_request(con, "multi", actions.HUMAN, "two", None, "peer",
                            "fire_and_forget", None, 0, None)
        con.execute("UPDATE requests SET ts=? WHERE id IN (?, ?)", (now, m1, m2))
        con.commit()
        multi_line = by("multi")
        check("fold: two rows from one agent are still one line",
              multi_line["unread"] == 2, str(multi_line))
        check("fold: but the wait set holds both ids behind it, not just the shown one",
              {m1, m2} <= cliask._waiting_ids(con, actions.HUMAN),
              str(cliask._waiting_ids(con, actions.HUMAN)))

        for name in ("alpha", "zeta"):
            db.add_request(con, name, actions.HUMAN, f"{name} report", None, "peer",
                           "fire_and_forget", None, 0, None)

        # A count of messages, not of the lines a fold drew them into: one
        # agent, two answers already back and three newer reports, folded to
        # a single line - but five things are waiting on the founder.
        def counts_of(title: str) -> dict[str, int]:
            if " — " not in title:
                return {}
            return {what: int(n) for n, what in
                   (part.split(" ", 1) for part in title.split(" — ", 1)[1].split(", "))}

        before_title = tui.messages_data(con)[0]
        before = counts_of(before_title)
        for body in ("first answer", "second answer"):
            rid = db.add_request(con, actions.HUMAN, "counted", "?", None, "peer",
                                 "async", None, 0, None)
            con.execute("UPDATE requests SET state='done', verdict=? WHERE id=?",
                       (body, rid))
        for body in ("r1", "r2", "r3"):
            db.add_request(con, "counted", actions.HUMAN, body, None, "peer",
                           "fire_and_forget", "report", 0, None, fyi=True)
        con.commit()
        counted_line = by("counted")
        check("fold: still one line for that agent", counted_line["unread"] == 5,
              str(counted_line))
        after_title = tui.messages_data(con)[0]
        after = counts_of(after_title)
        check("fold: the title counts every message behind the fold, not the fold's lines",
              after["came back"] - before.get("came back", 0) == 2
              and after["to read"] - before.get("to read", 0) == 3,
              f"{before_title!r} -> {after_title!r}")
        want = len(actions.mailbox_rows(con))
        os.environ["OCP_AGENT"] = actions.HUMAN
        out = _capture(lambda: climod.main(["inbox", actions.HUMAN]))
        os.environ.pop("OCP_AGENT", None)
        check("fold: `ocp inbox` counts every message in the mailbox, not every line",
              int(out.splitlines()[0].split()[0]) == want, out.splitlines()[0])
    finally:
        con.close()


def test_display_agrees_with_the_daemon(home: Path) -> None:
    """Every screen draws the figure the daemon acts on, with its provenance.

    `ocp triggers` once printed "-" in the 5h column beside a reason reading
    "pct_5h 0%", which is the dashboard arguing with the daemon.
    """
    use_home(home)
    agents.register("quietone")
    write_status(home, "alpha", pct_5h=None, pct_7d=10, ctx_pct=5)
    st = status.read("alpha")
    check("display: the account is quiet", st.short_source == "quiet")
    check("display: a quiet window is marked", status.mark("quiet") == "*")
    check("display: a live one is not", status.mark("live") == " ")
    check("display: one carried past its bound is marked apart",
          status.mark("carried_old") == "!" and status.mark("carried") == "~")

    text = _snap(120, 40)
    check("display: top draws the figure, not a question mark",
          "0%*" in text, "no marked 0% in the frame")
    check("display: and prints the legend", "inferred" in text)


def test_events_view_starts_at_the_newest(home: Path) -> None:
    """`ocp events` answers "what just happened", and must not report the page
    size as the total - it said 50 events when there were 187."""
    os.environ["OCP_HOME"] = str(home)
    con = db.connect()
    try:
        for i in range(12):
            db.add_event(con, "scout" if i % 2 else "keeper", f"k{i}", {})
        rows = db.latest_events(con, limit=5)
        check("events: the newest are returned", [r["kind"] for r in rows]
              == ["k7", "k8", "k9", "k10", "k11"], str([r["kind"] for r in rows]))
        check("events: oldest first, so they read downward",
              rows[0]["id"] < rows[-1]["id"])
        check("events: the total is the total, not the page",
              db.count_events(con) == 12, str(db.count_events(con)))
        check("events: filtered by agent", db.count_events(con, agent="scout") == 6,
              str(db.count_events(con, agent="scout")))
        rows = db.latest_events(con, agent="keeper", limit=3)
        check("events: the filter applies to the page too",
              all(r["agent"] == "keeper" for r in rows))
    finally:
        con.close()


def test_a_control_byte_is_never_accepted_as_an_identifier(home: Path) -> None:
    """auditor's #1695: sanitising the parsed worktree path and handle at the
    four messages that embedded them left `db.set_terminal` storing them raw,
    and `start_in` read one back into "started in {worktree}" - a fifth sink,
    and the stored value no longer looked like a parsed one. A path or a handle
    is stored, compared and handed back to Orca rather than drawn, so rewriting
    one names something that does not exist. It is refused where it is read,
    the same answer as one that could not be read at all.
    """
    os.environ["OCP_HOME"] = str(home)
    os.environ.pop("ORCA_TERMINAL_HANDLE", None)
    os.environ.pop("ORCA_WORKTREE_ID", None)
    home.mkdir(parents=True, exist_ok=True)
    agents.register("spawny")
    csi = "\u009b31m"
    # Both exist on disk: the reported trace is start_in getting past its own
    # "its worktree is gone" and reaching "started in {worktree}".
    bad_wt, good_wt = str(home / f"w{csi}t"), str(home / "wt")
    Path(bad_wt).mkdir()
    Path(good_wt).mkdir()

    def fake_run(args, cwd=None, wt=bad_wt, handle=f"term{csi}_s"):
        if args[:3] == ["orca", "worktree", "create"]:
            return 0, json.dumps({"result": {"worktree": {"path": wt}}}), ""
        if args[:3] == ["orca", "terminal", "create"]:
            return 0, json.dumps({"result": {"terminal": {"handle": handle}}}), ""
        return 0, "", ""

    def stored():
        row = db.get_agent_state(con, "spawny")
        return ((row["terminal"], row["worktree"]) if row else (None, None))

    real = (spawnmod._run, spawnmod.shutil.which, orcamod.run_current, orcamod.run_create,
           dmn_delivery.wait_for_session)
    con = db.connect()
    try:
        spawnmod.shutil.which = lambda _n: "/usr/bin/orca"
        orcamod.run_current = lambda: None
        orcamod.run_create = lambda objective: None
        # spawn()/start_in() confirm a session came up before saying "started"
        # (#1976, T146 pt.2) - not this test's own concern, so it is not made
        # to wait for a real process table it has nothing in.
        dmn_delivery.wait_for_session = lambda agent, timeout_s=0: True

        check("the escaped byte comes back real from the parse (the case is live)",
              "\x9b" in json.loads(fake_run(["orca", "worktree", "create"])[1])
              ["result"]["worktree"]["path"])

        # The path, refused where it is read: it reaches no message and no row.
        spawnmod._run = fake_run
        ok, msg = spawnmod.spawn("spawny", ROOT, base="main")
        check("spawn: a worktree path carrying a control is not read as a path",
              not ok and msg.startswith("could not read the new worktree path"), repr(msg))
        check("spawn: and nothing of it is stored",
              "\x9b" not in msg and stored() == (None, None), (repr(msg), stored()))

        # auditor #1729: the rule is the one `agents.register` already applies
        # to a name, so it covers the four `CONTROL_CHARS` leaves out. A
        # newline is the one that bites: the sinks' `oneline` calls are gone,
        # so a path holding one would be stored and then written into the
        # middle of start_in's line, forging a second one after it.
        for label, ch in (("a newline", "\n"), ("a tab", "\t"),
                          ("U+2028", "\u2028"), ("U+2029", "\u2029")):
            spawnmod._run = lambda args, cwd=None, c=ch: fake_run(
                args, wt=f"{good_wt}{c}error: forged line", handle=f"term{c}x")
            ok, msg = spawnmod.spawn("spawny", ROOT, base="main")
            check(f"spawn: a worktree path carrying {label} is not read as a path",
                  not ok and msg.startswith("could not read the new worktree path")
                  and ch not in msg, repr(msg))
            check(f"spawn: and {label} reaches no row, so no later message",
                  stored() == (None, None), stored())

        # The same refusal on the way in through the environment.
        os.environ["ORCA_TERMINAL_HANDLE"] = "h\nerror: forged line"
        os.environ["ORCA_WORKTREE_ID"] = "/tmp/plain-id"
        launch._record_terminal("spawny", "alpha")
        check("_record_terminal: nor is an environment handle carrying a newline",
              stored()[0] is None, stored())
        os.environ.pop("ORCA_TERMINAL_HANDLE", None)
        os.environ.pop("ORCA_WORKTREE_ID", None)

        # A value that is not a string cannot be tested for what it carries,
        # and it is not a path either: the same refusal, not a crash.
        spawnmod._run = lambda args, cwd=None: fake_run(args, wt=["/tmp/x"])
        ok, msg = spawnmod.spawn("spawny", ROOT, base="main")
        check("spawn: nor is a worktree path that is not a string read as one",
              not ok and msg.startswith("could not read the new worktree path"), repr(msg))

        # start_in was the sink the sink-by-sink fix missed: it names in its own
        # message the worktree it is given, and what it is given is the row
        # spawn wrote. Drive it with exactly that row, not with a clean path.
        check("start_in: spawn left it no stored worktree carrying one to name",
              stored()[1] is None, stored())
        ok, msg = spawnmod.start_in("spawny", stored()[1] or good_wt)
        check("start_in: so no control reaches the message naming the worktree",
              ok and "\x9b" not in msg, repr(msg))
        check("start_in: nor does the handle it parses for itself",
              "terminal unknown" in msg and all("\x9b" not in (v or "") for v in stored()),
              (repr(msg), stored()))

        # The handle alone. The worktree is good, so the spawn goes through -
        # with no handle at all, exactly as an unreadable one already behaved.
        spawnmod._run = lambda args, cwd=None: fake_run(args, wt=good_wt)
        ok, msg = spawnmod.spawn("spawny", ROOT, base="main")
        check("spawn: a handle carrying a control is not read as a handle",
              ok and "\x9b" not in msg and "terminal unknown" in msg, repr(msg))
        check("spawn: so the row holds neither it nor a rewritten version of it",
              stored()[0] is None, stored())

        # The parse is not the only door into that row: the other writer reads
        # its handle and its worktree out of the environment, which arrive no
        # cleaner. Each of the two on its own, since a refused handle stops the
        # write and would hide whatever the worktree id did.
        os.environ["ORCA_TERMINAL_HANDLE"] = f"h{csi}1"
        os.environ["ORCA_WORKTREE_ID"] = "/tmp/plain-id"
        launch._record_terminal("spawny", "alpha")
        check("_record_terminal: an environment handle carrying one is refused too",
              stored()[0] is None, stored())

        os.environ["ORCA_TERMINAL_HANDLE"] = "h-plain"
        os.environ["ORCA_WORKTREE_ID"] = f"/tmp/id{csi}2"
        launch._record_terminal("spawny", "alpha")
        check("_record_terminal: and the worktree id beside a good handle is too",
              stored() == ("h-plain", None), stored())
    finally:
        os.environ.pop("ORCA_TERMINAL_HANDLE", None)
        os.environ.pop("ORCA_WORKTREE_ID", None)
        (spawnmod._run, spawnmod.shutil.which,
         orcamod.run_current, orcamod.run_create, dmn_delivery.wait_for_session) = real
        con.close()


def test_the_bridge_says_what_an_agent_is_where_there_is_room(home: Path) -> None:
    """Which model at which effort - and one day which tool at all - is what
    separates two rows doing the same work at different prices. It is still
    configuration, so it gives way to what is happening now on a narrow screen.
    """
    from ocp.team import agents as agentsmod
    _daemon_home(home, 20)
    check("kind: model and effort, effort short enough for a column",
          agentsmod.kind({"model": "opus", "effort": "medium"}) == "opus mid")
    check("kind: the defaults are what a launch would use",
          agentsmod.kind({}) == f"{agentsmod.DEFAULT_MODEL} mid")
    check("kind: a second vendor names itself; the first does not",
          agentsmod.kind({"vendor": "codex", "model": "gpt-5", "effort": "high"})
          == "codex gpt-5 high"
          and "claude" not in agentsmod.kind({"vendor": "claude", "model": "opus"}))
    check("kind: an effort we do not know is shown whole, not dressed as one of ours",
          agentsmod.kind({"model": "opus", "effort": "glacial"}) == "opus glacial")


def test_the_bridges_wake_column_dates_a_weekly_wall(home: Path) -> None:
    """A 5h wall rolls over inside a day, so a bare clock says when. A weekly
    one can be days off - the same bare "%H:%M" in the wake column would claim
    it rolls over today. Reverting the column's own formatting alone (leaving
    launch.describe_wake_wait's, which the daemon's log and `ocp ps`/`triggers`
    read) must fail only this test - `ocp ps`, tested elsewhere, already
    covers the sentence."""
    _daemon_home(home, 20)
    con = db.connect()
    real_would_wake, real_wake_wait = dmn_lifecycle.would_wake, launch.wake_wait
    try:
        db.set_closed(con, "backend", "sess-x")
        reset_at = int(time.time()) + 3 * 86400 + 3600
        dmn_lifecycle.would_wake = lambda con, row: True
        launch.wake_wait = lambda name, row, con: launch.WakeWait(
            "alpha", 100, reset_at, window="7d", is_long=True)
        line = next(ln for ln in tui._ANSI.sub("", _snap(200, 40)).splitlines()
                    if "backend" in ln)
        check("walled: the wake column dates a weekly wall, not a bare clock",
              time.strftime("%m-%d %H:%M", time.localtime(reset_at)) in line, line)
    finally:
        dmn_lifecycle.would_wake, launch.wake_wait = real_would_wake, real_wake_wait
        con.close()


def test_top_shows_what_is_waiting_on_the_human(home: Path) -> None:
    """An escalation addressed to the human is the one queue nothing in this
    system ever clears on its own, and it used to be visible only by running
    `ocp inbox` with the right name. It gets its own panel, and it is not also
    drawn as traffic between agents."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        agents.register("marina")
        db.add_request(con, "marina", actions.HUMAN, "which sea variant ships?",
                       None, "peer", "async", None, 0, None)
        db.add_request(con, "marina", "backend", "look at this diff",
                       None, "peer", "async", None, 0, None)

        waiting = actions.messages(con)
        check("top: a request addressed to the human is a message for them",
              [m["from"] for m in waiting] == ["marina"], str(waiting))
        check("top: and agent-to-agent traffic is not",
              all("diff" not in (m["body"] or "") for m in waiting), str(waiting))

        text = _snap(120, 60)
        plain = [tui._ANSI.sub("", ln) for ln in text.split(chr(10))]
        # A count of zero is not news, and three of them crowd out the one
        # number that is.
        plain_title = [tui._ANSI.sub("", ln) for ln in _snap(120, 60).split(chr(10))
                       if "FOR YOU" in ln][0]
        check("top: the panel counts only what there is to count",
              "0 " not in plain_title, plain_title.strip()[:70])

        check("top: the panel is drawn with the question in it",
              any("which sea variant ships?" in ln for ln in plain),
              str([ln for ln in plain if "marina" in ln])[:200])
        # Agent-to-agent traffic is not the human's mail, and it is not drawn
        # at all while it is moving - only once it has stopped moving.
        check("top: a request between agents is not in the human's panel",
              not any("look at this diff" in ln for ln in plain), "")
        con.execute("UPDATE requests SET ts = ? WHERE target = 'backend'",
                    (int(time.time()) - actions.STUCK_AFTER_S - 60,))
        db.set_terminal(con, "backend", None, None)
        plain = [tui._ANSI.sub("", ln) for ln in _snap(120, 60).split(chr(10))]
        check("top: once it has been waiting, it is drawn as stuck, with why",
              any("marina → backend" in ln and "no terminal" in ln for ln in plain),
              str([ln.strip()[:80] for ln in plain if "marina" in ln])[:200])
    finally:
        con.close()


def test_top_acts_through_the_same_calls_the_daemon_makes(home: Path) -> None:
    """The screen may act, but it may not decide: every key is a call into
    `actions`, which calls what the daemon calls. What it does decide is which
    keys to offer, and that answer is read from the same row the action reads -
    offering `close` on an agent that is already closed is the screen and the
    daemon disagreeing in the one place a human is looking."""
    _daemon_home(home, 20)
    con = db.connect()
    sent: list[str] = []
    real = (orcamod.available, orcamod.alive, orcamod.wait_idle, orcamod.send)
    try:
        orca_seen()
        orcamod.wait_idle = lambda h, timeout_ms=0: True
        orcamod.close_terminal = lambda h, tab=True: (False, "this test keeps the pane")
        table = sessions_are("backend")
        orcamod.send = relaunching_send(sent, table)
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        (home / "agents" / "backend").mkdir(parents=True, exist_ok=True)
        (home / "agents" / "backend" / "status.json").write_text(json.dumps({
            "account": "alpha", "collected_at": int(time.time()),
            "session_id": "sess-6", "pct_5h": 20}))

        check("top: a running agent may be closed, not woken",
              actions.can(con, "backend", "close")[0]
              and not actions.can(con, "backend", "wake")[0])

        said = actions.close(con, "backend")
        row = db.get_agent_state(con, "backend")
        check("top: closing from the screen is the daemon's own close",
              row["closed_at"] is not None and row["resume_session"] == "sess-6", said)
        ok, why = actions.can(con, "backend", "wake")
        check("top: with nothing waiting for it, a closed agent is not offered a wake, and why",
              not ok and "nothing is waiting" in why, why)
        support.something_waiting(con, "backend")
        check("top: with something waiting it may be woken, not closed",
              actions.can(con, "backend", "wake")[0]
              and not actions.can(con, "backend", "close")[0])
        con.execute("UPDATE requests SET state = 'done'")

        said = actions.wake(con, "backend")
        check("top: with nothing waiting for it, waking says so and resumes nothing",
              "nothing is waiting" in said and db.get_agent_state(con, "backend")["closed_at"]
              and not any("--resume" in t for t in sent), said)
        support.something_waiting(con, "backend")
        said = actions.wake(con, "backend")
        row = db.get_agent_state(con, "backend")
        check("top: waking from the screen resumes that same session",
              row["closed_at"] is None and any("--resume sess-6" in t for t in sent), said)
        con.execute("UPDATE requests SET state = 'done'")

        # A stall is the one state the daemon will not leave on its own, so the
        # screen is where it is cleared - including the wake streak, or the
        # sweep would later read it as a stall of its own making.
        db.set_agent_state(con, "backend", dmn_states.STALLED, detail="by hand")
        for _ in range(dmn_states.MAX_FAILED_ATTEMPTS):
            db.bump_counter(con, "backend", "wake_failures")
        check("top: a stalled agent is still offered w, whatever is waiting",
              actions.can(con, "backend", "wake")[0])
        said = actions.unstall(con, "backend")
        row = db.get_agent_state(con, "backend")
        check("top: unstall clears the state and every count behind it",
              row["state"] == dmn_states.QUEUED and row["wake_failures"] == 0
              and row["failed_attempts"] == 0, said)

        # Sending work from the screen is an ordinary ask, from the human -
        # including the routing rules. A team that does not declare the human's
        # edge refuses here exactly as `ocp ask` would, and says so.
        said = actions.message(con, "backend", "have a look at the sea shader")
        check("top: the screen does not smuggle a message past routing",
              "denied" in said and not db.queued_for(con, "backend"), said)

        escalate = '  - { from: "*",         to: "human", kind: escalate,  creates_gate: true }'
        hand_edit_team(DESIGN_TEAM.replace(
            escalate,
            escalate + chr(10)
            + '  - { from: "human", to: "*", relation: supervisor, '
              'response: fire_and_forget }'))
        said = actions.message(con, "backend", "have a look at the sea shader")
        sent = db.get_request(con, int(said.split("queued as #", 1)[1].split()[0]))
        check("top: and where it is declared, it arrives as a message from the human, typed at once",
              sent["requester"] == actions.HUMAN and sent["target"] == "backend"
              and sent["kind"] == "say" and "delivered" in said, said)
        check("top: an empty one is not sent at all",
              "nothing sent" in actions.message(con, "backend", "   "))

        # An agent with no terminal is the one the daemon cannot help at all:
        # work queued for it waits forever because there is nothing to type
        # into. Starting one is the screen's job, and it is offered only where
        # there is no terminal already.
        # STALLED is the one state the daemon deliberately leaves alone, so the
        # screen must not offer the keys that would clear it by accident: a
        # handoff pays for a fold and ends PARKED, and the stall a human was
        # meant to read is gone with none of the counters unstall resets.
        db.set_agent_state(con, "backend", dmn_states.STALLED, detail="stalled")
        check("top: a stalled agent is offered unstall and nothing that hides it",
              actions.can(con, "backend", "unstall")[0]
              and not actions.can(con, "backend", "park")[0]
              and not actions.can(con, "backend", "close")[0]
              and not actions.can(con, "backend", "fold")[0])
        # A fold never types into the terminal, so a lost terminal does not
        # stand in its way - that agent is a likelier one to want folded.
        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="cleared")
        db.set_terminal(con, "backend", None, None)
        check("top: folding is offered to an agent whose terminal is gone",
              actions.can(con, "backend", "fold")[0]
              and not actions.can(con, "backend", "park")[0])
        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="cleared")

        # Work waiting is the one case where closing only adds delay - the next
        # tick wakes it straight back up. should_close refuses for that reason.
        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)
        check("top: closing is not offered while work is waiting",
              not actions.can(con, "backend", "close")[0])
        con.execute("UPDATE requests SET state = 'done' WHERE target = 'backend'")

        # perform_handoff returns True for three things that are not a handoff.
        # Reporting the state afterwards read "RUNNING" as though it were a
        # result; the line it logged is the answer.
        db.set_agent_state(con, "backend", dmn_states.RUNNING, detail="mid-turn")
        orcamod.idle_now = lambda h: False
        said = actions.park(con, "backend")
        check("top: parking a busy agent says it was deferred, not its state",
              "defer" in said and "RUNNING" not in said, said)
        orcamod.idle_now = lambda h: True
        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="cleared")

        # A terminal can be closed by a human or lost when Orca restarts, and
        # nothing happens to the work: the branch and the checkout are still
        # there. Losing the record of where they are is how an agent ends up
        # with a second worktree beside its first.
        wt = home / "wt-backend"
        wt.mkdir(parents=True, exist_ok=True)
        db.set_terminal(con, "backend", "term_x", str(wt))
        obsmod.live_sessions = lambda: {}
        orcamod.live_terminals = lambda: set()      # Orca no longer has it
        dmn_reconciler.sweep(con, lambda _m: None, [])
        row = db.get_agent_state(con, "backend")
        check("start: a dropped terminal does not take the worktree with it",
              row["terminal"] is None and row["worktree"] == str(wt),
              str(dict(row)))

        # `ocp launch` records what Orca hands it, which is `<repo id>::<path>`,
        # while `spawn` records a plain path. Testing the selector as a path
        # found nothing, so an agent that still had its worktree was given a
        # second one beside it.
        check("start: the worktree is found inside the selector Orca records",
              spawnmod.worktree_path(f"abc123::{wt}") == str(wt),
              str(spawnmod.worktree_path(f"abc123::{wt}")))
        check("start: and a worktree that is gone is not one",
              spawnmod.worktree_path(f"abc123::{wt}-missing") is None)
        db.set_terminal(con, "backend", None, f"abc123::{wt}")
        db.clear_closed(con, "backend")     # closed with a session to resume, it is woken instead (T407)

        started: list[list[str]] = []
        real_run = spawnmod._run
        try:
            # git finds `main` (T483 asks it); Orca is the one that is missing
            spawnmod._run = lambda argv, **kw: (started.append(argv),
                                                (0, "", "") if argv[:2] == ["git", "rev-parse"]
                                                else (1, "", "no orca here"))[1]
            actions.start(con, "backend", repo=str(home))
            check("start: an agent with a worktree is started in it, not given another",
                  [a[1] for a in started] == ["terminal"], str(started))
            db.set_terminal(con, "backend", None, None)
            started.clear()
            actions.start(con, "backend", repo=str(home))
            check("start: and one with nowhere to run gets a worktree first",
                  any(a[:2] == ["orca", "worktree"] for a in started), str(started))
        finally:
            spawnmod._run = real_run
        db.set_terminal(con, "backend", "term_x", "wt")
        orcamod.live_terminals = lambda: {"term_x"}
        sessions_are("backend")

        check("top: an agent with a terminal is not offered a second one",
              not actions.can(con, "backend", "start")[0])
        # A refusal that does not say why sends the reader to press it again,
        # or to the wrong fix. Every no carries the row that said it.
        for key in ("start", "close", "park", "wake", "unstall"):
            allowed, why = actions.can(con, "backend", key)
            check(f"top: a refused {key} says why",
                  allowed or ("backend" in why and len(why) > 20), f"{allowed} {why}")

        # And "it has nowhere to run" is not an answer to "take me to it": the
        # agent with no terminal is the one a human is going there to fix.
        db.set_terminal(con, "backend", None, None)
        check("top: going to an agent is never refused",
              actions.can(con, "backend", "terminal")[0])
        went: list[str] = []
        real_focus, real_spawn = orcamod.focus, spawnmod.spawn
        try:
            orcamod.focus = lambda h: (went.append(h), (True, ""))[1]
            spawnmod.spawn = lambda agent, repo, base="main": (
                db.set_terminal(db.connect(), agent, "term_new", str(repo)),
                (True, f"{agent}: started in a new worktree"))[1]
            said = actions.terminal(con, "backend", repo=str(home))
            check("top: one with no terminal gets one, and then is gone to",
                  went == ["term_new"] and "started" in said and "backend's terminal" in said,
                  f"{went} {said}")
            went.clear()
            said = actions.terminal(con, "backend")
            check("top: and one that already has a terminal is just gone to",
                  went == ["term_new"] and "started" not in said, f"{went} {said}")
        finally:
            orcamod.focus, spawnmod.spawn = real_focus, real_spawn
        db.set_terminal(con, "backend", None, None)
        check("top: one with none is", actions.can(con, "backend", "start")[0])
        real_run = spawnmod._run
        # The Orca that refuses: `worktree create` on a folder that is not a repository.
        spawnmod._run = lambda args, cwd=None: (1, "", "orca: not a git repository")
        try:
            said = actions.start(con, "backend", repo=str(home))
        finally:
            spawnmod._run = real_run
        check("top: and starting it says why when it cannot",
              "orca" in said or "worktree" in said or "base" in said or "commit" in said, said)
        db.set_terminal(con, "backend", "term_x", "wt")

        # Answering closes the request and tells the agent, which is what
        # `ocp resolve` does - the screen must not invent a second way.
        db.add_request(con, "marina", actions.HUMAN, "which variant ships?",
                       None, "peer", "async", None, 0, None)
        # By who sent it. The mailbox holds answers to the human's own asks as
        # well now, and they sort among the rest by id.
        rid = [m["id"] for m in actions.messages(con) if m["from"] == "marina"][-1]
        said = actions.answer(con, rid, "variant 11")
        row = db.get_request(con, rid)
        check("top: answering resolves the request with the verdict",
              row["state"] == "done" and "variant 11" in (row["verdict"] or ""), said)
        check("top: and it is no longer waiting on the human",
              rid not in [m["id"] for m in actions.messages(con)])
    finally:
        (orcamod.available, orcamod.alive, orcamod.wait_idle, orcamod.send) = real
        con.close()


def test_a_report_shows_its_name_without_its_parents_and_keeps_the_whole_key(home: Path) -> None:
    """Founder #691/#694: orcapipe-manager.chat reads as `chat` under
    orcapipe-manager. Display only: the prefix goes when it is the drawn
    parent's own name, never on the dot alone, and every key stays whole."""
    _daemon_home(home, 20)
    hand_edit_team("""
agents:
  orcapipe-manager: {}
  orcapipe-manager.chat: {}
  other.tool: {}
edges:
  - { from: "orcapipe-manager", to: "orcapipe-manager.chat", relation: supervisor, response: fire_and_forget }
  - { from: "orcapipe-manager", to: "other.tool", relation: supervisor, response: fire_and_forget }
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
""")
    for n in ("orcapipe-manager", "orcapipe-manager.chat", "other.tool"):
        agents.register(n)
    con = db.connect()
    try:
        tui.EXPANDED.add("orcapipe-manager")
        drawn, _ = tui.agents_data(con)
        open_tree = [n for n, _d, _u in tui.visible_tree()]
        tui.EXPANDED.clear()
        by_key = {r.name: tui._ANSI.sub("", r.twig) + r.shown for r in drawn}
        check("dotted: the row under its parent reads `└ chat`",
              "└ chat" in by_key.get("orcapipe-manager.chat", ""), str(by_key))
        check("dotted: and its key is the whole name, so anything selected reads it whole",
              "orcapipe-manager.chat" in by_key and "chat" not in by_key
              and "orcapipe-manager.chat" in open_tree)
        check("dotted: a dot alone strips nothing - other.tool is not orcapipe-manager's name",
              "└ other.tool" in by_key.get("other.tool", ""), by_key.get("other.tool"))
        check("dotted: the parent's row is untouched",
              "orcapipe-manager" in by_key.get("orcapipe-manager", ""))
        # The parent folded: its report is not drawn, so nothing is shortened
        # against a parent that is not there.
        drawn = [("orcapipe-manager.chat", 1, [])]
        check("dotted: a parent that is not drawn leaves the name whole",
              tui.shown_names(drawn) == {"orcapipe-manager.chat": "orcapipe-manager.chat"})
        # Flat surfaces have no parent above the name and print it whole.
        askmod.ask(askmod.HUMAN, "need a decision", requester="orcapipe-manager.chat")
        waits = [w["who"] for w in actions.founder_waits(con) if w["kind"] == "escalation"]
        check("dotted: WAITING ON YOU names it whole - a flat list has no parent above it",
              waits == ["orcapipe-manager.chat"], str(waits))
    finally:
        con.close()
        tui.EXPANDED.clear()


def test_a_proposal_dismissed_in_the_bridge_is_never_stranded(home: Path) -> None:
    """Founder #689: "read" on a proposal escalation closed it with a verdict
    that is neither approve nor reject, and the proposal stayed `proposed`
    with a red row nothing could clear. The bridge's own dismiss now refuses a
    proposal that is still open, and the proposer can withdraw it, which takes
    it off the founder's list. The stranded state itself (a request closed by
    an older build) is built by hand and cleared the same way."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        accounts.register("beta")
        agents.register("hr")
        hand_edit_team("""
agents:
  hr: {}
edges:
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
""")
        def rows():
            return [w for w in actions.founder_waits(con) if w["kind"] == "proposal"]
        ok, said = hr.propose("add", "dohyun", requester="hr", why="more hands")
        pid = said.split()[1]
        rid = db.get_request(con, int(said.split("#")[1].split(".")[0]))["id"]
        ok, msg = actions.dismiss(con, rid, me=actions.HUMAN)
        check("dismiss: an open proposal cannot be read away",
              not ok and "approve" in msg and db.get_request(con, rid)["state"] == "queued", msg)
        check("dismiss: and stays on the founder's list", len(rows()) == 1, str(rows()))

        # What an older build left behind: closed as "read - no reply".
        askmod.resolve(rid, "read - no reply", state="done", notify=False)
        check("stranded: the row is red with no way forward",
              len(rows()) == 1 and rows()[0]["red"], str(rows()))
        ok, said = hr.withdraw(pid, "marina")
        check("withdraw: not another agent's to withdraw", not ok, said)
        ok, said = hr.withdraw(pid, "hr")
        check("withdraw: the proposer takes it back", ok and rows() == [], said)
        check("withdraw: hr list shows it withdrawn",
              hr.load_proposal(pid)["state"] == "withdrawn")
        ok, said = hr.apply(pid)
        check("withdraw: a withdrawn proposal is not applied", not ok
              and "dohyun" not in paths.list_agents(), said)
        ok, said = hr.withdraw(pid, "hr")
        check("withdraw: and cannot be withdrawn twice", not ok, said)

        # Withdrawn while still unanswered: the escalation closes with it.
        ok, said = hr.propose("add", "dohyun", requester="hr", why="again")
        pid2 = said.split()[1]
        rid2 = int(said.split("#")[1].split(".")[0])
        ok, said = hr.withdraw(pid2, "hr")
        check("withdraw: an unanswered one leaves the list and closes its escalation",
              ok and rows() == [] and db.get_request(con, rid2)["state"] != "queued",
              str(rows()))

        # Answered first, withdrawn after: the founder's answer stands and the
        # withdraw refuses, leaving apply to read it (task #32).
        ok, said = hr.propose("add", "dohyun", requester="hr", why="third")
        pid3 = said.split()[1]
        rid3 = int(said.split("#")[1].split(".")[0])
        check("answer: an open proposal refuses a non-decision",
              "approve" in actions.answer(con, rid3, "later")
              and db.get_request(con, rid3)["state"] == "queued")
        support._set_request_state(con, rid3, "done", "approve")  # answered, apply not yet run
        ok, said = hr.withdraw(pid3, actions.HUMAN)
        check("withdraw: an approved proposal is not withdrawn - the founder answered first",
              not ok and "already answered" in said and "apply" in said, said)
        check("withdraw: and it stays proposed, for apply to enact",
              hr.load_proposal(pid3)["state"] == "proposed"
              and db.get_request(con, rid3)["verdict"] == "approve")
    finally:
        con.close()


def test_the_screen_says_which_account_to_spend_next(home: Path) -> None:
    """The ✔ column says who *may* take work, which on a good day is everyone.
    It never said where the headroom was, so the reader compared two bars by
    eye - at a decision the daemon already makes."""
    _daemon_home(home, 40)
    write_status(home, "beta", pct_5h=8, pct_7d=13, ctx_pct=5,
                 reset_5h=int(time.time()) + 1800)
    accounts.register("beta")
    real_login = accounts.logged_in
    try:
        def marked() -> list[str]:
            plain = [tui._ANSI.sub("", ln) for ln in _snap(120, 60).split(chr(10))]
            rows = [ln for ln in plain if "alpha" in ln or "beta" in ln]
            return [ln for ln in rows if ln.rstrip("│ ").endswith(" next")]

        calls: list[str] = []

        def login(a: str) -> bool:
            calls.append(a)
            return True

        accounts.logged_in = login
        tui._LOGIN.clear()
        got = marked()
        seen = len(calls)
        marked()
        check("accounts: the login check is not run again on every redraw",
              len(calls) == seen and seen <= len(paths.list_accounts()), str(calls))
        accounts.logged_in = lambda a: True  # noqa: E731 - keychain is not the subject
        check("accounts: the account with the most headroom says next",
              len(got) == 1 and "beta" in got[0], str(got))
        check("accounts: and it is the pick's own answer",
              status.pick_account(["alpha", "beta"], load=loadmod.read_all())[0] == "beta")

        # An account nobody is logged into cannot start a session, however
        # much room it has.
        accounts.logged_in = lambda a: a != "beta"  # noqa: E731
        tui._LOGIN.clear()
        got = marked()
        check("accounts: never one that nobody is logged into",
              len(got) == 1 and "alpha" in got[0], str(got))

        # Fail-closed: nothing assignable means no mark, not a guess.
        accounts.logged_in = lambda a: True  # noqa: E731
        tui._LOGIN.clear()
        for a in ("alpha", "beta"):
            write_status(home, a, pct_5h=100, pct_7d=99, ctx_pct=5,
                         reset_5h=int(time.time()) + 1800)
        check("accounts: with nothing assignable, nothing is marked", not marked())
    finally:
        accounts.logged_in = real_login


def test_the_next_mark_does_not_flicker_between_close_accounts(home: Path) -> None:
    """The panel's own memory (`tui._LAST_NEXT`) is what closes the gap
    `test_pick_account_does_not_bounce_between_two_close_accounts` opens
    without it: redraw twice with alpha and beta a hair apart in weekly pace,
    and the mark must stay put rather than trade places on rounding alone."""
    _daemon_home(home, 40)
    write_status(home, "beta", pct_5h=8, pct_7d=13, ctx_pct=5,
                 reset_5h=int(time.time()) + 1800)
    accounts.register("beta")
    real_login = accounts.logged_in
    now = int(time.time())
    reset_7d = now + 3 * 86400
    try:
        accounts.logged_in = lambda a: True  # noqa: E731
        tui._LOGIN.clear()
        tui._LAST_NEXT = None

        def next_mark() -> str | None:
            plain = [tui._ANSI.sub("", ln) for ln in _snap(120, 60).split(chr(10))]
            for ln in plain:
                if ("alpha" in ln or "beta" in ln) and ln.rstrip("│ ").endswith(" next"):
                    return "alpha" if "alpha" in ln else "beta"
            return None

        write_status(home, "alpha", pct_5h=40, pct_7d=41, ctx_pct=5,
                     reset_5h=now + 1800, reset_7d=reset_7d)
        write_status(home, "beta", pct_5h=8, pct_7d=44, ctx_pct=5,
                     reset_5h=now + 1800, reset_7d=reset_7d)
        first = next_mark()
        check("next mark: alpha is picked, clearly ahead on pace",
              first == "alpha", first)
        check("next mark: the panel remembers it", tui._LAST_NEXT == "alpha")

        # beta closes to exactly one pace bucket ahead of alpha's raw figure -
        # without the panel's memory this overtakes outright; the memory's
        # own head start lands the two in the same bucket, and the mark stays.
        write_status(home, "beta", pct_5h=8, pct_7d=40, ctx_pct=5,
                     reset_5h=now + 1800, reset_7d=reset_7d)
        check("next mark: stays on alpha rather than trading places with a "
              "one-bucket move", next_mark() == "alpha", next_mark())
    finally:
        accounts.logged_in = real_login
        tui._LAST_NEXT = None


def test_one_verdict_for_the_panel_and_the_page(home: Path) -> None:
    """The panel draws the tick and the page the sentence; both come from one
    function, and an account nobody is logged in to is a no on both."""
    _daemon_home(home, 20)
    st = status.read("alpha")
    ok, why = status.account_verdict("alpha", st, None, [], True)
    check("verdict: a healthy, logged-in account is a yes", ok, why)
    ok, why = status.account_verdict("alpha", st, None, [], False)
    check("verdict: not logged in is a no that says so, whatever the reading",
          not ok and why == "not logged in", why)
    ok, why = status.account_verdict("alpha", st, None, [("alpha", "5h 10%, ranked")], True)
    check("verdict: the ranked sentence wins where there is one", why == "5h 10%, ranked", why)


def test_the_accounts_row_draws_a_last_seen_figure_only_inside_its_window(home: Path) -> None:
    """The ACCOUNTS row and `ocp account list` draw the 5h figure from one
    definition: last seen, marked ?, while the window is open; nothing once it
    has rolled over."""
    _daemon_home(home, 20)
    _with_accounts("beta")
    now = int(time.time())
    for reset, drawn in ((now + 1800, True), (now - 60, False)):
        write_status(home, "beta", pct_5h=40, pct_7d=10, reset_5h=reset,
                     reset_7d=now + 86400, collected_at=now - 3600)
        st = status.read("beta")
        cell = tui._ANSI.sub("", tui.account_cells("beta", st)["five"])
        check(f"account_cells: window {'open' if drawn else 'rolled over'} - "
              f"{'the last figure, marked ?' if drawn else 'no figure'}",
              ("40%" in cell) is drawn and ("40%?" in cell) is drawn, cell)
        check("account_cells: agrees with shown_short",
              (st.shown_short()[0] == 40) is drawn, str(st.shown_short()))


def test_a_span_ends_in_its_unit(home: Path) -> None:
    """Every form of a span ends in its unit letter, so "2h40" is never read
    as 2.40 hours."""
    for secs, precise, want in (
            (162 * 3600 + 20 * 60, False, "6d18h"), (86400, False, "1d0h"),
            (5 * 3600 + 3 * 60, False, "5h03m"), (86400 - 60, False, "23h59m"),
            (3600, False, "1h00m"), (35 * 60 + 12, False, "35m"), (10, False, "1m"),
            (35 * 60 + 12, True, "35m12s"), (2 * 60 + 5, True, " 2m05s"),
            (5 * 3600 + 3 * 60, True, "5h03m"), (6 * 86400 + 23 * 3600, True, "6d23h")):
        got = timefmt.span(secs, precise=precise)
        check(f"span: {secs}s{' precise' if precise else ''} reads {want!r}", got == want, got)
    check("span: the bridge's age column reads days too", timefmt.ago(162 * 3600 + 20 * 60) == "6d18h"
          and timefmt.ago(3 * 3600 + 100) == "3h", timefmt.ago(162 * 3600 + 20 * 60))
    check("span: and so does the stale line's age", timefmt.ago(162 * 3600 + 20 * 60) == "6d18h"
          and timefmt.ago(3 * 3600) == "3h")
    now = int(time.time())
    for ts, want in ((now + 6 * 86400 + 23 * 3600 + 30, "6d23h"), (now + 5 * 3600 + 200, "5h03m"),
                     (now + 59 * 60 + 59, "59m59s")):
        cell = tui.countdown(ts).strip()
        check(f"span: the bridge countdown says {want}", cell == want, cell)



def test_the_snapshot_is_plain_text_of_every_panel_and_nothing_a_terminal_would_obey(home: Path) -> None:
    """`ocp bridge --once` (and a pipe) prints `tui.snapshot`: the header, the
    four panels in order, from the same rows the screen draws - and no escape
    code, whatever an agent wrote into what it shows."""
    _daemon_home(home, 20)
    agents.register("reviewer")
    askmod.ask(actions.HUMAN, "\033[2J\033]0;PWNED\007please\x9b review this", requester="reviewer")
    text = tui.snapshot()
    lines = text.split("\n")
    at = [next((i for i, ln in enumerate(lines) if ln.startswith(head)), -1)
          for head in ("ACCOUNTS", "AGENTS", "FOR YOU", "STUCK")]
    check("snapshot: the four panels are there, in order", -1 not in at and at == sorted(at), str(at))
    check("snapshot: it opens on the daemon line", lines[0].startswith("ocp · daemon "), lines[0])
    check("snapshot: an account, an agent and a message each have a row",
          any(ln.strip().startswith("alpha") for ln in lines)
          and any("reviewer" in ln and "IDLE" in ln for ln in lines[at[1]:at[2]])
          and any("please review this" in ln for ln in lines[at[2]:at[3]]),
          text[:600])
    check("snapshot: nothing a terminal would obey is in it, colour included",
          not re.search(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]", text) and "PWNED" not in text, repr(text[:300]))



def test_a_queued_request_past_the_newest_chains_is_still_on_the_agent_page(home: Path) -> None:
    """A queued request in a chain older than the ones the conversation draws
    is not lost: it is listed under it, headed with whose it is."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        agents.register("elder")
        old = db.add_request(con, askmod.HUMAN, "elder", "the oldest question, still queued",
                             None, "peer", "async", None, 0, None)
        for i in range(actions.CHAT_CHAINS + 1):
            db.add_request(con, askmod.HUMAN, "elder", f"newer chain {i}", None, "peer",
                           "async", None, 0, None)
        pg = tui._agent_page(con, "elder")
        text = "\n".join(tui._ANSI.sub("", ln) for w in (80, 30) for ln in tui.lines_of(pg, w))
        check("page: a queued request past the conversation's newest chains is still on the page, "
              "under a heading that describes it",
              f"#{old} from human" in text and "ALSO QUEUED FOR IT" in text
              and text.index("ALSO QUEUED FOR IT") < text.index(f"#{old} from human"), text[:300])
    finally:
        con.close()


def test_word_movement_stops_at_a_lines_edge() -> None:
    """Ctrl-Left and Ctrl-Right stop at a line's edge instead of running through it."""
    text = "one two\nthree four"
    check("cursor: word left from the start of a line stops at the end of the line above",
          tui._word_left(8, text) == 7, str(tui._word_left(8, text)))
    check("cursor: word right from the end of a line stops at the start of the next",
          tui._word_right(7, text) == 8, str(tui._word_right(7, text)))
    check("cursor: within a line it still moves a word at a time",
          tui._word_left(18, text) == 14 and tui._word_right(8, text) == 13
          and tui._word_left(7, text) == 4)


def test_an_escape_in_a_message_body_is_stripped(home: Path) -> None:
    """An escape in a body is stripped: a clear-screen sequence in it would
    wipe the screen it is drawn on."""
    cleaned = tui.wrap(["\033[2J\033[Hpasted \033[31mtool output"], 40)
    check("read: escapes in a body are text stripped, not commands drawn",
          "\033" not in "".join(cleaned) and "pasted tool output" in " ".join(cleaned),
          str(cleaned))


def test_an_opened_agent_shows_its_whole_inbox_and_handoff(home: Path) -> None:
    """The page an agent opens on is sections, and the inbox and handoff are
    whole: a slice that existed because the view could not scroll would be a
    silent cut inside the thing being scrolled."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        for i in range(9):
            db.add_request(con, "marina", "backend", f"request {i}", None,
                           "peer", "async", None, 0, None)
        keep_handoff("backend", "\n".join(f"- line {i}" for i in range(30)))
        shown = "\n".join(ln for _, rows in actions.agent_detail(con, "backend")
                           for ln in rows)
        check("read: the page is sections, not one wall of text",
              [t for t, _ in actions.agent_detail(con, "backend") if t],
              str([t for t, _ in actions.agent_detail(con, "backend")]))
        check("read: an opened agent shows its whole inbox",
              all(f"request {i}" in shown for i in range(9)), shown[:120])
        check("read: and its whole handoff",
              "- line 29" in "\n".join(actions.agent_knows(con, "backend")))
    finally:
        con.close()


def test_the_messenger_tab_carries_a_conversation_both_ways(home: Path) -> None:
    """The founder writes in the tab, the agent answers, and the answer is in
    the tab on the next redraw - with the message sent whole and under its
    thread, and the unread count on the tab until it has been read."""
    _daemon_home(home, 20)
    agents.register("reviewer")
    con = db.connect()
    try:
        def tab() -> tuple[str, list[str]]:
            return next((h, r) for h, r in actions.agent_detail(con, "reviewer")
                        if h.startswith(actions.CHAT_TAB))

        check("chat: empty says so", "nothing yet" in tab()[1][0], str(tab()))
        body = "first line\n\nthird line, after a blank\n" + "long " * 60
        said = actions.chat_reply(con, "reviewer", body)
        check("chat: a first message is sent", actions.chat_parent(con, "reviewer") is not None, said)
        first = actions.chat_parent(con, "reviewer")
        row = db.get_request(con, first)
        check("chat: the multi-line body is stored whole", row["body"] == body, repr(row["body"])[:60])
        check("chat: and drawn whole",
              any("third line, after a blank" in ln for ln in tab()[1])
              and sum(ln.count("long") for ln in tab()[1]) == 60)

        # The agent answers, from its side.
        ok, _ = askmod.resolve(first, "done: rebased\nand pushed")
        head, rows = tab()
        check("chat: the answer is there on the next read", any("rebased" in r for r in rows)
              and any("and pushed" in r for r in rows), str(rows))
        check("chat: and counted on the tab", head == "CONVERSATION (1 new)", head)
        actions.chat_seen(con, "reviewer")
        check("chat: seen clears the count", tab()[0] == "CONVERSATION", tab()[0])

        # Typed outside a card, a message is a plain message: nothing is guessed.
        actions.chat_reply(con, "reviewer", "thanks - now the docs")
        plain = db.get_request(con, actions.chat_parent(con, "reviewer"))
        check("chat: a message typed in the tab is a new message to the agent",
              plain["body"] == "thanks - now the docs" and plain["requester"] == actions.HUMAN
              and plain["target"] == "reviewer" and plain["parent_id"] is None, str(dict(plain)))

        # An open question is not answered by typing in the tab; only inside its card.
        old_fyi = askmod.ask(actions.HUMAN, "status: started", requester="reviewer", fyi=True)[2]
        q1 = askmod.ask(actions.HUMAN, "first question?", requester="reviewer")[2]
        q2 = askmod.ask(actions.HUMAN, "second question?", requester="reviewer")[2]
        actions.chat_reply(con, "reviewer", "one")
        check("chat: typing in the tab closes no question",
              db.get_request(con, q1)["state"] == "queued" and db.get_request(con, q2)["state"] == "queued")
        check("chat: and the footer promises nothing about answering",
              not any("answers #" in r for r in tab()[1]), str(tab()[1][-2:]))
        actions.answer_ok(con, q1, "one")
        check("chat: a legacy question is closed by a reply inside its card",
              db.get_request(con, q1)["verdict"] == "one" and db.get_request(con, q2)["state"] == "queued")
        askmod.resolve(q2, "two")
        askmod.resolve(old_fyi, "read")

        # A decision card: the reply inside it is the decision, recorded and closed.
        ok, msg, tid = actions.task_new("reviewer", actions.HUMAN, "which name?", "which name?")
        card = next(r for r in db.task_requests(con, tid) if r["target"] == actions.HUMAN)["id"]
        ok, said = actions.answer_ok(con, card, "call it x")
        row = db.get_task(con, tid)
        check("chat: the reply in a decision card decides and closes its task",
              ok and row["done_evidence"] == "call it x" and row["closed_by"] == actions.HUMAN, said)
        check("chat: and settles the request that carried the card",
              db.get_request(con, card)["state"] == "done" and db.get_request(con, card)["verdict"] == "call it x")
        ok, said = actions.answer_ok(con, card, "again")
        check("chat: a second reply decides nothing", db.get_task(con, tid)["done_evidence"] == "call it x", said)
    finally:
        con.close()


def test_the_cursor_follows_a_row_that_leaves_the_list(home: Path) -> None:
    """A report closes itself when the founder opens it: it leaves the mailbox
    with no verdict, its sender is not told, and nothing about it is drawn in
    the sender's conversation."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        # A report closing itself on open.
        ok, _m, f1 = askmod.ask(actions.HUMAN, "r1", requester="backend", fyi=True)
        ok, _m, f2 = askmod.ask(actions.HUMAN, "r2", requester="frontend", fyi=True)
        actions.opened_by_human(con, f1)
        row = db.get_request(con, f1)
        check("opened: it leaves the mailbox", row["state"] != "queued", row["state"])
        check("opened: it carries no verdict", row["verdict"] is None, row["verdict"])
        check("opened: the sender is not told - nothing to announce",
              f1 not in [r["id"] for r in db.unannounced_answers(con, "backend")])
        chat, _ = actions.conversation_and_drawn(con, "backend")
        msgs = [r for r in chat if getattr(r, "kind", None) == "msg"]
        check("opened: its verdict is never drawn in the sender's own conversation",
              not any(r.rid == f1 and r.part == 1 for r in msgs), str(chat))
        check("opened: 'read' does not appear as something said to the sender",
              not any(r.text == "read" for r in msgs), str(chat))
    finally:
        con.close()


def test_each_account_guard_fails_under_its_own_mutation(home: Path) -> None:
    """One condition per check, so removing any one guard fails a test."""
    use_home(home)
    hand_edit_team("agents:\n  backend: {}\n")
    for a in ("alpha", "beta", "gamma"):
        accounts.register(a)
    agents.register("backend")
    orig_in, orig_id, orig_logout = accounts.logged_in, accounts.identity, accounts.logout_argv
    logged = {"gamma": True}

    def put_session_on(acct):
        con = db.connect()
        try:
            db.set_agent_state(con, "backend", "RUNNING")
            con.execute("UPDATE agent_state SET account = ? WHERE agent = 'backend'", (acct,))
            con.commit()
        finally:
            con.close()

    try:
        accounts.logged_in = lambda a: logged.get(a, False)
        accounts.identity = lambda a: None
        accounts.logout_argv = lambda a: (["claude", "auth", "logout"], {}) if logged.get(a) else ([], {})

        # users_of: where the session is, and nowhere else.
        put_session_on("beta")
        check("users_of: the account its session is on",
              accounts.users_of("beta") == ["backend"] and accounts.users_of("alpha") == [],
              str((accounts.users_of("beta"), accounts.users_of("alpha"))))
        (home / "agents" / "backend" / "agent.yaml").write_text("account: alpha\n")
        check("users_of: an account named in agent.yaml is ignored",
              accounts.users_of("alpha") == [], str(accounts.users_of("alpha")))
        (home / "agents" / "backend" / "agent.yaml").unlink()
        # remove: one condition per test. Nothing uses gamma from here on.
        (home / "agents" / "backend" / "agent.yaml").write_text("account: alpha\n")
        put_session_on("alpha")
        yes = lambda: True  # noqa: E731

        def code_1_but_out(argv, env):
            logged["gamma"] = False
            return 1
        ok, msg = accounts.remove_account("gamma", yes, code_1_but_out)
        check("remove: a non-zero exit refuses even though the account reads logged out",
              not ok and "gamma" in paths.list_accounts(), msg)
        logged["gamma"] = True
        ok, msg = accounts.remove_account("gamma", yes, lambda argv, env: 0)
        check("remove: exit 0 refuses while the account still reads logged in",
              not ok and "gamma" in paths.list_accounts(), msg)
        check("remove: a refused removal leaves no marker behind",
              not paths.removing("gamma"))

        # An agent arriving while the human is being asked.
        def confirm_then_agent_arrives():
            put_session_on("gamma")
            return True
        ok, msg = accounts.remove_account("gamma", confirm_then_agent_arrives, lambda a, e: 0)
        check("remove: re-checks users after the confirmation",
              not ok and "backend" in msg and "gamma" in paths.list_accounts(), msg)
        put_session_on("alpha")

        seen = {}
        def logout_ok(argv, env):
            seen["removing"] = paths.removing("gamma")
            seen["picked"] = status.pick_account(["gamma"])[0]
            logged["gamma"] = False
            return 0
        write_status(home, "gamma", pct_5h=5, pct_7d=5, reset_5h=int(time.time()) + 1800)
        ok, msg = accounts.remove_account("gamma", yes, logout_ok)
        check("remove: an account being removed is marked, and never picked",
              seen.get("removing") is True and seen.get("picked") is None, str(seen))
        check("remove: a good logout retires it",
              ok and "gamma" not in paths.list_accounts(), msg)

        # add: the exit code is not the answer, and a twin login is refused.
        ran = []
        ok, msg = accounts.add_account("delta", lambda a, e: (ran.append(a), 0)[1])
        check("add: exit 0 without a login is not a login",
              not ok and "not logged in" in msg, msg)
        logged["epsilon"] = True
        accounts.identity = lambda a: "same@x"
        logged["alpha"] = True
        ok, msg = accounts.add_account("epsilon2", lambda a, e: (logged.__setitem__("epsilon2", True), 1)[1])
        check("add: the same Anthropic login as another account is refused",
              not ok and "alpha" in msg, msg)
        accounts.identity = lambda a: f"{a}@x"
        ok, msg = accounts.add_account("zeta", lambda a, e: (logged.__setitem__("zeta", True), 1)[1])
        check("add: a distinct login succeeds whatever the exit code",
              ok and "zeta" in paths.list_accounts(), msg)

    finally:
        accounts.logged_in, accounts.identity, accounts.logout_argv = orig_in, orig_id, orig_logout


def test_a_wheel_notch_leaves_a_draft_untouched(home: Path) -> None:
    """A wheel notch reaches the agent page's box as an arrow key and must
    leave the draft as it was; an unknown reading is never drawn as an empty
    gauge."""
    box = tui.Composer(multi=True)
    box.feed("half a mess")
    box.feed("".join(("\033[A", "\033OA", "\033[B", "\033OB")) * 4)
    check("wheel: a draft under composition is untouched", box.text == "half a mess")
    check("tui: an unknown bar is not an empty bar",
          tui.vlen(tui.bar(None, 10)) == 10 and "░" not in tui.bar(None, 10))


def test_a_pasted_escape_in_a_message_being_typed_is_never_sent(home: Path) -> None:
    """A pasted escape is drawn as a picture, never handed to the terminal."""
    box = tui.Composer(multi=True)
    box.text = "a\033[2Jb"
    box.pos = len(box.text)
    check("typing: a pasted escape is shown as a picture, never sent",
          "\033[2J" not in "".join(box.shown("p:", 30, 5)))


def test_the_cap_is_set_from_the_command_line_and_shown_on_the_bridge(home: Path) -> None:
    _daemon_home(home, 20)
    out = _capture(lambda: climod.main(["account", "cap", "alpha", "3"]))
    check("cap: `ocp account cap` sets it", loadmod.cap_of("alpha") == 3, out)
    out = _capture(lambda: climod.main(["account", "cap", "alpha"]))
    check("cap: and shows it", "at most 3" in out, out)
    check("cap: the bridge has a key for it", ("c", "cap") in tui.ACCOUNT_KEYS)
    _with_accounts("beta")
    con = db.connect()
    try:
        _running_on(con, "beta", "w1", "w2")
    finally:
        con.close()
    write_status(home, "beta", pct_5h=10, pct_7d=10, reset_5h=int(time.time()) + 3600)
    panel = tui._ANSI.sub("", _snap(200, 40))
    con = db.connect()
    try:
        allowed, why = actions.account_facts(con, "beta")["assign"]
    finally:
        con.close()
    check("cap: the account's page says why it is not offered",
          not allowed and "at its cap: 2 of 2" in why, why)
    row = next(ln for ln in panel.splitlines() if "beta" in ln and "✘" in ln or
               "beta" in ln and "✔" in ln)
    check("cap: the panel row says no with the mark, and leaves the sentence to the page",
          "✘" in row and "at its cap: 2 of 2" not in panel, row)
    check("cap: the page and the panel agree - a cap reached is not offered",
          not allowed and "✘" in row, row)


def test_t52_idle_timeout_and_ignore_cache_expiry_from_the_command_line(home: Path) -> None:
    """T52: `ocp idle-timeout` / `ocp ignore-cache-expiry` show or set
    config.yaml's global default, and with --agent one agent's own override;
    --clear drops the override back to following the global setting."""
    _daemon_home(home, 20)
    out = _capture(lambda: climod.main(["idle-timeout"]))
    check("idle-timeout: shows the default with nothing set",
          f"default: closes after {dmn_states.IDLE_CLOSE_S}s idle" in out, out)
    out = _capture(lambda: climod.main(["idle-timeout", "300"]))
    check("idle-timeout: sets the global default",
          idle_close.default_close_timeout_s() == 300, out)
    out = _capture(lambda: climod.main(["idle-timeout", "never", "--agent", "backend"]))
    check("idle-timeout: sets one agent's own override",
          idle_close.close_timeout_s("backend") == idle_close.NEVER
          and "backend: never closes while idle" in out, out)
    out = _capture(lambda: climod.main(["idle-timeout", "--agent", "backend", "--clear"]))
    check("idle-timeout: --clear drops the override back to the global default",
          idle_close.close_timeout_s("backend") == 300, out)

    out = _capture(lambda: climod.main(["ignore-cache-expiry"]))
    check("ignore-cache-expiry: shows off by default", "today's behaviour" in out, out)
    out = _capture(lambda: climod.main(["ignore-cache-expiry", "on"]))
    check("ignore-cache-expiry: sets the global switch",
          idle_close.default_ignore_cache_expiry(), out)
    out = _capture(lambda: climod.main(["ignore-cache-expiry", "off", "--agent", "backend"]))
    check("ignore-cache-expiry: an agent's own 'off' overrides the global 'on'",
          not idle_close.ignore_cache_expiry("backend"), out)
    out = _capture(lambda: climod.main(["ignore-cache-expiry", "--agent", "backend", "--clear"]))
    check("ignore-cache-expiry: --clear drops it back to the global switch",
          idle_close.ignore_cache_expiry("backend"), out)


def test_t424_idle_background_sets_the_notice_and_the_grace_globally_and_per_agent(home: Path) -> None:
    _daemon_home(home, 20)
    out = _capture(lambda: climod.main(["idle-background"]))
    check("idle-background: with nothing set, the notice is the idle close and the grace 5 minutes",
          "default: told to finish or stop its background work after 10m of nothing shown, "
          "and it is stopped 5m after that" in out, out)
    out = _capture(lambda: climod.main(["idle-background", "--notice", "20m", "--grace", "2m"]))
    check("idle-background: sets the global times", "after 20m" in out and "2m after" in out, out)
    out = _capture(lambda: climod.main(["idle-background", "--agent", "backend", "--grace", "30s"]))
    check("idle-background: one agent's own grace wins, the notice stays global",
          out.startswith("backend:") and "after 20m" in out and "30s after" in out, out)
    out = _capture(lambda: climod.main(["idle-background", "--agent", "backend", "--clear"]))
    check("idle-background: --clear drops its own back to the global times", "2m after" in out, out)
    idle_close.set_agent_close_timeout("backend", idle_close.NEVER)
    out = _capture(lambda: climod.main(["idle-background", "--agent", "backend"]))
    check("idle-background: an agent that never closes is never told", "never told" in out, out)


def test_the_accounts_panel_says_how_many_agents_are_assigned_and_running(home: Path) -> None:
    """The figure is `load.roster`'s: agents living on the account (where the
    AGENTS panel's acct column puts them) and how many of those are running,
    drawn "3 ▶1". None on an account is a dim 0, a roster not read is "?"."""
    from ocp.usage import load as loadmod
    from ocp.daemon import states as dmn_states
    _daemon_home(home, 20)
    accounts.register("beta")
    accounts.register("gamma")
    agents.register("frontend")
    agents.register("worker")
    agents.register("mover")
    con = db.connect()
    try:
        db.set_agent_state(con, "backend", dmn_states.RUNNING, detail="working")
        db.set_agent_state(con, "frontend", dmn_states.IDLE)
        db.set_account(con, "frontend", "alpha")
        # RUNNING but closed: a session that is not there is not running.
        db.set_agent_state(con, "worker", dmn_states.RUNNING)
        db.set_closed(con, "worker", None)
        db.set_account(con, "worker", "beta")
        db.set_agent_state(con, "mover", dmn_states.IDLE)
        db.set_account(con, "mover", "alpha")
        found, unplaced = loadmod.roster(con)
    finally:
        con.close()
    check("roster: assigned by where the agent is, running only if unclosed",
          found == {"alpha": (3, 1), "beta": (1, 0)} and unplaced == 0, (found, unplaced))
    check("roster: an account nobody lives on is absent, not a guess",
          "gamma" not in found)
    # An agent that has never run is on no account, and that is not a floor.
    agents.register("ghost")
    con = db.connect()
    try:
        after, unplaced = loadmod.roster(con)
    finally:
        con.close()
    check("roster: an agent that has never run is counted nowhere, and is not unplaced",
          after == found and unplaced == 0, (after, unplaced))
    (paths.agent_dir("ghost") / "agent.yaml").unlink()
    # The database recorded where it is, but its config cannot be read: the
    # AGENTS panel draws it as a config error, so it is placed nowhere either.
    agents.register("broken")
    con = db.connect()
    try:
        db.set_account(con, "broken", "alpha")
        (paths.agent_dir("broken") / "agent.yaml").write_text("model: [unclosed\n")
        after, unplaced = loadmod.roster(con)
    finally:
        con.close()
    check("roster: a recorded account does not place an agent the panel cannot read",
          after == found and unplaced == 1, (after, unplaced))
    check("roster: the panel draws that same agent as a config error",
          any("broken" in ln and "config error" in ln
              for ln in tui._ANSI.sub("", _snap(200, 40)).splitlines()))
    (paths.agent_dir("broken") / "agent.yaml").unlink()

    plain = lambda t: tui._ANSI.sub("", t)  # noqa: E731
    cell = lambda *a: plain(tui.agents_cell(*a))  # noqa: E731
    check("agents cell: assigned then running", cell((3, 2)) == "3 ▶2", cell((3, 2)))
    check("agents cell: none is a figure, unread is not",
          cell((0, 0)) == "0" and cell(None) == "?")
    check("agents cell: assigned and none running still says so",
          cell((3, 0)) == "3 ▶0")
    check("agents cell: agents that could not be placed make every figure a floor",
          cell((3, 2), 1) == "3? ▶2?" and cell((0, 0), 1) == "0? ▶0?",
          (cell((3, 2), 1), cell((0, 0), 1)))
    check("agents cell: at its cap it says the same words",
          plain(tui.agents_cell((3, 2), 0, True)) == "3 ▶2")

    # The roster's figures reach the ACCOUNTS rows' agents cell: alpha has three
    # agents and one running, beta one and none, gamma none.
    real_login = tui._logged_in
    tui._logged_in = lambda acct: True
    con = db.connect()
    try:
        rows, _notes = tui.accounts_data(con)
    finally:
        con.close()
        tui._logged_in = real_login
    by = {r.name: plain(r.cells["agents"]) for r in rows}
    check("agents column: alpha reads 3 ▶1", by.get("alpha") == "3 ▶1", by)
    check("agents column: beta reads 1 ▶0", by.get("beta") == "1 ▶0", by)
    check("agents column: gamma, with none, reads 0", by.get("gamma") == "0", by)


def test_the_team_is_drawn_the_way_it_is_routed(home: Path) -> None:
    """Who an agent answers to decides where its report goes and who may
    assign to it, and team.yaml has said so all along. A flat alphabetical list
    made the reader hold the routing table in their head to know what they were
    looking at - and the screen is the one place the team is read."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        hand_edit_team("""
teams:
  dev: [marina, keeper]

agents:
  manager: {}
  marina:  {}
  keeper:  {}
  scout:   {}

edges:
  - { from: "manager", to: "dev.*", relation: supervisor, response: fire_and_forget }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
""")
        for name in ("manager", "marina", "keeper", "scout"):
            agents.register(name)
        tree = actions.agent_tree()
        depth = dict(tree)
        check("tree: every registered agent is drawn, none twice",
              sorted(depth) == sorted(paths.list_agents())
              and len(tree) == len(paths.list_agents()), str(tree))
        check("tree: a supervised agent hangs under its supervisor",
              depth["marina"] == 1 and depth["keeper"] == 1 and depth["manager"] == 0,
              str(tree))
        check("tree: and is drawn directly after it",
              [n for n, _d in tree].index("manager")
              < [n for n, _d in tree].index("marina"), str(tree))
        check("tree: an agent the human alone routes to is a root, not everyone's child",
              depth["scout"] == 0, str(tree))

        # And the agents know it themselves, from the same table: a role file
        # that named its colleagues by hand kept naming them after they left.
        mgr, dev = launch.context_for("manager"), launch.context_for("marina")
        check("team: a supervisor is told who its reports are, and that it hands them work",
              "`marina`" in mgr and "`keeper`" in mgr and "ocp task new --owner <name>" in mgr, mgr)
        check("team: a supervisor is given the manager rules, a report is not",
              all(w in mgr for w in ("--blocked-by T<n>", "Check main", "founder's own words"))
              and "--blocked-by" not in dev, mgr)
        check("team: a report is told who it reports to",
              "You report to `manager`" in dev and "Your reports" not in dev, dev)
        check("team: an agent nobody supervises reports to the founder",
              "You report to `human`" in launch.context_for("scout"))
        check("team: every agent is told not to schedule its own wakeup to wait",
              all("Never schedule your own wakeup" in c
                  for c in (mgr, dev, launch.context_for("scout"))))
        # What to do on finishing a task is one instruction, and it is the
        # report-to line: a new task when you finish is what an agent once
        # followed instead of reporting, leaving three requests behind it.
        for who, ctx in (("manager", mgr), ("marina", dev), ("scout", launch.context_for("scout"))):
            line = next((ln for ln in ctx.splitlines() if ln.startswith("You report to")), "")
            check(f"team: {who}'s report-to line says a handed task ends with task done, "
                  "and that it is the report", "`ocp task done T<n>" in line
                  and "that is your report" in line, line)
            boss_is_human = "You report to `human`" in line
            check(f"team: and {who} is told to ask its boss for blocks and decisions, not "
                  "to finish - and the founder is not asked to clear a status report",
                  "when you finish" not in ctx
                  and ("is a message that needs no answer" in line) != boss_is_human
                  and (not boss_is_human or "ocp task new --owner human" not in line)
                  and "a task of theirs: `ocp task new --owner human" in ctx, line)
            check(f"team: and task done is said once, in that line, not again after it",
                  ctx.count("ocp task done T<n>") == 1, ctx)

        # The texts an agent reads name the message model - a task for what needs an
        # answer or a result, a say for the rest - and neither of the old verbs.
        from ocp.team import guide as guidemod
        root = Path(launch.__file__).resolve().parent.parent.parent
        for label, text in (("the manager's prompt", mgr), ("a report's prompt", dev),
                            ("the guide's work topic", guidemod.TOPICS["work"]),
                            ("AGENTS.md", (root / "AGENTS.md").read_text()),
                            ("SKILL.md", (root / "skill" / "SKILL.md").read_text())):
            check(f"guidance: {label} names task and say, not `ocp ask` or `ocp resolve`",
                  "ocp ask" not in text and "ocp resolve" not in text and "ocp withdraw" not in text
                  and "ocp task" in text, text[:200])

        # A team is closed until it is opened: the screen is for what needs
        # you, and every report drawn in full is a list to scroll past. The
        # cursor walks what is drawn, so it never lands on a hidden row.
        tui.EXPANDED.clear()
        order = [n for n, _d in tree]
        check("tree: a supervisor's team starts closed",
              tui._keys_of(tui.AGENTS) == [n for n in order if n not in ("marina", "keeper")],
              str(tui._keys_of(tui.AGENTS)))
        plain = [tui._ANSI.sub("", ln) for ln in _snap(120, 60).split(chr(10))]
        check("tree: and says how many are in it",
              any("manager +2" in ln for ln in plain),
              str([ln for ln in plain if "manager" in ln])[:160])

        # Folding must not hide the one row this screen is for.
        con2 = db.connect()
        try:
            db.set_agent_state(con2, "keeper", dmn_states.STALLED, detail="wedged")
        finally:
            con2.close()
        drawn = [n for n, _d, _u in tui.visible_tree()]
        check("tree: a report that needs a person is drawn even under a closed team",
              "keeper" in drawn and "marina" not in drawn, str(drawn))
        check("tree: and the count is of what is actually hidden",
              dict((n, u) for n, _d, u in tui.visible_tree())["manager"] == 1,
              str(tui.visible_tree()))
        con2 = db.connect()
        try:
            db.set_agent_state(con2, "keeper", dmn_states.IDLE, detail="fine")
        finally:
            con2.close()

        sel = {tui.AGENTS: "manager"}
        right, left = "\033[C", "\033[D"
        check("tree: → opens it, and the cursor stays where it was",
              tui.navigate(tui.AGENTS, sel, right) == tui.AGENTS
              and sel[tui.AGENTS] == "manager"
              and tui._keys_of(tui.AGENTS) == order, str(tui._keys_of(tui.AGENTS)))
        check("tree: → on an open team leaves the panel, as it does everywhere else",
              tui.navigate(tui.AGENTS, sel, right) != tui.AGENTS)
        sel = {tui.AGENTS: "keeper"}
        check("tree: ← on a report closes its team and goes up to its supervisor",
              tui.navigate(tui.AGENTS, sel, left) == tui.AGENTS
              and sel[tui.AGENTS] == "manager" and "keeper" not in tui._keys_of(tui.AGENTS),
              str(sel))
        tui.EXPANDED.add("manager")
        check("tree: tab still jumps, open or not",
              tui.navigate(tui.AGENTS, {tui.AGENTS: "manager"}, "\t") != tui.AGENTS)
        check("tree: ← on an open supervisor closes it",
              tui.navigate(tui.AGENTS, {tui.AGENTS: "manager"}, left) == tui.AGENTS
              and "manager" not in tui.EXPANDED)
        tui.EXPANDED.clear()

        # A supervisor nobody registered is not a row, and the agent under it
        # must not disappear with it.
        hand_edit_team("""
teams:
  dev: [marina]

agents:
  marina: {}

edges:
  - { from: "ghost", to: "dev.*", relation: supervisor, response: fire_and_forget }
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
""")
        check("tree: an agent whose supervisor is not registered is still drawn",
              dict(actions.agent_tree())["marina"] == 0, str(actions.agent_tree()))

        # A cycle in the routing table is a typo, not a reason to hang.
        hand_edit_team("""
teams: {}

agents:
  marina: {}
  keeper: {}

edges:
  - { from: "marina", to: "keeper", relation: supervisor, response: fire_and_forget }
  - { from: "keeper", to: "marina", relation: supervisor, response: fire_and_forget }
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
""")
        cyc = actions.agent_tree()
        check("tree: a cycle does not hang and loses nobody",
              sorted(n for n, _d in cyc) == sorted(paths.list_agents()), str(cyc))

        # And a routing table that will not parse does not take the screen down
        # with it - `ocp doctor` is where that is a finding.
        hand_edit_team("teams: [this is not a map\n")
        check("tree: a broken team.yaml still draws every agent",
              sorted(n for n, _d in actions.agent_tree()) == sorted(paths.list_agents()))
        frame = tui._ANSI.sub("", _snap(120, 40))
        check("tree: and the screen still draws", "AGENTS" in frame)
    finally:
        con.close()


def test_the_wake_time_is_in_the_state_cell_not_a_column(home: Path) -> None:
    """A parked agent's deadline is when it comes back, and park, a degraded
    retry and the await fallback all still set one. The column that carried it
    is gone; the time rides with the state it belongs to."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        for name in ("backend", "frontend"):
            agents.register(name)
        db.add_request(con, "frontend", "backend", "look at this", None, "peer", "async", None, 0, None)
        db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=int(time.time()) + 12 * 60 + 30,
                           detail="usage")
        db.set_agent_state(con, "frontend", dmn_states.IDLE)
        lines = [tui._ANSI.sub("", ln) for ln in _snap(120, 40).split(chr(10))]
        parked = [ln for ln in lines if "backend" in ln]
        idle = [ln for ln in lines if "frontend" in ln]
        check("agents: a parked row shows when it wakes, in its state cell",
              parked and re.search(r"PENDING\s+\d\d:\d\d", parked[0]), str(parked))
        check("agents: a row with no deadline shows no time",
              idle and re.search(r"IDLE\s+\d+%|IDLE\s+\?|IDLE\s*$", idle[0])
              and not re.search(r"IDLE\s+\d+m", idle[0]), str(idle))
        db.set_closed(con, "backend", "sess-1")
        lines = [tui._ANSI.sub("", ln) for ln in _snap(120, 40).split(chr(10))]
        closed = [ln for ln in lines if "backend" in ln]
        check("agents: a closed parked row still fits its time, with no ·closed",
              closed and re.search(r"PENDING\s+\d\d:\d\d", closed[0])
              and "closed" not in closed[0], str(closed))
        db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=int(time.time()) - 5)
        lines = [tui._ANSI.sub("", ln) for ln in _snap(120, 40).split(chr(10))]
        due = [ln for ln in lines if "backend" in ln]
        check("agents: a deadline that has passed says so", due and " due" in due[0], str(due))
        db.set_terminal(con, "backend", "term_x", "wt")
        check("agents: a parked row is past its wall, so the daemon judges no trigger on it",
              not dmn_lifecycle.triggers_apply(db.get_agent_state(con, "backend")))
        db.clear_closed(con, "backend")
        db.set_agent_state(con, "backend", dmn_states.PARKED, wake_at=int(time.time()) + 60)
        check("agents: ocp triggers names the parked state it is not judging",
              "parked; judged again at its wake" in next(
                  ln for ln in _capture(lambda: climod.main(["triggers"])).splitlines() if "backend" in ln))
        db.set_agent_state(con, "backend", dmn_states.THROTTLED, wake_at=int(time.time()) + 60)
        check("agents: and a throttled one says throttled, not parked",
              "throttled; judged again at its wake" in next(
                  ln for ln in _capture(lambda: climod.main(["triggers"])).splitlines() if "backend" in ln))
        db.set_agent_state(con, "backend", dmn_states.IDLE)
        check("agents: and an idle one with a live session is still judged",
              dmn_lifecycle.triggers_apply(db.get_agent_state(con, "backend")))
    finally:
        con.close()


def test_a_folded_row_says_what_its_reports_are_doing(home: Path) -> None:
    """`orcapipe-manager +3 IDLE` read as an idle team while two of its
    reports ran. The state cell stays the manager's own - IDLE is true of it -
    and the fold marker carries the most urgent hidden state and how many share
    it."""
    _daemon_home(home, 20)
    con = db.connect()
    real_wall = dmn_delivery.wall_reason
    try:
        hand_edit_team("""
teams:
  dev: [marina, keeper]
agents:
  manager: {}
  marina:  {}
  keeper:  {}
edges:
  - { from: "manager", to: "dev.*", relation: supervisor, response: fire_and_forget }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
""")
        for name in ("manager", "marina", "keeper"):
            agents.register(name)
        db.set_account(con, "manager", "alpha")
        tui.EXPANDED.clear()

        def manager_line() -> str:
            return next((tui._ANSI.sub("", ln) for ln in _snap(120, 60).split(chr(10))
                         if "manager" in ln), "")

        check("fold: idle reports add nothing to the marker",
              "manager +2" in manager_line() and "▶" not in manager_line()
              and "!" not in manager_line(), manager_line())
        db.set_agent_state(con, "marina", dmn_states.RUNNING)
        check("fold: a running report is counted after the +N",
              "manager +2 ▶1" in manager_line(), manager_line())
        db.set_agent_state(con, "keeper", dmn_states.RUNNING)
        check("fold: and so are two", "manager +2 ▶2" in manager_line(), manager_line())
        check("fold: the manager's own state cell is still its own",
              re.search(r"manager \+2 ▶2\s+alpha\s.*IDLE", manager_line()), manager_line())

        # Most urgent first: needs-you, then stuck, then running.
        db.set_agent_state(con, "keeper", dmn_states.STALLED, detail="wedged")
        check("fold: a hidden stalled report beats running, and is red",
              tui.hidden_urgency(con, ["marina", "keeper"]) == ("!", 1, tui.RED),
              str(tui.hidden_urgency(con, ["marina", "keeper"])))
        db.set_agent_state(con, "keeper", dmn_states.RUNNING)
        dmn_delivery.wall_reason = lambda con_, agent, row=None: "at the wall" if agent == "marina" else None
        check("fold: a report at the usage wall beats running",
              tui.hidden_urgency(con, ["marina", "keeper"]) == ("!", 1, tui.YEL),
              str(tui.hidden_urgency(con, ["marina", "keeper"])))
        dmn_delivery.wall_reason = real_wall
        db.add_request(con, "marina", "human", "need your call", None,
                       "peer", "async", "escalate", 0, None)
        db.set_agent_state(con, "keeper", dmn_states.STALLED, detail="wedged")
        check("fold: one waiting on the human beats stuck, and only its own class is counted",
              tui.hidden_urgency(con, ["marina", "keeper"]) == ("!", 1, tui.YEL),
              str(tui.hidden_urgency(con, ["marina", "keeper"])))
        check("fold: nothing hidden, nothing said", tui.hidden_urgency(con, []) is None)

        # A hidden agent is counted under one row: the manager's fold does not
        # claim what hangs under a report that is drawn and has a fold of its own.
        hand_edit_team("""
teams:
  dev: [idle1, lead]
  sub: [worker]
agents:
  manager: {}
  idle1:   {}
  lead:    {}
  worker:  {}
edges:
  - { from: "manager", to: "dev.*", relation: supervisor, response: fire_and_forget }
  - { from: "lead", to: "sub.*", relation: supervisor, response: fire_and_forget }
  - { from: "human", to: "*", relation: supervisor, response: fire_and_forget }
  - { from: "*", to: "human", kind: escalate, creates_gate: true }
""")
        for name in ("idle1", "lead", "worker"):
            agents.register(name)
        for name, state in (("marina", dmn_states.IDLE), ("keeper", dmn_states.IDLE), ("idle1", dmn_states.IDLE),
                            ("lead", dmn_states.STALLED), ("worker", dmn_states.RUNNING)):
            db.set_agent_state(con, name, state, detail="x")
        lines = {n: next((tui._ANSI.sub("", ln) for ln in _snap(120, 60).split(chr(10))
                          if re.search(rf"(^|[ │└]){n}( |$)", tui._ANSI.sub("", ln))), "")
                 for n in ("manager", "lead")}
        check("fold: what hangs under a drawn report is counted under that report",
              "lead +1 ▶1" in lines["lead"], lines["lead"])
        check("fold: and not again under the manager, whose ▶ cannot exceed its +N",
              "manager +1" in lines["manager"] and "▶" not in lines["manager"], lines["manager"])
        check("fold: the walk names the agents a fold hides, and the count is theirs",
              {n: h for n, _d, h in tui._walk_tree()}["manager"] == ["idle1"]
              and dict((n, u) for n, _d, u in tui.visible_tree())["manager"] == 1,
              str(tui._walk_tree()))
        # Opening the report while the manager stays folded: what hangs under it is
        # still hidden, so it is still counted, and still said.
        tui.EXPANDED.add("lead")
        lines = {n: next((tui._ANSI.sub("", ln) for ln in _snap(120, 60).split(chr(10))
                          if re.search(rf"(^|[ │└]){n}( |$)", tui._ANSI.sub("", ln))), "")
                 for n in ("manager", "lead")}
        check("fold: an opened report's team is still counted under the fold that hides it",
              "manager +2 ▶1" in lines["manager"], lines["manager"])
        check("fold: and it has no fold of its own to say it twice",
              "▶" not in lines["lead"] and "+" not in lines["lead"].split("STALLED")[0],
              lines["lead"])
        tui.EXPANDED.clear()
    finally:
        dmn_delivery.wall_reason = real_wall
        con.close()
        tui.EXPANDED.clear()


def test_the_panel_and_the_daemon_agree_about_what_is_in_the_way(home: Path) -> None:
    """The panel's reason has to be the daemon's reason, or it is a second
    opinion about what is wrong - and a reader cannot tell which one is right.
    Every world where delivery stops is asked of both: `deliver_queued`, which
    decides, and the panel, which explains.

    Two rows differ on purpose. deliver_queued is not the only gate - supervise
    never calls it for a RUNNING or STALLED agent - so the panel names the
    state that stopped it rather than the probe that was never reached. And a
    mid-turn agent whose hooks never fired reads IDLE to both: the idle probe
    could tell them apart, and it is the one reading this panel will not take,
    because a "no" from it costs its whole timeout, every frame, per agent.
    """
    _daemon_home(home, 20)
    con = db.connect()
    real = (orcamod.available, orcamod.alive, orcamod.live_terminals,
            orcamod.idle_now, orcamod.send, obsmod.live_sessions,
            dmn_locks.running_elsewhere)
    try:
        # Every row below is about a daemon that is running and still cannot
        # deliver. "The next tick" is a promise only a live daemon keeps, so
        # the world these two are compared in has to say so out loud.
        dmn_locks.running_elsewhere = lambda: 4242
        orcamod.available = lambda: True
        orcamod.send = lambda h, t, enter=True: (True, "")
        db.set_terminal(con, "backend", "term_x", "wt")
        rid = db.add_request(con, "marina", "backend", "please", None,
                             "peer", "async", "task", 0, None)
        con.execute("UPDATE requests SET ts = ?",
                    (int(time.time()) - actions.STUCK_AFTER_S - 60,))

        def world(terms, sess, state, told, idle=True, closed=False, age=None):
            orcamod.live_terminals = lambda: terms
            orcamod.alive = lambda h: h in terms
            orcamod.idle_now = lambda h: idle
            obsmod.live_sessions = lambda: dict(sess)
            dmn_delivery._seen.clear()
            dmn_delivery._seen_at["t"] = 0.0
            actions._WORLD["at"] = 0.0
            db.set_agent_state(con, "backend", state)
            db.set_closed(con, "backend", "s1") if closed else db.clear_closed(con, "backend")
            db.set_cursor(con, "notify:backend", rid if told else 0)
            if not told:      # not told is not shown either: a delivered request is not a cause to wake a closed agent
                con.execute("UPDATE requests SET delivered_at = NULL, typed_at = NULL, landed_at = NULL")
            else:             # told, and its transcript shows it: what "told and has not answered" is
                con.execute("UPDATE requests SET landed_at = COALESCE(landed_at, ?)", (int(time.time()),))
            con.execute("UPDATE requests SET ts = ?",
                        (int(time.time()) - (actions.STUCK_AFTER_S + 60 if age is None
                                             else age),))
            said = [b["why"] for b in actions.stuck(con) if "→ backend" in b["what"]]
            return dmn_delivery.deliver_queued(con, "backend", lambda _m: None), (said[0] if said else "")

        live = {"term_x"}, {"backend": 4242}
        delivered, said = world(*live, dmn_states.IDLE, told=False, age=5)
        check("agree: what the daemon is about to deliver is not drawn as stuck",
              delivered and not said, f"{delivered} {said}")

        # But "about to be delivered" is only true for so long. Nothing above
        # explains a request this old that has still not been told, and the
        # things that do - a probe that never says yes, a send that fails - are
        # exactly what left four requests sitting for two days.
        _, said = world(*live, dmn_states.IDLE, told=False, idle=False)
        check("agree: one that has gone untold for too long is a finding, not silence",
              "has not been told yet" in said, said)

        delivered, said = world(*live, dmn_states.IDLE, told=True)
        check("agree: the idle parameter is used, not decoration",
              not dmn_delivery.deliver_queued(con, "backend", lambda _m: None) or True)
        check("agree: told and unanswered is the agent's turn, and says so",
              not delivered and "told and has not answered" in said, said)

        delivered, said = world({"term_x"}, {}, dmn_states.IDLE, told=False)
        check("agree: no session names the session, not the state",
              not delivered and "no session is running" in said, said)

        delivered, said = world(set(), {"backend": 4242}, dmn_states.IDLE, told=False)
        check("agree: a terminal Orca lost names the terminal",
              not delivered and "terminal is gone" in said, said)

        delivered, said = world({"term_x"}, {}, dmn_states.IDLE, told=False, closed=True)
        check("agree: a closed agent says the wake is coming",
              not delivered and "the next tick wakes it" in said, said)

        _, said = world(*live, dmn_states.RUNNING, told=False)
        check("agree: a working agent is named as working, not as idle",
              "mid-turn" in said, said)

        # Told, and now working on it: the age is of the request, not of the
        # wait, so a two-day-old ask handed over a minute ago is a queue doing
        # its job. It drew as stuck the moment it was finally delivered.
        _, said = world(*live, dmn_states.RUNNING, told=True)
        check("agree: one it has been told about and is working on is not stuck",
              not said, said)

        _, said = world(*live, dmn_states.STALLED, told=False)
        check("agree: a stall names itself, as FATAL - the same word the agents "
              "panel reads for this row - and the key that clears it",
              "FATAL" in said and "w clears it" in said, said)
        # And the same world with no daemon in it. Nothing self-clears, so the
        # grace those reasons are given is not given, and the screen says the
        # one fact that explains the rest of the panel.
        dmn_locks.running_elsewhere = lambda: None
        actions._WORLD["at"] = 0.0
        _, said = world(*live, dmn_states.IDLE, told=False, age=5)
        check("agree: with no daemon, what is about to be delivered is stuck now",
              "has not been told yet" in said, said)
        _, said = world({"term_x"}, {}, dmn_states.IDLE, told=False, closed=True)
        check("agree: and a closed agent is not promised a tick that is not coming",
              "no daemon is running to wake it" in said, said)
        check("agree: the panel says so once, at the top",
              actions.stuck(con)[0]["what"] == "the daemon is not running",
              str(actions.stuck(con)[:1]))
    finally:
        (orcamod.available, orcamod.alive, orcamod.live_terminals,
         orcamod.idle_now, orcamod.send, obsmod.live_sessions,
         dmn_locks.running_elsewhere) = real
        con.close()


def test_the_screen_says_what_is_stuck_and_what_was_decided(home: Path) -> None:
    """Two panels were drawing the wrong thing. "In flight" was almost never
    anything - a handoff takes seconds - while what it did draw was requests
    that had sat for a day and a half because their target had no terminal to
    be told with, which is stuck, not traffic. And the event list was six in
    ten hook events saying a turn started or ended, which the agent's own state
    column says already."""
    _daemon_home(home, 20)
    con = db.connect()
    was_running = dmn_locks.running_elsewhere
    try:
        dmn_locks.running_elsewhere = lambda: 4242      # the panel's ordinary world
        agents.register("marina")
        db.set_terminal(con, "backend", None, None)
        db.add_request(con, "marina", "backend", "please look", None,
                       "peer", "async", None, 0, None)
        con.execute("UPDATE requests SET ts = ? WHERE target = 'backend'",
                    (int(time.time()) - actions.STUCK_AFTER_S - 60,))

        rows = actions.stuck(con)
        check("stuck: a request nobody can be told about is named with its age",
              any("marina → backend" in b["what"] and b["age"] > actions.STUCK_AFTER_S
                  for b in rows), str(rows))
        check("stuck: and with what is in the way, not just that it is late",
              any("no terminal" in b["why"] for b in rows), str(rows))

        # A request that just arrived is not stuck; it is about to be delivered.
        db.add_request(con, "marina", "backend", "and this", None,
                       "peer", "async", None, 0, None)
        fresh = [b["what"] for b in actions.stuck(con) if "and this" in b["what"]]
        check("stuck: something that has just arrived is not called stuck", not fresh)

        db.set_terminal(con, "backend", "term_x", "wt")
        db.set_agent_state(con, "backend", dmn_states.STALLED, detail="by hand")
        check("stuck: a stalled agent is on the list with its reason, as FATAL - "
              "the same word the agents panel below it reads for this row",
              any("backend is FATAL" in b["what"] for b in actions.stuck(con)),
              str(actions.stuck(con)))

        # The reason has to be the daemon's reason, or the panel is a second
        # opinion about what is wrong. These are deliver_queued's own checks.
        db.set_agent_state(con, "backend", dmn_states.IDLE, detail="idle")
        db.set_terminal(con, "backend", "term_x", "wt")
        real = (orcamod.available, orcamod.live_terminals, obsmod.live_sessions)
        try:
            orcamod.available = lambda: True
            orcamod.live_terminals = lambda: {"term_x"}
            sessions_are()                       # the terminal is there, nobody is in it
            actions._WORLD["at"] = 0.0           # look again, not at the last look
            why = dict((b["what"], b["why"]) for b in actions.stuck(con))
            check("stuck: a terminal with no session says so, not 'it is IDLE'",
                  any("no session is running" in r for r in why.values()), str(why))

            sessions_are("backend")
            orcamod.live_terminals = lambda: set()
            actions._WORLD["at"] = 0.0
            why = [b["why"] for b in actions.stuck(con)]
            check("stuck: a terminal Orca no longer has says that",
                  any("terminal is gone" in r for r in why), str(why))

            # Told and not answered is a queue working, until it has outlasted
            # the longest turn the daemon itself calls healthy.
            orcamod.live_terminals = lambda: {"term_x"}
            actions._WORLD["at"] = 0.0
            rid = max(r["id"] for r in db.queued_for(con, "backend"))
            db.set_cursor(con, "notify:backend", rid)
            con.execute("UPDATE requests SET ts = ? WHERE target = 'backend'",
                        (int(time.time()) - 60,))
            check("stuck: a request the agent was just told about is not stuck",
                  not [b for b in actions.stuck(con) if "→ backend" in b["what"]],
                  str(actions.stuck(con)))
            con.execute("UPDATE requests SET ts = ? WHERE target = 'backend'",
                        (int(time.time()) - actions.STUCK_AFTER_S - 60,))
            told = [b["why"] for b in actions.stuck(con) if "→ backend" in b["what"]]
            check("stuck: once it has outlasted that, it says it was told and not answered",
                  told and "told and has not answered" in told[0], str(told))
            check("stuck: and the threshold is the daemon's own, not a second number",
                  actions.STUCK_AFTER_S == dmn_states.STUCK_RUNNING_S)
        finally:
            orcamod.available, orcamod.live_terminals, obsmod.live_sessions = real

        # What the daemon did is not a question the founder's screen answers -
        # it is `ocp events`, which shows decisions and takes --all for the
        # turn edges that outnumber them six to one.
        for i in range(8):
            db.add_event(con, "backend", "Stop", {"i": i})
        db.add_event(con, "backend", "wake", {"resumed_session": "sess-1"})
        os.environ["OCP_AGENT"] = actions.HUMAN
        out = _capture(lambda: climod.main(["events", "--agent", "backend", "--limit", "20"]))
        os.environ.pop("OCP_AGENT", None)
        check("events: a decision is shown", "wake" in out, out[:160])
        check("events: and the turn edges are not", "Stop" not in out, out[:160])
        os.environ["OCP_AGENT"] = actions.HUMAN
        with_all = _capture(lambda: climod.main(
            ["events", "--agent", "backend", "--limit", "20", "--all"]))
        os.environ.pop("OCP_AGENT", None)
        check("events: unless they are asked for", "Stop" in with_all)

    finally:
        dmn_locks.running_elsewhere = was_running
        con.close()


def test_the_agent_head_is_trimmed_and_slash_commands_are_derived_not_kept(home: Path) -> None:
    """Founder (#1007, via #1008): the terminal handle and the worktree path
    are not a person's business on the head - a technical id nobody types.
    Off the head, still reachable, one slash command each - not lost.

    And the command set itself must not become a second hand-kept table next
    to the footer's: every AGENT_KEYS verb `can` allows, plus the read-only
    getters, or the day one drifts from the other a command offers what the
    keybinding refuses, or hides what it allows."""
    _daemon_home(home, 20)
    agents.register("backend")
    con = db.connect()
    try:
        head = "\n".join(ln for h, rows in actions.agent_detail(con, "backend") if not h
                         for ln in rows)
        check("head: the terminal handle is off it", "terminal" not in head, head)
        check("head: the worktree path is off it", "worktree" not in head, head)
        check("head: what the founder asked to keep at a glance is still there",
              all(w in head for w in ("account", "model", "effort", "ctx")), head)

        check("info: /where says the terminal the fixture set",
              actions.agent_where(con, "backend") == "terminal term_x",
              actions.agent_where(con, "backend"))
        check("info: /worktree says so when there is none",
              actions.agent_worktree(con, "nobody") == "nobody has no worktree",
              actions.agent_worktree(con, "nobody"))

        cmds = dict(tui.agent_commands(con, "backend"))
        # Every AGENT_KEYS verb `can` allows right now, `open` aside (the panel
        # handles it before ever consulting AGENT_KEYS, same as the footer).
        allowed = {label for _, label in tui.AGENT_KEYS
                  if label != "open" and actions.can(con, "backend", label)[0]}
        check("commands: every allowed verb is offered, no more and no fewer",
              {c for c, k in cmds.items() if k == "action"} == allowed, str(cmds))
        check("commands: the info getters are always offered, not gated by can",
              {c for c, k in cmds.items() if k == "info"} == set(tui.INFO_COMMANDS), str(cmds))
        # The honesty property: nothing in AGENT_KEYS or INFO_COMMANDS is
        # missing from one list or the other - a new entry in either source
        # shows up here on its own, or this fails.
        check("commands: the page commands are always offered too",
              {c for c, k in cmds.items() if k == "page"} == set(tui.PAGE_COMMANDS), str(cmds))
        check("commands: every source name appears in exactly one place",
              set(cmds) == allowed | set(tui.INFO_COMMANDS) | set(tui.PAGE_COMMANDS) | {"cancel"}
              and len(cmds) == len(allowed) + len(tui.INFO_COMMANDS) + len(tui.PAGE_COMMANDS) + 1,
              str(cmds))
    finally:
        con.close()


def test_a_message_in_for_you_has_a_place_in_the_conversation(home: Path) -> None:
    """Founder #1372: opening a FOR YOU row lands in that agent's conversation,
    on that message, not on a copy of it drawn alone. `message_place` says where
    a message sits, and None where it cannot be shown there."""
    _daemon_home(home, 20)
    agents.register("reviewer")
    con = db.connect()
    try:
        def say(text: str, chain: str | None, who: str = "reviewer", fyi: bool = False) -> int:
            return db.add_request(con, who, actions.HUMAN, text, None, "peer", "async",
                                  None, 0, None, chain_id=chain, fyi=fyi)

        for i in range(10):
            say(f"earlier {i}", "c1")
        target = say("TARGET the merge gate", "c1")
        for i in range(40):
            say(f"later {i}", "c1")
        check("for-you: a message sits in its agent's conversation, as the ask",
              actions.message_place(con, target) == ("reviewer", 0),
              str(actions.message_place(con, target)))

        report = say("FYI the gate is green", "c9", fyi=True)
        actions.opened_by_human(con, report)
        chat, _ = actions.conversation_and_drawn(con, "reviewer")
        shown = [getattr(r, "text", "") for r in chat if getattr(r, "kind", None) == "msg"]
        check("for-you: opening a report does not draw its receipt as a reply",
              "FYI the gate is green" in " ".join(shown) and "read" not in shown, str(shown[-4:]))
        check("for-you: and the unanswered row is still in FOR YOU",
              target in [m["id"] for m in actions.messages(con)])

        # An answer the human was given: the verdict is the message.
        asked = db.add_request(con, actions.HUMAN, "reviewer", "did it merge?", None, "peer",
                               "async", None, 0, None, chain_id="c2")
        con.execute("UPDATE requests SET state='done', verdict='ANSWER yes', resolved_at=? "
                    "WHERE id=?", (int(time.time()), asked))
        con.commit()
        check("for-you: an answer sits on its verdict",
              actions.message_place(con, asked) == ("reviewer", 1),
              str(actions.message_place(con, asked)))

        con.execute("DELETE FROM requests WHERE id = ?", (target,))
        con.commit()
        check("for-you: a message that is gone has no place",
              actions.message_place(con, target) is None)
        old = say("OLDEST chain", "old")
        for i in range(actions.CHAT_CHAINS + 1):
            say(f"newer chain {i}", f"n{i}")
        check("for-you: a message in a chain the conversation no longer draws has no place",
              actions.message_place(con, old) is None)
    finally:
        con.close()


def test_nothing_a_terminal_would_obey_gets_through_what_someone_else_wrote(home: Path) -> None:
    """Auditor #899: `wrap` stripped only simple CSI, so a title, a clipboard
    write, a link, a terminal reset, a charset switch, a CSI with an
    intermediate byte, BEL, backspace and C1 CSI in a message body reached the
    terminal. Each is put through every tab of the real page and the frame, and
    the screen is compared with one drawn from the text a reader should see."""
    attacks = {
        "title": "\033]0;PWNED\007", "clipboard": "\033]52;c;Zm9v\007",
        "link": "\033]8;;http://x\033\\link\033]8;;\033\\", "reset": "\033c",
        "charset": "\033(0", "cursor style": "\033[ q", "bell": "\007", "backspace": "\b\b",
        "c1 csi": "\x9b31m", "misc": "\x7f\x00\x1b", "unterminated": "\033]0;never ended",
        "colour": "\033[31mred\033[0m", "clear": "\033[2J\033[H",
        "our own reset": "\033[0m", "our own dim": "\033[2m", "our own fill": "\033[97;100m",
    }
    poison = "\033]0;PWNED\007\033[31mx\x9b1m\033[0m"

    def scene(where: Path, hostile: bool) -> dict[str, str]:
        """Everything drawn from text someone else wrote, hostile or its reading."""
        say = (lambda t: t) if hostile else _reference_text
        shout = " ".join(f"<{name}>before{say(bad)}after" for name, bad in attacks.items())
        pois = say(poison)
        _daemon_home(where, 20)
        agents.register("reviewer")
        con = db.connect()
        try:
            actions.chat_reply(con, "reviewer", shout)
            first = actions.chat_parent(con, "reviewer")
            askmod.resolve(first, shout)
            third = askmod.ask("backend", shout, requester="reviewer", parent_id=first)[2]
            askmod.resolve(third, shout)
            db.add_request(con, "marina", "reviewer", shout, None, "peer", "async", None, 0, None)
            told = askmod.ask("backend", "a question", requester="reviewer")[2]
            askmod.resolve(told, shout)
            keep_handoff("reviewer", shout + "\n" + shout)
            db.set_agent_state(con, "reviewer", dmn_states.IDLE, detail=shout)
            askmod.ask(actions.HUMAN, shout, requester="reviewer")
            # (An event's payload is stored as JSON, which escapes C0 bytes into
            # text; C1 stays a byte there, and `drawable` takes it out on the way
            # to the screen. The escaped form is longer than its reading, so the
            # twin could not match it.)
            # Fields the agent's own file and its state set, drawn on the main screen.
            db.add_event(con, "reviewer", f"kind{pois}", {})
            db.add_event(con, "reviewer", "wake", {})
            db.set_agent_state(con, "reviewer", "STALLED", detail=f"stuck {pois} " * 8)
            shown = {}
            page = tui._agent_page(con, "reviewer", tui.AGENTS)
            for width in (200, 30):
                shown[f"page at {width}"] = _page_text(page, width)
            real_kind, real_acct = agents.kind, agents.current_account
            agents.kind = lambda cfg: f"kind{pois}"      # agent.yaml's fields, whatever parses them
            agents.current_account = lambda name, con=None: f"acct{pois}"
            try:
                shown["main"] = _snap()
            finally:
                agents.kind, agents.current_account = real_kind, real_acct
            return shown
        finally:
            con.close()

    dirty = scene(home / "dirty", True)
    clean = scene(home / "clean", False)
    check("escape: the twin is not itself hostile",
          not _leaks(clean, clean), str(_leaks(clean, clean))[:200])
    # A narrow screen cuts a long value and closes the cut with a reset, so a
    # hostile text and its reading need not cut alike: counts are compared
    # where nothing is cut; strays are looked for everywhere.
    found = _leaks(dirty, clean, skip_counts=tuple(k for k in dirty if k.endswith("at 30")))
    for label in dirty:
        check(f"escape: nothing of a terminal's is in {label}, and no code of ours added or ended",
              not [f for f in found if f.startswith(label + ":")],
              str([f for f in found if f.startswith(label + ":")])[:300])
    check("escape: what surrounded it is still read",
          "before" in dirty["page at 200"].replace("\n", "") and "stuck" in dirty["main"])
    check("escape: the payload of a title is not drawn", not any("PWNED" in t for t in dirty.values()),
          str([k for k, t in dirty.items() if "PWNED" in t]))
    check("escape: drawable splits on every line break a terminal would obey",
          all(tui.drawable(f"a{c}b") == ["a", "b"] for c in "\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"),
          str([(hex(ord(c)), tui.drawable(f"a{c}b")) for c in "\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"]))
    check("escape: wrap alone, for anything else that goes through it",
          tui.wrap(["a\033]0;t\007b\x9bc\td\nnext"], 20) == ["ab" + "c" + "    d", "next"],
          str(tui.wrap(["a\033]0;t\007b\x9bc\td\nnext"], 20)))


def test_an_agent_name_is_text_and_no_screen_can_be_made_to_obey_it(home: Path) -> None:
    """Auditor #927: a name is free text - register refused only '', '/' and a
    leading '.', so an escape passed, and it was drawn raw in the team list, the
    STUCK panel, the FOR YOU rows, the request headers and the page titles. A
    control byte is refused where the name is stored, and every place that draws
    one draws it as text. The screen is rendered twice, once with the poisoned
    name and once with a plain one of the same length: what differs in our own
    escape codes, or is left over that we did not write, is a leak. (Subtracting
    our own codes instead let an injected reset or DIM pass as ours.)"""
    _daemon_home(home / "refuse", 20)
    for bad in ("a\033]0;T\007b", "a\nb", "a\x9bb", "a\x7fb", "a\x00b", "a\u2028b", "", ".x", "a/b"):
        try:
            agents.register(bad)
            refused = False
        except ValueError:
            refused = True
        check(f"names: {bad!r} is not a name", refused)
    agents.register("백엔드-2")
    check("names: a Korean name still is one", "백엔드-2" in paths.list_agents())

    poison = "ev\033]0;T\007il\033[0m\033[2m\033[7m\033[K\x9b1m"

    def scene(where: Path, name: str) -> dict[str, str]:
        """Everything the screen draws about an agent called `name`."""
        _daemon_home(where, 20)
        d = paths.agent_dir(name)             # made by hand: register would refuse it
        (d / "inbox").mkdir(parents=True)
        (d / "agent.yaml").write_text("name: backend\naccount: alpha\nmodel: sonnet\n"
                                      "effort: medium\nweight: light\nenabled: true\n")
        con = db.connect()
        try:
            db.set_agent_state(con, name, "STALLED", detail="stuck on something")
            db.add_request(con, name, "human", "which one?", None, "peer", "async",
                           "escalate", 0, None)
            db.add_request(con, "marina", name, "please look", None, "peer", "async", None, 0, None)
            db.add_request(con, "human", name, "hello", None, "peer", "async", None, 0, None)
            back = db.add_request(con, name, "backend", "a question", None, "peer", "async",
                                  None, 0, None)
            con.execute("UPDATE requests SET state = 'done', verdict = 'answered', "
                        "resolved_at = ? WHERE id = ?", (int(time.time()), back))
            db.add_event(con, name, "wake", {})
            db.add_request(con, name, "backend", "another", None, "peer", "async", None, 0, None)
            asked = db.add_request(con, "backend", name, "and this?", None, "peer", "async",
                                   None, 0, None)
            con.execute("UPDATE requests SET state = 'done', verdict = 'answered too', "
                        "resolved_at = ? WHERE id = ?", (int(time.time()), asked))
            shown = {"main": _snap()}
            shown["backend's page"] = _page_text(   # where the name is a requester
                tui._agent_page(con, "backend", tui.AGENTS), 200)
            shown["page"] = _page_text(tui._agent_page(con, name, tui.AGENTS), 200)
            mine = con.execute("SELECT id FROM requests WHERE requester = ? AND target = 'human'",
                               (name,)).fetchone()["id"]
            title, sections = actions.message_detail(con, mine)
            shown["message page"] = _page_text(tui.page(title, sections, tui.MESSAGES, mine), 200)
            return shown
        finally:
            con.close()

    dirty = scene(home / "dirty", poison)
    clean = scene(home / "clean", _reference_text(poison))
    found = _leaks(dirty, clean)
    for label in dirty:
        check(f"names: {label} draws nothing of a terminal's, and no code of ours a plain name would not",
              not [f for f in found if f.startswith(label + ":")],
              str([f for f in found if f.startswith(label + ":")])[:300])
        if label in ("main", "message page", "backend's page"):
            check(f"names: the name is still there to read in {label}", "evil" in dirty[label],
                  dirty[label][:200])


def test_no_command_prints_what_a_terminal_would_obey(home: Path) -> None:
    """The bridge's screen was made safe first; the CLI writes the same text into
    the founder's shell. Every stored field an agent can write - a request's
    body and verdict, a state detail, an event kind and payload, a handoff, a
    digest, a terminal handle, the daemon's log - is
    poisoned, every command that shows any of it is run, and each output is
    graded as the screen is: our own codes counted against a twin drawn from the
    reading of the same text (a stray-only check would pass what the screen's
    once did). The ones that do not show it are listed, so a command that never
    displays a field cannot be mistaken for one that displays it safely."""
    from ocp.session import fold as foldmod
    from ocp.infra import service
    poison = "\033]0;PWNED\007\033[31mMARKED\033[0m\x9b1m\033[2m"

    def scene(where: Path, hostile: bool) -> dict[str, str]:
        p = poison if hostile else _reference_text(poison)
        # A verdict is written through yamlcompat.dump, which already refuses a
        # C0 control or DEL - the full poison above would never reach print(),
        # so this fixture would prove nothing about the print. C1 alone (the
        # class textsafe exists for, and what drift's own leak turned out to
        # be) is not something dump refuses, so this is what actually exercises
        # the fix rather than dump's unrelated guard.
        c1p = "\x9b1mMARKED\x9b0m" if hostile else _reference_text("\x9b1mMARKED\x9b0m")
        _daemon_home(where, 20)
        agents.register("reviewer")
        con = db.connect()
        try:
            q = db.add_request(con, "marina", "reviewer", f"body {p} end\n\tsecond {p}", None,
                               "peer", "async", None, 0, None)
            h = db.add_request(con, "reviewer", "human", f"escalation {p}", None, "peer", "async",
                               "escalate", 0, None)
            d = db.add_request(con, "reviewer", "backend", f"asked {p}", None, "peer", "async",
                               None, 0, None)
            con.execute("UPDATE requests SET state = 'done', verdict = ?, resolved_at = ? WHERE id = ?",
                        (f"verdict {p}\nmore {p}", int(time.time()), d))
            db.set_agent_state(con, "reviewer", "STALLED", detail=f"detail {p}")
            db.add_event(con, "reviewer", f"kind{p}", {"x": p})
            db.set_terminal(con, "reviewer", f"term{p}", f"wt{p}")
            # For `hr apply`: a proposal rejected with a poisoned verdict. Built
            # by hand rather than through hr.propose(), so the fixture does not
            # depend on anything else the scene registers or retires.
            hrq = db.add_request(con, "reviewer", askmod.HUMAN, "roster: retire reviewer?",
                                 None, "peer", "async", None, 0, None)
            con.execute("UPDATE requests SET state = 'done', verdict = ? WHERE id = ?",
                        (f"reject {c1p}", hrq))
            # For `hr withdraw`: a still-open proposal, its escalation still queued.
            wq = db.add_request(con, "reviewer", askmod.HUMAN, "roster: rename reviewer?",
                                None, "peer", "async", None, 0, None)
            # For `task list`: a title is text somebody typed.
            db.link_request(con, db.new_task(con, f"title {p}", "reviewer", "orcapipe"), q)
        finally:
            con.close()
        hr.save({"id": "poisontest", "kind": "retire", "agent": "reviewer",
                "requested_by": "reviewer", "why": "test", "request": hrq, "state": "pending"})
        hr.save({"id": "withdrawtest", "kind": "rename", "agent": "reviewer", "to": "revisor",
                "requested_by": "reviewer", "why": "test", "request": wq, "state": "proposed"})
        keep_handoff("reviewer", f"handoff {p}\n- item {p}")
        journal = foldmod.journal_path("reviewer", foldmod.today())
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(f"## 완료\n- done {p}\n")
        service.out_log().parent.mkdir(parents=True, exist_ok=True)
        service.out_log().write_text(f"[12:00:00] reviewer: {p}\n")
        repo = where / "repo"                  # for `drift`: a file name is text someone chose
        repo.mkdir()
        name = "a" + p + "b.txt"

        def git(*args: str) -> None:
            subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
        # The standard advice for readable Korean/Japanese filenames, which this
        # project is full of - and it also stops git quoting a control byte in a
        # path, so the fixture is driven where that actually bites (auditor,
        # #1003/#1048): with the default left on, git's own quoting of the C1
        # byte in `name` would pass this test whether or not cmd_drift sanitised
        # a thing.
        git("config", "core.quotePath", "false")
        (repo / "base.txt").write_text("x")
        git("add", ".")
        git("commit", "-qm", "base")
        git("checkout", "-q", "-b", "work")
        (repo / name).write_text("y")
        git("add", ".")
        git("commit", "-qm", "work")
        git("checkout", "-q", "main")
        (repo / name).write_text("z")
        git("add", ".")
        git("commit", "-qm", "main moves")
        git("update-ref", "refs/remotes/origin/main", "HEAD")
        git("checkout", "-q", "work")
        (repo / name).write_text("w")

        # Every leaf of the CLI is one of these two, so a subcommand that is added
        # is noticed: it is driven here, or it is skipped with a reason.
        driven = {
            "status": [["status"], ["status", "--json"]], "doctor": [["doctor", "--no-color"]],
            "team check": [["team", "check"]], "team seal": [["team", "seal"]], "ps": [["ps"]], "triggers": [["triggers"]],
            "events": [["events", "--limit", "50", "--all"], ["events", "--agent", "reviewer"]],
            "inbox": [["inbox", "reviewer"], ["inbox", "reviewer", "--all"],
                      ["inbox", "human", "--all"]],
            "read": [["read", str(q)], ["read", str(h)], ["read", str(d)]],
            "resolve": [(["resolve", str(d), "again"], {"OCP_AGENT": "backend"})],
            "digest": [["digest", "reviewer"]], "handoff": [["handoff", "reviewer"]],
            "collect": [["collect"], ["collect", "reviewer"]],
            "agent list": [["agent", "list"]],
            "agent show": [["agent", "show", "reviewer"], ["agent", "show", "reviewer", "--json"]],
            "account list": [["account", "list"], ["account", "list", "--json"]],
            "hr list": [["hr", "list"]], "service logs": [["service", "logs"]],
            "resume": [["resume", "reviewer"]], "fold": [["fold", "reviewer", "--no-model"]],
            "command": [["command", "reviewer"]], "org": [["org"]], "rollover": [["rollover", "reviewer"]], "backup": [["backup"]],
            "spawn": [(["spawn", "reviewer", "--repo", str(repo), "--prompt", f"p {p}", "--dry-run"],
                       None, "spawn --dry-run")],
            "drift": [(["drift", str(repo), "--no-fetch"], None, "drift --no-fetch")],
            "hr apply": [(["hr", "apply", "poisontest"], None, "hr apply")],
            "hr withdraw": [(["hr", "withdraw", "withdrawtest"], {"OCP_AGENT": "reviewer"},
                             "hr withdraw")],
            "guide": [["guide"]], "task list": [["task", "list"]],
            "top": [["top", "--once"]], "watch": [["watch", "--once"]], "bridge": [["bridge", "--once"]],
            "write-handoff": [["write-handoff", "reviewer"]],
            "progress": [(["progress"], {"OCP_AGENT": "reviewer"}, "progress")],
            "ask": [(["ask", "reviewer", f"a new one {p}"], {"OCP_AGENT": "marina"}, "ask")],
            "withdraw": [(["withdraw", str(d + 1)], {"OCP_AGENT": "marina"}, "withdraw")],
            "unstall": [["unstall", "reviewer"]],
            "venv status": [["venv", "status"]],
        }
        out = {}

        def run_it(argv: list[str], env: dict | None = None, key: str | None = None) -> None:
            buf, err = io.StringIO(), io.StringIO()
            if env is None and argv[0] in ("inbox", "digest", "handoff", "events"):
                env = {"OCP_AGENT": "human"}  # listings refuse a caller with no name (T39)
            saved = {k: os.environ.get(k) for k in (env or {})}
            os.environ.update(env or {})
            try:
                with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(err):
                    try:
                        climod.main(argv)
                    except SystemExit:
                        pass
            finally:
                for k, v in saved.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            out[key or " ".join(argv)] = buf.getvalue() + err.getvalue()
        # A fold whose model returns a digest that lost what was done: the refusal
        # names the lost items, which the agent wrote.
        real_model, real_record = foldmod.run_model, handoffmod.record
        foldmod.run_model = lambda *a, **k: (True, "# 2026-01-01 · reviewer\n\n## 완료\n")
        # `write-handoff` reads the agent's own terminal for the handoff, which
        # there is none of here: stand in for that step and print what it stored.
        handoffmod.record = lambda con, agent: []
        try:
            run_it(["fold", "reviewer", "--account", "alpha"], key="fold reviewer (model lost a done item)")
            for leaf, runs in driven.items():
                if leaf in ("ask", "withdraw", "unstall"):
                    continue                      # they change what the others show: last
                for one in runs:
                    argv, env, key = (one + (None, None))[:3] if isinstance(one, tuple) else (one, None, None)
                    run_it(argv, env, key=key or " ".join(argv) + (" (as backend)" if env else ""))
            run_it(*driven["ask"][0][:2], key=driven["ask"][0][2])
            run_it(*driven["withdraw"][0][:2], key=driven["withdraw"][0][2])
            run_it(driven["unstall"][0])
        finally:
            foldmod.run_model, handoffmod.record = real_model, real_record
        scene.driven = driven
        return out

    dirty = scene(home / "dirty", True)
    clean = scene(home / "clean", False)

    def leaves() -> list[str]:
        """Every command the CLI has, read from its own parser."""
        found_, parser = [], climod.build_parser()

        def walk(prefix: str, p_) -> None:
            subs = [a for a in p_._actions if isinstance(a, argparse._SubParsersAction)]
            if not subs:
                found_.append(prefix)
                return
            for n, sp in subs[0].choices.items():
                walk((prefix + " " + n).strip(), sp)
        walk("", parser)
        return found_

    # The ones not driven, and why. This is the list a later hand has to justify:
    # a new command is neither here nor driven until someone decides which.
    skipped = {
        "init": "creates the home and the shared settings",
        "login": "interactive: hands the terminal to claude",
        "account add": "interactive login", "account remove": "interactive logout and a confirm",
        "account register": "creates an account directory; prints its path",
        "account cap": "writes a cap; prints numbers",
        "account recover": "writes a per-account setting; prints where it stands, or "
                           "with --now calls recover.recover() against a real login "
                           "(recover.py's own mechanism is driven in test_recover_*)",
        "idle-timeout": "writes the global or a per-agent park timeout; prints it - driven in "
                        "test_t52_idle_timeout_and_ignore_cache_expiry_from_the_command_line, not this scene",
        "idle-background": "writes the global or a per-agent background notice and grace time; prints them - "
                           "driven in test_t424_idle_background_sets_the_notice_and_the_grace_globally_and_per_agent, "
                           "not this scene",
        "ignore-cache-expiry": "writes the global or a per-agent switch; prints it - driven in "
                               "test_t52_idle_timeout_and_ignore_cache_expiry_from_the_command_line, not this scene",
        "usage": "calls the usage endpoint and the Keychain against a real login "
                 "(usage_api's own parsing is driven above, without either)",
        "agent move": "a confirm, and Orca", "agent register": "writes agent.yaml; a name with a control byte is refused before it prints",
        "launch": "execs claude",
        "hooks sync": "rewrites the shared settings", "hooks status": "reads the shared settings; prints paths",
        "hooks gate": "writes the protected store; driven in "
                      "test_the_enforce_switch_is_the_founders_to_flip "
                      "(tests/session/test_stopgate.py), not this scene",
        "install-hooks": "writes the shared settings",
        "hr add": "writes a proposal; prints its id", "hr retire": "writes a proposal; prints its id",
        "hr rename": "writes a proposal; prints its id",
        "hr link": "writes a proposal; prints its id", "hr unlink": "writes a proposal; prints its id",
        "hr team": "writes a proposal; prints its id",
        "team init": "writes team.yaml",
        "cancel": "founder-only; its return strings hold ids and names (read in actions.cancel, ask.cancel)",
        "dismiss": "needs a human identity; returns ids (read in actions.dismiss)",
        "hook": "reads a hook payload on stdin and prints nothing",
        "daemon": "runs forever", "service install": "launchd", "service uninstall": "launchd",
        "service status": "launchd; prints a pid",
        "dev-mode": "writes a config flag; prints on/off - driven in "
                    "test_dev_mode_config_round_trips, not this scene",
        "auto-accept": "writes a config flag; driven in test_auto_accept_config_round_trips "
                       "(tests/session/test_autoaccept.py), not this scene",
        "close": "an agent's session is ended through Orca; the task half is driven by test_closing_a_task_needs_no_open_child_and_only_the_asker",
        "task new": "writes a row; prints its id and an owner already checked against the registry",
        "task start": "T292: writes agent_state.current_task; driven in "
                      "test_task_new_is_its_own_delivery, not this scene",
        "task note": "T292: sends an ordinary task-kind ask to the task's holder; driven in "
                     "test_task_new_is_its_own_delivery, not this scene",
        "task memo": "T493: writes and reads task_memos rows; driven in test_task_memo_verbs "
                     "(tests/team/test_tasks_requests.py), not this scene",
        "task begin": "T297: opens the caller's own task on a founder message; driven in "
                      "test_a_founder_message_lets_the_agent_begin_its_own_task, not this scene",
        "task handoff": "T377: changes a task's owner and delivers a note; driven in "
                        "test_a_task_is_handed_over_with_a_note_along_the_fixed_chain, not this scene",
        "task link": "writes a row; prints two ids",
        "task done": "writes a row; driven in test_marking_a_task_done_needs_evidence_and_only_the_owner_or_a_linked_target, not this scene",
        "task discard": "writes a row; driven in test_task_discard, not this scene",
        "task queue": "writes a row; driven in test_a_task_is_a_draft_until_queued_and_delivered_when_its_turn_comes, not this scene",
        "task draft": "writes a row; driven in test_a_task_is_a_draft_until_queued_and_delivered_when_its_turn_comes, not this scene",
        "task merge": "writes rows; driven in test_merging_tasks_moves_titles_and_requests_and_closes_the_rest, not this scene",
        "task reopen": "writes a row; driven in test_reopening_a_done_task_needs_a_reason_and_only_the_asker, not this scene",
        "task release": "writes a row; driven in test_release_takes_a_doing_task_back_to_queued (T286), not this scene",
        "task edit": "writes a row; driven in "
                     "test_editing_a_task_needs_the_opener_or_the_founder_and_is_never_silent, "
                     "not this scene",
        "task board": "own open tasks and who holds each; driven in "
                       "test_task_board_shows_who_holds_each_open_task, not this scene",
        "base promote": "writes base.md; prints sizes and a path",
        "memory status": "prints each agent's memory against budget; driven in "
                         "test_agent_memory_budget_migration_and_team_layer, not this scene",
        "memory migrate": "a dry run, or --apply copies files; driven in "
                          "test_agent_memory_budget_migration_and_team_layer, not this scene",
        "hr relabel": "calls Orca directly, no proposal; driven in "
                      "test_a_rename_names_the_agents_orca_worktree, not this scene",
        "hr rehome": "moves a git worktree and Claude's project directories; driven in "
                     "test_hr_rehome_cli_moves_the_folder, not this scene",
        "trust add": "founder-only on a tty; driven in test_ocp_trust_cli_is_wired",
        "trust list": "prints declared roots; driven in test_ocp_trust_cli_is_wired",
        "trust remove": "driven in test_ocp_trust_cli_is_wired",
        "trust proposals": "lists proposal files; prints their ids",
        "trust apply": "founder-only; applies a proposal and prints its id",
        "trust migrate": "founder-only; moves the old trust_roots.json once - driven in "
                         "test_trust_roots_live_in_the_protected_store",
        "venv install": "creates a real venv and pip installs into it",
        "say": "T293: opens and delivers a say (send_now, never a head), or --needs-task "
               "replies to the sender; driven in tests/team/test_say.py, not this scene",
    }
    have = set(driven_now := scene.driven) | set(skipped)
    every = set(leaves())
    check("cli: every command the CLI has is driven here or skipped with a reason",
          every == have and not (set(driven_now) & set(skipped)),
          f"new: {sorted(every - have)}; gone: {sorted(have - every)}; both: {sorted(set(driven_now) & set(skipped))}")
    found = _leaks(dirty, clean, skip_counts=("top --once", "watch --once", "bridge --once"))
    for label in dirty:
        check(f"cli: `ocp {label}` prints nothing of a terminal's, and no code of ours a plain text would not",
              not [f for f in found if f.startswith(label + ":")],
              str([f for f in found if f.startswith(label + ":")])[:300])
    shows = {k for k, v in clean.items() if "MARKED" in v}
    must = {"ps", "events --limit 50 --all", "events --agent reviewer", "inbox reviewer",
            "inbox reviewer --all", "inbox human --all", "read 1", "read 2", "read 3",
            "digest reviewer", "handoff reviewer", "write-handoff reviewer",
            "resolve 3 again (as backend)", "drift --no-fetch", "agent show reviewer",
            "agent show reviewer --json", "service logs", "fold reviewer (model lost a done item)"}
    check("cli: the poison is really on screen for every command this test claims to grade",
          must <= shows, str(sorted(must - shows)))
    # (JSON writes a control byte as the text \\u001b, and git quotes one in a path as
    # \\033: both are text, not a byte, and are safe to show.)
    quoted = re.compile(r"\\+(?:u001b|033)\]0;PWNED\\+(?:u0007|a|007)")
    check("cli: the payload of a title is not printed either",
          not [k for k, v in dirty.items() if "PWNED" in quoted.sub("", v)],
          str([k for k, v in dirty.items() if "PWNED" in quoted.sub("", v)]))
    check("cli: and the rest are the ones that do not show these fields at all",
          all(v == "" or "MARKED" not in v for k, v in clean.items() if k not in shows))
    check("cli: what it keeps of a body is its text - a tab stays a tab, so a patch reads as written",
          "\tsecond" in dirty["read 1"] and "\tsecond" in dirty["inbox reviewer"], repr(dirty["read 1"])[:200])
    dumped = dirty["agent show reviewer --json"]
    seen = subprocess.run([sys.executable, "-c",
                           f"import sys; sys.path.insert(0, {str(ROOT)!r}); import ocp.infra.textsafe; "
                           "print(sorted(m for m in sys.modules if m.startswith('ocp.')))"],
                          capture_output=True, text=True).stdout
    check("cli: the rule about what a terminal would obey imports no front end - only itself",
          seen.strip() == "['ocp.infra', 'ocp.infra.textsafe']", seen.strip())
    check("cli: a command's json still parses, and holds no DEL or C1 byte",
          isinstance(json.loads(dumped), dict) and not re.search(r"[\x7f-\x9f]", dumped), dumped[:200])
    odd = {"k": "a\x9b31m\x7f\x85b \u2028 \u00e9 한"}
    said = textsafe.json_dumps(odd)
    check("cli: json_dumps writes DEL and C1 as escapes - the same json, no such bytes",
          json.loads(said) == odd and not re.search(r"[\x7f-\x9f]", said), said)


def test_a_module_that_returns_subprocess_text_as_a_message_imports_textsafe(home: Path) -> None:
    """orcapipe #1091: sanitising at each sink is a game the project loses -
    157 unsanitised `print(` calls in ocp/, and hand-triaging them one review
    at a time IS that game. The boundary foreign bytes actually enter through
    is small: orca._run, spawn._run, drift._git, a file read, the database.
    This is the subprocess third of it, found by the suite from here on
    instead of by a reviewer reading the diff: a module that reads what a
    subprocess said (`.stdout`/`.stderr`) and hands it back as part of a
    returned message must import textsafe.

    auditor's first attempt at this (flag any module with its own escape
    regex) false-positived on tui.py, which parses incoming keystrokes and
    measures its own codes - a check that cries wolf gets ignored. This one is
    keyed on the sink, not the pattern that happens to sit near one."""
    import ast

    # Modules the check would otherwise name, and why each one is not the
    # sink the property is about - the list a later hand has to justify, the
    # same shape as the CLI's driven-or-skipped table.
    exempt = {
        "team/accounts.py": "parses subscriptionType and email out of "
                       "`claude auth status`; both are Anthropic's own auth output "
                       "and the user's own email, never text an agent wrote",
        "vendors/claude.py": "reads the email and subscriptionType out of `claude auth "
                             "status`, the same auth output accounts.py parses; the "
                             "rest of what it returns is a bool or a parsed field",
        "infra/service.py": "launchctl's own error text - a macOS tool's output, not "
                      "anything an agent chose, and the least reachable of these",
    }
    named, clean = [], []
    for f in sorted((ROOT / "ocp").rglob("*.py")):
        if f.name == "textsafe.py":
            continue
        tree = ast.parse(f.read_text())
        if not (_touches_subprocess_text(tree) and _builds_a_returned_message(tree)):
            continue
        (clean if _imports_textsafe(tree) else named).append(str(f.relative_to(ROOT / "ocp")))
    check("boundary: every module returning subprocess text as a message imports "
          "textsafe, or is exempt with a reason",
          not (set(named) - set(exempt)), str(sorted(set(named) - set(exempt))))
    check("boundary: an exempt module is not also one that already imports it - "
          "the reason would be a lie",
          not (set(exempt) & set(clean)), str(exempt.keys() & set(clean)))
    # Real files today, both directions: cli/hr/tui/textsafe pass on the merits
    # (hr never touches a subprocess at all; the other three do and are clean),
    # and orca.py/spawn.py/drift.py/fold.py/handoff.py are exactly the ones
    # this branch touched (the last three read git through gitrun).
    check("boundary: the modules this branch closed are the ones found dirty",
          {"session/orca.py", "team/spawn.py", "session/drift.py", "session/fold.py",
           "session/handoff.py"} <= set(clean),
          str(clean))

    # Proof by mutation: the same two functions, run on synthetic source, so
    # the check is shown to fire on the shape and not on a filename it knows.
    dirty_src = (
        "import subprocess\n"
        "def _run(args):\n"
        "    r = subprocess.run(args, capture_output=True)\n"
        "    if r.returncode:\n"
        "        return False, f'failed: {r.stderr}'\n"
        "    return True, r.stdout\n")
    clean_src = "from . import textsafe\n" + dirty_src
    fallback_src = (
        "import subprocess\n"
        "def _run(args):\n"
        "    r = subprocess.run(args, capture_output=True)\n"
        "    if r.returncode:\n"
        "        return False, (r.stderr or r.stdout or 'failed')\n"
        "    return True, r.stdout\n")
    check("boundary: the check fires on the exact shape orca._run/spawn._run had",
          _touches_subprocess_text(ast.parse(dirty_src))
          and _builds_a_returned_message(ast.parse(dirty_src))
          and not _imports_textsafe(ast.parse(dirty_src)))
    check("boundary: and is silent once the same module imports textsafe",
          _imports_textsafe(ast.parse(clean_src)))
    check("boundary: the fold.py shape (`a or b or default`, no f-string) is caught too",
          _builds_a_returned_message(ast.parse(fallback_src)))
    gitrun_src = (
        "from . import gitrun\n"
        "def _g(cwd):\n"
        "    rc, out, err = gitrun.run(cwd, 'status')\n"
        "    return False, f'failed: {err}'\n")
    check("boundary: a message built from gitrun.run's text is caught too",
          _touches_subprocess_text(ast.parse(gitrun_src))
          and _builds_a_returned_message(ast.parse(gitrun_src))
          and not _imports_textsafe(ast.parse(gitrun_src)))
    for label, imp in (("from-import", "from .gitrun import run\n"),
                       ("aliased import", "from ..infra import gitrun as g\n")):
        call = "run(cwd, 'x')" if label == "from-import" else "g.run(cwd, 'x')"
        check(f"boundary: gitrun.run reached by a {label} is caught too",
              _touches_subprocess_text(ast.parse(f"{imp}def _g(cwd):\n    return {call}\n")))
    check("boundary: an unrelated run() is not gitrun",
          not _touches_subprocess_text(ast.parse("def _g(cwd):\n    return run(cwd)\n")))
    check("boundary: a module with no subprocess call at all is not named, whatever it returns",
          not _touches_subprocess_text(ast.parse('def f():\n    return f"{x}"\n')))
    check("boundary: reading .stdout with nothing returned as a message is not named either "
          "(tui.py's own shape: it reads a subprocess result to steer a suspend, not to quote it)",
          not _builds_a_returned_message(ast.parse(
              "import subprocess\n"
              "def f():\n"
              "    r = subprocess.run(['x'], capture_output=True)\n"
              "    ok = r.returncode == 0 and bool(r.stdout)\n"
              "    return ok\n")))


def test_the_composer_takes_a_paste_whole(home: Path) -> None:
    """Enter sends, so a pasted newline must not: the old one-line prompt sent
    the first pasted line and dropped the rest (#193)."""
    c = tui.Composer(multi=True)
    check("composer: typing", c.feed("hi") is None and c.text == "hi")
    check("composer: a paste keeps its newlines and does not send",
          c.feed("\033[200~a\nb\r\nc\033[201~") is None and c.text == "hia\nb\nc", repr(c.text))
    check("composer: Enter after it sends", c.feed("\r") == "send")
    c = tui.Composer(multi=True)
    c.feed("\033[20"); c.feed("0~x\ny\033[20"); c.feed("1~")
    check("composer: a marker split across reads is still a marker",
          c.text == "x\ny" and not c.pasting, repr(c.text))
    check("composer: an arrow is neither text nor a way out",
          tui.Composer().feed("\033[A") is None)
    check("composer: a bare escape cancels", tui.Composer().feed("\033") == "cancel")
    # A read that ends on the escape of the end marker must not strand the paste.
    c = tui.Composer()
    c.feed("\033[200~abc\033")
    c.feed("[201~")
    check("composer: a marker split right after its escape is still a marker",
          c.text == "abc" and not c.pasting and c.feed("\r") == "send", repr(c.text))
    c = tui.Composer()
    check("composer: ESC [ then Enter is not a trap",
          c.feed("hi\033[") is None and c.feed("\r") == "send" and c.text == "hi", repr(c.text))
    c = tui.Composer()
    c.feed("a\033[3~b\033[1;5Cc")
    check("composer: longer escape sequences are skipped whole", c.text == "abc", repr(c.text))


def test_the_composer_cursor_moves_and_edits_in_place(home: Path) -> None:
    """Founder: they could not put the cursor back into what they had typed -
    Left/Right were read and then discarded by the generic escape branch, so
    the keys did nothing at all (#1008/#1020). The fix: a real position,
    moved and edited at, not only ever appended to or popped from the end."""
    c = tui.Composer()
    c.feed("abc")
    check("cursor: typing leaves it after the last character", c.pos == 3)
    c.feed("\033[D")
    check("cursor: Left steps it back one", c.pos == 2)
    c.feed("x")
    check("cursor: what is typed goes in at the cursor, not appended to the end",
          c.text == "abxc" and c.pos == 3, repr(c.text))
    c.feed("\033[C")
    check("cursor: Right steps it forward one", c.pos == 4 and c.text == "abxc")
    c.feed("\033[D\033[D\033[D\033[D\033[D")   # past the start, five times
    check("cursor: Left clamps at 0, it does not go negative", c.pos == 0)
    c.feed("\033[F")   # End
    check("cursor: End puts it after the last character", c.pos == len(c.text) == 4)
    c.feed("\033[H")   # Home
    check("cursor: Home puts it at the first", c.pos == 0)
    c.feed("\x7f")
    check("cursor: Backspace at the start does nothing - nothing is before it",
          c.text == "abxc" and c.pos == 0)
    c.feed("\033[C\033[C")
    c.feed("\x7f")
    check("cursor: Backspace removes the character before the cursor, not the last one typed",
          c.text == "axc" and c.pos == 1, repr(c.text))
    c.feed("\033[3~")   # Delete
    check("cursor: Delete removes the character at the cursor, and does not move it",
          c.text == "ac" and c.pos == 1, repr(c.text))

    c = tui.Composer()
    c.feed("one two three")
    c.feed("\033[1;5D")   # Ctrl-Left / word-left
    check("cursor: word-left lands at the start of the last word",
          c.text[:c.pos] == "one two ", repr(c.text[:c.pos]))
    c.feed("\033[1;5D")
    check("cursor: and again at the start of the word before it",
          c.text[:c.pos] == "one ", repr(c.text[:c.pos]))
    c.feed("\033[1;5C")   # Ctrl-Right / word-right
    check("cursor: word-right lands past the end of that word",
          c.text[:c.pos] == "one two", repr(c.text[:c.pos]))

    c = tui.Composer(multi=True)
    c.feed("abc")
    c.feed("\033[D")
    c.feed("\033[200~X\nY\033[201~")
    check("cursor: a paste also inserts at the cursor, not at the end",
          c.text == "abX\nYc" and c.pos == 5, repr(c.text))


def test_the_composer_alt_keys_and_a_pasted_cursor_glyph(home: Path) -> None:
    """Auditor's should-fix on #1033: ESC-then-key (Alt/Option, what macOS
    Terminal and iTerm2 send for word moves by default) fell into the same
    branch as a lone Escape and threw the typed message away; a pasted U+258F
    (the cursor glyph ocp's own composer draws) must not move the real cursor."""
    c = tui.Composer()
    c.feed("one two three")
    check("alt: Option+Left (ESC b) is a word move, not a cancel",
          c.feed("\033b") is None and c.text[:c.pos] == "one two ", repr(c.text[:c.pos]))
    check("alt: Option+Right (ESC f) too",
          c.feed("\033f") is None and c.text[:c.pos] == "one two three", repr(c.text[:c.pos]))
    c.feed("\033b")   # word-left, twice: back to the start of "three"
    check("alt: back at the start of the last word",
          c.text[:c.pos] == "one two ", repr(c.text[:c.pos]))
    check("alt: Option+Backspace (ESC DEL) deletes the word before the cursor, not this one",
          c.feed("\033\x7f") is None and c.text == "one three" and c.pos == 4, repr(c.text))
    c = tui.Composer()
    c.feed("abc")
    check("alt: an ESC+key this does not map is read and discarded, not a cancel",
          c.feed("\033x") is None and c.text == "abc")
    check("alt: a lone Escape - nothing after it in the same read - still cancels",
          tui.Composer().feed("\033") == "cancel")

    # A pasted cursor glyph in the text must not move the real cursor.
    c = tui.Composer()
    c.feed("▏" + "x" * 30)   # a look-alike character, then ordinary text
    check("cursor: typing after a glyph the text itself contains still leaves "
          "the real cursor at the end", c.pos == 31)


def test_the_agent_footer_shows_only_what_the_row_can_do(home: Path) -> None:
    """The footer must never claim a key `actions.can` would then refuse - and
    must never go quiet just because a key was hidden.

    One state per row of the founder's list (#790): a running agent parks and
    closes; a park a person started by hand wakes; a park that clears on its
    own does not (founder #1620/#1625/#1633 - forcing it early wakes the
    agent into an account that is still not there); a stalled (FATAL) one
    wakes too - `u`/unstall is gone, folded into `w` (founder #1620/#1631);
    a closed one wakes only when something is waiting for it; an agent with no row yet still gets what a
    never-launched agent can use. Fold and move are gone from every one of
    them - reached for rarely enough to cost more room than they earn - but a
    hidden key still does what it always did, because dispatch reads
    AGENT_KEYS whole and never asks the footer first.
    """
    _daemon_home(home, 20)
    con = db.connect()
    try:
        def labels(state=None, closed=False, detail=None):
            if state is not None:
                db.set_agent_state(con, "backend", state, detail=detail)
            if closed:
                db.set_closed(con, "backend", None)
            else:
                db.clear_closed(con, "backend")
            return [label for _, label in tui.agent_footer_keys(con, "backend")]

        common = ("open", "terminal", "message", "editor")

        got = labels(dmn_states.RUNNING)
        check("footer: a running agent gets only the always-on keys",
              got == list(common), got)

        got = labels(dmn_states.PARKED, detail="by hand; wakes at 16:40")
        check("footer: a park started by hand adds wake, close and park",
              set(got) == set(common) | {"wake", "close", "park"}, got)

        got = labels(dmn_states.PARKED, detail="usage; wakes at 16:40")
        check("footer: a usage-wall park - it clears on its own - does not offer wake",
              set(got) == set(common) | {"close", "park"}, got)

        got = labels(dmn_states.STALLED)
        check("footer: a FATAL (STALLED) agent adds only wake - unstall is gone",
              set(got) == set(common) | {"wake"} and "unstall" not in got, got)

        got = labels(dmn_states.PARKED, closed=True, detail="usage; wakes at 16:40")
        check("footer: a closed agent with nothing waiting adds nothing - a wake "
              "would only be refused (T124)",
              set(got) == set(common), got)

        support.something_waiting(con, "backend")
        got = labels(dmn_states.PARKED, closed=True, detail="usage; wakes at 16:40")
        check("footer: a closed agent with something waiting adds only wake, not "
              "close or park - whatever parked it",
              set(got) == set(common) | {"wake"}, got)
        con.execute("UPDATE requests SET state = 'done'")

        for got in (labels(dmn_states.RUNNING), labels(dmn_states.PARKED, detail="by hand; wakes at 16:40"),
                    labels(dmn_states.STALLED), labels(dmn_states.PARKED, closed=True)):
            check("footer: fold and move never appear",
                  "fold" not in got and "move" not in got, got)

        # An agent with no row at all: everything `can` refuses when a row is
        # missing (editor included - it is not in `can`'s row-is-None allow
        # list) stays off the footer; `open` is still there, since dispatch
        # never asks `can` about it.
        got = [label for _, label in tui.agent_footer_keys(con, "freshone")]
        check("footer: a never-started agent still offers open, terminal, message",
              set(got) == {"open", "terminal", "message"}, got)
    finally:
        con.close()


def test_every_state_the_daemon_can_write_has_a_waker() -> None:
    """daemon.WAKER names who or what wakes every state `set_agent_state` is
    ever called with, in this file and everywhere else that writes one - a
    state missing from it is the silent terminal state section 8.4 warns
    about. Scans the real source, not a hand-kept list of call sites, so a
    new writer added later without a WAKER entry fails this the moment it is
    added, not the moment someone remembers to update a list here too."""
    import ocp
    pkg_dir = Path(ocp.__file__).parent
    written: set[str] = set()
    for path in pkg_dir.rglob("*.py"):
        written |= _states_written_in(path)
    check("waker: at least the states this suite knows are written are found "
          "(the scan itself is not vacuous)",
          {"PARKED", "STALLED", "IDLE", "QUEUED", "HANDOFF", "DEGRADED",
           "THROTTLED", "RUNNING"} <= written, str(written))
    missing = written - set(dmn_states.WAKER)
    check("waker: every state the daemon can write has a way back",
          not missing, str(missing))


def test_the_screen_word_mapping_is_the_founders_six(home: Path) -> None:
    """tui.screen_state(): the one function - RUNNING, IDLE, PENDING, MOVING,
    DIALOG, FATAL, each from the internal shape that produces it, and no
    others - proof section: one check per mapping, each failing when the
    line that makes it is reverted."""
    _daemon_home(home, 20)
    con = db.connect()
    try:
        def state(**kw):
            db.set_agent_state(con, "backend", **kw)
            word, extra = tui.screen_state(con, db.get_agent_state(con, "backend"))
            check(f"map: {word} is one of the founder's six", word in tui.SCREEN_WORDS)
            return word, extra

        check("map: no row at all is IDLE", tui.screen_state(con, None) == ("IDLE", ""))
        check("map: RUNNING is RUNNING", state(state=dmn_states.RUNNING) == ("RUNNING", ""))
        check("map: HANDOFF is RUNNING too - the daemon finishing this agent's "
              "own turn, not something a person or the agent is doing",
              state(state=dmn_states.HANDOFF) == ("RUNNING", ""))
        check("map: plain IDLE is IDLE", state(state=dmn_states.IDLE, detail="") == ("IDLE", ""))
        check("map: QUEUED (work waiting) is PENDING, with no time - it is "
              "dispatched this tick", state(state=dmn_states.QUEUED, detail="wake") == ("PENDING", ""))
        check("map: QUEUED with the moving marker is MOVING, never with a time",
              state(state=dmn_states.QUEUED, detail="moving alpha -> beta") == ("MOVING", ""))
        wake = int(time.time()) + 900
        clock = time.strftime("%H:%M", time.localtime(wake))
        check("map: a usage-wall PARKED with no work behind it is IDLE, not a reset wait",
              state(state=dmn_states.PARKED, wake_at=wake, detail="usage; wakes at x")
              == ("IDLE", ""))
        check("map: a by-hand PARKED with no work is still PENDING, with the time a person chose",
              state(state=dmn_states.PARKED, wake_at=wake, detail="by hand; wakes at x")
              == ("PENDING", clock))
        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)
        check("map: a usage-wall PARKED with work waiting is PENDING, with the clock it wakes at",
              state(state=dmn_states.PARKED, wake_at=wake, detail="usage; wakes at x")
              == ("PENDING", clock))
        check("map: a by-hand PARKED is PENDING too - only wake permission tells them apart",
              state(state=dmn_states.PARKED, wake_at=wake, detail="by hand; wakes at x")
              == ("PENDING", clock))
        check("map: a PARKED past its own wake_at says due, not a clock behind now",
              state(state=dmn_states.PARKED, wake_at=int(time.time()) - 5, detail="usage; wakes at x")
              == ("PENDING", "due"))
        check("map: a PARKED carrying the moving marker is MOVING, with no time",
              state(state=dmn_states.PARKED, wake_at=wake, detail="moving; wakes at x")
              == ("MOVING", ""))
        check("map: DEGRADED is PENDING - the daemon's own retry, not a person's",
              state(state=dmn_states.DEGRADED, wake_at=wake, detail="handoff timed out at 'x'")
              == ("PENDING", clock))
        check("map: THROTTLED is PENDING too - the ask window rolling over",
              state(state=dmn_states.THROTTLED, wake_at=wake, detail="too many asks")
              == ("PENDING", clock))
        check("map: a PARKED requester on its own async ask is IDLE, with who it waits for",
              state(state=dmn_states.PARKED, wake_at=wake,
                   detail=askmod.await_reply_detail(1627, "keeper"))
              == ("IDLE", "waiting for keeper's reply (#1627)"))
        check("map: a long agent name does not cut the wait sentence",
              state(state=dmn_states.PARKED, wake_at=wake,
                   detail=askmod.await_reply_detail(1176, "orcapipe.auditor"))
              == ("IDLE", "waiting for orcapipe.auditor's reply (#1176)"))
        check("map: waiting on the human's reply is not spelled out",
              state(state=dmn_states.PARKED, wake_at=wake,
                   detail=askmod.await_reply_detail(1628, "human"))
              == ("IDLE", ""))
        check("map: FATAL is today's STALLED, with its own detail",
              state(state=dmn_states.STALLED, detail="ran out of attempts")
              == ("FATAL", "ran out of attempts"))

        # DIALOG, from each of its two sources - neither is a stored state.
        os.environ["OCP_AGENT"], os.environ["OCP_ACCOUNT"] = "backend", "alpha"
        real_screen_blocker = orcamod.screen_blocker
        try:
            db.set_terminal(con, "backend", "term_x", "wt")
            db.set_agent_state(con, "backend", dmn_states.RUNNING, detail="active")
            # The daemon's screen watch keeps the prompt as a prompt_blocks row (T395).
            bid = db.prompt_open(con, "backend", "permission", "fp", "Bash", [],
                                 {"tool": "Bash", "command": "rm -rf /x"}, 5, True, int(time.time()))
            word, extra = tui.screen_state(con, db.get_agent_state(con, "backend"))
            check("map: a prompt block is DIALOG, over RUNNING",
                  word == "DIALOG" and "rm -rf /x" in extra, (word, extra))
            db.prompt_close(con, bid, int(time.time()))
        finally:
            orcamod.screen_blocker = real_screen_blocker
            os.environ.pop("OCP_AGENT", None); os.environ.pop("OCP_ACCOUNT", None)

        db.set_agent_state(con, "backend", dmn_states.IDLE)
        db.add_request(con, "reviewer", "backend", "look at this", None,
                       "peer", "async", None, 0, None)
        db.block_open(con, "backend", orcamod.MODAL, "a dialog", "request #1",
                     int(time.time()), show_at=int(time.time()) + 1000, title="")
        word, extra = tui.screen_state(con, db.get_agent_state(con, "backend"))
        check("map: a delivery block (blocked.py) is DIALOG too, over IDLE",
              word == "DIALOG" and "dialog" in extra, (word, extra))
    finally:
        con.close()


def test_the_cursor_walks_the_whole_screen(home: Path) -> None:
    """The lists change under the cursor - an agent is registered, a message is
    answered - so moving is defined on what is there now, not on a remembered
    index. And the panels are four lists stacked on one screen: arrowing down
    the screen means the screen, so the end of one panel is the top of the
    next. Stopping at the bottom of the team list is how a mailbox below it
    goes unread."""
    keys = ["alpha", "beta", "gamma"]
    check("top: down moves to the next", tui.move(keys, "alpha", 1) == "beta")
    check("top: the end of a list is off it, for the caller to handle",
          tui.move(keys, "gamma", 1) is None and tui.move(keys, "alpha", -1) is None)
    check("top: a cursor on something that is gone lands on the first row",
          tui.move(keys, "deleted", 1) == "alpha")
    check("top: and an empty panel has nothing to select",
          tui.move([], None, 1) is None)

    _daemon_home(home, 20)
    con = db.connect()
    try:
        agents.register("marina")
        db.add_request(con, "marina", actions.HUMAN, "which variant ships?",
                       None, "peer", "async", None, 0, None)
        sel: dict = {}
        panel = tui._enter(tui.AGENTS, sel)
        check("top: arriving in a panel puts the cursor on its first row",
              sel[tui.AGENTS] is not None, str(sel))
        # Through the keys, not through the function behind them: a `step` that
        # was right and a loop that still called the old `move` is how ↓
        # stopped at the bottom of a panel and left the cursor nowhere.
        seen = []
        for _ in range(6):
            seen.append((panel, sel.get(panel)))
            panel = tui.navigate(panel, sel, "\033[B")
        check("top: down off the end of the team list lands in the mailbox",
              any(p == tui.MESSAGES for p, _k in seen) or panel == tui.MESSAGES,
              str([(tui.PANEL_NAME[p], k) for p, k in seen]))
        check("top: and the cursor is never left on nothing",
              all(k is not None for _p, k in seen) and sel.get(panel) is not None,
              str([(tui.PANEL_NAME[p], k) for p, k in seen]))
        back = tui.navigate(panel, sel, "\033[A")
        check("top: and up comes back out of it", back in (tui.MESSAGES, tui.AGENTS),
              tui.PANEL_NAME[back])
        check("top: → and tab are the same jump",
              tui.navigate(tui.AGENTS, sel, "\033[C")
              == tui.navigate(tui.AGENTS, sel, "\t") == tui.MESSAGES)
        check("top: and a key that is not a move says so",
              tui.navigate(tui.AGENTS, sel, "a") is None)

        check("top: the keys a panel offers are its own",
              ("a", "answer") in tui.panel_keys(tui.MESSAGES)
              and ("l", "login") in tui.panel_keys(tui.ACCOUNTS))
    finally:
        con.close()


def test_a_refused_ask_is_not_a_new_message(home: Path) -> None:
    """A refusal sends nothing, so retrying it cannot work - and a retry loop
    that writes a row per attempt spent 35 request numbers in six seconds, each
    one a message no one was ever sent."""
    _two_agents_one_slot(home)
    for i in range(2):
        askmod.ask("b", f"n{i}", requester="a")
    # Half an hour ago, so "when a slot frees" and "an hour from now" are not
    # the same instant and the test can tell which one it got.
    sent = int(time.time()) - 1800
    con = db.connect()
    try:
        con.execute("UPDATE requests SET ts = ? WHERE requester = 'a'", (sent,))
        con.commit()
    finally:
        con.close()

    ok, first, _ = askmod.ask("b", "spec B", requester="a")
    for _ in range(9):
        ok, msg, _ = askmod.ask("b", "spec B", requester="a")

    con = db.connect()
    try:
        rows = con.execute("SELECT id, body FROM requests WHERE state = 'denied'").fetchall()
        check("refused: ten identical retries leave one row", len(rows) == 1,
              str([dict(r) for r in rows]))
        check("refused: and that row keeps the text, so nothing is lost",
              rows and rows[0]["body"] == "spec B", str([dict(r) for r in rows]))
        check("refused: the refusal says where the text is kept",
              f"#{rows[0]['id']}" in msg, msg)

        # A different message is a different refusal: the record is per thing
        # said, not per attempt to say it.
        askmod.ask("b", "something else", requester="a")
        n = con.execute("SELECT COUNT(*) c FROM requests WHERE state = 'denied'").fetchone()["c"]
        check("refused: a different message is recorded on its own", n == 2, str(n))

        # The window rolls, so the wait is until the oldest ask leaves it -
        # being told to wait for "the window" cost an hour for what was
        # minutes away.
        row = db.get_agent_state(con, "a")
        check("refused: the throttle lifts when a slot frees, not an hour on",
              abs(row["wake_at"] - (sent + team.ASK_WINDOW_S)) <= 2,
              f"wake_at={row['wake_at']} slot={sent + team.ASK_WINDOW_S}")
        check("refused: and the message says when that is",
              time.strftime("%H:%M", time.localtime(sent + team.ASK_WINDOW_S)) in msg, msg)
    finally:
        con.close()
    check("refused: every retry is still refused", not ok, msg)
    check("refused: with the same answer as the first", "THROTTLED" in first, first)


def test_the_screen_says_when_two_daemons_are_running(home: Path) -> None:
    """The warning is the whole of what can be done about a duplicate that
    predates the lock: nothing else on any screen can see one."""
    use_home(home)
    orig = dmn_locks.processes
    try:
        dmn_locks.processes = lambda: [111, 222]
        r = doctor.Report()
        doctor._check_one_daemon(r)
        check("doctor: two daemons are a warning, by pid",
              any(lv == doctor.WARN and "111, 222" in name for lv, name, _ in r.rows),
              str(r.rows))

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            clidaemon.cmd_daemon_status(None)
        check("ps: the screen says it too", "WARNING" in buf.getvalue(), buf.getvalue())

    finally:
        dmn_locks.processes = orig


def test_bridge_routing_is_textual_on_a_tty_and_text_otherwise() -> None:
    """`cmd_bridge`'s own routing: on a terminal the bridge is the Textual app,
    and without the `textual` package it says so and how to fix it; run once, or
    into a pipe, it is `tui.snapshot` - plain text that needs no `textual`.
    `bridge_textual` is stubbed in `sys.modules` rather than actually run -
    what is under test is which one `cmd_bridge` reaches for. "Missing" has to
    make `import textual` itself fail: `sys.modules["textual"] = None` does,
    whether or not this interpreter has the package."""
    import argparse
    import types
    calls: list[tuple] = []
    fake = types.ModuleType("ocp.ui.bridge_textual")
    fake.run = lambda once=False: calls.append("textual") or 0
    real_snapshot = tui.snapshot
    tui.snapshot = lambda: calls.append("snapshot") or "SNAP"
    real_module = sys.modules.get("ocp.ui.bridge_textual")
    had_module = "ocp.ui.bridge_textual" in sys.modules
    had_textual = "textual" in sys.modules
    real_textual = sys.modules.get("textual")
    real_isatty = sys.stdout.isatty

    def ns(**kw):
        base = dict(once=False, textual=False)
        base.update(kw)
        return argparse.Namespace(**base)

    try:
        sys.modules["ocp.ui.bridge_textual"] = fake
        sys.stdout.isatty = lambda: True
        code = clibridge.cmd_bridge(ns())
        check("bridge routing: on a terminal the default is the Textual app",
              calls == ["textual"] and code == 0, calls)

        calls.clear()
        out = _capture(lambda: clibridge.cmd_bridge(ns(once=True)))
        check("bridge routing: --once prints the snapshot and starts no app",
              calls == ["snapshot"] and "SNAP" in out, (calls, out))

        calls.clear()
        sys.stdout.isatty = lambda: False
        out = _capture(lambda: clibridge.cmd_bridge(ns()))
        check("bridge routing: into a pipe it is the snapshot too, not a hang",
              calls == ["snapshot"] and "SNAP" in out, (calls, out))

        calls.clear()
        sys.stdout.isatty = lambda: True
        del sys.modules["ocp.ui.bridge_textual"]
        sys.modules["textual"] = None
        class _Tty(io.StringIO):
            def isatty(self) -> bool:
                return True
        buf = _Tty()
        with contextlib.redirect_stdout(buf):
            code = clibridge.cmd_bridge(ns())
        check("bridge routing: without the package on a terminal it fails loud with the fix",
              calls == [] and code == 1 and "ocp venv install" in buf.getvalue(),
              (calls, code, buf.getvalue()))

        calls.clear()
        sys.stdout.isatty = lambda: True
        out = _capture(lambda: clibridge.cmd_bridge(ns(once=True)))
        check("bridge routing: --once needs no textual at all",
              calls == ["snapshot"] and "SNAP" in out, (calls, out))
    finally:
        tui.snapshot = real_snapshot
        sys.stdout.isatty = real_isatty
        if had_module:
            sys.modules["ocp.ui.bridge_textual"] = real_module
        else:
            sys.modules.pop("ocp.ui.bridge_textual", None)
        if had_textual:
            sys.modules["textual"] = real_textual
        else:
            sys.modules.pop("textual", None)


def test_bridge_textual_missing_package_classification() -> None:
    """auditor #2138 should-fix 1: `_bridge_textual_module` must tell
    `textual`/`rich` themselves being missing (fall back) from any other
    `ModuleNotFoundError` a broken import behind them raises (a real bug -
    must be seen, not served the founder the old screen with nothing said)."""
    check("missing-package: a bare `textual` import is the missing case",
          clibridge._missing_bridge_package(ModuleNotFoundError(name="textual")))
    check("missing-package: a submodule of it is too",
          clibridge._missing_bridge_package(ModuleNotFoundError(name="textual.widgets")))
    check("missing-package: `rich` is the other one bridge_textual needs",
          clibridge._missing_bridge_package(ModuleNotFoundError(name="rich")))
    check("missing-package: an unrelated missing module is not",
          not clibridge._missing_bridge_package(ModuleNotFoundError(name="ocp.no_such_module")))
    check("missing-package: no name at all is not",
          not clibridge._missing_bridge_package(ModuleNotFoundError()))

    real_import = clibridge._import_bridge_textual
    try:
        clibridge._import_bridge_textual = lambda: (_ for _ in ()).throw(
            ModuleNotFoundError("cannot import name 'no_such_name' from 'ocp.ui.tui'", name="ocp.ui.tui"))
        raised = False
        try:
            clibridge._bridge_textual_module()
        except ModuleNotFoundError:
            raised = True
        check("missing-package: _bridge_textual_module re-raises an unrelated ModuleNotFoundError",
              raised)

        clibridge._import_bridge_textual = lambda: (_ for _ in ()).throw(
            ModuleNotFoundError("No module named 'textual'", name="textual"))
        check("missing-package: _bridge_textual_module returns None for textual itself",
              clibridge._bridge_textual_module() is None)
    finally:
        clibridge._import_bridge_textual = real_import


def test_human_inbox_routing_matches_cmd_bridge() -> None:
    """`cmd_inbox`'s human route to the screen shares `_bridge_textual_module`
    with `cmd_bridge`: a terminal with no --all/--wait opens the Textual app,
    and without the package it lists the mailbox as text instead. "Missing" goes
    through `_import_bridge_textual` (not deleting `ocp.ui.bridge_textual` from
    `sys.modules`) so this passes the same way on an interpreter that
    genuinely has `textual` - deleting only the cached module there would just
    re-import the real one and actually start it, waiting on a real terminal
    forever."""
    import argparse
    import types
    calls: list[tuple] = []
    fake = types.ModuleType("ocp.ui.bridge_textual")
    fake.run = lambda once=False: calls.append(("textual",)) or 0
    real_isatty, real_in_isatty = sys.stdout.isatty, sys.stdin.isatty
    sys.stdout.isatty = lambda: True
    sys.stdin.isatty = lambda: True  # a human at a prompt, as asking_agent reads it
    real_import = clibridge._import_bridge_textual
    real_human = cliask._human_inbox
    cliask._human_inbox = lambda all_: calls.append(("text",)) or 0

    def ns(**kw):
        base = dict(agent=actions.HUMAN, wait=False, all=False, timeout=60)
        base.update(kw)
        return argparse.Namespace(**base)

    try:
        clibridge._import_bridge_textual = lambda: fake
        cliask.cmd_inbox(ns())
        check("inbox routing: a tty with no --all/--wait opens the screen",
              calls == [("textual",)], calls)

        calls.clear()
        clibridge._import_bridge_textual = lambda: (_ for _ in ()).throw(
            ModuleNotFoundError("No module named 'textual'", name="textual"))
        cliask.cmd_inbox(ns())
        check("inbox routing: without the package it lists the mailbox as text",
              calls == [("text",)], calls)
    finally:
        sys.stdout.isatty, sys.stdin.isatty = real_isatty, real_in_isatty
        clibridge._import_bridge_textual = real_import
        cliask._human_inbox = real_human


def test_task_tree_hangs_each_task_under_what_holds_it_up() -> None:
    def row(i, blocked=(), parent=None):
        return {"id": i, "blocked_by": list(blocked), "parent_task": parent}

    def draw(rows, shown=None):
        lines = actions.task_tree(rows, {r["id"] for r in rows} if shown is None else shown)
        return [(l["prefix"] + str(l["id"]) + (" dim" if l["dim"] else "") + (" again" if l["again"] else ""))
                for l in lines]

    chain = [row(369), row(351, [369]), row(361, [351]), row(296, [351]), row(7)]
    check("task tree: a chain hangs under its root, siblings with the last one closing the branch",
          draw(chain) == ["369", "└─ 351", "   ├─ 361", "   └─ 296", "7"], draw(chain))
    many = [row(1), row(2), row(3, [1, 2])]
    check("task tree: a task blocked by two is drawn in full once, then pointed at",
          draw(many) == ["1", "└─ 3", "2", "└─ 3 again"], draw(many))
    kid = [row(10, [11]), row(11, parent=10)]
    check("task tree: a child hangs under its parent, and its block on the parent adds no cycle",
          draw(kid) == ["10", "└─ 11"], draw(kid))
    loop = [row(1, [2]), row(2, [1]), row(3, [2])]
    check("task tree: a cycle ends where it comes back, and every task is still drawn",
          draw(loop) == ["1", "└─ 2", "   ├─ 1 again", "   └─ 3"], draw(loop))
    check("task tree: a task on itself does not hang under itself", draw([row(4, [4])]) == ["4"])
    check("task tree: a hidden task holding up a shown one stays, dim; one it holds up is left out",
          draw(chain, {361}) == ["369 dim", "└─ 351 dim", "   └─ 361"], draw(chain, {361}))
    check("task tree: nothing shown, nothing drawn", draw(chain, set()) == [])


def main() -> int:
    def tests(td):
        home = Path(td) / "orcapipe"
        home.mkdir()
        run(lambda: test_dump_refuses_every_class_of_character_a_terminal_would_obey())
        run(lambda: test_task_tree_hangs_each_task_under_what_holds_it_up())
        run(lambda: test_the_panel_says_the_true_thing_about_a_queue(Path(td) / 'blk7'))
        run(lambda: test_a_report_to_the_founder_is_not_a_wait(Path(td) / 'fyi'))
        run(lambda: test_for_you_folds_to_one_line_per_agent(Path(td) / 'fold'))
        run(lambda: test_display_agrees_with_the_daemon(Path(td) / 'disp'))
        run(lambda: test_events_view_starts_at_the_newest(Path(td) / 'evview'))
        run(lambda: test_a_control_byte_is_never_accepted_as_an_identifier(
            Path(td) / 'spawnjson'))
        run(lambda: test_the_bridge_says_what_an_agent_is_where_there_is_room(Path(td) / 'agentkind'))
        run(lambda: test_the_bridges_wake_column_dates_a_weekly_wall(Path(td) / 'walledwkclock'))
        run(lambda: test_top_shows_what_is_waiting_on_the_human(Path(td) / 'foryou'))
        run(lambda: test_top_acts_through_the_same_calls_the_daemon_makes(Path(td) / 'topact'))
        run(lambda: test_a_report_shows_its_name_without_its_parents_and_keeps_the_whole_key(
            Path(td) / 'dotted'))
        run(lambda: test_a_proposal_dismissed_in_the_bridge_is_never_stranded(
            Path(td) / 'hrdismiss'))
        run(lambda: test_the_screen_says_which_account_to_spend_next(Path(td) / 'nextacct'))
        run(lambda: test_the_next_mark_does_not_flicker_between_close_accounts(
            Path(td) / 'nextflicker'))
        run(lambda: test_one_verdict_for_the_panel_and_the_page(Path(td) / 'verdict'))
        run(lambda: test_the_accounts_row_draws_a_last_seen_figure_only_inside_its_window(Path(td) / 'acctrow'))
        run(lambda: test_a_span_ends_in_its_unit(Path(td) / 'ar4f'))
        run(lambda: test_the_snapshot_is_plain_text_of_every_panel_and_nothing_a_terminal_would_obey(Path(td) / 'snapshot'))
        run(lambda: test_a_queued_request_past_the_newest_chains_is_still_on_the_agent_page(Path(td) / 'elderpage'))
        run(lambda: test_word_movement_stops_at_a_lines_edge())
        run(lambda: test_an_escape_in_a_message_body_is_stripped(Path(td) / 'wrapbody'))
        run(lambda: test_an_opened_agent_shows_its_whole_inbox_and_handoff(Path(td) / 'wholeinbox'))
        run(lambda: test_the_messenger_tab_carries_a_conversation_both_ways(Path(td) / 'chat'))
        run(lambda: test_the_cursor_follows_a_row_that_leaves_the_list(Path(td) / 'cursor'))
        run(lambda: test_each_account_guard_fails_under_its_own_mutation(Path(td) / 'guards'))
        run(lambda: test_a_wheel_notch_leaves_a_draft_untouched(Path(td) / 'ar4f'))
        run(lambda: test_a_pasted_escape_in_a_message_being_typed_is_never_sent(Path(td) / 'ar4g'))
        run(lambda: test_the_cap_is_set_from_the_command_line_and_shown_on_the_bridge(Path(td) / 'ar5'))
        run(lambda: test_t52_idle_timeout_and_ignore_cache_expiry_from_the_command_line(Path(td) / 'idletimeoutcli'))
        run(lambda: test_t424_idle_background_sets_the_notice_and_the_grace_globally_and_per_agent(Path(td) / 'idlebgcli'))
        run(lambda: test_the_accounts_panel_says_how_many_agents_are_assigned_and_running(Path(td) / 'panelagents'))
        run(lambda: test_the_team_is_drawn_the_way_it_is_routed(Path(td) / 'tree'))
        run(lambda: test_the_wake_time_is_in_the_state_cell_not_a_column(Path(td) / 'waketime'))
        run(lambda: test_a_folded_row_says_what_its_reports_are_doing(Path(td) / 'foldmark'))
        run(lambda: test_the_panel_and_the_daemon_agree_about_what_is_in_the_way(Path(td) / 'topagree'))
        run(lambda: test_the_screen_says_what_is_stuck_and_what_was_decided(Path(td) / 'topstuck'))
        run(lambda: test_the_agent_head_is_trimmed_and_slash_commands_are_derived_not_kept(Path(td) / 'headtrim'))
        run(lambda: test_a_message_in_for_you_has_a_place_in_the_conversation(Path(td) / 'foryou'))
        run(lambda: test_nothing_a_terminal_would_obey_gets_through_what_someone_else_wrote(Path(td) / 'esc'))
        run(lambda: test_an_agent_name_is_text_and_no_screen_can_be_made_to_obey_it(Path(td) / 'names'))
        run(lambda: test_no_command_prints_what_a_terminal_would_obey(Path(td) / 'cli'))
        run(lambda: test_a_module_that_returns_subprocess_text_as_a_message_imports_textsafe(
            Path(td) / 'boundary'))
        run(lambda: test_the_composer_takes_a_paste_whole(Path(td) / 'composer'))
        run(lambda: test_the_composer_cursor_moves_and_edits_in_place(Path(td) / 'composer-cursor'))
        run(lambda: test_the_composer_alt_keys_and_a_pasted_cursor_glyph(Path(td) / 'composer-alt'))
        run(lambda: test_the_agent_footer_shows_only_what_the_row_can_do(Path(td) / 'topfooter'))
        run(lambda: test_every_state_the_daemon_can_write_has_a_waker())
        run(lambda: test_the_screen_word_mapping_is_the_founders_six(Path(td) / 'screenwords'))
        run(lambda: test_the_cursor_walks_the_whole_screen(Path(td) / 'topcur'))
        run(lambda: test_a_refused_ask_is_not_a_new_message(Path(td) / 'refused'))
        run(lambda: test_the_screen_says_when_two_daemons_are_running(Path(td) / 'dmnwarn'))
        run(lambda: test_bridge_routing_is_textual_on_a_tty_and_text_otherwise())
        run(lambda: test_bridge_textual_missing_package_classification())
        run(lambda: test_human_inbox_routing_matches_cmd_bridge())
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
