<!-- Generated from contract/spec/ by contract/generate.py; do not edit. -->
# terminal methods (protocol 1)

A method marked write takes a required `request_id`. Framing, roles, errors and gates are in [protocol.md](../protocol.md); field types in `protocol.ts`.

| Method | Role | Write | Params | Result | Errors | Gates |
|---|---|---|---|---|---|---|
| `terminal.attach` | member |  | `agent?`, `terminal?`, `history?`, `format?` | `TerminalSnapshot \| TerminalBytesSnapshot` | `not_found`, `bad_request`, `refused` | unsupported |
| `terminal.history` | member |  | `terminal`, `before`, `limit`, `format?` | `HistoryPage \| HistoryBytesPage` | `not_found`, `refused`, `bad_request` | unsupported |
| `terminal.resize` | member | yes | `terminal`, `cols`, `rows`, `request_id` | `Accepted` | `refused`, `not_found` | role, input_holder |
| `terminal.input` | member | yes | `terminal`, `data`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` | role, terminal_lock, input_holder, unsupported |
| `terminal.close` | member | yes | `agent`, `request_id` | `Accepted` | `refused`, `not_found` | role, terminal_lock, input_holder, state |
| `terminal.open` | member |  | `agent` | `TerminalOpening` | `not_found`, `bad_request`, `refused` | role, unsupported |
| `shell.open` | member | yes | `agent`, `command?`, `block?`, `request_id` | `ShellOpening` | `refused`, `not_found`, `bad_request` | unsupported, state |
| `shell.close` | member | yes | `terminal`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` | unsupported, state |
| `terminal.detach` | member |  | `terminal`, `format?` | `object` | `not_found`, `bad_request` |  |

### `terminal.attach`

Subscribe to one terminal; replies with a snapshot, then pushes notices. `format` chooses text rows (frame notices), text rows with style runs (frame notices, `styled`) or bytes (output notices). A connection has at most one attach per terminal and format: attaching a terminal and format the connection already watches replaces that attach, accepted and never refused (no notice of the old one follows the reply; the reply is a fresh snapshot, and the new attach's counter starts at its seq). An attach in the other format is independent, with its own counter. A client that wants more than one view of one terminal and format shares one attach. With agent, an agent that has no terminal, a structured agent among them, is not_found (terminal). With terminal, a handle no agent has is watched while the host lists it (it has no input holder, so terminal.input and terminal.resize refuse it); one the host does not list is not_found.

### `terminal.history`

Page scrollback; absolute line indexes.

### `terminal.resize`

Resize, never under 80 by 24: the input holder's while there is one, any attached connection's while there is none. While a person holds the input, only they resize, and the size from before their first resize is restored when they leave; anyone else is refused, gate `input_holder`, and scales its own view. While nobody holds it (the daemon's own hold included), any connection that has the terminal attached, in any format, may resize it to at least 80 columns by 24 rows, and the last resize wins; when the last attach goes and nobody holds the input, the size from before the first of these resizes is restored. From a connection that has not attached it, the resize is refused, gate `input_holder`. Under 80 by 24 any resize is refused, the holder's included, gate `input_holder`, and the caller keeps its grid and scales its own view: the daemon reads the screen line by line, and narrower, Claude Code wraps a dialog's footer and options so the daemon no longer recognises the dialog. Every attach is told of the change: a `resize` notice for text and styled, a `snapshot` notice, reason `resize`, for bytes. A handle no agent has is never resized.

### `terminal.input`

Raw keystrokes or pasted bytes into the terminal, from the input holder only. For a client that is a terminal (xterm.js), as session.type is for a person typing: the same gates, and it passes the wall and an open dialog as session.type and session.keys do (the person sees the screen; session.input keeps both gates). Unlike them it is refused while the daemon holds the input, since a stream of keystrokes beside the daemon's own typing would corrupt both: take the input first. A terminal with no holder, a login terminal, refuses it too. A shell shell.open started has no holder and takes it from any caller. A pane showing a shell prompt takes it, as with session.type. Calls on one connection are applied in the order they were read, however many are waiting. It is recorded as an event of the agent (kind type) whose actor is the caller, a paired device by its id, with the byte count and never the bytes. Terminal hosts that cannot send keys refuse with gate unsupported.

### `terminal.close`

Close the host's terminal to free it (the web UI's Close terminal). Different from session.stop close, which ends the session and keeps the terminal: refused while a session runs.

### `terminal.open`

Where an agent's terminal is and how to attach to it, for the one button that opens it when the agent needs you. Answers where the agent's own terminal is: the host (`tmux`), its handle, and `attach`, the command a client on the daemon's machine runs to see and answer it (`argv`, null when the host has no command; the client then uses `host` and `terminal`). It types nothing and answers nothing; the person at the terminal answers the dialog, which is the point (see x-dialogs). `focused` is true when the host was also asked to bring the terminal to the front (tmux select-window; best effort, false when it could not). Callers: the founder and a paired device (member); an agent is refused, gate role. An agent that is not registered is not_found (agent); one with no terminal, a structured agent among them, a closed one (it keeps the handle it had) and one whose terminal the host no longer lists are not_found (terminal). A device on another machine than the daemon's gets the same answer; `attach` names a local socket, so it is useful only where the daemon runs, and `local` says whether the caller is there (true for the unix socket, false over the network).

### `shell.open`

Open a new plain shell in an agent's worktree (the New tab menu's Terminal), or run a command there (a command card's Run). Starts the user's login shell in a new window of the terminal host, in the agent's worktree, and answers its handle; attach it with terminal.attach (terminal), type at it with terminal.input and size it with terminal.resize. It is not the agent's terminal and touches nothing of the agent's: no input holder and no terminal lock stand between a person and it, terminal.resize has no 80 by 24 floor for it, and the daemon never reads its screen. Several may be open beside one agent, at most 8 (refused, gate `state`). It lives until shell.close, the shell exits, or the host's server ends; the daemon does not close it when the agent's session ends. Not_found names the agent, or the worktree when the agent has none on disk. Refused with gate `unsupported` on a host that cannot open one. With `command` (and `block`) the window runs that command instead of a shell and stays after it ends: its exit status and end time are the terminal host's own, read by the daemon and told in the `runs` of the message `block` names (ChatMessage.runs, a chat.message event at start and at end). `command` without `block` or `block` without `command` is bad_request naming the other. A command run counts toward the 8 shells; closing its window before it ends makes its run `stopped`. Runs are kept in the daemon's meta table; a run whose message is gone is not shown.

### `shell.close`

Close a shell shell.open started. Closes the shell's window. A handle that is not a shell (an agent's own terminal included) is not_found, so this cannot end an agent's session.

### `terminal.detach`

Stop receiving this terminal, in one format or in all. Never stops the agent. not_found is for a terminal handle that does not exist. Closing the connection ends every attach.

