#!/usr/bin/env bash
# go.mod and go.sum must be what `go mod tidy` writes: fails, showing the diff, when they are not.
# `tools/check-go-tidy.sh [module dir]` (default go/). Tidy has to read the modules, so with the network
# unreachable and the module cache short it SKIPs, saying so, rather than failing a check it could not run.
here="$(cd "$(dirname "$0")" && pwd)"
dir="${1:-$here/../go}"
if ! command -v go >/dev/null; then
    echo "tools/check-go-tidy.sh: no go on PATH; go mod tidy -diff is a required check" >&2
    exit 1
fi
. "$here/go-env.sh"
out="$(go -C "$dir" mod tidy -diff 2>&1)" && exit 0
if grep -qE '^(diff |--- |\+\+\+ )' <<<"$out"; then
    echo "tools/check-go-tidy.sh: go.mod or go.sum is not tidy; run \`go -C go mod tidy\` and commit:" >&2
    echo "$out" >&2
    exit 1
fi
reason="$(grep -iE 'dial tcp|no such host|lookup |i/o timeout|proxy|network is unreachable|connection (refused|reset)|cannot find module' <<<"$out" | head -1)"
if [ -n "$reason" ]; then
    echo "tools/check-go-tidy.sh: SKIPPED: go mod tidy could not reach the module proxy ($reason)" >&2
    exit 0
fi
echo "tools/check-go-tidy.sh: go mod tidy -diff failed:" >&2
echo "$out" >&2
exit 1
