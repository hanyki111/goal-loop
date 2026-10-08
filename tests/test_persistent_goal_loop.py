from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.model import GoalIdentity, GoalPhase, ProposalStatus
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.repository import SqliteEventRepository
from goal_loop_tool.service import ContractDraft


class PersistentGoalLoopAcceptanceTests(unittest.TestCase):
    def test_acceptance_restarts_with_the_same_goal_and_contract(self) -> None:
        with TemporaryDirectory() as directory:
            repository = SqliteEventRepository(Path(directory) / "goal-loop.sqlite3")
            first = PersistentGoalLoop(repository)
            aim = first.create_aim("A durable Aim")
            proposal = first.create_proposal(aim.id, "Create a durable Goal")
            goal = first.accept_proposal(
                proposal.id,
                GoalIdentity(aim.id, "durable target", "durable responsibility"),
                ContractDraft("scope", "criterion", "verification"),
                idempotency_key="accept-1",
            )

            restarted = PersistentGoalLoop(SqliteEventRepository(Path(directory) / "goal-loop.sqlite3"))
            self.assertEqual(restarted.aims[aim.id].description, aim.description)
            self.assertEqual(restarted.proposals[proposal.id].status, ProposalStatus.ACCEPTED)
            self.assertEqual(restarted.proposals[proposal.id].accepted_goal_id, goal.id)
            self.assertEqual(restarted.goals[goal.id].phase, GoalPhase.COMMITTED)
            self.assertEqual(restarted.contracts[(goal.id, 1)].success_criterion, "criterion")
