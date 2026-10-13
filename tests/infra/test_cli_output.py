"""Tests: no hl command prints what a terminal would obey, and every command is either driven or skipped with a reason.

Run on its own: python3 tests/infra/test_cli_output.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403


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
    from handline.session import fold as foldmod
    from handline.infra import service
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
            h = db.add_request(con, "reviewer", "human", f"decision {p}", None, "peer", "async",
                               "escalate", 0, None)
            d = db.add_request(con, "reviewer", "backend", f"asked {p}", None, "peer", "async",
                               None, 0, None)
            support._set_request_state(con, d, "landed", f"verdict {p}\nmore {p}")
            db.set_agent_state(con, "reviewer", "STALLED", detail=f"detail {p}")
            db.add_event(con, "reviewer", f"kind{p}", {"x": p})
            db.set_terminal(con, "reviewer", f"term{p}", f"wt{p}")
            # For `hr apply`: a proposal rejected with a poisoned verdict. Built
            # by hand rather than through hr.propose(), so the fixture does not
            # depend on anything else the scene registers or retires.
            hrq = db.add_request(con, "reviewer", askmod.HUMAN, "roster: retire reviewer?",
                                 None, "peer", "async", None, 0, None)
            support._set_request_state(con, hrq, "landed", f"reject {c1p}")
            # For `hr withdraw`: a still-open proposal, its decision still queued.
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
        journal.write_text(f"## Done\n- done {p}\n")
        service.out_log().parent.mkdir(parents=True, exist_ok=True)
        service.out_log().write_text(f"[12:00:00] reviewer: {p}\n")
        repo = where / "repo"                  # for `spawn --dry-run`
        repo.mkdir()

        # Every leaf of the CLI is one of these two, so a subcommand that is added
        # is noticed: it is driven here, or it is skipped with a reason.
        driven = {
            "status": [["status"], ["status", "--json"]], "doctor": [["doctor", "--no-color"]],
            "team seal": [["team", "seal"]], "ps": [["ps"]],
            "events": [["events", "--limit", "50", "--all"], ["events", "--agent", "reviewer"]],
            "inbox": [["inbox", "reviewer"], ["inbox", "reviewer", "--all"],
                      ["inbox", "human", "--all"]],
            "read": [["read", str(q)], ["read", str(h)], ["read", str(d)]],
            "account list": [["account", "list"], ["account", "list", "--json"]],
            "hr list": [["hr", "list"]], "hr links": [(["hr", "links"], {"OCP_AGENT": "human"}, "hr links")], "service logs": [["service", "logs"]],
            "resume": [["resume", "reviewer"]], "fold": [["fold", "reviewer", "--no-model"]],
            "backup": [["backup"]],
            "spawn": [(["spawn", "reviewer", "--repo", str(repo), "--prompt", f"p {p}", "--dry-run"],
                       None, "spawn --dry-run")],
            "hr apply": [(["hr", "apply", "poisontest"], None, "hr apply")],
            "hr withdraw": [(["hr", "withdraw", "withdrawtest"], {"OCP_AGENT": "reviewer"},
                             "hr withdraw")],
            "hr approve": [(["hr", "approve", "poisontest"], {"OCP_AGENT": "human"}, "hr approve")],
            "hr reject": [(["hr", "reject", "poisontest"], {"OCP_AGENT": "human"}, "hr reject")],
            "guide": [["guide"]], "task list": [["task", "list"]], "task show": [["task", "show", "T1"]],
            "top": [["top", "--once"]], "watch": [["watch", "--once"]], "bridge": [["bridge", "--once"]],
            "progress": [(["progress"], {"OCP_AGENT": "reviewer"}, "progress")],
            "withdraw": [(["withdraw", str(d + 1)], {"OCP_AGENT": "marina"}, "withdraw")],
            "unstall": [["unstall", "reviewer"]],
            "venv status": [["venv", "status"]],
        }
        out = {}

        def run_it(argv: list[str], env: dict | None = None, key: str | None = None) -> None:
            buf, err = io.StringIO(), io.StringIO()
            if env is None and argv[0] in ("inbox", "events"):
                env = {"OCP_AGENT": "human"}  # listings refuse a caller with no name
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
        foldmod.run_model = lambda *a, **k: (True, "# 2026-01-01 · reviewer\n\n## Done\n")
        # `write-handoff` reads the agent's own terminal for the handoff, which
        # there is none of here: stand in for that step and print what it stored.
        handoffmod.record = lambda con, agent: []
        try:
            run_it(["fold", "reviewer", "--account", "alpha"], key="fold reviewer (model lost a done item)")
            for leaf, runs in driven.items():
                if leaf in ("withdraw", "unstall"):
                    continue                      # they change what the others show: last
                for one in runs:
                    argv, env, key = (one + (None, None))[:3] if isinstance(one, tuple) else (one, None, None)
                    run_it(argv, env, key=key or " ".join(argv) + (" (as backend)" if env else ""))
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
        "device pair": "talks to the gateway's socket as the founder; driven with a fake gateway in tests/team/test_hl_device.py",
        "device list": "talks to the gateway's socket as the founder; driven with a fake gateway in tests/team/test_hl_device.py",
        "device revoke": "talks to the gateway's socket as the founder; driven with a fake gateway in tests/team/test_hl_device.py",
        "login": "interactive: hands the terminal to claude",
        "account add": "interactive login", "account remove": "interactive logout and a confirm",
        "account register": "creates an account directory; prints its path",
        "account cap": "writes a cap; prints numbers",
        "account cycle": "writes a per-account month-cycle setting; driven in "
                         "test_account_cycle_command (tests/usage/test_cycle.py), not this scene",
        "account recover": "writes a per-account setting; prints where it stands, or "
                           "with --now calls recover.recover() against a real login "
                           "(recover.py's own mechanism is driven in test_recover_*)",
        "idle-timeout": "writes the global or a per-agent park timeout; prints it - driven in "
                        "test_t52_idle_timeout_and_ignore_cache_expiry_from_the_command_line, not this scene",
        "idle-background": "writes the global or a per-agent background notice and grace time; prints them - "
                           "driven in test_t424_idle_background_sets_the_notice_and_the_grace_globally_and_per_agent, "
                           "not this scene",
        "notice-digest": "writes the global or a per-agent digest wait; prints it - driven in "
                         "test_ocp_notice_digest_shows_and_sets_the_wait, not this scene",
        "schedule": "list is read-only, add and remove write agent.yaml (the founder's) - driven in "
                    "test_the_schedules_setting_is_validated_and_set_from_the_command_line "
                    "(tests/daemon/test_schedules.py), not this scene",
        "ignore-cache-expiry": "writes the global or a per-agent switch; prints it - driven in "
                               "test_t52_idle_timeout_and_ignore_cache_expiry_from_the_command_line, not this scene",
        "agent move": "a confirm, and Orca", "agent register": "writes agent.yaml; a name with a control byte is refused before it prints",
        "launch": "execs claude",
        "hooks gate": "writes the protected store; driven in "
                      "test_the_enforce_switch_is_the_founders_to_flip "
                      "(tests/session/test_stopgate.py), not this scene",
        "go": "writes the protected store; driven in test_the_go_switches_are_the_founders_and_off_until_set "
              "(tests/session/test_edittask.py), not this scene",
        "install-hooks": "writes the shared settings",
        "hr add": "writes a proposal; prints its id", "hr retire": "writes a proposal; prints its id",
        "hr rename": "writes a proposal; prints its id",
        "hr link": "writes a proposal; prints its id", "hr unlink": "writes a proposal; prints its id",
        "hr team": "writes a proposal; prints its id",
        "hr diff": "writes a proposal; driven in test_a_team_diff_is_one_proposal_applied_all_or_none "
                   "(tests/team/test_hr_agents.py), not this scene",
        "team init": "writes team.yaml",
        "cancel": "founder-only; its return strings hold ids and names (read in actions.cancel, ask.cancel)",
        "hook": "reads a hook payload on stdin and prints nothing",
        "daemon": "runs forever", "service install": "launchd", "service uninstall": "launchd",
        "service status": "launchd; prints a pid",
        "service use": "writes the daemon_impl setting; driven in test_service_use_sets_the_daemon_the_next_install_runs "
                       "(tests/session/test_daemon_session.py), not this scene",
        "dev-mode": "writes a config flag; prints on/off - driven in "
                    "test_dev_mode_config_round_trips, not this scene",
        "auto-accept": "writes a config flag; driven in test_auto_accept_config_round_trips "
                       "(tests/session/test_autoaccept.py), not this scene",
        "restart": "ends a session through Orca and starts it again; driven in test_restart_ends_the_session_and_starts_it_again_in_the_same_terminal, not this scene",
        "close": "an agent's session is ended through Orca; the task half is driven by test_closing_a_task_needs_no_open_child_and_only_the_asker",
        "task new": "writes a row; prints its id and an owner already checked against the registry",
        "task start": "writes agent_state.current_task; driven in "
                      "test_task_new_is_its_own_delivery, not this scene",
        "task note": "hidden alias of `task memo T add`; driven in test_task_memo_verbs, not this scene",
        "task memo": "writes and reads task_memos rows; driven in test_task_memo_verbs "
                     "(tests/team/test_tasks_requests.py), not this scene",
        "task begin": "opens the caller's own task on a founder message; driven in "
                      "test_a_founder_message_lets_the_agent_begin_its_own_task, not this scene",
        "task handoff": "changes a task's owner and delivers a note; driven in "
                        "test_a_task_is_handed_over_with_a_note_along_the_fixed_chain, not this scene",
        "task done": "writes a row; driven in test_marking_a_task_done_needs_evidence_and_only_the_owner_or_a_linked_target, not this scene",
        "task discard": "writes a row; driven in test_task_discard, not this scene",
        "task queue": "writes a row; driven in test_a_task_is_a_draft_until_queued_and_delivered_when_its_turn_comes, not this scene",
        "task draft": "writes a row; driven in test_a_task_is_a_draft_until_queued_and_delivered_when_its_turn_comes, not this scene",
        "task merge": "writes rows; driven in test_merging_tasks_moves_titles_and_requests_and_closes_the_rest, not this scene",
        "task reopen": "writes a row; driven in test_reopening_a_done_task_needs_a_reason_and_only_the_asker, not this scene",
        "task release": "writes a row; driven in test_release_takes_a_doing_task_back_to_queued, not this scene",
        "triggers": "agent.triggers, which only the Go daemon serves; pinned by contract/fixtures/agent_triggers.json, and the "
                    "table is driven in test_triggers_draws_the_daemons_verdicts, not this scene",
        "task accept": "task.accept, which only the Go daemon serves; pinned by contract/fixtures/task_report.json, not this scene",
        "task reject": "task.reject, which only the Go daemon serves; pinned by contract/fixtures/task_report.json, not this scene",
        "task edit": "writes a row; driven in "
                     "test_editing_a_task_needs_the_opener_or_the_founder_and_is_never_silent, "
                     "not this scene",
        "task board": "own open tasks and who holds each; driven in "
                       "test_task_board_shows_who_holds_each_open_task, not this scene",
        "memory status": "prints each agent's memory against budget; driven in "
                         "test_agent_memory_budget_migration_and_team_layer, not this scene",
        "memory migrate": "a dry run, or --apply copies files; driven in "
                          "test_agent_memory_budget_migration_and_team_layer, not this scene",
        "agent set": "writes agent.yaml, or raises a proposal; driven in "
                     "test_an_agent_changes_ordinary_settings_at_once_and_the_rest_only_when_the_founder_approves, not this scene",
        "hr context": "raises a proposal for new ROLE.md/base.md text; driven in "
                      "test_an_agents_role_and_base_change_only_by_a_proposal_the_founder_approves, not this scene",
        "hr relabel": "calls Orca directly, no proposal; driven in "
                      "test_a_rename_names_the_agents_orca_worktree, not this scene",
        "hr rehome": "moves a git worktree and Claude's project directories; driven in "
                     "test_hr_rehome_cli_moves_the_folder, not this scene",
        "trust add": "founder-only on a tty; driven in test_ocp_trust_cli_is_wired",
        "trust list": "prints declared roots; driven in test_ocp_trust_cli_is_wired",
        "trust remove": "driven in test_ocp_trust_cli_is_wired",
        "trust mcp": "founder-only; approves a declared folder's .mcp.json servers; driven by trust.approve_mcp in test_only_mcp_servers_the_founder_approved_by_content_are_enabled_at_launch",
        "trust proposals": "lists proposal files; prints their ids",
        "trust apply": "founder-only; applies a proposal and prints its id",
        "land": "checks a branch with tools/land.sh; driven in test_ocp_land_checks_and_sends_in_two_steps, not this scene",
        "venv install": "creates a real venv and pip installs into it",
        "say": "opens and delivers a say (send_now, never a head), or --needs-task "
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
            "service logs", "fold reviewer (model lost a done item)"}
    check("cli: the poison is really on screen for every command this test claims to grade",
          must <= shows, str(sorted(must - shows)))
    # (JSON writes a control byte as the text \\u001b, git quotes one in a path as \\033, and a
    # Python repr (the doctor's saved-terminal warning) as \\x1b: all text, not a byte, and safe to show.)
    quoted = re.compile(r"\\+(?:u001b|033|x1b)\]0;PWNED\\+(?:u0007|a|007|x07)")
    check("cli: the payload of a title is not printed either",
          not [k for k, v in dirty.items() if "PWNED" in quoted.sub("", v)],
          str([k for k, v in dirty.items() if "PWNED" in quoted.sub("", v)]))
    check("cli: and the rest are the ones that do not show these fields at all",
          all(v == "" or "MARKED" not in v for k, v in clean.items() if k not in shows))
    check("cli: what it keeps of a body is its text - a tab stays a tab, so a patch reads as written",
          "\tsecond" in dirty["read 1"] and "\tsecond" in dirty["inbox reviewer"], repr(dirty["read 1"])[:200])
    dumped = dirty["status --json"]
    seen = subprocess.run([sys.executable, "-c",
                           f"import sys; sys.path.insert(0, {str(ROOT)!r}); import handline.infra.textsafe; "
                           "print(sorted(m for m in sys.modules if m.startswith('handline.')))"],
                          capture_output=True, text=True).stdout
    check("cli: the rule about what a terminal would obey imports no front end - only itself",
          seen.strip() == "['handline.infra', 'handline.infra.textsafe']", seen.strip())
    check("cli: a command's json still parses, and holds no DEL or C1 byte",
          isinstance(json.loads(dumped), dict) and not re.search(r"[\x7f-\x9f]", dumped), dumped[:200])
    odd = {"k": "a\x9b31m\x7f\x85b \u2028 \u00e9 한"}
    said = textsafe.json_dumps(odd)
    check("cli: json_dumps writes DEL and C1 as escapes - the same json, no such bytes",
          json.loads(said) == odd and not re.search(r"[\x7f-\x9f]", said), said)


def test_triggers_draws_the_daemons_verdicts(home: Path) -> None:
    """The verdict and its reason are the daemon's (agent.triggers, served by the Go daemon only), so the reply is
    given here; the account and the figures beside it are read by the command."""
    from handline.cli import client as cliclient
    _daemon_home(home, 20)
    agents.register("reviewer")
    con = db.connect()
    try:
        db.set_account(con, "backend", "alpha")
    finally:
        con.close()
    asked = []
    real = cliclient.call

    def call(method, params=None):
        asked.append((method, params))
        return {"agents": [{"agent": "backend", "fired": True, "reason": "5h window at 96% \x9b31m(block 95%)"},
                           {"agent": "reviewer", "fired": False, "reason": "session closed; nothing to hand off"}]}
    cliclient.call = call
    try:
        out = _capture(lambda: climod.main(["triggers"]))
        check("triggers: it asks the daemon once, for every agent", asked == [("agent.triggers", None)], str(asked))
        backend = next(ln for ln in out.splitlines() if ln.startswith("backend"))
        check("triggers: the daemon's verdict and reason beside the account and its 5h figure",
              backend.split()[:3] == ["backend", "alpha", "20"] and "YES" in backend and "5h window at 96%" in backend, backend)
        check("triggers: a reason is printed as text, never as codes", "\x9b" not in out, repr(out))
        reviewer = next(ln for ln in out.splitlines() if ln.startswith("reviewer"))
        check("triggers: an agent with no account shows -, and what the daemon says of it",
              reviewer.split()[1] == "-" and "YES" not in reviewer and reviewer.endswith("session closed; nothing to hand off"),
              reviewer)

        def down(method, params=None):
            raise dmn_client.DaemonDown("no service is installed to start it")
        cliclient.call = down
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = clihealth.cmd_triggers(None)
        check("triggers: with no daemon it says so and decides nothing itself",
              rc == 1 and "could not read the triggers" in err.getvalue(), err.getvalue())
    finally:
        cliclient.call = real


def main() -> int:
    def tests(td):
        run(lambda: test_no_command_prints_what_a_terminal_would_obey(Path(td) / 'cli'))
        run(lambda: test_triggers_draws_the_daemons_verdicts(Path(td) / 'trig'))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
