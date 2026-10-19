#!/usr/bin/env bash
# Run a command in one of the machine's heavy-run slots (go/cmd/heavyslot): tools/heavy-slot.sh <command> [args...]
# tools/check.sh, tools/test-go.sh and the vitest test scripts (apps/web, apps/mobile, packages/app-data) run through it, so at most
# HANDLINE_HEAVY_SLOTS (default 1) of them run at once on this machine; the others wait and say who holds the slot. Inside a slot
# (HANDLINE_HEAVY_SLOT set) it runs the command as is.
[ -n "${HANDLINE_HEAVY_SLOT:-}" ] && exec "$@"
top="$(cd "$(dirname "$0")/.." && pwd)" || exit 1
command -v go >/dev/null || exec "$@"   # the Go checks say themselves that go is missing
bin="$top/go/heavyslot"   # kept between runs beside go/hl, not in TMPDIR: a run under tools/leakcheck.py must leave nothing there
if ! (cd "$top/go" && . "$top/tools/go-env.sh" && CGO_ENABLED=0 go build -o "$bin" ./cmd/heavyslot); then
    echo "tools/heavy-slot.sh: could not build the slot helper; runs without a slot" >&2
    exec "$@"
fi
exec "$bin" -- "$@"
