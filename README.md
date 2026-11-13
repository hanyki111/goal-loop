# Goal Loop

해야 할 일과 한 일을 다음 순서로 기록하고, 실행한 에이전트와 다른 에이전트가 결과를 판정하게 하는 도구입니다.

목표(Aim) → 접근안(Proposal) → 과제(Goal)와 완료 기준(Contract) → 실행(Attempt) → 관찰 근거(Evidence) → 검증 판단(Verdict) → 목표 달성 평가(Aim Evaluation)

명령줄(CLI)과 데스크톱 앱(한국어 화면)으로 씁니다. Memory Tool(지식 기록)과 AI_ORCHESTRATION(AO, 실행 계층)에 선택적으로 연결할 수 있고,
연결하지 않아도 혼자 동작합니다.

## 설치

확인한 환경은 Windows 11입니다. 다른 운영체제는 아직 확인하지 않았습니다.

**요구 사항**

- CLI: Python 3.11 이상
- 데스크톱 앱을 소스에서 빌드할 때: Node.js(npm)와 Rust(rustup)
- 위임 판정을 쓸 때(선택): 판정자로 쓸 에이전트 CLI. `codex`(OpenAI Codex CLI) 또는 `claude`(Claude Code CLI)가 필요합니다.

**CLI 설치**

```powershell
pipx install git+https://github.com/hanyki111/goal-loop     # pipx가 없으면: python -m pip install --user pipx
# 또는 프로젝트의 가상환경 안에서
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install git+https://github.com/hanyki111/goal-loop
goal-loop --help
```

가상환경에 설치했다면 그 가상환경을 켜거나(`.\.venv\Scripts\Activate.ps1`) `.\.venv\Scripts\goal-loop.exe`로 부릅니다.
아래 예시의 `goal-loop`와 `python`은 설치한 환경의 것을 뜻합니다.

**업데이트**

버전 번호는 커밋마다 올리지 않습니다(지금은 `0.1.0`). 그래서 `pipx upgrade goal-loop-tool`은 버전이 같다고 보고 새 커밋을
받지 않을 수 있습니다. 새 커밋을 받으려면 처음 설치한 출처에서 다시 설치합니다.

```powershell
pipx reinstall goal-loop-tool
# 가상환경에 설치했다면
.\.venv\Scripts\python.exe -m pip install --force-reinstall git+https://github.com/hanyki111/goal-loop
```

다시 설치해도 각 프로젝트에 놓인 스킬과 지침 절은 바뀌지 않습니다. 프로젝트마다 `project-init . --agents …`를 다시 실행해야
지침 파일의 Goal Loop 절이 새 내용으로 바뀌고, 없던 스킬이 생깁니다. 이미 있는 스킬 파일은 덮어쓰지 않으므로(`kept`),
새 스킬로 바꾸려면 그 스킬 파일을 지우고 다시 실행합니다.

## 프로젝트에서 시작하기

기록(journal)은 프로젝트 폴더 안 `.goal-loop/`에 쌓입니다. 이 폴더는 자체 `.gitignore`로 버전 관리에서 빠집니다.
에이전트(Claude Code, Codex)와 대화하며 쓰는 경우에는 `--agents`와 `--judge`를 함께 줍니다.

```powershell
cd <프로젝트 폴더>
goal-loop project-init . --agents claude,codex --judge codex
goal-loop where                      # source: project 이면 이 프로젝트의 기록을 씁니다
```

- `--agents claude,codex`: `.claude/skills/`와 `.agents/skills/` 아래에 두 스킬(아래 "에이전트와 함께 쓰기")을 둡니다.
  그리고 `CLAUDE.md`와 `AGENTS.md`에 표지(`<!-- goal-loop:begin … -->`)로 둘러싼 Goal Loop 절을 더합니다.
  절에는 항상 적용되는 규칙만 들어갑니다(세션 시작 명령, 에이전트의 기록 이름, `user` 이름으로 기록하지 않기, 판정자에게 맡기는 판정 명령).
  Memory Tool 지식 폴더가 있으면 세션 시작에 Memory Tool 단계도 들어갑니다. 다시 실행하면 표지 안만 바뀝니다.
- `--skills-only`(`--agents`와 함께): 두 스킬만 두고 `CLAUDE.md`·`AGENTS.md`는 읽지도 쓰지도 않습니다. 지침 파일에 Goal Loop 절을
  직접 쓴 곳(여러 프로젝트를 계획하는 거시 저널 폴더 등)에서 같은 지시가 두 번 들어가지 않게 합니다.
  결과의 `judges_by_agent`에 에이전트별 판정자가 나옵니다.
- `--judge codex|claude|glm`: `goal-loop-judge`가 `--judge` 없이 쓸 판정자를 `.goal-loop/judging.json`의 `judge`에 적습니다(없으면 `codex`).
  판정자가 실행한 에이전트와 같은 이름으로 기록되는 경우(Codex가 실행하고 Codex가 판정하는 경우)에는 `AGENTS.md`의 판정 명령이 `--judge claude`로 바뀝니다.
- `--glm-env-ps1 <파일>`: GLM 연결 파일을 `.goal-loop/judges.json`의 `glm.env_from_ps1`에 적습니다.
- 결과의 `warnings`는 판정자 CLI가 PATH에 없거나 GLM 연결이 없을 때 나옵니다.

## 기본 흐름

```powershell
goal-loop aim-create "이루고 싶은 변화"
goal-loop proposal-create aim-1 "무엇을 이루면 달성인가: (1) … (2) …" --actor claude   # 목표의 달성 기준
goal-loop proposal-close proposal-1 --kind criteria --actor claude --reason "목표의 달성 기준"
goal-loop proposal-create aim-1 "접근 방법" --actor claude
goal-loop goal-accept proposal-2 --target "할 일" --responsibility produce --scope "범위" --criterion "완료 기준" --verification "확인 방법" --key first-goal
goal-loop attempt-start goal-1 1 claude "실행"
goal-loop attempt-complete attempt-1 succeeded
goal-loop evidence-record attempt-1 claude "실행하고 관찰한 것" --attach .\test.log
goal-loop verification-begin goal-1 attempt-1
goal-loop-judge goal goal-1          # 다른 에이전트가 판정하고 Verdict를 기록합니다
goal-loop next                       # 다음에 할 일을 읽기 전용으로 보여 줍니다
```

- 성공한 실행은 검증된 과제가 아니고, 검증된 과제도 달성된 목표가 아닙니다. 목표는 `aim-evaluate`로 따로 평가해야 닫힙니다.
- 실패한 실행은 `retry-decide`로 다시 실행할 이유를 남긴 뒤 다시 시작합니다. 판정이 `not_satisfied`이면 같은 완료 기준으로 보완 실행을,
  `inconclusive`이면 같은 실행에 근거를 더해 다시 판정합니다. 완료 기준을 바꿔야 하면 `contract-revise`로 새 판을 만듭니다.
- 기록은 지우거나 고치지 않고 덧붙이기만 합니다. 잘못된 근거는 `evidence-correct`로 정정하고, 그 근거를 인용한 판정을 다시 검토합니다.
- 처음이라면 `goal-loop guide`(전체 순서와 복구 경로), `goal-loop guided`(질문을 따라 기록), `goal-loop help --all`(모든 명령)을 보십시오.
  세 명령은 기록을 만들지 않습니다.

명령과 복구 경로의 세부 설명은 [docs/CLI-REFERENCE.md](docs/CLI-REFERENCE.md)에 있습니다.

## 에이전트와 함께 쓰기

**스킬.** `project-init --agents`가 두 스킬을 둡니다. Claude Code에서는 `/goal-loop`, Codex에서는 `$goal-loop`처럼 부르거나,
요청 내용에 맞으면 에이전트가 알아서 불러 씁니다.

- `goal-loop`: CLI로 기록하는 방법입니다. `goal-loop next`를 먼저 읽고, 사용자의 승인 없이 기록을 만들거나 검증하지 않습니다.
- `goal-loop-planning`: 무엇을 기록할지 정하는 방법입니다. 일의 시점마다 두 질문(왜 이것인가, 이것이 끝나면 모든 것이 끝나는가)을 던지고,
  그 답으로 지금 아는 정도(구축형, 통합형, 원인 규명형, 탐색형)를 읽어 Aim과 Goal을 세우고 나눕니다.
  불합격이나 정체 뒤에 원인을 좁히는 방법도 다룹니다.

**위임 판정(선택).** 실행한 에이전트가 아닌 다른 에이전트가 프로젝트 사본에서 판정하고, 그 결과를 그대로 기록합니다.

```powershell
goal-loop-judge --judge codex goal goal-1   # 또는 --judge claude, --judge glm
goal-loop-judge aim aim-1
```

- `goal-loop-judge`는 `goal-loop`와 함께 설치됩니다(pipx 포함). 설치한 환경의 Python으로 `python -m goal_loop_tool.delegated_judging …`처럼 불러도 같습니다.
- `--judge codex`는 `codex exec`, `--judge claude`는 `claude -p`를 씁니다. 해당 CLI가 PATH에 있고 로그인되어 있어야 합니다.
- 다른 모델을 Claude CLI로 연결하려면 `.goal-loop/judges.json`에 연결 정보를 둡니다(예: `{"glm": {"env_from_ps1": "<path>/set-glm.ps1"}}`).
  이 정보는 그 판정 프로세스에만 전달되고 기록에서는 지워집니다.
- 사본을 만드는 방법, 프로젝트별 설정(`.goal-loop/judging.json`), 다른 저장소를 함께 넘기는 `--tree`는 [docs/DELEGATED-JUDGING.md](docs/DELEGATED-JUDGING.md)에 있습니다.

## 진행 상태 보기

### 데스크톱 앱

앱은 같은 기록을 화면으로 보여 주고, 판정·종료·보류 같은 사람의 결정을 받습니다. 앱은 `goal-loop` 명령을 불러 동작하므로
CLI를 먼저 설치합니다. `GOAL_LOOP_EXECUTABLE` 환경 변수가 있으면 그 실행 파일을, 없으면 PATH의 `goal-loop`를 씁니다.

```powershell
cd ui
npm install
npm run tauri build    # ui/src-tauri/target/release/bundle/ 아래에 설치 파일(.msi, -setup.exe)이 생깁니다
```

- 아직 서명된 설치 파일과 공개 릴리스는 없습니다. 서명되지 않은 설치 파일은 Windows SmartScreen 경고가 뜰 수 있습니다.
- 앱에서 "프로젝트 폴더 열기"로 `project-init`한 폴더를 고릅니다.
- 개발용 앱(`npm run tauri dev`)과 설치한 앱은 같은 WebView2 데이터 폴더를 써서, 실행 옵션이 다르면 두 번째 앱이 뜨지 않을 수 있습니다.
  그럴 때는 한쪽을 끄거나 `WEBVIEW2_USER_DATA_FOLDER`로 데이터 폴더를 나눕니다.
- 화면 구성과 동작은 [docs/DESKTOP-APP.md](docs/DESKTOP-APP.md)에 있습니다.

### 앱을 설치할 수 없을 때

- `goal-loop overview [--aim aim-1] [--all] [--watch 10]`: 터미널에 목표 트리, 과제 상태, 지금 할 일을 출력합니다.
- `goal-loop ui [--port 8765] [--no-open]`: 앱과 같은 화면을 `127.0.0.1`에서 브라우저로 엽니다. 화면 파일이 패키지에 들어 있어 Node가 필요하지 않습니다.
  **읽기 전용**이어서 기록을 바꾸는 요청은 모두 거부하고, 결정은 데스크톱 앱이나 CLI에서 합니다. 다른 프로세스가 기록하면 2초 안에 화면이 다시 읽습니다.
  원격 접속(VS Code Remote SSH 등)에서는 `--no-open --port 8765`로 띄우고 그 포트를 포워딩해 클라이언트 브라우저나 Simple Browser로 엽니다.
- 두 명령 모두 projection checkpoint를 갱신하지 않습니다.
- VS Code 안에서 보려면(Remote SSH 포함) [`vscode/`](vscode/README.md)의 확장을 빌드해 설치합니다.
  `.goal-loop/journal.sqlite3`를 누르면 데스크톱 앱과 같은 화면이 편집기 탭에 열립니다.

## 선택 연결

- **Memory Tool**([github.com/hanyki111/memory_tool](https://github.com/hanyki111/memory_tool)): 프로젝트에 Memory Tool 지식 폴더가 있으면
  앱의 상세 화면이 그 기록을 인용한 타임라인 항목과 연결한 문서를 읽기 전용으로 보여 주고, 판정마다 타임라인 줄 초안을 제안합니다
  (`goal-loop memory-draft`). 지식 폴더는 Memory Tool과 같은 규칙으로 찾습니다: 기본 `.memory`, 프로젝트 루트의 `.memory-tool.yml`
  (`base: "<이름>"`, `mbase set`이 씀)이나 `MEMORY_TOOL_BASE` 환경 변수로 바뀌고, `base: "."`이면 프로젝트 루트 자체입니다.
  Goal Loop는 그 폴더에 쓰지 않습니다.
- **AI_ORCHESTRATION(AO)**: 과제를 AO에 맡겨 실행하고(`goal-loop ao-attempt-start … --ao-workspace <AO 작업 공간>`, `goal-loop runtime-worker`),
  결과를 근거로 받습니다. 연결된 실행에서는 AO 내부 판정 일부가 완료를 막지 않고 참고 판정으로만 남고, 완료 판단은 Goal Loop 판정이 맡습니다.
  AO의 공개 설치 문서는 준비 중입니다.

세 도구를 함께 쓰는 순서는 [docs/GUIDE-THREE-TOOLS.ko.md](docs/GUIDE-THREE-TOOLS.ko.md)에 있습니다.

## 문서

| 문서 | 내용 |
|:---|:---|
| [docs/CLI-REFERENCE.md](docs/CLI-REFERENCE.md) | 기록 위치를 찾는 순서, 참고 문서와 첨부, 하위 목표, 복구 경로, 종료, Goal 관계, Runtime |
| [docs/DESKTOP-APP.md](docs/DESKTOP-APP.md) | 데스크톱 앱의 화면 구성과 작업 공간 |
| [docs/DELEGATED-JUDGING.md](docs/DELEGATED-JUDGING.md) | 위임 판정의 동작과 설정 |
| [docs/GUIDE-THREE-TOOLS.ko.md](docs/GUIDE-THREE-TOOLS.ko.md) | Goal Loop, Memory Tool, AO를 함께 쓰는 순서 |
| [docs/MVP-SPEC.md](docs/MVP-SPEC.md), [docs/PERSISTENCE-CONTRACT.md](docs/PERSISTENCE-CONTRACT.md) | 핵심 규칙과 저장 형식 |
| [docs/CORE-AIM-TREE.ko.md](docs/CORE-AIM-TREE.ko.md), [docs/CORE-LIFECYCLE-CLOSURE.ko.md](docs/CORE-LIFECYCLE-CLOSURE.ko.md) | 하위 목표와 종료 규칙의 설계 |
| [docs/PROJECT-FOLDER-REFERENCES.ko.md](docs/PROJECT-FOLDER-REFERENCES.ko.md) | 프로젝트 폴더와 참고 문서 |
| [docs/PLANNING-SKILL-WALKTHROUGH.ko.md](docs/PLANNING-SKILL-WALKTHROUGH.ko.md) | goal-loop-planning 스킬을 만든 과정 |

## 기여와 라이선스

기여 방법과 개발 환경은 [CONTRIBUTING.md](CONTRIBUTING.md), 취약점 신고는 [SECURITY.md](SECURITY.md), 버전별 변경은 [CHANGELOG.md](CHANGELOG.md)에 있습니다.

[Apache License 2.0](LICENSE)으로 배포합니다. 저작권 표기는 [NOTICE](NOTICE)에 있습니다.
