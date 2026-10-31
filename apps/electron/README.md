# @handline/electron

The desktop shell: one window on the built `apps/web`, one instance, a dock badge from the inbox count, native notifications.

    pnpm dev

The main process serves `apps/web/dist` on a loopback port and holds its own daemon connection (the Unix socket) for the badge and the notices. The web app is unchanged and reaches the daemon by its WebSocket.

| Env (`HANDLINE_` or `OCP_`) | Default                                                        |
| --------------------------- | -------------------------------------------------------------- |
| `SOCKET`                    | `<home>/daemon.sock`, home is `HANDLINE_HOME` or `~/.handline` |
| `WS`                        | the fake daemon (`ws://127.0.0.1:7777`)                        |
| `WEB_DIST`                  | `apps/web/dist`                                                |

## Two modes

The app has two modes, shown in the tray (_Mode_, _Daemon: address · version_) and switched there (the app relaunches).

- **Connect** (the default) never starts or stops a daemon. It attaches to the daemon already running on this computer (it pairs itself through the gateway's local socket), or to another device's gateway paired with _Pair with the link on the clipboard_ (copy the `handline://` link `hl device pair` prints; a Tailscale `*.ts.net` gateway needs no certificate, any other is trusted by the fingerprint in the link). With nothing running the tray says so; start a daemon and relaunch, or switch to Own.
- **Own daemon** is what the next section describes: the app starts the daemon and gateway and stops them on quit.

The choice and the remote pairing (it holds a device token) are kept in `settings.json` in the app's data folder, mode 0600. `HANDLINE_MODE=connect|own` overrides the kept mode for one run without saving it.

## On the real daemon

This section is Own-daemon mode (`HANDLINE_MODE=own`, or switch in the tray).

With the Go gateway running (`127.0.0.1:7443`), from the repo root:

    pnpm install && pnpm dev

It builds `apps/web` and starts the app (both rebuild on every save; reload the window with Cmd+R for the page), which owns its daemon: it puts a tray icon up at once (open window, daemon status, start daemon, quit), starts the daemon and the gateway as its own child processes when their sockets do not answer (both `go/handlined` of the checkout, the daemon with the launchd service's flags; `HANDLINE_HANDLINED` names another binary and `HANDLINE_CLAUDE` the claude the daemon runs; build the gateway with `go build -o handlined ./cmd/handlined` in `go/`), and stops what it started when it quits. Closing the window leaves it running in the tray. It pairs itself: the app is the founder's own process, so it reads the founder key from the protected store, asks the gateway's local socket for a code and trades it for a device token, in the main process only. The key is not logged, stored or sent to the page; the token is kept in the app's data folder (`device.json`, mode 0600) and put in the page's storage by the preload. A token the gateway no longer lists is replaced at the next launch. There is no pairing step and no `hl device pair`; the pairing screen shows only if the app could not pair. The gateway listens on `127.0.0.1:7443` (`HANDLINE_GATEWAY=host:port` moves it); the page is served on `127.0.0.1:17443` (`HANDLINE_WEB_PORT`) because the token is kept per page origin, and the shell sets the page's `Origin` to the gateway's own for that one socket. The daemon's output goes to `daemon.log` in the app's data folder. The badge and notices still use the daemon's Unix socket.

A device the gateway no longer knows (revoked, or a token from an older pairing) is dropped at the next launch and the window asks for a code again.

Not in: an HR proposal notice (the contract has no event for it), a notice's click takes the page to the route of the event's target through `window.handline.onNavigate` (the card routes arrive with U9 and U10), a Windows badge (count only on Linux, none on Windows).

The page's side is `window.handline` (docs/electron-bridge.md): `screen` (which agent is on screen, so no notice for it), `unsent` (whether a restart would lose something) and `onNavigate`. `src/preload.ts` is bundled to `dist/preload.cjs`; every call is checked for its sender and its arguments in `src/bridge.ts`. Until the page has called `unsent` once, the shell probes the page itself.

## Packaging

    pnpm install && pnpm package [version]

Makes `apps/electron/release/Handline-<version>-universal.dmg` and `-mac.zip`: the web build, the main process, the universal `handlined` and `hl` from `tools/build-darwin.sh`, then electron-builder (`electron-builder.config.mjs`). The app carries the web build and both binaries under `Contents/Resources` (`src/bundle.ts`); an installed app runs that daemon and serves that page, unless `HANDLINE_HANDLINED` or `HANDLINE_WEB_DIST` name others. Settings > Connection offers to link the carried `hl` into `~/.local/bin`.

Without a Developer ID the app is signed ad hoc (`ad-hoc-sign.mjs`): it runs on the Mac that built it, and anywhere else the first open needs right-click > Open. To sign with a Developer ID, set `HANDLINE_SIGN_IDENTITY` to its name before `pnpm package` (hardened runtime and `assets/entitlements.mac.plist` then apply). Notarization is off (`notarize: false`) until there is an Apple account.

## Following main

    pnpm install && pnpm app

On a Mac `pnpm start` and `pnpm app` run a copy of Electron.app named Handline (`~/.local/share/handline/Handline-<electron version>.app`, `HANDLINE_BUNDLE_DIR` moves them; one per Electron version, made on the first run), so the menu bar and the dock say Handline; if the copy cannot be made they run plain Electron and warn. The executable inside keeps the name Electron, because Electron takes a renamed one for a packaged app. "Restart now" starts the app again through the same launcher, so a new Electron version gets its own copy.

`pnpm app:install` writes `/Applications/Handline.app` (once; run it again to replace it): a thin launcher with the Handline icon. Opening it from Applications or the Dock does what `pnpm app` does with no terminal: it takes the login shell's PATH, brings `~/.local/share/handline/app-main` up to main and starts the app from it, then ends. A second open brings the running window to the front. What it says goes to `~/.local/share/handline/launcher.log`. It needs git, node, pnpm and go, and the `app-main` checkout (run `pnpm app` once first).

Run it from the checkout of main, once. It makes the app's own clone of this repo, at GitHub's main (this repo's `origin/main`, fetched first), in `~/.local/share/handline/app-main` (`HANDLINE_FOLLOW_DIR` moves it), installs, builds the page and the gateway and starts the app from there, so no working tree is touched. Start it the same way every time; it brings the clone up to main first.

The app follows GitHub's main: it watches `origin/main` of the repo it was cloned from (any fetch there moves it; the repo's own `main` branch is not used, PRs merge on GitHub) and fetches it when the window comes to the front, at most once a minute. When main moves, a popover at the bottom right of the window names the new commit: **Restart now** brings the clone up to that main, rebuilds only what changed (the page; the app itself and a reinstall when `apps/electron`, the client packages or the lockfile changed; the gateway when `go/` changed) and relaunches on the same page, with the device token kept; **Later** asks again at the next move. The tray shows the commit the app is on and offers _Restart to update_ or _Check for update_.

With Tailscale up, the gateway listens on the Mac's Tailscale address (`HANDLINE_GATEWAY` overrides) and advertises its `*.ts.net` name, so a phone's pairing link carries the name; without it, loopback only. The window reaches the gateway at the same address, which is chosen when the app starts, and the shell tells the page that one address is its own machine's, so plain ws to it is allowed (no other tailnet address is). If the gateway does not answer there and the app started it, the app restarts it on loopback; a gateway the app did not start is left alone and the tray says the gateway does not answer.

An agent's run stays in the background: it has no dock or menu bar icon, no update dialog, never takes focus, keeps its window hidden, shows no native notices and does not bring the everyday app forward. An agent is known by `OCP_AGENT` in its shell, and `pnpm dev:team` passes the choice on. `HANDLINE_BACKGROUND=1` asks for it and `HANDLINE_BACKGROUND=0` turns it off. The hidden page keeps drawing, so screenshots go through the DevTools port (`Page.captureScreenshot`), never a screen capture.

`HANDLINE_DEBUG_PORT=9333` opens the DevTools port on localhost for run checks. Any local process can then read the page, its device token included, so use it for checks only, never for the app you use every day.

## Trying a branch next to the installed app

    tools/try-prototype.sh [branch]

Runs a branch (default `story/prototype-apply`) as a second app on the live daemon with the same data. It keeps its own clone (`~/.local/share/handline/try-prototype`), its own app data (`~/.local/share/handline/try-prototype-data`: settings, device token, instance lock) and its own page port (`HANDLINE_WEB_PORT`, default 17445), so the installed app is not touched and quitting this one leaves the daemon running. Run it again to bring the clone to the branch's head.
