# fake-daemon

A daemon that speaks the contract with scripted agents, for UI work and tests without a real daemon.
`node src/serve.ts` serves it on a Unix socket and a WebSocket; `HANDLINE_FAKE=1 vite` in `apps/web` runs it beside the dev server.

## Seeds

The first-load fixtures leave out some states a UI check needs. Switch them on by name; `all` switches on every one.
A name that does not exist is an error.

| Seed | What it adds |
|---|---|
| `inbox-terminal` | An inbox card for `handline.auditor` with `fix: open_terminal`. |
| `hr` | Three proposals: add `handline.new-builder` (team `handline`, model `sonnet`), a setting change `model: sonnet -> opus`, and a retire to reject. |
| `tasks-200` | 200 tasks, T6000 to T6199, in every state. |
| `chat-actions` | A message from `handline.architect` with OK / No actions on an open task the founder owns (T6902). |

Switch on:

- **Vite:** `HANDLINE_FAKE=1 HANDLINE_FAKE_VERBS=1 HANDLINE_FAKE_SEEDS=hr,tasks-200 vite` (the actions call task verbs, so add `HANDLINE_FAKE_VERBS=1`).
- **serve.ts:** `node src/serve.ts --seed all`, or `HANDLINE_FAKE_SEEDS=all`.
- **A test:** `applySeeds(daemon, 'chat-actions')` from `src/seeds.ts`, before the client connects.
