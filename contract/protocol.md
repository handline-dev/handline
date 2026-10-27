<!-- Generated from contract/spec/ by contract/generate.py; do not edit. -->
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

One file per group, generated from `spec/<group>.json`; field types are in `protocol.ts`.

- [core](gen/core.md): 15 methods
- [accounts](gen/accounts.md): 12 methods
- [agents](gen/agents.md): 30 methods
- [chat](gen/chat.md): 25 methods
- [devices](gen/devices.md): 4 methods
- [repos](gen/repos.md): 6 methods
- [tasks](gen/tasks.md): 36 methods
- [team](gen/team.md): 14 methods
- [terminal](gen/terminal.md): 9 methods

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
| `notifications` | `task_done` ({task, task_title?, result?}), `prompt_asked` (Prompt), `stall` ({agent}), `account_out` ({account}), `disk_low` ({free_bytes, total_bytes}) | `events.feed` |
| `chat` | `chat.message` (ChatMessage), `chat.transcript` (TranscriptItem), `chat.working` (Working) | `chat.transcript` |
| `operations` | `operation.progress` (Operation) | `operation.get` |
| `devices` | `device.paired` (Device), `device.revoked` ({device}) | `devices.list` |
| `hr` | `hr.raised` ({proposal}), `hr.settled` ({proposal}) | `hr.list` |
| `transcript` | `transcript.event` (TranscriptEvent) | `transcript.events` |
| `prefs` | `prefs.changed` (Prefs) | `prefs.get` |
| `requests` | `request.changed` (RequestRow) | `requests.list` |

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
| `pace` | Every account it may run on has no free slot only because of its weekly pace (Account.pace; without pacing one would have room), or none would last to its 5-hour reset with one more agent on it: the daemon starts it when a slot frees, a line catches up or a 5-hour window starts over, by itself, and a start goes to the account furthest behind its line. text names the account and how far ahead it is for the pace, and names no account for the 5-hour limit; until is the earliest back_at, or the earliest 5-hour reset, null when not known. A turn a person starts (a message a person sends, a person's own start or wake) and a restart are never held. agent.force_start lets it go once. |
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
- **Restart.** From the moment a handoff, move or restart of an agent is decided (a trigger deferred behind a running turn included) until its session is closed (a clear owed included), no message is typed at the old session, a person's included: it is held (queued, sending_reason "held while it restarts; the new session gets it") and told to the new session first, headed by the line "(This arrived while you were restarting for a handoff.)". A handoff left open holds for 10 minutes at most. interrupt and the start verbs are not messages and act at once.
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
