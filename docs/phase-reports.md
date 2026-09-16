# Phase 보고 (KICKOFF §5 형식)

---

## Phase 0 완료

**한 일** — 프로브를 돌려 H·I·J0·J4 를 판정하고, 근거(명령·출력)를 `docs/phase0-findings.md` 에
남긴 뒤 §12 판정표를 갱신했다. 코드보다 먼저 커밋했다 (`bdbf579`).

**판정표에서 바뀐 것**

| # | 전 | 후 |
|---|---|---|
| H | 미확인 | **참** — `.claude.json` 이 프로필 안에 생기고 홈은 unchanged |
| I | 미확인 | **참, 예상보다 좋다** — `context_window.used_percentage` 가 직접 온다 |
| J0 | 미확인·최우선 | **거짓(유리)** — Orca 는 statusLine 을 강제하지 않는다. 훅으로 붙는다 |
| J4 | 미확인 | **해당 없음** — direnv 가 필요 없다 |
| A | 미확인 | **격리 측면 참** — 비기본 CONFIG_DIR 은 Keychain 을 안 쓴다 |
| F | 미확인 | **기능 실재 확인** — 택일 강제 필요 |
| K (신규) | — | **거짓** — Orca 네이티브 멀티계정은 런타임당 활성 1개(전역 스왑) |

**설계와 다르게 구현한 것**

1. **`probe.sh i` 를 그대로 쓰지 않았다.** `claude -p` 는 헤드리스라 statusLine 이 없다.
   그대로 돌리면 "수집기가 죽었다"는 **거짓 음성**이 난다(실제로 처음에 그렇게 나왔다).
   진짜 pty 에서 인터랙티브로 띄우는 `libexec/ptyrun.py` 로 바꿨다.
   사용자의 `settings.json` 은 건드리지 않고 `--settings` 로 주입했다.
2. **J4 의 1순위 대안이 오독이었다.** `orca.yaml environmentRecipes` 는 워크스페이스마다
   **일회용 원격 머신**을 띄우는 기능이지 로컬 env 주입 장치가 아니다. 대안이 될 수 없었다.

**다음 Phase 의 미확인 전제** — A(장시간 refresh 충돌), J4-full(래퍼로 띄운 에이전트를 Orca 가
에이전트로 인식하는가). 둘 다 **2번째 계정 로그인**이 있어야 닫힌다. 사람만 할 수 있다.

---

## Phase 1 완료

**한 일** — 수집기(§4 필드만 allowlist·원자적 쓰기), `ocp account/agent register`,
`ocp doctor`(18개 점검), 그리고 실행 계층인 런처를 만들었다.

**판정표에서 바뀐 것** — 없음. Phase 0 판정 위에서 구현만 했다.

**설계와 다르게 구현한 것**

1. **direnv → 런처 래퍼** (사용자 승인함). Orca 가 `CLAUDE_CONFIG_DIR` 을 비워두고
   `--command` 가 임의 셸을 받으므로 `ocp launch <agent>` 한 겹이면 된다. 새 의존성이 0이고,
   Orca 가 hibernation/restore 때 실행 커맨드를 보존하므로 **.envrc 보다 복원에 강하다.**
2. **status.json 에 원시 stdin 을 통째로 넣지 않는다.** KICKOFF §1.5 의 취지대로
   §4 표의 필드만 allowlist 로 뽑는다. 설계서 §4 문장은 "stdin JSON 을 그대로 쓴다"에 가깝지만,
   토큰 유출 경로를 줄이는 쪽을 택했다.
3. **Orca 훅 미러링을 추가했다** (사용자 지시: file watch & apply auto). 설계에 없던 항목이다.
   계정별 CONFIG_DIR 을 쓰면 Orca 훅이 없어서 대시보드에서 에이전트가 사라지기 때문이다.
   `ocp hooks sync --watch` 가 원본을 감시해 자동 반영한다.
4. **`autoContinueAtUsageLimit: false` 를 shared 에 못 박았다** (가정 F). 택일을 강제하라는
   §4 요구를 doctor 점검 + 기본값으로 구현했다.

**다음 Phase 의 미확인 전제** — 계정 2개의 status.json 동시 갱신은 **로그인 전까지 확인 불가**.
단일 계정(default)에서는 실세션으로 확인했다.

---

## Phase 2 완료

**한 일** — `team.yaml` 라우팅(패턴·deny 우선·기본 거부), 에이전트가 쓰는 유일한 명령 `ocp ask`,
그리고 이벤트/요청/커서를 담는 SQLite 계층을 만들었다.

**판정표에서 바뀐 것** — 없음.

**설계와 다르게 구현한 것**

1. **에지는 first-match-wins 로 정했다.** 설계서가 순서 의미를 명시하지 않았는데,
   설계서 자신의 `team.yaml` 이 순서에 의존한다(`reviewer → *` 가 `* → archivist` 보다 앞이라
   리뷰어의 verdict 는 async 로 남는다). 방화벽 규칙과 같은 관례를 택했고 주석에 남겼다.
   **deny 는 순서와 무관하게 항상 이긴다.**
2. **`orchestrator` 같은 에스컬레이션 대상을 라우팅 가능하게 했다.** `agents`/`teams` 에 없어서
   처음엔 "unknown target" 으로 막혔다. 와일드카드가 아닌 `to` 에 문자로 적힌 대상은
   등록 없이도 라우팅 대상으로 인정한다.
3. **내장 YAML 파서에 flow 컬렉션을 넣었다.** 설계서 `team.yaml` 이 `{ }`/`[ ]` 로 쓰여 있는데
   초기 파서가 이를 거부했다. 지금은 설계서의 실제 `team.yaml` 에 대해 PyYAML 과 **완전 일치**한다.
4. **async 요청자의 상태 이름을 §8.2 로 맞췄다** (Phase 4 에서 발견). 처음에 `PARKED_AWAITING` /
   `WAKEABLE` 을 임의로 만들었는데 데몬이 평가 대상에서 빼버렸다. 지금은 `PARKED` / `QUEUED` 다.
   **응답이 영영 안 와도 갇히지 않도록 fallback `wake_at` 을 붙였다** (§8.4 가 경고하는 영구정지).

**다음 Phase 의 미확인 전제** — "각자 계정에서" 주고받는 부분은 로그인 후에만 확인 가능.
라우팅·큐·거부는 계정 없이 전부 확인했다.

---

## Phase 3 완료

**한 일** — 6개 고정 섹션 다이제스트, 검증·롤백이 붙은 fold, 항상 붙는 결정론 블록,
자정 롤오버, 그리고 훅 이벤트 수집을 만들었다.

**판정표에서 바뀐 것** — 가정 **E** 의 적용 범위가 줄었다. §8.1 컨텍스트 트리거가
`context_window.used_percentage` 를 직접 쓰므로 **트리거 경로에는 transcript 집계가 없고
따라서 `message.id` dedup 도 필요 없다.** dedup 은 DB 에 그대로 구현해 뒀다 — 번레이트를
transcript 로 계산하게 되면 그때 필수다.

**설계와 다르게 구현한 것**

1. **fold 자신의 기록(`fold`·`fold_failed`)을 fold 델타에서 제외했다.** 빼지 않으면 fold 가
   자기 부기를 다시 요약하고 커서가 자기 꼬리를 쫓는다. 테스트가 이걸 잡았다.
2. **훅 페이로드에서 프롬프트 본문을 저장하지 않는다.** 길이만 넣는다. 설계서가 명시하진 않았지만
   §1.5 와 같은 이유다.

**다음 Phase 의 미확인 전제** — 실제 모델로 도는 fold 는 **로그인 후에만** 확인 가능.
성공·거부·타임아웃·재시도 경로는 모델을 모킹해 전부 확인했다.

---

## Phase 4 완료

**한 일** — 레벨 트리거(+번레이트 투영), 단계별로 커밋되는 handoff, park·무인 재개,
THROTTLED/STALLED 승급, 기동 시 무조건 reconciliation, 베이스 드리프트를 만들었다.

**판정표에서 바뀐 것** — 없음.

**설계와 다르게 구현한 것**

1. **`pct_5h` 가 null 이면 트리거가 **발화**한다** (`usage_unknown`). 설계서 §8.1 은 두 트리거만
   적었지만, §4 의 fail-closed 원칙을 트리거에도 적용했다. 모르는 채로 계속 도는 것보다
   handoff 하는 쪽이 안전하다.
2. **드리프트를 판정하지 못했을 때 "재수행하지 마십시오" 헤더를 쓰지 않는다.** §8.8 은 겹침
   유무 2가지만 적었는데, 판정 실패라는 3번째 경우가 있다. 이때 안심시키는 헤더를 주면
   fail-open 이라 작업이 조용히 사라지거나 중복된다. **미상은 겹침과 같게 취급한다.**
3. **DB 마이그레이션을 넣었다.** Phase 2 의 handoff 테이블이 §8.9 스키마와 달라서,
   새 인덱스가 없는 컬럼을 참조하며 `CREATE INDEX` 가 먼저 터졌다. handoff 행은 휘발성
   진행상태라 재생성하고, events·requests·cursors 는 보존한다.
4. **`ocp ps` / `ocp triggers` / `ocp unstall` 을 추가했다.** 설계서 §9 CLI 목록에 없지만,
   무인 시스템은 "지금 왜 이런 판단을 하는가"를 사람이 볼 수 있어야 한다.

**[v2] 로 표시돼 구현하지 않은 것** — §6 슬롯·리스, §7 스케줄링/`runway`, §8.7 thundering herd.
지시대로 손대지 않았다.

**아직 미확인인 것** — 아래 "남은 것" 참조.

---

## 로그인 후 실측 (2026-09-16)

창업자가 alpha·beta 둘 다 로그인한 뒤 남은 가정을 전부 닫았다.

| 가정 | 결과 |
|---|---|
| **A** (동시 실행) | **참.** 계정 2개를 동시에 띄워 각각 정확한 `status.json` (alpha 5h 0%/7d 40%, beta 5h 20%/7d 49%). 서로 다른 세션·비용, credential 상호 무효화 없음 |
| **J4-full** | **참.** Orca 가 래퍼로 띄운 세션을 `agentIdentity: "claude"` 로 인식하고 탭 제목을 `✳ Claude Code` 로 바꿨다. 미러링한 Orca 훅이 제 역할을 했다 |
| **Phase 1 완료조건** | **충족.** 계정 2개 상태 정확 + `pct_5h` 를 null 로 만들자 그 계정만 배정 거부되고 다른 계정이 선택됨 |
| **Phase 2 완료조건** | **충족.** backend=alpha, reviewer/archivist=beta 로 각자 계정에서 동작 |
| **L** (신규 발견) | credential 은 **macOS Keychain** 에 설정 디렉토리별로 들어간다: `Claude Code-credentials-<sha256(dir)[:8]>`. `.credentials.json` 파일은 안 생긴다 |

### 이 때문에 고친 버그 2개

1. **로그인 판정이 틀렸다.** `.credentials.json` 존재로 판정했는데 macOS 에는 그 파일이 없다.
   로그인이 멀쩡히 돼 있는데 `doctor` 가 "not logged in" 으로 실패하고 `ocp launch` 가 기동을 거부했다.
   Keychain 항목 존재로 판정하도록 고쳤다 — `security find-generic-password` 를 `-w` 없이 불러
   **존재만** 보고 비밀값은 읽지 않는다.
2. **재등록이 `--model` 을 무시했다.** reviewer 를 sonnet 으로 다시 등록했는데 opus 로 떴다.
   명시한 값은 반영하고, 생략하면 기존 값을 유지한다.

---

## 남은 것

`ocp doctor` 는 이제 **실패 0** 이다. 아직 직접 못 본 것은 둘뿐이다.

| | 왜 아직인가 |
|---|---|
| **hibernate/restore 가 계정을 보존하는가** | Orca 가 실행 커맨드를 보존한다는 문서상 보장 위에 서 있다. 우리 커맨드가 곧 래퍼라 재개하면 같은 계정으로 돌아오는 것이 **따라 나오지만**, 실제로 hibernate 시켜 본 것은 아니다 |
| **실제 모델 fold** | 성공·거부·타임아웃·재시도 경로는 모델을 모킹해 전부 확인했다. 진짜 모델로 한 번 돌려 형식을 지키는지는 `ocp fold backend` 로 언제든 확인할 수 있다 |
