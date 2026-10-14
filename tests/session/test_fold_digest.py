"""Tests: fold, digest, rollover and triggers.

Run on its own: python3 tests/session/test_fold_digest.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403


# ---------------------------------------------------------------- digest/fold
def test_digest() -> None:
    d = digest.Digest(date="2026-09-15", agent="backend", updated="14:32 (handoff #3)",
                      folded_through=18432,
                      sections={"done": ["auth refactor merged"], "in_progress": ["rate limiter"],
                                "decided": ["sqlite for events"], "blocked": [],
                                "next": ["write tests"], "base_candidates": []})
    text = digest.render(d)
    back = digest.parse(text)
    check("digest: round-trips the header", back.date == "2026-09-15" and back.agent == "backend")
    check("digest: round-trips folded_through", back.folded_through == 18432)
    check("digest: round-trips items", back.items("done") == ["auth refactor merged"])
    check("digest: all six sections present",
          all(f"## {h}" in text for _, h in digest.SECTIONS))

    ok, _ = digest.validate(text, None)
    check("digest: a good digest validates", ok)

    ok, problems = digest.validate(text.replace("## Done", "## Finished"), None)
    check("digest: a missing section fails validation", not ok and "Done" in problems[0])

    # The failure the design cares about: a fully written file that silently
    # drops completed work.
    lost = text.replace("- auth refactor merged\n", "")
    ok, problems = digest.validate(lost, back)
    check("digest: losing a completed item fails validation", not ok, str(problems))
    check("digest: the failure names what was lost", "auth refactor merged" in problems[0])

    ok, problems = digest.validate(text.replace("event 18432", "event 5"), back)
    check("digest: folded_through must not go backwards", not ok, str(problems))

    rolled = digest.carry_forward(back, "2026-09-16")
    check("rollover: done is emptied", rolled.items("done") == [])
    check("rollover: in_progress carries forward", rolled.items("in_progress") == ["rate limiter"])
    check("rollover: blocked/next carry forward", rolled.items("next") == ["write tests"])


def test_fold(home: Path) -> None:
    use_home(home)
    hand_edit_team(DESIGN_TEAM)
    agents.register("backend")

    con = db.connect()
    try:
        for i in range(4):
            db.add_event(con, "backend", "commit", {"sha": f"abc{i}", "subject": f"work {i}"})
        top = db.max_event_id(con)
    finally:
        con.close()

    day = foldmod.today()
    # A real fold once came back stamped with the time of the last event it had
    # read, hours earlier. The header is ours to write, so the model's version
    # of it is discarded - hence the wrong values here.
    good = digest.render(digest.Digest(
        date="1999-01-01", agent="somebody-else", updated="99:99", folded_through=top,
        sections={"done": ["work 0", "work 1"], "in_progress": ["work 2"], "decided": [],
                  "blocked": [], "next": ["work 3"], "base_candidates": []}))

    real = foldmod.run_model
    try:
        # --- happy path -------------------------------------------------
        foldmod.run_model = lambda *a, **k: (True, good)
        ok, msg = foldmod.fold("backend", use_model=True, fold_account="alpha")
        check("fold: a valid fold succeeds", ok, msg)
        con = db.connect()
        try:
            check("fold: cursor advances only on success",
                  db.get_cursor(con, "fold:backend") == top,
                  str(db.get_cursor(con, "fold:backend")))
        finally:
            con.close()
        published = digest.parse(foldmod.journal_path("backend", day).read_text())
        check("fold: digest is published", published.items("done") == ["work 0", "work 1"])
        check("fold: the header is stamped, not taken from the model",
              published.updated != "99:99" and published.date == day
              and published.agent == "backend",
              f"{published.date} {published.agent} {published.updated}")
        check("fold: deterministic block is attached", "events folded" in published.facts)
        check("fold: writes the digest and no handoff - that is not its job",
              kept_handoff("backend") is None)

        # --- a fully written garbage file must not replace a good one ----
        con = db.connect()
        try:
            db.add_event(con, "backend", "commit", {"sha": "zzz", "subject": "work 4"})
            top2 = db.max_event_id(con)
        finally:
            con.close()
        garbage = good.replace("- work 0\n", "")  # drops a completed item
        foldmod.run_model = lambda *a, **k: (True, garbage)
        ok, msg = foldmod.fold("backend", use_model=True, fold_account="alpha")
        check("fold: a garbage fold is rejected", not ok, msg)
        check("fold: the rejection says what was lost", "work 0" in msg, msg)
        con = db.connect()
        try:
            check("fold: cursor does NOT advance on rejection",
                  db.get_cursor(con, "fold:backend") == top,
                  f"expected {top}, got {db.get_cursor(con, 'fold:backend')}")
        finally:
            con.close()
        after = foldmod.journal_path("backend", day).read_text()
        check("fold: the degraded digest still keeps the completed work",
              "work 0" in after and "work 1" in after)
        check("fold: the degraded digest is marked as such", "degraded" in after)
        check("fold: degraded output still has the machine facts", "events folded" in after)

        # --- a failed model call takes the same safe path ----------------
        foldmod.run_model = lambda *a, **k: (False, "boom")
        ok, msg = foldmod.fold("backend", use_model=True, fold_account="alpha")
        check("fold: a failed model call degrades rather than destroying", not ok, msg)
        con = db.connect()
        try:
            check("fold: cursor still held after a failed call",
                  db.get_cursor(con, "fold:backend") == top)
        finally:
            con.close()

        # --- retrying the held delta must work once the model behaves ----
        good2 = digest.render(digest.Digest(
            date=day, agent="backend", updated="11:00", folded_through=top2,
            sections={"done": ["work 0", "work 1", "work 4"], "in_progress": [], "decided": [],
                      "blocked": [], "next": [], "base_candidates": []}))
        foldmod.run_model = lambda *a, **k: (True, good2)
        ok, msg = foldmod.fold("backend", use_model=True, fold_account="alpha")
        check("fold: the held delta is retried successfully", ok, msg)
        con = db.connect()
        try:
            check("fold: cursor finally advances past the retried delta",
                  db.get_cursor(con, "fold:backend") == top2)
        finally:
            con.close()
    finally:
        foldmod.run_model = real


def test_build_prompt_tells_the_model_not_to_write_a_started_by_reference_as_hash_n(
        home: Path) -> None:
    """auditor's #1600 NOTE, the residual of the #N fix that closed BLOCKING
    1: #N carries two meanings in this project's prose - 'the ask I am
    waiting on' and 'the ask that started this work'. Only the first should
    ever close a line, but reconcile_resolved's regex cannot tell intent
    from text - it only sees #N. Measured (still true, and expected to be -
    this is a prompt-level mitigation, not a structural one): with ids 1-8
    done, "Fix auditor's #5 BLOCKING finding" and "per #3, rework the guard"
    both still close to 완료, because both merely NAME a resolved request
    without waiting on it. The proportionate close auditor asked for is one
    more sentence in build_prompt telling the model which meaning #N is for,
    so the regex matches something the prompt actually constrains against -
    the same reasoning behind the #N rule BLOCKING 1's fix already added."""
    _reconcile_world(home)
    con = db.connect()
    try:
        for _ in range(8):
            rid = db.add_request(con, "backend", "auditor", "x", None, "peer",
                                 "fire_and_forget", None, 0, None)
            _set_request_state(con, rid, "landed", verdict="ok")

        started_by = ["Fix auditor's #5 BLOCKING finding", "per #3, rework the guard"]
        d = digest.Digest(date="2026-09-22", agent="backend", updated="09:00", folded_through=1,
                          sections={"in_progress": started_by, "blocked": [], "done": [],
                                   "decided": [], "next": [], "base_candidates": []})
        out = foldmod.reconcile_resolved(con, d)
        check("reconcile: naming a resolved request as the reason work started "
              "still closes the line - the residual, unstructural gap",
              out.items("done") == started_by, str(out.sections))
    finally:
        con.close()

    # The mitigation itself: the prompt tells the model #N means "waiting
    # on", not "started by" - one more sentence, no format change.
    prompt = foldmod.build_prompt("backend", digest.Digest(), [], "2026-09-22")
    check("build_prompt: tells the model #N means waiting-on, not started-by",
          "waiting on" in prompt and "work on it starts" in prompt
          and "another way" in prompt, prompt)

    # Auditor #1623 BLOCKING: a substring check on the prompt would pass for
    # any example at all, including one that still closes the line the
    # sentence exists to keep open - "the finding from #7" was exactly that,
    # since _ID_RE matches #N anywhere in the text, not only where the model
    # was told to put it. So the examples are read out of the live prompt
    # text itself, not a second, hand-kept list that a future edit to the
    # prompt could drift away from unnoticed, and run through
    # reconcile_resolved the same way the two counter-examples above were.
    # auditor #1623 NOTE: split()[1] on an anchor a future reword could drop
    # would raise IndexError before the "setup" check below ever ran - the
    # same shape as the .get() fix elsewhere, so the anchor split is checked
    # before it is indexed, not after.
    anchor = "another way instead ("
    parts = prompt.split(anchor, 1)
    check("build_prompt: setup - the rule's example list is where this test expects it",
          len(parts) == 2, prompt)
    segment = parts[1].split(")", 1)[0] if len(parts) == 2 else ""
    examples = re.findall(r'"([^"]+)"', segment)
    check("build_prompt: setup - the rule gives at least two examples to check",
          len(examples) >= 2, segment)
    con = db.connect()
    try:
        d2 = digest.Digest(date="2026-09-22", agent="backend", updated="09:00",
                           folded_through=1,
                           sections={"in_progress": examples, "blocked": [], "done": [],
                                    "decided": [], "next": [], "base_candidates": []})
        out2 = foldmod.reconcile_resolved(con, d2)
        check("reconcile: every example the prompt recommends stays in progress, "
              "not closed by an id it happens to contain",
              out2.items("in_progress") == examples, str(out2.sections))
    finally:
        con.close()


def test_rollover(home: Path) -> None:
    use_home(home)
    agents.register("backend")
    yesterday = "2000-01-01"
    foldmod.journal_path("backend", yesterday).write_text(digest.render(digest.Digest(
        date=yesterday, agent="backend", updated="23:59", folded_through=7,
        sections={"done": ["shipped X"], "in_progress": ["half-done Y"], "decided": ["chose Z"],
                  "blocked": ["waiting on review"], "next": ["start W"], "base_candidates": []})))
    ok, msg = foldmod.rollover("backend")
    check("rollover: runs", ok, msg)
    today_file = foldmod.journal_path("backend", foldmod.today())
    check("rollover: creates today's digest", today_file.exists())
    d = digest.parse(today_file.read_text())
    check("rollover: yesterday's done is cleared", d.items("done") == [], str(d.items("done")))
    check("rollover: in_progress survives", d.items("in_progress") == ["half-done Y"])
    check("rollover: blocked survives", d.items("blocked") == ["waiting on review"])
    check("rollover: next survives", d.items("next") == ["start W"])
    ok, msg = foldmod.rollover("backend")
    check("rollover: is idempotent", ok and "already exists" in msg, msg)


# ---------------------------------------------------------------- triggers
def test_triggers() -> None:
    T = triggers
    check("trigger: the default context-handoff limit is 150,000 tokens",
          T.CONTEXT_TOKENS == 150_000)
    check("trigger: quiet agent does not fire", not T.evaluate(10, 10).fired)
    check("trigger: context at the default fires",
          T.evaluate(10, T.CONTEXT_TOKENS).kind == "context")
    check("trigger: one token under the default does not",
          not T.evaluate(10, T.CONTEXT_TOKENS - 1).fired)
    check("trigger: an explicit ctx_limit overrides the default",
          T.evaluate(10, 300_000, ctx_limit=300_000).kind == "context"
          and not T.evaluate(10, 300_000 - 1, ctx_limit=300_000).fired)

    # A working agent runs to the wall: nothing below 100% is a usage trigger.
    for pct in (85, 90, 95, 99):
        check(f"trigger: {pct}% is not a reason to hand off", not T.evaluate(pct, 10).fired)
    t = T.evaluate(100, 10)
    check("trigger: the wall fires", t.fired and t.kind == "usage", t.reason)
    check("trigger: the weekly wall fires on its own figure",
          T.evaluate(9, 10, long_pct=100).kind == "usage_7d")
    check("trigger: 98% of the week is not a wall", not T.evaluate(9, 10, long_pct=98).fired)

    # Unknown usage is not a wall: the wall announces itself, and moving an agent
    # off an account nobody can read spends a cold start on a guess.
    t = T.evaluate(None, 10)
    check("trigger: unknown usage does not fire", not t.fired, t.reason)

    check("stay: an idle agent never moves",
          T.stay_or_move(False, 10 * 3600)[0] is False)
    check("stay: its account resets sooner than a start pays back",
          T.stay_or_move(True, 10 * 60)[0] is False)
    check("move: work waits and the reset is far", T.stay_or_move(True, 3 * 3600)[0])
    check("move: an unknown reset is not soon", T.stay_or_move(True, None)[0])
    check("stay: T follows a measured cold start",
          T.stay_within_s(13, 5 * 3600) == 13 / 100 * 5 * 3600
          and T.stay_within_s(13) == T.STAY_WITHIN_S
          and T.stay_within_s(None) == T.STAY_WITHIN_S)
    check("stretch: an empty account lasts headroom over one agent's burn",
          abs(T.stretch_s(45, 0.0) - 45 / T.TYPICAL_AGENT_BURN_PER_S) < 1e-6)
    check("stretch: fill shortens it", T.stretch_s(45, 0.02) < T.stretch_s(45, 0.0))

    rate = T.burn_rate_per_s([(0, 10), (60, 13), (120, 17)])
    check("burn: measured across the window", abs(rate * 60 - 3.5) < 0.01, str(rate * 60))
    check("burn: a reset does not produce a negative rate",
          T.burn_rate_per_s([(0, 88), (60, 2)]) == 0.0)
    check("burn: too few samples means zero", T.burn_rate_per_s([(0, 10)]) == 0.0)

    # pct_5h is a whole number, so one point over a couple of seconds reads as
    # 30%/min. That noise was enough to hand off and clear a working agent.
    noisy = T.burn_rate_per_s([(0, 62), (2, 63)])
    check("burn: a quantized step over seconds is not a rate", noisy == 0.0,
          f"{noisy * 60}%/min")
    check("burn: and it does not read as a wall", not T.evaluate(63, 10).fired)
    check("burn: a span shorter than the minimum is not measured",
          T.burn_rate_per_s([(0, 60), (T.MIN_BURN_SPAN_S - 1, 75)]) == 0.0)
    check("burn: a real climb over a long enough span still measures",
          abs(T.burn_rate_per_s([(0, 60), (300, 75)]) * 60 - 3.0) < 0.01)

    # The weekly window is run to its own wall too.
    t = T.evaluate(9, 10, long_pct=100)
    check("trigger: a longest-window reading at its wall fires",
          t.fired and t.kind == "usage_7d" and "longest window" in t.reason, t.reason)
    check("trigger: no 7d reading at all is not a 7d refusal",
          not T.evaluate(9, 10, long_pct=None).fired)
    check("trigger: a 5h wall wins when both are at theirs",
          T.evaluate(100, 10, long_pct=100).kind == "usage")


def test_fold_is_not_rejected_over_a_header_it_overwrites(home: Path) -> None:
    """The header is stamped after validation, so validating it throws away good
    section work over a line that was about to be replaced - and rejection is
    expensive: the degraded path, no cursor advance, a fold_failed event."""
    good = digest.render(digest.Digest(
        date="2026-01-01", agent="x", updated="10:00", folded_through=5,
        sections={"done": ["a"], "in_progress": [], "decided": [],
                  "blocked": [], "next": [], "base_candidates": []}))
    headerless = good.split("\n", 1)[1]
    ok, problems = digest.validate(headerless, None)
    check("digest: a malformed header is still a problem by default",
          not ok and any("header" in p for p in problems), str(problems))
    ok, problems = digest.validate(headerless, None, header_is_stamped=True)
    check("digest: but not for the caller that stamps it", ok, str(problems))


def test_a_fold_is_a_process_not_a_session(home: Path) -> None:
    """`hl fold` is a headless `claude -p` that inherits whatever OCP_AGENT its
    caller had. Counting it as the agent's session would tell the sweep an agent
    is alive for as long as a fold runs, and closed the moment it ends."""
    check("observe: an agent's session counts",
          obsmod.is_session("/usr/bin/claude --model sonnet --effort high"))
    check("observe: a fold does not",
          not obsmod.is_session("/usr/bin/claude --model haiku -p write me a digest"))
    check("observe: nor a fold with the prompt last",
          not obsmod.is_session("/usr/bin/claude --model haiku -p"))
    check("observe: and nor does anything that is not claude",
          not obsmod.is_session("/usr/bin/ssh somewhere"))


def test_a_rejected_fold_still_records_what_it_spent(home: Path) -> None:
    """A fold is the one spender no status line observes, so these events are
    the only record that the account was charged. A fold that ran the model and
    was then rejected spent exactly what an accepted one spends, so leaving the
    account off the failure made the spend reconstructable for the folds that
    worked and blank for the ones that went wrong."""
    use_home(home)
    agents.register("spender")
    con = db.connect()
    real = foldmod.run_model
    try:
        db.add_event(con, "spender", "UserPromptSubmit", {"prompt": "do the thing"})
        foldmod.run_model = lambda prompt, acct, model: (True, "not a digest at all")
        ok, msg = foldmod.fold("spender", use_model=True, fold_account="alpha")
        check("fold: an unusable answer is rejected", not ok, msg)
        rows = [r for r in db.latest_events(con, agent="spender", limit=20)
                if r["kind"] == "fold_failed"]
        check("fold: and the rejection is recorded", len(rows) == 1, str(len(rows)))
        payload = json.loads(rows[-1]["payload"])
        check("fold: naming the account that was charged for it",
              payload.get("account") == "alpha", str(payload))
        check("fold: and the model that actually ran - claude's own default, "
              "resolved even though nothing asked for one by name",
              payload.get("model") == "haiku", str(payload))
    finally:
        foldmod.run_model = real
        con.close()


def test_the_handoff_is_three_facts_read_without_a_model(home: Path) -> None:
    """A handoff has to work when every account is walled, which is when a
    model call cannot: it is the request in hand, the session's transcript path
    and the worktree's state, read from disk, and it is degraded only when one
    of those could not be read."""
    _daemon_home(home, 50)
    wt = _worktree_with_a_dirty_file(home / "wt-backend")
    real = foldmod.run_model
    foldmod.run_model = lambda *a, **k: (_ for _ in ()).throw(AssertionError("model called"))
    con = db.connect()
    try:
        db.set_terminal(con, "backend", "term_x", str(wt))
        rid = db.add_request(con, "marina", "backend", "fix the routing\nsecond line", None,
                             "peer", "async", None, 0, None)
        db.mark_delivered(con, rid)
        write_agent_status("backend", "alpha", session_id="sess-1",
                           transcript_path="/tmp/sess-1.jsonl")

        missing = handoffmod.record(con, "backend")
        text = db.get_handoff(con, "backend")
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=wt,
                              capture_output=True, text=True).stdout.strip()
        check("handoff: nothing is missing when the facts can be read",
              missing == [], str(missing))
        check("handoff: it names the request in hand, first line only",
              f"#{rid} from marina - fix the routing" in text and "second line" not in text,
              text)
        check("handoff: it names the previous session's transcript",
              "sess-1" in text and "/tmp/sess-1.jsonl" in text, text)
        check("handoff: it gives the branch, HEAD and what is uncommitted",
              f"branch: topic @ {head}" in text and "?? dirty.txt" in text, text)
        check("handoff: and nothing else - no digest sections",
              "진행중" not in text and "recent commits" not in text, text)

        # A request nobody has been shown yet is not one in hand.
        db.add_request(con, "marina", "backend", "later", None, "peer", "async", None, 0, None)
        _set_request_state(con, rid, "landed", "ok")
        check("handoff: no delivered request is a fact, not a gap",
              handoffmod.record(con, "backend") == []
              and "request: none in progress" in db.get_handoff(con, "backend"))

        # Each fact that cannot be read degrades it, and says which.
        paths.agent_status_file("backend").unlink()
        gaps = handoffmod.record(con, "backend")
        check("handoff: no snapshot of the session names the transcript as the gap",
              gaps == ["the session's transcript path"], str(gaps))
        check("handoff: and the record says so where the agent reads it",
              "could not read the session's transcript path" in db.get_handoff(con, "backend")
              and "branch: topic" in db.get_handoff(con, "backend"))
        db.set_terminal(con, "backend", "term_x", str(home / "gone"))
        gaps = handoffmod.record(con, "backend")
        check("handoff: a worktree that is gone is the other gap",
              len(gaps) == 2 and gaps[1].startswith("the worktree"), str(gaps))
    finally:
        foldmod.run_model = real
        con.close()


def test_the_handoff_carries_the_agents_progress_note_on_its_request(home: Path) -> None:
    """T60: the agent writes what is done and what is left on the request in
    hand (`hl progress`); the handoff carries it, and only for that request."""
    _daemon_home(home, 50)
    con = db.connect()
    try:
        db.set_terminal(con, "backend", "term_x", str(_worktree_with_a_dirty_file(home / "wt-backend")))
        write_agent_status("backend", "alpha", session_id="sess-1",
                           transcript_path="/tmp/sess-1.jsonl")
        ok, said = handoffmod.set_progress(con, "backend", "deferred: waits on T9")
        check("progress: no request in hand, the note is kept on the agent (T366)",
              ok and handoffmod.get_progress(con, "backend") == "deferred: waits on T9", said)
        handoffmod.record(con, "backend")
        check("progress: the handoff of an agent with no request carries it",
              "deferred: waits on T9" in db.get_handoff(con, "backend"))
        rid = db.add_request(con, "marina", "backend", "fix it", None,
                             "peer", "async", None, 0, None)
        db.mark_delivered(con, rid)
        _set_request_state(con, rid, "landed", "ok")
        check("progress: an idle note does not resurface after a request came and went (T366)",
              handoffmod.get_progress(con, "backend") is None)
        rid = db.add_request(con, "marina", "backend", "fix it", None,
                             "peer", "async", None, 0, None)
        ok, said = handoffmod.set_progress(con, "backend", "x")
        check("progress: a request not yet delivered is not one in hand; the note stays on the agent",
              ok and said.startswith("you"), said)
        t1 = db.new_task(con, "one", "backend", "marina")
        t2 = db.new_task(con, "two", "backend", "marina")
        db.set_current_task(con, "backend", t1)
        ok, said = handoffmod.set_progress(con, "backend", "half of T1")
        db.set_current_task(con, "backend", t2)
        check("progress: with a task in hand the note is the task's, not the next task's (T366)",
              ok and said.startswith(f"T{t1}") and handoffmod.get_progress(con, "backend") is None, said)
        db.set_current_task(con, "backend", None)
        db.mark_delivered(con, rid)
        ok, said = handoffmod.set_progress(con, "backend", "done: parser\nleft: tests")
        check("progress: kept on the delivered request", ok and said == f"#{rid}", said)
        check("progress: the agent's own note is dropped once a request has one",
              db.get_meta(con, "progress-agent:backend") is None)
        handoffmod.record(con, "backend")
        text = db.get_handoff(con, "backend")
        check("progress: the handoff carries both lines, right after the request",
              f"#{rid} from marina" in text and "  done: parser\n  left: tests" in text
              and text.index("progress") > text.index(f"#{rid}"), text)
        ok, _ = handoffmod.set_progress(con, "backend", "a\x1b[31mb\x9bc\u2028d")
        kept = handoffmod.get_progress(con, "backend")
        check("progress: ESC, a C1 control and U+2028 never reach the handoff; U+2028 is a line break",
              ok and kept == "abc\nd" and "\x1b" not in db.get_meta(con, f"progress:{rid}"),
              repr(kept))
        handoffmod.set_progress(con, "backend", "done: parser\nleft: tests")
        ok, said = handoffmod.set_progress(con, "backend", "y" * (handoffmod.PROGRESS_MAX + 1))
        check("progress: a note past the cap is refused and the old one stays",
              not ok and handoffmod.get_progress(con, "backend") == "done: parser\nleft: tests", said)
        handoffmod.set_progress(con, "backend", "")
        handoffmod.record(con, "backend")
        check("progress: an empty note clears it", "progress" not in db.get_handoff(con, "backend"))
        handoffmod.set_progress(con, "backend", "old")
        _set_request_state(con, rid, "landed", "ok")
        rid2 = db.add_request(con, "marina", "backend", "next", None,
                              "peer", "async", None, 0, None)
        db.mark_delivered(con, rid2)
        handoffmod.record(con, "backend")
        check("progress: the next request does not inherit the last one's note",
              "progress" not in db.get_handoff(con, "backend"), db.get_handoff(con, "backend"))
    finally:
        con.close()


def test_the_handoff_names_the_effective_head_not_a_blocked_one(home: Path) -> None:
    """T337 round 4, auditor #2028 finding 2: _request_line, set_progress and
    get_progress read db.head_of_queue directly - with a true head blocked
    on record, that named the wrong request: one the agent was never told,
    not the one behind it that it is actually working on. A resumed session
    would be told it is working on the wrong thing, and a progress note
    would land on a request that never carries it forward."""
    _daemon_home(home, 50)
    con = db.connect()
    try:
        db.set_terminal(con, "backend", "term_x", str(_worktree_with_a_dirty_file(home / "wt-backend")))
        write_agent_status("backend", "alpha", session_id="sess-1",
                           transcript_path="/tmp/sess-1.jsonl")

        ok, msg, tid = actions.task_new("product", "backend", "the blocked head", "the blocked head")
        check("setup: backend holds the task", ok, msg)
        head = db.task_requests(con, tid)[0]["id"]
        nxt = db.add_request(con, "reviewer", "backend", "fix it", None,
                             "peer", "async", None, 0, None)
        db.mark_delivered(con, nxt)
        ok, msg, t_blocker = actions.task_new("product", "reviewer", "unrelated", "unrelated")
        check("setup: a task to block on", ok, msg)
        ok, msg = actions.add_block(con, tid, t_blocker, "product")
        check("setup: the task is now blocked", ok, msg)

        line = handoffmod._request_line(con, "backend")
        check("handoff line: names the effective head, not the blocked true one",
              f"#{nxt} from reviewer" in line and f"#{head}" not in line, line)

        ok, said = handoffmod.set_progress(con, "backend", "done: the review\nleft: nothing")
        check("progress: kept on the effective head, not the blocked one",
              ok and said == f"#{nxt}", said)
        check("progress: read back the same way",
              handoffmod.get_progress(con, "backend") == "done: the review\nleft: nothing")
        check("progress: the blocked head itself carries no note",
              handoffmod.progress_of(con, head) is None)

        handoffmod.record(con, "backend")
        text = db.get_handoff(con, "backend")
        check("handoff: the record names the effective head",
              f"#{nxt} from reviewer" in text and f"#{head}" not in text, text)
    finally:
        con.close()


def test_the_digest_facts_do_not_repeat_one_event_kind(home: Path) -> None:
    """The machine record in a digest counts turn boundaries and quotes what
    happened, instead of repeating one event kind sixteen times."""
    use_home(home)
    agents.register("backend")
    now = int(time.time())
    events = [{"id": i, "ts": now, "kind": "SubagentStop", "payload": "{}"} for i in range(20)]
    events.append({"id": 99, "ts": now, "kind": "commit", "payload": '{"sha": "abc"}'})
    facts = foldmod.deterministic_facts("backend", events, repo=None)
    check("digest: the record counts turn boundaries and quotes what happened",
          "SubagentStopx20" in facts and facts.count("SubagentStop {") == 0
          and "commit" in facts, facts)


def test_run_model_asks_the_vendor_for_its_headless_argv(home: Path) -> None:
    """run_model builds argv from the account's own driver (design/04 §4.2),
    and refuses cleanly - no subprocess run at all - for a vendor whose
    driver says it has no one-shot run."""
    use_home(home)
    accounts.register("alpha")
    (vendors.config_dir("alpha") / ".credentials.json").write_text("{}")
    cdrv = vendors.get("claude")
    real_binary, real_run = cdrv.binary, subprocess.run
    seen: list[list[str]] = []
    try:
        cdrv.binary = lambda: "/x/bin/claude"

        def fake_run(argv, **kw):
            seen.append(argv)
            return subprocess.CompletedProcess(argv, 0, "ok", "")
        subprocess.run = fake_run
        ok, out = foldmod.run_model("hi", "alpha", "haiku")
        check("run_model: claude's argv is --model then -p prompt",
              ok and seen[0] == ["/x/bin/claude", "--model", "haiku", "-p", "hi"], str(seen))
    finally:
        cdrv.binary, subprocess.run = real_binary, real_run
    with second_vendor() as drv:
        drv.headless_argv = lambda exe, model, prompt: None
        accounts.register("zeta", vendor="zai")
        (vendors.config_dir("zeta") / ".credentials.json").write_text("{}")
        ok, out = foldmod.run_model("hi", "zeta", "haiku")
        check("run_model: a vendor with no one-shot run refuses cleanly",
              not ok and "no one-shot run" in out, out)


def test_fold_picks_an_account_of_the_agents_own_vendor(home: Path) -> None:
    """A fold auto-picking (no `fold_account`) asks `_cheapest_account` for
    the agent's own vendor, not always claude's - it reaches `run_model` with
    a second-vendor account for a second-vendor agent (design/04 §4.2)."""
    use_home(home)
    with second_vendor(metered=True):
        agents.register("zbackend")
        _with_vendor("zbackend", "zai")
        accounts.register("alpha")
        write_status(home, "alpha", pct_5h=10)
        accounts.register("zeta", vendor="zai")
        write_status(home, "zeta", pct_5h=10)
        accounts.logged_in = lambda a: True
        seen: list[str] = []
        real = foldmod.run_model
        try:
            foldmod.run_model = lambda prompt, acct, model: (seen.append(acct), (False, "stop"))[1]
            con = db.connect()
            try:
                db.add_event(con, "zbackend", "commit", {"sha": "a", "subject": "x"})
            finally:
                con.close()
            foldmod.fold("zbackend", use_model=True)
        finally:
            foldmod.run_model = real
        check("fold: a second-vendor agent's auto-pick lands on its own vendor's account",
              seen == ["zeta"], str(seen))


def test_fold_falls_back_to_claude_for_a_vendor_that_cannot_headless(home: Path) -> None:
    """An opencode agent's auto-pick still reaches a claude account for its
    fold call, as every fold did before vendors existed: opencode's driver
    has no one-shot run (`can_headless()` is False), so `_fold_account` falls back to claude's
    rather than picking an opencode account only to have `run_model` refuse
    it (design/04 §4.2, auditor's #321 should-fix 1 - this is not a
    behaviour change)."""
    use_home(home)
    agents.register("glm", model="zai-coding-plan/glm-5.3-flash")
    _with_vendor("glm", "opencode")
    accounts.register("alpha")
    write_status(home, "alpha", pct_5h=10)
    accounts.register("zai", vendor="opencode")
    write_status(home, "zai", pct_5h=10)
    accounts.logged_in = lambda a: True
    seen: list[str] = []
    real = foldmod.run_model
    try:
        foldmod.run_model = lambda prompt, acct, model: (seen.append(acct), (False, "stop"))[1]
        con = db.connect()
        try:
            db.add_event(con, "glm", "commit", {"sha": "a", "subject": "x"})
        finally:
            con.close()
        foldmod.fold("glm", use_model=True)
    finally:
        foldmod.run_model = real
    check("fold: an opencode agent's auto-pick still lands on a claude account",
          seen == ["alpha"], str(seen))


def test_fold_falls_back_to_claude_when_the_own_vendor_has_no_usable_account(home: Path) -> None:
    """T90: an agent whose vendor can run headless but has no readable account
    of it still gets its model call, on a claude account."""
    use_home(home)
    with second_vendor(metered=True):
        agents.register("zbackend")
        _with_vendor("zbackend", "zai")
        accounts.register("alpha")
        write_status(home, "alpha", pct_5h=10)
        accounts.logged_in = lambda a: True
        check("fold: the second vendor says it can run one-shot",
              vendors.get("zai").can_headless() is True and vendors.get("opencode").can_headless() is False
              and vendors.get("claude").can_headless() is True)
        check("fold: no zai account, so the pick falls to claude's",
              foldmod._fold_account("zbackend") == "alpha", str(foldmod._fold_account("zbackend")))


def test_korean_digests_read_and_are_migrated_to_english_headings(home: Path) -> None:
    """Digests written before the headings were English still parse; the upgrade rewrites them once,
    touching only heading lines; and the HR answer words the founder used before still count."""
    old = ("# 2026-09-15 \u00b7 backend\nupdated: 14:32 \u00b7 folded_through: event 7\n\n"
           "## \uc644\ub8cc\n- shipped \uc644\ub8cc in the item text\n\n## \uc9c4\ud589\uc911\n- rate limiter\n\n"
           "## \uacb0\uc815\n\n## \ub9c9\ud78c \uac83\n\n## \ub2e4\uc74c\n\n## base \uc2b9\uaca9 \ud6c4\ubcf4\n\n"
           "## \uc9c0\uac01 \ubc18\uc601\n- late one\n\n## \uae30\uacc4 \uae30\ub85d\nfacts here\n")
    d = digest.parse(old)
    check("old digest: the Korean headings parse into the same sections",
          d.items("done") == ["shipped \uc644\ub8cc in the item text"] and d.items("in_progress") == ["rate limiter"]
          and d.late == ["late one"] and d.facts == "facts here", str(d))
    check("old digest: it still validates", digest.validate(old, None)[0])
    new = digest.migrate_text(old)
    check("migrate: headings are English, an item containing a Korean word is untouched",
          "## Done" in new and "## In progress" in new and "## Late additions" in new
          and "## Machine record" in new and "- shipped \uc644\ub8cc in the item text" in new
          and "## \uc644\ub8cc" not in new, new)
    check("migrate: the result parses as the same digest", digest.parse(new) == d)

    agents.register("backend")
    j = foldmod.journal_path("backend", "2026-09-15")
    j.write_text(old)
    con = db.connect()
    try:
        con.execute("DELETE FROM cursors WHERE name = 'migrated:english_digests'")
    finally:
        con.close()
    con = support.upgrade()
    try:
        check("upgrade: the stored digest is rewritten in English, once",
              j.read_text() == new and db.get_cursor(con, "migrated:english_digests") == 1, j.read_text())
        j.write_text(old)
    finally:
        con.close()
    con = support.upgrade()
    con.close()
    check("upgrade: a second connect does not touch it again", j.read_text() == old)
    # A journal folder it cannot write must not fail a connect, now or on any later one.
    import os
    j.write_text(old)
    con = db.connect()
    try:
        con.execute("DELETE FROM cursors WHERE name = 'migrated:english_digests'")
    finally:
        con.close()
    os.chmod(j.parent, 0o555)
    try:
        try:
            support.upgrade().close()
            failed = ""
        except OSError as e:
            failed = str(e)
        check("upgrade: an unwritable journal folder does not fail a connect, the file stays readable",
              not failed and digest.parse(j.read_text()) == d, failed)
    finally:
        os.chmod(j.parent, 0o755)
    check("hr: the Korean approve and reject still decide, the English ones too",
          decisions.call_of("\uc2b9\uc778") == "approved" and decisions.call_of("\ubc18\ub824 \u2014 no") == "rejected"
          and decisions.call_of("approve") == "approved" and decisions.call_of("later") == "unclear")


def main() -> int:
    def tests(td):
        home = Path(td) / "orcapipe"
        home.mkdir()
        run(lambda: test_digest())
        run(lambda: test_korean_digests_read_and_are_migrated_to_english_headings(Path(td) / 'enmig'))
        run(lambda: test_fold(Path(td) / 'foldhome'))
        run(lambda: test_build_prompt_tells_the_model_not_to_write_a_started_by_reference_as_hash_n(
            Path(td) / 'reconhashn'))
        run(lambda: test_rollover(Path(td) / 'rollhome'))
        run(lambda: test_triggers())
        run(lambda: test_fold_is_not_rejected_over_a_header_it_overwrites(Path(td) / 'fhdr'))
        run(lambda: test_a_fold_is_a_process_not_a_session(Path(td) / 'foldproc'))
        run(lambda: test_a_rejected_fold_still_records_what_it_spent(Path(td) / 'spend'))
        run(lambda: test_the_handoff_is_three_facts_read_without_a_model(Path(td) / 'hdhome'))
        run(lambda: test_the_handoff_carries_the_agents_progress_note_on_its_request(Path(td) / 'hdprog'))
        run(lambda: test_the_handoff_names_the_effective_head_not_a_blocked_one(Path(td) / 'hdprogblocked'))
        run(lambda: test_the_digest_facts_do_not_repeat_one_event_kind(Path(td) / 'hdfacts'))
        run(lambda: test_fold_falls_back_to_claude_when_the_own_vendor_has_no_usable_account(Path(td) / 'foldfb'))
        run(lambda: test_run_model_asks_the_vendor_for_its_headless_argv(Path(td) / 'headless'))
        run(lambda: test_fold_picks_an_account_of_the_agents_own_vendor(Path(td) / 'foldvendor'))
        run(lambda: test_fold_falls_back_to_claude_for_a_vendor_that_cannot_headless(
            Path(td) / 'foldfallback'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
