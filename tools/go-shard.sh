#!/usr/bin/env bash
# The packages of one CI shard of the Go suite, one `go test` pattern per line: `tools/go-shard.sh <shard>`. `other` is every
# package no named shard lists, so a new package is tested without a line here. Sized by their run time, the long pole alone.
cd "$(dirname "$0")/../go" || exit 1
named() {
    case "$1" in
    compose) echo ./internal/compose ;;
    cli) printf './cmd/%s\n' hl heavyslot handlined ;;
    daemon) printf './internal/%s\n' daemon lifecycle tick supervisor session sessionrpc reads taskwrites usage usagepoll ;;
    terminal) printf './internal/%s\n' term termchan testkill ;;
    *) return 1 ;;
    esac
}
if [ "${1:-}" = other ]; then
    all="$(for s in compose cli daemon terminal; do named $s; done)"
    go list ./... | sed 's#^github.com/handline/handline/go#.#' | grep -vxF "$all"
else
    named "${1:-}" || { echo "usage: tools/go-shard.sh compose|cli|daemon|terminal|other" >&2; exit 2; }
fi
