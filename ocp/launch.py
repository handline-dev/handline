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

import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path
from dataclasses import dataclass

from . import accounts, agents, db, paths, status, triggers, vendors


# `build` and `fingerprint` must name the same tools, or a change to one
# reads as a launch that never drifted to the other.
#
# Work for another agent goes through `ocp ask`, where the bridge can see
# it. Agent spawns a subagent that dies with the session, unseen; SendMessage
# reaches another agent's own session directly, bypassing the bridge the
# same way - a manager used exactly that tonight to resume two subordinates
# by hand instead of asking them. Both are denied for the same reason.
#
# ScheduleWakeup and CronCreate: a self-scheduled wakeup survives the park's
# /clear and fires into a session at the usage wall, and ocp types the answer
# into the session when it comes, so nothing an agent schedules is needed.
DISALLOWED_TOOLS = "Agent,SendMessage,ScheduleWakeup,CronCreate"


def find_room(agent: str, exclude: dict[str, str] | None = None) -> str | None:
    """An account that can take this agent's next session, or None.

    The preferred account unless what is known says it is a bad place to start
    (`status.unfit`), else the best of the others by `pick_account`. `exclude`
    names accounts to skip and why - the one the agent just left.
    """
    preferred = agents.account_of(agent)
    from . import load as loadmod
    load = loadmod.read_all()
    if not (exclude and preferred in exclude) \
            and status.unfit(status.read(preferred), load.get(preferred), agent) is None:
        return preferred
    # Logged-in accounts only: `assignable` says nothing about credentials.
    # bootstrap: a never-run account is a real place to go. `preferred` is out:
    # it is what was just found unfit.
    candidates = [a for a in paths.list_accounts() if accounts.logged_in(a) and a != preferred]
    return status.pick_account(candidates, bootstrap=True, exclude=exclude, load=load,
                               agent=agent)[0]


def choose_account(agent: str, resuming: bool = False, con=None) -> str:
    """Which account this launch runs on.

    agent.yaml names a preference, not a destination: the moment a session is
    made is the only moment its account can be chosen, since after execve the
    config directory is fixed.

    Resuming is not that moment. A session id belongs to one account's config
    directory, so a resumed session goes back to the account it was recorded
    on, or the run is not a resume at all.

    `con`, when the caller already has one open, saves `current_account` its
    own connect-and-migrate: a caller repainting a screen has this on every
    row already and would otherwise open a database per row to ask.
    """
    if resuming:
        return agents.current_account(agent, con)
    # Nothing with room is not a reason to pick at random: `build` refuses on
    # a current reading at the wall, and names the account and the figure.
    return find_room(agent) or agents.account_of(agent)


@dataclass
class WakeWait:
    account: str
    pct: int
    # When the walled window rolls over: the 5h one, or the weekly one.
    reset: int | None
    # True when `account` is a guess (agent.yaml's preference, or a spawn's
    # own unconfirmed one) rather than the account this resume is actually
    # tied to - `account_reading` prefers a fresher reported one first, so
    # this is the residual case where nothing has confirmed one at all.
    assumed: bool = False
    window: str = "5h"


def wake_wait(agent: str, row, con) -> WakeWait | None:
    """Is a closed agent's wake walled behind its own account's 5h or weekly wall?

    This is the check daemon.py makes before it types anything into a closed
    agent's terminal. A resume cannot follow an account change, but the
    pinning rescue can start fresh elsewhere, so a wait is not final. It is factored out here, rather than
    inlined at the call site, so the screens that show an agent can ask the
    same question instead of restating the rule and drifting from it.

    It answers only "is the account full", not "is anyone trying to wake this
    agent" - the daemon's own call site (a move's wake included) needs the
    former unconditionally, before it spends a wake attempt finding out the
    hard way. A screen that means to show only the waits with something behind
    them gates on `daemon.would_wake` first; this makes no such promise on its
    own.

    The move branch already goes through `choose_account` -> `find_room` ->
    `status.unfit`, which refuses a walled account on both windows before
    this is ever reached - the 7d check here is belt and braces for it. The
    resume branch is the one this actually protects: it is pinned to the
    account its session is tied to, cannot follow an account change, and
    nothing upstream has refused that account on 7d - without this, a resume
    wakes an agent straight into the weekly wall.

    A computed condition, not a stored one: called again a moment later, on an
    account whose window has since rolled over, it returns None.
    """
    if not row or not row["closed_at"]:
        return None
    if row["resume_session"]:
        reading = agents.account_reading(agent, con)
        acct, assumed = reading.account, reading.source == "assumed"
    else:
        # The move's wake: nowhere its own session was tied to, so there is
        # no reading to prefer over a fresh choice - `account_reading`
        # answers a question this branch does not ask.
        acct, assumed = choose_account(agent, resuming=False, con=con), False
    st = status.read(acct)
    pct, week = st.trusted_pct_5h, st.trusted_pct_7d
    if pct is not None and pct >= status.PCT_5H_BLOCK:
        return WakeWait(acct, pct, st.reset_5h, assumed)
    if week is not None and week >= status.PCT_7D_BLOCK:
        return WakeWait(acct, week, st.reset_7d, assumed, window="7d")
    return None


def wake_wait_reason(agent: str, row, con) -> str | None:
    """The sentence ps, triggers and the daemon's own log line print for a
    walled wake.

    One wording, called from all three, so a screen can never say something
    the daemon does not act on. The bridge draws the same `wake_wait` but
    fits it to a fixed column instead of this sentence - it has no room for
    the whole thing, not a different opinion of it.
    """
    w = wake_wait(agent, row, con)
    return None if w is None else describe_wake_wait(w)


def describe_wake_wait(w: WakeWait) -> str:
    """The wording for a wait already computed, so a caller that holds one
    does not recompute it (the window may have rolled over in between)."""
    fmt = "%H:%M" if w.window == "5h" else "%m-%d %H:%M"
    when = "unknown" if not w.reset else time.strftime(fmt, time.localtime(w.reset))
    who = f"assumed account {w.account}" if w.assumed else w.account
    return f"waiting for {who}'s {w.window} window ({w.pct}%, rolls over {when})"


def _mcp_config_material() -> str:
    """What `fingerprint` hashes for the shared mcp.json: whether a launch
    would pass `--mcp-config` at all, and if so, the file's content - so
    editing it in place (fixing an invalid file, or adding a server) reads as
    drift even though the path never changes. Missing and invalid hash the
    same: neither puts anything on the argv, so replacing one broken file
    with another must not read as drift either.
    """
    argv, _ = mcp_config_arg()
    if not argv:
        return "absent"
    try:
        return hashlib.sha256(paths.mcp_config().read_bytes()).hexdigest()
    except OSError:
        return "absent"


def mcp_config_arg() -> tuple[list[str], str | None]:
    """`--mcp-config <file>` for `paths.mcp_config()`, if it exists and is a
    valid {"mcpServers": {...}} object.

    Not strict: passing this leaves an account's own servers loading too. A
    missing file changes nothing - the founder has not filled it in, or an
    agent needs none. An invalid one must not stop the launch (a typo in a
    shared file would otherwise wall off every agent), so the caller only
    warns; `ocp doctor` has the same check for when nobody is watching a
    launch's stderr.
    """
    p = paths.mcp_config()
    if not p.exists():
        return [], None
    try:
        data = json.loads(p.read_text())
    except (OSError, ValueError) as e:
        return [], f"{p} is invalid ({e})"
    # Well-formed JSON that is not claude's {"mcpServers": {...}} shape still
    # makes `claude` exit before the session starts - the exact wall-off this
    # skip exists to avoid. Server entries themselves need no checking: claude
    # accepts and rejects those the same way with or without this guard.
    if not (isinstance(data, dict) and isinstance(data.get("mcpServers"), dict)):
        return [], f"{p} is invalid (not a {{\"mcpServers\": {{...}}}} object)"
    return ["--mcp-config", str(p)], None


def build(agent: str, extra: list[str] | None = None, force: bool = False) -> tuple[list[str], dict]:
    """Return (argv, env) ready to hand to execve."""
    try:
        drv = vendors.for_agent(agent)
    except vendors.UnknownVendor as e:
        raise SystemExit(f"agent '{agent}': {e}") from None
    acct = choose_account(agent, resuming=drv.is_resume(extra))

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
    # Staleness still gates *assignment*, in status.assignable(): a stale
    # reading only lets the daemon assign there when no agent of ours is
    # already mid-turn on it, its window has not rolled over, and it is a
    # floor clear of the wall by a margin (status.idle_floor) - anything else
    # still refuses. And an account that really is full self-corrects within a
    # tick - the daemon asks the endpoint about an account a turn has just
    # happened on, and its trigger then hands off on fresh data.
    st = status.read(acct)
    if st.exists and st.stale:
        age = "unknown age" if st.age_s is None else f"{int(st.age_s)}s"
        print(f"warning: account '{acct}' usage is {age} old and may be out of date; "
              f"the daemon will ask the endpoint about it on its next tick.",
              file=sys.stderr)
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

    exe = drv.binary()
    if not exe:
        raise SystemExit(f"cannot find {drv.name} on PATH")

    cfg = agents.read(agent)
    # Always pass effort explicitly. Left unset it comes from whatever the
    # environment carries, which is how an agent ends up silently running at a
    # far more expensive level than intended.
    effort = str(cfg.get("effort") or agents.DEFAULT_EFFORT)
    if effort not in agents.VALID_EFFORT:
        raise SystemExit(
            f"agent '{agent}' has effort={effort!r}; must be one of "
            f"{', '.join(agents.VALID_EFFORT)}"
        )

    mcp_arg, mcp_err = mcp_config_arg()
    if mcp_err:
        print(f"warning: shared mcp config {mcp_err}, skipping --mcp-config",
              file=sys.stderr)

    # The agent's standing context (team model section 1). base.md is injected
    # every session - that is what its 2KB budget is a budget for - and the
    # handoff waiting for it is injected so it picks up where it left off.
    argv = drv.launch_argv(exe, cfg.get("model"), effort, DISALLOWED_TOOLS, mcp_arg,
                           context_for(agent))
    argv += list(extra or [])

    return argv, accounts.env_for(acct, agent)


def _static_preamble(agent: str) -> str:
    """The part of `context_for` that a rebuild reproduces byte for byte:
    everything except the handoff, which is supposed to differ every time
    this agent is parked; `fingerprint` hashes only this part.
    """
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
    return "\n\n".join(parts)


def context_for(agent: str) -> str:
    """ROLE.md + base.md + the handoff waiting for it, as one system-prompt appendix."""
    parts = [_static_preamble(agent)]
    text = _waiting_handoff(agent)
    if text:
        parts.append("# Where you left off (your handoff)\n"
                     "You were interrupted and resumed. This is your own record.\n"
                     + text.strip())
    return "\n\n".join(p for p in parts if p)


def _waiting_handoff(agent: str) -> str | None:
    con = db.connect()
    try:
        return db.get_handoff(con, agent)
    finally:
        con.close()


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
    report = (f"You report to `{boss}`. A task you were handed ends with `ocp resolve <id> "
              f"\"<report>\"` (or dismiss) - that is your report, even a deferral: the next "
              f"task arrives only then, and an answer typed in chat reaches no one.")
    if boss != actions.HUMAN:
        # An ask to the human is a row the founder has to clear, so it is named
        # once, below, for the decisions that are theirs.
        report += (f" `ocp ask {boss} \"...\"` is for being blocked, a decision only "
                   f"they can make, or work nobody handed you.")
    lines = ["# Your team", report]
    if reports:
        lines.append("Your reports: " + ", ".join(f"`{n}`" for n in reports) + ". "
                     "You do not implement - give one work with `ocp ask <name> "
                     "\"<task>\"`, and read what it sends back. One task at a "
                     "time; to correct one not yet delivered, `--replaces <id>` "
                     "or `ocp withdraw <id>`, not a message behind it.")
    lines.append("`ocp team` shows who you may ask and what each is doing now. "
                 "A decision or a block only the founder can clear: `ocp ask human`.")
    # A self-scheduled wakeup survives the park's /clear and fires into a
    # session that is at the usage wall.
    lines.append("Never schedule your own wakeup, /loop or poll to wait for a reply "
                 "or a review: ocp types the answer into your session when it comes.")
    return "\n".join(lines)


def fingerprint(agent: str) -> str:
    """What `build()` would launch this agent with right now, boiled down to
    the pieces a running session cannot pick up on its own: the model, the
    effort level, the disallowed-tools list, the shared mcp.json, and the
    static preamble (team line, ROLE.md, base.md). Never the account or the
    handoff - the account is chosen fresh on every real launch regardless of
    what last time picked, and the handoff is supposed to be different every
    time this agent is parked, so hashing either would call a session stale
    for a reason that has nothing to do with what it was launched with.

    Recorded at launch time (`_record_terminal`) and compared by `drifted`
    before typing a handoff into a session that is still open: the two agree
    only when nothing `build` would produce has changed since that session
    started. A row with nothing recorded disagrees too - nothing has confirmed
    that session against the recipe.
    """
    cfg = agents.read(agent)
    model = str(cfg.get("model") or "")
    effort = str(cfg.get("effort") or agents.DEFAULT_EFFORT)
    material = "\x1e".join([model, effort, DISALLOWED_TOOLS, _mcp_config_material(),
                            _static_preamble(agent)])
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def drifted(agent: str, row) -> bool:
    """Was this agent's running session launched with a different recipe than
    `build()` produces now? See `fingerprint`."""
    return fingerprint(agent) != row["launch_fingerprint"]


def take_prompt(path: str | None) -> str | None:
    """Read and remove a prompt file the daemon left for this launch.

    Removed as it is read, so a launch that dies before this point leaves a
    file no later launch will pick up: a prompt belongs to one launch.
    """
    if not path:
        return None
    p = Path(path)
    try:
        text = p.read_text()
        p.unlink()
    except OSError:
        return None
    return text or None


def exec_agent(agent: str, extra: list[str] | None = None, force: bool = False,
               prompt_file: str | None = None) -> None:
    prompt = take_prompt(prompt_file)
    argv, env = build(agent, list(extra or []), force)
    if prompt:
        argv.append(prompt)
    if not prompt_file:
        # A launch nobody is waiting on consumed the handoff `build` put in the
        # system prompt, so a record still on the row means one nobody was
        # handed. The daemon's own wake passes a prompt file and clears the row
        # itself once the session has started, because a launch that dies must
        # leave it for the fallback launch.
        con = db.connect()
        try:
            db.set_handoff(con, agent, None)
        finally:
            con.close()
    _record_terminal(agent, env.get("OCP_ACCOUNT"), fingerprint(agent))
    sys.stdout.flush()
    os.execve(argv[0], argv, env)


def _record_terminal(agent: str, acct: str | None = None, fp: str | None = None) -> None:
    """Remember where we are about to become the agent: the terminal, the
    account, and what `build()` produced this launch with.

    Orca puts the handle in the environment of every terminal it launches, and
    this is the only moment we can see it - after execve we are the agent. The
    daemon needs it to clear the agent's context and hand it back its handoff.

    The account comes from the environment we are about to exec with, not from
    agent.yaml: those are the same value only when the preferred account had
    room, and the whole point of recording it is that the two can differ.

    `fp` is the fingerprint `exec_agent` computed just before this ran; see
    `fingerprint`.
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
                db.set_launch_fingerprint(con, agent, fp)
            if acct:
                # Every path that starts a session passes through here, so
                # this is the one place a landing account differing from the
                # last one is ever seen - and the one place to carry what
                # that last session had learned along with it, before
                # `set_account` overwrites the record. Read the row directly
                # rather than through `current_account`: an agent that has
                # never run has no account to migrate anything from, and its
                # preference is not one just because nothing else is on
                # record.
                prev_row = db.get_agent_state(con, agent)
                previous = prev_row["account"] if prev_row else None
                if previous and previous != acct:
                    repo = agents.repo_of(agent)
                    project = accounts.memory_project_dir(repo) if repo else None
                    try:
                        carried = accounts.migrate_memory(previous, acct, project=project)
                        db.add_event(con, agent, "memory_migrated",
                                     {"from": previous, "to": acct, "carried": carried})
                    except Exception as e:  # noqa: BLE001 - record it, keep launching
                        db.add_event(con, agent, "memory_migrated",
                                     {"from": previous, "to": acct,
                                      "error": f"{type(e).__name__}: {e}"})
                db.set_account(con, agent, acct)
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001 - never block a launch on bookkeeping
        # Not silent: with no fingerprint on record the daemon relaunches this
        # session at every wake, and nothing else would say why.
        print(f"ocp launch: could not record this launch for {agent}: {e}",
              file=sys.stderr)


def command_for(agent: str) -> str:
    """The --command string to hand to Orca."""
    exe = os.environ.get("OCP_BIN") or shutil.which("ocp") or "ocp"
    return f"{exe} launch {agent}"
