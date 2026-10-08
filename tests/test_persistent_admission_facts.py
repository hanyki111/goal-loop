from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.model import AdmissionErrorCode, GoalIdentity
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.repository import SqliteEventRepository
from goal_loop_tool.service import ContractDraft, GoalLoopError


class PersistentAdmissionFactsAcceptanceTests(unittest.TestCase):
    def test_grant_hold_pause_and_resume_restore_admission_state(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal-loop.sqlite3"
            first = PersistentGoalLoop(SqliteEventRepository(path), enforce_authority=True)
            aim = first.create_aim("durable admission")
            proposal = first.create_proposal(aim.id, "produce result")
            goal = first.accept_proposal(
                proposal.id,
                GoalIdentity(aim.id, "target", "produce"),
                ContractDraft("scope", "criterion", "verify"),
                idempotency_key="admission-facts",
            )
            first.grant_execute("executor", goal.id)
            first.add_goal_hold(goal.id)
            first.pause_aim(aim.id, "admin", "waiting for review")

            restarted = PersistentGoalLoop(SqliteEventRepository(path), enforce_authority=True)
            self.assertIn(goal.id, restarted.execution_grants["executor"])
            self.assertIn(goal.id, restarted.goal_holds)
            self.assertIn(aim.id, restarted.paused_aim_ids)
            with self.assertRaises(GoalLoopError) as paused:
                restarted.start_attempt(goal.id, 1, "executor", "work")
            self.assertEqual(paused.exception.code, AdmissionErrorCode.AIM_PAUSED)

            restarted.clear_goal_hold(goal.id)
            restarted.resume_aim(aim.id, "admin", "review complete")
            resumed = PersistentGoalLoop(SqliteEventRepository(path), enforce_authority=True)
            attempt = resumed.start_attempt(goal.id, 1, "executor", "admitted work")
            self.assertEqual(attempt.goal_id, goal.id)
            self.assertNotIn(goal.id, resumed.goal_holds)
            self.assertNotIn(aim.id, resumed.paused_aim_ids)


