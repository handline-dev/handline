#!/usr/bin/env bash
# No new copies of Go code. dupl (pinned as a tool in go/go.mod) finds clones of at least TOKENS syntax tokens; each copy is a
# fragment, and the fragments in the code and in its tests may not number more than today's. Fewer fails too, asking for the
# lower number, so a cleanup lowers the bar with it and the bar stays at today's level.
#   tools/check-go-dupl.sh [module dir]   (default go/)
#   tools/check-go-dupl.sh --list [dir]   the clone groups, most copied lines first: the cleanup queue
here="$(cd "$(dirname "$0")" && pwd)"
TOKENS=50
CODE_MAX=57
TESTS_MAX=58

list=
[ "${1:-}" = "--list" ] && { list=1; shift; }
cd "${1:-$here/../go}" || exit 1

# files code|tests: the module's Go files of that kind, in byte order.
files() {
    if [ "$1" = tests ]; then
        find . -name '*_test.go' -not -path './vendor/*'
    else
        find . -name '*.go' -not -name '*_test.go' -not -path './vendor/*'
    fi | LC_ALL=C sort
}

if [ -n "$list" ]; then
    # One line per group: copied lines (each copy past the first), copies, then where each copy is.
    for kind in code tests; do
        files "$kind" | go tool dupl -files -t "$TOKENS" | awk -v kind="$kind" '
            function flush() { if (n) printf "%d\t%d\t%s\t%s\n", extra, n, kind, where }
            /^found [0-9]+ clones:/ { flush(); n = 0; extra = 0; where = ""; next }
            /^  / { split($1, a, ":"); split(a[2], r, ","); if (n++) extra += r[2] - r[1] + 1; where = where (where ? " " : "") $1 }
            END { flush() }'
    done | sort -t "$(printf '\t')" -k1,1nr -k4,4 | awk -F '\t' '{ printf "%3d. %d lines, %d copies (%s): %s\n", NR, $1, $2, $3, $4 }'
    exit 0
fi

rc=0
for kind in code tests; do
    max=CODE_MAX
    [ "$kind" = tests ] && max=TESTS_MAX
    out="$(files "$kind" | go tool dupl -files -plumbing -t "$TOKENS")" || { echo "FAIL  dupl did not run over the $kind" >&2; exit 1; }
    n="$(awk 'NF { print $1 }' <<<"$out" | sort -u | wc -l | tr -d ' ')"
    if [ "$n" -gt "${!max}" ]; then
        echo "FAIL  Go $kind: $n copied fragments of $TOKENS+ tokens, more than the $((${!max})) allowed: a new copy. Share the code instead:" >&2
        echo "$out" >&2
        rc=1
    elif [ "$n" -lt "${!max}" ]; then
        echo "FAIL  Go $kind: $n copied fragments, fewer than $((${!max})): set $max=$n in tools/check-go-dupl.sh, so the bar stays here" >&2
        rc=1
    fi
done
exit "$rc"
