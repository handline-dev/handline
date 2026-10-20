# Gate watches and their vendors

A gate is a task owned by `gate` that the daemon holds. It watches something outside handline, and the daemon marks it done
when that changes. A task blocked by the gate then comes back to its holder's queue. `hl task wait` opens one in a single
write (`task.wait`): it opens the gate, blocks the task on it and, if the task is doing, releases it.

## What a watch is

A watch is `{vendor, target, until}`:

- **vendor** is the outside tool. This daemon has `github`.
- **target** is the thing, written the way that tool writes it. Only the vendor reads it.
- **until** is what the gate waits for, from one shared list: `ci` (the checks finished), `closed` (a PR merged or closed),
  `done` (an issue finished). Each vendor takes some of them. If none is given, the vendor's first is used.

A vendor answers each read with `waiting`, `passed`, `failed` or `gone`, plus one line of evidence. The gate's result is
`passed`, `failed` or `timed_out`, and its done evidence is the vendor's line.

## The gate (package `gates`)

Every 60 s the daemon reads each open gate, one at a time, on a clock of its own (not the tick):

- **passed or failed:** the gate is done, with the vendor's evidence.
- **gone** (deleted, or this login cannot see it): after 3 gone reads in a row the gate is failed, with evidence
  `gone: <why>`. Any other answer starts the count again.
- **A read that fails** (network, rate limit, logged out): the gate keeps watching, and its status says
  `watching <label> - last read failed: <why>` until a read works again.
- **Past the deadline** (its time box, 24 h by default): the gate is `timed out`, and its target is not read.

## What a vendor implements (package `watch`)

A vendor is one file, `go/internal/watch/<vendor>.go`, listed in `watch.Vendors`. It has four methods:

- `Untils()` lists the untils it takes, the default first.
- `Parse(target, until)` checks the target without reading the tool. It returns the target written in the vendor's one
  canonical form, and a label people read (`PR #418 CI`). An error refuses the watch as a bad request.
- `Ready(ctx)` says whether the vendor is set up, and why not. A watch whose vendor is not set up is refused. This is checked
  last, so every other refusal reads nothing outside.
- `Check(ctx, target, until)` reads the target once. An error means it could not be read; it is never a verdict.

No secrets go in the database, on the wire, or in `agent.yaml`. A vendor whose tool has a CLI with its own login uses that CLI.

A new vendor ships: its file, a test that feeds `Check` recorded tool output for each until and each failure case, and a
section below.

## GitHub (`github`)

| | |
|---|---|
| target | `owner/name#N`, e.g. `handline-dev/handline#418`. GitHub numbers PRs and issues together: `ci` and `closed` read `#N` as a PR, and `done` reads it as an issue. A number of the other kind reads as gone. `hl task wait T<n> --pr 418` is `github:<this checkout's origin>#418`. |
| until `ci` (PR) | `gh pr view N -R owner/name --json state,mergeCommit,statusCheckRollup,commits`. A check run is waiting until its status is `COMPLETED`, and ok when it concludes `SUCCESS`, `NEUTRAL` or `SKIPPED`. A commit status is waiting while `PENDING` or `EXPECTED`, and ok when `SUCCESS`. Once nothing waits: all ok is passed (`ci passed`); anything else is failed (`ci failed: <names>`). A PR that is merged or closed while a check still waits is failed (`closed before CI`). A PR with no checks is waiting until 10 minutes after its head commit, then passed (`no CI on this PR`). |
| until `closed` (PR) | `MERGED` is passed (`merged <sha>`). `CLOSED` is failed (`closed unmerged`). `OPEN` is waiting. |
| until `done` (issue) | `gh issue view N -R owner/name --json state,stateReason`. `CLOSED` as `COMPLETED` is passed (`closed as completed`). `CLOSED` for any other reason, such as `NOT_PLANNED`, is failed (`closed as not planned`). `OPEN` is waiting. |
| auth and setup | The gh CLI's own login: `gh auth login`. Ready is `gh auth status` exiting 0. handline never sees a token. |
| cost | One gh call (one GraphQL query) per gate per read: 60 an hour per gate, of the 5000 an hour GitHub allows. |
| failures | `Could not resolve to a ...` or HTTP 404: gone (a deleted target, or a private repo this login cannot see). Anything else (network, 5xx, rate limit, logged out) is a read that failed. |
