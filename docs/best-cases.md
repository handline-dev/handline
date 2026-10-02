# Best cases

Three setups that have worked, taken from how orcapipe is built with orcapipe. Names in the
examples are made up. The commands are real.

## Build, review, merge

Three roles in a chain, so a change is never judged by the agent that wrote it.

| Agent | Job |
|---|---|
| `shop.builder` | Writes the change on its own branch and runs the tests. |
| `shop.auditor` | Reads the diff and the test results. Answers PASS or FAIL. |
| `shop.merger` | Merges after a PASS and reports the commit. |

You give the builder a task:

```bash
ocp task new --owner shop.builder -t 3h "add coupon codes at checkout" \
  "Add a coupon field to checkout. A valid code takes the percentage off the cart total. Done: the suite is green and the branch is ready for review."
```

When the code is ready, the builder opens a child task for the auditor and waits. It does not
hand its own task away:

```bash
ocp task new --owner shop.auditor --parent T41 -t 1h "review T41 @a1b2c3d" \
  "Branch coupon-codes. Adds the coupon field and the discount in cart.py. Check that an expired code is refused."
```

The child blocks T41. The auditor closes it with `PASS: ...` or `FAIL: ...`, and the builder is
told. On a PASS the builder opens the same kind of task for the merger. The merger closes it with
the merged commit. Then the builder closes T41 with that sha, which `ocp` checks is on `main`.

What makes this work:

- The reviewer has one question to answer and the evidence in front of it.
- A FAIL comes back to the builder with a reason, so the loop closes without you.
- You read the finished results in `ocp inbox`, not each step.

## Unattended overnight work

Several accounts and a daemon let work continue when one account runs out.

1. Add two or more accounts with `ocp account add`.
2. Run `ocp service install`, so the daemon survives a crash or a reboot.
3. Queue tasks before you leave, each with a clear "done".

During the night the daemon watches each account's usage. An agent that is working keeps going to
the limit, and the transcript keeps its work. If its account hits the limit, the daemon either
parks it until the window resets or moves it once to an account with room, whichever costs less.
A closed session is resumed when work arrives for it. In the morning `ocp inbox` holds what
finished, what was refused, and the decisions waiting for you.

Two things to check before you leave: `ocp doctor` shows no red lines, and `ocp ps` shows the
daemon running.

## A team with a manager

For a project with several parts, propose a whole team in one step:

```bash
ocp hr team shop --member sea=/Users/you/code/sea --member shore \
  --why "storefront split into search and checkout"
```

The manager is the member named like the team. You talk to the manager. It splits work among the
members with tasks, and it comes to you only for decisions that are yours. Members may ask their
manager and their team's reviewer, and nobody else, so one agent cannot send work to another
team without going through its manager.

Start with one manager and two members. Add more only when the manager's inbox shows it is the
bottleneck.

## What these have in common

- Every piece of work is a task with a stated end.
- Every result goes back to whoever asked.
- The agent that does the work is not the one that judges it.
- You are asked only when a decision is yours.
