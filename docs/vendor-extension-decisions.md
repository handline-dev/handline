# Decisions: extending ocp past Claude

What is **settled**, so it is not re-argued or forgotten. Small on purpose.

The two working documents move as the survey moves: `orca-cli-survey.md`
(what Orca offers) and `vendor-extension-board.md` (seam-by-seam verdicts).
This file does not move. A line leaves the OPEN list only when a measurement
settles it, and then it moves up with its evidence.

Asked by the founder in #1255 (withdrawn, superseded live) and #1277.
Decisions D1-D8 taken 2026-09-21.

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

### D8. `spawn.ensure_run` must own the Run; agents' terminals must not.

Measured, read-only: **25 Runs named "orcapipe team"**, ~9,000 tasks all
`ready`, the team's 18 current tasks spread over **8** of them, and **9 of 15
agents with a supervisor have no parent link** - harbor's six reports draw
entirely flat. Nine Runs were created inside 33 seconds during one cold start.

Cause is ours, not Orca's: `run-create` binds the calling terminal, and
`ensure_run` runs inside each agent's own terminal, taking whatever that
terminal is bound to and writing it back as the team's Run. The key follows the
most recent launch.

**Decided:** one owner binds the Run once; agents' own terminals never call
`run-create`. **And this is a prerequisite**, not a cleanup - adopting
terminals into a Run whose identity changes daily (O1) would inherit the mess.

**Note for whoever fixes it:** the flat-task fallback in
`spawn.register_worker` is not a bug and should stay. It is firing 60% of the
time because of the above, and it is the only reason the team is drawn at all.

---

## Open, with what would settle it

| id | question | settled by |
|---|---|---|
| **O1** | Supervised lane or handoff lane? We create Run + Task + Dispatch and settle none (D8). Either adopt terminals with `worker-start --terminal`, or stop creating Tasks and keep only what draws the team. | measuring what adoption grants - **needs the founder's go-ahead: it mutates a live fleet** |
| **O2** | Do ~150 lines of screen regex in `orca.py` go away? **Narrowed 2026-09-21:** measured all 24 dispatches - `attention.categories` carries only lifecycle states (`unverifiable`, `failure`, `interrupted`, `stale`), never "a human is needed", and `observation` is **absent on 24 of 24**, which Orca's guide defines as "it never looked". So today the answer is no. The remaining question is only whether **adoption** turns `observation` on. | the O1 mutation, and nothing else - no supervised worker exists here to observe |
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
