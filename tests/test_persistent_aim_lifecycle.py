from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.model import AimPhase, AttemptOutcome, GoalIdentity, ReviewDisposition, VerdictResult, VerdictValidity
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.repository import SqliteEventRepository
from goal_loop_tool.service import ContractDraft


class PersistentAimLifecycleAcceptanceTests(unittest.TestCase):
    def test_invalidated_aim_evaluation_and_explicit_reopen_restore_after_restart(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal-loop.sqlite3"
            first = PersistentGoalLoop(SqliteEventRepository(path))
            aim = first.create_aim("durable aim closure")
            proposal = first.create_proposal(aim.id, "produce result")
            goal = first.accept_proposal(
                proposal.id,
                GoalIdentity(aim.id, "target", "produce"),
                ContractDraft("scope", "criterion", "verify"),
                idempotency_key="aim-lifecycle",
            )
            attempt = first.start_attempt(goal.id, 1, "executor", "work")
            first.complete_attempt(attempt.id, AttemptOutcome.SUCCEEDED)
            evidence = first.record_evidence(attempt.id, "observer", "initial observation")
            first.begin_verification(goal.id, attempt.id)
            verdict = first.submit_verdict(
                goal.id, attempt.id, "verifier", VerdictResult.SATISFIED, (evidence.id,)
            )
            evaluation = first.evaluate_aim_as_achieved(
                aim.id, "evaluator", (verdict.id,), "sufficient"
            )
            correction = first.correct_evidence(evidence.id, "auditor", "observation corrected")
            review = next(review for review in first.impact_reviews.values() if review.correction_id == correction.id)
            first.resolve_verdict_review(review.id, ReviewDisposition.INVALIDATE, "auditor", "unreliable")
            resolution = first.resolve_aim_evaluation_review(
                evaluation.id, review.id, ReviewDisposition.INVALIDATE, "evaluator", "unsupported closure"
            )
            invalidation = first.reopen_aim_after_closure_invalidation(
                aim.id, evaluation.id, review.id, "admin", "resume pursuit"
            )

            restarted = PersistentGoalLoop(SqliteEventRepository(path))

            self.assertEqual(restarted.aim_evaluations[evaluation.id], evaluation)
            self.assertEqual(restarted.aim_evaluation_validity[evaluation.id], VerdictValidity.INVALIDATED)
            self.assertEqual(restarted.review_resolutions[resolution.id], resolution)
            self.assertEqual(restarted.aim_closure_invalidations[invalidation.id], invalidation)
            self.assertEqual(restarted.aims[aim.id].phase, AimPhase.OPEN)
            self.assertFalse(restarted.is_currently_achieved(aim.id))

