# web: shared daemon client and fake daemon

`src/` is the one TypeScript client for the web app, Electron and mobile; `fake-daemon/` serves the
JSON-RPC contract of `docs/daemon-plan.md` from fixtures so the real daemon is a swap.

- `src/protocol.ts`: every wire name, hand-written from the plan; replaced by the types generated from
  `contract/` (plan task 1). Choices the plan leaves open are marked `ASSUMED`.
- `src/client.ts`: hello, errors, request ids, subscribe with cursors, session control, prompts, terminal
  views (text frames), reconnect that resumes without loss.
- `src/transport.ts`, `src/node-socket.ts`: WebSocket (browser, Electron, React Native) and line socket (Node).
- `fake-daemon/`: `node fake-daemon/serve.ts` serves a socket and a WebSocket with a scripted session per agent.

Test: `cd web && npm test` (Node 22.18+, no dependencies; TypeScript runs by type stripping, so no enums or
parameter properties).
