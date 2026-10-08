# Usage, vendors and status: scope of the Go port

Status: scope for review; nothing is ported. Python read: `handline/usage` (14 modules, 3.5k lines) and `handline/vendors`
(9 files, 2.6k lines). Go today: `go/internal/usage` (config, status read, month cycle, 0.3k) behind `accounts.list` and
`stats.get`; `lifecycle` already holds a `UsagePort` interface (PickAccount, NotFor, Fit, CapOf, ActiveOn) that nothing implements.

## Two findings that shape the port

1. **The Python meter reads a credential.** `claude_meter.py` runs `security find-generic-password` for the account's OAuth
   token, renews it with `claude auth status`, and GETs `api.anthropic.com/api/oauth/usage`. The brief says the port reads no
   credential. The Agent SDK has a control request for exactly this: `get_usage` (experimental, in the SDK types, not yet
   seen on the CLI we pin) returns `rate_limits.five_hour` and `seven_day` as `{utilization, resets_at}`, plus plan type, with no
   model turn. A short-lived `claude --print --input-format stream-json` process per poll, using the S1 `structured.Open`, asks the
   CLI to do the request with its own login. The credential never reaches the daemon. **Live check needed before this is
   committed** (outside the sandbox, one `initialize` + `get_usage` on a test account): it works with no session running, it
   renews a dead token itself or says so, its figures equal the endpoint's, and the extra `rate_limit_event` frames on a running session
   (`utilization`, `resetsAt`, `rateLimitType`) can feed the same record for free. If `get_usage` fails the check, the fallback is the
   Python behaviour (read the keychain item for one request) and needs a founder yes, because it is a credential read.
2. **Most of `vendors/` is the terminal path.** Under structured sessions the screen helpers, the ask/confirm pickers, the
   start-up screens and the statusLine-style collector are not needed. What lives on is small: how to start the CLI (already
   ported as `lifecycle/build.go`), who an account is, and the meter.

## Module by module

L = live and used today. S = made unnecessary by structured sessions. D = legacy to drop (the founder's rule: do not carry legacy code into Go). P = straight port.

| Module | Lines | Used by | Verdict |
|---|---|---|---|
| `usage/windows` | 67 | status, usage_api, burnrate, outlook, db | **P**. Go has the reader (`usage.Window`); add the writer shape. |
| `usage/usage_api` | 129 | usage_poll, recover, status | **P**, but `read` asks the CLI (`get_usage`), `record` (merge, never lower a figure inside its own window) is straight. |
| `vendors/claude_meter` | 208 | usage_api | **D/replaced**: credential read, keychain, HTTPS. Replaced by `get_usage`. |
| `daemon/usage_poll` (not in the brief) | 191 | tick | **P**: due rules, cooldowns, backoff, one success per tick, `collect_snapshots`. `collect_snapshots` is **S** for structured agents (the `result` and `rate_limit_event` frames carry it). |
| `usage/recover` | 104 | usage_poll, `hl account` | **P**: one real turn on a stale or dead account (`hl fold` subprocess, as G7 does). Check whether `get_usage` already renews; if so only the needs-relogin mark survives. |
| `usage/status` | 1334 | tick, launch, send, handoff, reads, ui | **P, the core.** `AccountStatus` fail-closed rules (374), `read`, `windows_of`, runway (~200), `pick_account` (194) with its reasons. `read_for_agent` and `reported_account` map an agent to an account. About 0.9k of it is the pick and runway rules. |
| `usage/load` | 273 | pick, reads, lifecycle | **P**: active agents per account, caps (Go has the config part), `costs`. Active = mid-turn: from S1 `State()` for structured agents, from the DB for terminal ones. |
| `usage/burnrate` | 294 | evaluate, load | **P** in two halves. `Costs` (read samples, median, floor and ceiling) is needed by the pick. `observe` and `refresh_long_ratios` (write samples) can follow: with no samples `describe` returns `TYPICAL_AGENT_BURN_PER_S`. Check what the pick does with no long ratio (`_no_long_room`): it may refuse on the weekly window. |
| `usage/measure` | 81 | evaluate, handoff, lifecycle | **P**, small (cold-start cost for moving an agent). Not needed for the first cut. |
| `usage/cycle` | 244 | status, reads, subscriptions | **Mostly in Go.** Port `position` and `set_cycle` (writers). |
| `usage/tokenlog` | 151 | evaluate, tools/usage/fit.py | **P but late.** Reads only the account's `projects` transcripts; feeds `stats` estimates. Offline fit tool is not served. Under structured sessions `result.model_usage` gives per-call tokens directly; decide then whether to keep the transcript scan. |
| `usage/prices` | 84 | stats, agent_settings | **P**, tiny table. |
| `usage/stats` | 231 | stats.outlook view, details | **P**, pure over the DB; late. |
| `usage/outlook` | 178 | stats.outlook | **P**, pure; late. `stats.outlook` is the one deferred read still missing in Go. |
| `usage/collect` | 194 | usage_poll, events, details | **S** for structured agents (context and cost come in `result`). **T** (frozen, unported) for terminal agents. |
| `usage/replay` | 166 | tests only | **D**. No caller. |
| `vendors/__init__`, `base` | 539 | everything | **P, trimmed**: `account_vendor`, `config_dir`, `for_account`, `record_vendor`, `identity`, `subscription_type`, `credential_present` (login state, already read by `reads/accounts.go`). The `Driver` methods about screens (about 25 of 45) are **S/D**. |
| `vendors/claude` | 641 | launch, accounts | **P** for argv/env/identity/profile/login (about 0.25k; `lifecycle/build.go` has argv). Screen blockers, trust and bypass screens, `_describe_backgrounded` etc. (about 0.35k) **S/D**. |
| `vendors/claude_collect` | 182 | collect | **S**. |
| `vendors/claude_ask`, `claude_confirm` | 235 | prompts | **S** (the `can_use_tool` callback replaces the picker). |
| `vendors/claude_hooks` | 402 | install, doctor | **T/decide.** Writes the shared settings.json hooks that report RUNNING/IDLE and the PreToolUse guard. Structured sessions need none of the state hooks; the PreToolUse guard still runs in `--print` (S1 review T2582), so the guard hook must stay installed. Keep only `ensure_shared` and `install` for that, if a hook-based guard is still wanted. Needs a decision. |
| `vendors/opencode` | 433 | accounts on that vendor | **Ask.** Is any account on it? If not: **D** (433 lines, plus its own meter and `usage_reading`, 93). Default drop. |

Net to port: about 2.6k Python lines of the 6.2k (status, load, burn costs, windows, record, poll, recover, trimmed vendor
base, claude identity), plus about 0.9k later (burn observe, measure, tokenlog, stats, outlook, prices). Dropped or unported:
about 2.7k (meter, screen helpers, ask/confirm, collector, replay, opencode, most of the Driver surface).

## External calls the port makes (and nothing else)

| Call | Python today | Go port |
|---|---|---|
| Usage endpoint | `urllib` GET `api.anthropic.com/api/oauth/usage` with a bearer read from the keychain | **none from the daemon.** A short CLI process, `get_usage` control request. |
| Token renewal | `claude auth status --json` per account config dir | the CLI process above renews on its own start; `claude auth status --json` stays for "is it logged in" (already in `reads/accounts.go`). |
| Dead token / reset ping | `fold.run_model`, one real turn | `hl fold` subprocess, as G7. |
| Token log | reads `<config>/projects/**/*.jsonl` (conversation logs only) | same files, same limits; or `result.model_usage`. |
| Opencode quota | `opencode auth list` and a key GET | dropped unless an account uses it. |
| Keychain | `security find-generic-password` | **not made.** |
| Database | `usage_samples`, `token_calls`, meta cursors, `events` | same tables (no schema step); status/burn json files as today. |

## Units

| Unit | Content | Go lines | Owner (suggested) | After |
|---|---|---|---|---|
| **U1 meter and poll** | `Reading`/`Window` writer, `usage.Record`, the `get_usage` meter over S1 (`structured.Open`, short-lived), poll scheduling (due rules, cursors, backoff, one success per tick), recover via `hl fold`, `collect_snapshots` replaced by frames | ~1.0k | contract (knows S1) | S1 (landed), live check of `get_usage` |
| **U2 status and pick** | `AccountStatus` with the fail-closed rules, `read`, `read_for_agent`, runway, `pick_account` and its reasons, `load` (active, caps, costs reader), the `UsagePort` for `lifecycle` | ~1.3k | go (wrote G6/S3, consumes the port) | none for the tables; U1 for fresh data |
| **U3 burn, measure, token log** | `burnrate.observe`/`refresh_long_ratios`, `measure`, `tokenlog`, `prices`, `usage_samples` writes | ~0.9k | writes | U2 |
| **U4 outlook and stats** | `outlook`, `stats`, `stats.outlook`, `cycle.position`/`set_cycle`, `account.set_cap` | ~0.8k | writes | U2, U3 for estimates |

Order: U1 and U2 in parallel (different owners, the seam is the status.json shape and the `usage.Record` function), then U3, then U4.

## The smallest cut that lets Python stop

Python can stop serving the tick, the send path and the reads when Go has: **U1** (fresh readings, otherwise every account goes
stale and the fail-closed rule refuses all work within hours), and **U2** (the pick and the status that `lifecycle` and the
send path ask). U3 and U4 can wait if three things hold, each to be checked as the port starts: (a) `describe` falls back to
the starting burn value, (b) the pick's weekly-window rule does not refuse when no long ratio exists, (c) the Stats screen may lack
`stats.outlook` and the estimated-cost columns for a while. If (b) fails, a fixed default ratio is the stopgap, not U3.
That cut is about **2.3k Go lines** (U1 + U2).

## Size and dates

Counting from the speed of G4 to G7 (about 1k lines and one review round per working day for one agent, tests included),
and assuming S3/S4 are on main:

| Piece | Working days | Depends on |
|---|---|---|
| Live check of `get_usage` | 0.5 | a human outside the sandbox, one test account |
| U1 | 2 to 3 | the live check; if it fails and the keychain read is not allowed, U1 needs a founder decision first |
| U2 | 3 to 4 | nothing; parallel to U1 |
| Smallest cut (U1+U2, in parallel) | **3 to 4 days** after the live check | |
| U3 | 2 | U2 |
| U4 | 1 to 2 | U2, U3 |
| Everything | 6 to 8 days | |

## Decisions needed

1. `get_usage` through the CLI instead of the keychain read: yes to build U1 on it, with the live check as the gate?
2. Is any account on the `opencode` vendor? If not it is dropped with its 0.5k lines.
3. Terminal-mode agents (frozen, no new porting): when Python stops, do they stop with it? Their screen reading, collector and ask/confirm are not ported, so the answer is yes unless the founder wants a Python daemon kept for them.
4. Does the PreToolUse guard hook stay installed for structured sessions? If yes, `claude_hooks` is kept in a trimmed form (about 0.15k).

## Note line

drops: the keychain credential read and the usage HTTPS call -> a `get_usage` control request to the CLI; screen-based vendor helpers, the statusLine-style collector, replay and (unless used) opencode -> structured session frames or nothing. straight: the status fail-closed rules, the pick, load, burn costs, windows and the record merge.
