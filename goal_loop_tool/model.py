"""Domain records for the first executable vertical slice.

The records deliberately distinguish a Goal's lifecycle, an execution attempt's
outcome, and a verification verdict.  They are not interchangeable states.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AimPhase(StrEnum):
    OPEN = "open"
    ACHIEVED = "achieved"
    ABANDONED = "abandoned"


class ProposalStatus(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"
    CLOSED = "closed"


class GoalPhase(StrEnum):
    COMMITTED = "committed"
    ACTIVE = "active"
    VERIFYING = "verifying"
    VERIFIED = "verified"
    STOPPED = "stopped"


class AttemptOutcome(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class VerdictResult(StrEnum):
    SATISFIED = "satisfied"
    NOT_SATISFIED = "not_satisfied"
    INCONCLUSIVE = "inconclusive"


class VerdictValidity(StrEnum):
    VALID = "valid"
    UNDER_REVIEW = "under_review"
    INVALIDATED = "invalidated"


class ReviewDisposition(StrEnum):
    REAFFIRM = "reaffirm"
    INVALIDATE = "invalidate"


class AdmissionErrorCode(StrEnum):
    AIM_ACHIEVED = "aim_achieved"
    AIM_ABANDONED = "aim_abandoned"
    AIM_PAUSED = "aim_paused"
    GOAL_HELD = "goal_held"
    AUTHORITY_MISSING = "authority_missing"
    GOAL_PHASE_DISALLOWED = "goal_phase_disallowed"
    CONTRACT_VERSION_STALE = "contract_version_stale"
    RETRY_DECISION_REQUIRED = "retry_decision_required"
    REVERIFICATION_DECISION_REQUIRED = "reverification_decision_required"
    DEPENDENCY_RESULT_MISMATCH = "dependency_result_mismatch"
    DEPENDENCY_VERDICT_UNTRUSTED = "dependency_verdict_untrusted"
    DEPENDENCY_GOAL_UNVERIFIED = "dependency_goal_unverified"


@dataclass
class Aim:
    id: str
    description: str
    phase: AimPhase = AimPhase.OPEN
    abandoned_by_actor_id: str | None = None
    abandon_reason: str | None = None
    # A sub-Aim splits a direction that cannot yet be written as a Contract; set once, never moved.
    parent_aim_id: str | None = None


@dataclass
class AimEvaluation:
    id: str
    aim_id: str
    evaluator_actor_id: str
    verdict_ids: tuple[str, ...]
    conclusion: str
    # Valid evaluations of direct sub-Aims cited as the basis for this Aim's achievement.
    child_evaluation_ids: tuple[str, ...] = ()


@dataclass
class GoalProposal:
    id: str
    aim_id: str
    direction: str
    status: ProposalStatus = ProposalStatus.OPEN
    accepted_goal_id: str | None = None
    superseded_by_proposal_id: str | None = None
    proposed_by_actor_id: str | None = None
    closure_kind: str | None = None
    closed_by_actor_id: str | None = None
    closure_reason: str | None = None


@dataclass(frozen=True)
class GoalIdentity:
    primary_aim_id: str
    target_and_desired_result: str
    responsibility_kind: str


@dataclass(frozen=True)
class GoalContract:
    goal_id: str
    version: int
    operational_scope: str
    success_criterion: str
    verification_specification: str


@dataclass
class Goal:
    id: str
    identity: GoalIdentity
    phase: GoalPhase = GoalPhase.COMMITTED
    current_contract_version: int = 1
    verifying_attempt_id: str | None = None
    stopped_by_actor_id: str | None = None
    stop_reason: str | None = None


@dataclass
class ExecutionAttempt:
    id: str
    goal_id: str
    contract_version: int
    executor_actor_id: str
    operation: str
    outcome: AttemptOutcome = AttemptOutcome.RUNNING


@dataclass(frozen=True)
class EvidenceAttachment:
    """A copy of observed material, identified by content so later edits to the source are detectable."""
    name: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Evidence:
    id: str
    goal_id: str
    contract_version: int
    attempt_id: str
    submitted_by_actor_id: str
    observation: str
    attachments: tuple[EvidenceAttachment, ...] = ()


@dataclass
class Reference:
    """A pointer to a living document that gives context to an Aim, Proposal, or Goal; never a criterion."""
    id: str
    subject_kind: str
    subject_id: str
    location: str
    label: str | None
    added_by_actor_id: str
    removed_by_actor_id: str | None = None
    removal_reason: str | None = None


@dataclass
class Decision:
    id: str
    goal_id: str
    kind: str
    decided_by_actor_id: str
    reason: str
    target_attempt_id: str | None = None
    contract_version: int | None = None
    consumed_by_attempt_id: str | None = None


@dataclass(frozen=True)
class VerificationVerdict:
    id: str
    goal_id: str
    contract_version: int
    attempt_id: str
    verifier_actor_id: str
    result: VerdictResult
    evidence_ids: tuple[str, ...]
    supersedes_verdict_id: str | None = None
    # Why the verifier judged so, e.g. which numbered criteria were (not) met. Optional: older journals have none.
    reason: str | None = None


@dataclass(frozen=True)
class EvidenceCorrection:
    id: str
    corrected_evidence_id: str
    submitted_by_actor_id: str
    observation: str


@dataclass(frozen=True)
class ImpactReview:
    id: str
    trigger_verdict_id: str
    correction_id: str
    affected_goal_ids: tuple[str, ...]
    affected_aim_evaluation_ids: tuple[str, ...] = ()
    level: str = "hard"
    disposition: str = "review_needed"


@dataclass(frozen=True)
class ReviewResolution:
    id: str
    impact_review_id: str
    subject_kind: str
    subject_id: str
    disposition: ReviewDisposition
    decided_by_actor_id: str
    reason: str


@dataclass(frozen=True)
class AimClosureInvalidation:
    id: str
    aim_id: str
    evaluation_id: str
    triggering_record_id: str
    decided_by_actor_id: str
    reason: str


@dataclass(frozen=True)
class VerdictDependency:
    dependent_goal_id: str
    required_verdict_id: str
    required_result: VerdictResult


@dataclass(frozen=True)
class GoalDependency:
    dependent_goal_id: str
    required_goal_id: str

