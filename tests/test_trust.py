"""Tests: trusting worktrees and the trust prompt.

Run on its own: python3 tests/test_trust.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403




def test_trust_roots(home: Path) -> None:
    """Blocker 3 (auditor's #304): roots are an explicit list the founder
    sets, never inferred from the roster or Orca's own layout - propose/apply
    for an agent, enact directly for the founder, is_declared checks a path
    against exactly that list."""
    use_home(home)
    (home / "team.yaml").write_text(
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

    askmod.resolve(rid, "approve")
    ok, msg = trustmod.apply(pid)
    check("trust: applying an approved proposal declares the root", ok, msg)
    check("trust: it is now declared",
          trustmod.is_declared(home / "repos" / "second"))
    ok, msg = trustmod.apply(pid)
    check("trust: applying twice is refused", not ok, msg)

    ok, msg = trustmod.propose(str(home / "repos" / "third"), "for later",
                               requester="backend")
    pid3 = trustmod.listing()[-1]["id"]
    rid3 = trustmod.listing()[-1]["request"]
    askmod.resolve(rid3, "reject - not yet")
    ok, msg = trustmod.apply(pid3)
    check("trust: a rejected proposal is never applied", not ok, msg)
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
    askmod.resolve(rid4, "approve")
    tampered = trustmod.load_proposal(pid4)
    tampered["root"] = str(home / "repos" / "not-what-was-approved")
    trustmod._save_proposal(tampered)
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
    askmod.resolve(proposal_for(home / "repos" / "fifth" / "deep")["request"], "approve")
    widened = trustmod.load_proposal(pid5)
    widened["root"] = str(home / "repos" / "fifth")
    trustmod._save_proposal(widened)
    ok, msg = trustmod.apply(pid5)
    check("trust: apply refuses a proposal edited to a parent of what was approved",
          not ok and "does not match" in msg, msg)
    check("trust: and neither the parent nor the child is declared from it",
          not trustmod.is_declared(home / "repos" / "fifth")
          and not trustmod.is_declared(home / "repos" / "fifth" / "deep"))
    widened["root"] = str(home / "repos" / "fifth" / "deep")
    trustmod._save_proposal(widened)
    ok, msg = trustmod.apply(pid5)
    check("trust: the path as approved still applies", ok, msg)


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
    check("trust_screen: the marker with nothing after it is None too",
          drv.trust_screen([claudemod.TRUST_PROMPT_MARKER, "Accessing workspace:"]) is None)


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



def main() -> int:
    def tests(td):
        home = Path(td) / "orcapipe"
        home.mkdir()
        run(lambda: test_trust_roots(Path(td) / 'trust_roots'))
        run(lambda: test_ocp_trust_cli_is_wired(Path(td) / 'trust_cli'))
        run(lambda: test_eligible_worktrees_splits_declared_from_not(Path(td) / 'eligible_wt'))
        run(lambda: test_trust_worktrees(Path(td) / 'trust_wt'))
        run(lambda: test_trust_worktrees_quotes_the_path(Path(td) / 'trust_wt_quote'))
        run(lambda: test_trust_prompt_folder_parses_a_live_capture())
        run(lambda: test_trust_prompt_dispatches_to_the_terminals_own_vendor(Path(td) / 'trustvendor'))
        run(lambda: test_a_declared_root_gets_standing_consent(Path(td) / 'trustroot'))
        run(lambda: test_offer_to_trust_worktrees_lists_paths_and_skips(
            Path(td) / 'offertrust'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
