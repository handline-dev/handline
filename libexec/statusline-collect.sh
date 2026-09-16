#!/usr/bin/env bash
# statusline-collect.sh — ocp 수집기 (§4 데이터 출처)
#
# settings.json 의 statusLine.command 로 지정한다. 계정마다 한 벌이 아니라
# shared 에 한 벌만 두면 된다 — 계정명을 CLAUDE_CONFIG_DIR 에서 유도하기 때문이다.
#
#   ~/.orcapipe/accounts/<acct>/.claude  ->  <acct>
#
# stdin 으로 오는 statusLine JSON 에서 §4 표의 필드만 뽑아 원자적으로 저장하고,
# 사람이 읽을 상태줄 문자열을 stdout 으로 낸다.

set -uo pipefail

RAW="$(cat)"

CFG="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
ACCT="$(basename "$(dirname "$CFG")")"
[ "$ACCT" = "$(basename "$HOME")" ] && ACCT="default"

OUT_DIR="${OCP_HOME:-$HOME/.orcapipe}/accounts/$ACCT"
mkdir -p "$OUT_DIR" 2>/dev/null || exit 0

# ── 원시 stdin 을 통째로 저장하지 않는다 (KICKOFF §1.5 — 토큰 유출 경로).
#    §4 표에 있는 필드만 allowlist 로 뽑는다.
if command -v jq >/dev/null 2>&1; then
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
      ctx_pct:        (.context_window.used_percentage   // null),
      ctx_used:       (.context_window.current_usage     // null),
      ctx_size:       (.context_window.context_window_size // null),
      ctx_in:         (.context_window.total_input_tokens  // null),
      ctx_out:        (.context_window.total_output_tokens // null),
      cost_usd:       (.cost.total_cost_usd // null),
      lines_added:    (.cost.total_lines_added // null),
      lines_removed:  (.cost.total_lines_removed // null),
      exceeds_200k:   (.exceeds_200k_tokens // null),
      version:        (.version // null)
    }' 2>/dev/null)"
else
  SNAP=""
fi

# session_id 가 없으면 statusLine JSON 이 아니다 — 좋은 파일을 덮지 않는다.
if [ -n "$SNAP" ] && printf '%s' "$SNAP" | grep -q '"session_id":"'; then
  TMP="$(mktemp "$OUT_DIR/.status.XXXXXX")" || exit 0
  printf '%s\n' "$SNAP" > "$TMP" && mv -f "$TMP" "$OUT_DIR/status.json" || rm -f "$TMP"
fi

# ── 프로브용 원시 덤프: 명시적으로 켰을 때만. 평시엔 절대 남기지 않는다.
if [ -n "${OCP_PROBE_RAW:-}" ]; then
  printf '%s' "$RAW" > "$OCP_PROBE_RAW"
fi

# ── 상태줄 출력 ---------------------------------------------------------
if [ -n "${OCP_STATUSLINE_CHAIN:-}" ] && command -v "${OCP_STATUSLINE_CHAIN%% *}" >/dev/null 2>&1; then
  # 사용자가 쓰던 상태줄이 있으면 그쪽에 원본을 그대로 넘기고 출력을 위임한다.
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
