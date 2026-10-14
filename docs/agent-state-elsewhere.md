# How other agent tools know Claude Code's state

Research for story 5-0 (part 3): how other tools tell starting, ready, working, idle, waiting on a
permission or dialog, login expired and exited, and whether their code may be copied. Read from each project's
source on 2026-10-14.

## Summary

| Tool | Runs Claude as | How it knows the state | License |
|---|---|---|---|
| [Orca](https://github.com/stablyai/orca) | interactive, in its own terminal | hooks: 13 events into a local hook server, plus a status line script | MIT |
| [cmux](https://github.com/manaflow-ai/cmux) | interactive, in its own terminal | hooks: 9 events, `StopFailure` error mapped to a status | GPL-3.0-or-later; server parts BUSL-1.1 |
| [Happy](https://github.com/slopus/happy) | interactive (local mode) or the Agent SDK (remote mode) | local: a `SessionStart` hook names the session, then the transcript JSONL is tailed. Remote: SDK messages and `canUseTool` | MIT |
| [Sculptor](https://github.com/imbue-ai/sculptor) | interactive in a container, or stream-json | hooks (6 events), and `--include-hook-events` on stream-json; skips dialogs with `--dangerously-skip-permissions` | MIT |
| [vibe-kanban](https://github.com/BloopAI/vibe-kanban) | `claude -p`, stream-json in and out | the stream-json control protocol: `can_use_tool` and `hook_callback` requests, `result` ends a turn, process exit | Apache-2.0 |
| [Crystal](https://github.com/stravu/crystal) | `--output-format stream-json` in a pty | stream-json messages; permission through an MCP `--permission-prompt-tool` | MIT |
| [opcode](https://github.com/winfunc/opcode) (was claudia) | `claude -p --output-format stream-json` | stream-json and process exit; history from `~/.claude/projects/*.jsonl` | AGPL-3.0 |
| [claudecodeui](https://github.com/siteboon/claudecodeui) | the Claude Agent SDK (`query`) | SDK messages, SDK `hooks` and `canUseTool` | AGPL-3.0 |
| [Claude Squad](https://github.com/smtg-ai/claude-squad) | interactive in tmux | screen: hash of `tmux capture-pane` (changed means working), the string "No, and tell Claude what to do differently" means a prompt | AGPL-3.0 |
| [ccmanager](https://github.com/kbwo/ccmanager) | interactive in a pty | screen: a headless xterm, spinner regex, the prompt box border, 1.5 s idle debounce | MIT |
| [Conductor](https://www.conductor.build) | bundles its own Claude Code | not public: the FAQ does not say | closed source |

Two groups. The tools that keep the interactive terminal (Orca, cmux, Happy local, Sculptor) all moved to hooks.
The ones that read the screen (Claude Squad, ccmanager) are the small ones, and ccmanager's code is a list of
workarounds ("Claude Code sometimes appears idle in terminal output while still actively processing"). The rest
drop the terminal for stream-json or the SDK, which Handline cannot do for a terminal agent.

## The ones that use hooks

- **Orca.** The event list is
  [`claude-managed-hook-events.ts`](https://github.com/stablyai/orca/blob/main/src/main/claude/claude-managed-hook-events.ts):
  `SessionStart`, `UserPromptSubmit`, `Stop`, `StopFailure`, `SubagentStart`, `SubagentStop`, `TeammateIdle`,
  `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PermissionRequest`, `PostCompact`, `SessionEnd`, each with a
  comment on why it is needed. The rules that turn them into working, waiting and done are
  [`server-claude-status-rules.ts`](https://github.com/stablyai/orca/blob/main/src/main/agent-hooks/server/server-claude-status-rules.ts)
  and its neighbours in `src/main/agent-hooks/server/`: a permission stays visible over later progress, and a child
  agent's prompt is held over the parent. `docs/orca-cli-survey.md` §4 covers what Orca reports from them.
- **cmux.** Hooks set in
  [`CMUXCLI+ClaudeHookSettings.swift`](https://github.com/manaflow-ai/cmux/blob/main/CLI/CMUXCLI+ClaudeHookSettings.swift)
  (`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `PermissionRequest`, `Notification`, `Stop`,
  `StopFailure`, `SessionEnd`). The useful part is
  [`ClaudeStopFailure.swift`](https://github.com/manaflow-ai/cmux/blob/main/Packages/macOS/CMUXAgentLaunch/Sources/CMUXAgentLaunch/ClaudeStopFailure.swift),
  which shows a turn that died on `authentication_failed`, `billing_error`, `rate_limit` and the rest.
- **Happy, local mode.** [`claudeLocal.ts`](https://github.com/slopus/happy/blob/main/packages/happy-cli/src/claude/claudeLocal.ts)
  starts the real `claude` with `--settings <file>` holding one `SessionStart` hook that posts to a local server
  ([`generateHookSettings.ts`](https://github.com/slopus/happy/blob/main/packages/happy-cli/src/claude/utils/generateHookSettings.ts)).
  The session id that hook gives names the transcript, and
  [`sessionScanner.ts`](https://github.com/slopus/happy/blob/main/packages/happy-cli/src/claude/utils/sessionScanner.ts)
  tails it. This is Handline's own shape: a terminal agent, a hook for identity, the transcript for content.
- **Sculptor.** [`claude-code-hooks.json`](https://github.com/imbue-ai/sculptor/blob/main/samples/terminal_agents/claude-code/claude-code-hooks.json)
  (`SessionStart`, `UserPromptSubmit`, `PreToolUse`, `PostToolUse`, `Notification`, `Stop`). It avoids the permission
  and bypass dialogs instead of reading them: `--dangerously-skip-permissions` plus
  `skipDangerousModePermissionPrompt` in the settings.

## What hooks give for each state

From the hook reference (code.claude.com/docs/en/hooks):

| State | Hook signal |
|---|---|
| starting | process started, no `SessionStart` yet |
| ready | `SessionStart` (`source` startup, resume, clear, compact, fork) |
| working | `UserPromptSubmit` until `Stop`; `PreToolUse`/`PostToolUse` keep it fresh |
| idle | `Stop`; `Notification` `idle_prompt` |
| waiting on permission | `PermissionRequest`; `Notification` `permission_prompt` |
| waiting on a question | `Notification` `elicitation_dialog`, `agent_needs_input`; `Elicitation` |
| login expired, limits | `StopFailure` `error`: `authentication_failed`, `oauth_org_not_allowed`, `account_on_hold`, `billing_error`, `rate_limit`; `SessionEnd` `reason` `logout`; `Notification` `auth_success` |
| exited | `SessionEnd`, then the process exits |

What no tool reads from hooks: anything shown before `SessionStart`, such as the folder trust dialog, the bypass
warning and the login screen. Claude Squad answers the trust dialog from the screen
([`tmux.go` `CheckAndHandleTrustPrompt`](https://github.com/smtg-ai/claude-squad/blob/main/session/tmux/tmux.go));
Sculptor avoids it in settings. `docs/screen-reading-inventory.md` (story 5-0, parts 1 and 2) lists where Handline still needs the screen for these.

## Against Handline today

`go/internal/claude/hooks.go` `Wanted` already has 12 of Orca's 13 events (not `SubagentStart`). It has no
`Notification` hook, so `idle_prompt`, `permission_prompt`, `elicitation_dialog` and `auth_success` are never seen.

## Worth copying

- **Orca's status rules**, for the cases Handline would otherwise find one by one: a permission held over later
  progress, a child's prompt over the parent, `StopFailure` ending a turn. MIT: copying is allowed with the
  copyright and license notice kept.
- **Happy's local mode**, as the confirmation that a hook for identity plus the transcript for content is enough for
  an interactive agent. MIT.
- **cmux's `StopFailure` mapping**, as a list of values only: GPL-3.0 code cannot be copied into Handline without
  putting Handline under the GPL. The values themselves come from Claude Code's hook reference.

Not to copy: Claude Squad, opcode and claudecodeui are AGPL-3.0, and Claude Squad and ccmanager read the screen,
which is what this story removes. vibe-kanban (Apache-2.0, copying allowed with notice) and Crystal (MIT) only fit
an agent driven by stream-json, not a terminal one.
