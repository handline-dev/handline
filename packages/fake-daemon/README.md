# fake-daemon

A daemon that speaks the contract with scripted agents, for UI work and tests without a real daemon.
`node src/serve.ts` serves it on a Unix socket and a WebSocket; `HANDLINE_FAKE=1 vite` in `apps/web` runs it beside the dev server.

## Pairing a phone dev build

`node src/serve.ts --phone --ws 7791 --socket /tmp/x.sock` serves the WebSocket as the paired-device listener and prints a one-time `handline://pair?...&plain=1` link.
A dev build of the phone app takes it, and a release build ignores it. iOS simulator: `xcrun simctl launch <udid> dev.handline.mobile -handlineDevPair '<link>'`
(`simctl openurl` makes iOS ask "Open in Handline?", which needs a tap). Android emulator: `adb reverse tcp:7791 tcp:7791`, then `adb shell am start -a android.intent.action.VIEW -d '<link>'`.
The pairing is kept, so later launches need no link. Run Metro without `CI=1`, or it never rebuilds the bundle. Keep the socket path short (a Unix socket path over about 100 characters fails).

## Seeds

The first-load fixtures leave out some states a UI check needs. Switch them on by name; `all` switches on every one.
A name that does not exist is an error.

| Seed | What it adds |
|---|---|
| `inbox-terminal` | An inbox card for `handline.auditor` with `fix: open_terminal`. |
| `hr` | Three proposals: add `handline.new-builder` (team `handline`, model `sonnet`), a setting change `model: sonnet -> opus`, and a retire to reject. |
| `tasks-200` | 200 tasks, T6000 to T6199, in every state. |
| `chat-actions` | A message from `handline.architect` with OK / No actions on an open task the founder owns (T6902). |
| `account-stalled` | The account `beta` stalled (`billing_error`): the Usage list shows STALLED and its reason, and `account.wake` ends it. |

Switch on:

- **Vite:** `HANDLINE_FAKE=1 HANDLINE_FAKE_VERBS=1 HANDLINE_FAKE_SEEDS=hr,tasks-200 vite` (the actions call task verbs, so add `HANDLINE_FAKE_VERBS=1`).
- **serve.ts:** `node src/serve.ts --seed all`, or `HANDLINE_FAKE_SEEDS=all`.
- **A test:** `applySeeds(daemon, 'chat-actions')` from `src/seeds.ts`, before the client connects.
