from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.model import AttemptOutcome, GoalIdentity
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.repository import SqliteEventRepository
from goal_loop_tool.service import ContractDraft, GoalLoopError


class PersistentRetryAcceptanceTests(unittest.TestCase):
    def test_retry_decision_survives_restart_and_is_consumed_once(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal-loop.sqlite3"
            first = PersistentGoalLoop(SqliteEventRepository(path))
            aim = first.create_aim("retry persistence")
            proposal = first.create_proposal(aim.id, "make the output")
            goal = first.accept_proposal(
                proposal.id,
                GoalIdentity(aim.id, "output", "produce output"),
                ContractDraft("scope", "criterion", "verification"),
                idempotency_key="accept-retry",
            )
            failed = first.start_attempt(goal.id, 1, "executor", "first run")
            first.complete_attempt(failed.id, AttemptOutcome.FAILED)
            retry = first.decide_same_contract_retry(goal.id, failed.id, "admin", "retry it")

            restarted = PersistentGoalLoop(SqliteEventRepository(path))
            repaired = restarted.start_attempt(
                goal.id, 1, "executor", "second run", retry_decision_id=retry.id
            )
            self.assertEqual(restarted.decisions[retry.id].consumed_by_attempt_id, repaired.id)
            restarted.complete_attempt(repaired.id, AttemptOutcome.FAILED)

            reopened = PersistentGoalLoop(SqliteEventRepository(path))
            with self.assertRaises(GoalLoopError):
                reopened.start_attempt(goal.id, 1, "executor", "third run", retry_decision_id=retry.id)
