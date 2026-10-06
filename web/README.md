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
