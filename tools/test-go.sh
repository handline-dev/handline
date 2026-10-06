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

# The conformance fixtures against the built binary. Until the harness is on main there is nothing to run.
harness="$here/../contract/conformance.py"
if [ ! -f "$harness" ]; then
    echo "tools/test-go.sh: SKIPPED the conformance fixtures: contract/conformance.py is not on this branch yet" >&2
    exit 0
fi
work="$(mktemp -d "${TMPDIR:-/tmp}/handlined.XXXXXX")" || exit 1
trap 'kill "$pid" 2>/dev/null; rm -rf "$work"' EXIT
"$here/build-go.sh" "$work/handlined" || exit 1
HANDLINE_TEST_ROOT="$work" "$work/handlined" -home "$work/state" >"$work/log" 2>&1 &
pid=$!
for _ in $(seq 50); do [ -S "$work/state/daemon.sock" ] && break; sleep 0.1; done
if [ ! -S "$work/state/daemon.sock" ]; then
    echo "tools/test-go.sh: the daemon did not come up:" >&2; cat "$work/log" >&2; exit 1
fi
# The one socket serves the founder (the harness's default role) and the anonymous caller. No socket is
# given for agent and member: this daemon cannot tell those apart without a recorded session or a paired
# device, so their fixtures are reported SKIP, and --allow-skip keeps that from failing the run.
python3 "$harness" "$work/state/daemon.sock" --as anonymous="$work/state/daemon.sock" --allow-skip \
    "$here"/../contract/fixtures/*.json
