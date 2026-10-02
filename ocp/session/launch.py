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
import re
import shlex
import shutil
import sys
import time
from pathlib import Path
from dataclasses import dataclass

from ..team import accounts, agents
from .. import db, vendors
from ..infra import gitrun, names, paths, sessiontoken, textsafe
from ..usage import status
from . import events, triggers


# `build` and `fingerprint` must name the same tools, or a change to one
# reads as a launch that never drifted to the other.
#
# Work for another agent goes through `ocp task new`, where the bridge can see
# it. Agent spawns a subagent that dies with the session, unseen; SendMessage
# reaches another agent's own session directly, bypassing the bridge the
# same way - a manager used exactly that tonight to resume two subordinates
# by hand instead of asking them. Both are denied for the same reason.
#
# ScheduleWakeup and CronCreate: a self-scheduled wakeup survives the park's
# /clear and fires into a session at the usage wall, and ocp types the answer
# into the session when it comes, so nothing an agent schedules is needed.
DISALLOWED_TOOLS = "Agent,SendMessage,ScheduleWakeup,CronCreate"


def find_room(agent: str) -> str | None:
    """The account that can take this agent's next session, or None: the best
    of the logged-in ones by `pick_account`, and failing that one nothing says
    is a bad place to start (`status.unfit`) - a stale or missing reading is
    the account nobody has run on lately, and the session about to start is the
    only thing that writes a fresh one.
    """
    from ..usage import load as loadmod
    load = loadmod.read_all()
    # Logged-in accounts only: `assignable` says nothing about credentials.
    # bootstrap: a never-run account is a real place to go.
    # live_refresh=True: this is the one caller actually about to place an
    # agent, not a screen asking what would happen - the only place worth the
    # network cost of asking a carried_old or idle-floored candidate fresh
    # before it decides the answer (#1811 part 2, #1816).
    candidates = [a for a in paths.list_accounts() if accounts.may_start(a)]
    picked = status.pick_account(candidates, bootstrap=True, load=load, agent=agent,
                                 live_refresh=True)[0]
    vendor = vendors.agent_vendor(agent)
    return picked or next((a for a in candidates if not status.not_for(a, vendor)
                           and status.unfit(status.read(a), load.get(a), agent) is None), None)


def choose_account(agent: str, resuming: bool = False, con=None,
                   account: str | None = None) -> str | None:
    """Which account this launch runs on, or None when there is none.

    The moment a session is made is the only moment its account can be chosen,
    since after execve the config directory is fixed.

    Resuming is not that moment. A session id belongs to one account's config
    directory, so a resumed session goes back to the account it was recorded
    on, or the run is not a resume at all.

    `account` is a destination someone already chose (a move): `build` still
    holds it to the vendor, login and wall checks.

    `con`, when the caller already has one open, saves `current_account` its
    own connect-and-migrate: a caller repainting a screen has this on every
    row already and would otherwise open a database per row to ask.
    """
    if resuming:
        return agents.current_account(agent, con)
    return account or find_room(agent)


@dataclass
class WakeWait:
    account: str
    pct: int
    # When the walled window rolls over: the shortest one, or the longest.
    reset: int | None
    # True when `account` is a guess (one written before a launch confirmed
    # it) rather than the account this resume is actually tied to -
    # `account_reading` prefers a fresher reported one first, so this is the
    # residual case where nothing has confirmed one at all.
    assumed: bool = False
    window: str = ""            # the walled window's length as a person says it
    is_long: bool = False       # the longest window, not the shortest


def _wall(acct: str, assumed: bool = False) -> WakeWait | None:
    """The window this account is walled behind, the shortest first: an account
    walled on both reports the shortest one's rollover, and once that passes it is
    refused again on the longest still standing - the sentence names a rollover that
    does not itself unblock it."""
    st = status.read(acct)
    pct, week = st.short_trusted, st.long_trusted
    if pct is not None and pct >= status.SHORT_BLOCK:
        return WakeWait(acct, pct, st.short.reset, assumed, window=st.short.label)
    if week is not None and week >= status.LONG_BLOCK:
        return WakeWait(acct, week, st.long.reset, assumed, window=st.long.label,
                        is_long=True)
    return None


def frees_first(agent: str) -> WakeWait | None:
    """For an agent no account has room for: the walled account whose wall
    lifts first, or None when none is walled (capped, unreadable, or none
    of the agent's vendor)."""
    walls = [w for a in paths.list_accounts()
             if accounts.may_start(a) and not status.not_for(a, vendors.agent_vendor(agent))
             for w in [_wall(a)] if w]
    return min(walls, key=lambda w: w.reset or float("inf"), default=None)


def no_room(agent: str) -> str:
    """Why no account can take this agent, naming the one that frees up first."""
    named = paths.list_accounts()
    if not any(accounts.may_start(a) for a in named):
        return ("no account is registered - `ocp account add <name>` first" if not named else
                "no account is logged in. A human has to do this once (ocp never "
                f"touches credentials):\n\n  {accounts.login_command(named[0])}\n")
    w = frees_first(agent)
    return ("no account can take one more agent"
            + (f" - {describe_wake_wait(w)}" if w else ""))


def wake_wait(agent: str, row, con) -> WakeWait | None:
    """Is a closed agent's wake walled behind its own account's 5h or weekly wall?

    This is the check ocp/daemon/ makes before it types anything into a closed
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

    A start with no session to resume goes wherever `choose_account` finds
    room; with none it waits on the account that frees up first
    (`frees_first`). The resume branch is pinned to the account its session is
    tied to and cannot follow an account change even when another account does
    have room - nothing upstream ever checks 7d for it, walled or not.

    A computed condition, not a stored one: called again a moment later, on an
    account whose window has since rolled over, it returns None.
    """
    if not row or not row["closed_at"]:
        return None
    if row["resume_session"]:
        reading = agents.account_reading(agent, con)
        return reading and _wall(reading.account, reading.source == "assumed")
    return None if choose_account(agent, resuming=False, con=con) else frees_first(agent)


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


def wait_clock(w: WakeWait, unknown: str = "?") -> str:
    """When the walled window rolls over: a clock for the shortest one, a date and
    clock for the longest, which is days away."""
    if not w.reset:
        return unknown
    return time.strftime("%m-%d %H:%M" if w.is_long else "%H:%M", time.localtime(w.reset))


def describe_wake_wait(w: WakeWait) -> str:
    """The wording for a wait already computed, so a caller that holds one
    does not recompute it (the window may have rolled over in between)."""
    when = wait_clock(w, "unknown")
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


def main_ref_files(worktree: Path) -> list[str]:
    """The files git rewrites to move the repository's main branch, which a session may not write: its
    ref, the packed refs, and the lock files beside them (a ref moves by writing `<ref>.lock` and
    renaming it over the ref). The sandbox lets a session write the repository's .git for its own
    commits; this keeps the daemon the only mover of main. Branches are untouched; deleting a packed
    branch rewrites packed-refs, so that is refused too and left to the daemon."""
    common = _git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not common:
        return []
    ref = Path(common) / "refs" / "heads" / gitrun.INTEGRATION_BRANCH
    packed = Path(common) / "packed-refs"
    return [str(p) for p in (ref, Path(f"{ref}.lock"), packed, Path(f"{packed}.lock"))]


def hidden_memories(agent: str) -> list[str]:
    """The memory folders `agent` may not read: every other agent's, with no
    exception for its reports - a memory is that agent's own."""
    return [str(paths.agent_dir(a) / "memory") for a in paths.list_agents() if a != agent]


def _git(worktree: Path, *args: str) -> str:
    rc, out, _ = gitrun.run(worktree, *args)
    return out.strip() if rc == 0 else ""


def trusted_environment(worktree: Path | None) -> list[str]:
    """What the permission classifier should count as the team's own, as the
    prose it reads: the state folder, this worktree and its repository, and the
    GitHub repos its remotes name. Anything else stays outside the boundary."""
    lines = [f"The team's state folder {paths.home()} and everything under it is the team's own: "
             f"reading and writing it, and running `ocp`, is routine."]
    if worktree is None:
        return lines
    lines.append(f"This agent's own git worktree is {worktree}: `cd`, editing files, `sed -i`, "
                 f"running tests and committing inside it are routine.")
    common = _git(worktree, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if common:
        lines.append(f"Its repository is {Path(common).parent}, and the sibling worktrees under "
                     f"{worktree.parent} are other agents of the same team.")
    repos = sorted({m.group(1) for line in _git(worktree, "remote", "-v").splitlines()
                    if (m := re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?\s", line + " "))})
    if repos:
        lines.append("Source control: the GitHub repos " + ", ".join(f"github.com/{r}" for r in repos)
                     + " and their branches.")
    return lines


def hidden_transcripts(agent: str, drv) -> list[str]:
    """The transcript folders `agent` may not read (T40): every other agent's
    except its own and its reports', in every account of this vendor, since
    an agent moves between accounts. Only agents with a worktree on record
    can be named, so one that has not started yet has nothing to hide."""
    from .. import actions  # late: actions import this module
    table = actions.routing()
    con = db.connect()
    try:
        rows = db.all_agent_states(con)
    finally:
        con.close()
    configs = [vendors.config_dir(a) for a in paths.list_accounts()
               if vendors.account_vendor(a) == drv.name]
    out: list[str] = []
    for r in rows:
        other = r["agent"]
        if other == agent or actions.supervisor_of(table, other) == agent or not r["worktree"]:
            continue
        for cfg in configs:
            d = drv.transcript_dir(cfg, r["worktree"].split("::", 1)[-1])
            if d is not None:
                out.append(str(d))
    return out


def build(agent: str, extra: list[str] | None = None, force: bool = False,
          account: str | None = None) -> tuple[list[str], dict]:
    """Return (argv, env) ready to hand to execve."""
    try:
        drv = vendors.for_agent(agent)
    except vendors.UnknownVendor as e:
        raise SystemExit(f"agent '{agent}': {e}") from None
    resuming = drv.is_resume(extra)
    acct = choose_account(agent, resuming=resuming, account=account)
    if acct is None:
        raise SystemExit(f"agent '{agent}' has no recorded session to resume" if resuming
                         else f"agent '{agent}': {no_room(agent)}")
    # The last line before an exec: whatever chose this account, a session
    # is this vendor's binary pointed at that account's credentials.
    theirs = vendors.account_vendor(acct)
    if theirs != drv.name:
        raise SystemExit(f"agent '{agent}' is a {drv.name} agent and account '{acct}' "
                         f"is a {theirs} account - refusing to start it there.")

    hooks = drv.state_hooks()
    if hooks:
        try:
            hooks.ensure_shared()  # settings ocp keeps, e.g. notifications off
        except ValueError:
            pass  # an unreadable settings file is doctor's to report, not a reason not to start
        else:
            # A wanted event added after the shared settings were last installed
            # (T247's PreToolUse guard did this) would otherwise sit uninstalled
            # forever, silently: every launch is where install() gets the chance
            # to catch up, not just the one-off `ocp init`/`ocp install-hooks`.
            # Only reached once ensure_shared has confirmed the file is a
            # readable JSON object - install() does not check that itself.
            wanted = events.CAPTURED | events.ANSWERS | events.GUARDS
            if hooks.missing(wanted):
                try:
                    hooks.install(paths.ocp_exe(), wanted)
                except OSError as e:
                    # `ocp doctor` is the backstop for whoever is not watching
                    # stderr here - an agent's own sandbox denies this write by
                    # design, so it cannot disable its own gate hooks.
                    print(f"warning: ocp's own hooks are behind and could not be installed "
                          f"({e}) - run `ocp install-hooks` from outside an agent's sandbox",
                          file=sys.stderr)
            try:
                hooks.allow_home_socket()  # a home initialised before `ocp init` added it
            except OSError as e:
                print(f"warning: agents' sandbox cannot reach ocp's socket and it could not be "
                      f"allowed ({e}) - run `ocp init` from outside an agent's sandbox",
                      file=sys.stderr)
    if not accounts.may_start(acct):
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
    elif not force and st.exists and st.short_trusted is not None \
            and st.short_trusted >= status.SHORT_BLOCK:
        # The carried figure counts here: inside a window usage only rises, so
        # a figure we carried is a floor and a floor at the wall is at the wall.
        # A reading we have no figure for at all still starts, deliberately -
        # the session we are about to launch is the only thing that can tell us
        # where the account stands.
        raise SystemExit(
            f"account '{acct}' is at {st.short_trusted}% of the {st.short.label} limit "
            f"(>= {status.SHORT_BLOCK}%) - refusing to start.\n"
            f"resets at {st.short.reset}; use --force to override"
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
    preamble = context_for(agent)
    memory_dir = paths.agent_dir(agent) / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    # A vendor with no auto mode (auto_mode_argv() empty, e.g. opencode) or an
    # agent whose skip-permissions is in effect never reads `trusted`, so the
    # git calls that build it are skipped rather than run and thrown away.
    mode = ([] if agents.skip_permissions(cfg) and agents.skip_permissions_granted(agent)
            else drv.auto_mode_argv())
    argv = drv.launch_argv(exe, cfg.get("model"), effort, DISALLOWED_TOOLS,
                           mcp_arg + agents.mcp_config_args(cfg), preamble,
                           hide=hidden_transcripts(agent, drv) + hidden_memories(agent),
                           no_write=main_ref_files(Path.cwd()),
                           memory_dir=memory_dir,
                           trusted=trusted_environment(Path.cwd()) if mode else None,
                           chrome=agents.chrome(cfg))
    # The pair goes whole or not at all: a caller's own mode wins, and a lone
    # value left behind would be read as the opening prompt.
    if mode and not any(x == mode[0] or x.startswith(mode[0] + "=") for x in extra or []):
        argv += mode
    if agents.skip_permissions(cfg) and not agents.skip_permissions_granted(agent):
        print(f"warning: agent '{agent}' has skip_permissions in its agent.yaml, but the "
              f"founder never turned it on (`ocp agent register {agent} --skip-permissions on`, "
              f"from a terminal) - ignoring it", file=sys.stderr)
    elif agents.skip_permissions(cfg):
        flags = drv.skip_permissions_argv()
        if not flags:
            print(f"warning: agent '{agent}' has skip_permissions on, but {drv.name} "
                  f"has no such switch - starting it as usual", file=sys.stderr)
        argv += [f for f in flags if f not in (extra or [])]
    argv += list(extra or [])

    env = accounts.env_for(acct, agent)
    env.update(drv.launch_env(preamble, DISALLOWED_TOOLS, paths.agent_dir(agent)))
    return argv, env


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
             "until one does.",
             "On a long request, keep `ocp progress \"done: ... left: ...\"` current: "
             "your handoff carries it to the session that takes over."]
    team_line = _your_team(agent)
    if team_line:
        parts.append(team_line)
    for fn, label in (("ROLE.md", "role"), ("base.md", "standing context")):
        p = d / fn
        if p.exists():
            text = p.read_text().strip()
            if text:
                parts.append(f"# Your {label} ({fn})\n{text}")
    parts.extend(_team_rules(agent))
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


def _team_rules(agent: str) -> list[str]:
    """The shared/team/<team>.md of each team this agent is in, beside base.md."""
    from .. import actions  # actions -> daemon -> launch
    from ..team import memory

    t = actions.routing()
    return [f"# Rules for team {team}\n{text}"
            for team, text in memory.team_layers(agent, t.teams if t else {})]


def _your_team(agent: str) -> str:
    """Who this agent answers to and who answers to it, read from team.yaml.

    Generated rather than written into each ROLE.md: a role that named its
    colleagues by hand kept naming them after they were retired and renamed.
    """
    from .. import actions  # actions -> daemon -> launch

    t = actions.routing()
    if t is None:
        return ""
    boss = actions.supervisor_of(t, agent) or actions.HUMAN
    reports = [n for n in paths.list_agents()
               if n != agent and actions.supervisor_of(t, n) == agent]
    report = (f"You report to `{boss}`. A task you were handed ends with `ocp task done T<n> "
              f"\"<evidence>\"` or `ocp task handoff T<n> <agent> \"<note>\"` - that is your "
              f"report, even a deferral: the next task arrives only then, and an answer typed "
              f"in chat reaches no one.")
    if boss != actions.HUMAN:
        # A decision for the human is a task of theirs, so it is named once,
        # below, for the decisions that are theirs.
        report += (f" `ocp say {boss} \"...\"` is a message that needs no answer. Being "
                   f"blocked, a decision only they can make, or work nobody handed you is a task "
                   f"for them: `ocp task new --owner {boss} -t <time> \"<title>\" \"<body>\"`.")
    lines = ["# Your team", report,
             "Talk to people only through ocp; what you write in your terminal reaches no one. "
             "Keep it short. Reply when a message asks you something or you owe a result; a "
             "message that only informs needs no reply."]
    if reports:
        lines.append("Your reports: " + ", ".join(f"`{n}`" for n in reports) + ". "
                     "You do not implement - give one work with `ocp task new --owner <name> "
                     "-t <time> \"<title>\" \"<what to do, and what done looks like>\"`, and read "
                     "what it sends back. A message to a report is `ocp say <name> ... --task "
                     "T<n>` (a memo on that task). "
                     "One task at a time; correct one with `ocp task memo T<n> add \"...\"`, not a "
                     "message behind it, and throw a stale one away with `ocp task discard T<n> "
                     "\"<why>\"`. Record a wait as `--blocked-by T<n>` on the task, not in "
                     "prose. Check main before you assign, so nobody rebuilds what landed. Quote "
                     "the founder's own words in the task you open for their request, and answer "
                     "the founder through `ocp`, never in chat.")
    lines.append("`ocp team` shows who you may reach and what each is doing now. "
                 "A decision or a block only the founder can clear is a task of theirs: "
                 "`ocp task new --owner human -t <time> \"<title>\" \"<body>\"`.")
    # A self-scheduled wakeup survives the park's /clear and fires into a
    # session that is at the usage wall.
    lines.append("Never schedule your own wakeup, /loop or poll to wait for a reply "
                 "or a review: ocp types the answer into your session when it comes.")
    return "\n".join(lines)


def fingerprint(agent: str) -> str:
    """What `build()` would launch this agent with right now, boiled down to
    the pieces a running session cannot pick up on its own: the model, the
    effort level, the disallowed-tools list, the shared mcp.json, whether
    skip-permissions is in effect (T448: a session started without the flag
    kept running as if the setting were still off), and the static preamble (team line, ROLE.md, base.md, the rules file of each
    team it is in). Never the account or the
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
    if agents.skip_permissions_in_effect(agent, cfg):
        material += "\x1eskip-permissions"     # only when on: turning it off returns to the old hash
    if agents.mcp_config_args(cfg):
        material += "\x1emcp:" + agents.mcp_config_args(cfg)[1]   # only when set, like chrome
    if agents.chrome(cfg):
        material += "\x1echrome"                # only when on, like skip-permissions
    return hashlib.sha256(material.encode()).hexdigest()[:16]


def drifted(agent: str, row) -> bool:
    """Was this agent's running session launched with a different recipe than
    `build()` produces now? See `fingerprint`."""
    return fingerprint(agent) != row["launch_fingerprint"]


def take_prompt(path: str | None) -> str | None:
    """Read and remove a prompt file the daemon left for this launch.

    Taken only after `build` has let the launch go ahead: a launch that
    refuses leaves the file, which is how the daemon tells its own session
    from one somebody else started (`daemon.wait_for_session`). Removed as it
    is read, so no later launch picks it up: a prompt belongs to one launch.
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
               prompt_file: str | None = None, account: str | None = None) -> None:
    argv, env = build(agent, list(extra or []), force, account)
    prompt = take_prompt(prompt_file)
    if prompt:
        argv += vendors.for_agent(agent).prompt_argv(prompt)
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
    sessiontoken.record(agent)             # the proof this session is the agent's, for the daemon's land verbs
    env = names.both(env)
    _record_terminal(agent, env.get("OCP_ACCOUNT"), fingerprint(agent))
    gitrun.point_origin_head_at_main(Path.cwd())        # T935: the session names its main branch from it
    sys.stdout.flush()
    os.execve(argv[0], argv, env)


def _record_terminal(agent: str, acct: str | None = None, fp: str | None = None) -> None:
    """Remember where we are about to become the agent: the terminal, the
    account, and what `build()` produced this launch with.

    Orca puts the handle in the environment of every terminal it launches, and
    this is the only moment we can see it - after execve we are the agent. The
    daemon needs it to clear the agent's context and hand it back its handoff.

    The account comes from the environment we are about to exec with.

    `fp` is the fingerprint `exec_agent` computed just before this ran; see
    `fingerprint`.
    """
    # Late, because `spawn` imports this module: the launcher is the bottom of
    # the stack and the starters sit on top of it.
    from .. import db
    from . import orca
    from ..team import spawn

    handle, wt = orca.terminal_handle(), orca.worktree_id()
    # The other door into the same two columns `spawn` writes, and the values
    # come in no cleaner for arriving as environment rather than as JSON. Both
    # are identifiers, read back and named in messages long after this - so one
    # carrying a control byte is refused here, not rewritten, and the reader is
    # left with what it already handles: no handle, and no worktree.
    if handle and textsafe.has_unsafe(handle):
        handle = None
    if wt and textsafe.has_unsafe(wt):
        wt = None
    if not handle and not acct:
        return
    try:
        con = db.connect()
        try:
            if handle:
                db.set_terminal(con, agent, handle, wt)
                # However this terminal was made - by `ocp spawn`, or by a
                # human running `ocp launch` in one they opened - the agent
                # joins the team's Run here, so Orca draws it under whoever it
                # reports to instead of as one more tab.
                spawn.register_worker(con, agent, handle)
                db.set_launch_fingerprint(con, agent, fp)
            if acct:
                db.set_account(con, agent, acct)
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001 - never block a launch on bookkeeping
        # Not silent: with no fingerprint on record the daemon relaunches this
        # session at every wake, and nothing else would say why.
        print(f"ocp launch: could not record this launch for {agent}: {e}",
              file=sys.stderr)


def command_for(agent: str, account: str | None = None) -> str:
    """The --command string to hand to Orca; `account` fixes where a move lands."""
    exe = names.getenv("OCP_BIN") or shutil.which("handline") or shutil.which("ocp") or "ocp"
    return f"{exe} launch {agent}" + (f" --account {shlex.quote(account)}" if account else "")
