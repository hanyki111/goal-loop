"""Human-readable activity projection derived from durable journal facts."""

from __future__ import annotations

from dataclasses import dataclass

from .repository import ActivityRecord, SqliteEventRepository


@dataclass(frozen=True)
class ActivityCard:
    command_id: str
    recorded_at: str
    status: str
    title: str
    detail: str
    actor_id: str | None
    subject: str | None
    error_code: str | None = None


_TITLES = {
    "AimCreated": "Aim 생성",
    "ProposalCreated": "Goal Proposal 생성",
    "ProposalSuperseded": "Goal Proposal 교체",
    "ProposalAccepted": "Proposal 수락 및 Goal 확정",
    "ProposalClosed": "Goal Proposal 종료",
    "GoalStopped": "Goal 중단",
    "AimAbandoned": "Aim 포기",
    "AimParentLinked": "하위 목표로 연결",
    "AttemptStarted": "실행 시도 시작",
    "AttemptCompleted": "실행 시도 완료",
    "EvidenceRecorded": "Evidence 기록",
    "ReferenceAdded": "참고 문서 연결",
    "ReferenceRemoved": "참고 문서 연결 해제",
    "VerificationBegan": "검증 시작",
    "VerdictSubmitted": "Verification Verdict 제출",
    "EvidenceCorrected": "Evidence 정정",
    "VerdictReviewOpened": "Verdict 영향 검토 시작",
    "VerdictReviewResolved": "Verdict 영향 검토 종결",
    "VerdictInvalidated": "Verdict 무효화",
    "AimEvaluated": "Aim 달성 평가",
    "AimEvaluationReviewResolved": "Aim 평가 영향 검토 종결",
    "AimClosureInvalidated": "Aim 종결 무효화 및 재개",
    "ExecutionGranted": "실행 권한 부여",
    "ExecutionRevoked": "실행 권한 철회",
    "GoalHoldAdded": "Goal Hold 추가",
    "GoalHoldCleared": "Goal Hold 해제",
    "AimPaused": "Aim 일시 정지",
    "AimResumed": "Aim 재개",
    "VerdictDependencyAdded": "Verdict 의존성 추가",
    "GoalParentLinked": "상위 과제 연결",
    "GoalDependencyLinked": "선행 과제 연결",
    "GoalFollowLinked": "이어받은 과제 연결",
    "ContractVersionAdded": "Contract 버전 추가",
    "DecisionRecorded": "재시도 Decision 기록",
    "ReverificationDecisionRecorded": "재검증 Decision 기록",
}


def activity_cards(repository: SqliteEventRepository) -> list[ActivityCard]:
    """Return a Korean read model; journal data remains the authoritative record."""
    return [_card(record) for record in repository.activity_records()]


def _card(record: ActivityRecord) -> ActivityCard:
    if record.status == "rejected":
        return ActivityCard(
            command_id=record.command_id,
            recorded_at=record.recorded_at,
            status=record.status,
            title="명령 거절",
            detail=f"{record.command_type}: {record.error_message or '요청이 거절되었습니다.'}",
            actor_id=None,
            subject=None,
            error_code=record.error_code,
        )
    payload = record.payload or {}
    subject = f"{record.subject_kind} {record.subject_id}" if record.subject_kind else None
    return ActivityCard(
        command_id=record.command_id,
        recorded_at=record.recorded_at,
        status=record.status,
        title=_TITLES.get(record.event_type or "", record.event_type or record.command_type),
        detail=_detail(record.event_type, payload),
        actor_id=record.actor_id,
        subject=subject,
    )


def _detail(event_type: str | None, payload: dict[str, object]) -> str:
    if event_type == "AimCreated":
        parent = payload.get("parent_aim_id")
        return f"{payload.get('description', '')} (상위 목표: {parent})" if parent else str(payload.get("description", ""))
    if event_type == "AimParentLinked":
        return f"{payload.get('child_aim_id')} → 상위 목표 {payload.get('parent_aim_id')}"
    if event_type == "ProposalSuperseded":
        return f"후속 Proposal: {payload.get('replacement_proposal_id')}; 이유: {payload.get('reason', '')}"
    if event_type == "EvidenceRecorded" and payload.get("attachments"):
        names = ", ".join(str(item.get("name")) for item in payload["attachments"])
        return f"{payload.get('observation', '')} (첨부: {names})"
    if event_type in {"EvidenceRecorded", "EvidenceCorrected"}:
        return str(payload.get("observation", ""))
    if event_type == "ReferenceAdded":
        label = payload.get("label")
        return f"{label}: {payload.get('location')}" if label else str(payload.get("location", ""))
    if event_type == "ReferenceRemoved":
        return f"{payload.get('reference_id')}; 이유: {payload.get('reason') or '기록 없음'}"
    if event_type in {"AimPaused", "AimResumed", "DecisionRecorded", "ReverificationDecisionRecorded"}:
        return str(payload.get("reason", ""))
    if event_type == "VerdictSubmitted":
        return f"결론: {payload.get('result')}; Evidence: {', '.join(payload.get('evidence_ids', []))}"
    if event_type in {"VerdictReviewResolved", "AimEvaluationReviewResolved"}:
        return f"결정: {payload.get('disposition')}; 이유: {payload.get('reason', '')}"
    if event_type == "GoalFollowLinked":
        return f"{payload.get('previous_goal_id')} → {payload.get('goal_id')}; 이유: {payload.get('reason', '')}"
    if event_type == "ContractVersionAdded":
        return str(payload.get("reason", ""))
    if event_type == "ProposalClosed":
        return f"종료: {payload.get('closure_kind')}; 이유: {payload.get('reason', '')}"
    if event_type in {"GoalStopped", "AimAbandoned", "GoalHoldAdded"}:
        return str(payload.get("reason") or "")
    return ", ".join(f"{key}={value}" for key, value in payload.items())
