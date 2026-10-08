from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool.graph_view import export_goal_graph
from goal_loop_tool.persistent import PersistentGoalLoop
from goal_loop_tool.repository import SqliteEventRepository


class GraphViewTests(unittest.TestCase):
    def test_export_writes_html_from_read_only_projection(self):
        with TemporaryDirectory() as directory:
            destination = Path(directory) / "graph.html"
            loop = PersistentGoalLoop(SqliteEventRepository(Path(directory) / "goal.sqlite3"))
            aim = loop.create_aim("<safe>")
            export_goal_graph(loop, destination)
            self.assertTrue(destination.exists())
            content = destination.read_text(encoding="utf-8")
            self.assertIn("Goal Loop Graph", content)
            self.assertIn("<svg", content)
