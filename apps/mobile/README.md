# apps/mobile

The phone app (Expo dev client, not Expo Go: it carries the `pinned-socket` native module).

React Native's `WebSocket` only trusts certificates the OS trusts, and the daemon's is self-signed. `modules/pinned-socket` is a
local Expo module (iOS: `URLSessionWebSocketTask`; Android: OkHttp with a trust manager) that accepts only the certificate whose SHA-256 is
the pairing fingerprint; `src/pinned-transport.ts` makes it a client `Transport`.

Run on the iOS simulator against the fake daemon:

```
pnpm --filter @handline/mobile fake-listener      # prints a one-time handline://pair link; the daemon listens on wss://127.0.0.1:7788
ENTERPRISE_REPOSITORY=<maven mirror, if Maven Central is slow> npx expo run:ios     # first time: prebuild + pod install + build
npx expo start --dev-client                       # then paste the link into the app
maestro test -e URI="<the link>" e2e/pair-and-page.yaml   # the same flow, scripted, with screenshots
```

Run on the Android emulator (a Release build embeds the JS bundle, so no Metro is needed; the daemon is reached through `adb reverse`):

```
cd android && ./gradlew assembleRelease -PreactNativeArchitectures=arm64-v8a       # first time: about 9 minutes
adb install -r android/app/build/outputs/apk/release/app-release.apk
adb reverse tcp:7788 tcp:7788
pnpm --filter @handline/mobile fake-listener                                       # prints the link
adb shell monkey -p dev.handline.mobile -c android.intent.category.LAUNCHER 1      # Maestro does not launch the app
maestro --device emulator-5554 test -e URI="<the link>" e2e/pair-and-page.yaml
maestro --device emulator-5554 test -e URI="<the link, fp changed>" e2e/pin-mismatch.yaml   # must say the certificate does not match
```

Screenshots of both runs are in `shots/android/`.
