"""ocp guide <topic> - load detail on demand (KICKOFF rule 3, section 10).

No logic lives in SKILL.md. Agents read a short stub and pull the detail from
here only when they actually need it, so it costs no tokens the rest of the time.
"""
from __future__ import annotations

from . import paths

TOPICS = {
    "accounts": """accounts
  ocp account register <name>      create the directory and link it to shared
  <human, once>  CLAUDE_CONFIG_DIR=~/.orcapipe/accounts/<name>/.claude claude /login
  ocp account list                 usage and whether the account is assignable

ocp never reads, writes or copies credentials. A refresh token rotates on
single use, so two copies of one credential invalidate each other.""",
    "launch": """launching agents
  ocp launch <agent>               exec claude as that agent's account
  ocp command <agent>              print the --command string for Orca

From Orca:
  orca terminal create --worktree path:<wt> --command "ocp launch backend"

direnv is not used. Orca leaves CLAUDE_CONFIG_DIR empty and --command takes an
arbitrary shell string, so one wrapper is enough - and Orca preserves the launch
command across hibernation and restore.""",
    "status": """usage and fail-closed
  ocp status                       which account may take new work
  ocp account list --json          machine-readable

A pct_5h we cannot trust - no snapshot, unreadable, or too old - is not a full
tank, it is a refusal. Reading it as 0 makes the 90% trigger false forever: the
feature is dead while the dashboard stays green.

A window nobody has spent in is different. Claude Code sends no five_hour block
for a session that has made no request since the window rolled over, so a live
collector reporting nothing is the account telling us it is quiet: 0%, marked
`*`. `~` is a figure carried from an earlier payload, `!` one carried past the
age we will assign on - still a floor for triggers, no new work.

Design section 4 has the five cases and why only one of them is fail-closed.""",
    "work": """giving an agent work
  ocp ask <agent> "<task>"        queue it
  ocp inbox                       what is waiting for you
  ocp resolve <id> "<verdict>"    close one

An interactive shell with no OCP_AGENT belongs to a human, so `ocp ask` typed at
your own prompt comes from orchestrator. A process that merely lost the variable
has no terminal, and is refused rather than handed the orchestrator's name.

That is the whole of it. The daemon delivers: an idle agent is told on the next
tick, and a closed one is resumed first and then told. Nobody has to nudge a
terminal, and an agent is told once per request rather than reminded.

An agent that is mid-turn is left alone until it is free - typing at a busy TUI
leaves the text sitting in its input box, unsent.""",

    "memory": """closing an idle session
  ocp close <agent>                end the session, keep the terminal
  ocp resume <agent>               put it back, resuming the same session

A session that is not being used holds 250-500MB and spends no tokens at all.
Closing it ends the agent process; the terminal, its worktree and its account
stay, because Orca runs our launch command inside a shell and claude is that
shell's child. Waking types the launch command back into the same shell with
`--resume <session>`, so the account comes back with it and the conversation is
the one it had.

This is not a handoff. Nothing is folded and no context is reclaimed, because
there is nothing wrong with an idle agent's context. The daemon does it by
itself after 30 minutes of silence, unless the agent is working, mid-handoff,
STALLED, or has a request waiting.""",

    "hooks": """Orca hooks
  ocp hooks status                 show drift
  ocp hooks sync                   apply once
  ocp hooks sync --watch           watch and auto-apply

Orca integrates with Claude Code through hooks in ~/.claude/settings.json. If a
per-account CLAUDE_CONFIG_DIR lacks those hooks, our agents disappear from the
Orca dashboard.""",
    "doctor": """checks
  ocp doctor                       verify the preconditions of each phase

Exits 1 if anything failed. No phase is finished without it.""",
}


def show(topic: str | None) -> int:
    if not topic:
        print("topics:")
        for t in sorted(TOPICS):
            print(f"  {t:<10} {TOPICS[t].splitlines()[0]}")
        print(f"\ndesign docs: {paths.repo_root() / 'design'}")
        return 0
    if topic not in TOPICS:
        print(f"no such topic: {topic}\navailable: {', '.join(sorted(TOPICS))}")
        return 1
    print(TOPICS[topic])
    return 0
