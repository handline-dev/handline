#!/usr/bin/env bash
# Land a branch on main without main's checkout ever holding a half-merged tree.
#
# Every agent's `ocp` runs from the main checkout (bin/ocp is a symlink into
# it), so `git merge` there - conflict markers in .py files, minutes of a
# suite run on a merge nobody has finished - breaks ocp for everyone (T390).
# The merge and the suite run in a scratch worktree instead; main's checkout
# only ever moves by a fast-forward to a commit that is already finished.
#
#   tools/land.sh [--test] <sha> <merge message>
#
# --test runs tools/suite.sh on the merge result first; red lands nothing.
# Exit 0: landed, prints the merge sha. Exit 1: nothing changed in main's
# checkout - a conflict (the files are named), a red suite, or main moved
# meanwhile (run it again).
set -euo pipefail

run_tests=0
if [ "${1:-}" = "--test" ]; then run_tests=1; shift; fi
[ $# -eq 2 ] || { echo "usage: land.sh [--test] <sha> <merge message>" >&2; exit 2; }
sha="$(git rev-parse --verify "$1^{commit}")"
msg="$2"

# The checkout that has main checked out, wherever this was run from.
main_dir="$(git worktree list --porcelain | awk '/^worktree /{d=substr($0,10)} /^branch refs\/heads\/main$/{print d; exit}')"
[ -n "$main_dir" ] || { echo "no worktree has main checked out" >&2; exit 1; }

work="$(mktemp -d "${TMPDIR:-/tmp}/land.XXXXXX")"
cleanup() { git -C "$main_dir" worktree remove --force "$work" >/dev/null 2>&1 || rm -rf "$work"; }
trap cleanup EXIT

git -C "$main_dir" worktree add --detach "$work" main >/dev/null 2>&1
if ! git -C "$work" merge --no-ff -q -m "$msg" "$sha" >/dev/null 2>&1; then
    echo "conflicts with main in: $(git -C "$work" diff --name-only --diff-filter=U | tr '\n' ' ')" >&2
    echo "main's checkout is untouched; send the branch back to its owner" >&2
    exit 1
fi
if [ "$run_tests" = 1 ] && ! "$work/tools/suite.sh" >&2; then
    echo "the suite is red on the merge result; nothing landed" >&2
    exit 1
fi
merged="$(git -C "$work" rev-parse HEAD)"
if ! git -C "$main_dir" merge --ff-only -q "$merged" >/dev/null 2>&1; then
    echo "main moved while this ran (or its checkout is dirty); nothing landed - run it again" >&2
    exit 1
fi
echo "$merged"
