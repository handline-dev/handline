# Story 7-0: GitHub and GitLab integration for agents

Research only. Nothing is built. Licences are from `gh api repos/<r>` on 2026-10-14. READMEs of
`qodo-ai/pr-agent`, `anthropics/claude-code-action` and `BloopAI/vibe-kanban` were read. Everything
marked "general knowledge" was not checked in a doc and is unverified. Devin, Conductor, Cursor
(background agents, Bugbot), GitHub Copilot coding agent and Codex cloud are closed products: their
docs were not fetched, so only public behaviour from general knowledge is listed.

## 1. How others do it

| Tool | Licence | Identity | Events | PR to work | Review comments back to the agent | Merge gate |
|---|---|---|---|---|---|---|
| PR-Agent (`qodo-ai/pr-agent`) | MIT | GitHub App, token, or Action; GitLab, Bitbucket, Azure DevOps, Gitea | Webhook server, or a CLI/Action run per event (README) | One run per PR; no task | Reacts to a `/command` comment on the PR (README) | Not in the README; comments only (unverified) |
| Claude Code Action (`anthropics/claude-code-action`) | MIT | GitHub App plus repo secrets (README) | Runs in a workflow on PR, comment and review events (README lists comment and review triggers; the event list is general knowledge) | The workflow run is the unit | A mention or review comment triggers a run (README) | Not in the README; branch protection is the host's (general knowledge) |
| Vibe Kanban (`BloopAI/vibe-kanban`) | Apache-2.0 | Local GitHub login (general knowledge) | None found in the README | A workspace/attempt opens a PR | Unverified | None found |
| OpenHands (`All-Hands-AI/OpenHands`) | MIT | GitHub or GitLab token / app (general knowledge) | Webhook-style resolver on issues and PR comments (general knowledge) | Issue or PR to a conversation | Comment on the PR starts a run | None |
| Orca (`stablyai/orca`) | MIT | Local `gh` and `glab` CLIs (`docs/orca-feature-inventory.md` F50) | Polling through the CLI (general knowledge) | Worktree linked to an issue or PR (F08, F48) | Shown in the app (F48) | Unverified |
| Copilot coding agent, Devin, Cursor background agents / Bugbot, Codex cloud, Conductor | Closed | GitHub App (general knowledge) | Webhooks (general knowledge) | The agent opens a PR from a task | Review comments and bot findings fed back as new turns (Devin autofix, Copilot, Bugbot: general knowledge) | Required checks and approvals stay with GitHub |

Reading across:

- **Events beat polling.** The tools that react to comments are webhook- or Action-driven; the ones
  that poll (local CLI) only show state.
- **Nobody builds its own gate.** The merge gate is branch protection (required checks, approvals,
  merge queue) on the host; tools read the result.
- **The PR is the join.** The agent opens it, and its branch name or number is the key back to the work.
- **An App is for a server, a token or the CLI is for one person's machine.** Our daemon runs on
  the founder's machine, so the CLI and token path needs no public URL.

## 2. How a daemon here could do each part

| Part | GitHub | GitLab |
|---|---|---|
| Identity | `gh` login on the machine (token), or a GitHub App when a public URL exists | `glab` login or a project access token |
| Events | App webhooks need a public URL; without one, poll `gh api` conditional requests (`ETag`, cheap) | Project webhooks need a public URL; else poll the REST API |
| Task to PR | Record the PR number and branch on the task at `gh pr create`; match later by branch name | Same with the merge request IID |
| Review comments to the holder | Review-comment and issue-comment events to a say to the agent holding the task, linked to it | Note events to the same say |
| Reply and resolve | `gh api` reply to a review comment; resolve the thread through GraphQL (general knowledge) | Discussion reply and resolve through the REST API |
| CI state | Check runs and statuses on the head sha; one rolled-up state on the task | Pipeline status of the MR head |
| Merge gate | Read branch protection and the PR's `mergeable` / required-check state; never bypass | Merge request approvals and pipeline-must-succeed |

CI is `T3931`'s work under `T3929`, still queued: its plan is one always-reporting aggregate job as
the single required check on main, named `check` (PR #118, not merged yet). That job is the check the CI and merge-gate rows read; this note
only reads its state and does not define it.

## 3. What we would borrow

Ideas only; no code from Claude Squad (AGPL) or GitLab itself (NOASSERTION).

1. **PR-Agent: one provider interface** (get PR, comments, post comment, set status) with GitHub
   first and GitLab as a second implementation. Same shape in Go, no code taken; MIT would allow it.
2. **Orca: the local CLI as the identity.** No App to host, no secret of ours to hold.
3. **Copilot and Devin: review feedback arrives as a new turn on the same agent**, not a new agent.
4. **Branch protection as the gate**, as everyone does.

Libraries if the Go daemon talks to the API directly: `google/go-github` (BSD-3-Clause);
`xanzy/go-gitlab` (Apache-2.0) is archived, and its successor is GitLab's own Go client, hosted on
gitlab.com (not on GitHub, so licence and name unchecked). Or shell out to `gh` (MIT, `cli/cli`) and
`glab` (GitLab's CLI, also gitlab.com-hosted; licence unchecked).

## 4. Proposal

**The daemon owns:** a provider (GitHub, then GitLab) behind one interface; the PR link on a task
(number, branch, state); an event feed of review comments and CI results from `gh`, polled with
conditional requests until a public URL exists; routing of a review comment to the agent that holds
the task as a say linked to the task; reply and resolve as writes. It never merges on its own and
never changes branch protection.

**The apps show:** on the task page, the PR (state, CI rolled up, review threads waiting) and a
reply box per thread; on the Inbox, a review comment waiting on the founder; nothing the host
already shows better (the diff).

**First slice (7-1), smallest that is useful:** read-only. Record the PR on a task when an agent
opens it, show its state and CI on the task page (phone and PC), and say to the holder when a
review comment arrives. No reply, no resolve, no GitLab.

**Then:** reply and resolve (7-2), GitLab through the same interface (7-3), webhooks when there is
a public URL (7-4).

## 5. Founder answers

- **Slice:** the proposal is agreed, and 7-1 is read-only first.
- **Poll, no webhooks:** conditional requests (ETag) so an unchanged PR costs no rate limit, about once a minute.
- **Identity:** not ours to decide. The Git host identity is whatever each team configures for its agents (their own `gh` login, a bot account or an app), set per team and given to the agent through its prompt or environment. The daemon uses the identity the agent's environment already has and never assumes the founder's login.
