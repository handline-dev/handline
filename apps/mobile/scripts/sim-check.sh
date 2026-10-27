#!/usr/bin/env bash
# Run a phone check in a heavy slot and, when it ends (also when it fails), stop what it started and nothing else:
#   apps/mobile/scripts/sim-check.sh <command> [args...]     e.g. a script that boots the simulator, drives it and takes screenshots
# The command boots its simulator or emulator with tools/phone-sim.sh, which writes the UDID or serial it booted into the file named by
# PHONE_SIM_STARTED; those are stopped here. A device somebody else booted is never listed, so it is never stopped. What the command left
# behind (Metro, a fake daemon) is ended with its process group, which is made inside the slot because heavyslot gives its command a group of its own.
top="$(cd "$(dirname "$0")/../../.." && pwd)" || exit 1
sim="$top/tools/phone-sim.sh"
PHONE_SIM_STARTED="$(mktemp)" || exit 1
export PHONE_SIM_STARTED
"$top/tools/heavy-slot.sh" bash -c '
    set -m
    "$@" &
    pid=$!
    trap "kill -- -$pid 2>/dev/null" INT TERM
    wait "$pid"
    status=$?
    kill -- -"$pid" 2>/dev/null
    exit "$status"
' _ "$@"
status=$?
while read -r kind id; do
    case "$kind" in
        ios) "$sim" ios stop "$id" >/dev/null ;;
        android) "$sim" android stop "$id" >/dev/null ;;
    esac
done < "$PHONE_SIM_STARTED"
rm -f "$PHONE_SIM_STARTED"
exit "$status"
