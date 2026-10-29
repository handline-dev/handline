<!-- Generated from contract/spec/ by contract/generate.py; do not edit. -->
# accounts methods (protocol 1)

A method marked write takes a required `request_id`. Framing, roles, errors and gates are in [protocol.md](../protocol.md); field types in `protocol.ts`.

| Method | Role | Write | Params | Result | Errors | Gates |
|---|---|---|---|---|---|---|
| `accounts.list` | agent |  | `roster?`, `usage?` | `{accounts} & ReadCursor` | `bad_request` |  |
| `status.pick` | agent |  | none | `{picked, candidates, agent_costs}` |  |  |
| `account.set` | founder | yes | `account`, `label?`, `cap?`, `recover?`, `cycle?`, `cycle_end?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `account.wake` | founder | yes | `account`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` | state |
| `account.add` | founder | yes | `account`, `vendor?`, `request_id` | `Accepted & {account}` | `refused`, `bad_request` | role, state |
| `account.login` | founder | yes | `account`, `request_id` | `Accepted & {login_id, state, url}` | `refused`, `not_found` | role, state |
| `account.login_cancel` | founder | yes | `login_id`, `request_id` | `Accepted` | `refused`, `not_found` | role |
| `account.login_code` | founder | yes | `login_id`, `code`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` | role, state |
| `account.remove` | founder | yes | `account`, `dry_run?`, `confirm?`, `request_id` | `Accepted & {would_remove, plan}` | `refused`, `not_found`, `bad_request` |  |
| `stats.get` | member |  | `window` | `Stats` | `bad_request` |  |
| `stats.outlook` | member |  | `range`, `agent?` | `Outlook` | `bad_request` |  |

### `accounts.list`

Accounts with login state, use, cap, agents, recent moves.

### `status.pick`

Which account the next agent would be placed on, each account's reason, and what one more agent costs by kind.

### `account.set`

The account Settings form in one call: alias, cap, usage recovery and the month cycle; nothing is written if any is refused. Fields not given are left alone. Chosen over set_cycle_on/set_cycle_end methods because the UI saves these together and the CLI twins (cap, cycle, recover, alias) each write one of these fields.

### `account.wake`

Wake a STALLED account: its stall ends, agents may be placed on it again and the agents stalled with it are released, as session.wake clears a stalled agent (hl account wake). A login on the account, or a turn that ends well there, ends the stall too. Refused (gate state) when the account is not stalled.

### `account.add`

Register an account on this machine; it is not logged in until account.login runs. As `hl account add <name> --no-login`: the account's directory is made and linked to the shared files, and nothing is logged in, so accounts.list shows it with login_state needs_login and the accounts topic sends account.changed. A name already used is refused (gate `state`). A name that is empty, holds a slash, starts with a dot or would not be read back as text is bad_request, field `account`; a vendor the daemon has no driver for is bad_request, field `vendor`. Nothing is changed on a refusal. The app then calls account.login. Offering to trust the agents' worktrees on the new account is not part of it (settings.trust).

### `account.login`

Start a login on the account; answers the login id and the URL to open. No terminal is opened: the CLI runs without one, its URL is read from its stdout and its code is fed to its stdin by account.login_code. Refused with gate `state` while the account is already logging in; not_found for an unknown account. The end of a login is not a method: the accounts topic reports it in the account's `login`, state waiting, then code_sent after account.login_code, then done, or failed with a `reason`, or cancelled by account.login_cancel. A login that is done leaves login_state ok.

### `account.login_cancel`

Stop a login in progress. Idempotent: a login already ended answers accepted. An id the daemon never issued is not_found. The login's whole process group is killed, since the CLI ignores SIGTERM for a second.

### `account.login_code`

Hand the one-time code from the authorize page to a login waiting for it. The code is never echoed back, never in an event, a log or the request record. Refused with gate `state` when the login is not waiting for a code. An id the daemon never issued is not_found.

### `account.remove`

Log an account out and take it off the roster; dry_run names what would happen. The sequence of `hl account remove`: refused (gate `state`) while an agent is on the account or when claude's logout fails. The account's directory is moved aside, never deleted. A dry run writes nothing and records nothing.

### `stats.get`

Counts and sums over a window.

### `stats.outlook`

The week left of the accounts together, recorded and guessed, each account's runway, and what the agents cost per day.

