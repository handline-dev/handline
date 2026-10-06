#!/usr/bin/env bash
# Build the Go daemon, one binary with the git sha stamped in and no cgo: tools/build-go.sh [output]
top="$(cd "$(dirname "$0")/.." && pwd)" || exit 1
sha="$(git -C "$top" rev-parse --short HEAD)" || exit 1
cd "$top/go" || exit 1
. "$top/tools/go-env.sh"
CGO_ENABLED=0 go build -trimpath -ldflags "-X github.com/handline/handline/go/internal/version.Sha=$sha" \
    -o "${1:-$top/go/handlined}" ./cmd/handlined
