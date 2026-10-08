from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
import unittest

from goal_loop_tool.repository import SqliteEventRepository


class SchemaVersionTests(unittest.TestCase):
    def test_repository_creates_and_validates_schema_version(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal-loop.sqlite3"
            SqliteEventRepository(path)
            connection = sqlite3.connect(path)
            try:
                version = connection.execute("SELECT value FROM schema_metadata WHERE key = 'schema_version'").fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(version, "3")
            self.assertEqual(SqliteEventRepository(path).schema_version(), 3)

    def test_repository_rejects_an_unknown_schema_version(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal-loop.sqlite3"
            SqliteEventRepository(path)
            connection = sqlite3.connect(path)
            try:
                connection.execute("UPDATE schema_metadata SET value = '999' WHERE key = 'schema_version'")
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(RuntimeError, "Unsupported SQLite schema version"):
                SqliteEventRepository(path)

    def test_v1_metadata_is_migrated_to_v3(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "goal-loop.sqlite3"
            SqliteEventRepository(path)
            connection = sqlite3.connect(path)
            try:
                connection.execute("UPDATE schema_metadata SET value = '1' WHERE key = 'schema_version'")
                connection.commit()
            finally:
                connection.close()
            self.assertEqual(SqliteEventRepository(path).schema_version(), 3)
