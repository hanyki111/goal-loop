import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.moved_paths import load_moves, relocate, relocated_payload


class MovedPathsTests(unittest.TestCase):
    """Stored AO paths are read at their new location; the stored payload is never changed."""

    def test_prefix_is_replaced_ignoring_case_and_separators(self):
        moves = [("C:/work/ao-ws", "C:/work/ao/workspace")]
        self.assertEqual(relocate("C:\\Work\\ao-ws", moves), "C:/work/ao/workspace")
        self.assertEqual(relocate("c:/work/ao-ws/projects/x", moves), "C:/work/ao/workspace/projects/x")

    def test_only_whole_folder_names_match(self):
        moves = [("C:/work/ao-runner", "C:/new/runner")]
        self.assertEqual(relocate("C:/work/ao-runner-old", moves), "C:/work/ao-runner-old")
        self.assertEqual(relocate("D:/elsewhere", moves), "D:/elsewhere")

    def test_longest_matching_move_wins(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "journal.sqlite3"
            (Path(directory) / "moved-paths.json").write_text(json.dumps({"moves": [
                {"from": "E:/a", "to": "E:/x"}, {"from": "E:/a/b", "to": "E:/y"}]}), encoding="utf-8")
            moves = load_moves(database)
            self.assertEqual(relocate("E:/a/b/c", moves), "E:/y/c")
            self.assertEqual(relocate("E:/a/c", moves), "E:/x/c")

    def test_payload_copy_moves_only_path_fields(self):
        with TemporaryDirectory() as directory:
            database = Path(directory) / "journal.sqlite3"
            payload = {"ao_root": "E:/old/runner", "ao_workspace": "E:/old/ws", "title": "E:/old/ws 안내"}
            self.assertIs(relocated_payload(payload, database), payload)  # no file: unchanged
            (Path(directory) / "moved-paths.json").write_text(json.dumps({"moves": [{"from": "E:/old", "to": "E:/new"}]}), encoding="utf-8")
            moved = relocated_payload(payload, database)
            self.assertEqual(moved, {"ao_root": "E:/new/runner", "ao_workspace": "E:/new/ws", "title": "E:/old/ws 안내"})
            self.assertEqual(payload["ao_root"], "E:/old/runner")

    def test_broken_file_is_reported(self):
        with TemporaryDirectory() as directory:
            (Path(directory) / "moved-paths.json").write_text("{not json", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_moves(Path(directory) / "journal.sqlite3")


if __name__ == "__main__":
    unittest.main()
