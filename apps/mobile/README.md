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

An agent runs both without a window in front of the person, since a simulator window that opens takes their focus. `npx expo run:ios` opens Simulator.app, so an agent builds once and installs on a simulator booted without it; Maestro drives it as it is:

```
xcrun simctl boot "iPhone 16"                     # booted with no Simulator.app window; never `open -a Simulator`
npx expo prebuild --platform ios && xcodebuild -workspace ios/Handline.xcworkspace -scheme Handline -sdk iphonesimulator -derivedDataPath ios/build build
xcrun simctl install booted ios/build/Build/Products/Debug-iphonesimulator/Handline.app
xcrun simctl io booted screenshot shot.png        # a screenshot without a window
emulator -avd <name> -no-window -no-audio &       # Android, the same way
```

Start them for the check and stop them when it ends: a simulator or emulator left running costs a core (one sat idle for six days at 60-70% CPU), and the daemon holds new agent work while one runs. `tools/phone-sim.sh` does both and shows the state:

```
tools/phone-sim.sh status                   # what is running now, "none" when nothing is
tools/phone-sim.sh android start <name>     # no window, no sound; prints the serial it started, returns when booted (180s limit, PHONE_SIM_BOOT_TIMEOUT)
tools/phone-sim.sh android stop <serial>      # only that emulator, the one start printed; also ends its qemu
tools/phone-sim.sh ios start "iPhone 16"    # no Simulator.app window
tools/phone-sim.sh ios stop "iPhone 16"
```

`scripts/sim-check.sh <command>` runs a check in a heavy slot and, when it ends, whatever its result, stops the simulators and emulators the command booted with `tools/phone-sim.sh start` (it notes each in `PHONE_SIM_STARTED`) and the processes it left behind, such as Metro. A device booted by anyone else, or by a raw `simctl boot`, is left alone.
