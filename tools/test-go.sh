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
trap 'rm -rf "$work"' EXIT
"$here/build-go.sh" "$work/handlined" || exit 1
# No socket file is bound (a sandbox may forbid it): tools/conformance_fds.py hands the daemon socketpairs, one per
# fixture, and mints the founder key in a scratch home. The founder and anonymous roles run; agent and member have no
# connection of their own (this daemon cannot tell them apart without a recorded session or a paired device), so
# their fixtures are reported SKIP and --allow-skip says that is meant. A method the Go daemon does not serve yet is
# named in go/conformance-missing.txt: delete a line as its method lands.
python3 "$here/conformance_fds.py" "$work/handlined" --allow-missing-file "$here/../go/conformance-missing.txt" \
    "$here"/../contract/fixtures/*.json
