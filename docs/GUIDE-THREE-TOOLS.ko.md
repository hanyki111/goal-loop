# Goal Loop·Memory Tool·AO를 함께 쓰기 시작하기

이 문서는 새 프로젝트(또는 이미 있는 프로젝트)에서 세 도구를 함께 써서 개발이나 작업을 시작하는 순서를 설명합니다.
세 도구는 각각 혼자서도 동작하므로, 필요한 것만 골라 써도 됩니다.

## 1. 세 도구의 역할

| 도구 | 맡는 일 | 기록이 남는 곳 |
|---|---|---|
| **Goal Loop** | 무엇을 이루려 하는지(Aim), 무엇을 하기로 했는지(Goal과 완료 기준), 무엇을 했고 무엇을 보았는지(Attempt·Evidence), 그리고 실행하지 않은 다른 에이전트의 판정(Verdict)을 기록한다. 완료 판단은 여기서 한다. | `<프로젝트>/.goal-loop/` |
| **Memory Tool** | 오래 남길 지식을 기록한다: 날짜별 타임라인(결정, 이정표)과 주제별 모듈 문서. 다음 세션의 에이전트가 맥락을 다시 읽는 곳이다. | `<프로젝트>/.memory/`(기본값) |
| **AO (AI Orchestration)** | Goal 하나를 여러 AI 에이전트 CLI로 실제로 실행한다. 프로젝트 저장소의 별도 사본(작업 공간)에서 git worktree 레인을 만들어 작업하고, 검증기(typecheck·시험 등)를 돌린다. | `<AO 작업 공간>/` |

함께 쓸 때의 흐름은 다음과 같습니다.

```text
Goal Loop: Aim → 접근안 → Goal(완료 기준)
   │  ao-attempt-start (Goal의 완료 기준을 AO Goal로 넘김)
   ▼
AO: 레인에서 구현·검증 → 결과(commit, 검증기 결과)
   │  runtime-worker가 결과를 받아 Evidence로 기록
   ▼
Goal Loop: 실행하지 않은 에이전트가 위임 판정 → Verdict
   │  memory-draft가 타임라인 한 줄 초안을 제안
   ▼
Memory Tool: 결정과 이정표를 타임라인에 기록
```

연결된 실행에서 AO는 `--external-verdict`로 실행됩니다. 이 경우 AO 내부의 "목표 달성" 재확인 같은 판정은 완료를 막지 않고
참고 기록(`completion:advisory`)으로만 남습니다. 완료 판단은 Goal Loop의 위임 판정이 맡습니다.

## 2. 필요 환경

- Python 3.11 이상: Goal Loop, Memory Tool
- Node.js 20 이상과 git: AO
- 작업과 판정에 쓸 에이전트 CLI와 로그인: Claude Code(`claude`), Codex CLI(`codex`) 등
- 확인한 운영체제는 Windows 11입니다. 아래 명령은 PowerShell 기준입니다.

## 3. 설치

### 3.1 Goal Loop

```powershell
pipx install git+https://github.com/hanyki111/goal-loop
goal-loop --help
goal-loop-judge --help        # 위임 판정 명령
```

가상환경에 설치해도 됩니다. 자세한 내용은 Goal Loop README의 "설치와 시작" 절을 보십시오.

### 3.2 Memory Tool

```powershell
git clone https://github.com/hanyki111/memory_tool.git
cd memory_tool
python -m pip install -e .
python -m memory_tool --help
```

`malias install --powershell`을 실행하면 `m`, `minit` 같은 짧은 명령을 쓸 수 있습니다. 이 문서는 별칭 없이 `python -m memory_tool …`로 적습니다.

### 3.3 AO

```powershell
git clone https://github.com/hanyki111/ai-orchestration.git <AO 설치 폴더>
cd <AO 설치 폴더>
npm ci
npm run orchestrate -- --help
```

Goal Loop는 이 폴더를 `--ao-root`로 받아 AO CLI를 부릅니다.

## 4. 프로젝트 준비 (한 번)

아래에서 `<프로젝트>`는 작업할 프로젝트 폴더(git 저장소)이고, `<AO 작업 공간>`은 AO가 쓸 별도 폴더입니다.
AO 작업 공간은 프로젝트 폴더 **밖에** 둡니다.

### 4.1 Goal Loop 기록 만들기

```powershell
cd <프로젝트>
goal-loop project-init .
goal-loop where          # source: project 이면 이 프로젝트의 기록을 씁니다
```

`.goal-loop/` 폴더가 생기고, 그 폴더는 자체 `.gitignore`로 버전 관리에서 빠집니다.

### 4.2 Memory Tool 지식 폴더 만들기

```powershell
python -m memory_tool init
python -m memory_tool record "프로젝트 시작"
```

- `.memory/`(지식 폴더), `.memory-tool.yml`(기본 폴더 위치), `.claude/guidelines.md`, `.claude/skills/mt-*`가 생깁니다.
- 기본 폴더 이름은 `python -m memory_tool base`로 보고 바꿀 수 있습니다. Goal Loop는 같은 규칙(`MEMORY_TOOL_BASE`,
  `.memory-tool.yml`의 `base`, 기본 `.memory`)으로 그 폴더를 찾습니다.
- 지식 폴더를 저장소에 넣을지 정하십시오. 개인 기록이라면 `.gitignore`에 `.memory/`를 더합니다.

### 4.3 AO 작업 공간에 프로젝트 연결하기

AO는 프로젝트 저장소를 직접 고치지 않고, 작업 공간 안의 사본에서 작업합니다. 기존 저장소를 연결하려면
**bare clone과 main worktree를 먼저 만든 뒤** `project-create`를 부릅니다. bare 저장소가 이미 있으면 AO는 새 빈 저장소를 만들지 않고 그것을 씁니다.

```powershell
$WS = "<AO 작업 공간>"
$ID = "myapp"                                  # AO 프로젝트 ID
cd <AO 설치 폴더>
npm run orchestrate -- workspace-init -w $WS   # 예시 설정(adapters/, agents/, system.json)과 projects/·repos/·worktrees/
git clone --bare <프로젝트> $WS\repos\$ID.git
git --git-dir=$WS\repos\$ID.git worktree add $WS\worktrees\$ID main

npm run orchestrate -- project-create --name "My App" --id $ID -w $WS
npm run orchestrate -- milestone-create $ID --id lane-1 --name "첫 레인" -w $WS
```

- **milestone(레인)** 은 Goal들이 실행되는 git worktree 단위입니다. 레인 이름은 Goal Loop의 `--milestone-id`로 넘깁니다.
- **에이전트 설정:** `workspace-init`은 AO 저장소의 `examples/workspace`를 작업 공간에 복사합니다. 이미 있는 파일은 덮어쓰지 않습니다.
  예시는 Claude Code CLI(`claude-cli`, 기본)와 Codex CLI(`codex-cli`, 예비) 어댑터, 그리고 에이전트 넷(pm, architect, critic, implementer)입니다.
  두 CLI에 미리 로그인해 두십시오. 모델이나 에이전트를 바꾸려면 작업 공간의 `adapters/`, `agents/` 파일을 고칩니다.
  API 키가 필요한 어댑터를 쓸 때는 설정 파일에 `apiKey`를 적지 말고 환경 변수로 주십시오.

### 4.4 에이전트 지침 두기

에이전트(Claude Code, Codex)가 세션마다 같은 순서로 일하도록 프로젝트 루트에 지침을 둡니다.

- Goal Loop 스킬: Goal Loop 저장소의 `.claude/skills/goal-loop/SKILL.md`(Claude Code)와
  `.agents/skills/goal-loop/SKILL.md`(Codex)를 프로젝트의 같은 위치에 복사합니다.
- Memory Tool 스킬과 지침은 `python -m memory_tool init`이 만듭니다. `.memory/templates/CLAUDE.md.template`의 절을 참고하십시오.
- 아래는 `CLAUDE.md`(Codex는 `AGENTS.md`)에 넣을 최소 예시입니다.

```markdown
## 세션 시작
1. `python -m memory_tool context`를 실행하고 `.claude/memory-context.md`를 읽는다.
2. `goal-loop where`(source: project 확인), `goal-loop next`, `goal-loop status`로 열린 Aim, 실행 중인 Attempt,
   검증 대기를 요약한다.

## 기록 규칙
- 사람은 `user`, 에이전트는 자기 이름(`claude`, `codex`)으로 기록한다. 에이전트는 `user` 이름으로 아무것도 기록하지 않는다.
- 판정(Verdict)과 목표 달성 평가는 실행하지 않은 다른 에이전트에게 맡긴다:
  `verification-begin` 뒤 `goal-loop-judge --judge codex goal <goal-id>`, Aim은 `goal-loop-judge aim <aim-id>`.
- 근거(Evidence)는 실행한 뒤에 관찰한 것만 적고, 의존한 출력은 `--attach`로 붙인다.
- 판정이 기록되면 `goal-loop memory-draft`의 초안으로 `python -m memory_tool record "…"`를 남긴다.
- 각 Aim의 첫 접근안에 "무엇을 이루면 달성인가"를 쓰고 `proposal-close --kind criteria`로 닫는다.
  각 Goal의 완료 기준에는 멈춤 조건을 쓴다.
```

## 5. 작업 한 바퀴

### 5.1 목표와 과제 정하기

```powershell
goal-loop aim-create "이루고 싶은 변화"
goal-loop proposal-create aim-1 "무엇을 이루면 달성인가: (1) … (2) …" --actor claude
goal-loop proposal-close proposal-1 --kind criteria --actor claude --reason "목표의 달성 기준"
goal-loop proposal-create aim-1 "접근 방법" --actor claude
goal-loop goal-accept proposal-2 --target "할 일" --responsibility produce `
  --scope "바꿀 파일과 범위" `
  --criterion "(1) … (2) … 멈춤 조건: …" `
  --verification "확인할 명령" --key first-goal
```

### 5.2 AO에서 실행하기

```powershell
goal-loop ao-attempt-start goal-1 1 claude "AO 레인 lane-1에서 실행" `
  --ao-root <AO 설치 폴더> --ao-workspace $WS --ao-project-id $ID `
  --ao-goal-id first-goal --milestone-id lane-1 --activity-kind change `
  --approval wait --allowed-write-path "src/**" --allowed-write-path "tests/**"
goal-loop runtime-worker claude --once
```

- `ao-attempt-start`는 Goal의 현재 완료 기준을 AO Goal로 넘기는 실행 요청(`ao_cli.v1`)을 쌓습니다. 실제 실행은
  `runtime-worker`(또는 `runtime-dispatch`)가 합니다. 실행이 끝나면 결과가 Attempt의 결과와 AO 근거(Evidence)로 기록됩니다.
- `--allowed-write-path`는 AO가 쓸 수 있는 경로입니다. Goal의 범위와 맞추십시오.
- `--objective`를 주면 AO에는 완료 기준 대신 그 문장을 넘깁니다(Goal Loop 쪽에서만 확인할 항목을 뺄 때).

### 5.3 AO가 사람을 기다릴 때

AO는 승인, 범위 확장, 검토가 필요하면 멈추고 사람의 답을 기다립니다. `--approval wait`이면 worker가 기다리는 동안 그대로 대기합니다.
**worker를 돌리는 동안 `ao-status`를 주기적으로 확인하십시오.** 기다리는 질문을 놓치면 실행이 오래 멈춰 있게 됩니다.

```powershell
goal-loop ao-status                                       # waiting_for_human과 다음에 쓸 명령
goal-loop ao-respond <request-id> --approve --feedback "…"
goal-loop ao-respond <request-id> --reject  --feedback "…"
goal-loop ao-respond <request-id> --action expand_scope --write-path "docs/**"
goal-loop ao-resume <request-id> claude                    # 세션이 멈춰 있을 때(approval stop) 이어서 실행
goal-loop ao-review <request-id> claude --verdict reject --reason "…"   # AO Goal이 검토 대기(awaiting_review)일 때(accept·reject·concern·fail)
```

### 5.4 판정 받기

```powershell
goal-loop evidence-record attempt-1 claude "무엇을 실행했고 무엇을 보았는지" --attach .\test.log
goal-loop verification-begin goal-1 attempt-1
goal-loop-judge --judge codex --tree app=$WS\worktrees\<레인 worktree> goal goal-1
```

- 판정자는 프로젝트 사본에서 시험을 다시 돌리고, 답을 그대로 Verdict로 기록합니다.
- AO 레인에서 바뀐 코드는 프로젝트 폴더가 아니라 AO 작업 공간의 레인 worktree에 있습니다. `--tree 이름=경로`로 그 worktree를 넘기면
  판정자가 그 사본에서 확인합니다.
- `not_satisfied`이면 같은 완료 기준으로 수정 실행(새 `ao-attempt-start`)을 하고 다시 판정합니다.
- Codex 샌드박스는 다른 Windows 프로세스를 나열하거나 끝낼 수 없습니다. 프로세스를 다루는 시험이 있으면 `--judge claude`나 다른 판정자를 쓰십시오.

### 5.5 메모리에 남기기

```powershell
goal-loop memory-draft                               # 판정마다 타임라인 한 줄 초안과 명령
python -m memory_tool record "goal-1 satisfied(verdict-1, codex): …"
```

### 5.6 AO 결과를 프로젝트로 가져오기

레인의 Goal들이 끝나면 레인을 닫아 AO 작업 공간의 main에 병합하고, 그 main을 원래 프로젝트로 가져옵니다.

```powershell
cd <AO 설치 폴더>
npm run orchestrate -- milestone-close $ID lane-1 -w $WS

cd <프로젝트>
git fetch $WS\repos\$ID.git main
git merge FETCH_HEAD
```

반대로 프로젝트에서 직접 바꾼 내용이 있으면, 다음 레인을 만들기 전에 AO 작업 공간의 main에 반영합니다.

```powershell
git -C $WS\worktrees\$ID pull <프로젝트> main
```

## 6. 자주 막히는 점

- **AO가 오래 멈춰 있다:** 대부분 사람의 답을 기다리는 상태입니다. `goal-loop ao-status`를 보십시오(5.3).
- **`milestoneId … is not found`:** 레인을 먼저 `milestone-create`로 만드십시오. AO가 아무것도 바꾸지 않은 거부라면,
  `goal-loop runtime-requeue <request-id> claude`로 요청을 되돌린 뒤 worker를 다시 돌릴 수 있습니다.
- **판정이 시험 환경 때문에 실패했다:** 판정을 바꾸거나 다시 돌려 결과를 바꾸지 마십시오. 실행자의 견해를 근거로 남기고,
  무효로 할지는 사람이 정합니다(근거 정정 → 재검토 → 무효 → 새 실행에서 다른 판정자로 재판정).
- **위임 판정 명령을 찾지 못한다:** `goal-loop-judge`가 PATH에 없으면 Goal Loop를 설치한 환경의 Python으로
  `python -m goal_loop_tool.delegated_judging …`을 부르십시오.
- **AO가 범위 밖 파일을 바꾸려 한다:** AO가 범위 확장을 묻습니다. 필요한 경로만 `--action expand_scope --write-path`로 허락하십시오.

## 7. 확인한 범위

| 단계 | 확인한 곳 |
|---|---|
| Goal Loop 설치, `project-init`, Aim → Goal → Attempt → Evidence → 위임 판정 → 달성 평가 | 저장소 밖 연습 프로젝트(Goal Loop goal-108) |
| Memory Tool 초기화, 기록, `memory-draft` 연결 | 이 문서를 쓰며 만든 임시 프로젝트(goal-119)와 Goal Loop 저장소 자체 |
| AO 작업 공간에 기존 저장소 연결(bare clone, worktree, `project-create`, `milestone-create`), `ao-attempt-start`로 실행 요청 생성 | 임시 프로젝트와 임시 AO 작업 공간(goal-119) |
| AO Goal 실행, 사람 입력 대기 처리, 레인 병합, `--tree` 위임 판정 | AO 저장소 자체의 개발에서 반복 사용(Goal Loop aim-13, aim-14) |

아직 확인하지 않은 것은 다음과 같습니다.

- 저장소 밖 새 프로젝트에서, `workspace-init`의 예시 설정으로 AO Goal을 처음부터 끝까지 실행하는 일.
- Windows 밖의 운영체제.
