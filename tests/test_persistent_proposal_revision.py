from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.model import GoalIdentity, ProposalStatus
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.repository import CommandConflictError, SqliteEventRepository
from goal_loop_tool.service import ContractDraft, GoalLoopError


class PersistentProposalRevisionAcceptanceTests(unittest.TestCase):
    def test_revision_restarts_is_idempotent_and_blocks_old_acceptance(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal-loop.sqlite3"
            first = PersistentGoalLoop(SqliteEventRepository(path))
            aim = first.create_aim("Improve CLI onboarding")
            original = first.create_proposal(aim.id, "Write a short guide")

            replacement = first.revise_proposal(
                original.id,
                "Improve guided prompts and provide a short guide",
                "product-owner",
                "The guide alone does not remove first-run confusion.",
                command_id="proposal-revision-1",
            )
            repeated = first.revise_proposal(
                original.id,
                "Improve guided prompts and provide a short guide",
                "product-owner",
                "The guide alone does not remove first-run confusion.",
                command_id="proposal-revision-1",
            )
            self.assertEqual(repeated, replacement)
            self.assertEqual(first.proposals[original.id].status, ProposalStatus.SUPERSEDED)
            self.assertEqual(first.proposals[original.id].superseded_by_proposal_id, replacement.id)

            with self.assertRaises(CommandConflictError):
                first.revise_proposal(original.id, "different", "product-owner", "different", command_id="proposal-revision-1")
            with self.assertRaises(GoalLoopError):
                first.accept_proposal(
                    original.id, GoalIdentity(aim.id, "target", "produce"),
                    ContractDraft("scope", "criterion", "verify"), idempotency_key="old-proposal",
                )

            restarted = PersistentGoalLoop(SqliteEventRepository(path))
            self.assertEqual(restarted.proposals[original.id].status, ProposalStatus.SUPERSEDED)
            self.assertEqual(restarted.proposals[original.id].superseded_by_proposal_id, replacement.id)
            self.assertEqual(restarted.proposals[replacement.id].direction, replacement.direction)
            goal = restarted.accept_proposal(
                replacement.id, GoalIdentity(aim.id, "target", "produce"),
                ContractDraft("scope", "criterion", "verify"), idempotency_key="replacement-proposal",
            )
            self.assertEqual(goal.identity.primary_aim_id, aim.id)

    def test_accepted_proposal_cannot_be_revised(self) -> None:
        with TemporaryDirectory() as directory:
            loop = PersistentGoalLoop(SqliteEventRepository(Path(directory) / "goal-loop.sqlite3"))
            aim = loop.create_aim("accepted proposal")
            proposal = loop.create_proposal(aim.id, "initial direction")
            loop.accept_proposal(
                proposal.id, GoalIdentity(aim.id, "target", "produce"),
                ContractDraft("scope", "criterion", "verify"), idempotency_key="accepted-proposal",
            )
            with self.assertRaises(GoalLoopError):
                loop.revise_proposal(proposal.id, "changed direction", "owner", "changed mind")
