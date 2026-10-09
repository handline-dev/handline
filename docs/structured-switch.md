# Switch one agent to a structured session

How to move one agent, `<agent>` (the first structured agent), from a terminal session to a structured one while the Python daemon keeps every other agent.
Commands run from the repo root. `<home>` is the home, normally `~/.handline`.

**Status: do not run this yet.** The scratch trial below works, but four gaps stop a real session:
the installed claude is 2.1.295 and the Go daemon accepts only 2.1.294; there is no singleton, so two Go processes launch it twice;
nothing can start, stop, interrupt or answer a structured agent from `hl` or the apps; there is no permission policy, so every tool call waits for a person.
Three smaller gaps (Python's hook record writes its state, the Go task clock covers every owner, closing while claude lives) are on the board. Fix the four, then follow this.

## The mixed mode

One extra process beside the Python daemon, which keeps the socket, the gateway and every terminal agent:

    go/handlined -home <home> -allow-real-home -socket <home>/structured.sock -structured-cli <claude> -structured-tick

- **No `-cutover`.** Without it the tick writes only the rows of structured agents (`agent_view`, state, transcript) and every usage writer is a no-op
  (`compose/tick.go` `tickUsage.own`, `tickObserve.own`: burn file, `usage_samples`, token log). With it the Go tick owns the usage poll, every `agent_view` row and the stream rows, which Python also writes.
  The `-structured-tick` flag text ("the Python daemon must not tick beside it") is true only with `-cutover`.
- **A socket of its own.** `daemon.sock` is Python's: Go refuses to start when something answers there (`daemon.listenUnix`). `hl` knows only `daemon.sock` (no route yet).
- **No `-gateway`, no `-own-prompts`.** The second takes `locks/daemon.lock`, which Python holds.
- **Same checkout for both.** Go migrates `db.sqlite` to its own latest schema (`store.Open`). Build it with `tools/build-go.sh` from the commit Python runs.
- Python leaves the agent alone when `agent.yaml` has the top-level line `structured: true` (`team/agents.py is_structured`: tick, delivery, wake, close, handoff, view row, task clock; `tests/daemon/test_structured_skip.py`).
  The key is `structured`, not `mode` (that is the permission mode).

## Switch

1. Close the agent in Python while it is idle, so the row is closed with its session id and no claude runs in its pane (the pane stays, an empty shell):

       hl close <agent>
       python3 -c "import sqlite3;print(sqlite3.connect('<home>/db.sqlite').execute(\"select state,terminal,closed_at,resume_session from agent_state where agent='<agent>'\").fetchall())"

   Do this before the `structured:` line goes in: an agent switched with its terminal still open is held, and the stuck panel and the chat read "switched with its terminal open". The way out: remove the line, close it, put the line back. Wait for `closed_at` to be set. (Go would otherwise resume the same session id beside a live claude, a gap on the board.) Whether the leftover `terminal` handle in the row bothers Go was not tried: the scratch rows had none.
2. Install the process as a second launchd job. Copy the `EnvironmentVariables` of `~/Library/LaunchAgents/dev.orcapipe.daemon.plist` (PATH must reach `ocp`/`hl`), then:

       ~/Library/LaunchAgents/dev.orcapipe.structured.plist:
       Label dev.orcapipe.structured · RunAtLoad true · KeepAlive true · ThrottleInterval 30
       ProgramArguments: <repo>/go/handlined -home <home> -allow-real-home -socket <home>/structured.sock
                         -structured-cli <claude> -structured-tick
       StandardOutPath, StandardErrorPath: <home>/logs/structured.log
       launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/dev.orcapipe.structured.plist

   Never start it twice, by hand or by launchd: nothing stops that (a gap on the board). `hl service` knows only the Python job.
3. Add `structured: true` as its own top-level line to `<home>/agents/<agent>/agent.yaml`. The Go process reads the file each tick; nothing restarts.

## See it works

- `structured.log`: `handlined <sha> serving`, then on work `<agent>: started with its wake prompt` (a wake takes up to one tick, 30 s).
- `hl say <agent> "ping"`: queued by Python, delivered by Go. `transcript_events` for the agent gains `turn_start`, `text`, `turn_end`;
  `agent_view` for the agent reads `idle` or `running`, not `no_terminal`, once a session is open; `session_starts` has a row.
- Python is hands-off: `daemon.err.log` has no wake or delivery line.
- Leave a terminal agent in the same run: its state, `agent_view` row and typing must not change.

## Way back

1. Stop the Go job: `launchctl bootout gui/$(id -u)/dev.orcapipe.structured`. Its claude child loses stdin and ends.
2. Delete the `structured:` line from the agent's `agent.yaml`.
3. Python then sees a closed agent with a session id and wakes it in a terminal with that session on its next work. Nothing else needs undoing.
   Left behind and harmless: `<home>/structured/<agent>/settings.json` (rewritten on the next structured start), `transcript_events` rows (additive; Python never reads them), the `agent_view` row (Python's view pass rewrites it), `structured.sock` (removed at the next Go start).
   No lock files are created. A queued request stays queued.

## Trial (scratch home, fake CLI, over socketpairs: the sandbox binds no socket)

Checked, with the flags above and no `-cutover`: a task sent to it (`chat.send` with `task`) woke the closed session at once and the fake ran two turns (wake prompt, then the task);
`transcript.events` and `agent_view` filled; `task.done` closed the task; the daemon stopped and started again, recorded the session gone with its resume id, and the next message woke a fresh session in at most one tick;
`--settings` carried the hooks of ours from the shared settings (PreToolUse guard and Stop; run on the real `shared/.claude/settings.json`, `HooksSettings` kept all 12 `ocp hook` events), with `--setting-sources project`;
a terminal agent beside it kept its state and got no view row; `usage_samples`, `token_calls` and `stream_log` stayed empty; Python's skip tests leave it alone; two Go processes double-launch (a gap on the board).

The fake cannot show: a real permission prompt stream (a gap), the 2.1.295 control types (a gap), context and cache clocks (the fake has no transcript file: the cold-wake note says `degraded`),
hooks reaching the Python daemon over its socket (a gap), a closed row that still holds a `terminal` handle, the real worktree and account choice, usage limits and handoff, resume of a real session id, and the Python daemon running beside it (it could not bind a socket here).
