"""In-memory application service for the first Goal Loop Tool vertical slice."""

from __future__ import annotations

from dataclasses import dataclass

from .model import (
    Aim,
    AimPhase,
    AimEvaluation,
    AttemptOutcome,
    Decision,
    Evidence,
    EvidenceAttachment,
    ExecutionAttempt,
    Reference,
    Goal,
    GoalContract,
    GoalIdentity,
    GoalPhase,
    GoalProposal,
    ProposalStatus,
    VerificationVerdict,
    VerdictResult,
    VerdictValidity,
    EvidenceCorrection,
    ImpactReview,
    AdmissionErrorCode,
    AimClosureInvalidation,
    ReviewDisposition,
    ReviewResolution,
    VerdictDependency,
    GoalDependency,
)


# declined: compared and not chosen; withdrawn: the proposer took it back;
# criteria: adopted as the Aim's achievement criteria rather than pursued as a Goal.
PROPOSAL_CLOSURE_KINDS = ("declined", "withdrawn", "criteria")


class GoalLoopError(ValueError):
    """Raised when a command would violate a domain invariant."""

    def __init__(self, message: str, code: AdmissionErrorCode | None = None) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ContractDraft:
    operational_scope: str
    success_criterion: str
    verification_specification: str


class GoalLoop:
    """A deliberately small core, with storage kept in memory for the MVP slice."""

    def __init__(self, *, enforce_authority: bool = False) -> None:
        self.enforce_authority = enforce_authority
        self.aims: dict[str, Aim] = {}
        self.aim_evaluations: dict[str, AimEvaluation] = {}
        self.aim_evaluation_validity: dict[str, VerdictValidity] = {}
        self.proposals: dict[str, GoalProposal] = {}
        self.goals: dict[str, Goal] = {}
        self.contracts: dict[tuple[str, int], GoalContract] = {}
        self.attempts: dict[str, ExecutionAttempt] = {}
        self.evidence: dict[str, Evidence] = {}
        self.decisions: dict[str, Decision] = {}
        self.pending_reverification_decision_by_goal: dict[str, str] = {}
        self.verdicts: dict[str, VerificationVerdict] = {}
        self.verdict_validity: dict[str, VerdictValidity] = {}
        self.evidence_corrections: dict[str, EvidenceCorrection] = {}
        self.impact_reviews: dict[str, ImpactReview] = {}
        self.review_resolutions: dict[str, ReviewResolution] = {}
        self.aim_closure_invalidations: dict[str, AimClosureInvalidation] = {}
        self.verdict_dependencies: dict[str, list[VerdictDependency]] = {}
        self.goal_parents: dict[str, str] = {}
        self.goal_dependencies: dict[str, list[GoalDependency]] = {}
        self.execution_grants: dict[str, set[str]] = {}
        self.goal_holds: set[str] = set()
        # Who placed a Hold and why; legacy Holds recorded before 2026-10-03 carry no reason.
        self.goal_hold_records: dict[str, tuple[str, str | None]] = {}
        self.paused_aim_ids: set[str] = set()
        self.references: dict[str, Reference] = {}
        self._acceptance_keys: dict[str, tuple[tuple[object, ...], str]] = {}
        self._counters: dict[str, int] = {}

    def _new_id(self, prefix: str) -> str:
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return f"{prefix}-{self._counters[prefix]}"

    def create_aim(self, description: str, parent_aim_id: str | None = None) -> Aim:
        if parent_aim_id is not None:
            self._check_aim_parent(parent_aim_id)
        aim = Aim(id=self._new_id("aim"), description=description, parent_aim_id=parent_aim_id)
        self.aims[aim.id] = aim
        return aim

    def link_aim_parent(self, child_aim_id: str, parent_aim_id: str) -> Aim:
        """Place an existing Aim under a parent once; the tree has one parent per Aim and no cycles."""
        child = self._require(self.aims, child_aim_id, "Aim")
        if child.parent_aim_id is not None:
            raise GoalLoopError("An Aim already has a parent Aim; the link cannot be changed.")
        self._check_aim_parent(parent_aim_id)
        if parent_aim_id == child_aim_id or child_aim_id in self.aim_ancestors(parent_aim_id):
            raise GoalLoopError("An Aim cannot be placed under itself or its own sub-Aim.")
        child.parent_aim_id = parent_aim_id
        return child

    def _check_aim_parent(self, parent_aim_id: str) -> None:
        parent = self._require(self.aims, parent_aim_id, "Aim")
        if parent.phase is not AimPhase.OPEN:
            raise GoalLoopError("Only an open Aim can receive a sub-Aim.")

    def aim_ancestors(self, aim_id: str) -> list[str]:
        """Parent first, root last."""
        chain: list[str] = []
        current = self.aims[aim_id].parent_aim_id
        while current is not None and current not in chain:
            chain.append(current)
            current = self.aims[current].parent_aim_id
        return chain

    def _check_aim_lineage_admits(self, aim_id: str, action: str, *, execution: bool = False) -> None:
        """Abandoning an Aim stops new work under every sub-Aim; pausing stops only execution, as it does for the Aim itself."""
        for ancestor_id in self.aim_ancestors(aim_id):
            if self.aims[ancestor_id].phase is AimPhase.ABANDONED:
                raise GoalLoopError(f"An ancestor Aim {ancestor_id} is abandoned; {action} is not allowed.", AdmissionErrorCode.AIM_ABANDONED)
            if execution and ancestor_id in self.paused_aim_ids:
                raise GoalLoopError(f"An ancestor Aim {ancestor_id} is paused; {action} is not allowed.", AdmissionErrorCode.AIM_PAUSED)

    def evaluate_aim_as_achieved(
        self,
        aim_id: str,
        evaluator_actor_id: str,
        verdict_ids: tuple[str, ...],
        conclusion: str,
        child_evaluation_ids: tuple[str, ...] = (),
    ) -> AimEvaluation:
        """Record an explicit Aim evaluation; verified Goals or achieved sub-Aims never close an Aim implicitly."""
        aim = self._require(self.aims, aim_id, "Aim")
        if aim.phase is not AimPhase.OPEN:
            raise GoalLoopError("Only an open Aim can be evaluated as achieved.")
        if not verdict_ids and not child_evaluation_ids:
            raise GoalLoopError("An Aim evaluation must cite at least one Verdict or sub-Aim evaluation.")
        for child_evaluation_id in child_evaluation_ids:
            child_evaluation = self._require(self.aim_evaluations, child_evaluation_id, "Aim Evaluation")
            if self.aims[child_evaluation.aim_id].parent_aim_id != aim_id:
                raise GoalLoopError("An Aim evaluation may cite only evaluations of its direct sub-Aims.")
            if self.aim_evaluation_validity.get(child_evaluation_id) is not VerdictValidity.VALID:
                raise GoalLoopError("An achieved Aim requires currently valid sub-Aim evaluations.")
        for verdict_id in verdict_ids:
            verdict = self._require(self.verdicts, verdict_id, "Verdict")
            goal = self._require(self.goals, verdict.goal_id, "Goal")
            if goal.identity.primary_aim_id != aim_id:
                raise GoalLoopError("An Aim evaluation may cite only Verdicts from its primary Aim.")
            if verdict.result is not VerdictResult.SATISFIED:
                raise GoalLoopError("An achieved Aim requires satisfied Verdicts.")
            if self.verdict_validity.get(verdict_id) is not VerdictValidity.VALID:
                raise GoalLoopError("An achieved Aim requires currently valid Verdicts.")
        evaluation = AimEvaluation(
            id=self._new_id("aim-evaluation"),
            aim_id=aim_id,
            evaluator_actor_id=evaluator_actor_id,
            verdict_ids=verdict_ids,
            conclusion=conclusion,
            child_evaluation_ids=tuple(child_evaluation_ids),
        )
        self.aim_evaluations[evaluation.id] = evaluation
        self.aim_evaluation_validity[evaluation.id] = VerdictValidity.VALID
        aim.phase = AimPhase.ACHIEVED
        return evaluation
    def create_proposal(self, aim_id: str, direction: str, proposed_by_actor_id: str | None = None) -> GoalProposal:
        aim = self._require(self.aims, aim_id, "Aim")
        if aim.phase is AimPhase.ABANDONED:
            raise GoalLoopError("An abandoned Aim cannot receive a new Proposal.", AdmissionErrorCode.AIM_ABANDONED)
        self._check_aim_lineage_admits(aim_id, "a new Proposal")
        proposal = GoalProposal(id=self._new_id("proposal"), aim_id=aim_id, direction=direction, proposed_by_actor_id=proposed_by_actor_id)
        self.proposals[proposal.id] = proposal
        return proposal

    def revise_proposal(self, proposal_id: str, direction: str, actor_id: str, reason: str) -> GoalProposal:
        """Replace an open Proposal without rewriting its historical direction."""
        previous = self._require(self.proposals, proposal_id, "Proposal")
        if previous.status is not ProposalStatus.OPEN:
            raise GoalLoopError("Only an open Proposal can be revised.")
        if not direction.strip() or not reason.strip():
            raise GoalLoopError("Proposal revision requires a new direction and a reason.")
        successor = GoalProposal(
            id=self._new_id("proposal"), aim_id=previous.aim_id, direction=direction, proposed_by_actor_id=actor_id
        )
        previous.status = ProposalStatus.SUPERSEDED
        previous.superseded_by_proposal_id = successor.id
        self.proposals[successor.id] = successor
        return successor

    def accept_proposal(
        self,
        proposal_id: str,
        identity: GoalIdentity,
        contract_draft: ContractDraft,
        *,
        idempotency_key: str,
    ) -> Goal:
        """Atomically accept a proposal and create its Goal and Contract v1.

        The in-memory store has no partial write between validation and the
        three related records, mirroring the transaction boundary required of a
        persistent implementation.
        """
        fingerprint = (proposal_id, identity, contract_draft)
        if existing := self._acceptance_keys.get(idempotency_key):
            if existing[0] != fingerprint:
                raise GoalLoopError("Idempotency key conflicts with a different acceptance request.")
            return self.goals[existing[1]]

        proposal = self._require(self.proposals, proposal_id, "Proposal")
        aim = self._require(self.aims, proposal.aim_id, "Aim")
        if aim.phase is AimPhase.ACHIEVED:
            raise GoalLoopError("An achieved Aim cannot accept a new Proposal.")
        if aim.phase is AimPhase.ABANDONED:
            raise GoalLoopError("An abandoned Aim cannot accept a new Proposal.", AdmissionErrorCode.AIM_ABANDONED)
        self._check_aim_lineage_admits(aim.id, "accepting a Proposal")
        if proposal.status is not ProposalStatus.OPEN:
            raise GoalLoopError("Only an open proposal can be accepted.")
        if proposal.aim_id != identity.primary_aim_id:
            raise GoalLoopError("A Goal Identity must keep the Proposal's primary Aim.")

        goal = Goal(id=self._new_id("goal"), identity=identity)
        contract = GoalContract(
            goal_id=goal.id,
            version=1,
            operational_scope=contract_draft.operational_scope,
            success_criterion=contract_draft.success_criterion,
            verification_specification=contract_draft.verification_specification,
        )
        proposal.status = ProposalStatus.ACCEPTED
        proposal.accepted_goal_id = goal.id
        self.goals[goal.id] = goal
        self.contracts[(goal.id, 1)] = contract
        self._acceptance_keys[idempotency_key] = (fingerprint, goal.id)
        return goal

    def start_attempt(
        self,
        goal_id: str,
        contract_version: int,
        executor_actor_id: str,
        operation: str,
        *,
        retry_decision_id: str | None = None,
        reverification_decision_id: str | None = None,
    ) -> ExecutionAttempt:
        goal = self._require(self.goals, goal_id, "Goal")
        aim = self._require(self.aims, goal.identity.primary_aim_id, "Aim")
        if aim.phase is AimPhase.ACHIEVED:
            raise GoalLoopError("The Goal's Aim is achieved.", AdmissionErrorCode.AIM_ACHIEVED)
        if aim.phase is AimPhase.ABANDONED:
            raise GoalLoopError("The Goal's Aim is abandoned.", AdmissionErrorCode.AIM_ABANDONED)
        if goal.identity.primary_aim_id in self.paused_aim_ids:
            raise GoalLoopError("The Goal's primary Aim is paused.", AdmissionErrorCode.AIM_PAUSED)
        self._check_aim_lineage_admits(aim.id, "a new Attempt", execution=True)
        if goal_id in self.goal_holds:
            raise GoalLoopError("The Goal has an unresolved execution Hold.", AdmissionErrorCode.GOAL_HELD)
        if self.enforce_authority and goal_id not in self.execution_grants.get(executor_actor_id, set()):
            raise GoalLoopError("Actor lacks execute Authority for this Goal.", AdmissionErrorCode.AUTHORITY_MISSING)
        if goal.phase not in (GoalPhase.COMMITTED, GoalPhase.ACTIVE):
            raise GoalLoopError("Attempts can start only for committed or active Goals.", AdmissionErrorCode.GOAL_PHASE_DISALLOWED)
        if contract_version != goal.current_contract_version:
            raise GoalLoopError("Attempt uses a stale Goal Contract version.", AdmissionErrorCode.CONTRACT_VERSION_STALE)
        self._require(self.contracts, (goal_id, contract_version), "Goal Contract")
        for dependency in self.verdict_dependencies.get(goal_id, []):
            verdict_id = dependency.required_verdict_id if isinstance(dependency, VerdictDependency) else dependency
            required_result = dependency.required_result if isinstance(dependency, VerdictDependency) else VerdictResult.SATISFIED
            verdict = self._require(self.verdicts, verdict_id, "Verdict")
            if self.verdict_validity.get(verdict.id) is not VerdictValidity.VALID:
                raise GoalLoopError("A required Verdict is under review or invalidated.", AdmissionErrorCode.DEPENDENCY_VERDICT_UNTRUSTED)
            if verdict.result is not required_result:
                raise GoalLoopError("A required Verdict has the wrong result.", AdmissionErrorCode.DEPENDENCY_RESULT_MISMATCH)
        for dependency in self.goal_dependencies.get(goal_id, []):
            source = self._require(self.goals, dependency.required_goal_id, "Required Goal")
            if not self._has_current_valid_satisfied_verdict(source.id):
                raise GoalLoopError(
                    "A required Goal lacks a valid satisfied Verdict for its current Contract.",
                    AdmissionErrorCode.DEPENDENCY_GOAL_UNVERIFIED,
                )

        same_contract_attempts = [a for a in self.attempts.values() if a.goal_id == goal_id and a.contract_version == contract_version]
        if same_contract_attempts and same_contract_attempts[-1].outcome is AttemptOutcome.FAILED:
            decision = self._require(self.decisions, retry_decision_id, "Retry Decision") if retry_decision_id else None
            failed = same_contract_attempts[-1]
            if not decision or decision.kind != "retry_same_contract" or decision.target_attempt_id != failed.id or decision.contract_version != contract_version or decision.consumed_by_attempt_id:
                raise GoalLoopError("A matching unused retry Decision is required.", AdmissionErrorCode.RETRY_DECISION_REQUIRED)
        if pending_id := self.pending_reverification_decision_by_goal.get(goal_id):
            if reverification_decision_id != pending_id or self.decisions[pending_id].consumed_by_attempt_id:
                raise GoalLoopError("A matching unused re-verification Decision is required.", AdmissionErrorCode.REVERIFICATION_DECISION_REQUIRED)

        goal.phase = GoalPhase.ACTIVE
        attempt = ExecutionAttempt(
            id=self._new_id("attempt"),
            goal_id=goal_id,
            contract_version=contract_version,
            executor_actor_id=executor_actor_id,
            operation=operation,
        )
        self.attempts[attempt.id] = attempt
        if retry_decision_id:
            self.decisions[retry_decision_id].consumed_by_attempt_id = attempt.id
        if reverification_decision_id:
            self.decisions[reverification_decision_id].consumed_by_attempt_id = attempt.id
            self.pending_reverification_decision_by_goal.pop(goal_id, None)
        return attempt

    def grant_execute(self, actor_id: str, goal_id: str) -> None:
        self._require(self.goals, goal_id, "Goal")
        self.execution_grants.setdefault(actor_id, set()).add(goal_id)

    def revoke_execute(self, actor_id: str, goal_id: str) -> None:
        self.execution_grants.get(actor_id, set()).discard(goal_id)

    def add_goal_hold(self, goal_id: str, actor_id: str = "system", reason: str | None = None) -> None:
        self._require(self.goals, goal_id, "Goal")
        self.goal_holds.add(goal_id)
        self.goal_hold_records[goal_id] = (actor_id, reason)

    def clear_goal_hold(self, goal_id: str) -> None:
        self.goal_holds.discard(goal_id)
        self.goal_hold_records.pop(goal_id, None)

    def add_reference(self, subject_kind: str, subject_id: str, location: str, actor_id: str, label: str | None = None) -> Reference:
        """Point an Aim, Proposal, or Goal at a document. Recorded only; admission, validity and achievement ignore it."""
        subjects = {"aim": (self.aims, "Aim"), "proposal": (self.proposals, "Proposal"), "goal": (self.goals, "Goal")}
        if subject_kind not in subjects:
            raise GoalLoopError("A reference can be attached to an Aim, Proposal, or Goal only.")
        self._require(subjects[subject_kind][0], subject_id, subjects[subject_kind][1])
        if not location.strip():
            raise GoalLoopError("A reference needs a location.")
        if not actor_id.strip():
            raise GoalLoopError("A reference needs the actor who added it.")
        reference = Reference(self._new_id("reference"), subject_kind, subject_id, location, label or None, actor_id)
        self.references[reference.id] = reference
        return reference

    def remove_reference(self, reference_id: str, actor_id: str, reason: str | None = None) -> Reference:
        reference = self._require(self.references, reference_id, "Reference")
        if reference.removed_by_actor_id is not None:
            raise GoalLoopError("A reference can be removed once.")
        if not actor_id.strip():
            raise GoalLoopError("Removing a reference needs the actor.")
        reference.removed_by_actor_id = actor_id
        reference.removal_reason = reason or None
        return reference

    def close_proposal(self, proposal_id: str, closure_kind: str, actor_id: str, reason: str) -> GoalProposal:
        """Close an open Proposal without accepting it; it is never reopened.

        ``criteria`` closes a Proposal that carries its Aim's achievement criteria: it is
        neither pursued as a Goal nor rejected, and stays readable as those criteria.
        """
        proposal = self._require(self.proposals, proposal_id, "Proposal")
        if proposal.status is not ProposalStatus.OPEN:
            raise GoalLoopError("Only an open Proposal can be closed.")
        if closure_kind not in PROPOSAL_CLOSURE_KINDS:
            raise GoalLoopError("A Proposal closure must be declined, withdrawn or criteria.")
        if not reason.strip():
            raise GoalLoopError("Closing a Proposal requires a reason.")
        proposal.status = ProposalStatus.CLOSED
        proposal.closure_kind = closure_kind
        proposal.closed_by_actor_id = actor_id
        proposal.closure_reason = reason
        return proposal

    def stop_goal(self, goal_id: str, actor_id: str, reason: str) -> Goal:
        """Stop pursuing a Goal. Running Attempts are not cancelled; their outcome can still be recorded."""
        goal = self._require(self.goals, goal_id, "Goal")
        if goal.phase not in (GoalPhase.COMMITTED, GoalPhase.ACTIVE, GoalPhase.VERIFYING):
            raise GoalLoopError("Only a committed, active or verifying Goal can be stopped.")
        if not reason.strip():
            raise GoalLoopError("Stopping a Goal requires a reason.")
        goal.phase = GoalPhase.STOPPED
        goal.stopped_by_actor_id = actor_id
        goal.stop_reason = reason
        return goal

    def abandon_aim(self, aim_id: str, actor_id: str, reason: str) -> Aim:
        """Give up an open Aim. Child Proposals and Goals are not closed; admission reads the Aim phase."""
        aim = self._require(self.aims, aim_id, "Aim")
        if aim.phase is not AimPhase.OPEN:
            raise GoalLoopError("Only an open Aim can be abandoned.")
        if not reason.strip():
            raise GoalLoopError("Abandoning an Aim requires a reason.")
        aim.phase = AimPhase.ABANDONED
        aim.abandoned_by_actor_id = actor_id
        aim.abandon_reason = reason
        return aim

    def pause_aim(self, aim_id: str, actor_id: str = "system", reason: str = "paused") -> Decision:
        self._require(self.aims, aim_id, "Aim")
        if not reason.strip():
            raise GoalLoopError("Aim pause requires a reason.")
        decision = Decision(self._new_id("decision"), aim_id, "pause_aim", actor_id, reason)
        self.decisions[decision.id] = decision
        self.paused_aim_ids.add(aim_id)
        return decision

    def resume_aim(self, aim_id: str, actor_id: str, reason: str) -> Decision:
        self._require(self.aims, aim_id, "Aim")
        if not reason.strip():
            raise GoalLoopError("Aim resume requires a reason.")
        decision = Decision(self._new_id("decision"), aim_id, "resume_aim", actor_id, reason)
        self.decisions[decision.id] = decision
        self.paused_aim_ids.discard(aim_id)
        return decision

    def decide_reverification_requires_new_attempt(self, goal_id: str, actor_id: str, reason: str) -> Decision:
        goal = self._require(self.goals, goal_id, "Goal")
        if goal.phase is not GoalPhase.VERIFYING or not goal.verifying_attempt_id or not reason.strip():
            raise GoalLoopError("Re-verification requires a verifying Goal and a reason.")
        decision = Decision(
            self._new_id("decision"), goal_id, "reverification_requires_new_attempt", actor_id,
            reason, target_attempt_id=goal.verifying_attempt_id, contract_version=goal.current_contract_version,
        )
        self.decisions[decision.id] = decision
        self.pending_reverification_decision_by_goal[goal_id] = decision.id
        goal.verifying_attempt_id = None
        goal.phase = GoalPhase.ACTIVE
        return decision

    def revise_contract(
        self,
        goal_id: str,
        contract_draft: ContractDraft,
        *,
        decided_by_actor_id: str,
        reason: str,
    ) -> GoalContract:
        """Create the next immutable Contract version for the same Goal.

        A material change is allowed only while the Goal is still being pursued.
        Existing Attempts and Evidence retain their old version references; they
        are historical facts, not evidence for the new Contract by default.
        """
        goal = self._require(self.goals, goal_id, "Goal")
        if goal.phase not in (GoalPhase.COMMITTED, GoalPhase.ACTIVE):
            raise GoalLoopError("A Contract can be revised only for a committed or active Goal.")
        if not reason.strip():
            raise GoalLoopError("A Contract revision requires a reason.")
        current = self._require(self.contracts, (goal_id, goal.current_contract_version), "Goal Contract")
        if (
            current.operational_scope,
            current.success_criterion,
            current.verification_specification,
        ) == (
            contract_draft.operational_scope,
            contract_draft.success_criterion,
            contract_draft.verification_specification,
        ):
            raise GoalLoopError("A Contract revision requires a changed Contract draft.")

        next_version = goal.current_contract_version + 1
        contract = GoalContract(
            goal_id=goal.id,
            version=next_version,
            operational_scope=contract_draft.operational_scope,
            success_criterion=contract_draft.success_criterion,
            verification_specification=contract_draft.verification_specification,
        )
        self.contracts[(goal.id, next_version)] = contract
        goal.current_contract_version = next_version
        decision = Decision(
            id=self._new_id("decision"),
            goal_id=goal.id,
            kind="revise_contract",
            decided_by_actor_id=decided_by_actor_id,
            reason=reason,
        )
        self.decisions[decision.id] = decision
        return contract

    def complete_attempt(self, attempt_id: str, outcome: AttemptOutcome) -> ExecutionAttempt:
        attempt = self._require(self.attempts, attempt_id, "Attempt")
        if attempt.outcome is not AttemptOutcome.RUNNING:
            raise GoalLoopError("An Attempt may be completed once.")
        if outcome not in (AttemptOutcome.SUCCEEDED, AttemptOutcome.FAILED):
            raise GoalLoopError("This vertical slice accepts succeeded or failed outcomes only.")
        attempt.outcome = outcome
        return attempt

    def record_evidence(self, attempt_id: str, actor_id: str, observation: str, attachments: tuple[EvidenceAttachment, ...] = ()) -> Evidence:
        attempt = self._require(self.attempts, attempt_id, "Attempt")
        if attempt.outcome is AttemptOutcome.RUNNING:
            raise GoalLoopError("Evidence requires a completed Attempt in this vertical slice.")
        # Attachments are fixed with the Evidence: a Verdict citing it must cite exactly what the verifier saw.
        seen: set[tuple[str, str]] = set()
        for attachment in attachments:
            if not attachment.name.strip() or len(attachment.sha256) != 64 or any(ch not in "0123456789abcdef" for ch in attachment.sha256) or attachment.size < 0:
                raise GoalLoopError("An Evidence attachment needs a name, a lowercase SHA-256 digest, and a size.")
            if (attachment.name, attachment.sha256) in seen:
                raise GoalLoopError("The same attachment cannot be added to one Evidence twice.")
            seen.add((attachment.name, attachment.sha256))
        evidence = Evidence(
            id=self._new_id("evidence"),
            goal_id=attempt.goal_id,
            contract_version=attempt.contract_version,
            attempt_id=attempt.id,
            submitted_by_actor_id=actor_id,
            observation=observation,
            attachments=tuple(attachments),
        )
        self.evidence[evidence.id] = evidence
        return evidence

    def decide_same_contract_retry(self, goal_id: str, failed_attempt_id: str, actor_id: str, reason: str) -> Decision:
        goal = self._require(self.goals, goal_id, "Goal")
        attempt = self._require(self.attempts, failed_attempt_id, "Attempt")
        if goal.phase is not GoalPhase.ACTIVE or attempt.goal_id != goal_id or attempt.contract_version != goal.current_contract_version or attempt.outcome is not AttemptOutcome.FAILED or not reason.strip():
            raise GoalLoopError("A same-contract retry requires a failed current-Contract Attempt and reason.")
        decision = Decision(
            id=self._new_id("decision"),
            goal_id=goal_id,
            kind="retry_same_contract",
            decided_by_actor_id=actor_id,
            reason=reason,
            target_attempt_id=attempt.id,
            contract_version=attempt.contract_version,
        )
        self.decisions[decision.id] = decision
        return decision

    def begin_verification(self, goal_id: str, attempt_id: str) -> None:
        goal = self._require(self.goals, goal_id, "Goal")
        attempt = self._require(self.attempts, attempt_id, "Attempt")
        if attempt.goal_id != goal_id or attempt.contract_version != goal.current_contract_version:
            raise GoalLoopError("Verification must use the Goal's current Contract and an attached Attempt.")
        if attempt.outcome is not AttemptOutcome.SUCCEEDED:
            raise GoalLoopError("Only a successful Attempt can enter verification.")
        if goal.phase is not GoalPhase.ACTIVE:
            raise GoalLoopError("Only an active Goal can enter verification.")
        if any(
            verdict.attempt_id == attempt_id and verdict.result is not VerdictResult.INCONCLUSIVE
            for verdict in self.verdicts.values()
        ):
            raise GoalLoopError("A terminal Verdict already exists for this Attempt.")
        goal.verifying_attempt_id = attempt_id
        goal.phase = GoalPhase.VERIFYING

    def submit_verdict(
        self,
        goal_id: str,
        attempt_id: str,
        verifier_actor_id: str,
        result: VerdictResult,
        evidence_ids: tuple[str, ...],
        *,
        supersedes_verdict_id: str | None = None,
        reason: str | None = None,
    ) -> VerificationVerdict:
        goal = self._require(self.goals, goal_id, "Goal")
        attempt = self._require(self.attempts, attempt_id, "Attempt")
        if goal.phase is not GoalPhase.VERIFYING or goal.verifying_attempt_id != attempt_id:
            raise GoalLoopError("A Verdict requires a Goal in verifying phase.")
        if attempt.goal_id != goal_id or attempt.contract_version != goal.current_contract_version or attempt.outcome is not AttemptOutcome.SUCCEEDED:
            raise GoalLoopError("Verdict must target the Goal's current Contract and attached Attempt.")
        if verifier_actor_id == attempt.executor_actor_id:
            raise GoalLoopError("The executing Actor cannot issue the independent final Verdict.")
        if not evidence_ids:
            raise GoalLoopError("A Verdict must cite evidence.")
        prior = [verdict for verdict in self.verdicts.values() if verdict.attempt_id == attempt_id]
        if prior:
            previous = prior[-1]
            if previous.result is not VerdictResult.INCONCLUSIVE or supersedes_verdict_id != previous.id:
                raise GoalLoopError("Only a new Verdict superseding the latest inconclusive Verdict is allowed.")
            if not set(evidence_ids).difference(previous.evidence_ids):
                raise GoalLoopError("A superseding Verdict requires new Evidence.")
        for evidence_id in evidence_ids:
            evidence = self._require(self.evidence, evidence_id, "Evidence")
            if (evidence.goal_id != goal_id or evidence.contract_version != goal.current_contract_version
                    or evidence.attempt_id != attempt_id):
                raise GoalLoopError("Verdict evidence must belong to the same Goal Contract.")
            if any(correction.corrected_evidence_id == evidence_id for correction in self.evidence_corrections.values()):
                raise GoalLoopError("Corrected Evidence cannot support a new Verdict.")

        verdict = VerificationVerdict(
            id=self._new_id("verdict"),
            goal_id=goal_id,
            contract_version=goal.current_contract_version,
            attempt_id=attempt_id,
            verifier_actor_id=verifier_actor_id,
            result=result,
            evidence_ids=evidence_ids,
            supersedes_verdict_id=supersedes_verdict_id,
            reason=(reason or "").strip() or None,
        )
        self.verdicts[verdict.id] = verdict
        self.verdict_validity[verdict.id] = VerdictValidity.VALID
        if result is VerdictResult.SATISFIED:
            goal.phase = GoalPhase.VERIFIED
            goal.verifying_attempt_id = None
        elif result is VerdictResult.NOT_SATISFIED:
            goal.phase = GoalPhase.ACTIVE
            goal.verifying_attempt_id = None
        return verdict

    def add_verdict_dependency(self, goal_id: str, verdict_id: str, required_result: VerdictResult = VerdictResult.SATISFIED) -> None:
        self._require(self.goals, goal_id, "Goal")
        self._require(self.verdicts, verdict_id, "Verdict")
        self.verdict_dependencies.setdefault(goal_id, []).append(
            VerdictDependency(goal_id, verdict_id, required_result)
        )

    def add_goal_parent(self, child_goal_id: str, parent_goal_id: str, *, allow_legacy_deadlock: bool = False) -> None:
        child = self._require(self.goals, child_goal_id, "Child Goal")
        parent = self._require(self.goals, parent_goal_id, "Parent Goal")
        if child_goal_id == parent_goal_id:
            raise GoalLoopError("A Goal cannot be its own parent.")
        if child.identity.primary_aim_id != parent.identity.primary_aim_id:
            raise GoalLoopError("A Goal parent must belong to the same primary Aim.")
        if child_goal_id in self.goal_parents:
            raise GoalLoopError("A Goal already has an immutable parent link.")
        ancestor = parent_goal_id
        while ancestor in self.goal_parents:
            ancestor = self.goal_parents[ancestor]
            if ancestor == child_goal_id:
                raise GoalLoopError("A Goal parent link cannot create a cycle.")
        # A parent verifies only after its children finish, so a child must not also wait for an ancestor.
        required = {dependency.required_goal_id for dependency in self.goal_dependencies.get(child_goal_id, [])}
        if not allow_legacy_deadlock and ({parent_goal_id} | self._ancestors(parent_goal_id)) & required:
            raise GoalLoopError("A Goal cannot have a prerequisite as its ancestor.")
        self.goal_parents[child_goal_id] = parent_goal_id

    def _ancestors(self, goal_id: str) -> set[str]:
        ancestors: set[str] = set()
        cursor = self.goal_parents.get(goal_id)
        while cursor and cursor not in ancestors:
            ancestors.add(cursor)
            cursor = self.goal_parents.get(cursor)
        return ancestors

    def add_goal_dependency(self, dependent_goal_id: str, required_goal_id: str, *, allow_legacy_deadlock: bool = False) -> None:
        self._require(self.goals, dependent_goal_id, "Dependent Goal")
        self._require(self.goals, required_goal_id, "Required Goal")
        if dependent_goal_id == required_goal_id:
            raise GoalLoopError("A Goal cannot depend on itself.")
        if not allow_legacy_deadlock and required_goal_id in self._ancestors(dependent_goal_id):
            raise GoalLoopError("A Goal cannot depend on its own ancestor.")
        if self._goal_depends_on(required_goal_id, dependent_goal_id):
            raise GoalLoopError("A Goal dependency cannot create a cycle.")
        dependency = GoalDependency(dependent_goal_id, required_goal_id)
        if dependency in self.goal_dependencies.get(dependent_goal_id, []):
            raise GoalLoopError("This Goal dependency already exists.")
        self.goal_dependencies.setdefault(dependent_goal_id, []).append(dependency)

    def _goal_depends_on(self, goal_id: str, target_goal_id: str) -> bool:
        return any(
            dependency.required_goal_id == target_goal_id
            or self._goal_depends_on(dependency.required_goal_id, target_goal_id)
            for dependency in self.goal_dependencies.get(goal_id, [])
        )

    def _has_current_valid_satisfied_verdict(self, goal_id: str) -> bool:
        goal = self._require(self.goals, goal_id, "Goal")
        return any(
            verdict.goal_id == goal_id
            and verdict.contract_version == goal.current_contract_version
            and verdict.result is VerdictResult.SATISFIED
            and self.verdict_validity.get(verdict.id) is VerdictValidity.VALID
            for verdict in self.verdicts.values()
        )

    def correct_evidence(self, evidence_id: str, actor_id: str, observation: str) -> EvidenceCorrection:
        self._require(self.evidence, evidence_id, "Evidence")
        correction = EvidenceCorrection(self._new_id("correction"), evidence_id, actor_id, observation)
        self.evidence_corrections[correction.id] = correction
        for verdict in self.verdicts.values():
            if evidence_id not in verdict.evidence_ids:
                continue
            if self.verdict_validity.get(verdict.id) is VerdictValidity.INVALIDATED:
                continue
            self.verdict_validity[verdict.id] = VerdictValidity.UNDER_REVIEW
            affected = tuple(sorted(
                goal_id for goal_id, dependencies in self.verdict_dependencies.items()
                if any(
                    (dependency.required_verdict_id if isinstance(dependency, VerdictDependency) else dependency) == verdict.id
                    for dependency in dependencies
                )
            ))
            evaluations = tuple(
                evaluation.id for evaluation in self.aim_evaluations.values()
                if verdict.id in evaluation.verdict_ids
                and self.aim_evaluation_validity.get(evaluation.id) is not VerdictValidity.INVALIDATED
            )
            evaluations = self._with_citing_parent_evaluations(evaluations)
            for evaluation_id in evaluations:
                self.aim_evaluation_validity[evaluation_id] = VerdictValidity.UNDER_REVIEW
            review = ImpactReview(self._new_id("impact-review"), verdict.id, correction.id, affected, evaluations)
            self.impact_reviews[review.id] = review
        return correction

    def _with_citing_parent_evaluations(self, evaluation_ids: tuple[str, ...]) -> tuple[str, ...]:
        """A parent evaluation that cites an affected sub-Aim evaluation is reviewed in the same Impact Review."""
        affected = list(evaluation_ids)
        index = 0
        while index < len(affected):
            for evaluation in self.aim_evaluations.values():
                if (affected[index] in evaluation.child_evaluation_ids and evaluation.id not in affected
                        and self.aim_evaluation_validity.get(evaluation.id) is not VerdictValidity.INVALIDATED):
                    affected.append(evaluation.id)
            index += 1
        return tuple(affected)

    def review_verdict_after_correction(self, verdict_id: str, correction_id: str) -> ImpactReview:
        verdict = self._require(self.verdicts, verdict_id, "Verdict")
        correction = self._require(self.evidence_corrections, correction_id, "Evidence correction")
        if correction.corrected_evidence_id not in verdict.evidence_ids:
            raise GoalLoopError("Correction must target Evidence cited by the Verdict.")
        if self.verdict_validity.get(verdict_id) is VerdictValidity.INVALIDATED:
            raise GoalLoopError("An invalidated Verdict is terminal.")
        self.verdict_validity[verdict_id] = VerdictValidity.UNDER_REVIEW
        affected = tuple(sorted(
            goal_id for goal_id, dependencies in self.verdict_dependencies.items()
            if any(
                (dependency.required_verdict_id if isinstance(dependency, VerdictDependency) else dependency) == verdict_id
                for dependency in dependencies
            )
        ))
        review = ImpactReview(self._new_id("impact-review"), verdict_id, correction_id, affected)
        self.impact_reviews[review.id] = review
        return review

    def invalidate_verdict(self, verdict_id: str) -> None:
        self._require(self.verdicts, verdict_id, "Verdict")
        if self.verdict_validity.get(verdict_id) is not VerdictValidity.UNDER_REVIEW:
            raise GoalLoopError("Only a Verdict under review can be invalidated.")
        self.verdict_validity[verdict_id] = VerdictValidity.INVALIDATED

    def resolve_verdict_review(
        self,
        impact_review_id: str,
        disposition: ReviewDisposition,
        decided_by_actor_id: str,
        reason: str,
    ) -> ReviewResolution:
        review = self._require(self.impact_reviews, impact_review_id, "Impact Review")
        verdict_id = review.trigger_verdict_id
        if not reason.strip() or self.verdict_validity.get(verdict_id) is not VerdictValidity.UNDER_REVIEW:
            raise GoalLoopError("Only an under-review Verdict with a reason can be resolved.")
        resolution = ReviewResolution(
            self._new_id("review-resolution"), review.id, "verdict", verdict_id,
            disposition, decided_by_actor_id, reason,
        )
        self.review_resolutions[resolution.id] = resolution
        if disposition is ReviewDisposition.INVALIDATE:
            self.verdict_validity[verdict_id] = VerdictValidity.INVALIDATED
            return resolution
        related_reviews = [r.id for r in self.impact_reviews.values() if r.trigger_verdict_id == verdict_id]
        reaffirmed = {
            r.impact_review_id for r in self.review_resolutions.values()
            if r.subject_kind == "verdict" and r.subject_id == verdict_id
            and r.disposition is ReviewDisposition.REAFFIRM
        }
        if all(review_id in reaffirmed for review_id in related_reviews):
            self.verdict_validity[verdict_id] = VerdictValidity.VALID
        return resolution

    def resolve_aim_evaluation_review(
        self,
        evaluation_id: str,
        impact_review_id: str,
        disposition: ReviewDisposition,
        decided_by_actor_id: str,
        reason: str,
    ) -> ReviewResolution:
        evaluation = self._require(self.aim_evaluations, evaluation_id, "Aim Evaluation")
        review = self._require(self.impact_reviews, impact_review_id, "Impact Review")
        if evaluation_id not in review.affected_aim_evaluation_ids or not reason.strip():
            raise GoalLoopError("This Impact Review does not affect the Aim Evaluation.")
        resolution = ReviewResolution(
            self._new_id("review-resolution"), review.id, "aim_evaluation", evaluation_id,
            disposition, decided_by_actor_id, reason,
        )
        self.review_resolutions[resolution.id] = resolution
        if disposition is ReviewDisposition.INVALIDATE:
            self.aim_evaluation_validity[evaluation_id] = VerdictValidity.INVALIDATED
        elif (all(self.verdict_validity.get(verdict_id) is VerdictValidity.VALID for verdict_id in evaluation.verdict_ids)
                and all(self.aim_evaluation_validity.get(child_id) is VerdictValidity.VALID for child_id in evaluation.child_evaluation_ids)):
            self.aim_evaluation_validity[evaluation_id] = VerdictValidity.VALID
        return resolution

    def is_currently_achieved(self, aim_id: str) -> bool:
        self._require(self.aims, aim_id, "Aim")
        return any(
            evaluation.aim_id == aim_id and self.aim_evaluation_validity.get(evaluation.id) is VerdictValidity.VALID
            for evaluation in self.aim_evaluations.values()
        )

    def reopen_aim_after_closure_invalidation(
        self, aim_id: str, evaluation_id: str, triggering_record_id: str,
        decided_by_actor_id: str, reason: str,
    ) -> AimClosureInvalidation:
        aim = self._require(self.aims, aim_id, "Aim")
        evaluation = self._require(self.aim_evaluations, evaluation_id, "Aim Evaluation")
        if (evaluation.aim_id != aim_id
                or self.aim_evaluation_validity.get(evaluation_id) is not VerdictValidity.INVALIDATED
                or self.is_currently_achieved(aim_id) or not reason.strip()):
            raise GoalLoopError("Aim may reopen only after its unsupported closure is invalidated.")
        record = AimClosureInvalidation(
            self._new_id("aim-closure-invalidation"), aim_id, evaluation_id,
            triggering_record_id, decided_by_actor_id, reason,
        )
        self.aim_closure_invalidations[record.id] = record
        aim.phase = AimPhase.OPEN
        return record

    @staticmethod
    def _require(mapping: dict, key: object, label: str):
        try:
            return mapping[key]
        except KeyError as exc:
            raise GoalLoopError(f"{label} {key!r} does not exist.") from exc




