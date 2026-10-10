"""Transaction-first SQLite-backed GoalLoop façade."""
from __future__ import annotations

from dataclasses import asdict
import json
from uuid import uuid4

from .model import (
    AdmissionErrorCode, Aim, AimClosureInvalidation, AimEvaluation, AttemptOutcome,
    Decision, Evidence, EvidenceAttachment, EvidenceCorrection, ExecutionAttempt, Goal, GoalIdentity,
    GoalProposal, Reference, ReviewDisposition, ReviewResolution, VerificationVerdict, VerdictResult,
)
from .repository import (
    CommandConflictError, JournalConflictError, JournalEvent, RuntimeOutboxRequest,
    SqliteEventRepository,
)
from .service import ContractDraft, GoalLoop, GoalLoopError


class PersistentGoalLoop:
    # Concurrent sessions share one journal; a command whose base was overtaken is re-derived.
    _MAX_CONFLICT_RETRIES = 5

    def __init__(self, repository: SqliteEventRepository, *, enforce_authority: bool = False) -> None:
        self.repository = repository
        self.enforce_authority = enforce_authority
        self._loop = self._make_trial()
        if not self.repository.projection_checkpoint_is_current():
            self.repository.mark_projection_checkpoint(self.repository.journal_last_sequence())

    def rebuild_from_journal(self) -> None:
        self._loop = self._make_trial()
        self.repository.mark_projection_checkpoint(self.repository.journal_last_sequence())

    def __getattr__(self, name: str):
        return getattr(self._loop, name)

    def _execute(self, command_id, command_type, request, invoke):
        effective_command_id = command_id or self._event_id()
        fingerprint = json.dumps(
            {"command_type": command_type, "request": request}, sort_keys=True
        )
        existing = self.repository.receipt(effective_command_id)
        if existing is not None:
            if existing.request_fingerprint != fingerprint:
                raise CommandConflictError("Command id conflicts with a different request.")
            if existing.status == "rejected":
                self._raise_rejected(existing.error_code, existing.error_message)
            return self._result_from_receipt(existing.result)

        for attempt in range(self._MAX_CONFLICT_RETRIES + 1):
            # Read the position before replaying: a commit landing in between only causes a retry.
            base_sequence = self.repository.journal_last_sequence()
            trial = self._make_trial()
            try:
                invoked = invoke(trial)
                result, event, *extra = invoked
            except GoalLoopError as error:
                error_code = error.code.value if error.code is not None else "goal_loop_error"
                self.repository.reject(
                    command_id=effective_command_id,
                    command_type=command_type,
                    request_fingerprint=fingerprint,
                    error_code=error_code,
                    error_message=str(error),
                )
                raise
            try:
                receipt = self.repository.commit(
                    command_id=effective_command_id,
                    command_type=command_type,
                    request_fingerprint=fingerprint,
                    events=(event,),
                    result={
                        "void": result is None,
                        "id": getattr(result, "id", None),
                        "goal_id": getattr(result, "goal_id", None),
                        "version": getattr(result, "version", None),
                    },
                    runtime_requests=extra[0] if extra else (),
                    expected_last_sequence=base_sequence,
                )
            except JournalConflictError:
                if attempt == self._MAX_CONFLICT_RETRIES:
                    raise
                continue
            if receipt.status == "rejected":
                self._raise_rejected(receipt.error_code, receipt.error_message)
            if receipt.first_sequence != base_sequence + 1:
                # Another writer committed this same command id first; report its recorded result.
                self._loop = self._make_trial()
                return self._result_from_receipt(receipt.result)
            self._loop = trial
            return result

    def _result_from_receipt(self, result):
        if not result:
            raise RuntimeError("Committed command receipt has no result.")
        if result.get("void"):
            return None
        record_id = result.get("id")
        if record_id is not None:
            for records in (
                self._loop.aims, self._loop.aim_evaluations, self._loop.proposals,
                self._loop.goals, self._loop.attempts, self._loop.evidence,
                self._loop.decisions, self._loop.verdicts,
                self._loop.evidence_corrections, self._loop.impact_reviews,
                self._loop.review_resolutions, self._loop.aim_closure_invalidations,
                self._loop.references,
            ):
                if record_id in records:
                    return records[record_id]
        goal_id = result.get("goal_id")
        version = result.get("version")
        if goal_id is not None and version is not None:
            return self._loop.contracts[(goal_id, version)]
        raise RuntimeError("Committed command receipt cannot be resolved from replayed state.")

    @staticmethod
    def _raise_rejected(error_code, error_message):
        try:
            code = AdmissionErrorCode(error_code)
        except ValueError:
            code = None
        raise GoalLoopError(error_message or "Previously rejected command.", code)

    def create_aim(self, description: str, parent_aim_id: str | None = None, *, command_id=None) -> Aim:
        request = {"description": description}
        if parent_aim_id is not None:
            # Only present when used, so fingerprints of earlier aim-create commands stay identical.
            request["parent_aim_id"] = parent_aim_id
        return self._execute(command_id, "create_aim", request, lambda loop: self._aim_event(loop, description, parent_aim_id))

    def _aim_event(self, loop, description, parent_aim_id=None):
        aim = loop.create_aim(description, parent_aim_id)
        return aim, JournalEvent(self._event_id(), "AimCreated", "system", "aim", aim.id, asdict(aim))

    def link_aim_parent(self, child_aim_id: str, parent_aim_id: str, *, command_id=None) -> Aim:
        def invoke(loop):
            aim = loop.link_aim_parent(child_aim_id, parent_aim_id)
            payload = {"child_aim_id": child_aim_id, "parent_aim_id": parent_aim_id}
            return aim, JournalEvent(self._event_id(), "AimParentLinked", "system", "aim", child_aim_id, payload)
        return self._execute(command_id, "link_aim_parent", {"child_aim_id": child_aim_id, "parent_aim_id": parent_aim_id}, invoke)

    def create_proposal(self, aim_id: str, direction: str, proposed_by_actor_id: str | None = None, *, command_id=None) -> GoalProposal:
        def invoke(loop):
            proposal = loop.create_proposal(aim_id, direction, proposed_by_actor_id)
            return proposal, JournalEvent(self._event_id(), "ProposalCreated", proposed_by_actor_id or "system", "proposal", proposal.id, asdict(proposal))
        request = {"aim_id": aim_id, "direction": direction}
        if proposed_by_actor_id is not None:
            request["proposed_by_actor_id"] = proposed_by_actor_id
        return self._execute(command_id, "create_proposal", request, invoke)

    def close_proposal(self, proposal_id: str, closure_kind: str, actor_id: str, reason: str, *, command_id=None) -> GoalProposal:
        def invoke(loop):
            proposal = loop.close_proposal(proposal_id, closure_kind, actor_id, reason)
            payload = {"proposal_id": proposal_id, "closure_kind": closure_kind, "actor_id": actor_id, "reason": reason}
            return proposal, JournalEvent(self._event_id(), "ProposalClosed", actor_id, "proposal", proposal_id, payload)
        return self._execute(command_id, "close_proposal", {"proposal_id": proposal_id, "closure_kind": closure_kind, "actor_id": actor_id, "reason": reason}, invoke)

    def stop_goal(self, goal_id: str, actor_id: str, reason: str, *, command_id=None) -> Goal:
        def invoke(loop):
            goal = loop.stop_goal(goal_id, actor_id, reason)
            payload = {"goal_id": goal_id, "actor_id": actor_id, "reason": reason}
            return goal, JournalEvent(self._event_id(), "GoalStopped", actor_id, "goal", goal_id, payload)
        return self._execute(command_id, "stop_goal", {"goal_id": goal_id, "actor_id": actor_id, "reason": reason}, invoke)

    def abandon_aim(self, aim_id: str, actor_id: str, reason: str, *, command_id=None) -> Aim:
        def invoke(loop):
            aim = loop.abandon_aim(aim_id, actor_id, reason)
            payload = {"aim_id": aim_id, "actor_id": actor_id, "reason": reason}
            return aim, JournalEvent(self._event_id(), "AimAbandoned", actor_id, "aim", aim_id, payload)
        return self._execute(command_id, "abandon_aim", {"aim_id": aim_id, "actor_id": actor_id, "reason": reason}, invoke)

    def revise_proposal(self, proposal_id: str, direction: str, actor_id: str, reason: str, *, command_id=None) -> GoalProposal:
        def invoke(loop):
            successor = loop.revise_proposal(proposal_id, direction, actor_id, reason)
            payload = {
                "previous_proposal_id": proposal_id,
                "replacement_proposal_id": successor.id,
                "direction": direction,
                "actor_id": actor_id,
                "reason": reason,
            }
            return successor, JournalEvent(
                self._event_id(), "ProposalSuperseded", actor_id, "proposal", proposal_id, payload
            )
        return self._execute(
            command_id, "revise_proposal",
            {"proposal_id": proposal_id, "direction": direction, "actor_id": actor_id, "reason": reason}, invoke,
        )

    def accept_proposal(self, proposal_id: str, identity: GoalIdentity, contract_draft: ContractDraft, *, idempotency_key: str, command_id=None) -> Goal:
        def invoke(loop):
            goal = loop.accept_proposal(proposal_id, identity, contract_draft, idempotency_key=idempotency_key)
            payload = {"proposal_id": proposal_id, "goal_id": goal.id, "identity": asdict(identity), "contract": asdict(contract_draft), "idempotency_key": idempotency_key}
            return goal, JournalEvent(self._event_id(), "ProposalAccepted", "system", "proposal", proposal_id, payload)
        return self._execute(
            command_id, "accept_proposal",
            {"proposal_id": proposal_id, "identity": asdict(identity), "contract": asdict(contract_draft), "idempotency_key": idempotency_key},
            invoke,
        )

    def start_attempt(self, goal_id, contract_version, executor_actor_id, operation, *, retry_decision_id=None, reverification_decision_id=None, command_id=None) -> ExecutionAttempt:
        def invoke(loop):
            attempt = loop.start_attempt(
                goal_id, contract_version, executor_actor_id, operation,
                retry_decision_id=retry_decision_id,
                reverification_decision_id=reverification_decision_id,
            )
            payload = {"goal_id": goal_id, "contract_version": contract_version, "executor_actor_id": executor_actor_id, "operation": operation, "retry_decision_id": retry_decision_id, "reverification_decision_id": reverification_decision_id, "attempt_id": attempt.id}
            return attempt, JournalEvent(self._event_id(), "AttemptStarted", executor_actor_id, "attempt", attempt.id, payload)
        return self._execute(
            command_id, "start_attempt",
            {"goal_id": goal_id, "contract_version": contract_version, "executor_actor_id": executor_actor_id, "operation": operation, "retry_decision_id": retry_decision_id, "reverification_decision_id": reverification_decision_id},
            invoke,
        )

    def start_runtime_attempt(
        self,
        goal_id,
        contract_version,
        executor_actor_id,
        operation,
        *,
        request_kind: str,
        request_payload: dict[str, object],
        request_id: str | None = None,
        retry_decision_id=None,
        reverification_decision_id=None,
        command_id=None,
    ) -> ExecutionAttempt:
        """Start one Attempt and atomically enqueue its external Runtime request.

        This records no new domain event type: the outbox is a technical delivery
        projection, while ``AttemptStarted`` remains the canonical domain fact.
        Callers that need replay-safe request identity should supply both IDs.
        """
        effective_request_id = request_id or f"runtime-request-{uuid4().hex}"

        def invoke(loop):
            attempt = loop.start_attempt(
                goal_id, contract_version, executor_actor_id, operation,
                retry_decision_id=retry_decision_id,
                reverification_decision_id=reverification_decision_id,
            )
            payload = {
                "goal_id": goal_id,
                "contract_version": contract_version,
                "executor_actor_id": executor_actor_id,
                "operation": operation,
                "retry_decision_id": retry_decision_id,
                "reverification_decision_id": reverification_decision_id,
                "attempt_id": attempt.id,
            }
            event = JournalEvent(
                self._event_id(), "AttemptStarted", executor_actor_id,
                "attempt", attempt.id, payload,
            )
            request = RuntimeOutboxRequest(
                effective_request_id, attempt.id, request_kind, request_payload,
                "pending",
            )
            return attempt, event, (request,)

        return self._execute(
            command_id,
            "start_runtime_attempt",
            {
                "goal_id": goal_id,
                "contract_version": contract_version,
                "executor_actor_id": executor_actor_id,
                "operation": operation,
                "request_kind": request_kind,
                "request_payload": request_payload,
                "request_id": effective_request_id,
                "retry_decision_id": retry_decision_id,
                "reverification_decision_id": reverification_decision_id,
            },
            invoke,
        )

    def complete_attempt(self, attempt_id, outcome: AttemptOutcome, *, command_id=None) -> ExecutionAttempt:
        def invoke(loop):
            attempt = loop.complete_attempt(attempt_id, outcome)
            payload = {"attempt_id": attempt_id, "outcome": outcome.value}
            return attempt, JournalEvent(self._event_id(), "AttemptCompleted", "system", "attempt", attempt_id, payload)
        return self._execute(command_id, "complete_attempt", {"attempt_id": attempt_id, "outcome": outcome.value}, invoke)

    def decide_same_contract_retry(self, goal_id, failed_attempt_id, actor_id, reason, *, command_id=None) -> Decision:
        def invoke(loop):
            decision = loop.decide_same_contract_retry(goal_id, failed_attempt_id, actor_id, reason)
            payload = {"goal_id": goal_id, "failed_attempt_id": failed_attempt_id, "actor_id": actor_id, "reason": reason, "decision_id": decision.id}
            return decision, JournalEvent(self._event_id(), "DecisionRecorded", actor_id, "decision", decision.id, payload)
        return self._execute(command_id, "decide_same_contract_retry", {"goal_id": goal_id, "failed_attempt_id": failed_attempt_id, "actor_id": actor_id, "reason": reason}, invoke)

    def grant_execute(self, actor_id, goal_id, *, command_id=None) -> None:
        def invoke(loop):
            loop.grant_execute(actor_id, goal_id)
            payload = {"actor_id": actor_id, "goal_id": goal_id}
            return None, JournalEvent(
                self._event_id(), "ExecutionGranted", actor_id, "goal", goal_id, payload
            )
        return self._execute(command_id, "grant_execute", {"actor_id": actor_id, "goal_id": goal_id}, invoke)

    def revoke_execute(self, actor_id, goal_id, *, command_id=None) -> None:
        def invoke(loop):
            loop.revoke_execute(actor_id, goal_id)
            payload = {"actor_id": actor_id, "goal_id": goal_id}
            return None, JournalEvent(
                self._event_id(), "ExecutionRevoked", actor_id, "goal", goal_id, payload
            )
        return self._execute(command_id, "revoke_execute", {"actor_id": actor_id, "goal_id": goal_id}, invoke)

    def add_goal_hold(self, goal_id, actor_id: str = "system", reason: str | None = None, *, command_id=None) -> None:
        def invoke(loop):
            loop.add_goal_hold(goal_id, actor_id, reason)
            payload = {"goal_id": goal_id, "actor_id": actor_id, "reason": reason}
            return None, JournalEvent(self._event_id(), "GoalHoldAdded", actor_id, "goal", goal_id, payload)
        request = {"goal_id": goal_id}
        if reason is not None:
            request.update({"actor_id": actor_id, "reason": reason})
        return self._execute(command_id, "add_goal_hold", request, invoke)

    def clear_goal_hold(self, goal_id, *, command_id=None) -> None:
        def invoke(loop):
            loop.clear_goal_hold(goal_id)
            return None, JournalEvent(
                self._event_id(), "GoalHoldCleared", "system", "goal", goal_id, {"goal_id": goal_id}
            )
        return self._execute(command_id, "clear_goal_hold", {"goal_id": goal_id}, invoke)

    def pause_aim(self, aim_id, actor_id="system", reason="paused", *, command_id=None) -> Decision:
        def invoke(loop):
            decision = loop.pause_aim(aim_id, actor_id, reason)
            payload = {"aim_id": aim_id, "actor_id": actor_id, "reason": reason, "decision_id": decision.id}
            return decision, JournalEvent(
                self._event_id(), "AimPaused", actor_id, "aim", aim_id, payload
            )
        return self._execute(command_id, "pause_aim", {"aim_id": aim_id, "actor_id": actor_id, "reason": reason}, invoke)

    def resume_aim(self, aim_id, actor_id, reason, *, command_id=None) -> Decision:
        def invoke(loop):
            decision = loop.resume_aim(aim_id, actor_id, reason)
            payload = {"aim_id": aim_id, "actor_id": actor_id, "reason": reason, "decision_id": decision.id}
            return decision, JournalEvent(
                self._event_id(), "AimResumed", actor_id, "aim", aim_id, payload
            )
        return self._execute(command_id, "resume_aim", {"aim_id": aim_id, "actor_id": actor_id, "reason": reason}, invoke)

    def decide_reverification_requires_new_attempt(self, goal_id, actor_id, reason, *, command_id=None) -> Decision:
        def invoke(loop):
            decision = loop.decide_reverification_requires_new_attempt(goal_id, actor_id, reason)
            payload = {"goal_id": goal_id, "actor_id": actor_id, "reason": reason, "decision_id": decision.id}
            return decision, JournalEvent(
                self._event_id(), "ReverificationDecisionRecorded", actor_id,
                "decision", decision.id, payload
            )
        return self._execute(
            command_id, "decide_reverification_requires_new_attempt", {"goal_id": goal_id, "actor_id": actor_id, "reason": reason}, invoke
        )

    def revise_contract(self, goal_id, contract_draft, *, decided_by_actor_id, reason, command_id=None):
        def invoke(loop):
            contract = loop.revise_contract(goal_id, contract_draft, decided_by_actor_id=decided_by_actor_id, reason=reason)
            payload = {"goal_id": goal_id, "contract": asdict(contract_draft), "actor_id": decided_by_actor_id, "reason": reason}
            return contract, JournalEvent(self._event_id(), "ContractVersionAdded", decided_by_actor_id, "goal", goal_id, payload)
        return self._execute(command_id, "revise_contract", {"goal_id": goal_id, "contract": asdict(contract_draft), "decided_by_actor_id": decided_by_actor_id, "reason": reason}, invoke)

    def record_evidence(self, attempt_id, actor_id, observation, *, attachments=(), command_id=None):
        attachments = tuple(attachments)
        attachment_records = [asdict(attachment) for attachment in attachments]
        def invoke(loop):
            evidence = loop.record_evidence(attempt_id, actor_id, observation, attachments)
            payload = {"attempt_id": attempt_id, "actor_id": actor_id, "observation": observation, "evidence_id": evidence.id}
            if attachment_records:
                payload["attachments"] = attachment_records
            return evidence, JournalEvent(self._event_id(), "EvidenceRecorded", actor_id, "evidence", evidence.id, payload)
        request = {"attempt_id": attempt_id, "actor_id": actor_id, "observation": observation}
        if attachment_records:
            # Only present when used, so fingerprints of commands recorded before attachments stay identical.
            request["attachments"] = attachment_records
        return self._execute(command_id, "record_evidence", request, invoke)

    def add_reference(self, subject_kind, subject_id, location, actor_id, label=None, *, command_id=None) -> Reference:
        def invoke(loop):
            reference = loop.add_reference(subject_kind, subject_id, location, actor_id, label)
            payload = {"subject_kind": subject_kind, "subject_id": subject_id, "location": location, "label": label, "actor_id": actor_id, "reference_id": reference.id}
            return reference, JournalEvent(self._event_id(), "ReferenceAdded", actor_id, subject_kind, subject_id, payload)
        return self._execute(command_id, "add_reference", {"subject_kind": subject_kind, "subject_id": subject_id, "location": location, "label": label, "actor_id": actor_id}, invoke)

    def remove_reference(self, reference_id, actor_id, reason=None, *, command_id=None) -> Reference:
        def invoke(loop):
            reference = loop.remove_reference(reference_id, actor_id, reason)
            payload = {"reference_id": reference_id, "actor_id": actor_id, "reason": reason}
            return reference, JournalEvent(self._event_id(), "ReferenceRemoved", actor_id, reference.subject_kind, reference.subject_id, payload)
        return self._execute(command_id, "remove_reference", {"reference_id": reference_id, "actor_id": actor_id, "reason": reason}, invoke)

    def begin_verification(self, goal_id, attempt_id, *, command_id=None):
        def invoke(loop):
            loop.begin_verification(goal_id, attempt_id)
            payload = {"goal_id": goal_id, "attempt_id": attempt_id}
            return loop.goals[goal_id], JournalEvent(self._event_id(), "VerificationBegan", "system", "goal", goal_id, payload)
        return self._execute(command_id, "begin_verification", {"goal_id": goal_id, "attempt_id": attempt_id}, invoke)

    def submit_verdict(self, goal_id, attempt_id, verifier_actor_id, result, evidence_ids, *, supersedes_verdict_id=None, reason=None, command_id=None):
        reason = (reason or "").strip() or None
        # "reason" is added only when given, so requests and events without it keep their earlier shape and fingerprint.
        extra = {"reason": reason} if reason is not None else {}
        def invoke(loop):
            verdict = loop.submit_verdict(goal_id, attempt_id, verifier_actor_id, result, evidence_ids, supersedes_verdict_id=supersedes_verdict_id, reason=reason)
            payload = {"goal_id": goal_id, "attempt_id": attempt_id, "verifier_actor_id": verifier_actor_id, "result": result.value, "evidence_ids": list(evidence_ids), "supersedes_verdict_id": supersedes_verdict_id, "verdict_id": verdict.id, **extra}
            return verdict, JournalEvent(self._event_id(), "VerdictSubmitted", verifier_actor_id, "verdict", verdict.id, payload)
        return self._execute(command_id, "submit_verdict", {"goal_id": goal_id, "attempt_id": attempt_id, "verifier_actor_id": verifier_actor_id, "result": result.value, "evidence_ids": list(evidence_ids), "supersedes_verdict_id": supersedes_verdict_id, **extra}, invoke)

    def evaluate_aim_as_achieved(
        self, aim_id, evaluator_actor_id, verdict_ids, conclusion, child_evaluation_ids=(), *, command_id=None
    ) -> AimEvaluation:
        child_evaluation_ids = tuple(child_evaluation_ids)
        def invoke(loop):
            evaluation = loop.evaluate_aim_as_achieved(
                aim_id, evaluator_actor_id, tuple(verdict_ids), conclusion, child_evaluation_ids
            )
            payload = {
                "aim_id": aim_id,
                "evaluator_actor_id": evaluator_actor_id,
                "verdict_ids": list(verdict_ids),
                "conclusion": conclusion,
                "evaluation_id": evaluation.id,
            }
            if child_evaluation_ids:
                payload["child_evaluation_ids"] = list(child_evaluation_ids)
            return evaluation, JournalEvent(
                self._event_id(), "AimEvaluated", evaluator_actor_id,
                "aim_evaluation", evaluation.id, payload
            )
        request = {"aim_id": aim_id, "verdict_ids": list(verdict_ids)}
        if child_evaluation_ids:
            request["child_evaluation_ids"] = list(child_evaluation_ids)
        return self._execute(command_id, "evaluate_aim_as_achieved", request, invoke)

    def resolve_aim_evaluation_review(
        self, evaluation_id, impact_review_id, disposition: ReviewDisposition,
        decided_by_actor_id, reason, *, command_id=None
    ) -> ReviewResolution:
        def invoke(loop):
            resolution = loop.resolve_aim_evaluation_review(
                evaluation_id, impact_review_id, disposition, decided_by_actor_id, reason
            )
            payload = {
                "evaluation_id": evaluation_id,
                "impact_review_id": impact_review_id,
                "disposition": disposition.value,
                "decided_by_actor_id": decided_by_actor_id,
                "reason": reason,
                "resolution_id": resolution.id,
            }
            return resolution, JournalEvent(
                self._event_id(), "AimEvaluationReviewResolved", decided_by_actor_id,
                "impact_review", impact_review_id, payload
            )
        return self._execute(
            command_id, "resolve_aim_evaluation_review",
            {"evaluation_id": evaluation_id, "impact_review_id": impact_review_id, "disposition": disposition.value},
            invoke,
        )

    def reopen_aim_after_closure_invalidation(
        self, aim_id, evaluation_id, triggering_record_id, decided_by_actor_id, reason, *, command_id=None
    ) -> AimClosureInvalidation:
        def invoke(loop):
            invalidation = loop.reopen_aim_after_closure_invalidation(
                aim_id, evaluation_id, triggering_record_id, decided_by_actor_id, reason
            )
            payload = {
                "aim_id": aim_id,
                "evaluation_id": evaluation_id,
                "triggering_record_id": triggering_record_id,
                "decided_by_actor_id": decided_by_actor_id,
                "reason": reason,
                "invalidation_id": invalidation.id,
            }
            return invalidation, JournalEvent(
                self._event_id(), "AimClosureInvalidated", decided_by_actor_id,
                "aim", aim_id, payload
            )
        return self._execute(
            command_id, "reopen_aim_after_closure_invalidation",
            {"aim_id": aim_id, "evaluation_id": evaluation_id, "triggering_record_id": triggering_record_id},
            invoke,
        )

    def add_verdict_dependency(
        self, goal_id, verdict_id, required_result: VerdictResult = VerdictResult.SATISFIED, *, command_id=None
    ) -> None:
        def invoke(loop):
            loop.add_verdict_dependency(goal_id, verdict_id, required_result)
            payload = {
                "goal_id": goal_id,
                "verdict_id": verdict_id,
                "required_result": required_result.value,
            }
            return None, JournalEvent(
                self._event_id(), "VerdictDependencyAdded", "system", "goal", goal_id, payload
            )
        return self._execute(
            command_id, "add_verdict_dependency",
            {"goal_id": goal_id, "verdict_id": verdict_id, "required_result": required_result.value},
            invoke,
        )

    def add_goal_parent(self, child_goal_id, parent_goal_id, *, command_id=None) -> None:
        def invoke(loop):
            loop.add_goal_parent(child_goal_id, parent_goal_id)
            payload = {"child_goal_id": child_goal_id, "parent_goal_id": parent_goal_id}
            return None, JournalEvent(self._event_id(), "GoalParentLinked", "system", "goal", child_goal_id, payload)
        return self._execute(command_id, "add_goal_parent", {"child_goal_id": child_goal_id, "parent_goal_id": parent_goal_id}, invoke)

    def add_goal_dependency(self, dependent_goal_id, required_goal_id, *, command_id=None) -> None:
        def invoke(loop):
            loop.add_goal_dependency(dependent_goal_id, required_goal_id)
            payload = {"dependent_goal_id": dependent_goal_id, "required_goal_id": required_goal_id}
            return None, JournalEvent(self._event_id(), "GoalDependencyLinked", "system", "goal", dependent_goal_id, payload)
        return self._execute(command_id, "add_goal_dependency", {"dependent_goal_id": dependent_goal_id, "required_goal_id": required_goal_id}, invoke)

    def add_goal_follow(self, goal_id, previous_goal_id, actor_id, reason, *, command_id=None) -> None:
        def invoke(loop):
            loop.add_goal_follow(goal_id, previous_goal_id, actor_id, reason)
            payload = {"goal_id": goal_id, "previous_goal_id": previous_goal_id, "actor_id": actor_id, "reason": reason}
            return None, JournalEvent(self._event_id(), "GoalFollowLinked", actor_id, "goal", goal_id, payload)
        return self._execute(command_id, "add_goal_follow", {"goal_id": goal_id, "previous_goal_id": previous_goal_id}, invoke)

    def correct_evidence(self, evidence_id, actor_id, observation, *, command_id=None) -> EvidenceCorrection:
        def invoke(loop):
            correction = loop.correct_evidence(evidence_id, actor_id, observation)
            reviews = [
                review for review in loop.impact_reviews.values()
                if review.correction_id == correction.id
            ]
            payload = {
                "evidence_id": evidence_id,
                "actor_id": actor_id,
                "observation": observation,
                "correction_id": correction.id,
                "impact_review_ids": [review.id for review in reviews],
            }
            return correction, JournalEvent(
                self._event_id(), "EvidenceCorrected", actor_id, "evidence", evidence_id, payload
            )
        return self._execute(
            command_id, "correct_evidence", {"evidence_id": evidence_id, "actor_id": actor_id, "observation": observation}, invoke
        )

    def review_verdict_after_correction(self, verdict_id, correction_id, *, command_id=None):
        def invoke(loop):
            review = loop.review_verdict_after_correction(verdict_id, correction_id)
            payload = {
                "verdict_id": verdict_id,
                "correction_id": correction_id,
                "impact_review_id": review.id,
            }
            return review, JournalEvent(
                self._event_id(), "VerdictReviewOpened", "system", "verdict", verdict_id, payload
            )
        return self._execute(
            command_id, "review_verdict_after_correction",
            {"verdict_id": verdict_id, "correction_id": correction_id}, invoke,
        )

    def invalidate_verdict(self, verdict_id, *, command_id=None) -> None:
        def invoke(loop):
            loop.invalidate_verdict(verdict_id)
            return None, JournalEvent(
                self._event_id(), "VerdictInvalidated", "system", "verdict", verdict_id,
                {"verdict_id": verdict_id},
            )
        return self._execute(
            command_id, "invalidate_verdict", {"verdict_id": verdict_id}, invoke
        )

    def resolve_verdict_review(
        self, impact_review_id, disposition: ReviewDisposition, decided_by_actor_id, reason, *, command_id=None
    ) -> ReviewResolution:
        def invoke(loop):
            resolution = loop.resolve_verdict_review(
                impact_review_id, disposition, decided_by_actor_id, reason
            )
            payload = {
                "impact_review_id": impact_review_id,
                "disposition": disposition.value,
                "decided_by_actor_id": decided_by_actor_id,
                "reason": reason,
                "resolution_id": resolution.id,
            }
            return resolution, JournalEvent(
                self._event_id(), "VerdictReviewResolved", decided_by_actor_id,
                "impact_review", impact_review_id, payload
            )
        return self._execute(
            command_id, "resolve_verdict_review",
            {"impact_review_id": impact_review_id, "disposition": disposition.value, "decided_by_actor_id": decided_by_actor_id, "reason": reason}, invoke,
        )

    def _make_trial(self):
        loop = GoalLoop(enforce_authority=self.enforce_authority)
        snapshot_events = self.repository.latest_valid_snapshot_events()
        events = snapshot_events + self.repository.events()[len(snapshot_events):] if snapshot_events is not None else self.repository.events()
        for event in events:
            p = event.payload
            if event.event_type == "AimCreated": loop.create_aim(p["description"], p.get("parent_aim_id"))
            elif event.event_type == "AimParentLinked": loop.link_aim_parent(p["child_aim_id"], p["parent_aim_id"])
            elif event.event_type == "ProposalCreated": loop.create_proposal(p["aim_id"], p["direction"], p.get("proposed_by_actor_id"))
            elif event.event_type == "ProposalClosed": loop.close_proposal(p["proposal_id"], p["closure_kind"], p["actor_id"], p["reason"])
            elif event.event_type == "GoalStopped": loop.stop_goal(p["goal_id"], p["actor_id"], p["reason"])
            elif event.event_type == "AimAbandoned": loop.abandon_aim(p["aim_id"], p["actor_id"], p["reason"])
            elif event.event_type == "ProposalSuperseded": loop.revise_proposal(p["previous_proposal_id"], p["direction"], p["actor_id"], p["reason"])
            elif event.event_type == "ProposalAccepted": loop.accept_proposal(p["proposal_id"], GoalIdentity(**p["identity"]), ContractDraft(**p["contract"]), idempotency_key=p["idempotency_key"])
            elif event.event_type == "AttemptStarted": loop.start_attempt(p["goal_id"], p["contract_version"], p["executor_actor_id"], p["operation"], retry_decision_id=p["retry_decision_id"], reverification_decision_id=p.get("reverification_decision_id"))
            elif event.event_type == "AttemptCompleted": loop.complete_attempt(p["attempt_id"], AttemptOutcome(p["outcome"]))
            elif event.event_type == "DecisionRecorded": loop.decide_same_contract_retry(p["goal_id"], p["failed_attempt_id"], p["actor_id"], p["reason"])
            elif event.event_type == "ExecutionGranted": loop.grant_execute(p["actor_id"], p["goal_id"])
            elif event.event_type == "ExecutionRevoked": loop.revoke_execute(p["actor_id"], p["goal_id"])
            elif event.event_type == "GoalHoldAdded": loop.add_goal_hold(p["goal_id"], p.get("actor_id", "system"), p.get("reason"))
            elif event.event_type == "GoalHoldCleared": loop.clear_goal_hold(p["goal_id"])
            elif event.event_type == "AimPaused": loop.pause_aim(p["aim_id"], p["actor_id"], p["reason"])
            elif event.event_type == "AimResumed": loop.resume_aim(p["aim_id"], p["actor_id"], p["reason"])
            elif event.event_type == "ReverificationDecisionRecorded": loop.decide_reverification_requires_new_attempt(p["goal_id"], p["actor_id"], p["reason"])
            elif event.event_type == "ContractVersionAdded": loop.revise_contract(p["goal_id"], ContractDraft(**p["contract"]), decided_by_actor_id=p["actor_id"], reason=p["reason"])
            elif event.event_type == "EvidenceRecorded": loop.record_evidence(p["attempt_id"], p["actor_id"], p["observation"], tuple(EvidenceAttachment(**item) for item in p.get("attachments", [])))
            elif event.event_type == "ReferenceAdded": loop.add_reference(p["subject_kind"], p["subject_id"], p["location"], p["actor_id"], p["label"])
            elif event.event_type == "ReferenceRemoved": loop.remove_reference(p["reference_id"], p["actor_id"], p["reason"])
            elif event.event_type == "VerificationBegan": loop.begin_verification(p["goal_id"], p["attempt_id"])
            elif event.event_type == "VerdictSubmitted": loop.submit_verdict(p["goal_id"], p["attempt_id"], p["verifier_actor_id"], VerdictResult(p["result"]), tuple(p["evidence_ids"]), supersedes_verdict_id=p["supersedes_verdict_id"], reason=p.get("reason"))
            elif event.event_type == "VerdictDependencyAdded": loop.add_verdict_dependency(p["goal_id"], p["verdict_id"], VerdictResult(p["required_result"]))
            # Rules added later never reject facts already in the journal.
            elif event.event_type == "GoalParentLinked": loop.add_goal_parent(p["child_goal_id"], p["parent_goal_id"], allow_legacy_deadlock=True)
            elif event.event_type == "GoalDependencyLinked": loop.add_goal_dependency(p["dependent_goal_id"], p["required_goal_id"], allow_legacy_deadlock=True)
            elif event.event_type == "GoalFollowLinked": loop.add_goal_follow(p["goal_id"], p["previous_goal_id"], p["actor_id"], p["reason"])
            elif event.event_type == "EvidenceCorrected": loop.correct_evidence(p["evidence_id"], p["actor_id"], p["observation"])
            elif event.event_type == "VerdictReviewOpened": loop.review_verdict_after_correction(p["verdict_id"], p["correction_id"])
            elif event.event_type == "VerdictInvalidated": loop.invalidate_verdict(p["verdict_id"])
            elif event.event_type == "VerdictReviewResolved": loop.resolve_verdict_review(p["impact_review_id"], ReviewDisposition(p["disposition"]), p["decided_by_actor_id"], p["reason"])
            elif event.event_type == "AimEvaluated": loop.evaluate_aim_as_achieved(p["aim_id"], p["evaluator_actor_id"], tuple(p["verdict_ids"]), p["conclusion"], tuple(p.get("child_evaluation_ids", ())))
            elif event.event_type == "AimEvaluationReviewResolved": loop.resolve_aim_evaluation_review(p["evaluation_id"], p["impact_review_id"], ReviewDisposition(p["disposition"]), p["decided_by_actor_id"], p["reason"])
            elif event.event_type == "AimClosureInvalidated": loop.reopen_aim_after_closure_invalidation(p["aim_id"], p["evaluation_id"], p["triggering_record_id"], p["decided_by_actor_id"], p["reason"])
            else: raise ValueError(f"Unsupported persistent event {event.event_type!r}.")
        return loop

    @staticmethod
    def _event_id(): return f"event_{uuid4().hex}"
