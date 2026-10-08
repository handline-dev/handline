# Structured session interface (S1/S2), for S3, S4, S5

Package `go/internal/structured`. Stable unless a task memo to the S1 owner changes it.

- `Open(ctx, Launch, Handlers, initTimeout) (*Session, error)` starts the CLI, completes `initialize`, settles the ledger. `Launch{Path, Cwd, ConfigDir, SessionID, Resume, Model, Args, Env, Ledger}`: a new agent passes a fresh `SessionID`; a wake or recover passes the stored one with `Resume: true`. Call `CheckVersion(ctx, path, PinnedCLI)` first.
- Session id lives in `agent_state.resume_session` (existing column, no schema step). Sends live in the `operations` table, kind `send`, id `send:<agent>:<op>` (`DBLedger{DB, Agent, Now}`), states queued/sent/confirmed/unknown.
- `Send(op, text, Now|Queue) (Receipt, error)`; `WaitConfirmed(ctx, op)`; a repeat of `op` returns the recorded receipt, also after a restart. `Unknown` = written, never echoed: do not resend, tell the sender.
- `Interrupt(ctx)`, `SetModel`, `SetPermissionMode`, `Settings` (read back).
- `Close() error` ends stdin, kills the process group after `CloseGrace`, and returns an error if a process of the group is still alive: do not report the agent stopped then.
- Liveness/state: `Done()` (closed on exit), `ExitErr()`, `State()` (idle, running, requires_action), `Busy()`, `Pid()`. `Handlers{CanUseTool, OnEvent, OnExit}`.
- Events: `Translate(frame) []Event` and `EventSink.Emit(agent, Event)`; types listed in events.go.
- Permissions (S2): `Broker` with `Policy`, `PromptStore`; `Manager` owns sessions by agent name.

Prompt rows: `DBPrompts` writes the open permission prompt to `prompt_blocks` (kind permission, answerable, no wait id) and refreshes `last_seen` every few seconds while it waits, so `prompt.list` shows it. `prompt.answer` for an agent with a structured session (`prompts.Env.Structured`, set to the `Manager`) goes to `Broker.Answer` and takes `yes`, `no` or `esc` (deny); no terminal lock or input holder applies. A structured agent has no terminal handle, so the screen watch never touches its row.

Note line: **drops:** screen-read permission answering (`term.AnswerConfirmation`) -> `can_use_tool` callback; typing a message -> `Send`; `autoaccept` dialog rules -> the Policy's guard and allow list (the dialogs they answered do not exist in `--print`). **straight:** the shell guard (`session.Guard`) and the `prompt_blocks` row and `prompt.answer` shape, which the apps already read.

## Live check on the real CLI (2.1.294, account sigma, 2026-10-08)

Run from the sandbox with `LIVE_CLAUDE_CONFIG=<dir> go test -run TestLiveCLI -v ./internal/structured`. The sandbox cannot reach the model API, so no turn completed; the rest is observed.

- `initialize` is accepted with no hooks or MCP. Its response lists agents, account, `pending_permission_requests` and `pending_user_dialog_requests`.
- The child must be given `USER` and `LOGNAME`: with only PATH, HOME, TMPDIR and LANG the CLI answers "Not logged in" for a logged-in account. Fixed in `Launch.env`.
- `session_state_changed` frames only come with `CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS=1`. Set in `Launch.env`. Seen: `running` at turn start.
- The CLI sends `command_lifecycle` frames (`queued`, `started`, `cancelled`, with `command_uuid` = our message uuid) on every turn. The `isReplay` echo came when the model call failed fast (signed-out), and did not come in three minutes of API retries. So delivery is now confirmed by either: echo, or lifecycle `queued`/`started`. `cancelled` is not a confirmation.
- A signed-out account: the process stays alive, the assistant frame has `error: "authentication_failed"` and the result is `is_error` with "Not logged in"; exit status 1 only at close.
- Model API unreachable: `system/api_retry` frames, ten attempts with growing delay (about 3 minutes in all), then a result with `is_error` and no text. A stall limit on `api_retry` is needed in S3.
- NOT observed, still open: the `isReplay` echo on a successful turn, `priority:"now"` steering, `idle` after a turn and its order around a permission prompt, `can_use_tool` (no turn reached a tool), the `get_settings` shape (`effective.model`, `effective.permissions.defaultMode` are from the SDK types). Run the live test outside the sandbox once to settle these.
