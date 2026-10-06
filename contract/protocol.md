<!-- Generated from contract/protocol.json by contract/generate.py; do not edit. -->
# Handline daemon protocol (protocol 1)

OpenRPC-shaped. Source of contract/protocol.md and contract/protocol.ts: regenerate with `python3 contract/generate.py`. Extensions are x-* keys.

## Framing

- **Transport:** Unix socket (0600, local user is founder), one JSON-RPC 2.0 object per line; WebSocket (network listener, off by default), one object per text frame.
- **Reach:** v1 reaches this machine, the LAN or Tailscale only: the network listener binds the interface the config names (default loopback), says whether it uses TLS or Tailscale encryption, and there is no relay or public endpoint.
- **Lines:** UTF-8 JSON, terminated by \n, at most 1 MiB. No batches (bad_request). A connection may carry many calls; a one-shot call works.
- **Ids:** Request id is the client's integer or string; replies may arrive in any order. Notifications (no id) flow from the daemon after subscribe or terminal.attach, interleaved with replies.
- **Params:** By name only.
- **Legacy:** A line with `verb` and no `jsonrpc` is a legacy request, answered {ok, said} by the adapter until its last caller has moved.
- **Request id:** Every write takes request_id. The daemon keeps each successful reply for 24 hours; a retry with the same id and the same params returns it with replayed:true and repeats nothing. The same id with other params is bad_request. Refusals and errors are not stored: a retry is judged again.

## Handshake and versions

The first call is hello. Compatibility: adding a method or field never changes protocol; removing or changing one does, and the daemon then serves old and new side by side for one release. Clients ignore unknown fields and unknown enum-like strings (state words, kinds, event types). A client that does not know the daemon's protocol refuses to run and prints the one command that fixes it: `hl service install`.

## Roles

Order: anonymous < agent < member < founder. x-role on a method is the lowest role that may call it. The local user on the local socket is founder; a paired device is member; a call from inside an agent session is agent (peer-pid check); an unauthenticated network connection is anonymous. A call below its role is refused, gate `role`.

## Errors

Every error is a JSON-RPC error whose `data.code` is the stable string. A refusal carries the reason; fail-closed is `refused`, never a silent default.

| Code | RPC code | data |
|---|---|---|
| `not_found` | -32001 | Names what was not found. |
| `refused` | -32002 | RefusalData |
| `stale_prompt` | -32003 | {code, current: Prompt\|null}; nothing was sent. |
| `daemon_busy` | -32004 | {code, retry_after_ms} |
| `bad_request` | -32600 | {code, field?}; also JSON-RPC parse errors (-32700) and invalid params (-32602). |
| `internal` | -32603 | {code} |
| `daemon_down` | none (client side) | {code, log_path}; raised by the client library, never sent on the wire. |

## Gates

| Gate | Refuses when |
|---|---|
| `role` | Caller's role is agent: a person-at-the-terminal method. |
| `terminal_lock` | The agent's terminal lock, the one the tick holds. |
| `input_holder` | Someone else holds the input. A daemon holder is a short hold, not a refusal. |
| `wall` | The usage limit (wall_reason): typing would only spend quota. |
| `screen_blocker` | A dialog is open, so a typed line would answer it. Use keys or prompt.answer. |
| `state` | The state checks the legacy verb has today; this protocol adds none. |
| `unsupported` | Reserved and not implemented. |
| `session_token` | Session token and peer-pid check, unchanged. |

## Methods

A method marked write takes a required `request_id`.

| Method | Role | Write | Params | Result | Errors | Gates |
|---|---|---|---|---|---|---|
| `hello` | anonymous |  | `client`, `protocol`, `token?` | `{` | `refused`, `bad_request` |  |
| `whoami` | agent |  | none | `{` |  |  |
| `subscribe` | agent |  | `topics`, `since?` | `{` |  |  |
| `unsubscribe` | agent |  | `topics` | `{ [key: string]: unknown }` |  |  |
| `daemon.status` | member |  | none | `DaemonStatus` |  |  |
| `events.feed` | member |  | `since?`, `topics?`, `limit?` | `{` |  |  |
| `operation.get` | member |  | `operation` | `Operation` | `not_found` |  |
| `agents.list` | agent |  | none | `{` |  |  |
| `agent.get` | agent |  | `agent` | `AgentDetail` | `not_found` |  |
| `agent.timeline` | member |  | `agent`, `before?`, `limit?` | `{` | `not_found` |  |
| `agent.options` | member |  | none | `AgentOptions` |  |  |
| `agent.create` | founder | yes | `name`, `role`, `repo`, `base?`, `model?`, `effort?`, `account?`, `parent?`, `request_id` | `Accepted & {` | `refused`, `not_found` |  |
| `agent.set` | agent | yes | `agent`, `values`, `speaker?`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `agent.fold` | member | yes | `agent`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `agent.move` | member | yes | `agent`, `account`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `session.start` | agent | yes | `agent`, `account?`, `repo?`, `base?`, `request_id` | `Accepted` | `refused`, `not_found` | terminal_lock, state |
| `session.wake` | agent | yes | `agent`, `request_id` | `Accepted` | `refused`, `not_found` | terminal_lock, state |
| `session.stop` | agent | yes | `agent`, `mode`, `fresh?`, `request_id` | `Accepted` | `refused`, `not_found` | terminal_lock, state |
| `session.restart` | agent | yes | `agent`, `fresh?`, `force?`, `request_id` | `Accepted` | `refused`, `not_found` | terminal_lock, state |
| `session.park` | agent | yes | `agent`, `request_id` | `Accepted` | `refused`, `not_found` | terminal_lock, state |
| `session.input` | member | yes | `agent`, `text`, `enter?`, `request_id` | `Accepted` | `refused`, `not_found` | role, terminal_lock, input_holder, wall, screen_blocker |
| `session.keys` | member | yes | `agent`, `keys`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` | role, terminal_lock, input_holder |
| `session.interrupt` | member | yes | `agent`, `request_id` | `Accepted` | `refused`, `not_found` | role, terminal_lock, input_holder |
| `prompt.list` | member |  | `agent` | `{` | `not_found` |  |
| `prompt.answer` | member | yes | `agent`, `prompt`, `choice`, `request_id` | `Accepted` | `refused`, `not_found`, `stale_prompt` | role, terminal_lock, input_holder |
| `input.take` | member | yes | `agent`, `request_id` | `Accepted & {` | `refused`, `not_found` | role, input_holder |
| `input.ask` | member | yes | `agent`, `request_id` | `Accepted` | `refused` | unsupported |
| `input.release` | member | yes | `agent`, `request_id` | `Accepted` | `refused`, `not_found` | role |
| `chat.messages` | member |  | `agent`, `scope`, `before?`, `since?`, `limit?` | `{` | `not_found` |  |
| `chat.transcript` | member |  | `agent`, `before?`, `since?`, `limit?` | `{` | `not_found` |  |
| `chat.locate` | member |  | `agent`, `request` | `{` | `not_found` |  |
| `chat.send` | member | yes | `agent`, `body`, `task?`, `request_id` | `Accepted & {` | `refused`, `not_found` |  |
| `chat.answer` | member | yes | `request`, `text`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `chat.dismiss` | member | yes | `request`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `chat.withdraw` | member | yes | `request`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `terminal.attach` | member |  | `agent?`, `terminal?`, `history?` | `TerminalSnapshot` | `not_found`, `bad_request` |  |
| `terminal.history` | member |  | `terminal`, `before`, `limit` | `HistoryPage` | `not_found` |  |
| `terminal.resize` | member | yes | `terminal`, `cols`, `rows`, `request_id` | `Accepted` | `refused`, `not_found` | role, input_holder |
| `terminal.detach` | member |  | `terminal` | `{ [key: string]: unknown }` | `not_found` |  |
| `inbox.list` | agent |  | `unread?`, `kind?`, `agent?`, `before?`, `limit?` | `{` |  |  |
| `inbox.get` | agent |  | `id` | `InboxItem` | `not_found` |  |
| `inbox.mark_read` | agent | yes | `ids`, `request_id` | `Accepted` | `not_found` |  |
| `inbox.answer` | member | yes | `id`, `option?`, `text?`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `inbox.reply` | agent | yes | `id`, `text`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `tasks.list` | agent |  | `owner?`, `from?`, `state?`, `parent?`, `before?`, `limit?` | `{` |  |  |
| `task.get` | agent |  | `task` | `TaskDetail` | `not_found` |  |
| `task.new` | agent | yes | `owner`, `title`, `body`, `time_box`, `relation?`, `response?`, `parent?`, `request_id` | `Accepted & {` | `refused` |  |
| `task.handoff` | agent | yes | `task`, `to`, `note`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.done` | agent | yes | `task`, `evidence`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.reopen` | agent | yes | `task`, `note?`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.progress` | agent | yes | `task`, `note`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.withdraw` | member | yes | `task`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.cancel` | member | yes | `task`, `reason?`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.edit` | member | yes | `task`, `title?`, `body?`, `time_box?`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `say` | agent | yes | `target`, `body`, `task?`, `no_task?`, `parent?`, `request_id` | `Accepted` | `refused` |  |
| `ask` | agent | yes | `target`, `title`, `body`, `options?`, `request_id` | `Accepted & {` | `refused` |  |
| `push` | agent | yes | `target`, `request?`, `request_id` | `Accepted` | `refused` |  |
| `push.answer` | agent | yes | `requester`, `request_id` | `Accepted` | `refused` |  |
| `push.cancelled` | agent | yes | `request`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `land` | agent | yes | `sha`, `message`, `tree?`, `request_id` | `Accepted` | `refused` | session_token |
| `land.pass` | agent | yes | `sha`, `request_id` | `Accepted` | `refused` | session_token |
| `land.authorize` | agent | yes | `agent`, `land_verb`, `sha`, `key`, `request_id` | `Accepted` | `refused` | session_token |
| `hook.record` | agent | yes | `agent`, `kind`, `at`, `data`, `request_id` | `Accepted` | `refused` |  |
| `team.get` | agent |  | none | `{` |  |  |
| `hr.list` | member |  | `state?` | `{` |  |  |
| `hr.decide` | founder | yes | `proposal`, `decision`, `note?`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `accounts.list` | member |  | none | `{` |  |  |
| `account.set_cap` | founder | yes | `account`, `cap_pct`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `account.login` | founder | yes | `account`, `request_id` | `Accepted & {` | `refused`, `not_found` |  |
| `repos.list` | member |  | none | `{` |  |  |
| `worktrees.list` | member |  | `repo?`, `owner?` | `{` |  |  |
| `worktree.diff` | member |  | `path`, `file?` | `{` | `not_found` |  |
| `worktree.set_status` | member | yes | `path`, `status`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `schedules.list` | member |  | none | `{` |  |  |
| `schedule.add` | founder | yes | `target`, `cron`, `message`, `request_id` | `Accepted & {` | `refused`, `bad_request` |  |
| `schedule.edit` | founder | yes | `schedule`, `target?`, `cron?`, `message?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `schedule.remove` | founder | yes | `schedule`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `stats.get` | member |  | `window` | `Stats` |  |  |
| `settings.get` | founder |  | none | `Settings` |  |  |
| `settings.set` | founder | yes | `changes`, `request_id` | `Accepted` | `refused`, `bad_request` |  |
| `settings.trust` | founder | yes | `account`, `worktrees`, `request_id` | `Accepted` | `refused` |  |
| `devices.list` | founder |  | none | `{` |  |  |
| `device.pair` | founder | yes | `name?`, `request_id` | `Accepted & {` | `refused` |  |
| `device.redeem` | anonymous | yes | `code`, `name`, `request_id` | `Accepted & {` | `refused` |  |
| `device.revoke` | founder | yes | `device`, `request_id` | `Accepted` | `refused`, `not_found` |  |

Per-method summaries and field types are in `protocol.json` and `protocol.ts`.

### `hello`

First call on every connection: versions and who the caller is. A client whose protocol is below min_client gets `refused` (gate `protocol`) with hint `hl service install`. A newer client gets a normal reply and decides itself whether to run. Any other call before hello is bad_request.

### `whoami`

The viewer, the device, the role and the methods allowed.

### `subscribe`

Turn the connection into a stream of `event` notices; replays after `since` first.

### `unsubscribe`

Stop some topics.

### `daemon.status`

Status bar: alive, versions, terminal host, agent counts, inbox count.

### `events.feed`

Resumable feed of events with their deep-link targets.

### `operation.get`

A long-running operation's current state.

### `agents.list`

The rail: the stored view of every agent.

### `agent.get`

One agent: the view plus session and terminal facts.

### `agent.timeline`

State changes, deliveries, handoffs, key presses; newest first.

### `agent.options`

What the new-agent form offers.

### `agent.create`

Register, make the worktree, start. Long-running: progress arrives on `operations`.

### `agent.set`

An agent changes its own settings (legacy agent_set).

### `agent.fold`

Run hl fold for an agent (legacy fold).

### `agent.move`

Move an agent to another account (legacy move).

### `session.start`

Launch a session in the agent's terminal, optionally on a named account.

### `session.wake`

Resume a closed session.

### `session.stop`

exit asks Claude to quit; close ends the session and keeps the terminal; kill ends the process.

### `session.restart`

Restart a session.

### `session.park`

Park an agent.

### `session.input`

Type text at the prompt; Enter optional.

### `session.keys`

Named keys for menus and dialogs.

### `session.interrupt`

Stop the turn in progress (Esc). The daemon never does this by itself.

### `prompt.list`

The open permission prompts and dialogs.

### `prompt.answer`

Answer the prompt the caller saw; if it changed nothing is sent.

### `input.take`

Become the one who types at this terminal.

### `input.ask`

Reserved: ask the holder for input. Refused (gate `unsupported`) until implemented; see capabilities.input_ask.

### `input.release`

Hand the terminal back to the daemon.

### `chat.messages`

Messages of an agent's chat, newest first. Page with before (older) or since (newer).

### `chat.transcript`

The agent's replies, tool activity, prompt cards and task events as ordered items, plus its working state.

### `chat.locate`

Which scope and page show a message number.

### `chat.send`

A message to an agent through the daemon's delivery (queued, typed, landed); needs no input hold.

### `chat.answer`

Answer a message that asked something.

### `chat.dismiss`

Dismiss a message.

### `chat.withdraw`

Withdraw a message not yet landed.

### `terminal.attach`

Subscribe to one terminal; replies with a snapshot, then pushes frame, resize and marker notices.

### `terminal.history`

Page scrollback; absolute line indexes.

### `terminal.resize`

Resize; accepted from the input holder only, restored when they leave.

### `terminal.detach`

Stop receiving this terminal. Never stops the agent.

### `inbox.list`

Inbox items; an agent sees its own.

### `inbox.get`

One item (hl read).

### `inbox.mark_read`

Mark items read.

### `inbox.answer`

Answer a decision item with one of its options or free text.

### `inbox.reply`

Reply to an item.

### `tasks.list`

Task rows.

### `task.get`

One task with body, delivery history and linked events.

### `task.new`

Create a task.

### `task.handoff`

Hand a task to another agent.

### `task.done`

Finish a task with evidence.

### `task.reopen`

Reopen a task.

### `task.progress`

Record progress on a task (hl progress).

### `task.withdraw`

Withdraw a task the caller made.

### `task.cancel`

Cancel a task.

### `task.edit`

Edit a task that is not yet delivered.

### `say`

A message typed at once; needs no answer, starts no work.

### `ask`

A question that needs an answer.

### `push`

Tell a target about what was queued for it (legacy push).

### `push.answer`

Put an answer in front of the agent that asked (legacy push_answer).

### `push.cancelled`

Tell an agent a delivered request was closed under it (legacy notify_cancelled).

### `land`

Request a land; accepted only from the session's own hl land (session token and peer pid, as today).

### `land.pass`

Record a land pass.

### `land.authorize`

The session's hl land vouches for the next land verb (legacy land_authorize).

### `hook.record`

A hook records an event; the spool replays it with the same request_id.

### `team.get`

Agents, roles, allowed edges, role and base file metadata.

### `hr.list`

HR proposals with their diff.

### `hr.decide`

Approve or reject a proposal.

### `accounts.list`

Accounts with login state, use, cap, agents, recent moves.

### `account.set_cap`

Set or clear an account's cap.

### `account.login`

Start a login; returns a terminal for terminal.attach.

### `repos.list`

Repos.

### `worktrees.list`

Worktrees with status and changed files.

### `worktree.diff`

Read-only git diff run by the daemon.

### `worktree.set_status`

Set a worktree's status.

### `schedules.list`

Schedules.

### `schedule.add`

Add a schedule.

### `schedule.edit`

Edit a schedule.

### `schedule.remove`

Remove a schedule.

### `stats.get`

Counts and sums over a window.

### `settings.get`

Timings, terminal host, trust folders, hooks installed.

### `settings.set`

Change settings; keys as settings.get names them.

### `settings.trust`

Trust worktree folders for an account (legacy trust).

### `devices.list`

Paired devices with last seen.

### `device.pair`

Make a one-time pairing code.

### `device.redeem`

Trade a pairing code for a device token. Allowed before hello auth on the network listener.

### `device.revoke`

Cancel a device and close its connections at once.

## Topics

| Topic | Event types (data) | Snapshot method |
|---|---|---|
| `status` | `status.changed` (DaemonStatus) | `daemon.status` |
| `accounts` | `account.changed` (Account) | `accounts.list` |
| `agents` | `agent.changed` (AgentView), `agent.removed` ({name}), `agent.control` (TimelineEntry) | `agents.list` |
| `inbox` | `inbox.added` (InboxItem), `inbox.changed` (InboxItem) | `inbox.list` |
| `tasks` | `task.changed` (Task) | `tasks.list` |
| `notifications` | `task_done` ({task}), `prompt_asked` (Prompt), `stall` ({agent}), `account_out` ({account}) | `events.feed` |
| `chat` | `chat.message` (ChatMessage), `chat.transcript` (TranscriptItem), `chat.working` (Working) | `chat.transcript` |
| `operations` | `operation.progress` (Operation) | `operation.get` |

## Notifications

- `event`: Sent after subscribe, for the subscribed topics, in cursor order. Params: `Event`
- `gap`: retention: refetch the topic's snapshot and resubscribe from the cursor. slow_client: the daemon then closes the connection. Params: `{   reason: "retention" | "slow_client";   cursor: number;   topic?: Topic; }`
- `frame`: full:true carries every line and is what a slow client gets instead of a backlog. Params: `{   terminal: string;   seq: number;   full: boolean;   lines?: Row[];   changed?: {     y: number;     row: Row;   }[];   cursor: Cursor; }`
- `resize`:  Params: `{   terminal: string;   seq: number;   cols: number;   rows: number; }`
- `marker`: The daemon typed, or a control call happened, at this point in the stream. Params: `{   terminal: string;   seq: number;   at: Timestamp;   kind: "daemon_typed" | "control";   actor: Actor | null;   text?: string; }`

## Terminal channel

Frames are text rows only (format `text`); Row.spans is the reserved place for styled rows and the colour decision is the founder's, pending. One poller per terminal, at most ~5 reads a second however many clients; it stops when the last client leaves.

## Legacy verbs

| Legacy verb | Method |
|---|---|
| `land` | `land` |
| `land_pass` | `land.pass` |
| `land_authorize` | `land.authorize` |
| `agent_set` | `agent.set` |
| `message` | none: the daemon types `say` itself |
| `say` | `say` |
| `push` | `push` |
| `push_answer` | `push.answer` |
| `notify_cancelled` | `push.cancelled` |
| `wake` | `session.wake` |
| `close` | `session.stop{mode:close}` |
| `restart` | `session.restart` |
| `park` | `session.park` |
| `fold` | `agent.fold` |
| `move` | `agent.move` |
| `start` | `session.start` |
| `answer_block` | `prompt.answer` |
| `trust` | `settings.trust` |
