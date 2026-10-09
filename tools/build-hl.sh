#!/usr/bin/env bash
# Build the Go hl, which bin/hl runs once it is there: tools/build-hl.sh [output]
top="$(cd "$(dirname "$0")/.." && pwd)" || exit 1
cd "$top/go" || exit 1
. "$top/tools/go-env.sh"
CGO_ENABLED=0 go build -trimpath -o "${1:-$top/go/hl}" ./cmd/hl
