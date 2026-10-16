#!/usr/bin/env bash
# Refuse a diff that adds to a Python file marked DEPRECATED (it is ported: change the Go) or adds a file under handline/.
# A change that only deletes passes. Usage: tools/check-deprecated.sh [<base>] (default main)
cd "$(dirname "$0")/.." || exit 1
base="${1:-main}"
rc=0
while IFS=$'\t' read -r added _ path; do
    case "$path" in handline/*.py) ;; *) continue ;; esac
    if ! git cat-file -e "$base:$path" 2>/dev/null; then
        echo "tools/check-deprecated.sh: $path is a new file under handline/: new work goes in Go" >&2; rc=1
    elif [ "$added" != 0 ] && git show "$base:$path" | head -1 | grep -q '^# DEPRECATED'; then
        echo "tools/check-deprecated.sh: $path is DEPRECATED (ported to Go) and this adds $added line(s): change the Go" >&2; rc=1
    fi
done < <(git diff --numstat --no-renames "$base...HEAD")
exit "$rc"
