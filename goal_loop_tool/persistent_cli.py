"""Usable command-line surface for the persistent local Goal Loop MVP."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from pathlib import Path
import sys
import time
from typing import Callable

from .activity import activity_cards
from .ao_import import AoImportError, read_ao_goal, summarize, write_extracts, write_snapshot
from .ao_runtime import REQUEST_KIND as AO_SESSION_KIND, AoRuntimeError, contract_payload
from .moved_paths import relocated_payload
from .ao_cli_runtime import (
    REQUEST_KIND as AO_CLI_KIND, AoCli, AoCliPayload, cli_contract_payload, latest_session, resume_ao_cli,
)
from .ao_links import backfill as backfill_ao_links, link_goal, links as ao_goal_links
from .ao_sync import AoSyncError, sync as sync_ao_changes
from .claude_runtime import ClaudePrintProfile
from .codex_runtime import CodexExecProfile
from .model import AttemptOutcome, EvidenceAttachment, GoalIdentity, ReviewDisposition, VerdictResult, VerdictValidity
from .persistent import PersistentGoalLoop
from .project import (
    ProjectLocationError, artifacts_dir_for, discover_project_database, init_project, normalize_reference,
    project_database, project_root_for, store_attachment,
)
from .repository import SqliteEventRepository
from .outbox_runtime import RuntimeOutboxWorker
from .runtime_profiles import RuntimePayloadError, execute_profile_request
from .graph_view import export_goal_graph
from .ui_projection import goal_graph_projection
from . import memory_view
from .service import PROPOSAL_CLOSURE_KINDS, ContractDraft, GoalLoopError
from .subprocess_runtime import RuntimeExecution, SubprocessRuntime, deliver_execution


_COMMAND_HELP = {
    "aim-create": (
        "무엇을 이루고 싶은지(Aim)를 기록합니다. --parent로 큰 목표 아래의 하위 목표로 만들 수 있습니다. 완료 기준을 아직 쓸 수 없는 방향은 하위 목표로, 쓸 수 있는 작업은 하위 과제로 나눕니다.",
        'aim-create "전투가 재미있다" --parent aim-1',
    ),
    "aim-parent-add": (
        "이미 있는 목표를 다른 진행 중인 목표의 하위 목표로 한 번 연결합니다. 연결은 바꿀 수 없고, 순환은 거절됩니다.",
        "aim-parent-add aim-3 aim-1",
    ),
    "proposal-create": (
        "Aim을 향해 어떤 일을 할지, 아직 확정 전인 Goal Proposal을 만듭니다.",
        'proposal-create aim-1 "사용 흐름과 성공 기준을 초안으로 만든다" --actor owner',
    ),
    "proposal-close": (
        "검토 중인 Proposal을 Goal로 확정하지 않고 닫습니다. declined(채택 안 함), withdrawn(철회), criteria(Aim의 달성 기준으로 채택) 중 하나와 이유를 남기며, 다시 열지 않습니다. criteria로 닫은 Proposal은 그 Aim의 달성 기준으로 계속 보입니다.",
        'proposal-close proposal-2 --kind declined --actor owner --reason "proposal-1을 선택함"',
    ),
    "goal-stop": (
        "Goal을 더 이상 추진하지 않기로 하고 중단합니다. 새 Attempt와 검증이 막히며, 이미 실행 중인 Attempt의 결과는 계속 기록할 수 있습니다.",
        'goal-stop goal-3 --actor owner --reason "요구가 바뀌어 더 이상 필요 없음"',
    ),
    "aim-abandon": (
        "열린 Aim을 포기합니다. 하위 Proposal과 Goal을 자동으로 닫지 않지만, 새 Proposal·Goal 확정·Attempt 시작이 막힙니다.",
        'aim-abandon aim-2 --actor owner --reason "우선순위에서 제외됨"',
    ),
    "proposal-revise": (
        "열린 Proposal을 새 Proposal로 교체하고, 이전 방향·교체 이유를 활동 로그에 보존합니다.",
        'proposal-revise proposal-1 "개선된 실행 방향" --actor owner --reason "새 관찰을 반영"',
    ),
    "goal-accept": (
        "Proposal을 실행 가능한 Goal과 불변 Contract v1로 확정합니다.",
        "goal-accept proposal-1 --target ... --responsibility ... --scope ... --criterion ... --verification ... --key ...",
    ),
    "contract-revise": (
        "진행 중인 Goal의 scope·성공 기준·검증 방법을 새 불변 Contract 버전으로 바꿉니다.",
        'contract-revise goal-1 --scope "새 범위" --criterion "새 기준" --verification "새 검증" --actor "reviewer" --reason "요구가 바뀌었다"',
    ),
    "attempt-start": (
        "확정된 Goal Contract에 대해 실제 한 번의 실행 시도를 시작합니다.",
        'attempt-start goal-1 1 "human" "초안을 작성한다"',
    ),
    "runtime-attempt-start": (
        "Attempt 시작과 Runtime 요청 적재를 함께 기록합니다. 이 명령은 process를 실행하지 않습니다.",
        'runtime-attempt-start goal-1 1 "runtime" "구조 조사" codex "{\"prompt\": \"구조를 요약\"}" --request-id runtime-1',
    ),
    "runtime-requests": (
        "아직 worker가 claim하지 않은 Runtime 요청을 읽기 전용 JSON으로 봅니다.",
        "runtime-requests",
    ),
    "ao-attempt-start": (
        "과제의 현재 완료 기준(Contract)으로 AI_ORCHESTRATION(AO) Goal을 만들어 실행하는 ao_session.v1 요청을 쌓고 실행을 시작합니다. 실행은 runtime-dispatch가 한 번만 합니다. AO가 끝나면 결과를 실행 결과로 전달하고, --ao-workspace가 있으면 ao-import 관찰 근거도 기록합니다. AO가 사람 판단을 기다리면 결과를 전달하지 않고 요청을 claimed로 남깁니다.",
        'ao-attempt-start goal-1 1 claude "AO에서 실행" --ao-project-id my-project --ao-goal-id fix-search --ao-workspace ..\\ai_orchestration\\workspace',
    ),
    "ao-respond": (
        "ao_cli.v1 요청의 AO 세션이 기다리는 사람 입력(승인, 회의·질문의 차례)에 답합니다. 게이트 실패 질문에 --action expand_scope·escalate_scope로 답할 때는 더할 쓰기 경로를 --write-path로 넘깁니다(여러 번 가능). 답은 AO 세션의 입력함에 남고, 세션이 멈춰 있으면 ao-resume으로 이어서 실행합니다. 이 명령은 사람의 결정을 AO에 전하는 통로이며 Goal Loop 판정이 아닙니다.",
        'ao-respond runtime-3 --reject --feedback "검증을 건너뛰지 않는다"',
    ),
    "ao-resume": (
        "사람이 답한 뒤 멈춘 AO 세션을 AO CLI로 이어서 실행하고, 결과를 그 요청을 claim한 worker의 실행 결과로 전달합니다. 다시 사람을 기다리면 결과를 전달하지 않고 요청을 claimed로 남깁니다.",
        'ao-resume runtime-3 "worker-a"',
    ),
    "ao-review": (
        "ao_cli.v1 요청의 AO Goal이 검토 대기(awaiting_review)일 때 AO CLI resolve-review로 해소합니다. reject는 우려가 오탐이라 완료, accept는 우려를 받아들이고 처치를 끝내 완료, concern은 검토 대기를 유지하고, fail은 우려가 실제 결함이라 Goal을 실패로 끝냅니다(--reason 필요). 완료되면 그 결과를 claim한 worker의 실행 결과(succeeded)로, fail로 끝나면 실행 결과(failed)로 전달합니다. 사람의 결정을 AO에 전하는 통로이며 Goal Loop 판정이 아닙니다.",
        'ao-review runtime-3 "worker-a" --verdict reject --reason "export는 의도된 공개 API다"',
    ),
    "ao-status": (
        "AO 요청(ao_session.v1, ao_cli.v1)마다 요청 상태, AO Goal 상태, 마지막 AO 세션, 기다리는 사람 입력과 다음에 쓸 명령을 읽기 전용으로 봅니다.",
        "ao-status",
    ),
    "runtime-worker": (
        "pending Runtime 요청을 차례로 runtime-dispatch와 같은 방식으로 실행하는 백그라운드 worker입니다. AO가 사람을 기다리는 요청은 claimed로 남기고 다음 요청으로 넘어갑니다. --once는 한 바퀴만 돕니다.",
        'runtime-worker "worker-a" --poll-seconds 30',
    ),
    "runtime-request": (
        "pending·claimed·completed 상태와 관계없이 Runtime 요청 하나를 읽기 전용으로 봅니다.",
        "runtime-request runtime-1",
    ),
    "runtime-claim": (
        "한 pending Runtime 요청을 지정한 worker가 처리 중이라고 명시적으로 claim합니다.",
        'runtime-claim runtime-1 "worker-a"',
    ),
    "runtime-ack": (
        "자신이 claim한 Runtime 요청의 전달 처리를 끝냈다고 표시합니다. Attempt 결과는 별도입니다.",
        'runtime-ack runtime-1 "worker-a"',
    ),
    "runtime-requeue": (
        "자신이 claim한 요청을 recoverable failure 뒤 pending으로 되돌립니다.",
        'runtime-requeue runtime-1 "worker-a"',
    ),
    "runtime-dispatch": (
        "저장된 안전 profile payload를 한 worker가 한 번 실행·delivery·ack합니다. 실패하면 claim을 남기고 자동 재시도하지 않습니다.",
        'runtime-dispatch runtime-1 "worker-a"',
    ),
    "runtime-deliver": (
        "claim한 worker가 이미 관측한 외부 실행 결과를 기록·ack합니다. 이 명령은 process를 실행하지 않습니다.",
        'runtime-deliver runtime-1 "worker-a" succeeded delivery-1',
    ),
    "retry-decide": (
        "실패한 현재 Contract Attempt를 같은 약속으로 다시 실행할 이유를 Decision으로 남깁니다.",
        'retry-decide goal-1 attempt-1 "reviewer" "4-turn 제한에 도달했으므로 조사 한도를 조정해 다시 실행"',
    ),
    "reverification-decide": (
        "inconclusive Verdict 뒤 새 실행이 필요한 이유를 한 번 소비되는 re-verification Decision으로 남깁니다.",
        'reverification-decide goal-1 "reviewer" "새 측정이 필요하다"',
    ),
    "attempt-complete": (
        "실행 시도를 succeeded 또는 failed로 끝냅니다. 이것은 Goal의 검증 결론이 아닙니다.",
        "attempt-complete attempt-1 succeeded",
    ),
    "evidence-record": (
        "실행 결과에 관해 관찰한 사실(Evidence)을 남깁니다. --attach로 관찰에 사용한 로그 같은 파일(10MB 이하)을 복사해 함께 보존할 수 있습니다. 첨부는 기록할 때만 붙일 수 있습니다.",
        'evidence-record attempt-1 "human" "테스트 115개가 통과했다" --attach .\\pytest.log',
    ),
    "ao-import": (
        "AI_ORCHESTRATION(AO) Goal 하나의 결과(상태, 실행된 검증기, commit, 리뷰 사유)를 읽어 완료된 Attempt의 Evidence로 기록합니다. AO는 읽기만 하며, AO 스냅샷과 Goal·세션 원본 파일을 첨부합니다. AO 폴더나 Goal이 없으면 아무것도 기록하지 않습니다.",
        'ao-import attempt-1 claude ..\\ai_orchestration\\workspace\\projects\\my-project my-ao-goal',
    ),
    "memory-draft": (
        "프로젝트에 Memory Tool 지식 폴더(기본 .memory, .memory-tool.yml이나 MEMORY_TOOL_BASE로 바뀜)가 있을 때, 타임라인이 아직 인용하지 않은 판정과 달성 평가마다 타임라인 한 줄 초안과 'python -m memory_tool record' 명령을 보여 줍니다. Goal Loop는 그 폴더에 쓰지 않으며, 기록할지는 에이전트나 사람이 정합니다.",
        'memory-draft --record verdict-12',
    ),
    "ao-links": (
        "Goal Loop 과제와 AI_ORCHESTRATION(AO) Goal의 연결을 보여 줍니다. 연결은 과제의 참고 문서 링크('AO Goal <프로젝트>/<ID>')로 기록되며, ao-import와 ao_session.v1 dispatch가 자동으로 남깁니다. --backfill은 기존 요청과 AO 근거에서 빠진 연결을 채웁니다.",
        'ao-links --ao-goal ai-orchestration/fix-search',
    ),
    "ao-sync": (
        "연결된 AI_ORCHESTRATION(AO) Goal 쪽의 변화(AO Goal이 reverted·abandoned, 그 Goal을 affectedGoals에 둔 가정이 invalidated)를 찾아, 유효한 판정이 인용한 AO 근거에 정정을 남깁니다. 그 판정은 재검토(under_review)가 되고 해소는 사람이 합니다. 같은 변화는 한 번만 기록합니다. --dry-run은 기록하지 않고 찾은 변화만 보여 줍니다.",
        'ao-sync --ao-workspace E:\\ai_orchestration\\workspace --actor claude --dry-run',
    ),
    "reference-add": (
        "Aim, Proposal, Goal에 참고 문서(파일 경로나 URL)를 연결합니다. 문서는 바뀔 수 있으므로 판정 기준은 Contract 본문에 적으세요.",
        'reference-add goal-1 docs\\design.md --label "설계 문서" --actor "owner"',
    ),
    "reference-remove": (
        "참고 문서 연결을 해제합니다. 해제한 연결은 이력에 남습니다.",
        'reference-remove reference-1 --actor "owner" --reason "다른 문서로 대체"',
    ),
    "evidence-correct": (
        "기존 Evidence를 지우지 않고 정정 사실을 추가하며, 인용 Verdict의 Impact Review를 엽니다.",
        'evidence-correct evidence-1 "observer" "이전 관찰은 불완전했다"',
    ),
    "verification-begin": (
        "성공한 Attempt의 Evidence를 바탕으로 Goal 검증을 시작합니다.",
        "verification-begin goal-1 attempt-1",
    ),
    "verdict-submit": (
        "Evidence를 인용해 satisfied / not_satisfied / inconclusive Verdict를 기록합니다. --reason에 판단 이유(완료 기준 항목별 충족 여부)를 남기세요. 일부만 충족했다면 판정 값을 새로 만들지 않고 보완 실행, 기준 축소·명확화(contract-revise), 후속 접근안 중 하나로 처리합니다. inconclusive 뒤 재판정은 새 Evidence와 --supersedes-verdict가 필요합니다.",
        'verdict-submit goal-1 attempt-1 "reviewer" satisfied evidence-1 --reason "기준 1·2 충족, 3은 로그로 확인"',
    ),
    "status": (
        "저장된 Aim·Goal·Attempt 식별자와 projection checkpoint를 간단히 봅니다.",
        "status",
    ),
    "activity": (
        "append-only journal에서 만든 사람이 읽을 수 있는 한국어 활동 로그를 봅니다.",
        "activity",
    ),
    "next": (
        "현재 projection에서 가능한 다음 행동을 구조화된 읽기 전용 JSON으로 봅니다.",
        "next",
    ),
    "reviews": (
        "under_review Verdict·Aim Evaluation 및 그 Impact Review와 이미 기록된 resolution을 읽기 전용으로 봅니다.",
        "reviews",
    ),
    "verdict-review-resolve": (
        "Evidence correction 때문에 under_review가 된 Verdict의 Impact Review 하나를 reaffirm 또는 invalidate로 종결합니다.",
        'verdict-review-resolve impact-review-1 reaffirm "reviewer" "정정 후에도 근거가 충분하다"',
    ),
    "aim-evaluation-review-resolve": (
        "영향을 받은 Aim Evaluation의 Impact Review 하나를 reaffirm 또는 invalidate로 종결합니다.",
        'aim-evaluation-review-resolve aim-evaluation-1 impact-review-1 reaffirm "reviewer" "유효 Verdict가 Aim을 계속 뒷받침한다"',
    ),
    "aim-closure-invalidate": (
        "무효화된 Aim Evaluation 때문에 더 이상 현재 달성 상태가 아닌 Aim을 명시적으로 다시 엽니다.",
        'aim-closure-invalidate aim-1 aim-evaluation-1 review-resolution-1 "reviewer" "무효화된 종결 대신 Aim 추구를 재개한다"',
    ),
    "admission": (
        "새 Attempt를 막을 수 있는 Aim pause, Goal Hold, Verdict dependency, execute grant 상태를 읽기 전용으로 봅니다.",
        "admission",
    ),
    "aim-pause": (
        "Aim 아래의 새 Attempt 시작을 사유와 함께 일시 중지합니다. 이미 실행 중인 Attempt는 취소하지 않습니다.",
        'aim-pause aim-1 "owner" "외부 검토가 끝날 때까지 대기"',
    ),
    "aim-resume": (
        "일시 중지했던 Aim의 새 Attempt admission을 사유와 함께 다시 허용합니다. Attempt를 자동 시작하지 않습니다.",
        'aim-resume aim-1 "owner" "외부 검토 완료"',
    ),
    "goal-hold-add": (
        "Goal의 새 Attempt admission을 차단하는 Hold를 결정자와 사유와 함께 기록합니다.",
        'goal-hold-add goal-1 --actor owner --reason "외부 승인 대기"',
    ),
    "goal-hold-clear": (
        "Goal Hold를 해제하여 새 Attempt admission을 다시 허용할 수 있게 합니다.",
        "goal-hold-clear goal-1",
    ),
    "execution-grant": (
        "--enforce-authority 사용 시 특정 actor에게 Goal 실행 권한을 부여합니다.",
        'execution-grant goal-1 "executor"',
    ),
    "execution-revoke": (
        "특정 actor의 Goal 실행 권한을 철회합니다.",
        'execution-revoke goal-1 "executor"',
    ),
    "verdict-dependency-add": (
        "한 Goal의 새 Attempt가 다른 Verdict의 결과와 현재 유효성에 의존하도록 기록합니다.",
        "verdict-dependency-add goal-2 verdict-1 --required-result satisfied",
    ),
    "goal-parent-add": ("같은 Aim 안에서 child Goal의 immutable parent를 연결합니다.", "goal-parent-add goal-2 goal-1"),
    "goal-dependency-add": ("target Goal이 source Goal의 valid satisfied Verdict 뒤에만 실행되게 연결합니다.", "goal-dependency-add goal-2 goal-1"),
    "goal-graph": ("Goal parent-child와 Goal dependency 관계를 읽기 전용 JSON으로 봅니다.", "goal-graph"),
    "graph-export": ("Goal graph projection을 서버 없이 열 수 있는 로컬 HTML 파일로 export합니다.", "graph-export .\\goal-graph.html"),
    "guide": (
        "AI 없이 CLI만으로 Aim부터 review/recovery까지 진행하는 단계별 한국어 안내를 봅니다.",
        "guide",
    ),
    "aim-evaluate": (
        "유효한 satisfied Verdict나 직접 하위 목표의 유효한 달성 평가를 근거로 Aim 자체가 달성되었는지 명시적으로 평가합니다. 하위 목표를 모두 달성해도 상위 목표는 자동으로 달성되지 않습니다.",
        'aim-evaluate aim-1 "evaluator" "Aim의 의도까지 충족되었다." verdict-1 aim-evaluation-2',
    ),
    "project-init": (
        "폴더 안에 .goal-loop 프로젝트 기록을 만듭니다. 그 폴더 아래에서 실행하는 명령은 이 기록을 자동으로 사용합니다.",
        "project-init .",
    ),
    "where": (
        "지금 사용하는 기록(DB), 프로젝트 루트, 첨부 폴더, 그리고 DB를 찾은 방법을 보여 줍니다. 기록을 만들지 않습니다.",
        "where",
    ),
    "workspaces": (
        "데스크톱 앱에 등록된 작업 공간(프로젝트) 목록과 DB 경로를 보여 줍니다. 다른 명령에는 --workspace <이름>으로 지정합니다.",
        "workspaces",
    ),
    "doctor": (
        "SQLite 무결성, projection checkpoint, schema version을 점검합니다.",
        "doctor",
    ),
    "backup": (
        "실행 중인 DB에서도 일관된 SQLite 백업을 만듭니다.",
        "backup .\\backups\\goal-loop.sqlite3",
    ),
    "guided": (
        "Aim부터 질문에 답하며 Goal과 첫 Attempt까지 만드는 사람용 대화형 흐름입니다.",
        "guided",
    ),
    "claude-run": (
        "실행 중인 Attempt에서 Claude Code를 restricted 읽기 전용 조사로 한 번 실행합니다.",
        'claude-run attempt-1 "이 작업 폴더 구조를 요약하세요." --workdir .',
    ),
}


def _app_data_root() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "GoalLoopTool"
    return Path.home() / ".local" / "share" / "GoalLoopTool"


def resolve_database(database: Path | None, workspace: str | None, project: Path | None, cwd: Path) -> tuple[Path, str]:
    """Pick the journal and say how it was found: explicit choices first, then the enclosing project."""
    if database is not None:
        return database, "option"
    if workspace:
        return resolve_workspace(workspace), "workspace"
    if project is not None:
        candidate = project_database(project.resolve())
        if not candidate.is_file():
            raise ValueError(f"{project.resolve()}에 Goal Loop 프로젝트가 없습니다. 먼저 project-init을 실행하세요.")
        return candidate, "project-option"
    if override := os.environ.get("GOAL_LOOP_DATABASE"):
        return Path(override).expanduser(), "environment"
    if discovered := discover_project_database(cwd):
        return discovered, "project"
    return _app_data_root() / "goal-loop.sqlite3", "default"


def workspace_registry_path() -> Path:
    """The desktop app's workspace registry; the CLI only reads it."""
    return _app_data_root() / "workspaces.json"


def workspace_records() -> list[dict]:
    path = workspace_registry_path()
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_workspace(selector: str) -> Path:
    """Resolve a desktop workspace by exact id or exact name so a session and the GUI share one journal."""
    records = workspace_records()
    matches = [item for item in records if item.get("id") == selector] or [item for item in records if item.get("name") == selector]
    if len(matches) == 1:
        return Path(matches[0]["database"])
    names = ", ".join(f"{item.get('name')} ({item.get('id')})" for item in records) or "등록된 작업 공간 없음"
    if not matches:
        raise ValueError(f"작업 공간 '{selector}'을(를) 찾지 못했습니다. 등록된 작업 공간: {names}")
    raise ValueError(f"이름이 '{selector}'인 작업 공간이 여러 개입니다. id로 지정하세요: {names}")


def _command_prefix(database: Path) -> str:
    return f"python -m goal_loop_tool persistent --database {database}"


def _show_help(command: str | None, database: Path, output: Callable[[str], None] = print, *, all_commands: bool = False) -> None:
    """Present help as a user task, not just argparse syntax."""
    if all_commands:
        output("상세 명령 도움말 (각 예시는 현재 DB 경로를 포함합니다):")
        for name, (purpose, example) in _COMMAND_HELP.items():
            output(f"\n{name}: {purpose}")
            output(f"  {_command_prefix(database)} {example}")
        return
    if command is None:
        output("도움말: 아래 명령 중 하나를 입력하세요. 예: help aim-create")
        for name, (purpose, _) in _COMMAND_HELP.items():
            output(f"  {name:<20} {purpose}")
        return
    entry = _COMMAND_HELP.get(command)
    if entry is None:
        output(f"알 수 없는 명령입니다: {command}. 가능한 명령은 help로 확인하세요.")
        return
    purpose, example = entry
    output(f"{command}: {purpose}")
    output("실행 예:")
    output(f"  {_command_prefix(database)} {example}")


def _show_guide(database: Path, output: Callable[[str], None] = print) -> None:
    """Print a self-service lifecycle guide; it never writes a domain fact."""
    command = _command_prefix(database)
    lines = [
        "Goal Loop CLI 자습서 — 이 안내 자체는 어떤 기록도 만들지 않습니다.",
        "", "0. 현재 위치 확인: 먼저 읽기 전용 상태를 봅니다.",
        f"   {command} status", f"   {command} next", f"   {command} admission",
        "", "1. 계획 만들기: Aim은 원하는 변화, Proposal은 첫 실행 방향입니다.",
        f"   {command} aim-create \"처음 사용자가 끝까지 CLI를 쓸 수 있게 한다\"",
        f"   {command} proposal-create aim-1 \"사용 흐름을 관찰하고 안내를 개선한다\"",
        "", "2. 약속 확정: Goal Contract에는 결과 목표·범위·성공 기준·검증 방법을 각각 적습니다.",
        f"   {command} goal-accept proposal-1 --target \"사용 가능한 안내\" --responsibility produce --scope \"CLI와 문서\" --criterion \"처음 사용자도 완료\" --verification \"사용자가 로그 확인\" --key first-guide",
        "", "3. 실행과 검증: succeeded는 실행 결과일 뿐 Goal 성공이 아닙니다.",
        f"   {command} attempt-start goal-1 1 \"executor\" \"안내를 작성한다\"",
        f"   {command} attempt-complete attempt-1 succeeded",
        f"   {command} evidence-record attempt-1 \"observer\" \"파일과 동작을 확인했다\"",
        f"   {command} verification-begin goal-1 attempt-1",
        f"   {command} verdict-submit goal-1 attempt-1 \"reviewer\" satisfied evidence-1",
        "", "4. Runtime worker 연동(선택): 아래 명령은 요청만 저장하고 process를 자동 실행하지 않습니다.",
        f"   {command} runtime-attempt-start goal-1 1 \"runtime\" \"구조 조사\" codex_exec.v1 \"{{\\\"prompt\\\": \\\"구조를 요약\\\", \\\"workdir\\\": \\\".\\\"}}\" --request-id runtime-1",
        f"   {command} runtime-requests",
        f"   {command} runtime-claim runtime-1 \"worker-a\"",
        f"   {command} runtime-dispatch runtime-1 \"worker-a\"  # 한 번만 실행·delivery·ack",
        "   dispatch 실패 뒤에는 자동 재실행하지 않습니다. runtime-request로 claim을 점검하고 명시적으로 requeue합니다.",
        "", "5. 복구: failed는 Goal 실패가 아닙니다. next가 retry Decision 또는 재실행을 알려 줍니다.",
        "   inconclusive이면 같은 Attempt에 새 Evidence를 더하거나, re-verification Decision 뒤 새 Attempt를 시작합니다.",
        "", "6. 안전 제어: pause/Hold/dependency는 새 Attempt만 막습니다. admission과 next로 원인을 확인한 뒤 해소합니다.",
        f"   {command} aim-pause aim-1 \"owner\" \"검토 대기\"",
        f"   {command} goal-hold-add goal-1",
        "", "7. 정정과 종결: Evidence 정정 뒤에는 reviews와 next를 보고 review를 명시적으로 결론냅니다.",
        "   Goal verified와 Aim achieved는 다릅니다. Aim은 aim-evaluate로 별도 평가합니다.",
        "", "더 자세히: help <명령>, 전체 예시: help --all, 대화형 시작: guided",
    ]
    for line in lines:
        output(line)


def _show_interactive_guide(database: Path, output: Callable[[str], None] = print) -> None:
    """Teach the human-facing menu without requiring any prior Goal Loop terms."""
    lines = [
        "대화형 Goal Loop 안내 — 이 화면은 읽기 전용이며 아직 아무것도 저장하지 않습니다.",
        "", "먼저 네 가지를 구분하면 쉽습니다:",
        "  Aim: 이루고 싶은 변화나 결과입니다. 예: '처음 사용자가 CLI를 끝까지 쓴다.'",
        "  Proposal: Aim을 위해 먼저 시도할 방향입니다. 예: '사용 흐름을 관찰해 안내를 고친다.'",
        "  Goal Contract: 결과 목표·범위·성공 기준·검증 방법을 합의한 실행 약속입니다.",
        "  Attempt: 그 약속으로 실제 한 번 실행한 기록입니다. Attempt 성공은 Goal 성공 판정이 아닙니다.",
        "", "메뉴를 이렇게 사용하세요:",
        "  [1] 새 Aim 시작 — 처음이라면 여기서 Aim, Proposal, Goal Contract를 질문에 따라 만듭니다.",
        "  [2] 현재 상태 — 지금 막힌 이유와 다음에 할 일을 읽기 전용으로 봅니다.",
        "  [3] 명령 설명 — 명령 이름을 입력합니다. all을 입력하면 모든 명령의 상세 예시를 봅니다.",
        "  [4] 활동 로그 — 언제 누가 어떤 사실을 기록했는지 시간순으로 봅니다.",
        "  [5] Aim·Proposal 관리 — 기존 Aim에 새 방향을 추가하거나 열린 Proposal을 Goal로 확정합니다.",
        "  [6] Goal·Attempt 관리 — 실행 완료, Evidence, 검증, Verdict를 상태에 맞춰 진행합니다.",
        "", "가장 흔한 정상 흐름:",
        "  새 Aim → Proposal → Goal Contract → 첫 Attempt → 실행 완료 → Evidence → 검증 시작 → 독립 Verdict",
        "  Goal이 verified여도 Aim은 자동 달성되지 않습니다. Aim 전체가 충족됐다고 판단할 때만 별도 평가합니다.",
        "", "막혔을 때의 선택:",
        "  Attempt 실패: Goal 실패가 아닙니다. 같은 Contract 재시도는 이유를 Decision으로 기록한 뒤 한 번만 시작합니다.",
        "  Verdict inconclusive: 같은 Attempt에 새 Evidence를 더하거나, 새 실행이 필요하다는 Decision을 먼저 기록합니다.",
        "  Evidence 정정: 원 기록을 지우지 않습니다. reviews/next를 확인하고 Verdict와 Aim Evaluation review를 명시적으로 결론냅니다.",
        "  pause/Hold/dependency: 새 Attempt만 멈춥니다. [2] 또는 admission으로 원인을 확인한 뒤 재개·해제합니다.",
        "", f"명령형 전체 자습서도 필요하면: {_command_prefix(database)} guide",
        "어느 질문에서든 빈 입력은 그 단계를 안전하게 취소하고, 이미 저장한 기록은 지우지 않습니다.",
    ]
    for line in lines:
        output(line)


def _run_guided_operation(label: str, operation: Callable[[], None], output: Callable[[str], None]) -> None:
    """Keep an invalid human menu action in the terminal instead of a traceback."""
    try:
        operation()
    except GoalLoopError as error:
        output(f"{label} 작업을 기록하지 않았습니다: {error}")
        output("현재 상태와 허용되는 다음 행동은 메뉴 [2] 또는 admission/reviews에서 확인하세요.")


def _next_action_records(loop: PersistentGoalLoop, database: Path) -> list[dict]:
    """Project the smallest valid next actions without appending any journal event.

    The records are intentionally suggestions, not commands automatically carried
    out by an agent.  A caller must still obtain authorization before changing a
    durable Aim, Proposal, Goal, Attempt, Evidence, Decision, or Verdict.
    """
    prefix = ["goal-loop", "--database", str(database)]
    actions: list[dict] = []

    def add(kind: str, subject_kind: str, subject_id: str | None, summary: str, command: list[str] | None = None, *, requires_user_authorization: bool = True) -> None:
        # Several Goals under one paused Aim would otherwise repeat the same resume action.
        if any(action["kind"] == kind and action["subject"] == {"kind": subject_kind, "id": subject_id} for action in actions):
            return
        actions.append({
            "kind": kind,
            "subject": {"kind": subject_kind, "id": subject_id},
            "summary": summary,
            "requires_user_authorization": requires_user_authorization,
            "command_argv": prefix + command if command else None,
        })

    # Resolve Verdict reviews first. An Aim Evaluation cannot be reaffirmed while
    # any cited Verdict is still under review.
    resolutions = tuple(loop.review_resolutions.values())
    for review in loop.impact_reviews.values():
        verdict_id = review.trigger_verdict_id
        verdict_resolved = any(
            resolution.impact_review_id == review.id
            and resolution.subject_kind == "verdict"
            and resolution.subject_id == verdict_id
            for resolution in resolutions
        )
        if (loop.verdict_validity.get(verdict_id) is VerdictValidity.UNDER_REVIEW
                and not verdict_resolved):
            add(
                "resolve_verdict_review", "impact_review", review.id,
                f"Evidence correction 영향 검토 {review.id}가 Verdict {verdict_id}을 under_review로 만들었습니다. 근거를 재검토한 뒤 reaffirm 또는 invalidate로 명시적으로 종결하세요.",
                ["verdict-review-resolve", review.id, "<reaffirm|invalidate>", "<reviewer>", "<reason>"],
            )

    for review in loop.impact_reviews.values():
        for evaluation_id in review.affected_aim_evaluation_ids:
            evaluation_resolved = any(
                resolution.impact_review_id == review.id
                and resolution.subject_kind == "aim_evaluation"
                and resolution.subject_id == evaluation_id
                for resolution in resolutions
            )
            if evaluation_resolved or loop.aim_evaluation_validity.get(evaluation_id) is not VerdictValidity.UNDER_REVIEW:
                continue
            evaluation = loop.aim_evaluations[evaluation_id]
            cited_validities = [loop.verdict_validity.get(verdict_id) for verdict_id in evaluation.verdict_ids]
            cited_validities += [loop.aim_evaluation_validity.get(child_id) for child_id in evaluation.child_evaluation_ids]
            if any(validity is VerdictValidity.UNDER_REVIEW for validity in cited_validities):
                continue
            suggested_disposition = "invalidate" if any(
                validity is VerdictValidity.INVALIDATED for validity in cited_validities
            ) else "reaffirm"
            add(
                "resolve_aim_evaluation_review", "aim_evaluation", evaluation_id,
                f"Aim Evaluation {evaluation_id}의 영향 검토 {review.id}를 종결하세요. 인용 Verdict의 현재 상태에 맞춰 {suggested_disposition} 여부를 판단해야 합니다.",
                ["aim-evaluation-review-resolve", evaluation_id, review.id, f"<{suggested_disposition}>", "<reviewer>", "<reason>"],
            )

    for aim in loop.aims.values():
        if aim.phase.value != "achieved" or loop.is_currently_achieved(aim.id):
            continue
        for evaluation in loop.aim_evaluations.values():
            if (evaluation.aim_id != aim.id
                    or loop.aim_evaluation_validity.get(evaluation.id) is not VerdictValidity.INVALIDATED):
                continue
            invalidating_resolution = next((
                resolution for resolution in resolutions
                if resolution.subject_kind == "aim_evaluation"
                and resolution.subject_id == evaluation.id
                and resolution.disposition is ReviewDisposition.INVALIDATE
            ), None)
            if invalidating_resolution is not None:
                add(
                    "reopen_aim_after_closure_invalidation", "aim_evaluation", evaluation.id,
                    f"Aim {aim.id}의 종결 근거인 Aim Evaluation {evaluation.id}이 무효화됐고 현재 유효한 달성 평가는 없습니다. Aim을 다시 열지 여부를 명시적으로 판단하세요.",
                    ["aim-closure-invalidate", aim.id, evaluation.id, invalidating_resolution.id, "<reviewer>", "<reason>"],
                )

    if not loop.aims:
        add("create_aim", "workspace", None, "아직 Aim이 없습니다. 이루고 싶은 변화나 결과를 Aim으로 먼저 기록하세요.", ["aim-create", "<aim-description>"])
        return actions

    def abandoned_ancestor(aim_id: str) -> bool:
        return any(loop.aims[ancestor].phase.value == "abandoned" for ancestor in loop.aim_ancestors(aim_id))

    for aim in loop.aims.values():
        if aim.phase.value == "open" and not abandoned_ancestor(aim.id):
            open_proposals = [p for p in loop.proposals.values() if p.aim_id == aim.id and p.status.value == "open"]
            live_goals = [g for g in loop.goals.values() if g.identity.primary_aim_id == aim.id and g.phase.value != "stopped"]
            # An Aim split into sub-Aims advances through them; it needs its own Proposal only by choice.
            has_sub_aims = any(child.parent_aim_id == aim.id for child in loop.aims.values())
            if not open_proposals and not live_goals and not has_sub_aims:
                add("create_proposal", "aim", aim.id, f"Aim {aim.id}에 열린 Proposal이 없습니다. 먼저 한 가지 실행 방향을 Proposal로 적으세요.", ["proposal-create", aim.id, "<direction>"])
    for proposal in loop.proposals.values():
        # An achieved Aim may retain historical/open alternatives, but accepting one would
        # violate the Core contract.  `next` must never suggest a command Core will reject.
        if proposal.status.value == "open" and loop.aims[proposal.aim_id].phase.value == "open" and not abandoned_ancestor(proposal.aim_id):
            add("accept_proposal", "proposal", proposal.id, f"Proposal {proposal.id}을 실행하기 전에 결과 목표, 범위, 성공 기준, 검증 방법을 합의하여 Goal Contract로 확정하세요.", ["goal-accept", proposal.id, "--target", "<target>", "--responsibility", "<kind>", "--scope", "<scope>", "--criterion", "<criterion>", "--verification", "<verification>", "--key", "<idempotency-key>"])
    for goal in loop.goals.values():
        aim_id = goal.identity.primary_aim_id
        if loop.aims[aim_id].phase.value != "open" or abandoned_ancestor(aim_id):
            continue
        if goal.phase.value == "stopped":
            running = [a for a in loop.attempts.values() if a.goal_id == goal.id and a.outcome is AttemptOutcome.RUNNING]
            for attempt in running:
                add("complete_attempt", "attempt", attempt.id, f"Goal {goal.id}은 중단됐지만 Attempt {attempt.id}이 실행 중입니다. 정리하려면 실제 결과를 기록하세요.", ["attempt-complete", attempt.id, "<succeeded|failed>"])
            continue
        paused_ancestor = next((ancestor for ancestor in loop.aim_ancestors(aim_id) if ancestor in loop.paused_aim_ids), None)
        if paused_ancestor and aim_id not in loop.paused_aim_ids:
            add("resume_aim", "aim", paused_ancestor, f"상위 목표 {paused_ancestor}이 pause 상태이므로 Goal {goal.id}의 새 Attempt를 시작할 수 없습니다. 재개할 사유가 확인되면 상위 목표를 resume하세요.", ["aim-resume", paused_ancestor, "<actor>", "<reason>"])
            continue
        if aim_id in loop.paused_aim_ids:
            add("resume_aim", "aim", aim_id, f"Aim {aim_id}이 pause 상태이므로 Goal {goal.id}의 새 Attempt를 시작할 수 없습니다. 재개할 사유가 확인되면 Aim을 resume하세요.", ["aim-resume", aim_id, "<actor>", "<reason>"])
            continue
        if goal.id in loop.goal_holds:
            add("clear_goal_hold", "goal", goal.id, f"Goal {goal.id}에 unresolved Hold가 있어 새 Attempt를 시작할 수 없습니다. Hold를 해제해도 되는지 확인한 뒤 clear하세요.", ["goal-hold-clear", goal.id])
            continue
        blocked_dependencies = []
        for dependency in loop.verdict_dependencies.get(goal.id, []):
            verdict_id = getattr(dependency, "required_verdict_id", dependency)
            required_result = getattr(dependency, "required_result", VerdictResult.SATISFIED)
            verdict = loop.verdicts[verdict_id]
            if (loop.verdict_validity.get(verdict_id) is not VerdictValidity.VALID
                    or verdict.result is not required_result):
                blocked_dependencies.append(verdict_id)
        if blocked_dependencies:
            add("inspect_verdict_dependency", "goal", goal.id, f"Goal {goal.id}은 dependency Verdict {', '.join(blocked_dependencies)}가 현재 valid required result가 아니어서 새 Attempt를 시작할 수 없습니다. reviews와 activity에서 근거 상태를 확인하세요.", ["reviews"], requires_user_authorization=False)
            continue
        blocked_goals = [
            dependency.required_goal_id for dependency in loop.goal_dependencies.get(goal.id, [])
            if not loop._has_current_valid_satisfied_verdict(dependency.required_goal_id)
        ]
        if blocked_goals:
            add("inspect_goal_dependency", "goal", goal.id, f"Goal {goal.id}은 선행 Goal {', '.join(blocked_goals)}의 현재 valid satisfied Verdict가 필요합니다. goal-graph와 next로 선행 Goal을 먼저 완료하세요.", ["goal-graph"], requires_user_authorization=False)
            continue
        current_attempts = [a for a in loop.attempts.values() if a.goal_id == goal.id and a.contract_version == goal.current_contract_version]
        if not current_attempts:
            add("start_attempt", "goal", goal.id, f"Goal {goal.id}은 Contract v{goal.current_contract_version}로 첫 Attempt를 시작할 수 있습니다.", ["attempt-start", goal.id, str(goal.current_contract_version), "<executor>", "<operation>"])
            continue
        latest = current_attempts[-1]
        if latest.outcome is AttemptOutcome.RUNNING:
            add("complete_attempt", "attempt", latest.id, f"Attempt {latest.id}이 실행 중입니다. 실제 실행 결과를 succeeded 또는 failed로 기록하세요.", ["attempt-complete", latest.id, "<succeeded|failed>"])
        elif latest.outcome is AttemptOutcome.FAILED:
            pending = next((decision for decision in loop.decisions.values() if decision.kind == "retry_same_contract" and decision.goal_id == goal.id and decision.target_attempt_id == latest.id and decision.consumed_by_attempt_id is None), None)
            if pending:
                add("start_retry_attempt", "decision", pending.id, f"실패한 Attempt {latest.id}에 대한 retry Decision {pending.id}이 있습니다. 같은 Contract로 한 번만 소비하여 재시도할 수 있습니다.", ["attempt-start", goal.id, str(goal.current_contract_version), "<executor>", "<operation>", "--retry-decision", pending.id])
            else:
                add("decide_retry", "attempt", latest.id, f"Attempt {latest.id}은 실패했습니다. 같은 Contract로 재시도하려면 이유를 retry Decision으로 먼저 남기세요.", ["retry-decide", goal.id, latest.id, "<actor>", "<reason>"])
        elif goal.phase.value == "active":
            attempt_verdicts = [verdict for verdict in loop.verdicts.values() if verdict.attempt_id == latest.id]
            if attempt_verdicts and attempt_verdicts[-1].result is VerdictResult.NOT_SATISFIED:
                add("start_repair_attempt", "goal", goal.id, f"Attempt {latest.id}의 Verdict는 not_satisfied입니다. 같은 Contract에서 보완 작업을 새 Attempt로 시작할 수 있습니다.", ["attempt-start", goal.id, str(goal.current_contract_version), "<executor>", "<operation>"])
            elif not [e for e in loop.evidence.values() if e.attempt_id == latest.id]:
                add("record_evidence", "attempt", latest.id, f"완료된 Attempt {latest.id}에 관한 관찰 사실(Evidence)을 기록하세요. Attempt 성공은 아직 Goal 성공이 아닙니다.", ["evidence-record", latest.id, "<actor>", "<observation>"])
            elif attempt_verdicts and attempt_verdicts[-1].result is VerdictResult.INCONCLUSIVE:
                pending = next((decision for decision in loop.decisions.values() if decision.kind == "reverification_requires_new_attempt" and decision.goal_id == goal.id and decision.target_attempt_id == latest.id and decision.consumed_by_attempt_id is None), None)
                if pending:
                    add("start_reverification_attempt", "decision", pending.id, f"inconclusive Verdict 뒤 새 실행을 허용하는 re-verification Decision {pending.id}이 있습니다. 현재 Contract로 한 번만 소비하세요.", ["attempt-start", goal.id, str(goal.current_contract_version), "<executor>", "<operation>", "--reverification-decision", pending.id])
                else:
                    add("decide_reverification", "goal", goal.id, f"Goal {goal.id}의 이전 Verdict는 inconclusive입니다. 새 실행이 필요하면 먼저 re-verification Decision을 남기세요.", ["reverification-decide", goal.id, "<actor>", "<reason>"])
            else:
                add("begin_verification", "goal", goal.id, f"Attempt {latest.id}에 Evidence가 있습니다. Goal 검증을 시작할 수 있습니다.", ["verification-begin", goal.id, latest.id])
        elif goal.phase.value == "verifying":
            evidence_ids = [evidence.id for evidence in loop.evidence.values() if evidence.attempt_id == goal.verifying_attempt_id]
            prior = [verdict for verdict in loop.verdicts.values() if verdict.attempt_id == goal.verifying_attempt_id]
            if prior and prior[-1].result is VerdictResult.INCONCLUSIVE:
                new_evidence_ids = [evidence_id for evidence_id in evidence_ids if evidence_id not in prior[-1].evidence_ids]
                if not new_evidence_ids:
                    add("record_additional_evidence", "attempt", goal.verifying_attempt_id, f"Goal {goal.id}의 이전 Verdict는 inconclusive입니다. 같은 Attempt에 새 Evidence를 기록한 뒤에만 재판정할 수 있습니다.", ["evidence-record", goal.verifying_attempt_id or "<attempt-id>", "<actor>", "<new-observation>"])
                    add("decide_reverification", "goal", goal.id, f"같은 Attempt의 추가 관찰 대신 새 실행이 필요하다면 Goal {goal.id}에 re-verification Decision을 먼저 남기세요.", ["reverification-decide", goal.id, "<actor>", "<reason>"])
                else:
                    add("submit_superseding_verdict", "verdict", prior[-1].id, f"Goal {goal.id}에 새 Evidence가 있습니다. inconclusive Verdict {prior[-1].id}를 명시적으로 supersede하는 새 Verdict를 제출하세요.", ["verdict-submit", goal.id, goal.verifying_attempt_id or "<attempt-id>", "<verifier>", "<satisfied|not_satisfied|inconclusive>", *evidence_ids, "--supersedes-verdict", prior[-1].id])
            else:
                add("submit_verdict", "goal", goal.id, f"Goal {goal.id}은 검증 중입니다. Evidence를 인용하여 독립 verifier가 Verdict를 제출하세요.", ["verdict-submit", goal.id, goal.verifying_attempt_id or "<attempt-id>", "<verifier>", "<satisfied|not_satisfied|inconclusive>", *(evidence_ids or ["<evidence-id>"])])
    for aim in loop.aims.values():
        valid_satisfied = [
            verdict.id for verdict in loop.verdicts.values()
            if loop.goals[verdict.goal_id].identity.primary_aim_id == aim.id
            and verdict.result is VerdictResult.SATISFIED
            and loop.verdict_validity.get(verdict.id) is VerdictValidity.VALID
        ]
        valid_child_evaluations = [
            evaluation.id for evaluation in loop.aim_evaluations.values()
            if loop.aims[evaluation.aim_id].parent_aim_id == aim.id
            and loop.aim_evaluation_validity.get(evaluation.id) is VerdictValidity.VALID
        ]
        if aim.phase.value == "open" and (valid_satisfied or valid_child_evaluations):
            basis = "유효한 satisfied Verdict" if valid_satisfied else "달성된 하위 목표"
            add("evaluate_aim", "aim", aim.id, f"Aim {aim.id}에는 {basis}가 있습니다. Goal이나 하위 목표의 완료와 별도로 Aim의 의도까지 충족됐는지 평가하세요.", ["aim-evaluate", aim.id, "<evaluator>", "<conclusion>", *valid_satisfied, *valid_child_evaluations])
    return actions


def _next_actions(loop: PersistentGoalLoop, database: Path) -> list[str]:
    """Render the structured projection as compact guidance for the human CLI."""
    actions = _next_action_records(loop, database)
    if not actions:
        return ["현재 진행 중인 항목에 맞는 다음 명령은 help 또는 activity에서 확인하세요."]
    return [action["summary"] for action in actions]


def _review_records(loop: PersistentGoalLoop) -> dict:
    """Return the correction/review projection without changing the journal."""
    resolutions_by_review: dict[str, list[dict]] = {}
    for resolution in loop.review_resolutions.values():
        resolutions_by_review.setdefault(resolution.impact_review_id, []).append({
            "id": resolution.id,
            "subject_kind": resolution.subject_kind,
            "subject_id": resolution.subject_id,
            "disposition": resolution.disposition.value,
            "decided_by_actor_id": resolution.decided_by_actor_id,
            "reason": resolution.reason,
        })
    return {
        "under_review_verdicts": [
            {**verdict.__dict__, "result": verdict.result.value, "validity": VerdictValidity.UNDER_REVIEW.value}
            for verdict in loop.verdicts.values()
            if loop.verdict_validity.get(verdict.id) is VerdictValidity.UNDER_REVIEW
        ],
        "under_review_aim_evaluations": [
            {**evaluation.__dict__, "validity": VerdictValidity.UNDER_REVIEW.value}
            for evaluation in loop.aim_evaluations.values()
            if loop.aim_evaluation_validity.get(evaluation.id) is VerdictValidity.UNDER_REVIEW
        ],
        "impact_reviews": [
            {
                **review.__dict__,
                "trigger_verdict_validity": loop.verdict_validity.get(review.trigger_verdict_id).value,
                "affected_aim_evaluation_validities": {
                    evaluation_id: loop.aim_evaluation_validity.get(evaluation_id).value
                    for evaluation_id in review.affected_aim_evaluation_ids
                },
                "resolutions": resolutions_by_review.get(review.id, []),
            }
            for review in loop.impact_reviews.values()
        ],
    }


def _admission_records(loop: PersistentGoalLoop) -> dict:
    """Return the durable facts that can admit or block a new Attempt."""
    dependencies = []
    for goal_id, records in loop.verdict_dependencies.items():
        for dependency in records:
            verdict_id = getattr(dependency, "required_verdict_id", dependency)
            required_result = getattr(dependency, "required_result", VerdictResult.SATISFIED)
            verdict = loop.verdicts[verdict_id]
            dependencies.append({
                "goal_id": goal_id,
                "verdict_id": verdict_id,
                "required_result": required_result.value,
                "actual_result": verdict.result.value,
                "verdict_validity": loop.verdict_validity.get(verdict_id).value,
            })
    return {
        "paused_aim_ids": sorted(loop.paused_aim_ids),
        "goal_hold_ids": sorted(loop.goal_holds),
        "execution_grants": {actor_id: sorted(goal_ids) for actor_id, goal_ids in loop.execution_grants.items()},
        "verdict_dependencies": dependencies,
    }


def _choose_responsibility(
    input_fn: Callable[[str], str], output: Callable[[str], None]
) -> str:
    """Offer common responsibility kinds without making the stored value closed."""
    output("2/5 이 Goal에서 주로 무엇을 하나요? 이것은 사람의 직책이 아니라 작업의 성격입니다.")
    output("  [1] 무언가를 만든다·변경한다 (produce)")
    output("  [2] 사실·가능성을 조사한다 (investigate)")
    output("  [3] 근거를 비교해 결정을 내린다 (decide)")
    output("  번호 또는 produce / investigate / decide를 입력하세요. 다른 표현도 그대로 저장할 수 있습니다.")
    answer = input_fn("책임 유형: ").strip()
    return {"1": "produce", "2": "investigate", "3": "decide"}.get(answer.lower(), answer)


def _accept_proposal_interactively(
    loop: PersistentGoalLoop, proposal, *, input_fn: Callable[[str], str], output: Callable[[str], None]
):
    """Turn one open Proposal into a Goal without exposing Contract flags."""
    output(f"Proposal {proposal.id}: {proposal.direction}")
    output("이제 '무엇을 약속하고, 언제 성공으로 볼지'를 정합니다. 아래 답은 작업 제목이 아니라 나중에 확인할 약속입니다.")
    output("빈 입력이 하나라도 있으면 Goal은 확정하지 않고 Proposal만 안전하게 남깁니다.")
    output("1/5 결과 목표 (target): 이 Goal이 성공했을 때 사용자가·대상이 실제로 할 수 있게 되는 변화입니다.")
    output("  예: '새 사용자가 안내 없이 Aim을 만들고 첫 Attempt를 시작한다.'")
    target = input_fn("성공했을 때 무엇이 가능하거나 달라져야 하나요?: ").strip()
    responsibility = _choose_responsibility(input_fn, output)
    output("3/5 실행 범위 (scope): 이번 Goal에 포함하는 일과 의도적으로 제외하는 경계입니다.")
    output("  예: '대화형 첫 실행과 안내 문구만; Codex/Claude 자동 실행은 제외.'")
    scope = input_fn("이번에는 어디까지 할까요?: ").strip()
    output("4/5 성공 기준 (criterion): 완료했다고 주장할 수 있는, 관찰 가능하거나 판정 가능한 조건입니다.")
    output("  예: '새 DB에서 안내를 따라 Aim·Goal·Attempt를 만들고 activity에 기록이 보인다.'")
    criterion = input_fn("어떤 사실이 확인되면 성공인가요?: ").strip()
    output("5/5 검증 방법 (verification): 누가, 무엇을 보고, 어떤 방식으로 성공 기준을 확인할지입니다.")
    output("  예: 'reviewer가 SQLite activity 로그와 CLI 출력으로 모든 단계를 확인한다.'")
    verification = input_fn("누가 어떤 기록·결과를 보고 확인하나요?: ").strip()
    if not all((target, responsibility, scope, criterion, verification)):
        output("Goal은 확정하지 않았습니다. Proposal은 저장되어 있으며, 나중에 다시 관리 화면에서 이어갈 수 있습니다.")
        return None
    goal = loop.accept_proposal(
        proposal.id,
        GoalIdentity(proposal.aim_id, target, responsibility),
        ContractDraft(scope, criterion, verification),
        idempotency_key=f"guided-{proposal.id}",
    )
    output(f"Goal {goal.id}을 Contract v1로 확정했습니다.")
    output("Goal 성공과 Attempt 성공은 다릅니다. 실행 뒤 Evidence를 남기고, 별도 검증 Verdict를 제출해야 합니다.")
    return goal


def _manage_aim_and_proposals(
    loop: PersistentGoalLoop, *, input_fn: Callable[[str], str], output: Callable[[str], None]
) -> None:
    """Let a person resume an open Aim after deliberately deferring its Proposal."""
    if not loop.aims:
        output("관리할 Aim이 없습니다. [1] 새 Aim 시작에서 먼저 이루고 싶은 변화를 적으세요.")
        return
    output("Aim과 Proposal 상태:")
    for aim in loop.aims.values():
        proposals = [proposal for proposal in loop.proposals.values() if proposal.aim_id == aim.id]
        proposal_text = "Proposal 없음" if not proposals else ", ".join(f"{proposal.id} · {proposal.status.value}" for proposal in proposals)
        output(f"- {aim.id} [{aim.phase.value}]: {aim.description}")
        output(f"  Proposal: {proposal_text}")
    default_aim_id = next(iter(loop.aims)) if len(loop.aims) == 1 else ""
    prompt = f"관리할 Aim ID{' (빈 입력 = ' + default_aim_id + ')' if default_aim_id else ''}: "
    aim_id = input_fn(prompt).strip() or default_aim_id
    aim = loop.aims.get(aim_id)
    if aim is None:
        output("목록에 있는 Aim ID를 입력하세요. 아무것도 변경하지 않았습니다.")
        return
    if aim.phase.value != "open":
        output("종결된 Aim에는 새 Proposal을 추가할 수 없습니다.")
        return
    open_proposals = [proposal for proposal in loop.proposals.values() if proposal.aim_id == aim.id and proposal.status.value == "open"]
    output(f"선택한 Aim: {aim.description}")
    output("[1] 새 Proposal 추가  [2] 열린 Proposal을 Goal로 확정  [0] 돌아가기")
    choice = input_fn("> ").strip()
    if choice == "2":
        if not open_proposals:
            output("이 Aim에는 Goal로 확정할 열린 Proposal이 없습니다.")
            return
        for proposal in open_proposals:
            output(f"- {proposal.id}: {proposal.direction}")
        default_proposal_id = open_proposals[0].id if len(open_proposals) == 1 else ""
        prompt = f"확정할 Proposal ID{' (빈 입력 = ' + default_proposal_id + ')' if default_proposal_id else ''}: "
        proposal_id = input_fn(prompt).strip() or default_proposal_id
        proposal = next((item for item in open_proposals if item.id == proposal_id), None)
        if proposal is None:
            output("목록에 있는 열린 Proposal ID를 입력하세요.")
            return
        _accept_proposal_interactively(loop, proposal, input_fn=input_fn, output=output)
        return
    if choice != "1":
        return
    output("Proposal은 Aim을 향한 한 가지 실행 방향입니다. 아직 실행 약속이나 완료 선언이 아닙니다.")
    output("예: '처음 사용자가 막히는 지점을 관찰하고 안내 문구를 개선한다.'")
    direction = input_fn("이 Aim을 위해 먼저 해 볼 방향: ").strip()
    if not direction:
        output("Proposal은 만들지 않았습니다. Aim은 그대로 남아 있습니다.")
        return
    proposal = loop.create_proposal(aim.id, direction)
    output(f"Proposal {proposal.id}을 저장했습니다: {proposal.direction}")
    output("다음에는 이 Proposal을 Goal로 확정할 수 있습니다. 현재는 help goal-accept에서 필요한 Contract 항목을 안내합니다.")


def _start_existing_attempt(
    loop: PersistentGoalLoop,
    *,
    input_fn: Callable[[str], str],
    output: Callable[[str], None],
) -> None:
    """Resume a committed Goal without asking the user to reconstruct its ID-heavy command."""
    candidates = [
        goal for goal in loop.goals.values()
        if not any(attempt.goal_id == goal.id for attempt in loop.attempts.values())
    ]
    if not candidates:
        output("첫 Attempt를 시작할 수 있는 Goal이 없습니다. 실행 중인 Attempt는 완료 기록을, 검증 중인 Goal은 Evidence와 Verdict를 진행하세요.")
        return
    output("아직 실행 기록이 없는 Goal입니다:")
    for goal in candidates:
        contract = loop.contracts[(goal.id, goal.current_contract_version)]
        output(f"- {goal.id}: {goal.identity.target_and_desired_result} (Contract v{goal.current_contract_version})")
        output(f"  성공 기준: {contract.success_criterion}")
    default_goal_id = candidates[0].id if len(candidates) == 1 else ""
    prompt = f"시작할 Goal ID{' (빈 입력 = ' + default_goal_id + ')' if default_goal_id else ''}: "
    goal_id = input_fn(prompt).strip() or default_goal_id
    if goal_id not in {goal.id for goal in candidates}:
        output("목록에 있는, 아직 Attempt가 없는 Goal ID를 입력하세요. 아무것도 변경하지 않았습니다.")
        return
    _start_attempt_for_goal(loop, loop.goals[goal_id], input_fn=input_fn, output=output)


def _start_attempt_for_goal(
    loop: PersistentGoalLoop, goal, *, input_fn: Callable[[str], str], output: Callable[[str], None],
    retry_decision_id: str | None = None,
    reverification_decision_id: str | None = None,
) -> None:
    output("Attempt는 실제 한 번의 실행 기록입니다. 여기서 Goal이 끝났다고 판정하지는 않습니다.")
    executor = input_fn("이번 실행을 수행할 사람·도구 이름: ").strip()
    operation = input_fn("이번 한 번에 실제로 할 작업: ").strip()
    if not executor or not operation:
        output("Attempt는 시작하지 않았습니다. Goal은 그대로 남아 있습니다.")
        return
    attempt = loop.start_attempt(
        goal.id, goal.current_contract_version, executor, operation,
        retry_decision_id=retry_decision_id, reverification_decision_id=reverification_decision_id,
    )
    output(f"Attempt {attempt.id}을 시작했습니다. 실제 실행 뒤 결과를 기록하세요.")
    output("다음 순서: Attempt 완료 → Evidence 기록 → 검증 시작 → Verdict 제출")


def _manage_goal_work(
    loop: PersistentGoalLoop, *, input_fn: Callable[[str], str], output: Callable[[str], None]
) -> None:
    """State-aware human operations for the normal Goal lifecycle."""
    if not loop.goals:
        output("관리할 Goal이 없습니다. 먼저 새 Aim을 시작해 Goal을 확정하세요.")
        return
    output("Goal과 최근 Attempt 상태:")
    for goal in loop.goals.values():
        attempts = [a for a in loop.attempts.values() if a.goal_id == goal.id]
        attempt_text = "Attempt 없음" if not attempts else f"{attempts[-1].id} · {attempts[-1].outcome.value}"
        output(f"- {goal.id} [{goal.phase.value}]: {goal.identity.target_and_desired_result}")
        output(f"  최근 실행: {attempt_text}")
    default_goal_id = next(iter(loop.goals)) if len(loop.goals) == 1 else ""
    prompt = f"관리할 Goal ID{' (빈 입력 = ' + default_goal_id + ')' if default_goal_id else ''}: "
    goal_id = input_fn(prompt).strip() or default_goal_id
    goal = loop.goals.get(goal_id)
    if goal is None:
        output("목록에 있는 Goal ID를 입력하세요. 아무것도 변경하지 않았습니다.")
        return
    attempts = [a for a in loop.attempts.values() if a.goal_id == goal.id]
    latest = attempts[-1] if attempts else None
    output(f"선택한 Goal: {goal.id} — {goal.identity.target_and_desired_result}")
    if latest is None:
        output("[1] 첫 Attempt 시작  [0] 돌아가기")
        if input_fn("> ").strip() == "1":
            _start_attempt_for_goal(loop, goal, input_fn=input_fn, output=output)
        return
    if latest.outcome is AttemptOutcome.RUNNING:
        output(f"{latest.id}은 아직 실행 중입니다. [1] 성공으로 완료  [2] 실패로 완료  [0] 돌아가기")
        choice = input_fn("> ").strip()
        outcome = {"1": AttemptOutcome.SUCCEEDED, "2": AttemptOutcome.FAILED}.get(choice)
        if outcome is None:
            return
        loop.complete_attempt(latest.id, outcome)
        label = "성공" if outcome is AttemptOutcome.SUCCEEDED else "실패"
        output(f"{latest.id}을 {label}으로 완료 기록했습니다. 이것은 Goal Verdict가 아닙니다.")
        return
    if latest.outcome is AttemptOutcome.FAILED:
        output(f"{latest.id}은 실패로 완료되었습니다. [1] 실패 Evidence 기록  [2] 같은 Contract 재시도 결정·새 Attempt 시작  [0] 돌아가기")
        choice = input_fn("> ").strip()
        if choice == "1":
            actor = input_fn("관찰·기록한 사람 또는 도구: ").strip()
            observation = input_fn("무엇이 실패했나요? 관찰 가능한 출력·오류·측정값: ").strip()
            if actor and observation:
                record = loop.record_evidence(latest.id, actor, observation)
                output(f"Evidence {record.id}을 기록했습니다.")
            else:
                output("Evidence는 기록하지 않았습니다.")
            return
        if choice == "2":
            output("재시도 Decision은 같은 Contract를 왜 다시 실행해도 되는지 남기는 기록입니다. 성공 기준을 바꾸려면 Contract revision이 필요합니다.")
            actor = input_fn("재시도를 결정한 사람 또는 역할: ").strip()
            reason = input_fn("같은 Contract로 재시도하는 이유: ").strip()
            if not actor or not reason:
                output("재시도 Decision은 기록하지 않았습니다.")
                return
            decision = loop.decide_same_contract_retry(goal.id, latest.id, actor, reason)
            output(f"Decision {decision.id}을 기록했습니다. 이 Decision은 다음 Attempt 시작 때 한 번만 소비됩니다.")
            _start_attempt_for_goal(loop, goal, input_fn=input_fn, output=output, retry_decision_id=decision.id)
        return
    evidence = [record for record in loop.evidence.values() if record.attempt_id == latest.id]
    if goal.phase.value == "active":
        output(f"{latest.id}은 {latest.outcome.value}로 완료되었습니다. [1] Evidence 기록  [2] 검증 시작  [0] 돌아가기")
        choice = input_fn("> ").strip()
        if choice == "1":
            actor = input_fn("관찰·기록한 사람 또는 도구: ").strip()
            observation = input_fn("무엇을 확인했나요? 파일·출력·링크·측정값처럼 관찰 가능한 사실: ").strip()
            if not actor or not observation:
                output("Evidence는 기록하지 않았습니다.")
                return
            record = loop.record_evidence(latest.id, actor, observation)
            output(f"Evidence {record.id}을 기록했습니다. 필요하면 Evidence를 더 추가할 수 있습니다.")
        elif choice == "2":
            if latest.outcome is not AttemptOutcome.SUCCEEDED:
                output("실패한 Attempt는 검증을 시작할 수 없습니다. 재시도 또는 Contract 변경이 필요합니다.")
                return
            if not evidence:
                output("먼저 Evidence를 하나 이상 기록하세요. 실행 완료 주장은 검증 근거가 아닙니다.")
                return
            loop.begin_verification(goal.id, latest.id)
            output("검증을 시작했습니다. 다음에는 Evidence를 인용해 Verdict를 제출하세요.")
        return
    if goal.phase.value == "verifying":
        evidence_ids = [record.id for record in evidence]
        prior_verdicts = [verdict for verdict in loop.verdicts.values() if verdict.attempt_id == latest.id]
        prior = prior_verdicts[-1] if prior_verdicts else None
        output(f"검증 중인 Attempt: {latest.id}; 사용할 수 있는 Evidence: {', '.join(evidence_ids) or '없음'}")
        if prior is not None and prior.result is VerdictResult.INCONCLUSIVE:
            output(f"이전 Verdict {prior.id}은 inconclusive입니다. 자동 재시도하지 않습니다.")
            output("[1] 같은 Attempt에 새 Evidence 추가  [2] 새 Evidence로 이전 Verdict를 supersede하여 재판정  [3] 새 실행 필요 Decision 후 Attempt 시작  [0] 돌아가기")
        else:
            output("[1] Evidence 추가  [2] Verdict 제출  [0] 돌아가기")
        choice = input_fn("> ").strip()
        if choice == "1":
            actor = input_fn("관찰·기록한 사람 또는 도구: ").strip()
            observation = input_fn("추가로 확인한 사실: ").strip()
            if actor and observation:
                record = loop.record_evidence(latest.id, actor, observation)
                output(f"Evidence {record.id}을 기록했습니다.")
            else:
                output("Evidence는 기록하지 않았습니다.")
            return
        if prior is not None and prior.result is VerdictResult.INCONCLUSIVE and choice == "3":
            output("새 실행은 같은 Attempt의 Evidence 추가로 해결할 수 없을 때만 선택하세요.")
            actor = input_fn("새 실행을 결정한 사람 또는 역할: ").strip()
            reason = input_fn("새 실행이 필요한 이유: ").strip()
            if not actor or not reason:
                output("re-verification Decision은 기록하지 않았습니다.")
                return
            decision = loop.decide_reverification_requires_new_attempt(goal.id, actor, reason)
            output(f"Decision {decision.id}을 기록했습니다. 다음 Attempt 시작에서 한 번만 소비됩니다.")
            _start_attempt_for_goal(loop, goal, input_fn=input_fn, output=output, reverification_decision_id=decision.id)
            return
        if choice == "2":
            if not evidence_ids:
                output("Verdict에는 Evidence가 필요합니다.")
                return
            verifier = input_fn("검증자 이름 또는 역할: ").strip()
            output("결론을 고르세요: [1] 기준 충족 (satisfied)  [2] 기준 미충족 (not_satisfied)  [3] 판단 보류 (inconclusive)")
            result = {"1": VerdictResult.SATISFIED, "2": VerdictResult.NOT_SATISFIED, "3": VerdictResult.INCONCLUSIVE}.get(input_fn("> ").strip())
            cited = input_fn(f"인용할 Evidence ID들 (빈 입력 = {', '.join(evidence_ids)}): ").strip()
            selected_ids = tuple(cited.replace(",", " ").split()) if cited else tuple(evidence_ids)
            if not verifier or result is None:
                output("Verdict는 제출하지 않았습니다.")
                return
            reason = input_fn("판단 이유 (완료 기준 항목별 충족 여부, 빈 입력 가능): ").strip() or None
            if prior is not None and prior.result is VerdictResult.INCONCLUSIVE:
                if not set(selected_ids).difference(prior.evidence_ids):
                    output("재판정에는 이전 Verdict에 없던 새 Evidence가 하나 이상 필요합니다.")
                    return
                verdict = loop.submit_verdict(goal.id, latest.id, verifier, result, selected_ids, supersedes_verdict_id=prior.id, reason=reason)
            else:
                verdict = loop.submit_verdict(goal.id, latest.id, verifier, result, selected_ids, reason=reason)
            output(f"Verdict {verdict.id}을 {verdict.result.value}로 기록했습니다.")
        return
    output(f"현재 Goal 단계({goal.phase.value})에는 이 기본 관리 화면에서 실행할 작업이 없습니다. activity와 help를 확인하세요.")


def _manage_advanced_lifecycle(
    loop: PersistentGoalLoop, *, input_fn: Callable[[str], str], output: Callable[[str], None]
) -> None:
    """Human menu for exceptional lifecycle decisions; every mutation is explicit."""
    output("고급 lifecycle 관리 — 정정·review·Aim 종결·admission은 자동 처리하지 않습니다.")
    output("[1] Evidence 정정  [2] Impact Review 종결  [3] Aim 달성 평가  [4] 무효화된 Aim 재개  [5] pause/Hold 상태  [6] Contract revision  [0] 돌아가기")
    choice = input_fn("> ").strip()
    if choice == "1":
        for evidence in loop.evidence.values(): output(f"- {evidence.id}: {evidence.observation}")
        evidence_id = input_fn("정정할 Evidence ID: ").strip(); actor = input_fn("정정 기록자: ").strip(); observation = input_fn("정정 관찰 내용: ").strip()
        if evidence_id and actor and observation:
            correction = loop.correct_evidence(evidence_id, actor, observation)
            output(f"Correction {correction.id}을 기록했습니다. 이제 [2]에서 열린 review를 확인하세요.")
        return
    if choice == "2":
        pending = [review for review in loop.impact_reviews.values() if loop.verdict_validity.get(review.trigger_verdict_id) is VerdictValidity.UNDER_REVIEW]
        if pending:
            for review in pending: output(f"- {review.id}: Verdict {review.trigger_verdict_id}, Aim Evaluation {', '.join(review.affected_aim_evaluation_ids) or '없음'}")
            review = loop.impact_reviews.get(input_fn("종결할 Impact Review ID: ").strip())
            if review is None: output("목록의 Impact Review ID를 입력하세요."); return
            disposition = {"1": ReviewDisposition.REAFFIRM, "2": ReviewDisposition.INVALIDATE}.get(input_fn("[1] reaffirm (근거 유지)  [2] invalidate (근거 무효): ").strip())
            actor = input_fn("결정자: ").strip(); reason = input_fn("결정 이유: ").strip()
            if disposition and actor and reason:
                resolution = loop.resolve_verdict_review(review.id, disposition, actor, reason)
                output(f"Verdict review resolution {resolution.id}을 기록했습니다.")
            return
        evaluation_candidates = []
        for review in loop.impact_reviews.values():
            for evaluation_id in review.affected_aim_evaluation_ids:
                evaluation = loop.aim_evaluations[evaluation_id]
                if (loop.aim_evaluation_validity.get(evaluation_id) is VerdictValidity.UNDER_REVIEW
                        and not any(loop.verdict_validity.get(verdict_id) is VerdictValidity.UNDER_REVIEW for verdict_id in evaluation.verdict_ids)):
                    evaluation_candidates.append((review, evaluation))
        if not evaluation_candidates:
            output("현재 종결할 under_review Verdict 또는 Aim Evaluation이 없습니다."); return
        for review, evaluation in evaluation_candidates: output(f"- review {review.id}: Aim Evaluation {evaluation.id}")
        review_id = input_fn("Impact Review ID: ").strip(); evaluation_id = input_fn("Aim Evaluation ID: ").strip()
        candidate = next(((review, evaluation) for review, evaluation in evaluation_candidates if review.id == review_id and evaluation.id == evaluation_id), None)
        if candidate is None: output("목록에 있는 review와 Aim Evaluation 조합을 입력하세요."); return
        disposition = {"1": ReviewDisposition.REAFFIRM, "2": ReviewDisposition.INVALIDATE}.get(input_fn("[1] reaffirm  [2] invalidate: ").strip())
        actor = input_fn("결정자: ").strip(); reason = input_fn("결정 이유: ").strip()
        if disposition and actor and reason:
            resolution = loop.resolve_aim_evaluation_review(evaluation_id, review_id, disposition, actor, reason)
            output(f"Aim Evaluation review resolution {resolution.id}을 기록했습니다.")
        return
    if choice == "3":
        for aim in loop.aims.values():
            valid = [v.id for v in loop.verdicts.values() if loop.goals[v.goal_id].identity.primary_aim_id == aim.id and v.result is VerdictResult.SATISFIED and loop.verdict_validity.get(v.id) is VerdictValidity.VALID]
            if valid: output(f"- {aim.id}: 사용할 수 있는 valid satisfied Verdict = {', '.join(valid)}")
        aim_id = input_fn("평가할 Aim ID: ").strip(); evaluator = input_fn("평가자: ").strip(); conclusion = input_fn("Aim 전체가 충족됐다는 결론과 이유: ").strip()
        verdict_ids = tuple(input_fn("인용 Verdict ID들(공백 구분): ").split())
        if aim_id and evaluator and conclusion and verdict_ids:
            evaluation = loop.evaluate_aim_as_achieved(aim_id, evaluator, verdict_ids, conclusion)
            output(f"Aim Evaluation {evaluation.id}을 기록했습니다. 이것은 Goal verified와 별도 판단입니다.")
        return
    if choice == "4":
        candidates = [evaluation for evaluation in loop.aim_evaluations.values() if loop.aim_evaluation_validity.get(evaluation.id) is VerdictValidity.INVALIDATED and not loop.is_currently_achieved(evaluation.aim_id)]
        for evaluation in candidates: output(f"- {evaluation.id}: Aim {evaluation.aim_id}")
        evaluation = next((item for item in candidates if item.id == input_fn("무효화된 Aim Evaluation ID: ").strip()), None)
        if evaluation is None: output("재개할 수 있는 invalidated Evaluation이 없습니다."); return
        trigger = next((resolution for resolution in loop.review_resolutions.values() if resolution.subject_kind == "aim_evaluation" and resolution.subject_id == evaluation.id and resolution.disposition is ReviewDisposition.INVALIDATE), None)
        actor = input_fn("재개 결정자: ").strip(); reason = input_fn("Aim을 다시 여는 이유: ").strip()
        if trigger and actor and reason:
            record = loop.reopen_aim_after_closure_invalidation(evaluation.aim_id, evaluation.id, trigger.id, actor, reason)
            output(f"Aim closure invalidation {record.id}을 기록했습니다. Aim은 open 상태입니다.")
        return
    if choice == "5":
        output(json.dumps(_admission_records(loop), ensure_ascii=False, default=str))
        action = input_fn("[1] Aim pause  [2] Aim resume  [3] Goal Hold 추가  [4] Goal Hold 해제  [0] 돌아가기: ").strip()
        target = input_fn("Aim 또는 Goal ID: ").strip()
        if action in {"1", "2"}:
            actor = input_fn("결정자: ").strip(); reason = input_fn("이유: ").strip()
            if target and actor and reason:
                decision = loop.pause_aim(target, actor, reason) if action == "1" else loop.resume_aim(target, actor, reason)
                output(f"Decision {decision.id}을 기록했습니다.")
        elif action == "3" and target: loop.add_goal_hold(target); output("Goal Hold를 기록했습니다.")
        elif action == "4" and target: loop.clear_goal_hold(target); output("Goal Hold를 해제했습니다.")
        return
    if choice == "6":
        for goal in loop.goals.values(): output(f"- {goal.id}: Contract v{goal.current_contract_version}")
        goal_id = input_fn("변경할 Goal ID: ").strip(); scope = input_fn("새 scope: ").strip(); criterion = input_fn("새 success criterion: ").strip(); verification = input_fn("새 verification: ").strip(); actor = input_fn("결정자: ").strip(); reason = input_fn("변경 이유: ").strip()
        if all((goal_id, scope, criterion, verification, actor, reason)):
            contract = loop.revise_contract(goal_id, ContractDraft(scope, criterion, verification), decided_by_actor_id=actor, reason=reason)
            output(f"Contract v{contract.version}을 만들었습니다. 과거 Attempt/Evidence는 이전 version에 남습니다.")


def guided_main(
    database: Path,
    *,
    input_fn: Callable[[str], str] = input,
    output: Callable[[str], None] = print,
) -> None:
    """A small terminal conversation for the first human-facing lifecycle.

    Empty answers always leave existing durable facts unchanged; the user can
    return later and inspect `status`, `activity`, or `guided` again.
    """
    loop = _loop(database)
    output("Goal Loop 대화형 시작")
    output("Aim은 '무엇을 이루고 싶은가'입니다. 지금 할 일 하나가 아니라 원하는 변화나 결과를 적어 주세요.")
    output("처음이라면 메뉴 [7]의 상세 안내를 먼저 읽으세요. 어떤 단계에서든 빈 입력은 안전하게 취소하며 저장된 기록을 지우지 않습니다.")
    while True:
        output("")
        output("[1] 새 Aim 시작  [2] 현재 상태와 다음 단계  [3] 명령 설명  [4] 활동 로그  [5] Aim·Proposal 관리  [6] Goal·Attempt 관리  [7] 처음부터 배우기  [8] 고급 lifecycle 관리  [0] 끝내기")
        choice = input_fn("> ").strip()
        if choice in {"0", "quit", "exit"}:
            output("저장된 내용은 SQLite에 남아 있습니다. 다음에 guided로 다시 이어갈 수 있습니다.")
            return
        if choice in {"2", "status"}:
            output("현재 상태의 권장 다음 행동입니다. 실제로 변경하기 전에는 설명을 읽고 본인이 의도한 변화인지 확인하세요:")
            for action in _next_actions(loop, database):
                output(f"- {action}")
            continue
        if choice in {"3", "help"}:
            command_name = input_fn("알아볼 명령 이름 (전체 목록은 빈 입력, 모든 상세 예시는 all): ").strip()
            _show_help(command_name or None, database, output, all_commands=command_name.lower() == "all")
            continue
        if choice in {"4", "activity"}:
            cards = activity_cards(loop.repository)
            if not cards:
                output("아직 활동 기록이 없습니다.")
            for card in cards:
                output(f"- [{card.recorded_at}] {card.title}: {card.detail}")
            continue
        if choice in {"5", "continue", "resume"}:
            _run_guided_operation("Aim·Proposal 관리", lambda: _manage_aim_and_proposals(loop, input_fn=input_fn, output=output), output)
            continue
        if choice in {"6", "goal", "attempt"}:
            _run_guided_operation("Goal·Attempt 관리", lambda: _manage_goal_work(loop, input_fn=input_fn, output=output), output)
            continue
        if choice in {"7", "guide", "learn"}:
            _show_interactive_guide(database, output)
            continue
        if choice in {"8", "advanced", "review"}:
            _run_guided_operation("고급 lifecycle 관리", lambda: _manage_advanced_lifecycle(loop, input_fn=input_fn, output=output), output)
            continue
        if choice not in {"1", "new"}:
            output("1, 2, 3, 4, 5, 6, 7, 8, 0 중 하나를 입력하세요. 처음이면 7을 입력하세요.")
            continue

        description = input_fn("무엇을 이루고 싶나요? (Aim): ").strip()
        if not description:
            output("Aim을 만들지 않았습니다.")
            continue
        output("새 Aim에는 첫 Proposal이 필요합니다. Proposal은 Aim을 향해 먼저 시도할 한 가지 방향입니다.")
        output("예: '처음 사용자가 막히는 지점을 관찰하고 안내 문구를 개선한다.'")
        while not (direction := input_fn("어떤 일을 먼저 해 볼까요? (Proposal, 필수): ").strip()):
            output("Proposal은 비워 둘 수 없습니다. 방향을 한 문장으로 입력하세요. Aim은 아직 저장되지 않았습니다.")
        aim = loop.create_aim(description)
        output(f"Aim {aim.id}을 저장했습니다: {aim.description}")
        proposal = loop.create_proposal(aim.id, direction)
        output(f"Proposal {proposal.id}을 저장했습니다.")
        try:
            goal = _accept_proposal_interactively(loop, proposal, input_fn=input_fn, output=output)
        except GoalLoopError as error:
            output(f"Goal을 확정하지 않았습니다: {error}")
            continue
        if goal is None:
            continue
        if input_fn("지금 첫 실행 Attempt를 시작할까요? [y/N]: ").strip().lower() not in {"y", "yes", "네"}:
            output(f"다음 제안: {_command_prefix(database)} attempt-start {goal.id} 1 \"실행자\" \"수행할 작업\"")
            continue
        executor = input_fn("실행자 이름 또는 역할: ").strip()
        operation = input_fn("이번 Attempt에서 실제로 할 작업: ").strip()
        if not executor or not operation:
            output("Attempt는 시작하지 않았습니다. Goal은 안전하게 저장되어 있습니다.")
            continue
        try:
            attempt = loop.start_attempt(goal.id, goal.current_contract_version, executor, operation)
        except GoalLoopError as error:
            output(f"Attempt를 시작하지 않았습니다: {error}")
            output("메뉴 [2]에서 차단 원인을 확인하세요.")
            continue
        output(f"Attempt {attempt.id}을 시작했습니다.")
        output("실행을 마친 뒤에는 attempt-complete → evidence-record → verification-begin → verdict-submit 순서로 진행하세요.")
        output(f"자세한 설명: {_command_prefix(database)} help attempt-complete")


def _loop(path: Path) -> PersistentGoalLoop:
    return PersistentGoalLoop(SqliteEventRepository(path))


def _configure_console_encoding() -> None:
    """Match modern PowerShell's UTF-8 console for Korean guidance and logs."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def _positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive number")
    return parsed


def _json_object(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError("must be valid JSON object text") from error
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("must be a JSON object")
    return parsed


AO_KINDS = {AO_SESSION_KIND, AO_CLI_KIND}


def _dispatch_record(loop, database: Path, dispatched, worker: str) -> dict:
    """The JSON a dispatch (or an AO resume delivery) prints; AO results also get the Goal link and ao-import Evidence."""
    imported, ao_link = None, None
    if dispatched is not None and dispatched.request.request_kind in AO_KINDS:
        payload = dispatched.request.payload
        # The link is recorded whether or not AO Evidence can be imported: the Attempt ran this AO Goal either way.
        link = link_goal(loop, dispatched.attempt.goal_id, str(payload["ao_project_id"]), str(payload["ao_goal_id"]), worker,
                         str(payload["ao_web_url"]) if payload.get("ao_web_url") else None)
        ao_link = link.id if link else None
        if payload.get("ao_workspace"):
            imported = _import_ao_evidence(loop, database, dispatched, worker)
    derived = _record_derived_goals(loop, dispatched) if dispatched is not None and dispatched.request.request_kind == AO_CLI_KIND else []
    return {
        "dispatch": None if dispatched is None else {
            "request": dispatched.request.__dict__,
            "execution": dispatched.execution.__dict__,
            "attempt": dispatched.attempt.__dict__,
            "acknowledged_request": dispatched.acknowledged_request.__dict__,
        },
        **({"ao_import": imported} if imported is not None else {}),
        **({"ao_link": ao_link} if dispatched is not None and dispatched.request.request_kind in AO_KINDS else {}),
        **({"ao_derived_proposals": derived} if derived else {}),
    }


def _record_derived_goals(loop, dispatched) -> list[str]:
    """AO restructure S4: Goals AO derived while running (PM-confirmed proposals) become Proposals under the Goal's Aim.

    AO run through ao_cli.v1 forwards them instead of creating AO Goals; the result JSON lists them as derivedGoals.
    A marker per AO session and position keeps a repeated delivery from recording the same proposal twice.
    """
    try:
        result = json.loads(dispatched.execution.stdout or "{}")
    except json.JSONDecodeError:
        return []
    items = result.get("derivedGoals") or []
    if not isinstance(items, list) or not items:
        return []
    goal = loop.goals.get(dispatched.attempt.goal_id)
    if goal is None:
        return []
    aim_id = goal.identity.primary_aim_id
    payload = dispatched.request.payload
    source = f"AO Goal {payload.get('ao_project_id')}/{payload.get('ao_goal_id')}, 세션 {result.get('sessionId')}"
    existing = "\n".join(proposal.direction for proposal in loop.proposals.values())
    created: list[str] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not str(item.get("title") or "").strip():
            continue
        marker = f"[ao-derived:{result.get('sessionId')}:{index}]"
        if marker in existing:
            continue
        lines = [str(item["title"]).strip(), "",
                 f"AO 실행 중 파생된 제안({source}, 관계 {item.get('relationship') or '지정 없음'}, 활동 {item.get('activityKind') or '지정 없음'})."]
        if item.get("description"):
            lines.append(str(item["description"]))
        if item.get("objective"):
            lines += ["", f"목표: {item['objective']}"]
        lines += ["", marker]
        created.append(loop.create_proposal(aim_id, "\n".join(lines), "ao").id)
    return created


def _ao_status(repo, request_id: str | None, database: Path | None = None) -> list[dict]:
    """Read-only: each AO request with its AO Goal status, last AO session, waiting human input, and next commands."""
    rows = []
    for request in repo.runtime_requests():
        if request.request_kind not in AO_KINDS or (request_id and request.request_id != request_id):
            continue
        payload = relocated_payload(request.payload, database)
        row: dict = {"request_id": request.request_id, "request_kind": request.request_kind, "status": request.status,
                     "claimed_by": request.claimed_by, "attempt_id": request.attempt_id,
                     "ao_goal": f"{payload.get('ao_project_id')}/{payload.get('ao_goal_id')}"}
        workspace = payload.get("ao_workspace")
        if workspace:
            project_dir = Path(str(workspace)) / "projects" / str(payload["ao_project_id"])
            try:
                index = json.loads((project_dir / "goals" / "goals.json").read_text(encoding="utf-8"))
                entry = next((g for g in index.get("goals", []) if g.get("id") == payload["ao_goal_id"]), None)
                row["ao_goal_status"] = entry.get("status") if entry else None
            except (OSError, json.JSONDecodeError):
                row["ao_goal_status"] = None
            session_id = latest_session(str(workspace), str(payload["ao_project_id"]), str(payload["ao_goal_id"]))
            row["ao_session"] = session_id
            if session_id:
                session_dir = project_dir / "sessions" / session_id
                try:
                    row["ao_session_status"] = json.loads((session_dir / "session.json").read_text(encoding="utf-8")).get("status")
                except (OSError, json.JSONDecodeError):
                    row["ao_session_status"] = None
                try:
                    pending = json.loads((session_dir / "human" / "pending.json").read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    pending = None
                row["waiting_for_human"] = None if pending is None else {
                    "kind": pending.get("kind"), "question": pending.get("question"), "requested_at": pending.get("requestedAt")}
                if pending is not None and request.request_kind == AO_CLI_KIND:
                    row["next"] = ([f'ao-respond {request.request_id} --approve | --reject | --action <a> [--write-path <glob>] [--feedback "…"]']
                                   if pending.get("kind") == "approval" else [f'ao-respond {request.request_id} --answer "…"'])
                    if row.get("ao_session_status") == "stopped":
                        row["next"].append(f'ao-resume {request.request_id} "{request.claimed_by}"')
                elif request.request_kind == AO_CLI_KIND and request.status == "claimed" and row.get("ao_session_status") == "stopped":
                    row["next"] = [f'ao-resume {request.request_id} "{request.claimed_by}"']
                if request.request_kind == AO_CLI_KIND and request.status == "claimed" and row.get("ao_goal_status") == "awaiting_review":
                    row.setdefault("next", []).append(f'ao-review {request.request_id} "{request.claimed_by}" --verdict accept|reject|concern|fail [--reason "…"]')
        rows.append(row)
    return rows


def _run_worker(loop, repo, database: Path, worker: str, *, once: bool, poll_seconds: float, max_requests: int | None) -> None:
    """Dispatch pending requests one at a time, printing one JSON line per request; never retries a failed one."""
    dispatched_count = 0
    while True:
        pending = repo.pending_runtime_requests()
        for request in pending:
            if max_requests is not None and dispatched_count >= max_requests:
                return
            line: dict = {"worker": worker, "request_id": request.request_id, "request_kind": request.request_kind}
            try:
                dispatched = RuntimeOutboxWorker(loop, worker).dispatch(request.request_id, execute_profile_request)
                line.update(_dispatch_record(loop, database, dispatched, worker))
            except AoRuntimeError as error:
                line["left_claimed"] = f"AO result was not delivered: {error}"
            except (RuntimePayloadError, ValueError, FileNotFoundError) as error:
                line["left_claimed"] = f"Runtime request was not executed: {error}"
            dispatched_count += 1
            _emit(line)
            sys.stdout.flush()
        if once:
            return
        time.sleep(poll_seconds)


def _import_ao_evidence(loop, database: Path, dispatched, actor: str) -> dict:
    """After an AO dispatch, record what AO produced as Evidence on the same Attempt (a failure is reported, not raised).

    The Evidence also carries the Goal Loop side of the run (request, delivery, delivered outcome) so a verifier
    judging from attachments alone can see how the result reached the Attempt.
    """
    request, execution, attempt = dispatched.request, dispatched.execution, dispatched.attempt
    payload = relocated_payload(request.payload, database)
    project_dir = Path(str(payload["ao_workspace"])) / "projects" / str(payload["ao_project_id"])
    runtime_line = (f"runtime-dispatch: 요청 {request.request_id}({request.request_kind})을 worker {actor}가 실행해 "
                    f"{attempt.id}에 {attempt.outcome.value}를 전달했다(delivery {execution.delivery_id}).")
    runtime_record = {
        "request": {"request_id": request.request_id, "request_kind": request.request_kind, "attempt_id": request.attempt_id,
                    "status": dispatched.acknowledged_request.status, "claimed_by": dispatched.acknowledged_request.claimed_by},
        "execution": {"delivery_id": execution.delivery_id, "outcome": execution.outcome.value, "exit_code": execution.exit_code,
                      "stdout": execution.stdout, "timed_out": execution.timed_out},
        "attempt": {"id": attempt.id, "outcome": attempt.outcome.value},
    }
    try:
        snapshot = read_ao_goal(project_dir, str(payload["ao_goal_id"]))
        with tempfile.TemporaryDirectory() as staging:
            runtime_file = Path(staging) / f"runtime-{request.request_id}.json"
            runtime_file.write_text(json.dumps(runtime_record, ensure_ascii=False, indent=2), encoding="utf-8")
            staged = [runtime_file, write_snapshot(snapshot, Path(staging)), *write_extracts(snapshot, Path(staging))]
            for source in snapshot.source_files:
                name = f"goal-{payload['ao_goal_id']}.json" if source.name == "goal.json" else f"session-{source.parent.name}.json"
                staged.append(Path(shutil.copyfile(source, Path(staging) / name)))
            stored = [store_attachment(path, artifacts_dir_for(database)) for path in staged]
        attachments = tuple(EvidenceAttachment(item.name, item.sha256, item.size) for item in stored)
        evidence = loop.record_evidence(request.attempt_id, actor, runtime_line + "\n" + summarize(snapshot), attachments=attachments)
    except (AoImportError, ProjectLocationError, GoalLoopError) as error:
        return {"error": str(error)}
    return {"evidence_id": evidence.id}


def _emit(record) -> None:
    print(json.dumps(record, ensure_ascii=False, sort_keys=True, default=lambda value: getattr(value, "value", getattr(value, "__dict__", str(value)))))


def main(argv: list[str] | None = None) -> None:
    _configure_console_encoding()
    parser = argparse.ArgumentParser(description="Goal Loop persistent SQLite CLI")
    location = parser.add_mutually_exclusive_group()
    location.add_argument(
        "--database", type=Path,
        help="SQLite database path (default: GOAL_LOOP_DATABASE, then the nearest .goal-loop project, then the user-global database)",
    )
    location.add_argument("--workspace", help="desktop workspace name or id; resolves its database from the app's registry")
    location.add_argument("--project", type=Path, help="project folder whose .goal-loop journal to use")
    parser.add_argument("--enforce-authority", action="store_true", help="require an execution-grant for each attempt-start")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("status")
    sub.add_parser("activity")
    sub.add_parser("workspaces", help="List desktop workspaces and their databases without opening any journal")
    project_init = sub.add_parser("project-init", help="Create a .goal-loop project journal in a folder (default: current folder)")
    project_init.add_argument("folder", nargs="?", type=Path, default=Path("."))
    sub.add_parser("where", help="Show which journal is used, its project root, attachment folder, and how it was found")
    sub.add_parser("next", help="Return structured, read-only next actions for an agent or script")
    sub.add_parser("reviews", help="Return read-only under-review Verdicts, Aim Evaluations, and Impact Reviews")
    sub.add_parser("admission", help="Return read-only execution-admission facts and dependency states")
    sub.add_parser("goal-graph", help="Return read-only parent-child and Goal dependency graph links")
    sub.add_parser("ui-projection", help="Return versioned read-only graph data for React, Tauri, or Obsidian shells")
    memory_draft = sub.add_parser("memory-draft", help="Suggest Memory Tool timeline lines for Verdicts and Aim Evaluations the timeline does not cite yet (read-only)")
    memory_draft.add_argument("--record", help="only the draft for this verdict-* or aim-evaluation-* ID")
    memory_draft.add_argument("--limit", type=int, default=10)
    graph_export = sub.add_parser("graph-export", help="Export the read-only Goal graph as a local HTML file")
    graph_export.add_argument("destination", type=Path)
    sub.add_parser("runtime-requests", help="List pending durable Runtime requests without claiming or executing them")
    runtime_request = sub.add_parser("runtime-request", help="Read one Runtime request regardless of its delivery state")
    runtime_request.add_argument("request_id")
    runtime_start = sub.add_parser("runtime-attempt-start", help="Atomically start an Attempt and enqueue, but never execute, one Runtime request")
    runtime_start.add_argument("goal_id"); runtime_start.add_argument("version", type=int); runtime_start.add_argument("executor"); runtime_start.add_argument("operation")
    runtime_start.add_argument("request_kind"); runtime_start.add_argument("request_payload", type=_json_object)
    runtime_start.add_argument("--request-id"); runtime_start.add_argument("--command-id")
    runtime_start.add_argument("--retry-decision"); runtime_start.add_argument("--reverification-decision")
    ao_start = sub.add_parser("ao-attempt-start", help="Start an Attempt whose ao_session.v1 request runs the Goal's Contract as an AO Goal")
    ao_start.add_argument("goal_id"); ao_start.add_argument("version", type=int); ao_start.add_argument("executor"); ao_start.add_argument("operation")
    ao_start.add_argument("--ao-project-id", required=True); ao_start.add_argument("--ao-goal-id", required=True)
    ao_start.add_argument("--ao-url"); ao_start.add_argument("--ao-workspace", help="AO workspace folder; enables ao-import after dispatch")
    ao_start.add_argument("--ao-web-url", help="AO web address for the Goal link (default: an ao:/projects/<project>/goals/<id> location without a web address)")
    ao_start.add_argument("--milestone-id"); ao_start.add_argument("--activity-kind")
    ao_start.add_argument("--objective", help="AO objective when the success criterion also holds Goal Loop-only items (default: the success criterion)")
    ao_start.add_argument("--allowed-write-path", action="append", default=[], dest="allowed_write_paths")
    ao_start.add_argument("--timeout-seconds", type=float); ao_start.add_argument("--poll-seconds", type=float)
    ao_start.add_argument("--request-id"); ao_start.add_argument("--retry-decision"); ao_start.add_argument("--reverification-decision")
    ao_start.add_argument("--ao-root", help="AO checkout to run through its CLI without the AO server (ao_cli.v1); requires --ao-workspace")
    ao_start.add_argument("--approval", choices=["stop", "wait"], help="ao_cli.v1: stop leaves a human request and stops the AO session (default); wait keeps it waiting")
    ao_start.add_argument("--ao-checklist", action="append", default=[], help="ao_cli.v1: checklist item for the AO Goal; repeatable")
    ao_start.add_argument("--ao-verifier", action="append", default=[], type=_json_object, help="ao_cli.v1: AO verifier object as JSON, e.g. {\"type\": \"human\", \"prompt\": \"…\"}; repeatable")
    ao_respond = sub.add_parser("ao-respond", help="Answer the human input an ao_cli.v1 request's AO session is waiting for")
    ao_respond.add_argument("request_id")
    answer = ao_respond.add_mutually_exclusive_group(required=True)
    answer.add_argument("--approve", action="store_true"); answer.add_argument("--reject", action="store_true")
    answer.add_argument("--action", help="AO gate action: retry | bypass_gate | mark_failed | expand_scope | escalate_scope | escalate_to_pm")
    answer.add_argument("--answer", help="answer a meeting or user-query turn")
    ao_respond.add_argument("--feedback"); ao_respond.add_argument("--additional-retries")
    ao_respond.add_argument("--write-path", action="append", default=[], help="expand_scope/escalate_scope: write path glob to add; repeatable")
    ao_resume = sub.add_parser("ao-resume", help="Resume a stopped ao_cli.v1 AO session and deliver its result to the claimed request")
    ao_resume.add_argument("request_id"); ao_resume.add_argument("worker")
    ao_review = sub.add_parser("ao-review", help="Resolve an awaiting_review AO Goal of an ao_cli.v1 request and deliver the result when it completes")
    ao_review.add_argument("request_id"); ao_review.add_argument("worker")
    ao_review.add_argument("--verdict", required=True, choices=["accept", "reject", "concern", "fail"])
    ao_review.add_argument("--reason")
    ao_status = sub.add_parser("ao-status", help="Read-only state of AO requests: AO Goal, last AO session, waiting human input, next commands")
    ao_status.add_argument("--request-id")
    runtime_worker = sub.add_parser("runtime-worker", help="Dispatch pending Runtime requests one by one; leave AO requests that wait for a person claimed")
    runtime_worker.add_argument("worker")
    runtime_worker.add_argument("--once", action="store_true", help="one pass over the pending requests, then exit")
    runtime_worker.add_argument("--poll-seconds", type=float, default=30.0)
    runtime_worker.add_argument("--max-requests", type=int, help="stop after dispatching this many requests")
    runtime_claim = sub.add_parser("runtime-claim", help="Explicitly claim one pending Runtime request for a worker")
    runtime_claim.add_argument("request_id"); runtime_claim.add_argument("worker")
    runtime_ack = sub.add_parser("runtime-ack", help="Acknowledge a Runtime request claimed by this worker")
    runtime_ack.add_argument("request_id"); runtime_ack.add_argument("worker")
    runtime_requeue = sub.add_parser("runtime-requeue", help="Return a worker-owned Runtime claim to pending")
    runtime_requeue.add_argument("request_id"); runtime_requeue.add_argument("worker")
    runtime_dispatch = sub.add_parser("runtime-dispatch", help="Explicitly dispatch one supported Runtime payload without automatic retry")
    runtime_dispatch.add_argument("request_id"); runtime_dispatch.add_argument("worker")
    runtime_deliver = sub.add_parser("runtime-deliver", help="Recover a worker-owned claim by delivering an observed Runtime result without executing a process")
    runtime_deliver.add_argument("request_id"); runtime_deliver.add_argument("worker")
    runtime_deliver.add_argument("outcome", choices=[x.value for x in (AttemptOutcome.SUCCEEDED, AttemptOutcome.FAILED)])
    runtime_deliver.add_argument("delivery_id")
    sub.add_parser("guide", help="Print a detailed self-service Korean CLI guide without changing data")
    aim_evaluate = sub.add_parser("aim-evaluate", help="Explicitly evaluate an Aim as achieved from valid satisfied Verdicts and/or valid sub-Aim evaluations")
    aim_evaluate.add_argument("aim_id"); aim_evaluate.add_argument("evaluator"); aim_evaluate.add_argument("conclusion")
    aim_evaluate.add_argument("verdict_ids", nargs="+", help="verdict-* and aim-evaluation-* (direct sub-Aims) to cite")
    guided = sub.add_parser("guided", help="Ask questions and create an Aim-led workflow")
    guided.add_argument("--no-prompt", action="store_true", help=argparse.SUPPRESS)
    help_parser = sub.add_parser("help", help="Explain a persistent command in Korean")
    help_parser.add_argument("command_name", nargs="?")
    help_parser.add_argument("--all", action="store_true", help="show detailed examples for every command")
    backup = sub.add_parser("backup"); backup.add_argument("destination", type=Path)
    sub.add_parser("doctor")
    aim = sub.add_parser("aim-create"); aim.add_argument("description")
    aim.add_argument("--parent", help="parent Aim id; the new Aim becomes its sub-Aim")
    aim_parent = sub.add_parser("aim-parent-add", help="Place an existing Aim under an open parent Aim once")
    aim_parent.add_argument("child_aim_id"); aim_parent.add_argument("parent_aim_id")
    proposal = sub.add_parser("proposal-create"); proposal.add_argument("aim_id"); proposal.add_argument("direction")
    proposal.add_argument("--actor", default=None, help="Who proposed this direction (person or agent)")
    close_proposal = sub.add_parser("proposal-close", help="Close an open Proposal without accepting it")
    close_proposal.add_argument("proposal_id"); close_proposal.add_argument("--kind", required=True, choices=list(PROPOSAL_CLOSURE_KINDS))
    close_proposal.add_argument("--actor", required=True); close_proposal.add_argument("--reason", required=True)
    stop_goal = sub.add_parser("goal-stop", help="Stop pursuing a Goal")
    stop_goal.add_argument("goal_id"); stop_goal.add_argument("--actor", required=True); stop_goal.add_argument("--reason", required=True)
    abandon_aim = sub.add_parser("aim-abandon", help="Give up an open Aim")
    abandon_aim.add_argument("aim_id"); abandon_aim.add_argument("--actor", required=True); abandon_aim.add_argument("--reason", required=True)
    revise_proposal = sub.add_parser("proposal-revise")
    revise_proposal.add_argument("proposal_id"); revise_proposal.add_argument("direction")
    revise_proposal.add_argument("--actor", required=True); revise_proposal.add_argument("--reason", required=True)
    accept = sub.add_parser("goal-accept")
    accept.add_argument("proposal_id"); accept.add_argument("--target", required=True); accept.add_argument("--responsibility", required=True)
    accept.add_argument("--scope", required=True); accept.add_argument("--criterion", required=True); accept.add_argument("--verification", required=True); accept.add_argument("--key", required=True)
    revise_contract = sub.add_parser("contract-revise", help="Create the next immutable version for an active or committed Goal")
    revise_contract.add_argument("goal_id"); revise_contract.add_argument("--scope", required=True); revise_contract.add_argument("--criterion", required=True); revise_contract.add_argument("--verification", required=True)
    revise_contract.add_argument("--actor", required=True); revise_contract.add_argument("--reason", required=True)
    start = sub.add_parser("attempt-start"); start.add_argument("goal_id"); start.add_argument("version", type=int); start.add_argument("executor"); start.add_argument("operation")
    start.add_argument("--retry-decision"); start.add_argument("--reverification-decision")
    retry = sub.add_parser("retry-decide", help="Record the required one-use Decision before retrying a failed Attempt")
    retry.add_argument("goal_id"); retry.add_argument("failed_attempt_id"); retry.add_argument("actor"); retry.add_argument("reason")
    reverification = sub.add_parser("reverification-decide", help="Record the required one-use Decision before a new Attempt after an inconclusive Verdict")
    reverification.add_argument("goal_id"); reverification.add_argument("actor"); reverification.add_argument("reason")
    complete = sub.add_parser("attempt-complete"); complete.add_argument("attempt_id"); complete.add_argument("outcome", choices=[x.value for x in (AttemptOutcome.SUCCEEDED, AttemptOutcome.FAILED)])
    evidence = sub.add_parser("evidence-record"); evidence.add_argument("attempt_id"); evidence.add_argument("actor"); evidence.add_argument("observation")
    evidence.add_argument("--attach", action="append", type=Path, default=[], help="copy a file (≤10MB) as observed material; repeatable")
    ao_import = sub.add_parser("ao-import", help="Record one AI_ORCHESTRATION Goal's results as Evidence on a completed Attempt (AO is read only)")
    ao_import.add_argument("attempt_id"); ao_import.add_argument("actor")
    ao_import.add_argument("ao_project", type=Path, help="AO project folder: <ao-workspace>/projects/<project-id>")
    ao_import.add_argument("ao_goal_id", help="AO GoalEntry id (not a goal-loop Goal id)")
    ao_import.add_argument("--ao-web-url", help="AO web address for the Goal link (default: an ao:/projects/<project>/goals/<id> location without a web address)")
    ao_links_parser = sub.add_parser("ao-links", help="List Goal Loop Goal <-> AO Goal links (references written by ao-import and AO dispatch)")
    ao_links_parser.add_argument("--goal"); ao_links_parser.add_argument("--ao-goal", help="<project>/<ao-goal-id>")
    ao_links_parser.add_argument("--backfill", action="store_true", help="add links missing for existing AO requests and AO Evidence")
    ao_links_parser.add_argument("--actor", help="who records backfilled links (required with --backfill)")
    ao_links_parser.add_argument("--ao-web-url")
    ao_sync_parser = sub.add_parser("ao-sync", help="Record AO-side changes (reverted/abandoned AO Goals, invalidated assumptions) as Evidence corrections")
    ao_sync_parser.add_argument("--ao-workspace", type=Path, required=True); ao_sync_parser.add_argument("--actor", required=True)
    ao_sync_parser.add_argument("--dry-run", action="store_true")
    reference_add = sub.add_parser("reference-add", help="Point an Aim, Proposal, or Goal at a living document (context, never a criterion)")
    reference_add.add_argument("subject_id", help="aim-*, proposal-*, or goal-*"); reference_add.add_argument("location", help="URL or file path")
    reference_add.add_argument("--label"); reference_add.add_argument("--actor", required=True)
    reference_remove = sub.add_parser("reference-remove", help="Remove a reference; it stays in history")
    reference_remove.add_argument("reference_id"); reference_remove.add_argument("--actor", required=True); reference_remove.add_argument("--reason")
    correction = sub.add_parser("evidence-correct", help="Append a correction for existing Evidence and open its impact reviews")
    correction.add_argument("evidence_id"); correction.add_argument("actor"); correction.add_argument("observation")
    begin = sub.add_parser("verification-begin"); begin.add_argument("goal_id"); begin.add_argument("attempt_id")
    verdict = sub.add_parser("verdict-submit"); verdict.add_argument("goal_id"); verdict.add_argument("attempt_id"); verdict.add_argument("verifier"); verdict.add_argument("result", choices=[x.value for x in VerdictResult]); verdict.add_argument("evidence_ids", nargs="+"); verdict.add_argument("--supersedes-verdict")
    verdict.add_argument("--reason", help="why: which numbered criteria were met or not, and on what basis")
    verdict_review = sub.add_parser("verdict-review-resolve", help="Resolve one Impact Review for an under-review Verdict")
    verdict_review.add_argument("impact_review_id"); verdict_review.add_argument("disposition", choices=[x.value for x in ReviewDisposition]); verdict_review.add_argument("actor"); verdict_review.add_argument("reason")
    evaluation_review = sub.add_parser("aim-evaluation-review-resolve", help="Resolve one Impact Review for an under-review Aim Evaluation")
    evaluation_review.add_argument("evaluation_id"); evaluation_review.add_argument("impact_review_id"); evaluation_review.add_argument("disposition", choices=[x.value for x in ReviewDisposition]); evaluation_review.add_argument("actor"); evaluation_review.add_argument("reason")
    closure = sub.add_parser("aim-closure-invalidate", help="Explicitly reopen an Aim after its closure Evaluation was invalidated")
    closure.add_argument("aim_id"); closure.add_argument("evaluation_id"); closure.add_argument("triggering_record_id"); closure.add_argument("actor"); closure.add_argument("reason")
    pause = sub.add_parser("aim-pause", help="Block new Attempts under an Aim without changing its phase")
    pause.add_argument("aim_id"); pause.add_argument("actor"); pause.add_argument("reason")
    resume = sub.add_parser("aim-resume", help="Allow new Attempts under a paused Aim without starting one")
    resume.add_argument("aim_id"); resume.add_argument("actor"); resume.add_argument("reason")
    hold_add = sub.add_parser("goal-hold-add", help="Block new Attempts for a Goal")
    hold_add.add_argument("goal_id"); hold_add.add_argument("--actor", required=True); hold_add.add_argument("--reason", required=True)
    hold_clear = sub.add_parser("goal-hold-clear", help="Clear a Goal execution Hold")
    hold_clear.add_argument("goal_id")
    grant = sub.add_parser("execution-grant", help="Grant execute authority for a Goal when --enforce-authority is used")
    grant.add_argument("goal_id"); grant.add_argument("actor")
    revoke = sub.add_parser("execution-revoke", help="Revoke execute authority for a Goal")
    revoke.add_argument("goal_id"); revoke.add_argument("actor")
    dependency = sub.add_parser("verdict-dependency-add", help="Require a valid Verdict result before a Goal can start an Attempt")
    dependency.add_argument("goal_id"); dependency.add_argument("verdict_id")
    dependency.add_argument("--required-result", choices=[x.value for x in VerdictResult], default=VerdictResult.SATISFIED.value)
    parent_link = sub.add_parser("goal-parent-add", help="Append one immutable same-Aim parent-child Goal link")
    parent_link.add_argument("child_goal_id"); parent_link.add_argument("parent_goal_id")
    goal_dependency = sub.add_parser("goal-dependency-add", help="Require a source Goal valid satisfied Verdict before target Attempt start")
    goal_dependency.add_argument("dependent_goal_id"); goal_dependency.add_argument("required_goal_id")
    codex_run = sub.add_parser("codex-run", help="Explicitly run Codex for an existing running Attempt")
    codex_run.add_argument("attempt_id"); codex_run.add_argument("prompt")
    codex_run.add_argument("--workdir", type=Path, required=True)
    codex_run.add_argument("--sandbox", choices=["read-only", "workspace-write"], default="read-only")
    codex_run.add_argument("--timeout", type=float, default=300.0)
    codex_run.add_argument("--delivery-id")
    codex_run.add_argument("--executable", default="codex")
    codex_run.add_argument("--extra-arg", action="append", default=[])
    claude_run = sub.add_parser("claude-run", help="Run Claude Code in restricted read-only mode for an existing running Attempt")
    claude_run.add_argument("attempt_id"); claude_run.add_argument("prompt")
    claude_run.add_argument("--workdir", type=Path, required=True)
    claude_run.add_argument("--timeout", type=float, default=300.0)
    claude_run.add_argument("--delivery-id")
    claude_run.add_argument("--executable", default="claude")
    claude_run.add_argument("--model", required=True, help="Explicit Claude model alias or ID; no default model is selected")
    claude_run.add_argument("--max-turns", type=_positive_int, required=True, help="Positive cap on Claude agentic turns")
    claude_run.add_argument("--max-budget-usd", type=_positive_float, required=True, help="Positive per-run Claude API cost cap in USD")
    args = parser.parse_args(argv)
    if args.command == "workspaces":
        _emit({"registry": str(workspace_registry_path()), "workspaces": workspace_records()}); return
    if args.command == "project-init":
        try:
            database, created = init_project(args.folder)
        except ProjectLocationError as error:
            parser.error(str(error))
        repo = SqliteEventRepository(database)
        _emit({"project_root": str(project_root_for(database)), "database": str(database), "created": created, "schema_version": repo.schema_version()}); return
    try:
        args.database, database_source = resolve_database(args.database, args.workspace, args.project, Path.cwd())
    except ValueError as error:
        parser.error(str(error))
    if args.command == "where":
        root = project_root_for(args.database)
        # Read-only: reports the choice without creating the journal.
        _emit({"database": str(args.database), "source": database_source, "exists": args.database.is_file(),
               "project_root": str(root) if root else None, "artifacts": str(artifacts_dir_for(args.database))}); return
    if args.command == "help":
        _show_help(args.command_name, args.database, all_commands=args.all)
        return
    if args.command == "guide":
        _show_guide(args.database)
        return
    repo = SqliteEventRepository(args.database)
    if args.command == "guided":
        if args.no_prompt:
            _show_help("guided", args.database)
            return
        guided_main(args.database)
        return
    if args.command == "init": _emit({"database": str(args.database), "schema_version": repo.schema_version()}); return
    if args.command == "activity":
        for card in activity_cards(repo): _emit(card.__dict__)
        return
    if args.command == "doctor": _emit({"integrity": repo.integrity_diagnostics(), "checkpoint_current": repo.projection_checkpoint_is_current(), "schema_version": repo.schema_version()}); return
    if args.command == "backup":
        repo.backup_to(args.destination)
        artifacts = artifacts_dir_for(args.database)
        copied = None
        if artifacts.is_dir():
            # Copy to where the backup journal itself looks for attachments, so opening it shows them preserved.
            copied = artifacts_dir_for(args.destination)
            shutil.copytree(artifacts, copied, dirs_exist_ok=True)
        _emit({"backup": str(args.destination), "artifacts": str(copied) if copied else None}); return
    if args.command == "runtime-requests": _emit({"database": str(args.database), "requests": [request.__dict__ for request in repo.pending_runtime_requests()]}); return
    if args.command == "runtime-request":
        request = repo.runtime_request(args.request_id)
        _emit({"database": str(args.database), "request": request.__dict__ if request else None}); return
    if args.command == "runtime-claim":
        request = repo.claim_runtime_request(args.request_id, args.worker)
        _emit({"claimed": request.__dict__ if request else None}); return
    if args.command == "runtime-ack": _emit(repo.acknowledge_runtime_request(args.request_id, args.worker).__dict__); return
    if args.command == "runtime-requeue": _emit(repo.requeue_runtime_request(args.request_id, args.worker).__dict__); return
    loop = PersistentGoalLoop(repo, enforce_authority=args.enforce_authority)
    if args.command == "status": _emit({"aims": list(loop.aims), "goals": list(loop.goals), "attempts": list(loop.attempts), "checkpoint": repo.projection_checkpoint()}); return
    if args.command == "next": _emit({"database": str(args.database), "actions": _next_action_records(loop, args.database)}); return
    if args.command == "reviews": _emit({"database": str(args.database), **_review_records(loop)}); return
    if args.command == "admission": _emit({"database": str(args.database), "enforce_authority": args.enforce_authority, **_admission_records(loop)}); return
    if args.command == "goal-graph": _emit({"database": str(args.database), "parents": loop.goal_parents, "dependencies": {goal_id: [item.required_goal_id for item in records] for goal_id, records in loop.goal_dependencies.items()}}); return
    if args.command == "memory-draft":
        projection = goal_graph_projection(loop, project_root=project_root_for(args.database), artifacts_dir=None)
        found = [item for item in projection["memory_drafts"] if not args.record or item["record_id"] == args.record]
        _emit({"memory_drafts": found if args.record else found[: max(args.limit, 0)], "total": len(projection["memory_drafts"]),
               "memory": memory_view.memory_root(project_root_for(args.database)) is not None}); return
    if args.command == "ui-projection": _emit(goal_graph_projection(loop, project_root=project_root_for(args.database), artifacts_dir=artifacts_dir_for(args.database))); return
    if args.command == "graph-export": export_goal_graph(loop, args.destination); _emit({"graph_export": str(args.destination)}); return
    if args.command == "aim-evaluate":
        cited_evaluations = tuple(item for item in args.verdict_ids if item.startswith("aim-evaluation-"))
        cited_verdicts = tuple(item for item in args.verdict_ids if not item.startswith("aim-evaluation-"))
        _emit(loop.evaluate_aim_as_achieved(args.aim_id, args.evaluator, cited_verdicts, args.conclusion, cited_evaluations).__dict__); return
    if args.command == "aim-create": _emit(loop.create_aim(args.description, args.parent).__dict__); return
    if args.command == "aim-parent-add": _emit(loop.link_aim_parent(args.child_aim_id, args.parent_aim_id).__dict__); return
    if args.command == "proposal-create": _emit(loop.create_proposal(args.aim_id, args.direction, args.actor).__dict__); return
    if args.command == "proposal-close": _emit(loop.close_proposal(args.proposal_id, args.kind, args.actor, args.reason).__dict__); return
    if args.command == "goal-stop": goal = loop.stop_goal(args.goal_id, args.actor, args.reason); _emit({"id": goal.id, "phase": goal.phase.value, "stopped_by_actor_id": goal.stopped_by_actor_id, "stop_reason": goal.stop_reason}); return
    if args.command == "aim-abandon": _emit(loop.abandon_aim(args.aim_id, args.actor, args.reason).__dict__); return
    if args.command == "proposal-revise": _emit(loop.revise_proposal(args.proposal_id, args.direction, args.actor, args.reason).__dict__); return
    if args.command == "goal-accept":
        proposal = loop.proposals[args.proposal_id]
        _emit(loop.accept_proposal(args.proposal_id, GoalIdentity(proposal.aim_id, args.target, args.responsibility), ContractDraft(args.scope, args.criterion, args.verification), idempotency_key=args.key).__dict__); return
    if args.command == "contract-revise": _emit(loop.revise_contract(args.goal_id, ContractDraft(args.scope, args.criterion, args.verification), decided_by_actor_id=args.actor, reason=args.reason).__dict__); return
    if args.command == "attempt-start": _emit(loop.start_attempt(args.goal_id, args.version, args.executor, args.operation, retry_decision_id=args.retry_decision, reverification_decision_id=args.reverification_decision).__dict__); return
    if args.command == "runtime-attempt-start":
        attempt = loop.start_runtime_attempt(
            args.goal_id, args.version, args.executor, args.operation,
            request_kind=args.request_kind, request_payload=args.request_payload,
            request_id=args.request_id, retry_decision_id=args.retry_decision,
            reverification_decision_id=args.reverification_decision,
            command_id=args.command_id,
        )
        request = next(item for item in repo.pending_runtime_requests() if item.attempt_id == attempt.id)
        _emit({"attempt": attempt.__dict__, "runtime_request": request.__dict__}); return
    if args.command == "ao-attempt-start":
        goal = loop.goals.get(args.goal_id)
        if goal is None:
            parser.error(f"과제를 찾을 수 없습니다: {args.goal_id}")
        contract = loop.contracts[(goal.id, goal.current_contract_version)]
        goal_view = {"id": goal.id, "target": goal.identity.target_and_desired_result, "current_contract_version": goal.current_contract_version, "contract": contract.__dict__}
        if args.ao_root:
            if not args.ao_workspace:
                parser.error("--ao-root(ao_cli.v1)에는 --ao-workspace가 필요합니다.")
            try:
                payload = cli_contract_payload(
                    goal_view, ao_root=args.ao_root, ao_workspace=args.ao_workspace, ao_project_id=args.ao_project_id, ao_goal_id=args.ao_goal_id,
                    ao_web_url=args.ao_web_url, milestone_id=args.milestone_id, activity_kind=args.activity_kind,
                    allowed_write_paths=args.allowed_write_paths, timeout_seconds=args.timeout_seconds, approval=args.approval, objective=args.objective,
                    checklist=args.ao_checklist, verifiers=args.ao_verifier,
                )
            except ValueError as error:
                parser.error(str(error))
            attempt = loop.start_runtime_attempt(
                args.goal_id, args.version, args.executor, args.operation, request_kind=AO_CLI_KIND, request_payload=payload,
                request_id=args.request_id, retry_decision_id=args.retry_decision, reverification_decision_id=args.reverification_decision,
            )
            request = next(item for item in repo.pending_runtime_requests() if item.attempt_id == attempt.id)
            _emit({"attempt": attempt.__dict__, "runtime_request": request.__dict__}); return
        try:
            payload = contract_payload(
                {"id": goal.id, "target": goal.identity.target_and_desired_result, "current_contract_version": goal.current_contract_version, "contract": contract.__dict__},
                ao_project_id=args.ao_project_id, ao_goal_id=args.ao_goal_id, ao_url=args.ao_url, ao_workspace=args.ao_workspace, ao_web_url=args.ao_web_url,
                milestone_id=args.milestone_id, activity_kind=args.activity_kind, allowed_write_paths=args.allowed_write_paths,
                timeout_seconds=args.timeout_seconds, poll_seconds=args.poll_seconds, objective=args.objective,
            )
        except ValueError as error:
            parser.error(str(error))
        attempt = loop.start_runtime_attempt(
            args.goal_id, args.version, args.executor, args.operation, request_kind=AO_SESSION_KIND, request_payload=payload,
            request_id=args.request_id, retry_decision_id=args.retry_decision, reverification_decision_id=args.reverification_decision,
        )
        request = next(item for item in repo.pending_runtime_requests() if item.attempt_id == attempt.id)
        _emit({"attempt": attempt.__dict__, "runtime_request": request.__dict__}); return
    if args.command == "runtime-dispatch":
        try:
            dispatched = RuntimeOutboxWorker(loop, args.worker).dispatch(
                args.request_id, execute_profile_request
            )
        except AoRuntimeError as error:
            parser.error(f"AO result was not delivered: {error} The request remains claimed; resolve it in AO, then runtime-deliver or runtime-requeue.")
        except RuntimePayloadError as error:
            parser.error(f"Runtime payload was not executed: {error}. The request remains claimed.")
        except ValueError as error:
            parser.error(f"Runtime profile refused execution: {error}. The request remains claimed.")
        except FileNotFoundError as error:
            parser.error(
                f"Runtime executable was not found: {error.filename or error}. "
                "The request remains claimed; inspect it, then ack or requeue explicitly."
            )
        _emit(_dispatch_record(loop, args.database, dispatched, args.worker)); return
    if args.command == "ao-respond":
        request = repo.runtime_request(args.request_id)
        if request is None or request.request_kind != AO_CLI_KIND:
            parser.error(f"ao_cli.v1 요청이 아닙니다: {args.request_id}")
        payload = AoCliPayload.parse(relocated_payload(request.payload, args.database))
        session_id = latest_session(payload.ao_workspace, payload.ao_project_id, payload.ao_goal_id)
        if session_id is None:
            parser.error(f"AO Goal {payload.ao_project_id}/{payload.ao_goal_id}의 세션을 찾지 못했습니다.")
        if args.answer is not None:
            answer_args = ["--answer", args.answer]
        else:
            answer_args = ["--approve"] if args.approve else ["--reject"] if args.reject else ["--action", args.action]
            if args.feedback:
                answer_args += ["--feedback", args.feedback]
            if args.additional_retries:
                answer_args += ["--additional-retries", args.additional_retries]
            for path in args.write_path:
                answer_args += ["--write-path", path]
        try:
            answered = AoCli(payload).respond(session_id, answer_args)
        except AoRuntimeError as error:
            parser.error(str(error))
        _emit({"request_id": request.request_id, "ao_session": session_id, "answered": answered,
               "next": f'ao-resume {request.request_id} "{request.claimed_by}"' if answered.get("resumeNeeded") else None}); return
    if args.command == "ao-resume":
        request = repo.runtime_request(args.request_id)
        if request is None or request.request_kind != AO_CLI_KIND:
            parser.error(f"ao_cli.v1 요청이 아닙니다: {args.request_id}")
        if request.status != "claimed" or request.claimed_by != args.worker:
            parser.error(f"요청 {args.request_id}은 worker {args.worker}가 claim한 상태가 아닙니다(status {request.status}, claimed_by {request.claimed_by}).")
        payload = AoCliPayload.parse(relocated_payload(request.payload, args.database))
        session_id = latest_session(payload.ao_workspace, payload.ao_project_id, payload.ao_goal_id)
        if session_id is None:
            parser.error(f"AO Goal {payload.ao_project_id}/{payload.ao_goal_id}의 세션을 찾지 못했습니다.")
        try:
            execution = resume_ao_cli(payload, session_id, request_label=request.request_id)
            dispatched = RuntimeOutboxWorker(loop, args.worker).deliver(request.request_id, execution)
        except AoRuntimeError as error:
            parser.error(f"AO result was not delivered: {error} The request remains claimed.")
        except ValueError as error:
            parser.error(f"Runtime result was not delivered: {error}")
        _emit(_dispatch_record(loop, args.database, dispatched, args.worker)); return
    if args.command == "ao-review":
        request = repo.runtime_request(args.request_id)
        if request is None or request.request_kind != AO_CLI_KIND:
            parser.error(f"ao_cli.v1 요청이 아닙니다: {args.request_id}")
        if request.status != "claimed" or request.claimed_by != args.worker:
            parser.error(f"요청 {args.request_id}은 worker {args.worker}가 claim한 상태가 아닙니다(status {request.status}, claimed_by {request.claimed_by}).")
        if args.verdict == "fail" and not (args.reason or "").strip():
            parser.error("--verdict fail에는 --reason이 필요합니다(Goal을 실패로 끝내는 이유).")
        payload = AoCliPayload.parse(relocated_payload(request.payload, args.database))
        try:
            resolved = AoCli(payload).resolve_review(args.verdict, args.reason)
        except AoRuntimeError as error:
            parser.error(str(error))
        failed = resolved.get("goalStatus") == "failed"
        if not resolved.get("completed") and not failed:
            _emit({"request_id": request.request_id, "ao_review": resolved, "delivered": False,
                   "note": "AO Goal이 아직 완료되지 않아 결과를 전달하지 않았다. 요청은 claimed로 남는다."}); return
        session_id = latest_session(payload.ao_workspace, payload.ao_project_id, payload.ao_goal_id) or "unknown"
        execution = RuntimeExecution(f"ao-cli:{session_id}:review-{args.verdict}",
                                     AttemptOutcome.FAILED if failed else AttemptOutcome.SUCCEEDED, 1 if failed else 0,
                                     json.dumps({"ao_review": resolved, "sessionId": session_id}, ensure_ascii=False), "", False)
        try:
            dispatched = RuntimeOutboxWorker(loop, args.worker).deliver(request.request_id, execution)
        except ValueError as error:
            parser.error(f"Runtime result was not delivered: {error}")
        _emit({**_dispatch_record(loop, args.database, dispatched, args.worker), "ao_review": resolved}); return
    if args.command == "ao-status":
        _emit({"ao_requests": _ao_status(repo, args.request_id, args.database)}); return
    if args.command == "runtime-worker":
        _run_worker(loop, repo, args.database, args.worker, once=args.once, poll_seconds=args.poll_seconds, max_requests=args.max_requests)
        return
    if args.command == "runtime-deliver":
        execution = RuntimeExecution(
            args.delivery_id, AttemptOutcome(args.outcome), None, "", "", False
        )
        try:
            delivered = RuntimeOutboxWorker(loop, args.worker).deliver(args.request_id, execution)
        except ValueError as error:
            parser.error(f"Runtime result was not delivered: {error}")
        _emit({
            "request": delivered.request.__dict__, "execution": delivered.execution.__dict__,
            "attempt": delivered.attempt.__dict__,
            "acknowledged_request": delivered.acknowledged_request.__dict__,
        }); return
    if args.command == "retry-decide": _emit(loop.decide_same_contract_retry(args.goal_id, args.failed_attempt_id, args.actor, args.reason).__dict__); return
    if args.command == "reverification-decide": _emit(loop.decide_reverification_requires_new_attempt(args.goal_id, args.actor, args.reason).__dict__); return
    if args.command == "attempt-complete": _emit(loop.complete_attempt(args.attempt_id, AttemptOutcome(args.outcome)).__dict__); return
    if args.command == "evidence-record":
        try:
            # Copy before recording: a failed record leaves only an unreferenced, content-addressed copy.
            stored = [store_attachment(path, artifacts_dir_for(args.database)) for path in args.attach]
        except ProjectLocationError as error:
            parser.error(str(error))
        attachments = tuple(EvidenceAttachment(item.name, item.sha256, item.size) for item in stored)
        _emit(loop.record_evidence(args.attempt_id, args.actor, args.observation, attachments=attachments).__dict__); return
    if args.command == "ao-import":
        try:
            snapshot = read_ao_goal(args.ao_project, args.ao_goal_id)
            with tempfile.TemporaryDirectory() as staging:
                staged = [write_snapshot(snapshot, Path(staging)), *write_extracts(snapshot, Path(staging))]
                for source in snapshot.source_files:
                    # Every AO session file is named session.json; prefix the folder so attachments stay distinguishable.
                    name = f"goal-{args.ao_goal_id}.json" if source.name == "goal.json" else f"session-{source.parent.name}.json"
                    staged.append(Path(shutil.copyfile(source, Path(staging) / name)))
                stored = [store_attachment(path, artifacts_dir_for(args.database)) for path in staged]
        except (AoImportError, ProjectLocationError) as error:
            parser.error(str(error))
        attachments = tuple(EvidenceAttachment(item.name, item.sha256, item.size) for item in stored)
        evidence = loop.record_evidence(args.attempt_id, args.actor, summarize(snapshot), attachments=attachments)
        link = link_goal(loop, evidence.goal_id, snapshot.project_id, args.ao_goal_id, args.actor, args.ao_web_url)
        _emit({**evidence.__dict__, "ao_link": link.id if link else None}); return
    if args.command == "ao-sync":
        try:
            rows = sync_ao_changes(loop, artifacts_dir_for(args.database), args.ao_workspace, args.actor, dry_run=args.dry_run)
        except AoSyncError as error:
            parser.error(str(error))
        _emit({"dry_run": args.dry_run, "changes": rows}); return
    if args.command == "ao-links":
        if args.backfill and not args.actor:
            parser.error("--backfill에는 --actor가 필요합니다.")
        added = backfill_ao_links(loop, repo, artifacts_dir_for(args.database), args.actor, args.ao_web_url) if args.backfill else []
        ao_goal = tuple(args.ao_goal.split("/", 1)) if args.ao_goal and "/" in args.ao_goal else None
        if args.ao_goal and ao_goal is None:
            parser.error("--ao-goal은 <프로젝트>/<AO Goal ID> 형식입니다.")
        _emit({"added": added, "links": ao_goal_links(loop, artifacts_dir_for(args.database), goal_id=args.goal, ao_goal=ao_goal)}); return
    if args.command == "reference-add":
        kind = args.subject_id.split("-", 1)[0]
        try:
            location = normalize_reference(args.location, project_root_for(args.database), Path.cwd())
        except ProjectLocationError as error:
            parser.error(str(error))
        _emit(loop.add_reference(kind, args.subject_id, location, args.actor, args.label).__dict__); return
    if args.command == "reference-remove": _emit(loop.remove_reference(args.reference_id, args.actor, args.reason).__dict__); return
    if args.command == "evidence-correct": _emit(loop.correct_evidence(args.evidence_id, args.actor, args.observation).__dict__); return
    if args.command == "verification-begin": loop.begin_verification(args.goal_id, args.attempt_id); _emit(loop.goals[args.goal_id].__dict__); return
    if args.command == "verdict-submit": _emit(loop.submit_verdict(args.goal_id, args.attempt_id, args.verifier, VerdictResult(args.result), tuple(args.evidence_ids), supersedes_verdict_id=args.supersedes_verdict, reason=args.reason).__dict__); return
    if args.command == "verdict-review-resolve": _emit(loop.resolve_verdict_review(args.impact_review_id, ReviewDisposition(args.disposition), args.actor, args.reason).__dict__); return
    if args.command == "aim-evaluation-review-resolve": _emit(loop.resolve_aim_evaluation_review(args.evaluation_id, args.impact_review_id, ReviewDisposition(args.disposition), args.actor, args.reason).__dict__); return
    if args.command == "aim-closure-invalidate": _emit(loop.reopen_aim_after_closure_invalidation(args.aim_id, args.evaluation_id, args.triggering_record_id, args.actor, args.reason).__dict__); return
    if args.command == "aim-pause": _emit(loop.pause_aim(args.aim_id, args.actor, args.reason).__dict__); return
    if args.command == "aim-resume": _emit(loop.resume_aim(args.aim_id, args.actor, args.reason).__dict__); return
    if args.command == "goal-hold-add": loop.add_goal_hold(args.goal_id, args.actor, args.reason); _emit({"action": "goal_hold_added", "goal_id": args.goal_id, "actor_id": args.actor, "reason": args.reason}); return
    if args.command == "goal-hold-clear": loop.clear_goal_hold(args.goal_id); _emit({"action": "goal_hold_cleared", "goal_id": args.goal_id}); return
    if args.command == "execution-grant": loop.grant_execute(args.actor, args.goal_id); _emit({"action": "execution_granted", "goal_id": args.goal_id, "actor": args.actor}); return
    if args.command == "execution-revoke": loop.revoke_execute(args.actor, args.goal_id); _emit({"action": "execution_revoked", "goal_id": args.goal_id, "actor": args.actor}); return
    if args.command == "verdict-dependency-add": loop.add_verdict_dependency(args.goal_id, args.verdict_id, VerdictResult(args.required_result)); _emit({"action": "verdict_dependency_added", "goal_id": args.goal_id, "verdict_id": args.verdict_id, "required_result": args.required_result}); return
    if args.command == "goal-parent-add": loop.add_goal_parent(args.child_goal_id, args.parent_goal_id); _emit({"action": "goal_parent_linked", "child_goal_id": args.child_goal_id, "parent_goal_id": args.parent_goal_id}); return
    if args.command == "goal-dependency-add": loop.add_goal_dependency(args.dependent_goal_id, args.required_goal_id); _emit({"action": "goal_dependency_linked", "dependent_goal_id": args.dependent_goal_id, "required_goal_id": args.required_goal_id}); return
    if args.command == "codex-run":
        if not args.workdir.is_dir():
            parser.error(f"--workdir must be an existing directory: {args.workdir}")
        profile = CodexExecProfile(args.executable, args.sandbox, tuple(args.extra_arg))
        try:
            execution = SubprocessRuntime().run(
                profile.invocation(args.prompt, args.workdir, timeout_seconds=args.timeout),
                delivery_id=args.delivery_id,
            )
        except FileNotFoundError:
            parser.error(
                f"Codex executable was not found: {profile.executable!r}. "
                "Install the Codex CLI and add it to PATH, or pass its full path with --executable. "
                "The Attempt was not changed."
            )
        except OSError as error:
            if error.errno != 193 and getattr(error, "winerror", None) != 193:
                raise
            parser.error(
                f"Codex executable cannot be run directly: {profile.executable!r}. "
                "A .ps1 PowerShell script is not a direct subprocess executable. "
                "Use its companion .cmd file (replace .ps1 with .cmd), or use `codex` after PATH is configured. "
                "The Attempt was not changed."
            )
        attempt = deliver_execution(loop, args.attempt_id, execution)
        _emit({"attempt": attempt.__dict__, "execution": execution.__dict__})
        return
    if args.command == "claude-run":
        if not args.workdir.is_dir():
            parser.error(f"--workdir must be an existing directory: {args.workdir}")
        profile = ClaudePrintProfile(args.executable, args.model, args.max_turns, args.max_budget_usd)
        try:
            execution = SubprocessRuntime().run(
                profile.invocation(args.prompt, args.workdir, timeout_seconds=args.timeout),
                delivery_id=args.delivery_id,
            )
        except FileNotFoundError:
            parser.error(
                f"Claude executable was not found: {profile.executable!r}. "
                "Install Claude Code and add it to PATH, or pass its full path with --executable. "
                "The Attempt was not changed."
            )
        except OSError as error:
            if error.errno != 193 and getattr(error, "winerror", None) != 193:
                raise
            parser.error(
                f"Claude executable cannot be run directly: {profile.executable!r}. "
                "A .ps1 PowerShell script is not a direct subprocess executable. "
                "Use its companion .cmd file (replace .ps1 with .cmd), or use `claude` after PATH is configured. "
                "The Attempt was not changed."
            )
        attempt = deliver_execution(loop, args.attempt_id, execution)
        _emit({"attempt": attempt.__dict__, "execution": execution.__dict__})
        return
