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
