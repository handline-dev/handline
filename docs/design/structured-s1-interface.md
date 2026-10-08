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
