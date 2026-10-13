# Team dev instances

Each team (`handline.daemon`, `handline.pc`, `handline.mobile`) can run its own
daemon from its own branch, beside the live one. An instance lives in
`~/.handline-dev/<team>/` with its own database, socket, protected store,
gateway port (7451, 7452 and 7453) and launchd jobs. It never starts, wakes or
types into an agent session.

## Turning it on and off

Run these from a plain shell, with neither `HANDLINE_HOME` nor `OCP_HOME` set.
`hl dev on` snapshots whatever home `hl` points at, so with `HANDLINE_HOME` at
another team's dev home it copies that one instead of the live home.

    hl dev on <team> [--worktree PATH] [--listen ADDR] [--fresh]
    hl dev status
    hl dev rebuild <team>
    hl dev off <team>

- `on` builds `handlined` from the worktree (default: the team manager's) and
  starts the daemon and its gateway. The first `on` copies the live database
  read-only; later ones keep the instance's own copy unless `--fresh` is given.
  It prints `instance.json`.
- `rebuild` builds the worktree again and restarts both jobs onto the new binary.
- `off` stops both jobs. The folder stays, so the next `on` picks up where it left off.
- `on` refuses a home that resolves to the live one, and a gateway port in use.

From an agent session only the team's manager may change its instance. The
check reads `OCP_AGENT`, so it is advisory, not a lock.

## Using it

- **hl:** `env -u OCP_HOME HANDLINE_HOME=~/.handline-dev/<team> hl ...` talks to
  the instance. With `OCP_HOME` still naming the live home, hl refuses.
- **Desktop app:** `pnpm dev:team <team>` runs the vite page and the shell
  against the instance, with an app data folder of its own
  (`<instance>/app-data`). When the instance is off it says so and stops; it
  never falls back to the live daemon. See [app-dev-loop](app-dev-loop.md).
- **Phone:** the gateway listens on `127.0.0.1` by default, which a phone cannot
  reach. Start it on the Tailscale address with
  `hl dev on handline.mobile --listen :7453`, then pair with
  `env -u OCP_HOME HANDLINE_HOME=~/.handline-dev/handline.mobile hl device pair`.
  The link names the dev gateway, so the phone pairs with that instance only.
