# orcapipe

Run a team of role-based Claude agents across several accounts, cheaply and
unattended, on top of Orca.

The priority is not features. It is **not dying overnight without a human**, and
not spending tokens to decide things that can be decided deterministically.

## Install

```bash
ln -s "$PWD/bin/ocp" /usr/local/bin/ocp     # or put bin/ on PATH
ocp init
ocp account add alpha      # registers it, then hands the terminal to claude to log in
ocp account add beta       # use a different Anthropic login for each
```

The login is claude's own screen, so **a human** types into it. `ocp account
remove <name>` logs an account out and moves its directory aside; it refuses
while an agent is on it. Both are also `a` and `x` in the accounts panel.

`ocp` never reads, writes or copies credentials. A refresh token rotates on
single use, so two copies of one credential invalidate each other.

```bash
ocp agent register backend   --account alpha --model sonnet
ocp agent register reviewer  --account beta  --model opus
ocp agent register archivist --account beta  --model haiku
ocp team init && ocp doctor
```

## Run an agent

```bash
orca terminal create --worktree path:/path/to/wt --command "ocp launch backend"
```

`ocp launch` execs claude with that agent's `CLAUDE_CONFIG_DIR`. Orca preserves
the launch command across hibernation and restore, so a resumed agent comes
back on the same account.

## Give an agent work

```bash
ocp ask keeper "rebase onto main and re-run the suite"
ocp inbox                       # what is waiting on you, and what came back
ocp read 12                     # one of them whole, with its answer
ocp resolve 12 "approved"       # answer one
ocp dismiss 12                  # take one off the list without replying
```

That is all of it. The daemon delivers: an idle agent is told, a closed one is
resumed first and then told, and a busy one is left alone until it is free.
Nobody has to go and nudge a terminal. An interactive shell with no `OCP_AGENT`
is a human's, so `ocp ask` from your own prompt comes from `human`; a
script that lost the variable is refused rather than promoted, and says who it
is with `--of`.

## Run the supervisor

```bash
ocp daemon            # parks agents before they hit the wall, resumes them after
ocp ps                # daemon and agent state, and which supervisor is running it
ocp triggers          # what the daemon would decide right now, without acting
```

`ocp daemon` on its own dies with the terminal that started it. To survive a
reboot or a crash without a human restarting it, install it as a launchd
LaunchAgent:

```bash
ocp service install    # write the plist, load it into launchd (RunAtLoad + KeepAlive)
ocp service status     # installed / loaded / pid / crash-looping
ocp service logs       # tail the daemon's launchd-captured stdout
ocp service uninstall  # unload the job and remove the plist
```

One daemon runs, whoever started it: it holds an flock for its whole life, and
a second one exits rather than supervise beside it. The holder writes its pid
into that lock file, and `ocp ps`, `ocp doctor` and the bridge ask the lock
itself whether a daemon is running, so what refuses a second daemon and what is
reported are one fact and there is no pidfile to go stale. `ocp ps` and
`ocp doctor` also count daemons in the process table and say so when there are
two.

An agent ocp has work for and cannot give it - a dialog open on its screen (such
as `/exit` meeting background shells), text left unsent in its input, no terminal
or session - is written to `delivery_blocks` and is not silent: `ocp ps` and the
bridge list it under WAITING ON YOU after `blocked.SHOW_AFTER_S`, naming the
dialog from the screen, and it becomes one request to `human` after
`blocked.ESCALATE_AFTER_S`, closed by the daemon when the agent can be given its
work again. ocp names the dialog and never answers it.

## Commands

| | |
|---|---|
| `ocp close <agent>` | end an idle session, keep its terminal (`ocp resume` brings it back) |
| `ocp doctor` | verify the preconditions of every phase |
| `ocp account list` | how the accounts stand now: usage windows, the agents assigned to each and how many are running, and whether it may take work |
| `ocp status` | why the daemon picked the account it picked, and the price of one more agent it rests on |
| `ocp ask <target> "<req>"` | queue work for an agent; the daemon delivers it |
| `ocp inbox` / `ocp resolve <id> "<verdict>"` / `ocp dismiss <id>` / `ocp cancel <id>` (founder) | request queue |
| `ocp fold [agent]` | fold events into the digest (the one model call) |
| `ocp rollover` | midnight carry-forward, no model |
| `ocp digest` / `ocp handoff <agent>` | the digest / the handoff waiting for an agent (three facts: its request, its transcript, its worktree) |
| `ocp drift <repo>` | has the base moved under this agent? |
| `ocp hooks sync --watch` | keep Orca's agent hooks mirrored |
| `ocp service install/uninstall/status/logs` | run the daemon as a launchd LaunchAgent |
| `ocp bridge` | the screen the team is run from (`top` and `watch` still work) |
| `ocp guide [topic]` | detail, loaded on demand |

## How it decides things

**Fail-closed.** A `pct_5h` we cannot trust - no snapshot, unreadable, or too old
- is not a full tank, it is a refusal to assign. Reading it as 0 makes the 90%
trigger false forever: the feature is dead while the dashboard stays green.

**But quiet is not unknown.** Claude Code sends no `five_hour` block for a
session that has made no request since the window rolled over, so a live
collector reporting nothing means nothing has been *reported* spent in this
window. That reads as 0% and assigns, marked `*`. Refusing here parked five idle
agents. The exception is ours: `ocp fold` runs headless,
which has no status line, so its own small spend is never observed.

**A figure has a provenance, and it is drawn.** `~` is carried - an earlier
reading whose window is still open, since a payload that does not mention a
window must not erase what we knew. `!` is carried past the age we will assign
on: still used as a floor for triggers, because usage inside a window only
rises, but no new work goes to it. Inside one window the account figure never
goes down, because every session reports from the moment of its own last API
response and the staler one must not win.

**An account is a preference, not a destination.** `agent.yaml` says which
account an agent would rather be on, and a session goes there unless what is
known says it is a bad place to start: at the wall, at its cap of agents
mid-turn (2 by default, `ocp account cap <name> <n>` or `c` on the bridge), or
filling so fast that one more agent would last under half an hour. How fast it
is filling - the account's measured burn - is what says it has room, not a
fixed line, and the agent being placed is priced by its own kind: what one more
agent costs is the median of what agents of its model and effort were measured
spending, falling back to its model's, all agents', and 0.75%/min of the window
until enough has been seen.

**A working agent runs to the wall.** Nothing below 100% moves or parks it: a
turn that ends on the usage limit, or a reading at 100%, is the wall, and
nothing is lost there because the transcript keeps the work. An account nobody
can read is not a wall. At the wall the choice to move is a cost comparison: an
idle agent stays parked, and so does one whose account resets within T (39
minutes, then the measured cost of a cold start as a share of a 5h window -
`measure.py`). Otherwise it moves once, to the account the pick chooses - the
weekly headroom that expires soonest first - and never back to the one it just
left before that resets. If every account is capped or walled the work waits in
the queue. A resume goes back to the account its session lives on, because a
session id exists only under that account's config directory.

**Context and memory are different problems.** A handoff reclaims *context*: it
records the agent's request, its transcript path and its worktree's state,
clears the session, and costs no model call. Closing reclaims
*memory*: an idle session holds 250-500MB and no one is using it, so the session
ends and the terminal, worktree and account stay. Waking resumes that same
session, which costs nothing extra - measured, a resumed session read its whole
68k prefix from cache exactly as a resident one did, because the cache lives on
the server and expires on time, not on whether our process stayed alive.

**No model in the loop.** Triggers, account choice and resume decisions are all
deterministic, and so is the handoff. The only model call is `ocp fold`, which
nothing on the handoff path runs.

**Conditions, not edges.** Triggers are re-evaluated from persisted state every
tick, so a threshold crossed while the daemon was dead is still acted on.

**Unlisted edges are denied.** Agents cannot spray requests around, and a
refusal explains itself so they do not try to route around it.

**An answer goes back the way the question came.** `ocp resolve` writes the
verdict on the request; the daemon reads it out to the agent that asked, and a
human reads it in `ocp inbox` and on `ocp bridge`. Both directions, or the team
learns to talk around it: with only one drawn, agents took to sending a second,
unrelated request repeating what they had already answered.

## Layout

```
~/.orcapipe/                 git repo; accounts/ and logs/ are ignored
  config.yaml team.yaml db.sqlite locks/
  logs/daemon.out.log logs/daemon.err.log   # launchd's StandardOut/ErrorPath
  shared/.claude/            settings + collector, symlinked into each account
  shared/mcp.json            MCP servers every launch passes via --mcp-config;
                             founder-filled, optional, never generated from ~/.claude.json
  accounts/<acct>/.claude/   CLAUDE_CONFIG_DIR; credentials stay here, untouched
  accounts/<acct>/status.json
  agents/<name>/             agent.yaml ROLE.md base.md inbox/ journal/
```

Each account's `.claude/` links to the items of `shared/.claude/` that exist
(settings.json, CLAUDE.md, skills, agents, commands, plugins). An item added
there later is linked into every account that lacks it, by the daemon, on its
next tick - it fills an *absent* slot, so a link deleted on purpose comes back.
To opt an account out of a shared item, put a real file or a link of its own at
that path: the daemon leaves it alone, and `ocp doctor` names it as a shared
file that does not reach that account.

## Tests

```bash
python3 tests/test_ocp.py     # 240 checks, no network, no tokens
```

Design docs are in `design/`; the Phase 0 findings and their evidence are in
`docs/phase0-findings.md`.
