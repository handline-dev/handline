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
| `client` | 155 | 7 `cli/` files, `session/events`, `session/landgate`, `team/ask`, `actions/prompt_blocks` | keep | The socket client for JSON-RPC and the legacy verbs. No import from the package. To `handline/cli`, beside `cli/client.py`. |
| `locks` | 80 | `cli/daemon`, `cli/health`, `cli/service`, `infra/doctor`, `actions/stuck` | move | `running_elsewhere` and `DAEMON_LOCK` to `handline/infra`: the Go daemon holds the same `daemon` lock. `processes` looks for `hl daemon` in the process table and is deleted. |
| `states` | 167 | `actions/control`, `actions/stuck`, `cli/agent`, `session/blocked`, `session/idle_close` | move | The state words and `park_wakeable` to `handline/session`. The tick's timing constants are deleted with their readers. |
| `schedules` | 120 | `cli/schedule`, `actions/schedule`, `team/hr` | move | `parse`, `load`, `of`, `sid` read an agent's schedules from its agent.yaml: to `handline/team`. Firing them is the Go tick's. Go serves `schedules.list` and `schedule.add`, so `hl schedule` can switch later. |
| `devmode` | 174 | `cli/daemon`, `cli/bridge`, `cli/health`, `team/hr` | move | `dev_mode_enabled` and `set_dev_mode` read and write one setting: to `handline/infra`. The file watcher that restarts the Python daemon is deleted (Go has `tick/devmode.go`). |
| `lifecycle` | 1,419 | `cli/agent`, `cli/daemon`, `cli/health`, `cli/team`, `actions/control`, `actions/stuck`, `session/blocked`, `session/launch`, `team/ask`, `view/viewdata` | switch | `hl resume` wakes the agent in its own process: call `session.wake`. `would_wake` and `triggers_apply` feed `hl ps`, `hl status`, `hl team` and `hl triggers`: read `agents.list`, `team.get`. |
| `handoff` | 681 | `cli/agent`, `actions/control` | switch | `hl agent move` moves the agent itself: call `agent.move`. |
| `delivery` | 1,531 | `cli/ask`, `actions/control`, `actions/stuck`, `session/blocked`, `session/handoff`, `session/stopgate`, `team/ask`, `team/spawn`, `ui`, `view/viewdata` | switch | `hl inbox` and `hl read` use three text helpers and mark requests read themselves: call `inbox.list`, `inbox.get`, `inbox.mark_read`. The rest is typing at sessions, the Go daemon's. |
| `taskholds` | 170 | `cli/team` | switch | `agent_waits`, why a task waits, shown by `hl team`: `hl team` reads `team.get`; whether that carries the reason is to check. |
| `screenstate` | 193 | `view/viewdata`, `ui` | switch | The word a row shows, for `hl ps`: read `agents.list`. |
| `agentview` | 195 | `view/viewdata` | switch | As `screenstate`. |
| `handoff_note` | 220 | `actions/details`, `session/handoff`, `ui` | switch | The default on/off toggle is a setting the Go daemon reads; the note itself is Go's (`go/internal/handoff`). |
| `landing` | 172 | `actions/conversation`, `actions/stuck` | switch | `state` reads a session's transcript to say whether a request landed: the Go daemon records `landed_at`, which the chat reads carry. |
| `spool` | 112 | `session/events`, `infra/doctor` | delete | The Go `hl hook` and `go/internal/hooks/spool.go` write and read the spool. `hl doctor` keeps the two path names, in `handline/infra`. |
| `inputhold` | 100 | `actions/control`, `session/autoaccept` | delete | Who may type at a terminal: only the daemon asks. |
| `messaging` | 245 | `team/ask` | delete | `tell_now` types a message at once; `say` is the Go `hl`'s. |
| `evaluate` | 608 | `team/ask` | delete | The tick. `team/ask` uses one helper of it on the Python say path. |
| `reconciler` | 412 | `infra/doctor` | delete | The startup pass. `hl doctor` uses `silent_minutes`; that check goes, or reads `agents.list`. |
| `loop` | 180 | `cli/daemon` | delete | `hl daemon` runs the Python daemon: the command goes with it. |

The task that asked for this named 13 modules; `agentview`, `evaluate`, `messaging`, `reconciler` and `loop` are also
imported from outside, and `cmd_daemon*`, `cmd_dev_mode` are names of `cli/daemon.py`, not daemon modules.

## Order

1. Done with this survey: `hl service use python` is refused, by the command and by the proposal path in both daemons.
2. Move the keep and move rows (`client`, `locks`, `states`, `schedules`, `devmode`). None imports a switch or delete row
   once the deleted parts are cut, so this lands before anything else changes.
3. Switch the commands, one method at a time.
4. Delete `handline/daemon`, `hl daemon`, and the tests that only exercise it: 65 test files import the package, 48 of
   them in `tests/daemon`.

## Open

- `service.daemon_impl()` still reads an unset `daemon_impl` as python, so `hl service install` on a home that never ran
  `hl service use go` installs the Python daemon. Step 4 makes go the only value.
- `actions/`, `session/`, `team/ask` and `view/` are shared by the Python daemon and the Python `hl`. Which of their
  functions only the daemon calls is not in this list; step 3 finds that per command.
