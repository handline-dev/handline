# Go handoff and move for structured agents

Status: design for review, then the port. Python (`daemon/handoff.py`, `delivery._reclaim_context`) is the spec for WHEN; only HOW changes.

Today the Go tick calls `tick.Handoff`, and `compose/tick.go` serves `PerformHandoff`, `MoveToRoom` and `SettleOwedClear` with `false`, and
`MovePlan` with "stay". A structured agent at the 5h wall or a cache edge is therefore not handed off or moved.

## What survives (same conditions, same order)

- `PerformHandoff`: an open handoff row merges the new trigger as a reason; the too-soon guard (`MinHandoffIntervalS`, not for `forced`/`login`);
  a mid-turn agent defers to the next tick; the note hold (`handoff.Env.Hold`) except for `forced`, `login` and a settled limit stop; then the
  row (`StartHandoff`), state HANDOFF, `handoff.Record`, step `extracted`.
- `MovePlan`/`MoveToRoom`: only for `MOVABLE_TRIGGERS`; `stay_or_move`, never back to the account just left before it resets, the room test of
  `pick_account` (a forced move is held to the same test), `moved_account` event, `set_account`, cold-start measure.
- After a failed move, a forced move restores the prior record and goes IDLE; any other trigger parks. `_park` (wake time per trigger, assumed
  account and `retry_soon` get the short recheck, backoff on the degraded streak, `park` event, `continuations`).
- `ResumeHandoff` after a restart (steps requested/recorded/extracted/released) keeps its table.
- `SettleOwedClear`: the clear a background job holds off is owed, the agent is told once, and it happens when the work ends or after
  `ClearWaitS`; a lapsed cache clears at once; a forced-through clear appends the "cut off" section to the record.

## What changes

| Python | Structured |
|---|---|
| terminal lock, input hold, `_type_gate`, `_past_dialog` | `AgentLocks` for the act; mid-turn is `Runner.Busy`; an open permission prompt (`requires_action`) is a turn, so it defers |
| `session_present` | `Runner.Alive` |
| note asked by typing | already `Env.Ask` through the send path (S4, op id per ask) |
| `/exit`, keep the pane | `endStructured` with no resume id: `Runner.Close` errors while any process of the group lives, and that is the proof; a refused close is `retry_soon` |
| `/clear` (`_reclaim_context`) | the same close, then `ForgetResume`: the park's wake starts a fresh session whose first message is the handoff. There is no key to type, so a cleared context is a closed session |
| background work holds the clear | `Delivery.HasBackgroundWork` holds the close; notice sent as a message (op `clear_notice:<n>`); the `clear_owed:<agent>` cursor stays |
| wake on the other account | `WakeAgentLocked` with `MovedTo`, which `launchStructured` hands to `Sessions.Start` as the account |
| process on the new account | `Host.RunningAccount`; "" never contradicts |
| `fold`/`rollover` | stays a subprocess (`Sweeps.Rollover`, `hl fold`); only checked to be wired, not ported |

## Idempotence

The note ask has an op id per ask (exists). The handoff text is sent once by `wakeStructured`/`launchStructured` with a fresh `start:`/`wake:` id
per wake; a repeat of `PerformHandoff` finds the open row and merges. The move is closed-then-started; if the start fails the agent is closed with
its handoff kept, so the existing closed-agent wake path brings it back (the Python `closed_at` rule).

## Dropped (stated)

`_past_dialog`/`_stalled_on_dialog` and the settle window after typing (`_mark_typed`): nothing is typed. `term.alive`/`term.available`: no pane.

## Tests (`go test -race`, the fake CLI)

1. at the wall: asked for its note once, closed, moved, woken on the other account with the handoff text; sends counted by op id, no double.
2. mid-turn defers, then goes next tick; busy through a permission prompt defers.
3. close refused: stays, `retry_soon` park. Start fails after close: closed with record kept, woken by the tick.
4. forced move: room test refusal moves nothing; a failed move restores the record and IDLE.
5. context trigger: close + fresh wake on the same account, PARKED with immediate wake.
6. owed clear with a background job: notice once, held, settled at job end and at `ClearWaitS`.
7. resume after restart from each step.

## Settled after the auditor's review

1. **Figures.** `tickUsage.ReadForAgent` reads a status file a structured session never writes, so the context trigger, the note at the cache edge and
   the cost pilot would be inert. This task adds the meter: for a structured agent `ReadForAgent` fills `TrustedCtxUsed` from the session's
   `get_usage` (S3) and `TranscriptPath` from the S5 transcript, with the stored session id. A test drives the context trigger (test 5) from it,
   so the port is not correct-and-inert.
2. **RunningAccount.** `tickHost.RunningAccount` returns the account of the agent's last `Open` (the daemon chose the config dir), recorded by
   `Sessions.Start`; "" only when the agent was never opened since the daemon began.
3. **One judge of room.** `MovePlan` calls the same test as `wakeClosed`: `Launcher.full` (others mid-turn plus older closed waiters, own account left
   out) through `Launcher.SlowestPaced`/`pick`, not a second count. The exclusions (the account it is at the wall on, the one it just left until it
   resets) are added on top; the room verdict is not re-derived.
4. **A defer is not a degraded handoff.** Mid-turn, a held note, the too-soon guard, a held lock and an open prompt return a "deferred" result that the tick
   reads as no handoff: no `throttle_count`, no `failed_attempts`, no "-> degraded" action. Only a record that could not be read, a clear that
   failed, or a close that was refused bumps the streak. Test: a deferred handoff three ticks running leaves the counters at zero.
5. **Open permission prompt.** `requires_action` defers for `usage`/`context`/cache triggers and the agent's own request is told once (it is a turn the
   person or agent must answer). For `forced` and `login` the act denies the prompt with the interrupt (`Runner.Interrupt`), waits for idle
   (`WaitIdle`, `StructuredIdleWait`), and proceeds; if the turn does not end it answers "mid-turn" as a refusal to the person.
6. **Ledger on close.** `Runner.Send` returns only after the CLI echoed the message, so the note ask and the clear notice are confirmed before the close
   runs; the only sends `SessionLost` could mark unknown are ones whose echo timed out, which are unknown on any close. Held messages are given
   back as on any close. No suppression is added.
7. **Seams.** The three stubs in `compose/tick.go` and the reconciler's "resume a handoff" stub are served. `agentview.RefreshOne` and binding its `Agents`
   are not done here: `agentview.Env` is not constructed in compose on this base, so there is nothing to call; the composition-root task wires both
  .

Added tests: a deferred handoff three ticks running leaves the counters at zero; a move never picks the account it is leaving nor one whose slot an
older waiter holds; the context figure of a structured agent reaches the trigger; `RunningAccount` contradicts a process left on the old account.
