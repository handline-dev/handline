#!/usr/bin/env bash
# A phone simulator or emulator for a check, stopped when it ends: tools/phone-sim.sh <command>
#   status | android start NAME | android stop | ios start NAME | ios stop NAME
# One left running costs a core (an emulator sat idle for six days at 60-70% CPU), and the daemon holds new work while one runs
# (host_simulator_hold). Stop what you started; leave the ones you did not.
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
    *) sed -n '2,3p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2 ;;
esac
