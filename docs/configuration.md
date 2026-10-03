# Configuration

All state is under `~/.orcapipe` (or `$OCP_HOME`). It is a git repository, so `git log` there shows
what changed and when.

```
~/.orcapipe/
  config.yaml  team.yaml  db.sqlite  locks/
  logs/                       daemon output from launchd
  shared/.claude/             settings, CLAUDE.md, skills, agents, commands, plugins
  shared/mcp.json             optional MCP servers for every launch
  accounts/<name>/.claude/    one Claude config directory per account; credentials stay here
  accounts/<name>/status.json
  agents/<name>/              agent.yaml  ROLE.md  base.md  inbox/  journal/
```

`accounts/` and `logs/` are not tracked by git.

## team.yaml

Says who may send work to whom. A request that no rule allows is refused, and the refusal says why.

```yaml
teams:
  eng: [backend]

agents:
  backend:   {}
  reviewer:  {}

edges:
  - { from: "*", to: "reviewer", relation: peer, response: async }
  - { from: "*", to: "human", kind: task }

deny: []

limits:
  max_chain_depth: 4
  ask_rate_per_hour: 20
```

- `teams` groups agents. A team's manager is the member named like the team.
- `edges` allow one direction of contact. `from` and `to` are agent names or `*`.
- `deny` removes an allowed contact and always wins over an edge.
- `limits` stop runaway chains: how deep one request can chain, and how many requests an agent may
  send per hour.
- `human` is you. It is not an agent, but agents can send you a decision as a task.

The name gives the links, with no edge written: an agent may ask its parent (the name up to the
last dot, so `shop.sea` is under `shop`), its children and its siblings. A grandparent, a nephew and
another team are not reachable; a link between teams is an edge you write on purpose. A manager may
ask you. To add or remove one link or agent without editing the file, use `ocp hr`:

```bash
ocp hr team shop --member sea=/Users/you/code/sea --member shore --why "split the storefront work"
ocp hr link reviewer backend --why "backend may ask the reviewer for a second look"
```

An older `team.yaml` is moved onto the name links by `ocp hr links` (it shows the plan; `--apply`
writes it). `ocp hr` files a proposal for you to decide. `ocp hr approve <id>` applies it at once and
tells the agent that asked; `ocp hr reject <id> "why"` turns it down. `ocp hr list` shows proposals
and what became of them.

## agent.yaml

Written by `ocp agent register`. Edit it by registering again with new options, not by hand,
unless you also run `ocp team seal`. The fields you will use: `vendor`, `model`, `effort`, `weight`
(`light` or `heavy`), `repo`, `skip_permissions`, `chrome`, and `mcp_config`. Which account an
agent is on is not in this file. The daemon records it, and `ocp agent move` changes it.

Two more are the founder's to change, with `ocp agent set <agent> mode=<m>` or `sandbox=off|on`
(an agent that asks goes through HR). `mode` is Claude's permission mode: `default`, `auto` (the
default), `acceptEdits`, `plan`, or `bypass`, which is `skip_permissions`. `sandbox: off` starts
the agent's next session without Claude's sandbox and lasts only that session: it goes back on when
the session ends, or at once with `ocp agent set <agent> sandbox=on`.

## ROLE.md and base.md

Both are plain Markdown, and both have a size limit because they are sent with every session.

| File | Limit | What goes in it |
|---|---|---|
| `ROLE.md` | 1 KB | What this agent owns and what it does not. Start with one sentence. |
| `base.md` | 2 KB | Facts every session needs: paths, commands, conventions. |

`ocp doctor` warns when one is over its limit. If you are over, the content belongs in the repo's
own docs, where the agent can read it when it needs it. Both files are loaded into every session,
so everything in them costs usage every time.

## Sealed files

`team.yaml` and each `agent.yaml` are sealed. The hash of what you last wrote is kept in a protected
store at `~/.config/orcapipe/protected`. If a file no longer matches, `ocp` refuses to act on it
and `ocp doctor` names it. This stops an agent from editing its own permissions, because every
agent can write to `~/.orcapipe`.

If you edit one of these files yourself, read it over and run:

```bash
ocp team seal
```

## Accounts and limits

```bash
ocp account list              # usage windows, agents per account, whether it takes work
ocp account cap alpha 3       # how many agents may be mid-turn on alpha at once (default 2)
ocp status                    # why the daemon picked the account it did
```

An agent's account is a preference. A session goes there unless the account is at its limit, at
its cap, or filling too fast. See [how it works](how-it-works.md) for the rules.

## Shared settings

Whatever you put in `shared/.claude/` (`settings.json`, `CLAUDE.md`, `skills`, `agents`,
`commands`, `plugins`) is linked into every account's `.claude/`. The daemon links a new item on
its next tick. To keep one account different, put a real file at that path in the account's own
directory. The daemon leaves it alone, and `ocp doctor` names it.

`shared/mcp.json` is optional. If it exists, every launch passes it to Claude with
`--mcp-config`. It is not generated from `~/.claude.json`.
