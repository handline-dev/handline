# PR review loop: who opens the PR, and how a review reaches the agent

Research and proposal. Nothing is built. Web sources are listed at the end; a claim marked
"unverified" was not found in a doc. This builds on `docs/story7-git-host-research.md` (slice 7-1,
read-only PR state and a say on review comments), which stays as written.

## 1. The two problems

1. **The founder cannot approve or reject.** Agents open PRs through the founder's own `gh` login,
   so the founder is the PR author, and GitHub does not let an author approve their own PR
   ([GitHub docs](https://docs.github.com/en/pull-requests/how-tos/review-pull-requests/approving-a-pull-request-with-required-reviews)).
2. **A review goes nowhere.** A comment, "request changes" or approval on GitHub is not tied to a
   task, so the agent that wrote the code never hears it.

## 2. How other tools handle it

| Tool | Who opens the PR | How review comes back as work |
|---|---|---|
| GitHub Copilot coding agent | Copilot, as itself | A `@copilot` mention in a PR comment requests changes ([review docs](https://docs.github.com/en/copilot/how-tos/use-copilot-agents/cloud-agent/review-copilot-prs)). The person who assigned Copilot cannot approve its PR (the approval page linked in section 1). Session reuse, write-access gating and the 👀 reaction were not found in the cited page. |
| Devin | `devin-ai-integration[bot]` (GitHub App) | Autofix pushes fix commits for comments from an allowlisted set of bots; the docs warn that answering every bot comment can loop, so the allowlist is the recommended mode. Auto-fix is switched on from the review sidebar of a Devin PR (org admins only). A comment, bug or flag in the diff view can be sent to Devin ([Devin Review](https://docs.devin.ai/work-with-devin/devin-review), [Autofix](https://docs.devin.ai/use-cases/gallery/devin-review-autofix)). How a human's comment on a Devin PR is handled was not found. |
| Cursor background agents and Bugbot | The Cursor app | `@cursor` in the PR conversation continues the session found by the PR's branch; staff say this works on PRs only. `@cursor` inside an inline review thread is reported unreliable, with "post it in the main conversation" as the workaround. Bugbot reports as a check named "Cursor Bugbot" ([forum](https://forum.cursor.com/t/cursor-background-agent-from-github-creates-new-session-on-every-follow-up-post/158505), [forum](https://forum.cursor.com/t/bugbot-cursor-mentions-in-pr-comment-threads-not-working-github-enterprise/151708), [Bugbot](https://cursor.com/help/ai-features/bugbot)). |
| Sweep | The Sweep app | Replies on the PR and code comments are taken as requests to retry; "revert" reverts a file's edits. Only package listings and third-party reviews were found, no current official doc ([PyPI](https://pypi.org/project/sweepai/0.5.2)). |
| Graphite | The developer (stacked PRs via the CLI) | A review inbox with "needs your review / approved / waiting" and a verdict per PR in a stack: approve, request changes or comment. It is a review surface over GitHub and has no agent to send feedback to ([Graphite docs](https://graphite.com/docs/review-pull-requests)). |
| Linear | The developer or an agent | A PR is linked to an issue by the issue id in the branch name, PR title or commit message, or a magic word in the description; status follows PR state ([Linear docs](https://linear.app/docs/github)). No agent loop found in the docs searched. |

What repeats:

- **The author is never the human reviewer.** Every agent product opens PRs as its own
  bot/App identity, so the human can approve.
- **The PR is the key back to the work.** The branch name or PR number finds the session or issue.
- **Review comes back as a new turn on the same agent**, with its earlier context, not a new agent.
- **Only trusted people trigger it** (write access, or an allowlist), and bot-to-bot loops are
  capped. Inline-thread triggers are the weak spot, so a review submit is the safer event.

## 3. Proposal

### 3.1 Who opens PRs

A **GitHub App** owned by the founder's org, installed on the repos, named for the team. The
daemon mints a short-lived installation token from the App's private key and gives it to the
agent's `gh` through its environment. PRs show `<app>[bot]` as the author, so the founder can
approve and request changes.

- A machine-user account with a token is the fallback when an App cannot be installed. It works
  the same for approval, but costs a seat and holds a long-lived token.
- One App for all agents: the agent is named in the PR body, not by a GitHub account per agent.
- Confirmed by `handline.daemon`: pushes and PRs made with an installation token start the
  required `check` workflow (only a workflow's own `GITHUB_TOKEN` is kept from doing so), provided
  branch protection does not exclude the App from pushing. Still to test on a sandbox repo: that
  `pull_request_review` data is readable for bot-authored PRs.
- Per `story7`, identity stays per team config; this only makes the App the recommended default
  and lets the daemon mint its token.

### 3.2 How a review becomes work

Work lands per `AGENTS.md`: task branches merge into `story/<n>` without the founder, and the
founder reviews the one PR from `story/<n>` to main. Only `handline.pr` opens PRs, and builders hand
finished work to their lead. So by the time a PR opens its build tasks are done and accepted, and
`reject` and `accept` (which need a reported task) do not apply. Each PR has a founder merge task
(assigned to the human); feedback goes to that task and to the lead:

| On GitHub | Becomes | Effect |
|---|---|---|
| Review submitted: **Request changes** (body plus its line comments) | `hl task reopen` of the build task with one line, or a new fix task under the same story for the lead, carrying body then each thread as `file:line: text` with its URL | The holder is told; the lead routes the fix. A done child cannot be reopened under a closed story, so then use the fix task. |
| Review submitted: **Comment** (line comments, no verdict) | A memo on the founder merge task and a say to the lead | No state change. The lead decides whether to push a fix. |
| Review submitted: **Approve** | A memo on the founder merge task and a say to the lead | The task is ready to merge. Merge stays a separate founder action (or the merge queue); the daemon never merges unasked. |
| Single line comment or PR comment, outside a review | A memo; acted on only if it starts with `/fix` | Avoids a reply per comment while a review is being written. |
| Comment by the App or any account not on the reviewer list | Ignored | Stops loops and drive-by comments. |

Rules shared by all rows:

- Only reviewers on a per-repo list (the founder, to start) trigger anything.
- The agent that gets a change request replies on each thread when fixed, and the daemon
  resolves the thread. A 👀 reaction on the comment shows it was received.
- A per-PR cursor (last review id handled) makes each review act once.
- After three change-request rounds on one PR the task goes to its owner, not the agent again.

### 3.3 In the PC and phone apps

The apps drive the same flow instead of replacing GitHub's diff:

- **Task page, PR section:** state, rolled-up CI, open review threads, and a link to the diff.
- **Approve / Request changes** buttons with a text box. The daemon posts the review to GitHub
  with the founder's own `gh` login, because the founder is no longer the author. The webhook or
  poll then returns it as the same event and the 3.2 mapping runs, so there is one path whether the
  founder clicked in the app or on github.com.
- **Line comments in the app are a later step.** Until then the app links to the diff on GitHub
  for line comments, and its own box sends a general request-changes note.
- **Inbox:** a PR waiting for the founder's verdict is an item, as a review comment is in `story7`.

## 4. What the daemon must add

Checked by `handline.daemon` against main (memo 2146 on T4397): feasible. The daemon already runs
`gh` (`go/cmd/hl/land.go`), and `reopen`, `accept`, `reject`, memo and say exist; the router is new
code on them.

1. **PR link on a task:** `{repo, number, branch, task, head sha, review cursor}`, written by
   `handline.pr` when it opens the PR, from the request it already gets (branch, head sha, task
   `T<n>`, asked by). No branch-name fallback. The writer is that task's assignee; its owner is the
   lead who forwards finished work.
2. **Identity provider:** mint and refresh App installation tokens (1 h life) from the App key in
   the protected store, never in an agent's env. Agents get the token as `GH_TOKEN`. The founder's
   `gh` login stays the daemon's alone, for posting verdicts. Today agents share it, so the pr-gate
   (#274) is a guard, not an auth boundary; the App token makes it one.
3. **Review feed:** poll, not webhook (the daemon runs on the founder's Mac with no public URL).
   One conditional `GET pulls?state=open` per repo per minute (`If-None-Match`; a 304 costs no rate
   limit); fetch reviews and comments only for a linked PR whose `updated_at` moved. Cursor per PR
   in the db.
4. **Router:** the table in 3.2. No wake path of its own: `reopen` and `say` use normal delivery,
   which already wakes a closed holder (#267).
5. **Writes:** post a review (verdict, body) as the founder, reply to a thread, resolve a thread,
   add a reaction.
6. **Guards:** reviewer allowlist, ignore own bot, review cursor, round limit.
7. **API for the apps:** PR section data and the two verdict actions, both over the existing
   gateway auth.

Slices: 7-1 (read-only, already planned) then **A** link, identity and the review-to-task router
for the three verdicts, **B** verdict buttons in the apps, **C** thread reply/resolve and line
comments in the apps.

## 5. Founder decisions

- **App or machine user** as the PR author. Recommendation: App.
- **Reviewers allowed to trigger agents:** only the founder, or named others.
- **Does approve mean merge?** Recommendation: no; approve marks the founder merge task ready and
  merge is a separate action.
- **Slice order:** A first; apps (B) after the identity change is proven on a sandbox repo.
