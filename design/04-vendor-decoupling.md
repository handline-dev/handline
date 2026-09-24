# 04 - Taking claude out of ocp's core

Draft (T57). Design only; no code has moved.

`design/03-vendor-driver.md` already decided the shape, and part of it is built:
`ocp/vendors/` holds a `Driver` Protocol (`base.py`), `claude.py` and
`opencode.py`; accounts record a vendor; the config-dir variable, `never_touch`,
transcript layout, launch argv, session detection and credential presence are
driver answers. This file is the rest: the claude-specific code still living
outside `ocp/vendors/`, what interface it needs, and the order to move it.

## 1. What is still claude's, and where

| Area | Where | What is claude-specific |
|---|---|---|
| Config dir name | `paths.account_claude`, `paths.shared_claude` (10 call sites), `accounts.py` bootstrap | the `.claude` directory name; `shared/.claude/settings.json` created and edited by ocp |
| Headless runs | `fold.py:201` | `claude --model M -p prompt`; "claude not on PATH"; `status` reasons about `claude -p` spending quota with no statusLine |
| Folder-trust dialog | `orca.py:196-260` (`TRUST_PROMPT_MARKER`, `trust_prompt_showing`, `wait_for_trust_prompt`, `trust_prompt_folder`), `daemon/lifecycle.py` (`_handle_trust_prompt`, `_escalate_trust_prompt`) | the screen's wording and where the cursor sits. The *policy* (`trust.py`, `trust_roots.json`, founder approval) is generic |
| Other dialogs | `orca.py:408-560` (`claude_screen_blocker`, exit-dialog and session-limit wording) | already reached through `Driver.screen_blocker`, but the ~150 lines of claude markers sit in `orca.py`, not `claude.py` |
| Hooks | `hooks.py`, `cli/hooks.py`, `doctor.py:108-175`, `accounts.py` | Orca's hook block in `~/.claude/settings.json` mirrored into each account's `settings.json`; RUNNING/IDLE arrive through hooks. opencode has none (`tui.py:875`) |
| Usage meter | `usage_api.py` (oauth endpoint, `User-Agent: claude-code/2.1.0`, `claude auth status`, keychain read via `security find-generic-password`), `recover.py` (`refresh_credential`) | the endpoint, the credential store, the renewal trick. `recover.py` already refuses non-claude accounts by hand |
| Collector | `collect.py` | claude transcript JSONL, `statusLine` push history, context-window table by model id (`claude-opus-5`...), claude's synthetic-message marker |
| Window model | `pct_5h`, `pct_7d`, `reset_5h`, `reset_7d` in 25 files (`status.py` 89 refs, `triggers`, `usage_api`, `cli/usage`, `tui`, `replay`, `launch`, `burnrate`) | claude's two windows, by name |
| Prose | `guide.py`, `tui.py`, `actions/control.py`, `doctor.py` messages | "claude" as the word for the agent's tool |

Already generic and staying so: the queue, routing, triggers, handoff, the
daemon state machine, `burnrate` pricing by `vendor/model`, `pick_account`,
the db, the Orca client, `trust.py`'s approval gate.

## 2. The interface

`Driver` keeps being one Protocol for what every vendor answers. What only some
vendors can do becomes small optional capability Protocols, so a vendor without
one is a fact `isinstance` can test, the way `meters()` is today, and the core
`Driver` stops growing a method per claude feature.

Additions to `Driver` (every vendor answers):

```python
config_dir_path(account_dir: Path) -> Path     # accounts/<a>/.claude for claude; uses config_dir_name
headless_argv(exe: str, model: str | None, prompt: str) -> list[str] | None
                                               # None: this vendor has no one-shot run, fold is refused
trust_screen(tail: list[str]) -> str | None    # the folder shown when the screen is this vendor's trust dialog
trust_answer() -> str                          # keys that accept it (today: claude's "Yes" line)
context_window(model_id: str) -> int | None    # the table now in collect.py
```

`screen_blocker` stays the one dialog-reading method; the trust dialog is one
kind of `Blocker` it can return, so `trust_screen` may collapse into it (step 3
decides, after reading `_handle_trust_prompt`).

Optional capabilities:

```python
class Meter(Protocol):          # replaces usage_api.py's claude code
    def read_usage(self, acct: str) -> Reading | None
    def renew_credential(self, acct: str) -> tuple[bool, str]     # recover.py's job

class Collector(Protocol):      # design/03 §5's output contract
    def collect(self, transcript: Path, config_dir: Path) -> Snapshot | None

class StateHooks(Protocol):     # what hooks.py / cli/hooks / doctor do for claude
    def install(self, acct: str) -> tuple[bool, str]
    def status(self, acct: str) -> HookStatus
    state_from_hooks: bool      # tui.py:875's fork, asked instead of assumed
```

`Reading` and `Snapshot` are the dataclasses of design/03 §5: windows keyed by
duration, `status`/`error`, context figures. They are the only types the core
sees.

## 3. What moves, what stays

**Moves into `ocp/vendors/claude.py` (or `claude_*.py` beside it):** the trust
and dialog markers out of `orca.py`; `usage_api`'s endpoint, headers, keychain
read and renewal; `collect.py`'s transcript parse and model table; `hooks.py`'s
settings.json mirroring; fold's `claude -p` argv; the `.claude` directory name.

**Stays generic:** everything in the "already generic" list above, plus
`orca.py`'s Orca client (`screen_text`, `Blocker`, terminal calls),
`hooks`'s receiver `ocp hook` (it takes events from any source), `trust.py`, and
`status.py`'s trust rules (high-water mark, carried/`~`, `idle_floor`) - they are
about trusting a reading, not whose it is.

**On disk nothing is renamed.** `accounts/<a>/.claude` and `shared/.claude`
keep their names for claude; a driver-supplied name only matters for the next
vendor. No migration.

## 4. Order, one branch per step

Each step leaves the suite green and claude behaviour unchanged. Each branch is
audited before it is merged, as the four file splits were.

1. `vendor-paths` - `paths.account_claude`/`shared_claude` become
   `vendors.config_dir(acct)` / `vendors.shared_dir(vendor)`; `accounts.py`
   bootstrap asks the driver for what shared/ holds. 10 call sites.
2. `vendor-headless` - `Driver.headless_argv`; `fold` builds its command from the
   agent's vendor and refuses cleanly when there is none. `status`'s wording
   about `claude -p` names the vendor.
3. `vendor-dialogs` - trust marker/keys and `claude_screen_blocker`'s markers move
   from `orca.py` to `claude.py`; `daemon/lifecycle` calls the driver. `orca.py`
   loses every claude string.
4. `vendor-hooks` - `StateHooks`; `hooks.py` becomes claude's implementation,
   `doctor` and `cli/hooks` ask the driver; `tui.py:875` reads `state_from_hooks`.
5. `vendor-meter` - `Meter`; `usage_api.py` and `recover.py` move behind it;
   `daemon/usage_poll` and `cli/usage` call the driver. The keychain read is
   claude's only.
6. `vendor-collect` - `Collector` and the §5 contract; `collect.py` becomes
   claude's collector; the model table becomes `Driver.context_window`.
7. `vendor-windows` - design/03 step 3, the large one: 25 files stop reading
   window names. Write `pct_5h` and `windows` together for one release so a
   bad read is visible, then drop the old keys. Needs 5 and 6 first, because
   they are what produces the new shape.
8. `vendor-prose` - optional sweep of user-facing "claude" strings to the
   vendor's name. Cosmetic; last, and skippable.

Steps 1-4 are independent of each other and could run in parallel branches;
5, 6, 7 are a chain.

## 5. Decisions for the founder

- **Is a second real vendor a goal, or only a clean seam?** opencode's driver
  exists but it is unmetered and has no hooks; codex is measured only partly
  (design/03 §4.2). Steps 1-7 need neither, and every step is worth having if
  the answer is "seam only". If codex is next, step 6 is the gate: without a
  collector ocp cannot park a codex agent.
- **Tool denial (design/03 §6) is still unresolved** for a vendor without
  `--disallowedTools`. Nothing here decides it; it must be settled before a
  non-claude agent is given real work.
- **Window model dual-write:** one release of both key sets in step 7, or a
  hard cut. Recommend the dual write; the cost is a release of duplicated keys.
- **Optional capability Protocols vs one growing `Driver`:** recommended above
  because `Driver` is already 25 methods and three of the new ones apply to
  claude only in practice. If the founder prefers a single Protocol with
  no-op defaults, steps 4-6 keep their shape and only the type changes.
