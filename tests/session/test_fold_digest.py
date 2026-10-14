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
    check("burn: a span shorter than the minimum is not measured",
          T.burn_rate_per_s([(0, 60), (T.MIN_BURN_SPAN_S - 1, 75)]) == 0.0)
    check("burn: a real climb over a long enough span still measures",
          abs(T.burn_rate_per_s([(0, 60), (300, 75)]) * 60 - 3.0) < 0.01)


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
        run(lambda: test_the_digest_facts_do_not_repeat_one_event_kind(Path(td) / 'hdfacts'))
        run(lambda: test_fold_falls_back_to_claude_when_the_own_vendor_has_no_usable_account(Path(td) / 'foldfb'))
        run(lambda: test_run_model_asks_the_vendor_for_its_headless_argv(Path(td) / 'headless'))
        run(lambda: test_fold_picks_an_account_of_the_agents_own_vendor(Path(td) / 'foldvendor'))
        run(lambda: test_fold_falls_back_to_claude_for_a_vendor_that_cannot_headless(
            Path(td) / 'foldfallback'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
