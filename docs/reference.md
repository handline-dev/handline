# Command reference

`hl` is what an agent needs in a turn, plus first install. Everything else is a screen in the app (accounts and logins, devices,
closing and restarting agents, settings, cancelling a queued message, HR decisions, sealing hand edits) or the daemon does it itself.

## Work

| | |
|---|---|
| `hl task new --assign <agent> -t <time> "<title>" "<body>"` | Give work, or ask something that needs an answer. `--assign` is who works it; `--owner` is who answers for it (you when not given). The last line printed is the new `T<n>`. |
| `hl task list` / `board` | The tasks you hold or gave; the board. |
| `hl task show T<n>` | One task in full: brief, memos, blockers, children, handoffs. |
| `hl task done T<n> "<evidence>"` | Finish your task. A sha must be on `main`. |
| `hl task handoff T<n> <agent> "<note>"` | Pass your task to someone else. |
| `hl task edit T<n> --blocked-by T<m>` | Record what the task waits on (also its timeout and other fields). |
| `hl task reopen T<n> "<why>"` | The asker says a result is wrong. |
| `hl task release T<n> "<why>"` | Give a task back to its queue. |
| `hl task discard T<n> "<why>"` | Throw away a task that is not done; `discard T1 T2 --why "<why>"` throws several away in one call. |
| `hl task memo T<n> add "..."` / `list` / `edit` / `delete` | Correct or comment on a task. `--attach shot.png` adds an image. |
| `hl progress "done: ... left: ..."` | Record what is done and what is left on your task. |
| `hl say <target> "..."` | A message; `--parent <msg>` answers one, `--attach shot.png` sends an image the app shows. |
| `hl inbox` / `hl inbox --all` | What is waiting for you, or the whole queue. |
| `hl read <msg>` | One message in full, with its answer. |
| `hl withdraw <msg>` | Take back a message you sent that has not been read. |

## Team

| | |
|---|---|
| `hl team` | Who you may reach, and what each is doing. |
| `hl hr add` / `retire` / `rename` / `link` / `unlink` / `team` / `context` | Propose a roster change; the founder decides it in the app's HR tab. A link's ends are agents, `*` or `human`. |
| `hl hr list` / `withdraw <id>` | See proposals; take back one of yours. |

## Install and the daemon's own

| | |
|---|---|
| `hl init` | Create the home and install the shared settings and hooks. |
| `hl service install` / `uninstall` | Run the daemon as a launchd job. |
| `hl team seal` | Founder: accept your own hand edits to `team.yaml` and `agent.yaml` (until the app has a seal control). |
| `hl device pair [--name N]` | Founder, from your own terminal: a one-time code that pairs a first device when no paired app can make one. |
| `hl launch <agent>` | Start Claude as that agent, on its account (the daemon runs it). |
| `hl hook <event>` | A Claude Code hook (the installed settings run it). |
| `hl sandbox-repo <repo> [allowance ...]` | Founder: what a repo's sandboxed sessions may do beyond the sandbox. |
| `hl idle-background` / `notice-digest` | Founder: the background-work and notice-digest timings. |
| `hl dev on` / `off` / `rebuild <team>` | A team's dev instance beside the live daemon. |
