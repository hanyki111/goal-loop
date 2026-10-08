from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.repository import SqliteEventRepository


class TransactionFirstPersistenceTests(unittest.TestCase):
    def test_commit_failure_does_not_publish_trial_state_or_event(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal-loop.sqlite3"
            repository = SqliteEventRepository(path)
            loop = PersistentGoalLoop(repository)

            def fail_commit(**_: object) -> None:
                raise RuntimeError("injected SQLite failure")

            repository.commit = fail_commit  # type: ignore[method-assign]
            with self.assertRaisesRegex(RuntimeError, "injected SQLite failure"):
                loop.create_aim("must not publish")

            self.assertEqual(loop.aims, {})
            reopened = PersistentGoalLoop(SqliteEventRepository(path))
            self.assertEqual(reopened.aims, {})
