from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.model import AttemptOutcome, GoalIdentity, VerdictResult, VerdictValidity
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.repository import SqliteEventRepository
from goal_loop_tool.service import ContractDraft


class PersistentManualVerdictReviewAcceptanceTests(unittest.TestCase):
    def test_manual_review_and_direct_invalidation_restore_after_restart(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal-loop.sqlite3"
            first = PersistentGoalLoop(SqliteEventRepository(path))
            aim = first.create_aim("manual review persistence")
            proposal = first.create_proposal(aim.id, "produce result")
            goal = first.accept_proposal(
                proposal.id,
                GoalIdentity(aim.id, "target", "produce"),
                ContractDraft("scope", "criterion", "verify"),
                idempotency_key="manual-review",
            )
            attempt = first.start_attempt(goal.id, 1, "executor", "work")
            first.complete_attempt(attempt.id, AttemptOutcome.SUCCEEDED)
            evidence = first.record_evidence(attempt.id, "observer", "observation")
            first.begin_verification(goal.id, attempt.id)
            verdict = first.submit_verdict(
                goal.id, attempt.id, "verifier", VerdictResult.SATISFIED, (evidence.id,)
            )
            correction = first.correct_evidence(evidence.id, "auditor", "correction")
            manual_review = first.review_verdict_after_correction(verdict.id, correction.id)
            first.invalidate_verdict(verdict.id)

            restarted = PersistentGoalLoop(SqliteEventRepository(path))
            self.assertEqual(restarted.impact_reviews[manual_review.id], manual_review)
            self.assertEqual(restarted.verdict_validity[verdict.id], VerdictValidity.INVALIDATED)


