# The stored view of a structured agent

`agent_view` holds one JSON row per agent, which `agents.list` and `agent.get` serve (contract `AgentView`). The Python tick writes it
(`daemon/agentview.py`); nothing in Go does yet. The Go tick writes it for **structured** agents only (`Runner.Structured`). A terminal
agent's stored view is never touched, so the two daemons can share the table while agents move over.

Same table, same JSON, no schema step. The row is a read model: an agent whose view cannot be worked out keeps its last row, and the
others go on.

## Field by field

| Field | Source for a structured agent | Against Python |
|---|---|---|
| `name`, `role` | the agent's name; first non-heading line of its `ROLE.md` | same |
| `model`, `effort` | the agent's config (`team.Parts`) | same |
| `state`, `state_detail` | the `agent_state` row, then the live session (below) | see below |
| `state_since` | `agent_state.updated` | same |
| `block`, `blocker` | the `delivery_blocks` row once its `show_at` has passed | same table; no screen reader opens one, so it is set only by what the daemon itself records |
| `has_dialog` | an open prompt (`prompt_blocks`, fed by the permission broker's `requires_action`) | was "the screen shows a dialog"; now "a prompt is open" |
| `wall_reason`, `background_since`, `would_wake`, `cache_until`, `task`, `account`, `parent` | the same db facts through the Go ports (`supervisor`, `lifecycle`, `handoff`, `team`) | same |
| `ctx_pct` | the agent's own usage snapshot (`usage.ReadForAgent`) | same |

## State word

The word is made in the order Python makes it, from the row, with the session as the witness where the screen was:

1. open prompt -> `waiting` (detail: its headline, 40 chars)
2. row `STALLED` -> `stalled`
3. a restart owed -> `queued` with the retry note
4. row `RUNNING` / `HANDOFF` -> `running`; row `IDLE` / `QUEUED` with the live session mid-turn (S1 `running`) -> `running`
5. `QUEUED` -> `parked` + `moving`, or `idle` + the background note, or `queued`
6. `PARKED` / `DEGRADED` / `THROTTLED` -> `parked` + clock (`due` once passed), or `queued` / `idle` as Python
7. otherwise `idle`, with the background note when the session reports background work; `no_terminal` when there is no live session

**Dropped, and what replaces each:**

- the opencode screen witness (`_opencode_working`, pane read) -> the S1 session state: `running` and `requires_action` are busy.
- a screen `modal` / `backgrounded` block as DIALOG -> an open prompt only. A structured session has no screen to be stuck behind.
- `row.terminal` empty meaning `no_terminal` -> a structured row's terminal is always empty, so the word follows the live session: an
  idle row with no live session is `no_terminal`, with one it is `idle`.
- the idle-row waits on a child or a blocker keep their text, now cut to the contract's 40 characters.

## Where it runs

`Env.RefreshOne` is the reconciler's `RefreshView` port (after a sweep records a gone session, after a wake). `Env.Refresh` is one of
the tick's guarded passes (`Tick.Passes`), placed where Python refreshes: after the blocked sweep and the spool, before auto-accept. It
stores every structured agent's view. It deletes the row of an agent that is gone only at the cutover (`-cutover`, `Env.DropGone`): until the Python daemon stops it keeps the rows of terminal agents and the Go tick writes no row but a structured agent's, nor polls usage (`TickConfig.Cutover` is the one switch for both). The Python view pass leaves a structured agent's row alone in turn. Terminal agents are skipped by `RefreshOne`. The
composition root wires both.

## Tests

A view for a running, an idle, a requires_action (open prompt) and a crashed (session gone, stalled) structured agent; a terminal
agent's stored row left byte-for-byte; a failing agent keeps its last row and does not stop the next.
