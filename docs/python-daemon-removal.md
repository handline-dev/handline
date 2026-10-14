# Removing the Python daemon

Survey of main `643696b7`, by walking the imports. The Go daemon is the one that runs. `handline/daemon` is 72 modules,
15,361 lines. 53 of them are imported by nothing outside the package (the `rpc_*` methods, `socket_server`, `stream`,
`termchannel`, `timebox`, `usage_poll` and the like): **delete**. The other 19 (6,934 lines) are imported by `cli/`,
`actions/`, `session/`, `team/`, `view/`, `ui/` or `infra/`, and are listed below.

The Go `hl` runs `account`, `auto-accept`, `close`, `dev`, `dev-mode`, `idle-background`, `idle-timeout`,
`ignore-cache-expiry`, `launch`, `notice-digest`, `progress`, `restart`, `say` and `task` itself and hands every other
command to the Python `hl`, `hook` included (see [The Python hook](#the-python-hook)). So the Python `hl` still runs,
and several of its commands do the daemon's work in their own process beside the Go daemon. Those are the **switch**
rows: the module goes once the command calls the Go method.

- **keep**: `hl` needs it as it is; it moves out of `handline/daemon` unchanged.
- **move**: part of it is a local helper `hl` needs; that part moves, the rest is deleted.
- **switch**: `hl` uses it to do daemon work itself; the command calls the Go method named, then the module is deleted.
- **delete**: only the Python daemon's own paths reach it.

| module | lines | used outside the package by | verdict | what happens |
|---|---|---|---|---|
| `client` | 155 | 7 `cli/` files, `session/events`, `team/ask`, `actions/prompt_blocks` | keep | The socket client for JSON-RPC and the legacy verbs. Moved whole to `handline/infra/daemonclient.py`, not `handline/cli`: `session/`, `team/` and `actions/` call it too, and importing anything under `handline/cli` loads the whole command parser. |
| `locks` | 80 | `cli/daemon`, `infra/daemonlock`, `infra/doctor` | move | Moved: `running_elsewhere` and `DAEMON_LOCK` are `handline/infra/daemonlock.py`. The rest is deleted. |
| `states` | 167 | - | move | Moved: the state words, `park_wakeable`, `STUCK_RUNNING_S` and `IDLE_CLOSE_S` are `handline/session/states.py`. The other timing constants are the daemon's. |
| `schedules` | 120 | `cli/schedule`, `actions/schedule`, `team/hr` | move | Moved: `parse`, `load`, `of`, `sid` are `handline/team/schedules.py`. Go serves `schedules.list` and `schedule.add`, so `hl schedule` can switch later. The rest is deleted. |
| `devmode` | 174 | - | move | Moved: `dev_mode_enabled` and `set_dev_mode` are `handline/infra/devmode.py`. `hl doctor --drop-dead-columns` no longer asks whether the daemon is older than the Python files on disk: the Go daemon names none of those columns. The rest is deleted. |
| `lifecycle` | 1,419 | `actions/control`, `actions/stuck`, `session/blocked`, `session/launch`, `team/ask` | switch | Done: `hl resume` calls `session.wake`, `hl triggers` reads `agent.triggers`, `hl status` reads `status.pick`, and `hl ps` and `hl team` read `wake_wait_reason` of `agents.list` with `status: true`. What still imports it is reached only by the Python daemon. |
| `handoff` | 681 | `actions/control` | switch | Done: `hl agent move` calls `agent.move`. |
| `delivery` | 1,531 | `actions/stuck`, `session/blocked`, `session/handoff`, `team/ask`, `team/spawn` | switch | Done: `hl read` prints `request.read`; `hl inbox` prints `inbox.queue` and marks the item it shows read with `inbox.mark_read`. `hl withdraw` calls `request.withdraw` and `hl cancel` calls `request.cancel`, with a dry run for the plan it confirms. What still imports it is reached only by the Python daemon; `wall_reason` is `session/stopgate`. |
| `taskholds` | 170 | - | switch | Done: `hl team` reads why a task waits from `task_waits` of `agents.list` with `status: true`. |
| `screenstate` | 193 | - | delete | The word a row shows. `hl ps` and `hl team` read `screen_word` of `agents.list` with `status: true`. Done: `handline/ui` and the functions of `viewdata` that read it are deleted; `viewdata` keeps what `hl account list` and `hl daemon` draw. |
| `agentview` | 195 | - | switch | As `screenstate`. |
| `handoff_note` | 220 | `actions/details`, `session/handoff` | delete | The on/off toggle is `handoff_note` of the Go `settings.get` and `settings.set`; the note itself is Go's (`go/internal/handoff`). Both importers are reached only by the Python daemon. |
| `landing` | 172 | `actions/stuck` | delete | `state` reads a session's transcript to say whether a request landed: the Go daemon records `landed_at`, which the chat reads carry. Done: `agent_detail` and the conversation it drew are deleted. `actions/stuck` stays until step 4: the Python daemon's `agentview`, `taskholds` and `chat.list` call it, and so does `team/ask`'s push. |
| `spool` | 112 | `session/events` | move | The Go daemon reads the spool (`go/internal/hooks/spool.go`). Done: `hl doctor` takes the two file names from `handline/infra/paths.py`. Moved: the Python `hl hook` writes it with `infra/hookspool.append`. `ingest` and `pending` are the Python daemon's. |
| `inputhold` | 100 | `actions/control`, `session/autoaccept` | delete | Who may type at a terminal: only the daemon asks. |
| `messaging` | 245 | `team/ask` | delete | `tell_now` types a message at once; `say` is the Go `hl`'s. |
| `evaluate` | 608 | `team/ask` | delete | Deleted. `team/ask` writes the requester's QUEUED or IDLE itself after a request closes. |
| `reconciler` | 412 | - | delete | Deleted. `hl doctor` works out an agent left RUNNING with nothing heard itself, from the db, since it has to run with the daemon down. |
| `loop` | 180 | `cli/daemon` | delete | Deleted with `hl daemon`. |

The task that asked for this named 13 modules; `agentview`, `evaluate`, `messaging`, `reconciler` and `loop` are also
imported from outside, and `cmd_daemon*`, `cmd_dev_mode` are names of `cli/daemon.py`, not daemon modules.

## Order

1. Done with this survey: `hl service use python` is refused, by the command and by the proposal path in both daemons.
2. Done: the keep and move rows are out of `handline/daemon` (see their rows). What is left of `locks`, `states`,
   `schedules` and `devmode` there is deleted in step 4.
3. Switch the commands, one method at a time. Done: `hl triggers`, `hl status`, `hl ps`, `hl team`, `hl resume`,
   `hl agent move`, `hl read`, `hl inbox`, `hl cancel`, `hl withdraw`; `hl doctor` no longer imports the package's
   `reconciler` or `spool`. Every import left in the table is reached only by the Python daemon (see
   [The Python hook](#the-python-hook)). Deleted: `handline/ui` and the half of `view/viewdata` only it and the tests
   read.
4. Delete `handline/daemon`, `hl daemon`, and the tests that only exercise it.
   - a. Done: `hl daemon` and the tick (`loop`, `evaluate`, `reconciler`, `usage_poll`, `devmode`, `prompts`,
     `applysettings`, `costpilot`, `login_expired`, `noticedigest`, `schedules`, `subscriptions`, `taskqueue`, `waitfor`,
     `locks`) with their tests. `hl service install` installs only the Go daemon. `tools/gateway_smoke.py` runs on the
     Go daemon.
   - b. Done: the RPC layer (`rpc*`, `socket_server`, `stream`, `sends`, `relay`, `fds`, `spool`, `taskholds`,
     `timebox`, `actions/chatreads`, `events.apply_hook`) and the Python fake of the daemon (`tests/infra/fake_methods`;
     conformance runs against Go only). The tests of commands the Go `hl` runs went with it; the rest get the Go
     method's reply canned (`support.daemon_says`). 1,362 Python tests before, 954 after.
   - c. What is left, 13 modules, 5,799 lines, all reached from the Python `hl` through `actions`, `session` and
     `team`: `delivery` (`actions/stuck`, `session/blocked`, `session/handoff`, `team/ask`, `team/spawn`),
     `lifecycle` (`actions/control`, `actions/stuck`, `session/blocked`, `session/launch`, `team/ask`), `handoff`
     (`actions/control`), `handoff_note` (`actions/details`, `session/handoff`), `inputhold` (`actions/control`,
     `session/autoaccept`), `landing` (`actions/stuck`), `messaging` (`team/ask`); `agentview`, `screensamples`,
     `screenstate`, `states`, `termbytes` and `termchannel` only through those. Each goes when the command that
     reaches it stops doing the daemon's work in its own process.

## The Python hook

Claude Code runs `ocp hook <event>` for 12 events. The Go `hl` answers `PreToolUse`, `PostToolUse` and
`PostToolUseFailure` itself, and only once `hl go hook on` is set (it is off). Every other event, and those three while
it is off, run `session/events.record_from_stdin` in the Python `hl`.

What the Python path does with the Go daemon running. *Record* is `hook.record` on the Go daemon, or a spool line when
it does not answer within 2 s (`infra/hookspool.append`); the Go daemon ingests the spool and applies both
(`go/internal/hooks`). *Snapshot* is `usage/collect.collect`: it reads the transcript and writes
`agents/<name>/status.json`, which Go only reads (`go/internal/usage/read.go`).

| event | Python `hl hook` | Go `hl` today |
|---|---|---|
| `PreToolUse` | shell guard, edit gate; prints a deny | same, behind the switch |
| `PostToolUse`, `PostToolUseFailure` | clear the permission wait, record; `PostToolUse` also notes memory use and background jobs | same, behind the switch |
| `Stop` | stop gate (`session/stopgate`), then record and snapshot | none |
| `SessionStart`, `UserPromptSubmit`, `StopFailure`, `PostCompact` | record, snapshot | none |
| `SessionEnd`, `SubagentStop`, `PermissionRequest`, `TeammateIdle` | record | none |
| `hl hook commit` | `events.record_commit` straight into the db | none; `handoff.RecordCommit` exists with no caller. Nothing installs a git hook that runs it. |

Done: the hook and its stop gate load no module of `handline/daemon`. `spool.append` is `infra/hookspool`;
`wall_reason` and `stop_wait_reason` are `session/stopgate`, with `LOGIN_TRIGGER` and `MOVABLE_TRIGGERS` in
`session/states`; the proposal reading is `team/proposals`. `tests/session/test_stopgate.py` runs the hook with the
package emptied. What still loads the package on a hook is the command parser, through `cli/daemon` (all 71 files) and
the `actions` package (`control`, `stuck`): step 4's work for every command.

Names in `session/events`. The Python `hl` uses `CAPTURED`, `ANSWERS` and `GUARDS` (`session/launch`, `infra/doctor`,
`cli/hooks`), `TURN_BOUNDARIES` and `AUDIT_KINDS` (`cli/health`, `session/fold`), and `at_limit` (`session/stopgate`).
`record_from_stdin` and `record_commit` are `cli/hooks`'s. `apply_hook` is deleted (step 4b): the Go daemon applies
every hook (`go/internal/hooks`).

Porting the other nine events, `hook commit`, the stop gate and the snapshot writer to the Go `hl` is its own work after
step 4: about 4 pieces, then the switch goes and the Python path is deleted.

## Open

- `actions/`, `session/`, `team/ask` and `view/` are shared by the Python daemon and the Python `hl`. Which of their
  functions only the daemon calls is not in this list; step 3 finds that per command.
