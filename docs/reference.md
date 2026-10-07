# Command reference

One line each. `ocp <command> --help` has the options, and `hl guide <topic>` prints longer notes
(topics: accounts, doctor, hooks, launch, memory, status, team, work).

## Setup

| | |
|---|---|
| `hl init` | Create `~/.orcapipe` and install the shared settings and hooks. |
| `hl venv install` / `status` | Create or check the virtual environment the bridge needs. |
| `hl doctor` | Check every precondition and name what is wrong. |
| `hl account add <name>` / `remove` / `list` / `cap` | Manage accounts. `add` starts Claude's login. |
| `hl login <name>` | Log an account in again, or list who is logged in. |
| `hl agent register <name>` | Add or update an agent. |
| `hl team init` / `seal` | Write a starter `team.yaml`; accept hand edits to `team.yaml` and `agent.yaml`. |
| `hl trust add <folder>` | Let ocp answer Claude's "trust this folder" screen for a folder. |
| `hl trust mcp <folder>` | Founder only: approve the MCP servers in a declared folder's `.mcp.json` as they read now, so a launch there enables them without Claude asking. A server added or changed later is asked about. |
| `hl backup install` | Snapshot `~/.orcapipe` every five minutes when it changed. |

## Run

| | |
|---|---|
| `hl daemon` | Run the supervisor in the foreground. |
| `hl service install` / `uninstall` / `status` / `logs` | Run the daemon as a launchd job. |
| `hl launch <agent>` | Start Claude as that agent, on its account. Run it inside an Orca terminal. |
| `hl spawn <agent> --repo <path>` | Give an agent its own worktree and start it there. |
| `hl ps` | State of the daemon and each agent. |
| `hl triggers` | What the daemon would decide now, without acting. |
| `hl status` | Why the daemon picked the account it picked. |
| `hl close <agent>` | End an idle session and keep its terminal. |
| `hl restart <agent> [--fresh] [--force]` | End the session and start it again at once in the same terminal; `--fresh` is a new conversation, `--force` waits out a turn in progress. |
| `hl resume <agent>` | Hand a parked agent its handoff. |
| `hl` | The screen the team is run from. |

## Work

| | |
|---|---|
| `hl task new --owner <agent> -t <time> "<title>" "<body>" [--reply <msg> "<text>"]` | Give work, or ask something that needs an answer. The last line printed is the new `T<n>`; `--reply` also answers the founder's message with `{task}` filled in as that number. |
| `hl task list` | The board. |
| `hl task show T<n>` | One task in full: brief, memos, blockers, children, handoffs. |
| `hl task done T<n> "<evidence>"` | Finish your task. A sha must be on `main`. |
| `hl task handoff T<n> <agent> "<note>"` | Pass your task to someone else. |
| `hl task reopen T<n> "<why>"` | The asker says a result is wrong. |
| `hl task discard T<n> "<why>"` | Throw away a task that is not done. |
| `hl task memo T<n> add "..."` | Correct a task you gave. |
| `hl progress "<note>"` | Record what is done and what is left on your task. |
| `hl say <agent> "..."` | A message that needs no answer. |
| `hl inbox` / `hl inbox --all` | What is waiting for you, or the whole queue. |
| `hl read <id>` | One request in full, with its answer. |
| `hl cancel <id>` | The founder takes a request out of an agent's queue. |

## Team

| | |
|---|---|
| `hl team` | Who you may reach, and what each is doing. |
| `hl hr team` / `add` / `retire` / `rename` / `link` / `unlink` | Propose a roster change. |
| `hl hr diff --file F --why W` | Propose several changes as one (roster, `settings`, `schedule`, `global`): the founder decides once, they apply in order or not at all, you get one message. |
| `hl hr links [--apply]` | Move team.yaml onto the name links (founder). |
| `hl hr list [--json]` / `approve <id>… \| --all [--yes]` / `reject <id> [reason] \| --all [--reason R]` / `apply <id>` | See proposals; the founder approves (applied at once) or rejects; `apply` retries an approved one that failed. |
| `hl memory status` | Size of each agent's `ROLE.md` and `base.md` against its limit. |
| `hl schedule` | Messages the daemon types at an agent on a cron schedule. From an agent's session, `add`/`edit`/`remove` with `--why` raise a proposal; so do `hl agent register` and setting a global switch (`idle-timeout`, `notice-digest`, `ignore-cache-expiry`, `auto-accept`, `dev-mode`). |
| `hl fold [agent]` | Fold recorded events into the digest. The only command that calls a model. |
