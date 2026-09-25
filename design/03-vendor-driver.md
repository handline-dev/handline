# 03 - The vendor driver

Reference design (#1315). Built: §7 steps 1 and 2 (`ocp/vendors/`, #1657) and, under `design/04-vendor-decoupling.md` (T57), the dialogs, headless, hooks and meter seams. The rest is open.

One place that answers, per vendor, the fifteen questions enumerated in
`docs/vendor-extension-board.md` (local, not in the repo) §11. Everything else in ocp stays where it is.

Sources: the survey (`docs/orca-cli-survey.md`), the seam board, and the
decisions (`docs/vendor-extension-decisions.md`, D1-D10). Where codex's answer
was measured on this machine it says so; where it was not, it says that too.

---

## 1. What belongs in here, and what does not

The board classifies every seam. Only **DRIVER** rows go in this file.

| | |
|---|---|
| **in** | the 15 DRIVER seams: argv, binary, account env, resume, identity, credentials, shared config, memory, process attribution, dialog reading, decline key, context reclaim, session end, collection, probe |
| **out - KEEP** | the queue, routing, triggers, handoff, burn rate, account choice, the daemon's state machine. No vendor concept, and adding one would be a mistake |
| **out - RESHAPE** | the window model (D2) and the preamble writer (D10). Shared code whose *shape* is being generalized; the driver supplies parameters to it, not an implementation of it |
| **out - ORCA** | identity, activity, liveness, wait detection. Orca answers these for 18 agent families; asking a driver would be re-writing what we get free |

**The test for a new method:** if two vendors would give the same answer, it
does not belong here. That is what keeps this file from becoming a second copy
of ocp.

## 2. Shape

```
ocp/vendors/
    __init__.py     for_agent(name) -> Driver ; registry keyed by agent.yaml's `vendor`
    base.py         the Protocol below, and the shared dataclasses
    claude.py       today's behaviour, moved rather than rewritten
    codex.py        new
    collect_claude.py   (today's ocp/usage/collect.py - a puller already, D11)
    collect_codex.py    new - the same shape, over the rollout JSONL
```

Selection is already possible: `agents.parts()` returns `(vendor, model,
effort)` and `agents.DEFAULT_VENDOR` is `"claude"`. The field exists and is
drawn on the bridge; nothing reads it yet except `burnrate`, which already
prices by `vendor/model`.

## 3. The interface

```python
# ocp/vendors/base.py
from typing import Protocol, Literal

ADDITIVE, SUBSTITUTIVE = "additive", "substitutive"


class Driver(Protocol):
    name: str                       # "claude" | "codex" | ...
    min_version: tuple[int, ...]    # doctor refuses below this

    # -- process ---------------------------------------------------------
    def binary(self) -> str | None:
        """The executable, or None when it is not installed. (§11.2)"""

    def config_dir_env(self) -> str:
        """The variable that pins a session to one account's config dir.
        CLAUDE_CONFIG_DIR / CODEX_HOME. (§11.3)

        One string, and it also makes `observe.account_of_process` (§11.9)
        vendor-neutral: that function reads this variable off `ps eww` and
        resolves it under `paths.accounts_dir()`. It needs no other change."""

    def launch_argv(self, cfg: dict, extra: list[str]) -> list[str]:
        """argv for one session: model, effort, tool denial, mcp. (§11.1)

        NOT the preamble - D10 moved that to a file. See `preamble_file`."""

    def is_resume(self, extra: list[str]) -> bool:
        """Does this argv carry a session to resume? (§11.4)
        Every spelling the vendor accepts; reading a resume as a fresh start
        picks an account by headroom and then hands over a session id that
        exists under a different one."""

    # -- account identity and credentials --------------------------------
    def identity(self, account: str) -> str | None:
        """Who this account is signed in as, asked of the vendor's own
        record. Never read from a credential file. (§11.5)"""

    def credential_present(self, config_dir: Path) -> bool:
        """Whether a credential exists. **Never returns or logs it.**
        Claude: a Keychain item, queried without -w. Codex: auth.json
        exists. (§11.6 - AGENTS.md rule 1)"""

    def login_argv(self, account: str) -> tuple[list[str], dict]:
    def logout_argv(self, account: str) -> tuple[list[str], dict]:
        """A human runs the login; ocp never touches the secret."""

    def shared_items(self) -> list[str]:
        """Names under shared/ that are linked into each account's config
        dir. (§11.7)"""

    def memory_dir(self, config_dir: Path, repo: Path) -> Path | None:
        """Where this vendor keeps one repo's memory, so an account move can
        carry it. None when the vendor has no such thing. (§11.8)"""

    # -- the preamble (D10) ----------------------------------------------
    def preamble_file(self) -> tuple[str, Literal["additive", "substitutive"]]:
        """The file ocp writes into the agent's worktree, and whether it
        joins the repo's own instructions or replaces them."""

    # -- driving the TUI --------------------------------------------------
    def clear_command(self) -> str:      # "/clear" | "/new"   (§11.12)
    def exit_command(self) -> str:       # "/exit"  | "/quit"  (§11.13)

    def read_dialog(self, tail: list[str]) -> "Dialog | None":
        """The dialog on screen as data - title, numbered options, footer,
        body - or None when any part of it is not there to read. What cannot
        be parsed is not guessed. (§11.10)

        Detection of *whether* an agent is waiting is Orca's now (D9); this
        is only for saying which dialog, and for the next method."""

    def declining_key(self, dialog: "Dialog") -> tuple[str | None, str]:
        """(key, why not). Only a key that leaves the world as it is, and
        only on evidence from the panel itself. (§11.11)"""

    # -- telemetry (§11.14, §11.15) ---------------------------------------
    def install_collector(self, shared_dir: Path) -> None:
        """Put this vendor's collector where its sessions will run it.

        Nothing to do for either vendor we have: since D11 both are read, not
        run. It stays in the Protocol for a vendor that will only push."""

    def collector_kind(self) -> Literal["push", "pull"]:
        """push: the vendor calls our script. pull: we read the vendor's own
        session files.

        Both answers are `pull` today, which is the point of D11: one
        mechanism for both vendors instead of a push we could only build for
        Claude. What differs per vendor is where the files are and how a turn
        is read out of one, not whether we are called."""

    def probe(self, account: str, timeout: int) -> "AccountStatus":
        """Spend the minimum needed to make a quiet account report. A vendor
        whose usage can be read from a file should not implement this."""

    # -- events -----------------------------------------------------------
    def hook_events(self) -> dict[str, str]:
        """ocp's event name -> this vendor's. Mostly identity: codex uses the
        same PascalCase names in CODEX_HOME/hooks.json, verified here."""

    def install_hooks(self, events: list[str], command: str) -> None:
        """Codex additionally needs each entry's trusted_hash registered in
        config.toml; Orca ships `orca agent hooks prepare-codex` for it."""
```

`Dialog` and `AccountStatus` are the dataclasses ocp already has
(`orca.Dialog`, `status.AccountStatus`) - shared, not per vendor.

## 4. The two answer tables

### 4.1 claude - today's code, moved not rewritten

| method | where it is now |
|---|---|
| `binary` | `shutil.which("claude")`, 7 call sites |
| `config_dir_env` | `CLAUDE_CONFIG_DIR` (`accounts.env_for`) |
| `launch_argv` | `launch.build` |
| `is_resume` | `launch._is_resume` |
| `identity` | `accounts.identity` |
| `credential_present` | `accounts.logged_in` / `keychain_service` |
| `login/logout_argv` | `accounts.login_command`, `logout_argv` |
| `shared_items` | `accounts.LINKED` |
| `memory_dir` | `accounts.memory_project_dir` |
| `preamble_file` | new - `("CLAUDE.local.md", ADDITIVE)` |
| `clear/exit_command` | `/clear` / `/exit`, moved out of `orca.clear_context` / `close_session` |
| `screen_blocker` | `orca.claude_screen_blocker`; `idle_from_screen` false, Orca's tui-idle alone |
| `read_dialog` | `orca.parse_dialog` |
| `declining_key` | `orca.declining_key` |
| `install_collector` | nothing to install - `ocp/usage/collect.py` reads the transcript (D11) |
| `collector_kind` | `pull` (it was `push`; the statusLine no longer collects) |
| `probe` | `probe.py` |
| `hook_events` | `events.CAPTURED`, identity map |

### 4.2 codex - measured where marked ✅

| method | answer | |
|---|---|---|
| `binary` | `codex` | ✅ installed, 0.149.0 |
| `config_dir_env` | `CODEX_HOME` | ✅ |
| `launch_argv` | `--model`, `-c model_reasoning_effort=…`; **tool denial has no equivalent** | ⚠ §6 |
| `identity` | `auth.json` holds the account; a command for it is unconfirmed | ✗ |
| `credential_present` | `CODEX_HOME/auth.json` exists - a plain file, simpler than the Keychain | ✅ |
| `login_argv` | `codex login` (device authorization) | ✅ per Orca |
| `preamble_file` | `("AGENTS.override.md", SUBSTITUTIVE)` | ✅ #1306/#1307 |
| `clear_command` | `/new` | ✅ in the binary's command list |
| `exit_command` | `/quit` | ✅ same |
| `read_dialog` | unknown - codex's TUI has not been read on screen | ✗ |
| `collector_kind` | **`pull`** - no statusLine hook | ✅ |
| collector source | rollout JSONL: `token_count.info.model_context_window`, `.total_token_usage`, `.rate_limits.{primary,secondary}` each `{used_percent, window_minutes, resets_at}` | ✅ |
| `probe` | **not needed** - usage is in a file | ✅ |
| `hook_events` | same PascalCase names, in `CODEX_HOME/hooks.json`; needs `trusted_hash` in `config.toml` | ✅ observed live |

Also measured: this account's usable models are `gpt-5.6-terra`,
`gpt-5.6-luna`, `gpt-5.5`; the configured `gpt-5.6-sol` is refused on a
ChatGPT account, so `launch_argv` must not inherit `config.toml`'s default.

### 4.3 opencode - measured on 1.18.32 (`ocp/vendors/opencode.py`)

| method | answer | |
|---|---|---|
| `binary` | `opencode` | ✅ |
| `config_dir_env` | `XDG_DATA_HOME`, pointed at `<account>/xdg-data`; opencode keeps auth.json, its db and logs under `opencode/` inside it. `OPENCODE_CONFIG_DIR` moves nothing. Config and state stay shared | ✅ #1751 |
| `is_session` | the command line starts with the binary, then an option or nothing; `run`, `serve`, `auth` and the rest are not sessions | ✅ `ps` of a live TUI |
| `meters` | false: the db tallies spend, nothing says what is left | ✅ vendor-extension-board §12.4 (local, not in the repo) |
| `is_resume` | `-s`/`--session`, `-c`/`--continue` | ✅ `--help` |
| `launch_argv` | `-m provider/model` only. The TUI has no flag for effort, tool denial, MCP config or a system prompt | ✅ |
| `launch_env` | `OPENCODE_CONFIG_CONTENT`: the preamble as an `instructions` file (`<agent>/opencode-preamble.md`), added to the system prompt and reread for every session, a `/new`'s included; `Agent` denied as `permission.task`, which takes the tool out of the model's list. SendMessage, ScheduleWakeup and CronCreate have no opencode tool | ✅ #1926, a live session named the file's codeword and had no task tool |
| `prompt_argv` | `--prompt <text>`, submitted on start. A positional is the project directory | ✅ #1926 |
| `credential_present` | `opencode auth list` counts more than 0 credentials. The file alone does not answer: a logout leaves `{}` | ✅ |
| `login/logout_argv` | `opencode auth login` / `logout`, each asking which provider | ✅ |
| `identity`, `subscription_type` | none: a credential is per provider and names no account | ✅ |
| `shared_items` / `never_touch` | `[]` / `{"opencode"}` | |
| `memory_root` | none: no per-repo memory, and sessions are db rows, not transcript files | ✅ |
| `clear/exit_command` | `/new` (new session id, start screen) / `/exit` (process ends, shell stays) | ✅ #1926 |
| idle | Orca's `tui-idle` is satisfied throughout an opencode turn, from `--prompt` or typed; it does read a permission request as blocked. `idle_from_screen`: idle is the prompt box drawn with no "esc interrupt" under it | ✅ #1926 |
| `screen_blocker` | a permission request (footer "enter confirm", "Allow once" selected) is MODAL; a screen with no prompt box is UNREADABLE | ✅ #1926 |
| MCP config, effort, hook events | not built: no `ocp hook` events, so a first task's delivery is not confirmed and no context is collected | ✗ |

## 5. The collector's output contract - the part that matters most

Two collectors, one schema. This is D2's reshape and it is what makes the
driver worth building: everything downstream of the snapshot
(`status.py`'s five-case model, `triggers`, `burnrate`, `pick_account`) stays
exactly as it is, because it stops reading window *names*.

```jsonc
{
  "account": "alpha", "agent": "hr", "vendor": "claude",
  "collected_at": 1789..., "session_id": "...", "transcript_path": "...",
  "model_id": "...", "cwd": "...",

  // (D2) windows keyed by duration, Orca's shape. Replaces
  // pct_5h / pct_7d / pct_opus_7d and their resets.
  "windows": [
    {"minutes":   300, "used_percent": 21, "resets_at": 1789..., "observed_at": 1789..., "carried": false, "lost": false},
    {"minutes": 10080, "used_percent": 77, "resets_at": 1790..., "observed_at": 1789..., "carried": false, "lost": false}
  ],
  "status": "ok",          // or "unavailable", with...
  "error": null,           // ...the reason, per Orca's per-provider shape

  // per session, and the number nothing else on this machine has (D9)
  "ctx_used": 68000, "ctx_size": 200000, "ctx_pct": 34
}
```

Rules that survive untouched, because they are about *trusting* a reading
rather than about whose it is: the high-water mark inside one window, the
carried/`~` and carried-old/`!` marks, `quiet` vs `unknown`, `idle_floor`,
`rl_at` being when a figure was observed rather than when the line was drawn.

The one behavioural change: a window is found by duration, so
`PCT_5H_BLOCK` becomes "the shortest window" and `PCT_7D_BLOCK` "the longest",
and a vendor reporting one window or three needs no new code.

## 6. Two things that do not translate

**Tool denial (§11.1).** `--disallowedTools Agent,SendMessage,ScheduleWakeup,
CronCreate` has no codex equivalent. The *invariant* is what must survive, not
the flag: no subagents, no self-scheduled wakeups, no agent-to-agent messaging
outside the queue. Candidates, in order of strength: a `PreToolUse` hook that
denies (codex's hook schema has `PreToolUseHookSpecificOutputWire`, so denial
is expressible), then the approval policy, then a line in the preamble - which
D10 already accepted is only advisory. **Unresolved; decide before a codex
agent is given work.**

**The probe (§11.15).** It exists because claude's usage only appears in a TUI
status line. A vendor that writes usage to a file makes it unnecessary, so
`probe` is optional on the Protocol and `due()`/`unread()` must tolerate a
driver that has none.

## 7. Order to build, and what guards each step

Each step is independently shippable and leaves the tree green.

1. **`ocp/vendors/` with `claude.py` only.** Pure move. The guard is that the
   suite passes unchanged - no test should notice.
2. **`config_dir_env` through `observe.account_of_process`.** Smallest real
   generalization; makes one function vendor-neutral for free. Built: the
   config-dir *variable* is the driver's answer at every site that named it
   (`observe.account_of_process`, `actions._trust_one_worktree`, doctor's
   missing-dir line), and so is what that directory holds - `never_touch`
   (was `accounts.NEVER_TOUCH`) and the transcript layout `collect`,
   `handoff` and `tokenlog` walk, through `memory_root` and
   `memory_project_dir`. `collect.encode_cwd` had been a second copy of the
   latter's rule.

   Deliberately **not** in step 2: the config dir's *name*. `paths.account_claude`
   spells `.claude` and thirty call sites say it. `account_of_process` does not
   need it - it takes the first part under `paths.accounts_dir()`, whatever the
   directory is called - so nothing in this step was blocked by it. It is the
   right companion to the account-vendor change below, which is the other thing
   that has to know which vendor an account is.
3. **The window model (§5).** The big one: 208 lines, 16 files, five of which
   never mention `claude`. Do it with both `pct_5h` and `windows` written for
   one release so a bad read is visible, then drop the old keys.
4. **The preamble writer (D10).** Shared code, two-row table.
5. **`codex.py`**, collector first (§11.14) - without context ocp cannot park.
6. **Tool denial (§6).** Blocking for a codex agent that does real work.

Steps 1-4 are vendor-neutral refactors that leave ocp single-vendor and
working. **Nothing in 1-4 needs codex to exist.**

**Before a second vendor's account exists: accounts carry a vendor.** Today
an account records none, and every account-level answer comes from
`vendors.for_accounts()`, the default vendor's driver. That is two places,
not one:

- `for_accounts()` itself, which then takes the account.
- `accounts.LINKED`, which is `for_accounts().shared_items()` frozen at
  import time into a module constant. Three functions read it, and there is
  no account to hand a constant, so it has to become a per-account call.
  This is a change of shape, not of argument.

The failure if this is skipped is silent: env_for's config-dir variable,
logged_in, identity, login and logout, memory_root and shared_items would
all give claude's answers for the second vendor's account, and nothing
would raise. (#1691)

## 8. Open

- `read_dialog` for codex: needs a live TUI and a screen read.
- Whether codex's `/new` re-reads the preamble file (D10's residue).
- opencode's hook events (an opencode plugin posting to `ocp hook`, as Orca's
  own status plugin does) and its context collector (§4.3's ✗ row).
- Whether `identity` has a codex command, or must be read from `auth.json`.
