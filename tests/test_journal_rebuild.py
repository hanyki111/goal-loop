from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.model import AttemptOutcome, GoalIdentity
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.repository import SqliteEventRepository
from goal_loop_tool.service import ContractDraft


class JournalRebuildAcceptanceTests(unittest.TestCase):
    def test_explicit_rebuild_restores_current_state_from_journal_only(self) -> None:
        with TemporaryDirectory() as directory:
            loop = PersistentGoalLoop(SqliteEventRepository(Path(directory) / "goal-loop.sqlite3"))
            aim = loop.create_aim("rebuild")
            proposal = loop.create_proposal(aim.id, "produce")
            goal = loop.accept_proposal(proposal.id, GoalIdentity(aim.id, "target", "produce"), ContractDraft("scope", "criterion", "verify"), idempotency_key="rebuild")
            attempt = loop.start_attempt(goal.id, 1, "executor", "work")
            loop.complete_attempt(attempt.id, AttemptOutcome.SUCCEEDED)
            loop._loop.aims.clear()  # simulate loss of an in-memory projection
            loop.rebuild_from_journal()
            self.assertEqual(loop.aims[aim.id].description, "rebuild")
            self.assertEqual(loop.attempts[attempt.id].outcome, AttemptOutcome.SUCCEEDED)
