# Hook probes: AskUserQuestion, folder trust, Esc, Notification

Four questions left open in [screen-reading-inventory.md](screen-reading-inventory.md), answered by running Claude Code **2.1.299** (haiku) by hand. No daemon was involved.

## Setup

An interactive `claude` in its own tmux server (`tmux -L probe`), in a scratch folder, on the account with the most headroom (`tools/live_account.py`), with the live hooks shut out:

```sh
CLAUDE_CONFIG_DIR=<account>/.claude claude --model haiku --permission-mode default \
  --setting-sources project,local --settings probe-settings.json
```

`--setting-sources project,local` drops the user settings, which hold the daemon's `hl` hooks. Hooks from `--settings` still load. `probe-settings.json` hooks every event with matcher `*` to one script. That script appends the event's stdin to a log and, for AskUserQuestion, can print one of the outputs below. Keys were sent with `tmux send-keys`.

## 1. AskUserQuestion: a hook can answer it, with no keys

With no hook output, the order is: `PreToolUse` (tool_name `AskUserQuestion`, `tool_input.questions`), then `PermissionRequest` with the same input, then the dialog. About 6 s later, `Notification` `permission_prompt` arrives. Once someone answers, the answer shows up in `PostToolUse` `tool_response.answers` as `{"<question>": "<label>"}`.

Either of these hook outputs answers it. No dialog is drawn, and the model gets the chosen label (`User answered Claude's questions: · Which fruit? → Pear`):

```json
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "allow",
  "updatedInput": {"questions": [...as received...], "answers": {"Which fruit?": "Pear"}}}}
```

```json
{"hookSpecificOutput": {"hookEventName": "PermissionRequest",
  "decision": {"behavior": "allow", "updatedInput": {"questions": [...], "answers": {"Which day?": "Friday"}}}}}
```

The PreToolUse output skips `PermissionRequest` and fires no Notification. The PermissionRequest output adds `Allowed by PermissionRequest hook` under the answer. In both, `answers` is keyed by the question text, and the value is an option label. The daemon's PreToolUse matcher (`go/internal/claude/hooks.go`) would need `AskUserQuestion` added for the first form. The second form needs no matcher change.

## 2. Folder trust: no way found without `.claude.json`

Observed: in a folder that is not trusted, the dialog (`Is this a project you created or one you trust?`) comes before every hook. No event fired until it was accepted. Only then did `SessionStart` (source `startup`) arrive. So a hook can neither see nor answer it.

Tried, or read in the 2.1.299 binary:

- **Settings:** no settings key turns the dialog off. Neither `--settings` nor a project `.claude/settings.json` helps, since project settings only load once the folder is trusted.
- **Non-interactive runs:** `-p`, or stdout that is not a TTY, skip the dialog, per `claude --help`. A stream-json host can also pass `workspaceTrust` in its `initialize` request. Neither applies to a TUI session in tmux.
- **Trust inherited from a parent folder:** the check walks up from the working directory to the git root and accepts any trusted folder on the way. Outside a git repo, it walks up to `/`. That trust is still `hasTrustDialogAccepted` in `.claude.json`. A git worktree's own root has to be trusted, because a trusted parent above the repo does not count.
- **Environment variables:** the binary names several that touch trust, all internal to its own background, sandbox and runner modes. I stopped there: the permission check in my session refused further digging into ways around the trust dialog. None of them was tried.

**Answer: no.** Short of writing `hasTrustDialogAccepted`, the TUI shows the dialog, and today it is read off the screen. Avoiding it is a founder decision. The options are: allow that one `.claude.json` key to be written, keep reading the screen, or launch through the stream-json host, which takes trust in `initialize`.

## 3. Esc-interrupted turn: nothing fires

Three cases, each watched for at least 70 s afterwards:

| Esc pressed | Hooks after the Esc |
|---|---|
| While text streams (no tool) | none: no `Stop`, `StopFailure` or `Notification` |
| At a Bash permission dialog | none: no `PermissionDenied`, `PostToolUseFailure` or `Stop` |
| While an allowed Bash command runs | none: no `PostToolUse`/`PostToolUseFailure` or `Stop` |

`idle_prompt` did not follow an interrupt either, though it follows a `Stop` (§4). The only record is in the transcript JSONL: a `user` entry with text `[Request interrupted by user]`, or `[Request interrupted by user for tool use]` in the two tool cases. The first case also carries `interruptedMessageId`. So the end of an interrupted turn is known by watching the transcript for that entry. Otherwise it stays unknown until the next `UserPromptSubmit`.

Also seen: a background Bash command that finishes while the session is idle starts a new turn. A `UserPromptSubmit` fires, then a `Stop`. `Stop.background_tasks` lists the shell as `running` before the command ends, and is empty after. That corrects §4 of the inventory: the end of background work does fire a hook.

## 4. Notification: what arrives, and when

Seen live:

| `notification_type` | `message` | When |
|---|---|---|
| `permission_prompt` | `Claude needs your permission` | About 6 s after a `PermissionRequest` that no hook answered, for a tool such as Bash and for AskUserQuestion alike. Not sent when a hook answered first. |
| `idle_prompt` | `Claude is waiting for your input` | 60 s after a `Stop` with no new prompt. Sent once, and not after an Esc interrupt. |

The Notification matcher list in the binary gives every value Notification can carry: `permission_prompt`, `idle_prompt`, `auth_success`, `elicitation_dialog`, `elicitation_url_dialog`, `elicitation_complete`, `elicitation_response`, `agent_needs_input`, `agent_completed`, `worker_permission_prompt`, `push_notification`, `computer_use_enter`, `computer_use_exit`, `quota_auto_resume_fired`, `quota_auto_resume_stale`, `quota_auto_resume_disabled`, `model_refusal_fallback`, `auth_storage_failure` and `plugin_notification`. Not triggered here: `auth_success` (needs a login), and the `elicitation_*` types (need an MCP server that asks).

Other events seen: `SessionEnd` with reason `prompt_input_exit` on `/exit`, and `other` when the tmux session was killed.
