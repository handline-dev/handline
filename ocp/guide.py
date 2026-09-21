"""ocp guide <topic> - load detail on demand (KICKOFF rule 3, section 10).

No logic lives in SKILL.md. Agents read a short stub and pull the detail from
here only when they actually need it, so it costs no tokens the rest of the time.
"""
from __future__ import annotations

from . import paths

TOPICS = {
    "accounts": """accounts
  ocp account add <name>           register it and log it in (a human types the login)
  ocp account remove <name>        log it out and move it aside; refused while an agent is on it
  ocp account list                 usage, agents assigned and running, and whether it is assignable
  ocp agent move <agent> <account> record its handoff, end its live session and start fresh there, making it the preferred account (a human's call; also v on the bridge)

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
  ocp status                       why the pick chose the account it did
  ocp account list --json          machine-readable

A pct_5h we cannot trust - no snapshot, unreadable, or too old - is not a full
tank, it is a refusal. Reading it as 0 makes a walled account look open forever: the
feature is dead while the dashboard stays green.

A window nobody has spent in is different. Claude Code sends no five_hour block
for a session that has made no request since the window rolled over, so a live
collector reporting nothing is the account telling us it is quiet: 0%, marked
`*`. `~` is a figure carried from an earlier payload, `!` one carried past the
age we will assign on - still a floor for triggers, no new work.

Design section 4 has the five cases and why only one of them is fail-closed.

An agent's account is a preference. A session starts on it unless what is known
says it is a bad place to start - at the wall, at its cap of agents mid-turn
(`ocp account cap`), or filling so fast one more agent would not last half an
hour - and on the best account that qualifies when it is; the state row records
where it went, and `ocp bridge` shows that rather than the preference.
Nothing moves a live session below the wall unless a person orders it (`ocp
agent move`). At the wall an agent moves only if work is waiting and its account
resets too late to wait for, once, and a resume always goes back to the account
its session lives on.""",
    "work": """giving an agent work
  ocp team                        who you may ask, and what they are doing
  ocp ask <agent> "<task>"        queue it
  ocp ask <agent> "..." --first   put it at the head of the queue
  ocp ask <agent> "..." --replaces <id>   correct a task you sent, before it is delivered
  ocp ask human "..." --fyi       a report that asks nothing: not red, not counted as
                                  waiting on the founder, gone once read or superseded
  ocp withdraw <id>               take back your own request, before it is delivered
  ocp cancel <id>                 the founder's: take any request out of an agent's queue
  ocp inbox                       the one thing currently waiting for you, and a count
  ocp inbox --all                 the whole queue and its history
  ocp read <id>                   one of them whole
  ocp resolve <id> "<verdict>"    answer one
  ocp dismiss <id>                take one off your list without replying

An agent is given one request at a time - the head of its queue - not the
whole pile at once, so nobody has to hold eleven requests in their head to
know what to do next. The next one is delivered only once you resolve or
dismiss the current one. An agent that goes idle with the current one still
open, having asked nobody anything, is reminded once after 10 minutes; if it is
still idle on it 30 minutes after it last did anything, the bridge's STUCK panel
shows it, with what is queued behind it. Before a request has been delivered, its own
requester may still replace (`--replaces`) or withdraw it; once delivered, a
correction is a reply in its thread (`ocp ask <agent> "..." --parent <id>`),
not a new item behind it. Requests to `human` are the exception: a correction
with `--replaces` retires your earlier one even after the bridge showed it.
Only the founder can clear one that was delivered and never resolved: `ocp
cancel <id>` closes it as rejected ("withdrawn by the founder"), tells its
requester why and delivers what was behind it; the agent is told to stop if
it can be reached right now, otherwise it finds out only when its own resolve
of the row is refused. The row and its number stay; a request of the founder's
own that was never delivered is simply withdrawn.

On `ocp bridge`: ↑↓ walks the whole screen and crosses into the next panel at
the end of one, ←→ jumps panel to panel, and the panel your keys act on is the
one drawn in colour. Open a message with ↵ and answer it with `a` (or `e` for
$EDITOR) - from the list or from inside it, whichever you are looking at. On an
agent's page ←→ moves between its sections, and in WAITING FOR IT ↑↓ picks a
request and `x` cancels it, after saying who will be told. `m` sends an order
to the agent under the cursor and `e` writes a longer one in $EDITOR; `t` takes
you to that agent's own terminal, starting one if it has none.

An interactive shell with no OCP_AGENT belongs to a human, so `ocp ask` typed at
your own prompt comes from human. A process that merely lost the variable
has no terminal, and is refused rather than handed the human's name.

That is the whole of it. The daemon delivers: an idle agent is told on the next
tick, and a closed one is resumed first and then told. Nobody has to nudge a
terminal, and an agent is told once per request rather than reminded.

An agent that is mid-turn is left alone until it is free - typing at a busy TUI
leaves the text sitting in its input box, unsent. So is one whose screen holds a
dialog, or text nobody has sent: a line typed there would answer the dialog or
join the text. Work waiting behind either is on the founder's WAITING ON YOU
list after a few minutes and a request to them after a few more, naming the
dialog, and both end when the dialog does. ocp never answers a dialog; a person does.

An agent with no terminal is started rather than waited on, when its agent.yaml
says `repo:` - the daemon can make a worktree and a terminal but cannot choose
the repository. Without it, an order from one agent to another is a request for
a human to press a key, which is what sends an agent looking for a subagent
instead: a thing it can make itself, that dies with its session and appears on
no screen.

An answer goes back the same way, without being asked for again. `ocp resolve`
writes the verdict on the request, the daemon reads it out to the agent that
asked, and a human sees it in `ocp inbox` and on `ocp bridge` - so replying to
whoever asked is resolving their request, never a second request that repeats
it. A reply to *that* answer is `ocp ask ... --parent <id>`, which keeps the
exchange as one thread; the screen's answer key does it for you.""",

    "memory": """closing an idle session
  ocp close <agent>                end the session, keep the terminal
  ocp resume <agent>               put it back, resuming the same session

A session that is not being used holds 250-500MB and spends no tokens at all.
Closing it ends the agent process, and is recorded only once the process has
left the process table - a session that will not end is reported, not marked
closed. The terminal, its worktree and its account stay, because Orca runs our
launch command inside a shell and claude is that shell's child. Waking types
the launch command back into the same shell with `--resume <session>` and the wake prompt as the session's first prompt, so the
account comes back with it and the conversation is the one it had. If the agent
last worked more than 55 minutes ago its prompt cache (one hour) has expired
and a resume would re-read the whole transcript at full price, so it starts a
fresh conversation instead: the handoff taken when it was closed - the request
in hand, the transcript's path, the worktree's state - is in that session's
system prompt, and the wake prompt says why.

This is not a handoff. No context is reclaimed, because there is nothing
wrong with an idle agent's context. The daemon does it by
itself after 10 minutes of silence, unless the agent is working, mid-handoff,
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
