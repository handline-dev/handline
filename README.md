# orcapipe

Run a team of role-based Claude agents across several accounts, cheaply and
unattended, on top of Orca.

The priority is not features. It is **not dying overnight without a human**, and
not spending tokens to decide things that can be decided deterministically.

## Install

```bash
ln -s "$PWD/bin/ocp" /usr/local/bin/ocp     # or put bin/ on PATH
ocp init
ocp account register alpha
ocp account register beta
```

Then, once per account, **by a human**:

```bash
CLAUDE_CONFIG_DIR=~/.orcapipe/accounts/alpha/.claude claude /login
CLAUDE_CONFIG_DIR=~/.orcapipe/accounts/beta/.claude  claude /login
```

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

## Run the supervisor

```bash
ocp daemon            # parks agents before they hit the wall, resumes them after
ocp ps                # daemon and agent state
ocp triggers          # what the daemon would decide right now, without acting
```

## Commands

| | |
|---|---|
| `ocp close <agent>` | end an idle session, keep its terminal (`ocp resume` brings it back) |
| `ocp doctor` | verify the preconditions of every phase |
| `ocp status` / `ocp account list` | usage, and which account may take work |
| `ocp ask <target> "<req>"` | the only command agents use |
| `ocp inbox` / `ocp resolve <id> "<verdict>"` | request queue |
| `ocp fold [agent]` | fold events into the digest (the one model call) |
| `ocp rollover` | midnight carry-forward, no model |
| `ocp digest` / `ocp handoff <agent>` | the record |
| `ocp drift <repo>` | has the base moved under this agent? |
| `ocp hooks sync --watch` | keep Orca's agent hooks mirrored |
| `ocp guide [topic]` | detail, loaded on demand |

## How it decides things

**Fail-closed.** A `pct_5h` we cannot trust - no snapshot, unreadable, or too old
- is not a full tank, it is a refusal to assign. Reading it as 0 makes the 90%
trigger false forever: the feature is dead while the dashboard stays green.

**But quiet is not unknown.** Claude Code sends no `five_hour` block for a
session that has made no request since the window rolled over, so a live
collector reporting nothing means nothing has been *reported* spent in this
window. That reads as 0% and assigns, marked `*`. Refusing here parked five idle
agents and paid for a fold each. The exception is ours: `ocp fold` runs headless,
which has no status line, so its own small spend is never observed.

**A figure has a provenance, and it is drawn.** `~` is carried - an earlier
reading whose window is still open, since a payload that does not mention a
window must not erase what we knew. `!` is carried past the age we will assign
on: still used as a floor for triggers, because usage inside a window only
rises, but no new work goes to it. Inside one window the account figure never
goes down, because every session reports from the moment of its own last API
response and the staler one must not win.

**Context and memory are different problems.** A handoff reclaims *context*: it
folds the day, clears the session and costs one model call. Closing reclaims
*memory*: an idle session holds 250-500MB and no one is using it, so the session
ends and the terminal, worktree and account stay. Waking resumes that same
session, which costs nothing extra - measured, a resumed session read its whole
68k prefix from cache exactly as a resident one did, because the cache lives on
the server and expires on time, not on whether our process stayed alive.

**No model in the loop.** Triggers, account choice and resume decisions are all
deterministic. The only model call is one fold per handoff - and closing an idle
agent is not a handoff, so it costs none at all.

**Conditions, not edges.** Triggers are re-evaluated from persisted state every
tick, so a threshold crossed while the daemon was dead is still acted on.

**Unlisted edges are denied.** Agents cannot spray requests around, and a
refusal explains itself so they do not try to route around it.

## Layout

```
~/.orcapipe/                 git repo; accounts/ is ignored
  config.yaml team.yaml db.sqlite daemon.pid
  shared/.claude/            settings + collector, symlinked into each account
  accounts/<acct>/.claude/   CLAUDE_CONFIG_DIR; credentials stay here, untouched
  accounts/<acct>/status.json
  agents/<name>/             agent.yaml ROLE.md base.md inbox/ journal/ handoff/
```

## Tests

```bash
python3 tests/test_ocp.py     # 240 checks, no network, no tokens
```

Design docs are in `design/`; the Phase 0 findings and their evidence are in
`docs/phase0-findings.md`.
