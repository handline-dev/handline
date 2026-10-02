# Command reference

One line each. `ocp <command> --help` has the options, and `ocp guide <topic>` prints longer notes
(topics: accounts, doctor, hooks, launch, memory, status, team, work).

## Setup

| | |
|---|---|
| `ocp init` | Create `~/.orcapipe` and install the shared settings and hooks. |
| `ocp venv install` / `status` | Create or check the virtual environment the bridge needs. |
| `ocp doctor` | Check every precondition and name what is wrong. |
| `ocp account add <name>` / `remove` / `list` / `cap` | Manage accounts. `add` starts Claude's login. |
| `ocp login <name>` | Log an account in again, or list who is logged in. |
| `ocp agent register <name>` | Add or update an agent. |
| `ocp team init` / `seal` | Write a starter `team.yaml`; accept hand edits to `team.yaml` and `agent.yaml`. |
| `ocp trust add <folder>` | Let ocp answer Claude's "trust this folder" screen for a folder. |
| `ocp backup install` | Snapshot `~/.orcapipe` every five minutes when it changed. |

## Run

| | |
|---|---|
| `ocp daemon` | Run the supervisor in the foreground. |
| `ocp service install` / `uninstall` / `status` / `logs` | Run the daemon as a launchd job. |
| `ocp launch <agent>` | Start Claude as that agent, on its account. Run it inside an Orca terminal. |
| `ocp spawn <agent> --repo <path>` | Give an agent its own worktree and start it there. |
| `ocp ps` | State of the daemon and each agent. |
| `ocp triggers` | What the daemon would decide now, without acting. |
| `ocp status` | Why the daemon picked the account it picked. |
| `ocp close <agent>` | End an idle session and keep its terminal. |
| `ocp restart <agent> [--fresh] [--force]` | End the session and start it again at once in the same terminal; `--fresh` is a new conversation, `--force` waits out a turn in progress. |
| `ocp resume <agent>` | Hand a parked agent its handoff. |
| `ocp bridge` | The screen the team is run from. |

## Work

| | |
|---|---|
| `ocp task new --owner <agent> -t <time> "<title>" "<body>"` | Give work, or ask something that needs an answer. |
| `ocp task list` | The board. |
| `ocp task show T<n>` | One task in full: brief, memos, blockers, children, handoffs. |
| `ocp task done T<n> "<evidence>"` | Finish your task. A sha must be on `main`. |
| `ocp task handoff T<n> <agent> "<note>"` | Pass your task to someone else. |
| `ocp task reopen T<n> "<why>"` | The asker says a result is wrong. |
| `ocp task discard T<n> "<why>"` | Throw away a task that is not done. |
| `ocp task memo T<n> add "..."` | Correct a task you gave. |
| `ocp progress "<note>"` | Record what is done and what is left on your task. |
| `ocp say <agent> "..."` | A message that needs no answer. |
| `ocp inbox` / `ocp inbox --all` | What is waiting for you, or the whole queue. |
| `ocp read <id>` | One request in full, with its answer. |
| `ocp cancel <id>` | The founder takes a request out of an agent's queue. |

## Team

| | |
|---|---|
| `ocp team` | Who you may reach, and what each is doing. |
| `ocp hr team` / `add` / `retire` / `rename` / `link` / `unlink` | Propose a roster change. |
| `ocp hr list` / `apply <id>` | See proposals; carry out an approved one. |
| `ocp memory status` | Size of each agent's `ROLE.md` and `base.md` against its limit. |
| `ocp schedule` | Messages the daemon types at an agent on a cron schedule. |
| `ocp fold [agent]` | Fold recorded events into the digest. The only command that calls a model. |
