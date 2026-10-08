# Structured agent sessions

Status: design for review. Prototype: `go/internal/structured`.

## Decision asked for

Run each agent as a Claude CLI in stream-json mode (the Agent SDK's wire protocol), owned by the daemon, instead of a
Claude Code TUI in a tmux pane that the daemon reads from the screen and types keys into. The founder has decided this
direction; this note says how, what it removes, what it costs, and what is still unproven.

The terminal stays for a person who wants to watch. It is no longer how the daemon talks to an agent.

## What Orca does, and what we take

Read from stablyai/orca (MIT) at `main`:

- `claude-stream-json-connection.ts` starts the CLI through the Agent SDK with a message queue on stdin and an async
  iterator on stdout, a pinned config dir, a scrubbed environment (no inherited `ANTHROPIC_*`), and a close that proves the
  process tree is gone.
- `claude-structured-session-acquisition.ts` gates every frame on the session id of the init frame, publishes the session
  only once the child has a pid and has answered, and treats an early exit as a failed start.
- `claude-structured-options.ts` changes model, permission mode, effort and fast mode on a live session by control request,
  and reads the setting back, because the CLI answers success for some values it then ignores.
- `send-agent-turn.ts` is the one send path for a person and for another agent: an `operationId` the host records, so a
  retry replays the recorded answer; delivery `now` (join the running turn) or `queue` (held until the turn ends); and
  `waitForSendSettlement`, because "accepted" is not "delivered".
- `mailbox-pointer-*.ts` is their fallback for agents that only have a terminal: mail stays in the database, a short
  pointer is typed only when the agent is proven idle, and the agent reads the mail itself.

Orca uses the TypeScript SDK as the transport. We have no TypeScript runtime in the daemon, so we speak the protocol the SDK
speaks. It is documented in the package's `sdk.d.ts` (`@anthropic-ai/claude-agent-sdk` 0.3.293): newline-delimited JSON,
`user` messages in, `control_request` / `control_response` both ways, `system` frames such as `session_state_changed`
(`idle`, `running`, `requires_action`). That is a stable enough surface to implement, with the alpha caveat below.

## The wire, as the prototype uses it

Start: `claude --print --input-format stream-json --output-format stream-json --verbose --replay-user-messages
--permission-prompt-tool stdio [--session-id UUID] [--model M]`, the account's `CLAUDE_CONFIG_DIR` and nothing else from the
daemon's environment but `PATH`, `HOME`, `TMPDIR`, `LANG`.

| Direction | Object | Use |
|---|---|---|
| in | `{type:"control_request", request:{subtype:"initialize"}}` | handshake; no answer in time means the start failed |
| in | `{type:"user", uuid, message:{role:"user", content}, priority?:"now"}` | a message; `priority:"now"` steers it into the running turn |
| in | `control_request` `interrupt` | stop the running turn |
| in | `control_request` `set_model`, `set_permission_mode`, `apply_flag_settings`, `get_settings`, `list_models` | options (not in the prototype) |
| out | `{type:"user", uuid, isReplay:true}` | the CLI echoing a message: the delivery confirmation |
| out | `{type:"system", subtype:"session_state_changed", state}` | whether a turn is running or waits for an answer |
| out | `control_request` `can_use_tool` (`tool_name`, `input`, `tool_use_id`, `decision_reason`) | a permission prompt |
| in | `control_response` `{behavior:"allow", updatedInput}` or `{behavior:"deny", message}` | the answer |
| out | `control_cancel_request` | the CLI withdrew the prompt |
| out | `request_user_dialog` | only for dialog kinds the client declared in `initialize.supportedDialogKinds`; we declare none |
| out | `assistant`, `user` (tool results), `result`, `stream_event` | the transcript, with cost and token usage in `result` |

Pin: Claude CLI **2.1.294** (what this machine runs; the SDK is 0.3.293). Several control types are marked alpha in the SDK.
The daemon runs `claude --version` before a start and refuses another version until someone has run the live checks against
it and moved the pin (`structured.PinnedCLI`, `CheckVersion`).

## The session model

One CLI process per running agent, kept alive across turns, owned by the daemon.

- **Identity.** The daemon picks the session id (`--session-id`, a UUID) and stores it with the agent. A restart after the
  process dies is `--resume <id>`; a deliberate fresh start is a new id. The id replaces "which tmux pane".
- **State** comes from the CLI, not from reading a screen: `running` while a turn runs, `requires_action` while a permission
  prompt waits, `idle` between turns. The daemon's existing agent states (RUNNING, IDLE, BLOCKED/WAITING, STALLED, PARKED)
  are derived from this plus the process being alive. The prototype ignores an `idle` that arrives before the echo of a
  message it just wrote, because that `idle` ends the previous turn.
- **Liveness** is the process: exited, killed, or never answered `initialize`. A stalled agent is a turn that has run longer
  than a limit with no frames; it is interrupted by control request rather than keys.
- **Close** is closing stdin, then a kill after a grace period (the prototype uses 3 s). Orca also proves the descendants
  exited; we should do the same on macOS before the daemon reports a restart done (not in the prototype).
- **Options** (model, effort, permission mode) are control requests on the live session, each read back (`get_settings`)
  before the daemon says it applied them.
- **Cost and usage** arrive in `result` frames. This can replace parts of the usage collection that read transcripts or
  screens; the plan for G2 does not change until that is verified.

## Delivery: one send path, an operation id, now or queue

Every message to an agent from a person, another agent, the daemon's own notices, or a task, goes through one function:
`Send(operationID, text, Now|Queue) -> Receipt{State}`.

- **Operation id.** The caller supplies it (today's `request_id` on write calls). The daemon derives the message `uuid`
  from it (a name-based UUID), records the operation before writing, and answers a repeat with the recorded receipt without
  writing again. This is the existing `record_effect` idempotency applied to the CLI's input. It is what makes a retry safe.
- **Receipt states:** `queued` (held behind a running turn), `sent` (written to the CLI), `confirmed` (the CLI echoed the
  `uuid`). A caller that needs delivery waits for `confirmed` with its own deadline; "accepted" is never reported as
  "delivered".
- **Now** writes at once with `priority:"now"`, which joins the running turn. **Queue** while a turn runs is held by the
  daemon and written when the CLI reports the turn over, one message per turn. The CLI has its own `priority:"later"`; the
  daemon holds the queue itself because it can persist it, show it, and let a person withdraw it (`chat.withdraw`,
  `chat.send_now`, which are the existing "queued message" and "send now" buttons). With nothing running, Queue is the
  same as Now.
- **Crash rule.** If the daemon or the CLI dies with an operation in `sent` and not `confirmed`, the daemon does not write it
  again. It marks the operation `unknown` and tells the sender, because the CLI may or may not have run it and a repeat
  could do it twice. A `queued` operation survives and is written after the restart. This is the fail-closed choice; the
  prototype keeps the ledger in memory and the database table is part of the work below.

## Permissions and dialogs: callbacks, not keys

The CLI asks `can_use_tool` and waits. The daemon answers from, in order:

1. a policy the agent's settings already express (today's auto-accept rules, the protected-path and shell guards, the
   per-agent allow list), decided by code, with the rule that matched recorded in the event;
2. otherwise a **prompt record** in the existing prompt store (`prompt.list`, `prompt.answer`, the inbox card the web, PC and
   phone apps already show), and the callback waits until a person answers or the prompt is cancelled (`control_cancel_request`
   or session close).

Fail closed: no handler, a handler error, a timeout, or a closed session is a **deny** with a message the model sees. The
prototype's tests cover allow, no handler, and a failing handler.

Start-up dialogs (trust this folder, theme, login, update) do not exist in `--print` mode. Anything that still needs a person
arrives as `request_user_dialog` only if we declare the kind; we declare none, so the CLI never blocks on one. The start-up
auto-answer table and its fixtures are not needed, but the **trust and login** checks the daemon does before a start stay:
a signed-out account is found by the exit of the CLI with its stderr, as Orca does, not by a screen.

## What drops

By construction, nothing of these exists for a structured session:

- screen reading, prompt parsing, the screen-sample store, blocked / waiting / stall detection from screens;
- typing detection and the typing lock, the input hold (`input.take`, `input.release`, the holder gates), `terminal.input`,
  resize-by-holder, `session.type`, `session.keys`, `session.input` for agents;
- dialog handling and the start-up auto-answers;
- waiting for a quiet pane before typing, the paste/Enter timing, the press/composer drivers;
- the stall cases around all of the above (stuck prompt, half-typed line, lost Enter).

What replaces each: `session_state_changed`, `can_use_tool`, the echo of the message `uuid`, the control requests.

## The terminal, kept only to watch

A structured session has no pane. Two ways a person watches, in order of preference:

1. **The transcript view.** The daemon turns the CLI's frames into the same event stream the apps already read (see views
   below). This is what Orca's own chat does. No terminal is needed.
2. **A terminal agent, as a separate mode.** An agent configured as `terminal` keeps today's tmux pane and the G4 channel
   (`terminal.attach`, history, bytes), delivered to by Orca's mailbox-pointer approach rather than by typing messages. This
   is for a person who wants the real TUI. It is a fallback, not the default, and no new porting goes into it.

Attaching a TUI to the same session id a structured process holds would put two writers on one session; the design does not
allow it. `claude --resume` in a pane is a different agent mode, started only when the structured process is stopped.

## Views: web, PC, phone

The apps already render requests, inbox cards, prompts and the agent state. A structured session adds one thing to render:
the transcript. The daemon publishes a per-agent event stream (the CLI's frames reduced to what a view needs: assistant text,
tool use with its input, tool result, permission request and its answer, turn start and end with cost, errors), through the
existing `stream` topics with a sequence number, so a client that reconnects resumes. The permission request is a card with
allow and deny, which calls `prompt.answer`. The composer sends through the one send path with a client-made operation id.
Tool inputs and results are stored verbatim in `transcript_events` (the last 20000 rows per agent), so they are as sensitive as the files and commands they touched.
The apps need a transcript renderer; they do not need a terminal emulator for this mode.

## Fallbacks compared

- **Peer socket (scout's probe).** I have not read its result; it was not available to me. By description it injects a message
  into an already-running interactive session. It works with a TUI the person started, which stream-json cannot. It cannot
  give a permission callback, a turn state or an echo, and it is credential-adjacent (the peer inbox auth key), which the
  founder said to raise before building. Fallback for terminal agents only.
- **Claude Code channels** (`code.claude.com/docs/en/channels-reference`). A channel is an MCP server that Claude Code
  spawns, which pushes events into a running interactive session and can relay permission prompts to a trusted remote.
  Research preview, off by default for Team and Enterprise organizations until an admin enables it, and it needs the
  interactive session. It would give the terminal mode a clean inbox without typing. Worth revisiting when it leaves
  preview; not the main path.

## Unit by unit

"Go today" is what is on `main` or its branches as of this note. S = survives as is, R = rewritten over the structured
session, D = dropped (and, where already ported, deleted after the cutover), T = kept only for terminal-mode agents, frozen.

| Unit | Go today | Verdict |
|---|---|---|
| G1 foundation (paths, names, yaml, locks, protected and sealed files, gitrun, team core) | landed | **S**. The terminal lock (`lock`) is T. |
| G2 usage and vendors | landed (usage, AtLimit) | **S**. Later: read cost from `result` frames. The claude vendor's screen/transcript meter is T. |
| G3 backends: tmux and orca drivers, term.Backend | landed (`term/backend`, `drivers`) | **T**. Frozen. |
| G3 screen reading: `promptparse`, `screen`, `session/blocked`, `waiting`, `observe`, `autoaccept`, `screensamples` | landed (~2.0k) | **D** for structured agents. `autoaccept` rules become the policy step of the permission callback (**R**, small). |
| G3 `prompts` (block store, `prompt.list`, `prompt.answer`) | landed | **R**: the source of a prompt is `can_use_tool`, not a screen; the store, the RPC and the apps' cards stay (**S**). |
| G3 `shellguard`, `landgate`, `bgjobs` | landed | **S**: they guard commands and landing, not the screen. Move to the policy step where they read a command. |
| G4 terminal channel: attach, detach, history, bytes link, ansi, tmux extras | branch, split out for review | **T**: lands as reviewed, for terminal mode and for a person who attaches. |
| G4 input hold, `terminal.input`, `terminal.resize` | branch (held) | **D**. |
| G5 ask routing, tasks, holds, digest, push, memos, queue | landed | **S**. |
| G5 `send`, `sends`, `tell`, `teller`, `delivery`, `messaging`, `landing` (the typing of notices and requests) | landed | **R** into the one send path; the terminal path stays as T. This is the largest rewrite of landed code. |
| G6 `launch`, `build` | landed | **R**: argv for stream-json, session id, config dir pin, version check. The trust/login pre-checks stay. |
| G6 `spawn`, `wake`, `recover` | landed | **R**: spawn = open the connection; wake = resume the session id; recover = reconnect or restart from the session id. |
| G6 `close`, `idle`, `reconciler` | landed | **R**, smaller: idle = state `idle` and a timer; close = close stdin; the reconciler compares process, session and database instead of panes. |
| G6 `locks`, `trust` | landed | `locks` T; `trust` S for the account checks, D for the folder-trust dialog. |
| G7 handoff, note, fold trigger, events, digest, subscriptions | landed | **S**. `result` frames are an additional event source. |
| G8 triggers, evaluate, cost pilot, time box, login expiry, dev mode, apply settings, loop, waitfor | held | **S** with one change: the inputs "blocked", "waiting", "stalled" come from session state, not screens. Port after the core exists. |
| G9 `session.start/stop/park/wake/restart/interrupt` | branch (held) | **R** over the structured session. |
| G9 `session.input`, `session.keys`, `session.type` | branch (held) | **D** for agents. |
| G9 `terminal.close` | branch (held) | **T**. |
| G9 `agent.create` | branch (held) | cut by the founder's list; not ported. |
| Python `session/term.py`, `blocked.py`, `observe.py`, `waiting.py`, `promptparse.py`, `autoaccept.py` | python | **D** at cutover for structured agents (about 2.8k lines never ported). |
| Python `daemon/inputhold.py`, `rpc_input.py`, `rpc_gates.py`, `rpc_typing.py`, `termbytes.py`, `termchannel.py`, `screenstate.py` | python | the input and typing files **D**; channel files **T**. |
| Python `daemon/delivery.py`, `sends.py`, `messaging.py` | python | **R** into the send path. |
| Python `session/launch.py`, `idle_close.py`, `daemon/lifecycle.py`, `reconciler.py` | python | **R** as the Go G6 rows. |
| Python `vendors/claude*.py` | python | hooks (`claude_hooks`) **S**; ask/confirm screen helpers **D**; meter **T** until `result` frames are verified. |

## Size and timeline

Estimates in Go lines, without tests; tests roughly double the figure. They are guesses from the size of the units they
replace, not measurements.

| Piece | New or changed | What it is |
|---|---|---|
| S1 structured core | ~0.5k now (prototype) + ~0.7k | the connection, ledger in the database, now/queue, options, close with tree proof, event translation to the stream |
| S2 permissions | ~0.4k | policy step from autoaccept/guards, prompt records, callback wait and cancel |
| S3 lifecycle on S1 | ~1.2k changed of the 4.1k landed | launch, spawn, wake, recover, close, idle, reconciler |
| S4 send path | ~0.6k changed of the 3.7k supervisor | replace the typing in send/tell/delivery/landing |
| S5 views | ~0.6k daemon + the apps' transcript renderer | event stream topic and the `prompt.answer` card |
| Removed from the plan | G4 input hold and `terminal.input`/resize (~0.9k Go), G9 typing (~0.4k Go), stall handling in G8 | never written |
| Deleted after cutover | ~2.0k landed screen-reading Go (G3 blocked, waiting, observe, parsing) | once no terminal agent needs it, or kept for T |

Net: about 4k new or changed Go lines, against ~3k of already-ported Go that is deleted or frozen and ~4k of Python that is
never ported. Effect on the chain: G8 and G9 wait for S1 to S4 (about the size of two of the earlier pieces, G4 and G6, in
review rounds), then G9's real content shrinks to the `session.*` lifecycle methods over S3. G7 and the non-typing G5
pieces are unaffected. The cutover date moves by S1 to S4, and may come sooner than the straight port because the stall and
dialog cases, which are most of the Python's special handling, do not need porting.

## Proposed order

1. S1 and S2 behind a `structured` agent kind, one agent, side by side with terminal agents (the prototype is the start).
2. S4 the send path, so people and agents deliver the same way.
3. S3 lifecycle, then the cutover of the default for new agents.
4. S5 views in step with 1 to 3, so a person can see and answer.
5. G8 and the G9 `session.*` methods on top.
6. Delete the T code once no agent uses terminal mode.

## Prototype: what it proves and what it does not

`go/internal/structured` and its tests: a session over a fake CLI (the test binary re-run as the CLI) that speaks the wire
above. Proven by tests, with `go test -race`: initialize handshake and a failed start closing; a message delivered `Now` and
confirmed by its echo; a retry with the same operation id writes once; a `Queue` message held behind a running turn and
written after the turn ends (the turn is ended by an interrupt control request); a permission prompt answered by the
callback, and denied for no handler or a failing handler; the child sees only the config dir and four plain variables; a CLI
that is not the pinned version is refused.

**Not verified:** nothing here has run against the real CLI. The fake is written from the SDK's type definitions, not
from observed output. Open questions the real CLI must answer before the work above is committed to:
whether `--replay-user-messages` gives the `isReplay` echo with our `uuid` on this CLI (the SDK does not pass that flag, so
it may echo by another route); that `priority:"now"` steers a running turn; what `session_state_changed` does around a
permission prompt; that `initialize` is accepted without hooks or MCP; and the exit status and stderr of a signed-out
account. A live check is one short turn on a test account, which uses the account's usage; it should be run on purpose by
whoever owns that account.

## Decisions for the founder

1. Is terminal mode kept as a supported agent kind (the person who wants the real TUI), or only as the watch view that
   the transcript replaces? This note assumes it is kept, frozen, with no new porting.
2. Permission policy: auto-allow what today's rules allow and ask a person for the rest through the inbox. Anything stricter
   or looser changes how often a person is interrupted.
3. Who may run the live check against the real CLI, and on which account.
