#!/usr/bin/env python3
"""Build contract/tick/fixtures/*.json from the Python suite's own setups.

  env -u OCP_AGENT -u HANDLINE_AGENT python3 contract/tick/scenarios.py          write the fixtures
  env -u OCP_AGENT -u HANDLINE_AGENT python3 contract/tick/scenarios.py --check  fail if one is stale

Each scenario is a setup lifted from a named test (or a stuck case from the history), run against a
real home with the clock frozen; what comes out is data - the home's files, the database rows and
the scripted inputs - so neither daemon needs this file. The fixtures are the contract; this is only
how they were made.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT))
T0 = 1_760_000_000
SKIP = ("db.sqlite",)

import support as S  # noqa: E402  (sets the test home before handline is used)
from handline import db  # noqa: E402
from handline.daemon import states  # noqa: E402

time.time = lambda: float(T0)
SCENARIOS: list[tuple[str, str, str, object]] = []


def scenario(name: str, source: str, what: str):
    def deco(fn):
        SCENARIOS.append((name, source, what, fn))
        return fn
    return deco


class World:
    """A daemon home as the suite's `_daemon_home` makes it, plus the scripted inputs a fixture carries."""

    def __init__(self, n: int, pct: int | None = 20) -> None:
        self.home = Path(S.os.environ["OCP_HOME"]) / f"w{n}"
        S._daemon_home(self.home, pct)
        S.os.environ["OCP_HOME"] = str(self.home)
        self.con = db.connect()
        self.knobs: dict = {"host_up": False, "processes": None, "ticks": 1, "commands": [
            {"argv": ["claude", "auth", "status", "--json"], "rc": 1, "err": "not logged in"},
            {"argv": ["claude", "--model", "haiku", "-p", "hi"], "rc": 1, "err": "scripted: no model call"},
            {"argv": ["git", "rev-parse", "--abbrev-ref"], "rc": 0, "out": "topic\n"},
            {"argv": ["git", "rev-parse", "--short"], "rc": 0, "out": "abc1234\n"},
            {"argv": ["git", "rev-parse"], "rc": 0, "out": "0" * 40 + "\n"},
            {"argv": ["git", "rev-list"], "rc": 0, "out": "0\t0\n"},
            {"argv": ["git", "status"], "rc": 0, "out": ""},
            {"argv": ["git", "fetch"], "rc": 0}]}
        self.terminals: dict = {}

    def now(self) -> int:
        return T0

    def status(self, acct="alpha", **kw) -> None:
        kw.setdefault("collected_at", T0)
        S.write_status(self.home, acct, **kw)

    def request(self, to="backend", frm="marina", body="carry on"):
        return db.add_request(self.con, frm, to, body, None, "peer", "async", None, 0, None)

    def login(self, *accts) -> None:
        S.credentialed(self.home, *accts)

    def worktree(self, name="wt") -> str:
        (self.home / name).mkdir(exist_ok=True)
        return str(self.home / name)

    def agent(self, name: str, ctx_override: int | None = None, repo: str | None = None) -> None:
        """Register another agent; `ctx_override` is its own context_tokens."""
        S.agents.register(name, **({"repo": repo} if repo else {}))
        if ctx_override:
            f = S.agents.agent_file(name)
            f.write_text(f.read_text() + f"context_tokens: {ctx_override}\n")
            S.reseal(f)

    def agent_status(self, agent: str, acct: str = "alpha", **kw) -> None:
        kw.setdefault("collected_at", T0)
        S.write_agent_status(agent, acct, **kw)

    def lives(self, agent: str, handle: str = "term_x", acct: str = "alpha", wt: str = "wt") -> None:
        """The agent has a terminal on the host, a session in the process table and an account."""
        db.set_terminal(self.con, agent, handle, self.worktree(wt))
        db.set_account(self.con, agent, acct)

    def host(self, **terms) -> None:
        """The terminal host is up, with these terminals (handle -> screen rows or a dict)."""
        self.knobs["host_up"] = True
        self.terminals = {h: ({"screen": v} if isinstance(v, list) else v) for h, v in terms.items()}

    def sessions(self, **by_agent) -> None:
        """The process table lists these agents' sessions (agent -> {pid, account?, terminal?})."""
        self.knobs["processes"] = [{"pid": 4200 + i, "agent": a, "command": "claude", **kw}
                                   for i, (a, kw) in enumerate(by_agent.items())]

    def snapshot(self, name: str, source: str, what: str) -> dict:
        self.con.commit()
        files = {}
        for p in sorted(self.home.rglob("*")):
            rel = p.relative_to(self.home).as_posix()
            if p.is_dir():
                if not any(p.iterdir()):
                    self.knobs.setdefault("dirs", []).append(rel)
                continue
            if rel.startswith(SKIP) or rel.endswith((".lock", ".sock")):
                continue
            try:
                files[rel] = p.read_text()
            except UnicodeDecodeError:
                continue
        tables = {}
        for (t,) in self.con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name"):
            cur = self.con.execute(f'select rowid as "__rowid", * from "{t}" order by rowid')
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            if rows:
                tables[t] = rows
        fx = {"format": 1, "name": name, "source": source, "what": what,
              "clock": {"start": T0, "step_s": 30, "ticks": self.knobs.pop("ticks", 1)},
              **{k: v for k, v in self.knobs.items()}}
        if self.terminals:
            fx["terminals"] = self.terminals
        fx["files"], fx["db"] = files, tables
        return json.loads(json.dumps(fx).replace(str(self.home), "{HOME}"))


SD = "tests/session/test_daemon_session.py"


@scenario("park-at-the-wall", f"{SD}::test_daemon_park",
          "An agent over its 5-hour limit is parked with a handoff and a wake_at at the reset.")
def _(w):
    w.status(pct_5h=100, pct_7d=10, ctx_pct=5, reset_5h=T0 + 1800)


@scenario("due-parked-with-work-resumes", f"{SD}::test_daemon_resume",
          "A parked agent whose window rolled over returns to QUEUED when work is waiting.")
def _(w):
    db.set_agent_state(w.con, "backend", states.PARKED, wake_at=T0 - 10, detail="parked earlier")
    w.request()
    w.status(pct_5h=3, pct_7d=10, ctx_pct=5)


@scenario("expired-handoff-degrades", f"{SD}::test_daemon_tick_expires_handoff",
          "A handoff past its deadline is failed by the tick, not left wedging the agent in HANDOFF.")
def _(w):
    db.start_handoff(w.con, "h_tick", "backend", "s", "usage", T0 - 600)
    db.set_agent_state(w.con, "backend", states.HANDOFF)
    w.status(pct_5h=50)


@scenario("degraded-due-with-work-recovers", f"{SD}::test_daemon_degraded_recovers",
          "A due DEGRADED agent with a request waiting goes back to QUEUED or PARKED.")
def _(w):
    w.request()
    db.set_agent_state(w.con, "backend", states.DEGRADED, wake_at=T0 - 1)
    w.status(pct_5h=50)


@scenario("throttled-wake-with-nothing-queued-reads-idle", f"{SD}::test_a_stale_wake_at_does_not_claim_work_that_does_not_exist",
          "A due THROTTLED wake on an empty queue reads IDLE, never QUEUED.")
def _(w):
    db.set_agent_state(w.con, "backend", states.THROTTLED, wake_at=T0 - 1, detail="throttled for an unrelated reason")
    w.status(pct_5h=50)


@scenario("throttled-wake-with-a-request-reads-queued", f"{SD}::test_a_stale_wake_at_does_not_claim_work_that_does_not_exist",
          "The same due wake with a real request waiting reads QUEUED.")
def _(w):
    db.set_agent_state(w.con, "backend", states.THROTTLED, wake_at=T0 - 1, detail="throttled for an unrelated reason")
    w.request()
    w.status(pct_5h=50)


@scenario("usage-recovered-unparks-to-idle", f"{SD}::test_the_other_wake_writers_also_read_the_queue",
          "A parked agent whose account recovered wakes IDLE when nothing is queued.")
def _(w):
    S._park_via_handoff(w.con, "backend", "usage")
    db.set_agent_state(w.con, "backend", states.PARKED, wake_at=T0 + 3600, detail="usage")
    w.status(pct_5h=5, pct_7d=10, ctx_pct=5, reset_5h=T0 + 1800)
    w.host(term_x=list(S.COMPOSER_EMPTY))
    w.sessions(backend={"terminal": "term_x", "account": "alpha"})


@scenario("usage-recovered-unparks-to-queued", f"{SD}::test_the_other_wake_writers_also_read_the_queue",
          "The same recovery wakes QUEUED when a request is waiting.")
def _(w):
    w.request()
    S._park_via_handoff(w.con, "backend", "usage")
    db.set_agent_state(w.con, "backend", states.PARKED, wake_at=T0 + 3600, detail="usage")
    w.status(pct_5h=5, pct_7d=10, ctx_pct=5, reset_5h=T0 + 1800)
    w.host(term_x=list(S.COMPOSER_EMPTY))
    w.sessions(backend={"terminal": "term_x", "account": "alpha"})


@scenario("stale-status-is-not-believed", f"{SD}::test_daemon_ignores_stale_status",
          "A frozen status.json (collector dead, 100000 s old) reading 100% is not a wall: nothing fires.")
def _(w):
    w.status(pct_5h=100, pct_7d=10, ctx_pct=5, collected_at=T0 - 100000, reset_5h=T0 + 1800)


@scenario("handoff-in-flight-is-left-alone", f"{SD}::test_daemon_tick_expires_handoff",
          "A handoff whose deadline is still ahead stays in flight; the tick does not fail it.")
def _(w):
    db.start_handoff(w.con, "h_live", "backend", "s", "usage", T0 + 600)
    db.set_agent_state(w.con, "backend", states.HANDOFF)
    w.status(pct_5h=50)


@scenario("stalled-is-not-cleared-by-the-tick", f"{SD}::test_daemon_stall_decision",
          "STALLED (repeated degradation) is only cleared by a person, never by a tick.")
def _(w):
    db.set_agent_state(w.con, "backend", states.STALLED, detail="degraded repeatedly")
    w.status(pct_5h=20)


@scenario("repeated-degradation-raises-stalled", f"{SD}::test_daemon_stall_decision",
          "A due DEGRADED agent already at the throttle streak limit goes to STALLED, not back to work.")
def _(w):
    db.set_agent_state(w.con, "backend", states.DEGRADED, wake_at=T0 - 1, detail="handoff degraded")
    w.con.execute("UPDATE agent_state SET throttle_count = ? WHERE agent = 'backend'", (states.MAX_THROTTLE_STREAK,))
    w.request()
    w.status(pct_5h=100, pct_7d=10, ctx_pct=5, reset_5h=T0 + 1800)


@scenario("parked-stays-parked-on-its-own-context", f"{SD}::test_a_parked_agent_is_unparked_by_its_own_context_limit",
          "Parked for usage, account recovered, but 200k tokens of context: the default limit keeps it parked.")
def _(w):
    w.lives("backend")
    S._park_via_handoff(w.con, "backend", "usage")
    db.set_agent_state(w.con, "backend", states.PARKED, wake_at=T0 + 3600, detail="usage; wakes at 23:00")
    w.status(pct_5h=5, pct_7d=10, reset_5h=T0 + 1800)
    w.agent_status("backend", ctx_used=200_000, model_id="claude-sonnet-5")
    w.request()
    w.host(term_x=list(S.COMPOSER_EMPTY))
    w.sessions(backend={"terminal": "term_x", "account": "alpha"})


@scenario("context-override-unparks", f"{SD}::test_a_parked_agent_is_unparked_by_its_own_context_limit",
          "The same agent with its own context_tokens override of 10M is under its limit and un-parks.")
def _(w):
    w.agent("parkctl", ctx_override=10_000_000)
    w.lives("parkctl", "term_y")
    S._park_via_handoff(w.con, "parkctl", "usage")
    db.set_agent_state(w.con, "parkctl", states.PARKED, wake_at=T0 + 3600, detail="usage; wakes at 23:00")
    w.status(pct_5h=5, pct_7d=10, reset_5h=T0 + 1800)
    w.agent_status("parkctl", ctx_used=200_000, model_id="claude-sonnet-5")
    w.request("parkctl")
    w.host(term_y=list(S.COMPOSER_EMPTY))
    w.sessions(parkctl={"terminal": "term_y", "account": "alpha"})


@scenario("due-park-with-no-terminal-is-started", f"{SD}::test_a_due_park_with_no_terminal_is_started_not_held",
          "A reboot leaves a closed park, due, with work waiting and no terminal: the tick gives it a terminal.")
def _(w):
    w.login("alpha")
    w.agent("marina", repo=w.worktree("checkout"))
    db.set_terminal(w.con, "marina", None, None)
    db.set_closed(w.con, "marina", "sess_old")
    S.something_waiting(w.con, "marina")
    db.set_agent_state(w.con, "marina", states.PARKED, wake_at=T0 - 10, detail="usage; wakes at 01:59")
    w.status(pct_5h=20)
    w.host()
    w.knobs["new_worktrees"] = [str(w.home / "marina-wt")]
    w.knobs["new_terminals"] = [{"handle": "term_new", "starts_session": {"agent": "marina", "account": "alpha"}}]
    w.sessions()


@scenario("closed-park-wakes-in-a-new-terminal-resuming", f"{SD}::test_a_parked_closed_agent_without_a_pane_is_woken_at_its_deadline",
          "A closed park at its deadline, with a worktree and a told request, is woken into a new terminal with --resume.")
def _(w):
    wt = w.worktree("wt")
    rid = w.request("backend", "reviewer", "long job")
    db.mark_delivered(w.con, rid)
    db.set_terminal(w.con, "backend", None, wt)
    db.set_closed(w.con, "backend", "sess-4")
    db.set_agent_state(w.con, "backend", states.PARKED, wake_at=T0 - 5, detail="waiting for the window")
    w.status(pct_5h=20)
    w.agent_status("backend", session_id="sess-4", pct_5h=20)
    w.host()
    w.knobs["new_terminals"] = [{"handle": "term_x", "starts_session": {"agent": "backend", "account": "alpha"}}]
    w.sessions()


@scenario("unreadable-process-table-refuses-to-type", f"{SD}::test_a_wake_refuses_to_type_when_the_process_table_is_unreadable",
          "With the process table unreadable the daemon cannot tell a session from a shell: it types nothing.")
def _(w):
    w.lives("backend")
    w.request()
    db.set_closed(w.con, "backend", "sess-1")
    db.set_agent_state(w.con, "backend", states.IDLE, detail="finished its turn")
    w.status(pct_5h=20)
    w.host(term_x=["$ "])
    w.knobs["processes"] = None


SS = "tests/session/test_session_io.py"


@scenario("idle-agent-with-work-is-typed-at", f"{SD}::test_a_running_agent_is_told_when_work_arrives",
          "An IDLE agent on a live session with a request waiting is handed the work on its terminal.")
def _(w):
    w.lives("backend")
    db.set_agent_state(w.con, "backend", states.IDLE, detail="finished its turn")
    w.request()
    w.status(pct_5h=20)
    w.host(term_x=list(S.COMPOSER_EMPTY))
    w.sessions(backend={"terminal": "term_x", "account": "alpha"})


@scenario("mid-turn-terminal-is-told-at-once", f"{SD}::test_a_running_agent_is_told_when_work_arrives",
          "The same agent while its terminal is mid-turn (never idle) is still told about the request at once.")
def _(w):
    w.lives("backend")
    db.set_agent_state(w.con, "backend", states.IDLE, detail="finished its turn")
    w.request()
    w.status(pct_5h=20)
    w.host(term_x={"screen": list(S.COMPOSER_EMPTY), "idle": False})
    w.sessions(backend={"terminal": "term_x", "account": "alpha"})


@scenario("nothing-waiting-nothing-typed", f"{SD}::test_a_running_agent_is_told_when_work_arrives",
          "An IDLE agent on a live session with an empty queue is left alone.")
def _(w):
    w.lives("backend")
    db.set_agent_state(w.con, "backend", states.IDLE, detail="finished its turn")
    w.status(pct_5h=20)
    w.host(term_x=list(S.COMPOSER_EMPTY))
    w.sessions(backend={"terminal": "term_x", "account": "alpha"})


@scenario("closed-agent-with-work-is-resumed", f"{SD}::test_a_closed_session_closes_its_pane_and_the_wake_opens_a_new_terminal_that_resumes",
          "A closed agent (not parked) with a request waiting is woken in a new terminal that resumes its session.")
def _(w):
    wt = w.worktree("wt")
    w.request("backend", "reviewer", "pick this up")
    db.set_terminal(w.con, "backend", None, wt)
    db.set_closed(w.con, "backend", "sess-9")
    db.set_agent_state(w.con, "backend", states.IDLE, detail="closed")
    w.status(pct_5h=20)
    w.agent_status("backend", session_id="sess-9", pct_5h=20)
    w.host()
    w.knobs["new_terminals"] = [{"handle": "term_x", "starts_session": {"agent": "backend", "account": "alpha"}}]
    w.sessions()


@scenario("closed-agent-with-nothing-waiting-stays-closed", f"{SD}::test_a_closed_session_closes_its_pane_and_the_wake_opens_a_new_terminal_that_resumes",
          "A closed agent with an empty queue is not woken.")
def _(w):
    db.set_terminal(w.con, "backend", None, w.worktree("wt"))
    db.set_closed(w.con, "backend", "sess-9")
    db.set_agent_state(w.con, "backend", states.IDLE, detail="closed")
    w.status(pct_5h=20)
    w.host()
    w.sessions()


@scenario("process-on-the-wrong-account", f"{SD}::test_the_sweep_corrects_what_it_can_see",
          "The process table shows the session running under beta while it is recorded on alpha: the tick corrects the record.")
def _(w):
    w.lives("backend")
    db.set_agent_state(w.con, "backend", states.IDLE, detail="finished its turn")
    w.login("beta")
    w.status(pct_5h=20)
    w.status("beta", pct_5h=10)
    w.host(term_x=list(S.COMPOSER_EMPTY))
    w.sessions(backend={"terminal": "term_x", "account": "beta"})


@scenario("context-full-hands-off-and-clears", f"{SD}::test_a_context_handoff_does_not_thrash_within_min_handoff_interval_s",
          "A running agent whose context passed its limit is asked for a handoff and its context cleared.")
def _(w):
    w.lives("backend")
    db.set_agent_state(w.con, "backend", states.IDLE, detail="finished its turn")
    w.status(pct_5h=10, pct_7d=10)
    w.agent_status("backend", ctx_used=300_000, model_id="claude-sonnet-5")
    w.host(term_x=list(S.COMPOSER_EMPTY))
    w.sessions(backend={"terminal": "term_x", "account": "alpha"})


@scenario("context-handoff-too-soon-is-skipped", f"{SD}::test_a_context_handoff_does_not_thrash_within_min_handoff_interval_s",
          "The same agent whose last handoff was a minute ago is not folded again (MIN_HANDOFF_INTERVAL_S).")
def _(w):
    w.lives("backend")
    db.set_agent_state(w.con, "backend", states.IDLE, detail="finished its turn")
    db.start_handoff(w.con, "h_recent", "backend", "s", "context", T0 + 60)
    db.advance_handoff(w.con, "h_recent", "released", state="done")
    w.con.execute("UPDATE handoff SET updated = ? WHERE id = 'h_recent'", (T0 - 60,))
    w.status(pct_5h=10, pct_7d=10)
    w.agent_status("backend", ctx_used=300_000, model_id="claude-sonnet-5")
    w.host(term_x=list(S.COMPOSER_EMPTY))
    w.sessions(backend={"terminal": "term_x", "account": "alpha"})


@scenario("a-live-session-is-told-whatever-the-cap", f"{SD}::test_daemon_resume",
          "With active_cap 1 and one agent mid-turn on the account, a second agent on a live session is still told about its request: the cap limits starts, not messages.")
def _(w):
    cfg = S.loadmod.config_for_write()
    cfg["active_cap"] = 1
    S.loadmod.write_config(cfg)
    w.agent("second")
    w.lives("backend")
    w.lives("second", "term_y")
    db.set_agent_state(w.con, "backend", states.RUNNING, detail="working")
    db.set_agent_state(w.con, "second", states.IDLE, detail="finished its turn")
    w.request("second")
    w.status(pct_5h=20)
    w.host(term_x=list(S.COMPOSER_EMPTY), term_y=list(S.COMPOSER_EMPTY))
    w.sessions(backend={"terminal": "term_x", "account": "alpha"}, second={"terminal": "term_y", "account": "alpha"})


@scenario("degraded-not-yet-due-waits", f"{SD}::test_daemon_degraded_recovers",
          "A DEGRADED agent whose wake_at is still ahead keeps waiting even with a request queued.")
def _(w):
    w.request()
    db.set_agent_state(w.con, "backend", states.DEGRADED, wake_at=T0 + 600, detail="handoff degraded")
    w.status(pct_5h=50)


def _scheduled(w, items):
    w.lives("backend")
    db.set_agent_state(w.con, "backend", states.IDLE, detail="finished its turn")
    cfg = S.agents.read("backend")
    cfg["schedules"] = json.dumps(items)
    S.agents.write("backend", cfg)
    w.status(pct_5h=20)
    w.host(term_x=list(S.COMPOSER_EMPTY))
    w.sessions(backend={"terminal": "term_x", "account": "alpha"})


@scenario("schedule-fires-at-the-next-due-minute", "tests/daemon/test_schedules.py::test_a_due_schedule_is_typed_once_and_waits_for_a_running_agent",
          "An every-minute schedule is first seen at tick 1 (nothing sent); the next minute boundary, three ticks on, sends its message once.")
def _(w):
    _scheduled(w, [{"cron": "* * * * *", "message": "run the nightly check"}])
    w.knobs["ticks"] = 3


@scenario("schedule-not-due-is-quiet", "tests/daemon/test_schedules.py::test_a_due_schedule_is_typed_once_and_waits_for_a_running_agent",
          "A schedule that never comes due (31 February) types nothing.")
def _(w):
    _scheduled(w, [{"cron": "0 0 31 2 *", "message": "never"}])
    w.knobs["ticks"] = 3


@scenario("schedule-fires-per-due-minute-not-per-tick", "tests/daemon/test_schedules.py::test_a_due_schedule_is_typed_once_and_waits_for_a_running_agent",
          "Six ticks 30 s apart span two or three minute boundaries: the message is sent at each, not at every tick.")
def _(w):
    _scheduled(w, [{"cron": "* * * * *", "message": "tick"}])
    w.knobs["ticks"] = 6


def build() -> dict[str, dict]:
    out = {}
    for i, (name, source, what, fn) in enumerate(SCENARIOS, 1):
        w = World(i)
        fn(w)
        out[f"{i:02d}-{name}"] = w.snapshot(f"{i:02d}-{name}", source, what)
    return out


def main(argv: list[str]) -> int:
    fixtures = build()
    d = HERE / "fixtures"
    d.mkdir(exist_ok=True)
    if "--check" in argv:
        bad = [n for n, fx in fixtures.items() if not (d / f"{n}.json").exists()
               or json.loads((d / f"{n}.json").read_text()) != fx]
        extra = [p.name for p in d.glob("*.json") if p.stem not in fixtures]
        for n in bad + extra:
            print(f"stale fixture: {n}; run contract/tick/scenarios.py", file=sys.stderr)
        return 1 if bad or extra else 0
    for p in d.glob("*.json"):
        p.unlink()
    for n, fx in fixtures.items():
        (d / f"{n}.json").write_text(json.dumps(fx, indent=1, sort_keys=True) + "\n")
    print(len(fixtures), "fixtures")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
