# Running a team well

These come from running orcapipe on its own development. They are habits, not rules the tool
enforces.

## Write tasks that can be finished

A task has a title, a body, and a time. The body is the brief: what to do and what "done" looks
like. The agent reads it with no memory of your conversation, so write it for a stranger.

Weak:

```bash
ocp task new --owner backend -t 1h "fix sync" "it's broken"
```

Better:

```bash
ocp task new --owner backend -t 2h "fix flaky test_sync" \
  "tests/test_sync.py fails about one run in ten. Find the cause and fix it. Done: ten green runs in a row, and the commit sha."
```

The `-t` value is how long before you are told the task is still open. It is not a deadline the
agent sees.

If you need to correct a task that is not done, add a memo: `ocp task memo T12 add "also cover the
retry path"`. Do not send a second message behind it.

## One task at a time

An agent holds one task. The next one arrives when it is done or handed on. This keeps an agent
from splitting its attention, and it keeps you from holding eleven threads. Queue as much as you
like; the agent sees only the head.

## Separate "needs an answer" from "FYI"

There are two kinds of message and they behave differently.

- `ocp task new` is for anything that needs an answer or a result. It is tracked until it ends.
- `ocp say` is for a message that needs no answer. It is typed into the session at once and starts
  no work.

If you find yourself sending a `say` and waiting for a reply, it should have been a task.

## Close tasks with evidence

`ocp task done T12 "<evidence>"` takes a commit sha, a request number, or a plain reason. A sha has
to be on `main`. The person who asked reads the result. Nobody should send a second task that
repeats what the first one reported.

If the result is wrong, the asker runs `ocp task reopen T12 "<why>"`.

## Give each agent one job

Keep roles narrow and write them in `ROLE.md`: what the agent owns, and what it does not touch.
The roles that worked in practice:

- **A builder** per area of the code. It owns its files and writes the change.
- **An auditor** who reads a change and the test results and answers PASS or FAIL.
- **A merger** who merges only after a PASS.

The builder does not review or merge its own work. When code is ready, the builder opens a review
task for the auditor, waits, and on a PASS opens a merge task. See
[best cases](best-cases.md#build-review-merge).

## Keep fixed text small

`ROLE.md` (1 KB) and `base.md` (2 KB) go into every session. Put things that change or that only
some tasks need in the repo's docs, and tell the agent where to look.

## Decisions that are yours

When an agent needs a call only you can make, it opens a task with owner `human`. It shows up as a
card in the bridge. You answer inside the card, and the answer is recorded on the task. Agents
should not guess on your behalf, and they should not stop silently. If an agent is blocked, it
says so in a task.

## Cost

- Pick the smallest model that does the job. Use `--model haiku` for note-taking agents and a
  larger one where mistakes are costly.
- `--effort` changes the cost of every turn. Raise it for an agent that needs it, not for the team.
- Spread agents over accounts. `ocp account cap <name> <n>` limits how many run at once on one.
- Let idle sessions close. A closed session keeps its terminal and account and resumes on
  demand. An idle open one holds a few hundred megabytes of memory.

## Keep a human in the loop where it matters

`ocp` shows a dialog an agent is stuck on, and it names the dialog, but it never answers one.
Permission prompts, logins, and anything that costs money are yours.

If you turn on `--skip-permissions`, do it per agent and only for agents working in a throwaway
worktree.

## Habits that pay off

- Check `ocp inbox` before you check on an agent. The answer is usually there.
- Have long tasks keep a progress note with `ocp progress "done: ... left: ..."`. If the session
  is replaced, the next one starts from that note instead of the transcript.
- After you change `team.yaml` or an `agent.yaml`, run `ocp doctor`.
