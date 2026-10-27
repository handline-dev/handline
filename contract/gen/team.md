<!-- Generated from contract/spec/ by contract/generate.py; do not edit. -->
# team methods (protocol 1)

A method marked write takes a required `request_id`. Framing, roles, errors and gates are in [protocol.md](../protocol.md); field types in `protocol.ts`.

| Method | Role | Write | Params | Result | Errors | Gates |
|---|---|---|---|---|---|---|
| `team.seal` | founder | yes | `dry_run?`, `request_id` | `{ok, plan, said}` | `refused`, `bad_request` |  |
| `hr.propose` | agent | yes | `kind`, `args`, `why?`, `request_id` | `Accepted & {proposal}` | `refused`, `bad_request` |  |
| `hr.withdraw` | agent | yes | `proposal`, `request_id` | `Accepted` | `refused`, `not_found` | proposal_settled |
| `trust.propose` | agent | yes | `root`, `why?`, `request_id` | `Accepted & {proposal}` | `refused` |  |
| `trust.list` | agent |  | `proposals?` | `{roots, proposals, declared}` |  |  |
| `trust.remove` | founder | yes | `root`, `request_id` | `Accepted` | `not_found` |  |
| `team.get` | agent |  | none | `{agents, edges}` |  |  |
| `hr.list` | agent |  | `state?` | `{proposals} & ReadCursor` | `bad_request` |  |
| `hr.decide` | member | yes | `proposal`, `decision`, `note?`, `subject?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` | proposal_changed, proposal_settled |
| `hr.apply_now` | member | yes | `proposal`, `request_id` | `Accepted` | `refused`, `not_found` | proposal_settled |
| `schedules.list` | agent |  | none | `{schedules, errors}` |  |  |
| `schedule.add` | founder | yes | `target`, `cron`, `message`, `request_id` | `Accepted & {schedule}` | `refused`, `bad_request`, `not_found` |  |
| `schedule.edit` | founder | yes | `schedule`, `target?`, `cron?`, `message?`, `request_id` | `Accepted` | `refused`, `not_found`, `bad_request` |  |
| `schedule.remove` | founder | yes | `schedule`, `request_id` | `Accepted` | `refused`, `not_found` |  |

### `team.seal`

The founder seals team.yaml and every agent.yaml as they are now (the Team page of the app); dry_run names what would be sealed. Seals are what lets the daemon tell a file the founder approved from one changed behind their back. The call without `dry_run` is the go-ahead and seals every file that is not sealed as it is, and marks the seals as begun; a file already matching its seal is left. A dry run lists each file it would take on, `new` (no seal before) or `changed` (it did not match its seal), with the digest it would be sealed with, and writes nothing and records nothing. Nothing to seal is `ok` true with that said. An agent's session is refused by role.

### `hr.propose`

Propose a roster change; the founder decides (hl hr add, retire, rename, link, unlink, team, settings, context, schedule, and diff, which is kind bundle: args.changes lists several changes decided once and applied together or not at all).

### `hr.withdraw`

Take back an undecided proposal: its author's own, or any for the founder; another agent is refused (gate role).

### `trust.propose`

Propose declaring a folder ocp may answer Claude's trust prompt in (hl trust add); the founder's own call declares it. Called by an agent it raises a decision for the founder, `why` required, and answers the proposal's id; called by the founder it declares the root directly, `why` optional (default `added by the founder`), and answers no proposal. A root already inside a declared one, a missing why or a decision that could not be raised is refused with the store's words. A paired device is refused, gate role. `root` must be an absolute path (bad_request, field `root`): the daemon would read a relative one, or `~`, against its own folder and home, so the client resolves it against its own.

### `trust.list`

Declared trust roots, and with proposals the proposals (hl trust list, hl trust proposals).

### `trust.remove`

Drop a declared root (hl trust remove); needs no approval. The founder's: the declared roots live in the protected store, which an agent session cannot write, so an agent calling `hl trust remove` is refused today and by this method (gate role). A root that is not declared is not_found. `root` must be an absolute path, as for trust.propose (bad_request, field `root`).

### `team.get`

Agents, roles, allowed edges, role and base file metadata.

### `hr.list`

HR proposals with their diff: what waits for the founder, and what settled recently. Without `state`: every proposal that waits (open, applying, and apply_failed until it is decided again) and every settled one (approved, rejected, withdrawn) from the last 7 days, at most 50 of those. Order: the waiting ones first, oldest `created` first; then the settled ones, newest `settled` first. A client counts the dot from `open` and `apply_failed`, never from `applying`. With `state`, only that state, in the same order, and no 7-day or 50 bound. A proposal whose requested_by is human (the founder's own change) is listed as any other, so a held one shows while it waits. The Python store's states map as: proposed = open; approved = apply_failed (it holds the founder's approval and the change is not applied: `error` is set when an apply ran and failed, null when it never ran, e.g. a crash between the two); applied = approved; rejected = rejected; withdrawn = withdrawn; enacting and failed are never listed.

### `hr.decide`

Approve or reject a proposal; approving an apply_failed one applies it again. Approving a change to an agent with a session running is accepted, not refused: the proposal turns applying, `said` names the agents whose turn it waits on, and the change is applied when those turns end. Refused with gate proposal_changed or proposal_settled, carrying the proposal as it is; an applying proposal is refused proposal_settled.

### `hr.apply_now`

Apply a waiting HR change now: for an applying proposal, interrupt the turn of each agent in `waiting_on` (as session.interrupt does), then close the session, apply the change and restart the renamed agent at the next tick, without waiting for the turn to end. Returns at once with `said` naming the agents; the proposal stays applying until the change is applied (watch the hr topic). Refused with gate proposal_settled, carrying the proposal as it is, unless the proposal is applying. Works for every kind a running session can hold: rename, retire, context, link, settings.

### `schedules.list`

Schedules. An agent whose `schedules` value is bad is listed in `errors` (target and the error text), not shown as an agent with no schedules.

### `schedule.add`

Add a schedule.

### `schedule.edit`

Edit a schedule.

### `schedule.remove`

Remove a schedule.

