from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from goal_loop_tool import ContractDraft
from goal_loop_tool.ide_view import graph_rows, render_svg, write_ide_view
from goal_loop_tool.model import GoalIdentity
from goal_loop_tool.service import GoalLoop
from goal_loop_tool.ui_projection import goal_graph_projection


def accept(loop, aim_id, target, key):
    proposal = loop.create_proposal(aim_id, target)
    return loop.accept_proposal(proposal.id, GoalIdentity(aim_id, target, "produce"), ContractDraft("scope", f"{target} criterion", "check"), idempotency_key=key)


class IdeViewTests(unittest.TestCase):
    def test_outline_nests_sub_aims_and_goals_and_draws_follow_links(self):
        loop = GoalLoop()
        parent = loop.create_aim("Parent <aim>")
        child = loop.create_aim("Child aim", parent.id)
        first = accept(loop, child.id, "First | task", "ide-1")
        second = accept(loop, parent.id, "Second task", "ide-2")
        loop.add_goal_follow(second.id, first.id, "claude", "continues first")
        rows = graph_rows(goal_graph_projection(loop))
        self.assertEqual([(row["node"]["id"], row["depth"]) for row in rows], [(parent.id, 0), (child.id, 1), (first.id, 2), (second.id, 1)])
        svg = render_svg(goal_graph_projection(loop))
        self.assertIn("Parent &lt;aim&gt;", svg)
        self.assertIn("arrow-follows", svg)
        self.assertIn("continues first", svg)

    def test_files_are_written_with_markdown_image_and_escaped_tables(self):
        loop = GoalLoop()
        aim = loop.create_aim("Viewer")
        accept(loop, aim.id, "First | task", "ide-3")
        with TemporaryDirectory() as directory:
            paths = write_ide_view(goal_graph_projection(loop), Path(directory) / "view", "journal.sqlite3")
            markdown = Path(paths["markdown"]).read_text(encoding="utf-8")
            self.assertIn("![Goal Loop 그래프](graph.svg)", markdown)
            self.assertIn(r"First \| task", markdown)
            self.assertTrue(Path(paths["svg"]).read_text(encoding="utf-8").startswith("<svg"))

    def test_module_entry_point_writes_files_without_an_installed_executable(self):
        # Linux/macOS checkouts may only have Python; `python -m goal_loop_tool persistent` must be enough.
        with TemporaryDirectory() as directory:
            database, view = Path(directory) / "journal.sqlite3", Path(directory) / "view"
            command = [sys.executable, "-m", "goal_loop_tool", "persistent", "--database", str(database)]
            subprocess.run([*command, "aim-create", "Portable viewer"], check=True, capture_output=True)
            result = subprocess.run([*command, "ide-view", "--out", str(view)], check=True, capture_output=True, text=True, encoding="utf-8")
            self.assertIn("ide_view", result.stdout)
            self.assertIn("Portable viewer", (view / "graph.svg").read_text(encoding="utf-8"))
            self.assertIn("Noto Sans CJK KR", (view / "graph.svg").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
