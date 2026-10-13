#!/usr/bin/env bash
# Sourced by the Go scripts.
# A session's sandbox cannot write the default build cache; keep one that survives between runs.
cache="$(go env GOCACHE)"
if ! mkdir -p "$cache" 2>/dev/null || ! [ -w "$cache" ]; then
    export GOCACHE="${TMPDIR:-/tmp}/handline-gocache"
fi
# Once a day, drop the cache files no build used for two days (go marks an entry used by its mtime; its own trim waits five): the
# worktrees write about 24 GB a day, and each new file is one more event for fseventsd. In the background: the walk takes minutes.
cache="$(go env GOCACHE)"
if [ -z "$(find "$cache/handline-pruned" -mtime -1 2>/dev/null)" ] && touch "$cache/handline-pruned" 2>/dev/null; then
    (find "$cache" -mindepth 2 -type f -mtime +2 -delete >/dev/null 2>&1 &)
fi
