# Development

```bash
tools/test.sh     # the contract's generated files current, then the Go suite (tools/test-go.sh); no network, no tokens
tools/test.sh ./internal/reads -run X   # go test's own arguments: one package, one test
tools/check.sh main   # what a change needs: nothing for docs, the app checks, the contract and the Go suite otherwise
HANDLINE_GO_RACE=1 tools/check.sh main   # the same with go test -race: the auditor's run on a sha, nobody else's
```

`HANDLINE_LIVE=1 tools/live-e2e.sh` drives one real haiku agent through a test daemon on a scratch home and prints how
fast the event stream showed each step. It spends tokens on the account `tools/live_account.py` picks (the most headroom
now), so it runs only by hand, by any agent, and never from `tools/test.sh` or `check.sh`. Name the account in `HANDLINE_LIVE_ACCOUNT`.

`tools/prune_worktrees.py` lists the worktrees that are merged into main, clean and no agent's; `--remove` removes them.

Design notes are in `design/` and `docs/`.

Working on the desktop app itself — the page with hot reload, a dev Electron
instance next to the everyday one, pairing — is in
[app-dev-loop](app-dev-loop.md); running it for real (phones, Tailscale) in
[run-on-real-daemon](run-on-real-daemon.md).
