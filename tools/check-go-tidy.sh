#!/usr/bin/env bash
# go.mod and go.sum must be what `go mod tidy` writes: fails, showing the diff, when they are not.
# `tools/check-go-tidy.sh [module dir]` (default go/). Tidy has to read the modules, so when the network is
# the only thing wrong (no route, a proxy that refuses, GOPROXY=off with a short cache) it says SKIPPED and
# exits 0 rather than fail a check it could not run. A missing module or version (404, 410, "cannot find
# module") is a real failure and exits 1. There are no CI workflows: the gate is `tools/check.sh`, and
# only a run of it outside a sandbox proves tidiness; a sandboxed run that SKIPs proves nothing.
# `--classify` reads go's output on stdin and prints `skip` or `fail` (the tests feed it canned output).
here="$(cd "$(dirname "$0")" && pwd)"

# A transport failure: nothing about the module was learned.
transport='dial tcp|no such host|i/o timeout|network is unreachable|connection (refused|reset)|module lookup disabled by GOPROXY=off|: Forbidden$|TLS handshake timeout'

# `skip` when go printed errors and every one is a transport failure; `fail` otherwise (a diff, a 404, anything else).
classify() {
    local errors
    errors="$(grep -vE '^go: (downloading|finding|extracting) |^go: .* imports$|^[[:space:]]*$' || true)"
    if [ -n "$errors" ] && ! grep -qE 'cannot find module' <<<"$errors" && ! grep -vE "$transport" <<<"$errors" | grep -q .; then
        echo skip
    else
        echo fail
    fi
}

if [ "${1:-}" = "--classify" ]; then
    classify
    exit 0
fi

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
if [ "$(classify <<<"$out")" = skip ]; then
    reason="$(grep -E "$transport" <<<"$out" | head -1)"
    echo "tools/check-go-tidy.sh: SKIPPED: go mod tidy could not reach the modules ($reason)" >&2
    exit 0
fi
echo "tools/check-go-tidy.sh: go mod tidy -diff failed:" >&2
echo "$out" >&2
exit 1
