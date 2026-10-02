#!/usr/bin/env bash
# The whole suite from the repo root: tests/test_ocp.py runs every
# tests/*/test_*.py in a process of its own and exits 1 on any failure.
# One path with every other test run: tools/test.sh (T946).
exec "$(dirname "$0")/test.sh" "$@"
