# Orca 기반 멀티 에이전트 파이프라인 — 인프라 설계서 v0.5

> v0.5는 **v1 범위를 잘라낸 판**이다. 계정 동적 재배치를 v2로 미루면서 파생 기능 대부분이 v1에서 빠졌다(§0.5).
> 실행은 Orca 네이티브 + direnv. tmux 하이브리드는 폐기. 기각 사유는 부록 A.
> 팀 구성·관계·라우팅은 동반 문서 `orca-team-model.md`.

---

## 0. 요약

각 계정의 `CLAUDE_CONFIG_DIR`에 **statusLine 수집기**를 심어 공식 스키마로 상태를 받고,
Orca orchestration 위에 얹은 얇은 CLI `ocp`가 **계정 = 슬롯**으로 임대하며,
기록은 **세션 밖에서 fold**하고, 트리거·재개는 **영속 상태 + 기동 시 reconciliation**으로 돌린다.

세 가지 원칙:

- **우리 코드에 credential을 읽거나 쓰는 줄은 0줄이다**
- **오케스트레이션에 모델을 쓰지 않는다** — 전부 결정론적이라 데몬이 한다
- **지표는 하나다** — `runway`. 배정도 정렬도 이것만 본다


---

## 0.5 v1 범위 — 복잡도의 출처는 하나다

설계를 다시 읽어보면 **전체의 절반 이상이 "계정 동적 재배치" 하나에서 파생됐다.**

```
계정 동적 재배치
 ├ lease 테이블 · UNIQUE 인덱스 2개 · reaper · PID 생존 확인 · flock
 ├ 큐 · 에이징 · prio · 헤드 블로킹 방지 · min_runway_eff
 ├ runway 게이트 · burn floor · 콜드스타트 특례 · 리셋 경계 보정
 ├ thundering herd · 지터 · convoy 방지
 ├ 중재자 단일화 · 획득 실패 재선택
 └ tmux 하이브리드 (PID 소유권 때문에)
```

### 그런데 이득이 생각보다 작다

**총 처리량은 `계정 수 × 5시간 할당`으로 고정이다.** 동적 재배치가 늘리는 건 총량이 아니라 **활용률**이다 — 어떤 계정이 노는 동안 다른 에이전트가 그걸 쓰는 것.

활용률 이득은 **에이전트 수 ÷ 계정 수**에 비례한다. 에이전트가 계정보다 훨씬 많을 때만 크고, 비슷하면 거의 없다.

### v1은 고정 매핑으로 간다

```yaml
backend   → alpha
frontend  → beta
reviewer  → gamma      # serial, 가벼움
archivist → gamma      # ephemeral, 짧게 쓰고 나감
```

자기 계정이 90%에 닿으면 handoff하고 **리셋까지 쉰다.** 다른 계정으로 옮기지 않는다.

위 파생 목록이 **전부 사라진다.** 남는 것:

| v1에 남는 것 | 왜 |
|---|---|
| 에이전트 폴더 + ROLE/base/handoff/journal + fold | 계정과 무관하게 가치 있고 복잡도가 낮다 |
| `team.yaml` 라우팅 + `ocp ask` | 팀이 돌아가는 핵심. 계정과 직교 |
| statusLine 수집기 | 읽기 전용. 여러 계정 상태를 한눈에 |
| 데몬 (축소판) | 트리거 → fold → handoff → park → 리셋 시 재개 |
| Orca orchestration | 그대로 |

데몬은 리스도 큐도 없이 **에이전트별 독립 루프**가 된다: `내 계정이 87%인가 / 컨텍스트가 60%인가 → handoff → park → 리셋 시각에 재개.`

### v2로 미뤄도 안전한 이유

journal이 **에이전트 이름으로 묶이고 계정과 독립**이라(§5), 나중에 계정을 옮겨도 이력이 끊기지 않는다. 설계가 이미 그걸 보장하므로 **나중에 슬롯 모델을 얹어도 기록 계층은 그대로다.**

전환 신호: **에이전트 수가 계정 수의 2배를 넘으면** 활용률 손실이 체감된다. 그때 §6·§7을 켠다.

> 아래 §6(슬롯)·§7(스케줄링)은 **v2 참고용**으로 남겨둔다. v1에서는 구현하지 않는다.

---

## 1. 의존성

| 대상 | 판정 |
|---|---|
| **Orca orchestration / worktree** | 유지. DAG·워커·게이트를 그대로 쓴다 |
| **Claude Code statusLine** | **핵심.** 사용률·리셋시각·세션 조인키의 단일 출처 (§4) |
| cswap / clauth | **삭제** — 읽으려던 사용률 파일이 존재하지 않고, statusLine으로 대체됨 |
| ccusage | **사후 분석 전용** — 블록이 rate limit 윈도우가 아니라 로컬 재구성이라 스케줄링에 쓸 수 없다 |
| handoff·파서류 | 내재화 |

### 보안 — credential을 옮기지 않는다

계정 전환 도구가 다루는 것은 구독 OAuth token이고, API 키와 달리 계정 전체 권한이며 개별 revoke가 어렵다. 그런데 "서드파티냐 직접 구현이냐"는 틀린 질문이었다 — 직접 구현해도 락·로테이션 문제는 Claude Code 본체도 미해결이다.

```
계정당 CLAUDE_CONFIG_DIR 을 1회 /login 하고 그 디렉토리를 영구 보존한다.
"스왑" = 환경변수를 바꿔 새 프로세스를 띄우는 것.
credential 파일을 읽지도, 쓰지도, 복사하지도 않는다.
```

공식 문서가 `CLAUDE_CONFIG_DIR`별 격리를 보장한다 — macOS Keychain 항목까지 config dir 기준으로 키잉된다.

**복사는 절대 금지.** refresh token은 1회용 로테이션이라 복사본 둘이 각자 refresh하면 먼저 성공한 쪽이 나머지를 무효화하고 재인증 캐스케이드가 난다. 그래서 계정 공유(`max_concurrent ≥ 2`)를 설계에서 뺐다. 슬롯이 부족하면 큐잉으로 푼다.

### 다중 계정 도구 조사 — 쓸 수 있는 건 거의 없다

Orca는 **전역 활성 계정 1개** 개념이다(공식 문서: 상태바는 항상 *active* 계정을 반영). 그래서 별도 수단이 필요한데, 생태계를 훑어보니 두 부류로 갈리고 **별이 많은 쪽이 우리에게 쓸모없는 쪽**이었다.

| 부류 | 동작 | 동시 실행 | 예 |
|---|---|---|---|
| **A. 전역 스왑** | credential/Keychain을 원자적으로 교체 | **✘ 한 번에 1계정** | `aisw`(116★, Rust) · `claude-switch` · `multi-account-tool` · 목록의 대부분 |
| **B. CONFIG_DIR 격리** | 프로필별 디렉토리, 프로세스별 env | **✔** | `claude-profile-manager`(15★, Go) · `claude-profile-switch`(shell) · `clauth`(Rust, `start --isolated`) |

A 부류는 **Orca와 똑같은 제약**을 가진다. 도구를 바꿔도 문제가 그대로다. 가장 인기 있는 `aisw`가 여기 속한다.

B 부류 중 `claude-profile-manager`는 우리 §3 레이아웃과 **독립적으로 같은 결론**에 도달했다 — 프로필별 `CLAUDE_CONFIG_DIR`, credential은 프로필 안에, commands·skills·plugins는 심볼릭 공유. 설계 검증으로는 의미가 있다.

**그럼에도 도구를 붙이지 않는다.** 이유 셋:

1. 우리에게 필요한 건 런처 한 겹뿐이다 — `CLAUDE_CONFIG_DIR=accounts/<a>/.claude claude ...`. `ocp`가 이미 하는 일이다
2. **어느 도구도 우리 스케줄러를 대신하지 못한다.** 그들의 "best account" 로직은 `runway`·큐·리스 개념이 없다
3. 전부 2~116★이고, 무엇보다 도구를 붙이는 순간 **"credential을 만지는 코드 0줄"이 깨진다**

베낄 것만 베낀다: 레이아웃과 심볼릭 대상 목록, `CLAUDE_CONFIG_DIR=... claude /login` 부트스트랩(=`ocp account add`, 10줄), 링크 무결성 `doctor`.

### 실행 계층 — Orca 안에서, direnv로

Orca는 **전역 스왑 부류**다. 근거는 공식 문서 두 문장인데, *"switching accounts works even with live Claude sessions running"* 과 *"a guard so you don't trigger overlapping auth refreshes"* 가 결정적이다. 전환이 실행 시 env 주입이었다면 이미 돌고 있는 세션이 영향받을 수 없고 guard도 필요 없다.

그리고 Orca 이슈 트래커에 **우리와 똑같은 요청이 이미 올라와 있다** — [#11557 "Support multiple Claude Code subscriptions (separate config dirs / env vars)"](https://github.com/stablyai/orca/issues/11557). **미구현**이고, 요청자도 "터미널에서는 config dir을 나눠 쓰는데 Orca 안에서는 같은 걸 할 방법을 못 찾았다"고 적었다.

그래서 Orca 안에서 되는 방법은 하나다: **worktree에 `.envrc`를 두고 direnv에 맡긴다.**

```bash
# agents/backend 의 worktree/.envrc
export CLAUDE_CONFIG_DIR="$HOME/.orcapipe/accounts/alpha/.claude"
```

그러면 **Orca에서 평소처럼 에이전트를 띄우면 된다.** `worktree create --agent claude`도 쓸 수 있고, 터미널을 열어도 되고, hibernation·session restore·대시보드가 전부 그대로 동작한다. 우리가 실행 경로에 끼어들지 않는다.

v1이 **고정 매핑**(§0.5)이라 `.envrc`가 정적이어도 문제가 없다. 이게 고정 매핑의 가장 큰 실익이다.

**보너스**: Orca의 agent hibernation은 재개 시 *"launch command, arguments, and environment"* 를 보존한다. 한 번 들어간 env가 재개 후에도 유지된다.

**폐기**: tmux 하이브리드. PID 소유권이 필요했던 이유가 리스 회수(§6)였는데 v1에는 리스가 없다. v2에서 슬롯 모델을 켤 때 다시 꺼낸다.

**남은 위험 둘** (`phase0/probe.sh`):
- **J0** — Orca가 자기 statusLine을 CLI 플래그로 강제하면 §4 수집기가 죽는다. 여전히 최우선
- **J4** — Orca가 에이전트를 로그인 셸 없이 spawn하면 direnv가 안 먹는다. 그러면 `.envrc` 대신 `orca.yaml`의 `environmentRecipes`를 시도하고(`orca skills get orca-per-workspace-env`로 형식 확인), 그것도 안 되면 그때 tmux로 간다

### 도구 두 개가 `.claude.json`을 다르게 처리한다

`claude-profile-manager`는 `.claude.json`을 **프로필 디렉토리 안에** 두고, `claude-profile-switch`는 **`~/.claude.json`을 전역으로 스왑**한다. 후자는 동시 실행 시 두 세션이 한 파일을 공유한다는 뜻이다.

**두 도구가 서로 다르게 푼다는 것 자체가 Phase 0 가정 H가 진짜 갈림길이라는 증거다.**

### 어댑터 규칙

**allowlist로 필드만 추출**한다. `--json` 출력을 통째로 저널·DB에 덤프하지 않는다. 토큰을 subprocess `argv`에 넣지 않는다(`/proc/*/cmdline` 노출).

---

## 2. 도메인 모델

```
Run       (Orca) ── 목표 단위
 └ Task   (Orca) ── 작업/하위작업. 의존성 + 상태
    └ Dispatch (Orca) ── 태스크 1회 시도 = 터미널 1개
       └ Session ── transcript 1개 + status.json 1개
          ├ Agent (우리) ── 이름 + 역할 + 전용 폴더
          └ Lease (우리) ── 이 세션이 점유한 슬롯
```

**Agent 이름이 모든 시스템의 조인 키.** worktree 이름 · task label · 폴더명이 같은 문자열을 쓴다.

---

## 3. 레이아웃

`~/.orcapipe` 전체를 **git repo로 둔다.** 다이제스트 세대 보존·롤백·변경 이력이 전부 공짜로 따라온다. `accounts/*/` 는 `.gitignore`.

```
~/.orcapipe/                    ← git repo
  config.yaml
  team.yaml
  db.sqlite                     # 리스 · 이벤트 · handoff 진행상태 · wake_at   (gitignore)
  daemon.pid                    # PID + 부팅시각
  shared/.claude/               # 계정 공통 설정. 각 계정이 심볼릭으로 참조
    settings.json CLAUDE.md skills/ agents/ commands/ hooks/
  accounts/                     ← gitignore 전체
    alpha/
      .claude/                  # CLAUDE_CONFIG_DIR. credential만 실파일, 나머지는 shared 링크
      status.json               # statusLine 수집기가 원자적으로 쓰는 스냅샷
  agents/
    <name>/
      agent.yaml
      ROLE.md                   # ≤1KB
      base.md                   # ≤2KB — 매 세션 주입되는 고정비
      inbox/
      journal/2026-09-15.md     # 다이제스트. 세대 보존은 git이 한다
      handoff/latest.md
```

---

## 4. 데이터 출처 — statusLine 수집기

`~/.claude` 아래에 사용률 파일은 **없다.** 없는 파일을 읽어 `pct=0`을 반환하면 모든 슬롯이 만점이 되고 90% 트리거는 영원히 거짓이 된다 — **기능이 죽었는데 대시보드는 초록색**인 fail-open이다.

공식 계약은 **statusLine 스크립트의 stdin JSON**이다. 각 `CLAUDE_CONFIG_DIR`의 `settings.json`에 스크립트를 심고, 그 스크립트가 stdin JSON을 `accounts/<acct>/status.json`으로 원자적으로 쓴 뒤 원래 표시 문자열을 출력한다. 스크립트는 `$CLAUDE_CONFIG_DIR`로 자기 계정을 판별하므로 shared에 한 벌만 두면 된다.

| 필요한 값 | 출처 |
|---|---|
| `pct_5h` | `rate_limits.five_hour.used_percentage` — **서버값이되 그 세션의 마지막 API 응답 시점의 것**이다. 라이브 재조회가 아니다 ([claude-code#75408]) |
| 5h 리셋 시각 | `rate_limits.five_hour.resets_at` |
| 주간 여유 | `rate_limits.seven_day.*` |
| `rl_at` · `rl_carried` · `rl_lost` | 그 5h 수치를 **언제 관측했는지**, 이번 페이로드가 아니라 이월된 것인지, 창 경계를 몰라 **잃었는지**. `collected_at` 은 수집기가 살아있다는 말일 뿐이라 수치의 나이를 못 준다 |
| `rl7_at` · `rl7_carried` | 7d 수치에 대한 같은 것. 7d 는 창이 일주일이라 나이 제한을 두지 않는다 |
| 컨텍스트 사용률 | 세션 토큰 / 모델 한도 → **트리거 (§8)** |
| 세션 조인키 | `session_id`, `transcript_path` |
| 비용 | `cost.total_cost_usd` |

statusLine은 `resets_at` 도달 시 자동 재실행되므로 리셋 타이머가 거의 불필요하다.

### 제약 — `ocp doctor`가 강제 점검

- 세션이 살아있을 때만 갱신 → `refreshInterval`로 보완
- managed settings의 `disableAllHooks` 계열이 걸리면 **경고 없이 비활성화**
- usage 엔드포인트 자체에 rate limit이 있다. 공격적 폴링 시 stale 값으로 fail-open
- `pct_5h`가 null이라고 다 같은 null이 아니다. 판정은 **다섯 가지**다 (2026-09-17 실측)
  - `live` — 이번 페이로드가 그 창을 보고했다
  - `carried` — 이전 관측값이고, 창이 아직 안 끝났으며, 관측한 지 `CARRY_MAX_AGE_S`(900s) 이내다
  - `carried_old` — 같은 이월값인데 그보다 오래됐다. **트리거에는 쓰되**(창 안에서 사용량은 오르기만
    하므로 하한이다) **새 배정은 거부한다**
  - `quiet` — 살아있는 수집기가 그 창에 대해 아무 말도 안 한다. 이 창에서 아무도 안 썼다는
    **관측**이므로 0%로 읽고 배정한다. 여기서 거부하면 노는 에이전트를 handoff 시키고 fold 비용만 태운다
  - `unknown` — 스냅샷이 없거나·판독 불가(NaN·문자열·불리언)거나·낡았거나·**이월하지 못하고 잃었다**.
    이것만 fail-closed 다
- 그래서 수집기가 지키는 규칙 셋
  - 어떤 창을 보고하지 않는 페이로드는 **그 창의 이전 값을 지우지 않는다** (`resets_at`이 남아 있는 동안)
  - **창이 끝났는지 판단할 `resets_at` 자체가 없으면** 그 값을 버리되 `rl_lost` 로 표시한다. 잃은 값은
    조용함이 아니라 **미상**이다 — 이 구분이 없으면 95% 를 알고 있던 계정이 다음 틱에 0% 로 읽힌다
  - **한 창 안에서 계정 수치는 내려가지 않는다.** 세션마다 관측 시각이 달라서, 이게 없으면
    더 낡은 세션이 마지막에 그릴 때마다 계정 값이 뒤로 간다 ([claude-code#75408] 의 그 증상이다)
- **조용함 추론의 알려진 예외**: `ocp fold` 는 `claude -p` 로 도는데 헤드리스라 statusLine 이 없다.
  그래서 fold 가 쓴 쿼터는 **어떤 수집기도 보지 못한다.** 한 번에 작은 호출이지만, `quiet` 은
  "아무도 안 썼다"가 아니라 **"보고된 사용이 없다"**로 읽어야 한다
- Claude Code v2.1.234+의 **`autoContinueAtUsageLimit`**이 우리 재개 A와 겹친다. 끄고 우리가 관리하거나 켜고 위임하거나 **택일**을 강제한다

### transcript 집계 시 dedup 필수

한 API 응답의 content block이 각각 JSONL 라인이 되면서 **같은 usage 객체를 복사**한다. `message.id`로 dedup하지 않으면 번레이트가 과대 계산되고, 그러면 `runway`가 과소 → **모든 슬롯이 게이트 탈락 → 전원 PARKED 고착**. 실측 표본 하나에서 2.4배였다.

---

## 5. 기록

### 계층

```
events (SQLite)   기계 이벤트. 훅이 INSERT. 사람도 모델도 안 읽음
journal/<날짜>.md 다이제스트. handoff 때마다 fold로 통째 재작성
handoff/latest.md 다이제스트에서 기계 추출. 재개 시 주입
```

이벤트를 파일이 아니라 **SQLite에 넣는다.** 델타 경계가 `WHERE id > cursor` 한 줄이 되고, 훅이 늦게 도착해도 순서가 깨지지 않는다.

### fold는 세션 밖에서

```
fold 입력 = 이전 다이제스트 + events 델타 + transcript tail
fold 실행 = 별도 호출. 해당 계정이 아니어도 되고, 저가 모델로 충분하다 (archivist)
커서 전진 = 검증 통과한 fold가 커밋된 뒤, 같은 트랜잭션에서만
```

세션 안에서 요약하면 입력이 세션 길이에 비례해 무한 증가한다. 밖으로 빼면 캐시 만료 시각과 fold가 탈동조화되고, 90% 근처에서 예비분을 잠식하는 문제도 사라진다.

### 기록 타이밍 — 주기적으로 하지 않는다

| 시점 | 무엇 | 모델 |
|---|---|---|
| `SessionStart` / `Stop` / `SessionEnd` | 이벤트 INSERT | 안 씀 |
| git `post-commit` | 커밋·diffstat | 안 씀 |
| handoff | fold 1회 | **씀 (유일)** |

`PostToolUse`는 쓰지 않는다 — 너무 잦고 신호 대비 잡음이 크다.

### fold는 파괴적 재작성이다 — 검증 필수

원자적 쓰기는 "반쯤 쓰인 파일"만 막는다. **완전하게 쓰인 쓰레기 파일이 좋은 파일을 덮는 것**은 못 막는다.

```
1. 스키마 검증: 5개 고정 섹션 헤더 존재
2. 단조성 검증: 이전 다이제스트의 `완료` 항목이 하나도 사라지지 않았을 것
3. 실패 시 git으로 롤백 + degraded 폴백
```

그리고 handoff에는 **모델 출력과 무관한 결정론적 블록을 항상 붙인다** — 브랜치·HEAD·직전 커밋 5개·변경 파일·최근 이벤트. fold가 망해도 재개가 맨손이 되지 않게.

### 다이제스트 형식

```markdown
# 2026-09-15 · backend
updated: 14:32 (handoff #3) · folded_through: event 18432

## 완료 / ## 진행중 / ## 결정 / ## 막힌 것 / ## 다음 / ## base 승격 후보
```

섹션이 고정이면 fold가 "항목을 섹션 간에 옮기는 연산"이 된다. 자유 서술이면 매번 구조가 흔들려 재작성이 불안정해진다.

### 자정 롤오버 (모델 없음)

날짜가 바뀌면 전날 `## 진행중`이 **영원히 진행중으로 남는다.** 00:00 타이머에:

```
1. 전날 델타가 남아 있으면 강제 fold 1회
2. 오늘 다이제스트를 `진행중`/`막힌 것`/`다음`만 캐리포워드, `완료`는 비움
3. 마감 이후 도착한 fold는 `## 지각 반영` 블록으로
```

---

## 6. 슬롯  **[v2 — v1에서는 구현하지 않는다]**

계정을 워커 슬롯으로 두고 **슬롯이 큐에서 일을 가져간다(pull).** 에이전트가 계정을 고르지 않는다.
이 반전으로 예산 예측 불가·동시 만료·귀속 불가·인증 충돌이 동시에 사라진다.

```sql
CREATE TABLE lease (
  account TEXT, agent TEXT, session_id TEXT, worker_pid INTEGER,
  acquired_at INT, heartbeat_at INT, state TEXT
);
CREATE UNIQUE INDEX one_per_account ON lease(account) WHERE state='active';
CREATE UNIQUE INDEX one_per_agent   ON lease(agent)   WHERE state='active';
```

`one_per_agent`가 없으면 같은 에이전트가 두 계정에서 동시에 리스를 갖고, 같은 journal에 두 세션이 쓴다.

### 회수는 PID로 — TTL로 하면 안 된다

SQLite는 TTL 만료를 해주지 않는다. 그리고 **TTL 만료가 프로세스 사망을 함의하지 않는다.** 데몬이 죽어도 워커는 Orca 소유라 계속 살아서 그 CONFIG_DIR을 쓴다. 리스만 만료되면 다른 에이전트가 같은 슬롯을 잡아 **CONFIG_DIR 이중 사용**이 된다.

```
회수 조건 = PID/프로세스 그룹 생존 확인. TTL은 보조 신호
회수 전 SIGTERM으로 실제 종료시킨 뒤에만 state='released'
계정 디렉토리에 flock으로 OS 레벨 배타성도 건다
획득 트랜잭션 맨 앞 + 데몬 루프 30초마다 reaper 실행
```

DB 유니크 제약은 프로세스를 죽이지 않는다.

### 중재자는 하나다

```
기상·배정은 단일 데몬 루프가 큐를 정렬해 순차 수행한다.
에이전트가 직접 리스를 경쟁하지 않는다. UNIQUE는 안전망이지 스케줄러가 아니다.
획득 실패 시 그 슬롯만 후보에서 빼고 즉시 재선택 — 후보 소진 시에만 QUEUED
ocp start 도 직접 리스를 잡지 않고 큐에 최고 우선순위로 넣는다
```

---

## 7. 스케줄링 — 지표는 `runway` 하나  **[v2]**

```
headroom = max(0, (90 - pct_5h) / 100)          # 모든 곳에서 90% 캡 통일
burn     = max( EWMA(최소 10분 창), weight별 floor )
runway   = headroom / burn                       # 단위: 분

effective_runway = (reset_in <= 15분) ? reset_in + 다음_윈도우_runway : runway
weekly 보정      = × min(1, weekly_headroom / 0.2)
```

**배정도 정렬도 `effective_runway` 하나로 한다.**

```
heavy: effective_runway >= 60분
light: effective_runway >= 15분
pct_5h >= 90 인 슬롯은 후보 생성 단계에서 제외
정렬: effective_runway 내림차순
```

`burn`의 floor가 중요하다. floor가 없으면 두 가지 실패가 난다 — 전원 PARKED면 번레이트 0 → runway 무한 → 전원 통과 → 3분 만에 90% → 진동. 표본이 0인 콜드스타트에서는 번레이트를 얻으려면 돌려야 하고 돌리려면 번레이트가 필요한 **순환 대기 데드락**.

활성 블록이 없는 슬롯은 `runway = ∞`인 **최상위 후보로 명시 정의**한다.

### 큐

```
prio = blocks + wait_minutes / 30
매칭 = "슬롯 하나에 대해 큐를 순회하며 첫 적격 에이전트"   (헤드 블로킹 방지)
건너뛴 횟수를 에이징에 가산
min_runway_eff = max(3분, min_runway - waited_minutes/20)   (기아 해소)
```

마지막 줄이 없으면 에이징이 **순서만 바꾸고** 게이트를 못 뚫어 기아가 영원히 안 풀린다. 진행 보장은 "언젠가 임계값이 반드시 만족된다"로만 증명 가능하다.

### handoff는 선점점이다

handoff 시점에 리스를 일단 큐에 반납하고 재선택한다. 그러지 않으면 리스 해제 계기가 `worker_done`과 `90% 도달` 둘뿐이라 대기 에이전트가 5시간 내내 기회를 못 받는다. `max_lease_minutes` 상한도 둔다.

---

## 8. 트리거 · 상태머신 · 재개

### 8.1 트리거는 둘, 레벨 기반

```
컨텍스트  : context_pct >= 60%          ← 캐시 만료를 대체 (부록 A-2)
사용량    : pct_5h >= 90 - reserve_pct
```

엣지 트리거(이번 틱에 경계를 넘었는가)로 구현하면 **데몬이 죽어 있는 동안 지나간 크로싱을 영원히 감지하지 못한다.** 조건식으로 정의한다.

`reserve_pct`는 관측된 fold 평균 비용(초기 3~5%). 90%는 하드 정지선으로만 쓴다.
`pct >= 80`이면 폴링을 5초로 좁히고, 판정은 현재값이 아니라 `pct + burn × (poll + fold_eta)` 투영값으로 한다 — heavy 에이전트는 30초 틱 사이에 88%→97%로 점프할 수 있다.

### 8.2 상태머신

```
IDLE → QUEUED → RUNNING → DONE
                   │
                   ├─ 컨텍스트 / 사용량 ──▶ HANDOFF
                   │                          │
                   │            검증 통과 ─────┼──▶ 슬롯 재선택 ──▶ RUNNING
                   │                          │         (후보 없음)
                   │            실패·타임아웃 ─┴──▶ DEGRADED ──▶ PARKED
                   │
PARKED    ──wake_at──▶ QUEUED
THROTTLED ──예산 창 롤오버──▶ QUEUED      (자동)
STALLED   ──사람만 해제──▶ QUEUED
```

**HANDOFF에서 나가는 실패 간선이 있어야 한다.** 없으면 fold 실패·타임아웃이 전부 여기 수렴해서 에이전트가 어떤 상태도 아닌 채로 슬롯을 영구 점유한다.

### 8.3 fold 타임아웃

```
검출: 다이제스트 front-matter의 fold_id 일치 + 내용 해시 (mtime은 해상도 충돌·오인 문제)
타임아웃 90초
타임아웃 시: ① 커서 전진 안 함 ② 데몬이 모델 없이 degraded handoff 생성 ③ 반복되면 THROTTLED
```

### 8.4 PARKED를 깨울 주체

**"dispatched 상태만 평가"하면 전원 PARKED일 때 평가 대상이 0개가 된다.** 재개 계기인 리스 해제 이벤트도 활성 리스가 0이라 앞으로 영원히 발생하지 않는다. 계정이 텅 빈 채로 시스템이 영구 정지한다.

```
평가 대상 = RUNNING, HANDOFF, PARKED, QUEUED, THROTTLED
agent.wake_at 을 SQLite에 영속 (리셋 시각 · 리스 만료 중 이른 쪽)
데몬 기동 시 reconciliation 1회 무조건:
  - wake_at <= now 전수 스캔
  - 리스 테이블 ↔ 실제 실행 중 워커 대조
  - stale pidfile 자동 회수 (PID + 부팅시각)
```

인메모리 타이머는 최적화일 뿐 정확성의 근거가 될 수 없다. 이 reconciliation 하나가 "데몬 사망 중 지나간 트리거", "리스 좀비", "타이머 소실"을 **동시에** 막는다.

### 8.5 카운터는 옳은 것을 센다

"태스크당 dispatch 5회 상한"은 **실패가 아니라 정상적인 연속을 센다.** 계정이 부족해 handoff가 잦으면 태스크가 순조롭게 전진하는 중에도 상한에 걸린다 — 시스템이 열심히 일할수록 빨리 멈춘다.

```
failed_attempts : 크래시 · 게이트 거부 · 오류 종료에서만 +1. 상한 3
continuations   : handoff 기인. 무제한
폭주 방지는 횟수가 아니라 자원으로: task_token_budget / task_wallclock_budget
재개 예산은 성공한 dispatch만 카운트 — 실패한 재평가는 무료
```

마지막 줄이 없으면 **리셋 순간에 전원이 깨어나 경합에 지고 그 실패로 STALLED가 된다.** 자원이 회복된 바로 그 순간에 시스템의 3/4이 정지한다.

### 8.6 THROTTLED와 STALLED

```
THROTTLED : 자원 문제. 예산 창 롤오버 시 자동 → QUEUED. 연속 K회 상한 + 지수 백오프
STALLED   : 태스크 결함. 사람만 해제. gate-create는 THROTTLED 자동해제 K회 실패 후에만
```

예산 초과를 STALLED로 보내면 예산은 1시간 뒤 복원되는데 상태는 복원되지 않는다. 23:40에 걸리면 **아침까지 슬롯이 유휴인 채로 정지** — 무인 운영이 목적인 시스템이 정확히 그 구간에서 무효화된다.

### 8.7 Thundering herd  **[v2]**

```
리셋 기상에 에이전트별 지터 0~30초, 슬롯 수만큼만 기상
계정 블록 시작 시각을 의도적으로 스태거
재기동 직후 HANDOFF 진입을 초당 1건으로 제한
```

없으면 블록이 정렬되어 5시간마다 모든 슬롯이 동시에 비고 동시에 90%에 닿는 **convoy**가 생긴다.

### 8.8 베이스 드리프트

```
git fetch && git rev-list --count HEAD..origin/<base>
git diff --name-only HEAD...origin/<base>

판정은 커밋 수가 아니라 파일 교집합:
  드리프트 파일 ∩ 에이전트 touched-files ≠ ∅ → 강제 리베이스 또는 gate
```

`main..HEAD`는 **에이전트 자신의 커밋**이라 드리프트를 측정하지 못한다. 방향이 반대다.

재개 헤더도 여기에 맞춘다:

```
겹침 없음: 아래 "완료" 항목은 이미 끝났습니다. 재수행하지 마십시오.
겹침 있음: 아래 "완료" 항목은 베이스 이동으로 무효일 수 있습니다. 해당 파일을 먼저 확인하십시오.
```

### 8.9 handoff 진행 상태 영속화

```sql
CREATE TABLE handoff (
  id TEXT PRIMARY KEY, agent TEXT, session_id TEXT, trigger_kind TEXT,
  cursor_from INT, cursor_to INT, step TEXT,   -- requested|folded|extracted|released
  deadline INT, state TEXT                      -- in_flight|done|degraded
);
```

각 단계 커밋. 기동 시 `in_flight` 행을 찾아 **그 단계부터** 재개한다. in_flight 행이 있는 에이전트는 트리거 평가에서 제외하고, 두 번째 트리거는 사유 추가로 병합한다. `min()` 수식은 상호배제 장치가 아니다.

---

## 9. CLI

```bash
ocp agent register <name> --role-file ROLE.md --repo <sel> --weight heavy
ocp agent list|show [--json]

ocp run create --objective "..."
ocp task add --spec "..." [--depends-on <id>]
ocp start <agent> --task <id>

ocp slot list [--json]        # runway 포함
ocp slot why <agent>          # 게이트 통과·탈락 근거
ocp slot release <account> --force
ocp queue list

ocp journal show|fold
ocp base promote <agent> --item "..."
ocp handoff write <agent>
ocp resume <agent>
ocp unstall <agent>

ocp daemon [--foreground]
ocp watch
ocp status [--json|--table]
ocp doctor
```

`ocp doctor`: statusLine 설치 · `rate_limits` 존재 · Claude Code 버전 · `disableAllHooks` · `autoContinueAtUsageLimit` · 계정별 로그인 상태 · 심볼릭 링크 무결성 · stale pidfile · `base.md` 크기 초과.

---

## 10. 토큰 예산

| 방식 | 비용 |
|---|---|
| 로직을 SKILL.md에 | 지침 ~3,000토큰이 관련 턴마다 상주, 컴팩션마다 재로드 |
| 얇은 스텁 + CLI | 스텁 ~150토큰 + 호출한 명령의 출력만 |

*(개략 추정. Phase 0에서 실측 교정)*

- `SKILL.md`는 20줄 이하. 상세는 `ocp guide <topic>`로 필요할 때만
- 상태 조회 출력은 사람·데몬용. 모델 컨텍스트에 상태 덤프를 넣지 않는다
- 판단은 데몬이 한다. 모델은 결정에 참여하지 않는다
- **모델이 토큰을 쓰는 유일한 지점 = fold 1회**, 세션 밖에서 저가 모델로

---

## 11. 대시보드

```
┌ RUNS / TASKS ───────────────┬ SLOTS ──────────────────────┐
│ ▸ auth-refactor    3/7      │ alpha  5h ███░░ 62%  1:48↻  │
│   ├ ✔ schema      backend   │        runway 71m  ✔heavy   │
│   ├ ⟳ endpoints   backend   │ beta   5h █████ 87%  0:12↻  │
│   └ ⏸ tests       reviewer  │        runway 4m   ✘        │
├ AGENTS ─────────────────────┴─────────────────────────────┤
│ backend  [beta] ⟳ endpoints  ctx 61%  ⚠handoff            │
│ reviewer —      ⏸ THROTTLED  자동해제 00:40               │
└───────────────────────────────────────────────────────────┘
```

읽기 전용 조인 뷰. 로직이 없다. **Phase 5** — `ocp status --table`로 버티다 마지막에 만든다.

---

## 12. Phase 0 판정표

> **갱신 2026-09-16** — 근거(명령·출력)는 `docs/phase0-findings.md`.
> 측정: macOS · Claude Code 2.1.273 · Orca 1.4.203.

| # | 가정 | 판정 |
|---|---|---|
| A | `CLAUDE_CONFIG_DIR` 분리로 계정 2개 동시 실행 | **✔ 참 · 실측 확인 (2026-09-16).** 계정 2개를 **동시에** 띄워 각각 자기 `status.json` 을 정확히 갱신했다 (alpha 5h 0%/7d 40%, beta 5h 20%/7d 49% — 서로 다른 세션·비용). credential 상호 무효화 없음 |
| B | 사용률을 파일에서 읽기 | **✘ 거짓.** statusLine `rate_limits` 가 공식 계약 |
| D | 헤드리스 주입 + clear | **✔ 참 · 실측 확인 (2026-09-16).** `orca terminal send --text "/clear"` 로 컨텍스트가 실제로 반납된다: 살아있는 에이전트에서 `total_input_tokens` 45028 → 0, 세션 ID 교체. 터미널·워크트리·계정은 유지된다. 깨울 때 `handoff/latest.md` 를 첫 프롬프트로 주입하면 이어서 일한다 |
| E | JSONL 라인 1개 = API 요청 1개 | **✘ 거짓.** `message.id` dedup 필수. **단 §8.1 트리거는 이제 transcript 를 안 쓴다** (I 참조) — dedup 은 번레이트 계산에만 필요 |
| F | `autoContinueAtUsageLimit` 이 §8 과 충돌 안 함 | **✘ 기능 실재 확인** (바이너리 설정 키). 택일 강제 — `ocp doctor` 가 점검 |
| G | `CLAUDE_CODE_OAUTH_TOKEN` 경로에서 `rate_limits` | **미확인.** 토큰 없음. 계정 부족 시에만 필요 |
| H | `~/.claude.json` 이 `CLAUDE_CONFIG_DIR` 을 따라가는가 | **✔ 참.** 프로필 안에 생성되고 홈은 unchanged. 격리 완전 |
| I | 컨텍스트 사용률을 statusLine 에서 얻을 수 있는가 | **✔ 참, 예상보다 좋다.** `context_window.used_percentage` 가 **직접** 온다. 계산 불필요. 턴 전에는 `null` → fail-closed 대상 |
| **J0** | Orca 가 자기 statusLine 을 CLI 로 강제하는가 | **✘ 거짓 = 우리에게 유리.** argv 는 맨 `claude`. Orca 통합은 statusLine 이 아니라 **훅**이다. 수집기 실제 동작 확인 |
| **J4** | worktree `.envrc`(direnv) 가 Orca 에이전트에 먹히는가 | **해당 없음 — direnv 불필요.** Orca 가 `CLAUDE_CONFIG_DIR` 을 비워 두고 `--command` 가 임의 셸이라 **런처 래퍼**로 끝난다. `environmentRecipes` 는 원격 머신 기능이라 대안이 아니었다 |
| **J4-full** | 래퍼로 띄운 에이전트를 Orca 가 **에이전트로 인식**하는가 | **✔ 참 · 실측 확인 (2026-09-16).** `orca terminal create --command "ocp launch backend"` 로 띄우자 Orca 가 `agentIdentity: "claude"` 로 기록하고 탭 제목을 `✳ Claude Code` 로 바꿨다. 상태줄에 `alpha · 5h 0% · 7d 40%` — 우리 수집기가 그 안에서 동작 |
| **L** | credential 저장 위치 (신규) | **macOS 는 Keychain, 설정 디렉토리별로 분리.** `Claude Code-credentials-<sha256(config_dir)[:8]>` (기본 디렉토리만 접미사 없음). `.credentials.json` 파일은 생기지 않는다 |
| **K** | Orca 네이티브 멀티계정으로 대체 가능한가 | **✘ 거짓.** 구현은 됐으나 **런타임당 활성 계정 1개**(전역 스왑). `--account` 플래그 없음. `CLAUDE_CONFIG_DIR` 방식 유지 |

[claude-code#75408]: https://github.com/anthropics/claude-code/issues/75408

*(C = 캐시 만료 계산: 부록 A-2 로 트리거 자체가 삭제되어 검증 불필요)*

---

## 13. 로드맵

| Phase | 산출물 | 완료 기준 |
|---|---|---|
| **0** | `phase0/probe.sh` — H · I · J0~J3, 이어서 A(장시간) · D · E · F · G | 판정표 갱신. 실행 계층 확정 |
| **1** | statusLine 수집기, `agent register`, `doctor` | 계정 2개 상태가 status.json에 정확히. fail-closed 확인 |
| **2** | `.envrc` 고정 매핑, `team.yaml`, `ocp ask` | 에이전트 3개가 각자 계정에서 서로 요청을 주고받음 |
| **3** | 훅 이벤트, fold, 검증·롤백, 자정 롤오버 | 모델 토큰 0으로 이벤트 누락·중복 0 |
| **4** | 데몬 — 레벨 트리거, handoff, park, 리셋 재개, THROTTLED | 데몬 SIGKILL 후 전 상태 복구. 90% 도달 시 무인으로 park·재개 |
| **5** | TUI | — |
| **v2** | 슬롯·리스·큐 (§6·§7) | 에이전트 수 > 계정 수 × 2 가 되면 |

Phase 1~2만으로도 실사용 가치가 있다. Phase 4가 진짜 이득이다.

---

## 14. 남은 결정

1. **계정 수 ↔ 에이전트 수** — v1 고정 매핑이 성립하는지(§0.5). 에이전트가 계정의 2배를 넘으면 v2를 앞당겨야 한다
2. **`autoContinueAtUsageLimit`** — 우리가 관리 vs Claude Code에 위임
3. **컨텍스트 트리거 임계 60%** — Phase 0에서 재적재 비용 실측 후 조정
3-b. **`reviewer`와 `archivist`를 한 계정에 묶어도 되는가** — 둘 다 가볍지만 동시에 붙으면 §1의 credential 레이스 조건이다. `archivist`가 ephemeral이라 겹치는 창이 짧을 뿐, 0은 아니다. 계정이 4개면 분리하는 게 안전하다
4. **부록 A-2 (캐시 트리거 삭제)** — 원래 요구사항 ④를 대체하는 제안이다. 근거는 부록에 있고, 반대하시면 되돌린다
5. **journal 보존 정책** — git repo라 무한 보존이 기본. 압축 시점만 정하면 됨

---

## 부록 A. 기각한 것과 이유

v0.3까지 있었으나 v0.4에서 뺐다. 나중에 누가 다시 제안할 때를 위한 기록.

**A-1. `score = headroom / max(time_left, 0.05)`** — 절대 여유가 아니라 **페이스 비율**이라 의도를 뒤집었다. pct=0인 빈 계정도 리셋 후 50분간 heavy 부적격이고, 리셋 15분 전 pct=89인 계정이 score 2.2로 통과했다. 여유가 적을수록 점수가 높았다. `runway` 하나로 배정과 정렬을 모두 하면 이 왜곡이 원천적으로 없다.

**A-2. 캐시 만료 트리거 (원래 요구사항 ④)** — 캐시 미스의 실제 추가 비용은 `N × (캐시쓰기 1.25배 − 캐시읽기 0.1배) ≈ 1.15N`이고 **1회성**이다. 다음 턴에 캐시가 재생성되므로 반복되지 않는다. 반면 handoff는 fold + 새 세션 재적재(10~30k)가 든다.
→ 컨텍스트가 작으면 캐시 만료를 **그냥 무시하는 게 싸고**, 컨텍스트가 크면 캐시와 무관하게 handoff해야 한다. **두 경우 모두 캐시가 결정에 영향을 주지 않는다.** 그래서 트리거를 `context_pct`로 일원화했다.
부수 효과로 `prompt_cache` 감시, `lead_time` 검증(`lead_time >= ttl`이면 매 턴 handoff하는 버그), `min_handoff_interval`, TTL 1h↔5m 동적 전환 대응이 **전부 불필요해졌다.** `prompt_cache`는 대시보드 표시용으로만 남긴다.

**A-3. `max_concurrent ≥ 2` / 서브쿼터 공유** — credential 복사가 refresh token 로테이션 때문에 재인증 캐스케이드를 일으킨다. 증상이 "무작위 재인증 요구"라 원인 추적이 거의 불가능하다. 계정이 부족하면 공유가 아니라 큐잉으로 푼다.

**A-4. `pooled:N` intake** — 계정이 적으면 어차피 N=1이라 `dedicated`와 같다. 슬롯이 남아돌 때 다시 꺼낸다.

**A-5. `blocking` response** — 요청자가 슬롯과 계정 할당을 물고 논다. 계정이 유한한 시스템에서 가장 아까운 낭비다. 옵션으로 남겨두면 누군가 쓴다.

**A-6. `sticky` 계정 affinity** — 캐시는 세션 단위지 계정 단위가 아니라서 같은 계정으로 돌아갈 이점이 거의 없다. 유일한 이점이던 폴더 신뢰 상태는 `doctor`가 전 계정에 미리 심으면 사라진다.

**A-7. `events.jsonl` 파일 + 바이트 오프셋 커서** — SQLite로 옮기면 `WHERE id > cursor` 한 줄이 된다. 파일이면 fold 중 도착한 이벤트의 경계 처리, 훅의 지각 append, 파일 순서와 timestamp 순서 불일치를 전부 따로 다뤄야 했다.

**A-8. 다이제스트 `.md.1` 세대 파일** — `~/.orcapipe`를 git repo로 두면 세대 보존·롤백·diff가 공짜다.

---

*v0.5 · 2026-09-16 — v1 범위 축소*
