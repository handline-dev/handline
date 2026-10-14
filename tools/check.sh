#!/usr/bin/env bash
# The tests a change needs, from what it changes against <base> (default main): the one run the auditor makes on a sha.
#   tools/check.sh [<base>]
# - nothing changed, or documentation only (docs/, design/, *.md, but not contract/ or skill/): nothing.
# - only the Go daemon and the apps (go/, apps/, web/, packages/, the JS workspace files, the tools that serve only
#   the Go checks): the Go suite (tools/test-go.sh) and the Python files that test only Go (GO_FILES). No Python logic tests. None of this
#   tests the apps: their own checks are `pnpm turbo test typecheck lint`.
# - anything else (handline/, ocp/, tests/, tools/, ...): the whole suite, tools/suite.sh, which runs the Go suite too.
# Either of the last two also runs the tests of packages/app-data (6 s): its guards fail on a contract method the apps' tables do not name.
cd "$(dirname "$0")/.." || exit 1
# Hook and read parity with the Python daemon, and the Go migrations and method table being current.
GO_FILES=(tests/daemon/test_go_reads.py tests/daemon/test_go_hooks.py tests/infra/test_go_schema.py tests/infra/test_go_tidy.py)
base="${1:-main}"
app_data() {
    [ -d node_modules ] || { echo "tools/check.sh: no node_modules for the packages/app-data tests: run  pnpm install --frozen-lockfile" >&2; return 1; }
    pnpm --dir packages/app-data test
}
changed="$(git diff --name-only "$base...HEAD")" || exit 1      # from where HEAD left <base>: not what main did since
kind=none
while IFS= read -r path; do
    [ -n "$path" ] || continue
    case "$path" in
        contract/*|skill/*) kind=python; break ;;
        docs/*|design/*|*.md) [ "$kind" = none ] && kind=docs ;;
        go/*|apps/*|web/*|packages/*|package.json|pnpm-lock.yaml|pnpm-workspace.yaml|turbo.json) kind=go ;;
        tools/build-go.sh|tools/build-hl.sh|tools/go-env.sh|tools/check-go-tidy.sh|tools/conformance_fds.py|tools/go_contract.py|tools/go_schema.py) kind=go ;;
        *) kind=python; break ;;
    esac
done <<< "$changed"
case "$kind" in
    none) echo "nothing changed against $base: no tests to run" >&2 ;;
    docs) echo "documentation only: the suite is not run" >&2 ;;
    go)
        echo "tools/check.sh: only Go and the apps changed: the Go suite and ${GO_FILES[*]}, no Python logic tests" >&2
        app_data || exit 1
        tools/test-go.sh || exit 1
        for f in "${GO_FILES[@]}"; do tools/test.sh "$f" || exit 1; done ;;
    python)
        app_data || exit 1
        exec tools/suite.sh ;;
esac
