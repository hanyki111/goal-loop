from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.model import GoalIdentity
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.repository import CommandConflictError, SqliteEventRepository
from goal_loop_tool.service import ContractDraft, GoalLoopError


class PersistentCommandReceiptAcceptanceTests(unittest.TestCase):
    def test_duplicate_command_returns_original_result_without_another_event(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal-loop.sqlite3"
            first = PersistentGoalLoop(SqliteEventRepository(path))
            aim = first.create_aim("durable command", command_id="create-aim-1")

            repeated = first.create_aim("durable command", command_id="create-aim-1")
            self.assertEqual(repeated, aim)
            self.assertEqual(len(first.aims), 1)

            restarted = PersistentGoalLoop(SqliteEventRepository(path))
            replayed = restarted.create_aim("durable command", command_id="create-aim-1")
            self.assertEqual(replayed, aim)
            self.assertEqual(len(restarted.repository.events()), 1)
            with self.assertRaises(CommandConflictError):
                restarted.create_aim("different request", command_id="create-aim-1")

    def test_rejected_command_is_durable_and_replayed_after_restart(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal-loop.sqlite3"
            first = PersistentGoalLoop(SqliteEventRepository(path))
            aim = first.create_aim("rejected command")
            proposal = first.create_proposal(aim.id, "produce result")
            goal = first.accept_proposal(
                proposal.id,
                GoalIdentity(aim.id, "target", "produce"),
                ContractDraft("scope", "criterion", "verify"),
                idempotency_key="receipt-rejection",
            )
            with self.assertRaises(GoalLoopError) as first_error:
                first.start_attempt(goal.id, 99, "executor", "stale work", command_id="stale-attempt-1")
            self.assertEqual(first_error.exception.code.value, "contract_version_stale")
            receipt = first.repository.receipt("stale-attempt-1")
            self.assertEqual(receipt.status, "rejected")
            event_count = len(first.repository.events())

            restarted = PersistentGoalLoop(SqliteEventRepository(path))
            with self.assertRaises(GoalLoopError) as replay_error:
                restarted.start_attempt(goal.id, 99, "executor", "stale work", command_id="stale-attempt-1")
            self.assertEqual(replay_error.exception.code.value, "contract_version_stale")
            self.assertEqual(len(restarted.repository.events()), event_count)

