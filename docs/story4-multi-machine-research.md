# Story 4-0: running agents across several machines

Research only. Nothing is built. Licences come from `gh api repos/<r>` on 2026-10-14. Behaviour
marked "general knowledge" was not checked in the project's docs and is unverified. It builds on the
resource-pool model of `docs/story2-load-research.md` (host cap `H`, account cap, held-reason queue).

## 1. How others spread work over machines

| Project | Licence | Repos and worktrees | Build cache | Simulators / emulators | Secrets and accounts | A machine drops out |
|---|---|---|---|---|---|---|
| GitHub Actions runner (`actions/runner`) | MIT | Each job checks out into a per-runner work folder; no shared worktree | Cache is a hosted service keyed by string; the runner has none of its own (general knowledge) | Whatever the host has; macOS runners must be real Macs | Secrets delivered per job by the server; none stored on the runner | Server marks it offline; a queued job waits for another runner with the same label |
| Buildkite agent (`buildkite/agent`) | MIT | Agent keeps a git mirror per repo and checks out a clean copy per job (general knowledge) | Local to the agent; sharing is the operator's job | Host's own; routing by agent tags | Agent token per machine; job secrets from a hook or the secrets service | Heartbeat lost: job is failed or retried by policy, tags route the retry |
| Cirrus CLI + Tart (`cirruslabs/cirrus-cli`, `cirruslabs/tart`) | CLI MIT; Tart is FSL-1.1-ALv2 (source-available, turns Apache-2.0 after two years) | A fresh macOS/Linux VM per task, cloned from an image; the repo is cloned inside | Image layers hold warm caches | macOS VMs on Apple silicon, so iOS Simulator runs inside | Nothing persists in a thrown-away VM; secrets injected per task | The task is lost with the VM and re-run elsewhere |
| Nomad (`hashicorp/nomad`) | BUSL-1.1 (API: NOASSERTION) | Task directory per allocation | None built in | Host drivers only | Vault integration (general knowledge) | Missed heartbeat: allocations are rescheduled on other nodes |
| Dev containers (`devcontainers/spec` CC-BY-4.0, `devcontainers/cli` MIT) | see left | One container per workspace, repo bind-mounted or cloned into a volume; worktrees are the host's business | Named volumes for package caches | Linux only; no iOS | Env and mounted credential helpers; forwarded SSH agent | Not addressed; the container is a single machine's |
| Coder (`coder/coder`) | AGPL-3.0 | One workspace per task from a Terraform template; repo cloned on start | Persistent volume per workspace | Whatever the template provisions, including Mac hosts if the operator has them | Per-workspace token; external auth (git provider) broker | Workspace shows offline; agent reconnects on return; no automatic move |
| Gitpod (`gitpod-io/gitpod`) | AGPL-3.0 | Prebuilt workspace per branch, repo cloned in | Prebuilds are the cache | Linux | Per-user secrets injected | Workspace restarts from the last backup |
| E2B (`e2b-dev/E2B`, `e2b-dev/infra`) | Apache-2.0 both | Sandbox per agent run; files go in over an API | Template snapshots | Linux microVM; none for iOS | Passed as env at create time | Sandbox is gone; the caller creates another |
| Firecracker (`firecracker-microvm/firecracker`) | Apache-2.0 | n/a (the isolation layer under sandboxes) | Snapshot and restore of a warm VM | Linux guests only | n/a | n/a |
| Daytona (`daytonaio/daytona`) | Not detected by the API; no LICENSE file at the repo root (unverified) | Sandbox per run, snapshots | Snapshots | Linux | Env at create | Unverified |

Reading across:

- **Nobody moves a running agent.** Every system above either fails the job and re-queues it, or
  restarts it from a snapshot. Work that is already mid-turn stays with its machine.
- **Placement is by label or declared need**, not by live load (Buildkite tags, Nomad reservations).
  This matches story 2: a fixed integer and declared reservation beat anything adaptive.
- **The repo is cloned per machine** (mirror plus clean checkout in Buildkite). No tool shares a
  worktree across machines.
- **iOS is the hard constraint.** The simulator only exists on a Mac; Linux sandboxes (E2B,
  Firecracker, dev containers) cannot run it. Only real Macs, or macOS VMs on Macs, can.
- **Secrets stay off the box until a job needs them**, and thrown-away machines hold none.

## 2. What we would borrow

Ideas only. Coder and Gitpod are AGPL and Nomad is BUSL: no code from those three. MIT/Apache code
(Buildkite agent, E2B infra, Firecracker) could be used with its notice kept; Tart's licence also
forbids offering it as a competing service, so read it before depending on it.

1. **Buildkite: labels and a bare mirror.** Each machine reports tags (`mac`, `simulator`, `gpu`);
   a task asks for tags; placement picks a machine with a free slot. A per-machine git mirror makes
   a new worktree a cheap local checkout.
2. **Buildkite/Nomad: the machine is one more pool**, next to the account cap and `H` from story 2.
   A start that finds every fitting machine full waits with the reason "host full" on that machine.
3. **GitHub runner: the server never trusts the runner for secrets.** An account's credentials stay
   on the machine that owns that account; a task asks for the account and runs where it lives.
4. **Nomad: a lost machine's work is re-queued, not moved.** An agent that was mid-turn on a machine
   that drops out is marked lost with the reason, and the task goes back to queued with its worktree
   branch pushed (or flagged unpushed).
5. **E2B: an agent in a throw-away box** is useful only for pure-code work with no simulator and no
   account; worth a later step, not a first one.

## 3. Open questions for the founder

- Which machines exist beyond this one (a second Mac, a Linux box, cloud)? Everything below assumes
  at least one more Mac.
- Does an account's login (Claude session files) have to stay on one machine, or may two machines
  hold the same account? Quota is shared, so the account cap would have to be counted across them.
- Is `docs/story2-load-research.md` (PR #96) merged first? The host cap this builds on lives there.

## 4. Proposal: what moves first, what stays

**Stays on the main machine:** the daemon and its database, the gateway, the phone and PC apps'
connection, the chat and task state, every account's login, and anything that needs the iOS
Simulator or the founder's own desktop session. One source of truth; the other machines never hold
the database.

**Moves first (4-1):** one extra machine runs *workers*: agent sessions that need only a repo and a
shell. Candidates in order:

1. Test and build runs (the largest consumer in story 2: 4 GB, half the cores). A remote test slot,
   same lock-and-queue shape as the local one.
2. Agents on reviewing and research tasks (read-mostly, small worktree, no simulator).
3. Implementation agents on a pure-code branch, pushing to a branch the main machine fetches.

**Stays until later (4-2):** simulator and emulator work, anything that needs an account that is not
on the worker, and moving agents between machines mid-turn (nobody in the survey does it).

**What 4-1 needs from the daemon:** a machine record (name, tags, cores, memory, free slots, last
heartbeat), a placement test beside the account cap, a "lost" state with a reason, and the queue
reason "no machine with `<tag>` free". The admin view of story 2 gains a machine column.

## 5. Leader and follower: building blocks

The founder's direction: the leader writes, its state is replicated to followers, a follower is
elected leader when the leader dies, and work goes by each node's load. Licences and archive status
from `gh api` (2026-10-14); "general knowledge" is not checked in a doc. Today's daemon keeps one
SQLite file through `modernc.org/sqlite` (pure Go, no cgo; `go/go.mod`).

| Block | Licence | What it is | Fit |
|---|---|---|---|
| `hashicorp/raft` | MPL-2.0 (file-level copyleft; fine to link unmodified) | Raft library for Go: leader election, a replicated log, snapshots, your own state machine | Closest to "leader writes, followers replicate": the log entries would be the daemon's writes |
| `etcd-io/raft` | Apache-2.0 | Raft core only: the caller supplies network, storage and timers | More work than hashicorp/raft; the same model |
| `lni/dragonboat` | Apache-2.0 | Multi-group Raft library; last push 2025-07 | Heavier than we need; slower upstream |
| `rqlite/rqlite` | MIT | A server: SQLite replicated with Raft, SQL over HTTP, leader writes, read consistency tunable (README, "Tunable Consistency") | Gives leader/follower for free, but the daemon would talk to rqlite instead of opening its own file: the whole db layer changes |
| `canonical/dqlite` | LGPLv3 plus a special exception allowing static or dynamic linking in a Combined Work (its LICENSE file; API: NOASSERTION) | SQLite replicated with Raft, a C library (README) | Needs cgo; the daemon is pure Go today |
| `nats-io/nats-server` (JetStream) | Apache-2.0 | Messaging with replicated streams, Raft inside (general knowledge) | Good for events, not for SQL state |
| `superfly/litefs`, `benbjohnson/litestream` | Apache-2.0 both | Replicate or back up a SQLite file; LiteFS has a leader lease (general knowledge); Litestream is backup | Whole-file shipping; no election of its own in Litestream |
| `hashicorp/memberlist`, `serf` | MPL-2.0 | Gossip membership and failure detection | Useful for "who is alive" only |

What the survey in section 1 adds: no coding-agent tool in it elects a leader; Nomad and Consul use hashicorp/raft
and are the shape the founder describes (a Raft server set, workers that join), but Nomad is BUSL
and Consul's licence is NOASSERTION, so we use ideas and not code (Nomad's BUSL is from its `LICENSE` file, read in section 1).

**Recommendation (a choice, not built):** do not write consensus. Pick between two:

1. **`hashicorp/raft` inside the daemon.** The log carries the daemon's write calls (the same
   `rpc` write methods apps already send), each node applies them to its own SQLite. Keeps pure Go
   and the single-binary daemon. Cost: every write path must be a deterministic replayable command;
   anything that touches the machine (spawning an agent, a clock) must stay outside the log.
2. **rqlite under the daemon.** Least consensus code, but the db layer moves from a file to HTTP
   and `modernc.org/sqlite` is replaced: a rewrite of every query site.

Option 1 is the smaller rewrite of behaviour and keeps what we have; option 2 is the smaller amount
of new concurrency code. Neither makes agents move: a running agent is a process on one machine, so a
leader's death means its agents are lost and their tasks re-queued (section 2, point 4).

**Load balancing** stays our own and small: each node reports cores, memory and slots (the story 2
numbers) through the log or a heartbeat; the leader places new work on the node with the most free
slots that has the task's tags. A rule, not a model.

**Open for the founder:** how many nodes (Raft wants an odd number, three at least to survive one
loss; with two machines a failed leader leaves no majority and no automatic election)? That one
question may decide the whole shape, so ask it before choosing between 1 and 2.

## Sources

`gh api repos/<r>` for every repository in the table (licence and description), and
the `LICENSE` files of `cirruslabs/tart` and `hashicorp/nomad`. Buildkite agent behaviour is from the project's README only and mostly
general knowledge. No page for Daytona, Coder's docs or Nomad's task-driver docs was fetched.
