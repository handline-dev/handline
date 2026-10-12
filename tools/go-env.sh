#!/usr/bin/env bash
# Sourced by the Go scripts.
# A session's sandbox cannot write the default build cache; keep one that survives between runs.
cache="$(go env GOCACHE)"
if ! mkdir -p "$cache" 2>/dev/null || ! [ -w "$cache" ]; then
    export GOCACHE="${TMPDIR:-/tmp}/handline-gocache"
fi
# Once a week, drop the cache files no build used for two days (go marks an entry used by its mtime): the cache otherwise grows
# to tens of GB, and each new file is one more event for fseventsd.
cache="$(go env GOCACHE)"
if [ -z "$(find "$cache/handline-pruned" -mtime -7 2>/dev/null)" ] && touch "$cache/handline-pruned" 2>/dev/null; then
    find "$cache" -type f -mtime +2 ! -name handline-pruned -delete 2>/dev/null
fi
