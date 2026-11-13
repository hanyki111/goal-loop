# Goal Loop CLI 참고

README에 있던 CLI 세부 설명을 2026-11-13에 그대로 옮긴 문서입니다(goal-161). 처음 쓰는 방법은 [README](../README.md)에,
명령 하나의 정확한 인자는 `goal-loop help <명령>`에 있습니다. 이 문서의
`.\.venv\Scripts\python.exe -m goal_loop_tool persistent --database …` 형식은 설치한 `goal-loop` 명령과 같습니다.

## Persistent CLI MVP

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
goal-loop-judge goal goal-1   # a separate agent (Codex) judges on a project copy; its answer is recorded unchanged
$env:GOAL_LOOP_CEILING_DIRECTORIES = "C:\sandbox"         # project discovery never climbs above these folders (os.pathsep-separated)
```

Sub-Aims (`docs/CORE-AIM-TREE.ko.md`) split a direction that cannot yet be written as a Contract; a
task that can be written as a Contract is split with the Goal tree instead. Proposals and Goals never
move to another Aim, so when a second Proposal pursues the same direction, create the sub-Aim first and
put that Proposal under it:

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

A companion Skill, `goal-loop-planning` (same folders), covers what to record rather than how: it
classifies the problem (build, integration, root-cause, exploration), writes Aim criteria with a
measurement and a stop condition, splits sub-Aims and Goals to fit that type, and after a
`not_satisfied` Verdict narrows the cause to one and tests it with a diagnostic Goal before a retry.

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

Closure paths (2026-10-03, see [docs/CORE-LIFECYCLE-CLOSURE.ko.md](CORE-LIFECYCLE-CLOSURE.ko.md)) record a
decision to stop without deleting anything; closed records are never reopened:

```powershell
goal-loop proposal-close proposal-2 --kind declined --actor owner --reason "proposal-1 was chosen"
goal-loop goal-stop goal-3 --actor owner --reason "requirement dropped"
goal-loop aim-abandon aim-2 --actor owner --reason "out of scope"
```

An Aim's achievement criteria are a Proposal closed with `--kind criteria`. To change them later, revise them
instead of adding a second set (goal-174); the earlier criteria stay readable, marked as replaced, and the app and
`goal-loop-judge aim` judge against the current criteria only while showing the history:

```powershell
goal-loop aim-criteria-revise aim-2 "criteria: ..." --supersedes proposal-5 [--supersedes proposal-7] --actor owner --reason "narrowed to commands"
```

Only an open Aim's criteria can be revised (reopen an achieved Aim with `aim-closure-invalidate` first), and each
`--supersedes` must be a current criteria Proposal of that Aim. Setting criteria is the user's decision.

A stopped Goal blocks new Attempts and verification, but a running Attempt can still be completed.
An abandoned Aim blocks new Proposals, acceptance, and Attempts without closing its children.
`goal-hold-add` now requires `--actor` and `--reason`. A Goal cannot depend on its own ancestor (or take a
prerequisite as an ancestor), because the parent verifies only after its children; journals recorded before
this rule still replay.

`proposal-revise <proposal-id> <new-direction> --actor <actor> --reason <reason>`
creates a successor for an open Proposal and preserves the replaced direction in
the activity history. It does not overwrite Proposal text.

## Goal links and graph

`goal-follow-add <goal> <previous-goal> --actor <name> --reason "…"` records that a Goal started from an
earlier Goal's result or continues a stopped Goal another way. It may cross Aims and link finished Goals,
rejects self-links, duplicates and cycles, never blocks Attempts, and appears in `goal-graph`
(`follows`), `graph-export` (green dotted), `ui-projection` (`follows` edges, schema 16), and the app
(이어받음 in the graph legend and the Goal's relations, where it can also be added).

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
