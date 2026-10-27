#!/usr/bin/env bash
# Merges pull request N only if it is ready (green required checks, no conflicts, auditor PASS for its head, ux live PASS for an app change).
#   tools/merge-ready.sh <PR number>
# The rules are in tools/merge_ready.py; there is no flag to skip one. Nothing is merged when anything cannot be read.
exec python3 "$(dirname "$0")/merge_ready.py" "$@"
