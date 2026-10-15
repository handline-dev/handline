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

## Host cap rethink (founder memo 1803 on T3923)

The memo: `cores/2` has no measurement behind it, and an idle agent uses 2 to 6 % of a core. Measured again on
2026-10-15 07:27 to 07:29 on the same Mac (10 logical cores of which 4 performance, 24 GiB).

### What runs out first

| Resource | Measured now | How far from the limit |
|---|---|---|
| Disk | Data volume 460 GiB, 420 used, **10 GiB free (98 %)**; 14 worktrees take 20 GB (about 1.4 GB each, mostly `node_modules`), `~/.handline` 4.9 GB | Nearest: about seven more worktrees. The earlier sample today said 21 GB free |
| CPU (heavy runs) | Full web suite: 18.6 s wall, 115 s user + 12.6 s sys, about 6.8 cores busy; one test file 2.4 s. The root run measured above peaked at 454 % | Two full runs at once fill the 10 cores; the 15-minute load average reads 86 from such bursts |
| CPU (agents) | 17 `claude` processes sum to 32 % of one core, 0.1 to 4 % each | Not a limit |
| Memory | The same 17 agents: mean 229 MB, median 257, max 465 MB, about 5 GB; free memory 56 %, pressure level 1 (normal) | About 50 agents before pressure; one full web run adds 470 MB in its main process |
| File handles | `kern.maxfiles` 184 320, per process 92 160, 26 963 open; an agent holds 8 to 28 | Not a limit |
| Model rate limits | Not measured in this pass | The account cap already counts agents mid-turn per account |

Trust: one sample each, on a shared machine that was not idle (about 15 `handlined` and 17 agents running, load
5.4 over one minute but 51 and 89 over five and fifteen: a burst had just ended, some of it these runs). RSS counts shared pages twice. The disk
figure is exact; the CPU and memory figures are upper bounds.

### Where the cap belongs

Not on agents mid-turn for CPU or memory: agents cost little of either. The load comes from heavy runs (test,
build), and on this machine the first thing to run out is disk. So the limits are three, each from a live
reading and none from a core-count constant:

1. **Heavy runs** (tests and builds, which the founder already limits to the auditor): a slot count of
   `floor(idle cores / cores one run used)`, the second number recorded from the last run on this machine
   (about 7 here), minimum 1. A run waits for a slot; load per core above 1 at the start also holds it.
2. **Agents mid-turn**: the account cap stays the main limit. A memory ceiling is added from live numbers:
   `(available memory - what the system keeps) / median agent RSS`, shown, rarely binding.
3. **Disk guard**: a new worktree or agent start is held when free space is under three times the median
   worktree size already on disk (about 4.5 GB here), with the reason shown and a force-start way out.

Startup measures nothing fixed: it reads cores and memory once, and the per-run and per-agent costs are
rolling medians of what the daemon sees (`ps`, `vm_stat`, `df`), so a smaller or larger machine gets its own
numbers. Until a cost has a sample, the 2-1 doc's 400 MB per agent is the stand-in.

## Sources

Fetched 2026-10-14: the READMEs of the five repositories above through `gh api`;
docs.github.com (self-hosted runners), buildkite.com/docs/agent/v3/configuration,
developer.hashicorp.com/nomad/docs/job-specification/resources, onorca.dev/docs/agents/usage-tracking,
conductor.build/docs, and Vibe Kanban's `docs/core-features/monitoring-task-execution.mdx`.
