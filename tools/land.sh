#!/usr/bin/env bash
# Land a branch on main without main's checkout ever holding a half-merged tree.
#
# Every agent's `hl` runs from the main checkout (bin/ocp is a symlink into
# it), so `git merge` there - conflict markers in .py files, minutes of a
# suite run on a merge nobody has finished - breaks ocp for everyone (T390).
# The merge and the suite run in a scratch worktree instead; main's checkout
# only ever moves by a fast-forward to a commit that is already finished.
#
#   tools/land.sh [--test] [--note <text>] <sha> <merge message>
#   tools/land.sh --check [--passed] <sha> [<sha>...]
#
# --check is the merger's own test, run in its sandbox: it merges each <sha> in turn into main in a
# throwaway clone (reads main's repo, writes only $TMPDIR), runs the suite once on the result, and prints
# the tree after each merge, one line per sha. Exit 1 on a conflict, personal data or a local id in a
# merge (tools/localrefs.py), or a red suite. The daemon lands only a merge with that tree, and checks
# the message it was given itself. A merge's tree does not depend on its message, so the daemon landing
# the shas in this order reaches the same trees. When every path the merges change is documentation
# (docs-only below), the suite is not run.
# --passed says the auditor recorded a PASS for a single <sha>: when the merge's tree is then <sha>'s own
# tree (main has not moved past it), the suite the auditor ran is the suite of this merge and is not
# rerun. A train's last merge is never one sha's tree, so --passed changes nothing there.
#
# --test runs tools/suite.sh on the merge result first; red lands nothing.
# The merge result is also read by tools/localrefs.py: a diff or a message (this one
# included) with personal data or a local task/chat id lands nothing. The ids a merger
# wants to keep go in --note, which is appended to $OCP_HOME/landings.log, never to git.
# Exit 0: landed, prints the merge sha. Exit 1: nothing changed in main's
# checkout - a conflict (the files are named), a red suite, or main moved
# meanwhile (run it again).
set -euo pipefail

# Documentation no code or test reads: contract/ is read by the conformance tests, skill/ ships.
docs_only() {
    local path any=0
    while IFS= read -r path; do
        any=1
        case "$path" in
            contract/*|skill/*) return 1 ;;
            docs/*|design/*|*.md) ;;
            *) return 1 ;;
        esac
    done
    [ "$any" = 1 ]
}

if [ "${1:-}" = "--check" ]; then
    shift
    passed=0
    if [ "${1:-}" = "--passed" ]; then passed=1; shift; fi
    [ $# -ge 1 ] || { echo "usage: land.sh --check [--passed] <sha> [<sha>...]" >&2; exit 2; }
    shas=()
    for s in "$@"; do shas+=("$(git rev-parse --verify "$s^{commit}")"); done
    main_dir="$(git worktree list --porcelain | awk '/^worktree /{d=substr($0,10)} /^branch refs\/heads\/main$/{print d; exit}')"
    [ -n "$main_dir" ] || { echo "no worktree has main checked out" >&2; exit 1; }
    work="$(mktemp -d "${TMPDIR:-/tmp}/landcheck.XXXXXX")"
    trap 'rm -rf "$work"' EXIT
    git clone -q --shared "$main_dir" "$work" >/dev/null 2>&1
    git -C "$work" checkout -q main >/dev/null 2>&1
    base="$(git -C "$work" rev-parse HEAD)"
    trees=()
    for sha in "${shas[@]}"; do
        if ! git -C "$work" -c user.name=land -c user.email=land@localhost merge --no-ff -q -m check "$sha" >/dev/null 2>&1; then
            echo "${sha:0:8} conflicts with main in: $(git -C "$work" diff --name-only --diff-filter=U | tr '\n' ' ')" >&2
            exit 1
        fi
        if [ -f "$work/tools/localrefs.py" ]; then
            # The clone's main is the branch the merge just moved, so the range starts at the merge's first parent.
            if ! (cd "$work" && python3 tools/localrefs.py diff 'HEAD^..HEAD' && python3 tools/localrefs.py log 'HEAD^..HEAD') >&2; then
                echo "personal data or a local id in the merge of ${sha:0:8}; send the branch back to its owner" >&2
                exit 1
            fi
        fi
        trees+=("$(git -C "$work" rev-parse 'HEAD^{tree}')")
    done
    if [ "$passed" = 1 ] && [ ${#shas[@]} -eq 1 ] && [ "${trees[0]}" = "$(git -C "$work" rev-parse "${shas[0]}^{tree}")" ]; then
        echo "land.sh: the merge is the tree the auditor passed; the suite is not rerun" >&2
    elif git -C "$work" diff --name-only "$base" HEAD | docs_only; then
        echo "documentation only: the suite is not run" >&2
    else
        "$work/tools/suite.sh" >&2 || { echo "the suite is red on the merge result" >&2; exit 1; }
    fi
    printf '%s\n' "${trees[@]}"
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
