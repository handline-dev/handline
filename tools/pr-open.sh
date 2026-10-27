#!/usr/bin/env bash
# Opens a pull request after the checks CI would refuse it for, run here first: the PR text (pr_checks.py body-file), the changed files
# (pr_checks.py files), the commit messages and added lines (localrefs.py), Prettier on the touched files, and ESLint (its size limits too) on each touched app or package.
#   tools/pr-open.sh --title "..." --body-file FILE [--base main] [--check]     --check runs the checks and opens nothing
set -uo pipefail
top="$(git rev-parse --show-toplevel)" || exit 1
cd "$top" || exit 1
title="" body="" base="main" check=""
while [ $# -gt 0 ]; do
    case "$1" in
        --title) title="$2"; shift 2 ;;
        --body-file) body="$2"; shift 2 ;;
        --base) base="$2"; shift 2 ;;
        --check) check=1; shift ;;
        *) echo "usage: tools/pr-open.sh --title T --body-file FILE [--base main] [--check]" >&2; exit 2 ;;
    esac
done
[ -n "$title" ] && [ -f "$body" ] || { echo "tools/pr-open.sh: --title and an existing --body-file are needed" >&2; exit 2; }

git fetch -q origin "$base" || exit 1
from="$(git merge-base "origin/$base" HEAD)" || exit 1
failed=0
step() { echo "== $1"; }

step "the PR text"
python3 tools/pr_checks.py body-file "$body" "$title" "$base" || failed=1

step "the changed files"
python3 tools/pr_checks.py files "$from" || failed=1

step "the commit messages and added lines"
python3 tools/localrefs.py log "origin/$base..HEAD" || failed=1
python3 tools/localrefs.py diff "$from...HEAD" || failed=1

touched="$(git diff --name-only --no-renames --diff-filter=AM "$from...HEAD")"
step "Prettier"
pretty="$(grep -E '^((packages|apps|web)/.*\.(ts|tsx|js|mjs|json|css)|[^/]*\.json)$' <<<"$touched")"
[ -z "$pretty" ] || xargs npx --yes prettier@3.9.9 --check --log-level warn <<<"$pretty" || failed=1

step "ESLint"
for dir in $(grep -oE '^(apps|packages)/[^/]+' <<<"$touched" | sort -u); do
    [ -f "$dir/eslint.config.js" ] || continue
    files="$(grep -E "^$dir/.*\.(ts|tsx|js|mjs)$" <<<"$touched" | sed "s#^$dir/##")"
    [ -z "$files" ] || (cd "$dir" && xargs npx eslint --quiet <<<"$files") || failed=1
done

if [ "$failed" = 1 ]; then
    echo "tools/pr-open.sh: fix the above first; nothing was opened" >&2
    exit 1
fi
[ -n "$check" ] && { echo "all checks pass"; exit 0; }
git push -q -u origin HEAD && gh pr create --base "$base" --title "$title" --body-file "$body"
