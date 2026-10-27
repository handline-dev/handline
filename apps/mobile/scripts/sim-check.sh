#!/usr/bin/env bash
# Run a phone check in a heavy slot and, when it ends (also when it fails), stop what it started and nothing else:
#   apps/mobile/scripts/sim-check.sh <command> [args...]     e.g. a script that boots the simulator, drives it and takes screenshots
# The simulators and emulators that were up before the command stay up; the ones that appeared are stopped through tools/phone-sim.sh,
# and the processes the command left behind (Metro, a fake daemon) are ended with its process group.
top="$(cd "$(dirname "$0")/../../.." && pwd)" || exit 1
sim="$top/tools/phone-sim.sh"
booted_ios() { xcrun simctl list devices booted 2>/dev/null | grep -oE '[0-9A-F]{8}(-[0-9A-F]{4}){3}-[0-9A-F]{12}' | sort; }
booted_android() { adb devices 2>/dev/null | awk '/^emulator-[0-9]+\tdevice/ {print $1}' | sort; }
ios_before="$(booted_ios)" android_before="$(booted_android)"
set -m # the command runs as its own process group, so what it leaves behind can be ended with it
"$top/tools/heavy-slot.sh" "$@" &
pid=$!
trap 'kill -- -"$pid" 2>/dev/null' INT TERM
wait "$pid"
status=$?
trap - INT TERM
kill -- -"$pid" 2>/dev/null
for u in $(comm -13 <(echo "$ios_before") <(booted_ios)); do "$sim" ios stop "$u" >/dev/null; done
for s in $(comm -13 <(echo "$android_before") <(booted_android)); do "$sim" android stop "$s" >/dev/null; done
exit "$status"
