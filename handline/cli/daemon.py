"""`hl daemon` and dev mode."""
from __future__ import annotations

import sys
import time

from .. import actions
from ..infra import timefmt
from ..session import blocked as blockedmod
from ..daemon import locks as dmn_locks, loop as dmn_loop
from ..infra import daemonclient as dmn_client, devmode as dmn_devmode, daemonlock
from .. import db
from ..session import launch
from ..session import observe as obs
from ..session import term
from ..infra import gitrun, paths, protected, service
from ..infra import textsafe
from ..infra.yamlcompat import ConfigError
from ..view import viewdata

from . import client
from .health import _name_col


# ---------------------------------------------------------------- daemon
def cmd_daemon(args) -> int:
    return dmn_loop.run(once=args.once, verbose=not args.quiet,
                         max_ticks=args.max_ticks)


def cmd_dev_mode(args) -> int:
    """Off by default (task #64). A running daemon reads config.yaml once a
    tick, right after that tick finishes, so `hl dev-mode on` takes effect
    on its next tick - no restart needed to turn it on."""
    if args.state is None:
        print("on" if dmn_devmode.dev_mode_enabled() else "off")
        return 0
    if protected.in_agent_session():
        from .hr import propose_for_session
        return propose_for_session("global", "dev-mode", {"value": args.state}, args.why)
    try:
        dmn_devmode.set_dev_mode(args.state == "on")
    except ConfigError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"dev-mode: {args.state}")
    return 0


def _print_waits() -> None:
    """The founder's waits above everything else `ps` prints, in words: the same
    list the screen pins in red."""
    con = db.connect()
    try:
        waits = actions.founder_waits(con)
    finally:
        con.close()
    if not waits:
        return
    now = time.time()
    print(f"{viewdata.WAITING_ON_YOU} ({len(waits)})")
    for w in waits:
        print(f"  {textsafe.oneline(w['who'])} · {textsafe.oneline(w['what'])} · {timefmt.ago(now - w['since'])}")
    print()


def _code_line() -> str:
    """Which code the running daemon is on, from its own hello, and whether the checkout has moved on."""
    try:
        sock = dmn_client.connect(5.0)
        sha = dmn_client.call(sock, "hello", {"client": "hl", "protocol": 1})["daemon"] if sock else None
    except (OSError, dmn_client.RpcError):
        sha = None
    if not sha:
        return "  code: unknown (the daemon did not answer hello)"
    rc, out, _ = gitrun.run(paths.repo_root(), "rev-parse", "--short", "HEAD")
    head = out.strip() if rc == 0 else ""
    return f"  code: {sha}" + (f" - the checkout is at {head}: restart it with `{db.schema.RESTART_COMMAND}`" if head and head != sha else "")


def _state_of(line: dict | None) -> tuple[str, str]:
    """An agent's STATE word and its detail from its agents.list status line; "-" when the daemon gave none."""
    if not line or not line["screen_word"]:
        return "-", ""
    return line["screen_word"], textsafe.oneline(line["screen_detail"])


def cmd_daemon_status(args) -> int:
    _print_waits()
    pid = daemonlock.running_elsewhere()
    supervisor = service.supervisor_of(pid)
    print(f"daemon: {'running, pid ' + str(pid) if pid else 'not running'} - {supervisor}")
    if pid:
        print(_code_line())
    live = dmn_locks.processes()
    if len(live) > 1:
        print(f"  WARNING: {len(live)} daemons are running (pid "
              f"{', '.join(str(x) for x in live)}). Two supervisors notify, park and wake "
              f"the same agent twice - end all but one.")
    elif live and not pid:
        print(f"  (pid {live[0]} is an `hl daemon` process that does not hold the lock)")
    con = db.connect()
    try:
        rows = db.all_agent_states(con)
        if not rows:
            print("no agent state recorded yet")
        else:
            w = _name_col([r["agent"] for r in rows], 14)
            # STATE and the wall on a wake are the daemon's (agents.list); `ps` reports a daemon that is down, so it never starts one.
            try:
                lines = {a["name"]: a for a in client.call("agents.list", {"status": True}, start=False)["agents"]}
            except (dmn_client.DaemonDown, dmn_client.RpcError, OSError):
                lines = {}
            words = {r["agent"]: _state_of(lines.get(r["agent"])) for r in rows}
            sw = max([len("state")] + [len(word) + (1 + len(extra) if extra else 0)
                                       for word, extra in words.values()]) + 1
            print(f"\n{'AGENT':<{w}}{'ACCT':<8}{'STATE':<{sw}}{'WAKE':<8}{'fail':>5}{'cont':>6}{'thr':>5}  DETAIL")
            now = int(time.time())
            sessions = obs.live_sessions()
            for r in rows:
                wake = "-" if not r["wake_at"] else timefmt.stamp(r["wake_at"], timefmt.CLOCK)
                if r["wake_at"] and r["wake_at"] <= now:
                    wake += "!"
                # A running session with a stale launch is not closed, so it
                # needs its own mark; see `launch.fingerprint`.
                drifted = bool(
                    not r["closed_at"] and sessions and sessions.get(r["agent"])
                    and launch.drifted(r["agent"], r))
                word, extra = words[r["agent"]]
                shown = word + (f" {extra}" if extra else "") + ("·drift" if drifted else "")
                detail = textsafe.oneline(r["detail"] or "")
                prompt = actions.open_block_of(con, r["agent"])
                block = db.block_get(con, r["agent"])
                if prompt:
                    detail = textsafe.oneline(actions.block_headline(prompt))
                elif block and block["kind"] in (term.MODAL, term.BACKGROUNDED):
                    detail = textsafe.oneline(blockedmod.headline(block["kind"], block["detail"]))
                walled = textsafe.oneline((lines.get(r["agent"]) or {}).get("wake_wait_reason") or "")
                if walled:
                    # The reason is budgeted first and the agent's own detail
                    # yields room to it, not the other way around: a detail
                    # sitting ahead of an undelivered answer is the ordinary
                    # case this column exists to surface.
                    room = max(0, 70 - len(walled) - 3)
                    detail = f"{detail[:room]} · {walled}" if detail else walled
                else:
                    detail = detail[:70]
                print(f"{r['agent']:<{w}}{(r['account'] or '-'):<8}{shown:<{sw}}{wake:<8}"
                      f"{r['failed_attempts']:>5}{r['continuations']:>6}{r['throttle_count']:>5}  "
                      f"{detail}")
        flight = db.in_flight_handoffs(con)
        if flight:
            print("\nin-flight handoffs (resume from these steps):")
            for h in flight:
                print(f"  {h['agent']:<14} step={h['step']:<11} state={h['state']:<10} "
                      f"deadline={timefmt.stamp(h['deadline'], '%H:%M:%S')}")
    finally:
        con.close()
    return 0
