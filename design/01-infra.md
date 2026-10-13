# Orca-based multi-agent pipeline: infrastructure design v0.5

> v0.5 is the version that **cut the v1 scope**. Deferring dynamic account reallocation to v2 removed most of the features that came from it (§0.5).
> Execution is Orca native plus the `ocp launch` wrapper. The tmux hybrid is dropped; the reasons are in appendix A.
> Team composition, relations and routing are in the companion document `design/02-team.md`.

---

## 0. Summary

State comes from files the session already writes and from the usage endpoint (§4, D11; no collector is planted).
A thin CLI, `ocp`, on top of Orca orchestration **leases accounts as slots**.
Records are **folded outside the session**, and triggers and resumes run on **persisted state plus reconciliation at startup**.

Three principles:

- **Our code has zero lines that read or write a credential**
- **No model in orchestration.** Everything is deterministic, so the daemon does it
- **One metric**: `runway`. Assignment and ordering look only at it


---

## 0.5 v1 scope: the complexity has one source

Reading the design again, **more than half of it derives from one thing, dynamic account reallocation.**

```
dynamic account reallocation
 ├ lease table · 2 UNIQUE indexes · reaper · PID liveness check · flock
 ├ queue · aging · prio · head-of-line blocking guard · min_runway_eff
 ├ runway gate · burn floor · cold-start special case · reset-boundary correction
 ├ thundering herd · jitter · convoy guard
 ├ single arbiter · re-pick on acquire failure
 └ tmux hybrid (for PID ownership)
```

### The gain is smaller than it looks

**Total throughput is fixed at `accounts x 5-hour quota`.** Dynamic reallocation raises not the total but the **utilization**: while one account idles, another agent uses it.

The utilization gain scales with **agents / accounts**. It is large only when there are far more agents than accounts, and nearly nothing when the two are close.

### v1 uses a fixed mapping

> **Now**: an agent does move accounts. At a limit it moves once, to the account `pick` chooses (README). What follows is the record of the call made when v1 scope was cut.

```yaml
backend   → alpha
frontend  → beta
reviewer  → gamma      # serial, light
archivist → gamma      # ephemeral, writes briefly and leaves
```

When its own account hits the wall (100%), the agent hands off. If work is waiting and the reset is far, it moves once to another account with room; otherwise it **rests until the reset.**

The derived list above **disappears entirely.** What remains:

| What stays in v1 | Why |
|---|---|
| Agent folder + ROLE/base/handoff/journal + fold | Valuable whatever the accounts are, and low complexity |
| `team.yaml` routing + `ocp task new`/`ocp say` | The core of how the team runs. Orthogonal to accounts |
| State reading (D11: session files + endpoint) | Read-only. Several accounts' state at a glance |
| Daemon (reduced) | trigger → handoff (record three facts) → park → resume at reset |
| Orca orchestration | As is |

With no lease and no queue, the daemon becomes an **independent loop per agent**: `is my account at 87% / is context at 60% → handoff → park → resume at the reset time.`

### Why deferring to v2 is safe

The journal is **keyed by agent name, independent of the account** (§5), so moving accounts later does not break the history. The design already guarantees that, so **the record layer stays the same if the slot model is added later.**

Signal to switch: when **agents outnumber accounts 2 to 1**, the utilization loss becomes noticeable. Then turn on §6 and §7.

> §6 (slots) and §7 (scheduling) below were **not built**. The code picks an account by measured burn rate (no lease table). They stay as a record only.

---

## 1. Dependencies

| Item | Verdict |
|---|---|
| **Orca orchestration / worktree** | Keep. Use the DAG, workers and gates as they are |
| ~~Claude Code statusLine~~ | Replaced by D11: session files + the usage endpoint are the source (§4) |
| cswap / clauth | **Removed.** The usage file we meant to read does not exist, and statusLine replaced it |
| ccusage | **Post-hoc analysis only.** Its blocks are a local reconstruction, not the rate-limit window, so scheduling cannot use them |
| handoff and parsers | Built in |

### Security: never move a credential

What an account-switching tool handles is the subscription OAuth token. Unlike an API key it carries the whole account's authority and is hard to revoke individually. "Third party or our own implementation" turned out to be the wrong question: even a direct implementation has the lock and rotation problems, which Claude Code itself has not solved.

```
Run /login once per account in its own CLAUDE_CONFIG_DIR and keep that directory for good.
A "swap" = changing an environment variable and starting a new process.
Never read, write or copy a credential file.
```

The official docs guarantee isolation per `CLAUDE_CONFIG_DIR`. Even the macOS Keychain entry is keyed by config dir.

**Never copy.** A refresh token is a one-time rotation. If two copies each refresh, the first to succeed invalidates the other and a re-authentication cascade follows. That is why sharing an account (`max_concurrent >= 2`) was left out of the design. A shortage of slots is solved by queueing.

### Survey of multi-account tools: almost none is usable

Orca has the notion of **one global active account** (official docs: the status bar always reflects the *active* account). So another mechanism is needed. A look at the ecosystem splits the tools in two kinds, and **the one with more stars is the one that is useless to us.**

| Kind | Behavior | Concurrent runs | Examples |
|---|---|---|---|
| **A. Global swap** | Replaces the credential/Keychain atomically | **No: one account at a time** | `aisw` (116 stars, Rust), `claude-switch`, `multi-account-tool`, most of the list |
| **B. CONFIG_DIR isolation** | A directory per profile, env per process | **Yes** | `claude-profile-manager` (15 stars, Go), `claude-profile-switch` (shell), `clauth` (Rust, `start --isolated`) |

Kind A has **the same constraint as Orca.** Changing tools leaves the problem as it was. The most popular one, `aisw`, belongs here.

Among kind B, `claude-profile-manager` reached **the same conclusion independently** as our §3 layout: a `CLAUDE_CONFIG_DIR` per profile, the credential inside the profile, commands, skills and plugins shared by symlink. As a check on the design, that counts.

**Even so, we attach no tool.** Three reasons:

1. All we need is one layer of launcher: `CLAUDE_CONFIG_DIR=accounts/<a>/.claude claude ...`. `ocp` already does that
2. **No tool replaces our scheduler.** Their "best account" logic has no notion of `runway`, a queue or a lease
3. All of them have 2 to 116 stars, and above all, attaching a tool **breaks "zero lines of code that touch a credential"**

We copy only what is worth copying: the layout and the list of symlink targets, the `CLAUDE_CONFIG_DIR=... claude /login` bootstrap (= `ocp account add`, 10 lines), and a link-integrity `doctor`.

### Execution layer: inside Orca, through `ocp launch`

Orca is of the **global swap kind.** The evidence is two sentences of the official docs, and *"switching accounts works even with live Claude sessions running"* and *"a guard so you don't trigger overlapping auth refreshes"* are decisive. If switching were an env injection at launch, a session already running could not be affected and no guard would be needed.

And the Orca issue tracker **already holds the same request as ours**: [#11557 "Support multiple Claude Code subscriptions (separate config dirs / env vars)"](https://github.com/stablyai/orca/issues/11557). It is **not implemented**, and the requester also wrote "in the terminal I split config dirs, but inside Orca I could not find a way to do the same."

So there is one way that works inside Orca: **`ocp launch <agent>` sets `CLAUDE_CONFIG_DIR` and starts the agent.** Orca leaves that variable empty and `--command` is an arbitrary shell, so a wrapper is enough (J4, §12).

Then hibernation, session restore and the dashboard all keep working. Since v1 is a **fixed mapping** (§0.5), an agent's account is static. That is the biggest practical gain of the fixed mapping.

**Bonus**: Orca's agent hibernation keeps the *"launch command, arguments, and environment"* on resume. An env set once survives the resume.

**Dropped**: the tmux hybrid. PID ownership was needed for lease reclaim (§6), and v1 has no leases. It comes back out when the slot model is turned on in v2.

**Remaining risk**: **J0**, whether Orca forces its own statusLine through a CLI flag. The evidence is in `docs/phase0-findings.md`.

### Two tools treat `.claude.json` differently

`claude-profile-manager` keeps `.claude.json` **inside the profile directory**, and `claude-profile-switch` **swaps `~/.claude.json` globally**. The latter means two concurrent sessions share one file.

**That the two tools solve it differently is itself evidence that Phase 0 assumption H is the real fork.**

### Adapter rule

**Extract fields by allowlist.** Never dump `--json` output whole into a journal or the DB. Never put a token in a subprocess `argv` (exposed through `/proc/*/cmdline`).

---

## 2. Domain model

```
Run       (Orca) ── a goal
 └ Task   (Orca) ── work/sub-work. Dependencies + state
    └ Dispatch (Orca) ── one attempt at a task = one terminal
       └ Session ── one transcript + one status.json
          ├ Agent (ours) ── name + role + its own folder
          └ Lease (ours) ── the slot this session holds
```

**The agent name is the join key of every system.** Worktree name, task label and folder name use the same string.

---

## 3. Layout

Keep all of `~/.orcapipe` **as a git repo.** Digest generations, rollback and change history come free. `accounts/*/` is in `.gitignore`.

```
~/.orcapipe/                    ← git repo
  config.yaml
  team.yaml
  db.sqlite                     # leases · events · handoff progress · wake_at   (gitignored)
  locks/daemon.lock             # the daemon holds the flock and writes its own PID
  shared/.claude/               # settings common to accounts. Each account links to it by symlink
    settings.json CLAUDE.md skills/ agents/ commands/ hooks/
  accounts/                     ← all gitignored
    alpha/
      .claude/                  # CLAUDE_CONFIG_DIR. Only the credential is a real file, the rest are shared links
      status.json               # snapshot the statusLine collector writes atomically
  agents/
    <name>/
      agent.yaml
      ROLE.md
      base.md                   # injected into every session
      inbox/
      journal/2026-09-15.md     # digest. Git keeps the generations
```

---

## 4. Data sources: pull from session files + the usage endpoint

**The source changed on 2026-09-21 (D11).** The original contract of this section was the statusLine script's stdin JSON: we planted a collector in the `settings.json` of each `CLAUDE_CONFIG_DIR`, it received the payload every turn and wrote `status.json`. Now **we plant nothing**: we read the files the session writes anyway. The statusLine slot is left alone.

There is **no** usage file under `~/.claude`. Reading a file that does not exist and returning `pct=0` would make every slot a perfect score and the wall trigger forever false. That is a fail-open where **the feature is dead and the dashboard is green.** So there are two sources, and they fail in different ways:

- **The session's transcript** (`handline/usage/collect.py`): context, session identity, model, cost. Our hook (`Stop` etc., every turn) writes it, and the daemon tick picks up a session the hook did not report. Which account it is also comes from **the transcript's path** (`accounts/<acct>/.claude/...`), as first-hand as `CLAUDE_CONFIG_DIR`.
- **The usage endpoint** (`handline/usage/usage_api.py`): the rate limit. It is not in the transcript (only a 429 after hitting the wall). One account per tick, and only when it could have changed.

The files are still `accounts/<acct>/status.json` and `agents/<agent>/status.json` with the same field shapes, so no reader changed.

| Value needed | Source |
|---|---|
| `pct_5h` | The endpoint's `five_hour.utilization`. In the statusLine days it was **the value at that session's last API response** ([claude-code#75408]); the endpoint answers for now, with no session needed |
| 5h reset time | The endpoint's `five_hour.resets_at` |
| Weekly headroom | The endpoint's `seven_day.*` |
| `rl_at` · `rl_carried` · `rl_lost` | **When that 5h window was observed** (not when the number was set: if the payload reported the window, it is an observation even when its value lost to the high-water mark), whether it is carried over rather than from this payload, and whether it was **lost** because the window boundary was unknown. `collected_at` only says the collector is alive, so it cannot give the age of the number |
| `rl7_at` · `rl7_carried` · `rl7_lost` | The same for 7d. The 7d window is a week, so **no age limit is set**, but a lost 7d refuses assignment. The 95% gate says "block if there is a value and it is high", so with no value the result is not a refusal but **the absence of a refusal** |
| Context usage | Sum of the `usage` of the transcript's last assistant turn / model window → **trigger (§8)**. The window size per model is a table (`collect.CTX_SIZE`): an unknown model gives **no number**, and a small default would park every agent at 1/5 of its window |
| Session join keys | The transcript **file itself** is both: its name is the `session_id`, its path the `transcript_path` |
| Cost | The transcript's `cost-state.totalCostUSD` (some versions lack it. It is display-only with a single reader, so missing is not a failure) |

`collected_at` is **the transcript's mtime**: not when we read it but when the session last wrote. That keeps the meaning of `status.stale` ("the session stopped writing") and keeps the daemon from looking alive just by reading a dead session's file.

### Constraints: `ocp doctor` enforces the check

- It is refreshed only while the session is alive. This is unchanged. Our hook (`Stop`) stands in for the heartbeat, and the tick picks up a session where the hook did not fire
- If managed settings set anything in the `disableAllHooks` family, hooks are **disabled without a warning.** This now kills the **hooks**, not the statusLine, so context and session identity refresh only at the tick interval (30 seconds)
- The usage endpoint has a rate limit of its own. Aggressive polling fails open on a stale value
- A null `pct_5h` is not always the same null. There are **five verdicts** (measured 2026-09-17)
  - `live`: this payload reported that window
  - `carried`: an earlier observation, the window has not ended, and it was observed within `CARRY_MAX_AGE_S` (900s)
  - `carried_old`: the same carried value, but older. It is **used for triggers** (usage only rises within a window, so it is a floor) and **refuses new assignment**
  - `quiet`: a live collector says nothing about that window. This is an **observation** that no one used it in this window, so it reads as 0% and assigns. Refusing here would hand off an idle agent and burn only fold cost
  - `unknown`: no snapshot, or unreadable (NaN, a string, a boolean), or **lost without being carried.** Only this one fails closed. Assignment does not refuse a stale value as unknown: inside the window it is a floor, and when the window ends that floor loses its meaning and a probe asks again (`status.idle_floor`)
- So the **writer** keeps four rules. Two of them lived in the original collector's jq and now live in `handline/usage/usage_api.record` (the high-water mark and the rollover). The other two, carry-over for a payload that does not report a window and `rl_lost`, **no longer have anything to apply to**: the endpoint reports both windows or none. The reader rules (the five above) are all unchanged: when a poll fails or the token dies, that is all that is left
  - A payload that does not report a window **does not erase that window's earlier value** (while `resets_at` is still ahead.
    A local clock error of 60 seconds is tolerated, since `resets_at` is set by the server and compared by the local clock)
  - **If there is no `resets_at` to tell whether the window ended**, drop the value but mark it `rl_lost`. A lost value is
    **unknown**, not quiet. And **that state must be sticky**: if the next payload released it to quiet,
    an account known to be at 95% would read as 0% **after one refresh**. It stays unknown until a
    payload reports the window again
  - **Within one window the account number does not go down.** Sessions observe at different times, and without this
    every time an older session draws last, the account value goes backwards (that is the symptom in [claude-code#75408]).
    Compare **at the resolution the reader uses (the rounded-up integer).** The raw float has noise
    (`28.999999999999996` = 29), and one speck of downward noise would lock this rule shut for good.
    An ended window cannot press down a new value: an old high-water mark must not overwrite the 0 that a reset produced
  - **A payload that reports the window is an observation even if its value loses to the high-water mark.** If `rl_at` is not
    raised, an account that is alive and reporting every second ages into `carried_old` and cannot take new work
- **Known exception to the quiet inference**: `ocp fold` runs as `claude -p`, which is headless and has no statusLine.
  So the quota fold uses is **seen by no collector.** It is a small call each time, but `quiet` must be read as
  **"no reported use"**, not "no one used it"
- **`autoContinueAtUsageLimit`** in Claude Code v2.1.234+ overlaps our resume A. It forces a **choice**: turn it off and manage it ourselves, or turn it on and delegate

### Dedup is required when aggregating the transcript

Each content block of one API response becomes its own JSONL line and **copies the same usage object.** Without dedup by `message.id` the burn rate is overcounted, so `runway` is undercounted, **every slot fails the gate and everyone sticks in PARKED.** One measured sample was 2.4x.

**The context pull does not fall into this trap**: it reads **one** last `usage` record, not a sum. The answer is the same however many lines the same object is copied to. Measured on 19 agents, it matched the statusLine value to the token. Dedup is still needed only for the burn rate.

---

## 5. Records

### Layers

```
events (SQLite)   machine events. The hook INSERTs. Neither a person nor a model reads them
journal/<date>.md digest. `ocp fold` rewrites it whole (the handoff path does not call it)
handoff        stored on the agent row (agent_state.handoff). Built with no model from three facts: request ids, the previous session's transcript path, and the worktree branch/HEAD/status. Injected once on resume, then cleared
```

Events go **into SQLite, not a file.** The delta boundary becomes one line, `WHERE id > cursor`, and a hook that arrives late does not break the order.

### Fold runs outside the session

```
fold input  = previous digest + events delta + transcript tail
fold run    = a separate call. It need not be on that account, and a cheap model is enough (archivist)
cursor move = only after a fold that passed validation is committed, in the same transaction
```

Summarizing inside the session grows the input without bound in proportion to the session's length. Moving it out decouples the fold from the cache expiry time and removes the problem of eating the reserve near 90%.

### Record timing: not periodic

| When | What | Model |
|---|---|---|
| `SessionStart` / `Stop` / `SessionEnd` | INSERT an event | none |
| git `post-commit` | commit and diffstat | none |
| handoff | read request ids, transcript path and worktree state, and record them | none |

`PostToolUse` is installed but only releases a permission wait. It writes no event row, costs one stat when nothing waits, and is about 0.08 s per tool call (measured).

### Fold is a destructive rewrite: validation is required

An atomic write prevents only a "half-written file". It **cannot stop a fully written garbage file from overwriting a good one.**

```
1. Schema check: the 5 fixed section headers exist
2. Monotonicity check: no `Done` item of the previous digest may have vanished
3. On failure, roll back with git + a degraded fallback
```

And the digest **always carries a deterministic block independent of the model's output**: branch, HEAD, the last 5 commits, changed files, recent events. So the facts remain even if the fold fails. (The handoff itself does not go through the fold and is fully deterministic.)

### Digest format

```markdown
# 2026-09-15 · backend
updated: 14:32 (handoff 3) · folded_through: event 18432

## Done / ## In progress / ## Decided / ## Blocked / ## Next / ## Base candidates
```

With fixed sections, the fold becomes "an operation that moves items between sections". With free prose the structure shifts each time and the rewrite becomes unstable.

### Midnight rollover (no model)

When the date changes, yesterday's `## In progress` **stays in progress forever.** On the 00:00 timer:

```
1. If yesterday has an unfolded delta, force one fold
2. Carry forward into today's digest only `In progress`/`Blocked`/`Next`, and empty `Done`
3. A fold that arrives after the cutoff goes into a `## Late additions` block
```

---

## 6. Slots  **[not built: replaced by measured burn rate]**

Accounts are worker slots, and **a slot pulls work from the queue.** The agent does not choose an account.
This inversion removes unpredictable budget, simultaneous expiry, attribution failure and auth conflicts at once.

```sql
CREATE TABLE lease (
  account TEXT, agent TEXT, session_id TEXT, worker_pid INTEGER,
  acquired_at INT, heartbeat_at INT, state TEXT
);
CREATE UNIQUE INDEX one_per_account ON lease(account) WHERE state='active';
CREATE UNIQUE INDEX one_per_agent   ON lease(agent)   WHERE state='active';
```

Without `one_per_agent`, the same agent holds leases on two accounts at once and two sessions write the same journal.

### Reclaim by PID, not by TTL

SQLite does not expire a TTL for us. And **a TTL expiry does not imply the process died.** Even if the daemon dies, the worker is owned by Orca, stays alive and keeps using that CONFIG_DIR. If only the lease expires, another agent takes the same slot and the **CONFIG_DIR is used twice.**

```
reclaim condition = a PID/process-group liveness check. TTL is a secondary signal
state='released' only after really ending it with SIGTERM before reclaim
also take OS-level exclusivity with flock on the account directory
run the reaper at the start of the acquire transaction + every 30 s of the daemon loop
```

A DB unique constraint does not kill a process.

### There is one arbiter

```
Wake-up and assignment are done in order by a single daemon loop that sorts the queue.
Agents do not compete for leases themselves. UNIQUE is a safety net, not a scheduler.
On acquire failure drop only that slot from the candidates and re-pick at once; QUEUED only when candidates run out
ocp start does not take a lease directly either; it puts the request in the queue at top priority
```

---

## 7. Scheduling: one metric, `runway`  **[not built: replaced by measured burn rate]**

```
headroom = max(0, (90 - pct_5h) / 100)          # the 90% cap is the same everywhere
burn     = max( EWMA(10-minute minimum window), floor per weight )
runway   = headroom / burn                       # unit: minutes

effective_runway = (reset_in <= 15 min) ? reset_in + next_window_runway : runway
weekly correction = x min(1, weekly_headroom / 0.2)
```

**Both assignment and ordering use `effective_runway` alone.**

```
heavy: effective_runway >= 60 min
light: effective_runway >= 15 min
a slot with pct_5h >= 90 is excluded at candidate generation
order: effective_runway descending
```

The floor on `burn` matters. Without a floor two failures follow. If everyone is PARKED the burn rate is 0, so runway is infinite, so everyone passes, so 90% in 3 minutes, and then it oscillates. At a cold start with zero samples there is a **circular-wait deadlock**: to get a burn rate you must run, and to run you need a burn rate.

A slot with no active block is **defined explicitly as a top candidate** with `runway = ∞`.

### Queue

```
prio = blocks + wait_minutes / 30
match = "for one slot, walk the queue and take the first eligible agent"   (no head-of-line blocking)
add the number of skips to the aging
min_runway_eff = max(3 min, min_runway - waited_minutes/20)   (starvation relief)
```

Without the last line, aging **only changes the order**, cannot get through the gate, and starvation never clears. Progress can be proven only as "the threshold is eventually always met".

### Handoff is a preemption point

At handoff the lease is returned to the queue and re-picked. Otherwise the only occasions to release a lease are `worker_done` and `reaching 90%`, and a waiting agent gets no chance for a full 5 hours. A `max_lease_minutes` cap is also set.

---

## 8. Triggers, state machine, resume

### 8.1 Two triggers, level-based

```
context : context_pct >= 60%          ← replaces cache expiry (appendix A-2)
usage   : pct_5h >= 100  (or pct_7d >= 100, or a turn that ended on the limit)   ← at the wall only
```

Implemented as an edge trigger (did it cross the boundary this tick?), **a crossing that passed while the daemon was down is never detected.** Define them as conditions.

A working agent is not moved at a percentage. It runs to the wall (a turn that ended on the limit, or a read of 100%), and nothing is lost since the work stays in the transcript. An unreadable account is not a wall. Whether to move at the wall is a cost comparison, not a line: if no work is waiting, or the account resets within T (39 minutes, the cold-start cost measured since), it stays parked; otherwise it moves just once. Account choice filters by the active-agent cap (default 2) and by whether the account's current fill rate (burn) lasts 30 minutes or more even with one more agent on it, then goes in the order whose weekly headroom expires first.

### 8.2 State machine

```
IDLE → QUEUED → RUNNING → DONE
                   │
                   ├─ context / usage ──▶ HANDOFF
                   │                          │
                   │            validated ─────┼──▶ re-pick slot ──▶ RUNNING
                   │                          │         (no candidate)
                   │            fail·timeout ─┴──▶ DEGRADED ──▶ PARKED
                   │
PARKED    ──wake_at──▶ QUEUED      (IDLE if no request or reply is waiting)
THROTTLED ──budget window rollover──▶ QUEUED      (automatic; IDLE if no request or reply is waiting)
STALLED   ──only a person releases──▶ QUEUED
```

**There must be a failure edge out of HANDOFF.** Without one, handoff failures (a fact that could not be read) and timeouts all converge here, and the agent holds the slot forever in no state at all.

### 8.3 Handoff timeout

```
timeout 90 s (judged by the clock alone: there is no model call, so nothing to wait for)
on timeout: (1) record as degraded  (2) if it repeats, THROTTLED
```

### 8.4 Who wakes PARKED

**If only "dispatched" states are evaluated, then when everyone is PARKED the set to evaluate is empty.** The lease-release event that triggers a resume also never happens again, since active leases are 0. The system stops for good with the accounts empty.

```
evaluate = RUNNING, HANDOFF, PARKED, QUEUED, THROTTLED
persist agent.wake_at in SQLite (the earlier of the reset time and lease expiry)
at daemon start, one reconciliation, unconditionally:
  - full scan of wake_at <= now
  - compare the lease table against the workers actually running
```

An in-memory timer is only an optimization and cannot be the basis of correctness. This one reconciliation guards against "a trigger that passed while the daemon was dead", "a zombie lease" and "a lost timer" **at once.**

### 8.5 Counters count the right thing

"A cap of 5 dispatches per task" **counts normal continuations, not failures.** If handoffs are frequent for lack of accounts, a task hits the cap while advancing smoothly: the harder the system works, the sooner it stops.

```
failed_attempts : +1 only on a crash, a gate refusal or an error exit. Cap 3
continuations   : caused by handoff. Unlimited
runaway guard is by resource, not by count: task_token_budget / task_wallclock_budget
the resume budget counts only dispatches that succeeded: a failed re-evaluation is free
```

Without the last line, **everyone wakes at the reset moment, loses the race, and those failures make them STALLED.** At the very moment the resource recovers, three quarters of the system stops.

### 8.6 THROTTLED and STALLED

```
THROTTLED : a resource problem. At budget-window rollover, automatically → QUEUED (IDLE if no request or reply is waiting). Cap of K in a row + exponential backoff
STALLED   : a defect in the task. Only a person releases it. gate-create only after K failed THROTTLED auto-releases
```

Sending a budget overrun to STALLED means the budget is restored an hour later but the state is not. If it hits at 23:40, **the slots sit idle and stopped until morning**, and a system meant for unattended operation is nullified in exactly that stretch.

### 8.7 Thundering herd  **[v2]**

```
jitter of 0-30 s per agent on a reset wake-up, and wake only as many as there are slots
stagger the account block start times on purpose
limit HANDOFF entries right after a restart to 1 per second
```

Without these, the blocks line up and a **convoy** forms: every 5 hours all slots empty at once and hit 90% at once.

### 8.8 Base drift

```
git fetch && git rev-list --count HEAD..origin/<base>
git diff --name-only HEAD...origin/<base>

judge by file intersection, not commit count:
  drifted files ∩ the agent's touched-files ≠ ∅ → forced rebase or gate
```

`main..HEAD` is **the agent's own commits**, so it does not measure drift. The direction is reversed.

The resume header follows this too:

```
No overlap: the "Done" items below are already finished. Do not redo them.
Overlap:    the "Done" items below may be void because the base moved. Check those files first.
```

### 8.9 Persisting handoff progress

```sql
CREATE TABLE handoff (
  id TEXT PRIMARY KEY, agent TEXT, session_id TEXT, trigger_kind TEXT,
  step TEXT,                                   -- requested|recorded|extracted|released
  deadline INT, state TEXT                      -- in_flight|done|degraded
);
```

Commit each step. At startup find the `in_flight` rows and resume **from that step.** An agent with an in_flight row is excluded from trigger evaluation, and a second trigger is merged by adding a reason. The `min()` formula is not a mutual-exclusion device.

---

## 9. CLI

```bash
ocp agent register <name> --role-file ROLE.md --repo <sel> --weight heavy

ocp run create --objective "..."
ocp task add --spec "..." [--depends-on <id>]
ocp start <agent> --task <id>

ocp slot list [--json]        # includes runway
ocp slot why <agent>          # why it passed or failed the gate
ocp slot release <account> --force
ocp queue list

ocp journal show|fold
ocp resume <agent>
ocp unstall <agent>

ocp daemon [--foreground]
ocp watch
ocp status [--json]
ocp bridge [--once]
ocp doctor
```

`ocp doctor` checks: usage reading · presence of `rate_limits` · Claude Code version · `disableAllHooks` · `autoContinueAtUsageLimit` · per-account login state · symlink integrity · `base.md` over its size.

---

## 10. Token budget

| Way | Cost |
|---|---|
| Logic in SKILL.md | ~3,000 tokens of instructions resident on every relevant turn, reloaded at every compaction |
| Thin stub + CLI | ~150-token stub + only the output of the command called |

*(Rough estimate. To be calibrated by measurement in Phase 0.)*

- `SKILL.md` is at most 20 lines. Detail comes only when needed, through `ocp guide <topic>`
- Status output is for people and the daemon. Never put a state dump into the model's context
- The daemon decides. The model takes no part in decisions
- **The only point where a model spends tokens = one fold**, outside the session on a cheap model

---

## 11. Dashboard

```
┌ RUNS / TASKS ───────────────┬ SLOTS ──────────────────────┐
│ ▸ auth-refactor    3/7      │ alpha  5h ███░░ 62%  1:48↻  │
│   ├ ✔ schema      backend   │        runway 71m  ✔heavy   │
│   ├ ⟳ endpoints   backend   │ beta   5h █████ 87%  0:12↻  │
│   └ ⏸ tests       reviewer  │        runway 4m   ✘        │
├ AGENTS ─────────────────────┴─────────────────────────────┤
│ backend  [beta] ⟳ endpoints  ctx 61%  ⚠handoff            │
│ reviewer —      ⏸ THROTTLED  auto-release 00:40           │
└───────────────────────────────────────────────────────────┘
```

A read-only join view. No logic. **Phase 5**: built as `ocp bridge`. To print a single frame, `ocp bridge --once`.

---

## 11-b. Closing idle sessions: memory is a different resource from context

**Added 2026-09-17.** It was not in the original design and came in from measurement.

One resident agent session holds **250 to 500 MB** (measured: 5 sessions 1.7 GB, of which two that idled all day were 554 MB).
While idle it spends not one token. So a state exists that **uses only memory and does nothing.**

**Keep it apart from handoff.** Handoff returns *context*: record three facts and `/clear`. No model call.
Closing returns *memory*: no fold, no model call, one `/exit`. The problem is not the context
but that no one uses the session, so there is nothing to summarize.

**The terminal stays.** Orca runs our launch command inside a login shell, so claude is a child of
that shell (measured: the pid tree). `/exit` kills only claude, and the shell, tab, worktree and cwd stay.
To wake it, type `ocp launch <agent> -- --resume <session>` again in the same shell. **Since the command that revives it
is ours, the account is guaranteed by structure**: we do not lean on Orca's hibernation keeping the env.

**Mark it closed only after the process is gone.** Having typed `/exit` is not the same as the session having ended:
a modal swallows it, or the exit takes longer than one sweep tick (once on 2026-09-19: after SessionEnd, the next sweep, and only 31 seconds later was it seen gone). We wait until it
leaves the process table (`END_WAIT_S`, 60 s) and then record it. If it never dies, we report failure with no retry and no kill
and leave `close_failed`, so for `IDLE_CLOSE_S` the schedule does not try to close it again.
If a modal is up, we do not type at all.

**The cost of reviving is 0** (measured 2026-09-17, context 68k):

| | cache_creation | cache_read |
|---|---|---|
| resident, next turn | 37 | 68,074 |
| closed, first turn of `--resume` | 37 | 68,193 |
| `--resume` with the preamble changed | 377 | 68,286 |

The cache hangs on the server-side prefix, so it is **independent of whether the process lives or dies.** "Reviving causes a cache miss"
happens because the TTL ended, not because we closed it, and that would happen just the same if we had kept it alive. The third row matters too:
changing `--append-system-prompt` cost 377 tokens, not a full rewrite. So on waking we **may pass the
handoff, session id and transcript path along freely.**

**Four cases we do not close**: working (RUNNING), in handoff, STALLED (a person must look), and a received request waiting.
Otherwise it is closed once `IDLE_CLOSE_S` (default 1800 s) has passed since the last activity. The evidence of activity is
**the agent's own hook events**, not the daemon's bookkeeping.

**What we give on waking**: a revived session gets only "nothing was lost", a new session gets the handoff. Both
get the absolute path of their own transcript, with **"grep it, do not read it whole"** attached: reading it whole
refills the very context that was just returned.

---

## 12. Phase 0 verdict table

> **Updated 2026-09-16.** The evidence (commands, output) is in `docs/phase0-findings.md`.
> Measured on: macOS · Claude Code 2.1.273 · Orca 1.4.203.

| # | Assumption | Verdict |
|---|---|---|
| A | Two accounts can run at once with separate `CLAUDE_CONFIG_DIR`s | **True, confirmed by measurement (2026-09-16).** We started two accounts **at the same time** and each updated its own `status.json` correctly (alpha 5h 0%/7d 40%, beta 5h 20%/7d 49%, different sessions and costs). No credential invalidated the other |
| B | Read usage from a file | **False.** statusLine `rate_limits` is the official contract |
| D | Headless injection + clear | **True, confirmed by measurement (2026-09-16).** `orca terminal send --text "/clear"` really returns the context: on a live agent `total_input_tokens` went 45028 → 0 and the session ID changed. The terminal, worktree and account stay. On wake, injecting the saved handoff (the agent row) as the first prompt lets it carry on |
| E | One JSONL line = one API request | **False.** Dedup by `message.id` is required. **But the §8.1 trigger no longer uses the transcript** (see I): dedup is needed only for the burn-rate calculation |
| F | `autoContinueAtUsageLimit` does not conflict with §8 | **The feature exists** (a settings key in the binary). It forces a choice, and `ocp doctor` checks it |
| G | `rate_limits` on the `CLAUDE_CODE_OAUTH_TOKEN` path | **Unchecked.** No token. Needed only when accounts run short |
| H | `~/.claude.json` follows `CLAUDE_CONFIG_DIR` | **True.** It is created inside the profile and home is unchanged. Isolation is complete |
| I | Can context usage come from the statusLine | **True, better than expected.** `context_window.used_percentage` arrives **directly**, no calculation. Before a turn it is `null` → fail-closed |
| **J0** | Does Orca force its own statusLine through the CLI | **False, which favors us.** argv is a bare `claude`. Orca's integration is not a statusLine but **hooks**. The collector was confirmed to work |
| **J4** | Can `CLAUDE_CONFIG_DIR` be injected into an Orca agent | **Solved with a wrapper.** Orca leaves `CLAUDE_CONFIG_DIR` empty and `--command` is an arbitrary shell, so a **launcher wrapper** is all it takes. `environmentRecipes` is a remote-machine feature, so it was not an alternative |
| **J4-full** | Does Orca **recognize as an agent** one started by the wrapper | **True, confirmed by measurement (2026-09-16).** Started with `orca terminal create --command "ocp launch backend"`, Orca recorded `agentIdentity: "claude"` and changed the tab title to `✳ Claude Code`. The status line showed `alpha · 5h 0% · 7d 40%`: our collector works inside it |
| **L** | Where the credential is stored (new) | **On macOS, the Keychain, separated per config directory.** `Claude Code-credentials-<sha256(config_dir)[:8]>` (only the default directory has no suffix). No `.credentials.json` file is created |
| **K** | Can Orca's native multi-account replace this | **False.** It is implemented but has **one active account per runtime** (global swap). There is no `--account` flag. Keep the `CLAUDE_CONFIG_DIR` way |

[claude-code#75408]: https://github.com/anthropics/claude-code/issues/75408

*(C = cache-expiry calculation: the trigger itself was deleted in appendix A-2, so no verification is needed)*

---

## 13. Roadmap

| Phase | Output | Done when |
|---|---|---|
| **0** | `phase0/probe.sh` (local, not in the repo): H · I · J0-J3, then A (long run) · D · E · F · G | Verdict table updated. Execution layer fixed |
| **1** | statusLine collector, `agent register`, `doctor` | Two accounts' state is exact in status.json. Fail-closed confirmed |
| **2** | Fixed mapping, `team.yaml`, `ocp task new`/`ocp say` | Three agents exchange requests, each on its own account |
| **3** | Hook events, fold, validation and rollback, midnight rollover | Zero lost or duplicate events with zero model tokens |
| **4** | Daemon: level triggers, handoff, park, resume at reset, THROTTLED | Full state recovery after a daemon SIGKILL. Unattended park and resume on reaching 90% |
| **5** | TUI | — |
| **v2** | Slots, leases, queue (§6, §7) | When agents > accounts x 2 |

Phases 1 and 2 alone already have real value. Phase 4 is the real gain.

---

## 14. Open decisions

1. **Accounts vs agents.** Whether the v1 fixed mapping holds (§0.5). If agents exceed twice the accounts, v2 must be brought forward
2. **`autoContinueAtUsageLimit`**: manage it ourselves, or delegate to Claude Code
3. **Context trigger threshold 60%.** Adjust after measuring the reload cost in Phase 0
3-b. **May `reviewer` and `archivist` share one account?** Both are light, but if they attach at the same time it is the credential race condition of §1. Only because `archivist` is ephemeral is the overlapping window short, not zero. With 4 accounts, splitting them is safer
4. **Appendix A-2 (deleting the cache trigger).** A proposal that replaces the original requirement 4. The reasoning is in the appendix, and if you object it is reverted
5. **Journal retention policy.** It is a git repo, so unlimited retention is the default. Only the compaction time needs deciding

---

## Appendix A. What was rejected, and why

These were in up to v0.3 and removed in v0.4. A record for when someone proposes them again.

**A-1. `score = headroom / max(time_left, 0.05)`.** It is a **pace ratio**, not absolute room, so it inverted the intent. Even an empty account at pct=0 was ineligible for heavy for 50 minutes after the reset, while an account at pct=89 fifteen minutes before the reset passed with a score of 2.2. The less room, the higher the score. If `runway` alone does both assignment and ordering, this distortion cannot arise.

**A-2. Cache-expiry trigger (original requirement 4).** The real extra cost of a cache miss is `N x (cache write 1.25x - cache read 0.1x) ≈ 1.15N`, and it is **one-off.** The next turn rebuilds the cache, so it does not repeat. A handoff, by contrast, costs a fresh-session reload (10 to 30k).
→ When the context is small, **just ignoring** the cache expiry is cheaper, and when the context is large you must hand off regardless of the cache. **In both cases the cache does not affect the decision.** So the trigger was unified to `context_pct`.
As a side effect, `prompt_cache` monitoring, `lead_time` validation (the bug where `lead_time >= ttl` hands off every turn), `min_handoff_interval`, and handling of the TTL 1h↔5m dynamic switch **all became unnecessary.** `prompt_cache` stays only for the dashboard display.

**A-3. `max_concurrent ≥ 2` / sub-quota sharing.** Copying a credential causes a re-authentication cascade through refresh token rotation. The symptom is "random re-auth prompts", so tracing the cause is nearly impossible. When accounts run short, solve it by queueing, not sharing.

**A-4. `pooled:N` intake.** With few accounts N=1 anyway, so it equals `dedicated`. It comes back out when slots are left over.

**A-5. `blocking` response.** The requester holds a slot and an account assignment while it plays. In a system with finite accounts that is the most wasteful loss. If kept as an option, someone will use it.

**A-6. `sticky` account affinity.** The cache is per session, not per account, so there is almost no benefit in returning to the same account. The one benefit, folder-trust state, disappears if `doctor` plants it on every account in advance.

**A-7. An `events.jsonl` file + a byte-offset cursor.** Moved to SQLite it becomes one line, `WHERE id > cursor`. With a file we would have to handle separately the boundary for events arriving during a fold, a hook's late append, and file order disagreeing with timestamp order.

**A-8. A digest `.md.1` generation file.** With `~/.orcapipe` as a git repo, generation retention, rollback and diff are free.

---

*v0.5 · 2026-09-16: v1 scope reduced*
