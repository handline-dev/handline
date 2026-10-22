# Shaping a team (`hl hr`)

What running teams taught us. Read it before `hl hr add`, `hl hr team`, `hl hr retire` or a role change.

- **Shape.** Each team has a lead, builders and an auditor. The lead routes work and does not build. Builders own one layer
  or area each. The auditor checks finished work.
- **Landing.** One lander agent merges PRs one at a time on the newest main. A PR behind main goes back to its builder.
- **Checks live in CI.** Tests, lint and the file and PR-text rules run in GitHub Actions. The daemon only steers agents;
  do not put a check in a role that CI can run.
- **Audit by task.** An audit is a task for the auditor, never a message. It starts on green CI, passes one commit (the
  PR's head) and sets the `audit` status there. A new push needs a new audit; nothing merges without a PASS on its head.
- **UI work** comes with before and after shots, and its auditor clicks through the real app, not only the tests.
- **Wait on gates.** Block a task on the PR's CI or on another task (`hl task wait`, `--blocked-by`); do not hold it or poll.
- **Accounts.** Pace each account to its weekly limit (the `pacing` setting) instead of burning 5h windows, and size a
  team to what the accounts' week can carry.
- **The founder** gets one decision at a time, in plain words, with your recommendation.
- **Retiring.** Before `hl hr retire`, hand each of the agent's open tasks to a new owner (`hl task handoff`).
