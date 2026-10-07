<!-- Generated from contract/protocol.json by contract/generate.py; do not edit. -->
# Handline daemon protocol (protocol 1)

OpenRPC-shaped. Source of contract/protocol.md and contract/protocol.ts: regenerate with `python3 contract/generate.py`. Extensions are x-* keys.

## Framing

- **Transport:** Unix socket (0600, owned by the local user; the role is proven by hello, not by the socket), one JSON-RPC 2.0 object per line; WebSocket (network listener, off by default), one object per text frame.
- **Reach:** v1 reaches this machine, the LAN or Tailscale only (founder decision 3): the network listener binds the address the config names (default loopback; a LAN or Tailscale address is opt-in), always speaks an encrypted WebSocket whose certificate fingerprint is in the pairing QR, and has no relay or public endpoint.
- **Lines:** UTF-8 JSON, terminated by \n, at most 1 MiB. No batches (bad_request). A connection may carry many calls; a one-shot call works.
- **Ids:** Request id is the client's integer or string; replies may arrive in any order. Notifications (no id) flow from the daemon after subscribe or terminal.attach, interleaved with replies.
- **Concurrency:** Calls on one connection are served concurrently: a call that waits (inbox.list or inbox.get with wait_s, up to 600 s) does not hold up the calls after it, and replies come in the order they finish, matched by id. Calls on one connection that touch the same thing are not ordered by the connection: a client that needs the order waits for the first reply. Stream notifications are never held behind a waiting call.
- **Network:** The network listener is the only way in for a phone, a browser or another machine, and only a paired device may use it. A connection on it is unpaired until its hello carries a valid device `token`. An unpaired connection may call device.redeem and nothing else: every other call, hello and an unknown method name included, is refused before any handler runs with the one reply `{code: -32002, message: "refused", data: {code: "refused", gate: "unpaired", reason: "not paired"}}`, the same words every time: no method list, no state, no version, no hint, nothing that says why. A token that is revoked or was never issued is the same refusal, so a reply does not say which. The Unix socket is not the network: its roles are proven as below. Conformance: the network transport is the WebSocket over TLS the listener speaks, and a client pins the certificate fingerprint from the pairing uri; fixtures with `as: unpaired` or `as: member` over the network are run against it with `--as unpaired=...` and `--as member=...` sockets that are already past TLS and the pinned check (a socket the harness is given, or a connector function); contract/README.md says what that does and does not show.
- **Params:** By name only.
- **Legacy:** A line with `verb` and no `jsonrpc` is a legacy request, answered {ok, said} by the adapter until its last caller has moved.
- **Request id:** Every write takes request_id. The daemon keeps each successful reply for 24 hours; a retry with the same id and the same params returns it with replayed:true and repeats nothing. The same id with other params is bad_request. Refusals and errors are not stored: a retry is judged again.

## Handshake and versions

The first call is hello. Compatibility: adding a method or field never changes protocol; removing or changing one does, and the daemon then serves old and new side by side for one release. Clients ignore unknown fields and unknown enum-like strings (state words, kinds, event types). A client that does not know the daemon's protocol refuses to run and prints the one command that fixes it: `hl service install`.

## Roles

Order: anonymous < agent < member < founder. x-role on a method is the lowest role that may call it. Roles are proven, not assumed. founder: the connection said hello with the founder key and it matches (a Unix-socket peer being the same user proves nothing, because agents run as that user). agent: the peer pid verifies against a recorded session. member: a paired device's token. Anything else is anonymous: on the Unix socket it may call only hello and device.redeem; on the network listener it is unpaired and may call only device.redeem (see Framing, network). A call below its role is refused, gate `role`. session.type and session.keys act at a shell prompt as they do anywhere in the pane: only a paired device can connect to the network listener and an unpaired caller gets no data, so a shell is within what a paired device may reach.

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
| `unsupported` | The host or the format cannot do it: a resize on a host that cannot, bytes on a terminal whose host serves only text. input.ask is the other, reserved and not implemented. |
| `not_self` | The agent named is not the caller's own session. |
| `attempt_limit` | Too many wrong pairing codes: five wrong tries kill a code, and five wrong codes in ten minutes from one address refuse device.redeem from it for fifteen minutes. |
| `unpaired` | A network connection with no valid device token called anything but device.redeem. The reply is always message "refused" and reason "not paired": no data, no method list, no version, no hint. A revoked token is refused the same way, and its connections are closed. |
| `pairing_code` | device.redeem: the code is wrong, expired or already used. The reply does not say which. |
| `session_token` | Session token and peer-pid check, unchanged. |
| `proposal_changed` | hr.decide: the proposal changed after the caller read it (its `subject` is no longer the one handed in). Checked after the settled check. Nothing was done; the refusal's `proposal` is the proposal as it is now, to be read and decided again. |
| `proposal_settled` | hr.decide, hr.withdraw: the proposal is already approved, rejected or withdrawn, so there is nothing to decide or take back. The refusal's `proposal` is the proposal as it is now. |

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
| `agent.create` | founder | yes | `name`, `role`, `repo`, `base?`, `model?`, `effort?`, `account?`, `parent?`, `request_id` | `Accepted & {operation}` | `refused`, `not_found`, `bad_request` |  |
| `agent.set` | agent (only agent, founder) | yes | `agent`, `values`, `speaker?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` | not_self |
| `agent.fold` | agent | yes | `agent`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `agent.move` | agent | yes | `agent`, `account`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `session.start` | agent | yes | `agent`, `account?`, `repo?`, `base?`, `request_id` | `Accepted` | `refused`, `not_found` | terminal_lock, state |
| `session.wake` | agent | yes | `agent`, `request_id` | `Accepted` | `refused`, `not_found` | terminal_lock, state |
| `session.stop` | agent | yes | `agent`, `mode`, `fresh?`, `request_id` | `Accepted` | `refused`, `not_found` | role, terminal_lock, state |
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
| `chat.messages` | member |  | `agent`, `scope`, `before?`, `since?`, `limit?` | `{messages, more}` | `not_found`, `bad_request` |  |
| `chat.requests` | member |  | `agent`, `scope`, `before?`, `since?`, `limit?` | `{items, more, version, cursor}` | `not_found`, `bad_request` |  |
| `chat.transcript` | member |  | `agent`, `before?`, `since?`, `limit?` | `{items, more, working}` | `not_found`, `bad_request` |  |
| `chat.locate` | member |  | `request`, `agent?`, `limit?` | `{agent, scope, message, messages, more_older, more_newer}` | `not_found`, `bad_request` |  |
| `chat.search` | member |  | `agent?`, `query`, `scope?`, `limit?` | `{messages}` | `bad_request`, `not_found` |  |
| `chat.send` | member | yes | `agent`, `body`, `task?`, `request_id` | `Accepted & {request}` | `refused`, `not_found`, `bad_request` |  |
| `chat.send_now` | member | yes | `request`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `chat.answer` | member | yes | `request`, `text`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `chat.dismiss` | member | yes | `request`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `chat.withdraw` | agent | yes | `request`, `reason?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `terminal.attach` | member |  | `agent?`, `terminal?`, `history?`, `format?` | `TerminalSnapshot \| TerminalBytesSnapshot` | `not_found`, `bad_request`, `refused` | unsupported |
| `terminal.history` | member |  | `terminal`, `before`, `limit`, `format?` | `HistoryPage \| HistoryBytesPage` | `not_found`, `refused`, `bad_request` | unsupported |
| `terminal.resize` | member | yes | `terminal`, `cols`, `rows`, `request_id` | `Accepted` | `refused`, `not_found` | role, input_holder |
| `terminal.input` | member | yes | `terminal`, `data`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` | role, terminal_lock, input_holder, unsupported |
| `terminal.close` | member | yes | `agent`, `request_id` | `Accepted` | `refused`, `not_found` | role, terminal_lock, input_holder, state |
| `terminal.detach` | member |  | `terminal`, `format?` | `object` | `not_found`, `bad_request` |  |
| `inbox.list` | agent |  | `unread?`, `kind?`, `agent?`, `before?`, `limit?`, `wait_s?` | `{items, more}` |  |  |
| `inbox.get` | agent |  | `id`, `wait_s?` | `InboxItem` | `not_found`, `bad_request` |  |
| `inbox.mark_read` | agent | yes | `ids`, `request_id` | `Accepted` | `not_found`, `refused`, `bad_request` |  |
| `inbox.answer` | member | yes | `id`, `option?`, `text?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `inbox.reply` | agent | yes | `id`, `text`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `tasks.list` | agent |  | `owner?`, `from?`, `state?`, `parent?`, `before?`, `limit?`, `all?`, `everyone?` | `{items, more}` |  |  |
| `task.get` | agent |  | `task` | `TaskDetail` | `not_found` |  |
| `task.new` | agent | yes | `owner`, `title`, `body`, `time_box`, `relation?`, `response?`, `parent?`, `blocked_by?`, `priority?`, `expects?`, `reply?`, `request_id` | `Accepted & {task}` | `refused` |  |
| `task.handoff` | agent | yes | `task`, `to`, `note`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.done` | agent | yes | `task`, `evidence?`, `request?`, `repo?`, `reason?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.reopen` | agent | yes | `task`, `note?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.progress` | agent (only agent) | yes | `task`, `note`, `request_id` | `Accepted` | `refused`, `not_found` | not_self |
| `task.begin` | agent | yes | `title`, `body?`, `message`, `priority?`, `expects?`, `why`, `request_id` | `Accepted & {task}` | `refused` |  |
| `task.start` | agent | yes | `task`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.memo.list` | agent |  | `task` | `{memos}` | `not_found` |  |
| `task.memo.add` | agent | yes | `task`, `text`, `request_id` | `Accepted & {memo}` | `refused`, `not_found`, `bad_request` |  |
| `task.memo.edit` | agent | yes | `task`, `memo`, `text`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.memo.delete` | agent | yes | `task`, `memo`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.queue` | agent | yes | `tasks`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.draft` | agent | yes | `tasks`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.merge` | agent | yes | `into`, `tasks`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.release` | agent | yes | `task`, `to?`, `reason?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.discard` | agent | yes | `task`, `reason`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.board` | agent |  | none | `{mine, reports}` |  |  |
| `task.withdraw` | agent | yes | `task`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.cancel` | member | yes | `task`, `reason?`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.edit` | agent | yes | `task`, `owner?`, `title?`, `body?`, `time_box?`, `waits_on?`, `priority?`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.ask` | member | yes | `task`, `to`, `text`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `say` | agent | yes | `target`, `body`, `task?`, `no_task?`, `parent?`, `request_id` | `Accepted` | `refused`, `bad_request` |  |
| `ask` | agent | yes | `target`, `title`, `body`, `options?`, `request_id` | `Accepted & {request}` | `refused`, `bad_request` |  |
| `push` | agent | yes | `target`, `request?`, `request_id` | `Accepted` | `refused`, `bad_request`, `not_found` |  |
| `push.answer` | agent | yes | `requester`, `request_id` | `Accepted` | `refused`, `bad_request` |  |
| `push.cancelled` | agent | yes | `request`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `land` | agent (only agent) | yes | `sha`, `message`, `tree?`, `request_id` | `Accepted` | `refused` | not_self, session_token |
| `land.pass` | agent (only agent) | yes | `sha`, `request_id` | `Accepted` | `refused` | not_self, session_token |
| `land.authorize` | agent (only agent) | yes | `agent`, `land_verb`, `sha`, `key`, `request_id` | `Accepted` | `refused` | not_self, session_token |
| `hook.record` | agent (only agent) | yes | `agent`, `kind`, `at`, `data`, `request_id` | `Accepted` | `refused` | not_self |
| `hr.propose` | agent | yes | `kind`, `args`, `why?`, `request_id` | `Accepted & {proposal}` | `refused`, `bad_request` |  |
| `hr.withdraw` | agent | yes | `proposal`, `request_id` | `Accepted` | `refused`, `not_found` | proposal_settled |
| `trust.propose` | agent | yes | `root`, `why`, `request_id` | `Accepted & {proposal}` | `refused` |  |
| `trust.list` | agent |  | `proposals?` | `{roots, proposals}` |  |  |
| `trust.remove` | agent | yes | `root`, `request_id` | `Accepted` | `not_found` |  |
| `agent.triggers` | agent |  | `agent?` | `{agents}` |  |  |
| `memory.status` | agent |  | `agent?` | `{agents}` |  |  |
| `team.get` | agent |  | none | `{agents, edges}` |  |  |
| `hr.list` | agent |  | `state?` | `{proposals}` | `bad_request` |  |
| `hr.decide` | founder | yes | `proposal`, `decision`, `note?`, `subject?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` | proposal_changed, proposal_settled |
| `accounts.list` | agent |  | none | `{accounts}` |  |  |
| `account.set_cap` | founder | yes | `account`, `cap`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `account.set` | founder | yes | `account`, `label?`, `cap?`, `recover?`, `cycle?`, `cycle_end?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `account.login` | founder | yes | `account`, `request_id` | `Accepted & {terminal}` | `refused`, `not_found` |  |
| `repos.list` | member |  | none | `{repos}` |  |  |
| `worktrees.list` | member |  | `repo?`, `owner?` | `{worktrees}` |  |  |
| `worktree.diff` | member |  | `path`, `file?` | `{path, file, diff, truncated}` | `not_found` |  |
| `worktree.set_status` | member | yes | `path`, `status`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `schedules.list` | agent |  | none | `{schedules}` |  |  |
| `schedule.add` | founder | yes | `target`, `cron`, `message`, `request_id` | `Accepted & {schedule}` | `refused`, `bad_request`, `not_found` |  |
| `schedule.edit` | founder | yes | `schedule`, `target?`, `cron?`, `message?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `schedule.remove` | founder | yes | `schedule`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `stats.get` | member |  | `window` | `Stats` | `bad_request` |  |
| `stats.outlook` | member |  | `range` | `Outlook` | `bad_request` |  |
| `settings.get` | founder |  | `agent?` | `Settings` | `bad_request`, `not_found` |  |
| `settings.set` | founder | yes | `changes`, `agent?`, `request_id` | `Accepted` | `refused`, `bad_request` |  |
| `settings.trust` | founder | yes | `account`, `worktrees`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `answers.list` | founder |  | none | `{answers}` |  |  |
| `answer.add` | founder | yes | `folder?`, `vendor?`, `title?`, `label?`, `request_id` | `Accepted` | `refused`, `bad_request` |  |
| `answer.remove` | founder | yes | `kind`, `key`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `devices.list` | founder |  | none | `{devices}` |  |  |
| `device.pair` | founder | yes | `name?`, `ttl_s?`, `request_id` | `Accepted & {code, expires_at, fingerprint, uri}` | `refused` |  |
| `device.redeem` | anonymous | yes | `code`, `name`, `request_id` | `Accepted & {token, device}` | `refused` | pairing_code, attempt_limit |
| `device.revoke` | founder | yes | `device`, `request_id` | `Accepted` | `refused`, `not_found` |  |

Per-method summaries and field types are in `protocol.json` and `protocol.ts`.

### `hello`

First call on every connection: versions and who the caller is. A client whose protocol is below min_client gets `refused` (gate `protocol`) with hint `hl service install`. A newer client gets a normal reply and decides itself whether to run. Any other call before hello is bad_request. Role by proof: a matching `key` gives founder; with no key, a peer pid that verifies against a recorded session gives agent; a device token (network listener) gives member; anything else is anonymous and every method but hello is refused until a later hello proves more. On the network listener a hello without a valid `token` is refused, gate `unpaired`, with no data; with one it is member.

### `whoami`

The viewer, the device, the role and the methods allowed.

### `subscribe`

Turn the connection into a stream of `event` notices; replays after `since` first. topics is a non-empty list of known topics, else -32602 bad_request naming `topics`. Notices start only after this reply is sent. devices is subscribed on its own, by the founder: a topics list naming devices and any other topic is -32602 bad_request naming `topics`, because devices is served by the gateway itself and every other topic by the daemon behind it. A devices subscription is live only: `since` and `agent` are ignored, the reply cursor is 0, its events carry cursor 0, and a client that reconnects reads devices.list again.

### `unsubscribe`

Stop some topics; a subscription with no topics left ends. Result {}. Naming devices follows the same rule as subscribe: on its own.

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

exit asks Claude to quit; close ends the session and keeps the terminal; kill ends the process. An agent may call it for mode exit and close, as the legacy verbs allow; mode kill is refused to the agent role (`refused`, gate `role`) before the agent is looked up, because it ends another session mid-turn with no handoff record. Kill is for a person.

### `session.restart`

Restart a session.

### `session.park`

Park an agent.

### `session.input`

Type text at the prompt; Enter optional. Refused at a shell prompt (gate state, reason 'nothing sent - a shell prompt is showing'): the daemon types nothing when the pane shows a shell, since a line there would run as a command. session.type and session.keys are not checked this way. Refused with gate screen_blocker while the screen shows a dialog or one the daemon does not recognise, which includes the moments right after a wake, before the screen has been read: nothing is typed; session.keys and terminal.input are the way through (they pass an open dialog), or call again once the screen reads. Each call is recorded as an event of the agent (kind type, key or input) whose actor is the caller: for a paired device, kind device with its id.

### `session.keys`

Named keys for menus and dialogs.  Each call is recorded as an event of the agent (kind type, key or input) whose actor is the caller: for a paired device, kind device with its id.

### `session.type`

Plain typing into the pane: characters sent as typed, no Enter, no prompt checks. Which call sends text when: session.type is what a person typing in the pane uses, with input.take first; it passes the wall and any open dialog, as the keys would. session.keys sends named keys (menus, dialogs, ctrl-c). session.input sends a whole prompt line for the daemon's own flow and is refused (gates wall, screen_blocker) while a usage wall or a dialog is up, since a typed line would answer the dialog. Each call is recorded as an event of the agent (kind type, key or input) whose actor is the caller: for a paired device, kind device with its id.

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

### `chat.requests`

A chat's threads for the screen that draws them: the requests of an agent's chat oldest first, with the task changes and the session starts among them, and one version that changes when anything in the chat does. items are oldest first, each with a `type`: `request` is a chat.messages ChatMessage plus `dismissal` and `task_state`; `task_event` is a change of a task delivered in the page, since the first request in it (the state it reached, a handoff, an edit), with `request` naming the delivery a handoff belongs to so a client can draw both as one card; `session` is the start of a new conversation of the agent since that request (one that resumed the old conversation is not listed). Requests that no chat draws (withdrawn, never reached their target) are left out. HR cards are hr.list's.

### `chat.transcript`

The agent's replies, tool activity, prompt cards and task events as ordered items, plus its working state. Oldest first, in pages: `before` reads the page just older than an item id, `since` reads the items from the second that id is in. Items are the agent's replies, the prompts it opened and the events of the tasks it owns; there is no tool item yet.

### `chat.locate`

Where a message number sits: its conversation, scope, and the page of messages around it. not_found when the request does not exist or no chat draws it (withdrawn, or never reached its target). The page has up to `limit` messages, about half of them older than the one asked for.

### `chat.search`

Find messages by text; each hit carries the scope that shows it (the jump is chat.locate).

### `chat.send`

A message to an agent through the daemon's delivery (queued, typed, landed); needs no input hold.

### `chat.send_now`

Type a queued message now, ahead of the daemon's delivery order; the request keeps its id and goes on as sent.

### `chat.answer`

Answer a message that asked something.

### `chat.dismiss`

Dismiss a message.

### `chat.withdraw`

Withdraw a message not yet landed.

### `terminal.attach`

Subscribe to one terminal; replies with a snapshot, then pushes notices. `format` chooses text rows (frame notices) or bytes (output notices). A connection has at most one attach per terminal and format: attaching a terminal and format the connection already watches replaces that attach, accepted and never refused (no notice of the old one follows the reply; the reply is a fresh snapshot, and the new attach's counter starts at its seq). An attach in the other format is independent, with its own counter. A client that wants more than one view of one terminal and format shares one attach.

### `terminal.history`

Page scrollback; absolute line indexes.

### `terminal.resize`

Resize; accepted from the input holder only, restored when they leave.

### `terminal.input`

Raw keystrokes or pasted bytes into the terminal, from the input holder only. For a client that is a terminal (xterm.js), as session.type is for a person typing: the same gates, and it passes the wall and an open dialog as session.type and session.keys do (the person sees the screen; session.input keeps both gates). Unlike them it is refused while the daemon holds the input, since a stream of keystrokes beside the daemon's own typing would corrupt both: take the input first. A terminal with no holder, a login terminal, refuses it too. A pane showing a shell prompt takes it, as with session.type. Calls on one connection are applied in the order they were read, however many are waiting. It is recorded as an event of the agent (kind type) whose actor is the caller, a paired device by its id, with the byte count and never the bytes. Terminal hosts that cannot send keys refuse with gate unsupported.

### `terminal.close`

Close the host's terminal to free it (the web UI's Close terminal). Different from session.stop close, which ends the session and keeps the terminal: refused while a session runs.

### `terminal.detach`

Stop receiving this terminal, in one format or in all. Never stops the agent. not_found is for a terminal handle that does not exist. Closing the connection ends every attach.

### `inbox.list`

Inbox items; an agent sees its own.

### `inbox.get`

One item (hl read).

### `inbox.mark_read`

Mark items read; an id that is not yours is not_found. For the founder, every message of an agent up to the newest id named is read; a decision or a reply owed is never read away, so such an id, and what lies past it, stays unread (refused, gate state, when nothing could be read). For an agent only the head of its own queue can be read, as `hl inbox` does; another id of its own is refused, gate state.

### `inbox.answer`

Answer a decision item with one of its options or free text. The founder's answer to an item of the mailbox: a decision's, a question's, or a reply to an answer. No item carries options yet, so `option` is bad_request and `text` is the answer. An agent session is refused, gate role.

### `inbox.reply`

Reply to an item. A message to whoever sent the item, settling it (`hl say --parent`). An agent replies to its own incoming items; the founder to a mailbox item that is not a decision (a decision is answered, not replied to: refused, gate state).

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

Change a task: owner, title and priority by its opener or manager (its owner may record what it waits on), body and time box until it is delivered. Never silent: the holder is told.

### `task.ask`

The founder asks an agent a question about a task: a message linked to the task, whose holder does not change. The agent's reply joins the task.

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

Propose a roster change; the founder decides (hl hr add, retire, rename, link, unlink, team, settings, context, and diff, which is kind bundle: args.changes lists several changes decided once and applied together or not at all).

### `hr.withdraw`

Take back an undecided proposal: its author's own, or any for the founder; another agent is refused (gate role).

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

HR proposals with their diff: what waits for the founder, and what settled recently. Without `state`: every proposal that waits (open, and apply_failed until it is decided again) and every settled one (approved, rejected, withdrawn) from the last 7 days, at most 50 of those. Order: the waiting ones first, oldest `created` first; then the settled ones, newest `settled` first. A client counts the dot from `open` and `apply_failed`. With `state`, only that state, in the same order, and no 7-day or 50 bound. A proposal whose requested_by is human is never listed: the founder made that change directly and was not asked (the bridge leaves it out of its cards too). The Python store's states map as: proposed = open; approved = apply_failed (it holds the founder's approval and the change is not applied: `error` is set when an apply ran and failed, null when it never ran, e.g. a crash between the two); applied = approved; rejected = rejected; withdrawn = withdrawn; enacting and failed exist only for a human's own change and are never listed.

### `hr.decide`

Approve or reject a proposal; approving an apply_failed one applies it again. Refused with gate proposal_changed or proposal_settled, carrying the proposal as it is.

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

### `stats.outlook`

The week left of the accounts together, recorded and guessed, each account's runway, and what the agents cost per day.

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

Paired devices with last seen and whether each is connected.

### `device.pair`

Make a one-time pairing code. Founder only. The code is single use and expires: `ttl_s` seconds (default 300, at most 600). The reply carries the listener certificate fingerprint and the address in `uri`, which is what the QR holds; a client pins that fingerprint and refuses any other certificate. It is not replayed by request_id: a retry with the same request_id runs again and makes another code.

### `device.redeem`

Trade a pairing code for a device token. The only method an unpaired network caller may call. A code that is wrong, expired or already used is refused, gate `pairing_code`, without saying which; too many wrong tries are refused, gate `attempt_limit`. A code redeems once: the token is in this one reply and never again, and the daemon keeps only its hash. `name` is how the device is listed. It is not replayed by request_id either: a retry runs again, and a code that was redeemed stays used, so the retry is refused. The wrong-code count for a redeem over the local socket is kept under the host '' (the empty string), since it has no address.

### `device.revoke`

Cancel a device and close its connections at once. Cancels the device's token. Its open connections and terminal attaches are closed at once, its next call is refused (gate `unpaired`), and a device.revoked event is sent on the devices topic. Revoking a device that is not paired is not_found.

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
| `hl hr diff` | `hr.propose` |
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

- **Founder only today:** `hl cancel`; `hl unstall`; `hl team seal`; `hl hr approve|reject|apply|relabel|rehome|links`; `hl trust apply|mcp`; `hl schedule add|edit|remove`; `hl account add|remove|register|cap|cycle|recover (set; the billing-cycle and cap reads are for the next spec task)`; `hl idle-timeout|idle-background|notice-digest|ignore-cache-expiry|auto-accept (set)`; `hl dev-mode`; `hl device pair|list|revoke (device.pair, devices.list, device.revoke: the gateway's socket, from the founder's terminal)`; `hl service ...`
- **Needs a decision:** `hl spawn (maps to agent.create, which is founder: should a manager spawn a report?)`; `hl agent register (a roster change, founder)`
- **Not daemon methods:** `hl init`; `hl login`; `hl launch`; `hl doctor`; `hl hooks`; `hl install-hooks`; `hl daemon`; `hl backup`; `hl venv`; `hl bridge`; `hl top`; `hl watch`; `hl guide`; `hl memory migrate`; `hl task note (suppressed)`

The plan's earlier `requests` topic is replaced by `chat` (messages and landing state) and `inbox` (decisions); `usage` by `accounts`. The `hr` topic: hr.raised: a proposal was raised (state open); one event per proposal, once. hr.settled: a proposal left open or its apply ran again: it was decided, withdrawn, or its apply failed; sent for every outcome, a retry of an apply_failed one that fails again included (state apply_failed, a new error); the Proposal carries the new state and `settled`. The event's `agent` is the proposal's agent (null for a bundle), `title` its title, `target` {screen: hr, id: the proposal's id as text}, and `data` {proposal: the whole Proposal}, so a client updates its list and its count without a refetch; a client that missed events refetches hr.list. What hr.list serves, and in which order: see its description. An edit of a draft in place (the file is writable by its author) sends no event: a card learns of it from the refusal proposal_changed.

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
| `devices` | `device.paired` (Device), `device.revoked` ({device}) | `devices.list` |
| `hr` | `hr.raised` (Proposal), `hr.settled` (Proposal) | `hr.list` |

## State words

AgentView.state is one of these lower-case words. The set is open: a client treats a word it does not know as idle and keeps working. Upper case is never sent: the daemon's internal states (IDLE, QUEUED, RUNNING, DONE, HANDOFF, DEGRADED, PARKED, THROTTLED, STALLED) and the view words its screens use (PENDING, MOVING, DIALOG, FATAL) are mapped to these before they leave it. The mapping is made from the source facts (the state row, its wake time, the screen), never by reading state_detail text.

| Word | Meaning |
|---|---|
| `running` | A turn is running (a hand-off in progress counts: moving is working). |
| `idle` | At its prompt with nothing to do, or nothing pending for it (a park with no work behind it, a finished one). state_detail may say what it waits on (a child task, background work). |
| `queued` | Has work and will start at the daemon's next tick, with no time to wait for. state_detail may say why it is not running yet (a restart retry). |
| `parked` | Waits for a time: the usage window rolling over, a park a person asked for, a throttle or degraded retry, or an account move settling. state_detail is the clock (`due` once it has passed; `moving` for an account move, which has no clock). |
| `waiting` | A person must answer: a dialog or a prompt is open (has_dialog is true). |
| `stalled` | Stopped until a person unstalls it (the daemon will not wake it). |
| `blocked` | Reserved: waiting on a blocker the view names in `block`. The daemon does not send it today; a client draws it as blocked. |
| `no_terminal` | Registered but with no terminal to show: never started or its terminal closed, and nothing waits for it. Checked before idle; a closed agent with work waiting is queued or parked. |

What the daemon sends for each of its own states:

| Daemon state | Word |
|---|---|
| RUNNING, HANDOFF | `running` |
| IDLE, DONE, PARKED with no work behind it | `idle` |
| QUEUED, a restart retry (PENDING with no clock) | `queued` |
| PARKED, THROTTLED, DEGRADED with a wake time (PENDING with a clock); MOVING | `parked` |
| DIALOG (an open prompt or modal) | `waiting` |
| STALLED (FATAL) | `stalled` |
| no state row or no terminal, and nothing waiting for it (checked before IDLE) | `no_terminal` |

PENDING is two words, by what it waits for: `queued` when nothing but the next tick stands between it and a start, `parked` when it waits for a time. A client draws both as waiting its turn, and the clock in state_detail tells them apart. MOVING is `parked` with state_detail `moving`.

## Notifications

- `event`: Sent after subscribe, for the subscribed topics, in cursor order. Params: `Event`
- `gap`: retention: the resume cannot be served (the cursor is older than the daemon keeps, or more than 5000 of the subscribed events behind); refetch the topic's snapshot; the connection stays open and the cursor is the newest. slow_client: more than 1000 live events were waiting to be written to this client; the daemon sends this and then closes the connection. A resume up to 5000 behind is replayed in full and does not count toward the 1000. The daemon sets no `topic` in v1. Params: `{   reason: "retention" | "slow_client";   cursor: number;   topic?: Topic; }`
- `frame`: Text attaches only. full:true carries every line and is what a slow client gets instead of a backlog. Params: `{   terminal: string;   seq: number;   full: boolean;   lines?: Row[];   changed?: {     y: number;     row: Row;   }[];   cursor: Cursor; }`
- `resize`: Text attaches only; a bytes attach gets a snapshot notice instead. Params: `{   terminal: string;   seq: number;   cols: number;   rows: number; }`
- `marker`: The daemon typed, or a control call happened, at this point in the stream. Params: `{   terminal: string;   seq: number;   at: Timestamp;   kind: "daemon_typed" | "control";   actor: Actor | null;   text?: string;   /** The attach this notice belongs to, whose counter `seq` is: a terminal watched in both formats is sent each of these twice. Additive: a client that does not know it ignores it. */   format: "text" | "bytes"; }`
- `holder`: Sent to attached clients whenever the holders change (take, release, the idle hold timeout, the daemon taking over for a send). agent.changed carries the same in AgentView.input_holders. Params: `{   terminal: string;   seq: number;   holders: InputHolder[];   /** The attach this notice belongs to, whose counter `seq` is: a terminal watched in both formats is sent each of these twice. Additive: a client that does not know it ignores it. */   format: "text" | "bytes"; }`
- `output`: Bytes attaches only: the pane's output in order, at most one notice per 50 ms and 64 KiB decoded each. A notice may end inside a multi-byte character or an escape sequence. Params: `{   terminal: string;   seq: number;   /** Base64 of what the program wrote. */   data: string; }`
- `snapshot`: Bytes attaches only: the screen again, to be repainted as after an attach (reset, resize to cols by rows, write data), because the terminal changed size or the client fell behind. Everything the stream would have said before it is dropped; output follows with the next seq. Params: `{   terminal: string;   seq: number;   reason: "resize" | "slow_client";   cols: number;   rows: number;   /** Base64, as in the attach snapshot. */   data: string;   alt_screen: boolean;   cursor: {     x: number;     y: number;   };   cursor_visible: boolean; }`

## Terminal channel

Format `text` sends text rows (frame notices); Row.spans is the reserved place for styled rows and the colour decision is the founder's, pending. Format `bytes` (below) sends the pane's own escape-coded output, which carries the colour. One poller per terminal, at most ~5 reads a second however many clients; it stops when the last client leaves. The input holders are a list (TerminalSnapshot.holders, AgentView.input_holders) with a `holder` notice on change; a holder that has not typed for the input_hold_idle_s setting loses the hold.

### Format bytes

- **Format.** `terminal.attach` and `terminal.history` take `format`: `text` (the default; what they send is unchanged) or `bytes`. The format belongs to the attach, so two clients may watch one terminal in different formats. `hello.capabilities.terminal_format` lists the formats the daemon serves at all and `terminal_hosts` maps each host it has to its formats, e.g. `{tmux: [text, bytes], orca: [text]}`. A bytes request for a terminal whose host cannot serve it is refused, gate `unsupported`, and nothing is attached; a client then asks for text. A `format` that is neither word is bad_request naming `format`. A connection has at most one attach per terminal and format (see terminal.attach); detach takes an optional `format`.
- **Encoding.** Bytes travel as base64 (standard alphabet, padded) in JSON strings on the same transport as every other message: no binary frames. They need not be valid UTF-8, and a notice may end inside a character or an escape sequence, so a client writes each as bytes to a terminal that keeps its own parser state.
- **Snapshot.** `data` is written unchanged to a freshly reset terminal of `cols` by `rows` (the client resizes, resets, writes, in one step). It begins with `ESC [ ? 1049 h` when `alt_screen` is true, then paints the visible rows with their SGR codes, each row ending in a reset and rows separated by CR LF, never a bare LF; then sets the modes the pane has on that change what a client sends (application cursor keys, bracketed paste, mouse reporting and its SGR encoding) as far as the host reports them; it ends by placing the cursor at `cursor` and showing or hiding it as `cursor_visible` says. It is at most 1 MiB decoded: a screen whose coded form is larger is sent with its styling left out, never refused. Later transitions of the alternate screen are in the stream; the flag is for the snapshot alone.
- **Sequence.** One counter per attach, shared by all its notices (`output`, `snapshot`, `marker`, `holder`), and it goes up by exactly 1 with each. The snapshot's `seq` is the last notice its screen includes: the daemon takes the screen and starts the stream together, so the first notice after the reply has `seq` + 1 and no byte is lost or repeated between them. A client ignores a notice whose seq is not above the last it applied and re-attaches on a hole. The counters of different attaches, text ones included, are unrelated: a connection that watches one terminal in both formats has two counters, and attaching the same terminal and format again replaces that attach and starts a new one at its snapshot's seq. There is no resume: after a reconnect the client attaches again and repaints from the new snapshot, and what it missed is not replayed.
- **Slow client.** Output is never dropped from the middle. When one attach's unsent output reaches 1 MiB decoded, the daemon discards all of it and sends one `snapshot` notice, reason `slow_client`, with a seq above everything discarded; output follows it. Counting bytes, not seconds, is the whole rule. A connection that falls behind on events past the gap rule is closed with gap `slow_client` as before and attaches again.
- **History.** Lines come as strings with their SGR codes, as the host keeps them: one physical row each (the row as the pane wrapped it, never joined), starting at default attributes and ending in a reset, with no line break inside. `history` on attach and `limit` on `terminal.history` count lines as in text (default 100, at most 2000); a page is trimmed to 1 MiB and says `more`. Only the normal screen has scrollback: while `alt_screen` is true the snapshot's history is empty.
- **Input.** `terminal.input` carries keystrokes and pasted bytes as base64; its gates and recording are on the method.
- **Resize.** `terminal.resize` is unchanged: the input holder alone, restored when they leave. A bytes attach is told of any size change, the holder's or the restore, by a `snapshot` notice, reason `resize`, with the new cols and rows, and gets no `resize` notice: the client repaints rather than reflow, as tmux's reflow is not xterm's.
- **Markers.** `marker` and `holder` notices come to a bytes attach as to a text one, in the one counter: a `marker` stands where the daemon typed, before the output that echoes it. Each carries `format`, the attach it was sent to, since a terminal watched in both formats gets each notice once per attach with that attach's counter.

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
