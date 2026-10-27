<!-- Generated from contract/spec/ by contract/generate.py; do not edit. -->
# agents methods (protocol 1)

A method marked write takes a required `request_id`. Framing, roles, errors and gates are in [protocol.md](../protocol.md); field types in `protocol.ts`.

| Method | Role | Write | Params | Result | Errors | Gates |
|---|---|---|---|---|---|---|
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
| `agent.strip` | member |  | `agent` | `{agent, model, account, ctx_pct, ctx_used, ctx_limit, cache_left_s, screen_word, screen_detail} & ReadCursor` | `not_found`, `bad_request` |  |
| `progress.get` | agent (only agent) |  | none | `{note}` |  |  |
| `say` | agent | yes | `target`, `body`, `task?`, `no_task?`, `parent?`, `files?`, `request_id` | `Accepted` | `refused`, `bad_request` |  |
| `agent.triggers` | agent |  | `agent?` | `{agents}` |  |  |

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

### `agent.strip`

The line under an agent's chat header: model, account, context, prompt-cache life and the screen word. Context figures come from the usage snapshot of the agent's own session, so they are null for an agent with no account or no snapshot.

### `progress.get`

The caller's progress note: the one on its request in progress, else on the task it holds, else on the agent (what hl progress shows with no note).

### `say`

A message typed at once; needs no answer, starts no work.

### `agent.triggers`

What the daemon would decide for an agent right now (hl triggers). One row per agent the daemon knows, in name order; with `agent`, that agent's row alone, and an agent the home does not have is not_found. `fired` is whether the next tick would hand it off; `reason` is the trigger's reason, or for an agent whose triggers do not apply, what holds it (a walled wake, its state until its wake, or no session).

