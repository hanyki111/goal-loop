from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.activity import activity_cards
from goal_loop_tool.model import GoalIdentity
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.repository import SqliteEventRepository
from goal_loop_tool.service import ContractDraft, GoalLoopError


class ActivityProjectionTests(unittest.TestCase):
    def test_journal_and_rejected_receipt_become_readable_korean_cards(self) -> None:
        with TemporaryDirectory() as directory:
            repository = SqliteEventRepository(Path(directory) / "goal-loop.sqlite3")
            loop = PersistentGoalLoop(repository)
            aim = loop.create_aim("활동 로그를 이해하기 쉽게 만들기", command_id="aim-command")
            proposal = loop.create_proposal(aim.id, "결과 만들기")
            goal = loop.accept_proposal(
                proposal.id,
                GoalIdentity(aim.id, "target", "produce"),
                ContractDraft("scope", "criterion", "verify"),
                idempotency_key="activity",
            )
            with self.assertRaises(GoalLoopError):
                loop.start_attempt(goal.id, 99, "executor", "stale", command_id="rejected-command")

            cards = activity_cards(repository)
            aim_card = next(card for card in cards if card.command_id == "aim-command")
            rejected = next(card for card in cards if card.command_id == "rejected-command")
            self.assertEqual(aim_card.title, "Aim 생성")
            self.assertEqual(aim_card.detail, "활동 로그를 이해하기 쉽게 만들기")
            self.assertEqual(rejected.title, "명령 거절")
            self.assertEqual(rejected.error_code, "contract_version_stale")
            self.assertIn("stale", rejected.detail)
