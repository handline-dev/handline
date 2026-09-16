# Phase 0 — 실행 계층 검증 (축소판)

> 앞선 판에서 tmux 하이브리드를 제안했다가 **철회했다.** 복잡도의 출처를 다시 보니
> tmux가 필요했던 이유(PID 소유권)가 v1에 없는 기능(리스)에서 왔다.
> v1은 **Orca 안에서, direnv로** 간다.

---

## 1. Orca 이슈 트래커에 같은 요청이 있다 — 미구현

[stablyai/orca#11557 — *Support multiple Claude Code subscriptions (separate config dirs / env vars)*](https://github.com/stablyai/orca/issues/11557)

요청자의 말이 우리 상황과 같다: 터미널에서는 `~/.claude` / `~/.claude-work` 로 나눠 쓰는데
**Orca 안에서는 같은 걸 할 방법을 못 찾았다.** 유지보수자 응답도, 구현도 없다.

정황 증거도 같은 방향이다. 공식 문서의 *"switching accounts works even with live Claude sessions running"* 과
*"a guard so you don't trigger overlapping auth refreshes"* — 전환이 실행 시 env 주입이었다면
**이미 돌고 있는 세션이 영향받을 수 없고 guard도 필요 없다.** Orca는 전역 스왑 부류다.

---

## 2. 그래서 Orca 안에서 되는 방법 — worktree `.envrc`

```bash
# agents/backend 의 worktree/.envrc
export CLAUDE_CONFIG_DIR="$HOME/.orcapipe/accounts/alpha/.claude"
```

이러면 **Orca에서 평소처럼 에이전트를 띄우면 된다.** `worktree create --agent claude` 도 쓸 수 있고,
hibernation · session restore · 대시보드가 전부 그대로 동작한다. 우리가 실행 경로에 끼어들지 않는다.

v1이 **고정 매핑**이라 `.envrc` 가 정적이어도 문제가 없다. 이게 고정 매핑의 가장 큰 실익이다.

**보너스**: Orca의 agent hibernation은 재개 시 *"launch command, arguments, and **environment**"* 를 보존한다.
한 번 들어간 env가 재개 후에도 유지된다.

---

## 3. 남은 위험은 둘뿐

| | 묻는 것 | 거짓이면 |
|---|---|---|
| **J0** | Orca가 자기 statusLine을 **CLI 플래그로 강제**하는가 | §4 데이터 출처가 죽는다. **최우선** |
| **J4** | `.envrc`(direnv)가 Orca가 띄운 에이전트에 먹히는가 | ① `orca.yaml environmentRecipes` 시도 ② 그래도 안 되면 tmux 복귀 |

J0이 J4보다 중요하다. J4가 거짓이면 실행 방법을 바꾸면 되지만, **J0이 거짓이면 사용률을 읽을 방법 자체가 없다.**

---

## 4. 실행 순서

```bash
# 계정 1개면 되는 것
./probe.sh h        # ~/.claude.json 이 CONFIG_DIR 을 따라가는가
./probe.sh i        # statusLine 에 rate_limits / 컨텍스트가 오는가
./probe.sh j0       # Orca 가 claude 를 래핑 / statusLine 강제하는가   ← 최우선

# Orca worktree 필요
ORCA_WT_PATH=/path/to/agent/worktree ./probe.sh j4
```

`j0` 은 스크립트만으로 안 끝난다. **Orca 터미널 안에서** 직접:

```bash
env | grep -iE 'claude|anthropic|orca'
ps -o args= $(pgrep -f claude | head -1)
```

claude 프로세스의 argv 에 `--settings` / `--status-line` 류가 보이면 Orca가 강제하는 것이다.

### J4 거짓일 때

```bash
orca skills get orca-per-workspace-env     # orca.yaml environmentRecipes 형식 확인
```

이 형식은 Orca 바이너리가 직접 서빙한다(문서에 일부러 안 넣어둠 — 버전 드리프트 방지).
여기서도 env 주입이 안 되면 그때 tmux 하이브리드로 돌아간다. 설계는 그대로 두고 실행 계층만 바꾸면 된다.

---

## 5. 파일

| 파일 | 용도 |
|---|---|
| `statusline-collect.sh` | §4 수집기 프로토타입 **겸** 프로브 I. 그대로 Phase 1 로 이어진다 |
| `probe.sh` | H · I · J0 · J4 |

`statusline-collect.sh` 는 계정명을 `CLAUDE_CONFIG_DIR` 의 조부모 디렉토리에서 유도하므로
`shared/.claude/settings.json` 에 **한 벌만** 두면 모든 계정이 쓴다.

---

## 6. 폐기한 것

**tmux 하이브리드** — PID 소유권이 필요했던 이유가 §6 리스 회수인데 v1에 리스가 없다.
v2에서 슬롯 모델을 켤 때 다시 꺼낸다. 그때 필요한 논거는 설계서에 남겨뒀다.

**J1 · J2 · J3** (`terminal create --command` env 주입, `send`, `wait --for idle`) —
Orca 네이티브로 띄우면 전부 Orca가 알아서 하는 영역이라 검증할 이유가 없어졌다.
