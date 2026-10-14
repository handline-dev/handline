# Story 2-0: load limits and admin views

Research only. Nothing is built. "Unverified" marks a claim the project's own docs or code did not
show; absence of a feature in a doc page is not proof it does not exist.

## 1. How others limit and show load

| Project | Licence (`gh api repos/<r>`) | Caps concurrent work | Reacts to CPU/memory | Shows where the load is |
|---|---|---|---|---|
| Orca (`stablyai/orca`) | MIT | No cap in README or the usage-tracking doc (unverified) | None found | Usage page is per provider and account, not per agent or worktree |
| Conductor (conductor.build) | No public repo found; closed (unverified) | Docs say only "run ... in parallel"; no cap stated (unverified) | Unverified | Unverified |
| Vibe Kanban (`BloopAI/vibe-kanban`) | Apache-2.0 | None in the execution doc (unverified) | None found | Streaming logs per task attempt; no resource figures |
| Claude Squad (`smtg-ai/claude-squad`) | AGPL-3.0 | None in the README (unverified) | None found | Instance list in a TUI; no resource figures |
| GitHub Actions runner (`actions/runner`) | MIT | One job per runner process, scale by adding runners (from general knowledge; the docs page fetched is silent) | None; the operator owns the machine | Not by the runner itself |
| Buildkite agent (`buildkite/agent`) | MIT | `spawn` (default 1) agents in parallel, or `spawn-per-cpu`; `disconnect-after-idle-timeout`, `disconnect-after-uptime` | None documented | Not in the agent |
| Nomad (`hashicorp/nomad`) | BUSL-1.1 (API says NOASSERTION) | Scheduler places by declared `cpu`/`memory`; a node that is full takes no more | OS enforces `memory` hard limit (cgroup `memory.max`); `memory_max` allows oversubscription and Nomad may kill oversubscribed tasks under contention | Per-allocation resource stats |

Reading across: none of the agent managers (Orca, Conductor, Vibe Kanban, Claude Squad) documents a
concurrency cap or any CPU/memory reaction; the ones that cap do it with a fixed number the operator
sets (Buildkite) or with declared reservations (Nomad). Nobody in the sample adapts the cap to live
load.

What we would borrow (ideas only; Claude Squad is AGPL and Nomad is BUSL, so no code from either):

- Buildkite: one explicit integer, plus a per-CPU form (`spawn-per-cpu`). Our default can be derived
  from cores the same way.
- Nomad: a *declared* reservation per workload, checked at placement, separate from enforcement. For
  us the placement check is the slot test that already exists for accounts.
- Orca/Vibe Kanban: show state per worktree/attempt next to the agent, not in a separate tool.

## 2. Measured on this machine

Apple silicon, 10 cores, 24 GB RAM, 460 GB disk (21 GB free). The box is shared with about ten other
agent sessions and was never idle: load average 31 to 78 during the work, `PhysMem: 23G used, 778M
unused`. Every figure below is therefore an upper bound on a quiet machine, and CPU shares are
inflated by contention. One sample each, not a benchmark.

| What | CPU | Memory (RSS) | Disk |
|---|---|---|---|
| One agent (`claude` process of this session, 5 samples over 10 s) | 2 to 6 % of one core idle-ish, spikes while a tool runs | 344 to 358 MB; other live agents 270 to 380 MB | Account home 586 MB (`~/.handline/accounts/gamma`); worktree 1.5 GB of which `node_modules` 1.4 GB |
| Its children (shell, MCP servers, caffeinate) | 0 to 9 % | 2 to 8 MB each, more per MCP server | none |
| Daemon `handlined` (3 dev instances) | 0.0 % | 13.7, 18.0 and 21.9 MB | `db.sqlite` 296 MB + WAL 3.7 MB; instance folder 346 MB |
| Gateway `handlined -gateway` | 0.0 % | 12.9 and 13.5 MB (about 17 threads) | none |
| `pnpm turbo test --force` at the repo root (9 tasks, passed, 2 runs) | peak 454 % summed over the tree, mean 200 %; 238 s user + 30 s sys in 96 s | peak 4.27 GB summed over up to 29 processes (sum counts shared pages twice); largest single process 460 MB | `.turbo` 140 KB; negligible writes |

Commands:

    ps -o pid,pcpu,rss,etime,command -p <pid>                 # daemon, gateway, agent
    ps -axo pid,ppid,pcpu,rss,command | awk -v P=<agent> '$2==P'   # agent children
    top -l 2 -s 2 -n 0 | grep -E "CPU usage|PhysMem"
    sysctl -n hw.ncpu hw.memsize; vm_stat; df -h /; uptime
    du -sh <path>
    /usr/bin/time -l pnpm turbo test --force                   # 96.15 s real, 459 MB max RSS (one process)
    scratch sampler: every second, `ps -axo pid=,ppid=,pcpu=,rss=`, summed over the descendants of the
    pnpm process (79 samples)

Take-aways: the daemon and gateway are noise (under 25 MB each). One agent costs about 350 MB and
little CPU while it waits on the model, so the memory budget is `agents x ~400 MB`. A root test run
costs as much memory as ten agents and half the machine's cores, and ten of them at once is what
pushed the load average past 70. The existing cap (`hl account cap`, default 2) counts agents mid-turn
per account against the quota; nothing counts the machine.

## 3. Proposal

### 2-1: limits (deterministic, no model)

1. **Host cap.** A second slot test beside the account cap: at most `H` agents mid-turn on the
   machine. A start or wake that finds the host full waits in the same queue and carries the reason
   "host full: n of H" (the account reason stays "at its cap"). Default
   `H = min(cores / 2, (RAM - 4 GB) / 400 MB)`, so 5 here; settable with one command like the account
   cap.
2. **Shrink on measured pressure, never kill.** Each tick read `hw.ncpu` load average and memory
   pressure (`kern.memorystatus_vm_pressure_level`). Load per core above 1.5 or pressure at warn for
   3 ticks in a row: effective cap drops by one (floor 1). Calm for 10 ticks: it grows back by one,
   up to `H`. Running agents are never stopped; only new work is held. Fixed thresholds, fixed steps,
   no learning.
3. **Test-run slot.** `pnpm turbo test` at the root takes one of `T` machine-wide slots (default 1;
   a file lock with a pid, stale ones reclaimed). It is the single largest consumer measured above.
4. **Same code path as the account cap**, so a wake and a handoff cannot disagree on room.

Open: the 400 MB figure is measured on idle-ish agents; a busy one with a big build may be higher.
Take `H` from a config value first and let the numbers earn the default.

### 2-2: the admin view

Two tables and a strip, read-only, from one daemon call that samples `ps` every tick (no extra
agent):

- **Strip:** cores, load per core, memory used / pressure level, disk free; host cap used/effective/
  configured; test slots used/total.
- **Per agent:** name, account, state (mid-turn, idle, waiting), RSS and CPU summed over its process
  tree, worktree size, and "held now": the reason it waits (account cap, host full, wall) with its
  place in the queue. Account rows above give `active/cap` and the runway.
- **Per process:** pid, role (agent, MCP child, daemon, gateway, test run), owner agent, CPU, RSS,
  age. Sorted by RSS; this is where the 4 GB test run shows up.
- Each row shows the limit next to what is held now (for example `3/5 agents`, `1/1 test slots`),
  so the screen answers "why did my agent not start".

Attribution is by process tree from each agent's session pid; processes with no agent are listed
under "other" so the sum matches the machine.

## 4. Several machines as one pool

The founder's direction (T3866 memo 1715): an old laptop, a small desktop and a rented server working as
one, because app builds eat memory and disk. Research only; nothing is built.

### 4.1 How similar tools place work on machines

| Tool | Licence | How a job finds a machine | Borrow |
|---|---|---|---|
| GitHub Actions self-hosted runners | MIT (`actions/runner`) | Labels route a job to runners with matching labels; runners can be grouped | Labels on a machine (`gpu`, `big-disk`, `has-repo-x`) |
| Buildkite agent | MIT (`buildkite/agent`) | A step names a queue; an agent joins one queue by tag and takes the first available job; agent priority reorders | The agent pulls work; a machine that is full simply does not ask |
| Nomad | BSL 1.1 (`hashicorp/nomad`; GitHub says "Other"); not open source | Filter nodes (health, constraints), then score feasible ones, mainly by bin packing, with affinity | The two steps only. Do not copy code |
| Kubernetes | Apache-2.0 | Filter on a pod's resource *requests* against unreserved capacity, then score; no fit leaves it pending | Requests as the number placement uses; "pending" with a reason |
| Dev containers | spec CC-BY-4.0, CLI MIT | A file describes the environment a workspace needs; it does not say where it runs (unverified beyond the README) | Name a worktree's toolchain once so any machine can build it |
| Cloud sandboxes (E2B) | Apache-2.0 | Not checked beyond the licence | Nothing yet; a rented server is the same as one more machine here |

Not found in the pages read, so unverified: how many jobs one GitHub runner runs at once, what Buildkite does
when no agent is free, and how Nomad accounts CPU and memory per node.

### 4.2 What to borrow

1. **Filter, then score.** Filter: the machine has room under its own limit, holds (or can fetch) the
   branch, and carries the labels the task needs. Score only among those that pass: most free room first,
   so a laptop is not filled while the server idles. No fit: the work waits with the reason of the
   closest machine (below), never fails.
2. **Requests, not guesses.** An agent counts as one slot and a test run as the 4 GB one measured in
   section 2. Placement reads those numbers; the shrink rule of 2-1 changes a machine's room, not the numbers.
3. **Machines declare, the pool does not poll.** Each machine's daemon reports capacity, labels and what is
   held now on the tick it already has; one place decides where work goes. Where that place lives
   (one daemon driving remote workers, or a daemon per machine with a shared queue) is the open design
   question; the founder's call.

### 4.3 How the 2-1 limits keep a machine as one resource among several

Nothing in 2-1 changes per machine. Each machine keeps its own host cap `H`, shrink on measured pressure
and test-run slots. The pool adds one level above them:

- **Account cap stays global.** The quota belongs to the account, not the machine: `3 of 4` counts agents on
  every machine.
- **Host cap stays local.** A start or wake needs room at both levels. The account level is checked first
  (cheaper to explain), then a machine is chosen by 4.2.
- **An agent lives where its worktree is.** Moving it is the existing handoff (stop, push the branch, start
  on the other machine), not live migration. Cost: the branch must be pushed and the worktree rebuilt, so
  placement should prefer the machine that already has it.
- **The admin view (2-2) gains a machine column** and one strip per machine; "why did my agent not start"
  names the machine that was closest to fitting.

### 4.4 Load limits as one more waiting reason (founder memo 1696)

An agent that cannot run now waits for one thing. Today the waits are on the founder, on another task and on
an account limit. Add the load limits to the same list instead of a second mechanism:

| Waiting on | Detail shown | Cleared by |
|---|---|---|
| Founder | what is asked | an answer |
| Task | `T<n>` and its state | that task done |
| Account limit | account, `n of cap`, runway | a turn ending or the window |
| **Host full** | machine, `n of H` | a turn ending on that machine |
| **Pressure** | machine, load or memory level, effective cap | calm for 10 ticks (2-1) |
| **Test slot** | which run holds it | that run ending |

One field on the agent view (kind, reference, one line, place in the queue) feeds the rail chip, the agent
page and the admin table, so a wait on the load reads like every other wait. Whether the existing field
names are reused or a new one is added was not checked against the daemon's view code.

## Sources

Fetched 2026-10-14: the READMEs of the five repositories above through `gh api`;
docs.github.com (self-hosted runners), buildkite.com/docs/agent/v3/configuration,
developer.hashicorp.com/nomad/docs/job-specification/resources, onorca.dev/docs/agents/usage-tracking,
conductor.build/docs, and Vibe Kanban's `docs/core-features/monitoring-task-execution.mdx`.
Section 4, fetched 2026-10-15: docs.github.com (using labels with self-hosted runners),
buildkite.com/docs/agent/v3/queues, developer.hashicorp.com/nomad/docs/concepts/scheduling/scheduling,
kubernetes.io/docs/concepts/scheduling-eviction/kube-scheduler; licences through `gh api repos/<r>/license`
for actions/runner, buildkite/agent, hashicorp/nomad, kubernetes/kubernetes, devcontainers/spec and
devcontainers/cli, e2b-dev/E2B.
