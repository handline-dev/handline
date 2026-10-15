# Screen-reading inventory

Every place the Go daemon decides an agent's state by reading its Claude Code screen (`tmux capture-pane`), and the event that could replace each read. Read from main at b7ca0432. The hook payloads were checked against the installed Claude Code **2.1.299**: its hook schema, read out of the binary.

Already event-driven, with no screen read: running and idle (`UserPromptSubmit`, `Stop` and `StopFailure` in `go/internal/hooks/apply.go:262`, `:272`), the permission wait (`PermissionRequest`, `apply.go:160`), the session id (`SessionStart` with source `startup`, `apply.go:195`), and an expired login (`StopFailure` with error `authentication_failed`, `go/internal/tick/login.go:13`). Whether a session process is alive is polled from `ps` (`go/internal/session/observe.go`), not read off the screen.

Not covered here, because they decide no state: the terminal channel that streams the screen to viewers (`go/internal/termchan`), the screen samples (`session/screensamples.go`) and the debug view (`debugterm`).

Hooks installed today (`go/internal/claude/hooks.go:18`): SessionStart, SessionEnd, Stop, StopFailure, SubagentStop, PostCompact, UserPromptSubmit, PermissionRequest, TeammateIdle, PostToolUse, PostToolUseFailure, and PreToolUse for `Write|Edit|MultiEdit|NotebookEdit|Bash` only. **Notification is not installed.** How other tools map states to these hooks is in [agent-state-elsewhere.md](agent-state-elsewhere.md).

## 1. Ready for the first input (starting)

The screen key is `Tmux.WaitIdle` (`term/backend.go:183`): the last 40 rows unchanged for 1.5 s, and no `esc to interrupt` (`term/drivers.go:129`).

| Where | What it decides |
|---|---|
| `lifecycle/spawn.go:523` | A new agent's TUI is up before its first task is typed |
| `lifecycle/wake.go:767` | A relaunched session is up |
| `term/host.go:210` (`SessionOnScreen`) | Relaunch: a composer box (`term/composer.go:14`) or a dialog footer means a session is already drawn there |
| `supervisor/send.go:105` (`SessionOnScreen`) | Waiting for a session to end: is it still drawn |

**Replacement:** `SessionStart` (source `startup` or `resume`) says the session exists. Whether its TUI takes input at that moment is not established. Passing the first task as the launch's prompt argument removes the need to type it at all. **Partial.**

## 2. Folder-trust dialog

The screen key is `Is this a project you created or one you trust` or `This folder pre-approves`, with the folder read from under `Accessing workspace:` (`term/drivers.go:168`).

| Where | What it decides |
|---|---|
| `lifecycle/trust.go:28`, `:96`, `:105`, `:106` | A launch stopped at trust: which folder, and whether it is gone after the answer |
| `lifecycle/reconciler.go:381` (`TrustPromptGone`) | A trust stall can be cleared |
| `prompts/dialogs.go:43`, `:51` | The trust dialog shown to the founder |

**Replacement:** none from a hook, because the dialog comes before the session and its hooks. It can be avoided rather than read: `hasTrustDialogAccepted` for the project in the account's `.claude.json` (the key is in 2.1.299) means the dialog never shows, and trusting the main checkout covers its worktrees ([hook-probes.md](hook-probes.md) §2). **Kept:** the launch read (`clearTrustPrompt` in `lifecycle/trust.go:17`, via `Term.TrustPromptShowing`) stays a screen read, because the dialog is drawn before any hook loads, so no event exists. **No event; avoidable by config.**

## 3. Working or idle (mid-turn)

The screen key is the same `WaitIdle` as in §1: a stable screen and no `esc to interrupt`.

| Where | What it decides |
|---|---|
| `term/host.go:169` | Close: refuse while mid-turn |
| `term/host.go:227` | Relaunch: wait for the old turn |
| `lifecycle/trust.go:73`, `:113` | After accepting trust, the session settles |
| `lifecycle/structured.go:227` | A forced close or restart waits for the turn |
| `lifecycle/reconciler.go:517` (`IdleNow`) | The silence check: is a silent agent idle right now |

**Replacement:** `UserPromptSubmit` opens a turn; `Stop` or `StopFailure` closes it. Esc fires no hook ([hook-probes.md](hook-probes.md) §3), so while a turn is open the daemon watches its transcript with file events and closes the turn on the `[Request interrupted by user…]` entry. Built in `go/internal/turns`, behind `handlined -turns-from-hooks` (off by default). The screen is still read where no event has said: before a session's first prompt, and after a daemon restart until the next hook. **Replaced behind the switch.**

## 4. Idle with background work

The screen key is the hint row under the composer: `to manage`, with `<n> <noun>` counts (`term/composer.go:118`), and no spinner row (`term/drivers.go:154`).

| Where | What it decides |
|---|---|
| `supervisor/background.go:45` | Background count for the supervisor |
| `lifecycle/close.go:611`; `lifecycle/handoffmove.go:360` | Do not close an agent whose work still runs |
| `term/press.go:586` (`TypeableNow`) | No caller on main |

**Replacement:** `Stop` carries `background_tasks` (id, type, status, description, command), so "done" can be told from "waiting for background work". Background work that ends while the session is idle fires `UserPromptSubmit` then a `Stop` without it ([hook-probes.md](hook-probes.md) §3). Behind the same switch, `BackgroundWork` and `IdleWithBackground` answer from the last `Stop`. After an Esc no `Stop` comes, so the screen is read until the next one. **Replaced behind the switch.**

## 5. A dialog is open (blocker)

The screen key is a footer `Enter to confirm` or `Esc to cancel` in the last 3 rows (`term/screen.go:12`); otherwise, no composer box means unreadable.

| Where | What it decides |
|---|---|
| `sessionrpc/typing.go:177` | Refuse typing over a dialog |
| `session/blocked.go:248`, `:262`, `:275`, `:313` | A stuck agent's dialog; the exit-over-work dialog; note a failed close; get past a dialog |
| `session/waiting.go:188` | A permission wait is still up |
| `session/autoaccept.go:126` | Title match for auto-accept rules |
| `lifecycle/reconciler.go:391` | A dialog stall can be cleared |
| `lifecycle/close.go:574`, `:624`; `term/host.go:172` | Close: the dialog under `/exit`, an unreadable pane |
| `lifecycle/trust.go:121` | Any other startup dialog |

**Replacement:** a tool permission dialog is `PermissionRequest` (tool_name, tool_input, permission_suggestions). The dialog closing is the next `PostToolUse`, `PostToolUseFailure`, `PermissionDenied` or `Stop`. `Notification` with type `permission_prompt`, `idle_prompt` or `elicitation_dialog` would also say a person is needed, once installed. Claude Code's own dialogs (exit over running work, "Teach auto mode about your environment?", startup screens) have no hook. **Tool permissions: yes. The CLI's own dialogs: no replacement.**

## 6. What the prompt asks (reading it to answer)

The screen keys are numbered options under a `─`/`▔` rule (`term/screen.go:29`), the confirmation parse (`term/drivers.go:363`) and the AskUserQuestion parse (`term/drivers.go:621`).

| Where | What it decides |
|---|---|
| `prompts/watch.go:41` | Keep the founder's prompt row in line with the screen |
| `prompts/answer.go:53`, `:94` | Answer: still the same prompt, then press |
| `prompts/dialogs.go:47`; `session/blocked.go:489`, `:496` | Show the dialog's options or its screen to the founder |

**Replacement:** for a permission, `PermissionRequest` has the tool and its input, and its hook output `decision.behavior` `allow` or `deny` answers it with no keys. For AskUserQuestion, `PreToolUse` carries the questions in `tool_input` (the matcher would need `AskUserQuestion` added). Answering one through hook output is not verified. **Permissions: yes. Questions: read yes, answer unverified.**

## 7. Usage-limit dialog

The screen key is options starting `Stop and wait for limit to reset` and `Wait here, then continue automatically` (`term/screen.go:198`).

| Where | What it decides |
|---|---|
| `session/blocked.go:318` (`UsageLimitOpen`) | Only this dialog may be pressed past |

**Replacement:** `StopFailure` with error `rate_limit` says the turn ended on the limit; `Notification` types `quota_auto_resume_*` follow the auto-resume. Getting the dialog off the screen still takes a key. **Event for the state; pressing it still needs the screen.**

## 8. Conversation moved to the background (session picker)

The screen key is `moved to the background` above the box, with `enter to open` or `space to reply` below it (`term/drivers.go:261`).

| Where | What it decides |
|---|---|
| Inside every `ScreenBlocker` call in §5 | Typed text would start a new session |

**Replacement:** none found in 2.1.299. **No replacement.**

## 9. Session ended (shell prompt)

The screen key is `Resume this session with` (`term/composer.go:81`), or a shell prompt (`%`, `$`, `#`) as the last row (`term/composer.go:87`).

| Where | What it decides |
|---|---|
| `sessionrpc/typing.go:209` | Never type a prompt line into a shell |
| `term/host.go:210`, `supervisor/send.go:105` (via `SessionOnScreen`) | The session is gone from the pane |

**Replacement:** `SessionEnd` (reason `clear`, `resume`, `logout`, `prompt_input_exit` or `other`), and the session process's exit, watched rather than polled from `ps`. **Replaceable.**

## Python path left on main

The Python hook path (`handline/session/events.py`, for every event but Pre/PostToolUse) reads no screen. The `hl` verbs the Go `hl` does not answer still reach Python code that does, with the same patterns as above (`handline/session/term.py:456` and on): trust and idle in `handline/actions/details.py:321`; the trust dialog in `handline/actions/dialogs.py:42`; prompts in `handline/actions/prompt_blocks.py:114`. The Python daemon's modules that read it are deleted.

## No replacement yet

The folder-trust screen (avoidable by config, not by event); Claude Code's own dialogs (exit over work, auto-mode setup, startup screens); the background-session picker; background work ending while idle; answering an AskUserQuestion without keys (unverified); and whether an interrupted turn fires `Stop` (unverified).
