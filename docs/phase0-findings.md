# Phase 0 판정 근거

측정 환경: macOS 15 (Darwin 25.5.0) · Claude Code **2.1.273** · Orca **1.4.203** · 2026-09-16
모든 프로브는 `--model sonnet` 으로 돌렸다.

---

## H · `~/.claude.json` 이 `CLAUDE_CONFIG_DIR` 을 따라가는가 → **참**

```bash
BEFORE=$(stat -f %m ~/.claude.json)
CLAUDE_CONFIG_DIR=/tmp/ocp-probe/h/.claude claude --model sonnet -p "reply with the single word ok"
```

출력: `Not logged in · Please run /login`

```
/tmp/ocp-probe/h/.claude/.claude.json                       ← 프로필 안에 생겼다
/tmp/ocp-probe/h/.claude/backups/.claude.json.backup...
/tmp/ocp-probe/h/.claude/projects/-Users-ron-workspaces/
~/.claude.json mtime: 1789526410 → 1789526410  (변화 없음)
```

`.claude.json` 은 `CLAUDE_CONFIG_DIR` 안에 생성되고 홈의 것은 건드리지 않는다. **격리 완전.**

### 덤으로 확인된 것 — credential 도 따라간다 (설계서가 열어둔 위험)

빈 `CLAUDE_CONFIG_DIR` 이 `Not logged in` 을 냈다는 것은, macOS Keychain 의
`Claude Code-credentials`(acct=`ron`, 존재 확인함 — 값은 읽지 않았다)를 **기본 디렉토리만** 쓴다는 뜻이다.
비기본 `CLAUDE_CONFIG_DIR` 은 자기 디렉토리의 credential 을 쓴다.
→ **가정 A(계정 2개 동시 실행)의 전제가 macOS 에서 성립한다.** Keychain 전역 공유로 무너지지 않는다.

---

## I · 컨텍스트 사용률을 statusLine 에서 얻을 수 있는가 → **참 (설계 예상보다 좋다)**

> 배포된 `probe.sh i` 는 `claude -p` 를 쓴다. **헤드리스에는 statusLine 이 없다** — 그대로 돌리면
> 거짓 음성이 난다. 실제로 처음엔 `status.json` 이 안 만들어졌다.
> 진짜 pty 에서 인터랙티브로 띄워야 한다 (`libexec/ptyrun.py`).
> 사용자의 `settings.json` 은 건드리지 않고 `--settings` 로 주입했다.

statusLine stdin 최상위 키:

```
context_window  cost  cwd  effort  exceeds_200k_tokens  fast_mode  model
output_style  rate_limits  scratchpad_dir  session_id  thinking  transcript_path  version  workspace
```

턴이 1회 발생한 뒤:

```json
"rate_limits":    { "five_hour":  { "used_percentage": 4,  "resets_at": 1789543200 },
                    "seven_day":  { "used_percentage": 41, "resets_at": 1789678800 } }
"context_window": { "used_percentage": 5, "remaining_percentage": 95,
                    "context_window_size": 1000000, "total_input_tokens": 45465,
                    "current_usage": { "input_tokens": 2, "output_tokens": 4,
                                       "cache_creation_input_tokens": 18135,
                                       "cache_read_input_tokens": 27328 } }
"cost":           { "total_cost_usd": 0.0780496 }
```

§4 표가 요구한 값이 **전부** 온다. 그리고 설계서는 컨텍스트 사용률을
"세션 토큰 / 모델 한도"로 **계산**하려 했는데, `context_window.used_percentage` 가 **그대로 온다.**

### 이것이 바꾸는 것

- **§8.1 컨텍스트 트리거에 transcript 집계가 필요 없다.** 따라서 그 경로의 `message.id` dedup 도 불필요하다.
  (dedup 은 transcript 로 **번레이트**를 계산할 때는 여전히 필수다 — 가정 E 는 유효)
- `context_window.used_percentage` 는 **턴 발생 전 `null`** 이다. 0 이 아니다. 미상이므로
  fail-closed 로 다룬다. (`pct_5h` 의 null 은 §M 이후 **미상과 조용함으로 갈린다** — 컨텍스트에는
  그 구분이 없다. 턴 전이라 값이 없는 것과 세션이 죽은 것이 구별되지 않기 때문이다.)

`seven_day_opus` 는 이 표본에 **없었다**. 수집기는 `// null` 로 받는다.

---

## J0 · Orca 가 자기 statusLine 을 강제하는가 → **거짓 (= 우리에게 유리. 데이터 출처 살아있다)**

```
which -a claude   → /Users/ron/.local/bin/claude  (2회 출력이지만 PATH 중복일 뿐 동일 경로)
file              → Mach-O 64-bit executable arm64   ← 셸 래퍼가 아니다
ps -o args=       → claude                            ← --settings / --status-line 없음
managed-settings.json  → 3개 표준 위치 전부 없음
```

Orca 의 Claude 통합은 **statusLine 이 아니라 훅**이다. `~/.claude/settings.json` 의
`SessionStart` / `UserPromptSubmit` 등이 `~/.orca/agent-hooks/claude-hook.sh` 를 부르고,
터미널에 주입된 `ORCA_AGENT_HOOK_*` 로 Orca 런타임에 보고한다.
`ORCA_OMP_STATUS_EXTENSION` 은 Orca 자체 에이전트 상태용(OMP/pi 런타임)이지 Claude Code statusLine 이 아니다.

현재 `~/.claude/settings.json` 의 `statusLine` 은 `ccstatusline` — **사용자가 직접 설치한 것이고 Orca 와 무관하다.**

**결정적 증거**: 이 Orca 세션 안에서 `claude --settings <우리 것>` 으로 띄운 인터랙티브 세션에서
**우리 수집기가 실제로 실행됐고 `status.json` 이 쓰였다.**

---

## J4 · worktree `.envrc`(direnv) 가 먹히는가 → **해당 없음. direnv 가 필요 없다**

`direnv` 는 이 머신에 **미설치**다. 그런데 설치할 이유가 없어졌다.

### README 의 J4 대안은 오독이다

`orca skills get orca-per-workspace-env` 를 읽어보니 `orca.yaml environmentRecipes` 는
**워크스페이스마다 일회용 원격 머신**(클라우드 샌드박스 · VM · SSH 호스트 · 컨테이너)을 띄우는 기능이다.
로컬 worktree 에 환경변수를 주입하는 장치가 **아니다.** J4 거짓 시의 1순위 대안으로는 쓸 수 없다.

### 실측 — Orca 가 띄운 터미널의 환경

`orca terminal create --worktree path:... --command "env > /tmp/..."` 로 캡처:

```
CLAUDE_CONFIG_DIR=                    ← 비어 있다. Orca 는 이 변수를 건드리지 않는다
ORCA_AGENT_HOOK_PORT=56558
ORCA_AGENT_HOOK_TOKEN=<redacted>
ORCA_AGENT_HOOK_ENDPOINT=/Users/ron/Library/Application Support/orca/agent-hooks/endpoint.env
ORCA_PANE_KEY=<redacted>
ORCA_WORKTREE_ID=...::/Users/ron/workspaces/harbor-app
```

두 가지가 확정된다:

1. `CLAUDE_CONFIG_DIR` 은 **비어 있다** — 우리가 자유롭게 정한다. 충돌 없음
2. `--command` 는 **임의의 셸 문자열**이다 — `env CLAUDE_CONFIG_DIR=... claude` 로 끝난다

→ **direnv 도, environmentRecipes 도, tmux 도 필요 없다.** 런처 래퍼 한 겹이면 된다.

---

## Orca 가 이미 멀티 계정을 지원한다 — 그런데 우리 문제는 못 푼다

설계서 phase0/README §1 은 [stablyai/orca#11557] 이 **미구현**이라고 적었다. **지금은 구현돼 있다.**

```
$ orca account list
Managed Claude accounts (2):
  ehalim111@gmail.com
  funccoder@gmail.com (active)
```

사용자 머신에 이미 계정 2개가 등록돼 있다. 그러나 `--json` 을 보면:

```json
"activeAccountId": "8464aac1-...",
"activeAccountIdsByRuntime": { "host": "8464aac1-...", "wsl": {} }
```

**런타임당 활성 계정이 하나다.** `worktree create` 에도 `terminal create` 에도 `--account` 플래그가 없고,
`orca agent-context` 스키마에 `account` 가 등장하지 않는다.

→ 설계서가 정황으로 추론한 **"Orca 는 전역 스왑 부류다"** 가 **정확히 맞다.**
동시 실행 다계정에는 못 쓴다. **`CLAUDE_CONFIG_DIR` 방식은 그대로 유효하다.**
다만 근거 문장은 "미구현"이 아니라 "구현됐으나 전역 스왑이라 동시성이 없다"로 갱신해야 한다.

---

## F · `autoContinueAtUsageLimit` → **기능 존재 확인. 택일 강제 필요**

```
$ strings ~/.local/bin/claude | grep -o autoContinueAtUsageLimit
autoContinueAtUsageLimit
```

`disableAllHooks` · `allowManagedHooksOnly` 도 실재하는 설정 키다 → `ocp doctor` 가 셋 다 점검해야 한다.

---

## M · `rate_limits` 는 **그 세션이 마지막 응답에서 배운 만큼만** 온다 (2026-09-17 신규)

같은 계정, 같은 시각에 두 세션의 statusLine stdin 원본을 받아 비교했다.

방금 띄운 세션 (alpha, 10:50):

```json
"rate_limits": {"five_hour": {"used_percentage": 0, "resets_at": 1789614000},
                "seven_day": {"used_percentage": 41, "resets_at": 1789783200}}
```

어제 20시에 시작해 그 뒤로 요청이 없는 세션 (같은 alpha, 같은 10:50) — `five_hour` **키 자체가 없다**:

```json
{"pct_5h": null, "reset_5h": null, "pct_7d": 41, ...}
```

5h 창은 07:00 에 굴러갔다(`resets_at` 12:00 − 5h). 사용량이 0 이라 생략하는 게 아니다 — 방금 띄운
세션도 0% 였지만 값이 왔다.

**외부 근거.** [claude-code#75408] 이 메커니즘을 명시한다 — *"The value appears to reflect the
rate-limit state attached to that specific session's last actual API response, not a live re-check
… each terminal is showing a stale snapshot from a different moment."* 즉 세션마다 **관측 시각이
다르다.** 다만 "창이 굴러간 뒤 키를 통째로 떨어뜨린다"는 부분까지 적은 문서·이슈는 못 찾았다
(가장 가까운 #45133 은 `rate_limits` 객체 전체가 없는 별개 버그다). **그 절반은 우리 실측이 근거고,
문서로 확인된 것은 아니다.**

**이것이 만든 버그.** 수집기가 그 null 을 계정 파일에 그대로 써서, 같은 계정에서 **일하는 세션이
방금 보고한 값을 노는 세션이 지웠다.** 그러면 모든 판독기가 "usage unknown" 을 보고, fail-closed
규칙이 멀쩡한 에이전트 5개를 handoff 시킨다(에이전트당 fold 1회). `ocp triggers` 가 전원 `YES ·
usage_unknown` 이었던 게 이 상태다. 같은 원인이 대시보드 값이 **왔다갔다하는** 증상으로도 나온다 —
더 낡은 세션이 마지막에 그리면 계정 값이 뒤로 간다.

**고친 규칙** (설계서 §4 에 정본)

- 어떤 창을 보고하지 않는 페이로드는 그 창의 이전 값을 **지우지 않는다** (`resets_at` 이 남은 동안)
- 창이 끝났는지 볼 `resets_at` 이 **아예 없으면** 그 값을 버리되 `rl_lost` 로 표시한다 — 잃은 값은
  조용함이 아니라 미상이고, **그 상태는 창을 다시 보고받을 때까지 유지된다**(한 틱만 버티면
  다음 리프레시에 0% 로 읽힌다). 7d 도 같다
- **한 창 안에서 계정 수치는 내려가지 않는다** (세션마다 관측 시각이 다르므로)
- 이월값의 나이는 `rl_at` 이 재고, `collected_at` 이 아니다. 900초를 넘으면 트리거에는 하한으로
  계속 쓰되 **새 배정은 거부**한다
- 그러고도 값이 없으면 그건 **조용함**이라 0% 로 읽고 배정한다

**알려진 예외 두 가지.** (1) `ocp fold` 는 `claude -p` 헤드리스라 statusLine 이 없다 — fold 가 쓴
쿼터는 어떤 수집기도 못 본다. 그래서 `quiet` 은 "아무도 안 썼다"가 아니라 **"보고된 사용이 없다"**
이다. (2) ocp 가 수집하지 않는 설정 디렉토리에서 같은 구독을 쓰면(데스크톱 앱, 다른 기계, 사람의
`~/.claude` = `default` 계정) 역시 안 보인다. 5h 창은 **구독 단위지 설정 디렉토리 단위가 아니다.**

[claude-code#75408]: https://github.com/anthropics/claude-code/issues/75408

---

## 남은 미확인

| # | 가정 | 왜 아직 못 닫았나 |
|---|---|---|
| **A** (장시간) | 계정 2개 **동시 장시간** 실행에서 토큰 refresh 충돌 없음 | 2번째 계정이 `CLAUDE_CONFIG_DIR` 에 로그인돼 있어야 한다. **사람이 `claude /login` 1회** 해야 함 (AGENTS.md rule 1) |
| **D** | 헤드리스 주입 + clear | Phase 3 전까지 불필요 |
| **G** | `CLAUDE_CODE_OAUTH_TOKEN` 경로에서 `rate_limits` | 토큰이 없다. 계정이 모자랄 때만 필요 |
| **J4-full** | 래퍼로 띄운 에이전트를 Orca 가 **에이전트로 인식**하고 hibernate/restore 가 env 를 보존하는가 | A 와 같은 이유로 막혀 있다 |

[stablyai/orca#11557]: https://github.com/stablyai/orca/issues/11557
