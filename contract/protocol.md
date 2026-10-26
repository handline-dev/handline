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
- **Request id:** Every write takes request_id. The daemon keeps each successful reply for 24 hours; a retry with the same id and the same params returns it with replayed:true and repeats nothing. The same id with other params is bad_request. Refusals and errors are not stored: a retry is judged again. One exception: chat.send to a structured agent returns the receipt as it is now (see x-delivery).

## Handshake and versions

The first call is hello. Compatibility: adding a method or field never changes protocol; removing or changing one does, and the daemon then serves old and new side by side for one release. Clients ignore unknown fields and unknown enum-like strings (state words, kinds, event types). A client that does not know the daemon's protocol refuses to run and prints the one command that fixes it: `hl service install`.

## Roles

Order: anonymous < agent < member < founder. x-role on a method is the lowest role that may call it. Roles are proven, not assumed. founder: the connection said hello with the founder key and it matches (a Unix-socket peer being the same user proves nothing, because agents run as that user). agent: the peer pid verifies against a recorded session. member: no caller holds it today, since a paired device (its token proves it) is the founder; the role stays as the lowest a method can ask of a person. Anything else is anonymous: on the Unix socket it may call only hello and device.redeem; on the network listener it is unpaired and may call only device.redeem (see Framing, network). A call below its role is refused, gate `role`. session.type and session.keys act at a shell prompt as they do anywhere in the pane: only a paired device can connect to the network listener and an unpaired caller gets no data, so a shell is within what a paired device may reach.

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
| `input_holder` | Someone else holds the input. A daemon holder is a short hold, not a refusal. terminal.resize: under 80 by 24, whoever asks; with no holder, from a caller that has not attached the terminal. |
| `wall` | The usage limit (wall_reason): typing would only spend quota. |
| `screen_blocker` | A dialog is open, so a typed line would answer it. Use keys or prompt.answer. |
| `state` | The state checks the legacy verb has today; this protocol adds none. |
| `unsupported` | The host or the format cannot do it: a resize on a host that cannot, bytes on a terminal whose host serves only text. transcript.events for an agent the daemon runs as a terminal; the typing and terminal methods for an agent it runs as a structured session. |
| `not_self` | The agent named is not the caller's own session. |
| `attempt_limit` | Too many wrong pairing codes: five wrong tries kill a code, and five wrong codes in ten minutes from one address refuse device.redeem from it for fifteen minutes. |
| `unpaired` | A network connection with no valid device token called anything but device.redeem. The reply is always message "refused" and reason "not paired": no data, no method list, no version, no hint. A revoked token is refused the same way, and its connections are closed. |
| `pairing_code` | device.redeem: the code is wrong, expired or already used. The reply does not say which. |
| `proposal_changed` | hr.decide: the proposal changed after the caller read it (its `subject` is no longer the one handed in). Checked after the settled check. Nothing was done; the refusal's `proposal` is the proposal as it is now, to be read and decided again. |
| `proposal_settled` | hr.decide, hr.withdraw: the proposal is already approved, rejected or withdrawn, so there is nothing to decide or take back. The refusal's `proposal` is the proposal as it is now. |
| `confirm` | chat.cancel: the message was delivered, so cancelling needs confirm true. data.plan says what it would do. |
| `limit` | attachment.put: the file or the unsent total is over its limit, or too many uploads are open (x-attachments). |
| `listing` | inbox.queue: the caller may not list that agent's messages. Only the agent itself, its supervisor and the founder may. |
| `duplicate_title` | task.new: an open task under the same parent, or the parent itself, has nearly the title (same first word, 80% of the shorter title's words, Story prefix, case, punctuation and commit shas ignored). data.task names it; duplicate_ok opens the task anyway. |
| `open_children` | task.done: the task has open child tasks. The reason names each (number, state, assignee, title), the hint the commands that close one, and the refusal's `children` lists them, so a client can offer to discard them from the same prompt. |
| `denied` | file.write: the path is one the daemon never writes: under .git, .claude, .github, .husky, .codex, .cursor, .vscode, .devcontainer or a hooks folder (at any depth); a settings.json, settings.local.json, .mcp.json, mcp.json, hooks.json, package.json, .claude.json, tsconfig.json, turbo.json, deno.json, composer.json or lefthook.json; or anything but a .md or .json file. |
| `stale` | file.write: the file is not what the etag says (it changed, is gone, or exists when the etag is empty). Read it again; nothing was written. |
| `too_big` | file.write: the file on disk is over 1 MiB, so file.read gave only its start; it is not written (a text read from it would cut it). |

## Methods

A method marked write takes a required `request_id`.

| Method | Role | Write | Params | Result | Errors | Gates |
|---|---|---|---|---|---|---|
| `hello` | anonymous |  | `client`, `protocol`, `token?`, `key?` | `{protocol, min_client, daemon, schema, role, caller, capabilities, transport, methods}` | `refused`, `bad_request` |  |
| `whoami` | agent |  | none | `{caller, role, methods}` |  |  |
| `subscribe` | agent |  | `topics`, `since?`, `resume?`, `agent?` | `{cursor}` | `bad_request`, `refused` | role |
| `unsubscribe` | agent |  | `topics` | `object` | `bad_request` |  |
| `daemon.status` | agent |  | none | `DaemonStatus & ReadCursor` |  |  |
| `events.feed` | agent |  | `since?`, `topics?`, `limit?` | `{events, cursor, gap}` | `bad_request` |  |
| `operation.get` | member |  | `operation` | `Operation & ReadCursor` | `not_found` |  |
| `agents.list` | agent |  | `status?` | `{agents} & ReadCursor` | `bad_request` |  |
| `agent.get` | agent |  | `agent` | `AgentDetail & ReadCursor` | `not_found` |  |
| `pr.exists` | member |  | `agent`, `numbers` | `{repo, found}` | `not_found`, `bad_request` |  |
| `agent.timeline` | member |  | `agent`, `before?`, `limit?` | `{items, more} & ReadCursor` | `not_found` |  |
| `agent.options` | member |  | none | `AgentOptions` |  |  |
| `agent.set` | agent (only agent, founder) | yes | `agent`, `values`, `speaker?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` | not_self |
| `agent.fold` | agent | yes | `agent`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `agent.move` | agent | yes | `agent`, `account`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `agent.unstall` | founder | yes | `agent`, `request_id` | `Accepted` | `refused`, `not_found` | terminal_lock, state |
| `agent.force_start` | founder | yes | `agent`, `request_id` | `Accepted` | `refused`, `not_found` | state |
| `agent.retire` | founder | yes | `agent`, `request_id` | `Accepted` | `refused`, `not_found` | state |
| `agent.create` | founder | yes | `name`, `role`, `repo`, `base?`, `model?`, `effort?`, `parent?`, `account?`, `request_id` | `OperationStarted` | `refused`, `not_found`, `bad_request` | state |
| `session.start` | agent | yes | `agent`, `account?`, `repo?`, `base?`, `request_id` | `Accepted` | `refused`, `not_found` | terminal_lock, state |
| `session.wake` | agent | yes | `agent`, `request_id` | `Accepted` | `refused`, `not_found` | terminal_lock, state |
| `session.stop` | agent | yes | `agent`, `mode`, `fresh?`, `request_id` | `Accepted` | `refused`, `not_found` | role, terminal_lock, state |
| `session.restart` | agent | yes | `agent`, `fresh?`, `force?`, `request_id` | `Accepted` | `refused`, `not_found` | terminal_lock, state |
| `session.park` | agent | yes | `agent`, `request_id` | `Accepted` | `refused`, `not_found` | terminal_lock, state |
| `session.input` | member | yes | `agent`, `text`, `enter?`, `request_id` | `Accepted` | `refused`, `not_found` | role, terminal_lock, input_holder, wall, screen_blocker, unsupported |
| `session.keys` | member | yes | `agent`, `keys`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` | role, terminal_lock, input_holder, unsupported |
| `session.type` | member | yes | `agent`, `text`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` | role, terminal_lock, input_holder, unsupported |
| `session.interrupt` | member | yes | `agent`, `request_id` | `Accepted` | `refused`, `not_found` | role, terminal_lock, input_holder |
| `prompt.list` | member |  | `agent` | `{prompts} & ReadCursor` | `not_found` |  |
| `prompt.answer` | member | yes | `agent`, `prompt`, `choice`, `note?`, `request_id` | `Accepted` | `refused`, `not_found`, `stale_prompt` | role, terminal_lock, input_holder |
| `permission.ask` | agent |  | `tool`, `input` | `{behavior, message}` | `bad_request`, `refused` |  |
| `input.take` | member | yes | `agent`, `force?`, `request_id` | `Accepted & {holder}` | `refused`, `not_found` | role, input_holder, unsupported |
| `input.release` | member | yes | `agent`, `request_id` | `Accepted` | `refused`, `not_found` | role, unsupported |
| `chat.messages` | member |  | `agent`, `scope`, `before?`, `since?`, `limit?` | `{messages, more} & ReadCursor` | `not_found`, `bad_request` |  |
| `chat.requests` | member |  | `agent`, `scope`, `before?`, `since?`, `limit?` | `{items, more, version, cursor}` | `not_found`, `bad_request` |  |
| `chat.transcript` | member |  | `agent`, `before?`, `since?`, `limit?` | `{items, more, working} & ReadCursor` | `not_found`, `bad_request` |  |
| `transcript.events` | member |  | `agent`, `since_seq?`, `before_seq?`, `limit?` | `TranscriptEvents` | `not_found`, `bad_request`, `refused` | unsupported |
| `chat.locate` | member |  | `request`, `agent?`, `limit?` | `{agent, scope, message, messages, more_older, more_newer, transcript, transcript_more_older, transcript_more_newer}` | `not_found`, `bad_request` |  |
| `agent.strip` | member |  | `agent` | `{agent, model, account, ctx_pct, ctx_used, ctx_limit, cache_left_s, screen_word, screen_detail} & ReadCursor` | `not_found`, `bad_request` |  |
| `chat.refs` | member |  | `agent` | `{requests}` | `not_found`, `bad_request` |  |
| `chat.search` | agent |  | `agent?`, `query`, `scope?`, `with?`, `since?`, `limit?` | `{messages}` | `bad_request`, `not_found`, `refused` | role |
| `chat.send` | member | yes | `agent`, `body`, `task?`, `parent?`, `attachments?`, `parent_item?`, `delivery?`, `request_id` | `Accepted & {request, receipt}` | `refused`, `not_found`, `bad_request` |  |
| `chat.send_now` | member | yes | `request`, `request_id` | `Accepted & {receipt}` | `refused`, `not_found`, `bad_request` |  |
| `chat.cancel` | member | yes | `request`, `confirm?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `chat.seen` | member | yes | `agent`, `up_to`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `attachment.put` | member | yes | `name`, `mime`, `bytes`, `offset?`, `upload?`, `data`, `request_id` | `Accepted & {upload, received, attachment}` | `refused`, `not_found`, `bad_request` | role, limit |
| `attachment.get` | member |  | `id`, `thumb?`, `offset?` | `{mime, bytes, offset, data, done}` | `refused`, `not_found`, `bad_request` | role |
| `terminal.attach` | member |  | `agent?`, `terminal?`, `history?`, `format?` | `TerminalSnapshot \| TerminalBytesSnapshot` | `not_found`, `bad_request`, `refused` | unsupported |
| `terminal.history` | member |  | `terminal`, `before`, `limit`, `format?` | `HistoryPage \| HistoryBytesPage` | `not_found`, `refused`, `bad_request` | unsupported |
| `terminal.resize` | member | yes | `terminal`, `cols`, `rows`, `request_id` | `Accepted` | `refused`, `not_found` | role, input_holder |
| `terminal.input` | member | yes | `terminal`, `data`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` | role, terminal_lock, input_holder, unsupported |
| `terminal.close` | member | yes | `agent`, `request_id` | `Accepted` | `refused`, `not_found` | role, terminal_lock, input_holder, state |
| `terminal.open` | member |  | `agent` | `TerminalOpening` | `not_found`, `bad_request`, `refused` | role, unsupported |
| `terminal.detach` | member |  | `terminal`, `format?` | `object` | `not_found`, `bad_request` |  |
| `debug.terminals` | member |  | none | `{host, now, panes}` | `refused` | role, unsupported, state |
| `inbox.list` | agent |  | `unread?`, `kind?`, `agent?`, `before?`, `limit?`, `wait_s?` | `{items, more} & ReadCursor` |  |  |
| `inbox.queue` | agent |  | `whose?`, `all?`, `since?`, `wait_s?` | `{whose, newest, head, history, waiting, lines, dealt}` | `refused`, `bad_request` | listing |
| `inbox.get` | agent |  | `id`, `wait_s?` | `InboxItem & ReadCursor` | `not_found`, `bad_request` |  |
| `request.read` | agent |  | `id`, `wait_s?` | `{id, ref, requester, target, kind, state, created, chain_depth, parent, body, answer, parts}` | `not_found`, `bad_request` |  |
| `request.withdraw` | agent | yes | `id`, `reason?`, `request_id` | `{ok, said}` | `bad_request` |  |
| `request.cancel` | founder | yes | `id`, `dry_run?`, `request_id` | `{ok, plan, said}` | `refused`, `bad_request` |  |
| `team.seal` | founder | yes | `dry_run?`, `request_id` | `{ok, plan, said}` | `refused`, `bad_request` |  |
| `inbox.mark_read` | agent | yes | `ids`, `request_id` | `Accepted` | `not_found`, `refused`, `bad_request` |  |
| `inbox.reply` | agent | yes | `id`, `text`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `tasks.list` | agent |  | `owner?`, `reporter?`, `from?`, `state?`, `tags?`, `parent?`, `under?`, `before?`, `limit?`, `all?`, `everyone?`, `my_turn?`, `reason_gaps?` | `{items, more, reason_gaps} & ReadCursor` |  |  |
| `task.get` | agent |  | `task` | `TaskDetail & ReadCursor` | `not_found` |  |
| `task.history` | agent |  | `task` | `{task, rows}` | `not_found`, `bad_request` |  |
| `task.time` | agent |  | `task` | `{task, start, end, segments, estimate, expected} & ReadCursor` | `not_found`, `bad_request` |  |
| `task.shot` | member |  | `task`, `n`, `offset?` | `{mime, bytes, offset, data, done}` | `not_found`, `bad_request` |  |
| `task.new` | agent | yes | `owner`, `reporter?`, `title`, `body?`, `time_box?`, `parent?`, `duplicate_ok?`, `blocked_by?`, `watch?`, `priority?`, `estimate?`, `tags?`, `expects?`, `ui?`, `request_id` | `Accepted & {task}` | `refused` | duplicate_title |
| `task.handoff` | agent | yes | `task`, `to`, `note?`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.done` | agent | yes | `task`, `evidence?`, `request?`, `repo?`, `reason?`, `commit?`, `shots?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` | open_children |
| `task.reopen` | agent | yes | `task`, `note?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.progress` | agent (only agent) | yes | `task?`, `note`, `request_id` | `Accepted` | `refused`, `not_found` | not_self |
| `progress.get` | agent (only agent) |  | none | `{note}` |  |  |
| `task.begin` | agent | yes | `title`, `body?`, `message`, `priority?`, `expects?`, `why?`, `request_id` | `Accepted & {task}` | `refused` |  |
| `task.start` | agent | yes | `task`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.comment.list` | agent |  | `task`, `before?`, `limit?` | `{memos, more}` | `not_found` |  |
| `task.memo.list` | agent |  | `task`, `before?`, `limit?` | `{memos, more}` | `not_found` |  |
| `task.comment.add` | agent | yes | `task`, `text`, `now?`, `files?`, `request_id` | `Accepted & {memo}` | `refused`, `not_found`, `bad_request` |  |
| `task.memo.add` | agent | yes | `task`, `text`, `now?`, `files?`, `request_id` | `Accepted & {memo}` | `refused`, `not_found`, `bad_request` |  |
| `task.comment.edit` | agent | yes | `task`, `memo`, `text`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.memo.edit` | agent | yes | `task`, `memo`, `text`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.comment.delete` | agent | yes | `task`, `memo`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.memo.delete` | agent | yes | `task`, `memo`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.view.list` | member |  | none | `{views}` |  |  |
| `task.view.save` | member | yes | `name`, `filter`, `request_id` | `Accepted` | `bad_request` |  |
| `task.view.delete` | member | yes | `name`, `request_id` | `Accepted` | `not_found`, `bad_request` |  |
| `prefs.get` | member (only member, founder) |  | none | `Prefs & ReadCursor` | `refused` |  |
| `prefs.set` | member (only member, founder) | yes | `changes`, `request_id` | `Accepted` | `bad_request`, `refused` |  |
| `task.queue` | agent | yes | `tasks`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.draft` | agent | yes | `tasks`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.merge` | agent | yes | `into`, `tasks`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.release` | agent | yes | `task`, `to?`, `reason?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.request` | agent | yes | `request`, `title?`, `owner?`, `request_id` | `Accepted & {task, existing}` | `refused`, `not_found`, `bad_request` |  |
| `task.none` | agent | yes | `request`, `reason`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.wait` | agent | yes | `task`, `watch`, `time_box?`, `request_id` | `Accepted & {gate}` | `refused`, `not_found`, `bad_request` |  |
| `task.discard` | agent | yes | `task?`, `tasks?`, `reason?`, `reasons?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `task.board` | agent |  | `everyone?` | `{mine, reports}` |  |  |
| `task.withdraw` | agent | yes | `task`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.cancel` | member | yes | `task`, `reason?`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `task.edit` | agent | yes | `task`, `owner?`, `reporter?`, `title?`, `body?`, `time_box?`, `priority?`, `estimate?`, `blocked_by?`, `unblock?`, `add_tags?`, `remove_tags?`, `request_id`, `pr?` | `Accepted` | `refused`, `not_found` |  |
| `task.ask` | member | yes | `task`, `to`, `text`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `say` | agent | yes | `target`, `body`, `task?`, `no_task?`, `parent?`, `files?`, `request_id` | `Accepted` | `refused`, `bad_request` |  |
| `hook.record` | agent (only agent) | yes | `agent`, `kind`, `at`, `data`, `request_id` | `Accepted` | `refused` | not_self |
| `hr.propose` | agent | yes | `kind`, `args`, `why?`, `request_id` | `Accepted & {proposal}` | `refused`, `bad_request` |  |
| `hr.withdraw` | agent | yes | `proposal`, `request_id` | `Accepted` | `refused`, `not_found` | proposal_settled |
| `trust.propose` | agent | yes | `root`, `why?`, `request_id` | `Accepted & {proposal}` | `refused` |  |
| `trust.list` | agent |  | `proposals?` | `{roots, proposals, declared}` |  |  |
| `trust.remove` | founder | yes | `root`, `request_id` | `Accepted` | `not_found` |  |
| `agent.triggers` | agent |  | `agent?` | `{agents}` |  |  |
| `memory.status` | agent |  | `agent?` | `{agents}` |  |  |
| `team.get` | agent |  | none | `{agents, edges}` |  |  |
| `hr.list` | agent |  | `state?` | `{proposals} & ReadCursor` | `bad_request` |  |
| `hr.decide` | member | yes | `proposal`, `decision`, `note?`, `subject?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` | proposal_changed, proposal_settled |
| `accounts.list` | agent |  | `roster?`, `usage?` | `{accounts} & ReadCursor` | `bad_request` |  |
| `status.pick` | agent |  | none | `{picked, candidates, agent_costs}` |  |  |
| `account.set` | founder | yes | `account`, `label?`, `cap?`, `recover?`, `cycle?`, `cycle_end?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `account.wake` | founder | yes | `account`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` | state |
| `account.add` | founder | yes | `account`, `vendor?`, `request_id` | `Accepted & {account}` | `refused`, `bad_request` | role, state |
| `account.login` | founder | yes | `account`, `request_id` | `Accepted & {login_id, state, url}` | `refused`, `not_found` | role, state |
| `account.login_cancel` | founder | yes | `login_id`, `request_id` | `Accepted` | `refused`, `not_found` | role |
| `account.login_code` | founder | yes | `login_id`, `code`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` | role, state |
| `account.remove` | founder | yes | `account`, `dry_run?`, `confirm?`, `request_id` | `Accepted & {would_remove, plan}` | `refused`, `not_found`, `bad_request` |  |
| `repos.list` | member |  | none | `{repos}` |  |  |
| `worktrees.list` | member |  | `repo?`, `owner?` | `{worktrees}` |  |  |
| `worktree.diff` | member |  | `path`, `file?` | `{path, file, diff, truncated}` | `not_found` |  |
| `file.read` | founder |  | `agent`, `path` | `{text, size, truncated, mtime, etag}` | `bad_request`, `not_found`, `refused` |  |
| `file.write` | founder | yes | `agent`, `path`, `text`, `etag`, `request_id` | `{size, mtime, etag}` | `bad_request`, `not_found`, `refused` | denied, stale, too_big |
| `worktree.set_status` | member | yes | `path`, `status`, `request_id` | `Accepted` | `not_found`, `bad_request` |  |
| `schedules.list` | agent |  | none | `{schedules, errors}` |  |  |
| `schedule.add` | founder | yes | `target`, `cron`, `message`, `request_id` | `Accepted & {schedule}` | `refused`, `bad_request`, `not_found` |  |
| `schedule.edit` | founder | yes | `schedule`, `target?`, `cron?`, `message?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `schedule.remove` | founder | yes | `schedule`, `request_id` | `Accepted` | `refused`, `not_found` |  |
| `stats.get` | member |  | `window` | `Stats` | `bad_request` |  |
| `stats.headroom` | member |  | `from?`, `to?`, `step_s?` | `Headroom` | `bad_request` |  |
| `stats.outlook` | member |  | `range`, `agent?` | `Outlook` | `bad_request` |  |
| `connection.get` | founder |  | none | `ConnectionInfo` |  |  |
| `settings.get` | founder |  | `agent?` | `Settings` | `bad_request`, `not_found` |  |
| `settings.set` | founder | yes | `changes`, `agent?`, `request_id` | `Accepted` | `refused`, `bad_request` |  |
| `answers.list` | founder |  | none | `{answers}` |  |  |
| `answer.add` | founder | yes | `folder?`, `vendor?`, `title?`, `label?`, `request_id` | `Accepted` | `refused`, `bad_request` | unsupported |
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

Turn the connection into a stream of `event` notices; replays after `since` first, unless `resume` is latest. topics is a non-empty list of known topics, else -32602 bad_request naming `topics`. Notices start only after this reply is sent. devices is subscribed on its own, by the founder: a topics list naming devices and any other topic is -32602 bad_request naming `topics`, because devices is served by the gateway itself and every other topic by the daemon behind it. A devices subscription is live only: `since` and `agent` are ignored, the reply cursor is 0, its events carry cursor 0, and a client that reconnects reads devices.list again. The transcript topic needs member, as transcript.events does: an agent session that names it is refused, gate role, and nothing is subscribed (the stream holds every agent's tool inputs and results).

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

### `pr.exists`

Which of the numbers are pull requests (open, merged or closed) in the repo of the agent's worktree. numbers holds at most 20 integers, else -32602 bad_request naming `numbers`. The daemon asks GitHub with its own gh login, so a private repo answers. repo is null, and found empty, when the agent has no worktree or its origin is not on GitHub. A number that is a PR is remembered for good, one that is not for about a minute. When gh fails, found is empty and nothing is remembered.

### `agent.timeline`

The agent's recorded events, newest first. Today this is the agent's generic recorded events (the daemon's events table, rows of that agent): id is the event's id and `before` an event id; kind is the recorded kind; actor is the payload's `actor` when it is an object, else null; text is the payload's `text`, else `detail`, else `method`, else the kind; data is the whole payload ({} when it has none). State changes, deliveries and key presses appear only once they are recorded as events, which a later task does; clients must not expect them, and must treat kind as an open set.

### `agent.options`

What the new-agent form offers.

### `agent.set`

Change an agent's settings: an agent its own ordinary keys, the founder any (legacy agent_set).

### `agent.fold`

Run hl fold for an agent (legacy fold).

### `agent.move`

Move an agent to another account (legacy move).

### `agent.unstall`

Clear a STALLED agent: it goes to queued with its failure counters reset, as hl unstall does. Refused (gate state) when the agent is not stalled.

### `agent.force_start`

Let work that the machine's load or an account's pace holds for an agent (status reason load or pace) go past them once: it starts at the next tick, and agent.timeline records it (load_limit_passed, or pace_passed). Refused (gate state) when neither holds anything of the agent's.

### `agent.retire`

Retire an agent, as an approved HR retire does: it leaves team.yaml (team.yaml.bak keeps the old one), its scheduling row is deleted, its folder (ROLE.md, memory, settings) moves to agents/archive/<date>-<name>, its build caches are removed and its sealed key is forgotten. Its past messages, requests and events stay, and its git worktrees are judged: one that is merged into main, clean and no other agent's is removed (its branch stays); one that holds work (commits not on main, files not committed) stays and is put to the founder as a task tagged `worktree-removal`, which they answer as they answer any decision task: `1` (what is left is committed to a new branch rescue/<agent>-<date>, then the worktree is removed), `2 yes` (removed with whatever it holds; a bare `2` asks again) or `3` (it stays); nothing is removed before the answer, and an answer that cannot be carried out leaves the task open; any other worktree stays. `said` says which. Founder only. An agent with a session running is accepted, not refused: `said` names the wait, and the daemon retires it when its current turn ends (a session at its prompt is closed at once). Refused (gate state) while another agent reports to it or hangs under its name (<agent>.<rest>): the reason names them. An agent the daemon does not have is not_found.

### `agent.create`

Register a new agent, give it a worktree on a branch of a repo, and start it there, as hl spawn does. A long-running operation: the reply carries the operation id, and the operations topic and operation.get follow it to done or failed. Founder only. Parameters are checked before anything is looked up, in this order: request_id, name, role, effort, account, then the repo, the parent, the name against its parent, and whether the name is used. The operation has kind agent.create and its agent is the new name from the first event. steps_total may be null while it is unknown; a client shows step as the daemon sent it and invents none. When it is done, result is {agent, registered: true}. When it failed, result is {agent, registered}: registered says whether the agent was added to the team before the failure, so a failed operation can leave a registered agent that is not started; nothing is cleaned up. A name already used by an agent is refused (gate state) and no operation starts. A name with a parent must be '<parent>.<rest>'; anything else is bad_request, field name. base omitted means the daemon's default branch name, which is main today; a client that knows the repo's default_branch sends it.

### `session.start`

Launch a session for the agent, optionally on a named account: in its terminal, or for a structured agent as a structured session with no terminal.

### `session.wake`

Resume a closed session.

### `session.stop`

exit asks Claude to quit; close ends the session and keeps the terminal; kill ends the process. An agent may call it for mode exit and close, as the legacy verbs allow; mode kill is refused to the agent role (`refused`, gate `role`) before the agent is looked up, because it ends another session mid-turn with no handoff record. Kill is for a person. For a structured agent exit and close are the same: the daemon ends the CLI's input and waits for it to exit, and kill ends its process group. Close mid-turn is refused (gate state): interrupt first.

### `session.restart`

Restart a session.

### `session.park`

Park an agent.

### `session.input`

Type text at the prompt; Enter optional. Refused at a shell prompt (gate state, reason 'nothing sent - a shell prompt is showing'): the daemon types nothing when the pane shows a shell, since a line there would run as a command. session.type and session.keys are not checked this way. Refused with gate screen_blocker while the screen shows a dialog or one the daemon does not recognise, which includes the moments right after a wake, before the screen has been read: nothing is typed; session.keys and terminal.input are the way through (they pass an open dialog), or call again once the screen reads. Each call is recorded as an event of the agent (kind type, key or input) whose actor is the caller: for a paired device, kind device with its id. A daemon that runs structured agents need not serve this method (the Go daemon does not: it is absent from hello, so a client never calls it for one). One that serves it refuses an agent run as a structured session, gate unsupported: it has no terminal to type at, so a client sends chat.send and watches the transcript.

### `session.keys`

Named keys for menus and dialogs. Each call is recorded as an event of the agent (kind type, key or input) whose actor is the caller: for a paired device, kind device with its id. A daemon that runs structured agents need not serve this method (the Go daemon does not: it is absent from hello, so a client never calls it for one). One that serves it refuses an agent run as a structured session, gate unsupported: it has no terminal to type at, so a client sends chat.send and watches the transcript.

### `session.type`

Plain typing into the pane: characters sent as typed, no Enter, no prompt checks. Which call sends text when: session.type is what a person typing in the pane uses, with input.take first; it passes the wall and any open dialog, as the keys would. session.keys sends named keys (menus, dialogs, ctrl-c). session.input sends a whole prompt line for the daemon's own flow and is refused (gates wall, screen_blocker) while a usage wall or a dialog is up, since a typed line would answer the dialog. Each call is recorded as an event of the agent (kind type, key or input) whose actor is the caller: for a paired device, kind device with its id. A daemon that runs structured agents need not serve this method (the Go daemon does not: it is absent from hello, so a client never calls it for one). One that serves it refuses an agent run as a structured session, gate unsupported: it has no terminal to type at, so a client sends chat.send and watches the transcript.

### `session.interrupt`

Stop the turn in progress (Esc for a terminal agent; a control request to the CLI for a structured one). For a structured agent no terminal lock or input hold applies, and it is refused (gate state) when no turn is running, so a stale button learns why.

### `prompt.list`

The open permission prompts and dialogs.

### `prompt.answer`

Answer the prompt the caller saw; if it changed nothing is sent. For a structured agent's permission prompt the choice is one of its option keys, allow or deny; yes, no or esc is bad_request (field choice). No terminal lock or input hold applies. A prompt whose source is hook is answered the same way, allow or deny, through the call that is waiting on it (permission.ask).

### `permission.ask`

An agent's session asks the founder whether it may run a tool call, and waits for the answer. Called by `hl hook PermissionRequest`. Raises a Prompt of kind permission, source hook, with options allow and deny, and holds the call until prompt.answer answers it, the caller hangs up, or an hour passes. One such prompt per agent is open at a time; a second call waits for the first to close. Nothing is typed into the agent's terminal. Refused for a caller that is not an agent's session.

### `input.take`

Become the one who types at this terminal. A daemon that runs structured agents need not serve this method (the Go daemon does not: it is absent from hello, so a client never calls it for one). One that serves it refuses an agent run as a structured session, gate unsupported: it has no terminal to type at, so a client sends chat.send and watches the transcript.

### `input.release`

Hand the terminal back to the daemon. A daemon that runs structured agents need not serve this method (the Go daemon does not: it is absent from hello, so a client never calls it for one). One that serves it refuses an agent run as a structured session, gate unsupported: it has no terminal to type at, so a client sends chat.send and watches the transcript.

### `chat.messages`

Messages of an agent's chat, newest first. Page with before (older) or since (newer).

### `chat.requests`

A chat's threads for the screen that draws them: the requests of an agent's chat oldest first, with the task changes and the session starts among them, and one version that changes when anything in the chat does. items are oldest first, each with a `type`: `request` is a chat.messages ChatMessage plus `task_state`; `task_event` is a change of a task delivered in the page, since the first request in it (the state it reached, a handoff, an edit), with `request` naming the delivery a handoff belongs to so a client can draw both as one card; `session` is the start of a new conversation of the agent since that request (one that resumed the old conversation is not listed). Requests that no chat draws (withdrawn, never reached their target) are left out. HR cards are hr.list's.

### `chat.transcript`

The agent's replies, tool activity, prompt cards and task events as ordered items, plus its working state. Oldest first, in pages: `before` reads the page just older than an item id, `since` reads the items from the second that id is in. Items are the agent's replies, its tool calls, the prompts it opened and the events of the tasks it owns. A tool item's id is the second of the call and its `output` is what the store held when the page was read: a result that lands in a later second than its call is not sent again by `since`, so a client that needs it reads the page again (without `since`). A page carries at most 2 MiB of tool output between its items; the newest items keep their output and an older one is cut to fit, with `dropped_lines` counting what was left out. Session items (kind session) are part of the list: each carries session.cause and session.handoff, and sorts among the others by its second.

### `transcript.events`

A page of the agent's transcript events, by seq: the snapshot of the transcript topic. Refused (gate unsupported) for an agent the daemon does not run as a structured session (a terminal agent: watch its terminal). since_seq with before_seq, or a limit outside 1 to 500, is bad_request naming the field. Served for a structured agent whether or not its session is live, from the stored log.

### `chat.locate`

Where a message sits (its msg_ name, a bare hash or its number): its conversation, scope, the page of messages around it and the transcript of the same span. not_found when the request does not exist or no chat draws it (withdrawn, or never reached its target). The page has up to `limit` messages, about half of them older than the one asked for. When the request exists and only no chat draws it, the not_found data also carries `from`, `to` and `body` of that request (body as typed, control characters stripped), so a client can say what it was; for a request that does not exist it carries none of them.

### `agent.strip`

The line under an agent's chat header: model, account, context, prompt-cache life and the screen word. Context figures come from the usage snapshot of the agent's own session, so they are null for an agent with no account or no snapshot.

### `chat.refs`

What a # in the composer can point at: the agent's newest requests. The agent's tasks are `tasks.list` with `owner` or `from`. A request that no chat shows is read with `chat.locate`, whose not_found carries from, to and body.

### `chat.search`

Find messages by text; each hit carries the scope that shows it (the jump is chat.locate). An agent searches only its own messages: `agent` absent is the caller, any other name is refused (gate role).

### `chat.send`

A message to an agent through the daemon's delivery (queued, typed, landed); needs no input hold. With parent, a reply to a message. To a structured agent the request_id is the operation id and the result carries a receipt. To a structured agent the result holds `receipt`; to a terminal agent it does not. A repeat (the same request_id and params) returns replayed true and the receipt as it is now, not the first reply, and writes nothing; see x-delivery. A repeat of an operation that was withdrawn is refused (gate state): a retry does not bring back what the person took back. With attachments, the delivered text ends with one path line per image (x-attachments). With parent_item the delivered text begins with the reply quoted, each line after `> `, then a blank line, then the body. The quote is the reply's first 300 characters, counted as Unicode code points (not UTF-16 units), with no ellipsis added; the daemon cuts, once, and keeps the same text with the message (ChatMessage.parent_item), so a client draws it without reading the transcript again and the stored and the delivered quote are the same.

### `chat.send_now`

Write a queued message now, ahead of the daemon's delivery order; the request keeps its id and goes on as sent. For a message to a structured agent whose receipt is queued: written to the CLI with priority now, receipt sent. Any other receipt is refused (gate state), including a message written meanwhile. The daemon makes the release atomic with its queue, so a write that has started is never promoted twice. A terminal agent's message is as before.

### `chat.cancel`

Take back a message the person sent, delivered or not. The person's own undo. A message not yet delivered is taken back and nobody is told. To a structured agent a message whose receipt is queued is withdrawn the same way, with no confirm: it was never written to the CLI. A delivered one (receipt sent, confirmed or unknown, or a terminal agent's delivered message) is closed as rejected after confirm, its agent is told to stop and whatever queued behind it moves up. A withdrawn operation is dropped from chat.messages and a chat.send of its request_id is refused (gate state). A message the person did not send, or one already closed, is refused (gate state).

### `chat.seen`

Record that the person has an agent's messages in front of them, up to a message number. What it moves: the agent's messages to the person up to up_to leave the FOR YOU list as new (the mailbox's unseen count). Only that list forgets; decisions still count in the summary.

### `attachment.put`

Upload an image for a message, in chunks; the last chunk returns the Attachment to name in chat.send. See x-attachments. A chunk whose offset is not `received` is bad_request, field offset, and the reply's data says what the daemon holds, so a client that lost a reply resumes from there; the same request_id and params replays as for every write and writes nothing. When received reaches bytes the daemon checks the type by the first bytes and answers with `attachment`; a size over 10 MiB, an unsent total over its limit or too many open uploads is refused, gate limit, and the partial upload is dropped. An upload id that is unknown or expired is not_found.

### `attachment.get`

Read an attached image or a memo's file, or a small copy of an image, for drawing. See x-attachments. An id that no longer exists (never put, or kept past its time) is not_found.

### `terminal.attach`

Subscribe to one terminal; replies with a snapshot, then pushes notices. `format` chooses text rows (frame notices), text rows with style runs (frame notices, `styled`) or bytes (output notices). A connection has at most one attach per terminal and format: attaching a terminal and format the connection already watches replaces that attach, accepted and never refused (no notice of the old one follows the reply; the reply is a fresh snapshot, and the new attach's counter starts at its seq). An attach in the other format is independent, with its own counter. A client that wants more than one view of one terminal and format shares one attach. With agent, an agent that has no terminal, a structured agent among them, is not_found (terminal). With terminal, a handle no agent has is watched while the host lists it (it has no input holder, so terminal.input and terminal.resize refuse it); one the host does not list is not_found.

### `terminal.history`

Page scrollback; absolute line indexes.

### `terminal.resize`

Resize, never under 80 by 24: the input holder's while there is one, any attached connection's while there is none. While a person holds the input, only they resize, and the size from before their first resize is restored when they leave; anyone else is refused, gate `input_holder`, and scales its own view. While nobody holds it (the daemon's own hold included), any connection that has the terminal attached, in any format, may resize it to at least 80 columns by 24 rows, and the last resize wins; when the last attach goes and nobody holds the input, the size from before the first of these resizes is restored. From a connection that has not attached it, the resize is refused, gate `input_holder`. Under 80 by 24 any resize is refused, the holder's included, gate `input_holder`, and the caller keeps its grid and scales its own view: the daemon reads the screen line by line, and narrower, Claude Code wraps a dialog's footer and options so the daemon no longer recognises the dialog. Every attach is told of the change: a `resize` notice for text and styled, a `snapshot` notice, reason `resize`, for bytes. A handle no agent has is never resized.

### `terminal.input`

Raw keystrokes or pasted bytes into the terminal, from the input holder only. For a client that is a terminal (xterm.js), as session.type is for a person typing: the same gates, and it passes the wall and an open dialog as session.type and session.keys do (the person sees the screen; session.input keeps both gates). Unlike them it is refused while the daemon holds the input, since a stream of keystrokes beside the daemon's own typing would corrupt both: take the input first. A terminal with no holder, a login terminal, refuses it too. A pane showing a shell prompt takes it, as with session.type. Calls on one connection are applied in the order they were read, however many are waiting. It is recorded as an event of the agent (kind type) whose actor is the caller, a paired device by its id, with the byte count and never the bytes. Terminal hosts that cannot send keys refuse with gate unsupported.

### `terminal.close`

Close the host's terminal to free it (the web UI's Close terminal). Different from session.stop close, which ends the session and keeps the terminal: refused while a session runs.

### `terminal.open`

Where an agent's terminal is and how to attach to it, for the one button that opens it when the agent needs you. Answers where the agent's own terminal is: the host (`tmux`), its handle, and `attach`, the command a client on the daemon's machine runs to see and answer it (`argv`, null when the host has no command; the client then uses `host` and `terminal`). It types nothing and answers nothing; the person at the terminal answers the dialog, which is the point (see x-dialogs). `focused` is true when the host was also asked to bring the terminal to the front (tmux select-window; best effort, false when it could not). Callers: the founder and a paired device (member); an agent is refused, gate role. An agent that is not registered is not_found (agent); one with no terminal, a structured agent among them, a closed one (it keeps the handle it had) and one whose terminal the host no longer lists are not_found (terminal). A device on another machine than the daemon's gets the same answer; `attach` names a local socket, so it is useful only where the daemon runs, and `local` says whether the caller is there (true for the unix socket, false over the network).

### `terminal.detach`

Stop receiving this terminal, in one format or in all. Never stops the agent. not_found is for a terminal handle that does not exist. Closing the connection ends every attach.

### `debug.terminals`

Every pane of the terminal host with what the daemon knows of it, for the hidden debug window. Lists every pane of the daemon's terminal server (tmux), whichever agent has it or none, to find panes kept open past their prompt cache and shells left behind after an agent's session exited. Reads only: the host's pane list, the process table and the daemon's own rows; of the agent's view only cache_until. A host that cannot list its panes is refused, gate unsupported; one that cannot be reached, gate state. Watching a listed pane is terminal.attach by its handle; a pane no agent has takes no input. Callers: the founder and a paired device (member); below that is refused, gate role.

### `inbox.list`

Inbox items; an agent sees its own.

### `inbox.queue`

An inbox as hl inbox prints it. An agent's inbox is the one request at the head of its queue (the oldest queued one whose routing was checked; a say is never the head), and with `all` every request ever sent to it. The founder's is the mailbox folded to one line per agent, a decision keeping its own line, and with `all` the 20 most recent requests to or from the founder that are no longer queued. An agent may list its own inbox and those of the agents it supervises; the founder any. Nothing is marked read: the caller sends inbox.mark_read for the head it showed.

### `inbox.get`

One item.

### `request.read`

One request whole, as hl read prints it. Any caller may read any request, with both of its ends named; a request to an agent that the routing table has not passed yet is not_found to that agent. The founder reading one opens it: it is marked read, and when it is one the founder asked, its answer is marked told. A member's device reading it opens nothing. With `wait_s` the call holds while the request is queued; `state` says whether it settled, and a wait that ends with it still queued opens nothing.

### `request.withdraw`

Take back a request of your own before it is delivered, as hl withdraw does. The caller is the requester: an agent's session withdraws that agent's request, and the founder's key withdraws the founder's own. A request that is not the caller's, one already delivered or closed, and an id that does not exist are not errors: the reply has `ok` false and `said` gives the reason.

### `request.cancel`

The founder takes any request out of an agent's queue, as hl cancel does; dry_run names what would happen. Unlike chat.cancel it is not limited to messages the person sent, and it asks for no confirm: the call without `dry_run` is the go-ahead. A queued request is closed as rejected, delivered or not; its agent is told to stop when it was delivered, and its requester is told unless that is the founder. The founder's own request that was never delivered is withdrawn and nobody is told. A request that is gone or already closed is not an error: `ok` is false, with the reason in `plan` (dry run) or `said`. A dry run writes nothing and records nothing, and no reply is kept under the request_id.

### `team.seal`

The founder seals team.yaml and every agent.yaml as they are now (the Team page of the app); dry_run names what would be sealed. Seals are what lets the daemon tell a file the founder approved from one changed behind their back. The call without `dry_run` is the go-ahead and seals every file that is not sealed as it is, and marks the seals as begun; a file already matching its seal is left. A dry run lists each file it would take on, `new` (no seal before) or `changed` (it did not match its seal), with the digest it would be sealed with, and writes nothing and records nothing. Nothing to seal is `ok` true with that said. An agent's session is refused by role.

### `inbox.mark_read`

Mark items read; an id that is not yours is not_found. For the founder, every message of an agent up to the newest id named is read; a decision or a reply owed is never read away, so such an id, and what lies past it, stays unread (refused, gate state, when nothing could be read). For an agent only the head of its own queue can be read, as `hl inbox` does; another id of its own is refused, gate state.

### `inbox.reply`

Reply to an item. A message to whoever sent the item, with the item as its parent link (`hl say --parent`); it settles nothing. An agent replies to its own incoming items; the founder to a mailbox item that is not a decision (a decision is a task, closed with task.done: refused, gate state).

### `tasks.list`

Task rows. Newest id first. To read every task, call with `all: true` and `limit: 200`, then again with `before` set to the smallest `id` of the page before, until `more` is false: nothing is skipped or repeated, because a page is the rows with an id below `before`. The caller's own scope still applies (itself and its reports for an agent, unless `everyone`).

### `task.get`

One task with body, delivery history and linked events.

### `task.history`

Every change to a task, newest first: one row per field changed, with who made it, the value before and after, and when. It is read from the events each task write keeps in its own transaction, so it is never edited or deleted; a fix is a later row. A deleted memo's words stay in it. Anyone may edit a task (task.edit); this history is what makes that safe to undo by hand.

### `task.time`

Where a task's time went, from its opening to its end (or now): segments in order that cover it with no gap or overlap. Read from what the daemon already records (the task's state events, its blockers, its asks of the founder, its holder's parks and starts); nothing is timed twice. Where two causes overlap, the stretch shows one, in this order: waiting on the founder, blocked by a task, waiting on another agent, queued, at an account's wall, starting, working.

### `task.shot`

A screenshot kept with a finished task. One screenshot of a task whose end recorded it, in chunks. A task or number the result does not hold is not_found.

### `task.new`

Create a task. The founder or a member device opens one for an agent only, and it opens queued. The owner may not be the caller: a task an agent takes on itself is begun from the founder's message (task.begin).

### `task.handoff`

Hand a task to another agent. Anyone may hand an open task on, not only its owner (the asker takes a task back so). A review is a handoff: the holder hands the task to its reviewer, who hands it back with findings or on, or marks it done. task.history's row names who made the move (by).

### `task.done`

Finish a task with evidence. Exactly one of evidence, request or reason-with-evidence; none or two is bad_request. A commit and shots are optional, for any task; when given they are kept with the task (task.get result). A done by the owner or the founder closes the task: nothing waits on an accept. The founder's done with evidence words on a task they own whose card still waits on them decides that card with the words, so the asker is told and what waits on the answer (a trust proposal) is applied; it says `T<n> decided`.

### `task.reopen`

Reopen a done task: anyone may, with a reason the opener and assignee are told. A discarded task comes back as a draft, to be queued again.

### `task.progress`

Record progress (hl progress): on a task, or with no task on the caller's request in progress, else the task it holds, else the agent, even when it holds no task.

### `progress.get`

The caller's progress note: the one on its request in progress, else on the task it holds, else on the agent (what hl progress shows with no note).

### `task.begin`

Start a task the founder told the caller to do, without approval: opened for the caller, linked to the founder's message.

### `task.start`

Pick up a task as the caller's current one.

### `task.comment.list`

Comments beside a task. The wire still calls a comment a memo (Memo, `memo`, `memos`); task.memo.list is its old name.

### `task.memo.list`

The old name of task.comment.list: the same params, result and errors. Kept for clients that call it.

### `task.comment.add`

Add a comment; anyone may. It is told to whom the task answers (its reporter, else its opener), to its assignee whatever the task's state, and to each agent it names as @name; never to its writer or the founder. The holder of a doing task and a reader at its prompt are told at once; a reader mid-turn is told at the end of its turn, with all else then waiting for it in one message, and its lead is told if it is still unread an hour on. comment.edit and comment.delete are told the same way. The wire still calls a comment a memo (Memo, `memo`, `memos`); task.memo.add is its old name.

### `task.memo.add`

The old name of task.comment.add: the same params, result and errors. Kept for clients that call it.

### `task.comment.edit`

Edit a comment: its author or the founder. The wire still calls a comment a memo (Memo, `memo`, `memos`); task.memo.edit is its old name.

### `task.memo.edit`

The old name of task.comment.edit: the same params, result and errors. Kept for clients that call it.

### `task.comment.delete`

Delete a comment: its author or the founder. The wire still calls a comment a memo (Memo, `memo`, `memos`); task.memo.delete is its old name.

### `task.memo.delete`

The old name of task.comment.delete: the same params, result and errors. Kept for clients that call it.

### `task.view.list`

The saved views of the task board, by name.

### `task.view.save`

Keep a filter of the task board under a name, replacing a view already kept under it.

### `task.view.delete`

Delete a saved view.

### `prefs.get`

The person's view preferences: the agent list's pins, filter, layout and folds.

### `prefs.set`

Change the person's view preferences: each key given in changes.nav replaces the stored one. A pin, a fold or an order entry naming an agent the daemon does not know, or a name given twice, is bad_request naming the field. Once stored, a prefs.changed event on the prefs topic carries the whole Prefs. agent.retire drops the agent from pins, collapsed and order (and sends prefs.changed); a rename (hr) carries it over in place.

### `task.queue`

Approve draft tasks to run: anyone may; each task's opener and assignee are told.

### `task.draft`

Send queued or doing tasks back to draft: anyone may; each task's opener and assignee are told. A holder working the task is told to stop and moved on to its next task, and commits or changes it left in its worktree are kept in a memo on the task.

### `task.merge`

Fold tasks into one: the kept task takes their titles, requests and sub-tasks; anyone may, and the opener and assignee of each are told.

### `task.release`

Take a doing task back to queued or draft: anyone may, with a reason the opener and assignee are told (and the founder, for a task begun on their message).

### `task.request`

Make a founder's chat message a request: a task the founder opened (opened_by human), owned by the lead the message was sent to. The message becomes its first comment, images included. A message converts once; a second call returns the task already made from it. The founder, or the agent the message was sent to, may call it. Clears a no-work mark on the message.

### `task.none`

Mark a founder's chat message as needing no work, with a reason in one line, so it is not chased as unanswered. Refused for a message already made a request. The founder, or the agent the message was sent to, may call it.

### `task.wait`

Make a task wait on outside state: a gate on watch (as task.new with owner gate), the task blocked by it and, when doing, released, all in one write, so the task never waits on a gate that blocks nothing. The task comes back to its holder's queue when the gate opens.

### `task.discard`

Throw away a task that is not done: anyone may, with a reason the opener, assignee and asker are told. A request still open on it refuses, unless the caller leads a team whose tree holds the task (its owner or opener is the lead or reports to it): then each open request is withdrawn and its target told. tasks in place of task discards a batch in one call, all or none, with one discard_batch event: the one task_batch card in the caller's chat. A request that already landed in its agent's session does not refuse the task's asker or the founder: the task is discarded, that request closes with it and its holder is told why; anyone else is refused.

### `task.board`

The caller's open tasks and its reports', and who holds each now. The founder's board is the tasks the founder owns.

### `task.withdraw`

Withdraw a task the caller asked for; anyone else discards it with a reason.

### `task.cancel`

Cancel a task.

### `task.edit`

Change a task: owner, reporter, title, priority, body and time box, by anyone (body and time box until it is delivered). Waiting on other tasks is added (blocked_by) or removed (unblock) with it. Never silent: the opener and assignee are told, and the edit is kept with who made it.

### `task.ask`

The founder asks an agent a question about a task: a message linked to the task, whose holder does not change. The agent's reply joins the task.

### `say`

A message typed at once; needs no answer, starts no work.

### `hook.record`

A hook records an event; the spool replays it with the same request_id.

### `hr.propose`

Propose a roster change; the founder decides (hl hr add, retire, rename, link, unlink, team, settings, context, and diff, which is kind bundle: args.changes lists several changes decided once and applied together or not at all).

### `hr.withdraw`

Take back an undecided proposal: its author's own, or any for the founder; another agent is refused (gate role).

### `trust.propose`

Propose declaring a folder ocp may answer Claude's trust prompt in (hl trust add); the founder's own call declares it. Called by an agent it raises a decision for the founder, `why` required, and answers the proposal's id; called by the founder it declares the root directly, `why` optional (default `added by the founder`), and answers no proposal. A root already inside a declared one, a missing why or a decision that could not be raised is refused with the store's words. A paired device is refused, gate role. `root` must be an absolute path (bad_request, field `root`): the daemon would read a relative one, or `~`, against its own folder and home, so the client resolves it against its own.

### `trust.list`

Declared trust roots, and with proposals the proposals (hl trust list, hl trust proposals).

### `trust.remove`

Drop a declared root (hl trust remove); needs no approval. The founder's: the declared roots live in the protected store, which an agent session cannot write, so an agent calling `hl trust remove` is refused today and by this method (gate role). A root that is not declared is not_found. `root` must be an absolute path, as for trust.propose (bad_request, field `root`).

### `agent.triggers`

What the daemon would decide for an agent right now (hl triggers). One row per agent the daemon knows, in name order; with `agent`, that agent's row alone, and an agent the home does not have is not_found. `fired` is whether the next tick would hand it off; `reason` is the trigger's reason, or for an agent whose triggers do not apply, what holds it (a walled wake, its state until its wake, or no session).

### `memory.status`

Per-agent memory against its budget (hl memory status).

### `team.get`

Agents, roles, allowed edges, role and base file metadata.

### `hr.list`

HR proposals with their diff: what waits for the founder, and what settled recently. Without `state`: every proposal that waits (open, applying, and apply_failed until it is decided again) and every settled one (approved, rejected, withdrawn) from the last 7 days, at most 50 of those. Order: the waiting ones first, oldest `created` first; then the settled ones, newest `settled` first. A client counts the dot from `open` and `apply_failed`, never from `applying`. With `state`, only that state, in the same order, and no 7-day or 50 bound. A proposal whose requested_by is human (the founder's own change) is listed as any other, so a held one shows while it waits. The Python store's states map as: proposed = open; approved = apply_failed (it holds the founder's approval and the change is not applied: `error` is set when an apply ran and failed, null when it never ran, e.g. a crash between the two); applied = approved; rejected = rejected; withdrawn = withdrawn; enacting and failed are never listed.

### `hr.decide`

Approve or reject a proposal; approving an apply_failed one applies it again. Approving a change to an agent with a session running is accepted, not refused: the proposal turns applying, `said` names the agents whose turn it waits on, and the change is applied when those turns end. Refused with gate proposal_changed or proposal_settled, carrying the proposal as it is; an applying proposal is refused proposal_settled.

### `accounts.list`

Accounts with login state, use, cap, agents, recent moves.

### `status.pick`

Which account the next agent would be placed on, each account's reason, and what one more agent costs by kind.

### `account.set`

The account Settings form in one call: alias, cap, usage recovery and the month cycle; nothing is written if any is refused. Fields not given are left alone. Chosen over set_cycle_on/set_cycle_end methods because the UI saves these together and the CLI twins (cap, cycle, recover, alias) each write one of these fields.

### `account.wake`

Wake a STALLED account: its stall ends, agents may be placed on it again and the agents stalled with it are released, as session.wake clears a stalled agent (hl account wake). A login on the account, or a turn that ends well there, ends the stall too. Refused (gate state) when the account is not stalled.

### `account.add`

Register an account on this machine; it is not logged in until account.login runs. As `hl account add <name> --no-login`: the account's directory is made and linked to the shared files, and nothing is logged in, so accounts.list shows it with login_state needs_login and the accounts topic sends account.changed. A name already used is refused (gate `state`). A name that is empty, holds a slash, starts with a dot or would not be read back as text is bad_request, field `account`; a vendor the daemon has no driver for is bad_request, field `vendor`. Nothing is changed on a refusal. The app then calls account.login. Offering to trust the agents' worktrees on the new account is not part of it (settings.trust).

### `account.login`

Start a login on the account; answers the login id and the URL to open. No terminal is opened: the CLI runs without one, its URL is read from its stdout and its code is fed to its stdin by account.login_code. Refused with gate `state` while the account is already logging in; not_found for an unknown account. The end of a login is not a method: the accounts topic reports it in the account's `login`, state waiting, then code_sent after account.login_code, then done, or failed with a `reason`, or cancelled by account.login_cancel. A login that is done leaves login_state ok.

### `account.login_cancel`

Stop a login in progress. Idempotent: a login already ended answers accepted. An id the daemon never issued is not_found. The login's whole process group is killed, since the CLI ignores SIGTERM for a second.

### `account.login_code`

Hand the one-time code from the authorize page to a login waiting for it. The code is never echoed back, never in an event, a log or the request record. Refused with gate `state` when the login is not waiting for a code. An id the daemon never issued is not_found.

### `account.remove`

Log an account out and take it off the roster; dry_run names what would happen. The sequence of `hl account remove`: refused (gate `state`) while an agent is on the account or when claude's logout fails. The account's directory is moved aside, never deleted. A dry run writes nothing and records nothing.

### `repos.list`

Repos.

### `worktrees.list`

Worktrees with status and changed files.

### `worktree.diff`

Read-only git diff run by the daemon. Without `file`: the changes to tracked files against HEAD, then a new-file diff of each untracked file that .gitignore does not ignore (at most 200; one too big for what is left of the size cap is listed as `(new file, N bytes: not shown)`, not diffed). With `file`: that file alone, untracked or not.

### `file.read`

The founder reads a text file in an agent's worktree.

### `file.write`

The founder replaces or creates a .md or .json file in an agent's worktree, if it is still what the etag says.

### `worktree.set_status`

Set the status label of a worktree. The status is a free string (Worktree.status), 1 to 40 characters; the label changes nothing on disk, and worktrees.list shows it in place of the daemon's own word (dirty or clean). A path that is not a worktree an agent works in is not_found, what worktree, as for worktree.diff.

### `schedules.list`

Schedules. An agent whose `schedules` value is bad is listed in `errors` (target and the error text), not shown as an agent with no schedules.

### `schedule.add`

Add a schedule.

### `schedule.edit`

Edit a schedule.

### `schedule.remove`

Remove a schedule.

### `stats.get`

Counts and sums over a window.

### `stats.headroom`

Retired: stats.outlook's by_account carries the same lines and the pace line, and no app reads this. It leaves the contract when the daemon stops serving it. Each account's 5h and weekly headroom over a span, recorded up to now and guessed after, its resets, and the average of what binds.

### `stats.outlook`

The week left of the accounts together, recorded and guessed, each account's runway, and what the agents cost per day.

### `connection.get`

How this process is reached: its listener, the heavy-run slots and dev mode. Read only.

### `settings.get`

Timings, terminal host, trust folders, hooks installed.

### `settings.set`

Change settings: any keys of SettingsValues, globally or as one agent's override.

### `answers.list`

The founder's saved answers: trusted folders and dialog rules (Home settings). A daemon that answers no dialog lists no dialog rules (x-dialogs).

### `answer.add`

Save a standing answer: a folder to trust, or a dialog rule (vendor, title and label). Blank fields add nothing; a half-filled rule is refused. Founder because the dialog-rule store and trust are the founder's alone: an agent that could write them could answer its own dialogs. A dialog rule (vendor, title, label) is retired in a daemon that answers no dialog (x-dialogs): refused, gate unsupported, nothing saved; a folder is saved as before.

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

x-callers on a method, when present, lists the only roles that may call it, whatever the chain says. The agent-identity methods (hook.record, agent.set, task.progress) act as the caller's OWN session, found by peer pid or session token as say already finds its sender: an `agent` argument that is not the caller's own name is refused (gate not_self), and a member device cannot call them (agent.set is also the founder's, who may name any agent).

- **Sender:** say and task.new carry no sender parameter: the daemon sets it. An agent's calls carry its own name; a member device's and the founder's carry `human`.
- **Ownership:** Methods that act on a task by id (edit, merge, discard, reopen, queue, draft) are anyone's: the task's opener and assignee are told, and its history keeps who did it. withdraw, release, memo.edit and memo.delete keep their checks on who may; a refusal is `refused` with the reason.

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
| `hl task comment list` | `task.comment.list` |
| `hl task comment add` | `task.comment.add` |
| `hl task comment edit` | `task.comment.edit` |
| `hl task comment delete` | `task.comment.delete` |
| `hl progress` | `task.progress` |
| `hl inbox` | `inbox.queue` |
| `hl read` | `request.read` |
| `hl say` | `say` |
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
| `hl team` | `team.get` |
| `hl ps` | `agents.list` |
| `hl status` | `status.pick` |
| `hl events` | `events.feed` |
| `hl triggers` | `agent.triggers` |
| `hl schedule (show)` | `schedules.list` |
| `hl account list` | `accounts.list` |
| `hl memory status` | `memory.status` |
| `hl task request` | `task.request` |
| `hl task none` | `task.none` |

## Dropped from agents on purpose

Agent commands the spec does not keep at agent role, for the founder to decide. Each is founder-only or a person's act today, or is not a daemon call.

- **Founder only today:** `hl cancel`; `hl unstall`; `hl hr approve|reject|apply|relabel|rehome|links`; `hl trust remove|apply|mcp`; `hl schedule add|edit|remove`; `hl account add|remove|register|cap|cycle|recover|wake (set; the billing-cycle and cap reads are for the next spec task)`; `hl idle-timeout|idle-background|notice-digest|ignore-cache-expiry (set)`; `hl dev-mode`; `hl device pair|list|revoke (device.pair, devices.list, device.revoke: the gateway's socket, from the founder's terminal)`; `hl service ...`
- **Needs a decision:** `hl spawn (a founder's act; it calls session.start, and agent.create serves it to the founder's apps)`; `hl agent register (a roster change, founder)`
- **Not daemon methods:** `hl init`; `hl login`; `hl launch`; `hl doctor`; `hl hooks`; `hl go`; `hl sandbox-repo`; `hl install-hooks`; `hl backup`; `hl venv`; `hl bridge`; `hl top`; `hl watch`; `hl guide`; `hl withdraw`; `hl memory migrate`; `hl task note (suppressed)`

The plan's earlier `requests` topic is replaced by `chat` (messages and landing state) and `inbox` (decisions); `usage` by `accounts`. The `hr` topic: hr.raised: a proposal was raised (state open); one event per proposal, once. hr.settled: a proposal left open or applying, or its apply ran again: it was decided, withdrawn, turned applying, or its apply failed; sent for every outcome, a retry of an apply_failed one that fails again included (state apply_failed, a new error); the Proposal carries the new state and `settled`. The event's `agent` is the proposal's agent (null for a bundle), `title` its title, `target` {screen: hr, id: the proposal's id as text}, and `data` {proposal: the whole Proposal}, so a client updates its list and its count without a refetch; a client that missed events refetches hr.list. What hr.list serves, and in which order: see its description. An edit of a draft in place (the file is writable by its author) sends no event: a card learns of it from the refusal proposal_changed. The `notifications` topic's task_done: `agent` is the agent that closed the task, `data.task` its id, `data.task_title` its title and `data.result` the first non-blank line of its done evidence, trimmed, cut at 140 characters; each of the last two is absent when the daemon has none. A bell row draws the agent, task_title, result and `at` from the event alone, never fetching the task; `title` stays a plain line for a client that reads none of them.

## Topics

| Topic | Event types (data) | Snapshot method |
|---|---|---|
| `status` | `status.changed` (DaemonStatus) | `daemon.status` |
| `accounts` | `account.changed` (Account) | `accounts.list` |
| `agents` | `agent.changed` (AgentView), `agent.removed` ({name}), `agent.control` (TimelineEntry) | `agents.list` |
| `inbox` | `inbox.added` (InboxItem), `inbox.changed` (InboxItem) | `inbox.list` |
| `tasks` | `task.changed` (Task) | `tasks.list` |
| `notifications` | `task_done` ({task, task_title?, result?}), `prompt_asked` (Prompt), `stall` ({agent}), `account_out` ({account}) | `events.feed` |
| `chat` | `chat.message` (ChatMessage), `chat.transcript` (TranscriptItem), `chat.working` (Working) | `chat.transcript` |
| `operations` | `operation.progress` (Operation) | `operation.get` |
| `devices` | `device.paired` (Device), `device.revoked` ({device}) | `devices.list` |
| `hr` | `hr.raised` ({proposal}), `hr.settled` ({proposal}) | `hr.list` |
| `transcript` | `transcript.event` (TranscriptEvent) | `transcript.events` |
| `prefs` | `prefs.changed` (Prefs) | `prefs.get` |

## State words

AgentView.state is one of these lower-case words. The set is open: a client treats a word it does not know as idle and keeps working. Upper case is never sent: the daemon's internal states (IDLE, QUEUED, RUNNING, DONE, HANDOFF, DEGRADED, PARKED, THROTTLED, STALLED) and the view words its screens use (PENDING, MOVING, DIALOG, FATAL) are mapped to these before they leave it. The mapping is made from the source facts (the state row, its wake time, the screen), never by reading state_detail text.

| Word | Meaning |
|---|---|
| `running` | A turn is running (a hand-off in progress counts: moving is working). |
| `idle` | At its prompt with nothing to do, or nothing pending for it (a park with no work behind it, a finished one). state_detail may say what it waits on (a child task, background work). |
| `queued` | Has work and will start at the daemon's next tick, with no time to wait for: a restart between two sessions (`handoff.phase` starting) reads queued, not parked. state_detail may say why it is not running yet (a restart retry). |
| `starting` | A terminal agent's launch in progress: hl launch kept the session start, no hook of that session, no session end and no failed wake has come since, and its pane runs something other than its shell. A launch that died is back at the shell, so it never reads as starting. Never hides a running turn, a dialog or a stall. |
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
| a terminal launch in progress, before its session's first hook, with claude and not the shell in its pane (checked over idle, queued and no_terminal) | `starting` |
| PARKED, THROTTLED, DEGRADED with a wake time (PENDING with a clock); MOVING | `parked` |
| DIALOG (an open prompt or modal) | `waiting` |
| STALLED (FATAL) | `stalled` |
| no state row or no terminal, and nothing waiting for it (checked before IDLE) | `no_terminal` |

PENDING is two words, by what it waits for: `queued` when nothing but the next tick stands between it and a start, `parked` when it waits for a time. A client draws both as waiting its turn, and the clock in state_detail tells them apart. MOVING is `parked` with state_detail `moving`.

## Status

Every agent (AgentView.status) and every task (Task.status) carries one status: a state, the reason for it, and the line a client shows as it is. Both lists are closed for the daemon and open for a client: a word it does not know is drawn from `text`. The daemon fills it from the facts it acts on (the state row, the wake gate, the account it would wake on, the board), never from free text, and never sends a bare queued or an unknown where it knows the cause. state, state_detail, hold_reason, block_reason and waiting stay until the apps read status.

| State | Meaning |
|---|---|
| `preparing` | Has work and the daemon starts or sends it at its next tick; nothing stands in the way. Reasons: work, restart, retry. |
| `starting` | A launch is in progress (agents). Reason: launch. |
| `running` | Working: a turn runs, a hand-off is moving, background work runs, or a task is with an owner that has it. Reasons: turn, watch, handoff, background. |
| `waiting` | Held by something the daemon names: a person, another task, the owner, a clock, an account move, the machine's load, an account's pace. Reasons: you, opener, task, owner, clock, move, load, pace. |
| `stopped` | Will not go on by itself: no account has room (until the reset when it is known) or a person must unstall it. Reasons: wall, cap, stalled. |
| `idle` | At its prompt with nothing to do (agents). Reason: none. |
| `off` | Registered with no terminal and nothing waiting for it (agents). Reason: none. |
| `done` | Closed (tasks). Reasons: done (its owner finished it), discarded. |

| Reason | Meaning |
|---|---|
| `work` | Work waits for it and nothing is in the way: the next tick types or starts it. |
| `restart` | Between two sessions of a restart; the next one begins at the next tick. |
| `retry` | A failed start is retried; text says which attempt. |
| `launch` | Its terminal is starting. |
| `turn` | A turn is running (agents); the owner has the task (tasks). |
| `watch` | A gate watches outside state; text names it ("watching PR #412 CI"). |
| `handoff` | A hand-off to a new session is moving. |
| `background` | Background work runs while the agent sits at its prompt. |
| `you` | A person must answer: a dialog or prompt on the agent, an open request to the founder on the task, or a task the founder owns. |
| `opener` | A draft: waits on its opener to queue it. |
| `task` | Waits on other tasks; tasks lists them. |
| `owner` | Waits on its owner, busy with something text names. |
| `clock` | Waits for a time: until says when. |
| `move` | An account move is settling. |
| `load` | The machine is over a load limit (host_active_cap, load_per_core_pct, ram_free_min_pct): the daemon starts none of its work until it is under, by itself; text names the limit that tripped. agent.force_start lets it go once. A message the person sends (the founder's or a paired device's) is never held by it: its agent wakes at once, and load_limit_passed is recorded in agent.timeline within 10 s of the send; the limit holds only agents' work and the daemon's own, and for at most 10 minutes: work held that long goes once, and load_limit_passed (by: ceiling) is recorded. A message held by the load is not a stuck delivery: no heal step or stuck card is made for it. |
| `pace` | Every account it may run on has no free slot only because of its weekly pace (Account.pace; without pacing one would have room): the daemon starts it when a slot frees or a line catches up, by itself, and a start goes to the account furthest behind its line. text names the account and how far ahead it is; until is the earliest back_at, null when not known. A turn a person starts (a message a person sends, a person's own start or wake) and a restart are never held. agent.force_start lets it go once. |
| `wall` | The account it would run on is at a usage limit; until is when the window rolls over, null when not known. |
| `cap` | No account has a free slot; it starts when a running turn ends. |
| `stalled` | Stopped until a person unstalls it; text says why it stopped. |
| `none` | Nothing to say beyond the state. |
| `done` | Its owner finished it. |
| `discarded` | Thrown away; text says why. |

## Notifications

- `event`: Sent after subscribe, for the subscribed topics, in cursor order. Params: `Event`
- `gap`: retention: the resume cannot be served (the cursor is older than the daemon keeps, or more than 5000 of the subscribed events behind); refetch the topic's snapshot; the connection stays open and the cursor is the newest. slow_client: more than 1000 live events were waiting to be written to this client; the daemon sends this and then closes the connection. A resume up to 5000 behind is replayed in full and does not count toward the 1000. The daemon sets no `topic` in v1. Params: `{   reason: "retention" | "slow_client";   cursor: number;   topic?: Topic; }`
- `frame`: Text and styled attaches only. full:true carries every line and is what a slow client gets instead of a backlog. A frame may carry `changed: []` for a cursor move alone. Params: `{   terminal: string;   seq: number;   full: boolean;   lines?: Row[];   changed?: {     y: number;     row: Row;   }[];   cursor: Cursor;   /** Present on a `styled` attach's frames and absent on a text one's, so a connection that watches one terminal in both formats can tell which attach a frame belongs to. */   format?: "styled"; }`
- `resize`: Text attaches only; a bytes attach gets a snapshot notice instead. Params: `{   terminal: string;   seq: number;   cols: number;   rows: number; }`
- `marker`: The daemon typed, or a control call happened, at this point in the stream. Params: `{   terminal: string;   seq: number;   at: Timestamp;   kind: "daemon_typed" | "control";   actor: Actor | null;   text?: string;   /** The attach this notice belongs to, whose counter `seq` is: a terminal watched in both formats is sent each of these twice. Additive: a client that does not know it ignores it. */   format: "text" | "bytes" | "styled"; }`
- `holder`: Sent to attached clients whenever the holders change (take, release, the idle hold timeout, the daemon taking over for a send). agent.changed carries the same in AgentView.input_holders. Params: `{   terminal: string;   seq: number;   holders: InputHolder[];   /** The attach this notice belongs to, whose counter `seq` is: a terminal watched in both formats is sent each of these twice. Additive: a client that does not know it ignores it. */   format: "text" | "bytes" | "styled"; }`
- `output`: Bytes attaches only: the pane's output in order, at most one notice per 50 ms and 64 KiB decoded each. A notice may end inside a multi-byte character or an escape sequence. Params: `{   terminal: string;   seq: number;   /** Base64 of what the program wrote. */   data: string; }`
- `snapshot`: Bytes attaches only: the screen again, to be repainted as after an attach (reset, resize to cols by rows, write data), because the terminal changed size or the client fell behind. Everything the stream would have said before it is dropped; output follows with the next seq. Params: `{   terminal: string;   seq: number;   reason: "resize" | "slow_client";   cols: number;   rows: number;   /** Base64, as in the attach snapshot. */   data: string;   alt_screen: boolean;   cursor: {     x: number;     y: number;   };   cursor_visible: boolean; }`

## Terminal channel

Format `text` sends plain text rows (frame notices). Format `styled` sends the same rows and frames with each row's style runs (Row.spans), for a host that has no byte stream or a client that cannot run a terminal emulator (below). Format `bytes` (below) sends the pane's own escape-coded output, which carries the colour. One poller per terminal, at most ~5 reads a second however many clients; it stops when the last client leaves. The input holders are a list (TerminalSnapshot.holders, AgentView.input_holders) with a `holder` notice on change; a holder that has not typed for the input_hold_idle_s setting loses the hold.

### Format bytes

- **Format.** `terminal.attach` and `terminal.history` take `format`: `text` (the default; what they send is unchanged) or `bytes`. The format belongs to the attach, so two clients may watch one terminal in different formats. `hello.capabilities.terminal_format` lists the formats the daemon serves at all and `terminal_hosts` maps each host it has to its formats, e.g. `{tmux: [text, bytes]}`. A bytes request for a terminal whose host cannot serve it is refused, gate `unsupported`, and nothing is attached; a client then asks for text. A `format` that is neither word is bad_request naming `format`. A connection has at most one attach per terminal and format (see terminal.attach); detach takes an optional `format`.
- **Encoding.** Bytes travel as base64 (standard alphabet, padded) in JSON strings on the same transport as every other message: no binary frames. They need not be valid UTF-8, and a notice may end inside a character or an escape sequence, so a client writes each as bytes to a terminal that keeps its own parser state.
- **Snapshot.** `data` is written unchanged to a freshly reset terminal of `cols` by `rows` (the client resizes, resets, writes, in one step). It begins with `ESC [ ? 1049 h` when `alt_screen` is true, then paints the visible rows with their SGR codes, each row ending in a reset and rows separated by CR LF, never a bare LF; then sets the modes the pane has on that change what a client sends (application cursor keys, bracketed paste, mouse reporting and its SGR encoding) as far as the host reports them; it ends by placing the cursor at `cursor` and showing or hiding it as `cursor_visible` says. It is at most 1 MiB decoded: a screen whose coded form is larger is sent with its styling left out, never refused. Later transitions of the alternate screen are in the stream; the flag is for the snapshot alone.
- **Sequence.** One counter per attach, shared by all its notices (`output`, `snapshot`, `marker`, `holder`), and it goes up by exactly 1 with each. The snapshot's `seq` is the last notice its screen includes: the daemon takes the screen and starts the stream together, so the first notice after the reply has `seq` + 1 and no byte is lost or repeated between them. A client ignores a notice whose seq is not above the last it applied and re-attaches on a hole. The counters of different attaches, text ones included, are unrelated: a connection that watches one terminal in both formats has two counters, and attaching the same terminal and format again replaces that attach and starts a new one at its snapshot's seq. There is no resume: after a reconnect the client attaches again and repaints from the new snapshot, and what it missed is not replayed.
- **Slow client.** Output is never dropped from the middle. When one attach's unsent output reaches 1 MiB decoded, the daemon discards all of it and sends one `snapshot` notice, reason `slow_client`, with a seq above everything discarded; output follows it. Counting bytes, not seconds, is the whole rule. A connection that falls behind on events past the gap rule is closed with gap `slow_client` as before and attaches again.
- **History.** Lines come as strings with their SGR codes, as the host keeps them: one physical row each (the row as the pane wrapped it, never joined), starting at default attributes and ending in a reset, with no line break inside. `history` on attach and `limit` on `terminal.history` count lines as in text (default 100, at most 2000); a page is trimmed to 1 MiB and says `more`. Only the normal screen has scrollback: while `alt_screen` is true the snapshot's history is empty.
- **Input.** `terminal.input` carries keystrokes and pasted bytes as base64; its gates and recording are on the method.
- **Resize.** See `terminal.resize` for who may resize. A bytes attach is told of any size change, whoever caused it and the restore included, by a `snapshot` notice, reason `resize`, with the new cols and rows, and gets no `resize` notice: the client repaints rather than reflow, as tmux's reflow is not xterm's.
- **Markers.** `marker` and `holder` notices come to a bytes attach as to a text one, in the one counter: a `marker` stands where the daemon typed, before the output that echoes it. Each carries `format`, the attach it was sent to, since a terminal watched in both formats gets each notice once per attach with that attach's counter.

### Format styled

- **Format.** `terminal.attach` and `terminal.history` take `format: styled`. It is the text format with colour: the snapshot has `format` `styled`, every Row in `lines`, `history` and `terminal.history` pages may carry `spans`, and every `frame` notice of the attach carries `format` `styled`. Frames, seq, `full`, `changed`, `cursor` and `resize` are as for text; the `holder` and `marker` notices are as for text too, and carry `format` `styled` on this attach so a client can tell its counter from a text attach's of the same terminal, and `text` is exactly what a text attach would send. Offered the way `bytes` is: `hello.capabilities.terminal_format` lists it and `terminal_hosts` maps each host to its formats, e.g. `{tmux: [text, bytes, styled]}`; a styled request for a terminal whose host cannot serve it is refused, gate `unsupported`, and nothing is attached; a client then asks for text. The format belongs to the attach, so a text and a styled attach of one terminal on one connection have unrelated counters. `terminal.detach` takes `styled` as it takes `bytes`.
- **When.** A host serves `styled` when its rendered-screen read gives the attributes of each cell, not only the characters. It is meant for a host that has no byte stream but does render the screen it reads. The phone app has no terminal emulator and uses the format on any host that serves it. A host whose read has no attributes stays `text`: the daemon never invents styles, and never sends `spans` on a terminal that has none to give.
- **Spans.** A run is `[from, to)` over the row's characters counted in Unicode code points of `text` (not UTF-16 units, not bytes; a client in JavaScript converts with `Array.from`), and may extend past the end of `text` for blank cells that have a background: past the end, each blank cell counts as one, so `to` is at most len(`text`) + (`cols` minus the display width of `text`), where a wide character (CJK) is one code point and two cells. A run boundary never falls inside a cell: a base character stays with its combining code points, and a wide character is one unit. Runs are sorted, never overlap, are never empty and are sent only for cells whose style is not the default; a cell no run covers is drawn default. Two neighbouring cells with the same style are one run. Style is `SpanStyle`: `fg`, `bg` (a palette index 0-255 or `#rrggbb`) and the flags `bold`, `dim`, `italic`, `underline`, `inverse`, `strike`, each left out when off. Palette indexes 0-15 are the client's theme colours, so a dark and a light client each draw them in their own tokens; the daemon does not resolve a colour it was given as an index.
- **Cursor.** A frame's `cursor` (text and styled attaches; a bytes attach's is in cells) is `{x, y}` or null: null when the cursor is hidden or above the frame. `x` counts code points of the row's `text`, as spans do (a host that reads cells converts; a wide character is one), and may run past the end of `text` when the cursor sits in blank cells, each counting one. `y` is a row of the frame's `lines`, from 0, and may be len(lines) or more when the cursor is on a blank row the frame drops. A frame may carry `changed: []` for a cursor move alone.
- **Size.** A row's spans are what its colours need and no more; `terminal.history` pages and the 1 MiB trim of a text page apply to the JSON as sent, spans included, and `more` says when it was trimmed. A frame for a screen of a few hundred runs is a few KiB; the slow-client rule of text is unchanged (`full: true`).
- **Input.** `styled` is read-only like text: keys go by `terminal.input` where the host supports bytes in, and by `session.type` and `session.keys` as before.
- **Not in it.** No hyperlink, image, blink or font-weight beyond bold, no wide-character marker beyond what `text` has, no mouse or screen-mode state: a client that needs the real terminal asks for `bytes`.

## Transcript stream

- **What.** The per-agent stream a view draws for a structured session (the CLI's frames reduced to what a view needs), in place of a terminal. Topic `transcript`, event type `transcript.event`, data TranscriptEvent; events carry `agent`, so subscribe's `agent` filters. `hello.capabilities.transcript` says the daemon serves it; `AgentView.structured` says which agents it serves it for (absent: a terminal agent, whose transcript.events is refused, gate unsupported, and whose terminal a client shows). The topic needs member, as transcript.events does: subscribe naming it from an agent session is refused, gate role.
- **Kinds.** turn_start (a message began a turn: uuid, sender, text), text (one whole assistant text block), tool_use (id, tool, input), tool_result (tool_use_id, ok, text cut at 64 KiB), permission (a person is asked: a Prompt), permission_answer (allowed or denied, and by whom), turn_end (reason, cost_usd, duration_ms, usage), error (code, text). The order within a turn: turn_start, then text, tool_use, permission, permission_answer and tool_result as they happen, then turn_end. A tool_result follows its tool_use, not necessarily next. Open set: a client skips a kind it does not know.
- **Permission.** A tool call that needs a person is a `permission` event whose `prompt` is an ordinary Prompt: kind permission, id = the tool call's id, options `allow` and `deny`, tool, command and reason as for any prompt. The card calls prompt.answer with choice allow or deny, and prompt.list holds it while it is open (it is also in the chat view as a prompt item and in the inbox, as today). The `permission_answer` event ends it, whoever answered: `by` person (prompt.answer), policy (a rule decided; no `permission` came before), cancel (the CLI withdrew it) or closed (the session closed or no handler answered: deny). A client that reconnects and sees a permission with no answer after it asks prompt.list before showing buttons.
- **Sequence.** `seq` is per agent, starts at 1, goes up by exactly 1 with each event, continues across sessions of the agent (the `session` field changes on a fresh start, not on a resume) and is never reused. Events are final: nothing is changed after it is sent.
- **Resume.** The connection's cursor works as for every topic: subscribe with `since` replays what was missed, up to the usual 5000 events, in full. A client that has no cursor, or gets `gap`, or sees a seq that is not last + 1, reads transcript.events: first load with no since_seq (the newest page), older pages with before_seq, the missed ones with since_seq = the last seq it applied. To start without a race: subscribe first, then read, then apply stream events above the read's last_seq and drop those at or below the last it holds. `gap: true` in the read means the daemon no longer keeps the events between; the client shows the break and goes on from first_seq.
- **Send.** A message goes in by chat.send with the client's own request_id as its operation id (see x-delivery); the daemon derives the message uuid from it, so the `turn_start` event for a message that begins a turn carries the uuid. A message that joined a running turn has no turn_start. chat.messages has the message under the same operation, with its receipt.
- **Chat.** chat.transcript is unchanged and stays the chat view's feed: a `text` event is also a reply item and a `permission` also a prompt item (the event's `item` is that item's id, so a view showing both draws it once); tool_use, tool_result, turn_start, turn_end and error have no chat item, and chat.transcript has still no tool item. The task_event and session items exist only in chat.transcript. A view that renders the transcript uses this stream and ignores chat.transcript's reply and prompt items for the same agent.
- **Not in v1.** Partial text deltas, thinking blocks, images, and the edit diff of a tool. A tool's input is whatever the model sent.

## Delivery to a structured agent

- **What.** How a message reaches a structured agent, and what the composer shows. One path serves a person, an agent, the daemon's own notices and a task; the apps use it through chat.send.
- **Operation.** The caller's request_id is the operation id. The daemon derives the message uuid from it, records the operation before writing, and answers a repeat with the recorded operation without writing again. A client makes a new request_id for each message it means to send and keeps it across retries of that one message.
- **Receipt.** Receipt.state: queued (held behind a running turn), sent (written to the CLI), confirmed (the CLI echoed the uuid), unknown (see crash rule). The order is queued, sent, confirmed; unknown follows sent. `accepted` is never `delivered`: a caller that needs delivery waits for confirmed, with its own deadline. There is no `published` state: a message that joins a running turn has no turn_start, so `seq` on the receipt is optional and best effort.
- **Repeat.** The same request_id and params returns {accepted, replayed: true, request, receipt} with the receipt as it is now, so a retry after a lost reply learns whether the message was echoed. The same request_id with other params is bad_request. This is the one write whose replay is not the first reply frozen.
- **Delivery.** chat.send `delivery`: queue (default) or now. Queue while a turn runs is held by the daemon, which persists it, and is written when the CLI reports the turn over. Now is written at once and joins the running turn. With nothing running both are written at once and the receipt is sent.
- **Crash.** If the daemon or the CLI ends with an operation sent and not confirmed, the daemon does not write it again: the operation is unknown, the sender is told (the chat event), and it stays unknown. A queued operation survives and is written after the restart. A person who sees unknown may chat.send the text again under a NEW request_id (their call: the CLI may have run it); chat.cancel keeps its meaning for a delivered message. Nothing else changes an unknown operation.
- **Queued.** chat.send_now writes a queued operation now, with priority now (receipt sent). chat.cancel withdraws it without confirm. Both are refused (gate state) for any other receipt; there is no chat.withdraw.
- **View.** ChatMessage.receipt carries the receipt, and landing follows it: queued is queued, sent and unknown are typed (sending_reason says unknown), confirmed is delivered. Each change sends the usual chat.message event. A terminal agent's message has no receipt.
- **Terminal.** An agent whose AgentView.structured is not true accepts `delivery` and ignores it, and its result and messages have no receipt.

## Driving a structured agent

- **What.** How the apps drive and watch an agent that AgentView.structured marks. It has no terminal: everything below is a chat, session or prompt call.
- **Watch.** transcript.events and the transcript topic show what it did; chat.transcript and chat.messages draw the conversation and each message's receipt. Both stay readable while its session is closed.
- **Detail.** agent.get: terminal null, session_open true while its session is live, input_holders [].
- **Controls.** session.start, wake, restart, park, stop (exit and close are the same; kill is for a person) and interrupt (refused, gate state, when no turn runs); prompt.answer takes allow or deny; chat.send, chat.send_now and chat.cancel as in Delivery.
- **Never.** A client does not send session.input, session.keys, session.type, input.take, input.release or terminal.attach for a structured agent, and shows no terminal tab, holder, key strip or Take control. The Go daemon does not serve the first five at all; a daemon that does refuses a structured agent (gate unsupported). terminal.attach with agent is not_found (terminal): it has no terminal.

## Legacy verbs

| Legacy verb | Method |
|---|---|
| `agent_set` | `agent.set` |
| `message` | none |
| `say` | `say` |
| `push` | none |
| `notify_cancelled` | none |
| `wake` | `session.wake` |
| `close` | `session.stop{mode:close}` |
| `restart` | `session.restart` |
| `park` | `session.park` |
| `fold` | `agent.fold` |
| `move` | `agent.move` |
| `start` | `session.start` |
| `answer_block` | `prompt.answer` |
| `trust` | none |
