#!/usr/bin/env bash
# The live end-to-end test: a real Claude Code on the account with the most headroom, through a test daemon of its own. Spends
# tokens, so it runs only with HANDLINE_LIVE=1, and never from check.sh or CI:
#   HANDLINE_LIVE=1 tools/live-e2e.sh [--daemon BINARY] [--json] [--keep]
here="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$here/live_e2e.py" "$@"
