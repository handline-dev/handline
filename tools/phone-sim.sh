#!/usr/bin/env bash
# A phone simulator or emulator for a check, stopped when it ends: tools/phone-sim.sh <command>
#   status | android start NAME | android stop SERIAL | ios start NAME | ios stop NAME
# One left running costs a core (an emulator sat idle for six days at 60-70% CPU). Stop what you started; leave the ones you did not:
# android start prints the serial it launched, and stop takes it.
set -uo pipefail

running_android() { adb devices 2>/dev/null | awk '/^emulator-[0-9]+\tdevice/ {print $1}'; }
running_ios() { xcrun simctl list devices booted 2>/dev/null | grep -E 'Booted' | sed -E 's/^ *//; s/ \([0-9A-F-]{36}\).*//'; }
# A check that sets PHONE_SIM_STARTED gets one line per device this script booted ("android SERIAL", "ios UDID"), to stop exactly those.
note_started() { [ -z "${PHONE_SIM_STARTED:-}" ] || echo "$1 $2" >> "$PHONE_SIM_STARTED"; }
labelled() { [ -n "$2" ] && echo "$2" | sed "s/^/$1: /"; return 0; }

status() {
    local a i
    a="$(running_android)" i="$(running_ios)"
    labelled "Android emulator" "$a"
    labelled "iOS simulator" "$i"
    [ -z "$a$i" ] && echo none
    return 0
}

free_port() {
    local p
    for p in $(seq 5554 2 5584); do adb devices 2>/dev/null | grep -q "^emulator-$p\b" || { echo "$p"; return 0; }; done
    return 1
}

# Starts the emulator on a port of its own, so its serial is known, and gives up after PHONE_SIM_BOOT_TIMEOUT seconds (default 180)
# or as soon as the emulator process is gone (a wrong AVD name).
android_start() {
    local port serial pid deadline
    [ -n "${1:-}" ] || { echo "usage: tools/phone-sim.sh android start NAME" >&2; return 2; }
    if [ -n "$(running_android)" ]; then echo "an Android emulator is already running"; return 0; fi
    port="$(free_port)" || { echo "no free emulator port" >&2; return 1; }
    serial="emulator-$port"
    nohup emulator -avd "$1" -port "$port" -no-window -no-audio >/dev/null 2>&1 &
    pid=$!
    deadline=$((SECONDS + ${PHONE_SIM_BOOT_TIMEOUT:-180}))
    until [ "$(adb -s "$serial" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" = 1 ]; do
        kill -0 "$pid" 2>/dev/null || { echo "the emulator $1 exited before it booted" >&2; return 1; }
        [ "$SECONDS" -lt "$deadline" ] || { echo "the emulator $1 did not boot in time" >&2; adb -s "$serial" emu kill >/dev/null 2>&1; kill "$pid" 2>/dev/null; return 1; }
        sleep 2
    done
    note_started android "$serial"
    echo "started $serial"
    status
}

android_stop() {
    [ -n "${1:-}" ] || { echo "usage: tools/phone-sim.sh android stop SERIAL   (the one android start printed)" >&2; return 2; }
    adb -s "$1" emu kill >/dev/null 2>&1
    status
}

# The UDID of a simulator given by name (or the UDID itself), so only that one is booted and noted.
ios_udid() {
    case "$1" in
        *-*-*-*-*) echo "$1" ;;
        *) xcrun simctl list devices available 2>/dev/null | awk -v n="$1" '{ sub(/^ +/, "") } index($0, n " (") == 1 { if (match($0, /[0-9A-F]{8}(-[0-9A-F]{4}){3}-[0-9A-F]{12}/)) { print substr($0, RSTART, RLENGTH); exit } }' ;;
    esac
}

ios_start() {
    local udid
    [ -n "${1:-}" ] || { echo "usage: tools/phone-sim.sh ios start NAME" >&2; return 2; }
    udid="$(ios_udid "$1")"
    [ -n "$udid" ] || { echo "no simulator named $1" >&2; return 1; }
    xcrun simctl boot "$udid" || return 1   # never `open -a Simulator`: its window takes the person's focus
    note_started ios "$udid"
    status
}

case "${1:-}:${2:-}" in
    status:*) status ;;
    android:start) android_start "${3:-}" ;;
    android:stop) android_stop "${3:-}" ;;
    ios:start) ios_start "${3:-}" ;;
    ios:stop) [ -n "${3:-}" ] || { echo "usage: tools/phone-sim.sh ios stop NAME" >&2; exit 2; }; xcrun simctl shutdown "$3"; status ;;
    *) sed -n '2,3p' "$0" | sed 's/^# \{0,1\}//' >&2; exit 2 ;;
esac
