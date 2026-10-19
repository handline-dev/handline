#!/usr/bin/env bash
# The tests a change needs, from what it changes against <base> (default main): the one run the auditor makes on a sha.
#   tools/check.sh [<base>]
# - nothing changed, or documentation only (docs/, design/, *.md, but not contract/ or skill/): nothing.
# - anything else: the Go suite (tools/test-go.sh), and the contract's generated files being current.
# Either of the last two first runs the app checks: `pnpm turbo test typecheck lint` (turbo caches it) when a JS workspace
# file changed (apps/, packages/, the root JS files, contract/protocol.ts), else only the packages/app-data tests
# (6 s), whose guards fail on a contract method the apps' tables do not name.
# CHECK_PLAN=1 prints `kind=<none|docs|go> apps=<no|app-data|turbo>` and runs nothing.
# A failing step does not stop the ones after it, so one run shows every failure; the exit status is 1 if any failed.
# The tests run with TMPDIR and TMUX_TMPDIR in a directory of their own, and fail when they leave a process, a tmux
# server, a temp file or a write to the real protected store behind (tools/leakcheck.py).
cd "$(dirname "$0")/.." || exit 1
base="${1:-main}"
apps() {
    [ -d node_modules ] || { echo "tools/check.sh: no node_modules for the app checks: run  pnpm install --frozen-lockfile" >&2; return 1; }
    if grep -qE '^(apps|packages)/|^(package\.json|pnpm-lock\.yaml|pnpm-workspace\.yaml|turbo\.json|contract/package\.json|contract/protocol\.ts)$' <<< "$changed"; then
        local r=0
        pnpm turbo test typecheck lint --continue || r=1
        tools/dupes.sh || r=1
        return "$r"
    else
        pnpm --dir packages/app-data test
    fi
}
changed="$(git diff --name-only "$base...HEAD")" || exit 1      # from where HEAD left <base>: not what main did since
kind=none
while IFS= read -r path; do
    [ -n "$path" ] || continue
    case "$path" in
        contract/*|skill/*) kind=go; break ;;
        docs/*|design/*|*.md) [ "$kind" = none ] && kind=docs ;;
        *) kind=go ;;
    esac
done <<< "$changed"
# CHECK_PLAN=1: say what would run and stop (CI reads this to pick its jobs): kind is none, docs or go; apps is yes or no.
if [ -n "${CHECK_PLAN:-}" ]; then
    apps=no
    case "$kind" in go) grep -qE '^(apps|web|packages)/|^(package\.json|pnpm-lock\.yaml|pnpm-workspace\.yaml|turbo\.json|contract/package\.json|contract/protocol\.ts)$' <<< "$changed" && apps=turbo || apps=app-data ;; esac
    echo "kind=$kind apps=$apps"
    exit 0
fi
rc=0
if [ "$kind" = go ]; then
    # One heavy run at a time on this machine (tools/heavy-slot.sh): parallel suites starve each other into timeouts.
    [ -n "${HANDLINE_HEAVY_SLOT:-}" ] || exec tools/heavy-slot.sh "$PWD/tools/check.sh" "$@"
    run="$(tools/leakcheck.py begin)" || exit 1
    export TMPDIR="$run/tmp/" TMUX_TMPDIR="$run"
    # Stopped by a signal, the run still says what it left and takes its directory with it, once the step it was in has ended.
    stopped() { trap - INT TERM HUP; tools/leakcheck.py end --interrupted "$run"; exit "$1"; }
    trap 'stopped 130' INT; trap 'stopped 143' TERM; trap 'stopped 129' HUP
fi
case "$kind" in
    none) echo "nothing changed against $base: no tests to run" >&2 ;;
    docs) echo "documentation only: the suite is not run" >&2 ;;
    go)
        apps || rc=1
        python3 contract/generate.py --check || rc=1
        python3 tools/go_contract.py --check || rc=1
        python3 tools/go_schema.py --check || rc=1
        tools/test-go.sh || rc=1 ;;
esac
[ -n "${run:-}" ] && { tools/leakcheck.py end "$run" || rc=1; }
exit "$rc"
