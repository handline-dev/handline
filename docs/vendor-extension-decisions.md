# Decisions: extending ocp past Claude

What is **settled**, so it is not re-argued or forgotten. Small on purpose.

The two working documents move as the survey moves: `orca-cli-survey.md`
(what Orca offers) and `vendor-extension-board.md` (seam-by-seam verdicts).
This file does not move. A line leaves the OPEN list only when a measurement
settles it, and then it moves up with its evidence.

Asked by the founder in #1255 (withdrawn, superseded live) and #1277.
Decisions D1-D9 taken 2026-09-21.

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
~9,000 tasks, effectively all `ready`; the 18 agents' current tasks across 8
Runs; **12 of the 25 coordinated by 12 different agent terminals**; the current
Run at `consumer_generation` 21, i.e. 21 takeovers. And live during this
session, with no human action, `RUN_KEY` moved `run_5f5c6f452853` →
`run_1dd4c9a72be3`.

**Cost.** `task-create --parent` refuses across Runs, so **9 of the 15 agents
with a supervisor have no parent link** and all six of harbor's reports draw
flat. The org chart `register_worker` exists to draw fails 60% of the time,
silently. Work delivery is *not* affected - 16 of 18 agents still have a
dispatch on their current terminal - so this is a drawing and accumulation
bug, not an outage.

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

**Still a prerequisite** for O1: adopting terminals into a Run whose identity
changes on every Orca restart would inherit all of this.

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

---

## Open, with what would settle it

| id | question | settled by |
|---|---|---|
| **O1** | Supervised lane or handoff lane? **Narrowed by D9:** adoption is no longer needed for `agentWait`, so all it would still buy is the lifecycle - a `resource` row and `worker-stop`/`worker-release` acting on the process. We create Run + Task + Dispatch and settle none (D8). Either adopt terminals with `worker-start --terminal`, or stop creating Tasks and keep only what draws the team. | measuring what adoption grants - **needs the founder's go-ahead: it mutates a live fleet** |
| ~~O2~~ | *settled 2026-09-21 - see D9.* | |
| **O3** | How does a codex agent get ROLE.md + base.md + handoff? Claude uses `--append-system-prompt`; codex has no argv equivalent. Candidates: `AGENTS.md` under a per-account `CODEX_HOME`, or the first prompt. | a live codex launch, not reading |
| ~~O4~~ | *settled 2026-09-21 - see D8.* | |

**Rule for this list:** nothing below the line is designed on. O3 in particular
is load-bearing for the whole team model and must not be guessed at.

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
