#!/usr/bin/env bash
# Run a phone check in a heavy slot and stop the simulator and emulator when it ends, also when it fails:
#   apps/mobile/scripts/sim-check.sh <command> [args...]     e.g. a script that boots the simulator, drives it and takes screenshots
# The command boots what it needs; this only guarantees the shutdown.
top="$(cd "$(dirname "$0")/../../.." && pwd)" || exit 1
stop() {
    xcrun simctl shutdown all 2>/dev/null
    adb emu kill >/dev/null 2>&1
}
trap stop EXIT
"$top/tools/heavy-slot.sh" "$@"
