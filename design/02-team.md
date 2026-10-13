# Team model: roles, relations, routing, context budget

> For infrastructure (slots, triggers, records) see `design/01-infra.md`. This document covers only **how the team works** on top of it.
> The design goal in one line: **an agent says only "what to whom"; the system decides all of "how".**

---

## 0. First: the answer to the account question

### Can one `.claude` folder serve several accounts? → **No**

Claude Code does not keep credentials in a store keyed by profile. **One directory is one whole account.** But `CLAUDE_CONFIG_DIR` is **read per process at process start**, so two agents started with different values really run in parallel on two accounts. That is what makes our slot model hold.

### Do we need a tool to sync settings and history? → **No tool; a layout is enough**

There is an established pattern for this. **State the account owns is a real file per profile; settings a person owns are symlinked to a shared place.**

```
~/.orcapipe/shared/.claude/              ← managed in one place, shared by every account
  settings.json        # statusLine, hooks, permissions
  CLAUDE.md
  skills/ agents/ commands/ hooks/ output-styles/

~/.orcapipe/accounts/<acct>/.claude/
  .credentials.json    ← a real file. Never share it (§1 security)
  .claude.json         ← account identity and onboarding state. A real file
  projects/ history.jsonl sessions/ shell-snapshots/ backups/ plugins/   ← real directories
  settings.json  → ../../../shared/.claude/settings.json
  CLAUDE.md      → ../../../shared/.claude/CLAUDE.md
  skills/        → ../../../shared/.claude/skills
  agents/ commands/ hooks/ output-styles/ → (same)
```

Fix a skill once in `shared/.claude/skills` and every account sees it at once. `ocp doctor` checks link integrity; a symlink turning into a real file during a restore or backup is the known trap of this pattern.

### CONFIG_DIR per account or per agent? → **As many as there are accounts**

**A directory is the account's body, and an agent is a role that moves across them.**

| | Per account (chosen) | Per agent |
|---|---|---|
| Number of directories | N accounts | M agents (usually M > N) |
| Changing account | **One env var. Zero file operations** | Re-point the credential link + sync `.claude.json` |
| Concurrency cap | N | **Still N** |
| Guard against double use | The filesystem guarantees it by structure | Relies on a lease alone |

Per account, for three reasons.

1. **More directories do not add slots.** What caps concurrency is not the number of directories but the number of **credentials.** Nine directories for nine agents still give two concurrent runs if there are two accounts. Nine directories would just fight over two credentials.
2. **Per account actually gives more freedom to change accounts.** It runs against intuition, but `CLAUDE_CONFIG_DIR` is **read at process start.** Per account, changing account = changing one variable when launching, zero file operations. Per agent, every change has to touch a credential path, which is the area §1 forbids.
3. **There is one more line of defense.** Per account, "one directory = one account" is guaranteed by structure, so two agents on the same account **cannot even be expressed.** Per agent, nothing in the filesystem stops two directories from pointing at the same credential, and we would rely on a lease alone.

### So where do the reasons that made per-agent directories look needed go?

| Original reason | Actual answer |
|---|---|
| Per-agent hook, permission and MCP settings | **The worktree's `.claude/settings.json`.** Each agent has its own worktree, so this is where it belongs. There are also the `--append-system-prompt` / `--permission-mode` / `--mcp-config` flags |
| Separate history per agent | `agents/<name>/journal/`, already keyed by agent name |
| Transcripts scattered | The statusLine's `transcript_path` always says where the current one is. We do not use `--resume`, so it does not matter |

### The one real cost of per account

Project trust state (the "do you trust this folder?" entry in `.claude.json`) is needed for **every account N x worktree M combination.** When a new worktree is made, `ocp doctor` can plant it on every account in advance; it is a one-off.

And **Phase 0 assumption H**: `~/.claude.json` lives **outside** `~/.claude/`, yet the official docs say auth is in it. If it does not follow `CLAUDE_CONFIG_DIR`, account isolation has a hole. Thirty seconds confirms it.

### We do not sync history: there is no need

"History should continue when we switch accounts" was the original requirement, but **in our design this problem never arises.**

- Resume is by **handoff injection**, not `claude --resume`. It does not matter which account directory the previous session's transcript is in
- That handoff is made **deterministically** by `handoff.record` from three facts on disk. The resume path calls no model (`fold.run_model` is used only by `ocp fold`)
- An agent's single history is `agents/<name>/journal/`. It is **keyed by agent name, independent of the account**
- If the current transcript location is needed, the statusLine reports it as `transcript_path`

Sharing `projects/` could make `--resume` cross accounts, but the risk of write conflicts is unverified and it gains us nothing. **We do not share it.**

---

## 1. Four layers of context: where base.md sits

| Layer | Limit | Lifetime | Injected | Who writes it |
|---|---|---|---|---|
| `ROLE.md` | **1 KB** | immutable | every session | a person |
| `base.md` | **2 KB** | nearly immutable | every session | promotion (a person approves) |
| handoff (`agent_state.handoff`) | small | volatile | on resume only | deterministic (no model) |
| `journal/<date>.md` | unlimited | permanent | **not injected** | fold |

### Why it must be tight: this is a fixed cost

`ROLE + base` is **always prefilled at the start of every session.**

```
3 KB ≈ 750 tokens x 9 agents x 20 sessions a day ≈ 135k tokens/day (fixed)
if base.md grows from 2 KB to 8 KB, that alone adds 270k tokens a day
```

Left alone, base.md always bloats, because useful facts keep being found. So it runs on a **budget**, not a cap.

### The base budget

```
Hard cap 2 KB. Over it, ocp doctor fails (not a warning)
To add, something must go:
  edit base.md directly (ocp doctor checks the budget)
No automatic promotion. The fold only proposes candidates (the digest's "Base candidates" section)
```

### What goes in base

**What goes in**: what does not change, cannot be learned by reading the code, and is referred to repeatedly

```markdown
# base · backend
Stack: FastAPI + Postgres + Alembic. ORM is SQLModel.
Forbidden: writing migrations by hand. A schema change is a separate task after product approves.
Convention: one router file per feature. A service layer is required. No logic in controllers.
Term: "session" means a user session, not a Claude session.
Decision: the refresh token is an httpOnly cookie (2026-09-15; to reverse it, go through product)
```

**What must not go in**

- Current work state → `handoff`
- Past history → `journal`
- What reading the code shows (file layout, function signatures) → let them read it
- Anything with an expiry, like "this week's goal" → no one deletes it after it expires

---

## 2. Relations: relation and response, two declared axes

The relation between agents is declared on two axes. The agent does not even **need to know** these values.

### Axis 1: relation, the nature of the request

| Value | Meaning | Can refuse | Queue priority | On failure |
|---|---|---|---|---|
| `supervisor → subordinate` | order | no | high | preempts the target's current work |
| `peer ↔ peer` | request | yes | normal | replies with the reason for refusing |
| `subordinate → supervisor` | escalation | — | immediate | gate (`creates_gate`) |

The real difference in a hierarchy is not the title but **the right to refuse and the right to preempt.** Only those differ.

### Axis 2: response, what happens to the requester

| Value | Requester state | Cost |
|---|---|---|
| `async` **(default)** | PARKED after handoff. Resumes when the result arrives | context returned |
| `fire_and_forget` | just carries on | none |

`blocking` (the requester waits) is **not offered.** A requester holding a slot and an account assignment while it waits for a review is the most wasteful loss in a system with finite accounts. If kept as an option, someone will use it.

The default is `async`: the requester hands off and parks. It meshes with the state machine in infrastructure §8.
(At a limit an agent moves once to the account `pick` chooses, and the account it left is used at the next pick.)

In code an edge has `relation` (only `supervisor` is special, the rest are peer), `response` (`async` | `fire_and_forget`; any other value fails to load), and `kind` (`task` creates a decision task that goes to the founder; the old name `escalate` is read with the same meaning; `verdict`; `report`). A report to the founder is `ocp say`, and it closes as read when a person opens it.

### Task

Above requests sits the task: `ocp task new|list|queue|draft|link|edit|merge|done|reopen|discard|board`. A task goes draft (record only) → queued (the founder or the owner's manager approves) → doing (a request is out) → done. Done is final, and if the result is wrong, the person who opened it reopens it. A task with an open subtask or an open blocker cannot become done. There are only two ways to end, done and discard, and a task merged with `task merge` is a discard with the reason 'merged into T<n>'. A task has a title, an owner, an opener and a parent, and linking requests shows who holds it now. `done` requires one piece of evidence (a merge sha on main, a request id whose result was already reported, or one line of fact for work that leaves no commit). The owner or the target of a linked request closes it; the opener reopens or discards it.

---

## 3. An agent uses only two commands

```bash
ocp task new --owner <target> "<title>" "<request>"   # work: if you need a result, it is a task
ocp say <target> "<message>"                          # words needing no answer, typed in at once
```

(`ocp ask`, `ocp resolve` and `ocp dismiss` were removed.)

What the agent knows: **the target and the content.** That is all.

What the system decides automatically:

```
whether the relation allows it → if not, fail at once, with the reason
priority                       → relation + the requester's place on the critical path
account                        → pick by measured burn rate (infra §0.5; §6 and §7 are not built)
requester handling             → if async, handoff + park
reply path                     → the requester's inbox file, the daemon types a notice into the session
chain depth                    → loop prevention
```

If a request is refused the agent gets the reason: `denied: eng.* → growth.* needs to go through product`. The agent has no room to infer the policy or try to get around it.

---

## 4. `team.yaml`: the routing policy

```yaml
teams:
  product: [product]
  eng:     [backend, frontend]
  growth:  [marketing, analytics]
  ops:     [archivist]

agents:
  product:    { model: mid,   weight: light }
  backend:    { model: high,  weight: heavy }
  frontend:   { model: high,  weight: heavy }
  marketing:  { model: mid,   weight: light }
  analytics:  { model: mid,   weight: light }
  archivist:  { model: cheap, weight: light }
  reviewer:   { model: mid,   weight: light, reviews: [eng, growth, product] }

edges:
  - { from: "*",         to: "reviewer",     relation: peer,  response: async }
  - { from: "reviewer",  to: "*",            kind: verdict,   response: async }
  - { from: "product",   to: "eng.*",        relation: peer,  response: async }
  - { from: "product",   to: "growth.*",     relation: peer,  response: async }
  - { from: "analytics", to: "product",      relation: peer,  response: async }
  - { from: "*",         to: "archivist",    relation: peer,  response: fire_and_forget }
  - { from: "*",         to: "orchestrator", kind: task }

deny:
  - { from: "eng.*",    to: "growth.*",  reason: "no direct requests between teams; go through product" }
  - { from: "growth.*", to: "eng.*",     reason: "same" }

limits:
  max_chain_depth: 4
```

**An edge that is not stated is refused by default.** That keeps agents from scattering requests anywhere.

---

## 5. No model in the orchestrator

This is the biggest item in running cheaply.

In most multi-agent systems **the orchestrator LLM spends the most tokens.** It reads every worker's report, decides what comes next, and instructs again. Its context grows in proportion to the number of workers.

In our design the `orchestrator` is **not an agent but the daemon.**

```
resolving task dependencies → a task's `--blocked-by` (a notice when the blocking task closes)
account assignment          → pick by measured burn rate (infra §0.5)
request routing             → team.yaml (declared)
when to hand off            → infra §8 triggers (conditions)
the resume decision         → infra §8.4 reconciliation
```

All of it is deterministic, so no model is needed. Only at the points that really need human judgment (escalation, gates) does it go to a **person.** It does not go to a model.

### Model tier mapping

| Role | Tier | Why |
|---|---|---|
| orchestrator | **none** | the daemon |
| archivist (fold) | cheap | structured rewrite. No judgment needed |
| reviewer | mid | needs judgment but generates little |
| backend / frontend | high | the real implementation |
| product / marketing / analytics | mid | — |

### Other cost devices

- **Merging asks**: consecutive requests to the same target can be merged in the queue (`--mergeable`). A review is **once per PR**, not per commit
- **Forbidding `blocking`** removes idle slots (§2)
- **The fold uses the cheap tier** (infra §5)
- **The base budget** holds the fixed cost down (§1)

---

## 6. Preventing loops and runaways

```
every ask gets a chain_id + depth
depth > max_chain_depth → escalate to the orchestrator (gate)
A→B→A detected inside one chain → stop at once + gate
```

Review loops are especially dangerous: `backend → reviewer → (finding) → backend → reviewer → ...`
If the review round trips on the same file pass 3, it is **escalated automatically to product.** When two agents cannot agree, it is usually a planning problem.

---

## 7. Folder layout (adds to infra §3)

```
~/.orcapipe/agents/<name>/
  agent.yaml           # model, weight, account affinity
  ROLE.md
  base.md              # ← new
  journal/
    2026-09-15.md        # git keeps the generations (~/.orcapipe is a git repo)
  state.json
```

---

## 8. Order of introduction: do not start with 7

Seven agents cannot be carried on 2 or 3 accounts. v1 is a **fixed agent-per-account mapping** (infra §0.5), so the number of accounts is the cap.

| Stage | Agents | Why |
|---|---|---|
| **1** | `backend`, `reviewer`, `archivist` | The minimal loop. Once implement → review → record runs, the rest are copies |
| **2** | `+ frontend`, `+ product` | Check cross-team requests and the escalation path |
| **3** | `+ marketing`, `+ analytics` | The growth team |

`archivist` and `reviewer` do not hold an account long, so they can share one account. In practice only `backend` and `frontend` hold an account for long.

## 9. Open decisions

1. **Is `product` a supervisor or a peer?** The `team.yaml` above has it as a peer. If it were a supervisor, product could preempt eng's work
2. **Whether to split the reviewer by team later.** Start with one, and split when base.md can hardly hold three teams' criteria. That point is itself the signal to split
3. **`analytics` data access.** Depending on the metric source an MCP connector may be needed. Discussed separately

---

*2026-09-16 · companion to infrastructure design v0.5*
