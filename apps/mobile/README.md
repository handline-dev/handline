# apps/mobile

The phone app (Expo dev client, not Expo Go: it carries the `pinned-socket` native module).

React Native's `WebSocket` only trusts certificates the OS trusts, and the daemon's is self-signed. `modules/pinned-socket` is a
local Expo module (iOS: `URLSessionWebSocketTask` that accepts the certificate whose SHA-256 is the pairing fingerprint);
`src/pinned-transport.ts` makes it a client `Transport`. Android pinning is not written yet.

Run on the iOS simulator against the fake daemon:

```
pnpm --filter @handline/mobile fake-listener      # prints a one-time handline://pair link; the daemon listens on wss://127.0.0.1:7788
ENTERPRISE_REPOSITORY=<maven mirror, if Maven Central is slow> npx expo run:ios     # first time: prebuild + pod install + build
npx expo start --dev-client                       # then paste the link into the app
maestro test -e URI="<the link>" e2e/pair-and-page.yaml   # the same flow, scripted, with screenshots
```
