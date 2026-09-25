#!/usr/bin/env bash
# The whole suite from the repo root: tests/test_ocp.py runs every
# tests/*/test_*.py in a process of its own and exits 1 on any failure.
cd "$(dirname "$0")/.." || exit 1
exec python3 tests/test_ocp.py "$@"
