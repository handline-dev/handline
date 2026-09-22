# Decisions: extending ocp past Claude

What is **settled**, so it is not re-argued or forgotten. Small on purpose.

The two working documents move as the survey moves: `orca-cli-survey.md`
(what Orca offers) and `vendor-extension-board.md` (seam-by-seam verdicts).
This file does not move. A line leaves the OPEN list only when a measurement
settles it, and then it moves up with its evidence.

Asked by the founder in #1255 (withdrawn, superseded live) and #1277.
Decisions D1-D11 taken 2026-09-21.

---

## Decided

### D1. Account placement stays ours. Forever, for every vendor.

Orca runs **one active account per provider** - `account list --json` returns a
singular `claude.activeAccountId` and `activeAccountIdsByRuntime`, filing the
rest under `inactiveClaudeAccounts`. It is a switcher, not concurrency, and no
command (`worker-start`, `worktree create --agent`, `terminal create`) selects
an account.

ocp's premise is N accounts at once. Orca cannot host it.

**Forecloses:** `worker-start --agent` replacing `ocp launch`. Not a
performance question or a "later" - it cannot express the requirement.
**Implies:** if we want Orca's supervision at all, the only door is adoption,
`worker-start --terminal <handle>`, after our own launcher has made the
terminal. Whether to go through that door is **O1**.

### D2. Quota stays ours. Its shape becomes Orca's.

**How Orca gets usage without any statusLine** (#1353): it asks the provider's
API directly. `account list --json` labels its own method -
`rateLimits.claude.usageMetadata = {source: "oauth", attemptedSources:
["oauth", "cli"], credentialSource: "scoped-keychain", lastSuccessfulSource:
"oauth"}`. It reads the account's OAuth credential out of the keychain and
calls the endpoint, with a `cli` fallback. No TUI is read, no session need be
running - which is exactly why it can report on an account nothing is using.
Nothing about this touches `statusLine`; Orca's agent integration is hooks
(D9), and usage is a separate OAuth path.

**Why ours stays - restated 2026-09-21 after a second measurement.** The first
reading found Orca's figures 40.3h, 42.9h and 40.3h old, and this decision
rested on "too stale to act on". That is not a standing property: re-measured
during #1353 the **active** account's figure was **0.8h old** and accurate
(Orca session 100% / weekly 94%; ocp for delta 5h 100% / 7d 93%, 44m old - the
same account, in agreement). So the staleness argument is withdrawn as stated.

What survives is the **per-account** channel, and it is D1 again. Orca does
have one - `rateLimits.inactiveClaudeAccounts[]` - and across two measurements
it has never been usable:

| measured | `inactiveClaudeAccounts[]` | the active account |
|---|---|---|
| first pass | 3 entries, **40.3-42.9h old**, `isFetching: false`, disagreeing sharply (ehalim111: ocp 5h 15% / 7d 87% vs Orca 100% / 31%) | 3.4h old |
| #1353 | **empty - 0 entries** | 0.8h old, agrees with ours |

So the invariant is not "Orca's numbers are old". It is that **the number is
fresh for exactly the one account Orca calls active, and absent or stale for
every other** - while ocp is balancing five at once and needs all five
continuously. Right now ocp reads alpha 5h 98%, beta 7d 100%, delta 5h 100%,
gamma 7d 100%, omega 5h 7%, with five different 7d reset dates (09-23 to
09-27); omega, the account every agent is running on, is the one Orca has
nothing for.

A third window is named differently again - Orca has `session` / `weekly` /
`fableWeekly` where we hard-code `pct_opus_7d` - which is the shape argument
below, intact.

So: **adopt Orca's data shape, not Orca's data.** The window model becomes a
list keyed by duration - Orca's `windowMinutes`, codex's `window_minutes` -
plus per-provider `status` / `error`, replacing the hard-coded `pct_5h` /
`pct_7d` / `pct_opus_7d` triple (208 lines across 16 files).

**Why it matters most:** this is the deepest coupling in ocp and the one that
does not look like coupling - five of those sixteen files never mention
`claude`. **Forecloses:** a runtime dependency on Orca for any number that
gates a decision. **Superseded in part by Q4 (#1360):** the
per-account ceiling turned out to be Orca's, not the API's - the same endpoint
Orca calls answered for an account Orca does not consider active, with numbers
identical to our statusLine's. So the question is no longer whether to wait for
Orca to report per account; it is whether we call the endpoint ourselves. D2's
conclusion (our own quota layer, in Orca's shape) is unchanged and now rests on
a firmer floor.

### D3. The request queue is not replaced by Orca's mail.

Orca has typed, addressed, threaded mail with gates. ocp's queue has different
semantics and they are the product: one delivered request at a time,
`--replaces`, cycle detection, the human lane, refusal of unlisted edges.
Porting it would be a rewrite, not a deletion.

**Narrow exception, already on the board:** `gate-create` for the
blocked-escalation path, which is genuinely the same object.

### D4. Vendor differences live in a driver, behind the existing field.

`agent.yaml`'s `vendor` field already exists (`agents.DEFAULT_VENDOR`,
`agents.parts`, `agents.kind`) and `burnrate` already prices by `vendor/model`.
The seam was left open on purpose. Per-vendor behaviour goes behind one driver
interface, not `if vendor == …` at the call sites - today there are seven
separate `shutil.which("claude")` lookups and 28 inline slash-command sites.

### D5. The bar for "Orca does this instead".

Orca must already answer **the exact question, for every vendor, without us
parsing a vendor's rendering**. "Orca has a related verb" is not enough; that
is at most a hybrid. Written down because it is the rule that keeps the board
honest in both directions.

### D6. Never size a vendor's support by `--help`.

Learned the hard way: this study first concluded opencode was "a command string
and nothing more" from the CLI's examples. The app bundle carries a registry of
**18 agent families** with per-family hook events, session scanners and title
derivation, opencode among them. The CLI is the narrowest view of Orca. Check
the product, not the flag list.

### D7. opencode is supported as an agent, not as a supervised worker.

Three tiers, and they do not line up: **any** command runs (tier 1); **18
families** get identity, activity, liveness and hooks (tier 2, opencode
included); only **Claude, Codex, Cursor** get the orchestration launcher and
managed accounts (tier 3). The gap is filed as Orca issue
[#19823](https://github.com/stablyai/orca/issues/19823), open since 2026-09-10.

**Consequence for us:** since D1 says we never use the tier-3 launcher anyway,
opencode's exclusion from it costs us **nothing**. It is as viable a vendor for
ocp as codex.

### D8. The Run leak: `run_exists` reads "could not ask" as "gone".

**Revised 2026-09-21 after investigation (#1292). The first diagnosis named
the wrong culprit** - it blamed the `bound != run` overwrite, which is real but
secondary. The primary defect is four lines away:

```python
def run_exists(run: str) -> bool:
    """...reading the wrong one as "gone" made a new Run for every agent."""
    ok, out = _run(["orchestration", "run-show", "--id", run, "--json"])
    return ok and bool(_result(out, "run", "id"))       # <-- ok=False -> "gone"
```

Any failure - Orca restarting, the runtime not up yet, a timeout, a non-zero
exit - returns `False`. `ensure_run` reads that as "the team's Run is gone",
calls `run_create`, and overwrites `RUN_KEY` with the new one. **One Orca
restart during a wave of launches is N new Runs.** Nine of ours were created
inside 33 seconds.

ocp already knows this rule and applies it correctly two functions above:

```python
def live_terminals() -> set[str] | None:
    """...None is not an empty set. Reading "I could not ask" as "there are
    none" would tear down a healthy team the first time Orca was slow."""
```

Terminals get the tri-state. Runs do not. And `run_exists`'s own docstring
records that this exact symptom already happened once, was diagnosed as a wrong
flag, and the fail-open shape was left in place.

**Measured (read-only).** 25 Runs named "orcapipe team" of 28 on the machine;
**12 of them coordinated by 12 different agent terminals**; the current Run at
`consumer_generation` 21, i.e. 21 takeovers. And live during this session, with
no human action, `RUN_KEY` moved `run_5f5c6f452853` → `run_1dd4c9a72be3`.

**Two earlier claims of mine were wrong; both are withdrawn.**

*The ~9,000 tasks are not this bug, and not production.* 1,601 of the 1,770 in
the largest Run are test fixtures - `launchtest`, `st`, `spawny` - so the suite
was creating real Tasks against the live Orca runtime. **That is already
fixed**: the guard merged at 606e304 on 2026-09-21 01:01, and the newest
fixture task is 2026-09-20 16:26. Historical residue, not an ongoing leak. ocp
creates one Task per agent and reuses it.

*It is not a prerequisite for reading the fleet.* I claimed scattered Runs
block the one-call fleet view. They do not: an **unbound** caller gets every
Run's workers at once - measured, `scope: {"source": "all"}`, 24 workers in one
call - and ocp's own callers are unbound. Reading `activity` and `liveness` for
the fleet works today, scattering and all.

**Cost, stated honestly - it is smaller than it first looked.**

| consequence | kind |
|---|---|
| `task-create --parent` refuses across Runs, so **9 of 15** agents with a supervisor have no parent link; harbor's six reports all draw flat | **visual, in a view nobody here uses - see below** |
| **2 of 18** agents (`harbor.marina`, `personal`) have no dispatch at all, so `worker-list` cannot report their `activity` or `liveness` | **functional, but only if we build on those signals** |
| a new Run per Orca restart, ~8/day at the observed rate | clutter, and unbounded |

Work delivery is unaffected: 16 of 18 agents still have a dispatch on their
current terminal. Nothing is down.

**So fixing this does not shrink ocp.** The fix adds a tri-state and removes a
branch - about ten lines, deleting no feature. The case for it is that
`run_exists` is a correctness bug of a class this codebase explicitly rules
against four lines away, and that it keeps making Runs forever. The case
against doing it *now* is that its visible benefit is a correct org chart.

**It becomes a real prerequisite only if we commit to moving observation onto
Orca (D9),** because then the two dispatch-less agents are blind spots and a
Run identity that changes on every Orca restart is something we would be
depending on.

**Decided - the fix, in three parts:**

1. `run_exists` becomes tri-state (`True` / `False` / `None` for "could not
   ask"), and `ensure_run` creates a Run **only on a definite `False`**.
2. Drop the `bound != run` overwrite. The stored key is authoritative; a
   terminal's incidental binding never rewrites it. `run_use` brings this
   terminal to the team's Run instead of taking its word for where it is.
3. One creator. `ocp org` already exists for exactly this job and runs once,
   deliberately, in order (supervisors first). Launch should join, never create.

**Keep:** the flat-task fallback in `register_worker`. It is not a bug; it is
firing 60% of the time and is the only reason anything is drawn at all.

**Test gap:** the one test that touches this stubs `run_current` and
`run_create` to `None`, so neither the create-on-failure path nor the overwrite
is covered. The fix needs a test that drives `run_exists` returning `None`.

**Cleanup is a separate decision, not ours to take.** There is no `run-delete`
in the 234 commands - only `orchestration reset --all | --tasks | --messages`,
which is global and would also destroy orchestration state belonging to the
agents' own work (the "Art direction" and "Harbor scene fixes" Runs are
theirs, not ocp's). Re-pointing `RUN_KEY` at one Run and re-running `ocp org`
rebuilds the tree without deleting anything; the old Runs then sit there, inert
and noisy.

**Where is that org chart actually drawn? Measured (#1301) - there are three
trees, and D8 touches the one nobody looks at.**

| tree | built from | who sees it | D8? |
|---|---|---|---|
| **`ocp bridge` / `ocp ps`** | `team.yaml` via `actions.agent_tree()` | **this is the one we use** | **no effect** - `tui.py` never reads an Orca Run or Task |
| **Orca's worktree lineage** | `parentWorktreeId`, set by Orca from the spawning context | the Orca sidebar | **no effect** - and it is **already correct**: `expender→orcapipe`, `auditor→orcapipe`, `keeper→orcapipe`, `purser→orcapipe`, `foam→harbor` |
| **Orca's Run/Task tree** | `task-create --parent` | the desktop app's orchestration view only | **this is the one D8 fixes** |

`orca worktree ps` shows a flat per-worktree list, not the Task tree, so the
Task tree has no CLI surface at all. Neither ocp nor anything in the founder's
terminal workflow reads it.

**Priority: no.** The visible benefit of fixing D8 is a tree in a view nobody
here opens, and the two trees people do look at are unaffected and one of them
is already right. What remains is that `run_exists` is a correctness bug of a
class this codebase forbids four lines above it, and that it makes a Run per
Orca restart forever. Do the `run_exists` tri-state whenever `orca.py` is next
open; schedule the rest only if the observation move (D9) makes the Run
identity something we depend on.

### D9. Detection moves to Orca; reading the panel stays ours. The statusLine
stays - **for rate limits only** (revised #1357; it first said "permanently",
for the wrong payload).

Two measurements, both read-only, both correcting an earlier claim of mine.

**`agentWait` is on the terminal, not the dispatch.** I first looked only at
`worker-show` and `worker-list`, found no `observation` on any of 24
dispatches, and said Orca never looks. Wrong object. `orca terminal show --json`
carries `terminal.agentWait` on **32 of 32** terminals and it was non-null on
exactly the one that was waiting - our own `harbor`, sitting on Claude Code's
new-session box, which `orca.py` calls BACKGROUNDED. Orca caught it
vendor-neutrally, with its evidence (`{"source": "title"}`), **with no adoption
at all**.

**Decided:** `screen_blocker`'s *decision* becomes one `terminal show` field.
What stays ours is *describing* the panel - `parse_dialog`, `declining_key`,
`describe_modal` - because `agentWait` gave a source and nothing else, and
`decline_dialog` must never press Esc on a panel it could not read. Much
smaller than 150 lines, and no longer the thing that decides.

**The statusLine injection is permanent, and this does not depend on the quota
question** (re-measured for #1356). Asked whether taking usage from Orca would
let us drop the injection: no, and the two are independent. The collector's
payload is **five groups, only one of which is rate limits**, and the other
readers do not care where quota comes from:

| payload | readers | other source? |
|---|---|---|
| rate limits (5h / 7d / opus_7d + resets) | `status.py`, and everything downstream | Orca, for **1 of our 5** accounts (D2) |
| **context window** (`ctx_pct`) | `triggers.py`, `daemon.py`, `actions.py`, `status.py`, `cli.py`, `tui.py` | **none** |
| **session identity** (`session_id`, `transcript_path`) | `db.py`, `events.py`, `handoff.py`, `status.py`, `actions.py`, `daemon.py` | **none** |
| `model_id`, `cost_usd` | `status.py` | none |
| `ctx_size`, `ctx_used`, `lines_added`, `exceeds_200k` | **no reader** - collected, never used | n/a |

`triggers.evaluate(pct_5h, ctx_pct, pct_7d)` parks an agent on `ctx_pct`, and
`handoff.py` writes "previous session: `<session_id>`, recorded at
`<transcript_path>`" - the line a resumed agent reads to find its own history.
Re-checked for #1356: **`agent-context --json` lists 236 commands and not one
mentions a context window or a token count** (`contextWindow`, `context_window`,
`tokenCount`, `token_count`: 0 occurrences each). So the injection stays whole
even in the best case for quota, and dropping usage from it would delete about
eight lines of jq.

**Correction (#1357): D9 had this exactly backwards.** Asked whether the
context window is really needed, and what codex would do for session identity,
I measured both - and the answer inverts the claim above. **The statusLine is
not the architecture; it is Claude's convenience.** The vendor-neutral
mechanism is to *pull from the session file*, and it already works on both
vendors:

| payload | Claude statusLine (push) | Claude transcript (pull) | codex rollout (pull) |
|---|---|---|---|
| context used | `ctx_pct` | **yes** - sum of `usage` on the last assistant message; measured **170,893 tokens** on a live transcript | **yes** - `info.total_token_usage.total_tokens` |
| context size | `ctx_size` | from the model id | **yes** - `info.model_context_window` = 258,400 |
| session identity | `session_id`, `transcript_path` | **the file is both** | **yes** - `session_meta.id` / `session_id`, and `~/.codex/session_index.jsonl` indexes every one |
| rate limits | `pct_5h` / `pct_7d` / `pct_opus_7d` | **no** - only a `429` error record when the wall is already hit | **yes** - `rate_limits.primary{used_percent, window_minutes, resets_at}` |

Measured on 1,869 codex rollouts: **1,868 carry the `token_count` record** (the
one that does not is a single-turn `codex exec`). So codex needs **nothing
injected at all** - it writes its own identity, its own context window and its
own rate limits, the last already in D2's `window_minutes` shape.

**So the one payload that only the statusLine can give is rate limits** - the
opposite of what this decision said. Context and session identity both have a
pull source on both vendors.

**And that last claim expired within the day (#1397).** Q4 gave rate limits a
pull source too, so *nothing* the statusLine carries is now without an
alternative. It stays anyway, and the reason is the direction of the cost, not
the absence of a substitute: the statusLine is **pushed** - free, on every
turn, for any session that is running - where every alternative is **pulled**,
a call or a file scan per check. For an account with work on it the statusLine
is strictly better, and replacing it would be more machinery and more calls for
a worse answer.

What actually changed is its standing: from *load-bearing, no alternative* to
*the cheapest path where it works*. The two now cover disjoint cases, measured
on the live fleet:

| account | last write | age | why |
|---|---|---|---|
| `alpha`, `omega` | statusLine | **0m** | a session is running; the collector writes every turn |
| `beta`, `delta` | statusLine | 308m / 162m | quiet - the collector **cannot** speak, and this is the gap |
| `gamma` | **api** | 10m | asked, because it was quiet |

**The marker is self-healing**, checked by driving the real collector script
rather than reasoning about it: the collector rebuilds `status.json` from its
own fixed field list, so a collector write **drops `rl_source`**. It therefore
means "the last write was the API", and a live session reclaims the account on
its next turn with no cleanup anywhere.

**And the context trigger is weaker than stated.** Measured over the fleet's
whole history: **271 parks - 244 `usage`, 22 `usage_7d`, 5 `context` (1.8%)**.
`ctx_pct` was `None` on **5 of 19** agents at the time, so it already failed
silently a quarter of the time, and every agent was on a **1M** window, which
put `CONTEXT_PCT = 60` at 600k tokens - the usage wall nearly always arrived
first (superseded by the absolute-token `CONTEXT_TOKENS`, #1669, which closes
the silent-`None` hole outright). The five that did fire were `auditor` x3, `chat` and `orcapipe`: agents
that read large diffs, where the fallback is Claude's own auto-compaction -
unmanaged, lossy, and with no handoff written. So it earns its place, but as a
rare backstop, not as "the feature that keeps the fleet running unattended".

**Revised decision.** Keep the injection while it is the only source of rate
limits per account. Move context and session identity to a pull from the
session file, because that is the form codex needs anyway - one mechanism for
both vendors instead of a push we can only build for one.

**The prize the question is reaching for is real, though - it is just behind
D2's precondition.** Our readings are opportunistic: a session learns its rate
limits only when it makes an API call, so an idle account goes quiet and we
built a five-case trust model to cope (`carried` / `carried_old` / `quiet` /
`unknown` / live, plus `idle_floor` and the high-water mark, in
`status.py`'s 1,023 lines) and `probe.py` (172 lines) to spend quota forcing a
number out of a quiet account. Orca's OAuth pull has neither problem - it can
ask about any account at any time with nothing running. **If
`inactiveClaudeAccounts[]` were ever populated and fresh, that whole apparatus
could go**, and it is far larger than anything on the Claude-only list. It is
the single best reason to keep re-checking that field. What it would *not* buy
is the statusLine.

**Superseded by D11 (#1402).** "Keep the injection while it is the only source
of rate limits per account" did not survive the question being asked again: the
poll is that source now, and the statusLine collects nothing. What this decision
got right is the sentence above - the pull is the architecture, the push was
Claude's convenience.

**Implication for vendor two:** codex has no statusLine command hook, so its
collector **pulls** from the rollout JSONL
(`token_count.info.model_context_window` + `total_token_usage`, beside the same
`rate_limits`) where ours is **pushed**. Verified present on this machine. Same
output file, different mechanism - and it is the first thing to build for any
new vendor, because without context ocp cannot park.

### D10. The agent's preamble is a file in its worktree, not a flag.

`--append-system-prompt` is replaced by one file ocp writes into the agent's
own worktree at **launch** (not at worktree creation - the team line is
generated from `team.yaml`, `hr` can edit ROLE.md and base.md, and the handoff
must be consumed rather than persisted), kept out of git via
`.git/info/exclude`, and covered by `launch.fingerprint` as it is today.

The entire per-vendor surface:

```python
PREAMBLE_FILE = {
    "claude": ("CLAUDE.local.md",    ADDITIVE),      # read beside CLAUDE.md
    "codex":  ("AGENTS.override.md", SUBSTITUTIVE),  # replaces AGENTS.md
}
```

One string and one flag. On codex ocp appends the repo's own `AGENTS.md` after
its preamble, because the override suppresses it; on claude it must not, or the
content loads twice. Content, timing, git exclusion and fingerprinting are
shared.

**Measured, not assumed** (#1306, #1307): codex loads the file with no flag,
as a `role: user` block labelled "AGENTS.md instructions"; `AGENTS.override.md`
replaces `AGENTS.md` rather than joining it; Claude reads `CLAUDE.local.md` at
runtime and its own codex importer maps the two names to each other.

**Accepted cost:** this level is ranked *below* direct instructions by codex's
own spec, so ocp's hard rules become advisory in a way they were not under the
flag. Taken deliberately - cheap and working beats authoritative and
per-vendor. **Unverified:** whether codex's `/new` re-reads it.

**This retires the ⚠ on the hardest DRIVER row** and moves it to RESHAPE.

### D11. The statusLine collects nothing. It shows the human what the agent is
doing, and every figure it used to carry is pulled.

Asked by the founder in #1402: the status line should do something more
valuable, what it carries can be had from polling and the transcript, and the
slot is wanted for something else. With the codex objection stated plainly - a
mechanism that cannot exist on vendor two should not be load-bearing on vendor
one.

**Done.** The collector (`libexec/statusline-collect.sh`, 179 lines, half of it
a carry rule written in jq) is deleted. `ocp/collect.py` reads the same fields
out of the file the session is writing anyway. ocp installs no status line at
all (see "The slot is left empty" below).

| payload | before | now |
|---|---|---|
| context window | `context_window.used_percentage` | the last assistant turn's `usage`, over the model's window |
| session identity | `session_id`, `transcript_path` | the transcript file **is** both |
| which account | `CLAUDE_CONFIG_DIR` | the transcript's own path, `accounts/<acct>/.claude/...` |
| cost, lines | `cost.*` | the session's `cost-state` record |
| rate limits | `rate_limits.*` | `usage_api`, polled per account (D2, Q4) |
| `ctx_size`, `exceeds_200k`, … | collected, no reader | not collected |

**Checked against the collector on the live fleet before anything was
switched.** On all 14 agents that had a figure the pull reproduced the
statusLine's `current_usage` **to the token** (86,085 / 151,976 / 469,196 …) and
its percentage exactly, in 1-2ms each. On three more it read **0%** where the
push was still reporting the context of a session that had been cleared - a
`/clear` starts a new transcript, and `orcapipe.chat`'s was eleven lines with no
turn while its snapshot said 20%. On `harbor` it found a **live session the push
had missed for five hours**: that session runs without `OCP_AGENT`, so the
collector could not tell whose it was, and the pull does not need to be told.

**Three mechanisms, because they fail differently:**

- **the hooks we already install** - `Stop` fires at the end of every turn,
  carries the `cwd`, and already runs `ocp hook`. This is the same per-turn
  freshness the statusLine gave, and a `Stop` arriving is also the **heartbeat**,
  which is the one thing no pull can reproduce. `SessionStart` is included for
  the `/clear` case; `SessionEnd` is not, or a dead session would look fresh.
- **the daemon tick**, for the sessions no hook spoke for - `harbor`'s case is
  why this net exists rather than trusting the push.
- **the usage endpoint**, for the one group no transcript carries. Asked for one
  account per tick, and only when something could have changed: a turn on it
  since the last reading (`db.last_account_activity`), a window that has ended,
  or a cadence kept anyway for the spending nothing records (`ocp fold`'s
  `claude -p`, a human at a terminal). An idle fleet therefore costs no requests
  at all, instead of a round-robin every two minutes.

**`collected_at` is the transcript's own mtime, not when we read it.** That one
choice is what let every reader in `status.py` stay exactly as it was: staleness
still means "the session stopped writing", `read_for_agent` still refuses a dead
session's context, and `doctor`'s "a snapshot moving with no process" check still
holds. A pull that stamped its own clock would have reported every dead session
as live for ever.

**The slot is left empty (founder, #1592).** A display of the agent's state
(`libexec/statusline.sh` printing `agents/<agent>/line.txt`) was built here and
then removed: the founder runs their own status line (ccstatusline), and ocp has no
business in that slot. `ensure_shared` now deletes both of ocp's old scripts and
removes a `statusLine` entry only when it points at one of them. Anyone else's is
kept. Nothing parses stdin any more, which also retires **jq as a dependency of
ocp**.

**What this deleted, honestly.** 179 lines of bash, the statusLine install and
its chain variable, three doctor checks, jq, and `test_collector`. And the carry
apparatus's *reason*: the endpoint states both windows or answers nothing, so
there is no payload that quietly omits one and nothing writes `rl_lost` any
more. The **reading** rules in `status.py` stay whole - `quiet`, `unknown`,
`carried_old` and the floor are what a failed poll or a dead token still needs -
so this is a smaller cut than D9's "that whole apparatus could go" implied.
`probe.py` is also still here: its conditions are now nearly unreachable, but it
is the only way to learn usage when the endpoint cannot be asked at all.
Deleting it is a separate decision with the founder.

**Upgrade note:** the old entry is only removed when `ensure_shared` next runs
(`ocp init`, or registering an account). Until then `ocp doctor` fails "shared
statusLine is an old ocp script".

**Implication for vendor two:** `collector_kind` is now `pull` for both vendors.
codex needs nothing installed, writes its own context, identity and rate limits
into its rollout JSONL, and the driver's per-vendor surface shrinks to "where
are this vendor's session files, and how is a turn read out of one".

---

## Open, with what would settle it

| id | question | settled by |
|---|---|---|
| **O1** | Supervised lane or handoff lane? **Narrowed by D9:** adoption is no longer needed for `agentWait`, so all it would still buy is the lifecycle - a `resource` row and `worker-stop`/`worker-release` acting on the process. We create Run + Task + Dispatch and settle none (D8). Either adopt terminals with `worker-start --terminal`, or stop creating Tasks and keep only what draws the team. | measuring what adoption grants - **needs the founder's go-ahead: it mutates a live fleet** |
| ~~O2~~ | *settled 2026-09-21 - see D9.* | |
| ~~O3~~ | *settled 2026-09-21 - see D10.* One residue: does codex's `/new` re-read the file? | a live codex TUI session; costs tokens, deferred to when a codex agent is first run for real |
| ~~O4~~ | *settled 2026-09-21 - see D8.* | |

**Rule for this list:** nothing below the line is designed on. O3 in particular
is load-bearing for the whole team model and must not be guessed at.

---

## Queued work

Recorded so it is not lost. **Nothing here is started.** An item leaves this
list by being done, or by being cancelled with a reason.

### Q1. `orca.run_exists` - stop reading "could not ask" as "gone"

**Trigger:** next time `ocp/orca.py` is opened for anything. Not worth a branch
of its own (see D8: the org chart it would fix is drawn in a view nobody here
opens).

**Why it still stands on its own:** every failure of the call - Orca
restarting, runtime not up, timeout, non-zero exit - currently returns `False`,
and `spawn.ensure_run` reads that as "the team's Run is gone" and creates a new
one. A Run per Orca restart, forever. **Re-counted 2026-09-21 during #1328: 28
Runs**, up from 25, and the fleet's 24 workers are scattered over 11 of them -
**17 Runs hold nothing at all**. One was created in front of me: this session
moved to another account, got a new terminal, and `worker-list` with no `--run`
resolved to `run_3b06fdd6d8e7`, bound and empty. This is the exact shape
`orca.live_terminals` forbids four lines above, in a docstring that explains
why.

```python
# ocp/orca.py
def run_exists(run: str) -> bool | None:      # None = could not ask
    ok, out = _run(["orchestration", "run-show", "--id", run, "--json"])
    if not ok:
        return None
    return bool(_result(out, "run", "id"))
```

and in `spawn.ensure_run`, create **only on a definite `False`**. A `None`
means leave `RUN_KEY` alone and skip registration this launch.

**Test it needs:** the one existing test stubs `run_current` and `run_create`
to `None`, so neither the create-on-failure path nor the overwrite is covered.
Add a case that drives `run_exists` returning `None` and asserts no Run is
created and `RUN_KEY` is unchanged.

**Do not bundle:** the other two parts of D8 (dropping the `bound != run`
overwrite, single-creator via `ocp org`) and any cleanup of the 25 existing
Runs. Those wait for the observation move, and cleanup is the founder's call -
there is no `run-delete` verb, and `orchestration reset` is global and would
destroy the agents' own orchestration state.

### Q3. The hook mirror runs once and is never re-run (#1347)

**Measured 2026-09-21:** `hooks.plan()` returns `drifted=True` on this machine.
Orca's `~/.claude/settings.json` carries **13** hook events; our
`shared/.claude/settings.json` carries **12** - `SessionEnd` is missing.

`hooks.sync()` is called from exactly one place, `cli.py`'s `ocp init`, plus
the explicit `ocp hooks sync`. The watcher (`--watch`) is opt-in and is not
running. `doctor.py` *reports* the drift and does not repair it. So the mirror
is correct only until Orca ships a hook event, and then it silently is not.

**Why it is now worth doing:** it was cosmetic while the mirror only fed a
dashboard. If `stage.activity` replaces `orca.idle_now` (board §"Claude only",
item 1), an unattended mirror becomes load-bearing for a decision the daemon
makes every tick. **Do this before that swap, not after.**

Smallest fix: have the daemon call `hooks.sync()` on the same tick it already
does its other housekeeping, and let `hooks.py` take its source path from
`orca agent hooks status --json`'s `configPath` rather than hard-coding
`~/.claude/settings.json` (board item 4 - the same ten lines).

### Q2. Build the vendor driver, steps 1-4

Drafted in `design/03-vendor-driver.md` §7. Steps 1-4 are **vendor-neutral
refactors that need codex to exist for nothing**: move today's behaviour into
`ocp/vendors/claude.py` unchanged; make `observe.account_of_process` read the
config-dir variable from the driver; reshape the window model (D2 - the 208
lines across 16 files, writing both old and new keys for one release); and
move the preamble to a file (D10).

Not started. Step 3 is the one with real risk and should be its own branch.

### Q4. Pull rate limits over OAuth ourselves, for all five accounts (#1358)

**Done 2026-09-21 - built in #1369/#1381, and polled for every account in
D11.** The daemon asks one account per tick, on evidence that something could
have changed. What was *not* collected: the carry/quiet/unknown fallback stays
(the endpoint can still fail) and so does `probe.py`, for when it cannot be
asked at all. The record below is what settled it.

**Measured, not built.** The read half is proven (below). The write half - the
token refresh - is **not started and needs the founder's go-ahead**, because it
rotates a live credential. Note this crosses a line `accounts.logged_in`
deliberately holds today: it calls `security find-generic-password` **without**
`-w` so it never touches the secret.

**What makes it possible:** Orca already does it, and says so -
`usageMetadata = {source: "oauth", credentialSource: "scoped-keychain"}`. It
reads an account's OAuth credential out of the Keychain and calls the endpoint
**with no session running**. Our five accounts sit in the same Keychain, one
entry each (`accounts.keychain_service`: `Claude Code-credentials-<8 hex of the
config dir's sha256>`, verified against a live login). So the thing Orca does
for one account, we could do for five - which is D2's whole objection.

**Why it is the largest reduction on any list here.** Rate limits ride on API
**responses**: a session learns them only by making a request, which is why
`probe.py` must type a real prompt ("a session that has asked nothing has
learned no rate limits ... which reads as a quiet window") and why an idle
account goes silent at all. Remove that constraint and two things go:

| | lines | why it exists |
|---|---|---|
| `probe.py` | 172 | forces a reading out of a quiet account: a pty, a session boot, one Haiku turn, up to 90s, 30-min cooldown - and it spends quota to learn a number |
| most of `status.py`'s trust model | of 1,023 | `carried` / `carried_old` / `quiet` / `unknown`, `idle_floor`, the high-water mark - all of it copes with readings we can only take opportunistically |

#### Measured 2026-09-21 (#1360). It works, and the obstacle is not the one I expected.

The call is not guessed - it is Orca's own, read out of its bundle:

    GET https://api.anthropic.com/api/oauth/usage
    Authorization: Bearer <claudeAiOauth.accessToken>
    anthropic-beta: oauth-2025-04-20
    User-Agent: claude-code/2.1.0

**1. It answers, and it agrees with us.** On `omega`: HTTP 200 in 470 ms,
`five_hour.utilization` **36.0** with `resets_at`, `seven_day` **17.0**, plus a
`limits[]` array (`kind`, `percent`, `severity`, `resets_at`, `scope`) that
already carries the per-model weekly D2 wants. ocp's own statusLine for `omega`
at the same moment: `pct_5h` **36**, `reset_5h` **identical to the second**,
`pct_7d` 16. Same numbers, from a source that needs no session.

**2. It answers for an account Orca does not consider active.** Orca's active
account is `delta`; this ran on `omega`. So the one-account limit in D2 is
**Orca's, not the API's** - the ceiling was never in the endpoint.

**3. The obstacle is the token, and it lands exactly where we need it not to.**
The access token expires on its own short clock, unrelated to any rate-limit
window. Measured:

| account | state | access token | result |
|---|---|---|---|
| `omega` | live session | valid **+1.1h** | **HTTP 200** |
| `delta` | idle | valid +0.8h | not called |
| `gamma` | idle 2h+ | **expired -1.9h** | **HTTP 401** "OAuth access token has expired" |

Claude Code refreshes the token **while it runs**. So an account with nothing
running is exactly the account whose token has gone stale - and that is
precisely the account the statusLine cannot report and that `probe.py` exists
for. The gap the API was meant to fill is the gap where its credential is dead.

**4. Solvable, and the hazard is complementary to the need.** Every Keychain
entry carries `refreshToken` and `refreshTokenExpiresAt` beside the access
token, so a refresh is ordinary OAuth. Orca does it (`shouldAttemptDelegated
Refresh`, `hasRefreshableCredentials`) and **defers it while a live Claude
session is running** (`managedRefreshDeferredByLivePty`,
`deferredByLiveClaudeSession`) because both would rotate the same credential.
For us that conflict cannot arise: we only ever need to refresh an account with
**nothing running**, which is exactly when refreshing is safe.

**5. The polling question is settled, and in the founder's favour.** Q4 first
said the call "must be safe to call every tick". Wrong requirement. Every
reading carries `resets_at` for both windows, and an idle account's usage
cannot rise on its own - so the schedule is **one call per account per window,
plus one when a window turns over**, not a tick. The one caveat is already on
record in `status.py`: "nothing of ours is running" is not "nothing is
spending" (`ocp fold`, or the founder's own sessions, spend too). That argues
*for* the call - it replaces an inference we already know to be unsafe with a
re-check that costs 470 ms.

#### The refresh: attempted, and it does not work from a plain client (#1363)

Approved and tried on `gamma` (idle, so nothing to race with). The mechanism is
Orca's own, again read out of the bundle rather than guessed:

    POST https://platform.claude.com/v1/oauth/token
    Content-Type: application/x-www-form-urlencoded
    grant_type=refresh_token & refresh_token=<rt> & client_id=9d1c250a-e61b-44d9-88ed-5944d1962f5e

with the reply merged into `claudeAiOauth` - `access_token`, `expires_in` ->
`expiresAt`, and `refresh_token` / `scope` **only when present** (so the server
may or may not rotate the refresh token).

**Result: HTTP 403, Cloudflare `error code: 1010`, in 86 ms.** That is a client
**fingerprint** rejection, not an auth failure. Nothing was written - the script
only persists on a 200 - so the credential is exactly as it was.

**The two halves are gated differently, and that is the finding.**

| | host | our plain client |
|---|---|---|
| read usage | `api.anthropic.com/api/oauth/usage` | **200**, 470 ms |
| mint a token | `platform.claude.com/v1/oauth/token` | **403**, Cloudflare 1010 |

Orca gets through because it calls via Electron's `net.fetch`, which carries a
Chrome-like TLS/HTTP signature. A retry with `curl` and a browser `User-Agent`
was **not** run - the local permission classifier refused it (`Secret-Store
Writes`), so whether a different signature passes is **untested, not disproven**.

**What this costs Q4 if the refresh cannot be made to work.** The usage call
needs a live access token, and Claude Code only refreshes one *while it runs*.
So the band where the API helps is "idle, but not idle long enough for the
token to expire" - and an account idle under an hour still has a usable
statusLine reading anyway. `gamma` was expired after ~2h idle. Without a
refresh, Q4 does not reach the accounts it was built for, and `probe.py` stays.

**Keychain write mechanics, verified on a throwaway entry** (so this is not
re-learned later): `security add-generic-password -U -s <svc> -a <acct> -w
<value>` creates and updates correctly. The documented stdin-prompt form
(`-w` last, no value) reads from the **tty, not a pipe**, and silently stores an
empty password when piped. The inline form puts the secret in **argv**, which
`ps eww` exposes - and `observe.process_env` on this very machine reads exactly
that. A real implementation must call the Security framework directly rather
than shell out.

#### Settled (#1364): the CLI refreshes the token, so the raw POST is not needed

The retry was approved, and it made the question moot. Two results.

**1. The raw token endpoint stays shut, and it does not matter.** `curl` with a
browser `User-Agent` gets **past** Cloudflare where `urllib` did not - the 403
`1010` becomes a **429 `rate_limit_error`** from the API itself. Still 429 two
minutes later, no `Retry-After`, 55 ms (edge-cached). Not pursued further,
because:

**2. `claude auth status` refreshes the token itself.** Run against an account's
own config dir, it renewed `gamma`'s credential from **expired -1.9h** to
**valid +7.9h**. So the access-token lifetime is **~8 hours**, and the refresh
needs **no raw OAuth call, no fingerprint spoofing, and no Keychain write of
ours** - Claude Code performs the write. It spends no quota: there is no model
call in an auth check.

**The whole chain, end to end on an idle account:**

    CLAUDE_CONFIG_DIR=<account>/.claude claude auth status --json   # refreshes
    GET https://api.anthropic.com/api/oauth/usage                   # 200, 467 ms

`gamma` returned `five_hour.utilization` **0.0** (`resets_at: null` - nothing
spent in the window) and `seven_day` **100.0**, resetting
`2026-09-22T22:00Z`.

**3. Validated against `probe.py`, by accident and perfectly.** Six minutes
before this call, `probe.py` had run on `gamma` on its own - its snapshot is
unmistakable (`model_id: claude-haiku-4-5-20251001`, `agent: None`). The two
mechanisms agree exactly:

| | `probe.py` | the usage endpoint |
|---|---|---|
| how | pty, session boot, one Haiku turn | one HTTPS GET |
| cost | a session and its quota, up to 90 s, 30-min cooldown | **467 ms, no quota** |
| 5h | 0% | 0.0% |
| 7d | 100% | 100.0% |
| 7d reset | 09-23 07:00 local | `2026-09-22T22:00Z` - the same instant |

Same answers. One spends quota to get them.

**4. Nothing was harmed.** `claude auth status` on `gamma` reports
`loggedIn: true`; the 403 and 429 attempts wrote nothing, because the script
persists only on a 200.

**So Q4 is viable, and simpler than drafted.** The design is: for an account
whose token is near or past expiry, run `claude auth status --json` against its
config dir, then one GET - scheduled off `resets_at`, per the founder's point
in #1360. **This retires the argv-leak problem and the Security-framework
requirement entirely**, since ocp never writes a credential. What it puts in
reach is `probe.py` (172 lines and a session per quiet account) and most of
`status.py`'s carried/quiet/unknown model.

#### Both checks answered, and the read path is built (#1369)

**Does `auth status` refresh only near expiry?** Yes. On `gamma`'s token, then
valid another 7.9h, it changed `expiresAt` by nothing. So calling it is
idempotent and free; ocp never has to judge expiry itself.

**What does it do while a session is live?** With the token not near expiry,
nothing - run against `omega` while this very session was on it, `expiresAt`
was unchanged and the session was unaffected. The risky pair (live session
*and* near expiry) is avoided by design rather than tested: an account with a
session running is asked with `refresh=False`, because Claude Code is renewing
its token anyway and two writers would race. That is Orca's own
`deferredByLiveClaudeSession`, for the same reason.

**Built:** `ocp/usage_api.py` (the read path) and `ocp usage [account]`, which
prints the endpoint beside the snapshot and decides nothing. **Not wired into
the daemon** - no placement decision reads it yet.

**What it looks like on the live fleet.** Run across all five accounts, the two
sources agree wherever ocp has a current reading, and the API fills in where it
does not:

| account | ocp | endpoint | |
|---|---|---|---|
| `alpha`, `gamma`, `omega` | current | identical | the figures and the reset instant both |
| `delta` | 7d 93%, snapshot 2h11m old | 7d **94%** | ours had drifted |
| `beta` | **no 5h reading at all**, 4h37m old | 5h **0%** | the gap `probe.py` exists for |

**And one moment worth keeping**, caught by accident mid-measurement: at
21:00:18, eighteen seconds after `alpha`'s 5h window reset, `ocp status` read
`picked: none - no account may take work (fail-closed)` while `alpha` carried
`100~` from the window that had just ended. The endpoint said `utilization:
0.0, resets_at: null, is_active: false`. `alpha`'s 7d was 99%, below the wall,
so it was assignable and ocp was refusing it. The snapshot caught up only once
a session on it made its next call. **That lag is the cost this closes**, and
it recurs at every window rollover.

#### Wired in (#1381)

`probe.run` now tries the endpoint first and spends the account only when it
cannot answer. `usage_api.record` leaves the snapshot the next tick reads, so
nothing downstream learns about any of this.

**The seam moved during the work, and the first placement was wrong.** I put
the preference at `daemon._start_probe` - the daemon's call site - where it
would have blocked a tick for up to 55s on a hung auth check. The existing
comment there says why that is wrong: the probe is started **detached** because
a tick may not stop for a session's startup, and a network call is no
different. It belongs in `probe.run`, where the cost is. A test now pins that
the daemon still spawns and returns.

**What `record` must not do**, each learned from the model it writes into:

| rule | why |
|---|---|
| merge, never overwrite | `session_id`, `transcript_path` and the context figures are the collector's; a wholesale write takes the handoff's transcript pointer with it |
| never lower a figure inside its own window | the collector's rule - usage only rises while a window is open |
| **drop the high-water across a rollover** | the case that matters: that stale high-water is what made `alpha` unassignable eighteen seconds after its window reset |
| move `collected_at` | this replaces a probe, and a probe moves it by running a real session. Without it `AccountStatus.stale` fails the account closed before it reads any figure |

**Measured end to end on `gamma`, on the real path:** **1.07s**, against a
session boot and a Haiku turn. `rl_source: api`, the earlier probe's
`session_id` and `model_id` still in the file, verdict unchanged and correct.

**Applied to copies of all five live snapshots, `takes_work` changes on none of
them today.** What changes is the grounds - `beta`'s "nothing has run on it for
4h (fail-closed)" and `delta`'s "too close to 100% on a stale reading" both
become measured figures. (An earlier version of this check compared
`assignable()` alone, which omits the weekly gate, and appeared to flip
`gamma`. Against `takes_work()` it does not.)

**Still open, and deliberately not taken here:** retiring the carry / quiet /
unknown rules. They are what a *failed* reading falls back on, and the
endpoint can still fail - the fallback has to stay until there is evidence
about how often it is reached. The reduction `probe.py` represents is real but
it is not collected yet: the probe remains, as the fallback.

**Standing risk:** it is an undocumented endpoint. `claude auth status --json`
returns identity only (`loggedIn`, `email`, `orgId`, `subscriptionType` - no
usage), and the CLI has no usage verb, so there is no supported alternative to
fall back on. Whatever is built must fail closed to today's snapshot rather
than block a launch.

### Candidates, not queued

Identified and priced, but **not approved and not to be started**:

- the idle/busy probe → `stage.activity` + `liveness` (board §3, marked ORCA;
  replaces a probe that pays its full timeout to say "no")
- `hooks.py`'s hand-rolled mirror → `orca agent hooks on|off|status`
- `screen_blocker`'s decision → `terminal show`'s `agentWait` (D9)
- the window-model reshape (D2) - the big one

---

## Method notes worth keeping

- Orca's own specification is on the machine: `orca skills get orchestration`
  and `orca-cli`, seven references behind `--reference <name>`. Read it before
  inferring anything from `--help`.
- `orca agent-context --json` is the whole CLI as data (schema v1, 234
  commands) - better than paging `--help`.
- Checking whether an account is logged in **never** needs the secret.
  `accounts.logged_in` calls `security find-generic-password` without `-w` for
  that reason. Do not run it with `-g` while surveying; it prints the token.
