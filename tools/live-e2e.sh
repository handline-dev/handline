#!/usr/bin/env bash
# The live end-to-end test: a real Claude Code on a real account, through the live daemon. Spends tokens; never part of
# tools/suite.sh or check.sh. Run it from the founder's terminal: tools/live-e2e.sh --account NAME --agent NAME [--json]
here="$(cd "$(dirname "$0")" && pwd)"
HANDLINE_ALLOW_LIVE=1 exec python3 "$here/live_e2e.py" "$@"
