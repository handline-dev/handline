"""ocp guide <topic> - load detail on demand (AGENTS.md rule 3, section 10).

No logic lives in SKILL.md. Agents read a short stub and pull the detail from
here only when they actually need it, so it costs no tokens the rest of the time.
"""
from __future__ import annotations

from ..infra import paths
from .team import DEFAULT_LINKS

TOPICS = {
    "accounts": """accounts
  ocp account add <name>           register it and log it in (a human types the login)
  ocp account remove <name>        log it out and move it aside; refused while an agent is on it
  ocp account list                 usage, agents assigned and running, and whether it is assignable

ocp never reads, writes or copies credentials. A refresh token rotates on
single use, so two copies of one credential invalidate each other.""",
    "launch": """launching agents
  ocp launch <agent>               exec claude as that agent's account

From Orca:
  orca terminal create --worktree path:<wt> --command "ocp launch backend"

direnv is not used. Orca leaves CLAUDE_CONFIG_DIR empty and --command takes an
arbitrary shell string, so one wrapper is enough - and Orca preserves the launch
command across hibernation and restore.""",
    "status": """usage and fail-closed
  ocp status                       why the pick chose the account it did
  ocp account list --json          machine-readable

A usage figure we cannot trust - no snapshot, unreadable, or too old - is not a full
tank, it is a refusal. Reading it as 0 makes a walled account look open forever: the
feature is dead while the dashboard stays green.

A window nobody has spent in is different: a reading that reports no five_hour
window is the account telling us it is quiet, not that we cannot see - 0%,
marked `*`. `~` is a figure carried from an earlier reading, `!` one carried
past the age we will assign on - still a floor for triggers, no new work.

Design section 4 has the five cases and why only one of them is fail-closed.

No agent has an account of its own: a session starts on the best account that
has room - not at the wall, not at its cap of agents mid-turn (`ocp account
cap`), not filling so fast one more agent would not last half an hour - and the
state row records where it went, which `ocp bridge` shows. With no room the work
waits, and the refusal names the account that frees up first. Nothing moves a
live session below the wall. At the wall an agent moves only if work is waiting
and its account resets too late to wait for, once, and a resume always goes
back to the account its session lives on.""",
    "work": """giving an agent work, and talking to one
  ocp team                        who you may reach, and what they are doing
  ocp task new --owner <agent> -t <time> "<title>" "<body>"
                                  work, or a question that needs an answer or a result: the
                                  body is the brief (what to do, what done looks like) and
                                  is required; -t is how long before you are told it is
                                  still open. A decision only the founder can make is the
                                  same with --owner human
  ocp task show T<n>              one task whole: brief, memos, blockers, children, handoffs
  ocp task memo T<n> add "..."    a correction to a task you gave, not a message behind it
  ocp task discard T<n> "<why>"   throw away a task that is not done
  ocp say <agent> "..."           a message: typed at once, needs no answer, starts no work.
                                  To one of your reports it carries --task T<n> (a memo on that task)
  ocp say <agent> "..." --parent <id>   a reply, in its thread
  ocp inbox                       the one thing currently waiting for you, and a count
  ocp inbox --all                 the whole queue and its history
  ocp read <id>                   one of them whole
  ocp task done T<n> "<evidence>" finish your task (a sha, --request N, or a reason)
  ocp task handoff T<n> <agent> "<note>"   hand it on to someone else
  ocp cancel <id>                 the founder's: take any request out of an agent's queue
  ocp progress "<note>"           what is done and what is left on your task in progress
                                   (replaces the last note, at most 1500 characters; no note
                                   shows it). A handoff carries it, so the next session need
                                   not work it out from the transcript

There is one kind of message, and one thing that is not a message. A say reaches
its target at once and asks nothing. Everything that needs an answer or a result
is a task, and a task ends with `ocp task done` or is handed on: the result is
what the asker reads, so nobody sends a second task repeating it. An agent is
given one task at a time - the head of its queue - so nobody has to hold eleven
in their head; the next arrives once it is done or handed on. An agent that goes
idle with its task still open is reminded once after 10 minutes; if it is still
idle 30 minutes after it last did anything, the bridge's STUCK panel shows it,
with what is queued behind it. A wait is recorded on the task (`ocp task edit
T<n> --blocked-by T<m>`), not in prose. A task's opener can correct it with a
note while it is open. Only the founder can clear a request that was delivered
and never finished: `ocp cancel <id>` closes it as rejected ("withdrawn by the
founder"), tells the asker why and delivers what was behind it; the agent is
told to stop if it can be reached right now.

On `ocp bridge`: ↑↓ walks the whole screen and crosses into the next panel at
the end of one, ←→ jumps panel to panel, and the panel your keys act on is the
one drawn in colour. Open a message with ↵ and reply to it with `a` (or `e` for a
longer, multi-line one) - from the list or from inside it, whichever you are
looking at. An agent's page is the conversation with it, and whatever else is
queued for it sits under the conversation. ↵ writes to it; a message starting
with / is a command, and typing / lists them - among them /cancel <id>, which
takes a queued request out after saying who will be told, and /knows and
/done, which open what it knows and what it has done. `m` sends a message to
the agent under the cursor and `e` writes a longer, multi-line one. A decision
for the founder is a card in that agent's chat: the reply typed inside the card
is the decision, recorded on the task, and finishes it. `t` takes you to that
agent's own terminal, starting one if it has none.

An interactive shell with no OCP_AGENT belongs to a human, so `ocp say` typed at
your own prompt comes from human. A process that merely lost the variable
has no terminal, and is refused rather than handed the human's name.

That is the whole of it. The daemon delivers: an idle agent is told on the next
tick, and a closed one is resumed first and then told. Nobody has to nudge a
terminal, and an agent is told once per item rather than reminded.

An agent that is mid-turn is left alone until it is free - typing at a busy TUI
leaves the text sitting in its input box, unsent. So is one whose screen holds a
dialog, or whose conversation was moved to the background: a line typed there
would answer the dialog, or start a new session instead of reaching the agent.
Work waiting behind either is on the founder's WAITING ON YOU list after a few
minutes and a request to them after a few more, naming what is on the screen,
and both end when it does. ocp never answers a dialog; a person does.

An agent with no terminal is started rather than waited on, when its agent.yaml
says `repo:` - the daemon can make a worktree and a terminal but cannot choose
the repository. Without it, a task from one agent to another is a request for
a human to press a key, which is what sends an agent looking for a subagent
instead: a thing it can make itself, that dies with its session and appears on
no screen.""",

    "memory": """closing an idle session
  ocp close <agent>                end the session, keep the terminal
  (a task has no close: `ocp task done T<n> <sha, --request N, or a reason>`
   ends it, and its asker reopens it if the result was wrong. A task is
   always written T<n>; a bare number is a request)
  ocp resume <agent>               put it back, resuming the same session

A session that is not being used holds 250-500MB and spends no tokens at all.
Closing it ends the agent process, and is recorded only once the process has
left the process table - a session that will not end is reported, not marked
closed. The terminal, its worktree and its account stay, because Orca runs our
launch command inside a shell and claude is that shell's child. Waking types
the launch command back into the same shell with `--resume <session>` and the wake prompt as the session's first prompt, so the
account comes back with it and the conversation is the one it had. If the agent
last worked more than 55 minutes ago it is within 5 minutes of its prompt
cache's one-hour life - the margin a resume needs to reach its first request
before that cache is gone - so a resume risks re-reading the whole transcript
at full price and it starts a fresh conversation instead: the handoff taken
when it was closed - the request
in hand, the transcript's path, the worktree's state - is in that session's
system prompt, and the wake prompt says why.

This is not a handoff. No context is reclaimed, because there is nothing
wrong with an idle agent's context. The daemon does it by
itself after 10 minutes of silence, unless the agent is working, mid-handoff,
STALLED, or has a request waiting.

Just before a close or a planned move, while the cache is warm, the agent is asked
once to write `~/.orcapipe/agents/<you>/handoff.md`: only what `ocp` and your memory
cannot tell the next session (judgments in progress, what you wait on as T/# numbers,
unchecked hunches, how you read the founder's latest intent). It follows the facts in
the handoff. It is never waited on past 5 minutes: with no note the handoff is the
facts alone. `handoff_note: false` in config.yaml turns it off.""",

    "hooks": """Orca hooks
  ocp hooks status                 show drift
  ocp hooks sync                   apply once
  ocp hooks sync --watch           watch and auto-apply

Orca integrates with Claude Code through hooks in ~/.claude/settings.json. If a
per-account CLAUDE_CONFIG_DIR lacks those hooks, our agents disappear from the
Orca dashboard.""",
    "team": """who may ask whom
  ocp hr team <name> --member sea=<repo> --member shore --why "..."
                                   a manager and its members as one proposal, one approval
  ocp hr add / link / unlink       one agent or one edge, for what the defaults do not cover

Every team has these links without any edge written (the manager is the member
named like the team, the reviewer is `<team>.reviewer`):
""" + "\n".join(f"  - {d}" for d in DEFAULT_LINKS) + """

Anything else is denied, and the refusal says so: ask your manager, or propose an
edge with `ocp hr link`. A `deny` in team.yaml still beats a default link.""",
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
