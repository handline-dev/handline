# web: shared daemon client, fake daemon and the PC web UI

`src/` is the one TypeScript client for the web app, Electron and mobile; `fake-daemon/` serves the
JSON-RPC contract of `contract/protocol.json` from fixtures so the real daemon is a swap.

- `src/protocol.ts`: re-exports the generated `contract/protocol.ts`; nothing is hand-written.
- `src/client.ts`: hello, errors, request ids, subscribe with cursors, session control, prompts, terminal
  views (text frames), reconnect that resumes without loss.
- `src/transport.ts`, `src/node-socket.ts`: WebSocket (browser, Electron, React Native) and line socket (Node).
- `fake-daemon/`: `node fake-daemon/serve.ts` serves a socket and a WebSocket with a scripted session per agent.

The fake serves the session screen's methods (hello, subscribe, agents, `session.*`, `prompt.*`, `input.*`, `terminal.*`);
any other method is refused as `unsupported`, and `capabilities.chat` is false until it serves `chat.*`.

Test: `cd web && npm test` (Node 22.18+, no dependencies; TypeScript runs by type stripping, so no enums or
parameter properties). Type check: `tsc -p web` (strict, `erasableSyntaxOnly`; TypeScript and `@types/node` are not vendored).

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

- Run: `cd web && npm run ui`, then open http://127.0.0.1:8080/ (starts the fake daemon on ws 7777). Against another daemon:
  `node serve-ui.ts --port 8080`, then `/ui/?daemon=ws://host:port`.
- `serve-ui.ts` type-strips `.ts` per request, so the browser loads the same files the tests run; Electron can load the same URL.
- `ui/model.ts` (what the screens decide) and `ui/store.ts` (agents, detail, events) have no DOM and are tested in Node.
- Type check: `tsc -p ui`.
- To type in the terminal, press Take control first (the daemon stops typing there until Release); key chips take it for you.
- Not built yet: Inbox, Tasks, Team, Accounts and the other screens (their top-bar buttons are disabled); chat (needs `chat.*`
  from the daemon); a shell pane (needs shell terminals).
