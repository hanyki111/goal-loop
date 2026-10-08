from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.repository import JournalEvent, SqliteEventRepository


class SqliteBackupTests(unittest.TestCase):
    def test_backup_restores_journal_and_receipts(self) -> None:
        with TemporaryDirectory() as directory:
            source = Path(directory) / "source.sqlite3"
            backup = Path(directory) / "nested" / "backup.sqlite3"
            repository = SqliteEventRepository(source)
            repository.commit(command_id="c1", command_type="test", request_fingerprint="request", events=(JournalEvent("e1", "AimCreated", "actor", "aim", "aim-1", {"description": "backup"}),), result={"id": "aim-1"})
            repository.backup_to(backup)
            restored = SqliteEventRepository(backup)
            self.assertEqual(restored.events()[0].payload["description"], "backup")
            self.assertEqual(restored.receipt("c1").status, "committed")
