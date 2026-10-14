"""Tests: account.set, account.login, account.remove, hr.decide, schedule.add, schedule.edit, schedule.remove and settings.set
(contract/protocol.md): what each writes, who may call it, what a refusal leaves unwritten, a retry after a lost reply, and the
legacy `trust` verb and `hl account cap`, which no method serves, still working.

The terminal host (a login terminal) is replaced by a recorder: it has its own tests.

Run on its own: python3 tests/daemon/test_rpc_admin.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline.actions import details  # noqa: E402
from handline.daemon import rpc_terminal, socket_server  # noqa: E402
from handline.infra import paths, sessiontoken  # noqa: E402
from handline.session import term as termmod  # noqa: E402
from handline.team import agents, hr, proposals  # noqa: E402
from handline.usage import cycle, load as loadmod  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _code, _hp  # noqa: E402


def _call(method: str, params: dict, as_agent: str | None = None) -> dict:
    """One call on a fresh connection: from the founder, or from an agent session when `as_agent` is named."""
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: as_agent
    try:
        w = Wire()
        w.call("hello", _hp() if as_agent is None else {"client": "t", "protocol": 1})
        r = w.call(method, params)
        w.close()
        return r
    finally:
        sessiontoken.agent_of = real


def _world(home: Path) -> None:
    support._board_world(home)
    for a in ("acct.one", "acct.two"):
        paths.account_dir(a).mkdir(parents=True, exist_ok=True)


class Host:
    """The terminal host replaced: records what it was asked and answers what it is told."""
    def __enter__(self):
        self.calls, self.handle = [], "t-login"
        self.saved = [(termmod, n, getattr(termmod, n)) for n in ("available", "create_terminal")]
        termmod.available = lambda: True
        termmod.create_terminal = lambda command, cwd=None, title=None: (self.calls.append(("create", command, title)), self.handle)[1]
        return self

    def __exit__(self, *exc):
        for m, n, v in self.saved:
            setattr(m, n, v)


def test_account_remove_plans_without_changing_and_removes_only_when_confirmed(home: Path) -> None:
    from handline.infra import backup
    _world(home)
    r = _call("account.remove", {"account": "acct.two", "dry_run": True, "request_id": "r1"})
    check("account.remove: a dry run says what it would do and changes nothing", r["result"]["accepted"] and r["result"]["plan"][0].startswith("would")
          and paths.account_dir("acct.two").exists(), str(r))
    r = _call("account.remove", {"account": "acct.two", "request_id": "r2"})
    check("account.remove: without the typed name it is bad_request", _code(r) == "bad_request" and r["error"]["data"]["field"] == "confirm", str(r))
    r = _call("account.remove", {"account": "acct.two", "confirm": "acct.two", "request_id": "r3"})
    check("account.remove: with no recent backup it is refused and the account stays", _code(r) == "refused" and paths.account_dir("acct.two").exists(), str(r))
    real = backup.require_recent
    backup.require_recent = lambda *a, **k: None
    try:
        r = _call("account.remove", {"account": "acct.two", "confirm": "acct.two", "request_id": "r4"})
    finally:
        backup.require_recent = real
    check("account.remove: confirmed, it is removed and the directory is kept aside", r["result"]["accepted"] and "acct.two" not in paths.list_accounts(), str(r))
    from handline.daemon import rpc_accounts
    from handline.team import accounts as accountsmod
    real_out, real_backup = accountsmod.logout_argv, backup.require_recent
    accountsmod.logout_argv, backup.require_recent = (lambda name: (["false"], {})), (lambda *a, **k: None)
    try:
        r = _call("account.remove", {"account": "acct.one", "confirm": "acct.one", "request_id": "r6"})
    finally:
        accountsmod.logout_argv, backup.require_recent = real_out, real_backup
    check("account.remove: a logout that fails leaves the account where it is and says so", _code(r) == "refused" and paths.account_dir("acct.one").exists()
          and "acct.one" in paths.list_accounts(), str(r))
    check("account.remove: claude's logout, run for real, answers its exit code", rpc_accounts._run_claude(["true"], {}) == 0 and rpc_accounts._run_claude(["false"], {}) == 1)
    check("account.remove: a logout that cannot run (no such binary) answers None", rpc_accounts._run_claude(["no-such-binary-xyz"], {}) is None)
    r = _call("account.remove", {"account": "acct.nope", "dry_run": True, "request_id": "r5"})
    check("account.remove: an unknown account is not_found", _code(r) == "not_found", str(r))


def test_account_set_writes_what_it_is_given_and_nothing_if_any_is_refused(home: Path) -> None:
    _world(home)
    r = _call("account.set", {"account": "acct.one", "label": "Main", "cap": 2, "recover": True, "request_id": "s1"})
    check("account.set: alias, cap and recovery are written", r["result"]["accepted"] and loadmod.label_of("acct.one") == "Main"
          and loadmod.own_cap("acct.one") == 2 and loadmod.usage_recover_enabled("acct.one") is True, str(r))
    r = _call("account.set", {"account": "acct.one", "cycle_end": "2026-12-31", "request_id": "s2"})
    check("account.set: a cycle_end alone turns the cycle on, as hl account cycle --end does",
          r["result"]["accepted"] and cycle.enabled("acct.one") and cycle.own_end("acct.one") == "2026-12-31", str(r))
    r = _call("account.set", {"account": "acct.one", "cycle": False, "request_id": "s3"})
    check("account.set: cycle false turns it off and keeps the end day", r["result"]["accepted"] and not cycle.enabled("acct.one")
          and cycle.own_end("acct.one") == "2026-12-31", str(r))
    r = _call("account.set", {"account": "acct.one", "cycle_end": None, "request_id": "s4"})
    check("account.set: cycle_end null clears the end day", r["result"]["accepted"] and not cycle.own_end("acct.one"), str(r))
    r = _call("account.set", {"account": "acct.one", "label": "Other", "cap": 5, "request_id": "s5"})
    check("account.set: the fields not given are left alone", r["result"]["accepted"] and loadmod.usage_recover_enabled("acct.one") is True, str(r))
    for field, bad in (("label", "two\nlines"), ("label", "x" * 41), ("cap", 0), ("recover", "yes"), ("cycle", 1), ("cycle_end", "2026-13-45")):
        r = _call("account.set", {"account": "acct.one", "label": "Never", "cap": 9, field: bad, "request_id": f"s-{field}-{bad}"})
        check(f"account.set: {field} {bad!r} is bad_request naming it, and nothing else is written",
              _code(r) == "bad_request" and r["error"]["data"]["field"] == field and loadmod.label_of("acct.one") == "Other"
              and loadmod.own_cap("acct.one") == 5, str(r))


def test_account_login_opens_a_terminal_that_can_be_watched_and_refuses_what_it_cannot_start(home: Path) -> None:
    _world(home)
    os.environ["OCP_TEST_SECRET"] = "s3cret-value"
    with Host() as h:
        r = _call("account.login", {"account": "acct.one", "request_id": "l1"})
        check("account.login: a terminal is opened running the account's login and its handle is answered",
              r["result"]["terminal"] == "t-login" and h.calls and h.calls[0][2] == "login-acct.one" and "CLAUDE_CONFIG_DIR" in h.calls[0][1], str(r))
        check("account.login: the command line carries the account's config dir and none of the daemon's other environment",
              "s3cret-value" not in h.calls[0][1] and "OCP_TEST_SECRET" not in h.calls[0][1], h.calls[0][1][:200])
        check("account.login: the handle can be attached to, though no agent has it", "t-login" in rpc_terminal.LOGIN_TERMINALS)
        h.handle = None
        r = _call("account.login", {"account": "acct.one", "request_id": "l2"})
        check("account.login: a host that starts no terminal is refused, gate state", _code(r) == "refused" and r["error"]["data"]["gate"] == "state", str(r))
        r = _call("account.login", {"account": "acct.nope", "request_id": "l3"})
        check("account.login: an unknown account is not_found", _code(r) == "not_found", str(r))


def test_schedules_are_added_edited_moved_and_removed_by_id(home: Path) -> None:
    _world(home)
    r = _call("schedule.add", {"target": "worker", "cron": "0 9 * * *", "message": "stand up", "request_id": "a1"})
    sid = r["result"]["schedule"]
    check("schedule.add: the schedule is in agent.yaml and its id is the one the method names",
          agents.read("worker")["schedules"].count("stand up") == 1 and isinstance(sid, int), str(r))
    r = _call("schedule.add", {"target": "worker", "cron": "0 9 * * *", "message": "stand up", "request_id": "a2"})
    check("schedule.add: the same schedule twice is refused, not added", _code(r) == "refused" and agents.read("worker")["schedules"].count("stand up") == 1, str(r))
    r = _call("schedule.add", {"target": "manager", "cron": "0 9 * * *", "message": "stand up", "request_id": "a3"})
    check("schedule.add: the same schedule on another agent has an id of its own", r["result"]["schedule"] != sid, str(r))
    for params, field in (({"target": "worker", "cron": "not a cron", "message": "m"}, "cron"),
                          ({"target": "worker", "cron": "0 9 * * *", "message": " "}, "message"),
                          ({"target": "worker", "cron": 5, "message": "m"}, "cron")):
        r = _call("schedule.add", {**params, "request_id": f"a-{field}"})
        check(f"schedule.add: a bad {field} is bad_request naming it", _code(r) == "bad_request" and r["error"]["data"]["field"] == field, str(r))
    r = _call("schedule.add", {"target": "nobody", "cron": "0 9 * * *", "message": "m", "request_id": "a4"})
    check("schedule.add: an agent the team does not have is not_found", _code(r) == "not_found", str(r))
    r = _call("schedule.edit", {"schedule": sid, "cron": "0 10 * * *", "request_id": "e1"})
    new = r["result"]["schedule"]
    check("schedule.edit: the cron changes, the message stays, and the new id is answered",
          "0 10 * * *" in agents.read("worker")["schedules"] and "stand up" in agents.read("worker")["schedules"] and new != sid, str(r))
    r = _call("schedule.edit", {"schedule": sid, "cron": "0 11 * * *", "request_id": "e2"})
    check("schedule.edit: the old id is gone", _code(r) == "not_found", str(r))
    r = _call("schedule.edit", {"schedule": new, "target": "manager", "request_id": "e3"})
    check("schedule.edit: a new target moves it to that agent", "0 10 * * *" not in (agents.read("worker").get("schedules") or "")
          and "0 10 * * *" in agents.read("manager")["schedules"], str(r))
    moved = r["result"]["schedule"]
    r = _call("schedule.remove", {"schedule": moved, "request_id": "r1"})
    check("schedule.remove: it is gone", r["result"]["accepted"] and "0 10 * * *" not in agents.read("manager")["schedules"], str(r))
    r = _call("schedule.remove", {"schedule": moved, "request_id": "r2"})
    check("schedule.remove: removing it again is not_found", _code(r) == "not_found", str(r))
    r = _call("schedule.remove", {"schedule": "1", "request_id": "r3"})
    check("schedule.remove: an id that is not a number is bad_request", _code(r) == "bad_request", str(r))


def test_settings_set_writes_every_key_in_one_step_or_none(home: Path) -> None:
    _world(home)
    r = _call("settings.set", {"changes": {"active_cap": 4, "dev_mode": True, "input_hold_idle_s": 900}, "request_id": "t1"})
    cfg = loadmod._config()
    check("settings.set: each key is written to config.yaml", r["result"]["accepted"] and cfg["active_cap"] == 4 and cfg["dev_mode"] is True
          and cfg["input_hold_idle_s"] == 900, str((r, cfg)))
    r = _call("settings.set", {"changes": {"active_cap": None}, "request_id": "t2"})
    check("settings.set: null removes the key, the others stay", "active_cap" not in loadmod._config() and loadmod._config()["dev_mode"] is True, str(r))
    for changes, field in (({"nope": 1}, "changes"), ({"active_cap": 0}, "active_cap"), ({"dev_mode": "yes"}, "dev_mode"),
                           ({"park_timeout_s": -1}, "park_timeout_s"), ({"token_prices": {"opus": 12.5}}, "token_prices")):
        r = _call("settings.set", {"changes": {"bg_check_s": 30, **changes}, "request_id": f"t-{changes}"})
        check(f"settings.set: {changes} is bad_request and bg_check_s is not written either",
              _code(r) == "bad_request" and r["error"]["data"]["field"] == field and "bg_check_s" not in loadmod._config(), str(r))
    r = _call("settings.set", {"changes": {}, "request_id": "t0"})
    check("settings.set: no changes is bad_request", _code(r) == "bad_request", str(r))
    r = _call("settings.set", {"agent": "worker", "changes": {"park_timeout_s": 0, "bg_grace_s": 60, "ignore_cache_expiry": False}, "request_id": "t3"})
    mine = agents.read("worker")
    check("settings.set: with agent, the per-agent keys go to that agent's agent.yaml (0 is never; false is an override)",
          r["result"]["accepted"] and mine["park_timeout_s"] == 0 and mine["bg_grace_s"] == 60 and mine["ignore_cache_expiry"] is False
          and "bg_grace_s" not in loadmod._config(), str(r))
    r = _call("settings.set", {"agent": "worker", "changes": {"bg_grace_s": None}, "request_id": "t4"})
    check("settings.set: with agent, null drops the override", "bg_grace_s" not in agents.read("worker"), str(r))
    r = _call("settings.set", {"agent": "worker", "changes": {"active_cap": 2}, "request_id": "t5"})
    check("settings.set: a key with no per-agent value, with agent, is bad_request", _code(r) == "bad_request", str(r))
    r = _call("settings.set", {"agent": "nobody", "changes": {"bg_grace_s": 2}, "request_id": "t6"})
    check("settings.set: an agent the team does not have is not_found", _code(r) == "not_found", str(r))


def _proposal() -> dict:
    ok, said = hr.propose("settings", "worker", requester="manager", why="w", sets={"effort": "high"})
    assert ok, said
    return next(p for p in proposals.listing() if p.get("state") == "proposed")


def test_hr_decide_rejects_and_approves_the_proposal_it_names(home: Path) -> None:
    _world(home)
    p = _proposal()
    n = int(p["id"], 16)
    r = _call("hr.decide", {"proposal": n, "decision": "reject", "note": "not now", "request_id": "h1"})
    check("hr.decide: a reject is recorded with its note", r["result"]["accepted"] and hr.load_proposal(p["id"])["state"] == "rejected"
          and hr.load_proposal(p["id"])["verdict"] == "not now", str(r))
    r = _call("hr.decide", {"proposal": n, "decision": "approve", "subject": "x", "request_id": "h2"})
    check("hr.decide: a proposal already decided is refused", _code(r) == "refused", str(r))
    p = _proposal()
    r = _call("hr.decide", {"proposal": int(p["id"], 16), "decision": "approve", "request_id": "h3a"})
    check("hr.decide: an approve without the subject is bad_request and applies nothing",
          _code(r) == "bad_request" and hr.load_proposal(p["id"])["state"] == "proposed", str(r))
    shown = hr.subject_of(hr.load_proposal(p["id"]))
    edited = {**hr.load_proposal(p["id"]), "set.effort": "max"}
    hr.save(edited)
    r = _call("hr.decide", {"proposal": int(p["id"], 16), "decision": "approve", "subject": shown, "request_id": "h3b"})
    check("hr.decide: an approve of the subject drawn before the proposal was edited is refused and applies nothing",
          _code(r) == "refused" and hr.load_proposal(p["id"])["state"] == "proposed" and agents.read("worker").get("effort") != "max", str(r))
    r = _call("hr.decide", {"proposal": int(p["id"], 16), "decision": "approve", "subject": hr.subject_of(edited), "request_id": "h3"})
    check("hr.decide: an approve is applied", r["result"]["accepted"] and hr.load_proposal(p["id"])["state"] == "applied"
          and agents.read("worker").get("effort") == "max", str((r, hr.load_proposal(p["id"]))))
    for params, code in (({"proposal": 1, "decision": "approve"}, "not_found"), ({"proposal": n, "decision": "maybe"}, "bad_request"),
                         ({"proposal": "1", "decision": "approve"}, "bad_request"), ({"proposal": n, "decision": "reject", "note": 5}, "bad_request")):
        r = _call("hr.decide", {**params, "request_id": f"h-{params}"})
        check(f"hr.decide: {params} is {code}", _code(r) == code, str(r))


def test_every_method_is_refused_to_an_agent_session_and_writes_nothing(home: Path) -> None:
    _world(home)
    calls = {"account.set": {"account": "acct.one", "cap": 7},
             "account.login": {"account": "acct.one"}, "hr.decide": {"proposal": 1, "decision": "reject"},
             "schedule.add": {"target": "worker", "cron": "0 9 * * *", "message": "m"}, "schedule.edit": {"schedule": 1, "cron": "0 9 * * *"},
             "schedule.remove": {"schedule": 1}, "settings.set": {"changes": {"active_cap": 7}}}
    with Host() as h:
        for method, params in calls.items():
            r = _call(method, {**params, "request_id": f"deny-{method}"}, as_agent="manager")
            check(f"{method}: refused to an agent session, gate role", _code(r) == "refused" and r["error"]["data"]["gate"] == "role", str(r))
        check("an agent session changed nothing", loadmod.own_cap("acct.one") is None and "active_cap" not in loadmod._config()
              and not agents.read("worker").get("schedules") and not h.calls)


def test_a_retry_after_a_lost_reply_answers_the_first_run_and_repeats_none(home: Path) -> None:
    _world(home)
    with Host() as h:
        runs = {"schedule.add": {"target": "worker", "cron": "0 9 * * *", "message": "once"},
                "account.login": {"account": "acct.one"}, "settings.set": {"changes": {"input_hold_idle_s": 700}}}
        for method, params in runs.items():
            first = _call(method, {**params, "request_id": f"lost-{method}"})
            con = db.connect()
            con.execute("DELETE FROM rpc_replies WHERE request_id = ?", (f"lost-{method}",))     # the crash: the effect kept, the reply not
            con.close()
            retry = _call(method, {**params, "request_id": f"lost-{method}"})
            same = {k: v for k, v in retry["result"].items() if k != "replayed"} == first["result"]
            check(f"{method}: a retry after a lost reply answers the first run's result, marked replayed", retry["result"].get("replayed") is True and same, str((first, retry)))
        check("schedule.add: and the schedule is there once", agents.read("worker")["schedules"].count("once") == 1)
        check("account.login: and one terminal was opened", [c[0] for c in h.calls].count("create") == 1, str(h.calls))
        p = _proposal()
        first = _call("hr.decide", {"proposal": int(p["id"], 16), "decision": "reject", "request_id": "lost-hr"})
        con = db.connect()
        con.execute("DELETE FROM rpc_replies WHERE request_id = 'lost-hr'")
        con.close()
        retry = _call("hr.decide", {"proposal": int(p["id"], 16), "decision": "reject", "request_id": "lost-hr"})
        check("hr.decide: a retry after a lost reply answers the first run's result instead of finding it decided", retry["result"].get("replayed") is True, str((first, retry)))


def test_the_legacy_trust_verb_and_hl_account_cap_still_work(home: Path) -> None:
    _world(home)
    real = details.trust_worktrees
    details.trust_worktrees = lambda acct, wts: (True, f"{acct}: {len(wts)} trusted")
    try:
        reply = socket_server._handle_request({"verb": "trust", "account": "acct.one", "worktrees": [["worker", "/wt/worker"]]}, lambda m: None)
        check("the trust verb: still answers {ok, said} to a legacy caller", reply["ok"] is True and "1 trusted" in reply["said"], str(reply))
    finally:
        details.trust_worktrees = real
    rc, out = support._cli(["account", "cap", "acct.one", "2"], as_agent=None)
    check("hl account cap: still writes the cap itself", rc == 0 and loadmod.own_cap("acct.one") == 2, out)


def test_the_admin_fixtures_pass_against_this_daemon(home: Path) -> None:
    import socket
    import threading
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "contract"))
    import conformance

    def connector():
        def connect():
            a, b = socket.socketpair()
            threading.Thread(target=socket_server._serve_one, args=(b, lambda m: None), daemon=True).start()
            return a
        return connect
    _world(home)
    doc = json.loads(Path("contract/fixtures/admin_writes.json").read_text())
    doc["fixtures"] = [f for f in doc["fixtures"] if not f["about"].startswith("NOT_YET_IN_PYTHON")]
    fixtures = home / "admin_writes.json"
    fixtures.write_text(json.dumps(doc))
    real = sessiontoken.agent_of
    results = []
    try:
        for role in ("founder", "agent"):
            sessiontoken.agent_of = (lambda pid: "manager") if role == "agent" else (lambda pid: None)
            results += [(*r, role) for r in conformance.run_all([fixtures], {role: connector()},
                                                                 {"founder_key": _hp()["key"], "account": "acct.one", "schedule_agent": "worker"})
                        if r[0] != "skip"]
    finally:
        sessiontoken.agent_of = real
    bad = [(n, why) for st, why, n, _ in results if st != "pass"]
    check("fixtures: every admin fixture passes against this daemon, each as its own role", not bad and results, str(bad))


def main() -> int:
    def tests(td):
        run(lambda: test_account_remove_plans_without_changing_and_removes_only_when_confirmed(Path(td) / "d12"))
        run(lambda: test_account_set_writes_what_it_is_given_and_nothing_if_any_is_refused(Path(td) / "d1"))
        run(lambda: test_account_login_opens_a_terminal_that_can_be_watched_and_refuses_what_it_cannot_start(Path(td) / "d2"))
        run(lambda: test_schedules_are_added_edited_moved_and_removed_by_id(Path(td) / "d3"))
        run(lambda: test_settings_set_writes_every_key_in_one_step_or_none(Path(td) / "d4"))
        run(lambda: test_hr_decide_rejects_and_approves_the_proposal_it_names(Path(td) / "d6"))
        run(lambda: test_every_method_is_refused_to_an_agent_session_and_writes_nothing(Path(td) / "d8"))
        run(lambda: test_a_retry_after_a_lost_reply_answers_the_first_run_and_repeats_none(Path(td) / "d9"))
        run(lambda: test_the_legacy_trust_verb_and_hl_account_cap_still_work(Path(td) / "d10"))
        run(lambda: test_the_admin_fixtures_pass_against_this_daemon(Path(td) / "d11"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
