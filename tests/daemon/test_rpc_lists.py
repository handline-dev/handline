"""Tests: accounts.list, team.get, schedules.list, stats.get and settings.get (contract/protocol.md): each answers what its
spec says, in the spec's own schema, who may call it, what a bad parameter says, and that the commands they serve still work from an
agent session.

Run on its own: python3 tests/daemon/test_rpc_lists.py   (all areas: python3 tests/test_ocp.py)
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import support  # noqa: E402
from support import *  # noqa: E402,F401,F403
from handline import actions  # noqa: E402
from handline.daemon import rpc_schedules, rpc_team  # noqa: E402
from handline.infra import sessiontoken  # noqa: E402
from handline.team import agents, hr  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_rpc import Wire, _code, _hp  # noqa: E402

SPEC = json.loads((Path(__file__).resolve().parent.parent.parent / "contract" / "protocol.json").read_text())
SCHEMAS = SPEC["components"]["schemas"]
RESULT = {m["name"]: m["result"]["schema"] for m in SPEC["methods"]}


def _invalid(value, schema, path="result") -> str | None:
    """Why `value` is not what `schema` says, or None: the part of JSON Schema the spec uses."""
    if "$ref" in schema:
        return _invalid(value, SCHEMAS[schema["$ref"].rsplit("/", 1)[1]], path)
    if "oneOf" in schema:
        errs = [_invalid(value, s, path) for s in schema["oneOf"]]
        return None if sum(e is None for e in errs) == 1 else f"{path}: not exactly one of {errs}"
    if "enum" in schema and value not in schema["enum"]:
        return f"{path}: {value!r} is not in {schema['enum']}"
    t = schema.get("type")
    kinds = {"string": str, "boolean": bool, "object": dict, "array": list, "null": type(None)}
    if t == "integer":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif t == "number":
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
    elif t:
        ok = isinstance(value, kinds[t])
    else:
        ok = True
    if not ok:
        return f"{path}: {value!r} is not {t}"
    if t in ("integer", "number") and "minimum" in schema and value < schema["minimum"]:
        return f"{path}: {value} is below {schema['minimum']}"
    if t == "array":
        return next((e for i, v in enumerate(value) if (e := _invalid(v, schema["items"], f"{path}[{i}]"))), None) if "items" in schema else None
    if t == "object":
        props = schema.get("properties", {})
        for k in schema.get("required", []):
            if k not in value:
                return f"{path}: {k} is missing"
        for k, v in value.items():
            sub = props.get(k) or (schema["additionalProperties"] if isinstance(schema.get("additionalProperties"), dict) else None)
            if sub is None and (k not in props and schema.get("additionalProperties") is False):
                return f"{path}: {k} is not a property"
            if sub is not None and (e := _invalid(v, sub, f"{path}.{k}")):
                return e
    return None


def _call(method: str, params: dict | None = None, as_agent: str | None = None) -> dict:
    real = sessiontoken.agent_of
    sessiontoken.agent_of = lambda pid: as_agent
    try:
        w = Wire()
        w.call("hello", _hp() if as_agent is None else {"client": "t", "protocol": 1})
        r = w.call(method, params or {})
        w.close()
        return r
    finally:
        sessiontoken.agent_of = real


def _world(home: Path) -> None:
    support._board_world(home)
    termmod.live_terminals = lambda: set()      # no Orca here: the host answers, with nothing
    support.write_status(home, "alpha", pct_5h=40, pct_7d=10, ctx_pct=5, reset_5h=int(time.time()) + 1800)
    con = db.connect()
    try:
        db.set_account(con, "worker", "alpha")
        db.add_event(con, "worker", "moved_account", {"from": "beta", "to": "alpha", "trigger": "pinned"})
    finally:
        con.close()


def test_every_list_answers_in_the_schema_the_spec_gives_it(home: Path) -> None:
    _world(home)
    hr.propose("unlink", "manager", "manager", "no longer", to="worker")
    actions.schedule_add("worker", "0 9 * * *", "check the board")
    for method, params in (("accounts.list", {}), ("team.get", {}), ("schedules.list", {}),
                           ("stats.get", {"window": "24h"}), ("stats.get", {"window": "7d"}), ("settings.get", {}),
                           ("settings.get", {"agent": "worker"})):
        r = _call(method, params)
        why = _invalid(r["result"], RESULT[method]) if "result" in r else str(r)
        check(f"{method} {params}: the result is the spec's schema", why is None, str(why))


def test_roles_decide_who_reads_what(home: Path) -> None:
    _world(home)
    for method in ("accounts.list", "team.get", "schedules.list"):
        check(f"{method}: an agent session may read it", "result" in _call(method, as_agent="worker"))
    for method, params in (("stats.get", {"window": "24h"}), ("settings.get", {})):
        r = _call(method, params, as_agent="worker")
        check(f"{method}: an agent session is refused, gate role", _code(r) == "refused" and r["error"]["data"]["gate"] == "role", str(r))


def test_accounts_say_what_the_account_page_shows(home: Path) -> None:
    _world(home)
    got = _call("accounts.list")["result"]["accounts"]
    acct = next(a for a in got if a["name"] == "alpha")
    check("accounts.list: the five-hour use, reset and where it comes from", acct["five_hour"]["used_pct"] == 40.0
          and acct["five_hour"]["source"] == "measured" and acct["five_hour"]["resets_at"], str(acct["five_hour"]))
    check("accounts.list: the weekly use", acct["weekly"]["used_pct"] == 10.0, str(acct["weekly"]))
    check("accounts.list: the agents on it, and the move that brought one", acct["agents"] == ["worker"]
          and acct["moves"] and acct["moves"][0]["agent"] == "worker" and acct["moves"][0]["from"] == "beta" and acct["moves"][0]["reason"] == "pinned", str(acct))
    check("accounts.list: how many agents are on it, how many run, and how many are unplaced", acct["agents_assigned"] == 1
          and acct["agents_running"] == 0 and acct["agents_unplaced"] == 0, str(acct))
    check("accounts.list: no cap of its own follows the default one", acct["cap"] is None and acct["cap_effective"] >= 1, str(acct))
    r = _call("account.set", {"account": "alpha", "cap": 3, "label": "main", "recover": True, "request_id": "s1"})
    acct = next(a for a in _call("accounts.list")["result"]["accounts"] if a["name"] == "alpha")
    check("accounts.list: what account.set wrote is what it reads", acct["cap"] == 3 and acct["cap_effective"] == 3 and acct["label"] == "main"
          and acct["recover"] is True, str(acct))


def test_team_names_each_agent_its_parent_and_files_and_the_edges(home: Path) -> None:
    _world(home)
    got = _call("team.get")["result"]
    by = {a["name"]: a for a in got["agents"]}
    check("team.get: every registered agent, with its supervisor", set(by) >= {"manager", "worker"} and by["worker"]["parent"] == "manager"
          and by["manager"]["parent"] is None, str(by))
    check("team.get: the role and base files with when they were written", by["worker"]["role_file"]["path"].endswith("worker/ROLE.md")
          and by["worker"]["role_file"]["updated"] and by["worker"]["base_file"]["path"].endswith("worker/base.md"), str(by["worker"]))
    check("team.get: the allowed edges", {"from": "manager", "to": "worker"} in got["edges"], str(got["edges"]))


def test_schedules_carry_the_id_the_write_methods_use_and_when_they_run(home: Path) -> None:
    _world(home)
    r = _call("schedule.add", {"target": "worker", "cron": "0 9 * * *", "message": "check the board", "request_id": "c1"})
    sid = r["result"]["schedule"]
    got = _call("schedules.list")["result"]["schedules"]
    check("schedules.list: the one added, under the id schedule.add answered", len(got) == 1 and got[0]["id"] == sid and got[0]["target"] == "worker"
          and got[0]["cron"] == "0 9 * * *" and got[0]["message"] == "check the board", str(got))
    check("schedules.list: never run yet, and next at a time to come", got[0]["last_run"] is None and got[0]["next_run"] > time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), str(got[0]))
    con = db.connect()
    db.add_event(con, "worker", "schedule_fired", {"cron": "0 9 * * *", "request_id": 1}, ts=int(time.time()) - 60)
    con.close()
    got = _call("schedules.list")["result"]["schedules"]
    check("schedules.list: a firing is its last run", got[0]["last_run"] is not None, str(got))
    _call("schedule.remove", {"schedule": sid, "request_id": "c2"})
    check("schedules.list: removed, it is gone", _call("schedules.list")["result"]["schedules"] == [])


def test_schedules_list_says_a_bad_value_and_not_none(home: Path) -> None:
    _world(home)
    agents.write("worker", {**agents.read("worker"), "schedules": "not json"})
    got = _call("schedules.list")["result"]["schedules"]
    check("schedules.list: a bad value is listed with its error", len(got) == 1 and got[0]["target"] == "worker"
          and got[0]["error"].startswith("schedules is not JSON") and set(got[0]) == {"target", "error"}, str(got))
    check("schedules.list: that answer is in the spec's schema", _invalid(_call("schedules.list")["result"], RESULT["schedules.list"]) is None)


def test_stats_count_a_window(home: Path) -> None:
    _world(home)
    now = int(time.time())
    con = db.connect()
    try:
        tid = db.new_task(con, "work", "worker", "manager")
        con.execute("UPDATE tasks SET done_evidence = 'x', done_at = ? WHERE id = ?", (now - 3600, tid))
        old = db.new_task(con, "old", "worker", "manager")
        con.execute("UPDATE tasks SET done_evidence = 'x', done_at = ? WHERE id = ?", (now - 3 * 86400, old))
        db.add_event(con, "worker", "moved_account", {"from": "a", "to": "b", "trigger": "pinned"}, ts=now - 100)
        db.add_event(con, "worker", "task_handoff", {"task_id": 1}, ts=now - 100)
        db.add_event(con, "worker", "block_task", {"task_id": 1}, ts=now - 100)
        db.add_event(con, "worker", "UserPromptSubmit", {}, ts=now - 900)
        db.add_event(con, "worker", "Stop", {}, ts=now - 600)
    finally:
        con.close()
    day = _call("stats.get", {"window": "24h"})["result"]
    week = _call("stats.get", {"window": "7d"})["result"]
    check("stats.get: tasks done in the window only", day["tasks_done"] == 1 and week["tasks_done"] == 2, str((day, week)))
    check("stats.get: moves (this one and the world's), handoffs and blocks in it", (day["moves"], day["handoffs"], day["blocks"]) == (2, 1, 1), str(day))
    check("stats.get: seconds worked, from a prompt to its stop", day["working_seconds"] == {"worker": 300}, str(day["working_seconds"]))
    check("stats.get: the use of each account", {"account": "alpha", "five_hour_used_pct": 40.0, "weekly_used_pct": 10.0} in day["per_account"], str(day["per_account"]))
    for bad in ({}, {"window": "1h"}, {"window": 24}):
        r = _call("stats.get", bad)
        check(f"stats.get: {bad} is bad_request naming window", _code(r) == "bad_request" and r["error"]["data"]["field"] == "window", str(r))


def test_settings_say_where_every_setting_stands(home: Path) -> None:
    _world(home)
    got = _call("settings.get")["result"]
    check("settings.get: every value the spec names, with a default where there is none set", got["values"]["active_cap"] >= 1
          and got["values"]["park_timeout_s"] == 600 and got["values"]["handoff_note"] is False or got["values"]["handoff_note"] is True, str(got["values"]))
    check("settings.get: the timings are the seconds-valued ones", got["timings"]["bg_grace_s"] == 300 and "active_cap" not in got["timings"], str(got["timings"]))
    check("settings.get: the daemon has no network listener of its own", got["listener"]["enabled"] is False and got["listener"]["fingerprint"] is None, str(got["listener"]))
    r = _call("settings.set", {"changes": {"park_timeout_s": 120, "dev_mode": True}, "request_id": "t1"})
    r = _call("settings.set", {"changes": {"park_timeout_s": 30}, "agent": "worker", "request_id": "t2"})
    now = _call("settings.get")["result"]["values"]
    check("settings.get: what settings.set wrote is what it reads", now["park_timeout_s"] == 120 and now["dev_mode"] is True, str(now))
    own = _call("settings.get", {"agent": "worker"})["result"]
    check("settings.get: with an agent, its own overrides", own["overrides"] == {"park_timeout_s": 30} and "overrides" not in _call("settings.get")["result"], str(own.get("overrides")))
    r = _call("settings.get", {"agent": "ghost"})
    check("settings.get: an agent that is not there is not_found", _code(r) == "not_found", str(r))
    r = _call("settings.get", {"agent": 5})
    check("settings.get: an agent that is not a name is bad_request", _code(r) == "bad_request", str(r))


def test_the_commands_the_list_methods_serve_still_work_from_an_agent_session(home: Path) -> None:
    _world(home)
    actions.schedule_add("worker", "0 9 * * *", "check the board")
    for argv, want in ((["status"], "picked"), (["account", "list"], None), (["team"], "manager"), (["hr", "list"], None),
                       (["schedule", "list"], "check the board")):
        rc, out = support._cli(argv)
        check(f"hl {' '.join(argv)}: works at role agent", (rc == 0 or want == "picked") and (want is None or want in out), out)


def test_the_list_fixtures_pass_against_this_daemon(home: Path) -> None:
    import socket
    import threading
    from handline.daemon import socket_server
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "contract"))
    import conformance

    def connector():
        def connect():
            a, b = socket.socketpair()
            threading.Thread(target=socket_server._serve_one, args=(b, lambda m: None), daemon=True).start()
            return a
        return connect
    _world(home)
    real = sessiontoken.agent_of
    results = []
    try:
        for role in ("founder", "agent", "anonymous"):
            sessiontoken.agent_of = (lambda pid: "manager") if role == "agent" else (lambda pid: None)
            results += [(*r, role) for r in conformance.run_all([Path("contract/fixtures/lists.json")], {role: connector()},
                                                                 {"founder_key": _hp()["key"]}) if r[0] != "skip"]
    finally:
        sessiontoken.agent_of = real
    bad = [(n, why) for st, why, n, _ in results if st != "pass"]
    check("fixtures: every list fixture passes against this daemon, each as its own role", not bad and len({n for _, _, n, _ in results}) == 10, str(bad))


def main() -> int:
    def tests(td):
        run(lambda: test_every_list_answers_in_the_schema_the_spec_gives_it(Path(td) / "l1"))
        run(lambda: test_roles_decide_who_reads_what(Path(td) / "l2"))
        run(lambda: test_accounts_say_what_the_account_page_shows(Path(td) / "l3"))
        run(lambda: test_team_names_each_agent_its_parent_and_files_and_the_edges(Path(td) / "l4"))
        run(lambda: test_schedules_carry_the_id_the_write_methods_use_and_when_they_run(Path(td) / "l6"))
        run(lambda: test_schedules_list_says_a_bad_value_and_not_none(Path(td) / "l11"))
        run(lambda: test_stats_count_a_window(Path(td) / "l7"))
        run(lambda: test_settings_say_where_every_setting_stands(Path(td) / "l8"))
        run(lambda: test_the_commands_the_list_methods_serve_still_work_from_an_agent_session(Path(td) / "l9"))
        run(lambda: test_the_list_fixtures_pass_against_this_daemon(Path(td) / "l10"))
    return run_area(tests)


if __name__ == "__main__":
    sys.exit(main())
