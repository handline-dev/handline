# Direct writers: who still writes the database without a daemon method

Every place in `cli/`, `ui/` and `team/` that opens `db.connect()` and writes, found by walking the syntax tree (106 functions open a
connection, 63 call something that writes). Each is either **method** (a daemon method serves it; the command or screen is to call
that method), **inside** (a helper under a method: it stays the one write the method runs), **read** (the walk was wrong: it only
reads, or only touches the screen), **local** (stays direct, with the reason), or **gap** (no method in the spec yet: named, with where
it will be built).

"Served" below means on `main` when this was written; "in review" means a branch the auditor holds. The commands of the agent surface
are listed first, then every writer found.

## x-agent-commands: command, method, state

| command | method | state |
|---|---|---|
| `hl task new`, `edit`, `show`, `list` | task.new, task.edit, task.get, tasks.list | served, agent-role test |
| `hl task memo add|edit|delete` | task.memo.add, .edit, .delete | write methods slice b, in review, agent-role test there |
| `hl task done|reopen|merge|release|discard|queue|draft` | task.done, .reopen, .merge, .release, .discard, .queue, .draft | write methods slice c, in review, agent-role test there |
| `hl task begin|start|handoff`, `hl progress` | task.begin, .start, .handoff, .progress | gap: the agent-surface task |
| `hl task board`, `hl task memo list` | task.board, task.memo.list | reads, the Go side |
| `hl inbox` | inbox.list (+ inbox.mark_read for the head of the queue) | served; mark_read in slice b |
| `hl read` | inbox.get | gap: standing answers and inbox.get task |
| `hl say`, `hl ask`, `hl withdraw` | say, ask, chat.withdraw | gap: agent-surface task and chat slice 2 |
| `hl land`, `hl land --pass` | land, land.pass | gap: agent-surface task |
| `hl hook` | hook.record | served; no agent-role test of the command yet |
| `hl agent set|move`, `hl fold` | agent.set, agent.move, agent.fold | gap: agent-surface task |
| `hl close`, `hl restart` | session.stop, session.restart | served, agent-role test |
| `hl resume` | session.wake | served, agent-role test |
| `hl wake` | session.wake | **the spec lists a command that does not exist**: `hl wake` is no subcommand (`hl resume` is the one); fix the spec's x-agent-commands |
| `hl hr ...`, `hl trust ...` | hr.propose, hr.withdraw, hr.list, trust.propose, trust.list, trust.remove | gap: agent-surface task; hr.list in the reads part 2 task |
| `hl team`, `hl status`, `hl account list`, `hl schedule`, `hl memory status`, `hl triggers` | team.get, accounts.list, schedules.list, memory.status, agent.triggers | gap: reads part 2 (all but triggers and memory) and agent-surface task |
| `hl ps`, `hl events` | agents.list, events.feed | served, agent-role test |

## Every writer

### cli/

| function | writes | state |
|---|---|---|
| account.cmd_account_remove | accounts.remove_account | **gap**: the spec has no account.remove; founder command |
| agent.cmd_resume | set_agent_state, wake | method: session.wake (served) |
| agent.cmd_unstall | actions.unstall | **gap**: the spec has no unstall; founder command |
| ask.cmd_cancel | actions.cancel_plan | **gap**: no method for cancelling a planned message; chat slice 2 |
| ask.cmd_inbox | mark_delivered, mark_landed, answer verb | method: inbox.mark_read (slice b) |
| ask.cmd_read | db.dismissal | **gap**: inbox.get, which also records the dismissal |
| daemon.cmd_daemon_status, health.cmd_triggers | none (reads) | read |
| health.cmd_doctor | settle_leftover_deliveries | local: a repair `hl doctor` runs by hand on a stopped or sick daemon; routing it through the daemon would fail exactly when it is needed |
| hr.cmd_hr | hr.propose, decide, rehome.move | method hr.decide (served); **gap**: hr.propose, hr.withdraw, rehome |
| task.cmd_progress | set_progress | **gap**: task.progress |
| task.cmd_task_done, discard, queue/draft, reopen, release, merge | the actions of the same names | method: slice c |
| task.cmd_task_new, cmd_task_edit | task_new, edit_task, add_block, send_moved_task | method: task.new, task.edit (served) |
| task.cmd_task_note, cmd_task_memo | memo_add, edit, delete | method: slice b |
| task.cmd_task_board | none (reads) | read |

### team/

| function | writes | state |
|---|---|---|
| accounts.remove_account, remove_preview | clear_cursor, UPDATE agent_state | **gap**: with account.remove |
| ask.ask, ask.withdraw, ask.cancel, ask.resolve, ask._close_replied | requests, events, queue state | inside: what say, ask, chat.withdraw, inbox.answer and inbox.reply run; each of those methods writes its record in the same transaction (slice b made ask and resolve take it) |
| ask._note_nested, ask.note_verb_chain | add_event | inside: events about a command's own process chain, written where the command is served |
| spawn.spawn, spawn.start_in | set_terminal, open_terminal | inside: agent.create's start step and session.start |
| trust.propose, trust.apply | open_decision, settle_moot | **gap**: trust.propose, trust.list, trust.remove |

### ui/

| function | writes | state |
|---|---|---|
| chat_page assign, done, queue, reopen, discard, edit_brief (and their `answered`/`write`/`work` callbacks) | assign_task, mark_task_done, set_queued, reopen_task, discard_task, edit_task | method: task.edit (owner), slice c |
| chat_page.send_now, cancel_message | actions.send_now, cancel_plan | **gap**: no method for either; chat slice 2 |
| chat_page.start_send and the send work | send_reply | **gap**: chat.send; chat slice 2 |
| chat_page.refresh_block, refresh_agent_state, refresh_conversation, show_message, run_shell | none (screen state, history file) | read |
| components.account_actions._account_remove | remove_account | **gap**: with account.remove |
| components.row_actions._read_for_you | mark_read | method: inbox.mark_read (slice b) |
| components.tasks_view merge | merge_tasks | method: task.merge (slice c) |
| pages.account._load, pages.home._fetch | none | read |

## What is left

No writer is unnamed. The gaps are: account.remove, unstall, cancel_plan, send_now, chat.send and the rest of chat slice 2,
inbox.get (with its dismissal), task.begin/start/handoff/progress, say/ask/land/hr/trust/agent.* of the agent surface, rehome.
None of them has a method this part could serve except those the agent-surface and chat tasks already carry;
**account.remove, unstall, cancel_plan and send_now have no method in the spec at all**, so a decision on their names comes first.
`hl hook` has a method and no agent-role test of the command (it takes its event on stdin from the harness); `hl wake` is in the spec and not in the CLI.
