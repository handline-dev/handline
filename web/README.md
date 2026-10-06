# web: shared daemon client and fake daemon

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

Run: `npm run fake-daemon` and, in another shell, `npm run mobile`; open `http://<this-machine>:8080/` on the phone
(`?daemon=ws://<host>:7777` to point at another daemon; the fake daemon listens on 127.0.0.1 unless started with `--host`).
Test: `npm test`.
