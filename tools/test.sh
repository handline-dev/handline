#!/usr/bin/env bash
# The one way to run tests from the repo root, whoever runs it (T946):
#   tools/test.sh                          the whole suite (tests/test_ocp.py)
#   tools/test.sh -k reconcile             the tests whose name has "reconcile"
#   tools/test.sh tests/ui/test_x.py [-k]  one area file, optionally one test in it
#                                          (the bridge file is out of the whole suite for now)
# It runs under the machine's venv python (the bridge tests need its textual and are
# skipped without it), with OCP_AGENT unset (an agent's own would make every ocp a
# test subject's), unbuffered (a test that hangs shows where, not silence) and with
# OCP_HOME in a temp dir that is removed afterwards, so nothing here reads or writes ~/.orcapipe.
cd "$(dirname "$0")/.." || exit 1
home="${OCP_HOME:-$HOME/.orcapipe}"
py="$home/venv/bin/python"
if [ ! -x "$py" ]; then
    py=python3
    echo "tools/test.sh: no venv at $home/venv (run \`hl venv install\`); using python3, bridge tests will be skipped" >&2
fi
scratch="$(mktemp -d "${TMPDIR:-/tmp}/handline-test.XXXXXX")" || exit 1
trap 'rm -rf "$scratch"' EXIT
if [ $# -gt 0 ] && [ -f "$1" ]; then
    target=("$1"); shift
else
    target=(tests/test_ocp.py)
    [ $# -eq 0 ] && whole=1
fi
echo "tools/test.sh: $py -u ${target[*]} $*" >&2
env -u OCP_AGENT -u OCP_ACCOUNT -u HANDLINE_AGENT -u HANDLINE_ACCOUNT -u HANDLINE_HOME OCP_HOME="$scratch" PYTHONUNBUFFERED=1 "$py" -u "${target[@]}" "$@" | tee "$scratch/run.log"
status=${PIPESTATUS[0]}
# The whole suite includes the Go daemon's required check (go test -race ./...); OCP_TEST_GO names another
# command for it (tests/infra/test_test_sh.py runs this script with a stub, not a nested go test -race).
if [ -n "$whole" ]; then
    "${OCP_TEST_GO:-tools/test-go.sh}" || status=1
fi
# What failed is said last: the Go run's output follows the suite's own list of failures and would bury it.
if [ "$status" -ne 0 ] && grep -qE '^(FAIL |  FAILED:)' "$scratch/run.log"; then
    echo "tools/test.sh: failed:" >&2
    grep -E '^(FAIL |  FAILED:)' "$scratch/run.log" | cut -c1-300 >&2
fi
exit $status
