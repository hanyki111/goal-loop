from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.model import AttemptOutcome, GoalIdentity
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.repository import SqliteEventRepository
from goal_loop_tool.service import ContractDraft, GoalLoopError


class PersistentContractRevisionAcceptanceTests(unittest.TestCase):
    def test_late_v1_result_is_preserved_but_cannot_verify_v2_after_restart(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal-loop.sqlite3"
            loop = PersistentGoalLoop(SqliteEventRepository(path))
            aim = loop.create_aim("revision persistence")
            proposal = loop.create_proposal(aim.id, "make output")
            goal = loop.accept_proposal(proposal.id, GoalIdentity(aim.id, "target", "produce"), ContractDraft("scope", "v1", "verify"), idempotency_key="revision")
            v1 = loop.start_attempt(goal.id, 1, "executor", "v1 run")
            loop.revise_contract(goal.id, ContractDraft("scope", "v2", "verify"), decided_by_actor_id="admin", reason="criterion changed")
            loop.complete_attempt(v1.id, AttemptOutcome.SUCCEEDED)

            restarted = PersistentGoalLoop(SqliteEventRepository(path))
            self.assertEqual(restarted.goals[goal.id].current_contract_version, 2)
            self.assertEqual(restarted.attempts[v1.id].contract_version, 1)
            with self.assertRaises(GoalLoopError):
                restarted.begin_verification(goal.id, v1.id)
