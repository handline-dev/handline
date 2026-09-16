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

OUT_DIR="${OCP_HOME:-$HOME/.orcapipe}/accounts/$ACCT"
mkdir -p "$OUT_DIR" 2>/dev/null || exit 0

# Never persist the raw stdin blob (KICKOFF rule 5 - it is a token-leak path).
# Take only the allowlisted fields from the section 4 table.
if ! command -v jq >/dev/null 2>&1; then
  # Without jq there is no snapshot at all, and the daemon would read the
  # silence as "usage unknown" forever. Say so on the status line; `ocp doctor`
  # fails on this too.
  printf '%s · NO jq — ocp collector disabled' "$ACCT"
  exit 0
fi

if true; then
  SNAP="$(printf '%s' "$RAW" | jq -c --arg acct "$ACCT" --argjson now "$(date +%s)" '
    {
      account:        $acct,
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
fi

# No session_id means this was not statusLine JSON. Never let a garbage payload
# overwrite a good snapshot. Ask jq, rather than grepping for a substring whose
# spelling depends on jq's own key order and quoting.
if [ -n "$SNAP" ] && printf '%s' "$SNAP" | jq -e '.session_id != null' >/dev/null 2>&1; then
  TMP="$(mktemp "$OUT_DIR/.status.XXXXXX")" || exit 0
  printf '%s\n' "$SNAP" > "$TMP" && mv -f "$TMP" "$OUT_DIR/status.json" || rm -f "$TMP"
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
