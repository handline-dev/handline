#!/usr/bin/env bash
# Land a branch on main without main's checkout ever holding a half-merged tree.
#
# Every agent's `ocp` runs from the main checkout (bin/ocp is a symlink into
# it), so `git merge` there - conflict markers in .py files, minutes of a
# suite run on a merge nobody has finished - breaks ocp for everyone (T390).
# The merge and the suite run in a scratch worktree instead; main's checkout
# only ever moves by a fast-forward to a commit that is already finished.
#
#   tools/land.sh [--test] [--note <text>] <sha> <merge message>
#   tools/land.sh --check <sha>
#
# --check is the merger's own test, run in its sandbox: it merges <sha> into main in a throwaway
# clone (reads main's repo, writes only $TMPDIR), runs the suite, and prints the tree of the merge it
# tested. Exit 1 on a conflict or a red suite. The daemon lands only a merge with that tree.
#
# --test runs tools/suite.sh on the merge result first; red lands nothing.
# The merge result is also read by tools/localrefs.py: a diff or a message (this one
# included) with personal data or a local task/chat id lands nothing. The ids a merger
# wants to keep go in --note, which is appended to $OCP_HOME/landings.log, never to git.
# Exit 0: landed, prints the merge sha. Exit 1: nothing changed in main's
# checkout - a conflict (the files are named), a red suite, or main moved
# meanwhile (run it again).
set -euo pipefail

if [ "${1:-}" = "--check" ]; then
    [ $# -eq 2 ] || { echo "usage: land.sh --check <sha>" >&2; exit 2; }
    sha="$(git rev-parse --verify "$2^{commit}")"
    main_dir="$(git worktree list --porcelain | awk '/^worktree /{d=substr($0,10)} /^branch refs\/heads\/main$/{print d; exit}')"
    [ -n "$main_dir" ] || { echo "no worktree has main checked out" >&2; exit 1; }
    work="$(mktemp -d "${TMPDIR:-/tmp}/landcheck.XXXXXX")"
    trap 'rm -rf "$work"' EXIT
    git clone -q --shared "$main_dir" "$work" >/dev/null 2>&1
    git -C "$work" checkout -q main >/dev/null 2>&1
    if ! git -C "$work" -c user.name=land -c user.email=land@localhost merge --no-ff -q -m check "$sha" >/dev/null 2>&1; then
        echo "conflicts with main in: $(git -C "$work" diff --name-only --diff-filter=U | tr '\n' ' ')" >&2
        exit 1
    fi
    "$work/tools/suite.sh" >&2 || { echo "the suite is red on the merge result" >&2; exit 1; }
    git -C "$work" rev-parse 'HEAD^{tree}'
    exit 0
fi

run_tests=0
note=""
while [ $# -gt 0 ]; do
    case "$1" in
        --test) run_tests=1; shift ;;
        --note) [ $# -ge 2 ] || { echo "land.sh: --note needs a text" >&2; exit 2; }; note="$2"; shift 2 ;;
        *) break ;;
    esac
done
[ $# -eq 2 ] || { echo "usage: land.sh [--test] [--note <text>] <sha> <merge message>" >&2; exit 2; }
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
if [ -f "$work/tools/localrefs.py" ]; then
    if ! (cd "$work" && python3 tools/localrefs.py diff main..HEAD && python3 tools/localrefs.py log main..HEAD) >&2; then
        echo "personal data or a local id in the merge result; nothing landed - send the branch back to its owner" >&2
        exit 1
    fi
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
if [ -n "$note" ]; then
    log="${OCP_HOME:-$HOME/.orcapipe}/landings.log"
    printf '%s\t%s\t%s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$merged" "$note" >> "$log" 2>/dev/null \
        || echo "land.sh: landed, but could not write the note to $log" >&2
fi
echo "$merged"
