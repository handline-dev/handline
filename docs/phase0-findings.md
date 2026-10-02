# Phase 0 evidence for the verdicts

Measured on: macOS 15 (Darwin 25.5.0) · Claude Code **2.1.273** · Orca **1.4.203** · 2026-09-16
Every probe ran with `--model sonnet`.

---

## H · Does `~/.claude.json` follow `CLAUDE_CONFIG_DIR`? → **True**

```bash
BEFORE=$(stat -f %m ~/.claude.json)
CLAUDE_CONFIG_DIR=/tmp/ocp-probe/h/.claude claude --model sonnet -p "reply with the single word ok"
```

Output: `Not logged in · Please run /login`

```
/tmp/ocp-probe/h/.claude/.claude.json                       ← created inside the profile
/tmp/ocp-probe/h/.claude/backups/.claude.json.backup...
/tmp/ocp-probe/h/.claude/projects/-Users-<user>-workspaces/
~/.claude.json mtime: 1789526410 → 1789526410  (unchanged)
```

`.claude.json` is created inside `CLAUDE_CONFIG_DIR` and the one in home is left alone. **Isolation is complete.**

### A bonus finding: the credential follows too (a risk the design left open)

That an empty `CLAUDE_CONFIG_DIR` gave `Not logged in` means that only the **default directory** uses the macOS Keychain entry
`Claude Code-credentials` (acct=`<user>`; we confirmed it exists and did not read its value).
A non-default `CLAUDE_CONFIG_DIR` uses the credential in its own directory.
→ **The premise of assumption A (two accounts running at once) holds on macOS.** It does not collapse into one globally shared Keychain entry.

---

## I · Can context usage come from the statusLine? → **True (better than the design expected)**

> The shipped `probe.sh i` uses `claude -p`. **A headless run has no statusLine**, so running it as is
> gives a false negative. In fact `status.json` was not created at first.
> It must be started interactively in a real pty (`libexec/ptyrun.py`).
> The user's `settings.json` was left alone and ours was injected with `--settings`.

Top-level keys of the statusLine stdin:

```
context_window  cost  cwd  effort  exceeds_200k_tokens  fast_mode  model
output_style  rate_limits  scratchpad_dir  session_id  thinking  transcript_path  version  workspace
```

After one turn has happened:

```json
"rate_limits":    { "five_hour":  { "used_percentage": 4,  "resets_at": 1789543200 },
                    "seven_day":  { "used_percentage": 41, "resets_at": 1789678800 } }
"context_window": { "used_percentage": 5, "remaining_percentage": 95,
                    "context_window_size": 1000000, "total_input_tokens": 45465,
                    "current_usage": { "input_tokens": 2, "output_tokens": 4,
                                       "cache_creation_input_tokens": 18135,
                                       "cache_read_input_tokens": 27328 } }
"cost":           { "total_cost_usd": 0.0780496 }
```

**Every** value the §4 table asked for arrives. And where the design meant to **compute** context usage
as "session tokens / model limit", `context_window.used_percentage` **arrives as is.**

### What this changes

- **The §8.1 context trigger needs no transcript aggregation.** So the `message.id` dedup on that path is not needed either.
  (Dedup is still required when the **burn rate** is computed from the transcript: assumption E stands)
- `context_window.used_percentage` is **`null` before a turn happens.** It is not 0. It is unknown, so it is
  handled fail-closed. (A null `pct_5h` **splits into unknown and quiet** after §M; context has
  no such split, because before a turn "no value yet" cannot be told apart from "the session died".)

`seven_day_opus` was **not present** in this sample. The collector takes it as `// null`.

---

## J0 · Does Orca force its own statusLine? → **False (which favors us. The data source is alive)**

```
which -a claude   → /Users/<user>/.local/bin/claude  (printed twice, but only a PATH duplicate of the same path)
file              → Mach-O 64-bit executable arm64   ← not a shell wrapper
ps -o args=       → claude                            ← no --settings / --status-line
managed-settings.json  → absent from all 3 standard locations
```

Orca's Claude integration is **not a statusLine but hooks.** `SessionStart` / `UserPromptSubmit` and others in `~/.claude/settings.json`
call `~/.orca/agent-hooks/claude-hook.sh`, and it reports to the Orca runtime through the `ORCA_AGENT_HOOK_*` injected into the terminal.
`ORCA_OMP_STATUS_EXTENSION` is for Orca's own agent status (the OMP/pi runtime), not the Claude Code statusLine.

The `statusLine` in the current `~/.claude/settings.json` is `ccstatusline`, **which the user installed themselves and which has nothing to do with Orca.**

**Decisive evidence**: in an interactive session started with `claude --settings <ours>` inside this Orca session,
**our collector really ran and `status.json` was written.**

---

## J4 · Does the worktree `.envrc` (direnv) take effect? → **Not applicable. direnv is not needed**

`direnv` is **not installed** on this machine. And there is no longer a reason to install it.

### The J4 alternative in the README is a misreading

Reading `orca skills get orca-per-workspace-env` shows that `orca.yaml environmentRecipes` is a feature that starts
**a disposable remote machine per workspace** (a cloud sandbox, a VM, an SSH host, a container).
It is **not** a way to inject environment variables into a local worktree. It cannot serve as the first alternative if J4 is false.

### Measured: the environment of a terminal Orca started

Captured with `orca terminal create --worktree path:... --command "env > /tmp/..."`:

```
CLAUDE_CONFIG_DIR=                    ← empty. Orca does not touch this variable
ORCA_AGENT_HOOK_PORT=56558
ORCA_AGENT_HOOK_TOKEN=<redacted>
ORCA_AGENT_HOOK_ENDPOINT=/Users/<user>/Library/Application Support/orca/agent-hooks/endpoint.env
ORCA_PANE_KEY=<redacted>
ORCA_WORKTREE_ID=...::/Users/<user>/workspaces/harbor-app
```

Two things are settled:

1. `CLAUDE_CONFIG_DIR` is **empty**, so we set it freely. No conflict
2. `--command` is an **arbitrary shell string**, and `env CLAUDE_CONFIG_DIR=... claude` is all it takes

→ **Neither direnv, nor environmentRecipes, nor tmux is needed.** One layer of launcher wrapper is enough.

---

## Orca already supports multiple accounts, but it cannot solve our problem

The design's phase0/README §1 said [stablyai/orca#11557] was **not implemented.** **It is implemented now.**

```
$ orca account list
Managed Claude accounts (2):
  account-a@example.com
  account-b@example.com (active)
```

Two accounts are already registered on the user's machine. But `--json` shows:

```json
"activeAccountId": "8464aac1-...",
"activeAccountIdsByRuntime": { "host": "8464aac1-...", "wsl": {} }
```

**There is one active account per runtime.** Neither `worktree create` nor `terminal create` has an `--account` flag,
and `account` does not appear in the `orca agent-context` schema.

→ The design's circumstantial inference that **"Orca is the global-swap kind"** is **exactly right.**
It cannot be used for concurrent multi-account runs. **The `CLAUDE_CONFIG_DIR` way is still valid.**
Only the wording of the reason must be updated, from "not implemented" to "implemented, but a global swap with no concurrency".

---

## F · `autoContinueAtUsageLimit` → **The feature exists. A forced choice is needed**

```
$ strings ~/.local/bin/claude | grep -o autoContinueAtUsageLimit
autoContinueAtUsageLimit
```

`disableAllHooks` and `allowManagedHooksOnly` are also real settings keys → `ocp doctor` must check all three.

---

## M · `rate_limits` arrives **only as much as that session learned at its last response** (new 2026-09-17)

We took the raw statusLine stdin of two sessions on the same account at the same time and compared them.

A session just started (alpha, 10:50):

```json
"rate_limits": {"five_hour": {"used_percentage": 0, "resets_at": 1789614000},
                "seven_day": {"used_percentage": 41, "resets_at": 1789783200}}
```

A session started at 20:00 yesterday with no request since (same alpha, same 10:50): the `five_hour` **key itself is missing**:

```json
{"pct_5h": null, "reset_5h": null, "pct_7d": 41, ...}
```

The 5h window rolled over at 07:00 (`resets_at` 12:00 minus 5h). It is not omitted because usage is 0: the session just
started was also at 0% but the value came.

**External evidence.** [claude-code#75408] states the mechanism: *"The value appears to reflect the
rate-limit state attached to that specific session's last actual API response, not a live re-check
… each terminal is showing a stale snapshot from a different moment."* So every session has a **different
observation time.** But we found no doc or issue that covers the part where "the whole key is dropped after the window rolls over"
(the closest, #45133, is a separate bug where the whole `rate_limits` object is absent). **That half rests on our own measurement
and is not confirmed by any documentation.**

**The bug this caused.** The collector wrote that null into the account file as is, so on the same account **an idle session
erased the value a working session had just reported.** Then every reader saw "usage unknown", and the fail-closed
rule handed off 5 perfectly healthy agents (one fold per agent). `ocp triggers` showing every agent as `YES ·
usage_unknown` was this state. The same cause also shows up as the dashboard value **jumping around**:
when an older session draws last, the account value goes backwards.

**The fixed rules** (canonical in design §4)

- A payload that does not report a window does **not erase** that window's earlier value (while `resets_at` is still ahead)
- If there is **no `resets_at` at all** to tell whether the window ended, drop the value but mark it `rl_lost`. A lost value is
  unknown, not quiet, and **that state is kept until the window is reported again** (if it held for one tick only,
  the next refresh would read 0%). The same for 7d
- **Within one window the account number does not go down** (since each session has a different observation time)
- The age of a carried value is measured by `rl_at`, not `collected_at`. Past 900 seconds it is still used
  as a floor for triggers, but **a new assignment is refused**
- If there is still no value, that is **quiet**, so it reads as 0% and assigns

**Two known exceptions.** (1) `ocp fold` is headless `claude -p` and has no statusLine, so the quota fold uses
is seen by no collector. So `quiet` means **"no reported use"**, not "no one used it". (2) If the same subscription is used from a settings
directory ocp does not collect (the desktop app, another machine, a person's own `~/.claude` = the `default` account), that is not seen either. The 5h window is
**per subscription, not per settings directory.**

[claude-code#75408]: https://github.com/anthropics/claude-code/issues/75408

---

## Still unchecked

| # | Assumption | Why it is not closed yet |
|---|---|---|
| **A** (long run) | No token refresh conflict when two accounts run **at once for a long time** | The second account must be logged in under `CLAUDE_CONFIG_DIR`. **A person must run `claude /login` once** (AGENTS.md rule 1) |
| **D** | Headless injection + clear | Not needed before Phase 3 |
| **G** | `rate_limits` on the `CLAUDE_CODE_OAUTH_TOKEN` path | There is no token. Needed only when accounts run short |
| **J4-full** | Does Orca **recognize as an agent** one started by the wrapper, and does hibernate/restore keep the env | Blocked for the same reason as A |

[stablyai/orca#11557]: https://github.com/stablyai/orca/issues/11557
