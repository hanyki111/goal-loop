from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.repository import SqliteEventRepository


class CorruptionDiagnosticsTests(unittest.TestCase):
    def test_integrity_diagnostics_reports_clean_database(self) -> None:
        with TemporaryDirectory() as directory:
            repository = SqliteEventRepository(Path(directory) / "goal-loop.sqlite3")
            self.assertEqual(repository.integrity_diagnostics(), ("ok",))
