#!/usr/bin/env bash
# The Go daemon's required check: `go test -race ./...` in go/, and a cgo-free build of the one binary.
# Run by tools/test.sh with the whole suite; run it alone with `tools/test-go.sh [go test args]`.
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here/../go" || exit 1
if ! command -v go >/dev/null; then
    echo "tools/test-go.sh: no go on PATH; go test -race ./... is a required check" >&2
    exit 1
fi
. "$here/go-env.sh"
echo "tools/test-go.sh: go test -race ${*:-./...}" >&2
go test -race "${@:-./...}" || exit 1
[ $# -gt 0 ] && exit 0
CGO_ENABLED=0 go build -o /dev/null ./cmd/handlined
