# Board: what a second agent vendor costs ocp

Part 2 of the #1255 study, and a **living document** - it is updated as each
area is surveyed, not written once. Part 1 is `orca-cli-survey.md`; this board
assumes its findings, especially the three support tiers (§3.1 there).

**What is already settled is in `vendor-extension-decisions.md` (D1-D7), and
this board does not re-argue it.** Verdicts here that rest on a decision say
so.

The question it answers, seam by seam: *to run codex or opencode agents beside
claude ones, what has to change - and of that, what should become an Orca CLI
call rather than code of ours?*

## How to read a verdict

| code | meaning |
|---|---|
| **KEEP** | 변경 불필요. Already vendor-neutral; a second vendor does not touch it. |
| **RESHAPE** | 변경필요 · 직접. Ours to keep, but the *shape* is Claude's and must be generalized. No new per-vendor code, no Orca call. |
| **DRIVER** | 변경필요 · 직접구현. Genuinely **per-vendor** behaviour: one implementation per vendor. Orca cannot do it for us. This column is the bill for vendor #2. |
| **FIX** | 변경필요 · 우리 버그, 벤더 무관. Ours to repair, and a second vendor neither helps nor hurts. |
| **ORCA** | 변경필요 · Orca CLI 로 대체. Orca already does this, better and vendor-neutrally. We should delete ours and call it. |
| **HYBRID** | Orca covers part of it; the rest is a driver. |
| **DECIDE** | Not a code question yet - a choice has to be made first. |
| **TBD** | 미조사. Not yet read closely enough to judge. |

Rule used for **ORCA**: Orca must already answer the exact question, for every
vendor, without us parsing a vendor's rendering. "Orca has a related verb" is
not enough - that is **HYBRID** at best.

---

## 0. The premise check: multi-account

ocp exists to run a fleet across **several accounts at once**. Before any seam
is classified, that premise has to be tested against Orca, because if Orca
covered it the rest of this board would be much shorter. Measured on this
machine:

**Orca's execution model is one active account.** `orca account list --json`
returns `claude.activeAccountId` - singular - plus
`activeAccountIdsByRuntime: {"host": "<one id>", "wsl": {}}`, and files the
rest under `inactiveClaudeAccounts`. It is an account **switcher**, not
concurrency. Nothing in `worker-start`, `worktree create --agent` or
`terminal create` selects an account.

**So the account concern splits in two, and the halves have opposite answers:**

| half | question | Orca | verdict |
|---|---|---|---|
| **placement / execution** | which account does *this session* spend? | 1 active, switched | **ours forever, every vendor** |
| **accounting** | what has each account spent? | all registered accounts, 8 providers, OAuth-sourced | **shape yes, freshness no** (below) |

This is the single most important structural fact in the study. It means layer
3 (`worker-start --agent`) can never replace `ocp launch`, and the only way to
combine ours with Orca's supervision is adoption -
`worker-start --terminal <handle>` (part 1 §5.5).

### 0.1 What Orca's accounting does and does not give us

Part 1's correction section has the payload. The part that decides this board:
Orca's per-account figures are **real, multi-provider and correctly shaped**
(`windowMinutes`-keyed, `status`/`error` per provider, `inactiveClaudeAccounts[]`
covering accounts with no session running, fetched over OAuth so no `probe.py`
pty trick is needed) - and **too stale to act on**. Measured now:

    active   hellopiotee@gmail.com    session=100%  weekly=74%   age= 3.4h
    inactive funccoder@gmail.com      session=100%  weekly=70%   age=40.3h  isFetching=false
    inactive ehalim111@gmail.com      session=100%  weekly=31%   age=42.9h  isFetching=false
    inactive ronpark.dev@gmail.com    session=  0%  weekly=55%   age=40.3h  isFetching=false

ocp's own readings for the same logins are seconds old, and they disagree
sharply with Orca's (ocp has `ehalim111` at 5h 15% / 7d 87% against Orca's
100% / 31%) - which is what a 43-hour-old number looks like. There is no CLI
verb to force a refresh.

**Verdict: adopt Orca's data *shape*, not Orca's data.** The window model
reshape in §5 should copy `windowMinutes` + `status`/`error` exactly. That
future-proofs multi-vendor quota without taking a runtime dependency on
figures we cannot refresh.

### 0.2 Credentials: disjoint, and safely so

ocp keys credentials per config dir through Claude Code's scoped Keychain
entries (`accounts.keychain_service`): `alpha → …-c4b0821c`, `beta → …-30018abd`,
`delta → …-8c9240cb`, `gamma → …-deb6f5e4`, `omega → …-23408a86`. Orca's
managed accounts use the same mechanism (`credentialSource: "scoped-keychain"`)
under **different** entries.

Measured: **four of ocp's five accounts are the same Anthropic logins Orca has
registered** - `ehalim111`, `funccoder`, `hellopiotee`, `ronpark.dev` - logged
in twice, in two scopes, coexisting right now. So two independent logins of one
Anthropic account do **not** invalidate each other; the KICKOFF warning is
about *copying* a credential between two places, which is a different thing.
Useful to know, and it means registering ocp's accounts with Orca is possible -
but §0.1 says there would be little point until a refresh verb exists.

⚠ Do not re-run `security find-generic-password -g` while surveying. It prints
the secret. Presence is what `accounts.logged_in` checks, and it is checked
without `-w` for exactly this reason.

---

## Scoreboard

Seams classified so far: **38**.

| verdict | first pass | now | |
|---|---|---|---|
| **ORCA** | 6 | **11** | grew. Observation keeps confirming. |
| HYBRID | 1 | 2 | |
| **DRIVER** | 12 | **16** | per-vendor only - **this column is the bill for vendor #2** |
| RESHAPE | 3 | 5 | |
| KEEP | 15 | 7 | shrank because seams were split finer, not re-judged |
| FIX / DECIDE | 0 / 1 | 1 / 1 | |

**The trend, since it is easy to misread.** One verdict was demoted away from
Orca (Run binding, once a speculative ORCA, turned out to be our own bug - D8).
One was split rather than moved (dialog handling: detection to Orca, describing
the panel stays ours - D9). Two were *gained* (`stage.activity` + `liveness`
replacing an idle probe that costs its full timeout to say "no"; and `agentWait`
turning out to need no adoption at all).

So the Orca column is not closing. What keeps shrinking is the **scope of each
win** and the **preconditions** attached to it - which is what a boundary looks
like while it is being found.

**The headline.** ocp is ~17.5k lines. The vendor-specific part is small and
concentrated - but it is not where the `claude` string is. Counting mentions of
`claude` finds 189 lines in 22 files, mostly comments. Counting the **rate-limit
window model** (`pct_5h`, `pct_7d`, `opus_7d`, `reset_*`, `rl_at`, `rl7_*`)
finds **208 lines across 16 files**, including five with no mention of `claude`
at all (`db.py`, `triggers.py`, `burnrate.py`, `measure.py`, `replay.py`).
That is the real coupling and it is schema-deep. Orca has the right shape for
it and cannot supply the values fresh enough to use (§0.1), so this is ours to
rebuild - in Orca's shape.

---

## 1. Launch and process

| seam | ours | verdict | note |
|---|---|---|---|
| build the argv (model, effort, tool denial, mcp, system prompt) | `launch.build` | **DRIVER** | every flag is Claude's spelling. Orca layer 3 cannot take this over: `worker-start` has no env/config-dir flag, so it cannot place a session on a chosen account. |
| find the binary | `shutil.which("claude")` ×7 (`launch`, `probe`, `fold`, `accounts`, `doctor`, `cli` ×2) | **DRIVER** | one lookup per vendor, in one place instead of seven. |
| account env | `accounts.env_for` → `CLAUDE_CONFIG_DIR` | **DRIVER** | codex is `CODEX_HOME`; opencode TBD. |
| is this launch a resume | `launch._is_resume` (`--resume`/`-r`/`--resume=`) | **DRIVER** | codex spells it differently. |
| system-prompt injection | `--append-system-prompt` in `launch.build` | **RESHAPE** ✅ | **No longer the hard one - the founder's #1305 challenge was right and it collapses the seam.** Not a flag per vendor: one per-agent file written into the agent's own worktree, which both vendors already read. See §1.1. |
| launch fingerprint / drift | `launch.fingerprint`, `drifted` | **RESHAPE** | the mechanism (hash what a running session cannot pick up) is neutral; the *material list* is Claude's flags. Driver supplies the material, this keeps the logic. |
| make the terminal | `spawn.py` → `orca terminal create --command` | **KEEP** | already Orca, already vendor-blind (part 1 layer 1). |
| record the terminal handle | `launch._record_terminal` | **KEEP** | |

### 1.1 The preamble does not need a flag (settles the ⚠ on the row above)

I had framed `--append-system-prompt` as a constraint, and codex's lack of an
equivalent as the hardest item on the bill. Challenged on it (#1305), the
premise does not hold: **both vendors already read a per-agent file from the
worktree**, and ocp creates that worktree itself.

Codex's own prompt template, read from the binary, spells out the contract:

> The scope of an `AGENTS.md` file is the entire directory tree rooted at the
> folder that contains it. […] The contents of the AGENTS.md file at the root
> of the repo and any directories from the CWD up to the root **are included
> with the developer message** and don't need to be re-read.

So a file at the worktree root is loaded at session start, without a flag.
Claude reads `CLAUDE.md` the same way. **The seam stops being per-vendor and
becomes one file writer with a different filename per vendor** - which is
RESHAPE, not DRIVER, and removes one of the two ⚠ risks from the 16-item bill.

**Measured against a live codex, 2026-09-21, at zero token cost** (#1306). A
scratch dir held `AGENTS.md` (marker `MARKER-PLAIN-7Q`) and
`AGENTS.override.md` (marker `MARKER-OVERRIDE-3Z`); `codex exec` was run there
and rejected at the API with a 400 (the configured `gpt-5.6-sol` is not allowed
on a ChatGPT account) - **but codex had already assembled and written the
rollout**, so the prompt it built is on disk for free.

What it contains:

```json
{"type":"response_item","payload":{"type":"message","role":"user","content":[
  … , {"text": "# AGENTS.md instructions for <cwd>\n\n<INSTRUCTIONS>\n# Override guide\nMarker token for this file: MARKER-OVERRIDE-3Z\n</INSTRUCTIONS>"}, … ]}}
```

- The file **is** loaded, with no flag, as its own content block.
- `MARKER-OVERRIDE-3Z` appears twice; **`MARKER-PLAIN-7Q` appears zero times.**
  `AGENTS.override.md` does not sit beside `AGENTS.md` - it **replaces it
  entirely**.
- It arrives as `role: user`, explicitly labelled *"AGENTS.md instructions"* -
  the category codex's own spec ranks **below** direct system/developer/user
  instructions.

**So the adjustment I proposed was wrong in an important way.** I suggested
`AGENTS.override.md` so ocp would not collide with a repo's own `AGENTS.md`.
It does not avoid the collision, it **wins** it: the repo's conventions, test
commands and boundaries would be silently switched off for that agent.

**Three options, and none is free:**

| | keeps repo's AGENTS.md | untracked by git | survives a new thread | precedence |
|---|---|---|---|---|
| **A** write `AGENTS.md` | no - must merge into a **tracked** file, polluting every diff | no | yes | AGENTS-level |
| **B** write `AGENTS.override.md` | **no - suppresses it** | yes (`.git/info/exclude`) | yes | AGENTS-level |
| **C** first prompt (already done by `exec_agent`) | yes | n/a | **no** | highest |

**Recommended: B plus a merge.** ocp rewrites the file every launch anyway
(adjustment 2 below), so it can concatenate the repo's own `AGENTS.md` after
its preamble and keep both. That is the only option that loses nothing,
and it costs one file read per launch.
1. **Write it at launch, not at worktree creation.** Three payloads change
   between launches: the team line is *generated* from `team.yaml` precisely
   because hand-written colleague lists went stale; `hr` can edit ROLE.md and
   base.md; and the handoff is per-launch and must be **consumed**, since a
   stale "where you left off" is worse than none. `launch.fingerprint` exists
   to catch exactly this drift. Same moment `build()` already runs.
2. **Keep it out of git** via `.git/info/exclude` - per-worktree, local, never
   committed - rather than `.gitignore`, which is tracked.

**Two costs, named rather than waved past:**

- **A file is agent-visible and agent-writable; a system prompt is neither.**
  ROLE.md and base.md are already on disk and the agent is told its role, so
  visibility is not new - but an agent editing its own preamble is. Rewriting
  per launch (adjustment 2) means any edit is reverted at the next launch,
  and `fingerprint` already notices.
- **Lower precedence.** Codex states that "direct system/developer/user
  instructions take precedence over AGENTS.md instructions". For *who you are,
  who you report to, stay in your worktree* that is likely fine; for anything
  meant as a hard rule it is a demotion, and should be checked.
- **Precedence is now measured, not guessed, and it is genuinely lower.** The
  block arrives as `role: user` labelled "AGENTS.md instructions", which
  codex's spec ranks below direct instructions. Anything ocp means as a hard
  rule - do not schedule your own wakeup, stay in your worktree - is advisory
  at this level in a way it was not under `--append-system-prompt`.
- Still unverified: whether a codex `/new` re-reads the file. It is assembled
  at session start into the first user message, so a fresh thread almost
  certainly re-assembles it - but that is an inference.

**Bonus confirmation from the same run:** codex printed `hook: SessionStart` and
`hook: UserPromptSubmit`, so Orca's codex hook integration is live and firing.
And the models this account may use are `gpt-5.6-terra`, `gpt-5.6-luna`,
`gpt-5.5`; the configured `gpt-5.6-sol` is not one of them.

---

## 2. Accounts and credentials

| seam | ours | verdict | note |
|---|---|---|---|
| login / logout | `claude /login`, `claude auth logout` | **HYBRID** | `orca account add --agent claude\|codex` exists and registers with the runtime - but only for two vendors, and part 1 open question 2 (can Orca *place* a session on a listed account?) is unanswered. Until it is, ours stays and gains a per-vendor spelling. |
| who is this account | `claude auth status --json` | **DRIVER** | |
| credential location | `accounts.keychain_service` - macOS Keychain, per-config-dir SHA digest | **DRIVER** | this is Claude-only machinery. Codex is simpler: `auth.json` inside `CODEX_HOME`, a plain file. Good news, but a different implementation. |
| shared config items | `accounts.LINKED` = settings.json, CLAUDE.md, skills, agents, commands, plugins | **DRIVER** | per-vendor layout. One exception below. |
| shared skills | part of `LINKED` | **ORCA** | `orca skills install --skill <n> --agent claude-code,codex` installs into each vendor's own config, and `--agent universal` targets the shared `.agents/skills`. One call keeps two vendors in step; our symlink farm cannot. |
| carry memory across an account move | `accounts.migrate_memory`, `memory_project_dir` | **DRIVER** | the `projects/<encoded>/memory/` layout is Claude's. |

## 3. Observing a running agent ← biggest Orca win

| seam | ours | verdict | note |
|---|---|---|---|
| does the terminal exist | `orca.live_terminals` → `terminal list --json` | **ORCA** | we already make this call and **throw away all but `handle`**. The same 31 rows carry `agentIdentity` (the vendor - `"claude"` on 9 rows here, `null` on 22), `title` with a live activity glyph, `lastOutputAt`, `preview`, `branch`. |
| is a session running, and is it ours | `observe.is_session` (`"claude" in command`), `live_sessions` (ps + `OCP_AGENT`) | **ORCA** | a ps-table scan that pattern-matches the binary name is exactly what `agentIdentity` and `projection.liveness` answer, for 18 vendor families, with provenance and a three-valued verdict (`live`/`unverifiable`/`exited`). Ours cannot tell "I could not look" from "it is gone" as cleanly. |
| which account is this process spending | `observe.account_of_process` (reads `CLAUDE_CONFIG_DIR` off `ps eww`) | **DRIVER** | Orca has no idea which of *our* accounts a process is on. This one stays ours whatever else moves. |
| is it idle | `orca.wait_idle` → `terminal wait --for tui-idle` | **KEEP** | already Orca, and explicitly vendor-generic - the guide names Claude Code, Gemini, Codex, OMP, Pi and Grok. Idleness is the one thing a second vendor need not re-teach. |
| **detect** that an agent needs a human | `orca.screen_blocker` | **ORCA** ✅ | `terminal show --json` → `terminal.agentWait`, present on **32/32** terminals, non-null exactly when one is waiting, carrying the evidence that proved it. No adoption needed. §3.1. |
| **name** the dialog, and decide Esc is safe | `parse_dialog`, `declining_key`, `describe_modal`, `_describe_backgrounded` | **DRIVER** | `agentWait` said `{"source": "title"}` and nothing more. We need the title and the numbered options - to report *which* dialog, and because `decline_dialog` must never press a key on a panel it could not parse. §3.1. |
| is it working, idle or gone | `observe.live_sessions` + `orca.idle_now` (a `tui-idle` probe that costs its whole timeout to say "no") | **ORCA** ✅ | `projection.stage.activity` is a live three-state signal - `working` / `done` / `unknown` - already present on the dispatches we create, alongside `liveness.verdict` (`live` / `exited` / `unverifiable`) and its `source` (`agent_status`, `execution_host`). Measured across all 24: see §3.1. |
| press Esc to decline a dialog | `orca.decline_dialog`, `declining_key` | **DRIVER** | Orca *reports* a wait; it has no verb that answers one. Whoever presses a key must read the screen it is pressing on. This stays ours, per vendor - and it is the residue that keeps some screen parsing alive even if the line above moves. |

### 3.1 What Orca already reports, unsupervised (measured, read-only)

**`agentWait` lives on the terminal, not on the dispatch.** An earlier pass
looked only at `worker-show`/`worker-list`, found no `observation` key on any
of 24 dispatches, and concluded Orca never looks. Wrong place. `orca terminal
show --terminal <h> --json` carries `terminal.agentWait` on **32 of 32**
terminals, and it was **non-null on exactly the one that was waiting**:

```json
{ "title": "1 awaiting input · claude agents", "agentIdentity": "claude",
  "worktreePath": "…/harbor-app/manager", "agentWait": { "source": "title" } }
```

That is our own `harbor` agent, and its preview shows
`❯ describe a task for a new session` - the case `orca.py` calls BACKGROUNDED.
So Orca catches it, unsupervised, vendor-neutrally, in one field, **and
adoption is not required for any of it.**

What it does **not** give is the content: `{"source": "title"}` and nothing
else. ocp needs the panel's title and its numbered options, both to name the
block ("ocp names the dialog and never answers it") and because
`declining_key` must refuse to press Esc on a panel it could not parse. Only
one sample was available, all three documented sources being `hook`,
`prompt-text` or `title`; whether a `hook`-sourced wait carries more is
unmeasured.

So the seam splits: **detection moves to Orca, the reading of the panel stays
ours** - and what stays is much smaller than 150 lines, because `screen_blocker`
no longer has to be the thing that decides, only the thing that describes.

And on the dispatch side, all 24, every Run, no mutation:

| field | populated? | values seen |
|---|---|---|
| `stage.activity` | **yes** | `working`, `done`, `unknown` |
| `liveness.verdict` / `.source` | **yes** | `live`/`agent_status`, `exited`/`execution_host`, `unverifiable`/`None` |
| `attention.categories` | **yes, but** | `[]`, `unverifiable`, `failure`, `interrupted`, `stale` |
| `observation` (and so `observation.agentWait`) | **no - absent on 24 of 24** | — |

Two conclusions, and they point opposite ways.

**The idle/busy probe can go (ORCA).** `activity` plus `liveness` answers "is
this agent working, finished, or gone" for every dispatch at once, from hook
evidence, vendor-neutrally. Ours asks per agent with `terminal wait --for
tui-idle`, which by `orca.py`'s own measured comment **costs the full timeout
every time the answer is no**. This is a straight win and needs no adoption.

**The screen parsing cannot go (KEEP, for now).** `attention.categories` only
ever carries *lifecycle* states - nothing that means "a dialog is open and a
typed line would answer it". And `observation` is absent on every single
unsupervised dispatch, which by Orca's own guide means **"it never looked"** -
not that there is nothing to find. So `agentWait` is not something an
unsupervised caller gets, anywhere on this machine.

**So what is adoption still for?** Not `agentWait` - that arrives without it.
Only for the lifecycle: a `resource` row, and `worker-stop` / `worker-release`
acting on the process. That is O1, and it is now a much smaller question.

---

## 4. Delivering work

| seam | ours | verdict | note |
|---|---|---|---|
| type text at an agent | `orca.send` → `terminal send --text --enter` | **ORCA** | we use the bare form. Unused: `--wait-submit` (Orca separates *accepted* from *observed submission* - the "typed is not landed" problem `daemon.py` handles by hand), `--retry-request` (idempotent resend after an ambiguous failure), `--interrupt` as a flag. |
| reclaim context | `/clear` in `orca.clear_context` | **DRIVER** | codex's is `/new`. |
| end a session | `/exit` in `orca.close_session` | **DRIVER** | codex's is `/quit`. |
| relaunch in the surviving shell | `orca.relaunch` | **KEEP** | the command is ours, so the account comes back with it. |
| slash commands, all sites | 16 lines in `daemon.py`, 9 in `orca.py`, 3 in `blocked.py` | **RESHAPE** | they belong on the driver, not inline at 28 call sites. |

## 5. Quota and telemetry ← ours, but its shape should be Orca's

Orca *does* report usage (part 1, correction section) - for eight providers,
keyed by `windowMinutes`, covering inactive accounts, over OAuth. It is simply
40+ hours stale for the accounts ocp runs on, with no verb to refresh it
(§0.1). So the subsystem stays ours for every vendor, and the reshape below
should copy Orca's shape rather than invent one.

**The statusLine carries three payloads, not one**, and they have different
answers. `libexec/statusline-collect.sh` takes from Claude Code's statusLine
JSON: (a) account-wide **rate limits**, (b) per-session **context window**, and
(c) session identity (`session_id`, `transcript_path`, `model_id`, `cwd`).

| payload | who else has it | verdict |
|---|---|---|
| rate limits | Orca, per account, 8 providers - **40h stale, no refresh verb** | ours, fresh |
| **context window** | **nobody** | **ours, and there is no alternative** |
| session id / transcript | ours only | ours |

**(b) is the finding.** Not one of Orca's 234 commands mentions a context
window or a token count; `terminal show` has no such field; the only trace
anywhere is the `preview` string rendering Claude Code's own status line
(`· Ebbing… (20s · ↓ 1.3k tokens)`), which is a picture, not data. Context
drives ocp's handoff-and-park trigger, which is the feature that keeps a fleet
running unattended. **So the statusLine injection is not a convenience that
Orca might absorb one day - it is the only source of a number ocp cannot
operate without.**

For a second vendor that matters twice over, because codex has no statusLine
command hook at all. Its equivalent is in the rollout JSONL, verified on this
machine - `token_count.info.model_context_window` with `total_token_usage`,
beside the same `rate_limits` block - so a codex collector **pulls** where ours
is **pushed**. Same output file, different mechanism, and the context number is
there.

| seam | ours | verdict | note |
|---|---|---|---|
| collect a usage reading | `libexec/statusline-collect.sh` (statusLine JSON on stdin) | **DRIVER** ⚠ | Claude pushes this to a command we install. **Codex has no equivalent**: its `/statusline` configures which built-in items show, not a command hook. Codex's numbers are in its rollout JSONL instead - `token_count.rate_limits = {primary, secondary, credits}`, each window `{used_percent, window_minutes, resets_at}` - so a codex collector *pulls* (tails the rollout file, or runs off a `stop` hook) where ours is *pushed*. Different collector, same output file. |
| the snapshot schema | `pct_5h` / `pct_7d` / `pct_opus_7d` + resets, in `status.py` (96 lines), the collector (16), `db.py` (15), and 12 more files - **208 lines, 16 files** | **RESHAPE** ⚠⚠ | **the deepest coupling in ocp.** Claude's window *names* are hard-coded as fields. Codex reports `primary`/`secondary` keyed by `window_minutes`; Orca reports every provider that way too (`windowMinutes: 300 / 10080`). Three fixed names cannot hold any of it. Target shape is settled by §0.1: **copy Orca's** - a list of windows keyed by minutes, plus per-provider `status`/`error`. Five of the sixteen files never mention `claude`, so this will not be found by looking for the vendor. |
| carry / floor / quiet / unknown rules | `status.py` five-case model, `idle_floor`, high-water mark | **KEEP** | the arithmetic is about *trusting a reading*, not about whose reading it is. It survives the reshape above untouched, which is why the reshape is worth doing properly. |
| force a reading out of a quiet account | `probe.py` (pty, type a prompt, wait for the status line to write) | **DRIVER** | the whole technique exists because the status line is a TUI feature, and it spends quota to learn a number. Orca gets the same number over OAuth with no session at all - so this is the one seam that would simply *vanish* if Orca gained a refresh verb. Until then it stays, and a vendor that writes usage to a file (codex's rollout JSONL) needs no pty for it either. |
| price an agent, measure burn | `burnrate.py`, `measure.py`, `load.py` | **KEEP** | already vendor-aware: `burnrate` keys cost by `vendor/model` when vendor ≠ default. Written for this. |
| account choice, wall, unfit, pick | `status.pick_account`, `triggers.py` | **KEEP** | pure arithmetic over the snapshot. |

## 6. Events and hooks

| seam | ours | verdict | note |
|---|---|---|---|
| mirror Orca's own hooks into our config dirs | `hooks.py` (169 lines: read `~/.claude/settings.json`, match `ORCA_MARKERS`, splice into shared settings) | **ORCA** | `orca agent hooks on \| off \| status` exists. We are hand-mirroring a file Orca owns, and matching its hook commands by substring to do it. |
| install our own event hooks | `cli.cmd_hook_install` → `settings.json` `hooks` block | **HYBRID** | codex takes the **same JSON shape** (`{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":…,"timeout":10}]}]}}`) in `CODEX_HOME/hooks.json` - verified on this machine. But codex also requires a per-entry `trusted_hash` registered in `config.toml`, and Orca ships `orca agent hooks prepare-codex` for exactly that. So: our writer, generalized, plus an Orca call per vendor that needs one. |
| which events we capture | `events.CAPTURED` (SessionStart, UserPromptSubmit, Stop, PermissionRequest, …) | **RESHAPE** | good news: codex uses the same PascalCase names for most of them. A per-vendor name map, not a per-vendor implementation. Note Orca's own per-family mapping differs again (`opencode` → `SessionStart`, `gemini` → `BeforeAgent`, `amp` → `agent.start`). |
| payload fields we keep | `events.ALLOWLIST` | **RESHAPE** | |

## 7. The Orca objects we already create

Part 1 §5.2 is blunt: a caller handing off work **without supervision** should
"create no Run, Task, or Dispatch". We create all three and settle none.

| seam | ours | verdict | note |
|---|---|---|---|
| which lane are we in | `spawn.register_worker` creates Run + Task + Dispatch; nothing ever sends `worker_done` or calls `worker-release` | **DECIDE** ⚠ | two honest options. **(a) Supervised:** adopt each agent's terminal with `worker-start --terminal <handle>` after we launch it - we keep our launcher, our account choice, our argv, and Orca takes lifecycle ownership, which is also what may unlock `agentWait` in §3. **(b) Handoff lane:** stop creating Tasks, keep only what draws the team. Measured evidence for why this matters: **65 tasks in the bound Run, every one still `status: ready`**, and **five Runs all named "orcapipe team"**. |
| advance a task's status | never called | **ORCA** | `task-update --id --status`. Only needed in lane (b); a valid `worker_done` settles it automatically in lane (a). |
| Run binding | `spawn.ensure_run` | **FIX** ⚠⚠ | **Measured 2026-09-21: this is our bug, and it is bigger than five Runs.** See §7.1. |
| tab titles | not set; Orca falls back to cwd | **ORCA** | `terminal rename --title` exists and is unused. |
| escalate a block to the founder | `blocked.ESCALATE_AFTER_S` → `ocp ask human` | **ORCA** | `gate-create --task --question --options` is a first-class "blocked on a decision" object Orca draws and anyone can resolve from outside. Ours is a row only we can see. |
| memory / resource accounting | measured by hand once, in comments | **ORCA** | `diagnostics memory` is the sweep behind Orca's own Resource Usage popover; `worker-list --terminal-state reclaimable` names terminals owing a decision. |

### 7.1 `spawn.ensure_run` leaks a Run per terminal (settles O4)

Measured on this machine, read-only:

    25 Runs named "orcapipe team"      (28 total; the other 3 are agents' own work)
    ~9,000 tasks, effectively all `ready`, none ever settled
    18 agents' current tasks spread across 8 different Runs
    24 dispatches, every one `unsupervised` - no supervised worker exists anywhere

**Mechanism (revised - see D8 for the full account).** The primary defect is
that `orca.run_exists` returns `False` when it merely *could not ask*, so any
Orca restart makes `ensure_run` create a fresh Run and overwrite the key. The
overwrite below is real but secondary:

```python
run   = db.get_meta(con, RUN_KEY)
bound = orca.run_current()          # THIS terminal's binding
if bound:
    if bound != run:
        db.set_meta(con, RUN_KEY, bound)   # <-- team key follows one terminal
    return bound
```

Once a terminal is bound it stays bound, so every relaunch of agent A drags the
team key to A's Run, the next relaunch of B drags it to B's, and any newly
registered agent joins whichever Run won last. Nine of the 25 Runs were created
inside 33 seconds (09-18 09:37:11 → 09:37:44) - a cold start where several
agents raced with `RUN_KEY` unset and each created its own.

**What it costs.** The tree is the whole point of `register_worker` - "without
it an agent is one more tab". It is not being drawn:

| | |
|---|---|
| agents with a supervisor | 15 |
| ...whose task has **no parent link** | **9 (60%)** |
| harbor's six reports | all orphaned - that sub-team draws entirely flat |

`task-create --parent` refuses across Runs, and `spawn.register_worker`'s
fallback then creates the task flat *by design* ("a reason to draw the agent
flat, not a reason not to draw it"). The fallback works; it is firing 60% of
the time, silently.

**Verdict DRIVER, not ORCA:** Orca behaves as documented here. Binding is per
terminal and we are using it as if it were global. The fix is ours - one owner
of the Run, bound once, with the agents' own terminals never calling
`run-create`. This is a prerequisite for either lane in the row above: adopting
terminals into a Run that changes identity daily would inherit the same mess.

## 8. Untouched by any of this

**KEEP**, all of it - the reason a second vendor is affordable at all. These
carry no vendor concept and need no change:

`db.py` · `triggers.py` · `burnrate.py` · `measure.py` · `load.py` ·
`replay.py` · `handoff.py` · `drift.py` · `digest.py` · `team.py` · `hr.py` ·
`waiting.py` · `textsafe.py` · `yamlcompat.py` · `lock.py` · `service.py`

Sixteen modules, ~3.9k lines, zero mentions of `claude`. Note the five that
still carry the *window model* (§5) - vendor-free in name but not in shape.

The request queue (`ask.py`, `db.requests`) is **KEEP** and deliberately **not
ORCA**: Orca's mail is typed and addressed per Dispatch, but ocp's semantics -
one delivered request at a time, `--replaces`, cycle detection, the human lane,
refusal of unlisted edges - are ours and are the product. Reimplementing them
on Orca's mail would be a rewrite, not a deletion.

The `vendor` field already exists (`agents.DEFAULT_VENDOR`, `agents.parts`,
`agents.kind`, `burnrate`) and is drawn on the bridge. The seam was left open
on purpose; nothing reads it yet except the cost model and one column.

---

## 9. Surveyed / not surveyed

Read closely: `launch` `orca` `observe` `probe` `accounts` `hooks` `events`
`agents` `paths` `spawn` `handoff` `status`(head) `fold`(grep) `doctor`(grep)
`db`(schema).

**Not yet read line by line** - verdicts above touching them are from grep and
call sites, and may move: `daemon.py` (2242) · `tui.py` (2419) ·
`actions.py` (1222) · `cli.py` (1670) · `ask.py` (637) · `blocked.py` (347).

## 10. Next, in order

1. **`spawn.ensure_run`** against part 1 §5.6 - is the five-Run split a bug of
   ours, and what does it cost? Smallest question with a measurable answer.
2. **Measure adoption**: `worker-start --terminal <handle>` on one live agent.
   It gates §3's biggest ORCA (screen parsing) and §7's DECIDE, both.
3. **`daemon.py`** line by line - the largest unsurveyed module and the owner
   of every `/clear` and `/exit` call site.
4. **Prove the codex system prompt** (§1 ⚠). Nothing should be designed on a
   guess about `AGENTS.md` versus a first prompt.
5. Then the window-model reshape (§5, shape settled by §0.1), the big one, not
   started until 1-4 are known.
