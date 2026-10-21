# Screen-reading inventory

Every place the Go daemon decides an agent's state by reading its Claude Code screen (`tmux capture-pane`), and the event that could replace each read. Read from main at b7ca0432. The hook payloads were checked against the installed Claude Code **2.1.299**: its hook schema, read out of the binary.

Already event-driven, with no screen read: running and idle (`UserPromptSubmit`, `Stop` and `StopFailure` in `go/internal/hooks/apply.go:262`, `:272`), the permission wait (`PermissionRequest`, `apply.go:160`), the session id (`SessionStart` with source `startup`, `apply.go:195`), and an expired login (`StopFailure` with error `authentication_failed`, `go/internal/tick/login.go:13`). Whether a session process is alive is polled from `ps` (`go/internal/session/observe.go`), not read off the screen.

Not covered here, because they decide no state: the terminal channel that streams the screen to viewers (`go/internal/termchan`), the screen samples (`session/screensamples.go`) and the debug view (`debugterm`).

Hooks installed today (`go/internal/claude/hooks.go:18`): SessionStart, SessionEnd, Stop, StopFailure, SubagentStop, PostCompact, UserPromptSubmit, PermissionRequest, TeammateIdle, PostToolUse, PostToolUseFailure, and PreToolUse for `Write|Edit|MultiEdit|NotebookEdit|Bash` only, and Notification for `permission_prompt` only. How other tools map states to these hooks is in [agent-state-elsewhere.md](agent-state-elsewhere.md).

## 1. Ready for the first input (starting)

The screen key is `Tmux.WaitIdle` (`term/backend.go:183`): the last 40 rows unchanged for 1.5 s, and no `esc to interrupt` (`term/drivers.go:129`).

| Where | What it decides |
|---|---|
| `lifecycle/spawn.go:523` | A new agent's TUI is up before its first task is typed |
| `lifecycle/wake.go:767` | A relaunched session is up |
| `term/host.go:210` (`SessionOnScreen`) | Relaunch: a composer box (`term/composer.go:14`) or a dialog footer means a session is already drawn there |
| `supervisor/send.go:105` (`SessionOnScreen`) | Waiting for a session to end: is it still drawn |

**Replacement:** `SessionStart` (source `startup` or `resume`) says the session exists. Whether its TUI takes input at that moment is not established. Passing the first task as the launch's prompt argument removes the need to type it at all. **Partial.**

**Status: kept.** No event says the TUI takes input. In the live run a terminal agent went from `no_terminal` straight to `idle`, and `starting` was never shown. An open PR is meant to show `starting` while a session launches.

## 2. Folder-trust dialog

The screen key is `Is this a project you created or one you trust` or `This folder pre-approves`, with the folder read from under `Accessing workspace:` (`term/drivers.go:168`).

| Where | What it decides |
|---|---|
| `lifecycle/trust.go` (`startupScreens`) | A launch stopped at trust anyway: which folder, for the log line and the decision |
| `lifecycle/reconciler.go:381` (`TrustPromptGone`) | A trust stall can be cleared |
| `prompts/dialogs.go:43`, `:51` | The trust dialog shown to the founder |

**Replacement:** none from a hook, because the dialog comes before the session and its hooks. It can be avoided rather than read: `hasTrustDialogAccepted` for the project in the account's `.claude.json` (the key is in 2.1.299) means the dialog never shows, and trusting the main checkout covers its worktrees ([hook-probes.md](hook-probes.md) §2). **Done:** before every launch the daemon writes that key for the worktree in each claude account's config (`go/internal/claudetrust`), and nothing presses the dialog any more. The launch read (`startupScreens`, via `Term.TrustPromptShowing`) stays only to log and raise the dialog if it shows anyway.

**Status: avoided by config.** The read is kept as a check that the write worked.

## 3. Working or idle (mid-turn)

The screen key is the same `WaitIdle` as in §1: a stable screen and no `esc to interrupt`.

| Where | What it decides |
|---|---|
| `term/host.go:169` | Close: refuse while mid-turn |
| `term/host.go:227` | Relaunch: wait for the old turn |
| `lifecycle/trust.go:73`, `:113` | After accepting trust, the session settles |
| `lifecycle/structured.go:227` | A forced close or restart waits for the turn |
| `lifecycle/reconciler.go:517` (`IdleNow`) | The silence check: is a silent agent idle right now |

**Replacement:** `UserPromptSubmit` opens a turn; `Stop` or `StopFailure` closes it. Esc fires no hook ([hook-probes.md](hook-probes.md) §3), so while a turn is open the daemon watches its transcript with file events and closes the turn on the `[Request interrupted by user…]` entry. Built in `go/internal/turns`, behind `handlined -turns-from-hooks` (off by default; `hl service install` turns it on). The screen is still read where no event has said: before a session's first prompt, and after a daemon restart until the next hook. **Replaced behind the switch.**

**Status: replaced behind `-turns-from-hooks`.** In the live run (`tools/live-e2e.sh --terminal`, Claude Code 2.1.301), `running` reached the client 0.25 s after the message was sent, and `idle` 0.09 to 0.23 s after the `Stop` hook. After an Esc, idle came in 0.47 s, after `session.interrupt` in 0.47 s, and after a hard kill mid-turn the agent left `running` in 0.15 s.

## 4. Idle with background work

The screen key is the hint row under the composer: `to manage`, with `<n> <noun>` counts (`term/composer.go:118`), and no spinner row (`term/drivers.go:154`).

| Where | What it decides |
|---|---|
| `supervisor/background.go:45` | Background count for the supervisor |
| `lifecycle/close.go:611`; `lifecycle/handoffmove.go:360` | Do not close an agent whose work still runs |
| `term/press.go:586` (`TypeableNow`) | No caller on main |

**Replacement:** `Stop` carries `background_tasks` (id, type, status, description, command), so "done" can be told from "waiting for background work". Background work that ends while the session is idle fires `UserPromptSubmit` then a `Stop` without it ([hook-probes.md](hook-probes.md) §3). Behind the same switch, `BackgroundWork` and `IdleWithBackground` answer from the last `Stop`. After an Esc no `Stop` comes, so the screen is read until the next one. **Replaced behind the switch.**

**Status: replaced behind `-turns-from-hooks`.** Unit-tested. The live run has no background-work step.

## 5. A dialog is open (blocker)

The screen key is a footer `Enter to confirm` or `Esc to cancel` in the last 3 rows (`term/screen.go:12`); otherwise, no composer box means unreadable.

| Where | What it decides |
|---|---|
| `sessionrpc/typing.go:177` | Refuse typing over a dialog |
| `session/blocked.go:248`, `:262`, `:275`, `:313` | A stuck agent's dialog; the exit-over-work dialog; note a failed close; get past a dialog |
| `session/waiting.go:188` | A permission wait is still up |
| `lifecycle/reconciler.go:391` | A dialog stall can be cleared |
| `lifecycle/close.go:574`, `:624`; `term/host.go:172` | Close: the dialog under `/exit`, an unreadable pane |
| `lifecycle/trust.go:121` | Any other startup dialog |

**Replacement:** a tool permission dialog is `PermissionRequest` (tool_name, tool_input, permission_suggestions). The dialog closing is the next `PostToolUse`, `PostToolUseFailure`, `PermissionDenied` or `Stop`. `Notification` with type `permission_prompt`, `idle_prompt` or `elicitation_dialog` would also say a person is needed, once installed. Claude Code's own dialogs (exit over running work, "Teach auto mode about your environment?", startup screens) have no hook. **Tool permissions: yes. The CLI's own dialogs: no replacement.**

**Status: tool permissions replaced; the CLI's own dialogs kept.** The needs-you card is fed by `PermissionRequest`, and by `Notification` behind `-notification-needs-you`. In the live run the card showed with the prompt and cleared 2.8 s after the prompt was answered at the terminal. Through `Notification` it showed 4.1 s after the hook, on a run whose hooks went through Python. The Go `hl hook` stopped forwarding `Notification` when the hooks moved to Go; this branch forwards it again, and the live run can show it once the installed `hl` has the fix, since a live account's hooks run the installed `hl`. Kept: refusing to type over a dialog, and Claude Code's own dialogs, for which no hook exists.

**Heal: replaced behind `-turns-from-hooks`.** Heal leaves an agent alone while a person is needed. An open needs-you card says so without the screen, and otherwise the screen is read as for the close guard: a turn a `Stop` closed with no prompt since counts only the session picker, which no hook reports. The reconciler's silence check (§3) reads no screen in the running daemon: its terminal port takes idle from the session's own file.

**Close guard: replaced behind `-turns-from-hooks`.** Ending a session respawns its pane and types nothing, so the dialogs that matter are a tool's prompt, which raises `PermissionRequest` or `Notification`, and exit over running work, which background work already refuses. For a session whose turn a `Stop` closed, with no prompt raised since, the screen is still read, but only the session picker (§8), which no hook reports, refuses the close, so an unreadable screen no longer blocks it. After a `StopFailure` (the usage-limit dialog may be up), an Esc, or before any hook, the screen is read as before.

## 6. What the prompt asks (reading it to answer)

The screen keys are numbered options under a `─`/`▔` rule (`term/screen.go:29`), the confirmation parse (`term/drivers.go:363`) and the AskUserQuestion parse (`term/drivers.go:621`).

| Where | What it decides |
|---|---|
| `prompts/watch.go:41` | Keep the founder's prompt row in line with the screen |
| `prompts/answer.go:53`, `:94` | Answer: still the same prompt, then press |
| `prompts/dialogs.go:47`; `session/blocked.go:489`, `:496` | Show the dialog's options or its screen to the founder |

**Replacement:** for a permission, `PermissionRequest` has the tool and its input, and its hook output `decision.behavior` `allow` or `deny` answers it with no keys. For AskUserQuestion, `PreToolUse` carries the questions in `tool_input` (the matcher would need `AskUserQuestion` added). Answering one through hook output is not verified. **Permissions: yes. Questions: read yes, answer unverified.**

**Status: kept.** A daemon without the prompt watch (`-own-prompts`), as the live one runs, has no prompt rows for a terminal agent, and the card says to handle it in the terminal. Answering through hook output is not done.

## 7. Usage-limit dialog

The screen key is options starting `Stop and wait for limit to reset` and `Wait here, then continue automatically` (`term/screen.go:198`).

| Where | What it decides |
|---|---|
| `session/blocked.go:318` (`UsageLimitOpen`) | Only this dialog may be pressed past |

**Replacement:** `StopFailure` with error `rate_limit` says the turn ended on the limit; `Notification` types `quota_auto_resume_*` follow the auto-resume. Getting the dialog off the screen still takes a key. **Event for the state; pressing it still needs the screen.**

**Status: kept.** Getting the dialog off the screen still needs a key.

## 8. Conversation moved to the background (session picker)

The screen key is `moved to the background` above the box, with `enter to open` or `space to reply` below it (`term/drivers.go:261`).

| Where | What it decides |
|---|---|
| Inside every `ScreenBlocker` call in §5 | Typed text would start a new session |

**Replacement:** none found in 2.1.299. **No replacement.**

**Status: kept.** No event exists.

## 9. Session ended (shell prompt)

The screen key is `Resume this session with` (`term/composer.go:81`), or a shell prompt (`%`, `$`, `#`) as the last row (`term/composer.go:87`).

| Where | What it decides |
|---|---|
| `sessionrpc/typing.go:209` | Never type a prompt line into a shell |
| `term/host.go:210`, `supervisor/send.go:105` (via `SessionOnScreen`) | The session is gone from the pane |

**Replacement:** `SessionEnd` (reason `clear`, `resume`, `logout`, `prompt_input_exit` or `other`), and the session process's exit, watched rather than polled from `ps`. **Replaceable.**

**Status: replaced behind `-turns-from-hooks` where the hooks know.** After `SessionEnd` or the session process's exit, and until the next launch or `SessionStart`, the session is gone without a screen read; while its process is watched it is there. Otherwise, as after a daemon restart before any hook, the screen is read.

## Python path left on main

The Python hook path (`handline/session/events.py`, for every event but Pre/PostToolUse) reads no screen. The `hl` verbs the Go `hl` does not answer still reach Python code that does, with the same patterns as above (`handline/session/term.py:456` and on): trust and idle in `handline/actions/details.py:321`; the trust dialog in `handline/actions/dialogs.py:42`; prompts in `handline/actions/prompt_blocks.py:114`. The Python daemon's modules that read it are deleted.

## No replacement yet

The folder-trust screen (avoidable by config, not by event); Claude Code's own dialogs (exit over work, auto-mode setup, startup screens); the background-session picker; background work ending while idle; answering an AskUserQuestion without keys (unverified); and whether an interrupted turn fires `Stop` (unverified).
