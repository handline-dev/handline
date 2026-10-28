<!-- Generated from contract/spec/ by contract/generate.py; do not edit. -->
# core methods (protocol 1)

A method marked write takes a required `request_id`. Framing, roles, errors and gates are in [protocol.md](../protocol.md); field types in `protocol.ts`.

| Method | Role | Write | Params | Result | Errors | Gates |
|---|---|---|---|---|---|---|
| `hello` | anonymous |  | `client`, `protocol`, `token?`, `key?`, `client_build?` | `{protocol, min_client, daemon, version, exe, schema, role, caller, capabilities, transport, methods}` | `refused`, `bad_request` |  |
| `whoami` | agent |  | none | `{caller, role, methods}` |  |  |
| `subscribe` | agent |  | `topics`, `since?`, `resume?`, `agent?` | `{cursor}` | `bad_request`, `refused` | role |
| `unsubscribe` | agent |  | `topics` | `object` | `bad_request` |  |
| `daemon.status` | agent |  | none | `DaemonStatus & ReadCursor` |  |  |
| `events.feed` | agent |  | `since?`, `topics?`, `limit?` | `{events, cursor, gap}` | `bad_request` |  |
| `operation.get` | member |  | `operation` | `Operation & ReadCursor` | `not_found` |  |
| `debug.terminals` | member |  | none | `{host, now, panes}` | `refused` | role, unsupported, state |
| `prefs.get` | member (only member, founder) |  | none | `Prefs & ReadCursor` | `refused` |  |
| `prefs.set` | member (only member, founder) | yes | `changes`, `request_id` | `Accepted` | `bad_request`, `refused` |  |
| `hook.record` | agent (only agent) | yes | `agent`, `kind`, `at`, `data`, `request_id` | `Accepted` | `refused` | not_self |
| `memory.status` | agent |  | `agent?` | `{agents}` |  |  |
| `connection.get` | founder |  | none | `ConnectionInfo` |  |  |
| `daemon.upgrade` | founder | yes | `exe`, `sha`, `request_id` | `{from, to, exe}` | `bad_request`, `refused` | build |
| `settings.get` | founder |  | `agent?` | `Settings` | `bad_request`, `not_found` |  |
| `settings.set` | founder | yes | `changes`, `agent?`, `request_id` | `Accepted` | `refused`, `bad_request` |  |

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

### `debug.terminals`

Every pane of the terminal host with what the daemon knows of it, for the hidden debug window. Lists every pane of the daemon's terminal server (tmux), whichever agent has it or none, to find panes kept open past their prompt cache and shells left behind after an agent's session exited. Reads only: the host's pane list, the process table and the daemon's own rows; of the agent's view only cache_until. A host that cannot list its panes is refused, gate unsupported; one that cannot be reached, gate state. Watching a listed pane is terminal.attach by its handle; a pane no agent has takes no input. Callers: the founder and a paired device (member); below that is refused, gate role.

### `prefs.get`

The person's view preferences: the agent list's pins, filter, layout and folds.

### `prefs.set`

Change the person's view preferences: each key given in changes.nav replaces the stored one. A pin, a fold or an order entry naming an agent the daemon does not know, or a name given twice, is bad_request naming the field. Once stored, a prefs.changed event on the prefs topic carries the whole Prefs. agent.retire drops the agent from pins, collapsed and order (and sends prefs.changed); a rename (hr) carries it over in place.

### `hook.record`

A hook records an event; the spool replays it with the same request_id.

### `memory.status`

Per-agent memory against its budget (hl memory status).

### `connection.get`

How this process is reached: its listener, the heavy-run slots and dev mode. Read only.

### `daemon.upgrade`

Restart the daemon onto a given handlined: the app's half of one update replacing app and daemon together. The daemon runs `exe --version` (30 s at most) and refuses, gate `build`, unless the binary starts and prints `sha` (a checkout build prints the sha, a release <version>+<sha>[-dirty]); it then keeps running as it was. A binary that passes: the reply is sent, the tick in hand finishes, the lock and the database are given up as a clean stop gives them up, and the process execs `exe` with its own arguments. The client then sees the connection end and reconnects; the new daemon's hello and daemon.status carry the new version. A retry with the same request_id gets the kept reply and does not restart again. Served on the local socket only: a paired device does not choose the binary the machine runs.

### `settings.get`

Timings, terminal host, trust folders, hooks installed.

### `settings.set`

Change settings: any keys of SettingsValues, globally or as one agent's override.

