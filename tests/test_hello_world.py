from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from goal_loop_tool import (
    AttemptOutcome,
    ContractDraft,
    GoalLoop,
    GoalLoopError,
    GoalPhase,
    ProposalStatus,
    VerdictResult,
)
from goal_loop_tool.model import AimPhase, GoalIdentity, ReviewDisposition, VerdictValidity


class HelloWorldVerticalSliceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.loop = GoalLoop()
        self.aim = self.loop.create_aim("검증 가능한 텍스트 산출물을 만든다.")
        self.proposal = self.loop.create_proposal(
            self.aim.id,
            "지정한 내용을 가진 텍스트 파일을 만든다.",
        )
        self.identity = GoalIdentity(
            primary_aim_id=self.aim.id,
            target_and_desired_result="artifacts/hello.txt에 지정한 텍스트를 만든다.",
            responsibility_kind="텍스트 산출물을 생성하고 검증한다.",
        )
        self.contract = ContractDraft(
            operational_scope="로컬 artifacts 디렉터리",
            success_criterion="hello.txt의 내용이 정확히 hello-goal-loop다.",
            verification_specification="실행자와 다른 verifier Actor가 파일 내용을 직접 읽는다.",
        )

    def accept(self):
        return self.loop.accept_proposal(
            self.proposal.id,
            self.identity,
            self.contract,
            idempotency_key="accept-hello-1",
        )

    def test_happy_path_with_failed_attempt_and_same_contract_retry(self) -> None:
        goal = self.accept()
        self.assertEqual(self.proposal.status, ProposalStatus.ACCEPTED)
        self.assertEqual(self.proposal.accepted_goal_id, goal.id)
        self.assertEqual(goal.phase, GoalPhase.COMMITTED)

        first = self.loop.start_attempt(goal.id, 1, "executor-agent", "write hello file")
        self.loop.complete_attempt(first.id, AttemptOutcome.FAILED)
        failed_evidence = self.loop.record_evidence(first.id, "executor-agent", "target path unavailable")
        retry = self.loop.decide_same_contract_retry(goal.id, first.id, "policy-admin", "path setup repaired")

        self.assertEqual(retry.kind, "retry_same_contract")
        self.assertEqual(goal.phase, GoalPhase.ACTIVE)
        self.assertEqual(self.loop.contracts[(goal.id, 1)].version, 1)
        self.assertEqual(failed_evidence.contract_version, 1)

        second = self.loop.start_attempt(goal.id, 1, "executor-agent", "write hello file", retry_decision_id=retry.id)
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "hello.txt"
            output.write_text("hello-goal-loop", encoding="utf-8")
            self.loop.complete_attempt(second.id, AttemptOutcome.SUCCEEDED)
            evidence = self.loop.record_evidence(
                second.id,
                "verifier-agent",
                f"verifier read: {output.read_text(encoding='utf-8')}",
            )

        self.loop.begin_verification(goal.id, second.id)
        verdict = self.loop.submit_verdict(
            goal.id,
            second.id,
            "verifier-agent",
            VerdictResult.SATISFIED,
            (evidence.id,),
        )

        self.assertEqual(verdict.result, VerdictResult.SATISFIED)
        self.assertEqual(goal.phase, GoalPhase.VERIFIED)
        self.assertEqual(self.aim.phase, AimPhase.OPEN)

    def test_duplicate_acceptance_returns_the_same_goal(self) -> None:
        first = self.accept()
        second = self.accept()

        self.assertEqual(first.id, second.id)
        self.assertEqual(len(self.loop.goals), 1)
        self.assertEqual(len(self.loop.contracts), 1)

    def test_idempotency_key_rejects_a_different_acceptance_request(self) -> None:
        self.accept()
        other = self.loop.create_proposal(self.aim.id, "different work")
        with self.assertRaises(GoalLoopError):
            self.loop.accept_proposal(
                other.id,
                GoalIdentity(self.aim.id, "different target", "different responsibility"),
                self.contract,
                idempotency_key="accept-hello-1",
            )

    def test_executor_cannot_issue_its_own_independent_final_verdict(self) -> None:
        goal = self.accept()
        attempt = self.loop.start_attempt(goal.id, 1, "executor-agent", "write hello file")
        self.loop.complete_attempt(attempt.id, AttemptOutcome.SUCCEEDED)
        evidence = self.loop.record_evidence(attempt.id, "executor-agent", "file contains hello-goal-loop")
        self.loop.begin_verification(goal.id, attempt.id)

        with self.assertRaises(GoalLoopError):
            self.loop.submit_verdict(
                goal.id,
                attempt.id,
                "executor-agent",
                VerdictResult.SATISFIED,
                (evidence.id,),
            )

        self.assertEqual(goal.phase, GoalPhase.VERIFYING)

    def test_verdict_rejects_other_attempt_and_other_attempt_evidence(self) -> None:
        goal = self.accept()
        verified_attempt = self.loop.start_attempt(goal.id, 1, "executor-a", "write")
        self.loop.complete_attempt(verified_attempt.id, AttemptOutcome.SUCCEEDED)
        verified_evidence = self.loop.record_evidence(verified_attempt.id, "verifier", "good")
        other_attempt = self.loop.start_attempt(goal.id, 1, "executor-b", "write")
        self.loop.complete_attempt(other_attempt.id, AttemptOutcome.FAILED)
        self.loop.begin_verification(goal.id, verified_attempt.id)
        with self.assertRaises(GoalLoopError):
            self.loop.submit_verdict(goal.id, other_attempt.id, "verifier", VerdictResult.SATISFIED, (verified_evidence.id,))
        with self.assertRaises(GoalLoopError):
            self.loop.submit_verdict(goal.id, verified_attempt.id, "verifier", VerdictResult.SATISFIED, ())

    def test_correction_immediately_quarantines_dependent_verdict(self) -> None:
        source = self.accept()
        attempt = self.loop.start_attempt(source.id, 1, "executor", "write")
        self.loop.complete_attempt(attempt.id, AttemptOutcome.SUCCEEDED)
        evidence = self.loop.record_evidence(attempt.id, "verifier", "good")
        self.loop.begin_verification(source.id, attempt.id)
        verdict = self.loop.submit_verdict(source.id, attempt.id, "verifier", VerdictResult.SATISFIED, (evidence.id,))
        dependent = self.loop.accept_proposal(self.loop.create_proposal(self.aim.id, "use output").id, GoalIdentity(self.aim.id, "use", "consume"), self.contract, idempotency_key="dependent-quarantine")
        self.loop.add_verdict_dependency(dependent.id, verdict.id)
        self.loop.correct_evidence(evidence.id, "auditor", "wrong")
        self.assertEqual(self.loop.verdict_validity[verdict.id], VerdictValidity.UNDER_REVIEW)
        with self.assertRaises(GoalLoopError):
            self.loop.start_attempt(dependent.id, 1, "executor", "consume")

    def test_corrected_evidence_cannot_be_reused_and_invalidated_verdict_is_terminal(self) -> None:
        goal = self.accept()
        attempt = self.loop.start_attempt(goal.id, 1, "executor", "write")
        self.loop.complete_attempt(attempt.id, AttemptOutcome.SUCCEEDED)
        evidence = self.loop.record_evidence(attempt.id, "verifier", "good")
        self.loop.begin_verification(goal.id, attempt.id)
        verdict = self.loop.submit_verdict(goal.id, attempt.id, "verifier", VerdictResult.SATISFIED, (evidence.id,))
        correction = self.loop.correct_evidence(evidence.id, "auditor", "wrong")
        self.loop.invalidate_verdict(verdict.id)
        with self.assertRaises(GoalLoopError):
            self.loop.review_verdict_after_correction(verdict.id, correction.id)

    def test_failed_retry_and_reverification_decisions_are_consumed(self) -> None:
        goal = self.accept()
        failed = self.loop.start_attempt(goal.id, 1, "executor", "write")
        self.loop.complete_attempt(failed.id, AttemptOutcome.FAILED)
        with self.assertRaises(GoalLoopError):
            self.loop.start_attempt(goal.id, 1, "executor", "retry")
        retry = self.loop.decide_same_contract_retry(goal.id, failed.id, "admin", "retry")
        repaired = self.loop.start_attempt(goal.id, 1, "executor", "retry", retry_decision_id=retry.id)
        self.loop.complete_attempt(repaired.id, AttemptOutcome.SUCCEEDED)
        evidence = self.loop.record_evidence(repaired.id, "verifier", "uncertain")
        self.loop.begin_verification(goal.id, repaired.id)
        self.loop.submit_verdict(goal.id, repaired.id, "verifier", VerdictResult.INCONCLUSIVE, (evidence.id,))
        decision = self.loop.decide_reverification_requires_new_attempt(goal.id, "admin", "new measurement")
        with self.assertRaises(GoalLoopError):
            self.loop.start_attempt(goal.id, 1, "executor", "measure")
        self.loop.start_attempt(goal.id, 1, "executor", "measure", reverification_decision_id=decision.id)

    def test_pause_resume_restores_admission_without_starting_work(self) -> None:
        goal = self.accept()
        self.loop.pause_aim(self.aim.id, "admin", "temporary pause")
        with self.assertRaises(GoalLoopError):
            self.loop.start_attempt(goal.id, 1, "executor", "write")
        self.loop.resume_aim(self.aim.id, "admin", "continue")
        attempt = self.loop.start_attempt(goal.id, 1, "executor", "write")
        self.assertEqual(attempt.outcome, AttemptOutcome.RUNNING)

    def test_aim_evaluation_can_be_invalidated_and_explicitly_reopened(self) -> None:
        goal = self.accept()
        attempt = self.loop.start_attempt(goal.id, 1, "executor", "write")
        self.loop.complete_attempt(attempt.id, AttemptOutcome.SUCCEEDED)
        evidence = self.loop.record_evidence(attempt.id, "verifier", "good")
        self.loop.begin_verification(goal.id, attempt.id)
        verdict = self.loop.submit_verdict(goal.id, attempt.id, "verifier", VerdictResult.SATISFIED, (evidence.id,))
        evaluation = self.loop.evaluate_aim_as_achieved(self.aim.id, "evaluator", (verdict.id,), "sufficient")
        correction = self.loop.correct_evidence(evidence.id, "auditor", "wrong")
        review = next(review for review in self.loop.impact_reviews.values() if review.correction_id == correction.id)
        self.loop.resolve_verdict_review(review.id, ReviewDisposition.INVALIDATE, "auditor", "invalid")
        self.loop.resolve_aim_evaluation_review(evaluation.id, review.id, ReviewDisposition.INVALIDATE, "evaluator", "unsupported")
        self.assertFalse(self.loop.is_currently_achieved(self.aim.id))
        self.loop.reopen_aim_after_closure_invalidation(self.aim.id, evaluation.id, review.id, "admin", "resume pursuit")
        self.assertEqual(self.aim.phase, AimPhase.OPEN)

    def test_multiple_correction_reviews_require_all_reaffirmations(self) -> None:
        goal = self.accept()
        attempt = self.loop.start_attempt(goal.id, 1, "executor", "write")
        self.loop.complete_attempt(attempt.id, AttemptOutcome.SUCCEEDED)
        first = self.loop.record_evidence(attempt.id, "verifier", "first")
        second = self.loop.record_evidence(attempt.id, "verifier", "second")
        self.loop.begin_verification(goal.id, attempt.id)
        verdict = self.loop.submit_verdict(goal.id, attempt.id, "verifier", VerdictResult.SATISFIED, (first.id, second.id))
        one = self.loop.correct_evidence(first.id, "auditor", "question one")
        two = self.loop.correct_evidence(second.id, "auditor", "question two")
        reviews = [r for r in self.loop.impact_reviews.values() if r.trigger_verdict_id == verdict.id]
        self.loop.resolve_verdict_review(reviews[0].id, ReviewDisposition.REAFFIRM, "auditor", "one okay")
        self.assertEqual(self.loop.verdict_validity[verdict.id], VerdictValidity.UNDER_REVIEW)
        self.loop.resolve_verdict_review(reviews[1].id, ReviewDisposition.REAFFIRM, "auditor", "two okay")
        self.assertEqual(self.loop.verdict_validity[verdict.id], VerdictValidity.VALID)

    def test_late_v1_result_cannot_verify_current_v2_contract(self) -> None:
        goal = self.accept()
        v1_attempt = self.loop.start_attempt(goal.id, 1, "executor-agent", "write v1 hello file")

        v2 = self.loop.revise_contract(
            goal.id,
            ContractDraft(
                operational_scope="로컬 artifacts 디렉터리",
                success_criterion="hello.txt의 내용이 정확히 hello-goal-loop-v2다.",
                verification_specification="실행자와 다른 verifier Actor가 파일 내용을 직접 읽는다.",
            ),
            decided_by_actor_id="policy-admin",
            reason="산출물 내용 기준이 물질적으로 변경됨",
        )

        self.assertEqual(v2.version, 2)
        self.assertEqual(goal.current_contract_version, 2)
        self.assertEqual(self.loop.contracts[(goal.id, 1)].success_criterion, self.contract.success_criterion)

        # The already-running v1 Attempt may finish, but remains a v1 fact.
        self.loop.complete_attempt(v1_attempt.id, AttemptOutcome.SUCCEEDED)
        v1_evidence = self.loop.record_evidence(
            v1_attempt.id,
            "executor-agent",
            "v1 file contains hello-goal-loop",
        )
        self.assertEqual(v1_evidence.contract_version, 1)

        with self.assertRaises(GoalLoopError):
            self.loop.begin_verification(goal.id, v1_attempt.id)
        with self.assertRaises(GoalLoopError):
            self.loop.start_attempt(goal.id, 1, "executor-agent", "retry stale v1")

        v2_attempt = self.loop.start_attempt(goal.id, 2, "executor-agent", "write v2 hello file")
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = Path(temporary_directory) / "hello.txt"
            output.write_text("hello-goal-loop-v2", encoding="utf-8")
            self.loop.complete_attempt(v2_attempt.id, AttemptOutcome.SUCCEEDED)
            v2_evidence = self.loop.record_evidence(
                v2_attempt.id,
                "verifier-agent",
                f"verifier read: {output.read_text(encoding='utf-8')}",
            )

        self.loop.begin_verification(goal.id, v2_attempt.id)
        verdict = self.loop.submit_verdict(
            goal.id,
            v2_attempt.id,
            "verifier-agent",
            VerdictResult.SATISFIED,
            (v2_evidence.id,),
        )

        self.assertEqual(verdict.contract_version, 2)
        self.assertEqual(goal.phase, GoalPhase.VERIFIED)

    def test_contract_revision_requires_reason_and_change(self) -> None:
        goal = self.accept()
        with self.assertRaises(GoalLoopError):
            self.loop.revise_contract(goal.id, self.contract, decided_by_actor_id="admin", reason="")
        with self.assertRaises(GoalLoopError):
            self.loop.revise_contract(goal.id, self.contract, decided_by_actor_id="admin", reason="same")

    def test_evidence_correction_invalidates_verdict_and_blocks_dependent_goal(self) -> None:
        source = self.accept()
        attempt = self.loop.start_attempt(source.id, 1, "executor", "write file")
        self.loop.complete_attempt(attempt.id, AttemptOutcome.SUCCEEDED)
        evidence = self.loop.record_evidence(attempt.id, "verifier", "file contains hello-goal-loop")
        self.loop.begin_verification(source.id, attempt.id)
        verdict = self.loop.submit_verdict(
            source.id, attempt.id, "verifier", VerdictResult.SATISFIED, (evidence.id,)
        )
        dependent_proposal = self.loop.create_proposal(self.aim.id, "use verified output")
        dependent = self.loop.accept_proposal(
            dependent_proposal.id,
            GoalIdentity(self.aim.id, "use source output", "consume verified output"),
            self.contract,
            idempotency_key="accept-dependent-1",
        )
        self.loop.add_verdict_dependency(dependent.id, verdict.id)
        correction = self.loop.correct_evidence(evidence.id, "auditor", "measurement source was wrong")
        review = self.loop.review_verdict_after_correction(verdict.id, correction.id)

        self.assertEqual(source.phase, GoalPhase.VERIFIED)
        self.assertEqual(self.loop.verdict_validity[verdict.id], VerdictValidity.UNDER_REVIEW)
        self.assertEqual(review.affected_goal_ids, (dependent.id,))
        with self.assertRaises(GoalLoopError):
            self.loop.start_attempt(dependent.id, 1, "executor", "consume output")

        self.loop.invalidate_verdict(verdict.id)
        self.assertEqual(self.loop.verdict_validity[verdict.id], VerdictValidity.INVALIDATED)
        self.assertEqual(source.phase, GoalPhase.VERIFIED)

    def test_authority_hold_and_aim_pause_block_new_attempt_admission(self) -> None:
        loop = GoalLoop(enforce_authority=True)
        aim = loop.create_aim("controlled execution")
        proposal = loop.create_proposal(aim.id, "create a controlled artifact")
        goal = loop.accept_proposal(
            proposal.id,
            GoalIdentity(aim.id, "controlled artifact", "create it"),
            self.contract,
            idempotency_key="controlled-1",
        )
        with self.assertRaises(GoalLoopError):
            loop.start_attempt(goal.id, 1, "executor", "write")
        loop.grant_execute("executor", goal.id)
        loop.add_goal_hold(goal.id)
        with self.assertRaises(GoalLoopError):
            loop.start_attempt(goal.id, 1, "executor", "write")
        loop.clear_goal_hold(goal.id)
        loop.pause_aim(aim.id)
        with self.assertRaises(GoalLoopError):
            loop.start_attempt(goal.id, 1, "executor", "write")
    def test_not_satisfied_returns_to_active_for_same_contract_repair(self) -> None:
        goal = self.accept()
        attempt = self.loop.start_attempt(goal.id, 1, "executor", "write")
        self.loop.complete_attempt(attempt.id, AttemptOutcome.SUCCEEDED)
        evidence = self.loop.record_evidence(attempt.id, "verifier", "file content was wrong")
        self.loop.begin_verification(goal.id, attempt.id)
        verdict = self.loop.submit_verdict(
            goal.id, attempt.id, "verifier", VerdictResult.NOT_SATISFIED, (evidence.id,)
        )
        self.assertEqual(verdict.result, VerdictResult.NOT_SATISFIED)
        self.assertEqual(goal.phase, GoalPhase.ACTIVE)
        repaired = self.loop.start_attempt(goal.id, 1, "executor", "repair same contract")
        self.assertEqual(repaired.contract_version, 1)

    def test_inconclusive_keeps_verifying_for_more_evidence(self) -> None:
        goal = self.accept()
        attempt = self.loop.start_attempt(goal.id, 1, "executor", "write")
        self.loop.complete_attempt(attempt.id, AttemptOutcome.SUCCEEDED)
        evidence = self.loop.record_evidence(attempt.id, "verifier", "measurement source unavailable")
        self.loop.begin_verification(goal.id, attempt.id)
        verdict = self.loop.submit_verdict(
            goal.id, attempt.id, "verifier", VerdictResult.INCONCLUSIVE, (evidence.id,)
        )
        self.assertEqual(verdict.result, VerdictResult.INCONCLUSIVE)
        self.assertEqual(goal.phase, GoalPhase.VERIFYING)
    def test_aim_requires_explicit_evaluation_after_goal_verification(self) -> None:
        goal = self.accept()
        attempt = self.loop.start_attempt(goal.id, 1, "executor", "write")
        self.loop.complete_attempt(attempt.id, AttemptOutcome.SUCCEEDED)
        evidence = self.loop.record_evidence(attempt.id, "verifier", "file contains hello-goal-loop")
        self.loop.begin_verification(goal.id, attempt.id)
        verdict = self.loop.submit_verdict(
            goal.id, attempt.id, "verifier", VerdictResult.SATISFIED, (evidence.id,)
        )
        self.assertEqual(goal.phase, GoalPhase.VERIFIED)
        self.assertEqual(self.aim.phase, AimPhase.OPEN)

        evaluation = self.loop.evaluate_aim_as_achieved(
            self.aim.id,
            "aim-evaluator",
            (verdict.id,),
            "이 Aim에 필요한 산출물과 유효한 검증 판정이 확보되었다.",
        )
        self.assertEqual(evaluation.aim_id, self.aim.id)
        self.assertEqual(self.aim.phase, AimPhase.ACHIEVED)

