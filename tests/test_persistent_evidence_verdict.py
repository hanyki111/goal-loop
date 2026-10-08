from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.model import AttemptOutcome, GoalIdentity, GoalPhase, VerdictResult, VerdictValidity
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.repository import SqliteEventRepository
from goal_loop_tool.service import ContractDraft


class PersistentEvidenceVerdictAcceptanceTests(unittest.TestCase):
    def test_evidence_and_satisfied_verdict_restore_after_restart(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal-loop.sqlite3"
            first = PersistentGoalLoop(SqliteEventRepository(path))
            aim = first.create_aim("verify durable observations")
            proposal = first.create_proposal(aim.id, "produce a verifiable result")
            goal = first.accept_proposal(
                proposal.id,
                GoalIdentity(aim.id, "durable target", "produce"),
                ContractDraft("scope", "criterion", "independent verification"),
                idempotency_key="durable-verdict",
            )
            attempt = first.start_attempt(goal.id, 1, "executor", "perform work")
            first.complete_attempt(attempt.id, AttemptOutcome.SUCCEEDED)
            evidence = first.record_evidence(attempt.id, "observer", "observed result")
            first.begin_verification(goal.id, attempt.id)
            verdict = first.submit_verdict(
                goal.id,
                attempt.id,
                "independent-verifier",
                VerdictResult.SATISFIED,
                (evidence.id,),
            )

            restarted = PersistentGoalLoop(SqliteEventRepository(path))

            self.assertEqual(restarted.evidence[evidence.id], evidence)
            self.assertEqual(restarted.verdicts[verdict.id], verdict)
            self.assertEqual(restarted.verdict_validity[verdict.id], VerdictValidity.VALID)
            self.assertEqual(restarted.goals[goal.id].phase, GoalPhase.VERIFIED)
            self.assertIsNone(restarted.goals[goal.id].verifying_attempt_id)

