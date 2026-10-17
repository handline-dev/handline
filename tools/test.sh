#!/usr/bin/env bash
# The tests, from the repo root: the generated contract files being current, then the Go suite (tools/test-go.sh, which takes
# go test's own arguments, so  tools/test.sh ./internal/reads -run X  runs one package).
cd "$(dirname "$0")/.." || exit 1
if [ $# -eq 0 ]; then
    python3 contract/generate.py --check && python3 tools/go_contract.py --check || exit 1
fi
exec tools/test-go.sh "$@"
