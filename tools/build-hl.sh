#!/usr/bin/env bash
# Build the Go hl, which bin/hl runs once it is there: tools/build-hl.sh [output]
top="$(cd "$(dirname "$0")/.." && pwd)" || exit 1
cd "$top/go" || exit 1
. "$top/tools/go-env.sh"
out="${1:-$top/go/hl}"
CGO_ENABLED=0 go build -trimpath -o "$out" ./cmd/hl || exit 1
# What it was built from, beside it: line 1 the git revisions of go.mod, go.sum and each package of this module the
# build compiled, then the tree each names. bin/hl runs it only while those are unchanged, so a change to Go that
# cmd/hl does not compile (the daemon's) leaves it on.
mod="$(go list -m)" || exit 1
revs="HEAD:go/go.mod HEAD:go/go.sum"
for pkg in $(go list -deps -f '{{if not .Standard}}{{.ImportPath}}{{end}}' ./cmd/hl); do
    case "$pkg" in "$mod"/*) revs="$revs HEAD:go/${pkg#"$mod"/}" ;; esac
done
# shellcheck disable=SC2086  # one argument per revision
if trees="$(git -C "$top" rev-parse $revs 2>/dev/null)"; then
    printf '%s\n%s\n' "$revs" "$trees" > "$out.src"
else
    rm -f "$out.src"
fi
