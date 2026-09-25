# What the Orca CLI gives a caller that runs agents

Part 1 of the #1255 study. This document is about **Orca only** - what its CLI
offers, what it already knows about a running agent, and where "which agent
vendor" is a parameter it takes. What ocp does with any of it is part 2; the
facts here are stated without a verdict on our code.

Method: `orca agent-context --json` (schema v1; 234 commands when first read,
**236** on 2026-09-21) read whole, then
Orca's own bundled guides (`orca skills get orchestration` and `orca-cli`),
then live probes against this machine, then - where the CLI turned out to
understate the product (§3.1) - the installed app bundle and Orca's public
issue tracker. Everything quoted below was read from a running system or from
Orca's own repository, not from third-party write-ups.

    orca 1.4.205, runtime ef70a57f, app running, runtime ready
    read on 2026-09-21 from /Users/ron/orca/workspaces/orcapipe/orcapipe.expender

---

## 1. The shape of the surface

234 commands, in two piles that have nothing to do with each other. About 110
are for **running and watching agents**: `orchestration` (30), `terminal` (11),
`worktree` (7), `automations` (7), `skills` (6), `project` (7), `repo` (5),
`environment` (4), `agent` (4), `account` (2), plus `host`, `diagnostics`,
`claude-teams`, `status`, `open`, `serve`. The other ~124 are capability for an
agent to *use* - browser (~60), `computer`, `emulator`, `linear`, `artifacts` -
and are out of scope here.

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

Its limit is argv: it "uses Orca's configured launcher; it has no per-call
model/effort flags or arbitrary Codex argument forwarding". Anything that needs
its own command line drops back to layer 1.

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

### 3.1 The CLI is the narrowest view of it

Reading the CLI alone understates Orca badly, and it is worth knowing by how
much before concluding anything about a second vendor.

Orca's app bundle carries the real registry of agent families it understands
(`/Applications/Orca.app/Contents/Resources/app.asar`) - **18 of them**:

    claude  codex  gemini  antigravity  amp  opencode  mimo-code  cursor  pi
    omp  prime-agent  droid  command-code  grok  copilot  hermes  devin  kimi

and it knows each one specifically, not generically. Per family it carries the
hook event name that means "a turn started" (`claude` → `UserPromptSubmit`,
`opencode` → `SessionStart`, `gemini` → `BeforeAgent`, `antigravity` →
`PreInvocation`, `amp` → `agent.start`, `cursor` → `beforeSubmitPrompt`,
`mimo-code` → none), plus per-family session scanners
(`session-scanner-opencode-sqlite`), event parsers
(`opencode-family-events.js`), terminal-title derivation
(`opencode-terminal-title.js`) and an icon. That is the machinery behind
`agentIdentity`, `stage.activity` and `liveness.source: agent_status` in §4 -
and it is vendor-plural by construction.

So support is **three tiers, not two**:

| tier | what it covers | who is in it |
|---|---|---|
| 1. any terminal command | it runs, you drive it with `send`/`read`/`wait` | anything at all |
| 2. **known agent family** | identity, activity, liveness, hooks, session scan, title, icon | the 18 above, **opencode included** |
| 3. **orchestration launcher** | `--agent`, `--model`/`--effort`, managed accounts | **Claude, Codex, Cursor only** |

The gap between tiers 2 and 3 is not an accident of documentation; it is a
known, filed limitation. Orca issue
[#19823](https://github.com/stablyai/orca/issues/19823) (open, 2026-09-10) asks
for exactly it: no `--agent opencode` launch path, `--model` rejects
non-Claude/Codex/Cursor provider ids, `account list` surfaces no opencode
accounts, and `agent-context --json` "reflects the same three-agent world".
Its consequence is stated plainly - teams on opencode "cannot use Orca
supervised orchestration (Run/Task/Dispatch, worker_done settlement)" and fall
back to coordinator subagents, losing the durability and audit properties.

**So: opencode is well supported as an agent and unsupported as a supervised
worker.** It gets everything in §4 for free; it does not get layer 3. Two of
the three things a caller might want from Orca are already there for it.

Runtime capabilities name vendors the CLI does not either -
`agent-session.kimi-resume.v1`, `accounts.codex-reset-credit.v1`,
`skills.install-providers.v1`. The runtime model is multi-vendor and older than
the CLI's exposure of it, which is the single most important thing to carry
into part 2: **do not size a vendor's support by what `--help` prints.**

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
  (`agent_status` here). Three verdicts, `live` / `unverifiable` / `exited`, so
  "I could not look" is distinguishable from "it is gone";
  `evidence.liveStatus` says whether the observation is `fresh` or stale. This
  is the **agent** verdict - see §5.4 for the layering against PTY liveness.
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

`terminal wait --for exit|tui-idle --timeout-ms` is the blocking condition -
only those two exist - and read `wait.satisfied`, not the fact that something
printed, because a timeout still prints a normal result. It is explicitly
vendor-generic: the guide names it for "agent CLIs such as **Claude Code,
Gemini, Codex, OMP, Pi, and Grok**". Idleness is one of the few things a second
vendor would not have to re-teach.

---

## 5. What `orchestration` actually is

The 30 `orchestration` verbs are not a grab bag. They are one contract, and
Orca ships its own specification of it: `orca skills get orchestration`
(with seven bundled references, `--reference <name>`). Everything quoted in
this section is from that guide.

> Orchestration is Orca's structured coordination layer. It records **who owns
> work, which attempt is authoritative, and when supervised work has settled.**

### 5.1 Three nouns

| noun | what it is | what it is not |
|---|---|---|
| **Run** | "a durable namespace and coordinator inbox" | "it does not schedule or place workers" |
| **Task** | the work | not an attempt |
| **Dispatch** | **one authoritative Task attempt** | not a terminal |

> Lifecycle authority comes from the active Dispatch, not a terminal title,
> copied ID, old database row, provider transcript, or visible pane.

That sentence is the whole point of the layer. A Dispatch id is the capability:
it says which attempt may speak for a Task, so a stale pane or a second try
cannot settle work it no longer owns.

### 5.2 Four roles, and only one of them is orchestration

The guide opens by telling the caller **not** to use orchestration:

| context | role | route |
|---|---|---|
| user asks to supervise, monitor, wait for results, coordinate a DAG, use a gate | **Coordinator** | the supervised loop |
| the prompt carries a live injected preamble with Task and Dispatch IDs | **Dispatched worker** | obey the preamble |
| user asks to **hand off ownership** or start another agent/worktree **without supervision** | **Handoff owner** | use `orca-cli`; **"create no Run, Task, or Dispatch and do not monitor completion"** |
| no live preamble, no explicit supervision | ordinary terminal agent | "do not emit lifecycle messages" |

> Model or effort selection does not make a handoff supervised.

So the Run/Task/Dispatch objects are meant to be created **only** by something
that will then run the supervised loop and settle them. Creating them and not
settling them is outside the contract, not a lighter use of it.

### 5.3 The supervised loop

```text
orca status --json
orca orchestration run-create --objective "<objective>" --json
orca orchestration worker-start --spec "<worker A task>" --worktree current --agent codex  --json
orca orchestration worker-start --spec "<worker B task>" --worktree current --agent claude --json
orca orchestration check --wait --types "worker_done,escalation,question" --timeout-ms 900000 --json
```

Start the whole independent wave first, then wait once. `check` returns the
bound Run's oldest FIFO Delivery and **replays that batch until `--ack`**, so
every message must be processed before acknowledging. Per settled worker the
coordinator must then do exactly one of: reuse the terminal for a follow-up
Dispatch, `worker-retain`, or `worker-release`. The turn does not end while
`worker-list --terminal-state reclaimable` still returns rows.

A worker's obligations are equally explicit: do only the current Task, use the
preamble's `ask` for a blocking question (never a local TUI the coordinator
cannot answer), heartbeat only at the given cadence, `check` for follow-ups at
each checkpoint **and once more immediately before finishing**, and send
`worker_done` exactly once with both ids and an explicit
`--outcome succeeded|failed` - "never encode failure only in prose".

> A valid `worker_done` settles the Task and Dispatch automatically; do not
> follow it with `task-update --status completed`.

`task-update` is therefore for callers outside the supervised loop; inside it,
settlement is the worker's own report.

### 5.4 The safety floor

The doctrine is fail-closed, and it is stated in the same terms ocp uses:

> **Safe failure:** preserve work and authority and report the state as unknown
> or `unverifiable`. Only positive proof of exit authorizes stop, abandon, or
> retry, and only an accepted settlement authorizes release. Every other
> observation, absence included, is a checkpoint.

Three liveness verdicts, `live` / `unverifiable` / `exited`, and
**"contact loss is not process death"**. `unverifiable` is absence, and
"absence never authorizes stop, abandon, retry, or release."

Liveness is also **layered**, which the CLI help does not say:

> `worker-list`'s `projection.liveness` is the fleet verdict for the *agent*;
> `worker-show`'s `observation.status` is **PTY liveness only**. A live
> terminal can still hold a dead or stuck agent.

Each row also carries `projection.nextAction` as **literal argv** to run, and
a `none` nextAction has no argv: read `liveness.reason` and keep waiting.

### 5.5 The documented escape hatch

`dispatch --inject` against a terminal you made yourself is a named, supported
pattern - and the guide is blunt about its price:

> `dispatch --inject` creates authoritative Task/Dispatch context but
> deliberately keeps an operator-created process **unsupervised**: it creates no
> supervised worker resource row. `worker-show`, `worker-read`, and
> `worker-list` report the lane as `unsupervised`; `worker-stop` and
> `worker-abandon` do not close that process, and settled retain/release take no
> process action.
>
> `worker-start` is the normal path […] `dispatch --inject` […] is only for an
> expressiveness gap.

And the bridge between the two, which matters to anyone with its own launcher:

> Use `worker-start --terminal <handle>` when lifecycle ownership of an existing
> agent terminal is required.

That is the one call that turns a terminal you started - with your own argv,
your own environment, your own account - into a **supervised** worker.
`--model` / `--effort` are refused with `--terminal`, which costs nothing when
the launcher already set them. Orca also reports `launch.requested` vs
`launch.effective`: "never claim a model or effort from requested arguments
alone."

### 5.6 Mail, tasks and gates in detail

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
- delivery is honest about its own limits: "a successful `orchestration send`
  proves durable enqueue; its wake or nudge is best-effort attention only and
  does not prove the recipient read or accepted it."

**Tasks.** `task-create` (with `--parent` and `--deps <json_array>` - a real
DAG), `task-list --status --ready`, `task-update --id --status <pending | ready
| dispatched | completed | failed | blocked> [--result <json>]`, and
`dispatch --task --to <handle> [--inject] [--return-preamble] [--dry-run]`.

**Gates.** `gate-create --task --question [--options <json_array>]`,
`gate-list`, `gate-resolve --id --resolution`. A first-class "this task is
blocked on a decision" object, drawn by Orca, resolvable from outside.

**Run binding.** `run-create` both creates a Run **and binds the calling
terminal** to it; `run-use --id` binds an existing one; `run-current` reports
this terminal's binding. Binding is per terminal, and it is what `--run`
defaults to everywhere.

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

`skills install --skill <name> --agent claude-code,codex` installs a skill into
each named agent's own config, with `--agent universal` for the shared
`.agents/skills` directory alone - one command that keeps two vendors' skill
sets in step. `skills get <topic>` prints Orca's own bundled guides locally;
`orca-cli` and `orchestration` are the two that specify this surface.

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

Read these against §5.2 (who is supposed to create Run/Task/Dispatch at all)
and §5.5 (what `dispatch --inject` deliberately does not buy) in part 2.

---

## 9. What is left, and it needs measuring rather than reading

The CLI schema and Orca's own two guides are now read whole. Three things
remain, and none can be settled by reading - each needs a live agent:

1. **What does adoption actually buy?** `worker-start --terminal <handle>`
   (§5.5) is documented, but not what an adopted worker then reports. Does it
   get a `resource` row, does `worker-stop` / `worker-release` act on its
   process, and does `observation.agentWait` fill for it? On the unsupervised
   dispatch read here, `resource` was `absent` and there was no `observation`
   key at all. This is the single measurement that decides how much a caller
   with its own launcher can hand to Orca.
2. **Is `orca account list` a placement tool or only a record?** It holds
   `email`, `organizationName`, `authMethod` and `lastAuthenticatedAt` per
   account under `claude` and `codex` keys. Whether registering an account
   there lets Orca *start a session on it* is the difference between Orca
   owning multi-account placement and a caller owning it.
3. **Is the `agent-session.*` capability family reachable?** The runtime
   advertises `status-feed`, `structured`, `resume-history`, `rewind`,
   `prompt-cancel`, `session-boundary`. The CLI does not obviously surface
   any of it. If part of it is reachable, it is a better source than any
   screen read.

Questions 1 and 3 are best answered while doing part 2, against an agent we are
already running, rather than by standing up a probe for their own sake.

---

## 10. Summary

Orca is not a terminal multiplexer that happens to run agents. It has a
first-class agent model - provider, account, worker lifecycle, liveness with
provenance, attention, resource accounting, typed mail, a task DAG and decision
gates - and that model was built multi-vendor from the bottom: **18 agent
families** it understands specifically, each with its own hook event, session
scanner and title derivation.

Support is three tiers and they do not line up (§3.1). Anything runs as a
terminal command. Eighteen families, opencode among them, get identity,
activity, liveness and hooks. Only **Claude, Codex and Cursor** get the
orchestration launcher and managed accounts - a known gap, filed as Orca issue
[#19823](https://github.com/stablyai/orca/issues/19823), whose stated cost is
that an opencode team "cannot use Orca supervised orchestration".

`orchestration` is not a set of conveniences either. It is a written contract
about ownership and settlement (§5), it names the four roles a caller can be in
and tells three of them not to create its objects at all, and it supports a
caller with its own launcher two ways: `dispatch --inject` for context without
supervision, or `worker-start --terminal` to hand Orca lifecycle ownership of a
terminal the caller started itself.

### Correction: Orca does report usage

An earlier version of this document said nothing in the 234 commands reports
usage or rate limits. **That was wrong.** No command *summary* mentions it, but
`orca account list --json` carries a whole `rateLimits` block, and it is
substantial:

```json
"rateLimits": {
  "claude": { "provider": "claude",
    "session": { "usedPercent": 100, "windowMinutes":   300, "resetsAt": …, "resetDescription": "3:30 PM" },
    "weekly":  { "usedPercent":  74, "windowMinutes": 10080, "resetsAt": …, "resetDescription": "Thu 4:00 PM" },
    "fableWeekly": null, "status": "ok", "error": null,
    "usageMetadata": { "source": "oauth", "attemptedSources": ["oauth","cli"],
                       "credentialSource": "scoped-keychain",
                       "authProvenance": "managed:<accountId>" } },
  "codex": { …, "rateLimitResetCredits": { "availableCount": 2, … } },
  "gemini" | "opencodeGo" | "kimi" | "antigravity" | "minimax" | "grok":
    { "session": null, "status": "unavailable", "error": "<why>" },
  "inactiveClaudeAccounts": [ { "accountId": …, "rateLimits": {…},
                               "updatedAt": …, "isFetching": false } ]
}
```

Four things matter about it:

- **Eight providers**, not one. Each reports the same shape or says `status:
  "unavailable"` with the reason, so an absence is explained rather than blank.
- **Windows are keyed by `windowMinutes`** (300, 10080), not by fixed names.
  This is the generalized shape a multi-vendor quota model needs, already
  built.
- **It covers inactive accounts too** - `inactiveClaudeAccounts[]` carries a
  full reading per registered account - and it is sourced over **OAuth**
  (`usageMetadata.source`), so it needs no running session.
- **But it is only as fresh as Orca's own schedule.** Measured on this machine:
  the active account's figure was 3.4h old; the three inactive ones were
  **40.3h, 42.9h and 40.3h old**, all `isFetching: false`. No CLI verb forces a
  refresh.

**Re-measured 2026-09-21 (#1353), and it had changed:** the active account was
**0.8h** old and agreed with ocp's own reading for the same login, while
`inactiveClaudeAccounts[]` came back **empty**. So "not fresh enough" was one
reading's symptom, not the rule.

The accurate statement is narrower: Orca reliably knows what **the account it
calls active** has spent, over OAuth, in the right shape. The per-account
channel exists and is not dependable - 40h old on one reading, absent on the
next - and ocp needs all five of its accounts at once, continuously.

Part 2 (`vendor-extension-board.md`, local, not in the repo) takes this list seam by seam and asks
what ocp uses, what it rebuilt, and what a second vendor would cost. What has
been settled out of both is in `vendor-extension-decisions.md`.
