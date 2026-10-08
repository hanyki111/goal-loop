from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.repository import JournalEvent, SqliteEventRepository
from goal_loop_tool.persistent import PersistentGoalLoop


class ProjectionCheckpointTests(unittest.TestCase):
    def test_checkpoint_tracks_last_replayed_journal_sequence(self) -> None:
        with TemporaryDirectory() as directory:
            repo = SqliteEventRepository(Path(directory) / "goal-loop.sqlite3")
            repo.commit(command_id="c1", command_type="test", request_fingerprint="one", events=(JournalEvent("e1", "AimCreated", "actor", "aim", "aim-1", {"description": "x"}),), result={})
            self.assertEqual(repo.journal_last_sequence(), 1)
            repo.mark_projection_checkpoint(1)
            self.assertEqual(repo.projection_checkpoint(), 1)
            self.assertTrue(repo.projection_checkpoint_is_current())

    def test_restart_recovers_a_stale_checkpoint_from_journal(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal-loop.sqlite3"
            repo = SqliteEventRepository(path)
            repo.commit(command_id="c1", command_type="test", request_fingerprint="one", events=(JournalEvent("e1", "AimCreated", "actor", "aim", "aim-1", {"description": "x"}),), result={})
            self.assertFalse(repo.projection_checkpoint_is_current())
            PersistentGoalLoop(repo)
            self.assertTrue(repo.projection_checkpoint_is_current())
