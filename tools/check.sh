#!/usr/bin/env bash
# The tests a change needs, from what it changes against <base> (default main): the one run the auditor makes on a sha.
#   tools/check.sh [<base>]
# - nothing changed, or documentation only (docs/, design/, *.md, but not contract/ or skill/): nothing.
# - only the Go daemon and the apps (go/, apps/, packages/, the JS workspace files, the tools that serve only
#   the Go checks): the Go suite (tools/test-go.sh) and the Python files that test only Go (GO_FILES). No Python logic tests.
# - anything else (handline/, ocp/, tests/, tools/, ...): the whole suite, tools/suite.sh, which runs the Go suite too.
# Either of the last two first runs the app checks: `pnpm turbo test typecheck lint` (turbo caches it) when a JS workspace
# file changed (apps/, packages/, the root JS files, contract/protocol.ts), else only the packages/app-data tests
# (6 s), whose guards fail on a contract method the apps' tables do not name.
# CHECK_PLAN=1 prints `kind=<none|docs|go|python> apps=<no|app-data|turbo>` and runs nothing.
# A failing step does not stop the ones after it, so one run shows every failure; the exit status is 1 if any failed.
# The tests run with TMPDIR and TMUX_TMPDIR in a directory of their own, and fail when they leave a process, a tmux
# server, a temp file or a write to the real protected store behind (tools/leakcheck.py).
cd "$(dirname "$0")/.." || exit 1
# Hook and read parity with the Python daemon, and the Go migrations and method table being current.
GO_FILES=(tests/infra/test_go_schema.py tests/infra/test_go_tidy.py)
base="${1:-main}"
apps() {
    [ -d node_modules ] || { echo "tools/check.sh: no node_modules for the app checks: run  pnpm install --frozen-lockfile" >&2; return 1; }
    if grep -qE '^(apps|packages)/|^(package\.json|pnpm-lock\.yaml|pnpm-workspace\.yaml|turbo\.json|contract/package\.json|contract/protocol\.ts)$' <<< "$changed"; then
        pnpm turbo test typecheck lint --continue
        tools/dupes.sh
    else
        pnpm --dir packages/app-data test
    fi
}
changed="$(git diff --name-only "$base...HEAD")" || exit 1      # from where HEAD left <base>: not what main did since
kind=none
while IFS= read -r path; do
    [ -n "$path" ] || continue
    case "$path" in
        contract/*|skill/*) kind=python; break ;;
        docs/*|design/*|*.md) [ "$kind" = none ] && kind=docs ;;
        go/*|apps/*|packages/*|package.json|pnpm-lock.yaml|pnpm-workspace.yaml|turbo.json) kind=go ;;
        tools/build-go.sh|tools/build-hl.sh|tools/go-env.sh|tools/check-go-tidy.sh|tools/check-go-dupl.sh|tools/conformance_fds.py|tools/go_contract.py|tools/go_schema.py) kind=go ;;
        *) kind=python; break ;;
    esac
done <<< "$changed"
# CHECK_PLAN=1: say what would run and stop (CI reads this to pick its jobs): kind is none, docs, go or python; apps is yes or no.
if [ -n "${CHECK_PLAN:-}" ]; then
    apps=no
    case "$kind" in go|python) grep -qE '^(apps|web|packages)/|^(package\.json|pnpm-lock\.yaml|pnpm-workspace\.yaml|turbo\.json|contract/package\.json|contract/protocol\.ts)$' <<< "$changed" && apps=turbo || apps=app-data ;; esac
    echo "kind=$kind apps=$apps"
    exit 0
fi
rc=0
if [ "$kind" = go ] || [ "$kind" = python ]; then
    run="$(tools/leakcheck.py begin)" || exit 1
    export TMPDIR="$run/tmp/" TMUX_TMPDIR="$run"
fi
case "$kind" in
    none) echo "nothing changed against $base: no tests to run" >&2 ;;
    docs) echo "documentation only: the suite is not run" >&2 ;;
    go)
        echo "tools/check.sh: only Go and the apps changed: the Go suite and ${GO_FILES[*]}, no Python logic tests" >&2
        apps || rc=1
        tools/test-go.sh || rc=1
        for f in "${GO_FILES[@]}"; do tools/test.sh "$f" || rc=1; done ;;
    python)
        apps || rc=1
        tools/suite.sh || rc=1 ;;
esac
[ -n "${run:-}" ] && { tools/leakcheck.py end "$run" || rc=1; }
exit "$rc"
