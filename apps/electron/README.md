# @handline/electron

The desktop shell: one window on the built `apps/web`, one instance, a dock badge from the inbox count, native notifications.

    pnpm --filter @handline/web build && pnpm --filter @handline/electron start

The main process serves `apps/web/dist` on a loopback port and holds its own daemon connection (the Unix socket) for the badge and the notices. The web app is unchanged and reaches the daemon by its WebSocket.

| Env (`HANDLINE_` or `OCP_`) | Default |
|---|---|
| `SOCKET` | `<home>/daemon.sock`, home is `HANDLINE_HOME` or `~/.handline` |
| `WS` | the fake daemon (`ws://127.0.0.1:7777`) |
| `WEB_DIST` | `apps/web/dist` |

## On the real daemon

With the Go gateway running (`127.0.0.1:7443`), from the repo root:

    pnpm install && pnpm app

It builds `apps/web` and starts the app, which owns its daemon: it puts a tray icon up at once (open window, daemon status, start daemon, quit), starts the Python daemon and the Go gateway as its own child processes when their sockets do not answer (`bin/ocp daemon` and `go/handlined` of the checkout; `HANDLINE_OCP` and `HANDLINED` name other binaries; build the gateway with `go build -o handlined ./cmd/handlined` in `go/`), and stops what it started when it quits. Closing the window leaves it running in the tray. It pairs itself: the app is the founder's own process, so it reads the founder key from the protected store, asks the gateway's local socket for a code and trades it for a device token, in the main process only. The key is not logged, stored or sent to the page; the token is kept in the app's data folder (`device.json`, mode 0600) and put in the page's storage by the preload. A token the gateway no longer lists is replaced at the next launch. There is no pairing step and no `hl device pair`; the pairing screen shows only if the app could not pair. The gateway listens on `127.0.0.1:7443` (`HANDLINE_GATEWAY=host:port` moves it); the page is served on `127.0.0.1:17443` (`HANDLINE_WEB_PORT`) because the token is kept per page origin, and the shell sets the page's `Origin` to the gateway's own for that one socket. The daemon's output goes to `daemon.log` in the app's data folder. The badge and notices still use the daemon's Unix socket.

A device the gateway no longer knows (revoked, or a token from an older pairing) is dropped at the next launch and the window asks for a code again.

Not in: an HR proposal notice (the contract has no event for it), a notice's click takes the page to the route of the event's target through `window.handline.onNavigate` (the card routes arrive with U9 and U10), a Windows badge (count only on Linux, none on Windows).

The page's side is `window.handline` (docs/electron-bridge.md): `screen` (which agent is on screen, so no notice for it), `unsent` (whether a restart would lose something) and `onNavigate`. `src/preload.ts` is bundled to `dist/preload.cjs`; every call is checked for its sender and its arguments in `src/bridge.ts`. Until the page has called `unsent` once, the shell probes the page itself.
