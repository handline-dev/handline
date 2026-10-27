#!/usr/bin/env bash
# Run a command in one of the machine's heavy-run slots (go/cmd/heavyslot): tools/heavy-slot.sh <command> [args...]
# tools/check.sh, tools/test-go.sh and the vitest test scripts (apps/web, apps/mobile, packages/app-data) run through it, so at most
# HANDLINE_HEAVY_SLOTS (default 2) of them run at once on this machine; the others wait their turn, in the order they came, and say who holds the slots. Inside a slot
# (HANDLINE_HEAVY_SLOT set) it runs the command as is.
[ -n "${HANDLINE_HEAVY_SLOT:-}" ] && exec "$@"
top="$(cd "$(dirname "$0")/.." && pwd)" || exit 1
command -v go >/dev/null || exec "$@"   # the Go checks say themselves that go is missing
# The helper joins a queue every worktree shares, so it is built from origin/main's committed source (one binary per commit, kept in
# ~/.handline/heavy-bin), never from this worktree's: a helper that waits wrongly would hold every agent's heavy run (T6736). Whoever changes
# the helper tests it with HANDLINE_HEAVY_LOCAL=1, which builds from the worktree as before, and so also when origin/main cannot be read.
bin="$top/go/heavyslot"   # kept between runs beside go/hl, not in TMPDIR: a run under tools/leakcheck.py must leave nothing there
pinned=""
if [ -z "${HANDLINE_HEAVY_LOCAL:-}" ]; then
    if sha="$(git -C "$top" rev-parse --verify -q origin/main 2>/dev/null)"; then
        dir="${HANDLINE_HEAVY_BIN:-$HOME/.handline/heavy-bin}"
        pinned="$dir/heavyslot-$sha"
        if ! [ -x "$pinned" ]; then
            src="$(mktemp -d "${TMPDIR:-/tmp}/heavyslot-src.XXXXXX")" &&
                trap 'rm -rf "$src" "$pinned.$$"' EXIT &&
                trap 'exit 130' INT && trap 'exit 143' TERM   # a killed build must end the run, not fall through to the fallback
            if git -C "$top" archive "$sha" go tools/go-env.sh | tar -x -C "$src" && mkdir -p "$dir" &&
                (cd "$src/go" && . "$src/tools/go-env.sh" && CGO_ENABLED=0 go build -o "$pinned.$$" ./cmd/heavyslot) &&
                mv "$pinned.$$" "$pinned"; then
                # A binary is 12 MB and main takes hundreds of commits a day: keep the 3 newest, and drop what a killed build left.
                find "$dir" -name 'heavyslot-*' ! -name '*.*' | xargs ls -t | tail -n +4 | xargs rm -f
                find "$dir" -name 'heavyslot-*.[0-9]*' -mmin +60 -delete 2>/dev/null
            else
                pinned=""
            fi
            rm -rf "$src"; trap - EXIT INT TERM   # the trap is for a build that is killed; exec below would skip it
        fi
    fi
    [ -n "$pinned" ] || echo "tools/heavy-slot.sh: origin/main could not be read or built; the slot helper is built from this worktree" >&2
fi
if [ -n "$pinned" ] && [ -x "$pinned" ]; then
    exec "$pinned" -- "$@"
fi
if ! (cd "$top/go" && . "$top/tools/go-env.sh" && CGO_ENABLED=0 go build -o "$bin" ./cmd/heavyslot); then
    echo "tools/heavy-slot.sh: could not build the slot helper; runs without a slot" >&2
    exec "$@"
fi
exec "$bin" -- "$@"
