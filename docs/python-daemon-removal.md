# Removing the Python daemon

Survey of main `643696b7`, by walking the imports. The Go daemon is the one that runs. `handline/daemon` is 72 modules,
15,361 lines. 53 of them are imported by nothing outside the package (the `rpc_*` methods, `socket_server`, `stream`,
`termchannel`, `timebox`, `usage_poll` and the like): **delete**. The other 19 (6,934 lines) are imported by `cli/`,
`actions/`, `session/`, `team/`, `view/`, `ui/` or `infra/`, and are listed below.

The Go `hl` runs `account`, `auto-accept`, `close`, `dev`, `dev-mode`, `hook`, `idle-background`, `idle-timeout`,
`ignore-cache-expiry`, `launch`, `notice-digest`, `progress`, `restart`, `say` and `task` itself and hands every other
command to the Python `hl`. So the Python `hl` still runs, and several of its commands do the daemon's work in their own
process beside the Go daemon. Those are the **switch** rows: the module goes once the command calls the Go method.

- **keep**: `hl` needs it as it is; it moves out of `handline/daemon` unchanged.
- **move**: part of it is a local helper `hl` needs; that part moves, the rest is deleted.
- **switch**: `hl` uses it to do daemon work itself; the command calls the Go method named, then the module is deleted.
- **delete**: only the Python daemon's own paths reach it.

| module | lines | used outside the package by | verdict | what happens |
|---|---|---|---|---|
| `client` | 155 | 7 `cli/` files, `session/events`, `session/landgate`, `team/ask`, `actions/prompt_blocks` | keep | The socket client for JSON-RPC and the legacy verbs. Moved whole to `handline/infra/daemonclient.py`, not `handline/cli`: `session/`, `team/` and `actions/` call it too, and importing anything under `handline/cli` loads the whole command parser. |
| `locks` | 80 | `cli/daemon`, `infra/daemonlock`, `infra/doctor` | move | Moved: `running_elsewhere` and `DAEMON_LOCK` are `handline/infra/daemonlock.py`. `processes` stays for step 4; it finds only a Python daemon, for `hl daemon status`, `hl doctor` and a lock holder that wrote no pid. |
| `states` | 167 | - | move | Moved: the state words, `park_wakeable`, `STUCK_RUNNING_S` and `IDLE_CLOSE_S` are `handline/session/states.py`. The other timing constants are the daemon's. |
| `schedules` | 120 | `cli/schedule`, `actions/schedule`, `team/hr` | move | Moved: `parse`, `load`, `of`, `sid` are `handline/team/schedules.py`. `sweep`, which fires them, stays for step 4; only the Python tick calls it. Go serves `schedules.list` and `schedule.add`, so `hl schedule` can switch later. |
| `devmode` | 174 | - | move | Moved: `dev_mode_enabled` and `set_dev_mode` are `handline/infra/devmode.py`. The file watcher that restarts the Python daemon stays for step 4 (Go has `tick/devmode.go`). `hl doctor --drop-dead-columns` no longer asks whether the daemon is older than the Python files on disk: the Go daemon names none of those columns. |
| `lifecycle` | 1,419 | `actions/control`, `actions/stuck`, `session/blocked`, `session/launch`, `team/ask` | switch | Done: `hl resume` calls `session.wake`, `hl triggers` reads `agent.triggers`, `hl status` reads `status.pick`, and `hl ps` and `hl team` read `wake_wait_reason` of `agents.list` with `status: true`. What still imports it is reached only by the Python daemon. |
| `handoff` | 681 | `actions/control` | switch | Done: `hl agent move` calls `agent.move`. |
| `delivery` | 1,531 | `actions/stuck`, `session/blocked`, `session/handoff`, `session/stopgate`, `team/ask`, `team/spawn` | switch | Done: `hl read` prints `request.read`; `hl inbox` prints `inbox.queue` and marks the item it shows read with `inbox.mark_read`. `hl withdraw` calls `request.withdraw` and `hl cancel` calls `request.cancel`, with a dry run for the plan it confirms. What still imports it is reached only by the Python daemon. |
| `taskholds` | 170 | - | switch | Done: `hl team` reads why a task waits from `task_waits` of `agents.list` with `status: true`. |
| `screenstate` | 193 | - | delete | The word a row shows. `hl ps` and `hl team` read `screen_word` of `agents.list` with `status: true`. Done: `handline/ui` and the functions of `viewdata` that read it are deleted; `viewdata` keeps what `hl account list` and `hl daemon` draw. |
| `agentview` | 195 | - | switch | As `screenstate`. |
| `handoff_note` | 220 | `actions/details`, `session/handoff` | delete | The on/off toggle is `handoff_note` of the Go `settings.get` and `settings.set`; the note itself is Go's (`go/internal/handoff`). Both importers are reached only by the Python daemon. |
| `landing` | 172 | `actions/stuck` | delete | `state` reads a session's transcript to say whether a request landed: the Go daemon records `landed_at`, which the chat reads carry. Done: `agent_detail` and the conversation it drew are deleted. `actions/stuck` stays until step 4: the Python daemon's `agentview`, `taskholds` and `chat.list` call it, and so does `team/ask`'s push. |
| `spool` | 112 | `session/events` | delete | The Go `hl hook` and `go/internal/hooks/spool.go` write and read the spool. Done: `hl doctor` takes the two file names from `handline/infra/paths.py`. `session/events` is the Python `hl hook`, which the Go `hl` no longer runs. |
| `inputhold` | 100 | `actions/control`, `session/autoaccept` | delete | Who may type at a terminal: only the daemon asks. |
| `messaging` | 245 | `team/ask` | delete | `tell_now` types a message at once; `say` is the Go `hl`'s. |
| `evaluate` | 608 | `team/ask` | delete | The tick. `team/ask` uses one helper of it after a request closes, which no command reaches now (see `delivery`). |
| `reconciler` | 412 | - | delete | The startup pass. Done: `hl doctor` works out an agent left RUNNING with nothing heard itself, from the db, since it has to run with the daemon down. |
| `loop` | 180 | `cli/daemon` | delete | `hl daemon` runs the Python daemon: the command goes with it. |

The task that asked for this named 13 modules; `agentview`, `evaluate`, `messaging`, `reconciler` and `loop` are also
imported from outside, and `cmd_daemon*`, `cmd_dev_mode` are names of `cli/daemon.py`, not daemon modules.

## Order

1. Done with this survey: `hl service use python` is refused, by the command and by the proposal path in both daemons.
2. Done: the keep and move rows are out of `handline/daemon` (see their rows). What is left of `locks`, `states`,
   `schedules` and `devmode` there is deleted in step 4.
3. Switch the commands, one method at a time. Done: `hl triggers`, `hl status`, `hl ps`, `hl team`, `hl resume`,
   `hl agent move`, `hl read`, `hl inbox`, `hl cancel`, `hl withdraw`; `hl doctor` no longer imports the package's
   `reconciler` or `spool`. Every import left in the table is reached only by the Python daemon or by code no
   command runs (the Python `hl hook`). Deleted: `handline/ui` and the half of `view/viewdata` only it and the
   tests read.
4. Delete `handline/daemon`, `hl daemon`, and the tests that only exercise it: 65 test files import the package, 48 of
   them in `tests/daemon`.

## Open

- `service.daemon_impl()` still reads an unset `daemon_impl` as python, so `hl service install` on a home that never ran
  `hl service use go` installs the Python daemon. Step 4 makes go the only value.
- `actions/`, `session/`, `team/ask` and `view/` are shared by the Python daemon and the Python `hl`. Which of their
  functions only the daemon calls is not in this list; step 3 finds that per command.
