# @handline/electron

The desktop shell: one window on the built `apps/web`, one instance, a dock badge from the inbox count, native notifications.

    pnpm --filter @handline/web build && pnpm --filter @handline/electron start

The main process serves `apps/web/dist` on a loopback port and holds its own daemon connection (the Unix socket) for the badge and the notices. The web app is unchanged and reaches the daemon by its WebSocket.

| Env (`HANDLINE_` or `OCP_`) | Default |
|---|---|
| `SOCKET` | `<home>/daemon.sock`, home is `HANDLINE_HOME` or `~/.handline` |
| `WS` | the web app's own default (`ws://127.0.0.1:7777`) |
| `WEB_DIST` | `apps/web/dist` |

Not in: an HR proposal notice (the contract has no event for it), a notice's click opens the agent's workspace only (the card routes arrive with U9 and U10), a Windows badge (count only on Linux, none on Windows).
