from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.repository import JournalEvent, SqliteEventRepository


class SnapshotMetadataTests(unittest.TestCase):
    def test_snapshot_is_valid_only_for_its_exact_journal_prefix(self) -> None:
        with TemporaryDirectory() as directory:
            repo = SqliteEventRepository(Path(directory) / "goal-loop.sqlite3")
            repo.commit(command_id="c1", command_type="test", request_fingerprint="1", events=(JournalEvent("e1", "AimCreated", "actor", "aim", "aim-1", {"description": "one"}),), result={})
            snapshot = repo.create_snapshot_metadata()
            self.assertTrue(repo.snapshot_is_current(snapshot["id"]))
            self.assertEqual(repo.restore_snapshot_events(snapshot["id"])[0].event_id, "e1")
            repo.commit(command_id="c2", command_type="test", request_fingerprint="2", events=(JournalEvent("e2", "AimCreated", "actor", "aim", "aim-2", {"description": "two"}),), result={})
            self.assertFalse(repo.snapshot_is_current(snapshot["id"]))
            self.assertEqual([event.event_id for event in repo.latest_valid_snapshot_events()], ["e1"])
            with self.assertRaisesRegex(RuntimeError, "Snapshot is stale"):
                repo.restore_snapshot_events(snapshot["id"])
