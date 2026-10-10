# orcapipe

orcapipe runs a team of Claude Code agents and keeps them working while you are away.

You give an agent a task. A daemon delivers it, resumes the agent if its session was closed, and
moves it to another Claude account when the first one runs low. No model makes these decisions.
They are plain rules you can read and change.

It is a command-line tool called `hl`, plus a terminal screen (`hl`) for watching the team.

## Who it is for

It fits you if you run several Claude Code agents at once and you are tired of watching usage
limits, nudging idle terminals, and re-explaining work after a session dies. It works best with
two or more Claude accounts, but one is enough to try it.

It does not fit if you want a hosted service or a general agent framework. orcapipe only
supervises Claude Code sessions that run in [Orca](https://github.com/stablyai/orca) terminals on
your own Mac.

## What you need

- macOS. The background service uses launchd. Other systems are untested.
- Python 3 and git. It is developed on Python 3.14; older versions are untested.
- [Claude Code](https://claude.com/claude-code), with the `claude` command on your PATH.
- [Orca](https://github.com/stablyai/orca), with the `orca` command on your PATH.
- One Claude login per account you want to add. You type each login yourself. `hl` never reads
  or copies credentials.

## Install

```bash
git clone <this repository> orcapipe && cd orcapipe
ln -s "$PWD/bin/hl" /usr/local/bin/hl    # or add bin/ to your PATH
hl venv install                           # creates the venv (no dependencies to install now)
hl init                                   # creates ~/.orcapipe
hl doctor                                 # lists what is still missing
```

`hl doctor` will report a red line for no accounts and no agents. That is expected at this point.

## Your first team

Add an account. This opens Claude's own login screen, and you type into it:

```bash
hl account add alpha
```

Register an agent. `--repo` is the git repository it works in:

```bash
hl agent register backend --model sonnet --repo ~/code/myproject
hl team init
hl doctor
```

`hl team init` writes `~/.orcapipe/team.yaml`, which says who may send work to whom. Open it
once; it is short. The starting file also names `reviewer` and `archivist`. Either register them
the same way, or delete them from the file, or `hl doctor` warns about them. Fix any red line
it shows.

Start the agent in an Orca terminal. Use the full path to the repository:

```bash
orca terminal create --worktree path:/Users/you/code/myproject --command "hl launch backend"
```

Give it work, from your own shell:

```bash
hl task new --owner backend -t 2h "fix the flaky test" "tests/test_sync.py fails about one run in ten. Find the cause and fix it. Done means ten green runs in a row."
hl inbox        # what is waiting on you
hl       # the screen: agents, accounts, inbox
```

When the agent finishes, its result shows up in `hl inbox`. That is the whole loop: you
write a task with a clear "done", an agent does it and reports, and you read the report.

To keep the daemon running after you close the terminal or reboot:

```bash
hl service install
```

## Read next

| | |
|---|---|
| [docs/setup.md](docs/setup.md) | Install in detail, accounts, the daemon, and what to do when a step fails |
| [docs/configuration.md](docs/configuration.md) | `team.yaml`, `agent.yaml`, `ROLE.md`, and the other files in `~/.orcapipe` |
| [docs/guidelines.md](docs/guidelines.md) | How to run a team well: tasks, roles, review, cost |
| [docs/best-cases.md](docs/best-cases.md) | Setups that work, taken from how this project is built |
| [docs/reference.md](docs/reference.md) | Every command, one line each |
| [docs/how-it-works.md](docs/how-it-works.md) | The rules the daemon follows, and why |
| [docs/development.md](docs/development.md) | Running the tests and working on orcapipe itself |

## License

orcapipe is free software under the [GNU Affero General Public License v3.0](LICENSE)
(`AGPL-3.0-only`). If you cannot meet its terms, for example because you want to run a modified
version as a network service without publishing your changes, a commercial license is available.
Contact: *licensing contact to be filled in by the project owner*.

Contributions are accepted under the [contributor license agreement](CLA.md); see
[CONTRIBUTING.md](CONTRIBUTING.md).
