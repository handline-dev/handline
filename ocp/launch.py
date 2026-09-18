"""ocp launch - the execution layer (follows from Phase 0 verdict J4).

The design intended to inject CLAUDE_CONFIG_DIR through a worktree .envrc via
direnv. Phase 0 changed that premise:

  - Orca launches terminals with CLAUDE_CONFIG_DIR empty, so it is ours to set
  - `orca terminal create --command` takes an arbitrary shell string
  - `environmentRecipes` turned out to be for disposable remote machines, so it
    was never an env-injection fallback
  - direnv is not installed on this machine

So we use a launcher wrapper instead. Orca preserves the launch command across
hibernation and restore, which makes this recover better than .envrc would.

    orca terminal create --worktree path:<wt> --command "ocp launch backend"
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from dataclasses import dataclass

from . import accounts, agents, paths, status, triggers


# A session started this close to the wall folds almost at once: the usage
# trigger fires a reserve short of the hard stop, so an account inside that
# reserve has no room to work in, only room to hand off. The wall itself is
# still where a launch is refused - `build` does that, and says which account.
START_LIMIT = triggers.HARD_STOP_PCT - triggers.DEFAULT_RESERVE_PCT


def choose_account(agent: str, resuming: bool = False, limit: int | None = None) -> str:
    """Which account this launch runs on.

    agent.yaml names a preference, not a destination. The moment a session is
    made is the only moment its account can be chosen at all - after execve the
    config directory is fixed - so it is chosen here, and the preference is
    used whenever it still has room. Between two accounts the difference is
    headroom, which is what `pick_account` already measures for folds.

    Resuming is not that moment. A session id belongs to one account's config
    directory, so `--resume` on any other account finds nothing; a resumed
    session goes back to the account it was recorded on, or the run is not a
    resume at all.
    """
    if resuming:
        return agents.current_account(agent)
    preferred = agents.account_of(agent)
    # Only a reading that says "full" moves an agent. A missing or stale
    # snapshot is not a full tank and it is not an empty one either - it is the
    # account nobody has run on yet, and the session we are about to start is
    # the only thing that writes one. Treating unknown as full would send every
    # agent on a fresh install to whichever account happened to have a
    # snapshot, and leave the preferred mapping meaning nothing.
    st = status.read(preferred)
    pct = st.trusted_pct_5h
    full = (pct is not None and pct >= (limit if limit is not None else START_LIMIT)) or \
           (st.pct_7d is not None and st.pct_7d >= status.PCT_7D_BLOCK)
    if not full:
        return preferred
    # Among the accounts a human has actually logged in. `assignable` is about
    # usage and says nothing about credentials, so without this an agent moves
    # onto an account that cannot start anything and the launch dies there.
    candidates = [a for a in paths.list_accounts() if accounts.logged_in(a)]
    # bootstrap=True: a never-run account is a real place to go, not a reason
    # to sit out one at the wall (see pick_account).
    picked, _reasons = status.pick_account(candidates, bootstrap=True)
    # Nothing assignable is not a reason to pick something at random: `build`
    # refuses below on a current reading at the wall, and that refusal names
    # the account and the figure.
    return picked or preferred


@dataclass
class WakeWait:
    account: str
    pct: int
    reset_5h: int | None


def wake_wait(agent: str, row) -> WakeWait | None:
    """Is a closed agent's wake walled behind its own account's 5h window?

    This is the check daemon.py makes before it types anything into a closed
    agent's terminal (a resume is tied to the account its session lives under,
    so there is no other account to try). It is factored out here, rather than
    inlined at the call site, so the screens that show an agent can ask the
    same question instead of restating the rule and drifting from it.

    A computed condition, not a stored one: called again a moment later, on an
    agent with no work waiting or on an account whose window has since rolled
    over, it returns None.
    """
    if not row or not row["closed_at"]:
        return None
    acct = choose_account(agent, resuming=bool(row["resume_session"]))
    st = status.read(acct)
    pct = st.trusted_pct_5h
    if pct is None or pct < status.PCT_5H_BLOCK:
        return None
    return WakeWait(acct, pct, st.reset_5h)


def wake_wait_reason(agent: str, row) -> str | None:
    """The sentence ps, triggers and bridge print for a walled wake.

    One wording, called from all three screens and from the daemon's own log
    line, so a screen can never say something the daemon does not act on.
    """
    w = wake_wait(agent, row)
    if w is None:
        return None
    when = "unknown" if not w.reset_5h else time.strftime("%H:%M", time.localtime(w.reset_5h))
    return f"waiting for {w.account}'s 5h window ({w.pct}%, rolls over {when})"


def _is_resume(extra: list[str] | None) -> bool:
    """Does this launch carry a session to resume?

    Three spellings, because reading `--resume=<id>` as "not a resume" picks an
    account by headroom and then hands claude a session id that exists under
    the other one - a silently fresh session where a resume was asked for.
    """
    return any(x == "--resume" or x.startswith("--resume=") or x == "-r"
               for x in (extra or []))


def build(agent: str, extra: list[str] | None = None, force: bool = False) -> tuple[list[str], dict]:
    """Return (argv, env) ready to hand to execve."""
    acct = choose_account(agent, resuming=_is_resume(extra))

    if not accounts.logged_in(acct):
        raise SystemExit(
            f"account '{acct}' is not logged in.\n"
            f"A human has to do this once (ocp never touches credentials):\n\n"
            f"  {accounts.login_command(acct)}\n"
        )

    # Only a *current* reading may block a launch.
    #
    # Neither a missing nor a stale snapshot can, and for the same reason: the
    # session we are about to start is the only thing that writes one. An idle
    # account goes stale after STALE_AFTER_S, so refusing on staleness meant
    # nothing could be started after a quarter hour of quiet - the same
    # deadlock as refusing on first boot, just delayed.
    #
    # Staleness is still fail-closed where it belongs, in status.assignable():
    # the daemon will not *assign* work on a reading it cannot trust. And an
    # account that really is full self-corrects in seconds - the status line
    # refreshes as soon as the session starts, and the daemon's trigger then
    # hands off on fresh data.
    st = status.read(acct)
    if st.exists and st.stale:
        age = "unknown age" if st.age_s is None else f"{int(st.age_s)}s"
        print(f"warning: account '{acct}' usage is {age} old and may be out of date; "
              f"the status line will refresh it on start.", file=sys.stderr)
    elif not force and st.exists and st.trusted_pct_5h is not None \
            and st.trusted_pct_5h >= status.PCT_5H_BLOCK:
        # The carried figure counts here: inside a window usage only rises, so
        # a figure we carried is a floor and a floor at the wall is at the wall.
        # A reading we have no figure for at all still starts, deliberately -
        # the session we are about to launch is the only thing that can tell us
        # where the account stands.
        raise SystemExit(
            f"account '{acct}' is at {st.trusted_pct_5h}% of the 5h limit "
            f"(>= {status.PCT_5H_BLOCK}%) - refusing to start.\n"
            f"resets at {st.reset_5h}; use --force to override"
        )

    claude = shutil.which("claude")
    if not claude:
        raise SystemExit("cannot find claude on PATH")

    cfg = agents.read(agent)
    argv = [claude]
    model = cfg.get("model")
    if model:
        argv += ["--model", str(model)]
    # Work for another agent goes through `ocp ask`. A subagent does it where
    # nothing can see it: no row on the bridge, gone with the session, spent on
    # this account. The list is variadic, so an option must follow it - a
    # prompt there would be read as one more tool name.
    argv += ["--disallowedTools", "Agent"]
    # Always pass effort explicitly. Left unset it comes from whatever the
    # environment carries, which is how an agent ends up silently running at a
    # far more expensive level than intended.
    effort = str(cfg.get("effort") or agents.DEFAULT_EFFORT)
    if effort not in agents.VALID_EFFORT:
        raise SystemExit(
            f"agent '{agent}' has effort={effort!r}; must be one of "
            f"{', '.join(agents.VALID_EFFORT)}"
        )
    argv += ["--effort", effort]

    # The agent's standing context (team model section 1). base.md is injected
    # every session - that is what its 2KB budget is a budget for - and the
    # handoff is injected on resume so the agent picks up where it left off.
    preamble = context_for(agent)
    if preamble:
        argv += ["--append-system-prompt", preamble]
    argv += list(extra or [])

    return argv, accounts.env_for(acct, agent)


def context_for(agent: str) -> str:
    """ROLE.md + base.md + the latest handoff, as one system-prompt appendix."""
    d = paths.agent_dir(agent)
    parts = [f"You are `{agent}`, one agent in an orcapipe team. "
             f"Your name is also in $OCP_AGENT.",
             "Stay inside your own worktree. To read another branch's version of "
             "a file, use `git show <ref>:<path>` - it needs nothing outside the "
             "worktree. Copying a file out to /tmp to read it stops you on a "
             "permission prompt only a human can answer, and you will sit there "
             "until one does."]
    team_line = _your_team(agent)
    if team_line:
        parts.append(team_line)
    for fn, label in (("ROLE.md", "role"), ("base.md", "standing context")):
        p = d / fn
        if p.exists():
            text = p.read_text().strip()
            if text:
                parts.append(f"# Your {label} ({fn})\n{text}")
    handoff = d / "handoff" / "latest.md"
    if handoff.exists():
        text = handoff.read_text().strip()
        if text:
            parts.append(
                "# Where you left off (handoff/latest.md)\n"
                "You were interrupted and resumed. This is your own record.\n" + text
            )
    return "\n\n".join(parts)


def _your_team(agent: str) -> str:
    """Who this agent answers to and who answers to it, read from team.yaml.

    Generated rather than written into each ROLE.md: a role that named its
    colleagues by hand kept naming them after they were retired and renamed.
    """
    from . import actions  # actions -> daemon -> launch

    t = actions.routing()
    if t is None:
        return ""
    boss = actions.supervisor_of(t, agent) or actions.HUMAN
    reports = [n for n in paths.list_agents()
               if n != agent and actions.supervisor_of(t, n) == agent]
    lines = ["# Your team",
             f"You report to `{boss}`: `ocp ask {boss} \"...\"` when you finish, "
             f"or are blocked."]
    if reports:
        lines.append("Your reports: " + ", ".join(f"`{n}`" for n in reports) + ". "
                     "You do not implement - give one work with `ocp ask <name> "
                     "\"<task>\"`, and read what it sends back. One task at a "
                     "time; to correct one not yet delivered, `--replaces <id>` "
                     "or `ocp withdraw <id>`, not a message behind it.")
    lines.append("`ocp team` shows who you may ask and what each is doing now. "
                 "A decision only the founder can make: `ocp ask human`.")
    return "\n".join(lines)


def exec_agent(agent: str, extra: list[str] | None = None, force: bool = False) -> None:
    argv, env = build(agent, extra, force)
    _record_terminal(agent, env.get("OCP_ACCOUNT"))
    sys.stdout.flush()
    os.execve(argv[0], argv, env)


def _record_terminal(agent: str, acct: str | None = None) -> None:
    """Remember where we are about to become the agent: the terminal, and the account.

    Orca puts the handle in the environment of every terminal it launches, and
    this is the only moment we can see it - after execve we are the agent. The
    daemon needs it to clear the agent's context and hand it back its handoff.

    The account comes from the environment we are about to exec with, not from
    agent.yaml: those are the same value only when the preferred account had
    room, and the whole point of recording it is that the two can differ.
    """
    # Late, because `spawn` imports this module: the launcher is the bottom of
    # the stack and the starters sit on top of it.
    from . import db, orca, spawn

    handle = orca.terminal_handle()
    if not handle and not acct:
        return
    try:
        con = db.connect()
        try:
            if handle:
                db.set_terminal(con, agent, handle, os.environ.get("ORCA_WORKTREE_ID"))
                # However this terminal was made - by `ocp spawn`, or by a
                # human running `ocp launch` in one they opened - the agent
                # joins the team's Run here, so Orca draws it under whoever it
                # reports to instead of as one more tab.
                spawn.register_worker(con, agent, handle)
            if acct:
                db.set_account(con, agent, acct)
        finally:
            con.close()
    except Exception:  # noqa: BLE001 - never block a launch on bookkeeping
        pass


def command_for(agent: str) -> str:
    """The --command string to hand to Orca."""
    exe = os.environ.get("OCP_BIN") or shutil.which("ocp") or "ocp"
    return f"{exe} launch {agent}"
