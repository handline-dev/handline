# Setup

The README gets you to a first task. This page covers each step in more detail and what to do when
one fails.

## Requirements

| Needs | Why | Check |
|---|---|---|
| macOS | The daemon runs as a launchd service. | |
| Python 3 | `ocp` is a Python program. Developed on 3.14; other versions are untested. | `python3 --version` |
| git | Each agent works in a git worktree. | `git --version` |
| Claude Code | `ocp launch` starts it. | `claude --version` |
| Orca | Provides the terminals the agents run in. | `orca --help` |

`ocp doctor` checks that `claude` and `orca` are found and prints their paths.

## Install the command

`bin/ocp` is a shell wrapper. It finds its own repository through the symlink you make, so link it
instead of copying it:

```bash
ln -s "$PWD/bin/ocp" /usr/local/bin/ocp
```

If `/usr/local/bin` is not writable, link it into any directory on your PATH, or add the repo's
`bin/` to PATH.

Then install the one dependency, which the bridge screen needs:

```bash
ocp venv install
```

This creates a virtual environment at `~/.orcapipe/venv` and installs `textual` into it. Every
other command works without it. If it fails, read the `ERROR:` line it prints. A corporate proxy
or a TLS error shows up there.

## Choose where state lives

Everything `ocp` stores is under `~/.orcapipe`. To use another place, set `OCP_HOME` before every
command. This is how you try it without touching a real install:

```bash
export OCP_HOME=$(mktemp -d)
ocp init
```

`ocp init` writes the config, installs the hooks that report agent events, and makes the
directory a git repository, so you can see every change to your setup.

Note: the protected store described under [Configuration](configuration.md) lives under
`~/.config/orcapipe`, keyed by the `OCP_HOME` path. A throwaway `OCP_HOME` therefore still writes
there.

## Accounts

```bash
ocp account add alpha
ocp account add beta
ocp account list
```

`account add` registers the name and then starts Claude's own login in your terminal. You complete
the login yourself. Use a different Anthropic login for each account, because that is how you get
more usage windows. To register a name now and log in later, use `--no-login` and then
`ocp login <name>`.

`ocp` never reads, writes, or copies credentials. They stay in Claude's own config directory for
the account, `~/.orcapipe/accounts/<name>/.claude`. A refresh token works once and then rotates,
so two copies of one login would invalidate each other. That is why `ocp` does not copy them.

To remove an account: `ocp account remove <name>`. It logs the account out and moves its directory
aside. It refuses while an agent is running on it.

## Agents

```bash
ocp agent register backend --model sonnet --repo ~/code/myproject
```

`--repo` is required for the daemon to start the agent when work arrives. Useful options:

| Option | Meaning |
|---|---|
| `--model` | Which model it runs. Smaller models cost less usage. |
| `--effort` | `low` to `max`. The default is `medium`. This is a real cost lever. |
| `--role-file` | A file to install as the agent's `ROLE.md`. |
| `--skip-permissions on` | Its sessions do not ask before acting. The default is `off`. |
| `--mcp-config` | MCP servers for this agent only. |

Registering the same name again updates it. Options you leave out keep their current value.

## The team file

```bash
ocp team init
```

This writes a starter `~/.orcapipe/team.yaml`. It names `backend`, `reviewer` and `archivist`. If
you have not registered all three, `ocp doctor` warns. Register them or edit the file. After you
edit `team.yaml` or any `agent.yaml` by hand, run `ocp team seal` to accept your change (see
[Configuration](configuration.md#sealed-files)).

## Run the daemon

```bash
ocp daemon           # in the foreground, stops with the terminal
ocp service install  # as a launchd job: starts at login, restarts after a crash
ocp service status
ocp service logs
```

Only one daemon runs. A second one exits. `ocp ps` shows what the daemon and each agent are doing
now. `ocp triggers` shows what the daemon would decide at this moment, without doing it.

## Start an agent

```bash
orca terminal create --worktree path:/full/path/to/repo --command "ocp launch backend"
```

`ocp launch` picks an account, sets that account's config directory, and starts `claude`. Orca
keeps the command, so after a restart the agent comes back on the same account.

## When something fails

| You see | Cause and fix |
|---|---|
| `ocp bridge` prints a venv message | Run `ocp venv install`. |
| `ocp doctor`: no accounts registered | `ocp account add <name>`. |
| `ocp doctor`: agent has no claude account | Add one: `ocp account add <name>`. |
| `ocp doctor`: team.yaml names agents ocp does not have | Register them, or delete them from `team.yaml`. |
| `ocp doctor`: seals have not begun | Review `team.yaml` and the `agent.yaml` files, then run `ocp team seal`. |
| `needs a repo` on `agent register` | Add `--repo <path>`. |
| An agent never gets its task | `ocp ps` shows whether the daemon is running. `ocp triggers` shows what the daemon would do right now. |
| Task refused with "unknown target" | The name is not on `ocp team`. Use the full name shown there. |
| An agent is stuck on a dialog | `ocp ps` and the bridge list it under WAITING ON YOU, with the dialog named. `ocp` does not answer dialogs. Answer it in the terminal (`t` in the bridge). |
