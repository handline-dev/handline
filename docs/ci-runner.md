# The Go CI job

The `go` job of `.github/workflows/check.yml` runs on GitHub's `ubuntu-latest` runners, as the `apps` job does. They
are free for a public repository. It replaced a self-hosted runner on the founder's server. No Go test needs macOS.

- Tools: Go comes from `actions/setup-go` (the version in `go/go.mod`). The job installs `tmux` with apt, for the
  terminal tests; `git` and `python3` (the conformance harness) are on the image.
- Caches: `actions/setup-go` restores its module and build cache, keyed by `go/go.sum`.
- Leftovers: the suite runs under `tools/leakcheck.py run --dir <runner temp>/hlc`, which fails the job when it leaves a
  process, tmux server or temp file behind. `tools/test-go.sh` keeps its tidy and dupl gates.
- Forks: a hosted runner is not the founder's machine, so a pull request from a fork runs the job too.
