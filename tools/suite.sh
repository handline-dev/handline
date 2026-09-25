#!/usr/bin/env bash
# Run every tests/**/test_*.py from the repo root, four at a time, and print
# one verdict per file. Exit 1 if any file exits non-zero or its last line is
# not "N passed, 0 failed" - so a file that crashes before it counts anything
# cannot pass by printing nothing. test_ocp.py is the same suite run in one
# process, so it is left out here.
set -u
cd "$(dirname "$0")/.."

# test_bridge_textual needs `textual`: use the first python that has it.
textual_py=python3
for py in python3 "$HOME/.orcapipe/venv/bin/python3"; do
  if "$py" -c 'import textual' 2>/dev/null; then textual_py=$py; break; fi
done

out=$(mktemp -d "${TMPDIR:-/tmp}/suite.XXXXXX") || exit 1
trap 'rm -rf "$out"' EXIT

run_one() {
  local f=$1 py=python3 log
  [ "$(basename "$f")" = test_bridge_textual.py ] && py=$2
  log="$3/$(echo "$f" | tr / _).log"
  "$py" "$f" >"$log" 2>&1
  echo $? >"$log.rc"
}
export -f run_one

find tests -name 'test_*.py' ! -name test_ocp.py | sort |
  xargs -P 4 -I{} bash -c 'run_one "$1" "$2" "$3"' _ {} "$textual_py" "$out"

status=0
for f in $(find tests -name 'test_*.py' ! -name test_ocp.py | sort); do
  log="$out/$(echo "$f" | tr / _).log"
  rc=$(cat "$log.rc" 2>/dev/null || echo missing)
  last=$(tail -n 1 "$log" 2>/dev/null)
  if [ "$rc" = 0 ] && [[ "$last" =~ ^[0-9]+\ passed,\ 0\ failed$ ]]; then
    printf 'ok    %s  %s\n' "$f" "$last"
  else
    status=1
    printf 'FAIL  %s  exit %s  last line: %s\n' "$f" "$rc" "$last"
    grep -E '^\s*FAILED' "$log" | sort -u | head -20
  fi
done
exit $status
