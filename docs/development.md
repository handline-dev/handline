# Development

```bash
tools/test.sh     # the contract's generated files current, then the Go suite (tools/test-go.sh); no network, no tokens
tools/test.sh ./internal/reads -run X   # go test's own arguments: one package, one test
tools/check.sh main   # what a change needs: nothing for docs, the app checks, the contract and the Go suite otherwise
HANDLINE_GO_RACE=1 tools/test-go.sh   # the Go suite with go test -race: the auditor's run on a sha for a Go change, nobody else's
```

`HANDLINE_LIVE=1 tools/live-e2e.sh` drives one real haiku agent through a test daemon on a scratch home and prints how
fast the event stream showed each step. It spends tokens on the account `tools/live_account.py` picks (the most headroom
now), so it runs only by hand, by any agent, and never from `tools/test.sh` or `check.sh`. Name the account in `HANDLINE_LIVE_ACCOUNT`.

`tools/prune_worktrees.py` lists the worktrees that are merged into main, clean and no agent's; `--remove` removes them.

## React Compiler

The web and phone apps build with the React Compiler, and lint holds the Rules of React it relies on (`packages/config/eslint/react.js`, the
`react-hooks` compiler rules as errors). They are rules of React, so they hold with or without the compiler.

Do:

- Read changing values from a hook's return.
- Derive during render instead of setting state in an effect.
- Keep refs for event handlers and effects.

Do not:

- Mutate props, state or a hook's return during render.
- Read or write `ref.current` in render.
- Call `Date.now()` or `Math.random()` in render (take the time from `useNow`).
- Define a component inside a component.
- Disable a `react-hooks` rule. `'use no memo'` only with a comment saying why.

The debt is the `compilerDebt` block in `apps/web/eslint.config.js` and `apps/mobile/eslint.config.js`: files that still break a rule sit at
`warn` there. The list only shrinks; a file that is not in it gets the error.

Design notes are in `design/` and `docs/`.

Working on the desktop app itself — the page with hot reload, a dev Electron
instance next to the everyday one, pairing — is in
[app-dev-loop](app-dev-loop.md); running it for real (phones, Tailscale) in
[run-on-real-daemon](run-on-real-daemon.md).
