#!/usr/bin/env bash
# A phone simulator or emulator for a check, started when the check needs it and stopped when it ends: tools/phone-sim.sh <command>
#   status                 what is running now (iOS simulators and Android emulators), "none" when nothing is
#   android start NAME     boots the Android virtual device NAME without a window or sound and waits until it is ready
#   android stop           shuts the running Android emulator down (and so its qemu)
#   ios start NAME         boots the iOS simulator NAME with no Simulator.app window
#   ios stop NAME          shuts the iOS simulator NAME down
# An emulator left running costs a core for as long as it lives (one sat idle for six days at 60-70% CPU), and the daemon holds new work
# while one runs (loadgate, host_simulator_hold). Stop what you started when the check ends; leave the ones you did not start.
set -uo pipefail

running_android() { adb devices 2>/dev/null | awk '/^emulator-[0-9]+\tdevice/ {print $1}'; }
running_ios() { xcrun simctl list devices booted 2>/dev/null | grep -E 'Booted' | sed -E 's/^ *//; s/ \([0-9A-F-]{36}\).*//'; }
labelled() { [ -n "$2" ] && echo "$2" | sed "s/^/$1: /"; return 0; }

status() {
    local a i
    a="$(running_android)" i="$(running_ios)"
    labelled "Android emulator" "$a"
    labelled "iOS simulator" "$i"
    [ -z "$a$i" ] && echo none
    return 0
}

android_start() {
    [ -n "${1:-}" ] || { echo "usage: tools/phone-sim.sh android start NAME" >&2; return 2; }
    if [ -n "$(running_android)" ]; then echo "an Android emulator is already running"; return 0; fi
    nohup emulator -avd "$1" -no-window -no-audio >/dev/null 2>&1 &
    adb wait-for-device
    until [ "$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ]; do sleep 2; done
    status
}

android_stop() {
    local d
    for d in $(running_android); do adb -s "$d" emu kill >/dev/null 2>&1; done
    status
}

ios_start() {
    [ -n "${1:-}" ] || { echo "usage: tools/phone-sim.sh ios start NAME" >&2; return 2; }
    xcrun simctl boot "$1" && status   # never `open -a Simulator`: its window takes the person's focus
}

case "${1:-}:${2:-}" in
    status:*) status ;;
    android:start) android_start "${3:-}" ;;
    android:stop) android_stop ;;
    ios:start) ios_start "${3:-}" ;;
    ios:stop) [ -n "${3:-}" ] || { echo "usage: tools/phone-sim.sh ios stop NAME" >&2; exit 2; }; xcrun simctl shutdown "$3"; status ;;
    *) sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2 ;;
esac
