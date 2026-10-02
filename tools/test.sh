#!/usr/bin/env bash
# The one way to run tests from the repo root, whoever runs it (T946):
#   tools/test.sh                          the whole suite (tests/test_ocp.py)
#   tools/test.sh -k reconcile             the tests whose name has "reconcile"
#   tools/test.sh tests/ui/test_x.py [-k]  one area file, optionally one test in it
#                                          (not tests/ui/test_bridge_textual.py: it has no -k and refuses it)
# It runs under the machine's venv python (the bridge tests need its textual and are
# skipped without it), with OCP_AGENT unset (an agent's own would make every ocp a
# test subject's), unbuffered (a test that hangs shows where, not silence) and with
# OCP_HOME in a temp dir that is removed afterwards, so nothing here reads or writes ~/.orcapipe.
cd "$(dirname "$0")/.." || exit 1
home="${OCP_HOME:-$HOME/.orcapipe}"
py="$home/venv/bin/python"
if [ ! -x "$py" ]; then
    py=python3
    echo "tools/test.sh: no venv at $home/venv (run \`ocp venv install\`); using python3, bridge tests will be skipped" >&2
fi
scratch="$(mktemp -d "${TMPDIR:-/tmp}/ocp-test.XXXXXX")" || exit 1
trap 'rm -rf "$scratch"' EXIT
if [ $# -gt 0 ] && [ -f "$1" ]; then
    target=("$1"); shift
else
    target=(tests/test_ocp.py)
fi
echo "tools/test.sh: $py -u ${target[*]} $*" >&2
env -u OCP_AGENT -u OCP_ACCOUNT -u HANDLINE_AGENT -u HANDLINE_ACCOUNT -u HANDLINE_HOME OCP_HOME="$scratch" PYTHONUNBUFFERED=1 "$py" -u "${target[@]}" "$@"
