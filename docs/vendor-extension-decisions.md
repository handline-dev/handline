# Decisions: extending ocp past Claude

What is **settled**, so it is not re-argued or forgotten. Small on purpose.

The two working documents move as the survey moves: `orca-cli-survey.md`
(what Orca offers) and `vendor-extension-board.md` (seam-by-seam verdicts).
This file does not move. A line leaves the OPEN list only when a measurement
settles it, and then it moves up with its evidence.

Asked by the founder in #1255 (withdrawn, superseded live) and #1277.
Decisions D1-D10 taken 2026-09-21.

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

Orca does report usage (eight providers, `inactiveClaudeAccounts[]` included,
fetched over OAuth so no session is needed) - but measured on 2026-09-21 the
figures for the accounts we run on were **40.3h, 42.9h and 40.3h old**,
`isFetching: false`, with **no CLI verb to force a refresh**. Ours are seconds
old and disagree sharply.

So: **adopt Orca's data shape, not Orca's data.** The window model becomes a
list keyed by duration - Orca's `windowMinutes`, codex's `window_minutes` -
plus per-provider `status` / `error`, replacing the hard-coded `pct_5h` /
`pct_7d` / `pct_opus_7d` triple (208 lines across 16 files).

**Why it matters most:** this is the deepest coupling in ocp and the one that
does not look like coupling - five of those sixteen files never mention
`claude`. **Forecloses:** a runtime dependency on Orca for any number that
gates a decision. **Revisit if:** Orca ships a usage-refresh verb.

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

### D9. Detection moves to Orca; reading the panel stays ours. And the
statusLine stays, permanently.

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

**The statusLine injection is permanent.** It carries three payloads, not one:
rate limits (Orca has them, 40h stale), session identity (ours), and the
**per-session context window** - which **nothing else on this machine has**.
Not one of Orca's 234 commands mentions a context window or a token count.
Context drives the handoff-and-park trigger, the feature that keeps the fleet
running unattended, so this is not a convenience Orca might absorb later.

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

### Q2. Build the vendor driver, steps 1-4

Drafted in `design/03-vendor-driver.md` §7. Steps 1-4 are **vendor-neutral
refactors that need codex to exist for nothing**: move today's behaviour into
`ocp/vendors/claude.py` unchanged; make `observe.account_of_process` read the
config-dir variable from the driver; reshape the window model (D2 - the 208
lines across 16 files, writing both old and new keys for one release); and
move the preamble to a file (D10).

Not started. Step 3 is the one with real risk and should be its own branch.

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
