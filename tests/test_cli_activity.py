from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from goal_loop_tool.cli import main
from goal_loop_tool.model import GoalIdentity
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.repository import SqliteEventRepository
from goal_loop_tool.service import ContractDraft, GoalLoopError


class ActivityCliAcceptanceTests(unittest.TestCase):
    def test_activity_command_renders_korean_cards_from_sqlite(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal-loop.sqlite3"
            loop = PersistentGoalLoop(SqliteEventRepository(path))
            aim = loop.create_aim("CLI 활동 로그")
            proposal = loop.create_proposal(aim.id, "produce")
            goal = loop.accept_proposal(proposal.id, GoalIdentity(aim.id, "target", "produce"), ContractDraft("scope", "criterion", "verify"), idempotency_key="cli-activity")
            with self.assertRaises(GoalLoopError):
                loop.start_attempt(goal.id, 9, "executor", "stale", command_id="cli-rejection")
            output = StringIO()
            with patch("sys.argv", ["goal_loop_tool", "activity", "--database", str(path)]), redirect_stdout(output):
                main()
            self.assertIn("Aim 생성", output.getvalue())
            self.assertIn("명령 거절", output.getvalue())
            self.assertIn("contract_version_stale", output.getvalue())
