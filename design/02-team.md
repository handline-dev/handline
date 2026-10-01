# 팀 모델 — 역할 · 관계 · 라우팅 · 컨텍스트 예산

> 인프라(슬롯·트리거·기록)는 `design/01-infra.md` 참조. 이 문서는 그 위에서 **팀이 어떻게 일하는가**만 다룬다.
> 설계 목표 한 줄: **에이전트는 "누구에게 무엇을"만 말하고, "어떻게"는 전부 시스템이 정한다.**

---

## 0. 먼저: 계정 질문에 대한 답

### 하나의 `.claude` 폴더로 여러 계정을 쓸 수 있는가 → **없다**

Claude Code는 credential을 프로필 키 기반 저장소에 두지 않는다. **디렉토리 하나가 계정 하나 전체**다. 대신 `CLAUDE_CONFIG_DIR`은 **프로세스 시작 시점에 프로세스별로 읽히므로**, 서로 다른 값으로 띄운 두 에이전트는 두 계정에서 진짜로 병렬 실행된다. 우리 슬롯 모델이 성립하는 근거가 이것이다.

### 그럼 설정·이력 동기화 도구가 필요한가 → **도구는 필요 없고, 레이아웃이면 된다**

이건 이미 정립된 패턴이 있다. **계정이 소유하는 상태는 프로필별 실파일로, 사람이 소유하는 설정은 공유 위치로 심볼릭 링크**한다.

```
~/.orcapipe/shared/.claude/              ← 한 곳에서 관리, 모든 계정이 공유
  settings.json        # statusLine, hooks, permissions
  CLAUDE.md
  skills/ agents/ commands/ hooks/ output-styles/

~/.orcapipe/accounts/<acct>/.claude/
  .credentials.json    ← 실파일. 절대 공유 금지 (§1 보안)
  .claude.json         ← 계정 신원·온보딩 상태. 실파일
  projects/ history.jsonl sessions/ shell-snapshots/ backups/ plugins/   ← 실디렉토리
  settings.json  → ../../../shared/.claude/settings.json
  CLAUDE.md      → ../../../shared/.claude/CLAUDE.md
  skills/        → ../../../shared/.claude/skills
  agents/ commands/ hooks/ output-styles/ → (동일)
```

스킬을 `shared/.claude/skills`에서 한 번 고치면 모든 계정이 즉시 반영된다. `ocp doctor`가 링크 무결성을 점검한다 — 복원·백업 과정에서 심볼릭이 실파일로 바뀌는 사고가 이 패턴의 알려진 함정이다.

### CONFIG_DIR을 계정별로 둘까, 에이전트별로 둘까 → **계정 수만큼**

**디렉토리는 계정의 몸이고, 에이전트는 그 위를 옮겨 다니는 역할이다.**

| | 계정별 (채택) | 에이전트별 |
|---|---|---|
| 디렉토리 수 | 계정 수 N | 에이전트 수 M (보통 M > N) |
| 계정 변경 | **환경변수 하나. 파일 조작 0회** | credential 링크 재지정 + `.claude.json` 동기화 |
| 동시 실행 상한 | N | **여전히 N** |
| 이중 점유 방어 | 파일시스템이 구조적으로 보장 | 리스 하나에만 의존 |

세 가지 이유로 계정별이다.

1. **디렉토리를 늘려도 슬롯이 늘지 않는다.** 동시 실행 상한을 정하는 건 디렉토리 수가 아니라 **credential 수**다. 에이전트 9개에 디렉토리 9개를 만들어도 계정이 2개면 동시 실행은 2개다. 디렉토리 9개가 credential 2개를 놓고 싸울 뿐이다.
2. **계정 변경 자유도가 오히려 계정별 쪽이 높다.** 직관과 반대인데, `CLAUDE_CONFIG_DIR`은 **프로세스 시작 시점에 읽힌다.** 계정별이면 계정 변경 = 실행할 때 변수 하나 바꾸기, 파일 조작 0회. 에이전트별이면 매 변경마다 credential 경로를 건드려야 하고, 그건 §1에서 금지한 영역이다.
3. **방어선이 하나 더 있다.** 계정별은 "디렉토리 하나 = 계정 하나"가 구조적으로 보장된다 — 두 에이전트가 같은 계정을 쓰는 상태를 **표현할 수조차 없다**. 에이전트별이면 두 디렉토리가 같은 credential을 가리키는 걸 파일시스템이 막지 않는다. 리스 하나에만 의존하게 된다.

### 그럼 에이전트별 디렉토리가 필요해 보이는 이유들은 어디로 가나

| 원래 이유 | 실제 해법 |
|---|---|
| 에이전트별 훅·권한·MCP 설정 | **worktree의 `.claude/settings.json`.** 에이전트마다 worktree가 따로 있으니 여기가 제자리다. `--append-system-prompt` / `--permission-mode` / `--mcp-config` 플래그도 있다 |
| 에이전트별 이력 분리 | `agents/<name>/journal/` — 이미 에이전트 이름으로 묶인다 |
| transcript 산재 | statusLine의 `transcript_path`가 항상 현재 위치를 알려준다. `--resume`을 쓰지 않으므로 무관 |

### 계정별의 실제 비용 하나

프로젝트 신뢰 상태(`.claude.json`의 "이 폴더를 신뢰합니까")가 **계정 N × worktree M 조합**만큼 필요하다. 새 worktree를 만들 때 `ocp doctor`가 전 계정에 미리 심어두면 된다 — 일회성이다.

그리고 **Phase 0 가정 H**: `~/.claude.json`은 `~/.claude/` **밖**에 있는데 공식 문서는 여기에 auth가 들어있다고 한다. 이게 `CLAUDE_CONFIG_DIR`을 따라가지 않으면 계정 격리에 구멍이 난다. 30초면 확인된다.

### 이력 동기화는 하지 않는다 — 필요가 없다

"계정을 스위칭해도 이력이 이어지게"가 원래 요구였는데, **우리 설계에서는 이 문제가 애초에 발생하지 않는다.**

- 재개는 `claude --resume`이 아니라 **handoff 주입**으로 한다. 이전 세션의 transcript가 어느 계정 디렉토리에 있든 무관하다
- 그 handoff는 `handoff.record`가 디스크에 있는 사실 셋에서 **결정론적으로** 만든다. 재개 경로에서는 모델을 부르지 않는다 (`fold.run_model`은 `ocp fold`만 쓴다)
- 에이전트의 단일 이력은 `agents/<name>/journal/`이다. **계정과 무관하게 에이전트 이름으로 묶인다**
- 현재 transcript 위치가 필요하면 statusLine이 `transcript_path`로 알려준다

`projects/`를 공유하면 `--resume`이 계정을 넘나들게 만들 수는 있지만, 쓰기 충돌 위험이 검증되지 않았고 우리에게 이득도 없다. **공유하지 않는다.**

---

## 1. 컨텍스트 4계층 — base.md의 자리

| 계층 | 상한 | 수명 | 주입 | 누가 쓰나 |
|---|---|---|---|---|
| `ROLE.md` | **1 KB** | 불변 | 매 세션 | 사람 |
| `base.md` | **2 KB** | 준불변 | 매 세션 | 승격(사람 승인) |
| handoff (`agent_state.handoff`) | 작음 | 휘발 | 재개 시만 | 결정론 (모델 없음) |
| `journal/<date>.md` | 무제한 | 영구 | **주입 안 함** | fold |

### 왜 빡빡해야 하는가 — 이건 고정비다

`ROLE + base`는 **매 세션 시작마다 무조건 프리필된다.**

```
3 KB ≈ 750 토큰 × 에이전트 9개 × 하루 20세션 ≈ 135k 토큰/일 (고정)
base.md가 2 KB → 8 KB로 늘면 그것만으로 하루 270k 토큰이 추가된다
```

base.md는 방치하면 반드시 비대해진다. 유용한 사실이 계속 발견되기 때문이다. 그래서 상한이 아니라 **예산제**로 운영한다.

### base 예산제

```
하드 상한 2 KB. 초과하면 ocp doctor 실패 (경고 아님)
추가하려면 빼야 한다:
  base.md를 직접 편집한다 (예산은 ocp doctor가 확인)
자동 승격 금지. fold는 후보만 제안한다 (다이제스트의 ## base 승격 후보 섹션)
```

### 무엇이 base에 들어가는가

**들어갈 것** — 바뀌지 않고, 코드를 읽어서는 알 수 없고, 반복 참조되는 것

```markdown
# base · backend
스택: FastAPI + Postgres + Alembic. ORM은 SQLModel.
금지: 마이그레이션 직접 작성 금지 — 스키마 변경은 product 승인 후 별도 태스크.
컨벤션: 라우터는 기능별 파일. 서비스 레이어 필수. 컨트롤러에 로직 금지.
용어: "세션"은 사용자 세션이지 Claude 세션이 아님.
결정: 리프레시 토큰은 httpOnly 쿠키 (2026-09-15, 번복하려면 product 경유)
```

**들어가면 안 될 것**

- 현재 작업 상태 → `handoff`
- 지나간 이력 → `journal`
- 코드를 읽으면 알 수 있는 것 (파일 구조, 함수 시그니처) → 읽게 둔다
- "이번 주 목표" 같은 유통기한 있는 것 → 유통기한이 지나도 아무도 안 지운다

---

## 2. 관계 — relation 과 response, 두 축의 선언

에이전트 사이 관계를 두 축으로 선언한다. 에이전트는 이 값을 **알 필요조차 없다.**

### 축 1 — relation: 요청의 성격

| 값 | 의미 | 거부 가능 | 큐 우선순위 | 실패 시 |
|---|---|---|---|---|
| `supervisor → subordinate` | 명령 | ✘ | 높음 | 대상의 현재 작업을 선점 |
| `peer ↔ peer` | 요청 | ✔ | 보통 | 거부 사유를 회신 |
| `subordinate → supervisor` | 에스컬레이션 | — | 즉시 | 게이트(`creates_gate`) |

상하관계의 실질적 차이는 호칭이 아니라 **거부권과 선점권**이다. 그것만 다르다.

### 축 2 — response: 요청자는 어떻게 되나

| 값 | 요청자 상태 | 비용 |
|---|---|---|
| `async` **(기본)** | handoff 후 PARKED. 결과 도착 시 재개 | 컨텍스트 반납 |
| `fire_and_forget` | 그냥 계속 진행 | 없음 |

`blocking`(요청자가 대기)은 **두지 않았다.** 요청자가 리뷰를 기다리며 슬롯과 계정 할당을 물고 노는 게 계정이 유한한 시스템에서 가장 아까운 낭비다. 옵션으로 남겨두면 누군가 쓴다.

기본은 `async` — 요청자는 handoff하고 park한다. 인프라 설계서 §8 상태머신과 맞물린다.
(에이전트는 한도에 닿으면 pick이 고른 계정으로 한 번 옮겨 가고, 놀던 계정은 다음 pick에서 쓰인다.)

코드에서 에지는 `relation`(`supervisor` 만 특별 취급, 나머지는 peer), `response`(`async` | `fire_and_forget`, 그 밖의 값은 로드 실패), `kind`(`task` 는 창업자에게 가는 결정 작업을 만든다 — 예전 이름 `escalate` 도 같은 뜻으로 읽는다, `verdict`, `report`) 를 가진다. 요청은 `--fyi` 로 보고가 되며, 보고는 사람이 열면 닫힌다.

### 작업 (task)

요청 위에 작업이 있다: `ocp task new|list|queue|draft|link|edit|merge|done|reopen|discard|board`. 작업은 draft(기록만) → queued(창업자나 소유자의 매니저가 승인) → doing(요청이 나가 있음) → done 으로 간다. done 은 최종이고, 결과가 틀리면 연 사람이 reopen 한다. 열린 하위 작업이나 열린 blocker 가 있으면 done 이 되지 않는다. 끝나는 길은 done 과 discard 둘뿐이고, `task merge` 로 합쳐진 작업은 'merged into T<n>' 이유의 discard 다. 작업은 제목·소유자·연 사람·부모를 갖고, 요청을 연결해 두면 누가 지금 들고 있는지 보인다. `done` 은 근거 하나를 요구한다 (main 에 있는 merge sha, 결과가 이미 보고된 요청 id, 또는 커밋이 남지 않는 일의 사실 한 줄). 닫는 건 소유자나 연결된 요청의 대상, 재개·폐기는 연 사람이다.

---

## 3. 에이전트가 쓰는 명령은 둘뿐

```bash
ocp task new --owner <target> "<제목>" "<요청>"   # 일: 결과가 필요하면 작업이다
ocp say <target> "<메시지>"                       # 답이 필요 없는 말, 바로 입력된다
```

(`ocp ask`, `ocp resolve`, `ocp dismiss` 는 T560 에서 없어졌다.)

에이전트가 아는 것: **대상과 내용.** 그게 전부다.

시스템이 자동으로 정하는 것:

```
관계 허용 여부 → 거부면 즉시 실패, 이유와 함께
우선순위       → relation + 요청자의 크리티컬패스 위치
계정            → 측정한 번레이트로 pick (인프라 §0.5, §6·§7 은 만들지 않음)
요청자 처리    → async면 handoff + park
응답 경로      → 요청자의 inbox 파일, 데몬이 세션에 입력해 알림
체인 깊이      → 순환 방지
```

요청이 거부되면 에이전트는 이유를 받는다: `denied: eng.* → growth.* 는 product 경유 필요`. 에이전트가 정책을 추론하거나 우회를 시도할 여지가 없다.

---

## 4. `team.yaml` — 라우팅 정책

```yaml
teams:
  product: [product]
  eng:     [backend, frontend]
  growth:  [marketing, analytics]
  ops:     [archivist]

agents:
  product:    { model: mid,   weight: light }
  backend:    { model: high,  weight: heavy }
  frontend:   { model: high,  weight: heavy }
  marketing:  { model: mid,   weight: light }
  analytics:  { model: mid,   weight: light }
  archivist:  { model: cheap, weight: light }
  reviewer:   { model: mid,   weight: light, reviews: [eng, growth, product] }

edges:
  - { from: "*",         to: "reviewer",     relation: peer,  response: async }
  - { from: "reviewer",  to: "*",            kind: verdict,   response: async }
  - { from: "product",   to: "eng.*",        relation: peer,  response: async }
  - { from: "product",   to: "growth.*",     relation: peer,  response: async }
  - { from: "analytics", to: "product",      relation: peer,  response: async }
  - { from: "*",         to: "archivist",    relation: peer,  response: fire_and_forget }
  - { from: "*",         to: "orchestrator", kind: task }

deny:
  - { from: "eng.*",    to: "growth.*",  reason: "팀 간 직접 요청 금지 — product 경유" }
  - { from: "growth.*", to: "eng.*",     reason: "동일" }

limits:
  max_chain_depth: 4
  ask_rate_per_hour: 20        # 에이전트당
```

**명시되지 않은 에지는 거부가 기본이다.** 그래야 에이전트가 아무 데나 요청을 뿌리지 않는다.

---

## 5. 오케스트레이터에 모델을 쓰지 않는다

이게 저비용 운영의 가장 큰 항목이다.

대부분의 멀티 에이전트 시스템은 **오케스트레이터 LLM이 토큰을 가장 많이 쓴다.** 모든 워커의 보고를 읽고, 다음 할 일을 정하고, 다시 지시하기 때문이다. 컨텍스트가 워커 수에 비례해 커진다.

우리 설계에서 `orchestrator`는 **에이전트가 아니라 데몬이다.**

```
태스크 의존성 해소  → 작업의 `--blocked-by` (막던 작업이 닫히면 알림)
계정 배정          → 측정한 번레이트로 pick (인프라 §0.5)
요청 라우팅        → team.yaml (선언)
handoff 시점 판단  → 인프라 §8 트리거 (조건식)
재개 판단          → 인프라 §8.6 reconciliation
```

전부 결정론적이라 모델이 필요 없다. 사람 판단이 정말 필요한 지점(에스컬레이션, 게이트)에서만 **사람**에게 간다. 모델에게 가지 않는다.

### 모델 티어 매핑

| 역할 | 티어 | 근거 |
|---|---|---|
| orchestrator | **없음** | 데몬 |
| archivist (fold) | cheap | 구조화된 재작성. 판단 불필요 |
| reviewer | mid | 판단은 필요하나 생성량이 적음 |
| backend / frontend | high | 실제 구현 |
| product / marketing / analytics | mid | — |

### 그 외 비용 장치

- **ask 병합**: 같은 대상에 대한 연속 요청은 큐에서 병합 가능(`--mergeable`). 리뷰는 커밋 단위가 아니라 **PR 단위로 1회**
- **`blocking` 금지**로 유휴 슬롯 제거 (§2)
- **fold는 cheap 티어**로 (인프라 §5)
- **base 예산제**로 고정비 억제 (§1)

---

## 6. 순환과 폭주 방지

```
모든 ask에 chain_id + depth 부여
depth > max_chain_depth → orchestrator 에스컬레이션 (gate)
같은 chain 안에서 A→B→A 감지 → 즉시 중단 + gate
에이전트당 시간당 ask 상한 → 초과 시 THROTTLED (인프라 §8.8)
```

리뷰 루프가 특히 위험하다: `backend → reviewer → (지적) → backend → reviewer → ...`
같은 파일에 대한 리뷰 왕복이 3회를 넘으면 **자동으로 product에게 에스컬레이션**한다. 두 에이전트가 합의하지 못하는 건 대개 기획 문제다.

---

## 7. 폴더 레이아웃 (인프라 §3 보강)

```
~/.orcapipe/agents/<name>/
  agent.yaml           # model, weight, account affinity
  ROLE.md              # ≤1KB
  base.md              # ≤2KB  ← 신규
  journal/
    2026-09-15.md        # 세대 보존은 git이 한다 (~/.orcapipe는 git repo)
  state.json
```

---

## 8. 도입 순서 — 7명으로 시작하지 않는다

에이전트 7개는 계정 2~3개로 감당이 안 된다. v1은 **계정당 에이전트 고정 매핑**(인프라 §0.5)이라 계정 수가 곧 상한이다.

| 단계 | 에이전트 | 이유 |
|---|---|---|
| **1** | `backend`, `reviewer`, `archivist` | 최소 루프. 구현 → 리뷰 → 기록이 돌면 나머지는 복제다 |
| **2** | `+ frontend`, `+ product` | 팀 간 요청과 에스컬레이션 경로 검증 |
| **3** | `+ marketing`, `+ analytics` | growth 팀 |

`archivist`와 `reviewer`는 계정을 오래 잡지 않아 한 계정에 묶을 수 있다. 실질적으로 계정을 오래 점유하는 건 `backend`·`frontend`뿐이다.

## 9. 열린 결정

1. **`product`가 supervisor인가 peer인가** — 위 `team.yaml`은 peer다. supervisor면 product가 eng의 작업을 선점할 수 있다
2. **리뷰어를 나중에 팀별로 쪼갤지** — 하나로 시작하고, base.md 2KB가 세 팀 기준을 담기 버거워지면 그때 쪼갠다. 그 시점이 곧 분리 신호다
3. **`analytics`의 데이터 접근** — 지표 소스에 따라 MCP 커넥터가 필요할 수 있다. 별도 논의

---

*2026-09-16 · 인프라 설계서 v0.5의 동반 문서*
