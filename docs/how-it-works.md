# How it works

The rules the daemon follows. Everything here is deterministic. The one model call is `ocp fold`.

**Fail-closed.** A `pct_5h` we cannot trust - no snapshot, unreadable, or too old
- is not a full tank, it is a refusal to assign. Reading it as 0 makes the 90%
trigger false forever: the feature is dead while the dashboard stays green.

**But quiet is not unknown.** An account nobody has spent on reports no
`five_hour` window at all, which means nothing has been spent in this one - not
that we cannot see. That reads as 0% and assigns, marked `*`. Refusing here
parked five idle agents. The exception is ours: `ocp fold` runs headless and
leaves no event, so its own small spend is never observed.

**A figure has a provenance, and it is drawn.** `~` is carried - an earlier
reading whose window is still open, since a payload that does not mention a
window must not erase what we knew. `!` is carried past the age we will assign
on: still used as a floor for triggers, because usage inside a window only
rises, but no new work goes to it. Inside one window the account figure never
goes down, because every session reports from the moment of its own last API
response and the staler one must not win.

**An account is a preference, not a destination.** `agent.yaml` says which
account an agent would rather be on, and a session goes there unless what is
known says it is a bad place to start: at the wall, at its cap of agents
mid-turn (2 by default, `ocp account cap <name> <n>` or `c` on the bridge), or
filling so fast that one more agent would last under half an hour. How fast it
is filling - the account's measured burn - is what says it has room, not a
fixed line, and the agent being placed is priced by its own kind: what one more
agent costs is the median of what agents of its model and effort were measured
spending, falling back to its model's, all agents', and 0.75%/min of the window
until enough has been seen.

**A working agent runs to the wall.** Nothing below 100% moves or parks it: a
turn that ends on the usage limit, or a reading at 100%, is the wall, and
nothing is lost there because the transcript keeps the work. An account nobody
can read is not a wall. At the wall the choice to move is a cost comparison: an
idle agent stays parked, and so does one whose account resets within T (39
minutes, then the measured cost of a cold start as a share of a 5h window -
`measure.py`). Otherwise it moves once, to the account the pick chooses - the
weekly headroom that expires soonest first - and never back to the one it just
left before that resets. If every account is capped or walled the work waits in
the queue. A resume goes back to the account its session lives on, because a
session id exists only under that account's config directory.

**Context and memory are different problems.** A handoff reclaims *context*: it
records the agent's request, its transcript path and its worktree's state,
clears the session, and costs no model call. Closing reclaims
*memory*: an idle session holds 250-500MB and no one is using it, so the session
ends and the terminal, worktree and account stay. Waking resumes that same
session, which costs nothing extra - measured, a resumed session read its whole
68k prefix from cache exactly as a resident one did, because the cache lives on
the server and expires on time, not on whether our process stayed alive.

**No model in the loop.** Triggers, account choice and resume decisions are all
deterministic, and so is the handoff. The only model call is `ocp fold`, which
nothing on the handoff path runs.

**Conditions, not edges.** Triggers are re-evaluated from persisted state every
tick, so a threshold crossed while the daemon was dead is still acted on.

**Unlisted edges are denied.** Agents cannot spray requests around, and a
refusal explains itself so they do not try to route around it.

**A result goes back the way the work came.** `ocp task done` records the
evidence on the task; the daemon reads it out to the agent that asked, and a
human reads it in `ocp inbox` and on `ocp bridge`. Both directions, or the team
learns to talk around it: with only one drawn, agents took to sending a second,
unrelated task repeating what they had already reported.
