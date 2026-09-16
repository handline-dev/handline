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

A null pct_5h is not a full tank, it is a refusal. Reading a missing value as 0
makes the 90% trigger false forever: the feature is dead while the dashboard
stays green.""",
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
