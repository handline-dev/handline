#!/usr/bin/env bash
# probe.sh — Phase 0 가정 검증
#
#   ./probe.sh h      ~/.claude.json 이 CLAUDE_CONFIG_DIR 을 따라가는가
#   ./probe.sh i      statusLine stdin 에 필요한 필드가 오는가
#   ./probe.sh j0     Orca 가 claude 를 래핑하는가 / 자기 statusLine 을 강제하는가
#   ./probe.sh j4     worktree .envrc(direnv) 가 Orca 에이전트에 먹히는가
#   ./probe.sh all
#
# h, i, j0 은 계정 1개면 된다. j4 는 Orca worktree 가 필요하다.

set -uo pipefail
PROBE_ROOT="${PROBE_ROOT:-/tmp/ocp-probe}"
HERE="$(cd "$(dirname "$0")" && pwd)"
pass(){ printf '\033[32m✔ PASS\033[0m  %s\n' "$*"; }
fail(){ printf '\033[31m✘ FAIL\033[0m  %s\n' "$*"; }
info(){ printf '        %s\n' "$*"; }
head(){ printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

# ---------------------------------------------------------------- H
probe_h(){
  head "H · ~/.claude.json 이 CLAUDE_CONFIG_DIR 을 따라가는가"
  local d="$PROBE_ROOT/h/.claude"
  rm -rf "$PROBE_ROOT/h"; mkdir -p "$d"
  local before_mtime=""
  [ -f "$HOME/.claude.json" ] && before_mtime="$(stat -c %Y "$HOME/.claude.json" 2>/dev/null || stat -f %m "$HOME/.claude.json")"

  CLAUDE_CONFIG_DIR="$d" claude -p "reply with the single word ok" >/dev/null 2>&1

  info "생성된 것:"; find "$PROBE_ROOT/h" -maxdepth 2 | sed 's/^/          /'

  if [ -f "$d/.claude.json" ] || [ -f "$PROBE_ROOT/h/.claude.json" ]; then
    pass "프로필 안에 .claude.json 이 생겼다 — 격리 완전"
  else
    local after_mtime=""
    [ -f "$HOME/.claude.json" ] && after_mtime="$(stat -c %Y "$HOME/.claude.json" 2>/dev/null || stat -f %m "$HOME/.claude.json")"
    if [ -n "$after_mtime" ] && [ "$before_mtime" != "$after_mtime" ]; then
      fail "~/.claude.json 이 수정됐다 — 이 파일은 전역 공유다"
      info "→ 계정별로 이 파일을 심볼릭/스왑하는 장치가 추가로 필요하다"
      info "→ 동시 실행 시 이 파일이 담는 것(auth 일부, user-scope MCP)은 공유된다"
    else
      info "판정 보류 — 프로필에도 없고 홈도 안 바뀌었다. 수동 확인 필요"
    fi
  fi
}

# ---------------------------------------------------------------- I
probe_i(){
  head "I · statusLine stdin 필드"
  local d="$PROBE_ROOT/i/.claude"
  rm -rf "$PROBE_ROOT/i"; mkdir -p "$d"
  cat > "$d/settings.json" <<JSON
{ "statusLine": { "type": "command", "command": "$HERE/statusline-collect.sh" } }
JSON
  OCP_HOME="$PROBE_ROOT/i/orcapipe" CLAUDE_CONFIG_DIR="$d" \
    claude -p "reply with the single word ok" >/dev/null 2>&1

  local f; f="$(find "$PROBE_ROOT/i/orcapipe" -name status.json 2>/dev/null | head -1)"
  if [ -z "$f" ]; then
    fail "status.json 이 안 만들어졌다 — statusLine 이 실행되지 않았다"
    info "→ disableAllHooks / allowManagedHooksOnly 를 확인하라"
    return
  fi
  pass "수집기 동작. $f"
  info "최상위 키:"; jq -r 'keys[]' "$f" 2>/dev/null | sed 's/^/          /'
  for k in rate_limits prompt_cache session_id transcript_path cost; do
    if jq -e ".$k" "$f" >/dev/null 2>&1; then pass "$k 있음"; else fail "$k 없음"; fi
  done
  info "컨텍스트 사용률로 쓸 만한 필드 탐색:"
  jq -r 'paths(scalars) as $p | select($p|map(tostring)|join(".")|test("context|token|window";"i"))
         | ($p|map(tostring)|join(".")) + " = " + (getpath($p)|tostring)' "$f" 2>/dev/null \
    | sed 's/^/          /' || info "          (없음 — §8.1 트리거를 transcript 집계로 대체해야 한다)"
}

# ---------------------------------------------------------------- J0
probe_j0(){
  head "J0 · Orca 가 claude 를 래핑하거나 자기 statusLine 을 강제하는가"
  info "which -a claude:"; which -a claude 2>/dev/null | sed 's/^/          /'
  local n; n="$(which -a claude 2>/dev/null | wc -l)"
  if [ "$n" -gt 1 ]; then
    fail "claude 가 여러 개다 — PATH 앞쪽이 래퍼일 수 있다"
  else
    pass "claude 단일 경로"
  fi
  info ""
  info "Orca 터미널 '안에서' 아래를 직접 실행해 비교하라:"
  info "  env | grep -iE 'claude|anthropic|orca'"
  info "  ps -o args= -p \$\$ ; ps -o args= \$(pgrep -f 'claude' | head -1)"
  info ""
  info "판정: claude 프로세스의 argv 에 --settings / --status-line 류가 보이면"
  info "      Orca 가 statusLine 을 강제하는 것 → §4 수집기가 죽는다 (J0 거짓)"
}

# ---------------------------------------------------------------- J4
probe_j4(){
  head "J4 · worktree .envrc(direnv) 가 Orca 에이전트에 먹히는가"
  command -v direnv >/dev/null || { fail "direnv 미설치 — brew/apt 로 설치 후 재시도"; return; }
  local WT="${ORCA_WT_PATH:-}"
  [ -z "$WT" ] && { fail "ORCA_WT_PATH 에 에이전트 worktree 절대경로를 지정하라"; return; }
  local TARGET="${PROBE_J4_CONFIG_DIR:-$PROBE_ROOT/j4/.claude}"
  mkdir -p "$TARGET"

  cat > "$WT/.envrc" <<ENVRC
export CLAUDE_CONFIG_DIR="$TARGET"
ENVRC
  (cd "$WT" && direnv allow . >/dev/null 2>&1)
  pass ".envrc 설치: $WT/.envrc  ->  $TARGET"
  info ""
  info "이제 Orca 에서 이 worktree 에 에이전트를 평소처럼 띄우고, 그 터미널에서:"
  info "  echo \$CLAUDE_CONFIG_DIR"
  info "→ $TARGET 가 나오면 J4 참. 비어 있으면 거짓"
  info ""
  info "J4 거짓일 때 다음 시도:"
  info "  orca skills get orca-per-workspace-env      # orca.yaml environmentRecipes 형식 확인"
  info "  → 거기서도 env 주입이 안 되면 tmux 하이브리드로 복귀 (설계서 부록)"
  info ""
  info "확정 판정 — 에이전트를 띄운 뒤 아래로 transcript 위치를 본다:"
  info "  find $TARGET/projects -name '*.jsonl' -newermt '-5 minutes' | wc -l"
  info "  find \$HOME/.claude/projects -name '*.jsonl' -newermt '-5 minutes' | wc -l"
}

case "${1:-all}" in
  h) probe_h;; i) probe_i;; j0) probe_j0;; j1) probe_j1;; j23) probe_j23;;
  all) probe_h; probe_i; probe_j0; probe_j4;;
  *) sed -n '2,14p' "$0";;
esac
