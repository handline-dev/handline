# Story 4 design: one daemon per app, one leader for shared state

Design only; nothing is built. It builds on [the multi-machine research](story4-multi-machine-research.md)
(section 5 for the Raft blocks). The founder's answers to its open questions are folded into the sections
they change and listed under Decisions; Build slices at the end order the first build.

**First scope: this Mac and one more Mac.** With two nodes only manual election is possible, so manual is
built first. Automatic election stays designed (section 4) and is built later, for three nodes or more.

**Names used throughout:** a **node** is one daemon on one machine. Exactly one node is the **leader**;
every other node is a **follower**. No other pair is used, in this doc, the code or the UI.

## The shape

Every app runs its own daemon on its machine; there is no connect mode. Daemons talk to daemons. Each
node owns its machine: its agents, their terminals, the account logins stored there, and its load
numbers. None of that is replicated as truth. Shared state is the task board (tasks, memos, blockers),
chat and requests, and the team (`team.yaml`, links, trust roots). The leader keeps it, and every
follower holds a replica.

One mechanism carries shared state in both election modes: an ordered **write log**. A write method that
changes shared state (`task.new`, `chat.send`, `task.memo.add`, and the rest) becomes a log entry with an
index. The leader appends it, and every node applies the same entries in order to its own SQLite. An
entry must replay the same on every node, so anything that touches a machine stays out of the log:
spawning a session, a key, a clock reading. Only the result is a log entry, as a write (for example "agent
X on node B is running").

- **Reads** are served from the local replica. A caller that just wrote waits until its node has applied
  that index, so it reads its own write.
- **A shared write on a follower** is forwarded to the leader and answered once applied locally.

## Who does what

Only the leader schedules. Every node keeps its own machine running. The last column says what a
follower does with leader-only work while there is **no leader** (lost or released, until a new one is
promoted or elected).

| Duty | Who | With no leader |
|---|---|---|
| Pick the account for a launch | leader only | queue: the launch waits |
| Start a turn: deliver a queued message or task into an agent | leader only | queue: it stays queued, delivered by the next leader |
| Place work: which node and agent a new task goes to | leader only | queue: the task stays queued |
| Triggers (scheduled and idle wakes) | leader only | queue at most one firing per trigger; the next leader runs it once |
| Timeouts (task clocks, wake timers, the lost-node timer) | leader only | paused: no clock runs, none fires |
| Shared writes from an app (`task.new`, `chat.send`, memos, edits) | leader applies | refuse, gate `no_leader`, so the app says why; not queued, so no write can conflict with the next leader's state |
| Prune and back up shared state | leader only | skip until there is a leader |
| Keep its running agents and terminals alive | every node | as always |
| Record hooks and transcripts; working and idle state | every node | as always; reports to the leader are buffered and sent on promotion |
| Report its load (cores, memory, slots) | every node | buffered, as above |
| Apply the write log to its replica | every node | nothing new to apply |
| Serve its own app and the phone (reads, terminal attach) | every node | reads from the replica, which stops at the last entry |
| The founder's direct actions on its own agents (keys, Esc, stop) | every node | as always: they touch only that machine |

**A turn already running** on a follower's agent when the leader goes away **finishes, and then nothing
new starts**. Freezing it mid-turn would cut a tool call in half and leave its worktree half-written. Its
hooks and transcript are recorded locally as usual, and its end is reported when a leader exists again.
Any message waiting for that agent stays queued (row 2 of the table).

## 1. Joining as a follower

A new node joins with `hl node join <leader address>` and a pairing code from the leader (the existing
`device.pair` flow, issuing a node credential instead of a device token).

1. **It must hold no shared data.** A node with tasks, chat or a team of its own is refused with
   "has its own data: use import (section 2)". Nothing is merged.
2. **It takes the leader's data:** a snapshot of the shared tables at index N, then the log from N+1,
   kept streaming. Attachments come with the snapshot. Logins and agents do not.
3. **It registers its own agents.** For each agent defined on it, the node writes
   `node.agent.register {agent, node}` through the leader. A name already taken team-wide is refused,
   and the founder renames it. Work and messages for the agent are applied everywhere but delivered only
   by its own node, when the leader starts the turn.
4. **It reports its load** as a heartbeat to the leader every few seconds: cores, memory, free slots and
   tags (the story 2 numbers). A heartbeat is not a log entry: load is the machine's own and changes too
   often.

## 2. Importing a machine that has its own data

For a node that already ran alone, with tasks and chat of its own:

1. Its shared tables are set aside unchanged as a read-only **import source**, and it joins as in
   section 1 with an empty shared store.
2. The founder sees the source's open tasks in the app (`node.import.list`) and picks which to bring.
3. Each picked task is created through the leader as a new task with a **new number**. Its body, memos
   and attachments are copied, and a first memo says "imported from <node> T<old>". Blockers between
   picked tasks are remapped. References to old numbers inside text are left as written.
4. Chat history comes as a **read-only archive**: the app shows it under the source node's name, and
   nothing in it can be answered, edited or delivered. Closed tasks are not imported; they stay readable
   in the source until the founder discards it (`node.import.discard`), which drops the archive too.

No row is merged and no number is reused. The source records which tasks were already brought, so a
second import does not duplicate them.

## 3. What is replicated and what is not

| Lives only on its own node | Shared, kept by the leader |
|---|---|
| Agent processes, terminals, tmux and structured sessions | Tasks, memos, blockers, task numbers |
| Account logins and usage readings | Chat, requests, inbox, attachments |
| Load: cores, memory, slots, the test lock | Team: agents and their node, links, trust roots |
| The agent's worktrees and its repo mirror | Who is leader, the election mode, the node list |

One account's login may live on two machines. The leader counts the **account cap across nodes**: each
node reports its running sessions per account in its heartbeat, and the leader picks the account for a
launch against the sum.

The leader keeps a **node table** (name, address, tags, last heartbeat, lost or not). Agent state that
apps show (`agent.changed`: running, idle, needs you) is the agent's own node's. That node sends it to
the leader as compact state writes, so every app sees every agent. A terminal is attached through the
agent's node, and the app's own daemon relays the bytes.

## 4. The leader: chosen by the founder or elected

`node.election {mode: "manual" | "automatic"}` is a founder write. Manual is the default, and the only
mode built first (two Macs); the automatic parts below are built later.

**When the leader goes away**, it is either lost (missed heartbeats for 60 s) or released (the founder
runs `node.leader.release`, or the leader's machine shuts down cleanly). Every follower stops scheduling
at once (the table above).
- **Manual:** every follower asks the founder, **"The leader <name> is gone. You are a follower now:
  promote this daemon to leader?"** The question shows as a card in that machine's app, as a notice on
  the phone (from whichever node it is paired with), and in `hl node status`, answered with
  `hl node promote`. The first yes wins: that node becomes leader, and the others' cards close as its
  first entry reaches them. Until a yes, nothing is scheduled anywhere.
- **Automatic:** no question; the followers elect a leader among themselves. The card says "electing
  a leader" and closes when the election completes. If no majority is up, no election can complete, and
  the card offers the escape: switch to manual and promote this daemon.

A planned move skips the gap: `node.leader.set <node>` on the leader hands leadership straight to a
follower that is caught up.

**Manual, the epoch.** Each leader holds an **epoch** that grows by one at every change, and every log
entry carries it. A node that sees a higher epoch stops leading at once and follows. If a lost leader
returns, or the founder said yes on two cut-off nodes, the higher epoch wins. The other node's entries
that never reached it are set aside as an import source (section 2), not replayed. Manual is the only safe
mode with two machines: with two nodes, no vote can tell a dead leader from a cut network.

**Automatic, Raft.** The leader is the leader of a `hashicorp/raft` group inside the daemon. The write
log is the Raft log, the daemon's SQLite is the Raft state machine (FSM `Apply` runs the same entries),
and snapshots are SQLite snapshots. Raft's own term plays the epoch's part.

**Refusing automatic with fewer than three nodes.** The write checks the node list:
- fewer than 3 voting nodes, or any of them not caught up and reachable: refused with gate
  `too_few_nodes` ("automatic election needs three or more nodes; you have 2"), and nothing changes;
- under automatic, removing a node that would leave fewer than 3 voters is refused the same way;
  switch to manual first. A node that is merely lost is not removed, so the group keeps its size and
  waits for a majority.

**Switching.**
- *Manual to automatic:* the leader checks the rule above, takes a snapshot at index N, and bootstraps
  the Raft group with every node as a voter and itself as the first leader, starting from N. It then
  writes the new mode as the group's first entry. Followers attach to Raft from N.
- *Automatic to manual:* the leader writes the mode change as a Raft entry, with the next epoch. Once that
  entry commits, every node leaves Raft and follows this leader's log from that index. The Raft state is
  dropped.
Both switches need a leader; neither runs while there is none.

## 5. Agents on a lost machine

A node that misses heartbeats for 60 s is marked **lost** by the leader. Agents do not move: a running
agent is a process on that machine.
- Its agents show the state `lost` with the reason "node <name> lost <time>". Messages to them stay
  queued, and no turn is started.
- Tasks they hold stay `doing`, flagged "agent on a lost node". The founder may hand a task to another
  agent (`task.handoff`). The worktree's last pushed branch is named in the flag, or "unpushed".
- No automatic re-queue, however long the node stays lost: its tasks never go back to queued without the
  founder, as with any held task.
- On the lost machine itself, it is the leader that went away (section 4): its turns finish, nothing new
  starts, and its own app asks the promote question in manual mode. If the founder promotes it there, the
  epoch decides when the machines meet again.
- On return, the node rejoins as a follower, catches up, and sends what it buffered. Its agents take new
  turns again once the leader starts them.

If the lost node was the leader, section 4 applies to every node.

## 6. What the Mac app's single mode needs from the daemon

The app drops connect mode (`apps/electron/src/mode.ts`) and always starts its own daemon.
The pc side changes the app; the daemon provides:
1. **`node.status`**: this node's role (leader or follower), the leader's name, replication lag, the
   node table, and the election mode, on a `nodes` topic.
2. **The promote card:** the question of section 4 as an inbox item with a `node.promote` answer, also
   pushed to the phone.
3. **Write forwarding**, so every write the app sends works on a follower unchanged; and the
   `no_leader` refusal, so the app can say why a write waits.
4. **Agents of other nodes** in `agents.list` and `agent.changed`, with their node, and terminal attach
   relayed through the agent's node.
5. **Join and import methods** (`node.join`, `node.import.list`, `node.import.apply`,
   `node.import.discard`) for the app's setup screen, replacing the remote pairing it does today.
6. **The election setting** with its refusal (`too_few_nodes`), `node.leader.set` and
   `node.leader.release`.

The phone pairs with **any node's** gateway and follows the leader when it changes. It sees everything
through that node's replica and forwarding, so it still gets the promote question when the leader is lost.

## Decisions

1. **Scope:** this Mac and one more Mac, so manual election only; automatic stays designed and is built
   later (the intro and section 4).
2. **Logins:** one account's login may live on two machines, and the account cap is counted across
   nodes (section 3).
3. **Import:** chat history comes as a read-only archive (section 2).
4. **Lost node:** lost after 60 s of missed heartbeats, and its tasks never go back to queued without the
   founder (section 5).
5. **Phone:** pairs with any node and follows the leader (section 6).
6. **No leader:** shared writes from an app are refused with `no_leader`, not queued (the duty table).

## Build slices

Two Macs, manual election. Each slice lands and is reviewed on its own, in this order.

| # | Slice | Area | Hours |
|---|---|---|---|
| 1 | Node identity and the node table; `node.status` and the `nodes` topic on one node | daemon Go | 6 |
| 2 | The write log on a single node: shared writes become indexed entries, applied in order; a writer reads its own write | daemon Go | 12 |
| 3 | Node pairing: `device.pair` issues a node credential; `hl node join`; a node with its own data is refused | daemon Go, hl CLI | 6 |
| 4 | Follower replica: snapshot at N, then the log streamed from N+1 and applied | daemon Go | 12 |
| 5 | Write forwarding from a follower, and the `no_leader` refusal | daemon Go | 6 |
| 6 | Heartbeats with load and per-account sessions; lost after 60 s; the `lost` agent state and the task flag | daemon Go | 6 |
| 7 | Leader-only duties gated: launches, deliveries, placement, triggers and timeouts wait or pause with no leader | daemon Go | 8 |
| 8 | Agents across nodes: `node.agent.register`, other nodes' agents in `agents.list` and `agent.changed`, the account cap across nodes | daemon Go | 8 |
| 9 | Terminal attach relayed through the agent's node | daemon Go | 6 |
| 10 | Manual promotion: the epoch on every entry, the promote card, `hl node promote`, `node.leader.set` and `node.leader.release`; a lower epoch's unsent entries set aside | daemon Go, hl CLI | 10 |
| 11 | Import: the source set aside, `node.import.list`, `node.import.apply`, `node.import.discard`, the chat archive | daemon Go | 10 |
| 12 | The Mac app's single mode: no connect mode, node status, the promote card, the join and import setup screen | app | 12 |
| 13 | The phone pairs with any node, follows the leader and shows the promote question | app | 4 |

About 106 hours in all. Automatic election (Raft, `too_few_nodes`, the switch both ways) is not in this
list.
