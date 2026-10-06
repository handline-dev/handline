<!-- Generated from contract/protocol.json by contract/generate.py; do not edit. -->
# Handline daemon protocol (protocol 1)

OpenRPC-shaped. Source of contract/protocol.md and contract/protocol.ts: regenerate with `python3 contract/generate.py`. Extensions are x-* keys.

## Framing

- **Transport:** Unix socket (0600, owned by the local user; the role is proven by hello, not by the socket), one JSON-RPC 2.0 object per line; WebSocket (network listener, off by default), one object per text frame.
- **Reach:** v1 reaches this machine, the LAN or Tailscale only (founder decision 3): the network listener binds the address the config names (default loopback; a LAN or Tailscale address is opt-in), always speaks an encrypted WebSocket whose certificate fingerprint is in the pairing QR, and has no relay or public endpoint.
- **Lines:** UTF-8 JSON, terminated by \n, at most 1 MiB. No batches (bad_request). A connection may carry many calls; a one-shot call works.
- **Ids:** Request id is the client's integer or string; replies may arrive in any order. Notifications (no id) flow from the daemon after subscribe or terminal.attach, interleaved with replies.
- **Params:** By name only.
- **Legacy:** A line with `verb` and no `jsonrpc` is a legacy request, answered {ok, said} by the adapter until its last caller has moved.
- **Request id:** Every write takes request_id. The daemon keeps each successful reply for 24 hours; a retry with the same id and the same params returns it with replayed:true and repeats nothing. The same id with other params is bad_request. Refusals and errors are not stored: a retry is judged again.

## Handshake and versions

The first call is hello. Compatibility: adding a method or field never changes protocol; removing or changing one does, and the daemon then serves old and new side by side for one release. Clients ignore unknown fields and unknown enum-like strings (state words, kinds, event types). A client that does not know the daemon's protocol refuses to run and prints the one command that fixes it: `hl service install`.

## Roles

Order: anonymous < agent < member < founder. x-role on a method is the lowest role that may call it. Roles are proven, not assumed. founder: the connection said hello with the founder key and it matches (a Unix-socket peer being the same user proves nothing, because agents run as that user). agent: the peer pid verifies against a recorded session. member: a paired device's token. Anything else is anonymous and may call only hello (and device.redeem on the network listener). A call below its role is refused, gate `role`.

## Errors

Every error is a JSON-RPC error whose `data.code` is the stable string. A refusal carries the reason; fail-closed is `refused`, never a silent default.

| Code | RPC code | data |
|---|---|---|
| `not_found` | -32001 | Names what was not found. |
| `refused` | -32002 | RefusalData |
| `stale_prompt` | -32003 | {code, current: Prompt\|null}; nothing was sent. |
| `daemon_busy` | -32004 | {code, retry_after_ms} |
| `bad_request` | -32600 | {code, field?, method?}; also JSON-RPC parse errors (-32700), invalid params (-32602) and an unknown method (-32601, data.method). |
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
| `not_self` | The agent named is not the caller's own session. |
| `attempt_limit` | Too many wrong pairing codes: five wrong tries kill a code, and five wrong codes in ten minutes from one address refuse device.redeem from it for fifteen minutes. |
| `session_token` | Session token and peer-pid check, unchanged. |

## Methods

A method marked write takes a required `request_id`.

| Method | Role | Write | Params | Result | Errors | Gates |
|---|---|---|---|---|---|---|
| `hello` | anonymous |  | `client`, `protocol`, `token?`, `key?` | `{protocol, min_client, daemon, schema, role, caller, capabilities, transport, methods}` | `refused`, `bad_request` |  |
| `whoami` | agent |  | none | `{caller, role, methods}` |  |  |
| `subscribe` | agent |  | `topics`, `since?`, `agent?` | `{cursor}` | `bad_request` |  |
| `unsubscribe` | agent |  | `topics` | `object` | `bad_request` |  |
| `daemon.status` | agent |  | none | `DaemonStatus` |  |  |
| `events.feed` | agent |  | `since?`, `topics?`, `limit?` | `{events, cursor, gap}` | `bad_request` |  |
| `operation.get` | member |  | `operation` | `Operation` | `not_found` |  |
| `agents.list` | agent |  | none | `{agents}` |  |  |
| `agent.get` | agent |  | `agent` | `AgentDetail` | `not_found` |  |
| `agent.timeline` | member |  | `agent`, `before?`, `limit?` | `{items, more}` | `not_found` |  |
| `agent.options` | member |  | none | `AgentOptions` |  |  |
| `agent.create` | founder | yes | `name`, `role`, `repo`, `base?`, `model?`, `effort?`, `account?`, `parent?`, `request_id` | `Accepted & {operation}` | `refused`, `not_found` |  |
| `agent.set` | agent (only agent, founder) | yes | `agent`, `values`, `speaker?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` | not_self |
| `agent.fold` | agent | yes | `agent`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `agent.move` | agent | yes | `agent`, `account`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `session.start` | agent | yes | `agent`, `account?`, `repo?`, `base?`, `request_id` | `Accepted` | `refused`, `not_found` | terminal_lock, state |
| `session.wake` | agent | yes | `agent`, `request_id` | `Accepted` | `refused`, `not_found` | terminal_lock, state |
| `session.stop` | agent | yes | `agent`, `mode`, `fresh?`, `request_id` | `Accepted` | `refused`, `not_found` | terminal_lock, state |
| `session.restart` | agent | yes | `agent`, `fresh?`, `force?`, `request_id` | `Accepted` | `refused`, `not_found` | terminal_lock, state |
| `session.park` | agent | yes | `agent`, `request_id` | `Accepted` | `refused`, `not_found` | terminal_lock, state |
| `session.input` | member | yes | `agent`, `text`, `enter?`, `request_id` | `Accepted` | `refused`, `not_found` | role, terminal_lock, input_holder, wall, screen_blocker |
| `session.keys` | member | yes | `agent`, `keys`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` | role, terminal_lock, input_holder |
| `session.type` | member | yes | `agent`, `text`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` | role, terminal_lock, input_holder |
| `session.interrupt` | member | yes | `agent`, `request_id` | `Accepted` | `refused`, `not_found` | role, terminal_lock, input_holder |
| `prompt.list` | member |  | `agent` | `{prompts}` | `not_found` |  |
| `prompt.answer` | member | yes | `agent`, `prompt`, `choice`, `request_id` | `Accepted` | `refused`, `not_found`, `stale_prompt` | role, terminal_lock, input_holder |
| `input.take` | member | yes | `agent`, `request_id` | `Accepted & {holder}` | `refused`, `not_found` | role, input_holder |
| `input.ask` | member | yes | `agent`, `request_id` | `Accepted` | `refused` | unsupported |
| `input.release` | member | yes | `agent`, `request_id` | `Accepted` | `refused`, `not_found` | role |
| `chat.messages` | member |  | `agent`, `scope`, `before?`, `since?`, `limit?` | `{messages, more}` | `not_found` |  |
| `chat.transcript` | member |  | `agent`, `before?`, `since?`, `limit?` | `{items, more, working}` | `not_found` |  |
| `chat.locate` | member |  | `request`, `agent?`, `limit?` | `{agent, scope, message, messages, more_older, more_newer}` | `not_found` |  |
| `chat.search` | member |  | `agent?`, `query`, `scope?`, `limit?` | `{messages}` |  |  |
| `chat.send` | member | yes | `agent`, `body`, `task?`, `request_id` | `Accepted & {request}` | `refused`, `not_found` |  |
| `chat.answer` | member | yes | `request`, `text`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `chat.dismiss` | member | yes | `request`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `chat.withdraw` | agent | yes | `request`, `reason?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `terminal.attach` | member |  | `agent?`, `terminal?`, `history?` | `TerminalSnapshot` | `not_found`, `bad_request` |  |
| `terminal.history` | member |  | `terminal`, `before`, `limit` | `HistoryPage` | `not_found` |  |
| `terminal.resize` | member | yes | `terminal`, `cols`, `rows`, `request_id` | `Accepted` | `refused`, `not_found` | role, input_holder |
| `terminal.close` | member | yes | `agent`, `request_id` | `Accepted` | `refused`, `not_found` | role, terminal_lock, input_holder, state |
| `terminal.detach` | member |  | `terminal` | `object` | `not_found` |  |
| `inbox.list` | agent |  | `unread?`, `kind?`, `agent?`, `before?`, `limit?`, `wait_s?` | `{items, more}` |  |  |
| `inbox.get` | agent |  | `id`, `wait_s?` | `InboxItem` | `not_found` |  |
| `inbox.mark_read` | agent | yes | `ids`, `request_id` | `Accepted` | `not_found` |  |
| `inbox.answer` | member | yes | `id`, `option?`, `text?`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `inbox.reply` | agent | yes | `id`, `text`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `tasks.list` | agent |  | `owner?`, `from?`, `state?`, `parent?`, `before?`, `limit?`, `all?`, `everyone?` | `{items, more}` |  |  |
| `task.get` | agent |  | `task` | `TaskDetail` | `not_found` |  |
| `task.new` | agent | yes | `owner`, `title`, `body`, `time_box`, `relation?`, `response?`, `parent?`, `blocked_by?`, `priority?`, `expects?`, `reply?`, `request_id` | `Accepted & {task}` | `refused` |  |
| `task.handoff` | agent | yes | `task`, `to`, `note`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.done` | agent | yes | `task`, `evidence?`, `request?`, `repo?`, `reason?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.reopen` | agent | yes | `task`, `note?`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.progress` | agent (only agent) | yes | `task`, `note`, `request_id` | `Accepted` | `refused`, `not_found` | not_self |
| `task.begin` | agent | yes | `title`, `body?`, `message`, `priority?`, `expects?`, `why`, `request_id` | `Accepted & {task}` | `refused` |  |
| `task.start` | agent | yes | `task`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.memo.list` | agent |  | `task` | `{memos}` | `not_found` |  |
| `task.memo.add` | agent | yes | `task`, `text`, `request_id` | `Accepted & {memo}` | `refused`, `not_found` |  |
| `task.memo.edit` | agent | yes | `task`, `memo`, `text`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.memo.delete` | agent | yes | `task`, `memo`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.queue` | agent | yes | `tasks`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.draft` | agent | yes | `tasks`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.merge` | agent | yes | `into`, `tasks`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.release` | agent | yes | `task`, `to?`, `reason?`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.discard` | agent | yes | `task`, `reason`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.board` | agent |  | none | `{mine, reports}` |  |  |
| `task.withdraw` | agent | yes | `task`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.cancel` | member | yes | `task`, `reason?`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.edit` | agent | yes | `task`, `owner?`, `title?`, `body?`, `time_box?`, `waits_on?`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `say` | agent | yes | `target`, `body`, `task?`, `no_task?`, `parent?`, `request_id` | `Accepted` | `refused` |  |
| `ask` | agent | yes | `target`, `title`, `body`, `options?`, `request_id` | `Accepted & {request}` | `refused` |  |
| `push` | agent | yes | `target`, `request?`, `request_id` | `Accepted` | `refused` |  |
| `push.answer` | agent | yes | `requester`, `request_id` | `Accepted` | `refused` |  |
| `push.cancelled` | agent | yes | `request`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `land` | agent (only agent) | yes | `sha`, `message`, `tree?`, `request_id` | `Accepted` | `refused` | not_self, session_token |
| `land.pass` | agent (only agent) | yes | `sha`, `request_id` | `Accepted` | `refused` | not_self, session_token |
| `land.authorize` | agent (only agent) | yes | `agent`, `land_verb`, `sha`, `key`, `request_id` | `Accepted` | `refused` | not_self, session_token |
| `hook.record` | agent (only agent) | yes | `agent`, `kind`, `at`, `data`, `request_id` | `Accepted` | `refused` | not_self |
| `hr.propose` | agent | yes | `kind`, `args`, `why?`, `request_id` | `Accepted & {proposal}` | `refused`, `bad_request` |  |
| `hr.withdraw` | agent | yes | `proposal`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `trust.propose` | agent | yes | `root`, `why`, `request_id` | `Accepted & {proposal}` | `refused` |  |
| `trust.list` | agent |  | `proposals?` | `{roots, proposals}` |  |  |
| `trust.remove` | agent | yes | `root`, `request_id` | `Accepted` | `not_found` |  |
| `agent.triggers` | agent |  | `agent?` | `{agents}` |  |  |
| `memory.status` | agent |  | `agent?` | `{agents}` |  |  |
| `team.get` | agent |  | none | `{agents, edges}` |  |  |
| `hr.list` | agent |  | `state?` | `{proposals}` |  |  |
| `hr.decide` | founder | yes | `proposal`, `decision`, `note?`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `accounts.list` | agent |  | none | `{accounts}` |  |  |
| `account.set_cap` | founder | yes | `account`, `cap`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `account.set` | founder | yes | `account`, `label?`, `cap?`, `recover?`, `cycle?`, `cycle_end?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `account.login` | founder | yes | `account`, `request_id` | `Accepted & {terminal}` | `refused`, `not_found` |  |
| `repos.list` | member |  | none | `{repos}` |  |  |
| `worktrees.list` | member |  | `repo?`, `owner?` | `{worktrees}` |  |  |
| `worktree.diff` | member |  | `path`, `file?` | `{path, file, diff, truncated}` | `not_found` |  |
| `worktree.set_status` | member | yes | `path`, `status`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `schedules.list` | agent |  | none | `{schedules}` |  |  |
| `schedule.add` | founder | yes | `target`, `cron`, `message`, `request_id` | `Accepted & {schedule}` | `refused`, `bad_request` |  |
| `schedule.edit` | founder | yes | `schedule`, `target?`, `cron?`, `message?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `schedule.remove` | founder | yes | `schedule`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `stats.get` | member |  | `window` | `Stats` |  |  |
| `settings.get` | founder |  | `agent?` | `Settings` |  |  |
| `settings.set` | founder | yes | `changes`, `agent?`, `request_id` | `Accepted` | `refused`, `bad_request` |  |
| `settings.trust` | founder | yes | `account`, `worktrees`, `request_id` | `Accepted` | `refused` |  |
| `answers.list` | founder |  | none | `{answers}` |  |  |
| `answer.add` | founder | yes | `folder?`, `vendor?`, `title?`, `label?`, `request_id` | `Accepted` | `refused`, `bad_request` |  |
| `answer.remove` | founder | yes | `kind`, `key`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `devices.list` | founder |  | none | `{devices}` |  |  |
| `device.pair` | founder | yes | `name?`, `request_id` | `Accepted & {code, expires_at, fingerprint, uri}` | `refused` |  |
| `device.redeem` | anonymous | yes | `code`, `name`, `request_id` | `Accepted & {token, device}` | `refused` | attempt_limit |
| `device.revoke` | founder | yes | `device`, `request_id` | `Accepted` | `refused`, `not_found` |  |

Per-method summaries and field types are in `protocol.json` and `protocol.ts`.

### `hello`

First call on every connection: versions and who the caller is. A client whose protocol is below min_client gets `refused` (gate `protocol`) with hint `hl service install`. A newer client gets a normal reply and decides itself whether to run. Any other call before hello is bad_request. Role by proof: a matching `key` gives founder; with no key, a peer pid that verifies against a recorded session gives agent; a device token (network listener) gives member; anything else is anonymous and every method but hello is refused until a later hello proves more.

### `whoami`

The viewer, the device, the role and the methods allowed.

### `subscribe`

Turn the connection into a stream of `event` notices; replays after `since` first. topics is a non-empty list of known topics, else -32602 bad_request naming `topics`. Notices start only after this reply is sent.

### `unsubscribe`

Stop some topics; a subscription with no topics left ends. Result {}.

### `daemon.status`

Status bar: alive, versions, terminal host, agent counts, inbox count.

### `events.feed`

Resumable feed of events with their deep-link targets; the polling twin of subscribe. topics defaults to [notifications]; limit defaults to 50, at most 200. Without `since`: the newest events, oldest first. With `since`: the events after it; if `since` is older than retention the feed starts at the oldest kept event and `gap` is true. cursor is the newest cursor in the reply, or the current newest when there are no events.

### `operation.get`

A long-running operation's current state.

### `agents.list`

The rail: the stored view of every agent.

### `agent.get`

One agent: the view plus session and terminal facts.

### `agent.timeline`

The agent's recorded events, newest first. Today this is the agent's generic recorded events (the daemon's events table, rows of that agent): id is the event's id and `before` an event id; kind is the recorded kind; actor is the payload's `actor` when it is an object, else null; text is the payload's `text`, else `detail`, else `method`, else the kind; data is the whole payload ({} when it has none). State changes, deliveries and key presses appear only once they are recorded as events, which a later task does; clients must not expect them, and must treat kind as an open set.

### `agent.options`

What the new-agent form offers.

### `agent.create`

Register, make the worktree, start. Long-running: progress arrives on `operations`.

### `agent.set`

Change an agent's settings: an agent its own ordinary keys, the founder any (legacy agent_set).

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

### `session.type`

Plain typing into the pane: characters sent as typed, no Enter, no prompt checks. Which call sends text when: session.type is what a person typing in the pane uses, with input.take first; it passes the wall and any open dialog, as the keys would. session.keys sends named keys (menus, dialogs, ctrl-c). session.input sends a whole prompt line for the daemon's own flow and is refused (gates wall, screen_blocker) while a usage wall or a dialog is up, since a typed line would answer the dialog.

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

Where a message number sits: its conversation, scope, and the page of messages around it.

### `chat.search`

Find messages by text; each hit carries the scope that shows it (the jump is chat.locate).

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

### `terminal.close`

Close the host's terminal to free it (the web UI's Close terminal). Different from session.stop close, which ends the session and keeps the terminal: refused while a session runs.

### `terminal.detach`

Stop receiving this terminal. Never stops the agent.

### `inbox.list`

Inbox items; an agent sees its own.

### `inbox.get`

One item (hl read).

### `inbox.mark_read`

Mark items read; an id that is not yours is not_found.

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

Finish a task with evidence. Exactly one of evidence, request or reason-with-evidence; none or two is bad_request.

### `task.reopen`

Reopen a task.

### `task.progress`

Record progress on a task (hl progress).

### `task.begin`

Start a task the founder told the caller to do, without approval: opened for the caller, linked to the founder's message.

### `task.start`

Pick up a task as the caller's current one.

### `task.memo.list`

Memos beside a task.

### `task.memo.add`

Add a memo; anyone may. A memo on a doing task is told to its holder.

### `task.memo.edit`

Edit a memo: its author or the founder.

### `task.memo.delete`

Delete a memo: its author or the founder.

### `task.queue`

Approve draft tasks to run: the founder or the owner's manager.

### `task.draft`

Send queued tasks back to draft: the founder or the owner's manager.

### `task.merge`

Fold tasks into one: the kept task takes their titles, requests and sub-tasks; opener or founder only.

### `task.release`

Take a doing task back to queued or draft: the opener or the founder.

### `task.discard`

Throw away a task that is not done: the asker or the founder, with a reason.

### `task.board`

The caller's open tasks and its reports', and who holds each now.

### `task.withdraw`

Withdraw a task the caller made.

### `task.cancel`

Cancel a task.

### `task.edit`

Change a task: owner and title by its opener or manager (its owner may record what it waits on), body and time box until it is delivered. Never silent: the holder is told.

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

### `hr.propose`

Propose a roster change; the founder decides (hl hr add, retire, rename, link, unlink, team, settings, context).

### `hr.withdraw`

Take back the caller's own undecided proposal.

### `trust.propose`

Propose declaring a folder ocp may answer Claude's trust prompt in (the founder's own call declares it).

### `trust.list`

Declared trust roots and proposals.

### `trust.remove`

Drop a declared root; no gate, as today.

### `agent.triggers`

What the daemon would decide for an agent right now (hl triggers).

### `memory.status`

Per-agent memory against its budget (hl memory status).

### `team.get`

Agents, roles, allowed edges, role and base file metadata.

### `hr.list`

HR proposals with their diff.

### `hr.decide`

Approve or reject a proposal.

### `accounts.list`

Accounts with login state, use, cap, agents, recent moves.

### `account.set_cap`

Set an account's cap, the most agents mid-turn at once (hl account cap).

### `account.set`

The account Settings form in one call: alias, cap, usage recovery and the month cycle; nothing is written if any is refused. Fields not given are left alone. Chosen over set_cycle_on/set_cycle_end methods because the UI saves these together and the CLI twins (cap, cycle, recover, alias) each write one of these fields.

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

Change settings: any keys of SettingsValues, globally or as one agent's override.

### `settings.trust`

Trust worktree folders for an account (legacy trust).

### `answers.list`

The founder's saved answers: trusted folders and dialog rules (Home settings).

### `answer.add`

Save a standing answer: a folder to trust, or a dialog rule (vendor, title and label). Blank fields add nothing; a half-filled rule is refused. Founder because the dialog-rule store and trust are the founder's alone: an agent that could write them could answer its own dialogs.

### `answer.remove`

Remove a standing answer by the kind and key answers.list gave.

### `devices.list`

Paired devices with last seen.

### `device.pair`

Make a one-time pairing code.

### `device.redeem`

Trade a pairing code for a device token. Allowed before hello auth on the network listener; wrong, expired or used codes and too many tries are refused (gate attempt_limit).

### `device.revoke`

Cancel a device and close its connections at once.

## Who the caller is

x-callers on a method, when present, lists the only roles that may call it, whatever the chain says. The agent-identity methods (hook.record, agent.set, task.progress, land, land.pass, land.authorize) act as the caller's OWN session, found by peer pid or session token as say already finds its sender: an `agent` argument that is not the caller's own name is refused (gate not_self), and a member device cannot call them (agent.set is also the founder's, who may name any agent).

- **Sender:** say, ask and task.new carry no sender parameter: the daemon sets it. An agent's calls carry its own name; a member device's and the founder's carry `human`.
- **Ownership:** Methods that act on a request or task by id (withdraw, edit, release, discard, merge, memo.edit, memo.delete, queue, draft) keep today's checks on who may: the opener, the owner, the owner's manager or the founder; a refusal is `refused` with the reason.

## Agent commands

Every `hl` command an agent runs today and the method that serves it.

| Command | Method |
|---|---|
| `hl task new` | `task.new` |
| `hl task begin` | `task.begin` |
| `hl task start` | `task.start` |
| `hl task handoff` | `task.handoff` |
| `hl task done` | `task.done` |
| `hl task reopen` | `task.reopen` |
| `hl task edit` | `task.edit` |
| `hl task merge` | `task.merge` |
| `hl task release` | `task.release` |
| `hl task discard` | `task.discard` |
| `hl task queue` | `task.queue` |
| `hl task draft` | `task.draft` |
| `hl task board` | `task.board` |
| `hl task show` | `task.get` |
| `hl task list` | `tasks.list` |
| `hl task memo list` | `task.memo.list` |
| `hl task memo add` | `task.memo.add` |
| `hl task memo edit` | `task.memo.edit` |
| `hl task memo delete` | `task.memo.delete` |
| `hl progress` | `task.progress` |
| `hl inbox` | `inbox.list` |
| `hl read` | `inbox.get` |
| `hl say` | `say` |
| `hl ask` | `ask` |
| `hl withdraw` | `chat.withdraw` |
| `hl land` | `land` |
| `hl land --pass` | `land.pass` |
| `hl hook` | `hook.record` |
| `hl agent set` | `agent.set` |
| `hl agent move` | `agent.move` |
| `hl fold` | `agent.fold` |
| `hl close` | `session.stop` |
| `hl restart` | `session.restart` |
| `hl resume` | `session.wake` |
| `hl wake` | `session.wake` |
| `hl hr add|retire|rename|link|unlink|team|context` | `hr.propose` |
| `hl hr withdraw` | `hr.withdraw` |
| `hl hr list` | `hr.list` |
| `hl trust add` | `trust.propose` |
| `hl trust list|proposals` | `trust.list` |
| `hl trust remove` | `trust.remove` |
| `hl team` | `team.get` |
| `hl ps` | `agents.list` |
| `hl status` | `accounts.list` |
| `hl events` | `events.feed` |
| `hl triggers` | `agent.triggers` |
| `hl schedule (show)` | `schedules.list` |
| `hl account list` | `accounts.list` |
| `hl memory status` | `memory.status` |

## Dropped from agents on purpose

Agent commands the spec does not keep at agent role, for the founder to decide. Each is founder-only or a person's act today, or is not a daemon call.

- **Founder only today:** `hl cancel`; `hl unstall`; `hl team seal`; `hl hr approve|reject|apply|relabel|rehome|links`; `hl trust apply|mcp`; `hl schedule add|edit|remove`; `hl account add|remove|register|cap|cycle|recover (set; the billing-cycle and cap reads are for the next spec task)`; `hl idle-timeout|idle-background|notice-digest|ignore-cache-expiry|auto-accept (set)`; `hl dev-mode`; `hl service ...`
- **Needs a decision:** `hl spawn (maps to agent.create, which is founder: should a manager spawn a report?)`; `hl agent register (a roster change, founder)`
- **Not daemon methods:** `hl init`; `hl login`; `hl launch`; `hl doctor`; `hl hooks`; `hl install-hooks`; `hl daemon`; `hl backup`; `hl venv`; `hl bridge`; `hl top`; `hl watch`; `hl guide`; `hl memory migrate`; `hl task note (suppressed)`

The plan's earlier `requests` topic is replaced by `chat` (messages and landing state) and `inbox` (decisions); `usage` by `accounts`.

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
- `gap`: retention: the resume cannot be served (the cursor is older than the daemon keeps, or more than 5000 of the subscribed events behind); refetch the topic's snapshot; the connection stays open and the cursor is the newest. slow_client: more than 1000 live events were waiting to be written to this client; the daemon sends this and then closes the connection. A resume up to 5000 behind is replayed in full and does not count toward the 1000. The daemon sets no `topic` in v1. Params: `{   reason: "retention" | "slow_client";   cursor: number;   topic?: Topic; }`
- `frame`: full:true carries every line and is what a slow client gets instead of a backlog. Params: `{   terminal: string;   seq: number;   full: boolean;   lines?: Row[];   changed?: {     y: number;     row: Row;   }[];   cursor: Cursor; }`
- `resize`:  Params: `{   terminal: string;   seq: number;   cols: number;   rows: number; }`
- `marker`: The daemon typed, or a control call happened, at this point in the stream. Params: `{   terminal: string;   seq: number;   at: Timestamp;   kind: "daemon_typed" | "control";   actor: Actor | null;   text?: string; }`
- `holder`: Sent to attached clients whenever the holders change (take, release, the idle hold timeout, the daemon taking over for a send). agent.changed carries the same in AgentView.input_holders. Params: `{   terminal: string;   seq: number;   holders: InputHolder[]; }`

## Terminal channel

Frames are text rows only (format `text`); Row.spans is the reserved place for styled rows and the colour decision is the founder's, pending. One poller per terminal, at most ~5 reads a second however many clients; it stops when the last client leaves. The input holders are a list (TerminalSnapshot.holders, AgentView.input_holders) with a `holder` notice on change; a holder that has not typed for the input_hold_idle_s setting loses the hold.

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
