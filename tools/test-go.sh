#!/usr/bin/env bash
# The Go daemon's required check: `go test ./...` in go/, and a cgo-free build of the one binary.
# Run by tools/test.sh with the whole suite; run it alone with `tools/test-go.sh [go test args]`.
# -race only when HANDLINE_GO_RACE=1, as the auditor sets it for a sha: a race build doubles the build cache, and every worktree runs this.
here="$(cd "$(dirname "$0")" && pwd)"
cd "$here/../go" || exit 1
if ! command -v go >/dev/null; then
    echo "tools/test-go.sh: no go on PATH; go test ./... is a required check" >&2
    exit 1
fi
. "$here/go-env.sh"
# The live tests spend real tokens: a shell that exported HANDLINE_LIVE still does not run them here.
unset HANDLINE_LIVE
# A scratch home for anything a test starts that reads one: never the live home of the agent running this. Its path is fixed
# per checkout, since go test keys its cache on the environment a test reads, and a fresh path each run would miss it.
scratch_home="${TMPDIR:-/tmp}/handline-go-home.$(printf %s "$here" | cksum | cut -d' ' -f1)"
rm -rf "$scratch_home" && mkdir -p "$scratch_home" || exit 1
trap 'rm -rf "$scratch_home"' EXIT
export HANDLINE_HOME="$scratch_home" OCP_HOME="$scratch_home"
# The user home the tests and the daemons they start see: nothing they write lands in the real ~/.config or ~/.claude.
mkdir -p "$scratch_home/user" && export HANDLINE_TEST_ROOT="$scratch_home/user"
real_protected="$HOME/.config/orcapipe/protected"
protected_before="$(ls -A "$real_protected" 2>/dev/null)"
protected_unchanged() {
    [ "$(ls -A "$real_protected" 2>/dev/null)" = "$protected_before" ] && return 0
    echo "FAIL  the Go tests wrote to the real $real_protected" >&2
    return 1
}
# The whole run also checks go.mod and go.sum are tidy, and that no new copy of Go code came in (a run with go test args does not).
[ $# -eq 0 ] && { "$here/check-go-tidy.sh" || exit 1; }
[ $# -eq 0 ] && { "$here/check-go-dupl.sh" || exit 1; }
race=()
[ "${HANDLINE_GO_RACE:-}" = 1 ] && race=(-race)
echo "tools/test-go.sh: go test ${race[*]} ${*:-./...}" >&2
go test "${race[@]}" "${@:-./...}" || exit 1
protected_unchanged || exit 1
[ $# -gt 0 ] && exit 0
CGO_ENABLED=0 go build -o /dev/null ./cmd/handlined

# The conformance fixtures against the built binary. Until the harness is on main there is nothing to run.
harness="$here/../contract/conformance.py"
if [ ! -f "$harness" ]; then
    echo "tools/test-go.sh: SKIPPED the conformance fixtures: contract/conformance.py is not on this branch yet" >&2
    exit 0
fi
work="$(mktemp -d "${TMPDIR:-/tmp}/handlined.XXXXXX")" || exit 1
trap 'rm -rf "$work" "$scratch_home"' EXIT
# The lists are kept one name per line in byte order, so two branches deleting neighbouring names merge instead of conflicting.
for list in "$here/../go/conformance-missing.txt" "$here/../go/conformance-needvar.txt" "$here/../go/conformance-skip.txt"; do
    grep -v '^#' "$list" | LC_ALL=C sort -c 2>/dev/null || {
        echo "FAIL  $list is not sorted: run  grep -v '^#' it | LC_ALL=C sort -u  and keep the # header lines on top" >&2
        exit 1
    }
done
"$here/build-go.sh" "$work/handlined" || exit 1
# No socket file is bound (a sandbox may forbid it): tools/conformance_fds.py hands the daemon socketpairs, one per
# fixture, and mints the founder key in a scratch home; the agent role's socketpairs come from a process recorded as an agent's
# session there. A method the Go daemon does not serve yet is named in go/conformance-missing.txt: delete a line as its method
# lands. A fixture that needs a --var this run cannot seed yet is named in go/conformance-needvar.txt the same way, and one
# left failing on a known gap in go/conformance-skip.txt.
# The daemon runs with -structured-cli and a fake CLI that answers --version with the version the Go source pins, as the
# service runs it with the real one.
FAKE_CLAUDE_VERSION="$(sed -n 's/.*PinnedCLI = "\([^"]*\)".*/\1/p' "$here/../go/internal/structured/session.go")"
[ -n "$FAKE_CLAUDE_VERSION" ] || { echo "tools/test-go.sh: no PinnedCLI in go/internal/structured/session.go" >&2; exit 1; }
export FAKE_CLAUDE_VERSION
conformance() {
    python3 "$here/conformance_fds.py" "$work/handlined" "$@" --allow-missing-file "$here/../go/conformance-missing.txt" \
        --allow-needvar-file "$here/../go/conformance-needvar.txt" --skip-file "$here/../go/conformance-skip.txt" \
        "$here"/../contract/fixtures/*.json
}
echo "tools/test-go.sh: conformance fixtures, -structured-cli" >&2
conformance --structured-cli "$here/fake_claude.py" || exit 1
echo "tools/test-go.sh: conformance fixtures, -structured-cli -structured-tick" >&2
conformance --structured-cli "$here/fake_claude.py" --structured-tick || exit 1
protected_unchanged
