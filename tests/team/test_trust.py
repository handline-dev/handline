"""Tests: trusting worktrees and the trust prompt.

Run on its own: python3 tests/team/test_trust.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403




def test_trust_roots(home: Path) -> None:
    """Blocker 3 (auditor's #304): roots are an explicit list the founder
    sets, never inferred from the roster or Orca's own layout - propose/apply
    for an agent, enact directly for the founder, is_declared checks a path
    against exactly that list."""
    use_home(home)
    hand_edit_team(
        "agents:\n  backend: {}\n"
        'edges:\n  - { from: "*", to: "human", kind: escalate, creates_gate: true }\n')
    agents.register("backend")
    root = home / "repos" / "widget"
    root.mkdir(parents=True, exist_ok=True)

    check("trust: nothing is declared to start", not trustmod.list_roots())
    check("trust: is_declared is false with nothing declared",
          not trustmod.is_declared(root))

    ok, msg = trustmod.enact(str(root), "the founder's own say-so")
    check("trust: the founder enacts directly, no proposal needed", ok, msg)
    check("trust: it is now declared", trustmod.is_declared(root))
    check("trust: a worktree under it counts too",
          trustmod.is_declared(root / "some-agent-worktree"))
    check("trust: a sibling directory does not",
          not trustmod.is_declared(root.parent / "other"))

    ok, msg = trustmod.enact(str(root), "again")
    check("trust: enacting an already-declared root is refused", not ok, msg)

    ok, msg = trustmod.propose(str(home / "repos" / "second"), "", requester="backend")
    check("trust: a proposal needs a reason", not ok, msg)

    ok, msg = trustmod.propose(str(home / "repos" / "second"), "needs it for work",
                               requester="backend")
    check("trust: an agent proposes rather than declaring directly", ok, msg)
    props = trustmod.listing()
    check("trust: the proposal is on record", len(props) == 1, str(props))
    pid, rid = props[0]["id"], props[0]["request"]

    ok, msg = trustmod.apply(pid)
    check("trust: applying before the founder answers is refused", not ok, msg)

    ok, msg = askmod.resolve(rid, "approve")
    check("trust: an approve declares the root at once, and says so", ok and "applied" in msg, msg)
    check("trust: it is now declared",
          trustmod.is_declared(home / "repos" / "second"))
    check("trust: and the proposer's answer carries the outcome",
          f"proposal {pid} applied" in db.get_request(db.connect(), rid)["verdict"])
    ok, msg = trustmod.apply(pid)
    check("trust: applying twice is refused", not ok, msg)

    ok, msg = trustmod.propose(str(home / "repos" / "third"), "for later",
                               requester="backend")
    third = next(p for p in trustmod.listing()  # listing() is ordered by random id
                 if p["root"] == trustmod._resolved(home / "repos" / "third"))
    pid3, rid3 = third["id"], third["request"]
    askmod.resolve(rid3, "reject - not yet")
    ok, msg = trustmod.apply(pid3)
    check("trust: a rejected proposal is never applied", not ok, msg)
    check("trust: and its file records the reject at once",
          trustmod.load_proposal(pid3)["state"] == "rejected")
    trustmod.propose(str(home / "repos" / "multi"), "needs it", requester="backend")
    multi = next(p for p in trustmod.listing()
                 if p["root"] == trustmod._resolved(home / "repos" / "multi"))
    ok, msg = askmod.resolve(multi["request"], "reject - not yet\nask again next week")
    check("trust: a two-line reject still answers the proposer, and the file records it",
          ok and trustmod.load_proposal(multi["id"])["state"] == "rejected", msg)
    check("trust: and the root stays undeclared",
          not trustmod.is_declared(home / "repos" / "third"))

    ok, msg = trustmod.remove(str(root))
    check("trust: the founder can remove a root, no gate needed", ok, msg)
    check("trust: it reads as undeclared again", not trustmod.is_declared(root))
    ok, msg = trustmod.remove(str(root))
    check("trust: removing an undeclared root is refused", not ok, msg)

    # note 6 (auditor's #339): is_declared and remove must agree on what a
    # root's own spelling is, or a symlinked path declared one way is never
    # removable the other way.
    real_root = home / "repos" / "real"
    real_root.mkdir(parents=True, exist_ok=True)
    link_root = home / "repos" / "linked"
    link_root.symlink_to(real_root)
    ok, msg = trustmod.enact(str(link_root), "via a symlink")
    check("trust: enacted through a symlinked spelling", ok, msg)
    check("trust: stored under the resolved (real) path",
          trustmod.list_roots()[-1]["root"] == str(real_root.resolve()),
          trustmod.list_roots()[-1])
    ok, msg = trustmod.remove(str(link_root))
    check("trust: removed through the same symlinked spelling", ok, msg)
    check("trust: gone, checked either way",
          not trustmod.is_declared(real_root) and not trustmod.is_declared(link_root))

    # note 7 (auditor's #339): apply refuses a proposal whose root was
    # changed after the founder approved a (different) one.
    ok, msg = trustmod.propose(str(home / "repos" / "fourth"), "for a test",
                               requester="backend")
    check("trust: the fourth proposal went through", ok, msg)

    def proposal_for(path) -> dict:
        # listing() is ordered by proposal id, which is random.
        want = trustmod._resolved(path)
        return next(p for p in trustmod.listing() if p["root"] == want)

    pid4 = proposal_for(home / "repos" / "fourth")["id"]
    rid4 = proposal_for(home / "repos" / "fourth")["request"]
    tampered = trustmod.load_proposal(pid4)
    tampered["root"] = str(home / "repos" / "not-what-was-approved")
    trustmod._save_proposal(tampered)
    askmod.resolve(rid4, "approve")
    ok, msg = trustmod.apply(pid4)
    check("trust: apply refuses a root that does not match what was approved",
          not ok, msg)
    check("trust: and nothing is declared from it",
          not trustmod.is_declared(home / "repos" / "not-what-was-approved"))

    # auditor's #384: a substring test let through a proposal edited to a
    # PARENT of the path the founder read, which widens consent to everything
    # under it.
    ok, msg = trustmod.propose(str(home / "repos" / "fifth" / "deep"), "for a test",
                               requester="backend")
    check("trust: the fifth proposal went through", ok, msg)
    pid5 = proposal_for(home / "repos" / "fifth" / "deep")["id"]
    widened = trustmod.load_proposal(pid5)
    widened["root"] = str(home / "repos" / "fifth")
    trustmod._save_proposal(widened)
    askmod.resolve(widened["request"], "approve")
    ok, msg = trustmod.apply(pid5)
    check("trust: apply refuses a proposal edited to a parent of what was approved",
          not ok and "changed after the founder answered" in msg, msg)
    check("trust: and neither the parent nor the child is declared from it",
          not trustmod.is_declared(home / "repos" / "fifth")
          and not trustmod.is_declared(home / "repos" / "fifth" / "deep"))
    widened["root"] = str(home / "repos" / "fifth" / "deep")
    trustmod._save_proposal(widened)
    ok, msg = trustmod.apply(pid5)
    check("trust: the path as approved still applies", ok, msg)


def test_trust_roots_live_in_the_protected_store(home: Path) -> None:
    """T179 (founder #1006): the declared roots and the founder's answers to
    trust and roster proposals are kept where no agent can write them, and the
    old trust_roots.json is moved there once."""
    use_home(home)
    hand_edit_team(
        "agents:\n  backend: {}\n"
        'edges:\n  - { from: "*", to: "human", kind: escalate, creates_gate: true }\n')
    agents.register("backend")
    from ocp.infra import protected
    legacy = trustmod.legacy_path()
    legacy.write_text(json.dumps([{"root": str(home / "repos"), "why": "old", "added_by": "human",
                                   "added_at": 1}]))
    real = os.environ.get("OCP_AGENT")
    os.environ["OCP_AGENT"] = "backend"
    try:
        check("migrate: an agent's session reads nothing from the old file",
              trustmod.list_roots() == [] and legacy.exists())
        check("migrate: and cannot move it", trustmod.migrate_legacy() is None and legacy.exists())
    finally:
        os.environ.pop("OCP_AGENT", None)
    check("migrate: doctor says it is not moved yet",
          any("not moved yet" in n for _l, n, _d in _doctor_rows()), str(_doctor_rows()))
    said = trustmod.migrate_legacy()
    check("migrate: the founder moves it, and is shown each root to check",
          said and "check they are all yours" in said and str(home / "repos") in said, str(said))
    check("migrate: and the roots are there",
          [r["why"] for r in trustmod.list_roots()] == ["old"] and not legacy.exists())
    check("migrate: doctor lists the declared roots, so they can be checked at any time",
          any(n == "declared trust roots: 1" and str(home / "repos") in d
              for _l, n, d in _doctor_rows()), str(_doctor_rows()))
    check("migrate: once - the roots are in the protected store",
          protected.get("trust", "roots.json") is not None)
    legacy.write_text(json.dumps([{"root": "/etc", "why": "planted", "added_by": "human"}]))
    check("migrate: a file put back afterwards is never read as the founder's",
          [r["why"] for r in trustmod.list_roots()] == ["old"])
    check("migrate: and doctor names it", any("is back in OCP_HOME" in n
                                              for _l, n, _d in _doctor_rows()))
    legacy.unlink()

    os.environ["OCP_AGENT"] = "backend"
    try:
        ok, msg = trustmod.enact(str(home / "x"), "an agent adding a root")
        check("roots: an agent cannot declare one, whatever it calls", not ok and "founder's" in msg, msg)
        ok, msg = trustmod.remove(str(home / "repos"))
        check("roots: nor drop one", not ok and trustmod.is_declared(home / "repos"), msg)
        check("roots: but it can read them", trustmod.is_declared(home / "repos"))
    finally:
        os.environ.pop("OCP_AGENT", None)

    ok, msg = trustmod.propose(str(home / "second"), "needs it", requester="backend")
    pid, rid = trustmod.listing()[0]["id"], trustmod.listing()[0]["request"]
    con = db.connect()
    try:
        con.execute("UPDATE requests SET state = 'done', verdict = 'approve' WHERE id = ?", (rid,))
        ok, msg = trustmod.apply(pid, con)
        check("answers: a verdict written into the db alone declares nothing",
              not ok and "no answer from the founder is on record" in msg
              and not trustmod.is_declared(home / "second"), msg)
    finally:
        con.close()
    ok, msg = trustmod.propose(str(home / "third"), "needs this too", requester="backend")
    third = [p for p in trustmod.listing() if p["root"].endswith("third")][0]
    ok, msg = askmod.resolve(third["request"], "approve")
    check("answers: the founder's own approve records the answer and declares the root",
          ok and trustmod.is_declared(home / "third")
          and decisions.get("trust", third["id"])["call"] == "approved", msg)
    check("answers: the record names the request", decisions.get("trust", third["id"])["request"]
          == third["request"])
    check("answers: and the root the founder was shown",
          decisions.get("trust", third["id"])["subject"] == trustmod._resolved(third["root"]))
    check("answers: a draft edited afterwards is no longer what was approved",
          decisions.refusal("trust", third["id"], third["request"], str(home / "elsewhere"))
          is not None)
    other = decisions.refusal("trust", third["id"], third["request"] + 1, third["root"])
    check("answers: a record for another request is no answer", other is not None, str(other))

    # T182: the answer is for the root the founder was shown. An agent that
    # rewrites both the draft and the request body afterwards changes neither.
    ok, msg = trustmod.propose(str(home / "fourth"), "and this", requester="backend")
    fourth = [p for p in trustmod.listing() if p["root"].endswith("fourth")][0]
    con = db.connect()
    try:
        subject, why = trustmod.answered(fourth, db.get_request(con, fourth["request"]))
        decisions.record("trust", fourth["id"], fourth["request"], "approved", subject)
        con.execute("UPDATE requests SET state = 'done', verdict = 'approve' WHERE id = ?",
                    (fourth["request"],))
        body = db.get_request(con, fourth["request"])["body"].replace(
            str(home / "fourth"), str(home / "repos"))
        con.execute("UPDATE requests SET body = ? WHERE id = ?", (body, fourth["request"]))
        forged = dict(fourth, root=str(home / "repos"))
        trustmod._save_proposal(forged)
        ok, msg = trustmod.apply(fourth["id"], con)
        check("bind: rewriting the draft and the request body afterwards is refused",
              not ok and "changed after the founder answered" in msg, msg)
    finally:
        con.close()

    ok, msg = trustmod.propose(str(home / "sixth"), "and this", requester="backend")
    sixth = [p for p in trustmod.listing() if p["root"].endswith("sixth")][0]
    con = db.connect()
    try:
        con.execute("UPDATE requests SET body = ? WHERE id = ?",
                    ("[trust] something else entirely", sixth["request"]))
    finally:
        con.close()
    ok, msg = askmod.resolve(sixth["request"], "approve")
    check("bind: an escalation that names no root approves nothing",
          "does not name the root" in msg and not trustmod.is_declared(home / "sixth")
          and decisions.get("trust", sixth["id"]) is None, msg)

    ok, msg = trustmod.propose(str(home / "seventh"), "and this", requester="backend")
    seventh = [p for p in trustmod.listing() if p["root"].endswith("seventh")][0]
    edited = dict(seventh, root=str(home / "elsewhere"))
    trustmod._save_proposal(edited)
    ok, msg = askmod.resolve(seventh["request"], "reject - no")
    check("bind: a reject settles it whatever the draft says",
          trustmod.load_proposal(seventh["id"])["state"] == "rejected", msg)

    from ocp.infra import backup
    snap = backup.snapshot(home, home.parent / (home.name + "-bk"))
    import tarfile
    with tarfile.open(snap) as tar:
        names = tar.getnames()
    check("backup: the protected entries ride along, under protected/",
          any(n.startswith("protected/trust/") for n in names), str(names[:8]))


def _doctor_rows():
    from ocp.infra import doctor
    r = doctor.Report()
    doctor._check_protected(r)
    return r.rows


def test_ocp_trust_cli_is_wired(home: Path) -> None:
    """`ocp trust add/list/remove` reach the same trust.py the daemon reads,
    the founder's own path through the argparse layer."""
    use_home(home)
    root = home / "repos" / "widget"
    root.mkdir(parents=True, exist_ok=True)

    class FakeStdin:
        def isatty(self) -> bool:
            return True

    real_env, real_stdin = os.environ.pop("OCP_AGENT", None), sys.stdin
    try:
        sys.stdin = FakeStdin()
        out = _capture(lambda: climod.main(
            ["trust", "add", str(root), "--why", "cli smoke"]))
        check("cli trust add: the founder declares it directly, no proposal",
              trustmod.is_declared(root), out)
        out = _capture(lambda: climod.main(["trust", "list"]))
        check("cli trust list: the new root is shown", str(root) in out, out)
        out = _capture(lambda: climod.main(["trust", "remove", str(root)]))
        check("cli trust remove: it is gone", not trustmod.is_declared(root), out)
    finally:
        sys.stdin = real_stdin
        if real_env is not None:
            os.environ["OCP_AGENT"] = real_env


def test_eligible_worktrees_splits_declared_from_not(home: Path) -> None:
    """Blocker 3 / should-fix 5 (auditor's #304): 1c only ever offers to
    trust a worktree inside a declared root, and the rest are reported as
    skipped rather than silently pressed too."""
    os.environ["OCP_HOME"] = str(home)
    agents.register("backend")
    agents.register("frontend")
    wt_in = home / "declared" / "backend-wt"
    wt_in.mkdir(parents=True, exist_ok=True)
    wt_out = home / "elsewhere" / "frontend-wt"
    wt_out.mkdir(parents=True, exist_ok=True)
    con = db.connect()
    try:
        db.set_terminal(con, "backend", "term_b", str(wt_in))
        db.set_terminal(con, "frontend", "term_f", str(wt_out))
    finally:
        con.close()
    trustmod.enact(str(home / "declared"), "test root")

    declared, skipped = actions.eligible_worktrees()
    check("eligible: the declared worktree is offered",
          declared == [("backend", str(wt_in))], declared)
    check("eligible: the undeclared one is only reported as skipped",
          skipped == [("frontend", str(wt_out))], skipped)


def test_trust_worktrees(home: Path) -> None:
    """1c's mechanism: one throwaway session per (agent, worktree) pair."""
    os.environ["OCP_HOME"] = str(home)
    created: list[tuple[str, str | None]] = []
    sent: list[tuple[str, str]] = []
    closed: list[str] = []
    real = (orcamod.create_terminal, orcamod.trust_prompt_showing, orcamod.confirm_trust,
            orcamod.wait_idle, orcamod.idle_now, orcamod.send, orcamod.close_terminal)
    try:
        orcamod.create_terminal = (
            lambda cmd, **k: (created.append((cmd, k.get("title"))), "term_probe")[1])
        orcamod.trust_prompt_showing = lambda h: True
        orcamod.confirm_trust = lambda h: (True, "trusted")
        orcamod.wait_idle = lambda h, *a, **k: True
        orcamod.idle_now = lambda h: False
        orcamod.send = lambda h, text, **k: (sent.append((h, text)), (True, "ok"))[1]
        orcamod.close_terminal = lambda h, **k: (closed.append(h), (True, "closed"))[1]

        result = actions.trust_worktrees("gamma", [("backend", "/w/backend")])
        check("trust: one line per worktree", len(result) == 1, str(result))
        check("trust: reports the trust", "trusted" in result[0], result[0])
        check("trust: starts the session in the worktree, under the new account",
              created and "/w/backend" in created[0][0] and "gamma" in created[0][0],
              str(created))
        check("trust: exits the throwaway session afterwards",
              sent == [("term_probe", "/exit")], sent)
        check("trust: and closes its terminal", closed == ["term_probe"], closed)

        created.clear(); sent.clear(); closed.clear()
        orcamod.trust_prompt_showing = lambda h: False
        result = actions.trust_worktrees("gamma", [("backend", "/w/backend")])
        check("trust: no prompt shown is reported, not claimed as a confirmed trust",
              not result[0].endswith(": trusted") and "no trust prompt" in result[0],
              result[0])
        check("trust: still exits and closes even then",
              sent == [("term_probe", "/exit")] and closed == ["term_probe"],
              str((sent, closed)))
    finally:
        (orcamod.create_terminal, orcamod.trust_prompt_showing, orcamod.confirm_trust,
         orcamod.wait_idle, orcamod.idle_now, orcamod.send, orcamod.close_terminal) = real


def test_trust_worktrees_quotes_the_path(home: Path) -> None:
    """Should-fix 6 (#304): a worktree path containing a quote must not
    break the command it is interpolated into."""
    os.environ["OCP_HOME"] = str(home)
    created: list[str] = []
    real = orcamod.create_terminal
    try:
        orcamod.create_terminal = lambda cmd, **k: (created.append(cmd), None)[1]
        actions.trust_worktrees("gamma", [("backend", "/tmp/a'b")])
        check("trust: the command still parses, quote and all",
              created and shlex.split(created[0].split("&&")[0])[-1] == "/tmp/a'b",
              str(created))
    finally:
        orcamod.create_terminal = real


def test_trust_prompt_folder_parses_a_live_capture() -> None:
    """auditor's #339 should-fix 3: claude's `trust_screen` is what blocker
    2's whole fix rests on, and nothing pinned its join-bare, wrapped-path
    behaviour against a real screen - only a synthetic one-liner. A parse
    that silently comes out wrong fails closed (escalate + STALLED), so a
    quiet regression here stalls every agent in a declared root (design/04
    §4.3: the parse itself is claude's driver's now, not orca's)."""
    from ocp.vendors import claude as claudemod
    drv = vendors.get("claude")
    live = LIVE_TRUST_PROMPT_CAPTURE.split("\n")
    check("trust_screen: the wrapped path is joined bare, no space",
          drv.trust_screen(live) == LIVE_TRUST_PROMPT_FOLDER, drv.trust_screen(live))
    check("trust_screen: trust_prompt_showing agrees this is the screen",
          claudemod.TRUST_PROMPT_MARKER in LIVE_TRUST_PROMPT_CAPTURE)
    check("trust_screen: no marker at all is None, not a guess",
          drv.trust_screen(["ordinary TUI, nothing to see here"]) is None)
    check("trust_screen: the marker with no workspace line is \"\" (showing, "
          "unreadable), not None (auditor's FAIL on #337)",
          drv.trust_screen([claudemod.TRUST_PROMPT_MARKER]) == "")
    check("trust_screen: the workspace line with nothing after it is \"\" too",
          drv.trust_screen([claudemod.TRUST_PROMPT_MARKER, "Accessing workspace:"]) == "")


def test_trust_prompt_dispatches_to_the_terminals_own_vendor(home: Path) -> None:
    """trust_prompt_showing/trust_prompt_folder ask the terminal's own
    vendor's driver, not always claude's (design/04 §4.3): a second vendor's
    own trust dialog is read by its own driver, and never mistaken for one on
    a claude terminal showing something else."""
    os.environ["OCP_HOME"] = str(home)
    con = db.connect()
    real_text = orcamod.screen_text
    try:
        with second_vendor() as drv:
            drv.trust_screen = lambda tail: (
                "the/folder" if any("SECOND VENDOR TRUST" in ln for ln in tail) else None)
            agents.register("zbackend")
            _with_vendor("zbackend", "zai")
            agents.register("backend")
            db.set_terminal(con, "zbackend", "term_z", "wt")
            db.set_terminal(con, "backend", "term_c", "wt")
            orcamod.screen_text = lambda h: (
                "SECOND VENDOR TRUST" if h == "term_z" else "an ordinary claude screen")
            check("trust: the second vendor's own dialog is recognized on its terminal",
                  orcamod.trust_prompt_showing("term_z")
                  and orcamod.trust_prompt_folder("term_z") == "the/folder")
            check("trust: claude's terminal is not read as the second vendor's dialog",
                  not orcamod.trust_prompt_showing("term_c")
                  and orcamod.trust_prompt_folder("term_c") is None)
    finally:
        orcamod.screen_text = real_text
        con.close()


def test_trust_prompt_showing_stays_true_when_the_folder_cannot_be_read(home: Path) -> None:
    """auditor's FAIL on #337: an unparseable trust screen (its own marker
    shown, no workspace line yet - a mid-redraw, or a layout drift) must
    still read as showing, or the wake path calls it clear and types its
    wake line and Enter into the dialog - picking the default "No, exit"
    where main escalates and stalls."""
    os.environ["OCP_HOME"] = str(home)
    real_text = orcamod.screen_text
    try:
        orcamod.screen_text = lambda h: (
            "Quick safety check: Is this a project you created or one you trust?\n"
            "...\nNo, exit\nYes, I trust this folder\n")
        check("trust: showing, even though the folder cannot be read",
              orcamod.trust_prompt_showing("term_x"))
        check("trust: and the folder itself is None, not a guess",
              orcamod.trust_prompt_folder("term_x") is None)
    finally:
        orcamod.screen_text = real_text


def test_a_declared_root_gets_standing_consent(home: Path) -> None:
    """Standing consent, and blocker 2 (auditor's #304): pressed only for the
    folder the dialog itself names, checked against both the declared roots
    and the recorded worktree, and only once the keys actually land.
    Anything else escalates, the same as with no standing consent at all."""
    _daemon_home(home, 50)
    con = db.connect()
    wt = home / "worktree"
    wt.mkdir(parents=True, exist_ok=True)
    other = home / "elsewhere"
    other.mkdir(parents=True, exist_ok=True)

    def screen_for(path) -> str:
        return (f"some prompt line\nAccessing workspace:\n{path}\n"
                f"Quick safety check: Is this a project you created or one you trust?\n"
                f"...\nNo, exit\nYes, I trust this folder\n")

    real = (orcamod.resume_with, orcamod.alive, orcamod.trust_prompt_showing,
            orcamod.confirm_trust, orcamod.wait_idle, orcamod.screen_text,
            trustmod.is_declared, orcamod.idle_now, orcamod.send, orcamod.screen_blocker,
            orcamod.time.sleep)
    sent: list[str] = []
    trusted: list[str] = []
    showing = {"v": True}

    def reset():
        showing["v"] = True
        sent.clear()
        trusted.clear()
        # A typed handoff is consumed, so each case parks with a fresh one.
        keep_handoff("backend", "# handoff · backend\n## next\n- carry on\n")
        db.set_agent_state(con, "backend", dmn_states.PARKED, detail="reset for the next case")

    try:
        orcamod.available = lambda: True
        orcamod.alive = lambda h: True
        orcamod.resume_with = lambda h, text: (sent.append(text), (True, "typed"))[1]
        orcamod.wait_idle = lambda h, *a, **k: True
        orcamod.trust_prompt_showing = lambda h: showing["v"]
        # As in production: the trust dialog is a modal to screen_blocker.
        orcamod.screen_blocker = lambda h: (orcamod.Blocker(orcamod.MODAL)
                                            if orcamod.trust_prompt_showing(h) else None)
        sessions_are("backend")
        db.set_terminal(con, "backend", "term_x", str(wt))
        keep_handoff("backend", "# handoff · backend\n## next\n- carry on\n")

        # 1. Not a declared root at all: escalates, nothing pressed.
        orcamod.screen_text = lambda h: screen_for(wt)
        trustmod.is_declared = lambda p: False
        orcamod.confirm_trust = lambda h: (trusted.append(h), (True, "trusted"))[1]
        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        check("wake: an undeclared root escalates, nothing confirmed or typed",
              not ok and not sent and not trusted, str((ok, sent, trusted)))
        check("wake: ...and the agent is stalled",
              db.get_agent_state(con, "backend")["state"] == dmn_states.STALLED)

        # 2. Declared, but the dialog names a folder the record does not.
        reset()
        orcamod.screen_text = lambda h: screen_for(other)
        trustmod.is_declared = lambda p: True
        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        check("wake: a recorded path that differs from the dialog's folder escalates",
              not ok and not sent and not trusted, str((ok, sent, trusted)))

        # 3. Declared and matching, but the keys never land.
        reset()
        orcamod.screen_text = lambda h: screen_for(wt)
        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        check("wake: keys that did not land escalate rather than typing blind",
              not ok and not sent, str((ok, sent, trusted)))

        # 4. Declared, matching, and the keys land: pressed, then typed.
        reset()
        def confirm_and_close(h):
            trusted.append(h)
            showing["v"] = False
            return True, "trusted"
        orcamod.confirm_trust = confirm_and_close
        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        check("wake: declared, matching, and the keys land: pressed",
              ok and trusted == ["term_x"], str((ok, trusted)))
        check("wake: ...and the handoff is still typed once it is",
              sent and "carry on" in sent[-1], sent)
        events = [dict(r) for r in db.events_after(con, 0, agent="backend")]
        check("wake: ...and standing consent is on the record",
              any(e["kind"] == "trust_confirmed" for e in events), events)

        # 5. The keys land but claude is still redrawing: the settle waits for
        # the screen to clear instead of reading the redraw as a refusal.
        reset()
        confirmed, reads = {"v": False}, {"n": 0}

        def slow_redraw(h):
            if not confirmed["v"]:
                return True
            reads["n"] += 1
            return reads["n"] < 3      # still drawing for the first two reads

        def confirm_landing(h):
            confirmed["v"] = True
            return True, "trusted"
        orcamod.trust_prompt_showing = slow_redraw
        orcamod.confirm_trust = confirm_landing
        orcamod.time.sleep = lambda s: None
        ok, why = dmn_lifecycle.wake_agent(con, "backend", lambda _m: None)
        check("wake: a redraw that outlasts the press is waited out, not escalated",
              ok and sent and reads["n"] >= 3
              and db.get_agent_state(con, "backend")["state"] != dmn_states.STALLED,
              str((ok, why, reads, sent)))
        orcamod.time.sleep = real[-1]
        orcamod.trust_prompt_showing = lambda h: showing["v"]

        # 6. The deliver paths meet the same screen: typeable_now refuses it,
        # so neither types nor presses - the sweep is what reports it.
        reset()
        orcamod.confirm_trust = lambda h: (trusted.append(h), (True, "trusted"))[1]
        orcamod.screen_text = lambda h: screen_for(wt)
        trustmod.is_declared = lambda p: False
        orcamod.idle_now = lambda h: True
        orcamod.send = lambda h, text, enter=True: (sent.append(text), (True, ""))[1]
        db.add_request(con, "reviewer", "backend", "please look", None,
                       "peer", "fire_and_forget", None, 0, None)
        asked = db.add_request(con, "backend", "reviewer", "did it land?", None,
                               "peer", "async", None, 0, None)
        _set_request_state(con, asked, "done", "yes")
        db.set_agent_state(con, "backend", dmn_states.RUNNING)
        dmn_delivery.deliver_answers(con, "backend", lambda _m: None)
        dmn_delivery.deliver_queued(con, "backend", lambda _m: None)
        check("trust: the deliver paths neither type nor press at the screen",
              not sent and not trusted, str((sent, trusted)))
    finally:
        (orcamod.resume_with, orcamod.alive, orcamod.trust_prompt_showing,
         orcamod.confirm_trust, orcamod.wait_idle, orcamod.screen_text,
         trustmod.is_declared, orcamod.idle_now, orcamod.send, orcamod.screen_blocker,
         orcamod.time.sleep) = real
        con.close()


def test_a_trust_stall_is_lifted_once_the_prompt_is_answered_and_the_session_runs(home: Path) -> None:
    """founder #1212: the trust prompt was accepted (standing consent from the
    blocked sweep, a person, or the vendor) and the agent works, but the row
    stayed FATAL - STALLED is left for a person, and nothing looked again. The
    sweep reads the screen: gone means lifted and the escalation closed; still
    up, unreadable, or a stall for another reason means untouched."""
    _daemon_home(home, 50)
    con = db.connect()
    trust_screen = ("Accessing workspace:\nwt\n"
                    "Quick safety check: Is this a project you created or one you trust?\n"
                    "...\nNo, exit\nYes, I trust this folder\n")
    screen = {"v": trust_screen}
    real = (orcamod.available, orcamod.live_terminals, obsmod.live_sessions, orcamod.screen_text)

    def stalled_on_the_trust_screen() -> int:
        db.set_agent_state(con, "backend", dmn_states.QUEUED, detail="reset")
        dmn_lifecycle._escalate_trust_prompt(con, "backend", "term_x", lambda _m: None, "wt", "wt")
        return db.open_escalation_ids(con, "backend", dmn_lifecycle.TRUST_ESCALATION)[0]

    def open_trust_escalations() -> list[int]:
        return [r for p in dmn_lifecycle.trust_escalation_prefixes("backend")
                for r in db.open_escalation_ids(con, "backend", p)]

    def sweep() -> list[str]:
        said: list[str] = []
        dmn_reconciler.sweep(con, lambda _m: None, said)
        return said

    try:
        db.set_terminal(con, "backend", "term_x", "wt")
        orcamod.available = lambda: True
        orcamod.live_terminals = lambda: {"term_x"}
        obsmod.live_sessions = lambda: {"backend": 4242}
        orcamod.screen_text = lambda h: screen["v"]
        rid = stalled_on_the_trust_screen()
        check("trust stall: the prompt raised a stall and one escalation",
              db.get_agent_state(con, "backend")["state"] == dmn_states.STALLED, rid)

        sweep()
        check("trust stall: the prompt still up leaves the stall",
              db.get_agent_state(con, "backend")["state"] == dmn_states.STALLED)

        screen["v"] = None
        sweep()
        check("trust stall: a screen that cannot be read leaves the stall",
              db.get_agent_state(con, "backend")["state"] == dmn_states.STALLED)

        screen["v"] = "an ordinary claude screen"
        obsmod.live_sessions = lambda: {}
        sweep()
        check("trust stall: no running session leaves the stall",
              db.get_agent_state(con, "backend")["state"] == dmn_states.STALLED)

        obsmod.live_sessions = lambda: {"backend": 4242}
        said = sweep()
        row = db.get_agent_state(con, "backend")
        check("trust stall: the prompt gone and a session running lifts it",
              row["state"] == dmn_states.IDLE, str(dict(row)))
        check("trust stall: ...says so", any("STALLED -> IDLE" in a for a in said), str(said))
        check("trust stall: ...and closes the escalation",
              db.get_request(con, rid)["state"] == "done"
              and not open_trust_escalations())

        # A pending standing-consent proposal (team/trust.py) opens with "[trust]" too and is the
        # founder's to decide: lifting a stall must leave it, and it is not a stall escalation.
        proposal = askmod.escalate(con, "backend", "[trust] add /some/root - asked by backend\n\nwhy\n\n"
                                   "Answer `approve` or `reject` (proposal 1).", "trust proposal")
        rid = stalled_on_the_trust_screen()
        sweep()
        check("trust stall: a pending trust proposal survives the lift, the stall's own escalation closes",
              db.get_request(con, proposal)["state"] == "queued" and db.get_request(con, rid)["state"] == "done",
              str((db.get_request(con, proposal)["state"], db.get_request(con, rid)["state"])))
        askmod.resolve(proposal, "test cleanup", state="done", notify=False)

        # An escalation raised before the marker began "[trust] <agent>'s terminal is showing ...".
        db.set_agent_state(con, "backend", dmn_states.STALLED, detail="folder-trust prompt")
        legacy = askmod.escalate(con, "backend", "[trust] backend's terminal is showing claude's folder-trust "
                                 "prompt for wt", "old")
        sweep()
        check("trust stall: an escalation raised under the old marker closes with the lift",
              db.get_request(con, legacy)["state"] == "done")

        # A closed agent whose session is running, stalled by the trust prompt with the wake
        # streak at its cap, is lifted once and said once: the wake-stall branch below the lift
        # must not judge the row from before it and overwrite the lift's detail.
        stalled_on_the_trust_screen()
        db.set_closed(con, "backend", "sess-1")
        for _ in range(dmn_states.MAX_FAILED_ATTEMPTS):
            db.bump_counter(con, "backend", "wake_failures")
        said = sweep()
        row = db.get_agent_state(con, "backend")
        check("trust stall: a closed agent at the wake cap is lifted with the trust detail kept",
              row["state"] == dmn_states.IDLE and "folder-trust prompt" in row["detail"], str(dict(row)))
        check("trust stall: ...and the running session clears the wake streak it refutes",
              row["wake_failures"] == 0, str(dict(row)))
        check("trust stall: ...and the lift is said once, not again as a wake stall",
              sum("STALLED -> IDLE" in a for a in said) == 1
              and not any("wake stall" in a or "running after all" in a for a in said), str(said))

        # The same agent while the prompt is still up: its running session is the one sitting at
        # the prompt, so the wake-stall branch must not lift a stall the screen has not refuted.
        screen["v"] = trust_screen
        rid = stalled_on_the_trust_screen()
        db.set_closed(con, "backend", "sess-1")
        for _ in range(dmn_states.MAX_FAILED_ATTEMPTS):
            db.bump_counter(con, "backend", "wake_failures")
        said = sweep()
        row = db.get_agent_state(con, "backend")
        check("trust stall: a running session at the unanswered prompt keeps the stall and its detail",
              row["state"] == dmn_states.STALLED and dmn_lifecycle.TRUST_STALL in row["detail"], str(dict(row)))
        check("trust stall: ...and its escalation stays open, nothing said of a wake stall",
              db.get_request(con, rid)["state"] == "queued"
              and not any("STALLED -> IDLE" in a for a in said), str(said))
        check("trust stall: ...while the closed mark it refutes is still cleared",
              not db.get_agent_state(con, "backend")["closed_at"])

        # An escalation that will not close keeps the stall: the row moves only after it.
        real_resolve = askmod.resolve
        def refuse(*_a, **_k):
            raise RuntimeError("db locked")
        askmod.resolve = refuse
        screen["v"] = "an ordinary claude screen"
        try:
            sweep()
        finally:
            askmod.resolve = real_resolve
        check("trust stall: a failed close of the escalation leaves the stall in place",
              db.get_agent_state(con, "backend")["state"] == dmn_states.STALLED
              and db.get_request(con, rid)["state"] == "queued")
        sweep()
        check("trust stall: ...and the next tick closes both",
              db.get_agent_state(con, "backend")["state"] == dmn_states.IDLE
              and db.get_request(con, rid)["state"] == "done")

        # A wake stall - the streak at its cap, no trust prompt behind it - is lifted by a running
        # session, once.
        db.set_agent_state(con, "backend", dmn_states.STALLED, detail="3 failed attempts: x")
        db.set_closed(con, "backend", "sess-1")
        for _ in range(dmn_states.MAX_FAILED_ATTEMPTS):
            db.bump_counter(con, "backend", "wake_failures")
        said = sweep()
        row = db.get_agent_state(con, "backend")
        check("wake stall: a running session lifts it and clears the streak",
              row["state"] == dmn_states.IDLE and row["wake_failures"] == 0 and not row["closed_at"], str(dict(row)))
        check("wake stall: ...said once", sum("STALLED -> IDLE" in a for a in said) == 1, str(said))

        # A stall for another reason is not this one's to lift, however the screen reads.
        db.set_agent_state(con, "backend", dmn_states.STALLED, detail="3 failed attempts: x")
        sweep()
        check("trust stall: a stall for another reason is left for a person",
              db.get_agent_state(con, "backend")["state"] == dmn_states.STALLED)
    finally:
        orcamod.available, orcamod.live_terminals, obsmod.live_sessions, orcamod.screen_text = real
        con.close()


def test_offer_to_trust_worktrees_lists_paths_and_skips(home: Path) -> None:
    """Should-fix 5 (auditor's #304): the y/N question names exactly the
    worktrees a yes will press, and reports the rest as skipped rather than
    acting on them silently."""
    use_home(home)
    agents.register("backend")
    agents.register("frontend")
    wt_in = home / "declared" / "backend-wt"
    wt_in.mkdir(parents=True, exist_ok=True)
    wt_out = home / "elsewhere" / "frontend-wt"
    wt_out.mkdir(parents=True, exist_ok=True)
    con = db.connect()
    try:
        db.set_terminal(con, "backend", "term_b", str(wt_in))
        db.set_terminal(con, "frontend", "term_f", str(wt_out))
    finally:
        con.close()
    trustmod.enact(str(home / "declared"), "test root")

    real_trust = actions.trust_worktrees
    asked: list[str] = []
    try:
        actions.trust_worktrees = lambda acct, wts: [f"{a}: trusted" for a, w in wts]
        out = _capture(lambda: cliaccount._offer_to_trust_worktrees(
            "gamma", lambda p: (asked.append(p), "y")[1]))
    finally:
        actions.trust_worktrees = real_trust
    check("offer: the declared worktree is named in the question",
          any("backend" in p and str(wt_in) in p for p in asked), asked)
    check("offer: the undeclared one is named as skipped, not offered",
          any("skipping" in p and "frontend" in p for p in asked), asked)
    check("offer: a yes goes ahead and trusts only the declared one",
          "backend: trusted" in out, out)



def test_dialog_rules_are_the_founders_and_match_by_label(home: Path) -> None:
    """T199: a rule is {vendor, title, label}, kept in the protected store; an
    agent's session is refused without a raise; match answers by exact label."""
    from ocp.team import dialog_rules as dr
    _daemon_home(home, 20)
    check("rules: none to begin with", dr.list_rules() == [])
    ok, said = dr.add("claude", "Allow this tool?", "Yes, always")
    check("rules: the founder adds one", ok and dr.list_rules() ==
          [{"vendor": "claude", "title": "Allow this tool?", "label": "Yes, always"}], said)
    check("rules: adding it again is a no-op", dr.add("claude", "Allow this tool?", "Yes, always")[0]
          and len(dr.list_rules()) == 1)
    ok, said = dr.add("claude", "Allow this tool?", "No")
    check("rules: a second label for the same prompt is refused", not ok and len(dr.list_rules()) == 1, said)
    check("rules: an empty or control-character field is refused",
          not dr.add("", "t", "l")[0] and not dr.add("claude", "t\x1b", "l")[0])

    options = [(1, "Yes"), (2, "Yes, always"), (3, "No")]
    check("match: the label's own number, not a fixed one",
          dr.match("claude", "Allow this tool?", options) == 2
          and dr.match("claude", "Allow this tool?", [(1, "Yes, always"), (2, "No")]) == 1)
    check("match: another vendor, another title, or a label that is not offered gives None",
          dr.match("opencode", "Allow this tool?", options) is None
          and dr.match("claude", "Allow this?", options) is None
          and dr.match("claude", "Allow this tool?", [(1, "Yes"), (2, "No")]) is None
          and dr.match("claude", "Allow this tool?", [(1, "yes, always")]) is None)
    check("match: two options with the label are ambiguous, so None",
          dr.match("claude", "Allow this tool?", [(1, "Yes, always"), (2, "Yes, always")]) is None)

    saved = os.environ.get("OCP_AGENT")
    os.environ["OCP_AGENT"] = "manager"
    try:
        ok, said = dr.add("claude", "Trust this folder?", "Yes")
        check("rules: an agent's session gets (False, msg), not a raise, and adds nothing",
              not ok and len(dr.list_rules()) == 1, said)
        ok, said = dr.remove("claude", "Allow this tool?", "Yes, always")
        check("rules: nor can it remove one", not ok and len(dr.list_rules()) == 1, said)
    finally:
        if saved is None:
            os.environ.pop("OCP_AGENT", None)
        else:
            os.environ["OCP_AGENT"] = saved

    ok, said = dr.remove("claude", "Allow this tool?", "Yes, always")
    check("rules: the founder removes it", ok and dr.list_rules() == [], said)
    check("rules: removing what is not there says so", not dr.remove("claude", "x", "y")[0])


def main() -> int:
    def tests(td):
        home = Path(td) / "orcapipe"
        home.mkdir()
        run(lambda: test_trust_roots(Path(td) / 'trust_roots'))
        run(lambda: test_dialog_rules_are_the_founders_and_match_by_label(Path(td) / 'dlgrules'))
        run(lambda: test_trust_roots_live_in_the_protected_store(Path(td) / 'trustprot'))
        run(lambda: test_ocp_trust_cli_is_wired(Path(td) / 'trust_cli'))
        run(lambda: test_eligible_worktrees_splits_declared_from_not(Path(td) / 'eligible_wt'))
        run(lambda: test_trust_worktrees(Path(td) / 'trust_wt'))
        run(lambda: test_trust_worktrees_quotes_the_path(Path(td) / 'trust_wt_quote'))
        run(lambda: test_trust_prompt_folder_parses_a_live_capture())
        run(lambda: test_trust_prompt_dispatches_to_the_terminals_own_vendor(Path(td) / 'trustvendor'))
        run(lambda: test_trust_prompt_showing_stays_true_when_the_folder_cannot_be_read(
            Path(td) / 'trustunreadable'))
        run(lambda: test_a_declared_root_gets_standing_consent(Path(td) / 'trustroot'))
        run(lambda: test_a_trust_stall_is_lifted_once_the_prompt_is_answered_and_the_session_runs(
            Path(td) / 'truststall'))
        run(lambda: test_offer_to_trust_worktrees_lists_paths_and_skips(
            Path(td) / 'offertrust'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
