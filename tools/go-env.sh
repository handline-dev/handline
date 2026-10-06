#!/usr/bin/env bash
# Sourced by the Go scripts.
# A session's sandbox cannot write the default build cache; keep one that survives between runs.
cache="$(go env GOCACHE)"
if ! mkdir -p "$cache" 2>/dev/null || ! [ -w "$cache" ]; then
    export GOCACHE="${TMPDIR:-/tmp}/handline-gocache"
fi
