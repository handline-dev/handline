# How it works

The daemon decides what to do with agents and accounts using fixed rules. No model is involved.
The one command that calls a model is `ocp fold`, which summarizes recorded events into a digest.
Nothing that moves, parks, or resumes an agent depends on it.

## Usage numbers

Each account reports how much of its five-hour window it has used. The daemon treats that number
carefully.

- **A number it cannot trust blocks new work.** If there is no reading, or the reading is too old,
  the daemon does not assume the account is empty. It refuses to assign work there. Treating a
  missing reading as zero would make the "near the limit" trigger never fire.
- **An account nobody has used is different from an unknown one.** A fresh account reports no
  window at all, which means nothing has been spent. The daemon reads that as 0% and assigns work,
  and marks it with `*` on screen.
- **The screen shows where each number came from.** `~` means an earlier reading from a window that
  is still open. `!` means a reading older than the daemon will assign on. It still counts as a
  minimum for triggers, because usage inside a window only goes up, but no new work goes there.
- **A window's number never goes down.** Sessions report at different moments, and the older report
  must not overwrite a newer one.

## Choosing an account

An agent has a preferred account. A new session starts there unless the account is a bad place to
start. It is a bad place if:

- it is at its limit,
- it already has its maximum number of agents mid-turn (2 by default; change it with
  `ocp account cap <name> <n>`, or `c` on the bridge), or
- it is filling so fast that one more agent would last less than half an hour.

How fast an account fills is measured, not fixed. The cost of one more agent is the median of what
agents of the same model and effort have actually used. If there are not enough measurements, the
daemon falls back to the same model, then to all agents, then to 0.75% of the window per minute.

## When an account runs out

A working agent keeps going until it hits the limit. Below 100% the daemon never moves or parks
it. A turn that ends on the usage limit, or a reading of 100%, means the account is out. Nothing
is lost, because the session transcript keeps the work. An account the daemon cannot read is not
treated as out.

When an account is out, the daemon compares costs.

- An idle agent stays parked.
- An agent whose account resets soon stays parked. "Soon" is 39 minutes at first, then the
  measured cost of starting a session cold, as a share of a five-hour window.
- Otherwise the agent moves once, to the account with the most weekly headroom that expires
  soonest. It does not go back to the account it left until that account resets.

If every account is out or at its cap, the work waits in the queue. A resumed session always goes
back to the account its session lives on, because a session id exists only in that account's config
directory.

## Context and memory

These are two separate problems with two separate fixes.

- **Handoff frees context.** It records the agent's current request, the path to its transcript,
  and the state of its worktree. Then it clears the session. No model call is needed.
- **Closing frees memory.** An idle session holds 250 to 500 MB. Closing ends the session and
  keeps the terminal, the worktree, and the account.

Waking a closed session resumes the same session, and it costs no more than keeping it open. In a
measurement, a resumed session read its whole 68,000-token prefix from the cache, just like a
session that had stayed open. The cache lives on the server and expires by time, not by whether
your process stayed alive.

## Rules that keep it reliable

- **Rules run on stored state, every tick.** If a threshold was crossed while the daemon was down,
  the daemon still acts on it when it comes back.
- **Contacts not listed in `team.yaml` are denied.** The refusal says why, so an agent does not
  try to route around it.
- **A result goes back the way the work came.** `ocp task done` records the evidence on the task.
  The daemon tells the agent that asked, and you see it in `ocp inbox` and on the bridge. If only
  one direction existed, agents started sending a second task to repeat what they had already
  reported.
