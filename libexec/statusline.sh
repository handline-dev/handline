#!/usr/bin/env bash
# statusline.sh - what ocp shows the human in an agent's terminal (D11).
#
# Installed as statusLine.command in settings.json. One copy is shared by every
# account: it needs nothing from the payload, only OCP_AGENT from the session's
# own environment.
#
# This used to be ocp's data source. It is not any more - session identity,
# context, cost and rate limits are all pulled now (`ocp/collect.py`,
# `ocp/usage_api.py`) - so nothing here parses stdin, writes a snapshot, or
# needs jq.
#
# It only prints a line someone else wrote. Claude Code re-runs this command on
# a refresh interval for every live session, which at nineteen agents is about
# a hundred runs a minute, so the work that composes the line belongs anywhere
# but here (`ocp/statusline.py` explains the measurement).

set -uo pipefail

# Read the payload without keeping it. Draining matters - the writer gets EPIPE
# otherwise - and KICKOFF rule 5 is why it is never written anywhere.
RAW="$(cat)"

# If the user had a status line of their own, hand it the payload untouched and
# let it render. Installing ocp does not take someone's status line away.
if [ -n "${OCP_STATUSLINE_CHAIN:-}" ] && command -v "${OCP_STATUSLINE_CHAIN%% *}" >/dev/null 2>&1; then
  printf '%s' "$RAW" | ${OCP_STATUSLINE_CHAIN} && exit 0
fi

OCP="${OCP_HOME:-$HOME/.orcapipe}"
LINE="$OCP/agents/${OCP_AGENT:-}/line.txt"

if [ -n "${OCP_AGENT:-}" ] && [ -r "$LINE" ]; then
  # `head -c` bounds what a corrupted file can paint over the terminal, and
  # `tr` keeps it to the one line the slot is.
  head -c 2000 "$LINE" | tr -d '\r' | head -n 1
  exit 0
fi

# No line yet - a session that started before the daemon wrote one, or one
# running outside ocp. Say which agent and account this is, which is the whole
# of what we know here.
CFG="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
ACCT="$(basename "$(dirname "$CFG")")"
[ "$CFG" = "$HOME/.claude" ] && ACCT="default"
printf '%s' "${OCP_AGENT:-$ACCT}"
exit 0
