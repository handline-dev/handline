#!/usr/bin/env python3
"""Run one real agent end to end on the tmux backend and report each step.

  OCP_TERMINAL=tmux tools/tmux_trial.py <agent> <git repo> [--keep]

The agent must be registered with a logged-in account (`ocp agent register`, `ocp account add`).
Steps: launch (a worktree, a window, `ocp launch`), idle detection on the booted TUI, one delivered
message and the turn it starts, /clear, /exit, then the window closed. After each step it prints
what the screen readers made of the screen, and the last rows of the screen itself, so a parser that
needs a change for tmux shows up as a step that failed or a reading that is wrong for what is drawn.

It types into a real claude and uses a little of the account's quota (one short turn). It does not
need a running daemon. `--keep` leaves the window open at the end to look at.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("OCP_TERMINAL", "tmux")

from ocp import db  # noqa: E402
from ocp.session import backends, orca  # noqa: E402
from ocp.team import spawn  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def show(handle: str, label: str) -> None:
    """The readers' verdicts on this screen, then its last rows."""
    term = orca._screen(handle)
    rows = (term or {}).get("tail") or []
    blocker = orca.screen_blocker(handle)
    prompt = orca.prompt_on_screen(handle)
    print(f"    [{label}] blocker={getattr(blocker, 'kind', blocker)!r} prompt={prompt[0]!r} "
          f"trust={orca.trust_prompt_showing(handle)} typeable={orca.typeable_now(handle)}")
    for row in rows[-10:]:
        print(f"      | {row}")


def step(name: str, fn) -> object:
    t0 = time.monotonic()
    print(f"== {name}")
    try:
        ok, detail, value = fn()
    except Exception:  # noqa: BLE001 - a trial reports, it does not stop
        ok, detail, value = False, traceback.format_exc(limit=3), None
    RESULTS.append((name, ok, detail))
    print(f"   {'OK  ' if ok else 'FAIL'} {name} ({time.monotonic() - t0:.1f}s) {detail}")
    return value


def events_since(since: int, agent: str, kind: str) -> bool:
    con = db.connect()
    try:
        return any(r["kind"] == kind for r in db.events_after(con, since, agent=agent))
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("agent")
    ap.add_argument("repo")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    host = backends.current()
    print(f"backend: {host.name} (available: {host.available()})")
    if host.name != "tmux" or not host.available():
        print("this trial is for tmux: set OCP_TERMINAL=tmux and have tmux on PATH", file=sys.stderr)
        return 2
    state: dict = {}

    def launch():
        ok, msg = spawn.spawn(args.agent, Path(args.repo).resolve())
        con = db.connect()
        try:
            row = db.get_agent_state(con, args.agent)
        finally:
            con.close()
        state["handle"] = row["terminal"] if row else None
        return ok and bool(state["handle"]), msg.replace("\n", " | "), state["handle"]
    step("launch: worktree, window, ocp launch, a session in the process table", launch)
    handle = state.get("handle")
    if not handle:
        print("no terminal, nothing more to try")
        return 1

    def alive():
        return orca.alive(handle), f"terminal {handle}", None
    step("list: the window is a live terminal", alive)

    def idle():
        ok = orca.wait_idle(handle, 90_000)
        show(handle, "after boot")
        return ok, "idle once the TUI has drawn its prompt", None
    step("idle: wait_idle on the booted TUI", idle)

    def busy():
        con = db.connect()
        try:
            since = db.max_event_id(con)
        finally:
            con.close()
        state["since"] = since
        ok, why = orca.send(handle, "Reply with the single word ok and nothing else.")
        time.sleep(1.5)
        mid = orca.wait_idle(handle, 1_500)
        show(handle, "turn started")
        return ok and not mid, f"send={why!r}; reported idle {mid} while the turn runs (want False)", None
    step("busy: a message is typed and a running turn is not idle", busy)

    def landed():
        deadline = time.time() + 60
        while time.time() < deadline and not events_since(state["since"], args.agent, "UserPromptSubmit"):
            time.sleep(1)
        ok = events_since(state["since"], args.agent, "UserPromptSubmit")
        return ok, "the agent's own hook reported the prompt (it is the witness; tmux cannot watch a prompt land)", None
    step("delivered: UserPromptSubmit arrived", landed)

    def settled():
        ok = orca.wait_idle(handle, 90_000)
        show(handle, "after the turn")
        return ok, "idle again after the turn", None
    step("idle: wait_idle after the turn", settled)

    def clear():
        ok, why = orca.clear_context(handle, wait=False)
        time.sleep(3)
        show(handle, "after /clear")
        return ok, why, None
    step("clear: /clear typed", clear)

    def exit_():
        ok, why = orca.close_session(handle, wait=False)
        time.sleep(5)
        show(handle, "after /exit")
        return ok and orca.alive(handle), f"{why}; the window should still be there with a shell in it", None
    step("exit: /exit typed, the window stays", exit_)

    if not args.keep:
        step("close: the window is closed", lambda: (orca.close_terminal(handle)[0] and not orca.alive(handle), "", None))
    print("\nsummary")
    for name, ok, detail in RESULTS:
        print(f"  {'OK  ' if ok else 'FAIL'} {name}")
    return 0 if all(ok for _, ok, _ in RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
