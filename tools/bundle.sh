#!/usr/bin/env bash
# Builds one bundle branch from the pull requests that are ready to merge (3 or more), and with --push opens its PR. Merges nothing.
#   tools/bundle.sh [--without N ...] [--push]
# The rules are in tools/bundle.py; readiness is tools/merge_ready.py's.
exec python3 "$(dirname "$0")/bundle.py" "$@"
