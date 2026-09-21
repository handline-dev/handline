# What the Orca CLI gives a caller that runs agents

Part 1 of the #1255 study. This document is about **Orca only** - what its CLI
offers, what it already knows about a running agent, and where "which agent
vendor" is a parameter it takes. What ocp does with any of it is part 2; the
facts here are stated without a verdict on our code.

Method: `orca agent-context --json` (schema v1, 234 commands) read whole, plus
live probes against this machine. Everything below that is quoted was read from
a running system, not from documentation.

    orca 1.4.205, runtime ef70a57f, app running, runtime ready
    read on 2026-09-21 from /Users/ron/orca/workspaces/orcapipe/orcapipe.expender

---

## 1. The shape of the surface

234 commands. They divide into two piles that have nothing to do with each
other, and only the first is about running agents:

| pile | groups | count |
|---|---|---|
| **running and watching agents** | `orchestration` (30), `terminal` (11), `worktree` (7), `automations` (7), `skills` (6), `project` (7), `repo` (5), `environment` (4), `agent` (4), `account` (2), `host`, `diagnostics`, `claude-teams`, `status`, `open`, `serve` | ~110 |
| **things an agent can drive** | browser (`tab`, `snapshot`, `click`, `storage`, … ~60), `computer` (14), `emulator` (16), `linear` (27), `artifacts` (5) | ~124 |

The second pile is capability for an agent to *use* and is out of scope here.

Every command takes `--json`, and every reply is the same envelope:

```json
{ "id": "<request id>", "ok": true, "result": { … } }
```

Errors come back with `ok: false` and a code. Mutating verbs also take
`--retry-request <id>`: after an ambiguous failure you reissue the exact same
command with the id it reported and Orca will not perform the action twice. The
id is bound to the payload **and** to the exact terminal process incarnation,
so it cannot leak across a restart.

---

## 2. Three layers for putting an agent somewhere

Orca offers the same job at three altitudes. They are not alternatives so much
as a stack, and a caller can enter at any level.

### Layer 1 - a terminal running a command

```
orca terminal create --worktree path:/…   --command "ocp launch backend"
orca terminal create --worktree active    --command "codex"
orca terminal create --worktree path:/…   --command "opencode" --focus
```

Orca's own examples use `codex` and `opencode` here. At this layer Orca has no
opinion about what the command is: it makes a tab, runs the string, and hands
back a handle. Everything else is `terminal send` / `read` / `wait`.

`terminal split --command` does the same inside an existing tab.

### Layer 2 - a worktree with an agent in its first terminal

```
orca worktree create --name agent-task --agent codex --prompt "hi" --json
```

`--agent <id>` launches "a known TUI agent"; `--prompt` delivers the first
instruction to it. `result.agentTerminalHandle` carries the handle. This is the
first level where Orca knows *which agent* is running, because it started it.

### Layer 3 - a supervised worker under a Run

```
orca orchestration worker-start --spec "…" --worktree new-child \
      --agent codex --model <id> --effort high --json
```

This is the whole thing: it creates (or reuses) the workspace, runs setup
hooks by policy, launches the named agent with a model and effort, creates the
Task, dispatches it, and supervises the process from then on. `--on
<saved-environment>` puts it on another machine instead.

Its note is explicit that model ids are multi-vendor:

> `--model` supports Claude, Codex, and Cursor opaque provider model ids;
> `--effort` requires `--model`. Neither can combine with `--terminal`.

and that the lifecycle is uniform whatever started it:

> Drive every worker the same way whichever way it was started: the same
> orchestration verbs, the same handle. Mail, dispatch, worker-show,
> worker-read and the whole lifecycle behave identically.

### supervised vs unsupervised - the distinction that matters

A worker Orca *started* (`worker-start`) is **supervised**: Orca owns the
terminal, accounts for it as a resource, can stop and release it.

A terminal you made yourself and then bound with `orchestration dispatch
--task … --to <handle>` is **unsupervised** (stage `context_only`). It appears
in every listing, carries the same dispatch id and the same mail address, and
`worker-read` reads it - but Orca will not claim to own its process:
`worker-stop` fences it without killing anything, `worker-release` reports it
`retained` and takes no process action, and `resource` is
`{"state": "absent", "reason": "unsupervised"}`.

Both are first-class. The choice is about who owns the process, not about how
much of the API you get.

---

## 3. Where the agent vendor is a parameter

This is the inventory that decides whether a second vendor is Orca-supported or
something a caller has to carry alone.

| command | vendor surface | evidence |
|---|---|---|
| `account add` | `--agent claude\|codex`; runs `claude login` / `codex login`, then registers the account with the runtime. Codex uses device authorization | usage + notes |
| `account list` | "managed Claude and Codex accounts on this Orca host" | usage |
| `worktree create` | `--agent <id>` launches a known TUI agent; examples use `codex` | usage |
| `worker-start` | `--agent <agent>`, `--model <id>`, `--effort <level>`; Claude, Codex **and Cursor** model ids | notes |
| `automations create` | `--provider <agent>`; examples pass `--provider codex` | examples |
| `skills install` | `--agent <name>[,<name>]`, example `--agent claude-code,codex`; `--agent universal` for the shared `.agents/skills` dir | usage |
| `agent hooks prepare-codex` | "Repair Orca-managed Codex hook trust before a shell launch" - a codex-only verb | summary |
| `orchestration send` | group addresses include `@codex` alongside `@all`, `@idle` | notes |
| `terminal create` / `split` | `--command "codex"`, `--command "opencode"` in Orca's own examples | examples |
| `claude-teams` | `orca claude-teams [claude args…]` - Claude Code Agent Teams in native Orca splits; Claude-only | summary |
| worker projections | every worker row carries `provider: {id, model}` | live JSON |
| `terminal list` rows | every terminal carries `agentIdentity` | live JSON |

Runtime capabilities name vendors the CLI does not: `agent-session.kimi-resume.v1`,
`accounts.codex-reset-credit.v1`, `skills.install-providers.v1`. The runtime
model is plainly multi-vendor and older than the CLI's exposure of it.

**opencode** appears only as a `--command` string in two examples. It is
launchable as a terminal command and nothing more: no `--agent opencode`, no
account management, no hooks verb. Treat it as layer 1 only until proven
otherwise.

---

## 4. What Orca already observes about a running agent

This is the part that is easy to miss, because it is not advertised as
observation - it arrives inside listings.

### 4.1 `terminal list --json` - 31 rows on this machine, one call

Each row:

```
handle  ptyId  incarnationId  orphaned  worktreeId  worktreePath  branch
tabId   leafId  connected  writable  lastOutputAt  title  preview  agentIdentity
```

- `agentIdentity` is a bare vendor string. On this machine: `"claude"` on 9
  rows, `null` on 22. Orca tells you which terminals hold a coding agent and
  which vendor, for free, for all of them at once.
- `title` carries a live activity glyph for agent panes - `◑ Session handoff
  omega`, `✳ Orcapipe manager`, `1 awaiting input · claude agents` - while
  plain shells fall back to a truncated path (`..p/harbor.deck`).
- `lastOutputAt` is a millisecond timestamp of the last output.
- `preview` is a short tail of the pane.

### 4.2 `worker-list` / `worker-show` - the structured view

A real row, read from this machine (the dispatch is one ocp created):

```json
{ "dispatchId": "ctx_0ae189c0a7ea", "taskId": "task_1b8dfa4fb53b",
  "runId": "run_5f5c6f452853",
  "workerState": "unsupervised", "dispatchStatus": "dispatched",
  "agentTerminalHandle": "term_5e23a06d-…", "terminalState": "retained",
  "projection": {
    "provider":  { "id": "claude", "model": null },
    "host":      { "kind": "local", "id": "local" },
    "workspace": { "id": "<repoId>::/Users/ron/orca/workspaces/…", "kind": "folder_or_worktree" },
    "stage":     { "worker": "unsupervised", "dispatch": "dispatched", "activity": "working" },
    "outcome":   "in_progress",
    "liveness":  { "verdict": "live", "observedAt": 1789976858918, "source": "agent_status" },
    "evidence":  { "durable": true, "liveStatus": "fresh", "lastObservedAt": … },
    "resource":  { "state": "absent", "reason": "unsupervised" },
    "nextAction":{ "kind": "inspect", "argv": ["orchestration","worker-show","--dispatch","ctx_…"] },
    "attention": { "categories": [], "requiresAction": false } } }
```

Four fields here answer questions that otherwise cost a screen read each:

- **`stage.activity`** - `working` / idle. Derived from agent status hooks, not
  from parsing a TUI.
- **`liveness.verdict` + `.source`** - is the agent alive, and *how Orca knows*
  (`agent_status` here). `evidence.liveStatus` says whether that observation is
  `fresh` or stale, so "I could not look" is distinguishable from "it is gone".
- **`attention.requiresAction` / `.categories`** - the agent needs a human.
  `worker-show`'s note names the richer form: `observation.agentWait`
  "names a worker parked on a prompt only a human can answer, with the evidence
  that proved it (**hook, prompt-text, or title**). Null means Orca looked and
  found no wait. An absent field means it never looked."
  Three-valued and with provenance - the same fail-closed distinction a caller
  would otherwise have to build itself, and vendor-neutral because Orca, not
  the caller, owns the per-vendor evidence.
- **`resource`** - process accounting, reported separately from task status,
  because "a completed Task can still own a live terminal".

`terminal-state` is its own enum: `active | reclaimable | retained |
release_pending | release_unknown | released`.

### 4.3 Reading output

`terminal read` has two sources and they are not interchangeable:

- default: **accumulated stream**, escape sequences stripped, pageable with
  `--cursor` / `nextCursor`, with `oldestCursor` reported when lines were
  dropped. Its own note warns that any repainting program "comes back as
  stacked fragments", so it is unsuitable for judging what is *rendered*.
- `--screen`: the **current frame**. No history, and `--cursor` is refused
  with it.
- the result says which you got: `source: stream | screen | screen-unavailable`.
- `draft` is returned separately - the UI composer's text, deliberately
  excluded from `tail`, and "never treat it as terminal output or a submitted
  instruction."

`worker-read --dispatch … --source auto|transcript|terminal` is the structured
sibling: `auto` "uses an exact hook-reported transcript when available and
otherwise returns labeled terminal output". A transcript read works even for a
worker that has no terminal at all. Its cursor is pinned to one source and
Orca reports `source_changed` rather than silently paging the wrong thing.

### 4.4 Sending input

`terminal send [--text] [--enter] [--interrupt] [--wait-submit <seconds>]`

> For a text-plus-Enter agent prompt, the result separates input acceptance
> from observed submission and turn start.
> `--wait-submit` only observes the accepted prompt for the requested duration;
> timeout returns the queued/input-accepted receipt and never resends.

So "typed" and "landed" are two different facts Orca will report separately,
and the wait never causes a resend. `--interrupt` is a first-class flag rather
than a control byte in `--text`.

`terminal wait --for exit|tui-idle --timeout-ms` is the blocking condition.
Only those two conditions exist.

---

## 5. Mail, tasks and gates

Orca has a whole inter-agent messaging system, independent of terminals.

**Mail.** `send` / `check` / `reply` / `ask` / `inbox`.

- typed: `status, dispatch, worker_done, merge_ready, escalation, handoff,
  decision_gate, question, heartbeat`, plus `--priority` and `--thread-id`.
- addressed to `run:<id>`, `dispatch:<id>`, or a group - `@all`, `@idle`,
  `@codex`, `@worktree:<id>`. Group addresses exclude the owning coordinator.
- `check` is a FIFO batch with explicit `--ack`, `--peek`, `--all`, and
  `--wait` with `--types` as the wake condition. A bound Run replays the same
  batch until acknowledged. `--wait` emits `_keepalive` JSON lines on stderr
  every 15s so a caller can tell the process is alive.
- `ask` blocks until answered and survives timeout: the question stays pending
  and is resumed by its message id.
- `worker_done` requires `--outcome succeeded|failed` and cannot target a group.

**Tasks.** `task-create` (with `--parent` and `--deps <json_array>` - a real
DAG), `task-list --status --ready`, `task-update --id --status <pending | ready
| dispatched | completed | failed | blocked> [--result <json>]`, and
`dispatch --task --to <handle> [--inject] [--return-preamble] [--dry-run]`.

`--inject` is what makes Orca deliver the task spec into the terminal itself;
omitting it leaves the spec as card metadata only.

**Gates.** `gate-create --task --question [--options <json_array>]`,
`gate-list`, `gate-resolve --id --resolution`. A first-class "this task is
blocked on a decision" object, drawn by Orca, resolvable from outside.

**Runs.** A Run is "a namespace and home inbox. It never schedules or places
workers." `run-create` both creates **and binds the calling terminal**;
`run-use --id` binds an existing one; `run-current` reports this terminal's
binding. Binding is per terminal, and it is what `--run` defaults to.

---

## 6. Fleet and placement

- `host list` - "this machine, the SSH targets registered on it, and the Orca
  servers paired with it", with the flag that reaches each kind: a paired
  server is `--environment <name>`, an SSH target is `--host ssh:<id>`. SSH
  rows report detected platform and connection state.
- `environment add/list/show/rm` - the pairing store for remote runtimes.
- `worker-start --on <saved-environment>` - start the worker on another
  machine while the Run stays here.
- `worker-list --include-remote` - fleet listing with opaque `nextCursor`
  paging, capped at 100 local rows.
- `worktree list` / `worktree ps` - every row carries `host=`, and the trailing
  `scope:` line names the hosts the page covers **and the ones it does not**,
  because "an empty answer for it is not evidence that it has none".
- `automations create --provider <agent> --trigger <preset|cron|rrule>` with
  `--precheck` (exit 0 continues, anything else records a skipped run),
  `--workspace` to reuse a worktree, `--reuse-session` / `--fresh-session`.
  A scheduler with a provider parameter and a cheap guard, already built.
- `diagnostics memory` - "the same host process sweep used by the Resource
  Usage popover", point-in-time rather than a heartbeat.

---

## 7. Hooks and skills

`agent hooks on | off | status | prepare-codex`. Orca installs status hooks
into each agent's own config so its panes report to the runtime - this is the
`agent_status` source behind `liveness` and `activity` above. Codex needs the
extra `prepare-codex` step because codex stores a per-entry `trusted_hash` and
refuses an untrusted hook.

`skills install --skill <name> --agent claude-code,codex` resolves to the
community `npx skills add …` command with non-interactive flags, installs
globally by default, and offers `--agent universal` for the shared
`.agents/skills` directory alone. `skills list` / `get <topic>` print Orca's
own bundled guides locally - `orca-cli` and `orchestration` are the two that
describe this surface from Orca's side, and `skills installed` / `share`
handle publishing.

---

## 8. State observed on this machine

Facts, recorded here because they are evidence about how the Run model behaves
in practice. What they imply for ocp belongs to part 2.

- **Five Runs all named `orcapipe team`**, `consumer_generation` 1-3, created
  between 2026-09-19 and 2026-09-20, each with a different
  `coordinator_handle`. Runs are bound per terminal, so a team whose members
  each bind from their own terminal can end up spread across several cards.
- **65 tasks in the bound Run, every one `status: ready`.** Tasks are created
  and never advanced; `task-update` has not been called.
- **`worker-list` scoped to the bound Run returns 1 row** (`scope: {"run":
  "run_5f5c6f452853", "source": "bound"}`). Without a binding it would cover
  every Run; with one it covers only that Run's workers.
- The one row is an ocp-created dispatch, reported `unsupervised` /
  `context_only` / `terminalState: retained`, `provider.id: "claude"`,
  `activity: working`, `liveness: live` from `agent_status`.

---

## 9. Questions to answer before part 2

1. **Can a `worker-start` agent be given a chosen account?** `worker-start`
   takes `--agent`, `--model`, `--effort` but no environment or config-dir
   flag. If the account a session spends is not selectable there, layer 3
   cannot replace a launcher that picks an account per session - the two would
   have to be combined (`--terminal <handle>` with a terminal we made
   ourselves). Worth testing directly.
2. **What does `orca account list` know that a caller could use?** It returns
   `email`, `organizationName`, `authMethod`, `lastAuthenticatedAt` per
   account, under `claude` and `codex` keys. Does registering an account there
   let Orca *place* a session on it, or is it only a record?
3. **Is `observation.agentWait` populated for unsupervised dispatches?** The
   live row showed `attention.requiresAction: false` and no `observation` key.
   If `agentWait` only fills for supervised workers, that changes what a
   terminal-owning caller can rely on.
4. **Does any CLI verb expose `agent-session.status-feed.v1`?** The runtime
   advertises a structured agent-session family (`status-feed`, `structured`,
   `resume-history`, `rewind`, `prompt-cancel`, `session-boundary`) that the
   CLI does not obviously surface. If some of it is reachable, it is a better
   source than reading screens.
5. **Does Orca report usage or rate limits anywhere?** Nothing in the 234
   commands mentions rate limits, quota or usage percentages. Confirm the
   absence: it decides whether quota accounting stays a caller's problem for
   every vendor.

---

## 10. Summary

Orca is not a terminal multiplexer that happens to run agents. It has a
first-class agent model - provider, account, worker lifecycle, liveness with
provenance, attention, resource accounting, typed mail, a task DAG and decision
gates - and that model was built multi-vendor: Claude and Codex are equals in
account management and hooks, Cursor is a peer in model selection, and the
runtime already names Kimi. opencode is a command string and nothing more.

The one thing conspicuously absent across all 234 commands is **usage and rate
limits**. Orca will tell you what an agent is, where it is, whether it is
alive, whether it is working and whether it needs a human. It will not tell you
what it has spent.

Part 2 takes this list and asks what ocp uses, what it rebuilt, and what a
second vendor would cost.
