from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.model import AttemptOutcome, GoalIdentity, VerdictResult
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.repository import SqliteEventRepository
from goal_loop_tool.service import ContractDraft


class PersistentReverificationDecisionAcceptanceTests(unittest.TestCase):
    def test_reverification_decision_survives_restart_and_is_consumed_once(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal-loop.sqlite3"
            first = PersistentGoalLoop(SqliteEventRepository(path))
            aim = first.create_aim("durable reverification")
            proposal = first.create_proposal(aim.id, "produce result")
            goal = first.accept_proposal(
                proposal.id,
                GoalIdentity(aim.id, "target", "produce"),
                ContractDraft("scope", "criterion", "verify"),
                idempotency_key="reverification",
            )
            initial = first.start_attempt(goal.id, 1, "executor", "initial work")
            first.complete_attempt(initial.id, AttemptOutcome.SUCCEEDED)
            evidence = first.record_evidence(initial.id, "observer", "incomplete observation")
            first.begin_verification(goal.id, initial.id)
            first.submit_verdict(goal.id, initial.id, "verifier", VerdictResult.INCONCLUSIVE, (evidence.id,))
            decision = first.decide_reverification_requires_new_attempt(goal.id, "reviewer", "need another run")

            restarted = PersistentGoalLoop(SqliteEventRepository(path))
            self.assertEqual(restarted.pending_reverification_decision_by_goal[goal.id], decision.id)

            retry = restarted.start_attempt(
                goal.id, 1, "executor", "new runtime work", reverification_decision_id=decision.id
            )
            again = PersistentGoalLoop(SqliteEventRepository(path))
            self.assertEqual(again.attempts[retry.id].contract_version, 1)
            self.assertEqual(again.decisions[decision.id].consumed_by_attempt_id, retry.id)
            self.assertNotIn(goal.id, again.pending_reverification_decision_by_goal)

