# web: shared daemon client, fake daemon and the PC web UI

The client and the fake daemon now live in `packages/` (see `docs/turborepo-layout.md`); this folder keeps the PC UI, the phone app and their tests.

- `packages/client` (`@handline/client`): the one TypeScript client for the web app, Electron and mobile: hello, errors, request ids,
  subscribe with cursors, session control, prompts, terminal views, reconnect, and the WebSocket transport. `protocol.ts` re-exports `contract/protocol.ts` (generated).
- `packages/client-node` (`@handline/client-node`): the daemon's Unix socket as a Transport.
- `packages/fake-daemon` (`@handline/fake-daemon`): serves `contract/protocol.json` from fixtures; `pnpm --filter @handline/fake-daemon serve`
  runs a socket and a WebSocket with a scripted session per agent. Its tests run the client against it.
- The browser has no build step, so `mobile/index.html` carries an import map for `@handline/client` and `@handline/contract`.

The fake also plays the paired-device listener (`acceptNetwork`, wss on `/rpc`): unpaired connections get the one `unpaired` refusal, `device.pair` and `device.redeem` work, and a token's hello makes a member. Not modelled: attempt limits, code expiry, `devices.list`, `device.revoke`, so fixtures that need them are not run against it (`serve.ts --network-socket PATH` serves the unpaired socket for `contract/conformance.py --as unpaired=PATH`).

The fake serves the session screen's methods (hello, subscribe, agents, `session.*`, `prompt.*`, `input.*`, `terminal.*`);
any other method is refused as `unsupported`, and `capabilities.chat` is false until it serves `chat.*`.

Test: `pnpm install && pnpm turbo test` from the repo root (Node 22.18+; TypeScript runs by type stripping, so no enums or
parameter properties). Type check the packages: `pnpm turbo typecheck`.

## mobile/: the phone app (PWA)

A dependency-free PWA on the shared client, chosen over React Native/Expo because the client's WebSocket transport
already runs in a phone browser unchanged, there is no app store or signing for a LAN/Tailscale-only tool, and it installs
from the browser ("Add to Home Screen"); native push can come later as a thin wrapper if the PWA's limits bite.
There is no build step: `mobile/server.ts` strips types per request with Node's own stripper.

- `store.ts`: agent list and agent page state, kept current from events; actions return the daemon's own refusal sentence.
- `views.ts`: pure state-to-HTML. `app.ts`: browser wiring. `server.ts`: static server for the page, the client and the contract types.
- Built: Agents list; agent page with Chat/Terminal tabs, Start/Resume/Interrupt/Exit/Close/Kill, terminal text grid with
  typed input and the key strip, permission card (Yes/No) on both tabs. Chat shows its empty state until the daemon serves `chat.*`.
  The other tab-bar destinations are present but disabled.

Run: `npm run fake-daemon` and, in another shell, `npm run mobile -- --cert FILE --key FILE`; open `https://<this-machine>:8443/`
on the phone. TLS is required for a phone to install the PWA and for the page to open `wss://`; the page derives `wss`
from its own scheme (`?daemon=wss://<host>:7777` points at another daemon). Without `--cert` the server serves plain
`http://127.0.0.1:8080` for development only, against the fake daemon (plain `ws`, started with `--host` to leave localhost).
Test: `npm test`.

Not yet built: Inbox, Tasks, Accounts and More; take/release input; the landscape fullscreen terminal and terminal resize
for the phone (design 7.4, 4.2); pair device; a real icon (the manifest's are placeholders).

## PC web UI

Gone: `apps/web` replaced the no-build `ui/` page and its server.
