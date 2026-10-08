from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.model import AttemptOutcome, GoalIdentity
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.repository import CommandConflictError, SqliteEventRepository
from goal_loop_tool.runtime import deliver_runtime_result
from goal_loop_tool.service import ContractDraft, GoalLoopError


class RuntimeDeliveryAcceptanceTests(unittest.TestCase):
    def test_delivery_replays_conflicts_and_rejects_late_result(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal-loop.sqlite3"
            first = PersistentGoalLoop(SqliteEventRepository(path))
            aim = first.create_aim("runtime delivery")
            proposal = first.create_proposal(aim.id, "produce")
            goal = first.accept_proposal(proposal.id, GoalIdentity(aim.id, "target", "produce"), ContractDraft("scope", "criterion", "verify"), idempotency_key="runtime")
            attempt = first.start_attempt(goal.id, 1, "executor", "work")
            completed = deliver_runtime_result(first, attempt.id, AttemptOutcome.SUCCEEDED, "delivery-1")
            restarted = PersistentGoalLoop(SqliteEventRepository(path))
            self.assertEqual(deliver_runtime_result(restarted, attempt.id, AttemptOutcome.SUCCEEDED, "delivery-1"), completed)
            with self.assertRaises(CommandConflictError):
                deliver_runtime_result(restarted, attempt.id, AttemptOutcome.FAILED, "delivery-1")
            with self.assertRaises(GoalLoopError):
                deliver_runtime_result(restarted, attempt.id, AttemptOutcome.SUCCEEDED, "delivery-2")
