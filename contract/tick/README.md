# contract/tick: the differential tick harness

The daemon's tick is a function of three things: the database, the clock and the terminals. A fixture is those three, written down; the output is what the tick did with them. Any daemon that gives the same output for every fixture behaves the same, which is how the Go daemon is held to the Python one.

    python3 contract/tick/pyrun.py FIXTURE.json > out.json          run one fixture through the Python daemon
    python3 contract/tick/compare.py "CMD_A" "CMD_B" [FIXTURE ...]   run both on the same fixtures, report differences
    env -u OCP_AGENT -u HANDLINE_AGENT python3 contract/tick/scenarios.py [--check]   rebuild (or check) fixtures/

`compare.py` runs each command as `CMD FIXTURE.json`, expects the output JSON on stdout, prints SAME or DIFF per fixture with the differing paths, and exits 1 on any difference. The Python daemon's command is `python3 contract/tick/pyrun.py`.

## Fixture (`fixtures/NN-name.json`)

- `name`, `source` (the test or history it came from), `what` (one line).
- `clock`: `start` (epoch seconds), `step_s` between ticks, `ticks` to run. Everything that reads time reads this clock; sleeping moves it and nothing waits.
- `files`: the home directory's files by relative path (`team.yaml`, `agents/*/agent.yaml`, status files, handoffs); `{HOME}` stands for the home. Sealed files are sealed on load. `dirs`: empty directories.
- `db`: every table's rows (`__rowid` keeps the row id) inserted before the first tick.
- `processes`: the process table, `[{pid, agent, account?, terminal?, command?}]`; `null` means it cannot be read.
- `logged_in`: account to bool (default logged in). `commands`: the subprocess calls the tick may make, `[{argv, rc, out, err}]`, matched by argv prefix, first match wins; any call not listed is refused and reported under `unscripted`.
- The terminal host: `host_up`, `terminals` (handle to `{screen: [rows], idle?, unreadable?, refuse_send?, on_send?: [{text|contains, screen?, starts_session?, ends_session?, repeat?}]}`), `new_terminals` (handles, or `{handle, screen, on_send, starts_session}`, handed out in order to `create_terminal`; `starts_session` adds a process-table row and takes the launch's prompt file as `hl launch` does), `new_worktrees` (paths `create_worktree` hands out).

## Output

`{format, name, ticks: [{now, actions, db_diff, terminal_ops, log}], unscripted}`. `db_diff` is per table the rows added, removed and the changed columns of kept rows; `terminal_ops` is the ordered operations on the host (`create_terminal`, `send`, `wait_idle`, `close_terminal`, `create_worktree`, `focus`) with their arguments; `unscripted` lists every input the fixture did not provide (empty in all 30). Paths in the output show `{HOME}`.

## Inputs the tick reads that are not db, clock or terminals

Each is injected by `pyrun.py` today and must be injected by any port, or the outputs cannot match:

- The process table (`ps`) and each process's environment (account, agent), and logins (`accounts.logged_in`).
- Subprocess calls: `git` (`rev-parse`, `rev-list`, `status`, `fetch`), `claude auth status --json`, and `claude --model haiku -p hi` (a model call made inside the tick by the usage recovery, `fold.py`).
- Files in the home: status files, `agent.yaml`, `team.yaml`, handoff notes, the wake prompt files; the protected seal store; the lookup of `claude` on PATH.
- Time other than `time.time`: bare `time.strftime`/`localtime` and `datetime.now` (the day rollover in `fold.today`), one `'now'` in a migration's SQL; plus TZ and locale (fixed to UTC and C).
- Randomness: `uuid4` for handoff ids (the harness numbers them).
- Background threads the tick starts (the lands worker, the plan question in `subscriptions.py`, sends threads: `RUN_INLINE`) and state kept across ticks in module variables (`delivery._seen`, `_NOTED`, `_FAILED`, `evaluate._SAID`, `usage_poll._last_rollover_day`, `handoff._OWED_SAID`): a port must keep the same per-process memory or its log lines differ after the first tick.
- The prompt-watch pass (`prompts.py`) is a separate loop, not part of `tick`, so it is not covered here.

## The 30 scenarios and where each came from

Built by `scenarios.py` from the setups of the named tests (the test is the source of the situation, the fixture of the answer); 05 and 06 also come from a founder report (a stale wake_at claimed work that did not exist), 15 and 16 from earlier bug fixes (closed parks that needed a terminal), 12 from the stall decision of 8.6.

| fixture | source | what it shows |
|---|---|---|
| 01-park-at-the-wall | `tests/session/test_daemon_session.py::test_daemon_park` | An agent over its 5-hour limit is parked with a handoff and a wake_at at the reset. |
| 02-due-parked-with-work-resumes | `tests/session/test_daemon_session.py::test_daemon_resume` | A parked agent whose window rolled over returns to QUEUED when work is waiting. |
| 03-expired-handoff-degrades | `tests/session/test_daemon_session.py::test_daemon_tick_expires_handoff` | A handoff past its deadline is failed by the tick, not left wedging the agent in HANDOFF. |
| 04-degraded-due-with-work-recovers | `tests/session/test_daemon_session.py::test_daemon_degraded_recovers` | A due DEGRADED agent with a request waiting goes back to QUEUED or PARKED. |
| 05-throttled-wake-with-nothing-queued-reads-idle | `tests/session/test_daemon_session.py::test_a_stale_wake_at_does_not_claim_work_that_does_not_exist` | A due THROTTLED wake on an empty queue reads IDLE, never QUEUED. |
| 06-throttled-wake-with-a-request-reads-queued | `tests/session/test_daemon_session.py::test_a_stale_wake_at_does_not_claim_work_that_does_not_exist` | The same due wake with a real request waiting reads QUEUED. |
| 07-usage-recovered-unparks-to-idle | `tests/session/test_daemon_session.py::test_the_other_wake_writers_also_read_the_queue` | A parked agent whose account recovered wakes IDLE when nothing is queued. |
| 08-usage-recovered-unparks-to-queued | `tests/session/test_daemon_session.py::test_the_other_wake_writers_also_read_the_queue` | The same recovery wakes QUEUED when a request is waiting. |
| 09-stale-status-is-not-believed | `tests/session/test_daemon_session.py::test_daemon_ignores_stale_status` | A frozen status.json (collector dead, 100000 s old) reading 100% is not a wall: nothing fires. |
| 10-handoff-in-flight-is-left-alone | `tests/session/test_daemon_session.py::test_daemon_tick_expires_handoff` | A handoff whose deadline is still ahead stays in flight; the tick does not fail it. |
| 11-stalled-is-not-cleared-by-the-tick | `tests/session/test_daemon_session.py::test_daemon_stall_decision` | STALLED (repeated degradation) is only cleared by a person, never by a tick. |
| 12-repeated-degradation-raises-stalled | `tests/session/test_daemon_session.py::test_daemon_stall_decision` | A due DEGRADED agent already at the throttle streak limit goes to STALLED, not back to work. |
| 13-parked-stays-parked-on-its-own-context | `tests/session/test_daemon_session.py::test_a_parked_agent_is_unparked_by_its_own_context_limit` | Parked for usage, account recovered, but 200k tokens of context: the default limit keeps it parked. |
| 14-context-override-unparks | `tests/session/test_daemon_session.py::test_a_parked_agent_is_unparked_by_its_own_context_limit` | The same agent with its own context_tokens override of 10M is under its limit and un-parks. |
| 15-due-park-with-no-terminal-is-started | `tests/session/test_daemon_session.py::test_a_due_park_with_no_terminal_is_started_not_held` | A reboot leaves a closed park, due, with work waiting and no terminal: the tick gives it a terminal. |
| 16-closed-park-wakes-in-a-new-terminal-resuming | `tests/session/test_daemon_session.py::test_a_parked_closed_agent_without_a_pane_is_woken_at_its_deadline` | A closed park at its deadline, with a worktree and a told request, is woken into a new terminal with --resume. |
| 17-unreadable-process-table-refuses-to-type | `tests/session/test_daemon_session.py::test_a_wake_refuses_to_type_when_the_process_table_is_unreadable` | With the process table unreadable the daemon cannot tell a session from a shell: it types nothing. |
| 18-idle-agent-with-work-is-typed-at | `tests/session/test_daemon_session.py::test_a_running_agent_is_told_when_work_arrives` | An IDLE agent on a live session with a request waiting is handed the work on its terminal. |
| 19-mid-turn-terminal-is-told-at-once | `tests/session/test_daemon_session.py::test_a_running_agent_is_told_when_work_arrives` | The same agent while its terminal is mid-turn (never idle) is still told about the request at once. |
| 20-nothing-waiting-nothing-typed | `tests/session/test_daemon_session.py::test_a_running_agent_is_told_when_work_arrives` | An IDLE agent on a live session with an empty queue is left alone. |
| 21-closed-agent-with-work-is-resumed | `tests/session/test_daemon_session.py::test_a_closed_session_closes_its_pane_and_the_wake_opens_a_new_terminal_that_resumes` | A closed agent (not parked) with a request waiting is woken in a new terminal that resumes its session. |
| 22-closed-agent-with-nothing-waiting-stays-closed | `tests/session/test_daemon_session.py::test_a_closed_session_closes_its_pane_and_the_wake_opens_a_new_terminal_that_resumes` | A closed agent with an empty queue is not woken. |
| 23-process-on-the-wrong-account | `tests/session/test_daemon_session.py::test_the_sweep_corrects_what_it_can_see` | The process table shows the session running under beta while it is recorded on alpha: the tick corrects the record. |
| 24-context-full-hands-off-and-clears | `tests/session/test_daemon_session.py::test_a_context_handoff_does_not_thrash_within_min_handoff_interval_s` | A running agent whose context passed its limit is asked for a handoff and its context cleared. |
| 25-context-handoff-too-soon-is-skipped | `tests/session/test_daemon_session.py::test_a_context_handoff_does_not_thrash_within_min_handoff_interval_s` | The same agent whose last handoff was a minute ago is not folded again (MIN_HANDOFF_INTERVAL_S). |
| 26-a-live-session-is-told-whatever-the-cap | `tests/session/test_daemon_session.py::test_daemon_resume` | With active_cap 1 and one agent mid-turn on the account, a second agent on a live session is still told about its request: the cap limits starts, not messages. |
| 27-degraded-not-yet-due-waits | `tests/session/test_daemon_session.py::test_daemon_degraded_recovers` | A DEGRADED agent whose wake_at is still ahead keeps waiting even with a request queued. |
| 28-schedule-fires-at-the-next-due-minute | `tests/daemon/test_schedules.py::test_a_due_schedule_is_typed_once_and_waits_for_a_running_agent` | An every-minute schedule is first seen at tick 1 (nothing sent); the next minute boundary, three ticks on, sends its message once. |
| 29-schedule-not-due-is-quiet | `tests/daemon/test_schedules.py::test_a_due_schedule_is_typed_once_and_waits_for_a_running_agent` | A schedule that never comes due (31 February) types nothing. |
| 30-schedule-fires-per-due-minute-not-per-tick | `tests/daemon/test_schedules.py::test_a_due_schedule_is_typed_once_and_waits_for_a_running_agent` | Six ticks 30 s apart span two or three minute boundaries: the message is sent at each, not at every tick. |

Not yet covered, for a later round: the notice digest (idle, busy, closed), task holds, and the LIMIT_DIALOG key press on a parked agent.
