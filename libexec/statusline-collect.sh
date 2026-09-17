#!/usr/bin/env bash
# statusline-collect.sh - ocp's data source (design 01-infra.md section 4).
#
# Installed as statusLine.command in settings.json. Only one copy is needed:
# it derives its account name from CLAUDE_CONFIG_DIR, so every account can
# share the single copy in shared/.claude/.
#
#   ~/.orcapipe/accounts/<acct>/.claude  ->  <acct>
#
# Reads the statusLine JSON on stdin, extracts only the fields section 4 needs,
# writes them atomically, and prints a human-readable status line.

set -uo pipefail

RAW="$(cat)"

CFG="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
ACCT="$(basename "$(dirname "$CFG")")"
# Compare the whole path, not the basename. Comparing basenames meant an
# account literally named after the user (accounts/ron/.claude with
# $HOME=/Users/ron) was renamed to "default", so its snapshot landed in the
# wrong directory and every agent on it stayed permanently unassignable.
[ "$CFG" = "$HOME/.claude" ] && ACCT="default"

OCP="${OCP_HOME:-$HOME/.orcapipe}"
OUT_DIR="$OCP/accounts/$ACCT"
mkdir -p "$OUT_DIR" 2>/dev/null || exit 0

# Rate limits are account-wide, so every session on an account reports the same
# ones and the account file is the right home for them. Context usage,
# session_id and transcript_path are per SESSION: with two agents on one
# account they would overwrite each other, and the context trigger would judge
# one agent by the other's context. So an agent launched by `ocp launch` also
# writes its own copy, and the daemon reads context from there.
AGENT_DIR=""
if [ -n "${OCP_AGENT:-}" ]; then
  AGENT_DIR="$OCP/agents/$OCP_AGENT"
  mkdir -p "$AGENT_DIR" 2>/dev/null || AGENT_DIR=""
fi

# Never persist the raw stdin blob (KICKOFF rule 5 - it is a token-leak path).
# Take only the allowlisted fields from the section 4 table.
if ! command -v jq >/dev/null 2>&1; then
  # Without jq there is no snapshot at all, and the daemon would read the
  # silence as "usage unknown" forever. Say so on the status line; `ocp doctor`
  # fails on this too.
  printf '%s · NO jq — ocp collector disabled' "$ACCT"
  exit 0
fi

NOW="$(date +%s)"

SNAP="$(printf '%s' "$RAW" | jq -c --arg acct "$ACCT" --arg agent "${OCP_AGENT:-}" \
                                    --argjson now "$NOW" '
    {
      account:        $acct,
      agent:          (if $agent == "" then null else $agent end),
      collected_at:   $now,
      session_id:     .session_id,
      transcript_path:.transcript_path,
      model_id:       (.model.id // null),
      cwd:            (.workspace.current_dir // .cwd // null),
      pct_5h:         (.rate_limits.five_hour.used_percentage   // null),
      reset_5h:       (.rate_limits.five_hour.resets_at         // null),
      pct_7d:         (.rate_limits.seven_day.used_percentage   // null),
      reset_7d:       (.rate_limits.seven_day.resets_at         // null),
      pct_opus_7d:    (.rate_limits.seven_day_opus.used_percentage // null),
      reset_opus_7d:  (.rate_limits.seven_day_opus.resets_at        // null),
      ctx_pct:        (.context_window.used_percentage     // null),
      ctx_used:       (.context_window.current_usage       // null),
      ctx_size:       (.context_window.context_window_size // null),
      ctx_in:         (.context_window.total_input_tokens  // null),
      ctx_out:        (.context_window.total_output_tokens // null),
      cost_usd:       (.cost.total_cost_usd      // null),
      lines_added:    (.cost.total_lines_added   // null),
      lines_removed:  (.cost.total_lines_removed // null),
      exceeds_200k:   (.exceeds_200k_tokens // null),
      version:        (.version // null)
    }' 2>/dev/null)"

# A window this payload does not report is not a window at zero, and it is not
# this account going dark either. Claude Code reports a rate-limit window from
# what its own session has learned, so a session that has made no request since
# the window rolled over sends no block for it at all - measured: a session
# started now reports five_hour 0%, while a 14-hour-old idle session on the
# same account at the same moment reports no five_hour key.
#
# Writing that absence over the account file erased what an active session on
# the same account had just reported, and every reader then saw "usage
# unknown": the daemon handed off five healthy agents, each one costing a fold.
# So a payload without a window leaves the previous reading in place, as long
# as that reading still describes the window we are in. Once resets_at has
# passed it describes a window that is over, and it goes.
#
# rl_at is when the 5h figure was actually observed, and rl_carried says it did
# not come from this payload. Readers need both: collected_at only says the
# collector is alive.
if [ -n "$SNAP" ]; then
  PREV="$(cat "$OUT_DIR/status.json" 2>/dev/null)"
  printf '%s' "$PREV" | jq -e . >/dev/null 2>&1 || PREV=null
  SNAP="$(jq -cn --argjson new "$SNAP" --argjson prev "$PREV" --argjson now "$NOW" '
    reduce ("5h", "7d", "opus_7d") as $w (
      $new;
      ("pct_" + $w) as $pk | ("reset_" + $w) as $rk |
      if .[$pk] != null then .
      elif $prev != null and $prev[$pk] != null and (($prev[$rk] // 0) > $now)
        then .[$pk] = $prev[$pk] | .[$rk] = $prev[$rk]
           | .carried = ((.carried // []) + [$w])
      else . end
    )
    | (((.carried // []) | index("5h")) != null) as $c
    | .rl_carried = $c
    | .rl_at = (if $c then ($prev.rl_at // $prev.collected_at)
                elif .pct_5h != null then $now
                else null end)
    | del(.carried)' 2>/dev/null || printf '%s' "$SNAP")"
fi

# No session_id means this was not statusLine JSON. Never let a garbage payload
# overwrite a good snapshot. Ask jq, rather than grepping for a substring whose
# spelling depends on jq's own key order and quoting.
if [ -n "$SNAP" ] && printf '%s' "$SNAP" | jq -e '.session_id != null' >/dev/null 2>&1; then
  TMP="$(mktemp "$OUT_DIR/.status.XXXXXX")" || exit 0
  printf '%s\n' "$SNAP" > "$TMP" && mv -f "$TMP" "$OUT_DIR/status.json" || rm -f "$TMP"
  if [ -n "$AGENT_DIR" ]; then
    TMP2="$(mktemp "$AGENT_DIR/.status.XXXXXX")" || exit 0
    printf '%s\n' "$SNAP" > "$TMP2" && mv -f "$TMP2" "$AGENT_DIR/status.json" || rm -f "$TMP2"
  fi
fi

# Raw dump for probing only, and only when explicitly enabled.
if [ -n "${OCP_PROBE_RAW:-}" ]; then
  printf '%s' "$RAW" > "$OCP_PROBE_RAW"
fi

# --- status line output -------------------------------------------------
# If the user already had a status line, hand it the original payload and
# let it render, so installing ocp does not take their status line away.
if [ -n "${OCP_STATUSLINE_CHAIN:-}" ] && command -v "${OCP_STATUSLINE_CHAIN%% *}" >/dev/null 2>&1; then
  printf '%s' "$RAW" | ${OCP_STATUSLINE_CHAIN} && exit 0
fi

if [ -n "$SNAP" ]; then
  printf '%s' "$SNAP" | jq -r '
    def pct: if . == null then "?" else (.|floor|tostring) end;
    [ .account, "5h " + (.pct_5h|pct) + "%", "7d " + (.pct_7d|pct) + "%" ] | join(" · ")
  ' 2>/dev/null || printf '%s' "$ACCT"
else
  printf '%s' "$ACCT"
fi
exit 0
