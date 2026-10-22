# The Go CI jobs

`.github/workflows/check.yml` runs on GitHub's `ubuntu-latest` runners, free for a public repository. No job needs macOS
or a machine of ours.

- `apps`: the web, mobile and electron tests, typecheck and lint.
- `daemon (<shard>)`: the Go suite, one job per shard of packages in parallel: `compose` (the long pole alone), `cli`,
  `daemon`, `terminal`, and `other`, which is every package no named shard lists. `tools/go-shard.sh` holds the lists.
- `contract`: what is not a package's test: `go.mod` tidy, no new copy of Go code, the build, the conformance fixtures
  (`tools/test-go.sh --gates`) and the generated contract files.

Tools: Go comes from `actions/setup-go` (the version in `go/go.mod`), which restores a module and build cache keyed by
`go/go.sum`. The Go jobs install `tmux` with apt, for the terminal tests; `git` and `python3` are on the image.

Leftovers: each Go job runs under `tools/leakcheck.py run --dir <runner temp>/hlc`, which fails it when it leaves a process,
tmux server or temp file behind.

A pull request from a fork runs the jobs too: a hosted runner is not the founder's machine.

# Pull request checks

The checks main requires, and who makes each:

| Check | Made by | Green when |
|---|---|---|
| `pre-audit` | `check.yml` | the apps, the Go suite and `files` passed. `files` (`tools/pr_checks.py files`): no binary or image outside `apps/mobile/shots/` and the apps' asset folders, no scratch file, no comment block over 6 lines with a line the change adds or edits, and Prettier on the files the change touches. An auditor starts only on a green `pre-audit`. |
| `pr-rules` | `pr-rules.yml` | the body has `## Why`, `## What changed`, `## Evidence` and `## Risk` with text; an image when `apps/web/src` or `apps/mobile/src` changes; no Korean and no message ids; the base is main or `story/*`. It reruns when the body is edited. |
| `audit` | the auditor | the auditor set it on the PR's head after a PASS. A new push has no `audit`, so the PR waits for a fresh PASS. |

The auditor's PASS, on the head it audited:

    gh api repos/handline-dev/handline/statuses/<head sha> -f state=success -f context=audit -f description="PASS T<n>"

and a FAIL with `-f state=failure`. One lander agent merges PRs one at a time. A PR behind main goes back to its builder to update, and the new head needs green checks and a fresh `audit` before it lands.
