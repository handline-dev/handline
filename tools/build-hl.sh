#!/usr/bin/env bash
# Build the Go hl, which bin/hl runs once it is there: tools/build-hl.sh [output]
top="$(cd "$(dirname "$0")/.." && pwd)" || exit 1
cd "$top/go" || exit 1
. "$top/tools/go-env.sh"
out="${1:-$top/go/hl}"
CGO_ENABLED=0 go build -trimpath -o "$out" ./cmd/hl || exit 1
# What it was built from, beside it: bin/hl runs it only while the checkout's go/ is still that.
git -C "$top" rev-parse HEAD:go > "$out.src" 2>/dev/null || rm -f "$out.src"
