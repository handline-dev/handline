# Daemon-centred architecture

Plan only; no code changes come with this document.

Two steps, in this order:

1. **Phase 1.** The Python daemon becomes the only thing that touches state. Every caller (the `hl`
   CLI, the handline IDE, agent hooks) talks to it over a local socket with a versioned contract.
2. **Phase 2.** The daemon is rewritten in Go behind the same contract. Nothing that talks to the
   socket changes.

Phase 1 is worth doing alone. It is also the only way phase 2 can be done safely: today the sqlite
schema is the real contract, and nobody can rewrite the daemon without breaking everything that
reads it.

## Rules that do not change

- Decisions stay deterministic and fail-closed. No model call outside `hl fold`.
- Triggers are levels read from persisted state, never edges.
- Nothing reads, writes or copies credentials.
- With the daemon down there is no fallback path that acts on an agent (the standing decision for
  the control socket). Auto-start, below, is a way to bring the daemon back, not a way around it.

## Where we are

- **A socket already exists.** `daemon/socket_server.py` listens on a 0600 Unix socket, one JSON
  request and one `{ok, said}` reply per connection, 17 verbs, all writes (`message`, `wake`,
  `close`, `land`, `trust`, ...). It has no reads, no version, no structured results and no way to
  push. Phase 1 grows this socket; it is not a new one.
- **Everything else reaches the state directly.** `db.connect()` is called from 45 places in `cli/`,
  37 in `ui/`, 25 in `team/`, 13 in `session/`. `ui/chat_page.py` alone holds 31 raw SQL statements,
  and the UI makes about 110 calls into `actions` plus daemon internals (`would_wake`,
  `wall_reason`, a private `_dev_mode_should_restart`). Scout's survey of the
  UI's dependencies on core has the full map; this plan cites its counts and does not repeat it.
- **Any process migrates the schema.** `db.connect()` runs `_migrate` on open, and there is no
  schema version number, only "does this column exist". Two daemons in two languages cannot share
  that.
- **Core imports UI.** `cli/` imports the non-Textual half of `ui/tui.py` (`screen_state`,
  `snapshot`, state words). That half is core logic and has to move.
- **launchd already supervises.** The plist has `KeepAlive`, and a singleton lock keeps one daemon
  per home.
- **Size of phase 2.** What a Go daemon replaces is `daemon/` (8.0k lines), `session/` (6.6k),
  `usage/` (3.5k), `vendors/` (2.7k) and the daemon-facing parts of `actions/` and `team/`: about
  22k lines of Python. The 58k lines of Python tests mostly call Python internals, so they do not
  carry over; section "Phase 2" says what replaces them.

## Decisions this plan makes

Say so if you disagree with any; each is cheap to change before work starts.

1. **The contract is the socket, not `hl --json` and not db views.** `hl --json` pays a Python
   start per call (not measured here, but about 100 ms or more) and still leaves the schema as the
   contract. Db views leave the schema as the contract. Only a socket lets the daemon own the
   schema, push changes, and be replaced.
2. **After phase 1 the daemon is the only process that writes the database and the only one that
   migrates it.** Clients do not open the db.
3. **The Python daemon stays until the Go one passes the conformance and tick fixtures** and has been
   in use without a rollback, and is deleted only after that.

## Phase 1: one socket, every caller

### Protocol

- **JSON-RPC 2.0**, one JSON object per line, over the existing socket. A connection may stay open
  and carry many calls; a one-shot call still works. The 17 legacy `{verb, ...}` requests keep
  working through an adapter until their last caller has moved, then the adapter is deleted.
- **Handshake.** The first call is `hello {client, protocol}`. The reply carries `protocol` (an
  integer), `min_client`, `daemon` (the loaded git sha), `schema` and the method list with the
  protocol each appeared in. A client refuses to run on a protocol it does not know and prints the
  one command that fixes it (`hl service install` reloads the service today).
- **Compatibility.** Adding a method or a field never changes `protocol`. Removing or changing one
  does, and the daemon then serves the old and new protocol side by side for one release. Clients
  ignore fields they do not know.
- **Errors** carry a stable code (`not_found`, `refused`, `daemon_busy`, `bad_request`, `internal`)
  and a sentence a person can read. Fail-closed is a `refused` with the reason, never a silent
  default.
- **Who is calling.** The local socket stays 0600, and the per-session token and peer-pid check for
  `land` carries over unchanged. Roles and remote callers are in the next section.
- **Reads return what the daemon already decided.** The daemon stores one view per agent (state
  word, wall reason, background work since, would-wake, cache time left, blocker headline) and
  serves it. The UI stops recomputing daemon decisions, and stops reading live terminals itself: the daemon serves the terminal channel.
- **Read methods wrap what exists.** The ~50 read functions in `actions` (task rows, tree,
  timeline, mailbox, schedule, usage outlook, accounts) are called by the daemon and serialised.
  They are not rewritten in phase 1.
- **Write methods** replace the 29 in-process UI writes and the CLI's own writes. Several have no
  CLI twin yet (`save_account_settings`, `save_global_switches`, `set_cycle_*`, `send_now`,
  `mark_read`, `trust_worktrees`); each gets one method that the CLI and the UI both call.

### Event stream

`subscribe {topics, since}` turns the connection into a stream of `event {cursor, topic, ...}`
notices. Topics are `status`, `accounts`, `agents`, `inbox`, `tasks`, `notifications`, `operations` and `chat`; `chat` replaces the earlier `requests` (messages and their landing state) and `accounts` replaces `usage`.

- The cursor is the events row id, which only grows, so a client that reconnects with its last
  cursor gets exactly what it missed. A cursor older than retention gets a `gap` notice and the
  client refetches.
- Once the daemon is the only writer it emits on write. Until then, writers outside the daemon are
  still there, so the daemon also watches sqlite's `PRAGMA data_version` every 250 ms and emits for
  what it did not write itself.
- A client that cannot keep up is dropped with `gap`, never buffered without limit.

### Callers, roles and commands

The web UI is a remote surface, so the contract has to say who is calling, from where, and what they
may do. The founder decided (decision 3 of the web UI design): the first version reaches this machine, the LAN
or Tailscale only, with no relay and no public endpoint. The network listener always speaks an encrypted
WebSocket; a Tailscale address is just the address it binds.

- **Two listeners, one contract.** The local socket as today, and an optional network listener
  carrying the same JSON-RPC lines over an encrypted WebSocket. The network listener is off by
  default and bound to the address the config names.
- **Roles.** `founder`, `member` (a paired device with limited rights) and `agent` (a call from
  inside an agent session). Roles are proven, not assumed: the founder by the founder key in `hello` (the local user is not enough, because agents run as that user), an agent by a peer pid that verifies against a recorded session, a member by a device token; anything else is anonymous and may call only `hello`. The spec marks every
  method with the lowest role that may call it, and `hello` returns the caller's role and the
  methods it may use, so the UI can grey the rest. Founder only: schedules, role changes, the
  roster, settings and device management. The agent role keeps every `hl` command an agent runs today,
  because after phase 1 they all go through the daemon: the task verbs (`new`, `handoff`, `done`,
  `reopen`), `progress`, `inbox` and `read`, `say`, `ask` and the push family, `land`, the hooks' record
  path, `agent_set`, and `wake`, `close`, `restart` and `park` of another agent (a manager relaunching
  a report is a live workflow, and these go through the daemon so an agent's sandbox need not read the
  process table). Those four keep the state checks they have today; this plan adds no new one. Barred
  from agents are the person-at-the-terminal methods (`input`, `keys`, `interrupt`, `prompt.answer`,
  `input.take`, `input.release`, `terminal.*`) and device and settings management.
- **Devices.** A person pairs a device with a one-time code shown on the desktop (a QR for a phone;
  it carries the listener's certificate fingerprint). `device.pair` makes the code, `device.redeem`
  trades it for a token, `device.revoke` cancels it and closes that device's connections. Tokens
  are stored hashed with a last-seen time. These are this system's own tokens; no Claude credential
  is read or stored.
- **Actor and retry.** Every write records an event naming the actor (user or device). Every write
  takes a `request_id`; the daemon keeps the reply for a day, and a retry with the same id returns
  that reply without repeating the action.

### Session control

The IDE and the web UI drive Claude Code directly, so the API covers a session end to end. Most of
it exists as legacy verbs and moves under the new protocol.

| Method | What it does | Becomes / new |
|---|---|---|
| `session.start`, `session.wake` | Launch a session in an agent's terminal (optionally on a named account), or resume a closed one. | `start`, `wake` |
| `session.stop {mode}` | `exit` asks Claude to quit; `close` ends the session and keeps the terminal; `kill` ends the process. | `close`; `exit` and `kill` are new |
| `session.restart {fresh}`, `session.park` | Restart, or park. | `restart`, `park` |
| `session.input {agent, text, enter}` | Type text at the prompt. | `message` is the daemon's own queued, retried typing; a person's input is its own path |
| `session.keys {agent, keys}` | Named keys (`esc`, `enter`, `up`, `down`, `tab`, `shift-tab`, `ctrl-c`, digits) for menus and dialogs. | new |
| `session.interrupt {agent}` | Stop the turn in progress (Esc). | new |
| `prompt.list {agent}`, `prompt.answer {prompt, choice}` | The open permission prompts and dialogs, and the answer; `choice` is an offered key, or `{text}` for a dialog's free-text answer. | `answer_block`, `trust`; the list is only in the db today |
| `terminal.close {agent}` | Close the host's terminal to free it; refused while a session runs. Not `session.stop close`, which ends the session and keeps the terminal. | new |
| `input.take {agent}`, `input.release {agent}` | Become, or stop being, the one who types at this terminal. | new |

Each method passes only the gates that fit what it does. Every refusal is a `refused` reply with
the gate's own reason; nothing is silently dropped or queued behind the caller's back.

| Gate | `input` | `keys` | `interrupt` | `prompt.answer` | `start`, `wake`, `stop`, `restart` |
|---|---|---|---|---|---|
| Role: not `agent` | yes | yes | yes | yes | no, any role (as today) |
| Agent's terminal lock, the one the tick holds | yes | yes | yes | yes | yes |
| Input holder: refused while someone else holds | yes | yes | yes | yes | no |
| Wall (`wall_reason`, the usage limit): typing would only spend quota | yes | no | no | no | no |
| Screen blocker: a dialog is open, so a typed line would answer it | yes | no | no | no | no |
| The prompt is still the one the caller saw (`prompt` id) | no | no | no | yes | no |
| Routing table (`enforce_edges`) | no | no | no | no | no |

The last row is deliberate: the routing table governs requests between agents, not a person typing.
`land` and `land_pass` keep their session token check unchanged. A person who needs to type at a
wall or past a dialog uses `keys` or `prompt.answer`, which do not start a turn.

- **The daemon never interrupts by itself.** Esc happens only on an explicit `session.interrupt`.
- **Input holder.** Each terminal has one holder: `daemon` (the default) or a named person or
  device, with the time they took it. While someone else holds it, the daemon delivers no task and
  presses no key there; the work stays queued with the existing "held" mark. A holder who is idle
  for a set time hands back to the daemon. `input` and `keys` from a non-holder are a short hold:
  the daemon waits for the line to land, then goes on. The daemon's own typing is recorded as an
  event so the terminal channel can mark it.
- **Control is an event.** Each call is recorded with its actor, so the tick sees that a person
  typed or interrupted instead of guessing why the agent's state changed.
- **Prompt ids.** `prompt.answer` names the prompt it answers. If the open prompt has changed, the
  reply is `stale_prompt` and nothing is sent. The prompt carries its kind, id, tool, command,
  reason, lines and offered keys; tool, command and reason need a parser for Claude's permission
  prompt that goes past what the dialog parser reads today (task 9).

### Terminal channel

A terminal stream is its own subscription, apart from the event stream: high volume, per terminal,
with its own backpressure. `terminal.attach {agent | terminal}` replies with a snapshot (the current
frame and one page of history) and then pushes `frame {seq, changed rows}` notices, `resize`
notices and `marker` notices (the daemon typed; control events). `terminal.history {before, limit}`
pages scrollback. `terminal.resize {cols, rows}` is accepted from the input holder only and restored
when they leave, where the backend can resize.

- **The daemon serves it.** The IDE and the web UI no longer read terminals themselves; one poller
  per terminal runs only while someone is attached, at most about 5 reads a second however many
  clients watch. A client that falls behind is sent a fresh frame, not a backlog.
- **What the backends can give.** Orca's `terminal read` returns either the current frame as text or
  an accumulated stream with escapes stripped, which its own documentation calls unsuitable for
  judging what is rendered. tmux's `capture-pane` is also text. So frames are text rows, without
  colour or cursor-addressed output, and the UI renders a text grid. A true terminal (colours,
  full-screen programs) needs a byte feed: tmux `pipe-pane` can give one, Orca has none. That is a
  limit of the backends, not of the contract, and the frame format has a place for styled rows so
  it can be added without a protocol change.
- **Detaching never stops the agent.**

### What the web UI gets

Each need in the UI's data list (section 8 of its design), and the method that serves it. Live means
pushed on a topic; `topics` are `status`, `accounts`, `agents`, `inbox`, `tasks`, `notifications`,
`operations` and `chat`, plus the terminal channel above.

| Screen | Methods |
|---|---|
| Status bar | `daemon.status` (alive, version, protocol, terminal host name and health, agent counts by state, inbox count), `accounts.list`. Live. |
| Rail | `agents.list`: the stored view (role, model, effort, state and since, block kind and since, account, current task, cache deadline, has-dialog, parent). Live. |
| Session, state | `agent.get` (the view plus worktree path and branch, terminal handle, session id, open or closed, input holder, open prompt), `agent.timeline`, `prompt.list`. Live. |
| Session, terminal | `terminal.attach`, `terminal.history`, `terminal.resize`. |
| Session, actions | `session.*`, `input.*`, `prompt.answer` above; each replies accepted or `refused {reason}` and takes `request_id`. |
| Inbox | `inbox.list` (kind, agent, title, body, evidence, task, times, read or answered, decision options, prompt payload), `inbox.mark_read`, `inbox.answer`, `inbox.reply`. Live. |
| Tasks | `tasks.list`, `task.get` (state, relation, response mode, time box, times, evidence, hold or block reason, delivery history, linked events), `task.new`, `task.withdraw`, `task.cancel`, `task.edit`. Live. |
| Team | `team.get` (agents, roles, allowed edges, role and base file metadata), `hr.list` (proposals with their diff), `hr.decide`. |
| Accounts | `accounts.list` (login state, 5-hour and weekly use with reset and source mark, cap, agents on it, recent moves), `account.login`, which returns a terminal to `terminal.attach`. Live. |
| Worktrees | `repos.list`, `worktrees.list` (path, branch, owner, parent and child, status, changed files), `worktree.diff {path, file}`. The diff is read-only git run by the daemon; new. |
| New agent | `agent.options` (repos, roles, models, accounts offered, free names). |
| Schedules | `schedules.list`; founder only: `schedule.add`, `schedule.edit`, `schedule.remove`. |
| Stats | `stats.get {window}` for 24 hours or 7 days. |
| Settings | `settings.get` (timings, terminal host choice and health, trust folders, hooks installed), `settings.set`, `devices.list` (with last seen), `device.pair`, `device.redeem`, `device.revoke`. |
| Chat | `chat.messages {scope}`, `chat.transcript` (replies, tool activity, prompt cards, task events, working state), `chat.locate`, `chat.send`, `chat.answer`, `chat.dismiss`. Live on `chat`. `hello` carries `capabilities` (chat, review). |
| Notifications | `events.feed {since}`: task done, prompt asked, stall, account out, each with a deep-link target; resumable by cursor. Live. |
| Everywhere | `whoami` (viewer, device, role, methods allowed); every write is an event with its actor, shown in `events.feed` and `agent.timeline`. |

The UI's seven cross-cutting needs land as: login and device tokens (callers and roles); one event
stream with resume (event stream, so a reconnecting client cannot miss a prompt); a separate
terminal channel; the input holder; prompt ids; request ids; roles per method.

### Auto-start

The client library `ensure_daemon()` is the one place that finds the daemon: connect, and on a
refusal ask launchd (`launchctl kickstart`) to start the service, then retry for a bounded time. It
never spawns a daemon itself, so there is never a second one. If the daemon does not come up, the
caller gets `daemon_down` with the log path and nothing was done.

### Agent hooks and the record path

Hooks record events from inside an agent session, on every tool call, and must not lose them while
the daemon restarts. A hook sends its event over the socket; if the daemon is down it appends the
event to a spool file in the home directory, and the daemon ingests and removes the spool at start.
This is the one place a caller keeps a local fallback, and it only records: it never decides or acts.
The hook client must start fast, because it runs per tool call; measure it before and after.

### Schema ownership

A `schema_version` table with numbered, ordered migrations replaces the column-exists checks. The
daemon runs them at start. `db.connect()` stops migrating, and refuses a database newer than the
code knows. This is a precondition for phase 2, but it lands in phase 1 because it is also a plain
fix: two old processes can race a migration today.

### Moving the callers

| Caller | Move |
|---|---|
| `ui/tui.py` | Non-Textual half to a `view/` package in core (breaks core importing ui). |
| `cli/` | Command by command onto the client library. Reads first, then writes. |
| `ui/` | `chat_page` raw SQL becomes read methods; the 29 writes become write methods; `actrun` is replaced by method calls (the job manager with timeouts and kill stays); the live-terminal read becomes the daemon's terminal channel, and the IDE gains session control through the methods above. |
| `team/`, `usage/` | Their readers in `cli/` and `ui/` call methods; their writers sit behind the daemon. |

The end state is checked by a test: `cli/` and `ui/` may not import `db` or `daemon` internals.
It starts with an allow-list of today's imports, and every migration task shrinks the list. The
list is empty when phase 1 is done.

## Phase 2: Go behind the same contract

### Scope

The Go daemon replaces `daemon/`, `session/`, `usage/` and `vendors/` and the daemon-facing parts
of `actions/` and `team/`. The CLI and the IDE stay Python and keep talking to the socket. `hl
fold` stays the one model call; the daemon starts it as a subprocess, as today. The `orca`,
`tmux` and `git` calls stay subprocesses. The daemon is not in active service, so the port is
straight: no shadow run, no differential runs, no mirroring of Python beyond what the fixtures check.

### The bar

1. **Conformance fixtures** in `contract/fixtures/`, run against the Go binary by `tools/test-go.sh`.
   A method Go serves leaves `go/conformance-missing.txt`.
2. **Tick fixtures** in `contract/tick/fixtures/` (30): a database, a fake clock and a scripted
   terminal host in, the database diff and the ordered terminal operations out. Their outputs are
   frozen once from the Python daemon (`pyrun.py`) and kept as the expected files; the Go tick
   test reads the fixture and compares with the expected file. `compare.py` is not run again.
3. **Ordinary Go unit tests**, `go test -race ./...`, for each module.

### Go requirements

- `go test -race ./...` is a required check: in CI, in the author's done criteria and in the
  auditor's review. A race failure is a failed review, not a note.
- Pure-Go sqlite (no cgo), so the launchd build needs no C toolchain. WAL and `busy_timeout` set the
  way `db.connect()` sets them today.
- One daemon binary, version stamped with the git sha, reported in `hello`.
- Credential rule unchanged: the Go code never reads or stores a credential; whatever the Python
  vendors do today for usage numbers is ported as the same calls and nothing more. Fail closed,
  deterministic given the clock.
- A method Go serves needs no Python implementation first.

### Port order

Sizes are Python lines. Each row is one branch and one auditor review. The Go daemon already has the
sqlite store, the rpc server, hooks ingest, the reads for agents, tasks and inbox, the stream, devices.

| # | Module | What | Lines | Depends on | Methods it brings |
|---|---|---|---|---|---|
| G1 | foundation | `infra` (paths, names, textsafe, timefmt, yaml config, lock, protected and sealed files, gitrun), `team` core (agents, accounts with login state, team routing, trust, agent settings) | 3.8k | | |
| G2 | usage and vendors | windows, prices, token log, collect, status, load, cycle, burn rate, outlook, stats, measure, recover, usage api, the vendor drivers | 6.9k | G1 | `accounts.list`, `stats.get`, `stats.outlook`, `settings.get` |
| G3 | session backends | tmux and orca drivers, screen reading and prompt parsing, blocked, waiting, observe, the guards, auto-accept, the prompt block store | 3.8k | G1 | `prompt.list`, `prompt.answer` |
| G4 | terminal channel | bytes and text frames, screen state, the shared poller, input holder, gates | 1.4k | G3 | `terminal.*`, `input.*` |
| G5 | messaging and delivery | ask routing, sends, landing, delivery, task queue and holds, notice digest, the task subset of `actions.tasks` the supervisor calls | 3.6k | G1, G3 | |
| G6 | launch and lifecycle | launch, spawn, recover, lifecycle, reconciler, idle close, locks | 3.3k | G2, G3, G5 | |
| G7 | record path | handoff and its note, fold trigger (subprocess `hl fold`), events, digest, spool, subscriptions | 2.8k | G5 | `events.feed` |
| G8 | tick and policies | triggers, evaluate, cost pilot, time box, login expiry, dev mode, apply settings, the loop | 1.9k | G5 to G7 | |
| G9 | session methods | `session.*` (start, stop, park, wake, restart, input, type, keys, interrupt), `agent.create`, `operation.get`, `agent.options` | 0.5k | G3, G4, G6 | those |

G2 and G3 run in parallel once G1 is in; G4 follows G3; G5 follows G1 and G3. G2 checks first what
the Python usage code reads for `login_state` and the usage numbers and ports those calls only.

### Cutover

Cut over when the conformance fixtures, the tick fixtures and `go test -race` pass; fix forward. Swap
the launchd service to the Go binary. The schema stays readable by both until the Python daemon is
deleted, so no migration may drop or reshape a column, and rollback is swapping the service back.

**Cutover checklist: what the Python daemon does and where Go does it.** Status: *done* is wired in the
daemon or tick and tested; *unwired* is ported and tested but nothing calls it; *gap* has no Go; *dropped*
is cut by the founder's rule that new work goes only in Go or the founder's rule against dialog
auto-answers. Agents stay terminal Claude Code sessions and the Go daemon supervises them; structured
work is paused, not the plan.

| Duty (Python) | Go file | Test | Status |
|---|---|---|---|
| Per-agent turn: wake due, start/wake for work, deliver, handoff on trigger (policies, evaluate) | `tick/evaluate.go`, `tick/triggers.go` | `tick/supervise_test.go`, `TestEvaluate`, `compose/tick_test.go`, `contract/tick` fixtures | done |
| Cost pilot (renew, fold when idle) | `tick/costpilot.go` | `tick/costpilot_test.go` | done |
| Time box, stuck waits | `tick/timebox.go`, `tick/waitfor.go` | `tick/timebox_test.go`, `TestStuckWaitsEmpty` | done |
| Apply settings | `tick/applysettings.go` | `tick/applysettings_test.go` | done |
| Reconciler sweep, handoff expiry each tick | `lifecycle/reconciler.go` | `lifecycle/reconciler_test.go` | done |
| Idle close | `lifecycle/idle.go` | `lifecycle/idle_test.go` | done |
| Agent view refresh | `compose/agentview.go` | `agentview` tests | done |
| Usage poll, burn rate, samples, token log | `usagepoll/`, `compose/usagepoll.go`, `tick/evaluate.go` | `usagepoll/poll_test.go`, `compose/tickparity_test.go` | done (with `-cutover`) |
| Land worker | `daemon/daemon.go` `landLoop`, `lands/` | `lands/lands_test.go` | done |
| Hook spool ingest | `daemon/daemon.go` `spoolLoop`, `hooks/spool.go` | `hooks/hooks_test.go` | done |
| Stream detectors (agents, notifications, chat, hr, tasks, inbox) | `watcher/*.go` | `watcher/*_test.go`, `watcher/parity_test.go` | done (with `-cutover`) |
| Relay socket and `-gateway` | `daemon/gateway.go`, `daemon/network.go` | `daemon/gateway_test.go`, `daemon/network_test.go` | dropped: the Go daemon is the daemon and serves `-listen` itself |
| Notice digest and stopped-agent notices | `supervisor/digest.go` `SweepNotices` | `supervisor/more_test.go` | unwired |
| Task queue sweep, task holds sweep | `supervisor/queue.go`, `supervisor/holds.go` | `supervisor/tasks_test.go`, `supervisor/sweeps_test.go` | unwired |
| Subscription endings and plan check | `subscriptions/subscriptions.go` `Sweep` | `subscriptions/subscriptions_test.go` | unwired |
| Startup: `reconcile`, `migrate_old_messages`, `start_waiting`, `reload_pending`, founder key, daemon singleton | `lifecycle/reconciler.go`, `supervisor/messaging.go`, `tick/evaluate.go`, `sessiontoken` | the units only; `daemon/ownprompts_test.go` for the lock | unwired |
| Loop extras: dev-mode restart, log rotation, fd check, scratch sweep, SIGUSR1 stack dump, account relink | `tick/devmode.go` (`Loop.Dev` unset), `Loop.Housekeeping` (empty), `Sweeps.RelinkAccounts` (nil) | `tick_test.go` `TestDevMode...` | unwired (rotation, fd check, scratch sweep, SIGUSR1: gap) |
| Schedules runner (cron messages typed at agents) | writes only: `adminwrites/schedules.go` | none for running | gap |
| Midnight rollover and `agent.fold` (the one model call) | stub `Rollover` in `compose/tick.go` | none | gap |
| Login expiry: mark the account, move its agents | `team/accounts.go` marks; reconciler `LiftLogin` is a no-op in `compose/tick.go` | none | gap (the Python detector reads a terminal screen; a structured session needs its own) |
| Blocked sweep, auto-accept, screen snapshots, screen-based prompt watch, folder-trust and past-dialog presses | `session/blocked.go`, `prompts/watch.go`, `lifecycle/trust.go` | their tests | dropped (no dialog auto-answers) |
| Supervising terminal agents | `compose/termport.go`, `term/host.go` (the terminal port, landed) | | gap, in progress: the terminal tick (`-terminal-tick`), peer-inbox delivery, needs-you and `terminal.open` (from the needs-you spec) |

After the switch neither `-own-prompts` nor `-gateway` stays. `-gateway` exists only to front a Python
daemon. `-own-prompts` starts the screen watch, and the daemon reads no screen and answers no dialog
(x-dialogs); `prompt.list` stays, as a read of the `prompt_blocks` rows. The daemon
singleton it takes becomes unconditional (startup row above).

**Terminal agents after cutover.** The Go daemon types nothing at a running terminal session.
- Delivery is the peer inbox only; an agent with no live session has its work held ("no live session") until a wake starts one.
- A handoff, a move to another account and a clear that is owed close the session by closing its pane and start a new session in a new terminal in the same worktree, with no resume id and the handoff record as its first message. The close is proved by the session's pid; a session that outlives its pane is reported and never started over.
- There is no in-place `/clear`, and `input.take` and `input.release` are gone: nobody holds a terminal's input. A person watching sees the pane close and a new one open.
- A forced move of a busy terminal agent is refused with the reason, since a turn there cannot be interrupted without a key.
- A handoff holds the agent's lock while it waits: up to 60 s for the session to end after its pane closes, and up to a minute for the clear notice to be confirmed. A session that outlives its pane is closed on the first tick after its pid is gone.
- The screen is still read to reap a terminal that is gone and to start a session (the folder-trust and bypass screens at launch).

## Tasks for the sub-team

Three reports under this daemon: `api` (the Python server side), `contract` (the spec, the
fixtures and the harnesses) and `go` (the Go daemon). Moving the CLI and the UI belongs to the
`core` and `ui` agents, who get the contract from this team and ask it for missing methods. The web
UI team builds its fake daemon and TypeScript client from task 1 and the fixtures, so task 1 goes
first and is published as soon as it is reviewed. Size is S (a part of a session), M (a session),
L (several).

**Phase 1**

| # | Owner | Task | Size | Done when |
|---|---|---|---|---|
| 1 | contract | The protocol spec as one machine-readable file in `contract/` (framing, handshake, versioning, error codes, roles per method, every method's request and response shape, topics), with the docs and the TypeScript types generated from it. | M | Every row of "What the web UI gets" and every session-control method is in it; `core`, `ui` and the web UI team agree it covers every call they make. |
| 2 | api | Persistent connections, JSON-RPC framing, `hello`, errors, legacy-verb adapter. | M | The `hello` and errors fixtures pass; the 17 legacy verbs still pass their tests. Depends on 1. |
| 3 | api | Schema version table and ordered migrations; the daemon migrates, clients do not. | M | An old database migrates once, races between processes cannot corrupt it, a newer database is refused. |
| 4 | api | Per-agent view stored and served: `agents.list`, `agent.get`, `agent.timeline`. | M | The UI's state column renders from the view alone. Depends on 2. |
| 5 | api | Read methods for the rest of the table: tasks, inbox, team and proposals, accounts, repos and worktrees (with the read-only diff), schedules, stats, settings, `daemon.status`. Three tasks by area. | L | Each has a fixture; no caller needs raw SQL. Depends on 2. |
| 6 | api | Write methods: the 29 UI writes, the CLI's writes and the missing CLI twins, `agent.create` with its progress notices. Three tasks by area. | L | Each has a fixture and a refusal case. Depends on 2, 8. |
| 7 | api | `subscribe`, topics, cursors, `events.feed`, `gap`, the `data_version` watch, slow-client drop. | M | A client sees every change, including one made by another process, and reconnects without loss. Depends on 2. |
| 8 | api | Roles per method, `whoami`, request ids with the day's reply store, the actor on every write's event. | M | A call below its role is refused; a retried write is not repeated; every write's event names its actor; every `hl` command an agent runs today still works from an agent session. Depends on 2. |
| 9 | api | Session control: `session.*` (legacy verbs re-exposed, plus `exit`, `kill`, `keys`, `input`, `interrupt`), `prompt.list` and `prompt.answer` with prompt ids, the input holder, and a parser for the permission prompt's tool, command and reason. Two tasks: the methods with their gates, then the prompt parser. | L | Each method has a fixture and a refusal case for every gate in its column, including a call from inside an agent session, a held terminal, the wall, an open dialog and a stale prompt; the tick is shown not to type beside a control call. Depends on 2, 8. |
| 10 | api | The terminal channel: `terminal.attach`, `history`, `resize`, frames, markers, the shared poller, resync, rate cap. | M | Two clients watch one terminal with one poll; a slow client gets a fresh frame; the poll stops when the last client leaves; resize is refused to a non-holder. Depends on 2, 9. |
| 11 | api | The network listener, pairing, device tokens, revocation, encrypted transport. Off by default. | L | A paired device calls and is refused above its role; a revoked device is cut off at once; with the listener off nothing listens. Depends on 8. |
| 12 | api | `ensure_daemon()` and the hook spool. | M | Kill the daemon: the next `hl` call starts it and works; events from hooks during the outage are all present after. Hook start time measured. |
| 13 | core | Move `ui/tui.py` non-Textual half to `view/`; move `cli/` onto the client library, reads then writes. | L | The import-allow-list test passes with no `cli/` entries. Depends on 5, 6. |
| 14 | ui | Move the IDE: `chat_page` SQL to reads, writes to methods, `actrun` to calls, the live-terminal read to the terminal channel, session control wired in. | L | No `ui/` entries left in the allow-list; the IDE updates from `subscribe`, not polling, and can start, stop, type to, interrupt and answer prompts of a session. Depends on 4 to 10. |
| 15 | contract | The import allow-list test, and delete the legacy adapter once nothing uses it. | S | Test merged; adapter removed. Last in phase 1. |

**Phase 2** (the Go work starts after tasks 1, 2, 3 and the `hello` and errors part of task 16; the
rest of the fixtures arrive as phase 1 lands their methods)

| # | Owner | Task | Size | Done when |
|---|---|---|---|---|
| 16 | contract | Conformance suite as JSON fixtures in `contract/`, runnable against a socket path; the same fixtures drive the web UI team's fake daemon. First the `hello` and errors fixtures, then one set per phase 1 task. | M | The Python daemon passes everything its phase 1 tasks have landed. |
| 17 | contract | Tick fixtures: fixture format, fake clock, fake terminal backend; 30 scenarios from the Python suite, their outputs frozen. | L | Done: `contract/tick`. |
| 18 | go | Module, the local and network listeners, `hello`, roles, sqlite and migrations, `go test -race` in CI. | M | The `hello` and errors fixtures pass on Go. Depends on 2, 3, 16. |
| 19 | go | The record path: events, hooks ingest, collect, token log, spool, handoff, fold trigger. | L | Conformance for these passes on Go. |
| 20 | go | Reads, writes, request ids, subscribe and device auth, by area in the order of the tables above. Several tasks. | L | Each area's fixtures pass on Go. |
| 21 | go | Usage and vendors: burn rate, usage reads, measure, vendor drivers. | L | See G2. |
| 22 | go | The port order above, G1 to G9. | L | Conformance, tick fixtures and `go test -race` pass. |
| 23 | | Dropped: no shadow run. | | |
| 24 | go | Cutover and rollback notes, service swap, deletion of the Python daemon. | M | Founder approves the cutover. |

Every `api`, `contract` and `go` task goes to the auditor before it closes. The `go` tasks (18 to 22
and 24) add `go test -race` to the review.

## Risks

- **The tick fixtures are the safety net.** Thirty scenarios from past incidents with frozen outputs; a
  green Go tick means they pass, and a stuck case found later becomes a fixture.
- **View drift.** The per-agent view is a new thing the daemon must keep correct on every state
  change. A test compares it with a recomputation from first principles on the existing fixtures.
- **Hook latency.** The record path runs on every tool call. If the socket round trip is not at
  least as fast as today's direct write, hooks go through a small compiled helper instead.
- **A remote door on a machine that runs agents.** The network listener lets a phone answer
  permission prompts and type into sessions that run with the user's rights. It is off by default,
  needs explicit pairing, is encrypted, records every action with its device, and can be revoked at
  once; the roles keep device management and settings with the founder.
- **Terminal fidelity.** Frames are text, because the backends give text. If the web UI needs
  colours or full-screen programs, a byte feed must come from the backend (tmux can; Orca cannot
  today), and that choice is the founder's.
- **Two languages on one database** exist only in the rollback window. Migrations in that window are additive.
- **`data_version` polling** is a stopgap, and it disappears with the last outside writer.
- **The estimate is rough.** The scope is about 27k Python lines in four packages plus the daemon-facing
  parts of `team` and `actions`; the first two modules will show whether the sizes hold, and the plan is re-cut then.
