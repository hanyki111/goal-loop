from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.model import AttemptOutcome, GoalIdentity, ReviewDisposition, VerdictResult, VerdictValidity
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.repository import SqliteEventRepository
from goal_loop_tool.service import ContractDraft


class PersistentCorrectionReviewAcceptanceTests(unittest.TestCase):
    def test_correction_and_invalidation_review_restore_after_restart(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal-loop.sqlite3"
            first = PersistentGoalLoop(SqliteEventRepository(path))
            aim = first.create_aim("durable correction")
            proposal = first.create_proposal(aim.id, "produce result")
            goal = first.accept_proposal(
                proposal.id,
                GoalIdentity(aim.id, "target", "produce"),
                ContractDraft("scope", "criterion", "verify"),
                idempotency_key="correction-review",
            )
            attempt = first.start_attempt(goal.id, 1, "executor", "work")
            first.complete_attempt(attempt.id, AttemptOutcome.SUCCEEDED)
            evidence = first.record_evidence(attempt.id, "observer", "initial observation")
            first.begin_verification(goal.id, attempt.id)
            verdict = first.submit_verdict(
                goal.id, attempt.id, "verifier", VerdictResult.SATISFIED, (evidence.id,)
            )
            correction = first.correct_evidence(evidence.id, "observer", "observation corrected")
            review = next(review for review in first.impact_reviews.values() if review.correction_id == correction.id)
            resolution = first.resolve_verdict_review(
                review.id, ReviewDisposition.INVALIDATE, "reviewer", "evidence is unreliable"
            )

            restarted = PersistentGoalLoop(SqliteEventRepository(path))

            self.assertEqual(restarted.evidence_corrections[correction.id], correction)
            self.assertEqual(restarted.impact_reviews[review.id], review)
            self.assertEqual(restarted.review_resolutions[resolution.id], resolution)
            self.assertEqual(restarted.verdict_validity[verdict.id], VerdictValidity.INVALIDATED)

