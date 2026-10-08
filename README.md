# Goal Loop

해야 할 일과 한 일을 목표(Aim) → 접근안(Proposal) → 과제(Goal) → 실행(Attempt) → 관찰 근거(Evidence) → 검증 판단(Verdict)으로
기록하고, 실행한 에이전트와 다른 에이전트가 판정하게 하는 도구입니다. 명령줄(CLI)과 데스크톱 앱(한국어 화면)으로 씁니다.
Memory Tool(지식 기록)과 AI_ORCHESTRATION(AO, 실행 계층)에 선택적으로 연결할 수 있고, 연결하지 않아도 혼자 동작합니다.

## 설치와 시작

확인한 환경은 Windows 11입니다. 다른 운영체제는 아직 확인하지 않았습니다.

### 요구 사항

- CLI: Python 3.11 이상
- 데스크톱 앱을 소스에서 빌드할 때: Node.js(npm)와 Rust(rustup)
- 위임 판정을 쓸 때(선택): 판정자로 쓸 에이전트 CLI — `codex`(OpenAI Codex CLI) 또는 `claude`(Claude Code CLI)

### CLI 설치

```powershell
pipx install git+https://github.com/hanyki111/goal-loop
# 또는 가상환경 안에서
python -m pip install git+https://github.com/hanyki111/goal-loop
goal-loop --help
```

### 프로젝트에서 시작하기

기록(journal)은 프로젝트 폴더 안 `.goal-loop/`에 쌓입니다. 이 폴더는 자체 `.gitignore`로 버전 관리에서 빠집니다.

```powershell
cd <프로젝트 폴더>
goal-loop project-init .
goal-loop where                      # source: project 이면 이 프로젝트의 기록을 씁니다
goal-loop aim-create "이루고 싶은 변화"
goal-loop proposal-create aim-1 "접근 방법" --actor claude
goal-loop goal-accept proposal-1 --target "할 일" --responsibility produce --scope "범위" --criterion "완료 기준" --verification "확인 방법" --key first-goal
goal-loop attempt-start goal-1 1 claude "실행"
goal-loop attempt-complete attempt-1 succeeded
goal-loop evidence-record attempt-1 claude "실행하고 관찰한 것" --attach .\test.log
goal-loop verification-begin goal-1 attempt-1
goal-loop next                       # 다음에 할 일을 읽기 전용으로 보여 줍니다
goal-loop guide                      # 명령 안내
```

### 위임 판정(선택)

실행한 에이전트가 아닌 다른 에이전트가 프로젝트 사본에서 판정하고, 그 결과를 그대로 기록합니다.

```powershell
python -m goal_loop_tool.delegated_judging --judge codex goal goal-1   # 또는 --judge claude
python -m goal_loop_tool.delegated_judging aim aim-1
```

- `--judge codex`는 `codex exec`, `--judge claude`는 `claude -p`를 씁니다. 해당 CLI가 PATH에 있고 로그인되어 있어야 합니다.
- 다른 모델을 Claude CLI로 연결하려면 `.goal-loop/judges.json`에 연결 정보를 둡니다(예: `{"glm": {"env_from_ps1": "<path>/set-glm.ps1"}}`).
  이 정보는 그 판정 프로세스에만 전달되고 기록에서는 지워집니다.

### 데스크톱 앱

앱은 같은 기록을 화면으로 보여 주고, 판정·종료·보류 같은 사람의 결정을 받습니다. 앱은 `goal-loop` 명령을 불러 동작하므로
CLI를 먼저 설치합니다. 앱은 `GOAL_LOOP_EXECUTABLE` 환경 변수가 있으면 그 실행 파일을, 없으면 PATH의 `goal-loop`를 씁니다.

```powershell
cd ui
npm install
npm run tauri build    # ui/src-tauri/target/release/bundle/ 아래에 설치 파일(.msi, -setup.exe)이 생깁니다
```

아직 서명된 설치 파일과 공개 릴리스는 없습니다. 서명되지 않은 설치 파일은 Windows SmartScreen 경고가 뜰 수 있습니다.
앱에서 "프로젝트 폴더 열기"로 `project-init`한 폴더를 고릅니다.
개발용 앱(`npm run tauri dev`)과 설치한 앱은 같은 WebView2 데이터 폴더를 써서, 실행 옵션이 다르면 두 번째 앱이 뜨지 않을 수 있습니다.
그럴 때는 한쪽을 끄거나 `WEBVIEW2_USER_DATA_FOLDER`로 데이터 폴더를 나눕니다.

### 선택 연결

- **Memory Tool**([github.com/hanyki111/memory_tool](https://github.com/hanyki111/memory_tool)): 프로젝트에 Memory Tool 지식 폴더가 있으면
  앱의 상세 화면이 그 기록을 인용한 타임라인 항목과 연결한 문서를 읽기 전용으로 보여 주고, 판정마다 타임라인 줄 초안을 제안합니다
  (`goal-loop memory-draft`). 지식 폴더는 Memory Tool과 같은 규칙으로 찾습니다: 기본 `.memory`, 프로젝트 루트의 `.memory-tool.yml`
  (`base: "<이름>"`, `mbase set`이 씀)이나 `MEMORY_TOOL_BASE` 환경 변수로 바뀌고, `base: "."`이면 프로젝트 루트 자체입니다.
  Goal Loop는 그 폴더에 쓰지 않습니다.
- **AI_ORCHESTRATION(AO)**: 과제를 AO에 맡겨 실행하고(`goal-loop ao-attempt-start … --ao-workspace <AO 작업 공간>`, `goal-loop runtime-worker`),
  결과를 근거로 받습니다. 연결된 실행에서는 AO 내부 판정 일부가 완료를 막지 않고 참고 판정으로만 남고, 완료 판단은 Goal Loop 판정이 맡습니다.
  AO의 공개 설치 문서는 준비 중입니다.

## 이 저장소 개발

아래는 이 저장소를 고치는 사람을 위한 내용입니다.

### Shared memory (optional)

Claude Code and Codex share the project-local `.memory/` knowledge base through the editable `memory_tool` installation in `.venv/`.

```powershell
# From this project root
.\.venv\Scripts\python.exe -m memory_tool context
.\.venv\Scripts\python.exe -m memory_tool status
.\.venv\Scripts\python.exe -m memory_tool search "goal decomposition"
```

Read [AGENTS.md](AGENTS.md) in Codex and [CLAUDE.md](CLAUDE.md) in Claude Code for the common workflow.

## Concept-validation mock MVP

The in-memory core is a pre-product semantic prototype. It validates selected scenarios and
counterexamples for immutable Goal Contracts, Attempt/Verdict separation, Contract-version
safety, Evidence correction and review, execution admission, and explicit Aim Evaluation.
The normative boundary is in [docs/MVP-SPEC.md](docs/MVP-SPEC.md). The persistent SQLite journal,
the AI_ORCHESTRATION adapter and the Goal Tree were built on top of it later (see the sections below).

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

한국어 단계 추적 데모입니다.

```powershell
.\.venv\Scripts\python.exe -m goal_loop_tool demo
.\.venv\Scripts\python.exe -m goal_loop_tool not-satisfied
.\.venv\Scripts\python.exe -m goal_loop_tool inconclusive
.\.venv\Scripts\python.exe -m goal_loop_tool contract-revision
.\.venv\Scripts\python.exe -m goal_loop_tool evidence-invalidation
.\.venv\Scripts\python.exe -m goal_loop_tool execution-admission
.\.venv\Scripts\python.exe -m goal_loop_tool aim-evaluation
```

`demo` optionally accepts `--output <path>` for its newly created file. The default writes a
new file under `artifacts/`.


# Persistent CLI MVP

A project keeps its journal inside the project folder, as memory_tool keeps `.memory/`:

```powershell
goal-loop project-init .      # creates .goal-loop/ (journal.sqlite3, artifacts/, a self-ignoring .gitignore)
goal-loop where               # which journal, its project root, and how it was found
```

The CLI picks the journal in this order: `--database <file>`, `--workspace "<name or id>"` (the desktop
app's registry; `goal-loop workspaces` lists it), `--project <folder>`, `GOAL_LOOP_DATABASE`, the nearest
`.goal-loop/journal.sqlite3` found by walking up from the current folder (at most 10 levels), and
finally the user-global `%LOCALAPPDATA%\GoalLoopTool\goal-loop.sqlite3`. A journal inside `.goal-loop/`
always knows its project root, however it was selected. Keep project folders out of cloud-sync folders
such as OneDrive: syncing a live SQLite file can corrupt it. The installable `goal-loop` command is
the preferred interface for Codex and Claude Code Skills.

Several sessions may write to one journal; a command overtaken by another writer is re-derived from
the newer journal before it is appended. The desktop app reloads when its window regains focus.

References and attachments (`docs/PROJECT-FOLDER-REFERENCES.ko.md`):

```powershell
goal-loop reference-add goal-1 docs/design.md --label "design" --actor owner   # living document; stored relative to the project
goal-loop reference-remove reference-1 --actor owner --reason "replaced"
goal-loop evidence-record attempt-1 claude "115 tests passed" --attach .\pytest.log   # copied with its SHA-256; only at record time
goal-loop ao-import attempt-1 claude <ao-workspace>\projects\<project-id> <ao-goal-id>   # AI_ORCHESTRATION Goal results as Evidence (read only)
goal-loop ao-attempt-start goal-1 1 claude "run in AO" --ao-project-id <id> --ao-goal-id <slug> --ao-workspace <ao-workspace>   # then runtime-dispatch runs it in AO once
goal-loop ao-links [--goal goal-1] [--ao-goal <project>/<ao-goal-id>] [--backfill --actor claude]   # Goal <-> AO Goal links
python -m goal_loop_tool.delegated_judging goal goal-1   # a separate agent (Codex) judges on a project copy; its answer is recorded unchanged
$env:GOAL_LOOP_CEILING_DIRECTORIES = "C:\sandbox"         # project discovery never climbs above these folders (os.pathsep-separated)
```

Sub-Aims (`docs/CORE-AIM-TREE.ko.md`) split a direction that cannot yet be written as a Contract; a
task that can be written as a Contract is split with the Goal tree instead:

```powershell
goal-loop aim-create "Combat feels good" --parent aim-1     # or later, once: goal-loop aim-parent-add aim-3 aim-1
goal-loop aim-evaluate aim-1 owner "The game is done" aim-evaluation-2 aim-evaluation-3
```

A parent Aim is never achieved implicitly: its own evaluation cites valid Verdicts and/or valid
evaluations of its direct sub-Aims. Pausing an ancestor blocks new Attempts below it, abandoning one
also blocks new Proposals and acceptance, and nothing below is closed automatically. A correction that
puts a sub-Aim evaluation under review also reviews the parent evaluations citing it.

A reference may change after it is linked, so it never replaces a Contract criterion. An attachment
is copied to `.goal-loop/artifacts/<sha256>/<name>` (10MB per file) and `backup` copies the artifacts
next to the backup file.

```powershell
goal-loop init
goal-loop aim-create "Ship a verified result"
goal-loop status
goal-loop activity
goal-loop doctor
```

For this repository, install the command into the project virtual environment once:

```powershell
.\.venv\Scripts\python.exe -m pip install -e .
```

In this project, use `& .\.venv\Scripts\goal-loop.exe ...` unless its `Scripts` directory is
already on your PATH. The shorter `goal-loop ...` form is for a shell where the command resolves.

### AI 없이 시작하는 CLI 안내

처음 사용하는 사람은 아래 세 명령만으로 현재 상태, 전체 단계별 예시, 개별 명령의
상세 예시를 확인할 수 있습니다. `guide`와 `help`는 DB에 기록을 만들지 않습니다.

```powershell
goal-loop guide
goal-loop help --all
goal-loop help verdict-submit
```

`guide`는 Aim → Proposal → Goal Contract → Attempt → Evidence → Verdict → Aim 평가의
순서와 실패·inconclusive·Evidence correction의 복구 경로를 설명합니다. 질문을 따라
기록하고 싶다면 `goal-loop guided`를 사용합니다. 기존 기록을 안전하게 이어가려면
항상 `goal-loop next`와 `goal-loop status`부터 확인하세요.

새 Attempt의 admission을 제어하거나 막힌 이유를 확인할 때는 다음 명령을 씁니다.

```powershell
goal-loop admission
goal-loop aim-pause aim-1 "owner" "waiting for review"
goal-loop aim-resume aim-1 "owner" "review complete"
goal-loop goal-hold-add goal-1 --actor owner --reason "waiting for design approval"
goal-loop goal-hold-clear goal-1
goal-loop verdict-dependency-add goal-2 verdict-1 --required-result satisfied
```

pause와 Hold는 이미 실행 중인 Attempt를 취소하지 않고 새 Attempt만 막습니다. `next`는
pause/Hold/dependency가 새 실행을 막으면 `attempt-start` 대신 재개·Hold 해제·dependency
점검 행동을 제안합니다. Authority grant를 실제 admission 조건으로 사용할 환경은 모든
관련 명령에 `--enforce-authority`를 붙이고 `execution-grant goal-1 "executor"`로 먼저
권한을 기록합니다.

### Move existing project-local history once

The new default does not silently copy an existing `./goal-loop.sqlite3`. If this project-local
file contains history you want to keep, first confirm the destination does not exist, then use
the SQLite backup command below. It creates the destination directory and makes a consistent
snapshot without deleting the original.

```powershell
$sharedDb = Join-Path $env:LOCALAPPDATA 'GoalLoopTool\goal-loop.sqlite3'
Test-Path -LiteralPath $sharedDb
.\.venv\Scripts\goal-loop.exe --database .\goal-loop.sqlite3 backup $sharedDb
```

### Use inside Codex and Claude Code

This repository contains the same scoped Goal Loop Skill for both agent CLIs. After installing
the console command, invoke it explicitly as `$goal-loop` in Codex or `/goal-loop` in Claude
Code, then describe the Aim or management action. The Skill reads `goal-loop next` first: this
is a read-only JSON projection of the smallest valid lifecycle actions, including relevant IDs
and command argument templates. It does not create or verify durable facts without the user's
authorization. Use `goal-loop status` for a compact inventory and `goal-loop activity` when
the human history matters.

- Codex: `.agents/skills/goal-loop/SKILL.md`
- Claude Code: `.claude/skills/goal-loop/SKILL.md`

For example, an empty database reports `create_aim`. A failed Attempt reports either
`decide_retry` or, after an unconsumed Decision exists, `start_retry_attempt` with that
`decision-*` ID. `next` only recommends actions; an agent must still explain the semantic
change and receive user authorization before issuing a mutating command.

Even a `verified` Goal does not close its Aim automatically. When `next` reports
`evaluate_aim`, review whether the cited, currently valid `satisfied` Verdicts actually cover
the Aim's intended result, then make that separate conclusion explicit:

```powershell
goal-loop aim-evaluate aim-1 "reviewer" "The verified result covers the Aim's intended outcome." verdict-1
```

An `inconclusive` Verdict is not a failure verdict and is not silently retried. `next` presents
two explicit recovery paths: record new Evidence on the same successful Attempt, then submit a
new Verdict with `--supersedes-verdict`; or decide that a new execution is necessary, create
`reverification-decide`, and consume its `decision-*` exactly once in `attempt-start`.

A Verdict has no "partially satisfied" result. Record the verifier's reason with
`verdict-submit ... --reason "(1) met: … (2) not met: …"`; the reason is shown under the
recorded Verdict. When only part of a Contract is met: (A) `not_satisfied`, then repair on the
same Contract; (B) narrow the criterion with `contract-revise` (the reason stays in history) and
move the rest to a new Proposal; (C) clarify ambiguous wording with `contract-revise` before
judging; (D) issues outside the criterion do not block `satisfied` — record a follow-up Proposal.

Evidence가 나중에 잘못되었거나 불완전한 것으로 확인되면 원 기록을 고치거나 지우지
않습니다. 정정 사실을 append하고, `reviews`로 under_review Verdict·Aim Evaluation과
Impact Review를 먼저 확인합니다. `next`는 먼저 Verdict review를, 그 판정이 끝난 뒤
영향받은 Aim Evaluation review를 구조화해 제안합니다. 각 resolution은 별도의 사용자
승인이 필요한 의미론적 판단입니다.

```powershell
goal-loop evidence-correct evidence-1 "observer" "The initial observation was incomplete."
goal-loop reviews
goal-loop verdict-review-resolve impact-review-1 reaffirm "reviewer" "Corrected evidence remains sufficient."
goal-loop aim-evaluation-review-resolve aim-evaluation-1 impact-review-1 reaffirm "reviewer" "The valid Verdict still covers the Aim."
```

`reaffirm`은 모든 해당 Impact Review가 재확인됐을 때만 Verdict를 `valid`로 되돌립니다.
`invalidate`는 과거 Verdict/Goal verified 이력을 지우지 않으며 현재 유효성만
`invalidated`로 만듭니다.

무효화된 Aim Evaluation 때문에 Aim의 종결 근거가 더 이상 유효하지 않아도 Aim은 자동으로
열리지 않습니다. 현재 valid Evaluation이 없을 때만 `next`가 `aim-closure-invalidate`를
제안하며, 그 때에도 어떤 무효화 record가 종결을 흔들었는지와 재개 사유를 명시합니다.

```powershell
goal-loop aim-closure-invalidate aim-1 aim-evaluation-1 review-resolution-1 "reviewer" "Resume pursuit after the unsupported closure."
```

If a Goal's scope, success criterion, or verification method changes materially, do not retry
the old version. Create a new immutable Contract version instead; existing Attempt and Evidence
records remain historical facts tied to their old version.

```powershell
goal-loop contract-revise goal-1 --scope "new scope" --criterion "new criterion" --verification "new verification method" --actor "owner" --reason "requirements changed"
```

처음 사용하는 사람은 대화형 시작점을 쓰는 편이 좋습니다. Aim(이루고 싶은 변화)을
적으면 다음 단계를 선택지와 함께 안내하고, 원하면 Proposal·Goal·첫 Attempt까지
질문으로 기록합니다. 기존 명령형 CLI는 자동화와 정확한 재실행을 위해 그대로 유지됩니다.

```powershell
goal-loop guided
goal-loop help goal-accept
```

`guided`를 열었을 때 처음이라면 메뉴 `7`(처음부터 배우기)을 선택하세요. Aim·Proposal·Goal
Contract·Attempt·Evidence·Verdict의 차이, 일반 진행 순서, 실패·inconclusive·Evidence 정정·pause/
Hold/dependency 차단의 대응을 terminal 안에서 설명합니다. 메뉴 `3`(명령 설명)에서 `all`을 입력하면
모든 명령의 목적과 실행 예시를 이어서 볼 수 있습니다.

대화형 메뉴 `6`은 정상 실행과 검증을 진행합니다. Verdict가 `inconclusive`이면 같은 Attempt에
새 Evidence를 더해 이전 Verdict를 supersede하는 재판정을 하거나, 새 실행이 필요하다는 Decision을
기록한 뒤 한 번의 새 Attempt를 시작하도록 안내합니다. 메뉴 `8`(고급 lifecycle 관리)은 Evidence
정정과 Impact Review 종결, Aim 평가와 invalidated closure 뒤 재개, pause/Hold, Contract revision을
질문으로 기록합니다. 이 판단들은 모두 사용자 입력이 있어야 기록되며 자동으로 실행되지 않습니다.

`guided`에서 빈 답은 안전하게 해당 단계를 건너뜁니다. 이미 저장한 Aim과 Proposal은
사라지지 않으며, 나중에 다시 `guided`, `status`, `activity`로 이어갈 수 있습니다.
Goal을 확정할 때는 결과 목표(성공 뒤 무엇이 가능해지는가), 작업 성격, 실행 범위,
성공 기준, 검증 방법을 각각 예시와 함께 묻습니다. 작업 성격은 `1`(만든다),
`2`(조사한다), `3`(결정한다) 중 하나로 고를 수 있습니다.
이미 만든 Goal은 `guided` 메뉴의 `6`(Goal·Attempt 관리)에서 선택해 첫 Attempt를
시작할 수 있습니다. 같은 메뉴는 Goal·최근 Attempt 상태에 맞춰 Attempt 완료,
Evidence 기록, 검증 시작, Verdict 제출만 선택지로 보여 주는 관리 화면입니다.

`guided`의 `5`는 Aim·Proposal 관리이며, Proposal을 건너뛴 기존 Aim에 Proposal을
추가하거나 열린 Proposal을 대화형으로 Goal로 확정할 수 있습니다. `6`은
Goal·Attempt 관리입니다.

새 Aim을 만드는 흐름에서는 첫 Proposal이 필수입니다. Proposal을 빈칸으로 두면
Aim은 저장되지 않고 방향을 다시 입력하게 됩니다. Aim만 남은 과거 데이터는 `5`에서
Proposal을 추가해 복구할 수 있습니다.

Use `proposal-create`, `goal-accept`, `attempt-start`, `attempt-complete`,
`evidence-record`, `verification-begin`, and `verdict-submit` for the core
lifecycle. `backup <destination>` creates a consistent SQLite backup.
`proposal-create <aim-id> <direction> --actor <who>` records who proposed an approach (agents should
always pass their own name).

Closure paths (2026-10-03, see [docs/CORE-LIFECYCLE-CLOSURE.ko.md](docs/CORE-LIFECYCLE-CLOSURE.ko.md)) record a
decision to stop without deleting anything; closed records are never reopened:

```powershell
goal-loop proposal-close proposal-2 --kind declined --actor owner --reason "proposal-1 was chosen"
goal-loop goal-stop goal-3 --actor owner --reason "requirement dropped"
goal-loop aim-abandon aim-2 --actor owner --reason "out of scope"
```

A stopped Goal blocks new Attempts and verification, but a running Attempt can still be completed.
An abandoned Aim blocks new Proposals, acceptance, and Attempts without closing its children.
`goal-hold-add` now requires `--actor` and `--reason`. A Goal cannot depend on its own ancestor (or take a
prerequisite as an ancestor), because the parent verifies only after its children; journals recorded before
this rule still replay.

`proposal-revise <proposal-id> <new-direction> --actor <actor> --reason <reason>`
creates a successor for an open Proposal and preserves the replaced direction in
the activity history. It does not overwrite Proposal text.

## Subprocess Runtime foundation

`SubprocessRuntime` executes an explicit argv list in a chosen working directory and returns
stdout, stderr, exit code, timeout state, and a runtime delivery ID. Process execution is
outside the SQLite transaction; `deliver_execution` then uses that delivery ID to durably
complete the matching Attempt. `CodexExecProfile` builds `codex exec` invocations with only
`read-only` or `workspace-write` sandbox modes; it never enables `danger-full-access`.
`ClaudePrintProfile` builds a bounded `claude -p --restricted` investigation: command/code
execution tools are absent, permission prompts are denied, output is JSON, and session files
are not persisted. The first implementation deliberately has no Runtime launch in `guided` yet.

An explicit, scriptable launch is available only for an already running Attempt:

```powershell
.\.venv\Scripts\python.exe -m goal_loop_tool persistent --database .\goal-loop.sqlite3 codex-run attempt-1 "Inspect this workspace and report the most important next step." --workdir . --sandbox read-only --delivery-id codex-attempt-1
```

Claude Code has the same explicit, scriptable boundary, but is currently restricted to
read-only investigation:

```powershell
.\.venv\Scripts\python.exe -m goal_loop_tool persistent --database .\goal-loop.sqlite3 claude-run attempt-1 "Inspect this workspace and summarize its structure." --workdir . --model sonnet --max-turns 8 --max-budget-usd 0.25 --delivery-id claude-attempt-1
```

Every Claude launch must explicitly select its model, positive turn cap, and positive USD cost
cap; there is no silent default model or unlimited run.

Runtime integration also has a durable, local outbox boundary for adapters. A caller can use
`PersistentGoalLoop.start_runtime_attempt(...)` to commit `AttemptStarted` and one typed Runtime
request in the same SQLite transaction. Workers explicitly list pending requests, claim one by
request ID, then either acknowledge it or return their own claim to pending after a recoverable
failure. The outbox is technical delivery state, not a new Goal Loop fact: it does not launch a
subprocess, complete an Attempt, retry automatically, or change the Core's admission and
verification semantics. Supply stable `command_id` and `request_id` when an adapter needs
replay-safe enqueueing.

The persistent CLI exposes the same boundary without a background dispatcher. First enqueue a
typed request; a worker then explicitly claims it. `runtime-ack` only marks delivery handling
complete—it never marks the Attempt successful. A worker that cannot continue must use
`runtime-requeue`; after actual execution, send the result through the existing Runtime delivery
path.

```powershell
.\.venv\Scripts\python.exe -m goal_loop_tool persistent --database .\goal-loop.sqlite3 runtime-attempt-start goal-1 1 runtime "Inspect structure" codex_exec.v1 '{"prompt":"Inspect and summarize","workdir":"."}' --request-id codex-request-1 --command-id start-codex-1
.\.venv\Scripts\python.exe -m goal_loop_tool persistent --database .\goal-loop.sqlite3 runtime-requests
.\.venv\Scripts\python.exe -m goal_loop_tool persistent --database .\goal-loop.sqlite3 runtime-claim codex-request-1 worker-a
.\.venv\Scripts\python.exe -m goal_loop_tool persistent --database .\goal-loop.sqlite3 runtime-request codex-request-1
.\.venv\Scripts\python.exe -m goal_loop_tool persistent --database .\goal-loop.sqlite3 runtime-dispatch codex-request-1 worker-a
.\.venv\Scripts\python.exe -m goal_loop_tool persistent --database .\goal-loop.sqlite3 runtime-requeue codex-request-1 worker-a
.\.venv\Scripts\python.exe -m goal_loop_tool persistent --database .\goal-loop.sqlite3 runtime-deliver codex-request-1 worker-a succeeded delivery-1
```

For an application adapter, `RuntimeOutboxWorker(loop, worker_id).dispatch(request_id, executor)`
is the conservative bridge. It claims exactly one pending request, calls the adapter-supplied
`executor` once, durably delivers the resulting Runtime outcome, then acknowledges the request.
It deliberately does not interpret a payload, select a command, or retry. If execution, delivery,
or acknowledgement fails, the request remains claimed for inspection and an explicit operator
decision; this avoids silently duplicating external work.

If a process already finished but the worker stopped before delivery, do not requeue just to
repeat it. Keep the claim and use `runtime-deliver <request-id> <worker> <succeeded|failed>
<delivery-id>` to durably record the observed outcome and acknowledge the request without
launching a process.

The built-in executor recognizes only these payload contracts. `codex_exec.v1` requires
`prompt` and an existing `workdir`; optional fields are positive `timeout_seconds`,
`sandbox` (`read-only` or `workspace-write`), `executable`, and safe `extra_args`.
`claude_print.v1` requires `prompt`, `workdir`, `model`, positive integer `max_turns`, and
positive `max_budget_usd`; it optionally accepts `timeout_seconds` and `executable`. Unknown
fields and unsupported kinds are rejected before a process starts.

## Goal Tree foundation

## React / Tauri desktop preview

`ui/` contains the local-first React workspace shared by the future desktop, mobile, and
Obsidian adapters. It reads the same default SQLite database as `goal-loop`; it does not create
a second application model or bypass Core validation.

The desktop UI uses Korean display terms decided on 2026-10-03: **목표** (Aim), **접근안** (Proposal),
**과제** (Goal), **완료 기준** (Contract), **실행** (Attempt), **관찰 근거** (Evidence), **검증 판단**
(Verdict), and **목표 달성 평가** (Aim Evaluation). These are display labels only; Core, CLI,
`ui-projection`, and code identifiers keep the English names.

The workspace has three resizable panes: the 목표 navigator on the left, a graph in the middle that
draws 목표 → 접근안 → 과제 (approaches can be toggled off), and a detail pane on the right. Every
detail screen follows the same rules: one **지금 할 일** box driven by read-only `goal-loop next`
(the same action never appears in two places), a one-line hint with concept explanations behind
`ⓘ`, stage tabs (목표: 접근안 · 과제 · 달성 평가; 과제: 실행 · 관찰 근거 · 검증), collapsible
sections for criteria, relations, execution hold, and activity, and internal IDs and CLI commands
only under **기술 정보**. Recording an action shows a short confirmation; when Core rejects a record
the detail pane shows the reason in Korean and keeps the raw CLI output folded.

Notable flows: confirming an approach prefills the task name; after a failed run the detail pane
offers retrying with the same criteria (one form, recorded as a retry Decision plus a new Attempt),
revising the criteria, or trying another approach; runs started under an older criteria version can
be tidied without counting toward the new version; evidence cited by a Verdict can be corrected,
which opens a review shown on the task, the 목표, and **검토함** (with a count in the header). The
**전체 작업함** lists every next action as links grouped by 목표, and **실행 대기함** shows only Runtime
delivery state; neither treats technical delivery as completion.

`ui-projection` is now schema version 11. Besides graph nodes and records it exposes per-Goal
`contract_history`, retry and re-verification `decisions`, `evidence_corrections`, the
`impact_reviews` they opened, closure and attribution facts (proposer, closure kind and reason,
stop reason, abandon reason, hold actor and reason), `project_root`, live `references` on Aims,
Proposals and Goals (with resolved path and existence), Evidence `attachments` (with copy path
and `preserved`/`changed`/`missing` status), each Aim's `parent_aim_id` with `aim-parent` graph edges,
and each Aim Evaluation's `child_evaluation_ids`. All additions are read-only.

The app opens a **project folder** (`프로젝트 관리 → 프로젝트 폴더 열기`) and keeps its journal in that
folder's `.goal-loop/`; removing such a project from the app only forgets the registration. Each
detail screen has a **참고 문서** section, and the Evidence form can attach files. Files open with their
default program; executables are revealed in Explorer instead of being run.

UI component checks are available with `cd ui; npm run test:ui`. Production builds remain available
with `npm run build` and `npm run tauri build -- --debug`. The [UI redesign specification](docs/UI-REDESIGN-SPEC.ko.md)
records the 2026-09-29 design; its vocabulary and several screens were superseded by the 2026-10 polish described above.

### Independent workspaces and hiding

The desktop workspace selector creates a separate SQLite journal for each project. Selecting a
workspace passes its database path to the existing read-only projections and allow-listed commands;
it does not add a `Project` entity to the Goal Loop Core. A fresh installation starts with the same
workspace-creation screen shown after the last workspace is deleted. Existing shared default data
is presented once as **기존 작업 공간** so it can be renamed, retained, or explicitly deleted.
The **숨기기** (hide) controls for 목표, 접근안, and 과제 are reversible local display
preferences: they hide records from the navigator, graph, next-action summary, and work inbox on
this PC, but never delete, pause, close, or alter the append-only journal. Restore them from
**숨긴 항목** at the bottom of the navigator or from the parent 목표 screen. Every workspace can be renamed or deleted from the selector after explicit confirmation.
Deletion removes only its verified SQLite file and SQLite sidecars; deleting the last workspace
returns to the common creation screen rather than silently creating another database.

From the project root:

```powershell
cd ui
npm install
npm run tauri dev
```

The wrapper supplies the project virtual-environment and Rust paths, uses port `5174`, and ignores
Rust build output in Vite's watcher. Select an Aim to record a Proposal, then select that Proposal
to make its execution goal or record a replacement version. Select a Goal node to add an immutable
parent link or a valid-satisfied-verdict dependency. The right Inspector covers
Aim → Proposal → Goal Contract → Attempt → Evidence → Verification → Verdict, Contract revision,
Evidence correction, retry/reverification decisions, Aim evaluation, and correction-driven review
resolutions. Buttons record facts only
after the person using the UI chooses the action; a successful Attempt remains distinct from a
verified Goal, and a verified Goal remains distinct from an achieved Aim.

For a distributable debug build:

```powershell
npm run tauri build -- --debug
```

Use the terminal `goal-loop guide`, `goal-loop guided`, and `goal-loop help --all` for the
complete self-service CLI guide. The graph shell is a local UI preview, not a replacement for the
CLI's Runtime outbox or an authorization boundary.

`goal-parent-add <child-goal> <parent-goal>` records one immutable parent-child link inside the
same Aim. `goal-dependency-add <target-goal> <source-goal>` blocks new target Attempts until the
source Goal has a valid `satisfied` Verdict for its current Contract. Both links are append-only,
reject cycles, and are visible through read-only `goal-graph`. `next` reports a blocked source
Goal instead of suggesting an invalid target Attempt.

Export the local read-only view with `graph-export .\goal-graph.html`. The generated HTML needs
no server or dependency and shows each Goal's Aim, phase, parent, and required predecessor Goals.
Parent links are blue; dependency links are orange dashed lines; a red node has an unmet Goal
dependency. Hover a node for its Aim and current dependency status.

A failed Attempt is not a failed Goal. Before starting the same Contract again, record and
consume one retry Decision; this preserves why the retry is valid:

```powershell
.\.venv\Scripts\python.exe -m goal_loop_tool persistent --database .\goal-loop.sqlite3 retry-decide goal-1 attempt-1 "reviewer" "The run reached its turn limit; repeat the same investigation with a justified higher limit."
.\.venv\Scripts\python.exe -m goal_loop_tool persistent --database .\goal-loop.sqlite3 attempt-start goal-1 1 claude "Repeat the inspection" --retry-decision decision-1
```

Use `--sandbox workspace-write` only when the user intends to allow edits inside that workdir.
