# web: shared daemon client, fake daemon and the PC web UI

The client and the fake daemon now live in `packages/` (see `docs/turborepo-layout.md`); this folder keeps the PC UI, the phone app and their tests.

- `packages/client` (`@handline/client`): the one TypeScript client for the web app, Electron and mobile: hello, errors, request ids,
  subscribe with cursors, session control, prompts, terminal views, reconnect, and the WebSocket transport. `protocol.ts` re-exports `contract/protocol.ts` (generated).
- `packages/client-node` (`@handline/client-node`): the daemon's Unix socket as a Transport.
- `packages/fake-daemon` (`@handline/fake-daemon`): serves `contract/protocol.json` from fixtures; `pnpm --filter @handline/fake-daemon serve`
  runs a socket and a WebSocket with a scripted session per agent. Its tests run the client against it.
- The browser has no build step, so `ui/index.html` and `mobile/index.html` carry an import map for `@handline/client` and `@handline/contract`.

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

`ui/` is the PC app: frame (top bar, agent rail, tabs, context panel, status bar) and the agent page (header controls,
permission card, empty chat, a dockable live text-grid terminal pane with key chips). It uses `src/` as is and the types of
`contract/protocol.ts`; no framework and no build step.

- Run: `cd web && npm run ui`, then open http://127.0.0.1:8080/ (starts the fake daemon on ws 7777). TLS is the rule: add
  `--cert FILE --key FILE` for https (and wss for the fake daemon); without a cert only loopback is served, plain, for development.
  The page connects to its own host on 7777, `wss` on an https page; another daemon: `/ui/?daemon=wss://host:port`
  (plain `ws://` is refused unless the daemon is on this machine).
- `serve-ui.ts` type-strips `.ts` per request, so the browser loads the same files the tests run; Electron can load the same URL.
- `ui/model.ts` (what the screens decide) and `ui/store.ts` (agents, detail, events) have no DOM and are tested in Node.
- Type check: `tsc -p ui`.
- Typing goes through a focused hidden textarea (input and composition events), so IME text and paste work; Backspace, Delete,
  Left, Right, Home and End are mapped but say "the daemon cannot take it yet" until `session.keys` names them (`DAEMON_KEYS` in `ui/model.ts`).
- To type in the terminal, press Take control first (the daemon stops typing there until Release); key chips take it for you.
- Not built yet: Inbox, Tasks, Team, Accounts and the other screens (their top-bar buttons are disabled); chat (needs `chat.*`
  from the daemon); a shell pane (needs shell terminals).
