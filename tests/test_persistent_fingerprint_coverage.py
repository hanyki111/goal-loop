from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.model import GoalIdentity
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.repository import CommandConflictError, SqliteEventRepository
from goal_loop_tool.service import ContractDraft


class PersistentFingerprintCoverageTests(unittest.TestCase):
    def test_acceptance_command_conflicts_when_contract_or_identity_changes(self) -> None:
        with TemporaryDirectory() as directory:
            loop = PersistentGoalLoop(SqliteEventRepository(Path(directory) / "goal-loop.sqlite3"))
            aim = loop.create_aim("fingerprint")
            proposal = loop.create_proposal(aim.id, "produce")
            identity = GoalIdentity(aim.id, "target", "produce")
            draft = ContractDraft("scope", "criterion", "verify")
            loop.accept_proposal(proposal.id, identity, draft, idempotency_key="key", command_id="accept-command")
            with self.assertRaises(CommandConflictError):
                loop.accept_proposal(
                    proposal.id, GoalIdentity(aim.id, "target", "different responsibility"), draft,
                    idempotency_key="key", command_id="accept-command",
                )


