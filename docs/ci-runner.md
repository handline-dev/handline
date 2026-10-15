# The Go CI runner

The `go` job of `.github/workflows/check.yml` runs on a self-hosted runner on the founder's server (2 cores, 3 GB of
memory), named `handline-ci-1`, registered for the handline-dev organization and labelled `handline-ci`; the `apps` job
stays on GitHub's runners. Its Go build and test caches stay on the server's disk between runs, so `go test ./...` reruns
only the packages a change touched and those that import them.

## The registration token

GitHub, the handline-dev organization's Settings > Actions > Runners > New runner. Type it on the server in step 3 only.
It never goes in the repository, a script default, a shell history line that is kept, or a log.

## Setup (as root, once)

1. Packages: `apt-get install -y git tmux python3 curl` (Python 3.12 or newer, for the parity tests). The job installs Go
   itself (`actions/setup-go`).
2. A user with no sudo, and its directories:
   `useradd -m -s /bin/bash ci && sudo -u ci mkdir -p ~ci/runner`
3. The runner, as `ci` in `~ci/runner`: download the Linux x64 package the GitHub page names and unpack it, then
   `./config.sh --url https://github.com/handline-dev --labels handline-ci --name handline-ci-1`, and paste the token
   at its prompt (not as `--token`, which would land in the shell history). `self-hosted`, `Linux` and `X64` are added by
   the runner.
4. The service, as root in `~ci/runner`: `./svc.sh install ci && ./svc.sh start` (a systemd unit that starts on boot).

## Where things go, and what cleans them

- Checkout: `~ci/runner/_work/handline`, reused; `actions/checkout` removes untracked files each run.
- Test temp files: the job sets `TMPDIR` to the runner's temp directory, which the runner empties after every job, and
  it kills the processes a job leaves behind (tmux servers, daemons) when the job ends.
- Go caches: `~ci/.cache/go-build` and `~ci/go/pkg/mod`. `tools/go-env.sh` already drops build cache files
  no build used for two days, once a day. The hard cap is 10 GB, a root cron line:
  `0 4 * * * [ "$(du -sm ~ci/.cache/go-build | cut -f1)" -gt 10240 ] && sudo -u ci rm -rf ~ci/.cache/go-build`

## Memory

With 2 cores and 3 GB the job runs `go test` with `-p=1` (one test binary at a time); GOMAXPROCS stays at the core
count. One runner runs one job at a time; others wait in its queue.

## Until the runner exists

The `go` job waits for a runner and the pull request does not go green. The `apps` job runs on GitHub's runners regardless.
